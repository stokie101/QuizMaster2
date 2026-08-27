"""Hosted control-dock tokens for the LiveForge widget host.

The OBS quiz control dock is served by the LiveForge widget Worker at
https://widgets.liveforge.online/u/<public_widget_id>/... . That page runs in
OBS, which carries none of the desktop app's cookies or headers, so it proves
ownership with a signed control token instead.

The token is minted once per account by POSTing the signed-in Supabase access
token to the Worker, and it is deterministic and permanent: the same account
always gets the same token bytes back, so the dock URL is FIXED and can be
copied, pasted and saved anywhere without ever going stale mid-stream. Access is
withdrawn by the Worker's per-request entitlement check (a lapsed subscription
stops the dock), not by the token expiring.

The token travels in the URL fragment (#control_token=...), which browsers never
send to a server, so it stays out of request logs, referrers and proxies.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from core.services.auth_service import AuthService

logger = logging.getLogger(__name__)

WIDGET_TYPES = {"quiz", "chess"}

# The Worker's control paths, keyed by widget type.
CONTROL_PATHS = {
    "quiz": "/quiz_controls",
    "chess": "/chess/controls",
}

# Re-mint no more often than this after a failure, so a signed-out app does not
# hammer the widget host on every page render.
RETRY_INTERVAL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 10.0


class HostedControlTokens:
    """Mint and cache the permanent control token for each hosted dock."""

    _instance: Optional["HostedControlTokens"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "HostedControlTokens":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: Dict[str, str] = {}
        self._owner: Dict[str, str] = {}
        self._last_failure_at: Dict[str, float] = {}
        self._last_error: Dict[str, str] = {}

    # -- public API ---------------------------------------------------------

    def cached_token(self, widget_type: str, public_widget_id: str) -> Optional[str]:
        """Return a token already held for this account, without any network."""
        with self._lock:
            if self._owner.get(widget_type) != public_widget_id:
                return None
            return self._tokens.get(widget_type)

    def token(self, widget_type: str, refresh: bool = False) -> Optional[str]:
        """Return the account's control token, minting it if needed."""
        if widget_type not in WIDGET_TYPES:
            return None

        public_widget_id, access_token = self._owner_credentials()
        if not public_widget_id or not access_token:
            self._record_failure(widget_type, "not_signed_in")
            return None

        if not refresh:
            cached = self.cached_token(widget_type, public_widget_id)
            if cached:
                return cached
            if self._in_backoff(widget_type):
                return None

        return self._mint(widget_type, public_widget_id, access_token)

    def dock_url(self, widget_type: str, base_url: str, public_widget_id: str, token: Optional[str]) -> str:
        """Build the hosted dock URL, carrying the token in the fragment."""
        path = CONTROL_PATHS.get(widget_type)
        if not path or not public_widget_id:
            return ""
        url = f"{base_url.rstrip('/')}/u/{quote(public_widget_id, safe='')}{path}"
        if token:
            url = f"{url}#control_token={quote(token, safe='')}"
        return url

    def status(self, widget_type: str) -> Dict[str, Any]:
        with self._lock:
            return {
                "widget_type": widget_type,
                "has_token": bool(self._tokens.get(widget_type)),
                "public_widget_id": self._owner.get(widget_type),
                "error": self._last_error.get(widget_type),
            }

    def clear(self) -> None:
        """Drop every cached token, e.g. on sign-out or account switch."""
        with self._lock:
            self._tokens.clear()
            self._owner.clear()
            self._last_failure_at.clear()
            self._last_error.clear()

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _owner_credentials() -> tuple[Optional[str], Optional[str]]:
        auth = AuthService.get_instance()
        session = getattr(auth, "current_session", None)
        profile = getattr(auth, "current_profile", None)
        access_token = getattr(session, "access_token", None) if session else None
        public_widget_id = (getattr(profile, "public_widget_id", None) or "").strip() or None
        if not access_token or not str(access_token).strip():
            return public_widget_id, None
        return public_widget_id, str(access_token)

    def _in_backoff(self, widget_type: str) -> bool:
        with self._lock:
            failed_at = self._last_failure_at.get(widget_type, 0.0)
        return (time.monotonic() - failed_at) < RETRY_INTERVAL_SECONDS

    def _record_failure(self, widget_type: str, error: str) -> None:
        with self._lock:
            self._last_failure_at[widget_type] = time.monotonic()
            self._last_error[widget_type] = error

    def _mint(self, widget_type: str, public_widget_id: str, access_token: str) -> Optional[str]:
        from core.server.url_config import HOSTED_WIDGETS_BASE_URL

        endpoint = f"{HOSTED_WIDGETS_BASE_URL}/api/{widget_type}/control-sessions"
        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"public_widget_id": public_widget_id},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("hosted_control_token_request_failed widget=%s error=%s", widget_type, exc)
            self._record_failure(widget_type, "widget_host_unreachable")
            return None

        if response.status_code != 200:
            error = self._error_code(response)
            logger.warning(
                "hosted_control_token_refused widget=%s status=%s error=%s",
                widget_type, response.status_code, error,
            )
            self._record_failure(widget_type, error)
            return None

        try:
            payload = response.json()
        except ValueError:
            self._record_failure(widget_type, "invalid_widget_host_response")
            return None

        token = str(payload.get("control_token") or "").strip()
        if not token:
            self._record_failure(widget_type, "missing_control_token")
            return None

        issued_for = str(payload.get("public_widget_id") or public_widget_id)
        with self._lock:
            self._tokens[widget_type] = token
            self._owner[widget_type] = issued_for
            self._last_failure_at.pop(widget_type, None)
            self._last_error.pop(widget_type, None)
        logger.info("hosted_control_token_ready widget=%s public_widget_id_present=True", widget_type)
        return token

    @staticmethod
    def _error_code(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return f"http_{response.status_code}"
        if isinstance(body, dict):
            return str(body.get("error") or body.get("detail") or f"http_{response.status_code}")
        return f"http_{response.status_code}"
