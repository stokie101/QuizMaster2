"""Override frontend file routes so release installs do not expose HTML/CSS/JS.

The normal route modules are kept for development, but release builds generate
an embedded asset bundle. These routes remove the filesystem-backed frontend
routes and serve the same URLs from memory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from core.utils.embedded_web_assets import embedded_asset_response
from core.utils.resource_loader import get_resource_path


_core_assets_root: Path | None = None


def _core_assets_dir() -> Path:
    global _core_assets_root
    if _core_assets_root is None:
        try:
            _core_assets_root = get_resource_path("core/assets").resolve()
        except Exception:
            _core_assets_root = Path("core/assets").resolve()
    return _core_assets_root


def _loose_asset_file(relative_path: str) -> Path | None:
    if not relative_path.startswith("core/assets/"):
        return None
    root = _core_assets_dir()
    candidate = (root / relative_path[len("core/assets/"):]).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


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

SCOPED_WIDGET_ROUTES: dict[str, tuple[str, str]] = {
    "/quiz_display": ("core/quiz/html/display.html", "text/html"),
    "/leaderboard": ("core/quiz/html/leaderboard.html", "text/html"),
    "/timer_display": ("core/quiz/html/timer_display.html", "text/html"),
    "/quiz_controls": ("core/quiz/html/controls.html", "text/html"),
}


def _drop_frontend_routes(app: FastAPI) -> None:
    paths_to_drop = set(EXACT_FRONTEND_ROUTES) | {"/leaderboard"}
    app.router.routes = [
        route for route in app.router.routes
        if not (isinstance(route, APIRoute) and getattr(route, "path", None) in paths_to_drop)
    ]


def _asset_or_404(relative_path: str, media_type: str | None):
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
    async def leaderboard_page():
        return _asset_or_404("core/quiz/html/leaderboard.html", "text/html")

    for route_path, (asset_path, media_type) in SCOPED_WIDGET_ROUTES.items():
        async def scoped_handler(asset_path=asset_path, media_type=media_type):
            return _asset_or_404(asset_path, media_type)

        app.add_api_route(f"/u/{{profile_id}}{route_path}", scoped_handler, methods=["GET"], include_in_schema=False)

    @app.get("/core/assets/{asset_path:path}", include_in_schema=False)
    async def core_assets(asset_path: str):
        relative = f"core/assets/{asset_path}"
        loose = _loose_asset_file(relative)
        if loose is not None:
            return FileResponse(str(loose))
        return _asset_or_404(relative, None)

    @app.get("/static/{asset_path:path}", include_in_schema=False)
    async def static_assets(asset_path: str):
        return _asset_or_404(f"core/server/static/{asset_path}", "application/octet-stream")

    @app.get("/themes/{asset_path:path}", include_in_schema=False)
    async def theme_assets(asset_path: str):
        return _asset_or_404(f"core/server/themes/{asset_path}", "application/octet-stream")

    @app.get("/overlays/{asset_path:path}", include_in_schema=False)
    async def overlay_assets(asset_path: str):
        return _asset_or_404(f"core/server/overlays/{asset_path}", "application/octet-stream")
