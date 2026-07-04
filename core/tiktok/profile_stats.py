"""Profile-level TikTok statistics, independent from TikTok LIVE ingestion."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROFILE_STATS_TTL_SECONDS = 15 * 60
PROFILE_STATS_CACHE_PATH = Path("data/tiktok_profile_stats.json")
_STAT_PATTERNS = {
    "follower_count": r'"followerCount"\s*:\s*(\d+)',
    "following_count": r'"followingCount"\s*:\s*(\d+)',
    "like_count": r'"heartCount"\s*:\s*(\d+)',
}
_COMPACT_COUNT_PATTERN = re.compile(r"^\s*\d+(?:\.\d+)?\s*[KMB]\s*$", re.IGNORECASE)
_TRUSTED_EXACT_SOURCES = {
    "tiktok_live_profile",
    "tiktok_live_stats",
}
_ESTIMATED_SOURCES = {
    "tiktok_public_profile",
    "tiktok_public_profile_estimated",
    "tiktok_live_estimate",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def unavailable_stats(username: str = "", *, stale: bool = False) -> dict[str, Any]:
    return {
        "platform": "tiktok",
        "username": str(username or "").lstrip("@"),
        "display_name": "",
        "avatar_url": "",
        "follower_count": None,
        "following_count": None,
        "like_count": None,
        "source": "unavailable",
        "updated_at": None,
        "stale": stale,
        "available": False,
        "estimated": False,
        "exact": False,
    }


def fetch_tiktok_profile(username: str) -> dict[str, Any]:
    """Fetch public profile metadata without depending on a LIVE connection."""
    import requests

    normalized = str(username or "").strip().lstrip("@")
    if not normalized:
        raise ValueError("TikTok username is required")
    response = requests.get(
        f"https://www.tiktok.com/@{normalized}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
        timeout=8,
    )
    response.raise_for_status()
    html = response.text
    counts: dict[str, int | None] = {}
    for key, pattern in _STAT_PATTERNS.items():
        match = re.search(pattern, html)
        counts[key] = int(match.group(1)) if match else None
    if counts["follower_count"] is None:
        raise ValueError("TikTok profile response did not include followerCount")

    display_match = re.search(r'"nickname"\s*:\s*"([^"]*)"', html)
    avatar_match = re.search(r'"avatarLarger"\s*:\s*"([^"]*)"', html)
    return {
        "platform": "tiktok",
        "username": normalized,
        "display_name": display_match.group(1) if display_match else normalized,
        "avatar_url": (avatar_match.group(1).replace(r"\u002F", "/") if avatar_match else ""),
        **counts,
        "source": "tiktok_public_profile_estimated",
        "updated_at": _now_iso(),
        "stale": True,
        "available": False,
        "estimated": True,
        "exact": False,
    }


class TikTokProfileStatsService:
    _instance: "TikTokProfileStatsService | None" = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        cache_path: Path | None = None,
        fetcher: Callable[[str], dict[str, Any]] | None = None,
        ttl_seconds: int = PROFILE_STATS_TTL_SECONDS,
    ):
        self.cache_path = cache_path or PROFILE_STATS_CACHE_PATH
        self.fetcher = fetcher or fetch_tiktok_profile
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._refreshing: set[str] = set()
        self._cache = self._load_cache()

    @classmethod
    def get_instance(cls) -> "TikTokProfileStatsService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)

    @staticmethod
    def _normalize(stats: dict[str, Any], username: str) -> dict[str, Any]:
        result = unavailable_stats(username)
        result.update({key: stats.get(key) for key in result})
        result["platform"] = "tiktok"
        result["username"] = str(result.get("username") or username).lstrip("@")
        follower_count = result.get("follower_count")
        compact = isinstance(follower_count, str) and bool(_COMPACT_COUNT_PATTERN.fullmatch(follower_count))
        exact_integer = (
            not isinstance(follower_count, bool)
            and (
                isinstance(follower_count, int)
                or (isinstance(follower_count, str) and follower_count.strip().isdigit())
            )
        )
        source = result.get("source")
        forced_estimate = source in _ESTIMATED_SOURCES
        trusted_source = source in _TRUSTED_EXACT_SOURCES
        explicitly_exact = stats.get("exact") is True
        exact = bool(
            exact_integer
            and not compact
            and not forced_estimate
            and not stats.get("estimated")
            and (explicitly_exact or trusted_source)
        )
        if exact:
            result["follower_count"] = int(follower_count)
        result["exact"] = exact
        result["estimated"] = bool(
            forced_estimate
            or stats.get("estimated")
            or compact
            or (follower_count is not None and not exact)
        )
        result["stale"] = bool(forced_estimate or stats.get("stale") or result["estimated"])
        result["available"] = exact
        return result

    def get_cached(self, profile_id: str, username: str = "") -> dict[str, Any]:
        with self._lock:
            stats = self._cache.get(str(profile_id))
            if not stats:
                logger.info(
                    "follower_widget_stats_unavailable profile_id=%s reason=no_cached_stats",
                    profile_id,
                )
                return unavailable_stats(username)
            result = self._normalize(dict(stats), username)
        if result["exact"]:
            logger.info(
                "follower_widget_stats_loaded profile_id=%s follower_count=%s stale=%s exact=true",
                profile_id, result["follower_count"], result["stale"],
            )
        elif result["estimated"]:
            logger.info(
                "follower_count_estimated profile_id=%s follower_count=%s source=%s stale=true exact=false",
                profile_id, result["follower_count"], result["source"],
            )
        else:
            logger.info("follower_count_unavailable profile_id=%s", profile_id)
        return result

    def refresh(self, profile_id: str, username: str, *, force: bool = False) -> dict[str, Any]:
        profile_id = str(profile_id or "").strip()
        username = str(username or "").strip().lstrip("@")
        if not profile_id or not username:
            return self.get_cached(profile_id, username)

        with self._lock:
            cached = self._cache.get(profile_id)
            fetched_at = float((cached or {}).get("_fetched_at", 0) or 0)
            same_username = (cached or {}).get("username") == username
            if not force and same_username and time.time() - fetched_at < self.ttl_seconds:
                return self._normalize(dict(cached), username)

        logger.info(
            "tiktok_profile_stats_fetch_started profile_id=%s username=%s",
            profile_id, username,
        )
        try:
            fetched = self._normalize(self.fetcher(username), username)
            fetched["_fetched_at"] = time.time()
            with self._lock:
                cached = self._normalize(dict(self._cache.get(profile_id) or {}), username)
                # A public-profile estimate must never replace the last exact
                # value obtained from the connected LIVE client.
                if fetched["exact"] or not cached["exact"]:
                    self._cache[profile_id] = fetched
                else:
                    cached["_fetched_at"] = fetched["_fetched_at"]
                    self._cache[profile_id] = cached
                self._save_cache()
            logger.info(
                "tiktok_profile_stats_fetch_success profile_id=%s username=%s follower_count=%s source=%s",
                profile_id, username, fetched["follower_count"], fetched["source"],
            )
            return self._normalize(fetched, username)
        except Exception as exc:
            logger.warning(
                "tiktok_profile_stats_fetch_failed profile_id=%s username=%s error=%s",
                profile_id, username, exc,
            )
            with self._lock:
                cached = self._cache.get(profile_id)
                if cached:
                    cached["stale"] = True
                    cached["available"] = bool(cached.get("exact"))
                    self._save_cache()
                    return self._normalize(dict(cached), username)
            return unavailable_stats(username, stale=True)

    def save_exact_live_count(
        self,
        profile_id: str,
        username: str,
        follower_count: int,
        *,
        source: str = "tiktok_live_profile",
    ) -> dict[str, Any]:
        """Persist an exact follower snapshot supplied by the active LIVE client."""
        if isinstance(follower_count, bool) or not isinstance(follower_count, int) or follower_count < 0:
            raise ValueError("Exact LIVE follower count must be a non-negative integer")
        if source not in _TRUSTED_EXACT_SOURCES:
            raise ValueError("Untrusted exact LIVE follower source")
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            raise ValueError("Profile ID is required")
        stats = self._normalize({
            "username": str(username or "").lstrip("@"),
            "follower_count": follower_count,
            "source": source,
            "updated_at": _now_iso(),
            "stale": False,
            "available": True,
            "estimated": False,
            "exact": True,
        }, username)
        stats["_fetched_at"] = time.time()
        with self._lock:
            self._cache[profile_id] = stats
            self._save_cache()
        logger.info(
            "exact_live_follower_count_saved profile_id=%s follower_count=%s source=%s stale=false exact=true",
            profile_id, follower_count, source,
        )
        return self._normalize(stats, username)

    def refresh_in_background(self, profile_id: str, username: str, *, force: bool = False) -> bool:
        key = str(profile_id or "")
        with self._lock:
            if not key or key in self._refreshing:
                return False
            self._refreshing.add(key)

        def run() -> None:
            try:
                self.refresh(key, username, force=force)
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        threading.Thread(target=run, daemon=True, name=f"tiktok-profile-{key[:12]}").start()
        return True

    def optimistic_increment(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            cached = self._cache.get(str(profile_id))
            previous = (cached or {}).get("follower_count")
            if not isinstance(previous, (int, float)):
                return self._normalize(dict(cached), "") if cached else unavailable_stats()
            cached["follower_count"] = int(previous) + 1
            cached["stale"] = True
            cached["source"] = "tiktok_live_estimate"
            cached["estimated"] = True
            self._save_cache()
            result = self._normalize(dict(cached), cached.get("username", ""))
        logger.info(
            "follower_widget_optimistic_increment profile_id=%s previous=%s next=%s estimated=true",
            profile_id, previous, result["follower_count"],
        )
        return result
