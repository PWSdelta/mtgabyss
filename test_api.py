#!/usr/bin/env python3
"""
Test script to debug API submission issues
"""
import requests
import json

# Test the API endpoint directly
MTGABYSS_BASE_URL = "https://mtgabyss.com"

def test_api_endpoint():
    """Test if the API endpoint is working"""
    
    # Test 1: Check if API is reachable
    print("🔍 Testing API reachability...")
    try:
        resp = requests.get(f"{MTGABYSS_BASE_URL}/api/stats", timeout=10)
        print(f"   Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"   Response: {resp.json()}")
        else:
            print(f"   Error: {resp.text}")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Test 2: Check submit_work endpoint with a simple payload
    print("\n🔍 Testing submit_work endpoint...")
    
    test_payload = [{
        "uuid": "test-uuid-12345",
        "analysis": {
            "long_form": "## Test Analysis\n\nThis is a test analysis to see if the API is working.",
            "sections": {
                "overview": {
                    "title": "Card Overview", 
                    "content": "This is a test card analysis.",
                    "language": "en"
                }
            },
            "analyzed_at": "2025-07-05T23:30:00.000Z",
            "model_used": "gemini-1.5-flash",
            "guide_version": "2.1_sequential"
        },
        "category": "mtg",
        "card_data": {
            "name": "Test Card",
            "uuid": "test-uuid-12345",
            "mana_cost": "{1}",
            "type_line": "Artifact"
        }
    }]
    
    try:
        resp = requests.post(f"{MTGABYSS_BASE_URL}/api/submit_work", 
                           json=test_payload, 
                           timeout=30)
        print(f"   Status: {resp.status_code}")
        print(f"   Response: {resp.text}")
    except Exception as e:
        print(f"   Error: {e}")

    # Test 3: Check get_random_unreviewed endpoint  
    print("\n🔍 Testing get_random_unreviewed endpoint...")
    try:
        resp = requests.get(f"{MTGABYSS_BASE_URL}/api/get_random_unreviewed?lang=en&limit=1", timeout=10)
        print(f"   Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"   Found {len(data.get('cards', []))} cards")
            if data.get('cards'):
                card = data['cards'][0]
                print(f"   Sample card: {card.get('name')} ({card.get('uuid')})")
        else:
            print(f"   Error: {resp.text}")
    except Exception as e:
        print(f"   Error: {e}")

if __name__ == "__main__":
    test_api_endpoint()
