#!/usr/bin/env python3
"""
MTGAbyss All-Local Worker - Optimized for cost savings and consistent performance
===============================================================================

A smart worker that uses different LOCAL models for different sections:
- All processing done locally with Ollama
- Zero API costs - save budget for translation services
- Consistent performance without rate limits
- Strategic model assignment based on section requirements

Features:
- Model specialization by section type (all local)
- Zero API costs (perfect for budget allocation to translation)
- No rate limits or API dependencies
- Quality optimization (right local model for right task)
- Fallback mechanisms
- Progress tracking across models
"""

import argparse
import time
import requests
import json
import os
import logging
from datetime import datetime, UTC
from typing import List, Dict, Optional, Any
import sys

# Try to import Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Warning: google-generativeai not installed. Gemini sections will be skipped.")

# Try to import Ollama
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    print("Warning: ollama not installed. Ollama sections will be skipped.")

# Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'https://mtgabyss.com')
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

# MODEL ASSIGNMENT STRATEGY
# Assign models based on what they do best
MODEL_ASSIGNMENTS = {
    # GEMINI - Best for creative, subjective, and complex analysis
    'gemini': {
        'model': 'gemini-1.5-flash',
        'sections': [
            'tldr',           # Quick summaries (Gemini is great at concise writing)
            'flavor',         # Creative flavor analysis
            'history',        # Historical context and storytelling
            'advanced',       # Complex strategic thinking
            'conclusion'      # Final synthesis and recommendations
        ],
        'description': 'Creative, subjective, and synthesis tasks'
    },
    
    # OLLAMA - Best for technical, factual, and structured analysis
    'ollama': {
        'model': 'llama3.1:latest',  # Better reasoning than 3.2:3b
        'sections': [
            'mechanics',      # Technical rules and interactions
            'strategic',      # Tactical applications
            'deckbuilding',   # Structured deck advice
            'format',         # Format viability analysis
            'scenarios',      # Concrete gameplay examples
            'budget',         # Factual pricing and alternatives
            'mistakes'        # Common pitfalls (factual)
        ],
        'description': 'Technical, factual, and structured tasks'
    }
}

def simple_log(message: str):
    """Simple logging function for important messages"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

class MixedModelWorker:
    # Define the display order for sections (can be changed as needed)
    SECTION_DISPLAY_ORDER = [
        "tldr", "mechanics", "strategic", "deckbuilding", "format",
        "scenarios", "history", "flavor", "budget", "advanced", "mistakes", "conclusion"
    ]

    # Optionally, map section keys to display names for the frontend (for even more flexibility)
    SECTION_DISPLAY_MAP = {
        "tldr": "TL;DR Summary",
        "mechanics": "Card Mechanics & Interactions",
        "strategic": "Strategic Applications",
        "deckbuilding": "Deckbuilding & Synergies",
        "format": "Format Analysis",
        "scenarios": "Gameplay Scenarios",
        "history": "Historical Context",
        "flavor": "Flavor & Design",
        "budget": "Budget & Accessibility",
        "advanced": "Advanced Techniques",
        "mistakes": "Common Mistakes",
        "conclusion": "Conclusion"
    }

    def __init__(self, gemini_model: str = 'gemini-1.5-flash', ollama_model: str = 'llama3.1:latest', rate_limit: float = 1.0):
        self.gemini_model = gemini_model
        self.ollama_model = ollama_model
        self.rate_limit = rate_limit
        self.processed_count = 0
        
        # Update model assignments with user-specified models
        MODEL_ASSIGNMENTS['gemini']['model'] = gemini_model
        MODEL_ASSIGNMENTS['ollama']['model'] = ollama_model
        
        # Initialize available providers
        self.gemini_client = None
        self.ollama_available = OLLAMA_AVAILABLE
        
        if GEMINI_AVAILABLE and GEMINI_API_KEY:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                self.gemini_client = genai.GenerativeModel(gemini_model)
                logger.info(f"Initialized Gemini with model: {gemini_model}")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini: {e}")
                self.gemini_client = None
        
        if OLLAMA_AVAILABLE:
            logger.info(f"Initialized Ollama with model: {ollama_model}")
        
        # Create section assignment map
        self.section_to_model = {}
        for provider, config in MODEL_ASSIGNMENTS.items():
            for section in config['sections']:
                self.section_to_model[section] = provider
    
    def get_model_for_section(self, section: str, section_config: Dict = None) -> str:
        """Get the assigned model provider for a section"""
        if section_config and 'model' in section_config:
            model_name = section_config['model']
            # Determine provider based on model name
            if model_name.startswith('gemini'):
                return 'gemini'
            else:
                return 'ollama'  # Assume all non-Gemini models are Ollama
        
        # Fallback to the old mapping system if no model specified in config
        return self.section_to_model.get(section, 'ollama')  # Default to ollama
    
    def get_model_name_for_section(self, section_config: Dict) -> str:
        """Get the specific model name for a section"""
        return section_config.get('model', self.ollama_model)
    
    def generate_with_gemini(self, prompt: str, model_name: str = None, temperature: float = 0.7) -> Optional[str]:
        """Generate content using Gemini with a specific model and temperature"""
        if not self.gemini_client:
            logger.warning("Gemini not available, cannot generate content")
            return None
        actual_model = model_name or self.gemini_model
        try:
            # Create a new client if model differs from initialized one
            if actual_model != self.gemini_model:
                client = genai.GenerativeModel(actual_model)
            else:
                client = self.gemini_client
            start_time = time.time()
            response = client.generate_content(prompt, generation_config={"temperature": temperature})
            duration = time.time() - start_time
            if response and response.text:
                logger.info(f"Gemini ({actual_model}) call: {duration:.2f}s, temp={temperature}")
                return response.text.strip()
            else:
                logger.warning(f"Gemini ({actual_model}) returned empty response")
                return None
        except Exception as e:
            logger.error(f"Gemini ({actual_model}) generation error: {e}")
            return None
    
    def generate_with_ollama(self, prompt: str, model_name: str = None, temperature: float = 0.7) -> Optional[str]:
        """Generate content using Ollama with a specific model and temperature"""
        if not self.ollama_available:
            logger.warning("Ollama not available, cannot generate content")
            return None
        actual_model = model_name or self.ollama_model
        try:
            start_time = time.time()
            response = ollama.generate(
                model=actual_model,
                prompt=prompt,
                stream=False,
                options={"temperature": temperature}
            )
            duration = time.time() - start_time
            if response and 'response' in response:
                logger.info(f"Ollama ({actual_model}) call: {duration:.2f}s, temp={temperature}")
                return response['response'].strip()
            else:
                logger.warning(f"Ollama ({actual_model}) returned empty response")
                return None
        except Exception as e:
            logger.error(f"Ollama ({actual_model}) generation error: {e}")
            return None
    
    def generate_section(self, section_key: str, section_config: Dict, card: Dict, prior_sections: Optional[Dict] = None, card_temp_config: Optional[Dict] = None) -> Optional[Dict]:
        """Generate a single section using the appropriate model. If section is 'conclusion', include prior 11 sections as context. Supports min/max temperature per section/card."""
        import random
        section_title = section_config['title']
        section_prompt = section_config['prompt']

        # Get the assigned model provider and specific model name
        model_provider = self.get_model_for_section(section_key, section_config)
        model_name = self.get_model_name_for_section(section_config)

        # --- Temperature selection logic ---
        # Priority: card_temp_config > section_config > default
        min_temp = 0.6
        max_temp = 0.9
        # Allow per-section config
        if 'min_temperature' in section_config:
            min_temp = section_config['min_temperature']
        if 'max_temperature' in section_config:
            max_temp = section_config['max_temperature']
        # Allow per-card config (overrides section)
        if card_temp_config:
            if 'min_temperature' in card_temp_config:
                min_temp = card_temp_config['min_temperature']
            if 'max_temperature' in card_temp_config:
                max_temp = card_temp_config['max_temperature']
        # Clamp and randomize
        min_temp = max(0.0, min(min_temp, 2.0))
        max_temp = max(min_temp, min(max_temp, 2.0))
        temperature = round(random.uniform(min_temp, max_temp), 3)

        # For conclusion, add context from the first 11 sections if available
        context_text = ""
        if section_key == "conclusion" and prior_sections:
            context_text = "\n\n---\n\n".join([
                f"## {self.SECTION_DISPLAY_MAP.get(k, k)}\n\n{prior_sections[k]['content'].strip()}"
                for k in self.SECTION_DISPLAY_ORDER if k != "conclusion" and k in prior_sections and prior_sections[k].get('content')
            ])
            if context_text:
                context_text = f"\n\nANALYSIS OF PREVIOUS SECTIONS:\n{context_text}\n\n"

        # Build the prompt
        full_prompt = f"Section: {section_title}\n\n{section_prompt}\n\nCard details:\nName: {card['name']}\nMana Cost: {card.get('mana_cost', 'N/A')}\nType: {card.get('type_line', 'N/A')}\nText: {card.get('oracle_text', 'N/A')}\n{f'P/T: {card.get('power')}/{card.get('toughness')}' if card.get('power') else ''}\nRarity: {card.get('rarity', 'N/A')}\n{context_text}Style Guidelines:\n- Use natural paragraphs, bullet points and tables sparingly\n- Liberally mention other cards using [[Card Name]] in double brackets\n- Do NOT mention yourself, the AI, or the analysis process\n- Do NOT end with phrases like 'in conclusion'\n- Be specific and actionable\n"

        logger.info(f"Generating section '{section_key}' for {card['name']} using {model_name} (temp={temperature})")

        # Generate with the appropriate model
        content = None
        if model_provider == 'gemini':
            content = self.generate_with_gemini(full_prompt, model_name, temperature=temperature)
        elif model_provider == 'ollama':
            content = self.generate_with_ollama(full_prompt, model_name, temperature=temperature)

        if content:
            return {
                'title': section_title,
                'content': content,
                'model_used': model_name,
                'temperature_used': temperature,
                'generated_at': datetime.now(UTC).isoformat()
            }
        else:
            logger.error(f"Failed to generate section '{section_key}' with {model_name}")
            return None
    
    def fetch_card_to_process(self) -> Optional[Dict]:
        """Fetch a card that needs processing"""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed'
            params = {'limit': 1}
            response = requests.get(url, params=params, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('cards'):
                    priority_info = data.get('selection_info', {})
                    selection_type = priority_info.get('type', 'unknown')
                    logger.info(f"Got card from unified endpoint (type: {selection_type})")
                    return data['cards'][0]
            elif response.status_code == 404:
                logger.info("No cards available for processing")
                return None
            else:
                logger.warning(f"Unexpected response: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching card: {e}")
            return None
    
    def get_section_definitions(self) -> Dict:
        """Get all section definitions with specific model assignments and min/max temperature for each section."""
        return {
            "tldr": {
                "title": "TL;DR Summary",
                "prompt": "Write a 2-3 sentence summary of the card's main strengths, weaknesses, and archetypes. Be concise.",
                "model": "llama3.1:latest",
                "min_temperature": 0.2,
                "max_temperature": 0.7
            },
            "mechanics": {
                "title": "Card Mechanics & Interactions",
                "prompt": "Explain the card's rules, mechanics, and any unique interactions. Include edge cases and rules notes.",
                "model": "llama3.1:latest",
                "min_temperature": 0.15,
                "max_temperature": 0.5
            },
            "deckbuilding": {
                "title": "Deckbuilding & Synergies",
                "prompt": "Discuss deckbuilding considerations, synergies, and combos. What cards work well with it? Ensure to mention other cards using [[Card Name]] format.",
                "model": "llama3.1:latest",
                "min_temperature": 0.3,
                "max_temperature": 0.8
            },
            "scenarios": {
                "title": "Gameplay Scenarios",
                "prompt": "Give 2-3 example in-game scenarios where this card is impactful. Use specific board states if possible.",
                "model": "llama3.1:latest",
                "min_temperature": 0.4,
                "max_temperature": 0.9
            },
            "flavor": {
                "title": "Flavor & Design",
                "prompt": "Comment on the card's flavor, art, and design. How does it fit the set/theme?",
                "model": "llama3.1:latest",
                "min_temperature": 0.5,
                "max_temperature": 1.0
            },
            "history": {
                "title": "Historical Context",
                "prompt": "Discuss the card's history, reprints, and impact on the game over time.",
                "model": "qwen2.5:7b",
                "min_temperature": 0.3,
                "max_temperature": 0.8
            },
            "budget": {
                "title": "Budget & Accessibility",
                "prompt": "Is this card budget-friendly? Are there cheaper alternatives?",
                "model": "qwen2.5:7b",
                "min_temperature": 0.2,
                "max_temperature": 0.6
            },
            "strategic": {
                "title": "Strategic Applications",
                "prompt": "Describe how this card is used strategically. What decks/archetypes want it? What roles does it fill?",
                "model": "qwen2.5:7b",
                "min_temperature": 0.3,
                "max_temperature": 0.85
            },
            "mistakes": {
                "title": "Common Mistakes",
                "prompt": "List common mistakes or misplays involving this card.",
                "model": "mistral:7b-instruct",
                "min_temperature": 0.2,
                "max_temperature": 0.7
            },
            "format": {
                "title": "Format Analysis",
                "prompt": "Analyze the card's viability in Commander format mainly, and others where applicable. Where does it shine?",
                "model": "mistral:7b-instruct",
                "min_temperature": 0.2,
                "max_temperature": 0.7
            },
            "advanced": {
                "title": "Advanced Techniques",
                "prompt": "Describe advanced or less obvious uses, tricks, or interactions.",
                "model": "qwen2.5:7b",
                "min_temperature": 0.4,
                "max_temperature": 0.95
            },
            "conclusion": {
                "title": "Conclusion",
                "prompt": "Summarize the card's overall value and when to play it.",
                "model": "llama3.1:latest",
                "min_temperature": 0.2,
                "max_temperature": 0.7
            }
        }
    
    def process_card_mixed_model(self, card: Dict, card_temp_config: Optional[Dict] = None) -> Optional[Dict]:
        """Process a card using mixed models for different sections, serially, with no context chaining between sections. Supports per-card min/max temperature config."""
        card_name = card.get('name', 'Unknown Card')
        card_uuid = card.get('uuid')

        # --- Count mentions of this card in all guides ---
        try:
            import re
            from pymongo import MongoClient
            MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
            DB_NAME = os.getenv('MTGABYSS_DB', 'mtgabyss')
            client = MongoClient(MONGODB_URI)
            db = client[DB_NAME]
            cards_collection = db.cards
            mentions_histogram = db.mentions_histogram
            # Find all guides that mention this card (case-insensitive, in any section)
            regex = re.compile(r'\[\[\s*' + re.escape(card_name) + r'\s*\]\]|\[\s*' + re.escape(card_name) + r'\s*\]', re.IGNORECASE)
            mention_count = 0
            for guide in cards_collection.find({'analysis': {'$exists': True}}):
                analysis = guide.get('analysis', {})
                content = ''
                if 'sections' in analysis and isinstance(analysis['sections'], dict):
                    for section in analysis['sections'].values():
                        if isinstance(section, dict) and 'content' in section:
                            content += section['content'] + ' '
                elif 'content' in analysis:
                    content = analysis['content']
                elif 'long_form' in analysis:
                    content = analysis['long_form']
                if regex.search(content):
                    mention_count += 1
            # Update mentions_histogram for this card
            if card_uuid:
                mentions_histogram.update_one(
                    {'uuid': card_uuid},
                    {
                        '$set': {
                            'card_name': card_name,
                            'last_mentioned': datetime.utcnow(),
                        },
                        '$inc': {'mention_count': mention_count},
                        '$setOnInsert': {
                            'first_mentioned': datetime.utcnow(),
                            'created_at': datetime.utcnow()
                        }
                    },
                    upsert=True
                )
            logger.info(f"#mentions for {card_name}: {mention_count}")
        except Exception as e:
            logger.warning(f"Could not count mentions for {card_name}: {e}")

        logger.info(f"Starting mixed-model analysis for {card_name} (UUID: {card_uuid})")
        start_time = time.time()

        sections = {}
        section_definitions = self.get_section_definitions()

        gemini_sections = 0
        ollama_sections = 0
        failed_sections = 0

        # Process sections in display order, serially, with no context chaining
        for section_key in self.SECTION_DISPLAY_ORDER:
            if section_key not in section_definitions:
                continue
            section_config = section_definitions[section_key]
            model_provider = self.get_model_for_section(section_key, section_config)
            # Check if the required model is available
            if model_provider == 'gemini' and not self.gemini_client:
                logger.warning(f"Skipping section '{section_key}' - Gemini not available")
                failed_sections += 1
                continue
            elif model_provider == 'ollama' and not self.ollama_available:
                logger.warning(f"Skipping section '{section_key}' - Ollama not available")
                failed_sections += 1
                continue

            # No prior_sections/context chaining
            section_result = self.generate_section(section_key, section_config, card, prior_sections=None, card_temp_config=card_temp_config)

            if section_result:
                sections[section_key] = section_result
                if model_provider == 'gemini':
                    gemini_sections += 1
                else:
                    ollama_sections += 1
                time.sleep(self.rate_limit)
            else:
                failed_sections += 1

        if not sections:
            logger.error(f"Failed to generate any sections for {card_name}")
            return None

        # Create the complete analysis payload
        total_time = time.time() - start_time

        analysis_data = {
            'sections': sections,
            'analyzed_at': datetime.now(UTC).isoformat(),
            'guide_version': '3.0_mixed_model',
            'model_strategy': {
                'gemini_sections': gemini_sections,
                'ollama_sections': ollama_sections,
                'failed_sections': failed_sections,
                'total_sections': len(section_definitions),
                'gemini_model': self.gemini_model,
                'ollama_model': self.ollama_model
            },
            'processing_time': total_time
        }

        # Create formatted content by assembling sections
        formatted_content = self.format_sections_for_display(sections, section_definitions)
        if formatted_content:
            analysis_data['content'] = formatted_content

        # Set status: public if 6 or more sections are present
        status = 'public' if len(sections) >= 6 else 'draft'
        payload = {
            'uuid': card_uuid,
            'analysis': analysis_data,
            'guide_meta': {
                'type': 'mixed_model',
                'version': '3.0',
                'sections_generated': len(sections),
                'models_used': f"gemini:{gemini_sections}, ollama:{ollama_sections}"
            },
            'category': 'mtg',
            'card_data': card,
            'status': status
        }

        logger.info(f"Mixed-model analysis completed for {card_name} in {total_time:.2f}s")
        logger.info(f"  Gemini sections: {gemini_sections}, Ollama sections: {ollama_sections}, Failed: {failed_sections}")

        return payload
    
    def format_sections_for_display(self, sections: Dict, section_definitions: Dict) -> str:
        """Format sections into a complete guide for display in a fixed order using SECTION_DISPLAY_ORDER and SECTION_DISPLAY_MAP"""
        ordered_content = []
        for section_key in self.SECTION_DISPLAY_ORDER:
            if section_key in sections:
                section_data = sections[section_key]
                # Use display map if present, else fallback to section definition title
                section_title = self.SECTION_DISPLAY_MAP.get(section_key, section_data.get('title', section_definitions[section_key]['title']))
                section_content = section_data.get('content', '')
                if section_content.strip():
                    ordered_content.append(f"## {section_title}\n\n{section_content.strip()}\n")
        return "\n".join(ordered_content)
    
    def submit_section_component(self, card_uuid: str, section_key: str, section_result: Dict, card: Dict) -> bool:
        """Submit a single section component to the server for assembly"""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/submit_guide_component'
            payload = {
                'uuid': card_uuid,
                'component_type': section_key,  # Use canonical section key (e.g. 'tldr', 'mechanics_breakdown', etc.)
                'component_content': section_result.get('content', ''),
                'component_title': section_result.get('title', section_key),
                'card_data': card,
                'status': 'public'  # Always set status to public
            }
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 200:
                result = response.json()
                # Accept both 'ok' and 'success' as valid statuses
                if result.get('status') in ('ok', 'success'):
                    logger.info(f"Submitted section '{section_key}' for {card_uuid} ({result})")
                    return True
                else:
                    logger.error(f"Server rejected section '{section_key}': {result}")
                    return False
            else:
                logger.error(f"Failed to submit section '{section_key}': {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error submitting section '{section_key}': {e}")
            return False
    
    def run(self, limit: int = None):
        """Main processing loop (submits each section as a component)"""
        logger.info("MTGAbyss Mixed-Model Worker (Componentized Submission)")
        logger.info("=" * 30)
        logger.info(f"Configured Ollama model: {self.ollama_model} ({'✅' if self.ollama_available else '❌'})")
        logger.info(f"Configured Gemini model: {self.gemini_model} ({'✅' if self.gemini_client else '❌'})")
        logger.info(f"Rate limit: {self.rate_limit}s between sections")

        # Show section assignments using v1-style (user-facing) keys and actual model for each section
        section_definitions = self.get_section_definitions()
        logger.info("Section assignment (user-facing keys and models):")
        for section_key in self.SECTION_DISPLAY_ORDER:
            if section_key in section_definitions:
                # Determine v1-style assignment (Gemini or Ollama) from MODEL_ASSIGNMENTS
                v1_assignment = None
                for provider, config in MODEL_ASSIGNMENTS.items():
                    if section_key in config['sections']:
                        v1_assignment = provider.upper()
                        break
                if not v1_assignment:
                    v1_assignment = "OLLAMA"  # Default fallback
                model = section_definitions[section_key].get('model', 'unknown')
                logger.info(f"  {section_key:12} [{v1_assignment}]: {model}")
        logger.info("Press Ctrl+C to stop.")

        # --- Stats logging additions ---
        def log_api_stats():
            try:
                resp = requests.get(f"{MTGABYSS_BASE_URL}/api/stats", timeout=10)
                if resp.status_code == 200:
                    stats = resp.json().get('stats', {})
                    logger.info(f"[API Stats] Total: {stats.get('total_cards')}, Reviewed: {stats.get('reviewed_cards')}, Legacy: {stats.get('legacy_reviewed_cards')}, Unreviewed: {stats.get('unreviewed_cards')}, Completion: {stats.get('completion_percentage')}%")
                else:
                    logger.warning(f"/api/stats returned {resp.status_code}")
            except Exception as e:
                logger.warning(f"Failed to fetch /api/stats: {e}")

        try:
            while limit is None or self.processed_count < limit:
                # Fetch a card to process
                card = self.fetch_card_to_process()
                if not card:
                    logger.info("No cards available. Waiting 30 seconds...")
                    time.sleep(30)
                    continue

                card_name = card.get('name', 'Unknown Card')
                card_uuid = card.get('uuid')
                logger.info(f"Processing card: {card_name} (UUID: {card_uuid})")

                section_definitions = self.get_section_definitions()
                failed_sections = 0
                completed_sections = 0
                sections = {}

                # Group section keys by model provider for batching
                model_to_sections = {'gemini': [], 'ollama': []}
                for section_key, section_config in section_definitions.items():
                    model_provider = self.get_model_for_section(section_key, section_config)
                    if model_provider in model_to_sections:
                        model_to_sections[model_provider].append(section_key)
                    else:
                        model_to_sections[model_provider] = [section_key]

                # Batch process by model provider
                for model_provider, section_keys in model_to_sections.items():
                    if not section_keys:
                        continue
                    for section_key in section_keys:
                        section_config = section_definitions[section_key]
                        # Check if the required model is available
                        if model_provider == 'gemini' and not self.gemini_client:
                            logger.warning(f"Skipping section '{section_key}' - Gemini not available")
                            failed_sections += 1
                            continue
                        elif model_provider == 'ollama' and not self.ollama_available:
                            logger.warning(f"Skipping section '{section_key}' - Ollama not available")
                            failed_sections += 1
                            continue

                        section_result = self.generate_section(section_key, section_config, card)

                        if section_result:
                            # Submit this section as a component
                            if self.submit_section_component(card_uuid, section_key, section_result, card):
                                completed_sections += 1
                                sections[section_key] = section_result
                            else:
                                failed_sections += 1
                            time.sleep(self.rate_limit)
                        else:
                            failed_sections += 1

                self.processed_count += 1
                simple_log(f"Completed {card_name} ({self.processed_count} total, {completed_sections} sections, {failed_sections} failed)")

                # Log stats every 3 cards
                if self.processed_count % 3 == 0:
                    log_api_stats()

                # Send Discord notification after all sections for a card (if webhook is set)
                if DISCORD_WEBHOOK_URL:
                    # Build a payload similar to process_card_mixed_model for notification
                    total_sections = len(section_definitions)
                    gemini_sections = sum(1 for k in sections if self.get_model_for_section(k, section_definitions[k]) == 'gemini')
                    ollama_sections = sum(1 for k in sections if self.get_model_for_section(k, section_definitions[k]) == 'ollama')
                    failed = failed_sections
                    analysis_data = {
                        'sections': sections,
                        'analyzed_at': datetime.now(UTC).isoformat(),
                        'guide_version': '3.0_mixed_model',
                        'model_strategy': {
                            'gemini_sections': gemini_sections,
                            'ollama_sections': ollama_sections,
                            'failed_sections': failed,
                            'total_sections': total_sections,
                            'gemini_model': self.gemini_model,
                            'ollama_model': self.ollama_model
                        },
                        'processing_time': 0  # Not tracked here
                    }
                    payload = {
                        'uuid': card_uuid,
                        'analysis': analysis_data,
                        'guide_meta': {
                            'type': 'mixed_model',
                            'version': '3.0',
                            'sections_generated': len(sections),
                            'models_used': f"gemini:{gemini_sections}, ollama:{ollama_sections}"
                        },
                        'category': 'mtg',
                        'card_data': card,
                        'has_full_content': len(sections) >= len(section_definitions) * 0.8
                    }
                    self.send_discord_notification(card, payload)

                # Rate limiting between cards
                time.sleep(self.rate_limit)

        except KeyboardInterrupt:
            logger.info("Stopping worker...")

        simple_log(f"Processed {self.processed_count} cards total")
    
    def send_discord_notification(self, card: Dict, payload: Dict):
        """Send Discord notification about completed analysis (with big image, like halfguide)"""
        try:
            if not DISCORD_WEBHOOK_URL:
                logger.warning("DISCORD_WEBHOOK_URL is not set. Skipping Discord notification.")
                return
            model_strategy = payload['analysis'].get('model_strategy', {})
            gemini_count = model_strategy.get('gemini_sections', 0)
            ollama_count = model_strategy.get('ollama_sections', 0)
            card_url = f"{MTGABYSS_BASE_URL}/card/{card['uuid']}"
            # Try to get a big image for the card (normal, art_crop, or fallback)
            image_url = None
            for key in ["art_crop", "normal", "large", "small"]:
                if card.get("image_uris") and card["image_uris"].get(key):
                    image_url = card["image_uris"][key]
                    break
            if not image_url and card.get("card_faces") and len(card["card_faces"]) > 0:
                for face in card["card_faces"]:
                    if face.get("image_uris") and face["image_uris"].get("art_crop"):
                        image_url = face["image_uris"]["art_crop"]
                        break
            embed = {
                "title": f"📊 Mixed-Model Guide Complete",
                "description": f"**[{card['name']}]({card_url})**\n🔗 {card_url}",
                "color": 0x00ff00,
                "fields": [
                    {"name": "🤖 Gemini Sections", "value": str(gemini_count), "inline": True},
                    {"name": "🦙 Ollama Sections", "value": str(ollama_count), "inline": True},
                    {"name": "� Guide Type", "value": "Full Mixed-Model Guide (12 sections)", "inline": True}
                ],
                "footer": {"text": f"Mixed-Model Worker v3.0"}
            }
            if image_url:
                embed["image"] = {"url": image_url}
            webhook_data = {"embeds": [embed]}
            resp = requests.post(DISCORD_WEBHOOK_URL, json=webhook_data, timeout=10)
            if resp.status_code >= 400:
                logger.warning(f"Discord webhook returned status {resp.status_code}: {resp.text}")
            else:
                logger.info(f"Discord notification sent for card {card.get('name', 'Unknown Card')}")
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")

def main():
    parser = argparse.ArgumentParser(
        description='MTGAbyss All-Local Worker - Use different local models for different sections (zero API costs)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use default models
  python mixed_model_worker.py
  
  # Specify custom models
  python mixed_model_worker.py --gemini-model gemini-1.5-pro --ollama-model llama3.1:8b
  
  # Process only 5 cards
  python mixed_model_worker.py --limit 5
  
  # Slower rate limiting
  python mixed_model_worker.py --rate-limit 2.0

Model Assignment Strategy:
  All sections now use local Ollama models for cost optimization:
  
  Examples from current configuration:
  - tldr: llama3.2:3b (fast summaries)
  - mechanics: llama3.1:latest (technical rules)
  - strategic: qwen2.5:7b (strategic reasoning)
  - format: mistral:7b-instruct (structured analysis)
  - history: qwen2.5:7b (narrative content)
  - flavor: llama3.1:latest (creative analysis)
  - advanced: qwen2.5:7b (complex thinking)
  - conclusion: llama3.1:latest (synthesis)
  
  Benefits:
  - Zero API costs (all local)
  - Save budget for Google Translate
  - Consistent performance
  - No rate limits
  
  You can experiment by editing the 'model' field in get_section_definitions():
  - Try different Ollama models: qwen2.5:7b, mistral:7b-instruct, gemma2:latest, llama3.2:3b
  - Mix and match based on section requirements
        """
    )
    
    parser.add_argument('--gemini-model', default='gemini-1.5-flash',
                       help='Gemini model to use (default: gemini-1.5-flash)')
    parser.add_argument('--ollama-model', default='llama3.1:latest',
                       help='Ollama model to use (default: llama3.1:latest)')
    parser.add_argument('--limit', type=int,
                       help='Maximum number of cards to process')
    parser.add_argument('--rate-limit', type=float, default=1.0,
                       help='Seconds to wait between sections (default: 1.0)')
    parser.add_argument('--api-base-url',
                       help='Override MTGABYSS_BASE_URL')
    
    args = parser.parse_args()
    
    # Override base URL if provided
    if args.api_base_url:
        global MTGABYSS_BASE_URL
        MTGABYSS_BASE_URL = args.api_base_url
    
    # Check if at least one model is available
    if not GEMINI_AVAILABLE and not OLLAMA_AVAILABLE:
        logger.error("No models available! Install google-generativeai and/or ollama packages.")
        return 1
    
    if GEMINI_AVAILABLE and not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set - Gemini sections will be skipped")
    
    # Create and run the worker
    worker = MixedModelWorker(
        gemini_model=args.gemini_model,
        ollama_model=args.ollama_model,
        rate_limit=args.rate_limit
    )
    
    worker.run(limit=args.limit)
    return 0

if __name__ == "__main__":
    sys.exit(main())
