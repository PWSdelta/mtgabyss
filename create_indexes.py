"""
MongoDB Index Creation Script for MTGAbyss

Run this script on your server to add indexes that will speed up queries for unreviewed cards, stats, and card lookups.
"""

from pymongo import MongoClient
import os

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('MONGODB_DB', 'mtgabyss')
COLLECTION_NAME = 'cards'

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
cards = db[COLLECTION_NAME]

print(f"Using database: {DB_NAME}, collection: {COLLECTION_NAME}")

# Indexes for fast unreviewed card queries and stats

# Improved and additional indexes for performance
indexes = [
    # Existing indexes
    ([('analysis', 1), ('lang', 1)], {}),
    ([('analysis', 1), ('lang', 1), ('rarity', 1)], {}),
    ([('analysis', 1), ('lang', 1), ('set', 1)], {}),
    ([('uuid', 1)], {'unique': True}),
    ([('scryfall_id', 1)], {}),
    ([('name', 1)], {}),

    # New/Improved indexes
    # For 'cards mentioned in this review' (name + analysis.long_form)
    ([('name', 1), ('analysis.long_form', 1)], {}),
    # For 'most expensive cards' (analysis.long_form + price fields)
    ([('analysis.long_form', 1), ('prices.usd', -1), ('prices.eur', -1)], {}),
    # For filtering by image presence
    ([('imageUris.normal', 1)], {}),
    # (Optional) Partial index for cards with analysis.long_form (if supported)
    # Uncomment if using MongoDB 3.2+
    # ([('name', 1)], {'partialFilterExpression': {'analysis.long_form': {'$exists': True, '$ne': ''}}}),
]

for fields, opts in indexes:
    print(f"Creating index: {fields} {opts}")
    result = cards.create_index(fields, **opts)
    print(f"  -> Index created: {result}")

print("All indexes created. You can now restart your Flask app for best performance.")
