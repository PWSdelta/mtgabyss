import json
import gzip
import requests
from pymongo import MongoClient, UpdateOne
import os
import gc
from decimal import Decimal

# Config
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('DB_NAME', 'mtgabyss')
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'cards')
BATCH_SIZE = 10  # Ultra-small batch for 1GB RAM server with 2.5GB file

# Scryfall bulk data API URL
SCRYFALL_BULK_API_URL = 'https://api.scryfall.com/bulk-data'
LOCAL_GZ_PATH = os.path.join(os.path.dirname(__file__), 'downloads', 'all-cards.json')

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
cards = db[COLLECTION_NAME]

# Ensure index on uuid for fast upserts
cards.create_index('uuid', unique=True)

def get_all_cards_download_url():
    """Fetch the current download URL for all cards from Scryfall API"""
    print("Fetching current download URL from Scryfall API...")
    response = requests.get(SCRYFALL_BULK_API_URL)
    response.raise_for_status()
    bulk_data = response.json()
    
    for item in bulk_data['data']:
        if item['type'] == 'all_cards':
            print(f"Found all_cards bulk data: {item['name']} ({item['size']} bytes)")
            return item['download_uri']
    
    raise ValueError("Could not find 'all_cards' bulk data item")

def download_if_needed():
    if not os.path.exists(LOCAL_GZ_PATH):
        download_url = get_all_cards_download_url()
        print(f'Downloading {download_url} ...')
        os.makedirs(os.path.dirname(LOCAL_GZ_PATH), exist_ok=True)
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(LOCAL_GZ_PATH, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print('Download complete.')
    else:
        # Check if existing file is complete (not corrupted)
        file_size = os.path.getsize(LOCAL_GZ_PATH)
        expected_min_size = 2 * 1024 * 1024 * 1024  # 2GB minimum (file is ~2.5GB)
        
        if file_size < expected_min_size:
            print(f'Existing file appears incomplete ({file_size:,} bytes, expected ~2.5GB), re-downloading...')
            os.remove(LOCAL_GZ_PATH)
            download_if_needed()  # Recursive call to download
        else:
            print(f'File already exists: {LOCAL_GZ_PATH} ({file_size:,} bytes) - skipping download')

def convert_decimals(obj):
    """Convert Decimal objects to floats for MongoDB compatibility"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    return obj

def process_cards_ultra_low_memory(file_path):
    """Process cards with ultra-minimal memory usage for 1GB RAM server"""
    print(f'Processing cards from {file_path} with ultra-low memory approach...')
    
    to_upsert = []
    count = 0
    
    try:
        # Use ijson for true streaming JSON parsing
        import ijson
        print("Using ijson for memory-efficient streaming...")
        
        # Check if file is gzipped or plain JSON
        with open(file_path, 'rb') as test_file:
            magic = test_file.read(2)
            test_file.seek(0)
            is_gzipped = magic == b'\x1f\x8b'
        
        if is_gzipped:
            print("File is gzipped, decompressing...")
            file_handle = gzip.open(file_path, 'rb')
        else:
            print("File is plain JSON...")
            file_handle = open(file_path, 'rb')
        
        with file_handle:
            # Parse the JSON array items one by one
            parser = ijson.items(file_handle, 'item')
            
            for card in parser:
                if isinstance(card, dict) and 'lang' in card and 'id' in card:
                    # Create minimal card document with only essential fields first
                    card_doc = {
                        'uuid': card['id'] + '-' + card['lang'],
                        'scryfall_id': card['id'],
                        'scryfall_oracle_id': card.get('oracle_id'),
                        'name': card.get('name', ''),
                        'lang': card['lang'],
                        'set': card.get('set', ''),
                        'mana_cost': card.get('mana_cost', ''),
                        'type_line': card.get('type_line', ''),
                        'oracle_text': card.get('oracle_text', ''),
                        'power': card.get('power'),
                        'toughness': card.get('toughness'),
                        'cmc': card.get('cmc', 0),
                        'colors': card.get('colors', []),
                        'rarity': card.get('rarity', ''),
                        'released_at': card.get('released_at', ''),
                        'image_uris': card.get('image_uris', {}),
                        'prices': card.get('prices', {})
                    }
                    
                    # Convert any Decimal values to floats for MongoDB
                    card_doc = convert_decimals(card_doc)
                    
                    # Only add non-None/non-empty values to minimize document size
                    card_doc = {k: v for k, v in card_doc.items() if v is not None and v != ''}
                    
                    to_upsert.append(UpdateOne(
                        {'uuid': card_doc['uuid']},
                        {'$set': card_doc},
                        upsert=True
                    ))
                    count += 1
                    
                    # Process in very small batches for 1GB RAM
                    if len(to_upsert) >= BATCH_SIZE:
                        cards.bulk_write(to_upsert, ordered=False)
                        print(f'Upserted batch ending at {count} cards... (Memory optimized)')
                        to_upsert.clear()
                        gc.collect()  # Force garbage collection
                    
                    if count % 2500 == 0:
                        print(f'Processed {count} cards so far...')
                        
        # Process final batch
        if to_upsert:
            cards.bulk_write(to_upsert, ordered=False)
            print(f'Final batch upserted. Total processed: {count}')
            gc.collect()
        
        return count
        
    except ImportError:
        print("ijson not available, installing it...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'ijson==3.2.3'])
        print("ijson installed, please run the script again.")
        return 0
        
    except Exception as e:
        print(f'Error during processing: {e}')
        if to_upsert:
            try:
                cards.bulk_write(to_upsert, ordered=False)
                print(f'Emergency batch saved: {len(to_upsert)} cards')
            except:
                pass
        raise

def main():
    print("Starting MTG Abyss card import (optimized for 1GB RAM)...")
    download_if_needed()
    
    # Process cards using ultra-low memory approach
    total_processed = process_cards_ultra_low_memory(LOCAL_GZ_PATH)
    
    print(f'Import complete. Total cards processed: {total_processed}')
    
    # Delete the file after import to save disk space
    try:
        os.remove(LOCAL_GZ_PATH)
        print(f'Deleted {LOCAL_GZ_PATH} to save disk space')
    except Exception as e:
        print(f'Warning: Could not delete {LOCAL_GZ_PATH}: {e}')

if __name__ == '__main__':
    main()
