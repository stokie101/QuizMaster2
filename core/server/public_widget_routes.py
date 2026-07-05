"""Helpers for exposing desktop widget routes through account-scoped public paths."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Request
from fastapi.routing import APIRoute

from core.server.session_identity import validate_profile_or_warn
from core.server.widget_sessions import SessionAuthorizationError, WidgetSessionStore

logger = logging.getLogger(__name__)
_SESSION_FEATURES = {"quiz"}


def _validate_public_widget_id(public_widget_id: str) -> str:
    try:
        return validate_profile_or_warn(public_widget_id, route="public_widget_route")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _write_state_rejection(request: Request, reason: str) -> None:
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "state.log").open("a", encoding="utf-8") as handle:
            handle.write(
                f"{datetime.now(UTC).isoformat()} status=400 path={request.url.path} "
                f"method={request.method} reason={reason} query={dict(request.query_params)}\n"
            )
    except Exception:
        logger.debug("Could not write state rejection log", exc_info=True)


def _optional_session_id(request: Request) -> str:
    """Read an optional ``?session=`` id. Per-user widgets are scoped by
    ``public_widget_id`` alone, so a session id is never required."""
    return str(request.query_params.get("session") or "").strip()


def _public_session_dependency(feature: str):
    def validate(request: Request, public_widget_id: str):
        # Everything is linked by the account's public_widget_id. The desktop
        # serves a single signed-in user, so the id in the path fully identifies
        # the owner and their live state -- no session id needed. If a caller
        # still supplies one, honour it for stricter isolation.
        _validate_public_widget_id(public_widget_id)
        session_id = _optional_session_id(request)
        if not session_id:
            return None
        try:
            return WidgetSessionStore.get_instance().resolve_public(feature, public_widget_id, session_id)
        except SessionAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return validate




def _public_projection(feature: str) -> dict:
    """Read the current feature state and remove private control-only fields."""
    from core.services.service_locator import ServiceLocator
    locator = ServiceLocator.get_instance()
    try:
        manager = locator.get_service("QuizManager") if hasattr(locator, "get_service") else locator.get("QuizManager")
    except (ValueError, RuntimeError, AttributeError):
        manager = None
    state = manager.get_current_state() if manager else {}
    if manager and getattr(manager, "leaderboard_manager", None):
        leaderboard = getattr(manager.leaderboard_manager, "leaderboard_data", None)
        if leaderboard is not None:
            state = {**state, "leaderboard": leaderboard}

    private_keys = {
        "correct_answer", "correctAnswer", "answer_key", "admin_settings",
        "control_token", "access_token", "refresh_token", "authorization",
    }

    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items() if key not in private_keys}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    return sanitize(dict(state or {}))


def _persist_control_snapshot(feature: str, public_widget_id: str, request: Request, session=None) -> None:
    snapshot = _public_projection(feature)
    version = None
    if session is not None:
        try:
            updated = WidgetSessionStore.get_instance().update_snapshot(
                feature, session.session_id, session.owner_user_id, snapshot,
                public_snapshot=snapshot, event_type=request.url.path.rsplit("/", 1)[-1] or "command",
            )
            version = updated.version
        except Exception as exc:
            logger.warning("scoped_widget_snapshot_persist_failed feature=%s error=%s", feature, exc)
    try:
        from core.services.service_locator import ServiceLocator
        locator = ServiceLocator.get_instance()
        server = locator.get_service("Server") if hasattr(locator, "get_service") else locator.get("Server")
        if server and hasattr(server, "emit_to_room"):
            payload = {"snapshot": snapshot}
            if version is not None:
                payload["version"] = version
            # The account room is keyed by public_widget_id -- the same room the
            # display widgets join, so no session id is needed to reach them.
            server.emit_to_room("snapshot", payload, f"profile:{public_widget_id}")
    except Exception as exc:
        logger.warning("scoped_widget_emit_failed feature=%s error=%s", feature, exc)


def _control_session_dependency(feature: str):
    def validate(
        request: Request,
        public_widget_id: str,
        x_quizmaster_control_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        # The public_widget_id in the path is the credential: this confirms it
        # matches the signed-in QuizMaster user on this desktop (and the tunnel
        # only reaches that machine), which authorizes the owner. No session id
        # or separate token is required for controls.
        _validate_public_widget_id(public_widget_id)
        store = WidgetSessionStore.get_instance()
        session_id = _optional_session_id(request)
        session = None
        try:
            if session_id:
                session = store.resolve_public(feature, public_widget_id, session_id)
                if x_quizmaster_control_token:
                    store.authorize_control(feature, session_id, x_quizmaster_control_token)
                else:
                    from core.server.widget_session_routes import _authenticated_owner
                    owner_user_id, _ = _authenticated_owner(authorization)
                    store.authorize_owner(feature, session.session_id, owner_user_id)
            else:
                # Sessionless control: the validated public_widget_id above is
                # the credential, so the matching signed-in owner is already
                # authorized. An access token is still honoured if one is sent,
                # but it is never required and never accepted in a URL.
                if authorization:
                    from core.server.widget_session_routes import _authenticated_owner
                    _authenticated_owner(authorization)
            yield session
            _persist_control_snapshot(feature, public_widget_id, request, session)
        except HTTPException:
            raise
        except SessionAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return validate


def add_public_widget_aliases(
    app,
    routes: list[APIRoute],
    *,
    include: Callable[[str], bool],
    feature: str,
) -> None:
    """Mirror routes below ``/u/{public_widget_id}`` with scoped authorization."""
    existing = {(route.path, tuple(sorted(route.methods or ()))) for route in app.routes if isinstance(route, APIRoute)}
    for route in routes:
        if not include(route.path):
            continue
        alias = f"/u/{{public_widget_id}}{route.path}"
        methods = list(route.methods or ["GET"])
        key = (alias, tuple(sorted(methods)))
        if key in existing:
            continue

        dependencies = [Depends(_validate_public_widget_id)]
        if feature in _SESSION_FEATURES:
            dependencies = [Depends(_public_session_dependency(feature))]
            if any(method.upper() not in {"GET", "HEAD", "OPTIONS"} for method in methods):
                dependencies.append(Depends(_control_session_dependency(feature)))

        app.add_api_route(
            alias,
            route.endpoint,
            methods=methods,
            name=f"public_{feature}_{route.name}",
            response_model=route.response_model,
            status_code=route.status_code,
            response_class=route.response_class,
            dependencies=dependencies,
            include_in_schema=False,
        )
        existing.add(key)
        logger.debug("Registered public %s route: %s", feature, alias)
