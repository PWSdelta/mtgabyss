from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from flask_caching import Cache
from pymongo import MongoClient
import os
import logging
import markdown
import re
from datetime import datetime
from time import time


app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'filesystem', 'CACHE_DIR': './flask_cache'})
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client.mtgabyss
cards = db.cards
# Ensure indexes for fast unreviewed card queries
try:
    cards.create_index('has_analysis')
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

    # Also convert {{Card Name}} to [Card Name] for linking
    text = re.sub(r'\{\{([^}]+)\}\}', r'[\1]', text)

    # Per-request cache for card name lookups
    card_cache = {}

    def card_link_replacer(match):
        card_name = match.group(1)
        if current_card_name and card_name.strip().lower() == current_card_name.strip().lower():
            return card_name
        cache_key = card_name.strip().lower()
        card_data = card_cache.get(cache_key)
        if card_data is None:
            # Try to find the card by name (case-insensitive, exact match)
            card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {'uuid': 1, 'imageUris.normal': 1})
            if not card or 'uuid' not in card:
                # Try partial match if no exact match
                card = cards.find_one({'name': {'$regex': re.escape(card_name), '$options': 'i'}}, {'uuid': 1, 'imageUris.normal': 1})
            if card and 'uuid' in card:
                card_data = {'uuid': card['uuid'], 'image': card.get('imageUris', {}).get('normal')}
            else:
                card_data = None
            card_cache[cache_key] = card_data
        uuid = card_data['uuid'] if card_data else None
        image_url = card_data['image'] if card_data and 'image' in card_data else None
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
        # Get and sort in Python to avoid MongoDB sort on string/NaN values
        results = list(cards.find({
            'name': {'$regex': query, '$options': 'i'},
            'has_analysis': True,
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
        now = time()
        # 1 hour = 3600 seconds
        if not _frontpage_cache['results'] or now - _frontpage_cache['timestamp'] > 3600:
            # Get 30 random English cards with analysis and normal image, then sort by prices.usd descending
            results = list(cards.aggregate([
                {'$match': {'has_analysis': True}},
                {'$sample': {'size': 30}}
            ]))
            # Sort in Python since $sample is used
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
    # Get 5 most recent analyzed cards (excluding this one), cached for 2 hours
    @cache.cached(timeout=7200, key_prefix=lambda: f"recent_cards_ex_{uuid}")
    def get_recent_cards():
        return list(cards.find(
            {'has_analysis': True, 'uuid': {'$ne': uuid}}
        ).sort([('analysis.analyzed_at', -1)]).limit(5))
    recent_cards = get_recent_cards()
    # Get 6 random cards with analysis and image, not this one, for recommendations
    rec_cards = list(cards.aggregate([
        {'$match': {
            'analysis': {'$exists': True},
            'imageUris.normal': {'$exists': True},
            'uuid': {'$ne': uuid}
        }},
        {'$sample': {'size': 6}}
    ]))

    # --- Cards Mentioned in This Review ---
    mentioned_cards = []
    if card and card.get('analysis'):
        mention_names = extract_mentions_from_guide(card['analysis'])
        if mention_names:
            # Only include cards with analysis (support both new "content" and old "long_form")
            found_cards = list(cards.find({
                'name': {'$in': mention_names},
                '$or': [
                    {'analysis.content': {'$exists': True, '$ne': ''}},
                    {'analysis.long_form': {'$exists': True, '$ne': ''}}
                ]
            }, {'uuid': 1, 'name': 1, 'imageUris.normal': 1, 'prices': 1}))
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

    # --- Most Expensive Cards (with analysis, cached) ---
    @cache.cached(timeout=6*60*60, key_prefix='expensive_cards')
    def get_expensive_cards():
        pipeline = [
            {'$match': {
                '$or': [
                    {'analysis.content': {'$exists': True, '$ne': ''}},
                    {'analysis.long_form': {'$exists': True, '$ne': ''}}
                ],
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
        return list(cards.aggregate(pipeline))

    expensive_cards = get_expensive_cards()
    
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
    # Show only cards with art_crop images and a review
    reviewed_cards = cards.find({
        'analysis': {'$exists': True}
    }).limit(60)
    return render_template('gallery.html', cards=reviewed_cards)

@app.route('/random')
def random_card_redirect():
    # Only pick from reviewed cards
    cursor = cards.aggregate([
        {'$match': {'has_analysis': True}},
        {'$sample': {'size': 1}}
    ])
    card = next(cursor, None)
    if not card:
        return "No reviewed cards found", 404
    return redirect(f"/card/{card['uuid']}")

@app.route('/clear_cache')
def clear_cache():
    _frontpage_cache['results'] = []
    _frontpage_cache['timestamp'] = 0
    return "Cache cleared"

# Worker API endpoints
@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get processing statistics for workers"""
    try:
        total_cards = cards.count_documents({})
        reviewed_cards = cards.count_documents({'has_analysis': True})
        unreviewed_cards = total_cards - reviewed_cards
        
        return jsonify({
            'status': 'success',
            'stats': {
                'total_cards': total_cards,
                'reviewed_cards': reviewed_cards,
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
    """Get a random card that hasn't been analyzed yet for worker processing"""
    try:
        # Optional query parameters
        limit = int(request.args.get('limit', 1))  # How many cards to return
        
        # Build query for unreviewed cards
        query = {'has_analysis': False}
        
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


# --- BATCH SUBMIT WORK ENDPOINT ---
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
        update_fields['has_analysis'] = True
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
            results.append({'uuid': entry['uuid'], 'status': 'ok'})
        except Exception as e:
            logger.error(f"Error saving analysis for {entry['uuid']}: {str(e)}")
            results.append({'uuid': entry['uuid'], 'status': 'error', 'message': str(e)})
    return jsonify({'status': 'ok', 'results': results})


# --- SITEMAP LOGIC ---
from math import ceil

SITEMAP_CARD_CHUNK = 50000

@app.route('/sitemap.xml', methods=['GET'])
def sitemap_index():
    """Sitemap index referencing all card sitemaps and static sitemap"""
    # Count total cards
    total_cards = cards.count_documents({})
    num_card_sitemaps = ceil(total_cards / SITEMAP_CARD_CHUNK)
    sitemap_urls = []
    # Add static sitemap
    sitemap_urls.append(url_for('sitemap_static', _external=True))
    # Add card sitemaps
    for i in range(1, num_card_sitemaps + 1):
        sitemap_urls.append(url_for('sitemap_cards', n=i, _external=True))
    return Response(render_template('sitemap_index.xml', sitemap_urls=sitemap_urls), mimetype='application/xml')

@app.route('/sitemap-static.xml', methods=['GET'])
def sitemap_static():
    """Sitemap for static and non-card pages"""
    ten_days_ago = (datetime.now()).date().isoformat()
    pages = [
        {'loc': url_for('search', _external=True), 'lastmod': ten_days_ago},
        {'loc': url_for('random_card_redirect', _external=True), 'lastmod': ten_days_ago},
        {'loc': url_for('gallery', _external=True), 'lastmod': ten_days_ago},
    ]
    return Response(render_template('sitemap_static.xml', pages=pages), mimetype='application/xml')

@app.route('/sitemap-cards-<int:n>.xml', methods=['GET'])
def sitemap_cards(n):
    """Sitemap for a chunk of card detail pages (50k per sitemap)"""
    ten_days_ago = (datetime.now()).date().isoformat()
    skip = (n - 1) * SITEMAP_CARD_CHUNK
    card_cursor = cards.find({}, {'uuid': 1}).skip(skip).limit(SITEMAP_CARD_CHUNK)
    pages = [
        {'loc': url_for('card_detail', uuid=card['uuid'], _external=True), 'lastmod': ten_days_ago}
        for card in card_cursor
    ]
    return Response(render_template('sitemap_cards.xml', pages=pages), mimetype='application/xml')

# Helper functions for backward compatibility with guide formats
def is_sectioned_guide(analysis_data):
    """Check if this is a new sectioned guide or old monolithic format"""
    return (analysis_data and 
            isinstance(analysis_data.get('sections'), dict) and
            analysis_data.get('guide_version', '').startswith('2.'))

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

if __name__ == '__main__':
    app.run(debug=True)
