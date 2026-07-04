"""
core/server/routes/client_routes.py — Client Management Routes

Handles:
- Client information (get connected clients)
- Signal emission (manual signal triggering)
- Force refresh endpoint
"""

import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_client_routes(app: FastAPI, server):
    """Register client management and signal routes"""

    # ============================================================
    # CLIENT INFORMATION
    # ============================================================

    @app.get("/api/clients/info")
    async def clients_info():
        """Get information about connected clients"""
        try:
            return JSONResponse({
                "success": True,
                "clients": server.get_connected_clients_info(),
            })
        except Exception as e:
            logger.error(f"❌ Failed to get client info: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # SIGNAL EMISSION
    # ============================================================

    @app.post("/api/signals/emit")
    async def emit_signal(request: Request):
        """
        Emit a custom WebSocket signal
        
        Body:
        {
            "signal_name": "custom_event",
            "args": [arg1, arg2, ...]
        }
        """
        try:
            data = await request.json()
            name = data.get("signal_name")
            args = data.get("args", [])

            if not name:
                raise HTTPException(400, "signal_name required")

            server.emit_signal_ws(name, *args)

            logger.info(f"✅ Signal emitted: {name}")
            return JSONResponse({"success": True, "signal": name})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to emit signal: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/force_refresh")
    async def force_refresh():
        """Force refresh all connected clients"""
        try:
            server.emit_signal_ws("force_refresh")

            logger.info("✅ Force refresh emitted")
            return JSONResponse({"success": True})

        except Exception as e:
            logger.error(f"❌ Failed to force refresh: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    logger.info("✅ Client routes configured")
