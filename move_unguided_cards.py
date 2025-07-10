#!/usr/bin/env python3
"""
Simple script to move all cards without guide sections from 'cards' to 'pending_guide'
Run once on server to clean up the main collection.
"""

import os
from pymongo import MongoClient

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'

def main():
    print("🔗 Connecting to MongoDB...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    
    cards_collection = db['cards']
    pending_collection = db['pending_guide']
    
    # Find cards without guide sections
    print("🔍 Finding cards without guide sections...")
    query = {
        '$and': [
            {'$or': [
                {'uuid': {'$exists': True, '$ne': None}},
                {'id': {'$exists': True, '$ne': None}},
                {'oracle_id': {'$exists': True, '$ne': None}}
            ]},
            {'$or': [
                {'guide_sections': {'$exists': False}},
                {'guide_sections': None},
                {'guide_sections': []},
                {'guide_sections': {}}
            ]}
        ]
    }
    
    cards_to_move = list(cards_collection.find(query))
    total_count = len(cards_to_move)
    
    if total_count == 0:
        print("✅ No cards found without guide sections!")
        return
    
    print(f"📦 Found {total_count} cards to move to pending_guide")
    
    # Move cards in batches
    batch_size = 1000
    moved_count = 0
    
    for i in range(0, total_count, batch_size):
        batch = cards_to_move[i:i + batch_size]
        
        # Insert batch into pending_guide (upsert to handle duplicates)
        for card in batch:
            try:
                # Use upsert to avoid duplicate key errors
                filter_query = {}
                if card.get('uuid'):
                    filter_query['uuid'] = card['uuid']
                elif card.get('id'):
                    filter_query['id'] = card['id']
                elif card.get('oracle_id'):
                    filter_query['oracle_id'] = card['oracle_id']
                
                pending_collection.replace_one(filter_query, card, upsert=True)
                moved_count += 1
                
                if moved_count % 100 == 0:
                    print(f"   📋 Moved {moved_count}/{total_count} cards...")
                    
            except Exception as e:
                print(f"⚠️  Error moving card {card.get('name', 'Unknown')}: {e}")
                continue
    
    print(f"✅ Successfully moved {moved_count} cards to pending_guide")
    
    # Remove moved cards from main collection
    print("🗑️  Removing cards from main collection...")
    card_ids = []
    for card in cards_to_move:
        if card.get('_id'):
            card_ids.append(card['_id'])
    
    if card_ids:
        result = cards_collection.delete_many({'_id': {'$in': card_ids}})
        print(f"🗑️  Removed {result.deleted_count} cards from main collection")
    
    # Final stats
    main_count = cards_collection.count_documents({})
    pending_count = pending_collection.count_documents({})
    
    print("\n📊 Final Statistics:")
    print(f"   Main 'cards' collection: {main_count}")
    print(f"   'pending_guide' collection: {pending_count}")
    print(f"   Total cards: {main_count + pending_count}")
    
    print("\n✅ Migration complete!")
    client.close()

if __name__ == "__main__":
    main()
