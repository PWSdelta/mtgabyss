#!/usr/bin/env python3
"""
Work Queue Status Check
=====================

Check the current status of the MTGAbyss work queue with commander prioritization.
Shows what cards are next in line for processing.
"""

import os
import sys
import logging
from pymongo import MongoClient
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'

def connect_to_mongodb():
    """Connect to MongoDB and return the cards collection"""
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DATABASE_NAME]
        collection = db[CARDS_COLLECTION]
        logger.info(f"Connected to MongoDB: {MONGODB_URI}")
        return collection, client
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

def check_work_queue_status(cards_collection):
    """Check the current status of the work queue"""
    
    # Overall stats
    total_cards = cards_collection.count_documents({})
    unguided_cards = cards_collection.count_documents({"unguided": True})
    guided_cards = cards_collection.count_documents({"unguided": False})
    commanders_total = cards_collection.count_documents({"is_commander": True})
    commanders_unguided = cards_collection.count_documents({"is_commander": True, "unguided": True})
    commanders_guided = commanders_total - commanders_unguided
    
    logger.info("📊 Work Queue Status")
    logger.info("=" * 50)
    logger.info(f"Total cards in database: {total_cards:,}")
    logger.info(f"Cards needing guides (unguided): {unguided_cards:,}")
    logger.info(f"Cards with guides (guided): {guided_cards:,}")
    logger.info(f"Completion rate: {guided_cards/total_cards*100:.1f}%")
    logger.info("")
    logger.info(f"👑 Commander Status:")
    logger.info(f"  Total commanders: {commanders_total:,}")
    logger.info(f"  Unguided commanders: {commanders_unguided:,}")
    logger.info(f"  Guided commanders: {commanders_guided:,}")
    logger.info(f"  Commander completion: {commanders_guided/commanders_total*100:.1f}%")
    
    # Show next cards in queue (priority order)
    logger.info("\n🎯 Next 20 Cards in Work Queue (Priority Order):")
    logger.info("-" * 70)
    
    next_cards = list(cards_collection.find(
        {"unguided": True},
        {
            "name": 1, 
            "is_commander": 1, 
            "edhrec_rank": 1, 
            "type_line": 1, 
            "priority_level": 1,
            "rarity": 1
        }
    ).sort([
        ("is_commander", -1),  # Commanders first
        ("edhrec_rank", 1)     # Lower rank = more popular
    ]).limit(20))
    
    for i, card in enumerate(next_cards, 1):
        commander_flag = "👑" if card.get('is_commander', False) else "🃏"
        edhrec_rank = card.get('edhrec_rank', 'N/A')
        priority = card.get('priority_level', 'normal')
        rarity = card.get('rarity', 'N/A')
        type_line = card.get('type_line', 'N/A')
        
        # Truncate type line if too long
        if len(type_line) > 30:
            type_line = type_line[:27] + "..."
            
        logger.info(f"{i:2d}. {commander_flag} {card.get('name', 'N/A'):<25} | Rank: {str(edhrec_rank):<8} | {rarity:<8} | {type_line}")
    
    # Show EDHREC rank distribution for unguided cards
    logger.info("\n📈 EDHREC Rank Distribution (Unguided Cards):")
    logger.info("-" * 50)
    
    # Count cards in different EDHREC rank ranges
    rank_ranges = [
        (1, 100, "Top 100"),
        (101, 500, "Top 500"),
        (501, 1000, "Top 1K"),
        (1001, 5000, "Top 5K"),
        (5001, 10000, "Top 10K"),
        (10001, float('inf'), "10K+")
    ]
    
    for min_rank, max_rank, label in rank_ranges:
        if max_rank == float('inf'):
            count = cards_collection.count_documents({
                "unguided": True,
                "edhrec_rank": {"$gte": min_rank}
            })
        else:
            count = cards_collection.count_documents({
                "unguided": True,
                "edhrec_rank": {"$gte": min_rank, "$lte": max_rank}
            })
        logger.info(f"  {label:<10}: {count:,} cards")
    
    # Count cards without EDHREC rank
    no_rank_count = cards_collection.count_documents({
        "unguided": True,
        "$or": [
            {"edhrec_rank": {"$exists": False}},
            {"edhrec_rank": None}
        ]
    })
    logger.info(f"  {'No rank':<10}: {no_rank_count:,} cards")
    
    # Show top completed commanders
    logger.info("\n🏆 Top 10 Completed Commanders (by EDHREC rank):")
    logger.info("-" * 50)
    
    completed_commanders = list(cards_collection.find(
        {
            "is_commander": True,
            "unguided": False,
            "edhrec_rank": {"$exists": True, "$ne": None}
        },
        {"name": 1, "edhrec_rank": 1, "type_line": 1}
    ).sort("edhrec_rank", 1).limit(10))
    
    for i, card in enumerate(completed_commanders, 1):
        logger.info(f"{i:2d}. 👑 {card.get('name', 'N/A')} (EDHREC rank: {card.get('edhrec_rank', 'N/A')})")
    
    if not completed_commanders:
        logger.info("  No commanders have been completed yet.")

def main():
    logger.info("Checking MTGAbyss Work Queue Status...")
    
    # Connect to MongoDB
    cards_collection, client = connect_to_mongodb()
    
    try:
        check_work_queue_status(cards_collection)
        
        logger.info("\n" + "=" * 50)
        logger.info("✅ Work queue status check complete!")
        logger.info("Run 'python worker_cards.py --half-guides' to start processing cards.")
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        sys.exit(1)
    finally:
        client.close()

if __name__ == "__main__":
    main()
