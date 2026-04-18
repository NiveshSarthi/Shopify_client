import os
import requests
import json
from dotenv import load_dotenv

# Load config from .env
load_dotenv()

EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL")
EXTERNAL_API_TOKEN = os.getenv("EXTERNAL_API_BEARER_TOKEN")
PUBLIC_HOST_URL = os.getenv("PUBLIC_HOST_URL")
TEMPLATE_SECOND = os.getenv("META_TEMPLATE_NAME", "second_template_werw")
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")

def test_send_second_template():
    if not EXTERNAL_API_URL or not EXTERNAL_API_TOKEN or not PUBLIC_HOST_URL:
        print("❌ Error: Missing EXTERNAL_API_URL, EXTERNAL_API_BEARER_TOKEN or PUBLIC_HOST_URL in .env")
        return

    # Use your target phone number
    target_number = "917701905881"
    
    # We use a known existing image from your folder
    existing_image = "order_1042_1776435487.png" 
    full_image_url = f"{PUBLIC_HOST_URL.rstrip('/')}/images/{existing_image}"

    url = f"{EXTERNAL_API_URL.rstrip('/')}/api/v1/messages/template-send"
    headers = {
        "Authorization": f"Bearer {EXTERNAL_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "number": target_number,
        "template_name": TEMPLATE_SECOND,
        "language_code": "en_US",
        "variable_mapping": {
            "1": "Abhinav Test",
            "2": "Sector 72, Faridabad - Testing"  # Simple address
        },
        "header_media": {
            "media_type": "image",
            "image_url": full_image_url
        }
    }
    
    if PHONE_NUMBER_ID:
        payload["phone_number_id"] = PHONE_NUMBER_ID

    print(f"🚀 Sending Test Request to: {url}")
    print(f"📦 Payload: {json.dumps(payload, indent=2)}")
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=40)
        print(f"📡 Status Code: {resp.status_code}")
        print(f"📄 Response Body: {resp.text}")
        
        if resp.status_code == 200:
            print("✅ SUCCESS: The message was accepted by the API!")
        else:
            print(f"❌ FAILED: Error {resp.status_code}")
            
    except Exception as e:
        print(f"💥 Exception during request: {e}")

if __name__ == "__main__":
    test_send_second_template()
