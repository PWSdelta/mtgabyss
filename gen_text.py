import json
from pymongo import MongoClient
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
import google.generativeai as genai
import requests
import time

# Load environment variables
load_dotenv()

# Configure Gemini
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Google Translate API key
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

print(f"Using Gemini key: {GEMINI_API_KEY[:10]}..." if GEMINI_API_KEY else "No Gemini key found")
print(f"Using Google key: {GOOGLE_API_KEY[:10]}..." if GOOGLE_API_KEY else "No Google key found")

# MongoDB connection
client = MongoClient(os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/'))
db = client.tcgplex
destinations_collection = db.destinations
api_logs_collection = db.api_logs  # New collection for API response tracking

# 12-section structure with finalized prompts
sections = {
    "introduction": (
        "Write an overview of [DESTINATION] as a golf location. "
        "Highlight what makes it special for golf travelers."
    ),
    "renowned_golf_courses": (
        "Cover 5–7 top golf courses in [DESTINATION] with signature holes, "
        "green fees, booking tips, and local quirks."
    ),
    "seasonal_insights": (
        "Describe the best seasons to golf in [DESTINATION], "
        "including weather, peak/off-season tips."
    ),
    "2_day_golf_itinerary": (
        "Create a 2-day golf itinerary in [DESTINATION] with morning and afternoon plans, "
        "courses, restaurants, and cultural activities."
    ),
    "never_hurts_to_learn_again": (
        "Beginner-friendly golf tips for [DESTINATION]. "
        "Cover etiquette, renting gear, caddies, local rules in an approachable way."
    ),
    "travel_and_lodging": (
        "Advice on flights, transport, hotels, and stay-and-play packages in [DESTINATION]."
    ),
    "off_the_beaten_path_things_to_do": (
        "List hidden local attractions, cultural stops, and scenic places beyond golf in [DESTINATION]."
    ),
    "cultural_highlights": (
        "Explore [DESTINATION]'s culture, traditions, festivals, and arts."
    ),
    "dining": (
        "Describe signature dishes, food culture, restaurants, and culinary events in [DESTINATION]."
    ),
    "things_to_do_beyond_golf": (
        "Non-golf activities, tours, and entertainment for travelers in [DESTINATION]."
    ),
    "safety_and_practical_tips": (
        "Offer safety advice, visa info, local customs, tipping, language tips for [DESTINATION]."
    ),
    "call_to_action_and_resources": (
        "Write a short closing encouraging readers to plan/book their trip, "
        "suggesting resources and inviting them to revisit the guide."
    )
}

# Simple destination for testing
destination_name = "France"

print(f"Processing destination: {destination_name}")

# Log API usage for cost tracking
def log_api_usage(api_type, request_data, response_data, destination, section, language=None, error=None):
    log_entry = {
        "timestamp": datetime.now(timezone.utc),
        "api_type": api_type,  # "gemini" or "google_translate"
        "destination": destination,
        "section": section,
        "language": language,
        "request": {
            "prompt_length": len(str(request_data)) if request_data else 0,
            "data": str(request_data)[:1000] if request_data else None  # Truncate for storage
        },
        "response": {
            "content_length": len(str(response_data)) if response_data else 0,
            "word_count": len(str(response_data).split()) if response_data else 0,
            "data": str(response_data) if response_data else None,
            "success": error is None
        },
        "error": str(error) if error else None,
        "estimated_cost": calculate_estimated_cost(api_type, request_data, response_data)
    }
    
    try:
        api_logs_collection.insert_one(log_entry)
        print(f"Logged {api_type} usage for {section} - Cost: ${log_entry['estimated_cost']:.4f}")
    except Exception as e:
        print(f"Failed to log API usage: {e}")

# Estimate API costs (approximate)
def calculate_estimated_cost(api_type, request_data, response_data):
    if api_type == "gemini":
        # Gemini Flash pricing: ~$0.075/1M input tokens, ~$0.30/1M output tokens
        # Rough estimate: 1 token ≈ 4 characters
        input_chars = len(str(request_data)) if request_data else 0
        output_chars = len(str(response_data)) if response_data else 0
        input_tokens = input_chars / 4
        output_tokens = output_chars / 4
        cost = (input_tokens * 0.075 / 1000000) + (output_tokens * 0.30 / 1000000)
        return cost
    elif api_type == "google_translate":
        # Google Translate pricing: ~$20/1M characters
        chars = len(str(request_data)) if request_data else 0
        cost = chars * 20 / 1000000
        return cost
    return 0.0

# Real Gemini AI generation with logging
def gemini_generate(prompt, destination_name, section_key):
    prompt_filled = prompt.replace("[DESTINATION]", destination_name)
    
    # Create a comprehensive prompt for better content
    full_prompt = f"""
    You are a professional golf travel writer creating a detailed destination guide.
    
    Task: {prompt_filled}
    
    Requirements:
    - Write 400-600 words
    - Use a friendly, expert, approachable tone
    - Include specific local details, prices (if known), addresses when relevant
    - Focus on international golf travelers planning a trip
    - Include cultural context and practical tips
    - Use subheadings for easy reading
    - Make it engaging and informative
    
    Write the content now:
    """
    
    try:
        print(f"Generating content with Gemini for: {prompt_filled[:50]}...")
        response = model.generate_content(full_prompt)
        
        # Log successful API call
        log_api_usage("gemini", full_prompt, response.text, destination_name, section_key)
        
        return response.text
    except Exception as e:
        print(f"Gemini generation error: {e}")
        
        # Log failed API call
        log_api_usage("gemini", full_prompt, None, destination_name, section_key, error=e)
        
        return f"Error generating content: {e}"

# Google Translate function using REST API with logging
def translate_to_japanese(text, destination_name, section_key):
    if not GOOGLE_API_KEY:
        return f"Google API key not found in environment variables"
    
    try:
        print(f"Translating to Japanese: {text[:50]}...")
        
        url = f"https://translation.googleapis.com/language/translate/v2?key={GOOGLE_API_KEY}"
        
        payload = {
            'q': text,
            'source': 'en',
            'target': 'ja',
            'format': 'text'
        }
        
        response = requests.post(url, data=payload)
        
        if response.status_code == 200:
            result = response.json()
            translated_text = result['data']['translations'][0]['translatedText']
            
            # Log successful translation
            log_api_usage("google_translate", text, translated_text, destination_name, section_key, "ja")
            
            return translated_text
        else:
            error_msg = f"Translation API error: {response.status_code} - {response.text}"
            print(error_msg)
            
            # Log failed translation
            log_api_usage("google_translate", text, None, destination_name, section_key, "ja", error_msg)
            
            return error_msg
            
    except Exception as e:
        print(f"Translation error: {e}")
        
        # Log exception
        log_api_usage("google_translate", text, None, destination_name, section_key, "ja", e)
        
        return f"Translation error: {e}"

# Generate English content with Gemini
print("=== Generating English Content with Gemini Flash ===")
review_components_en = {}

for section_key, prompt in sections.items():
    section_text = gemini_generate(prompt, destination_name, section_key)
    review_components_en[section_key] = {
        "content": section_text,
        "word_count": len(section_text.split()),
        "generated_at": datetime.now(timezone.utc)
    }
    time.sleep(1)  # Rate limiting

# Save English content to database
destination_review_doc = {
    "name": destination_name,
    "slug": destination_name.lower().replace(" ", "-"),
    "publishing_status": "draft",  # draft, review, published, archived
    "review_components": {
        "en": {
            "title": f"{destination_name} Golf Travel Guide",
            "language": "en",
            "generated_at": datetime.now(timezone.utc),
            "sections": review_components_en
        }
    },
    "created_at": datetime.now(timezone.utc),
    "updated_at": datetime.now(timezone.utc)
}

print("\n=== Saving English Content to Database ===")
try:
    # Insert or update English content
    result = destinations_collection.update_one(
        {"slug": destination_name.lower().replace(" ", "-")},
        {"$set": destination_review_doc},
        upsert=True
    )
    print(f"English content saved successfully. Upserted ID: {result.upserted_id}")
    english_saved = True
except Exception as e:
    print(f"Database error for English: {e}")
    english_saved = False

# Generate Japanese translations if English was saved successfully
if english_saved:
    print("\n=== Translating to Japanese ===")
    review_components_ja = {}
    
    for section_key, section_data in review_components_en.items():
        print(f"\nTranslating section: {section_key}")
        print(f"English content preview: {section_data['content'][:100]}...")
        
        translated_content = translate_to_japanese(section_data["content"], destination_name, section_key)
        
        print(f"Japanese content preview: {translated_content[:100]}...")
        print(f"Translation successful: {translated_content != section_data['content']}")
        
        review_components_ja[section_key] = {
            "content": translated_content,
            "word_count": len(translated_content.split()),
            "generated_at": datetime.now(timezone.utc),
            "translated_from": "en"
        }
        time.sleep(0.5)  # Rate limiting for translate API
    
    # Save Japanese content to database
    print("\n=== Saving Japanese Content to Database ===")
    try:
        # Update with Japanese content and publishing status
        update_data = {
            "review_components.ja": {
                "title": f"{destination_name} ゴルフ旅行ガイド",
                "language": "ja",
                "generated_at": datetime.now(timezone.utc),
                "sections": review_components_ja
            },
            "publishing_status": "review",  # Move to review status when both languages exist
            "updated_at": datetime.now(timezone.utc)
        }
        
        print(f"Updating document with Japanese content...")
        print(f"Sample Japanese section: {list(review_components_ja.values())[0]['content'][:100]}...")
        
        result = destinations_collection.update_one(
            {"slug": destination_name.lower().replace(" ", "-")},
            {"$set": update_data}
        )
        
        print(f"Japanese content saved successfully. Modified count: {result.modified_count}")
        
        # Verify the save by reading back
        verification = destinations_collection.find_one(
            {"slug": destination_name.lower().replace(" ", "-")},
            {"review_components.ja.sections.introduction.content": 1}
        )
        
        if verification and "review_components" in verification and "ja" in verification["review_components"]:
            ja_intro = verification["review_components"]["ja"]["sections"]["introduction"]["content"]
            print(f"✓ Verification - Japanese content in DB: {ja_intro[:100]}...")
            
            # Check if it contains Japanese characters
            has_japanese = any('\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff' or '\u4e00' <= char <= '\u9faf' for char in ja_intro)
            print(f"✓ Contains Japanese characters: {has_japanese}")
        else:
            print("❌ Verification failed - Japanese content not found in database!")
        
        # Print summary with cost tracking
        print(f"\n=== SUMMARY ===")
        print(f"Destination: {destination_name}")
        print(f"English sections generated: {len(review_components_en)}")
        print(f"Japanese sections translated: {len(review_components_ja)}")
        print(f"Total words (EN): {sum(s['word_count'] for s in review_components_en.values())}")
        print(f"Saved to database with slug: {destination_name.lower().replace(' ', '-')}")
        
        # Calculate total estimated costs
        total_cost = 0
        for log in api_logs_collection.find({"destination": destination_name}):
            total_cost += log.get("estimated_cost", 0)
        print(f"Total estimated API cost: ${total_cost:.4f}")
        
        # Show API usage summary
        gemini_calls = api_logs_collection.count_documents({"destination": destination_name, "api_type": "gemini"})
        translate_calls = api_logs_collection.count_documents({"destination": destination_name, "api_type": "google_translate"})
        print(f"Gemini API calls: {gemini_calls}")
        print(f"Google Translate API calls: {translate_calls}")
        print(f"All API responses stored in 'api_logs' collection for historical tracking")
        
    except Exception as e:
        print(f"Database error for Japanese: {e}")
else:
    print("Skipping Japanese translation due to English save failure")

# Close MongoDB connection
client.close()