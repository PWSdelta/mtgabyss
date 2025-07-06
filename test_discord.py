#!/usr/bin/env python3
"""
Test Discord notification functionality
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
MTGABYSS_PUBLIC_URL = os.getenv('MTGABYSS_PUBLIC_URL', 'https://mtgabyss.com')

def test_discord_notification():
    """Send a test Discord notification"""
    if not DISCORD_WEBHOOK_URL:
        print("❌ No Discord webhook URL found in .env file")
        return False
    
    try:
        # Create test embed
        embed = {
            "title": "🧪 Test Notification: Discord Integration Active",
            "description": "This is a test message to verify Discord notifications are working correctly.",
            "color": 0x00ff00,  # Green color
            "fields": [
                {
                    "name": "✅ Status",
                    "value": "Discord notifications are **ACTIVE** and working properly!",
                    "inline": False
                },
                {
                    "name": "🔧 Worker Mode",
                    "value": "Batched processing with 75% cost savings",
                    "inline": True
                },
                {
                    "name": "🔗 Links",
                    "value": f"[MTGAbyss Home]({MTGABYSS_PUBLIC_URL})",
                    "inline": True
                }
            ],
            "footer": {
                "text": "MTGAbyss • Test Message"
            },
            "timestamp": datetime.now().isoformat()
        }
        
        payload = {
            "embeds": [embed],
            "username": "MTGAbyss Worker (Test)",
        }
        
        print(f"🚀 Sending test notification to Discord...")
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        
        if resp.status_code == 204:
            print("✅ Test Discord notification sent successfully!")
            return True
        else:
            print(f"❌ Discord notification failed with status: {resp.status_code}")
            print(f"Response: {resp.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error sending Discord notification: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing Discord Integration...")
    print(f"Webhook URL: {DISCORD_WEBHOOK_URL[:50]}..." if len(DISCORD_WEBHOOK_URL) > 50 else f"Webhook URL: {DISCORD_WEBHOOK_URL}")
    print()
    
    success = test_discord_notification()
    if success:
        print("\n🎉 Discord integration test completed successfully!")
        print("Your Discord notifications are ready for the MTG card analysis worker.")
    else:
        print("\n❌ Discord integration test failed.")
        print("Please check your DISCORD_WEBHOOK_URL in the .env file.")
