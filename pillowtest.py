import os
import requests
import json
import uuid
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI()

# ---------- CONFIGURATION ----------
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "996819796855984")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "EAAWkjxOQzMYBRN17smC1dtcZCZBuBZCPMjH4GftuxLPVuj48YEZAJOS5brlIggqAzqiBUJLZBuUv1McYMkfvij07zCvgPgfLeQPYSZAMXCxxlNlp010hX1MGlyt1ShG9CqCZB0iUMCp7KxmMJhkN5CWfwwsP6SjAwmnS4ImsVsUUqeejh0HneX0iBLzgpe0TFEOOQZDZD")
TEMPLATE_NAME = os.getenv("META_TEMPLATE_NAME", "template_book_demo")
LANG_CODE = os.getenv("META_LANG_CODE", "en_US")

PUBLIC_HOST_URL = os.getenv("PUBLIC_HOST_URL")

_cached_token = None

def get_shopify_token(code: str = None):
    """Fetch permanent access token using Client ID/Secret or OAuth code."""
    global _cached_token
    
    if code:
        url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
        payload = {
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
            "code": code
        }
        resp = requests.post(url, json=payload)
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            _cached_token = token
            return token
        return None

    if _cached_token:
        return _cached_token
    
    if SHOPIFY_ACCESS_TOKEN:
        _cached_token = SHOPIFY_ACCESS_TOKEN
        return _cached_token

    # Fetch via Client Credentials
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    try:
        resp = requests.post(url, json=payload)
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            _cached_token = token
            print("Successfully fetched Shopify access token using client credentials.")
            return token
    except Exception as e:
        print(f"Error fetching Shopify token: {e}")
    
    return None

@app.on_event("startup")
async def startup_event():
    """Attempt automatic webhook registration on startup."""
    token = get_shopify_token()
    if PUBLIC_HOST_URL and token:
        print(f"Server starting. Attempting auto-registration for: {PUBLIC_HOST_URL}")
        register_webhook(token, PUBLIC_HOST_URL)
    else:
        print("Automatic webhook registration skipped (missing PUBLIC_HOST_URL or token).")

# ---------- HELPERS ----------

def register_webhook(token: str, address: str):
    """Register orders/paid webhook, but only if it doesn't already exist."""
    domain = SHOP_DOMAIN if SHOP_DOMAIN.endswith(".myshopify.com") else f"{SHOP_DOMAIN}.myshopify.com"
    base_url = f"https://{domain}/admin/api/2024-04/webhooks.json"
    target_address = f"{address}/webhook/shopify"
    headers = {"X-Shopify-Access-Token": token}
    
    try:
        resp = requests.get(base_url, headers=headers)
        if resp.status_code == 200:
            existing = resp.json().get("webhooks", [])
            for wh in existing:
                if wh.get("address") == target_address and wh.get("topic") == "orders/paid":
                    print(f"Webhook already exists at {target_address}. Skipping.")
                    return {"message": "Webhook already exists", "webhook": wh}
    except Exception as e:
        print(f"Error checking existing webhooks: {e}")

    payload = {"webhook": {"topic": "orders/paid", "address": target_address, "format": "json"}}
    resp = requests.post(base_url, json=payload, headers=headers)
    return resp.json()

def fetch_product_image_url(product_id: int, variant_id: int, token: str):
    """Fetch the first image URL for a product or variant from Shopify."""
    if not token: return None
    domain = SHOP_DOMAIN if SHOP_DOMAIN.endswith(".myshopify.com") else f"{SHOP_DOMAIN}.myshopify.com"
    headers = {"X-Shopify-Access-Token": token}
    
    if variant_id:
        try:
            url = f"https://{domain}/admin/api/2024-04/variants/{variant_id}.json"
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                var = resp.json().get("variant", {})
                img_url = var.get("image_id") or var.get("src")
                if img_url: return img_url
        except: pass

    if product_id:
        try:
            url = f"https://{domain}/admin/api/2024-04/products/{product_id}/images.json"
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                images = resp.json().get("images", [])
                if images: return images[0].get("src")
            elif resp.status_code == 403:
                print("🚨 SCOPE ERROR: Needs 'read_products' permission.")
        except Exception as e:
            print(f"Error fetching product image: {e}")
    return None

def download_image(url: str):
    """Download an image and return as PIL Image."""
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return Image.open(BytesIO(resp.content)).convert("RGBA")
    except: pass
    return None

# ---------- IMAGE GENERATION ----------

def generate_pillow_image(order_data: dict, token: str):
    scale = 4
    BG, CARD, BORDER, PRIMARY, SECONDARY, ACCENT = "#f4f5f7", "#ffffff", "#e6e6e6", "#111111", "#6b7280", "#b98900"

    order_id = order_data.get("order_number", "N/A")
    financial_status = order_data.get("financial_status", "pending").replace("_", " ").title()
    fulfillment_status = (order_data.get("fulfillment_status") or "Unfulfilled").title()
    currency = "₹" if order_data.get("currency") == "INR" else order_data.get("currency", "₹")
    
    line_items = order_data.get("line_items", [])
    processed_items = []
    for li in line_items[:4]: # Limit to 4 for card space
        img_url = fetch_product_image_url(li.get("product_id"), li.get("variant_id"), token)
        processed_items.append({
            "name": li.get("title", "Product"),
            "subtitle": li.get("variant_title", ""),
            "price": float(li.get("price", 0)),
            "qty": int(li.get("quantity", 1)),
            "image": download_image(img_url) if img_url else None
        })

    subtotal = float(order_data.get("current_subtotal_price", 0))
    total_discounts = float(order_data.get("total_discounts", 0))
    shipping_set = order_data.get("total_shipping_price_set", {}) or {}
    shipping = float(shipping_set.get("shop_money", {}).get("amount", 0))
    total_tax = float(order_data.get("total_tax", 0))
    total_price = float(order_data.get("current_total_price", 0))
    paid = total_price - float(order_data.get("total_outstanding", total_price))

    W, H = 800 * scale, 1400 * scale
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    def font(size, bold=False):
        try:
            for p in ["/System/Library/Fonts/SFNS.ttf", "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
                if os.path.exists(p): return ImageFont.truetype(p, size)
            return ImageFont.load_default()
        except: return ImageFont.load_default()

    title_font, bold_font, small_font = font(24*scale, True), font(18*scale, True), font(14*scale)

    def shadow_card(x, y, w, h, r=20):
        shadow = Image.new("RGBA", (w, h), (0,0,0,0))
        ImageDraw.Draw(shadow).rounded_rectangle((0,0,w,h), r, fill=(0,0,0,40))
        img.paste(shadow.filter(ImageFilter.GaussianBlur(10)), (x+5, y+5), shadow.filter(ImageFilter.GaussianBlur(10)))

    def text(x, y, t, f=small_font, fill=PRIMARY):
        draw.text((x, y), str(t), font=f, fill=fill)

    pad = 24 * scale
    text(pad, pad, f"#{order_id}", bold_font)
    text(pad + 140*scale, pad, financial_status, small_font, ACCENT)
    text(pad + 360*scale, pad, fulfillment_status, small_font, ACCENT)

    card_w = W - (pad * 2)
    card_h = len(processed_items) * 100 * scale + 100 * scale
    card_y = 80 * scale
    shadow_card(pad, card_y, card_w, card_h)
    text(pad + pad, card_y + pad, f"Items ({len(line_items)})", bold_font, ACCENT)

    y = card_y + 80 * scale
    for item in processed_items:
        img_size = 70 * scale
        if item["image"]:
            item_img = item["image"].resize((img_size, img_size))
            mask = Image.new("L", (img_size, img_size), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, img_size, img_size), 12, fill=255)
            img.paste(item_img, (pad + pad, y), mask)
            draw.rounded_rectangle((pad + pad, y, pad + pad + img_size, y + img_size), 12, outline=BORDER)
        else:
            draw.rounded_rectangle((pad + pad, y, pad + pad + img_size, y + img_size), 12, fill="#f0f0f0")
        tx = pad + pad + img_size + 20 * scale
        text(tx, y, item["name"][:35] + ("..." if len(item["name"]) > 35 else ""), bold_font)
        if item["subtitle"]: text(tx, y + 28*scale, item["subtitle"], small_font, SECONDARY)
        rx = pad + card_w - pad
        draw.text((rx - 280*scale, y), f"{currency}{item['price']:.2f} x {item['qty']}", font=small_font, fill=SECONDARY)
        draw.text((rx - 60*scale, y), f"{currency}{(item['price'] * item['qty']):.2f}", font=bold_font, fill=PRIMARY)
        y += 100 * scale

    card2_y = card_y + card_h + 30 * scale
    shadow_card(pad, card2_y, card_w, 350 * scale)
    text(pad + pad, card2_y + pad, financial_status, bold_font, ACCENT)
    text(pad + pad, card2_y + 70 * scale, "Thank you for your order!", small_font, SECONDARY)

    y_cursor = card2_y + 110 * scale
    def total_row(label, value, curr_y, is_bold=False, color=PRIMARY):
        f = bold_font if is_bold else small_font
        text(pad + pad, curr_y, label, f, PRIMARY)
        val_str = f"{currency}{abs(value):.2f}"
        if label == "Discount": val_str = f"- {val_str}"
        draw.text((pad + card_w - pad - 120*scale, curr_y), val_str, font=f, fill=color)
        return curr_y + 40 * scale

    y_cursor = total_row("Subtotal", subtotal, y_cursor)
    if total_discounts > 0: y_cursor = total_row("Discount", total_discounts, y_cursor, color="#d91e18")
    if shipping > 0: y_cursor = total_row("Shipping", shipping, y_cursor)
    if total_tax > 0: y_cursor = total_row("Tax", total_tax, y_cursor)
    y_cursor += 10 * scale
    draw.line((pad+pad, y_cursor, pad+card_w-pad, y_cursor), fill=BORDER, width=2)
    y_cursor += 20 * scale
    y_cursor = total_row("Total", total_price, y_cursor, is_bold=True)
    y_cursor = total_row("Paid", paid, y_cursor)
    total_row("Balance", max(0, total_price - paid), y_cursor, is_bold=True, color=ACCENT)

    out_name = f"order_{order_id}_{uuid.uuid4().hex[:8]}.png"
    out_path = os.path.join(os.path.dirname(__file__), out_name)
    img.save(out_path, dpi=(300, 300))
    return out_path

# ---------- META ----------

def upload_media(file_path: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "image/png"), "type": (None, "image/png"), "messaging_product": (None, "whatsapp")}
            resp = requests.post(url, headers=headers, files=files)
            if resp.status_code == 200: return resp.json().get("id")
    except: pass
    return None

def send_whatsapp_template(to: str, media_id: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": TEMPLATE_NAME, "language": {"code": LANG_CODE},
            "components": [{"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]}]
        }
    }
    return requests.post(url, headers=headers, json=payload).json()

# ---------- BACKGROUND TASK ----------

async def handle_order_webhook(order_data: dict):
    try:
        token = get_shopify_token()
        if not token: return
        local_path = generate_pillow_image(order_data, token)
        media_id = upload_media(local_path)
        if media_id:
            customer = order_data.get("customer") or {}
            phone = customer.get("phone") or customer.get("default_address", {}).get("phone")
            if phone:
                clean_phone = "".join(filter(str.isdigit, phone))
                if len(clean_phone) == 10: clean_phone = "91" + clean_phone
                send_whatsapp_template(clean_phone, media_id)
        if os.path.exists(local_path): os.remove(local_path)
    except Exception as e: print(f"Error in handle_order_webhook: {e}")

# ---------- ENDPOINTS ----------

@app.post("/webhook/shopify")
async def shopify_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    background_tasks.add_task(handle_order_webhook, data)
    return {"status": "received"}

@app.get("/setup")
async def setup(request: Request, code: str = None, host_url: str = None):
    if code: 
        token = get_shopify_token(code=code)
        return {"access_token": token}
    token = get_shopify_token()
    if not token: return {"error": "No token."}
    if not host_url:
        proto, host = request.headers.get("x-forwarded-proto", "http"), request.headers.get("host")
        if host: host_url = f"{proto}://{host}"
    if host_url:
        register_webhook(token, host_url)
        return {"message": f"Setup complete for {host_url}"}
    return {"error": "Missing host_url"}

@app.get("/cleanup")
async def cleanup():
    token = get_shopify_token()
    if not token: return {"error": "No token."}
    domain = SHOP_DOMAIN if SHOP_DOMAIN.endswith(".myshopify.com") else f"{SHOP_DOMAIN}.myshopify.com"
    url = f"https://{domain}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    deleted = []
    try:
        resp = requests.get(url, headers=headers)
        for wh in resp.json().get("webhooks", []):
            wh_id = wh.get("id")
            del_resp = requests.delete(f"https://{domain}/admin/api/2024-04/webhooks/{wh_id}.json", headers=headers)
            deleted.append({"id": wh_id, "status": del_resp.status_code})
    except: pass
    return {"message": "Cleanup complete", "deleted_count": len(deleted)}

@app.get("/")
def health():
    return {"status": "running", "token": get_shopify_token() is not None}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002)