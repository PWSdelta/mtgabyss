#!/usr/bin/env python3
"""
Debug script to check what's actually in the database
"""
import os
import requests
from dotenv import load_dotenv
load_dotenv()

MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')

def check_recent_analyses():
    """Check recent analyses via API"""
    try:
        # Try to get some analyzed cards
        resp = requests.get(f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed?lang=en&limit=1', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ API is responding: {data.get('status')}")
            print(f"   Unreviewed cards remaining: {data.get('total_unreviewed', 'Unknown')}")
        else:
            print(f"❌ API error: {resp.status_code}")
            print(f"   Response: {resp.text}")
    except Exception as e:
        print(f"❌ API connection failed: {e}")

def test_card_lookup():
    """Try to look up Gaea's Liege specifically"""
    try:
        # Search for the card we just processed
        resp = requests.get(f'{MTGABYSS_BASE_URL}/api/search?q=Gaea%27s+Liege', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ Search API works: Found {len(data.get('cards', []))} cards")
            if data.get('cards'):
                card = data['cards'][0]
                print(f"   Card: {card.get('name')} (UUID: {card.get('uuid')})")
                print(f"   Has analysis: {bool(card.get('analysis'))}")
                if card.get('analysis'):
                    analysis = card['analysis']
                    print(f"   Analysis version: {analysis.get('guide_version')}")
                    print(f"   Sections: {len(analysis.get('sections', {}))}")
                    print(f"   Long form length: {len(analysis.get('long_form', ''))}")
        else:
            print(f"❌ Search failed: {resp.status_code}")
    except Exception as e:
        print(f"❌ Search failed: {e}")

def test_submit_endpoint():
    """Test if submit endpoint is working"""
    try:
        # Just test the endpoint with a GET (should return method not allowed)
        resp = requests.get(f'{MTGABYSS_BASE_URL}/api/submit_work', timeout=10)
        print(f"Submit endpoint test: {resp.status_code} (should be 405 Method Not Allowed)")
    except Exception as e:
        print(f"❌ Submit endpoint test failed: {e}")

if __name__ == "__main__":
    print("🔍 MTGAbyss Database Debug Script")
    print("=" * 40)
    
    print("\n1. Testing API connection...")
    check_recent_analyses()
    
    print("\n2. Testing card search...")
    test_card_lookup()
    
    print("\n3. Testing submit endpoint...")
    test_submit_endpoint()
    
    print("\n✅ Debug complete!")
