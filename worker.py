#!/usr/bin/env python3
"""
MTG Card Analysis Worker (Simple Serial Version)
Fetches random cards from Scryfall, generates a deep-dive analysis,
saves to MongoDB, and posts to Discord.
"""

import os
import time
import logging
import requests
from datetime import datetime
from typing import Dict, Optional
import ollama
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()

# --- Config ---
LLM_MODEL = os.getenv('LLM_MODEL', 'llama3.1:latest')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
SCRYFALL_API_BASE = 'https://api.scryfall.com'

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# # --- MongoDB ---
# client = MongoClient(MONGODB_URI)
# db = client.mtg
# cards = db.cards

def fetch_random_card() -> Optional[Dict]:
    try:
        resp = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
        resp.raise_for_status()
        card = resp.json()
        logger.info(f"Fetched card: {card['name']} ({card['id']})")
        return card
    except Exception as e:
        logger.error(f"Error fetching card: {e}")
        return None

def create_analysis_prompt(card: Dict) -> str:
    return f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]].

Include:
- TL;DR summary
- Detailed card mechanics and interactions
- Strategic uses, combos, and synergies
- Deckbuilding roles and archetypes
- Format viability and competitive context
- Rules interactions and technical notes
- Art, flavor, and historical context
- Summary of key points (use a different section title for this)

Use natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 2000 words. Do not mention yourself or the analysis process.
Wrap up with a conclusion summary

Card details:
Name: {card['name']}
Mana Cost: {card.get('mana_cost', 'N/A')}
Type: {card.get('type_line', 'N/A')}
Text: {card.get('oracle_text', 'N/A')}
{f"P/T: {card.get('power')}/{card.get('toughness')}" if 'power' in card else ''}
"""

def create_polish_prompt(card: Dict, raw_analysis: str) -> str:
    return f"""Polish and elevate the following Magic: The Gathering card review to sound like an experienced player with deep knowledge of archetypes and deckbuilding. Improve clarity, flow, and insight, but do not shorten or omit any important details. Use natural paragraphs and markdown headers.

Original review:
---
{raw_analysis}
---

Moderate use of specific card examples in [[double brackets]].
Do not use [[double brackets]] for any mention of {card['name']}.
Limit use bullet points.
Write at least 2357 words.
Do not mention yourself or the analysis process.
"""

def generate_analysis(card: Dict) -> Optional[str]:
    prompt = create_analysis_prompt(card)
    try:
        logger.info(f"Generating analysis for {card['name']} using {LLM_MODEL}")
        response = ollama.generate(
            model=LLM_MODEL,
            prompt=prompt,
            options={'timeout': 300}
        )
        text = response.get('response', '')
        if len(text) < 1000:
            logger.warning(f"Analysis too short for {card['name']}")
            return None
        logger.info(f"Analysis generated ({len(text)} chars)")
        return text
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None

def save_to_database(card: dict, analysis: str) -> bool:
    """Send analysis to Flask API endpoint instead of writing directly to MongoDB."""
    try:
        payload = {
            "uuid": card["id"],
            "analysis": analysis,
            "card_data": card  # send the full card dict!
        }
        api_url = f"{MTGABYSS_BASE_URL}/api/submit_work"
        resp = requests.post(api_url, json=payload, timeout=30)
        resp.raise_for_status()
        if resp.json().get("status") == "ok":
            logger.info(f"Submitted analysis for {card['name']} to API")
            return True
        else:
            logger.error(f"API error: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"API submit error: {e}")
        return False

def send_discord_notification(card: Dict) -> bool:
    if not DISCORD_WEBHOOK_URL:
        logger.warning("No Discord webhook URL configured")
        return False
    try:
        card_name = card['name']
        card_url = f"{MTGABYSS_BASE_URL}/card/{card['id']}"
        image_url = card.get('image_uris', {}).get('normal', '')
        embed = {
            "title": f"✨ New Analysis: {card_name}",
            "description": f"Comprehensive analysis completed for [[{card_name}]]",
            "url": card_url,
            "color": 0x00FF00,
            "fields": [
                {"name": "Type", "value": card.get('type_line', 'Unknown'), "inline": True},
                {"name": "Mana Cost", "value": card.get('mana_cost', 'N/A'), "inline": True},
                {"name": "Set", "value": card.get('set_name', 'Unknown'), "inline": True}
            ],
            "footer": {"text": f"MTGAbyss • {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
        }
        if image_url:
            embed["thumbnail"] = {"url": image_url}
        payload = {"embeds": [embed]}
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Sent Discord notification for {card_name}")
        return True
    except Exception as e:
        logger.error(f"Discord notification error: {e}")
        return False

def main():
    print(f"""
🃏 MTG Card Analysis Worker (Serial)
===================================
Model: {LLM_MODEL}
Database: {MONGODB_URI}
Discord: {'✅' if DISCORD_WEBHOOK_URL else '❌'}
MTGAbyss URL: {MTGABYSS_BASE_URL}

Press Ctrl+C to stop.
""")
    while True:
        card = fetch_random_card()
        if not card:
            time.sleep(10)
            continue

        # First pass: generate raw analysis
        raw_analysis = generate_analysis(card)
        if not raw_analysis:
            logger.error(f"Failed to generate analysis for {card['name']}")
            time.sleep(5)
            continue

        # Second pass: polish the analysis
        polish_prompt = create_polish_prompt(card, raw_analysis)
        try:
            logger.info(f"Polishing analysis for {card['name']} using {LLM_MODEL}")
            response = ollama.generate(
                model=LLM_MODEL,
                prompt=polish_prompt,
                options={'timeout': 300}
            )
            polished_analysis = response.get('response', '')
            if len(polished_analysis) < 1000:
                logger.warning(f"Polished analysis too short for {card['name']}")
                continue
            logger.info(f"Polished analysis generated ({len(polished_analysis)} chars)")
        except Exception as e:
            logger.error(f"LLM error during polish: {e}")
            continue

        if save_to_database(card, polished_analysis):
            send_discord_notification(card)

if __name__ == "__main__":
    main()