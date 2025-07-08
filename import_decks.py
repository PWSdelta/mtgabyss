#!/usr/bin/env python3
"""
Import deck files from AllDeckFiles/ directory into MongoDB
"""

import os
import json
import logging
from pymongo import MongoClient
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def import_decks():
    """Import all JSON deck files from AllDeckFiles/ directory"""
    
    # Connect to MongoDB
    client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
    db = client.mtgabyss
    decks = db.decks
    
    # Path to deck files
    deck_files_dir = "AllDeckFiles"
    
    if not os.path.exists(deck_files_dir):
        logger.error(f"Directory {deck_files_dir} not found!")
        return
    
    # Clear existing decks collection
    logger.info("Clearing existing decks collection...")
    decks.delete_many({})
    
    # Import all JSON files
    imported_count = 0
    error_count = 0
    
    for filename in os.listdir(deck_files_dir):
        if not filename.endswith('.json'):
            continue
            
        filepath = os.path.join(deck_files_dir, filename)
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                deck_data = json.load(f)
            
            # Add metadata
            deck_data['imported_at'] = datetime.now().isoformat()
            deck_data['source_file'] = filename
            
            # Ensure required fields exist
            if 'name' not in deck_data:
                deck_data['name'] = filename.replace('.json', '')
            
            if 'mainboard' not in deck_data:
                deck_data['mainboard'] = []
            
            if 'sideboard' not in deck_data:
                deck_data['sideboard'] = []
            
            # Insert into MongoDB
            decks.insert_one(deck_data)
            imported_count += 1
            
            if imported_count % 100 == 0:
                logger.info(f"Imported {imported_count} decks...")
                
        except Exception as e:
            logger.error(f"Error importing {filename}: {str(e)}")
            error_count += 1
    
    logger.info(f"Import complete! Imported {imported_count} decks with {error_count} errors.")
    
    # Create indexes for better performance
    logger.info("Creating indexes...")
    try:
        decks.create_index('name')
        decks.create_index('imported_at')
        decks.create_index('source_file')
        logger.info("Indexes created successfully")
    except Exception as e:
        logger.error(f"Error creating indexes: {str(e)}")
    
    # Show some stats
    total_decks = decks.count_documents({})
    logger.info(f"Total decks in collection: {total_decks}")
    
    # Show sample deck
    sample_deck = decks.find_one({})
    if sample_deck:
        logger.info(f"Sample deck: {sample_deck.get('name', 'Unnamed')}")
        logger.info(f"  Mainboard cards: {len(sample_deck.get('mainboard', []))}")
        logger.info(f"  Sideboard cards: {len(sample_deck.get('sideboard', []))}")

if __name__ == "__main__":
    import_decks()
