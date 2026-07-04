"""Central URL configuration for QuizMaster public/local links."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode, urlparse

MAX_ACTION_SCREENS = 0

logger = logging.getLogger(__name__)

LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://localhost:5555").rstrip("/")
# Hosted value preserved for the future website/widget split; set URL_MODE=public
# and WIDGETS_BASE_URL=https://widgets.quizmaster.online to re-enable it.
HOSTED_WIDGETS_BASE_URL = "https://widgets.quizmaster.online"
WIDGETS_BASE_URL = os.getenv("WIDGETS_BASE_URL", LOCAL_BASE_URL).rstrip("/")
# Backwards-compatible alias for modules that still consume PUBLIC_BASE_URL.
PUBLIC_BASE_URL = WIDGETS_BASE_URL
URL_MODE = os.getenv("URL_MODE", "local").strip().lower() or "local"
WIDGET_DEBUG = os.getenv("QUIZMASTER_WIDGET_DEBUG", "0") == "1"

# These desktop-only pages do not yet have an account-scoped public backend contract.
PUBLIC_URL_BLOCKLIST = frozenset({"/obs/control"})


def _debug(message: str, *args: object) -> None:
    if WIDGET_DEBUG:
        logger.info("widget_debug " + message, *args)


def resolved_runtime_identity() -> dict[str, object]:
    """Return the resolved account/local identity used by URL generation."""
    try:
        from core.services.identity_resolver import resolve_identity
        return resolve_identity().to_dict()
    except Exception as exc:
        return {
            "auth_user_id": None,
            "public_widget_id": None,
            "local_profile_id": None,
            "active_runtime_id": None,
            "account_status": "identity_unavailable",
            "email": None,
            "plan": "Free",
            "warning": str(exc),
            "authenticated": False,
            "auth_user_id_present": False,
            "url_mode": "unavailable",
        }


def active_profile_id() -> str:
    """Return the public widget id, permitting local identity only in development mode."""
    identity = resolved_runtime_identity()
    public_widget_id = str(identity.get("public_widget_id") or "").strip()
    if public_widget_id:
        return public_widget_id
    if URL_MODE != "public":
        local_id = str(identity.get("active_runtime_id") or identity.get("local_profile_id") or "local-dev").strip()
        if local_id:
            return local_id
    raise ValueError(
        identity.get("warning")
        or "Your QuizMaster account is missing a public_widget_id; public widget URLs cannot be generated."
    )


def active_base_url() -> str:
    """Return the base URL that should be displayed/copied in the UI."""
    return WIDGETS_BASE_URL if URL_MODE == "public" else LOCAL_BASE_URL


def user_prefix(user_id: str | None = None) -> str:
    """Return the required user URL prefix for public widgets."""
    runtime_id = user_id or active_profile_id()
    return f"/u/{runtime_id}"


def normalize_widget_path(path: str) -> str:
    """Return the canonical public widget/control path without a local/user prefix."""
    clean_path = path if path.startswith("/") else f"/{path}"
    if clean_path.startswith("/quiz/overlay"):
        clean_path = clean_path.replace("/quiz/overlay", "/actions_events/overlay", 1)
    return clean_path


def public_widget_path(path: str, user_id: str | None = None) -> str:
    """Return a user-scoped widget/control path for displayed public URLs."""
    clean_path = normalize_widget_path(path)
    if clean_path in PUBLIC_URL_BLOCKLIST:
        raise ValueError(f"Public URL generation is disabled until a scoped route exists: {clean_path}")
    if clean_path.startswith("/u/"):
        return clean_path
    return f"{user_prefix(user_id)}{clean_path}"


def _append_query(url: str, query: dict[str, object] | None = None) -> str:
    if query:
        return f"{url}?{urlencode(query)}"
    return url


def get_public_url(path: str, query: dict[str, object] | None = None, user_id: str | None = None) -> str:
    """Build a displayed browser-source URL from the active base configuration."""
    try:
        if URL_MODE != "public":
            return get_internal_url(path, query)
        url = _append_query(f"{WIDGETS_BASE_URL}{public_widget_path(path, user_id)}", query)
        _debug(
            "generated_route=%s base_host=%s public_widget_id_present=%s transport=https",
            normalize_widget_path(path), urlparse(WIDGETS_BASE_URL).netloc, True,
        )
        return url
    except Exception as exc:
        _debug(
            "generated_route=%s base_host=%s public_widget_id_present=%s transport=https error=%s",
            normalize_widget_path(path), urlparse(WIDGETS_BASE_URL).netloc, False, str(exc),
        )
        return ""


def get_internal_url(path: str = "", query: dict[str, object] | None = None) -> str:
    """Build a local-only URL for desktop QWebEngine/internal app loading."""
    clean_path = path if path.startswith("/") else f"/{path}" if path else ""
    return _append_query(f"{LOCAL_BASE_URL}{clean_path}", query)


def get_socket_url(path: str = "/socket.io") -> str:
    """Build the Socket.IO endpoint URL, using WSS for the production widget host."""
    base = WIDGETS_BASE_URL if URL_MODE == "public" else LOCAL_BASE_URL
    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    clean_path = path if path.startswith("/") else f"/{path}"
    url = f"{scheme}://{parsed.netloc}{clean_path}"
    _debug("transport=socket websocket_url_host=%s websocket_scheme=%s", parsed.netloc, scheme)
    return url


def build_public_url(path: str, query: dict[str, object] | None = None, user_id: str | None = None) -> str:
    """Build a displayed/copyable public URL scoped to public_widget_id."""
    return get_public_url(path, query, user_id=user_id)


def _build_session_urls(widget_type: str, session_id: str | None = None) -> dict[str, str]:
    """Create one owner-scoped session and return all URLs for that instance."""
    identity = resolved_runtime_identity()
    owner_user_id = str(identity.get("auth_user_id") or "").strip()
    public_widget_id = str(identity.get("public_widget_id") or "").strip()
    if not owner_user_id or not public_widget_id:
        raise ValueError("Authenticated user_id and public_widget_id are required for widget sessions")

    from core.server.widget_sessions import WidgetSessionStore

    store = WidgetSessionStore.get_instance()
    session = (
        store.authorize_owner(widget_type, session_id, owner_user_id)
        if session_id
        else store.create_session(widget_type, owner_user_id, public_widget_id)
    )
    exchange = store.issue_control_exchange(widget_type, session.session_id, owner_user_id)
    query = {"session": session.session_id}
    control_query = {**query, "control_exchange": exchange}
    if widget_type == "quiz":
        return {
            "session_id": session.session_id,
            "display_url": get_public_url("/quiz_display", query, user_id=public_widget_id),
            "leaderboard_url": get_public_url("/leaderboard", query, user_id=public_widget_id),
            "controls_url": get_public_url("/quiz_controls", control_query, user_id=public_widget_id),
        }
    if widget_type == "chess":
        return {
            "session_id": session.session_id,
            "display_url": get_public_url("/chess/display", query, user_id=public_widget_id),
            "status_url": get_public_url("/chess/leaderboard", query, user_id=public_widget_id),
            "controls_url": get_public_url("/chess/controls", control_query, user_id=public_widget_id),
        }
    raise ValueError("Unsupported widget type")


def build_quiz_urls(session_id: str | None = None) -> dict[str, str]:
    return _build_session_urls("quiz", session_id)


def build_chess_urls(session_id: str | None = None) -> dict[str, str]:
    return _build_session_urls("chess", session_id)


def as_dict() -> dict[str, object]:
    """Serialize URL config for browser clients."""
    identity = resolved_runtime_identity()
    public_widget_id = identity.get("public_widget_id")
    active_id = public_widget_id or (identity.get("active_runtime_id") if URL_MODE != "public" else None)
    return {
        "WIDGETS_BASE_URL": WIDGETS_BASE_URL,
        "PUBLIC_BASE_URL": WIDGETS_BASE_URL,
        "LOCAL_BASE_URL": LOCAL_BASE_URL,
        "URL_MODE": URL_MODE,
        "WIDGET_DEBUG": WIDGET_DEBUG,
        "LIVEFORGE_USER_ID": active_id,
        "PROFILE_ID": active_id,
        "PUBLIC_WIDGET_ID": public_widget_id,
        "ACTIVE_RUNTIME_ID": identity.get("active_runtime_id"),
        "ACTIVE_BASE_URL": active_base_url(),
        "DISPLAY_BASE_URL": active_base_url(),
        "HOSTED_WIDGETS_BASE_URL": HOSTED_WIDGETS_BASE_URL,
        "API_BASE_URL": active_base_url(),
        "IDENTITY": identity,
        "CAN_GENERATE_PUBLIC_URLS": bool(active_id),
        "ACTIONS_EVENTS_OVERLAY_URLS": {
            str(screen): get_public_url("/actions_events/overlay", {"screen": screen})
            for screen in range(1, MAX_ACTION_SCREENS + 1)
        },
        "ACTIONS_EVENTS_CONTROL_DOCK_URL": get_public_url("/actions-events/control-dock"),
        "SOCKET_URL": get_socket_url("/socket.io"),
        "FEATURE_URLS": {
            "actions_events_overlay": get_public_url("/actions_events/overlay"),
            "actions_events_control_dock": get_public_url("/actions-events/control-dock"),
            "quiz_display": get_public_url("/quiz_display"),
            "quiz_controls": get_public_url("/quiz_controls"),
            "quiz_leaderboard": get_public_url("/leaderboard"),
            "chess_display": get_public_url("/chess/display"),
            "chess_controls": get_public_url("/chess/controls"),
            "chess_leaderboard": get_public_url("/chess/leaderboard"),
            "genre_wheel_widget": get_public_url("/genre_wheel/widget"),
            "genre_wheel_control": get_public_url("/genre_wheel/control"),
            "timer_widget": get_public_url("/timer/widget"),
            "timer_control": get_public_url("/timer/control"),
            "chat_overlay_widget": get_public_url("/chat-overlay/widget"),
            "forge_widget": get_public_url("/forge/widget"),
            "throne_widget": get_public_url("/throne/widget"),
        },
    }
