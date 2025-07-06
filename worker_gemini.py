#!/usr/bin/env python3
"""
MTG Card Analysis Worker (Gemini 4x3 Batched Version)
Fetches unreviewed cards, generates comprehensive analysis using Gemini
with 4x3 batching (4 API calls, 3 sections each), saves to database.
"""

import os
import time
import requests
import logging
from datetime import datetime
from typing import Dict, Optional, List
import google.generativeai as genai
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()

# --- Config ---
LLM_MODEL = os.getenv('LLM_MODEL', 'gemini-1.5-flash')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')
SCRYFALL_API_BASE = 'https://api.scryfall.com'

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER_GEMINI - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def simple_log(msg):
    print(msg)

# Initialize Gemini
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(LLM_MODEL)
logger.info(f"Initialized Gemini with model: {LLM_MODEL}")

# MTG Card Guide Section Structure (12 sections, grouped into 4 batches of 3)
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

def fetch_random_card_fallback() -> Optional[Dict]:
    """Fallback to Scryfall if our API is unavailable"""
    try:
        resp = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
        resp.raise_for_status()
        card = resp.json()
        simple_log(f"Fallback - Fetched card: {card['name']} ({card['id']})")
        card['imageUris'] = card.get('image_uris', {})
        return card
    except Exception as e:
        simple_log(f"Fallback error fetching card: {e}")
        return None

def fetch_unreviewed_card_batch(batch_size=100) -> Optional[list]:
    """Fetch a batch of unreviewed cards from our MTGAbyss API"""
    try:
        api_url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed?lang=en&limit={batch_size}'
        resp = requests.get(api_url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data['status'] == 'no_cards':
            simple_log("No more unreviewed cards available!")
            return None
        if data['status'] != 'success' or not data.get('cards'):
            simple_log(f"API error: {data.get('message', 'Unknown error')}")
            return None
        cards_batch = data['cards']
        for card in cards_batch:
            card['id'] = card['uuid']
            card['image_uris'] = card.get('image_uris', {})
        simple_log(f"Fetched {len(cards_batch)} unreviewed cards. Progress: {data['total_unreviewed']:,} unreviewed cards remaining")
        return cards_batch
    except Exception as e:
        simple_log(f"Error fetching unreviewed card batch from API: {e}")
        simple_log("Falling back to Scryfall random card...")
        fallback = fetch_random_card_fallback()
        return [fallback] if fallback else None

def create_batched_prompt(card: Dict, sections_batch: List[str]) -> str:
    """Create a single prompt that generates multiple sections at once"""
    card_name = card['name']
    
    card_details = f"""
Card Details:
Name: {card_name}
Mana Cost: {card.get('mana_cost', 'N/A')}
Type: {card.get('type_line', 'N/A')}
Text: {card.get('oracle_text', 'N/A')}
{f"P/T: {card.get('power')}/{card.get('toughness')}" if 'power' in card else ''}
Set: {card.get('set_name', 'Unknown')}
"""
    
    instructions = f"""
You are analyzing the Magic: The Gathering card [[{card_name}]] and need to write {len(sections_batch)} different sections.

For each section, write 300-500 words in a natural, engaging style using markdown formatting.
IMPORTANT: Always wrap ALL Magic card names in [[double brackets]] for proper frontend parsing.
Reference other Magic cards frequently to provide context and comparisons.
Do not use bullet points. Write for experienced Magic players.
Do not mention yourself or the analysis process.

Please provide each section with a clear header using the exact format:
## SECTION: [section title]
[content]

Generate the following sections:
"""
    
    for section_key in sections_batch:
        section = GUIDE_SECTIONS[section_key]
        instructions += f"\n## SECTION: {section['title']}\n{section['prompt']}\n"
    
    return f"{instructions}\n{card_details}"

def parse_batched_response(response_text: str, sections_batch: List[str]) -> Dict[str, Dict]:
    """Parse a batched response into individual sections"""
    sections = {}
    
    # Split by section headers
    parts = response_text.split('## SECTION: ')
    
    for part in parts[1:]:  # Skip first empty part
        lines = part.strip().split('\n', 1)
        if len(lines) < 2:
            continue
            
        title = lines[0].strip()
        content = lines[1].strip()
        
        # Find matching section key by title
        section_key = None
        for key in sections_batch:
            if GUIDE_SECTIONS[key]['title'] == title:
                section_key = key
                break
        
        if section_key and content:
            sections[section_key] = {
                "title": title,
                "content": content,
                "language": "en"
            }
    
    return sections

def generate_complete_guide_batched(card: Dict) -> Dict[str, Dict]:
    """Generate all sections using 4x3 batched requests for cost optimization"""
    guide_sections = {}
    total_start = time.time()
    
    # Group sections into batches (3 sections per batch for optimal cost/quality balance)
    section_keys = list(GUIDE_SECTIONS.keys())
    batch_size = 3  # Optimal balance between cost and quality
    batches = [section_keys[i:i + batch_size] for i in range(0, len(section_keys), batch_size)]
    
    logger.info(f"Generating guide for {card['name']} in {len(batches)} batches")
    
    for i, sections_batch in enumerate(batches):
        batch_start = time.time()
        prompt = create_batched_prompt(card, sections_batch)
        
        logger.info(f"Batch {i+1}/{len(batches)}: Generating {len(sections_batch)} sections for {card['name']}")
        
        try:
            response = gemini_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    candidate_count=1,
                    max_output_tokens=2000,
                    temperature=0.7,
                )
            )
            response_text = response.candidates[0].content.parts[0].text
            
            # Parse the batched response
            batch_sections = parse_batched_response(response_text, sections_batch)
            guide_sections.update(batch_sections)
            
            batch_elapsed = time.time() - batch_start
            logger.info(f"Batch {i+1} completed in {batch_elapsed:.2f}s ({len(batch_sections)} sections)")
            
            # Small delay between batches to be respectful to the API
            if i < len(batches) - 1:
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error generating batch {i+1} for {card['name']}: {e}")
            continue
    
    total_elapsed = time.time() - total_start
    logger.info(f"Complete batched guide generation took {total_elapsed:.2f} seconds for {card['name']} ({len(guide_sections)} sections)")
    
    return guide_sections

def format_guide_for_display(guide_sections: Dict[str, Dict]) -> str:
    """Format sectioned guide for display"""
    formatted_content = []
    
    for section_key in GUIDE_SECTIONS.keys():
        if section_key in guide_sections:
            section_data = guide_sections[section_key]
            title = section_data["title"]
            content = section_data["content"]
            formatted_content.append(f"## {title}\n\n{content}\n")
    
    return "\n".join(formatted_content)

def save_batch_to_database(card_analysis_batch: list) -> bool:
    """Submit a batch of analyses to the API in one request"""
    try:
        api_url = f"{MTGABYSS_BASE_URL}/api/submit_work"
        resp = requests.post(api_url, json=card_analysis_batch, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "ok":
            simple_log(f"Submitted batch of {len(card_analysis_batch)} analyses to API")
            return True
        else:
            simple_log(f"API error: {resp.text}")
            return False
    except Exception as e:
        simple_log(f"API batch submit error: {e}")
        return False

def send_discord_notification(card: Dict, guide_sections: Dict, processing_time: float):
    """Send Discord notification for completed card analysis"""
    if not DISCORD_WEBHOOK_URL:
        return
    
    try:
        card_name = card['name']
        card_set = card.get('set_name', 'Unknown Set')
        
        # Simple, foolproof UUID cleaning - remove everything after the last dash if it looks like a language code
        card_uuid = card['uuid']
        parts = card_uuid.split('-')
        if len(parts) > 1 and len(parts[-1]) == 2 and parts[-1].isalpha():
            card_uuid = '-'.join(parts[:-1])
        
        card_url = f"{MTGABYSS_PUBLIC_URL}/card/{card_uuid}"
        image_url = card.get('image_uris', {}).get('normal', '')
        
        # Create embed
        embed = {
            "title": f"📊 New Analysis: {card_name}",
            "description": f"Comprehensive guide generated for **{card_name}** from *{card_set}*",
            "url": card_url,
            "color": 0x0099ff,  # Blue color
            "fields": [
                {
                    "name": "⚡ Processing Stats",
                    "value": f"• **Sections**: {len(guide_sections)}/12\n• **Time**: {processing_time:.1f}s\n• **Mode**: Gemini 4x3 Batched",
                    "inline": True
                },
                {
                    "name": "🔗 Links",
                    "value": f"[View Analysis]({card_url})\n[MTGAbyss Home]({MTGABYSS_PUBLIC_URL})",
                    "inline": True
                }
            ],
            "footer": {
                "text": f"MTGAbyss • {LLM_MODEL} • Gemini 4x3"
            },
            "timestamp": datetime.now().isoformat()
        }
        
        # Add card image if available
        if image_url:
            embed["thumbnail"] = {"url": image_url}
        
        # Add card details
        card_details = []
        if card.get('mana_cost'):
            card_details.append(f"**Cost**: {card['mana_cost']}")
        if card.get('type_line'):
            card_details.append(f"**Type**: {card['type_line']}")
        if card.get('power') and card.get('toughness'):
            card_details.append(f"**P/T**: {card['power']}/{card['toughness']}")
        
        if card_details:
            embed["fields"].insert(0, {
                "name": "📋 Card Details",
                "value": "\n".join(card_details),
                "inline": False
            })
        
        payload = {
            "embeds": [embed],
            "username": "MTGAbyss Gemini Worker",
            "avatar_url": "https://cdn.discordapp.com/attachments/123456789/logo.png"
        }
        
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 204:
            logger.info(f"✅ Discord notification sent for {card_name}")
        else:
            logger.warning(f"Discord notification failed: {resp.status_code}")
            
    except Exception as e:
        logger.error(f"Error sending Discord notification: {e}")

def main():
    print(f"""
MTG Card Analysis Worker - Gemini 4x3 Batched Mode
==================================================
Model: {LLM_MODEL} (Google Gemini)
Database: {MONGODB_URI}
Discord: {'✅' if DISCORD_WEBHOOK_URL else '❌'}
MTGAbyss URL: {MTGABYSS_BASE_URL}
Card Source: {MTGABYSS_BASE_URL}/api/get_random_unreviewed

💰 COST ESTIMATION:
- Gemini 4x3 Batching: ~$0.004 per card (vs $0.015 unbatched)
- 75% cost reduction through intelligent batching!
- 4 API calls per card instead of 12

⚡ PROCESSING OPTIMIZATIONS:
- 3 sections per batch (optimal cost/quality balance)
- 4 API calls per card total
- Smart section parsing and validation
- Discord notifications enabled

This worker will process unreviewed cards from your database in batches.
Press Ctrl+C to stop.
""")
    
    try:
        resp = requests.get(f'{MTGABYSS_BASE_URL}/api/stats', timeout=60)
        if resp.status_code == 200:
            stats = resp.json().get('stats', {})
            print(f"📊 Database Status:")
            print(f"   Total cards: {stats.get('total_cards', 'Unknown'):,}")
            print(f"   Reviewed: {stats.get('reviewed_cards', 'Unknown'):,}")
            print(f"   Unreviewed: {stats.get('unreviewed_cards', 'Unknown'):,}")
            print(f"   Progress: {stats.get('completion_percentage', 0):.1f}%")
            print()
        else:
            print("⚠️  Could not fetch stats from MTGAbyss API")
    except Exception as e:
        print(f"⚠️  API connection test failed: {e}")
        print("Will fallback to Scryfall random cards if needed.")

    print("Starting Gemini 4x3 worker batch loop...\n")
    BATCH_SIZE = 5
    while True:
        round_start = time.time()
        cards_batch = fetch_unreviewed_card_batch(BATCH_SIZE)
        if not cards_batch:
            simple_log("No unreviewed cards available, waiting 60 seconds...")
            time.sleep(60)
            continue

        batch_payload = []
        successful_cards = 0
        
        for card in cards_batch:
            card_start = time.time()
            logger.info(f"🚀 GEMINI 4x3: Processing {card['name']} ({card.get('set_name', 'Unknown')})")
            
            # Generate complete sectioned guide using 4x3 batching
            guide_sections = generate_complete_guide_batched(card)
            if not guide_sections:
                logger.error(f"❌ Failed to generate guide sections for {card['name']}")
                continue

            # Format the complete guide for storage
            complete_guide = format_guide_for_display(guide_sections)
            
            card_elapsed = time.time() - card_start
            logger.info(f"✅ {card['name']} completed in {card_elapsed:.1f}s ({len(complete_guide)} chars, {len(guide_sections)} sections)")

            # Add a newline between each card analysis for clarity
            print("\n" + "="*80 + "\n")
            print(f"Analysis for card: {card['name']}")
            print(complete_guide)
            print("\n" + "="*80 + "\n")

            # Prepare payload for batch submit
            card_category = 'mtg'
            analysis_dict = {
                "long_form": complete_guide,
                "sections": guide_sections,  # Store individual sections
                "analyzed_at": datetime.now().isoformat(),
                "model_used": LLM_MODEL,
                "guide_version": "2.1_gemini_4x3"
            }
            
            payload = {
                "uuid": card.get("uuid", card.get("id")),
                "analysis": analysis_dict,
                "category": card_category,
                "card_data": card
            }
            card['imageUris'] = card.get('image_uris', {})
            batch_payload.append(payload)
            successful_cards += 1

            # Send Discord notification
            send_discord_notification(card, guide_sections, card_elapsed)

        if batch_payload:
            if save_batch_to_database(batch_payload):
                elapsed = time.time() - round_start
                cards_per_minute = (successful_cards / elapsed) * 60
                logger.info(f"🚀 GEMINI 4x3 BATCH COMPLETE: {successful_cards}/{len(cards_batch)} cards in {elapsed:.1f}s ({cards_per_minute:.1f} cards/min)")
        else:
            simple_log("No analyses to submit for this batch.")

if __name__ == "__main__":
    main()
