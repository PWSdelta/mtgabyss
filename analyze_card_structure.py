#!/usr/bin/env python3
"""
Analyze MTGAbyss card structure to understand available identifiers
"""

import os
from pymongo import MongoClient
import json

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'

def main():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    cards_collection = db['cards']
    
    print("🔍 Analyzing card structure...")
    
    # Get a sample of cards
    sample_cards = list(cards_collection.find().limit(5))
    
    if not sample_cards:
        print("❌ No cards found in collection")
        return
    
    print(f"\n📊 Sample of {len(sample_cards)} cards:")
    
    for i, card in enumerate(sample_cards):
        print(f"\n--- Card {i+1}: {card.get('name', 'Unknown')} ---")
        
        # Show key identifier fields
        identifiers = {}
        for field in ['uuid', 'id', 'oracle_id', 'scryfall_id', '_id']:
            if field in card:
                identifiers[field] = str(card[field])[:50]  # Truncate long values
        
        print("Identifiers:")
        for field, value in identifiers.items():
            print(f"  {field}: {value}")
        
        # Check for dual-faced structure
        if 'card_faces' in card:
            print(f"  card_faces: {len(card['card_faces'])} faces")
            for j, face in enumerate(card['card_faces']):
                print(f"    Face {j+1}: {face.get('name', 'Unknown face')}")
        
        # Show all top-level fields
        print("All fields:")
        field_names = list(card.keys())
        print(f"  {', '.join(field_names)}")
    
    # Count null UUIDs
    null_count = cards_collection.count_documents({'uuid': {'$in': [None, '']}})
    total_count = cards_collection.count_documents({})
    
    print(f"\n📈 Statistics:")
    print(f"  Total cards: {total_count}")
    print(f"  Cards with null/empty UUID: {null_count}")
    print(f"  Cards with valid UUID: {total_count - null_count}")
    
    client.close()

if __name__ == "__main__":
    main()
