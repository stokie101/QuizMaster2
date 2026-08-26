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

from core.utils.embedded_web_assets import embedded_asset_response, has_embedded_assets
from core.utils.resource_loader import get_resource_path


_app_root: Path | None = None


def _app_root_dir() -> Path:
    """Application root: the source tree in development, the app dir when frozen."""
    global _app_root
    if _app_root is None:
        try:
            _app_root = get_resource_path("core/assets").resolve().parent.parent
        except Exception:
            _app_root = Path(__file__).resolve().parents[3]
    return _app_root


def _loose_asset_file(relative_path: str) -> Path | None:
    """Resolve an app-relative asset path to a file on disk, if one is there.

    Development runs have no generated bundle, so every embedded route below
    would 404 without this fallback.
    """
    root = _app_root_dir()
    candidate = (root / relative_path).resolve()
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
    "/leaderboard": ("core/quiz/html/leaderboard.html", "text/html"),
    "/timer_display": ("core/quiz/html/timer_display.html", "text/html"),
}

SCOPED_WIDGET_ROUTES: dict[str, tuple[str, str]] = {
    "/quiz_display": ("core/quiz/html/display.html", "text/html"),
    "/leaderboard": ("core/quiz/html/leaderboard.html", "text/html"),
    "/timer_display": ("core/quiz/html/timer_display.html", "text/html"),
    "/quiz_controls": ("core/quiz/html/controls.html", "text/html"),
}


def _drop_frontend_routes(app: FastAPI) -> None:
    """Remove earlier filesystem-backed frontend routes before adding embedded ones.

    quiz_routes.py registers both plain routes (/quiz_display) and public-widget
    aliases (/u/{public_widget_id}/quiz_display). If the aliases are not removed,
    FastAPI matches those older disk-backed handlers before the embedded handlers
    added below, so Nuitka release builds can still return "*.html not found" for
    hosted/public widget URLs even though the asset is present in web_assets_bundle.

    Only release builds have a bundle to serve from, so only they drop routes.
    """
    if not has_embedded_assets():
        # Development run: there is no bundle to serve from, so the on-disk
        # routes registered earlier are the only ones that can return the file.
        return

    paths_to_drop = set(EXACT_FRONTEND_ROUTES)
    for route_path in SCOPED_WIDGET_ROUTES:
        paths_to_drop.add(f"/u/{{public_widget_id}}{route_path}")
        paths_to_drop.add(f"/u/{{profile_id}}{route_path}")

    app.router.routes = [
        route for route in app.router.routes
        if not (isinstance(route, APIRoute) and getattr(route, "path", None) in paths_to_drop)
    ]


def _repo_file(relative_path: str) -> Path | None:
    """Locate a frontend file on disk, for runs without a generated bundle."""
    for base in (_project_root(), Path.cwd()):
        candidate = (base / relative_path).resolve()
        if candidate.is_file():
            return candidate
    return None


def _asset_or_404(relative_path: str, media_type: str | None):
    response = embedded_asset_response(relative_path, media_type)
    if response is not None:
        return response
    loose = _loose_asset_file(relative_path)
    if loose is not None:
        return FileResponse(str(loose), media_type=media_type) if media_type else FileResponse(str(loose))
    raise HTTPException(status_code=404, detail=f"Asset not found: {relative_path}")


def register_embedded_frontend_routes(app: FastAPI) -> None:
    _drop_frontend_routes(app)

    for route_path, (asset_path, media_type) in EXACT_FRONTEND_ROUTES.items():
        async def handler(asset_path=asset_path, media_type=media_type):
            return _asset_or_404(asset_path, media_type)

        app.add_api_route(route_path, handler, methods=["GET"], include_in_schema=False)

    for route_path, (asset_path, media_type) in SCOPED_WIDGET_ROUTES.items():
        async def scoped_handler(asset_path=asset_path, media_type=media_type):
            return _asset_or_404(asset_path, media_type)

        app.add_api_route(f"/u/{{profile_id}}{route_path}", scoped_handler, methods=["GET"], include_in_schema=False)

    @app.get("/core/assets/{asset_path:path}", include_in_schema=False)
    async def core_assets(asset_path: str):
        # Branding images ship loose so they stay swappable, so disk wins here.
        relative = f"core/assets/{asset_path}"
        loose = _loose_asset_file(relative)
        if loose is not None:
            return FileResponse(str(loose))
        return _asset_or_404(relative, None)

    # Pass no media type so each asset keeps the one recorded in the bundle.
    # Forcing application/octet-stream here made release builds serve bundled
    # JS and CSS as opaque downloads, which browsers refuse to run or apply.
    @app.get("/static/{asset_path:path}", include_in_schema=False)
    async def static_assets(asset_path: str):
        return _asset_or_404(f"core/server/static/{asset_path}", None)

    @app.get("/themes/{asset_path:path}", include_in_schema=False)
    async def theme_assets(asset_path: str):
        return _asset_or_404(f"core/server/themes/{asset_path}", None)

    @app.get("/overlays/{asset_path:path}", include_in_schema=False)
    async def overlay_assets(asset_path: str):
        return _asset_or_404(f"core/server/overlays/{asset_path}", None)
