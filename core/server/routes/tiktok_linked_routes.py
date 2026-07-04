"""Linked TikTok account live-chat routes for QuizMaster.

These routes are the normal TikTok tab flow: resolve the TikTok username from the
QuizMaster Cloudflare auth broker, then connect TikTok Live chat to that linked
account. Manual username connection remains only as a hidden debug fallback in
legacy TikTok routes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from config.auth_broker import DEFAULT_AUTH_BASE_URL, load_auth_broker_config

logger = logging.getLogger(__name__)


def _sanitize_log_id(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:128]


def _quizmaster_broker_url() -> str:
    try:
        configured = (load_auth_broker_config().auth_base_url or DEFAULT_AUTH_BASE_URL).strip().rstrip("/")
    except Exception:
        configured = DEFAULT_AUTH_BASE_URL.rstrip("/")
    if "quizmaster.online" not in configured:
        logger.warning("linked_tiktok_broker_url_rejected configured=%s", configured)
        return DEFAULT_AUTH_BASE_URL.rstrip("/")
    return configured


def _resolve_ids() -> tuple[str, str]:
    profile_id = ""
    device_id = ""
    try:
        from core.services.identity_resolver import resolve_identity

        profile_id = str(resolve_identity().active_runtime_id or "").strip()
    except Exception as exc:
        logger.warning("linked_tiktok_identity_resolution_failed error=%s", exc)
    try:
        from core.services.local_identity import LocalIdentityService

        profile = LocalIdentityService().get_status().get("profile") or {}
        device_id = str(profile.get("device_id") or "").strip()
    except Exception as exc:
        logger.warning("linked_tiktok_device_resolution_failed error=%s", exc)
    return profile_id or "demo_profile", device_id or "demo_device"


def _linked_snapshot() -> dict[str, Any]:
    from core.tiktok.account_stats import CloudflareTikTokAuthProvider

    profile_id, device_id = _resolve_ids()
    provider = CloudflareTikTokAuthProvider(_quizmaster_broker_url(), device_id=device_id)
    status = provider.fetch_status(profile_id)
    stats = status.get("account_stats") if isinstance(status.get("account_stats"), dict) else {}
    username = str(stats.get("username") or "").strip().lstrip("@")
    followers = stats.get("exact_current_followers")
    available = bool(stats.get("available") and stats.get("exact") and username)
    return {
        "connected": bool(status.get("connected") or available),
        "available": available,
        "username": username,
        "display_name": stats.get("display_name") or username,
        "avatar_url": stats.get("avatar_url_100") or stats.get("avatar_url") or stats.get("avatar_large_url") or "",
        "verified": bool(stats.get("is_verified")),
        "followers": followers if isinstance(followers, int) and not isinstance(followers, bool) else None,
        "profile_id": profile_id,
        "device_id": device_id,
        "broker_url": _quizmaster_broker_url(),
    }


def register_tiktok_linked_routes(app: FastAPI, server):
    """Register official linked-account live chat routes."""

    @app.post("/api/tiktok/connect-linked")
    async def tiktok_connect_linked():
        """Connect live chat to the TikTok username returned by the auth broker."""
        try:
            from core.tiktok.tiktok_live_manager import TikTokLiveManager

            snapshot = _linked_snapshot()
            username = str(snapshot.get("username") or "").strip().lstrip("@")
            if not snapshot.get("available") or not username:
                raise HTTPException(
                    status_code=409,
                    detail="Official TikTok account must be linked before connecting live chat",
                )

            tiktok_manager = TikTokLiveManager.get_instance()
            if tiktok_manager.is_connected() and tiktok_manager.get_current_username() == username:
                return JSONResponse({
                    "success": True,
                    "username": username,
                    "source": "official_tiktok_account",
                    "message": f"Already connected to @{username}",
                })

            logger.info("linked_tiktok_live_chat_connect username=%s", _sanitize_log_id(username))
            ok = tiktok_manager.connect_to_user(username)
            return JSONResponse({
                "success": bool(ok),
                "username": username,
                "source": "official_tiktok_account",
                "message": f"Connecting to @{username}" if ok else "Failed to start connection",
            })
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("linked_tiktok_live_chat_connect_failed error=%s", exc, exc_info=True)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=502)

    logger.info("✅ TikTok linked-account routes registered")
