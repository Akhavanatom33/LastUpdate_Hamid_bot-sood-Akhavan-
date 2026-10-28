import os
import tempfile
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault(
    "DB_PATH",
    os.path.join(tempfile.gettempdir(), "arena-bot-import.sqlite3"),
)

import bot  # noqa: E402


# Nodeهایی که EylanPanel برای API Key فروشگاه برمی‌گرداند (اکتبر ۲۰۲۶)
VISIBLE_NODES = [
    {"id": 4, "name": "Germany-4"},
    {"id": 13, "name": "Turkey-3"},
    {"id": 3, "name": "Germany-3"},
    {"id": 2, "name": "Germany-2"},
    {"id": 14, "name": "Turkey-4"},
    {"id": 11, "name": "Turkey-1"},
    {"id": 12, "name": "Turkey-2"},
]


class FakeClient:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_nodes(self):
        return list(self._nodes)


class EylanNodeNameTests(unittest.TestCase):
    def test_every_plan_resolves_against_current_panel_nodes(self):
        # اگر نام Nodeای در پنل عوض شود و در eylan_plans.json نماند،
        # این تست قبل از Deploy خطا می‌دهد.
        client = FakeClient(VISIBLE_NODES)

        for product_id, plan in bot.EYLAN_PLANS.items():
            with self.subTest(product=product_id):
                ids = bot._resolve_required_node_ids(client, dict(plan))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertGreater(len(ids), 0)

    def test_default_env_names_resolve(self):
        client = FakeClient(VISIBLE_NODES)
        ids = bot._resolve_required_node_ids(client, {})
        self.assertEqual(ids, [4, 2, 11, 13])  # Germany-4, Germany-2, Turkey-1, Turkey-3

    def test_old_name_fails_with_clear_message(self):
        client = FakeClient(VISIBLE_NODES)
        with self.assertRaises(bot.EylanPanelError) as ctx:
            bot._resolve_required_node_ids(
                client, {"required_node_names": ["Germany-4", "Turkey-1-A"]}
            )
        message = str(ctx.exception)
        self.assertIn("Turkey-1-A", message)
        self.assertIn("Turkey-1", message)  # در لیست Nodeهای قابل مشاهده

    def test_trial_payload_contains_four_node_ids(self):
        client = FakeClient(VISIBLE_NODES)
        payload = bot.eylan_payload_for_product(
            bot.TRIAL_PRODUCT_ID, "trial_user", client=client
        )
        self.assertEqual(payload["nodes"], [4, 2, 11, 13])
        self.assertTrue(payload["enable_wg1"])


if __name__ == "__main__":
    unittest.main()
