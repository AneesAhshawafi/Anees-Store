import os
import sys
import logging
import html
import re
import telebot
from telebot import types

import config
import database
from api_client import api_client
from elite_client import elite_client
import sync_worker
import payments


def clean_html_tags(text: str) -> str:
    """Removes raw <tg-emoji> and unsupported tags, leaving clean unicode emojis intact."""
    if not text:
        return ""
    # Extract inner content/emoji from <tg-emoji>...</tg-emoji>
    cleaned = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', str(text), flags=re.DOTALL)
    cleaned = re.sub(r'</?tg-emoji[^>]*>', '', cleaned)
    return cleaned.strip()

# Set stdout encoding for Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ShopBot")

# Initialize TeleBot
DEFAULT_DUMMY_TOKEN = "123456789:AAFDummyTokenForTestingOnlyXYZ1234"
active_token = config.BOT_TOKEN.strip() if config.BOT_TOKEN and ":" in config.BOT_TOKEN else DEFAULT_DUMMY_TOKEN

if not config.BOT_TOKEN or ":" not in config.BOT_TOKEN:
    logger.warning("BOT_TOKEN is not configured or invalid in .env! Using test placeholder.")

bot = telebot.TeleBot(active_token, parse_mode="HTML")

# In-memory dictionary for handling multi-step flows (e.g. ChatGPT email)
user_states = {}


def is_admin(user_id: int) -> bool:
    """Checks if user ID belongs to configured bot admins."""
    return user_id in config.ADMIN_IDS


def notify_admin(message_html: str):
    """Sends notification to all configured admins."""
    for admin_id in config.ADMIN_IDS:
        try:
            bot.send_message(admin_id, message_html, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


def notify_admin_with_markup(message_html: str, reply_markup=None):
    """Sends notification to all configured admins with optional inline keyboard."""
    for admin_id in config.ADMIN_IDS:
        try:
            bot.send_message(admin_id, message_html, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


def notify_order_completed(order_info: dict):
    """Callback triggered when a manual order is completed during background sync."""
    user_id = order_info.get("user_id")
    order_id = order_info.get("order_id")
    prod_name = order_info.get("product_name")
    codes = order_info.get("codes", [])

    codes_text = "\n".join([f"<code>{html.escape(c)}</code>" for c in codes]) if codes else "تم تفعيل طلبك بنجاح."

    text = (
        f"🎉 <b>تم اكتمال تسليم طلبك اليدوي بنجاح!</b>\n\n"
        f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n"
        f"📦 <b>المنتج:</b> <b>{html.escape(str(prod_name))}</b>\n\n"
        f"🔑 <b>بيانات الاستلام / الأكواد:</b>\n"
        f"{codes_text}\n\n"
        f"شكراً لصبرك ونتمنى لك تجربة سعيدة! ❤️"
    )

    if user_id:
        try:
            bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} of completed order {order_id}: {e}")


# ==========================================
# Keyboard Builders
# ==========================================

def get_main_keyboard():
    """Builds the main /start reply keyboard with 3 buttons: المتجر 1, المتجر 2, رصيدي."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_store1 = types.KeyboardButton("🏪 المتجر 1")
    btn_store2 = types.KeyboardButton("💎 المتجر 2")
    btn_balance = types.KeyboardButton("💰 رصيدي")
    markup.add(btn_store1, btn_store2)
    markup.add(btn_balance)
    return markup


def get_store1_keyboard():
    """Sub-menu keyboard for Store 1."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("🛒 منتجات المتجر 1"))
    markup.add(types.KeyboardButton("🔙 القائمة الرئيسية"))
    return markup


def get_store2_keyboard():
    """Sub-menu keyboard for Store 2 (Elite)."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("🛒 منتجات المتجر 2"))
    markup.add(types.KeyboardButton("🔙 القائمة الرئيسية"))
    return markup


def build_products_keyboard(products: list, page: int = 0):
    """Builds paginated inline keyboard for Store 1 products catalog."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    per_page = config.PRODUCTS_PER_PAGE
    total_pages = max(1, (len(products) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_products = products[start_idx:end_idx]

    for p in current_page_products:
        p_id = str(p.get("id") or p.get("_id", ""))
        raw_name = p.get("name_ar") or p.get("name_en") or "منتج"
        name = clean_html_tags(raw_name)
        
        # Price extraction (your_price overrides store_price)
        price = p.get("your_price") if p.get("your_price") is not None else p.get("store_price", 0.0)
        try:
            price_val = float(price)
            price_str = f"${price_val:.2f}"
        except Exception:
            price_str = f"${price}"

        # Delivery type icon
        is_manual = p.get("is_manual", False)
        icon = "🤝" if is_manual else "⚡"

        # Truncate long names for button
        if len(name) > 30:
            name = name[:27] + "..."

        btn_text = f"{icon} {name} | {price_str}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"p_{p_id}"))

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️ السابق", callback_data=f"page_{page - 1}"))
    
    nav_buttons.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))

    if page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("التالي ▶️", callback_data=f"page_{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    # Refresh button
    markup.row(types.InlineKeyboardButton("🔄 تحديث القائمة", callback_data="refresh_products"))

    return markup


def build_product_detail_keyboard(product_id: str, is_in_stock: bool):
    """Builds inline keyboard for Store 1 product details."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_in_stock:
        markup.add(types.InlineKeyboardButton("🛒 شراء الآن", callback_data=f"buy_{product_id}"))
    else:
        markup.add(types.InlineKeyboardButton("❌ غير متوفر حالياً", callback_data="noop"))
    markup.add(types.InlineKeyboardButton("🔙 العودة للمنتجات", callback_data="back_products"))
    return markup


def build_elite_products_keyboard(products: list, page: int = 0):
    """Builds paginated inline keyboard for Store 2 (Elite) products catalog."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    per_page = config.PRODUCTS_PER_PAGE
    total_pages = max(1, (len(products) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_products = products[start_idx:end_idx]

    for p in current_page_products:
        p_id = str(p.get("id", ""))
        raw_name = p.get("name") or "منتج إيليت"
        name = clean_html_tags(raw_name)

        price = p.get("price", 0.0)
        try:
            price_val = float(price)
            price_str = f"${price_val:.2f}"
        except Exception:
            price_str = f"${price}"

        stock = p.get("stock")
        if stock is not None and isinstance(stock, (int, float)) and stock <= 0:
            stock_badge = "❌"
        else:
            stock_badge = "⚡"

        if len(name) > 30:
            name = name[:27] + "..."

        btn_text = f"{stock_badge} {name} | {price_str}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"s2_p_{p_id}"))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️ السابق", callback_data=f"s2_page_{page - 1}"))

    nav_buttons.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))

    if page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("التالي ▶️", callback_data=f"s2_page_{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.row(types.InlineKeyboardButton("🔄 تحديث القائمة", callback_data="s2_refresh_products"))
    return markup


def build_elite_product_detail_keyboard(product_id: str, is_in_stock: bool):
    """Builds inline keyboard for Store 2 (Elite) product details."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_in_stock:
        markup.add(types.InlineKeyboardButton("🛒 شراء الآن", callback_data=f"s2_buy_{product_id}"))
    else:
        markup.add(types.InlineKeyboardButton("❌ نفد من المخزون", callback_data="noop"))
    markup.add(types.InlineKeyboardButton("🔙 العودة لمنتجات المتجر 2", callback_data="s2_back_products"))
    return markup


# ==========================================
# Handlers: /start & Main Menu
# ==========================================

@bot.message_handler(commands=["start"])
def handle_start(message: types.Message):
    """Handles /start command: updates user in DB and shows main 3 buttons."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)

    welcome_text = (
        f"👋 <b>أهلاً بك يا {html.escape(user.first_name or 'عزيزي')} في المتجر الرقمي!</b>\n\n"
        "🛍️ يمكنك استعراض كافة المنتجات الرقمية والاشتراكات والشراء بكل سهولة وأمان.\n\n"
        "اختر المتجر المطلوب أو استعرض رصيدك أدناه:\n"
        "• 🏪 <b>المتجر 1:</b> اشتراكات رقمية وخدمات متنوعة.\n"
        "• 💎 <b>المتجر 2:</b> أدوات وحسابات النخبة (Elite Tools Store).\n"
        "• 💰 <b>رصيدي:</b> لمعرفة رصيدك المسجل وطلباتك."
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda msg: msg.text in ("🏪 المتجر 1", "/store1"))
def handle_store1_menu(message: types.Message):
    """Handles '🏪 المتجر 1': displays Store 1 menu."""
    text = (
        "🏪 <b>المتجر 1 (الرئيسي):</b>\n\n"
        "يقدم تشكيلة واسعة من الاشتراكات والخدمات الرقمية المتنوعة.\n"
        "⚡ <b>تسليم فوري للأكواد</b> أو 🤝 <b>تسليم يدوي موثوق</b>.\n\n"
        "👇 اضغط على <b>🛒 منتجات المتجر 1</b> أدناه لعرض الكتالوج والشراء:"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_store1_keyboard(), parse_mode="HTML")


@bot.message_handler(func=lambda msg: msg.text in ("💎 المتجر 2", "/store2"))
def handle_store2_menu(message: types.Message):
    """Handles '💎 المتجر 2': displays Store 2 (Elite) menu."""
    text = (
        "💎 <b>المتجر 2 (Elite Tools Store):</b>\n\n"
        "متجر متخصص في حسابات وأدوات المطورين والذكاء الاصطناعي والتصميم.\n"
        "⚡ <b>تسليم فوري لبيانات وحسابات الدخول الجاهزة</b>.\n\n"
        "👇 اضغط على <b>🛒 منتجات المتجر 2</b> أدناه لعرض الكتالوج والشراء:"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_store2_keyboard(), parse_mode="HTML")


@bot.message_handler(func=lambda msg: msg.text == "🔙 القائمة الرئيسية")
def handle_back_to_main(message: types.Message):
    """Returns to the 3-button main menu."""
    text = "🏠 <b>القائمة الرئيسية:</b>\nاختر المتجر المطلوب أو استعرض رصيدك:"
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")


@bot.message_handler(func=lambda msg: msg.text in ("🛒 منتجات المتجر 1", "🛒 المنتجات", "/products"))
def handle_products_menu(message: types.Message):
    """Handles Store 1 products catalog: shows catalog instantly from cache."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)

    products = database.get_cached_products()
    if not products:
        loading_msg = bot.send_message(message.chat.id, "⏳ <i>جارٍ تجهيز قائمة المنتجات لأول مرة...</i>", parse_mode="HTML")
        products = api_client.get_products()
        if not products:
            bot.edit_message_text(
                "⚠️ <b>عذراً، لا توجد منتجات متاحة حالياً.</b> يرجى المحاولة لاحقاً.",
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                parse_mode="HTML"
            )
            return
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except Exception:
            pass

    text = (
        "🏪 <b>قائمة منتجات المتجر 1:</b>\n\n"
        "⚡ <b>تسليم فوري:</b> تصلك بيانات الكود مباشرة فور الشراء.\n"
        "🤝 <b>تسليم يدوي:</b> يتم تجهيزه وتسليمه لك من قِبل الإدارة.\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    bot.send_message(
        message.chat.id,
        text,
        reply_markup=build_products_keyboard(products, page=0),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda msg: msg.text in ("🛒 منتجات المتجر 2", "/elite_products"))
def handle_elite_products_menu(message: types.Message):
    """Handles Store 2 (Elite) products catalog: shows catalog instantly from cache."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)

    products = database.get_cached_elite_products()
    if not products:
        loading_msg = bot.send_message(message.chat.id, "⏳ <i>جارٍ تجهيز قائمة منتجات متجر Elite...</i>", parse_mode="HTML")
        products = elite_client.get_products()
        if not products:
            bot.edit_message_text(
                "⚠️ <b>عذراً، لا توجد منتجات متاحة حالياً في متجر Elite.</b> يرجى المحاولة لاحقاً.",
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                parse_mode="HTML"
            )
            return
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except Exception:
            pass

    text = (
        "💎 <b>قائمة منتجات المتجر 2 (Elite Tools):</b>\n\n"
        "⚡ <b>جميع المنتجات تسليم فوري لحسابات وبيانات جاهزة!</b>\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    bot.send_message(
        message.chat.id,
        text,
        reply_markup=build_elite_products_keyboard(products, page=0),
        parse_mode="HTML"
    )


def get_wallet_text(user, user_id: int) -> str:
    """Formats wallet and account overview message."""
    balance = database.get_user_balance(user_id)
    orders = database.get_user_orders(user_id, limit=50)
    total_dep = database.get_user_total_deposited(user_id)
    total_spent = database.get_user_total_spent(user_id)
    first_name = user.first_name if hasattr(user, "first_name") else "غير محدد"

    text = (
        f"👤 <b>معلومات المحفظة والحساب:</b>\n\n"
        f"🏷️ <b>الاسم:</b> {html.escape(first_name or 'غير محدد')}\n"
        f"🆔 <b>معرف الحساب:</b> <code>{user_id}</code>\n"
        f"💰 <b>الرصيد المتاح:</b> <code>${balance:.2f}</code>\n"
        f"💳 <b>إجمالي الإيداعات:</b> <code>${total_dep:.2f}</code>\n"
        f"🛍️ <b>إجمالي المشتريات:</b> <code>${total_spent:.2f}</code> ({len(orders)} طلب)\n\n"
        "💡 <i>يمكنك شحن رصيدك فوراً أو استعراض سجل الحركات عبر الأزرار أدناه:</i>"
    )

    if is_admin(user_id):
        api_bal_info = api_client.get_balance()
        api_bal = api_bal_info.get("balance", "N/A")

        elite_bal_info = elite_client.get_balance()
        elite_bal = elite_bal_info.get("balance", "N/A")

        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🛠️ <b>بيانات الإدارة (لوحة الأدمن):</b>\n"
            f"🏬 <b>رصيد المتجر 1 (الرئيسي):</b> <code>${api_bal}</code>\n"
            f"💎 <b>رصيد المتجر 2 (Elite):</b> <code>${elite_bal}</code>\n"
            f"💡 للمزامنة استخدم الأمر: /sync"
        )
    return text


def build_wallet_inline_keyboard():
    """Builds inline keyboard for user wallet view."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("💳 شحن الرصيد (إيداع)", callback_data="wallet_deposit"),
        types.InlineKeyboardButton("📜 سجل الحركات", callback_data="wallet_history")
    )
    markup.add(
        types.InlineKeyboardButton("🏪 منتجات المتجر 1", callback_data="nav_store1"),
        types.InlineKeyboardButton("💎 منتجات المتجر 2", callback_data="nav_store2")
    )
    return markup


@bot.message_handler(func=lambda msg: msg.text in ("💰 رصيدي", "/balance"))
def handle_balance_menu(message: types.Message):
    """Handles '💰 رصيدي': shows user wallet balance & orders info."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)
    text = get_wallet_text(user, user.id)
    bot.send_message(message.chat.id, text, reply_markup=build_wallet_inline_keyboard(), parse_mode="HTML")


# ==========================================
# Callback Handlers: Navigation & Products
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data == "noop")
def handle_noop(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)


# ==========================================
# Callback Handlers: Wallet & Deposits
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data == "wallet_main")
def handle_wallet_main(call: types.CallbackQuery):
    """Returns to the main wallet balance view."""
    bot.answer_callback_query(call.id)
    text = get_wallet_text(call.from_user, call.from_user.id)
    markup = build_wallet_inline_keyboard()
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "wallet_deposit")
def handle_wallet_deposit_menu(call: types.CallbackQuery):
    """Shows active payment providers for deposit."""
    bot.answer_callback_query(call.id)
    active_providers = payments.get_active_providers()

    if not active_providers:
        bot.send_message(
            call.message.chat.id,
            "⚠️ <b>عذراً:</b> لا توجد طرق دفع مفعلة حالياً في النظام. يرجى التواصل مع إدارة المتجر.",
            parse_mode="HTML"
        )
        return

    text = (
        "💳 <b>شحن وإيداع الرصيد في المحفظة:</b>\n\n"
        "اختر وسيلة الدفع المناسبة لك أدناه للمتابعة:\n"
        "⚡ <i>يتم التحقق الآلي من الحوالة وإيداع الرصيد فور إرسال إثبات التحويل!</i>"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    for p in active_providers:
        markup.add(types.InlineKeyboardButton(f"{p.icon} {p.display_name}", callback_data=f"dep_prov_{p.id}"))

    markup.add(types.InlineKeyboardButton("🔙 العودة للمحفظة", callback_data="wallet_main"))

    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("dep_prov_"))
def handle_deposit_provider_selected(call: types.CallbackQuery):
    """Displays deposit instructions for the chosen provider and asks for transaction proof."""
    bot.answer_callback_query(call.id)
    prov_id = call.data.replace("dep_prov_", "")
    provider = payments.get_provider(prov_id)

    if not provider or not provider.is_configured():
        bot.send_message(call.message.chat.id, "⚠️ وسيلة الدفع هذه غير متوفرة حالياً.", parse_mode="HTML")
        return

    info = provider.get_deposit_instructions(call.from_user.id)
    text = info.get("text", "")

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🔙 إلغاء والعودة", callback_data="wallet_deposit"))

    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")

    prompt_msg = bot.send_message(
        call.message.chat.id,
        "✍️ <b>يرجى إرسال رقم الطلب (Order ID) أو معرف المعاملة الآن:</b>\n"
        "<i>(أو أرسل /cancel لإلغاء العملية)</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(prompt_msg, process_deposit_proof, prov_id)


def process_deposit_proof(message: types.Message, provider_id: str):
    """Processes customer's submitted payment proof (e.g. Binance Order ID)."""
    proof_text = (message.text or "").strip()
    if proof_text.lower() in ("/cancel", "الغاء", "إلغاء"):
        bot.send_message(message.chat.id, "❌ تم إلغاء عملية الشحن.", reply_markup=get_main_keyboard())
        return

    provider = payments.get_provider(provider_id)
    if not provider:
        bot.send_message(message.chat.id, "⚠️ مزود الدفع غير متوفر.", reply_markup=get_main_keyboard())
        return

    if len(proof_text) < 4:
        retry_msg = bot.send_message(
            message.chat.id,
            "⚠️ <b>رقم المعاملة قصير جداً أو غير صحيح!</b>\nيرجى إرسال رقم الطلب الصحيح أو أرسل /cancel للإلغاء:",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(retry_msg, process_deposit_proof, provider_id)
        return

    user = message.from_user

    # Anti-replay: Check if already credited in DB
    if database.is_deposit_reference_claimed(provider_id, proof_text):
        bot.send_message(
            message.chat.id,
            "⚠️ <b>تنبيه أمني:</b> هذا الإيصال أو رقم المعاملة تم استخدامه وقيد رصيده مسبقاً في المحفظة!\n"
            "لا يمكن استخدام نفس الإيصال أكثر من مرة.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    wait_msg = bot.send_message(
        message.chat.id,
        "⏳ <i>جارٍ فحص المعاملة مع مزود الدفع والتأكد من وصول الحوالة... يُرجى الانتظار لحظات.</i>",
        parse_mode="HTML"
    )

    result = provider.verify_deposit(user.id, proof_text)

    if result.get("verified"):
        amount = float(result.get("amount", 0.0))
        currency = result.get("currency", "USDT")
        ref_id = str(result.get("reference_id", proof_text))

        # Claim atomically in MongoDB
        claimed = database.claim_deposit_reference(
            provider=provider_id,
            reference_id=ref_id,
            user_id=user.id,
            amount=amount,
            currency=currency,
            details=result.get("raw")
        )

        if not claimed:
            bot.edit_message_text(
                "⚠️ هذا الإيصال تم استخدامه مسبقاً!",
                chat_id=message.chat.id,
                message_id=wait_msg.message_id,
                parse_mode="HTML"
            )
            return

        # Credit wallet balance
        before_bal = database.get_user_balance(user.id)
        new_bal = database.add_user_balance(user.id, amount)

        # Record wallet transaction
        database.record_wallet_transaction(
            user_id=user.id,
            tx_type="deposit",
            amount=amount,
            balance_before=before_bal,
            balance_after=new_bal,
            payment_method=provider_id,
            reference_id=ref_id,
            details=f"شحن تلقائي عبر {provider.display_name}"
        )

        success_text = (
            "🎉 <b>تم شحن رصيدك بنجاح!</b>\n\n"
            f"💵 <b>المبلغ المودع:</b> <code>${amount:.2f} {currency}</code>\n"
            f"💰 <b>رصيدك الجديد في المحفظة:</b> <code>${new_bal:.2f}</code>\n"
            f"🆔 <b>رقم المعاملة:</b> <code>{ref_id}</code>\n\n"
            "نتمنى لك تجربة تسوق ممتعة ومميزة! ❤️"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🏪 منتجات المتجر 1", callback_data="nav_store1"),
            types.InlineKeyboardButton("💎 منتجات المتجر 2", callback_data="nav_store2")
        )
        markup.add(types.InlineKeyboardButton("💰 عرض المحفظة", callback_data="wallet_main"))

        try:
            bot.edit_message_text(success_text, chat_id=message.chat.id, message_id=wait_msg.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception:
            bot.send_message(message.chat.id, success_text, reply_markup=markup, parse_mode="HTML")

        # Notify Admin
        buyer_info = f"@{user.username}" if user.username else f"ID: {user.id} ({user.first_name})"
        admin_text = (
            "🔔 <b>إيداع جديد في محفظة المتجر!</b>\n\n"
            f"👤 <b>العميل:</b> {html.escape(user.first_name or '')} ({buyer_info})\n"
            f"🆔 <b>معرف العميل (ID):</b> <code>{user.id}</code>\n"
            f"💵 <b>المبلغ المودع:</b> <code>${amount:.2f} {currency}</code>\n"
            f"💰 <b>رصيد العميل بعد الإيداع:</b> <code>${new_bal:.2f}</code>\n"
            f"💳 <b>طريقة الإيداع:</b> {provider.display_name}\n"
            f"🆔 <b>رقم العملية:</b> <code>{ref_id}</code>\n"
            "⚡ <b>حالة التحقق:</b> تم الفحص الآلي وإيداع الرصيد بنجاح."
        )
        notify_admin(admin_text)

    else:
        # Automated verification could not confirm
        error_msg = result.get("error", "لم يتم العثور على المعاملة.")
        pending_id = database.create_pending_deposit(
            user_id=user.id,
            provider=provider_id,
            reference_id=proof_text,
            user_note=error_msg
        )

        if result.get("is_geo_restricted"):
            # Automatically forward to admin review right away
            buyer_info = f"@{user.username}" if user.username else f"ID: {user.id} ({user.first_name})"
            admin_markup = types.InlineKeyboardMarkup(row_width=2)
            admin_markup.add(
                types.InlineKeyboardButton("✅ موافقة وشحن", callback_data=f"adm_app_{pending_id}"),
                types.InlineKeyboardButton("❌ رفض", callback_data=f"adm_rej_{pending_id}")
            )
            admin_text = (
                "📩 <b>طلب شحن رصيد جديد (يحتاج تأكيد الإدارة):</b>\n\n"
                f"🆔 <b>معرف الطلب:</b> <code>{pending_id}</code>\n"
                f"👤 <b>العميل:</b> {html.escape(user.first_name or '')} ({buyer_info})\n"
                f"🆔 <b>معرف العميل (ID):</b> <code>{user.id}</code>\n"
                f"💳 <b>وسيلة الدفع:</b> {provider.display_name}\n"
                f"🔢 <b>رقم العملية المقدم:</b> <code>{proof_text}</code>\n\n"
                "💡 <i>تحقق من إشعار تطبيق بينانس، ثم اضغط على موافقة لشحن الرصيد للعميل أو رفض.</i>"
            )
            notify_admin_with_markup(admin_text, admin_markup)

            auto_forward_text = (
                "⏳ <b>تم استلام رقم المعاملة بنجاح، وجارٍ التأكيد مع الإدارة!</b>\n\n"
                f"🔢 <b>رقم المعاملة:</b> <code>{proof_text}</code>\n"
                f"🆔 <b>معرف المتابعة:</b> <code>{pending_id}</code>\n\n"
                "📌 <i>تم إرسال إشعار فوري لإدارة المتجر لمطابقة الإيصال وإيداع الرصيد في محفظتك خلال لحظات. شكراً لصبرك! ❤️</i>"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("💰 عرض المحفظة", callback_data="wallet_main"))
            try:
                bot.edit_message_text(auto_forward_text, chat_id=message.chat.id, message_id=wait_msg.message_id, reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot.send_message(message.chat.id, auto_forward_text, reply_markup=markup, parse_mode="HTML")
            return

        fail_text = (
            "⚠️ <b>تعذر التحقق التلقائي من رقم المعاملة:</b>\n"
            f"<blockquote>{html.escape(error_msg)}</blockquote>\n\n"
            "💡 <b>ماذا يمكنك أن تفعل؟</b>\n"
            "• تأكد أنك نسخت <b>رقم الطلب (Order ID)</b> الصحيح من إيصال بينانس.\n"
            "• قد يستغرق ظهور الحوالة في النظام ثوانٍ معدودة، يمكنك المحاولة مرة أخرى.\n"
            "• أو اضغط على <b>📩 إرسال للإدارة للمراجعة</b> وسيقوم فريق العمل بفحصها وشحنها لك فوراً."
        )

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📩 إرسال للإدارة للمراجعة الفورية", callback_data=f"dep_review_{pending_id}"),
            types.InlineKeyboardButton("🔄 إعادة المحاولة", callback_data=f"dep_prov_{provider_id}"),
            types.InlineKeyboardButton("🔙 العودة للمحفظة", callback_data="wallet_main")
        )

        try:
            bot.edit_message_text(fail_text, chat_id=message.chat.id, message_id=wait_msg.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception:
            bot.send_message(message.chat.id, fail_text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("dep_review_"))
def handle_deposit_request_review(call: types.CallbackQuery):
    """Customer submits pending deposit for admin review."""
    pending_id = call.data.replace("dep_review_", "")
    pending = database.get_pending_deposit(pending_id)

    if not pending:
        bot.answer_callback_query(call.id, "⚠️ الطلب غير متوفر أو تمت معالجته.", show_alert=True)
        return

    user = call.from_user
    buyer_info = f"@{user.username}" if user.username else f"ID: {user.id} ({user.first_name})"

    bot.edit_message_text(
        "⏳ <b>تم إرسال طلب الشحن إلى إدارة المتجر للمراجعة الفورية!</b>\n\n"
        f"🆔 <b>معرف الطلب الداخلي:</b> <code>{pending_id}</code>\n"
        f"🔢 <b>رقم المعاملة:</b> <code>{pending.get('reference_id')}</code>\n\n"
        "سيصلك إشعار فوري في البوت فور مراجعة الإدارة وتأكيد الإيداع في محفظتك. شكراً لصبرك! ❤️",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id, "تم إرسال الطلب للإدارة بنجاح!")

    # Notify Admins with action buttons
    admin_markup = types.InlineKeyboardMarkup(row_width=2)
    admin_markup.add(
        types.InlineKeyboardButton("✅ موافقة وشحن", callback_data=f"adm_app_{pending_id}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"adm_rej_{pending_id}")
    )
    admin_text = (
        "📩 <b>طلب شحن رصيد معلق يحتاج مراجعة الإدارة!</b>\n\n"
        f"🆔 <b>معرف الطلب:</b> <code>{pending_id}</code>\n"
        f"👤 <b>العميل:</b> {html.escape(user.first_name or '')} ({buyer_info})\n"
        f"🆔 <b>معرف العميل (ID):</b> <code>{user.id}</code>\n"
        f"💳 <b>وسيلة الدفع:</b> {pending.get('provider')}\n"
        f"🔢 <b>رقم العملية المقدم:</b> <code>{pending.get('reference_id')}</code>\n\n"
        "💡 <i>تحقق من تطبيق الدفع، ثم اضغط على موافقة لشحن الرصيد للعميل أو رفض.</i>"
    )
    notify_admin_with_markup(admin_text, admin_markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_app_"))
def handle_admin_approve_deposit(call: types.CallbackQuery):
    """Admin clicks approve on pending deposit."""
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ خاص بالإدارة فقط.")
        return

    pending_id = call.data.replace("adm_app_", "")
    pending = database.get_pending_deposit(pending_id)
    if not pending or pending.get("status") != "pending":
        bot.answer_callback_query(call.id, "⚠️ تمت معالجة هذا الطلب مسبقاً.", show_alert=True)
        return

    msg = bot.send_message(
        call.message.chat.id,
        f"💵 <b>الموافقة على طلب الشحن <code>{pending_id}</code>:</b>\n\n"
        f"العميل: <code>{pending.get('user_id')}</code>\n"
        f"رقم الإيصال: <code>{pending.get('reference_id')}</code>\n\n"
        "أدخل <b>المبلغ بالدولار ($)</b> الذي تريد إيداعه في محفظة العميل:\n"
        "<i>(مثال: <code>5</code> أو <code>10.5</code> أو أرسل /cancel لإلغاء الأمر)</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_admin_manual_deposit, pending_id, call.message.message_id)
    bot.answer_callback_query(call.id)


def process_admin_manual_deposit(message: types.Message, pending_id: str, admin_msg_id: int):
    """Processes admin manual deposit amount input."""
    amount_str = (message.text or "").strip()
    if amount_str.lower() in ("/cancel", "الغاء", "إلغاء"):
        bot.send_message(message.chat.id, "❌ تم إلغاء العملية.")
        return

    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError()
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ المبلغ غير صالح! أدخل رقماً صحيحاً (مثال: 10) أو أرسل /cancel:")
        bot.register_next_step_handler(msg, process_admin_manual_deposit, pending_id, admin_msg_id)
        return

    pending = database.get_pending_deposit(pending_id)
    if not pending or pending.get("status") != "pending":
        bot.send_message(message.chat.id, "⚠️ تمت معالجة هذا الطلب مسبقاً.")
        return

    target_user_id = pending.get("user_id")
    ref_id = pending.get("reference_id")
    provider = pending.get("provider", "manual")

    # Mark resolved in DB
    database.resolve_pending_deposit(pending_id, status="approved", resolved_by=message.from_user.id, final_amount=amount)
    database.claim_deposit_reference(provider, ref_id, target_user_id, amount, "USDT", {"approved_by": message.from_user.id})

    # Credit user balance
    before_bal = database.get_user_balance(target_user_id)
    new_bal = database.add_user_balance(target_user_id, amount)

    # Record wallet transaction
    database.record_wallet_transaction(
        user_id=target_user_id,
        tx_type="deposit",
        amount=amount,
        balance_before=before_bal,
        balance_after=new_bal,
        payment_method=f"{provider}_manual",
        reference_id=ref_id,
        details=f"موافقة يدوية من الإدارة بواسطة {message.from_user.id}"
    )

    bot.send_message(
        message.chat.id,
        f"✅ <b>تم شحن رصيد العميل بنجاح!</b>\n\n"
        f"👤 <b>العميل:</b> <code>{target_user_id}</code>\n"
        f"💵 <b>المبلغ المودع:</b> <code>${amount:.2f}</code>\n"
        f"💰 <b>رصيده الجديد:</b> <code>${new_bal:.2f}</code>",
        parse_mode="HTML"
    )

    # Notify customer
    try:
        bot.send_message(
            target_user_id,
            f"🎉 <b>تمت الموافقة على طلب الشحن الخاص بك!</b>\n\n"
            f"💵 <b>المبلغ المضاف:</b> <code>${amount:.2f} USDT</code>\n"
            f"💰 <b>رصيدك الحالي في المحفظة:</b> <code>${new_bal:.2f}</code>\n"
            f"🆔 <b>رقم المعاملة:</b> <code>{ref_id}</code>\n\n"
            "شكراً لتسوقك معنا! نتشرف بخدمتك دائماً ❤️",
            reply_markup=build_wallet_inline_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify user {target_user_id} of approved deposit: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_rej_"))
def handle_admin_reject_deposit(call: types.CallbackQuery):
    """Admin rejects pending deposit."""
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ خاص بالإدارة فقط.")
        return

    pending_id = call.data.replace("adm_rej_", "")
    pending = database.get_pending_deposit(pending_id)
    if not pending or pending.get("status") != "pending":
        bot.answer_callback_query(call.id, "⚠️ تمت معالجة هذا الطلب مسبقاً.", show_alert=True)
        return

    database.resolve_pending_deposit(pending_id, status="rejected", resolved_by=call.from_user.id)
    target_user_id = pending.get("user_id")

    try:
        bot.edit_message_text(
            f"❌ <b>تم رفض طلب الشحن <code>{pending_id}</code> بواسطة الإدارة.</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
    except Exception:
        pass

    bot.answer_callback_query(call.id, "تم رفض الطلب.")

    # Notify customer
    try:
        bot.send_message(
            target_user_id,
            f"❌ <b>عذراً، تم رفض طلب الشحن رقم <code>{pending_id}</code> من قِبل الإدارة.</b>\n\n"
            "تأكد من صحة رقم المعاملة وتحويل المبلغ، أو تواصل مع الدعم الفني للاستفسار.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify user {target_user_id} of rejected deposit: {e}")


@bot.callback_query_handler(func=lambda call: call.data == "wallet_history")
def handle_wallet_history(call: types.CallbackQuery):
    """Displays last wallet transactions for the user."""
    bot.answer_callback_query(call.id)
    user = call.from_user
    txs = database.get_user_wallet_history(user.id, limit=8)

    if not txs:
        text = (
            "📜 <b>سجل حركات المحفظة:</b>\n\n"
            "📭 لا توجد حركات مالية مسجلة في محفظتك حتى الآن.\n"
            "اشحن محفظتك الآن واستمتع بالتسوق والشراء السريع!"
        )
    else:
        lines = ["📜 <b>سجل آخر حركات المحفظة:</b>\n"]
        for t in txs:
            t_type = t.get("type")
            amt = float(t.get("amount", 0.0))
            bal_after = float(t.get("balance_after", 0.0))
            date_val = t.get("created_at")
            date_str = date_val.strftime("%m/%d %H:%M") if date_val else ""

            if t_type == "deposit":
                badge = "➕ إيداع"
                amt_str = f"+${amt:.2f}"
            elif t_type in ("purchase_store1", "purchase_store2"):
                badge = "➖ شراء"
                amt_str = f"-${abs(amt):.2f}"
            elif t_type == "refund":
                badge = "🔄 استرجاع"
                amt_str = f"+${abs(amt):.2f}"
            else:
                badge = "⚙️ تسوية"
                amt_str = f"${amt:.2f}"

            ref_str = f" | {t.get('reference_id')}" if t.get("reference_id") else ""
            lines.append(f"• <b>{badge}:</b> <code>{amt_str}</code> (الرصيد: <code>${bal_after:.2f}</code>)\n  🕒 {date_str}{ref_str}")

        text = "\n".join(lines)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("💳 شحن الرصيد", callback_data="wallet_deposit"),
        types.InlineKeyboardButton("🔙 العودة للمحفظة", callback_data="wallet_main")
    )

    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "nav_store1")
def handle_nav_store1(call: types.CallbackQuery):
    """Direct navigation shortcut to Store 1 products catalog."""
    bot.answer_callback_query(call.id)
    products = database.get_cached_products()
    if not products:
        products = api_client.get_products()

    text = (
        "🏪 <b>قائمة منتجات المتجر 1:</b>\n\n"
        "⚡ <b>تسليم فوري:</b> تصلك بيانات الكود مباشرة فور الشراء.\n"
        "🤝 <b>تسليم يدوي:</b> يتم تجهيزه وتسليمه لك من قِبل الإدارة.\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_products_keyboard(products, page=0), parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=build_products_keyboard(products, page=0), parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "nav_store2")
def handle_nav_store2(call: types.CallbackQuery):
    """Direct navigation shortcut to Store 2 products catalog."""
    bot.answer_callback_query(call.id)
    products = database.get_cached_elite_products()
    if not products:
        products = elite_client.get_products()

    text = (
        "💎 <b>قائمة منتجات المتجر 2 (Elite Tools):</b>\n\n"
        "⚡ <b>جميع المنتجات تسليم فوري لحسابات وبيانات جاهزة!</b>\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_elite_products_keyboard(products, page=0), parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=build_elite_products_keyboard(products, page=0), parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("page_"))
def handle_catalog_page(call: types.CallbackQuery):
    """Handles pagination buttons."""
    page_num = int(call.data.replace("page_", ""))
    products = database.get_cached_products()
    if not products:
        products = api_client.get_products()

    markup = build_products_keyboard(products, page=page_num)
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    except Exception:
        pass
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "back_products")
def handle_back_products(call: types.CallbackQuery):
    """Returns to catalog instantly from cache without network latency."""
    bot.answer_callback_query(call.id)
    products = database.get_cached_products()
    if not products:
        products = api_client.get_products()

    text = (
        "📦 <b>قائمة المنتجات والاشتراكات المتاحة:</b>\n\n"
        "⚡ <b>تسليم فوري:</b> تصلك بيانات الكود مباشرة فور الشراء.\n"
        "🤝 <b>تسليم يدوي:</b> يتم تجهيزه وتسليمه لك من قِبل الإدارة.\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=build_products_keyboard(products, page=0),
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=build_products_keyboard(products, page=0),
            parse_mode="HTML"
        )


@bot.callback_query_handler(func=lambda call: call.data == "refresh_products")
def handle_refresh_products(call: types.CallbackQuery):
    """Refreshes product list from API."""
    bot.answer_callback_query(call.id, text="🔄 جارٍ تحديث المنتجات...")
    products = api_client.get_products()
    if not products:
        products = database.get_cached_products()

    text = (
        "📦 <b>قائمة المنتجات والاشتراكات المتاحة:</b>\n\n"
        "⚡ <b>تسليم فوري:</b> تصلك بيانات الكود مباشرة فور الشراء.\n"
        "🤝 <b>تسليم يدوي:</b> يتم تجهيزه وتسليمه لك من قِبل الإدارة.\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=build_products_keyboard(products, page=0),
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=build_products_keyboard(products, page=0),
            parse_mode="HTML"
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("p_"))
def handle_product_details(call: types.CallbackQuery):
    """Displays full details of a specific product instantly from cache."""
    prod_id = call.data.replace("p_", "")
    product = database.get_cached_product(prod_id) or api_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ المنتج غير متوفر حالياً.", show_alert=True)
        return

    raw_name = product.get("name_ar") or product.get("name_en") or "منتج بدون اسم"
    name = clean_html_tags(raw_name)
    raw_desc = product.get("desc_ar") or product.get("desc_en") or "لا يوجد وصف إضافي."
    desc = clean_html_tags(raw_desc)
    
    price = product.get("your_price") if product.get("your_price") is not None else product.get("store_price", 0.0)
    try:
        price_val = float(price)
        price_str = f"${price_val:.2f}"
    except Exception:
        price_str = f"${price}"

    stock = product.get("stock")
    is_in_stock = True
    if stock == "unlimited":
        stock_str = "غير محدود ♾️"
    elif isinstance(stock, (int, float)):
        if stock <= 0:
            stock_str = "نفد من المخزون ❌"
            is_in_stock = False
        else:
            stock_str = f"{int(stock)} متوفر"
    else:
        stock_str = str(stock)

    is_manual = product.get("is_manual", False)
    if is_manual:
        delivery_type = "🤝 <b>تسليم يدوي</b> (يتم تسليم الطلب يدوياً بواسطة الإدارة)"
    else:
        delivery_type = "⚡ <b>تسليم فوري</b> (تصلك الأكواد مباشرة بعد إتمام الشراء)"

    source = product.get("source")
    extra_note = ""
    if source == "chatgpt_business":
        extra_note = "\n\n💡 <i>ملاحظة: هذا المنتج يتطلب إدخال بريدك الإلكتروني لإرسال دعوة التفعيل.</i>"

    details_text = (
        f"📦 <b>{html.escape(name)}</b>\n\n"
        f"📝 <b>الوصف:</b>\n"
        f"<blockquote>{html.escape(desc)}</blockquote>\n\n"
        f"💵 <b>السعر:</b> <code>{price_str}</code>\n"
        f"📊 <b>المخزون:</b> <b>{stock_str}</b>\n"
        f"🚚 <b>نوع التسليم:</b> {delivery_type}"
        f"{extra_note}"
    )

    markup = build_product_detail_keyboard(prod_id, is_in_stock)

    try:
        bot.edit_message_text(
            details_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            details_text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    bot.answer_callback_query(call.id)


# ==========================================
# Callback Handlers: Store 2 (Elite) Navigation & Details
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("s2_page_"))
def handle_elite_catalog_page(call: types.CallbackQuery):
    """Handles pagination for Store 2 (Elite) products catalog."""
    page_num = int(call.data.replace("s2_page_", ""))
    products = database.get_cached_elite_products()
    if not products:
        products = elite_client.get_products()

    markup = build_elite_products_keyboard(products, page=page_num)
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    except Exception:
        pass
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "s2_back_products")
def handle_elite_back_products(call: types.CallbackQuery):
    """Returns to Store 2 catalog instantly from cache without network latency."""
    bot.answer_callback_query(call.id)
    products = database.get_cached_elite_products()
    if not products:
        products = elite_client.get_products()

    text = (
        "💎 <b>قائمة منتجات المتجر 2 (Elite Tools):</b>\n\n"
        "⚡ <b>جميع المنتجات تسليم فوري لحسابات وبيانات جاهزة!</b>\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=build_elite_products_keyboard(products, page=0),
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=build_elite_products_keyboard(products, page=0),
            parse_mode="HTML"
        )


@bot.callback_query_handler(func=lambda call: call.data == "s2_refresh_products")
def handle_elite_refresh_products(call: types.CallbackQuery):
    """Refreshes Store 2 products from Elite API."""
    bot.answer_callback_query(call.id, text="🔄 جارٍ تحديث منتجات المتجر 2...")
    products = elite_client.get_products()
    if not products:
        products = database.get_cached_elite_products()

    text = (
        "💎 <b>قائمة منتجات المتجر 2 (Elite Tools):</b>\n\n"
        "⚡ <b>جميع المنتجات تسليم فوري لحسابات وبيانات جاهزة!</b>\n\n"
        "👇 <i>اضغط على أي منتج لعرض تفاصيله والشراء:</i>"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=build_elite_products_keyboard(products, page=0),
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=build_elite_products_keyboard(products, page=0),
            parse_mode="HTML"
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("s2_p_"))
def handle_elite_product_details(call: types.CallbackQuery):
    """Displays full details of a specific Store 2 product instantly."""
    prod_id = call.data.replace("s2_p_", "")
    product = database.get_cached_elite_product(prod_id) or elite_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ المنتج غير متوفر حالياً.", show_alert=True)
        return

    raw_name = product.get("name") or "منتج إيليت"
    name = clean_html_tags(raw_name)
    raw_desc = product.get("description") or "تسليم فوري لبيانات وحسابات الاستخدام الجاهزة."
    desc = clean_html_tags(raw_desc)

    price = product.get("price", 0.0)
    try:
        price_val = float(price)
        price_str = f"${price_val:.2f}"
    except Exception:
        price_str = f"${price}"

    stock = product.get("stock")
    is_in_stock = True
    if stock is not None and isinstance(stock, (int, float)):
        if stock <= 0:
            stock_str = "نفد من المخزون ❌"
            is_in_stock = False
        else:
            stock_str = f"{int(stock)} متوفر"
    else:
        stock_str = "متوفر ⚡"

    warranty = product.get("warranty")
    warranty_text = f"\n🛡️ <b>الضمان:</b> {html.escape(str(warranty))}" if warranty else ""

    details_text = (
        f"💎 <b>{html.escape(name)}</b>\n\n"
        f"📝 <b>الوصف:</b>\n"
        f"<blockquote>{html.escape(desc)}</blockquote>\n\n"
        f"💵 <b>السعر:</b> <code>{price_str}</code>\n"
        f"📊 <b>المخزون:</b> <b>{stock_str}</b>\n"
        f"⚡ <b>نوع التسليم:</b> فوري لبيانات الدخول والحساب"
        f"{warranty_text}"
    )

    markup = build_elite_product_detail_keyboard(prod_id, is_in_stock)

    try:
        bot.edit_message_text(
            details_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            details_text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    bot.answer_callback_query(call.id)


# ==========================================
# Handlers: Purchase Flow
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def handle_buy_button(call: types.CallbackQuery):
    """Initiates purchase confirmation or prompts for email if ChatGPT product."""
    user = call.from_user
    prod_id = call.data.replace("buy_", "")
    product = database.get_cached_product(prod_id) or api_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ المنتج غير متوفر.", show_alert=True)
        return

    # Check stock
    stock = product.get("stock")
    if isinstance(stock, (int, float)) and stock <= 0:
        bot.answer_callback_query(call.id, "⚠️ عذراً، نفد المخزون من هذا المنتج!", show_alert=True)
        return

    price = product.get("your_price") if product.get("your_price") is not None else product.get("store_price", 0.0)
    try:
        price_val = float(price)
    except Exception:
        price_val = 0.0

    # User balance check (if REQUIRE_USER_BALANCE is enabled)
    if config.REQUIRE_USER_BALANCE:
        user_bal = database.get_user_balance(user.id)
        if user_bal < price_val:
            diff = price_val - user_bal
            insufficient_text = (
                f"⚠️ <b>رصيدك في المحفظة غير كافٍ لإتمام عملية الشراء!</b>\n\n"
                f"💰 <b>رصيدك الحالي:</b> <code>${user_bal:.2f}</code>\n"
                f"💵 <b>سعر المنتج:</b> <code>${price_val:.2f}</code>\n"
                f"🔻 <b>المبلغ المطلوب شحنه:</b> <code>${diff:.2f}</code>\n\n"
                f"👇 يمكنك شحن محفظتك فوراً واختيار وسيلة الدفع المناسبة:"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("💳 شحن الرصيد الآن", callback_data="wallet_deposit"),
                types.InlineKeyboardButton("🔙 العودة للمنتج", callback_data=f"p_{prod_id}")
            )
            try:
                bot.edit_message_text(insufficient_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot.send_message(call.message.chat.id, insufficient_text, reply_markup=markup, parse_mode="HTML")
            bot.answer_callback_query(call.id)
            return

    # Check if ChatGPT product (requires email)
    if product.get("source") == "chatgpt_business":
        msg = bot.send_message(
            call.message.chat.id,
            "📧 <b>منتج اشتراك ChatGPT:</b>\n\n"
            "يرجى إرسال <b>البريد الإلكتروني (Email)</b> الخاص بك المرتبط بحساب ChatGPT لإرسال الدعوة إليه:\n"
            "<i>(أو أرسل /cancel لإلغاء العملية)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_chatgpt_email, prod_id, price_val, product)
        bot.answer_callback_query(call.id)
        return

    # Regular confirmation dialogue
    raw_name = product.get("name_ar") or product.get("name_en") or "منتج"
    name = clean_html_tags(raw_name)
    is_manual = product.get("is_manual", False)
    delivery_type_str = "🤝 تسليم يدوي" if is_manual else "⚡ تسليم فوري"

    confirm_text = (
        f"🧾 <b>تأكيد عملية الشراء:</b>\n\n"
        f"📦 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
        f"💵 <b>المبلغ الإجمالي:</b> <code>${price_val:.2f}</code>\n"
        f"🚚 <b>نوع التسليم:</b> {delivery_type_str}\n\n"
        f"هل أنت متأكد من رغبتك في إتمام الشراء الآن؟"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f"confirm_buy_{prod_id}"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data=f"p_{prod_id}")
    )

    try:
        bot.edit_message_text(
            confirm_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(call.message.chat.id, confirm_text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)


def process_chatgpt_email(message: types.Message, prod_id: str, price: float, product: dict):
    """Processes email input for ChatGPT purchases."""
    email_text = (message.text or "").strip()
    if email_text.lower() in ("/cancel", "الغاء", "إلغاء"):
        bot.send_message(message.chat.id, "❌ تم إلغاء العملية.", reply_markup=get_main_keyboard())
        return

    if "@" not in email_text or "." not in email_text:
        msg = bot.send_message(
            message.chat.id,
            "⚠️ البريد الإلكتروني غير صحيح! يرجى كتابة إيميل صالح (مثال: user@example.com) أو أرسل /cancel:",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_chatgpt_email, prod_id, price, product)
        return

    execute_purchase(
        chat_id=message.chat.id,
        user=message.from_user,
        product=product,
        prod_id=prod_id,
        price=price,
        email=email_text
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_buy_"))
def handle_confirm_purchase(call: types.CallbackQuery):
    """Executes the purchase after confirmation."""
    prod_id = call.data.replace("confirm_buy_", "")
    product = database.get_cached_product(prod_id) or api_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ تعذر جلب بيانات المنتج.", show_alert=True)
        return

    price = product.get("your_price") if product.get("your_price") is not None else product.get("store_price", 0.0)
    try:
        price_val = float(price)
    except Exception:
        price_val = 0.0

    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "⏳ <b>جارٍ معالجة طلبك وإرساله للمتجر... يُرجى الانتظار لحظات.</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
    except Exception:
        pass

    execute_purchase(
        chat_id=call.message.chat.id,
        user=call.from_user,
        product=product,
        prod_id=prod_id,
        price=price_val,
        message_id_to_edit=call.message.message_id
    )


def execute_purchase(chat_id: int, user: types.User, product: dict, prod_id: str, price: float, email: str = None, message_id_to_edit: int = None):
    """Core purchase logic: deducts balance (if configured), calls API, delivers product, notifies admin."""
    buyer_info = f"@{user.username}" if user.username else f"ID: {user.id} ({user.first_name})"
    raw_name = product.get("name_ar") or product.get("name_en") or "منتج"
    name = clean_html_tags(raw_name)
    is_manual = product.get("is_manual", False)

    # 1. Deduct user balance in MongoDB if required
    before_bal = database.get_user_balance(user.id)
    balance_deducted = False
    if config.REQUIRE_USER_BALANCE:
        if before_bal < price:
            fail_text = "⚠️ <b>رصيدك غير كافٍ لإتمام العملية!</b>"
            _send_or_edit(chat_id, fail_text, message_id_to_edit)
            return
        database.add_user_balance(user.id, -price)
        balance_deducted = True

    # 2. Call external Shop API POST /purchase
    resp = api_client.purchase(
        product_id=prod_id,
        qty=1,
        buyer_info=buyer_info,
        email=email
    )

    # Check for failure
    if not resp.get("success"):
        # Refund user balance if deducted
        if balance_deducted:
            database.add_user_balance(user.id, price)
            new_bal = database.get_user_balance(user.id)
            database.record_wallet_transaction(
                user_id=user.id,
                tx_type="refund",
                amount=price,
                balance_before=before_bal - price,
                balance_after=new_bal,
                payment_method="store1_refund",
                reference_id=prod_id,
                details=f"استرجاع قيمة: {name} (فشل الطلب لدى المزود)"
            )

        error_raw = resp.get("error", "Unknown error")
        if "Insufficient balance" in error_raw:
            error_ar = "⚠️ <b>تعذر إتمام الشراء:</b> رصيد حساب المتجر غير كافٍ حالياً. تم إشعار الإدارة لمعالجة الأمر."
        elif "Not enough stock" in error_raw:
            error_ar = "⚠️ <b>عذراً:</b> لقد نفد المخزون من هذا المنتج للتو!"
        else:
            error_ar = f"❌ <b>تعذر إتمام الطلب:</b> {html.escape(str(error_raw))}"

        _send_or_edit(chat_id, error_ar, message_id_to_edit)
        return

    # Purchase succeeded!
    order_id = resp.get("order_id", "API_ORD")
    total_price = resp.get("total_price", price)
    status = resp.get("status", "completed" if not is_manual else "pending_manual")
    codes = resp.get("codes", [])

    # Record wallet transaction
    if balance_deducted:
        new_bal = database.get_user_balance(user.id)
        database.record_wallet_transaction(
            user_id=user.id,
            tx_type="purchase_store1",
            amount=-total_price,
            balance_before=before_bal,
            balance_after=new_bal,
            payment_method="wallet",
            reference_id=order_id,
            details=f"شراء: {name}"
        )

    # Save order in MongoDB
    order_data = {
        "order_id": order_id,
        "user_id": user.id,
        "buyer_info": buyer_info,
        "product_id": prod_id,
        "product_name": name,
        "price": total_price,
        "status": status,
        "is_manual": is_manual,
        "codes": codes,
        "email": email,
        "raw_response": resp
    }
    database.save_order(order_data)

    # Delivery & User response
    if is_manual:
        # Manual delivery message
        user_success_text = (
            "✅ <b>تم استلام طلبك! سيتم التسليم خلال دقائق.</b>\n\n"
            f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n"
            f"📦 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
            f"💵 <b>السعر:</b> <code>${total_price:.2f}</code>\n"
            "🤝 <b>نوع التسليم:</b> يدوي\n\n"
            "⏳ يقوم فريق العمل بتجهيز طلبك حالياً، وسنرسل لك إشعاراً بالأكواد فور الانتهاء."
        )
    elif email:
        # ChatGPT invitation delivery
        user_success_text = (
            "🎉 <b>تم إرسال دعوة التفعيل بنجاح!</b>\n\n"
            f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n"
            f"📦 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
            f"📧 <b>البريد المُرسل إليه:</b> <code>{html.escape(email)}</code>\n\n"
            "📩 يرجى فحص صندوق الوارد (أو الرسائل غير المرغوب فيها) في بريدك الإلكتروني وقبول الدعوة."
        )
    else:
        # Instant code delivery
        codes_formatted = "\n".join([f"<code>{html.escape(c)}</code>" for c in codes]) if codes else "تم استلام الطلب."
        user_success_text = (
            "🎉 <b>تم الشراء بنجاح!</b>\n\n"
            f"📦 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
            f"💵 <b>السعر:</b> <code>${total_price:.2f}</code>\n"
            f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n\n"
            f"🔑 <b>بيانات الاستلام / الأكواد:</b>\n"
            f"{codes_formatted}\n\n"
            "شكراً لتسوقك معنا! نتشرف بخدمتك دائماً ❤️"
        )

    _send_or_edit(chat_id, user_success_text, message_id_to_edit)

    # Notify Admin (notify_admin)
    delivery_badge = "🤝 يدوي (يحتاج تسليمك)" if is_manual else "⚡ فوري (تم إرسال الكود)"
    admin_notif_text = (
        f"🔔 <b>طلب جديد في المتجر!</b>\n\n"
        f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n"
        f"👤 <b>العميل:</b> {html.escape(user.first_name or '')} ({buyer_info})\n"
        f"🆔 <b>معرف العميل (ID):</b> <code>{user.id}</code>\n"
        f"📦 <b>المنتج:</b> <b>{html.escape(name)}</b> (ID: <code>{prod_id}</code>)\n"
        f"💵 <b>السعر:</b> <code>${total_price:.2f}</code>\n"
        f"🚚 <b>نوع التسليم:</b> {delivery_badge}\n"
    )
    if email:
        admin_notif_text += f"📧 <b>إيميل العميل:</b> <code>{html.escape(email)}</code>\n"
    if codes and not is_manual:
        admin_notif_text += f"🔑 <b>الأكواد المسلّمة:</b>\n" + "\n".join([f"<code>{html.escape(c)}</code>" for c in codes])

    notify_admin(admin_notif_text)


def _send_or_edit(chat_id: int, text: str, message_id: int = None):
    """Helper to edit existing message or send a new one."""
    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")


# ==========================================
# Handlers: Store 2 (Elite) Purchase Flow
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("s2_buy_"))
def handle_elite_buy_button(call: types.CallbackQuery):
    """Initiates purchase confirmation for Store 2 product."""
    user = call.from_user
    prod_id = call.data.replace("s2_buy_", "")
    product = database.get_cached_elite_product(prod_id) or elite_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ المنتج غير متوفر.", show_alert=True)
        return

    stock = product.get("stock")
    if stock is not None and isinstance(stock, (int, float)) and stock <= 0:
        bot.answer_callback_query(call.id, "⚠️ عذراً، نفد المخزون من هذا المنتج!", show_alert=True)
        return

    price = product.get("price", 0.0)
    try:
        price_val = float(price)
    except Exception:
        price_val = 0.0

    if config.REQUIRE_USER_BALANCE:
        user_bal = database.get_user_balance(user.id)
        if user_bal < price_val:
            diff = price_val - user_bal
            insufficient_text = (
                f"⚠️ <b>رصيدك في المحفظة غير كافٍ لإتمام عملية الشراء!</b>\n\n"
                f"💰 <b>رصيدك الحالي:</b> <code>${user_bal:.2f}</code>\n"
                f"💵 <b>سعر المنتج:</b> <code>${price_val:.2f}</code>\n"
                f"🔻 <b>المبلغ المطلوب شحنه:</b> <code>${diff:.2f}</code>\n\n"
                f"👇 يمكنك شحن محفظتك فوراً واختيار وسيلة الدفع المناسبة:"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("💳 شحن الرصيد الآن", callback_data="wallet_deposit"),
                types.InlineKeyboardButton("🔙 العودة للمنتج", callback_data=f"s2_p_{prod_id}")
            )
            try:
                bot.edit_message_text(insufficient_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot.send_message(call.message.chat.id, insufficient_text, reply_markup=markup, parse_mode="HTML")
            bot.answer_callback_query(call.id)
            return

    raw_name = product.get("name") or "منتج إيليت"
    name = clean_html_tags(raw_name)

    confirm_text = (
        f"🧾 <b>تأكيد عملية الشراء (المتجر 2):</b>\n\n"
        f"💎 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
        f"💵 <b>المبلغ الإجمالي:</b> <code>${price_val:.2f}</code>\n"
        f"⚡ <b>نوع التسليم:</b> فوري لبيانات وحساب جاهز\n\n"
        f"هل أنت متأكد من رغبتك في إتمام الشراء الآن؟"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f"s2_confirm_buy_{prod_id}"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data=f"s2_p_{prod_id}")
    )

    try:
        bot.edit_message_text(
            confirm_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(call.message.chat.id, confirm_text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("s2_confirm_buy_"))
def handle_elite_confirm_purchase(call: types.CallbackQuery):
    """Executes Store 2 purchase after confirmation."""
    prod_id = call.data.replace("s2_confirm_buy_", "")
    product = database.get_cached_elite_product(prod_id) or elite_client.get_product(prod_id)

    if not product:
        bot.answer_callback_query(call.id, "⚠️ تعذر جلب بيانات المنتج.", show_alert=True)
        return

    price = product.get("price", 0.0)
    try:
        price_val = float(price)
    except Exception:
        price_val = 0.0

    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "⏳ <b>جارٍ معالجة طلبك مع متجر النخبة (Elite)... يُرجى الانتظار لحظات.</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
    except Exception:
        pass

    execute_elite_purchase(
        chat_id=call.message.chat.id,
        user=call.from_user,
        product=product,
        prod_id=prod_id,
        price=price_val,
        message_id_to_edit=call.message.message_id
    )


def execute_elite_purchase(chat_id: int, user: types.User, product: dict, prod_id: str, price: float, message_id_to_edit: int = None):
    """Core purchase logic for Store 2 (Elite Tools API)."""
    buyer_info = f"@{user.username}" if user.username else f"ID: {user.id} ({user.first_name})"
    raw_name = product.get("name") or "منتج إيليت"
    name = clean_html_tags(raw_name)

    # 1. Deduct user balance in MongoDB if required
    before_bal = database.get_user_balance(user.id)
    balance_deducted = False
    if config.REQUIRE_USER_BALANCE:
        if before_bal < price:
            fail_text = "⚠️ <b>رصيدك غير كافٍ لإتمام العملية!</b>"
            _send_or_edit(chat_id, fail_text, message_id_to_edit)
            return
        database.add_user_balance(user.id, -price)
        balance_deducted = True

    # 2. Call Elite API POST /order
    resp = elite_client.purchase(product_id=prod_id, quantity=1)

    if not resp.get("ok"):
        # Refund user balance if deducted
        if balance_deducted:
            database.add_user_balance(user.id, price)
            new_bal = database.get_user_balance(user.id)
            database.record_wallet_transaction(
                user_id=user.id,
                tx_type="refund",
                amount=price,
                balance_before=before_bal - price,
                balance_after=new_bal,
                payment_method="elite_refund",
                reference_id=prod_id,
                details=f"استرجاع قيمة: {name} (فشل الطلب لدى المزود)"
            )

        error_raw = resp.get("error", "Unknown error")
        if "balance" in str(error_raw).lower() or resp.get("http_status") == 402:
            error_ar = "⚠️ <b>تعذر إتمام الشراء:</b> رصيد حساب المتجر غير كافٍ لدى المزود حالياً. تم إشعار الإدارة."
        elif "stock" in str(error_raw).lower() or resp.get("http_status") == 409:
            error_ar = "⚠️ <b>عذراً:</b> لقد نفد المخزون من هذا المنتج للتو!"
        else:
            error_ar = f"❌ <b>تعذر إتمام الطلب:</b> {html.escape(str(error_raw))}"

        _send_or_edit(chat_id, error_ar, message_id_to_edit)
        return

    # Purchase succeeded!
    order_id = resp.get("orderId", f"ELITE_{prod_id[:8]}")
    charged = resp.get("charged", price)
    try:
        charged_val = float(charged)
    except Exception:
        charged_val = price
    items = resp.get("items", [])

    # Record wallet transaction
    if balance_deducted:
        new_bal = database.get_user_balance(user.id)
        database.record_wallet_transaction(
            user_id=user.id,
            tx_type="purchase_store2",
            amount=-charged_val,
            balance_before=before_bal,
            balance_after=new_bal,
            payment_method="wallet",
            reference_id=order_id,
            details=f"شراء: {name}"
        )

    # Save order in MongoDB
    order_data = {
        "order_id": order_id,
        "store": "elite",
        "user_id": user.id,
        "buyer_info": buyer_info,
        "product_id": prod_id,
        "product_name": name,
        "price": charged_val,
        "status": "completed",
        "is_manual": False,
        "codes": items,
        "raw_response": resp
    }
    database.save_order(order_data)

    # Format delivered items inside code blocks
    items_formatted = "\n\n".join([f"<code>{html.escape(str(item))}</code>" for item in items]) if items else "تم تسليم الطلب بنجاح."

    user_success_text = (
        "🎉 <b>تم الشراء بنجاح من المتجر 2!</b>\n\n"
        f"💎 <b>المنتج:</b> <b>{html.escape(name)}</b>\n"
        f"💵 <b>السعر:</b> <code>${charged_val:.2f}</code>\n"
        f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n\n"
        f"🔑 <b>بيانات الحساب / الاستلام:</b>\n"
        f"{items_formatted}\n\n"
        "شكراً لتسوقك معنا! نتشرف بخدمتك دائماً ❤️"
    )
    _send_or_edit(chat_id, user_success_text, message_id_to_edit)

    # Notify Admin
    admin_notif_text = (
        f"🔔 <b>طلب جديد في المتجر 2 (Elite)!</b>\n\n"
        f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n"
        f"👤 <b>العميل:</b> {html.escape(user.first_name or '')} ({buyer_info})\n"
        f"🆔 <b>معرف العميل (ID):</b> <code>{user.id}</code>\n"
        f"💎 <b>المنتج:</b> <b>{html.escape(name)}</b> (ID: <code>{prod_id}</code>)\n"
        f"💵 <b>السعر:</b> <code>${charged_val:.2f}</code>\n"
        f"⚡ <b>نوع التسليم:</b> فوري\n"
    )
    if items:
        admin_notif_text += f"\n🔑 <b>البيانات المسلّمة:</b>\n" + "\n".join([f"<code>{html.escape(str(i))}</code>" for i in items])

    notify_admin(admin_notif_text)


# ==========================================
# Admin Commands (/sync, /orders, /set_conn, etc.)
# ==========================================

@bot.message_handler(commands=["sync"])
def handle_admin_sync(message: types.Message):
    """Admin command /sync: checks stock changes from /changes and updates local DB."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ هذا الأمر مخصص لإدارة البوت فقط.")
        return

    wait_msg = bot.reply_to(message, "🔄 <i>جارٍ فحص التغييرات ومزامنة المخزون...</i>", parse_mode="HTML")

    sync_result = sync_worker.sync_changes(notify_callback=notify_order_completed)

    if not sync_result.get("success"):
        bot.edit_message_text(
            f"❌ <b>فشلت المزامنة:</b> {html.escape(str(sync_result.get('error')))}",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML"
        )
        return

    events_count = sync_result.get("events_count", 0)
    updated_stocks = sync_result.get("updated_stocks", 0)
    completed_orders = sync_result.get("completed_orders", 0)
    cursor = sync_result.get("cursor", 0)

    summary_text = (
        "✅ <b>تمت المزامنة بنجاح!</b>\n\n"
        f"📊 <b>الأحداث الجديدة:</b> <code>{events_count}</code>\n"
        f"📦 <b>المخزون المحدّث:</b> <code>{updated_stocks}</code> منتج\n"
        f"🤝 <b>طلبات يدوية مكتملة:</b> <code>{completed_orders}</code>\n"
        f"⏱️ <b>مؤشر المزامنة (Cursor):</b> <code>{cursor}</code>"
    )
    bot.edit_message_text(
        summary_text,
        chat_id=message.chat.id,
        message_id=wait_msg.message_id,
        parse_mode="HTML"
    )


@bot.message_handler(commands=["api_balance"])
def handle_admin_api_balance(message: types.Message):
    """Admin command to check store API balance for both Store 1 and Store 2."""
    if not is_admin(message.from_user.id):
        return
    res1 = api_client.get_balance()
    bal1 = f"${res1.get('balance', 0.0):.2f}" if res1.get("success") else f"❌ {res1.get('error')}"

    res2 = elite_client.get_balance()
    bal2 = f"${res2.get('balance', 0.0):.2f}" if res2.get("ok") else f"❌ {res2.get('error')}"

    text = (
        "💳 <b>أرصدة المتاجر الخارجية (لوحة الأدمن):</b>\n\n"
        f"🏬 <b>المتجر 1 (الرئيسي):</b> <code>{bal1}</code>\n"
        f"💎 <b>المتجر 2 (Elite Tools):</b> <code>{bal2}</code>"
    )
    bot.reply_to(message, text, parse_mode="HTML")


@bot.message_handler(commands=["orders"])
def handle_admin_orders(message: types.Message):
    """Admin command to view last orders."""
    if not is_admin(message.from_user.id):
        return

    orders = database.get_all_orders(limit=10)
    if not orders:
        bot.reply_to(message, "📭 لا توجد طلبات مسجلة في قاعدة البيانات حالياً.")
        return

    lines = ["📋 <b>آخر الطلبات المسجلة في المتجر:</b>\n"]
    for o in orders:
        oid = o.get("order_id", "N/A")
        store = o.get("store", "store1")
        store_icon = "💎" if store == "elite" else "🏬"
        name = o.get("product_name", "منتج")
        price = o.get("price", 0)
        status = o.get("status", "N/A")
        buyer = o.get("buyer_info", "مشتري")
        status_icon = "✅" if status == "completed" else "⏳"
        lines.append(f"{status_icon} {store_icon} <code>{oid}</code> | {html.escape(name)} | ${price} | {buyer}")

    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["set_conn"])
def handle_admin_set_conn(message: types.Message):
    """Admin command /set_conn <connection_code>: updates connection code in MongoDB."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "⚠️ <b>طريقة الاستخدام:</b>\n<code>/set_conn conn_eyJr...</code>",
            parse_mode="HTML"
        )
        return

    new_code = parts[1].strip()
    success = database.set_connection_code(new_code)
    if success:
        bot.reply_to(message, "✅ <b>تم حفظ وتحديث كود الاتصال في MongoDB بنجاح!</b>", parse_mode="HTML")
    else:
        bot.reply_to(message, "❌ <b>كود الاتصال غير صالح!</b> تأكد من صحة التشفير Base64 والمفاتيح.", parse_mode="HTML")


@bot.message_handler(commands=["set_elite_key"])
def handle_admin_set_elite_key(message: types.Message):
    """Admin command /set_elite_key <key>: updates Store 2 API key in MongoDB."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "⚠️ <b>طريقة الاستخدام:</b>\n<code>/set_elite_key ETS-xxxxxxxxxxxx</code>",
            parse_mode="HTML"
        )
        return

    new_key = parts[1].strip()
    if database.set_elite_api_key(new_key):
        bot.reply_to(message, "✅ <b>تم حفظ وتحديث مفتاح API لمتجر Elite بنجاح!</b>", parse_mode="HTML")
    else:
        bot.reply_to(message, "❌ المفتاح غير صالح.")


@bot.message_handler(commands=["add_balance"])
def handle_admin_add_balance(message: types.Message):
    """Admin command /add_balance <user_id> <amount>: adds funds to user's MongoDB balance."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(
            message,
            "⚠️ <b>طريقة الاستخدام:</b>\n<code>/add_balance USER_ID AMOUNT</code>\n<i>مثال:</i> <code>/add_balance 123456789 10</code>",
            parse_mode="HTML"
        )
        return

    try:
        target_user = int(parts[1])
        amount = float(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ المعرف أو المبلغ غير صالح.")
        return

    new_bal = database.add_user_balance(target_user, amount)
    bot.reply_to(
        message,
        f"✅ <b>تم تعديل الرصيد بنجاح!</b>\nالمستخدم: <code>{target_user}</code>\nالمبلغ المضاف: <code>${amount:.2f}</code>\nالرصيد الجديد: <code>${new_bal:.2f}</code>",
        parse_mode="HTML"
    )


@bot.message_handler(commands=["wallet"])
def handle_admin_check_wallet(message: types.Message):
    """Admin command /wallet <user_id>: views a user's wallet info and recent history."""
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ <b>طريقة الاستخدام:</b>\n<code>/wallet USER_ID</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ معرف المستخدم غير صالح.")
        return

    bal = database.get_user_balance(target_uid)
    tot_dep = database.get_user_total_deposited(target_uid)
    tot_spent = database.get_user_total_spent(target_uid)
    txs = database.get_user_wallet_history(target_uid, limit=5)

    lines = [
        f"💳 <b>بيانات محفظة المستخدم <code>{target_uid}</code>:</b>\n",
        f"💰 <b>الرصيد الحالي:</b> <code>${bal:.2f}</code>",
        f"➕ <b>إجمالي الإيداعات:</b> <code>${tot_dep:.2f}</code>",
        f"➖ <b>إجمالي المشتريات:</b> <code>${tot_spent:.2f}</code>\n",
        "📜 <b>آخر الحركات:</b>"
    ]
    if not txs:
        lines.append("<i>لا توجد حركات مسجلة.</i>")
    else:
        for t in txs:
            lines.append(f"• {t.get('type')}: ${t.get('amount')} (الرصيد بعد: ${t.get('balance_after')})")

    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["admin", "help"])
def handle_admin_help(message: types.Message):
    """Shows admin help commands if sender is admin, or basic help otherwise."""
    if is_admin(message.from_user.id):
        text = (
            "🛠️ <b>لوحة تحكم إدارة البوت:</b>\n\n"
            "• /sync — مزامنة التغييرات والمخزون من الـ API (المتجر 1)\n"
            "• /api_balance — فحص رصيد حسابات المتاجر الخارجية\n"
            "• /orders — عرض آخر الطلبات المسجلة لكافة المتاجر\n"
            "• /wallet [ID] — عرض رصيد وسجل حركات مستخدم\n"
            "• /add_balance [ID] [AMOUNT] — شحن رصيد لمستخدم في المتجر\n"
            "• /set_conn [CODE] — تحديث كود الاتصال للمتجر 1\n"
            "• /set_elite_key [KEY] — تحديث مفتاح API لمتجر 2 (Elite)\n"
            "• /start — القائمة الرئيسية للعملاء\n"
        )
    else:
        text = (
            "👋 <b>مرحباً بك في المتجر!</b>\n\n"
            "استخدم الأزرار للتنقل:\n"
            "• 🏪 <b>المتجر 1:</b> اشتراكات رقمية وخدمات متنوعة.\n"
            "• 💎 <b>المتجر 2:</b> أدوات وحسابات النخبة (Elite Tools).\n"
            "• 💰 <b>رصيدي:</b> لمعرفة رصيدك المسجل وطلباتك."
        )
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")


# ==========================================
# Main Startup
# ==========================================

def start_bot():
    """Initializes database, pre-warms cache, starts background sync worker and runs Telegram bot polling."""
    logger.info("Initializing Database...")
    db = database.get_db()

    # Pre-warm catalog caches for instant button responses
    try:
        logger.info("Pre-warming Store 1 products cache...")
        if not database.get_cached_products():
            api_client.get_products()
    except Exception as e:
        logger.warning(f"Could not pre-warm Store 1 cache: {e}")

    try:
        logger.info("Pre-warming Store 2 products cache...")
        if not database.get_cached_elite_products():
            elite_client.get_products()
    except Exception as e:
        logger.warning(f"Could not pre-warm Store 2 cache: {e}")

    # Start background polling worker if enabled
    if config.POLL_INTERVAL_SECONDS > 0:
        sync_thread = sync_worker.BackgroundSyncThread(
            interval_seconds=config.POLL_INTERVAL_SECONDS,
            notify_callback=notify_order_completed
        )
        sync_thread.start()

    logger.info("Starting pyTelegramBotAPI Polling...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    if not config.BOT_TOKEN or config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n" + "=" * 60)
        print("⚠️ تنبيه: يرجى وضع BOT_TOKEN الخاص ببوتك في ملف .env للتشغيل!")
        print("يمكنك إنشاء بوت جديد من @BotFather في تيليجرام.")
        print("=" * 60 + "\n")
    start_bot()
