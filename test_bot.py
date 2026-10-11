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
from elite_client import elite_client
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

    def test_03_main_start_keyboard_has_three_buttons(self):
        """Verify /start keyboard contains EXACTLY the 3 requested buttons: 🏪 المتجر 1, 💎 المتجر 2, and 💰 رصيدي."""
        kb = bot.get_main_keyboard()
        buttons = []
        for row in kb.keyboard:
            for btn in row:
                t = btn.get("text") if isinstance(btn, dict) else getattr(btn, "text", str(btn))
                buttons.append(t)
        
        self.assertEqual(len(buttons), 3, "Main menu must have exactly 3 buttons")
        self.assertIn("🏪 المتجر 1", buttons)
        self.assertIn("💎 المتجر 2", buttons)
        self.assertIn("💰 رصيدي", buttons)

        # Also verify Store 1 and Store 2 sub-keyboards
        s1_kb = bot.get_store1_keyboard()
        s1_btns = [getattr(b, "text", b.get("text") if isinstance(b, dict) else str(b)) for row in s1_kb.keyboard for b in row]
        self.assertIn("🛒 منتجات المتجر 1", s1_btns)
        self.assertIn("🔙 القائمة الرئيسية", s1_btns)

        s2_kb = bot.get_store2_keyboard()
        s2_btns = [getattr(b, "text", b.get("text") if isinstance(b, dict) else str(b)) for row in s2_kb.keyboard for b in row]
        self.assertIn("🛒 منتجات المتجر 2", s2_btns)
        self.assertIn("🔙 القائمة الرئيسية", s2_btns)
        print("\n[OK] test_03: Main keyboard has exactly 3 buttons:", buttons)

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

    def test_07_elite_store_client_and_cache(self):
        """Verify Store 2 (Elite Tools) API client balance and products catalog."""
        # 1. Balance
        bal_res = elite_client.get_balance()
        self.assertTrue(bal_res.get("ok"), f"Elite balance check failed: {bal_res}")
        self.assertIn("balance", bal_res)
        print(f"\n[OK] test_07: Elite Store 2 balance: ${bal_res.get('balance')}")

        # 2. Products
        prods = elite_client.get_products()
        self.assertGreater(len(prods), 0, "Elite store should have products")
        sample = prods[0]
        self.assertIn("id", sample)
        self.assertIn("name", sample)
        self.assertIn("price", sample)

        # 3. Cache
        cached = database.get_cached_elite_products()
        self.assertEqual(len(cached), len(prods))
        sample_cached = database.get_cached_elite_product(sample["id"])
        self.assertIsNotNone(sample_cached)
        self.assertEqual(sample_cached["name"], sample["name"])
        print(f"      Elite Store 2 products fetched and cached: {len(prods)} products.")

    def test_08_wallet_transactions_and_anti_replay_deposit(self):
        """Verify wallet transactions and duplicate deposit prevention."""
        test_uid = 555444333
        test_ref = f"REF_TEST_{database.datetime.now().timestamp()}"

        # 1. Claim reference first time -> should succeed
        c1 = database.claim_deposit_reference("binance_pay", test_ref, test_uid, 15.0, "USDT")
        self.assertTrue(c1, "First deposit claim must succeed")

        # 2. Claim identical reference second time -> must FAIL (anti-replay)
        c2 = database.claim_deposit_reference("binance_pay", test_ref, test_uid, 15.0, "USDT")
        self.assertFalse(c2, "Duplicate deposit claim must fail")

        # 3. Verify is_deposit_reference_claimed
        self.assertTrue(database.is_deposit_reference_claimed("binance_pay", test_ref))

        # 4. Record wallet transaction
        tx_id = database.record_wallet_transaction(
            user_id=test_uid,
            tx_type="deposit",
            amount=15.0,
            balance_before=0.0,
            balance_after=15.0,
            payment_method="binance_pay",
            reference_id=test_ref,
            details="Test Deposit"
        )
        self.assertIsNotNone(tx_id)

        history = database.get_user_wallet_history(test_uid, limit=5)
        self.assertGreater(len(history), 0)
        self.assertEqual(history[0]["amount"], 15.0)
        print(f"\n[OK] test_08: Wallet deposit, anti-replay, and audit transaction verified successfully.")

    def test_09_payment_providers_registry_and_binance_pay(self):
        """Verify payment providers modular architecture."""
        import payments
        orig_pay_id = config.BINANCE_PAY_ID
        if not config.BINANCE_PAY_ID:
            config.BINANCE_PAY_ID = "988320017"

        try:
            active = payments.get_active_providers()
            self.assertGreater(len(active), 0, "At least one payment provider must be active")

            prov = payments.get_provider("binance_pay")
            self.assertIsNotNone(prov, "Binance Pay provider must be registered")
            self.assertEqual(prov.id, "binance_pay")

            instructions = prov.get_deposit_instructions(user_id=12345)
            self.assertIn("account_id", instructions)
            self.assertTrue(len(instructions["account_id"]) > 0)
            self.assertEqual(instructions["currency"], "USDT")
            print(f"\n[OK] test_09: Payments registry verified. Active providers: {[p.id for p in active]}")
        finally:
            config.BINANCE_PAY_ID = orig_pay_id


if __name__ == "__main__":
    unittest.main()
