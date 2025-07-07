# wipe_analyses.py
from pymongo import MongoClient

# Adjust these if needed
MONGODB_URI = 'mongodb://localhost:27017'
DB_NAME = 'mtgabyss'
COLLECTION = 'cards'

def main():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    cards = db[COLLECTION]
    result = cards.update_many(
        {},
        {'$unset': {'analysis': '', 'guide_meta': '', 'has_full_content': ''}}
    )
    print(f"Wiped analysis fields from {result.modified_count} cards.")

if __name__ == "__main__":
    main()