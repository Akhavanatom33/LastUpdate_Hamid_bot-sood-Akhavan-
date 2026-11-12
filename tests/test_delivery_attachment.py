import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_tmp = tempfile.mkdtemp()
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ.setdefault("ADMIN_IDS", "1")

import bot  # noqa: E402


class DeliveryAttachmentTest(unittest.TestCase):
    def setUp(self):
        bot.init_db()
        with bot.db() as conn:
            conn.execute("DELETE FROM connection_content")

    def add(self, ctype, file_id=None, text=None, caption=None):
        msg = SimpleNamespace(
            content_type=ctype,
            text=text,
            caption=caption,
            document=SimpleNamespace(file_id=file_id),
            # فوروارد: اطلاعات مبدا نباید ذخیره/ارسال شود
            forward_origin=SimpleNamespace(sender_user=SimpleNamespace(id=1)),
        )
        with mock.patch.object(bot.bot, "send_message"):
            bot.store_connection_content(
                1, msg, bot.DELIVERY_ATTACHMENT_SECTION
            )

    def test_section_registered(self):
        self.assertTrue(bot.is_guide_section(bot.DELIVERY_ATTACHMENT_SECTION))
        kb = bot.admin_guide_keyboard()
        datas = [b.callback_data for row in kb.keyboard for b in row]
        self.assertIn("guide:section:delivery_attachment", datas)

    def test_sends_file_with_caption_without_forward(self):
        self.add("document", file_id="FID", caption="کپشن تست")
        with mock.patch.object(bot.bot, "send_document") as send_doc, \
                mock.patch.object(bot.bot, "forward_message") as fwd:
            n = bot.send_delivery_attachments(99, "wireguard")
        self.assertEqual(n, 1)
        send_doc.assert_called_once_with(99, "FID", caption="کپشن تست")
        fwd.assert_not_called()

    def test_not_sent_for_other_services(self):
        self.add("document", file_id="FID", caption="x")
        with mock.patch.object(bot.bot, "send_document") as send_doc:
            self.assertEqual(bot.send_delivery_attachments(99, "chatgpt"), 0)
        send_doc.assert_not_called()

    def test_failure_does_not_raise(self):
        self.add("document", file_id="FID", caption="x")
        with mock.patch.object(
            bot.bot, "send_document", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(bot.send_delivery_attachments(99, "v2ray"), 0)

    def test_delete_one_or_many(self):
        for i in range(3):
            self.add("document", file_id=f"F{i}", caption=str(i))
        rows = bot.get_connection_content_rows(bot.DELIVERY_ATTACHMENT_SECTION)
        deleted = bot.delete_connection_content_items(
            bot.DELIVERY_ATTACHMENT_SECTION, [rows[0]["id"], rows[2]["id"]]
        )
        self.assertEqual(deleted, 2)
        left = bot.get_connection_content_rows(bot.DELIVERY_ATTACHMENT_SECTION)
        self.assertEqual([r["file_id"] for r in left], ["F1"])


if __name__ == "__main__":
    unittest.main()
