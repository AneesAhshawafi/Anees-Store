import logging
import uuid
import requests
import database

logger = logging.getLogger(__name__)


class ShopApiClient:
    """Client for the external Shop API using credentials stored in MongoDB."""

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def _get_base_and_headers(self, idempotency_key: str = None) -> tuple:
        """Retrieves base URL and headers from MongoDB credentials."""
        base_url, api_key = database.get_api_credentials()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return base_url, headers

    def get_products(self) -> list:
        """Fetch all available products from GET /products."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/products"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                products = data.get("products", [])
                # Cache products in DB
                database.cache_products(products)
                return products
            logger.error(f"Failed to fetch products: HTTP {resp.status_code} - {resp.text}")
            return []
        except Exception as e:
            logger.error(f"Error connecting to /products: {e}")
            # Fallback to cached products in DB if network fails
            return database.get_cached_products()

    def get_product(self, product_id: str) -> dict:
        """Fetch details of a single product from GET /product/{id}."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/product/{product_id}"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("product") or data
            logger.error(f"Failed to fetch product {product_id}: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"Error fetching product {product_id}: {e}")
        # Fallback to cache
        return database.get_cached_product(product_id)

    def get_balance(self) -> dict:
        """Fetch store API balance from GET /balance."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/balance"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return {"success": False, "error": str(e)}

    def get_orders(self) -> list:
        """Fetch last 20 orders from GET /orders."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/orders"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("orders", [])
            return []
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []

    def get_order(self, order_id: str) -> dict:
        """Fetch specific order details from GET /order/{order_id}."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/order/{order_id}"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            logger.error(f"Error getting order {order_id}: {e}")
            return {"success": False, "error": str(e)}

    def purchase(self, product_id: str, qty: int = 1, buyer_info: str = None, email: str = None, idempotency_key: str = None) -> dict:
        """
        Sends purchase request to POST /purchase.
        Automatically handles ChatGPT email or regular quantity purchases.
        """
        if not idempotency_key:
            idempotency_key = str(uuid.uuid4())

        base_url, headers = self._get_base_and_headers(idempotency_key=idempotency_key)
        url = f"{base_url}/purchase"

        payload = {
            "product_id": str(product_id)
        }

        if email:
            payload["email"] = email
        else:
            payload["qty"] = int(qty)
            if buyer_info:
                payload["buyer_info"] = str(buyer_info)

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            try:
                data = resp.json()
            except Exception:
                data = {"success": False, "error": resp.text or f"HTTP {resp.status_code}"}

            if resp.status_code in (200, 202):
                data["http_status"] = resp.status_code
                return data

            # Unsuccessful HTTP response
            error_msg = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
            return {"success": False, "error": error_msg, "http_status": resp.status_code}

        except requests.exceptions.Timeout:
            logger.error("Purchase request timed out.")
            return {"success": False, "error": "Request timed out"}
        except Exception as e:
            logger.error(f"Purchase request error: {e}")
            return {"success": False, "error": str(e)}

    def get_changes(self, since: int = 0, limit: int = 100, event_type: str = None) -> dict:
        """Fetch changes from GET /changes."""
        base_url, headers = self._get_base_and_headers()
        url = f"{base_url}/changes"
        params = {
            "since": int(since),
            "limit": int(limit)
        }
        if event_type:
            params["event_type"] = event_type

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            return {"success": False, "error": f"HTTP {resp.status_code}", "events": []}
        except Exception as e:
            logger.error(f"Error fetching changes: {e}")
            return {"success": False, "error": str(e), "events": []}


# Singleton instance
api_client = ShopApiClient()
