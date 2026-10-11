ELITE TOOLS STORE / DEVELOPERS
Your store.
Our product API.
Connect your website or bot to our product catalog and deliver orders using your shop wallet.

Authentication
Products
Balance
Orders
Base URL: https://api.elitetoolz.store/api
Both @ELITE_TOOLS_STORE_BOT and @EliteToolzBOT use this same API URL and documentation. Your API key selects the bot that issued it: its catalog, prices, wallet balance and order history. Fund that same bot wallet. Keys and balances are separate between bots; an order deducts only from the issuing bot wallet.

1 · Get connected
Open the Telegram bot → Reseller API → Generate New API Key. Fund your bot wallet, then send your key from your server in this header:

X-API-Key: YOUR_API_KEY
Keep keys private. Never include them in browser code or public repositories.

2 · List products
GET /products
curl https://api.elitetoolz.store/api/products -H "X-API-Key: YOUR_API_KEY"
Returns {ok: true, products: [...]}. Product fields: id, name, parentProductId, type, category, price, stock, warranty, description. Prices are in USDT. Use id when placing an order. Stock is checked again at purchase time; null is not a numeric count.

3 · Wallet balance
GET /balance
curl https://api.elitetoolz.store/api/balance -H "X-API-Key: YOUR_API_KEY"
Returns ok, userId, balance and referralBalance. Orders spend the main balance.

4 · Place an order
POST /order
Content-Type: application/json
X-API-Key: YOUR_API_KEY

{"productId":"PRODUCT_ID","quantity":1}
Use an integer quantity from 1 to 100. Free products cannot be purchased through this endpoint. Successful orders deduct price × quantity from the wallet.

// Example response — illustrative values
{"ok":true,"orderId":"EXAMPLE","product":"Example product","quantity":1,"charged":1.5,"newBalance":8.5,"items":["EXAMPLE_CREDENTIALS"]}
Preserve complete delivery blocks and line breaks in items. Some entries start with __CREDENTIAL__:; remove only that leading marker for display. Never expose customer credentials publicly.

Retry policy
This reseller API does not accept a client idempotency key. Repeating POST /order may create a second purchase. After a timeout or HTTP 503, check your bot order history and contact support before retrying.

Error responses
{"ok":false,"error":"Description"}
400 — invalid input or free product
401 — missing or invalid X-API-Key
402 — insufficient wallet balance
404 — product unavailable
409 — stock unavailable
503 — order processing issue; contact support before retrying
Compatible paths
/reseller/products, /reseller/balance and /reseller/buy are aliases under the same /api base URL.

Integration workflow
Create an API key in the Telegram bot and store it as ELITE_API_KEY in your server environment.
Call GET /balance to confirm authentication and available funds.
Call GET /products and save the selected product ID. Your selling price is controlled by your own shop; this API charges its current catalog price.
Before ordering, validate quantity and create a unique record in your own system. Prevent double-clicks and simultaneous submissions for that record.
Submit POST /order once. Store the returned orderId, charged amount and delivery blocks securely.
Deliver the saved result to your customer. If customer delivery fails, resend your saved result instead of purchasing again.
Complete endpoint reference
All three endpoints require X-API-Key. Bearer tokens and ?action=... are not the authentication or routing format for this API. Send JSON for POST requests.

GET /products · response fields
The response contains ok: true and a products array. The endpoint lists available catalog entries; an entry may have zero stock.

Field	Meaning
id	String used as productId in an order.
name	Display name.
parentProductId	Parent ID or null. Use this to group variants.
type	tool or sub_tool.
category	Catalog category.
price	Current unit cost in USDT.
stock	Numeric stock count, or null where a finite count is not supplied. Null does not guarantee that any order will succeed.
warranty	Product warranty text.
description	Product description.
{
  "ok": true,
  "products": [
    {
      "id": "EXAMPLE_PRODUCT",
      "name": "Example product",
      "parentProductId": null,
      "type": "tool",
      "category": "Example",
      "price": 1.5,
      "stock": 12,
      "warranty": "See product terms",
      "description": "Illustrative catalog item"
    }
  ]
}
Example values only. Stock is a snapshot, not a reservation. Supplier refresh failures may leave cached stock values; order-time checks remain decisive.

GET /balance · response fields
{
  "ok": true,
  "userId": 123456789,
  "balance": 10,
  "referralBalance": 0
}
These are example values. balance is the main spendable wallet used by this API. referralBalance is reported separately; it is not added to the purchasing balance by this endpoint.

POST /order · request fields
Field	Client requirement
productId	Required product ID from the catalog.
quantity	Send an explicit whole number from 1 to 100.
Always validate quantity in your application. The current server coerces values to numbers, rounds down and falls back to 1 for some falsy inputs. Do not rely on that coercion. Free products are rejected.

POST /order · response fields
Field	Meaning
ok	true for a successful response.
orderId	Store order reference. Save for support.
product	Product display name.
quantity	Purchased quantity.
charged	Total deducted in USDT.
newBalance	Wallet balance returned after purchase.
items	Array of delivery strings. A string may contain several credentials and instructions; do not infer account count by splitting lines.
Working examples
Use these in your server environment. Examples contain placeholders only. The order examples make a real purchase when called with a valid funded key and product.

cURL
export ELITE_API_KEY='YOUR_API_KEY'
curl --fail-with-body 'https://api.elitetoolz.store/api/balance' \
  -H "X-API-Key: $ELITE_API_KEY"

curl --fail-with-body 'https://api.elitetoolz.store/api/products' \
  -H "X-API-Key: $ELITE_API_KEY"

# Real purchase: run once only when ready.
curl --fail-with-body -X POST 'https://api.elitetoolz.store/api/order' \
  -H "X-API-Key: $ELITE_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"productId":"PRODUCT_ID","quantity":1}'
Node.js · server-side client
const base = "https://api.elitetoolz.store/api";
const apiKey = process.env.ELITE_API_KEY;
if (!apiKey) throw new Error("ELITE_API_KEY is required");

async function request(path, body) {
  const response = await fetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-API-Key": apiKey,
      "Content-Type": "application/json"
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(30000)
  });
  const result = await response.json();
  if (!response.ok || result.ok !== true) {
    throw new Error("HTTP " + response.status + ": " +
      (result.error || "Unexpected API response"));
  }
  return result;
}

const wallet = await request("/balance");
// Store results securely; do not log keys or credentials.

// A call to this function purchases the product. No automatic retries.
async function placeOrder(productId, quantity) {
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 100)
    throw new Error("Quantity must be an integer from 1 to 100");
  return request("/order", { productId, quantity });
}
Python · requests client
import os
import requests

BASE = "https://api.elitetoolz.store/api"
KEY = os.environ["ELITE_API_KEY"]
HEADERS = {"X-API-Key": KEY}

def read_api(path):
    response = requests.get(BASE + path, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("ok") is not True:
        raise RuntimeError("Unexpected API response")
    return data

wallet = read_api("/balance")

# Real purchase when called. Do not add automatic POST retries.
def place_order(product_id, quantity):
    if type(quantity) is not int or not 1 <= quantity <= 100:
        raise ValueError("Quantity must be an integer from 1 to 100")
    response = requests.post(
        BASE + "/order", headers=HEADERS,
        json={"productId": product_id, "quantity": quantity}, timeout=30
    )
    response.raise_for_status()
    data = response.json()
    if data.get("ok") is not True:
        raise RuntimeError("Unexpected API response")
    return data
A client timeout does not cancel or prove failure of a server-side purchase. A network error or malformed response must be reconciled before placing another order.

Credential delivery
Store the original items array exactly as received. Render delivery strings as plain text, not HTML. Escape Telegram formatting or send a text file when a message is too long. Preserve passwords, 2FA values, URLs and instructions without truncation.

function displayDelivery(item) {
  const marker = "__CREDENTIAL__:";
  return item.startsWith(marker) ? item.slice(marker.length) : item;
}
Do not blindly parse by colons or newlines: a credential itself can contain those characters. Do not put deliveries in analytics, logs or public URLs.

Troubleshooting and recovery
Problem	Next step
401	Check X-API-Key, whitespace, and whether the key was regenerated or revoked.
402	Check the same bot account wallet balance and current product cost.
404	Refresh the catalog and verify productId. The product may be disabled.
409	Refresh stock and verify quantity. Stock may change between listing and checkout.
503, timeout or lost response	Stop automatic retries. Check order history in the bot and contact support with timestamp, product and quantity. A supplier outcome may be uncertain.
Delivery was returned but your customer did not receive it	Resend your securely stored delivery. Do not place another purchase.
Current API limits and supported features
Three authenticated operations: products, balance, order.
No client idempotency guarantee; external_order_id and Idempotency-Key are not supported by this reseller route.
No reseller order-history, refund, cancellation or webhook subscription endpoint is currently exposed by these routes.
No guaranteed requests-per-minute quota is defined by these routes. Cache catalog results and avoid rapid repeated polling.
Do not assume parallel purchases on the same wallet are protected by a transaction lock. Serialize purchases in your integration.
No sandbox purchase endpoint is documented. Example purchases use real funds.
Telegram paid-order recovery and supplier APIs are separate flows; their capabilities do not imply support for those features in this API.

Support checklist
Share your orderId if available, UTC timestamp, productId, quantity, HTTP status and redacted error text. Never send your API key or customer credentials. If a key was exposed, regenerate it in the bot and update your server environment.

Need help? Open Support in the Telegram bot and provide your order ID, never your API key.