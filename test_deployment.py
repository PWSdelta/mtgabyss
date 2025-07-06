#!/usr/bin/env python3
"""
Quick deployment test script
"""
import requests
import json

def test_deployment(base_url='http://localhost:5000'):
    """Test key deployment features"""
    print(f"Testing deployment at: {base_url}")
    
    tests = [
        ('API Stats', f'{base_url}/api/stats'),
        ('Priority Status', f'{base_url}/api/priority_status'),
        ('Most Mentioned', f'{base_url}/api/get_most_mentioned?limit=1'),
        ('Random Cards', f'{base_url}/api/get_random_unreviewed?limit=1'),
    ]
    
    for test_name, url in tests:
        try:
            response = requests.get(url, timeout=10)
            status = "✅" if response.status_code == 200 else "❌"
            print(f"{status} {test_name}: {response.status_code}")
            if response.status_code != 200:
                print(f"   Error: {response.text[:100]}")
        except Exception as e:
            print(f"❌ {test_name}: {e}")
    
    # Test homepage
    try:
        response = requests.get(base_url, timeout=10)
        status = "✅" if response.status_code == 200 else "❌"
        print(f"{status} Homepage: {response.status_code}")
    except Exception as e:
        print(f"❌ Homepage: {e}")

if __name__ == '__main__':
    import sys
    base_url = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:5000'
    test_deployment(base_url)
