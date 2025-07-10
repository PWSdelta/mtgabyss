#!/usr/bin/env python3
"""
MTGAbyss Combined Worker - Half-Guide & Full-Guide Modes
======================================================

A unified worker script for generating either half-guides (6-section minimal) or full-guides (all sections) for MTGAbyss cards.
- Use --half-guides for the 6-section minimal guide (tldr, mechanics, strategic, advanced, mistakes, conclusion)
- Use --full-guides for the full set of guide sections (deckbuilding, format, scenarios, history, flavor, budget, etc.)
- Always passes the full card context (pretty-printed JSON) to every model prompt, regardless of mode.
- Merges logic from worker_halfguides.py and worker_prime.py for maintainability.

Usage:
  python worker_cards.py --half-guides
  python worker_cards.py --full-guides
  python worker_cards.py --half-guides --limit 100
  python worker_cards.py --full-guides --rate-limit 2.0
"""

import argparse
import time
import requests
import json
import os
import logging
import sys
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

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
    print("Warning: ollama not installed. Olloma sections will be skipped.")

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
MTGABYSS_BASE_URL = os.getenv('MTGABYSS_BASE_URL', 'https://mtgabyss.com')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Configure beautiful logging with elapsed time tracking
import time as time_module
_start_time = time_module.time()

def elapsed_time():
    """Get elapsed time since worker startup in a readable format"""
    elapsed = time_module.time() - _start_time
    if elapsed < 60:
        return f"+{elapsed:.1f}s"
    elif elapsed < 3600:
        return f"+{elapsed/60:.1f}m"
    else:
        return f"+{elapsed/3600:.1f}h"

# Configure logging (prevent duplicate logs)
logger = logging.getLogger('MTGWorker')

# Only configure if not already configured
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)-8s | %(name)-15s | %(message)s'
    )
    
    # Set up colored logging for console (if available)
    try:
        import colorlog
        console_handler = colorlog.StreamHandler()
        console_handler.setFormatter(colorlog.ColoredFormatter(
            '%(log_color)s%(levelname)-8s | %(name)-15s | %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red',
            }
        ))
        logger.handlers.clear()
        logger.addHandler(console_handler)
        logger.propagate = False  # Prevent propagation to root logger
        logger.info("🎨 Worker colored logging enabled")
    except ImportError:
        logger.info("📝 Worker standard logging active (install colorlog for colors)")
else:
    # Logger already configured
    logger.info("📝 Worker logging already configured")

def log_card_work(action, card_name, uuid="", extra_info=""):
    """Helper for consistent card work logging"""
    card_display = f"'{card_name}' ({uuid[:8]}...)" if uuid else f"'{card_name}'"
    if extra_info:
        logger.info(f"🃏 {action}: {card_display} | {extra_info} | {elapsed_time()}")
    else:
        logger.info(f"🃏 {action}: {card_display} | {elapsed_time()}")

def log_model_work(model_name, action, details=""):
    """Helper for model-related logging"""
    if details:
        logger.info(f"🤖 [{model_name.upper()}] {action} | {details} | {elapsed_time()}")
    else:
        logger.info(f"🤖 [{model_name.upper()}] {action} | {elapsed_time()}")

def log_api_call(endpoint, status, details=""):
    """Helper for API call logging"""
    status_emoji = "✅" if status == "success" else "❌" if status == "error" else "⚠️"
    if details:
        logger.info(f"{status_emoji} API {endpoint} → {status.upper()} | {details} | {elapsed_time()}")
    else:
        logger.info(f"{status_emoji} API {endpoint} → {status.upper()} | {elapsed_time()}")

def log_worker_stats(operation, count, details=""):
    """Helper for worker statistics logging"""
    if details:
        logger.info(f"📊 {operation}: {count} | {details} | {elapsed_time()}")
    else:
        logger.info(f"📊 {operation}: {count} | {elapsed_time()}")

def simple_log(message: str):
    """Legacy simple logging function - now uses beautiful logging"""
    logger.info(f"📝 {message}")


# Flexible, key-agnostic section definitions
def get_guide_section_definitions(mode: str):
    """
    Return a flexible, key-agnostic section definitions dict for the guide system.
    In a real system, this could load from a config file, database, or API.
    """
    if mode == 'half':
        # Half-guide: streamlined 6-section format
        return {
            "tldr": {
                "title": "TL;DR Summary",
                "prompt": "Provide a clear and concise summary of this card's main strengths, typical uses, and impact in Commander decks. Synthesize the key insights from the detailed analysis sections to give readers the essential information they need. Mention other relevant formats only if it enhances understanding.",
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
        # Full-guide: comprehensive 12-section format
        return {
            "tldr": {
                "title": "TL;DR Summary",
                "prompt": "Summarize this Magic: The Gathering card in 3-5 punchy sentences. Synthesize the key insights from the detailed analysis sections to highlight its power level, main use cases, and most popular formats—especially Commander, if applicable.",
                "model": "llama3.1:latest"
            },
            "mechanics": {
                "title": "Card Mechanics & Interactions",
                "prompt": "Explain this card's rules, keyword abilities, and how it functions on the stack and battlefield. Include edge cases, unusual rules interactions, and any Commander-specific quirks.",
                "model": "llama3.1:latest"
            },
            "strategic": {
                "title": "Strategic Applications",
                "prompt": "Describe how this card is used strategically in real decks. What Commander strategies, color identities, or archetypes benefit from it most? Include competitive, casual, or niche builds.",
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
            "deckbuilding": {
                "title": "Deckbuilding & Synergies",
                "prompt": "Explain how to build around this card. What Commanders, color identities, themes, or engines does it work with? Include specific synergy cards and combo notes.",
                "model": "llama3.1:latest"
            },
            "format": {
                "title": "Format & Archetype Roles",
                "prompt": "Break down the card's impact in Commander and any other formats it's legal in. Where is it competitive, casual, banned, or overlooked?",
                "model": "llama3.1:latest"
            },
            "scenarios": {
                "title": "Key Scenarios & Matchups",
                "prompt": "Describe scenarios and matchups where this card excels or fails. In Commander, consider multiplayer politics, board presence, or combos.",
                "model": "llama3.1:latest"
            },
            "history": {
                "title": "History & Notable Appearances",
                "prompt": "Summarize this card's history: printings, reprints, tournament presence, EDHREC stats, or iconic decks it appeared in.",
                "model": "llama3.1:latest"
            },
            "flavor": {
                "title": "Flavor & Lore",
                "prompt": "Describe the card's flavor, lore, and setting. Tie in world-building elements and character backstory if known.",
                "model": "llama3.1:latest"
            },
            "budget": {
                "title": "Budget & Accessibility",
                "prompt": "Discuss the card's price, reprint status, and budget-friendliness. Suggest similar options for budget decks—especially in Commander.",
                "model": "llama3.1:latest"
            },
            "conclusion": {
                "title": "Conclusion",
                "prompt": "Wrap up by evaluating how strong or versatile the card is—especially in Commander. Who should include it, and when is it best left out?",
                "model": "llama3.1:latest"
            }
        }


class CombinedGuideWorker:
    def __init__(self, mode: str, gemini_model: str = 'gemini-1.5-flash', ollama_model: str = 'llama3.1:latest', rate_limit: float = 1.0):
        self.mode = mode
        self.gemini_model = gemini_model
        self.ollama_model = ollama_model
        self.rate_limit = rate_limit
        self.processed_count = 0
        self.gemini_client = None
        self.ollama_available = OLLAMA_AVAILABLE
        
        # Initialize models with better logging
        if GEMINI_AVAILABLE and GEMINI_API_KEY:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                self.gemini_client = genai.GenerativeModel(gemini_model)
                log_model_work("gemini", f"Initialized successfully", f"model: {gemini_model}")
            except Exception as e:
                log_model_work("gemini", f"Initialization failed", str(e))
                self.gemini_client = None
        elif GEMINI_AVAILABLE:
            log_model_work("gemini", "Available but no API key", "set GEMINI_API_KEY environment variable")
        else:
            log_model_work("gemini", "Not available", "install google-generativeai package")
            
        if OLLAMA_AVAILABLE:
            log_model_work("ollama", f"Initialized successfully", f"model: {ollama_model}")
        else:
            log_model_work("ollama", "Not available", "install ollama package")
            
        # Use flexible, key-agnostic section definitions
        self.section_definitions = get_guide_section_definitions(mode)
        # Preserve order as defined in the section_definitions dict
        self.SECTION_DISPLAY_ORDER = list(self.section_definitions.keys())
        
        log_worker_stats(f"Worker initialized", f"{mode.upper()} mode", f"{len(self.section_definitions)} sections configured")

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
            log_model_work("gemini", "Unavailable - client not initialized")
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
                log_model_work("gemini", f"Generated content", f"{actual_model} | {duration:.2f}s | {len(response.text)} chars")
                return response.text.strip()
            else:
                log_model_work("gemini", f"Empty response", f"{actual_model} | {duration:.2f}s")
                return None
        except Exception as e:
            log_model_work("gemini", f"Generation failed", f"{actual_model} | {str(e)}")
            return None

    def generate_with_ollama(self, prompt: str, model_name: str = None) -> Optional[str]:
        if not self.ollama_available:
            log_model_work("ollama", "Unavailable - service not available")
            return None
        actual_model = model_name or self.ollama_model
        
        # Log prompt size for diagnosis
        prompt_size = len(prompt)
        if prompt_size > 10000:
            log_model_work("ollama", f"Large prompt detected", f"size: {prompt_size:,} chars")
        
        try:
            start_time = time.time()
            log_model_work("ollama", f"Starting generation", f"{actual_model} | prompt: {prompt_size:,} chars")
            
            response = ollama.generate(
                model=actual_model,
                prompt=prompt,
                stream=False
            )
            duration = time.time() - start_time
            if response and 'response' in response:
                log_model_work("ollama", f"Generated content", f"{actual_model} | {duration:.2f}s | {len(response['response'])} chars")
                return response['response'].strip()
            else:
                log_model_work("ollama", f"Empty response", f"{actual_model} | {duration:.2f}s")
                return None
        except Exception as e:
            duration = time.time() - start_time
            log_model_work("ollama", f"Generation failed", f"{actual_model} | {duration:.2f}s | {str(e)}")
            return None

    def generate_section(self, section_key: str, section_config: Dict, card: Dict, prior_sections: Optional[Dict] = None) -> Optional[Dict]:
        section_title = section_config['title']
        section_prompt = section_config['prompt']
        model_provider = self.get_model_for_section(section_key, section_config)
        model_name = self.get_model_name_for_section(section_config)

        # Always include full card context for every section
        card_context_lines = [
            f"Name: {card.get('name', 'N/A')}",
            f"Mana Cost: {card.get('mana_cost', 'N/A')}",
            f"Type: {card.get('type_line', 'N/A')}",
            f"Oracle Text: {card.get('oracle_text', 'N/A')}",
            f"Power/Toughness: {card.get('power', 'N/A')}/{card.get('toughness', 'N/A')}",
            f"Rarity: {card.get('rarity', 'N/A')}",
            f"Set: {card.get('set', 'N/A')}",
            f"Colors: {', '.join(card.get('colors', [])) if card.get('colors') else 'N/A'}",
            f"CMC: {card.get('cmc', 'N/A')}",
            f"Prices: {json.dumps(card.get('prices', {})) if card.get('prices') else 'N/A'}"
        ]
        card_context = "\n".join(card_context_lines)

        # Temporarily disable context for TL;DR and conclusion to improve performance
        context_text = ""

        full_prompt = (
            f"Section: {section_title}\n\n"
            f"{section_prompt}\n\n"
            f"Card details (key fields):\n{card_context}\n"
            f"{context_text}"
            "Style Guidelines:\n"
            "- Use natural paragraphs, bullet points and tables sparingly\n"
            "- Liberally mention other cards using [[Card Name]] in double brackets\n"
            "- Do NOT mention yourself, the AI, or the analysis process\n"
            "- Do NOT end with phrases like 'in conclusion', 'to conclude', 'in summary', 'overall', or 'to sum up'\n"
            "- Do NOT use meta-commentary about the card being 'underappreciated', 'overlooked', or 'versatile'\n"
            "- Do NOT mention 'elevating gameplay', 'unlocking strategies', or 'taking to the next level'\n"
            "- Do NOT refer to the reader directly with 'you', 'your deck', or 'your gameplay'\n"
            "- Be specific and actionable with concrete examples\n"
            "- Write as if explaining to an experienced Magic player, not teaching basics\n"
        )
        
        # Log prompt size for debugging
        prompt_size = len(full_prompt)
        if prompt_size > 5000:
            logger.warning(f"Large prompt for section '{section_key}': {prompt_size:,} characters")
        logger.info(f"Generating section '{section_key}' for {card.get('name', 'N/A')} using {model_name} (prompt: {prompt_size:,} chars)")
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
                'generated_at': datetime.now(timezone.utc).isoformat()
            }
        else:
            logger.error(f"Failed to generate section '{section_key}' with {model_name}")
            return None

    def fetch_existing_sections(self, card_uuid: str) -> Dict[str, Dict]:
        """Fetch existing sections for a card from the database to avoid regenerating them."""
        try:
            url = f'{MTGABYSS_BASE_URL}/api/get_card_sections'
            params = {'uuid': card_uuid}
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('sections'):
                    existing_sections = {}
                    for section in data['sections']:
                        section_key = section.get('component_type', section.get('type', ''))
                        if section_key and section_key in self.section_definitions:
                            existing_sections[section_key] = {
                                'title': section.get('title', section.get('component_title', '')),
                                'content': section.get('content', section.get('component_content', '')),
                                'model_used': section.get('model_used', 'unknown'),
                                'generated_at': section.get('generated_at', section.get('created_at', ''))
                            }
                    log_card_work("Found existing sections", f"card {card_uuid[:8]}...", "", f"{len(existing_sections)} sections already exist")
                    return existing_sections
                else:
                    log_card_work("No existing sections", f"card {card_uuid[:8]}...", "", "starting fresh")
                    return {}
            elif response.status_code == 404:
                log_card_work("No existing sections", f"card {card_uuid[:8]}...", "", "card not found or no sections")
                return {}
            else:
                log_api_call("/api/get_card_sections", "error", f"HTTP {response.status_code}")
                return {}
        except Exception as e:
            log_api_call("/api/get_card_sections", "error", str(e))
            return {}

    def fetch_card_to_process(self) -> Optional[Dict]:
        try:
            url = f'{MTGABYSS_BASE_URL}/api/get_random_unreviewed'
            # Pass the worker mode to get the appropriate card assignment
            mode_param = 'half-guide' if self.mode == 'half' else 'full-guide'
            params = {'limit': 1, 'mode': mode_param}
            response = requests.get(url, params=params, timeout=60)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('cards'):
                    card = data['cards'][0]
                    edhrec_rank = card.get('edhrec_rank', 'N/A')
                    logger.info(f"Got card from EDHREC assignment: {card.get('name')} (rank: {edhrec_rank}, mode: {mode_param})")
                    return card
            elif response.status_code == 404:
                logger.info("No cards available for processing")
                # TODO: Could add fallback to fetch from pending_guide collection here
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
                'component_type': section_key,  # e.g. 'tldr', 'mechanics', etc.
                'component_content': section_result.get('content', ''),
                'component_title': section_result.get('title', section_key),
                'card_data': card,
                'status': 'public'  # Always set status to public
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
        log_worker_stats("Worker starting", f"{self.mode.upper()} mode", f"limit: {limit or 'unlimited'}")
        logger.info("=" * 50)
        
        # Show model availability
        gemini_status = "✅" if self.gemini_client else "❌"
        ollama_status = "✅" if self.ollama_available else "❌"
        log_model_work("status", f"Ollama {ollama_status}", f"model: {self.ollama_model}")
        log_model_work("status", f"Gemini {gemini_status}", f"model: {self.gemini_model}")
        
        logger.info(f"🔧 Rate limit: {self.rate_limit}s between sections")
        logger.info("🎯 Section assignment (user-facing keys and models):")
        for section_key in self.SECTION_DISPLAY_ORDER:
            if section_key in self.section_definitions:
                model = self.section_definitions[section_key].get('model', 'unknown')
                logger.info(f"  {section_key:12}: {model}")
        logger.info("🎮 Press Ctrl+C to stop worker")
        
        def log_api_stats():
            try:
                resp = requests.get(f"{MTGABYSS_BASE_URL}/api/stats", timeout=10)
                if resp.status_code == 200:
                    stats = resp.json().get('stats', {})
                    log_worker_stats("API Stats", 
                        f"{stats.get('completion_percentage', 0):.1f}% complete",
                        f"total: {stats.get('total_cards', 0):,} | reviewed: {stats.get('reviewed_cards', 0):,} | remaining: {stats.get('unreviewed_cards', 0):,}")
                else:
                    log_api_call("/api/stats", "error", f"HTTP {resp.status_code}")
            except Exception as e:
                log_api_call("/api/stats", "error", str(e))
        try:
            while limit is None or self.processed_count < limit:
                card = self.fetch_card_to_process()
                if not card:
                    log_card_work("Queue empty", "waiting for cards")
                    time.sleep(30)
                    continue
                    
                card_name = card.get('name', 'Unknown Card')
                card_uuid = card.get('uuid')
                log_card_work("Processing started", card_name, card_uuid, f"{self.mode.upper()} mode")
                
                # Fetch existing sections to avoid regeneration
                existing_sections = self.fetch_existing_sections(card_uuid)
                
                failed_sections = 0
                completed_sections = 0
                sections = {}
                
                # Process all sections except tldr and conclusion first (tldr needs context from other sections)
                for section_key in self.SECTION_DISPLAY_ORDER:
                    if section_key in ["tldr", "conclusion"]:
                        continue
                    section_config = self.section_definitions[section_key]
                    model_provider = self.get_model_for_section(section_key, section_config)
                    
                    if model_provider == 'gemini' and not self.gemini_client:
                        log_model_work("gemini", f"Skipping section '{section_key}'", "client not available")
                        failed_sections += 1
                        continue
                    elif model_provider == 'ollama' and not self.ollama_available:
                        log_model_work("ollama", f"Skipping section '{section_key}'", "service not available")
                        failed_sections += 1
                        continue
                    
                    # Check if this section already exists
                    if section_key in existing_sections:
                        logger.info(f"Section '{section_key}' already exists for {card_name}, skipping regeneration")
                        sections[section_key] = existing_sections[section_key]
                        completed_sections += 1
                    else:
                        section_result = self.generate_section(section_key, section_config, card)
                        if section_result:
                            if self.submit_section_component(card_uuid, section_key, section_result, card):
                                completed_sections += 1
                                sections[section_key] = section_result
                            else:
                                failed_sections += 1
                        else:
                            failed_sections += 1
                
                # Now generate TL;DR with context from completed sections
                if "tldr" in self.section_definitions:
                    tldr_config = self.section_definitions["tldr"]
                    tldr_provider = self.get_model_for_section("tldr", tldr_config)
                    
                    # Check if TL;DR already exists
                    if "tldr" in existing_sections:
                        log_card_work("Skipping existing section", card_name, card_uuid, "'tldr' already exists")
                        sections["tldr"] = existing_sections["tldr"]
                        completed_sections += 1
                    elif (tldr_provider == 'gemini' and not self.gemini_client) or (tldr_provider == 'ollama' and not self.ollama_available):
                        logger.warning("Skipping section 'tldr' - required model not available")
                        failed_sections += 1
                    else:
                        log_card_work("Generating new section", card_name, card_uuid, "'tldr' with prior context")
                        tldr_result = self.generate_section("tldr", tldr_config, card, prior_sections=sections)
                        if tldr_result:
                            if self.submit_section_component(card_uuid, "tldr", tldr_result, card):
                                completed_sections += 1
                                sections["tldr"] = tldr_result
                            else:
                                failed_sections += 1
                            time.sleep(self.rate_limit)
                        else:
                            failed_sections += 1
                
                # Now process conclusion with context from previous sections
                if "conclusion" in self.section_definitions:
                    conclusion_config = self.section_definitions["conclusion"]
                    conclusion_provider = self.get_model_for_section("conclusion", conclusion_config)
                    
                    # Check if conclusion already exists
                    if "conclusion" in existing_sections:
                        log_card_work("Skipping existing section", card_name, card_uuid, "'conclusion' already exists")
                        sections["conclusion"] = existing_sections["conclusion"]
                        completed_sections += 1
                    elif (conclusion_provider == 'gemini' and not self.gemini_client) or (conclusion_provider == 'ollama' and not self.ollama_available):
                        logger.warning("Skipping section 'conclusion' - required model not available")
                        failed_sections += 1
                    else:
                        log_card_work("Generating new section", card_name, card_uuid, "'conclusion' missing")
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
                
                # Calculate how many sections were reused vs generated
                existing_count = len(existing_sections)
                generated_count = completed_sections - existing_count
                
                simple_log(f"Completed {card_name} ({self.processed_count} total, {completed_sections} sections, {failed_sections} failed, {existing_count} reused, {generated_count} generated)")
                if self.processed_count % 3 == 0:
                    log_api_stats()
                if DISCORD_WEBHOOK_URL:
                    gemini_sections = sum(1 for k in sections if self.get_model_for_section(k, self.section_definitions[k]) == 'gemini')
                    ollama_sections = sum(1 for k in sections if self.get_model_for_section(k, self.section_definitions[k]) == 'ollama')
                    analysis_data = {
                        'sections': sections,
                        'analyzed_at': datetime.now(timezone.utc).isoformat(),
                        'guide_version': f'{self.mode}guide_v1',
                        'model_strategy': {
                            'gemini_sections': gemini_sections,
                            'ollama_sections': ollama_sections,
                            'failed_sections': failed_sections,
                            'total_sections': len(self.section_definitions),
                            'gemini_model': self.gemini_model,
                            'ollama_model': self.ollama_model
                        },
                        'processing_time': 0
                    }
                    payload = {
                        'uuid': card_uuid,
                        'analysis': analysis_data,
                        'guide_meta': {
                            'type': f'{self.mode}guide',
                            'version': '1.0',
                            'sections_generated': len(sections),
                            'models_used': f"gemini:{gemini_sections}, ollama:{ollama_sections}"
                        },
                        'category': 'mtg',
                        'card_data': card,
                        'has_full_content': len(sections) >= 12
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
            # Try to get a good image URL (prefer art_crop, then normal, then small)
            image_url = None
            if card.get('image_uris'):
                image_url = card['image_uris'].get('art_crop') or card['image_uris'].get('normal') or card['image_uris'].get('small')
            elif card.get('card_faces') and isinstance(card['card_faces'], list) and card['card_faces']:
                # Double-faced card
                face = card['card_faces'][0]
                if face.get('image_uris'):
                    image_url = face['image_uris'].get('art_crop') or face['image_uris'].get('normal') or face['image_uris'].get('small')
            # Fallback
            if not image_url:
                image_url = f"https://mtgabyss.com/static/mtg_card_back.png"
            embed = {
                "title": f"{'Half-Guide' if self.mode == 'half' else 'Full-Guide'} Complete: {card.get('name', 'Unknown Card')}",
                "url": card_url,
                "description": f"**[{card.get('name', 'Unknown Card')}]({card_url})**\nSet: {card.get('set_name', card.get('set', ''))} | Rarity: {card.get('rarity', '')}\n\n[View on MTGAbyss]({card_url})",
                "color": 0x00ff00,
                "fields": [
                    {"name": "Guide Type", "value": f"{'Half-Guide (6 sections)' if self.mode == 'half' else 'Full-Guide (all sections)'}", "inline": True},
                    {"name": "Gemini Sections", "value": str(gemini_count), "inline": True},
                    {"name": "Ollama Sections", "value": str(ollama_count), "inline": True},
                ],
                "image": {"url": image_url},
                "footer": {"text": f"MTGAbyss Combined Worker v1.0"},
                "timestamp": datetime.now(timezone.utc).isoformat()
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
        description='MTGAbyss Combined Worker - Generate half-guides or full-guides for random cards',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python worker_cards.py --half-guides
  python worker_cards.py --full-guides
  python worker_cards.py --half-guides --limit 100
  python worker_cards.py --full-guides --rate-limit 2.0
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--half-guides', action='store_true', help='Generate 6-section minimal guides (default)')
    group.add_argument('--full-guides', action='store_true', help='Generate full guides (all sections)')
    parser.add_argument('--ollama-big-model', default=None, help='Override Ollama model for all sections (e.g., "llama3:70b", "qwen2:72b", etc.)')
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
    mode = 'half' if args.half_guides else 'full'
    # Create the worker with the specified parameters
    worker = CombinedGuideWorker(
        mode=mode,
        gemini_model=args.gemini_model,
        ollama_model=args.ollama_big_model if args.ollama_big_model else args.ollama_model,
        rate_limit=args.rate_limit
    )
    
    # If --ollama-big-model is set, override all ollama section models
    if args.ollama_big_model:
        section_defs = worker.section_definitions.copy()
        for k, v in section_defs.items():
            if v.get('model', '').startswith('llama') or v.get('model', '').startswith('qwen') or v.get('model', '').startswith('mistral'):
                v['model'] = args.ollama_big_model
        # Patch the worker's section_definitions to use the big model
        worker.section_definitions = section_defs
    worker.run(limit=args.limit)
    return 0

if __name__ == "__main__":
    sys.exit(main())
