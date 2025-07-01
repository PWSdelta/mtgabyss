from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from pymongo import MongoClient
import os
import logging
import markdown
import re
from datetime import datetime
from time import time


app = Flask(__name__)
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client.mtgabyss
cards = db.cards
# Ensure indexes for fast unreviewed card queries
try:
    # Index for fast lookup of unreviewed cards by language (and optionally rarity/set)
    cards.create_index([('analysis', 1), ('lang', 1)])
    # If you often filter by rarity or set, add compound indexes as well:
    cards.create_index([('analysis', 1), ('lang', 1), ('rarity', 1)])
    cards.create_index([('analysis', 1), ('lang', 1), ('set', 1)])
    # For card detail lookups
    cards.create_index('uuid', unique=True)
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

    # Per-request cache for card name lookups
    card_cache = {}

    def card_link_replacer(match):
        card_name = match.group(1)
        if current_card_name and card_name.strip().lower() == current_card_name.strip().lower():
            return card_name
        cache_key = card_name.strip().lower()
        if cache_key in card_cache:
            uuid = card_cache[cache_key]
            if uuid:
                url = url_for('card_detail', uuid=uuid)
                return f'<a href="{url}">{card_name}</a>'
            else:
                url = url_for('search', q=card_name)
                return f'<a href="{url}">{card_name}</a>'
        # Try to find the card by name (case-insensitive, exact match)
        card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {'uuid': 1})
        if card and 'uuid' in card:
            card_cache[cache_key] = card['uuid']
            url = url_for('card_detail', uuid=card['uuid'])
            return f'<a href="{url}">{card_name}</a>'
        # Try partial match if no exact match
        card = cards.find_one({'name': {'$regex': re.escape(card_name), '$options': 'i'}}, {'uuid': 1})
        if card and 'uuid' in card:
            card_cache[cache_key] = card['uuid']
            url = url_for('card_detail', uuid=card['uuid'])
            return f'<a href="{url}">{card_name}</a>'
        # Fallback: link to search page with card name
        card_cache[cache_key] = None
        url = url_for('search', q=card_name)
        return f'<a href="{url}">{card_name}</a>'

    # Replace [[Card Name]] and [Card Name] (but not [B] or [/B])
    text = re.sub(r'\[\[(.+?)\]\]', card_link_replacer, text)
    text = re.sub(r'\[(?!/?B\])(.*?)\]', card_link_replacer, text)
    return text

# Simple in-memory cache for randomized homepage results
_frontpage_cache = {
    'results': [],
    'timestamp': 0
}

# Web routes
@app.route('/')
def search():
    """Card search page"""
    query = request.args.get('q', '')
    if query:
        results = list(cards.find({'name': {'$regex': query, '$options': 'i'}}).limit(30))
    else:
        now = time()
        # 1 hour = 3600 seconds
        if not _frontpage_cache['results'] or now - _frontpage_cache['timestamp'] > 3600:
            # Get 100 random cards with analysis
            results = list(cards.aggregate([
                {'$match': {'analysis': {'$exists': True}}},
                {'$sample': {'size': 30}}
            ]))
            _frontpage_cache['results'] = results
            _frontpage_cache['timestamp'] = now
        else:
            results = _frontpage_cache['results']
    return render_template('search.html', cards=results, query=query)

@app.route('/card/<uuid>')
def card_detail(uuid):
    """Card detail page"""
    card = cards.find_one({'uuid': uuid})
    if card and 'category' not in card:
        card['category'] = 'mtg'
    # all_cards removed for performance
    return render_template('card.html', card=card, current_card_name=card['name'] if card else None)

@app.route('/gallery')
def gallery():
    """Scrolling gallery page"""
    # Show only cards with art_crop images and a review
    reviewed_cards = cards.find({
        'imageUris.art_crop': {'$exists': True},
        'analysis': {'$exists': True}
    }).limit(60)
    return render_template('gallery.html', cards=reviewed_cards)

@app.route('/random')
def random_card_redirect():
    # Only pick from reviewed cards
    cursor = cards.aggregate([
        {'$match': {'analysis': {'$exists': True}}},
        {'$sample': {'size': 1}}
    ])
    card = next(cursor, None)
    if not card:
        return "No reviewed cards found", 404
    return redirect(f"/card/{card['uuid']}")

# Worker API endpoints
@app.route('/api/get_random_unreviewed', methods=['GET'])
def get_random_unreviewed():
    """Get a random card that hasn't been analyzed yet for worker processing"""
    try:
        # Optional query parameters
        lang = request.args.get('lang', 'en')  # Default to English cards
        limit = int(request.args.get('limit', 1))  # How many cards to return
        
        # Build query for unreviewed cards
        query = {
            'analysis': {'$exists': False},  # No analysis field means unreviewed
            'lang': lang  # Filter by language
        }
        
        # Optional: filter by specific criteria
        if request.args.get('rarity'):
            query['rarity'] = request.args.get('rarity')
        
        if request.args.get('set'):
            query['set'] = request.args.get('set')
        
        # Get count of unreviewed cards for progress tracking
        total_unreviewed = cards.count_documents(query)
        
        # Get random unreviewed card(s)
        pipeline = [
            {'$match': query},
            {'$sample': {'size': limit}}
        ]
        
        unreviewed_cards = list(cards.aggregate(pipeline))
        
        if not unreviewed_cards:
            return jsonify({
                'status': 'no_cards',
                'message': 'No unreviewed cards found matching criteria',
                'total_unreviewed': total_unreviewed
            }), 404
        
        # Return essential card data for processing
        result_cards = []
        for card in unreviewed_cards:
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
                'lang': card.get('lang', 'en'),
                'image_uris': card.get('image_uris', {}),
                'prices': card.get('prices', {})
            }
            # Remove None values to keep response clean
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'total_unreviewed': total_unreviewed,
            'returned_count': len(result_cards)
        })
        
    except Exception as e:
        logger.error(f"Error fetching random unreviewed card: {str(e)}")
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get processing statistics for workers"""
    try:
        total_cards = cards.count_documents({})
        reviewed_cards = cards.count_documents({'analysis': {'$exists': True}})
        unreviewed_cards = total_cards - reviewed_cards
        
        # Get language breakdown of unreviewed cards
        lang_pipeline = [
            {'$match': {'analysis': {'$exists': False}}},
            {'$group': {'_id': '$lang', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]
        unreviewed_by_lang = list(cards.aggregate(lang_pipeline))
        
        return jsonify({
            'status': 'success',
            'stats': {
                'total_cards': total_cards,
                'reviewed_cards': reviewed_cards,
                'unreviewed_cards': unreviewed_cards,
                'completion_percentage': round((reviewed_cards / total_cards * 100), 2) if total_cards > 0 else 0,
                'unreviewed_by_language': unreviewed_by_lang
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500

@app.route('/api/submit_work', methods=['POST'])
def submit_work():
    data = request.json
    
    if not data or 'uuid' not in data or 'analysis' not in data:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    update_fields = {}
    # Flatten card_data fields to top level
    if 'card_data' in data:
        for k, v in data['card_data'].items():
            if k != 'id':
                update_fields[k] = v
    # Always save uuid and analysis at top  level
    update_fields['uuid'] = data['uuid']
    update_fields['analysis'] = data['analysis']

    try:
        cards.update_one(
            {'uuid': data['uuid']},
            {'$set': update_fields},
            upsert=True
        )
        logger.info(f"Saved analysis for card {data['uuid']}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error saving analysis for {data['uuid']}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    pages = []
    ten_days_ago = (datetime.now()).date().isoformat()
    pages.append({
        'loc': url_for('search', _external=True),
        'lastmod': ten_days_ago
    })
    pages.append({
        'loc': url_for('random_card_redirect', _external=True),
        'lastmod': ten_days_ago
    })
    pages.append({
        'loc': url_for('gallery', _external=True),
        'lastmod': ten_days_ago
    })

    # Use your MongoDB collection directly
    for card in cards.find({}, {'uuid': 1}):
        pages.append({
            'loc': url_for('card_detail', uuid=card['uuid'], _external=True),
            'lastmod': ten_days_ago
        })

    sitemap_xml = render_template('sitemap.xml', pages=pages)
    return Response(sitemap_xml, mimetype='application/xml')

if __name__ == '__main__':
    app.run(debug=True)
