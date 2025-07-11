#!/usr/bin/env python3
"""
Mark Incomplete Guides Script
============================

Marks cards without at least 6 guide sections as 'full_guide: false' 
and optionally moves them to a pending collection for processing.

Usage:
  python mark_incomplete_guides.py --analyze
  python mark_incomplete_guides.py --mark-incomplete
  python mark_incomplete_guides.py --move-to-pending
  python mark_incomplete_guides.py --all
"""

import argparse
import sys
import os
from pymongo import MongoClient
from datetime import datetime, timezone
from collections import defaultdict

# MongoDB configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'
PENDING_COLLECTION = 'pending_guides'

def get_mongodb_client():
    """Get MongoDB client connection"""
    try:
        client = MongoClient(MONGODB_URI)
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"❌ Error connecting to MongoDB: {e}")
        return None

def count_guide_sections(card):
    """Count the number of guide sections a card has"""
    # First check if we already have section_count stored
    if card.get('section_count') is not None:
        return card['section_count']
    
    sections = 0
    
    # Check guide_sections field (primary method used by direct worker)
    if card.get('guide_sections'):
        if isinstance(card['guide_sections'], dict):
            # Count non-empty sections
            sections = len([k for k, v in card['guide_sections'].items() if v and v.get('content')])
        elif isinstance(card['guide_sections'], list):
            sections = len(card['guide_sections'])
    
    # Check legacy sections field
    if card.get('sections'):
        if isinstance(card['sections'], list):
            sections = max(sections, len(card['sections']))
        elif isinstance(card['sections'], dict):
            sections = max(sections, len(card['sections']))
    
    # Check for individual section fields (legacy method)
    section_fields = ['tldr', 'mechanics', 'strategic', 'advanced', 'mistakes', 'conclusion',
                     'deckbuilding', 'format', 'scenarios', 'history', 'flavor', 'budget']
    
    individual_sections = sum(1 for field in section_fields if card.get(field))
    sections = max(sections, individual_sections)
    
    return sections

def analyze_completeness():
    """Analyze guide completeness across all cards"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🔍 Analyzing guide completeness...")
        
        total_cards = cards_collection.count_documents({})
        print(f"📊 Total cards in collection: {total_cards:,}")
        
        if total_cards == 0:
            print("⚠️  No cards found in collection!")
            return
        
        # Analyze all cards
        section_distribution = defaultdict(int)
        incomplete_cards = []
        complete_cards = []
        
        cursor = cards_collection.find({})
        for card in cursor:
            section_count = count_guide_sections(card)
            
            # Categorize by section count
            if section_count == 0:
                section_distribution['0'] += 1
            elif section_count <= 2:
                section_distribution['1-2'] += 1
            elif section_count <= 5:
                section_distribution['3-5'] += 1
            elif section_count <= 8:
                section_distribution['6-8'] += 1
            elif section_count <= 11:
                section_distribution['9-11'] += 1
            else:
                section_distribution['12+'] += 1
            
            card_info = {
                'name': card.get('name', 'Unknown'),
                'uuid': card.get('uuid'),
                'section_count': section_count,
                'edhrec_rank': card.get('edhrec_rank'),
                'is_commander': card.get('is_commander', False),
                'rarity': card.get('rarity'),
                'colors': card.get('color_identity', []),
                'type_line': card.get('type_line', '')
            }
            
            if section_count < 6:
                incomplete_cards.append(card_info)
            else:
                complete_cards.append(card_info)
        
        # Print distribution
        print(f"\n📈 Guide Section Distribution:")
        for category in ['0', '1-2', '3-5', '6-8', '9-11', '12+']:
            count = section_distribution[category]
            percentage = (count / total_cards * 100) if total_cards > 0 else 0
            sections_label = f"{category:8} sections"
            print(f"  {sections_label}: {count:6,} ({percentage:5.1f}%)")
        
        # Summary
        incomplete_count = len(incomplete_cards)
        complete_count = len(complete_cards)
        completion_rate = (complete_count / total_cards * 100) if total_cards > 0 else 0
        
        print(f"\n🎯 Completeness Summary:")
        print(f"  Incomplete (<6 sections): {incomplete_count:,}")
        print(f"  Complete (6+ sections):   {complete_count:,}")
        print(f"  Completion rate:          {completion_rate:.1f}%")
        
        # Show top incomplete cards by EDHREC rank
        incomplete_with_rank = [c for c in incomplete_cards if c['edhrec_rank']]
        incomplete_with_rank.sort(key=lambda x: x['edhrec_rank'])
        
        print(f"\n🔥 Top 15 Incomplete Cards (by EDHREC popularity):")
        for i, card in enumerate(incomplete_with_rank[:15], 1):
            commander_icon = "👑" if card['is_commander'] else "🃏"
            colors = ''.join(card['colors']) if card['colors'] else 'C'
            print(f"  {i:2d}. {commander_icon} {card['name']:<25} | Rank: {card['edhrec_rank']:>5} | Sections: {card['section_count']}")
        
        # Save analysis
        output_file = '/tmp/guide_completeness_analysis.json'
        import json
        with open(output_file, 'w') as f:
            json.dump({
                'total_cards': total_cards,
                'incomplete_count': incomplete_count,
                'complete_count': complete_count,
                'completion_rate': completion_rate,
                'section_distribution': dict(section_distribution),
                'incomplete_cards': incomplete_cards[:50],  # Top 50 incomplete
                'analysis_date': datetime.now(timezone.utc).isoformat()
            }, f, indent=2, default=str)
        
        print(f"\n💾 Analysis saved to: {output_file}")
        
    except Exception as e:
        print(f"❌ Error during analysis: {e}")
    finally:
        client.close()

def mark_incomplete_cards():
    """Mark all cards with <6 sections as full_guide: false"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🏷️  Marking incomplete cards...")
        
        # Process all cards
        updated_count = 0
        cursor = cards_collection.find({})
        
        for card in cursor:
            section_count = count_guide_sections(card)
            
            # Determine if complete
            is_complete = section_count >= 6
            
            # Update the card
            update_data = {
                'full_guide': is_complete,
                'section_count': section_count,
                'unguided': not is_complete,  # Inverse of full_guide
                'updated_at': datetime.now(timezone.utc)
            }
            
            result = cards_collection.update_one(
                {'_id': card['_id']},
                {'$set': update_data}
            )
            
            if result.modified_count > 0:
                updated_count += 1
        
        print(f"✅ Updated {updated_count:,} cards with completion status")
        
        # Show summary
        complete_count = cards_collection.count_documents({'full_guide': True})
        incomplete_count = cards_collection.count_documents({'full_guide': False})
        
        print(f"📊 Final Status:")
        print(f"  Complete guides (full_guide: true):   {complete_count:,}")
        print(f"  Incomplete guides (full_guide: false): {incomplete_count:,}")
        
    except Exception as e:
        print(f"❌ Error marking cards: {e}")
    finally:
        client.close()

def move_to_pending():
    """Move incomplete cards to pending_guides collection"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        pending_collection = db[PENDING_COLLECTION]
        
        print("📦 Moving incomplete cards to pending collection...")
        
        # Find all incomplete cards
        incomplete_cards = list(cards_collection.find({'full_guide': False}))
        
        if not incomplete_cards:
            print("ℹ️  No incomplete cards found to move")
            return
        
        print(f"📋 Found {len(incomplete_cards):,} incomplete cards to move")
        
        # Add metadata to each card before moving
        for card in incomplete_cards:
            card['moved_to_pending_at'] = datetime.now(timezone.utc)
            card['original_collection'] = CARDS_COLLECTION
            card['reason'] = 'incomplete_guide'
        
        # Insert into pending collection
        if incomplete_cards:
            pending_collection.insert_many(incomplete_cards)
            print(f"✅ Inserted {len(incomplete_cards):,} cards into pending collection")
        
        # Remove from cards collection
        remove_result = cards_collection.delete_many({'full_guide': False})
        print(f"✅ Removed {remove_result.deleted_count:,} incomplete cards from main collection")
        
        # Summary
        remaining_cards = cards_collection.count_documents({})
        pending_cards = pending_collection.count_documents({})
        
        print(f"\n📊 Collection Status:")
        print(f"  Main collection (cards):    {remaining_cards:,}")
        print(f"  Pending collection:         {pending_cards:,}")
        
    except Exception as e:
        print(f"❌ Error moving cards: {e}")
    finally:
        client.close()

def main():
    parser = argparse.ArgumentParser(
        description='Mark incomplete guides and manage collections',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--analyze', action='store_true',
                       help='Analyze guide completeness')
    parser.add_argument('--mark-incomplete', action='store_true',
                       help='Mark cards with <6 sections as incomplete')
    parser.add_argument('--move-to-pending', action='store_true',
                       help='Move incomplete cards to pending collection')
    parser.add_argument('--all', action='store_true',
                       help='Run all operations in sequence')
    
    args = parser.parse_args()
    
    if not any([args.analyze, args.mark_incomplete, args.move_to_pending, args.all]):
        parser.print_help()
        return 1
    
    if args.all:
        print("🚀 Running all operations...\n")
        analyze_completeness()
        print("\n" + "="*50 + "\n")
        mark_incomplete_cards()
        print("\n" + "="*50 + "\n")
        move_to_pending()
    else:
        if args.analyze:
            analyze_completeness()
        
        if args.mark_incomplete:
            mark_incomplete_cards()
        
        if args.move_to_pending:
            move_to_pending()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
