"""Runtime profile/session identity helpers for public widget isolation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from core.services.identity_resolver import resolve_identity

logger = logging.getLogger(__name__)

PUBLIC_MODE = True
DEV_PROFILE_ID = "local-dev"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SessionIdentity:
    profile_id: str
    session_id: str


class RuntimeSessionIdentity:
    """Stable per-install profile id plus per-process live routing session id."""

    _profile_id: str | None = None
    _session_id: str | None = None

    @classmethod
    def profile_id(cls) -> str:
        try:
            identity = resolve_identity()
            if identity.active_runtime_id:
                cls._profile_id = identity.active_runtime_id
                return identity.active_runtime_id
            logger.warning(
                "runtime_identity_unavailable account_status=%s warning=%s",
                identity.account_status, identity.warning,
            )
        except Exception as exc:
            logger.warning("runtime_identity_lookup_failed error=%s", exc, exc_info=True)
        raise ValueError("No active QuizMaster runtime identity is available")

    @classmethod
    def session_id(cls) -> str:
        if not cls._session_id:
            cls._session_id = f"session_{uuid.uuid4()}"
            logger.info(
                "quizmaster_session_started profile_id=%s session_id=%s started_at=%s",
                cls.profile_id(), cls._session_id, _now_iso(),
            )
        return cls._session_id

    @classmethod
    def current(cls) -> SessionIdentity:
        return SessionIdentity(profile_id=cls.profile_id(), session_id=cls.session_id())


def identity_from_event(event_data: Mapping[str, Any] | None) -> SessionIdentity:
    data = event_data or {}
    return SessionIdentity(
        profile_id=str(data.get("profile_id") or data.get("user_id") or RuntimeSessionIdentity.profile_id()),
        session_id=str(data.get("session_id") or RuntimeSessionIdentity.session_id()),
    )


def bind_event_identity(event_data: dict[str, Any] | None, *, source: str = "live") -> dict[str, Any]:
    data = dict(event_data or {})
    identity = RuntimeSessionIdentity.current()
    data.setdefault("profile_id", identity.profile_id)
    data.setdefault("session_id", identity.session_id)
    data.setdefault("event_source", source)
    data.setdefault("is_test_event", source in {"manual_test", "manual_fire", "test"})
    return data


def profile_room(profile_id: str | None = None) -> str:
    return f"profile:{profile_id or RuntimeSessionIdentity.profile_id()}"


def validate_profile_or_warn(route_profile_id: str | None, *, route: str) -> str:
    active = RuntimeSessionIdentity.profile_id()
    if not route_profile_id:
        if PUBLIC_MODE:
            logger.warning(
                "public_route_missing_profile_id route=%s active_profile_id=%s action=fallback_to_active_profile",
                route, active,
            )
        return active
    if route_profile_id != active:
        logger.warning(
            "profile_route_mismatch route=%s requested_profile_id=%s active_profile_id=%s action=reject",
            route, route_profile_id, active,
        )
        raise ValueError("Requested profile_id does not match the active QuizMaster profile")
    return active
