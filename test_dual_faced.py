from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017/')
db = client.mtg_cards

# Find dual-faced cards
cards = list(db.cards.find({'name': {'$regex': ' // '}}).limit(5))

print("Found dual-faced cards:")
for card in cards:
    print(f"Name: {card['name']}, UUID: {card['uuid']}")
    if 'card_faces' in card:
        print(f"  Has card_faces: {len(card['card_faces'])} faces")
        for i, face in enumerate(card['card_faces']):
            if 'image_uris' in face:
                print(f"    Face {i}: Has image_uris")
            else:
                print(f"    Face {i}: No image_uris")
    if 'image_uris' in card:
        print(f"  Has root level image_uris")
    else:
        print(f"  No root level image_uris")
    print()
