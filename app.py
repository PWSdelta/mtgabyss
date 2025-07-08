from flask import Flask, render_template, jsonify, request, redirect, url_for, Response, abort
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
import os
import logging
import markdown
import re
import math
import random
from time import time

# Configure beautiful logging with elapsed time tracking
import time as time_module
_start_time = time_module.time()

def elapsed_time():
    """Get elapsed time since startup in a readable format"""
    elapsed = time_module.time() - _start_time
    if elapsed < 60:
        return f"+{elapsed:.1f}s"
    elif elapsed < 3600:
        return f"+{elapsed/60:.1f}m"
    else:
        return f"+{elapsed/3600:.1f}h"

# Only configure logging once to prevent duplicate logs
if not logging.getLogger('MTGAbyss').handlers:
    # Set up colored logging for console (if available)
    try:
        import colorlog
        console_handler = colorlog.StreamHandler()
        console_handler.setFormatter(colorlog.ColoredFormatter(
            '%(log_color)s%(levelname)-8s | %(name)-15s | %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red',
            }
        ))
        
        # Configure the MTGAbyss logger specifically
        logger = logging.getLogger('MTGAbyss')
        logger.setLevel(logging.INFO)
        logger.handlers.clear()  # Clear any existing handlers
        logger.addHandler(console_handler)
        logger.propagate = False  # Don't propagate to root logger
        logger.info("🎨 Colored logging enabled")
    except ImportError:
        # Fallback to basic logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(levelname)-8s | %(name)-15s | %(message)s'
        )
        logger = logging.getLogger('MTGAbyss')
        logger.info("📝 Standard logging active (install colorlog for colors)")
else:
    # Logger already configured, just get it
    logger = logging.getLogger('MTGAbyss')

def log_card_action(action, card_name, uuid, extra_info=""):
    """Helper for consistent card-related logging"""
    card_display = f"'{card_name}' ({uuid[:8]}...)" if card_name else f"({uuid[:8]}...)"
    if extra_info:
        logger.info(f"🃏 {action}: {card_display} | {extra_info} | {elapsed_time()}")
    else:
        logger.info(f"🃏 {action}: {card_display} | {elapsed_time()}")

def log_worker_action(worker_type, action, details=""):
    """Helper for worker-related logging"""
    if details:
        logger.info(f"⚙️  [{worker_type.upper()}] {action} | {details} | {elapsed_time()}")
    else:
        logger.info(f"⚙️  [{worker_type.upper()}] {action} | {elapsed_time()}")

def log_api_stats(endpoint, status, details=""):
    """Helper for API endpoint logging"""
    status_emoji = "✅" if status == "success" else "❌" if status == "error" else "⚠️"
    if details:
        logger.info(f"{status_emoji} API {endpoint} → {status.upper()} | {details} | {elapsed_time()}")
    else:
        logger.info(f"{status_emoji} API {endpoint} → {status.upper()} | {elapsed_time()}")

def log_operation_timing(operation_name, start_time):
    """Helper for logging operation duration"""
    duration = time_module.time() - start_time
    if duration < 1:
        duration_str = f"{duration*1000:.0f}ms"
    else:
        duration_str = f"{duration:.1f}s"
    logger.info(f"⏱️  {operation_name} completed in {duration_str} | {elapsed_time()}")

# Helper to bump a card's priority for regeneration (used by both API and UI)
def slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')
# Web routes



# Environment variables
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')



app = Flask(__name__)
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client.mtgabyss
cards = db.cards
mentions_histogram = db.mentions_histogram  # UUID-based mention tracking: { uuid: count }
priority_regen_queue = db.priority_regen_queue  # Simple priority queue for /regen requests
decks = db.decks  # Add deck collection

# Simple worker position tracking to prevent same-card pulls
_worker_positions = {
    'full-guide': 0,
    'half-guide': 10  # Start with offset
}

# Prime numbers for jiggling queue positions
_prime_offsets = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


# Helper to bump a card's priority for regeneration (used by both API and UI)
def bump_card_priority(uuid):
    """Set a card's mention_count very high to prioritize for regeneration."""
    mentions_histogram.update_one(
        {'uuid': uuid},
        {'$set': {'mention_count': 9999, 'last_mentioned': datetime.now()}},
        upsert=True
    )

# Helper function to add cards to the unified priority queue
def add_to_priority_queue(uuid, reason='manual'):
    """Add a card to the unified priority queue for processing"""
    try:
        # Check if card exists
        card = cards.find_one({'uuid': uuid}, {'uuid': 1, 'name': 1, 'edhrec_rank': 1, 'set': 1})
        if not card:
            return False
        card_name = card.get('name', 'Unknown')
        # Check if this card name is already in queue (any printing)
        existing = priority_regen_queue.find_one({'name': card_name, 'processed': False})
        if existing:
            logger.debug(f"🔁 Skipping {card_name} - already in queue as {existing['uuid'][:8]}...")
            return False
        # Add to priority queue (or update if already exists)
        priority_regen_queue.update_one(
            {'uuid': uuid},
            {
                '$set': {
                    'uuid': uuid,
                    'name': card_name,
                    'reason': reason,
                    'added_at': datetime.now(),
                    'processed': False
                }
            },
            upsert=True
        )
        # Also add to to_regen collection
        to_regen_collection = db.to_regenerate
        to_regen_collection.update_one(
            {'uuid': uuid},
            {
                '$set': {
                    'uuid': uuid,
                    'name': card_name,
                    'edhrec_rank': card.get('edhrec_rank'),
                    'set': card.get('set'),
                    'submitted_at': datetime.now(),
                    'reason': reason
                }
            },
            upsert=True
        )
        logger.info(f"🎯 Added to priority queue and to_regenerate: '{card_name}' ({uuid[:8]}...) | reason: {reason}")
        return True
    except Exception as e:
        logger.error(f"❌ Error adding {uuid} to priority queue: {e}")
        return False

# Hidden route to force-queue a card for regeneration
@app.route('/card/<uuid>/regen', methods=['POST', 'GET'])
def regen_card(uuid):
    """Hidden endpoint to add a card to the unified priority queue for regeneration."""
    add_to_priority_queue(uuid, reason='manual_regen')
    return redirect(url_for('card_detail', uuid=uuid))

# Ensure indexes for fast queries
try:
    cards.create_index('has_analysis')
    cards.create_index('uuid', unique=True)
    # UUID-based histogram indexes
    mentions_histogram.create_index('uuid', unique=True)
    mentions_histogram.create_index([('mention_count', -1), ('last_mentioned', -1)])  # For fast high-count lookups
    # EDHREC indexes for popularity-based prioritization
    cards.create_index('edhrec_rank')  # Lower rank = more popular
    cards.create_index('edhrec_popularity')  # Higher popularity = more popular
    # Priority queue indexes
    priority_regen_queue.create_index('uuid', unique=True)
    priority_regen_queue.create_index('processed')
    priority_regen_queue.create_index('added_at')
    logger.info("📊 Database indexes created successfully")
except Exception as e:
    logger.error(f"❌ Could not create MongoDB indexes: {e}")

# Function to refresh the priority queue with EDHREC-based cards
def refresh_priority_queue(limit=100):
    """Populate the priority queue with top EDHREC cards that need work (deduplicated by card name)"""
    try:
        # First, pull all cards from the priority_cards collection (manual/prio list)
        priority_collection = db.priority_cards
        prio_cards = list(priority_collection.find({'processed': False}))
        prio_names = set()
        added_count = 0
        skipped_count = 0
        # Insert prio cards first (if not already in regen queue)
        for prio in prio_cards:
            prio_name = prio.get('name')
            prio_names.add(prio_name)
            existing = priority_regen_queue.find_one({'name': prio_name, 'processed': False})
            if not existing:
                if add_to_priority_queue(prio['uuid'], reason='priority_list'):
                    added_count += 1
            else:
                skipped_count += 1


        # Now fill with all cards that need work (sections < 12), skipping any already in prio_names
        pipeline = [
            {'$match': {
                'edhrec_rank': {'$exists': True, '$ne': None},
                '$expr': {
                    '$lt': [
                        {'$size': {'$objectToArray': {'$ifNull': ['$analysis.sections', {}]}}},
                        12
                    ]
                }
            }},
            {'$sort': {'edhrec_rank': 1, 'released_at': 1}},
            {'$group': {'_id': '$name', 'best_printing': {'$first': '$$ROOT'}}},
            {'$sort': {'best_printing.edhrec_rank': 1}},
            {'$limit': limit},
            {'$replaceRoot': {'newRoot': '$best_printing'}}
        ]
        cards_needing_work = list(cards.aggregate(pipeline))
        for card in cards_needing_work:
            if card['name'] in prio_names:
                skipped_count += 1
                continue
            existing = priority_regen_queue.find_one({'name': card['name'], 'processed': False})
            if not existing:
                if add_to_priority_queue(card['uuid'], reason='needs_work'):
                    added_count += 1
            else:
                skipped_count += 1

        logger.info(f"🔄 Queue refresh: added {added_count} unique cards (prio+edhrec), skipped {skipped_count} already queued | total queue size: {priority_regen_queue.count_documents({'processed': False})}")
        return added_count
    except Exception as e:
        logger.error(f"❌ Error refreshing priority queue: {e}")
        return 0


# --- REMOVE DUPLICATE PRINTINGS FROM CARDS AND PRIORITY QUEUE ON STARTUP ---
def delete_duplicate_cards_and_queue():
    """Remove all but the oldest printing of each card name from the main cards collection and the priority queue."""

    # Remove all but the oldest printing of each card name from the main cards collection
    pipeline = [
        {"$sort": {"name": 1, "released_at": 1}},
        {"$group": {
            "_id": "$name",
            "uuids": {"$push": "$uuid"},
            "ids": {"$push": "$_id"},
            "count": {"$sum": 1}
        }}
    ]
    duplicates = list(cards.aggregate(pipeline))
    removed = 0
    for group in duplicates:
        if group["count"] > 1:
            # Keep the first (oldest by released_at), remove the rest
            to_remove_uuids = group["uuids"][1:]
            to_remove_ids = group["ids"][1:]
            if to_remove_uuids:
                result = cards.delete_many({"uuid": {"$in": to_remove_uuids}})
                removed += result.deleted_count
    if removed:
        logger.info(f"🗑️ Removed {removed} duplicate printings from cards collection (kept only oldest printing per name)")

    # Remove duplicate printings from priority queue (group by name, order by submitted_at or priority_order, keep oldest)
    priority_collection = db.priority_cards
    pipeline = [
        {"$sort": {"name": 1, "submitted_at": 1, "priority_order": 1}},
        {"$group": {
            "_id": "$name",
            "all": {"$push": {"_id": "$_id", "submitted_at": "$submitted_at", "priority_order": "$priority_order"}},
            "count": {"$sum": 1}
        }},
        {"$match": {"count": {"$gt": 1}}}
    ]
    dupes = list(priority_collection.aggregate(pipeline))
    removed_queue = 0
    for group in dupes:
        # Keep the first (oldest by submitted_at or priority_order), remove the rest
        to_remove = [doc["_id"] for doc in group["all"][1:]]
        if to_remove:
            result = priority_collection.delete_many({"_id": {"$in": to_remove}})
            removed_queue += result.deleted_count
    if removed_queue:
        logger.info(f"🗑️ Removed {removed_queue} duplicate printings from priority queue")

delete_duplicate_cards_and_queue()

# Log startup information - count cards by guide completion level
half_guides = cards.count_documents({
    'analysis.sections': {'$exists': True},
    '$expr': {
        '$gte': [
            {'$size': {'$objectToArray': '$analysis.sections'}},
            6  # Half guides have 6+ sections
        ]
    }
})
full_guides = cards.count_documents({
    'analysis.sections': {'$exists': True},
    '$expr': {
        '$gte': [
            {'$size': {'$objectToArray': '$analysis.sections'}},
            12  # Full guides have 12+ sections
        ]
    }
})
logger.info(f"🚀 MTGAbyss backend starting | {half_guides:,} half guides (6+ sections) | {full_guides:,} full guides (12+ sections)")


# --- Priority queue will be managed externally - no auto-initialization ---
# Workers will pull from existing priority_cards collection or fall back to EDHREC-based assignment

# Create Markdown instance with desired extensions
md = markdown.Markdown(extensions=['extra', 'codehilite', 'tables'])

@app.template_filter('markdown')
def markdown_filter(text):
    if not text:
        return ''
    return md.convert(text)

@app.template_filter('number_format')
def number_format_filter(value):
    """Format numbers with commas for thousands separators"""
    if value is None:
        return '0'
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)

@app.template_filter('link_card_mentions')
def link_card_mentions(text, current_card_name=None):
    if not text:
        return ''

    # Only replace [b]...[/b] if [b] is not immediately followed by a letter (to avoid breaking words like Builder's)
    text = re.sub(r'(?<!\w)\[b\](.+?)\[/b\]', r'<strong>\1</strong>', text, flags=re.IGNORECASE|re.DOTALL)

    # Also convert {{Card Name}} to [Card Name] for linking
    text = re.sub(r'\{\{([^}]+)\}\}', r'[\1]', text)

    # No per-request cache for card name lookups (removed)

    def card_link_replacer(match):
        card_name = match.group(1)
        if current_card_name and card_name.strip().lower() == current_card_name.strip().lower():
            return card_name
        # Try to find the card by name (case-insensitive, exact match)
        card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {
            'uuid': 1, 'image_uris': 1, 'imageUris': 1, 'card_faces': 1
        })
        if not card or 'uuid' not in card:
            # Try partial match if no exact match
            card = cards.find_one({'name': {'$regex': re.escape(card_name), '$options': 'i'}}, {
                'uuid': 1, 'image_uris': 1, 'imageUris': 1, 'card_faces': 1
            })
        if card and 'uuid' in card:
            uuid = card['uuid']
            image_url = get_card_image_uri(card, 'normal')
        else:
            uuid = None
            image_url = None
        if uuid:
            url = url_for('card_detail', uuid=uuid)
            if image_url:
                # Add Bootstrap popover attributes for image preview
                return (
                    f'<a href="{url}" class="card-mention-popover" '
                    f'data-bs-toggle="popover" data-bs-trigger="hover focus" data-bs-placement="top" '
                    f'data-bs-html="true" data-bs-content="<img src=\'{image_url}\' style=\'max-width:110px;max-height:160px;\'/>">{card_name}</a>'
                )
            else:
                return f'<a href="{url}">{card_name}</a>'
        else:
            url = url_for('search', q=card_name)
            return f'<a href="{url}">{card_name}</a>'

    # Replace [[Card Name]] and [Card Name] (but not [B] or [/B])
    text = re.sub(r'\[\[(.+?)\]\]', card_link_replacer, text)
    text = re.sub(r'\[(?!/?B\])(.*?)\]', card_link_replacer, text)
    # Add Bootstrap popover JS only once per request (idempotent)
    if '<script id="card-mention-popover-js">' not in text:
        popover_js = '''<script id="card-mention-popover-js">
        document.addEventListener('DOMContentLoaded', function() {
          var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
          popoverTriggerList.forEach(function (popoverTriggerEl) {
            new bootstrap.Popover(popoverTriggerEl);
          });
        });
        </script>'''
        text += popover_js
    return text


# Web routes
@app.route('/')
def search():
    """Card search page"""
    query = request.args.get('q', '')
    if query:
        # Get and sort in Python to avoid MongoDB sort on string/NaN values
        results = list(cards.find({
            'name': {'$regex': query, '$options': 'i'},
            'status': 'public',  # Show all published guides (lite or full)
        }).limit(30))
        import math
        def price_usd(card):
            val = card.get('prices', {}).get('usd')
            try:
                fval = float(val)
                if math.isnan(fval) or math.isinf(fval):
                    return 0
                return fval
            except Exception:
                return 0
        results = sorted(results, key=price_usd, reverse=True)[:30]
    else:
        # Get 30 random English cards with full content and normal image, then sort by prices.usd descending
        results = list(cards.aggregate([
            {'$match': {'status': 'public'}},  # Show all published guides (lite or full)
            {'$sample': {'size': 30}}
        ]))
        import math
        def price_usd(card):
            val = card.get('prices', {}).get('usd')
            try:
                fval = float(val)
                if math.isnan(fval) or math.isinf(fval):
                    return 0
                return fval
            except Exception:
                return 0
        results = sorted(results, key=price_usd, reverse=True)[:30]
    return render_template('search.html', cards=results, query=query)

@app.route('/card/<uuid>')
def card_detail(uuid):
    """Card detail page"""
    card = cards.find_one({'uuid': uuid})
    if card and 'category' not in card:
        card['category'] = 'mtg'
    # Get 5 most recent analyzed cards (excluding this one)
    recent_cards = list(cards.find(
        {'status': 'public', 'uuid': {'$ne': uuid}}  # Only cards with published guides
    ).sort([('analysis.analyzed_at', -1)]).limit(5))
    # Get 6 random cards with full content and image, not this one, for recommendations
    rec_cards = list(cards.aggregate([
        {'$match': {
            'status': 'public',  # Only cards with published guides
            'image_uris.normal': {'$exists': True},
            'uuid': {'$ne': uuid}
        }},
        {'$sample': {'size': 6}}
    ]))

    # --- Cards Mentioned in This Review ---
    mentioned_cards = []
    if card and card.get('analysis'):
        mention_names = extract_mentions_from_guide(card['analysis'])
        if mention_names:
            # Only include cards with full content analysis
            found_cards = list(cards.find({
                'name': {'$in': mention_names},
                'status': 'public'  # Only cards with published guides
            }, {'uuid': 1, 'name': 1, 'image_uris.normal': 1, 'prices': 1}))
            # Unique by name, pick highest price (world avg) per name
            card_by_name = {}
            for c in found_cards:
                name = c.get('name')
                usd = float(c.get('prices', {}).get('usd') or 0)
                eur = float(c.get('prices', {}).get('eur') or 0)
                if usd and eur:
                    avg = (usd + eur) / 2
                elif usd:
                    avg = usd
                elif eur:
                    avg = eur
                else:
                    avg = 0
                c['_world_avg'] = avg
                if name not in card_by_name or avg > card_by_name[name]['_world_avg']:
                    card_by_name[name] = c
            # Sort by price descending, limit to 6
            mentioned_cards = sorted(card_by_name.values(), key=lambda x: x['_world_avg'], reverse=True)[:6]

    # --- Most Expensive Cards (with full content analysis) ---
    pipeline = [
        {'$match': {
            'has_full_content': True,  # Only cards with complete analysis
            '$or': [
                {'prices.usd': {'$type': 'string', '$ne': ''}},
                {'prices.eur': {'$type': 'string', '$ne': ''}}
            ]
        }},
        {'$addFields': {
            'world_avg': {
                '$cond': [
                    {'$and': [
                        {'$ifNull': ['$prices.usd', False]},
                        {'$ifNull': ['$prices.eur', False]}
                    ]},
                    {'$divide': [
                        {'$add': [
                            {'$toDouble': '$prices.usd'},
                            {'$toDouble': '$prices.eur'}
                        ]}, 2
                    ]},
                    {'$cond': [
                        {'$ifNull': ['$prices.usd', False]},
                        {'$toDouble': '$prices.usd'},
                        {'$toDouble': '$prices.eur'}
                    ]}
                ]
            }
        }},
        {'$sort': {'world_avg': -1}},
        {'$limit': 6},
        {'$project': {'uuid': 1, 'name': 1, 'imageUris.normal': 1, 'prices': 1}}
    ]
    expensive_cards = list(cards.aggregate(pipeline))
    
    # Get guide information for template (backward compatible)
    guide_sections = None
    guide_content = None
    guide_meta = None
    native_guide_sections = None
    native_guide_content = None
    native_guide_meta = None
    
    if card and card.get('analysis'):
        # English guide
        guide_sections, guide_content, guide_meta = get_guide_content(card['analysis'], 'en')
        
        # Native language guide (if available)
        card_lang = card.get('lang', 'en')
        if card_lang != 'en':
            native_guide_sections, native_guide_content, native_guide_meta = get_guide_content(card['analysis'], card_lang)

    return render_template(
        'card.html',
        card=card,
        current_card_name=card['name'] if card else None,
        recent_cards=recent_cards,
        rec_cards=rec_cards,
        mentioned_cards=mentioned_cards,
        expensive_cards=expensive_cards,
        guide_sections=guide_sections,
        guide_content=guide_content,
        guide_meta=guide_meta,
        native_guide_sections=native_guide_sections,
        native_guide_content=native_guide_content,
        native_guide_meta=native_guide_meta
    )

@app.route('/gallery')
def gallery():
    """Scrolling gallery page"""
    # Show only cards with full content analysis and art_crop images
    reviewed_cards = cards.find({
        'status': 'public'
    }).limit(60)
    return render_template('gallery.html', cards=reviewed_cards)

@app.route('/random')
def random_card_redirect():
    # Only pick from cards with full content analysis
    cursor = cards.aggregate([
        {'$match': {'status': 'public'}},
        {'$sample': {'size': 1}}
    ])
    card = next(cursor, None)
    if not card:
        return "No cards with full content found", 404
    return redirect(f"/card/{card['uuid']}")

# --- ARTIST ROUTES ---
@app.route('/artist/<slug>')
def artist_detail(slug):
    # Find all cards by this artist more efficiently
    artist_cards = list(cards.find({
        'artist': {'$exists': True, '$ne': ''}
    }, {
        '_id': 0,  # Exclude ObjectId to prevent serialization issues
        'uuid': 1, 'name': 1, 'artist': 1, 'set': 1, 'set_name': 1, 
        'rarity': 1, 'image_uris': 1, 'imageUris': 1, 'analysis': 1,
        'cmc': 1, 'colors': 1, 'type_line': 1, 'released_at': 1
    }))
    
    # Filter by slugified artist name
    artist_cards = [card for card in artist_cards if slugify(card.get('artist', '')) == slug]
    if not artist_cards:
        abort(404)
    
    artist_name = artist_cards[0].get('artist', slug)
    
    # Sort cards by release date (newest first)
    artist_cards.sort(key=lambda x: x.get('released_at', '1900-01-01'), reverse=True)
    
    # Cards with guides (has at least one guide section)
    cards_with_guides = [card for card in artist_cards if card.get('analysis') and (card['analysis'].get('sections') or card['analysis'].get('content'))]
    
    # Group cards by set for better organization
    sets_dict = {}
    for card in artist_cards:
        set_name = card.get('set_name', card.get('set', 'Unknown Set'))
        if set_name not in sets_dict:
            sets_dict[set_name] = []
        sets_dict[set_name].append(card)
    
    # Sort sets by most recent card in each set
    sorted_sets = sorted(sets_dict.items(), key=lambda x: max(card.get('released_at', '1900-01-01') for card in x[1]), reverse=True)
    
    # Get stats
    total_cards = len(artist_cards)
    cards_with_analysis = len(cards_with_guides)
    unique_sets = len(sets_dict)
    
    return render_template(
        'artist.html',
        artist_name=artist_name,
        artist_slug=slug,
        cards_with_guides=cards_with_guides,
        all_cards=artist_cards,
        sorted_sets=sorted_sets,
        stats={
            'total_cards': total_cards,
            'cards_with_analysis': cards_with_analysis,
            'unique_sets': unique_sets
        }
    )

@app.route('/artists')
def artist_index():
    # Get all unique artists with aggregation for better performance
    pipeline = [
        {'$match': {'artist': {'$exists': True, '$ne': ''}}},
        {'$group': {
            '_id': '$artist',
            'card_count': {'$sum': 1},
            'analyzed_count': {'$sum': {'$cond': [{'$ifNull': ['$analysis', False]}, 1, 0]}},
            'sample_card': {'$first': {'uuid': '$uuid', 'name': '$name', 'image_uris': '$image_uris', 'imageUris': '$imageUris'}}
        }},
        {'$sort': {'card_count': -1}}
    ]
    
    artist_data = list(cards.aggregate(pipeline))
    
    # Build enhanced artist list
    artist_list = []
    for artist_doc in artist_data:
        artist_name = artist_doc['_id']
        slug = slugify(artist_name)
        sample_card = artist_doc['sample_card']
        
        # Clean sample card data to remove any ObjectId references
        if sample_card:
            sample_card = {
                'uuid': sample_card.get('uuid'),
                'name': sample_card.get('name'),
                'image_uris': sample_card.get('image_uris'),
                'imageUris': sample_card.get('imageUris')
            }
        
        artist_list.append({
            'name': artist_name,
            'slug': slug,
            'count': artist_doc['card_count'],
            'analyzed_count': artist_doc['analyzed_count'],
            'sample_card': sample_card
        })
    
    return render_template('artist_index.html', artists=artist_list)

# Deck routes
@app.route('/decks')
def deck_index():
    """Display a paginated index of Commander decks (99+ cards)"""
    try:
        page = int(request.args.get('page', 1))
        per_page = 20
        skip = (page - 1) * per_page
        
        # Filter for Commander decks only
        filter_query = {'format': 'Commander Deck'}
        
        # Get total count of Commander decks
        total_decks = decks.count_documents(filter_query)
        
        # Get decks for current page
        deck_list = list(decks.find(filter_query, {
            '_id': 1,  # Keep _id for the deck detail links
            'name': 1,
            'commander': 1,
            'format': 1,
            'total_cards': 1,
            'colors': 1,
            'date_added': 1
        }).sort('date_added', -1).skip(skip).limit(per_page))
        
        # Convert ObjectIds to strings for template compatibility
        for deck in deck_list:
            if '_id' in deck:
                deck['_id'] = str(deck['_id'])
        
        # Calculate pagination info
        total_pages = math.ceil(total_decks / per_page) if total_decks > 0 else 1
        has_prev = page > 1
        has_next = page < total_pages
        
        return render_template('deck_index.html',
                             decks=deck_list,
                             page=page,
                             total_pages=total_pages,
                             has_prev=has_prev,
                             has_next=has_next,
                             total_decks=total_decks,
                             per_page=per_page)
    except Exception as e:
        logger.error(f"Error in deck_index: {e}")
        return f"Error loading decks: {e}", 500

@app.route('/deck/<deck_id>')
def deck_detail(deck_id):
    """Display detailed view of a specific deck"""
    try:
        # Get deck from database
        deck = decks.find_one({'_id': ObjectId(deck_id)})
        if not deck:
            abort(404)
        
        # Convert ObjectId to string for template compatibility
        if '_id' in deck:
            deck['_id'] = str(deck['_id'])
        
        # Get full card data for each card in the deck
        deck_cards = []
        for card_entry in deck.get('cards', []):
            card_name = card_entry.get('name')
            quantity = card_entry.get('quantity', 1)
            
            # Find card in database (exclude _id to avoid ObjectId issues)
            card = cards.find_one({'name': card_name}, {'_id': 0})
            if card:
                deck_cards.append({
                    'card': card,
                    'quantity': quantity
                })
        
        return render_template('deck.html',
                             deck=deck,
                             deck_cards=deck_cards)
    except Exception as e:
        logger.error(f"Error in deck_detail: {e}")
        return f"Error loading deck: {e}", 500

@app.route('/api/generate_deck_review/<deck_id>', methods=['POST'])
def generate_deck_review(deck_id):
    """Generate an AI review for a deck"""
    try:
        # Get deck from database
        deck = decks.find_one({'_id': ObjectId(deck_id)})
        if not deck:
            return jsonify({'error': 'Deck not found'}), 404
        
        # Simple AI review (placeholder - you can enhance this)
        review = f"This {deck.get('format', 'unknown format')} deck '{deck.get('name', 'Unnamed')}' "
        
        if deck.get('commander'):
            review += f"is built around the commander {deck['commander']}. "
        
        total_cards = deck.get('total_cards', 0)
        review += f"It contains {total_cards} cards total. "
        
        colors = deck.get('colors', [])
        if colors:
            review += f"The deck runs {', '.join(colors)} colors. "
        
        review += "This appears to be a well-constructed deck with good synergy between its components."
        
        return jsonify({'review': review})
    except Exception as e:
        logger.error(f"Error generating deck review: {e}")
        return jsonify({'error': str(e)}), 500

# Worker API endpoints
@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get processing statistics for workers (English cards only)"""
    try:
        # Only count English cards to avoid duplicate language versions
        total_cards = cards.count_documents({'lang': 'en'})
        # Count cards with full content analysis
        reviewed_cards = cards.count_documents({'lang': 'en', 'has_full_content': True})
        # Also count legacy has_analysis for comparison
        legacy_reviewed = cards.count_documents({'lang': 'en', 'has_analysis': True})
        unreviewed_cards = total_cards - reviewed_cards
        
        return jsonify({
            'status': 'success',
            'stats': {
                'total_cards': total_cards,
                'reviewed_cards': reviewed_cards,
                'legacy_reviewed_cards': legacy_reviewed,  # For comparison/migration tracking
                'unreviewed_cards': unreviewed_cards,
                'completion_percentage': round((reviewed_cards / total_cards * 100), 2) if total_cards > 0 else 0            
            }
        })
        
    except Exception as e:
        log_api_stats("stats", "error", str(e))
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500


@app.route('/api/get_random_unreviewed', methods=['GET'])
def get_random_unreviewed():
    """Get the most popular EDHREC card that needs work (< 6 sections)"""
    try:
        limit = int(request.args.get('limit', 1))
        mode = request.args.get('mode', 'full-guide')  # Keep for compatibility but treat both the same
        
        # Simple: Get most popular EDHREC-ranked card that has < 6 sections
        pipeline = [
            {'$match': {
                'edhrec_rank': {'$exists': True, '$ne': None, '$type': 'number', '$gte': 1},  # Must have valid EDHREC rank
                'lang': 'en',  # English cards only
                '$expr': {
                    '$lt': [
                        {'$size': {'$objectToArray': {'$ifNull': ['$analysis.sections', {}]}}},
                        6  # Both modes need cards with < 6 sections
                    ]
                }
            }},
            {'$sort': {'edhrec_rank': 1, 'released_at': 1}},  # Most popular first
            {'$group': {'_id': '$name', 'best_printing': {'$first': '$$ROOT'}}},
            {'$sort': {'best_printing.edhrec_rank': 1}},
            {'$limit': limit},
            {'$replaceRoot': {'newRoot': '$best_printing'}}
        ]
        
        available_cards = list(cards.aggregate(pipeline))
        
        if not available_cards:
            return jsonify({
                'status': 'no_cards',
                'message': 'No cards found that need work',
                'queue_info': {'mode': mode, 'explanation': 'No cards with < 6 sections'}
            }), 404
        
        # Convert to expected format
        result_cards = []
        for card in available_cards:
            card_data = {
                'uuid': card.get('uuid'),
                'scryfall_id': card.get('scryfall_id'),
                'name': card.get('name'),
                'mana_cost': card.get('mana_cost', ''),
                'type_line': card.get('type_line', ''),
                'oracle_text': card.get('oracle_text', ''),
                'power': card.get('power'),
                'toughness': card.get('toughness'),
                'cmc': card.get('cmc', 0),
                'colors': card.get('colors', []),
                'rarity': card.get('rarity', ''),
                'set': card.get('set', ''),
                'image_uris': card.get('image_uris', {}),
                'prices': card.get('prices', {}),
                'edhrec_rank': card.get('edhrec_rank'),
                'priority_source': 'simple_edhrec',
                'queue_reason': 'most_popular_needs_work'
            }
            # Remove None values
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        # Simple logging
        if result_cards:
            card = result_cards[0]
            current_sections = len(available_cards[0].get('analysis', {}).get('sections', {}))
            log_worker_action(mode, f"Card assignment", f"{card['name']} (rank:{card.get('edhrec_rank', 'N/A')}, sections:{current_sections})")

        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'returned_count': len(result_cards),
            'selection_info': {
                'type': 'simple_edhrec',
                'mode': mode,
                'explanation': 'Most popular card with < 6 sections',
                'total_available': len(available_cards),
                'query_timestamp': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        log_api_stats("get_random_unreviewed", "error", str(e))
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500


# --- PRIORITY LIST ENDPOINTS ---
@app.route('/api/submit_priority_list', methods=['POST'])
def submit_priority_list():
    """Submit a list of card UUIDs to prioritize for processing"""
    try:
        data = request.json
        if not data or 'uuids' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing required field: uuids (array of UUIDs)'
            }), 400
        
        uuids = data['uuids']
        if not isinstance(uuids, list):
            return jsonify({
                'status': 'error',
                'message': 'uuids must be an array'
            }), 400
        
        # Clean and validate UUIDs
        valid_uuids = []
        invalid_uuids = []
        
        for uuid in uuids:
            uuid = str(uuid).strip()
            if uuid:
                # Check if card exists by uuid or scryfall_id
                card = cards.find_one({'$or': [{'uuid': uuid}, {'scryfall_id': uuid}]}, {'uuid': 1, 'name': 1, 'has_full_content': 1})
                if card:
                    valid_uuids.append({
                        'uuid': card.get('uuid'),  # Use the database UUID, not the input UUID
                        'name': card.get('name', 'Unknown'),
                        'has_analysis': card.get('has_full_content', False)
                    })
                else:
                    invalid_uuids.append(uuid)
        
        if not valid_uuids:
            return jsonify({
                'status': 'error',
                'message': 'No valid UUIDs found in the provided list',
                'invalid_uuids': invalid_uuids
            }), 400
        
        # Store priority list in a new collection
        priority_collection = db.priority_cards
        
        # Clear existing priority list and add new ones
        priority_collection.delete_many({})
        
        priority_docs = []
        for i, card_info in enumerate(valid_uuids):
            priority_docs.append({
                'uuid': card_info['uuid'],
                'name': card_info['name'],
                'priority_order': i + 1,
                'has_analysis': card_info['has_analysis'],
                'submitted_at': datetime.now(),
                'processed': False
            })
        
        if priority_docs:
            priority_collection.insert_many(priority_docs)
            logger.info(f"📋 Priority queue updated: {len(priority_docs)} cards queued | {len([c for c in valid_uuids if c['has_analysis']])} with analysis, {len([c for c in valid_uuids if not c['has_analysis']])} need analysis")
            # Create index for fast queries
            try:
                priority_collection.create_index('uuid', unique=True)
                priority_collection.create_index('priority_order')
                priority_collection.create_index('processed')
            except Exception:
                pass  # Indexes might already exist
        
        return jsonify({
            'status': 'success',
            'message': f'Priority list submitted with {len(valid_uuids)} valid cards',
            'valid_cards': len(valid_uuids),
            'invalid_uuids': invalid_uuids,
            'cards_with_analysis': len([c for c in valid_uuids if c['has_analysis']]),
            'cards_needing_analysis': len([c for c in valid_uuids if not c['has_analysis']])
        })
        
    except Exception as e:
        log_api_stats("submit_priority_list", "error", str(e))
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/get_priority_work', methods=['GET'])
def get_priority_work():
    """Get the next card from the priority list for processing"""
    try:
        priority_collection = db.priority_cards
        
        # Find the next unprocessed card in priority order
        priority_card = priority_collection.find_one(
            {'processed': False},
            sort=[('priority_order', 1)]
        )
        
        if not priority_card:
            return jsonify({
                'status': 'no_priority_work',
                'message': 'No cards in priority queue',
                'total_in_queue': priority_collection.count_documents({})
            }), 404
        
        # Get the full card data
        card = cards.find_one({'uuid': priority_card['uuid']})
        if not card:
            # Mark as processed if card doesn't exist
            priority_collection.update_one(
                {'uuid': priority_card['uuid']},
                {'$set': {'processed': True, 'processed_at': datetime.now()}}
            )
            return jsonify({
                'status': 'error',
                'message': f'Priority card {priority_card["uuid"]} not found in database'
            }), 404
        
        # Return card data in same format as get_random_unreviewed
        card_data = {
            'uuid': card.get('uuid'),
            'scryfall_id': card.get('scryfall_id'),
            'name': card.get('name'),
            'mana_cost': card.get('mana_cost', ''),
            'type_line': card.get('type_line', ''),
            'oracle_text': card.get('oracle_text', ''),
            'power': card.get('power'),
            'toughness': card.get('toughness'),
            'cmc': card.get('cmc', 0),
            'colors': card.get('colors', []),
            'rarity': card.get('rarity', ''),
            'set': card.get('set', ''),
            'image_uris': card.get('image_uris', {}),
            'prices': card.get('prices', {})
        }
        # Remove None values
        card_data = {k: v for k, v in card_data.items() if v is not None}
        
        # Get queue stats
        total_in_queue = priority_collection.count_documents({})
        remaining_in_queue = priority_collection.count_documents({'processed': False})
        
        return jsonify({
            'status': 'success',
            'cards': [card_data],
            'returned_count': 1,
            'priority_info': {
                'priority_order': priority_card['priority_order'],
                'total_in_queue': total_in_queue,
                'remaining_in_queue': remaining_in_queue,
                'queue_progress': f"{total_in_queue - remaining_in_queue + 1}/{total_in_queue}"
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting priority work: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/priority_status', methods=['GET'])
def priority_status():
    """Get status of the priority processing queue"""
    try:
        priority_collection = db.priority_cards
        
        total_in_queue = priority_collection.count_documents({})
        processed = priority_collection.count_documents({'processed': True})
        remaining = priority_collection.count_documents({'processed': False})
        
        # Get next few cards in queue
        next_cards = list(priority_collection.find(
            {'processed': False},
            {'uuid': 1, 'name': 1, 'priority_order': 1},
            sort=[('priority_order', 1)],
            limit=5
        ))
        
        # Get recently processed cards
        recent_processed = list(priority_collection.find(
            {'processed': True},
            {'uuid': 1, 'name': 1, 'priority_order': 1, 'processed_at': 1},
            sort=[('processed_at', -1)],
            limit=5
        ))
        
        return jsonify({
            'status': 'success',
            'queue_stats': {
                'total_submitted': total_in_queue,
                'processed': processed,
                'remaining': remaining,
                'completion_percentage': round((processed / total_in_queue * 100), 2) if total_in_queue > 0 else 0
            },
            'next_cards': [
                {
                    'uuid': card['uuid'],
                    'name': card['name'],
                    'priority_order': card['priority_order']
                } for card in next_cards
            ],
            'recently_processed': [
                {
                    'uuid': card['uuid'],
                    'name': card['name'],
                    'priority_order': card['priority_order'],
                    'processed_at': card.get('processed_at')
                } for card in recent_processed
            ]
        })
        
    except Exception as e:
        logger.error(f"Error getting priority status: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# --- MODIFIED SUBMIT WORK TO HANDLE PRIORITY QUEUE ---
@app.route('/api/submit_work', methods=['POST'])
def submit_work():
    data = request.json
    # Accept either a single dict (legacy) or a list of dicts (batch)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return jsonify({'status': 'error', 'message': 'Invalid payload'}), 400

    results = []
    for entry in data:
        if not entry or 'uuid' not in entry or 'analysis' not in entry:
            results.append({'uuid': entry.get('uuid') if entry else None, 'status': 'error', 'message': 'Missing required fields'})
            continue
        update_fields = {}
        # Flatten card_data fields to top level
        if 'card_data' in entry:
            for k, v in entry['card_data'].items():
                if k != 'id':
                    update_fields[k] = v
        # Always save uuid and analysis at top level
        update_fields['uuid'] = entry['uuid']
        update_fields['analysis'] = entry['analysis']
        # Set has_full_content if provided by worker
        if entry.get('has_full_content') is True:
            update_fields['has_full_content'] = True
        # Set analyzed_at inside the analysis object, not overwriting it
        if 'analysis' in update_fields and isinstance(update_fields['analysis'], dict):
            update_fields['analysis']['analyzed_at'] = datetime.now().isoformat()
        try:
            # Always set status to 'public' on new/updated guides
            update_fields['status'] = 'public'
            cards.update_one(
                {'uuid': entry['uuid']},
                {'$set': update_fields},
                upsert=True
            )
            log_card_action("Analysis saved", card_name, entry['uuid'], f"sections: {len(entry.get('analysis', {}).get('sections', {}))}")
            
            # Extract mentions and update mention counts for new analyses
            try:
                card_name = entry.get('card_data', {}).get('name') or update_fields.get('name')
                if card_name and entry.get('analysis'):
                    # Extract mentions from the analysis using simple method
                    analysis_content = entry['analysis']
                    if isinstance(analysis_content, dict):
                        # For sectioned analysis, check all sections
                        all_text = ""
                        if 'sections' in analysis_content:
                            for section_data in analysis_content['sections'].values():
                                if isinstance(section_data, dict) and 'content' in section_data:
                                    all_text += section_data['content'] + " "
                        elif 'content' in analysis_content:
                            all_text = analysis_content['content']
                    else:
                        # For string analysis content
                        all_text = str(analysis_content)
                    
                    mentioned_cards = extract_card_mentions_simple(all_text)
                    if mentioned_cards:
                        logger.info(f"🔗 Found {len(mentioned_cards)} card mentions in '{card_name}': {', '.join(mentioned_cards[:3])}{'...' if len(mentioned_cards) > 3 else ''}")
                        update_mentions_histogram_simple(mentioned_cards, card_name)
                    else:
                        logger.debug(f"📝 No card mentions found in '{card_name}'")
            except Exception as mention_error:
                logger.error(f"🔗 Error tracking mentions for '{card_name}': {mention_error}")
                # Don't fail the whole operation if mention tracking fails
            
            # Mark priority card as processed if it exists in priority queue, and shuffle queue after submission
            try:
                priority_collection = db.priority_cards
                # Delete all queue entries for this card name
                if card_name:
                    delete_result = priority_collection.delete_many({'name': card_name})
                    logger.info(f"🗑️ All queue entries for '{card_name}' deleted from priority queue (deleted: {delete_result.deleted_count})")
                else:
                    delete_result = priority_collection.delete_one({'uuid': entry['uuid']})
                    logger.info(f"🗑️ Queue entry for uuid {entry['uuid']} deleted from priority queue (deleted: {delete_result.deleted_count})")
                # Shuffle the remaining queue after submission
                remaining = list(priority_collection.find({'processed': False}))
                import random
                random.shuffle(remaining)
                for i, doc in enumerate(remaining):
                    priority_collection.update_one({'_id': doc['_id']}, {'$set': {'priority_order': i+1}})
            except Exception as priority_error:
                logger.error(f"📋 Error updating priority status for '{card_name}': {priority_error}")
                # Don't fail the whole operation if priority update fails
            
            results.append({'uuid': entry['uuid'], 'status': 'ok'})
        except Exception as e:
            log_card_action("Save failed", entry.get('card_data', {}).get('name', 'Unknown'), entry['uuid'], str(e))
            results.append({'uuid': entry['uuid'], 'status': 'error', 'message': str(e)})
    return jsonify({'status': 'ok', 'results': results})


# --- COMPONENTIZED CONTENT GENERATION API ---

@app.route('/api/fetch_guide_component', methods=['GET'])
def fetch_guide_component():
    """
    Fetch the next guide component that needs to be generated for any MTG card.
    Returns component metadata for workers to generate content.
    """
    try:
        # Find English cards that need complete analysis or are missing specific components
        card_needing_work = cards.find_one({
            'lang': 'en',  # Only work on English cards to avoid duplicates
            '$or': [
                # Cards with no analysis at all
                {'has_analysis': {'$ne': True}},
                # Cards with partial analysis (missing sections)
                {
                    'analysis.sections': {'$exists': True},
                    '$expr': {
                        '$lt': [
                            {'$size': {'$objectToArray': '$analysis.sections'}},
                            12  # Total number of guide sections
                        ]
                    }
                }
            ]
        })
        
        if not card_needing_work:
            return jsonify({
                'status': 'no_work',
                'message': 'No components need generation at this time'
            }), 204
        
        # Determine which component is needed
        existing_sections = set()
        if card_needing_work.get('analysis', {}).get('sections'):
            existing_sections = set(card_needing_work['analysis']['sections'].keys())
        
        # Find the first missing section from our guide structure
        missing_section = None
        # Iterate over present sections in order (no fixed GUIDE_SECTIONS)
        for section_key in card_needing_work.get('analysis', {}).get('sections', {}).keys():
            if section_key not in existing_sections:
                missing_section = section_key
                break
        
        if not missing_section:
            # All sections exist, check if we need to regenerate content
            if not card_needing_work.get('analysis', {}).get('content'):
                missing_section = 'content_assembly'
        
        if not missing_section:
            return jsonify({
                'status': 'no_work',
                'message': 'Card analysis is complete'
            }), 204

        # Return component work specification

        # --- FULL CARD OBJECT PASSING LOGIC ---
        # Model selection logic
        requested_model = request.args.get('model')
        available_models = ['llama3.1:latest', 'gemini-pro', 'gpt-4o', 'gpt-4-turbo']
        import random
        if requested_model == 'all':
            models_to_use = available_models
        elif requested_model == 'random':
            models_to_use = [random.choice(available_models)]
        elif requested_model in available_models:
            models_to_use = [requested_model]
        else:
            models_to_use = ['llama3.1:latest']

        def can_use_full_card(model_name):
            return model_name in ['llama3.1:latest', 'gpt-4o', 'gpt-4-turbo']

        full_card_obj = dict(card_needing_work)
        full_card_obj.pop('_id', None)

        model_contexts = []
        for model in models_to_use:
            # Context chaining: gather all previous section outputs in guide order
            chained_sections = []
            if card_needing_work.get('analysis', {}).get('sections'):
                for section_key in card_needing_work.get('analysis', {}).get('sections', {}).keys():
                    if section_key == missing_section:
                        break
                    section_data = card_needing_work['analysis']['sections'].get(section_key)
                    if section_data and section_data.get('content'):
                        chained_sections.append({
                            'section': section_key,
                            'title': section_data.get('title', section_key.replace('_', ' ').title()),
                            'content': section_data['content']
                        })

            context = {
                'card_data': {
                    'uuid': card_needing_work.get('uuid'),
                    'name': card_needing_work.get('name'),
                    'mana_cost': card_needing_work.get('manaCost', ''),
                    'type_line': card_needing_work.get('typeLine', ''),
                    'oracle_text': card_needing_work.get('text', ''),
                    'power': card_needing_work.get('power', ''),
                    'toughness': card_needing_work.get('toughness', ''),
                    'set_name': card_needing_work.get('setName', ''),
                    'rarity': card_needing_work.get('rarity', '')
                },
                'existing_sections': list(existing_sections),
                'total_sections_needed': len(card_needing_work.get('analysis', {}).get('sections', {})),
                'chained_sections': chained_sections
            }
            if can_use_full_card(model):
                context['full_card'] = full_card_obj
            model_contexts.append({
                'model': model,
                'context': context
            })

        component_spec = {
            'status': 'work_available',
            'destination': {
                'uuid': card_needing_work['uuid'],
                'name': card_needing_work['name'],
                'type': 'mtg_card'
            },
            'component': {
                'type': missing_section,
                'title': missing_section.replace('_', ' ').title(),
                'prompt_template': '',
                'language': 'en',
                'models': models_to_use
            },
            'contexts': model_contexts if len(model_contexts) > 1 else model_contexts[0]
        }

        logger.info(f"🎯 Provided component work: '{missing_section}' for '{card_needing_work['name']}' | models: {', '.join(models_to_use)}")
        return jsonify(component_spec)
        
    except Exception as e:
        log_api_stats("fetch_guide_component", "error", str(e))
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

@app.route('/api/getwork', methods=['GET'])
def api_getwork():
    """
    Returns a single card for worker processing.
    Prioritizes cards that are unreviewed, missing sections, or need reprocessing.
    """
    from flask import jsonify

    # Use the correct collection and section keys
    card = None

    # 1. Try to find a card with no analysis at all
    card = cards.find_one({"analysis": {"$exists": False}})
    if not card:
        # 2. Try to find a card with incomplete analysis (missing sections)
        card = cards.find_one({
            "$or": [
                {"analysis": {"$exists": False}},
                {"analysis.sections": {"$exists": False}}
            ]
        })
    if not card:
        # 3. Fallback: get any card that is not fully reviewed
        card = cards.find_one({"has_full_content": {"$ne": True}})

    if card:
        card.pop('_id', None)
        return jsonify({"status": "ok", "card": card})
    else:
        return jsonify({"status": "empty", "card": None})

@app.route('/api/submit_guide_component', methods=['POST'])
def submit_guide_component():
    """
    Submit a single generated guide component for an MTG card.
    Assembles and updates the complete content server-side.
    """
    try:
        data = request.json
        # Debug: Log incoming payload and component_type
        import json
        logger.info(f"🔧 Component submission: '{data.get('component_type')}' for card {data.get('uuid', 'unknown')[:8]}... | payload size: {len(str(data))} chars")

        # Safeguard: Prevent accidental overwrite with generic 'section' key
        if data.get('component_type') == 'section':
            logger.warning(f"⚠️  Rejected generic component_type='section' for card {data.get('uuid', 'unknown')[:8]}...")
            return jsonify({'status': 'error', 'message': 'Invalid component_type: section'}), 400
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400
        
        # Validate required fields
        required_fields = ['uuid', 'component_type', 'component_content']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error', 
                    'message': f'Missing required field: {field}'
                }), 400
        
        uuid = data['uuid']
        component_type = data['component_type']
        component_content = data['component_content']
        component_title = data.get('component_title', component_type.replace('_', ' ').title())
        
        # Find the existing card
        card = cards.find_one({'uuid': uuid})
        if not card:
            return jsonify({
                'status': 'error',
                'message': f'Card with UUID {uuid} not found'
            }), 404
        
        # Initialize analysis structure if it doesn't exist
        if 'analysis' not in card:
            card['analysis'] = {
                'sections': {},
                'analyzed_at': datetime.now().isoformat(),
                'model_used': data.get('model_used', 'Unknown'),
                'guide_version': '2.1_gemini_componentized'
            }
        
        # Add the new component to sections
        if 'sections' not in card['analysis']:
            card['analysis']['sections'] = {}
        
        card['analysis']['sections'][component_type] = {
            'title': component_title,
            'content': component_content,
            'language': data.get('language', 'en'),
            'generated_at': datetime.now().isoformat(),
            'model_used': data.get('model_used', 'Unknown')
        }
        
        # SIMPLE MENTION TRACKING: Extract and track mentions from this component
        try:
            mentioned_cards = extract_card_mentions_simple(component_content)
            if mentioned_cards:
                logger.info(f"🔗 Found {len(mentioned_cards)} mentions in '{card['name']}' component '{component_type}': {', '.join(mentioned_cards[:3])}{'...' if len(mentioned_cards) > 3 else ''}")
                update_mentions_histogram_simple(mentioned_cards, card['name'])
            else:
                logger.debug(f"📝 No mentions found in '{card['name']}' component '{component_type}'")
        except Exception as mention_error:
            logger.error(f"🔗 Error tracking mentions in component '{component_type}' for '{card['name']}': {mention_error}")
            # Don't fail the component save if mention tracking fails
        
        # Update metadata
        card['analysis']['last_updated'] = datetime.now().isoformat()
        if data.get('model_used'):
            card['analysis']['model_used'] = data['model_used']
        
        # Check if we have all sections and can assemble complete content
        existing_sections = set(card['analysis']['sections'].keys())
        all_sections = set(card['analysis']['sections'].keys())
        
        if existing_sections == all_sections and len(all_sections) > 0:
            # Assemble complete formatted content
            formatted_content = assemble_guide_content_from_sections(card['analysis']['sections'])
            card['analysis']['content'] = formatted_content
            card['analysis']['status'] = 'complete'
            log_card_action("Complete analysis assembled", card['name'], uuid, f"{len(formatted_content):,} chars, {len(all_sections)} sections")
        else:
            missing_count = len(all_sections - existing_sections)
            card['analysis']['status'] = f'partial ({len(existing_sections)}/{len(all_sections)} sections)'
            log_card_action("Partial analysis updated", card['name'], uuid, f"{missing_count} sections remaining")
        
        # Save to database
        # Always set status to 'public' on new/updated guides
        cards.update_one(
            {'uuid': uuid},
            {
                '$set': {
                    'analysis': card['analysis'],
                    'has_analysis': len(existing_sections) > 0,
                    'last_updated': datetime.now().isoformat(),
                    'status': 'public'
                }
            }
        )
        
        response_data = {
            'status': 'success',
            'uuid': uuid,
            'component_type': component_type,
            'sections_complete': len(existing_sections),
            'sections_total': len(all_sections),
            'analysis_status': card['analysis']['status']
        }
        
        # If analysis is complete, include the URL
        if card['analysis'].get('status') == 'complete':
            response_data['card_url'] = f"{MTGABYSS_PUBLIC_URL}/card/{uuid}"
        
        log_card_action("Component submitted", card['name'], uuid, f"'{component_type}' → {card['analysis']['status']}")
        return jsonify(response_data)
        
    except Exception as e:
        log_api_stats("submit_guide_component", "error", str(e))
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

@app.route('/api/guide_status/<uuid>', methods=['GET'])
def guide_status(uuid):
    """
    Get the current status of guide generation for an MTG card.
    Useful for monitoring progress and determining next steps.
    """
    try:
        card = cards.find_one({'uuid': uuid})
        if not card:
            return jsonify({
                'status': 'error',
                'message': f'Destination with UUID {uuid} not found'
            }), 404
        
        analysis = card.get('analysis', {})
        existing_sections = set(analysis.get('sections', {}).keys())
        all_sections = set(analysis.get('sections', {}).keys())
        
        # Determine what's missing
        missing_sections = all_sections - existing_sections
        
        status_data = {
            'uuid': uuid,
            'name': card['name'],
            'has_analysis': card.get('has_analysis', False),
            'sections_complete': len(existing_sections),
            'sections_total': len(all_sections),
            'completion_percentage': (len(existing_sections) / len(all_sections)) * 100,
            'existing_sections': list(existing_sections),
            'missing_sections': list(missing_sections),
            'analysis_status': analysis.get('status', 'not_started'),
            'last_updated': analysis.get('last_updated'),
            'model_used': analysis.get('model_used'),
            'guide_version': analysis.get('guide_version')
        }
        
        if analysis.get('content'):
            status_data['content_length'] = len(analysis['content'])
            status_data['card_url'] = f"{MTGABYSS_PUBLIC_URL}/card/{uuid}"
        
        return jsonify(status_data)
        
    except Exception as e:
        logger.error(f"Error in destination_status: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

def assemble_guide_content_from_sections(sections):
    """
    Assemble a full guide from a dict of sections, preserving order and using section titles if present.
    """
    if not sections or not isinstance(sections, dict):
        return ''
    parts = []
    for key, section in sections.items():
        title = section.get('title', key.replace('_', ' ').title())
        content = section.get('content', '')
        if content:
            parts.append(f"## {title}\n\n{content.strip()}\n")
    return '\n'.join(parts).strip()



# /api/request_guide endpoint: bumps priority and redirects to /random
@app.route('/api/request_guide', methods=['POST'])
def request_guide():
    data = request.get_json()
    card_uuid = data.get('uuid')
    if not card_uuid:
        return jsonify({'status': 'error', 'message': 'Missing uuid'}), 400

    bump_card_priority(card_uuid)

    return jsonify({
        'status': 'success',
        'uuid': card_uuid,
        'message': (
            "Your guide request has been summoned! Our Planeswalkers are on the job—"
            "check back soon for your card’s analysis. In the meantime, you’ve been redirected "
            "to a random card to explore more Magic strategy."
        ),
        'redirect_url': '/random'
    }), 200

# --- SITEMAP LOGIC ---

# --- SINGLE SITEMAP FOR ALL CARDS WITH FULL CONTENT ---
@app.route('/sitemap.xml', methods=['GET'])
def sitemap_xml():
    """Single sitemap for all card detail pages with full analysis and static pages."""
    ten_days_ago = (datetime.now()).date().isoformat()
    # Card detail pages
    card_cursor = cards.find({'has_full_content': True}, {'uuid': 1})
    card_pages = [
        {'loc': url_for('card_detail', uuid=card['uuid'], _external=True), 'lastmod': ten_days_ago}
        for card in card_cursor
    ]
    # Static pages
    static_pages = [
        {'loc': url_for('search', _external=True), 'lastmod': ten_days_ago},
        {'loc': url_for('random_card_redirect', _external=True), 'lastmod': ten_days_ago},
        {'loc': url_for('gallery', _external=True), 'lastmod': ten_days_ago},
    ]
    pages = static_pages + card_pages
    return Response(render_template('sitemap_cards.xml', pages=pages), mimetype='application/xml')

# Helper functions for backward compatibility with guide formats
def is_sectioned_guide(analysis_data):
    """Always treat as sectioned guide if 'sections' is a dict (removes 3-section minimum and legacy check)"""
    if not analysis_data:
        return False
    sections = analysis_data.get('sections')
    return isinstance(sections, dict) and len(sections) > 0

def get_guide_content(analysis_data, language='en'):
    """Get guide content in the appropriate format"""
    if not analysis_data:
        return None, None, None
    
    # Check if it's a sectioned guide
    if is_sectioned_guide(analysis_data):
        # New format: return sections, formatted content, and metadata
        sections_key = f'{"native_language_" if language != "en" else ""}sections'
        content_key = f'{"native_language_" if language != "en" else ""}content'
        # Support both new "content" and old "long_form" for backward compatibility
        old_content_key = f'{"native_language_" if language != "en" else ""}long_form'
        
        sections = analysis_data.get(sections_key, {})
        formatted_content = analysis_data.get(content_key) or analysis_data.get(old_content_key, '')
        # If content is missing but sections exist, assemble from sections
        if not formatted_content and sections:
            formatted_content = assemble_guide_content_from_sections(sections)

        guide_meta = {
            'type': 'sectioned',
            'version': analysis_data.get('guide_version', '2.0'),
            'section_count': len(sections),
            'model_used': analysis_data.get('model_used', 'Unknown'),
            'analyzed_at': analysis_data.get('analyzed_at')
        }
        return sections, formatted_content, guide_meta
    else:
        # Legacy format: return as single section
        content_key = f'{"native_language_" if language != "en" else ""}content'
        old_content_key = f'{"native_language_" if language != "en" else ""}long_form'
        content = analysis_data.get(content_key) or analysis_data.get(old_content_key, '')
        
        if content:
            # Wrap legacy content as a single section
            legacy_sections = {
                'legacy_content': {
                    'title': 'Complete Guide',
                    'content': content,
                    'language': language
                }
            }
            
            guide_meta = {
                'type': 'legacy',
                'version': '1.0',
                'section_count': 1,
                'model_used': analysis_data.get('model_used', 'Unknown'),
                'analyzed_at': analysis_data.get('analyzed_at')
            }
            
            return legacy_sections, content, guide_meta
        
    return None, None, None

def extract_card_mentions_simple(text):
    """
    Simple, bulletproof card mention extraction from text.
    Finds [[Card Name]] and [Card Name] patterns.
    Returns list of unique card names.
    """
    if not text:
        return []
    
    mentions = set()
    
    # Find [[Card Name]] patterns
    for match in re.findall(r'\[\[([^\]]+)\]\]', text):
        card_name = match.strip()
        if card_name:
            mentions.add(card_name)
    
    # Find [Card Name] patterns (but not [B] or [/B])
    # First, remove all [[...]] patterns to avoid double-matching
    text_without_double_brackets = re.sub(r'\[\[[^\]]+\]\]', '', text)
    
    for match in re.findall(r'\[([^\]]+)\]', text_without_double_brackets):
        card_name = match.strip()
        # Skip formatting tags like [B] and [/B] and empty strings
        if card_name and len(card_name) > 1 and not re.match(r'^/?[BIU]$', card_name, re.IGNORECASE):
            mentions.add(card_name)
    
    return list(mentions)

def update_mentions_histogram_simple(mentioned_card_names, mentioning_card_name):
    """
    Simple, bulletproof mentions histogram update.
    For each mentioned card name, find its UUID and increment count.
    """
    if not mentioned_card_names:
        return
    
    current_time = datetime.now()
    updated_count = 0
    
    for card_name in mentioned_card_names:
        # Skip self-references
        if card_name.lower() == mentioning_card_name.lower():
            continue
            
        try:
            # Find card by name to get UUID
            card = cards.find_one(
                {'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, 
                {'uuid': 1, 'name': 1}
            )
            
            if not card:
                logger.debug(f"🔍 Card '{card_name}' not found for mention tracking")
                continue
                
            # Update mentions histogram
            mentions_histogram.update_one(
                {'uuid': card['uuid']},
                {
                    '$inc': {'mention_count': 1},
                    '$set': {'last_mentioned': current_time},
                    '$setOnInsert': {'card_name': card['name']}
                },
                upsert=True
            )
            updated_count += 1
                
        except Exception as e:
            logger.error(f"🔗 Error tracking mention of '{card_name}': {e}")
    
    if updated_count > 0:
        logger.debug(f"🔗 Updated mentions histogram for {updated_count} cards")

def extract_mentions_from_guide(analysis_data, language='en'):
    """Extract card mentions from either format of guide"""
    sections, formatted_content, guide_meta = get_guide_content(analysis_data, language)
    
    if not formatted_content:
        return []
    
    def extract_mentions(text):
        if not text:
            return []
        names = set()
        # [[Card Name]] - double brackets (priority)
        for m in re.findall(r'\[\[([^\]]+)\]\]', text):
            names.add(m.strip())
        # [Card Name] but not [B] or [/B] and not part of [[ ]]
        # Remove any [[ ]] patterns first to avoid conflicts
        text_without_double_brackets = re.sub(r'\[\[.+?\]\]', '', text)
        for m in re.findall(r'\[([^\]]+)\]', text_without_double_brackets):
            names.add(m.strip())
        return list(names)
    
    return extract_mentions(formatted_content)

def get_card_uuid_by_name(card_name: str) -> str:
    """Get UUID for a card by name (case-insensitive)"""
    # Try exact match first
    card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {'uuid': 1})
    if card:
        return card.get('uuid')
    
    # Try partial match
    card = cards.find_one({'name': {'$regex': re.escape(card_name), '$options': 'i'}}, {'uuid': 1})
    if card:
        return card.get('uuid')
    
    return None

@app.route('/api/get_most_mentioned', methods=['GET'])
def get_most_mentioned():
    """Get cards that are frequently mentioned for processing"""
    try:
        # Optional query parameters
        limit = int(request.args.get('limit', 1))
        min_mentions = int(request.args.get('min_mentions', 1))  # Minimum mentions to be considered
        
        # Get most mentioned cards from histogram
        mentioned_cards = list(mentions_histogram.find(
            {'mention_count': {'$gte': min_mentions}},
            sort=[('mention_count', -1), ('last_mentioned', -1)],
            limit=limit * 2  # Get extra in case some don't exist
        ))
        
        if not mentioned_cards:
            return jsonify({
                'status': 'no_cards',
                'message': f'No cards found with {min_mentions}+ mentions that need analysis',
                'total_mentions_tracked': mentions_histogram.count_documents({})
            }), 404
        
        # Get full card data and format response
        result_cards = []
        for mention_doc in mentioned_cards:
            if len(result_cards) >= limit:
                break
                
            card = cards.find_one({'uuid': mention_doc['uuid']})
            if not card:
                continue
                
           
            card_data = {
                'uuid': card.get('uuid'),
                'scryfall_id': card.get('scryfall_id'),
                'name': card.get('name'),
                'mana_cost': card.get('mana_cost', ''),
                'type_line': card.get('type_line', ''),
                'oracle_text': card.get('oracle_text', ''),
                'power': card.get('power'),
                'toughness': card.get('toughness'),
                'cmc': card.get('cmc', 0),
                'colors': card.get('colors', []),
                'rarity': card.get('rarity', ''),
                'set': card.get('set', ''),
                'image_uris': card.get('image_uris', {}),
                'prices': card.get('prices', {}),
                'mention_count': mention_doc.get('mention_count', 0)
            }
            # Remove None values
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        if not result_cards:
            return jsonify({
                'status': 'no_cards',
                'message': f'No valid cards found with {min_mentions}+ mentions',
                'total_mentions_tracked': mentions_histogram.count_documents({})
            }), 404
        
        # Get stats for response
        total_tracked = mentions_histogram.count_documents({})
        high_priority = mentions_histogram.count_documents({'mention_count': {'$gte': min_mentions}})
        
        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'returned_count': len(result_cards),
            'mention_stats': {
                'total_mentioned_cards': total_tracked,
                'high_priority_cards': high_priority,
                'min_mentions_threshold': min_mentions
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching most mentioned cards: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def add_mentioned_cards_to_priority_queue(mentioned_card_names: list):
    """Automatically add mentioned cards to TOP of priority queue if they need analysis"""
    if not mentioned_card_names:
        return
    
    try:
        priority_collection = db.priority_cards
        current_time = datetime.now()
        
        # Compact priority queue: remove duplicates and renumber
        compact_priority_queue()
        
        # Get current minimum priority order (top of queue)
        min_priority = priority_collection.find_one(
            {}, 
            sort=[('priority_order', 1)]
        )
        
        # Start inserting at the top (lower priority_order = higher priority)
        insert_priority = (min_priority.get('priority_order', 1) if min_priority else 1) - 1
        
        added_count = 0
        
        for card_name in mentioned_card_names:
            # Find the card in the main cards collection
            card = cards.find_one({
                '$or': [
                    {'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}},
                    {'uuid': card_name},
                    {'scryfall_id': card_name}
                ]
            }, {'uuid': 1, 'name': 1, 'has_full_content': 1})

            if not card:
                logger.debug(f"Card '{card_name}' not found in database")
                continue  # Card doesn't exist in database

            # Check if card already has an analysis
            existing_analysis = cards.find_one({
                'uuid': card['uuid'],
                '$or': [
                    {'analysis': {'$exists': True, '$ne': None}},
                    {'has_full_content': True}
                ]
            }, {'uuid': 1})
            if existing_analysis:
                logger.debug(f"Card '{card['name']}' already has analysis, skipping priority queue")
                continue  # Already analyzed

            # Check if already in priority queue
            existing = priority_collection.find_one({'uuid': card['uuid']})
            if existing:
                logger.debug(f"Card '{card['name']}' already in priority queue")
                continue  # Already in priority queue

            # Add to TOP of priority queue
            try:
                priority_collection.insert_one({
                    'uuid': card['uuid'],
                    'name': card['name'],
                    'priority_order': insert_priority,
                    'has_analysis': False,
                    'submitted_at': current_time,
                    'processed': False,
                    'auto_added': True,  # Mark as automatically added
                    'mentioned_in': 'auto_discovery'
                })

                logger.info(f"Auto-added '{card['name']}' to TOP of priority queue (order {insert_priority})")
                insert_priority -= 1 # Next card goes even higher in priority
                added_count += 1

            except Exception as insert_error:
                logger.error(f"Error auto-adding '{card_name}' to priority: {insert_error}")
        
        if added_count > 0:
            logger.info(f"Auto-added {added_count} mentioned cards to TOP of priority queue")
            
    except Exception as e:
        logger.error(f"Error in add_mentioned_cards_to_priority_queue: {e}")

def compact_priority_queue():
    """Remove duplicates and renumber priority queue for efficiency"""
    try:
        priority_collection = db.priority_cards

        # Rarity order for sorting
        RARITY_ORDER = {'mythic': 0, 'rare': 1, 'uncommon': 2, 'common': 3}

        # Get all unique cards, keeping the one with lowest priority_order (highest priority)
        pipeline = [
            {'$sort': {'priority_order': 1}},  # Sort by priority (lowest first)
            {
                '$group': {
                    '_id': '$uuid',
                    'doc': {'$first': '$$ROOT'}  # Keep first (highest priority) occurrence
                }
            },
            {'$replaceRoot': {'newRoot': '$doc'}},
            {'$sort': {'priority_order': 1}}
        ]

        unique_cards = list(priority_collection.aggregate(pipeline))
        if not unique_cards:
            return

        # Fetch rarity for each card and sort by rarity, then by priority_order
        def get_rarity_sort_key(card_uuid):
            card = cards.find_one({'uuid': card_uuid}, {'rarity': 1})
            rarity = card.get('rarity', '').lower() if card else ''
            return RARITY_ORDER.get(rarity, 99)

        unique_cards.sort(key=lambda c: (get_rarity_sort_key(c['uuid']), c.get('priority_order', 9999)))

        # Clear the collection and re-insert with sequential numbering
        priority_collection.delete_many({})
        for i, card in enumerate(unique_cards, start=1):
            card['priority_order'] = i
            card.pop('_id', None)  # Remove old _id
            priority_collection.insert_one(card)

        logger.info(f"Compacted priority queue: {len(unique_cards)} unique cards, renumbered 1-{len(unique_cards)}, prioritized by rarity")
    except Exception as e:
        logger.error(f"Error compacting priority queue: {e}")

# Helper function for dual-faced card images
def get_card_image_uri(card, image_type='normal'):
    """Get the correct image URI for a card, handling dual-faced cards properly."""
    # Check if card has faces (dual-faced card)
    if card.get('card_faces') and len(card['card_faces']) > 0:
        # For dual-faced cards, use the first face's image
        first_face = card['card_faces'][0]
        if first_face.get('image_uris') and first_face['image_uris'].get(image_type):
            return first_face['image_uris'][image_type]
    
    # For single-faced cards or fallback, check root level image_uris
    if card.get('image_uris') and card['image_uris'].get(image_type):
        return card['image_uris'][image_type]
    
    # Also check for imageUris (legacy field name)
    if card.get('imageUris') and card['imageUris'].get(image_type):
        return card['imageUris'][image_type]
    
    return None

@app.template_filter('get_card_image')
def get_card_image_filter(card, image_type='normal'):
    """Template filter to get the correct image URI for a card."""
    return get_card_image_uri(card, image_type)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=2357)

def delete_duplicate_cards():
    """Delete duplicate cards, keeping only the oldest printing of each card."""
    try:
        # Group by card name and find the oldest printing
        pipeline = [
            {
                '$group': {
                    '_id': '$name',
                    'oldest_card': {'$first': '$$ROOT'},
                    'all_ids': {'$push': '$_id'}
                }
            }
        ]

        grouped_cards = list(cards.aggregate(pipeline))

        # Delete all cards except the oldest printing for each name
        for card_group in grouped_cards:
            oldest_id = card_group['oldest_card']['_id']
            all_ids = card_group['all_ids']

            # Remove the oldest ID from the list of IDs to delete
            all_ids.remove(oldest_id)

            # Delete all other printings
            if all_ids:
                cards.delete_many({'_id': {'$in': all_ids}})

        logger.info("✅ Duplicate cards removed, keeping only the oldest printing for each name.")
    except Exception as e:
        logger.error(f"❌ Error deleting duplicate cards: {e}")

# Call the function to clean the database
delete_duplicate_cards()

@app.route('/api/get_card_sections', methods=['GET'])
def get_card_sections():
    """
    Get existing sections for a card to avoid regenerating already completed content.
    """
    try:
        card_uuid = request.args.get('uuid')
        if not card_uuid:
            return jsonify({'status': 'error', 'message': 'uuid parameter required'}), 400

        # Find the card first
        card = cards.find_one({'uuid': card_uuid})
        if not card:
            return jsonify({'status': 'error', 'message': 'Card not found'}), 404

        # Find all existing sections for this card
        existing_sections = []
        if 'analysis' in card and card['analysis']:
            analysis = card['analysis']
            if 'sections' in analysis:
                for section_key, section_data in analysis['sections'].items():
                    if isinstance(section_data, dict) and 'content' in section_data:
                        existing_sections.append({
                            'component_type': section_key,
                            'component_title': section_data.get('title', section_key),
                            'content': section_data.get('content', ''),
                            'model_used': section_data.get('model_used', 'unknown'),
                            'generated_at': section_data.get('generated_at', ''),
                            'created_at': section_data.get('created_at', '')
                        })

        log_api_stats("get_card_sections", "success", f"card: {card.get('name', 'Unknown')} | sections: {len(existing_sections)}")
        return jsonify({
            'status': 'success',
            'card_name': card.get('name', 'Unknown'),
            'uuid': card_uuid,
            'sections': existing_sections,
            'total_sections': len(existing_sections)
        })

    except Exception as e:
        log_api_stats("get_card_sections", "error", str(e))
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500
