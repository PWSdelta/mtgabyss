#!/usr/bin/env python3
"""
Quick database check to see if we have any cards with sectioned guides
"""

import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')

def check_database():
    client = MongoClient(MONGODB_URI)
    db = client['mtgabyss']
    cards = db['cards']
    
    # Check total cards
    total_cards = cards.count_documents({})
    print(f"Total cards in database: {total_cards:,}")
    
    # Check cards with analysis
    cards_with_analysis = cards.count_documents({"has_analysis": True})
    print(f"Cards with analysis: {cards_with_analysis:,}")
    
    # Check for new sectioned guides
    sectioned_guides = cards.count_documents({"analysis.guide_version": {"$regex": "sectioned"}})
    print(f"Cards with sectioned guides: {sectioned_guides}")
    
    # Check for firehose guides specifically
    firehose_guides = cards.count_documents({"analysis.guide_version": "2.0_sectioned_firehose"})
    print(f"Cards with firehose sectioned guides: {firehose_guides}")
    
    # Show a sample of recent analyses
    print("\n--- Recent analyses (last 5) ---")
    recent_analyses = cards.find(
        {"has_analysis": True}, 
        {"name": 1, "set_name": 1, "analysis.analyzed_at": 1, "analysis.guide_version": 1, "analysis.model_used": 1}
    ).sort("analysis.analyzed_at", -1).limit(5)
    
    for card in recent_analyses:
        analysis = card.get('analysis', {})
        print(f"- {card.get('name', 'Unknown')} ({card.get('set_name', 'Unknown Set')})")
        print(f"  Analyzed: {analysis.get('analyzed_at', 'Unknown')}")
        print(f"  Version: {analysis.get('guide_version', 'Unknown')}")
        print(f"  Model: {analysis.get('model_used', 'Unknown')}")
        print()
    
    # Check if we have any sectioned guides to display
    if sectioned_guides > 0:
        print("--- Sample sectioned guide ---")
        sample = cards.find_one({"analysis.sections": {"$exists": True}})
        if sample:
            print(f"Card: {sample.get('name', 'Unknown')}")
            sections = sample.get('analysis', {}).get('sections', {})
            print(f"Sections available: {', '.join(sections.keys())}")
            if 'overview' in sections:
                overview_preview = sections['overview'][:200] + "..." if len(sections['overview']) > 200 else sections['overview']
                print(f"Overview preview: {overview_preview}")
    
    client.close()

if __name__ == "__main__":
    check_database()
