#!/usr/bin/env python3
"""
Fix MongoDB duplicate key error for null UUIDs
==============================================

This script fixes the E11000 duplicate key error by removing cards with null UUIDs
or invalid identifiers from the cards collection.
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
    
    # Find cards with null or missing UUIDs
    print("🔍 Finding cards with null/missing UUIDs...")
    null_uuid_query = {
        '$or': [
            {'uuid': None},
            {'uuid': {'$exists': False}},
            {'uuid': ''},
            {'uuid': {'$in': [None, '', {}]}}
        ]
    }
    
    null_uuid_cards = list(cards_collection.find(null_uuid_query))
    print(f"📦 Found {len(null_uuid_cards)} cards with null/missing UUIDs")
    
    if len(null_uuid_cards) == 0:
        print("✅ No cards with null UUIDs found!")
        
        # Check for duplicate UUIDs
        print("🔍 Checking for duplicate UUIDs...")
        pipeline = [
            {'$group': {'_id': '$uuid', 'count': {'$sum': 1}}},
            {'$match': {'count': {'$gt': 1}}}
        ]
        duplicates = list(cards_collection.aggregate(pipeline))
        
        if duplicates:
            print(f"⚠️  Found {len(duplicates)} duplicate UUIDs:")
            for dup in duplicates[:10]:  # Show first 10
                print(f"   UUID: {dup['_id']} (count: {dup['count']})")
        else:
            print("✅ No duplicate UUIDs found!")
        
        client.close()
        return
    
    # Show some examples
    print("📋 Examples of problematic cards:")
    for i, card in enumerate(null_uuid_cards[:5]):
        name = card.get('name', 'Unknown')
        uuid_val = card.get('uuid', 'MISSING')
        id_val = card.get('id', 'MISSING')
        oracle_id = card.get('oracle_id', 'MISSING')
        print(f"   {i+1}. '{name}' - UUID: {uuid_val}, ID: {id_val}, Oracle: {oracle_id}")
    
    # Option 1: Delete cards with no valid identifiers
    no_valid_id_query = {
        '$and': [
            null_uuid_query,
            {'$or': [
                {'id': {'$in': [None, '', {}]}},
                {'id': {'$exists': False}},
                {'oracle_id': {'$in': [None, '', {}]}},
                {'oracle_id': {'$exists': False}}
            ]}
        ]
    }
    
    no_valid_id_cards = cards_collection.count_documents(no_valid_id_query)
    print(f"\n🗑️  Cards with NO valid identifiers (UUID, ID, or Oracle ID): {no_valid_id_cards}")
    
    if no_valid_id_cards > 0:
        print("   These cards will be DELETED as they cannot be uniquely identified.")
        confirm = input("   Delete cards with no valid identifiers? (y/N): ").lower().strip()
        
        if confirm == 'y':
            result = cards_collection.delete_many(no_valid_id_query)
            print(f"🗑️  Deleted {result.deleted_count} cards with no valid identifiers")
        else:
            print("   Skipped deletion.")
    
    # Option 2: Move remaining null UUID cards to pending_guide
    remaining_null_uuid = cards_collection.count_documents(null_uuid_query)
    
    if remaining_null_uuid > 0:
        print(f"\n📦 Remaining cards with null UUIDs: {remaining_null_uuid}")
        print("   These cards have valid ID or Oracle ID but null UUID.")
        print("   They will be moved to pending_guide collection.")
        
        confirm = input("   Move remaining null UUID cards to pending_guide? (y/N): ").lower().strip()
        
        if confirm == 'y':
            pending_collection = db['pending_guide']
            
            # Move cards to pending_guide
            null_uuid_cards_remaining = list(cards_collection.find(null_uuid_query))
            moved_count = 0
            
            for card in null_uuid_cards_remaining:
                try:
                    # Use ID or Oracle ID as filter for upsert
                    filter_query = {}
                    if card.get('id'):
                        filter_query['id'] = card['id']
                    elif card.get('oracle_id'):
                        filter_query['oracle_id'] = card['oracle_id']
                    else:
                        continue  # Skip if no valid identifier
                    
                    pending_collection.replace_one(filter_query, card, upsert=True)
                    moved_count += 1
                    
                    if moved_count % 100 == 0:
                        print(f"   📋 Moved {moved_count}/{len(null_uuid_cards_remaining)} cards...")
                        
                except Exception as e:
                    print(f"   ⚠️  Error moving card {card.get('name', 'Unknown')}: {e}")
                    continue
            
            print(f"✅ Successfully moved {moved_count} cards to pending_guide")
            
            # Remove moved cards from main collection
            if moved_count > 0:
                result = cards_collection.delete_many(null_uuid_query)
                print(f"🗑️  Removed {result.deleted_count} cards from main collection")
        else:
            print("   Skipped moving cards.")
    
    # Final check
    remaining_null = cards_collection.count_documents(null_uuid_query)
    total_cards = cards_collection.count_documents({})
    
    print(f"\n📊 Final Status:")
    print(f"   Cards with null UUIDs remaining: {remaining_null}")
    print(f"   Total cards in main collection: {total_cards}")
    
    if remaining_null == 0:
        print("✅ All null UUID issues resolved! Your app should start now.")
    else:
        print("⚠️  Some null UUID cards remain. Manual intervention may be needed.")
    
    client.close()

if __name__ == "__main__":
    main()
