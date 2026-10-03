import os
import sys
import json
import time
import re
import sqlite3
import logging
import threading
import tempfile
from html import escape as html_escape
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types
from dotenv import load_dotenv

from eylanpanel_api import (
    EylanPanelClient,
    EylanPanelError,
)


# ══════════════════════════════════════════════════════════════════════════════
# ENV / CONFIG
# ══════════════════════════════════════════════════════════════════════════════

load_dotenv()

print(
    "[boot] starting bot process...",
    flush=True
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    print(
        "[boot] FATAL: BOT_TOKEN is missing. "
        "Set it in the platform environment variables.",
        flush=True
    )

    raise RuntimeError(
        "BOT_TOKEN is missing. Set it in .env or platform Variables."
    )


def parse_int_list(value: str):
    result = set()

    for raw in (value or "").split(","):
        raw = raw.strip()

        if raw and raw.lstrip("-").isdigit():
            result.add(int(raw))

    return result


ADMIN_IDS = parse_int_list(
    os.getenv("ADMIN_IDS", "")
)


def parse_int_list_ordered(value: str):
    """مثل parse_int_list ولی ترتیب ورودی حفظ می‌شود (set ترتیب ندارد)."""
    result = []

    for raw in (value or "").split(","):
        raw = raw.strip()

        if raw and raw.lstrip("-").isdigit():
            number = int(raw)

            if number not in result:
                result.append(number)

    return result


ADMIN_ID_LIST = parse_int_list_ordered(
    os.getenv("ADMIN_IDS", "")
)

# «اخوان» = اولین ادمینی که در ADMIN_IDS ست شده است.
# اگر لازم شد می‌شود با متغیر PROFIT_ADMIN_ID آن را صریحاً مشخص کرد.
_profit_admin_env = os.getenv("PROFIT_ADMIN_ID", "").strip()

if _profit_admin_env.lstrip("-").isdigit():
    PROFIT_ADMIN_ID = int(_profit_admin_env)
elif ADMIN_ID_LIST:
    PROFIT_ADMIN_ID = ADMIN_ID_LIST[0]
else:
    PROFIT_ADMIN_ID = None

CHANNEL_IDS = list(
    parse_int_list(
        os.getenv("CHANNEL_IDS", "")
    )
)

CHANNEL_LINKS = [
    x.strip()
    for x in os.getenv("CHANNEL_LINKS", "").split(",")
    if x.strip()
]

CHANNEL_LABELS = [
    x.strip()
    for x in os.getenv("CHANNEL_LABELS", "").split(",")
    if x.strip()
]

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "@Support"
).strip()

ABOUT_TEXT = """مجموعه NoLimit4u یک مجموعه بزرگ فروش خدمات الکترونیک است که از سال 1400 افتتاح گردیده و همواره با تلاش های روز افزون بر بهبود کیفیت محصولات و خدمات خود تلاش به عمل آورده است.🍻

این مجموعه خدمات و سرویس های متعدد شامل اشتراک اکانت های هوش مصنوعی، سرویس های کاهش پینگ، سرویس های تحریم گذر و دیگر سرویس های درحال توسعه میباشد✅

برای پیشنهاد جهت بهبود عملکرد مجموعه یا اضافه شدن خدمات جدید، پیشنهادات خود را برای ما ارسال کنید🌹"""

CARD_NUMBER = os.getenv(
    "CARD_NUMBER",
    "0000-0000-0000-0000"
).strip()

CARD_OWNER = os.getenv(
    "CARD_OWNER",
    "نام صاحب کارت"
).strip()

STORE_NAME = os.getenv(
    "STORE_NAME",
    "فروشگاه دیجیتال"
).strip()

CURRENCY_LABEL = os.getenv(
    "CURRENCY_LABEL",
    "تومان"
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/shop.sqlite3"
).strip()

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@VpnInternetMeli"
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "3000"
    ).strip()
    or "3000"
)

# ── EylanPanel API ─────────────────────────────────────────────────────────
# Secrets stay in Railway/server Variables. Never put the API key in JSON.
EYLAN_API_KEY = os.getenv("EYLAN_API_KEY", "").strip()
EYLAN_API_DOMAIN = os.getenv(
    "EYLAN_API_DOMAIN",
    "necro.gamegozardns2.shop:3500/r/necro"
).strip()
EYLAN_API_FALLBACK_DOMAIN = os.getenv(
    "EYLAN_API_FALLBACK_DOMAIN",
    "necro.gamegozardns2.shop:3500"
).strip()
EYLAN_ALLOW_HTTP = os.getenv("EYLAN_ALLOW_HTTP", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
EYLAN_SINGBOX_INBOUNDS = [
    item.strip()
    for item in os.getenv("EYLAN_SINGBOX_INBOUNDS", "").split(",")
    if item.strip()
]
EYLAN_REQUIRED_NODE_NAMES = [
    item.strip()
    for item in os.getenv(
        "EYLAN_REQUIRED_NODE_NAMES",
        "Germany-4,Germany-2,Turkey-1-A,Turkey-3"
    ).split(",")
    if item.strip()
]
try:
    EYLAN_API_TIMEOUT = float(
        os.getenv("EYLAN_API_TIMEOUT", "20").strip() or "20"
    )
except ValueError:
    EYLAN_API_TIMEOUT = 20.0

try:
    _trial_limit_raw = os.getenv("EYLAN_TRIAL_DATA_LIMIT", "").strip()
    EYLAN_TRIAL_DATA_LIMIT = (
        float(_trial_limit_raw) if _trial_limit_raw else None
    )
except ValueError:
    raise RuntimeError(
        "EYLAN_TRIAL_DATA_LIMIT must be a number (for example 10 or 0 for unlimited)."
    )


def get_eylan_client():
    return EylanPanelClient(
        api_key=EYLAN_API_KEY,
        domains=[
            EYLAN_API_DOMAIN,
            EYLAN_API_FALLBACK_DOMAIN,
        ],
        timeout=EYLAN_API_TIMEOUT,
        allow_http=EYLAN_ALLOW_HTTP,
    )


def resolve_db_path(path):
    """
    Some hosting platforms mount the container filesystem read-only.
    If DB_PATH is not writable we fall back to /tmp so the bot can still
    boot, and we print a very loud warning because data will be lost on
    every restart until a persistent volume is attached.
    """

    directory = os.path.dirname(path) or "."

    try:
        os.makedirs(
            directory,
            exist_ok=True
        )

        probe = os.path.join(
            directory,
            ".write_probe"
        )

        with open(probe, "w") as probe_file:
            probe_file.write("ok")

        os.remove(probe)

        return path

    except OSError as exc:

        fallback = "/tmp/shop.sqlite3"

        print(
            f"[boot] WARNING: '{directory}' is not writable ({exc}). "
            f"Falling back to '{fallback}'. "
            "Attach a persistent volume mounted at "
            f"'{directory}' or the database will be wiped on every restart.",
            flush=True
        )

        os.makedirs(
            "/tmp",
            exist_ok=True
        )

        return fallback


DB_PATH = resolve_db_path(
    DB_PATH
)

print(
    f"[boot] database file: {DB_PATH}",
    flush=True
)


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING / BOT
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
    force=True
)

logger = logging.getLogger("shop_bot")

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTS
# ══════════════════════════════════════════════════════════════════════════════

PRODUCTS_PATH = os.path.join(
    os.path.dirname(__file__),
    "products.json"
)

with open(
    PRODUCTS_PATH,
    "r",
    encoding="utf-8"
) as f:
    PRODUCTS = json.load(f)


PRODUCT_INDEX = {
    p["id"]: {
        **p,
        "category": category
    }
    for category, items in PRODUCTS.items()
    for p in items
}

# قیمت‌های پیش‌فرض products.json؛ قیمت‌هایی که ادمین از پنل تغییر می‌دهد
# در جدول price_overrides دیتابیس نگه‌داری و روی همین محصولات اعمال می‌شود.
DEFAULT_PRICES = {
    pid: int(p["price"])
    for pid, p in PRODUCT_INDEX.items()
}

# نرخ سود «اخوان» برای هر فروش (profit_rates.json)
PROFIT_RATES_PATH = os.path.join(
    os.path.dirname(__file__),
    "profit_rates.json"
)

AKHAVAN_PROFIT_DEFAULT_RATES = {
    "fixed": {
        "wireguard-50gb-1m": 25000,
        "wireguard-100gb-1m": 50000,
        "wireguard-200gb-1m": 100000,
    },
    "per_gb": {
        "v2ray": 750,
    },
}

try:
    with open(
        PROFIT_RATES_PATH,
        "r",
        encoding="utf-8"
    ) as f:
        AKHAVAN_PROFIT_RATES = json.load(f)
except (OSError, ValueError):
    AKHAVAN_PROFIT_RATES = AKHAVAN_PROFIT_DEFAULT_RATES

V2RAY_GB_RE = re.compile(
    r"^v2ray-(\d+(?:\.\d+)?)gb$"
)


def akhavan_profit_for_product(product_id):
    """
    سود اخوان از فروش یک محصول (تومان).
    وایرساک: مبلغ ثابت هر پلن — V2Ray: نرخ هر گیگ × حجم پلن.
    محصولاتی که نرخ ندارند (مثلاً هوش مصنوعی) سود صفر دارند.
    """

    fixed = AKHAVAN_PROFIT_RATES.get("fixed") or {}

    if product_id in fixed:
        return int(fixed[product_id])

    match = V2RAY_GB_RE.match(product_id or "")

    if match:
        per_gb = int(
            (AKHAVAN_PROFIT_RATES.get("per_gb") or {}).get("v2ray", 0)
        )

        return int(round(float(match.group(1)) * per_gb))

    return 0


EYLAN_PLANS_PATH = os.path.join(
    os.path.dirname(__file__),
    "eylan_plans.json"
)

with open(
    EYLAN_PLANS_PATH,
    "r",
    encoding="utf-8"
) as f:
    EYLAN_PLANS = json.load(f)


SERVICE_LABELS = {
    "wireguard": "🎮 وایرساک / وایرگارد",
    "v2ray": "🛜 V2Ray",
    "chatgpt": "🤖 هوش مصنوعی",
}


# سرویس‌هایی که کاربر می‌تواند آموزش «نحوه اتصال» آن‌ها را از داخل ربات ببیند.
# آموزش «هوش مصنوعی» زیر کلید chatgpt ذخیره می‌شود (همان کلیدی که قبلاً در پنل
# مدیریت وجود داشت) تا اگر ادمین قبلاً محتوایی ثبت کرده، از بین نرود.
# ادمین محتوای هر بخش را از «پنل مدیریت ← 🎓 مدیریت نحوه اتصال» تنظیم می‌کند.
USER_CONNECTION_SERVICES = {
    "wireguard": SERVICE_LABELS["wireguard"],
    "v2ray": SERVICE_LABELS["v2ray"],
    "chatgpt": SERVICE_LABELS["chatgpt"],
}


# ── اکانت تست وایرساک ────────────────────────────────────────────────────────
# اکانت تست حداکثر ۲ روز از لحظه ساخت فعال می‌ماند و از لحظه اولین اتصال
# فقط ۱ روز فرصت تست وجود دارد.
TRIAL_ACTIVE_DAYS = 2
TRIAL_USAGE_DAYS = 1

TRIAL_SERVICE_TYPE = "wireguard_trial"
TRIAL_PRODUCT_ID = "wireguard-trial"
TRIAL_PRODUCT_TITLE = "اکانت تست رایگان — وایرساک / وایرگارد"

TRIAL_IMPORTANT_NOTES = (
    "‼️ <b>نکات بسیار مهم درباره اکانت تست</b>\n"
    "⏱ از لحظه‌ای که برای اولین بار به یکی از سرورها وصل شوید، "
    "فقط <b>۱ روز</b> فرصت تست دارید.\n"
    "📅 اگر اصلاً به هیچ سروری وصل نشوید، اکانت تست حداکثر "
    "<b>۲ روز</b> فعال می‌ماند و بعد از آن منقضی می‌شود.\n"
    "🔔 تایمر ۱ روزه دقیقاً از اولین اتصال شما شروع می‌شود، نه از زمان تحویل.\n"
    "⚠️ پس لطفاً تست را به روزهای بعد موکول نکنید؛ "
    "یک هفته بعد این اکانت دیگر فعال نیست."
)


# متن راهنمای دریافت نام کاربری (هنگام خرید سرویس وایرساک / V2Ray)
SERVICE_USERNAME_PROMPT = (
    "👤 لطفاً یک نام کاربری برای همین سرویس ارسال کنید "
    "تا همراه سفارش برای ادمین ارسال شود:\n\n"
    "(اگر قبلاً پنل کاربری دریافت کرده بودید، شامل تست یا یک ماهه، "
    "نام کاربری قبلی‌تان که داخل پنل برایتان نمایش داده می‌شد را "
    "همینجا ارسال کنید تا روی همان اکانت قبلی برایتان تمدید شود)"
)


URL_RE = re.compile(
    r"^(https?://|tg://|www\.)",
    re.IGNORECASE
)


# ارقام فارسی/عربی → انگلیسی (برای مبالغی که ادمین وارد می‌کند)
DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789"
)


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA foreign_keys=ON"
    )

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def now():
    return datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    )


def money(amount):
    return f"{int(amount):,} {CURRENCY_LABEL}"


def esc(value):
    return html_escape(
        str(value)
    )


def init_db():
    with db() as conn:

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                product_title TEXT NOT NULL,
                price INTEGER NOT NULL,
                wallet_reserved INTEGER NOT NULL DEFAULT 0,
                cash_required INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                payment_amount INTEGER NOT NULL DEFAULT 0,
                receipt_file_id TEXT,
                receipt_type TEXT,
                rejection_reason TEXT,
                assigned_admin_id INTEGER,
                gmail_address TEXT,
                service_username TEXT,
                renewal_for_service INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS wireguard_trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                panel_link TEXT,
                delivered_at TEXT,
                delivered_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER,
                kind TEXT NOT NULL,
                amount INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_nav (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                parent_state TEXT,
                data_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_states (
                admin_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connection_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                content_type TEXT NOT NULL,
                file_id TEXT,
                text_content TEXT,
                caption TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS active_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                service_type TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product_title TEXT NOT NULL,
                gmail_address TEXT,
                service_username TEXT,
                started_at TEXT NOT NULL,
                expires_at TEXT,
                is_expired INTEGER NOT NULL DEFAULT 0,
                expiry_notified INTEGER NOT NULL DEFAULT 0,
                renewal_order_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS support_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_id INTEGER,
                order_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                content_type TEXT NOT NULL,
                text_content TEXT,
                file_id TEXT,
                caption TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_message_drafts (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                messages_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_overrides (
                product_id TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS akhavan_profit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                order_id INTEGER,
                user_id INTEGER,
                product_id TEXT,
                product_title TEXT,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS force_join_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                title TEXT,
                link TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                added_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_akhavan_ledger_order
            ON akhavan_profit_ledger(order_id)
            WHERE order_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_force_join_order
            ON force_join_channels(sort_order);

            CREATE INDEX IF NOT EXISTS idx_orders_user
            ON orders(user_id);

            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status);

            CREATE INDEX IF NOT EXISTS idx_users_updated
            ON users(updated_at);

            CREATE INDEX IF NOT EXISTS idx_connection_service_order
            ON connection_content(service, sort_order);

            CREATE INDEX IF NOT EXISTS idx_active_services_user
            ON active_services(user_id);

            CREATE INDEX IF NOT EXISTS idx_active_services_expires
            ON active_services(expires_at, is_expired);
            """
        )

        # ──────────────────────────────────────────────────────────────────────
        # Migrations
        # ──────────────────────────────────────────────────────────────────────

        user_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        if "is_blocked" not in user_columns:
            conn.execute(
                """
                ALTER TABLE users
                ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0
                """
            )

        order_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }

        if "wallet_reserved" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN wallet_reserved INTEGER NOT NULL DEFAULT 0
                """
            )

        if "cash_required" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN cash_required INTEGER NOT NULL DEFAULT 0
                """
            )

        if "gmail_address" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN gmail_address TEXT
                """
            )

        if "service_username" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN service_username TEXT
                """
            )

        if "renewal_for_service" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN renewal_for_service INTEGER
                """
            )

        if "api_username" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN api_username TEXT
                """
            )

        if "api_link" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN api_link TEXT
                """
            )

        if "api_status" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN api_status TEXT
                """
            )

        if "api_error" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN api_error TEXT
                """
            )

        if "delivery_message_id" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN delivery_message_id INTEGER
                """
            )

        if "delivery_mode" not in order_columns:
            conn.execute(
                """
                ALTER TABLE orders
                ADD COLUMN delivery_mode TEXT
                """
            )

        service_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(active_services)"
            ).fetchall()
        }

        if "service_username" not in service_columns:
            conn.execute(
                """
                ALTER TABLE active_services
                ADD COLUMN service_username TEXT
                """
            )

        trial_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(wireguard_trials)"
            ).fetchall()
        }

        if "status" not in trial_columns:
            conn.execute(
                """
                ALTER TABLE wireguard_trials
                ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
                """
            )

        if "panel_link" not in trial_columns:
            conn.execute(
                """
                ALTER TABLE wireguard_trials
                ADD COLUMN panel_link TEXT
                """
            )

        if "delivered_at" not in trial_columns:
            conn.execute(
                """
                ALTER TABLE wireguard_trials
                ADD COLUMN delivered_at TEXT
                """
            )

        if "delivered_by" not in trial_columns:
            conn.execute(
                """
                ALTER TABLE wireguard_trials
                ADD COLUMN delivered_by INTEGER
                """
            )

        # Legacy database migration
        try:
            conn.execute(
                """
                UPDATE orders
                SET cash_required=deposit_requested
                WHERE cash_required=0
                AND deposit_requested>0
                """
            )
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute(
                """
                UPDATE orders
                SET status='receipt_submitted'
                WHERE status='awaiting_receipt'
                AND receipt_file_id IS NOT NULL
                """
            )
        except sqlite3.OperationalError:
            pass

        try:
            legacy = conn.execute(
                "SELECT * FROM admin_sessions"
            ).fetchall()
        except sqlite3.OperationalError:
            legacy = []

        for row in legacy:
            try:
                conn.execute(
                    """
                    INSERT INTO admin_states(
                        admin_id,
                        mode,
                        data_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(admin_id) DO NOTHING
                    """,
                    (
                        row["admin_id"],
                        row["mode"],
                        json.dumps(
                            {
                                "order_id": row["order_id"]
                            },
                            ensure_ascii=False
                        ),
                        row["updated_at"]
                    )
                )
            except Exception:
                logger.exception(
                    "Failed migrating legacy admin session"
                )

        # ──────────────────────────────────────────────────────────────────
        # Seed force-join channels from legacy ENV vars (one-time, only if
        # the table is still empty) so upgrades keep working without any
        # manual admin action right after deploy.
        # ──────────────────────────────────────────────────────────────────

        try:
            existing_count = conn.execute(
                "SELECT COUNT(*) AS c FROM force_join_channels"
            ).fetchone()["c"]
        except Exception:
            existing_count = 0

        if existing_count == 0 and CHANNEL_IDS:
            for i, channel_id in enumerate(CHANNEL_IDS):
                link = (
                    CHANNEL_LINKS[i]
                    if i < len(CHANNEL_LINKS)
                    else None
                )
                label = (
                    CHANNEL_LABELS[i]
                    if i < len(CHANNEL_LABELS)
                    else f"کانال {i + 1}"
                )

                try:
                    conn.execute(
                        """
                        INSERT INTO force_join_channels(
                            chat_id,
                            title,
                            link,
                            sort_order,
                            added_by,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(channel_id),
                            label,
                            link,
                            i,
                            None,
                            now()
                        )
                    )
                except Exception:
                    logger.exception(
                        "Failed seeding force-join channel from ENV: %s",
                        channel_id
                    )

        try:
            has_setting = conn.execute(
                "SELECT 1 FROM bot_settings WHERE key='force_join_enabled'"
            ).fetchone()
        except Exception:
            has_setting = None

        if not has_setting:
            conn.execute(
                """
                INSERT INTO bot_settings(key, value, updated_at)
                VALUES ('force_join_enabled', '1', ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (now(),)
            )

        try:
            has_maintenance = conn.execute(
                "SELECT 1 FROM bot_settings WHERE key='maintenance_mode'"
            ).fetchone()
        except Exception:
            has_maintenance = None

        if not has_maintenance:
            conn.execute(
                """
                INSERT INTO bot_settings(key, value, updated_at)
                VALUES ('maintenance_mode', '0', ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (now(),)
            )


# ══════════════════════════════════════════════════════════════════════════════
# USERS / NAVIGATION
# ══════════════════════════════════════════════════════════════════════════════

def ensure_user(user):
    t = now()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                first_name,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                updated_at=excluded.updated_at
            """,
            (
                user.id,
                user.username,
                user.first_name or "",
                t,
                t
            )
        )


def set_blocked(
    user_id,
    blocked=True
):
    with db() as conn:
        conn.execute(
            """
            UPDATE users
            SET is_blocked=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                1 if blocked else 0,
                now(),
                user_id
            )
        )


def get_balance(user_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

    return int(row["balance"]) if row else 0


def get_user_row(user_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()


def get_reserved_total(user_id):
    """
    مجموع موجودی‌ای که روی سفارش‌های باز کاربر رزرو شده است.

    این مبلغ همان لحظه ثبت سفارش از balance کسر می‌شود، پس جزو موجودی
    قابل استفاده نیست؛ ولی اگر سفارش لغو یا رد شود دوباره برمی‌گردد.
    """

    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(wallet_reserved), 0) AS total
            FROM orders
            WHERE user_id=?
            AND status IN (
                'awaiting_payment',
                'awaiting_receipt',
                'receipt_submitted'
            )
            """,
            (user_id,)
        ).fetchone()

    return int(row["total"]) if row else 0


def parse_amount(text):
    """
    مبلغ واردشده توسط ادمین را به عدد صحیح تبدیل می‌کند.

    ارقام فارسی/عربی، جداکننده هزارگان و کلمه «تومان» پشتیبانی می‌شود.
    خروجی None یعنی ورودی نامعتبر است.
    """

    if not isinstance(text, str):
        return None

    cleaned = text.translate(
        DIGIT_TRANSLATION
    ).strip()

    for token in (
        "تومان",
        "تومن",
        "ریال",
        ",",
        "،",
        "٬",
        "_",
        " "
    ):
        cleaned = cleaned.replace(
            token,
            ""
        )

    cleaned = cleaned.strip()

    if not cleaned:
        return None

    negative = False

    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]

    elif cleaned.startswith("+"):
        cleaned = cleaned[1:]

    if not cleaned.isdigit():
        return None

    try:
        value = int(cleaned)
    except ValueError:
        return None

    return -value if negative else value


def apply_balance_change(
    user_id,
    operation,
    amount,
    admin_id=None,
    note=None
):
    """
    تغییر موجودی کیف پول کاربر توسط ادمین.

    operation یکی از 'add' / 'sub' / 'set' / 'zero' است.
    خروجی: (ok, old_balance, new_balance) — ok=False یعنی کاربر پیدا نشد.
    موجودی هرگز منفی نمی‌شود.
    """

    t = now()

    with db() as conn:

        row = conn.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

        if not row:
            return False, 0, 0

        old_balance = int(
            row["balance"]
        )

        amount = abs(
            int(amount or 0)
        )

        if operation == "add":
            new_balance = old_balance + amount

        elif operation == "sub":
            new_balance = old_balance - amount

        elif operation == "set":
            new_balance = amount

        else:
            new_balance = 0

        if new_balance < 0:
            new_balance = 0

        conn.execute(
            """
            UPDATE users
            SET balance=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                new_balance,
                t,
                user_id
            )
        )

        delta = new_balance - old_balance

        kind_map = {
            "add": "admin_credit",
            "sub": "admin_debit",
            "set": "admin_set",
            "zero": "admin_zero"
        }

        conn.execute(
            """
            INSERT INTO transactions(
                user_id,
                order_id,
                kind,
                amount,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                None,
                kind_map.get(
                    operation,
                    "admin_adjust"
                ),
                delta,
                note or (
                    f"تغییر موجودی توسط ادمین {admin_id}"
                    if admin_id
                    else "تغییر موجودی توسط ادمین"
                ),
                t
            )
        )

    return True, old_balance, new_balance


def recent_transactions(
    user_id,
    limit=10
):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM transactions
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                user_id,
                limit
            )
        ).fetchall()


# ══════════════════════════════════════════════════════════════════════════════
# WIREGUARD TRIAL
# ══════════════════════════════════════════════════════════════════════════════

def has_wireguard_trial(user_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM wireguard_trials
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

    return row is not None


def create_wireguard_trial(
    user_id,
    username
):
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO wireguard_trials(
                    user_id,
                    username,
                    created_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    now()
                )
            )
        except sqlite3.IntegrityError:
            return False

        return True


def get_wireguard_trial(user_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM wireguard_trials
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()


def mark_wireguard_trial_delivered(
    user_id,
    admin_id,
    panel_link
):
    with db() as conn:
        conn.execute(
            """
            UPDATE wireguard_trials
            SET status='delivered',
                panel_link=?,
                delivered_at=?,
                delivered_by=?
            WHERE user_id=?
            """,
            (
                panel_link,
                now(),
                admin_id,
                user_id
            )
        )


def save_nav(
    user_id,
    state,
    parent=None,
    data=None
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO user_nav(
                user_id,
                state,
                parent_state,
                data_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                state=excluded.state,
                parent_state=excluded.parent_state,
                data_json=excluded.data_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                state,
                parent,
                json.dumps(
                    data or {},
                    ensure_ascii=False
                ),
                now()
            )
        )


def get_nav(user_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM user_nav
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

    if not row:
        return {
            "state": "home",
            "parent_state": None,
            "data": {}
        }

    try:
        data = json.loads(
            row["data_json"] or "{}"
        )
    except Exception:
        data = {}

    return {
        "state": row["state"],
        "parent_state": row["parent_state"],
        "data": data
    }


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN STATE
# ══════════════════════════════════════════════════════════════════════════════

def set_admin_state(
    admin_id,
    mode,
    data=None
):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO admin_states(
                admin_id,
                mode,
                data_json,
                updated_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(admin_id) DO UPDATE SET
                mode=excluded.mode,
                data_json=excluded.data_json,
                updated_at=excluded.updated_at
            """,
            (
                admin_id,
                mode,
                json.dumps(
                    data or {},
                    ensure_ascii=False
                ),
                now()
            )
        )


def get_admin_state(admin_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM admin_states
            WHERE admin_id=?
            """,
            (admin_id,)
        ).fetchone()

    if not row:
        return None

    try:
        data = json.loads(
            row["data_json"] or "{}"
        )
    except Exception:
        data = {}

    return {
        "mode": row["mode"],
        "data": data
    }


def update_admin_state(
    admin_id,
    data
):
    with db() as conn:
        conn.execute(
            """
            UPDATE admin_states
            SET data_json=?,
                updated_at=?
            WHERE admin_id=?
            """,
            (
                json.dumps(
                    data,
                    ensure_ascii=False
                ),
                now(),
                admin_id
            )
        )


def clear_admin_state(admin_id):
    with db() as conn:
        conn.execute(
            """
            DELETE FROM admin_states
            WHERE admin_id=?
            """,
            (admin_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE SERVICES
# ══════════════════════════════════════════════════════════════════════════════

def create_active_service(
    user_id,
    order_id,
    service_type,
    product_id,
    product_title,
    gmail_address=None,
    has_expiry=True,
    expiry_days=30,
    service_username=None
):
    started = now()

    if has_expiry:
        expires = (
            datetime.now(timezone.utc)
            + timedelta(days=expiry_days)
        ).isoformat(
            timespec="seconds"
        )
    else:
        expires = None

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO active_services(
                user_id,
                order_id,
                service_type,
                product_id,
                product_title,
                gmail_address,
                service_username,
                started_at,
                expires_at,
                is_expired,
                expiry_notified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                user_id,
                order_id,
                service_type,
                product_id,
                product_title,
                gmail_address,
                service_username,
                started,
                expires
            )
        )

        return cur.lastrowid


def get_active_services(user_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM active_services
            WHERE user_id=?
            ORDER BY started_at DESC
            """,
            (user_id,)
        ).fetchall()


def get_active_service_by_id(service_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM active_services
            WHERE id=?
            """,
            (service_id,)
        ).fetchone()


def update_service_gmail(
    service_id,
    gmail_address
):
    with db() as conn:
        conn.execute(
            """
            UPDATE active_services
            SET gmail_address=?
            WHERE id=?
            """,
            (
                gmail_address,
                service_id
            )
        )


def expire_service(service_id):
    with db() as conn:
        conn.execute(
            """
            UPDATE active_services
            SET is_expired=1
            WHERE id=?
            """,
            (service_id,)
        )


def renew_service(
    service_id,
    new_order_id
):
    new_expires = (
        datetime.now(timezone.utc)
        + timedelta(days=30)
    ).isoformat(
        timespec="seconds"
    )

    with db() as conn:
        conn.execute(
            """
            UPDATE active_services
            SET expires_at=?,
                is_expired=0,
                expiry_notified=0,
                renewal_order_id=?
            WHERE id=?
            """,
            (
                new_expires,
                new_order_id,
                service_id
            )
        )


def get_services_expiring_soon():
    threshold = (
        datetime.now(timezone.utc)
        + timedelta(days=3)
    ).isoformat(
        timespec="seconds"
    )

    current = now()

    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM active_services
            WHERE expires_at IS NOT NULL
            AND expires_at <= ?
            AND expires_at >= ?
            AND is_expired=0
            AND expiry_notified=0
            AND service_type <> ?
            """,
            (
                threshold,
                current,
                TRIAL_SERVICE_TYPE
            )
        ).fetchall()


def get_expired_services():
    current = now()

    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM active_services
            WHERE expires_at IS NOT NULL
            AND expires_at < ?
            AND is_expired=0
            """,
            (current,)
        ).fetchall()


def mark_expiry_notified(service_id):
    with db() as conn:
        conn.execute(
            """
            UPDATE active_services
            SET expiry_notified=1
            WHERE id=?
            """,
            (service_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# SUPPORT
# ══════════════════════════════════════════════════════════════════════════════

def create_support_chat(
    user_id,
    service_id=None,
    order_id=None
):
    t = now()

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO support_chats(
                user_id,
                service_id,
                order_id,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'open', ?, ?)
            """,
            (
                user_id,
                service_id,
                order_id,
                t,
                t
            )
        )

        return cur.lastrowid


def get_open_support_chat(user_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM support_chats
            WHERE user_id=?
            AND status='open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,)
        ).fetchone()


def get_support_chat_by_id(chat_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM support_chats
            WHERE id=?
            """,
            (chat_id,)
        ).fetchone()


def close_support_chat(chat_id):
    with db() as conn:
        conn.execute(
            """
            UPDATE support_chats
            SET status='closed',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                chat_id
            )
        )


def save_draft_messages(
    user_id,
    chat_id,
    messages
):
    t = now()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO user_message_drafts(
                user_id,
                chat_id,
                messages_json,
                updated_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                messages_json=excluded.messages_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                chat_id,
                json.dumps(
                    messages,
                    ensure_ascii=False
                ),
                t
            )
        )


def get_draft_messages(user_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM user_message_drafts
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

    if not row:
        return None, []

    try:
        msgs = json.loads(
            row["messages_json"] or "[]"
        )
    except Exception:
        msgs = []

    return (
        row["chat_id"],
        msgs
    )


def clear_draft_messages(user_id):
    with db() as conn:
        conn.execute(
            """
            DELETE FROM user_message_drafts
            WHERE user_id=?
            """,
            (user_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def reply_main_keyboard():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        is_persistent=False
    )

    kb.row(
        "🖥 خرید سرور",
        "🤖 اشتراک هوش مصنوعی"
    )

    kb.row(
        "🔗 نحوه اتصال",
        "🎧 پشتیبانی"
    )

    kb.row(
        "ℹ️ درباره ما",
        "💰 موجودی"
    )

    kb.row(
        "📦 سرویس های من",
        "📊 وضعیت سرور"
    )

    return kb


def reply_back_keyboard():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        is_persistent=False
    )

    kb.row(
        "⬅️ بازگشت",
        "🏠 منوی اصلی"
    )

    kb.row(
        "💰 موجودی"
    )

    return kb


def remove_keyboard():
    return types.ReplyKeyboardRemove()


def main_menu_inline():
    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.add(
        types.InlineKeyboardButton(
            "🖥 خرید سرور",
            callback_data="cat:server"
        ),
        types.InlineKeyboardButton(
            "🤖 اشتراک هوش مصنوعی",
            callback_data="cat:ai"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🔗 نحوه اتصال",
            callback_data="connection"
        ),
        types.InlineKeyboardButton(
            "🎧 پشتیبانی",
            callback_data="support"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "ℹ️ درباره ما",
            callback_data="about"
        )
    )

    return kb


# ══════════════════════════════════════════════════════════════════════════════
# BOT SETTINGS (key/value, persisted in DB)
# ══════════════════════════════════════════════════════════════════════════════

def get_setting(key, default=None):
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM bot_settings WHERE key=?",
            (key,)
        ).fetchone()

    if not row:
        return default

    return row["value"]


def set_setting(key, value):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, str(value), now())
        )


def is_force_join_enabled():
    return get_setting("force_join_enabled", "1") == "1"


def set_force_join_enabled(flag):
    set_setting("force_join_enabled", "1" if flag else "0")


def is_maintenance_mode():
    return get_setting("maintenance_mode", "0") == "1"


def set_maintenance_mode(flag):
    set_setting("maintenance_mode", "1" if flag else "0")


# ══════════════════════════════════════════════════════════════════════════════
# FORCE JOIN CHANNELS (📢 عضویت اجباری — managed from the admin panel)
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_chat_id(raw):
    raw = str(raw).strip()

    if raw.lstrip("-").isdigit():
        return int(raw)

    return raw


def get_force_join_channels():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM force_join_channels
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def add_force_join_channel(chat_id, title, link, added_by=None):
    with db() as conn:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM force_join_channels"
        ).fetchone()["m"]

        conn.execute(
            """
            INSERT INTO force_join_channels(
                chat_id,
                title,
                link,
                sort_order,
                added_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(chat_id),
                title,
                link,
                max_order + 1,
                added_by,
                now()
            )
        )


def remove_force_join_channel(channel_row_id):
    with db() as conn:
        conn.execute(
            "DELETE FROM force_join_channels WHERE id=?",
            (channel_row_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC ADMINS (🧑‍💼 مدیریت ادمین‌ها — اضافه/حذف ادمین بدون ریستارت ربات)
# ══════════════════════════════════════════════════════════════════════════════

ENV_ADMIN_ID_ORDER = tuple(ADMIN_ID_LIST)
ENV_ADMIN_IDS = frozenset(ENV_ADMIN_ID_ORDER)


def get_extra_admin_ids():
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT user_id FROM bot_admins ORDER BY created_at ASC"
            ).fetchall()
    except Exception:
        return []

    return [row["user_id"] for row in rows]


def reload_admin_ids():
    """ADMIN_IDS/ADMIN_ID_LIST را با ادمین‌های اضافه‌شده از پنل هماهنگ می‌کند.

    چون ADMIN_IDS همان‌جا (in-place) آپدیت می‌شود، همه‌ی توابعی که از قبل
    به آن ارجاع دارند بدون نیاز به ریستارت ربات مقدار تازه را می‌بینند.
    """

    extra = get_extra_admin_ids()

    ordered = []

    for admin_id in ENV_ADMIN_ID_ORDER:
        if admin_id not in ordered:
            ordered.append(admin_id)

    for admin_id in extra:
        if admin_id not in ordered:
            ordered.append(admin_id)

    ADMIN_ID_LIST[:] = ordered

    ADMIN_IDS.clear()
    ADMIN_IDS.update(ordered)


def add_extra_admin(user_id, added_by=None):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO bot_admins(user_id, added_by, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, added_by, now())
        )

    reload_admin_ids()


def remove_extra_admin(user_id):
    with db() as conn:
        conn.execute(
            "DELETE FROM bot_admins WHERE user_id=?",
            (user_id,)
        )

    reload_admin_ids()


def join_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for i, channel in enumerate(get_force_join_channels()):
        label = channel.get("title") or f"عضویت در کانال {i + 1}"
        link = channel.get("link")

        if not link:
            raw_chat_id = channel.get("chat_id") or ""

            if str(raw_chat_id).startswith("@"):
                link = f"https://t.me/{str(raw_chat_id)[1:]}"

        if not link:
            # بدون لینک معتبر نمی‌شود دکمه URL ساخت؛ از این کانال صرف‌نظر می‌شود.
            continue

        kb.add(
            types.InlineKeyboardButton(
                f"📢 {label}",
                url=link
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "✅ بررسی عضویت",
            callback_data="check_join"
        )
    )

    return kb


# ══════════════════════════════════════════════════════════════════════════════
# MEMBERSHIP
# ══════════════════════════════════════════════════════════════════════════════

def is_member(user_id):
    if user_id in ADMIN_IDS:
        return True

    if not is_force_join_enabled():
        return True

    channels = get_force_join_channels()

    if not channels:
        return True

    for channel in channels:
        channel_id = _normalize_chat_id(
            channel["chat_id"]
        )

        try:
            member = bot.get_chat_member(
                channel_id,
                user_id
            )

            if member.status in (
                "creator",
                "administrator",
                "member"
            ):
                continue

            if (
                member.status == "restricted"
                and getattr(
                    member,
                    "is_member",
                    False
                )
            ):
                continue

            return False

        except Exception as exc:
            logger.warning(
                "Membership check failed for %s in %s: %s",
                user_id,
                channel_id,
                exc
            )

            return False

    return True


def ensure_joined(message):
    ensure_user(
        message.from_user
    )

    if (
        is_maintenance_mode()
        and message.from_user.id not in ADMIN_IDS
    ):
        bot.send_message(
            message.chat.id,
            "🛠 ربات موقتاً در حال به‌روزرسانی است. لطفاً چند دقیقه دیگر دوباره تلاش کنید."
        )

        return False

    if is_member(
        message.from_user.id
    ):
        return True

    bot.send_message(
        message.chat.id,
        "برای استفاده از ربات ابتدا در کانال‌های زیر عضو شوید و سپس روی «بررسی عضویت» بزنید.",
        reply_markup=join_keyboard()
    )

    return False


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def edit_or_send(
    call,
    text,
    reply_markup=None,
    parse_mode="HTML",
    bottom_keyboard=None
):
    """
    Edit callback message or send a new one.

    IMPORTANT:
    دیگر هیچ پیام اضافه‌ای با متن
    «⬇️ برای ناوبری از کلیدهای زیر استفاده کنید:»
    ارسال نمی‌شود.

    bottom_keyboard فقط برای سازگاری با کد قبلی نگه داشته شده
    و عمداً استفاده نمی‌شود.
    """

    chat_id = call.message.chat.id
    edited = False

    try:
        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

        edited = True

    except Exception:
        pass

    if not edited:
        bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )


def send_home(
    chat_id,
    user_id=None,
    greeting=True
):
    if user_id is None:
        user_id = chat_id

    bal = get_balance(user_id)

    save_nav(
        user_id,
        "home",
        None,
        {}
    )

    text = (
        (
            f"سلام 👋\n"
            f"به <b>{esc(STORE_NAME)}</b> خوش آمدید.\n\n"
            if greeting
            else ""
        )
        + f"💰 موجودی شما: <b>{money(bal)}</b>\n\n"
        "یکی از گزینه‌ها را انتخاب کنید:"
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=reply_main_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVER / AI MENUS
# ══════════════════════════════════════════════════════════════════════════════

def show_server_menu(
    chat_id,
    user_id,
    new_message=True
):
    save_nav(
        user_id,
        "server",
        "home",
        {}
    )

    text = (
        "🖥 <b>خرید سرور</b>\n\n"
        "نوع سرویس موردنظر را انتخاب کنید:"
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=inline_server_menu()
    )


def inline_server_menu():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🎮 تحریم‌گذر گیمینگ وایرساک",
            callback_data="srv:wireguard"
        ),
        types.InlineKeyboardButton(
            "🛜 سرویس V2Ray مولتی‌لوکیشن",
            callback_data="srv:v2ray"
        )
    )

    return kb


def show_ai_menu(
    chat_id,
    user_id
):
    save_nav(
        user_id,
        "ai",
        "home",
        {}
    )

    text = (
        "🤖 <b>اشتراک هوش مصنوعی</b>\n\n"
        "سرویس موردنظر را انتخاب کنید:"
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=inline_ai_menu()
    )


def inline_ai_menu():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🤖 هوش مصنوعی ChatGPT",
            callback_data="ai:chatgpt"
        )
    )

    return kb


def wireguard_items():
    return [
        p
        for p in PRODUCT_INDEX.values()
        if p["id"].startswith("wireguard-")
    ]


def v2ray_items():
    return [
        p
        for p in PRODUCT_INDEX.values()
        if p["id"].startswith("v2ray-")
    ]


def show_service_products(
    call,
    service
):
    if service == "wireguard":

        items = wireguard_items()

        title = (
            "🎮 <b>تحریم‌گذر گیمینگ وایرساک</b>\n\n"
            "⏱ اعتبار: <b>یک ماهه</b>\n"
            "ℹ️ نوع: <b>سرویس وایرساک / وایرگارد</b>\n\n"
            "برای خرید، حجم موردنظر را انتخاب کنید."
        )

        state = "wireguard"
        parent = "server"

        info_cb = "service_info:wireguard"

    else:

        items = v2ray_items()

        title = (
            "🛜 <b>سرویس V2Ray مولتی‌لوکیشن</b>\n\n"
            "⏱ اعتبار: <b>نامحدود زمان ♾️</b>\n\n"
            "برای خرید، حجم موردنظر را انتخاب کنید."
        )

        state = "v2ray"
        parent = "server"

        info_cb = "service_info:v2ray"

    save_nav(
        call.from_user.id,
        state,
        parent,
        {}
    )

    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.add(
        types.InlineKeyboardButton(
            "ℹ️ اطلاعات سرویس",
            callback_data=info_cb
        )
    )

    for p in items:
        kb.row(
            types.InlineKeyboardButton(
                f"💰 {money(p['price'])}",
                callback_data=f"buy:{p['id']}"
            ),
            types.InlineKeyboardButton(
                f"📦 {p['volume']}",
                callback_data=f"info:{p['id']}"
            )
        )

    if service == "wireguard":
        kb.row(
            types.InlineKeyboardButton(
                "🎁 رایگان",
                callback_data="wg_trial_start"
            ),
            types.InlineKeyboardButton(
                "🧪 تست",
                callback_data="wg_trial_start"
            )
        )

    edit_or_send(
        call,
        title,
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


def show_chatgpt(call):
    save_nav(
        call.from_user.id,
        "chatgpt",
        "ai",
        {}
    )

    text = (
        "🤖 <b>هوش مصنوعی ChatGPT</b>\n\n"
        "نوع اشتراک موردنظر را انتخاب کنید:\n\n"
        "⭐ <b>اکانت Plus</b> — روی جیمیل شخصی\n"
        "👥 <b>اکانت اشتراکی یک ماهه</b> — جیمیل‌های آماده"
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "⭐ اکانت Plus — روی جیمیل شخصی",
            callback_data="ai:personal"
        ),
        types.InlineKeyboardButton(
            "👥 اکانت اشتراکی یک ماهه",
            callback_data="ai:shared"
        )
    )

    edit_or_send(
        call,
        text,
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


def show_shared_ai(call):
    save_nav(
        call.from_user.id,
        "ai_shared",
        "chatgpt",
        {}
    )

    items = [
        p
        for p in PRODUCTS.get("ai", [])
        if p["id"].startswith("chatgpt-shared-")
    ]

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for p in items:
        kb.add(
            types.InlineKeyboardButton(
                f"🛒 {p['volume']} — {money(p['price'])}",
                callback_data=f"buy:{p['id']}"
            )
        )

    edit_or_send(
        call,
        "👥 <b>ChatGPT Plus — اکانت اشتراکی یک ماهه</b>\n\n"
        "نوع اکانت را انتخاب کنید:",
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


def show_personal_ai(call):
    product = PRODUCT_INDEX.get(
        "chatgpt-plus-personal"
    )

    if not product:
        bot.answer_callback_query(
            call.id,
            "محصول پیدا نشد.",
            show_alert=True
        )
        return

    save_nav(
        call.from_user.id,
        "ai_personal",
        "chatgpt",
        {
            "product_id": product["id"]
        }
    )

    text = (
        "⭐ <b>ChatGPT Plus روی جیمیل شخصی</b>\n\n"
        f"{esc(product.get('info', ''))}\n\n"
        f"💰 قیمت: <b>{money(product['price'])}</b>\n\n"
        f"{esc(product.get('delivery_note', ''))}\n\n"
        "🔹 برای شروع فرآیند خرید، روی دکمه زیر بزنید."
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🛒 شروع خرید",
            callback_data=f"personal_ai_start:{product['id']}"
        )
    )

    edit_or_send(
        call,
        text,
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ORDERS / INVOICES
# ══════════════════════════════════════════════════════════════════════════════

def product_back_state(product_id):
    if product_id.startswith("wireguard-"):
        return "wireguard"

    if product_id.startswith("v2ray-"):
        return "v2ray"

    if product_id.startswith("chatgpt-shared-"):
        return "ai_shared"

    if product_id == "chatgpt-plus-personal":
        return "ai_personal"

    return "home"


def invoice_text(
    order_id,
    product=None,
    extra_balance_info=None
):
    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

    if not order:
        return "سفارش پیدا نشد."

    product = product or PRODUCT_INDEX.get(
        order["product_id"]
    ) or {}

    current_balance = get_balance(
        order["user_id"]
    )

    reserved = int(
        order["wallet_reserved"]
    )

    cash_required = int(
        order["cash_required"]
    )

    price = int(
        order["price"]
    )

    plan_title = (
        product.get("plan_title")
        or order["product_title"]
    )

    volume = product.get(
        "volume",
        "-"
    )

    gmail_line = ""

    if order["gmail_address"]:
        gmail_line = (
            f"📧 جیمیل: "
            f"<code>{esc(order['gmail_address'])}</code>\n"
        )

    # ── بخش کیف پول ───────────────────────────────────────────────────────────
    if reserved > 0 and cash_required > 0:

        wallet_block = (
            f"👛 موجودی کیف پول شما: <b>{money(reserved)}</b>\n"
            f"💵 مبلغ قابل پرداخت: <b>{money(cash_required)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"✅ با توجه به موجودی کیف پول شما، به‌جای "
            f"<b>{money(price)}</b> فقط "
            f"<b>{money(cash_required)}</b> واریز کنید.\n"
            "پس از تأیید سفارش، موجودی استفاده‌شده از کیف پول شما کسر می‌شود.\n"
        )

        footer = (
            "برای ادامه خرید، دکمه «پرداخت و ادامه» را زیر همین پیام بزنید."
        )

    elif cash_required <= 0:

        wallet_block = (
            f"👛 موجودی کیف پول شما: <b>{money(reserved)}</b>\n"
            f"💵 مبلغ قابل پرداخت: <b>{money(0)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🎉 موجودی کیف پول شما برای این خرید کافی است.\n"
            "📤 نیازی به واریز وجه و ارسال فیش نیست.\n"
        )

        footer = (
            "برای نهایی کردن خرید، دکمه «پرداخت و ادامه» را زیر همین پیام بزنید."
        )

    else:

        wallet_block = (
            f"👛 موجودی کیف پول شما: <b>{money(current_balance)}</b>\n"
            f"💵 مبلغ قابل پرداخت: <b>{money(cash_required)}</b>\n"
        )

        footer = (
            "برای ادامه خرید، دکمه «پرداخت و ادامه» را زیر همین پیام بزنید."
        )

    return (
        "🧾 <b>پیش‌فاکتور شما</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧾 شماره سفارش: <b>#{order_id}</b>\n"
        f"🛍 نوع سرویس: <b>{esc(plan_title)}</b>\n"
        f"📦 حجم / نوع: <b>{esc(volume)}</b>\n"
        f"{gmail_line}"
        f"💰 قیمت نهایی: <b>{money(price)}</b>\n"
        f"{wallet_block}"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{footer}"
    )


def invoice_keyboard(order_id):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🎁 کد تخفیف",
            callback_data=f"coupon:{order_id}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "💳 پرداخت و ادامه",
            callback_data=f"pay:{order_id}"
        )
    )

    return kb


def create_order(
    user_id,
    product,
    gmail_address=None
):
    with db() as conn:

        user = conn.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

        if not user:
            raise RuntimeError(
                "User does not exist"
            )

        balance = int(
            user["balance"]
        )

        reserved = min(
            balance,
            int(product["price"])
        )

        cash_required = (
            int(product["price"])
            - reserved
        )

        t = now()

        cur = conn.execute(
            """
            INSERT INTO orders(
                user_id,
                product_id,
                product_title,
                price,
                wallet_reserved,
                cash_required,
                status,
                gmail_address,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                product["id"],
                product["title"],
                product["price"],
                reserved,
                cash_required,
                "awaiting_payment",
                gmail_address,
                t,
                t
            )
        )

        order_id = cur.lastrowid

        if reserved:
            conn.execute(
                """
                UPDATE users
                SET balance=balance-?,
                    updated_at=?
                WHERE user_id=?
                """,
                (
                    reserved,
                    t,
                    user_id
                )
            )

            conn.execute(
                """
                INSERT INTO transactions(
                    user_id,
                    order_id,
                    kind,
                    amount,
                    note,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    order_id,
                    "wallet_reserve",
                    -reserved,
                    "رزرو موجودی برای سفارش",
                    t
                )
            )

        return order_id


def cancel_unpaid_order(
    order_id,
    user_id
):
    with db() as conn:

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND user_id=?
            """,
            (
                order_id,
                user_id
            )
        ).fetchone()

        if not order:
            return False

        if order["status"] not in (
            "awaiting_payment",
            "awaiting_receipt"
        ):
            return False

        reserved = int(
            order["wallet_reserved"]
        )

        if reserved:

            conn.execute(
                """
                UPDATE users
                SET balance=balance+?,
                    updated_at=?
                WHERE user_id=?
                """,
                (
                    reserved,
                    now(),
                    user_id
                )
            )

            conn.execute(
                """
                INSERT INTO transactions(
                    user_id,
                    order_id,
                    kind,
                    amount,
                    note,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    order_id,
                    "wallet_release",
                    reserved,
                    "آزادسازی موجودی با لغو سفارش",
                    now()
                )
            )

        conn.execute(
            """
            UPDATE orders
            SET status='cancelled',
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                order_id
            )
        )

        return True


def payment_text(
    order_id,
    product,
    required,
    price=None,
    wallet_used=0
):
    title = (
        (product or {}).get("title")
        or "سرویس انتخابی شما"
    )

    wallet_block = ""

    if wallet_used and price and price > required:

        wallet_block = (
            f"👛 از موجودی کیف پول شما: <b>{money(wallet_used)}</b>\n"
            f"💰 قیمت کامل سرویس: <b>{money(price)}</b>\n"
            f"✅ به‌جای <b>{money(price)}</b> فقط "
            f"<b>{money(required)}</b> واریز کنید.\n\n"
        )

    return (
        f"💳 <b>پرداخت سفارش #{order_id}</b>\n\n"
        f"🛍 محصول: <b>{esc(title)}</b>\n"
        f"{wallet_block}"
        f"💵 مبلغ قابل پرداخت: <b>{money(required)}</b>\n\n"
        "مبلغ بالا را به شماره کارت زیر واریز کنید و سپس تصویر فیش را در همین گفتگو ارسال نمایید.\n\n"
        f"💳 شماره کارت: <code>{esc(CARD_NUMBER)}</code>\n"
        f"👤 به نام: <b>{esc(CARD_OWNER)}</b>\n\n"
        "⚠️ لطفاً مبلغ دقیق سفارش را واریز کنید.\n"
        "⚠️ رمز پویا یا اطلاعات بانکی حساس را ارسال نکنید."
    )


def finalize_wallet_only_order(
    order_id,
    chat_id=None
):
    """
    نهایی کردن سفارشی که کل مبلغ آن از کیف پول تأمین شده است.

    در این حالت نیازی به ارسال فیش نیست؛ سفارش مستقیم به صف تحویل ادمین
    می‌رود و به ادمین اعلام می‌شود که مبلغ از کیف پول کاربر کسر شده است.
    """

    with db() as conn:

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

        if not order:
            return False

        cur = conn.execute(
            """
            UPDATE orders
            SET status='paid_pending_fulfillment',
                payment_amount=?,
                updated_at=?
            WHERE id=?
            AND status IN (
                'awaiting_payment',
                'awaiting_receipt'
            )
            """,
            (
                int(order["price"]),
                now(),
                order_id
            )
        )

        if cur.rowcount != 1:
            return False

        conn.execute(
            """
            INSERT INTO transactions(
                user_id,
                order_id,
                kind,
                amount,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order["user_id"],
                order_id,
                "wallet_payment",
                0,
                "پرداخت کامل سفارش از کیف پول (بدون فیش)",
                now()
            )
        )

    wallet_used = int(
        order["wallet_reserved"]
    )

    target_chat = (
        chat_id
        if chat_id is not None
        else order["user_id"]
    )

    try:

        bot.send_message(
            target_chat,
            f"✅ <b>خرید سفارش #{order_id} با موفقیت انجام شد.</b>\n\n"
            f"🛍 محصول: <b>{esc(order['product_title'])}</b>\n"
            f"💰 قیمت: <b>{money(order['price'])}</b>\n"
            f"👛 پرداخت‌شده از کیف پول: <b>{money(wallet_used)}</b>\n"
            f"💳 موجودی فعلی کیف پول شما: "
            f"<b>{money(get_balance(order['user_id']))}</b>\n\n"
            "📤 چون موجودی کیف پول شما کافی بود، نیازی به ارسال فیش نبود.\n"
            "⏳ سفارش شما در صف تحویل قرار گرفت و به‌زودی ارسال می‌شود.",
            reply_markup=reply_main_keyboard()
        )

    except Exception:

        logger.exception(
            "Failed notifying user about wallet-only order #%s",
            order_id
        )

    notify_fulfillment_admins(
        order_id
    )

    return True


def payment_keyboard(order_id):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "📤 ارسال فیش",
            callback_data=f"await_receipt:{order_id}"
        )
    )

    return kb


# ══════════════════════════════════════════════════════════════════════════════
# DELIVERY
# ══════════════════════════════════════════════════════════════════════════════

def notify_admins_receipt(order_id):
    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

    if not order:
        return

    gmail_line = ""

    if order["gmail_address"]:
        gmail_line = (
            f"\n📧 جیمیل: "
            f"<code>{esc(order['gmail_address'])}</code>"
        )

    username_line = ""

    if order["service_username"]:
        username_line = (
            f"\n👤 نام کاربری: "
            f"<b>{esc(order['service_username'])}</b>"
        )

    wallet_used = int(
        order["wallet_reserved"]
    )

    cash_required = int(
        order["cash_required"]
    )

    price = int(
        order["price"]
    )

    if wallet_used > 0:

        wallet_block = (
            f"💰 قیمت سرویس: <b>{money(price)}</b>\n"
            f"👛 موجودی کیف پول کاربر (کسر شد): <b>{money(wallet_used)}</b>\n"
            f"💵 مبلغ فیش ارسالی: <b>{money(cash_required)}</b>\n"
            f"👛 موجودی فعلی کاربر: <b>{money(get_balance(order['user_id']))}</b>\n"
            "ℹ️ این کاربر از قبل "
            f"<b>{money(wallet_used)}</b> موجودی کیف پول داشته است؛ "
            f"به همین دلیل به‌جای <b>{money(price)}</b> فقط "
            f"<b>{money(cash_required)}</b> واریز کرده است.\n"
        )

    else:

        wallet_block = (
            f"💰 قیمت سرویس: <b>{money(price)}</b>\n"
            f"💵 مبلغ فیش ارسالی: <b>{money(cash_required)}</b>\n"
        )

    text = (
        "💳 <b>فیش پرداخت جدید</b>\n\n"
        f"سفارش: <b>#{order_id}</b>\n"
        f"محصول: <b>{esc(order['product_title'])}</b>\n"
        f"{wallet_block}"
        f"کاربر: <code>{order['user_id']}</code>"
        f"{gmail_line}"
        f"{username_line}\n\n"
        "لطفاً فیش را بررسی کنید."
    )

    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.add(
        types.InlineKeyboardButton(
            "✅ تأیید",
            callback_data=f"receipt:approve:{order_id}"
        ),
        types.InlineKeyboardButton(
            "❌ رد",
            callback_data=f"receipt:reject:{order_id}"
        )
    )

    for admin_id in ADMIN_IDS:
        try:

            if order["receipt_type"] == "photo":

                bot.send_photo(
                    admin_id,
                    order["receipt_file_id"],
                    caption=text,
                    reply_markup=kb
                )

            else:

                bot.send_document(
                    admin_id,
                    order["receipt_file_id"],
                    caption=text,
                    reply_markup=kb
                )

        except Exception:
            logger.exception(
                "Failed notifying admin %s",
                admin_id
            )


def notify_fulfillment_admins(order_id):
    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

    if not order:
        return

    gmail_line = ""

    if order["gmail_address"]:
        gmail_line = (
            f"\n📧 جیمیل مشتری: "
            f"<code>{esc(order['gmail_address'])}</code>"
        )

    username_line = ""

    if order["service_username"]:
        username_line = (
            f"\n👤 نام کاربری: "
            f"<b>{esc(order['service_username'])}</b>"
        )

    wallet_used = int(
        order["wallet_reserved"]
    )

    cash_required = int(
        order["cash_required"]
    )

    price = int(
        order["price"]
    )

    if cash_required <= 0 and wallet_used > 0:

        payment_block = (
            "💳 نحوه پرداخت: <b>کیف پول (بدون فیش)</b>\n"
            f"💰 قیمت سرویس: <b>{money(price)}</b>\n"
            f"👛 از کیف پول کاربر کسر شد: <b>{money(wallet_used)}</b>\n"
            f"👛 موجودی فعلی کاربر: <b>{money(get_balance(order['user_id']))}</b>\n"
            "ℹ️ موجودی کیف پول این کاربر برای خرید کافی بوده، "
            "بنابراین فیشی ارسال نکرده است.\n"
        )

    elif wallet_used > 0:

        payment_block = (
            "💳 نحوه پرداخت: <b>کیف پول + فیش</b>\n"
            f"💰 قیمت سرویس: <b>{money(price)}</b>\n"
            f"👛 از کیف پول: <b>{money(wallet_used)}</b>\n"
            f"💵 فیش واریزی: <b>{money(cash_required)}</b>\n"
        )

    else:

        payment_block = (
            "💳 نحوه پرداخت: <b>فیش واریزی</b>\n"
            f"💰 قیمت سرویس: <b>{money(price)}</b>\n"
        )

    text = (
        "📦 <b>سفارش آماده تحویل</b>\n\n"
        f"سفارش: <b>#{order_id}</b>\n"
        f"محصول: <b>{esc(order['product_title'])}</b>\n"
        f"{payment_block}"
        f"کاربر: <code>{order['user_id']}</code>"
        f"{gmail_line}"
        f"{username_line}\n\n"
        "برای شروع تحویل، دکمه زیر را بزنید."
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "📦 شروع تحویل سفارش",
            callback_data=f"fulfill:start:{order_id}"
        )
    )

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                text,
                reply_markup=kb
            )
        except Exception:
            logger.exception(
                "Failed notifying fulfillment admin %s",
                admin_id
            )


def wireguard_trial_admin_text(
    user_id,
    username
):
    tg_line = ""

    with db() as conn:

        row = conn.execute(
            """
            SELECT username, first_name
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

    if row:

        parts = []

        if row["first_name"]:
            parts.append(
                esc(row["first_name"])
            )

        if row["username"]:
            parts.append(
                f"@{esc(row['username'])}"
            )

        if parts:
            tg_line = (
                "🆔 اکانت تلگرام: "
                + " — ".join(parts)
                + "\n"
            )

    return (
        "🧪 <b>درخواست اکانت تست وایرساک</b>\n\n"
        f"کاربر <b>{esc(username)}</b> درخواست اکانت تست و ارسال دارد.\n\n"
        f"👤 نام کاربری تست: <b>{esc(username)}</b>\n"
        f"{tg_line}"
        f"🔢 آیدی عددی: <code>{user_id}</code>\n\n"
        "برای تحویل اکانت تست فقط دکمه زیر را بزنید؛ ربات خودش از EylanPanel اکانت را می‌سازد، لینک پنل را می‌گیرد و برای کاربر می‌فرستد.\n\n"
        "ℹ️ در پیام آماده به کاربر یادآوری می‌شود که از اولین اتصال فقط "
        "<b>۱ روز</b> فرصت تست دارد و اگر اصلاً وصل نشود، "
        "اکانت حداکثر <b>۲ روز</b> فعال می‌ماند."
    )


def wireguard_trial_admin_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "📤 ارسال اکانت تست",
            callback_data=f"wgtrial:send:{user_id}"
        )
    )

    return kb


def notify_admins_wireguard_trial(
    user_id,
    username
):
    text = wireguard_trial_admin_text(
        user_id,
        username
    )

    kb = wireguard_trial_admin_keyboard(
        user_id
    )

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                text,
                reply_markup=kb
            )
        except Exception:
            logger.exception(
                "Failed notifying admin %s about wireguard trial",
                admin_id
            )


def service_for_product(product_id):
    if product_id.startswith("wireguard-"):
        return "wireguard"

    if product_id.startswith("v2ray-"):
        return "v2ray"

    if product_id.startswith("chatgpt-"):
        return "chatgpt"

    return "other"


def is_url_text(text):
    if not isinstance(text, str):
        return False

    stripped = text.strip()

    if not stripped:
        return False

    if any(
        ch.isspace()
        for ch in stripped
    ):
        return False

    if URL_RE.match(stripped):
        return True

    return bool(
        re.match(
            r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?$",
            stripped
        )
    )


def normalized_link(value):
    value = value.strip()

    if value.startswith(
        (
            "http://",
            "https://",
            "tg://"
        )
    ):
        return value

    return "https://" + value


def delivery_template(
    service,
    payload,
    order
):
    payload = payload.strip()

    href = (
        normalized_link(payload)
        if is_url_text(payload)
        else None
    )

    if href:
        link_box = (
            f'<a href="{esc(href)}">'
            f'{esc(payload)}'
            f'</a>'
        )
    else:
        link_box = (
            f"<b>{esc(payload)}</b>"
        )

    if service == "wireguard":

        return (
            "<b>اکانت کاربری شما ساخته شد☑️</b>\n\n"
            "🎮 <b>سرویس وایرساک / وایرگارد شما:</b>\n\n"
            f"{link_box}\n\n"
            "👆 این لینک، لینک اشتراک اختصاصی سرویس شماست.\n"
            "آن را برای مدیریت/دریافت تنظیمات سرویس در برنامه یا پنل سازگار با سرویس استفاده کنید.\n\n"
            "📚 <b>راهنمای اتصال</b>\n"
            "برای اتصال صحیح، از منوی پایین روی «🔗 نحوه اتصال» بزنید و آموزش مربوط به همین سرویس را مرحله‌به‌مرحله دنبال کنید.\n\n"
            "✨ امیدواریم تجربه‌ای سریع، پایدار و لذت‌بخش داشته باشید."
        )

    if service == "v2ray":

        return (
            "🎉 <b>سفارش شما با موفقیت آماده شد!</b>\n\n"
            "🙏 از اینکه این ربات را برای خرید سرویس خود انتخاب کردید، صمیمانه سپاسگزاریم.\n"
            "💙 اعتماد شما برای ما ارزشمند است و خوشحالیم که در خدمت شما هستیم.\n\n"
            "✅ <b>رسید سفارش شما تأیید شد.</b>\n\n"
            "🛜 <b>لینک ساب شما:</b>\n\n"
            f"{link_box}\n\n"
            "📌 لینک بالا را باز کنید و در برنامه اتصال موردنظر خود وارد کنید.\n\n"
            "📚 <b>آموزش اتصال V2Ray</b>\n"
            "برای مشاهده آموزش کامل، از منوی پایین روی «🔗 نحوه اتصال» بزنید.\n"
            "آموزش‌های تصویری و ویدئویی لازم برای اتصال V2Ray در همان بخش قرار گرفته‌اند.\n\n"
            "✨ امیدواریم از سرعت و پایداری سرویس خود لذت ببرید.\n"
            "❤️ ممنون که ما را انتخاب کردید."
        )

    guide_line = (
        "📚 برای راهنمای استفاده و آموزش، از بخش «🔗 نحوه اتصال» استفاده کنید.\n\n"
        if service in USER_CONNECTION_SERVICES
        else "📚 در صورت نیاز به راهنمایی، از بخش «🎧 پشتیبانی» اقدام کنید.\n\n"
    )

    return (
        "🎉 <b>سفارش شما با موفقیت آماده شد!</b>\n\n"
        "🙏 از اعتماد شما و انتخاب این ربات سپاسگزاریم.\n\n"
        "✅ <b>اطلاعات اشتراک شما:</b>\n\n"
        f"{link_box}\n\n"
        f"{guide_line}"
        "❤️ از همراهی شما متشکریم."
    )


def wireguard_trial_delivery_template(
    payload,
    username=None
):
    payload = payload.strip()

    href = (
        normalized_link(payload)
        if is_url_text(payload)
        else None
    )

    if href:
        link_box = (
            f'<a href="{esc(href)}">'
            "تست"
            f'</a>\n\n'
            f"<code>{esc(payload)}</code>"
        )
    else:
        link_box = (
            f"<b>{esc(payload)}</b>"
        )

    username_line = ""

    if username:
        username_line = (
            f"👤 نام کاربری: <b>{esc(username)}</b>\n\n"
        )

    return (
        "🎉 <b>اکانت تست شما با موفقیت آماده شد!</b>\n\n"
        "🙏 از اینکه این ربات را برای دریافت سرویس خود انتخاب کردید، صمیمانه سپاسگزاریم.\n"
        "💙 اعتماد شما برای ما ارزشمند است و خوشحالیم که در خدمت شما هستیم.\n\n"
        "✅ <b>درخواست اکانت تست شما تأیید شد.</b>\n\n"
        f"{username_line}"
        "🧪 <b>اکانت تست وایرساک / وایرگارد شما:</b>\n\n"
        f"{link_box}\n\n"
        "👆 این لینک، لینک اشتراک سرویس تست شماست. آن را در برنامه سازگار با سرویس وارد کنید.\n"
        "در صورت پشتیبانی برنامه، حجم باقی‌مانده، مدت اعتبار و سرورهای قابل استفاده نمایش داده می‌شود.\n\n"
        f"{TRIAL_IMPORTANT_NOTES}\n\n"
        "📚 <b>راهنمای اتصال</b>\n"
        "برای اتصال صحیح، از منوی پایین روی «🔗 نحوه اتصال» بزنید و آموزش مربوط به همین سرویس را مرحله‌به‌مرحله دنبال کنید.\n\n"
        "🛒 برای تهیه سرویس کامل، از منوی پایین روی «🖥 خرید سرور» بزنید و حجم موردنظر خود را انتخاب کنید.\n\n"
        "✨ امیدواریم تجربه‌ای سریع، پایدار و لذت‌بخش داشته باشید.\n"
        "❤️ ممنون که ما را انتخاب کردید."
    )


def chatgpt_shared_delivery_template(
    gmail_address
):
    return (
        "✅ <b>دسترسی اکانت ChatGPT</b>\n\n"
        f"آدرس کانال:\n{esc(CHANNEL_USERNAME)}\n\n"
        f"ایمیل:\n<b>{esc(gmail_address)}</b>\n\n"
        "پسورد:\n<b>با کد یکبار مصرف وارد شوید.</b>\n\n"
        "⛔️ لطفاً این اطلاعات را با هیچ‌کس به اشتراک نگذار.\n\n"
        "🟢 موقع لاگین داخل اکانت، ورود از طریق کد یکبار مصرف رو بزنید و سپس در ربات روی دکمه «دریافت کد از پشتیبانی» کلیک کرده و از پشتیبانی درخواست دریافت کد کنید!\n\n"
        "🟢 برای دفعات بعدی اگر لاگ اوت شدید و نیاز به دریافت مجدد کد داشتید، از داخل منو وارد بخش «📦 سرویس های من» شده و سرویس خود را انتخاب کنید و بر روی دکمه «ارسال پیام به پشتیبانی» کلیک کرده و مشکلات خود را در قالب پیام یا عکس یا هردو ارسال نمایید تا در اولین فرصت توسط پشتیبانی پاسخ داده شود!"
    )


def chatgpt_personal_delivery_template(
    gmail_address
):
    return (
        "اکانت پلاس بر روی اکانت زیر فعال شد✅\n\n"
        f"ایمیل:\n<b>{esc(gmail_address)}</b>\n\n"
        "از سفارش شما متشکریم🌹"
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRICE OVERRIDES (تغییر قیمت سرویس‌ها از پنل ادمین)
# ══════════════════════════════════════════════════════════════════════════════

def apply_product_price(product_id, price):
    """قیمت را هم در PRODUCT_INDEX و هم در لیست دسته‌بندی‌شده اعمال می‌کند."""

    product = PRODUCT_INDEX.get(product_id)

    if not product:
        return False

    price = int(price)

    product["price"] = price

    for items in PRODUCTS.values():
        for item in items:
            if item.get("id") == product_id:
                item["price"] = price

    return True


def load_price_overrides():
    """
    قیمت‌های ذخیره‌شده در دیتابیس را روی محصولات اعمال می‌کند.
    بعد از بازگردانی دیتابیس هم صدا زده می‌شود تا قیمت‌ها با دیتابیس جدید یکی شوند.
    """

    for pid, default_price in DEFAULT_PRICES.items():
        apply_product_price(pid, default_price)

    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT product_id, price FROM price_overrides"
            ).fetchall()

    except sqlite3.Error:
        logger.exception("Failed loading price overrides")
        return 0

    applied = 0

    for row in rows:
        if (
            row["product_id"] in PRODUCT_INDEX
            and int(row["price"]) > 0
        ):
            apply_product_price(
                row["product_id"],
                row["price"]
            )

            applied += 1

    return applied


def set_product_price(product_id, price, admin_id=None):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO price_overrides(
                product_id,
                price,
                updated_by,
                updated_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(product_id) DO UPDATE SET
                price=excluded.price,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
            """,
            (
                product_id,
                int(price),
                admin_id,
                now()
            )
        )

    apply_product_price(product_id, price)


def reset_product_price(product_id):
    with db() as conn:
        conn.execute(
            "DELETE FROM price_overrides WHERE product_id=?",
            (product_id,)
        )

    apply_product_price(
        product_id,
        DEFAULT_PRICES[product_id]
    )


# ══════════════════════════════════════════════════════════════════════════════
# AKHAVAN PROFIT LEDGER (سود اخوان)
# ══════════════════════════════════════════════════════════════════════════════

def akhavan_current_balance(conn):
    """موجودی فعلی سود؛ None یعنی هنوز مبلغ اولیه تنظیم نشده است."""

    row = conn.execute(
        """
        SELECT balance_after
        FROM akhavan_profit_ledger
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    return int(row["balance_after"]) if row else None


def akhavan_set_balance(value):
    """
    اولین بار: مبلغ اولیه (opening) ثبت می‌شود و حسابداری از همان لحظه شروع می‌شود.
    دفعات بعد: موجودی به مبلغ جدید اصلاح می‌شود (kind='set') و در گزارش دیده می‌شود.
    """

    value = int(value)

    with db() as conn:
        current = akhavan_current_balance(conn)

        if current is None:
            kind = "opening"
            delta = value
            note = "مبلغ اولیه"
        else:
            kind = "set"
            delta = value - current
            note = "تنظیم دستی مبلغ"

        conn.execute(
            """
            INSERT INTO akhavan_profit_ledger(
                kind,
                amount,
                balance_after,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                kind,
                delta,
                value,
                note,
                now()
            )
        )

    return kind, delta


def _record_akhavan_profit(conn, order):
    """
    داخل همان تراکنش complete_order صدا زده می‌شود.
    اگر سود ثبت شد یک dict برمی‌گرداند، وگرنه None
    (محصول بدون نرخ سود، مبلغ اولیه هنوز تنظیم نشده، یا سفارش قبلاً ثبت شده).
    """

    profit = akhavan_profit_for_product(
        order["product_id"]
    )

    if profit <= 0:
        return None

    current = akhavan_current_balance(conn)

    if current is None:
        return None

    already = conn.execute(
        """
        SELECT 1
        FROM akhavan_profit_ledger
        WHERE order_id=?
        """,
        (order["id"],)
    ).fetchone()

    if already:
        return None

    new_balance = current + profit

    conn.execute(
        """
        INSERT INTO akhavan_profit_ledger(
            kind,
            order_id,
            user_id,
            product_id,
            product_title,
            amount,
            balance_after,
            note,
            created_at
        )
        VALUES ('sale', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order["id"],
            order["user_id"],
            order["product_id"],
            order["product_title"],
            profit,
            new_balance,
            "سود فروش سرویس",
            now()
        )
    )

    return {
        "order_id": order["id"],
        "product_title": order["product_title"],
        "profit": profit,
        "previous": current,
        "balance": new_balance
    }


def _notify_akhavan_profit(entry):
    # این پیام حالا برای همه‌ی ادمین‌ها ارسال می‌شود چون «💐 سود اخوان»
    # دیگر مخصوص یک ادمین خاص نیست.
    recipients = set(ADMIN_IDS) | ({PROFIT_ADMIN_ID} if PROFIT_ADMIN_ID else set())

    for admin_id in recipients:
        try:
            bot.send_message(
                admin_id,
                "🌹 <b>سود جدید ثبت شد</b>\n\n"
                f"🛍 سفارش #{entry['order_id']} — {esc(entry['product_title'])}\n"
                f"➕ سود این خرید: <b>{money(entry['profit'])}</b>\n\n"
                f"🌸 سود مجموعه بعد از این خرید <b>{money(entry['balance'])}</b> شد 🌺"
            )

        except Exception:
            logger.exception(
                "Failed sending Akhavan profit notification to %s",
                admin_id
            )


def complete_order(order_id):
    t = now()
    entry = None

    with db() as conn:
        previous = conn.execute(
            "SELECT status FROM orders WHERE id=?",
            (order_id,)
        ).fetchone()

        conn.execute(
            """
            UPDATE orders
            SET status='completed',
                completed_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                t,
                t,
                order_id
            )
        )

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

        # سود اخوان فقط یک بار و فقط وقتی سفارش تازه تکمیل می‌شود ثبت می‌شود.
        if (
            order
            and previous
            and previous["status"] != "completed"
        ):
            try:
                entry = _record_akhavan_profit(
                    conn,
                    order
                )

            except Exception:
                entry = None

                logger.exception(
                    "Failed recording Akhavan profit for order #%s",
                    order_id
                )

    if entry:
        _notify_akhavan_profit(entry)

    return order


# ══════════════════════════════════════════════════════════════════════════════
# FULFILLMENT
# ══════════════════════════════════════════════════════════════════════════════

EYLAN_USERNAME_RE = re.compile(
    r"^[A-Za-z0-9_-]{1,80}$"
)


def eylan_plan_for_product(product_id):
    plan = EYLAN_PLANS.get(product_id)
    if not plan:
        raise EylanPanelError(
            f"برای محصول «{product_id}» پلن EylanPanel در eylan_plans.json تعریف نشده است."
        )
    return dict(plan)


def _plan_service(product_id):
    if product_id.startswith("wireguard-"):
        return "wireguard"
    if product_id.startswith("v2ray-"):
        return "v2ray"
    return None


def _resolve_required_node_ids(client, plan):
    configured = plan.get("required_node_names") or EYLAN_REQUIRED_NODE_NAMES
    names = [str(x).strip() for x in configured if str(x).strip()]

    if not names:
        raise EylanPanelError(
            "هیچ Node برای این محصول تعریف نشده است. "
            "EYLAN_REQUIRED_NODE_NAMES یا required_node_names را تنظیم کنید."
        )

    nodes = client.get_nodes()
    normalized = {}
    for node in nodes:
        node_id = node.get("id")
        node_name = str(node.get("name") or "").strip()
        if node_id is None or not node_name:
            continue
        normalized[node_name.casefold()] = node_id

    missing = [name for name in names if name.casefold() not in normalized]
    if missing:
        available = [
            str(node.get("name") or "").strip()
            for node in nodes
            if node.get("name")
        ]
        raise EylanPanelError(
            "این Nodeها برای API Key در EylanPanel پیدا نشدند: "
            f"{', '.join(missing)}. Nodeهای قابل مشاهده: "
            f"{', '.join(available) if available else 'هیچ موردی'}"
        )

    # Preserve the order configured by the shop so the same four nodes are
    # always attached to every automatically provisioned server.
    return [normalized[name.casefold()] for name in names]


def _resolve_singbox_inbounds(client, plan):
    configured = plan.get("singbox_inbounds") or EYLAN_SINGBOX_INBOUNDS
    if configured:
        return list(dict.fromkeys(str(x).strip() for x in configured if str(x).strip()))

    # No static list configured: discover the inbounds allowed for this API key.
    remote = client.get_singbox_inbounds()
    tags = []
    for item in remote:
        tag = str(item.get("tag") or "").strip()
        if tag:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))
    if not tags:
        raise EylanPanelError(
            "هیچ Sing-box inbound مجازی برای API Key شما پیدا نشد. "
            "یا EYLAN_SINGBOX_INBOUNDS را تنظیم کنید یا دسترسی inbound را در EylanPanel بررسی کنید."
        )
    return tags


def eylan_payload_for_product(
    product_id,
    username,
    *,
    for_update=False,
    client=None,
):
    plan = eylan_plan_for_product(product_id)

    if not EYLAN_USERNAME_RE.fullmatch((username or "").strip()):
        raise EylanPanelError(
            "نام کاربری سرویس باید فقط شامل حروف انگلیسی، عدد، _ یا - و حداکثر ۸۰ کاراکتر باشد."
        )

    service = _plan_service(product_id) or plan.get("service")
    if service not in {"wireguard", "v2ray"}:
        raise EylanPanelError(
            f"محصول «{product_id}» برای اتوماسیون EylanPanel سرویس معتبری ندارد."
        )

    payload = {
        "max_clients": int(plan.get("max_clients", 1)),
        "data_limit": plan.get("data_limit", 0),
        "data_limit_unit": plan.get("data_limit_unit", "GB"),
    }

    # The EylanPanel API supports a `nodes` array. For this shop we explicitly
    # resolve and attach all four required nodes instead of relying on panel
    # defaults, so no customer silently receives a partial node set.
    api_client = client or get_eylan_client()
    payload["nodes"] = _resolve_required_node_ids(api_client, plan)

    if service == "wireguard":
        payload.update({
            "activation_type": plan.get("activation_type", "fixed_date"),
            "enable_wg1": bool(plan.get("enable_wg1", True)),
            "enable_openvpn": bool(plan.get("enable_openvpn", False)),
        })

        data_limit = plan.get("data_limit")
        if product_id == TRIAL_PRODUCT_ID and EYLAN_TRIAL_DATA_LIMIT is not None:
            data_limit = EYLAN_TRIAL_DATA_LIMIT
        if data_limit is None:
            payload.pop("data_limit", None)
        else:
            payload["data_limit"] = data_limit

        if for_update:
            activation_type = payload["activation_type"]
            if activation_type == "flexible_days":
                payload["pending_activation_days"] = int(
                    plan.get("pending_activation_days", 2)
                )
                payload["expiry_days"] = int(plan.get("expiry_days", 1))
            else:
                # Extend from the later of now/current remote expiry so a
                # renewal never shortens an already-active service.
                remote = None
                if client is not None:
                    remote = client.get_user(username)
                base_dt = datetime.now(timezone.utc)
                if isinstance(remote, dict):
                    for key in ("expiry_date", "expires_at", "expiry_date_str"):
                        raw = remote.get(key)
                        if not raw:
                            continue
                        try:
                            raw_text = str(raw).replace("Z", "+00:00")
                            parsed = datetime.fromisoformat(raw_text)
                            if parsed.tzinfo is None:
                                parsed = parsed.replace(tzinfo=timezone.utc)
                            base_dt = max(base_dt, parsed.astimezone(timezone.utc))
                            break
                        except (TypeError, ValueError):
                            continue
                expiry_days = int(plan.get("expiry_days", 30))
                payload["expiry_date_str"] = (
                    base_dt + timedelta(days=expiry_days)
                ).date().isoformat()
            return payload

        payload["username"] = username.strip()
        if payload["activation_type"] == "fixed_date":
            payload["expiry_days"] = int(plan.get("expiry_days", 30))
        else:
            payload["expiry_days"] = int(plan.get("expiry_days", 1))
            payload["pending_activation_days"] = int(
                plan.get("pending_activation_days", 2)
            )
        return payload

    # V2Ray in EylanPanel is exposed through Sing-box. These shop products are
    # time-unlimited, so no fixed expiry field is sent. Access is controlled
    # by the data limit and the allowed Sing-box inbounds.
    payload.update({
        "enable_singbox": True,
    })
    if not for_update:
        # A brand-new V2Ray user starts with WireGuard/OpenVPN disabled.
        payload["enable_wg1"] = False
        payload["enable_openvpn"] = False
    inbounds = _resolve_singbox_inbounds(
        client or get_eylan_client(),
        plan,
    )
    payload["singbox_inbounds"] = inbounds

    if not for_update:
        payload["username"] = username.strip()

    return payload


def local_username_belongs_to_user(
    user_id,
    username
):
    username = (username or "").strip()
    if not username:
        return False

    with db() as conn:
        order_rows = conn.execute(
            """
            SELECT user_id
            FROM orders
            WHERE service_username=?
            """,
            (username,)
        ).fetchall()

        service_rows = conn.execute(
            """
            SELECT user_id
            FROM active_services
            WHERE service_username=?
            """,
            (username,)
        ).fetchall()

        trial_rows = conn.execute(
            """
            SELECT user_id
            FROM wireguard_trials
            WHERE username=?
            """,
            (username,)
        ).fetchall()

    owners = {
        int(row["user_id"])
        for row in (order_rows + service_rows + trial_rows)
    }

    if not owners:
        return False

    if any(owner != int(user_id) for owner in owners):
        raise EylanPanelError(
            f"نام کاربری {username} در دیتابیس این ربات متعلق به کاربر دیگری است؛ برای جلوگیری از تغییر اشتباه، عملیات متوقف شد."
        )

    return True


def save_order_api_state(
    order_id,
    *,
    api_username=None,
    api_link=None,
    api_status=None,
    api_error=None,
    service_username=None,
    delivery_message_id=None,
    delivery_mode=None,
):
    fields = []
    values = []

    if api_username is not None:
        fields.append("api_username=?")
        values.append(api_username)

    if api_link is not None:
        fields.append("api_link=?")
        values.append(api_link)

    if api_status is not None:
        fields.append("api_status=?")
        values.append(api_status)

    if api_error is not None:
        fields.append("api_error=?")
        values.append(api_error)

    if service_username is not None:
        fields.append("service_username=?")
        values.append(service_username)

    if delivery_message_id is not None:
        fields.append("delivery_message_id=?")
        values.append(delivery_message_id)

    if delivery_mode is not None:
        fields.append("delivery_mode=?")
        values.append(delivery_mode)

    fields.append("updated_at=?")
    values.append(now())
    values.append(order_id)

    with db() as conn:
        conn.execute(
            f"UPDATE orders SET {', '.join(fields)} WHERE id=?",
            tuple(values)
        )


def get_order_by_id_for_fulfillment(
    order_id,
    admin_id
):
    with db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND assigned_admin_id=?
            AND status='fulfilling'
            """,
            (order_id, admin_id)
        ).fetchone()


def ensure_eylan_active_service(order, order_id):
    service = service_for_product(order["product_id"])
    if service not in {"wireguard", "v2ray"}:
        raise EylanPanelError("این محصول جزو سرویس‌های خودکار EylanPanel نیست.")

    renewal_for_service_id = (
        order["renewal_for_service"]
        if "renewal_for_service" in order.keys()
        else None
    )

    if renewal_for_service_id:
        # V2Ray products are time-unlimited; there is no local expiry to extend.
        if service == "wireguard":
            renew_service(renewal_for_service_id, order_id)
        else:
            with db() as conn:
                conn.execute(
                    """
                    UPDATE active_services
                    SET renewal_order_id=?, is_expired=0
                    WHERE id=?
                    """,
                    (order_id, renewal_for_service_id)
                )
        return renewal_for_service_id

    with db() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM active_services
            WHERE user_id=?
            AND order_id=?
            AND service_type=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (order["user_id"], order_id, service),
        ).fetchone()

    if existing:
        return existing["id"]

    plan = eylan_plan_for_product(order["product_id"])
    has_expiry = service == "wireguard"
    expiry_days = int(plan.get("expiry_days", 30)) if has_expiry else 0

    return create_active_service(
        user_id=order["user_id"],
        order_id=order_id,
        service_type=service,
        product_id=order["product_id"],
        product_title=order["product_title"],
        gmail_address=order["gmail_address"],
        has_expiry=has_expiry,
        expiry_days=expiry_days,
        service_username=order["service_username"],
    )


def provision_eylan_order_with_api(order):
    product_id = order["product_id"]
    service = service_for_product(product_id)
    if service not in {"wireguard", "v2ray"}:
        raise EylanPanelError(
            f"محصول «{product_id}» برای تحویل خودکار پشتیبانی نمی‌شود."
        )

    username = (order["service_username"] or "").strip()
    if not username:
        raise EylanPanelError("نام کاربری سرویس برای این سفارش ثبت نشده است.")

    # If the subscription was already generated, never create a second user.
    if (
        order["api_link"]
        and order["api_status"] in ("link_ready", "delivered", "message_sent")
        and order["api_username"]
    ):
        return order["api_username"], order["api_link"]

    client = get_eylan_client()

    # IMPORTANT: if create/update already succeeded but the following request
    # failed (for example while fetching the subscription URL), do not repeat
    # the mutation on retry. Re-fetch the link only.
    persisted_username = (order["api_username"] or "").strip()
    persisted_status = (order["api_status"] or "").strip()
    if persisted_username and persisted_status in {"created", "updated"}:
        link = client.get_subscription_link(persisted_username)
        save_order_api_state(
            order["id"],
            api_username=persisted_username,
            api_link=link,
            api_status="link_ready",
            api_error="",
            service_username=persisted_username,
        )
        return persisted_username, link

    # For existing users, validate local ownership before touching the remote account.
    existing_remote = client.get_user(username)
    if existing_remote is not None:
        if not local_username_belongs_to_user(order["user_id"], username):
            raise EylanPanelError(
                f"نام کاربری {username} از قبل در EylanPanel وجود دارد، اما در دیتابیس این ربات برای این کاربر ثبت نشده است؛ "
                "برای جلوگیری از دستکاری اکانت شخص دیگر، سفارش متوقف شد."
            )

        update_payload = eylan_payload_for_product(
            product_id,
            username,
            for_update=True,
            client=client,
        )
        client.update_user(username, update_payload)
        final_username = username
        save_order_api_state(
            order["id"],
            api_username=final_username,
            api_status="updated",
        )
    else:
        payload = eylan_payload_for_product(
            product_id,
            username,
            for_update=False,
            client=client,
        )
        final_username = client.create_user(payload)
        save_order_api_state(
            order["id"],
            api_username=final_username,
            api_status="created",
            service_username=final_username,
        )

    link = client.get_subscription_link(final_username)

    save_order_api_state(
        order["id"],
        api_username=final_username,
        api_link=link,
        api_status="link_ready",
        api_error="",
        service_username=final_username,
    )

    return final_username, link


# Backward-compatible wrappers kept so older internal calls remain safe.
def provision_wireguard_order_with_api(order):
    return provision_eylan_order_with_api(order)


def provision_v2ray_order_with_api(order):
    return provision_eylan_order_with_api(order)


def _save_trial_panel_link(
    user_id,
    panel_link
):
    with db() as conn:
        conn.execute(
            """
            UPDATE wireguard_trials
            SET panel_link=?
            WHERE user_id=?
            """,
            (
                panel_link,
                user_id
            )
        )


def provision_wireguard_trial_with_api(
    trial
):
    username = (trial["username"] or "").strip()

    if not username:
        raise EylanPanelError(
            "نام کاربری اکانت تست خالی است."
        )

    if trial["panel_link"]:
        return username, trial["panel_link"]

    client = get_eylan_client()
    payload = eylan_payload_for_product(
        TRIAL_PRODUCT_ID,
        username,
        for_update=False
    )

    existing_remote = client.get_user(username)

    if existing_remote is not None:
        # این username قبلاً توسط همین درخواست تست این ربات ثبت شده است؛
        # فقط لینک را دوباره از API می‌گیریم تا کاربر تکراری ساخته نشود.
        local_username_belongs_to_user(
            trial["user_id"],
            username
        )
        final_username = username
    else:
        final_username = client.create_user(
            payload
        )

    link = client.get_subscription_link(
        final_username
    )

    _save_trial_panel_link(
        trial["user_id"],
        link
    )

    if final_username != username:
        with db() as conn:
            conn.execute(
                """
                UPDATE wireguard_trials
                SET username=?
                WHERE user_id=?
                """,
                (
                    final_username,
                    trial["user_id"]
                )
            )

    return final_username, link


def _fulfillment_retry_keyboard(order_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "🔄 تلاش مجدد تحویل خودکار",
            callback_data=f"fulfill:retry:{order_id}"
        ),
        types.InlineKeyboardButton(
            "✋ تبدیل به تحویل دستی",
            callback_data=f"fulfill:manual:{order_id}"
        ),
        types.InlineKeyboardButton(
            "❌ لغو حالت تحویل",
            callback_data="fulfill:cancel"
        )
    )
    return kb


def deliver_eylan_order_via_api(admin_id, order_id):
    order = get_order_by_id_for_fulfillment(order_id, admin_id)
    if not order:
        clear_admin_state(admin_id)
        bot.send_message(admin_id, "این سفارش دیگر در حالت تحویل نیست.")
        return True

    service = service_for_product(order["product_id"])
    # V2Ray دیگر خودکار تحویل داده نمی‌شود (از پنل جداگانه و دستی ارسال می‌شود).
    if service != "wireguard":
        return False

    try:
        final_username, panel_link = provision_eylan_order_with_api(order)
        order = get_order_by_id_for_fulfillment(order_id, admin_id) or order

        # Local state is prepared before the customer receives the link.
        ensure_eylan_active_service(order, order_id)

        existing_message_id = order["delivery_message_id"]
        if not existing_message_id:
            delivery_msg = delivery_template(service, panel_link, order)
            sent = bot.send_message(
                order["user_id"],
                delivery_msg,
                reply_markup=reply_main_keyboard(),
            )
            existing_message_id = sent.message_id
            save_order_api_state(
                order_id,
                delivery_message_id=existing_message_id,
                api_status="message_sent",
            )

        complete_order(order_id)
        save_order_api_state(
            order_id,
            api_username=final_username,
            api_link=panel_link,
            api_status="delivered",
            api_error="",
            delivery_message_id=existing_message_id,
        )

        clear_admin_state(admin_id)
        label = "WireGuard" if service == "wireguard" else "V2Ray / Sing-box"
        bot.send_message(
            admin_id,
            f"✅ سفارش #{order_id} به‌صورت خودکار ساخته و تحویل داده شد.\n"
            f"🔌 نوع سرویس: <b>{label}</b>\n"
            f"👤 نام کاربری: <b>{esc(final_username)}</b>"
        )

    except EylanPanelError as exc:
        error_text = str(exc)
        logger.exception("EylanPanel fulfillment failed for order #%s", order_id)
        save_order_api_state(
            order_id,
            api_status="failed",
            api_error=error_text,
        )
        bot.send_message(
            admin_id,
            "❌ تحویل خودکار انجام نشد.\n\n"
            f"<code>{esc(error_text)}</code>\n\n"
            "سفارش در حالت تحویل باقی مانده و با دکمه زیر می‌توانید دوباره تلاش کنید.",
            reply_markup=_fulfillment_retry_keyboard(order_id),
        )

    except Exception as exc:
        logger.exception("Unexpected automatic fulfillment failure for order #%s", order_id)
        save_order_api_state(
            order_id,
            api_status="failed",
            api_error=str(exc),
        )
        bot.send_message(
            admin_id,
            "❌ در فرایند تحویل خطای غیرمنتظره رخ داد.\n\n"
            f"<code>{esc(str(exc))}</code>",
            reply_markup=_fulfillment_retry_keyboard(order_id),
        )

    return True


def deliver_wireguard_order_via_api(admin_id, order_id):
    return deliver_eylan_order_via_api(admin_id, order_id)


def deliver_v2ray_order_via_api(admin_id, order_id):
    return deliver_eylan_order_via_api(admin_id, order_id)


def deliver_wireguard_trial_via_api(
    admin_id,
    target_id
):
    trial = get_wireguard_trial(target_id)

    if not trial:
        clear_admin_state(admin_id)
        bot.send_message(
            admin_id,
            "درخواست تست پیدا نشد."
        )
        return True

    try:
        final_username, panel_link = provision_wireguard_trial_with_api(
            trial
        )

        delivery_msg = wireguard_trial_delivery_template(
            panel_link,
            final_username
        )

        bot.send_message(
            target_id,
            delivery_msg,
            reply_markup=reply_main_keyboard()
        )

        mark_wireguard_trial_delivered(
            target_id,
            admin_id,
            panel_link
        )

        try:
            with db() as conn:
                already = conn.execute(
                    """
                    SELECT id
                    FROM active_services
                    WHERE user_id=?
                    AND service_type=?
                    """,
                    (
                        target_id,
                        TRIAL_SERVICE_TYPE
                    )
                ).fetchone()

            if not already:
                create_active_service(
                    user_id=target_id,
                    order_id=0,
                    service_type=TRIAL_SERVICE_TYPE,
                    product_id=TRIAL_PRODUCT_ID,
                    product_title=TRIAL_PRODUCT_TITLE,
                    has_expiry=True,
                    expiry_days=TRIAL_ACTIVE_DAYS,
                    service_username=final_username
                )
        except Exception:
            logger.exception(
                "Failed registering trial service for user %s",
                target_id
            )
            # تحویل انجام شده؛ خطای ثبت داخلی نباید باعث ساخت اکانت دوم شود.

        clear_admin_state(
            admin_id
        )

        bot.send_message(
            admin_id,
            f"✅ اکانت تست برای کاربر <code>{target_id}</code> "
            f"(<b>{esc(final_username)}</b>) به‌صورت خودکار ساخته و ارسال شد."
        )

    except EylanPanelError as exc:
        logger.exception(
            "EylanPanel trial fulfillment failed for user %s",
            target_id
        )

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(
                "🔄 تلاش مجدد",
                callback_data=f"wgtrial:send:{target_id}"
            )
        )

        bot.send_message(
            admin_id,
            "❌ ساخت خودکار اکانت تست انجام نشد.\n\n"
            f"<code>{esc(str(exc))}</code>",
            reply_markup=kb
        )

    except Exception as exc:
        logger.exception(
            "Unexpected EylanPanel trial failure for user %s",
            target_id
        )

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(
                "🔄 تلاش مجدد",
                callback_data=f"wgtrial:send:{target_id}"
            )
        )

        bot.send_message(
            admin_id,
            "❌ خطای غیرمنتظره در تحویل اکانت تست.\n\n"
            f"<code>{esc(str(exc))}</code>",
            reply_markup=kb
        )

    return True


def deliver_order_payload(
    admin_id,
    message
):
    state = get_admin_state(
        admin_id
    )

    if not state or state["mode"] != "fulfill":
        return False

    order_id = int(
        state["data"].get(
            "order_id",
            0
        )
    )

    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND assigned_admin_id=?
            AND status='fulfilling'
            """,
            (
                order_id,
                admin_id
            )
        ).fetchone()

    if not order:
        clear_admin_state(
            admin_id
        )

        bot.send_message(
            admin_id,
            "این سفارش دیگر در حالت تحویل نیست."
        )

        return True

    service = service_for_product(
        order["product_id"]
    )

    renewal_for_service_id = (
        order["renewal_for_service"]
        if "renewal_for_service" in order.keys()
        else None
    )

    # ──────────────────────────────────────────────────────────────────────────
    # ChatGPT Personal
    # ──────────────────────────────────────────────────────────────────────────

    if (
        service == "chatgpt"
        and order["product_id"] == "chatgpt-plus-personal"
    ):

        gmail_address = (
            order["gmail_address"] or ""
        )

        delivery_msg = (
            chatgpt_personal_delivery_template(
                gmail_address
            )
        )

        try:

            bot.send_message(
                order["user_id"],
                delivery_msg,
                reply_markup=reply_main_keyboard()
            )

            if renewal_for_service_id:

                renew_service(
                    renewal_for_service_id,
                    order_id
                )

            else:

                create_active_service(
                    user_id=order["user_id"],
                    order_id=order_id,
                    service_type="chatgpt_personal",
                    product_id=order["product_id"],
                    product_title=order["product_title"],
                    gmail_address=gmail_address,
                    has_expiry=True
                )

            complete_order(
                order_id
            )

            clear_admin_state(
                admin_id
            )

            bot.send_message(
                admin_id,
                f"✅ سفارش #{order_id} تکمیل شد و پیام آماده برای مشتری ارسال شد."
            )

        except Exception:
            logger.exception(
                "Failed delivering personal AI order #%s",
                order_id
            )

            bot.send_message(
                admin_id,
                "❌ ارسال سفارش انجام نشد."
            )

        return True

    # ──────────────────────────────────────────────────────────────────────────
    # ChatGPT Shared
    # ──────────────────────────────────────────────────────────────────────────

    if (
        service == "chatgpt"
        and message.content_type == "text"
    ):

        gmail_candidate = (
            message.text or ""
        ).strip()

        if (
            "@" in gmail_candidate
            or gmail_candidate.endswith(".com")
        ):

            with db() as conn:
                conn.execute(
                    """
                    UPDATE orders
                    SET gmail_address=?
                    WHERE id=?
                    """,
                    (
                        gmail_candidate,
                        order_id
                    )
                )

            delivery_msg = (
                chatgpt_shared_delivery_template(
                    gmail_candidate
                )
            )

            try:

                if renewal_for_service_id:

                    renew_service(
                        renewal_for_service_id,
                        order_id
                    )

                    update_service_gmail(
                        renewal_for_service_id,
                        gmail_candidate
                    )

                    svc_id = (
                        renewal_for_service_id
                    )

                else:

                    svc_id = create_active_service(
                        user_id=order["user_id"],
                        order_id=order_id,
                        service_type="chatgpt_shared",
                        product_id=order["product_id"],
                        product_title=order["product_title"],
                        gmail_address=gmail_candidate,
                        has_expiry=True
                    )

                kb = types.InlineKeyboardMarkup(
                    row_width=1
                )

                kb.add(
                    types.InlineKeyboardButton(
                        "📩 دریافت کد از پشتیبانی",
                        callback_data=f"request_code:{svc_id}"
                    )
                )

                bot.send_message(
                    order["user_id"],
                    delivery_msg,
                    reply_markup=kb
                )

                complete_order(
                    order_id
                )

                clear_admin_state(
                    admin_id
                )

                bot.send_message(
                    admin_id,
                    f"✅ سفارش #{order_id} تکمیل شد. جیمیل '{esc(gmail_candidate)}' ثبت شد."
                )

            except Exception:
                logger.exception(
                    "Failed delivering shared AI order #%s",
                    order_id
                )

                bot.send_message(
                    admin_id,
                    "❌ ارسال سفارش انجام نشد."
                )

            return True

    def register_service_or_renew(
        svc_type,
        has_expiry,
        gmail=None
    ):
        if renewal_for_service_id:

            renew_service(
                renewal_for_service_id,
                order_id
            )

        else:

            create_active_service(
                user_id=order["user_id"],
                order_id=order_id,
                service_type=svc_type,
                product_id=order["product_id"],
                product_title=order["product_title"],
                gmail_address=gmail,
                has_expiry=has_expiry,
                service_username=order["service_username"]
            )

    try:

        if message.content_type == "text":

            payload = (
                message.text or ""
            ).strip()

            if (
                service in (
                    "wireguard",
                    "v2ray"
                )
                and is_url_text(payload)
            ):

                delivery_msg = delivery_template(
                    service,
                    payload,
                    order
                )

                bot.send_message(
                    order["user_id"],
                    delivery_msg,
                    reply_markup=reply_main_keyboard()
                )

                register_service_or_renew(
                    service,
                    service == "wireguard"
                )

            else:

                bot.send_message(
                    order["user_id"],
                    "رسید سفارش شما تایید شد✅\n\n"
                    "اطلاعات سفارش شما:"
                )

                bot.copy_message(
                    order["user_id"],
                    message.chat.id,
                    message.message_id
                )

                register_service_or_renew(
                    service,
                    service == "wireguard"
                )

        elif message.content_type in (
            "document",
            "photo",
            "video",
            "audio",
            "voice",
            "animation",
            "sticker"
        ):

            guide_hint = (
                "\nبرای مشاهده آموزش اتصال به قسمت «نحوه اتصال» مراجعه کنید☑️"
                if service in USER_CONNECTION_SERVICES
                else ""
            )

            bot.send_message(
                order["user_id"],
                "رسید سفارش شما تایید شد✅\n\n"
                "فایل/اطلاعات سفارش شما در پیام بعدی ارسال شده است."
                f"{guide_hint}"
            )

            bot.copy_message(
                order["user_id"],
                message.chat.id,
                message.message_id
            )

            register_service_or_renew(
                service,
                service == "wireguard"
            )

        else:

            bot.copy_message(
                order["user_id"],
                message.chat.id,
                message.message_id
            )

        complete_order(
            order_id
        )

        clear_admin_state(
            admin_id
        )

        bot.send_message(
            admin_id,
            f"✅ سفارش #{order_id} برای مشتری ارسال و تکمیل شد."
        )

    except Exception:

        logger.exception(
            "Failed delivering order #%s",
            order_id
        )

        bot.send_message(
            admin_id,
            "❌ ارسال سفارش انجام نشد. چیزی برای مشتری ارسال نشده و سفارش همچنان در حالت تحویل باقی می‌ماند."
        )

    return True


def deliver_wireguard_trial_payload(
    admin_id,
    message
):
    state = get_admin_state(
        admin_id
    )

    if not state or state["mode"] != "wg_trial_send":
        return False

    if message.content_type != "text":

        bot.send_message(
            admin_id,
            "❌ لطفاً فقط لینک پنل کاربری را به صورت متن ارسال کنید.\n"
            "برای لغو، /cancel را بفرستید."
        )

        return True

    target_id = int(
        state["data"].get(
            "user_id",
            0
        )
    )

    username = state["data"].get(
        "username",
        ""
    )

    payload = (
        message.text or ""
    ).strip()

    if not payload:

        bot.send_message(
            admin_id,
            "❌ لینک خالی است. دوباره ارسال کنید."
        )

        return True

    if not is_url_text(payload):

        bot.send_message(
            admin_id,
            "❌ چیزی که فرستادید لینک معتبر نیست.\n"
            "لطفاً فقط لینک پنل کاربری را (بدون متن اضافه) ارسال کنید.\n"
            "برای لغو، /cancel را بفرستید."
        )

        return True

    delivery_msg = wireguard_trial_delivery_template(
        payload,
        username
    )

    try:

        bot.send_message(
            target_id,
            delivery_msg,
            reply_markup=reply_main_keyboard()
        )

    except Exception as exc:

        logger.exception(
            "Failed delivering wireguard trial to user %s",
            target_id
        )

        if (
            "blocked" in str(exc).lower()
            or "chat not found" in str(exc).lower()
        ):

            set_blocked(
                target_id,
                True
            )

        bot.send_message(
            admin_id,
            "❌ ارسال اکانت تست انجام نشد. کاربر ربات را بلاک کرده یا در دسترس نیست."
        )

        return True

    mark_wireguard_trial_delivered(
        target_id,
        admin_id,
        payload
    )

    # اکانت تست حداکثر TRIAL_ACTIVE_DAYS روز فعال می‌ماند.
    try:

        with db() as conn:

            already = conn.execute(
                """
                SELECT id
                FROM active_services
                WHERE user_id=?
                AND service_type=?
                """,
                (
                    target_id,
                    TRIAL_SERVICE_TYPE
                )
            ).fetchone()

        if not already:

            create_active_service(
                user_id=target_id,
                order_id=0,
                service_type=TRIAL_SERVICE_TYPE,
                product_id=TRIAL_PRODUCT_ID,
                product_title=TRIAL_PRODUCT_TITLE,
                has_expiry=True,
                expiry_days=TRIAL_ACTIVE_DAYS,
                service_username=username
            )

    except Exception:

        logger.exception(
            "Failed registering trial service for user %s",
            target_id
        )

    clear_admin_state(
        admin_id
    )

    notify_chat_id = state["data"].get(
        "notify_chat_id"
    )

    notify_message_id = state["data"].get(
        "notify_message_id"
    )

    if notify_chat_id and notify_message_id:

        try:

            bot.edit_message_reply_markup(
                notify_chat_id,
                notify_message_id,
                reply_markup=None
            )

        except Exception:
            pass

    bot.send_message(
        admin_id,
        f"✅ اکانت تست برای کاربر <code>{target_id}</code> "
        f"(<b>{esc(username)}</b>) در قالب پیام آماده ارسال شد."
    )

    return True


def fulfill_personal_ai_direct(
    admin_id,
    order_id
):
    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND status='fulfilling'
            """,
            (order_id,)
        ).fetchone()

    if not order:
        bot.send_message(
            admin_id,
            "سفارش پیدا نشد یا در وضعیت تحویل نیست."
        )
        return

    gmail_address = (
        order["gmail_address"] or ""
    )

    delivery_msg = (
        chatgpt_personal_delivery_template(
            gmail_address
        )
    )

    try:

        bot.send_message(
            order["user_id"],
            delivery_msg,
            reply_markup=reply_main_keyboard()
        )

        if order["renewal_for_service"]:

            renew_service(
                order["renewal_for_service"],
                order_id
            )

        else:

            create_active_service(
                user_id=order["user_id"],
                order_id=order_id,
                service_type="chatgpt_personal",
                product_id=order["product_id"],
                product_title=order["product_title"],
                gmail_address=gmail_address,
                has_expiry=True
            )

        complete_order(
            order_id
        )

        clear_admin_state(
            admin_id
        )

        bot.send_message(
            admin_id,
            f"✅ سفارش #{order_id} تکمیل شد و پیام آماده برای مشتری ارسال شد."
        )

    except Exception:

        logger.exception(
            "Failed delivering personal AI order #%s",
            order_id
        )

        bot.send_message(
            admin_id,
            "❌ ارسال پیام انجام نشد."
        )


def admin_assign_new_gmail(
    admin_id,
    service_id,
    new_gmail
):
    svc = get_active_service_by_id(
        service_id
    )

    if not svc:
        bot.send_message(
            admin_id,
            "سرویس پیدا نشد."
        )
        return

    update_service_gmail(
        service_id,
        new_gmail
    )

    delivery_msg = (
        chatgpt_shared_delivery_template(
            new_gmail
        )
    )

    try:

        kb = types.InlineKeyboardMarkup(
            row_width=1
        )

        kb.add(
            types.InlineKeyboardButton(
                "📩 دریافت کد از پشتیبانی",
                callback_data=f"request_code:{service_id}"
            )
        )

        bot.send_message(
            svc["user_id"],
            delivery_msg,
            reply_markup=kb
        )

        bot.send_message(
            admin_id,
            f"✅ جیمیل جدید '{esc(new_gmail)}' برای سرویس #{service_id} ثبت شد."
        )

    except Exception:

        logger.exception(
            "Failed assigning new gmail for service #%s",
            service_id
        )

        bot.send_message(
            admin_id,
            "❌ ارسال پیام انجام نشد."
        )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN STATISTICS
# ══════════════════════════════════════════════════════════════════════════════

def admin_stats_text():
    now_dt = datetime.now(
        timezone.utc
    )

    today_start = now_dt.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ).isoformat(
        timespec="seconds"
    )

    day_ago = (
        now_dt - timedelta(days=1)
    ).isoformat(
        timespec="seconds"
    )

    week_ago = (
        now_dt - timedelta(days=7)
    ).isoformat(
        timespec="seconds"
    )

    month_ago = (
        now_dt - timedelta(days=30)
    ).isoformat(
        timespec="seconds"
    )

    with db() as conn:

        total_users = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            """
        ).fetchone()[0]

        active_24h = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE updated_at >= ?
            """,
            (day_ago,)
        ).fetchone()[0]

        active_7d = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE updated_at >= ?
            """,
            (week_ago,)
        ).fetchone()[0]

        active_30d = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE updated_at >= ?
            """,
            (month_ago,)
        ).fetchone()[0]

        blocked_users = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE is_blocked=1
            """
        ).fetchone()[0]

        total_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            """
        ).fetchone()[0]

        today_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE created_at >= ?
            """,
            (today_start,)
        ).fetchone()[0]

        completed_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status='completed'
            """
        ).fetchone()[0]

        today_completed = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status='completed'
            AND completed_at >= ?
            """,
            (today_start,)
        ).fetchone()[0]

        pending_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status IN (
                'awaiting_payment',
                'awaiting_receipt',
                'receipt_submitted',
                'paid_pending_fulfillment',
                'fulfilling'
            )
            """
        ).fetchone()[0]

        cancelled_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status='cancelled'
            """
        ).fetchone()[0]

        rejected_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status='rejected'
            """
        ).fetchone()[0]

        total_sales = conn.execute(
            """
            SELECT COALESCE(
                SUM(price),
                0
            )
            FROM orders
            WHERE status='completed'
            """
        ).fetchone()[0]

        today_sales = conn.execute(
            """
            SELECT COALESCE(
                SUM(price),
                0
            )
            FROM orders
            WHERE status='completed'
            AND completed_at >= ?
            """,
            (today_start,)
        ).fetchone()[0]

        total_wallet_balance = conn.execute(
            """
            SELECT COALESCE(
                SUM(balance),
                0
            )
            FROM users
            """
        ).fetchone()[0]

        active_services = conn.execute(
            """
            SELECT COUNT(*)
            FROM active_services
            WHERE is_expired=0
            """
        ).fetchone()[0]

        expired_services = conn.execute(
            """
            SELECT COUNT(*)
            FROM active_services
            WHERE is_expired=1
            """
        ).fetchone()[0]

        expiring_soon = conn.execute(
            """
            SELECT COUNT(*)
            FROM active_services
            WHERE expires_at IS NOT NULL
            AND expires_at >= ?
            AND expires_at <= ?
            AND is_expired=0
            """,
            (
                now_dt.isoformat(
                    timespec="seconds"
                ),
                (
                    now_dt
                    + timedelta(days=3)
                ).isoformat(
                    timespec="seconds"
                )
            )
        ).fetchone()[0]

        top_products = conn.execute(
            """
            SELECT
                product_title,
                COUNT(*) AS total
            FROM orders
            WHERE status='completed'
            GROUP BY product_id, product_title
            ORDER BY total DESC
            LIMIT 5
            """
        ).fetchall()

        status_rows = conn.execute(
            """
            SELECT
                status,
                COUNT(*) AS total
            FROM orders
            GROUP BY status
            ORDER BY total DESC
            """
        ).fetchall()

    status_map = {
        "awaiting_payment": "در انتظار پرداخت",
        "awaiting_receipt": "منتظر فیش",
        "receipt_submitted": "فیش ارسال شده",
        "paid_pending_fulfillment": "آماده تحویل",
        "fulfilling": "در حال تحویل",
        "completed": "تکمیل شده",
        "rejected": "رد شده",
        "cancelled": "لغو شده"
    }

    lines = [
        "📊 <b>آمار کامل ربات</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "👥 <b>کاربران</b>",
        f"👤 کل کاربران: <b>{total_users:,}</b>",
        f"🟢 فعال در ۲۴ ساعت اخیر: <b>{active_24h:,}</b>",
        f"📅 فعال در ۷ روز اخیر: <b>{active_7d:,}</b>",
        f"🗓 فعال در ۳۰ روز اخیر: <b>{active_30d:,}</b>",
        f"🚫 کاربران بلاک‌شده: <b>{blocked_users:,}</b>",
        "",
        "💰 <b>فروش و درآمد</b>",
        f"💵 فروش کل: <b>{money(total_sales)}</b>",
        f"💰 فروش امروز: <b>{money(today_sales)}</b>",
        f"💳 مجموع موجودی کیف پول کاربران: <b>{money(total_wallet_balance)}</b>",
        "",
        "📦 <b>سفارش‌ها</b>",
        f"🧾 کل سفارش‌ها: <b>{total_orders:,}</b>",
        f"🆕 سفارش‌های امروز: <b>{today_orders:,}</b>",
        f"✅ سفارش‌های تکمیل‌شده: <b>{completed_orders:,}</b>",
        f"✅ تکمیل‌شده امروز: <b>{today_completed:,}</b>",
        f"⏳ سفارش‌های باز: <b>{pending_orders:,}</b>",
        f"❌ سفارش‌های لغوشده: <b>{cancelled_orders:,}</b>",
        f"🚫 سفارش‌های ردشده: <b>{rejected_orders:,}</b>",
        "",
        "📦 <b>سرویس‌ها</b>",
        f"🟢 سرویس‌های فعال: <b>{active_services:,}</b>",
        f"⏰ در آستانه انقضا (۳ روز): <b>{expiring_soon:,}</b>",
        f"🔴 سرویس‌های منقضی: <b>{expired_services:,}</b>",
        ""
    ]

    if top_products:

        lines.append(
            "🏆 <b>پرفروش‌ترین محصولات</b>"
        )

        for index, row in enumerate(
            top_products,
            1
        ):
            lines.append(
                f"{index}. "
                f"{esc(row['product_title'])} — "
                f"<b>{row['total']:,}</b> فروش"
            )

        lines.append("")

    if status_rows:

        lines.append(
            "📈 <b>وضعیت سفارش‌ها</b>"
        )

        for row in status_rows:

            label = status_map.get(
                row["status"],
                row["status"]
            )

            lines.append(
                f"• {label}: "
                f"<b>{row['total']:,}</b>"
            )

    lines.extend(
        [
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🕒 "
            f"آخرین بروزرسانی: "
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        ]
    )

    return "\n".join(lines)


def admin_stats_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔄 بروزرسانی آمار",
            callback_data="admin:stats"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📦 مشاهده سفارش‌ها",
            callback_data="admin:orders"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "👥 مشاهده کاربران",
            callback_data="admin:users"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

def admin_panel_keyboard(admin_id=None):
    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.row(
        types.InlineKeyboardButton(
            "📊 آمار ربات",
            callback_data="admin:stats"
        ),
        types.InlineKeyboardButton(
            "📦 سفارش‌ها",
            callback_data="admin:orders"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "👥 کاربران",
            callback_data="admin:users"
        ),
        types.InlineKeyboardButton(
            "📨 ارسال به کاربر",
            callback_data="admin:send_user"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "📢 ارسال همگانی",
            callback_data="admin:broadcast_text"
        ),
        types.InlineKeyboardButton(
            "📣 فوروارد همگانی",
            callback_data="admin:broadcast_forward"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "👛 مدیریت موجودی",
            callback_data="admin:wallet"
        ),
        types.InlineKeyboardButton(
            "🎓 مدیریت نحوه اتصال",
            callback_data="admin:guides"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "📥 دریافت دیتابیس",
            callback_data="admin:db_backup"
        ),
        types.InlineKeyboardButton(
            "📤 ارسال دیتابیس",
            callback_data="admin:db_restore"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "💲 تغییر قیمت سرویس‌ها",
            callback_data="admin:prices"
        ),
        types.InlineKeyboardButton(
            "🔐 عضویت اجباری",
            callback_data="admin:forcejoin"
        )
    )

    maintenance_on = is_maintenance_mode()

    kb.row(
        types.InlineKeyboardButton(
            "🧑‍💼 مدیریت ادمین‌ها",
            callback_data="admin:admins"
        ),
        types.InlineKeyboardButton(
            "🛠 خاموش کردن ربات" if not maintenance_on else "✅ روشن کردن ربات",
            callback_data="admin:maintenance_toggle"
        )
    )

    # 💐 سود اخوان حالا برای همه‌ی ادمین‌ها نمایش داده می‌شود.
    if admin_id is not None and admin_id in ADMIN_IDS:
        kb.row(
            types.InlineKeyboardButton(
                "💐 سود اخوان",
                callback_data="admin:akhavan"
            )
        )

    return kb


def admin_guide_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for service, label in SERVICE_LABELS.items():
        kb.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"guide:service:{service}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


def admin_guide_service_keyboard(service):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "➕ افزودن محتوا",
            callback_data=f"guide:add:{service}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📤 ارسال آموزش به کاربر",
            callback_data=f"guide:send:{service}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "👁 مشاهده آموزش",
            callback_data=f"guide:preview:{service}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 حذف همه محتوا",
            callback_data=f"guide:clear:{service}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت",
            callback_data="admin:guides"
        )
    )

    return kb


# ══════════════════════════════════════════════════════════════════════════════
# USERS / ORDERS ADMIN
# ══════════════════════════════════════════════════════════════════════════════

def list_users_text():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                user_id,
                username,
                first_name,
                balance,
                is_blocked,
                updated_at
            FROM users
            ORDER BY updated_at DESC
            LIMIT 10
            """
        ).fetchall()

    if not rows:
        return "👥 هنوز کاربری ثبت نشده است."

    lines = [
        "👥 <b>۱۰ کاربر اخیر</b>",
        ""
    ]

    for i, r in enumerate(
        rows,
        1
    ):

        username = (
            f"@{r['username']}"
            if r["username"]
            else "بدون یوزرنیم"
        )

        status = (
            "🚫 بلاک"
            if r["is_blocked"]
            else "✅ فعال"
        )

        lines.append(
            f"{i}. <b>{esc(r['first_name'] or 'بدون نام')}</b> | "
            f"{esc(username)}\n"
            f"   ID: <code>{r['user_id']}</code> | "
            f"موجودی: {money(r['balance'])} | {status}"
        )

    return "\n".join(lines)


def orders_text(limit=30):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM orders
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    if not rows:
        return "📦 هیچ سفارشی ثبت نشده است."

    status_map = {
        "awaiting_payment": "در انتظار پرداخت",
        "awaiting_receipt": "منتظر فیش",
        "receipt_submitted": "فیش ارسال شده",
        "paid_pending_fulfillment": "آماده تحویل",
        "fulfilling": "در حال تحویل",
        "completed": "تکمیل شده",
        "rejected": "رد شده",
        "cancelled": "لغو شده"
    }

    lines = [
        "📦 <b>آخرین سفارش‌ها</b>",
        ""
    ]

    for r in rows:

        lines.append(
            f"#{r['id']} | "
            f"{r['user_id']} | "
            f"{esc(r['product_title'])}\n"
            f"💰 {money(r['price'])} | "
            f"{status_map.get(r['status'], r['status'])}"
        )

    return "\n".join(lines)


def active_user_ids():
    with db() as conn:
        return [
            int(r["user_id"])
            for r in conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE is_blocked=0
                ORDER BY updated_at DESC
                """
            ).fetchall()
        ]


# ══════════════════════════════════════════════════════════════════════════════
# BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

def broadcast_copy(
    admin_id,
    message
):
    user_ids = active_user_ids()

    success = 0
    blocked = 0
    failed = 0

    bot.send_message(
        admin_id,
        f"📣 ارسال همگانی شروع شد. تعداد گیرندگان: {len(user_ids)}"
    )

    for user_id in user_ids:

        try:

            bot.copy_message(
                user_id,
                message.chat.id,
                message.message_id
            )

            success += 1

        except Exception as exc:

            text = str(exc).lower()

            if (
                "blocked" in text
                or "chat not found" in text
                or "user is deactivated" in text
            ):

                set_blocked(
                    user_id,
                    True
                )

                blocked += 1

            else:
                failed += 1

        time.sleep(
            0.04
        )

    bot.send_message(
        admin_id,
        "✅ فوروارد همگانی تمام شد.\n"
        f"موفق: {success}\n"
        f"بلاک/غیرفعال: {blocked}\n"
        f"خطا: {failed}"
    )


def broadcast_text(
    admin_id,
    message
):
    user_ids = active_user_ids()

    success = 0
    blocked = 0
    failed = 0

    bot.send_message(
        admin_id,
        f"📢 ارسال همگانی شروع شد. تعداد گیرندگان: {len(user_ids)}"
    )

    for user_id in user_ids:

        try:

            bot.send_message(
                user_id,
                message.text,
                parse_mode=None
            )

            success += 1

        except Exception as exc:

            text = str(exc).lower()

            if (
                "blocked" in text
                or "chat not found" in text
                or "user is deactivated" in text
            ):

                set_blocked(
                    user_id,
                    True
                )

                blocked += 1

            else:
                failed += 1

        time.sleep(
            0.04
        )

    bot.send_message(
        admin_id,
        "✅ ارسال همگانی تمام شد.\n"
        f"موفق: {success}\n"
        f"بلاک/غیرفعال: {blocked}\n"
        f"خطا: {failed}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION GUIDE
# ══════════════════════════════════════════════════════════════════════════════

def connection_admin_content_count(
    service
):
    with db() as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM connection_content
                WHERE service=?
                """,
                (service,)
            ).fetchone()[0]
        )


def store_connection_content(
    admin_id,
    message,
    service
):
    supported = {
        "text",
        "photo",
        "video",
        "document",
        "audio",
        "voice",
        "animation",
        "sticker"
    }

    if message.content_type not in supported:
        bot.send_message(
            admin_id,
            "❌ این نوع پیام برای آموزش پشتیبانی نمی‌شود."
        )
        return

    file_id = None
    text_content = None
    caption = None

    if message.content_type == "text":

        text_content = message.text

    elif message.content_type == "photo":

        file_id = message.photo[-1].file_id
        caption = message.caption

    elif message.content_type == "video":

        file_id = message.video.file_id
        caption = message.caption

    elif message.content_type == "document":

        file_id = message.document.file_id
        caption = message.caption

    elif message.content_type == "audio":

        file_id = message.audio.file_id
        caption = message.caption

    elif message.content_type == "voice":

        file_id = message.voice.file_id
        caption = message.caption

    elif message.content_type == "animation":

        file_id = message.animation.file_id
        caption = message.caption

    elif message.content_type == "sticker":

        file_id = message.sticker.file_id

    sort_order = (
        connection_admin_content_count(
            service
        )
        + 1
    )

    with db() as conn:
        conn.execute(
            """
            INSERT INTO connection_content(
                service,
                content_type,
                file_id,
                text_content,
                caption,
                sort_order,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service,
                message.content_type,
                file_id,
                text_content,
                caption,
                sort_order,
                now()
            )
        )

    bot.send_message(
        admin_id,
        f"✅ محتوای شماره {sort_order} برای "
        f"{SERVICE_LABELS[service]} ذخیره شد.\n"
        "محتوای بعدی را بفرستید یا «اتمام افزودن» را بزنید.",
        reply_markup=guide_upload_keyboard()
    )


def guide_upload_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "✅ اتمام افزودن",
            callback_data="guide:add_done"
        )
    )

    return kb


def send_connection_content(
    chat_id,
    service
):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM connection_content
            WHERE service=?
            ORDER BY sort_order, id
            """,
            (service,)
        ).fetchall()

    label = SERVICE_LABELS.get(
        service,
        service
    )

    if not rows:

        bot.send_message(
            chat_id,
            f"🎓 برای {label} هنوز آموزشی ثبت نشده است.",
            reply_markup=reply_back_keyboard()
        )

        return False

    bot.send_message(
        chat_id,
        f"🎓 <b>آموزش {label}</b>",
        reply_markup=reply_back_keyboard()
    )

    for row in rows:

        try:

            ctype = row["content_type"]

            if ctype == "text":

                bot.send_message(
                    chat_id,
                    row["text_content"] or "",
                    parse_mode=None
                )

            elif ctype == "photo":

                bot.send_photo(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "video":

                bot.send_video(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "document":

                bot.send_document(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "audio":

                bot.send_audio(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "voice":

                bot.send_voice(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "animation":

                bot.send_animation(
                    chat_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "sticker":

                bot.send_sticker(
                    chat_id,
                    row["file_id"]
                )

        except Exception:

            logger.exception(
                "Failed sending connection item id=%s",
                row["id"]
            )

    return True


def preview_connection_content(
    admin_id,
    service
):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM connection_content
            WHERE service=?
            ORDER BY sort_order, id
            """,
            (service,)
        ).fetchall()

    if not rows:

        bot.send_message(
            admin_id,
            "محتوایی برای نمایش وجود ندارد."
        )

        return

    bot.send_message(
        admin_id,
        f"👁 <b>پیش‌نمایش آموزش {SERVICE_LABELS[service]}</b>"
    )

    for row in rows:

        ctype = row["content_type"]

        try:

            if ctype == "text":

                bot.send_message(
                    admin_id,
                    row["text_content"] or "",
                    parse_mode=None
                )

            elif ctype == "photo":

                bot.send_photo(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "video":

                bot.send_video(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "document":

                bot.send_document(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "audio":

                bot.send_audio(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "voice":

                bot.send_voice(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "animation":

                bot.send_animation(
                    admin_id,
                    row["file_id"],
                    caption=row["caption"] or None
                )

            elif ctype == "sticker":

                bot.send_sticker(
                    admin_id,
                    row["file_id"]
                )

        except Exception:

            logger.exception(
                "Failed preview content id=%s",
                row["id"]
            )


def clear_connection_content(
    service
):
    with db() as conn:
        conn.execute(
            """
            DELETE FROM connection_content
            WHERE service=?
            """,
            (service,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# LIVE SERVER STATUS
# ══════════════════════════════════════════════════════════════════════════════

# V2Ray از پنل جداگانه و دستی تحویل می‌شود؛ پس وضعیت لحظه‌ای از EylanPanel ندارد.
SERVER_SERVICE_TYPES = {"wireguard", TRIAL_SERVICE_TYPE}

def _first_present(mapping, *keys):
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _parse_remote_datetime(value):
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    date_only = False
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(raw, fmt)
                date_only = fmt == "%Y-%m-%d"
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if date_only:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
    return parsed.astimezone(timezone.utc)


def _format_gb(bytes_value):
    gb = max(0.0, float(bytes_value or 0)) / 1_000_000_000
    return f"{gb:.2f}".rstrip("0").rstrip(".") + " GB" if gb else "0 GB"


def _format_days_remaining(expiry_value):
    expiry = _parse_remote_datetime(expiry_value)
    if not expiry:
        return None
    seconds = (expiry - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return "منقضی شده"
    days = int(seconds // 86400)
    if days <= 0:
        return "کمتر از ۱ روز"
    return f"{days} روز"


def _status_from_panel_record(record):
    limit = _first_present(record, "data_limit", "traffic_limit", "quota")
    unit = str(_first_present(record, "data_limit_unit", "traffic_unit") or "GB").upper()
    used = _first_present(record, "total_traffic_bytes", "used_traffic_bytes", "total_traffic")
    if used is None:
        dl = record.get("download_bytes")
        ul = record.get("upload_bytes")
        if dl is not None or ul is not None:
            try:
                used = float(dl or 0) + float(ul or 0)
            except (TypeError, ValueError):
                used = 0
    try:
        used = float(used or 0)
    except (TypeError, ValueError):
        used = 0.0

    try:
        limit_num = float(limit) if limit is not None else 0.0
    except (TypeError, ValueError):
        limit_num = 0.0

    multiplier = 1_000_000 if unit == "MB" else 1_000_000_000
    limit_bytes = limit_num * multiplier
    remaining_bytes = max(0.0, limit_bytes - used) if limit_num > 0 else None

    expiry = _first_present(
        record,
        "expiry_date",
        "expiry_date_str",
        "expires_at",
        "expiry",
        "expiry_datetime",
    )
    activation_type = _first_present(record, "activation_type")
    is_active = record.get("is_active")
    is_online = record.get("is_online")
    return {
        "limit_bytes": limit_bytes if limit_num > 0 else None,
        "used_bytes": used,
        "remaining_bytes": remaining_bytes,
        "expiry": expiry,
        "days_remaining": _format_days_remaining(expiry),
        "activation_type": activation_type,
        "is_active": is_active,
        "is_online": is_online,
        "raw": record,
    }


def get_live_server_status(svc):
    username = (service_field(svc, "service_username") or "").strip()
    if not username:
        raise EylanPanelError("برای این سرویس نام کاربری پنل ثبت نشده است.")

    client = get_eylan_client()
    record = client.get_user_status(username)
    status = _status_from_panel_record(record)

    # The API docs expose usage and expiry, but older panel builds may omit one
    # of those fields from the single-user response. Fill any missing value
    # from the shop plan/local service only as a graceful fallback.
    try:
        plan = eylan_plan_for_product(svc["product_id"])
    except Exception:
        plan = {}

    if status["limit_bytes"] is None:
        fallback_limit = plan.get("data_limit")
        if svc.get("product_id") == TRIAL_PRODUCT_ID and EYLAN_TRIAL_DATA_LIMIT is not None:
            fallback_limit = EYLAN_TRIAL_DATA_LIMIT
        if fallback_limit:
            unit = str(plan.get("data_limit_unit", "GB")).upper()
            multiplier = 1_000_000 if unit == "MB" else 1_000_000_000
            status["limit_bytes"] = float(fallback_limit) * multiplier
            status["remaining_bytes"] = max(0.0, status["limit_bytes"] - status["used_bytes"])

    if status["days_remaining"] is None and svc["expires_at"]:
        status["expiry"] = svc["expires_at"]
        status["days_remaining"] = _format_days_remaining(svc["expires_at"])

    if status["days_remaining"] is None and svc["service_type"] == "v2ray":
        status["days_remaining"] = "نامحدود ♾️"

    status["username"] = username
    status["product_title"] = svc["product_title"]
    status["service_type"] = svc["service_type"]
    return status


def server_status_text(svc, status):
    label = service_type_label(svc)
    lines = [
        "📊 <b>وضعیت سرور</b>",
        f"🖥 سرویس: <b>{esc(label)}</b>",
        f"📦 محصول: <b>{esc(status['product_title'])}</b>",
        f"👤 نام کاربری: <b>{esc(status['username'])}</b>",
    ]

    if status["limit_bytes"] is None:
        lines.append("📦 حجم باقی‌مانده: <b>نامحدود ♾️</b>")
    else:
        lines.append(f"📦 حجم باقی‌مانده: <b>{_format_gb(status['remaining_bytes'])}</b>")
        lines.append(f"📉 حجم مصرف‌شده: <b>{_format_gb(status['used_bytes'])}</b>")

    days = status.get("days_remaining")
    if days:
        lines.append(f"⏳ زمان باقی‌مانده: <b>{esc(days)}</b>")
    elif str(status.get("activation_type") or "").lower() == "flexible_days":
        lines.append("⏳ زمان باقی‌مانده: <b>در انتظار اولین اتصال</b>")
    else:
        lines.append("⏳ زمان باقی‌مانده: <b>نامشخص</b>")

    is_active = status.get("is_active")
    if str(status.get("activation_type") or "").lower() == "flexible_days" and not status.get("expiry") and is_active is False:
        state_text = "🟡 در انتظار اولین اتصال"
    elif is_active is True:
        state_text = "🟢 فعال"
    elif is_active is False:
        state_text = "🔴 غیرفعال / منقضی"
    else:
        state_text = "ℹ️ وضعیت پنل دریافت شد"
    lines.append(f"📡 وضعیت: <b>{state_text}</b>")

    if status.get("is_online") is True:
        lines.append("🌐 اتصال فعلی: <b>آنلاین</b>")
    elif status.get("is_online") is False:
        lines.append("🌐 اتصال فعلی: <b>آفلاین</b>")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MY SERVICES
# ══════════════════════════════════════════════════════════════════════════════

SERVICE_TYPE_LABELS = {
    "wireguard": "🎮 وایرساک / وایرگارد",
    "v2ray": "🛜 V2Ray",
    "chatgpt_shared": "🤖 ChatGPT اشتراکی",
    "chatgpt_personal": "⭐ ChatGPT Plus شخصی",
    "chatgpt": "🤖 ChatGPT",
    TRIAL_SERVICE_TYPE: "🧪 اکانت تست وایرساک / وایرگارد"
}


def service_type_label(svc):
    return SERVICE_TYPE_LABELS.get(
        svc["service_type"],
        svc["product_title"]
    )


def service_field(
    svc,
    field
):
    """
    خواندن امن یک ستون از رکورد سرویس (برای دیتابیس‌های قدیمی‌تر)
    """

    try:
        if field in svc.keys():
            return svc[field]
    except Exception:
        pass

    return None


def format_service_card(svc):
    stype = svc["service_type"]

    label = service_type_label(
        svc
    )

    status_tag = (
        "❌ منقضی"
        if svc["is_expired"]
        else "✅ فعال"
    )

    lines = [
        f"<b>{label}</b> {status_tag}",
        f"🆔 شناسه سرویس: <b>#{svc['id']}</b>"
    ]

    if stype == TRIAL_SERVICE_TYPE:
        lines.append(
            "🎁 نوع: <b>اکانت تست رایگان</b>"
        )
    else:
        lines.append(
            f"📦 {esc(svc['product_title'])}"
        )

    username = service_field(
        svc,
        "service_username"
    )

    if username:
        lines.append(
            f"👤 نام کاربری: <b>{esc(username)}</b>"
        )

    lines.append(
        f"📅 شروع: {svc['started_at'][:10]}"
    )

    if svc["expires_at"]:
        lines.append(
            f"⏳ انقضا: {svc['expires_at'][:10]}"
        )
    else:
        lines.append(
            "⏳ انقضا: نامحدود ♾️"
        )

    if svc["gmail_address"]:
        lines.append(
            f"📧 جیمیل: "
            f"<b>{esc(svc['gmail_address'])}</b>"
        )

    if stype == TRIAL_SERVICE_TYPE:
        lines.append(
            "\n" + TRIAL_IMPORTANT_NOTES
        )

    return "\n".join(lines)


def my_services_keyboard(
    services
):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for svc in services:

        label = service_type_label(
            svc
        )

        expired_tag = (
            " ❌ منقضی"
            if svc["is_expired"]
            else " ✅"
        )

        kb.add(
            types.InlineKeyboardButton(
                f"{label}{expired_tag}",
                callback_data=f"my_service:{svc['id']}"
            )
        )

    return kb


def service_detail_keyboard(
    svc
):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    stype = svc["service_type"]

    if stype in SERVER_SERVICE_TYPES:
        kb.add(
            types.InlineKeyboardButton(
                "📊 وضعیت سرور",
                callback_data=f"server_status:{svc['id']}"
            )
        )

    if stype == TRIAL_SERVICE_TYPE:

        kb.add(
            types.InlineKeyboardButton(
                "🛒 خرید سرویس کامل",
                callback_data="srv:wireguard"
            )
        )

    elif stype in (
        "wireguard",
        "chatgpt_shared",
        "chatgpt_personal",
        "chatgpt"
    ):

        kb.add(
            types.InlineKeyboardButton(
                "🔄 تمدید سرویس",
                callback_data=f"renew_service:{svc['id']}"
            )
        )

    if stype in (
        "chatgpt_shared",
        "chatgpt"
    ):

        kb.add(
            types.InlineKeyboardButton(
                "📩 ارسال پیام به پشتیبانی",
                callback_data=f"support_chat:{svc['id']}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "📋 تاریخچه سرویس‌ها",
            callback_data=f"service_history:{svc['user_id']}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به سرویس‌ها",
            callback_data="my_services_list"
        )
    )

    return kb


def show_server_status_menu(chat_id, user_id):
    services = [
        svc for svc in get_active_services(user_id)
        if svc["service_type"] in SERVER_SERVICE_TYPES
    ]

    if not services:
        bot.send_message(
            chat_id,
            "📊 <b>وضعیت سرور</b>\n\nشما هنوز سرویس وایرساک / WireGuard فعالی ندارید.",
            reply_markup=reply_main_keyboard()
        )
        return

    if len(services) == 1:
        try:
            status = get_live_server_status(services[0])
            bot.send_message(
                chat_id,
                server_status_text(services[0], status),
                reply_markup=service_detail_keyboard(services[0])
            )
        except EylanPanelError as exc:
            logger.exception("Failed to fetch server status for service %s", services[0]["id"])
            bot.send_message(
                chat_id,
                "⚠️ در حال حاضر دریافت وضعیت لحظه‌ای سرور از پنل ممکن نشد.\n\n"
                f"<code>{esc(str(exc))}</code>\n\n"
                + format_service_card(services[0]),
                reply_markup=service_detail_keyboard(services[0])
            )
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for svc in services:
        kb.add(
            types.InlineKeyboardButton(
                f"{service_type_label(svc)} — #{svc['id']}",
                callback_data=f"server_status:{svc['id']}"
            )
        )
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="my_services_list"))

    bot.send_message(
        chat_id,
        "📊 <b>وضعیت سرور</b>\n\nسرویسی که می‌خواهید وضعیت لحظه‌ای آن نمایش داده شود را انتخاب کنید:",
        reply_markup=kb
    )


def show_my_services(
    chat_id,
    user_id
):
    services = get_active_services(
        user_id
    )

    if not services:

        bot.send_message(
            chat_id,
            "📦 <b>سرویس های من</b>\n\n"
            "شما هنوز سرویس فعالی ندارید.\n"
            "برای خرید از منوی اصلی اقدام کنید.",
            reply_markup=reply_main_keyboard()
        )

        return

    save_nav(
        user_id,
        "my_services",
        "home",
        {}
    )

    bot.send_message(
        chat_id,
        "📦 <b>سرویس های من</b>\n\n"
        "سرویس موردنظر را انتخاب کنید:",
        reply_markup=my_services_keyboard(
            services
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
# SUPPORT ADMIN
# ══════════════════════════════════════════════════════════════════════════════

def admin_support_info_keyboard(
    chat_id,
    service_id
):
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "💬 ارسال پیام به کاربر",
            callback_data=f"admin_reply:{chat_id}"
        )
    )

    if service_id:

        kb.add(
            types.InlineKeyboardButton(
                "📧 تخصیص اکانت جدید",
                callback_data=f"admin_new_gmail:{service_id}"
            )
        )

        kb.add(
            types.InlineKeyboardButton(
                "📋 تاریخچه سرویس",
                callback_data=f"admin_svc_history:{service_id}"
            )
        )

    return kb


def _build_admin_support_header(
    user_id,
    user_row,
    svc
):
    username = (
        f"@{user_row['username']}"
        if user_row
        and user_row["username"]
        else "بدون یوزرنیم"
    )

    name = (
        user_row["first_name"]
        if user_row
        else "نامشخص"
    )

    lines = [
        "📨 <b>پیام پشتیبانی جدید</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"👤 نام: <b>{esc(name)}</b>",
        f"🔗 یوزرنیم: {esc(username)}",
        f"🆔 شناسه: <code>{user_id}</code>"
    ]

    if svc:

        stype_label = {
            "wireguard": "🎮 وایرساک",
            "v2ray": "🛜 V2Ray",
            "chatgpt_shared": "🤖 ChatGPT اشتراکی",
            "chatgpt_personal": "⭐ ChatGPT Plus شخصی"
        }.get(
            svc["service_type"],
            svc["service_type"]
        )

        lines.append(
            f"📦 سرویس فعال: <b>{stype_label}</b>"
        )

        lines.append(
            f"🛍 محصول: {esc(svc['product_title'])}"
        )

        if svc["expires_at"]:

            lines.append(
                f"⏳ انقضا: {svc['expires_at'][:10]}"
            )

        if svc["gmail_address"]:

            lines.append(
                "📧 جیمیل فعلی: "
                f"<b>{esc(svc['gmail_address'])}</b>"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📝 <b>پیام‌های کاربر:</b>"
    )

    return "\n".join(lines)


def _extract_message_data(
    message
):
    ctype = message.content_type

    if ctype == "text":

        return {
            "content_type": "text",
            "text": message.text
        }

    elif ctype == "photo":

        return {
            "content_type": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": message.caption
        }

    elif ctype == "document":

        return {
            "content_type": "document",
            "file_id": message.document.file_id,
            "caption": message.caption
        }

    elif ctype == "video":

        return {
            "content_type": "video",
            "file_id": message.video.file_id,
            "caption": message.caption
        }

    elif ctype == "voice":

        return {
            "content_type": "voice",
            "file_id": message.voice.file_id,
            "caption": message.caption
        }

    elif ctype == "audio":

        return {
            "content_type": "audio",
            "file_id": message.audio.file_id,
            "caption": message.caption
        }

    elif ctype == "animation":

        return {
            "content_type": "animation",
            "file_id": message.animation.file_id,
            "caption": message.caption
        }

    elif ctype == "sticker":

        return {
            "content_type": "sticker",
            "file_id": message.sticker.file_id
        }

    return None


def _replay_message_to_chat(
    target_chat_id,
    msg_data
):
    ctype = msg_data.get(
        "content_type"
    )

    if ctype == "text":

        bot.send_message(
            target_chat_id,
            msg_data.get("text") or "",
            parse_mode=None
        )

    elif ctype == "photo":

        bot.send_photo(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "document":

        bot.send_document(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "video":

        bot.send_video(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "voice":

        bot.send_voice(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "audio":

        bot.send_audio(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "animation":

        bot.send_animation(
            target_chat_id,
            msg_data["file_id"],
            caption=msg_data.get("caption") or None
        )

    elif ctype == "sticker":

        bot.send_sticker(
            target_chat_id,
            msg_data["file_id"]
        )


def _start_user_support_chat(
    chat_id,
    user_id,
    service_id
):
    existing = get_open_support_chat(
        user_id
    )

    if existing:

        support_chat_id = existing["id"]

    else:

        support_chat_id = create_support_chat(
            user_id,
            service_id=service_id
        )

    save_nav(
        user_id,
        "support_sending",
        "my_services",
        {
            "support_chat_id": support_chat_id,
            "service_id": service_id
        }
    )

    clear_draft_messages(
        user_id
    )

    save_draft_messages(
        user_id,
        support_chat_id,
        []
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "✅ ارسال به پشتیبانی",
            callback_data=f"send_support_msgs:{support_chat_id}"
        )
    )

    bot.send_message(
        chat_id,
        "📨 <b>ارسال پیام به پشتیبانی</b>\n\n"
        "پیام‌های خود (متن، عکس یا هردو) را پشت سر هم ارسال کنید.\n"
        "سپس روی دکمه «ارسال به پشتیبانی» بزنید:",
        reply_markup=kb
    )


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(
    commands=["start", "menu"]
)
def start(message):
    ensure_user(
        message.from_user
    )

    if not ensure_joined(message):
        return

    send_home(
        message.chat.id,
        message.from_user.id,
        greeting=True
    )


@bot.message_handler(
    commands=["admin"]
)
def admin_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(
        message.from_user.id
    )

    bot.send_message(
        message.chat.id,
        "🛠 <b>پنل مدیریت</b>\n\n"
        "بخش موردنظر را انتخاب کنید:",
        reply_markup=admin_panel_keyboard(message.from_user.id)
    )


@bot.message_handler(
    commands=["stats"]
)
def stats_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:

        bot.send_message(
            message.chat.id,
            admin_stats_text(),
            reply_markup=admin_stats_keyboard()
        )

    except Exception:

        logger.exception(
            "Failed sending admin stats"
        )

        bot.send_message(
            message.chat.id,
            "❌ دریافت آمار با خطا مواجه شد."
        )


@bot.message_handler(
    commands=["balance"]
)
def balance_command(message):
    ensure_user(
        message.from_user
    )

    if not ensure_joined(message):
        return

    bot.send_message(
        message.chat.id,
        f"💰 موجودی شما: "
        f"<b>{money(get_balance(message.from_user.id))}</b>",
        reply_markup=reply_back_keyboard()
    )


@bot.message_handler(
    commands=["orders"]
)
def orders_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    bot.send_message(
        message.chat.id,
        orders_text()
    )


# ══════════════════════════════════════════════════════════════════════════════
# JOIN CALLBACK
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "check_join"
)
def cb_check_join(call):
    if is_member(
        call.from_user.id
    ):

        bot.answer_callback_query(
            call.id,
            "عضویت شما تأیید شد ✅"
        )

        try:
            bot.delete_message(
                call.message.chat.id,
                call.message.message_id
            )
        except Exception:
            pass

        send_home(
            call.message.chat.id,
            call.from_user.id,
            greeting=False
        )

    else:

        bot.answer_callback_query(
            call.id,
            "هنوز عضویت شما در همه کانال‌ها تأیید نشده است.",
            show_alert=True
        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN MENU CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "cat:server"
)
def cb_cat_server(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_server_menu(
        call.message.chat.id,
        call.from_user.id
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "cat:ai"
)
def cb_cat_ai(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_ai_menu(
        call.message.chat.id,
        call.from_user.id
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "srv:wireguard"
)
def cb_wireguard(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را تکمیل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_service_products(
        call,
        "wireguard"
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "srv:v2ray"
)
def cb_v2ray(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را تکمیل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_service_products(
        call,
        "v2ray"
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "wg_trial_start"
)
def cb_wg_trial_start(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را تکمیل کنید.",
            show_alert=True
        )

        return

    if has_wireguard_trial(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "❌ شما قبلاً از اکانت تست وایرساک استفاده کرده‌اید. "
            "هر اکانت تلگرام فقط یک‌بار می‌تواند اکانت تست دریافت کند.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "wireguard_trial_username",
        "wireguard",
        {}
    )

    bot.send_message(
        call.message.chat.id,
        "🧪 <b>اکانت تست وایرساک</b>\n\n"
        "لطفاً یک نام کاربری برای اکانت تست ارسال کنید:",
        reply_markup=reply_back_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "ai:chatgpt"
)
def cb_chatgpt(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_chatgpt(
        call
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "ai:personal"
)
def cb_ai_personal(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_personal_ai(
        call
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "ai:shared"
)
def cb_ai_shared(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    show_shared_ai(
        call
    )


# ══════════════════════════════════════════════════════════════════════════════
# PERSONAL AI
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "personal_ai_start:"
    )
)
def cb_personal_ai_start(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را تکمیل کنید.",
            show_alert=True
        )

        return

    product_id = call.data.split(
        ":",
        1
    )[1]

    product = PRODUCT_INDEX.get(
        product_id
    )

    if not product:

        bot.answer_callback_query(
            call.id,
            "محصول پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "personal_ai_gmail",
        "ai_personal",
        {
            "product_id": product_id
        }
    )

    bot.send_message(
        call.message.chat.id,
        "📧 <b>جیمیل شما</b>\n\n"
        "جیمیلی که می‌خواهید بر روی آن اکانت Plus فعال شود را ارسال کنید:",
        reply_markup=reply_back_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE INFO
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "service_info:"
    )
)
def cb_service_info(call):
    kind = call.data.split(
        ":",
        1
    )[1]

    bot.answer_callback_query(
        call.id
    )

    if kind == "wireguard":

        text = (
            "ℹ️ <b>اطلاعات سرویس وایرساک / وایرگارد</b>\n\n"
            "🎮 مناسب استفاده گیمینگ\n"
            "⏱ اعتبار بسته‌ها: یک ماهه\n"
            "📦 حجم‌ها: ۵۰، ۱۰۰ و ۲۰۰ گیگابایت\n\n"
            "بعد از تأیید پرداخت، لینک پنل یا فایل تنظیمات از طرف ادمین برای شما ارسال می‌شود."
        )

        service = "wireguard"

    else:

        text = (
            "ℹ️ <b>اطلاعات سرویس V2Ray مولتی‌لوکیشن</b>\n\n"
            "⏱ زمان: نامحدود ♾️\n"
            "📦 حجم‌ها از ۱۵ تا ۲۰۰ گیگابایت\n\n"
            "بعد از تأیید پرداخت، لینک ساب یا فایل تنظیمات از طرف ادمین برای شما ارسال می‌شود."
        )

        service = "v2ray"

    save_nav(
        call.from_user.id,
        "service_info",
        service,
        {
            "service": service
        }
    )

    edit_or_send(
        call,
        text,
        bottom_keyboard=reply_back_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT INFO / BUY
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith("info:")
)
def cb_info(call):
    product_id = call.data.split(
        ":",
        1
    )[1]

    product = PRODUCT_INDEX.get(
        product_id
    )

    if not product:

        bot.answer_callback_query(
            call.id,
            "محصول پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "product_info",
        product_back_state(product_id),
        {
            "product_id": product_id
        }
    )

    text = (
        "ℹ️ <b>اطلاعات سرویس</b>\n\n"
        f"📌 {esc(product['title'])}\n"
        f"⏱ {esc(product.get('plan_title', '-'))}\n"
        f"📦 {esc(product.get('volume', '-'))}\n\n"
        f"{esc(product.get('info', ''))}\n\n"
        f"💰 قیمت: <b>{money(product['price'])}</b>"
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🛒 خرید",
            callback_data=f"buy:{product_id}"
        )
    )

    edit_or_send(
        call,
        text,
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("buy:")
)
def cb_buy(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت کانال‌ها را تکمیل کنید.",
            show_alert=True
        )

        return

    product_id = call.data.split(
        ":",
        1
    )[1]

    product = PRODUCT_INDEX.get(
        product_id
    )

    if not product:

        bot.answer_callback_query(
            call.id,
            "این محصول دیگر موجود نیست.",
            show_alert=True
        )

        return

    if product_id == "chatgpt-plus-personal":

        bot.answer_callback_query(
            call.id
        )

        save_nav(
            call.from_user.id,
            "personal_ai_gmail",
            "ai_personal",
            {
                "product_id": product_id
            }
        )

        bot.send_message(
            call.message.chat.id,
            "📧 <b>جیمیل شما</b>\n\n"
            "جیمیلی که می‌خواهید بر روی آن اکانت Plus فعال شود را ارسال کنید:",
            reply_markup=reply_back_keyboard()
        )

        return

    with db() as conn:
        existing = conn.execute(
            """
            SELECT id, status
            FROM orders
            WHERE user_id=?
            AND product_id=?
            AND status IN (
                'awaiting_payment',
                'awaiting_receipt',
                'receipt_submitted',
                'paid_pending_fulfillment',
                'fulfilling'
            )
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                call.from_user.id,
                product_id
            )
        ).fetchone()

    if existing:

        bot.answer_callback_query(
            call.id,
            f"برای این محصول سفارش باز #{existing['id']} دارید.",
            show_alert=True
        )

        return

    try:

        order_id = create_order(
            call.from_user.id,
            product
        )

    except Exception:

        logger.exception(
            "Failed creating order"
        )

        bot.answer_callback_query(
            call.id,
            "ثبت سفارش با خطا مواجه شد.",
            show_alert=True
        )

        return

    save_nav(
        call.from_user.id,
        "invoice",
        product_back_state(product_id),
        {
            "order_id": order_id,
            "product_id": product_id
        }
    )

    bot.answer_callback_query(
        call.id,
        "سفارش ثبت شد ✅"
    )

    bot.send_message(
        call.message.chat.id,
        invoice_text(
            order_id,
            product
        ),
        reply_markup=invoice_keyboard(
            order_id
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
# COUPON / PAYMENT
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith("coupon:")
)
def cb_coupon(call):
    bot.answer_callback_query(
        call.id,
        "در حال حاضر امکان ثبت کد تخفیف فعال نشده است.",
        show_alert=True
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("pay:")
)
def cb_pay(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را تکمیل کنید.",
            show_alert=True
        )

        return

    try:

        order_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "شناسه سفارش نامعتبر است.",
            show_alert=True
        )

        return

    with db() as conn:
        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND user_id=?
            """,
            (
                order_id,
                call.from_user.id
            )
        ).fetchone()

    if not order:

        bot.answer_callback_query(
            call.id,
            "سفارش پیدا نشد.",
            show_alert=True
        )

        return

    if order["status"] == "receipt_submitted":

        bot.answer_callback_query(
            call.id,
            "فیش شما در حال بررسی است.",
            show_alert=True
        )

        return

    if order["status"] == "paid_pending_fulfillment":

        bot.answer_callback_query(
            call.id,
            "پرداخت این سفارش قبلاً انجام شده است.",
            show_alert=True
        )

        return

    if order["status"] != "awaiting_payment":

        bot.answer_callback_query(
            call.id,
            "این سفارش در وضعیت قابل پرداخت نیست.",
            show_alert=True
        )

        return

    required = int(
        order["cash_required"]
    )

    service = service_for_product(
        order["product_id"]
    )

    product = PRODUCT_INDEX.get(
        order["product_id"]
    ) or {
        "title": order["product_title"],
        "price": order["price"]
    }

    # ── موجودی کیف پول کافی است: بدون فیش ─────────────────────────────────────
    if required <= 0:

        # برای وایرساک / V2Ray هنوز به نام کاربری نیاز داریم.
        if (
            service in (
                "wireguard",
                "v2ray"
            )
            and not order["service_username"]
        ):

            bot.answer_callback_query(
                call.id
            )

            save_nav(
                call.from_user.id,
                "awaiting_service_username",
                "invoice",
                {
                    "order_id": order_id,
                    "wallet_only": True
                }
            )

            send_service_username_prompt(
                call.message.chat.id,
                call.from_user.id,
                order,
                intro=(
                    "🎉 <b>موجودی کیف پول شما برای این خرید کافی است؛ "
                    "نیازی به ارسال فیش نیست.</b>\n\n"
                )
            )

            return

        if not finalize_wallet_only_order(
            order_id,
            call.message.chat.id
        ):

            bot.answer_callback_query(
                call.id,
                "این سفارش قبلاً تغییر کرده است.",
                show_alert=True
            )

            return

        bot.answer_callback_query(
            call.id,
            "پرداخت از کیف پول انجام شد ✅"
        )

        save_nav(
            call.from_user.id,
            "home",
            None,
            {}
        )

        return

    # ── پرداخت نقدی (با یا بدون کیف پول) ──────────────────────────────────────
    with db() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status='awaiting_receipt',
                updated_at=?
            WHERE id=?
            AND status='awaiting_payment'
            """,
            (
                now(),
                order_id
            )
        )

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "payment",
        "invoice",
        {
            "order_id": order_id
        }
    )

    bot.send_message(
        call.message.chat.id,
        payment_text(
            order_id,
            product,
            required,
            price=int(order["price"]),
            wallet_used=int(order["wallet_reserved"])
        ),
        reply_markup=payment_keyboard(
            order_id
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "await_receipt:"
    )
)
def cb_await_receipt(call):
    try:

        order_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "شناسه سفارش نامعتبر است.",
            show_alert=True
        )

        return

    with db() as conn:

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND user_id=?
            """,
            (
                order_id,
                call.from_user.id
            )
        ).fetchone()

    if not order or order["status"] not in (
        "awaiting_receipt",
        "receipt_submitted"
    ):

        bot.answer_callback_query(
            call.id,
            "این سفارش آماده دریافت فیش نیست.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id,
        "حالا تصویر یا فایل فیش را در همین گفتگو ارسال کنید."
    )

    bot.send_message(
        call.message.chat.id,
        f"📤 فیش سفارش #{order_id} را همینجا ارسال کنید."
    )


# ══════════════════════════════════════════════════════════════════════════════
# MY SERVICES CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "my_services_list"
)
def cb_my_services_list(call):
    bot.answer_callback_query(
        call.id
    )

    services = get_active_services(
        call.from_user.id
    )

    if not services:

        bot.send_message(
            call.message.chat.id,
            "📦 سرویس فعالی ندارید."
        )

        return

    save_nav(
        call.from_user.id,
        "my_services",
        "home",
        {}
    )

    bot.send_message(
        call.message.chat.id,
        "📦 <b>سرویس های من</b>\n\n"
        "سرویس موردنظر را انتخاب کنید:",
        reply_markup=my_services_keyboard(
            services
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("server_status:")
)
def cb_server_status(call):
    try:
        service_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(call.id, "شناسه سرویس نامعتبر است.", show_alert=True)
        return

    svc = get_active_service_by_id(service_id)
    if not svc or svc["user_id"] != call.from_user.id or svc["service_type"] not in SERVER_SERVICE_TYPES:
        bot.answer_callback_query(call.id, "سرویس پیدا نشد.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "در حال دریافت وضعیت لحظه‌ای...")
    try:
        status = get_live_server_status(svc)
        bot.send_message(
            call.message.chat.id,
            server_status_text(svc, status),
            reply_markup=service_detail_keyboard(svc)
        )
    except EylanPanelError as exc:
        logger.exception("Failed to fetch server status for service %s", service_id)
        bot.send_message(
            call.message.chat.id,
            "⚠️ دریافت وضعیت لحظه‌ای از EylanPanel انجام نشد.\n\n"
            f"<code>{esc(str(exc))}</code>\n\n"
            + format_service_card(svc),
            reply_markup=service_detail_keyboard(svc)
        )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "my_service:"
    )
)
def cb_my_service_detail(call):
    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    svc = get_active_service_by_id(
        service_id
    )

    if not svc or svc["user_id"] != call.from_user.id:

        bot.answer_callback_query(
            call.id,
            "سرویس پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "service_detail",
        "my_services",
        {
            "service_id": service_id
        }
    )

    text = (
        "📦 <b>جزئیات سرویس</b>\n\n"
        + format_service_card(svc)
    )

    bot.send_message(
        call.message.chat.id,
        text,
        reply_markup=service_detail_keyboard(
            svc
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "renew_service:"
    )
)
def cb_renew_service(call):
    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    svc = get_active_service_by_id(
        service_id
    )

    if not svc or svc["user_id"] != call.from_user.id:

        bot.answer_callback_query(
            call.id,
            "سرویس پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    product = PRODUCT_INDEX.get(
        svc["product_id"]
    )

    if not product:

        for pid, p in PRODUCT_INDEX.items():

            if (
                svc["service_type"] == "wireguard"
                and pid.startswith("wireguard-")
            ):

                product = p
                break

            elif (
                svc["service_type"] == "chatgpt_shared"
                and pid.startswith("chatgpt-shared-")
            ):

                product = p
                break

            elif (
                svc["service_type"] == "chatgpt_personal"
                and pid == "chatgpt-plus-personal"
            ):

                product = p
                break

    if not product:

        bot.send_message(
            call.message.chat.id,
            "❌ محصول برای تمدید پیدا نشد. لطفاً با پشتیبانی تماس بگیرید."
        )

        return

    save_nav(
        call.from_user.id,
        "renew_invoice",
        "my_services",
        {
            "service_id": service_id,
            "product_id": product["id"]
        }
    )

    with db() as conn:

        existing = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id=?
            AND product_id=?
            AND status IN (
                'awaiting_payment',
                'awaiting_receipt',
                'receipt_submitted',
                'paid_pending_fulfillment',
                'fulfilling'
            )
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                call.from_user.id,
                product["id"]
            )
        ).fetchone()

    if existing:

        bot.send_message(
            call.message.chat.id,
            f"⚠️ شما یک سفارش باز #{existing['id']} برای این سرویس دارید.",
            reply_markup=reply_back_keyboard()
        )

        return

    try:

        order_id = create_order(
            call.from_user.id,
            product
        )

        with db() as conn:

            conn.execute(
                """
                UPDATE orders
                SET renewal_for_service=?
                WHERE id=?
                """,
                (
                    service_id,
                    order_id
                )
            )

    except Exception:

        logger.exception(
            "Failed creating renewal order"
        )

        bot.send_message(
            call.message.chat.id,
            "❌ خطا در ثبت سفارش تمدید."
        )

        return

    bot.send_message(
        call.message.chat.id,
        invoice_text(
            order_id,
            product
        ),
        reply_markup=invoice_keyboard(
            order_id
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "service_history:"
    )
)
def cb_service_history(call):
    try:

        user_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    if (
        user_id != call.from_user.id
        and call.from_user.id not in ADMIN_IDS
    ):

        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    services = get_active_services(
        user_id
    )

    if not services:

        bot.send_message(
            call.message.chat.id,
            "هیچ سرویسی در تاریخچه ثبت نشده است."
        )

        return

    lines = [
        "📋 <b>تاریخچه سرویس‌ها</b>\n"
    ]

    for svc in services:

        lines.append(
            format_service_card(svc)
        )

        lines.append(
            "───────────────"
        )

    bot.send_message(
        call.message.chat.id,
        "\n".join(lines)
    )


# ══════════════════════════════════════════════════════════════════════════════
# SUPPORT CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "request_code:"
    )
)
def cb_request_code(call):
    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    svc = get_active_service_by_id(
        service_id
    )

    if not svc or svc["user_id"] != call.from_user.id:

        bot.answer_callback_query(
            call.id,
            "سرویس پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    _start_user_support_chat(
        call.message.chat.id,
        call.from_user.id,
        service_id
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "support_chat:"
    )
)
def cb_support_chat(call):
    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    svc = get_active_service_by_id(
        service_id
    )

    if not svc or svc["user_id"] != call.from_user.id:

        bot.answer_callback_query(
            call.id,
            "سرویس پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    _start_user_support_chat(
        call.message.chat.id,
        call.from_user.id,
        service_id
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "send_support_msgs:"
    )
)
def cb_send_support_msgs(call):
    try:

        support_chat_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    draft_chat_id, messages = get_draft_messages(
        call.from_user.id
    )

    if not messages:

        bot.answer_callback_query(
            call.id,
            "هیچ پیامی ارسال نکرده‌اید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id,
        "پیام‌های شما ارسال شد ✅"
    )

    nav = get_nav(
        call.from_user.id
    )

    service_id = nav["data"].get(
        "service_id"
    )

    svc = (
        get_active_service_by_id(
            service_id
        )
        if service_id
        else None
    )

    with db() as conn:

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE user_id=?
            """,
            (call.from_user.id,)
        ).fetchone()

    header = _build_admin_support_header(
        call.from_user.id,
        user,
        svc
    )

    support_kb = admin_support_info_keyboard(
        support_chat_id,
        service_id
    )

    for admin_id in ADMIN_IDS:

        try:

            bot.send_message(
                admin_id,
                header,
                reply_markup=support_kb
            )

            for msg_data in messages:

                _replay_message_to_chat(
                    admin_id,
                    msg_data
                )

        except Exception:

            logger.exception(
                "Failed sending support messages to admin %s",
                admin_id
            )

    clear_draft_messages(
        call.from_user.id
    )

    save_nav(
        call.from_user.id,
        "home",
        None,
        {}
    )

    bot.send_message(
        call.message.chat.id,
        "✅ پیام‌های شما برای پشتیبانی ارسال شد.\n"
        "در اولین فرصت پاسخ داده خواهد شد.",
        reply_markup=reply_main_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN SUPPORT REPLY
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "admin_reply:"
    )
)
def cb_admin_reply(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:

        support_chat_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    chat = get_support_chat_by_id(
        support_chat_id
    )

    if not chat:

        bot.answer_callback_query(
            call.id,
            "چت پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "admin_support_reply",
        {
            "support_chat_id": support_chat_id,
            "target_user_id": chat["user_id"]
        }
    )

    clear_draft_messages(
        call.from_user.id
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "✅ ارسال پیام‌ها",
            callback_data=f"admin_send_reply:{support_chat_id}"
        )
    )

    bot.send_message(
        call.from_user.id,
        f"💬 <b>پاسخ به کاربر <code>{chat['user_id']}</code></b>\n\n"
        "پیام‌های خود را ارسال کنید، سپس روی «ارسال پیام‌ها» بزنید:",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "admin_send_reply:"
    )
)
def cb_admin_send_reply(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        call.from_user.id
    )

    if (
        not state
        or state["mode"] != "admin_support_reply"
    ):

        bot.answer_callback_query(
            call.id,
            "حالت پاسخ فعال نیست.",
            show_alert=True
        )

        return

    draft_chat_id, messages = get_draft_messages(
        call.from_user.id
    )

    if not messages:

        bot.answer_callback_query(
            call.id,
            "هیچ پیامی ارسال نکرده‌اید.",
            show_alert=True
        )

        return

    target_user_id = int(
        state["data"]["target_user_id"]
    )

    bot.answer_callback_query(
        call.id,
        "پیام ارسال شد ✅"
    )

    try:

        for msg_data in messages:

            _replay_message_to_chat(
                target_user_id,
                msg_data
            )

        bot.send_message(
            call.from_user.id,
            f"✅ پیام‌ها برای کاربر "
            f"<code>{target_user_id}</code> ارسال شد."
        )

    except Exception:

        logger.exception(
            "Failed sending admin reply to user %s",
            target_user_id
        )

        bot.send_message(
            call.from_user.id,
            "❌ ارسال پیام ناموفق بود."
        )

    clear_draft_messages(
        call.from_user.id
    )

    clear_admin_state(
        call.from_user.id
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "admin_new_gmail:"
    )
)
def cb_admin_new_gmail(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "admin_new_gmail",
        {
            "service_id": service_id
        }
    )

    bot.send_message(
        call.from_user.id,
        f"📧 جیمیل جدید برای سرویس #{service_id} را وارد کنید:"
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "admin_svc_history:"
    )
)
def cb_admin_svc_history(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:

        service_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    svc = get_active_service_by_id(
        service_id
    )

    if not svc:

        bot.answer_callback_query(
            call.id,
            "سرویس پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    services = get_active_services(
        svc["user_id"]
    )

    lines = [
        f"📋 <b>تاریخچه سرویس‌های کاربر "
        f"<code>{svc['user_id']}</code></b>\n"
    ]

    for s in services:

        lines.append(
            format_service_card(s)
        )

        lines.append(
            "───────────────"
        )

    bot.send_message(
        call.from_user.id,
        "\n".join(lines)
    )


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION / SUPPORT / ABOUT
# ══════════════════════════════════════════════════════════════════════════════

def connection_reply_keyboard():
    return reply_back_keyboard()


def connection_menu_keyboard():
    """
    منوی انتخاب آموزش «نحوه اتصال» برای کاربر.

    فقط سرویس‌هایی که آموزش‌شان داخل ربات ارائه می‌شود اینجا هستند
    (وایرساک / وایرگارد، V2Ray و هوش مصنوعی).
    """

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for service_key, label in USER_CONNECTION_SERVICES.items():

        kb.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"connection_service:{service_key}"
            )
        )

    return kb


CONNECTION_MENU_TEXT = (
    "🔗 <b>نحوه اتصال</b>\n\n"
    "برای شروع، نوع سرویس خود را انتخاب کنید:"
)


def latest_purchased_connection_service(user_id):
    """
    آخرین سرویسِ خریداری‌شده (سفارش تکمیل‌شده) کاربر را برمی‌گرداند
    به شرطی که آموزش آن در «نحوه اتصال» وجود داشته باشد؛ در غیر این صورت None.
    """

    with db() as conn:
        rows = conn.execute(
            """
            SELECT product_id
            FROM orders
            WHERE user_id=?
            AND status='completed'
            ORDER BY COALESCE(completed_at, updated_at) DESC, id DESC
            LIMIT 20
            """,
            (user_id,)
        ).fetchall()

    for row in rows:

        service = service_for_product(
            row["product_id"]
        )

        if service in USER_CONNECTION_SERVICES:
            return service

    return None


def open_connection_for_user(
    chat_id,
    user_id,
    call=None
):
    """
    ورود به «نحوه اتصال».

    اگر کاربر قبلاً خرید تکمیل‌شده‌ای دارد، آموزش همان سرویس مستقیم نمایش
    داده می‌شود؛ با «⬅️ بازگشت» منوی بقیه آموزش‌ها باز می‌شود.
    اگر خرید ندارد (یا آموزش آن سرویس هنوز ثبت نشده)، منوی انتخاب سرویس
    نمایش داده می‌شود.
    """

    service = latest_purchased_connection_service(
        user_id
    )

    if (
        service
        and connection_admin_content_count(service) > 0
    ):

        save_nav(
            user_id,
            "connection_service",
            "connection",
            {
                "service": service,
                "auto": True
            }
        )

        send_connection_content(
            chat_id,
            service
        )

        bot.send_message(
            chat_id,
            "↩️ برای دیدن آموزش سرویس‌های دیگر، از پایین روی «⬅️ بازگشت» بزنید."
        )

        return

    save_nav(
        user_id,
        "connection",
        "home",
        {}
    )

    if call is not None:

        edit_or_send(
            call,
            CONNECTION_MENU_TEXT,
            connection_menu_keyboard(),
            bottom_keyboard=connection_reply_keyboard()
        )

    else:

        bot.send_message(
            chat_id,
            CONNECTION_MENU_TEXT,
            reply_markup=connection_menu_keyboard()
        )


@bot.callback_query_handler(
    func=lambda c: c.data == "connection"
)
def cb_connection(call):
    if not is_member(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "ابتدا عضویت خود را کامل کنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    open_connection_for_user(
        call.message.chat.id,
        call.from_user.id,
        call=call
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "connection_service:"
    )
)
def cb_connection_service(call):
    service = call.data.split(
        ":",
        1
    )[1]

    if service not in USER_CONNECTION_SERVICES:

        bot.answer_callback_query(
            call.id,
            "بخش پیدا نشد.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "connection_service",
        "connection",
        {
            "service": service
        }
    )

    send_connection_content(
        call.message.chat.id,
        service
    )


def support_url():
    username = SUPPORT_USERNAME.lstrip("@").strip()

    if username.startswith(
        "https://t.me/"
    ):
        return username

    return (
        f"https://t.me/{username}"
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "support"
)
def cb_support(call):
    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "support",
        "home",
        {}
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🎧 ارتباط با پشتیبانی",
            url=support_url()
        )
    )

    edit_or_send(
        call,
        "🎧 <b>پشتیبانی</b>\n\n"
        "برای ارتباط با پشتیبانی روی دکمه زیر بزنید.",
        kb,
        bottom_keyboard=reply_back_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "about"
)
def cb_about(call):
    bot.answer_callback_query(
        call.id
    )

    save_nav(
        call.from_user.id,
        "about",
        "home",
        {}
    )

    edit_or_send(
        call,
        f"ℹ️ <b>درباره ما</b>\n\n"
        f"{esc(ABOUT_TEXT)}",
        bottom_keyboard=reply_back_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# RECEIPT ADMIN CALLBACK
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "receipt:"
    )
)
def cb_receipt(call):
    if call.from_user.id not in ADMIN_IDS:

        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )

        return

    try:

        _, action, order_s = call.data.split(
            ":"
        )

        order_id = int(
            order_s
        )

    except Exception:

        bot.answer_callback_query(
            call.id,
            "اطلاعات سفارش نامعتبر است.",
            show_alert=True
        )

        return

    if action == "approve":

        with db() as conn:

            order = conn.execute(
                """
                SELECT *
                FROM orders
                WHERE id=?
                """,
                (order_id,)
            ).fetchone()

            if (
                not order
                or order["status"] != "receipt_submitted"
            ):

                bot.answer_callback_query(
                    call.id,
                    "این سفارش قبلاً بررسی شده یا فیشی برای آن ثبت نشده است.",
                    show_alert=True
                )

                return

            cash_required = int(
                order["cash_required"]
            )

            conn.execute(
                """
                INSERT INTO transactions(
                    user_id,
                    order_id,
                    kind,
                    amount,
                    note,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order["user_id"],
                    order_id,
                    "cash_payment",
                    cash_required,
                    "تأیید فیش و پرداخت سفارش",
                    now()
                )
            )

            conn.execute(
                """
                UPDATE orders
                SET status='paid_pending_fulfillment',
                    payment_amount=?,
                    updated_at=?
                WHERE id=?
                AND status='receipt_submitted'
                """,
                (
                    int(order["price"]),
                    now(),
                    order_id
                )
            )

        bot.answer_callback_query(
            call.id,
            "فیش تأیید شد ✅"
        )

        if order["product_id"] == "chatgpt-plus-personal":

            try:

                bot.send_message(
                    order["user_id"],
                    "✅ سفارش شما تایید شد.\n\n"
                    "زمان تحویل سفارش شما به‌طور حدودی بین ۱ ساعت تا ۴۸ ساعت خواهد بود!\n"
                    "پس از تکمیل سفارش شما، توسط ربات برایتان اعلان تکمیل سفارش و فعال شدن پلاس بر روی اکانتتان ارسال خواهد شد!",
                    reply_markup=reply_main_keyboard()
                )

            except Exception:

                logger.exception(
                    "Failed sending personal AI confirmation to user %s",
                    order["user_id"]
                )

        notify_fulfillment_admins(
            order_id
        )

    else:

        with db() as conn:

            order = conn.execute(
                """
                SELECT *
                FROM orders
                WHERE id=?
                """,
                (order_id,)
            ).fetchone()

            if (
                not order
                or order["status"] != "receipt_submitted"
            ):

                bot.answer_callback_query(
                    call.id,
                    "این سفارش قبلاً بررسی شده است.",
                    show_alert=True
                )

                return

            reserved = int(
                order["wallet_reserved"]
            )

            if reserved:

                conn.execute(
                    """
                    UPDATE users
                    SET balance=balance+?,
                        updated_at=?
                    WHERE user_id=?
                    """,
                    (
                        reserved,
                        now(),
                        order["user_id"]
                    )
                )

                conn.execute(
                    """
                    INSERT INTO transactions(
                        user_id,
                        order_id,
                        kind,
                        amount,
                        note,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order["user_id"],
                        order_id,
                        "wallet_release",
                        reserved,
                        "بازگشت موجودی بعد از رد فیش",
                        now()
                    )
                )

            conn.execute(
                """
                UPDATE orders
                SET status='rejected',
                    rejection_reason=?,
                    updated_at=?
                WHERE id=?
                AND status='receipt_submitted'
                """,
                (
                    "فیش نامعتبر یا مورد تأیید نبود.",
                    now(),
                    order_id
                )
            )

        bot.answer_callback_query(
            call.id,
            "فیش رد شد."
        )

        try:

            bot.send_message(
                order["user_id"],
                f"❌ <b>فیش سفارش #{order_id} مورد تأیید نبود.</b>\n\n"
                "درخواست شما رد شد. در صورت نیاز می‌توانید دوباره اقدام کنید.",
                reply_markup=reply_main_keyboard()
            )

        except Exception:

            logger.exception(
                "Unable to notify user after rejection"
            )

    try:

        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# FULFILLMENT CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "fulfill:start:"
    )
)
def cb_fulfill_start(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )
        return

    try:
        order_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(
            call.id,
            "شناسه سفارش نامعتبر است.",
            show_alert=True
        )
        return

    with db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?",
            (order_id,)
        ).fetchone()

        if not order:
            bot.answer_callback_query(
                call.id,
                "سفارش پیدا نشد.",
                show_alert=True
            )
            return

        cur = conn.execute(
            """
            UPDATE orders
            SET status='fulfilling',
                assigned_admin_id=?,
                updated_at=?
            WHERE id=?
            AND status='paid_pending_fulfillment'
            """,
            (call.from_user.id, now(), order_id)
        )

        if cur.rowcount != 1:
            bot.answer_callback_query(
                call.id,
                "این سفارش قبلاً توسط ادمین دیگری گرفته شده یا بسته شده است.",
                show_alert=True
            )
            return

    # V2Ray فقط به‌صورت دستی تحویل داده می‌شود؛ انتخاب روش تحویل نمایش داده نمی‌شود.
    if service_for_product(order["product_id"]) == "v2ray":
        bot.answer_callback_query(
            call.id,
            "تحویل V2Ray دستی است ✋"
        )
        _start_manual_fulfillment(
            call.from_user.id,
            order_id
        )
        return

    set_admin_state(
        call.from_user.id,
        "fulfill_choose",
        {"order_id": order_id}
    )

    bot.answer_callback_query(call.id, "روش تحویل را انتخاب کنید.")

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "⚡ تحویل اتوماتیک",
            callback_data=f"fulfill:auto:{order_id}"
        ),
        types.InlineKeyboardButton(
            "✋ تحویل دستی",
            callback_data=f"fulfill:manual:{order_id}"
        ),
        types.InlineKeyboardButton(
            "❌ لغو حالت تحویل",
            callback_data="fulfill:cancel"
        )
    )

    service = service_for_product(order["product_id"])
    if service == "wireguard":
        auto_note = (
            "✅ برای این محصول، تحویل اتوماتیک از EylanPanel در دسترس است.\n"
            "در حالت دستی، ربات همان لینک/فایل ارسالی شما را داخل پیام آماده قرار می‌دهد."
        )
    else:
        auto_note = (
            "ℹ️ تحویل اتوماتیک فقط برای سرویس WireGuard فعال است.\n"
            "برای سایر محصولات، گزینه «تحویل دستی» را انتخاب کنید."
        )

    bot.send_message(
        call.from_user.id,
        f"📦 <b>تحویل سفارش #{order_id}</b>\n\n"
        f"🛍 محصول: <b>{esc(order['product_title'])}</b>\n\n"
        f"{auto_note}\n\n"
        "روش تحویل را انتخاب کنید:",
        reply_markup=kb
    )


def _start_manual_fulfillment(admin_id, order_id):
    order = get_order_by_id_for_fulfillment(order_id, admin_id)
    if not order:
        bot.send_message(
            admin_id,
            "این سفارش دیگر در حالت تحویل نیست."
        )
        clear_admin_state(admin_id)
        return False

    set_admin_state(
        admin_id,
        "fulfill",
        {"order_id": order_id, "delivery_mode": "manual"}
    )
    save_order_api_state(
        order_id,
        delivery_mode="manual"
    )

    if order["product_id"] == "chatgpt-plus-personal":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(
                "✅ تحویل سفارش (پیام آماده)",
                callback_data=f"fulfill:personal_deliver:{order_id}"
            ),
            types.InlineKeyboardButton(
                "❌ لغو حالت تحویل",
                callback_data="fulfill:cancel"
            )
        )
        bot.send_message(
            admin_id,
            f"📦 <b>تحویل سفارش #{order_id}</b>\n\n"
            f"📧 جیمیل مشتری: <b>{esc(order['gmail_address'] or 'ثبت نشده')}</b>\n\n"
            "برای ارسال پیام آماده به مشتری، روی دکمه زیر بزنید:",
            reply_markup=kb
        )
        return True

    if order["product_id"].startswith("chatgpt-shared-"):
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(
            "❌ لغو حالت تحویل",
            callback_data="fulfill:cancel"
        ))
        bot.send_message(
            admin_id,
            f"📦 <b>تحویل سفارش #{order_id} — ChatGPT اشتراکی</b>\n\n"
            "آدرس جیمیل اکانت را ارسال کنید تا ربات آن را داخل پیام آماده قرار داده و برای مشتری بفرستد:",
            reply_markup=kb
        )
        return True

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "❌ لغو حالت تحویل",
        callback_data="fulfill:cancel"
    ))
    username_line = ""
    if order["service_username"]:
        username_line = (
            f"👤 نام کاربری: <b>{esc(order['service_username'])}</b>\n\n"
        )

    if service_for_product(order["product_id"]) in {"wireguard", "v2ray"}:
        prompt = (
            "لینک/فایل سرویس را ارسال کنید تا در همان پیام آماده برای مشتری قرار بگیرد.\n"
            "متن، عکس، فایل یا ویدئو نیز در صورت نیاز پذیرفته می‌شود."
        )
    else:
        prompt = (
            "اکنون مورد تحویل سفارش را ارسال کنید.\n"
            "فایل، عکس، ویدئو یا متنِ موردنیاز سفارش را بفرستید."
        )

    bot.send_message(
        admin_id,
        f"📦 <b>تحویل دستی سفارش #{order_id}</b>\n\n"
        f"{username_line}"
        f"{prompt}",
        reply_markup=kb
    )
    return True


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("fulfill:auto:")
)
def cb_fulfill_auto(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True)
        return

    try:
        order_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(call.id, "شناسه سفارش نامعتبر است.", show_alert=True)
        return

    order = get_order_by_id_for_fulfillment(order_id, call.from_user.id)
    if not order:
        bot.answer_callback_query(call.id, "این سفارش دیگر در حالت تحویل نیست.", show_alert=True)
        clear_admin_state(call.from_user.id)
        return

    service = service_for_product(order["product_id"])
    if service != "wireguard":
        bot.answer_callback_query(
            call.id,
            "تحویل اتوماتیک برای این محصول فعال نیست؛ تحویل دستی را انتخاب کنید.",
            show_alert=True
        )
        return

    save_order_api_state(order_id, delivery_mode="auto")
    set_admin_state(
        call.from_user.id,
        "fulfill",
        {"order_id": order_id, "delivery_mode": "auto"}
    )
    bot.answer_callback_query(call.id, "تحویل اتوماتیک شروع شد ✅")
    label = "WireGuard" if service == "wireguard" else "V2Ray / Sing-box"
    bot.send_message(
        call.from_user.id,
        f"⚙️ در حال ساخت خودکار سفارش #{order_id} در EylanPanel و دریافت لینک {label}..."
    )
    deliver_eylan_order_via_api(call.from_user.id, order_id)


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("fulfill:manual:")
)
def cb_fulfill_manual(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True)
        return

    try:
        order_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(call.id, "شناسه سفارش نامعتبر است.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "تحویل دستی انتخاب شد ✅")
    _start_manual_fulfillment(call.from_user.id, order_id)


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "fulfill:retry:"
    )
)
def cb_fulfill_retry(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )
        return

    try:
        order_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(
            call.id,
            "شناسه سفارش نامعتبر است.",
            show_alert=True
        )
        return

    order = get_order_by_id_for_fulfillment(
        order_id,
        call.from_user.id
    )

    if not order or service_for_product(order["product_id"]) != "wireguard":
        bot.answer_callback_query(
            call.id,
            "این سفارش دیگر برای تحویل خودکار آماده نیست.",
            show_alert=True
        )
        return

    save_order_api_state(order_id, delivery_mode="auto")
    set_admin_state(
        call.from_user.id,
        "fulfill",
        {"order_id": order_id, "delivery_mode": "auto"}
    )

    bot.answer_callback_query(
        call.id,
        "تلاش مجدد شروع شد..."
    )

    deliver_eylan_order_via_api(
        call.from_user.id,
        order_id
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "fulfill:personal_deliver:"
    )
)
def cb_fulfill_personal_deliver(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:

        order_id = int(
            call.data.split(":")[-1]
        )

    except (
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id,
            "خطا.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id,
        "در حال تحویل..."
    )

    fulfill_personal_ai_direct(
        call.from_user.id,
        order_id
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "wgtrial:send:"
    )
)
def cb_wg_trial_send(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )
        return

    try:
        target_id = int(call.data.split(":")[-1])
    except (TypeError, ValueError):
        bot.answer_callback_query(
            call.id,
            "شناسه کاربر نامعتبر است.",
            show_alert=True
        )
        return

    trial = get_wireguard_trial(target_id)
    if not trial:
        bot.answer_callback_query(
            call.id,
            "درخواست تست پیدا نشد.",
            show_alert=True
        )
        return

    set_admin_state(
        call.from_user.id,
        "wg_trial_send",
        {
            "user_id": target_id,
            "notify_chat_id": call.message.chat.id,
            "notify_message_id": call.message.message_id
        }
    )

    bot.answer_callback_query(
        call.id,
        "در حال ساخت و ارسال خودکار..."
    )

    bot.send_message(
        call.from_user.id,
        "⚙️ در حال ساخت اکانت تست در EylanPanel و دریافت لینک پنل..."
    )

    deliver_wireguard_trial_via_api(
        call.from_user.id,
        target_id
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "wgtrial:cancel"
)
def cb_wg_trial_cancel(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        call.from_user.id
    )

    if (
        not state
        or state["mode"] != "wg_trial_send"
    ):

        bot.answer_callback_query(
            call.id,
            "ارسال فعالی ندارید.",
            show_alert=True
        )

        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id,
        "ارسال اکانت تست لغو شد."
    )

    try:

        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    except Exception:
        pass

    bot.send_message(
        call.from_user.id,
        "↩️ ارسال اکانت تست لغو شد. "
        "هر زمان خواستید، دوباره روی دکمه «📤 ارسال اکانت تست» بزنید."
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "fulfill:cancel"
)
def cb_fulfill_cancel(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        call.from_user.id
    )

    if (
        not state
        or state["mode"] not in {"fulfill", "fulfill_choose"}
    ):

        bot.answer_callback_query(
            call.id,
            "تحویل فعالی ندارید.",
            show_alert=True
        )

        return

    order_id = int(
        state["data"].get(
            "order_id",
            0
        )
    )

    with db() as conn:

        conn.execute(
            """
            UPDATE orders
            SET status='paid_pending_fulfillment',
                assigned_admin_id=NULL,
                updated_at=?
            WHERE id=?
            AND assigned_admin_id=?
            AND status='fulfilling'
            """,
            (
                now(),
                order_id,
                call.from_user.id
            )
        )

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id,
        "حالت تحویل لغو شد."
    )

    bot.send_message(
        call.from_user.id,
        f"↩️ سفارش #{order_id} دوباره در صف تحویل قرار گرفت."
    )

    notify_fulfillment_admins(
        order_id
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "admin:home"
)
def cb_admin_home(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    edit_or_send(
        call,
        "🛠 <b>پنل مدیریت</b>\n\n"
        "بخش موردنظر را انتخاب کنید:",
        admin_panel_keyboard(call.from_user.id)
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:stats"
)
def cb_admin_stats(call):
    if call.from_user.id not in ADMIN_IDS:

        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id,
        "آمار بروزرسانی شد ✅"
    )

    try:

        edit_or_send(
            call,
            admin_stats_text(),
            admin_stats_keyboard()
        )

    except Exception:

        logger.exception(
            "Failed generating admin statistics"
        )

        bot.send_message(
            call.from_user.id,
            "❌ دریافت آمار با خطا مواجه شد."
        )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:orders"
)
def cb_admin_orders(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    edit_or_send(
        call,
        orders_text(),
        kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:users"
)
def cb_admin_users(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "📨 ارسال پیام به کاربر",
            callback_data="admin:send_user"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    edit_or_send(
        call,
        list_users_text(),
        kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:send_user"
)
def cb_admin_send_user(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "send_user_wait_id",
        {}
    )

    bot.send_message(
        call.from_user.id,
        "👤 شناسه عددی کاربر را وارد کنید:",
        reply_markup=remove_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:broadcast_text"
)
def cb_admin_broadcast_text(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "broadcast_text",
        {}
    )

    bot.send_message(
        call.from_user.id,
        "📢 متن موردنظر برای ارسال همگانی را همینجا ارسال کنید.\n\n"
        "برای لغو، /cancel بفرستید."
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:broadcast_forward"
)
def cb_admin_broadcast_forward(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "broadcast_forward",
        {}
    )

    bot.send_message(
        call.from_user.id,
        "📣 پیام موردنظر را در همین گفتگو فوروارد کنید.\n\n"
        "برای لغو، /cancel بفرستید."
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:guides"
)
def cb_admin_guides(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    edit_or_send(
        call,
        "🎓 <b>مدیریت نحوه اتصال</b>\n\n"
        "بخش آموزشی را انتخاب کنید:",
        admin_guide_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "guide:service:"
    )
)
def cb_guide_service(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    service = call.data.split(
        ":"
    )[-1]

    if service not in SERVICE_LABELS:
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    count = connection_admin_content_count(
        service
    )

    edit_or_send(
        call,
        f"🎓 <b>{SERVICE_LABELS[service]}</b>\n\n"
        f"تعداد محتوای ذخیره‌شده: <b>{count}</b>",
        admin_guide_service_keyboard(
            service
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "guide:add:"
    )
)
def cb_guide_add(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    service = call.data.split(
        ":"
    )[-1]

    if service not in SERVICE_LABELS:
        return

    set_admin_state(
        call.from_user.id,
        "guide_add",
        {
            "service": service
        }
    )

    bot.answer_callback_query(
        call.id
    )

    bot.send_message(
        call.from_user.id,
        f"➕ <b>افزودن آموزش {SERVICE_LABELS[service]}</b>\n\n"
        "حالا پیام‌ها را یکی‌یکی بفرستید. متن، عکس، ویدئو، فایل، صدا و ویس پشتیبانی می‌شود.\n"
        "هر تعداد محتوا که لازم دارید ارسال کنید و در پایان «اتمام افزودن» را بزنید.",
        reply_markup=guide_upload_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "guide:add_done"
)
def cb_guide_add_done(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        call.from_user.id
    )

    if (
        not state
        or state["mode"] != "guide_add"
    ):

        bot.answer_callback_query(
            call.id,
            "فرآیند افزودنی فعالی ندارید.",
            show_alert=True
        )

        return

    service = state["data"].get(
        "service"
    )

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id,
        "ذخیره شد ✅"
    )

    kb = admin_guide_service_keyboard(
        service
    )

    bot.send_message(
        call.from_user.id,
        f"✅ افزودن آموزش {SERVICE_LABELS[service]} به پایان رسید.\n"
        f"تعداد محتوا: {connection_admin_content_count(service)}",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "guide:preview:"
    )
)
def cb_guide_preview(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    service = call.data.split(
        ":"
    )[-1]

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    preview_connection_content(
        call.from_user.id,
        service
    )

    bot.send_message(
        call.from_user.id,
        "⬅️ برای بازگشت از دکمه زیر استفاده کنید.",
        reply_markup=admin_guide_service_keyboard(
            service
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "guide:clear:"
    )
)
def cb_guide_clear(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    service = call.data.split(
        ":"
    )[-1]

    clear_connection_content(
        service
    )

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id,
        "همه محتواها حذف شدند ✅",
        show_alert=True
    )

    edit_or_send(
        call,
        f"🎓 <b>{SERVICE_LABELS[service]}</b>\n\n"
        "تمام محتوای آموزشی حذف شد.",
        admin_guide_service_keyboard(
            service
        )
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "guide:send:"
    )
)
def cb_guide_send(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    service = call.data.split(
        ":",
        2
    )[2]

    if service not in SERVICE_LABELS:

        bot.answer_callback_query(
            call.id,
            "بخش پیدا نشد.",
            show_alert=True
        )

        return

    if connection_admin_content_count(service) == 0:

        bot.answer_callback_query(
            call.id,
            "برای این بخش هنوز محتوایی ثبت نشده است.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "guide_send_wait_id",
        {
            "service": service
        }
    )

    bot.send_message(
        call.from_user.id,
        f"📤 <b>ارسال آموزش {SERVICE_LABELS[service]}</b>\n\n"
        "شناسه عددی کاربر مقصد را ارسال کنید.\n\n"
        "برای لغو، /cancel بفرستید.",
        reply_markup=remove_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN WALLET (مدیریت موجودی کاربر)
# ══════════════════════════════════════════════════════════════════════════════

def admin_wallet_text(user_id):
    user = get_user_row(
        user_id
    )

    if not user:
        return None

    username = (
        f"@{user['username']}"
        if user["username"]
        else "بدون یوزرنیم"
    )

    reserved = get_reserved_total(
        user_id
    )

    reserved_line = ""

    if reserved > 0:
        reserved_line = (
            f"🔒 رزروشده روی سفارش‌های باز: <b>{money(reserved)}</b>\n"
            "(این مبلغ از موجودی کم شده و اگر سفارش لغو/رد شود برمی‌گردد)\n"
        )

    status = (
        "🚫 بلاک"
        if user["is_blocked"]
        else "✅ فعال"
    )

    return (
        "👛 <b>مدیریت موجودی کاربر</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 نام: <b>{esc(user['first_name'] or 'بدون نام')}</b>\n"
        f"🔗 یوزرنیم: {esc(username)}\n"
        f"🆔 شناسه: <code>{user_id}</code>\n"
        f"📶 وضعیت: {status}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 موجودی فعلی: <b>{money(user['balance'])}</b>\n"
        f"{reserved_line}"
        "━━━━━━━━━━━━━━━━━━\n"
        "عملیات موردنظر را انتخاب کنید:"
    )


def admin_wallet_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.row(
        types.InlineKeyboardButton(
            "➕ افزایش موجودی",
            callback_data=f"wallet:add:{user_id}"
        ),
        types.InlineKeyboardButton(
            "➖ کاهش موجودی",
            callback_data=f"wallet:sub:{user_id}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🔢 تنظیم مقدار دقیق",
            callback_data=f"wallet:set:{user_id}"
        ),
        types.InlineKeyboardButton(
            "🧹 صفر کردن موجودی",
            callback_data=f"wallet:zero:{user_id}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "📜 تراکنش‌های اخیر",
            callback_data=f"wallet:tx:{user_id}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🔁 کاربر دیگر",
            callback_data="admin:wallet"
        ),
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


def show_admin_wallet_panel(
    admin_id,
    user_id
):
    text = admin_wallet_text(
        user_id
    )

    if not text:

        bot.send_message(
            admin_id,
            "⚠️ این کاربر در دیتابیس ربات ثبت نشده است.\n"
            "کاربر باید حداقل یک‌بار ربات را استارت کرده باشد."
        )

        return False

    bot.send_message(
        admin_id,
        text,
        reply_markup=admin_wallet_keyboard(
            user_id
        )
    )

    return True


def notify_user_balance_change(
    user_id,
    operation,
    old_balance,
    new_balance
):
    delta = new_balance - old_balance

    if delta > 0:
        headline = (
            f"➕ <b>{money(delta)}</b> به کیف پول شما اضافه شد."
        )

    elif delta < 0:
        headline = (
            f"➖ <b>{money(abs(delta))}</b> از کیف پول شما کسر شد."
        )

    else:
        headline = (
            "🔄 موجودی کیف پول شما بررسی شد و تغییری نکرد."
        )

    extra = ""

    if new_balance > 0:
        extra = (
            "\n\n🛍 هنگام خرید، این مبلغ به‌صورت خودکار از قیمت سرویس کم می‌شود "
            "و فقط مابه‌التفاوت را واریز می‌کنید."
        )

    try:

        bot.send_message(
            user_id,
            "👛 <b>به‌روزرسانی کیف پول</b>\n\n"
            f"{headline}\n"
            f"💰 موجودی فعلی شما: <b>{money(new_balance)}</b>"
            f"{extra}",
            reply_markup=reply_main_keyboard()
        )

    except Exception as exc:

        logger.warning(
            "Could not notify user %s about balance change: %s",
            user_id,
            exc
        )

        if (
            "blocked" in str(exc).lower()
            or "chat not found" in str(exc).lower()
        ):

            set_blocked(
                user_id,
                True
            )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:wallet"
)
def cb_admin_wallet(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "wallet_wait_id",
        {}
    )

    bot.send_message(
        call.from_user.id,
        "👛 <b>مدیریت موجودی</b>\n\n"
        "شناسه عددی کاربر را ارسال کنید:\n\n"
        "برای لغو، /cancel بفرستید.",
        reply_markup=remove_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "wallet:"
    )
)
def cb_admin_wallet_action(call):
    if call.from_user.id not in ADMIN_IDS:

        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )

        return

    parts = call.data.split(
        ":"
    )

    if len(parts) < 3:

        bot.answer_callback_query(
            call.id,
            "درخواست نامعتبر است.",
            show_alert=True
        )

        return

    action = parts[1]

    try:
        target_id = int(
            parts[2]
        )
    except (TypeError, ValueError):

        bot.answer_callback_query(
            call.id,
            "شناسه کاربر نامعتبر است.",
            show_alert=True
        )

        return

    if not get_user_row(target_id):

        bot.answer_callback_query(
            call.id,
            "این کاربر در دیتابیس ربات نیست.",
            show_alert=True
        )

        return

    # ── تراکنش‌های اخیر ───────────────────────────────────────────────────────
    if action == "tx":

        bot.answer_callback_query(
            call.id
        )

        rows = recent_transactions(
            target_id,
            10
        )

        if not rows:

            bot.send_message(
                call.from_user.id,
                f"📜 برای کاربر <code>{target_id}</code> تراکنشی ثبت نشده است."
            )

            return

        kind_map = {
            "wallet_reserve": "رزرو برای سفارش",
            "wallet_release": "آزادسازی موجودی",
            "wallet_payment": "پرداخت از کیف پول",
            "cash_payment": "پرداخت نقدی (فیش)",
            "admin_credit": "افزایش توسط ادمین",
            "admin_debit": "کاهش توسط ادمین",
            "admin_set": "تنظیم توسط ادمین",
            "admin_zero": "صفر کردن توسط ادمین"
        }

        lines = [
            f"📜 <b>۱۰ تراکنش اخیر کاربر</b> <code>{target_id}</code>",
            ""
        ]

        for r in rows:

            amount = int(
                r["amount"]
            )

            sign = (
                "➕"
                if amount > 0
                else (
                    "➖"
                    if amount < 0
                    else "▫️"
                )
            )

            order_part = (
                f" | سفارش #{r['order_id']}"
                if r["order_id"]
                else ""
            )

            lines.append(
                f"{sign} {money(abs(amount))} | "
                f"{kind_map.get(r['kind'], r['kind'])}"
                f"{order_part}\n"
                f"   🕒 {r['created_at'][:16].replace('T', ' ')}"
            )

        bot.send_message(
            call.from_user.id,
            "\n".join(lines)
        )

        return

    # ── صفر کردن موجودی (با تأیید) ────────────────────────────────────────────
    if action == "zero":

        bot.answer_callback_query(
            call.id
        )

        current = get_balance(
            target_id
        )

        kb = types.InlineKeyboardMarkup(
            row_width=2
        )

        kb.row(
            types.InlineKeyboardButton(
                "✅ بله، صفر کن",
                callback_data=f"wallet:zero_ok:{target_id}"
            ),
            types.InlineKeyboardButton(
                "❌ انصراف",
                callback_data=f"wallet:panel:{target_id}"
            )
        )

        bot.send_message(
            call.from_user.id,
            "🧹 <b>صفر کردن موجودی</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 موجودی فعلی: <b>{money(current)}</b>\n\n"
            "آیا مطمئن هستید که موجودی این کاربر صفر شود؟",
            reply_markup=kb
        )

        return

    if action == "zero_ok":

        ok, old_balance, new_balance = apply_balance_change(
            target_id,
            "zero",
            0,
            call.from_user.id,
            "صفر کردن موجودی توسط ادمین"
        )

        if not ok:

            bot.answer_callback_query(
                call.id,
                "کاربر پیدا نشد.",
                show_alert=True
            )

            return

        bot.answer_callback_query(
            call.id,
            "موجودی صفر شد ✅"
        )

        bot.send_message(
            call.from_user.id,
            "✅ <b>موجودی صفر شد</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 موجودی قبلی: <b>{money(old_balance)}</b>\n"
            f"💰 موجودی جدید: <b>{money(new_balance)}</b>"
        )

        if old_balance != new_balance:

            notify_user_balance_change(
                target_id,
                "zero",
                old_balance,
                new_balance
            )

        show_admin_wallet_panel(
            call.from_user.id,
            target_id
        )

        return

    # ── نمایش دوباره پنل ──────────────────────────────────────────────────────
    if action == "panel":

        bot.answer_callback_query(
            call.id
        )

        clear_admin_state(
            call.from_user.id
        )

        show_admin_wallet_panel(
            call.from_user.id,
            target_id
        )

        return

    # ── افزایش / کاهش / تنظیم ─────────────────────────────────────────────────
    if action not in (
        "add",
        "sub",
        "set"
    ):

        bot.answer_callback_query(
            call.id,
            "عملیات نامعتبر است.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id
    )

    set_admin_state(
        call.from_user.id,
        "wallet_amount",
        {
            "user_id": target_id,
            "op": action
        }
    )

    prompts = {
        "add": (
            "➕ <b>افزایش موجودی</b>\n\n"
            "مبلغی که می‌خواهید به موجودی کاربر اضافه شود را ارسال کنید."
        ),
        "sub": (
            "➖ <b>کاهش موجودی</b>\n\n"
            "مبلغی که می‌خواهید از موجودی کاربر کم شود را ارسال کنید."
        ),
        "set": (
            "🔢 <b>تنظیم موجودی</b>\n\n"
            "موجودی نهایی کاربر را ارسال کنید."
        )
    }

    current = get_balance(
        target_id
    )

    bot.send_message(
        call.from_user.id,
        f"{prompts[action]}\n\n"
        f"👤 کاربر: <code>{target_id}</code>\n"
        f"💰 موجودی فعلی: <b>{money(current)}</b>\n\n"
        f"مثال: <code>200000</code> یا <code>۲۰۰,۰۰۰ تومان</code>\n"
        "برای لغو، /cancel بفرستید."
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN DATABASE BACKUP / RESTORE
# ══════════════════════════════════════════════════════════════════════════════

# ربات‌های تلگرام فقط تا ۲۰ مگابایت می‌توانند فایل دانلود کنند.
DB_RESTORE_MAX_BYTES = 20 * 1024 * 1024

# حداکثر حجم فایلی که ربات می‌تواند آپلود کند (۵۰ مگابایت) با کمی حاشیه.
DB_BACKUP_MAX_BYTES = 49 * 1024 * 1024

DB_REQUIRED_TABLES = (
    "users",
    "orders"
)


def _cleanup_temp_file(path):
    """فایل موقت (و پوشه‌ی موقت آن) را پاک می‌کند."""

    if not path:
        return

    for suffix in (
        "",
        "-wal",
        "-shm",
        "-journal"
    ):

        try:
            os.remove(
                path + suffix
            )
        except OSError:
            pass

    try:
        os.rmdir(
            os.path.dirname(path)
        )
    except OSError:
        pass


def _discard_pending_db_restore(state):
    if (
        state
        and state.get("mode") == "db_restore_confirm"
    ):
        _cleanup_temp_file(
            (state.get("data") or {}).get("path")
        )


def _db_counts(conn):
    def count(table):
        try:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
        except sqlite3.Error:
            return 0

    return {
        "users": count("users"),
        "orders": count("orders"),
        "services": count("active_services")
    }


def create_db_snapshot():
    """
    یک نسخه‌ی کاملاً سازگار از دیتابیس زنده می‌سازد.

    از Backup API خود SQLite استفاده می‌شود، چون دیتابیس در حالت WAL است و
    کپی ساده‌ی فایل ممکن است اطلاعات تازه را ناقص ببرد.
    """

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M%S"
    )

    directory = tempfile.mkdtemp(
        prefix="dbsnap_"
    )

    path = os.path.join(
        directory,
        f"shop_backup_{stamp}.sqlite3"
    )

    src = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    dst = sqlite3.connect(
        path
    )

    try:

        src.backup(
            dst
        )

        # فایل خروجی یک فایل تکی و مستقل باشد (بدون -wal و -shm)
        dst.execute(
            "PRAGMA journal_mode=DELETE"
        )

        dst.commit()

    except Exception:

        dst.close()
        src.close()

        _cleanup_temp_file(
            path
        )

        raise

    dst.close()
    src.close()

    return path


def send_db_backup_to_admin(
    admin_id,
    title
):
    """بکاپ دیتابیس را به‌صورت فایل برای ادمین می‌فرستد."""

    path = create_db_snapshot()

    try:

        size = os.path.getsize(
            path
        )

        if size > DB_BACKUP_MAX_BYTES:

            bot.send_message(
                admin_id,
                "❌ حجم دیتابیس از حد مجاز ارسال تلگرام (۵۰ مگابایت) بیشتر است."
            )

            return False

        conn = sqlite3.connect(
            path
        )

        try:
            counts = _db_counts(
                conn
            )
        finally:
            conn.close()

        stamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

        caption = (
            f"💾 <b>{esc(title)}</b>\n"
            f"🕒 {stamp}\n"
            f"👥 کاربران: {counts['users']}\n"
            f"📦 سفارش‌ها: {counts['orders']}\n"
            f"🛠 سرویس‌های فعال: {counts['services']}\n"
            f"📏 حجم: {size / 1024:.1f} KB"
        )

        with open(path, "rb") as f:

            bot.send_document(
                admin_id,
                f,
                caption=caption
            )

        return True

    finally:

        _cleanup_temp_file(
            path
        )


def validate_uploaded_db(path):
    """
    بررسی می‌کند فایل آپلودشده یک دیتابیس سالم و مربوط به همین ربات باشد.
    خروجی: (ok, error_text, counts)
    """

    try:

        with open(path, "rb") as f:
            header = f.read(16)

    except OSError:

        return False, "خواندن فایل انجام نشد.", None

    if header != b"SQLite format 3\x00":

        return False, "این فایل یک دیتابیس SQLite معتبر نیست.", None

    conn = None

    try:

        conn = sqlite3.connect(
            path
        )

        result = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if (
            not result
            or str(result[0]).lower() != "ok"
        ):

            return False, "فایل دیتابیس آسیب دیده است.", None

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        missing = [
            t
            for t in DB_REQUIRED_TABLES
            if t not in tables
        ]

        if missing:

            return (
                False,
                "این فایل مربوط به دیتابیس این ربات نیست "
                f"(جدول {', '.join(missing)} پیدا نشد).",
                None
            )

        return True, "", _db_counts(conn)

    except sqlite3.Error as exc:

        return False, f"خواندن دیتابیس ممکن نشد: {exc}", None

    finally:

        if conn is not None:
            conn.close()


def restore_database_from_file(path):
    """
    محتوای دیتابیس زنده را با فایل بکاپ جایگزین می‌کند.

    از Backup API استفاده می‌شود؛ یعنی فایل دیتابیس در حال استفاده حذف یا
    جابه‌جا نمی‌شود و اگر خطایی رخ دهد، دیتابیس فعلی دست‌نخورده می‌ماند.
    """

    src = sqlite3.connect(
        path
    )

    dst = sqlite3.connect(
        DB_PATH,
        timeout=60
    )

    try:

        src.backup(
            dst
        )

        try:
            dst.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            )
        except sqlite3.Error:
            pass

    finally:

        src.close()
        dst.close()

    # بکاپ‌های قدیمی‌تر ممکن است ستون/جدول جدید نداشته باشند.
    init_db()

    # قیمت‌های دیتابیس بازگردانی‌شده روی محصولات اعمال شود.
    load_price_overrides()

    # ادمین‌های اضافه‌شده از پنل در دیتابیس بازگردانی‌شده هم اعمال شوند.
    reload_admin_ids()


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:db_backup"
)
def cb_admin_db_backup(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id,
        "در حال آماده‌سازی بکاپ..."
    )

    try:

        send_db_backup_to_admin(
            call.from_user.id,
            "بکاپ دیتابیس ربات"
        )

    except Exception:

        logger.exception(
            "Database backup failed"
        )

        bot.send_message(
            call.from_user.id,
            "❌ ساخت بکاپ دیتابیس انجام نشد."
        )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:db_restore"
)
def cb_admin_db_restore(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    bot.answer_callback_query(
        call.id
    )

    _discard_pending_db_restore(
        get_admin_state(
            call.from_user.id
        )
    )

    set_admin_state(
        call.from_user.id,
        "db_restore_wait_file",
        {}
    )

    bot.send_message(
        call.from_user.id,
        "📤 <b>ارسال (بازگردانی) دیتابیس</b>\n\n"
        "فایل بکاپ دیتابیس را به‌صورت <b>فایل (Document)</b> ارسال کنید "
        "(همان فایلی که با «📥 دریافت دیتابیس» گرفته‌اید).\n\n"
        "⚠️ با بازگردانی، <b>تمام اطلاعات فعلی</b> ربات (کاربران، سفارش‌ها، "
        "موجودی‌ها، سرویس‌ها و آموزش‌ها) با اطلاعات داخل فایل جایگزین می‌شود. "
        "قبل از جایگزینی، یک بکاپ از وضعیت فعلی هم برایتان ارسال می‌شود.\n\n"
        "حداکثر حجم فایل: ۲۰ مگابایت\n"
        "برای لغو، /cancel بفرستید.",
        reply_markup=remove_keyboard()
    )


def handle_db_restore_upload(message):
    admin_id = message.from_user.id

    if message.content_type != "document":

        bot.send_message(
            message.chat.id,
            "❌ لطفاً فایل دیتابیس را به‌صورت «فایل» (Document) ارسال کنید.\n"
            "برای لغو، /cancel بفرستید."
        )

        return

    doc = message.document

    if (
        doc.file_size
        and doc.file_size > DB_RESTORE_MAX_BYTES
    ):

        bot.send_message(
            message.chat.id,
            "❌ حجم فایل بیشتر از ۲۰ مگابایت است و ربات نمی‌تواند آن را دریافت کند."
        )

        return

    bot.send_message(
        message.chat.id,
        "⏳ در حال دریافت و بررسی فایل..."
    )

    directory = tempfile.mkdtemp(
        prefix="dbrestore_"
    )

    path = os.path.join(
        directory,
        "uploaded.sqlite3"
    )

    try:

        file_info = bot.get_file(
            doc.file_id
        )

        data = bot.download_file(
            file_info.file_path
        )

        with open(path, "wb") as f:
            f.write(data)

    except Exception:

        logger.exception(
            "Failed downloading database file"
        )

        _cleanup_temp_file(
            path
        )

        bot.send_message(
            message.chat.id,
            "❌ دریافت فایل از تلگرام انجام نشد. دوباره تلاش کنید یا /cancel بفرستید."
        )

        return

    ok, error, counts = validate_uploaded_db(
        path
    )

    if not ok:

        _cleanup_temp_file(
            path
        )

        bot.send_message(
            message.chat.id,
            f"❌ {esc(error)}\n\n"
            "فایل درست را ارسال کنید یا /cancel بفرستید."
        )

        return

    set_admin_state(
        admin_id,
        "db_restore_confirm",
        {
            "path": path,
            "users": counts["users"],
            "orders": counts["orders"],
            "services": counts["services"]
        }
    )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "✅ تأیید و جایگزینی دیتابیس",
            callback_data="admin:db_restore_yes"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "❌ لغو",
            callback_data="admin:db_restore_no"
        )
    )

    bot.send_message(
        message.chat.id,
        "✅ فایل سالم است.\n\n"
        "📦 <b>محتوای فایل بکاپ:</b>\n"
        f"👥 کاربران: {counts['users']}\n"
        f"📦 سفارش‌ها: {counts['orders']}\n"
        f"🛠 سرویس‌های فعال: {counts['services']}\n\n"
        "⚠️ با تأیید، تمام اطلاعات فعلی ربات با این فایل جایگزین می‌شود.",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:db_restore_no"
)
def cb_admin_db_restore_no(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        call.from_user.id
    )

    _discard_pending_db_restore(
        state
    )

    if state and state["mode"] in (
        "db_restore_confirm",
        "db_restore_wait_file"
    ):

        clear_admin_state(
            call.from_user.id
        )

    bot.answer_callback_query(
        call.id,
        "لغو شد."
    )

    try:

        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    except Exception:
        pass

    bot.send_message(
        call.from_user.id,
        "✅ بازگردانی دیتابیس لغو شد. اطلاعات فعلی تغییری نکرد.",
        reply_markup=admin_panel_keyboard(call.from_user.id)
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:db_restore_yes"
)
def cb_admin_db_restore_yes(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    admin_id = call.from_user.id

    state = get_admin_state(
        admin_id
    )

    path = (
        (state or {}).get("data") or {}
    ).get("path")

    if (
        not state
        or state["mode"] != "db_restore_confirm"
        or not path
        or not os.path.isfile(path)
    ):

        bot.answer_callback_query(
            call.id,
            "این درخواست دیگر معتبر نیست. دوباره «📤 ارسال دیتابیس» را بزنید.",
            show_alert=True
        )

        return

    bot.answer_callback_query(
        call.id,
        "در حال بازگردانی..."
    )

    try:

        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    except Exception:
        pass

    # ۱) قبل از هر تغییر، از وضعیت فعلی بکاپ ایمنی می‌فرستیم.
    try:

        if not send_db_backup_to_admin(
            admin_id,
            "بکاپ خودکار قبل از بازگردانی"
        ):
            raise RuntimeError(
                "safety backup was not sent"
            )

    except Exception:

        logger.exception(
            "Safety backup before restore failed"
        )

        _cleanup_temp_file(
            path
        )

        clear_admin_state(
            admin_id
        )

        bot.send_message(
            admin_id,
            "❌ بکاپ ایمنی از وضعیت فعلی ساخته نشد؛ بازگردانی انجام نشد "
            "و اطلاعات فعلی دست‌نخورده است."
        )

        return

    # ۲) جایگزینی دیتابیس
    try:

        restore_database_from_file(
            path
        )

    except Exception:

        logger.exception(
            "Database restore failed"
        )

        _cleanup_temp_file(
            path
        )

        try:
            clear_admin_state(
                admin_id
            )
        except Exception:
            pass

        bot.send_message(
            admin_id,
            "❌ بازگردانی دیتابیس انجام نشد. اطلاعات فعلی دست‌نخورده است.\n"
            "می‌توانید دوباره تلاش کنید."
        )

        return

    _cleanup_temp_file(
        path
    )

    # وضعیت‌های ادمین/کاربران از خودِ بکاپ آمده‌اند؛ وضعیت این ادمین پاک می‌شود.
    clear_admin_state(
        admin_id
    )

    logger.warning(
        "Database restored from uploaded backup by admin %s",
        admin_id
    )

    bot.send_message(
        admin_id,
        "✅ <b>دیتابیس با موفقیت بازگردانی شد.</b>\n\n"
        f"👥 کاربران: {state['data'].get('users', 0)}\n"
        f"📦 سفارش‌ها: {state['data'].get('orders', 0)}\n"
        f"🛠 سرویس‌های فعال: {state['data'].get('services', 0)}",
        reply_markup=admin_panel_keyboard(admin_id)
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN: FORCE JOIN (🔐 عضویت اجباری)
# ══════════════════════════════════════════════════════════════════════════════

def admin_forcejoin_text():
    channels = get_force_join_channels()
    enabled = is_force_join_enabled()

    lines = [
        "🔐 <b>عضویت اجباری</b>",
        "",
        "ربات باید در کانال‌هایی که اینجا اضافه می‌کنید عضو (و ترجیحاً ادمین) باشد. "
        "کاربر تا وقتی عضو همه‌ی این کانال‌ها نشود، نمی‌تواند از ربات استفاده کند.",
        "",
        f"وضعیت فعلی: {'✅ فعال' if enabled else '❌ غیرفعال'}"
    ]

    if not channels:
        lines.append("\nهیچ کانالی ثبت نشده است. با «➕ افزودن کانال» شروع کنید.")
    else:
        lines.append("\n📋 کانال‌های فعلی:")

        for i, channel in enumerate(channels, start=1):
            lines.append(
                f"{i}. {esc(channel.get('title') or channel['chat_id'])}"
            )

    return "\n".join(lines)


def admin_forcejoin_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for channel in get_force_join_channels():
        title = channel.get("title") or str(channel["chat_id"])

        kb.add(
            types.InlineKeyboardButton(
                f"🗑 حذف «{title}»",
                callback_data=f"admin:fj_del:{channel['id']}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "➕ افزودن کانال",
            callback_data="admin:fj_add"
        )
    )

    enabled = is_force_join_enabled()

    kb.add(
        types.InlineKeyboardButton(
            "❌ غیرفعال‌سازی عضویت اجباری" if enabled else "✅ فعال‌سازی عضویت اجباری",
            callback_data="admin:fj_toggle"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:forcejoin"
)
def cb_admin_forcejoin(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    edit_or_send(
        call,
        admin_forcejoin_text(),
        admin_forcejoin_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:fj_toggle"
)
def cb_admin_fj_toggle(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    set_force_join_enabled(
        not is_force_join_enabled()
    )

    bot.answer_callback_query(
        call.id,
        "وضعیت عضویت اجباری تغییر کرد ✅"
    )

    edit_or_send(
        call,
        admin_forcejoin_text(),
        admin_forcejoin_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin:fj_del:")
)
def cb_admin_fj_delete(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:
        channel_row_id = int(call.data.split(":")[-1])
    except ValueError:
        bot.answer_callback_query(call.id, "نامعتبر.", show_alert=True)
        return

    remove_force_join_channel(
        channel_row_id
    )

    bot.answer_callback_query(
        call.id,
        "کانال حذف شد ✅"
    )

    edit_or_send(
        call,
        admin_forcejoin_text(),
        admin_forcejoin_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:fj_add"
)
def cb_admin_fj_add(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    set_admin_state(
        call.from_user.id,
        "forcejoin_add",
        {}
    )

    bot.answer_callback_query(
        call.id
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت",
            callback_data="admin:forcejoin"
        )
    )

    edit_or_send(
        call,
        "➕ <b>افزودن کانال عضویت اجباری</b>\n\n"
        "ربات را ابتدا در کانال موردنظر <b>ادمین</b> کنید، سپس یکی از راه‌های زیر را انجام دهید:\n\n"
        "1️⃣ یک پیام از همان کانال را اینجا <b>فوروارد</b> کنید.\n"
        "2️⃣ یا یوزرنیم کانال را با @ بفرستید (مثال: <code>@mychannel</code>).\n"
        "3️⃣ یا آیدی عددی کانال را بفرستید (مثال: <code>-1001234567890</code>).\n\n"
        "برای لغو /cancel بفرستید.",
        kb
    )


def _resolve_force_join_source(message):
    if getattr(message, "forward_from_chat", None) is not None:
        return message.forward_from_chat.id

    if message.content_type != "text":
        return None

    raw = (message.text or "").strip()

    if not raw:
        return None

    if raw.lstrip("-").isdigit():
        return int(raw)

    if raw.startswith("@"):
        return raw

    if "t.me/" in raw:
        username = raw.split("t.me/")[-1].strip("/").split("?")[0]

        if username:
            return f"@{username}"

    return None


def handle_forcejoin_add_message(message):
    admin_id = message.from_user.id
    chat_ref = _resolve_force_join_source(message)

    if chat_ref is None:
        bot.send_message(
            message.chat.id,
            "❌ متوجه نشدم. یک پیام از کانال فوروارد کنید، یا یوزرنیم/آیدی عددی "
            "کانال را بفرستید؛ یا /cancel بفرستید."
        )
        return

    try:
        chat = bot.get_chat(chat_ref)
    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ کانال پیدا نشد. مطمئن شوید ربات قبلاً به آن کانال اضافه شده "
            "و دوباره تلاش کنید، یا /cancel بفرستید."
        )
        return

    try:
        me = bot.get_me()
        bot_member = bot.get_chat_member(chat.id, me.id)

        if bot_member.status not in ("administrator", "creator"):
            bot.send_message(
                message.chat.id,
                "⚠️ ربات در این کانال عضو است ولی <b>ادمین</b> نیست. "
                "لطفاً ربات را ادمین کانال کنید و دوباره همین پیام را بفرستید.",
                parse_mode="HTML"
            )
            return

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ ربات در این کانال عضو نیست. اول ربات را به کانال اضافه و ادمین کنید، "
            "سپس دوباره تلاش کنید."
        )
        return

    link = None

    if getattr(chat, "username", None):
        link = f"https://t.me/{chat.username}"
    else:
        try:
            link = bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None

    title = (
        getattr(chat, "title", None)
        or (f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id))
    )

    add_force_join_channel(
        chat.id,
        title,
        link,
        added_by=admin_id
    )

    clear_admin_state(admin_id)

    note = "" if link else "\n⚠️ لینک عمومی پیدا نشد؛ کاربران فقط با «بررسی عضویت» چک می‌شوند."

    bot.send_message(
        message.chat.id,
        f"✅ کانال «{esc(title)}» به لیست عضویت اجباری اضافه شد.{note}",
        reply_markup=admin_forcejoin_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN: MAINTENANCE MODE (🛠 خاموش/روشن کردن ربات)
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "admin:maintenance_toggle"
)
def cb_admin_maintenance_toggle(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    new_state = not is_maintenance_mode()
    set_maintenance_mode(new_state)

    bot.answer_callback_query(
        call.id,
        "🛠 ربات خاموش شد (فقط ادمین‌ها دسترسی دارند)." if new_state else "✅ ربات دوباره روشن شد."
    )

    edit_or_send(
        call,
        "🛠 <b>پنل مدیریت</b>\n\n"
        "بخش موردنظر را انتخاب کنید:",
        admin_panel_keyboard(call.from_user.id)
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN: ADMINS MANAGEMENT (🧑‍💼 مدیریت ادمین‌ها)
# ══════════════════════════════════════════════════════════════════════════════

def admin_admins_text():
    lines = [
        "🧑‍💼 <b>مدیریت ادمین‌ها</b>",
        "",
        "لیست فعلی ادمین‌های ربات:",
        ""
    ]

    for admin_id in ADMIN_ID_LIST:
        origin = "پایه (ENV)" if admin_id in ENV_ADMIN_IDS else "افزوده‌شده از پنل"
        star = " 🌹" if admin_id == PROFIT_ADMIN_ID else ""

        lines.append(f"• <code>{admin_id}</code> — {origin}{star}")

    lines.append(
        "\nℹ️ ادمین‌های «پایه» فقط از طریق متغیر محیطی ADMIN_IDS قابل حذف هستند."
    )

    return "\n".join(lines)


def admin_admins_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)

    for admin_id in ADMIN_ID_LIST:
        if admin_id in ENV_ADMIN_IDS:
            continue

        kb.add(
            types.InlineKeyboardButton(
                f"🗑 حذف ادمین {admin_id}",
                callback_data=f"admin:adm_del:{admin_id}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "➕ افزودن ادمین جدید",
            callback_data="admin:adm_add"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:admins"
)
def cb_admin_admins(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(call.from_user.id)

    bot.answer_callback_query(call.id)

    edit_or_send(
        call,
        admin_admins_text(),
        admin_admins_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:adm_add"
)
def cb_admin_adm_add(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    set_admin_state(
        call.from_user.id,
        "admin_add_wait_id",
        {}
    )

    bot.answer_callback_query(call.id)

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت",
            callback_data="admin:admins"
        )
    )

    edit_or_send(
        call,
        "➕ <b>افزودن ادمین جدید</b>\n\n"
        "آیدی عددی کاربر را بفرستید (کاربر باید حداقل یک‌بار ربات را استارت "
        "کرده باشد تا آیدی او برای شما قابل پیدا کردن باشد).\n\n"
        "برای لغو /cancel بفرستید.",
        kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin:adm_del:")
)
def cb_admin_adm_delete(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:
        target_id = int(call.data.split(":")[-1])
    except ValueError:
        bot.answer_callback_query(call.id, "نامعتبر.", show_alert=True)
        return

    if target_id in ENV_ADMIN_IDS:
        bot.answer_callback_query(
            call.id,
            "این ادمین پایه است و از پنل قابل حذف نیست.",
            show_alert=True
        )
        return

    remove_extra_admin(target_id)

    bot.answer_callback_query(
        call.id,
        "ادمین حذف شد ✅"
    )

    edit_or_send(
        call,
        admin_admins_text(),
        admin_admins_keyboard()
    )


def handle_admin_add_message(message):
    admin_id = message.from_user.id

    if message.content_type != "text":
        bot.send_message(
            message.chat.id,
            "لطفاً فقط آیدی عددی را به صورت متن ارسال کنید."
        )
        return

    raw = (message.text or "").translate(DIGIT_TRANSLATION).strip()

    if not raw.lstrip("-").isdigit():
        bot.send_message(
            message.chat.id,
            "❌ آیدی نامعتبر است. فقط عدد بفرستید یا /cancel بزنید."
        )
        return

    target_id = int(raw)

    if target_id in ADMIN_IDS:
        bot.send_message(
            message.chat.id,
            "ℹ️ این کاربر از قبل ادمین است."
        )
    else:
        add_extra_admin(target_id, added_by=admin_id)

        bot.send_message(
            message.chat.id,
            f"✅ کاربر <code>{target_id}</code> به‌عنوان ادمین اضافه شد."
        )

        try:
            bot.send_message(
                target_id,
                "🎉 شما به‌عنوان ادمین ربات اضافه شدید. برای ورود به پنل مدیریت /admin را بفرستید."
            )
        except Exception:
            pass

    clear_admin_state(admin_id)

    bot.send_message(
        message.chat.id,
        admin_admins_text(),
        reply_markup=admin_admins_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN: PRICE EDITING (💲 تغییر قیمت سرویس‌ها)
# ══════════════════════════════════════════════════════════════════════════════

PRICE_MAX = 100_000_000_000


def admin_prices_text():
    return (
        "💲 <b>تغییر قیمت سرویس‌ها</b>\n\n"
        "محصولی که می‌خواهید قیمتش را عوض کنید انتخاب کنید.\n"
        "✏️ کنار محصول یعنی قیمت آن نسبت به قیمت پیش‌فرض تغییر کرده است.\n\n"
        "ℹ️ قیمت جدید فقط برای سفارش‌های بعدی اعمال می‌شود؛ "
        "سفارش‌های قبلی با قیمت زمان خرید باقی می‌مانند."
    )


def admin_prices_keyboard():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    for items in PRODUCTS.values():
        for item in items:
            pid = item["id"]
            product = PRODUCT_INDEX.get(pid) or item
            price = int(product["price"])
            mark = (
                " ✏️"
                if price != DEFAULT_PRICES.get(pid, price)
                else ""
            )

            kb.add(
                types.InlineKeyboardButton(
                    f"{item['title']} — {price:,}{mark}",
                    callback_data=f"admin:price_pick:{pid}"
                )
            )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:prices"
)
def cb_admin_prices(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    edit_or_send(
        call,
        admin_prices_text(),
        admin_prices_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin:price_pick:")
)
def cb_admin_price_pick(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    product_id = call.data.split(":", 2)[-1]
    product = PRODUCT_INDEX.get(product_id)

    if not product:
        bot.answer_callback_query(
            call.id,
            "محصول پیدا نشد.",
            show_alert=True
        )
        return

    set_admin_state(
        call.from_user.id,
        "price_edit",
        {"product_id": product_id}
    )

    bot.answer_callback_query(
        call.id
    )

    default_price = DEFAULT_PRICES.get(product_id)

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    if default_price is not None:
        kb.add(
            types.InlineKeyboardButton(
                f"↩️ بازگشت به قیمت پیش‌فرض ({default_price:,})",
                callback_data=f"admin:price_reset:{product_id}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به لیست محصولات",
            callback_data="admin:prices"
        )
    )

    edit_or_send(
        call,
        f"💲 <b>{esc(product['title'])}</b>\n\n"
        f"قیمت فعلی: <b>{money(product['price'])}</b>\n\n"
        "قیمت جدید را به <b>تومان</b> و فقط به‌صورت عدد بفرستید "
        "(مثال: <code>275000</code>).\n"
        "برای لغو /cancel بفرستید.",
        kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin:price_reset:")
)
def cb_admin_price_reset(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    product_id = call.data.split(":", 2)[-1]

    if product_id not in PRODUCT_INDEX or product_id not in DEFAULT_PRICES:
        bot.answer_callback_query(
            call.id,
            "محصول پیدا نشد.",
            show_alert=True
        )
        return

    reset_product_price(
        product_id
    )

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id,
        "قیمت به حالت پیش‌فرض برگشت ✅"
    )

    edit_or_send(
        call,
        admin_prices_text(),
        admin_prices_keyboard()
    )


def handle_price_edit_message(message, state):
    admin_id = message.from_user.id
    product_id = (state.get("data") or {}).get("product_id")
    product = PRODUCT_INDEX.get(product_id)

    if not product:
        clear_admin_state(admin_id)

        bot.send_message(
            message.chat.id,
            "❌ محصول پیدا نشد. دوباره از «💲 تغییر قیمت سرویس‌ها» شروع کنید.",
            reply_markup=admin_panel_keyboard(admin_id)
        )
        return

    value = (
        parse_amount(message.text)
        if message.content_type == "text"
        else None
    )

    if value is None or value <= 0 or value > PRICE_MAX:
        bot.send_message(
            message.chat.id,
            "❌ قیمت نامعتبر است. فقط یک عدد بزرگ‌تر از صفر بفرستید "
            "(مثال: <code>275000</code>) یا /cancel بفرستید."
        )
        return

    old_price = int(product["price"])

    set_product_price(
        product_id,
        value,
        admin_id
    )

    clear_admin_state(admin_id)

    logger.info(
        "Admin %s changed price of %s: %s -> %s",
        admin_id,
        product_id,
        old_price,
        value
    )

    bot.send_message(
        message.chat.id,
        "✅ <b>قیمت تغییر کرد</b>\n\n"
        f"🛍 {esc(product['title'])}\n"
        f"قبلی: {money(old_price)}\n"
        f"جدید: <b>{money(value)}</b>"
    )

    bot.send_message(
        message.chat.id,
        admin_prices_text(),
        reply_markup=admin_prices_keyboard()
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN: AKHAVAN PROFIT (💐 سود اخوان)
# ══════════════════════════════════════════════════════════════════════════════

AKHAVAN_REPORT_PAGE_SIZE = 8
IRAN_UTC_OFFSET = timedelta(hours=3, minutes=30)


def is_profit_admin(user_id):
    # 💐 سود اخوان حالا برای همه‌ی ادمین‌های ربات در دسترس است،
    # نه فقط «اخوان» (اولین ادمین در ADMIN_IDS).
    return user_id in ADMIN_IDS


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

    gy2 = gy + 1 if gm > 2 else gy

    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + g_d_m[gm - 1]
    )

    jy = -1595 + (33 * (days // 12053))
    days %= 12053

    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)

    return jy, jm, jd


def jalali_datetime_str(iso_value):
    """تاریخ UTC ذخیره‌شده در دیتابیس → «۱۴۰۵/۰۷/۱۱ ۱۹:۰۴» به وقت ایران."""

    try:
        parsed = datetime.fromisoformat(
            str(iso_value)
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        local = parsed.astimezone(timezone.utc) + IRAN_UTC_OFFSET

        jy, jm, jd = gregorian_to_jalali(
            local.year,
            local.month,
            local.day
        )

        return (
            f"{jy:04d}/{jm:02d}/{jd:02d} "
            f"{local.hour:02d}:{local.minute:02d}"
        )

    except Exception:
        return str(iso_value or "")


def akhavan_stats():
    with db() as conn:
        balance = akhavan_current_balance(conn)

        sales = conn.execute(
            """
            SELECT COUNT(*) AS c,
                   COALESCE(SUM(amount), 0) AS s
            FROM akhavan_profit_ledger
            WHERE kind='sale'
            """
        ).fetchone()

        opening = conn.execute(
            """
            SELECT amount, created_at
            FROM akhavan_profit_ledger
            WHERE kind='opening'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()

    return {
        "balance": balance,
        "sales_count": int(sales["c"]),
        "sales_total": int(sales["s"]),
        "opening_amount": int(opening["amount"]) if opening else None,
        "opening_at": opening["created_at"] if opening else None,
    }


def akhavan_rates_lines():
    lines = []

    for pid, amount in (
        AKHAVAN_PROFIT_RATES.get("fixed") or {}
    ).items():
        product = PRODUCT_INDEX.get(pid)
        title = product["title"] if product else pid
        lines.append(
            f"• {esc(title)}: <b>{money(amount)}</b>"
        )

    per_gb = (AKHAVAN_PROFIT_RATES.get("per_gb") or {}).get("v2ray")

    if per_gb:
        lines.append(
            f"• V2Ray (هر گیگ): <b>{money(per_gb)}</b>"
        )

    return lines


def akhavan_panel_text():
    stats = akhavan_stats()

    lines = ["💐 <b>سود اخوان</b>", ""]

    if stats["balance"] is None:
        lines += [
            "⚠️ هنوز مبلغ اولیه تنظیم نشده است.",
            "با دکمه «🏁 تنظیم مبلغ اولیه» عدد شروع را ثبت کنید؛ "
            "از همان لحظه سود هر فروش به آن اضافه می‌شود.",
        ]

    else:
        lines += [
            f"💰 سود فعلی: <b>{money(stats['balance'])}</b>",
            f"🏁 مبلغ اولیه: {money(stats['opening_amount'])} "
            f"({jalali_datetime_str(stats['opening_at'])})",
            f"🛒 تعداد فروش‌های ثبت‌شده: <b>{stats['sales_count']}</b>",
            f"➕ مجموع سود فروش‌ها: <b>{money(stats['sales_total'])}</b>",
        ]

    rates = akhavan_rates_lines()

    if rates:
        lines += ["", "📐 <b>نرخ سود هر فروش:</b>"] + rates

    return "\n".join(lines)


def akhavan_panel_keyboard():
    stats = akhavan_stats()

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "🏁 تنظیم مبلغ اولیه"
            if stats["balance"] is None
            else "✏️ اصلاح مبلغ سود",
            callback_data="akh:set"
        )
    )

    if stats["balance"] is not None:
        kb.add(
            types.InlineKeyboardButton(
                "📋 گزارش تراکنش‌ها",
                callback_data="akh:report:last"
            ),
            types.InlineKeyboardButton(
                "📄 دریافت فایل گزارش کامل",
                callback_data="akh:file"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به پنل",
            callback_data="admin:home"
        )
    )

    return kb


def _akhavan_user_label(row):
    username = row["username"]
    first_name = row["first_name"]
    user_id = row["user_id"]

    if user_id is None:
        return ""

    if username:
        return f"@{username} ({user_id})"

    if first_name:
        return f"{first_name} ({user_id})"

    return str(user_id)


def _akhavan_fetch_rows(offset=0, limit=None):
    query = """
        SELECT l.*,
               u.username AS username,
               u.first_name AS first_name
        FROM akhavan_profit_ledger l
        LEFT JOIN users u ON u.user_id = l.user_id
        ORDER BY l.id
    """

    params = ()

    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params = (limit, offset)

    with db() as conn:
        return conn.execute(query, params).fetchall()


def akhavan_report_page(page):
    with db() as conn:
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM akhavan_profit_ledger"
            ).fetchone()[0]
        )

    pages = max(
        1,
        (total + AKHAVAN_REPORT_PAGE_SIZE - 1) // AKHAVAN_REPORT_PAGE_SIZE
    )

    if page == "last":
        page = pages
    else:
        page = max(1, min(int(page), pages))

    offset = (page - 1) * AKHAVAN_REPORT_PAGE_SIZE

    rows = _akhavan_fetch_rows(
        offset,
        AKHAVAN_REPORT_PAGE_SIZE
    )

    lines = [
        "📋 <b>گزارش سود اخوان</b>",
        f"صفحه {page} از {pages}",
        "",
    ]

    for index, row in enumerate(rows, start=offset + 1):
        when = jalali_datetime_str(row["created_at"])

        if row["kind"] == "opening":
            lines += [
                f"<b>{index})</b> 🏁 مبلغ اولیه — {when}",
                f"     💰 سود: <b>{money(row['balance_after'])}</b>",
            ]

        elif row["kind"] == "set":
            sign = "+" if row["amount"] >= 0 else "-"
            lines += [
                f"<b>{index})</b> ✏️ اصلاح دستی ({sign}{money(abs(row['amount']))}) — {when}",
                f"     💰 سود: <b>{money(row['balance_after'])}</b>",
            ]

        else:
            buyer = _akhavan_user_label(row)

            lines += [
                f"<b>{index})</b> 🛒 سفارش #{row['order_id']} — {esc(row['product_title'] or '')}",
                f"     👤 {esc(buyer)} | 🕒 {when}",
                f"     ➕ {money(row['amount'])} ← سود شد: <b>{money(row['balance_after'])}</b>",
            ]

        lines.append("")

    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    nav = []

    if page > 1:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️ قبلی",
                callback_data=f"akh:report:{page - 1}"
            )
        )

    if page < pages:
        nav.append(
            types.InlineKeyboardButton(
                "بعدی ➡️",
                callback_data=f"akh:report:{page + 1}"
            )
        )

    if nav:
        kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "📄 دریافت فایل گزارش کامل",
            callback_data="akh:file"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به سود اخوان",
            callback_data="admin:akhavan"
        )
    )

    return "\n".join(lines).rstrip(), kb


@bot.callback_query_handler(
    func=lambda c: c.data == "admin:akhavan"
)
def cb_admin_akhavan(call):
    if not is_profit_admin(call.from_user.id):
        bot.answer_callback_query(
            call.id,
            "دسترسی ندارید.",
            show_alert=True
        )
        return

    clear_admin_state(
        call.from_user.id
    )

    bot.answer_callback_query(
        call.id
    )

    edit_or_send(
        call,
        akhavan_panel_text(),
        akhavan_panel_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "akh:set"
)
def cb_akhavan_set(call):
    if not is_profit_admin(call.from_user.id):
        return

    set_admin_state(
        call.from_user.id,
        "akhavan_set",
        {}
    )

    bot.answer_callback_query(
        call.id
    )

    stats = akhavan_stats()

    if stats["balance"] is None:
        prompt = (
            "🏁 <b>تنظیم مبلغ اولیه</b>\n\n"
            "مبلغ شروع سود را به <b>تومان</b> و فقط به‌صورت عدد بفرستید "
            "(مثال: <code>122000</code>).\n"
            "از لحظه‌ی ثبت، سود هر فروش به آن اضافه می‌شود."
        )
    else:
        prompt = (
            "✏️ <b>اصلاح مبلغ سود</b>\n\n"
            f"سود فعلی: <b>{money(stats['balance'])}</b>\n\n"
            "مبلغ درست را به <b>تومان</b> و فقط به‌صورت عدد بفرستید. "
            "این اصلاح در گزارش هم ثبت می‌شود."
        )

    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ بازگشت به سود اخوان",
            callback_data="admin:akhavan"
        )
    )

    edit_or_send(
        call,
        prompt + "\n\nبرای لغو /cancel بفرستید.",
        kb
    )


def handle_akhavan_set_message(message):
    admin_id = message.from_user.id

    if not is_profit_admin(admin_id):
        clear_admin_state(admin_id)
        return

    value = (
        parse_amount(message.text)
        if message.content_type == "text"
        else None
    )

    if value is None or value < 0 or value > PRICE_MAX:
        bot.send_message(
            message.chat.id,
            "❌ مبلغ نامعتبر است. فقط یک عدد (۰ یا بیشتر) بفرستید "
            "(مثال: <code>122000</code>) یا /cancel بفرستید."
        )
        return

    kind, delta = akhavan_set_balance(
        value
    )

    clear_admin_state(admin_id)

    if kind == "opening":
        done = (
            "✅ <b>مبلغ اولیه ثبت شد.</b>\n"
            f"🏁 شروع: <b>{money(value)}</b>\n\n"
            "از همین لحظه سود هر فروش به آن اضافه می‌شود 🌹"
        )
    else:
        sign = "+" if delta >= 0 else "-"
        done = (
            "✅ <b>مبلغ سود اصلاح شد.</b>\n"
            f"💰 سود فعلی: <b>{money(value)}</b> "
            f"({sign}{money(abs(delta))})"
        )

    bot.send_message(
        message.chat.id,
        done
    )

    bot.send_message(
        message.chat.id,
        akhavan_panel_text(),
        reply_markup=akhavan_panel_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("akh:report:")
)
def cb_akhavan_report(call):
    if not is_profit_admin(call.from_user.id):
        return

    raw = call.data.split(":")[-1]

    page = "last" if raw == "last" else raw

    if page != "last" and not page.isdigit():
        bot.answer_callback_query(
            call.id,
            "صفحه نامعتبر است.",
            show_alert=True
        )
        return

    bot.answer_callback_query(
        call.id
    )

    text, kb = akhavan_report_page(
        page
    )

    edit_or_send(
        call,
        text,
        kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "akh:file"
)
def cb_akhavan_file(call):
    if not is_profit_admin(call.from_user.id):
        return

    bot.answer_callback_query(
        call.id,
        "در حال ساخت فایل گزارش..."
    )

    rows = _akhavan_fetch_rows()

    if not rows:
        bot.send_message(
            call.from_user.id,
            "هنوز چیزی برای گزارش ثبت نشده است."
        )
        return

    kind_labels = {
        "opening": "مبلغ اولیه",
        "set": "اصلاح دستی",
        "sale": "فروش",
    }

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M"
    )

    with tempfile.TemporaryDirectory(
        prefix="akhavan_report_"
    ) as directory:
        path = os.path.join(
            directory,
            f"akhavan_profit_report_{stamp}.csv"
        )

        import csv

        with open(
            path,
            "w",
            encoding="utf-8-sig",
            newline=""
        ) as f:
            writer = csv.writer(f)

            writer.writerow([
                "ردیف",
                "تاریخ شمسی (وقت ایران)",
                "تاریخ UTC",
                "نوع",
                "شماره سفارش",
                "محصول",
                "کاربر",
                f"سود این ردیف ({CURRENCY_LABEL})",
                f"سود بعد از ثبت ({CURRENCY_LABEL})",
                "توضیح",
            ])

            for index, row in enumerate(rows, start=1):
                writer.writerow([
                    index,
                    jalali_datetime_str(row["created_at"]),
                    row["created_at"],
                    kind_labels.get(row["kind"], row["kind"]),
                    row["order_id"] or "",
                    row["product_title"] or "",
                    _akhavan_user_label(row),
                    row["amount"],
                    row["balance_after"],
                    row["note"] or "",
                ])

        stats = akhavan_stats()

        with open(path, "rb") as f:
            bot.send_document(
                call.from_user.id,
                f,
                caption=(
                    "📄 <b>گزارش کامل سود اخوان</b>\n"
                    f"🛒 فروش‌های ثبت‌شده: {stats['sales_count']}\n"
                    f"💰 سود فعلی: {money(stats['balance'] or 0)}"
                )
            )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN STATE MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════════════

ADMIN_CONTENT_TYPES = [
    "text",
    "photo",
    "video",
    "document",
    "audio",
    "voice",
    "animation",
    "sticker"
]


@bot.message_handler(
    content_types=ADMIN_CONTENT_TYPES,
    func=lambda m:
        m.from_user.id in ADMIN_IDS
        and get_admin_state(m.from_user.id) is not None
)
def admin_state_message(message):
    state = get_admin_state(
        message.from_user.id
    )

    if not state:
        return

    mode = state["mode"]

    if (
        message.content_type == "text"
        and (message.text or "").strip() == "/cancel"
    ):

        _discard_pending_db_restore(
            state
        )

        clear_admin_state(
            message.from_user.id
        )

        clear_draft_messages(
            message.from_user.id
        )

        bot.send_message(
            message.chat.id,
            "✅ عملیات لغو شد.",
            reply_markup=admin_panel_keyboard(message.from_user.id)
        )

        return

    if mode == "db_restore_wait_file":

        handle_db_restore_upload(
            message
        )

        return

    if mode == "db_restore_confirm":

        bot.send_message(
            message.chat.id,
            "⚠️ برای ادامه از دکمه‌های «تأیید» یا «لغو» پیام قبلی استفاده کنید، "
            "یا /cancel بفرستید."
        )

        return

    if mode == "price_edit":

        handle_price_edit_message(
            message,
            state
        )

        return

    if mode == "forcejoin_add":

        handle_forcejoin_add_message(
            message
        )

        return

    if mode == "admin_add_wait_id":

        handle_admin_add_message(
            message
        )

        return

    if mode == "akhavan_set":

        handle_akhavan_set_message(
            message
        )

        return

    if mode == "guide_add":

        store_connection_content(
            message.from_user.id,
            message,
            state["data"].get("service")
        )

        return

    if mode == "fulfill":

        order_id = int(
            state["data"].get("order_id", 0)
        )
        delivery_mode = (
            state["data"].get("delivery_mode")
            or "manual"
        )
        order = get_order_by_id_for_fulfillment(
            order_id,
            message.from_user.id
        )

        if (
            order
            and service_for_product(order["product_id"]) == "wireguard"
            and delivery_mode != "manual"
        ):
            bot.send_message(
                message.chat.id,
                "ℹ️ تحویل وایرساک / وایرگارد در این حالت اتوماتیک است. "
                "اگر می‌خواهید لینک را خودتان وارد کنید، از گزینه «✋ تحویل دستی» استفاده کنید."
            )
            return

        deliver_order_payload(
            message.from_user.id,
            message
        )

        return

    if mode == "wg_trial_send":

        bot.send_message(
            message.chat.id,
            "ℹ️ تحویل اکانت تست خودکار است؛ لینک را دستی ارسال نکنید. "
            "روی دکمه «تلاش مجدد» پیام خطا بزنید یا از پیام درخواست تست دوباره استفاده کنید."
        )

        return

    if mode == "guide_send_wait_id":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "لطفاً فقط شناسه عددی کاربر را به صورت متن ارسال کنید."
            )

            return

        raw = (
            message.text or ""
        ).translate(
            DIGIT_TRANSLATION
        ).strip()

        if not raw.lstrip("-").isdigit():

            bot.send_message(
                message.chat.id,
                "❌ شناسه معتبر نیست. فقط عدد وارد کنید."
            )

            return

        target_id = int(
            raw
        )

        service = state["data"].get(
            "service"
        )

        if not get_user_row(target_id):

            bot.send_message(
                message.chat.id,
                "⚠️ این کاربر در دیتابیس ربات ثبت نشده است."
            )

            return

        try:

            sent = send_connection_content(
                target_id,
                service
            )

            if sent:

                bot.send_message(
                    message.chat.id,
                    f"✅ آموزش {SERVICE_LABELS.get(service, service)} "
                    f"برای کاربر <code>{target_id}</code> ارسال شد."
                )

            else:

                bot.send_message(
                    message.chat.id,
                    "⚠️ محتوایی برای ارسال وجود نداشت."
                )

        except Exception as exc:

            logger.exception(
                "Failed sending guide to user %s",
                target_id
            )

            if (
                "blocked" in str(exc).lower()
                or "chat not found" in str(exc).lower()
            ):

                set_blocked(
                    target_id,
                    True
                )

            bot.send_message(
                message.chat.id,
                "❌ ارسال آموزش انجام نشد. کاربر ربات را بلاک کرده یا در دسترس نیست."
            )

        clear_admin_state(
            message.from_user.id
        )

        return

    if mode == "wallet_wait_id":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "لطفاً فقط شناسه عددی کاربر را به صورت متن ارسال کنید."
            )

            return

        raw = (
            message.text or ""
        ).translate(
            DIGIT_TRANSLATION
        ).strip()

        if not raw.lstrip("-").isdigit():

            bot.send_message(
                message.chat.id,
                "❌ شناسه معتبر نیست. فقط عدد وارد کنید."
            )

            return

        target_id = int(
            raw
        )

        if show_admin_wallet_panel(
            message.from_user.id,
            target_id
        ):

            clear_admin_state(
                message.from_user.id
            )

        return

    if mode == "wallet_amount":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "لطفاً فقط مبلغ را به صورت متن ارسال کنید."
            )

            return

        target_id = int(
            state["data"].get(
                "user_id",
                0
            )
        )

        operation = state["data"].get(
            "op",
            "add"
        )

        amount = parse_amount(
            message.text
        )

        if amount is None:

            bot.send_message(
                message.chat.id,
                "❌ مبلغ معتبر نیست.\n"
                "فقط عدد ارسال کنید. مثال: <code>200000</code>"
            )

            return

        amount = abs(
            amount
        )

        if operation in (
            "add",
            "sub"
        ) and amount == 0:

            bot.send_message(
                message.chat.id,
                "❌ مبلغ صفر قابل قبول نیست."
            )

            return

        ok, old_balance, new_balance = apply_balance_change(
            target_id,
            operation,
            amount,
            message.from_user.id
        )

        if not ok:

            bot.send_message(
                message.chat.id,
                "⚠️ کاربر پیدا نشد."
            )

            clear_admin_state(
                message.from_user.id
            )

            return

        clamp_note = ""

        if (
            operation == "sub"
            and old_balance - amount < 0
        ):
            clamp_note = (
                "\n⚠️ مبلغ درخواستی بیشتر از موجودی کاربر بود؛ "
                "موجودی روی صفر تنظیم شد."
            )

        op_label = {
            "add": "افزایش موجودی",
            "sub": "کاهش موجودی",
            "set": "تنظیم موجودی"
        }.get(
            operation,
            "تغییر موجودی"
        )

        bot.send_message(
            message.chat.id,
            f"✅ <b>{op_label} انجام شد</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 موجودی قبلی: <b>{money(old_balance)}</b>\n"
            f"💰 موجودی جدید: <b>{money(new_balance)}</b>"
            f"{clamp_note}"
        )

        if old_balance != new_balance:

            notify_user_balance_change(
                target_id,
                operation,
                old_balance,
                new_balance
            )

        clear_admin_state(
            message.from_user.id
        )

        show_admin_wallet_panel(
            message.from_user.id,
            target_id
        )

        return

    if mode == "send_user_wait_id":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "لطفاً فقط ID عددی کاربر را به صورت متن ارسال کنید."
            )

            return

        raw = (
            message.text or ""
        ).translate(
            DIGIT_TRANSLATION
        ).strip()

        if not raw.lstrip("-").isdigit():

            bot.send_message(
                message.chat.id,
                "❌ شناسه معتبر نیست. فقط عدد وارد کنید."
            )

            return

        target_id = int(
            raw
        )

        with db() as conn:

            exists = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id=?
                """,
                (target_id,)
            ).fetchone()

        if not exists:

            bot.send_message(
                message.chat.id,
                "⚠️ این کاربر در دیتابیس ربات ثبت نشده است."
            )

            return

        set_admin_state(
            message.from_user.id,
            "send_user_wait_message",
            {
                "user_id": target_id
            }
        )

        bot.send_message(
            message.chat.id,
            f"📨 حالا پیام موردنظر برای کاربر "
            f"<code>{target_id}</code> را ارسال کنید."
        )

        return

    if mode == "send_user_wait_message":

        target_id = int(
            state["data"]["user_id"]
        )

        try:

            bot.copy_message(
                target_id,
                message.chat.id,
                message.message_id
            )

            bot.send_message(
                message.chat.id,
                f"✅ پیام برای کاربر "
                f"<code>{target_id}</code> ارسال شد."
            )

            clear_admin_state(
                message.from_user.id
            )

        except Exception as exc:

            logger.exception(
                "Failed sending admin message to user %s",
                target_id
            )

            if (
                "blocked" in str(exc).lower()
                or "chat not found" in str(exc).lower()
            ):

                set_blocked(
                    target_id,
                    True
                )

            bot.send_message(
                message.chat.id,
                "❌ ارسال پیام انجام نشد."
            )

        return

    if mode == "broadcast_text":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "برای ارسال همگانی، یک پیام متنی ارسال کنید."
            )

            return

        broadcast_text(
            message.from_user.id,
            message
        )

        clear_admin_state(
            message.from_user.id
        )

        return

    if mode == "broadcast_forward":

        broadcast_copy(
            message.from_user.id,
            message
        )

        clear_admin_state(
            message.from_user.id
        )

        return

    if mode == "admin_new_gmail":

        if message.content_type != "text":

            bot.send_message(
                message.chat.id,
                "لطفاً آدرس جیمیل را به صورت متن ارسال کنید."
            )

            return

        new_gmail = (
            message.text or ""
        ).strip()

        service_id = int(
            state["data"]["service_id"]
        )

        admin_assign_new_gmail(
            message.from_user.id,
            service_id,
            new_gmail
        )

        clear_admin_state(
            message.from_user.id
        )

        return

    if mode == "admin_support_reply":

        draft_chat_id, existing_msgs = get_draft_messages(
            message.from_user.id
        )

        target_chat_id = state["data"].get(
            "support_chat_id"
        )

        msg_data = _extract_message_data(
            message
        )

        if msg_data:

            existing_msgs.append(
                msg_data
            )

            save_draft_messages(
                message.from_user.id,
                target_chat_id,
                existing_msgs
            )

            bot.send_message(
                message.chat.id,
                f"✅ پیام اضافه شد "
                f"({len(existing_msgs)} پیام). "
                "برای ارسال روی دکمه «ارسال پیام‌ها» بزنید."
            )

        return


@bot.message_handler(
    commands=["cancel"]
)
def cancel_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    state = get_admin_state(
        message.from_user.id
    )

    clear_admin_state(
        message.from_user.id
    )

    clear_draft_messages(
        message.from_user.id
    )

    if state:

        bot.send_message(
            message.chat.id,
            "✅ عملیات لغو شد.",
            reply_markup=admin_panel_keyboard(message.from_user.id)
        )


# ══════════════════════════════════════════════════════════════════════════════
# USER TEXT ROUTER
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(
    content_types=["text"]
)
def user_text_router(message):
    ensure_user(
        message.from_user
    )

    if (
        message.from_user.id in ADMIN_IDS
        and get_admin_state(message.from_user.id)
    ):
        return

    if not ensure_joined(message):
        return

    nav = get_nav(
        message.from_user.id
    )

    if nav["state"] == "support_sending":

        draft_chat_id, existing_msgs = get_draft_messages(
            message.from_user.id
        )

        support_chat_id = nav["data"].get(
            "support_chat_id"
        )

        msg_data = _extract_message_data(
            message
        )

        if msg_data:

            existing_msgs.append(
                msg_data
            )

            save_draft_messages(
                message.from_user.id,
                support_chat_id,
                existing_msgs
            )

            kb = types.InlineKeyboardMarkup(
                row_width=1
            )

            kb.add(
                types.InlineKeyboardButton(
                    "✅ ارسال به پشتیبانی",
                    callback_data=f"send_support_msgs:{support_chat_id}"
                )
            )

            bot.send_message(
                message.chat.id,
                f"✅ پیام اضافه شد "
                f"({len(existing_msgs)} پیام). "
                "پیام‌های بیشتر بفرستید یا ارسال کنید.",
                reply_markup=kb
            )

        return

    if nav["state"] == "personal_ai_gmail":

        _handle_personal_ai_gmail(
            message,
            nav
        )

        return

    if nav["state"] == "awaiting_service_username":

        _handle_service_username(
            message,
            nav
        )

        return

    if nav["state"] == "wireguard_trial_username":

        _handle_wireguard_trial_username(
            message,
            nav
        )

        return

    text = (
        message.text or ""
    ).strip()

    if text == "🖥 خرید سرور":

        show_server_menu(
            message.chat.id,
            message.from_user.id
        )

    elif text == "🤖 اشتراک هوش مصنوعی":

        show_ai_menu(
            message.chat.id,
            message.from_user.id
        )

    elif text == "🔗 نحوه اتصال":

        open_connection_for_user(
            message.chat.id,
            message.from_user.id
        )

    elif text == "🎧 پشتیبانی":

        save_nav(
            message.from_user.id,
            "support",
            "home",
            {}
        )

        kb = types.InlineKeyboardMarkup(
            row_width=1
        )

        kb.add(
            types.InlineKeyboardButton(
                "🎧 ارتباط با پشتیبانی",
                url=support_url()
            )
        )

        bot.send_message(
            message.chat.id,
            "🎧 <b>پشتیبانی</b>\n\n"
            "برای ارتباط با پشتیبانی روی دکمه زیر بزنید.",
            reply_markup=kb
        )

    elif text == "ℹ️ درباره ما":

        save_nav(
            message.from_user.id,
            "about",
            "home",
            {}
        )

        bot.send_message(
            message.chat.id,
            f"ℹ️ <b>درباره ما</b>\n\n"
            f"{esc(ABOUT_TEXT)}",
            reply_markup=reply_back_keyboard()
        )

    elif text == "💰 موجودی":

        current = get_nav(
            message.from_user.id
        )

        current_state = current.get(
            "state",
            "home"
        )

        if current_state != "balance":

            save_nav(
                message.from_user.id,
                "balance",
                current_state,
                current.get(
                    "data",
                    {}
                )
            )

        balance = get_balance(
            message.from_user.id
        )

        reserved = get_reserved_total(
            message.from_user.id
        )

        reserved_line = ""

        if reserved > 0:
            reserved_line = (
                f"\n🔒 رزروشده روی سفارش‌های باز: <b>{money(reserved)}</b>"
                "\n(اگر سفارش لغو یا رد شود، این مبلغ به موجودی شما برمی‌گردد)"
            )

        hint = (
            "\n\n🛍 هنگام خرید، موجودی کیف پول به‌صورت خودکار از قیمت سرویس "
            "کم می‌شود و فقط مابه‌التفاوت را واریز می‌کنید."
            if balance > 0
            else ""
        )

        bot.send_message(
            message.chat.id,
            f"💰 موجودی شما: <b>{money(balance)}</b>"
            f"{reserved_line}"
            f"{hint}",
            reply_markup=reply_back_keyboard()
        )

    elif text == "📊 وضعیت سرور":

        show_server_status_menu(
            message.chat.id,
            message.from_user.id
        )

    elif text == "📦 سرویس های من":

        show_my_services(
            message.chat.id,
            message.from_user.id
        )

    elif text == "🏠 منوی اصلی":

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif text == "⬅️ بازگشت":

        navigate_back(
            message
        )

    else:

        if nav["state"] == "payment":

            bot.send_message(
                message.chat.id,
                "📤 لطفاً تصویر یا فایل فیش را ارسال کنید."
            )

        else:

            bot.send_message(
                message.chat.id,
                "لطفاً یکی از گزینه‌های منوی پایین صفحه را انتخاب کنید.",
                reply_markup=reply_main_keyboard()
            )


def _handle_personal_ai_gmail(
    message,
    nav
):
    gmail = (
        message.text or ""
    ).strip()

    if (
        "@" not in gmail
        or "." not in gmail
    ):

        bot.send_message(
            message.chat.id,
            "❌ آدرس جیمیل وارد شده معتبر نیست. "
            "لطفاً یک آدرس ایمیل صحیح وارد کنید "
            "(مثال: example@gmail.com):"
        )

        return

    product_id = nav["data"].get(
        "product_id",
        "chatgpt-plus-personal"
    )

    product = PRODUCT_INDEX.get(
        product_id
    )

    if not product:

        bot.send_message(
            message.chat.id,
            "❌ محصول پیدا نشد.",
            reply_markup=reply_main_keyboard()
        )

        return

    with db() as conn:

        existing = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE user_id=?
            AND product_id=?
            AND status IN (
                'awaiting_payment',
                'awaiting_receipt',
                'receipt_submitted',
                'paid_pending_fulfillment',
                'fulfilling'
            )
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                message.from_user.id,
                product_id
            )
        ).fetchone()

    if existing:

        bot.send_message(
            message.chat.id,
            f"⚠️ برای این محصول سفارش باز "
            f"#{existing['id']} دارید.",
            reply_markup=reply_main_keyboard()
        )

        return

    try:

        order_id = create_order(
            message.from_user.id,
            product,
            gmail_address=gmail
        )

    except Exception:

        logger.exception(
            "Failed creating personal AI order"
        )

        bot.send_message(
            message.chat.id,
            "❌ خطا در ثبت سفارش.",
            reply_markup=reply_main_keyboard()
        )

        return

    save_nav(
        message.from_user.id,
        "invoice",
        "ai_personal",
        {
            "order_id": order_id,
            "product_id": product_id
        }
    )

    bot.send_message(
        message.chat.id,
        f"✅ جیمیل <b>{esc(gmail)}</b> ثبت شد.\n\n"
        "اکنون پیش‌فاکتور زیر را بررسی کنید:",
        reply_markup=reply_back_keyboard()
    )

    bot.send_message(
        message.chat.id,
        invoice_text(
            order_id,
            product
        ),
        reply_markup=invoice_keyboard(
            order_id
        )
    )


def get_last_service_username(
    user_id,
    service,
    exclude_order_id=None
):
    """
    آخرین نام کاربری‌ای که کاربر با آن خرید موفق (تکمیل‌شده) برای همین نوع
    سرویس (وایرساک یا V2Ray) داشته است.
    """

    if service not in (
        "wireguard",
        "v2ray"
    ):
        return None

    with db() as conn:

        row = conn.execute(
            """
            SELECT service_username
            FROM orders
            WHERE user_id=?
            AND status='completed'
            AND product_id LIKE ?
            AND service_username IS NOT NULL
            AND TRIM(service_username) <> ''
            AND id <> ?
            ORDER BY COALESCE(completed_at, updated_at) DESC, id DESC
            LIMIT 1
            """,
            (
                user_id,
                f"{service}-%",
                int(exclude_order_id or 0)
            )
        ).fetchone()

    if not row:
        return None

    return (
        row["service_username"] or ""
    ).strip() or None


def send_service_username_prompt(
    chat_id,
    user_id,
    order,
    intro=""
):
    """
    درخواست نام کاربری سرویس.

    اگر کاربر قبلاً با یک نام کاربری خرید موفق داشته، آن نام کاربری همراه با
    دکمه‌های «بله / خیر» نمایش داده می‌شود؛ وگرنه همان پیام همیشگی ارسال می‌شود.
    """

    service = service_for_product(
        order["product_id"]
    )

    last_username = get_last_service_username(
        user_id,
        service,
        exclude_order_id=order["id"]
    )

    if not last_username:

        bot.send_message(
            chat_id,
            f"{intro}{SERVICE_USERNAME_PROMPT}",
            reply_markup=reply_back_keyboard()
        )

        return

    # نام کاربری پیشنهادی را در nav نگه می‌داریم تا با زدن «بله» دقیقاً همان
    # مقداری که به کاربر نشان داده شد ثبت شود.
    nav = get_nav(
        user_id
    )

    nav_data = dict(
        nav["data"]
    )

    nav_data["suggested_username"] = last_username

    save_nav(
        user_id,
        nav["state"],
        nav["parent_state"],
        nav_data
    )

    if intro.strip():

        bot.send_message(
            chat_id,
            intro.strip(),
            reply_markup=reply_back_keyboard()
        )

    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.row(
        types.InlineKeyboardButton(
            "✅ بله، همین است",
            callback_data=f"reuse_uname:yes:{order['id']}"
        ),
        types.InlineKeyboardButton(
            "❌ خیر",
            callback_data=f"reuse_uname:no:{order['id']}"
        )
    )

    bot.send_message(
        chat_id,
        "👤 <b>آیا دفعه پیش با این نام کاربری خرید کرده بودید؟</b>\n\n"
        f"<b>{esc(last_username)}</b>\n\n"
        "اگر بله، همین نام کاربری برای این سفارش هم ثبت می‌شود.\n"
        "اگر خیر، دکمه «❌ خیر» را بزنید و نام کاربری موردنظر را ارسال کنید.",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith(
        "reuse_uname:"
    )
)
def cb_reuse_username(call):
    parts = call.data.split(
        ":"
    )

    action = (
        parts[1]
        if len(parts) > 1
        else ""
    )

    try:

        order_id = int(
            parts[2]
        )

    except (
        IndexError,
        TypeError,
        ValueError
    ):

        bot.answer_callback_query(
            call.id
        )

        return

    user_id = call.from_user.id

    nav = get_nav(
        user_id
    )

    data = nav["data"]

    if (
        nav["state"] != "awaiting_service_username"
        or int(data.get("order_id", 0)) != order_id
    ):

        bot.answer_callback_query(
            call.id,
            "این درخواست دیگر معتبر نیست.",
            show_alert=True
        )

        return

    # دکمه‌ها بعد از اولین انتخاب حذف می‌شوند تا دوباره زده نشوند.
    try:

        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    except Exception:
        pass

    if action == "yes":

        username = (
            data.get("suggested_username") or ""
        ).strip()

        if not username:

            bot.answer_callback_query(
                call.id,
                "نام کاربری قبلی پیدا نشد؛ لطفاً نام کاربری را ارسال کنید.",
                show_alert=True
            )

            return

        bot.answer_callback_query(
            call.id
        )

        _process_service_username(
            user_id,
            call.message.chat.id,
            username,
            data
        )

        return

    bot.answer_callback_query(
        call.id
    )

    if action == "no":

        bot.send_message(
            call.message.chat.id,
            "👤 پس نام کاربری مورد نظر خود را ارسال کنید:",
            reply_markup=reply_back_keyboard()
        )


def _handle_service_username(
    message,
    nav
):
    username = (
        message.text or ""
    ).strip()

    if (
        not username
        or len(username) > 64
    ):

        bot.send_message(
            message.chat.id,
            "❌ نام کاربری نامعتبر است. "
            "لطفاً یک نام کاربری معتبر ارسال کنید:"
        )

        return

    _process_service_username(
        message.from_user.id,
        message.chat.id,
        username,
        nav["data"]
    )


def _process_service_username(
    user_id,
    chat_id,
    username,
    nav_data
):
    order_id = int(
        nav_data.get(
            "order_id",
            0
        )
    )

    with db() as conn:

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND user_id=?
            """,
            (
                order_id,
                user_id
            )
        ).fetchone()

    wallet_only = bool(
        nav_data.get(
            "wallet_only"
        )
    )

    valid_receipt_order = (
        order
        and order["status"] == "awaiting_receipt"
        and order["receipt_file_id"]
    )

    valid_wallet_order = (
        order
        and wallet_only
        and order["status"] == "awaiting_payment"
        and int(order["cash_required"]) <= 0
    )

    if not (valid_receipt_order or valid_wallet_order):

        bot.send_message(
            chat_id,
            "❌ سفارش معتبری برای ثبت نام کاربری پیدا نشد.",
            reply_markup=reply_main_keyboard()
        )

        save_nav(
            user_id,
            "home",
            None,
            {}
        )

        return

    # ── خرید کامل با کیف پول (بدون فیش) ───────────────────────────────────────
    if valid_wallet_order:

        with db() as conn:

            cur = conn.execute(
                """
                UPDATE orders
                SET service_username=?,
                    updated_at=?
                WHERE id=?
                AND status='awaiting_payment'
                """,
                (
                    username,
                    now(),
                    order_id
                )
            )

            if cur.rowcount != 1:

                bot.send_message(
                    chat_id,
                    "این سفارش قبلاً تغییر کرده است."
                )

                return

        save_nav(
            user_id,
            "home",
            None,
            {}
        )

        bot.send_message(
            chat_id,
            f"✅ نام کاربری <b>{esc(username)}</b> ثبت شد.",
            reply_markup=reply_main_keyboard()
        )

        if not finalize_wallet_only_order(
            order_id,
            chat_id
        ):

            bot.send_message(
                chat_id,
                "❌ نهایی کردن سفارش انجام نشد. لطفاً با پشتیبانی تماس بگیرید."
            )

        return

    # ── خرید با فیش واریزی ────────────────────────────────────────────────────
    with db() as conn:

        cur = conn.execute(
            """
            UPDATE orders
            SET service_username=?,
                status='receipt_submitted',
                updated_at=?
            WHERE id=?
            AND status='awaiting_receipt'
            """,
            (
                username,
                now(),
                order_id
            )
        )

        if cur.rowcount != 1:

            bot.send_message(
                chat_id,
                "این سفارش قبلاً تغییر کرده است."
            )

            return

    save_nav(
        user_id,
        "home",
        None,
        {}
    )

    bot.send_message(
        chat_id,
        f"✅ نام کاربری <b>{esc(username)}</b> ثبت شد.\n\n"
        f"فیش و نام کاربری سفارش #{order_id} برای بررسی ادمین ارسال شد.\n"
        "پس از بررسی، نتیجه برای شما ارسال می‌شود.",
        reply_markup=reply_main_keyboard()
    )

    notify_admins_receipt(
        order_id
    )


def _handle_wireguard_trial_username(
    message,
    nav
):
    username = (
        message.text or ""
    ).strip()

    if (
        not username
        or len(username) > 64
    ):

        bot.send_message(
            message.chat.id,
            "❌ نام کاربری نامعتبر است. "
            "لطفاً یک نام کاربری معتبر ارسال کنید:"
        )

        return

    if has_wireguard_trial(
        message.from_user.id
    ):

        save_nav(
            message.from_user.id,
            "home",
            None,
            {}
        )

        bot.send_message(
            message.chat.id,
            "❌ شما قبلاً از اکانت تست وایرساک استفاده کرده‌اید.\n"
            "هر اکانت تلگرام فقط یک‌بار می‌تواند اکانت تست دریافت کند.",
            reply_markup=reply_main_keyboard()
        )

        return

    created = create_wireguard_trial(
        message.from_user.id,
        username
    )

    if not created:

        save_nav(
            message.from_user.id,
            "home",
            None,
            {}
        )

        bot.send_message(
            message.chat.id,
            "❌ شما قبلاً از اکانت تست وایرساک استفاده کرده‌اید.\n"
            "هر اکانت تلگرام فقط یک‌بار می‌تواند اکانت تست دریافت کند.",
            reply_markup=reply_main_keyboard()
        )

        return

    save_nav(
        message.from_user.id,
        "home",
        None,
        {}
    )

    bot.send_message(
        message.chat.id,
        f"✅ درخواست اکانت تست با نام کاربری "
        f"<b>{esc(username)}</b> ثبت شد.\n"
        "به‌زودی توسط پشتیبانی برای شما ارسال می‌شود.",
        reply_markup=reply_main_keyboard()
    )

    notify_admins_wireguard_trial(
        message.from_user.id,
        username
    )


# ══════════════════════════════════════════════════════════════════════════════
# RECEIPT / MEDIA HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(
    content_types=["photo", "document"]
)
def receipt_or_draft_handler(message):
    ensure_user(
        message.from_user
    )

    if message.from_user.id in ADMIN_IDS:

        state = get_admin_state(
            message.from_user.id
        )

        if (
            state
            and state["mode"] == "admin_support_reply"
        ):

            draft_chat_id, existing_msgs = get_draft_messages(
                message.from_user.id
            )

            target_chat_id = state["data"].get(
                "support_chat_id"
            )

            msg_data = _extract_message_data(
                message
            )

            if msg_data:

                existing_msgs.append(
                    msg_data
                )

                save_draft_messages(
                    message.from_user.id,
                    target_chat_id,
                    existing_msgs
                )

                bot.send_message(
                    message.chat.id,
                    f"✅ پیام اضافه شد "
                    f"({len(existing_msgs)} پیام)."
                )

            return

        if state:
            return

    if not is_member(
        message.from_user.id
    ):

        ensure_joined(
            message
        )

        return

    nav = get_nav(
        message.from_user.id
    )

    if nav["state"] == "support_sending":

        draft_chat_id, existing_msgs = get_draft_messages(
            message.from_user.id
        )

        support_chat_id = nav["data"].get(
            "support_chat_id"
        )

        msg_data = _extract_message_data(
            message
        )

        if msg_data:

            existing_msgs.append(
                msg_data
            )

            save_draft_messages(
                message.from_user.id,
                support_chat_id,
                existing_msgs
            )

            kb = types.InlineKeyboardMarkup(
                row_width=1
            )

            kb.add(
                types.InlineKeyboardButton(
                    "✅ ارسال به پشتیبانی",
                    callback_data=f"send_support_msgs:{support_chat_id}"
                )
            )

            bot.send_message(
                message.chat.id,
                f"✅ پیام اضافه شد "
                f"({len(existing_msgs)} پیام).",
                reply_markup=kb
            )

        return

    with db() as conn:

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE user_id=?
            AND status='awaiting_receipt'
            ORDER BY id DESC
            LIMIT 1
            """,
            (message.from_user.id,)
        ).fetchone()

    if not order:

        bot.send_message(
            message.chat.id,
            "برای این پیام سفارش در حال پرداختی ندارید.",
            reply_markup=reply_main_keyboard()
        )

        return

    if message.content_type == "photo":

        file_id = (
            message.photo[-1].file_id
        )

        rtype = "photo"

    else:

        file_id = (
            message.document.file_id
        )

        rtype = "document"

    needs_username = service_for_product(
        order["product_id"]
    ) in (
        "wireguard",
        "v2ray"
    )

    if needs_username:

        with db() as conn:

            cur = conn.execute(
                """
                UPDATE orders
                SET receipt_file_id=?,
                    receipt_type=?,
                    updated_at=?
                WHERE id=?
                AND status='awaiting_receipt'
                """,
                (
                    file_id,
                    rtype,
                    now(),
                    order["id"]
                )
            )

            if cur.rowcount != 1:

                bot.send_message(
                    message.chat.id,
                    "این سفارش قبلاً تغییر کرده است."
                )

                return

        save_nav(
            message.from_user.id,
            "awaiting_service_username",
            "payment",
            {
                "order_id": order["id"]
            }
        )

        send_service_username_prompt(
            message.chat.id,
            message.from_user.id,
            order,
            intro=f"✅ فیش سفارش #{order['id']} دریافت شد.\n\n"
        )

        return

    with db() as conn:

        cur = conn.execute(
            """
            UPDATE orders
            SET receipt_file_id=?,
                receipt_type=?,
                status='receipt_submitted',
                updated_at=?
            WHERE id=?
            AND status='awaiting_receipt'
            """,
            (
                file_id,
                rtype,
                now(),
                order["id"]
            )
        )

        if cur.rowcount != 1:

            bot.send_message(
                message.chat.id,
                "این سفارش قبلاً تغییر کرده است."
            )

            return

    bot.send_message(
        message.chat.id,
        f"✅ فیش سفارش #{order['id']} دریافت شد.\n"
        "پس از بررسی، نتیجه برای شما ارسال می‌شود.",
        reply_markup=reply_back_keyboard()
    )

    notify_admins_receipt(
        order["id"]
    )


@bot.message_handler(
    content_types=[
        "audio",
        "voice",
        "video",
        "animation",
        "sticker"
    ]
)
def media_fallback(message):
    ensure_user(
        message.from_user
    )

    if (
        message.from_user.id in ADMIN_IDS
        and get_admin_state(message.from_user.id)
    ):
        return

    if not is_member(
        message.from_user.id
    ):

        ensure_joined(
            message
        )

        return

    nav = get_nav(
        message.from_user.id
    )

    if nav["state"] == "support_sending":

        draft_chat_id, existing_msgs = get_draft_messages(
            message.from_user.id
        )

        support_chat_id = nav["data"].get(
            "support_chat_id"
        )

        msg_data = _extract_message_data(
            message
        )

        if msg_data:

            existing_msgs.append(
                msg_data
            )

            save_draft_messages(
                message.from_user.id,
                support_chat_id,
                existing_msgs
            )

            kb = types.InlineKeyboardMarkup(
                row_width=1
            )

            kb.add(
                types.InlineKeyboardButton(
                    "✅ ارسال به پشتیبانی",
                    callback_data=f"send_support_msgs:{support_chat_id}"
                )
            )

            bot.send_message(
                message.chat.id,
                f"✅ پیام اضافه شد "
                f"({len(existing_msgs)} پیام).",
                reply_markup=kb
            )

        return

    if nav["state"] == "payment":

        bot.send_message(
            message.chat.id,
            "📤 این پیام نوع مناسبی برای فیش نیست. "
            "لطفاً عکس فیش یا فایل فیش را ارسال کنید."
        )

    else:

        bot.send_message(
            message.chat.id,
            "لطفاً از گزینه‌های منوی پایین صفحه استفاده کنید.",
            reply_markup=reply_main_keyboard()
        )


# ══════════════════════════════════════════════════════════════════════════════
# NAVIGATION
# ══════════════════════════════════════════════════════════════════════════════

def navigate_back(message):
    nav = get_nav(
        message.from_user.id
    )

    state = nav["state"]
    parent = nav["parent_state"] or "home"
    data = nav.get(
        "data",
        {}
    )

    if state == "home":

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif (
        state == "server"
        or (
            parent == "home"
            and state == "server"
        )
    ):

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif state in (
        "wireguard",
        "v2ray"
    ):

        show_server_menu(
            message.chat.id,
            message.from_user.id
        )

    elif state == "ai":

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif state == "chatgpt":

        show_ai_menu(
            message.chat.id,
            message.from_user.id
        )

    elif state in (
        "ai_shared",
        "ai_personal"
    ):

        bot.send_message(
            message.chat.id,
            "🤖 <b>هوش مصنوعی ChatGPT</b>\n\n"
            "نوع اشتراک موردنظر را انتخاب کنید:",
            reply_markup=inline_chatgpt_menu()
        )

        save_nav(
            message.from_user.id,
            "chatgpt",
            "ai",
            {}
        )

    elif state == "personal_ai_gmail":

        show_ai_menu(
            message.chat.id,
            message.from_user.id
        )

    elif state == "product_info":

        back = parent

        if back == "wireguard":

            show_products_from_user(
                message,
                "wireguard"
            )

        elif back == "v2ray":

            show_products_from_user(
                message,
                "v2ray"
            )

        else:

            show_ai_context_from_user(
                message,
                back
            )

    elif state == "invoice":

        order_id = int(
            data.get(
                "order_id",
                0
            )
        )

        product_id = data.get(
            "product_id"
        )

        cancelled = cancel_unpaid_order(
            order_id,
            message.from_user.id
        )

        if cancelled:

            back_state = product_back_state(
                product_id or ""
            )

            save_nav(
                message.from_user.id,
                back_state,
                (
                    "server"
                    if back_state in (
                        "wireguard",
                        "v2ray"
                    )
                    else "ai"
                ),
                {}
            )

            bot.send_message(
                message.chat.id,
                "↩️ سفارش لغو شد و موجودی رزروشده شما آزاد شد."
            )

            if back_state == "wireguard":

                show_products_from_user(
                    message,
                    "wireguard"
                )

            elif back_state == "v2ray":

                show_products_from_user(
                    message,
                    "v2ray"
                )

            else:

                show_ai_context_from_user(
                    message,
                    back_state
                )

        else:

            send_home(
                message.chat.id,
                message.from_user.id,
                greeting=False
            )

    elif state == "payment":

        order_id = int(
            data.get(
                "order_id",
                0
            )
        )

        with db() as conn:

            order = conn.execute(
                """
                SELECT *
                FROM orders
                WHERE id=?
                AND user_id=?
                """,
                (
                    order_id,
                    message.from_user.id
                )
            ).fetchone()

        if order and order["status"] in (
            "awaiting_receipt",
            "receipt_submitted"
        ):

            product = PRODUCT_INDEX.get(
                order["product_id"]
            )

            save_nav(
                message.from_user.id,
                "invoice",
                product_back_state(
                    order["product_id"]
                ),
                {
                    "order_id": order_id,
                    "product_id": order["product_id"]
                }
            )

            bot.send_message(
                message.chat.id,
                invoice_text(
                    order_id,
                    product
                ),
                reply_markup=invoice_keyboard(
                    order_id
                )
            )

        else:

            send_home(
                message.chat.id,
                message.from_user.id,
                greeting=False
            )

    elif state == "service_info":

        service = data.get(
            "service"
        )

        if service in (
            "wireguard",
            "v2ray"
        ):

            show_products_from_user(
                message,
                service
            )

        else:

            send_home(
                message.chat.id,
                message.from_user.id,
                greeting=False
            )

    elif state == "awaiting_service_username":

        order_id = int(
            data.get(
                "order_id",
                0
            )
        )

        wallet_only = bool(
            data.get(
                "wallet_only"
            )
        )

        cancelled = False

        if wallet_only and order_id:

            # سفارشی که هنوز نهایی نشده لغو می‌شود تا موجودی رزروشده آزاد شود.
            cancelled = cancel_unpaid_order(
                order_id,
                message.from_user.id
            )

        if cancelled:

            bot.send_message(
                message.chat.id,
                "↩️ سفارش لغو شد و موجودی رزروشده شما آزاد شد."
            )

        else:

            bot.send_message(
                message.chat.id,
                "⚠️ سفارش شما هنوز نهایی نشده است، چون نام کاربری سرویس ثبت نشده.\n"
                "برای ادامه، دوباره فیش را ارسال کنید یا با «🎧 پشتیبانی» تماس بگیرید."
            )

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif state == "connection":

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif state == "connection_service":

        save_nav(
            message.from_user.id,
            "connection",
            "home",
            {}
        )

        bot.send_message(
            message.chat.id,
            CONNECTION_MENU_TEXT,
            reply_markup=connection_menu_keyboard()
        )

    elif state == "balance":

        if parent == "server":

            show_server_menu(
                message.chat.id,
                message.from_user.id
            )

        elif parent == "ai":

            show_ai_menu(
                message.chat.id,
                message.from_user.id
            )

        else:

            send_home(
                message.chat.id,
                message.from_user.id,
                greeting=False
            )

    elif state in (
        "support",
        "about"
    ):

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    elif state in (
        "my_services",
        "service_detail",
        "renew_invoice",
        "support_sending"
    ):

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )

    else:

        send_home(
            message.chat.id,
            message.from_user.id,
            greeting=False
        )


def inline_chatgpt_menu():
    kb = types.InlineKeyboardMarkup(
        row_width=1
    )

    kb.add(
        types.InlineKeyboardButton(
            "⭐ اکانت Plus — روی جیمیل شخصی",
            callback_data="ai:personal"
        ),
        types.InlineKeyboardButton(
            "👥 اکانت اشتراکی یک ماهه",
            callback_data="ai:shared"
        )
    )

    return kb


def show_products_from_user(
    message,
    service
):
    save_nav(
        message.from_user.id,
        service,
        "server",
        {}
    )

    if service == "wireguard":

        items = wireguard_items()

        text = (
            "🎮 <b>تحریم‌گذر گیمینگ وایرساک</b>\n\n"
            "⏱ اعتبار: <b>یک ماهه</b>\n"
            "ℹ️ نوع: <b>سرویس وایرساک / وایرگارد</b>\n\n"
            "برای خرید، حجم موردنظر را انتخاب کنید."
        )

        info_cb = (
            "service_info:wireguard"
        )

    else:

        items = v2ray_items()

        text = (
            "🛜 <b>سرویس V2Ray مولتی‌لوکیشن</b>\n\n"
            "⏱ اعتبار: <b>نامحدود زمان ♾️</b>\n\n"
            "برای خرید، حجم موردنظر را انتخاب کنید."
        )

        info_cb = (
            "service_info:v2ray"
        )

    kb = types.InlineKeyboardMarkup(
        row_width=2
    )

    kb.add(
        types.InlineKeyboardButton(
            "ℹ️ اطلاعات سرویس",
            callback_data=info_cb
        )
    )

    for p in items:

        kb.row(
            types.InlineKeyboardButton(
                f"💰 {money(p['price'])}",
                callback_data=f"buy:{p['id']}"
            ),
            types.InlineKeyboardButton(
                f"📦 {p['volume']}",
                callback_data=f"info:{p['id']}"
            )
        )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=kb
    )


def show_ai_context_from_user(
    message,
    state
):
    if state == "chatgpt":

        bot.send_message(
            message.chat.id,
            "🤖 <b>هوش مصنوعی ChatGPT</b>\n\n"
            "نوع اشتراک موردنظر را انتخاب کنید:",
            reply_markup=inline_chatgpt_menu()
        )

        save_nav(
            message.from_user.id,
            "chatgpt",
            "ai",
            {}
        )

    elif state == "ai_shared":

        items = [
            p
            for p in PRODUCTS.get("ai", [])
            if p["id"].startswith("chatgpt-shared-")
        ]

        kb = types.InlineKeyboardMarkup(
            row_width=1
        )

        for p in items:

            kb.add(
                types.InlineKeyboardButton(
                    f"🛒 {p['volume']} — {money(p['price'])}",
                    callback_data=f"buy:{p['id']}"
                )
            )

        bot.send_message(
            message.chat.id,
            "👥 <b>ChatGPT Plus — اکانت اشتراکی یک ماهه</b>\n\n"
            "نوع اکانت را انتخاب کنید:",
            reply_markup=kb
        )

        save_nav(
            message.from_user.id,
            "ai_shared",
            "chatgpt",
            {}
        )

    else:

        bot.send_message(
            message.chat.id,
            "🤖 <b>هوش مصنوعی ChatGPT</b>\n\n"
            "نوع اشتراک موردنظر را انتخاب کنید:",
            reply_markup=inline_chatgpt_menu()
        )

        save_nav(
            message.from_user.id,
            "chatgpt",
            "ai",
            {}
        )


# ══════════════════════════════════════════════════════════════════════════════
# EXPIRY CHECK
# ══════════════════════════════════════════════════════════════════════════════

def check_expiry_loop():
    while True:

        try:

            expiring = get_services_expiring_soon()

            for svc in expiring:

                try:

                    kb = types.InlineKeyboardMarkup(
                        row_width=1
                    )

                    kb.add(
                        types.InlineKeyboardButton(
                            "🔄 تمدید سرویس",
                            callback_data=f"renew_service:{svc['id']}"
                        )
                    )

                    days_left = ""

                    if svc["expires_at"]:

                        expires_dt = datetime.fromisoformat(
                            svc["expires_at"]
                        )

                        now_dt = datetime.now(
                            timezone.utc
                        )

                        diff = (
                            expires_dt
                            - now_dt
                        )

                        days_left = (
                            f"({max(0, diff.days)} روز دیگر)"
                        )

                    stype_label = service_type_label(
                        svc
                    )

                    bot.send_message(
                        svc["user_id"],
                        "⚠️ <b>سرویس شما در حال انقضاست!</b>\n\n"
                        f"📦 سرویس: <b>{stype_label}</b>\n"
                        f"⏳ تاریخ انقضا: "
                        f"<b>{svc['expires_at'][:10]}</b> "
                        f"{days_left}\n\n"
                        "برای جلوگیری از قطع سرویس، همین الان تمدید کنید:",
                        reply_markup=kb
                    )

                    mark_expiry_notified(
                        svc["id"]
                    )

                except Exception:

                    logger.exception(
                        "Failed sending expiry notification for service #%s",
                        svc["id"]
                    )

            expired = get_expired_services()

            for svc in expired:

                try:

                    expire_service(
                        svc["id"]
                    )

                    stype_label = service_type_label(
                        svc
                    )

                    kb = types.InlineKeyboardMarkup(
                        row_width=1
                    )

                    if svc["service_type"] == TRIAL_SERVICE_TYPE:

                        kb.add(
                            types.InlineKeyboardButton(
                                "🛒 خرید سرویس کامل",
                                callback_data="srv:wireguard"
                            )
                        )

                        bot.send_message(
                            svc["user_id"],
                            "❌ <b>اکانت تست شما منقضی شد!</b>\n\n"
                            f"📦 سرویس: <b>{stype_label}</b>\n"
                            f"📅 تاریخ انقضا: "
                            f"<b>{svc['expires_at'][:10]}</b>\n\n"
                            "اکانت تست فقط یک‌بار و برای مدت کوتاه ارائه می‌شود.\n"
                            "برای ادامه استفاده، سرویس کامل تهیه کنید:",
                            reply_markup=kb
                        )

                    else:

                        kb.add(
                            types.InlineKeyboardButton(
                                "🔄 تمدید سرویس",
                                callback_data=f"renew_service:{svc['id']}"
                            )
                        )

                        bot.send_message(
                            svc["user_id"],
                            "❌ <b>سرویس شما منقضی شد!</b>\n\n"
                            f"📦 سرویس: <b>{stype_label}</b>\n"
                            f"📅 تاریخ انقضا: "
                            f"<b>{svc['expires_at'][:10]}</b>\n\n"
                            "برای استفاده مجدد، سرویس خود را تمدید کنید:",
                            reply_markup=kb
                        )

                except Exception:

                    logger.exception(
                        "Failed processing expired service #%s",
                        svc["id"]
                    )

        except Exception:

            logger.exception(
                "Error in expiry check loop"
            )

        time.sleep(
            3600
        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

class _HealthHandler(BaseHTTPRequestHandler):

    def _respond(self):
        body = b"ok"

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        self._respond()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header(
            "Content-Length",
            "0"
        )
        self.end_headers()

    def log_message(self, *args):
        return


def start_health_server():
    """
    A Telegram long-polling bot does not need an HTTP port, but most PaaS
    providers health-check a TCP port and restart (or never mark healthy)
    a container that does not listen. This tiny server satisfies that.
    """

    try:

        server = ThreadingHTTPServer(
            ("0.0.0.0", PORT),
            _HealthHandler
        )

    except OSError:

        logger.exception(
            "Could not bind health server on port %s",
            PORT
        )

        return

    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()

    logger.info(
        "Health server listening on 0.0.0.0:%s",
        PORT
    )


def main():
    start_health_server()

    init_db()

    reload_admin_ids()

    applied_prices = load_price_overrides()

    logger.info(
        "Loaded %s custom product price(s) | profit admin=%s",
        applied_prices,
        PROFIT_ADMIN_ID
    )

    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS is empty. No admin will receive notifications."
        )

    try:

        me = bot.get_me()

        logger.info(
            "Connected to Telegram as @%s (id=%s)",
            me.username,
            me.id
        )

    except Exception as exc:

        logger.error(
            "Cannot reach Telegram API. "
            "Check BOT_TOKEN and outbound network access: %s",
            exc
        )

    try:

        bot.remove_webhook()

        time.sleep(1)

        logger.info(
            "Webhook removed; using long polling."
        )

    except Exception as exc:

        logger.warning(
            "remove_webhook failed: %s",
            exc
        )

    logger.info(
        "Bot started | admins=%s | force_join_channels=%s (enabled=%s) | products=%s",
        sorted(ADMIN_IDS),
        [c["chat_id"] for c in get_force_join_channels()],
        is_force_join_enabled(),
        len(PRODUCT_INDEX)
    )

    expiry_thread = threading.Thread(
        target=check_expiry_loop,
        daemon=True
    )

    expiry_thread.start()

    while True:

        try:

            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as exc:

            logger.exception(
                "Polling crashed (%s); restarting in 5s",
                exc
            )

            time.sleep(
                5
            )


if __name__ == "__main__":
    main()
