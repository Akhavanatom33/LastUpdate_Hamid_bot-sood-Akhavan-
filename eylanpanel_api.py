from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests


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


@dataclass(frozen=True)
class EylanPanelResponse:
    status_code: int
    url: str
    data: Any


class EylanPanelClient:
    """
    Defensive client for the documented EylanPanel REST API.

    Important safety rule:
    - GET/PUT requests may try the next configured base URL when the current
      base is unavailable.
    - POST requests are never blindly replayed after a transport timeout,
      because the panel may have already created the user. create_user()
      performs a GET-by-username reconciliation first when possible.
    """

    def __init__(
        self,
        api_key: str,
        domains: Iterable[str],
        timeout: float = 20.0,
        allow_http: bool = False,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout or 20.0)
        self.base_urls = self._normalize_domains(domains, allow_http=allow_http)

        if not self.api_key:
            raise EylanPanelConfigurationError(
                "EYLAN_API_KEY is missing. Set it in the server environment variables."
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
                "User-Agent": "Hamid-Telegram-Shop/EylanPanel-API/2.0",
            }
        )

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

    def _url(self, base: str, path: str) -> str:
        return f"{base.rstrip('/')}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        allow_not_found: bool = False,
        retry_transport: Optional[bool] = None,
    ) -> EylanPanelResponse:
        method = method.upper()
        if retry_transport is None:
            retry_transport = method in {"GET", "HEAD", "PUT", "DELETE"}

        last_error: Optional[Exception] = None
        last_auth_error: Optional[Exception] = None
        saw_not_found = False

        for index, base in enumerate(self.base_urls):
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
                if not retry_transport:
                    raise EylanPanelAmbiguousRequestError(
                        f"EylanPanel transport error for {method} {path}: {exc}"
                    ) from exc
                continue

            if response.status_code == 404:
                saw_not_found = True
                # A 404 can mean the base path is wrong. Continue to the next
                # candidate, especially for paths rooted below /r/<name>.
                continue

            if response.status_code in (401, 403):
                last_auth_error = EylanPanelHTTPError(
                    f"EylanPanel authentication/permission failed with HTTP "
                    f"{response.status_code} for {url}"
                )
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
                continue

            try:
                data = response.json()
            except ValueError:
                data = response.text

            if response.status_code >= 400:
                detail = ""
                if isinstance(data, dict):
                    detail = (
                        data.get("message")
                        or data.get("error")
                        or data.get("detail")
                        or ""
                    )
                if not detail:
                    detail = str(data).strip()[:500]

                raise EylanPanelHTTPError(
                    f"EylanPanel API returned HTTP {response.status_code} for {path}"
                    + (f": {detail}" if detail else "")
                )

            if isinstance(data, dict) and data.get("success") is False:
                detail = (
                    data.get("message")
                    or data.get("error")
                    or data.get("detail")
                    or "API request failed"
                )
                raise EylanPanelHTTPError(str(detail))

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
                f"Could not reach EylanPanel using the configured API domains: {last_error}"
            ) from last_error

        if allow_not_found and saw_not_found:
            raise EylanPanelNotFound(
                f"EylanPanel resource was not found: {path}"
            )

        if saw_not_found:
            raise EylanPanelHTTPError(
                f"EylanPanel returned HTTP 404 for every configured API domain: {path}"
            )

        raise EylanPanelHTTPError(
            f"Could not reach EylanPanel using the configured API domains: {path}"
        )

    @staticmethod
    def _username_path(username: str) -> str:
        username = (username or "").strip()
        if not username:
            raise EylanPanelHTTPError("Username is empty.")
        return quote(username, safe="")

    def status(self) -> Dict[str, Any]:
        response = self._request("GET", "/api/v1/status")
        return response.data if isinstance(response.data, dict) else {"raw": response.data}

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

        if not isinstance(response.data, dict):
            raise EylanPanelHTTPError("Unexpected user response from EylanPanel API.")
        return response.data

    def list_users(self) -> List[Dict[str, Any]]:
        """Return the documented full user list with usage/expiry information."""
        response = self._request("GET", "/api/v1/users/list_all")
        data = response.data
        if not isinstance(data, dict):
            raise EylanPanelHTTPError("Unexpected users-list response from EylanPanel API.")
        users = data.get("users")
        if not isinstance(users, list):
            raise EylanPanelHTTPError("EylanPanel did not return a valid 'users' list.")
        return [item for item in users if isinstance(item, dict)]

    def get_user_status(self, username: str) -> Dict[str, Any]:
        """
        Fetch live user status, enriching the single-user response with
        list_all when limit/expiry fields are not present there.
        """
        username = (username or "").strip()
        if not username:
            raise EylanPanelHTTPError("Username is empty.")

        detail = self.get_user(username)
        if detail is None:
            raise EylanPanelNotFound(f"EylanPanel user '{username}' was not found.")

        merged: Dict[str, Any] = dict(detail)

        limit_keys = ("data_limit", "traffic_limit", "quota")
        expiry_keys = ("expiry_date", "expiry_date_str", "expires_at", "expiry", "expiry_datetime")
        activation_keys = ("activation_type",)
        if (
            not any(key in merged for key in limit_keys)
            or not any(key in merged for key in expiry_keys)
            or not any(key in merged for key in activation_keys)
        ):
            for item in self.list_users():
                if str(item.get("username") or "").strip() == username:
                    merged.update(item)
                    break

        return merged

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Return nodes visible/allowed to this API key."""
        response = self._request("GET", "/api/v1/nodes")
        data = response.data
        if not isinstance(data, dict):
            raise EylanPanelHTTPError("Unexpected nodes response from EylanPanel API.")
        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            raise EylanPanelHTTPError("EylanPanel did not return a valid 'nodes' list.")
        return [item for item in nodes if isinstance(item, dict)]

    def create_user(self, payload: Dict[str, Any]) -> str:
        username = str(payload.get("username") or "").strip()
        if not username:
            raise EylanPanelHTTPError("Cannot create an EylanPanel user without username.")

        try:
            response = self._request(
                "POST",
                "/api/v1/users",
                json_body=payload,
                retry_transport=False,
            )
        except EylanPanelAmbiguousRequestError as exc:
            # The POST may have succeeded remotely but its response may have
            # been lost. Reconcile by username before reporting failure.
            try:
                existing = self.get_user(username)
            except EylanPanelError:
                existing = None
            if existing is not None:
                return username
            raise exc

        data = response.data
        if not isinstance(data, dict):
            raise EylanPanelHTTPError("Unexpected create-user response from EylanPanel API.")

        created = data.get("created_users")
        if isinstance(created, list) and created and str(created[0]).strip():
            return str(created[0]).strip()

        return username

    def update_user(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "PUT",
            f"/api/v1/users/{encoded}",
            json_body=payload,
        )
        if not isinstance(response.data, dict):
            raise EylanPanelHTTPError("Unexpected update-user response from EylanPanel API.")
        return response.data

    def get_subscription_link(self, username: str) -> str:
        encoded = self._username_path(username)
        response = self._request(
            "GET",
            f"/api/v1/users/{encoded}/sub",
        )
        data = response.data

        if not isinstance(data, dict):
            raise EylanPanelHTTPError(
                "Unexpected subscription-link response from EylanPanel API."
            )

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
        if not isinstance(response.data, dict):
            raise EylanPanelHTTPError("Unexpected config response from EylanPanel API.")
        return response.data

    def get_wg1_files(self, username: str) -> Dict[str, Any]:
        encoded = self._username_path(username)
        response = self._request(
            "GET",
            f"/api/v1/users/{encoded}/wg1_files",
        )
        if not isinstance(response.data, dict):
            raise EylanPanelHTTPError("Unexpected wg1 response from EylanPanel API.")
        return response.data

    def get_singbox_inbounds(self) -> List[Dict[str, Any]]:
        response = self._request(
            "GET",
            "/api/v1/public/singbox/my_inbounds",
        )
        data = response.data
        if not isinstance(data, dict):
            raise EylanPanelHTTPError(
                "Unexpected Sing-box inbound response from EylanPanel API."
            )
        inbounds = data.get("inbounds")
        if not isinstance(inbounds, list):
            raise EylanPanelHTTPError(
                "EylanPanel did not return a valid 'inbounds' list."
            )
        return [item for item in inbounds if isinstance(item, dict)]

    def revoke_subscription(self, username: str) -> str:
        encoded = self._username_path(username)
        response = self._request(
            "POST",
            f"/api/v1/users/{encoded}/revoke",
            retry_transport=False,
        )
        data = response.data
        if not isinstance(data, dict):
            raise EylanPanelHTTPError("Unexpected revoke response from EylanPanel API.")
        link = data.get("new_subscription_url") or data.get("sub_url")
        if not link:
            raise EylanPanelHTTPError("EylanPanel did not return the new subscription URL.")
        return str(link).strip()
