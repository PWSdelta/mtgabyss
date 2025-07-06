import pymongo
from pymongo import MongoClient
import json

# Connect to MongoDB
client = MongoClient('mongodb://localhost:27017/')
db = client['mtg_analysis']
collection = db['cards']

# Get a few sample cards and check their imageUris
cards = list(collection.find({}).limit(5))
for card in cards:
    print(f'Card: {card.get("name", "Unknown")}')
    print(f'imageUris: {json.dumps(card.get("imageUris", {}), indent=2)}')
    print('---')
