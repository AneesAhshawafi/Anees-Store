import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Admin Telegram ID (can be multiple separated by comma)
ADMIN_IDS = [
    int(x.strip()) 
    for x in os.getenv("ADMIN_ID", "0").split(",") 
    if x.strip().isdigit() and int(x.strip()) > 0
]

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "anees_store")

# Initial Connection Code (Used ONLY to seed MongoDB on first run if DB is empty)
# The code will always be read from and saved to MongoDB settings!
INITIAL_CONNECTION_CODE = os.getenv(
    "INITIAL_CONNECTION_CODE",
    "conn_eyJrIjoic2tfOTEwMTA1Y2I4ZDVkNWFjOTVjNzA4Njg4Y2I2NjhmNmEzMjc2ODVhMDFlYmNiMjA3IiwidSI6Imh0dHBzOi8vaW5zMjExMjEzMS1md2YxLm9ucmVuZGVyLmNvbS84ZjcxYWVkZDM0OTRlMDQyYmIwNjQwOGY1MGI3ZjkzOCJ9"
)

# User balance enforcement toggle:
# If True, user must have enough balance in bot's MongoDB to purchase.
# If False, bot sends purchase request directly to API (relying on API balance).
REQUIRE_USER_BALANCE = os.getenv("REQUIRE_USER_BALANCE", "True").lower() in ("true", "1", "yes")

# Auto-polling sync interval in seconds (0 to disable)
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# Items per page in products catalog
PRODUCTS_PER_PAGE = int(os.getenv("PRODUCTS_PER_PAGE", "8"))

# Store 2 (Elite Tools Store) Config
INITIAL_ELITE_API_KEY = os.getenv("INITIAL_ELITE_API_KEY", "ETS-496d5002defc54145cbba6cd32569782259a85cef53c0396")
ELITE_API_BASE_URL = os.getenv("ELITE_API_BASE_URL", "https://api.elitetoolz.store/api")

# Payment Providers / Binance Pay Config
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "")
BINANCE_NICKNAME = os.getenv("BINANCE_NICKNAME", "Anees Store")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
MIN_DEPOSIT_USDT = float(os.getenv("MIN_DEPOSIT_USDT", "0.10"))

