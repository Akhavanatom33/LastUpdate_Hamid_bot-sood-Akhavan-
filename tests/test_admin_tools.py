import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from test_order_dates import (  # noqa: E402
    ADMIN_ID,
    NOW,
    USER_ID,
    OrderDatesBase,
    callback_datas,
    iso,
)

import bot  # noqa: E402


class AdminToolsBase(OrderDatesBase):
    def add_order(
        self,
        status="completed",
        created=None,
        completed=None,
        price=100000,
        product_id="chatgpt-shared-5",
        title="ChatGPT اشتراکی ۵ نفره",
        user_id=USER_ID,
        assigned_admin_id=None,
    ):
        created = created or (NOW - timedelta(days=1))
        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders(
                    user_id, product_id, product_title, price, status,
                    assigned_admin_id, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    product_id,
                    title,
                    price,
                    status,
                    assigned_admin_id,
                    iso(created),
                    iso(created),
                    iso(completed) if completed else None,
                ),
            )
            return int(cur.lastrowid)

    def call(self, data, user=ADMIN_ID):
        return SimpleNamespace(
            id="1",
            data=data,
            from_user=SimpleNamespace(id=user),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=user), message_id=10
            ),
        )

    def last_edit_text(self):
        return bot.bot.edit_message_text.call_args[0][0]


class PanelLayoutTests(AdminToolsBase):
    def test_panel_has_all_new_buttons(self):
        data = callback_datas(bot.admin_panel_keyboard(ADMIN_ID, ADMIN_ID))
        for expected in (
            "admin:faq",
            "admin:order_dates",
            "admin:user_profile",
            "admin:expiring",
            "admin:pending_orders",
            "admin:sales",
        ):
            self.assertIn(expected, data)


class UserProfileTests(AdminToolsBase):
    def test_resolve_target_user(self):
        self.assertEqual(
            bot.resolve_admin_target_user("555")["user_id"], USER_ID
        )
        self.assertEqual(
            bot.resolve_admin_target_user("۵۵۵")["user_id"], USER_ID
        )
        self.assertEqual(
            bot.resolve_admin_target_user("@BUYER")["user_id"], USER_ID
        )
        self.assertEqual(
            bot.resolve_admin_target_user("buyer")["user_id"], USER_ID
        )
        self.assertIsNone(bot.resolve_admin_target_user("999999"))
        self.assertIsNone(bot.resolve_admin_target_user("@nobody"))
        self.assertIsNone(bot.resolve_admin_target_user("x y; DROP"))
        self.assertIsNone(bot.resolve_admin_target_user(""))
        self.assertIsNone(bot.resolve_admin_target_user(None))

    def test_profile_contents(self):
        done = NOW - timedelta(days=7)
        o1 = self.add_order(created=done, completed=done)
        o2 = self.add_order(
            created=NOW - timedelta(days=2),
            completed=NOW - timedelta(days=2),
            price=50000,
        )
        o3 = self.add_order(status="awaiting_receipt")
        self.make_service(o1, done, done + timedelta(days=30))
        self.make_service(
            o2, NOW - timedelta(days=40), NOW - timedelta(days=10)
        )

        text, ids = bot.admin_user_profile(USER_ID, now_dt=NOW)

        self.assertEqual(ids, [o3, o2, o1])
        self.assertIn("Buyer", text)
        self.assertIn("@buyer", text)
        self.assertIn("<code>555</code>", text)
        self.assertIn("کل سفارش‌ها: <b>3</b>", text)
        self.assertIn("تکمیل‌شده: 2 سفارش", text)
        self.assertIn("150,000", text)  # 100000 + 50000
        self.assertIn("منتظر فیش: 1", text)
        self.assertIn(f"#{o1}", text)
        self.assertIn(bot.jalali_datetime_str(iso(done)), text)
        self.assertIn("23 روز", text)  # سرویس فعال
        self.assertIn("منقضی (10 روز پیش)", text)  # سرویس منقضی

    def test_profile_for_unknown_user_is_none(self):
        self.assertIsNone(bot.admin_user_profile(424242))

    def test_profile_lookup_flow(self):
        order_id = self.add_order(completed=NOW - timedelta(days=1))
        bot.set_admin_state(ADMIN_ID, "user_profile_lookup", {})

        bot.admin_state_message(self.admin_message("nobody_here"))
        self.assertEqual(
            bot.get_admin_state(ADMIN_ID)["mode"], "user_profile_lookup"
        )
        self.assertIn("پیدا نشد", self.sent[-1][1])

        bot.admin_state_message(self.admin_message("@buyer"))
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

        _chat, text, kwargs = self.sent[-1]
        self.assertIn("پروفایل کاربر", text)
        data = callback_datas(kwargs["reply_markup"])
        self.assertIn(f"admin:od:{order_id}", data)
        self.assertIn(f"admin:uprof_wallet:{USER_ID}", data)

    def test_order_shortcut_and_wallet_shortcut(self):
        order_id = self.add_order(completed=NOW - timedelta(days=1))

        bot.cb_admin_order_dates_shortcut(self.call(f"admin:od:{order_id}"))
        self.assertIn(f"تاریخ سفارش #{order_id}", self.sent[-1][1])

        count = len(self.sent)
        bot.cb_admin_order_dates_shortcut(
            self.call(f"admin:od:{order_id}", user=USER_ID)
        )
        self.assertEqual(len(self.sent), count)  # غیرادمین

        bot.cb_admin_order_dates_shortcut(self.call("admin:od:9999"))
        self.assertEqual(len(self.sent), count)  # سفارش ناموجود → هشدار

        with mock.patch.object(bot, "show_admin_wallet_panel") as wallet:
            bot.cb_admin_profile_wallet(
                self.call(f"admin:uprof_wallet:{USER_ID}")
            )
            wallet.assert_called_once_with(ADMIN_ID, USER_ID)


class ExpiringTests(AdminToolsBase):
    def test_sections(self):
        def svc(days_from_now, product="p"):
            order_id = self.add_order()
            self.make_service(
                order_id,
                NOW - timedelta(days=30),
                NOW + timedelta(days=days_from_now)
                if days_from_now is not None
                else None,
            )
            return order_id

        soon = svc(3)
        far = svc(20)
        just_expired = svc(-2)
        long_expired = svc(-30)
        unlimited = svc(None)

        text = bot.admin_expiring_text(now_dt=NOW)

        upcoming, expired = text.split("منقضی‌شده در 7 روز اخیر")
        self.assertIn(f"سفارش #{soon}", upcoming)
        self.assertIn("3 روز مانده", upcoming)  # ۳ روز تا انقضا
        self.assertNotIn(f"سفارش #{far})", text)
        self.assertNotIn(f"سفارش #{long_expired})", text)
        self.assertNotIn(f"سفارش #{unlimited})", text)
        self.assertIn(f"سفارش #{just_expired}", expired)
        self.assertIn("منقضی (2 روز پیش)", expired)
        self.assertIn("@buyer (555)", text)

    def test_empty_and_overflow(self):
        text = bot.admin_expiring_text(now_dt=NOW)
        self.assertEqual(text.count("— موردی نیست —"), 2)

        for _ in range(bot.ADMIN_TOOLS_LIST_LIMIT + 3):
            order_id = self.add_order()
            self.make_service(
                order_id, NOW - timedelta(days=25), NOW + timedelta(days=5)
            )

        text = bot.admin_expiring_text(now_dt=NOW)
        self.assertIn("و 3 مورد دیگر", text)
        self.assertLess(len(text), 4096)

    def test_callback_renders_and_clears_state(self):
        bot.set_admin_state(ADMIN_ID, "order_dates_lookup", {})
        bot.cb_admin_expiring(self.call("admin:expiring"))
        self.assertIn("سرویس‌های رو به انقضا", self.last_edit_text())
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

        bot.bot.edit_message_text.reset_mock()
        bot.cb_admin_expiring(self.call("admin:expiring", user=USER_ID))
        bot.bot.edit_message_text.assert_not_called()


class PendingOrdersTests(AdminToolsBase):
    def test_only_actionable_orders_oldest_first(self):
        old = self.add_order(
            status="paid_pending_fulfillment",
            created=NOW - timedelta(days=2, hours=3),
        )
        newer = self.add_order(
            status="receipt_submitted", created=NOW - timedelta(hours=5)
        )
        busy = self.add_order(
            status="fulfilling",
            created=NOW - timedelta(days=1),
            assigned_admin_id=ADMIN_ID,
        )
        for ignored in (
            "completed", "rejected", "cancelled",
            "awaiting_receipt", "awaiting_payment",
        ):
            self.add_order(status=ignored)

        text, ids = bot.admin_pending_orders_text(now_dt=NOW)

        self.assertEqual(ids, [old, busy, newer])
        self.assertIn("تعداد کل: <b>3</b>", text)
        self.assertIn("2 روز و 3 ساعت انتظار", text)
        self.assertIn("5 ساعت انتظار", text)
        self.assertIn("آماده تحویل", text)
        self.assertIn("فیش ارسال شده", text)
        self.assertIn(f"<code>{ADMIN_ID}</code>", text)
        self.assertNotIn("رد شده", text)

    def test_empty_queue(self):
        text, ids = bot.admin_pending_orders_text(now_dt=NOW)
        self.assertEqual(ids, [])
        self.assertIn("همه‌ی سفارش‌ها رسیدگی شده‌اند", text)

    def test_callback_has_order_shortcuts(self):
        order_id = self.add_order(status="paid_pending_fulfillment")
        bot.cb_admin_pending_orders(self.call("admin:pending_orders"))

        markup = bot.bot.edit_message_text.call_args.kwargs["reply_markup"]
        data = callback_datas(markup)
        self.assertIn(f"admin:od:{order_id}", data)
        self.assertIn("admin:pending_orders", data)
        self.assertIn("admin:home", data)


class SalesReportTests(AdminToolsBase):
    def setUp(self):
        super().setUp()
        # NOW = ۲۷ مهر ۱۴۰۵ ۱۵:۳۰ تهران؛ نیمه‌شب تهران = 20:30 UTC روز قبل
        self.today_a = self.add_order(
            created=NOW,
            completed=datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc),
            price=100000,
        )
        self.today_b = self.add_order(
            created=NOW,
            completed=NOW - timedelta(hours=1),
            price=50000,
            product_id="wg",
            title="وایرگارد",
        )
        # ۲۳:۳۰ تهران دیروز → هنوز «امروز» نیست
        self.yesterday = self.add_order(
            created=NOW,
            completed=datetime(2026, 10, 18, 20, 0, tzinfo=timezone.utc),
            price=70000,
        )
        self.ten_days = self.add_order(
            created=NOW,
            completed=NOW - timedelta(days=10),
            price=30000,
        )
        self.old = self.add_order(
            created=NOW,
            completed=NOW - timedelta(days=45),
            price=999000,
        )
        self.add_order(status="rejected", price=888000)

    def test_today_boundary_uses_iran_midnight(self):
        text = bot.admin_sales_report("today", now_dt=NOW)

        self.assertIn("گزارش فروش — امروز", text)
        self.assertIn("از 1405/07/27", text)
        self.assertIn("سفارش‌های تکمیل‌شده: <b>2</b>", text)
        self.assertIn("150,000", text)
        self.assertIn("میانگین هر سفارش: 75,000", text)
        self.assertNotIn("به تفکیک روز", text)

    def test_week_and_month(self):
        week = bot.admin_sales_report("7", now_dt=NOW)
        self.assertIn("سفارش‌های تکمیل‌شده: <b>3</b>", week)
        self.assertIn("220,000", week)  # 100k+50k+70k
        self.assertIn("1405/07/27: 2 سفارش", week)
        self.assertIn("1405/07/26: 1 سفارش", week)

        month = bot.admin_sales_report("30", now_dt=NOW)
        self.assertIn("سفارش‌های تکمیل‌شده: <b>4</b>", month)
        self.assertIn("250,000", month)
        self.assertNotIn("999,000", month)  # ۴۵ روز پیش
        self.assertNotIn("888,000", month)  # ردشده

        # به تفکیک محصول: ChatGPT ۳ سفارش، وایرگارد ۱
        self.assertIn("1. ChatGPT اشتراکی ۵ نفره — 3 سفارش", month)
        self.assertIn("2. وایرگارد — 1 سفارش", month)

    def test_empty_period(self):
        with bot.db() as conn:
            conn.execute("DELETE FROM orders")
        text = bot.admin_sales_report("today", now_dt=NOW)
        self.assertIn("سفارشی تکمیل نشده", text)

    def test_unknown_period_falls_back_to_today(self):
        text = bot.admin_sales_report("bogus", now_dt=NOW)
        self.assertIn("امروز", text)

    def test_callback_switches_period(self):
        bot.cb_admin_sales(self.call("admin:sales:30"))
        self.assertIn("۳۰ روز اخیر", self.last_edit_text())

        markup = bot.bot.edit_message_text.call_args.kwargs["reply_markup"]
        self.assertEqual(
            sorted(
                d for d in callback_datas(markup) if d.startswith("admin:sales")
            ),
            ["admin:sales:30", "admin:sales:7", "admin:sales:today"],
        )

        bot.cb_admin_sales(self.call("admin:sales"))
        self.assertIn("امروز", self.last_edit_text())

        bot.bot.edit_message_text.reset_mock()
        bot.cb_admin_sales(self.call("admin:sales:7", user=USER_ID))
        bot.bot.edit_message_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
