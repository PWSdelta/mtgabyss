#!/usr/bin/env python3
"""
Move cards from pending to main collection for processing
========================================================

Simple script to move cards from the pending collection back to the main 
collection so the worker can process them. Supports moving all cards or 
just a specific number.

Usage:
  python move_pending_to_main.py --all
  python move_pending_to_main.py --limit 100
  python move_pending_to_main.py --commanders-only --limit 50
"""

import argparse
from pymongo import MongoClient
import os

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def move_cards_to_main(limit=None, commanders_only=False):
    """Move cards from pending to main collection"""
    
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    # Build query
    query = {}
    if commanders_only:
        query['is_commander'] = True
    
    # Get cards to move
    if limit:
        cards_to_move = list(db['cards_pending'].find(query).limit(limit))
        print(f"Moving {len(cards_to_move)} cards to main collection...")
    else:
        count = db['cards_pending'].count_documents(query)
        print(f"Moving all {count} cards to main collection...")
        cards_to_move = list(db['cards_pending'].find(query))
    
    if not cards_to_move:
        print("No cards found to move.")
        return
    
    # Insert into main collection
    db['cards'].insert_many(cards_to_move)
    
    # Remove from pending collection
    card_ids = [card['_id'] for card in cards_to_move]
    db['cards_pending'].delete_many({'_id': {'$in': card_ids}})
    
    print(f"✅ Successfully moved {len(cards_to_move)} cards from pending to main collection.")
    
    # Show current counts
    main_count = db['cards'].count_documents({})
    pending_count = db['cards_pending'].count_documents({})
    print(f"Current counts: Main={main_count}, Pending={pending_count}")
    
    client.close()

def main():
    parser = argparse.ArgumentParser(description='Move cards from pending to main collection')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--all', action='store_true', help='Move all cards from pending to main')
    group.add_argument('--limit', type=int, help='Move specific number of cards')
    parser.add_argument('--commanders-only', action='store_true', help='Only move commanders')
    
    args = parser.parse_args()
    
    if args.all:
        move_cards_to_main(commanders_only=args.commanders_only)
    else:
        move_cards_to_main(limit=args.limit, commanders_only=args.commanders_only)

if __name__ == "__main__":
    main()
