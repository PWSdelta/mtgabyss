#!/usr/bin/env python3
"""
Fresh Oracle Cards Import for MTGAbyss
=====================================

Import all cards from json/oracle-cards.json into the MongoDB cards collection.
- Clears existing cards collection
- Imports all ~35,000 cards from Scryfall Oracle data
- Deletes non-EDHREC cards immediately after import
- Marks all remaining cards as status: "unreviewed" so they don't show on the site
- Creates proper indexes for performance

Usage:
  python import_oracle_cards.py --import
  python import_oracle_cards.py --dry-run  # Preview what would happen
  python import_oracle_cards.py --import --keep-non-edhrec  # Keep all cards (skip deletion)
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'
ORACLE_FILE = '/home/owner/mtgabyss/json/oracle-cards.json'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-8s | %(message)s'
)
logger = logging.getLogger(__name__)

def check_oracle_file():
    """Check if the oracle cards file exists and get basic info"""
    if not os.path.exists(ORACLE_FILE):
        logger.error(f"Oracle cards file not found: {ORACLE_FILE}")
        return False
    
    file_size = os.path.getsize(ORACLE_FILE)
    logger.info(f"📁 Found oracle cards file: {ORACLE_FILE}")
    logger.info(f"📊 File size: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")
    
    return True

def load_oracle_cards():
    """Load cards from the oracle JSON file"""
    logger.info("📖 Loading cards from oracle file...")
    
    try:
        with open(ORACLE_FILE, 'r', encoding='utf-8') as f:
            cards_data = json.load(f)
        
        logger.info(f"✅ Loaded {len(cards_data):,} cards from oracle file")
        return cards_data
    
    except Exception as e:
        logger.error(f"❌ Error loading oracle file: {e}")
        return None

def analyze_cards(cards_data):
    """Analyze the cards to understand what we're working with"""
    logger.info("🔍 Analyzing card data...")
    
    total_cards = len(cards_data)
    edhrec_cards = 0
    commander_cards = 0
    type_counts = {}
    rarity_counts = {}
    
    for card in cards_data:
        # Count EDHREC cards
        if card.get('edhrec_rank') is not None:
            edhrec_cards += 1
        
        # Count commanders (creatures or planeswalkers that can be commanders)
        type_line = card.get('type_line', '').lower()
        if ('legendary' in type_line and 
            ('creature' in type_line or 'planeswalker' in type_line)):
            commander_cards += 1
        
        # Count by type
        main_type = type_line.split(' — ')[0] if ' — ' in type_line else type_line
        type_counts[main_type] = type_counts.get(main_type, 0) + 1
        
        # Count by rarity
        rarity = card.get('rarity', 'unknown')
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
    
    logger.info(f"📈 Analysis results:")
    logger.info(f"   Total cards: {total_cards:,}")
    logger.info(f"   EDHREC cards: {edhrec_cards:,} ({edhrec_cards/total_cards*100:.1f}%)")
    logger.info(f"   Non-EDHREC cards: {total_cards-edhrec_cards:,} ({(total_cards-edhrec_cards)/total_cards*100:.1f}%)")
    logger.info(f"   Potential commanders: {commander_cards:,}")
    
    logger.info(f"📊 Top card types:")
    for card_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        logger.info(f"   {card_type}: {count:,}")
    
    logger.info(f"🎭 Rarity distribution:")
    for rarity, count in sorted(rarity_counts.items(), key=lambda x: x[1], reverse=True):
        logger.info(f"   {rarity}: {count:,}")
    
    return {
        'total_cards': total_cards,
        'edhrec_cards': edhrec_cards,
        'non_edhrec_cards': total_cards - edhrec_cards,
        'commander_cards': commander_cards,
        'type_counts': type_counts,
        'rarity_counts': rarity_counts
    }

def clear_existing_cards(db):
    """Clear the existing cards collection"""
    logger.info("🗑️ Clearing existing cards collection...")
    
    cards_collection = db['cards']
    existing_count = cards_collection.count_documents({})
    
    if existing_count > 0:
        logger.info(f"   Found {existing_count:,} existing cards")
        result = cards_collection.delete_many({})
        logger.info(f"   Deleted {result.deleted_count:,} existing cards")
    else:
        logger.info("   No existing cards to delete")

def import_cards_to_mongodb(db, cards_data):
    """Import cards to MongoDB with proper processing"""
    logger.info("💾 Importing cards to MongoDB...")
    
    cards_collection = db['cards']
    batch_size = 1000
    imported_count = 0
    total_cards = len(cards_data)
    
    # Process cards in batches
    for i in range(0, total_cards, batch_size):
        batch = cards_data[i:i + batch_size]
        processed_batch = []
        
        for card in batch:
            # Use Scryfall's 'id' as 'uuid' for consistency
            if 'id' in card:
                card['uuid'] = card['id']
            
            # Add import metadata
            card['imported_at'] = datetime.now(timezone.utc).isoformat()
            card['status'] = 'unreviewed'  # Mark all as unreviewed initially
            
            processed_batch.append(card)
        
        try:
            cards_collection.insert_many(processed_batch, ordered=False)
            imported_count += len(processed_batch)
            
            if imported_count % 5000 == 0 or imported_count == total_cards:
                logger.info(f"   📋 Imported {imported_count:,}/{total_cards:,} cards...")
        
        except BulkWriteError as e:
            # Handle any duplicate key errors gracefully
            inserted_count = len(processed_batch) - len(e.details.get('writeErrors', []))
            imported_count += inserted_count
            logger.warning(f"   ⚠️ Batch had {len(e.details.get('writeErrors', []))} errors, {inserted_count} succeeded")
    
    logger.info(f"✅ Successfully imported {imported_count:,} cards")
    return imported_count

def delete_non_edhrec_cards(db):
    """Delete all cards that don't have EDHREC rank"""
    logger.info("🔥 Deleting non-EDHREC cards...")
    
    cards_collection = db['cards']
    
    # Count non-EDHREC cards first
    non_edhrec_query = {
        '$or': [
            {'edhrec_rank': {'$exists': False}},
            {'edhrec_rank': None},
            {'edhrec_rank': {'$type': 'null'}}
        ]
    }
    
    non_edhrec_count = cards_collection.count_documents(non_edhrec_query)
    logger.info(f"   Found {non_edhrec_count:,} non-EDHREC cards to delete")
    
    if non_edhrec_count > 0:
        result = cards_collection.delete_many(non_edhrec_query)
        logger.info(f"   🗑️ Deleted {result.deleted_count:,} non-EDHREC cards")
        
        # Verify remaining count
        remaining_count = cards_collection.count_documents({})
        edhrec_count = cards_collection.count_documents({'edhrec_rank': {'$exists': True, '$ne': None}})
        logger.info(f"   📊 Remaining cards: {remaining_count:,} (EDHREC: {edhrec_count:,})")
    else:
        logger.info("   No non-EDHREC cards found")

def create_indexes(db):
    """Create necessary indexes for performance"""
    logger.info("🏗️ Creating database indexes...")
    
    cards_collection = db['cards']
    
    indexes_to_create = [
        ('uuid', 'unique identifier'),
        ('name', 'card name searches'),
        ('edhrec_rank', 'EDHREC ranking'),
        ('status', 'review status'),
        ('type_line', 'card type filtering'),
        ('rarity', 'rarity filtering'),
        ('colors', 'color identity'),
        ('cmc', 'mana cost'),
        ('set', 'set filtering'),
    ]
    
    for field, description in indexes_to_create:
        try:
            cards_collection.create_index(field, sparse=True)
            logger.info(f"   ✅ Created index on '{field}' ({description})")
        except Exception as e:
            logger.warning(f"   ⚠️ Index creation warning for '{field}': {e}")

def show_final_stats(db):
    """Show final statistics after import"""
    logger.info("📊 Final import statistics:")
    
    cards_collection = db['cards']
    
    total_cards = cards_collection.count_documents({})
    edhrec_cards = cards_collection.count_documents({'edhrec_rank': {'$exists': True, '$ne': None}})
    unreviewed_cards = cards_collection.count_documents({'status': 'unreviewed'})
    
    # Get some sample commanders
    commanders = list(cards_collection.find({
        'edhrec_rank': {'$exists': True, '$ne': None},
        'type_line': {'$regex': 'legendary.*creature', '$options': 'i'}
    }).sort('edhrec_rank', 1).limit(5))
    
    logger.info(f"   Total cards in database: {total_cards:,}")
    logger.info(f"   Cards with EDHREC rank: {edhrec_cards:,}")
    logger.info(f"   Cards marked 'unreviewed': {unreviewed_cards:,}")
    
    if commanders:
        logger.info(f"   Top 5 commanders by EDHREC rank:")
        for i, cmd in enumerate(commanders, 1):
            logger.info(f"      {i}. {cmd.get('name')} (rank: {cmd.get('edhrec_rank')})")

def main():
    parser = argparse.ArgumentParser(
        description='Import Oracle cards and prepare for new workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python import_oracle_cards.py --import
  python import_oracle_cards.py --dry-run
  python import_oracle_cards.py --import --keep-non-edhrec
        """
    )
    
    parser.add_argument('--import', action='store_true', dest='do_import',
                        help='Actually perform the import (required)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would happen without making changes')
    parser.add_argument('--keep-non-edhrec', action='store_true',
                        help='Keep non-EDHREC cards (skip deletion step)')
    
    args = parser.parse_args()
    
    if not args.do_import and not args.dry_run:
        logger.error("❌ Must specify either --import or --dry-run")
        return 1
    
    logger.info("🚀 MTGAbyss Oracle Cards Import Starting")
    logger.info("=" * 50)
    
    # Check oracle file
    if not check_oracle_file():
        return 1
    
    # Load cards
    cards_data = load_oracle_cards()
    if not cards_data:
        return 1
    
    # Analyze cards
    analysis = analyze_cards(cards_data)
    
    if args.dry_run:
        logger.info("\n🔍 DRY RUN MODE - No changes will be made")
        logger.info(f"Would import {analysis['total_cards']:,} cards")
        if not args.keep_non_edhrec:
            logger.info(f"Would delete {analysis['non_edhrec_cards']:,} non-EDHREC cards")
            logger.info(f"Would keep {analysis['edhrec_cards']:,} EDHREC cards")
        logger.info("All cards would be marked as 'unreviewed'")
        return 0
    
    # Connect to MongoDB
    logger.info(f"🔌 Connecting to MongoDB: {MONGODB_URI}")
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        # Test connection
        db.command('ping')
        logger.info("✅ MongoDB connection successful")
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return 1
    
    try:
        # Clear existing cards
        clear_existing_cards(db)
        
        # Import new cards
        imported_count = import_cards_to_mongodb(db, cards_data)
        
        # Delete non-EDHREC cards (unless keeping them)
        if not args.keep_non_edhrec:
            delete_non_edhrec_cards(db)
        
        # Create indexes
        create_indexes(db)
        
        # Show final stats
        show_final_stats(db)
        
        logger.info("\n🎉 Import completed successfully!")
        logger.info("📝 Next steps:")
        logger.info("   - All cards are marked as 'unreviewed' and won't show on the site")
        logger.info("   - Use your review workflow to spot-check and approve cards")
        logger.info("   - Only cards with status='reviewed' should be shown publicly")
        
    except Exception as e:
        logger.error(f"❌ Import failed: {e}")
        return 1
    
    finally:
        client.close()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
