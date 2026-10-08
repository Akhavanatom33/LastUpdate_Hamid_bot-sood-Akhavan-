import os
import tempfile
import unittest


os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ["ADMIN_IDS"] = ""
os.environ.setdefault(
    "DB_PATH",
    os.path.join(tempfile.gettempdir(), "arena-bot-import.sqlite3"),
)

import bot  # noqa: E402


class BotDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = os.path.join(self.temp_dir.name, "shop.sqlite3")
        bot.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_users(self, *user_ids):
        timestamp = bot.now()
        with bot.db() as conn:
            conn.executemany(
                """
                INSERT INTO users(
                    user_id, username, first_name, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (user_id, f"user{user_id}", f"User {user_id}", timestamp, timestamp)
                    for user_id in user_ids
                ],
            )

    def add_trial(self, user_id, status, username=None):
        with bot.db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO wireguard_trials(
                    user_id, username, created_at, status, delivered_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username or f"trial_{user_id}_{status}",
                    bot.now(),
                    status,
                    bot.now() if status == "delivered" else None,
                ),
            )
            return int(cursor.lastrowid)


class GlobalTrialQuotaResetTests(BotDatabaseTestCase):
    def test_reset_gives_only_eligible_users_one_fresh_trial(self):
        self.add_users(1, 2, 3, 4, 5, 6)

        # Existing manual allowances must not affect the reset rule.
        bot.set_wireguard_trial_allowance(1, 9, 99)
        bot.set_wireguard_trial_allowance(2, 10, 99)
        bot.set_wireguard_trial_allowance(3, 0, 99)
        bot.set_wireguard_trial_allowance(4, 8, 99)
        bot.set_wireguard_trial_allowance(5, 99, 99)
        bot.set_wireguard_trial_allowance(6, 0, 99)

        self.add_trial(2, "delivered")
        self.add_trial(3, "delivered", "trial_3_first")
        self.add_trial(3, "delivered", "trial_3_second")
        self.add_trial(4, "pending")
        self.add_trial(5, "delivered")
        self.add_trial(5, "provisioning")
        self.add_trial(6, "failed")

        result = bot.reset_all_wireguard_trial_quotas(admin_id=99)

        self.assertEqual(result["total_users"], 6)
        self.assertEqual(result["renewed_users"], 3)
        self.assertEqual(result["ready_users"], 4)
        self.assertEqual(result["in_progress_users"], 2)

        expected = {
            1: {"allowed": 1, "delivered": 0, "in_progress": 0, "remaining": 1},
            2: {"allowed": 2, "delivered": 1, "in_progress": 0, "remaining": 1},
            3: {"allowed": 3, "delivered": 2, "in_progress": 0, "remaining": 1},
            4: {"allowed": 1, "delivered": 0, "in_progress": 1, "remaining": 0},
            5: {"allowed": 3, "delivered": 1, "in_progress": 1, "remaining": 1},
            6: {"allowed": 1, "delivered": 0, "in_progress": 0, "remaining": 1},
        }
        for user_id, expected_status in expected.items():
            status = bot.wireguard_trial_quota_status(user_id)
            for key, value in expected_status.items():
                self.assertEqual(status[key], value, (user_id, key, status))

        # Double-clicking reset before anyone uses the grant must not stack it.
        bot.reset_all_wireguard_trial_quotas(admin_id=99)
        for user_id, expected_status in expected.items():
            status = bot.wireguard_trial_quota_status(user_id)
            self.assertEqual(status["allowed"], expected_status["allowed"])
            self.assertEqual(status["remaining"], expected_status["remaining"])

        last_reset = bot.get_last_wireguard_trial_quota_reset()
        self.assertEqual(last_reset["admin_id"], 99)
        self.assertEqual(last_reset["total_users"], 6)

    def test_reset_can_be_used_again_after_fresh_trial_is_consumed(self):
        self.add_users(10)
        self.add_trial(10, "delivered")

        bot.reset_all_wireguard_trial_quotas(admin_id=99)
        new_trial_id = bot.create_wireguard_trial(10, "second_trial")
        self.assertIsNotNone(new_trial_id)

        with bot.db() as conn:
            conn.execute(
                """
                UPDATE wireguard_trials
                SET status='delivered', delivered_at=?
                WHERE id=?
                """,
                (bot.now(), new_trial_id),
            )

        self.assertEqual(bot.wireguard_trial_quota_status(10)["remaining"], 0)
        bot.reset_all_wireguard_trial_quotas(admin_id=99)
        status = bot.wireguard_trial_quota_status(10)
        self.assertEqual(status["delivered"], 2)
        self.assertEqual(status["allowed"], 3)
        self.assertEqual(status["remaining"], 1)


class AkhavanProfitTests(BotDatabaseTestCase):
    def test_shared_chatgpt_profit_rates(self):
        self.assertEqual(bot.akhavan_profit_for_product("chatgpt-shared-15"), 25_000)
        self.assertEqual(bot.akhavan_profit_for_product("chatgpt-shared-10"), 37_500)
        self.assertEqual(bot.akhavan_profit_for_product("chatgpt-shared-5"), 50_000)

    def test_two_five_person_orders_add_one_hundred_thousand_profit(self):
        self.add_users(20)
        bot.akhavan_set_balance(0)

        order_ids = []
        with bot.db() as conn:
            # Profit is fixed per product and must not depend on the sale price.
            for sale_price in (1, 9_999_999):
                cursor = conn.execute(
                    """
                    INSERT INTO orders(
                        user_id, product_id, product_title, price, status,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'approved', ?, ?)
                    """,
                    (
                        20,
                        "chatgpt-shared-5",
                        "ChatGPT Plus — اکانت خلوت ۵ نفره",
                        sale_price,
                        bot.now(),
                        bot.now(),
                    ),
                )
                order_ids.append(int(cursor.lastrowid))

        for order_id in order_ids:
            bot.complete_order(order_id)

        with bot.db() as conn:
            sales = conn.execute(
                """
                SELECT COUNT(*) AS count, SUM(amount) AS total
                FROM akhavan_profit_ledger
                WHERE kind='sale'
                """
            ).fetchone()
            balance = bot.akhavan_current_balance(conn)

        self.assertEqual(int(sales["count"]), 2)
        self.assertEqual(int(sales["total"]), 100_000)
        self.assertEqual(balance, 100_000)


if __name__ == "__main__":
    unittest.main()
