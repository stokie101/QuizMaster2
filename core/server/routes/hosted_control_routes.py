"""Endpoints that hand the app's own pages a ready-to-copy hosted dock URL.

The hosted control docks run in OBS, where none of the desktop app's cookies or
headers exist, so their URL must carry the account's control token in its
fragment. The app pages ask for the finished URL here rather than assembling one
themselves, so that every copy button, settings page and OBS helper shows the
same authorized URL.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from core.server.url_config import CONTROL_DOCK_PATHS, control_dock_url
from core.services.hosted_bridge import hosted_bridge_status
from core.services.hosted_control import HostedControlTokens

logger = logging.getLogger(__name__)

PATH_FOR_WIDGET = {widget: path for path, widget in CONTROL_DOCK_PATHS.items()}


def register_hosted_control_routes(app: FastAPI, server) -> None:
    @app.get("/api/hosted/control-docks")
    async def hosted_control_docks():
        """Return one authorized dock URL per hosted control surface."""
        docks = {}
        tokens = {}
        errors = {}
        service = HostedControlTokens.get_instance()
        for widget_type, path in PATH_FOR_WIDGET.items():
            url = ""
            try:
                url = control_dock_url(path)
            except Exception as exc:  # a signed-out app must still render
                logger.debug("hosted_control_dock_url_failed widget=%s error=%s", widget_type, exc)
            status = service.status(widget_type)
            docks[widget_type] = url
            # The raw token as well as the finished URL, so a page that builds a
            # control URL with its own query string can still authorize it.
            tokens[widget_type] = service.cached_token(widget_type, status.get("public_widget_id") or "")
            if not status.get("has_token"):
                errors[widget_type] = status.get("error") or "control_token_unavailable"
        # A dock URL is only half of it: the app also has to be attached to the
        # widget host's command channel, or every button on that dock does
        # nothing. Report both together so the app can say which half is missing.
        return JSONResponse({
            "success": True,
            "docks": docks,
            "tokens": tokens,
            "errors": errors,
            "bridge": hosted_bridge_status(),
        })

    @app.post("/api/hosted/control-docks/refresh")
    async def refresh_hosted_control_docks():
        """Re-mint every dock token, e.g. after signing in on a new account."""
        tokens = HostedControlTokens.get_instance()
        tokens.clear()
        docks = {}
        for widget_type, path in PATH_FOR_WIDGET.items():
            tokens.token(widget_type, refresh=True)
            try:
                docks[widget_type] = control_dock_url(path, mint=False)
            except Exception:
                docks[widget_type] = ""
        return JSONResponse({"success": True, "docks": docks})

    @app.get("/api/hosted/control-docks/{widget_type}")
    async def hosted_control_dock(widget_type: str):
        path = PATH_FOR_WIDGET.get(widget_type)
        if not path:
            raise HTTPException(status_code=404, detail="Unknown control dock")
        url = control_dock_url(path)
        status = HostedControlTokens.get_instance().status(widget_type)
        return JSONResponse({
            "success": bool(url and status.get("has_token")),
            "widget_type": widget_type,
            "url": url,
            "error": None if status.get("has_token") else (status.get("error") or "control_token_unavailable"),
        })
