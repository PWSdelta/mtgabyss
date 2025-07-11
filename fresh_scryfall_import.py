#!/usr/bin/env python3
"""
Fresh Scryfall Oracle Data Importer for MTGAbyss
================================================

Downloads fresh Oracle card data from Scryfall and imports it properly into MongoDB.
- Downloads latest bulk data (oracle-cards.json) from Scryfall
- Handles proper UUID structure using Scryfall's 'id' field
- Creates proper indexes for both normal and dual-faced cards
- Replaces existing data with fresh import (within 12 hours)

Usage:
  python fresh_scryfall_import.py --import
  python fresh_scryfall_import.py --check-age
  python fresh_scryfall_import.py --force-download
"""

import os
import sys
import json
import requests
import gzip
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import argparse

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'
SCRYFALL_BULK_API = 'https://api.scryfall.com/bulk-data'
DATA_DIR = '/tmp/scryfall_data'
MAX_AGE_HOURS = 12

def ensure_data_dir():
    """Ensure the data directory exists"""
    os.makedirs(DATA_DIR, exist_ok=True)

def get_bulk_data_info():
    """Get information about available bulk data from Scryfall"""
    print("🔍 Checking Scryfall bulk data availability...")
    
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
            print("❌ Oracle cards bulk data not found!")
            return None
        
        print(f"✅ Found oracle cards data:")
        print(f"   Size: {oracle_data.get('compressed_size', 0):,} bytes compressed")
        print(f"   Updated: {oracle_data.get('updated_at')}")
        print(f"   Download URL: {oracle_data.get('download_uri')}")
        
        return oracle_data
        
    except Exception as e:
        print(f"❌ Error fetching bulk data info: {e}")
        return None

def download_oracle_data(download_url):
    """Download oracle cards data (handles both compressed and uncompressed)"""
    ensure_data_dir()
    
    temp_file = os.path.join(DATA_DIR, 'oracle-cards-temp')
    json_file = os.path.join(DATA_DIR, 'oracle-cards.json')
    
    print(f"📥 Downloading oracle cards data...")
    print(f"   From: {download_url}")
    print(f"   To: {temp_file}")
    
    try:
        # Download file
        response = requests.get(download_url, stream=True, timeout=60)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(temp_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r   Progress: {percent:.1f}% ({downloaded:,}/{total_size:,} bytes)", end='', flush=True)
        
        print(f"\n✅ Download complete: {downloaded:,} bytes")
        
        # Check if the file is gzipped
        with open(temp_file, 'rb') as f:
            magic = f.read(2)
        
        if magic == b'\x1f\x8b':  # gzip magic number
            print("📤 File is gzipped, extracting...")
            with gzip.open(temp_file, 'rb') as f_in:
                with open(json_file, 'wb') as f_out:
                    f_out.write(f_in.read())
            os.remove(temp_file)
        else:
            print("📄 File is uncompressed JSON")
            os.rename(temp_file, json_file)
        
        print(f"✅ Data ready at: {json_file}")
        return json_file
        
    except Exception as e:
        print(f"❌ Error downloading data: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None

def check_existing_data():
    """Check if we have recent data that's still fresh"""
    json_file = os.path.join(DATA_DIR, 'oracle-cards.json')
    
    if not os.path.exists(json_file):
        print("📁 No existing local data found")
        return False, None
    
    # Check file age
    file_time = datetime.fromtimestamp(os.path.getmtime(json_file), tz=timezone.utc)
    now = datetime.now(timezone.utc)
    age_hours = (now - file_time).total_seconds() / 3600
    
    print(f"📅 Local data age: {age_hours:.1f} hours")
    
    if age_hours > MAX_AGE_HOURS:
        print(f"⏰ Data is older than {MAX_AGE_HOURS} hours, needs refresh")
        return False, json_file
    else:
        print(f"✅ Data is fresh (< {MAX_AGE_HOURS} hours)")
        return True, json_file

def import_cards_to_mongodb(json_file):
    """Import cards from JSON file to MongoDB"""
    print(f"🗄️ Importing cards to MongoDB...")
    
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        
        # Backup existing collections
        backup_existing_collections(db)
        
        # Clear existing cards collection
        cards_collection = db['cards']
        print("🗑️ Clearing existing cards collection...")
        cards_collection.delete_many({})
        
        # Import new data
        print(f"📖 Reading cards from {json_file}...")
        imported_count = 0
        batch_size = 1000
        batch = []
        
        with open(json_file, 'r', encoding='utf-8') as f:
            cards_data = json.load(f)
        
        total_cards = len(cards_data)
        print(f"📊 Found {total_cards:,} cards to import")
        
        for card in cards_data:
            # Use Scryfall's 'id' as 'uuid' for consistency with your existing code
            if 'id' in card:
                card['uuid'] = card['id']
            
            batch.append(card)
            
            if len(batch) >= batch_size:
                cards_collection.insert_many(batch)
                imported_count += len(batch)
                print(f"   📋 Imported {imported_count:,}/{total_cards:,} cards...")
                batch = []
        
        # Import remaining cards
        if batch:
            cards_collection.insert_many(batch)
            imported_count += len(batch)
        
        print(f"✅ Successfully imported {imported_count:,} cards")
        
        # Create proper indexes
        create_proper_indexes(db)
        
        # Show statistics
        show_import_statistics(db)
        
        client.close()
        return True
        
    except Exception as e:
        print(f"❌ Error importing cards: {e}")
        return False

def backup_existing_collections(db):
    """Create backup of existing collections"""
    print("💾 Creating backup of existing data...")
    
    try:
        # Backup cards collection
        cards_collection = db['cards']
        backup_collection = db['cards_backup']
        
        existing_count = cards_collection.count_documents({})
        if existing_count > 0:
            print(f"   Backing up {existing_count:,} existing cards...")
            
            # Clear old backup
            backup_collection.delete_many({})
            
            # Copy to backup
            cards = list(cards_collection.find())
            if cards:
                backup_collection.insert_many(cards)
                print(f"   ✅ Backed up {len(cards):,} cards")
        else:
            print("   No existing cards to backup")
            
    except Exception as e:
        print(f"   ⚠️ Backup failed: {e}")

def create_proper_indexes(db):
    """Create proper indexes for the collections"""
    print("🏗️ Creating MongoDB indexes...")
    
    try:
        collections_to_index = ['cards', 'pending_guide']
        
        for collection_name in collections_to_index:
            collection = db[collection_name]
            
            print(f"   Creating indexes on {collection_name}...")
            
            # Drop existing indexes (except _id)
            existing_indexes = list(collection.list_indexes())
            for index in existing_indexes:
                if index['name'] != '_id_':
                    try:
                        collection.drop_index(index['name'])
                    except:
                        pass
            
            # Create new indexes
            collection.create_index('uuid', unique=True, sparse=True)
            collection.create_index('id', sparse=True) 
            collection.create_index('oracle_id', sparse=True)
            collection.create_index('name', sparse=True)
            collection.create_index('set', sparse=True)
            
            # For cards collection, add guide-specific indexes
            if collection_name == 'cards':
                collection.create_index('edhrec_rank', sparse=True)
                collection.create_index([('colors', 1), ('cmc', 1)], sparse=True)
            
            print(f"   ✅ Created indexes on {collection_name}")
        
    except Exception as e:
        print(f"   ⚠️ Error creating indexes: {e}")

def show_import_statistics(db):
    """Show statistics about the imported data"""
    print("\n📊 Import Statistics:")
    
    try:
        cards_collection = db['cards']
        
        total_cards = cards_collection.count_documents({})
        dual_faced = cards_collection.count_documents({'card_faces': {'$exists': True}})
        with_oracle_text = cards_collection.count_documents({'oracle_text': {'$exists': True, '$ne': ''}})
        
        print(f"   Total cards imported: {total_cards:,}")
        print(f"   Dual-faced cards: {dual_faced:,}")
        print(f"   Cards with oracle text: {with_oracle_text:,}")
        
        # Sample some card types
        creatures = cards_collection.count_documents({'type_line': {'$regex': 'Creature', '$options': 'i'}})
        instants = cards_collection.count_documents({'type_line': {'$regex': 'Instant', '$options': 'i'}})
        sorceries = cards_collection.count_documents({'type_line': {'$regex': 'Sorcery', '$options': 'i'}})
        
        print(f"   Creatures: {creatures:,}")
        print(f"   Instants: {instants:,}")
        print(f"   Sorceries: {sorceries:,}")
        
    except Exception as e:
        print(f"   ⚠️ Error getting statistics: {e}")

def main():
    global MAX_AGE_HOURS
    
    parser = argparse.ArgumentParser(
        description='Fresh Scryfall Oracle Data Importer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--import', action='store_true', dest='do_import',
                       help='Download and import fresh data (checks age first)')
    parser.add_argument('--force-download', action='store_true',
                       help='Force download even if local data is fresh')
    parser.add_argument('--check-age', action='store_true',
                       help='Just check the age of local data')
    parser.add_argument('--max-age', type=int, default=MAX_AGE_HOURS,
                       help=f'Maximum age in hours before refresh (default: {MAX_AGE_HOURS})')
    
    args = parser.parse_args()
    
    MAX_AGE_HOURS = args.max_age
    
    ensure_data_dir()
    
    if args.check_age:
        is_fresh, file_path = check_existing_data()
        if is_fresh:
            print("✅ Local data is fresh")
            return 0
        else:
            print("⏰ Local data needs refresh")
            return 1
    
    if args.do_import or args.force_download:
        # Check if we need to download
        should_download = args.force_download
        
        if not should_download:
            is_fresh, existing_file = check_existing_data()
            should_download = not is_fresh
        
        json_file = None
        
        if should_download:
            # Get bulk data info
            bulk_info = get_bulk_data_info()
            if not bulk_info:
                return 1
            
            # Download fresh data
            json_file = download_oracle_data(bulk_info['download_uri'])
            if not json_file:
                return 1
        else:
            # Use existing file
            _, json_file = check_existing_data()
        
        if args.do_import and json_file:
            # Import to MongoDB
            if import_cards_to_mongodb(json_file):
                print("\n✅ Fresh Scryfall data import complete!")
                return 0
            else:
                print("\n❌ Import failed!")
                return 1
        elif json_file:
            print(f"\n✅ Fresh data ready at: {json_file}")
            return 0
    
    parser.print_help()
    return 1

if __name__ == "__main__":
    sys.exit(main())
