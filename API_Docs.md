# 📡 Shop Bot — Full API Documentation

All requests require this header:
```
Authorization: Bearer sk_910105cb8d5d5ac95c708688cb668f6a327685a01ebcb207
```

External-store products use the same `ext_...` ID returned by GET /products in
POST /purchase with `product_id` and `qty`. Send a unique `Idempotency-Key`
header for each purchase; reuse that key only when retrying the SAME purchase.
HTTP 202 / pending_verification means the supplier outcome is pending: do not
start another purchase. Check GET /order/ORDER_ID. HTTP 200 / completed
contains delivery in `codes`.

The base URL and key are inside `connection_code` (base64-encoded):
```
conn_eyJrIjoic2tfOTEwMTA1Y2I4ZDVkNWFjOTVjNzA4Njg4Y2I2NjhmNmEzMjc2ODVhMDFlYmNiMjA3IiwidSI6Imh0dHBzOi8vaW5zMjExMjEzMS1md2YxLm9ucmVuZGVyLmNvbS84ZjcxYWVkZDM0OTRlMDQyYmIwNjQwOGY1MGI3ZjkzOCJ9
```

Decode it:
```python
import base64, json
data = json.loads(base64.b64decode(code.replace("conn_", "")))
API_KEY = data["k"]
API_URL = data["u"]
```

---

## 📥 GET Endpoints (9)

### 1. `GET /products`
List all products available to you (hidden ones are excluded).
```json
{
  "success": true,
  "products": [{
    "id": "1",
    "name_ar": "نتفلكس",
    "name_en": "Netflix",
    "desc_ar": "...",
    "desc_en": "...",
    "your_price": 3.5,
    "stock": 45,                 // number or "unlimited"
    "is_manual": false,
    "custom_emoji_id": "5123",
    "name_ar_html": "<tg-emoji emoji-id=\"5123\">✨</tg-emoji> نتفلكس",
    "name_en_html": "<tg-emoji emoji-id=\"5123\">✨</tg-emoji> Netflix",
    "has_premium_emoji": true,
    "desc_has_premium_emoji": true,
    "desc_emoji_ids": ["6677"],
    "all_emoji_ids": ["5123", "6677"]
  }]
}
```

### 2. `GET /product/{id}`
Details of a single product (same schema as `/products` for one item).

### 3. `GET /balance`
Your current balance.
```json
{"success": true, "balance": 25.00}
```

### 4. `GET /orders`
Last 20 orders.
```json
{
  "success": true,
  "orders": [{
    "order_id": "API_xxx",
    "product_id": "1",
    "product_name": "Netflix",
    "qty": 1,
    "total_price": 3.5,
    "status": "completed",        // or "pending_manual"
    "is_manual": false,
    "codes": ["xxxx-yyyy"],
    "buyer_info": "@username",
    "created_at": "2026-06-22 14:30"
  }]
}
```

### 5. `GET /order/{order_id}`
Details of a specific order. Useful for manual orders — poll until `status = completed`.

### 6. `GET /my_prices`
Your custom prices (override the store defaults).
```json
{"success": true, "prices": {"1": 4.0, "2": 5.5}}
```

### 7. `GET /stats`
Your account statistics.
```json
{
  "success": true,
  "total_orders": 152,
  "total_spent": 458.20,
  "balance": 25.00,
  "hidden_products": 3,
  "active_products": 47
}
```

### 8. `GET /webhook`
View current webhook settings.
```json
{
  "success": true,
  "webhook": {
    "url": "https://your-bot.com/webhook",
    "is_active": true,
    "has_secret": true,
    "event_filter": [],
    "failures": 0,
    "last_success": "...",
    "last_failure": ""
  },
  "supported_events": ["stock.added", "stock.sold", "..."]
}
```

### 9. `GET /changes?since=TIMESTAMP&limit=100&event_type=...`
Polling fallback if you don't have a public webhook URL. Returns all events since `since`.

**Parameters:**
- `since` (int) — epoch timestamp; only events newer than this
- `limit` (int, default=100, max=500)
- `event_type` (optional) — filter to a single type

```json
{
  "success": true,
  "events": [{
    "event_id": "evt_1718973_abc",
    "event_type": "stock.sold",
    "timestamp": 1718973123,
    "product_id": "1",
    "data": {"qty_sold": 1, "remaining_stock": 44, "is_manual": false}
  }],
  "count": 1,
  "cursor": 1718973123,           // use this as ?since=cursor next time
  "server_time": 1718973200
}
```
Events older than 24 hours are deleted automatically.

---

## 📤 POST Endpoints (6)

### 10. `POST /purchase`
Buy a product.

**Body:**
```json
{
  "product_id": "1",
  "qty": 1,
  "buyer_info": "@customer"      // optional
}
```

**Response if INSTANT (`is_manual=false`):**
```json
{
  "success": true,
  "status": "completed",
  "order_id": "API_xxx",
  "codes": ["xxxx-yyyy-zzzz"],
  "total_price": 3.5,
  "new_balance": 21.5
}
```

**Response if MANUAL (`is_manual=true`):**
```json
{
  "success": true,
  "status": "pending_manual",
  "order_id": "API_xxx",
  "total_price": 3.5,
  "new_balance": 21.5,
  "note": "Codes will be delivered manually"
}
```

**Errors:**
- `{"error": "Insufficient balance"}` — low balance
- `{"error": "Not enough stock"}` — out of stock
- `{"error": "Product not found"}` — product deleted or hidden
- `{"error": "Invalid quantity"}` — qty ≤ 0

---

### 🤖 ChatGPT Business products (IMPORTANT — different!)

ChatGPT Business products have IDs like `cgpt_xxxxx_yyyyy` and `"source": "chatgpt_business"` in /products.
Each duration (week / month) is a separate product with its own price.

These are SEAT INVITATIONS, not instant codes. To buy one you MUST include the buyer email:

**Body:**
```json
{
  "product_id": "cgpt_xxxxx_yyyyy",
  "email": "buyer@example.com"
}
```

**Success:**
```json
{
  "success": true,
  "type": "chatgpt_business",
  "order_id": "CGAPIxxxxxx",
  "email": "buyer@example.com",
  "expires_at": "2026-10-05T12:00:00",
  "duration_minutes": 43200,
  "message": "Invitation sent. Buyer must accept it from their email.",
  "total": 15.0
}
```

The buyer gets a ChatGPT invite email and must ACCEPT it. The seat auto-expires after the duration.

**ChatGPT errors:**
- `{"error": "email required for ChatGPT products"}` — you forgot the email
- `{"error": "No seats available"}` — seats full
- `{"error": "No seats available (refunded)"}` — refunded, no free seat
- `{"error": "Insufficient balance"}` — top up

> Regular products are unchanged. Only ChatGPT products need the `email` field.

### 11. `POST /set_price`
Set a custom price for a product (applies to your customers only).

**Body:**
```json
{"product_id": "1", "price": 4.5}
```
To remove a custom price: send `"price": null` or 0.

### 12. `POST /set_product`
Customize a product's name/description for your customers (does not affect the source store).

**Body:**
```json
{
  "product_id": "1",
  "name_ar": "اسم مخصص",
  "name_en": "Custom Name",
  "desc_ar": "وصفي",
  "desc_en": "My description"
}
```
All fields are optional. Send empty string to clear.

### 13. `POST /set_webhook` 🔥
Register a URL to receive real-time events (sync).

**Body:**
```json
{
  "url": "https://your-bot.com/webhook",
  "secret": "optional — auto-generated if omitted",
  "event_filter": ["stock.sold", "order.completed"]   // optional — only these types
}
```

**Response:**
```json
{
  "success": true,
  "url": "...",
  "secret": "abc123...",         // save this to verify signatures
  "event_filter": []
}
```

### 14. `POST /delete_webhook`
Delete the registered webhook. (Empty body)

### 15. `POST /test_webhook`
Send a test event to your webhook to confirm it works. (Empty body)
```json
{"success": true, "delivered": true, "url": "..."}
```

---

## 🔔 Webhook Events

The bot sends `POST` to your registered URL on every change:

**Incoming headers:**
```
Content-Type: application/json
X-Event-Id: evt_xxx
X-Event-Type: stock.sold
X-Webhook-Timestamp: 1718973123
X-Webhook-Signature: sha256=HASH
```

**Signature verification (security):**
```python
import hmac, hashlib

def verify(secret, timestamp, body_bytes, received_sig):
    msg = f"{timestamp}.".encode() + body_bytes
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", received_sig)
```

### Supported events:

| Event Type | When it fires | `data` payload |
|------------|---------------|----------------|
| `stock.added` | New stock added | `{product_id, qty_added, remaining_stock}` |
| `stock.sold` | Sale happened | `{product_id, qty_sold, remaining_stock, is_manual, via_api}` |
| `stock.removed` | Stock manually deleted | `{product_id, qty_removed, remaining_stock}` |
| `stock.updated` | A code was edited | `{product_id, remaining_stock}` |
| `product.created` | New product | `{product_id, name_ar, name_en, price, is_manual, stock}` |
| `product.updated` | Product edited | `{product_id, changes: {...}}` |
| `product.deleted` | Product deleted | `{product_id}` |
| `order.completed` | Order finalized | `{order_id, product_id, qty, status, is_manual, via_api}` |

**Example payload:**
```json
{
  "event_id": "evt_1718973_abc",
  "event_type": "stock.sold",
  "timestamp": 1718973123,
  "product_id": "1",
  "data": {
    "product_id": "1",
    "qty_sold": 1,
    "remaining_stock": 44,
    "is_manual": false,
    "via_api": false
  }
}
```

**Webhook behavior:**
- Timeout: 8 seconds
- 3 retry attempts with 1s/2s/3s backoff
- Auto-disabled after 50 consecutive failures
- Events stored in DB are deleted after 24 hours (TTL)

---

## ⚠️ Important notes

1. **Product key:** can be `id` or `_id` (ObjectId). Use whichever the response gives you.
2. **Currency:** all prices in USD.
3. **`is_manual` is critical:** read it before showing the product so the customer knows the delivery type.
4. **HTML in names:** the `*_html` fields contain `<tg-emoji>` tags for premium emojis — use `parse_mode="HTML"`.
5. **Idempotency:** sending the same `purchase` request twice will charge twice — do NOT auto-retry `/purchase`.
6. **Rate limit:** none currently, but be reasonable — don't poll `/products` every second; use `/changes` or webhooks.
