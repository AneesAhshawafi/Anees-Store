import logging
import time
import threading
import database
from api_client import api_client

logger = logging.getLogger(__name__)


def sync_changes(notify_callback=None) -> dict:
    """
    Polls /changes endpoint, updates MongoDB local stock and orders.
    Optionally notifies bot users if their manual orders are completed.
    """
    cursor = database.get_sync_cursor()
    resp = api_client.get_changes(since=cursor, limit=100)

    if not resp.get("success"):
        logger.warning(f"Sync failed or returned error: {resp.get('error')}")
        return {"success": False, "error": resp.get("error"), "events_count": 0}

    events = resp.get("events", [])
    new_cursor = resp.get("cursor", cursor)

    updated_stocks = 0
    completed_orders = []

    for event in events:
        event_type = event.get("event_type", "")
        product_id = str(event.get("product_id") or "")
        data = event.get("data", {})

        # 1. Stock changes
        if event_type in ("stock.sold", "stock.added", "stock.removed", "stock.updated"):
            remaining = data.get("remaining_stock")
            if product_id and remaining is not None:
                database.update_cached_stock(product_id, remaining)
                updated_stocks += 1

        # 2. Product lifecycle
        elif event_type in ("product.created", "product.updated"):
            if product_id:
                try:
                    full_prod = api_client.get_product(product_id)
                    if full_prod:
                        database.cache_products([full_prod])
                except Exception as e:
                    logger.error(f"Error caching product {product_id}: {e}")

        elif event_type == "product.deleted":
            if product_id:
                database.remove_cached_product(product_id)

        # 3. Manual order completed by supplier
        elif event_type == "order.completed":
            order_id = str(data.get("order_id") or event.get("order_id") or "")
            if order_id:
                local_order = database.get_order(order_id)
                if local_order and local_order.get("status") != "completed":
                    try:
                        order_api = api_client.get_order(order_id)
                        if order_api and order_api.get("status") == "completed":
                            codes = order_api.get("codes", [])
                            database.update_order_status(order_id, "completed", codes=codes)
                            completed_orders.append({
                                "order_id": order_id,
                                "user_id": local_order.get("user_id"),
                                "product_name": local_order.get("product_name"),
                                "codes": codes
                            })
                    except Exception as e:
                        logger.error(f"Error updating completed order {order_id}: {e}")

    # Update cursor in MongoDB
    if new_cursor and new_cursor != cursor:
        database.set_sync_cursor(new_cursor)

    # Deliver manual orders to users if callback is given
    if notify_callback and completed_orders:
        for order_info in completed_orders:
            try:
                notify_callback(order_info)
            except Exception as e:
                logger.error(f"Error in notify_callback for order {order_info.get('order_id')}: {e}")

    return {
        "success": True,
        "events_count": len(events),
        "updated_stocks": updated_stocks,
        "completed_orders": len(completed_orders),
        "cursor": new_cursor
    }


def full_sync_products() -> int:
    """Refetches all products from GET /products and updates MongoDB cache."""
    products = api_client.get_products()
    if products:
        database.cache_products(products)
        return len(products)
    return 0


class BackgroundSyncThread(threading.Thread):
    """Background polling worker for real-time stock sync."""

    def __init__(self, interval_seconds: int = 30, notify_callback=None):
        super().__init__(daemon=True)
        self.interval = max(10, interval_seconds)
        self.notify_callback = notify_callback
        self.running = True

    def run(self):
        logger.info(f"Background sync worker started (Interval: {self.interval}s)")
        # Full sync on boot
        try:
            full_sync_products()
        except Exception as e:
            logger.error(f"Initial products sync error: {e}")

        while self.running:
            try:
                time.sleep(self.interval)
                sync_changes(notify_callback=self.notify_callback)
            except Exception as e:
                logger.error(f"Background sync error: {e}")

    def stop(self):
        self.running = False
