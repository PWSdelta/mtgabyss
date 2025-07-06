"""
Check dual-faced Magic: The Gathering cards in the database and print their image fields.
"""
from pymongo import MongoClient
import os

# Use environment variable or default
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
DB_NAME = os.getenv('MTGABYSS_DB', 'mtg_cards')

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]

print("Searching for dual-faced cards (name contains ' // '):\n")
cards = list(db.cards.find({'name': {'$regex': ' // '}}).limit(5))
if not cards:
    print("No dual-faced cards found.")
else:
    for card in cards:
        print(f"Name: {card.get('name')}")
        print(f"  UUID: {card.get('uuid', card.get('id', 'N/A'))}")
        if 'card_faces' in card:
            print(f"  card_faces: {len(card['card_faces'])} faces")
            for i, face in enumerate(card['card_faces']):
                if 'image_uris' in face:
                    print(f"    Face {i} image_uris: {list(face['image_uris'].keys())}")
                else:
                    print(f"    Face {i} has no image_uris")
        if 'image_uris' in card:
            print(f"  Root image_uris: {list(card['image_uris'].keys())}")
        else:
            print("  No root image_uris")
        print()

print("\nSearching for cards with 'card_faces' field (likely dual-faced):\n")
cards = list(db.cards.find({'card_faces.0': {'$exists': True}}).limit(5))
if not cards:
    print("No cards with 'card_faces' found.")
else:
    for card in cards:
        print(f"Name: {card.get('name')}")
        print(f"  UUID: {card.get('uuid', card.get('id', 'N/A'))}")
        print(f"  card_faces: {len(card['card_faces'])} faces")
        for i, face in enumerate(card['card_faces']):
            if 'image_uris' in face:
                print(f"    Face {i} image_uris: {list(face['image_uris'].keys())}")
            else:
                print(f"    Face {i} has no image_uris")
        if 'image_uris' in card:
            print(f"  Root image_uris: {list(card['image_uris'].keys())}")
        else:
            print("  No root image_uris")
        print()