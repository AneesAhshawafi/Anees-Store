import hashlib
import hmac
import html
import logging
import time
import requests
from payments.base import BasePaymentProvider
import config

logger = logging.getLogger(__name__)


class BinancePayProvider(BasePaymentProvider):
    """Payment provider implementing Binance Pay SAPI transaction verification."""

    def __init__(self):
        self._server_time_offset = 0
        self._last_time_sync = 0

    @property
    def id(self) -> str:
        return "binance_pay"

    @property
    def display_name(self) -> str:
        return "Binance Pay (بينانس باي)"

    @property
    def icon(self) -> str:
        return "🟡"

    def is_configured(self) -> bool:
        """Returns True if Binance Pay ID is configured."""
        return bool(config.BINANCE_PAY_ID)

    def _get_proxies(self):
        if config.BINANCE_PROXY_URL:
            return {"http": config.BINANCE_PROXY_URL, "https": config.BINANCE_PROXY_URL}
        return None

    def _sync_server_time(self):
        """Calculates time difference between local clock and Binance server."""
        now = time.time()
        if now - self._last_time_sync < 300 and self._last_time_sync > 0:
            return  # Cache time offset for 5 minutes

        try:
            resp = requests.get("https://api.binance.com/api/v3/time", timeout=5, proxies=self._get_proxies())
            if resp.status_code == 200:
                server_time = resp.json().get("serverTime", int(now * 1000))
                local_time = int(now * 1000)
                self._server_time_offset = server_time - local_time
                self._last_time_sync = now
                logger.info(f"Binance time synced. Offset: {self._server_time_offset}ms")
        except Exception as e:
            logger.warning(f"Could not sync Binance server time: {e}")

    def _get_timestamp(self) -> int:
        self._sync_server_time()
        return int(time.time() * 1000) + self._server_time_offset

    def get_deposit_instructions(self, user_id: int) -> dict:
        pay_id = config.BINANCE_PAY_ID
        nickname = config.BINANCE_NICKNAME or "Anees Store"
        min_dep = config.MIN_DEPOSIT_USDT

        text = (
            "🟡 <b>شحن الرصيد عبر Binance Pay:</b>\n\n"
            "لإيداع رصيد في محفظتك، يرجى إرسال المبلغ عبر تطبيق Binance باتباع الخطوات التالية:\n\n"
            f"1️⃣ <b>معرف الدفع (Binance Pay ID):</b>\n"
            f"👉 <code>{pay_id}</code> <i>(اضغط للنسخ)</i>\n\n"
            f"2️⃣ <b>الاسم المستعار للحساب:</b> <b>{html.escape(nickname)}</b>\n"
            f"3️⃣ <b>العملة المقبولة:</b> <b>USDT</b>\n"
            f"4️⃣ <b>الحد الأدنى للإيداع:</b> <code>${min_dep:.2f} USDT</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📱 <b>طريقة الإرسال من التطبيق:</b>\n"
            "• افتح تطبيق <b>Binance</b> ⬅️ اضغط على أيقونة <b>Pay</b> ⬅️ <b>إرسال (Send)</b>.\n"
            "• اختر <b>معرف الدفع (Pay ID)</b> وألصق الرقم المنسوخ أعلاه.\n"
            "• حدد المبلغ المطلوب بالـ <b>USDT</b> وأتمم التحويل.\n"
            "• بعد نجاح العملية، افتح تفاصيل الإيصال وانسخ <b>رقم الطلب (Order ID)</b> أو <b>معرف المعاملة (Transaction ID)</b>.\n\n"
            "👇 <b>أرسل الآن رقم الطلب أو المعاملة إلى البوت ليتم إيداع الرصيد تلقائياً:</b>"
        )

        return {
            "title": "Binance Pay",
            "text": text,
            "account_id": pay_id,
            "nickname": nickname,
            "currency": "USDT",
            "min_amount": min_dep,
            "prompt_for_input": "يرجى كتابة أو لصق رقم الطلب (Order ID) الخاص بعملية التحويل في بينانس:"
        }

    def verify_deposit(self, user_id: int, proof_data: str) -> dict:
        """
        Verifies deposit proof against Binance SAPI.
        Looks for orderId or transactionId in recent transactions.
        """
        clean_ref = str(proof_data or "").strip()
        if not clean_ref:
            return {"verified": False, "error": "رقم المعاملة غير صالح أو فارغ."}

        api_key = config.BINANCE_API_KEY
        api_secret = config.BINANCE_API_SECRET

        if not api_key or not api_secret:
            logger.warning("Binance API keys not set. Auto-verification requires API credentials.")
            return {
                "verified": False,
                "error": "لم يتم إعداد مفاتيح التحقق الآلي في الخادم.",
                "reference_id": clean_ref,
                "requires_admin_review": True
            }

        # Query Binance SAPI transactions
        try:
            ts = self._get_timestamp()
            query = f"timestamp={ts}&recvWindow=60000"
            sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
            url = f"https://api.binance.com/sapi/v1/pay/transactions?{query}&signature={sig}"
            headers = {"X-MBX-APIKEY": api_key}
            proxies = self._get_proxies()

            resp = requests.get(url, headers=headers, timeout=12, proxies=proxies)
            if resp.status_code != 200:
                # Handle possible timestamp sync retry
                if resp.status_code == 400 and resp.json().get("code") == -1021:
                    logger.info("Resyncing Binance time after timestamp mismatch...")
                    self._last_time_sync = 0
                    self._sync_server_time()
                    ts = self._get_timestamp()
                    query = f"timestamp={ts}&recvWindow=60000"
                    sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
                    url = f"https://api.binance.com/sapi/v1/pay/transactions?{query}&signature={sig}"
                    resp = requests.get(url, headers=headers, timeout=12, proxies=proxies)

            data = resp.json()
            if not data.get("success") or "data" not in data:
                raw_err = data.get("errorMessage") or data.get("msg") or f"HTTP {resp.status_code}"
                logger.error(f"Binance SAPI error: {raw_err}")
                
                is_geo = "restricted location" in str(raw_err).lower() or "eligibility" in str(raw_err).lower()
                user_msg = (
                    "خادم الاستضافة متواجد في منطقة خاضعة لقيود بينانس الجغرافية (US). جاري تحويل المعاملة تلقائياً لمراجعة وتأكيد الإدارة."
                    if is_geo else f"تعذر استعلام بينانس: {raw_err}"
                )
                return {
                    "verified": False,
                    "error": user_msg,
                    "reference_id": clean_ref,
                    "requires_admin_review": True,
                    "is_geo_restricted": is_geo
                }

            transactions = data.get("data", [])
            target_tx = None

            # Look for matching orderId or transactionId
            for tx in transactions:
                t_order_id = str(tx.get("orderId", "")).strip()
                t_txn_id = str(tx.get("transactionId", "")).strip()

                if clean_ref == t_order_id or clean_ref.lower() == t_txn_id.lower():
                    target_tx = tx
                    break

            if not target_tx:
                return {
                    "verified": False,
                    "error": "لم نتمكن من العثور على عملية تطابق هذا الرقم في سجلات بينانس. تأكد من صحة الرقم أو انتظر لحظات وحاول ثانية.",
                    "reference_id": clean_ref,
                    "requires_admin_review": True
                }

            # Verify it's an incoming transaction (positive amount)
            raw_amount = target_tx.get("amount", "0")
            try:
                amount_val = float(raw_amount)
            except Exception:
                amount_val = 0.0

            if amount_val <= 0:
                return {
                    "verified": False,
                    "error": "هذه المعاملة مسجلة كحوالة صادرة وليست واردة لحساب المتجر!",
                    "reference_id": clean_ref
                }

            currency = str(target_tx.get("currency", "USDT")).upper()
            if currency != "USDT":
                return {
                    "verified": False,
                    "error": f"العملة المستلمة هي {currency}، بينما المتجر يقبل USDT فقط.",
                    "reference_id": clean_ref
                }

            if amount_val < config.MIN_DEPOSIT_USDT:
                return {
                    "verified": False,
                    "error": f"المبلغ المستلم (${amount_val:.2f}) أقل من الحد الأدنى للإيداع (${config.MIN_DEPOSIT_USDT:.2f}).",
                    "reference_id": clean_ref
                }

            matched_ref = str(target_tx.get("orderId") or clean_ref)
            return {
                "verified": True,
                "amount": amount_val,
                "currency": currency,
                "reference_id": matched_ref,
                "raw": target_tx
            }

        except Exception as e:
            logger.error(f"Error during Binance deposit verification: {e}")
            return {
                "verified": False,
                "error": f"حدث خطأ أثناء فحص المعاملة: {e}",
                "reference_id": clean_ref,
                "requires_admin_review": True
            }
