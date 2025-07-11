#!/usr/bin/env python3
"""
Move Incomplete Guides Script
============================

Marks cards without at least 6 guide sections as 'full_guide: false' 
and moves them to the pending_guide collection for processing.

This helps maintain data quality by ensuring only complete guides 
remain in the main cards collection.

Usage:
  python move_incomplete_guides.py --analyze
  python move_incomplete_guides.py --mark-incomplete
  python move_incomplete_guides.py --move-to-pending
  python move_incomplete_guides.py --full-cleanup
"""

import argparse
import sys
import os
from pymongo import MongoClient
from datetime import datetime
from typing import Dict, List

# MongoDB configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'
PENDING_COLLECTION = 'pending_guide'

def get_mongodb_client():
    """Get MongoDB client connection"""
    try:
        client = MongoClient(MONGODB_URI)
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None

def count_guide_sections(card: Dict) -> int:
    """Count the number of guide sections for a card"""
    section_count = 0
    
    # Check guide_sections field
    if card.get('guide_sections'):
        if isinstance(card['guide_sections'], list):
            section_count = len(card['guide_sections'])
        elif isinstance(card['guide_sections'], dict):
            section_count = len(card['guide_sections'])
    
    # Check sections field (alternative field name)
    elif card.get('sections'):
        if isinstance(card['sections'], list):
            section_count = len(card['sections'])
        elif isinstance(card['sections'], dict):
            section_count = len(card['sections'])
    
    # Check individual section fields (if stored separately)
    section_fields = ['tldr', 'mechanics', 'strategic', 'advanced', 'mistakes', 'conclusion',
                     'deckbuilding', 'format', 'scenarios', 'history', 'flavor', 'budget']
    
    if section_count == 0:
        for field in section_fields:
            if card.get(field) and isinstance(card[field], str) and len(card[field].strip()) > 10:
                section_count += 1
    
    return section_count

def analyze_guide_completeness():
    """Analyze the completeness of guides in the database"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🔍 Analyzing guide completeness...")
        
        total_cards = cards_collection.count_documents({})
        print(f"📊 Total cards in collection: {total_cards:,}")
        
        # Analyze section counts
        section_distribution = {
            '0': 0, '1-2': 0, '3-5': 0, '6-8': 0, '9-11': 0, '12+': 0
        }
        
        incomplete_cards = []
        complete_cards = []
        
        cursor = cards_collection.find({})
        processed = 0
        
        for card in cursor:
            processed += 1
            if processed % 5000 == 0:
                print(f"  Processed {processed:,} cards...")
            
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
            
            # Track incomplete vs complete
            if section_count < 6:
                incomplete_cards.append({
                    'name': card.get('name'),
                    'uuid': card.get('uuid'),
                    'section_count': section_count,
                    'is_commander': card.get('is_commander', False),
                    'edhrec_rank': card.get('edhrec_rank'),
                    '_id': card['_id']
                })
            else:
                complete_cards.append({
                    'name': card.get('name'),
                    'section_count': section_count
                })
        
        print(f"\n📈 Guide Section Distribution:")
        for category, count in section_distribution.items():
            percentage = (count / total_cards) * 100 if total_cards > 0 else 0
            print(f"  {category:<8} sections: {count:>6,} ({percentage:>5.1f}%)")
        
        print(f"\n🎯 Completeness Summary:")
        incomplete_count = len(incomplete_cards)
        complete_count = len(complete_cards)
        print(f"  Incomplete (<6 sections): {incomplete_count:,}")
        print(f"  Complete (6+ sections):   {complete_count:,}")
        print(f"  Completion rate:          {(complete_count/total_cards)*100:.1f}%")
        
        # Show some examples of incomplete cards
        print(f"\n🔥 Top 15 Incomplete Cards (by EDHREC popularity):")
        incomplete_with_rank = [c for c in incomplete_cards if c.get('edhrec_rank')]
        incomplete_with_rank.sort(key=lambda x: x['edhrec_rank'])
        
        for i, card in enumerate(incomplete_with_rank[:15], 1):
            commander_indicator = "👑" if card['is_commander'] else "🃏"
            print(f"  {i:2d}. {commander_indicator} {card['name']:<25} | Rank: {card['edhrec_rank']:>5} | Sections: {card['section_count']}")
        
        # Save analysis results
        analysis_file = '/tmp/guide_completeness_analysis.json'
        import json
        with open(analysis_file, 'w') as f:
            json.dump({
                'total_cards': total_cards,
                'section_distribution': section_distribution,
                'incomplete_count': incomplete_count,
                'complete_count': complete_count,
                'incomplete_cards': incomplete_cards[:100],  # First 100 for review
                'analysis_date': datetime.utcnow().isoformat()
            }, f, indent=2, default=str)
        
        print(f"\n💾 Analysis saved to: {analysis_file}")
        
    except Exception as e:
        print(f"Error during analysis: {e}")
    finally:
        client.close()

def mark_incomplete_guides():
    """Mark cards with <6 sections as full_guide: false"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🏷️  Marking incomplete guides...")
        
        cursor = cards_collection.find({})
        marked_incomplete = 0
        marked_complete = 0
        processed = 0
        
        for card in cursor:
            processed += 1
            if processed % 1000 == 0:
                print(f"  Processed {processed:,} cards...")
            
            section_count = count_guide_sections(card)
            
            # Update full_guide flag based on section count
            if section_count < 6:
                cards_collection.update_one(
                    {'_id': card['_id']},
                    {
                        '$set': {
                            'full_guide': False,
                            'section_count': section_count,
                            'guide_status': 'incomplete',
                            'updated_at': datetime.utcnow()
                        }
                    }
                )
                marked_incomplete += 1
            else:
                cards_collection.update_one(
                    {'_id': card['_id']},
                    {
                        '$set': {
                            'full_guide': True,
                            'section_count': section_count,
                            'guide_status': 'complete',
                            'updated_at': datetime.utcnow()
                        }
                    }
                )
                marked_complete += 1
        
        print(f"✅ Marked {marked_incomplete:,} cards as incomplete (full_guide: false)")
        print(f"✅ Marked {marked_complete:,} cards as complete (full_guide: true)")
        
        # Create indexes for efficient querying
        try:
            cards_collection.create_index('full_guide')
            cards_collection.create_index('guide_status')
            cards_collection.create_index([('full_guide', 1), ('is_commander', 1)])
            print("📊 Created indexes for guide status queries")
        except Exception as e:
            print(f"ℹ️  Index creation: {e}")
        
    except Exception as e:
        print(f"Error marking guides: {e}")
    finally:
        client.close()

def move_incomplete_to_pending():
    """Move incomplete guides to pending_guide collection"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        pending_collection = db[PENDING_COLLECTION]
        
        print("📦 Moving incomplete guides to pending collection...")
        
        # Find all incomplete cards
        incomplete_cards = list(cards_collection.find({'full_guide': False}))
        print(f"Found {len(incomplete_cards):,} incomplete cards to move")
        
        if not incomplete_cards:
            print("No incomplete cards found to move")
            return
        
        # Move cards in batches
        batch_size = 1000
        moved_count = 0
        
        for i in range(0, len(incomplete_cards), batch_size):
            batch = incomplete_cards[i:i + batch_size]
            
            # Prepare documents for pending collection
            pending_docs = []
            for card in batch:
                # Add metadata about the move
                card['moved_from'] = 'cards'
                card['moved_at'] = datetime.utcnow()
                card['move_reason'] = 'incomplete_guide'
                card['original_id'] = card['_id']
                
                # Remove the _id to let MongoDB generate a new one
                card.pop('_id', None)
                pending_docs.append(card)
            
            # Insert into pending collection
            try:
                pending_collection.insert_many(pending_docs, ordered=False)
                
                # Remove from cards collection
                original_ids = [doc['original_id'] for doc in pending_docs]
                cards_collection.delete_many({'_id': {'$in': original_ids}})
                
                moved_count += len(batch)
                print(f"  Moved {moved_count:,} cards...")
                
            except Exception as e:
                print(f"Error moving batch {i//batch_size + 1}: {e}")
                continue
        
        print(f"✅ Successfully moved {moved_count:,} incomplete cards to pending collection")
        
        # Create indexes on pending collection
        try:
            pending_collection.create_index('uuid')
            pending_collection.create_index('name')
            pending_collection.create_index('is_commander')
            pending_collection.create_index('edhrec_rank')
            pending_collection.create_index('moved_at')
            print("📊 Created indexes on pending collection")
        except Exception as e:
            print(f"ℹ️  Pending collection indexes: {e}")
        
        # Show final stats
        remaining_cards = cards_collection.count_documents({})
        pending_cards = pending_collection.count_documents({})
        
        print(f"\n📊 Final Collection Stats:")
        print(f"  Cards collection: {remaining_cards:,}")
        print(f"  Pending collection: {pending_cards:,}")
        
    except Exception as e:
        print(f"Error moving cards: {e}")
    finally:
        client.close()

def full_cleanup():
    """Run the complete cleanup process"""
    print("🚀 Starting full cleanup process...")
    print("=" * 50)
    
    print("\n1️⃣  Analyzing current state...")
    analyze_guide_completeness()
    
    print("\n2️⃣  Marking incomplete guides...")
    mark_incomplete_guides()
    
    print("\n3️⃣  Moving incomplete cards to pending...")
    move_incomplete_to_pending()
    
    print("\n✅ Full cleanup process completed!")
    print("=" * 50)

def main():
    parser = argparse.ArgumentParser(
        description='Move incomplete guide cards to pending collection',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--analyze', action='store_true',
                       help='Analyze guide completeness without making changes')
    parser.add_argument('--mark-incomplete', action='store_true',
                       help='Mark cards with <6 sections as full_guide: false')
    parser.add_argument('--move-to-pending', action='store_true',
                       help='Move incomplete cards to pending_guide collection')
    parser.add_argument('--full-cleanup', action='store_true',
                       help='Run complete cleanup process (analyze + mark + move)')
    
    args = parser.parse_args()
    
    if not any([args.analyze, args.mark_incomplete, args.move_to_pending, args.full_cleanup]):
        parser.print_help()
        return 1
    
    if args.analyze:
        analyze_guide_completeness()
    
    if args.mark_incomplete:
        mark_incomplete_guides()
    
    if args.move_to_pending:
        move_incomplete_to_pending()
    
    if args.full_cleanup:
        full_cleanup()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
