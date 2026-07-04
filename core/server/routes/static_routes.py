"""
core/server/routes/static_routes.py — QuizMaster Static File Routes

In release builds, frontend files are served from the generated embedded asset
bundle instead of loose Program Files HTML/CSS/JS folders.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from core.utils.embedded_web_assets import embedded_asset_response, get_embedded_asset_text

logger = logging.getLogger(__name__)

QUIZ_TEMPLATE_CSV = "\n".join([
    "question,answer_a,answer_b,answer_c,answer_d,correct_answer,type,difficulty,category",
    "What color is the sky?,Blue,Green,Red,Yellow,a,multiple_choice,easy,general",
    "2 + 2 = ?,3,4,5,6,b,multiple_choice,easy,math",
]) + "\n"


def register_static_routes(app: FastAPI, server):
    """Register all static file routes."""

    html_dir = server.HTML_DIR
    js_dir = server.JS_DIR
    static_dir = server.BASE_DIR / "static"
    css_dir = static_dir / "css"

    def _serve(relative_path: str, fallback_path: Path, media_type: str | None = None, missing_text: str | None = None):
        embedded = embedded_asset_response(relative_path, media_type)
        if embedded is not None:
            return embedded
        if fallback_path.exists():
            return FileResponse(fallback_path, media_type=media_type)
        if missing_text is not None:
            return Response(missing_text, media_type=media_type)
        raise HTTPException(404, f"{relative_path} not found")

    def _html(name: str, inject_script: str | None = None):
        rel = f"core/server/static/html/{name}"
        text = get_embedded_asset_text(rel)
        path = html_dir / name
        if text is None:
            if not path.exists():
                raise HTTPException(404, f"{name} not found")
            text = path.read_text(encoding="utf-8")
        if inject_script:
            tag = f'<script src="{inject_script}"></script>'
            if tag not in text:
                text = text.replace("</body>", f"{tag}\n</body>")
        return Response(text, media_type="text/html")

    # Development-only filesystem mounts. In release builds these folders are not
    # bundled as loose files; matching routes below serve from the embedded bundle.
    omq = server.BASE_DIR / "overlays" / "openmicquiz"
    if omq.exists():
        app.mount("/openmicquiz", StaticFiles(directory=str(omq)), name="openmicquiz")
        themes_path = omq / "themes"
        app.mount("/overlays/openmicquiz/themes", StaticFiles(directory=str(themes_path)), name="overlays-openmicquiz-themes")
        logger.info("✓ Mounted OpenMicQuiz overlay from filesystem")

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    themes_dir = server.BASE_DIR / "themes"
    if themes_dir.exists():
        app.mount("/themes", StaticFiles(directory=str(themes_dir)), name="themes")
        logger.info("✓ Themes mounted from: %s", themes_dir)

    @app.get("/global.css")
    async def css_global():
        return _serve("core/server/static/css/global.css", css_dir / "global.css", "text/css", "/* global.css not found */")

    @app.get("/consolidated.css")
    async def css_consolidated():
        return _serve("core/server/static/css/consolidated.css", css_dir / "consolidated.css", "text/css", "/* consolidated.css missing */")

    @app.get("/bootstrap.js")
    async def js_bootstrap():
        return _serve("core/server/static/js/bootstrap.js", js_dir / "bootstrap.js", "application/javascript", "// bootstrap.js missing")

    @app.get("/service_locator.js")
    async def js_service_locator():
        return _serve("core/server/static/js/service_locator.js", js_dir / "service_locator.js", "application/javascript", "// service_locator.js missing")

    @app.get("/bridge_client.js")
    async def js_bridge_client():
        return _serve("core/server/static/js/bridge_client.js", js_dir / "bridge_client.js", "application/javascript", "// bridge_client.js missing")

    @app.get("/theme_manager.js")
    async def js_theme_manager():
        return _serve("core/server/static/js/theme_manager.js", js_dir / "theme_manager.js", "application/javascript", "// theme_manager.js missing")

    @app.get("/tiktok_tab.js")
    async def js_tiktok_tab():
        return _serve("core/server/static/js/tiktok_tab.js", js_dir / "tiktok_tab.js", "application/javascript", "// tiktok_tab.js missing")

    @app.get("/main_tab.js")
    async def js_main_tab():
        return _serve("core/server/static/js/main_tab.js", js_dir / "main_tab.js", "application/javascript", "// main_tab.js missing")

    @app.get("/account.js")
    async def js_account():
        return _serve("core/server/static/js/account.js", js_dir / "account.js", "application/javascript", "// account.js missing")

    @app.post("/api/quiz/template/save")
    async def save_quiz_template():
        try:
            from core.display.file_dialog_bridge import select_save_file_path

            result = await run_in_threadpool(
                select_save_file_path,
                "Save Quiz Template",
                "quiz_template.csv",
                "CSV files (*.csv);;All files (*.*)",
            )
            if result.get("cancelled") or not result.get("path"):
                return JSONResponse({"success": True, "cancelled": True})

            target = Path(result["path"]).expanduser()
            if target.suffix == "":
                target = target.with_suffix(".csv")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(QUIZ_TEMPLATE_CSV, encoding="utf-8", newline="")
            return JSONResponse({"success": True, "cancelled": False, "path": str(target)})
        except Exception as exc:
            logger.error("Failed to save quiz template: %s", exc, exc_info=True)
            return JSONResponse({"success": False, "error": "Could not save quiz template."}, status_code=500)

    @app.get("/")
    async def root():
        return _html("main_window.html")

    @app.get("/main_window.html")
    async def main_window():
        return _html("main_window.html")

    @app.get("/main_tab.html")
    async def main_tab():
        return _html("main_tab.html")

    @app.get("/tiktok_tab.html")
    async def tiktok_tab():
        return _html("tiktok_tab.html")

    @app.get("/quiz_tab.html")
    async def quiz_tab():
        return _html("quiz_tab.html", "/quiz_template_download.js")

    @app.get("/overlay-studio")
    async def overlay_studio():
        return _html("overlay_studio.html")

    @app.get("/overlay_studio.html")
    async def overlay_studio_html():
        return _html("overlay_studio.html")

    @app.get("/settings")
    async def settings():
        return _html("settings.html")

    @app.get("/settings.html")
    async def settings_html():
        return _html("settings.html")

    @app.get("/account")
    async def account():
        return _html("account.html")

    @app.get("/account.html")
    async def account_html():
        return _html("account.html")

    @app.get("/favicon.ico")
    async def favicon():
        embedded = embedded_asset_response("core/assets/images/icon.ico", "image/x-icon")
        if embedded is not None:
            return embedded
        path = server.BASE_DIR / "assets" / "images" / "icon.ico"
        return FileResponse(path) if path.exists() else Response(status_code=204)

    @app.get("/{filename}.{ext}")
    async def wildcard(filename: str, ext: str):
        """Fallback route for serving static files."""
        name = f"{filename}.{ext}"
        if ext == "js":
            return _serve(f"core/server/static/js/{name}", js_dir / name, "application/javascript")
        if ext == "css":
            return _serve(f"core/server/static/css/{name}", css_dir / name, "text/css")

        embedded = embedded_asset_response(f"core/assets/{name}") or embedded_asset_response(f"core/server/static/{name}")
        if embedded is not None:
            return embedded

        f = server.BASE_DIR / "assets" / name
        if f.exists():
            return FileResponse(f)
        f = static_dir / name
        if f.exists():
            return FileResponse(f)

        raise HTTPException(404, f"{name} not found")

    logger.info("✅ Static routes configured")
