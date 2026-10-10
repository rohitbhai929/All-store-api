import io
import logging
import sqlite3
import re
import asyncio
import random
import string
import time
import uuid
import threading
import os
import shutil
import zipfile
import requests
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback
    ZoneInfo = None
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = 8932730901
DEFAULT_ADMIN_USERNAME = "Rohit_BhaiTOP"

# --- GROUP NOTIFICATION SETTINGS ---
GROUP_ID = -1003453424345
GROUP_LINK = "https://t.me/ALLSTORESHOPPROOFS"
GROUP_PROOF_PHOTO = "https://t.me/onlyPrivateChiz/18"
BUY_NOW_LINK = "https://t.me/AllStoreShopBot?start=ref_8932730901"
PAYMENT_PROOFS_LINK = "https://t.me/ALLSTORESHOPPROOFS"

# Bantibhaiya reseller API. A purchase uses the bot's product PID plus the
# plan's remote_duration (the exact duration/plan label accepted by the website).
API_URL = "https://bantibhaiya.to/api/reseller_v1.php"
# Set STORE_MASTER_KEY in the deployment environment if the upstream provider requires it.
MASTER_KEY = os.getenv("STORE_MASTER_KEY", "").strip()
# Prefer STORE_API_KEY environment variable; the fallback is the key supplied
# by the owner. It can also be changed from the bot's Admin → API Settings.
DEFAULT_API_KEY = os.getenv("STORE_API_KEY", "").strip()

DEFAULT_FAMPAY_UPI = "samiranbhuyan@fam"
DEFAULT_FAMPAY_API_KEY = "YOUR_FAMPAY_API_KEY_HERE"

OLD_DEFAULT_AI_API_KEY = ""
DEFAULT_AI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
AI_MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash"]
AI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
AI_MAX_HISTORY = 12

DB_PATH = os.getenv("DB_PATH", "store_bot.db").strip() or "store_bot.db"

logging.basicConfig(level=logging.INFO)

PROCESSING_PURCHASES = set()
DB_LOCK = threading.RLock()

# ============================================================
# BUTTON LABELS
# ============================================================
BTN_TEXT = {
    "btn_shop": "🛍️ Shop Now",
    "btn_upgrade": "🌟 Upgrade To Reseller",
    "btn_reseller_dashboard": "Reseller Dashboard",
    "btn_profile": "👤 My Profile",
    "btn_add_bal": "💰 Add Balance",
    "btn_history": "🧾 All Orders",
    "btn_refer": "🎁 Referral",
    "btn_spin": "🎰 Lucky Spin",
    "btn_how": "❓ How To Use",
    "btn_support": "🎧 Support",
    "btn_back": "🔙 Back",
}

SHOP_HEADER_TEXT = (
    "🎮 ━ CHOOSE YOUR GAME ━ 🎮\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "⭐ Available Products\n\n"
    "├ 💸 Premium Keys\n"
    "├ ⚡ Instant Delivery\n"
    "├ 🔒 Secure Payment\n"
    "└ 🏆 24/7 Support\n\n"
    "📦 Select a product below:"
)


def parse_duration_hours(duration_str):
    """Best-effort parse of a plan's duration label into hours, for sorting."""
    if not duration_str:
        return float('inf')
    s = str(duration_str).strip().lower()
    m = re.match(r'(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|days?|d|minutes?|mins?|m)\b', s)
    if not m:
        return float('inf')
    val = float(m.group(1))
    unit = m.group(2)
    if unit.startswith('day') or unit == 'd':
        return val * 24
    if unit.startswith('hour') or unit.startswith('hr') or unit == 'h':
        return val
    if unit.startswith('min') or unit == 'm':
        return val / 60
    return float('inf')


def escape_markdown(text):
    if not text:
        return ""
    return str(text).replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")


# ============================================================
# MARKDOWNV2 HELPERS
# ============================================================
MDV2_SPECIAL = set(r'_*[]()~`>#+-=|{}.!')


def md_escape(text):
    if text is None:
        return ""
    return "".join("\\" + c if c in MDV2_SPECIAL else c for c in str(text))


def fmt_amount(x):
    try:
        return md_escape(f"{x:.2f}")
    except Exception:
        return md_escape(str(x))


def fmt_inr_tag(x):
    try:
        val = float(x)
    except (TypeError, ValueError):
        return f"{x}₹"
    if val == int(val):
        return f"{int(val)}₹"
    return f"{val:.2f}₹"


MAINTENANCE_MSG = (
    "🛠 *UNDER MAINTENANCE* 🛠\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "The bot is temporarily offline for maintenance\\.\n"
    "Please check back shortly — thank you for your patience\\."
)

PAYMENT_MAINTENANCE_MSG = (
    "🛠 *Payment System Under Maintenance* 🛠\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "⏳ Please wait a while or contact the admin\\."
)

BANNED_MSG = (
    "🚫 *YOU ARE BANNED* 🚫\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "❌ Your account has been banned by the admin\\.\n"
    "📞 Contact support if you think this is a mistake\\."
)


def build_welcome_text(name, bal, uid):
    """Welcome message — medium length, clean & attractive (MarkdownV2)."""
    n = md_escape(name)
    return (
        "🔥🎮 𝐀𝐋𝐋 𝐒𝐓𝐎𝐑𝐄 𝐒𝐇𝐎𝐏 𝐁𝐎𝐓 🎮🔥\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome back, {n}\\! 🎉✨\n"
        "Hope you're having a great day\\! 😎\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 Your one\\-stop shop for premium digital keys\\!\n"
        "Fast delivery • Trusted service • Best prices 💯\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🛍️ *Shop Now* — Buy premium keys instantly\n"
        "👤 *My Profile* — Check your balance & stats\n"
        "💰 *Add Balance* — Top up your wallet\n"
        "🧾 *All Orders* — View your purchase history\n"
        "🎁 *Referral* — Earn ₹1 per friend\n"
        "🎰 *Lucky Spin* — Win free balance & coupons\n"
        "📢 *Check Proofs* — Check Proofs\n"
        "🎧 *Support* — We're here 24/7 for you\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Instant Delivery • 🔒 100% Secure • 🏆 Trusted\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇✨ Tap any option below to get started\\! ✨👇"
    )


def build_support_text():
    """Support screen (MarkdownV2)."""
    s_uname = md_escape(get_support_username())
    return (
        "📞 SUPPORT CENTER 🔥\n"
        "━━━━━━━━━━━━━━━━━━━━ \n"
        "🫂 Need help? Choose any support option below\\. 🔥\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        "❤️ Telegram — Faster & Direct Supports ⚡️\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        "🎫 Support Ticket — Report & Track Your Issue ✏️\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        "🎯 Quick Response ✅\n"
        "✨ Friendly Support ✅\n"
        "🌟 Fast Issue Resolution ✅\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        f"👑 Admin Contact: @{s_uname} ✅\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        "🚀 We're Always Ready To Help \n"
        " You\\!\\! 🛡"
    )


def build_balance_added_text(amount, new_balance):
    return (
        "✅ *Balance Added* ✅\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 *Amount Added:* ₹{fmt_amount(amount)}\n"
        f"💰 *New Balance:* ₹{fmt_amount(new_balance)}\n\n"
        "🛍️ Send /start to shop now"
    )


def build_manual_reseller_upgrade_text():
    return (
        "🌟 *Account Updated* 🌟\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ *You are a Reseller now*\n"
        "💎 *Enjoy your special discounted prices*\n"
        "🛍️ *Tap Shop Now to buy*"
    )


def build_manual_reseller_removed_text():
    return (
        "⚠️ *Account Updated* ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "❌ *Your Reseller status has been removed*\n"
        "🛍️ *You will now see normal pricing*"
    )


def build_low_balance_qr_caption(item_name, plan_label, price, deficit):
    is_reseller = plan_label is None
    lines = [
        "📈 *Payment Qr Generated*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if is_reseller:
        lines.append(f"💡 *For:* {md_escape(item_name)}")
    else:
        lines.append(f"📦 *Product:* {md_escape(item_name)}")
        lines.append(f"⏱️ *Plan:* {md_escape(plan_label)}")
    lines.append(f"💵 *Price:* ₹{fmt_amount(price)}")
    lines.append(f"💰 *Need To Pay:* ₹{fmt_amount(deficit)}")
    lines.append("")
    if is_reseller:
        lines.append(f"💳 Pay exactly ₹{fmt_amount(deficit)} to get Reseller instantly")
    else:
        lines.append(f"💳 Pay exactly ₹{fmt_amount(deficit)} to receive your key instantly")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ============================================================
# BUTTON HELPERS
# ============================================================
def button(text, callback_data):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def url_button(text, url):
    return InlineKeyboardButton(text=text, url=url)


def back_button(callback_data="admin_back_panel", text="Back"):
    return button(f"🔙 {text}", callback_data)


def cancel_button_keyboard():
    return InlineKeyboardMarkup([[button("❌ Cancel", "admin_cancel_flow")]])


def zigzag_rows(buttons):
    """Lay buttons out two per row (never a lone single-button row unless the
    final row has an odd leftover button)."""
    rows = []
    for i in range(0, len(buttons), 2):
        chunk = buttons[i:i + 2]
        rows.append([button(lbl, cb) for lbl, cb in chunk])
    return rows


async def send_txt_file(bot, chat_id, text, filename, caption=None):
    """Send a plain-text string to a chat as a .txt document."""
    bio = io.BytesIO(text.encode("utf-8"))
    bio.name = filename
    await bot.send_document(chat_id=chat_id, document=bio, filename=filename, caption=caption)


# ============================================================
# DATABASE
# ============================================================
_DB_CONN = None


def get_db_conn():
    global _DB_CONN
    if _DB_CONN is None:
        _DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
        try:
            _DB_CONN.execute("PRAGMA journal_mode=WAL")
            _DB_CONN.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
    return _DB_CONN


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0.0,
            is_reseller INTEGER DEFAULT 0,
            total_deposit REAL DEFAULT 0.0,
            referred_by INTEGER DEFAULT 0,
            has_purchased INTEGER DEFAULT 0,
            last_spin_time INTEGER DEFAULT 0,
            lang TEXT DEFAULT 'en'
        )
    """)
    for col_sql in [
        "ALTER TABLE users ADD COLUMN phone_number TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''",
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except Exception:
            pass

    # ===== admins table (broadcast-only admins) =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            pid TEXT PRIMARY KEY,
            name TEXT
        )
    """)
    for col_sql in [
        "ALTER TABLE products ADD COLUMN is_maintenance INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN group_link TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN demo_video TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN apk_files TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN apk_links TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN apk_video TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN sort_order INTEGER DEFAULT 0",
        # ===== NEW: per-product offer =====
        "ALTER TABLE products ADD COLUMN offer_percent REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN offer_target TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN variant_id TEXT DEFAULT ''",
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except Exception:
            pass

    # Safe one-time mapping for the exact Z-REX NonRoot product shown in the
    # supplied Bantibhaiya catalog (variant ID 83). Do not guess mappings for others.
    cursor.execute(
        "UPDATE products SET variant_id = '83' WHERE COALESCE(TRIM(variant_id), '') = '' "
        "AND (LOWER(name) LIKE '%z rex%' OR LOWER(name) LIKE '%z-rex%') "
        "AND (LOWER(name) LIKE '%nonroot%' OR LOWER(name) LIKE '%non root%')"
    )

    # ===== NEW: custom users list for a product offer =====
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_offer_users (
            pid TEXT,
            user_id INTEGER,
            PRIMARY KEY (pid, user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_pid TEXT,
            duration TEXT,
            remote_duration TEXT,
            price REAL DEFAULT 0.0,
            reseller_price REAL DEFAULT 0.0
        )
    """)
    for col_sql in [
        "ALTER TABLE plans ADD COLUMN variant_id TEXT DEFAULT ''",
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except Exception:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_pid TEXT,
            duration TEXT,
            key_value TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_name TEXT,
            key_value TEXT,
            price REAL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Safe, additive migrations for purchase traceability.
    for col_sql in [
        "ALTER TABLE history ADD COLUMN order_id TEXT DEFAULT ''",
        "ALTER TABLE history ADD COLUMN purchase_method TEXT DEFAULT 'Manual Balance'",
    ]:
        try:
            cursor.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manual_stock_alert_state (
            product_pid TEXT NOT NULL,
            duration TEXT NOT NULL,
            alerted INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (product_pid, duration)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            status TEXT,
            plan_id INTEGER,
            purpose TEXT DEFAULT 'balance'
        )
    """)
    for col_sql in [
        "ALTER TABLE payments ADD COLUMN provider TEXT DEFAULT 'fampay'",
        "ALTER TABLE payments ADD COLUMN date TIMESTAMP",
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except Exception:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS custom_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_id INTEGER,
            price REAL,
            UNIQUE(user_id, plan_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('reseller_upgrade_price', '449')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('how_to_use_video', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('fampay_upi', ?)", (DEFAULT_FAMPAY_UPI,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('fampay_api_key', ?)", (DEFAULT_FAMPAY_API_KEY,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('store_api_key', ?)", (DEFAULT_API_KEY,))
    # If a previous build saved a KeyHive Bearer token, replace that known
    # incompatible token with this endpoint's configured default. Preserve
    # other admin-configured keys (including the 32-character Bantibhaiya key).
    cursor.execute("SELECT value FROM settings WHERE key = 'store_api_key'")
    _stored_api_key = str((cursor.fetchone() or [''])[0] or '').strip()
    if _stored_api_key.lower().startswith('bkey_'):
        cursor.execute("UPDATE settings SET value = ? WHERE key = 'store_api_key'", (DEFAULT_API_KEY,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('support_username', ?)", (DEFAULT_ADMIN_USERNAME,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('payment_provider', 'fampay')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('paytm_upi', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('paytm_merchant_id', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('paytm_merchant_key', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_maintenance', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('payment_maintenance', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('reseller_enabled', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('all_reseller_price', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('global_offer_percent', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ai_api_key', ?)", (DEFAULT_AI_API_KEY,))
    cursor.execute(
        "UPDATE settings SET value = ? WHERE key = 'ai_api_key' AND (value = ? OR value = '')",
        (DEFAULT_AI_API_KEY, OLD_DEFAULT_AI_API_KEY)
    )

    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_balance_min', '0.1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_balance_max', '2')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_coupon_min', '2')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_coupon_max', '10')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_balance_chance', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_coupon_chance', '2')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('spin_coupon_validity_hours', '24')")
    cursor.execute("SELECT 1 FROM settings WHERE key = 'spin_odds_v2'")
    if not cursor.fetchone():
        cursor.execute("UPDATE settings SET value = '1' WHERE key = 'spin_balance_chance'")
        cursor.execute("UPDATE settings SET value = '2' WHERE key = 'spin_coupon_chance'")
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('spin_odds_v2', '1')")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            discount_percent REAL,
            max_users INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            source TEXT DEFAULT 'admin',
            owner_user_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coupon_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            user_id INTEGER,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_history_date ON history(date)",
        "CREATE INDEX IF NOT EXISTS idx_plans_product ON plans(product_pid)",
        "CREATE INDEX IF NOT EXISTS idx_keys_product_dur ON keys(product_pid, duration)",
        "CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_coupon_uses_code ON coupon_uses(code)",
        "CREATE INDEX IF NOT EXISTS idx_coupon_uses_user ON coupon_uses(user_id)",
    ]:
        try:
            cursor.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS & API
# ============================================================
def get_user(user_id, username=None, full_name=None, referrer_id=0):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, balance, is_reseller, total_deposit, referred_by, has_purchased, last_spin_time, lang, phone_number, is_verified, is_banned, ban_reason FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        ref_by = referrer_id if (referrer_id and referrer_id != user_id) else 0
        cursor.execute("INSERT INTO users (user_id, username, full_name, referred_by, lang) VALUES (?, ?, ?, ?, 'en')", (user_id, username, full_name, ref_by))
        conn.commit()
        user = (user_id, username, full_name, 0.0, 0, 0.0, ref_by, 0, 0, 'en', '', 0, 0, '')
    else:
        updated_uname = username if username is not None else (user[1] if len(user) > 1 and user[1] is not None else "")
        updated_fname = full_name if full_name is not None else (user[2] if len(user) > 2 and user[2] is not None else "")
        if updated_uname != user[1] or updated_fname != user[2]:
            cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (updated_uname, updated_fname, user_id))
            conn.commit()

        u_bal = user[3] if len(user) > 3 and user[3] is not None else 0.0
        u_reseller = user[4] if len(user) > 4 and user[4] is not None else 0
        u_deposit = user[5] if len(user) > 5 and user[5] is not None else 0.0
        u_ref = user[6] if len(user) > 6 and user[6] is not None else 0
        u_purchased = user[7] if len(user) > 7 and user[7] is not None else 0
        u_spin = user[8] if len(user) > 8 and user[8] is not None else 0
        u_lang = user[9] if len(user) > 9 and user[9] is not None else 'en'
        u_phone = user[10] if len(user) > 10 and user[10] is not None else ''
        u_verified = user[11] if len(user) > 11 and user[11] is not None else 0
        u_banned = user[12] if len(user) > 12 and user[12] is not None else 0
        u_ban_reason = user[13] if len(user) > 13 and user[13] is not None else ''
        user = (user[0], updated_uname, updated_fname, u_bal, u_reseller, u_deposit, u_ref, u_purchased, u_spin, u_lang, u_phone, u_verified, u_banned, u_ban_reason)
    return user


def is_user_verified(user_row):
    return bool(user_row and len(user_row) > 11 and user_row[11])


def is_user_banned(user_row):
    return bool(user_row and len(user_row) > 12 and user_row[12])


def update_balance(user_id, amount, count_as_deposit=False):
    conn = get_db_conn()
    cursor = conn.cursor()
    if count_as_deposit and amount > 0:
        cursor.execute("UPDATE users SET balance = balance + ?, total_deposit = total_deposit + ? WHERE user_id = ?", (amount, amount, user_id))
    else:
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()


def get_setting(key, default=""):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    res = cursor.fetchone()
    return res[0] if res is not None else str(default)


def set_setting(key, value):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def get_reseller_price():
    try:
        return float(get_setting('reseller_upgrade_price', '449'))
    except (TypeError, ValueError):
        return 449.0


def get_upi():
    return (get_setting('fampay_upi', '') or '').strip()


def get_fampay_api_key():
    return (get_setting('fampay_api_key', '') or '').strip()


def get_store_api_key():
    # An explicitly saved admin setting wins; an empty setting falls back to
    # the configured environment/default key so fresh and migrated DBs work.
    return (get_setting('store_api_key', '') or DEFAULT_API_KEY or '').strip()


def get_ai_api_key():
    return (get_setting('ai_api_key', DEFAULT_AI_API_KEY) or '').strip()


def get_support_username():
    return (get_setting('support_username', DEFAULT_ADMIN_USERNAME) or '').strip()


def is_bot_maintenance():
    return (get_setting('bot_maintenance', '0') or '0').strip() == '1'


def set_bot_maintenance(flag_on: bool):
    set_setting('bot_maintenance', '1' if flag_on else '0')


def is_payment_maintenance():
    return (get_setting('payment_maintenance', '0') or '0').strip() == '1'


def set_payment_maintenance(flag_on: bool):
    set_setting('payment_maintenance', '1' if flag_on else '0')


def is_reseller_enabled():
    return (get_setting('reseller_enabled', '1') or '1').strip() == '1'


def set_reseller_enabled(flag_on: bool):
    set_setting('reseller_enabled', '1' if flag_on else '0')


def is_all_reseller_price():
    return (get_setting('all_reseller_price', '0') or '0').strip() == '1'


def set_all_reseller_price(flag_on: bool):
    set_setting('all_reseller_price', '1' if flag_on else '0')


def get_global_offer_percent():
    try:
        val = float(get_setting('global_offer_percent', '0') or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val < 0:
        val = 0.0
    if val > 100:
        val = 100.0
    return val


def set_global_offer_percent(pct):
    set_setting('global_offer_percent', str(pct))


# ============================================================
# PER-PRODUCT OFFER (NEW)
# target: 'reseller' | 'users' | 'custom' | 'all'
# ============================================================
OFFER_TARGET_LABELS = {
    "reseller": "👑 Reseller",
    "users": "👤 Users (Non-Reseller)",
    "custom": "🎯 Custom Users",
    "all": "🌍 All Users",
}


def fmt_pct(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")


def get_product_offer(pid):
    """Returns (percent, target) for a product. (0, '') if no offer."""
    if not pid:
        return 0.0, ""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT offer_percent, offer_target FROM products WHERE pid = ?", (pid,))
    row = cursor.fetchone()
    if not row:
        return 0.0, ""
    try:
        pct = float(row[0] or 0)
    except (TypeError, ValueError):
        pct = 0.0
    target = (row[1] or "").strip()
    if pct <= 0 or target not in OFFER_TARGET_LABELS:
        return 0.0, ""
    return min(pct, 100.0), target


def get_product_offer_custom_users(pid):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM product_offer_users WHERE pid = ? ORDER BY user_id", (pid,))
    return [r[0] for r in cursor.fetchall()]


def get_product_offer_percent_for_user(pid, user_id, is_reseller):
    """Offer % that applies to THIS user on THIS product (0 if not eligible)."""
    pct, target = get_product_offer(pid)
    if pct <= 0:
        return 0.0
    if target == "all":
        return pct
    if target == "reseller":
        return pct if is_reseller else 0.0
    if target == "users":
        return 0.0 if is_reseller else pct
    if target == "custom":
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM product_offer_users WHERE pid = ? AND user_id = ?", (pid, user_id))
        return pct if cursor.fetchone() else 0.0
    return 0.0


def save_product_offer(pid, pct, target, user_ids=None):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET offer_percent = ?, offer_target = ? WHERE pid = ?", (pct, target, pid))
    cursor.execute("DELETE FROM product_offer_users WHERE pid = ?", (pid,))
    if target == "custom" and user_ids:
        for uid in user_ids:
            cursor.execute("INSERT OR IGNORE INTO product_offer_users (pid, user_id) VALUES (?, ?)", (pid, uid))
    conn.commit()


def clear_product_offer(pid):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET offer_percent = 0, offer_target = '' WHERE pid = ?", (pid,))
    cursor.execute("DELETE FROM product_offer_users WHERE pid = ?", (pid,))
    conn.commit()


# ============================================================
# ADMIN MANAGEMENT HELPERS
# ============================================================
def is_main_admin(user_id):
    """Owner — full access."""
    return user_id == ADMIN_ID


def is_broadcast_admin(user_id):
    """Limited admin — broadcast only."""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None


def is_any_admin(user_id):
    """Owner OR broadcast admin."""
    return is_main_admin(user_id) or is_broadcast_admin(user_id)


def add_broadcast_admin(user_id, added_by=0):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by)
    )
    conn.commit()


def remove_broadcast_admin(user_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    return cursor.rowcount > 0


def get_all_broadcast_admins():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT admins.user_id, users.full_name, users.username
        FROM admins LEFT JOIN users ON admins.user_id = users.user_id
        ORDER BY admins.added_at
    """)
    return cursor.fetchall()


# ============================================================
# BAN MANAGEMENT HELPERS
# ============================================================
def ban_user(user_id, reason=""):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?", (reason, user_id))
    conn.commit()
    return cursor.rowcount > 0


def unban_user(user_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 0, ban_reason = '' WHERE user_id = ?", (user_id,))
    conn.commit()
    return cursor.rowcount > 0


def get_all_banned_users():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name, username, ban_reason FROM users WHERE is_banned = 1 ORDER BY user_id")
    return cursor.fetchall()


async def notify_user_banned(context, user_id, reason=""):
    try:
        text = (
            f"🚫 *YOU HAVE BEEN BANNED* 🚫\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"❌ Your account has been banned by the admin\\.\n"
        )
        if reason:
            text += f"📝 *Reason:* {md_escape(reason)}\n"
        text += (
            f"\n📞 If you think this is a mistake, contact support\\."
        )
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="MarkdownV2")
    except Exception as e:
        logging.error(f"Failed to notify user {user_id} of ban: {e}")


async def notify_user_unbanned(context, user_id):
    try:
        text = (
            f"✅ *YOU HAVE BEEN UNBANNED* ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎉 Your account has been unbanned\\.\n"
            f"🛍️ You can now use the bot again\\.\n\n"
            f"Send /start to begin shopping\\!"
        )
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="MarkdownV2")
    except Exception as e:
        logging.error(f"Failed to notify user {user_id} of unban: {e}")


# ============================================================
# LUCKY SPIN SETTINGS
# ============================================================
def get_spin_settings():
    return {
        'bal_min': float(get_setting('spin_balance_min', '0.1') or 0.1),
        'bal_max': float(get_setting('spin_balance_max', '2') or 2),
        'coup_min': float(get_setting('spin_coupon_min', '2') or 2),
        'coup_max': float(get_setting('spin_coupon_max', '10') or 10),
        'bal_chance': float(get_setting('spin_balance_chance', '1') or 1),
        'coup_chance': float(get_setting('spin_coupon_chance', '2') or 2),
        'coupon_validity_hours': float(get_setting('spin_coupon_validity_hours', '24') or 24),
    }


def set_spin_setting(key, value):
    set_setting(key, value)


# ============================================================
# COUPON SYSTEM
# ============================================================
def generate_coupon_code(prefix="WIN"):
    while True:
        code = prefix + "-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM coupons WHERE code = ?", (code,))
        if not cursor.fetchone():
            return code


def create_coupon(discount_percent, max_users, valid_hours, source='admin', owner_user_id=0, prefix="WIN"):
    code = generate_coupon_code(prefix)
    expires_at = (datetime.utcnow() + timedelta(hours=valid_hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO coupons (code, discount_percent, max_users, used_count, expires_at, source, owner_user_id) VALUES (?, ?, ?, 0, ?, ?, ?)",
        (code, discount_percent, max_users, expires_at, source, owner_user_id)
    )
    conn.commit()
    return code, expires_at


def get_coupon(code):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, discount_percent, max_users, used_count, expires_at, source, owner_user_id FROM coupons WHERE code = ?",
        (code.strip().upper(),)
    )
    return cursor.fetchone()


def validate_coupon_for_user(code, user_id):
    row = get_coupon(code)
    if not row:
        return False, "❌ Invalid coupon code."
    _, discount, max_users, used_count, expires_at, source, owner_uid = row
    try:
        exp_dt = datetime.strptime(str(expires_at)[:19], "%Y-%m-%d %H:%M:%S")
        if datetime.utcnow() > exp_dt:
            return False, "❌ This coupon has expired."
    except Exception:
        pass
    if used_count >= max_users:
        return False, "❌ This coupon has reached its max usage limit."
    if owner_uid and owner_uid != user_id:
        return False, "❌ This coupon is not valid for your account."
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM coupon_uses WHERE code = ? AND user_id = ?", (row[0], user_id))
    if cursor.fetchone():
        return False, "❌ You have already used this coupon."
    return True, row


def redeem_coupon_for_user(code, user_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO coupon_uses (code, user_id) VALUES (?, ?)", (code, user_id))
    cursor.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code,))
    conn.commit()


async def notify_admin_coupon_used(context, user_id, coupon_row):
    code, discount, max_users, used_count, expires_at, source, owner_uid = coupon_row
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, username FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    full_name = escape_markdown(u[0]) if u and u[0] else "N/A"
    username = f"@{escape_markdown(u[1])}" if u and u[1] else "N/A"
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🎟️ **COUPON USED** 🎟️\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Name:** {full_name}\n"
                f"🏷️ **Username:** {username}\n"
                f"🆔 **User ID:** `{user_id}`\n"
                f"🎫 **Coupon:** `{code}`\n"
                f"💯 **Coupon %:** {discount:.2f}%\n"
                f"⏳ **Expires:** {to_ist_str(expires_at)} (IST)\n"
                f"👥 **Max Users:** {used_count + 1}/{max_users}\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to notify admin of coupon use: {e}")


async def send_banned_notice(update: Update):
    if update.callback_query:
        try:
            await update.callback_query.answer("🚫 You are banned!", show_alert=True)
        except Exception:
            pass
        try:
            await update.callback_query.edit_message_text(BANNED_MSG, parse_mode="MarkdownV2")
            return
        except Exception:
            pass
        try:
            await update.callback_query.message.reply_text(BANNED_MSG, parse_mode="MarkdownV2")
        except Exception:
            pass
    elif update.effective_message:
        try:
            await update.effective_message.reply_text(BANNED_MSG, parse_mode="MarkdownV2")
        except Exception:
            pass


async def send_maintenance_notice(update: Update):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        try:
            await update.callback_query.edit_message_text(MAINTENANCE_MSG, parse_mode="MarkdownV2")
            return
        except Exception:
            pass
        try:
            await update.callback_query.message.reply_text(MAINTENANCE_MSG, parse_mode="MarkdownV2")
        except Exception:
            pass
    elif update.effective_message:
        try:
            await update.effective_message.reply_text(MAINTENANCE_MSG, parse_mode="MarkdownV2")
        except Exception:
            pass


IST_OFFSET = timedelta(hours=5, minutes=30)


def to_ist_str(utc_str):
    if not utc_str:
        return "N/A"
    try:
        dt = datetime.strptime(str(utc_str)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(utc_str)
    ist_dt = dt + IST_OFFSET
    return ist_dt.strftime("%d %b %Y, %I:%M %p")


def generate_ticket_id():
    conn = get_db_conn()
    cursor = conn.cursor()
    while True:
        tid = "TKT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        cursor.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (tid,))
        if not cursor.fetchone():
            return tid


def get_payment_provider():
    p = (get_setting('payment_provider', 'fampay') or 'fampay').strip().lower()
    return p if p in ("fampay", "paytm") else "fampay"


def set_payment_provider(provider):
    provider = (provider or "").strip().lower()
    if provider not in ("fampay", "paytm"):
        provider = "fampay"
    set_setting('payment_provider', provider)


def get_paytm_upi():
    return (get_setting('paytm_upi', '') or '').strip()


def get_paytm_merchant_id():
    return (get_setting('paytm_merchant_id', '') or '').strip()


def get_paytm_merchant_key():
    return (get_setting('paytm_merchant_key', '') or '').strip()


def mask_secret(s):
    if not s:
        return "Not Set"
    if len(s) <= 8:
        return s[:2] + "*****" + s[-1:] if len(s) > 1 else s
    return f"{s[:5]}*****{s[-3:]}"


def get_custom_price(user_id, plan_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM custom_prices WHERE user_id = ? AND plan_id = ?", (user_id, plan_id))
    row = cursor.fetchone()
    return row[0] if row is not None else None


def set_custom_price(user_id, plan_id, price):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO custom_prices (user_id, plan_id, price) VALUES (?, ?, ?)", (user_id, plan_id, price))
    conn.commit()


def get_effective_price(user_id, plan_id, normal_price, reseller_price, is_reseller, pid=None):
    # 1) Custom price for this exact user+plan always wins
    custom = get_custom_price(user_id, plan_id)
    if custom is not None:
        return custom

    # 2) Base price (normal / reseller)
    if is_all_reseller_price():
        base = reseller_price
    else:
        base = reseller_price if is_reseller else normal_price

    # 3) NEW: per-product offer (only for the group the admin selected)
    if pid is None:
        try:
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT product_pid FROM plans WHERE id = ?", (plan_id,))
            prow = cursor.fetchone()
            pid = prow[0] if prow else None
        except Exception:
            pid = None
    if pid:
        prod_offer = get_product_offer_percent_for_user(pid, user_id, bool(is_reseller))
        if prod_offer > 0:
            base = round(base * (1 - prod_offer / 100), 2)

    # 4) Global (all products) offer
    offer = get_global_offer_percent()
    if offer > 0:
        base = round(base * (1 - offer / 100), 2)
    return base


def is_low_balance_error(msg):
    if not msg:
        return False
    msg_l = str(msg).lower()
    keywords = ["insufficient", "low balance", "low_balance", "not enough balance",
                "add balance", "recharge", "out of stock", "no stock", "wallet balance"]
    return any(k in msg_l for k in keywords)


def _extract_api_key(payload):
    """Extract a delivered key from documented/common Bantibhaiya JSON response shapes."""
    if isinstance(payload, str):
        value = payload.strip()
        return value or None
    if isinstance(payload, list):
        for item in payload:
            found = _extract_api_key(item)
            if found:
                return found
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("key", "license_key", "license", "code", "token"):
        value = payload.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    for field in ("keys", "delivered_keys", "data", "result", "order", "items"):
        if field in payload:
            found = _extract_api_key(payload[field])
            if found:
                return found
    return None


def _call_reseller_api_sync(pid, remote_duration=None, variant_id=None):
    """Submit one Bantibhaiya purchase using the reference file's request shape.

    PID and the website's exact remote duration remain the primary identifiers.
    The optional plan variant is included only when configured for that plan.
    Purchases are not blindly retried because a timeout can occur after an order
    has already been created by the provider.
    """
    product_pid = str(pid or "").strip()
    duration = str(remote_duration or "").strip()
    active_api_key = get_store_api_key()
    if not active_api_key:
        return False, "Bantibhaiya API key is not configured. Set it in Admin → API Settings."
    if not product_pid:
        return False, "Product PID is missing. Set the provider PID on the product."
    if not duration:
        return False, "Remote duration is missing. Set the exact plan label used on the provider website."

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (compatible; ALL-STORE-SHOP-BOT/1.0)",
    }
    if MASTER_KEY:
        headers["x-master-key"] = MASTER_KEY

    # Match the request fields used by the reference implementation. Some
    # reseller API deployments parse product_id/qty/count or sku/plan aliases.
    payload = {
        "api_key": active_api_key,
        "action": "buy",
        "pid": product_pid,
        "product_id": product_pid,
        "quantity": 1,
        "qty": 1,
        "count": 1,
        "duration": duration,
        "sku": duration,
        "plan": duration,
    }
    configured_variant = str(variant_id or "").strip()
    if configured_variant:
        payload["variant_id"] = configured_variant
        payload["variant"] = configured_variant

    try:
        response = requests.post(API_URL, data=payload, headers=headers, timeout=25)
    except requests.RequestException as exc:
        return False, f"Bantibhaiya request failed; check the provider panel before retrying: {exc}"

    raw = (response.text or "").strip()
    try:
        result = response.json()
    except ValueError:
        result = None

    if not response.ok:
        detail = None
        if isinstance(result, dict):
            detail = result.get("msg") or result.get("message") or result.get("error") or result.get("detail")
        return False, f"Bantibhaiya HTTP {response.status_code}: {str(detail or raw or 'Request failed')[:500]}"

    if isinstance(result, dict):
        status = str(result.get("status", "")).strip().lower()
        success_flag = result.get("success")
        error_value = result.get("error")
        detail = result.get("msg") or result.get("message") or result.get("detail") or error_value
        if status in {"error", "failed", "failure", "false", "0"} or success_flag is False or error_value:
            return False, f"Bantibhaiya rejected the order: {str(detail or result)[:500]}"

        key = _extract_api_key(result)
        if key:
            return True, key

        if status in {"success", "ok", "completed", "complete", "1"} or success_flag is True:
            order_ref = result.get("order_id") or result.get("order") or detail
            if order_ref:
                return True, str(order_ref).strip()
        return False, f"Bantibhaiya returned no verifiable key/order result: {str(detail or result)[:500]}"

    if result is None and raw and not re.search(
        r"\b(error|failed|failure|invalid|insufficient|out of stock|not found|rejected)\b", raw, re.I
    ) and len(raw) >= 4:
        return True, raw[:2000]
    return False, f"Bantibhaiya returned an empty or unrecognized response: {(raw or 'No response body')[:500]}"


async def call_reseller_api(pid, remote_duration=None, variant_id=None):
    return await asyncio.to_thread(_call_reseller_api_sync, pid, remote_duration, variant_id)


async def process_referral_reward(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT referred_by, has_purchased FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        referred_by, has_purchased = row[0], row[1]
        if has_purchased == 0:
            cursor.execute("UPDATE users SET has_purchased = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            if referred_by and referred_by > 0 and referred_by != user_id:
                cursor.execute("UPDATE users SET balance = balance + 1.0 WHERE user_id = ?", (referred_by,))
                conn.commit()
                try:
                    await context.bot.send_message(
                        chat_id=referred_by,
                        text="🎉 **Referral Reward Credited!**\n\nA user you referred made their first purchase! **₹1.00** has been added to your balance.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.error(f"Failed to send referral reward alert: {e}")


def _display_purchase_method(purchase_type):
    raw = str(purchase_type or "").lower()
    if "api" in raw:
        return "API Purchase"
    if "key" in raw and "manual" in raw:
        return "Manual Key Purchase"
    return "Manual Balance Purchase"


def _now_ist():
    """Return current time in India Standard Time, independent of server timezone."""
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("Asia/Kolkata"))
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def _purchase_time_ist():
    return _now_ist().strftime("%d %b %Y, %I:%M %p IST")


def _new_order_id():
    return "ORD-" + (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()


async def send_purchase_admin_alert(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, prod_name: str,
    duration: str, key_delivered: str, cost: float, purchase_type: str,
    order_id: str = "", balance: float = None, status: str = "SUCCESS",
    failure_reason: str = ""
):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, username, balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    full_name = str(row[0] or "N/A") if row else "N/A"
    username = ("@" + str(row[1])) if row and row[1] else "N/A"
    remaining = balance if balance is not None else (row[2] if row and row[2] is not None else 0.0)
    method = _display_purchase_method(purchase_type)
    now = _purchase_time_ist()
    lines = [
        "🛒 ORDER SUCCESSFUL" if status == "SUCCESS" else "🚨 ORDER FAILED",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🧾 Order ID: {order_id or 'N/A'}",
        f"👤 Customer: {full_name}",
        f"🏷️ Username: {username}",
        f"🆔 User ID: {user_id}",
        f"📦 Product: {prod_name}",
    ]
    if duration and str(duration).strip():
        lines.append(f"⏱️ Plan: {duration}")
    lines += [
        f"🔄 Method: {method}",
        f"💵 Amount: ₹{float(cost):.2f}",
        f"💰 Remaining balance: ₹{float(remaining):.2f}",
        f"📌 Status: {'Delivered successfully' if status == 'SUCCESS' else 'Failed'}",
    ]
    if status == "SUCCESS" and key_delivered:
        # Admin-only delivery details; never included in group announcements.
        lines.append(f"🔑 Delivery: {str(key_delivered)[:800]}")
    if failure_reason:
        lines.append(f"⚠️ Reason: {str(failure_reason)[:700]}")
    lines += [f"🕒 Date: {now}", "━━━━━━━━━━━━━━━━━━━━"]
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text="\n".join(lines))
    except Exception:
        logging.exception("Failed to send purchase admin notification")


async def send_manual_stock_alert(
    context: ContextTypes.DEFAULT_TYPE, product_name: str, duration: str,
    remaining: int, product_pid: str = ""
):
    if remaining != 2:
        return
    conn = get_db_conn()
    # Claim the alert atomically so concurrent purchase tasks cannot duplicate it.
    with DB_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT alerted FROM manual_stock_alert_state WHERE product_pid = ? AND duration = ?",
                (str(product_pid), str(duration))
            ).fetchone()
            if row and int(row[0] or 0):
                conn.commit()
                return
            conn.execute("""
                INSERT INTO manual_stock_alert_state(product_pid, duration, alerted, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(product_pid, duration)
                DO UPDATE SET alerted = 1, updated_at = CURRENT_TIMESTAMP
            """, (str(product_pid), str(duration)))
            conn.commit()
        except Exception:
            conn.rollback()
            logging.exception("Could not claim manual low-stock alert")
            return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🚨 MANUAL STOCK LOW — RESTOCK NEEDED\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Product: {product_name}\n"
                f"🆔 Product ID: {product_pid or 'N/A'}\n"
                f"⏱️ Plan: {duration or 'N/A'}\n"
                f"🔑 Available keys: {remaining}\n"
                "📥 Please restock this manual product plan.\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        )
    except Exception:
        # Permit a later retry if Telegram delivery failed.
        with DB_LOCK:
            conn.execute(
                "UPDATE manual_stock_alert_state SET alerted = 0 WHERE product_pid = ? AND duration = ?",
                (str(product_pid), str(duration))
            )
            conn.commit()
        logging.exception("Failed to send manual stock alert")


def mask_name(name: str) -> str:
    """Public group-safe customer label; never reveal the full customer name."""
    if not name or not str(name).strip():
        return "***"
    clean = str(name).strip()
    # Preserve at most the first character; mask the rest for privacy.
    return clean[0] + "***"


def mask_key(key: str) -> str:
    if not key:
        return "Not shown"
    key = str(key).strip()
    return (key[:4] + "****") if len(key) > 4 else "****"


async def send_group_purchase_notification(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, prod_name: str,
    plan_duration: str, key_delivered: str, cost: float, purchase_type: str,
    order_id: str = "", status: str = "SUCCESS"
):
    try:
        cursor = get_db_conn().cursor()
        cursor.execute("SELECT full_name, username FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        buyer_name = str(row[0] or "Customer") if row else "Customer"
        # Public group notification intentionally excludes username, user ID, and key.
        method = _display_purchase_method(purchase_type)
        caption = (
            "🎉 NEW ORDER COMPLETED\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🧾 Order ID: {order_id or 'N/A'}\n"
            f"👤 Customer: {mask_name(buyer_name)}\n"
            f"📦 Product: {prod_name}\n"
        )
        if plan_duration and str(plan_duration).strip():
            caption += f"⏱️ Plan: {plan_duration}\n"
        caption += (
            f"🔄 Method: {method}\n"
            f"💵 Amount: ₹{float(cost):.2f}\n"
            f"📌 Status: {'Successful' if status == 'SUCCESS' else 'Failed'}\n"
            f"🕒 Date: {_purchase_time_ist()}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = InlineKeyboardMarkup([[url_button("🛒 Buy Now", BUY_NOW_LINK)]])
        try:
            await context.bot.send_photo(
                chat_id=GROUP_ID, photo=GROUP_PROOF_PHOTO, caption=caption,
                reply_markup=keyboard
            )
        except Exception as photo_err:
            logging.warning("Group proof photo could not be sent; using text: %s", photo_err)
            await context.bot.send_message(
                chat_id=GROUP_ID, text=caption + (f"\n\n{GROUP_LINK}" if GROUP_LINK else ""),
                reply_markup=keyboard, disable_web_page_preview=True
            )
    except Exception:
        logging.exception("Failed to send group purchase notification")


async def send_admin_low_balance_alert(context: ContextTypes.DEFAULT_TYPE, user_id: int, prod_name: str, duration: str, price: float, reason: str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, username, balance, is_reseller FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    full_name = escape_markdown(u_row[0]) if u_row and u_row[0] else "N/A"
    username = f"@{escape_markdown(u_row[1])}" if u_row and u_row[1] else "N/A"
    wallet_balance = u_row[2] if u_row and u_row[2] is not None else 0.0
    acc_type = "🌟 Reseller" if (u_row and u_row[3]) else "👤 User"
    safe_prod_name = escape_markdown(prod_name)
    safe_duration = escape_markdown(duration)
    safe_reason = escape_markdown(reason)
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⚠️ **LOW API BALANCE / ORDER FAILED** ⚠️\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Name:** {full_name}\n"
                f"🏷️ **Username:** {username}\n"
                f"🆔 **User ID:** `{user_id}`\n"
                f"💰 **Wallet Balance:** ₹{wallet_balance:.2f}\n"
                f"🏷️ **Account Type:** {acc_type}\n\n"
                f"📦 **Product:** {safe_prod_name}\n"
                f"⏱️ **Plan:** {safe_duration}\n"
                f"💵 **Price:** ₹{price:.2f}\n\n"
                f"❌ **Reason:** {safe_reason}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Please top up the reseller panel / restock, then ask the user to retry."
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to notify admin of low API balance: {e}")


async def deliver_product_apks(context: ContextTypes.DEFAULT_TYPE, user_id: int, pid: str, prod_name: str):
    if not pid:
        return
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT apk_files, apk_links, apk_video FROM products WHERE pid = ?", (pid,))
    row = cursor.fetchone()
    if not row:
        return

    apk_files_raw = row[0] if row[0] else ""
    apk_links_raw = row[1] if len(row) > 1 and row[1] else ""
    apk_video = row[2] if len(row) > 2 and row[2] else ""

    header_caption = f"📥 Download Apk From - {prod_name}"

    files = []
    for entry in apk_files_raw.split("\n"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|||", 1)
        file_id = parts[0]
        file_name = parts[1] if len(parts) > 1 else None
        if file_id:
            files.append((file_id, file_name))

    if files:
        try:
            if len(files) == 1:
                fid, fname = files[0]
                await context.bot.send_document(chat_id=user_id, document=fid, filename=fname, caption=header_caption)
            else:
                remaining = files
                first_batch = True
                while remaining:
                    chunk = remaining[:10]
                    remaining = remaining[10:]
                    media = []
                    for i, (fid, fname) in enumerate(chunk):
                        if i == 0 and first_batch:
                            media.append(InputMediaDocument(media=fid, filename=fname, caption=header_caption))
                        else:
                            media.append(InputMediaDocument(media=fid, filename=fname))
                    await context.bot.send_media_group(chat_id=user_id, media=media)
                    first_batch = False
        except Exception as e:
            logging.error(f"Failed to send apk file(s) to {user_id} for {pid}: {e}")

    links = [l.strip() for l in apk_links_raw.split("\n") if l.strip()]
    if links:
        # Keep each URL exactly as entered: no Markdown backticks or escaping.
        links_text = "📥 Download Apk From 📥\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, link in enumerate(links, 1):
            links_text += f"🔗 Link {i}:\n{link}\n\n"
        links_text += "━━━━━━━━━━━━━━━━━━━━"
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=links_text,
                disable_web_page_preview=True
            )
        except Exception as e:
            logging.error(f"Failed to send apk links to {user_id} for {pid}: {e}")

    if apk_video:
        try:
            await context.bot.send_video(chat_id=user_id, video=apk_video, caption=f"🎥 Video - {prod_name}")
        except Exception as e:
            logging.error(f"Failed to send apk video to {user_id} for {pid}: {e}")


async def finalize_purchase(context: ContextTypes.DEFAULT_TYPE, user_id: int, plan_id, purchase_type="Manual/Balance", coupon_discount=0):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT product_pid, duration, remote_duration, price, reseller_price, variant_id FROM plans WHERE id = ?", (plan_id,))
    plan = cursor.fetchone()
    if not plan:
        return False, "❌ This plan no longer exists.", "", None, None

    cursor.execute("SELECT name, group_link FROM products WHERE pid = ?", (plan[0],))
    prod_row = cursor.fetchone()
    prod_name = str(prod_row[0] if prod_row else "Unknown Product")
    group_link = (prod_row[1] if prod_row and len(prod_row) > 1 and prod_row[1] else "").strip()
    api_product_pid = str(plan[0] or "").strip()
    api_remote_duration = str(plan[2] or "").strip()

    cursor.execute("SELECT balance, is_reseller, full_name, username FROM users WHERE user_id = ?", (user_id,))
    bal_row = cursor.fetchone()
    if not bal_row:
        return False, "❌ User account not found. Please send /start and try again.", "", None, None
    balance = float(bal_row[0] or 0.0)
    is_reseller = int(bal_row[1] or 0)
    cost = get_effective_price(user_id, plan_id, plan[3], plan[4], is_reseller, plan[0])
    if coupon_discount:
        cost = round(cost * (1 - float(coupon_discount) / 100), 2)
    if cost < 0:
        return False, "❌ Invalid purchase price. Please contact support.", "", None, None
    if balance < cost:
        return False, (
            f"⚠️ *Balance Credited, But Not Enough Yet\\!*\n\n"
            f"📦 *Item:* {md_escape(prod_name)} \\({md_escape(plan[1])}\\)\n"
            f"💵 *Item Price:* ₹{fmt_amount(cost)}\n"
            f"💰 *Your Balance:* ₹{fmt_amount(balance)}\n\n"
            "Please add balance and try again."
        ), "", None, None

    # Prefer manual stock when available. Claim a key and debit the wallet in one
    # SQLite transaction; if there is no manual key, perform the external API call
    # without holding a database transaction open.
    key_delivered = None
    actual_mode = None
    manual_remaining = None
    order_id = _new_order_id()
    with DB_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            fresh = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
            fresh_balance = float(fresh[0] or 0.0) if fresh else 0.0
            key_data = conn.execute(
                "SELECT id, key_value FROM keys WHERE product_pid = ? AND duration = ? ORDER BY id LIMIT 1",
                (plan[0], plan[1])
            ).fetchone()
            if key_data and fresh_balance >= cost:
                key_id, key_delivered = key_data
                deleted = conn.execute("DELETE FROM keys WHERE id = ?", (key_id,)).rowcount
                if deleted != 1:
                    conn.rollback()
                    return False, "⚠️ Stock changed during purchase. Please try again.", "", None, None
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (cost, user_id, cost))
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    conn.rollback()
                    return False, "❌ Insufficient balance. Please add balance and try again.", "", None, None
                manual_remaining = int(conn.execute(
                    "SELECT COUNT(*) FROM keys WHERE product_pid = ? AND duration = ?", (plan[0], plan[1])
                ).fetchone()[0] or 0)
                actual_mode = "Manual Key"
                conn.execute(
                    "INSERT INTO history (user_id, item_name, key_value, price, order_id, purchase_method) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, f"{prod_name} ({plan[1]})", str(key_delivered), cost, order_id, "Manual Key Purchase")
                )
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            logging.exception("Manual purchase transaction failed")
            return False, "❌ Purchase could not be completed safely. No balance was deducted; please try again.", "", None, None

    if actual_mode is None:
        # No manual key was available: request exactly one API item.
        if not api_product_pid or not api_remote_duration:
            missing = "Product PID" if not api_product_pid else "Remote duration"
            return False, (
                f"❌ API {missing} is missing for {prod_name}.\n\n"
                "Set the product PID and the exact website plan label in Manage Products / Manage Plans. "
                "Your wallet balance was not deducted."
            ), "", None, None
        api_variant_id = str(plan[5] or "").strip() if len(plan) > 5 else ""
        success, api_msg = await call_reseller_api(api_product_pid, api_remote_duration, api_variant_id)
        if not success:
            reason = str(api_msg)
            await send_admin_low_balance_alert(context, user_id, prod_name, plan[1], cost, reason)
            asyncio.create_task(send_purchase_admin_alert(
                context, user_id, prod_name, plan[1], "", cost, "API Purchase",
                order_id=order_id, balance=balance, status="FAILED", failure_reason=reason
            ))
            return False, (
                f"❌ *Purchase Failed\\!*\n\n"
                f"📦 *Item:* {md_escape(prod_name)}\n"
                f"⏱️ *Duration:* {md_escape(plan[1])}\n\n"
                "🛠 The API order failed. Your wallet balance was not deducted.\n"
                "🎧 Please try again later or contact support."
            ), "", None, None

        key_delivered = str(api_msg)
        actual_mode = "API"
        with DB_LOCK:
            try:
                conn.execute("BEGIN IMMEDIATE")
                fresh = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
                fresh_balance = float(fresh[0] or 0.0) if fresh else 0.0
                if fresh_balance < cost:
                    conn.rollback()
                    # External order has succeeded, so do not claim it failed silently.
                    logging.critical("API order %s succeeded but user %s balance became insufficient before debit", order_id, user_id)
                    asyncio.create_task(send_purchase_admin_alert(
                        context, user_id, prod_name, plan[1], key_delivered, cost, "API Purchase",
                        order_id=order_id, balance=fresh_balance, status="FAILED",
                        failure_reason="API order succeeded but wallet balance changed before debit; manual reconciliation required."
                    ))
                    return False, (
                        "⚠️ Your API order was accepted, but your balance changed before payment could be recorded. "
                        "Please contact support immediately and provide order ID " + order_id + "."
                    ), "", None, None
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (cost, user_id, cost))
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    conn.rollback()
                    return False, "❌ Balance update failed. Contact support with order ID " + order_id, "", None, None
                conn.execute(
                    "INSERT INTO history (user_id, item_name, key_value, price, order_id, purchase_method) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, f"{prod_name} ({plan[1]})", key_delivered, cost, order_id, "API Purchase")
                )
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception("API purchase database finalization failed for order %s", order_id)
                return False, "⚠️ API order response was received, but recording failed. Contact support with order ID " + order_id, "", None, None

    current_row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    remaining_balance = float(current_row[0] or 0.0) if current_row else 0.0
    await process_referral_reward(context, user_id)

    if actual_mode == "Manual Key" and manual_remaining is not None:
        if manual_remaining > 2:
            with DB_LOCK:
                conn.execute(
                    """INSERT INTO manual_stock_alert_state(product_pid, duration, alerted, updated_at)
                       VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                       ON CONFLICT(product_pid, duration)
                       DO UPDATE SET alerted = 0, updated_at = CURRENT_TIMESTAMP""",
                    (str(plan[0]), str(plan[1]))
                )
                conn.commit()
        elif manual_remaining == 2:
            asyncio.create_task(send_manual_stock_alert(
                context, prod_name, plan[1], manual_remaining, str(plan[0])
            ))

    method_label = "API Purchase" if actual_mode == "API" else "Manual Key Purchase"
    asyncio.create_task(send_purchase_admin_alert(
        context, user_id, prod_name, plan[1], key_delivered, cost, method_label,
        order_id=order_id, balance=remaining_balance, status="SUCCESS"
    ))
    asyncio.create_task(send_group_purchase_notification(
        context, user_id, prod_name, plan[1], key_delivered, cost, method_label,
        order_id=order_id, status="SUCCESS"
    ))

    text = (
        "🎉 PURCHASE SUCCESSFUL\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧾 Order ID: {order_id}\n"
        f"📦 Product: {prod_name}\n"
        f"⏱️ Plan: {plan[1]}\n"
        f"💵 Amount Paid: ₹{cost:.2f}\n"
        f"💰 Remaining Balance: ₹{remaining_balance:.2f}\n\n"
        "🔑 YOUR KEY\n"
        f"{key_delivered}\n\n"
        "✅ Delivery Status: Successfully Delivered\n"
        f"🕒 Date: {_purchase_time_ist()}\n"
        f"🎧 Support: @{get_support_username()}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🙏 Thank you for shopping with !"
    )
    return True, text, group_link, plan[0], prod_name


async def deliver_purchase_result(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, back_btn_txt: str = BTN_TEXT["btn_back"], group_link: str = "", parse_mode: str = None):
    """Send purchase results safely and make the delivered key tap-to-copy in Telegram."""
    import html

    keyboard = []
    if group_link:
        keyboard.append([url_button("📢 Join Updates", group_link)])
    keyboard.append([button(back_btn_txt, "main_menu")])
    markup = InlineKeyboardMarkup(keyboard)

    # Telegram lets users tap a <code> value to copy it. Escape all dynamic text
    # so product names/keys containing special characters cannot break formatting.
    marker = "🔑 YOUR KEY\n"
    if marker in text:
        before, after = text.split(marker, 1)
        key_line, sep, after_key = after.partition("\n")
        html_text = html.escape(before) + "🔑 YOUR KEY\n"
        html_text += "<code>" + html.escape(key_line) + "</code>"
        if sep:
            html_text += "\n" + html.escape(after_key)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=html_text,
                parse_mode="HTML",
                reply_markup=markup
            )
            return
        except Exception as first_error:
            logging.error(f"Purchase result HTML send failed for user {chat_id}: {first_error}")
            # Plain-text fallback still delivers the completed order result.
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
            return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup
        )
    except Exception as first_error:
        logging.error(f"Purchase result formatting/send failed for user {chat_id}: {first_error}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=text.replace("\\", ""),
            reply_markup=markup
        )


async def show_order_summary(query, context, plan_id, prod_name, plan_duration, base_price):
    coupon = context.user_data.get('order_coupon')
    final_price = base_price
    if coupon:
        final_price = round(base_price * (1 - coupon['discount'] / 100), 2)

    text = (
        f"🧾 *ORDER SUMMARY* 🧾\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Product: {md_escape(prod_name)}\n"
        f"⏱️ Plan: {md_escape(plan_duration)}\n"
        f"💵 Product price: ₹{fmt_amount(base_price)}\n"
    )
    if coupon:
        text += f"🎟️ Coupon: `{coupon['code']}` \\(\\-{fmt_amount(coupon['discount'])}%\\)\n"
    text += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Final Total: ₹{fmt_amount(final_price)}\n\n"
        f"Choose your payment method\n"
        f"👇 Pick any option below"
    )

    keyboard = [
        [button(f"💳 UPI India - {fmt_inr_tag(final_price)}", f"order_pay_upi_{plan_id}")],
        [button("🎟️ Redeem Coupon", "order_redeem_coupon")],
        [button(BTN_TEXT["btn_back"], "main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_confirm_purchase(query, context, plan_id, prod_name, plan_duration, base_price, balance):
    coupon = context.user_data.get('order_coupon')
    final_price = base_price
    if coupon:
        final_price = round(base_price * (1 - coupon['discount'] / 100), 2)
    after_bal = balance - final_price

    text = (
        f"✅ *CONFIRM YOUR PURCHASE* ✅\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 *Product:* {md_escape(prod_name)}\n"
        f"⏱️ *Duration:* {md_escape(plan_duration)}\n"
        f"💵 *Price:* ₹{fmt_amount(base_price)}\n"
    )
    if coupon:
        text += f"🎟️ *Coupon:* `{coupon['code']}` \\(\\-{fmt_amount(coupon['discount'])}%\\)\n"
    text += (
        f"💳 *Current Balance:* ₹{fmt_amount(balance)}\n"
        f"💰 *Final Total:* ₹{fmt_amount(final_price)}\n"
        f"🧮 *Balance After:* ₹{fmt_amount(after_bal)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ *Instant Delivery* — your key is generated the moment you confirm\\.\n"
        f"🔒 *100% Secure* — nothing is deducted unless delivery succeeds\\.\n\n"
        f"Tap *Confirm Purchase* below to receive your key instantly\\."
    )
    confirm_keyboard = [
        [button("✅ Confirm Purchase", f"confirm_buy_{plan_id}")],
        [button("🎟️ Redeem Coupon", "order_redeem_coupon")],
        [button("❌ Cancel", "main_menu")]
    ]
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(confirm_keyboard))


async def show_product_plans_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: str, user_id: int):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name, is_maintenance, demo_video, description FROM products WHERE pid = ?", (pid,))
    prod = cursor.fetchone()

    cursor.execute("SELECT is_reseller FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    is_reseller = u_row[0] if u_row and u_row[0] is not None else 0

    back_to_products_cb = "user_shop"
    back_to_products_txt = BTN_TEXT["btn_back"]

    if prod and prod[1]:
        text = f"🛠 **{escape_markdown(prod[0])}**\n\nThis product is in maintenance now. Please check back later."
        keyboard = [[button(back_to_products_txt, back_to_products_cb)]]
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    cursor.execute("SELECT id, duration, price, reseller_price FROM plans WHERE product_pid = ?", (pid,))
    plans = cursor.fetchall()
    plans = sorted(plans, key=lambda p: parse_duration_hours(p[1]))

    if not prod or not plans:
        text = "⚠️ Product/Plans currently unavailable."
        keyboard = [[button(back_to_products_txt, back_to_products_cb)]]
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for pl in plans:
        cost = get_effective_price(user_id, pl[0], pl[2], pl[3], is_reseller, pid)
        keyboard.append([button(f"⏱️ {pl[1]} - ₹{cost:.2f}", f"buy_final_{pl[0]}")])
    demo_video = prod[2] if prod[2] else ""
    if demo_video:
        keyboard.append([button("🎥 Check Gameplay Video", f"demo_video_{pid}")])
    keyboard.append([button(back_to_products_txt, back_to_products_cb)])

    prod_name_safe = md_escape(prod[0])
    prod_description = prod[3] if prod[3] else ""

    desc_block = ""
    if prod_description:
        desc_lines = []
        for line in prod_description.split("\n"):
            if line.strip():
                desc_lines.append(f"✨ {md_escape(line.strip())} ✅")
            else:
                desc_lines.append("")
        desc_block = "\n".join(desc_lines) + "\n\n"

    offer_pct = get_global_offer_percent()
    offer_block = f"🎁 *SPECIAL OFFER: {fmt_amount(offer_pct)}% OFF* 🎁\n\n" if offer_pct > 0 else ""

    # NEW: product-specific offer banner (only if THIS user is eligible)
    prod_offer_pct = get_product_offer_percent_for_user(pid, user_id, bool(is_reseller))
    if prod_offer_pct > 0:
        offer_block += f"🔥 *PRODUCT OFFER FOR YOU: {md_escape(fmt_pct(prod_offer_pct))}% OFF* 🔥\n\n"

    text = (
        f"📦 *{prod_name_safe}* 📦\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{desc_block}"
        f"{offer_block}"
        f"⭐ *AVAILABLE PLANS & PRICING*\n\n"
        f"👇 *Select Your Plan Below*"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def finalize_reseller_upgrade(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    price = get_reseller_price()
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, is_reseller, full_name, username FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        balance, is_reseller, full_name, username = 0.0, 0, "N/A", "N/A"
    else:
        balance = row[0] if row[0] is not None else 0.0
        is_reseller = row[1] if row[1] is not None else 0
        full_name = row[2] if row[2] is not None else "N/A"
        username = row[3] if row[3] is not None else "N/A"

    if is_reseller:
        return False, "✅ You're already a **Reseller**! 🌟"

    if balance < price:
        return False, (
            f"⚠️ **Balance Credited, But Still Short!**\n\n"
            f"💎 **Upgrade Price:** ₹{price:.2f}\n"
            f"💰 **Your Balance:** ₹{balance:.2f}\n\n"
            f"Please try again from the Main Menu."
        )

    cursor.execute("UPDATE users SET balance = balance - ?, is_reseller = 1 WHERE user_id = ?", (price, user_id))
    conn.commit()

    try:
        user_uname = f"@{escape_markdown(username)}" if username and username != "N/A" else "N/A"
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔥 **NEW RESELLER UPGRADE!** 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Name:** {escape_markdown(full_name)}\n"
                f"🏷️ **Username:** {user_uname}\n"
                f"🆔 **User ID:** `{user_id}`\n"
                f"💵 **Paid Amount:** ₹{price:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")

    text = (
        f"🎉 **Upgrade Successful!**\n\n"
        f"You are now a **Reseller** 🌟\n"
        f"💵 **Amount Deducted:** ₹{price:.2f}\n\n"
        f"Enjoy special reseller pricing on all products!"
    )
    return True, text


def _generate_payment_qr_sync(provider: str, amount: float):
    if provider == "paytm":
        upi = get_paytm_upi()
        if not upi:
            return False, "UPI_NOT_SET", None
        url = f"https://paytm.anujbots.xyz/qr.php?upi={upi}&amount={amount}"
    else:
        upi = get_upi()
        api_key = get_fampay_api_key()
        if not upi or not api_key:
            return False, "UPI_NOT_SET", None
        url = f"https://fampay.anujbots.xyz/qr.php?upi={upi}&amount={amount}"

    try:
        res = requests.get(url, timeout=15).json()
    except Exception:
        return False, None, None

    is_ok = (res.get("status") == "success") or (res.get("success") is True)
    if not is_ok:
        return False, None, None

    data_block = res.get("data") if isinstance(res.get("data"), dict) else res
    qr_url = data_block.get("qr_url") or res.get("qr_url")
    order_id = data_block.get("order_id") or res.get("order_id")

    if not qr_url or not order_id:
        return False, None, None

    return True, qr_url, order_id


async def generate_payment_qr(provider: str, amount: float):
    return await asyncio.to_thread(_generate_payment_qr_sync, provider, amount)


def _verify_payment_status_sync(provider: str, order_id: str):
    if provider == "paytm":
        merchantid = get_paytm_merchant_id()
        merchantkey = get_paytm_merchant_key()
        url = f"https://anujbots.xyz/paytm/verify.php?orderid={order_id}&merchantid={merchantid}&merchantkey={merchantkey}"
        try:
            res = requests.get(url, timeout=10).json()
        except Exception as e:
            logging.error(f"Paytm verify exception ({order_id}): {e}")
            return False, 0.0
        if not res.get("success"):
            return False, 0.0
        if res.get("status") == "TXN_SUCCESS":
            try:
                amt = float(res.get("amount") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            return True, amt
        return False, 0.0
    else:
        api_key = get_fampay_api_key()
        url = f"https://fampay.anujbots.xyz/verify.php?order_id={order_id}&api_key={api_key}"
        try:
            res = requests.get(url, timeout=10).json()
        except Exception as e:
            logging.error(f"FamPay verify exception ({order_id}): {e}")
            return False, 0.0
        is_success = res.get("status") == "success" or res.get("status") == "SUCCESS" or res.get("txnStatus") == "SUCCESS"
        if is_success:
            raw_amt = res.get("data", {}).get("amount") or res.get("amount") or 0.0
            try:
                amt = float(raw_amt)
            except (TypeError, ValueError):
                amt = 0.0
            return True, amt
        return False, 0.0


async def verify_payment_status(provider: str, order_id: str):
    return await asyncio.to_thread(_verify_payment_status_sync, provider, order_id)


async def poll_payment_status(context: ContextTypes.DEFAULT_TYPE, order_id: str, user_id: int, msg_id: int, purpose: str, pending_plan_id: int, amount_needed: float, provider: str = "fampay"):
    max_seconds = 300
    interval = 5
    elapsed = 0

    while elapsed < max_seconds:
        await asyncio.sleep(interval)
        elapsed += interval

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM payments WHERE order_id = ?", (order_id,))
        p_row = cursor.fetchone()

        if p_row and p_row[0] == 'SUCCESS':
            return

        try:
            is_success, amt = await verify_payment_status(provider, order_id)
            if is_success:
                conn = get_db_conn()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO payments (order_id, user_id, amount, status, plan_id, purpose, provider, date) VALUES (?, ?, ?, 'SUCCESS', ?, ?, ?, datetime('now'))",
                    (order_id, user_id, amt, pending_plan_id, purpose, provider)
                )
                cursor.execute("UPDATE users SET balance = balance + ?, total_deposit = total_deposit + ? WHERE user_id = ?", (amt, amt, user_id))
                cursor.execute("SELECT full_name, username, lang FROM users WHERE user_id = ?", (user_id,))
                u_info = cursor.fetchone()
                full_name = escape_markdown(u_info[0]) if u_info and u_info[0] else "N/A"
                username = f"@{escape_markdown(u_info[1])}" if u_info and u_info[1] else "N/A"
                conn.commit()

                back_btn_txt = BTN_TEXT["btn_back"]

                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"🔔 **Auto-Payment Verified!**\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **Name:** {full_name}\n"
                            f"🏷️ **Username:** {username}\n"
                            f"🆔 **User ID:** `{user_id}`\n"
                            f"💵 **Amount Credited:** ₹{amt:.2f}\n"
                            f"🏷️ **Purpose:** {purpose}\n"
                            f"💳 **Gateway:** {provider.upper()}\n"
                            f"🆔 **Order ID:** `{order_id}`\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception:
                    pass

                if purpose == 'plan' and pending_plan_id:
                    success, text, group_link, pid, prod_name = await finalize_purchase(context, user_id, pending_plan_id, purchase_type="Direct QR / Auto UPI")
                    await deliver_purchase_result(context, user_id, text, back_btn_txt, group_link)
                    if success and pid:
                        await deliver_product_apks(context, user_id, pid, prod_name)
                elif purpose == 'reseller':
                    success, text = await finalize_reseller_upgrade(context, user_id)
                    await deliver_purchase_result(context, user_id, text, back_btn_txt, parse_mode="Markdown")
                else:
                    dep_text = (
                        f"✅ *Payment Received & Verified\\!* ✅\n\n"
                        f"💵 *Amount Credited:* ₹{fmt_amount(amt)}\n"
                        f"🧾 *Order ID:* `{order_id}`"
                    )
                    await deliver_purchase_result(
                        context, user_id,
                        dep_text,
                        back_btn_txt, parse_mode="MarkdownV2"
                    )
                return
        except Exception as e:
            logging.error(f"Polling Exception ({order_id}): {e}")

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE payments SET status = 'EXPIRED' WHERE order_id = ? AND status = 'PENDING'", (order_id,))
    conn.commit()

    try:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
        expired_text = (
            f"Your QR for ₹{amount_needed:.2f} expired because no payment was received within 5 minutes.\n\n"
            f"You can try buying again anytime."
        )
        retry_markup = InlineKeyboardMarkup([[button("🔄 Try Again", "user_shop")]])
        await context.bot.send_message(chat_id=user_id, text=expired_text, reply_markup=retry_markup)
    except Exception as e:
        logging.error(f"Failed to edit expired msg: {e}")


async def raise_payment_qr(context: ContextTypes.DEFAULT_TYPE, user_id: int, target_amount: float, current_balance: float, purpose: str, label: str, plan_id=None, item_name: str = None, plan_label: str = None, price: float = None, provider: str = None):
    if is_payment_maintenance():
        return False, "PAYMENT_MAINTENANCE"

    if not provider:
        provider = get_payment_provider()
    deficit = round(target_amount - current_balance, 2)
    if deficit <= 0:
        deficit = round(target_amount, 2)

    ok, qr_url_or_err, order_id = await generate_payment_qr(provider, deficit)
    if not ok:
        return False, qr_url_or_err

    qr_url = qr_url_or_err

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO payments (order_id, user_id, amount, status, plan_id, purpose, provider, date) VALUES (?, ?, ?, 'PENDING', ?, ?, ?, datetime('now'))",
        (order_id, user_id, deficit, plan_id, purpose, provider)
    )
    conn.commit()

    if item_name:
        item_price = price if price is not None else target_amount
        caption = build_low_balance_qr_caption(item_name, plan_label, item_price, deficit)
    else:
        caption = (
            f"💵 *Amount:* ₹{fmt_amount(deficit)}\n"
            f"⏱️ *Pay within 5 minutes*\n"
            f"✅ *Amount is credited automatically*"
        )
    keyboard = [[button("🔙 Cancel / Back", "cancel_qr")]]

    try:
        msg = await context.bot.send_photo(
            chat_id=user_id,
            photo=qr_url,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Failed to send QR photo to {user_id} (order {order_id}): {e}")
        try:
            conn2 = get_db_conn()
            cursor2 = conn2.cursor()
            cursor2.execute("UPDATE payments SET status = 'EXPIRED' WHERE order_id = ?", (order_id,))
            conn2.commit()
        except Exception:
            pass
        return False, "QR_SEND_FAILED"

    asyncio.create_task(poll_payment_status(context, order_id, user_id, msg.message_id, purpose, plan_id, deficit, provider))
    return True, order_id


async def create_backup_zip():
    def _build():
        conn = get_db_conn()
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.commit()
        except Exception:
            pass
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = f"/tmp/backup_{ts}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(DB_PATH, arcname=os.path.basename(DB_PATH))
        return zip_path
    return await asyncio.to_thread(_build)


# ============================================================
# TXT EXPORT BUILDERS
# ============================================================
def build_all_prices_txt():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT products.name, plans.duration, plans.price, plans.reseller_price
        FROM plans JOIN products ON plans.product_pid = products.pid
    """)
    rows = cursor.fetchall()
    rows = sorted(rows, key=lambda r: (r[0], parse_duration_hours(r[1])))
    now_ist = (datetime.utcnow() + IST_OFFSET).strftime("%d %b %Y, %I:%M %p")
    lines = [
        "ALL PRODUCT PRICES",
        f"Generated: {now_ist} (IST)",
        f"Total Plans: {len(rows)}",
        "=" * 40,
        "",
    ]
    if not rows:
        lines.append("No plans found yet.")
    for pname, dur, price, r_price, variant in rows:
        lines.append(f"Product: {pname}")
        lines.append(f"Plan: {dur}")
        lines.append(f"Normal price: Rs {float(price or 0):.2f}")
        lines.append(f"Reseller price: Rs {float(r_price or 0):.2f}")
        if variant:
            lines.append(f"Variant ID: {variant}")
        lines.append("-" * 40)
    return "\n".join(lines)


def build_user_orders_txt(user_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, username FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    name = (u[0] if u and u[0] else "N/A")
    uname = (f"@{u[1]}" if u and u[1] else "N/A")
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history WHERE user_id = ?", (user_id,))
    summary = cursor.fetchone()
    total_orders = summary[0] if summary and summary[0] is not None else 0
    total_spend = summary[1] if summary and summary[1] is not None else 0.0

    cursor.execute("SELECT item_name, key_value, price, date FROM history WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()

    lines = [
        "ALL STORE SHOP BOT",
        f"USERNAME: {uname}",
        f"USER ID: {user_id}",
        f"NAME: {name}",
        f"TOTAL ORDERS: {total_orders}",
        f"TOTAL SPEND: Rs {total_spend:.2f}",
        "=" * 40,
        "",
    ]
    if not rows:
        lines.append("No orders found.")
    for i, (item, key, price, date) in enumerate(rows, 1):
        lines.append(f"Order #{i}")
        lines.append(f"Item: {item}")
        lines.append(f"Key: {key}")
        lines.append(f"Amount: Rs {float(price or 0):.2f}")
        lines.append(f"Date: {to_ist_str(date)} (IST)")
        lines.append("-" * 40)
    return "\n".join(lines)


def build_all_numbers_txt():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, full_name, phone_number FROM users
        WHERE is_verified = 1 ORDER BY user_id
    """)
    rows = cursor.fetchall()
    now_ist = (datetime.utcnow() + IST_OFFSET).strftime("%d %b %Y, %I:%M %p")
    lines = [
        "ALL VERIFIED USERS",
        f"Generated: {now_ist} (IST)",
        f"Total Verified: {len(rows)}",
        "=" * 40,
        "",
    ]
    if not rows:
        lines.append("No verified users yet.")
    for i, (uid, uname, fname, phone) in enumerate(rows, 1):
        lines.append(f"#{i}")
        lines.append(f"Username: {('@' + uname) if uname else 'N/A'}")
        lines.append(f"Name: {fname or 'N/A'}")
        lines.append(f"Number: {phone or 'N/A'}")
        lines.append(f"User ID: {uid}")
        lines.append("-" * 40)
    return "\n".join(lines)


def build_ban_list_txt():
    rows = get_all_banned_users()
    now_ist = (datetime.utcnow() + IST_OFFSET).strftime("%d %b %Y, %I:%M %p")
    lines = [
        "BANNED USERS LIST",
        f"Generated: {now_ist} (IST)",
        f"Total Banned: {len(rows)}",
        "=" * 40,
        "",
    ]
    if not rows:
        lines.append("No banned users.")
    for i, (uid, fname, uname, reason) in enumerate(rows, 1):
        lines.append(f"#{i}")
        lines.append(f"User ID: {uid}")
        lines.append(f"Name: {fname or 'N/A'}")
        lines.append(f"Username: @{uname}" if uname else "Username: N/A")
        lines.append(f"Reason: {reason or 'N/A'}")
        lines.append("-" * 40)
    return "\n".join(lines)


# ============================================================
# AI SUPPORT (Google Gemini)
# ============================================================
AI_SYSTEM_PROMPT = (
    "You are the built-in AI Support assistant inside the Admin Panel of a Telegram digital-key "
    "store bot. You are talking to the bot's owner (the admin). Answer ANY question the admin asks - "
    "about this bot's admin panel, Telegram bots, Python, business, payments, marketing, or general "
    "knowledge - like a real, helpful AI assistant. Reply in the same language the admin writes in "
    "(Hindi/Hinglish or English). Be clear and practical; "
    "give short numbered steps when explaining how to do something. Use plain text only (no ** or # "
    "markdown). You cannot see live bot data (users, balances, keys, sales) and must never pretend you "
    "can. Never ask the admin to paste secret keys or tokens into the chat.\n\n"
    "=== KNOWLEDGE ABOUT THIS BOT (use when relevant) ===\n"
    "Admin Panel (open with /admin) main menu has these sections:\n"
    "0) Admin Management: Add Admin (promotes a user to Broadcast-only admin), Remove Admin, "
    "Check Admins (list of owner + broadcast admins). Only the owner (main admin) can add/remove "
    "admins. Broadcast admins can ONLY use the Broadcast feature when they open /admin.\n"
    "1) Manage Product: Add Product (Step 1: product names one per line; Step 2: same number of PIDs one per line, same order), "
    "Remove Product (also deletes its plans and keys), Add Plan (pick product, then durations, remote API duration codes, "
    "normal prices, reseller prices - each one per line, same order), Remove Plan, Edit Plan Info (duration name shown to users, "
    "remote API duration code), Rename Product, Change PID (change a product's internal PID; plans & key stock move "
    "automatically to the new PID — deep links using the old PID stop working), Line Up Products (reorder how products "
    "appear in Shop Now by sending every product's PID, one per line, in the desired top-to-bottom order).\n"
    "2) Keys Management: Add Keys (pick product + duration, paste keys one per line), Remove Keys (delete individual keys), "
    "Check Keys Stock. Purchases use local key stock first; if there is no local key, the bot buys from the reseller API automatically.\n"
    "3) Pricing Management: Change Product Price (normal price of a plan; the screen shows Product, Plan and Current price), "
    "Set Plan Reseller Price (also shows Product, Plan and Current price), Set Upgrade Price "
    "(price for a user to upgrade to Reseller), Set Custom Price (special price for one user ID on one plan), Remove Custom Price, "
    "See All Prices (sends a .txt file listing every Product / Plan / Normal price / Reseller price), Get All Users Offer "
    "(set a sitewide discount % that is applied on top of every user's price on every plan; send 0 to disable it), "
    "Product Offer (set a discount % on ONE product only, for one chosen group: Reseller, Users (non-reseller), "
    "Custom Users (specific user IDs) or All Users; only that group gets the discount on that product; it can be removed any time; "
    "a Custom Price for a user still overrides it).\n"
    "4) Coupon Management: Create Coupon (3 steps: discount %, max users, validity like '24 hours' or '3 days'), View Coupons, "
    "Spin Balance Range (min and max rupees a balance win can give), Spin Coupon Range (min and max discount % of a spin coupon), "
    "Spin Chances (send '<coupon%> <balance%>'; the rest is 'Better Luck Next Time'). Default odds: coupon 2%, balance 1%, "
    "better luck 97%. Users get 1 free spin per 24 hours. Users redeem a coupon on the order screen (Redeem Coupon button); "
    "each user can use a code only once; admin is notified when a coupon is used.\n"
    "5) Balance Management: Add Balance and Remove Balance (send '<user_id> <amount>'; the user gets a notification on add), Check Balance.\n"
    "6) Reseller Management: Add Reseller (user is notified), Remove Reseller, My Resellers list.\n"
    "7) Additional Features: Product Deep Links, Set Product Video (a real Telegram video, sent "
    "as an actual video attachment BEFORE purchase on the plans screen — not a link), Set Description (product description shown on "
    "the plans screen; send 'clear' to remove), Set APKs on Product (Step 1: real APK file attachments, skippable; Step 2: optional "
    "download link(s), skippable; Step 3: an optional real Telegram video attachment, skippable — NOT a link. When a user buys that "
    "product, all APK files + any links are sent together right after the key, and the video (if set) is delivered separately as a "
    "real video message), Get All Num (sends a .txt file of every verified user: Username, Name, Number, User ID).\n"
    "8) Extra Features: Bot Stats (also shows today's total payment received, with a Check All Payments button for a full list of "
    "today's successful payments — type/provider, name, username, user id, and date+time), Bot Maintenance (whole bot offline for "
    "users; admin can still use it), Maintenance Mode (per-product maintenance), All User Reseller (everyone pays reseller price), "
    "Reseller Menu (show/hide the Upgrade To Reseller button), Ban Management (ban/unban users, check ban list — banned users cannot "
    "use the bot at all).\n"
    "9) Settings: Auto API Setting (set/change the reseller API key - the API endpoint is "
    "fixed to Bantibhaiya /api/v1/generate-key and cannot be changed), Set AI API Key (set/change the Gemini AI Support key), "
    "Set How To Use Video, Set Support Link (support username).\n"
    "Also on the main panel: Payment Settings (FamPay/Paytm UPI, API keys, switch the single active gateway, Payment Maintenance toggle "
    "that blocks QR payments), Broadcast (send any message to all users), Backup (zip of the whole database), Restore (upload a backup "
    "zip; overwrites everything), AI Support.\n"
    "User side: Shop Now shows all products in one list (in the admin-configured Line Up order) -> product -> plan -> pay with "
    "wallet balance or auto UPI QR (payment auto-verifies within 5 minutes and the key is delivered instantly). There are no "
    "categories. If a Get All Users Offer % is active, it is shown on the plans screen and is automatically applied to every "
    "user's price alongside their normal/reseller/custom price. Refer & Earn gives Rs 1 to the referrer when the referred user "
    "makes a first purchase. Users can open support tickets; the admin replies from the ticket message. "
    "The All Orders screen shows the user's recent activity and has an Export Txt button that sends ALL of that user's orders as a .txt file. "
    "Add Balance simply asks "
    "the user for an amount and generates a QR with whichever payment gateway (FamPay or Paytm) is currently set active by the "
    "admin — there is no gateway choice shown to the user. Product purchases and Reseller Upgrade payments use that same "
    "currently active gateway."
)

AI_HELP_TEXTS = {
    "product": (
        "📦 **Manage Product**\n\n"
        "Admin Panel → Manage Product has: Add Product (Step 1 names, Step 2 PIDs — line by line), "
        "Remove Product, Add Plan (duration, remote code, normal price, reseller price — line by line), "
        "Remove Plan, Edit Plan Info, Rename Product, Change PID, and Line Up Products (reorder the shop list)."
    ),
    "keys": (
        "🔑 **Keys Management**\n\n"
        "Add Keys — choose a product + duration, then send keys line by line.\n"
        "Remove Keys — delete keys one by one.\n"
        "Check Keys Stock — see how many keys each product/duration has."
    ),
    "pricing": (
        "💲 **Pricing Management**\n\n"
        "Change Product Price and Set Plan Reseller Price (both show Product, Plan and Current price), Set Upgrade Price, "
        "Set Custom Price (special price for one user), Remove Custom Price, "
        "See All Prices (sends a .txt file with every plan's Normal & Reseller price), "
        "🎁 Get All Users Offer (a sitewide % discount applied for every user, on top of their normal/reseller price), and "
        "🎯 Product Offer (a % discount on ONE product for Reseller / Users / Custom Users / All Users)."
    ),
    "coupons": (
        "🎟️ **Coupon Management**\n\n"
        "Create Coupon — discount %, max users, validity.\n"
        "View Coupons — all coupons and their usage.\n"
        "Spin Balance Range / Spin Coupon Range / Spin Chances — set the Lucky Spin rewards and odds here."
    ),
    "balance": (
        "💳 **Balance Management**\n\n"
        "Add Balance / Remove Balance — send `<user_id> <amount>`. Check Balance — look up a balance by user ID."
    ),
    "reseller": (
        "👥 **Reseller Management**\n\n"
        "Add Reseller, Remove Reseller, My Resellers. The upgrade price is set in Pricing Management."
    ),
    "additional": (
        "➕ **Additional Features**\n\n"
        "Product Deep Links, Set Product Video (a real video attachment, shown before purchase), Set Description, "
        "📥 Set APKs on Product (APK files + links delivered together after the key, plus an optional "
        "real video attachment sent separately), and 📱 Get All Num (txt file of all verified users: Username, Name, Number, User ID)."
    ),
    "extra": (
        "✨ **Extra Features**\n\n"
        "Bot Stats (includes today's total payment received + a Check All Payments button showing every payment "
        "received today with type, name, username, user id and date/time), Bot Maintenance, Maintenance Mode "
        "(per product), All User Reseller, Reseller Menu, and 🚫 Ban Management (ban/unban users, check the ban list)."
    ),
    "settings": (
        "⚙️ **Settings**\n\n"
        "Auto API Setting (reseller API key), Set AI API Key (Gemini key for AI Support), Set HowToVideo, Set Support Link. "
        "The API URL is fixed, only the key can be changed."
    ),
    "payment": (
        "💠 **Payment Settings**\n\n"
        "FamPay/Paytm UPI + keys, Switch Gateway, Payment Maintenance toggle. Whichever gateway is set active "
        "is used for Add Balance, Product Purchases and Reseller Upgrade — the user is never asked to choose."
    ),
    "backup": (
        "💾 **Backup & Restore**\n\n"
        "Backup — your whole database in a zip. Restore — send the same zip back (current data will be overwritten, so take a backup first)."
    ),
    "apks": (
        "📥 **Set APKs on Product**\n\n"
        "Additional Features → Set APKs on Product → pick a product → "
        "Step 1: send your APK file(s) as attachments, or tap ⏭️ Skip Files → "
        "Step 2: send link(s), or tap ⏭️ Skip Links → "
        "Step 3: send an actual video attachment, or tap ⏭️ Skip Video → saved. On purchase, all files go out, then a message "
        "with any links, and the video (if set) is sent separately as a real video."
    ),
    "payments_report": (
        "🧾 **Check All Payments / Today's Payment Received**\n\n"
        "Extra Features → Bot Stats now shows a 'Today's Payment Received' line (count + total ₹) and a "
        "🧾 Check All Payments button. That screen lists every SUCCESS payment received today with: amount, "
        "Type (FamPay/Paytm), Name, Username, User ID and Time+Date (IST)."
    ),
    "pid": (
        "🆔 **Change PID**\n\n"
        "Manage Product → 🆔 Change PID → pick a product → send the new PID. All of that product's Plans "
        "and local Key stock are moved to the new PID automatically. Any old deep link using the previous "
        "PID will stop working — reshare the new one from Product Deep Links."
    ),
    "lineup": (
        "↕️ **Line Up Products**\n\n"
        "Manage Product → ↕️ Line Up Products → the current top-to-bottom order is shown with each PID → "
        "send every PID again, one per line, in the order you want (top, then any middle ones, then bottom). "
        "Shop Now will then list products in that exact order."
    ),
    "offer": (
        "🎁 **Get All Users Offer**\n\n"
        "Pricing Management → 🎁 Get All Users Offer → send a discount % (e.g. `15`). That % is instantly "
        "applied on top of every user's price (normal or reseller) on every plan, shown to them as a Special "
        "Offer banner on the plans screen. Send `0` to disable it and restore normal pricing. Custom prices "
        "set for a specific user still override this."
    ),
    "prodoffer": (
        "🎯 **Product Offer**\n\n"
        "Pricing Management → 🎯 Product Offer → pick ONE product → choose who gets it: "
        "👑 Reseller, 👤 Users (non-reseller), 🎯 Custom Users or 🌍 All Users → send the discount % (e.g. `5` or `10`). "
        "For Custom Users you then send the User IDs (space/line separated). Only the chosen group sees the discount "
        "on that product. Tap ❌ Remove Offer on the same screen to stop it. A Custom Price for a user still overrides it."
    ),
    "getnum": (
        "📱 **Get All Num**\n\n"
        "Additional Features → 📱 Get All Num → the bot sends a .txt file with every verified user's "
        "Username, Name, Number and User ID."
    ),
    "adminmgmt": (
        "🛡️ **Admin Management**\n\n"
        "Admin Panel → 🛡️ Admin Management has:\n"
        "➕ Add Admin — send a User ID to give that user Broadcast-only access.\n"
        "➖ Remove Admin — pick a broadcast admin to remove.\n"
        "📋 Check Admins — see the owner and all broadcast admins.\n\n"
        "Broadcast admins can only use the Broadcast feature (they see only a Broadcast button when they open /admin). "
        "Only the owner can add/remove admins."
    ),
    "ban": (
        "🚫 **Ban Management**\n\n"
        "Extra Features → 🚫 Ban Management has:\n"
        "🔨 Ban User — send a User ID to ban that user (optionally with a reason).\n"
        "✅ Unban User — send a User ID to unban.\n"
        "📋 Check Ban List — see all banned users.\n"
        "📄 Export Ban List — get a .txt file of all banned users.\n\n"
        "Banned users cannot use the bot at all — they see a 'You are Banned' message when they try. "
        "They are notified when banned/unbanned."
    ),
    "variant": (
        "🆔 **Variant ID**\n\n"
        "When adding a plan, you can set a Variant ID (optional). This is sent to the reseller API "
        "along with the product PID and remote duration when purchasing. If left empty, the bot uses "
        "only PID + remote duration."
    ),
}

_AI_WORKING = {"model": None, "auth": None}

_AI_AUTH_STYLES = ["x-goog-api-key", "bearer", "query"]


def _ai_build_request(model, auth, key, payload):
    url = f"{AI_API_BASE}/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = None
    if auth == "x-goog-api-key":
        headers["x-goog-api-key"] = key
    elif auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    else:
        params = {"key": key}
    return url, headers, params


def _ask_ai_sync(history):
    key = get_ai_api_key()
    if not key:
        return False, "NO_KEY"

    msgs = list(history[-AI_MAX_HISTORY:])
    while msgs and msgs[0].get("role") != "user":
        msgs.pop(0)
    if not msgs:
        return False, "Empty question"

    contents = []
    for m in msgs:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

    def make_payload(model):
        gen_cfg = {"maxOutputTokens": 4096, "temperature": 0.7}
        if "2.5" in model:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        return {
            "system_instruction": {"parts": [{"text": AI_SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": gen_cfg,
        }

    combos = []
    if _AI_WORKING["model"] and _AI_WORKING["auth"]:
        combos.append((_AI_WORKING["model"], _AI_WORKING["auth"]))
    for model in AI_MODELS:
        for auth in _AI_AUTH_STYLES:
            if (model, auth) not in combos:
                combos.append((model, auth))

    last_err = "Unknown AI error"
    for model, auth in combos:
        payload = make_payload(model)
        url, headers, params = _ai_build_request(model, auth, key, payload)
        try:
            r = requests.post(url, headers=headers, params=params, json=payload, timeout=90)
            data = r.json()
        except Exception as e:
            last_err = f"Request failed: {e}"
            continue

        if r.status_code != 200:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("message") or f"HTTP {r.status_code}"
            last_err = msg
            if r.status_code == 429:
                return False, "Rate limit / quota reached on this API key. Please wait a minute and try again."
            continue

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                fb = data.get("promptFeedback", {})
                block_reason = fb.get("blockReason")
                if block_reason:
                    return False, f"Blocked by Gemini safety filter: {block_reason}"
                last_err = "Empty AI response"
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        except Exception as e:
            last_err = f"Failed to parse AI response: {e}"
            continue

        if not text:
            last_err = "Empty AI response"
            continue

        _AI_WORKING["model"] = model
        _AI_WORKING["auth"] = auth
        return True, text

    return False, last_err


async def ask_ai(history):
    return await asyncio.to_thread(_ask_ai_sync, history)


async def send_long_text(message, text, reply_markup=None):
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or ["(empty)"]
    for i, ch in enumerate(chunks):
        await message.reply_text(ch, reply_markup=reply_markup if i == len(chunks) - 1 else None)


# ============================================================
# PHONE NUMBER VERIFICATION
# ============================================================
VERIFICATION_TEXT = (
    "🏪  All Store Shop Bot 🛒️\n\n"
    "🎉 Welcome {name}\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🚨 VERIFICATION REQUIRED ✅\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "🌟 To start shopping, please verify your phone number. 👨\n\n"
    "📈 Why we need this: 🔥\n"
    "🛒 Secure your purchases ✅\n"
    "🔥 Deliver keys to you ✅\n"
    "💡 Protect your account ✅\n\n"
    "🤖 Tap the button below to share your contact. ✅"
)

VERIFICATION_SUCCESS_TEXT = (
    "✅ VERIFICATION SUCCESSFUL ✅\n\n"
    "📞 Your phone number has been verified successfully.\n\n"
    "🔥 You now have full access to the bot."
)


def build_verify_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("✅ Verify Account", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


async def send_verification_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str):
    text = VERIFICATION_TEXT.format(name=name or "User")
    keyboard = build_verify_keyboard()
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        try:
            await update.callback_query.answer("⚠️ Please verify your phone number first.", show_alert=True)
        except Exception:
            pass
        await context.bot.send_message(chat_id=update.callback_query.from_user.id, text=text, reply_markup=keyboard)
    else:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


async def notify_admin_verification(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str, full_name: str, phone: str):
    uname_disp = f"@{escape_markdown(username)}" if username else "N/A"
    name_disp = escape_markdown(full_name) if full_name else "N/A"
    phone_disp = phone or "N/A"
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ **NEW VERIFICATION** ✅\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Name:** {name_disp}\n"
                f"🏷️ **Username:** {uname_disp}\n"
                f"🆔 **User ID:** `{user_id}`\n"
                f"📞 **Number:** `{phone_disp}`\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to notify admin of verification: {e}")


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    contact = message.contact if message else None
    if not contact:
        return

    user_info = update.effective_user

    # Check if user is banned
    user_row = get_user(user_info.id, user_info.username, user_info.full_name)
    if is_user_banned(user_row):
        await send_banned_notice(update)
        return

    if contact.user_id and contact.user_id != user_info.id:
        await message.reply_text(
            "❌ Please share your OWN contact using the button below — not someone else's.",
            reply_markup=build_verify_keyboard()
        )
        return

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT is_verified FROM users WHERE user_id = ?", (user_info.id,))
    row = cursor.fetchone()
    already_verified = bool(row and row[0])

    phone = (contact.phone_number or "").strip()
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"

    cursor.execute(
        "UPDATE users SET phone_number = ?, is_verified = 1 WHERE user_id = ?",
        (phone, user_info.id)
    )
    conn.commit()

    await message.reply_text(VERIFICATION_SUCCESS_TEXT, reply_markup=ReplyKeyboardRemove())

    if not already_verified:
        await notify_admin_verification(context, user_info.id, user_info.username, user_info.full_name, phone)

    pending_pid = context.user_data.pop('pending_product_deep_link', None)
    if pending_pid:
        await show_product_plans_menu(update, context, pending_pid, user_info.id)
    else:
        await start(update, context)


# ============================================================
# START & MAIN MENU
# ============================================================
def build_main_menu_keyboard(is_reseller=False):
    keyboard = [
        [button(BTN_TEXT["btn_shop"], "user_shop")],
        [button(BTN_TEXT["btn_profile"], "user_profile"), button(BTN_TEXT["btn_add_bal"], "user_add_balance")],
        [button(BTN_TEXT["btn_history"], "user_history"), button(BTN_TEXT["btn_refer"], "user_refer_earn")],
    ]
    if is_reseller:
        keyboard.append([button(f"👑 {BTN_TEXT['btn_reseller_dashboard']}", "user_reseller_dashboard")])
    elif is_reseller_enabled():
        keyboard.append([button(BTN_TEXT["btn_upgrade"], "user_upgrade_reseller")])
    keyboard.append([url_button("📢 Check Proofs", PAYMENT_PROOFS_LINK)])
    keyboard += [
        [button(BTN_TEXT["btn_spin"], "user_lucky_spin"), button(BTN_TEXT["btn_how"], "user_how_to_use")],
        [button(BTN_TEXT["btn_support"], "user_support")]
    ]
    return keyboard


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_info = update.effective_user

    # Check if user is banned
    user_row = get_user(user_info.id, user_info.username, user_info.full_name)
    if is_user_banned(user_row) and user_info.id != ADMIN_ID:
        await send_banned_notice(update)
        return

    if is_bot_maintenance() and user_info.id != ADMIN_ID and not is_any_admin(user_info.id):
        await send_maintenance_notice(update)
        return

    referrer_id = 0
    product_deep_link = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.replace("ref_", ""))
            except ValueError:
                pass
        elif arg.startswith("prod_"):
            product_deep_link = arg.replace("prod_", "")

    user_data = get_user(user_info.id, user_info.username, user_info.full_name, referrer_id)

    if user_info.id != ADMIN_ID and not is_any_admin(user_info.id) and not is_user_verified(user_data):
        if product_deep_link:
            context.user_data['pending_product_deep_link'] = product_deep_link
        await send_verification_prompt(update, context, user_info.first_name)
        return

    if product_deep_link:
        await show_product_plans_menu(update, context, product_deep_link, user_info.id)
        return

    is_reseller_flag = bool(user_data[4]) if len(user_data) > 4 and user_data[4] else False
    keyboard = build_main_menu_keyboard(is_reseller_flag)
    welcome_msg = build_welcome_text(
        user_info.first_name or "User",
        (user_data[3] if len(user_data) > 3 and user_data[3] is not None else 0.0),
        user_data[0]
    )

    if update.message:
        await update.message.reply_text(welcome_msg, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        try:
            await update.callback_query.edit_message_text(welcome_msg, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await context.bot.send_message(chat_id=user_info.id, text=welcome_msg, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# LUCKY SPIN — rolling animation
# ============================================================
SPIN_REEL_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣", "⭐", "🍀", "🎲"]


async def animate_spin_reels(query, total_seconds: float = 4.0, interval: float = 0.4):
    steps = max(1, int(total_seconds / interval))
    for _ in range(steps):
        reel = "  ".join(random.choice(SPIN_REEL_SYMBOLS) for _ in range(3))
        try:
            await query.edit_message_text(
                f"🎰 ━━ LUCKY SPIN ━━ 🎰\n\n"
                f"{reel}\n\n"
                f"🍀 Spinning..."
            )
        except Exception:
            pass
        await asyncio.sleep(interval)


async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    # Check if user is banned
    user_row = get_user(user_id, query.from_user.username, query.from_user.full_name)
    if is_user_banned(user_row) and user_id != ADMIN_ID:
        await send_banned_notice(update)
        return

    if is_bot_maintenance() and user_id != ADMIN_ID and not is_any_admin(user_id):
        await send_maintenance_notice(update)
        return

    user = get_user(user_id, query.from_user.username, query.from_user.full_name)
    back_btn_txt = BTN_TEXT["btn_back"]

    if user_id != ADMIN_ID and not is_any_admin(user_id) and not is_user_verified(user):
        await send_verification_prompt(update, context, query.from_user.first_name)
        return

    if data == "main_menu":
        await query.answer()
        context.user_data['state'] = None
        if query.message and getattr(query.message, 'photo', None):
            try:
                await query.message.delete()
            except Exception:
                pass
        await start(update, context)

    elif data == "cancel_qr":
        await query.answer()
        context.user_data['state'] = None
        try:
            await query.message.delete()
        except Exception:
            pass
        await start(update, context)

    elif data.startswith("demo_video_"):
        pid = data.split("demo_video_", 1)[1]
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name, demo_video FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row or not row[1]:
            await query.answer("❌ Video not available.", show_alert=True)
            return
        await query.answer()
        try:
            await context.bot.send_video(chat_id=user_id, video=row[1], caption=f"🎥 Gameplay Video - {row[0]}")
        except Exception as e:
            logging.error(f"Failed to send demo video to {user_id} for {pid}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text="❌ Failed to send the video, please contact support.")
            except Exception:
                pass

    elif data == "user_profile":
        await query.answer()
        name = md_escape(user[2] if user[2] else "N/A")
        uname = f"@{md_escape(user[1])}" if user[1] else "N/A"
        uid = user[0]
        bal = user[3] if user[3] is not None else 0.0
        acc_type = "🌟 Reseller" if (len(user) > 4 and user[4]) else "👤 User"
        phone = user[10] if len(user) > 10 and user[10] else "Not Verified"

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history WHERE user_id = ?", (uid,))
        row = cursor.fetchone()
        orders = (row[0] if row and row[0] is not None else 0)
        spend = (row[1] if row and row[1] is not None else 0.0)

        text = (
            f"👤 *MY PROFILE* 👤\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📛 *Name:* {name}\n"
            f"🏷️ *Username:* {uname}\n"
            f"🆔 *User ID:* `{uid}`\n"
            f"📞 *Phone:* {md_escape(phone)}\n"
            f"💎 *Account Type:* {md_escape(acc_type)}\n"
            f"💰 *Balance:* ₹{fmt_amount(bal)}\n"
            f"🛍️ *Total Orders:* {orders}\n"
            f"💸 *Total Spend:* ₹{fmt_amount(spend)}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = [
            [button(BTN_TEXT["btn_add_bal"], "user_add_balance")],
            [button(back_btn_txt, "main_menu")]
        ]

        try:
            await query.message.delete()
        except Exception:
            pass

        photo_sent = False
        try:
            photos = await context.bot.get_user_profile_photos(uid, limit=1)
            if photos and photos.total_count > 0:
                file_id = photos.photos[0][-1].file_id
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=file_id,
                    caption=text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                photo_sent = True
        except Exception as e:
            logging.error(f"Failed to fetch profile photo for {uid}: {e}")

        if not photo_sent:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data == "user_add_balance":
        await query.answer()
        context.user_data['state'] = 'WAIT_DEPOSIT_AMT'
        text = (
            f"💳 *Add Balance To Wallet* 💳\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Enter the amount you want to deposit to your wallet:\n"
            f"_\\(Example: `50` or `100`\\)_"
        )
        keyboard = [[button(back_btn_txt, "main_menu")]]
        try:
            await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_how_to_use":
        await query.answer()
        vid_url = get_setting('how_to_use_video', '')
        text = (
            f"❓ *HOW TO PURCHASE KEYS* ❓\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"1\ufe0f\u20e3 Tap *Shop Now* and select a product\\.\n"
            f"2\ufe0f\u20e3 Pick your desired plan duration\\.\n"
            f"3\ufe0f\u20e3 Scan the automatic QR code and complete the payment\\.\n"
            f"4\ufe0f\u20e3 Payment auto\\-verifies within 5 mins and your key is delivered instantly\\! \u26a1"
        )
        keyboard = []
        if vid_url and vid_url.startswith("http"):
            keyboard.append([url_button("▶️ Watch Video Tutorial", vid_url)])
        keyboard.append([button(back_btn_txt, "main_menu")])
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_refer_earn":
        await query.answer()
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        total_refs = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ? AND has_purchased = 1", (user_id,))
        successful_refs = cursor.fetchone()[0]

        earned = successful_refs * 1.0

        text = (
            f"🎁 *REFER & EARN* 🎁\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Earn *₹{fmt_amount(1.0)}* for every friend who makes their first purchase\\!\n\n"
            f"👥 *Referred:* {total_refs}   ✅ *Purchased:* {successful_refs}\n"
            f"💰 *Earned:* ₹{fmt_amount(earned)}\n\n"
            f"🔗 *Your Link:*\n`{ref_link}`"
        )

        share_msg = f"🛒 Buy top-quality digital keys instantly!\nJoin using my referral link to get started:\n{ref_link}"
        share_url = f"https://t.me/share/url?url={ref_link}&text={requests.utils.quote(share_msg)}"

        keyboard = [
            [url_button("📤 Share Link", share_url)],
            [button(back_btn_txt, "main_menu")]
        ]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_lucky_spin":
        await query.answer()
        current_time = int(time.time())
        last_spin = user[8] if len(user) > 8 and user[8] is not None else 0
        cooldown = 86400

        intro_text = (
            f"🎰 ━━ *Lucky SPIN* ━━ 🎰\n\n"
            f"🎁 *Daily Free Spin\\!*\n"
            f"🎟️ *Coupon \\+ Win Balance*\n"
            f"⏰ *Get 1 Free Spin Every Day*\n"
            f"━━━━━━━━━━━━━━━\n"
        )

        if current_time - last_spin < cooldown:
            remaining = cooldown - (current_time - last_spin)
            hours, minutes, seconds = remaining // 3600, (remaining % 3600) // 60, remaining % 60
            intro_text += f"⏳ *Next Spin In:* {hours:02d}h {minutes:02d}m {seconds:02d}s"
            keyboard = [[button(back_btn_txt, "main_menu")]]
        else:
            intro_text += (
                f"✅ *Spin Available*\n\n"
                f"🍀 *Spin And Test Your Luck*"
            )
            keyboard = [
                [button("🎰 Spin Now", "user_spin_now")],
                [button(back_btn_txt, "main_menu")]
            ]

        await query.edit_message_text(intro_text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_spin_now":
        current_time = int(time.time())
        last_spin = user[8] if len(user) > 8 and user[8] is not None else 0
        cooldown = 86400

        if current_time - last_spin < cooldown:
            remaining = cooldown - (current_time - last_spin)
            hours, minutes, seconds = remaining // 3600, (remaining % 3600) // 60, remaining % 60
            await query.answer(f"⏳ Next spin in {hours:02d}h {minutes:02d}m {seconds:02d}s...", show_alert=True)
            return

        await query.answer()

        await animate_spin_reels(query)

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_spin_time = ? WHERE user_id = ?", (current_time, user_id))
        conn.commit()

        s = get_spin_settings()
        keyboard = [[button(back_btn_txt, "main_menu")]]

        roll = random.uniform(0, 100)
        if roll < s['coup_chance']:
            discount = round(random.uniform(s['coup_min'], s['coup_max']), 2)
            code, expires_at = create_coupon(
                discount_percent=discount, max_users=1, valid_hours=s['coupon_validity_hours'],
                source='spin', owner_user_id=user_id, prefix="SPIN"
            )
            spin_result = (
                f"🎉 *Coupon Win* 🎉\n\n"
                f"🎟️ *Your Coupon:* `{code}` \\({fmt_amount(discount)}%\\)\n"
                f"💸 *Use it to get a discount*"
            )
        elif roll < s['coup_chance'] + s['bal_chance']:
            reward = round(random.uniform(s['bal_min'], s['bal_max']), 2)
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
            conn.commit()
            spin_result = (
                f"🎉 *Congratulations* 🎉\n\n"
                f"💰 *You Won ₹{fmt_amount(reward)} Balance*\n"
                f"✅ *Added Now*"
            )
        else:
            spin_result = (
                f"😅 *Better Luck Next Time* 😅\n\n"
                f"❌ *Nothing Won This Time*\n"
                f"⏰ *Come Back Tomorrow For Another Free Spin*"
            )

        await query.edit_message_text(spin_result, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_support":
        await query.answer()
        context.user_data['state'] = None
        s_uname = get_support_username().replace("@", "")
        text = build_support_text()
        keyboard = [
            [url_button("👨‍💻 Contact Admin", f"https://t.me/{s_uname}")],
            [button("🎫 Open Ticket", "user_open_ticket")],
            [button(back_btn_txt, "main_menu")]
        ]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_open_ticket":
        await query.answer()
        ticket_id = generate_ticket_id()
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tickets (ticket_id, user_id, status) VALUES (?, ?, 'open')", (ticket_id, user_id))
        conn.commit()

        context.user_data['state'] = 'WAIT_TICKET_MSG'
        context.user_data['open_ticket_id'] = ticket_id

        text = (
            f"🎫 *TICKET CREATED* 🎫\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 *Ticket ID:* `{ticket_id}`\n\n"
            f"✍️ Type your message or problem now\\.\n\n"
            f"Our admin will receive it instantly and reply to you right here\\."
        )
        keyboard = [[button(back_btn_txt, "main_menu")]]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_upgrade_reseller":
        if not is_reseller_enabled():
            await query.answer("❌ Reseller upgrades are currently unavailable.", show_alert=True)
            return
        await query.answer()
        try:
            price = get_reseller_price()
            text = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌟 *UPGRADE TO RESELLER* 🌟\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💎 *Benefits:*\n\n"
                f"• Special reseller prices on ALL products\n"
                f"• Priority support — jump the queue\n"
                f"• Balance tracking dashboard\n"
                f"• Unlock bulk buying for your own customers\n"
                f"• One\\-time payment — lifetime reseller access\n\n"
                f"💰 *One\\-time Investment:* ₹{fmt_amount(price)}\n\n"
                f"🚀 Ready to start earning as a Reseller?"
            )
            keyboard = [
                [button("🌟 Upgrade Now", "user_reseller_upgrade_now")],
                [button(back_btn_txt, "main_menu")]
            ]
            await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logging.error(f"Failed to show reseller upgrade screen for user {user_id}: {e}")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Something went wrong while loading the Reseller Upgrade screen. Please try again or contact Admin Support.",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
            except Exception:
                pass

    elif data == "user_reseller_dashboard":
        await query.answer()
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        orders = row[0] if row and row[0] is not None else 0
        spend = row[1] if row and row[1] is not None else 0.0
        bal = user[3] if user[3] is not None else 0.0
        text = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 🌟 *RESELLER DASHBOARD* 🌟 👑\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💎 *Your Benefits:*\n\n"
            f"• Special reseller prices on ALL products\n"
            f"• Priority support — jump the queue\n"
            f"• Balance tracking dashboard\n"
            f"• Unlock bulk buying for your own customers\n"
            f"• Lifetime reseller access\n\n"
            f"💰 *Wallet Balance:* ₹{fmt_amount(bal)}\n"
            f"🛍️ *Total Orders:* {orders}\n"
            f"💸 *Total Spend:* ₹{fmt_amount(spend)}\n\n"
            f"🚀 Keep enjoying your reseller perks\\!"
        )
        keyboard = [
            [button(BTN_TEXT["btn_add_bal"], "user_add_balance")],
            [button(back_btn_txt, "main_menu")]
        ]
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "user_reseller_upgrade_now":
        if not is_reseller_enabled():
            await query.answer("❌ Reseller upgrades are currently unavailable.", show_alert=True)
            return
        if user[4]:
            await query.answer("✅ You're already a Reseller!", show_alert=True)
            return
        if user_id in PROCESSING_PURCHASES:
            await query.answer("⏳ Your upgrade is already being processed...", show_alert=True)
            return
        PROCESSING_PURCHASES.add(user_id)
        try:
            await query.answer()
            price = get_reseller_price()
            balance = user[3] if user[3] is not None else 0.0
            back_kb = InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
            if balance >= price:
                success, text = await finalize_reseller_upgrade(context, user_id)
                try:
                    await query.message.delete()
                except Exception:
                    pass
                if success:
                    await deliver_purchase_result(context, user_id, text, back_btn_txt, parse_mode="Markdown")
                else:
                    try:
                        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", reply_markup=back_kb)
                    except Exception as e:
                        logging.error(f"Failed to send reseller upgrade status message: {e}")
                        await context.bot.send_message(chat_id=user_id, text="⚠️ Something went wrong. Please try again or send /start.", reply_markup=back_kb)
            else:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                ok, info = await raise_payment_qr(
                    context, user_id, price, balance, purpose='reseller',
                    label="Reseller Upgrade", item_name="Reseller Upgrade", price=price
                )
                if not ok:
                    if info == "PAYMENT_MAINTENANCE":
                        await context.bot.send_message(chat_id=user_id, text=PAYMENT_MAINTENANCE_MSG, parse_mode="MarkdownV2", reply_markup=back_kb)
                    elif info == "UPI_NOT_SET":
                        await context.bot.send_message(chat_id=user_id, text="❌ *UPI Not Set\\!*\n\nPlease contact admin to enable payments\\.", parse_mode="MarkdownV2", reply_markup=back_kb)
                    else:
                        await context.bot.send_message(chat_id=user_id, text="❌ Payment QR generation failed. Please try again later.", reply_markup=back_kb)
        except Exception as e:
            logging.error(f"Reseller upgrade flow failed for user {user_id}: {e}")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Something went wrong while processing your reseller upgrade. Please try again or contact Admin Support.",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
            except Exception:
                pass
        finally:
            PROCESSING_PURCHASES.discard(user_id)

    elif data == "user_history" or data.startswith("user_history_page_"):
        await query.answer()
        try:
            page = int(data.rsplit("_", 1)[1]) if data.startswith("user_history_page_") else 0
        except (TypeError, ValueError):
            page = 0
        page = max(0, page)

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
        purchase_count = int((cursor.fetchone() or [0])[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'SUCCESS'", (user_id,))
        deposit_count = int((cursor.fetchone() or [0])[0] or 0)
        total_entries = purchase_count + deposit_count

        PAGE_SIZE = 5
        total_pages = max(1, (total_entries + PAGE_SIZE - 1) // PAGE_SIZE)
        if page >= total_pages:
            page = total_pages - 1

        cursor.execute("""
            SELECT 'purchase' AS type, date, item_name AS item,
                   key_value AS key_value, price AS amount, '' AS provider
            FROM history WHERE user_id = ?
            UNION ALL
            SELECT 'deposit' AS type, date, 'Wallet Deposit' AS item,
                   '' AS key_value, amount AS amount, COALESCE(provider, '') AS provider
            FROM payments WHERE user_id = ? AND status = 'SUCCESS'
            ORDER BY date DESC LIMIT ? OFFSET ?
        """, (user_id, user_id, PAGE_SIZE, page * PAGE_SIZE))
        entries = cursor.fetchall()

        if not entries:
            body = "📭 *No Orders / Activity Found*"
        else:
            body = ""
            for etype, date_val, item, key_value, amount, provider in entries:
                when = md_escape(to_ist_str(date_val))
                if etype == "purchase":
                    body += (
                        "🛍️ *PURCHASE ORDER*\n"
                        f"📦 *Product:* {md_escape(item)}\n"
                        f"🔑 *Key:* `{md_escape(key_value)}`\n"
                        f"💵 *Amount:* ₹{fmt_amount(amount)}\n"
                        f"🗓️ *Date:* {when} \\(IST\\)\n"
                        "─────────────────────\n"
                    )
                else:
                    method = md_escape(str(provider).upper()) if provider else "UPI"
                    body += (
                        "💳 *BALANCE DEPOSIT*\n"
                        f"💵 *Amount:* ₹{fmt_amount(amount)}\n"
                        f"🏦 *Method:* {method}\n"
                        f"🗓️ *Date:* {when} \\(IST\\)\n"
                        "─────────────────────\n"
                    )

        text = (
            "🧾 *ALL ORDERS & ACTIVITY* 🧾\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🛍️ Purchases: *{purchase_count}*\n"
            f"💳 Deposits: *{deposit_count}*\n"
            f"📊 Total Entries: *{total_entries}*\n"
            f"📄 Page: *{page + 1}/{total_pages}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n" + body
        )
        if len(text) > 4000:
            text = text[:3900] + "\n_\.\.\. page shortened_"

        history_keyboard = []
        nav = []
        if page > 0:
            nav.append(button("⬅️ Previous", f"user_history_page_{page - 1}"))
        if page < total_pages - 1:
            nav.append(button("Next ➡️", f"user_history_page_{page + 1}"))
        if nav:
            history_keyboard.append(nav)
        if total_entries > 0:
            history_keyboard.append([button("📄 Export All Orders", "user_export_orders")])
        else:
            history_keyboard.append([button(BTN_TEXT["btn_shop"], "user_shop")])
        history_keyboard.append([button(back_btn_txt, "main_menu")])

        await query.edit_message_text(
            text, parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(history_keyboard)
        )

    elif data == "user_export_orders":
        cursor = get_db_conn().cursor()
        cursor.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
        cnt = (cursor.fetchone() or [0])[0] or 0
        if cnt == 0:
            await query.answer("📭 No orders found to export.", show_alert=True)
            return
        await query.answer("⏳ Preparing your file...")
        try:
            txt = build_user_orders_txt(user_id)
            await send_txt_file(
                context.bot, user_id, txt, f"orders_{user_id}.txt",
                caption=f"📄 All your orders ({cnt})"
            )
        except Exception as e:
            logging.error(f"Failed to export orders for {user_id}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text="❌ Failed to create the file. Please try again.")
            except Exception:
                pass

    elif data == "user_shop":
        await query.answer()
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT pid, name, is_maintenance FROM products ORDER BY sort_order, name")
        products = cursor.fetchall()

        keyboard = []
        offer_pct = get_global_offer_percent()
        if not products:
            text = (
                "🎮 ━ CHOOSE YOUR GAME ━ 🎮\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "No products available yet. Please check back later."
            )
        else:
            text = SHOP_HEADER_TEXT
            if offer_pct > 0:
                text += f"\n\n🎁 SPECIAL OFFER: {offer_pct}% OFF on everything! 🎁"
            for p in products:
                pid, pname = p[0], p[1]
                in_maint = len(p) > 2 and p[2]
                label = f"🛠 {pname}" if in_maint else f"🛒 {pname}"
                keyboard.append([button(label, f"shop_prod_{pid}")])

        keyboard.append([button(back_btn_txt, "main_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("shop_prod_"):
        await query.answer()
        pid = data.split("_", 2)[2]
        await show_product_plans_menu(update, context, pid, user_id)

    elif data.startswith("buy_final_"):
        plan_id = data.split("_", 2)[2]
        try:
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT product_pid, duration, price, reseller_price FROM plans WHERE id = ?", (plan_id,))
            plan = cursor.fetchone()

            if not plan:
                await query.answer("❌ Plan not found!", show_alert=True)
                return

            cursor.execute("SELECT name, is_maintenance FROM products WHERE pid = ?", (plan[0],))
            prod_row = cursor.fetchone()
            prod_name = prod_row[0] if prod_row else "Unknown Product"
            in_maint = prod_row and len(prod_row) > 1 and prod_row[1]

            if in_maint:
                await query.answer()
                text = f"🛠 *{md_escape(prod_name)}*\n\nThis product is in maintenance now\\. Please check back later\\."
                await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]]))
                return

            cursor.execute("SELECT balance, is_reseller FROM users WHERE user_id = ?", (user_id,))
            b_row = cursor.fetchone()
            balance = b_row[0] if b_row and b_row[0] is not None else 0.0
            is_reseller = b_row[1] if b_row and b_row[1] is not None else 0

            cost = get_effective_price(user_id, plan_id, plan[2], plan[3], is_reseller, plan[0])
            await query.answer()

            context.user_data['order_pid'] = plan[0]
            context.user_data['order_plan_id'] = plan_id
            context.user_data.pop('order_coupon', None)

            if balance >= cost:
                context.user_data['order_flow'] = 'confirm'
                await show_confirm_purchase(query, context, plan_id, prod_name, plan[1], cost, balance)
            else:
                context.user_data['order_flow'] = 'summary'
                await show_order_summary(query, context, plan_id, prod_name, plan[1], cost)
        except Exception as e:
            logging.error(f"buy_final_ failed for user {user_id}, plan {plan_id}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text="⚠️ Something went wrong. Please try again or send /start.")
            except Exception:
                pass

    elif data == "order_redeem_coupon":
        await query.answer()
        context.user_data['state'] = 'WAIT_ORDER_COUPON_CODE'
        flow = context.user_data.get('order_flow', 'summary')
        back_cb = "confirm_back_purchase" if flow == 'confirm' else "order_back_summary"
        await query.edit_message_text(
            "🎟️ *Enter your coupon code:*",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, back_cb)]])
        )

    elif data == "order_back_summary":
        await query.answer()
        context.user_data['state'] = None
        plan_id = context.user_data.get('order_plan_id')
        if not plan_id:
            await start(update, context)
            return
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT product_pid, duration, price, reseller_price FROM plans WHERE id = ?", (plan_id,))
        plan = cursor.fetchone()
        if not plan:
            await start(update, context)
            return
        cursor.execute("SELECT name FROM products WHERE pid = ?", (plan[0],))
        prow = cursor.fetchone()
        cursor.execute("SELECT is_reseller FROM users WHERE user_id = ?", (user_id,))
        r = cursor.fetchone()
        is_reseller = r[0] if r else 0
        cost = get_effective_price(user_id, plan_id, plan[2], plan[3], is_reseller, plan[0])
        await show_order_summary(query, context, plan_id, prow[0] if prow else "Product", plan[1], cost)

    elif data == "confirm_back_purchase":
        await query.answer()
        context.user_data['state'] = None
        plan_id = context.user_data.get('order_plan_id')
        if not plan_id:
            await start(update, context)
            return
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT product_pid, duration, price, reseller_price FROM plans WHERE id = ?", (plan_id,))
        plan = cursor.fetchone()
        if not plan:
            await start(update, context)
            return
        cursor.execute("SELECT name FROM products WHERE pid = ?", (plan[0],))
        prow = cursor.fetchone()
        cursor.execute("SELECT balance, is_reseller FROM users WHERE user_id = ?", (user_id,))
        r = cursor.fetchone()
        balance = r[0] if r and r[0] is not None else 0.0
        is_reseller = r[1] if r else 0
        cost = get_effective_price(user_id, plan_id, plan[2], plan[3], is_reseller, plan[0])
        await show_confirm_purchase(query, context, plan_id, prow[0] if prow else "Product", plan[1], cost, balance)

    elif data.startswith("order_pay_upi_"):
        plan_id = data.split("order_pay_upi_", 1)[1]
        await query.answer()
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT product_pid, duration, price, reseller_price FROM plans WHERE id = ?", (plan_id,))
        plan = cursor.fetchone()
        if not plan:
            await query.edit_message_text("❌ Plan not found.")
            return
        cursor.execute("SELECT name FROM products WHERE pid = ?", (plan[0],))
        prow = cursor.fetchone()
        prod_name = prow[0] if prow else "Product"
        cursor.execute("SELECT balance, is_reseller FROM users WHERE user_id = ?", (user_id,))
        b = cursor.fetchone()
        balance = b[0] if b and b[0] is not None else 0.0
        is_reseller = b[1] if b and b[1] is not None else 0
        base_cost = get_effective_price(user_id, plan_id, plan[2], plan[3], is_reseller, plan[0])

        coupon = context.user_data.get('order_coupon')
        final_cost = base_cost
        if coupon:
            final_cost = round(base_cost * (1 - coupon['discount'] / 100), 2)

        try:
            await query.message.delete()
        except Exception:
            pass

        loading_msg = await context.bot.send_message(
            chat_id=user_id,
            text="⏳ *Generating QR\\.\\.\\.*",
            parse_mode="MarkdownV2"
        )

        ok, info = await raise_payment_qr(
            context, user_id, final_cost, balance, purpose='plan', plan_id=plan_id,
            label=f"{prod_name} ({plan[1]})", item_name=prod_name, plan_label=plan[1], price=final_cost
        )
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if coupon and ok:
            redeem_coupon_for_user(coupon['code'], user_id)
            coupon_row = get_coupon(coupon['code'])
            if coupon_row:
                await notify_admin_coupon_used(context, user_id, coupon_row)
            context.user_data.pop('order_coupon', None)

        if not ok:
            back_kb = InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
            if info == "PAYMENT_MAINTENANCE":
                await context.bot.send_message(chat_id=user_id, text=PAYMENT_MAINTENANCE_MSG, parse_mode="MarkdownV2", reply_markup=back_kb)
            elif info == "UPI_NOT_SET":
                await context.bot.send_message(chat_id=user_id, text="❌ *UPI Not Set\\!*\n\nPlease contact admin to enable payments\\.", parse_mode="MarkdownV2", reply_markup=back_kb)
            else:
                await context.bot.send_message(chat_id=user_id, text="❌ Payment QR generation failed. Please try again later.", reply_markup=back_kb)

    elif data.startswith("confirm_buy_"):
        plan_id = data.split("_", 2)[2]

        if user_id in PROCESSING_PURCHASES:
            await query.answer("⏳ Your purchase is already being processed, please wait...", show_alert=True)
            return
        PROCESSING_PURCHASES.add(user_id)

        try:
            await query.answer()
            try:
                await query.message.delete()
            except Exception:
                pass

            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT product_pid, duration, price, reseller_price FROM plans WHERE id = ?", (plan_id,))
            plan = cursor.fetchone()

            if not plan:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ This plan no longer exists.",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
                return

            cursor.execute("SELECT name, is_maintenance FROM products WHERE pid = ?", (plan[0],))
            prod_row = cursor.fetchone()
            prod_name = prod_row[0] if prod_row else "Unknown Product"
            in_maint = prod_row and len(prod_row) > 1 and prod_row[1]

            if in_maint:
                text = f"🛠 *{md_escape(prod_name)}*\n\nThis product is in maintenance now\\. Please check back later\\."
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
                return

            cursor.execute("SELECT balance, is_reseller FROM users WHERE user_id = ?", (user_id,))
            b_row = cursor.fetchone()
            balance = b_row[0] if b_row and b_row[0] is not None else 0.0
            is_reseller = b_row[1] if b_row and b_row[1] is not None else 0

            cost = get_effective_price(user_id, plan_id, plan[2], plan[3], is_reseller, plan[0])

            coupon = context.user_data.get('order_coupon')
            coupon_discount = coupon['discount'] if coupon else 0
            final_cost = round(cost * (1 - coupon_discount / 100), 2) if coupon_discount else cost

            if balance < final_cost:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Your balance is no longer sufficient\\. Please try again from *Shop Now*\\.",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
                return

            success, text, group_link, pid, prod_name2 = await finalize_purchase(context, user_id, plan_id, purchase_type="Manual/Balance", coupon_discount=coupon_discount)
            if success and coupon:
                redeem_coupon_for_user(coupon['code'], user_id)
                coupon_row = get_coupon(coupon['code'])
                if coupon_row:
                    await notify_admin_coupon_used(context, user_id, coupon_row)
                context.user_data.pop('order_coupon', None)
            # Use normal Markdown for the purchase receipt. If Telegram rejects
            # formatting, deliver_purchase_result() automatically retries plain text.
            await deliver_purchase_result(context, user_id, text, back_btn_txt, group_link, parse_mode="Markdown")
            if success and pid:
                try:
                    await deliver_product_apks(context, user_id, pid, prod_name2)
                except Exception as apk_error:
                    # APK delivery must not turn an already successful purchase
                    # into the generic purchase-failed message.
                    logging.error(f"APK delivery failed after successful purchase for user {user_id}: {apk_error}")
        except Exception as e:
            logging.error(f"Purchase confirmation failed for user {user_id}, plan {plan_id}: {e}")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Something went wrong while completing your purchase. If your balance was deducted, please contact Admin Support.",
                    reply_markup=InlineKeyboardMarkup([[button(back_btn_txt, "main_menu")]])
                )
            except Exception:
                pass
        finally:
            PROCESSING_PURCHASES.discard(user_id)


# ============================================================
# ADMIN PANEL
# ============================================================
ADMIN_PANEL_TEXT = (
    "⚡━━━━━━━━━━━━━━━━━━━━⚡\n"
    "   🛡️✨ 𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐍𝐓𝐑𝐎𝐋 𝐏𝐀𝐍𝐄𝐋 ✨🛡️\n"
    "⚡━━━━━━━━━━━━━━━━━━━━⚡\n\n"
    "👑 Welcome back, Boss! 👑\n\n"
    "🛒 Manage Products & Plans 📦\n"
    "🔑 Manage Keys & Stock 🗝️\n"
    "💲 Manage Pricing & 💳 Balance\n"
    "🎟️ Coupons & 🎰 Lucky Spin\n"
    "👥 Resellers & 💠 Payments\n"
    "🛡️ Admin Management\n"
    "🚫 Ban Management\n"
    "📢 Broadcast, 💾 Backup & More 🚀\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "👇✨ Select an option below to get started! ✨👇"
)

ADMIN_MENU_KEYS = (
    "product", "keys", "pricing", "coupon",
    "balance", "reseller", "additional", "extra", "settings",
    "adminmgmt", "banmgmt",
)

BROADCAST_ADMIN_PANEL_TEXT = (
    "🛡️ **ADMIN PANEL (Limited)**\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "📢 You have **Broadcast-only** access.\n\n"
    "👇 Tap Broadcast to send a message to all users."
)


def get_admin_panel_markup():
    flat_buttons = [
        ("📦 Manage Product", "admin_menu_product"),
        ("🔑 Keys Management", "admin_menu_keys"),
        ("💲 Pricing Management", "admin_menu_pricing"),
        ("🎟️ Coupon Management", "admin_menu_coupon"),
        ("💳 Balance Management", "admin_menu_balance"),
        ("👥 Reseller Management", "admin_menu_reseller"),
        ("➕ Additional Features", "admin_menu_additional"),
        ("✨ Extra Features", "admin_menu_extra"),
        ("⚙️ Settings", "admin_menu_settings"),
        ("🛡️ Admin Management", "admin_menu_adminmgmt"),
        ("🚫 Ban Management", "admin_menu_banmgmt"),
        ("💠 Payment Settings", "admin_set_payment"),
        ("📢 Broadcast", "admin_broadcast"),
        ("💾 Backup", "admin_backup"),
        ("♻️ Restore", "admin_restore"),
        ("🤖 AI Support", "admin_ai_support"),
    ]
    keyboard = zigzag_rows(flat_buttons)
    keyboard.append([button("🔙 Back To Main Menu", "main_menu")])
    return keyboard


def get_broadcast_admin_panel_markup():
    return [
        [button("📢 Broadcast", "admin_broadcast")],
        [button("🔙 Back To Main Menu", "main_menu")],
    ]


def build_admin_submenu(menu_key):
    buttons = []
    sep = "━━━━━━━━━━━━━━━━━━━━"

    if menu_key == "product":
        text = (f"📦 **MANAGE PRODUCT** 📦\n{sep}\n\n"
                "Manage your products and their plans here.\n\n👇 Choose an option:")
        buttons = [
            ("➕ Add Product", "admin_add_prod"),
            ("🗑️ Remove Product", "admin_rem_prod"),
            ("➕ Add Plan", "admin_add_plan"),
            ("🗑️ Remove Plan", "admin_rem_plan"),
            ("✏️ Edit Plan Info", "admin_edit_plan_info"),
            ("✏️ Rename Product", "admin_change_name"),
            ("🆔 Change PID", "admin_change_pid"),
            ("↕️ Line Up Products", "admin_lineup_prod"),
        ]

    elif menu_key == "keys":
        text = (f"🔑 **KEYS MANAGEMENT** 🔑\n{sep}\n\n"
                "Manage your local key stock here.\n\n👇 Choose an option:")
        buttons = [
            ("🔑 Add Keys", "admin_add_keys"),
            ("🗑️ Remove Keys", "admin_rem_keys"),
            ("🔍 Check Keys Stock", "admin_check_keys"),
        ]

    elif menu_key == "pricing":
        offer_pct = get_global_offer_percent()
        offer_info = f"🎁 **Current All Users Offer:** {offer_pct}% OFF\n\n" if offer_pct > 0 else ""
        text = (f"💲 **PRICING MANAGEMENT** 💲\n{sep}\n\n"
                f"{offer_info}"
                "Set plan prices, reseller prices, custom prices and product offers here.\n\n👇 Choose an option:")
        buttons = [
            ("💲 Change Product Price", "admin_change_price"),
            ("⚙️ Set Plan Reseller Price", "admin_set_reseller_price"),
            ("💎 Set Upgrade Price", "admin_set_upgrade_price"),
            ("💵 Set Custom Price", "admin_set_custom_price"),
            ("🗑️ Remove Custom Price", "admin_rem_custom_price"),
            ("📋 See All Prices", "admin_view_all_prices"),
            ("🎁 Get All Users Offer", "admin_set_global_offer"),
            ("🎯 Product Offer", "admin_prod_offer"),
        ]

    elif menu_key == "coupon":
        try:
            s = get_spin_settings()
            better = round(max(0.0, 100 - s['coup_chance'] - s['bal_chance']), 2)
            odds = (
                f"🎰 **Lucky Spin Odds:** Coupon {s['coup_chance']}% | Balance {s['bal_chance']}% | Better Luck {better}%\n"
                f"💰 Balance Range: ₹{s['bal_min']} – ₹{s['bal_max']}\n"
                f"🎫 Coupon Range: {s['coup_min']}% – {s['coup_max']}%\n\n"
            )
        except Exception:
            odds = ""
        text = (f"🎟️ **COUPON MANAGEMENT** 🎟️\n{sep}\n\n"
                f"{odds}"
                "Manage coupons and Lucky Spin (balance/coupon) settings here.\n\n👇 Choose an option:")
        buttons = [
            ("🎟️ Create Coupon", "admin_create_coupon"),
            ("📋 View Coupons", "admin_view_coupons"),
            ("🎰 Spin Balance Range", "admin_set_spin_balance"),
            ("🎰 Spin Coupon Range", "admin_set_spin_coupon"),
            ("🎰 Spin Chances", "admin_set_spin_chance"),
        ]

    elif menu_key == "balance":
        text = (f"💳 **BALANCE MANAGEMENT** 💳\n{sep}\n\n"
                "Manage user wallet balances here.\n\n👇 Choose an option:")
        buttons = [
            ("➕ Add Balance", "admin_add_bal"),
            ("➖ Remove Balance", "admin_rem_bal"),
            ("🔍 Check Balance", "admin_check_bal"),
        ]

    elif menu_key == "reseller":
        text = (f"👥 **RESELLER MANAGEMENT** 👥\n{sep}\n\n"
                "Manage resellers here.\n\n👇 Choose an option:")
        buttons = [
            ("➕ Add Reseller", "admin_add_reseller"),
            ("➖ Remove Reseller", "admin_rem_reseller"),
            ("👥 My Resellers", "admin_my_resellers"),
        ]

    elif menu_key == "additional":
        text = (f"➕ **ADDITIONAL FEATURES** ➕\n{sep}\n\n"
                "Extra product options.\n\n👇 Choose an option:")
        buttons = [
            ("🔗 Product Deep Links", "admin_prod_links"),
            ("🎥 Set Product Video", "admin_set_prod_video"),
            ("📝 Set Description", "admin_set_prod_desc"),
            ("📥 Set APKs on Product", "admin_set_prod_apks"),
            ("📱 Get All Num", "admin_get_all_num"),
        ]

    elif menu_key == "extra":
        maint_on = is_bot_maintenance()
        reseller_on = is_reseller_enabled()
        all_reseller_on = is_all_reseller_price()
        text = (f"✨ **EXTRA FEATURES** ✨\n{sep}\n\n"
                "Stats, maintenance and reseller toggles.\n\n👇 Choose an option:")
        buttons = [
            ("📊 Bot Stats", "admin_bot_stats"),
            ("🧾 Check All Payments", "admin_check_payments"),
            ("🛠 Bot Maintenance: ON" if maint_on else "✅ Bot Maintenance: OFF",
             "admin_toggle_maintenance"),
            ("🛠 Maintenance Mode", "admin_maintenance_prod"),
            ("🌍 All User Reseller: ON" if all_reseller_on else "🌍 All User Reseller: OFF",
             "admin_toggle_all_reseller"),
            ("🌟 Reseller Menu: ON" if reseller_on else "🚫 Reseller Menu: OFF",
             "admin_toggle_reseller_menu"),
        ]

    elif menu_key == "settings":
        text = (f"⚙️ **SETTINGS** ⚙️\n{sep}\n\n"
                f"🌐 **Default API:** `{API_URL}`\n"
                f"🔑 **API Key:** `{mask_secret(get_store_api_key())}`\n\n"
                "👇 Choose an option:")
        buttons = [
            ("🔑 Auto API Setting", "admin_set_store_api"),
            ("🤖 Set AI API Key", "admin_set_ai_key"),
            ("🎬 Set HowToVideo", "admin_set_how_video"),
            ("☎️ Set Support Link", "admin_set_support_uname"),
        ]

    elif menu_key == "adminmgmt":
        cursor = get_db_conn().cursor()
        cursor.execute("SELECT COUNT(*) FROM admins")
        count = (cursor.fetchone() or [0])[0] or 0
        text = (
            f"🛡️ **ADMIN MANAGEMENT** 🛡️\n"
            f"{sep}\n\n"
            f"👑 **Main Admin (You):** Full access\n"
            f"📢 **Broadcast Admins:** {count} (broadcast only)\n\n"
            f"Broadcast admins can only send broadcasts — they cannot access any other admin feature.\n\n"
            f"👇 Choose an option:"
        )
        buttons = [
            ("➕ Add Admin", "admin_add_admin"),
            ("➖ Remove Admin", "admin_rem_admin"),
            ("📋 Check Admins", "admin_check_admins"),
        ]

    # ===== Ban Management submenu =====
    elif menu_key == "banmgmt":
        banned_count = len(get_all_banned_users())
        text = (
            f"🚫 **BAN MANAGEMENT** 🚫\n"
            f"{sep}\n\n"
            f"📊 **Banned Users:** {banned_count}\n\n"
            f"Manage banned users here. Banned users cannot use the bot at all — "
            f"they are notified when banned/unbanned.\n\n"
            f"👇 Choose an option:"
        )
        buttons = [
            ("🔨 Ban User", "admin_ban_user"),
            ("✅ Unban User", "admin_unban_user"),
            ("📋 Check Ban List", "admin_check_ban_list"),
            ("📄 Export Ban List", "admin_export_ban_list"),
        ]

    else:
        return ADMIN_PANEL_TEXT, get_admin_panel_markup()

    keyboard = zigzag_rows(buttons)
    keyboard.append([back_button("admin_main_panel", "Back To Admin Panel")])
    return text, keyboard


def render_admin_view(context):
    menu = None
    try:
        menu = context.user_data.get('admin_menu')
    except Exception:
        menu = None
    if menu in ADMIN_MENU_KEYS:
        return build_admin_submenu(menu)
    return ADMIN_PANEL_TEXT, get_admin_panel_markup()


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_main_admin(user_id):
        context.user_data.pop('admin_menu', None)
        context.user_data['state'] = None
        keyboard = get_admin_panel_markup()
        await update.message.reply_text(ADMIN_PANEL_TEXT, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if is_broadcast_admin(user_id):
        context.user_data['state'] = None
        context.user_data.pop('admin_menu', None)
        await update.message.reply_text(
            BROADCAST_ADMIN_PANEL_TEXT,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
        )
        return

    return


async def reopen_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, confirm_msg: str = None):
    if confirm_msg:
        await update.message.reply_text(confirm_msg, parse_mode="Markdown")
    user_id = update.effective_user.id if update.effective_user else 0
    if is_main_admin(user_id):
        text, keyboard = render_admin_view(context)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif is_broadcast_admin(user_id):
        await update.message.reply_text(
            BROADCAST_ADMIN_PANEL_TEXT,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
        )


async def refresh_admin_panel(context: ContextTypes.DEFAULT_TYPE, query, message_text=None):
    text, keyboard = render_admin_view(context)
    if message_text:
        text = message_text
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logging.error(f"Failed to refresh admin panel: {e}")


async def safe_edit_message_text(query, text, parse_mode=None, reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        if "not modified" in str(e).lower():
            try:
                await query.answer("✅ Already up to date.")
            except Exception:
                pass
        else:
            logging.error(f"safe_edit_message_text failed: {e}")
            try:
                await query.answer("⚠️ Failed to refresh, try again.", show_alert=True)
            except Exception:
                pass


def build_product_offer_screen(pid):
    """Returns (text, keyboard) for a single product's offer screen."""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
    row = cursor.fetchone()
    pname = escape_markdown(row[0]) if row else escape_markdown(pid)
    pct, target = get_product_offer(pid)

    if pct > 0:
        status = f"✅ **Active:** {fmt_pct(pct)}% OFF → {OFFER_TARGET_LABELS[target]}"
        if target == "custom":
            uids = get_product_offer_custom_users(pid)
            shown = ", ".join(f"`{u}`" for u in uids[:30])
            more = f" …+{len(uids) - 30} more" if len(uids) > 30 else ""
            status += f"\n👥 **Custom Users ({len(uids)}):** {shown}{more}"
    else:
        status = "❌ **Active:** No offer on this product"

    text = (
        f"🎯 **PRODUCT OFFER — {pname}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 **PID:** `{pid}`\n"
        f"{status}\n\n"
        f"👇 Choose who should get the discount on this product.\n"
        f"Only the selected group will see the offer price."
    )
    keyboard = [
        [button("👑 Reseller", "prodoffer_tgt_reseller"), button("👤 Users", "prodoffer_tgt_users")],
        [button("🎯 Custom Users", "prodoffer_tgt_custom"), button("🌍 All Users", "prodoffer_tgt_all")],
    ]
    if pct > 0:
        keyboard.append([button("❌ Remove Offer", "prodoffer_remove")])
    keyboard.append([back_button("admin_prod_offer")])
    return text, keyboard


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not is_any_admin(user_id):
        return

    if not is_main_admin(user_id):
        allowed = {
            "admin_broadcast",
            "admin_broadcast_cancel",
            "admin_back_panel",
            "admin_main_panel",
        }
        if query.data not in allowed:
            await query.answer("❌ You only have Broadcast access.", show_alert=True)
            return

    await query.answer()
    data = query.data

    conn = get_db_conn()
    cursor = conn.cursor()

    if data == "admin_main_panel":
        context.user_data.pop('admin_menu', None)
        context.user_data['state'] = None
        if is_main_admin(user_id):
            await refresh_admin_panel(context, query)
        else:
            try:
                await query.edit_message_text(
                    BROADCAST_ADMIN_PANEL_TEXT,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
                )
            except Exception as e:
                logging.error(f"Failed to restore broadcast admin panel: {e}")

    elif data.startswith("admin_menu_"):
        key = data.split("admin_menu_", 1)[1]
        if key in ADMIN_MENU_KEYS:
            context.user_data['admin_menu'] = key
            context.user_data['state'] = None
        await refresh_admin_panel(context, query)

    elif data == "admin_back_panel":
        context.user_data['state'] = None
        if is_main_admin(user_id):
            await refresh_admin_panel(context, query)
        else:
            try:
                await query.edit_message_text(
                    BROADCAST_ADMIN_PANEL_TEXT,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
                )
            except Exception:
                pass

    # ===== PRODUCT OFFER (NEW) =====
    elif data == "admin_prod_offer":
        context.user_data['state'] = None
        for k in ('prodoffer_pid', 'prodoffer_target', 'prodoffer_pct', 'api_variant_pid'):
            context.user_data.pop(k, None)
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pct, target = get_product_offer(p[0])
            if pct > 0:
                short = {"reseller": "Reseller", "users": "Users", "custom": "Custom", "all": "All"}[target]
                label = f"🎯 {p[1]} ({fmt_pct(pct)}% • {short})"
            else:
                label = f"➕ {p[1]} (No Offer)"
            keyboard.append([button(label, f"prodoffer_prod_{p[0]}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text(
            "🎯 **PRODUCT OFFER** 🎯\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Select a product to set a discount on **that product only**.\n"
            "You can give it to Reseller / Users / Custom Users / All Users.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("prodoffer_prod_"):
        pid = data.split("prodoffer_prod_", 1)[1]
        cursor.execute("SELECT 1 FROM products WHERE pid = ?", (pid,))
        if not cursor.fetchone():
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['state'] = None
        context.user_data['prodoffer_pid'] = pid
        text, keyboard = build_product_offer_screen(pid)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("prodoffer_tgt_"):
        target = data.split("prodoffer_tgt_", 1)[1]
        pid = context.user_data.get('prodoffer_pid')
        if not pid or target not in OFFER_TARGET_LABELS:
            await query.answer("❌ Session expired, please select the product again.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        context.user_data['prodoffer_target'] = target
        context.user_data['state'] = 'WAIT_PRODUCT_OFFER_PERCENT'
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prow = cursor.fetchone()
        pname = escape_markdown(prow[0]) if prow else escape_markdown(pid)
        await query.edit_message_text(
            f"🎯 **PRODUCT OFFER — {pname}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 **Target:** {OFFER_TARGET_LABELS[target]}\n\n"
            f"➡️ Send the discount percentage (1-100):\n"
            f"*(Example: `5` or `10`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "prodoffer_remove":
        pid = context.user_data.get('prodoffer_pid')
        if not pid:
            await query.answer("❌ Session expired, please select the product again.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        clear_product_offer(pid)
        await query.answer("✅ Offer removed!", show_alert=True)
        text, keyboard = build_product_offer_screen(pid)
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    # ===== BAN MANAGEMENT callbacks =====
    elif data == "admin_ban_user":
        context.user_data['state'] = 'WAIT_BAN_USER_ID'
        await query.edit_message_text(
            "🔨 **BAN USER** 🔨\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "➡️ Send the User ID to ban:\n"
            "*(Example: `123456789`)*\n\n"
            "💡 You can also add a reason after the ID:\n"
            "*(Example: `123456789 spamming`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_unban_user":
        context.user_data['state'] = 'WAIT_UNBAN_USER_ID'
        await query.edit_message_text(
            "✅ **UNBAN USER** ✅\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "➡️ Send the User ID to unban:\n"
            "*(Example: `123456789`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_check_ban_list":
        banned = get_all_banned_users()
        text = "🚫 **BANNED USERS LIST** 🚫\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not banned:
            text += "✅ No banned users\\."
        else:
            text += f"📊 Total Banned: {len(banned)}\n\n"
            for i, (uid, fname, uname, reason) in enumerate(banned[:30], 1):
                name_disp = md_escape(fname) if fname else "N/A"
                uname_disp = f"@{md_escape(uname)}" if uname else "N/A"
                reason_disp = md_escape(reason) if reason else "No reason"
                text += (
                    f"#{i}\n"
                    f"🆔 *User ID:* `{uid}`\n"
                    f"👤 *Name:* {name_disp}\n"
                    f"🏷️ *Username:* {uname_disp}\n"
                    f"📝 *Reason:* {reason_disp}\n"
                    f"─────────────────────\n"
                )
            if len(banned) > 30:
                text += f"\n_…and {len(banned) - 30} more\\. Use Export Ban List for the full list\\._"
        text += "\n━━━━━━━━━━━━━━━━━━━━"
        await safe_edit_message_text(query, text[:4000], parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))

    elif data == "admin_export_ban_list":
        try:
            txt = build_ban_list_txt()
            banned = get_all_banned_users()
            await send_txt_file(
                context.bot, ADMIN_ID, txt, "ban_list.txt",
                caption=f"🚫 Ban List ({len(banned)} users)"
            )
            await query.edit_message_text(
                f"✅ **Ban List** file sent above 👆\n\nTotal banned: {len(banned)}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )
        except Exception as e:
            logging.error(f"Failed to send ban list txt: {e}")
            await query.edit_message_text(
                f"❌ Failed to create the file: {e}",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )

    # ===== Admin Management callbacks =====
    elif data == "admin_add_admin":
        context.user_data['state'] = 'WAIT_ADD_ADMIN_ID'
        await query.edit_message_text(
            "➕ **ADD ADMIN** ➕\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "New admins get **Broadcast-only** access.\n"
            "They will see only a Broadcast button when they send /admin.\n\n"
            "➡️ Send the User ID to promote:\n"
            "*(Example: `123456789`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_rem_admin":
        admins = get_all_broadcast_admins()
        if not admins:
            await query.edit_message_text(
                "🛡️ **REMOVE ADMIN**\n\n⚠️ No broadcast admins added yet.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )
            return
        keyboard = []
        for a in admins:
            uid, fname, uname = a
            label = escape_markdown(fname) if fname else str(uid)
            keyboard.append([button(f"❌ {label} ({uid})", f"deladmin_{uid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text(
            "🛡️ **REMOVE ADMIN**\n\nSelect a broadcast admin to remove:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("deladmin_"):
        try:
            uid = int(data.split("_", 1)[1])
        except ValueError:
            await query.answer("❌ Invalid ID.", show_alert=True)
            return
        removed = remove_broadcast_admin(uid)
        if removed:
            await query.answer("✅ Admin removed!", show_alert=True)
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text="⚠️ *Your admin access has been removed.*",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            await query.answer("❌ Admin not found.", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_check_admins":
        owner = get_user(ADMIN_ID)
        owner_name = escape_markdown(owner[2]) if owner and owner[2] else "Owner"
        owner_uname = f"@{escape_markdown(owner[1])}" if owner and owner[1] else "N/A"

        admins = get_all_broadcast_admins()

        text = (
            f"🛡️ **ADMIN LIST** 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👑 **Main Admin (Full Access)**\n"
            f"👤 **Name:** {owner_name}\n"
            f"🏷️ **Username:** {owner_uname}\n"
            f"🆔 **User ID:** `{ADMIN_ID}`\n"
            f"------------------------------------\n"
        )

        if not admins:
            text += "\n📢 No broadcast admins added yet."
        else:
            text += f"\n📢 **Broadcast Admins ({len(admins)})**\n\n"
            for i, a in enumerate(admins, 1):
                uid, fname, uname = a
                fname_disp = escape_markdown(fname) if fname else "N/A"
                uname_disp = f"@{escape_markdown(uname)}" if uname else "N/A"
                text += (
                    f"#{i}\n"
                    f"👤 **Name:** {fname_disp}\n"
                    f"🏷️ **Username:** {uname_disp}\n"
                    f"🆔 **User ID:** `{uid}`\n"
                    f"------------------------------------\n"
                )

        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text[:4000], parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_toggle_maintenance":
        new_state = not is_bot_maintenance()
        set_bot_maintenance(new_state)
        await query.answer("🛠 Bot Maintenance ENABLED" if new_state else "✅ Bot Maintenance DISABLED — the bot is live for everyone again!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_toggle_reseller_menu":
        new_state = not is_reseller_enabled()
        set_reseller_enabled(new_state)
        await query.answer(f"🌟 Reseller Menu {'ENABLED' if new_state else 'DISABLED'}!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_toggle_all_reseller":
        new_state = not is_all_reseller_price()
        set_all_reseller_price(new_state)
        await query.answer(
            "🌍 All User Reseller ENABLED — every user now pays reseller price!" if new_state
            else "🌍 All User Reseller DISABLED — normal users pay normal price again.",
            show_alert=True
        )
        await refresh_admin_panel(context, query)

    elif data == "admin_set_global_offer":
        curr = get_global_offer_percent()
        context.user_data['state'] = 'WAIT_GLOBAL_OFFER_PERCENT'
        await query.edit_message_text(
            f"🎁 **GET ALL USERS OFFER** 🎁\n\n"
            f"Current Offer: {curr}% OFF for ALL users\n\n"
            f"➡️ Send the discount percentage to apply for EVERY user, on EVERY product "
            f"(on top of their normal/reseller price):\n*(Example: `15` for 15% OFF)*\n\n"
            f"Send `0` to disable the offer and restore normal pricing.",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_view_all_prices":
        try:
            txt = build_all_prices_txt()
            await send_txt_file(
                context.bot, ADMIN_ID, txt, "all_prices.txt",
                caption="📋 All Product Prices (Product / Plan / Normal / Reseller / Variant)"
            )
            await query.edit_message_text(
                "✅ **All Prices** file sent above 👆",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )
        except Exception as e:
            logging.error(f"Failed to send all prices txt: {e}")
            await query.edit_message_text(
                f"❌ Failed to create the file: {e}",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )

    elif data == "admin_get_all_num":
        try:
            txt = build_all_numbers_txt()
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_verified = 1")
            cnt = cursor.fetchone()[0] or 0
            await send_txt_file(
                context.bot, ADMIN_ID, txt, "all_verified_users.txt",
                caption=f"📱 All Verified Users ({cnt}) — Username / Name / Number / User ID"
            )
            await query.edit_message_text(
                f"✅ **Get All Num** file sent above 👆\n\nTotal verified users: {cnt}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )
        except Exception as e:
            logging.error(f"Failed to send all numbers txt: {e}")
            await query.edit_message_text(
                f"❌ Failed to create the file: {e}",
                reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]])
            )

    elif data == "admin_bot_stats":
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM users WHERE has_purchased = 1")
        active_buyers = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history")
        total_sales_count, total_sales_amt = cursor.fetchone()
        total_sales_count = total_sales_count or 0
        total_sales_amt = total_sales_amt or 0.0
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_reseller = 1")
        total_resellers = cursor.fetchone()[0] or 0
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history
            WHERE date(datetime(date, '+5 hours', '+30 minutes')) = date(datetime('now', '+5 hours', '+30 minutes'))
        """)
        today_sales_count, today_sales_amt = cursor.fetchone()
        today_sales_count = today_sales_count or 0
        today_sales_amt = today_sales_amt or 0.0
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(price), 0) FROM history
            WHERE strftime('%Y-%m', datetime(date, '+5 hours', '+30 minutes'))
                = strftime('%Y-%m', datetime('now', '+5 hours', '+30 minutes'))
        """)
        month_sales_count, month_sales_amt = cursor.fetchone()
        month_sales_count = month_sales_count or 0
        month_sales_amt = month_sales_amt or 0.0
        cursor.execute("SELECT COUNT(*) FROM products")
        total_products = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments
            WHERE status = 'SUCCESS'
              AND date(datetime(date, '+5 hours', '+30 minutes')) = date(datetime('now', '+5 hours', '+30 minutes'))
        """)
        today_pay_count, today_pay_amt = cursor.fetchone()
        today_pay_count = today_pay_count or 0
        today_pay_amt = today_pay_amt or 0.0

        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_count = cursor.fetchone()[0] or 0

        text = (
            f"📊 **BOT STATS (LIVE)** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 **Total Registered Users:** {total_users}\n"
            f"🛒 **Active Buyers:** {active_buyers}\n"
            f"💰 **Total Sales:** {total_sales_count} orders (₹{total_sales_amt:.2f})\n"
            f"🌟 **Total Resellers:** {total_resellers}\n"
            f"🚫 **Banned Users:** {banned_count}\n"
            f"📅 **Today's Sales:** {today_sales_count} orders (₹{today_sales_amt:.2f})\n"
            f"🗓️ **This Month:** {month_sales_count} orders (₹{month_sales_amt:.2f})\n"
            f"📦 **Total Active Products:** {total_products}\n"
            f"💳 **Today's Payment Received:** {today_pay_count} txns (₹{today_pay_amt:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = [
            [button("🔄 Refresh", "admin_bot_stats")],
            [button("🧾 Check All Payments", "admin_check_payments")],
            [back_button("admin_back_panel")]
        ]
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_check_payments":
        cursor.execute("""
            SELECT payments.amount, payments.provider, payments.date, payments.user_id,
                   payments.order_id, users.full_name, users.username
            FROM payments LEFT JOIN users ON payments.user_id = users.user_id
            WHERE payments.status = 'SUCCESS'
              AND date(datetime(payments.date, '+5 hours', '+30 minutes'))
                  = date(datetime('now', '+5 hours', '+30 minutes'))
            ORDER BY (payments.date IS NULL), payments.date DESC
        """)
        rows = cursor.fetchall()

        text = "🧾 **TODAY'S PAYMENTS** 🧾\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not rows:
            text += "⚠️ No payments received today yet."
        else:
            total = 0.0
            truncated = False
            for r in rows:
                amt, provider, pay_date, uid, order_id, fname, uname = r
                amt = amt or 0.0
                total += amt
                prov_disp = (provider or "fampay").upper()
                name_disp = escape_markdown(fname) if fname else "N/A"
                uname_disp = f"@{escape_markdown(uname)}" if uname else "N/A"
                when = to_ist_str(pay_date)
                entry = (
                    f"💵 **Amount:** ₹{amt:.2f}\n"
                    f"💠 **Type:** {prov_disp}\n"
                    f"👤 **Name:** {name_disp}\n"
                    f"🏷️ **Username:** {uname_disp}\n"
                    f"🆔 **User ID:** `{uid}`\n"
                    f"🕒 **Time:** {when} (IST)\n"
                    f"------------------------------------\n"
                )
                if len(text) + len(entry) > 3600:
                    truncated = True
                    break
                text += entry
            text += f"\n💰 **Total Received Today:** ₹{total:.2f}"
            if truncated:
                text += "\n*(List truncated due to message length limit — use Refresh after clearing older ones if needed)*"

        keyboard = [
            [button("🔄 Refresh", "admin_check_payments")],
            [button("📊 Back To Bot Stats", "admin_bot_stats")],
            [back_button("admin_back_panel")]
        ]
        await safe_edit_message_text(query, text[:4090], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_set_support_uname":
        curr = get_support_username()
        context.user_data['state'] = 'WAIT_SUPPORT_UNAME'
        await query.edit_message_text(
            f"☎️ **SET SUPPORT USERNAME**\n\n"
            f"Current Username: `@{curr}`\n\n"
            f"➡️ Send the new Telegram username (without @):\n*(Example: `Rohit_BhaiTOP`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_add_prod":
        context.user_data['state'] = 'WAIT_PROD_NAMES_BULK'
        text = (
            "➕ **ADD PRODUCT(S)** ➕\n\n"
            "➡️ **Step 1/2:** Send the product name(s), one per line:\n"
            "*(Example:*\n`Drip`\n`Hg`\n`Prime`*)*\n\n"
            "One product will be created for each name you send."
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_rem_prod":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products to remove.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"❌ {p[1]} ({p[0]})", f"del_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select product to remove:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("DELETE FROM products WHERE pid = ?", (pid,))
        cursor.execute("DELETE FROM plans WHERE product_pid = ?", (pid,))
        cursor.execute("DELETE FROM keys WHERE product_pid = ?", (pid,))
        cursor.execute("DELETE FROM product_offer_users WHERE pid = ?", (pid,))
        conn.commit()
        await query.answer("✅ Product removed!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_add_plan":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("❌ Add a product first!", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"📦 {p[1]}", f"add_plan_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Product for Plan:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("add_plan_prod_"):
        pid = data.split("_", 3)[3]
        context.user_data['temp_pid'] = pid
        context.user_data['state'] = 'WAIT_PLAN_DURATIONS_BULK'
        await query.edit_message_text(
            "➡️ **Step 1/5:** Send the plan duration(s), one per line:\n"
            "*(Example:*\n`1 Hour`\n`2 Days`*)*\n\n"
            "One plan will be created for each duration you send.",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_rem_plan":
        cursor.execute("SELECT plans.id, products.name, plans.duration FROM plans JOIN products ON plans.product_pid = products.pid")
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: (p[1], parse_duration_hours(p[2])))
        if not plans:
            await query.edit_message_text("No active plans found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"❌ {p[1]} - {p[2]}", f"del_plan_{p[0]}")] for p in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Plan to remove:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_plan_"):
        plan_id = data.split("_", 2)[2]
        cursor.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        conn.commit()
        await query.answer("✅ Plan removed!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_edit_plan_info":
        cursor.execute("SELECT plans.id, products.name, plans.duration, plans.remote_duration FROM plans JOIN products ON plans.product_pid = products.pid")
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: (p[1], parse_duration_hours(p[2])))
        if not plans:
            await query.edit_message_text("No active plans found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"✏️ {p[1]} - {p[2]}", f"editplaninfo_{p[0]}")] for p in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("✏️ **EDIT PLAN INFO** ✏️\n\nSelect a plan to view/change its Duration Name or Remote Duration:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("editplaninfo_"):
        plan_id = data.split("editplaninfo_", 1)[1]
        cursor.execute("SELECT products.name, plans.duration, plans.remote_duration FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Plan not found!", show_alert=True)
            return
        text = (
            f"✏️ **EDIT PLAN — {escape_markdown(row[0])}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ **Current Duration Name:** `{row[1]}`\n"
            f"🔗 **Current Remote Duration:** `{row[2]}`\n"
            f"Select what you want to change:"
        )
        keyboard = [
            [button("✏️ Edit Duration Name", f"editplandur_{plan_id}")],
            [button("✏️ Edit Remote Duration", f"editplanremote_{plan_id}")],
            [back_button("admin_edit_plan_info")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("editplandur_"):
        plan_id = data.split("editplandur_", 1)[1]
        context.user_data['editplan_id'] = plan_id
        context.user_data['state'] = 'WAIT_EDIT_PLAN_DURATION_NAME'
        await query.edit_message_text("➡️ Enter new Duration Name shown to users:\n*(Example: `7 Days`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data.startswith("editplanremote_"):
        plan_id = data.split("editplanremote_", 1)[1]
        context.user_data['editplan_id'] = plan_id
        context.user_data['state'] = 'WAIT_EDIT_PLAN_REMOTE_DUR'
        await query.edit_message_text("➡️ Enter new Remote API Duration code:\n*(Example: `7d`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_set_prod_desc":
        cursor.execute("SELECT pid, name, description FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pid, pname = p[0], p[1]
            has_desc = len(p) > 2 and p[2]
            label = f"📝 {pname} (Set)" if has_desc else f"➕ {pname} (Not Set)"
            keyboard.append([button(label, f"proddesc_prod_{pid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("📝 **Select a product to set its Description:**\n_(Shown to users on the product's plans screen. Each line is auto-wrapped like ✨ Your Text ✅)_", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("proddesc_prod_"):
        pid = data.split("proddesc_prod_", 1)[1]
        cursor.execute("SELECT name, description FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['proddesc_pid'] = pid
        context.user_data['state'] = 'WAIT_PROD_DESCRIPTION'
        curr = row[1] if len(row) > 1 and row[1] else "Not Set"
        await query.edit_message_text(
            f"📝 **SET DESCRIPTION — {escape_markdown(row[0])}**\n\n"
            f"Current Description: `{curr}`\n\n"
            f"➡️ Send the new product description:\n"
            f"_(One point per line — each line is shown to users wrapped as ✨ Your Line ✅)_\n"
            f"*(Send `clear` to remove it)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_prod_apks":
        cursor.execute("SELECT pid, name, apk_files, apk_links, apk_video FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pid, pname = p[0], p[1]
            has_content = (len(p) > 2 and p[2]) or (len(p) > 3 and p[3]) or (len(p) > 4 and p[4])
            label = f"📥 {pname} (Set)" if has_content else f"➕ {pname} (Not Set)"
            keyboard.append([button(label, f"apklinks_prod_{pid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text(
            "📥 **Select a product to set its APK file(s) / Link(s) / Video:**\n"
            "_(Step 1: APK file attachments — skippable. Step 2: optional download link(s) — skippable. "
            "Step 3: an actual video attachment — skippable.)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("apklinks_prod_"):
        pid = data.split("apklinks_prod_", 1)[1]
        cursor.execute("SELECT name, apk_files, apk_links, apk_video FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['apklinks_pid'] = pid
        context.user_data['temp_apk_files'] = []
        context.user_data['temp_apk_links'] = []
        context.user_data['temp_apk_video'] = ""
        context.user_data['state'] = 'WAIT_PROD_APK_FILES'
        curr_files_raw = row[1] if len(row) > 1 and row[1] else ""
        curr_files_count = len([f for f in curr_files_raw.split("\n") if f.strip()])
        curr_links = row[2] if len(row) > 2 and row[2] else "Not Set"
        curr_video_set = bool(len(row) > 3 and row[3])
        text = (
            f"📥 **SET APK/LINKS — {escape_markdown(row[0])}**\n\n"
            f"Current APK file(s): {curr_files_count if curr_files_count else 'Not Set'}\n"
            f"Current Links:\n`{curr_links}`\n"
            f"Current Video: {'Set ✅' if curr_video_set else 'Not Set'}\n\n"
            f"➡️ **Step 1:** Send your APK file(s) now — as real Telegram file attachments, one at "
            f"a time or several in a row, as many as you want. (These are delivered to buyers as "
            f"the actual file, via Telegram itself — no re-upload happens, so any size works.)\n\n"
            f"Or tap ⏭️ Skip Files if you only want to use a direct link + video instead.\n\n"
            f"When done, tap ✅ Confirm below."
        )
        keyboard = [
            [button("✅ Confirm & Continue", "apklinks_confirm")],
            [button("⏭️ Skip Files", "apklinks_skip_files")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "apklinks_skip_files":
        pid = context.user_data.get('apklinks_pid')
        if not pid:
            await query.answer("❌ Session expired.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        context.user_data['temp_apk_files'] = []
        context.user_data['state'] = 'WAIT_PROD_APK_LINKS'
        keyboard = [
            [button("✅ Confirm & Continue", "apklinks_confirm")],
            [button("⏭️ Skip Links", "apklinks_skip_links")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ]
        await query.edit_message_text(
            "⏭️ Files skipped.\n\n"
            "➡️ **Step 2:** Send Link(s) now (optional) — one per line, or several "
            "messages. Or tap ⏭️ Skip Links if you don't want to add any.\n"
            "When done (or skipped), tap ✅ Confirm & Continue.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "apklinks_confirm":
        pid = context.user_data.get('apklinks_pid')
        if not pid:
            await query.answer("❌ Session expired.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        current_state = context.user_data.get('state')

        if current_state == 'WAIT_PROD_APK_FILES':
            files = context.user_data.get('temp_apk_files', [])
            if not files:
                await query.answer("⚠️ Send at least one APK file, or tap ⏭️ Skip Files!", show_alert=True)
                return
            context.user_data['state'] = 'WAIT_PROD_APK_LINKS'
            names_preview = "\n".join(f"• {n}" for _, n in files[:10])
            more = f"\n...and {len(files) - 10} more" if len(files) > 10 else ""
            keyboard = [
                [button("✅ Confirm & Continue", "apklinks_confirm")],
                [button("⏭️ Skip Links", "apklinks_skip_links")],
                [button("❌ Cancel", "admin_cancel_flow")]
            ]
            await query.edit_message_text(
                f"✅ Received {len(files)} APK file\\(s\\):\n{md_escape(names_preview)}{md_escape(more)}\n\n"
                f"➡️ **Step 2:** Send Link\\(s\\) now \\(optional\\) — one per line, or several "
                f"messages\\. Or tap ⏭️ Skip Links if you don't want to add any\\.\n"
                f"When done \\(or skipped\\), tap ✅ Confirm & Continue\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if current_state == 'WAIT_PROD_APK_LINKS':
            links = context.user_data.get('temp_apk_links', [])
            context.user_data['state'] = 'WAIT_PROD_APK_VIDEO'
            preview = "\n".join(links[:10]) if links else "(none)"
            more = f"\n...and {len(links) - 10} more" if len(links) > 10 else ""
            keyboard = [
                [button("⏭️ Skip Video", "apklinks_skip_video")],
                [button("❌ Cancel", "admin_cancel_flow")]
            ]
            await query.edit_message_text(
                f"✅ Link\\(s\\) saved: {len(links)}\n`{md_escape(preview)}`{md_escape(more)}\n\n"
                f"➡️ **Step 3:** Send the actual **video file** \\(as a real Telegram video attachment\\) "
                f"for this product — sent separately, in its own message, when a user buys\\. "
                f"Or tap ⏭️ Skip Video\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        await query.answer("❌ Unexpected step, please start again.", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "apklinks_skip_links":
        pid = context.user_data.get('apklinks_pid')
        if not pid:
            await query.answer("❌ Session expired.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        context.user_data['temp_apk_links'] = []
        context.user_data['state'] = 'WAIT_PROD_APK_VIDEO'
        keyboard = [
            [button("⏭️ Skip Video", "apklinks_skip_video")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ]
        await query.edit_message_text(
            f"⏭️ Links skipped\\.\n\n"
            f"➡️ **Step 3:** Send the actual **video file** \\(as a real Telegram video attachment\\) "
            f"for this product — sent separately, in its own message, when a user buys\\. "
            f"Or tap ⏭️ Skip Video\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "apklinks_skip_video":
        pid = context.user_data.get('apklinks_pid')
        if not pid:
            await query.answer("❌ Session expired.", show_alert=True)
            await refresh_admin_panel(context, query)
            return
        files = context.user_data.get('temp_apk_files', [])
        links = context.user_data.get('temp_apk_links', [])
        apk_files_str = "\n".join(f"{fid}|||{fname}" for fid, fname in files)
        apk_links_str = "\n".join(links)
        cursor.execute(
            "UPDATE products SET apk_files = ?, apk_links = ?, apk_video = ? WHERE pid = ?",
            (apk_files_str, apk_links_str, "", pid)
        )
        conn.commit()
        context.user_data.pop('apklinks_pid', None)
        context.user_data.pop('temp_apk_files', None)
        context.user_data.pop('temp_apk_links', None)
        context.user_data.pop('temp_apk_video', None)
        context.user_data['state'] = None
        link_note = f" + {len(links)} Link(s)" if links else ""
        await query.answer("✅ Saved!", show_alert=False)
        await refresh_admin_panel(context, query, f"✅ {len(files)} APK file(s){link_note} saved! (No video)")

    elif data == "admin_add_keys":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        keyboard = [[button(f"📦 {p[1]}", f"key_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Product to add keys:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("key_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("SELECT duration FROM plans WHERE product_pid = ?", (pid,))
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: parse_duration_hours(p[0]))
        keyboard = [[button(f"⏱️ {pl[0]}", f"key_plan_{pid}_{pl[0]}")] for pl in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Duration for keys:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("key_plan_"):
        rest = data.split("key_plan_", 1)[1]
        pid, _, dur = rest.rpartition("_")
        context.user_data['key_pid'] = pid
        context.user_data['key_dur'] = dur
        context.user_data['state'] = 'WAIT_KEYS_INPUT'
        await query.edit_message_text("➡️ Send keys line by line:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_check_keys":
        cursor.execute("""
            SELECT products.name, keys.duration, keys.key_value, keys.id
            FROM keys LEFT JOIN products ON keys.product_pid = products.pid
            ORDER BY products.name, keys.duration
        """)
        all_keys = cursor.fetchall()
        text = "🔍 **Current Key Stock & Details** 🔍\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not all_keys:
            text += "⚠️ No keys available in local stock inventory."
        else:
            current_prod_dur = None
            for row in all_keys:
                p_name = escape_markdown(row[0]) if row[0] else "Unknown Product"
                p_dur = escape_markdown(row[1])
                k_val = row[2]
                header = f"📦 **{p_name}** | ⏱️ **{p_dur}**\n"
                if header != current_prod_dur:
                    current_prod_dur = header
                    text += f"\n{current_prod_dur}"
                text += f"• `{k_val}`\n"
                if len(text) > 3800:
                    text += "\n*(List truncated due to message length limit)*"
                    break
        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_rem_keys":
        cursor.execute("""
            SELECT DISTINCT products.pid, products.name FROM keys
            JOIN products ON keys.product_pid = products.pid ORDER BY products.name
        """)
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("🗑️ **Remove Keys**\n\n⚠️ No keys found.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"📦 {p[1]}", f"remkeyprod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🗑️ **Select Product to remove keys from:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remkeyprod_"):
        pid = data.split("_", 1)[1]
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prod_row = cursor.fetchone()
        prod_name = escape_markdown(prod_row[0]) if prod_row else escape_markdown(pid)
        cursor.execute("SELECT DISTINCT duration FROM keys WHERE product_pid = ?", (pid,))
        durations = [r[0] for r in cursor.fetchall()]
        durations = sorted(durations, key=parse_duration_hours)
        if not durations:
            await query.edit_message_text(f"🗑️ **{prod_name}**\n\n⚠️ No keys found.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[back_button("admin_rem_keys")]]))
            return
        context.user_data['remkey_pid'] = pid
        context.user_data['remkey_durations'] = durations
        keyboard = [[button(f"⏱️ {d}", f"remkeydur_{i}")] for i, d in enumerate(durations)]
        keyboard.append([back_button("admin_rem_keys")])
        await query.edit_message_text(f"🗑️ **{prod_name}**\n\nSelect Plan/Duration:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remkeydur_"):
        idx = int(data.split("_", 1)[1])
        pid = context.user_data.get('remkey_pid')
        durations = context.user_data.get('remkey_durations', [])
        if pid is None or idx >= len(durations):
            await query.answer("❌ Session expired, please try again.", show_alert=True)
            return
        duration = durations[idx]
        context.user_data['remkey_selected_dur'] = duration
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prod_row = cursor.fetchone()
        prod_name = escape_markdown(prod_row[0]) if prod_row else escape_markdown(pid)
        safe_duration = escape_markdown(duration)
        cursor.execute("SELECT id, key_value FROM keys WHERE product_pid = ? AND duration = ? ORDER BY id", (pid, duration))
        keys_rows = cursor.fetchall()
        if not keys_rows:
            await query.edit_message_text(f"🗑️ **{prod_name}** | ⏱️ **{safe_duration}**\n\n⚠️ No keys found.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[back_button(f"remkeyprod_{pid}")]]))
            return
        text = f"🗑️ **REMOVE KEYS**\n📦 **Product:** {prod_name}\n⏱️ **Plan:** {safe_duration}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for k in keys_rows:
            key_id, key_val = k
            text += f"🔑 `{key_val}`\n"
            keyboard.append([button(f"❌ Remove Now — {key_val[:20]}", f"remkeydel_{key_id}")])
            if len(text) > 3500:
                text += "\n*(List truncated — remove some keys and refresh)*"
                break
        keyboard.append([back_button(f"remkeyprod_{pid}")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remkeydel_"):
        key_id = data.split("_", 1)[1]
        cursor.execute("SELECT product_pid, duration, key_value FROM keys WHERE id = ?", (key_id,))
        krow = cursor.fetchone()
        if not krow:
            await query.answer("❌ Already removed!", show_alert=True)
        else:
            cursor.execute("DELETE FROM keys WHERE id = ?", (key_id,))
            conn.commit()
            await query.answer("✅ Key removed!", show_alert=True)
        pid = context.user_data.get('remkey_pid')
        duration = context.user_data.get('remkey_selected_dur')
        if not pid or not duration:
            await refresh_admin_panel(context, query)
            return
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prod_row = cursor.fetchone()
        prod_name = escape_markdown(prod_row[0]) if prod_row else escape_markdown(pid)
        safe_duration = escape_markdown(duration)
        cursor.execute("SELECT id, key_value FROM keys WHERE product_pid = ? AND duration = ? ORDER BY id", (pid, duration))
        keys_rows = cursor.fetchall()
        if not keys_rows:
            await query.edit_message_text(f"🗑️ **{prod_name}** | ⏱️ **{safe_duration}**\n\n⚠️ No keys found.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[back_button(f"remkeyprod_{pid}")]]))
            return
        text = f"🗑️ **REMOVE KEYS**\n📦 **Product:** {prod_name}\n⏱️ **Plan:** {safe_duration}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for k in keys_rows:
            k_id, k_val = k
            text += f"🔑 `{k_val}`\n"
            keyboard.append([button(f"❌ Remove Now — {k_val[:20]}", f"remkeydel_{k_id}")])
            if len(text) > 3500:
                text += "\n*(List truncated — remove some keys and refresh)*"
                break
        keyboard.append([back_button(f"remkeyprod_{pid}")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_prod_links":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        bot_uname = context.bot.username
        text = "🔗 **ALL PRODUCTS UNIQUE DEEP LINKS** 🔗\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not prods:
            text += "❌ No products found. Add products first."
        else:
            for p in prods:
                pid, pname = p[0], p[1]
                safe_pname = escape_markdown(pname)
                link = f"https://t.me/{bot_uname}?start=prod_{pid}"
                text += f"📦 **Product:** {safe_pname}\n🆔 **PID:** `{pid}`\n🔗 **Direct Link:**\n`{link}`\n------------------------------------\n"
        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_maintenance_prod":
        cursor.execute("SELECT pid, name, is_maintenance FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pid, pname, in_maint = p[0], p[1], (len(p) > 2 and p[2])
            label = f"🛠 {pname} (Under Maintenance)" if in_maint else f"✅ {pname} (Active)"
            keyboard.append([button(label, f"toggle_maint_{pid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🛠 **Select a product to toggle Maintenance Mode:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("toggle_maint_"):
        pid = data.split("_", 2)[2]
        cursor.execute("SELECT name, is_maintenance FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        new_state = 0 if row[1] else 1
        cursor.execute("UPDATE products SET is_maintenance = ? WHERE pid = ?", (new_state, pid))
        conn.commit()
        if new_state:
            await query.answer(f"🛠 {row[0]} set to Maintenance!", show_alert=True)
        else:
            await query.answer(f"✅ {row[0]} is Active again!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_set_group_link":
        cursor.execute("SELECT pid, name, group_link FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pid, pname = p[0], p[1]
            has_link = len(p) > 2 and p[2]
            label = f"🔗 {pname} (Set)" if has_link else f"➕ {pname} (Not Set)"
            keyboard.append([button(label, f"grplink_prod_{pid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🔗 **Select a product to set its Group/Updates Link:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("grplink_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("SELECT name, group_link FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['grplink_pid'] = pid
        context.user_data['state'] = 'WAIT_GROUP_LINK'
        curr = row[1] if len(row) > 1 and row[1] else "Not Set"
        await query.edit_message_text(
            f"🔗 **SET GROUP LINK — {escape_markdown(row[0])}**\n\n"
            f"Current Link: `{curr}`\n\n"
            f"➡️ Send the Telegram group/channel invite link:\n"
            f"*(Example: `https://t.me/+5xJE88q0GoA3MWE1`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_prod_video":
        cursor.execute("SELECT pid, name, demo_video FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = []
        for p in prods:
            pid, pname = p[0], p[1]
            has_vid = len(p) > 2 and p[2]
            label = f"🎥 {pname} (Set)" if has_vid else f"➕ {pname} (Not Set)"
            keyboard.append([button(label, f"prodvideo_prod_{pid}")])
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🎥 **Select a product to set its Demo Video:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("prodvideo_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("SELECT name, demo_video FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['prodvideo_pid'] = pid
        context.user_data['state'] = 'WAIT_PROD_VIDEO_LINK'
        curr_set = bool(len(row) > 1 and row[1])
        await query.edit_message_text(
            f"🎥 **SET DEMO VIDEO — {escape_markdown(row[0])}**\n\n"
            f"Current Video: {'Set ✅' if curr_set else 'Not Set'}\n\n"
            f"➡️ Send the demo video now as a real Telegram **video attachment** "
            f"(not a link — it will be sent to buyers directly, before purchase):\n"
            f"*(Send `clear` to remove the current video)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_custom_price":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"📦 {p[1]}", f"custprice_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("💵 **Select Product for Custom Price:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("custprice_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("SELECT id, duration, price, reseller_price FROM plans WHERE product_pid = ?", (pid,))
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: parse_duration_hours(p[1]))
        if not plans:
            await query.edit_message_text("No plans found for this product.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"⏱️ {pl[1]} (N ₹{pl[2]:.2f} / R ₹{pl[3]:.2f})", f"custprice_plan_{pl[0]}")] for pl in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Plan:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("custprice_plan_"):
        plan_id = data.split("_", 2)[2]
        context.user_data['custprice_plan_id'] = plan_id
        context.user_data['state'] = 'WAIT_CUSTOM_PRICE_INPUT'
        cursor.execute("SELECT products.name, plans.duration, plans.price, plans.reseller_price FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        prow = cursor.fetchone()
        info = ""
        if prow:
            info = (
                f"📦 **Product:** {escape_markdown(prow[0])}\n"
                f"⏱️ **Plan:** {escape_markdown(prow[1])}\n"
                f"💵 **Current Normal price:** ₹{prow[2]:.2f}\n"
                f"👑 **Current Reseller price:** ₹{prow[3]:.2f}\n\n"
            )
        await query.edit_message_text(
            f"💵 **SET CUSTOM PRICE**\n━━━━━━━━━━━━━━━━━━━━\n\n{info}"
            "➡️ Send **User ID** and **Price** separated by a space:\n*(Example: `123456789 25`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_rem_custom_price":
        cursor.execute("""
            SELECT DISTINCT products.pid, products.name FROM custom_prices
            JOIN plans ON custom_prices.plan_id = plans.id
            JOIN products ON plans.product_pid = products.pid ORDER BY products.name
        """)
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No custom prices set for any product yet.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"📦 {p[1]}", f"remcustprice_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🗑️ **Select Product to view/remove Custom Prices:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remcustprice_prod_"):
        pid = data.split("_", 2)[2]
        cursor.execute("""
            SELECT DISTINCT plans.id, plans.duration FROM plans
            JOIN custom_prices ON custom_prices.plan_id = plans.id
            WHERE plans.product_pid = ?
        """, (pid,))
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: parse_duration_hours(p[1]))
        if not plans:
            await query.edit_message_text("No custom prices set for this product's plans.", reply_markup=InlineKeyboardMarkup([[back_button("admin_rem_custom_price")]]))
            return
        keyboard = [[button(f"⏱️ {pl[1]}", f"remcustprice_plan_{pl[0]}")] for pl in plans]
        keyboard.append([back_button("admin_rem_custom_price")])
        await query.edit_message_text("Select Plan:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remcustprice_plan_"):
        plan_id = data.split("_", 2)[2]
        cursor.execute("""
            SELECT products.pid, products.name, plans.duration FROM plans
            JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?
        """, (plan_id,))
        plan_info = cursor.fetchone()
        pid = plan_info[0] if plan_info else None
        prod_name = escape_markdown(plan_info[1]) if plan_info else "Unknown"
        plan_dur = escape_markdown(plan_info[2]) if plan_info else "Unknown"
        back_cb = f"remcustprice_prod_{pid}" if pid else "admin_rem_custom_price"
        cursor.execute("""
            SELECT custom_prices.id, custom_prices.user_id, custom_prices.price, users.full_name, users.username
            FROM custom_prices LEFT JOIN users ON custom_prices.user_id = users.user_id
            WHERE custom_prices.plan_id = ? ORDER BY custom_prices.id DESC
        """, (plan_id,))
        rows = cursor.fetchall()
        if not rows:
            await query.edit_message_text("No custom prices set for this plan.", reply_markup=InlineKeyboardMarkup([[back_button(back_cb)]]))
            return
        text = f"🗑️ **CUSTOM PRICES**\n📦 **Product:** {prod_name}\n⏱️ **Plan:** {plan_dur}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        for r in rows:
            cp_id, uid, price, fname, uname = r
            display_name = escape_markdown(fname) if fname else "N/A"
            display_uname = f"@{escape_markdown(uname)}" if uname else "N/A"
            text += f"👤 **Name:** {display_name}\n🏷️ **Username:** {display_uname}\n🆔 **User ID:** `{uid}`\n💵 **Price:** ₹{price:.2f}\n------------------------------------\n"
            keyboard.append([button(f"❌ Remove — {uid} (₹{price:.2f})", f"remcustprice_del_{cp_id}")])
        keyboard.append([back_button(back_cb)])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("remcustprice_del_"):
        cp_id = data.split("_", 2)[2]
        cursor.execute("SELECT plan_id, user_id, price FROM custom_prices WHERE id = ?", (cp_id,))
        cp_row = cursor.fetchone()
        if not cp_row:
            await query.answer("❌ Already removed!", show_alert=True)
            return
        plan_id, removed_uid, removed_price = cp_row
        cursor.execute("DELETE FROM custom_prices WHERE id = ?", (cp_id,))
        conn.commit()
        await query.answer(f"✅ Custom price removed for user {removed_uid}!", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_set_payment":
        provider = get_payment_provider()
        fampay_upi_disp = mask_secret(get_upi())
        fampay_key_disp = mask_secret(get_fampay_api_key())
        paytm_upi_disp = mask_secret(get_paytm_upi())
        paytm_mid_disp = mask_secret(get_paytm_merchant_id())
        paytm_mkey_disp = mask_secret(get_paytm_merchant_key())
        active_label = "🅿️ Paytm" if provider == "paytm" else "💠 FamPay"
        pay_maint_on = is_payment_maintenance()
        pay_maint_label = "🛠 Payment Maintenance: ON" if pay_maint_on else "✅ Payment Maintenance: OFF"
        text = (
            f"💠 **PAYMENT GATEWAY SETTINGS** 💠\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ **Active Gateway (Add Balance / Buy / Reseller):** {active_label}\n"
            f"🛠 **Payment Maintenance:** {'ON' if pay_maint_on else 'OFF'}\n"
            f"_(When ON: Add Balance / Product Buy / Reseller QR is blocked and users see a maintenance notice instead.)_\n\n"
            f"**💠 FamPay**\n"
            f"🏦 UPI ID: `{fampay_upi_disp}`\n"
            f"🔑 API Key: `{fampay_key_disp}`\n\n"
            f"**🅿️ Paytm**\n"
            f"🏦 UPI ID: `{paytm_upi_disp}`\n"
            f"🆔 Merchant ID: `{paytm_mid_disp}`\n"
            f"🔑 Merchant Key: `{paytm_mkey_disp}`\n\n"
            f"Select what you want to change:"
        )
        switch_label = "🔁 Switch to Paytm" if provider == "fampay" else "🔁 Switch to FamPay"
        keyboard = [
            [button(pay_maint_label, "admin_toggle_payment_maintenance")],
            [button(switch_label, "admin_switch_provider")],
            [button("✏️ Set FamPay UPI", "admin_input_upi"), button("✏️ Set FamPay API Key", "admin_input_fampay_key")],
            [button("✏️ Set Paytm UPI", "admin_input_paytm_upi")],
            [button("✏️ Set Paytm Merchant ID", "admin_input_paytm_mid"), button("✏️ Set Paytm Merchant Key", "admin_input_paytm_mkey")],
            [back_button("admin_back_panel")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_toggle_payment_maintenance":
        new_state = not is_payment_maintenance()
        set_payment_maintenance(new_state)
        await query.answer(
            "🛠 Payment Maintenance ENABLED — Add Balance / Product Buy / Reseller QR is now blocked!" if new_state
            else "✅ Payment Maintenance DISABLED — payments are live again!",
            show_alert=True
        )
        await refresh_admin_panel(context, query)

    elif data == "admin_switch_provider":
        current = get_payment_provider()
        new_provider = "paytm" if current == "fampay" else "fampay"
        set_payment_provider(new_provider)
        await query.answer(f"✅ Payment gateway switched to {new_provider.upper()}! (Used for Add Balance, Purchases & Reseller Upgrade.)", show_alert=True)
        await refresh_admin_panel(context, query)

    elif data == "admin_input_upi":
        context.user_data['state'] = 'WAIT_UPI_ID'
        await query.edit_message_text("➡️ Send your new FamPay / UPI ID:\n*(Example: `samiranbhuyan@fam`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_input_fampay_key":
        context.user_data['state'] = 'WAIT_FAMPAY_KEY'
        await query.edit_message_text("➡️ Send your new FamPay API Key:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_input_paytm_upi":
        context.user_data['state'] = 'WAIT_PAYTM_UPI'
        await query.edit_message_text("➡️ Send your new Paytm UPI ID:\n*(Example: `paytm.s1zxmoz@pty`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_input_paytm_mid":
        context.user_data['state'] = 'WAIT_PAYTM_MID'
        await query.edit_message_text("➡️ Send your new Paytm Merchant ID:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_input_paytm_mkey":
        context.user_data['state'] = 'WAIT_PAYTM_MKEY'
        await query.edit_message_text("➡️ Send your new Paytm Merchant Key:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_set_store_api":
        key_disp = mask_secret(get_store_api_key())
        text = (
            f"🔑 **AUTO API SETTING** 🔑\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 Default API (fixed): `{API_URL}`\n"
            f"🔑 API Key: `{key_disp}`\n\n"
            f"➡️ Only the API Key can be changed from the bot. The URL is fixed.\n"
            f"Tap below to update:"
        )
        keyboard = [
            [button("✏️ Set API Key", "admin_input_store_api")],
            [back_button("admin_back_panel")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_input_store_api":
        active_key = get_store_api_key()
        key_display = mask_secret(active_key)
        context.user_data['state'] = 'WAIT_STORE_API_KEY'
        await query.edit_message_text(f"🔑 Current API Key: `{key_display}`\n\n➡️ Send the new Reseller API Key:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_set_ai_key":
        key_disp = mask_secret(get_ai_api_key())
        text = (
            f"🤖 **SET AI API KEY (Gemini)** 🤖\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 Current Key: `{key_disp}`\n\n"
            f"➡️ Send your new Google Gemini API Key.\n"
            f"_(Get one free at https://aistudio.google.com/apikey — it starts with `AIzaSy` or `AQ.`)_"
        )
        context.user_data['state'] = 'WAIT_AI_KEY_INPUT'
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_set_reseller_price":
        cursor.execute("SELECT plans.id, products.name, plans.duration, plans.price, plans.reseller_price FROM plans JOIN products ON plans.product_pid = products.pid")
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: (p[1], parse_duration_hours(p[2])))
        if not plans:
            await query.edit_message_text("No plans found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"⚙️ {p[1]} - {p[2]} (Current: ₹{p[4]:.2f})", f"set_plan_reseller_{p[0]}")] for p in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Plan to change Reseller Price:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("set_plan_reseller_"):
        plan_id = data.split("_", 3)[3]
        cursor.execute("SELECT products.name, plans.duration, plans.price, plans.reseller_price FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        prow = cursor.fetchone()
        if not prow:
            await query.answer("❌ Plan not found!", show_alert=True)
            return
        context.user_data['edit_plan_id'] = plan_id
        context.user_data['state'] = 'WAIT_PLAN_RESELLER_PRICE'
        await query.edit_message_text(
            f"⚙️ **CHANGE RESELLER PRICE**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 **Product:** {escape_markdown(prow[0])}\n"
            f"⏱️ **Plan:** {escape_markdown(prow[1])}\n"
            f"💵 **Current Normal price:** ₹{prow[2]:.2f}\n"
            f"👑 **Current Reseller price:** ₹{prow[3]:.2f}\n\n"
            f"➡️ Enter new Reseller Price for this plan:\n*(Example: `20`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_upgrade_price":
        curr = get_reseller_price()
        context.user_data['state'] = 'WAIT_RESELLER_UPGRADE_PRICE'
        await query.edit_message_text(
            f"💎 **SET RESELLER UPGRADE PRICE**\n\n"
            f"Current Upgrade Price: ₹{curr:.2f}\n\n"
            f"➡️ Send new upgrade price:\n*(Example: `449`)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_add_reseller":
        context.user_data['state'] = 'WAIT_ADD_RESELLER_ID'
        await query.edit_message_text("➡️ Send User ID to promote to Reseller:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_rem_reseller":
        context.user_data['state'] = 'WAIT_REM_RESELLER_ID'
        await query.edit_message_text("➡️ Send User ID to demote from Reseller:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_my_resellers":
        cursor.execute("SELECT user_id, full_name, username FROM users WHERE is_reseller = 1")
        resellers = cursor.fetchall()
        text = "👥 **MY RESELLERS LIST** 👥\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not resellers:
            text += "No active resellers found."
        else:
            for r in resellers:
                r_name = escape_markdown(r[1]) if r[1] else "N/A"
                r_uname = f"@{escape_markdown(r[2])}" if r[2] else "N/A"
                text += f"👤 **Name:** {r_name}\n🏷️ **Username:** {r_uname}\n🆔 **ID:** `{r[0]}`\n------------------------------------\n"
        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text[:4000], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_change_price":
        cursor.execute("SELECT plans.id, products.name, plans.duration, plans.price FROM plans JOIN products ON plans.product_pid = products.pid")
        plans = cursor.fetchall()
        plans = sorted(plans, key=lambda p: (p[1], parse_duration_hours(p[2])))
        if not plans:
            await query.edit_message_text("No active plans found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"💲 {p[1]} ({p[2]}) - ₹{p[3]:.2f}", f"chg_price_{p[0]}")] for p in plans]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Plan to change Normal Price:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("chg_price_"):
        plan_id = data.split("_", 2)[2]
        cursor.execute("SELECT products.name, plans.duration, plans.price, plans.reseller_price FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        prow = cursor.fetchone()
        if not prow:
            await query.answer("❌ Plan not found!", show_alert=True)
            return
        context.user_data['chg_plan_id'] = plan_id
        context.user_data['state'] = 'WAIT_NEW_PLAN_PRICE'
        await query.edit_message_text(
            f"💲 **CHANGE NORMAL PRICE**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 **Product:** {escape_markdown(prow[0])}\n"
            f"⏱️ **Plan:** {escape_markdown(prow[1])}\n"
            f"💵 **Current Normal price:** ₹{prow[2]:.2f}\n"
            f"👑 **Current Reseller price:** ₹{prow[3]:.2f}\n\n"
            f"➡️ Enter new Normal Price:\n*(Example: `50`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_change_name":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"✏️ {p[1]}", f"rename_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("Select Product to Rename:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("rename_prod_"):
        pid = data.split("_", 2)[2]
        context.user_data['rename_pid'] = pid
        context.user_data['state'] = 'WAIT_NEW_PROD_NAME'
        await query.edit_message_text("➡️ Enter new Product Name:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_change_pid":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"🆔 {p[1]} ({p[0]})", f"chgpid_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text("🆔 **CHANGE PID**\n\nSelect a product to change its PID:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("chgpid_prod_"):
        pid = data.split("chgpid_prod_", 1)[1]
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found!", show_alert=True)
            return
        context.user_data['chgpid_old_pid'] = pid
        context.user_data['state'] = 'WAIT_NEW_PROD_PID'
        await query.edit_message_text(
            f"🆔 **CHANGE PID — {escape_markdown(row[0])}**\n\n"
            f"Current PID: `{pid}`\n\n"
            f"➡️ Send the new PID:\n*(Example: `DRIP_NONROOT`)*\n\n"
            f"⚠️ Plans and Key stock linked to this product move automatically to the new PID. "
            f"Any deep link using the old PID will stop working — reshare it after changing.",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_product_variant":
        cursor.execute("SELECT pid, name, COALESCE(variant_id, '') FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        keyboard = [[button(f"🔗 {p[1]} — Variant: {p[2] or 'Not set'}", f"api_variant_prod_{p[0]}")] for p in prods]
        keyboard.append([back_button("admin_back_panel")])
        await query.edit_message_text(
            "🔗 **SET KEYHIVE API VARIANT ID**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose the product, then send its numeric Variant ID from Bantibhaiya's `/products` response.\n"
            "Example: Z-REX ApkMod [Non Root] uses the documented variant ID `83` if that is the variant you intend to sell.\n\n"
            "This is separate from the bot's internal Product PID and plan duration.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("api_variant_prod_"):
        pid = data.split("api_variant_prod_", 1)[1]
        row = cursor.execute("SELECT name, COALESCE(variant_id, '') FROM products WHERE pid = ?", (pid,)).fetchone()
        if not row:
            await query.answer("Product not found.", show_alert=True)
            return
        context.user_data['api_variant_pid'] = pid
        context.user_data['state'] = 'WAIT_PRODUCT_VARIANT_ID'
        await query.edit_message_text(
            f"🔗 **SET API VARIANT ID**\n\n📦 Product: {escape_markdown(row[0])}\n"
            f"🆔 Current Variant ID: `{row[1] or 'Not set'}`\n\n"
            "Send the numeric Bantibhaiya Variant ID (example: `83`). Send `clear` to remove it.",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_lineup_prod":
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        prods = cursor.fetchall()
        if not prods:
            await query.edit_message_text("No products found.", reply_markup=InlineKeyboardMarkup([[back_button("admin_back_panel")]]))
            return
        context.user_data['state'] = 'WAIT_PRODUCT_LINEUP'
        listing = "\n".join(f"{i + 1}. {escape_markdown(p[1])} — `{p[0]}`" for i, p in enumerate(prods))
        example_lines = [prods[-1][0]] + ([prods[0][0]] if len(prods) > 1 else [])
        example_ex = "\n".join(f"`{e}`" for e in example_lines)
        text = (
            f"↕️ **LINE UP PRODUCTS** ↕️\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 **Current Order (Top → Bottom):**\n{listing}\n\n"
            f"➡️ Send the PID of every product above, one per line, in the exact order "
            f"you want them shown to users (top to bottom):\n"
            f"*(Example:*\n{example_ex}*)*\n\n"
            f"⚠️ You must include every PID exactly once — send the full list."
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_set_how_video":
        context.user_data['state'] = 'WAIT_HOW_VIDEO_URL'
        await query.edit_message_text("➡️ Send video tutorial URL:\n*(Example: `https://youtube.com/...` or send `clear` to remove)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_add_bal":
        context.user_data['state'] = 'WAIT_ADD_BAL_INPUT'
        await query.edit_message_text("➡️ Send User ID and Amount separated by a space:\n*(Example: `123456789 100`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_rem_bal":
        context.user_data['state'] = 'WAIT_REM_BAL_INPUT'
        await query.edit_message_text("➡️ Send User ID and Amount separated by a space:\n*(Example: `123456789 50`)*", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_check_bal":
        context.user_data['state'] = 'WAIT_CHECK_BAL_ID'
        await query.edit_message_text("➡️ Send User ID to check balance:", parse_mode="Markdown", reply_markup=cancel_button_keyboard())

    elif data == "admin_create_coupon":
        context.user_data['state'] = 'WAIT_COUPON_PERCENT'
        await query.edit_message_text(
            "🎟️ **CREATE COUPON — Step 1/3**\n\n➡️ Enter the discount %:\n*(Example: `15`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_view_coupons":
        cursor.execute("SELECT code, discount_percent, max_users, used_count, expires_at, source FROM coupons ORDER BY created_at DESC LIMIT 20")
        rows = cursor.fetchall()
        text = "🎟️ **ACTIVE / RECENT COUPONS** 🎟️\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if not rows:
            text += "No coupons created yet."
        else:
            for r in rows:
                code, disc, maxu, used, exp, src = r
                text += (
                    f"🎫 `{code}` — {disc:.2f}%\n"
                    f"👥 {used}/{maxu} used | ⏳ {to_ist_str(exp)} IST | {src}\n"
                    f"------------------------------------\n"
                )
        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text[:4000], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_set_spin_balance":
        s = get_spin_settings()
        context.user_data['state'] = 'WAIT_SPIN_BAL_RANGE'
        await query.edit_message_text(
            f"🎰 **SET SPIN BALANCE RANGE**\n\nCurrent: ₹{s['bal_min']} – ₹{s['bal_max']}\n\n"
            f"➡️ Send the min and max separated by a space:\n*(Example: `0.1 2`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_spin_coupon":
        s = get_spin_settings()
        context.user_data['state'] = 'WAIT_SPIN_COUPON_RANGE'
        await query.edit_message_text(
            f"🎰 **SET SPIN COUPON % RANGE**\n\nCurrent: {s['coup_min']}% – {s['coup_max']}%\n\n"
            f"➡️ Send the min and max separated by a space:\n*(Example: `2 10`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_set_spin_chance":
        s = get_spin_settings()
        better = round(max(0.0, 100 - s['coup_chance'] - s['bal_chance']), 2)
        context.user_data['state'] = 'WAIT_SPIN_CHANCE'
        await query.edit_message_text(
            f"🎰 **SET SPIN WIN CHANCES**\n\n"
            f"Current:\n"
            f"🎫 Chance to win a coupon: {s['coup_chance']}%\n"
            f"💰 Chance to win balance: {s['bal_chance']}%\n"
            f"😅 Better Luck Next Time: {better}%\n\n"
            f"➡️ Send two numbers: `<coupon%> <balance%>`\n"
            f"*(Example: `2 1` — means 2% coupon, 1% balance, 97% better luck)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_broadcast":
        context.user_data['state'] = 'WAIT_BROADCAST_MSG'
        await query.edit_message_text(
            "📢 **BROADCAST MESSAGE**\n\n"
            "Send ANYTHING to broadcast to all users — Text, Photo, Video, Voice note, etc.\n\n"
            "Whatever you send will be copied exactly as-is to every user.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[button("❌ Cancel", "admin_broadcast_cancel")]])
        )

    elif data == "admin_broadcast_cancel":
        context.user_data['state'] = None
        await query.answer("❌ Broadcast cancelled.")
        if is_main_admin(user_id):
            await refresh_admin_panel(context, query)
        else:
            try:
                await query.edit_message_text(
                    BROADCAST_ADMIN_PANEL_TEXT,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
                )
            except Exception:
                pass

    elif data == "admin_cancel_flow":
        for k in ('temp_prod_names', 'temp_pid', 'temp_plan_durations',
                  'temp_plan_remotes', 'temp_plan_prices',
                  'temp_coupon_pct', 'temp_coupon_maxu',
                  'apklinks_pid', 'temp_apk_files', 'temp_apk_links', 'temp_apk_video',
                  'chgpid_old_pid',
                  'prodoffer_pid', 'prodoffer_target', 'prodoffer_pct'):
            context.user_data.pop(k, None)
        context.user_data['state'] = None
        await query.answer("❌ Cancelled.")
        if is_main_admin(user_id):
            await refresh_admin_panel(context, query)
        else:
            try:
                await query.edit_message_text(
                    BROADCAST_ADMIN_PANEL_TEXT,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(get_broadcast_admin_panel_markup())
                )
            except Exception:
                pass

    elif data == "admin_backup":
        await query.answer("⏳ Creating backup, please wait...")
        try:
            backup_path = await create_backup_zip()
            caption = (
                f"💾 *Backup Ready* 💾\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"This contains all your data — products, plans, key stock, "
                f"custom prices, coupons and settings \\(API keys, UPI, payment config\\) — everything\\.\n\n"
                f"To restore it, use the ♻️ Restore button and send this same file back\\."
            )
            with open(backup_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    filename=os.path.basename(backup_path),
                    caption=caption,
                    parse_mode="MarkdownV2"
                )
            os.remove(backup_path)
        except Exception as e:
            logging.error(f"Backup failed: {e}")
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Backup failed: {e}")
            except Exception:
                pass
        await refresh_admin_panel(context, query)

    elif data == "admin_restore":
        context.user_data['state'] = 'WAIT_RESTORE_FILE'
        await query.edit_message_text(
            "♻️ *RESTORE FROM BACKUP* ♻️\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ This will OVERWRITE all your current data \\(products, plans, keys, coupons, settings, everything\\) "
            "with the data from the backup file\\. This action cannot be undone\\.\n\n"
            "➡️ Send the backup `\\.zip` file here:",
            parse_mode="MarkdownV2",
            reply_markup=cancel_button_keyboard()
        )

    elif data == "admin_ai_support":
        context.user_data['state'] = 'WAIT_AI_SUPPORT_QUESTION'
        context.user_data['ai_history'] = []
        if get_ai_api_key():
            status = "✅ Real AI is ON — ask anything (panel, bot, coding, business, anything)."
        else:
            status = "⚠️ Basic panel help is available right now. Ask about any admin panel feature below."
        text = (
            "🤖 **AI SUPPORT** 🤖\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{status}\n\n"
            "Type your question and send it. The chat continues until you press Back.\n\n"
            "_Example: `how do I add a product`, `how do I change the coupon odds`_"
        )
        keyboard = [[back_button("admin_back_panel")]]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("ticket_reply_"):
        ticket_id = data.split("ticket_reply_", 1)[1]
        cursor.execute("SELECT user_id FROM tickets WHERE ticket_id = ?", (ticket_id,))
        trow = cursor.fetchone()
        if not trow:
            return
        context.user_data['state'] = 'WAIT_TICKET_REPLY'
        context.user_data['reply_ticket_id'] = ticket_id
        context.user_data['reply_user_id'] = trow[0]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"✍️ **Send your reply now** for Ticket `{ticket_id}`:",
            parse_mode="Markdown"
        )


# ============================================================
# TEXT INPUT HANDLER
# ============================================================
async def handle_text_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    if not state:
        return

    # Check ban for non-admin users
    user_row = get_user(user_id)
    if is_user_banned(user_row) and user_id != ADMIN_ID:
        context.user_data['state'] = None
        await send_banned_notice(update)
        return

    if is_bot_maintenance() and user_id != ADMIN_ID and not is_any_admin(user_id):
        context.user_data['state'] = None
        await send_maintenance_notice(update)
        return

    # ===== WAIT_ADD_ADMIN_ID =====
    if state == 'WAIT_ADD_ADMIN_ID':
        if not is_main_admin(user_id):
            return
        if not update.message or not update.message.text:
            return
        text = update.message.text.strip()
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID. Send a numeric Telegram User ID.")
            return
        if new_admin_id == ADMIN_ID:
            await update.message.reply_text("❌ That's already the main admin.")
            return
        cursor = get_db_conn().cursor()
        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (new_admin_id,))
        if not cursor.fetchone():
            await update.message.reply_text(
                "❌ That user hasn't started the bot yet. Ask them to send /start first."
            )
            return
        add_broadcast_admin(new_admin_id, added_by=user_id)
        context.user_data['state'] = None
        try:
            await context.bot.send_message(
                chat_id=new_admin_id,
                text=(
                    "🎉 *You've been promoted to Admin\\!*\n\n"
                    "📢 *Access:* Broadcast Only\n\n"
                    "Send /admin to open your panel\\."
                ),
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass
        await reopen_admin_panel(update, context, f"✅ User `{new_admin_id}` is now a Broadcast Admin!")
        return

    # ===== BAN/UNBAN states =====
    if state == 'WAIT_BAN_USER_ID':
        if not is_main_admin(user_id):
            return
        if not update.message or not update.message.text:
            return
        parts = update.message.text.strip().split(maxsplit=1)
        try:
            target_id = int(parts[0])
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid format. Send a numeric User ID (optionally with a reason).")
            return
        reason = parts[1] if len(parts) > 1 else ""
        if target_id == ADMIN_ID:
            await update.message.reply_text("❌ You cannot ban the main admin!")
            return
        if is_any_admin(target_id):
            await update.message.reply_text("❌ You cannot ban an admin!")
            return
        cursor = get_db_conn().cursor()
        cursor.execute("SELECT full_name, username FROM users WHERE user_id = ?", (target_id,))
        urow = cursor.fetchone()
        if not urow:
            await update.message.reply_text("❌ User not found in database. They must have started the bot at least once.")
            return
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?", (reason, target_id))
        conn.commit()
        await notify_user_banned(context, target_id, reason)
        context.user_data['state'] = None
        name_disp = escape_markdown(urow[0]) if urow[0] else "N/A"
        await reopen_admin_panel(
            update, context,
            f"✅ **User Banned!**\n\n🆔 **User ID:** `{target_id}`\n👤 **Name:** {name_disp}\n📝 **Reason:** {escape_markdown(reason) if reason else 'None'}"
        )
        return

    if state == 'WAIT_UNBAN_USER_ID':
        if not is_main_admin(user_id):
            return
        if not update.message or not update.message.text:
            return
        try:
            target_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID. Send a numeric Telegram User ID.")
            return
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT full_name, username, is_banned FROM users WHERE user_id = ?", (target_id,))
        urow = cursor.fetchone()
        if not urow:
            await update.message.reply_text("❌ User not found in database.")
            return
        if not urow[2]:
            await update.message.reply_text("ℹ️ This user is not banned.")
            context.user_data['state'] = None
            await reopen_admin_panel(update, context)
            return
        cursor.execute("UPDATE users SET is_banned = 0, ban_reason = '' WHERE user_id = ?", (target_id,))
        conn.commit()
        await notify_user_unbanned(context, target_id)
        context.user_data['state'] = None
        name_disp = escape_markdown(urow[0]) if urow[0] else "N/A"
        await reopen_admin_panel(
            update, context,
            f"✅ **User Unbanned!**\n\n🆔 **User ID:** `{target_id}`\n👤 **Name:** {name_disp}"
        )
        return

    if state == 'WAIT_BROADCAST_MSG':
        if not is_any_admin(user_id):
            return
        context.user_data['state'] = None
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
        users = cursor.fetchall()
        src_chat_id = update.effective_chat.id
        src_msg_id = update.message.message_id
        sent, failed = 0, 0
        await update.message.reply_text(f"📢 Starting broadcast to {len(users)} users... ⏳")
        for u in users:
            try:
                await context.bot.copy_message(chat_id=u[0], from_chat_id=src_chat_id, message_id=src_msg_id)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await reopen_admin_panel(update, context, f"✅ Broadcast Completed!\n\nSuccessful: {sent}\nFailed: {failed}")
        return

    if state == 'WAIT_TICKET_MSG':
        ticket_id = context.user_data.get('open_ticket_id')
        context.user_data['state'] = None
        context.user_data['open_ticket_id'] = None
        if not ticket_id:
            await update.message.reply_text("❌ Ticket session expired. Please open a new ticket.")
            return
        user_obj = update.effective_user
        full_name_old = escape_markdown(user_obj.full_name) if user_obj.full_name else "N/A"
        full_name_mdv2 = md_escape(user_obj.full_name) if user_obj.full_name else "N/A"
        uname_mdv2 = f"@{md_escape(user_obj.username)}" if user_obj.username else "N/A"
        header_text = (
            f"🎫 *NEW SUPPORT TICKET* 🎫\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 *Ticket ID:* `{ticket_id}`\n"
            f"👤 *Name:* {full_name_mdv2}\n"
            f"🏷️ *Username:* {uname_mdv2}\n"
            f"🆔 *User ID:* `{user_id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✍️ *Message:*"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=header_text, parse_mode="MarkdownV2")
            await context.bot.copy_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💬 Tap below to reply to **{full_name_old}** on Ticket `{ticket_id}`:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[button("💬 Reply to Ticket", f"ticket_reply_{ticket_id}")]])
            )
        except Exception as e:
            logging.error(f"Failed to forward ticket {ticket_id} to admin: {e}")
        await update.message.reply_text(
            f"✅ *Ticket submitted\\!* ✅\n\n"
            f"🆔 *Ticket ID:* `{ticket_id}`\n\n"
            f"⏳ Our admin will reply here soon\\. 🙏",
            parse_mode="MarkdownV2"
        )
        return

    if state == 'WAIT_TICKET_REPLY':
        if user_id != ADMIN_ID:
            return
        ticket_id = context.user_data.get('reply_ticket_id')
        target_user_id = context.user_data.get('reply_user_id')
        context.user_data['state'] = None
        context.user_data['reply_ticket_id'] = None
        context.user_data['reply_user_id'] = None
        if not ticket_id or not target_user_id:
            await update.message.reply_text("❌ Reply session expired.")
            return
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"💬 *Support Reply* 💬\n\n🆔 *Ticket ID:* `{ticket_id}`",
                parse_mode="MarkdownV2"
            )
            await context.bot.copy_message(chat_id=target_user_id, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            await update.message.reply_text(f"✅ Reply sent for Ticket `{ticket_id}`!", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to deliver reply: {e}")
        return

    if state == 'WAIT_RESTORE_FILE':
        if user_id != ADMIN_ID:
            return
        context.user_data['state'] = None
        doc = update.message.document if update.message else None
        if not doc or not (doc.file_name or "").lower().endswith(".zip"):
            await update.message.reply_text(
                "❌ This is not a valid backup `.zip` file. Please send only a file created with 💾 Backup.",
                parse_mode="Markdown"
            )
            return
        tmp_zip = None
        tmp_db = None
        try:
            tg_file = await doc.get_file()
            tmp_zip = f"/tmp/restore_{uuid.uuid4().hex}.zip"
            await tg_file.download_to_drive(tmp_zip)

            db_entry_name = None
            with zipfile.ZipFile(tmp_zip, "r") as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".db"):
                        db_entry_name = name
                        break
                if not db_entry_name:
                    await update.message.reply_text("❌ No `.db` file was found in this zip — it doesn't look like a backup file.")
                    return
                tmp_db = f"/tmp/restore_{uuid.uuid4().hex}.db"
                with zf.open(db_entry_name) as src, open(tmp_db, "wb") as dst:
                    shutil.copyfileobj(src, dst)

            global _DB_CONN
            try:
                if _DB_CONN:
                    _DB_CONN.close()
            except Exception:
                pass
            _DB_CONN = None

            shutil.copyfile(tmp_db, DB_PATH)
            for ext in ("-wal", "-shm"):
                stale = DB_PATH + ext
                if os.path.exists(stale):
                    try:
                        os.remove(stale)
                    except Exception:
                        pass

            init_db()

            await update.message.reply_text(
                "✅ **Restore Complete!**\n\n"
                "The bot is now running with the backup data — Products, "
                "Plans, Keys, Custom Prices, Coupons and Settings have all been restored.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Restore failed: {e}")
            await update.message.reply_text(f"❌ Restore failed: {e}")
        finally:
            for p in (tmp_zip, tmp_db):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        return

    if state == 'WAIT_PROD_APK_FILES':
        if user_id != ADMIN_ID:
            return
        pid = context.user_data.get('apklinks_pid')
        confirm_kb = InlineKeyboardMarkup([
            [button("✅ Confirm & Continue", "apklinks_confirm")],
            [button("⏭️ Skip Files", "apklinks_skip_files")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ])
        if not pid:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please try again.")
            return
        doc = update.message.document if update.message else None
        if not doc:
            await update.message.reply_text(
                "❌ Please send the APK as an actual file attachment (not text or a link), "
                "or tap ⏭️ Skip Files.",
                reply_markup=confirm_kb
            )
            return
        files_list = context.user_data.setdefault('temp_apk_files', [])
        file_name = doc.file_name or f"file_{len(files_list) + 1}.apk"
        files_list.append((doc.file_id, file_name))
        await update.message.reply_text(
            f"✅ Added: {file_name}\nTotal APK(s) so far: {len(files_list)}\n\n"
            f"➡️ Send more APK files, or tap ✅ Confirm / ⏭️ Skip Files when done.",
            reply_markup=confirm_kb
        )
        return

    if state == 'WAIT_PROD_APK_VIDEO':
        if user_id != ADMIN_ID:
            return
        pid = context.user_data.get('apklinks_pid')
        skip_kb = InlineKeyboardMarkup([
            [button("⏭️ Skip Video", "apklinks_skip_video")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ])
        if not pid:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please try again.")
            return
        vid = update.message.video if update.message else None
        doc = update.message.document if update.message else None
        file_id = None
        if vid:
            file_id = vid.file_id
        elif doc and (doc.mime_type or "").startswith("video/"):
            file_id = doc.file_id
        if not file_id:
            await update.message.reply_text(
                "❌ Please send the video as an actual video file attachment (not a link/text), "
                "or tap ⏭️ Skip Video.",
                reply_markup=skip_kb
            )
            return

        files = context.user_data.get('temp_apk_files', [])
        links = context.user_data.get('temp_apk_links', [])
        apk_files_str = "\n".join(f"{fid}|||{fname}" for fid, fname in files)
        apk_links_str = "\n".join(links)
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE products SET apk_files = ?, apk_links = ?, apk_video = ? WHERE pid = ?",
            (apk_files_str, apk_links_str, file_id, pid)
        )
        conn.commit()
        context.user_data.pop('apklinks_pid', None)
        context.user_data.pop('temp_apk_files', None)
        context.user_data.pop('temp_apk_links', None)
        context.user_data.pop('temp_apk_video', None)
        context.user_data['state'] = None
        link_note = f" + {len(links)} Link(s)" if links else ""
        await reopen_admin_panel(update, context, f"✅ {len(files)} APK file(s){link_note} + Video saved!")
        return

    if state == 'WAIT_PROD_VIDEO_LINK':
        if user_id != ADMIN_ID:
            return
        pid = context.user_data.get('prodvideo_pid')
        if not pid:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please try again.")
            return

        text_msg = ""
        if update.message and update.message.text:
            text_msg = update.message.text.strip()

        if text_msg.lower() == 'clear':
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("UPDATE products SET demo_video = '' WHERE pid = ?", (pid,))
            conn.commit()
            context.user_data['state'] = None
            await reopen_admin_panel(update, context, "✅ Demo video removed!")
            return

        vid = update.message.video if update.message else None
        doc = update.message.document if update.message else None
        file_id = None
        if vid:
            file_id = vid.file_id
        elif doc and (doc.mime_type or "").startswith("video/"):
            file_id = doc.file_id

        if not file_id:
            await update.message.reply_text(
                "❌ Please send the demo video as an actual video file attachment "
                "(not a link/text), or send `clear` to remove the current video.",
                reply_markup=cancel_button_keyboard()
            )
            return

        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET demo_video = ? WHERE pid = ?", (file_id, pid))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Demo video updated!")
        return

    if not update.message.text:
        return
    text = update.message.text.strip()

    if state == 'WAIT_DEPOSIT_AMT':
        try:
            amt = float(text)
            if amt <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Enter a positive number.")
            return
        context.user_data['state'] = None
        user = get_user(user_id)
        current_bal = user[3] if user[3] is not None else 0.0
        ok, info = await raise_payment_qr(context, user_id, amt, current_bal, purpose='balance', label="Wallet Deposit")
        if not ok:
            if info == "PAYMENT_MAINTENANCE":
                await update.message.reply_text(PAYMENT_MAINTENANCE_MSG, parse_mode="MarkdownV2")
            elif info == "UPI_NOT_SET":
                await update.message.reply_text("❌ *UPI Not Set\\!*\n\nPlease contact admin\\.", parse_mode="MarkdownV2")
            else:
                await update.message.reply_text("❌ Payment QR generation failed. Please try again later.")
        return

    if state == 'WAIT_ORDER_COUPON_CODE':
        context.user_data['state'] = None
        code = text.strip().upper()
        plan_id = context.user_data.get('order_plan_id')
        if not plan_id:
            await update.message.reply_text("❌ Session expired, please try again from /start.")
            return
        ok, result = validate_coupon_for_user(code, user_id)
        if not ok:
            await update.message.reply_text(result)
            return
        _, discount, max_users, used_count, expires_at, source, owner_uid = result
        context.user_data['order_coupon'] = {'code': code, 'discount': discount}
        flow = context.user_data.get('order_flow', 'summary')
        back_cb = "confirm_back_purchase" if flow == 'confirm' else "order_back_summary"
        keyboard = [[button(BTN_TEXT["btn_back"], back_cb)]]
        await update.message.reply_text(
            f"✅ Coupon `{code}` applied\\! \\-{fmt_amount(discount)}% discount added\\.",
            parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if user_id != ADMIN_ID:
        return

    conn = get_db_conn()
    cursor = conn.cursor()

    if state == 'WAIT_SUPPORT_UNAME':
        s_uname = text.replace("@", "").strip()
        set_setting('support_username', s_uname)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Support username updated to `@{s_uname}`!")

    elif state == 'WAIT_PROD_NAMES_BULK':
        names = [n.strip() for n in text.split("\n") if n.strip()]
        if not names:
            await update.message.reply_text("❌ Send at least one product name, one name per line.", reply_markup=cancel_button_keyboard())
            return
        context.user_data['temp_prod_names'] = names
        context.user_data['state'] = 'WAIT_PROD_PIDS_BULK'
        preview = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
        await update.message.reply_text(
            f"✅ Received {len(names)} product name(s):\n{preview}\n\n"
            f"➡️ **Step 2/2:** Now send {len(names)} PID(s), one per line, in the same order as above:\n"
            f"*(Example:*\n`DRIP_NONROOT`\n`HG_NONROOT`\n`PRIME_NONROOT`*)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_PROD_PIDS_BULK':
        names = context.user_data.get('temp_prod_names', [])
        pids_raw = [p.strip() for p in text.split("\n") if p.strip()]
        if len(pids_raw) != len(names):
            await update.message.reply_text(
                f"❌ You sent {len(names)} product name(s) but {len(pids_raw)} PID(s). "
                f"The line count must match — send {len(names)} PID(s) again:",
                reply_markup=cancel_button_keyboard()
            )
            return
        created = []
        cursor.execute("SELECT COALESCE(MAX(sort_order), -1) FROM products")
        max_order_row = cursor.fetchone()
        next_order = (max_order_row[0] if max_order_row and max_order_row[0] is not None else -1) + 1
        for name, pid_raw in zip(names, pids_raw):
            pid = pid_raw.upper().replace(" ", "_")
            cursor.execute("INSERT OR REPLACE INTO products (pid, name, sort_order) VALUES (?, ?, ?)", (pid, name, next_order))
            next_order += 1
            created.append((name, pid))
        conn.commit()
        context.user_data.pop('temp_prod_names', None)
        context.user_data['state'] = None
        summary = "\n".join(f"• **{escape_markdown(n)}** — `{p}`" for n, p in created)
        await reopen_admin_panel(update, context, f"✅ {len(created)} Product(s) Added!\n\n{summary}\n\n➡️ Don't forget to add plans (Manage Product → Add Plan).")

    elif state == 'WAIT_PLAN_DURATIONS_BULK':
        durations = [d.strip() for d in text.split("\n") if d.strip()]
        if not durations:
            await update.message.reply_text("❌ Send at least one duration, one duration per line.", reply_markup=cancel_button_keyboard())
            return
        context.user_data['temp_plan_durations'] = durations
        context.user_data['state'] = 'WAIT_PLAN_REMOTE_DURS_BULK'
        preview = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(durations))
        await update.message.reply_text(
            f"✅ Received {len(durations)} duration(s):\n{preview}\n\n"
            f"➡️ **Step 2/5:** Now send {len(durations)} Remote API Duration code(s), in the same order:\n"
            f"*(Example:*\n`1d`\n`2d`*)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_PLAN_REMOTE_DURS_BULK':
        durations = context.user_data.get('temp_plan_durations', [])
        remotes = [r.strip() for r in text.split("\n") if r.strip()]
        if len(remotes) != len(durations):
            await update.message.reply_text(
                f"❌ {len(durations)} duration(s) need {len(durations)} Remote Duration code(s), "
                f"but you sent {len(remotes)}. Send again:",
                reply_markup=cancel_button_keyboard()
            )
            return
        context.user_data['temp_plan_remotes'] = remotes
        context.user_data['state'] = 'WAIT_PLAN_PRICES_BULK'
        preview = "\n".join(f"{i + 1}. {durations[i]} → `{remotes[i]}`" for i in range(len(durations)))
        await update.message.reply_text(
            f"✅ Remote Duration(s) set:\n{preview}\n\n"
            f"➡️ **Step 3/5:** Now send {len(durations)} Normal Price(s) (₹), in the same order:\n"
            f"*(Example:*\n`9`\n`65`*)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_PLAN_PRICES_BULK':
        durations = context.user_data.get('temp_plan_durations', [])
        price_lines = [p.strip() for p in text.split("\n") if p.strip()]
        if len(price_lines) != len(durations):
            await update.message.reply_text(
                f"❌ {len(durations)} Normal Price(s) needed, but you sent {len(price_lines)}. Send again:",
                reply_markup=cancel_button_keyboard()
            )
            return
        try:
            prices = [float(p) for p in price_lines]
        except ValueError:
            await update.message.reply_text("❌ Send numbers only — one price per line.", reply_markup=cancel_button_keyboard())
            return
        context.user_data['temp_plan_prices'] = prices
        context.user_data['state'] = 'WAIT_PLAN_RESELLER_PRICES_BULK'
        preview = "\n".join(f"{i + 1}. {durations[i]} → ₹{prices[i]:.2f}" for i in range(len(durations)))
        await update.message.reply_text(
            f"✅ Normal Price(s) set:\n{preview}\n\n"
            f"➡️ **Step 4/5:** Now send {len(durations)} Reseller Price(s) (₹), in the same order:\n"
            f"*(Example:*\n`7`\n`55`*)*",
            parse_mode="Markdown",
            reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_PLAN_RESELLER_PRICES_BULK':
        pid = context.user_data.get('temp_pid')
        durations = context.user_data.get('temp_plan_durations', [])
        remotes = context.user_data.get('temp_plan_remotes', [])
        prices = context.user_data.get('temp_plan_prices', [])
        r_lines = [p.strip() for p in text.split("\\n") if p.strip()]
        if not pid or len(r_lines) != len(durations):
            await update.message.reply_text(
                f"❌ {len(durations)} Reseller Price(s) needed, but you sent {len(r_lines)} (or the session expired). Send again:",
                reply_markup=cancel_button_keyboard()
            )
            return
        try:
            r_prices = [float(p) for p in r_lines]
        except ValueError:
            await update.message.reply_text("❌ Send numbers only — one price per line.", reply_markup=cancel_button_keyboard())
            return

        for i in range(len(durations)):
            cursor.execute(
                "INSERT INTO plans (product_pid, duration, remote_duration, price, reseller_price) VALUES (?, ?, ?, ?, ?)",
                (pid, durations[i], remotes[i], prices[i], r_prices[i])
            )
        conn.commit()
        summary = "\\n".join(
            f"• {durations[i]} — Normal ₹{prices[i]:.2f} / Reseller ₹{r_prices[i]:.2f}"
            for i in range(len(durations))
        )
        for k in ('temp_pid', 'temp_plan_durations', 'temp_plan_remotes', 'temp_plan_prices', 'temp_plan_reseller_prices'):
            context.user_data.pop(k, None)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ {len(durations)} Plan(s) Added!\\n\\n{summary}")

    elif state == 'WAIT_EDIT_PLAN_DURATION_NAME':
        plan_id = context.user_data.get('editplan_id')
        cursor.execute("UPDATE plans SET duration = ? WHERE id = ?", (text, plan_id))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Plan duration name updated!")

    elif state == 'WAIT_EDIT_PLAN_REMOTE_DUR':
        plan_id = context.user_data.get('editplan_id')
        cursor.execute("UPDATE plans SET remote_duration = ? WHERE id = ?", (text, plan_id))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Plan remote duration updated!")

    elif state == 'WAIT_KEYS_INPUT':
        pid = context.user_data.get('key_pid')
        dur = context.user_data.get('key_dur')
        keys_list = [k.strip() for k in text.split("\n") if k.strip()]
        for k in keys_list:
            cursor.execute("INSERT INTO keys (product_pid, duration, key_value) VALUES (?, ?, ?)", (pid, dur, k))
        if keys_list:
            cursor.execute("""
                INSERT INTO manual_stock_alert_state(product_pid, duration, alerted, updated_at)
                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(product_pid, duration)
                DO UPDATE SET alerted = 0, updated_at = CURRENT_TIMESTAMP
            """, (str(pid), str(dur)))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Added {len(keys_list)} keys successfully!")

    elif state == 'WAIT_UPI_ID':
        set_setting('fampay_upi', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ FamPay UPI ID set to `{mask_secret(text)}`!")

    elif state == 'WAIT_FAMPAY_KEY':
        set_setting('fampay_api_key', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ FamPay API Key updated!")

    elif state == 'WAIT_PAYTM_UPI':
        set_setting('paytm_upi', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Paytm UPI ID set!")

    elif state == 'WAIT_PAYTM_MID':
        set_setting('paytm_merchant_id', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Paytm Merchant ID set!")

    elif state == 'WAIT_PAYTM_MKEY':
        set_setting('paytm_merchant_key', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Paytm Merchant Key set!")

    elif state == 'WAIT_STORE_API_KEY':
        set_setting('store_api_key', text)
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Store API Key set!")

    elif state == 'WAIT_AI_KEY_INPUT':
        new_key = text.strip()
        set_setting('ai_api_key', new_key)
        _AI_WORKING["model"] = None
        _AI_WORKING["auth"] = None
        context.user_data['state'] = None
        warn = ""
        if not (new_key.startswith("AIzaSy") or new_key.startswith("AQ.")):
            warn = "\n\n⚠️ Note: Gemini keys normally start with `AIzaSy` or `AQ.` — double-check this key if AI Support still errors."
        try:
            await update.message.delete()
        except Exception:
            pass
        await reopen_admin_panel(update, context, f"✅ AI API Key updated!{warn}")

    elif state == 'WAIT_PLAN_RESELLER_PRICE':
        try:
            r_price = float(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return
        plan_id = context.user_data.get('edit_plan_id')
        cursor.execute("SELECT products.name, plans.duration, plans.reseller_price FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        prow = cursor.fetchone()
        cursor.execute("UPDATE plans SET reseller_price = ? WHERE id = ?", (r_price, plan_id))
        conn.commit()
        context.user_data['state'] = None
        if prow:
            msg = (
                f"✅ Plan Reseller Price updated!\n\n"
                f"📦 **Product:** {escape_markdown(prow[0])}\n"
                f"⏱️ **Plan:** {escape_markdown(prow[1])}\n"
                f"💵 **Old price:** ₹{prow[2]:.2f}\n"
                f"👑 **New Reseller price:** ₹{r_price:.2f}"
            )
        else:
            msg = "✅ Plan Reseller Price updated!"
        await reopen_admin_panel(update, context, msg)

    elif state == 'WAIT_RESELLER_UPGRADE_PRICE':
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid price.")
            return
        set_setting('reseller_upgrade_price', str(price))
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Reseller Upgrade Price set to ₹{price:.2f}!")

    elif state == 'WAIT_ADD_RESELLER_ID':
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID.")
            return
        cursor.execute("UPDATE users SET is_reseller = 1 WHERE user_id = ?", (uid,))
        conn.commit()
        try:
            notif_keyboard = InlineKeyboardMarkup([[
                button(BTN_TEXT["btn_shop"], "user_shop")
            ]])
            await context.bot.send_message(
                chat_id=uid,
                text=build_manual_reseller_upgrade_text(),
                parse_mode="MarkdownV2",
                reply_markup=notif_keyboard
            )
        except Exception as e:
            logging.error(f"Failed to notify user {uid} of manual reseller upgrade: {e}")
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ User `{uid}` promoted to Reseller!")

    elif state == 'WAIT_REM_RESELLER_ID':
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID.")
            return
        cursor.execute("UPDATE users SET is_reseller = 0 WHERE user_id = ?", (uid,))
        conn.commit()
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=build_manual_reseller_removed_text(),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logging.error(f"Failed to notify user {uid} of manual reseller removal: {e}")
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ User `{uid}` demoted from Reseller!")

    elif state == 'WAIT_NEW_PLAN_PRICE':
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid price.")
            return
        plan_id = context.user_data.get('chg_plan_id')
        cursor.execute("SELECT products.name, plans.duration, plans.price FROM plans JOIN products ON plans.product_pid = products.pid WHERE plans.id = ?", (plan_id,))
        prow = cursor.fetchone()
        cursor.execute("UPDATE plans SET price = ? WHERE id = ?", (price, plan_id))
        conn.commit()
        context.user_data['state'] = None
        if prow:
            msg = (
                f"✅ Plan Normal Price updated!\n\n"
                f"📦 **Product:** {escape_markdown(prow[0])}\n"
                f"⏱️ **Plan:** {escape_markdown(prow[1])}\n"
                f"💵 **Old price:** ₹{prow[2]:.2f}\n"
                f"💲 **New Normal price:** ₹{price:.2f}"
            )
        else:
            msg = "✅ Plan Normal Price updated!"
        await reopen_admin_panel(update, context, msg)

    elif state == 'WAIT_NEW_PROD_NAME':
        pid = context.user_data.get('rename_pid')
        cursor.execute("UPDATE products SET name = ? WHERE pid = ?", (text, pid))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Product renamed successfully!")

    elif state == 'WAIT_PRODUCT_VARIANT_ID':
        pid = context.user_data.get('api_variant_pid')
        if not pid:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired. Open Manage Product → Set API Variant ID again.")
            return
        value = text.strip()
        if value.lower() == 'clear':
            value = ''
        elif not value.isdigit() or int(value) <= 0:
            await update.message.reply_text("❌ Send a positive numeric Bantibhaiya Variant ID, or `clear` to remove it.", parse_mode="Markdown", reply_markup=cancel_button_keyboard())
            return
        cursor.execute("UPDATE products SET variant_id = ? WHERE pid = ?", (value, pid))
        conn.commit()
        context.user_data['state'] = None
        context.user_data.pop('api_variant_pid', None)
        await reopen_admin_panel(update, context, "ℹ️ This legacy Variant ID is not used by the Bantibhaiya integration. API purchases use the product PID and the plan's Remote Duration.")

    elif state == 'WAIT_NEW_PROD_PID':
        old_pid = context.user_data.get('chgpid_old_pid')
        if not old_pid:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please try again.")
            return
        new_pid = text.strip().upper().replace(" ", "_")
        if not new_pid:
            await update.message.reply_text("❌ Invalid PID. Please send a valid PID.", reply_markup=cancel_button_keyboard())
            return
        if new_pid == old_pid:
            context.user_data['state'] = None
            context.user_data.pop('chgpid_old_pid', None)
            await reopen_admin_panel(update, context, "ℹ️ PID unchanged — it's already that value.")
            return
        cursor.execute("SELECT 1 FROM products WHERE pid = ?", (new_pid,))
        if cursor.fetchone():
            await update.message.reply_text(
                f"❌ PID `{new_pid}` is already used by another product. Send a different PID.",
                parse_mode="Markdown", reply_markup=cancel_button_keyboard()
            )
            return
        try:
            cursor.execute("UPDATE products SET pid = ? WHERE pid = ?", (new_pid, old_pid))
            cursor.execute("UPDATE plans SET product_pid = ? WHERE product_pid = ?", (new_pid, old_pid))
            cursor.execute("UPDATE keys SET product_pid = ? WHERE product_pid = ?", (new_pid, old_pid))
            cursor.execute("UPDATE product_offer_users SET pid = ? WHERE pid = ?", (new_pid, old_pid))
            conn.commit()
        except Exception as e:
            conn.rollback()
            context.user_data['state'] = None
            context.user_data.pop('chgpid_old_pid', None)
            await update.message.reply_text(f"❌ Failed to change PID: {e}")
            return
        context.user_data['state'] = None
        context.user_data.pop('chgpid_old_pid', None)
        await reopen_admin_panel(
            update, context,
            f"✅ PID changed!\n\n🆔 Old: `{old_pid}`\n🆔 New: `{new_pid}`\n\n"
            f"Plans & Key stock moved automatically. Update any saved deep link to use the new PID."
        )

    elif state == 'WAIT_PRODUCT_LINEUP':
        cursor.execute("SELECT pid FROM products")
        all_pids = set(r[0] for r in cursor.fetchall())
        lines = [l.strip().upper() for l in text.split("\n") if l.strip()]
        given_pids = set(lines)
        if len(lines) != len(given_pids):
            await update.message.reply_text(
                "❌ Duplicate PID(s) found in your list. Send the full list again, one PID per line, no repeats.",
                reply_markup=cancel_button_keyboard()
            )
            return
        if given_pids != all_pids:
            missing = all_pids - given_pids
            extra = given_pids - all_pids
            err = "❌ PID list doesn't match your current products."
            if missing:
                err += f"\n\n⚠️ Missing: {', '.join(sorted(missing))}"
            if extra:
                err += f"\n\n⚠️ Unknown: {', '.join(sorted(extra))}"
            err += "\n\nSend the full corrected list again (every PID, one per line):"
            await update.message.reply_text(err, reply_markup=cancel_button_keyboard())
            return
        for i, pid in enumerate(lines):
            cursor.execute("UPDATE products SET sort_order = ? WHERE pid = ?", (i, pid))
        conn.commit()
        context.user_data['state'] = None
        cursor.execute("SELECT pid, name FROM products ORDER BY sort_order, name")
        new_order = cursor.fetchall()
        preview = "\n".join(f"{i + 1}. {escape_markdown(p[1])}" for i, p in enumerate(new_order))
        await reopen_admin_panel(update, context, f"✅ **Product Order Updated!**\n\n{preview}")

    elif state == 'WAIT_HOW_VIDEO_URL':
        if text.lower() == 'clear':
            set_setting('how_to_use_video', '')
            context.user_data['state'] = None
            await reopen_admin_panel(update, context, "✅ How-To-Use video removed!")
        else:
            set_setting('how_to_use_video', text)
            context.user_data['state'] = None
            await reopen_admin_panel(update, context, "✅ How-To-Use video URL updated!")

    elif state == 'WAIT_GROUP_LINK':
        pid = context.user_data.get('grplink_pid')
        cursor.execute("UPDATE products SET group_link = ? WHERE pid = ?", (text, pid))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Group/Updates link updated!")

    elif state == 'WAIT_ADD_BAL_INPUT':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `<user_id> <amount>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[0])
            amt = float(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID or Amount.")
            return
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
        bal_row = cursor.fetchone()
        new_bal = bal_row[0] if bal_row and bal_row[0] is not None else amt
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=build_balance_added_text(amt, new_bal),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logging.error(f"Failed to notify user {uid} of balance add: {e}")
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ ₹{amt:.2f} added to user `{uid}`'s balance!")

    elif state == 'WAIT_REM_BAL_INPUT':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `<user_id> <amount>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[0])
            amt = float(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID or Amount.")
            return
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, uid))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ ₹{amt:.2f} removed from user `{uid}`'s balance!")

    elif state == 'WAIT_PROD_DESCRIPTION':
        pid = context.user_data.get('proddesc_pid')
        new_desc = "" if text.lower() == "clear" else text
        cursor.execute("UPDATE products SET description = ? WHERE pid = ?", (new_desc, pid))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, "✅ Product description updated!")

    elif state == 'WAIT_PROD_APK_LINKS':
        new_links = [l.strip() for l in text.split("\n") if l.strip()]
        confirm_kb = InlineKeyboardMarkup([
            [button("✅ Confirm & Continue", "apklinks_confirm")],
            [button("⏭️ Skip Links", "apklinks_skip_links")],
            [button("❌ Cancel", "admin_cancel_flow")]
        ])
        if not new_links:
            await update.message.reply_text("❌ Send at least one link, or tap ⏭️ Skip Links.", reply_markup=confirm_kb)
            return
        links_list = context.user_data.setdefault('temp_apk_links', [])
        links_list.extend(new_links)
        await update.message.reply_text(
            f"✅ Added {len(new_links)} link(s). Total so far: {len(links_list)}\n\n"
            f"➡️ Send more links, or tap ✅ Confirm / ⏭️ Skip Links when done.",
            reply_markup=confirm_kb
        )

    elif state == 'WAIT_CUSTOM_PRICE_INPUT':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `<user_id> <price>`", parse_mode="Markdown")
            return
        try:
            uid = int(parts[0])
            price = float(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID or Price.")
            return
        plan_id = context.user_data.get('custprice_plan_id')
        cursor.execute("INSERT OR REPLACE INTO custom_prices (user_id, plan_id, price) VALUES (?, ?, ?)", (uid, plan_id, price))
        conn.commit()
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Custom price ₹{price:.2f} set for user `{uid}`!")

    elif state == 'WAIT_CHECK_BAL_ID':
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID.")
            return
        cursor.execute("SELECT full_name, username, balance, is_reseller FROM users WHERE user_id = ?", (uid,))
        row = cursor.fetchone()
        context.user_data['state'] = None
        if not row:
            await update.message.reply_text("❌ User not found.")
        else:
            name = escape_markdown(row[0]) if row[0] else "N/A"
            uname = f"@{escape_markdown(row[1])}" if row[1] else "N/A"
            bal = row[2] if row[2] is not None else 0.0
            acc = "🌟 Reseller" if row[3] else "👤 User"
            await update.message.reply_text(
                f"🔍 **USER BALANCE INFO** 🔍\n\n"
                f"👤 **Name:** {name}\n"
                f"🏷️ **Username:** {uname}\n"
                f"🆔 **User ID:** `{uid}`\n"
                f"💰 **Balance:** ₹{bal:.2f}\n"
                f"🏷️ **Account Type:** {acc}",
                parse_mode="Markdown"
            )

    elif state == 'WAIT_COUPON_PERCENT':
        try:
            pct = float(text)
            if pct <= 0 or pct > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid %. Send a number between 1 and 100.")
            return
        context.user_data['temp_coupon_pct'] = pct
        context.user_data['state'] = 'WAIT_COUPON_MAX_USERS'
        await update.message.reply_text(
            "➡️ **Step 2/3:** How many users can use this coupon?\n*(Example: `10` or `20`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_COUPON_MAX_USERS':
        try:
            max_u = int(text)
            if max_u <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid number.")
            return
        context.user_data['temp_coupon_maxu'] = max_u
        context.user_data['state'] = 'WAIT_COUPON_VALIDITY'
        await update.message.reply_text(
            "➡️ **Step 3/3:** How long should the coupon stay valid?\n*(Example: `24 hours` or `3 days`)*",
            parse_mode="Markdown", reply_markup=cancel_button_keyboard()
        )

    elif state == 'WAIT_COUPON_VALIDITY':
        hours = parse_duration_hours(text)
        if hours == float('inf'):
            await update.message.reply_text("❌ Couldn't understand the format. Example: `24 hours` or `3 days`.", parse_mode="Markdown")
            return
        pct = context.user_data.pop('temp_coupon_pct', None)
        max_u = context.user_data.pop('temp_coupon_maxu', None)
        context.user_data['state'] = None
        if pct is None or max_u is None:
            await update.message.reply_text("❌ Session expired, please try again.")
            return
        code, expires_at = create_coupon(pct, max_u, hours, source='admin')
        await reopen_admin_panel(
            update, context,
            f"✅ **Coupon Created!**\n\n"
            f"🎫 Code: `{code}`\n💯 Discount: {pct}%\n👥 Max Users: {max_u}\n⏳ Valid Till: {to_ist_str(expires_at)} IST"
        )

    elif state == 'WAIT_SPIN_BAL_RANGE':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: `<min> <max>`", parse_mode="Markdown")
            return
        try:
            lo, hi = float(parts[0]), float(parts[1])
            if lo <= 0 or hi <= lo:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid range.")
            return
        set_spin_setting('spin_balance_min', str(lo))
        set_spin_setting('spin_balance_max', str(hi))
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Spin balance range set: ₹{lo} – ₹{hi}")

    elif state == 'WAIT_SPIN_COUPON_RANGE':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: `<min> <max>`", parse_mode="Markdown")
            return
        try:
            lo, hi = float(parts[0]), float(parts[1])
            if lo <= 0 or hi <= lo or hi > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid range.")
            return
        set_spin_setting('spin_coupon_min', str(lo))
        set_spin_setting('spin_coupon_max', str(hi))
        context.user_data['state'] = None
        await reopen_admin_panel(update, context, f"✅ Spin coupon % range set: {lo}% – {hi}%")

    elif state == 'WAIT_SPIN_CHANCE':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: `<coupon%> <balance%>`  (Example: `2 1`)", parse_mode="Markdown")
            return
        try:
            c_chance, b_chance = float(parts[0]), float(parts[1])
            if c_chance < 0 or b_chance < 0 or c_chance + b_chance > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Both numbers must be between 0 and 100, and their total must not exceed 100.")
            return
        set_spin_setting('spin_coupon_chance', str(c_chance))
        set_spin_setting('spin_balance_chance', str(b_chance))
        context.user_data['state'] = None
        better = round(100 - c_chance - b_chance, 2)
        await reopen_admin_panel(
            update, context,
            f"✅ Spin odds set:\n🎫 Coupon {c_chance}%\n💰 Balance {b_chance}%\n😅 Better Luck Next Time {better}%"
        )

    elif state == 'WAIT_GLOBAL_OFFER_PERCENT':
        try:
            pct = float(text)
            if pct < 0 or pct > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid percentage. Send a number between 0 and 100 (0 disables the offer).")
            return
        set_global_offer_percent(pct)
        context.user_data['state'] = None
        if pct > 0:
            await reopen_admin_panel(
                update, context,
                f"✅ **All Users Offer set to {pct}% OFF!**\n\nEvery user now sees this discount on every product's price."
            )
        else:
            await reopen_admin_panel(update, context, "✅ All Users Offer disabled. Normal pricing restored for everyone.")

    # ===== PRODUCT OFFER: step 1 — discount % =====
    elif state == 'WAIT_PRODUCT_OFFER_PERCENT':
        pid = context.user_data.get('prodoffer_pid')
        target = context.user_data.get('prodoffer_target')
        if not pid or target not in OFFER_TARGET_LABELS:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please open Product Offer again.")
            return
        try:
            pct = float(text.replace("%", "").strip())
            if pct <= 0 or pct > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid percentage. Send a number between 1 and 100 (Example: `5` or `10`).",
                parse_mode="Markdown", reply_markup=cancel_button_keyboard()
            )
            return

        if target == "custom":
            context.user_data['prodoffer_pct'] = pct
            context.user_data['state'] = 'WAIT_PRODUCT_OFFER_USERS'
            await update.message.reply_text(
                f"✅ Discount set: **{fmt_pct(pct)}%**\n\n"
                f"➡️ Now send the **User ID(s)** who should get this offer — one per line "
                f"or separated by spaces:\n*(Example:*\n`123456789`\n`987654321`*)*",
                parse_mode="Markdown",
                reply_markup=cancel_button_keyboard()
            )
            return

        save_product_offer(pid, pct, target)
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prow = cursor.fetchone()
        pname = escape_markdown(prow[0]) if prow else escape_markdown(pid)
        for k in ('prodoffer_pid', 'prodoffer_target', 'prodoffer_pct'):
            context.user_data.pop(k, None)
        context.user_data['state'] = None
        await reopen_admin_panel(
            update, context,
            f"✅ **Product Offer Set!**\n\n"
            f"📦 **Product:** {pname}\n"
            f"💯 **Discount:** {fmt_pct(pct)}% OFF\n"
            f"👥 **For:** {OFFER_TARGET_LABELS[target]}"
        )

    # ===== PRODUCT OFFER: step 2 (custom users only) — user IDs =====
    elif state == 'WAIT_PRODUCT_OFFER_USERS':
        pid = context.user_data.get('prodoffer_pid')
        pct = context.user_data.get('prodoffer_pct')
        if not pid or not pct:
            context.user_data['state'] = None
            await update.message.reply_text("❌ Session expired, please open Product Offer again.")
            return
        ids = []
        for tok in re.findall(r"\d+", text):
            val = int(tok)
            if val not in ids:
                ids.append(val)
        if not ids:
            await update.message.reply_text(
                "❌ No valid User ID found. Send numeric User ID(s), one per line.",
                reply_markup=cancel_button_keyboard()
            )
            return
        save_product_offer(pid, pct, "custom", ids)
        cursor.execute("SELECT name FROM products WHERE pid = ?", (pid,))
        prow = cursor.fetchone()
        pname = escape_markdown(prow[0]) if prow else escape_markdown(pid)
        shown = ", ".join(f"`{u}`" for u in ids[:30])
        more = f" …+{len(ids) - 30} more" if len(ids) > 30 else ""
        for k in ('prodoffer_pid', 'prodoffer_target', 'prodoffer_pct'):
            context.user_data.pop(k, None)
        context.user_data['state'] = None
        await reopen_admin_panel(
            update, context,
            f"✅ **Product Offer Set!**\n\n"
            f"📦 **Product:** {pname}\n"
            f"💯 **Discount:** {fmt_pct(pct)}% OFF\n"
            f"👥 **For:** {OFFER_TARGET_LABELS['custom']} ({len(ids)})\n"
            f"🆔 {shown}{more}"
        )

    elif state == 'WAIT_AI_SUPPORT_QUESTION':
        back_kb = InlineKeyboardMarkup([[back_button("admin_back_panel")]])

        if not get_ai_api_key():
            q = text.lower()
            keyword_map = {
                'prodoffer': ['product offer', 'offer on product', 'single product offer', 'one product offer', 'product discount'],
                'getnum': ['get all num', 'all num', 'phone number', 'numbers', 'verified user'],
                'product': ['product', 'plan', 'pid', 'name'],
                'keys': ['key', 'stock'],
                'pricing': ['price', 'custom'],
                'reseller': ['reseller'],
                'payment': ['payment', 'upi', 'fampay', 'paytm', 'qr'],
                'settings': ['api', 'setting', 'support link', 'video'],
                'extra': ['maintenance', 'toggle', 'stat'],
                'apks': ['apk', 'download link', 'download links'],
                'additional': ['deep link', 'description', 'demo'],
                'balance': ['balance', 'wallet'],
                'coupons': ['coupon', 'spin', 'discount'],
                'backup': ['backup', 'restore'],
                'payments_report': ['payment received', 'check all payment', 'all payments', 'today payment', "today's payment"],
                'pid': ['change pid', 'pid change', 'rename pid'],
                'lineup': ['line up', 'lineup', 'reorder', 'order products', 'product order'],
                'offer': ['all users offer', 'global offer', 'sitewide offer', 'offer for all', 'all user offer'],
                'adminmgmt': ['admin management', 'add admin', 'remove admin', 'broadcast admin', 'check admins'],
                'ban': ['ban', 'unban', 'banned', 'ban list', 'ban management'],
                'variant': ['variant', 'variant id'],
            }
            topic = next((t for t, kws in keyword_map.items() if any(k in q for k in kws)), None)
            answer = AI_HELP_TEXTS.get(
                topic,
                "❓ Please be more specific — product / keys / pricing / reseller / payment / settings / apks / coupons / balance / backup / pid / lineup / offer / product offer / admin management / ban management / variant id."
            )
            try:
                await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=back_kb)
            except Exception:
                await update.message.reply_text(answer.replace("*", "").replace("`", ""), reply_markup=back_kb)
            return

        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        except Exception:
            pass

        history = context.user_data.setdefault('ai_history', [])
        history.append({"role": "user", "content": text})
        ok, reply = await ask_ai(history)
        if ok:
            history.append({"role": "assistant", "content": reply})
            if len(history) > 20:
                del history[:len(history) - 20]
            await send_long_text(update.message, reply, reply_markup=back_kb)
        else:
            history.pop()
            await update.message.reply_text(
                f"❌ AI error: {reply}\n\n"
                f"👉 Fix: ⚙️ Settings → 🤖 Set AI API Key → paste a fresh Gemini key "
                f"(from aistudio.google.com/apikey).",
                reply_markup=back_kb
            )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Unhandled exception: {context.error}", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Something went wrong. Please try again or send /start."
            )
    except Exception:
        pass


# ============================================================
# PUBLIC RESELLER API (Railway-compatible, standard-library HTTP server)
# ============================================================
# Configure PUBLIC_API_KEY and PUBLIC_API_USER_ID in Railway Variables.
# This API uses one configured wallet account; clients cannot choose arbitrary
# Telegram user IDs and spend other users' balances.
PUBLIC_API_KEY = os.getenv("PUBLIC_API_KEY", "").strip()
PUBLIC_API_USER_ID = os.getenv("PUBLIC_API_USER_ID", "").strip()
_API_SERVER = None
_API_SERVER_THREAD = None
_API_REQUEST_LOCK = threading.RLock()


def _api_json_response(handler, status_code, payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _api_auth_ok(handler):
    expected = (os.getenv("PUBLIC_API_KEY", "") or "").strip()
    supplied = (handler.headers.get("X-API-Key", "") or "").strip()
    auth = (handler.headers.get("Authorization", "") or "").strip()
    if not supplied and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    return bool(expected and supplied and __import__("hmac").compare_digest(expected, supplied))


def _api_configured_user():
    raw = (os.getenv("PUBLIC_API_USER_ID", "") or "").strip()
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _api_ensure_tables():
    with DB_LOCK:
        conn = get_db_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_orders (
                request_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def _api_get_products():
    with DB_LOCK:
        conn = get_db_conn()
        rows = conn.execute("""
            SELECT p.pid, p.name, COALESCE(p.description, ''), COALESCE(p.is_maintenance, 0)
            FROM products p ORDER BY COALESCE(p.sort_order, 0), p.name
        """).fetchall()
    return [{"pid": str(r[0]), "name": str(r[1] or ""), "description": str(r[2] or ""),
             "maintenance": bool(r[3])} for r in rows]


def _api_get_plans(pid):
    with DB_LOCK:
        conn = get_db_conn()
        rows = conn.execute("""
            SELECT id, product_pid, duration, remote_duration, price, reseller_price
            FROM plans WHERE product_pid = ? ORDER BY id
        """, (str(pid),)).fetchall()
    return [{"plan_id": int(r[0]), "pid": str(r[1]), "duration": str(r[2] or ""),
             "remote_duration": str(r[3] or ""), "price": float(r[4] or 0),
             "reseller_price": float(r[5] or 0)} for r in rows]


def _api_wallet_details():
    user_id = _api_configured_user()
    if user_id is None:
        return None, None, "PUBLIC_API_USER_ID is not configured in Railway Variables."
    with DB_LOCK:
        conn = get_db_conn()
        row = conn.execute("SELECT balance, is_reseller, full_name, username FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return user_id, None, "Configured API wallet user does not exist in the bot database. Open the bot with that Telegram account (/start), then ensure its wallet is funded."
    return user_id, row, None


def _api_telegram_send(chat_id, text):
    token = (os.getenv("BOT_TOKEN", "") or BOT_TOKEN or "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": str(text)[:3900], "disable_web_page_preview": True},
            timeout=6,
        )
    except Exception:
        logging.exception("API purchase notification failed")


def _api_buy(payload):
    """Buy by plans.id, optionally validating product PID; debit the configured API wallet."""
    user_id, wallet, config_error = _api_wallet_details()
    if config_error:
        return 503, {"ok": False, "error": config_error}
    try:
        plan_id = int(payload.get("plan_id", payload.get("planId", payload.get("plan", 0))))
    except (TypeError, ValueError):
        plan_id = 0
    if plan_id <= 0:
        return 400, {"ok": False, "error": "plan_id is required and must be a positive integer."}
    requested_pid = str(payload.get("pid", payload.get("product_pid", payload.get("productPid", ""))) or "").strip()
    request_id = str(payload.get("request_id", payload.get("idempotency_key", "")) or "").strip()[:120]
    if not request_id:
        return 400, {"ok": False, "error": "request_id is required. Send a unique ID for each new purchase to prevent duplicate orders."}

    conn = get_db_conn()
    with _API_REQUEST_LOCK, DB_LOCK:
        if request_id:
            previous = conn.execute("SELECT response_json FROM api_orders WHERE request_id = ?", (request_id,)).fetchone()
            if previous:
                try:
                    return 200, json.loads(previous[0])
                except Exception:
                    pass
        plan = conn.execute("""
            SELECT pl.id, pl.product_pid, pl.duration, pl.remote_duration, pl.price,
                   pl.reseller_price, COALESCE(pl.variant_id, ''), COALESCE(pr.name, 'Unknown Product'),
                   COALESCE(pr.is_maintenance, 0), COALESCE(pr.group_link, '')
            FROM plans pl LEFT JOIN products pr ON pr.pid = pl.product_pid WHERE pl.id = ?
        """, (plan_id,)).fetchone()
        if not plan:
            return 404, {"ok": False, "error": "Plan ID not found.", "plan_id": plan_id}
        pid = str(plan[1] or "").strip()
        if requested_pid and requested_pid != pid:
            return 400, {"ok": False, "error": "plan_id does not belong to the supplied product PID.", "plan_id": plan_id, "pid": pid}
        if bool(plan[8]):
            return 409, {"ok": False, "error": "This product is currently under maintenance.", "pid": pid, "plan_id": plan_id}
        wallet = conn.execute("SELECT balance, is_reseller, full_name, username FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not wallet:
            return 503, {"ok": False, "error": "Configured API wallet user was not found."}
        cost = float(get_effective_price(user_id, plan_id, plan[4], plan[5], int(wallet[1] or 0), pid))
        if cost < 0:
            return 500, {"ok": False, "error": "Invalid plan price."}
        balance = float(wallet[0] or 0.0)
        if balance < cost:
            return 402, {"ok": False, "error": "Insufficient API wallet balance.", "required": round(cost, 2), "balance": round(balance, 2)}
        order_id = _new_order_id()
        prod_name = str(plan[7] or "Unknown Product")
        duration = str(plan[2] or "")
        key_value = ""
        method = ""
        remaining = balance
        # First try manual stock; claim stock and debit in the same DB transaction.
        try:
            conn.execute("BEGIN IMMEDIATE")
            fresh = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
            fresh_balance = float(fresh[0] or 0.0) if fresh else 0.0
            stock = conn.execute("SELECT id, key_value FROM keys WHERE product_pid = ? AND duration = ? ORDER BY id LIMIT 1", (pid, duration)).fetchone()
            if fresh_balance < cost:
                conn.rollback()
                return 402, {"ok": False, "error": "Insufficient API wallet balance.", "required": round(cost, 2), "balance": round(fresh_balance, 2)}
            if stock:
                deleted = conn.execute("DELETE FROM keys WHERE id = ?", (stock[0],)).rowcount
                if deleted != 1:
                    conn.rollback()
                    return 409, {"ok": False, "error": "Stock changed; retry with a new request_id."}
                key_value = str(stock[1] or "")
                method = "Manual Key"
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (cost, user_id, cost))
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    conn.rollback()
                    return 402, {"ok": False, "error": "Insufficient API wallet balance."}
                conn.execute("INSERT INTO history (user_id, item_name, key_value, price, order_id, purchase_method) VALUES (?, ?, ?, ?, ?, ?)",
                             (user_id, f"{prod_name} ({duration})", key_value, cost, order_id, "API Manual Key"))
                conn.commit()
                remaining = fresh_balance - cost
            else:
                conn.rollback()
                # Reserve the wallet amount before making an external purchase.
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (cost, user_id, cost))
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    conn.rollback()
                    return 402, {"ok": False, "error": "Insufficient API wallet balance."}
                conn.commit()
                remote_duration = str(plan[3] or "").strip()
                if not pid or not remote_duration:
                    with DB_LOCK:
                        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (cost, user_id)); conn.commit()
                    return 422, {"ok": False, "error": "Product PID or remote_duration is missing; wallet was refunded.", "pid": pid, "plan_id": plan_id}
                success, api_result = _call_reseller_api_sync(pid, remote_duration, str(plan[6] or ""))
                if not success:
                    with DB_LOCK:
                        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (cost, user_id)); conn.commit()
                    return 502, {"ok": False, "error": "Upstream provider rejected the order; wallet was refunded.", "details": str(api_result)[:500], "pid": pid, "plan_id": plan_id}
                key_value = str(api_result)
                method = "API"
                with DB_LOCK:
                    conn.execute("INSERT INTO history (user_id, item_name, key_value, price, order_id, purchase_method) VALUES (?, ?, ?, ?, ?, ?)",
                                 (user_id, f"{prod_name} ({duration})", key_value, cost, order_id, "Public API Purchase"))
                    conn.commit()
                    bal = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
                    remaining = float(bal[0] or 0.0) if bal else 0.0
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            logging.exception("Public API purchase processing failed")
            return 500, {"ok": False, "error": "Internal purchase error. Check server logs before retrying."}

        result = {
            "ok": True, "order_id": order_id, "status": "delivered", "pid": pid,
            "plan_id": plan_id, "product": prod_name, "duration": duration,
            "amount": round(cost, 2), "balance": round(remaining, 2),
            "method": method, "key": key_value,
        }
        if request_id:
            try:
                conn.execute("INSERT INTO api_orders (request_id, order_id, response_json) VALUES (?, ?, ?)",
                             (request_id, order_id, json.dumps(result, ensure_ascii=False)))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                previous = conn.execute("SELECT response_json FROM api_orders WHERE request_id = ?", (request_id,)).fetchone()
                if previous:
                    return 200, json.loads(previous[0])
        admin_text = (
            "🛒 PUBLIC API ORDER SUCCESS\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🧾 Order ID: {order_id}\n👤 API Wallet User: {user_id}\n"
            f"📦 Product: {prod_name}\n🆔 PID: {pid}\n🗂 Plan ID: {plan_id}\n"
            f"⏱ Plan: {duration}\n🔄 Method: {method}\n💵 Amount: ₹{cost:.2f}\n"
            f"💰 Remaining: ₹{remaining:.2f}\n🔑 Key: {key_value}"
        )
        _api_telegram_send(ADMIN_ID, admin_text)
        group_link = str(plan[9] or "").strip()
        if GROUP_ID:
            _api_telegram_send(GROUP_ID,
                "🛒 NEW API ORDER\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 {prod_name}\n⏱ {duration}\n💵 ₹{cost:.2f}\n"
                f"🧾 Order: {order_id}\n✅ Delivered successfully")
        return 200, result


class _PublicAPIHandler(BaseHTTPRequestHandler):
    server_version = "AllStoreAPI/1.0"

    def log_message(self, fmt, *args):
        logging.info("Public API: " + (fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/", "/health"):
            return _api_json_response(self, 200, {"ok": True, "service": "ALL STORE SHOP API", "docs": "/docs", "api_base": "/api/v1"})
        if path == "/docs":
            html = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ALL STORE API Docs</title><style>body{font:16px Arial;max-width:900px;margin:28px auto;padding:0 16px;background:#0b1220;color:#e7eefc}code,pre{background:#172238;padding:3px 6px;border-radius:6px;overflow-wrap:anywhere}h1,h2{color:#75b8ff}.box{border:1px solid #33445f;border-radius:12px;padding:14px;margin:14px 0}</style></head><body><h1>🛍️ ALL STORE SHOP API</h1><p>Use this API to list products/plans, check the configured wallet and purchase by plan ID + product PID.</p><div class="box"><h2>Authentication</h2><p>Send <code>X-API-Key: YOUR_PUBLIC_API_KEY</code> with every <code>/api/v1/*</code> request.</p></div><div class="box"><h2>Products</h2><pre>GET /api/v1/products</pre></div><div class="box"><h2>Plans for a product</h2><pre>GET /api/v1/products/{PID}/plans</pre><p>Each plan includes <code>plan_id</code>, <code>pid</code>, <code>duration</code>, <code>remote_duration</code>, and price.</p></div><div class="box"><h2>Wallet balance</h2><pre>GET /api/v1/balance</pre><p>Balance belongs to the account configured by <code>PUBLIC_API_USER_ID</code> on Railway.</p></div><div class="box"><h2>Buy</h2><pre>POST /api/v1/buy
Content-Type: application/json
X-API-Key: YOUR_PUBLIC_API_KEY

{
  "pid": "PRODUCT_PID",
  "plan_id": 123,
  "request_id": "unique-order-reference-001"
}</pre><p><code>plan_id</code> is the bot's plan ID. <code>pid</code> is the product PID and is validated against that plan. Reuse the same request_id only to safely retrieve the same successful result.</p></div><p>Keep your API key private. Successful responses contain the delivered key.</p></body></html>""".encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html); return
        if not _api_auth_ok(self):
            return _api_json_response(self, 401, {"ok": False, "error": "Unauthorized. Send a valid X-API-Key header."})
        if path == "/api/v1/products":
            return _api_json_response(self, 200, {"ok": True, "products": _api_get_products()})
        if path.startswith("/api/v1/products/") and path.endswith("/plans"):
            pid = unquote(path[len("/api/v1/products/"):-len("/plans")].strip("/"))
            return _api_json_response(self, 200, {"ok": True, "pid": pid, "plans": _api_get_plans(pid)})
        if path == "/api/v1/balance":
            user_id, row, err = _api_wallet_details()
            if err:
                return _api_json_response(self, 503, {"ok": False, "error": err})
            return _api_json_response(self, 200, {"ok": True, "user_id": user_id, "balance": round(float(row[0] or 0), 2)})
        return _api_json_response(self, 404, {"ok": False, "error": "Endpoint not found."})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if not _api_auth_ok(self):
            return _api_json_response(self, 401, {"ok": False, "error": "Unauthorized. Send a valid X-API-Key header."})
        if path != "/api/v1/buy":
            return _api_json_response(self, 404, {"ok": False, "error": "Endpoint not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                return _api_json_response(self, 400, {"ok": False, "error": "Request body must be JSON and under 64 KB."})
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
        except Exception:
            return _api_json_response(self, 400, {"ok": False, "error": "Invalid JSON request body."})
        status, result = _api_buy(payload)
        return _api_json_response(self, status, result)


def start_public_api_server():
    global _API_SERVER, _API_SERVER_THREAD
    port = int(os.getenv("PORT", "8080"))
    _api_ensure_tables()
    _API_SERVER = ThreadingHTTPServer(("0.0.0.0", port), _PublicAPIHandler)
    _API_SERVER.daemon_threads = True
    _API_SERVER_THREAD = threading.Thread(target=_API_SERVER.serve_forever, name="public-api-server", daemon=True)
    _API_SERVER_THREAD.start()
    logging.info("Public API server listening on 0.0.0.0:%s", port)


# ============================================================
# ENTRYPOINT
# ============================================================
def main():
    # Railway web service: run the JSON API alongside the Telegram polling bot.
    start_public_api_server()
    app = Application.builder().token(os.getenv("BOT_TOKEN", BOT_TOKEN)).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    admin_pattern = (
        r"^(admin_|del_prod_|del_plan_|add_plan_prod_|key_prod_|key_plan_|"
        r"toggle_maint_|grplink_prod_|set_plan_reseller_|chg_price_|rename_prod_|"
        r"chgpid_prod_|deladmin_|"
        r"api_variant_prod_|prodvideo_prod_|custprice_prod_|custprice_plan_|remcustprice_prod_|"
        r"remcustprice_plan_|remcustprice_del_|remkeyprod_|remkeydur_|remkeydel_|"
        r"ticket_reply_|editplaninfo_|editplandur_|editplanremote_|proddesc_prod_|"
        r"apklinks_|prodoffer_)"
    )
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=admin_pattern))
    app.add_handler(CallbackQueryHandler(user_callback))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text_inputs))
    app.add_error_handler(global_error_handler)

    logging.info("Bot started, polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
