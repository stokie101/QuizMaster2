"""Authenticated TikTok account statistics and exact snapshot persistence."""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from config.auth_broker import AuthBrokerConfig, DEFAULT_AUTH_BASE_URL, load_auth_broker_config

logger = logging.getLogger(__name__)

ACCOUNT_STATS_CACHE_PATH = Path("data/tiktok_account_stats.json")

TRUSTED_AUTHENTICATED_SOURCES = {
    "tiktok_authenticated_account",
    "tiktok_oauth_broker",
    "tiktok_official_account",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _quizmaster_broker_url(config: AuthBrokerConfig) -> str:
    configured = str(config.auth_base_url or DEFAULT_AUTH_BASE_URL).strip().rstrip("/")
    if "quizmaster.online" not in configured:
        return DEFAULT_AUTH_BASE_URL.rstrip("/")
    return configured


def unavailable_account_stats(profile_id: str = "", username: str = "") -> dict[str, Any]:
    return {
        "platform": "tiktok",
        "profile_id": str(profile_id or ""),
        "username": str(username or "").strip().lstrip("@"),
        "exact_current_followers": None,
        "exact_current_following": None,
        "exact_profile_likes": None,
        "profile_views": None,
        "live_views": None,
        "updated_at": None,
        "source": "unavailable",
        "exact": False,
        "available": False,
        "stale": False,
    }


class TikTokAccountStatsProvider(ABC):
    """Interface for a future authenticated TikTok account/session adapter."""

    def start(self) -> None:
        """Start provider resources, if any."""

    def stop(self) -> None:
        """Stop provider resources, if any."""

    @abstractmethod
    def fetch_account_stats(self, profile_id: str, username: str) -> dict[str, Any]:
        """Return an authenticated, exact account statistics snapshot."""
        raise NotImplementedError


class AccountStatsUnavailableError(RuntimeError):
    """Provider failure carrying a safe, machine-readable unavailability reason."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class CloudflareTikTokAuthProvider(TikTokAccountStatsProvider):
    """Use QuizMaster's public broker without handling backend or TikTok secrets."""

    def __init__(self, base_url: str, device_id: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.device_id = str(device_id or "").strip()
        self.timeout = timeout

    def _identity_params(
        self,
        profile_id: str,
        device_id: str | None = None,
    ) -> dict[str, str]:
        return {
            "profile_id": str(profile_id or "").strip(),
            "device_id": str(device_id if device_id is not None else self.device_id).strip(),
        }

    def connect(
        self,
        profile_id: str,
        device_id: str | None = None,
    ) -> bool:
        """Open TikTok OAuth in the user's external browser."""
        request = requests.Request(
            "GET",
            urljoin(self.base_url, "auth/tiktok/start"),
            params=self._identity_params(profile_id, device_id),
        ).prepare()
        return webbrowser.open(request.url)

    def fetch_status(
        self,
        profile_id: str = "",
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the public broker status payload plus safe account stats when available."""
        response = requests.get(
            urljoin(self.base_url, "auth/tiktok/status"),
            params=self._identity_params(profile_id, device_id),
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.ok or not isinstance(payload, dict):
            raise AccountStatsUnavailableError("provider_error")

        if payload.get("connected") or payload.get("configured") or payload.get("available"):
            try:
                account_response = requests.get(
                    urljoin(self.base_url, "auth/tiktok/account-stats"),
                    params=self._identity_params(profile_id, device_id),
                    timeout=self.timeout,
                )
                account_payload = account_response.json()
                if isinstance(account_payload, dict):
                    payload["account_stats"] = account_payload
                    payload["account_stats_http_status"] = account_response.status_code
                else:
                    payload["account_stats"] = {"available": False, "reason": "invalid_account_stats_payload"}
                    payload["account_stats_http_status"] = account_response.status_code
            except Exception as exc:
                payload["account_stats"] = {"available": False, "reason": "account_stats_fetch_failed", "error": str(exc)}

        return payload

    def fetch_account_stats(self, profile_id: str, username: str) -> dict[str, Any]:
        response = requests.get(
            urljoin(self.base_url, "auth/tiktok/account-stats"),
            params=self._identity_params(profile_id),
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if not response.ok:
            reason = (
                payload.get("reason")
                or payload.get("error")
                or payload.get("code")
                or "provider_error"
            )
            raise AccountStatsUnavailableError(str(reason))
        if not isinstance(payload, dict):
            raise AccountStatsUnavailableError("provider_error")
        return payload


# Compatibility for callers using the previous account-stats-specific name.
CloudflareTikTokAccountStatsProvider = CloudflareTikTokAuthProvider


class TikTokAccountStatsService:
    """Stores exact authenticated account snapshots separately from public data."""

    _instance: "TikTokAccountStatsService | None" = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        provider: TikTokAccountStatsProvider | None = None,
        cache_path: Path | None = None,
    ):
        self.provider = provider
        self.cache_path = cache_path or ACCOUNT_STATS_CACHE_PATH
        self._lock = threading.RLock()
        self._started = False
        self._snapshots = self._load_snapshots()

    @classmethod
    def get_instance(cls) -> "TikTokAccountStatsService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if self.provider is not None:
                self.provider.start()
            self._started = True

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            if self.provider is not None:
                self.provider.stop()
            self._started = False

    def clear(self) -> None:
        """Forget every cached authenticated-account snapshot and delete the
        on-disk cache. Called on logout so a new account never inherits the
        previous user's exact TikTok account stats."""
        with self._lock:
            self._snapshots = {}
            for path in (self.cache_path, self.cache_path.with_suffix(".tmp")):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def refresh_now(self, profile_id: str, username: str) -> dict[str, Any]:
        profile_id = str(profile_id or "").strip()
        username = str(username or "").strip().lstrip("@")
        if self.provider is None:
            logger.warning(
                "tiktok_account_stats_unavailable profile_id=%s reason=no_authenticated_provider",
                profile_id,
            )
            return unavailable_account_stats(profile_id, username)

        try:
            snapshot = self._normalize(
                self.provider.fetch_account_stats(profile_id, username),
                profile_id,
                username,
            )
        except AccountStatsUnavailableError as exc:
            logger.warning(
                "tiktok_account_stats_unavailable profile_id=%s reason=%s",
                profile_id,
                exc.reason,
            )
            return unavailable_account_stats(profile_id, username)
        except Exception as exc:
            logger.warning(
                "tiktok_account_stats_unavailable profile_id=%s reason=provider_error error=%s",
                profile_id,
                exc,
            )
            return unavailable_account_stats(profile_id, username)

        if not snapshot["available"]:
            reason = (
                snapshot.get("reason")
                or snapshot.get("error")
                or snapshot.get("code")
                or "provider_returned_unavailable"
            )
            logger.warning(
                "tiktok_account_stats_unavailable profile_id=%s reason=%s",
                profile_id,
                reason,
            )
            return snapshot

        with self._lock:
            self._snapshots[profile_id] = snapshot
            self._save_snapshots()
        return dict(snapshot)

    def get_last_exact(self, profile_id: str) -> dict[str, Any]:
        profile_id = str(profile_id or "").strip()
        with self._lock:
            snapshot = self._snapshots.get(profile_id)
            if snapshot is not None:
                normalized = self._normalize(
                    dict(snapshot),
                    profile_id,
                    str(snapshot.get("username") or ""),
                )
                if normalized["available"]:
                    return normalized
        return unavailable_account_stats(profile_id)

    def _load_snapshots(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_snapshots(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._snapshots, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)

    @staticmethod
    def _normalize(
        stats: dict[str, Any],
        profile_id: str,
        username: str,
    ) -> dict[str, Any]:
        result = unavailable_account_stats(profile_id, username)
        if isinstance(stats, dict):
            result.update({key: stats.get(key) for key in result})
            for extra_field in (
                "display_name",
                "avatar_url",
                "avatar_url_100",
                "avatar_large_url",
                "profile_deep_link",
                "is_verified",
            ):
                if stats.get(extra_field) is not None:
                    result[extra_field] = stats.get(extra_field)
            for reason_field in ("reason", "error", "code"):
                if stats.get(reason_field):
                    result[reason_field] = stats[reason_field]
        followers = result.get("exact_current_followers")
        exact_followers = (
            not isinstance(followers, bool)
            and (
                isinstance(followers, int)
                or (isinstance(followers, str) and followers.strip().isdigit())
            )
        )
        authenticated = result.get("source") in TRUSTED_AUTHENTICATED_SOURCES
        exact = bool(exact_followers and authenticated and stats.get("exact") is True)
        if exact:
            result["exact_current_followers"] = max(0, int(followers))
        else:
            result["exact_current_followers"] = None
        result["platform"] = "tiktok"
        result["profile_id"] = profile_id
        result["username"] = str(result.get("username") or username).strip().lstrip("@")
        result["source"] = "tiktok_authenticated_account" if exact else "unavailable"
        result["updated_at"] = str(result.get("updated_at") or _now_iso()) if exact else None
        result["exact"] = exact
        result["available"] = exact
        result["stale"] = False
        return result


def register_cloudflare_tiktok_account_stats_provider(
    config: AuthBrokerConfig | None = None,
) -> TikTokAccountStatsProvider | None:
    """Register the broker provider from startup configuration without exposing secrets."""
    broker_config = config or load_auth_broker_config()
    missing_fields = broker_config.status()["missing_fields"]
    if missing_fields:
        logger.warning(
            "cloudflare_tiktok_auth_provider_missing missing_fields=%s",
            missing_fields,
        )
        return None

    try:
        from core.services.local_identity import LocalIdentityService

        identity = LocalIdentityService().get_status().get("profile") or {}
        device_id = str(identity.get("device_id") or "")
    except Exception:
        device_id = ""

    provider = CloudflareTikTokAuthProvider(
        _quizmaster_broker_url(broker_config),
        device_id=device_id,
    )
    service = TikTokAccountStatsService.get_instance()
    with service._lock:
        service.provider = provider
    logger.info(
        "cloudflare_tiktok_auth_provider_registered base_url=%s mode=production_public_broker",
        _quizmaster_broker_url(broker_config),
    )
    return provider
