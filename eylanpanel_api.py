from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests


# ══════════════════════════════════════════════════════════════════════════════
# خطاها
# ══════════════════════════════════════════════════════════════════════════════

class EylanPanelError(RuntimeError):
    """Base error for EylanPanel API integration."""


class EylanPanelConfigurationError(EylanPanelError):
    """Raised when API configuration is missing or invalid."""


class EylanPanelHTTPError(EylanPanelError):
    """Raised when the API returns an HTTP or application-level error."""


class EylanPanelAmbiguousRequestError(EylanPanelHTTPError):
    """The request may have reached the server, but its result is unknown."""


class EylanPanelNotFound(EylanPanelHTTPError):
    """Raised when an API resource does not exist on every candidate base URL."""


class EylanPanelVerificationError(EylanPanelError):
    """
    The API answered 2xx, but a follow-up read proved the change did NOT land.

    این همان حالتی است که قبلاً به‌صورت «موفقیت‌آمیز» گزارش می‌شد در حالی که
    در پنل هیچ کاربری ساخته/تمدید نشده بود. از این به بعد چنین پاسخی خطا است.
    """


@dataclass(frozen=True)
class EylanPanelResponse:
    status_code: int
    url: str
    data: Any


@dataclass
class Attempt:
    """یک تلاش واقعی شبکه؛ برای گزارش عیب‌یابی به ادمین نگه داشته می‌شود."""

    method: str
    url: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    note: str = ""

    def render(self) -> str:
        if self.status_code is not None:
            outcome = f"HTTP {self.status_code}"
        elif self.error:
            outcome = f"خطا: {self.error}"
        else:
            outcome = "بدون پاسخ"
        line = f"{self.method} {self.url} → {outcome}"
        if self.note:
            line += f" ({self.note})"
        return line


@dataclass
class RequestTrace:
    """جمع تلاش‌های آخرین درخواست؛ در متن خطا و داشبورد ادمین استفاده می‌شود."""

    path: str
    attempts: List[Attempt] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join(a.render() for a in self.attempts)


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class EylanPanelClient:
    """
    Defensive client for the documented EylanPanel REST API (v4.0.1.4).

    Important safety rules
    ----------------------
    1. GET/HEAD may try the next configured base URL when the current base is
       unavailable or answers 404 (the panel path may be rooted below
       ``/r/<reseller>``).
    2. POST/PUT/PATCH/DELETE are NEVER replayed against another base URL and
       never retried after a transport timeout, because the panel may already
       have applied the change. ``create_user`` reconciles by username first.
    3. Every mutation can be *verified* by reading the user back. A 2xx answer
       that did not actually change the panel raises
       :class:`EylanPanelVerificationError` instead of being reported as
       success — this was the root cause of «✅ ساخته شد» without any user
       appearing in the panel.
    """

    #: آخرین رِخداد شبکه؛ توسط داشبورد عیب‌یابی ادمین خوانده می‌شود.
    last_trace: Optional[RequestTrace] = None

    def __init__(
        self,
        api_key: str,
        domains: Iterable[str],
        timeout: float = 20.0,
        allow_http: bool = False,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout or 20.0)
        self.allow_http = bool(allow_http)
        self.raw_domains = [
            str(d or "").strip() for d in domains if str(d or "").strip()
        ]
        self.base_urls = self._normalize_domains(domains, allow_http=allow_http)
        self.last_trace = None

        if not self.api_key:
            raise EylanPanelConfigurationError(
                "EYLAN_API_KEY is missing. Set it in the server environment variables, "
                "then restart/redeploy the bot so the new value is loaded."
            )

        if not self.base_urls:
            raise EylanPanelConfigurationError(
                "No valid EylanPanel API domain is configured."
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-KEY": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Hamid-Telegram-Shop/EylanPanel-API/3.0",
            }
        )

    # ── پیکربندی ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_domains(
        domains: Iterable[str],
        *,
        allow_http: bool,
    ) -> List[str]:
        seen = set()
        normalized: List[str] = []

        for raw in domains:
            raw = (raw or "").strip()
            if not raw:
                continue

            if not raw.startswith(("http://", "https://")):
                raw = "https://" + raw

            raw = raw.rstrip("/")
            candidates = [raw]

            if allow_http:
                if raw.startswith("https://"):
                    candidates.append("http://" + raw[len("https://"):])
                elif raw.startswith("http://"):
                    candidates.append("https://" + raw[len("http://"):])

            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    normalized.append(candidate)

        return normalized

    def describe_configuration(self) -> Dict[str, Any]:
        """خلاصه‌ی پیکربندی (بدون لو دادن کلید) برای داشبورد ادمین."""

        return {
            "api_key_set": bool(self.api_key),
            "api_key_length": len(self.api_key),
            "configured_domains": list(self.raw_domains),
            "base_urls_tried": list(self.base_urls),
            "allow_http": self.allow_http,
            "timeout": self.timeout,
        }

    def _url(self, base: str, path: str) -> str:
        return f"{base.rstrip('/')}/{path.lstrip('/')}"

    # ── هسته‌ی درخواست ───────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        allow_not_found: bool = False,
        retry_transport: Optional[bool] = None,
        allow_base_fallback: Optional[bool] = None,
    ) -> EylanPanelResponse:
        method = method.upper()

        if retry_transport is None:
            retry_transport = method not in _MUTATING_METHODS

        # یک درخواست تغییردهنده (POST/PUT/DELETE) هرگز روی دامنه‌ی دوم
        # تکرار نمی‌شود؛ وگرنه ممکن است کاربر در دامنه‌ی/اسکوپ دیگری ساخته شود
        # و ادمین در پنل خودش هیچ چیزی نبیند.
        if allow_base_fallback is None:
            allow_base_fallback = method not in _MUTATING_METHODS

        trace = RequestTrace(path=f"{method} {path}")
        self.last_trace = trace

        last_error: Optional[Exception] = None
        last_auth_error: Optional[Exception] = None
        saw_not_found = False
        not_found_urls: List[str] = []

        for base in self.base_urls:
            url = self._url(base, path)

            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    json=json_body,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                trace.attempts.append(
                    Attempt(method, url, error=str(exc)[:200])
                )

                if not retry_transport:
                    raise EylanPanelAmbiguousRequestError(
                        f"EylanPanel transport error for {method} {path}: {exc}\n"
                        f"{trace.render()}"
                    ) from exc
                continue

            trace.attempts.append(Attempt(method, url, response.status_code))

            if response.status_code == 404:
                saw_not_found = True
                not_found_urls.append(url)

                if not allow_base_fallback:
                    raise EylanPanelNotFound(
                        "EylanPanel مسیر درخواست را روی دامنه‌ی پیکربندی‌شده پیدا نکرد "
                        f"(HTTP 404): {url}\n"
                        "چون این یک درخواست تغییردهنده است، روی دامنه‌ی دیگر تکرار نمی‌شود "
                        "تا اکانت در اسکوپ اشتباه ساخته نشود.\n"
                        "EYLAN_API_DOMAIN را بررسی کنید؛ اگر پنل زیر مسیر نمایندگی است "
                        "باید به شکل host:port/r/<name> باشد."
                    )

                # A 404 can mean the base path is wrong. Continue to the next
                # candidate, especially for paths rooted below /r/<name>.
                continue

            if response.status_code in (401, 403):
                last_auth_error = EylanPanelHTTPError(
                    f"EylanPanel authentication/permission failed with HTTP "
                    f"{response.status_code} for {url}"
                )
                if not allow_base_fallback:
                    raise last_auth_error
                # Trying a second scheme/base with the same API key is not
                # harmful and may be needed when one candidate is stale.
                continue

            if response.status_code in (405, 502, 503, 504):
                last_error = EylanPanelHTTPError(
                    f"HTTP {response.status_code} from {url}"
                )

                # Never replay a non-idempotent request (notably POST /users)
                # across another base URL. Gateway errors 502/503/504 are
                # ambiguous because the upstream may already have processed it.
                if not retry_transport:
                    if response.status_code in (502, 503, 504):
                        raise EylanPanelAmbiguousRequestError(
                            f"HTTP {response.status_code} from {url}"
                        )
                    raise last_error

                if not allow_base_fallback:
                    raise last_error

                continue

            try:
                data = response.json()
            except ValueError:
                data = response.text

            if response.status_code >= 400:
                raise EylanPanelHTTPError(
                    f"EylanPanel API returned HTTP {response.status_code} for {path}"
                    + (f": {self._error_detail(data)}" if self._error_detail(data) else "")
                )

            if isinstance(data, dict) and data.get("success") is False:
                raise EylanPanelHTTPError(
                    str(
                        data.get("message")
                        or data.get("error")
                        or data.get("detail")
                        or "API request failed"
                    )
                )

            return EylanPanelResponse(
                status_code=response.status_code,
                url=url,
                data=data,
            )

        # If one base URL returned 404 but another base URL was unavailable or
        # rejected the request, do not misclassify the resource as missing.
        if last_auth_error:
            raise last_auth_error

        if last_error:
            raise EylanPanelHTTPError(
                f"Could not reach EylanPanel using the configured API domains: {last_error}\n"
                f"{trace.render()}"
            ) from last_error

        if allow_not_found and saw_not_found:
            raise EylanPanelNotFound(
                f"EylanPanel resource was not found: {path}"
            )

        if saw_not_found:
            raise EylanPanelHTTPError(
                "EylanPanel returned HTTP 404 for every configured API domain: "
                f"{path}\n" + "\n".join(not_found_urls)
            )

        raise EylanPanelHTTPError(
            f"Could not reach EylanPanel using the configured API domains: {path}"
        )

    @staticmethod
    def _error_detail(data: Any) -> str:
        if isinstance(data, dict):
            detail = (
                data.get("message")
                or data.get("error")
                or data.get("detail")
                or ""
            )
            if detail:
                return str(detail)

        return str(data).strip()[:500]

    @staticmethod
    def _username_path(username: str) -> str:
        username = (username or "").strip()
        if not username:
            raise EylanPanelHTTPError("Username is empty.")
        return quote(username, safe="")

    @staticmethod
    def _as_dict(data: Any, what: str) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise EylanPanelHTTPError(f"Unexpected {what} response from EylanPanel API.")
        return data

    # ── وضعیت و منابع مجاز ───────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        response = self._request("GET", "/api/v1/status")
        return self._as_dict(response.data, "status")

    def get_users_stats(self) -> Dict[str, Any]:
        """
        GET /api/v1/users/stats

        سبک است و علاوه بر آمار، `allowed_resources` را برمی‌گرداند:
        نودها، اینستنس‌های وایرگارد و اینباندهای سینگ‌باکسی که *همین کلید API*
        اجازه‌ی استفاده از آن‌ها را دارد. برای عیب‌یابی «چرا کاربر ساخته نمی‌شود»
        این دقیقاً همان چیزی است که لازم داریم.
        """

        response = self._request("GET", "/api/v1/users/stats")
        return self._as_dict(response.data, "users-stats")

    def allowed_resources(self) -> Dict[str, Any]:
        stats = self.get_users_stats()
        resources = stats.get("allowed_resources")
        return resources if isinstance(resources, dict) else {}

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Return nodes visible/allowed to this API key."""

        response = self._request("GET", "/api/v1/nodes")
        data = response.data

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        data = self._as_dict(data, "nodes")

        for key in ("nodes", "data", "results"):
            nodes = data.get(key)
            if isinstance(nodes, list):
                return [item for item in nodes if isinstance(item, dict)]

        raise EylanPanelHTTPError("EylanPanel did not return a valid 'nodes' list.")

    def get_singbox_inbounds(self) -> List[Dict[str, Any]]:
        return self._list_endpoint(
            "/api/v1/public/singbox/my_inbounds",
            ("inbounds", "data", "results"),
            "Sing-box inbound",
        )

    def get_my_wireguard_instances(self) -> List[Dict[str, Any]]:
        """
        GET /api/v1/public/wireguard/my_instances

        وایرگاردهای مجاز برای این کلید (wg1 به بعد). نام هر مورد (مثلاً "wg2")
        همان مقداری است که باید داخل `wg_instances` ارسال شود.
        """

        return self._list_endpoint(
            "/api/v1/public/wireguard/my_instances",
            ("instances", "wg_instances", "data", "results"),
            "WireGuard instance",
        )

    def _list_endpoint(
        self,
        path: str,
        keys: Iterable[str],
        label: str,
    ) -> List[Dict[str, Any]]:
        response = self._request("GET", path)
        data = response.data

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        data = self._as_dict(data, label)

        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        raise EylanPanelHTTPError(
            f"EylanPanel did not return a valid {label} list for {path}."
        )

    # ── کاربران ──────────────────────────────────────────────────────────────

    def list_users(self) -> List[Dict[str, Any]]:
        """Return the documented full user list with usage/expiry information."""

        response = self._request("GET", "/api/v1/users/list_all")
        data = self._as_dict(response.data, "users-list")
        users = data.get("users")
        if not isinstance(users, list):
            raise EylanPanelHTTPError("EylanPanel did not return a valid 'users' list.")
        return [item for item in users if isinstance(item, dict)]

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        encoded = self._username_path(username)
        try:
            response = self._request(
                "GET",
                f"/api/v1/users/{encoded}",
                allow_not_found=True,
            )
        except EylanPanelNotFound:
            return None

        return self._as_dict(response.data, "user")

    def require_user(self, username: str) -> Dict[str, Any]:
        """همان get_user ولی اگر کاربر نبود خطای روشن می‌دهد."""

        user = self.get_user(username)
        if user is None:
            raise EylanPanelNotFound(
                f"EylanPanel user '{username}' was not found."
            )
        return user

    def get_user_status(self, username: str) -> Dict[str, Any]:
        """
        Fetch live user status, enriching the single-user response with
        list_all when limit/expiry fields are not present there.
        """

        username = (username or "").strip()
        if not username:
            raise EylanPanelHTTPError("Username is empty.")

        merged: Dict[str, Any] = dict(self.require_user(username))

        limit_keys = ("data_limit", "traffic_limit", "quota")
        expiry_keys = (
            "expiry_date",
            "expiry_date_str",
            "expiry_date_actual_iso",
            "expiry_date_display",
            "expires_at",
            "expiry",
            "expiry_datetime",
            "remaining_days",
        )
        activation_keys = ("activation_type",)

        if (
            not any(key in merged for key in limit_keys)
            or not any(key in merged for key in expiry_keys)
            or not any(key in merged for key in activation_keys)
        ):
            for item in self.list_users():
                if str(item.get("username") or "").strip() == username:
                    for key, value in item.items():
                        merged.setdefault(key, value)
                    break

        return merged

    def create_user(self, payload: Dict[str, Any]) -> str:
        """ساخت کاربر و برگرداندن نام کاربری نهایی (سازگار با کد قبلی)."""

        return self.create_user_ex(payload)["username"]

    def create_user_ex(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /api/v1/users

        پاسخ مستند پنل:
            {"success": true, "message": "1 user(s) created.",
             "created_users": ["new_user"]}

        نکته‌ی مهم مستندات: اگر با کلید API یک *نماینده* نام کاربری‌ای بسازید
        که در سطح کل پنل قبلاً گرفته شده باشد، پنل خودش یک عدد تصادفی به انتهای
        آن اضافه می‌کند و نام نهایی را در `created_users` و `message` اعلام
        می‌کند. پس نام بازگشتی همیشه از `created_users` خوانده می‌شود و اگر با
        نام درخواستی فرق داشت، فراخوان باید آن را به ادمین گزارش کند.
        """

        requested = str(payload.get("username") or "").strip()
        if not requested and not payload.get("bulk_count"):
            raise EylanPanelHTTPError(
                "Cannot create an EylanPanel user without username."
            )

        try:
            response = self._request(
                "POST",
                "/api/v1/users",
                json_body=payload,
                retry_transport=False,
                allow_base_fallback=False,
            )
        except EylanPanelAmbiguousRequestError as exc:
            # The POST may have succeeded remotely but its response may have
            # been lost. Reconcile by username before reporting failure.
            try:
                existing = self.get_user(requested) if requested else None
            except EylanPanelError:
                existing = None
            if existing is not None:
                return {
                    "username": requested,
                    "requested_username": requested,
                    "renamed": False,
                    "message": "reconciled after ambiguous transport error",
                    "created_users": [requested],
                    "raw": existing,
                    "reconciled": True,
                }
            raise exc

        data = self._as_dict(response.data, "create-user")

        created = data.get("created_users")
        final = ""
        if isinstance(created, list):
            for item in created:
                candidate = str(item or "").strip()
                if candidate:
                    final = candidate
                    break

        if not final:
            # پنل گفته success ولی هیچ نام کاربری‌ای برنگردانده؛ برای ساخت تکی
            # این یعنی عملاً کاربری ساخته نشده است.
            if not payload.get("bulk_count"):
                raise EylanPanelVerificationError(
                    "EylanPanel پاسخ موفق داد ولی هیچ کاربری در created_users اعلام نکرد؛ "
                    "یعنی اکانتی ساخته نشده است. "
                    f"پاسخ پنل: {str(data)[:300]}"
                )
            final = requested

        final = final.strip() or requested

        return {
            "username": final,
            "requested_username": requested,
            "renamed": bool(requested) and final != requested,
            "message": str(data.get("message") or ""),
            "created_users": [
                str(x).strip() for x in created if str(x or "").strip()
            ] if isinstance(created, list) else [],
            "raw": data,
            "reconciled": False,
        }

    def update_user(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "PUT",
            f"/api/v1/users/{encoded}",
            json_body=payload,
            retry_transport=False,
            allow_base_fallback=False,
        )
        return self._as_dict(response.data, "update-user")

    def reset_traffic(self, username: str) -> Dict[str, Any]:
        """
        POST /api/v1/users/<username>/reset_traffic

        حجم مصرفی را صفر و قفل حجم را باز می‌کند — همان کاری که بعد از تمدید
        لازم است تا مشتری واقعاً با حجم جدید بتواند وصل شود.
        """

        encoded = self._username_path(username)
        response = self._request(
            "POST",
            f"/api/v1/users/{encoded}/reset_traffic",
            retry_transport=False,
            allow_base_fallback=False,
        )
        return self._as_dict(response.data, "reset-traffic")

    def toggle_user(self, username: str) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "POST",
            f"/api/v1/users/{encoded}/toggle",
            retry_transport=False,
            allow_base_fallback=False,
        )
        return self._as_dict(response.data, "toggle-user")

    def delete_user(self, username: str) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "DELETE",
            f"/api/v1/users/{encoded}",
            retry_transport=False,
            allow_base_fallback=False,
        )
        return self._as_dict(response.data, "delete-user")

    def get_subscription_link(self, username: str) -> str:
        encoded = self._username_path(username)
        response = self._request(
            "GET",
            f"/api/v1/users/{encoded}/sub",
        )
        data = self._as_dict(response.data, "subscription-link")

        link = data.get("sub_url") or data.get("subscription_url")
        if not link or not isinstance(link, str):
            raise EylanPanelHTTPError(
                f"EylanPanel did not return a subscription URL for '{username}'."
            )
        return link.strip()

    def get_all_configs(self, username: str) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "GET",
            f"/api/v1/users/{encoded}/all_ovpn_links",
        )
        return self._as_dict(response.data, "config")

    def get_wg1_files(self, username: str) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "GET",
            f"/api/v1/users/{encoded}/wg1_files",
        )
        return self._as_dict(response.data, "wg1")

    def revoke_subscription(self, username: str) -> str:
        encoded = self._username_path(username)
        response = self._request(
            "POST",
            f"/api/v1/users/{encoded}/revoke",
            retry_transport=False,
            allow_base_fallback=False,
        )
        data = self._as_dict(response.data, "revoke")

        link = data.get("new_subscription_url") or data.get("sub_url")
        if not link:
            raise EylanPanelHTTPError(
                "EylanPanel did not return the new subscription URL."
            )
        return str(link).strip()

    # ── راستی‌آزمایی ─────────────────────────────────────────────────────────

    def verify_user_exists(
        self,
        username: str,
        *,
        expected_data_limit: Optional[float] = None,
        tolerance_gb: float = 0.01,
    ) -> Dict[str, Any]:
        """
        بعد از POST /users حتماً کاربر را دوباره می‌خوانیم.

        اگر پنل به هر دلیلی کاربر را نساخته باشد (پاسخ success بدون ساخت،
        اسکوپ اشتباه، سهمیه‌ی نماینده و …) اینجا خطای روشن می‌دهیم تا ربات
        هرگز «✅ ساخته شد» الکی نگوید.
        """

        try:
            record = self.get_user_status(username)
        except EylanPanelNotFound as exc:
            # «موفق» گفت ولی کاربری وجود ندارد ⇒ همان باگی که قبلاً به ادمین
            # گزارش موفقیت می‌داد.
            raise EylanPanelVerificationError(
                f"EylanPanel پاسخ موفق داد ولی کاربر '{username}' در پنل وجود "
                "ندارد؛ یعنی اکانتی ساخته نشده است. "
                "احتمالاً کلید API به اسکوپ/دامنه‌ی دیگری وصل است یا سهمیه‌ی "
                "نماینده پر شده است. (🛠 مدیریت EylanPanel → 🩺 عیب‌یابی)"
            ) from exc

        if expected_data_limit is not None and expected_data_limit > 0:
            remote_limit = record.get("data_limit")
            if remote_limit is not None:
                try:
                    remote_value = float(remote_limit)
                except (TypeError, ValueError):
                    remote_value = None

                unit = str(record.get("data_limit_unit") or "GB").upper()
                if remote_value is not None and unit == "MB":
                    remote_value = remote_value / 1024.0

                if (
                    remote_value is not None
                    and abs(remote_value - float(expected_data_limit)) > tolerance_gb
                ):
                    raise EylanPanelVerificationError(
                        f"کاربر '{username}' در پنل ساخته شد ولی حجم آن "
                        f"{remote_value:g} گیگابایت است، در حالی که "
                        f"{float(expected_data_limit):g} گیگابایت درخواست شده بود."
                    )

        return record

    def verify_expiry_extended(
        self,
        username: str,
        *,
        expected_date: Any,
    ) -> Dict[str, Any]:
        """
        بعد از PUT (تمدید) تاریخ انقضای واقعی پنل را می‌خوانیم.

        اگر پنل فیلد را نادیده گرفته باشد (مثلاً کاربر already-connected است و
        فقط expiry_date_str پذیرفته می‌شود) اینجا لو می‌رود.
        """

        from datetime import date, datetime, timedelta, timezone

        try:
            record = self.get_user_status(username)
        except EylanPanelNotFound as exc:
            raise EylanPanelVerificationError(
                f"EylanPanel پاسخ موفق داد ولی کاربر '{username}' در پنل وجود "
                "ندارد؛ یعنی تمدید روی هیچ اکانتی اعمال نشده است."
            ) from exc

        target: Optional[date] = None
        if isinstance(expected_date, datetime):
            target = expected_date.date()
        elif isinstance(expected_date, date):
            target = expected_date
        elif expected_date:
            try:
                target = datetime.fromisoformat(
                    str(expected_date).replace("Z", "+00:00")
                ).date()
            except ValueError:
                target = None

        if target is None:
            return record

        raw_expiry = (
            record.get("expiry_date_actual_iso")
            or record.get("expiry_date")
            or record.get("expiry_date_str")
            or record.get("expires_at")
            or record.get("expiry_datetime")
        )

        if not raw_expiry:
            remaining = record.get("remaining_days")
            if remaining is None:
                raise EylanPanelVerificationError(
                    f"تمدید کاربر '{username}' ثبت شد ولی پنل هیچ تاریخ انقضایی "
                    "برنگرداند؛ نمی‌توان تأیید کرد که تمدید واقعاً اعمال شده است."
                )
            return record

        try:
            parsed = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
        except ValueError:
            # قالب‌هایی مثل "2026-12-31" یا "Unlimited"
            text = str(raw_expiry).strip()
            if text.lower() in {"unlimited", "نامحدود"}:
                return record
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return record

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        remote_date = parsed.astimezone(timezone.utc).date()

        # یک روز تلورانس برای اختلاف منطقه‌ی زمانی پنل.
        if remote_date < target - timedelta(days=1):
            raise EylanPanelVerificationError(
                f"تمدید کاربر '{username}' در پنل اعمال نشد: تاریخ انقضای فعلی پنل "
                f"{remote_date.isoformat()} است ولی انتظار {target.isoformat()} داشتیم. "
                "معمولاً یعنی پنل فیلد ارسالی را نادیده گرفته است."
            )

        return record
