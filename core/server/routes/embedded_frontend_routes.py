"""Override frontend file routes so release installs do not expose HTML/CSS/JS.

The normal route modules are kept for development, but release builds generate
an embedded asset bundle. These routes remove the filesystem-backed frontend
routes and serve the same URLs from memory.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from core.utils.embedded_web_assets import embedded_asset_response


EXACT_FRONTEND_ROUTES: dict[str, tuple[str, str]] = {
    "/core/quiz/css/quiz_display.css": ("core/quiz/css/quiz_display.css", "text/css"),
    "/core/quiz/css/controls.css": ("core/quiz/css/controls.css", "text/css"),
    "/core/quiz/css/overlay_theme.css": ("core/quiz/css/overlay_theme.css", "text/css"),
    "/core/quiz/js/quiz_controls.js": ("core/quiz/js/quiz_controls.js", "application/javascript"),
    "/core/quiz/js/quiz_display.js": ("core/quiz/js/quiz_display.js", "application/javascript"),
    "/core/quiz/js/quiz_signals.js": ("core/quiz/js/quiz_signals.js", "application/javascript"),
    "/core/quiz/js/quiz_tab.js": ("core/quiz/js/quiz_tab.js", "application/javascript"),
    "/core/quiz/js/overlay_theme.js": ("core/quiz/js/overlay_theme.js", "application/javascript"),
    "/core/quiz/js/leaderboard.js": ("core/quiz/js/leaderboard.js", "application/javascript"),
    "/core/quiz/html/controls.html": ("core/quiz/html/controls.html", "text/html"),
    "/core/quiz/html/display.html": ("core/quiz/html/display.html", "text/html"),
    "/core/quiz/html/quiz_tab.html": ("core/quiz/html/quiz_tab.html", "text/html"),
    "/core/quiz/html/leaderboard.html": ("core/quiz/html/leaderboard.html", "text/html"),
    "/quiz_display": ("core/quiz/html/display.html", "text/html"),
    "/quiz_controls": ("core/quiz/html/controls.html", "text/html"),
    "/timer_display": ("core/quiz/html/timer_display.html", "text/html"),
}


def _drop_frontend_routes(app: FastAPI) -> None:
    paths_to_drop = set(EXACT_FRONTEND_ROUTES) | {"/leaderboard"}
    app.router.routes = [
        route for route in app.router.routes
        if not (isinstance(route, APIRoute) and getattr(route, "path", None) in paths_to_drop)
    ]


def _asset_or_404(relative_path: str, media_type: str):
    response = embedded_asset_response(relative_path, media_type)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Embedded asset not found: {relative_path}")
    return response


def register_embedded_frontend_routes(app: FastAPI) -> None:
    _drop_frontend_routes(app)

    for route_path, (asset_path, media_type) in EXACT_FRONTEND_ROUTES.items():
        async def handler(asset_path=asset_path, media_type=media_type):
            return _asset_or_404(asset_path, media_type)

        app.add_api_route(route_path, handler, methods=["GET"], include_in_schema=False)

    @app.get("/leaderboard", include_in_schema=False)
    async def leaderboard_page(request: Request):
        if request.query_params.get("obs") != "true":
            return RedirectResponse(url="/overlay-studio", status_code=307)
        return _asset_or_404("core/quiz/html/leaderboard.html", "text/html")

    @app.get("/core/assets/{asset_path:path}", include_in_schema=False)
    async def core_assets(asset_path: str):
        # media_type=None -> use the bundle's stored MIME (e.g. image/png for the
        # logo) instead of forcing application/octet-stream.
        return _asset_or_404(f"core/assets/{asset_path}", None)

    @app.get("/static/{asset_path:path}", include_in_schema=False)
    async def static_assets(asset_path: str):
        return _asset_or_404(f"core/server/static/{asset_path}", "application/octet-stream")

    @app.get("/themes/{asset_path:path}", include_in_schema=False)
    async def theme_assets(asset_path: str):
        return _asset_or_404(f"core/server/themes/{asset_path}", "application/octet-stream")

    @app.get("/overlays/{asset_path:path}", include_in_schema=False)
    async def overlay_assets(asset_path: str):
        return _asset_or_404(f"core/server/overlays/{asset_path}", "application/octet-stream")
