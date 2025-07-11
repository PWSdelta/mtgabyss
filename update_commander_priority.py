#!/usr/bin/env python3
"""
Commander Priority Update Script
===============================

Updates cards in the MongoDB database to add commander flags and priority levels.
This ensures commanders are reviewed first before other cards.

Usage:
  python update_commander_priority.py --set-flags
  python update_commander_priority.py --add-unguided-flag
  python update_commander_priority.py --check-status
"""

import argparse
import sys
import os
from pymongo import MongoClient
from datetime import datetime

# MongoDB configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'

def get_mongodb_client():
    """Get MongoDB client connection"""
    try:
        client = MongoClient(MONGODB_URI)
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None

def is_commander(card):
    """Check if a card is a potential commander"""
    type_line = card.get('type_line', '').lower()
    oracle_text = card.get('oracle_text', '').lower()
    
    # Legendary creatures
    if 'legendary' in type_line and 'creature' in type_line:
        return True
    
    # Cards that explicitly can be commanders
    if 'can be your commander' in oracle_text:
        return True
    
    return False

def add_unguided_flag():
    """Add 'unguided' flag to all cards based on whether they have guide sections"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🚩 Adding 'unguided' flag to all cards...")
        
        # Cards with guide sections - mark as guided (unguided: false)
        guided_result = cards_collection.update_many(
            {
                '$or': [
                    {'guide_sections': {'$exists': True, '$ne': None, '$ne': []}},
                    {'sections': {'$exists': True, '$ne': None, '$ne': []}}
                ]
            },
            {
                '$set': {
                    'unguided': False,
                    'updated_at': datetime.utcnow()
                }
            }
        )
        
        # Cards without guide sections - mark as unguided (unguided: true)
        unguided_result = cards_collection.update_many(
            {
                '$and': [
                    {
                        '$or': [
                            {'guide_sections': {'$exists': False}},
                            {'guide_sections': None},
                            {'guide_sections': []},
                            {'sections': {'$exists': False}},
                            {'sections': None},
                            {'sections': []}
                        ]
                    },
                    {'unguided': {'$ne': False}}  # Don't overwrite cards already marked as guided
                ]
            },
            {
                '$set': {
                    'unguided': True,
                    'updated_at': datetime.utcnow()
                }
            }
        )
        
        print(f"✅ Updated {guided_result.modified_count:,} cards as guided (unguided: false)")
        print(f"✅ Updated {unguided_result.modified_count:,} cards as unguided (unguided: true)")
        
        # Create index on unguided field for faster queries
        try:
            cards_collection.create_index('unguided')
            print("📊 Created index on 'unguided' field")
        except Exception as e:
            print(f"ℹ️  Index on 'unguided' field: {e}")
        
    except Exception as e:
        print(f"Error adding unguided flag: {e}")
    finally:
        client.close()

def set_commander_flags():
    """Set commander flags and priority levels"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🎯 Setting commander flags and priorities...")
        
        # Find and update all commanders
        cursor = cards_collection.find({})
        commander_count = 0
        updated_count = 0
        
        for card in cursor:
            if is_commander(card):
                commander_count += 1
                
                # Determine commander type
                type_line = card.get('type_line', '').lower()
                oracle_text = card.get('oracle_text', '').lower()
                
                if 'legendary' in type_line and 'creature' in type_line:
                    commander_type = 'legendary_creature'
                elif 'can be your commander' in oracle_text:
                    commander_type = 'explicit_commander'
                else:
                    commander_type = 'other'
                
                # Set priority based on EDHREC rank
                edhrec_rank = card.get('edhrec_rank')
                if edhrec_rank:
                    if edhrec_rank <= 100:
                        priority = 'critical'
                        priority_score = 10
                    elif edhrec_rank <= 500:
                        priority = 'high'
                        priority_score = 8
                    elif edhrec_rank <= 2000:
                        priority = 'medium'
                        priority_score = 6
                    else:
                        priority = 'normal'
                        priority_score = 4
                else:
                    priority = 'low'
                    priority_score = 2
                
                # Update the card
                update_result = cards_collection.update_one(
                    {'_id': card['_id']},
                    {
                        '$set': {
                            'is_commander': True,
                            'commander_type': commander_type,
                            'priority_level': priority,
                            'priority_score': priority_score,
                            'review_priority': 1,  # All commanders get top review priority
                            'updated_at': datetime.utcnow()
                        }
                    }
                )
                
                if update_result.modified_count > 0:
                    updated_count += 1
        
        print(f"✅ Updated {updated_count:,} commanders out of {commander_count:,} found")
        
        # Create indexes for efficient querying
        try:
            cards_collection.create_index('is_commander')
            cards_collection.create_index('priority_score')
            cards_collection.create_index([('is_commander', 1), ('unguided', 1), ('priority_score', -1)])
            print("📊 Created indexes for commander priority queries")
        except Exception as e:
            print(f"ℹ️  Indexes: {e}")
        
    except Exception as e:
        print(f"Error setting commander flags: {e}")
    finally:
        client.close()

def check_status():
    """Check the current status of commander flags and priorities"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("📊 Current Database Status:")
        
        # Total cards
        total_cards = cards_collection.count_documents({})
        print(f"  Total cards: {total_cards:,}")
        
        # Commander counts
        total_commanders = cards_collection.count_documents({'is_commander': True})
        print(f"  Total commanders: {total_commanders:,}")
        
        # Unguided status
        unguided_total = cards_collection.count_documents({'unguided': True})
        unguided_commanders = cards_collection.count_documents({'is_commander': True, 'unguided': True})
        
        print(f"  Unguided cards: {unguided_total:,}")
        print(f"  Unguided commanders: {unguided_commanders:,}")
        
        if total_commanders > 0:
            completion_rate = ((total_commanders - unguided_commanders) / total_commanders) * 100
            print(f"  Commander completion rate: {completion_rate:.1f}%")
        
        # Priority breakdown for commanders
        print(f"\n🎯 Commander Priority Breakdown:")
        priority_pipeline = [
            {'$match': {'is_commander': True}},
            {'$group': {
                '_id': '$priority_level',
                'count': {'$sum': 1},
                'unguided': {'$sum': {'$cond': [{'$eq': ['$unguided', True]}, 1, 0]}}
            }},
            {'$sort': {'_id': 1}}
        ]
        
        for priority in cards_collection.aggregate(priority_pipeline):
            level = priority['_id'] or 'unset'
            count = priority['count']
            unguided = priority['unguided']
            guided = count - unguided
            print(f"  {level.title():<10}: {count:>4} total | {guided:>3} guided | {unguided:>3} unguided")
        
        # Top unguided commanders
        print(f"\n🔥 Top 15 Unguided Commanders (by EDHREC rank):")
        top_unguided = cards_collection.find({
            'is_commander': True,
            'unguided': True,
            'edhrec_rank': {'$ne': None}
        }).sort('edhrec_rank', 1).limit(15)
        
        for i, cmd in enumerate(top_unguided, 1):
            colors = ''.join(cmd.get('color_identity', [])) if cmd.get('color_identity') else 'C'
            priority = cmd.get('priority_level', 'unset')
            print(f"  {i:2d}. {cmd['name']:<25} | Rank: {cmd['edhrec_rank']:>5} | {colors} | {priority}")
        
    except Exception as e:
        print(f"Error checking status: {e}")
    finally:
        client.close()

def main():
    parser = argparse.ArgumentParser(
        description='Update commander priorities in MTGAbyss database',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--set-flags', action='store_true',
                       help='Set commander flags and priority levels')
    parser.add_argument('--add-unguided-flag', action='store_true',
                       help='Add unguided flag to all cards')
    parser.add_argument('--check-status', action='store_true',
                       help='Check current status of flags and priorities')
    
    args = parser.parse_args()
    
    if not any([args.set_flags, args.add_unguided_flag, args.check_status]):
        parser.print_help()
        return 1
    
    if args.add_unguided_flag:
        add_unguided_flag()
    
    if args.set_flags:
        set_commander_flags()
    
    if args.check_status:
        check_status()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
