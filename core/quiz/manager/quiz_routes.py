"""
core/server/routes/quiz_routes.py — Quiz Management Routes

Handles:
- Quiz control (start, stop, pause, resume, skip)
- Quiz loading (CSV upload)
- State management (get state, snapshot)
- Timer endpoints
- Force refresh for displays
- Debug endpoints

Ensures quiz tab works properly with all control functions!
"""

import json
import logging
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

from core.quiz.manager.quiz_state_machine import QuizState
from core.server.routes.bridge_routes import QM, CSV

# Get the correct base path for PyInstaller
if getattr(sys, 'frozen', False):
    # Running as compiled exe - use _MEIPASS for bundled resources
    BASE_PATH = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    # Running as script
    BASE_PATH = os.getcwd()

logger = logging.getLogger(__name__)


def _serve_quiz_html(filename: str):
    """Serve a core/quiz/html page from disk (dev) or the embedded bundle.

    Release builds (Nuitka/PyInstaller) ship no loose frontend files -- the HTML
    lives in core.resources.web_assets_bundle. Reading only from disk made the
    scoped /u/<public_widget_id>/{quiz_controls,leaderboard,...} pages 404 with
    "<name>.html not found" in the packaged app.
    """
    from fastapi import HTTPException
    file_path = os.path.join(BASE_PATH, "core", "quiz", "html", filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="text/html")
    try:
        from core.utils.embedded_web_assets import embedded_asset_response
        response = embedded_asset_response(f"core/quiz/html/{filename}", "text/html")
        if response is not None:
            return response
    except Exception as exc:
        logger.warning("embedded html lookup failed for %s: %s", filename, exc)
    raise HTTPException(status_code=404, detail=f"{filename} not found")


def _write_settings_save_error(payload, exc):
    """Persist full diagnostics for Quiz Settings save failures."""
    try:
        log_path = Path(BASE_PATH) / "logs" / "settings_save.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- {datetime.now(UTC).isoformat()} ---\n")
            fh.write("Payload:\n")
            fh.write(json.dumps(payload, indent=2, default=str) if payload is not None else "<unread>\n")
            fh.write("\nTraceback:\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            fh.write("\n")
    except Exception:
        logger.error("Failed to write settings_save.log", exc_info=True)


def register_quiz_routes(app: FastAPI, server):
    from fastapi.routing import APIRoute
    from core.server.public_widget_routes import add_public_widget_aliases
    route_start = len(app.routes)
    """Register all quiz management routes"""

    # Service helpers (imported from main bridge_routes)

    def _frontend_state_for(qm) -> str:
        raw_state = str(qm.state.state.value).upper()
        state_map = {
            "RUNNING": "QUESTION_ACTIVE",
            "PAUSED": "PAUSED",
            "IDLE": "IDLE",
            "LOADING": "IDLE",
            "COMPLETED": "ENDED",
            "STOPPED": "ENDED",
            "ERROR": "ENDED",
        }
        return state_map.get(raw_state, "IDLE")

    def _sync_frontend_quiz_state(qm):
        current_state = qm.get_current_state()
        frontend_state = _frontend_state_for(qm)
        server._update_snapshot("quiz_state", {
            "state": frontend_state,
            "paused": frontend_state == "PAUSED",
            "quiz_loaded": qm.total_question_count > 0,
            "question_number": current_state.get("question_number"),
            "total_questions": current_state.get("total_questions"),
            "current_question": current_state.get("current_question"),
        })
        return frontend_state


    def _overlay_theme_path():
        path = Path(BASE_PATH) / "data" / "overlay_theme.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _default_overlay_theme():
        return {
            "version": 1,
            "quiz": {"layout": "studio", "scale": 1, "colors": {"accent": "#7c5cff", "accent2": "#5b8cff", "text": "#eef0f6", "panel": {"background": "#10111a", "opacity": 0.72, "blur": 10}}, "font": {"family": "Inter", "weight": 700, "size": 18}, "radius": 18, "animation": {"style": "fade", "speed": 0.25}, "elements": {"showVotePercent": True, "showVoteBars": True, "highlightCorrect": True}},
            "leaderboard": {"layout": "list", "scale": 1, "colors": {"accent": "#7c5cff", "accent2": "#5b8cff", "text": "#eef0f6", "panel": {"background": "#10111a", "opacity": 0.78, "blur": 8}}, "font": {"family": "Inter", "weight": 700, "size": 14}, "radius": 16, "animation": {"style": "slide", "speed": 0.25}, "elements": {"showAvatars": True, "rowsVisible": 5}},
            "timer": {"layout": "ring", "scale": 1, "colors": {"accent": "#7c5cff", "accent2": "#5b8cff", "text": "#eef0f6", "panel": {"background": "#08090e", "opacity": 0.55, "blur": 6}}, "font": {"family": "JetBrains Mono", "weight": 700, "size": 28}, "radius": 999, "animation": {"style": "fade", "speed": 0.25}, "elements": {"showLabel": True}},
        }


    def _sanitize_overlay_theme(theme):
        sanitized = _default_overlay_theme()
        if isinstance(theme, dict):
            for key in ("quiz", "leaderboard", "timer"):
                if isinstance(theme.get(key), dict):
                    sanitized[key].update(theme[key])
                sanitized[key].pop("position", None)
        sanitized["version"] = 1
        return sanitized

    @app.get("/api/overlay-theme")
    async def get_overlay_theme():
        path = _overlay_theme_path()
        if not path.exists():
            return JSONResponse({"success": True, "theme": _default_overlay_theme(), "source": "defaults"})
        try:
            theme = _sanitize_overlay_theme(json.loads(path.read_text(encoding="utf-8")))
            path.write_text(json.dumps(theme, indent=2), encoding="utf-8")
            return JSONResponse({"success": True, "theme": theme, "source": "file"})
        except Exception as exc:
            logger.warning("overlay_theme_load_failed: %s", exc)
            return JSONResponse({"success": True, "theme": _default_overlay_theme(), "source": "defaults"})

    @app.post("/api/overlay-theme")
    async def save_overlay_theme(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"success": False, "error": "Theme payload must be an object"}, status_code=400)
        body = _sanitize_overlay_theme(body)
        _overlay_theme_path().write_text(json.dumps(body, indent=2), encoding="utf-8")
        try:
            server.emit_signal_ws("overlay_theme_updated", {"theme": body})
        except Exception as exc:
            logger.warning("overlay_theme_broadcast_failed: %s", exc)
        return JSONResponse({"success": True, "theme": body})

    # ============================================================
    # CONFIGURATION ROUTES (NEW)
    # ============================================================

    @app.get("/api/config/all")
    async def get_all_config():
        """
        Get all configuration settings in nested format.
        Returns: {"QUIZ": {"timer_duration": "10", ...}, "AUDIO": {...}, ...}
        """
        try:
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse({
                    "success": False,
                    "error": "Configuration manager not available"
                }, status_code=500)

            # Build nested config dict
            config_data = {}

            for section in cm.config.sections():
                config_data[section] = dict(cm.config.items(section))

            logger.info(f"✅ Retrieved configuration: {len(config_data)} sections")

            return JSONResponse({
                "success": True,
                "config": config_data,
                "timestamp": datetime.now(UTC).isoformat()
            })

        except Exception as e:
            logger.error(f"❌ Failed to get configuration: {e}", exc_info=True)
            return JSONResponse({
                "success": False,
                "error": str(e)
            }, status_code=500)

    @app.post("/api/config/save")
    async def save_config(request: Request):
        """
        Save configuration settings from frontend.

        Expected format:
        {
            "settings": {
                "QUIZ.timer_duration": "15",
                "QUIZ.answer_display_time": "5",
                "AUDIO.master_volume": "80",
                ...
            }
        }

        The keys use dot notation (SECTION.option) which we split and apply.
        """
        try:
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse({
                    "success": False,
                    "error": "Configuration manager not available"
                }, status_code=500)

            data = await request.json()
            payload_for_log = data
            raw_settings = data.get("settings", data) if isinstance(data, dict) else {}

            if not raw_settings:
                logger.warning("⚠️ No settings provided in request")
                return JSONResponse({
                    "success": False,
                    "error": "No settings provided"
                }, status_code=400)

            # Process each setting into the nested model ConfigManager persists.
            updates_count = 0
            errors = []
            nested_updates = {}

            for key, value in raw_settings.items():
                if isinstance(value, dict):
                    nested_updates.setdefault(key, {}).update(value)
                    updates_count += len(value)
                    continue
                if '.' not in key:
                    logger.warning(f"⚠️ Invalid setting key format: {key}")
                    errors.append(f"Invalid key format: {key}")
                    continue

                section, option = key.split('.', 1)
                nested_updates.setdefault(section, {})[option] = value
                updates_count += 1

            # Save to disk once so every UI setting persists atomically.
            if nested_updates and not cm.update_full_config(nested_updates):
                logger.error("❌ Failed to save config file")
                return JSONResponse({
                    "success": False,
                    "error": "Failed to save configuration",
                    "updates_applied": updates_count
                }, status_code=500)

            logger.info(f"💾 Configuration saved: {updates_count} settings updated")

            # Emit signal to notify other clients
            try:
                server.emit_signal_ws("config_updated", {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "updates_count": updates_count,
                    "settings": nested_updates
                })
                server.emit_signal_ws("settings_changed", nested_updates)
            except Exception as e:
                logger.warning(f"⚠️ Failed to emit config_updated signal: {e}")

            response = {
                "success": True,
                "updates_count": updates_count,
                "timestamp": datetime.now(UTC).isoformat()
            }

            if errors:
                response["errors"] = errors
                response["partial_success"] = True

            return JSONResponse(response)

        except Exception as e:
            _write_settings_save_error(payload_for_log, e)
            logger.error(f"❌ Failed to save configuration: {e}", exc_info=True)
            return JSONResponse({
                "success": False,
                "error": str(e)
            }, status_code=500)

    # ============================================================
    # STATE MANAGEMENT
    # ============================================================

    @app.get("/api/state/full")
    async def get_full_state():
        """Get complete application state (quiz, config, timer, connections)"""
        try:
            qm = QM()
            cm = getattr(server, "config_manager", None)

            # Get real state from QuizManager
            real_state = qm.state.state.value

            current_q = qm.get_current_question()
            qnum, total = qm.get_question_progress()

            # Build state dict
            state_data = {
                "state": real_state,
                "quiz_loaded": total > 0,
                "paused": (real_state == QuizState.PAUSED.value),
                "current_question": current_q,
                "question_index": qnum,
                "total_questions": total,
                "reset_version": server._reset_version,
            }

            # Config data
            config_data = {}
            if cm:
                for section in cm.config.sections():
                    config_data[section] = dict(cm.config.items(section))

            # Connected clients
            with server._clients_lock:
                client_count = len(server.connected_clients)

            # Timer state
            timer_state = dict(server._timer_state)
            if timer_state.get("running") and timer_state.get("started_at"):
                elapsed = time.time() - timer_state["started_at"]
                timer_state["remaining"] = max(0, timer_state["total"] - elapsed)

            return JSONResponse({
                "success": True,
                "state": state_data,
                "config": config_data,
                "timer": timer_state,
                "connection": {
                    "connected_clients": client_count,
                    "room": server.LOCKED_ROOM,
                    "protocol_version": server._protocol_version,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            })

        except Exception as e:
            logger.error(f"❌ Failed to get full state: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/state")
    async def get_state():
        """Get current quiz state (simplified)"""
        try:
            qm = QM()

            # Use the real state machine, normalized for frontend controls
            state = _frontend_state_for(qm)

            return JSONResponse({
                "success": True,
                "state": state,
                "quiz_loaded": qm.total_question_count > 0,
                "paused": (state == "PAUSED"),
                "current_question": qm.get_current_question(),
                "timestamp": datetime.now(UTC).isoformat()
            })

        except Exception as e:
            logger.error(f"❌ Failed to get state: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/snapshot")
    async def get_snapshot():
        """Get current snapshot of quiz state"""
        return JSONResponse({
            "success": True,
            "snapshot": dict(server.snapshot),
            "reset_version": server._reset_version,
            "timestamp": datetime.now(UTC).isoformat()
        })

    # ============================================================
    # QUIZ CONTROL
    # ============================================================

    @app.post("/api/quiz/start")
    async def quiz_start():
        """Start the quiz"""
        try:
            qm = QM()

            # Check current state
            current_state = qm.state.state

            if qm.total_question_count == 0:
                raise HTTPException(400, "No quiz loaded")

            if current_state not in {QuizState.IDLE, QuizState.COMPLETED, QuizState.STOPPED}:
                raise HTTPException(400, "Quiz cannot be started from current state")

            # Start quiz
            if not qm.start_quiz():
                raise HTTPException(400, "Failed to start quiz")
            server.sync_snapshot_from_quiz(qm)

            frontend_state = _sync_frontend_quiz_state(qm)

            logger.info(f"✅ Quiz started in state: {frontend_state}")
            return JSONResponse({"success": True})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to start quiz: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/quiz/stop")
    async def quiz_stop():
        """Stop the quiz"""
        try:
            qm = QM()
            if not qm.stop_quiz():
                raise HTTPException(400, "Cannot stop quiz right now")

            frontend_state = _sync_frontend_quiz_state(qm)

            logger.info(f"✅ Quiz stopped in state: {frontend_state}")
            return JSONResponse({"success": True})

        except Exception as e:
            logger.error(f"❌ Failed to stop quiz: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/quiz/pause")
    async def quiz_pause():
        """Pause the quiz with proper state synchronization"""
        try:
            qm = QM()
            if not qm.pause_quiz():
                raise HTTPException(400, "Cannot pause quiz right now")

            frontend_state = _sync_frontend_quiz_state(qm)

            logger.info(f"✅ Quiz paused successfully to state: {frontend_state}")
            return JSONResponse({"success": True, "state": frontend_state})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to pause quiz: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/quiz/resume")
    async def quiz_resume():
        """Resume the quiz with proper state synchronization"""
        try:
            qm = QM()
            if not qm.resume_quiz():
                raise HTTPException(400, "Cannot resume quiz right now")

            frontend_state = _sync_frontend_quiz_state(qm)

            logger.info(f"✅ Quiz resumed successfully to state: {frontend_state}")
            return JSONResponse({"success": True, "state": frontend_state})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to resume quiz: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/quiz/skip")
    async def quiz_skip():
        """Skip to next question"""
        try:
            qm = QM()

            if qm.state.state != QuizState.RUNNING:
                raise HTTPException(400, "Skip is only allowed while a question is active")

            if not qm.skip_question():
                raise HTTPException(400, "Cannot skip question right now")

            _sync_frontend_quiz_state(qm)

            logger.info("✅ Question skipped")
            return JSONResponse({"success": True})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to skip question: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # QUIZ LOADING - FIXED WITH BETTER ERROR HANDLING
    # ============================================================

    @app.post("/api/quiz/load")
    async def quiz_load(request: Request):
        """Load quiz from CSV data"""
        tmp_path = None

        try:
            # Parse JSON body
            try:
                data = await request.json()
                logger.info("✅ Received JSON data")
            except Exception as e:
                logger.error(f"❌ Failed to parse JSON: {e}")
                raise HTTPException(400, f"Invalid JSON body: {e}")

            # Extract CSV text
            csv_text = data.get("csv_text")
            logger.info(f"📄 CSV text length: {len(csv_text) if csv_text else 0}")

            if not csv_text:
                logger.error("❌ No csv_text in request")
                raise HTTPException(400, "Missing 'csv_text' field in request")

            if not isinstance(csv_text, str):
                logger.error(f"❌ csv_text is not string: {type(csv_text)}")
                raise HTTPException(400, "csv_text must be a string")

            if not csv_text.strip():
                logger.error("❌ csv_text is empty after strip")
                raise HTTPException(400, "csv_text is empty")

            # Write to temp file
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", encoding="utf-8")
                tmp_path = tmp.name
                tmp.write(csv_text)
                tmp.close()
                logger.info(f"✅ Wrote temp file: {tmp_path}")
            except Exception as e:
                logger.error(f"❌ Failed to write temp file: {e}")
                raise HTTPException(500, f"Failed to create temporary CSV file: {e}")

            # Load CSV
            try:
                csv_handler = CSV()
                if not csv_handler:
                    logger.error("❌ CSV handler is None")
                    raise HTTPException(500, "CSV handler not available")

                logger.info(f"📊 Loading CSV from: {tmp_path}")
                result = csv_handler.load_file(tmp_path)

                if isinstance(result, tuple):
                    success, error = result
                else:
                    success, error = result, None

                if not success:
                    logger.error(f"❌ CSV load failed: {error}")
                    raise HTTPException(500, error or "CSV parse failed")

                logger.info("✅ CSV file loaded successfully")

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"❌ Exception loading CSV: {e}", exc_info=True)
                raise HTTPException(500, f"Error loading CSV: {e}")

            # Get quiz data
            quiz_data = csv_handler.quiz_data or []
            logger.info(f"📊 Quiz data: {len(quiz_data)} questions")

            if not quiz_data:
                logger.error("❌ No questions in quiz_data")
                raise HTTPException(500, "No valid questions found in CSV")

            # Load into QuizManager
            try:
                qm = QM()
                if not qm:
                    logger.error("❌ QuizManager is None")
                    raise HTTPException(500, "QuizManager not available")

                logger.info(f"🎯 Setting quiz data ({len(quiz_data)} questions)...")
                if not qm.set_quiz_data(quiz_data):
                    logger.error("❌ QuizManager rejected quiz data")
                    raise HTTPException(500, "QuizManager rejected quiz data")

                logger.info("✅ Quiz data set in QuizManager")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"❌ Exception setting quiz data: {e}", exc_info=True)
                raise HTTPException(500, f"Error setting quiz data: {e}")

            # Sync and emit
            server.sync_snapshot_from_quiz(qm)
            server.emit_signal_ws("quiz_data_loaded", len(quiz_data))
            server.emit_signal_ws("state_changed", _frontend_state_for(qm))

            logger.info(f"🎉 Quiz loaded successfully: {len(quiz_data)} questions")
            return JSONResponse({"success": True, "questions": len(quiz_data)})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error in quiz_load: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        finally:
            # Cleanup temporary CSV
            if tmp_path:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                        logger.debug(f"🗑️ Removed temp file: {tmp_path}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to remove temp file: {e}")

    # ============================================================
    # TIMER ENDPOINTS
    # ============================================================

    @app.get("/api/quiz/timer/duration")
    async def timer_duration():
        """Get timer duration from ConfigManager (single source of truth)"""
        try:
            # Get ConfigManager
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse(
                    {"success": False, "error": "ConfigManager unavailable"},
                    status_code=503
                )

            # Read from config file - try both keys for compatibility
            value = None

            if cm.config.has_option("TIMER", "duration"):
                value = cm.config.getint("TIMER", "duration")
                logger.info(f"📤 Timer duration from TIMER.duration: {value}s")
            elif cm.config.has_option("TIMER", "timer_duration"):
                value = cm.config.getint("TIMER", "timer_duration")
                logger.info(f"📤 Timer duration from TIMER.timer_duration: {value}s")

            if value is None:
                logger.warning("⚠️ No timer duration found in config, using default 30s")
                value = 30

            return JSONResponse({"success": True, "duration": value})

        except Exception as e:
            logger.error(f"❌ Failed to get timer duration: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/quiz/timer/duration")
    async def set_timer_duration(request: Request):
        """Set timer duration in ConfigManager"""
        try:
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse(
                    {"success": False, "error": "ConfigManager unavailable"},
                    status_code=503
                )

            data = await request.json()
            new_duration = data.get("duration")

            if new_duration is None or not isinstance(new_duration, (int, float)):
                raise HTTPException(400, "Invalid duration value")

            # Ensure TIMER section exists
            if not cm.config.has_section("TIMER"):
                cm.config.add_section("TIMER")

            # Set the value
            cm.config.set("TIMER", "duration", str(int(new_duration)))

            # Save config
            cm.save_config()

            # Emit update signal
            server.emit_signal_ws("timer_duration_changed", int(new_duration))

            logger.info(f"✅ Timer duration updated to {new_duration}s")
            return JSONResponse({"success": True, "duration": int(new_duration)})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to set timer duration: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # FORCE REFRESH ENDPOINTS
    # ============================================================

    @app.post("/api/quiz/force_refresh")
    async def force_refresh():
        """Force all displays to refresh their state"""
        try:
            server.emit_signal_ws("force_refresh", {
                "timestamp": datetime.now(UTC).isoformat(),
                "reason": "manual_refresh"
            })

            logger.info("✅ Force refresh signal sent")
            return JSONResponse({"success": True})

        except Exception as e:
            logger.error(f"❌ Failed to force refresh: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # SETTINGS ENDPOINTS
    # ============================================================

    @app.get("/api/settings")
    async def get_settings():
        """Get all quiz settings"""
        try:
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse(
                    {"success": False, "error": "ConfigManager unavailable"},
                    status_code=503
                )

            # Get all settings
            settings = {}
            for section in cm.config.sections():
                settings[section] = {}
                for key, value in cm.config.items(section):
                    settings[section][key] = value

            logger.info("📤 Settings retrieved")
            return JSONResponse({"success": True, "settings": settings})

        except Exception as e:
            logger.error(f"❌ Failed to get settings: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/settings")
    async def update_settings(request: Request):
        """Update quiz settings"""
        try:
            cm = getattr(server, "config_manager", None)

            if not cm:
                logger.error("❌ ConfigManager not available")
                return JSONResponse(
                    {"success": False, "error": "ConfigManager unavailable"},
                    status_code=503
                )

            data = await request.json()

            # Update settings
            success = cm.update_full_config(data)

            if success:
                # Emit settings changed signal
                server.emit_signal_ws("settings_changed", data)
                logger.info("✅ Settings updated and broadcasted")
                return JSONResponse({"success": True})
            else:
                logger.error("❌ Failed to update settings")
                return JSONResponse(
                    {"success": False, "error": "Failed to save settings"},
                    status_code=500
                )

        except Exception as e:
            logger.error(f"❌ Failed to update settings: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # DEBUG ENDPOINTS
    # ============================================================
    @app.post("/api/quiz/force_refresh_display")
    async def force_refresh_display():
        """Force all displays to refresh with current question data

        This endpoint:
        1. Gets the current question from QuizManager
        2. Broadcasts it via WebSocket to all connected displays
        3. Returns success/failure
        """
        try:
            qm = QM()

            # Get current quiz state
            state = qm.state.state.value
            current_q = qm.get_current_question()

            if not current_q:
                logger.warning("⚠️ Force refresh requested but no active question")
                return JSONResponse({
                    "success": False,
                    "error": "No active question to display"
                })

            # Log the refresh action
            logger.info(f"🔄 Force refresh display - Question: {current_q.get('question', '')[:50]}...")

            # Broadcast question_changed signal to all displays via WebSocket
            server.emit_signal_ws("question_changed", current_q)

            logger.info("✅ Force refresh signal broadcasted successfully")

            return JSONResponse({
                "success": True,
                "question_preview": current_q.get('question', '')[:100],
                "question_number": current_q.get('number', 0),
                "timestamp": datetime.now(UTC).isoformat()
            })

        except Exception as e:
            logger.error(f"❌ Failed to force refresh display: {e}", exc_info=True)
            return JSONResponse({
                "success": False,
                "error": str(e)
            }, status_code=500)

    @app.get("/api/debug/display_state")
    async def get_display_state():
        """Debug endpoint to check current display state"""
        try:
            qm = QM()
            if not qm:
                return JSONResponse({
                    "error": "QuizManager not available"
                }, status_code=500)

            state = qm.get_current_state()
            current_q = qm.get_current_question()
            q_num, q_total = qm.get_question_progress()

            return JSONResponse({
                "state": state.get("state"),
                "question_number": q_num,
                "total_questions": q_total,
                "has_current_question": bool(current_q),
                "current_question": current_q,
                "current_question_text": current_q.get("question", "")[:100] if current_q else None,
                "quiz_loaded": qm.total_question_count > 0,
                "timer_running": qm.timer.running if hasattr(qm, 'timer') else None
            })

        except Exception as e:
            logger.error(f"❌ Debug failed: {e}", exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ============================================================
    # STATIC FILE ROUTES (CSS & JS)
    # ============================================================

    @app.get("/core/quiz/css/quiz_display.css")
    async def serve_quiz_display_css():
        """Serve quiz display CSS file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "css", "quiz_display.css")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="CSS file not found")
        return FileResponse(file_path, media_type="text/css")

    @app.get("/core/quiz/js/quiz_controls.js")
    async def serve_quiz_controls_js():
        """Serve quiz controls JavaScript file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "quiz_controls.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="JavaScript file not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/js/quiz_display.js")
    async def serve_quiz_display_js():
        """Serve quiz display JavaScript file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "quiz_display.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="JavaScript file not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/js/quiz_signals.js")
    async def serve_quiz_signals_js():
        """Serve quiz signals JavaScript file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "quiz_signals.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="JavaScript file not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/js/quiz_tab.js")
    async def serve_quiz_tab_js():
        """Serve quiz tab JavaScript file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "quiz_tab.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="JavaScript file not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/js/overlay_theme.js")
    async def serve_overlay_theme_js():
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "overlay_theme.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="overlay_theme.js not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/js/leaderboard.js")
    async def serve_leaderboard_js():
        """Serve leaderboard JavaScript file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "js", "leaderboard.js")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="JavaScript file not found")
        return FileResponse(file_path, media_type="application/javascript")

    @app.get("/core/quiz/css/controls.css")
    async def serve_controls_css():
        """Serve quiz controls CSS file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "css", "controls.css")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="CSS file not found")
        return FileResponse(file_path, media_type="text/css")

    @app.get("/core/quiz/css/overlay_theme.css")
    async def serve_overlay_theme_css():
        file_path = os.path.join(BASE_PATH, "core", "quiz", "css", "overlay_theme.css")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="overlay_theme.css not found")
        return FileResponse(file_path, media_type="text/css")

    @app.get("/core/quiz/html/controls.html")
    async def serve_controls_html():
        """Serve quiz controls HTML file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "html", "controls.html")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="HTML file not found")
        return FileResponse(file_path, media_type="text/html")

    @app.get("/core/quiz/html/display.html")
    async def serve_display_html():
        """Serve quiz display HTML file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "html", "display.html")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="HTML file not found")
        return FileResponse(file_path, media_type="text/html")

    @app.get("/core/quiz/html/quiz_tab.html")
    async def serve_quiz_tab_html():
        """Serve quiz tab HTML file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "html", "quiz_tab.html")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="HTML file not found")
        return FileResponse(file_path, media_type="text/html")

    @app.get("/core/quiz/html/leaderboard.html")
    async def serve_leaderboard_html():
        """Serve leaderboard HTML file"""
        file_path = os.path.join(BASE_PATH, "core", "quiz", "html", "leaderboard.html")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="HTML file not found")
        return FileResponse(file_path, media_type="text/html")

    logger.info("✅ Quiz routes configured")

    # ============================================================
    # QUIZ PAGE ROUTES (URLs without .html)
    # ============================================================

    @app.get("/quiz_display")
    async def quiz_display_page():
        """Serve quiz display page"""
        return _serve_quiz_html("display.html")

    @app.get("/quiz_controls")
    async def quiz_controls_page():
        """Serve quiz controls page"""
        return _serve_quiz_html("controls.html")

    @app.get("/leaderboard")
    async def leaderboard_page():
        """Serve the OBS leaderboard browser-source page at the stable URL.

        Always serves the overlay, consistent with quiz_display/quiz_controls/
        timer_display. The leaderboard previously redirected bare /leaderboard to
        the overlay studio, which also hijacked the official per-user URL
        (/u/<public_widget_id>/leaderboard) and showed the studio instead of the
        overlay. The studio is reachable directly at /overlay-studio.
        """
        return _serve_quiz_html("leaderboard.html")

    @app.get("/timer_display")
    async def timer_display_page():
        """Serve standalone timer browser-source page"""
        return _serve_quiz_html("timer_display.html")

    quiz_routes = [route for route in app.routes[route_start:] if isinstance(route, APIRoute)]
    add_public_widget_aliases(
        app,
        quiz_routes,
        include=lambda path: path in {"/quiz_display", "/quiz_controls", "/leaderboard", "/timer_display"} or path.startswith("/api/"),
        feature="quiz",
    )
