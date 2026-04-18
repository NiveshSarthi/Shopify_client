import os
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN")

def get_token():
    domain = SHOP_DOMAIN
    if not domain.endswith(".myshopify.com"):
        domain = f"{domain}.myshopify.com"
    url = f"https://{domain}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    resp = requests.post(url, data=payload)
    return resp.json().get("access_token")

def list_webhooks():
    token = get_token()
    domain = SHOP_DOMAIN
    if not domain.endswith(".myshopify.com"):
        domain = f"{domain}.myshopify.com"
    
    url = f"https://{domain}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        webhooks = resp.json().get("webhooks", [])
        if not webhooks:
            print("No webhooks found.")
        else:
            print(f"--- Active Webhooks ({len(webhooks)}) ---")
            for w in webhooks:
                print(f"Topic: {w['topic']}")
                print(f"Address: {w['address']}")
                print(f"ID: {w['id']}")
                print("-" * 20)
    else:
        print(f"Error: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    list_webhooks()
