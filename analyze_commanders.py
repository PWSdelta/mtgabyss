#!/usr/bin/env python3
"""
Commander Analysis Script for MTGAbyss
=====================================

Identifies and analyzes all potential commanders from the Scryfall data to prioritize them for review.
This script helps ensure all commanders get reviewed first before other cards.

Usage:
  python analyze_commanders.py --analyze
  python analyze_commanders.py --mark-priority
  python analyze_commanders.py --stats
"""

import argparse
import json
import os
import sys
from typing import List, Dict, Set
from pymongo import MongoClient
from collections import defaultdict, Counter

# MongoDB configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'

def get_mongodb_client():
    """Get MongoDB client connection"""
    try:
        client = MongoClient(MONGODB_URI)
        # Test the connection
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None

def is_potential_commander(card: Dict) -> tuple[bool, str]:
    """
    Determine if a card can be a commander and why.
    Returns (is_commander, reason)
    """
    if not card:
        return False, "no card data"
    
    # Check if it's a legendary creature
    type_line = card.get('type_line', '').lower()
    if 'legendary' in type_line and 'creature' in type_line:
        return True, "legendary creature"
    
    # Check for planeswalkers that can be commanders (specific text)
    oracle_text = card.get('oracle_text', '').lower()
    if 'planeswalker' in type_line and 'can be your commander' in oracle_text:
        return True, "planeswalker commander"
    
    # Check for specific cards that can be commanders (like some artifacts)
    if 'can be your commander' in oracle_text:
        return True, "explicit commander ability"
    
    # Partner commanders (legendary creatures with partner)
    if 'legendary' in type_line and 'creature' in type_line and 'partner' in oracle_text:
        return True, "partner commander"
    
    return False, "not a commander"

def analyze_commanders():
    """Analyze all cards to identify commanders and their characteristics"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🔍 Analyzing cards to identify commanders...")
        
        # Get all cards
        total_cards = cards_collection.count_documents({})
        print(f"📊 Total cards in database: {total_cards:,}")
        
        commanders = []
        non_commanders = 0
        color_distribution = defaultdict(int)
        rarity_distribution = defaultdict(int)
        set_distribution = defaultdict(int)
        cmc_distribution = defaultdict(int)
        edhrec_ranks = []
        
        # Process all cards
        cursor = cards_collection.find({})
        processed = 0
        
        for card in cursor:
            processed += 1
            if processed % 5000 == 0:
                print(f"  Processed {processed:,} cards...")
            
            is_commander, reason = is_potential_commander(card)
            
            if is_commander:
                commander_data = {
                    'name': card.get('name'),
                    'uuid': card.get('uuid'),
                    'id': card.get('id'),  # Scryfall ID
                    'type_line': card.get('type_line'),
                    'mana_cost': card.get('mana_cost'),
                    'cmc': card.get('cmc', 0),
                    'colors': card.get('colors', []),
                    'color_identity': card.get('color_identity', []),
                    'rarity': card.get('rarity'),
                    'set': card.get('set'),
                    'set_name': card.get('set_name'),
                    'edhrec_rank': card.get('edhrec_rank'),
                    'prices': card.get('prices', {}),
                    'reason': reason,
                    'oracle_text': card.get('oracle_text', '')[:200] + '...' if len(card.get('oracle_text', '')) > 200 else card.get('oracle_text', ''),
                    'has_guide': bool(card.get('guide_sections')) if card.get('guide_sections') else False,
                    'unguided': card.get('unguided', True)  # Default to unguided if not set
                }
                commanders.append(commander_data)
                
                # Track statistics
                color_count = len(card.get('color_identity', []))
                color_key = f"{color_count} colors" if color_count > 0 else "colorless"
                color_distribution[color_key] += 1
                
                rarity_distribution[card.get('rarity', 'unknown')] += 1
                set_distribution[card.get('set', 'unknown')] += 1
                cmc_distribution[card.get('cmc', 0)] += 1
                
                if card.get('edhrec_rank'):
                    edhrec_ranks.append(card.get('edhrec_rank'))
            else:
                non_commanders += 1
        
        print(f"\n📊 Commander Analysis Results:")
        print(f"  Total commanders found: {len(commanders):,}")
        print(f"  Non-commander cards: {non_commanders:,}")
        print(f"  Commander percentage: {len(commanders)/total_cards*100:.1f}%")
        
        # Sort commanders by EDHREC rank (popularity)
        commanders_with_rank = [c for c in commanders if c['edhrec_rank']]
        commanders_without_rank = [c for c in commanders if not c['edhrec_rank']]
        
        commanders_with_rank.sort(key=lambda x: x['edhrec_rank'])
        
        print(f"\n🏆 Top 20 Most Popular Commanders (by EDHREC rank):")
        for i, cmd in enumerate(commanders_with_rank[:20], 1):
            guide_status = "✅" if not cmd['unguided'] else "❌"
            colors = ''.join(cmd['color_identity']) if cmd['color_identity'] else 'C'
            print(f"  {i:2d}. {cmd['name']:<30} | Rank: {cmd['edhrec_rank']:>5} | {colors} | {guide_status}")
        
        print(f"\n📈 Statistics:")
        print(f"  Commanders with EDHREC rank: {len(commanders_with_rank):,}")
        print(f"  Commanders without EDHREC rank: {len(commanders_without_rank):,}")
        
        if edhrec_ranks:
            print(f"  EDHREC rank range: {min(edhrec_ranks)} - {max(edhrec_ranks):,}")
            print(f"  Average EDHREC rank: {sum(edhrec_ranks)/len(edhrec_ranks):.0f}")
        
        print(f"\n🎨 Color Distribution:")
        for color, count in sorted(color_distribution.items()):
            percentage = count/len(commanders)*100
            print(f"  {color:<12}: {count:>4} ({percentage:>5.1f}%)")
        
        print(f"\n💎 Rarity Distribution:")
        for rarity, count in sorted(rarity_distribution.items(), key=lambda x: x[1], reverse=True):
            percentage = count/len(commanders)*100
            print(f"  {rarity.title():<12}: {count:>4} ({percentage:>5.1f}%)")
        
        print(f"\n🎯 Guide Status:")
        guided_count = len([c for c in commanders if not c['unguided']])
        unguided_count = len([c for c in commanders if c['unguided']])
        print(f"  Guided commanders: {guided_count:,}")
        print(f"  Unguided commanders: {unguided_count:,}")
        print(f"  Completion rate: {guided_count/len(commanders)*100:.1f}%")
        
        # Save results to file
        output_file = '/tmp/commanders_analysis.json'
        with open(output_file, 'w') as f:
            json.dump({
                'total_commanders': len(commanders),
                'commanders_with_rank': commanders_with_rank,
                'commanders_without_rank': commanders_without_rank,
                'statistics': {
                    'color_distribution': dict(color_distribution),
                    'rarity_distribution': dict(rarity_distribution),
                    'set_distribution': dict(set_distribution),
                    'cmc_distribution': dict(cmc_distribution),
                    'guided_count': guided_count,
                    'unguided_count': unguided_count
                }
            }, f, indent=2, default=str)
        
        print(f"\n💾 Full analysis saved to: {output_file}")
        
    except Exception as e:
        print(f"Error during analysis: {e}")
    finally:
        client.close()

def mark_commanders_priority():
    """Mark all commanders with high priority for guide generation"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("🎯 Marking commanders as high priority...")
        
        # Find all commanders and mark them
        cursor = cards_collection.find({})
        commander_count = 0
        updated_count = 0
        
        for card in cursor:
            is_commander, reason = is_potential_commander(card)
            
            if is_commander:
                commander_count += 1
                
                # Update the card with commander priority
                update_result = cards_collection.update_one(
                    {'_id': card['_id']},
                    {
                        '$set': {
                            'is_commander': True,
                            'commander_reason': reason,
                            'priority_level': 'high',
                            'review_priority': 1  # Highest priority
                        }
                    }
                )
                
                if update_result.modified_count > 0:
                    updated_count += 1
        
        print(f"✅ Marked {updated_count:,} commanders out of {commander_count:,} found")
        
    except Exception as e:
        print(f"Error marking commanders: {e}")
    finally:
        client.close()

def show_commander_stats():
    """Show quick stats about commanders in the database"""
    client = get_mongodb_client()
    if not client:
        return
    
    try:
        db = client[DATABASE_NAME]
        cards_collection = db[CARDS_COLLECTION]
        
        print("📊 Quick Commander Stats:")
        
        # Count commanders
        commander_pipeline = [
            {
                '$match': {
                    '$or': [
                        {'type_line': {'$regex': 'legendary.*creature', '$options': 'i'}},
                        {'oracle_text': {'$regex': 'can be your commander', '$options': 'i'}}
                    ]
                }
            },
            {
                '$group': {
                    '_id': None,
                    'total': {'$sum': 1},
                    'with_edhrec': {'$sum': {'$cond': [{'$ne': ['$edhrec_rank', None]}, 1, 0]}},
                    'unguided': {'$sum': {'$cond': [{'$eq': ['$unguided', True]}, 1, 0]}},
                    'avg_edhrec': {'$avg': '$edhrec_rank'}
                }
            }
        ]
        
        result = list(cards_collection.aggregate(commander_pipeline))
        
        if result:
            stats = result[0]
            total = stats['total']
            with_edhrec = stats['with_edhrec']
            unguided = stats['unguided']
            avg_edhrec = stats.get('avg_edhrec', 0)
            
            print(f"  Total commanders: {total:,}")
            print(f"  With EDHREC rank: {with_edhrec:,} ({with_edhrec/total*100:.1f}%)")
            print(f"  Unguided: {unguided:,} ({unguided/total*100:.1f}%)")
            print(f"  Average EDHREC rank: {avg_edhrec:.0f}" if avg_edhrec else "  Average EDHREC rank: N/A")
            
            # Top unguided commanders by popularity
            print(f"\n🎯 Top 10 Unguided Commanders (by EDHREC popularity):")
            top_unguided = cards_collection.find({
                'type_line': {'$regex': 'legendary.*creature', '$options': 'i'},
                'unguided': True,
                'edhrec_rank': {'$ne': None}
            }).sort('edhrec_rank', 1).limit(10)
            
            for i, cmd in enumerate(top_unguided, 1):
                colors = ''.join(cmd.get('color_identity', [])) if cmd.get('color_identity') else 'C'
                print(f"  {i:2d}. {cmd['name']:<25} | Rank: {cmd['edhrec_rank']:>5} | {colors}")
        
    except Exception as e:
        print(f"Error getting stats: {e}")
    finally:
        client.close()

def main():
    parser = argparse.ArgumentParser(
        description='Analyze commanders in the MTGAbyss database',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--analyze', action='store_true', 
                       help='Full analysis of all commanders')
    parser.add_argument('--mark-priority', action='store_true',
                       help='Mark all commanders as high priority')
    parser.add_argument('--stats', action='store_true',
                       help='Show quick commander statistics')
    
    args = parser.parse_args()
    
    if not any([args.analyze, args.mark_priority, args.stats]):
        parser.print_help()
        return 1
    
    if args.analyze:
        analyze_commanders()
    
    if args.mark_priority:
        mark_commanders_priority()
    
    if args.stats:
        show_commander_stats()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
