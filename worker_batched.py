#!/usr/bin/env python3
"""
MTG Card Guide Generator (Batched Version)
Optimized version that batches multiple sections into single API calls
for reduced costs and improved efficiency.
"""

import os
import sys
import time
import requests
import logging
import argparse
from datetime import datetime
from typing import Dict, Optional, List
import google.generativeai as genai
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER_BATCHED - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Config ---
LLM_MODEL = os.getenv('LLM_MODEL', 'gemini-1.5-flash')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TEST_MODE = os.getenv('TEST_MODE', 'true').lower() == 'true'
MAX_CARDS_PER_RUN = int(os.getenv('MAX_CARDS_PER_RUN', '5'))
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')

# Initialize Gemini
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found!")
    sys.exit(1)

# Initialize Gemini with existing API
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(LLM_MODEL)
logger.info(f"Initialized Gemini with model: {LLM_MODEL}")

# MTG Card Guide Section Structure (same as before)
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
    """Generate all sections using batched requests for cost optimization"""
    guide_sections = {}
    total_start = time.time()
    
    # Group sections into batches (3-4 sections per batch for optimal cost/quality balance)
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

def fetch_single_unreviewed_card() -> Optional[Dict]:
    """Fetch a single unreviewed card from API"""
    try:
        api_url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed?lang=en&limit=1'
        resp = requests.get(api_url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        
        if data['status'] == 'no_cards':
            logger.info("No more unreviewed cards available!")
            return None
            
        if data['status'] != 'success' or not data.get('cards'):
            logger.error(f"API error: {data.get('message', 'Unknown error')}")
            return None
            
        card = data['cards'][0]
        card['id'] = card['uuid']
        card['image_uris'] = card.get('image_uris', {})
            
        logger.info(f"Fetched card: {card['name']}. Remaining: {data['total_unreviewed']:,}")
        return card
        
    except Exception as e:
        logger.error(f"Error fetching card from API: {e}")
        return None

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

def save_analysis_to_database(payload: dict) -> bool:
    """Submit single analysis to API"""
    try:
        api_url = f"{MTGABYSS_BASE_URL}/api/submit_work"
        resp = requests.post(api_url, json=[payload], timeout=60)
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("status") == "ok":
            logger.info(f"✅ Analysis saved for {payload.get('card_data', {}).get('name', 'Unknown card')}")
            return True
        else:
            logger.error(f"❌ API error saving analysis: {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ API submit error: {e}")
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
        # If UUID ends with -XX (language code), remove it
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
                    "value": f"• **Sections**: {len(guide_sections)}/12\n• **Time**: {processing_time:.1f}s\n• **Mode**: Batched (75% cost savings)",
                    "inline": True
                },
                {
                    "name": "🔗 Links",
                    "value": f"[View Analysis]({card_url})\n[MTGAbyss Home]({MTGABYSS_PUBLIC_URL})",
                    "inline": True
                }
            ],
            "footer": {
                "text": f"MTGAbyss • {LLM_MODEL} • Batched Mode"
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
            "username": "MTGAbyss Worker",
            "avatar_url": "https://cdn.discordapp.com/attachments/123456789/logo.png"  # Optional
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
🚀 MTG Card Guide Generator - SEQUENTIAL MODE 🚀
=================================================
Model: {LLM_MODEL} (Google Gemini)
Database: {MONGODB_URI}
MTGAbyss URL: {MTGABYSS_BASE_URL}

💰 OPTIMIZED PROCESSING:
- Sequential processing: One card at a time
- Immediate saving after each card
- Robust error handling per card
- Test Mode: {'✅ ENABLED' if TEST_MODE else '❌ DISABLED'} (Max {MAX_CARDS_PER_RUN} cards)

⚡ PROCESSING OPTIMIZATIONS:
- 3 sections per batch (optimal cost/quality balance)
- 4 API calls per card instead of 12
- Smart section parsing and validation
- Immediate Discord notifications

Starting sequential worker...
""")

    cards_processed = 0
    
    while True:
        if TEST_MODE and cards_processed >= MAX_CARDS_PER_RUN:
            logger.info(f"🛑 TEST MODE: Reached limit of {MAX_CARDS_PER_RUN} cards. Stopping.")
            break
            
        # Fetch single card
        card = fetch_single_unreviewed_card()
        if not card:
            logger.info("No cards available, waiting 60 seconds...")
            time.sleep(60)
            continue

        card_start = time.time()
        logger.info(f"🚀 PROCESSING: {card['name']} [{cards_processed + 1}/{MAX_CARDS_PER_RUN if TEST_MODE else '∞'}]")
        
        # Generate complete guide using batched approach
        guide_sections = generate_complete_guide_batched(card)
        if not guide_sections:
            logger.error(f"❌ Failed to generate guide for {card['name']}")
            continue

        # Format complete guide
        complete_guide = format_guide_for_display(guide_sections)
        
        card_elapsed = time.time() - card_start
        logger.info(f"✅ {card['name']} guide generated in {card_elapsed:.1f}s ({len(complete_guide)} chars, {len(guide_sections)} sections)")

        # Prepare payload
        analysis_dict = {
            "long_form": complete_guide,
            "sections": guide_sections,
            "analyzed_at": datetime.now().isoformat(),
            "model_used": LLM_MODEL,
            "guide_version": "2.1_sequential"
        }
        
        payload = {
            "uuid": card.get("uuid", card.get("id")),
            "analysis": analysis_dict,
            "category": "mtg",
            "card_data": card
        }
        
        # Submit analysis immediately
        if save_analysis_to_database(payload):
            # Send Discord notification only after successful save
            send_discord_notification(card, guide_sections, card_elapsed)
            cards_processed += 1
            logger.info(f"🎉 {card['name']} fully processed and saved!")
        else:
            logger.error(f"❌ Failed to save analysis for {card['name']}, skipping Discord notification")

        # Small delay between cards to be respectful
        time.sleep(2)

if __name__ == "__main__":
    main()
