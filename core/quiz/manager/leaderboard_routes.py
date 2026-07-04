"""
core/server/routes/leaderboard_routes.py — Leaderboard Management Routes

Handles:
- Leaderboard updates (from quiz manager or external sources)
- Leaderboard reset
"""

import logging
from datetime import datetime, UTC

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_leaderboard_routes(app: FastAPI, server):
    from fastapi.routing import APIRoute
    from core.server.public_widget_routes import add_public_widget_aliases
    route_start = len(app.routes)
    """Register leaderboard management routes"""

    # ============================================================
    # LEADERBOARD UPDATE
    # ============================================================

    @app.post("/api/leaderboard/update")
    async def leaderboard_update(request: Request):
        """Update leaderboard with new entries"""
        try:
            data = await request.json()
            entries = data.get("entries", [])

            # Normalize entries
            normalized = []
            for i, e in enumerate(entries, start=1):
                user_id = str(e.get("user_id") or f"user_{i}")[:64]
                username = (e.get("username") or f"User {i}")[:80]

                normalized.append({
                    "user_id": user_id,
                    "username": username,
                    "score": int(e.get("score", 0)),
                    "correct": int(e.get("correct", 0)),
                    "incorrect": int(e.get("incorrect", 0)),
                    "streak": int(e.get("streak", 0)),
                    "avatar_url": e.get("avatar_url"),
                })

            # Update server snapshot
            server._update_snapshot("leaderboard", normalized)

            # Emit signal to all connected clients
            server.emit_signal_ws("leaderboard_updated", {
                "version": server._reset_version,
                "entries": normalized,
                "timestamp": datetime.now(UTC).isoformat(),
            })

            logger.info(f"✅ Leaderboard updated: {len(normalized)} entries")
            return JSONResponse({"success": True, "entries": len(normalized)})

        except Exception as e:
            logger.error(f"❌ Failed to update leaderboard: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # LEADERBOARD RESET
    # ============================================================

    @app.post("/api/leaderboard/reset")
    async def leaderboard_reset():
        """Reset leaderboard (clear all entries)"""
        try:
            version = server._reset_leaderboard_atomically()

            logger.info(f"✅ Leaderboard reset (version: {version})")
            return JSONResponse({"success": True, "reset_version": version})

        except Exception as e:
            logger.error(f"❌ Failed to reset leaderboard: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    logger.info("✅ Leaderboard routes configured")

    leaderboard_routes = [route for route in app.routes[route_start:] if isinstance(route, APIRoute)]
    add_public_widget_aliases(
        app,
        leaderboard_routes,
        include=lambda path: path.startswith("/api/leaderboard/"),
        feature="quiz_leaderboard",
    )
