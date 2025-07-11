#!/usr/bin/env python3
"""
Move cards without any guide sections to a 'pending_guide' collection.
This helps separate completed guides from cards that still need work.
"""

import os
import logging
from pymongo import MongoClient
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def move_cards_to_pending(keep_count=0):
    """Move cards without any sections to pending_guide collection"""
    
    client = MongoClient(MONGODB_URI)
    db = client.mtgabyss
    cards = db.cards
    pending_guide = db.pending_guide
    
    # Find cards that have no sections at all and have valid scryfall_id
    query = {
        '$and': [
            {
                '$or': [
                    {'analysis.sections': {'$exists': False}},
                    {'analysis.sections': {}},
                    {'analysis.sections': None},
                    {'analysis': {'$exists': False}},
                    {'analysis': None}
                ]
            },
            {
                'scryfall_id': {'$exists': True, '$ne': None, '$ne': ''}  # Must have valid scryfall_id
            }
        ]
    }
    
    # Count total cards without sections
    total_pending = cards.count_documents(query)
    logger.info(f"Found {total_pending:,} cards without any guide sections")
    
    if total_pending == 0:
        logger.info("No cards need to be moved to pending_guide collection")
        return
    
    # Calculate how many to move (leave some for testing if requested)
    cards_to_move = total_pending - keep_count
    if cards_to_move <= 0:
        logger.info(f"Keeping all {total_pending:,} cards in main collection for testing")
        return
    
    # Ask for confirmation
    if keep_count > 0:
        response = input(f"Move {cards_to_move:,} cards to 'pending_guide' (keeping {keep_count:,} for testing)? (y/N): ")
    else:
        response = input(f"Move {cards_to_move:,} cards to 'pending_guide' collection? (y/N): ")
    
    if response.lower() != 'y':
        logger.info("Operation cancelled")
        return
    
    # Create index on pending_guide collection for fast lookups
    try:
        pending_guide.create_index('scryfall_id', unique=True)
        pending_guide.create_index('name')
        pending_guide.create_index('edhrec_rank')
        logger.info("Created indexes on pending_guide collection")
    except Exception as e:
        logger.warning(f"Could not create indexes: {e}")
    
    # Batch process the move
    batch_size = 1000
    moved_count = 0
    
    # Process in batches to avoid memory issues
    # Sort by EDHREC rank (keep most popular cards for testing)
    cursor = cards.find(query).sort([('edhrec_rank', 1), ('_id', 1)]).skip(keep_count).batch_size(batch_size)
    
    for card in cursor:
        if moved_count >= cards_to_move:
            break
        try:
            # Add metadata about when it was moved
            card['moved_to_pending_at'] = datetime.now()
            card['original_collection'] = 'cards'
            
            # Insert into pending_guide collection
            pending_guide.insert_one(card)
            
            # Remove from cards collection
            cards.delete_one({'_id': card['_id']})
            
            moved_count += 1
            
            if moved_count % 100 == 0:
                logger.info(f"Moved {moved_count:,} / {cards_to_move:,} cards...")
                
        except Exception as e:
            logger.error(f"Error moving card {card.get('name', 'Unknown')}: {e}")
            continue
    
    logger.info(f"✅ Successfully moved {moved_count:,} cards to 'pending_guide' collection")
    
    # Verify the move
    remaining_in_cards = cards.count_documents(query)
    total_in_pending = pending_guide.count_documents({})
    
    logger.info(f"📊 Post-move statistics:")
    logger.info(f"   - Cards remaining without sections: {remaining_in_cards:,}")
    logger.info(f"   - Total cards in pending_guide: {total_in_pending:,}")
    logger.info(f"   - Total cards moved: {moved_count:,}")

def restore_cards_from_pending():
    """Restore cards from pending_guide back to cards collection (if needed)"""
    
    client = MongoClient(MONGODB_URI)
    db = client.mtgabyss
    cards = db.cards
    pending_guide = db.pending_guide
    
    total_pending = pending_guide.count_documents({})
    logger.info(f"Found {total_pending:,} cards in pending_guide collection")
    
    if total_pending == 0:
        logger.info("No cards in pending_guide to restore")
        return
    
    response = input(f"Restore {total_pending:,} cards from 'pending_guide' back to 'cards'? (y/N): ")
    if response.lower() != 'y':
        logger.info("Operation cancelled")
        return
    
    restored_count = 0
    
    for card in pending_guide.find():
        try:
            # Remove the pending metadata
            if 'moved_to_pending_at' in card:
                del card['moved_to_pending_at']
            if 'original_collection' in card:
                del card['original_collection']
            
            # Insert back into cards collection
            cards.insert_one(card)
            
            # Remove from pending_guide
            pending_guide.delete_one({'_id': card['_id']})
            
            restored_count += 1
            
            if restored_count % 100 == 0:
                logger.info(f"Restored {restored_count:,} / {total_pending:,} cards...")
                
        except Exception as e:
            logger.error(f"Error restoring card {card.get('name', 'Unknown')}: {e}")
            continue
    
    logger.info(f"✅ Successfully restored {restored_count:,} cards to 'cards' collection")

def show_statistics():
    """Show current distribution of cards across collections"""
    
    client = MongoClient(MONGODB_URI)
    db = client.mtgabyss
    cards = db.cards
    pending_guide = db.pending_guide if 'pending_guide' in db.list_collection_names() else None
    
    # Cards with sections
    cards_with_sections = cards.count_documents({
        'analysis.sections': {'$exists': True, '$ne': {}, '$ne': None}
    })
    
    # Cards without sections (still in cards collection)
    cards_without_sections = cards.count_documents({
        '$or': [
            {'analysis.sections': {'$exists': False}},
            {'analysis.sections': {}},
            {'analysis.sections': None},
            {'analysis': {'$exists': False}},
            {'analysis': None}
        ]
    })
    
    # Total cards in main collection
    total_cards = cards.count_documents({})
    
    # Pending guide collection stats
    pending_count = pending_guide.count_documents({}) if pending_guide else 0
    
    print(f"\n📊 MTGAbyss Card Distribution:")
    print(f"   Main 'cards' collection:")
    print(f"     - Cards WITH sections: {cards_with_sections:,}")
    print(f"     - Cards WITHOUT sections: {cards_without_sections:,}")
    print(f"     - Total in main collection: {total_cards:,}")
    print(f"   ")
    print(f"   'pending_guide' collection: {pending_count:,}")
    print(f"   ")
    print(f"   Grand total: {total_cards + pending_count:,}")
    
    if cards_without_sections > 0:
        print(f"\n💡 You have {cards_without_sections:,} cards in the main collection that could be moved to pending_guide")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python move_cards_to_pending.py move [keep_count]  - Move cards without sections to pending_guide")
        print("  python move_cards_to_pending.py restore            - Restore cards from pending_guide to cards")
        print("  python move_cards_to_pending.py stats              - Show current statistics")
        print("")
        print("Examples:")
        print("  python move_cards_to_pending.py move 500           - Keep 500 most popular cards for testing")
        print("  python move_cards_to_pending.py move               - Move all cards without sections")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == 'move':
        # Check for optional keep_count argument
        keep_count = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        move_cards_to_pending(keep_count=keep_count)
    elif command == 'restore':
        restore_cards_from_pending()
    elif command == 'stats':
        show_statistics()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
