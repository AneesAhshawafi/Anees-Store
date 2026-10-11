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
import sync_worker


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
    """Builds the main /start reply keyboard with EXACTLY 2 buttons as requested."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_products = types.KeyboardButton("🛒 المنتجات")
    btn_balance = types.KeyboardButton("💰 رصيدي")
    markup.add(btn_products, btn_balance)
    return markup


def build_products_keyboard(products: list, page: int = 0):
    """Builds paginated inline keyboard for products catalog."""
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
    """Builds inline keyboard for product details."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    if is_in_stock:
        markup.add(types.InlineKeyboardButton("🛒 شراء الآن", callback_data=f"buy_{product_id}"))
    else:
        markup.add(types.InlineKeyboardButton("❌ غير متوفر حالياً", callback_data="noop"))
    markup.add(types.InlineKeyboardButton("🔙 العودة للمنتجات", callback_data="back_products"))
    return markup


# ==========================================
# Handlers: /start & Main Menu
# ==========================================

@bot.message_handler(commands=["start"])
def handle_start(message: types.Message):
    """Handles /start command: updates user in DB and shows main 2 buttons."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)

    welcome_text = (
        f"👋 <b>أهلاً بك يا {html.escape(user.first_name or 'عزيزي')} في متجر أنيس!</b>\n\n"
        "🛍️ يمكنك استعراض كافة المنتجات الرقمية والاشتراكات والشراء بكل سهولة وأمان.\n\n"
        "⚡ <b>تسليم فوري للأكواد</b> أو 🤝 <b>تسليم يدوي موثوق</b>.\n\n"
        "اختر ما يناسبك من القائمة أدناه للبدء:"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda msg: msg.text in ("🛒 المنتجات", "/products"))
def handle_products_menu(message: types.Message):
    """Handles '🛒 المنتجات': shows catalog instantly from cache."""
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
        "📦 <b>قائمة المنتجات والاشتراكات المتاحة:</b>\n\n"
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


@bot.message_handler(func=lambda msg: msg.text in ("💰 رصيدي", "/balance"))
def handle_balance_menu(message: types.Message):
    """Handles '💰 رصيدي': shows user wallet balance & orders info."""
    user = message.from_user
    database.upsert_user(user.id, user.username, user.first_name)

    balance = database.get_user_balance(user.id)
    orders = database.get_user_orders(user.id, limit=50)

    text = (
        f"👤 <b>معلومات الحساب:</b>\n\n"
        f"🏷️ <b>الاسم:</b> {html.escape(user.first_name or 'غير محدد')}\n"
        f"🆔 <b>معرف الحساب:</b> <code>{user.id}</code>\n"
        f"💰 <b>رصيدك في المتجر:</b> <code>${balance:.2f}</code>\n"
        f"📦 <b>إجمالي طلباتك:</b> <code>{len(orders)}</code>\n\n"
        "💬 <i>للشحن أو لأي استفسار، تواصل مع الدعم الفني والإدارة.</i>"
    )

    # If admin, append store API balance
    if is_admin(user.id):
        api_bal_info = api_client.get_balance()
        api_bal = api_bal_info.get("balance", "N/A")
        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🛠️ <b>بيانات الإدارة (لوحة الأدمن):</b>\n"
            f"🏬 <b>رصيد حساب الـ API:</b> <code>${api_bal}</code>\n"
            f"💡 للمزامنة استخدم الأمر: /sync"
        )

    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")


# ==========================================
# Callback Handlers: Navigation & Products
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data == "noop")
def handle_noop(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)


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
            insufficient_text = (
                f"⚠️ <b>رصيدك غير كافٍ لإتمام عملية الشراء!</b>\n\n"
                f"💰 <b>رصيدك الحالي:</b> <code>${user_bal:.2f}</code>\n"
                f"💵 <b>سعر المنتج:</b> <code>${price_val:.2f}</code>\n\n"
                f"يرجى شحن رصيدك عبر التواصل مع إدارة المتجر للمتابعة."
            )
            bot.send_message(call.message.chat.id, insufficient_text, parse_mode="HTML")
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
    balance_deducted = False
    if config.REQUIRE_USER_BALANCE:
        user_bal = database.get_user_balance(user.id)
        if user_bal < price:
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
    """Admin command to check store API balance."""
    if not is_admin(message.from_user.id):
        return
    res = api_client.get_balance()
    if res.get("success"):
        bal = res.get("balance", 0.0)
        bot.reply_to(message, f"🏬 <b>رصيدك في API المتجر:</b> <code>${bal:.2f}</code>", parse_mode="HTML")
    else:
        bot.reply_to(message, f"❌ خطأ: {res.get('error')}")


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
        name = o.get("product_name", "منتج")
        price = o.get("price", 0)
        status = o.get("status", "N/A")
        buyer = o.get("buyer_info", "مشتري")
        status_icon = "✅" if status == "completed" else "⏳"
        lines.append(f"{status_icon} <code>{oid}</code> | {html.escape(name)} | ${price} | {buyer}")

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


@bot.message_handler(commands=["admin", "help"])
def handle_admin_help(message: types.Message):
    """Shows admin help commands if sender is admin, or basic help otherwise."""
    if is_admin(message.from_user.id):
        text = (
            "🛠️ <b>لوحة تحكم إدارة البوت:</b>\n\n"
            "• /sync — مزامنة التغييرات والمخزون من الـ API\n"
            "• /api_balance — فحص رصيد حساب الـ API الخارجي\n"
            "• /orders — عرض آخر الطلبات المسجلة\n"
            "• /add_balance [ID] [AMOUNT] — شحن رصيد لمستخدم في المتجر\n"
            "• /set_conn [CODE] — تحديث كود الاتصال في MongoDB\n"
            "• /start — القائمة الرئيسية للعملاء\n"
        )
    else:
        text = (
            "👋 <b>مرحباً بك في المتجر!</b>\n\n"
            "استخدم الأزرار أدناه للتنقل:\n"
            "• 🛒 <b>المنتجات:</b> لاستعراض الباقات والاشتراكات المتاحة.\n"
            "• 💰 <b>رصيدي:</b> لمعرفة رصيدك المسجل وطلباتك."
        )
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")


# ==========================================
# Main Startup
# ==========================================

def start_bot():
    """Initializes database, starts background sync worker and runs Telegram bot polling."""
    logger.info("Initializing Database...")
    db = database.get_db()

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
