#!/usr/bin/env python3
"""
MTGAbyss Unified Worker - Supports both Gemini and Ollama LLMs
==============================================================

A unified worker that can process Magic: The Gathering cards using either:
- Google Gemini API (cloud)
- Local Ollama models

Features:
- CLI interface with argparse
- Batch processing
- Rate limiting for APIs
- Discord notifications
- Progress tracking
- Flexible model selection
"""

import argparse
import time
import requests
import json
import os
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any
import sys

# Try to import Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Warning: google-generativeai not installed. Gemini mode will not be available.")

# Try to import Ollama
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    print("Warning: ollama not installed. Ollama mode will not be available.")

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'http://localhost:5000')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def simple_log(message: str):
    """Simple logging function for important messages"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

class UnifiedWorker:
    gemini_total_time = 0.0
    gemini_total_calls = 0
    gemini_total_tokens = 0
    gemini_total_cost = 0.0

    def __init__(self, provider: str, model: str, batch_size: int = 5, rate_limit: float = 1.0):
        self.provider = provider.lower()
        self.model = model
        self.batch_size = batch_size
        self.rate_limit = rate_limit
        self.processed_count = 0
        
        # Initialize the selected provider
        if self.provider == 'gemini':
            self._init_gemini()
        elif self.provider == 'ollama':
            self._init_ollama()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def _init_gemini(self):
        """Initialize Gemini API"""
        if not GEMINI_AVAILABLE:
            raise ImportError("google-generativeai package not installed")
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        genai.configure(api_key=GEMINI_API_KEY)
        self.client = genai.GenerativeModel(self.model)
        logger.info(f"Initialized Gemini with model: {self.model}")
    
    def _init_ollama(self):
        """Initialize Ollama client"""
        if not OLLAMA_AVAILABLE:
            raise ImportError("ollama package not installed")
        
        # Test Ollama connection
        try:
            ollama.list()
            logger.info(f"Initialized Ollama with model: {self.model}")
        except Exception as e:
            raise ConnectionError(f"Could not connect to Ollama: {e}")
    
    def log_gemini_usage(self, prompt: str, response, elapsed: float):
        # Try to extract token/cost info if available, using attribute access for usage_metadata
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
        total_tokens = input_tokens + output_tokens
        # Gemini 1.5 pricing (as of July 2025):
        # Flash: $0.35/M input, $1.05/M output; Pro: $3.50/M input, $10.50/M output
        # https://cloud.google.com/vertex-ai/generative-ai/pricing
        model = self.model.lower() if hasattr(self, 'model') else ''
        if 'pro' in model:
            input_rate = 0.0000035
            output_rate = 0.0000105
        else:
            input_rate = 0.00000035
            output_rate = 0.00000105
        input_cost = input_tokens * input_rate
        output_cost = output_tokens * output_rate
        total_cost = input_cost + output_cost
        UnifiedWorker.gemini_total_time += elapsed
        UnifiedWorker.gemini_total_calls += 1
        UnifiedWorker.gemini_total_tokens += total_tokens
        UnifiedWorker.gemini_total_cost += total_cost
        logger.info(f"Gemini call: {input_tokens} in, {output_tokens} out tokens, {elapsed:.2f}s, ${total_cost:.6f}")
        logger.info(f"Gemini aggregate: {UnifiedWorker.gemini_total_calls} calls, {UnifiedWorker.gemini_total_tokens} tokens, {UnifiedWorker.gemini_total_time:.2f}s, ${UnifiedWorker.gemini_total_cost:.4f}")

    def generate_text(self, prompt: str, timeout: int = 300) -> Optional[str]:
        """Generate text using the configured provider, with detailed Gemini logging."""
        try:
            if self.provider == 'gemini':
                import time as _time
                t0 = _time.perf_counter()
                response = self.client.generate_content(prompt)
                elapsed = _time.perf_counter() - t0
                self.log_gemini_usage(prompt, response, elapsed)
                return response.text if hasattr(response, 'text') and response.text else None
            elif self.provider == 'ollama':
                import time as _time
                t0 = _time.perf_counter()
                response = ollama.generate(
                    model=self.model,
                    prompt=prompt,
                    options={'timeout': timeout}
                )
                elapsed = _time.perf_counter() - t0
                logger.info(f"Ollama call: {elapsed:.2f}s")
                return response.get('response', '')
        except Exception as e:
            logger.error(f"Error generating text with {self.provider}: {e}")
            return None
    
    def fetch_unreviewed_cards(self, limit: int) -> List[Dict]:
        """Fetch unreviewed cards from the API"""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed'
            params = {'limit': limit}
            
            response = requests.get(url, params=params, timeout=60)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    return data.get('cards', [])
                else:
                    logger.warning(f"API returned non-success status: {data.get('message')}")
                    return []
            elif response.status_code == 404:
                logger.info("No unreviewed cards available")
                return []
            else:
                logger.error(f"API error: {response.status_code} - {response.text}")
                return []
        
        except Exception as e:
            logger.error(f"Error fetching cards: {e}")
            return []
    
#     def create_analysis_prompt(self, card: Dict) -> str:
#         """Create the analysis prompt for a card"""
#         return f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]].

# Include:
# - TL;DR summary (2-3 sentences max)
# - Detailed card mechanics and interactions
# - Strategic uses, combos, and synergies  
# - Deckbuilding roles and archetypes
# - Format viability and competitive context
# - Rules interactions and technical notes
# - Art, flavor, and historical context
# - Key Points Summary

# Use natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 3357 words. Do not mention yourself or the analysis process.

# Card details:
# Name: {card['name']}
# Mana Cost: {card.get('mana_cost', 'N/A')}
# Type: {card.get('type_line', 'N/A')}
# Text: {card.get('oracle_text', 'N/A')}
# {f'P/T: {card.get("power")}/{card.get("toughness")}' if card.get('power') else ''}
# Set: {card.get('set', 'N/A')}
# Rarity: {card.get('rarity', 'N/A')}"""

#     def create_polish_prompt(self, card: Dict, raw_analysis: str) -> str:
#         """Create the polishing prompt for raw analysis"""
#         return f"""Polish and enhance this Magic: The Gathering card analysis for [[{card['name']}]]. 

# Improve:
# - Flow and readability
# - Technical accuracy
# - Strategic depth
# - Format coverage
# - Card interactions and synergies

# Keep the same structure and content depth. Use markdown headers. Reference other cards in [[double brackets]]. Write in natural paragraphs (no bullet points). Maintain at least 3357 words.

# Original analysis:
# {raw_analysis}"""

#     def create_native_language_prompt(self, card: Dict, language: str) -> str:
#         """Create prompt for native language analysis"""
#         return f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]] in {language} (the card's printed language).

# Include:
# - TL;DR summary
# - Detailed card mechanics and interactions
# - Strategic uses, combos, and synergies
# - Deckbuilding roles and archetypes
# - Format viability and competitive context
# - Rules interactions and technical notes
# - Art, flavor, and historical context
# - Summary of key points (use a different section title for this)

# Use natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 3357 words. Do not mention yourself or the analysis process.
# Wrap up with a conclusion summary

# Card details:
# Name: {card['name']}
# Mana Cost: {card.get('mana_cost', 'N/A')}
# Type: {card.get('type_line', 'N/A')}
# Text: {card.get('oracle_text', 'N/A')}
# {f'P/T: {card.get("power")}/{card.get("toughness")}' if card.get('power') else ''}"""

    def get_section_prompts(self, card: Dict) -> Dict[str, Dict[str, str]]:
        """Return a dict of {section_key: {title, prompt}} for all guide sections, with global style instructions."""
        style_instructions = (
            "- Use natural paragraphs, but you may use bullet points and tables sparingly if it improves clarity.\n"
            "- Liberally mention other cards using [[Card Name]] in double brackets.\n"
            "- Do NOT mention yourself, the AI, or the analysis process.\n"
            "- Do NOT end every section with phrases like 'in conclusion' or similar.\n"
        )
        sections = [
            ("tldr", "TL;DR Summary", "Write a 2-3 sentence summary of the card's main strengths, weaknesses, and archetypes. Be concise."),
            ("mechanics", "Card Mechanics & Interactions", "Explain the card's rules, mechanics, and any unique interactions. Include edge cases and rules notes."),
            ("strategic", "Strategic Applications", "Describe how this card is used strategically. What decks/archetypes want it? What roles does it fill?"),
            ("deckbuilding", "Deckbuilding & Synergies", "Discuss deckbuilding considerations, synergies, and combos. What cards work well with it?"),
            ("format", "Format Analysis", "Analyze the card's viability in different formats (Standard, Historic, Commander, etc). Where does it shine?"),
            ("scenarios", "Gameplay Scenarios", "Give 2-3 example in-game scenarios where this card is impactful. Use specific board states if possible."),
            ("history", "Historical Context", "Discuss the card's history, reprints, and impact on the game over time."),
            ("flavor", "Flavor & Design", "Comment on the card's flavor, art, and design. How does it fit the set/theme?"),
            ("budget", "Budget & Accessibility", "Is this card budget-friendly? Are there cheaper alternatives?"),
            ("advanced", "Advanced Techniques", "Describe advanced or less obvious uses, tricks, or interactions."),
            ("mistakes", "Common Mistakes", "List common mistakes or misplays involving this card."),
            ("conclusion", "Conclusion", "Summarize the card's overall value and when to play it.")
        ]
        prompts = {}
        for key, title, desc in sections:
            prompts[key] = {
                "title": title,
                "prompt": f"""Section: {title}\n\n{desc}\n\n{style_instructions}\nCard details:\nName: {card['name']}\nMana Cost: {card.get('mana_cost', 'N/A')}\nType: {card.get('type_line', 'N/A')}\nText: {card.get('oracle_text', 'N/A')}\n{f'P/T: {card.get('power')}/{card.get('toughness')}' if card.get('power') else ''}\nSet: {card.get('set', 'N/A')}\nRarity: {card.get('rarity', 'N/A')}\n"""
            }
        return prompts

    def process_card(self, card: Dict) -> Optional[Dict]:
        """Process a single card and return the payload with sectioned analysis. Also returns cost if Gemini is used."""
        card_name = card.get('name', 'Unknown Card')
        card_uuid = card.get('uuid') or card.get('id')
        logger.info(f"Generating sectioned analysis for {card_name} (UUID: {card_uuid}) using {self.provider}:{self.model}")
        start_time = time.time()

        # Track Gemini cost for this card
        gemini_cost_before = UnifiedWorker.gemini_total_cost

        # Generate sectioned guide
        section_prompts = self.get_section_prompts(card)
        guide_sections = {}
        for section_key, section_info in section_prompts.items():
            logger.info(f"Generating section '{section_key}' for {card_name}")
            content = self.generate_text(section_info["prompt"])
            if not content or len(content.strip()) < 50:
                logger.warning(f"Section '{section_key}' for {card_name} is too short or empty.")
                content = "(Section not available.)"
            guide_sections[section_key] = {
                "title": section_info["title"],
                "content": content.strip()
            }

        elapsed = time.time() - start_time
        logger.info(f"Sectioned analysis completed for {card_name} in {elapsed:.2f} seconds.")

        # Native language sectioned guide (optional, only if not English)
        native_guide_sections = None
        native_lang = card.get('lang', 'en')
        if native_lang != 'en':
            logger.info(f"Generating native language ({native_lang}) sectioned guide for {card_name}")
            native_guide_sections = {}
            for section_key, prompt in section_prompts.items():
                native_prompt = prompt.replace("in-depth analysis guide for the Magic: The Gathering card", f"in-depth analysis guide for the Magic: The Gathering card in {native_lang}")
                content = self.generate_text(native_prompt)
                if not content or len(content.strip()) < 50:
                    content = "(Section not available.)"
                native_guide_sections[section_key] = {
                    "title": prompt.split('\n')[1].strip() if '\n' in prompt else section_key.capitalize(),
                    "content": content.strip()
                }

        # Prepare payload
        analysis_dict = {
            "sections": guide_sections,
            "analyzed_at": datetime.now().isoformat(),
            "model_used": f"{self.provider}:{self.model}"
        }
        guide_meta = {
            "type": "sectioned",
            "section_count": len(guide_sections),
            "analyzed_at": analysis_dict["analyzed_at"],
            "model_used": analysis_dict["model_used"]
        }
        if native_guide_sections:
            analysis_dict["native_language_sections"] = native_guide_sections

        payload = {
            "uuid": card_uuid,
            "analysis": analysis_dict,
            "guide_meta": guide_meta,
            "category": "mtg",
            "card_data": card,
            "has_full_content": True
        }

        # Remove any has_analysis or similar legacy fields to avoid confusion
        if "has_analysis" in payload:
            del payload["has_analysis"]
        if "has_analysis" in card:
            del card["has_analysis"]

        # Ensure image_uris compatibility
        if 'image_uris' in card:
            card['imageUris'] = card['image_uris']

        # Log payload structure for debugging
        logger.info(f"Prepared payload for {card_name} (UUID: {card_uuid}): has_full_content={payload.get('has_full_content')}, keys={list(payload.keys())}")

        # Calculate Gemini cost for this card
        gemini_cost_after = UnifiedWorker.gemini_total_cost
        card_cost = gemini_cost_after - gemini_cost_before if self.provider == 'gemini' else 0.0
        payload["gemini_cost_usd"] = card_cost

        return payload
    
    def save_batch_to_database(self, batch_payload: List[Dict]) -> bool:
        """Save a batch of analyses to the database"""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/submit_work'
            response = requests.post(
                url,
                json=batch_payload,
                headers={'Content-Type': 'application/json'},
                timeout=120
            )
            if response.status_code == 200:
                data = response.json()
                # Accept both list and dict-with-results
                if isinstance(data, list):
                    results = data
                elif isinstance(data, dict) and 'results' in data and isinstance(data['results'], list):
                    results = data['results']
                else:
                    logger.error(f"Unexpected API response: {data}")
                    return False
                success_count = sum(1 for result in results if isinstance(result, dict) and result.get('status') == 'ok')
                logger.info(f"Successfully saved {success_count}/{len(batch_payload)} analyses")
                return success_count > 0
            else:
                logger.error(f"Database save failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error saving batch to database: {e}")
            return False
    
    def send_discord_notification(self, card: Dict):
        """Send Discord notification for completed analysis"""
        if not DISCORD_WEBHOOK_URL:
            return
        
        try:
            card_url = f"{MTGABYSS_BASE_URL}/card/{card.get('uuid')}"
            image_url = card.get('image_uris', {}).get('normal', '')
            
            embed = {
                "title": f"✅ Analysis Complete: {card.get('name')}",
                "url": card_url,
                "color": 0x00ff00,
                "fields": [
                    {"name": "Type", "value": card.get('type_line', 'N/A'), "inline": True},
                    {"name": "Set", "value": card.get('set', 'N/A'), "inline": True},
                    {"name": "Model", "value": f"{self.provider}:{self.model}", "inline": True}
                ],
                "timestamp": datetime.now().isoformat()
            }
            
            if image_url:
                embed["thumbnail"] = {"url": image_url}
            
            payload = {"embeds": [embed]}
            
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")
    
    def get_stats(self) -> Dict:
        """Get processing statistics"""
        try:
            response = requests.get(f'{MTGABYSS_BASE_URL}/api/stats', timeout=30)
            if response.status_code == 200:
                return response.json().get('stats', {})
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
        return {}
    
    def print_stats(self):
        """Print current processing statistics"""
        stats = self.get_stats()
        if stats:
            print(f"📊 Database Status:")
            print(f"   Total cards: {stats.get('total_cards', 'Unknown'):,}")
            print(f"   Reviewed: {stats.get('reviewed_cards', 'Unknown'):,}")
            print(f"   Unreviewed: {stats.get('unreviewed_cards', 'Unknown'):,}")
            print(f"   Progress: {stats.get('completion_percentage', 0):.1f}%")
            print()
    
    def run(self, limit: Optional[int] = None, show_output: bool = True):
        """Main worker loop with aggregate and per-batch Gemini cost reporting."""
        print(f"""
MTGAbyss Unified Worker
======================
Provider: {self.provider}
Model: {self.model}
Batch Size: {self.batch_size}
Rate Limit: {self.rate_limit}s between batches
Limit: {limit if limit else 'unlimited'}
Database: {MTGABYSS_BASE_URL}
Discord: {'✅' if DISCORD_WEBHOOK_URL else '❌'}

Press Ctrl+C to stop.
""")
        
        self.print_stats()
        
        try:
            while True:
                if limit is not None and self.processed_count >= limit:
                    simple_log(f"Reached processing limit of {limit} cards. Exiting.")
                    break
                
                round_start = time.time()
                batch_gemini_cost_before = UnifiedWorker.gemini_total_cost
                
                # Fetch cards
                remaining_limit = None
                if limit is not None:
                    remaining_limit = min(self.batch_size, limit - self.processed_count)
                else:
                    remaining_limit = self.batch_size
                
                cards_batch = self.fetch_unreviewed_cards(remaining_limit)
                
                if not cards_batch:
                    simple_log("No unreviewed cards available, waiting 60 seconds...")
                    time.sleep(60)
                    continue
                
                # Process cards
                batch_payload = []
                batch_card_costs = []
                for card in cards_batch:
                    if limit is not None and self.processed_count >= limit:
                        break
                    
                    payload = self.process_card(card)
                    if payload:
                        batch_payload.append(payload)
                        self.processed_count += 1
                        # Track per-card cost if Gemini
                        if self.provider == 'gemini':
                            card_cost = payload.get("gemini_cost_usd", 0.0)
                            batch_card_costs.append(card_cost)
                        # Show analysis output if requested
                        if show_output:
                            print("\n" + "="*80)
                            print(f"Analysis for: {card.get('name')}")
                            print("="*80)
                            analysis = payload.get('analysis', {}).get('long_form', '')
                            print(analysis[:500] + "..." if len(analysis) > 500 else analysis)
                            print("="*80 + "\n")
                
                # Save batch
                if batch_payload:
                    if self.save_batch_to_database(batch_payload):
                        # Send notifications
                        for i, card in enumerate(cards_batch[:len(batch_payload)]):
                            self.send_discord_notification(card)
                        
                        elapsed = time.time() - round_start
                        # Calculate and print batch Gemini cost
                        batch_gemini_cost_after = UnifiedWorker.gemini_total_cost
                        batch_cost = batch_gemini_cost_after - batch_gemini_cost_before if self.provider == 'gemini' else 0.0
                        if self.provider == 'gemini':
                            simple_log(f"Batch Gemini API cost: ${batch_cost:.6f} (aggregate: ${UnifiedWorker.gemini_total_cost:.4f})")
                            if batch_card_costs:
                                for idx, card_cost in enumerate(batch_card_costs):
                                    simple_log(f"  Card {idx+1}: ${card_cost:.6f}")
                        simple_log(f"Completed batch of {len(batch_payload)} cards in {elapsed:.2f} seconds")
                    else:
                        simple_log("Failed to save batch to database")
                else:
                    simple_log("No analyses generated in this batch")
                
                # Rate limiting
                if self.rate_limit > 0:
                    time.sleep(self.rate_limit)
        
        except KeyboardInterrupt:
            simple_log(f"Worker stopped by user. Processed {self.processed_count} cards total.")
        except Exception as e:
            logger.error(f"Worker error: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(
        description="MTGAbyss Unified Worker - Process MTG cards with Gemini or Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use Gemini with default model
  python unified_worker.py --provider gemini
  
  # Use Ollama with specific model
  python unified_worker.py --provider ollama --model llama3.1:8b
  
  # Process only 10 cards with batch size 2
  python unified_worker.py --provider gemini --limit 10 --batch-size 2
  
  # Use custom rate limiting
  python unified_worker.py --provider ollama --rate-limit 2.0
        """
    )
    
    parser.add_argument('--provider', 
                       choices=['gemini', 'ollama'], 
                       required=True,
                       help='LLM provider to use')
    
    parser.add_argument('--model', 
                       default=None,
                       help='Model name (default: gemini-1.5-flash for Gemini, llama3.1:8b for Ollama)')
    
    parser.add_argument('--limit', 
                       type=int, 
                       default=None,
                       help='Maximum number of cards to process')
    
    parser.add_argument('--batch-size', 
                       type=int, 
                       default=5,
                       help='Number of cards to process per batch (default: 5)')
    
    parser.add_argument('--rate-limit', 
                       type=float, 
                       default=1.0,
                       help='Seconds to wait between batches (default: 1.0)')
    
    parser.add_argument('--quiet', 
                       action='store_true',
                       help='Hide analysis output (only show progress)')
    
    args = parser.parse_args()
    
    # Set default models
    if args.model is None:
        if args.provider == 'gemini':
            args.model = 'gemini-1.5-flash'
        elif args.provider == 'ollama':
            args.model = 'llama3.1:8b'
    
    # Validate provider availability
    if args.provider == 'gemini' and not GEMINI_AVAILABLE:
        print("Error: Gemini provider selected but google-generativeai package not installed.")
        print("Install with: pip install google-generativeai")
        sys.exit(1)
    
    if args.provider == 'ollama' and not OLLAMA_AVAILABLE:
        print("Error: Ollama provider selected but ollama package not installed.")
        print("Install with: pip install ollama")
        sys.exit(1)
    
    if args.provider == 'gemini' and not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY environment variable not set.")
        sys.exit(1)
    
    try:
        worker = UnifiedWorker(
            provider=args.provider,
            model=args.model,
            batch_size=args.batch_size,
            rate_limit=args.rate_limit
        )
        
        worker.run(limit=args.limit, show_output=not args.quiet)
    
    except Exception as e:
        logger.error(f"Failed to start worker: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
