"""HTTP endpoints for owner-created widget sessions and one-time control exchange."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from core.server.widget_sessions import SessionAuthorizationError, WidgetSessionStore
from core.services.auth_service import AuthService


def _authenticated_owner(authorization: str | None) -> tuple[str, str]:
    session = AuthService.get_instance().current_session
    profile = AuthService.get_instance().current_profile
    expected = session.access_token if session else ""
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    owner_user_id = getattr(profile, "id", None) or ((session.user or {}).get("id") if session else None)
    public_widget_id = getattr(profile, "public_widget_id", None) or ((session.user or {}).get("public_widget_id") if session else None)
    if not expected or not supplied or not secrets.compare_digest(supplied, expected) or not owner_user_id or not public_widget_id:
        raise HTTPException(status_code=401, detail="Authenticated QuizMaster owner required")
    return str(owner_user_id), str(public_widget_id)


def register_widget_session_routes(app: FastAPI, server) -> None:
    @app.post("/api/widget-sessions/{widget_type}")
    async def create_widget_session(widget_type: str, authorization: str | None = Header(default=None)):
        if widget_type not in {"quiz", "chess"}:
            raise HTTPException(status_code=404, detail="Unknown widget type")
        owner_user_id, public_widget_id = _authenticated_owner(authorization)
        session = WidgetSessionStore.get_instance().create_session(widget_type, owner_user_id, public_widget_id)
        exchange = WidgetSessionStore.get_instance().issue_control_exchange(widget_type, session.session_id, owner_user_id)
        return JSONResponse({
            "success": True,
            "session": session.public_view(),
            "room_type": widget_type,
            "control_exchange": exchange,
        })

    @app.post("/u/{public_widget_id}/api/widget-sessions/control/exchange")
    async def exchange_control(public_widget_id: str, request: Request):
        body = await request.json()
        try:
            token, session = WidgetSessionStore.get_instance().exchange_control_code(str(body.get("code") or ""))
            if session.public_widget_id != public_widget_id:
                raise SessionAuthorizationError("Control exchange public widget mismatch")
            return JSONResponse({
                "success": True,
                "control_token": token,
                "session_id": session.session_id,
                "widget_type": session.widget_type,
                "expires_in": 15 * 60,
            })
        except SessionAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/u/{public_widget_id}/api/widget-sessions/{widget_type}/{session_id}/snapshot")
    async def public_snapshot(public_widget_id: str, widget_type: str, session_id: str):
        try:
            session = WidgetSessionStore.get_instance().resolve_public(widget_type, public_widget_id, session_id)
            return JSONResponse({"success": True, **session.public_view()})
        except SessionAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
