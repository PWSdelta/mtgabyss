import json
import gzip
import requests
from pymongo import MongoClient, UpdateOne
import os

# Config
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('DB_NAME', 'mtgabyss')
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'cards')
BATCH_SIZE = 2357  # Large batch for speed

# Scryfall compressed bulk data URL
SCRYFALL_BULK_URL = 'https://data.scryfall.io/all-cards/all-cards.json.gz'
LOCAL_GZ_PATH = os.path.join(os.path.dirname(__file__), 'downloads', 'all-cards.json.gz')

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
cards = db[COLLECTION_NAME]

# Ensure index on uuid for fast upserts
cards.create_index('uuid', unique=True)

def download_if_needed():
    if not os.path.exists(LOCAL_GZ_PATH):
        print(f'Downloading {SCRYFALL_BULK_URL} ...')
        os.makedirs(os.path.dirname(LOCAL_GZ_PATH), exist_ok=True)
        with requests.get(SCRYFALL_BULK_URL, stream=True) as r:
            r.raise_for_status()
            with open(LOCAL_GZ_PATH, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print('Download complete.')
    else:
        print(f'File already exists: {LOCAL_GZ_PATH}')

def main():
    download_if_needed()
    print(f'Loading Scryfall all-cards from {LOCAL_GZ_PATH} ...')
    with gzip.open(LOCAL_GZ_PATH, 'rt', encoding='utf-8') as f:
        all_cards = json.load(f)
    print(f'Total cards in file: {len(all_cards)}')

    to_upsert = []
    count = 0
    for card in all_cards:
        if 'lang' in card and 'id' in card:
            card_doc = card.copy()
            card_doc['uuid'] = card['id'] + '-' + card['lang']
            card_doc['scryfall_id'] = card['id']
            card_doc['scryfall_oracle_id'] = card.get('oracle_id')
            card_doc.pop('_id', None)
            to_upsert.append(UpdateOne(
                {'uuid': card_doc['uuid']},
                {'$set': card_doc},
                upsert=True
            ))
            count += 1
            if len(to_upsert) >= BATCH_SIZE:
                cards.bulk_write(to_upsert, ordered=False)
                print(f'Upserted {count} cards so far...')
                to_upsert = []
    # Final batch
    if to_upsert:
        cards.bulk_write(to_upsert, ordered=False)
        print(f'Final batch upserted. Total upserted: {count}')
    print(f'Import complete. Total cards processed: {count}')
    # Delete the file after import
    os.remove(LOCAL_GZ_PATH)
    print(f'Deleted {LOCAL_GZ_PATH}')

if __name__ == '__main__':
    main()
