import os
import requests
import json
import uuid
import hmac
import hashlib
import base64
import sys
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI()

# ---------- CONFIGURATION ----------
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
raw_domain = os.getenv("SHOP_DOMAIN", "")
SHOP_DOMAIN = raw_domain.replace("https://", "").replace("http://", "").split("/")[0]

SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "996819796855984")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "EAAWkjxOQzMYBRN17smC1dtcZCZBuBZCPMjH4GftuxLPVuj48YEZAJOS5brlIggqAzqiBUJLZBuUv1McYMkfvij07zCvgPgfLeQPYSZAMXCxxlNlp010hX1MGlyt1ShG9CqCZB0iUMCp7KxmMJhkN5CWfwwsP6SjAwmnS4ImsVsUUqeejh0HneX0iBLzgpe0TFEOOQZDZD")
TEMPLATE_NAME = os.getenv("META_TEMPLATE_NAME", "template_book_demo")
LANG_CODE = os.getenv("META_LANG_CODE", "en_US")

PUBLIC_HOST_URL = os.getenv("PUBLIC_HOST_URL")

_cached_token = None
_processed_orders = set()

def log(msg):
    print(f"DEBUG: {msg}")
    sys.stdout.flush()

# ---------- SECURITY ----------

def verify_shopify_hmac(data: bytes, hmac_header: str):
    if not SHOPIFY_CLIENT_SECRET: return True
    if not hmac_header: return False
    hash = hmac.new(SHOPIFY_CLIENT_SECRET.encode('utf-8'), data, hashlib.sha256)
    digest = base64.b64encode(hash.digest()).decode('utf-8')
    return hmac.compare_digest(digest, hmac_header)

# ---------- CORE LOGIC ----------

def get_shopify_token(force_refresh=False):
    global _cached_token
    if _cached_token and not force_refresh: return _cached_token
    if SHOPIFY_ACCESS_TOKEN and not force_refresh:
        _cached_token = SHOPIFY_ACCESS_TOKEN
        return _cached_token

    log(f"Refreshing Shopify token for {SHOP_DOMAIN}...")
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    payload = {"client_id": SHOPIFY_CLIENT_ID, "client_secret": SHOPIFY_CLIENT_SECRET, "grant_type": "client_credentials"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            _cached_token = token
            log("Token refreshed.")
            return token
    except Exception as e:
        log(f"Token error: {e}")
    return _cached_token

def shopify_request(url, headers, method="GET", json=None):
    try:
        if method == "GET": resp = requests.get(url, headers=headers, timeout=15)
        elif method == "DELETE": resp = requests.delete(url, headers=headers, timeout=15)
        else: resp = requests.post(url, headers=headers, json=json, timeout=15)
            
        if resp.status_code == 401:
            log("401 Detected, refreshing token...")
            new_token = get_shopify_token(force_refresh=True)
            if new_token:
                headers["X-Shopify-Access-Token"] = new_token
                if method == "GET": return requests.get(url, headers=headers, timeout=15)
                if method == "DELETE": return requests.delete(url, headers=headers, timeout=15)
                return requests.post(url, headers=headers, json=json, timeout=15)
        return resp
    except Exception as e:
        return {"error": str(e)}

def fetch_product_image_url(product_id, variant_id):
    token = get_shopify_token()
    headers = {"X-Shopify-Access-Token": token}
    
    # 1. Try Variant (if there's a specific image for the option)
    if variant_id:
        url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/variants/{variant_id}.json"
        resp = shopify_request(url, headers)
        if isinstance(resp, requests.Response) and resp.status_code == 200:
            v = resp.json().get("variant", {})
            if v.get("src"): return v["src"] # Check if src is directly on variant
    
    # 2. Try Product images list
    if product_id:
        url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/products/{product_id}/images.json"
        resp = shopify_request(url, headers)
        if isinstance(resp, requests.Response) and resp.status_code == 200:
            imgs = resp.json().get("images", [])
            for img in imgs:
                if img.get("src"): 
                    log(f"Found product image URL: {img['src']}")
                    return img["src"]
        elif isinstance(resp, requests.Response) and resp.status_code == 403:
            log("🚨 403 FORBIDDEN: App needs 'read_products' scope.")
            
    return None

def download_image(url):
    try:
        log(f"Downloading image from: {url}")
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        log(f"Download failed: {e}")
    return None

def generate_pillow_image(order_data):
    scale = 4
    BG, CARD, BORDER, PRIMARY, SECONDARY, ACCENT = "#F8F9FA", "#FFFFFF", "#E9ECEF", "#212529", "#6C757D", "#CC8E00"
    
    order_id = order_data.get("order_number", "N/A")
    financial_status = order_data.get("financial_status", "Paid").replace("_", " ").title()
    currency = "₹" if order_data.get("currency") == "INR" else order_data.get("currency", "₹")
    
    items = order_data.get("line_items", [])
    processed_items = []
    log(f"Generating image for Order #{order_id}")
    
    for li in items[:4]:
        img_url = fetch_product_image_url(li.get("product_id"), li.get("variant_id"))
        it_img = download_image(img_url) if img_url else None
        processed_items.append({
            "name": li.get("title", "Item"),
            "price": float(li.get("price", 0)),
            "qty": int(li.get("quantity", 1)),
            "image": it_img
        })

    subtotal = float(order_data.get("current_subtotal_price", 0))
    discount = float(order_data.get("total_discounts", 0))
    shipping = float((order_data.get("total_shipping_price_set", {}) or {}).get("shop_money", {}).get("amount", 0))
    tax = float(order_data.get("total_tax", 0))
    total = float(order_data.get("current_total_price", 0))
    paid = total - float(order_data.get("total_outstanding", 0))

    W, H = 800 * scale, 1200 * scale
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    def get_font(size):
        paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/System/Library/Fonts/SFNS.ttf", "/Library/Fonts/Arial.ttf"]
        for p in paths:
            if os.path.exists(p): return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    f_title, f_bold, f_regular = get_font(28*scale), get_font(20*scale), get_font(16*scale)

    def draw_card(x, y, w, h, r=35):
        shadow = Image.new("RGBA", (w+60, h+60), (0,0,0,0))
        ImageDraw.Draw(shadow).rounded_rectangle((30, 30, w+30, h+30), r, fill=(0,0,0,20))
        img.paste(shadow.filter(ImageFilter.GaussianBlur(15)), (x-30, y-30), shadow.filter(ImageFilter.GaussianBlur(15)))
        draw.rounded_rectangle((x, y, x+w, y+h), r, fill=CARD)

    pad = 40 * scale
    draw.text((pad, pad), f"ORDER #{order_id}", font=f_title, fill=PRIMARY)
    draw.text((pad, pad + 40*scale), financial_status, font=f_regular, fill=ACCENT)

    card_y, item_h = 140 * scale, 120 * scale
    card_h = len(processed_items) * item_h + 80 * scale
    draw_card(pad, card_y, W-pad*2, card_h)
    draw.text((pad*2, card_y + 35*scale), "YOUR ITEMS", font=f_bold, fill=SECONDARY)

    curr_y = card_y + 90 * scale
    for item in processed_items:
        sz = 85 * scale
        if item["image"]:
            it_img = item["image"].resize((sz, sz), Image.Resampling.LANCZOS)
            mask = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, sz, sz), 20, fill=255)
            img.paste(it_img, (pad*2, curr_y), mask)
            draw.rounded_rectangle((pad*2, curr_y, pad*2+sz, curr_y+sz), 20, outline=BORDER, width=2)
        else:
            draw.rounded_rectangle((pad*2, curr_y, pad*2+sz, curr_y+sz), 20, fill=BORDER)
        
        tx = pad*2 + sz + 25*scale
        draw.text((tx, curr_y + 10*scale), item["name"][:35], font=f_bold, fill=PRIMARY)
        draw.text((tx, curr_y + 45*scale), f"{currency}{item['price']:.2f} x {item['qty']}", font=f_regular, fill=SECONDARY)
        draw.text((W - pad*2 - 130*scale, curr_y + 30*scale), f"{currency}{item['price']*item['qty']:.2f}", font=f_bold, fill=PRIMARY)
        curr_y += item_h

    sum_y = card_y + card_h + 40 * scale
    draw_card(pad, sum_y, W-pad*2, 380 * scale)
    sy = sum_y + 40*scale
    def row(label, val, y, bold=False, color=PRIMARY):
        draw.text((pad*2, y), label, font=f_bold if bold else f_regular, fill=PRIMARY if bold else SECONDARY)
        v_str = f"{currency}{abs(val):.2f}"
        if label == "Discount": v_str = f"- {v_str}"
        draw.text((W - pad*2 - 150*scale, y), v_str, font=f_bold if bold else f_regular, fill=color)
        return y + 45*scale

    sy = row("Subtotal", subtotal, sy)
    if discount > 0: sy = row("Discount", discount, sy, color="#DC3545")
    sy = row("Shipping", shipping, sy)
    sy = row("Tax", tax, sy)
    draw.line((pad*2, sy+15, W-pad*2, sy+15), fill=BORDER, width=2)
    sy += 45*scale
    sy = row("Total", total, sy, bold=True)
    row("Balance Due", total-paid, sy, bold=True, color=ACCENT)

    out = os.path.join(os.path.dirname(__file__), f"order_{order_id}.png")
    img.save(out, dpi=(300, 300))
    log(f"Generated order image: {out}")
    return out

async def process_order(data):
    order_id = data.get("id")
    if order_id in _processed_orders: return
    _processed_orders.add(order_id)
    try:
        path = generate_pillow_image(data)
        media_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        with open(path, "rb") as f:
            r = requests.post(media_url, headers=headers, files={"file": (os.path.basename(path), f, "image/png"), "type": (None, "image/png"), "messaging_product": (None, "whatsapp")})
            mid = r.json().get("id")
        if mid:
            cust = data.get("customer") or {}
            phone = cust.get("phone") or cust.get("default_address", {}).get("phone")
            if phone:
                p = "".join(filter(str.isdigit, phone))
                if len(p) == 10: p = "91" + p
                payload = {"messaging_product": "whatsapp", "to": p, "type": "template", "template": {"name": TEMPLATE_NAME, "language": {"code": LANG_CODE}, "components": [{"type": "header", "parameters": [{"type": "image", "image": {"id": mid}}]}]}}
                requests.post(f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages", headers=headers, json=payload)
        if os.path.exists(path): os.remove(path)
    except Exception as e: log(f"Process error: {e}")

@app.post("/webhook/orders")
async def webhook(request: Request, background_tasks: BackgroundTasks, x_shopify_hmac_sha256: str = Header(None)):
    body = await request.body()
    log("🔥 WEBHOOK RECEIVED")
    if not verify_shopify_hmac(body, x_shopify_hmac_sha256):
        log("❌ Invalid HMAC")
        raise HTTPException(status_code=401)
    data = json.loads(body)
    log(f"📦 Order: {data.get('order_number')}")
    background_tasks.add_task(process_order, data)
    return {"ok": True}

@app.get("/setup")
async def setup(request: Request):
    token = get_shopify_token()
    host = PUBLIC_HOST_URL or f"{request.headers.get('x-forwarded-proto', 'http')}://{request.headers.get('host')}"
    url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    
    # Simple cleanup
    r = shopify_request(url, headers)
    if isinstance(r, requests.Response) and r.status_code == 200:
        for wh in r.json().get("webhooks", []):
            shopify_request(f"https://{SHOP_DOMAIN}/admin/api/2024-04/webhooks/{wh['id']}.json", headers, method="DELETE")
    
    results = []
    target = f"{host}/webhook/orders"
    for topic in ["orders/create", "orders/paid"]:
        resp = shopify_request(url, headers, method="POST", json={"webhook": {"topic": topic, "address": target, "format": "json"}})
        if isinstance(resp, requests.Response):
            results.append({"topic": topic, "status": resp.status_code, "msg": resp.text if resp.status_code >= 400 else "OK"})
        else:
            results.append({"topic": topic, "status": "Error", "msg": str(resp)})
    return {"message": f"Setup for {host}", "details": results}

@app.get("/")
def health(): return {"status": "ok", "shop": SHOP_DOMAIN}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002)