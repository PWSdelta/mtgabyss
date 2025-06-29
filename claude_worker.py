import os
import anthropic
import time
import requests
from datetime import datetime
import dotenv

dotenv.load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("Set ANTHROPIC_API_KEY in your environment.")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def count_tokens(text):
    # Claude uses UTF-8 bytes, roughly 4 chars = 1 token, but use SDK if available
    return client.count_tokens(text)

def call_claude(prompt, model="claude-3-haiku-20240307", max_tokens=4096):
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

SCRYFALL_API_BASE = 'https://api.scryfall.com'

def fetch_random_card():
    try:
        resp = requests.get(f'{SCRYFALL_API_BASE}/cards/random', timeout=10)
        resp.raise_for_status()
        card = resp.json()
        card['imageUris'] = card.get('image_uris', {})
        return card
    except Exception as e:
        print(f"Error fetching card: {e}")
        return None

def create_analysis_prompt(card):
    return f"""Write a comprehensive, in-depth analysis guide for the Magic: The Gathering card [[{card['name']}]].\n\nInclude:\n- TL;DR summary\n- Detailed card mechanics and interactions\n- Strategic uses, combos, and synergies\n- Deckbuilding roles and archetypes\n- Format viability and competitive context\n- Rules interactions and technical notes\n- Art, flavor, and historical context\n- Summary of key points (use a different section title for this)\n\nUse natural paragraphs, markdown headers, and liberal use of specific card examples in [[double brackets]]. Do not use bullet points. Write at least 3357 words. Do not mention yourself or the analysis process.\nWrap up with a conclusion summary\n\nCard details:\nName: {card['name']}\nMana Cost: {card.get('mana_cost', 'N/A')}\nType: {card.get('type_line', 'N/A')}\nText: {card.get('oracle_text', 'N/A')}\n{f'P/T: {card.get('power')}/{card.get('toughness')}' if 'power' in card else ''}\n"""

def create_polish_prompt(card, raw_analysis):
    return f"""Polish and elevate the following Magic: The Gathering card review to sound like an experienced player with deep knowledge of archetypes and deckbuilding. Improve clarity, flow, and insight, but do not shorten or omit any important details. Use natural paragraphs and markdown headers.\n\nOriginal review:\n---\n{raw_analysis}\n---\n\nModerate use of specific card examples in [[double brackets]].\nDo not use [[double brackets]] for any mention of {card['name']}.\nLimit use bullet points.\nWrite at least 3357 words.\nDo not mention yourself or the analysis process.\n"""

def send_discord_notification(card, webhook_url):
    if not webhook_url:
        print("No Discord webhook URL configured.")
        return False
    try:
        card_name = card['name']
        # Link to your own site instead of Scryfall
        card_url = f"https://mtgabyss.com/card/{card['id']}"
        image_url = card.get('image_uris', {}).get('normal', '')
        embed = {
            "title": f"✨ New Analysis: {card_name}",
            "description": f"Comprehensive analysis completed for [[{card_name}]]",
            "url": card_url,
            "color": 0x00FF00,
            "fields": [
                {"name": "Type", "value": card.get('type_line', 'Unknown'), "inline": True},
                {"name": "Mana Cost", "value": card.get('mana_cost', 'N/A'), "inline": True},
                {"name": "Set", "value": card.get('set_name', 'Unknown'), "inline": True}
            ],
            "footer": {"text": f"MTGAbyss Claude Test • {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
        }
        if image_url:
            embed["thumbnail"] = {"url": image_url}
        payload = {"embeds": [embed]}
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"Sent Discord notification for {card_name}")
        return True
    except Exception as e:
        print(f"Discord notification error: {e}")
        return False

def save_to_database(card, analysis, api_base_url):
    try:
        payload = {
            "uuid": card["id"],
            "analysis": {
                "long_form": analysis,
                "analyzed_at": datetime.now().isoformat(),
                "model_used": "claude-3-haiku-20240307"
            },
            "card_data": card
        }
        api_url = f"{api_base_url}/api/submit_work"
        card['imageUris'] = card.get('image_uris', {})
        resp = requests.post(api_url, json=payload, timeout=30)
        resp.raise_for_status()
        if resp.json().get("status") == "ok":
            print(f"Submitted analysis for {card['name']} to API")
            return True
        else:
            print(f"API error: {resp.text}")
            return False
    except Exception as e:
        print(f"API submit error: {e}")
        return False

if __name__ == "__main__":
    import sys
    try:
        num_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    except Exception:
        num_runs = 1
    for i in range(num_runs):
        print(f"\n=== Run {i+1} of {num_runs} ===\n")
        card = fetch_random_card()
        if not card:
            print("Failed to fetch card.")
            continue
        print(f"Fetched card: {card['name']} ({card['id']})")
        prompt = create_analysis_prompt(card)
        print("Calling Claude for raw analysis...")
        start = time.time()
        raw_result, input_tokens, output_tokens = call_claude(prompt)
        elapsed = time.time() - start
        print(f"Raw analysis input tokens: {input_tokens}")
        print(f"Raw analysis output tokens: {output_tokens}")
        print(f"Raw analysis total tokens: {input_tokens + output_tokens}")
        print(f"Elapsed: {elapsed:.2f}s\n")
        print("First 500 chars of raw analysis:\n", raw_result[:500])

        polish_prompt = create_polish_prompt(card, raw_result)
        print("\nCalling Claude for polish step...")
        start = time.time()
        polished_result, polish_input_tokens, polish_output_tokens = call_claude(polish_prompt)
        elapsed = time.time() - start
        print(f"Polish input tokens: {polish_input_tokens}")
        print(f"Polish output tokens: {polish_output_tokens}")
        print(f"Polish total tokens: {polish_input_tokens + polish_output_tokens}")
        print(f"Elapsed: {elapsed:.2f}s\n")
        print("First 500 chars of polished analysis:\n", polished_result[:500])

        # Cost estimation (Haiku, as example)
        haiku_in = 0.25 / 1_000_000
        haiku_out = 1.25 / 1_000_000
        total_cost = (input_tokens + polish_input_tokens) * haiku_in + (output_tokens + polish_output_tokens) * haiku_out
        print(f"\nEstimated total cost (Haiku): ${total_cost:.4f}")

        # After polish step
        DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
        send_discord_notification(card, DISCORD_WEBHOOK_URL)

        MTGABYSS_BASE_URL = os.getenv("MTGABYSS_BASE_URL", "http://localhost:5000")
        save_to_database(card, polished_result, MTGABYSS_BASE_URL)