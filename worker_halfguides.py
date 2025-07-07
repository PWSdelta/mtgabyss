#!/usr/bin/env python3
"""
MTGAbyss Half-Guide Worker - Fast, Wide Coverage (6-Section Guides)
==================================================================

A worker that generates only the most essential sections for each card:
- tldr, mechanics, strategic, advanced, mistakes, conclusion
- No deckbuilding, format, scenarios, history, flavor, or budget
- Cards are picked at random (no histogram/priority logic)
- Designed for rapid, broad coverage of the card pool

Features:
- Minimal section set for actionable, useful guides
- Same robust retry, logging, and Discord notification as worker_prime
- Can be expanded later to full guides
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

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'https://mtgabyss.com')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def simple_log(message: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

HALFGUIDE_SECTIONS = [
    "tldr", "mechanics", "strategic", "advanced", "mistakes", "conclusion"
]
HALFGUIDE_DISPLAY_MAP = {
    "tldr": "TL;DR Summary",
    "mechanics": "Card Mechanics & Interactions",
    "strategic": "Strategic Applications",
    "advanced": "Advanced Techniques",
    "mistakes": "Common Mistakes",
    "conclusion": "Conclusion"
}

def get_halfguide_section_definitions():
    return {
        "tldr": {
            "title": "TL;DR Summary",
            "prompt": "Write a 2-3 sentence summary of the card's main strengths, weaknesses, and archetypes. Be concise.",
            "model": "llama3.1:latest"
        },
        "mechanics": {
            "title": "Card Mechanics & Interactions",
            "prompt": "Explain the card's rules, mechanics, and any unique interactions. Include edge cases and rules notes.",
            "model": "llama3.1:latest"
        },
        "strategic": {
            "title": "Strategic Applications",
            "prompt": "Describe how this card is used strategically. What decks/archetypes want it? What roles does it fill?",
            "model": "qwen2.5:7b"
        },
        "advanced": {
            "title": "Advanced Techniques",
            "prompt": "Describe advanced or less obvious uses, tricks, or interactions.",
            "model": "qwen2.5:7b"
        },
        "mistakes": {
            "title": "Common Mistakes",
            "prompt": "List common mistakes or misplays involving this card.",
            "model": "mistral:7b-instruct"
        },
        "conclusion": {
            "title": "Conclusion",
            "prompt": "Summarize the card's overall value and when to play it.",
            "model": "llama3.1:latest"
        }
    }

class HalfGuideWorker:
    SECTION_DISPLAY_ORDER = HALFGUIDE_SECTIONS
    SECTION_DISPLAY_MAP = HALFGUIDE_DISPLAY_MAP

    def __init__(self, gemini_model: str = 'gemini-1.5-flash', ollama_model: str = 'llama3.1:latest', rate_limit: float = 1.0):
        self.gemini_model = gemini_model
        self.ollama_model = ollama_model
        self.rate_limit = rate_limit
        self.processed_count = 0
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

    def get_model_for_section(self, section: str, section_config: Dict = None) -> str:
        if section_config and 'model' in section_config:
            model_name = section_config['model']
            if model_name.startswith('gemini'):
                return 'gemini'
            else:
                return 'ollama'
        return 'ollama'

    def get_model_name_for_section(self, section_config: Dict) -> str:
        return section_config.get('model', self.ollama_model)

    def generate_with_gemini(self, prompt: str, model_name: str = None) -> Optional[str]:
        if not self.gemini_client:
            logger.warning("Gemini not available, cannot generate content")
            return None
        actual_model = model_name or self.gemini_model
        try:
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
        if not self.ollama_available:
            logger.warning("Ollama not available, cannot generate content")
            return None
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

    def generate_section(self, section_key: str, section_config: Dict, card: Dict, prior_sections: Optional[Dict] = None) -> Optional[Dict]:
        section_title = section_config['title']
        section_prompt = section_config['prompt']
        model_provider = self.get_model_for_section(section_key, section_config)
        model_name = self.get_model_name_for_section(section_config)
        context_text = ""
        if section_key == "conclusion" and prior_sections:
            context_text = "\n\n---\n\n".join([
                f"## {self.SECTION_DISPLAY_MAP.get(k, k)}\n\n{prior_sections[k]['content'].strip()}"
                for k in self.SECTION_DISPLAY_ORDER if k != "conclusion" and k in prior_sections and prior_sections[k].get('content')
            ])
            if context_text:
                context_text = f"\n\nANALYSIS OF PREVIOUS SECTIONS:\n{context_text}\n\n"
        full_prompt = f"Section: {section_title}\n\n{section_prompt}\n\nCard details:\nName: {card['name']}\nMana Cost: {card.get('mana_cost', 'N/A')}\nType: {card.get('type_line', 'N/A')}\nText: {card.get('oracle_text', 'N/A')}\n{f'P/T: {card.get('power')}/{card.get('toughness')}' if card.get('power') else ''}\nRarity: {card.get('rarity', 'N/A')}\n{context_text}Style Guidelines:\n- Use natural paragraphs, bullet points and tables sparingly\n- Liberally mention other cards using [[Card Name]] in double brackets\n- Do NOT mention yourself, the AI, or the analysis process\n- Do NOT end with phrases like 'in conclusion'\n- Be specific and actionable\n"
        logger.info(f"Generating section '{section_key}' for {card['name']} using {model_name}")
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
        try:
            url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed'
            params = {'limit': 1}
            response = requests.get(url, params=params, timeout=60)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('cards'):
                    logger.info(f"Got card from unified endpoint (random)")
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

    def submit_section_component(self, card_uuid: str, section_key: str, section_result: Dict, card: Dict) -> bool:
        try:
            url = f'{MTGABYSS_BASE_URL}/api/submit_guide_component'
            payload = {
                'uuid': card_uuid,
                'section_key': section_key,
                'section_data': section_result,
                'card_data': card,
                'component_type': 'section',
                'component_content': section_result.get('content', '')
            }
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 200:
                result = response.json()
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
        logger.info("MTGAbyss Half-Guide Worker (6-Section Minimal Guides)")
        logger.info("=" * 30)
        logger.info(f"Configured Ollama model: {self.ollama_model} ({'✅' if self.ollama_available else '❌'})")
        logger.info(f"Configured Gemini model: {self.gemini_model} ({'✅' if self.gemini_client else '❌'})")
        logger.info(f"Rate limit: {self.rate_limit}s between sections")
        section_definitions = get_halfguide_section_definitions()
        logger.info("Section assignment (user-facing keys and models):")
        for section_key in self.SECTION_DISPLAY_ORDER:
            if section_key in section_definitions:
                model = section_definitions[section_key].get('model', 'unknown')
                logger.info(f"  {section_key:12}: {model}")
        logger.info("Press Ctrl+C to stop.")
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
                card = self.fetch_card_to_process()
                if not card:
                    logger.info("No cards available. Waiting 30 seconds...")
                    time.sleep(30)
                    continue
                card_name = card.get('name', 'Unknown Card')
                card_uuid = card.get('uuid')
                logger.info(f"Processing card: {card_name} (UUID: {card_uuid})")
                failed_sections = 0
                completed_sections = 0
                sections = {}
                # Process all except conclusion first
                for section_key in self.SECTION_DISPLAY_ORDER:
                    if section_key == "conclusion":
                        continue
                    section_config = section_definitions[section_key]
                    model_provider = self.get_model_for_section(section_key, section_config)
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
                        if self.submit_section_component(card_uuid, section_key, section_result, card):
                            completed_sections += 1
                            sections[section_key] = section_result
                        else:
                            failed_sections += 1
                        time.sleep(self.rate_limit)
                    else:
                        failed_sections += 1
                # Now process conclusion with context from previous sections
                if "conclusion" in section_definitions:
                    conclusion_config = section_definitions["conclusion"]
                    conclusion_provider = self.get_model_for_section("conclusion", conclusion_config)
                    if (conclusion_provider == 'gemini' and not self.gemini_client) or (conclusion_provider == 'ollama' and not self.ollama_available):
                        logger.warning("Skipping section 'conclusion' - required model not available")
                        failed_sections += 1
                    else:
                        conclusion_result = self.generate_section("conclusion", conclusion_config, card, prior_sections=sections)
                        if conclusion_result:
                            if self.submit_section_component(card_uuid, "conclusion", conclusion_result, card):
                                completed_sections += 1
                                sections["conclusion"] = conclusion_result
                            else:
                                failed_sections += 1
                            time.sleep(self.rate_limit)
                        else:
                            failed_sections += 1
                self.processed_count += 1
                simple_log(f"Completed {card_name} ({self.processed_count} total, {completed_sections} sections, {failed_sections} failed)")
                if self.processed_count % 3 == 0:
                    log_api_stats()
                if DISCORD_WEBHOOK_URL:
                    gemini_sections = sum(1 for k in sections if self.get_model_for_section(k, section_definitions[k]) == 'gemini')
                    ollama_sections = sum(1 for k in sections if self.get_model_for_section(k, section_definitions[k]) == 'ollama')
                    analysis_data = {
                        'sections': sections,
                        'analyzed_at': datetime.now(UTC).isoformat(),
                        'guide_version': 'halfguide_v1',
                        'model_strategy': {
                            'gemini_sections': gemini_sections,
                            'ollama_sections': ollama_sections,
                            'failed_sections': failed_sections,
                            'total_sections': len(section_definitions),
                            'gemini_model': self.gemini_model,
                            'ollama_model': self.ollama_model
                        },
                        'processing_time': 0
                    }
                    payload = {
                        'uuid': card_uuid,
                        'analysis': analysis_data,
                        'guide_meta': {
                            'type': 'halfguide',
                            'version': '1.0',
                            'sections_generated': len(sections),
                            'models_used': f"gemini:{gemini_sections}, ollama:{ollama_sections}"
                        },
                        'category': 'mtg',
                        'card_data': card,
                        'has_full_content': len(sections) >= len(section_definitions) * 0.8
                    }
                    self.send_discord_notification(card, payload)
                time.sleep(self.rate_limit)
        except KeyboardInterrupt:
            logger.info("Stopping worker...")
        simple_log(f"Processed {self.processed_count} cards total")

    def send_discord_notification(self, card: Dict, payload: Dict):
        try:
            if not DISCORD_WEBHOOK_URL:
                logger.warning("DISCORD_WEBHOOK_URL is not set. Skipping Discord notification.")
                return
            model_strategy = payload['analysis'].get('model_strategy', {})
            gemini_count = model_strategy.get('gemini_sections', 0)
            ollama_count = model_strategy.get('ollama_sections', 0)
            card_url = f"{MTGABYSS_BASE_URL}/card/{card['uuid']}"
            embed = {
                "title": f"📊 Half-Guide Complete",
                "description": f"**[{card['name']}]({card_url})**\n🔗 {card_url}",
                "color": 0x00ff00,
                "fields": [
                    {"name": "🤖 Gemini Sections", "value": str(gemini_count), "inline": True},
                    {"name": "🦙 Ollama Sections", "value": str(ollama_count), "inline": True},
                    {"name": "💡 Guide Type", "value": "Half-Guide (6 sections)", "inline": True}
                ],
                "footer": {"text": f"Half-Guide Worker v1.0"}
            }
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
        description='MTGAbyss Half-Guide Worker - Generate 6-section minimal guides for random cards',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python worker_halfguides.py
  python worker_halfguides.py --limit 100
  python worker_halfguides.py --rate-limit 2.0
        """
    )
    parser.add_argument('--gemini-model', default='gemini-1.5-flash', help='Gemini model to use (default: gemini-1.5-flash)')
    parser.add_argument('--ollama-model', default='llama3.1:latest', help='Ollama model to use (default: llama3.1:latest)')
    parser.add_argument('--limit', type=int, help='Maximum number of cards to process')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Seconds to wait between sections (default: 1.0)')
    parser.add_argument('--api-base-url', help='Override MTGABYSS_BASE_URL')
    args = parser.parse_args()
    if args.api_base_url:
        global MTGABYSS_BASE_URL
        MTGABYSS_BASE_URL = args.api_base_url
    if not GEMINI_AVAILABLE and not OLLAMA_AVAILABLE:
        logger.error("No models available! Install google-generativeai and/or ollama packages.")
        return 1
    if GEMINI_AVAILABLE and not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set - Gemini sections will be skipped")
    worker = HalfGuideWorker(
        gemini_model=args.gemini_model,
        ollama_model=args.ollama_model,
        rate_limit=args.rate_limit
    )
    worker.run(limit=args.limit)
    return 0

if __name__ == "__main__":
    sys.exit(main())
