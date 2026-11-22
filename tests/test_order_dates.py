"""
پایه‌ی تست‌های ابزارهای ادمین (📅 تاریخ سفارشات و دوستانش).

این فایل علاوه بر تست‌های خودش، Fixture مشترکی می‌سازد که
`tests/test_admin_tools.py` از آن استفاده می‌کند:

    ADMIN_ID, USER_ID, NOW, iso(), callback_datas(), OrderDatesBase

`NOW` عمداً روی ۲۷ مهر ۱۴۰۵ ساعت ۱۵:۳۰ به وقت تهران قفل شده است
(= 2026-10-19T12:00:00Z) تا مرز روز شمسی و محاسبه‌ی «چند روز مانده»
قابل پیش‌بینی و تکرارپذیر باشد.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault(
    "DB_PATH",
    os.path.join(tempfile.gettempdir(), "arena-bot-import.sqlite3"),
)

import bot  # noqa: E402


ADMIN_ID = 111
USER_ID = 555

# ۱۴۰۵/۰۷/۲۷ — ۱۵:۳۰ تهران
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    """datetime → همان قالبی که ربات داخل دیتابیس می‌نویسد."""

    if dt is None:
        return None

    return dt.isoformat(timespec="seconds")


def callback_datas(markup):
    """همه‌ی callback_dataهای یک InlineKeyboardMarkup."""

    if markup is None:
        return []

    return [
        button.callback_data
        for row in getattr(markup, "keyboard", [])
        for button in row
    ]


class OrderDatesBase(unittest.TestCase):
    """
    هر تست یک دیتابیس موقت تازه می‌گیرد و `bot.bot` را با Mock عوض می‌کند
    تا هیچ پیامی واقعاً به تلگرام نرود.

    همه‌ی پیام‌های ارسالی داخل `self.sent` به شکل
    `(chat_id, text, kwargs)` جمع می‌شوند.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self.db_path = os.path.join(self._tmp.name, "test.sqlite3")

        self._db_patch = mock.patch.object(bot, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

        bot.init_db()

        self.sent = []

        fake_bot = mock.MagicMock()

        def _send(chat_id, text, *args, **kwargs):
            self.sent.append((chat_id, text, kwargs))
            return SimpleNamespace(message_id=len(self.sent))

        fake_bot.send_message.side_effect = _send
        fake_bot.send_document.side_effect = _send
        fake_bot.send_photo.side_effect = _send
        fake_bot.send_video.side_effect = _send
        fake_bot.copy_message.side_effect = _send

        self._bot_patch = mock.patch.object(bot, "bot", fake_bot)
        self._bot_patch.start()
        self.addCleanup(self._bot_patch.stop)

        # «اکنون» در کل تست ثابت می‌ماند.
        self._now_patch = mock.patch.object(
            bot,
            "now",
            lambda: iso(NOW),
        )
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)

        self.add_user(USER_ID, "buyer", "Buyer")
        self.add_user(ADMIN_ID, "adminshop", "Admin")

        self._admins_patch = mock.patch.object(bot, "ADMIN_IDS", {ADMIN_ID})
        self._admins_patch.start()
        self.addCleanup(self._admins_patch.stop)

    # ── سازنده‌های داده ──────────────────────────────────────────────────────

    def add_user(self, user_id, username=None, first_name=None, balance=0):
        with bot.db() as conn:
            conn.execute(
                """
                INSERT INTO users(
                    user_id, username, first_name, balance,
                    is_blocked, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name
                """,
                (
                    user_id,
                    username,
                    first_name or "",
                    balance,
                    iso(NOW - timedelta(days=60)),
                    iso(NOW),
                ),
            )
        return user_id

    def add_order(
        self,
        status="completed",
        created=None,
        completed=None,
        price=100000,
        product_id="wireguard-50gb-1m",
        title="وایرساک / وایرگارد — ۵۰ گیگ ۱ ماهه",
        user_id=USER_ID,
        assigned_admin_id=None,
        gmail_address=None,
        service_username=None,
        renewal_for_service=None,
        wallet_reserved=0,
        cash_required=None,
    ):
        created = created or (NOW - timedelta(days=1))

        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders(
                    user_id, product_id, product_title, price,
                    wallet_reserved, cash_required, status,
                    assigned_admin_id, gmail_address, service_username,
                    renewal_for_service, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    product_id,
                    title,
                    price,
                    wallet_reserved,
                    price - wallet_reserved
                    if cash_required is None
                    else cash_required,
                    status,
                    assigned_admin_id,
                    gmail_address,
                    service_username,
                    renewal_for_service,
                    iso(created),
                    iso(completed or created),
                    iso(completed),
                ),
            )
            return int(cur.lastrowid)

    def make_service(
        self,
        order_id,
        started,
        expires,
        user_id=USER_ID,
        service_type="wireguard",
        product_id="wireguard-50gb-1m",
        product_title="وایرساک / وایرگارد — ۵۰ گیگ ۱ ماهه",
        gmail_address=None,
        service_username=None,
        is_expired=None,
        renewal_order_id=None,
    ):
        if is_expired is None:
            is_expired = 1 if (expires and expires <= NOW) else 0

        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO active_services(
                    user_id, order_id, service_type, product_id,
                    product_title, gmail_address, service_username,
                    started_at, expires_at, is_expired, expiry_notified,
                    renewal_order_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    user_id,
                    order_id,
                    service_type,
                    product_id,
                    product_title,
                    gmail_address,
                    service_username,
                    iso(started),
                    iso(expires),
                    is_expired,
                    renewal_order_id,
                ),
            )
            return int(cur.lastrowid)

    def add_transaction(self, user_id, order_id, kind, amount, note=""):
        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO transactions(
                    user_id, order_id, kind, amount, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, order_id, kind, amount, note, iso(NOW)),
            )
            return int(cur.lastrowid)

    # ── ابزارهای تماس ────────────────────────────────────────────────────────

    def call(self, data, user=ADMIN_ID):
        return SimpleNamespace(
            id="1",
            data=data,
            from_user=SimpleNamespace(id=user),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=user, type="private"), message_id=10
            ),
        )

    def admin_message(self, text, user=ADMIN_ID, content_type="text"):
        return SimpleNamespace(
            content_type=content_type,
            text=text if content_type == "text" else None,
            from_user=SimpleNamespace(
                id=user, username="adminshop", first_name="Admin"
            ),
            chat=SimpleNamespace(id=user, type="private"),
            message_id=99,
        )

    def user_message(self, text, user=USER_ID):
        return self.admin_message(text, user=user)

    def last_edit_text(self):
        return bot.bot.edit_message_text.call_args[0][0]


# ══════════════════════════════════════════════════════════════════════════════
# 📅 تاریخ سفارشات
# ══════════════════════════════════════════════════════════════════════════════

class ParseOrderNumberTests(OrderDatesBase):
    def test_accepts_plain_and_prefixed_numbers(self):
        for text, expected in (
            ("20", 20),
            ("#20", 20),
            ("سفارش ۲۰", 20),
            ("۴۸", 48),
            ("  #48  ", 48),
        ):
            with self.subTest(text=text):
                self.assertEqual(bot.parse_order_number(text), expected)

    def test_rejects_garbage(self):
        for text in ("", None, "abc", "0", "1" * 15):
            with self.subTest(text=text):
                self.assertIsNone(bot.parse_order_number(text))

    def test_takes_first_number_in_text(self):
        # «-3» یا «سفارش ۱۲ لغو شد» → همان عدد داخل متن؛ این رفتار عمدی است
        # چون ادمین معمولاً شماره را داخل یک جمله می‌فرستد.
        self.assertEqual(bot.parse_order_number("-3"), 3)
        self.assertEqual(bot.parse_order_number("سفارش ۱۲ را ببین"), 12)


class OrderDatesReportTests(OrderDatesBase):
    def test_unknown_order_returns_none(self):
        self.assertIsNone(bot.order_dates_report(4242))

    def test_completed_order_shows_period_and_remaining(self):
        created = NOW - timedelta(days=7)
        order_id = self.add_order(created=created, completed=created)
        self.make_service(order_id, created, created + timedelta(days=30))

        text = bot.order_dates_report(order_id, now_dt=NOW)

        self.assertIn(f"تاریخ سفارش #{order_id}", text)
        self.assertIn("وایرساک / وایرگارد — ۵۰ گیگ ۱ ماهه", text)
        self.assertIn("تکمیل شده", text)
        self.assertIn("23 روز", text)
        self.assertIn(bot.jalali_datetime_str(iso(created)), text)

    def test_unpaid_order_has_no_period(self):
        order_id = self.add_order(status="awaiting_payment")

        text = bot.order_dates_report(order_id, now_dt=NOW)

        self.assertIn("در انتظار پرداخت", text)
        self.assertIn("هنوز تحویل نشده", text)

    def test_rejected_order_says_not_delivered(self):
        order_id = self.add_order(status="rejected")

        text = bot.order_dates_report(order_id, now_dt=NOW)

        self.assertIn("تحویل نشده است", text)

    def test_lookup_flow_reports_and_clears_state(self):
        order_id = self.add_order()
        bot.set_admin_state(ADMIN_ID, "order_dates_lookup", {})

        bot.handle_order_dates_message(self.admin_message("not a number"))
        self.assertIn("نامعتبر", self.sent[-1][1])
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

        bot.handle_order_dates_message(self.admin_message("9999"))
        self.assertIn("پیدا نشد", self.sent[-1][1])
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

        bot.handle_order_dates_message(self.admin_message(f"#{order_id}"))
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))
        self.assertIn(f"تاریخ سفارش #{order_id}", self.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
