from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from pymongo import MongoClient
import os
import logging
import markdown
import re
from datetime import datetime
from time import time

# Environment variables
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')


app = Flask(__name__)
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client.mtgabyss
cards = db.cards
card_mentions = db.card_mentions  # New collection for tracking mention counts

# Ensure indexes for fast queries
try:
    cards.create_index('has_analysis')
    cards.create_index('uuid', unique=True)
    # New indexes for mention tracking
    card_mentions.create_index('card_name', unique=True)
    card_mentions.create_index('mention_count')  # For sorting by popularity
    card_mentions.create_index([('mention_count', -1), ('last_mentioned', -1)])  # Compound index for priority
except Exception as e:
    print(f"Could not create MongoDB indexes: {e}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Markdown instance with desired extensions
md = markdown.Markdown(extensions=['extra', 'codehilite', 'tables'])

@app.template_filter('markdown')
def markdown_filter(text):
    if not text:
        return ''
    return md.convert(text)

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
            'has_full_content': True,  # Only show cards with complete analysis
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
            {'$match': {'has_full_content': True}},  # Only cards with complete analysis
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
        {'has_full_content': True, 'uuid': {'$ne': uuid}}  # Only cards with complete analysis
    ).sort([('analysis.analyzed_at', -1)]).limit(5))
    # Get 6 random cards with full content and image, not this one, for recommendations
    rec_cards = list(cards.aggregate([
        {'$match': {
            'has_full_content': True,  # Only cards with complete analysis
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
                'has_full_content': True  # Only cards with complete analysis
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
        'has_full_content': True
    }).limit(60)
    return render_template('gallery.html', cards=reviewed_cards)

@app.route('/random')
def random_card_redirect():
    # Only pick from cards with full content analysis
    cursor = cards.aggregate([
        {'$match': {'has_full_content': True}},
        {'$sample': {'size': 1}}
    ])
    card = next(cursor, None)
    if not card:
        return "No cards with full content found", 404
    return redirect(f"/card/{card['uuid']}")


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
        logger.error(f"Error fetching stats: {str(e)}")
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500


@app.route('/api/get_random_unreviewed', methods=['GET'])
def get_random_unreviewed():
    """Get random cards for worker processing (no filtering, just random cards)"""
    try:
        # Optional query parameters
        limit = int(request.args.get('limit', 1))  # How many cards to return
        
        # No filtering - just get random cards
        query = {}  # Process any card randomly
        
        # Get count of total cards for progress tracking
        total_cards = cards.count_documents(query)
        
        # Get random card(s)
        pipeline = [
            {'$match': query},
            {'$sample': {'size': limit}}
        ]
        
        random_cards = list(cards.aggregate(pipeline))
        
        if not random_cards:
            return jsonify({
                'status': 'no_cards',
                'message': 'No cards found in database',
                'total_cards': total_cards
            }), 404
        
        # Return essential card data for processing
        result_cards = []
        for card in random_cards:
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
            # Remove None values to keep response clean
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'total_cards': total_cards,
            'returned_count': len(result_cards)
        })
        
    except Exception as e:
        logger.error(f"Error fetching random card: {str(e)}")
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
                'submitted_at': datetime.utcnow(),
                'processed': False
            })
        
        if priority_docs:
            priority_collection.insert_many(priority_docs)
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
        logger.error(f"Error submitting priority list: {str(e)}")
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
                {'$set': {'processed': True, 'processed_at': datetime.utcnow()}}
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
            update_fields['analysis']['analyzed_at'] = datetime.utcnow().isoformat()
        try:
            cards.update_one(
                {'uuid': entry['uuid']},
                {'$set': update_fields},
                upsert=True
            )
            logger.info(f"Saved analysis for card {entry['uuid']}")
            
            # Extract mentions and update mention counts for new analyses
            try:
                card_name = entry.get('card_data', {}).get('name') or update_fields.get('name')
                if card_name and entry.get('analysis'):
                    # Extract mentions from the analysis
                    mentioned_cards = extract_mentions_from_guide(entry['analysis'], 'en')
                    if mentioned_cards:
                        logger.info(f"Found {len(mentioned_cards)} card mentions in {card_name}: {mentioned_cards}")
                        update_mention_counts(card_name, mentioned_cards)
                        
                        # Immediately add mentioned cards to priority queue if they need analysis
                        add_mentioned_cards_to_priority_queue(mentioned_cards)
                    else:
                        logger.info(f"No card mentions found in {card_name}")
            except Exception as mention_error:
                logger.error(f"Error processing mentions for {entry['uuid']}: {mention_error}")
                # Don't fail the whole operation if mention tracking fails
            
            # Mark priority card as processed if it exists in priority queue
            try:
                priority_collection = db.priority_cards
                priority_result = priority_collection.update_one(
                    {'uuid': entry['uuid']},
                    {'$set': {'processed': True, 'processed_at': datetime.utcnow()}}
                )
                if priority_result.matched_count > 0:
                    logger.info(f"Marked priority card {entry['uuid']} as processed")
            except Exception as priority_error:
                logger.error(f"Error updating priority status for {entry['uuid']}: {priority_error}")
                # Don't fail the whole operation if priority update fails
            
            results.append({'uuid': entry['uuid'], 'status': 'ok'})
        except Exception as e:
            logger.error(f"Error saving analysis for {entry['uuid']}: {str(e)}")
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
        for section_key in GUIDE_SECTIONS.keys():
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
        component_spec = {
            'status': 'work_available',
            'destination': {
                'uuid': card_needing_work['uuid'],
                'name': card_needing_work['name'],
                'type': 'mtg_card'
            },
            'component': {
                'type': missing_section,
                'title': GUIDE_SECTIONS.get(missing_section, {}).get('title', missing_section),
                'prompt_template': GUIDE_SECTIONS.get(missing_section, {}).get('prompt', ''),
                'language': 'en'  # Default to English, could be parameterized
            },
            'context': {
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
                'total_sections_needed': len(GUIDE_SECTIONS)
            }
        }
        
        logger.info(f"Provided component work: {missing_section} for card {card_needing_work['name']}")
        return jsonify(component_spec)
        
    except Exception as e:
        logger.error(f"Error in fetch_destination_component: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

@app.route('/api/submit_guide_component', methods=['POST'])
def submit_guide_component():
    """
    Submit a single generated guide component for an MTG card.
    Assembles and updates the complete content server-side.
    """
    try:
        data = request.json
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
                'analyzed_at': datetime.utcnow().isoformat(),
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
            'generated_at': datetime.utcnow().isoformat(),
            'model_used': data.get('model_used', 'Unknown')
        }
        
        # Update metadata
        card['analysis']['last_updated'] = datetime.utcnow().isoformat()
        if data.get('model_used'):
            card['analysis']['model_used'] = data['model_used']
        
        # Check if we have all sections and can assemble complete content
        existing_sections = set(card['analysis']['sections'].keys())
        all_sections = set(GUIDE_SECTIONS.keys())
        
        if existing_sections >= all_sections:
            # Assemble complete formatted content
            formatted_content = format_guide_for_display(card['analysis']['sections'])
            card['analysis']['content'] = formatted_content
            card['analysis']['status'] = 'complete'
            logger.info(f"Complete analysis assembled for {card['name']} ({len(formatted_content)} chars)")
        else:
            missing_count = len(all_sections - existing_sections)
            card['analysis']['status'] = f'partial ({len(existing_sections)}/{len(all_sections)} sections)'
            logger.info(f"Partial analysis updated for {card['name']} ({missing_count} sections remaining)")
        
        # Save to database
        cards.update_one(
            {'uuid': uuid},
            {
                '$set': {
                    'analysis': card['analysis'],
                    'has_analysis': len(existing_sections) > 0,
                    'last_updated': datetime.utcnow().isoformat()
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
        
        logger.info(f"Component {component_type} submitted for {card['name']}")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error in submit_destination_component: {str(e)}")
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
        all_sections = set(GUIDE_SECTIONS.keys())
        
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


# --- HELPER FUNCTIONS FOR COMPONENTIZED SYSTEM ---

def format_guide_for_display(sections_dict):
    """
    Format individual sections into a complete guide for display.
    This is the server-side assembly of componentized content.
    """
    if not sections_dict:
        return ""
    
    # Order sections according to our guide structure
    ordered_content = []
    for section_key in GUIDE_SECTIONS.keys():
        if section_key in sections_dict:
            section_data = sections_dict[section_key]
            section_title = section_data.get('title', GUIDE_SECTIONS[section_key]['title'])
            section_content = section_data.get('content', '')
            
            if section_content.strip():
                # Format each section with markdown header
                ordered_content.append(f"## {section_title}\n\n{section_content.strip()}\n")
    
    return "\n".join(ordered_content)


# --- GUIDE SECTIONS DEFINITION ---
# Moving this from worker to shared location for API use

GUIDE_SECTIONS = {
    "overview": {
        "title": "Card Overview",
        "prompt": "Write a comprehensive overview of [[{card_name}]] as a Magic: The Gathering card. Explain what makes this card unique and noteworthy in the game's ecosystem."
    },
    "mechanics_breakdown": {
        "title": "Mechanics and Interactions", 
        "prompt": "Provide a detailed breakdown of [[{card_name}]]'s mechanics, rules interactions, and technical aspects. Cover how the card works, edge cases, and timing considerations."
    },
    "strategic_applications": {
        "title": "Strategic Applications",
        "prompt": "Analyze the strategic uses of [[{card_name}]] in gameplay. Cover combos, synergies, and tactical applications across different scenarios."
    },
    "deckbuilding_guide": {
        "title": "Deckbuilding and Archetypes",
        "prompt": "Explain how to build around [[{card_name}]] and what archetypes it fits into. Cover deck construction considerations, support cards, and build-around strategies."
    },
    "format_analysis": {
        "title": "Format Viability",
        "prompt": "Assess [[{card_name}]]'s performance and viability across different Magic formats (Standard, Modern, Legacy, Commander, etc.). Include competitive context and meta positioning."
    },
    "gameplay_scenarios": {
        "title": "Gameplay Scenarios",
        "prompt": "Describe common gameplay scenarios involving [[{card_name}]]. Cover typical play patterns, decision points, and situational considerations."
    },
    "historical_context": {
        "title": "Historical Impact",
        "prompt": "Explore [[{card_name}]]'s place in Magic's history, its impact on the game, and how it has evolved in the meta over time."
    },
    "flavor_and_design": {
        "title": "Flavor and Design Philosophy", 
        "prompt": "Analyze the flavor, art, and design philosophy behind [[{card_name}]]. Cover lore connections, artistic elements, and design intent."
    },
    "budget_considerations": {
        "title": "Budget and Accessibility",
        "prompt": "Discuss the financial aspects of [[{card_name}]] including market price, budget alternatives, and accessibility for different player types."
    },
    "advanced_techniques": {
        "title": "Advanced Play Techniques",
        "prompt": "Cover advanced techniques, pro tips, and high-level play considerations for [[{card_name}]]. Include expert-level insights and optimization strategies."
    },
    "common_mistakes": {
        "title": "Common Mistakes and Pitfalls",
        "prompt": "Identify common mistakes players make with [[{card_name}]] and how to avoid them. Cover misplays, misconceptions, and learning points."
    },
    "conclusion": {
        "title": "Final Assessment",
        "prompt": "Provide a final assessment and summary of [[{card_name}]]'s overall value, role in Magic, and recommendations for players considering using it."
    }
}


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
    """Check if this is a new sectioned guide or old monolithic format (robust to new worker output)"""
    # Accept if 'sections' is a dict and has at least 3 keys (for new worker)
    if not analysis_data:
        return False
    sections = analysis_data.get('sections')
    if isinstance(sections, dict) and len(sections) >= 3:
        return True
    # Accept if 'guide_version' startswith '2.' (legacy sectioned)
    if analysis_data.get('guide_version', '').startswith('2.'):
        return True
    return False

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

def extract_mentions_from_guide(analysis_data, language='en'):
    """Extract card mentions from either format of guide"""
    sections, formatted_content, guide_meta = get_guide_content(analysis_data, language)
    
    if not formatted_content:
        return []
    
    def extract_mentions(text):
        if not text:
            return []
        names = set()
        # [[Card Name]]
        for m in re.findall(r'\[\[(.+?)\]\]', text):
            names.add(m.strip())
        # [Card Name] but not [B] or [/B]
        for m in re.findall(r'\[(?!/?B\])(.*?)\]', text):
            names.add(m.strip())
        return list(names)
    
    return extract_mentions(formatted_content)

def update_mention_counts(card_name: str, mentioned_cards: list):
    """Update mention counts for cards referenced in a new analysis"""
    if not mentioned_cards:
        return
    
    current_time = datetime.utcnow()
    
    # Process each mentioned card
    for mentioned_card in mentioned_cards:
        # Skip self-references
        if mentioned_card.strip().lower() == card_name.strip().lower():
            continue
            
        try:
            # Use upsert to increment mention count
            result = card_mentions.update_one(
                {'card_name': mentioned_card},
                {
                    '$inc': {'mention_count': 1},
                    '$set': {
                        'last_mentioned': current_time,
                        'last_mentioned_in': card_name
                    },
                    '$setOnInsert': {
                        'first_mentioned': current_time,
                        'created_at': current_time
                    }
                },
                upsert=True
            )
            
            if result.upserted_id:
                logger.info(f"Started tracking mentions for '{mentioned_card}' (mentioned in {card_name})")
            else:
                # Get current count for logging
                mention_doc = card_mentions.find_one({'card_name': mentioned_card})
                count = mention_doc.get('mention_count', 0) if mention_doc else 0
                logger.info(f"Updated mention count for '{mentioned_card}': {count} mentions (latest: {card_name})")
                
        except Exception as e:
            logger.error(f"Error updating mention count for '{mentioned_card}': {e}")

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
    """Get cards that are frequently mentioned but don't have analysis yet"""
    try:
        # Optional query parameters
        limit = int(request.args.get('limit', 1))
        min_mentions = int(request.args.get('min_mentions', 2))  # Minimum mentions to be considered
        
        # Get most mentioned cards that don't have analysis yet
        pipeline = [
            # Join with cards collection to check analysis status
            {
                '$lookup': {
                    'from': 'cards',
                    'let': {'mention_name': '$card_name'},
                    'pipeline': [
                        {
                            '$match': {
                                '$expr': {
                                    '$eq': [
                                        {'$toLower': '$name'},
                                        {'$toLower': '$$mention_name'}
                                    ]
                                }
                            }
                        }
                    ],
                    'as': 'card_match'
                }
            },
            # Filter for cards that exist and don't have full content
            {
                '$match': {
                    'mention_count': {'$gte': min_mentions},
                    'card_match': {'$ne': []},  # Card exists
                    'card_match.has_full_content': {'$ne': True}  # No analysis yet
                }
            },
            # Sort by mention count (most mentioned first)
            {'$sort': {'mention_count': -1, 'last_mentioned': -1}},
            # Limit results
            {'$limit': limit},
            # Include the actual card data
            {
                '$addFields': {
                    'card': {'$arrayElemAt': ['$card_match', 0]}
                }
            }
        ]
        
        mentioned_cards = list(card_mentions.aggregate(pipeline))
        
        if not mentioned_cards:
            return jsonify({
                'status': 'no_cards',
                'message': f'No cards found with {min_mentions}+ mentions that need analysis',
                'total_mentions_tracked': card_mentions.count_documents({})
            }), 404
        
        # Format response with card data
        result_cards = []
        for mention_doc in mentioned_cards:
            card = mention_doc.get('card', {})
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
                # Add mention metadata
                'mention_count': mention_doc.get('mention_count', 0),
                'last_mentioned': mention_doc.get('last_mentioned'),
                'last_mentioned_in': mention_doc.get('last_mentioned_in')
            }
            # Remove None values
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        # Get stats for response
        total_tracked = card_mentions.count_documents({})
        high_priority = card_mentions.count_documents({'mention_count': {'$gte': min_mentions}})
        
        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'returned_count': len(result_cards),
            'mention_stats': {
                'total_cards_tracked': total_tracked,
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
        current_time = datetime.utcnow()
        
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
                
            if card.get('has_full_content'):
                logger.debug(f"Card '{card['name']}' already has analysis")
                continue  # Card already has analysis
                
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
        
        # Clear the collection and re-insert with sequential numbering
        priority_collection.delete_many({})
        
        for i, card in enumerate(unique_cards, start=1):
            card['priority_order'] = i
            card.pop('_id', None)  # Remove old _id
            priority_collection.insert_one(card)
        
        logger.info(f"Compacted priority queue: {len(unique_cards)} unique cards, renumbered 1-{len(unique_cards)}")
        
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
    app.run(debug=True)
