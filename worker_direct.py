#!/usr/bin/env python3
"""
Direct MongoDB Worker for MTGAbyss
=================================

A worker that connects directly to MongoDB instead of going through API endpoints.
This ensures reliable card fetching and prioritizes commanders by EDHREC rank.

Usage:
  python worker_direct.py --half-guides
  python worker_direct.py --full-guides
  python worker_direct.py --half-guides --limit 10
"""

import argparse
import time
import json
import os
import logging
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from pymongo import MongoClient

# Try to import models
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Warning: google-generativeai not installed. Gemini sections will be skipped.")

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    print("Warning: ollama not installed. Ollama sections will be skipped.")

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'mtgabyss'
CARDS_COLLECTION = 'cards'
PENDING_COLLECTION = 'pending_guides'
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-8s | %(name)-15s | %(message)s'
)
logger = logging.getLogger('DirectWorker')

def get_mongodb_client():
    """Get MongoDB client connection"""
    try:
        client = MongoClient(MONGODB_URI)
        client.admin.command('ping')
        return client
    except Exception as e:
        logger.error(f"Error connecting to MongoDB: {e}")
        return None

def get_guide_section_definitions(mode: str):
    """Get section definitions for the specified mode"""
    if mode == 'half':
        return {
            "tldr": {
                "title": "TL;DR Summary",
                "prompt": "Provide a clear and concise summary of this card's main strengths, typical uses, and impact in Commander decks. Focus on what makes this card valuable and when players should consider it.",
                "model": "llama3.1:latest"
            },
            "mechanics": {
                "title": "Card Mechanics & Interactions",
                "prompt": "Explain the card's rules and abilities in detail, including any notable edge cases or unique interactions especially relevant in Commander games. Use examples to clarify complex points.",
                "model": "llama3.1:latest"
            },
            "strategic": {
                "title": "Strategic Applications",
                "prompt": "Discuss how this card is used strategically within Commander. Cover common archetypes it fits into, its role on the battlefield, and what kind of decks or strategies benefit most.",
                "model": "llama3.1:latest"
            },
            "advanced": {
                "title": "Advanced Techniques",
                "prompt": "Detail any advanced, creative, or less-obvious uses for this card. Cover synergies, rules tricks, or interactions that strong Commander players would appreciate.",
                "model": "llama3.1:latest"
            },
            "mistakes": {
                "title": "Common Mistakes",
                "prompt": "List common mistakes or misplays players make with this card—especially in Commander. Cover timing issues, misunderstood rules, or poor synergies.",
                "model": "llama3.1:latest"
            },
            "conclusion": {
                "title": "Conclusion",
                "prompt": "Offer a final evaluation of this card's overall value in Commander decks, including when and why players should consider including it.",
                "model": "llama3.1:latest"
            }
        }
    else:
        # Full guide with 12 sections
        return {
            "tldr": {"title": "TL;DR Summary", "prompt": "Summarize this Magic: The Gathering card in 3-5 punchy sentences. Highlight its power level, main use cases, and most popular formats—especially Commander.", "model": "llama3.1:latest"},
            "mechanics": {"title": "Card Mechanics & Interactions", "prompt": "Explain this card's rules, keyword abilities, and how it functions. Include edge cases and Commander-specific quirks.", "model": "llama3.1:latest"},
            "strategic": {"title": "Strategic Applications", "prompt": "Describe how this card is used strategically in real decks. What Commander strategies benefit from it most?", "model": "llama3.1:latest"},
            "advanced": {"title": "Advanced Techniques", "prompt": "Detail advanced, creative, or less-obvious uses for this card. Cover synergies and interactions that strong players would appreciate.", "model": "llama3.1:latest"},
            "mistakes": {"title": "Common Mistakes", "prompt": "List common mistakes or misplays players make with this card—especially in Commander.", "model": "llama3.1:latest"},
            "deckbuilding": {"title": "Deckbuilding & Synergies", "prompt": "Explain how to build around this card. What Commanders and themes does it work with? Include specific synergy cards.", "model": "llama3.1:latest"},
            "format": {"title": "Format & Archetype Roles", "prompt": "Break down the card's impact in Commander and other formats. Where is it competitive, casual, or overlooked?", "model": "llama3.1:latest"},
            "scenarios": {"title": "Key Scenarios & Matchups", "prompt": "Describe scenarios where this card excels or fails. In Commander, consider multiplayer politics and board presence.", "model": "llama3.1:latest"},
            "history": {"title": "History & Notable Appearances", "prompt": "Summarize this card's history: printings, reprints, tournament presence, EDHREC stats, or iconic decks.", "model": "llama3.1:latest"},
            "flavor": {"title": "Flavor & Lore", "prompt": "Describe the card's flavor, lore, and setting. Tie in world-building elements and character backstory if known.", "model": "llama3.1:latest"},
            "budget": {"title": "Budget & Accessibility", "prompt": "Discuss the card's price, reprint status, and budget-friendliness. Suggest similar options for budget decks.", "model": "llama3.1:latest"},
            "conclusion": {"title": "Conclusion", "prompt": "Wrap up by evaluating how strong or versatile the card is—especially in Commander. Who should include it?", "model": "llama3.1:latest"}
        }

class DirectMongoWorker:
    def __init__(self, mode: str, ollama_model: str = 'llama3.1:latest', rate_limit: float = 1.0):
        self.mode = mode
        self.ollama_model = ollama_model
        self.rate_limit = rate_limit
        self.processed_count = 0
        self.mongo_client = get_mongodb_client()
        
        if not self.mongo_client:
            raise Exception("Could not connect to MongoDB")
        
        self.db = self.mongo_client[DATABASE_NAME]
        self.cards_collection = self.db[CARDS_COLLECTION]
        self.pending_collection = self.db[PENDING_COLLECTION]
        
        # Initialize Ollama
        self.ollama_available = OLLAMA_AVAILABLE
        if OLLAMA_AVAILABLE:
            logger.info(f"✅ Ollama initialized with model: {ollama_model}")
        else:
            logger.error("❌ Ollama not available")
            
        # Get section definitions
        self.section_definitions = get_guide_section_definitions(mode)
        logger.info(f"🎯 Worker initialized in {mode.upper()} mode with {len(self.section_definitions)} sections")

    def fetch_card_to_process(self) -> Optional[Dict]:
        """Fetch the next card to process, prioritizing commanders by EDHREC rank"""
        try:
            # Try main collection first (prioritize commanders, then by EDHREC rank)
            query = {'unguided': {'$ne': False}}  # Cards that need guides
            
            # Sort: commanders first, then by EDHREC rank (ascending = most popular first)
            sort_criteria = [
                ('is_commander', -1),  # Commanders first (True > False)
                ('edhrec_rank', 1)     # Lower rank = more popular
            ]
            
            card = self.cards_collection.find_one(query, sort=sort_criteria)
            
            if card:
                logger.info(f"🏠 Got card from main collection: {card.get('name')}")
                return card
            
            # Fallback to pending collection
            query = {}  # All cards in pending are unguided
            card = self.pending_collection.find_one(query, sort=sort_criteria)
            
            if card:
                logger.info(f"📦 Got card from pending collection: {card.get('name')}")
                return card
            
            logger.info("❌ No cards available for processing")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching card: {e}")
            return None

    def generate_with_ollama(self, prompt: str) -> Optional[str]:
        """Generate content using Ollama"""
        if not self.ollama_available:
            logger.error("Ollama not available")
            return None
        
        try:
            start_time = time.time()
            response = ollama.generate(
                model=self.ollama_model,
                prompt=prompt,
                stream=False
            )
            duration = time.time() - start_time
            
            if response and 'response' in response:
                content = response['response'].strip()
                logger.info(f"🤖 Generated content in {duration:.2f}s ({len(content)} chars)")
                return content
            else:
                logger.error("Empty response from Ollama")
                return None
                
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            return None

    def generate_section(self, section_key: str, section_config: Dict, card: Dict) -> Optional[Dict]:
        """Generate a single guide section"""
        section_title = section_config['title']
        section_prompt = section_config['prompt']
        
        # Build card context
        card_context_lines = [
            f"Name: {card.get('name', 'N/A')}",
            f"Mana Cost: {card.get('mana_cost', 'N/A')}",
            f"Type: {card.get('type_line', 'N/A')}",
            f"Oracle Text: {card.get('oracle_text', 'N/A')}",
            f"Power/Toughness: {card.get('power', 'N/A')}/{card.get('toughness', 'N/A')}",
            f"Rarity: {card.get('rarity', 'N/A')}",
            f"Colors: {', '.join(card.get('colors', [])) if card.get('colors') else 'N/A'}",
            f"CMC: {card.get('cmc', 'N/A')}"
        ]
        card_context = "\n".join(card_context_lines)
        
        full_prompt = (
            f"Section: {section_title}\n\n"
            f"{section_prompt}\n\n"
            f"Card details:\n{card_context}\n\n"
            "Style Guidelines:\n"
            "- Use natural paragraphs, avoid bullet points\n"
            "- Mention other cards using [[Card Name]] in double brackets\n"
            "- Do NOT mention yourself, the AI, or the analysis process\n"
            "- Do NOT use meta-commentary about the card being 'underappreciated' or 'versatile'\n"
            "- Be specific and actionable with concrete examples\n"
            "- Write for experienced Magic players\n"
        )
        
        logger.info(f"🔄 Generating section '{section_key}' for {card.get('name')}")
        content = self.generate_with_ollama(full_prompt)
        
        if content:
            return {
                'title': section_title,
                'content': content,
                'model_used': self.ollama_model,
                'generated_at': datetime.now(timezone.utc).isoformat()
            }
        else:
            logger.error(f"Failed to generate section '{section_key}'")
            return None

    def save_completed_card(self, card: Dict, sections: Dict) -> bool:
        """Save completed card with all sections to main collection"""
        try:
            # Prepare the completed card document
            completed_card = card.copy()
            completed_card.update({
                'guide_sections': sections,
                'full_guide': True,
                'unguided': False,
                'section_count': len(sections),
                'completed_at': datetime.now(timezone.utc),
                'guide_mode': self.mode
            })
            
            # Remove from pending if it was there
            if 'moved_to_pending_at' in completed_card:
                self.pending_collection.delete_one({'_id': card['_id']})
                logger.info(f"🗑️  Removed {card.get('name')} from pending collection")
                del completed_card['_id']  # Let MongoDB assign new ID
            
            # Insert/update in main collection
            result = self.cards_collection.replace_one(
                {'uuid': card.get('uuid')},
                completed_card,
                upsert=True
            )
            
            if result.upserted_id or result.modified_count > 0:
                logger.info(f"💾 Saved completed guide for {card.get('name')}")
                return True
            else:
                logger.error(f"Failed to save {card.get('name')}")
                return False
                
        except Exception as e:
            logger.error(f"Error saving card: {e}")
            return False

    def run(self, limit: int = None):
        """Main worker loop"""
        logger.info(f"🚀 Starting {self.mode.upper()} guide worker")
        logger.info(f"🎯 Will generate {len(self.section_definitions)} sections per card")
        logger.info(f"⏱️  Rate limit: {self.rate_limit}s between sections")
        
        try:
            while limit is None or self.processed_count < limit:
                card = self.fetch_card_to_process()
                if not card:
                    logger.info("😴 No cards to process, sleeping...")
                    time.sleep(30)
                    continue
                
                card_name = card.get('name', 'Unknown Card')
                is_commander = card.get('is_commander', False)
                edhrec_rank = card.get('edhrec_rank', 'N/A')
                commander_icon = "👑" if is_commander else "🃏"
                
                logger.info(f"{commander_icon} Processing: {card_name} (EDHREC: {edhrec_rank})")
                
                # Generate all sections
                sections = {}
                failed_sections = 0
                
                for section_key, section_config in self.section_definitions.items():
                    section_result = self.generate_section(section_key, section_config, card)
                    
                    if section_result:
                        sections[section_key] = section_result
                        logger.info(f"✅ Completed section: {section_key}")
                    else:
                        failed_sections += 1
                        logger.error(f"❌ Failed section: {section_key}")
                    
                    # Rate limiting
                    time.sleep(self.rate_limit)
                
                # Save completed card if we have enough sections
                min_sections = 6 if self.mode == 'half' else 12
                if len(sections) >= min_sections - 1:  # Allow 1 failed section
                    if self.save_completed_card(card, sections):
                        self.processed_count += 1
                        logger.info(f"🎉 Completed {card_name} ({self.processed_count} total)")
                    else:
                        logger.error(f"💥 Failed to save {card_name}")
                else:
                    logger.error(f"💥 Too many failed sections for {card_name} ({len(sections)}/{len(self.section_definitions)})")
                
        except KeyboardInterrupt:
            logger.info("🛑 Worker stopped by user")
        except Exception as e:
            logger.error(f"💥 Worker error: {e}")
        finally:
            if self.mongo_client:
                self.mongo_client.close()
                logger.info("🔌 MongoDB connection closed")
        
        logger.info(f"📊 Final stats: {self.processed_count} cards processed")

def main():
    parser = argparse.ArgumentParser(description='Direct MongoDB MTGAbyss Worker')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--half-guides', action='store_true', help='Generate 6-section guides')
    group.add_argument('--full-guides', action='store_true', help='Generate 12-section guides')
    parser.add_argument('--ollama-model', default='llama3.1:latest', help='Ollama model to use')
    parser.add_argument('--limit', type=int, help='Maximum number of cards to process')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Seconds between sections')
    
    args = parser.parse_args()
    
    if not OLLAMA_AVAILABLE:
        logger.error("❌ Ollama not available! Install ollama package.")
        return 1
    
    mode = 'half' if args.half_guides else 'full'
    
    try:
        worker = DirectMongoWorker(
            mode=mode,
            ollama_model=args.ollama_model,
            rate_limit=args.rate_limit
        )
        worker.run(limit=args.limit)
        return 0
    except Exception as e:
        logger.error(f"Failed to start worker: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
