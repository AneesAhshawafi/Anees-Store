import logging
import requests
import database
from config import ELITE_API_BASE_URL

logger = logging.getLogger(__name__)


class EliteApiClient:
    """Client for Store 2 (Elite Tools Store Reseller API)."""

    def __init__(self, timeout: int = 25):
        self.base_url = ELITE_API_BASE_URL.rstrip("/")
        self.timeout = timeout

    def _get_headers(self) -> dict:
        key = database.get_elite_api_key()
        return {
            "X-API-Key": key,
            "Content-Type": "application/json"
        }

    def get_products(self) -> list:
        """Fetch all catalog products from GET /products."""
        url = f"{self.base_url}/products"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    products = data.get("products", [])
                    # Cache in DB and in-memory
                    database.cache_elite_products(products)
                    return products
            logger.error(f"Failed to fetch Elite products: HTTP {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Error fetching Elite products: {e}")

        # Fallback to cache
        return database.get_cached_elite_products()

    def get_product(self, product_id: str) -> dict:
        """Fetch specific Elite product from cache or list."""
        cached = database.get_cached_elite_product(product_id)
        if cached:
            return cached
        # If not in cache, refresh
        products = self.get_products()
        for p in products:
            if str(p.get("id")) == str(product_id):
                return p
        return None

    def get_balance(self) -> dict:
        """Fetch Elite wallet balance from GET /balance."""
        url = f"{self.base_url}/balance"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error(f"Error getting Elite balance: {e}")
            return {"ok": False, "error": str(e)}

    def purchase(self, product_id: str, quantity: int = 1) -> dict:
        """
        Places a real order via POST /order.
        Body: {"productId": product_id, "quantity": quantity}
        """
        url = f"{self.base_url}/order"
        payload = {
            "productId": str(product_id),
            "quantity": int(quantity)
        }
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=self.timeout)
            try:
                data = resp.json()
            except Exception:
                data = {"ok": False, "error": resp.text or f"HTTP {resp.status_code}"}

            if resp.status_code == 200 and data.get("ok"):
                # Clean __CREDENTIAL__: marker from items as specified in api docs
                raw_items = data.get("items", [])
                cleaned_items = []
                for item in raw_items:
                    marker = "__CREDENTIAL__:"
                    if isinstance(item, str) and item.startswith(marker):
                        cleaned_items.append(item[len(marker):])
                    else:
                        cleaned_items.append(str(item))
                data["items"] = cleaned_items
                return data

            error_msg = data.get("error") or f"HTTP {resp.status_code}"
            return {"ok": False, "error": error_msg, "http_status": resp.status_code}

        except requests.exceptions.Timeout:
            logger.error("Elite purchase request timed out.")
            return {"ok": False, "error": "Request timed out"}
        except Exception as e:
            logger.error(f"Elite purchase error: {e}")
            return {"ok": False, "error": str(e)}


elite_client = EliteApiClient()
