#!/usr/bin/env python3
"""
Quick Scryfall Data Refresh for MTGAbyss
========================================

Simple script to refresh MTGAbyss with fresh Scryfall data.
- Downloads latest Oracle cards if older than 12 hours
- Handles duplicates properly
- Creates proper indexes

Usage:
  python refresh_scryfall.py        # Check and refresh if needed
  python refresh_scryfall.py --force # Force refresh regardless of age
"""

import os
import sys
import json
import requests
import gzip
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
import argparse

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = 'mtgabyss'
SCRYFALL_BULK_API = 'https://api.scryfall.com/bulk-data'
DATA_DIR = '/tmp/scryfall_data'
MAX_AGE_HOURS = 12

def main():
    parser = argparse.ArgumentParser(description='Quick Scryfall data refresh')
    parser.add_argument('--force', action='store_true', help='Force refresh regardless of age')
    args = parser.parse_args()
    
    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)
    json_file = os.path.join(DATA_DIR, 'oracle-cards.json')
    
    # Check if we need fresh data
    need_refresh = args.force
    
    if not need_refresh and os.path.exists(json_file):
        file_time = datetime.fromtimestamp(os.path.getmtime(json_file), tz=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - file_time).total_seconds() / 3600
        need_refresh = age_hours > MAX_AGE_HOURS
        print(f"📅 Local data age: {age_hours:.1f} hours ({'refresh needed' if need_refresh else 'still fresh'})")
    
    if need_refresh:
        print("🔄 Refreshing Scryfall data...")
        
        # Get download URL
        response = requests.get(SCRYFALL_BULK_API, timeout=30)
        bulk_data = response.json()
        
        oracle_data = None
        for item in bulk_data.get('data', []):
            if item.get('type') == 'oracle_cards':
                oracle_data = item
                break
        
        if not oracle_data:
            print("❌ Oracle cards data not found!")
            return 1
        
        download_url = oracle_data['download_uri']
        print(f"📥 Downloading from: {download_url}")
        
        # Download
        temp_file = json_file + '.tmp'
        response = requests.get(download_url, stream=True, timeout=60)
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(temp_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r   Progress: {percent:.1f}%", end='', flush=True)
        
        print(f"\n✅ Downloaded {downloaded:,} bytes")
        
        # Handle gzip if needed
        with open(temp_file, 'rb') as f:
            magic = f.read(2)
        
        if magic == b'\x1f\x8b':
            print("📤 Extracting gzipped data...")
            with gzip.open(temp_file, 'rb') as f_in:
                with open(json_file, 'wb') as f_out:
                    f_out.write(f_in.read())
            os.remove(temp_file)
        else:
            os.rename(temp_file, json_file)
    
    # Import to MongoDB
    print("🗄️ Importing to MongoDB...")
    
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    cards_collection = db['cards']
    
    # Clear existing and import fresh
    cards_collection.delete_many({})
    
    with open(json_file, 'r', encoding='utf-8') as f:
        cards_data = json.load(f)
    
    print(f"📊 Importing {len(cards_data):,} cards...")
    
    # Add uuid field and import in batches
    batch = []
    imported = 0
    
    for card in cards_data:
        if 'id' in card:
            card['uuid'] = card['id']  # Use Scryfall's id as uuid
        batch.append(card)
        
        if len(batch) >= 1000:
            cards_collection.insert_many(batch)
            imported += len(batch)
            print(f"   📋 {imported:,} cards imported...")
            batch = []
    
    if batch:
        cards_collection.insert_many(batch)
        imported += len(batch)
    
    print(f"✅ Imported {imported:,} cards")
    
    # Create indexes (skip if they cause duplicate errors)
    print("🏗️ Creating indexes...")
    try:
        cards_collection.create_index('uuid', sparse=True)
        cards_collection.create_index('name', sparse=True)
        cards_collection.create_index('set', sparse=True)
        print("✅ Indexes created")
    except Exception as e:
        print(f"⚠️ Index creation warning: {e}")
    
    client.close()
    print("\n🎉 MTGAbyss database refreshed with fresh Scryfall data!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
