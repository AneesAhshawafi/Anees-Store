REST Architecture
A strictly typed, highly atomic engine. Securely drain wallet balances and synchronously map dynamic admin-altered constraints into your SaaS.

Target Instance
https://api.bunaistore.shop/v1
Copy URI
Authentication
Every request must include the X-API-Key header. Keys are generated per-user inside the Telegram bot. Empty, whitespace-only, or revoked keys return 401.

Example Header
Copy
-H "X-API-Key: shop:12312..."
Type & Error Constraints
401
Unauthorized Header
X-API-Key is invalid, blank, missing, or compromised.
402
Insufficient Funds (NSF)
Volatile balance calculation failed the lock. Refuse service.
403
Forbidden / Suspended
Account permanently banned. API globally suspended for token.
404
Not Found
Product deleted by admin, or order doesn't belong to you.
409
Stock Conflict
Race condition: stock was purchased by another buyer between your catalog read and checkout.
429
Rate Limited
Too many requests. GET endpoints: 60/min. POST endpoints: 20/min. Per API key. Retry after the Retry-After header value.
503
Service Unavailable
Marketplace Maintenance Mode active or API globally disabled by admin.
502
Bad Gateway
Upstream provider failed — virtual number purchase could not be completed. Balance is NOT deducted.
Rate Limits
All /v1/ endpoints are rate-limited per API key. GET responses include Cache-Control: public, max-age=30 headers — clients should respect these to reduce unnecessary calls.

GET Endpoints
60 req / min

POST Endpoints
20 req / min

GET
/v1/me
cURL Request
Copy
curl -X GET "https://api.bunaistore.shop/v1/me" \
  -H "X-API-Key: YOUR_KEY"
Response
Copy
{
  "user_id": 12345678,
  "display_name": "Premium Dev",
  "balance": 1500.50,
  "api_orders": 45,
  "api_spent": 142.00
}
Data Dictionary
user_id
integer
Your Telegram user ID. Always present, never changes.
display_name
string | null
Nullable: Returns null if the user has no Telegram display name set.
balance
float
Current wallet balance in USD. Volatile — changes on every top-up or purchase.
api_orders
integer
Counts only orders placed via the API. Bot-placed orders are excluded.
api_spent
float
Total USD spent via API orders only. Does not include bot purchases or top-ups.
GET
/v1/products
cURL Execution
Copy
curl -X GET \
  "https://api.bunaistore.shop/v1/products?view=variants&type=auto&limit=10" \
  -H "X-API-Key: YOUR_KEY"
Payload List
Copy
[
  {
    "product_id": "ab360422",
    "group_id": "d8b627d5",
    "group_name": "Amazon Prime",
    "variant_name": "1 month",
    "display_name": "Amazon Prime > 1 month",
    "price": 1.00,
    "stock_type": "manual",
    "stock_count": 11,
    "infinite_stock": false,
    "warranty_hours": 1,
    "has_promo": false,
    "group_share_path": "/group_d8b627d5",
    "plan_share_path": "/plan_ab360422_d8b627d5"
  }
]
Query Parameters
view
string | default: variants
Use variants for exact purchasable plans or groups for grouped collection cards.
type
string | optional
Filter by "auto" or "manual". Omit to return all types. Any other value is silently ignored and returns everything.
include_note
boolean | default: false
If true, appends the heavy HTML-formatted product descriptions to each object. Off by default to optimize bandwidth.
offset
integer | default: 0
Skip the first N products. Use with limit for pagination.
limit
integer | default: 50
Hardcapped at 100. Any value above 100 is silently clamped down.
Response Fields
group_id / variant_name
string
Grouped catalog variants return both the collection identity and exact plan identity for cleaner client UX.
group_share_path / plan_share_path
string | null
Public bridge routes on api.bunaistore.shop for collections and exact plans.
stock_type
enum string
"auto" = instant digital delivery (accounts/keys). "manual" = admin fulfills the order externally after purchase.
stock_count
integer
Returns 9999 when infinite_stock is true. Otherwise reflects real-time available stock. Products with 0 stock are automatically pruned from results.
infinite_stock
boolean
Critical: If true, unlimited supply. Ignore stock_count. Admin can toggle this live, switching between unlimited and finite.
warranty_hours
integer
0 = no warranty. Otherwise, the number of hours the warranty covers from purchase time.
has_promo
boolean
Indicates bulk discount tiers exist. Use /v1/products/{id} to inspect the actual tier breakpoints.
GET
/v1/product-groups
Use this for collection-level browsing. Grouped products such as Amazon Prime or Netflix families appear once here, with optional embedded plans via include_variants=true.

curl -X GET \
  "https://api.bunaistore.shop/v1/product-groups?include_variants=true&limit=10" \
  -H "X-API-Key: YOUR_KEY"
GET
/v1/products/{id}
cURL Execution
Copy
curl -X GET \
  "https://api.bunaistore.shop/v1/products/PRD_ajS8x" \
  -H "X-API-Key: YOUR_KEY"
Deep Response
Copy
{
  "product_id": "PRD_ajS8x",
  "name": "Netflix Premium",
  "note": "Read TOS before buying.",
  "price": 3.50,
  "stock_type": "auto",
  "stock_count": 12,
  "infinite_stock": false,
  "warranty_hours": 720,
  "promo_active": true,
  "promo_cb": 5.0,
  "promo_tiers": {"5": 5.0, "10": 12.0}
}
Extra Properties (vs List)
note
string
Pre-purchase description set by admin. Returns "" (empty string) if not configured — never null. Admin can update this at any time. Always display to end users before checkout.
promo_active
boolean
Whether bulk discount is currently enabled. Even if promo_tiers has data, discounts only apply when this is true.
promo_cb
float
Global cashback percentage applied on top of tier discounts. 0.0 if disabled.
promo_tiers
object | {}
Map of {"min_qty": discount_%}. Example: {"5": 5.0} = buying 5+ items gives 5% off. Returns empty {} when no tiers configured.
POST
/v1/orders
Submit Request
Copy
curl -X POST "https://api.bunaistore.shop/v1/orders" \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "PRD_XYZ",
    "qty": 1
  }'
Response
Copy
{
  "status": "success",
  "order": {
    "order_id": "ORDx8kLm2Qp",
    "product_id": "PRD_ajS8x",
    "product_name": "Netflix Premium",
    "qty": 3,
    "timestamp": "2024-06-19T14:32:07",
    "base_price": 10.50,
    "total_paid": 9.98,
    "discount_applied": 0.52,
    "stock_type": "auto",
    "items": [
      "Full account details...\nRegion: US\nPlan: Premium...",
      "Full account details...\nRegion: CA\nPlan: Premium...",
      "Full account details...\nRegion: UK\nPlan: Premium..."
    ],
    "warranty_expires": "2024-07-19T14:32:07",
    "delivered": true,
    "refunded": false,
    "after_note": "Use CA VPN for login."
  }
}
⚠
Large Payload Warning
Each string in the items array can be hundreds of lines long — full account credentials, license keys, multi-paragraph instructions, or delivery blobs with \n newlines embedded. Buying qty: 12 of a text-heavy product can produce a response exceeding 50KB+. Always handle items as arbitrarily long strings. Do not truncate or assume single-line values.

Payload Architecture
qty
integer | default: 1
Omitting defaults to 1. Values ≤ 0 trigger 400.
include_after_note
boolean | default: false
If true, appends the post-purchase setup instructions to the response. Off by default to save transit bandwidth.
timestamp
ISO string
UTC timestamp of when the order was atomically committed. Always present.
base_price
float
Original total before any promo discount. Calculated as price × qty.
total_paid
float
Actual amount deducted from wallet after promo tiers applied.
discount_applied
float
base_price - total_paid. Returns 0.0 if no promo was active.
items
array<string>
Auto products: Array length equals qty. Each string is one delivered item — can range from a short email:password to a multi-paragraph blob exceeding 1KB+ with embedded \n newlines (full account details, license keys, instructions, etc). Always iterate the full array and render each item independently. Do not assume single-line format.

Manual products: Always returns [] — admin fulfills externally via bot.
warranty_expires
ISO string | null
null if warranty_hours was 0 at purchase time.
refunded
boolean
Admin can issue refunds retrospectively. Always starts false.
after_note
string
Not stored in order. Fetched live from the product at response time. Returns "" (empty string) if unset or if admin later deletes the product entirely. Do NOT cache — re-fetch via /v1/orders/{id} for latest instructions.
GET
/v1/orders/{id}
cURL Execution
Copy
curl -X GET \
  "https://api.bunaistore.shop/v1/orders/ORD_1A..." \
  -H "X-API-Key: YOUR_KEY"
Deep Response
Copy
{
  "order": {
    "order_id": "ORD_1A...",
    "product_id": "PRD_ajS8x",
    "product_name": "Netflix Premium",
    "qty": 1,
    "timestamp": "2024-06-19T...",
    "total_paid": 3.50,
    "stock_type": "auto",
    "items": ["email:pass"],
    "warranty_expires": "2024-07-19T...",
    "delivered": true,
    "refunded": false,
    "after_note": "Use Canada VPN."
  }
}
Key Differences vs POST
total_paid
float
Unlike POST which returns both base_price and discount_applied, the lookup only returns the final paid amount.
delivered
boolean
Always true for "auto" orders. For "manual" orders it is false until the admin delivers the order (and stays false if refunded). Poll this endpoint to find out when it flips.
after_note
string
Live pointer: NOT stored in order history. Fetched from the product at query time. If admin updates instructions or deletes the product after your purchase, this value changes. Returns "" if the product was deleted.
refunded
boolean
Can change from false to true if admin issues a refund after the original purchase.
Developer

Identity
Manage your developer profile. All endpoints require X-API-Key authentication.

GET
/v1/developer/me
Returns your API key and live wallet balance — all in one call. No separate balance endpoint needed.

cURL
Copy
curl -X GET "https://api.bunaistore.shop/v1/developer/me" \
  -H "X-API-Key: YOUR_KEY"
200 OK
Copy
{
  "user_id":        12345678,
  "api_key":        "sk_live_a1b2...",
  "balance":        142.50     // live, always current
}
LIVE
Swagger UI Playground
Authorize with your API Key and execute live HTTP requests directly from your browser. Responses will print natively below.

Marketplace Reseller API
 1.1.0 
OAS 3.1
/openapi.json
Automated API for purchasing and managing marketplace digital products.

Catalog supports both single products and grouped product collections with multiple plans.


Authorize
Profile
Authenticated user profile and API usage.



GET
/v1/me
Get User Details



Catalog
Browse single products, grouped product collections, and plan variants.



GET
/v1/products
List Catalog Variants




GET
/v1/product-groups
List Product Groups




GET
/v1/product-groups/{group_id}
Get Product Group With Variants




GET
/v1/products/{product_id}
Get Exact Product Variant



Orders
Create and inspect user orders.



POST
/v1/orders
Create New Order




GET
/v1/orders/{order_id}
View Existing Order



Numbers
Buy virtual phone numbers for Telegram verification.


Links
Bot deep-link bridge metadata for products, groups, and plans.



GET
/v1/links/product/{product_id}
Get Product Link Metadata




GET
/v1/links/group/{group_id}
Get Group Link Metadata



Developer
Developer settings: API key and account balance.



GET
/v1/developer/me
Get Developer Settings




Schemas
HTTPValidationErrorExpand allobject
OrderRequestExpand allobject
ValidationErrorExpand allobject