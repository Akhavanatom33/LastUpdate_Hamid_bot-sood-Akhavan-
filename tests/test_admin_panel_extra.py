"""
تست‌های قابلیت‌های جدید پنل ادمین:

  🚨 صف تحویل‌های ناموفق
  📝 یادداشت ادمین روی سفارش
  ⚡ اقدامات سریع (ارسال دوباره، تحویل دستی، مرجوعی، تمدید دستی، تلاش مجدد)
  🛠 عملیات پنل روی سرویس (وضعیت زنده، ریست ترافیک، فعال/غیرفعال، ابطال لینک)
  📤 خروجی CSV
  🔔 خلاصه‌ی روزانه‌ی ادمین

اصل مشترک همه‌ی این تست‌ها: ربات باید **صادق** باشد — اگر کاری انجام نشده
(پنل قبول نکرد، چیزی برای ارسال نبود، مبلغی پرداخت نشده) همان را بگوید و
الکی «انجام شد» نگوید.
"""

import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from eylanpanel_api import EylanPanelError  # noqa: E402


# ساعت ایران در لحظه‌ی NOW: ۱۵:۳۰
IRAN_HOUR_AT_NOW = 15


class FakeEylanClient:
    """کلاینت جعلی پنل؛ فقط همان متدهایی که ابزارهای ادمین صدا می‌زنند."""

    def __init__(self, users=None):
        self.users = dict(users or {})
        self.calls = []
        self.update_fails = False
        self.verify_fails = False
        self.active = True

    # ── خواندن ────────────────────────────────────────────────────────────
    def get_user_status(self, username):
        self.calls.append(("get_user", username))

        if username not in self.users:
            raise EylanPanelError(f"user '{username}' was not found")

        record = dict(self.users[username])
        record.setdefault("username", username)
        record.setdefault("is_active", self.active)
        return record

    # ── نوشتن ─────────────────────────────────────────────────────────────
    def update_user(self, username, payload):
        self.calls.append(("update", username, dict(payload)))

        if self.update_fails:
            raise EylanPanelError("HTTP 500 panel down")

        new_expiry = payload.get("expiry_date_str")
        if new_expiry:
            self.users.setdefault(username, {})
            self.users[username]["expiry_date_actual_iso"] = (
                f"{new_expiry}T23:59:59+00:00"
            )

        return {"success": True, "message": "updated"}

    def verify_expiry_extended(self, username, *, expected_date):
        self.calls.append(("verify", username))

        if self.verify_fails:
            raise EylanPanelError("تمدید اعمال نشد")

        return {"success": True}

    def reset_traffic(self, username):
        self.calls.append(("reset", username))
        return {
            "success": True,
            "message": "traffic reset",
            "user_re_enabled": True,
        }

    def toggle_user(self, username):
        self.calls.append(("toggle", username))
        self.active = not self.active
        return {
            "success": True,
            "message": "toggled",
            "is_active": self.active,
        }

    def revoke_subscription(self, username):
        self.calls.append(("revoke", username))
        return f"https://panel.test/sub/NEW/{username}"


class AdminExtraBase(OrderDatesBase):
    """پایه‌ی مشترک: کلید API روشن و سود اخوان بی‌صدا (تا تست‌ها شبکه نزنند)."""

    def setUp(self):
        super().setUp()

        self._key_patch = mock.patch.object(bot, "EYLAN_API_KEY", "test-key")
        self._key_patch.start()
        self.addCleanup(self._key_patch.stop)

        # ثبت سود اخوان داخل complete_order به شبکه می‌زند؛ اینجا لازم نیست.
        self._profit_patch = mock.patch.object(
            bot, "_record_akhavan_profit", lambda *a, **k: None
        )
        self._profit_patch.start()
        self.addCleanup(self._profit_patch.stop)

        # «اکنون» برای محاسبه‌ی تمدید دستی هم قفل می‌شود.
        self._eylan_now_patch = mock.patch.object(bot, "_eylan_now", lambda: NOW)
        self._eylan_now_patch.start()
        self.addCleanup(self._eylan_now_patch.stop)

        self.panel = FakeEylanClient()

        self._client_patch = mock.patch.object(
            bot, "get_eylan_client", lambda: self.panel
        )
        self._client_patch.start()
        self.addCleanup(self._client_patch.stop)

    # ── ابزارها ────────────────────────────────────────────────────────────
    def set_order_api(self, order_id, **values):
        with bot.db() as conn:
            for column, value in values.items():
                conn.execute(
                    f"UPDATE orders SET {column}=? WHERE id=?",
                    (value, order_id),
                )

    def get_order(self, order_id):
        with bot.db() as conn:
            return dict(conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone())

    def get_service(self, service_id):
        with bot.db() as conn:
            return dict(conn.execute(
                "SELECT * FROM active_services WHERE id=?", (service_id,)
            ).fetchone())

    def wallet_balance(self, user_id=USER_ID):
        with bot.db() as conn:
            return int(conn.execute(
                "SELECT balance FROM users WHERE user_id=?", (user_id,)
            ).fetchone()[0])

    def texts(self):
        return [t for _c, t, _k in self.sent if isinstance(t, str)]

    def all_text(self):
        return "\n".join(self.texts())

    def edited_text(self):
        """متنی که با edit_message_text ویرایش شده (یا None)."""

        call_args = bot.bot.edit_message_text.call_args
        if not call_args:
            return None

        args, kwargs = call_args
        return args[0] if args else kwargs.get("text")

    def edited_markup(self):
        call_args = bot.bot.edit_message_text.call_args
        if not call_args:
            return None

        return call_args[1].get("reply_markup")

    def seen_text(self):
        """همه‌ی متن‌هایی که ادمین دیده؛ چه ارسال‌شده چه ویرایش‌شده."""

        parts = self.texts()
        edited = self.edited_text()
        if edited:
            parts.append(edited)
        return "\n".join(parts)

    def seen_datas(self):
        """همه‌ی callback_dataهایی که ادمین دیده (ارسالی + ویرایشی)."""

        datas = [
            d
            for _c, _t, kwargs in self.sent
            for d in callback_datas(kwargs.get("reply_markup"))
        ]
        datas += callback_datas(self.edited_markup())
        return datas


# ══════════════════════════════════════════════════════════════════════════════
# 🚨 صف تحویل‌های ناموفق
# ══════════════════════════════════════════════════════════════════════════════

class FailedDeliveryQueueTests(AdminExtraBase):
    def test_failed_orders_are_listed(self):
        failed = self.add_order(status="fulfilling")
        self.set_order_api(failed, api_status="failed_verification",
                           api_error="تمدید اعمال نشد")

        verification = self.add_order(status="fulfilling")
        self.set_order_api(verification, api_status="failed")

        items = bot.failed_delivery_orders(now_dt=NOW)

        ids = {item["id"] for item in items}
        self.assertEqual(ids, {failed, verification})

        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id[failed]["reason"], "failed_verification")
        self.assertEqual(by_id[failed]["error"], "تمدید اعمال نشد")
        self.assertEqual(by_id[verification]["reason"], "failed")

    def test_closed_orders_are_not_in_the_queue(self):
        for status in bot.CLOSED_ORDER_STATUSES:
            order_id = self.add_order(status=status)
            self.set_order_api(order_id, api_status="failed")

        self.assertEqual(bot.failed_delivery_orders(now_dt=NOW), [])
        self.assertEqual(bot.count_failed_deliveries(), 0)

    def test_error_without_status_also_counts(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_error="HTTP 403 quota")

        self.assertEqual(bot.count_failed_deliveries(), 1)

    def test_clean_orders_are_not_in_the_queue(self):
        delivered = self.add_order(status="completed")
        self.set_order_api(delivered, api_status="delivered")

        self.add_order(status="awaiting_payment")

        self.assertEqual(bot.failed_delivery_orders(now_dt=NOW), [])

    def test_report_text_has_codes_and_legend(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed_verification",
                           api_error="پنل تاریخ را عوض نکرد")

        text, ids = bot.admin_failed_deliveries_text(now_dt=NOW)

        self.assertEqual(ids, [order_id])
        self.assertIn("تحویل‌های ناموفق", text)
        self.assertIn(bot.order_code(order_id), text)
        self.assertIn("failed_verification", text)
        self.assertIn("پنل تاریخ را عوض نکرد", text)
        self.assertIn("راهنمای شناسه‌ها", text)

    def test_empty_queue_says_so(self):
        text, ids = bot.admin_failed_deliveries_text(now_dt=NOW)

        self.assertEqual(ids, [])
        self.assertIn("مورد ناموفقی وجود ندارد", text)

    def test_keyboard_offers_retry_and_dossier(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed")

        data = callback_datas(
            bot.admin_failed_deliveries_keyboard(
                bot.failed_delivery_orders(now_dt=NOW)
            )
        )

        self.assertIn(f"admin:oact:retry:{order_id}", data)
        self.assertIn(f"admin:dossier:o:{order_id}", data)
        self.assertIn("admin:eylan:diag", data)

    def test_callback_requires_admin(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed")

        bot.cb_admin_failed_deliveries(
            self.call("admin:failed_deliveries", user=USER_ID)
        )
        self.assertEqual(self.sent, [])

        bot.cb_admin_failed_deliveries(self.call("admin:failed_deliveries"))
        self.assertIn("تحویل‌های ناموفق", self.seen_text())

    def test_admin_panel_button_shows_the_count(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed")

        kb = bot.admin_panel_keyboard(ADMIN_ID, ADMIN_ID)

        button = next(
            b
            for row in kb.keyboard
            for b in row
            if b.callback_data == "admin:failed_deliveries"
        )

        self.assertIn("(1)", button.text)


# ══════════════════════════════════════════════════════════════════════════════
# 📝 یادداشت ادمین
# ══════════════════════════════════════════════════════════════════════════════

class AdminNoteTests(AdminExtraBase):
    def test_note_round_trip(self):
        order_id = self.add_order()

        self.assertTrue(bot.save_order_admin_note(order_id, "  زنگ بزن به مشتری  "))
        self.assertEqual(
            bot.order_admin_note(self.get_order(order_id)),
            "زنگ بزن به مشتری",
        )

    def test_missing_note_is_none(self):
        order_id = self.add_order()
        self.assertIsNone(bot.order_admin_note(self.get_order(order_id)))
        self.assertIsNone(bot.order_admin_note(None))
        self.assertIsNone(bot.order_admin_note({}))

    def test_empty_note_clears_it(self):
        order_id = self.add_order()
        bot.save_order_admin_note(order_id, "یادداشت اول")
        bot.save_order_admin_note(order_id, "")

        self.assertIsNone(bot.order_admin_note(self.get_order(order_id)))

    def test_note_is_truncated(self):
        order_id = self.add_order()

        bot.save_order_admin_note(order_id, "ا" * (bot.ADMIN_NOTE_MAX_CHARS + 500))

        note = bot.order_admin_note(self.get_order(order_id))
        self.assertEqual(len(note), bot.ADMIN_NOTE_MAX_CHARS)

    def test_note_shows_in_dossier(self):
        order_id = self.add_order()
        bot.save_order_admin_note(order_id, "مشتری گفت تا شنبه صبر کنم")

        report = "\n".join(bot.admin_order_dossier_sections(order_id, now_dt=NOW))

        self.assertIn("یادداشت ادمین", report)
        self.assertIn("مشتری گفت تا شنبه صبر کنم", report)

    def test_note_flow_from_callback_to_message(self):
        order_id = self.add_order()

        bot.cb_admin_order_note(self.call(f"admin:order_note:{order_id}"))

        state = bot.get_admin_state(ADMIN_ID)
        self.assertEqual(state["mode"], "order_note_edit")
        self.assertEqual(int(state["data"]["order_id"]), order_id)
        self.assertIn("یادداشت ادمین", self.all_text())

        bot.handle_order_note_message(self.admin_message("دنبال‌کردن: فردا"))

        self.assertIsNone(bot.get_admin_state(ADMIN_ID))
        self.assertEqual(
            bot.order_admin_note(self.get_order(order_id)), "دنبال‌کردن: فردا"
        )
        self.assertIn("ذخیره شد", self.sent[-1][1])

        data = callback_datas(self.sent[-1][2].get("reply_markup"))
        self.assertIn(f"admin:dossier:o:{order_id}", data)

    def test_clear_keyword_removes_note(self):
        order_id = self.add_order()
        bot.save_order_admin_note(order_id, "چیزی")

        bot.set_admin_state(ADMIN_ID, "order_note_edit", {"order_id": order_id})
        bot.handle_order_note_message(self.admin_message("حذف"))

        self.assertIsNone(bot.order_admin_note(self.get_order(order_id)))
        self.assertIn("پاک شد", self.sent[-1][1])

    def test_non_text_is_rejected(self):
        order_id = self.add_order()
        bot.set_admin_state(ADMIN_ID, "order_note_edit", {"order_id": order_id})

        bot.handle_order_note_message(
            self.admin_message(None, content_type="photo")
        )

        self.assertIn("متن بفرستید", self.sent[-1][1])
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

    def test_unknown_order_in_callback(self):
        bot.cb_admin_order_note(self.call("admin:order_note:4242"))

        bot.bot.answer_callback_query.assert_called()
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

    def test_note_requires_admin(self):
        order_id = self.add_order()

        bot.cb_admin_order_note(
            self.call(f"admin:order_note:{order_id}", user=USER_ID)
        )

        self.assertIsNone(bot.get_admin_state(ADMIN_ID))
        self.assertEqual(self.sent, [])


# ══════════════════════════════════════════════════════════════════════════════
# ⚡ اقدامات سریع سفارش
# ══════════════════════════════════════════════════════════════════════════════

class OrderActionTests(AdminExtraBase):
    def test_actions_menu_lists_buttons(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed", api_error="quota")

        bot.cb_admin_order_actions(self.call(f"admin:order_actions:{order_id}"))

        data = self.seen_datas()

        self.assertIn(f"admin:order_note:{order_id}", data)
        self.assertIn(f"admin:oact:resend:{order_id}", data)
        self.assertIn(f"admin:oact:retry:{order_id}", data)
        self.assertIn(f"admin:oact:extend:{order_id}", data)
        self.assertIn(f"admin:oact:done:{order_id}", data)
        self.assertIn(f"admin:oact:refund:{order_id}", data)

    def test_actions_menu_unknown_order(self):
        bot.cb_admin_order_actions(self.call("admin:order_actions:4242"))
        bot.bot.answer_callback_query.assert_called()

    # ── ارسال دوباره‌ی پیام تحویل ────────────────────────────────────────────
    def test_resend_delivery_sends_link_to_customer(self):
        order_id = self.add_order(status="completed")
        self.set_order_api(
            order_id,
            api_link="https://panel.test/sub/HASH/ali_vpn",
            api_status="delivered",
        )

        ok, message = bot.resend_order_delivery(ADMIN_ID, order_id)

        self.assertTrue(ok)
        self.assertEqual(self.sent[-1][0], USER_ID)
        self.assertIn("panel.test/sub/HASH/ali_vpn", self.sent[-1][1])
        self.assertEqual(
            self.get_order(order_id)["delivery_message_id"], len(self.sent)
        )

    def test_resend_gmail_order_without_link(self):
        order_id = self.add_order(
            status="completed",
            product_id="chatgpt-shared-10",
            title="ChatGPT Plus — اکانت نیمه‌خلوت ۱۰ نفره",
            gmail_address="customer@gmail.com",
        )

        ok, message = bot.resend_order_delivery(ADMIN_ID, order_id)

        self.assertTrue(ok)
        self.assertIn("customer@gmail.com", self.sent[-1][1])

    def test_resend_without_anything_to_send_is_honest(self):
        order_id = self.add_order(status="awaiting_receipt")

        ok, message = bot.resend_order_delivery(ADMIN_ID, order_id)

        self.assertFalse(ok)
        self.assertIn("چیزی برای ارسال مجدد وجود ندارد", message)
        self.assertEqual(self.sent, [])

    # ── ثبت تحویل دستی ──────────────────────────────────────────────────────
    def test_mark_delivered_completes_order_and_notifies(self):
        order_id = self.add_order(status="fulfilling")

        ok, message = bot.mark_order_delivered(ADMIN_ID, order_id)

        self.assertTrue(ok)
        self.assertEqual(self.get_order(order_id)["status"], "completed")
        self.assertEqual(self.get_order(order_id)["delivery_mode"], "manual_admin")
        self.assertIn("تحویل شد", self.all_text())

    def test_mark_delivered_refuses_completed_order(self):
        order_id = self.add_order(status="completed")

        ok, message = bot.mark_order_delivered(ADMIN_ID, order_id)

        self.assertFalse(ok)
        self.assertIn("قبلاً تکمیل شده", message)

    def test_mark_delivered_refuses_closed_order(self):
        order_id = self.add_order(status="expired_unpaid")

        ok, message = bot.mark_order_delivered(ADMIN_ID, order_id)

        self.assertFalse(ok)
        self.assertIn("منطقی نیست", message)
        self.assertEqual(self.get_order(order_id)["status"], "expired_unpaid")

    def test_done_action_needs_confirmation(self):
        order_id = self.add_order(status="fulfilling")

        bot.cb_admin_order_action(self.call(f"admin:oact:done:{order_id}"))

        # هنوز تکمیل نشده؛ اول تأیید می‌خواهد
        self.assertEqual(self.get_order(order_id)["status"], "fulfilling")
        self.assertIn("ثبت تحویل دستی", self.all_text())
        self.assertIn(
            f"admin:oact:done:yes:{order_id}", self.seen_datas()
        )

        bot.cb_admin_order_action(
            self.call(f"admin:oact:done:yes:{order_id}")
        )

        self.assertEqual(self.get_order(order_id)["status"], "completed")

    # ── مرجوعی به کیف پول ───────────────────────────────────────────────────
    def test_refund_returns_reserved_plus_paid_once(self):
        order_id = self.add_order(
            status="completed", price=600000, wallet_reserved=100000
        )
        self.set_order_api(order_id, payment_amount=500000)

        amount, message = bot.refund_order_to_wallet(ADMIN_ID, order_id, "خرابی پنل")

        self.assertEqual(amount, 600000)
        self.assertEqual(self.wallet_balance(), 600000)
        self.assertEqual(self.get_order(order_id)["status"], "refunded")

        with bot.db() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE order_id=?", (order_id,)
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "refund")
        self.assertEqual(int(rows[0]["amount"]), 600000)

        self.assertIn("خرابی پنل", self.all_text())

    def test_refund_does_not_double_credit(self):
        order_id = self.add_order(status="completed", wallet_reserved=100000)
        self.set_order_api(order_id, payment_amount=200000)

        bot.refund_order_to_wallet(ADMIN_ID, order_id)
        amount, message = bot.refund_order_to_wallet(ADMIN_ID, order_id)

        self.assertEqual(amount, 0)
        self.assertIn("قبلاً مرجوع شده", message)
        self.assertEqual(self.wallet_balance(), 300000)

    def test_refund_without_payment_is_honest(self):
        order_id = self.add_order(status="awaiting_receipt", price=100000)

        amount, message = bot.refund_order_to_wallet(ADMIN_ID, order_id)

        self.assertEqual(amount, 0)
        self.assertIn("چیزی برای مرجوع کردن نیست", message)
        self.assertEqual(self.wallet_balance(), 0)
        self.assertEqual(self.get_order(order_id)["status"], "awaiting_receipt")

    def test_refund_action_needs_confirmation(self):
        order_id = self.add_order(status="completed", wallet_reserved=50000)

        bot.cb_admin_order_action(self.call(f"admin:oact:refund:{order_id}"))

        self.assertEqual(self.wallet_balance(), 0)
        self.assertIn("مبلغ قابل برگشت", self.all_text())
        self.assertIn(f"admin:oact:refund:yes:{order_id}", self.seen_datas())

        bot.cb_admin_order_action(self.call(f"admin:oact:refund:yes:{order_id}"))

        self.assertEqual(self.wallet_balance(), 50000)

    # ── تلاش مجدد تحویل خودکار ──────────────────────────────────────────────
    def test_retry_claims_order_and_forces_delivery(self):
        order_id = self.add_order(
            status="fulfilling", assigned_admin_id=ADMIN_ID + 999
        )
        self.set_order_api(order_id, api_status="failed_verification")

        with mock.patch.object(
            bot, "deliver_eylan_order_via_api", return_value=True
        ) as deliver:
            ok, message = bot.retry_failed_delivery(ADMIN_ID, order_id)

        self.assertTrue(ok)
        deliver.assert_called_once_with(ADMIN_ID, order_id, force=True)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], "fulfilling")
        self.assertEqual(order["assigned_admin_id"], ADMIN_ID)
        self.assertEqual(order["delivery_mode"], "auto")

    def test_retry_refuses_non_wireguard_product(self):
        order_id = self.add_order(
            status="fulfilling", product_id="chatgpt-shared-10",
            title="ChatGPT Plus"
        )

        with mock.patch.object(bot, "deliver_eylan_order_via_api") as deliver:
            ok, message = bot.retry_failed_delivery(ADMIN_ID, order_id)

        self.assertFalse(ok)
        self.assertIn("تحویل خودکار EylanPanel ندارد", message)
        deliver.assert_not_called()

    def test_retry_refuses_completed_order(self):
        order_id = self.add_order(status="completed")

        with mock.patch.object(bot, "deliver_eylan_order_via_api") as deliver:
            ok, message = bot.retry_failed_delivery(ADMIN_ID, order_id)

        self.assertFalse(ok)
        self.assertIn("قبلاً تکمیل شده", message)
        deliver.assert_not_called()

    # ── تمدید دستی سرویس ────────────────────────────────────────────────────
    def test_extend_local_only_when_no_panel_username(self):
        order_id = self.add_order(status="completed")
        service_id = self.make_service(
            order_id, NOW - timedelta(days=20), NOW + timedelta(days=10)
        )

        result = bot.extend_order_service(ADMIN_ID, order_id, 5)

        self.assertTrue(result["ok"])
        self.assertFalse(result["panel"])

        new_expiry = datetime.fromisoformat(self.get_service(service_id)["expires_at"])
        self.assertEqual(
            new_expiry,
            (NOW + timedelta(days=10)) + timedelta(days=5),
        )
        self.assertEqual(self.get_service(service_id)["is_expired"], 0)

    def test_extend_applies_panel_first_and_verifies(self):
        order_id = self.add_order(status="completed", service_username="ali_vpn")
        service_id = self.make_service(
            order_id, NOW - timedelta(days=20), NOW + timedelta(days=10),
            service_username="ali_vpn",
        )
        self.panel.users["ali_vpn"] = {}

        result = bot.extend_order_service(ADMIN_ID, order_id, 30)

        self.assertTrue(result["ok"])
        self.assertTrue(result["panel"])

        kinds = [c[0] for c in self.panel.calls]
        self.assertEqual(kinds, ["update", "verify"])

        payload = self.panel.calls[0][2]
        # انقضای فعلی ۲۰۲۶-۱۰-۲۹ است؛ +۳۰ روز ⇒ ۲۰۲۶-۱۱-۲۸
        self.assertEqual(payload, {"expiry_date_str": "2026-11-28"})
        self.assertNotIn("expiry_days", payload)

    def test_extend_does_not_touch_db_when_panel_fails(self):
        order_id = self.add_order(status="completed", service_username="ali_vpn")
        service_id = self.make_service(
            order_id, NOW - timedelta(days=20), NOW + timedelta(days=10),
            service_username="ali_vpn",
        )
        before = self.get_service(service_id)["expires_at"]
        self.panel.update_fails = True

        result = bot.extend_order_service(ADMIN_ID, order_id, 30)

        self.assertFalse(result["ok"])
        self.assertIn("ناموفق", result["message"])
        self.assertEqual(self.get_service(service_id)["expires_at"], before)

    def test_extend_does_not_touch_db_when_verification_fails(self):
        order_id = self.add_order(status="completed", service_username="ali_vpn")
        service_id = self.make_service(
            order_id, NOW - timedelta(days=20), NOW + timedelta(days=10),
            service_username="ali_vpn",
        )
        before = self.get_service(service_id)["expires_at"]
        self.panel.verify_fails = True

        result = bot.extend_order_service(ADMIN_ID, order_id, 30)

        self.assertFalse(result["ok"])
        self.assertEqual(self.get_service(service_id)["expires_at"], before)

    def test_extend_without_api_key_is_refused(self):
        order_id = self.add_order(status="completed", service_username="ali_vpn")
        self.make_service(
            order_id, NOW - timedelta(days=20), NOW + timedelta(days=10),
            service_username="ali_vpn",
        )

        with mock.patch.object(bot, "EYLAN_API_KEY", ""):
            result = bot.extend_order_service(ADMIN_ID, order_id, 30)

        self.assertFalse(result["ok"])
        self.assertIn("EYLAN_API_KEY", result["message"])

    def test_extend_rejects_bad_day_count(self):
        order_id = self.add_order(status="completed")
        self.make_service(order_id, NOW, NOW + timedelta(days=10))

        for days in (0, -5, "abc", bot.ADMIN_EXTEND_MAX_DAYS + 1):
            with self.subTest(days=days):
                result = bot.extend_order_service(ADMIN_ID, order_id, days)
                self.assertFalse(result["ok"])

    def test_extend_without_service_is_honest(self):
        order_id = self.add_order(status="awaiting_receipt")

        result = bot.extend_order_service(ADMIN_ID, order_id, 30)

        self.assertFalse(result["ok"])
        self.assertIn("سرویس زمان‌داری ثبت نشده", result["message"])

    def test_extend_message_flow(self):
        order_id = self.add_order(status="completed")
        self.make_service(order_id, NOW - timedelta(days=1), NOW + timedelta(days=9))

        bot.cb_admin_order_action(self.call(f"admin:oact:extend:{order_id}"))

        state = bot.get_admin_state(ADMIN_ID)
        self.assertEqual(state["mode"], "order_extend_days")

        bot.handle_order_extend_message(self.admin_message("۱۰"))

        self.assertIsNone(bot.get_admin_state(ADMIN_ID))
        self.assertIn("تمدید شد", self.sent[-1][1])

        new_expiry = datetime.fromisoformat(
            self.get_service(1)["expires_at"]
        )
        self.assertEqual(new_expiry, NOW + timedelta(days=19))

    def test_extend_message_rejects_non_number(self):
        order_id = self.add_order(status="completed")
        bot.set_admin_state(ADMIN_ID, "order_extend_days", {"order_id": order_id})

        bot.handle_order_extend_message(self.admin_message("ده روز"))

        self.assertIn("عدد معتبر نیست", self.sent[-1][1])
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))


# ══════════════════════════════════════════════════════════════════════════════
# 🛠 عملیات پنل روی سرویس
# ══════════════════════════════════════════════════════════════════════════════

class ServicePanelOpsTests(AdminExtraBase):
    def _service(self, username="ali_vpn", service_type="wireguard"):
        order_id = self.add_order(status="completed", service_username=username)
        return self.make_service(
            order_id, NOW - timedelta(days=5), NOW + timedelta(days=25),
            service_type=service_type, service_username=username,
        )

    def test_username_comes_from_service(self):
        service_id = self._service("ali_vpn")

        svc, username = bot._service_panel_target(service_id)

        self.assertEqual(username, "ali_vpn")
        self.assertEqual(int(svc["id"]), service_id)

    def test_trial_service_never_borrows_an_orders_username(self):
        """
        برای سرویس تست، order_id در واقع شناسه‌ی wireguard_trials است؛ اگر
        join به orders انجام شود، نام کاربری یک سفارش بی‌ربط برداشته می‌شود و
        ادمین ناخواسته اکانت شخص دیگری را دستکاری می‌کند.
        """

        other_order = self.add_order(status="completed")
        self.set_order_api(other_order, api_username="victim_user")

        with bot.db() as conn:
            conn.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name='active_services'",
                (0,),
            )

        trial_service = self.make_service(
            int(other_order),  # ← همان شماره‌ی سفارشِ قربانی
            NOW - timedelta(days=1), NOW + timedelta(days=1),
            service_type=bot.TRIAL_SERVICE_TYPE,
            product_id=bot.TRIAL_PRODUCT_ID,
            product_title=bot.TRIAL_PRODUCT_TITLE,
            service_username=None,
        )

        svc, username = bot._service_panel_target(trial_service)

        self.assertEqual(username, "")
        self.assertNotEqual(username, "victim_user")

    def test_panel_text_shows_service_details(self):
        service_id = self._service("ali_vpn")
        self.panel.users["ali_vpn"] = {"data_limit": 50}

        text = bot.admin_service_panel_text(service_id, now_dt=NOW)

        self.assertIn(bot.service_code(service_id), text)
        self.assertIn("ali_vpn", text)
        self.assertIn("باقی‌مانده", text)

    def test_live_status_reads_panel(self):
        service_id = self._service("ali_vpn")
        self.panel.users["ali_vpn"] = {"data_limit": 50, "is_active": True}

        text = bot.admin_service_panel_text(service_id, now_dt=NOW, live=True)

        self.assertIn("وضعیت زنده از پنل", text)
        self.assertEqual(self.panel.calls[0][0], "get_user")

    def test_live_status_reports_panel_failure(self):
        service_id = self._service("missing_user")

        text = bot.admin_service_panel_text(service_id, now_dt=NOW, live=True)

        self.assertIn("ناموفق بود", text)

    def test_no_username_disables_panel_ops(self):
        order_id = self.add_order(status="completed")
        service_id = self.make_service(
            order_id, NOW, NOW + timedelta(days=30), service_username=None
        )

        text = bot.admin_service_panel_text(service_id, now_dt=NOW)
        self.assertIn("عملیات پنل ممکن نیست", text)

        self.assertIn(
            "نام کاربری پنل",
            bot.service_panel_action(service_id, "reset", ADMIN_ID),
        )
        self.assertEqual(self.panel.calls, [])

    def test_no_api_key_is_honest(self):
        service_id = self._service("ali_vpn")

        with mock.patch.object(bot, "EYLAN_API_KEY", ""):
            message = bot.service_panel_action(service_id, "reset", ADMIN_ID)

        self.assertIn("EYLAN_API_KEY", message)
        self.assertEqual(self.panel.calls, [])

    def test_reset_traffic(self):
        service_id = self._service("ali_vpn")

        message = bot.service_panel_action(service_id, "reset", ADMIN_ID)

        self.assertIn("ریست شد", message)
        self.assertEqual(self.panel.calls, [("reset", "ali_vpn")])

    def test_toggle_reports_new_state(self):
        service_id = self._service("ali_vpn")
        self.panel.active = True

        first = bot.service_panel_action(service_id, "toggle", ADMIN_ID)
        self.assertIn("🟢 فعال است: <b>False</b>", first)

        second = bot.service_panel_action(service_id, "toggle", ADMIN_ID)
        self.assertIn("🟢 فعال است: <b>True</b>", second)

        self.assertEqual(
            self.panel.calls, [("toggle", "ali_vpn"), ("toggle", "ali_vpn")]
        )

    def test_revoke_sends_new_link_to_customer(self):
        service_id = self._service("ali_vpn")

        message = bot.service_panel_action(service_id, "revoke", ADMIN_ID)

        self.assertIn("NEW/ali_vpn", message)
        self.assertIn("برای مشتری هم ارسال شد", message)

        self.assertEqual(self.sent[-1][0], USER_ID)
        self.assertIn("NEW/ali_vpn", self.sent[-1][1])

    def test_unknown_action_is_rejected(self):
        service_id = self._service("ali_vpn")

        self.assertIn(
            "ناشناخته", bot.service_panel_action(service_id, "delete", ADMIN_ID)
        )
        self.assertEqual(self.panel.calls, [])

    def test_writes_need_confirmation(self):
        service_id = self._service("ali_vpn")

        bot.cb_admin_service_panel_action(
            self.call(f"admin:spanel:reset:{service_id}")
        )

        self.assertEqual(self.panel.calls, [])
        self.assertIn("ریست ترافیک", self.all_text())
        self.assertIn(
            f"admin:spanel:reset:yes:{service_id}", self.seen_datas()
        )

        bot.cb_admin_service_panel_action(
            self.call(f"admin:spanel:reset:yes:{service_id}")
        )

        self.assertEqual(self.panel.calls, [("reset", "ali_vpn")])

    def test_status_action_does_not_need_confirmation(self):
        service_id = self._service("ali_vpn")
        self.panel.users["ali_vpn"] = {}

        bot.cb_admin_service_panel_action(
            self.call(f"admin:spanel:status:{service_id}")
        )

        self.assertIn("وضعیت زنده", self.all_text())

    def test_panel_errors_are_reported_not_swallowed(self):
        service_id = self._service("ali_vpn")

        with mock.patch.object(
            self.panel, "reset_traffic",
            side_effect=EylanPanelError("HTTP 500 panel down"),
        ):
            bot.cb_admin_service_panel_action(
                self.call(f"admin:spanel:reset:yes:{service_id}")
            )

        self.assertIn("عملیات ناموفق بود", self.all_text())
        self.assertIn("HTTP 500", self.all_text())

    def test_service_dossier_links_to_panel_ops(self):
        service_id = self._service("ali_vpn")

        data = callback_datas(bot.admin_dossier_keyboard("service", service_id))

        self.assertIn(f"admin:service_panel:{service_id}", data)

    def test_panel_ops_require_admin(self):
        service_id = self._service("ali_vpn")

        bot.cb_admin_service_panel(
            self.call(f"admin:service_panel:{service_id}", user=USER_ID)
        )

        self.assertEqual(self.sent, [])


# ══════════════════════════════════════════════════════════════════════════════
# 📤 خروجی CSV
# ══════════════════════════════════════════════════════════════════════════════

class CsvExportTests(AdminExtraBase):
    def test_orders_csv_has_note_and_panel_columns(self):
        order_id = self.add_order(
            status="completed", gmail_address="customer@gmail.com",
            service_username="ali_vpn",
        )
        self.set_order_api(
            order_id,
            api_username="ali_vpn",
            api_link="https://panel.test/sub/X/ali_vpn",
            api_status="delivered",
            api_verified_at=iso(NOW),
        )
        bot.save_order_admin_note(order_id, "پیگیری شد")

        with tempfile.TemporaryDirectory() as directory:
            path, count = bot.export_orders_csv(directory, now_dt=NOW)

            self.assertEqual(count, 1)
            self.assertTrue(path.endswith(".csv"))

            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))

        header, first = rows[0], rows[1]

        self.assertIn("یادداشت ادمین", header)
        self.assertIn("وضعیت API", header)
        self.assertIn("کد کامل", header)

        self.assertEqual(first[header.index("شماره سفارش")], str(order_id))
        self.assertEqual(first[header.index("کد کامل")], bot.order_code_long(order_id))
        self.assertEqual(first[header.index("یادداشت ادمین")], "پیگیری شد")
        self.assertEqual(first[header.index("جیمیل")], "customer@gmail.com")
        self.assertEqual(first[header.index("وضعیت")], "تکمیل شده")

    def test_services_csv_uses_origin_codes(self):
        order_id = self.add_order(status="completed")
        service_id = self.make_service(
            order_id, NOW - timedelta(days=1), NOW + timedelta(days=29),
            service_username="ali_vpn",
        )

        with tempfile.TemporaryDirectory() as directory:
            path, count = bot.export_services_csv(directory, now_dt=NOW)

            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))

        header, first = rows[0], rows[1]

        self.assertEqual(count, 1)
        self.assertEqual(first[header.index("شناسه سرویس")], str(service_id))
        self.assertEqual(first[header.index("کد منشأ")], bot.order_code(order_id))
        self.assertEqual(first[header.index("منشأ")], "سفارش")
        self.assertEqual(first[header.index("منقضی شده؟")], "خیر")

    def test_export_callback_sends_document(self):
        self.add_order(status="completed")

        bot.cb_admin_export(self.call("admin:export:orders"))

        caption = self.sent[-1][2].get("caption")
        self.assertIn("خروجی سفارش‌ها", caption)

    def test_export_menu_lists_both_kinds(self):
        bot.cb_admin_export_menu(self.call("admin:export"))

        data = self.seen_datas()

        self.assertIn("admin:export:orders", data)
        self.assertIn("admin:export:services", data)

    def test_export_rejects_unknown_kind(self):
        bot.cb_admin_export(self.call("admin:export:users"))

        bot.bot.answer_callback_query.assert_called()
        self.assertEqual(self.sent, [])

    def test_export_requires_admin(self):
        bot.cb_admin_export(self.call("admin:export:orders", user=USER_ID))
        self.assertEqual(self.sent, [])


# ══════════════════════════════════════════════════════════════════════════════
# 🔔 خلاصه‌ی روزانه‌ی ادمین
# ══════════════════════════════════════════════════════════════════════════════

class DailyDigestTests(AdminExtraBase):
    def _yesterday_data(self):
        start = bot.iran_day_start_utc(NOW, 1)

        sold = self.add_order(
            status="completed", price=300000,
            created=start + timedelta(hours=2),
            completed=start + timedelta(hours=3),
        )
        self.add_order(
            status="awaiting_receipt", price=100000,
            created=start + timedelta(hours=5),
        )
        failed = self.add_order(status="fulfilling")
        self.set_order_api(failed, api_status="failed_verification")
        self.make_service(sold, NOW, NOW + timedelta(days=2))

        return sold, failed

    def test_digest_text_covers_the_day(self):
        self._yesterday_data()

        text = bot.admin_daily_digest_text(now_dt=NOW)

        self.assertIn("خلاصه‌ی روزانه", text)
        self.assertIn("300,000", text)          # فروش دیروز
        self.assertIn("نیاز به اقدام", text)
        self.assertIn("تحویل‌های ناموفق: <b>1</b>", text)
        self.assertIn("رو به انقضا", text)

    def test_digest_is_enabled_by_default(self):
        self.assertTrue(bot.daily_digest_enabled())
        self.assertEqual(bot.daily_digest_hour(), bot.DAILY_DIGEST_DEFAULT_HOUR)

    def test_toggle_and_hour(self):
        bot.set_daily_digest_enabled(False)
        self.assertFalse(bot.daily_digest_enabled())

        bot.set_daily_digest_enabled(True)
        self.assertTrue(bot.daily_digest_enabled())

        self.assertEqual(bot.set_daily_digest_hour(21), 21)
        self.assertEqual(bot.daily_digest_hour(), 21)

        # مقادیر نامعتبر محدود می‌شوند
        self.assertEqual(bot.set_daily_digest_hour(99), 23)
        self.assertEqual(bot.set_daily_digest_hour(-4), 0)

    def test_sends_once_at_the_configured_iran_hour(self):
        self._yesterday_data()
        bot.set_daily_digest_hour(IRAN_HOUR_AT_NOW)

        self.assertTrue(bot.maybe_send_admin_daily_digest(now_dt=NOW))
        self.assertIn("خلاصه‌ی روزانه", self.all_text())

        sent_before = len(self.sent)

        # همان روز دوباره ارسال نمی‌شود
        self.assertFalse(
            bot.maybe_send_admin_daily_digest(now_dt=NOW + timedelta(minutes=30))
        )
        self.assertEqual(len(self.sent), sent_before)

        # فردا دوباره ارسال می‌شود
        self.assertTrue(
            bot.maybe_send_admin_daily_digest(now_dt=NOW + timedelta(days=1))
        )

    def test_does_not_send_at_other_hours(self):
        bot.set_daily_digest_hour(3)

        self.assertFalse(bot.maybe_send_admin_daily_digest(now_dt=NOW))
        self.assertEqual(self.sent, [])

    def test_disabled_digest_is_not_sent(self):
        bot.set_daily_digest_enabled(False)
        bot.set_daily_digest_hour(IRAN_HOUR_AT_NOW)

        self.assertFalse(bot.maybe_send_admin_daily_digest(now_dt=NOW))
        self.assertEqual(self.sent, [])

    def test_menu_and_callbacks(self):
        bot.cb_admin_digest(self.call("admin:digest"))
        self.assertIn("خلاصه‌ی روزانه", self.seen_text())

        data = self.seen_datas()
        self.assertIn("admin:digest:toggle", data)
        self.assertIn("admin:digest:hour", data)
        self.assertIn("admin:digest:now", data)

        bot.cb_admin_digest(self.call("admin:digest:toggle"))
        self.assertFalse(bot.daily_digest_enabled())

        bot.cb_admin_digest(self.call("admin:digest:toggle"))
        self.assertTrue(bot.daily_digest_enabled())

    def test_send_now_button(self):
        self._yesterday_data()

        bot.cb_admin_digest(self.call("admin:digest:now"))

        self.assertIn("خلاصه‌ی روزانه", self.all_text())
        self.assertIn("ادمین ارسال شد", self.all_text())

    def test_hour_setting_flow(self):
        bot.cb_admin_digest(self.call("admin:digest:hour"))

        self.assertEqual(bot.get_admin_state(ADMIN_ID)["mode"], "digest_hour_set")

        bot.handle_digest_hour_message(self.admin_message("۲۲"))

        self.assertEqual(bot.daily_digest_hour(), 22)
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

    def test_hour_setting_rejects_garbage(self):
        bot.set_admin_state(ADMIN_ID, "digest_hour_set", {})

        bot.handle_digest_hour_message(self.admin_message("25"))
        self.assertIn("۰ تا ۲۳", self.sent[-1][1])
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

        bot.handle_digest_hour_message(self.admin_message("صبح"))
        self.assertIn("۰ تا ۲۳", self.sent[-1][1])


# ══════════════════════════════════════════════════════════════════════════════
# سیم‌کشی‌ها
# ══════════════════════════════════════════════════════════════════════════════

class WiringTests(AdminExtraBase):
    def test_admin_panel_has_the_new_tools(self):
        data = callback_datas(bot.admin_panel_keyboard(ADMIN_ID, ADMIN_ID))

        for expected in (
            "admin:failed_deliveries",
            "admin:export",
            "admin:digest",
            "admin:order_search",
            "admin:stale_orders",
        ):
            self.assertIn(expected, data)

    def test_order_dossier_links_to_actions(self):
        order_id = self.add_order()

        data = callback_datas(bot.admin_dossier_keyboard("order", order_id))

        self.assertIn(f"admin:order_actions:{order_id}", data)
        self.assertIn(f"admin:order_note:{order_id}", data)

    def test_refunded_status_has_a_label(self):
        self.assertIn("refunded", bot.ORDER_DATES_STATUS_LABELS)
        self.assertIn("مرجوع", bot.ORDER_DATES_STATUS_LABELS["refunded"])

    def test_refunded_orders_leave_the_failed_queue(self):
        order_id = self.add_order(status="fulfilling", wallet_reserved=10000)
        self.set_order_api(order_id, api_status="failed")

        self.assertEqual(bot.count_failed_deliveries(), 1)

        bot.refund_order_to_wallet(ADMIN_ID, order_id)

        self.assertEqual(bot.count_failed_deliveries(), 0)

    def test_router_handles_the_new_modes(self):
        order_id = self.add_order(status="completed")

        bot.set_admin_state(ADMIN_ID, "order_note_edit", {"order_id": order_id})
        bot.admin_state_message(self.admin_message("یادداشت از طریق روتر"))
        self.assertEqual(
            bot.order_admin_note(self.get_order(order_id)), "یادداشت از طریق روتر"
        )

        bot.set_admin_state(ADMIN_ID, "digest_hour_set", {})
        bot.admin_state_message(self.admin_message("8"))
        self.assertEqual(bot.daily_digest_hour(), 8)

    def test_dashboard_shows_failed_deliveries(self):
        order_id = self.add_order(status="fulfilling")
        self.set_order_api(order_id, api_status="failed")

        self.assertIn("تحویل‌های ناموفق", bot.admin_stats_text())


if __name__ == "__main__":
    unittest.main()
