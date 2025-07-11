#!/usr/bin/env python3
"""
Quick Guide Detection Diagnostic
==============================

Checks how existing guides are stored in your database to fix the analysis script.
"""

from pymongo import MongoClient
import json

MONGODB_URI = 'mongodb://localhost:27017'
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'

def check_guide_structures():
    client = MongoClient(MONGODB_URI)
    db = client[DATABASE_NAME]
    
    print("🔍 Checking guide data structures...")
    
    # Check all collections
    collections = db.list_collection_names()
    print(f"📦 Available collections: {collections}")
    
    for collection_name in ['cards', 'pending_guide', 'completed_guides']:
        if collection_name in collections:
            print(f"\n🗃️ Checking {collection_name} collection:")
            collection = db[collection_name]
            
            # Get total count
            total = collection.count_documents({})
            print(f"  Total documents: {total:,}")
            
            if total > 0:
                # Sample cards from this collection
                sample_cards = list(collection.find({}).limit(5))
                check_collection_samples(sample_cards, collection_name, collection)
    
    client.close()

def check_collection_samples(sample_cards, collection_name, collection):
    
    structure_types = {}
    
    for i, card in enumerate(sample_cards):
        print(f"\n📋 Card {i+1}: {card.get('name', 'Unknown')}")
        
        # Check what guide-related fields exist
        guide_fields = []
        for field in ['guide_sections', 'sections', 'full_guide', 'section_count', 'unguided']:
            if field in card:
                guide_fields.append(field)
                value = card[field]
                if isinstance(value, dict):
                    print(f"  {field}: dict with {len(value)} keys - {list(value.keys())[:3]}...")
                elif isinstance(value, list):
                    print(f"  {field}: list with {len(value)} items")
                else:
                    print(f"  {field}: {value}")
        
        if not guide_fields:
            print("  ❌ No guide fields found")
        
        # Try to count sections with different methods
        section_count = 0
        
        # Method 1: section_count field
        if card.get('section_count'):
            section_count = max(section_count, card['section_count'])
            print(f"  📊 Method 1 (section_count field): {card['section_count']}")
        
        # Method 2: guide_sections dict
        if card.get('guide_sections') and isinstance(card['guide_sections'], dict):
            count = len([k for k, v in card['guide_sections'].items() if v and v.get('content')])
            section_count = max(section_count, count)
            print(f"  📊 Method 2 (guide_sections dict): {count}")
        
        # Method 3: individual section fields
        section_fields = ['tldr', 'mechanics', 'strategic', 'advanced', 'mistakes', 'conclusion']
        individual_count = sum(1 for field in section_fields if card.get(field))
        section_count = max(section_count, individual_count)
        if individual_count > 0:
            print(f"  📊 Method 3 (individual fields): {individual_count}")
        
        print(f"  🎯 Final section count: {section_count}")
        
        # Track structure types
        structure_key = tuple(sorted(guide_fields))
        if structure_key not in structure_types:
            structure_types[structure_key] = 0
        structure_types[structure_key] += 1
    
    print(f"\n📈 Structure Types Found:")
    for structure, count in structure_types.items():
        print(f"  {structure}: {count} cards")
    
    # Quick count of cards with any guide data
    print(f"\n🔢 Quick Counts:")
    
    total_cards = cards.count_documents({})
    print(f"  Total cards: {total_cards:,}")
    
    with_section_count = cards.count_documents({'section_count': {'$exists': True, '$gt': 0}})
    print(f"  Cards with section_count > 0: {with_section_count:,}")
    
    with_guide_sections = cards.count_documents({'guide_sections': {'$exists': True, '$ne': {}}})
    print(f"  Cards with guide_sections: {with_guide_sections:,}")
    
    with_full_guide_true = cards.count_documents({'full_guide': True})
    print(f"  Cards with full_guide: true: {with_full_guide_true:,}")
    
    unguided_false = cards.count_documents({'unguided': False})
    print(f"  Cards with unguided: false: {unguided_false:,}")
    
    # Check for individual section fields
    with_tldr = cards.count_documents({'tldr': {'$exists': True, '$ne': None}})
    print(f"  Cards with tldr field: {with_tldr:,}")
    
    client.close()

if __name__ == "__main__":
    check_guide_structures()
