from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from pymongo import MongoClient
import os
import logging
import markdown
from datetime import datetime

app = Flask(__name__)
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client.mtg
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

# Web routes
@app.route('/')
def search():
    """Card search page"""
    query = request.args.get('q', '')
    if query:
        results = cards.find({'name': {'$regex': query, '$options': 'i'}})
    else:
        results = cards.find().limit(20)
    return render_template('search.html', cards=results, query=query)

@app.route('/card/<uuid>')
def card_detail(uuid):
    """Card detail page"""
    card = cards.find_one({'uuid': uuid})
    return render_template('card.html', card=card)

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
