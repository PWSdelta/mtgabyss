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
    
    def generate_with_gemini(self, prompt: str, model_name: str = None) -> Optional[str]:
        """Generate content using Gemini with a specific model"""
        if not self.gemini_client:
            logger.warning("Gemini not available, cannot generate content")
            return None
        
        # Use specified model or fall back to default
        actual_model = model_name or self.gemini_model
        
        try:
            # Create a new client if model differs from initialized one
            if actual_model != self.gemini_model:
                client = genai.GenerativeModel(actual_model)
            else:
                client = self.gemini_client
            
            start_time = time.time()
            response = client.generate_content(prompt)
            duration = time.time() - start_time
            
            if response and response.text:
                logger.info(f"Gemini ({actual_model}) call: {duration:.2f}s")
                return response.text.strip()
            else:
                logger.warning(f"Gemini ({actual_model}) returned empty response")
                return None
                
        except Exception as e:
            logger.error(f"Gemini ({actual_model}) generation error: {e}")
            return None
    
    def generate_with_ollama(self, prompt: str, model_name: str = None) -> Optional[str]:
        """Generate content using Ollama with a specific model"""
        if not self.ollama_available:
            logger.warning("Ollama not available, cannot generate content")
            return None
        
        # Use specified model or fall back to default
        actual_model = model_name or self.ollama_model
        
        try:
            start_time = time.time()
            response = ollama.generate(
                model=actual_model,
                prompt=prompt,
                stream=False
            )
            duration = time.time() - start_time
            
            if response and 'response' in response:
                logger.info(f"Ollama ({actual_model}) call: {duration:.2f}s")
                return response['response'].strip()
            else:
                logger.warning(f"Ollama ({actual_model}) returned empty response")
                return None
                
        except Exception as e:
            logger.error(f"Ollama ({actual_model}) generation error: {e}")
            return None
    
    def generate_section(self, section_key: str, section_config: Dict, card: Dict) -> Optional[Dict]:
        """Generate a single section using the appropriate model"""
        section_title = section_config['title']
        section_prompt = section_config['prompt']
        
        # Get the assigned model provider and specific model name
        model_provider = self.get_model_for_section(section_key, section_config)
        model_name = self.get_model_name_for_section(section_config)
        
        # Build the prompt
        full_prompt = f"""Section: {section_title}

{section_prompt}

Card details:
Name: {card['name']}
Mana Cost: {card.get('mana_cost', 'N/A')}
Type: {card.get('type_line', 'N/A')}
Text: {card.get('oracle_text', 'N/A')}
{f'P/T: {card.get("power")}/{card.get("toughness")}' if card.get('power') else ''}
Rarity: {card.get('rarity', 'N/A')}

Style Guidelines:
- Use natural paragraphs, bullet points and tables sparingly
- Liberally mention other cards using [[Card Name]] in double brackets
- Do NOT mention yourself, the AI, or the analysis process
- Do NOT end with phrases like 'in conclusion'
- Be specific and actionable
"""
        
        logger.info(f"Generating section '{section_key}' for {card['name']} using {model_name}")
        
        # Generate with the appropriate model
        content = None
        if model_provider == 'gemini':
            content = self.generate_with_gemini(full_prompt, model_name)
        elif model_provider == 'ollama':
            content = self.generate_with_ollama(full_prompt, model_name)
        
        if content:
            return {
                'title': section_title,
                'content': content,
                'model_used': model_name,
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
        """Get all section definitions with specific model assignments - ALL LOCAL"""
        return {
            "tldr": {
                "title": "TL;DR Summary",
                "prompt": "Write a 2-3 sentence summary of the card's main strengths, weaknesses, and archetypes. Be concise.",
                "model": "llama3.1:latest"  # Fast and efficient for summaries
            },
            "mechanics": {
                "title": "Card Mechanics & Interactions",
                "prompt": "Explain the card's rules, mechanics, and any unique interactions. Include edge cases and rules notes.",
                "model": "llama3.1:latest"  # Good reasoning for technical rules
            },
            "strategic": {
                "title": "Strategic Applications",
                "prompt": "Describe how this card is used strategically. What decks/archetypes want it? What roles does it fill?",
                "model": "qwen2.5:7b"  # Excellent strategic reasoning
            },
            "deckbuilding": {
                "title": "Deckbuilding & Synergies",
                "prompt": "Discuss deckbuilding considerations, synergies, and combos. What cards work well with it? Ensure to mention other cards using [[Card Name]] format.",
                "model": "llama3.1:latest"  # Good for structured analysis
            },
            "format": {
                "title": "Format Analysis",
                "prompt": "Analyze the card's viability in different formats (Standard, Historic, Commander, etc). Where does it shine?",
                "model": "mistral:7b-instruct"  # Good at following format structure
            },
            "scenarios": {
                "title": "Gameplay Scenarios",
                "prompt": "Give 2-3 example in-game scenarios where this card is impactful. Use specific board states if possible.",
                "model": "llama3.1:latest"  # Good for concrete examples
            },
            "history": {
                "title": "Historical Context",
                "prompt": "Discuss the card's history, reprints, and impact on the game over time.",
                "model": "qwen2.5:7b"  # Good for narrative and historical content
            },
            "flavor": {
                "title": "Flavor & Design",
                "prompt": "Comment on the card's flavor, art, and design. How does it fit the set/theme?",
                "model": "llama3.1:latest"  # Creative analysis, local
            },
            "budget": {
                "title": "Budget & Accessibility",
                "prompt": "Is this card budget-friendly? Are there cheaper alternatives?",
                "model": "qwen2.5:7b"  # Good for factual comparisons
            },
            "advanced": {
                "title": "Advanced Techniques",
                "prompt": "Describe advanced or less obvious uses, tricks, or interactions.",
                "model": "qwen2.5:7b"  # Complex strategic thinking, local
            },
            "mistakes": {
                "title": "Common Mistakes",
                "prompt": "List common mistakes or misplays involving this card.",
                "model": "mistral:7b-instruct"  # Good at structured lists
            },
            "conclusion": {
                "title": "Conclusion",
                "prompt": "Summarize the card's overall value and when to play it.",
                "model": "llama3.1:latest"  # Good synthesis, local
            }
        }
    
    def process_card_mixed_model(self, card: Dict) -> Optional[Dict]:
        """Process a card using mixed models for different sections"""
        card_name = card.get('name', 'Unknown Card')
        card_uuid = card.get('uuid')
        
        logger.info(f"Starting mixed-model analysis for {card_name} (UUID: {card_uuid})")
        start_time = time.time()
        
        sections = {}
        section_definitions = self.get_section_definitions()
        
        # Generate each section with its assigned model
        gemini_sections = 0
        ollama_sections = 0
        failed_sections = 0
        
        for section_key, section_config in section_definitions.items():
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
            
            section_result = self.generate_section(section_key, section_config, card)
            
            if section_result:
                sections[section_key] = section_result
                if model_provider == 'gemini':
                    gemini_sections += 1
                else:
                    ollama_sections += 1
                
                # Rate limiting between sections
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
            'has_full_content': len(sections) >= len(section_definitions) * 0.8  # 80% completion threshold
        }
        
        logger.info(f"Mixed-model analysis completed for {card_name} in {total_time:.2f}s")
        logger.info(f"  Gemini sections: {gemini_sections}, Ollama sections: {ollama_sections}, Failed: {failed_sections}")
        
        return payload
    
    def format_sections_for_display(self, sections: Dict, section_definitions: Dict) -> str:
        """Format sections into a complete guide for display"""
        ordered_content = []
        
        for section_key in section_definitions.keys():
            if section_key in sections:
                section_data = sections[section_key]
                section_title = section_data.get('title', section_definitions[section_key]['title'])
                section_content = section_data.get('content', '')
                
                if section_content.strip():
                    ordered_content.append(f"## {section_title}\n\n{section_content.strip()}\n")
        
        return "\n".join(ordered_content)
    
    def submit_analysis(self, payload: Dict) -> bool:
        """Submit the completed analysis to the server"""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/submit_work'
            response = requests.post(url, json=payload, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'ok':
                    logger.info(f"Successfully submitted analysis for {payload['uuid']}")
                    return True
                else:
                    logger.error(f"Server rejected analysis: {result}")
                    return False
            else:
                logger.error(f"Failed to submit analysis: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error submitting analysis: {e}")
            return False
    
    def run(self, limit: int = None):
        """Main processing loop"""
        logger.info("MTGAbyss Mixed-Model Worker")
        logger.info("=" * 30)
        logger.info(f"Gemini model: {self.gemini_model} ({'✅' if self.gemini_client else '❌'})")
        logger.info(f"Ollama model: {self.ollama_model} ({'✅' if self.ollama_available else '❌'})")
        logger.info(f"Rate limit: {self.rate_limit}s between sections")
        
        # Show model assignments
        section_definitions = self.get_section_definitions()
        gemini_sections = [(k, v.get('model')) for k, v in section_definitions.items() if v.get('model', '').startswith('gemini')]
        ollama_sections = [(k, v.get('model')) for k, v in section_definitions.items() if not v.get('model', '').startswith('gemini')]
        
        gemini_available = "✅" if self.gemini_client else "❌"
        ollama_available = "✅" if self.ollama_available else "❌"
        
        if gemini_sections:
            logger.info(f"GEMINI {gemini_available}:")
            for section, model in gemini_sections:
                logger.info(f"  {section}: {model}")
        
        if ollama_sections:
            logger.info(f"OLLAMA {ollama_available}:")
            for section, model in ollama_sections:
                logger.info(f"  {section}: {model}")
        
        logger.info("Press Ctrl+C to stop.")
        
        try:
            while limit is None or self.processed_count < limit:
                # Fetch a card to process
                card = self.fetch_card_to_process()
                if not card:
                    logger.info("No cards available. Waiting 30 seconds...")
                    time.sleep(30)
                    continue
                
                # Process the card with mixed models
                payload = self.process_card_mixed_model(card)
                if not payload:
                    logger.error(f"Failed to process card {card.get('name')}")
                    continue
                
                # Submit the analysis
                if self.submit_analysis(payload):
                    self.processed_count += 1
                    simple_log(f"Completed {card['name']} ({self.processed_count} total)")
                    
                    # Send Discord notification if configured
                    if DISCORD_WEBHOOK_URL:
                        self.send_discord_notification(card, payload)
                else:
                    logger.error(f"Failed to submit analysis for {card['name']}")
                
                # Rate limiting between cards
                time.sleep(self.rate_limit)
                
        except KeyboardInterrupt:
            logger.info("Stopping worker...")
        
        simple_log(f"Processed {self.processed_count} cards total")
    
    def send_discord_notification(self, card: Dict, payload: Dict):
        """Send Discord notification about completed analysis"""
        try:
            model_strategy = payload['analysis'].get('model_strategy', {})
            gemini_count = model_strategy.get('gemini_sections', 0)
            ollama_count = model_strategy.get('ollama_sections', 0)
            
            # Get the card URL for easy spot checking
            card_url = f"{MTGABYSS_BASE_URL}/card/{card['uuid']}"
            
            embed = {
                "title": f"📊 Mixed-Model Analysis Complete",
                "description": f"**[{card['name']}]({card_url})**\n🔗 {card_url}",
                "color": 0x00ff00,
                "fields": [
                    {"name": "🤖 Gemini Sections", "value": str(gemini_count), "inline": True},
                    {"name": "🦙 Ollama Sections", "value": str(ollama_count), "inline": True},
                    {"name": "💰 Cost Optimized", "value": f"{ollama_count}/{gemini_count + ollama_count} local", "inline": True}
                ],
                "footer": {"text": f"Mixed-Model Worker v3.0"}
            }
            
            webhook_data = {"embeds": [embed]}
            requests.post(DISCORD_WEBHOOK_URL, json=webhook_data, timeout=10)
            
        except Exception as e:
            logger.debug(f"Discord notification failed: {e}")

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
