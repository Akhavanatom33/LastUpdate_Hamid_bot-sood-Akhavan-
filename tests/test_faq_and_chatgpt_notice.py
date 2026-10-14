import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ["ADMIN_IDS"] = "777"
os.environ.setdefault(
    "DB_PATH",
    os.path.join(tempfile.gettempdir(), "arena-bot-import.sqlite3"),
)

import bot  # noqa: E402


ADMIN_ID = 777
USER_ID = 555


def callback_datas(markup):
    return [
        button.callback_data
        for row in markup.keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


def button_texts(markup):
    return [
        button.text if hasattr(button, "text") else button
        for row in markup.keyboard
        for button in row
    ]


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = os.path.join(self.temp_dir.name, "shop.sqlite3")
        bot.init_db()
        bot.ADMIN_IDS = set(bot.ADMIN_IDS) | {ADMIN_ID}

        self.sent = []
        patcher = mock.patch.object(
            bot.bot,
            "send_message",
            side_effect=self._record_send,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        for name in ("answer_callback_query", "edit_message_text"):
            p = mock.patch.object(bot.bot, name)
            p.start()
            self.addCleanup(p.stop)

    def _record_send(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=len(self.sent))

    def tearDown(self):
        self.temp_dir.cleanup()

    def admin_message(self, text, content_type="text"):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=ADMIN_ID),
            chat=SimpleNamespace(id=ADMIN_ID, type="private"),
            content_type=content_type,
            text=text,
            message_id=1,
        )


class FaqStorageTests(BaseCase):
    def test_add_remove_and_order(self):
        first = bot.add_faq_item("سوال اول؟", "پاسخ اول", added_by=ADMIN_ID)
        second = bot.add_faq_item("سوال دوم؟", "پاسخ دوم", added_by=ADMIN_ID)
        third = bot.add_faq_item("سوال سوم؟", "پاسخ سوم", added_by=ADMIN_ID)

        self.assertEqual(
            [i["question"] for i in bot.get_faq_items()],
            ["سوال اول؟", "سوال دوم؟", "سوال سوم؟"],
        )

        # فقط همان سوال خاص حذف می‌شود و بقیه می‌مانند
        self.assertTrue(bot.remove_faq_item(second))
        self.assertFalse(bot.remove_faq_item(second))
        self.assertEqual(
            [i["id"] for i in bot.get_faq_items()], [first, third]
        )

        # ترتیب بعد از حذف و افزودن دوباره حفظ می‌شود
        fourth = bot.add_faq_item("سوال چهارم؟", "پاسخ چهارم")
        self.assertEqual(
            [i["id"] for i in bot.get_faq_items()], [first, third, fourth]
        )

    def test_pagination_helpers(self):
        for n in range(bot.FAQ_PAGE_SIZE * 2 + 1):
            bot.add_faq_item(f"سوال {n}", "پاسخ")

        items = bot.get_faq_items()
        _, total_pages = bot.faq_clamp_page(0, len(items))
        self.assertEqual(total_pages, 3)
        self.assertEqual(bot.faq_clamp_page(99, len(items))[0], 2)
        self.assertEqual(bot.faq_clamp_page(-4, len(items))[0], 0)
        self.assertEqual(bot.faq_clamp_page("abc", len(items))[0], 0)

        # هیچ صفحه‌ای بیش از حد مجاز دکمه ندارد
        kb = bot.faq_user_list_keyboard(0)
        question_buttons = [
            d for d in callback_datas(kb) if d.startswith("faq:q:")
        ]
        self.assertEqual(len(question_buttons), bot.FAQ_PAGE_SIZE)

        admin_kb = bot.admin_faq_keyboard(0)
        delete_buttons = [
            d for d in callback_datas(admin_kb)
            if d.startswith("admin:faq_del:")
        ]
        self.assertEqual(len(delete_buttons), bot.FAQ_PAGE_SIZE)

    def test_long_questions_get_short_button_labels(self):
        label = bot.faq_short_label("الف" * 100)
        self.assertLessEqual(len(label), 45)
        self.assertTrue(label.endswith("…"))


class FaqKeyboardTests(BaseCase):
    def test_user_menus_have_faq_button(self):
        self.assertIn("❓ سوالات متداول", [
            text
            for row in bot.reply_main_keyboard().keyboard
            for text in (
                [b["text"] if isinstance(b, dict) else getattr(b, "text", b)
                 for b in row]
            )
        ])
        self.assertIn("faq", callback_datas(bot.main_menu_inline()))

    def test_admin_panel_has_faq_button_for_every_admin_context(self):
        # چت خصوصی ادمین
        private = bot.admin_panel_keyboard(ADMIN_ID, ADMIN_ID)
        self.assertIn("admin:faq", callback_datas(private))
        # بدون chat_id (مثلاً فراخوانی قدیمی)
        bare = bot.admin_panel_keyboard()
        self.assertIn("admin:faq", callback_datas(bare))

    def test_faq_name_is_not_hidden_behind_eylan(self):
        # نام EylanPanel نباید جایی در بخش سوالات متداول ظاهر شود
        bot.add_faq_item("سوال؟", "پاسخ")
        blob = bot.faq_user_list_text() + " ".join(
            callback_datas(bot.faq_user_list_keyboard(0))
        )
        self.assertNotIn("Eylan", blob)


class FaqAdminFlowTests(BaseCase):
    def test_admin_can_add_question_in_two_steps(self):
        bot.set_admin_state(ADMIN_ID, "faq_add_question", {})

        bot.handle_faq_add_question_message(
            self.admin_message("چطور سرویس را تمدید کنم؟")
        )
        state = bot.get_admin_state(ADMIN_ID)
        self.assertEqual(state["mode"], "faq_add_answer")
        self.assertEqual(
            state["data"]["question"], "چطور سرویس را تمدید کنم؟"
        )

        bot.handle_faq_add_answer_message(
            self.admin_message("از بخش «📦 سرویس های من» وارد شوید."),
            state,
        )

        self.assertIsNone(bot.get_admin_state(ADMIN_ID))
        items = bot.get_faq_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["added_by"], ADMIN_ID)
        self.assertIn("سرویس های من", items[0]["answer"])

    def test_invalid_input_is_rejected_and_state_kept(self):
        bot.set_admin_state(ADMIN_ID, "faq_add_question", {})

        bot.handle_faq_add_question_message(
            self.admin_message(None, content_type="photo")
        )
        bot.handle_faq_add_question_message(self.admin_message("   "))
        bot.handle_faq_add_question_message(
            self.admin_message("ا" * (bot.FAQ_QUESTION_MAX_LEN + 1))
        )

        self.assertEqual(
            bot.get_admin_state(ADMIN_ID)["mode"], "faq_add_question"
        )
        self.assertEqual(bot.get_faq_items(), [])

        bot.set_admin_state(ADMIN_ID, "faq_add_answer", {"question": "q"})
        state = bot.get_admin_state(ADMIN_ID)
        bot.handle_faq_add_answer_message(
            self.admin_message("ب" * (bot.FAQ_ANSWER_MAX_LEN + 1)), state
        )
        self.assertEqual(bot.get_faq_items(), [])
        self.assertEqual(
            bot.get_admin_state(ADMIN_ID)["mode"], "faq_add_answer"
        )

    def test_html_in_text_is_escaped_for_users(self):
        faq_id = bot.add_faq_item("<b>x</b> & y؟", "<i>پاسخ</i> & z")
        call = SimpleNamespace(
            id="1",
            data=f"faq:q:{faq_id}:0",
            from_user=SimpleNamespace(id=USER_ID),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=USER_ID), message_id=10
            ),
        )

        bot.cb_faq_question(call)

        args, kwargs = bot.bot.edit_message_text.call_args
        text = args[0]
        self.assertNotIn("<i>", text)
        self.assertIn("&lt;i&gt;", text)
        self.assertIn("&amp;", text)

    def test_delete_requires_confirmation_and_only_removes_one(self):
        keep = bot.add_faq_item("نگه دار", "پاسخ")
        drop = bot.add_faq_item("حذف شود", "پاسخ")

        def make_call(data, user=ADMIN_ID):
            return SimpleNamespace(
                id="1",
                data=data,
                from_user=SimpleNamespace(id=user),
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=user), message_id=10
                ),
            )

        # مرحله اول فقط تأیید می‌خواهد و چیزی حذف نمی‌کند
        bot.cb_admin_faq_delete_ask(make_call(f"admin:faq_del:{drop}:0"))
        self.assertEqual(len(bot.get_faq_items()), 2)

        # غیرادمین نمی‌تواند حذف کند
        bot.cb_admin_faq_delete_confirm(
            make_call(f"admin:faq_delok:{drop}:0", user=USER_ID)
        )
        self.assertEqual(len(bot.get_faq_items()), 2)

        bot.cb_admin_faq_delete_confirm(
            make_call(f"admin:faq_delok:{drop}:0")
        )
        self.assertEqual(
            [i["id"] for i in bot.get_faq_items()], [keep]
        )

    def test_non_admin_cannot_open_or_start_add(self):
        call = SimpleNamespace(
            id="1",
            data="admin:faq_add",
            from_user=SimpleNamespace(id=USER_ID),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=USER_ID), message_id=10
            ),
        )
        bot.cb_admin_faq_add(call)
        self.assertIsNone(bot.get_admin_state(USER_ID))


class ChatGptNoticeTests(BaseCase):
    def test_notice_text_matches_requested_message(self):
        text = bot.chatgpt_shared_notice_template()
        for expected in (
            "✅ اطلاعیه شماه یک ❌",
            "🌟 استفاده اشتراکی نیاز به همراهی زیاد و احترام به حقوق بقیه داره؛",
            "🚫 لطفا از «شکن» و «۴۰۳» برای لاگین و کار با gpt استفاده نکنید. "
            "VPNهای معتبر توصیه ماست.",
            "🚫 چت بقیه رو نخوانید.",
            "🚫 به هیچ وجه چت باقی کاربران رو پاک نکنید، به هیچ وجه!",
            "🔒 سعی کنید اطلاعات خصوصی و شخصی خودتون رو در چت ننویسید و اگر نوشتین پاک کنید.",
            "🔑 توصیه میکنیم رمز یا توکن به سرویس ارسال نکنید، اگر ارسال کردید "
            "تغییرش بدید یا چت رو پاک کنید.",
        ):
            self.assertIn(expected, text)

    def test_notice_failure_does_not_raise(self):
        bot.bot.send_message.side_effect = RuntimeError("blocked by user")
        self.assertFalse(bot.send_chatgpt_shared_notice(USER_ID))

    def _make_order(self):
        t = bot.now()
        with bot.db() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, first_name, created_at, updated_at)
                VALUES (?, 'buyer', 'Buyer', ?, ?)
                """,
                (USER_ID, t, t),
            )
            cursor = conn.execute(
                """
                INSERT INTO orders(
                    user_id, product_id, product_title, price, status,
                    assigned_admin_id, created_at, updated_at
                )
                VALUES (?, 'chatgpt-shared-5', 'ChatGPT اشتراکی ۵ نفره', 100000,
                        'fulfilling', ?, ?, ?)
                """,
                (USER_ID, ADMIN_ID, t, t),
            )
            return int(cursor.lastrowid)

    def test_order_delivery_sends_two_separate_messages(self):
        order_id = self._make_order()
        bot.set_admin_state(ADMIN_ID, "fulfill", {"order_id": order_id})

        handled = bot.deliver_order_payload(
            ADMIN_ID, self.admin_message("customer@gmail.com")
        )
        self.assertTrue(handled)

        to_user = [s for s in self.sent if s[0] == USER_ID]
        self.assertEqual(len(to_user), 2)

        first, second = to_user
        self.assertIn("دسترسی اکانت ChatGPT", first[1])
        self.assertIn("customer@gmail.com", first[1])
        self.assertIsNotNone(first[2].get("reply_markup"))

        self.assertEqual(second[1], bot.chatgpt_shared_notice_template())
        self.assertNotIn("دسترسی اکانت ChatGPT", second[1])

        with bot.db() as conn:
            status = conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "completed")

    def test_order_still_completes_if_notice_cannot_be_sent(self):
        order_id = self._make_order()
        bot.set_admin_state(ADMIN_ID, "fulfill", {"order_id": order_id})

        calls = {"n": 0}

        def flaky(chat_id, text, **kwargs):
            if chat_id == USER_ID:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("telegram error")
            self.sent.append((chat_id, text, kwargs))
            return SimpleNamespace(message_id=1)

        bot.bot.send_message.side_effect = flaky

        bot.deliver_order_payload(
            ADMIN_ID, self.admin_message("customer@gmail.com")
        )

        with bot.db() as conn:
            status = conn.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "completed")

    def test_personal_plus_account_does_not_get_shared_notice(self):
        text = bot.chatgpt_personal_delivery_template("me@gmail.com")
        self.assertNotIn("اطلاعیه", text)


if __name__ == "__main__":
    unittest.main()
