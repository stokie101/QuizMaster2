"""Broker-backed official TikTok diagnostics for the QuizMaster desktop app.

The desktop app must not store TikTok app keys or client secrets. Official TikTok
login is handled by the QuizMaster Cloudflare auth broker, and this module only
keeps a safe app-side diagnostic summary.
"""

from __future__ import annotations

import time
from typing import Any, Literal
from urllib.parse import urlencode

from config.auth_broker import DEFAULT_AUTH_BASE_URL, load_auth_broker_config

TikTokOfficialMode = Literal["production", "sandbox"]

MISSING_LIVE_FIELDS = (
    "live viewer count",
    "live comments",
    "live likes",
    "live follows",
    "gifts",
)


def _broker_base_url() -> str:
    """Return QuizMaster's public broker URL without allowing another app broker."""
    configured = (load_auth_broker_config().auth_base_url or DEFAULT_AUTH_BASE_URL).strip().rstrip("/")
    if "quizmaster.online" not in configured:
        return DEFAULT_AUTH_BASE_URL.rstrip("/")
    return configured


class TikTokOfficialDiagnosticsStore:
    """Safe diagnostics wrapper around the Cloudflare auth broker."""

    def __init__(self) -> None:
        self._auth_by_mode: dict[TikTokOfficialMode, dict[str, Any]] = {}

    def start_url(self, mode: TikTokOfficialMode) -> tuple[str, dict[str, Any]]:
        broker_base_url = _broker_base_url()
        params = {
            "source": "quizmaster_app",
            "mode": mode,
        }
        return f"{broker_base_url}/auth/tiktok/start?{urlencode(params)}", {
            "configured": True,
            "mode": mode,
            "broker_url": broker_base_url,
            "storage": "cloudflare_broker",
            "app_keys_required": False,
        }

    def finish_callback(self, mode: TikTokOfficialMode, code: str, state: str) -> dict[str, Any]:
        broker_base_url = _broker_base_url()
        result = self._result(
            mode,
            status="broker_callback_only",
            broker_url=broker_base_url,
            note="TikTok callback is handled by the Cloudflare broker, not by the desktop app.",
        )
        self._auth_by_mode[mode] = result
        return result

    def diagnostics(self) -> dict[str, Any]:
        broker_base_url = _broker_base_url()
        return {
            "success": True,
            "note": "Official TikTok Login/Display diagnostics are handled by the Cloudflare auth broker. The desktop app does not store TikTok app keys.",
            "broker": {
                "url": broker_base_url,
                "app_keys_required": False,
            },
            "comparison": {
                "current_unofficial_connector_provides": ["live chat", "gifts", "likes", "joins", "follows if available"],
                "official_login_display_api_provides": ["profile", "account stats"],
                "unknown_needs_testing": list(MISSING_LIVE_FIELDS),
            },
            "modes": {mode: self._auth_by_mode.get(mode, self._result(mode, status="broker_status_checked_elsewhere")) for mode in ("production", "sandbox")},
        }

    def _result(self, mode: TikTokOfficialMode, **extra: Any) -> dict[str, Any]:
        result = {
            "mode": mode,
            "updated_at": int(time.time()),
            "available_official_fields": [],
            "missing_live_fields": list(MISSING_LIVE_FIELDS),
        }
        result.update(extra)
        return result


official_tiktok_diagnostics = TikTokOfficialDiagnosticsStore()
