#!/usr/bin/env python3
"""
MTG Card Analysis Worker using Google Gemini
Fetches random cards from Scryfall, generates a deep-dive analysis,
saves to MongoDB, and posts to Discord.
"""

import os
import time
import requests
import logging
from datetime import datetime
from typing import Dict, Optional
import google.generativeai as genai
from pymongo import MongoClient
from dotenv import load_dotenv
import argparse
load_dotenv()

# --- Config ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')
SCRYFALL_API_BASE = 'https://api.scryfall.com'

if not GEMINI_API_KEY:
    raise RuntimeError("Set GEMINI_API_KEY in your environment.")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER_GEMINI - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def simple_log(msg):
    print(msg)

def fetch_random_card_fallback() -> Optional[Dict]:
    """Fallback to Scryfall if our API is unavailable"""
    try:
        resp = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
        resp.raise_for_status()
        card = resp.json()
        simple_log(f"Fallback - Fetched card: {card['name']} ({card['id']})")
        card['image_uris'] = card.get('image_uris', {})
        return card
    except Exception as e:
        simple_log(f"Fallback error fetching card: {e}")
        return None

def fetch_unreviewed_card_batch(batch_size=5) -> Optional[list]:
    """Fetch a batch of unreviewed cards from our MTGAbyss API"""
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

def generate_analysis_gemini(prompt: str) -> Optional[str]:
    """Generate analysis using Gemini"""
    try:
        logger.info(f"Generating analysis using {GEMINI_MODEL}")
        start_time = time.time()
        
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=8192,
                temperature=0.7,
            )
        )
        
        elapsed = time.time() - start_time
        logger.info(f"Analysis generation took {elapsed:.2f} seconds")
        
        text = response.text
        if len(text) < 1000:
            logger.warning(f"Analysis too short")
            return None
        logger.info(f"Analysis generated ({len(text)} chars)")
        return text
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None

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
    parser = argparse.ArgumentParser(description="MTGAbyss Card Analysis Worker (Gemini)")
    parser.add_argument('--limit', type=int, default=None, help='Maximum number of cards to process before exiting')
    args = parser.parse_args()

    print(f"""
MTG Card Analysis Worker (Gemini Batch Mode)
==========================================
Model: {GEMINI_MODEL}
Database: {MONGODB_URI}
Discord: {'✅' if DISCORD_WEBHOOK_URL else '❌'}
MTGAbyss URL: {MTGABYSS_BASE_URL}
Card Source: {MTGABYSS_BASE_URL}/api/get_random_unreviewed

This worker will process unreviewed cards from your database in batches using Google Gemini.
Press Ctrl+C to stop.
--limit: {args.limit if args.limit is not None else 'unlimited'}
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

    print("Starting Gemini worker batch loop...\n")
    BATCH_SIZE = 3  # Smaller batch for Gemini due to rate limits
    processed_count = 0
    
    while True:
        if args.limit is not None and processed_count >= args.limit:
            print(f"Reached processing limit of {args.limit} cards. Exiting.")
            break
            
        round_start = time.time()
        cards_batch = fetch_unreviewed_card_batch(BATCH_SIZE)
        if not cards_batch:
            simple_log("No unreviewed cards available, waiting 60 seconds...")
            time.sleep(60)
            continue

        batch_payload = []
        for card in cards_batch:
            if args.limit is not None and processed_count >= args.limit:
                print(f"Reached processing limit of {args.limit} cards. Exiting.")
                break
                
            # First pass: generate raw analysis
            raw_analysis = generate_analysis_gemini(create_analysis_prompt(card))
            if not raw_analysis:
                logger.error(f"Failed to generate analysis for {card['name']}")
                continue

            # Second pass: polish the analysis
            polished_analysis = generate_analysis_gemini(create_polish_prompt(card, raw_analysis))
            if not polished_analysis:
                logger.warning(f"Failed to polish analysis for {card['name']}, using raw analysis")
                polished_analysis = raw_analysis

            # If card is not English, generate native language analysis
            native_lang = card.get('lang', 'en')
            native_analysis = None
            if native_lang != 'en':
                logger.info(f"Card {card['name']} is in {native_lang}. Generating native language analysis...")
                native_prompt = f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]] in {native_lang} (the card's printed language).

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
{f'P/T: {card.get('power')}/{card.get('toughness')}' if 'power' in card else ''}
"""
                native_analysis = generate_analysis_gemini(native_prompt)
                if native_analysis:
                    logger.info(f"Native language analysis generated ({len(native_analysis)} chars)")

            # Display analysis
            print("\n" + "="*80 + "\n")
            print(f"Analysis for card: {card['name']} (Gemini)")
            print(polished_analysis)
            if native_analysis:
                print("\n--- Native Language Analysis ---\n")
                print(native_analysis)
            print("\n" + "="*80 + "\n")

            # Prepare payload for batch submit
            analysis_dict = {
                "long_form": polished_analysis,
                "analyzed_at": datetime.now().isoformat(),
                "model_used": GEMINI_MODEL
            }
            if native_analysis:
                analysis_dict["native_language_long_form"] = native_analysis
                
            payload = {
                "uuid": card.get("uuid", card.get("id")),
                "analysis": analysis_dict,
                "category": "mtg",
                "card_data": card
            }
            batch_payload.append(payload)
            processed_count += 1
            
            # Rate limiting for Gemini
            time.sleep(1)

        if batch_payload:
            if save_batch_to_database(batch_payload):
                elapsed = time.time() - round_start
                logger.info(f"Finished batch of {len(batch_payload)} cards in {elapsed:.2f} seconds.")
        else:
            simple_log("No analyses to submit for this batch.")

if __name__ == "__main__":
    main()
