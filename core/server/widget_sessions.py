"""Owner-scoped Quiz and Chess sessions for public widgets and private controls."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

WidgetType = Literal["quiz", "chess"]
ACTIVE_STATUSES = {"created", "active", "paused"}
ALL_STATUSES = ACTIVE_STATUSES | {"completed", "expired", "revoked"}
CONTROL_TOKEN_TTL_SECONDS = 15 * 60
# A control-exchange code travels inside a URL the streamer copies out of
# Overlay Studio and pastes into an OBS browser source, so a one-minute life
# expired before the page it belongs to was ever opened.
EXCHANGE_TTL_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _debug(message: str, *args: object) -> None:
    if os.getenv("LIVEFORGE_WIDGET_DEBUG", "0") == "1":
        logger.info("widget_debug " + message, *args)


@dataclass
class WidgetSession:
    session_id: str
    widget_type: WidgetType
    owner_user_id: str
    public_widget_id: str
    status: str = "created"
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)
    version: int = 0
    snapshot: dict[str, Any] = field(default_factory=dict)
    last_event_id: str | None = None
    last_event_type: str | None = None
    last_event_occurred_at: str | None = None

    @property
    def room(self) -> str:
        return f"{self.widget_type}:{self.owner_user_id}:{self.session_id}"

    def public_view(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "widget_type": self.widget_type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "snapshot": self.snapshot,
            "last_event_id": self.last_event_id,
            "last_event_type": self.last_event_type,
            "last_event_occurred_at": self.last_event_occurred_at,
        }


class SessionAuthorizationError(PermissionError):
    """Raised when a public route or control credential is not authorized."""


class SessionConflictError(RuntimeError):
    """Raised when a stale event attempts to overwrite newer state."""


class WidgetSessionStore:
    """Durable server-side store with owner validation and scoped control tokens."""

    _instance: "WidgetSessionStore | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "WidgetSessionStore":
        with cls._instance_lock:
            if cls._instance is None:
                configured = os.getenv("LIVEFORGE_WIDGET_SESSION_STORE")
                cls._instance = cls(Path(configured) if configured else Path("data/widget_sessions.json"))
            return cls._instance

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._sessions: dict[str, WidgetSession] = {}
        self._control_tokens: dict[str, dict[str, Any]] = {}
        self._exchange_codes: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _key(widget_type: WidgetType, session_id: str) -> str:
        return f"{widget_type}:{session_id}"

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                for raw in payload.get("sessions", []):
                    session = WidgetSession(**raw)
                    self._sessions[self._key(session.widget_type, session.session_id)] = session
                self._control_tokens = dict(payload.get("control_tokens") or {})
                self._exchange_codes = dict(payload.get("exchange_codes") or {})
                self._purge_expired_credentials()
            except Exception as exc:
                logger.error("widget_session_store_load_failed path=%s error=%s", self.path, exc)
                raise

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "sessions": [asdict(item) for item in self._sessions.values()],
            "control_tokens": self._control_tokens,
            "exchange_codes": self._exchange_codes,
        }
        temp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(temp, self.path)

    def _purge_expired_credentials(self) -> None:
        now = _now()
        self._control_tokens = {
            key: value for key, value in self._control_tokens.items()
            if _parse_iso(value["expires_at"]) > now and not value.get("revoked")
        }
        self._exchange_codes = {
            key: value for key, value in self._exchange_codes.items()
            if _parse_iso(value["expires_at"]) > now and not value.get("used")
        }

    def create_session(
        self,
        widget_type: WidgetType,
        owner_user_id: str,
        public_widget_id: str,
        snapshot: dict[str, Any] | None = None,
    ) -> WidgetSession:
        if widget_type not in {"quiz", "chess"}:
            raise ValueError("Unsupported widget type")
        if not owner_user_id or not public_widget_id:
            raise ValueError("Authenticated owner_user_id and public_widget_id are required")
        with self._lock:
            session = WidgetSession(
                session_id=str(uuid.uuid4()),
                widget_type=widget_type,
                owner_user_id=owner_user_id,
                public_widget_id=public_widget_id,
                snapshot=dict(snapshot or {}),
            )
            self._sessions[self._key(widget_type, session.session_id)] = session
            self._save()
            _debug(
                "widget_type=%s session_id=%s public_widget_id=%s owner_user_id_present=true derived_room_type=%s",
                widget_type, session.session_id, public_widget_id, widget_type,
            )
            return session

    def get(self, widget_type: WidgetType, session_id: str) -> WidgetSession:
        with self._lock:
            session = self._sessions.get(self._key(widget_type, session_id))
            if not session:
                raise SessionAuthorizationError("Session not found")
            return session

    def resolve_public(self, widget_type: WidgetType, public_widget_id: str, session_id: str) -> WidgetSession:
        session = self.get(widget_type, session_id)
        authorized = secrets.compare_digest(session.public_widget_id, public_widget_id or "")
        authorized = authorized and session.status not in {"expired", "revoked"}
        _debug(
            "widget_type=%s session_id=%s public_widget_id=%s subscription_authorized=%s derived_room_type=%s snapshot_version=%s",
            widget_type, session_id, public_widget_id, authorized, widget_type, session.version,
        )
        if not authorized:
            raise SessionAuthorizationError("Public widget does not own this session")
        return session

    def authorize_owner(self, widget_type: WidgetType, session_id: str, owner_user_id: str) -> WidgetSession:
        session = self.get(widget_type, session_id)
        authorized = bool(owner_user_id) and secrets.compare_digest(session.owner_user_id, owner_user_id)
        authorized = authorized and session.status in ACTIVE_STATUSES
        _debug(
            "widget_type=%s session_id=%s publish_authorized=%s owner_user_id_present=%s",
            widget_type, session_id, authorized, bool(owner_user_id),
        )
        if not authorized:
            raise SessionAuthorizationError("Authenticated user does not own this active session")
        return session

    def issue_control_exchange(
        self,
        widget_type: WidgetType,
        session_id: str,
        owner_user_id: str,
        ttl_seconds: int = EXCHANGE_TTL_SECONDS,
    ) -> str:
        session = self.authorize_owner(widget_type, session_id, owner_user_id)
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._exchange_codes[_digest(code)] = {
                "widget_type": widget_type,
                "session_id": session.session_id,
                "owner_user_id": owner_user_id,
                "expires_at": _iso(_now() + timedelta(seconds=ttl_seconds)),
                "used": False,
            }
            self._save()
        return code

    def exchange_control_code(self, code: str) -> tuple[str, WidgetSession]:
        code_hash = _digest(code or "")
        with self._lock:
            self._purge_expired_credentials()
            grant = self._exchange_codes.get(code_hash)
            if not grant or grant.get("used"):
                raise SessionAuthorizationError("Control exchange is invalid or expired")
            grant["used"] = True
            session = self.authorize_owner(grant["widget_type"], grant["session_id"], grant["owner_user_id"])
            token = secrets.token_urlsafe(48)
            self._control_tokens[_digest(token)] = {
                "widget_type": session.widget_type,
                "session_id": session.session_id,
                "owner_user_id": session.owner_user_id,
                "scope": "control",
                "expires_at": _iso(_now() + timedelta(seconds=CONTROL_TOKEN_TTL_SECONDS)),
                "revoked": False,
            }
            self._save()
            return token, session

    def authorize_control(self, widget_type: WidgetType, session_id: str, token: str) -> WidgetSession:
        with self._lock:
            self._purge_expired_credentials()
            grant = self._control_tokens.get(_digest(token or ""))
            if not grant:
                raise SessionAuthorizationError("Control token is invalid or expired")
            if grant.get("scope") != "control" or grant.get("widget_type") != widget_type:
                raise SessionAuthorizationError("Control token scope does not match widget type")
            if grant.get("session_id") != session_id:
                raise SessionAuthorizationError("Control token does not match session")
            return self.authorize_owner(widget_type, session_id, grant.get("owner_user_id") or "")

    def update_snapshot(
        self,
        widget_type: WidgetType,
        session_id: str,
        owner_user_id: str,
        snapshot: dict[str, Any],
        *,
        event_type: str,
        expected_version: int | None = None,
        public_snapshot: dict[str, Any] | None = None,
    ) -> WidgetSession:
        session = self.authorize_owner(widget_type, session_id, owner_user_id)
        with self._lock:
            if expected_version is not None and expected_version != session.version:
                raise SessionConflictError(f"Stale version {expected_version}; current version is {session.version}")
            session.version += 1
            session.snapshot = dict(public_snapshot if public_snapshot is not None else snapshot)
            session.last_event_id = str(uuid.uuid4())
            session.last_event_type = event_type
            session.last_event_occurred_at = _iso()
            session.updated_at = session.last_event_occurred_at
            if session.status == "created":
                session.status = "active"
            self._save()
            _debug(
                "widget_type=%s session_id=%s snapshot_version=%s last_event_type=%s last_event_version=%s",
                widget_type, session_id, session.version, event_type, session.version,
            )
            return session

    def set_status(self, widget_type: WidgetType, session_id: str, owner_user_id: str, status: str) -> WidgetSession:
        if status not in ALL_STATUSES:
            raise ValueError("Invalid session status")
        session = self.get(widget_type, session_id)
        if not secrets.compare_digest(session.owner_user_id, owner_user_id or ""):
            raise SessionAuthorizationError("Authenticated user does not own this session")
        with self._lock:
            session.status = status
            session.updated_at = _iso()
            self._save()
            return session

    def reset(self, widget_type: WidgetType, session_id: str, owner_user_id: str) -> WidgetSession:
        """Reuse the same session ID, clear state, and advance the version."""
        session = self.authorize_owner(widget_type, session_id, owner_user_id)
        with self._lock:
            session.snapshot = {}
            session.version += 1
            session.last_event_id = str(uuid.uuid4())
            session.last_event_type = "reset"
            session.last_event_occurred_at = _iso()
            session.status = "active"
            session.updated_at = session.last_event_occurred_at
            self._save()
            return session

    def revoke(self, widget_type: WidgetType, session_id: str, owner_user_id: str) -> WidgetSession:
        return self.set_status(widget_type, session_id, owner_user_id, "revoked")
