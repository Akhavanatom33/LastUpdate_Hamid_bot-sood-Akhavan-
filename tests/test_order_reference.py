"""
تست‌های «شماره سفارش قاطی نمی‌شود» + «جستجوی جهانی» + «گزارش کامل سفارش».

سناریوی واقعی که این تست‌ها پوشش می‌دهند:
مشتری در «سرویس‌های من» شناسه‌ی سرویس را می‌دید و به ادمین می‌گفت
«سفارش من #48 است»؛ ادمین ۴۸ را جستجو می‌کرد و یک سفارش کاملاً متفاوت
(محصول/مشتری دیگر) برمی‌گشت. حالا:

  ۱) شناسه‌ها قالب متمایز دارند:  #48/ORD-48 سفارش، S-46 سرویس، T-12 تست.
  ۲) یک عدد مبهم به همه‌ی جدول‌ها زده می‌شود و اگر چند نتیجه داشت، فهرست
     ابهام‌زدایی با دکمه نشان داده می‌شود — هرگز حدس تکی نمی‌زند.
  ۳) گزارش کامل، همه‌ی اطلاعات موجود سفارش را یک‌جا می‌دهد.
"""

import os
import tempfile
import unittest
from datetime import timedelta
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


class CollidingIdMixin:
    """
    برای بازتولید باگ واقعی، شناسه‌ی سرویس را عمداً روی همان شماره‌ی سفارش
    می‌گذاریم (در دیتابیس واقعی هم orders.id و هم active_services.id از ۱
    شروع می‌شوند و به هم می‌رسند).
    """

    def set_service_id(self, service_id, new_id):
        with bot.db() as conn:
            conn.execute(
                "UPDATE active_services SET id=? WHERE id=?",
                (new_id, service_id),
            )
        return new_id


class CodeFormatTests(OrderDatesBase):
    def test_codes_are_visually_distinct(self):
        self.assertEqual(bot.order_code(48), "#48")
        self.assertEqual(bot.order_code_long(48), "ORD-48")
        self.assertEqual(bot.service_code(46), "S-46")
        self.assertEqual(bot.trial_code(12), "T-12")

    def test_legend_explains_every_prefix(self):
        self.assertIn("#48", bot.ID_LEGEND)
        self.assertIn("S-46", bot.ID_LEGEND)
        self.assertIn("T-12", bot.ID_LEGEND)

    def test_trial_service_origin_is_trial_not_order(self):
        """
        در سرویس‌های تست، ستون order_id در واقع شناسه‌ی wireguard_trials است.
        نوشتن «سفارش #N» برای آن‌ها غلط بود.
        """

        svc = {"service_type": bot.TRIAL_SERVICE_TYPE, "order_id": 12}

        self.assertEqual(bot.service_origin_label(svc), "تست")
        self.assertEqual(bot.service_origin_code(svc), "T-12")
        self.assertEqual(bot.service_ref_text(svc), "(تست T-12)")

    def test_normal_service_origin_is_order(self):
        svc = {"service_type": "wireguard", "order_id": 30}

        self.assertEqual(bot.service_origin_label(svc), "سفارش")
        self.assertEqual(bot.service_origin_code(svc), "#30")
        self.assertEqual(bot.service_ref_text(svc), "(سفارش #30)")


class ServiceCardTests(OrderDatesBase):
    def test_user_service_card_never_shows_bare_hash_for_service(self):
        order_id = self.add_order(service_username="ali_vpn")
        service_id = self.make_service(
            order_id,
            NOW - timedelta(days=3),
            NOW + timedelta(days=27),
            service_username="ali_vpn",
        )

        svc = bot.get_active_service_by_id(service_id)
        card = bot.format_service_card(svc)

        self.assertIn(bot.service_code(service_id), card)   # S-<id>
        self.assertIn(f"سفارش: <b>#{order_id}</b>", card)   # شماره سفارش واقعی
        self.assertNotIn(f"شناسه سرویس: <b>#{service_id}</b>", card)

    def test_trial_card_shows_trial_code(self):
        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO wireguard_trials(
                    user_id, username, created_at, status
                )
                VALUES (?, 'trial_user', ?, 'delivered')
                """,
                (USER_ID, iso(NOW)),
            )
            trial_id = int(cur.lastrowid)

        service_id = self.make_service(
            trial_id,
            NOW - timedelta(days=1),
            NOW + timedelta(days=1),
            service_type=bot.TRIAL_SERVICE_TYPE,
            product_id=bot.TRIAL_PRODUCT_ID,
            product_title=bot.TRIAL_PRODUCT_TITLE,
        )

        card = bot.format_service_card(bot.get_active_service_by_id(service_id))

        self.assertIn(bot.service_code(service_id), card)
        self.assertIn(f"تست: <b>T-{trial_id}</b>", card)


class AdminListTests(OrderDatesBase):
    def test_expiring_list_uses_distinct_codes(self):
        order_id = self.add_order()
        service_id = self.make_service(
            order_id, NOW - timedelta(days=27), NOW + timedelta(days=3)
        )

        text = bot.admin_expiring_text(now_dt=NOW)

        self.assertIn(bot.service_code(service_id), text)
        self.assertIn(f"سفارش #{order_id}", text)
        self.assertNotIn(f"• #{service_id} (سفارش", text)

    def test_expiring_list_marks_trial_services(self):
        trial_id = 7
        service_id = self.make_service(
            trial_id,
            NOW - timedelta(days=1),
            NOW + timedelta(days=1),
            service_type=bot.TRIAL_SERVICE_TYPE,
            product_id=bot.TRIAL_PRODUCT_ID,
            product_title=bot.TRIAL_PRODUCT_TITLE,
        )

        text = bot.admin_expiring_text(now_dt=NOW)

        self.assertIn(bot.service_code(service_id), text)
        self.assertIn(f"تست T-{trial_id}", text)
        self.assertNotIn(f"سفارش #{trial_id}", text)

    def test_user_profile_uses_distinct_codes(self):
        order_id = self.add_order()
        service_id = self.make_service(
            order_id, NOW - timedelta(days=1), NOW + timedelta(days=29)
        )

        text, ids = bot.admin_user_profile(USER_ID, now_dt=NOW)

        self.assertEqual(ids, [order_id])
        self.assertIn(bot.service_code(service_id), text)
        self.assertIn(f"سفارش #{order_id}", text)

    def test_orders_list_has_legend(self):
        self.add_order()
        text = bot.orders_text(limit=5)
        self.assertIn("راهنمای شناسه‌ها", text)

    def test_eylan_local_services_show_origin(self):
        order_id = self.add_order()
        self.make_service(
            order_id, NOW - timedelta(days=1), NOW + timedelta(days=29),
            service_username="ali_vpn",
        )

        text = bot.eylan_local_services_text()

        self.assertIn("سفارش: <b>", text)
        self.assertIn(f"#{order_id}", text)


class ParseReferenceTests(OrderDatesBase):
    def test_plain_number_is_ambiguous_by_design(self):
        kind, value = bot.parse_admin_reference("48")
        self.assertIsNone(kind)
        self.assertEqual(value, 48)

    def test_persian_digits(self):
        self.assertEqual(bot.parse_admin_reference("۴۸"), (None, 48))

    def test_explicit_prefixes(self):
        self.assertEqual(bot.parse_admin_reference("#48"), ("order", 48))
        self.assertEqual(bot.parse_admin_reference("ORD-48"), ("order", 48))
        self.assertEqual(bot.parse_admin_reference("ord-48"), ("order", 48))
        self.assertEqual(bot.parse_admin_reference("سفارش ۴۸"), ("order", 48))
        self.assertEqual(bot.parse_admin_reference("S-46"), ("service", 46))
        self.assertEqual(bot.parse_admin_reference("s-46"), ("service", 46))
        self.assertEqual(bot.parse_admin_reference("سرویس ۴۶"), ("service", 46))
        self.assertEqual(bot.parse_admin_reference("T-12"), ("trial", 12))
        self.assertEqual(bot.parse_admin_reference("تست ۱۲"), ("trial", 12))

    def test_identity_inputs(self):
        self.assertEqual(bot.parse_admin_reference("@buyer"), ("user", "buyer"))
        self.assertEqual(bot.parse_admin_reference("ali_vpn"), (None, "ali_vpn"))
        self.assertEqual(
            bot.parse_admin_reference("shop@gmail.com"),
            ("gmail", "shop@gmail.com"),
        )

    def test_empty(self):
        self.assertEqual(bot.parse_admin_reference(""), (None, None))
        self.assertEqual(bot.parse_admin_reference(None), (None, None))


class ResolveReferenceTests(CollidingIdMixin, OrderDatesBase):
    """
    هسته‌ی باگ گزارش‌شده: عدد ۴۸ هم به سفارش می‌خورد هم به سرویس.
    """

    def setUp(self):
        super().setUp()

        # سفارش #48 یک محصول است، سرویس S-48 محصولِ مشتریِ دیگری
        for _ in range(47):
            self.add_order(status="cancelled")
        self.order_48 = self.add_order(
            status="completed",
            product_id="chatgpt-shared-10",
            title="ChatGPT Plus — اکانت نیمه‌خلوت ۱۰ نفره",
            gmail_address="customer@gmail.com",
            service_username="ai_user",
        )

        other_id = USER_ID + 100
        self.add_user(other_id, "otherguy", "OtherGuy")

        for _ in range(47):
            other_order = self.add_order(
                status="completed", user_id=other_id
            )
        self.service_48 = self.make_service(
            other_order,
            NOW - timedelta(days=10),
            NOW + timedelta(days=20),
            user_id=other_id,
            service_username="other_vpn",
        )
        # برخورد شماره‌ها: سرویس شماره‌ی ۴۸ مشتریِ دیگری است
        self.service_48 = self.set_service_id(self.service_48, 48)

    def test_bare_number_returns_both_candidates_never_guesses(self):
        candidates = bot.resolve_admin_reference("48")

        kinds = {c["kind"] for c in candidates}
        self.assertIn("order", kinds)
        self.assertIn("service", kinds)

        order_candidate = next(c for c in candidates if c["kind"] == "order")
        service_candidate = next(c for c in candidates if c["kind"] == "service")

        self.assertEqual(order_candidate["id"], self.order_48)
        self.assertEqual(service_candidate["id"], self.service_48)
        self.assertEqual(order_candidate["code"], "#48")
        self.assertEqual(service_candidate["code"], "S-48")

        # هر دو با مشتری درستشان
        self.assertEqual(order_candidate["user_id"], USER_ID)
        self.assertEqual(service_candidate["user_id"], USER_ID + 100)

    def test_explicit_prefix_narrows_to_one(self):
        only_order = bot.resolve_admin_reference("#48")
        self.assertEqual(
            [(c["kind"], c["id"]) for c in only_order],
            [("order", self.order_48)],
        )

        only_service = bot.resolve_admin_reference("S-48")
        self.assertEqual(
            [(c["kind"], c["id"]) for c in only_service],
            [("service", self.service_48)],
        )

    def test_panel_username_finds_the_right_order(self):
        candidates = bot.resolve_admin_reference("ai_user")

        self.assertTrue(candidates)
        self.assertTrue(
            all(c["kind"] == "order" for c in candidates),
            [c["kind"] for c in candidates],
        )
        self.assertEqual(candidates[0]["id"], self.order_48)

    def test_gmail_finds_the_order(self):
        candidates = bot.resolve_admin_reference("customer@gmail.com")

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["kind"], "order")
        self.assertEqual(candidates[0]["id"], self.order_48)

    def test_telegram_id_finds_recent_records(self):
        candidates = bot.resolve_admin_reference(str(USER_ID))

        self.assertTrue(candidates)
        self.assertTrue(
            any(c["id"] == self.order_48 for c in candidates),
            [c["id"] for c in candidates],
        )

    def test_at_username_finds_records(self):
        candidates = bot.resolve_admin_reference("@otherguy")

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["user_id"], USER_ID + 100)

    def test_candidates_are_deduped_and_ordered(self):
        candidates = bot.resolve_admin_reference("48")

        keys = [(c["kind"], c["id"]) for c in candidates]
        self.assertEqual(len(keys), len(set(keys)))

        rank = {"order": 0, "service": 1, "trial": 2}
        self.assertEqual(
            keys,
            sorted(keys, key=lambda k: (rank[k[0]], -k[1])),
        )

    def test_unknown_number_returns_empty(self):
        self.assertEqual(bot.resolve_admin_reference("999999"), [])


class OrderSearchFlowTests(CollidingIdMixin, OrderDatesBase):
    def _prepare(self):
        self.order_id = self.add_order(
            status="completed",
            product_id="chatgpt-shared-10",
            title="ChatGPT Plus — اکانت نیمه‌خلوت ۱۰ نفره",
            gmail_address="customer@gmail.com",
            service_username="ai_user",
            completed=NOW - timedelta(days=2),
        )

        other_id = USER_ID + 100
        self.add_user(other_id, "otherguy", "OtherGuy")
        self.service_id = self.make_service(
            self.order_id,
            NOW - timedelta(days=2),
            NOW + timedelta(days=28),
            service_username="ai_user",
            gmail_address="customer@gmail.com",
        )
        return other_id

    def test_single_match_sends_full_dossier(self):
        self._prepare()
        bot.set_admin_state(ADMIN_ID, "order_search_lookup", {})

        bot.handle_order_search_message(
            self.admin_message(f"ORD-{self.order_id}")
        )

        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

        all_text = "\n".join(t for _c, t, _k in self.sent)
        self.assertIn("گزارش کامل سفارش", all_text)
        self.assertIn("ChatGPT Plus", all_text)

    def test_ambiguous_number_shows_disambiguation_menu(self):
        self._prepare()

        # سرویس اول را جابه‌جا می‌کنیم تا شماره‌ی سفارش آزاد شود
        self.set_service_id(self.service_id, self.order_id + 500)

        # سرویس دوم را عمداً روی همان شماره‌ی سفارش می‌گذاریم
        conflicting = self.make_service(
            self.order_id,
            NOW - timedelta(days=1),
            NOW + timedelta(days=10),
            user_id=USER_ID + 100,
        )
        conflicting = self.set_service_id(conflicting, self.order_id)

        bot.set_admin_state(ADMIN_ID, "order_search_lookup", {})
        bot.handle_order_search_message(
            self.admin_message(str(self.order_id))
        )

        text = self.sent[-1][1]
        markup = self.sent[-1][2].get("reply_markup")
        data = callback_datas(markup)

        self.assertIn("مورد پیدا شد", text)
        self.assertIn(bot.order_code(self.order_id), text)
        self.assertIn(bot.service_code(conflicting), text)
        self.assertIn(f"admin:dossier:o:{self.order_id}", data)
        self.assertIn(f"admin:dossier:s:{conflicting}", data)

        # هنوز در حالت جستجو هستیم تا ادمین بتواند دوباره بفرستد
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

    def test_no_match_explains_the_difference(self):
        bot.set_admin_state(ADMIN_ID, "order_search_lookup", {})

        bot.handle_order_search_message(self.admin_message("48"))

        text = self.sent[-1][1]
        self.assertIn("پیدا نشد", text)
        self.assertIn("S-", text)
        self.assertIsNotNone(bot.get_admin_state(ADMIN_ID))

    def test_non_text_is_rejected(self):
        bot.set_admin_state(ADMIN_ID, "order_search_lookup", {})

        bot.handle_order_search_message(
            self.admin_message(None, content_type="photo")
        )

        self.assertIn("متن بفرستید", self.sent[-1][1])

    def test_dossier_callback_requires_admin(self):
        self._prepare()

        bot.cb_admin_dossier(self.call(f"admin:dossier:o:{self.order_id}", user=USER_ID))
        self.assertEqual(self.sent, [])

        bot.cb_admin_dossier(self.call(f"admin:dossier:o:{self.order_id}"))
        all_text = "\n".join(t for _c, t, _k in self.sent)
        self.assertIn("گزارش کامل سفارش", all_text)

    def test_search_button_is_on_the_admin_panel(self):
        data = callback_datas(bot.admin_panel_keyboard(ADMIN_ID, ADMIN_ID))

        self.assertIn("admin:order_search", data)
        self.assertIn("admin:stale_orders", data)

    def test_order_dates_tool_redirects_service_ids(self):
        """اگر ادمین در «تاریخ سفارشات» شناسه‌ی سرویس بزند، سرویس را می‌گیرد."""

        self._prepare()
        bot.set_admin_state(ADMIN_ID, "order_dates_lookup", {})

        bot.handle_order_dates_message(
            self.admin_message(f"S-{self.service_id}")
        )

        all_text = "\n".join(t for _c, t, _k in self.sent)
        self.assertIn("گزارش کامل سرویس", all_text)
        self.assertIsNone(bot.get_admin_state(ADMIN_ID))

    def test_order_dates_tool_warns_when_number_is_not_an_order(self):
        self._prepare()

        # شماره‌ای که فقط به سرویس می‌خورد
        bot.set_admin_state(ADMIN_ID, "order_dates_lookup", {})
        bot.handle_order_dates_message(
            self.admin_message(str(self.service_id + 5000))
        )
        self.assertIn("پیدا نشد", self.sent[-1][1])


class DossierContentTests(OrderDatesBase):
    """گزارش کامل باید «تمامیییی اطلاعات موجود» را بدهد."""

    def setUp(self):
        super().setUp()

        self.created = NOW - timedelta(days=10)
        self.completed = NOW - timedelta(days=9)

        self.order_id = self.add_order(
            status="completed",
            created=self.created,
            completed=self.completed,
            price=685000,
            product_id="chatgpt-shared-10",
            title="ChatGPT Plus — اکانت نیمه‌خلوت ۱۰ نفره",
            gmail_address="customer@gmail.com",
            service_username="panel_user",
            wallet_reserved=85000,
            assigned_admin_id=ADMIN_ID,
        )

        with bot.db() as conn:
            conn.execute(
                """
                UPDATE orders
                SET api_username='panel_user',
                    api_link='https://panel.test/sub/HASH/panel_user',
                    api_status='delivered',
                    api_verified_at=?,
                    api_panel_snapshot=?,
                    delivery_mode='auto',
                    receipt_file_id='FILE_ID',
                    receipt_type='photo',
                    payment_amount=600000,
                    delivery_message_id=4242
                WHERE id=?
                """,
                (
                    iso(self.completed),
                    '{"username": "panel_user", "data_limit": 50, '
                    '"expiry_date_actual_iso": "2026-11-18T23:59:59+00:00", '
                    '"is_active": true}',
                    self.order_id,
                ),
            )

        self.add_transaction(USER_ID, self.order_id, "wallet_reserve", -85000)
        self.add_transaction(USER_ID, self.order_id, "payment", 600000)

        self.service_id = self.make_service(
            self.order_id,
            self.completed,
            self.completed + timedelta(days=30),
            service_username="panel_user",
            gmail_address="customer@gmail.com",
        )

    def report(self):
        sections = bot.admin_order_dossier_sections(self.order_id, now_dt=NOW)
        self.assertTrue(sections)
        return "\n".join(sections)

    def test_unknown_order_returns_empty(self):
        self.assertEqual(bot.admin_order_dossier_sections(4242), [])

    def test_order_identity(self):
        text = self.report()

        self.assertIn(f"گزارش کامل سفارش #{self.order_id}", text)
        self.assertIn(f"ORD-{self.order_id}", text)

    def test_customer_details(self):
        text = self.report()

        self.assertIn(f"<code>{USER_ID}</code>", text)   # آیدی عددی
        self.assertIn("@buyer", text)                     # یوزرنیم تلگرام
        self.assertIn("Buyer", text)                      # نام
        self.assertIn("موجودی کیف پول", text)

    def test_product_details(self):
        text = self.report()

        self.assertIn("ChatGPT Plus — اکانت نیمه‌خلوت ۱۰ نفره", text)
        self.assertIn("chatgpt-shared-10", text)

    def test_money_and_transactions(self):
        text = self.report()

        self.assertIn("685,000", text)
        self.assertIn("85,000", text)     # رزرو کیف پول
        self.assertIn("600,000", text)    # پرداخت
        self.assertIn("wallet_reserve", text)

    def test_delivery_and_panel_details(self):
        text = self.report()

        self.assertIn("panel_user", text)
        self.assertIn("https://panel.test/sub/HASH/panel_user", text)
        self.assertIn("delivered", text)
        self.assertIn("راستی‌آزمایی پنل", text)
        self.assertIn("customer@gmail.com", text)   # جیمیل اکانت هوش مصنوعی
        self.assertIn("فیش واریزی: ثبت شده", text)
        self.assertIn(f"<code>{ADMIN_ID}</code>", text)
        self.assertIn("4242", text)

    def test_dates_and_remaining_time(self):
        text = self.report()

        self.assertIn(bot.jalali_datetime_str(iso(self.created)), text)
        self.assertIn(bot.jalali_datetime_str(iso(self.completed)), text)
        self.assertIn("از ثبت تا الآن", text)
        self.assertIn("روز مانده", text)

    def test_linked_service(self):
        text = self.report()

        self.assertIn(bot.service_code(self.service_id), text)
        self.assertIn("سرویس‌های متصل به این سفارش", text)

    def test_every_section_within_telegram_limit(self):
        for section in bot.admin_order_dossier_sections(self.order_id, NOW):
            self.assertLessEqual(len(section), bot.TELEGRAM_TEXT_LIMIT)

    def test_keyboard_has_useful_shortcuts(self):
        kb = bot.admin_dossier_keyboard("order", self.order_id)
        data = callback_datas(kb)

        self.assertIn(f"admin:dossier:o:{self.order_id}", data)
        self.assertIn(f"admin:od:{self.order_id}", data)
        self.assertIn("admin:order_search", data)
        self.assertIn("admin:home", data)

    def test_service_dossier(self):
        sections = bot.admin_service_dossier_sections(self.service_id, NOW)
        text = "\n".join(sections)

        self.assertIn(f"گزارش کامل سرویس {bot.service_code(self.service_id)}", text)
        self.assertIn(f"سفارش مبدأ: #{self.order_id}", text)
        self.assertIn("panel_user", text)
        self.assertIn("customer@gmail.com", text)
        self.assertIn("تاریخچه‌ی سرویس‌های این مشتری", text)

    def test_service_dossier_for_trial_shows_trial_code(self):
        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO wireguard_trials(
                    user_id, username, created_at, status, panel_link
                )
                VALUES (?, 'trial_user', ?, 'delivered', 'https://panel.test/x')
                """,
                (USER_ID, iso(NOW)),
            )
            trial_id = int(cur.lastrowid)

        service_id = self.make_service(
            trial_id,
            NOW - timedelta(days=1),
            NOW + timedelta(days=1),
            service_type=bot.TRIAL_SERVICE_TYPE,
            product_id=bot.TRIAL_PRODUCT_ID,
            product_title=bot.TRIAL_PRODUCT_TITLE,
            service_username="trial_user",
        )

        text = "\n".join(bot.admin_service_dossier_sections(service_id, NOW))

        self.assertIn(f"تست {bot.trial_code(trial_id)}", text)
        self.assertNotIn(f"سفارش مبدأ: #{trial_id}", text)

    def test_trial_dossier(self):
        with bot.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO wireguard_trials(
                    user_id, username, created_at, status, panel_link,
                    delivered_at, delivered_by
                )
                VALUES (?, 'trial_user', ?, 'delivered',
                        'https://panel.test/x', ?, ?)
                """,
                (USER_ID, iso(NOW), iso(NOW), ADMIN_ID),
            )
            trial_id = int(cur.lastrowid)

        text = "\n".join(bot.admin_trial_dossier_sections(trial_id, NOW))

        self.assertIn(f"اکانت تست {bot.trial_code(trial_id)}", text)
        self.assertIn("trial_user", text)
        self.assertIn("https://panel.test/x", text)

    def test_send_report_sections_attaches_keyboard_only_to_last(self):
        sections = ["بخش یک", "بخش دو", "بخش سه"]
        kb = bot.admin_dossier_keyboard("order", self.order_id)

        count = bot.send_report_sections(ADMIN_ID, sections, kb)

        self.assertEqual(count, 3)
        self.assertEqual(len(self.sent), 3)
        self.assertIsNone(self.sent[0][2].get("reply_markup"))
        self.assertIsNone(self.sent[1][2].get("reply_markup"))
        self.assertIsNotNone(self.sent[2][2].get("reply_markup"))

    def test_long_report_is_split_not_truncated(self):
        long_text = "\n".join(f"خط شماره {i} — " + "x" * 40 for i in range(200))

        parts = bot._split_report(long_text)

        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), bot.TELEGRAM_TEXT_LIMIT)
        self.assertEqual("\n".join(parts), long_text)


class StaleOrderTests(OrderDatesBase):
    def test_abandoned_orders_are_closed_and_wallet_released(self):
        fresh = self.add_order(
            status="awaiting_payment", created=NOW - timedelta(hours=1)
        )
        stale = self.add_order(
            status="awaiting_payment",
            created=NOW - timedelta(hours=100),
            wallet_reserved=50000,
        )
        stale_receipt = self.add_order(
            status="awaiting_receipt",
            created=NOW - timedelta(days=9),
        )
        done = self.add_order(
            status="completed", created=NOW - timedelta(days=9)
        )

        with bot.db() as conn:
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(hours=100)), stale),
            )
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(days=9)), stale_receipt),
            )
            conn.execute(
                "UPDATE users SET balance=0 WHERE user_id=?", (USER_ID,)
            )

        closed = bot.expire_stale_orders(now_dt=NOW)

        closed_ids = {o["id"] for o in closed}
        self.assertEqual(closed_ids, {stale, stale_receipt})

        with bot.db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM orders WHERE id=?", (stale,)
                ).fetchone()[0],
                "expired_unpaid",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM orders WHERE id=?", (fresh,)
                ).fetchone()[0],
                "awaiting_payment",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM orders WHERE id=?", (done,)
                ).fetchone()[0],
                "completed",
            )
            balance = conn.execute(
                "SELECT balance FROM users WHERE user_id=?", (USER_ID,)
            ).fetchone()[0]

        self.assertEqual(balance, 50000)

    def test_release_transaction_is_recorded(self):
        stale = self.add_order(
            status="awaiting_payment",
            created=NOW - timedelta(days=5),
            wallet_reserved=30000,
        )
        with bot.db() as conn:
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(days=5)), stale),
            )

        bot.expire_stale_orders(now_dt=NOW)

        rows = bot._order_transactions(stale)
        kinds = [r["kind"] for r in rows]
        self.assertIn("wallet_release", kinds)

    def test_order_in_active_flow_is_not_closed(self):
        stale = self.add_order(
            status="awaiting_receipt", created=NOW - timedelta(days=5)
        )
        with bot.db() as conn:
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(days=5)), stale),
            )

        bot.save_nav(
            USER_ID,
            "awaiting_service_username",
            "invoice",
            {"order_id": stale},
        )

        closed = bot.expire_stale_orders(now_dt=NOW)

        self.assertEqual(closed, [])

    def test_stale_label_is_registered(self):
        self.assertIn("expired_unpaid", bot.ORDER_DATES_STATUS_LABELS)
        self.assertIn("نیمه‌تمام", bot.ORDER_DATES_STATUS_LABELS["expired_unpaid"])

    def test_admin_report_lists_closed_orders(self):
        stale = self.add_order(
            status="awaiting_payment", created=NOW - timedelta(days=5)
        )
        with bot.db() as conn:
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(days=5)), stale),
            )

        bot.expire_stale_orders(now_dt=NOW)

        text, ids = bot.admin_stale_orders_text(now_dt=NOW)

        self.assertEqual(ids, [stale])
        self.assertIn("سفارش‌های نیمه‌تمام", text)
        self.assertIn(bot.order_code(stale), text)
        self.assertIn("راهنمای شناسه‌ها", text)

    def test_closed_orders_do_not_count_as_sales(self):
        stale = self.add_order(
            status="awaiting_payment",
            created=NOW - timedelta(days=5),
            price=500000,
        )
        with bot.db() as conn:
            conn.execute(
                "UPDATE orders SET updated_at=? WHERE id=?",
                (iso(NOW - timedelta(days=5)), stale),
            )

        bot.expire_stale_orders(now_dt=NOW)

        text = bot.admin_sales_report("30", now_dt=NOW)
        self.assertNotIn("500,000", text)


if __name__ == "__main__":
    unittest.main()
