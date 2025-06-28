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
def link_card_mentions(text):
    if not text:
        return ''
    # Find all [[Card Name]]
    def replacer(match):
        card_name = match.group(1)
        # Try to find the card by name (case-insensitive)
        card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {'uuid': 1})
        if card and 'uuid' in card:
            url = url_for('card_detail', uuid=card['uuid'])
            return f'<a href="{url}">{card_name}</a>'
        else:
            # Fallback: link to search page with card name
            url = url_for('search', q=card_name)
            return f'<a href="{url}">{card_name}</a>'
    # Replace all [[Card Name]] with links
    return re.sub(r'\[\[(.+?)\]\]', replacer, text)

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
        results = list(cards.find({'name': {'$regex': query, '$options': 'i'}}))
    else:
        now = time()
        # 1 hour = 3600 seconds
        if not _frontpage_cache['results'] or now - _frontpage_cache['timestamp'] > 3600:
            # Get 100 random cards with analysis
            results = list(cards.aggregate([
                {'$match': {'analysis': {'$exists': True}}},
                {'$sample': {'size': 100}}
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
    all_cards = list(cards.find({}, {'name': 1, 'uuid': 1, '_id': 0}))
    return render_template('card.html', card=card, all_cards=all_cards)

@app.route('/gallery')
def gallery():
    """Scrolling gallery page"""
    # Example: show only rare cards with normal images
    rare_cards = cards.find({'rarity': 'rare', 'imageUris.art_crop': {'$exists': True}}).limit(60)
    return render_template('gallery.html', cards=rare_cards)

@app.route('/random')
def random_card_redirect():
    card = cards.aggregate([{'$sample': {'size': 1}}]).next()
    return redirect(f"/card/{card['uuid']}")

# Worker API endpoints
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
