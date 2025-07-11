#!/usr/bin/env python3
"""
MTGAbyss Fresh Start - Complete Database Reset & Scryfall Import
==============================================================

Wipes everything clean and starts fresh with the latest Scryfall Oracle data.
Perfect for AI algorithm experimentation without legacy baggage.

Usage:
  python fresh_start.py                    # Download and import fresh data
  python fresh_start.py --wipe-only        # Just wipe collections
  python fresh_start.py --import-only      # Just import (assumes oracle-cards.json exists)
"""

import os
import sys
import requests
import json
import gzip
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import argparse
from datetime import datetime

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'

def wipe_collections(db):
    """Completely wipe all card collections for a fresh start"""
    print("🧹 Wiping all card collections...")
    
    collections_to_wipe = ['cards', 'pending_guide', 'guide_components', 'card_sections']
    
    for collection_name in collections_to_wipe:
        try:
            collection = db[collection_name]
            count = collection.count_documents({})
            if count > 0:
                collection.drop()
                print(f"   🗑️  Dropped '{collection_name}' ({count} documents)")
            else:
                print(f"   ✅ '{collection_name}' was already empty")
        except Exception as e:
            print(f"   ⚠️  Error with '{collection_name}': {e}")
    
    print("✨ Database wiped clean!")

def download_fresh_scryfall_data():
    """Download fresh Oracle card data from Scryfall"""
    print("📥 Downloading fresh Scryfall Oracle data...")
    
    # Get bulk data info
    bulk_url = "https://api.scryfall.com/bulk-data"
    response = requests.get(bulk_url)
    bulk_data = response.json()
    
    # Find Oracle cards download
    oracle_data = None
    for item in bulk_data['data']:
        if item['type'] == 'oracle_cards':
            oracle_data = item
            break
    
    if not oracle_data:
        print("❌ Could not find Oracle cards bulk data")
        return False
    
    print(f"📊 Found Oracle data: {oracle_data['name']}")
    print(f"   Size: {oracle_data.get('size', 0):,} bytes")
    print(f"   Updated: {oracle_data.get('updated_at', 'Unknown')}")
    
    # Download the data
    download_url = oracle_data['download_uri']
    print(f"⬇️  Downloading from: {download_url}")
    
    response = requests.get(download_url, stream=True)
    
    if response.status_code != 200:
        print(f"❌ Download failed: {response.status_code}")
        return False
    
    # Save data directly (it's not actually compressed)
    json_file = "oracle-cards.json"
    with open(json_file, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    print(f"💾 Downloaded to: {json_file}")
    print("✅ Ready for import")
    
    return True

def import_fresh_data(db):
    """Import fresh Oracle card data into MongoDB"""
    print("📥 Importing fresh Oracle card data...")
    
    if not os.path.exists('oracle-cards.json'):
        print("❌ oracle-cards.json not found. Run download first.")
        return False
    
    # Load JSON data
    print("📖 Loading JSON data...")
    with open('oracle-cards.json', 'r', encoding='utf-8') as f:
        cards = json.load(f)
    
    print(f"📊 Loaded {len(cards):,} cards from Scryfall")
    
    # Import to cards collection
    cards_collection = db['cards']
    
    # Create proper indexes first
    print("🏗️  Creating indexes...")
    try:
        # Create sparse indexes (allows multiple nulls but enforces uniqueness on non-nulls)
        cards_collection.create_index('id', unique=True, sparse=True)
        cards_collection.create_index('oracle_id', sparse=True)
        cards_collection.create_index('name', sparse=True)
        print("   ✅ Created indexes")
    except Exception as e:
        print(f"   ⚠️  Index creation warning: {e}")
    
    # Import cards in batches
    batch_size = 1000
    imported = 0
    skipped = 0
    
    print("📦 Importing cards in batches...")
    
    for i in range(0, len(cards), batch_size):
        batch = cards[i:i + batch_size]
        
        # Insert batch
        try:
            # Use insert_many with ordered=False to continue on errors
            result = cards_collection.insert_many(batch, ordered=False)
            imported += len(result.inserted_ids)
        except Exception as e:
            # Count successful insertions vs errors
            for card in batch:
                try:
                    cards_collection.insert_one(card)
                    imported += 1
                except DuplicateKeyError:
                    skipped += 1
                except Exception:
                    skipped += 1
        
        # Progress update
        if (i + batch_size) % 5000 == 0:
            print(f"   📋 Processed {i + batch_size:,}/{len(cards):,} cards...")
    
    print(f"✅ Import complete!")
    print(f"   📊 Imported: {imported:,} cards")
    print(f"   ⚠️  Skipped: {skipped:,} cards")
    
    # Final stats
    total_in_db = cards_collection.count_documents({})
    print(f"   🎯 Total in database: {total_in_db:,} cards")
    
    # Clean up JSON file
    os.remove('oracle-cards.json')
    print("🧹 Cleaned up temporary files")
    
    return True

def main():
    parser = argparse.ArgumentParser(description='Fresh start for MTGAbyss database')
    parser.add_argument('--wipe-only', action='store_true', help='Only wipe collections, don\'t import')
    parser.add_argument('--import-only', action='store_true', help='Only import, don\'t wipe or download')
    args = parser.parse_args()
    
    print("🚀 MTGAbyss Fresh Start")
    print("=" * 50)
    
    # Connect to MongoDB
    print("🔗 Connecting to MongoDB...")
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    
    if not args.import_only:
        # Wipe existing data
        wipe_collections(db)
        
        if args.wipe_only:
            print("✅ Wipe complete!")
            return
    
    if not args.wipe_only:
        if not args.import_only:
            # Download fresh data
            if not download_fresh_scryfall_data():
                print("❌ Download failed")
                return
        
        # Import fresh data
        if not import_fresh_data(db):
            print("❌ Import failed")
            return
    
    print("\n🎉 Fresh start complete!")
    print("Your database is now clean and ready for AI algorithm experimentation!")
    
    client.close()

if __name__ == "__main__":
    main()
