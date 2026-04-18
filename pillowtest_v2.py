import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime
from io import BytesIO

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont

# Load environment variables
load_dotenv()

app = FastAPI()


# ---------- CONFIGURATION ----------
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")


def sanitize_domain(raw: str | None) -> str:
    if not raw:
        return ""
    domain = str(raw).strip().lower()
    domain = (
        domain.replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .split("/")[0]
    )
    if domain and "." not in domain:
        domain = f"{domain}.myshopify.com"
    return domain


SHOP_DOMAIN = sanitize_domain(os.getenv("SHOP_DOMAIN", ""))
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL")
EXTERNAL_API_TOKEN = os.getenv("EXTERNAL_API_BEARER_TOKEN")
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")

TEMPLATE_FIRST = os.getenv("META_TEMPLATE_FIRST", "metafirsttemplate")
TEMPLATE_SECOND = os.getenv("META_TEMPLATE_NAME", "second_template_werw")
TEMPLATE_THIRD = os.getenv("META_TEMPLATE_THIRD", "metathirdtemplate")

LANG_CODE = os.getenv("META_LANG_CODE", "en_US")
PUBLIC_HOST_URL = os.getenv("PUBLIC_HOST_URL")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_OUTPUT_DIR = os.path.join(
    BASE_DIR,
    os.getenv("GENERATED_IMAGES_DIR", "generated_images"),
)

_cached_token = None
_processed_orders: set[int | str | None] = set()


# ---------- LOGGING ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [pillowtest_v2] %(message)s",
)
logger = logging.getLogger("pillowtest_v2")


def log(message: str, level: str = "info") -> None:
    if level == "debug":
        logger.debug(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)
    sys.stdout.flush()


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "N/A"
    clean = "".join(filter(str.isdigit, str(phone)))
    if len(clean) <= 4:
        return clean
    return f"{clean[:2]}******{clean[-2:]}"


def ensure_image_output_dir() -> str:
    os.makedirs(IMAGE_OUTPUT_DIR, exist_ok=True)
    return IMAGE_OUTPUT_DIR


def resolve_image_path(filename: str) -> str:
    safe_filename = os.path.basename(filename)
    return os.path.join(IMAGE_OUTPUT_DIR, safe_filename)


ensure_image_output_dir()
log(f"Logger initialized at level={LOG_LEVEL}", level="info")
log(
    "Configuration loaded "
    f"shop_domain={SHOP_DOMAIN or 'N/A'} "
    f"public_host_configured={bool(PUBLIC_HOST_URL)} "
    f"external_api_configured={bool(EXTERNAL_API_URL and EXTERNAL_API_TOKEN)} "
    f"image_output_dir={IMAGE_OUTPUT_DIR}",
    level="info",
)


# ---------- IMAGE SERVER ----------
@app.get("/images/{filename}")
async def serve_image(filename: str):
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        log(f"Rejected invalid image filename={filename}", level="warning")
        raise HTTPException(status_code=404, detail="Image not found")

    file_path = resolve_image_path(safe_filename)
    exists = os.path.exists(file_path)
    log(f"Image request filename={safe_filename} exists={exists}", level="info")
    if exists:
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Image not found")


# ---------- SECURITY ----------
def verify_shopify_hmac(data: bytes, hmac_header: str | None) -> bool:
    if not SHOPIFY_CLIENT_SECRET:
        log(
            "SHOPIFY_CLIENT_SECRET missing; bypassing HMAC verification",
            level="warning",
        )
        return True
    if not hmac_header:
        log("Missing X-Shopify-Hmac-Sha256 header", level="warning")
        return False

    hash_value = hmac.new(SHOPIFY_CLIENT_SECRET.encode("utf-8"), data, hashlib.sha256)
    digest = base64.b64encode(hash_value.digest()).decode("utf-8")
    is_valid = hmac.compare_digest(digest, hmac_header)
    log(f"HMAC verification result: {is_valid}", level="debug")
    return is_valid


# ---------- PAYMENT CALCULATION ----------
def get_payment_data(total_price: float | str) -> tuple[int, int, str]:
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


# ---------- EXTERNAL WHATSAPP API SENDER ----------
def send_external_template(
    number: str,
    template_name: str,
    variables: list[str | int] | None = None,
    image_url: str | None = None,
) -> dict:
    if not EXTERNAL_API_URL or not EXTERNAL_API_TOKEN:
        log("External API config missing; cannot send template", level="error")
        return {"error": "external_api_config_missing"}

    clean_number = "".join(filter(str.isdigit, str(number)))
    if len(clean_number) == 10 and not clean_number.startswith("91"):
        clean_number = f"91{clean_number}"
    if len(clean_number) < 12:
        log(f"Invalid destination number={mask_phone(clean_number)}", level="error")
        return {"error": "invalid_destination_number"}

    endpoint = f"{EXTERNAL_API_URL.rstrip('/')}/api/v1/messages/template-send"
    headers = {
        "Authorization": f"Bearer {EXTERNAL_API_TOKEN}",
        "Content-Type": "application/json",
    }

    payload: dict[str, object] = {
        "number": clean_number,
        "template_name": template_name,
        "language_code": LANG_CODE,
        "variable_mapping": {},
        "header_media": {},
    }

    if variables:
        for i, val in enumerate(variables, start=1):
            payload["variable_mapping"][str(i)] = str(val)

    if image_url:
        payload["header_media"] = {"media_type": "image", "image_url": image_url}

    if PHONE_NUMBER_ID:
        payload["phone_number_id"] = PHONE_NUMBER_ID

    log(
        "Sending external template "
        f"template={template_name} to={mask_phone(clean_number)} "
        f"vars={len(variables or [])} image={bool(image_url)}",
        level="info",
    )

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        log(
            f"External API responded status={response.status_code} template={template_name}",
            level="info",
        )

        try:
            body = response.json()
        except ValueError:
            body = {"raw_response": response.text}

        if response.status_code >= 400:
            log(
                f"External API error status={response.status_code} body={body}",
                level="error",
            )
            return {
                "error": "external_api_http_error",
                "status_code": response.status_code,
                "body": body,
            }

        return body if isinstance(body, dict) else {"result": body}
    except Exception as exc:
        log(f"External API send exception: {exc}", level="error")
        return {"error": str(exc)}


# ---------- IMAGE GENERATION ----------
def get_font(size: int, bold: bool = False):
    log(f"Resolving font size={size} bold={bold}", level="debug")
    paths = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                log(f"Using font path: {path}", level="debug")
                return ImageFont.truetype(path, size)
            except Exception:
                log(f"Failed to load font path: {path}", level="warning")
                continue

    log("Falling back to default bitmap font", level="warning")
    return ImageFont.load_default()


def get_shopify_token(force_refresh: bool = False) -> str | None:
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
        response = requests.post(url, json=payload, timeout=10)
        log(f"Shopify token response status={response.status_code}", level="info")
        if response.status_code == 200:
            _cached_token = response.json().get("access_token")
            log("Shopify token fetched successfully", level="info")
            return _cached_token

        log(f"Shopify token fetch failed body={response.text}", level="error")
        return _cached_token
    except Exception as exc:
        log(f"Shopify token request exception: {exc}", level="error")
        return _cached_token


def shopify_request(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    payload: dict | None = None,
) -> requests.Response | None:
    log(f"Shopify request start method={method} url={url}", level="debug")
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=15)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, timeout=15)
        else:
            response = requests.post(url, headers=headers, json=payload, timeout=15)

        log(
            f"Shopify response status={response.status_code} method={method}",
            level="debug",
        )

        if response.status_code == 401:
            log("Shopify request returned 401; refreshing token", level="warning")
            new_token = get_shopify_token(force_refresh=True)
            if new_token:
                headers["X-Shopify-Access-Token"] = new_token
                if method == "GET":
                    log("Retrying Shopify GET after token refresh", level="info")
                    return requests.get(url, headers=headers, timeout=15)
                if method == "DELETE":
                    log("Retrying Shopify DELETE after token refresh", level="info")
                    return requests.delete(url, headers=headers, timeout=15)
                log("Retrying Shopify POST after token refresh", level="info")
                return requests.post(url, headers=headers, json=payload, timeout=15)

            log("Token refresh failed after Shopify 401", level="error")
        return response
    except Exception as exc:
        log(f"Shopify request exception for {url}: {exc}", level="error")
        return None


def fetch_product_image_url(
    product_id: int | None, variant_id: int | None
) -> str | None:
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
        variant_url = (
            f"https://{SHOP_DOMAIN}/admin/api/2024-04/variants/{variant_id}.json"
        )
        variant_response = shopify_request(variant_url, headers)
        if (
            isinstance(variant_response, requests.Response)
            and variant_response.status_code == 200
        ):
            variant = variant_response.json().get("variant", {})
            if variant.get("src"):
                log(f"Found image via variant {variant_id}", level="debug")
                return variant["src"]
        log(f"Variant image lookup failed for {variant_id}", level="warning")

    if product_id:
        product_url = (
            f"https://{SHOP_DOMAIN}/admin/api/2024-04/products/{product_id}/images.json"
        )
        product_response = shopify_request(product_url, headers)
        if (
            isinstance(product_response, requests.Response)
            and product_response.status_code == 200
        ):
            images = product_response.json().get("images", [])
            if images:
                log(f"Found image via product {product_id}", level="debug")
                return images[0].get("src")
        log(f"Product image lookup failed for {product_id}", level="warning")

    log("No image URL found for line item", level="warning")
    return None


def download_image(url: str | None):
    if not url:
        log("download_image called without URL", level="warning")
        return None

    log(f"Downloading image from url={url}", level="debug")
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            log("Image download successful", level="debug")
            return Image.open(BytesIO(response.content)).convert("RGBA")
        log(
            f"Image download failed with status={response.status_code}", level="warning"
        )
    except Exception as exc:
        log(f"Image download exception: {exc}", level="error")
    return None


def generate_pillow_image(order_data: dict) -> str | None:
    order_number = order_data.get("order_number", "N/A")
    log(f"Starting image generation for order={order_number}", level="info")
    try:
        scale = 4
        bg, card, border, primary, secondary = (
            "#F9F9F9",
            "#FFFFFF",
            "#EEEEEE",
            "#1A1A1A",
            "#616161",
        )
        paid_bg, paid_text, paid_dot = "#E3F9E5", "#006328", "#00A15E"
        pending_bg, pending_text, pending_dot = "#FFEA8A", "#4A2E00", "#E29100"
        unfulfilled_bg, unfulfilled_text, unfulfilled_dot = (
            "#FFF4BD",
            "#4A2E00",
            "#E29100",
        )

        financial_status = order_data.get("financial_status", "pending")
        status_text = "Paid" if financial_status == "paid" else "Payment pending"
        status_bg = paid_bg if financial_status == "paid" else pending_bg
        status_txt_color = paid_text if financial_status == "paid" else pending_text
        status_dot_color = paid_dot if financial_status == "paid" else pending_dot

        date_str = "N/A"
        created_at = order_data.get("created_at")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                date_str = dt.strftime("%B %d, %Y at %-I:%M %p").lower()
            except Exception as exc:
                log(
                    f"Date parsing failed for created_at={created_at}: {exc}",
                    level="warning",
                )

        items = order_data.get("line_items", [])
        log(f"Processing {len(items)} line items for image", level="debug")
        processed_items = []
        for idx, line_item in enumerate(items[:4], start=1):
            log(
                "Preparing line item "
                f"index={idx} product_id={line_item.get('product_id')} "
                f"variant_id={line_item.get('variant_id')}",
                level="debug",
            )
            image_url = fetch_product_image_url(
                line_item.get("product_id"),
                line_item.get("variant_id"),
            )
            item_image = download_image(image_url) if image_url else None
            processed_items.append(
                {
                    "name": line_item.get("title", "Item"),
                    "variant": line_item.get("variant_title", ""),
                    "price": float(line_item.get("price", 0)),
                    "qty": int(line_item.get("quantity", 1)),
                    "image": item_image,
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
            f"subtotal={subtotal} discount={discount} shipping={shipping} "
            f"total={total} paid={paid}",
            level="debug",
        )

        width, height = 800 * scale, 1200 * scale
        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        title_font = get_font(32 * scale, True)
        bold_font = get_font(21 * scale, True)
        regular_font = get_font(19 * scale)
        small_font = get_font(17 * scale)
        badge_font = get_font(13 * scale, True)

        def draw_badge(x, y, text, badge_bg, color, dot_color):
            text_width = draw.textlength(text, font=badge_font)
            badge_height, badge_width = 30 * scale, text_width + 35 * scale
            draw.rounded_rectangle(
                (x, y, x + badge_width, y + badge_height),
                badge_height / 2,
                fill=badge_bg,
            )
            cx, cy = x + 13 * scale, y + badge_height / 2
            draw.ellipse(
                (cx - 3 * scale, cy - 3 * scale, cx + 3 * scale, cy + 3 * scale),
                fill=dot_color,
            )
            text_bbox = draw.textbbox((0, 0), text, font=badge_font)
            text_h = text_bbox[3] - text_bbox[1]
            text_y = y + ((badge_height - text_h) / 2) - text_bbox[1]
            draw.text(
                (x + 24 * scale, text_y),
                text,
                font=badge_font,
                fill=color,
            )
            return badge_width

        pad = 50 * scale
        draw.text((pad, pad), f"#{order_number}", font=title_font, fill=primary)
        badge_x = (
            pad + draw.textlength(f"#{order_number}", font=title_font) + 25 * scale
        )
        badge_y = pad + (32 * scale - 30 * scale) / 2 + 4 * scale
        first_badge_width = draw_badge(
            badge_x,
            badge_y,
            status_text,
            status_bg,
            status_txt_color,
            status_dot_color,
        )
        badge_x += first_badge_width + 15 * scale
        draw_badge(
            badge_x,
            badge_y,
            "Unfulfilled",
            unfulfilled_bg,
            unfulfilled_text,
            unfulfilled_dot,
        )
        draw.text((pad, pad + 65 * scale), date_str, font=regular_font, fill=secondary)

        currency = "INR "
        card_y, item_height = 200 * scale, 145 * scale
        card_height = (len(processed_items) * item_height) + 105 * scale
        draw.rounded_rectangle(
            (pad, card_y, width - pad, card_y + card_height),
            20 * scale,
            outline=border,
            width=3 * scale,
            fill=card,
        )
        draw.text(
            (pad * 2, card_y + 35 * scale), "Shipping", font=bold_font, fill=primary
        )
        draw.line(
            (pad * 1.5, card_y + 90 * scale, width - pad * 1.5, card_y + 90 * scale),
            fill=border,
            width=2 * scale,
        )

        current_y = card_y + 120 * scale
        for i, item in enumerate(processed_items):
            size = 110 * scale
            if item["image"]:
                resized = item["image"].resize((size, size), Image.Resampling.LANCZOS)
                mask = Image.new("L", (size, size), 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, size, size), 15 * scale, fill=255
                )
                image.paste(resized, (pad * 2, current_y), mask)
                draw.rounded_rectangle(
                    (pad * 2, current_y, pad * 2 + size, current_y + size),
                    15 * scale,
                    outline=border,
                    width=2 * scale,
                )
            else:
                draw.rounded_rectangle(
                    (pad * 2, current_y, pad * 2 + size, current_y + size),
                    15 * scale,
                    fill=border,
                )

            text_x = pad * 2 + size + 35 * scale
            draw.text(
                (text_x, current_y + 5 * scale),
                item["name"][:40],
                font=bold_font,
                fill=primary,
            )
            if item["variant"]:
                draw.text(
                    (text_x, current_y + 42 * scale),
                    item["variant"],
                    font=small_font,
                    fill=secondary,
                )

            price_y = current_y + (78 * scale if item["variant"] else 50 * scale)
            draw.text(
                (text_x, price_y),
                f"{currency}{item['price']:.2f} x {item['qty']}",
                font=regular_font,
                fill=secondary,
            )

            total_line = f"{currency}{item['price'] * item['qty']:.2f}"
            total_line_width = draw.textlength(total_line, font=bold_font)
            draw.text(
                (width - pad * 2 - total_line_width, current_y + 40 * scale),
                total_line,
                font=bold_font,
                fill=primary,
            )

            if i < len(processed_items) - 1:
                draw.line(
                    (
                        pad * 2,
                        current_y + item_height - 15 * scale,
                        width - pad * 2,
                        current_y + item_height - 15 * scale,
                    ),
                    fill=border,
                    width=1 * scale,
                )
            current_y += item_height

        summary_y = card_y + card_height + 45 * scale
        draw.rounded_rectangle(
            (pad, summary_y, width - pad, summary_y + 540 * scale),
            20 * scale,
            outline=border,
            width=3 * scale,
            fill=card,
        )
        summary_line_y = summary_y + 55 * scale
        draw_badge(
            pad * 2,
            summary_line_y,
            status_text,
            status_bg,
            status_txt_color,
            status_dot_color,
        )
        summary_line_y += 95 * scale

        def draw_summary_row(label, value, y_pos, bold=False, value_color=primary):
            draw.text(
                (pad * 2, y_pos),
                label,
                font=bold_font if bold else regular_font,
                fill=primary if bold else secondary,
            )
            value_str = (
                f"{value}" if isinstance(value, str) else f"{currency}{value:,.2f}"
            )
            if label == "Discount":
                value_str = f"- {value_str}"
            value_width = draw.textlength(
                value_str, font=bold_font if bold else regular_font
            )
            draw.text(
                (width - pad * 2 - value_width, y_pos),
                value_str,
                font=bold_font if bold else regular_font,
                fill=value_color,
            )
            return y_pos + 62 * scale

        summary_line_y = draw_summary_row("Subtotal", subtotal, summary_line_y)
        if discount > 0:
            summary_line_y = draw_summary_row(
                "Discount",
                discount,
                summary_line_y,
                value_color="#DC3545",
            )
        summary_line_y = draw_summary_row(
            "Shipping",
            "Free" if shipping == 0 else shipping,
            summary_line_y,
        )

        draw.line(
            (
                pad * 2.5,
                summary_line_y + 15 * scale,
                width - pad * 2.5,
                summary_line_y + 15 * scale,
            ),
            fill=border,
            width=2 * scale,
        )
        summary_line_y += 85 * scale
        summary_line_y = draw_summary_row(
            "Total (Inclusive tax)",
            total,
            summary_line_y,
            bold=True,
        )
        draw_summary_row(
            "Balance Due",
            total - paid,
            summary_line_y,
            bold=True,
            value_color="#D98E00",
        )

        filename = f"order_{order_number}_{int(time.time())}.png"
        output_dir = ensure_image_output_dir()
        output_path = os.path.join(output_dir, filename)
        image.save(output_path, format="PNG", dpi=(300, 300))

        log(f"Image generated and saved at path={output_path}", level="info")
        return filename
    except Exception as exc:
        log(f"Image generation failed for order={order_number}: {exc}", level="error")
        return None


# ---------- SEQUENCE LOGIC ----------
async def process_order_sequence_v2(data: dict) -> None:
    order_id = data.get("id")
    log(f"Starting order sequence for order_id={order_id}", level="info")
    if order_id in _processed_orders:
        log(f"Skipping duplicate order_id={order_id}", level="warning")
        return

    _processed_orders.add(order_id)
    log(f"Order marked as processed order_id={order_id}", level="debug")

    try:
        customer = data.get("customer") or {}
        shipping_address = data.get("shipping_address") or {}
        billing_address = data.get("billing_address") or {}

        raw_phone = (
            customer.get("phone")
            or shipping_address.get("phone")
            or billing_address.get("phone")
            or ""
        )
        phone = "".join(filter(str.isdigit, str(raw_phone)))[-10:]
        phone = f"91{phone}" if phone else ""
        if len(phone) < 12:
            log(f"Phone validation failed for order_id={order_id}", level="warning")
            return

        log(
            f"Resolved destination phone for order_id={order_id}: {mask_phone(phone)}",
            level="info",
        )

        loop = asyncio.get_running_loop()

        log(f"Sending first template for order_id={order_id}", level="info")
        first_result = await loop.run_in_executor(
            None, send_external_template, phone, TEMPLATE_FIRST
        )
        if isinstance(first_result, dict) and first_result.get("error"):
            log(
                f"First template failed for order_id={order_id}: {first_result}",
                level="error",
            )
        else:
            log(f"First template sent for order_id={order_id}", level="info")

        log("Sleeping 1s between first and second template", level="debug")
        await asyncio.sleep(1)

        filename = await loop.run_in_executor(None, generate_pillow_image, data)
        if not filename:
            log(f"Image generation failed for order_id={order_id}", level="error")
            return
        log(
            f"Generated image filename={filename} for order_id={order_id}", level="info"
        )

        image_url = None
        if PUBLIC_HOST_URL:
            image_url = f"{PUBLIC_HOST_URL.rstrip('/')}/images/{filename}"
            log(
                f"Generated image URL for order_id={order_id}: {image_url}",
                level="info",
            )
        else:
            log(
                "PUBLIC_HOST_URL missing; second template may fail without public image URL",
                level="warning",
            )

        address_source = shipping_address or billing_address
        pin_code = (
            shipping_address.get("zip")
            or shipping_address.get("zipcode")
            or billing_address.get("zip")
            or billing_address.get("zipcode")
            or ""
        )
        address_line = (
            f"{address_source.get('address1', '')}, "
            f"{address_source.get('city', '')}, "
            f"{address_source.get('province', '')}"
        ).strip(", ")
        address_with_pin = address_line or "N/A"
        if pin_code:
            address_with_pin = f"{address_with_pin}, PIN: {pin_code}"

        log(
            f"Resolved address variable for order_id={order_id}: {address_with_pin}",
            level="info",
        )

        second_vars = [customer.get("first_name", "Customer"), address_with_pin]
        log(
            f"Sending second template with media for order_id={order_id}",
            level="info",
        )
        second_result = await loop.run_in_executor(
            None,
            send_external_template,
            phone,
            TEMPLATE_SECOND,
            second_vars,
            image_url,
        )
        if isinstance(second_result, dict) and second_result.get("error"):
            log(
                f"Second template failed for order_id={order_id}: {second_result}",
                level="error",
            )
        else:
            log(f"Second template sent for order_id={order_id}", level="info")

        log("Sleeping 1s before third template", level="debug")
        await asyncio.sleep(1)

        total = float(data.get("current_total_price", 0))
        rounded_order, token_amount, payment_link = get_payment_data(total)
        third_vars = [int(rounded_order), int(token_amount), payment_link]
        log(
            "Sending third template "
            f"order_id={order_id} rounded={rounded_order} token={token_amount}",
            level="info",
        )
        third_result = await loop.run_in_executor(
            None,
            send_external_template,
            phone,
            TEMPLATE_THIRD,
            third_vars,
        )
        if isinstance(third_result, dict) and third_result.get("error"):
            log(
                f"Third template failed for order_id={order_id}: {third_result}",
                level="error",
            )
        else:
            log(f"Third template sent for order_id={order_id}", level="info")

        log(
            f"Order sequence completed order_id={order_id} image_file={filename}",
            level="info",
        )
    except Exception as exc:
        log(f"Sequence error order_id={order_id}: {exc}", level="error")


# ---------- WEBHOOKS & SETUP ----------
@app.post("/webhook/orders")
@app.post("/webhook/shopify")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str | None = Header(None),
):
    log(f"Webhook invoked path={request.url.path}", level="info")
    body = await request.body()
    log(f"Webhook body size={len(body)} bytes", level="debug")

    if not verify_shopify_hmac(body, x_shopify_hmac_sha256):
        log("Webhook HMAC verification failed", level="warning")
        raise HTTPException(status_code=401)

    try:
        payload = json.loads(body)
    except Exception as exc:
        log(f"Failed to parse webhook JSON: {exc}", level="error")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    log(f"Queueing background sequence for order_id={payload.get('id')}", level="info")
    background_tasks.add_task(process_order_sequence_v2, payload)
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
        response = shopify_request(
            url,
            headers,
            method="POST",
            payload={"webhook": {"topic": topic, "address": target, "format": "json"}},
        )
        status = response.status_code if response else "Error"
        results.append({"topic": topic, "status": status})
        log(f"Webhook registration result topic={topic} status={status}", level="info")

    log("Setup route completed", level="info")
    return {
        "message": f"Setup for {host}",
        "used_domain": current_domain,
        "details": results,
    }


@app.get("/cleanup")
async def cleanup():
    log("Cleanup route invoked", level="info")
    output_dir = ensure_image_output_dir()
    count = 0
    for filename in os.listdir(output_dir):
        if filename.startswith("order_") and filename.endswith(
            (".jpg", ".jpeg", ".png")
        ):
            try:
                os.remove(os.path.join(output_dir, filename))
                count += 1
                log(f"Removed generated image file={filename}", level="debug")
            except Exception as exc:
                log(f"Failed to remove file={filename}: {exc}", level="warning")

    log(f"Cleanup completed removed_files={count}", level="info")
    return {"message": f"Cleaned up {count} images"}


@app.get("/")
def health():
    log("Health endpoint invoked", level="debug")
    return {"status": "ok"}


if __name__ == "__main__":
    log("Starting app via __main__", level="info")
    uvicorn.run(app, host="0.0.0.0", port=5002)
