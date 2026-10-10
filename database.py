import base64
import json
import logging
from datetime import datetime, timezone
import pymongo
from config import MONGO_URI, DB_NAME, INITIAL_CONNECTION_CODE

logger = logging.getLogger(__name__)

# Global mongo client and database references
_client = None
_db = None
_is_mock = False


def get_db():
    """Initializes and returns the MongoDB database instance."""
    global _client, _db, _is_mock
    if _db is not None:
        return _db

    try:
        # Try real MongoDB connection
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2500)
        client.server_info()  # Forces a call to verify connection
        _client = client
        _db = _client[DB_NAME]
        _is_mock = False
        logger.info(f"Connected successfully to MongoDB at {MONGO_URI} (Database: {DB_NAME})")
    except Exception as e:
        logger.warning(
            f"Could not connect to MongoDB at {MONGO_URI} ({e}). "
            "Falling back to in-memory mongomock for testing/standalone mode."
        )
        try:
            import mongomock
            _client = mongomock.MongoClient()
            _db = _client[DB_NAME]
            _is_mock = True
            logger.info("Using mongomock in-memory database.")
        except Exception as mock_err:
            logger.error(f"Failed to initialize mongomock fallback: {mock_err}")
            raise e

    # Seed initial connection code if missing
    _seed_connection_code()
    return _db


def is_mock_db():
    return _is_mock


def _seed_connection_code():
    """Seeds INITIAL_CONNECTION_CODE if no connection_code is found in MongoDB."""
    if not INITIAL_CONNECTION_CODE:
        return
    existing = _db.settings.find_one({"key": "connection_code"})
    if not existing:
        _db.settings.update_one(
            {"key": "connection_code"},
            {"$set": {"key": "connection_code", "value": INITIAL_CONNECTION_CODE, "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        logger.info("Initial connection_code successfully saved to MongoDB settings.")


# ==========================================
# Connection Code & API Credentials Management
# (Always read from MongoDB — Never hardcoded)
# ==========================================

def get_connection_code() -> str:
    """Reads connection_code from MongoDB."""
    db = get_db()
    doc = db.settings.find_one({"key": "connection_code"})
    if doc and doc.get("value"):
        return doc["value"].strip()
    return ""


def set_connection_code(new_code: str) -> bool:
    """Updates connection_code in MongoDB."""
    if not new_code or not new_code.strip():
        return False
    clean_code = new_code.strip()
    # Validate base64 format before saving
    try:
        raw_b64 = clean_code.replace("conn_", "")
        data = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
        if "k" not in data or "u" not in data:
            return False
    except Exception as e:
        logger.error(f"Invalid connection code format: {e}")
        return False

    db = get_db()
    db.settings.update_one(
        {"key": "connection_code"},
        {"$set": {
            "key": "connection_code",
            "value": clean_code,
            "updated_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )
    logger.info("Connection code updated in MongoDB.")
    return True


def get_api_credentials():
    """
    Decodes the connection_code stored in MongoDB and returns (api_url, api_key).
    Raises ValueError if connection_code is not configured or invalid.
    """
    code = get_connection_code()
    if not code:
        raise ValueError("Connection code is not configured in MongoDB settings!")

    raw_b64 = code.replace("conn_", "")
    data = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
    api_key = data.get("k", "")
    api_url = data.get("u", "").rstrip("/")
    if not api_key or not api_url:
        raise ValueError("Decoded connection code is missing API key ('k') or URL ('u').")
    return api_url, api_key


# ==========================================
# User & Balances Management
# ==========================================

def get_user_balance(user_id: int) -> float:
    """Gets current user balance from MongoDB."""
    db = get_db()
    user = db.balances.find_one({"user_id": int(user_id)})
    if user:
        return float(user.get("balance", 0.0))
    return 0.0


def set_user_balance(user_id: int, new_balance: float) -> float:
    """Sets user balance in MongoDB."""
    db = get_db()
    db.balances.update_one(
        {"user_id": int(user_id)},
        {"$set": {
            "balance": round(float(new_balance), 2),
            "updated_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )
    return round(float(new_balance), 2)


def add_user_balance(user_id: int, amount: float) -> float:
    """Adds (or deducts if negative) amount to user's balance."""
    db = get_db()
    current = get_user_balance(user_id)
    new_bal = round(current + float(amount), 2)
    db.balances.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {
                "balance": new_bal,
                "updated_at": datetime.now(timezone.utc)
            },
            "$setOnInsert": {
                "created_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )
    return new_bal


def upsert_user(user_id: int, username: str = None, first_name: str = None):
    """Saves or updates user metadata."""
    db = get_db()
    update_data = {
        "updated_at": datetime.now(timezone.utc)
    }
    if username:
        update_data["username"] = username
    if first_name:
        update_data["first_name"] = first_name

    db.balances.update_one(
        {"user_id": int(user_id)},
        {
            "$set": update_data,
            "$setOnInsert": {
                "balance": 0.0,
                "created_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )


# ==========================================
# Orders Management
# ==========================================

def save_order(order_data: dict) -> str:
    """Saves an order to MongoDB orders collection."""
    db = get_db()
    if "created_at" not in order_data:
        order_data["created_at"] = datetime.now(timezone.utc)
    order_id = str(order_data.get("order_id", ""))
    
    if order_id:
        db.orders.update_one(
            {"order_id": order_id},
            {"$set": order_data},
            upsert=True
        )
    else:
        result = db.orders.insert_one(order_data)
        order_id = str(result.inserted_id)
    return order_id


def get_order(order_id: str) -> dict:
    """Retrieves an order by order_id."""
    db = get_db()
    return db.orders.find_one({"order_id": str(order_id)})


def update_order_status(order_id: str, status: str, codes: list = None) -> bool:
    """Updates order status and codes."""
    db = get_db()
    update_doc = {
        "status": status,
        "updated_at": datetime.now(timezone.utc)
    }
    if codes is not None:
        update_doc["codes"] = codes
    res = db.orders.update_one(
        {"order_id": str(order_id)},
        {"$set": update_doc}
    )
    return res.modified_count > 0 or res.matched_count > 0


def get_user_orders(user_id: int, limit: int = 10) -> list:
    """Gets the last orders of a user."""
    db = get_db()
    cursor = db.orders.find({"user_id": int(user_id)}).sort("created_at", pymongo.DESCENDING).limit(limit)
    return list(cursor)


def get_all_orders(limit: int = 20) -> list:
    """Gets recent orders for admin."""
    db = get_db()
    cursor = db.orders.find().sort("created_at", pymongo.DESCENDING).limit(limit)
    return list(cursor)


# ==========================================
# Polling Cursor Management
# ==========================================

def get_sync_cursor() -> int:
    """Gets the stored cursor for /changes endpoint."""
    db = get_db()
    doc = db.settings.find_one({"key": "changes_cursor"})
    if doc and "value" in doc:
        return int(doc["value"])
    return 0


def set_sync_cursor(cursor: int):
    """Saves the last processed cursor."""
    db = get_db()
    db.settings.update_one(
        {"key": "changes_cursor"},
        {"$set": {
            "key": "changes_cursor",
            "value": int(cursor),
            "updated_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )


# ==========================================
# Products Cache Management
# ==========================================

def cache_products(products: list):
    """Saves or updates products in the local MongoDB products collection."""
    db = get_db()
    for prod in products:
        p_id = str(prod.get("id") or prod.get("_id", ""))
        if not p_id:
            continue
        prod["id"] = p_id
        prod["cached_at"] = datetime.now(timezone.utc)
        db.products.update_one(
            {"id": p_id},
            {"$set": prod},
            upsert=True
        )


def get_cached_products() -> list:
    """Returns all cached products."""
    db = get_db()
    return list(db.products.find())


def get_cached_product(product_id: str) -> dict:
    """Returns a specific product from cache."""
    db = get_db()
    return db.products.find_one({"id": str(product_id)})


def update_cached_stock(product_id: str, new_stock):
    """Updates product remaining stock in cache."""
    db = get_db()
    db.products.update_one(
        {"id": str(product_id)},
        {"$set": {
            "stock": new_stock,
            "stock_updated_at": datetime.now(timezone.utc)
        }}
    )


def remove_cached_product(product_id: str):
    """Removes a deleted product from cache."""
    db = get_db()
    db.products.delete_one({"id": str(product_id)})
