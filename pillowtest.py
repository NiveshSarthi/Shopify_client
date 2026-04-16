import os
import requests
import json
import uuid
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import uvicorn
from dotenv import load_dotenv

# Load environment variables if .env exists
load_dotenv()

app = FastAPI()

# ---------- CONFIGURATION ----------
# Shopify
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN") # e.g. tb57sv-zg.myshopify.com
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

# Meta / WhatsApp
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "996819796855984")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "EAAWkjxOQzMYBRN17smC1dtcZCZBuBZCPMjH4GftuxLPVuj48YEZAJOS5brlIggqAzqiBUJLZBuUv1McYMkfvij07zCvgPgfLeQPYSZAMXCxxlNlp010hX1MGlyt1ShG9CqCZB0iUMCp7KxmMJhkN5CWfwwsP6SjAwmnS4ImsVsUUqeejh0HneX0iBLzgpe0TFEOOQZDZD")
TEMPLATE_NAME = os.getenv("META_TEMPLATE_NAME", "template_book_demo")
LANG_CODE = os.getenv("META_LANG_CODE", "en_US")

# ---------- HELPERS ----------

def get_shopify_token(code: str):
    """Exchange auth code for permanent access token."""
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "code": code
    }
    resp = requests.post(url, json=payload)
    if resp.status_code == 200:
        return resp.json().get("access_token")
    return None

def register_webhook(token: str, address: str):
    """Register 'orders/paid' webhook on Shopify."""
    url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    payload = {
        "webhook": {
            "topic": "orders/paid",
            "address": f"{address}/webhook/shopify",
            "format": "json"
        }
    }
    resp = requests.post(url, json=payload, headers=headers)
    return resp.json()

def fetch_product_image_url(product_id: int, token: str):
    """Fetch the first image URL for a product from Shopify."""
    if not token:
        return None
    url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/products/{product_id}/images.json"
    headers = {"X-Shopify-Access-Token": token}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            images = resp.json().get("images", [])
            if images:
                return images[0].get("src")
    except Exception as e:
        print(f"Error fetching image for product {product_id}: {e}")
    return None

def download_image(url: str):
    """Download an image from a URL and return as PIL Image."""
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            return Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        print(f"Failed to download image from {url}: {e}")
    return None

# ---------- IMAGE GENERATION ----------

def generate_pillow_image(order_data: dict, token: str):
    # ---------- SCALE ----------
    scale = 4

    # ---------- COLORS ----------
    BG = "#f4f5f7"
    CARD = "#ffffff"
    BORDER = "#e6e6e6"
    PRIMARY = "#111111"
    SECONDARY = "#6b7280"
    ACCENT = "#b98900"

    # ---------- DATA EXTRACTION ----------
    order_id = order_data.get("order_number", "N/A")
    financial_status = order_data.get("financial_status", "pending").replace("_", " ").title()
    fulfillment_status = order_data.get("fulfillment_status") or "Unfulfilled"
    fulfillment_status = fulfillment_status.title()
    
    currency = order_data.get("currency", "₹")
    if currency == "INR": currency = "₹"
    
    line_items = order_data.get("line_items", [])
    
    # Process items and fetch images
    processed_items = []
    for li in line_items:
        product_id = li.get("product_id")
        img_url = fetch_product_image_url(product_id, token)
        item_img = download_image(img_url) if img_url else None
        
        processed_items.append({
            "name": li.get("title", "Product"),
            "subtitle": li.get("variant_title", ""),
            "price": float(li.get("price", 0)),
            "qty": int(li.get("quantity", 1)),
            "image": item_img
        })

    subtotal = float(order_data.get("current_subtotal_price", 0))
    total_price = float(order_data.get("current_total_price", 0))
    total_paid = float(order_data.get("total_outstanding", 0)) # Wait, outstanding is balance. 
    # Let's use total_price - total_outstanding for paid.
    paid = total_price - float(order_data.get("total_outstanding", total_price))

    # ---------- IMAGE SETUP ----------
    W, H = 800 * scale, 1400 * scale
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ---------- FONTS ----------
    def font(size, bold=False):
        try:
            # Common paths for Mac
            paths = [
                "/System/Library/Fonts/SFNS.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial.ttf"
            ]
            for p in paths:
                if os.path.exists(p):
                    return ImageFont.truetype(p, size)
            return ImageFont.load_default()
        except:
            return ImageFont.load_default()

    title_font = font(24 * scale, True)
    bold_font = font(18 * scale, True)
    small_font = font(14 * scale)

    # ---------- HELPERS ----------
    def shadow_card(x, y, w, h, r=20):
        shadow = Image.new("RGBA", (w, h), (0,0,0,0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.rounded_rectangle((0,0,w,h), r, fill=(0,0,0,40))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        img.paste(shadow, (x+5, y+5), shadow)
        draw.rounded_rectangle((x, y, x+w, y+h), r, fill=CARD, outline=BORDER)

    def text(x, y, t, f=small_font, fill=PRIMARY):
        draw.text((x, y), str(t), font=f, fill=fill)

    # ---------- HEADER ----------
    pad = 24 * scale
    text(pad, pad, f"#{order_id}", bold_font)
    text(pad + 140*scale, pad, financial_status, small_font, ACCENT)
    text(pad + 360*scale, pad, fulfillment_status, small_font, ACCENT)

    # ---------- CARD 1 (ITEMS) ----------
    card_w = W - (pad * 2)
    item_h = 100 * scale
    card_h = len(processed_items) * item_h + 100 * scale

    card_y = 80 * scale
    shadow_card(pad, card_y, card_w, card_h)

    text(pad + pad, card_y + pad, f"Items ({len(processed_items)})", bold_font, ACCENT)

    y = card_y + 80 * scale

    for item in processed_items:
        img_size = 70 * scale

        if item["image"]:
            try:
                item_img = item["image"].resize((img_size, img_size))
                mask = Image.new("L", (img_size, img_size), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, img_size, img_size), 12, fill=255)
                img.paste(item_img, (pad + pad, y), mask)
                draw.rounded_rectangle((pad + pad, y, pad + pad + img_size, y + img_size), 12, outline=BORDER)
            except Exception as e:
                print(f"Failed to paste image: {e}")
                draw.rounded_rectangle((pad + pad, y, pad + pad + img_size, y + img_size), 12, fill="#f0f0f0")
        else:
            draw.rounded_rectangle((pad + pad, y, pad + pad + img_size, y + img_size), 12, fill="#f0f0f0")

        # text
        tx = pad + pad + img_size + 20 * scale
        text(tx, y, item["name"][:35] + ("..." if len(item["name"]) > 35 else ""), bold_font)

        if item["subtitle"]:
            text(tx, y + 28*scale, item["subtitle"], small_font, SECONDARY)

        # right price
        rx = pad + card_w - pad
        price_str = f"{currency}{item['price']:.2f} x {item['qty']}"
        total_str = f"{currency}{(item['price'] * item['qty']):.2f}"

        draw.text((rx - 280*scale, y), price_str, font=small_font, fill=SECONDARY)
        draw.text((rx - 60*scale, y), total_str, font=bold_font, fill=PRIMARY)

        y += item_h

    # ---------- CARD 2 (SUMMARY) ----------
    card2_y = card_y + card_h + 30 * scale
    card2_h = 350 * scale
    shadow_card(pad, card2_y, card_w, card2_h)

    text(pad + pad, card2_y + pad, financial_status, bold_font, ACCENT)
    text(pad + pad, card2_y + 70 * scale, "Thank you for your order!", small_font, SECONDARY)

    # ---------- TOTALS ----------
    t_y = card2_y + 140 * scale

    def total_row(label, value, curr_y, is_bold=False):
        f = bold_font if is_bold else small_font
        text(pad + pad, curr_y, label, f, PRIMARY)
        val_str = f"{currency}{value:.2f}"
        draw.text((pad + card_w - 100*scale, curr_y), val_str, font=f, fill=PRIMARY)

    total_row("Subtotal", subtotal, t_y)
    total_row("Total", total_price, t_y + 50*scale, True)
    total_row("Paid", paid, t_y + 100*scale)
    total_row("Balance", total_price - paid, t_y + 150*scale, True)

    # ---------- SAVE ----------
    out_name = f"order_{order_id}_{uuid.uuid4().hex[:8]}.png"
    out_path = os.path.join(os.path.dirname(__file__), out_name)
    img.save(out_path, dpi=(300, 300))
    print(f"Saved generated image to {out_path}")
    return out_path

# ---------- META INTEGRATION ----------

def upload_media(file_path: str):
    media_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    mime_type = "image/png"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime_type)}
        data = {"type": "image", "messaging_product": "whatsapp"}
        resp = requests.post(media_url, headers=headers, files=files, data=data)
    
    if resp.status_code != 200:
        print("Media upload failed:", resp.text)
        return None
    return resp.json().get("id")

def send_whatsapp_template(to: str, media_id: str, body_params: list = None):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": TEMPLATE_NAME,
            "language": {"code": LANG_CODE},
            "components": [
                {
                    "type": "header",
                    "parameters": [{"type": "image", "image": {"id": media_id}}]
                }
            ]
        }
    }
    if body_params:
        payload["template"]["components"].append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in body_params]
        })
    
    resp = requests.post(url, headers=headers, json=payload)
    return resp.json()

# ---------- CORE LOGIC ----------

async def handle_order_webhook(order_data: dict):
    """Background task to process order, generate image, and send WhatsApp."""
    try:
        token = SHOPIFY_ACCESS_TOKEN
        # 1. Generate Image
        local_path = generate_pillow_image(order_data, token)
        
        # 2. Upload to Meta
        media_id = upload_media(local_path)
        
        if media_id:
            # 3. Send WhatsApp
            # Extract customer phone
            customer = order_data.get("customer", {})
            phone = customer.get("phone") or customer.get("default_address", {}).get("phone")
            
            # Clean phone number (remove +, spaces, etc. - simple version)
            if phone:
                clean_phone = "".join(filter(str.isdigit, phone))
                # Ensure it has country code (simple default to 91 if 10 digits)
                if len(clean_phone) == 10: clean_phone = "91" + clean_phone
                
                res = send_whatsapp_template(clean_phone, media_id)
                print(f"WhatsApp sent to {clean_phone}: {res}")
            else:
                print("No customer phone number found.")
        
        # Cleanup
        if os.path.exists(local_path):
            os.remove(local_path)
            
    except Exception as e:
        print(f"Error in handle_order_webhook: {e}")

# ---------- ENDPOINTS ----------

@app.post("/webhook/shopify")
async def shopify_webhook(request: Request, background_tasks: BackgroundTasks):
    """Shopify webhook receiver."""
    # Verify HMAC here in production
    data = await request.json()
    print(f"Received Shopify webhook for order: {data.get('order_number')}")
    
    # Process in background to return 200 early
    background_tasks.add_task(handle_order_webhook, data)
    
    return {"status": "received"}

@app.get("/setup")
async def setup(request: Request, code: str = None, host_url: str = None):
    """Helper to get token or register webhook."""
    if code:
        token = get_shopify_token(code)
        return {"access_token": token}
    
    # If host_url not provided, try to detect it from request headers
    if not host_url:
        # Check X-Forwarded-Proto for https (important for ngrok)
        proto = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("host")
        if host:
            host_url = f"{proto}://{host}"
    
    if host_url and SHOPIFY_ACCESS_TOKEN:
        print(f"Attempting to register webhook for {host_url}")
        res = register_webhook(SHOPIFY_ACCESS_TOKEN, host_url)
        return {
            "message": f"Registering webhook at {host_url}/webhook/shopify",
            "webhook_registration": res
        }
    
    return {"message": "Provide 'code' to get token or ensure SHOPIFY_ACCESS_TOKEN is set in .env."}

@app.get("/")
def health():
    return {"status": "running", "message": "Shopify-Meta Webhook Server is live."}

if __name__ == "__main__":
    # To run locally: uvicorn pillowtest:app --reload --port 5002
    uvicorn.run(app, host="0.0.0.0", port=5002)