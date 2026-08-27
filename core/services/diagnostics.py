"""Support bundle: what went wrong, in a form a user can send you.

When the hosted dock or a quiz misbehaves for a customer, the evidence is
already on their machine -- the rotating logs under %APPDATA%/QuizMaster/logs
and the runtime's own state. Without a way to collect it, the first you hear of
a fault is a message saying "it doesn't work", and the diagnosis starts from
nothing.

This builds a single zip: a report of the runtime's current state plus the
recent logs, with credentials stripped. Nothing is uploaded anywhere -- the user
chooses what to do with the file.
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Anything that could authenticate as the user must never reach a support file.
# Matched on shape rather than on the key that carries it, so a value logged
# under a name nobody anticipated is still caught.
REDACTIONS: List[tuple[re.Pattern[str], str]] = [
    # JWTs and the widget control tokens, which share the base64url.base64url shape.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]+)?"), "[redacted-token]"),
    (re.compile(r"(control_token=)[^&\s\"']+"), r"\1[redacted]"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|password|secret)\b(\"?\s*[:=]\s*\"?)[^\s,\"'}]+",),
     r"\1\2[redacted]"),
    # Email addresses: identifying, and never needed to diagnose a fault.
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[redacted-email]"),
]

MAX_LOG_BYTES = 512 * 1024  # the tail of each log; enough for a session
BUNDLE_PREFIX = "quizmaster-diagnostics"


def redact(text: str) -> str:
    """Strip anything credential-shaped from a block of text."""
    out = text
    for pattern, replacement in REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def _identity() -> Dict[str, Any]:
    try:
        from core.server.url_config import resolved_runtime_identity

        identity = dict(resolved_runtime_identity())
    except Exception as exc:
        return {"error": str(exc)}

    widget_id = str(identity.get("public_widget_id") or "")
    return {
        # The prefix identifies the account's app without publishing the id.
        "public_widget_id_prefix": widget_id[:8] + "…" if widget_id else None,
        "public_widget_id_present": bool(widget_id),
        "authenticated": bool(identity.get("authenticated")),
        "account_status": identity.get("account_status"),
        "plan": identity.get("plan"),
        "url_mode": identity.get("url_mode"),
    }


def _quiz_state() -> Dict[str, Any]:
    try:
        from core.services.service_locator import ServiceLocator

        quiz_manager = ServiceLocator.get_instance().get_service("QuizManager")
        if not quiz_manager:
            return {"available": False}
        timer = getattr(quiz_manager, "timer", None)
        return {
            "available": True,
            "state": str(getattr(quiz_manager.state, "state", "")),
            "total_questions": getattr(quiz_manager, "total_question_count", None),
            "timer_running": bool(timer.is_running()) if timer else None,
            "timer_paused": bool(timer.is_paused()) if timer else None,
            "timer_remaining": timer.get_remaining() if timer else None,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _hosted() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        from core.services.hosted_bridge import hosted_bridge_status

        out["bridge"] = hosted_bridge_status()
    except Exception as exc:
        out["bridge"] = {"error": str(exc)}
    try:
        from core.services.hosted_control import HostedControlTokens

        status = HostedControlTokens.get_instance().status("quiz")
        out["control_token"] = {
            "has_token": bool(status.get("has_token")),
            "error": status.get("error"),
        }
    except Exception as exc:
        out["control_token"] = {"error": str(exc)}
    try:
        from core.server.url_config import HOSTED_WIDGETS_BASE_URL

        out["widget_host"] = HOSTED_WIDGETS_BASE_URL
    except Exception:
        pass
    return out


def _sound_config() -> Dict[str, Any]:
    try:
        from config.config_manager import ConfigManager

        config = ConfigManager.get_instance()
        keys = (
            "enable_timer_sound", "timer_volume",
            "enable_background_sound", "background_volume",
            "enable_effects_sound", "effects_volume",
        )
        return {key: config.get("SOUND", key, fallback=None) for key in keys}
    except Exception as exc:
        return {"error": str(exc)}


def build_report() -> Dict[str, Any]:
    """The runtime's current state, with nothing identifying in it."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": {
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
            "platform": platform.platform(),
        },
        "account": _identity(),
        "quiz": _quiz_state(),
        "hosted": _hosted(),
        "sound": _sound_config(),
    }


def _log_dir() -> Path:
    from core.utils.logging_config import resolve_appdata_root

    return resolve_appdata_root() / "logs"


def _log_tail(path: Path, limit: int = MAX_LOG_BYTES) -> str:
    """The last `limit` bytes of a log, redacted."""
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # discard the partial first line
            return redact(handle.read())
    except Exception as exc:
        return f"[could not read {path.name}: {exc}]"


def build_bundle_bytes() -> bytes:
    """The whole support bundle as a zip, in memory."""
    report = build_report()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(report, indent=2, default=str))
        log_dir = _log_dir()
        if log_dir.is_dir():
            for log in sorted(log_dir.glob("*.log")):
                archive.writestr(f"logs/{log.name}", _log_tail(log))
    return buffer.getvalue()


def write_bundle(destination: Path | None = None) -> Path:
    """Write the bundle next to the logs (or where asked) and return its path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(destination) if destination else (_log_dir() / f"{BUNDLE_PREFIX}-{stamp}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_bundle_bytes())
    logger.info("diagnostics_bundle_written name=%s bytes=%s", target.name, target.stat().st_size)
    return target
