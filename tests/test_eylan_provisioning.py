"""
تست‌های تحویل خودکار EylanPanel.

این فایل یک «پنل جعلی» دارد که *همان رفتارهای واقعی* پنل را شبیه‌سازی می‌کند
(بر اساس مستندات رسمی v4.0.1.4):

  * POST /api/v1/users فیلدهای ناشناخته مثل enable_wg2 / enable_wg3 را
    بی‌صدا نادیده می‌گیرد و فقط `wg_instances` را می‌پذیرد.
  * PUT /api/v1/users/<username> فیلد `expiry_days` را قبول ندارد؛ تمدید
    فقط با `expiry_date_str` واقعاً اعمال می‌شود.
  * اگر کلید API نماینده باشد و نام کاربری تکراری بدهید، پنل یک عدد تصادفی
    به انتهای نام اضافه می‌کند و نام نهایی را در `created_users` برمی‌گرداند.
  * پاسخ `success: true` بدون ساخت واقعی کاربر هم ممکن است (همان حالتی که
    قبلاً ربات را به «✅ ساخته شد» اشتباه می‌انداخت).

پس اگر ربات دوباره به همان باگ‌ها برگردد، این تست‌ها قرمز می‌شوند.
"""

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
)

import bot  # noqa: E402
from eylanpanel_api import (  # noqa: E402
    EylanPanelError,
    EylanPanelNotFound,
    EylanPanelVerificationError,
)


VISIBLE_NODES = [
    {"id": 3, "name": "Germany-3"},
    {"id": 4, "name": "Germany-4"},
    {"id": 2, "name": "Germany-2"},
    {"id": 11, "name": "Turkey-1"},
    {"id": 13, "name": "Turkey-3"},
    {"id": 12, "name": "Turkey-2"},
    {"id": 14, "name": "Turkey-4"},
]

#: فیلدهایی که پنل واقعاً می‌شناسد (مستندات v4.0.1.4).
POST_USER_FIELDS = {
    "username", "bulk_count", "bulk_prefix", "max_clients", "data_limit",
    "data_limit_unit", "activation_type", "expiry_date_str", "expiry_days",
    "pending_activation_days", "nodes", "enable_l2tp", "l2tp_password",
    "enable_cisco", "cisco_password", "enable_wg1", "wg_instances",
    "selected_wg_instances", "enable_openvpn", "enable_singbox",
    "singbox_inbounds", "selected_singbox_inbounds", "notes", "sub_admin_id",
}

PUT_USER_FIELDS = {
    "max_clients", "data_limit", "data_limit_unit", "notes", "activation_type",
    "pending_activation_days", "expiry_date", "expiry_date_str", "nodes",
    "enable_l2tp", "l2tp_password", "enable_cisco", "cisco_password",
    "enable_wg1", "wg_instances", "selected_wg_instances", "enable_openvpn",
    "enable_singbox", "singbox_inbounds", "selected_singbox_inbounds",
    "reset_activation", "is_disabled",
}


class FakePanel:
    """شبیه‌ساز پنل EylanPanel با همان قوانین مستندشده."""

    def __init__(self, nodes=None, break_creation=False):
        self.users = {}
        self.nodes = list(nodes if nodes is not None else VISIBLE_NODES)
        self.calls = []
        self.unknown_post_fields = set()
        self.unknown_put_fields = set()
        self.wg_instances_seen = []
        #: اگر True باشد، POST پاسخ موفق می‌دهد ولی کاربری نمی‌سازد
        #: (دقیقاً همان سناریوی «موفق ولی هیچ اتفاقی نیفتاد»).
        self.break_creation = break_creation
        self.reset_traffic_calls = []
        self.existing_usernames = set()
        #: اگر True باشد، PUT را می‌پذیرد ولی تاریخ انقضا را عوض نمی‌کند
        #: (پنل فیلد را بی‌صدا نادیده گرفته است).
        self.ignore_expiry_update = False

    # ── endpointها ──────────────────────────────────────────────────────────

    def get_nodes(self):
        self.calls.append(("nodes", None))
        return list(self.nodes)

    def get_singbox_inbounds(self):
        return [{"tag": "reality-main", "type": "vless"}]

    def get_my_wireguard_instances(self):
        return [
            {"id": 1, "name": "wg1"},
            {"id": 2, "name": "wg2"},
            {"id": 3, "name": "wg3"},
        ]

    def status(self):
        return {"success": True, "status": "ok", "version": "4.0.1.4"}

    def get_users_stats(self):
        return {
            "success": True,
            "total_users": len(self.users),
            "allowed_resources": {
                "nodes": [
                    {"id": n["id"], "name": n["name"], "status": "online"}
                    for n in self.nodes
                ],
                "wg_instances": self.get_my_wireguard_instances(),
                "singbox_inbounds": self.get_singbox_inbounds(),
            },
        }

    def allowed_resources(self):
        return self.get_users_stats()["allowed_resources"]

    def get_user(self, username):
        self.calls.append(("get_user", username))
        record = self.users.get(username)
        return dict(record) if record else None

    def require_user(self, username):
        record = self.get_user(username)
        if record is None:
            # مثل کلاینت واقعی: NotFound نه خطای عمومی
            raise EylanPanelNotFound(f"user '{username}' was not found")
        return record

    def get_user_status(self, username):
        return self.require_user(username)

    def list_users(self):
        return [dict(u) for u in self.users.values()]

    def create_user_ex(self, payload):
        self.calls.append(("create", dict(payload)))

        self.unknown_post_fields |= set(payload) - POST_USER_FIELDS
        self.wg_instances_seen.append(payload.get("wg_instances"))

        requested = str(payload.get("username") or "").strip()

        final = requested
        if requested in self.existing_usernames:
            # رفتار واقعی پنل برای کلید نماینده: افزودن عدد تصادفی
            final = f"{requested}731"

        if self.break_creation:
            return {
                "username": requested,
                "requested_username": requested,
                "renamed": False,
                "message": "1 user(s) created.",
                "created_users": [requested],
                "raw": {"success": True},
                "reconciled": False,
            }

        self.users[final] = self._record_from_payload(final, payload)

        return {
            "username": final,
            "requested_username": requested,
            "renamed": final != requested,
            "message": "1 user(s) created.",
            "created_users": [final],
            "raw": {"success": True, "created_users": [final]},
            "reconciled": False,
        }

    def create_user(self, payload):
        return self.create_user_ex(payload)["username"]

    def update_user(self, username, payload):
        self.calls.append(("update", username, dict(payload)))

        self.unknown_put_fields |= set(payload) - PUT_USER_FIELDS

        record = self.users.get(username)
        if record is None:
            raise EylanPanelError(f"user '{username}' was not found")

        # ── رفتار واقعی پنل ──────────────────────────────────────────────────
        # expiry_days در PUT مستند نشده و نادیده گرفته می‌شود.
        if (
            not self.ignore_expiry_update
            and "expiry_date_str" in payload
            and payload["expiry_date_str"]
        ):
            record["expiry_date_actual_iso"] = (
                f"{payload['expiry_date_str']}T23:59:59+00:00"
            )
            record["expiry_date_str"] = payload["expiry_date_str"]
        # اگر فقط expiry_days فرستاده شود ⇒ هیچ تغییری نمی‌کند (باگ قدیمی ربات)

        for key in (
            "data_limit", "data_limit_unit", "max_clients", "nodes",
            "enable_wg1", "enable_openvpn", "enable_singbox", "notes",
        ):
            if key in payload:
                record[key] = payload[key]

        if "wg_instances" in payload:
            record["allowed_wg_instances"] = list(payload["wg_instances"])

        return {"success": True}

    def reset_traffic(self, username):
        self.reset_traffic_calls.append(username)
        record = self.users.get(username)
        if record is not None:
            record["total_traffic_bytes"] = 0
            record["is_active"] = True
        return {"success": True, "message": f"Traffic reset for '{username}'."}

    def get_subscription_link(self, username):
        self.calls.append(("sub", username))
        if username not in self.users:
            raise EylanPanelNotFound(f"user '{username}' was not found")
        return f"https://panel.test/sub/HASH/{username}"

    def verify_user_exists(self, username, *, expected_data_limit=None,
                           tolerance_gb=0.01):
        if username not in self.users:
            raise EylanPanelVerificationError(
                f"پنل پاسخ موفق داد ولی کاربر '{username}' وجود ندارد"
            )

        record = self.get_user_status(username)

        if expected_data_limit:
            remote = record.get("data_limit")
            if remote is not None and abs(float(remote) - float(expected_data_limit)) > tolerance_gb:
                raise EylanPanelVerificationError(
                    f"data limit mismatch: {remote} != {expected_data_limit}"
                )

        return record

    def verify_expiry_extended(self, username, *, expected_date):
        if username not in self.users:
            raise EylanPanelVerificationError(
                f"پنل پاسخ موفق داد ولی کاربر '{username}' وجود ندارد"
            )

        record = self.get_user_status(username)

        if expected_date is None:
            return record

        raw = record.get("expiry_date_actual_iso")
        if not raw:
            raise EylanPanelVerificationError("پنل تاریخ انقضا برنگرداند")

        remote_date = datetime.fromisoformat(raw).date()

        if remote_date < expected_date - timedelta(days=1):
            raise EylanPanelVerificationError(
                f"تمدید اعمال نشد: پنل {remote_date.isoformat()} دارد، "
                f"انتظار {expected_date.isoformat()}"
            )

        return record

    def describe_configuration(self):
        return {
            "api_key_set": True,
            "api_key_length": 10,
            "configured_domains": ["panel.test"],
            "base_urls_tried": ["https://panel.test"],
            "allow_http": False,
            "timeout": 20.0,
        }

    # ── داخلی ───────────────────────────────────────────────────────────────

    def _record_from_payload(self, username, payload):
        expiry_iso = None
        expiry_date = None

        if payload.get("activation_type", "fixed_date") == "fixed_date":
            days = int(payload.get("expiry_days") or 30)
            expiry_date = (NOW + timedelta(days=days)).date()
            expiry_iso = f"{expiry_date.isoformat()}T23:59:59+00:00"

        return {
            "username": username,
            "success": True,
            "is_active": True,
            "is_online": False,
            "data_limit": payload.get("data_limit"),
            "data_limit_unit": payload.get("data_limit_unit", "GB"),
            "max_clients": payload.get("max_clients", 1),
            "activation_type": payload.get("activation_type", "fixed_date"),
            "expiry_date_actual_iso": expiry_iso,
            "expiry_date_str": expiry_date.isoformat() if expiry_date else None,
            "allowed_nodes": list(payload.get("nodes") or []),
            "enable_wg1": bool(payload.get("enable_wg1")),
            "allowed_wg_instances": list(payload.get("wg_instances") or []),
            "enable_singbox": bool(payload.get("enable_singbox")),
            "allowed_singbox_inbounds": list(payload.get("singbox_inbounds") or []),
            "total_traffic_bytes": 0,
            "notes": payload.get("notes"),
            "first_connection_at_iso": None,
        }

    def seed_user(self, username, **overrides):
        record = self._record_from_payload(username, {"data_limit": 50})
        record.update(overrides)
        self.users[username] = record
        return record


class EylanProvisioningBase(OrderDatesBase):
    def setUp(self):
        super().setUp()

        self.panel = FakePanel()

        self._client_patch = mock.patch.object(
            bot, "get_eylan_client", lambda: self.panel
        )
        self._client_patch.start()
        self.addCleanup(self._client_patch.stop)

        self._key_patch = mock.patch.object(bot, "EYLAN_API_KEY", "test-key")
        self._key_patch.start()
        self.addCleanup(self._key_patch.stop)

        # محاسبه‌ی تاریخ تمدید باید نسبت به یک «اکنون» قطعی سنجیده شود.
        self._eylan_now_patch = mock.patch.object(bot, "_eylan_now", lambda: NOW)
        self._eylan_now_patch.start()
        self.addCleanup(self._eylan_now_patch.stop)

    def add_fulfilling_order(
        self,
        username="ali_vpn",
        product_id="wireguard-50gb-1m",
        title="وایرساک / وایرگارد — ۵۰ گیگ ۱ ماهه",
        renewal_for_service=None,
        **kwargs,
    ):
        order_id = self.add_order(
            status="fulfilling",
            product_id=product_id,
            title=title,
            assigned_admin_id=ADMIN_ID,
            user_id=USER_ID,
            service_username=username,
            renewal_for_service=renewal_for_service,
            **kwargs,
        )
        return order_id

    def get_order(self, order_id):
        with bot.db() as conn:
            return conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()

    def admin_texts(self):
        return [text for _chat, text, _kw in self.sent]

    def last_admin_text(self):
        for _chat, text, _kw in reversed(self.sent):
            if text:
                return text
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# ۱) payload درست مطابق مستندات
# ══════════════════════════════════════════════════════════════════════════════

class PayloadContractTests(EylanProvisioningBase):
    def test_wireguard_payload_uses_wg_instances_not_enable_wgN(self):
        payload = bot.eylan_payload_for_product(
            "wireguard-50gb-1m", "ali_vpn", client=self.panel
        )

        # فیلدهای enable_wg2 / enable_wg3 در API وجود ندارند ⇒ پنل نادیده می‌گرفت
        self.assertNotIn("enable_wg2", payload)
        self.assertNotIn("enable_wg3", payload)

        self.assertEqual(payload["wg_instances"], ["wg2", "wg3"])
        self.assertTrue(payload["enable_wg1"])
        self.assertEqual(payload["nodes"], [3, 4, 2, 11, 13, 12, 14])
        self.assertEqual(payload["data_limit"], 50)
        self.assertEqual(payload["expiry_days"], 30)
        self.assertEqual(payload["activation_type"], "fixed_date")
        self.assertEqual(payload["username"], "ali_vpn")

    def test_every_product_payload_only_uses_documented_fields(self):
        for product_id, plan in bot.EYLAN_PLANS.items():
            with self.subTest(product=product_id):
                payload = bot.eylan_payload_for_product(
                    product_id, "sample_user", client=self.panel
                )
                unknown = set(payload) - POST_USER_FIELDS
                self.assertEqual(
                    unknown, set(),
                    f"{product_id} فیلد(های) نامعتبر می‌فرستد: {sorted(unknown)}"
                )

    def test_renewal_payload_uses_expiry_date_str_not_expiry_days(self):
        """
        باگ اصلی تمدید: PUT فیلد expiry_days را قبول ندارد، پس تمدید
        «موفق» گزارش می‌شد ولی تاریخ انقضا در پنل تکان نمی‌خورد.
        """

        self.panel.seed_user(
            "ali_vpn",
            expiry_date_actual_iso=(NOW + timedelta(days=5)).strftime(
                "%Y-%m-%dT23:59:59+00:00"
            ),
        )

        remote = self.panel.get_user("ali_vpn")

        payload = bot.eylan_payload_for_product(
            "wireguard-50gb-1m",
            "ali_vpn",
            for_update=True,
            client=self.panel,
            remote=remote,
        )

        self.assertNotIn("expiry_days", payload)
        self.assertNotIn("username", payload)
        self.assertIn("expiry_date_str", payload)

        unknown = set(payload) - PUT_USER_FIELDS
        self.assertEqual(unknown, set(), sorted(unknown))

        # تمدید نباید سرویس فعال را کوتاه کند: ۵ روز باقی‌مانده + ۳۰ روز
        expected = (NOW + timedelta(days=5) + timedelta(days=30)).date()
        self.assertEqual(payload["expiry_date_str"], expected.isoformat())

    def test_renewal_from_expired_service_starts_from_today(self):
        self.panel.seed_user(
            "ali_vpn",
            expiry_date_actual_iso=(NOW - timedelta(days=10)).strftime(
                "%Y-%m-%dT23:59:59+00:00"
            ),
        )

        payload = bot.eylan_payload_for_product(
            "wireguard-50gb-1m",
            "ali_vpn",
            for_update=True,
            client=self.panel,
            remote=self.panel.get_user("ali_vpn"),
        )

        self.assertEqual(
            payload["expiry_date_str"],
            (NOW + timedelta(days=30)).date().isoformat(),
        )

    def test_flexible_days_plan_never_sends_expiry_days_on_create(self):
        payload = bot.eylan_payload_for_product(
            bot.TRIAL_PRODUCT_ID, "trial_user", client=self.panel
        )

        self.assertEqual(payload["activation_type"], "flexible_days")
        self.assertNotIn("expiry_days", payload)
        self.assertGreaterEqual(payload["pending_activation_days"], 1)

    def test_notes_carry_order_reference(self):
        order_id = self.add_fulfilling_order()
        order = self.get_order(order_id)

        note = bot.eylan_order_note(order, kind="create")

        self.assertIn(f"order #{order_id}", note)
        self.assertIn(str(USER_ID), note)

    def test_notes_can_be_disabled(self):
        with mock.patch.object(bot, "EYLAN_WRITE_ORDER_NOTES", False):
            self.assertIsNone(bot.eylan_order_note(None))


# ══════════════════════════════════════════════════════════════════════════════
# ۲) ساخت خودکار: واقعاً در پنل ساخته شود و راستی‌آزمایی شود
# ══════════════════════════════════════════════════════════════════════════════

class CreateProvisioningTests(EylanProvisioningBase):
    def test_create_actually_writes_user_to_panel(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")
        order = self.get_order(order_id)

        result = bot.provision_eylan_order_with_api(order)

        self.assertEqual(result["action"], "created")
        self.assertEqual(result["username"], "ali_vpn")
        self.assertIn("ali_vpn", self.panel.users)
        self.assertTrue(result["verified"])
        self.assertTrue(result["link"].startswith("https://panel.test/sub/"))

        # وضعیت سفارش در دیتابیس
        order = self.get_order(order_id)
        self.assertEqual(order["api_status"], "verified")
        self.assertEqual(order["api_username"], "ali_vpn")
        self.assertTrue(order["api_verified_at"])
        self.assertTrue(order["api_panel_snapshot"])

        # پنل هم wg2/wg3 را گرفته باشد
        self.assertEqual(
            self.panel.users["ali_vpn"]["allowed_wg_instances"], ["wg2", "wg3"]
        )
        self.assertEqual(self.panel.unknown_post_fields, set())

    def test_silent_panel_noop_is_reported_as_error_not_success(self):
        """
        مهم‌ترین تست این بخش: اگر پنل «موفق» بگوید ولی کاربری نسازد،
        ربات باید خطا بدهد — نه «✅ ساخته و تحویل داده شد».
        """

        self.panel.break_creation = True
        order_id = self.add_fulfilling_order(username="ghost_user")

        bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)

        self.assertNotIn("ghost_user", self.panel.users)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], "fulfilling")  # تکمیل‌نشده باقی می‌ماند
        self.assertEqual(order["api_status"], "failed_verification")

        admin_text = "\n".join(self.admin_texts())
        self.assertNotIn("به‌صورت خودکار ساخته و تحویل داده شد", admin_text)
        self.assertIn("تغییر واقعاً اعمال نشده", admin_text)

    def test_panel_rename_is_reported_to_admin(self):
        self.panel.existing_usernames.add("taken_user")

        order_id = self.add_fulfilling_order(username="taken_user")
        order = self.get_order(order_id)

        result = bot.provision_eylan_order_with_api(order)

        self.assertTrue(result["renamed"])
        self.assertEqual(result["requested_username"], "taken_user")
        self.assertEqual(result["username"], "taken_user731")
        self.assertIn("taken_user731", self.panel.users)

        order = self.get_order(order_id)
        self.assertEqual(order["service_username"], "taken_user731")
        self.assertEqual(order["api_renamed_from"], "taken_user")

    def test_username_owned_by_another_user_blocks_provisioning(self):
        other_id = USER_ID + 1
        self.add_user(other_id, "other", "Other")
        self.panel.seed_user("shared_name")

        with bot.db() as conn:
            conn.execute(
                "UPDATE active_services SET service_username=? WHERE 1=0",
                ("shared_name",),
            )

        # نام کاربری در پنل هست ولی در دیتابیس ربات به این کاربر تعلق ندارد
        order_id = self.add_fulfilling_order(username="shared_name")
        order = self.get_order(order_id)

        with self.assertRaises(EylanPanelError):
            bot.provision_eylan_order_with_api(order)


# ══════════════════════════════════════════════════════════════════════════════
# ۳) میان‌بُر وضعیت ذخیره‌شده دیگر دروغ نمی‌گوید
# ══════════════════════════════════════════════════════════════════════════════

class CachedStateTests(EylanProvisioningBase):
    def _mark_delivered(self, order_id, username, link):
        bot.save_order_api_state(
            order_id,
            api_username=username,
            api_link=link,
            api_status="delivered",
            api_verified_at=bot.now(),
        )

    def test_cached_state_is_only_reused_when_panel_user_still_exists(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")
        self._mark_delivered(order_id, "ali_vpn", "https://panel.test/sub/OLD/ali_vpn")

        # کاربر در پنل وجود ندارد ⇒ باید دوباره ساخته شود
        order = self.get_order(order_id)
        result = bot.provision_eylan_order_with_api(order)

        self.assertEqual(result["action"], "created")
        self.assertIn("ali_vpn", self.panel.users)

    def test_cached_state_reused_only_after_live_panel_check(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")
        self.panel.seed_user("ali_vpn")
        self._mark_delivered(order_id, "ali_vpn", "https://panel.test/sub/HASH/ali_vpn")

        order = self.get_order(order_id)
        result = bot.provision_eylan_order_with_api(order)

        self.assertEqual(result["action"], "reused")
        # هیچ ساخت/ویرایشی روی پنل انجام نشده
        self.assertNotIn("create", [c[0] for c in self.panel.calls])
        self.assertNotIn("update", [c[0] for c in self.panel.calls])

    def test_force_flag_always_redoes_the_panel_operation(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")
        self.panel.seed_user("ali_vpn")
        self._mark_delivered(order_id, "ali_vpn", "https://panel.test/sub/HASH/ali_vpn")

        order = self.get_order(order_id)
        result = bot.provision_eylan_order_with_api(order, force=True)

        # اکانت وجود دارد ⇒ مسیر تمدید، ولی حتماً یک PUT واقعی زده می‌شود
        self.assertEqual(result["action"], "renewed")
        self.assertIn("update", [c[0] for c in self.panel.calls])

    def test_retry_button_uses_force(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")
        self.panel.seed_user("ali_vpn")
        self._mark_delivered(order_id, "ali_vpn", "https://panel.test/sub/HASH/ali_vpn")

        call = self.call(f"fulfill:retry:{order_id}")
        bot.cb_fulfill_retry(call)

        self.assertIn("update", [c[0] for c in self.panel.calls])


# ══════════════════════════════════════════════════════════════════════════════
# ۴) تمدید خودکار
# ══════════════════════════════════════════════════════════════════════════════

class RenewalProvisioningTests(EylanProvisioningBase):
    def _renewal_setup(self, days_left=5):
        self.panel.seed_user(
            "ali_vpn",
            expiry_date_actual_iso=(NOW + timedelta(days=days_left)).strftime(
                "%Y-%m-%dT23:59:59+00:00"
            ),
            total_traffic_bytes=900_000_000,
        )

        first_order = self.add_order(
            status="completed",
            completed=NOW - timedelta(days=25),
            service_username="ali_vpn",
        )
        service_id = self.make_service(
            first_order,
            NOW - timedelta(days=25),
            NOW + timedelta(days=days_left),
            service_username="ali_vpn",
        )

        renewal_order = self.add_fulfilling_order(
            username="ali_vpn", renewal_for_service=service_id
        )

        with bot.db() as conn:
            conn.execute(
                "UPDATE active_services SET service_username=? WHERE id=?",
                ("ali_vpn", service_id),
            )

        return first_order, service_id, renewal_order

    def test_renewal_extends_expiry_on_the_panel(self):
        _first, service_id, order_id = self._renewal_setup(days_left=5)

        order = self.get_order(order_id)
        result = bot.provision_eylan_order_with_api(order)

        self.assertEqual(result["action"], "renewed")

        remote = self.panel.users["ali_vpn"]
        expected = (NOW + timedelta(days=5) + timedelta(days=30)).date()
        self.assertEqual(
            remote["expiry_date_str"], expected.isoformat()
        )
        self.assertEqual(result["expiry_date"], expected.isoformat())

    def test_renewal_resets_traffic_on_the_panel(self):
        _first, _service_id, order_id = self._renewal_setup(days_left=5)

        order = self.get_order(order_id)
        bot.provision_eylan_order_with_api(order)

        self.assertEqual(self.panel.reset_traffic_calls, ["ali_vpn"])
        self.assertEqual(self.panel.users["ali_vpn"]["total_traffic_bytes"], 0)

    def test_renewal_traffic_reset_can_be_disabled(self):
        _first, _service_id, order_id = self._renewal_setup(days_left=5)

        with mock.patch.object(bot, "EYLAN_RENEWAL_RESET_TRAFFIC", False):
            bot.provision_eylan_order_with_api(self.get_order(order_id))

        self.assertEqual(self.panel.reset_traffic_calls, [])

    def test_renewal_without_explicit_expiry_date_never_reaches_panel(self):
        """
        اگر payload تمدید تاریخ انقضای صریح نداشته باشد، ربات *قبل از* تماس
        با پنل متوقف می‌شود؛ چون می‌داند پنل expiry_days را نادیده می‌گیرد و
        تمدید بی‌اثر می‌ماند.
        """

        _first, _service_id, order_id = self._renewal_setup(days_left=5)

        original = bot.eylan_payload_for_product

        def _drop_expiry(*args, **kwargs):
            payload = original(*args, **kwargs)
            payload.pop("expiry_date_str", None)
            payload["expiry_days"] = 30
            return payload

        with mock.patch.object(bot, "eylan_payload_for_product", _drop_expiry):
            with self.assertRaises(EylanPanelError) as ctx:
                bot.provision_eylan_order_with_api(self.get_order(order_id))

        self.assertIn("expiry_date_str", str(ctx.exception))

        # هیچ PUT بی‌اثری به پنل ارسال نشد
        self.assertNotIn("update", [c[0] for c in self.panel.calls])

    def test_renewal_reports_failure_when_panel_ignores_the_change(self):
        """
        اگر پنل PUT را بپذیرد ولی عملاً تاریخ را عوض نکند، راستی‌آزمایی
        باید خطا بدهد (نه «✅ تمدید شد»).
        """

        _first, _service_id, order_id = self._renewal_setup(days_left=5)

        self.panel.ignore_expiry_update = True

        with self.assertRaises(EylanPanelVerificationError):
            bot.provision_eylan_order_with_api(self.get_order(order_id))

    def test_renewal_noop_shows_warning_to_admin_not_success(self):
        _first, _service_id, order_id = self._renewal_setup(days_left=5)

        self.panel.ignore_expiry_update = True

        bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], "fulfilling")
        self.assertEqual(order["api_status"], "failed_verification")

        admin_text = "\n".join(self.admin_texts())
        self.assertNotIn("تحویل داده شد", admin_text)
        self.assertIn("اعمال نشده", admin_text)

    def test_local_service_expiry_is_extended_after_delivery(self):
        _first, service_id, order_id = self._renewal_setup(days_left=5)

        bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)

        svc = bot.get_active_service_by_id(service_id)
        new_expiry = datetime.fromisoformat(svc["expires_at"])
        self.assertGreater(new_expiry, NOW + timedelta(days=29))
        self.assertEqual(int(svc["renewal_order_id"]), order_id)

        admin_text = "\n".join(self.admin_texts())
        self.assertIn("تمدید", admin_text)
        self.assertIn("راستی‌آزمایی", admin_text)


# ══════════════════════════════════════════════════════════════════════════════
# ۵) تحویل کامل سفارش + پیام ادمین
# ══════════════════════════════════════════════════════════════════════════════

class DeliveryFlowTests(EylanProvisioningBase):
    def test_successful_delivery_completes_order_and_notifies(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")

        handled = bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)
        self.assertTrue(handled)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], "completed")
        self.assertEqual(order["api_status"], "delivered")
        self.assertTrue(order["completed_at"])

        # سرویس محلی هم ساخته شد
        services = bot.get_active_services(USER_ID)
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["service_username"], "ali_vpn")

        admin_text = "\n".join(self.admin_texts())
        self.assertIn(f"سفارش #{order_id}", admin_text)
        self.assertIn("ali_vpn", admin_text)
        self.assertIn("راستی‌آزمایی", admin_text)

    def test_non_wireguard_product_is_not_auto_delivered(self):
        order_id = self.add_fulfilling_order(
            username="ai_user",
            product_id="chatgpt-shared-5",
            title="ChatGPT Plus — اکانت عادی ۱۵ نفره",
        )

        handled = bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)
        self.assertFalse(handled)
        self.assertEqual(self.panel.calls, [])

    def test_order_not_in_fulfilling_mode_is_rejected(self):
        order_id = self.add_order(status="paid_pending_fulfillment")

        bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)

        self.assertIn("دیگر در حالت تحویل نیست", self.last_admin_text())

    def test_api_error_keeps_order_open_with_retry_button(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")

        with mock.patch.object(
            self.panel,
            "create_user_ex",
            side_effect=EylanPanelError("HTTP 403 quota"),
        ):
            bot.deliver_eylan_order_via_api(ADMIN_ID, order_id)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], "fulfilling")
        self.assertEqual(order["api_status"], "failed")
        self.assertIn("quota", order["api_error"])

        markup = bot.bot.send_message.call_args.kwargs.get("reply_markup")
        self.assertIsNotNone(markup)

    def test_auto_delivery_button_starts_flow(self):
        order_id = self.add_fulfilling_order(username="ali_vpn")

        bot.cb_fulfill_auto(self.call(f"fulfill:auto:{order_id}"))

        self.assertIn("ali_vpn", self.panel.users)
        self.assertEqual(self.get_order(order_id)["status"], "completed")


# ══════════════════════════════════════════════════════════════════════════════
# ۶) عیب‌یابی اتصال از پنل ادمین
# ══════════════════════════════════════════════════════════════════════════════

class DiagnosticTests(EylanProvisioningBase):
    def test_diagnostic_lists_allowed_resources_and_payload(self):
        text = bot.eylan_diagnostic_text()

        self.assertIn("عیب‌یابی اتصال EylanPanel", text)
        self.assertIn("/api/v1/status", text)
        self.assertIn("Germany-3", text)
        self.assertIn("wg2", text)
        self.assertIn("wg_instances", text)
        self.assertIn("نمونه payload ساخت کاربر", text)
        self.assertIn("nodes=", text)

    def test_diagnostic_flags_missing_node(self):
        self.panel.nodes = [{"id": 3, "name": "Germany-3"}]

        text = bot.eylan_diagnostic_text()

        self.assertIn("پیدا نشد", text)
        self.assertIn("Turkey-1", text)

    def test_diagnostic_without_api_key(self):
        with mock.patch.object(bot, "EYLAN_API_KEY", ""):
            text = bot.eylan_diagnostic_text()

        self.assertIn("EYLAN_API_KEY تنظیم نشده", text)

    def test_diagnostic_callback_requires_admin(self):
        bot.cb_admin_eylan_diag(self.call("admin:eylan:diag", user=USER_ID))
        bot.bot.edit_message_text.assert_not_called()

        bot.cb_admin_eylan_diag(self.call("admin:eylan:diag"))
        self.assertIn("عیب‌یابی", bot.bot.edit_message_text.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
