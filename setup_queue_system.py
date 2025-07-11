#!/usr/bin/env python3
"""
Setup Three-Collection Queue System
===================================

Organizes cards into a clean three-collection system:
- cards: Complete guides (6+ sections), live on site
- pending: Work queue ordered by EDHREC rank
- unguided: Storage for cards without guides

Usage:
  python setup_queue_system.py --setup
  python setup_queue_system.py --prime-queue --limit 100
  python setup_queue_system.py --move-complete-to-live
"""

import argparse
from pymongo import MongoClient
import os
from datetime import datetime

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def setup_collections():
    """Initialize the three-collection system"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    print("🔧 Setting up three-collection queue system...")
    
    # Check current state
    main_count = db['cards'].count_documents({})
    pending_count = db['cards_pending'].count_documents({})
    
    print(f"Current state: main={main_count}, pending={pending_count}")
    
    # If there are cards in pending, rename it to unguided
    if pending_count > 0:
        print(f"📦 Moving {pending_count} cards from 'cards_pending' to 'unguided'...")
        
        # Copy all cards from pending to unguided
        cards = list(db['cards_pending'].find({}))
        if cards:
            db['unguided'].insert_many(cards)
            db['cards_pending'].drop()
        
        print(f"✅ Moved {len(cards)} cards to 'unguided' collection")
    
    # Create indexes for performance
    print("🔍 Creating indexes...")
    
    try:
        # Index for pending collection (work queue)
        db['pending'].create_index([('edhrec_rank', 1), ('is_commander', -1)])
        db['pending'].create_index([('uuid', 1)], unique=True)
        
        # Index for cards collection (live site)
        db['cards'].create_index([('uuid', 1)], unique=True)
        
        # Index for unguided collection
        db['unguided'].create_index([('uuid', 1)], unique=True)
        db['unguided'].create_index([('edhrec_rank', 1)])
        
        print("✅ Indexes created")
    except Exception as e:
        print(f"⚠️  Some indexes already exist: {e}")
        print("✅ Continuing with existing indexes")
    
    # Final counts
    cards_count = db['cards'].count_documents({})
    pending_count = db['pending'].count_documents({})
    unguided_count = db['unguided'].count_documents({})
    
    print(f"\n📊 Final collection counts:")
    print(f"  cards (live):    {cards_count:,}")
    print(f"  pending (queue): {pending_count:,}")
    print(f"  unguided:        {unguided_count:,}")
    print(f"  total:           {cards_count + pending_count + unguided_count:,}")
    
    client.close()

def prime_queue(limit=None, commanders_only=False):
    """Add cards to the work queue from unguided, ordered by EDHREC rank"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    # Build query for best cards to prioritize
    query = {'edhrec_rank': {'$exists': True, '$ne': None}}
    if commanders_only:
        query['is_commander'] = True
    
    # Get cards sorted by EDHREC rank (ascending = most popular first)
    sort_criteria = [('edhrec_rank', 1)]
    
    if limit:
        cards_to_queue = list(db['unguided'].find(query).sort(sort_criteria).limit(limit))
        print(f"📋 Adding {len(cards_to_queue)} cards to work queue...")
    else:
        count = db['unguided'].count_documents(query)
        print(f"📋 Adding all {count} EDHREC-ranked cards to work queue...")
        cards_to_queue = list(db['unguided'].find(query).sort(sort_criteria))
    
    if not cards_to_queue:
        print("No cards found to add to queue.")
        return
    
    # Add timestamp for queue tracking
    for card in cards_to_queue:
        card['queued_at'] = datetime.utcnow()
    
    # Insert into pending (work queue)
    try:
        db['pending'].insert_many(cards_to_queue, ordered=False)
        
        # Remove from unguided
        card_ids = [card['_id'] for card in cards_to_queue]
        db['unguided'].delete_many({'_id': {'$in': card_ids}})
        
        print(f"✅ Successfully queued {len(cards_to_queue)} cards")
        
        # Show top cards in queue
        top_cards = list(db['pending'].find({}).sort([('edhrec_rank', 1)]).limit(5))
        print(f"\n🔝 Top 5 cards in queue:")
        for i, card in enumerate(top_cards, 1):
            commander_flag = "👑" if card.get('is_commander') else "🃏"
            rank = card.get('edhrec_rank', 'N/A')
            print(f"  {i}. {commander_flag} {card.get('name')} (rank: {rank})")
        
    except Exception as e:
        print(f"❌ Error adding cards to queue: {e}")
    
    # Show current counts
    cards_count = db['cards'].count_documents({})
    pending_count = db['pending'].count_documents({})
    unguided_count = db['unguided'].count_documents({})
    
    print(f"\n📊 Updated collection counts:")
    print(f"  cards (live):    {cards_count:,}")
    print(f"  pending (queue): {pending_count:,}")
    print(f"  unguided:        {unguided_count:,}")
    
    client.close()

def move_complete_to_live():
    """Move cards with 6+ guide sections from pending to live cards collection"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    print("🔍 Looking for cards with complete guides in pending collection...")
    
    # Find cards in pending with 6+ guide sections
    pipeline = [
        {
            '$addFields': {
                'guide_sections_count': {
                    '$size': {'$ifNull': ['$guide_sections', []]}
                }
            }
        },
        {
            '$match': {
                'guide_sections_count': {'$gte': 6}
            }
        }
    ]
    
    complete_cards = list(db['pending'].aggregate(pipeline))
    
    if not complete_cards:
        print("No cards with complete guides found in pending collection.")
        return
    
    print(f"📦 Found {len(complete_cards)} cards with complete guides")
    
    # Move to live cards collection
    for card in complete_cards:
        card['published_at'] = datetime.utcnow()
        # Remove the guide_sections_count field we added
        card.pop('guide_sections_count', None)
    
    try:
        db['cards'].insert_many(complete_cards, ordered=False)
        
        # Remove from pending
        card_ids = [card['_id'] for card in complete_cards]
        db['pending'].delete_many({'_id': {'$in': card_ids}})
        
        print(f"✅ Successfully moved {len(complete_cards)} complete cards to live collection")
        
        # Show some examples
        for card in complete_cards[:3]:
            sections_count = len(card.get('guide_sections', []))
            print(f"  📄 {card.get('name')} ({sections_count} sections)")
        
        if len(complete_cards) > 3:
            print(f"  ... and {len(complete_cards) - 3} more")
        
    except Exception as e:
        print(f"❌ Error moving cards: {e}")
    
    # Final counts
    cards_count = db['cards'].count_documents({})
    pending_count = db['pending'].count_documents({})
    unguided_count = db['unguided'].count_documents({})
    
    print(f"\n📊 Updated collection counts:")
    print(f"  cards (live):    {cards_count:,}")
    print(f"  pending (queue): {pending_count:,}")
    print(f"  unguided:        {unguided_count:,}")
    
    client.close()

def main():
    parser = argparse.ArgumentParser(description='Setup and manage three-collection queue system')
    parser.add_argument('--setup', action='store_true', help='Initialize the three-collection system')
    parser.add_argument('--prime-queue', action='store_true', help='Add cards to work queue from unguided')
    parser.add_argument('--move-complete-to-live', action='store_true', help='Move cards with complete guides to live collection')
    parser.add_argument('--limit', type=int, help='Limit number of cards when priming queue')
    parser.add_argument('--commanders-only', action='store_true', help='Only queue commanders when priming')
    
    args = parser.parse_args()
    
    if args.setup:
        setup_collections()
    elif args.prime_queue:
        prime_queue(limit=args.limit, commanders_only=args.commanders_only)
    elif args.move_complete_to_live:
        move_complete_to_live()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
