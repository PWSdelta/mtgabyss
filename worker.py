#!/usr/bin/env python3
"""
MTG Card Analysis Worker (Simple Serial Version)
Fetches random cards from Scryfall, generates a deep-dive analysis,
saves to MongoDB, and posts to Discord.
"""

import os
import time
import requests
import logging
from datetime import datetime
from typing import Dict, Optional
import ollama
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()

# --- Config ---
LLM_MODEL = os.getenv('LLM_MODEL', 'llama3.1:8b')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')  # For Discord links
SCRYFALL_API_BASE = 'https://api.scryfall.com'

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def simple_log(msg):
    print(msg)

# # --- MongoDB ---~
# client = MongoClient(MONGODB_URI)
# db = client.mtg
# cards = db.cards

def fetch_random_card_fallback() -> Optional[Dict]:
    """Fallback to Scryfall if our API is unavailable"""
    try:
        resp = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
        resp.raise_for_status()
        card = resp.json()
        simple_log(f"Fallback - Fetched card: {card['name']} ({card['id']})")
        card['imageUris'] = card.get('image_uris', {})
        return card
    except Exception as e:
        simple_log(f"Fallback error fetching card: {e}")
        return None


# --- Batch fetch unreviewed cards ---
def fetch_unreviewed_card_batch(batch_size=100) -> Optional[list]:
    """Fetch a batch of unreviewed cards from our MTGAbyss API instead of Scryfall"""
    try:
        api_url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed?lang=en&limit={batch_size}'
        resp = requests.get(api_url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data['status'] == 'no_cards':
            simple_log("No more unreviewed cards available!")
            return None
        if data['status'] != 'success' or not data.get('cards'):
            simple_log(f"API error: {data.get('message', 'Unknown error')}")
            return None
        cards_batch = data['cards']
        for card in cards_batch:
            card['id'] = card['uuid']
            card['image_uris'] = card.get('image_uris', {})
        simple_log(f"Fetched {len(cards_batch)} unreviewed cards. Progress: {data['total_unreviewed']:,} unreviewed cards remaining")
        return cards_batch
    except Exception as e:
        simple_log(f"Error fetching unreviewed card batch from API: {e}")
        simple_log("Falling back to Scryfall random card...")
        fallback = fetch_random_card_fallback()
        return [fallback] if fallback else None

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

Use natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 3357 words. Do not mention yourself or the analysis process.
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
Write at least 3357 words.
Do not mention yourself or the analysis process.
"""

def generate_analysis(card: Dict) -> Optional[str]:
    prompt = create_analysis_prompt(card)
    try:
        logger.info(f"Generating analysis for {card['name']} using {LLM_MODEL}")
        start_time = time.time()
        response = ollama.generate(
            model=LLM_MODEL,
            prompt=prompt,
            options={'timeout': 300}
        )
        elapsed = time.time() - start_time
        logger.info(f"Analysis generation took {elapsed:.2f} seconds for {card['name']}")
        text = response.get('response', '')
        if len(text) < 1000:
            logger.warning(f"Analysis too short for {card['name']}")
            return None
        logger.info(f"Analysis generated ({len(text)} chars)")
        return text
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None


# --- Batch save to database ---
def save_batch_to_database(card_analysis_batch: list) -> bool:
    """Submit a batch of analyses to the API in one request"""
    try:
        api_url = f"{MTGABYSS_BASE_URL}/api/submit_work"
        resp = requests.post(api_url, json=card_analysis_batch, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "ok":
            simple_log(f"Submitted batch of {len(card_analysis_batch)} analyses to API")
            return True
        else:
            simple_log(f"API error: {resp.text}")
            return False
    except Exception as e:
        simple_log(f"API batch submit error: {e}")
        return False


def main():
    # Check if our API is working
    print(f"""
MTG Card Analysis Worker (Batch Mode)
=====================================
Model: {LLM_MODEL}
Database: {MONGODB_URI}
Discord: {'✅' if DISCORD_WEBHOOK_URL else '❌'}
MTGAbyss URL: {MTGABYSS_BASE_URL}
Card Source: {MTGABYSS_BASE_URL}/api/get_random_unreviewed

This worker will process unreviewed cards from your database in batches.
Press Ctrl+C to stop.
""")
    try:
        resp = requests.get(f'{MTGABYSS_BASE_URL}/api/stats', timeout=60)
        if resp.status_code == 200:
            stats = resp.json().get('stats', {})
            print(f"📊 Database Status:")
            print(f"   Total cards: {stats.get('total_cards', 'Unknown'):,}")
            print(f"   Reviewed: {stats.get('reviewed_cards', 'Unknown'):,}")
            print(f"   Unreviewed: {stats.get('unreviewed_cards', 'Unknown'):,}")
            print(f"   Progress: {stats.get('completion_percentage', 0):.1f}%")
            print()
        else:
            print("⚠️  Could not fetch stats from MTGAbyss API")
    except Exception as e:
        print(f"⚠️  API connection test failed: {e}")
        print("Will fallback to Scryfall random cards if needed.")

    print("Starting worker batch loop...\n")
    BATCH_SIZE = 5
    while True:
        round_start = time.time()
        cards_batch = fetch_unreviewed_card_batch(BATCH_SIZE)
        if not cards_batch:
            simple_log("No unreviewed cards available, waiting 60 seconds...")
            time.sleep(60)
            continue

        batch_payload = []
        for card in cards_batch:
            # First pass: generate raw analysis (English)
            raw_analysis = generate_analysis(card)
            if not raw_analysis:
                logger.error(f"Failed to generate analysis for {card['name']}")
                continue

            # Second pass: polish the analysis (English)
            polish_prompt = create_polish_prompt(card, raw_analysis)
            try:
                logger.info(f"Polishing analysis for {card['name']} using {LLM_MODEL}")
                start_time = time.time()
                response = ollama.generate(
                    model=LLM_MODEL,
                    prompt=polish_prompt,
                    options={'timeout': 300}
                )
                elapsed = time.time() - start_time
                logger.info(f"Polishing took {elapsed:.2f} seconds for {card['name']}")
                polished_analysis = response.get('response', '')
                if len(polished_analysis) < 1000:
                    logger.warning(f"Polished analysis too short for {card['name']}")
                    continue
                logger.info(f"Polished analysis generated ({len(polished_analysis)} chars)")
            except Exception as e:
                logger.error(f"LLM error during polish: {e}")
                continue

            # If card is not English, generate native language analysis
            native_lang = card.get('lang', 'en')
            native_analysis = None
            if native_lang != 'en':
                logger.info(f"Card {card['name']} is in {native_lang}. Generating native language analysis...")
                native_prompt = f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]] in {native_lang} (the card's printed language).\n\nInclude:\n- TL;DR summary\n- Detailed card mechanics and interactions\n- Strategic uses, combos, and synergies\n- Deckbuilding roles and archetypes\n- Format viability and competitive context\n- Rules interactions and technical notes\n- Art, flavor, and historical context\n- Summary of key points (use a different section title for this)\n\nUse natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 3357 words. Do not mention yourself or the analysis process.\nWrap up with a conclusion summary\n\nCard details:\nName: {card['name']}\nMana Cost: {card.get('mana_cost', 'N/A')}\nType: {card.get('type_line', 'N/A')}\nText: {card.get('oracle_text', 'N/A')}\n{f'P/T: {card.get('power')}/{card.get('toughness')}' if 'power' in card else ''}\n"""
                try:
                    response = ollama.generate(
                        model=LLM_MODEL,
                        prompt=native_prompt,
                        options={'timeout': 300}
                    )
                    native_analysis = response.get('response', '')
                    if len(native_analysis) < 1000:
                        logger.warning(f"Native language analysis too short for {card['name']}")
                        native_analysis = None
                    else:
                        logger.info(f"Native language analysis generated ({len(native_analysis)} chars)")
                except Exception as e:
                    logger.error(f"LLM error during native language analysis: {e}")
                    native_analysis = None

            # Add a newline between each card analysis for clarity
            print("\n" + "="*80 + "\n")
            print(f"Analysis for card: {card['name']}")
            print(polished_analysis)
            if native_analysis:
                print("\n--- Native Language Analysis ---\n")
                print(native_analysis)
            print("\n" + "="*80 + "\n")

            # Prepare payload for batch submit
            card_category = 'mtg'
            analysis_dict = {
                "long_form": polished_analysis,
                "analyzed_at": datetime.now().isoformat(),
                "model_used": LLM_MODEL
            }
            if native_analysis:
                analysis_dict["native_language_long_form"] = native_analysis
            payload = {
                "uuid": card.get("uuid", card.get("id")),
                "analysis": analysis_dict,
                "category": card_category,
                "card_data": card
            }
            card['imageUris'] = card.get('image_uris', {})
            batch_payload.append(payload)

        if batch_payload:
            if save_batch_to_database(batch_payload):
                # for card in cards_batch:
                #     send_discord_notification(card)
                elapsed = time.time() - round_start
                logger.info(f"[SimpleLog] Finished batch of {len(batch_payload)} cards in {elapsed:.2f} seconds.")
        else:
            simple_log("No analyses to submit for this batch.")

if __name__ == "__main__":
    main()