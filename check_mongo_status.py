"""
MongoDB Index and Lock Status Checker for MTGAbyss

Run this script to check for active index builds, current locks, and index sizes on your 'cards' collection.
"""

from pymongo import MongoClient
import os

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('MONGODB_DB', 'mtgabyss')
COLLECTION_NAME = 'cards'

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
cards = db[COLLECTION_NAME]


print(f"Using database: {DB_NAME}, collection: {COLLECTION_NAME}\n")

# Connect to admin DB for currentOp
admin_db = client['admin']

# 1. Check for active index builds (must use admin DB)
print("Checking for active index builds...")
try:
    current_ops = admin_db.command('currentOp')
    index_builds = [op for op in current_ops.get('inprog', []) if op.get('command', {}).get('createIndexes')]
    if index_builds:
        print(f"Active index builds found: {len(index_builds)}")
        for op in index_builds:
            print(f"  Namespace: {op.get('ns')}, Command: {op.get('command')}, Progress: {op.get('progress', {})}")
    else:
        print("No active index builds.")
except Exception as e:
    print(f"Error checking current operations: {e}")

# 2. Check for locks
print("\nChecking for locks...")
try:
    server_status = db.command('serverStatus')
    locks = server_status.get('locks', {})
    if locks:
        for k, v in locks.items():
            print(f"Lock: {k}, Info: {v}")
    else:
        print("No lock info found.")
except Exception as e:
    print(f"Error checking locks: {e}")


# 3. List all indexes and their sizes (use collStats command)
print("\nIndex sizes for 'cards' collection:")
try:
    stats = db.command({'collStats': COLLECTION_NAME})
    index_sizes = stats.get('indexSizes', {})
    for name, size in index_sizes.items():
        print(f"  {name}: {size / (1024*1024):.2f} MB")
except Exception as e:
    print(f"Error getting index sizes: {e}")

print("\nAll indexes on 'cards' collection:")
try:
    for idx in cards.list_indexes():
        print(idx)
except Exception as e:
    print(f"Error listing indexes: {e}")
