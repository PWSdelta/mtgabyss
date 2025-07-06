#!/usr/bin/env python3
"""
Script to submit a priority list of card UUIDs to MTGAbyss for processing.
Usage: python submit_priority_list.py uuids.txt
"""
import sys
import requests
import json

def read_uuids_from_file(filename):
    """Read UUIDs from a text file, one per line"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            uuids = []
            for line in f:
                uuid = line.strip()
                if uuid and not uuid.startswith('#'):  # Skip empty lines and comments
                    uuids.append(uuid)
            return uuids
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found")
        return None
    except Exception as e:
        print(f"Error reading file: {e}")
        return None

def submit_priority_list(uuids, base_url='http://localhost:5000'):
    """Submit a list of UUIDs to the priority queue"""
    url = f"{base_url}/api/submit_priority_list"
    payload = {'uuids': uuids}
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        return response.json(), response.status_code
    except requests.exceptions.RequestException as e:
        return {'error': str(e)}, 500

def check_priority_status(base_url='http://localhost:5000'):
    """Check the current status of the priority queue"""
    url = f"{base_url}/api/priority_status"
    
    try:
        response = requests.get(url, timeout=30)
        return response.json(), response.status_code
    except requests.exceptions.RequestException as e:
        return {'error': str(e)}, 500

def main():
    if len(sys.argv) != 2:
        print("Usage: python submit_priority_list.py <uuids_file.txt>")
        print("\nExample uuids.txt format:")
        print("17ef3058-46b8-4ec4-950f-c721919c4ac1")
        print("2b4c5a3f-8d1e-4f7a-9c2b-1a3d4e5f6789")
        print("# This is a comment line and will be ignored")
        print("3c5d6a4g-9e2f-5g8b-ad3c-2b4e5f6g7890")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    # Read UUIDs from file
    print(f"Reading UUIDs from {filename}...")
    uuids = read_uuids_from_file(filename)
    
    if uuids is None:
        sys.exit(1)
    
    if not uuids:
        print("Error: No valid UUIDs found in file")
        sys.exit(1)
    
    print(f"Found {len(uuids)} UUIDs to submit")
    
    # Submit to priority queue
    print("Submitting priority list...")
    result, status_code = submit_priority_list(uuids)
    
    if status_code == 200:
        print("✅ Priority list submitted successfully!")
        print(f"   Valid cards: {result.get('valid_cards', 0)}")
        print(f"   Cards needing analysis: {result.get('cards_needing_analysis', 0)}")
        print(f"   Cards with analysis: {result.get('cards_with_analysis', 0)}")
        
        if result.get('invalid_uuids'):
            print(f"   ⚠️  Invalid UUIDs: {len(result['invalid_uuids'])}")
            for uuid in result['invalid_uuids'][:5]:  # Show first 5
                print(f"      - {uuid}")
            if len(result['invalid_uuids']) > 5:
                print(f"      ... and {len(result['invalid_uuids']) - 5} more")
    else:
        print(f"❌ Error submitting priority list (HTTP {status_code}):")
        print(f"   {result.get('message', 'Unknown error')}")
        sys.exit(1)
    
    # Check queue status
    print("\nChecking priority queue status...")
    status_result, status_code = check_priority_status()
    
    if status_code == 200:
        stats = status_result.get('queue_stats', {})
        print(f"   Queue status: {stats.get('processed', 0)}/{stats.get('total_submitted', 0)} processed")
        print(f"   Completion: {stats.get('completion_percentage', 0):.1f}%")
        
        next_cards = status_result.get('next_cards', [])
        if next_cards:
            print(f"   Next cards to process:")
            for card in next_cards[:3]:  # Show first 3
                print(f"      {card['priority_order']}. {card['name']} ({card['uuid']})")
    else:
        print(f"⚠️  Could not check queue status: {status_result.get('message', 'Unknown error')}")

if __name__ == '__main__':
    main()
