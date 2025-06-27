#!/usr/bin/env python3
"""
MTG Card Analysis Worker
Fetches random cards from Scryfall, analyzes them using a multi-step LLM chain,
stores results in database, and sends notifications to Discord.
"""

import requests
import time
import logging
import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
import ollama
from pymongo import MongoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MTG_WORKER - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'llama3.1:latest')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
# DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/1387562115727888384/3ixaRfIBQFfpyxk3YrmofsbcuA5h8ar0O1Edzb0vEXEbsbYQScxVM79i24M0y1pa_5Mh'
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
SCRYFALL_API_BASE = 'https://api.scryfall.com'

# Analysis components from the original worker
ANALYSIS_COMPONENTS = [
    "play_tips",
    "mulligan_considerations", 
    "rules_clarifications",
    "combo_suggestions",
    "format_analysis",
    "synergy_analysis",
    "competitive_analysis",
    "tactical_analysis",
    "thematic_analysis",
    "historical_context",
    "art_flavor_analysis",
    "design_philosophy",
    "advanced_interactions",
    "meta_positioning",
    "budget_alternatives",
    "deck_archetypes",
    "new_player_guide",
    "sideboard_guide",
    "power_level_assessment",
    "investment_outlook"
]

class MTGAnalysisWorker:
    def __init__(self):
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client.mtg
        self.cards = self.db.cards
        logger.info(f"Worker initialized with model: {OLLAMA_MODEL}")
    
    def fetch_random_card(self) -> Optional[Dict]:
        """Fetch a random card from Scryfall API"""
        try:
            response = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
            response.raise_for_status()
            card_data = response.json()
            logger.info(f"Fetched random card: {card_data['name']} ({card_data['id']})")
            return card_data
        except Exception as e:
            logger.error(f"Error fetching random card: {e}")
            return None
    
    def create_component_prompt(self, card_data: Dict, component: str) -> str:
        """Create enhanced analysis prompts for specific components"""
        card_name = card_data.get('name', 'Unknown')
        mana_cost = card_data.get('mana_cost', 'N/A')
        type_line = card_data.get('type_line', 'N/A')
        oracle_text = card_data.get('oracle_text', 'N/A')
        power = card_data.get('power', '')
        toughness = card_data.get('toughness', '')
        
        # Build comprehensive card info
        base_info = f"""Card: {card_name}
Mana Cost: {mana_cost}
Type: {type_line}"""
        
        if power and toughness:
            base_info += f"\nPower/Toughness: {power}/{toughness}"
        
        base_info += f"\nText: {oracle_text}"
        
        # Component-specific prompts (from universal_worker_v3_clean.py)
        component_prompts = {
            'play_tips': f"""{base_info}

Provide practical gameplay tips for [[{card_name}]]:
1. Optimal timing and situations for play
2. Best synergies and combinations  
3. Key strategic considerations
4. Common mistakes to avoid

Be concise and actionable.""",

            'mulligan_considerations': f"""{base_info}

Analyze mulligan decisions for [[{card_name}]]:
1. When to keep hands with this card
2. When to mulligan it away
3. Hand quality evaluation with this card
4. Opening hand priorities

Focus on practical decision-making.""",

            'rules_clarifications': f"""{base_info}

Provide rules analysis for [[{card_name}]]:
1. Complex rules interactions and timing
2. Common misconceptions  
3. Edge cases and rulings
4. Layer system interactions if applicable

Be precise and comprehensive.""",

            'combo_suggestions': f"""{base_info}

Analyze combo potential for [[{card_name}]]:
1. Direct combo pieces and interactions
2. Synergistic packages and engines
3. Win condition setups
4. Casual and competitive combinations

Include specific card recommendations.""",

            'format_analysis': f"""{base_info}

Evaluate [[{card_name}]] across formats:
1. Standard viability and applications
2. Modern/Pioneer positioning
3. Legacy/Vintage considerations  
4. Commander/EDH role
5. Limited/Draft value

Provide format-specific insights.""",

            'synergy_analysis': f"""{base_info}

Analyze synergies for [[{card_name}]]:
1. Cards that work well with this
2. Archetype synergies and fit
3. Anti-synergies to avoid
4. Deck building considerations

Include specific card and strategy examples.""",

            'competitive_analysis': f"""{base_info}

Assess competitive viability of [[{card_name}]]:
1. Current meta positioning
2. Tournament results and trends
3. Competitive advantages/weaknesses
4. Future competitive potential

Be analytical and data-driven.""",

            'tactical_analysis': f"""{base_info}

Provide tactical guidance for [[{card_name}]]:
1. Optimal timing and sequencing
2. Key interactions and decision points
3. Play patterns and lines
4. Situational considerations

Focus on in-game tactics.""",

            'thematic_analysis': f"""{base_info}

Analyze the thematic elements of [[{card_name}]]:
1. Lore and story connections
2. Flavor text significance
3. Art and design theme coherence
4. Place in MTG's world-building

Explore narrative and artistic depth.""",

            'historical_context': f"""{base_info}

Provide historical context for [[{card_name}]]:
1. Design evolution and precedents
2. Meta impact when released
3. Power level shifts over time
4. Historical significance in MTG

Include design and competitive history.""",

            'art_flavor_analysis': f"""{base_info}

Analyze the artistic and flavor elements of [[{card_name}]]:
1. Art analysis and visual storytelling
2. Flavor text analysis and meaning
3. Creative design and aesthetic
4. Cultural and artistic references

Focus on creative and artistic elements.""",

            'design_philosophy': f"""{base_info}

Examine the design philosophy of [[{card_name}]]:
1. Design goals and intentions
2. Mechanical innovation and precedent
3. Balance considerations and constraints
4. Design space exploration

Analyze from a design perspective.""",

            'advanced_interactions': f"""{base_info}

Analyze complex interactions for [[{card_name}]]:
1. Complex edge cases and scenarios
2. Layer system interactions
3. Timing and priority issues
4. Judge call scenarios

Cover advanced rules complexity.""",

            'meta_positioning': f"""{base_info}

Analyze meta positioning for [[{card_name}]]:
1. Role in current metagame
2. Matchup considerations
3. Meta shifts that affect it
4. Adaptation potential

Focus on competitive metagame analysis.""",

            'budget_alternatives': f"""{base_info}

Provide budget analysis for [[{card_name}]]:
1. Budget-friendly alternatives
2. Cost-effective substitutions
3. Budget deck considerations
4. Performance trade-offs

Help players with limited budgets.""",

            'deck_archetypes': f"""{base_info}

Analyze deck archetype fit for [[{card_name}]]:
1. Primary deck types that want this
2. Archetype-specific roles
3. Deck building considerations
4. Alternative inclusions

Cover various competitive archetypes.""",

            'new_player_guide': f"""{base_info}

Create new player guidance for [[{card_name}]]:
1. Basic functionality explanation
2. Good/bad for beginners assessment
3. Learning opportunities
4. Common beginner mistakes

Make it accessible for new players.""",

            'sideboard_guide': f"""{base_info}

Provide sideboard guidance for [[{card_name}]]:
1. Sideboard applications and timing
2. Matchups where it's important
3. Meta-specific considerations
4. Sideboard card interactions

Focus on competitive sideboarding.""",

            'power_level_assessment': f"""{base_info}

Assess the power level of [[{card_name}]]:
1. Overall power rating and justification
2. Comparison to similar cards
3. Power level in different contexts
4. Historical power level perspective

Provide objective power assessment.""",

            'investment_outlook': f"""{base_info}

Analyze investment potential for [[{card_name}]]:
1. Current market position
2. Factors affecting value
3. Long-term outlook
4. Collectibility considerations

Focus on financial and collectible aspects."""
        }
        
        return component_prompts.get(component, f"Analyze {component} for {card_name}.")
    
    def generate_component_analysis(self, card_data: Dict, component: str) -> Optional[str]:
        """Generate analysis for a single component"""
        try:
            prompt = self.create_component_prompt(card_data, component)
            response = ollama.generate(
                model=OLLAMA_MODEL,
                prompt=prompt,
                options={'timeout': 30}
            )
            
            if response and len(response['response']) > 50:
                logger.debug(f"Generated {component} analysis ({len(response['response'])} chars)")
                return response['response']
            return None
            
        except Exception as e:
            logger.warning(f"Failed to generate {component}: {e}")
            return None
    
    def synthesize_analysis(self, card_data: Dict, components: Dict[str, str]) -> Optional[str]:
        """Synthesize all components into a fluid, comprehensive analysis"""
        try:
            card_name = card_data['name']
            
            # Format components for synthesis
            formatted_components = "\n\n".join([
                f"**{component.replace('_', ' ').title()}:**\n{analysis}"
                for component, analysis in components.items()
            ])
            
            synthesis_prompt = f"""You are writing a comprehensive MTG card guide for [[{card_name}]].

You have the following detailed analysis components:

{formatted_components}

Create a fluid, well-written comprehensive guide that incorporates these insights naturally.
- Use natural transitions between topics
- Avoid repeating section headers - make it read like one cohesive article
- Start with the most important aspects for this specific card
- Include all the key insights but organize them logically
- Write in an engaging, authoritative style
- Use [[card names]] format when referencing cards
- The guide should feel like one unified piece, not separate sections

Write a complete analysis that flows naturally from topic to topic."""

            response = ollama.generate(
                model=OLLAMA_MODEL,
                prompt=synthesis_prompt,
                options={'timeout': 90}
            )
            
            if response and len(response['response']) > 500:
                logger.info(f"Generated synthesis ({len(response['response'])} chars)")
                return response['response']
            return None
            
        except Exception as e:
            logger.error(f"Failed to synthesize analysis: {e}")
            return None
    
    def generate_tldr(self, card_data: Dict, full_analysis: str) -> Optional[str]:
        """Generate a TL;DR summary of the analysis"""
        try:
            card_name = card_data['name']
            
            tldr_prompt = f"""Create a concise TL;DR summary for [[{card_name}]] based on this comprehensive analysis:

{full_analysis[:2000]}...

Provide a brief 3-4 sentence summary that captures:
1. The card's primary use case and power level
2. Key format recommendations
3. Most important strategic considerations
4. Overall assessment

Keep it under 150 words and make it accessible to all players."""

            response = ollama.generate(
                model=OLLAMA_MODEL,
                prompt=tldr_prompt,
                options={'timeout': 30}
            )
            
            if response and len(response['response']) > 20:
                logger.info(f"Generated TL;DR ({len(response['response'])} chars)")
                return response['response']
            return None
            
        except Exception as e:
            logger.error(f"Failed to generate TL;DR: {e}")
            return None
    
    def analyze_card(self, card_data: Dict) -> Optional[Dict]:
        """Perform complete multi-step analysis of a card"""
        card_name = card_data['name']
        logger.info(f"Starting analysis for {card_name}")
        
        # Step 1: Generate individual components
        components = {}
        for component in ANALYSIS_COMPONENTS:
            logger.info(f"Generating {component} for {card_name}")
            analysis = self.generate_component_analysis(card_data, component)
            if analysis:
                components[component] = analysis
            time.sleep(1)  # Brief pause between generations
        
        if not components:
            logger.error(f"No successful components generated for {card_name}")
            return None
        
        logger.info(f"Generated {len(components)}/{len(ANALYSIS_COMPONENTS)} components for {card_name}")
        
        # Step 2: Synthesize into comprehensive analysis
        logger.info(f"Synthesizing comprehensive analysis for {card_name}")
        full_analysis = self.synthesize_analysis(card_data, components)
        if not full_analysis:
            logger.error(f"Failed to synthesize analysis for {card_name}")
            return None
        
        # Step 3: Generate TL;DR
        logger.info(f"Generating TL;DR for {card_name}")
        tldr = self.generate_tldr(card_data, full_analysis)
        if not tldr:
            logger.warning(f"Failed to generate TL;DR for {card_name}")
            tldr = f"Analysis available for [[{card_name}]]"
        
        return {
            'complete_analysis': full_analysis,
            'tldr': tldr,
            'components_generated': list(components.keys()),
            'analyzed_at': datetime.now().isoformat(),
            'model_used': OLLAMA_MODEL
        }
    
    def save_to_database(self, card_data: Dict, analysis: Dict) -> bool:
        """Save card and analysis to MongoDB"""
        try:
            # Prepare card document
            card_doc = {
                'uuid': card_data['id'],
                'name': card_data['name'],
                'mana_cost': card_data.get('mana_cost', ''),
                'type_line': card_data.get('type_line', ''),
                'oracle_text': card_data.get('oracle_text', ''),
                'power': card_data.get('power'),
                'toughness': card_data.get('toughness'),
                'imageUris': card_data.get('image_uris', {}),
                'scryfall_uri': card_data.get('scryfall_uri', ''),
                'set_name': card_data.get('set_name', ''),
                'rarity': card_data.get('rarity', ''),
                'analysis': analysis,
                'created_at': datetime.now().isoformat()
            }
            
            # Use upsert to avoid duplicates
            result = self.cards.update_one(
                {'uuid': card_data['id']},
                {'$set': card_doc},
                upsert=True
            )
            
            if result.upserted_id or result.modified_count > 0:
                logger.info(f"Saved {card_data['name']} to database")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")
            return False
    
    def send_discord_notification(self, card_data: Dict) -> bool:
        """Send Discord notification about successful analysis"""
        if not DISCORD_WEBHOOK_URL:
            logger.warning("No Discord webhook URL configured")
            return False
            
        try:
            card_name = card_data['name']
            card_url = f"{MTGABYSS_BASE_URL}/card/{card_data['id']}"
            image_url = card_data.get('image_uris', {}).get('normal', '')
            
            embed = {
                "title": f"✨ New Analysis: {card_name}",
                "description": f"Comprehensive analysis completed for [[{card_name}]]",
                "url": card_url,
                "color": 0x00FF00,  # Green
                "fields": [
                    {
                        "name": "Type",
                        "value": card_data.get('type_line', 'Unknown'),
                        "inline": True
                    },
                    {
                        "name": "Mana Cost",
                        "value": card_data.get('mana_cost', 'N/A'),
                        "inline": True
                    },
                    {
                        "name": "Set",
                        "value": card_data.get('set_name', 'Unknown'),
                        "inline": True
                    }
                ],
                "footer": {
                    "text": f"MTGAbyss • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                }
            }
            
            if image_url:
                embed["thumbnail"] = {"url": image_url}
            
            payload = {
                "embeds": [embed]
            }
            
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Sent Discord notification for {card_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")
            return False
    
    def run(self):
        """Main worker loop"""
        logger.info("Starting MTG Analysis Worker")
        
        while True:
            try:
                # Fetch random card
                card_data = self.fetch_random_card()
                if not card_data:
                    logger.error("Failed to fetch card, waiting before retry...")
                    time.sleep(30)
                    continue
                
                # Check if already analyzed
                existing = self.cards.find_one({'uuid': card_data['id']})
                if existing and existing.get('analysis'):
                    logger.info(f"Card {card_data['name']} already analyzed, skipping...")
                    time.sleep(5)
                    continue
                
                # Analyze the card
                analysis = self.analyze_card(card_data)
                if not analysis:
                    logger.error(f"Failed to analyze {card_data['name']}")
                    time.sleep(10)
                    continue
                
                # Save to database
                if self.save_to_database(card_data, analysis):
                    # Send Discord notification
                    self.send_discord_notification(card_data)
                    logger.info(f"✅ Completed processing {card_data['name']}")
                else:
                    logger.error(f"Failed to save {card_data['name']} to database")
                
                # Brief pause before next card
                time.sleep(5)
                
            except KeyboardInterrupt:
                logger.info("Worker stopped by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(30)

def main():
    """Main entry point"""
    print(f"""
🃏 MTG Card Analysis Worker
===========================
Model: {OLLAMA_MODEL}
Database: {MONGODB_URI}
Discord: {'✅ Configured' if DISCORD_WEBHOOK_URL else '❌ Not configured'}
MTGAbyss URL: {MTGABYSS_BASE_URL}

Starting continuous analysis of random MTG cards...
Press Ctrl+C to stop.
    """)
    
    worker = MTGAnalysisWorker()
    worker.run()

if __name__ == "__main__":
    main()
