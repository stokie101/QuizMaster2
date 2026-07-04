"""Resolve QuizMaster account identity into one runtime ownership source.

This module intentionally does not sync local data, provision billing, or create
cloud records. It only chooses the ID the desktop runtime should use for widget
ownership and public URL generation.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

from core.services.auth_service import AuthService
from core.services.local_identity import LocalIdentityService

logger = logging.getLogger(__name__)

SIGNED_OUT = "not_signed_in"
SIGNED_IN = "signed_in"
SIGNED_IN_MISSING_WIDGET_ID = "signed_in_missing_public_widget_id"
LOCAL_IDENTITY_UNAVAILABLE = "local_identity_unavailable"


@dataclass(frozen=True)
class ResolvedIdentity:
    auth_user_id: Optional[str]
    public_widget_id: Optional[str]
    local_profile_id: Optional[str]
    active_runtime_id: Optional[str]
    account_status: str
    email: Optional[str]
    plan: str
    warning: Optional[str] = None

    @property
    def authenticated(self) -> bool:
        return self.account_status in {SIGNED_IN, SIGNED_IN_MISSING_WIDGET_ID}

    @property
    def url_mode(self) -> str:
        if self.authenticated and self.public_widget_id:
            return "account_public_widget_id"
        if self.authenticated:
            return "blocked_missing_public_widget_id"
        if self.local_profile_id:
            return "local_profile_fallback"
        return "unavailable"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authenticated"] = self.authenticated
        data["auth_user_id_present"] = bool(self.auth_user_id)
        data["url_mode"] = self.url_mode
        data["sync_status"] = "Not enabled yet"
        data["cloud_sync"] = "Coming later"
        return data


class IdentityResolver:
    """Resolve authenticated and local QuizMaster identities without mutating local IDs."""

    def __init__(
        self,
        auth_service: AuthService | None = None,
        local_identity_service: LocalIdentityService | None = None,
    ):
        self.auth_service = auth_service or AuthService.get_instance()
        self.local_identity_service = local_identity_service or LocalIdentityService()

    def resolve(self) -> ResolvedIdentity:
        local_profile_id, local_warning = self._local_profile_id()
        profile = getattr(self.auth_service, "current_profile", None)
        session = getattr(self.auth_service, "current_session", None)

        auth_user_id = getattr(profile, "id", None) or ((session.user or {}).get("id") if session else None)
        has_authenticated_session = bool(session and session.is_present())
        if has_authenticated_session or auth_user_id:
            public_widget_id = (getattr(profile, "public_widget_id", None) or "").strip() or None
            email = getattr(profile, "email", None) or ((session.user or {}).get("email") if session else None)
            plan = getattr(profile, "plan", None) or "Free"
            if public_widget_id:
                return ResolvedIdentity(
                    auth_user_id=auth_user_id,
                    public_widget_id=public_widget_id,
                    local_profile_id=local_profile_id,
                    active_runtime_id=public_widget_id,
                    account_status=SIGNED_IN,
                    email=email,
                    plan=plan,
                    warning=local_warning,
                )
            return ResolvedIdentity(
                auth_user_id=auth_user_id,
                public_widget_id=None,
                local_profile_id=local_profile_id,
                active_runtime_id=None,
                account_status=SIGNED_IN_MISSING_WIDGET_ID,
                email=email,
                plan=plan,
                warning="Connected account is missing public_widget_id; account-owned public widget URLs are blocked until the profile is repaired.",
            )

        status = SIGNED_OUT if local_profile_id else LOCAL_IDENTITY_UNAVAILABLE
        return ResolvedIdentity(
            auth_user_id=None,
            public_widget_id=None,
            local_profile_id=local_profile_id,
            active_runtime_id=local_profile_id,
            account_status=status,
            email=None,
            plan="Free",
            warning=local_warning,
        )

    def _local_profile_id(self) -> tuple[Optional[str], Optional[str]]:
        try:
            status = self.local_identity_service.get_status()
            profile = status.get("profile") or {}
            profile_id = str(profile.get("profile_id") or "").strip() or None
            warning = status.get("warning") or status.get("error")
            if not profile_id:
                warning = warning or "QuizMaster local profile ID is unavailable; local/offline widget URLs cannot be generated."
            return profile_id, warning
        except Exception as exc:
            logger.warning("QuizMaster local identity resolution failed: %s", exc, exc_info=True)
            return None, str(exc)


def resolve_identity() -> ResolvedIdentity:
    """Return the current QuizMaster runtime ownership identity."""
    return IdentityResolver().resolve()


def _safe_email(email: Optional[str]) -> Optional[str]:
    """Return a diagnostic-safe email that can still correlate startup phases."""
    value = str(email or "").strip()
    if not value:
        return None
    local, separator, domain = value.partition("@")
    if not separator:
        return f"{value[:1]}***"
    return f"{local[:1]}***@{domain}"


def log_runtime_identity(phase: str, identity: ResolvedIdentity | None = None) -> ResolvedIdentity | None:
    """Log the current resolved identity without changing resolution behavior."""
    try:
        resolved = identity or resolve_identity()
        logger.info(
            "runtime_identity_resolved phase=%s account_status=%s public_widget_id=%s "
            "local_profile_id=%s active_runtime_id=%s url_mode=%s email=%s",
            phase,
            resolved.account_status,
            resolved.public_widget_id,
            resolved.local_profile_id,
            resolved.active_runtime_id,
            resolved.url_mode,
            _safe_email(resolved.email),
        )
        return resolved
    except Exception as exc:
        logger.info(
            "runtime_identity_resolved phase=%s account_status=resolution_failed "
            "public_widget_id=None local_profile_id=None active_runtime_id=None "
            "url_mode=unavailable email=None error=%s",
            phase,
            exc,
        )
        return None
