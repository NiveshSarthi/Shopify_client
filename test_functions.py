import os
import requests
from dotenv import load_dotenv

# Load configuration from the .env file in the current directory
load_dotenv()

SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN")

def test_get_shopify_token():
    print("--- Shopify Token Test ---")
    print(f"Shop Domain: {SHOP_DOMAIN}")
    print(f"Client ID: {SHOPIFY_CLIENT_ID}")
    # Hide secret for security
    print(f"Client Secret: {'*' * len(SHOPIFY_CLIENT_SECRET) if SHOPIFY_CLIENT_SECRET else 'None'}")

    if not SHOP_DOMAIN or not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        print("ERROR: Missing one or more environment variables.")
        return

    domain = SHOP_DOMAIN
    # if not domain.endswith(".myshopify.com"):
    #     domain = f"{domain}.myshopify.com"

    url = f"https://{domain}/admin/oauth/access_token"
    
    # Try both JSON and Form Data to be sure
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    print(f"\nAttempting POST to: {url}")
    
    try:
        # Most newer Shopify versions/Custom Apps expect JSON if Content-Type is set, 
        # but your previous script used urlencode. Let's try Form Data (data=) first.
        print("Trying with Form Data (application/x-www-form-urlencoded)...")
        resp = requests.post(url, data=payload)
        
        print(f"Status Code: {resp.status_code}")
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            print(f"SUCCESS! Token: {token[:10]}...")
            return token
        else:
            print(f"FAILED: {resp.text}")
            
            # Alternative: Try JSON encoding if form data fails
            print("\nTrying with JSON encoding (application/json)...")
            resp_json = requests.post(url, json=payload)
            print(f"Status Code: {resp_json.status_code}")
            if resp_json.status_code == 200:
                token = resp_json.json().get("access_token")
                print(f"SUCCESS with JSON! Token: {token[:10]}...")
                return token
            else:
                print(f"FAILED with JSON: {resp_json.text}")

    except Exception as e:
        print(f"EXCEPTION: {e}")

def test_register_webhook(token):
    print("\n--- Listing and Registering Webhooks ---")
    domain = SHOP_DOMAIN
    if not domain.endswith(".myshopify.com"):
        domain = f"{domain}.myshopify.com"
        
    url = f"https://{domain}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    
    # 1. List existing webhooks
    print("Fetching existing webhooks...")
    list_resp = requests.get(url, headers=headers)
    if list_resp.status_code == 200:
        webhooks = list_resp.json().get("webhooks", [])
        print(f"Found {len(webhooks)} existing webhooks:")
        for w in webhooks:
            print(f"- {w['topic']} at {w['address']} (ID: {w['id']})")
    else:
        print(f"Failed to list webhooks: {list_resp.text}")

    # 2. Try to register a basic one (ruling out scope issues)
    test_addr = "https://shopify_client.zavyo.io/webhook/shopify"
    print(f"\n--- Testing Topic: app/uninstalled ---")
    payload = {
        "webhook": {
            "topic": "app/uninstalled",
            "address": test_addr,
            "format": "json"
        }
    }
    reg_resp = requests.post(url, json=payload, headers=headers)
    print(f"Status: {reg_resp.status_code}")
    print(f"Response: {reg_resp.text}")

    # 3. Try to register the intended one
    print(f"\n--- Testing Topic: orders/paid ---")
    payload_orders = {
        "webhook": {
            "topic": "orders/paid",
            "address": test_addr,
            "format": "json"
        }
    }
    reg_resp_orders = requests.post(url, json=payload_orders, headers=headers)
    print(f"Status: {reg_resp_orders.status_code}")
    print(f"Response: {reg_resp_orders.text}")

if __name__ == "__main__":
    token = test_get_shopify_token()
    if token:
        test_register_webhook(token)
    else:
        print("\nTest failed at token step.")