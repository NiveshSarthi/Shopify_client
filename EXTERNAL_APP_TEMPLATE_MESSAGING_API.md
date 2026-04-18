# External API: Direct Template Send (No Campaign)

Use this one endpoint from external apps to send WhatsApp template messages directly into Inbox (without creating a campaign record).

- Endpoint: `POST /api/v1/messages/template-send`
- Auth: `Authorization: Bearer <access_token>`
- Result: message is stored in inbox history as outgoing template message

## What this endpoint supports

- Any approved template available in connected WhatsApp account
- Positional and named variables
- Button variable mapping (URL / quick reply / copy code)
- Media headers (image/video/document) where template requires it
- Image header rendering in inbox (when preview can be resolved)
- Optional sender number override via `phone_number_id`

## Request Body

```json
{
  "number": "919876543210",
  "template_name": "second_template_werw",
  "language_code": "en_US",
  "variable_mapping": {},
  "button_mapping": {},
  "header_media": {},
  "phone_number_id": "645185858686220"
}
```

### Fields

- `number` (string, required): recipient number with country code (digits format recommended, e.g. `919876543210`)
- `template_name` (string, required): approved template name
- `language_code` (string, optional): default `en_US`
- `variable_mapping` (object, optional): template body variables
- `button_mapping` (object, optional): dynamic button values
- `header_media` (object, optional): required for templates with media header
- `phone_number_id` (string, optional): sender number override for multi-number orgs

## Variable Mapping

### Positional template example (`{{1}}`, `{{2}}`)

```json
{
  "variable_mapping": {
    "1": "{{name}}",
    "2": "Gold Plan"
  }
}
```

### Named template example (`{{first_name}}`, `{{offer_code}}`)

```json
{
  "variable_mapping": {
    "first_name": "{{name}}",
    "offer_code": "APRIL50"
  }
}
```

Supported special values:

- `{{name}}` -> resolved from contact name for this number (if contact exists)
- `{{number}}` -> recipient number

## Button Mapping

```json
{
  "button_mapping": {
    "0": {"type": "url", "value": "https://example.com/order/123"},
    "1": {"type": "quick_reply", "value": "CONFIRM_ORDER"}
  }
}
```

Supported button types:

- `url`
- `quick_reply`
- `copy_code`

## Header Media (for media-header templates)

### Image header with `media_id`

```json
{
  "header_media": {
    "media_type": "image",
    "media_id": "2453499048423954"
  }
}
```

### Image header with `image_url`

```json
{
  "header_media": {
    "media_type": "image",
    "image_url": "https://cdn.example.com/banner.jpg"
  }
}
```

Notes for `image_url`:

- Must be publicly reachable by backend
- JPG/JPEG or PNG
- Max 5 MB

## Full Example (Image Header + Variables)

```bash
curl -X POST "$BASE_URL/api/v1/messages/template-send" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "number": "919876543210",
    "template_name": "second_template_werw",
    "language_code": "en_US",
    "variable_mapping": {
      "1": "{{name}}",
      "2": "Gold Plan"
    },
    "header_media": {
      "media_type": "image",
      "image_url": "https://cdn.example.com/offer-banner.jpg"
    },
    "phone_number_id": "645185858686220"
  }'
```

## Success Response

```json
{
  "status": "success",
  "message": "Template message sent",
  "message_id": "wamid.HBg...",
  "inbox_message_id": "6800f7...",
  "number": "919876543210",
  "template_name": "second_template_werw",
  "template_language": "en_US",
  "phone_number_id": "645185858686220"
}
```

## Common Errors

- `401 Invalid token` -> missing/expired bearer token
- `403 You do not have permission ...` -> role/access issue
- `number is required` / `template_name is required`
- `Template not found in connected WhatsApp account`
- `Template header expects <type> media ...`
- `Template requires an image header ...`
- `Template image must be JPG or PNG` / size limit errors

## Practical Notes

- This endpoint does **not** create a campaign.
- Message is still visible in Inbox with template metadata.
- For best personalization with `{{name}}`, ensure the recipient exists as a contact in your org.
