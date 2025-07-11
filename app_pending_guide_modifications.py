"""
Suggested modifications to app.py to support pending_guide collection

Add this after your existing database setup (around line 116):
"""

# Add to database collections setup:
pending_guide = db.pending_guide  # Cards without any guide sections

# Add a new API endpoint to fetch from pending_guide when main queue is empty:

@app.route('/api/get_pending_card', methods=['GET'])
def get_pending_card():
    """Get a card from pending_guide collection for initial guide generation"""
    try:
        limit = int(request.args.get('limit', 1))
        
        # Get most popular EDHREC card from pending_guide collection
        pipeline = [
            {'$match': {
                'edhrec_rank': {'$exists': True, '$ne': None, '$type': 'number', '$gte': 1},
                'lang': 'en'  # English cards only
            }},
            {'$sort': {'edhrec_rank': 1}},  # Most popular first
            {'$limit': limit}
        ]
        
        available_cards = list(pending_guide.aggregate(pipeline))
        
        if not available_cards:
            return jsonify({
                'status': 'no_cards',
                'message': 'No cards found in pending_guide collection'
            }), 404
        
        # Convert to expected format
        result_cards = []
        for card in available_cards:
            card_data = {
                'uuid': card.get('uuid'),
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
                'source_collection': 'pending_guide'
            }
            # Remove None values
            card_data = {k: v for k, v in card_data.items() if v is not None}
            result_cards.append(card_data)
        
        return jsonify({
            'status': 'success',
            'cards': result_cards,
            'source': 'pending_guide'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Modify the existing get_random_unreviewed endpoint to fallback to pending_guide:

@app.route('/api/get_random_unreviewed', methods=['GET'])
def get_random_unreviewed():
    """Get cards for processing, with fallback to pending_guide collection"""
    try:
        # ... existing logic ...
        
        if not available_cards:
            # Fallback: try to get cards from pending_guide collection
            logger.info("No cards in main collection, checking pending_guide...")
            
            pending_pipeline = [
                {'$match': {
                    'edhrec_rank': {'$exists': True, '$ne': None, '$type': 'number', '$gte': 1},
                    'lang': 'en'
                }},
                {'$sort': {'edhrec_rank': 1}},
                {'$limit': limit}
            ]
            
            pending_cards = list(pending_guide.aggregate(pending_pipeline))
            
            if pending_cards:
                logger.info(f"Found {len(pending_cards)} cards in pending_guide collection")
                
                # Move the selected card back to main collection for processing
                selected_card = pending_cards[0]
                
                # Remove from pending_guide and add to cards collection
                pending_guide.delete_one({'_id': selected_card['_id']})
                
                # Remove MongoDB _id before inserting into cards collection
                if '_id' in selected_card:
                    del selected_card['_id']
                    
                cards.insert_one(selected_card)
                
                logger.info(f"Moved card '{selected_card.get('name')}' from pending_guide to cards for processing")
                
                # Return the card for processing
                return jsonify({
                    'status': 'success',
                    'cards': [selected_card],
                    'source': 'moved_from_pending'
                })
            
            return jsonify({
                'status': 'no_cards',
                'message': 'No cards found in either collection'
            }), 404
        
        # ... rest of existing logic ...
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
