#!/usr/bin/env python3
"""
Setup EDHREC Queue for MTGAbyss
===============================

This script:
1. Downloads fresh oracle-cards.json from Scryfall bulk-data API
2. Imports all cards into 'cards_unsorted' collection
3. Deletes non-EDHREC ranked cards (~5k cards)
4. Renames the filtered collection to 'cards_edhrec'

The cards_edhrec collection becomes the processing queue for workers.
"""

import os
import sys
import json
import requests
import gzip
import tempfile
import logging
from datetime import datetime, timezone
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import argparse

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'
SCRYFALL_BULK_API = 'https://api.scryfall.com/bulk-data'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-8s | %(message)s'
)
logger = logging.getLogger(__name__)

def get_bulk_data_info():
    """Get information about available bulk data from Scryfall"""
    logger.info("🔍 Fetching Scryfall bulk data information...")
    
    try:
        response = requests.get(SCRYFALL_BULK_API, timeout=30)
        response.raise_for_status()
        
        bulk_data = response.json()
        
        # Find oracle-cards data
        oracle_data = None
        for item in bulk_data.get('data', []):
            if item.get('type') == 'oracle_cards':
                oracle_data = item
                break
        
        if not oracle_data:
            logger.error("❌ Oracle cards bulk data not found!")
            return None
        
        logger.info(f"✅ Found oracle cards data:")
        logger.info(f"   Size: {oracle_data.get('compressed_size', 0):,} bytes compressed")
        logger.info(f"   Updated: {oracle_data.get('updated_at')}")
        logger.info(f"   Download URL: {oracle_data.get('download_uri')}")
        
        return oracle_data
        
    except Exception as e:
        logger.error(f"❌ Error fetching bulk data info: {e}")
        return None

def download_oracle_data(download_url):
    """Download oracle cards data to temporary file"""
    logger.info("📥 Downloading fresh oracle cards data from Scryfall...")
    
    try:
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        temp_path = temp_file.name
        temp_file.close()
        
        # Download file
        response = requests.get(download_url, stream=True, timeout=60)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r   Progress: {percent:.1f}% ({downloaded:,}/{total_size:,} bytes)", end='', flush=True)
        
        print(f"\n✅ Download complete: {downloaded:,} bytes")
        
        # Check if the file is gzipped and decompress if needed
        with open(temp_path, 'rb') as f:
            magic = f.read(2)
        
        if magic == b'\x1f\x8b':  # gzip magic number
            logger.info("📤 File is gzipped, extracting...")
            decompressed_path = temp_path + '.decompressed'
            with gzip.open(temp_path, 'rb') as f_in:
                with open(decompressed_path, 'wb') as f_out:
                    f_out.write(f_in.read())
            os.remove(temp_path)
            temp_path = decompressed_path
        else:
            logger.info("📄 File is uncompressed JSON")
        
        logger.info(f"✅ Data ready at: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"❌ Error downloading data: {e}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.remove(temp_path)
        return None

def import_to_unsorted(json_file_path):
    """Import cards from JSON file to cards_unsorted collection"""
    logger.info("🗄️ Importing cards to MongoDB 'cards_unsorted' collection...")
    
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        
        # Drop existing cards_unsorted collection if it exists
        if 'cards_unsorted' in db.list_collection_names():
            logger.info("🗑️ Dropping existing 'cards_unsorted' collection...")
            db['cards_unsorted'].drop()
        
        cards_unsorted = db['cards_unsorted']
        
        # Load and import data
        logger.info(f"📖 Reading cards from {json_file_path}...")
        with open(json_file_path, 'r', encoding='utf-8') as f:
            cards_data = json.load(f)
        
        total_cards = len(cards_data)
        logger.info(f"📊 Found {total_cards:,} cards to import")
        
        # Import in batches
        batch_size = 1000
        imported_count = 0
        batch = []
        
        for card in cards_data:
            # Use Scryfall's 'id' as 'uuid' for consistency
            if 'id' in card:
                card['uuid'] = card['id']
            
            batch.append(card)
            
            if len(batch) >= batch_size:
                cards_unsorted.insert_many(batch)
                imported_count += len(batch)
                logger.info(f"   📋 Imported {imported_count:,}/{total_cards:,} cards...")
                batch = []
        
        # Import remaining cards
        if batch:
            cards_unsorted.insert_many(batch)
            imported_count += len(batch)
        
        logger.info(f"✅ Successfully imported {imported_count:,} cards to 'cards_unsorted'")
        
        # Create basic indexes
        logger.info("🏗️ Creating indexes...")
        cards_unsorted.create_index('uuid', sparse=True)
        cards_unsorted.create_index('name', sparse=True)
        cards_unsorted.create_index('edhrec_rank', sparse=True)
        
        client.close()
        return imported_count
        
    except Exception as e:
        logger.error(f"❌ Error importing cards: {e}")
        return 0

def filter_and_rename():
    """Delete non-EDHREC cards and rename collection to cards_edhrec"""
    logger.info("🔄 Filtering for EDHREC cards and renaming collection...")
    
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        
        cards_unsorted = db['cards_unsorted']
        
        # Count total cards before filtering
        total_before = cards_unsorted.count_documents({})
        logger.info(f"📊 Total cards before filtering: {total_before:,}")
        
        # Count EDHREC cards
        edhrec_count = cards_unsorted.count_documents({
            'edhrec_rank': {'$exists': True, '$ne': None, '$type': 'number'}
        })
        logger.info(f"📈 Cards with EDHREC rank: {edhrec_count:,}")
        
        # Count non-EDHREC cards
        non_edhrec_count = cards_unsorted.count_documents({
            '$or': [
                {'edhrec_rank': {'$exists': False}},
                {'edhrec_rank': None},
                {'edhrec_rank': {'$type': 'string'}}  # Sometimes it's a string
            ]
        })
        logger.info(f"❌ Cards without EDHREC rank: {non_edhrec_count:,}")
        
        # Delete non-EDHREC cards
        logger.info("🗑️ Deleting non-EDHREC cards...")
        delete_result = cards_unsorted.delete_many({
            '$or': [
                {'edhrec_rank': {'$exists': False}},
                {'edhrec_rank': None},
                {'edhrec_rank': {'$type': 'string'}}
            ]
        })
        logger.info(f"✅ Deleted {delete_result.deleted_count:,} non-EDHREC cards")
        
        # Verify remaining count
        remaining_count = cards_unsorted.count_documents({})
        logger.info(f"📊 Remaining cards: {remaining_count:,}")
        
        # Drop existing cards_edhrec collection if it exists
        if 'cards_edhrec' in db.list_collection_names():
            logger.info("🗑️ Dropping existing 'cards_edhrec' collection...")
            db['cards_edhrec'].drop()
        
        # Rename cards_unsorted to cards_edhrec
        logger.info("📝 Renaming 'cards_unsorted' to 'cards_edhrec'...")
        cards_unsorted.rename('cards_edhrec')
        
        # Verify the new collection
        cards_edhrec = db['cards_edhrec']
        final_count = cards_edhrec.count_documents({})
        logger.info(f"✅ Final 'cards_edhrec' collection has {final_count:,} cards")
        
        # Show some sample cards
        logger.info("📋 Sample cards in queue (by EDHREC rank):")
        sample_cards = list(cards_edhrec.find(
            {'edhrec_rank': {'$exists': True, '$ne': None}},
            {'name': 1, 'edhrec_rank': 1, 'type_line': 1}
        ).sort('edhrec_rank', 1).limit(10))
        
        for i, card in enumerate(sample_cards, 1):
            name = card.get('name', 'N/A')
            rank = card.get('edhrec_rank', 'N/A')
            type_line = card.get('type_line', 'N/A')
            logger.info(f"  {i:2d}. {name:<30} | Rank: {rank:>6} | {type_line}")
        
        client.close()
        return final_count
        
    except Exception as e:
        logger.error(f"❌ Error filtering and renaming: {e}")
        return 0

def main():
    parser = argparse.ArgumentParser(
        description='Setup EDHREC queue for MTGAbyss',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script will:
1. Download fresh oracle-cards.json from Scryfall
2. Import all cards into 'cards_unsorted'
3. Delete non-EDHREC cards
4. Rename to 'cards_edhrec' for processing queue

Examples:
  python setup_edhrec_queue.py
        """
    )
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("🔍 DRY RUN MODE - No changes will be made")
        logger.info("Would download fresh Scryfall data")
        logger.info("Would import to 'cards_unsorted'")
        logger.info("Would delete non-EDHREC cards")
        logger.info("Would rename to 'cards_edhrec'")
        return 0
    
    logger.info("🚀 MTGAbyss EDHREC Queue Setup Starting")
    logger.info("=" * 50)
    
    # Step 1: Get bulk data info
    oracle_info = get_bulk_data_info()
    if not oracle_info:
        return 1
    
    # Step 2: Download data
    json_file = download_oracle_data(oracle_info['download_uri'])
    if not json_file:
        return 1
    
    try:
        # Step 3: Import to unsorted collection
        imported_count = import_to_unsorted(json_file)
        if imported_count == 0:
            return 1
        
        # Step 4: Filter and rename
        final_count = filter_and_rename()
        if final_count == 0:
            return 1
        
        logger.info("=" * 50)
        logger.info("🎉 EDHREC Queue Setup Complete!")
        logger.info(f"✅ Collection 'cards_edhrec' ready with {final_count:,} EDHREC-ranked cards")
        logger.info("🎯 Workers can now process cards from this collection in EDHREC rank order")
        
    finally:
        # Clean up temporary file
        if json_file and os.path.exists(json_file):
            os.remove(json_file)
            logger.info("🧹 Cleaned up temporary files")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
