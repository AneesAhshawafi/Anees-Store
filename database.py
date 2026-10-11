import base64
import json
import logging
import uuid
from datetime import datetime, timezone
import pymongo
from config import MONGO_URI, DB_NAME, INITIAL_CONNECTION_CODE, INITIAL_ELITE_API_KEY

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

    # Ensure indexes for deposits and transactions
    try:
        _db.deposits.create_index([("provider", 1), ("reference_id", 1)], unique=True)
        _db.wallet_transactions.create_index([("user_id", 1), ("created_at", -1)])
    except Exception as idx_err:
        logger.warning(f"Note on index creation: {idx_err}")

    # Seed initial connection code and elite key if missing
    _seed_connection_code()
    _seed_elite_key()
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


def _seed_elite_key():
    """Seeds INITIAL_ELITE_API_KEY if no elite_api_key is found in MongoDB."""
    if not INITIAL_ELITE_API_KEY:
        return
    existing = _db.settings.find_one({"key": "elite_api_key"})
    if not existing:
        _db.settings.update_one(
            {"key": "elite_api_key"},
            {"$set": {"key": "elite_api_key", "value": INITIAL_ELITE_API_KEY, "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        logger.info("Initial elite_api_key successfully saved to MongoDB settings.")


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
# Wallet & Transactions Management
# ==========================================

def record_wallet_transaction(
    user_id: int,
    tx_type: str,
    amount: float,
    balance_before: float,
    balance_after: float,
    payment_method: str = "balance",
    reference_id: str = None,
    details: str = None
) -> str:
    """Records an audit log entry for any wallet balance change."""
    db = get_db()
    now = datetime.now(timezone.utc)
    tx_id = f"TX_{int(now.timestamp())}_{uuid.uuid4().hex[:6]}"
    doc = {
        "tx_id": tx_id,
        "user_id": int(user_id),
        "type": tx_type,  # deposit, purchase_store1, purchase_store2, refund, admin_adjustment
        "amount": round(float(amount), 2),
        "balance_before": round(float(balance_before), 2),
        "balance_after": round(float(balance_after), 2),
        "payment_method": payment_method,
        "reference_id": str(reference_id) if reference_id else None,
        "details": details or "",
        "created_at": now
    }
    db.wallet_transactions.insert_one(doc)
    return tx_id


def get_user_wallet_history(user_id: int, limit: int = 10) -> list:
    """Returns the most recent wallet transactions for a given user."""
    db = get_db()
    return list(db.wallet_transactions.find({"user_id": int(user_id)}).sort("created_at", -1).limit(limit))


def is_deposit_reference_claimed(provider: str, reference_id: str) -> bool:
    """Checks if a deposit reference/order ID has already been credited."""
    db = get_db()
    ref = str(reference_id).strip()
    return db.deposits.find_one({"provider": str(provider).lower(), "reference_id": ref}) is not None


def claim_deposit_reference(
    provider: str,
    reference_id: str,
    user_id: int,
    amount: float,
    currency: str = "USDT",
    details: dict = None
) -> bool:
    """
    Atomically claims a deposit reference in the deposits collection.
    Guaranteed unique via database index to prevent duplicate credits.
    """
    db = get_db()
    prov = str(provider).lower()
    ref = str(reference_id).strip()

    # In mock db or as safety check, reject if already claimed
    if is_mock_db() and is_deposit_reference_claimed(prov, ref):
        return False

    now = datetime.now(timezone.utc)
    doc = {
        "provider": prov,
        "reference_id": ref,
        "user_id": int(user_id),
        "amount": round(float(amount), 2),
        "currency": currency,
        "claimed_at": now,
        "details": details or {}
    }
    try:
        db.deposits.insert_one(doc)
        return True
    except (pymongo.errors.DuplicateKeyError, Exception) as e:
        if isinstance(e, pymongo.errors.DuplicateKeyError) or "duplicate" in str(e).lower():
            return False
        logger.error(f"Error claiming deposit reference {reference_id}: {e}")
        return False


def create_pending_deposit(
    user_id: int,
    provider: str,
    reference_id: str,
    amount: float = None,
    user_note: str = None
) -> str:
    """Creates a pending deposit review item for admin manual review."""
    db = get_db()
    now = datetime.now(timezone.utc)
    dep_id = f"DEP_{int(now.timestamp())}_{uuid.uuid4().hex[:4]}"
    doc = {
        "deposit_id": dep_id,
        "user_id": int(user_id),
        "provider": str(provider).lower(),
        "reference_id": str(reference_id).strip(),
        "amount": round(float(amount), 2) if amount else None,
        "user_note": user_note,
        "status": "pending",  # pending, approved, rejected
        "created_at": now,
        "resolved_at": None,
        "resolved_by": None
    }
    db.pending_deposits.insert_one(doc)
    return dep_id


def get_pending_deposit(deposit_id: str) -> dict:
    """Retrieves a pending deposit item by its deposit_id."""
    db = get_db()
    return db.pending_deposits.find_one({"deposit_id": str(deposit_id)})


def resolve_pending_deposit(deposit_id: str, status: str, resolved_by: int = None, final_amount: float = None) -> bool:
    """Marks a pending deposit as approved or rejected."""
    db = get_db()
    update_data = {
        "status": status,
        "resolved_at": datetime.now(timezone.utc),
        "resolved_by": resolved_by
    }
    if final_amount is not None:
        update_data["amount"] = round(float(final_amount), 2)

    res = db.pending_deposits.update_one(
        {"deposit_id": str(deposit_id), "status": "pending"},
        {"$set": update_data}
    )
    return res.modified_count > 0


def get_user_total_deposited(user_id: int) -> float:
    """Calculates total lifetime deposits for a user."""
    db = get_db()
    pipeline = [
        {"$match": {"user_id": int(user_id), "type": "deposit"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    res = list(db.wallet_transactions.aggregate(pipeline))
    if res and len(res) > 0:
        return round(float(res[0].get("total", 0.0)), 2)
    return 0.0


def get_user_total_spent(user_id: int) -> float:
    """Calculates total lifetime spend from wallet for a user."""
    db = get_db()
    pipeline = [
        {"$match": {"user_id": int(user_id), "type": {"$in": ["purchase_store1", "purchase_store2"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    res = list(db.wallet_transactions.aggregate(pipeline))
    if res and len(res) > 0:
        return round(abs(float(res[0].get("total", 0.0))), 2)
    return 0.0


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
# Products Cache Management (High-Performance In-Memory & Bulk MongoDB)
# ==========================================

_in_memory_products = []
_in_memory_products_dict = {}


def cache_products(products: list):
    """Saves or updates products in in-memory cache and bulk writes to MongoDB."""
    global _in_memory_products, _in_memory_products_dict
    if not products:
        return

    db = get_db()
    now = datetime.now(timezone.utc)
    operations = []

    clean_list = []
    clean_dict = {}

    for prod in products:
        p_id = str(prod.get("id") or prod.get("_id", ""))
        if not p_id:
            continue
        prod_copy = dict(prod)
        prod_copy["id"] = p_id
        prod_copy["cached_at"] = now
        clean_list.append(prod_copy)
        clean_dict[p_id] = prod_copy

        operations.append(
            pymongo.UpdateOne(
                {"id": p_id},
                {"$set": prod_copy},
                upsert=True
            )
        )

    # Update in-memory cache instantly (0ms)
    _in_memory_products = clean_list
    _in_memory_products_dict = clean_dict

    # Bulk write to MongoDB in 1 single network call
    if operations:
        try:
            db.products.bulk_write(operations, ordered=False)
        except Exception as e:
            if is_mock_db():
                for op in operations:
                    db.products.update_one(op._filter, op._doc, upsert=True)
            else:
                logger.error(f"Error bulk updating products: {e}")


def get_cached_products() -> list:
    """Returns products from in-memory cache instantly (fallback to MongoDB)."""
    global _in_memory_products, _in_memory_products_dict
    if _in_memory_products:
        return _in_memory_products

    try:
        db = get_db()
        items = list(db.products.find())
        if items:
            _in_memory_products = items
            _in_memory_products_dict = {str(p.get("id") or p.get("_id")): p for p in items}
            return _in_memory_products
    except Exception as e:
        logger.error(f"Error reading cached products from MongoDB: {e}")

    return []


def get_cached_product(product_id: str) -> dict:
    """Returns a specific product instantly from memory or DB."""
    global _in_memory_products_dict
    p_id = str(product_id)
    if p_id in _in_memory_products_dict:
        return _in_memory_products_dict[p_id]

    try:
        db = get_db()
        item = db.products.find_one({"id": p_id})
        if item:
            _in_memory_products_dict[p_id] = item
            return item
    except Exception as e:
        logger.error(f"Error reading product {p_id} from MongoDB: {e}")

    return None


def update_cached_stock(product_id: str, new_stock):
    """Updates product remaining stock in memory and MongoDB."""
    global _in_memory_products_dict, _in_memory_products
    p_id = str(product_id)
    if p_id in _in_memory_products_dict:
        _in_memory_products_dict[p_id]["stock"] = new_stock

    for p in _in_memory_products:
        if str(p.get("id") or p.get("_id")) == p_id:
            p["stock"] = new_stock
            break

    try:
        db = get_db()
        db.products.update_one(
            {"id": p_id},
            {"$set": {
                "stock": new_stock,
                "stock_updated_at": datetime.now(timezone.utc)
            }}
        )
    except Exception as e:
        logger.error(f"Error updating stock for {p_id}: {e}")


def remove_cached_product(product_id: str):
    """Removes a deleted product from memory and MongoDB."""
    global _in_memory_products_dict, _in_memory_products
    p_id = str(product_id)
    _in_memory_products_dict.pop(p_id, None)
    _in_memory_products = [p for p in _in_memory_products if str(p.get("id") or p.get("_id")) != p_id]

    try:
        db = get_db()
        db.products.delete_one({"id": p_id})
    except Exception as e:
        logger.error(f"Error removing product {p_id}: {e}")


# ==========================================
# Elite Store (Store 2) Credentials & Cache
# ==========================================

def get_elite_api_key() -> str:
    """Reads elite_api_key from MongoDB settings."""
    db = get_db()
    doc = db.settings.find_one({"key": "elite_api_key"})
    if doc and doc.get("value"):
        return doc["value"].strip()
    return INITIAL_ELITE_API_KEY or ""


def set_elite_api_key(new_key: str) -> bool:
    """Updates elite_api_key in MongoDB settings."""
    if not new_key or not new_key.strip():
        return False
    db = get_db()
    db.settings.update_one(
        {"key": "elite_api_key"},
        {"$set": {
            "key": "elite_api_key",
            "value": new_key.strip(),
            "updated_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )
    logger.info("Elite API Key updated in MongoDB settings.")
    return True


_in_memory_elite_products = []
_in_memory_elite_products_dict = {}


def cache_elite_products(products: list):
    """Caches Store 2 (Elite) products in-memory and bulk writes to MongoDB."""
    global _in_memory_elite_products, _in_memory_elite_products_dict
    if not products:
        return

    db = get_db()
    now = datetime.now(timezone.utc)
    operations = []
    clean_list = []
    clean_dict = {}

    for prod in products:
        p_id = str(prod.get("id", ""))
        if not p_id:
            continue
        prod_copy = dict(prod)
        prod_copy["id"] = p_id
        prod_copy["store"] = "elite"
        prod_copy["cached_at"] = now
        clean_list.append(prod_copy)
        clean_dict[p_id] = prod_copy

        operations.append(
            pymongo.UpdateOne(
                {"id": p_id, "store": "elite"},
                {"$set": prod_copy},
                upsert=True
            )
        )

    _in_memory_elite_products = clean_list
    _in_memory_elite_products_dict = clean_dict

    if operations:
        try:
            db.elite_products.bulk_write(operations, ordered=False)
        except Exception as e:
            if is_mock_db():
                for op in operations:
                    db.elite_products.update_one(op._filter, op._doc, upsert=True)
            else:
                logger.error(f"Error bulk updating elite products: {e}")


def get_cached_elite_products() -> list:
    """Returns Store 2 (Elite) products from memory instantly (or MongoDB)."""
    global _in_memory_elite_products, _in_memory_elite_products_dict
    if _in_memory_elite_products:
        return _in_memory_elite_products

    try:
        db = get_db()
        items = list(db.elite_products.find())
        if items:
            _in_memory_elite_products = items
            _in_memory_elite_products_dict = {str(p.get("id")): p for p in items}
            return _in_memory_elite_products
    except Exception as e:
        logger.error(f"Error reading cached elite products: {e}")

    return []


def get_cached_elite_product(product_id: str) -> dict:
    """Returns a specific Elite product instantly."""
    global _in_memory_elite_products_dict
    p_id = str(product_id)
    if p_id in _in_memory_elite_products_dict:
        return _in_memory_elite_products_dict[p_id]

    try:
        db = get_db()
        item = db.elite_products.find_one({"id": p_id})
        if item:
            _in_memory_elite_products_dict[p_id] = item
            return item
    except Exception as e:
        logger.error(f"Error reading elite product {p_id}: {e}")

    return None
