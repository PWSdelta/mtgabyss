#!/usr/bin/env python3
"""
Seed the mentions_histogram collection by scanning all analyzed guides for card mentions.
This script will incrementally add to the histogram, not erase existing data.
"""
import os
import re
from pymongo import MongoClient
from datetime import datetime

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('MTGABYSS_DB', 'mtgabyss')

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
cards = db.cards
mentions_histogram = db.mentions_histogram

def extract_card_mentions(text):
    """Extract card mentions from text using [[Card Name]] and [Card Name] patterns."""
    if not text:
        return []
    mentions = set()
    # [[Card Name]]
    for match in re.findall(r'\[\[([^\]]+)\]\]', text):
        card_name = match.strip()
        if card_name:
            mentions.add(card_name)
    # [Card Name] (not [B], [/B], etc.)
    text_wo_double = re.sub(r'\[\[[^\]]+\]\]', '', text)
    for match in re.findall(r'\[([^\]]+)\]', text_wo_double):
        card_name = match.strip()
        if card_name and len(card_name) > 1 and not re.match(r'^/?[BIU]$', card_name, re.IGNORECASE):
            mentions.add(card_name)
    return list(mentions)

def main():
    print("Scanning all analyzed cards for mentions...")
    analyzed_cards = cards.find({'analysis': {'$exists': True}})
    total_mentions = 0
    updated_cards = set()
    for card in analyzed_cards:
        card_name = card.get('name')
        analysis = card.get('analysis', {})
        # Prefer sectioned guides, but fallback to content/long_form
        content = ''
        if 'sections' in analysis and isinstance(analysis['sections'], dict):
            for section in analysis['sections'].values():
                if isinstance(section, dict) and 'content' in section:
                    content += section['content'] + ' '
        elif 'content' in analysis:
            content = analysis['content']
        elif 'long_form' in analysis:
            content = analysis['long_form']
        mentions = extract_card_mentions(content)
        for mentioned_name in mentions:
            if mentioned_name.lower() == (card_name or '').lower():
                continue  # skip self-mentions
            # Find mentioned card by name (case-insensitive)
            mentioned_card = cards.find_one({'name': {'$regex': f'^{re.escape(mentioned_name)}$', '$options': 'i'}}, {'uuid': 1, 'name': 1})
            if not mentioned_card:
                continue
            uuid = mentioned_card['uuid']
            # Increment histogram
            mentions_histogram.update_one(
                {'uuid': uuid},
                {
                    '$inc': {'mention_count': 1},
                    '$set': {
                        'last_mentioned': datetime.utcnow(),
                        'last_mentioned_in': card_name,
                        'card_name': mentioned_card['name']
                    },
                    '$setOnInsert': {
                        'first_mentioned': datetime.utcnow(),
                        'created_at': datetime.utcnow()
                    }
                },
                upsert=True
            )
            total_mentions += 1
            updated_cards.add(uuid)
    print(f"Done. Updated mentions for {len(updated_cards)} cards. Total mentions added: {total_mentions}")

if __name__ == "__main__":
    main()
