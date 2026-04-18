import os
import logging
import requests
import json
import hmac
import hashlib
import base64
import sys
import time
from io import BytesIO
from datetime import datetime
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


def sanitize_domain(raw):
    if not raw:
        return ""
    d = str(raw).strip().lower()
    d = (
        d.replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .split("/")[0]
    )
    if d and "." not in d:
        d = f"{d}.myshopify.com"
    return d


SHOP_DOMAIN = sanitize_domain(os.getenv("SHOP_DOMAIN", ""))
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")

# Templates
TEMPLATE_FIRST = os.getenv("META_TEMPLATE_FIRST", "metafirsttemplate")
TEMPLATE_SECOND = os.getenv("META_TEMPLATE_NAME", "metasecondtemplate")
TEMPLATE_THIRD = os.getenv("META_TEMPLATE_THIRD", "metathirdtemplate")

LANG_CODE = os.getenv("META_LANG_CODE", "en_US")
PUBLIC_HOST_URL = os.getenv("PUBLIC_HOST_URL")

_cached_token = None
_processed_orders = set()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [pillowtest] %(message)s",
)
logger = logging.getLogger("pillowtest")


def log(msg, level="info"):
    if level == "debug":
        logger.debug(msg)
    elif level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    else:
        logger.info(msg)
    sys.stdout.flush()


def mask_phone(phone):
    if not phone:
        return "N/A"
    clean = "".join(filter(str.isdigit, str(phone)))
    if len(clean) <= 4:
        return clean
    return f"{clean[:2]}******{clean[-2:]}"


log(f"Logger initialized at level={LOG_LEVEL}", level="info")
log(
    "Configuration loaded "
    f"shop_domain={SHOP_DOMAIN or 'N/A'} "
    f"public_host_configured={bool(PUBLIC_HOST_URL)} "
    f"phone_id_configured={bool(PHONE_NUMBER_ID)}",
    level="info",
)


# ---------- SECURITY ----------


def verify_shopify_hmac(data: bytes, hmac_header: str):
    if not SHOPIFY_CLIENT_SECRET:
        log(
            "SHOPIFY_CLIENT_SECRET missing; bypassing HMAC verification",
            level="warning",
        )
        return True
    if not hmac_header:
        log("Missing X-Shopify-Hmac-Sha256 header", level="warning")
        return False
    hash = hmac.new(SHOPIFY_CLIENT_SECRET.encode("utf-8"), data, hashlib.sha256)
    digest = base64.b64encode(hash.digest()).decode("utf-8")
    is_valid = hmac.compare_digest(digest, hmac_header)
    log(f"HMAC verification result: {is_valid}", level="debug")
    return is_valid


# ---------- WHATSAPP UTILITIES ----------


def send_whatsapp_raw(payload):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    msg_type = payload.get("type")
    destination = mask_phone(payload.get("to"))
    log(
        f"Sending WhatsApp payload type={msg_type} to={destination}",
        level="info",
    )
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        log(f"WhatsApp API responded with status {resp.status_code}", level="info")
        if resp.status_code >= 400:
            log(f"WhatsApp API error body: {resp.text}", level="error")
        return resp.json()
    except Exception as e:
        log(f"WhatsApp request failed: {e}", level="error")
        return {"error": str(e)}


def send_simple_template(phone, template_name):
    log(
        f"Preparing simple template send template={template_name} to={mask_phone(phone)}",
        level="info",
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {"name": template_name, "language": {"code": LANG_CODE}},
    }
    return send_whatsapp_raw(payload)


def send_variable_template(phone, template_name, variables: list):
    log(
        "Preparing variable template send "
        f"template={template_name} to={mask_phone(phone)} vars={len(variables)}",
        level="info",
    )
    params = [{"type": "text", "text": str(v)} for v in variables]
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": LANG_CODE},
            "components": [{"type": "body", "parameters": params}],
        },
    }
    return send_whatsapp_raw(payload)


def get_payment_data(total_price):
    total = float(total_price)
    log(f"Calculating payment data for total={total}", level="debug")
    if total < 5000:
        order_rounded = 5000
        token_amount = 500
    else:
        order_rounded = round(total / 5000) * 5000
        token_amount = round(total / 5000) * 1000

    base_url = "https://payments.cashfree.com/forms/"
    if token_amount == 500:
        link = f"{base_url}whoworewhhat500"
    elif token_amount == 1000:
        link = f"{base_url}whoworewhhat"
    else:
        link = f"https://payments.cashfree.com/forms?code=whoworewhhat{token_amount}"
    log(
        f"Payment data calculated rounded={order_rounded} token={token_amount}",
        level="debug",
    )
    return order_rounded, token_amount, link


# ---------- IMAGE GENERATION ----------


def get_font(size, bold=False):
    log(f"Resolving font size={size} bold={bold}", level="debug")
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                log(f"Using font path: {p}", level="debug")
                return ImageFont.truetype(p, size)
            except:
                log(f"Failed to load font path: {p}", level="warning")
                continue
    log("Falling back to default bitmap font", level="warning")
    return ImageFont.load_default()


def fetch_product_image_url(product_id, variant_id):
    log(
        f"Fetching product image for product_id={product_id} variant_id={variant_id}",
        level="debug",
    )
    token = get_shopify_token()
    if not token:
        log("No Shopify token available for image fetch", level="error")
        return None
    headers = {"X-Shopify-Access-Token": token}
    if variant_id:
        url = f"https://{SHOP_DOMAIN}/admin/api/2024-04/variants/{variant_id}.json"
        resp = shopify_request(url, headers)
        if isinstance(resp, requests.Response) and resp.status_code == 200:
            v = resp.json().get("variant", {})
            if v.get("src"):
                log(f"Found image via variant {variant_id}", level="debug")
                return v["src"]
        log(f"Variant image lookup failed for {variant_id}", level="warning")
    if product_id:
        url = (
            f"https://{SHOP_DOMAIN}/admin/api/2024-04/products/{product_id}/images.json"
        )
        resp = shopify_request(url, headers)
        if isinstance(resp, requests.Response) and resp.status_code == 200:
            imgs = resp.json().get("images", [])
            if imgs:
                log(f"Found image via product {product_id}", level="debug")
                return imgs[0].get("src")
        log(f"Product image lookup failed for {product_id}", level="warning")
    log("No image URL found for line item", level="warning")
    return None


def download_image(url):
    if not url:
        log("download_image called without URL", level="warning")
        return None
    log(f"Downloading image from url={url}", level="debug")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            log("Image download successful", level="debug")
            return Image.open(BytesIO(resp.content)).convert("RGBA")
        log(f"Image download failed with status={resp.status_code}", level="warning")
    except Exception as e:
        log(f"Image download exception: {e}", level="error")
    return None


def generate_pillow_image(order_data):
    order_id = order_data.get("order_number", "N/A")
    log(f"Starting image generation for order={order_id}", level="info")
    scale = 4
    BG, CARD, BORDER, PRIMARY, SECONDARY = (
        "#F9F9F9",
        "#FFFFFF",
        "#EEEEEE",
        "#1A1A1A",
        "#616161",
    )
    PENDING_BG, PENDING_TEXT, PENDING_DOT = "#FFEA8A", "#4A2E00", "#E29100"
    PAID_BG, PAID_TEXT, PAID_DOT = "#E3F9E5", "#006328", "#00A15E"
    UNFULFILLED_BG, UNFULFILLED_TEXT, UNFULFILLED_DOT = "#FFF4BD", "#4A2E00", "#E29100"

    # Financial status check
    f_status_raw = order_data.get("financial_status", "pending")
    status_text = "Paid" if f_status_raw == "paid" else "Payment pending"
    status_bg = PAID_BG if f_status_raw == "paid" else PENDING_BG
    status_txt_color = PAID_TEXT if f_status_raw == "paid" else PENDING_TEXT
    status_dot_color = PAID_DOT if f_status_raw == "paid" else PENDING_DOT

    # Use INR string
    CUR = "INR "

    date_str = "N/A"
    created_at = order_data.get("created_at")
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %d, %Y at %-I:%M %p").lower()
        except Exception as e:
            log(
                f"Date parsing failed for created_at={created_at}: {e}", level="warning"
            )

    items = order_data.get("line_items", [])
    log(f"Processing {len(items)} line items for image", level="debug")
    processed_items = []
    for idx, li in enumerate(items[:4], start=1):
        log(
            "Preparing line item "
            f"index={idx} product_id={li.get('product_id')} variant_id={li.get('variant_id')}",
            level="debug",
        )
        img_url = fetch_product_image_url(li.get("product_id"), li.get("variant_id"))
        it_img = download_image(img_url) if img_url else None
        processed_items.append(
            {
                "name": li.get("title", "Item"),
                "variant": li.get("variant_title", ""),
                "price": float(li.get("price", 0)),
                "qty": int(li.get("quantity", 1)),
                "image": it_img,
            }
        )

    subtotal = float(order_data.get("current_subtotal_price", 0))
    discount = float(order_data.get("total_discounts", 0))
    shipping = float(
        (order_data.get("total_shipping_price_set", {}) or {})
        .get("shop_money", {})
        .get("amount", 0)
    )
    total = float(order_data.get("current_total_price", 0))
    paid = total - float(order_data.get("total_outstanding", 0))
    log(
        "Financials prepared "
        f"subtotal={subtotal} discount={discount} shipping={shipping} total={total} paid={paid}",
        level="debug",
    )

    W, H = 800 * scale, 1200 * scale
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = get_font(32 * scale, True)
    f_bold = get_font(21 * scale, True)
    f_regular = get_font(19 * scale)
    f_small = get_font(17 * scale)
    f_badge = get_font(13 * scale, True)

    def draw_badge(x, y, text, bg, color, dot_color):
        tw = draw.textlength(text, font=f_badge)
        bh, bw = 30 * scale, tw + 35 * scale
        draw.rounded_rectangle((x, y, x + bw, y + bh), bh / 2, fill=bg)
        cx, cy = x + 13 * scale, y + bh / 2
        cr = 3 * scale
        draw.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=dot_color)
        draw.text((x + 24 * scale, y + 4 * scale), text, font=f_badge, fill=color)
        return bw

    pad = 50 * scale
    draw.text((pad, pad), f"#{order_id}", font=f_title, fill=PRIMARY)
    bx = pad + draw.textlength(f"#{order_id}", font=f_title) + 25 * scale
    by = pad + (32 * scale - 30 * scale) / 2 + 4 * scale
    bx += (
        draw_badge(bx, by, status_text, status_bg, status_txt_color, status_dot_color)
        + 15 * scale
    )
    draw_badge(bx, by, "Unfulfilled", UNFULFILLED_BG, UNFULFILLED_TEXT, UNFULFILLED_DOT)
    draw.text((pad, pad + 65 * scale), date_str, font=f_regular, fill=SECONDARY)

    card_y, item_h = 200 * scale, 145 * scale
    card_h = (len(processed_items) * item_h) + 105 * scale
    draw.rounded_rectangle(
        (pad, card_y, W - pad, card_y + card_h),
        20 * scale,
        outline=BORDER,
        width=3 * scale,
        fill=CARD,
    )
    draw.text((pad * 2, card_y + 35 * scale), "Shipping", font=f_bold, fill=PRIMARY)
    draw.line(
        (pad * 1.5, card_y + 90 * scale, W - pad * 1.5, card_y + 90 * scale),
        fill=BORDER,
        width=2 * scale,
    )

    cy = card_y + 120 * scale
    for i, item in enumerate(processed_items):
        sz = 110 * scale
        if item["image"]:
            it_img = item["image"].resize((sz, sz), Image.Resampling.LANCZOS)
            mask = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, sz, sz), 15 * scale, fill=255)
            img.paste(it_img, (pad * 2, cy), mask)
            draw.rounded_rectangle(
                (pad * 2, cy, pad * 2 + sz, cy + sz),
                15 * scale,
                outline=BORDER,
                width=2 * scale,
            )
        else:
            draw.rounded_rectangle(
                (pad * 2, cy, pad * 2 + sz, cy + sz), 15 * scale, fill=BORDER
            )

        tx = pad * 2 + sz + 35 * scale
        draw.text((tx, cy + 5 * scale), item["name"][:40], font=f_bold, fill=PRIMARY)
        if item["variant"]:
            draw.text(
                (tx, cy + 42 * scale), item["variant"], font=f_small, fill=SECONDARY
            )

        price_line = f"{CUR}{item['price']:.2f} × {item['qty']}"
        draw.text(
            (tx, cy + (78 * scale if item["variant"] else 50 * scale)),
            price_line,
            font=f_regular,
            fill=SECONDARY,
        )

        total_p_str = f"{CUR}{item['price'] * item['qty']:.2f}"
        tw_total = draw.textlength(total_p_str, font=f_bold)
        draw.text(
            (W - pad * 2 - tw_total, cy + 40 * scale),
            total_p_str,
            font=f_bold,
            fill=PRIMARY,
        )

        if i < len(processed_items) - 1:
            draw.line(
                (
                    pad * 2,
                    cy + item_h - 15 * scale,
                    W - pad * 2,
                    cy + item_h - 15 * scale,
                ),
                fill=BORDER,
                width=1 * scale,
            )
        cy += item_h

    sum_y = card_y + card_h + 45 * scale
    draw.rounded_rectangle(
        (pad, sum_y, W - pad, sum_y + 540 * scale),
        20 * scale,
        outline=BORDER,
        width=3 * scale,
        fill=CARD,
    )
    sy = sum_y + 55 * scale
    draw_badge(pad * 2, sy, status_text, status_bg, status_txt_color, status_dot_color)
    sy += 95 * scale

    def row(l, v, y, b=False, c=PRIMARY):
        draw.text(
            (pad * 2, y),
            l,
            font=f_bold if b else f_regular,
            fill=PRIMARY if b else SECONDARY,
        )
        v_str = f"{v}" if isinstance(v, str) else f"{CUR}{v:,.2f}"
        if l == "Discount":
            v_str = f"- {v_str}"
        tw = draw.textlength(v_str, font=f_bold if b else f_regular)
        draw.text((W - pad * 2 - tw, y), v_str, font=f_bold if b else f_regular, fill=c)
        return y + 62 * scale

    sy = row("Subtotal", subtotal, sy)
    if discount > 0:
        sy = row("Discount", discount, sy, c="#DC3545")
    sy = row("Shipping", "Free" if shipping == 0 else shipping, sy)
    draw.line(
        (pad * 2.5, sy + 15 * scale, W - pad * 2.5, sy + 15 * scale),
        fill=BORDER,
        width=2 * scale,
    )
    sy += 85 * scale
    sy = row("Total (Inclusive tax)", total, sy, b=True)
    row("Balance Due", total - paid, sy, b=True, c="#D98E00")

    out = os.path.join(os.path.dirname(__file__), f"order_{order_id}.png")
    img.save(out, dpi=(300, 300))
    log(f"Image generated and saved at path={out}", level="info")
    return out


# ---------- SERVER LOGIC ----------


def get_shopify_token(force_refresh=False):
    global _cached_token
    if _cached_token and not force_refresh:
        log("Using cached Shopify token", level="debug")
        return _cached_token
    if SHOPIFY_ACCESS_TOKEN and not force_refresh:
        log("Using static Shopify access token from environment", level="debug")
        _cached_token = SHOPIFY_ACCESS_TOKEN
        return _cached_token
    if not SHOP_DOMAIN:
        log("SHOP_DOMAIN missing; cannot fetch Shopify token", level="error")
        return _cached_token
    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        log("Shopify client credentials missing; cannot fetch token", level="error")
        return _cached_token
    url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    try:
        log(f"Fetching Shopify token from domain={SHOP_DOMAIN}", level="info")
        resp = requests.post(url, json=payload, timeout=10)
        log(f"Shopify token response status={resp.status_code}", level="info")
        if resp.status_code == 200:
            _cached_token = resp.json().get("access_token")
            log("Shopify token fetched successfully", level="info")
            return _cached_token
        log(f"Shopify token fetch failed body={resp.text}", level="error")
    except Exception as e:
        log(f"Shopify token request exception: {e}", level="error")
    return _cached_token


def shopify_request(url, headers, method="GET", json=None):
    log(f"Shopify request start method={method} url={url}", level="debug")
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=15)
        elif method == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=15)
        else:
            resp = requests.post(url, headers=headers, json=json, timeout=15)
        log(
            f"Shopify response status={resp.status_code} method={method}", level="debug"
        )
        if resp.status_code == 401:
            log("Shopify request returned 401; refreshing token", level="warning")
            new_token = get_shopify_token(force_refresh=True)
            if new_token:
                headers["X-Shopify-Access-Token"] = new_token
                if method == "GET":
                    log("Retrying Shopify GET after token refresh", level="info")
                    return requests.get(url, headers=headers, timeout=15)
                log("Retrying Shopify POST after token refresh", level="info")
                return requests.post(url, headers=headers, json=json, timeout=15)
            log("Token refresh failed after Shopify 401", level="error")
        return resp
    except Exception as e:
        log(f"Shopify request exception for {url}: {e}", level="error")
        return None


async def process_order_sequence(data):
    order_id = data.get("id")
    log(f"Starting order sequence for order_id={order_id}", level="info")
    if order_id in _processed_orders:
        log(f"Skipping duplicate order_id={order_id}", level="warning")
        return
    _processed_orders.add(order_id)
    log(f"Order marked as processed order_id={order_id}", level="debug")
    try:
        cust = data.get("customer") or {}
        addr = data.get("shipping_address") or data.get("billing_address") or {}
        p = (
            "91"
            + "".join(
                filter(str.isdigit, cust.get("phone") or addr.get("phone") or "")
            )[-10:]
        )
        if len(p) < 12:
            log(f"Phone validation failed for order_id={order_id}", level="warning")
            return

        log(
            f"Resolved destination phone for order_id={order_id}: {mask_phone(p)}",
            level="info",
        )

        log(f"Sending first template for order_id={order_id}", level="info")
        send_simple_template(p, TEMPLATE_FIRST)

        log("Sleeping 1s between WhatsApp messages", level="debug")
        time.sleep(1)

        log(f"Generating order image for order_id={order_id}", level="info")
        path = generate_pillow_image(data)
        if not path or not os.path.exists(path):
            log(f"Image generation failed for order_id={order_id}", level="error")
            return

        log(f"Generated image path={path} for order_id={order_id}", level="info")

        m_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        mid = None
        with open(path, "rb") as f:
            log(
                f"Uploading image to WhatsApp media API for order_id={order_id}",
                level="info",
            )
            r = requests.post(
                m_url,
                headers=headers,
                files={
                    "file": (os.path.basename(path), f, "image/png"),
                    "type": (None, "image/png"),
                    "messaging_product": (None, "whatsapp"),
                },
                timeout=30,
            )
            log(
                f"Media upload response status={r.status_code} order_id={order_id}",
                level="info",
            )
            mid = r.json().get("id")

        if mid:
            log(f"Media id received for order_id={order_id}", level="info")
            full_addr = f"{addr.get('address1', '')}, {addr.get('city', '')}, {addr.get('province', '')} {addr.get('zipcode', '')}".strip(
                ", "
            )
            payload = {
                "messaging_product": "whatsapp",
                "to": p,
                "type": "template",
                "template": {
                    "name": TEMPLATE_SECOND,
                    "language": {"code": LANG_CODE},
                    "components": [
                        {
                            "type": "header",
                            "parameters": [{"type": "image", "image": {"id": mid}}],
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": cust.get("first_name", "Customer"),
                                },
                                {"type": "text", "text": full_addr or "N/A"},
                            ],
                        },
                    ],
                },
            }
            log(
                f"Sending second template with media for order_id={order_id}",
                level="info",
            )
            res = send_whatsapp_raw(payload)
            if isinstance(res, dict) and res.get("error"):
                log(
                    f"Second template failed for order_id={order_id}: {res.get('error')}",
                    level="error",
                )
            else:
                log(f"Second template sent for order_id={order_id}", level="info")
        else:
            log(
                f"Media id missing; skipping second template for order_id={order_id}",
                level="warning",
            )

        if os.path.exists(path):
            os.remove(path)
            log(f"Temporary image removed path={path}", level="debug")

        log("Sleeping 1s before third template", level="debug")
        time.sleep(1)

        total = float(data.get("current_total_price", 0))
        o_round, t_amt, link = get_payment_data(total)
        log(
            "Sending third template "
            f"order_id={order_id} rounded={o_round} token={t_amt}",
            level="info",
        )
        send_variable_template(p, TEMPLATE_THIRD, [int(o_round), int(t_amt), link])
        log(f"Order sequence completed order_id={order_id}", level="info")
    except Exception as e:
        log(f"Sequence error order_id={order_id}: {e}", level="error")


@app.post("/webhook/orders")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(None),
):
    log("Webhook /webhook/orders invoked", level="info")
    body = await request.body()
    log(f"Webhook body size={len(body)} bytes", level="debug")
    if not verify_shopify_hmac(body, x_shopify_hmac_sha256):
        log("Webhook HMAC verification failed", level="warning")
        raise HTTPException(status_code=401)

    try:
        payload = json.loads(body)
    except Exception as e:
        log(f"Failed to parse webhook JSON: {e}", level="error")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    log(f"Queueing background sequence for order_id={payload.get('id')}", level="info")
    background_tasks.add_task(process_order_sequence, payload)
    return {"ok": True}


@app.get("/setup")
async def setup(request: Request):
    log("Setup route invoked", level="info")
    current_domain = sanitize_domain(os.getenv("SHOP_DOMAIN", ""))
    token = get_shopify_token()
    if not token:
        log("Setup aborted because Shopify token is unavailable", level="error")
        return {"error": "Could not get Shopify token. Check your .env config."}

    host = PUBLIC_HOST_URL or f"{request.url.scheme}://{request.url.netloc}"
    url = f"https://{current_domain}/admin/api/2024-04/webhooks.json"
    headers = {"X-Shopify-Access-Token": token}
    target = f"{host}/webhook/orders"
    log(f"Registering webhooks for target={target}", level="info")
    results = []
    for topic in ["orders/create", "orders/paid"]:
        log(f"Registering webhook topic={topic}", level="info")
        resp = shopify_request(
            url,
            headers,
            method="POST",
            json={"webhook": {"topic": topic, "address": target, "format": "json"}},
        )
        status = resp.status_code if resp else "Error"
        log(f"Webhook registration result topic={topic} status={status}", level="info")
        results.append({"topic": topic, "status": status})

    log("Setup route completed", level="info")
    return {
        "message": f"Setup for {host}",
        "used_domain": current_domain,
        "details": results,
    }


@app.get("/")
def health():
    log("Health endpoint invoked", level="debug")
    return {"status": "ok"}


if __name__ == "__main__":
    log("Starting app via __main__", level="info")
    uvicorn.run(app, host="0.0.0.0", port=5002)
