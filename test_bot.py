import os
import sys
import unittest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
import database
from api_client import api_client
import sync_worker
import bot


class TestAneesStoreBot(unittest.TestCase):

    def setUp(self):
        self.db = database.get_db()

    def test_01_mongodb_credentials_stored_and_decoded(self):
        """Verify API credentials are read from MongoDB and never hardcoded in source code."""
        code = database.get_connection_code()
        self.assertTrue(code.startswith("conn_"), "Connection code must start with conn_")
        
        api_url, api_key = database.get_api_credentials()
        self.assertTrue(api_url.startswith("https://"), "API URL must be valid HTTPS")
        self.assertTrue(api_key.startswith("sk_"), "API Key must start with sk_")
        print("\n[OK] test_01: Credentials decoded from DB successfully:")
        print(f"      URL: {api_url}")
        print(f"      Key Prefix: {api_key[:8]}...")

    def test_02_fetch_products_and_check_manual_flag(self):
        """Verify products can be fetched, cached, and is_manual flag is properly parsed."""
        products = api_client.get_products()
        self.assertGreater(len(products), 0, "Products list should not be empty")

        sample = products[0]
        self.assertIn("is_manual", sample, "Product must have is_manual field")
        self.assertIn("name_ar", sample, "Product must have name_ar field")
        
        # Verify cached in MongoDB
        cached = database.get_cached_products()
        self.assertGreater(len(cached), 0, "Products should be cached in MongoDB")
        print(f"\n[OK] test_02: Fetched {len(products)} products and cached in MongoDB.")

    def test_03_main_start_keyboard_has_exactly_two_buttons(self):
        """Verify /start keyboard contains EXACTLY the 2 requested buttons: 🛒 المنتجات and 💰 رصيدي."""
        kb = bot.get_main_keyboard()
        buttons = []
        for row in kb.keyboard:
            for btn in row:
                t = btn.get("text") if isinstance(btn, dict) else getattr(btn, "text", str(btn))
                buttons.append(t)
        
        self.assertEqual(len(buttons), 2, "Main menu must have exactly 2 buttons")
        self.assertIn("🛒 المنتجات", buttons)
        self.assertIn("💰 رصيدي", buttons)
        print("\n[OK] test_03: Main keyboard has exactly 2 buttons:", buttons)

    def test_04_product_catalog_keyboard_icons(self):
        """Verify product catalog buttons contain proper delivery icons (⚡ or 🤝)."""
        products = database.get_cached_products()
        markup = bot.build_products_keyboard(products, page=0)
        
        found_instant = False
        found_manual = False
        for row in markup.keyboard:
            for btn in row:
                if "⚡" in btn.text:
                    found_instant = True
                if "🤝" in btn.text:
                    found_manual = True

        self.assertTrue(found_instant or found_manual, "Buttons must have delivery icons")
        print(f"\n[OK] test_04: Catalog buttons generated properly with delivery icons (Instant: {found_instant}, Manual: {found_manual}).")

    def test_05_database_balances_and_orders(self):
        """Verify user balance tracking and order saving in MongoDB."""
        test_user_id = 999888777
        # Set balance
        database.set_user_balance(test_user_id, 50.0)
        bal = database.get_user_balance(test_user_id)
        self.assertEqual(bal, 50.0)

        # Add balance
        new_bal = database.add_user_balance(test_user_id, 25.5)
        self.assertEqual(new_bal, 75.5)

        # Save order
        order_data = {
            "order_id": "TEST_ORD_123",
            "user_id": test_user_id,
            "buyer_info": "@test_user",
            "product_id": "1",
            "product_name": "Netflix Test",
            "price": 3.5,
            "status": "completed",
            "is_manual": False,
            "codes": ["TEST-CODE-XXXX-YYYY"]
        }
        database.save_order(order_data)
        saved = database.get_order("TEST_ORD_123")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["product_name"], "Netflix Test")
        self.assertEqual(saved["codes"], ["TEST-CODE-XXXX-YYYY"])
        print("\n[OK] test_05: Balances and Orders stored and retrieved from MongoDB successfully.")

    def test_06_sync_changes_cursor_tracking(self):
        """Verify admin /sync works and updates cursor."""
        res = sync_worker.sync_changes()
        self.assertTrue(res["success"], "Sync should succeed")
        cursor = database.get_sync_cursor()
        self.assertGreater(cursor, 0, "Cursor should be updated")
        print(f"\n[OK] test_06: Sync changes processed {res['events_count']} events. Cursor: {cursor}")


if __name__ == "__main__":
    unittest.main()
