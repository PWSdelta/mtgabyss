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

    # No per-request cache for card name lookups (removed)

    def card_link_replacer(match):
        card_name = match.group(1)
        if current_card_name and card_name.strip().lower() == current_card_name.strip().lower():
            return card_name
        # Try to find the card by name (case-insensitive, exact match)
        card = cards.find_one({'name': {'$regex': f'^{re.escape(card_name)}$', '$options': 'i'}}, {'uuid': 1, 'imageUris.normal': 1})
        if not card or 'uuid' not in card:
            # Try partial match if no exact match
            card = cards.find_one({'name': {'$regex': re.escape(card_name), '$options': 'i'}}, {'uuid': 1, 'imageUris.normal': 1})
        if card and 'uuid' in card:
            uuid = card['uuid']
            image_url = card.get('imageUris', {}).get('normal')
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

if __name__ == '__main__':
    app.run(debug=True)
