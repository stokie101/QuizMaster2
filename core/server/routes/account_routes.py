"""Desktop account/profile API routes for the QuizMaster main window."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.services.auth_service import AuthService, DASHBOARD_URL
from core.services.identity_resolver import resolve_identity

logger = logging.getLogger(__name__)


def register_account_routes(app: FastAPI, server):
    """Register local account UI routes.

    These endpoints expose the active QuizMaster desktop account session to the
    local web UI. They do not sync quiz data, settings, media, or local runtime
    state to the cloud.
    """

    @app.get("/api/account/profile")
    async def account_profile():
        try:
            auth_service = AuthService.get_instance()
            if not getattr(auth_service, "current_session", None):
                auth_service.restore_saved_session()

            identity = resolve_identity().to_dict()
            status = auth_service.get_profile_status()
            profile = status.get("profile") or {}

            if identity.get("authenticated"):
                profile.update({
                    "id": identity.get("auth_user_id"),
                    "email": identity.get("email"),
                    "plan": identity.get("plan") or "Free",
                    "public_widget_id": identity.get("public_widget_id"),
                })
                status["authenticated"] = True
                status["profile"] = profile

            status["identity"] = identity
            status["dashboard_url"] = DASHBOARD_URL
            status["sync_status"] = "Not enabled yet"
            return JSONResponse({"success": True, **status})
        except Exception as exc:
            logger.warning("Account profile request failed: %s", exc, exc_info=True)
            return JSONResponse(
                {
                    "success": False,
                    "authenticated": False,
                    "error": str(exc),
                    "dashboard_url": DASHBOARD_URL,
                    "sync_status": "Not enabled yet",
                },
                status_code=503,
            )

    @app.post("/api/account/logout")
    async def account_logout():
        try:
            AuthService.get_instance().logout()
            return JSONResponse({"success": True})
        except Exception as exc:
            logger.warning("Account logout failed: %s", exc, exc_info=True)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
