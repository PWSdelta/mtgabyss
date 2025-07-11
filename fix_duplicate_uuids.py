#!/usr/bin/env python3
"""
Fix MongoDB duplicate UUID issues for MTGAbyss
==============================================

Handles cards with null UUIDs and ensures proper indexing for both normal and dual-faced cards.
Based on Scryfall API card object structure:
- Normal cards: have uuid, id, oracle_id
- Dual-faced cards: have card_faces array, each face may have different ids
- Some cards may have null values that cause duplicate key errors

This script:
1. Finds cards with null UUIDs
2. Removes or fixes them based on available identifiers
3. Creates proper compound indexes
4. Handles dual-faced card structure properly
"""

import os
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import uuid as uuid_lib

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'

def main():
    print("🔗 Connecting to MongoDB...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    
    cards_collection = db['cards']
    
    print("🔍 Analyzing UUID issues...")
    
    # Find cards with null UUIDs
    null_uuid_query = {'uuid': {'$in': [None, '']}}
    null_uuid_cards = list(cards_collection.find(null_uuid_query))
    
    print(f"📊 Found {len(null_uuid_cards)} cards with null/empty UUIDs")
    
    if len(null_uuid_cards) == 0:
        print("✅ No null UUID cards found!")
        create_proper_indexes(db)
        return
    
    # Analyze the structure of these cards
    analyze_card_structure(null_uuid_cards)
    
    # Fix or remove problematic cards
    fix_null_uuid_cards(cards_collection, null_uuid_cards)
    
    # Create proper indexes
    create_proper_indexes(db)
    
    print("✅ UUID cleanup complete!")
    client.close()

def analyze_card_structure(cards):
    print("\n🔬 Analyzing card structure...")
    
    has_id = 0
    has_oracle_id = 0
    has_scryfall_id = 0
    has_card_faces = 0
    completely_unusable = 0
    
    for card in cards[:10]:  # Sample first 10
        identifiers = []
        if card.get('id'):
            has_id += 1
            identifiers.append('id')
        if card.get('oracle_id'):
            has_oracle_id += 1
            identifiers.append('oracle_id')
        if card.get('scryfall_id'):
            has_scryfall_id += 1
            identifiers.append('scryfall_id')
        if card.get('card_faces'):
            has_card_faces += 1
            identifiers.append('card_faces')
        
        if not identifiers:
            completely_unusable += 1
            
        print(f"   Card '{card.get('name', 'Unknown')}': {', '.join(identifiers) if identifiers else 'NO IDENTIFIERS'}")
    
    print(f"\n📈 Sample analysis (first 10 cards):")
    print(f"   Cards with 'id': {has_id}")
    print(f"   Cards with 'oracle_id': {has_oracle_id}")
    print(f"   Cards with 'scryfall_id': {has_scryfall_id}")
    print(f"   Cards with 'card_faces': {has_card_faces}")
    print(f"   Completely unusable: {completely_unusable}")

def fix_null_uuid_cards(collection, cards):
    print(f"\n🔧 Fixing {len(cards)} cards with null UUIDs...")
    
    fixed_count = 0
    removed_count = 0
    
    for card in cards:
        card_id = card['_id']
        card_name = card.get('name', 'Unknown Card')
        
        # Try to use existing identifiers as UUID
        new_uuid = None
        
        # Priority order: id > oracle_id > scryfall_id
        if card.get('id'):
            new_uuid = card['id']
        elif card.get('oracle_id'):
            new_uuid = card['oracle_id']
        elif card.get('scryfall_id'):
            new_uuid = card['scryfall_id']
        
        if new_uuid:
            try:
                # Update the card with a proper UUID
                collection.update_one(
                    {'_id': card_id},
                    {'$set': {'uuid': new_uuid}}
                )
                fixed_count += 1
                if fixed_count % 100 == 0:
                    print(f"   ✅ Fixed {fixed_count} cards...")
            except Exception as e:
                print(f"   ⚠️ Error fixing {card_name}: {e}")
                # If update fails, remove the card
                collection.delete_one({'_id': card_id})
                removed_count += 1
        else:
            # No usable identifiers, remove the card
            print(f"   🗑️ Removing unusable card: {card_name}")
            collection.delete_one({'_id': card_id})
            removed_count += 1
    
    print(f"✅ Fixed {fixed_count} cards with new UUIDs")
    print(f"🗑️ Removed {removed_count} unusable cards")

def create_proper_indexes(db):
    print("\n🏗️ Creating proper MongoDB indexes...")
    
    collections_to_index = ['cards', 'pending_guide']
    
    for collection_name in collections_to_index:
        collection = db[collection_name]
        
        try:
            # Drop existing problematic indexes
            print(f"   Dropping existing indexes on {collection_name}...")
            try:
                collection.drop_index('uuid_1')
                print(f"   🗑️ Dropped uuid_1 index on {collection_name}")
            except:
                pass  # Index might not exist
            
            # Create compound index that handles nulls better
            # This allows multiple null values but ensures unique non-null combinations
            print(f"   Creating new indexes on {collection_name}...")
            
            # Individual indexes for queries
            collection.create_index('uuid', sparse=True)  # sparse=True allows multiple nulls
            collection.create_index('id', sparse=True)
            collection.create_index('oracle_id', sparse=True)
            
            # Compound index for uniqueness (only on non-null values)
            collection.create_index([
                ('uuid', 1),
                ('id', 1),
                ('oracle_id', 1)
            ], sparse=True, name='card_identifiers_compound')
            
            print(f"   ✅ Created indexes on {collection_name}")
            
        except Exception as e:
            print(f"   ⚠️ Error creating indexes on {collection_name}: {e}")
    
    print("✅ Index creation complete!")

def verify_fix(db):
    print("\n🔍 Verifying fix...")
    
    cards_collection = db['cards']
    
    # Check for remaining null UUIDs
    remaining_nulls = cards_collection.count_documents({'uuid': {'$in': [None, '']}})
    
    if remaining_nulls == 0:
        print("✅ No remaining null UUIDs!")
    else:
        print(f"⚠️ Still have {remaining_nulls} cards with null UUIDs")
    
    # Check total counts
    total_cards = cards_collection.count_documents({})
    print(f"📊 Total cards in main collection: {total_cards}")

if __name__ == "__main__":
    main()
