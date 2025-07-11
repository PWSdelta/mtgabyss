#!/usr/bin/env python3
"""
Simple script to move cards without guides to the pending collection.
No flags, no complexity - just move cards that don't have guide sections.
"""

import os
from pymongo import MongoClient
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def main():
    """Move all cards without guides from main collection to pending collection."""
    
    # Connect to MongoDB
    client = MongoClient(MONGODB_URI)
    db = client.mtgabyss
    
    # Collections
    cards_collection = db.cards
    pending_collection = db.cards_pending
    
    logger.info("Starting simple card migration...")
    
    # Find cards in main collection that don't have guides
    # A card has guides if it has guide_sections with content
    query = {
        "$or": [
            {"guide_sections": {"$exists": False}},
            {"guide_sections": None},
            {"guide_sections": {}},
            {"section_count": {"$lt": 1}},
            {"section_count": {"$exists": False}}
        ]
    }
    
    # Count cards to move
    cards_to_move = list(cards_collection.find(query))
    total_count = len(cards_to_move)
    
    if total_count == 0:
        logger.info("No cards without guides found. Nothing to move.")
        return
    
    logger.info(f"Found {total_count} cards without guides to move to pending collection")
    
    # Move cards to pending collection
    moved_count = 0
    for card in cards_to_move:
        try:
            # Insert into pending collection
            pending_collection.insert_one(card)
            
            # Remove from main collection
            cards_collection.delete_one({"_id": card["_id"]})
            
            moved_count += 1
            if moved_count % 100 == 0:
                logger.info(f"Moved {moved_count}/{total_count} cards...")
                
        except Exception as e:
            logger.error(f"Error moving card {card.get('name', 'unknown')}: {e}")
            continue
    
    logger.info(f"Migration complete: {moved_count} cards moved to pending collection")
    
    # Show final counts
    main_count = cards_collection.count_documents({})
    pending_count = pending_collection.count_documents({})
    
    logger.info(f"Final counts:")
    logger.info(f"  Main collection: {main_count} cards")
    logger.info(f"  Pending collection: {pending_count} cards")

if __name__ == "__main__":
    main()
