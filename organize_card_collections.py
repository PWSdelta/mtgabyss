#!/usr/bin/env python3
"""
Organize Cards into Three-Collection System
==========================================

Organizes cards into the proper three-collection system:
- cards: Live site cards (6+ guide sections, fully validated)
- pending: Work queue (ordered by EDHREC rank, workers pull from here) 
- unguided: Storage for cards without guides

Usage:
  python organize_card_collections.py --analyze
  python organize_card_collections.py --organize
  python organize_card_collections.py --prime-queue --limit 100
"""

import argparse
from pymongo import MongoClient
import os

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def count_guide_sections(card):
    """Count the number of guide sections a card has"""
    guide_count = 0
    
    # Check guide_sections field
    if 'guide_sections' in card and isinstance(card['guide_sections'], list):
        guide_count += len(card['guide_sections'])
    
    # Check guides field (various formats)
    if 'guides' in card:
        guides = card['guides']
        if isinstance(guides, dict):
            guide_count += len(guides)
        elif isinstance(guides, list):
            guide_count += len(guides)
    
    return guide_count

def analyze_collections():
    """Analyze current state of collections"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    print("🔍 Analyzing current collection state...")
    
    # Check main cards collection
    main_cards = list(db['cards'].find({}, {'name': 1, 'edhrec_rank': 1, 'guide_sections': 1, 'guides': 1}))
    print(f"\n📊 Main 'cards' collection: {len(main_cards)} cards")
    
    if main_cards:
        guided_count = 0
        unguided_count = 0
        
        for card in main_cards:
            sections = count_guide_sections(card)
            if sections >= 6:
                guided_count += 1
            else:
                unguided_count += 1
        
        print(f"  ✅ Cards with 6+ sections: {guided_count}")
        print(f"  ❌ Cards with <6 sections: {unguided_count}")
    
    # Check pending collection
    pending_count = db['pending'].count_documents({})
    print(f"\n📦 Pending collection: {pending_count} cards")
    
    # Check unguided collection  
    unguided_count = db['unguided'].count_documents({})
    print(f"\n🔄 Unguided collection: {unguided_count} cards")
    
    # Check old collections
    cards_pending_count = db['cards_pending'].count_documents({})
    if cards_pending_count > 0:
        print(f"\n⚠️  Old 'cards_pending' collection: {cards_pending_count} cards (should migrate)")
    
    client.close()

def organize_collections():
    """Organize cards into proper three-collection system"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    print("🔄 Organizing cards into three-collection system...")
    
    # Get all cards from main collection
    main_cards = list(db['cards'].find({}))
    print(f"Processing {len(main_cards)} cards from main collection...")
    
    cards_to_keep = []      # 6+ sections, stay in main
    cards_to_pending = []   # <6 sections but have EDHREC rank
    cards_to_unguided = []  # <6 sections, no EDHREC rank
    
    for card in main_cards:
        sections = count_guide_sections(card)
        has_edhrec = card.get('edhrec_rank') and card.get('edhrec_rank') != 'N/A'
        
        if sections >= 6:
            cards_to_keep.append(card)
        elif has_edhrec:
            cards_to_pending.append(card)
        else:
            cards_to_unguided.append(card)
    
    print(f"  ✅ Keeping in main: {len(cards_to_keep)} cards (6+ sections)")
    print(f"  📦 Moving to pending: {len(cards_to_pending)} cards (<6 sections, has EDHREC)")
    print(f"  🔄 Moving to unguided: {len(cards_to_unguided)} cards (<6 sections, no EDHREC)")
    
    # Clear main collection and re-add only complete cards
    if cards_to_keep:
        db['cards'].delete_many({})
        db['cards'].insert_many(cards_to_keep)
        print(f"  ✅ Restored {len(cards_to_keep)} complete cards to main collection")
    
    # Move cards to pending (work queue)
    if cards_to_pending:
        # Sort by EDHREC rank (ascending = most popular first)
        cards_to_pending.sort(key=lambda x: x.get('edhrec_rank', 999999))
        db['pending'].insert_many(cards_to_pending)
        print(f"  📦 Added {len(cards_to_pending)} cards to pending queue")
    
    # Move cards to unguided storage
    if cards_to_unguided:
        db['unguided'].insert_many(cards_to_unguided)
        print(f"  🔄 Added {len(cards_to_unguided)} cards to unguided storage")
    
    # Migrate any cards from old collections
    old_pending = list(db['cards_pending'].find({}))
    if old_pending:
        print(f"  📤 Migrating {len(old_pending)} cards from old 'cards_pending' collection...")
        for card in old_pending:
            has_edhrec = card.get('edhrec_rank') and card.get('edhrec_rank') != 'N/A'
            if has_edhrec:
                db['pending'].insert_one(card)
            else:
                db['unguided'].insert_one(card)
        db['cards_pending'].drop()
        print("  ✅ Migration complete, dropped old collection")
    
    # Create indexes for efficiency
    print("  🔧 Creating indexes...")
    try:
        db['pending'].create_index([('edhrec_rank', 1)])
        db['pending'].create_index([('is_commander', -1), ('edhrec_rank', 1)])
        print("  ✅ Indexes created successfully")
    except Exception as e:
        print(f"  ⚠️  Index creation: {e}")
    
    print("\n🎉 Organization complete!")
    analyze_collections()
    
    client.close()

def prime_queue(limit=50):
    """Move some cards from unguided to pending to prime the work queue"""
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    
    print(f"🚀 Priming work queue with {limit} cards...")
    
    # Get cards with EDHREC ranks from unguided, prioritizing commanders
    query = {'edhrec_rank': {'$exists': True, '$ne': None}}
    
    # First try to get commanders
    commanders = list(db['unguided'].find({**query, 'is_commander': True}).sort('edhrec_rank', 1).limit(limit // 2))
    
    # Then get other cards
    remaining = limit - len(commanders)
    if remaining > 0:
        other_cards = list(db['unguided'].find({**query, 'is_commander': {'$ne': True}}).sort('edhrec_rank', 1).limit(remaining))
    else:
        other_cards = []
    
    cards_to_move = commanders + other_cards
    
    if cards_to_move:
        # Insert into pending
        db['pending'].insert_many(cards_to_move)
        
        # Remove from unguided
        card_ids = [card['_id'] for card in cards_to_move]
        db['unguided'].delete_many({'_id': {'$in': card_ids}})
        
        print(f"  ✅ Moved {len(cards_to_move)} cards to pending queue")
        print(f"    👑 Commanders: {len(commanders)}")
        print(f"    🃏 Other cards: {len(other_cards)}")
    else:
        print("  ❌ No eligible cards found to move")
    
    client.close()

def main():
    parser = argparse.ArgumentParser(description='Organize cards into three-collection system')
    parser.add_argument('--analyze', action='store_true', help='Analyze current collection state')
    parser.add_argument('--organize', action='store_true', help='Organize cards into proper collections')
    parser.add_argument('--prime-queue', action='store_true', help='Prime the work queue with cards from unguided')
    parser.add_argument('--limit', type=int, default=50, help='Number of cards to move when priming queue')
    
    args = parser.parse_args()
    
    if args.analyze:
        analyze_collections()
    elif args.organize:
        organize_collections()
    elif args.prime_queue:
        prime_queue(args.limit)
    else:
        print("Please specify --analyze, --organize, or --prime-queue")

if __name__ == "__main__":
    main()
