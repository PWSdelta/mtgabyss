#!/usr/bin/env python3
"""
Check dual-faced card structure in the database
"""
from pymongo import MongoClient
import os
import json

def check_dual_faced_cards():
    client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
    db = client.mtgabyss
    cards = db.cards

    # Find cards with // in their name (dual-faced cards)
    dual_cards = list(cards.find({'name': {'$regex': ' // '}}).limit(5))

    print('=== DUAL-FACED CARDS STRUCTURE ===')
    print(f'Found {len(dual_cards)} dual-faced cards')
    
    for card in dual_cards:
        print(f'\nCard: {card.get("name", "Unknown")}')
        print(f'UUID: {card.get("uuid")}')
        print(f'Image URIs (root level): {json.dumps(card.get("image_uris", {}), indent=2)}')
        print(f'Has card_faces: {"card_faces" in card}')
        
        if 'card_faces' in card:
            faces = card.get('card_faces', [])
            print(f'Number of faces: {len(faces)}')
            for i, face in enumerate(faces):
                print(f'  Face {i}: {face.get("name", "No name")}')
                print(f'  Face {i} Image URIs: {json.dumps(face.get("image_uris", {}), indent=2)}')
        print('---')

if __name__ == '__main__':
    check_dual_faced_cards()
