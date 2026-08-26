"""
core/server/routes/tiktok_routes.py — TikTok Integration Routes (ENHANCED)

Handles:
- TikTok connection/disconnection
- TikTok status monitoring
- Username persistence (Robust: checks [TikTokLive] AND [TikTok])
- Status polling thread
- HIGH-VOLUME METRICS & CONFIGURATION (NEW)
"""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse

logger = logging.getLogger(__name__)


def _sanitize_log_id(value: str) -> str:
    """Keep IDs readable for diagnostics without allowing log injection."""
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:128]


def register_tiktok_routes(app: FastAPI, server):
    """Register TikTok integration routes"""

    # Get TikTok manager instance
    from core.tiktok.tiktok_live_manager import TikTokLiveManager
    tiktok_manager = TikTokLiveManager.get_instance()

    # ============================================================
    # CONNECTION MANAGEMENT
    # ============================================================


    @app.post("/api/tiktok/connect")
    async def tiktok_connect(request: Request):
        """Connect to TikTok Live for a specific username"""
        try:
            data = await request.json()
            username = data.get("username")

            if not username:
                raise HTTPException(status_code=400, detail="Username required")

            logger.info(f"🔵 TikTok connect request for: @{username}")

            # Start connection
            ok = tiktok_manager.connect_to_user(username)

            logger.info(f"{'✅' if ok else '❌'} TikTok connection {'started' if ok else 'failed'}: @{username}")
            return JSONResponse({
                "success": ok,
                "message": f"Connecting to @{username}" if ok else "Failed to start connection"
            })

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ TikTok connect error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/disconnect")
    async def tiktok_disconnect():
        """Disconnect from TikTok Live"""
        try:
            logger.info("🔵 TikTok disconnect request")
            tiktok_manager.disconnect()

            logger.info("✅ TikTok disconnected")
            return JSONResponse({"success": True, "message": "Disconnected"})

        except Exception as e:
            logger.error(f"❌ TikTok disconnect error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # STATUS MONITORING
    # ============================================================

    @app.get("/api/tiktok/status")
    async def tiktok_status():
        """Get current TikTok connection status"""
        try:
            return JSONResponse({
                "success": True,
                "connected": tiktok_manager.is_connected(),
                "username": tiktok_manager.get_current_username(),
                # The page polls this, so a failure that happened while no
                # socket was listening is still explainable to the user.
                "last_error": tiktok_manager.get_last_error(),
                "debug": tiktok_manager.get_debug_info()
            })

        except Exception as e:
            logger.error(f"❌ TikTok status error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # USERNAME PERSISTENCE - ROBUST FIX
    # ============================================================

    def _save_username(username: str) -> None:
        """Persist the username as the TikTokLive source account."""
        normalized = str(username or "").strip().lstrip("@")
        if not normalized:
            return
        config = getattr(server, "config_manager", None)
        if not config:
            return
        try:
            if hasattr(config, "config") and not config.config.has_section("TikTokLive"):
                config.config.add_section("TikTokLive")
            saved = False
            if hasattr(config, "set"):
                saved = bool(config.set("TikTokLive", "last_username", normalized))
            if not saved and hasattr(config, "save_config"):
                config.save_config()
            logger.info("tiktok_username_saved username=%s", _sanitize_log_id(normalized))
        except Exception as exc:
            logger.warning("tiktok_username_save_failed error=%s", exc)

    @app.get("/api/tiktok/username")
    async def get_tiktok_username():
        """Get the last TikTok username this app connected to."""
        try:
            config = getattr(server, "config_manager", None)
            if not config:
                return JSONResponse({"success": False, "error": "ConfigManager not available"}, status_code=503)

            # DEBUG: Log exact path being read
            if hasattr(config, "config_path"):
                logger.info(f"📂 Reading config from: {config.config_path}")

            username = ""

            # 1. Try NEW section [TikTokLive]
            try:
                if hasattr(config, 'get'):
                    username = config.get("TikTokLive", "last_username", fallback="")
            except Exception:
                pass

            # 2. If empty, try OLD section [TikTok]
            if not username:
                try:
                    logger.info("⚠️ [TikTokLive] empty, checking legacy [TikTok] section...")
                    username = config.get("TikTok", "last_username", fallback="")
                    if username:
                        logger.info(f"✅ Found legacy username: {username}")
                except Exception:
                    pass

            # 3. Direct config object fallback (last resort)
            if not username and hasattr(config, 'config'):
                try:
                    if config.config.has_option("TikTokLive", "last_username"):
                        username = config.config.get("TikTokLive", "last_username")
                    elif config.config.has_option("TikTok", "last_username"):
                        username = config.config.get("TikTok", "last_username")
                except Exception:
                    pass

            logger.info(f"✅ Returning username: '{username}'")
            return JSONResponse({"success": True, "username": username, "source": "legacy_saved_username" if username else "unavailable"})

        except Exception as e:
            logger.error(f"❌ Get TikTok username error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/username")
    async def set_tiktok_username(request: Request):
        """Save TikTok username to config (Standardizes on [TikTokLive])"""
        try:
            data = await request.json()
            username = str(data.get("username", "")).strip()

            if not username:
                raise HTTPException(status_code=400, detail="Username cannot be empty")

            _save_username(username)
            try:
                from core.server.session_identity import RuntimeSessionIdentity
                from core.tiktok.profile_stats import TikTokProfileStatsService
                TikTokProfileStatsService.get_instance().refresh_in_background(
                    RuntimeSessionIdentity.profile_id(), username, force=True,
                )
            except Exception as exc:
                logger.debug("TikTok profile stats refresh could not start: %s", exc)
            return JSONResponse({"success": True, "username": username})

        except Exception as e:
            logger.error(f"❌ Set TikTok username error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @app.get("/api/tiktok/profile-stats")
    @app.get("/api/tiktok/profile_stats")
    @app.get("/u/{public_widget_id}/api/tiktok/profile-stats")
    async def get_profile_stats(public_widget_id: str | None = None):
        """Return follower stats for the connected TikTok username."""
        from core.server.session_identity import RuntimeSessionIdentity, validate_profile_or_warn
        from core.tiktok.profile_stats import TikTokProfileStatsService

        if public_widget_id is not None:
            try:
                validate_profile_or_warn(public_widget_id, route="tiktok_profile_stats")
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc))

        profile_id = RuntimeSessionIdentity.profile_id()
        username = tiktok_manager.get_current_username()
        if not username and getattr(server, "config_manager", None):
            username = server.config_manager.get("TikTokLive", "last_username", fallback="")
        service = TikTokProfileStatsService.get_instance()
        cached = service.get_cached(profile_id, username)
        if username:
            service.refresh_in_background(profile_id, username)
        return JSONResponse(cached)

    # ============================================================
    # HIGH-VOLUME METRICS & MONITORING (NEW)
    # ============================================================

    @app.get("/api/tiktok/metrics")
    async def get_metrics():
        """Get real-time event processing metrics"""
        try:
            # Check if high-volume manager is available
            if not hasattr(tiktok_manager, 'event_manager'):
                return JSONResponse({
                    "success": False,
                    "error": "High-volume event manager not available"
                }, status_code=503)

            metrics = tiktok_manager.event_manager.get_metrics()

            # Add system metrics if rate limiter available
            system_metrics = {}
            if hasattr(tiktok_manager, 'rate_limiter'):
                try:
                    status = tiktok_manager.rate_limiter.get_status()
                    system_metrics = status.get('metrics', {})
                except Exception as e:
                    logger.debug(f"Could not get rate limiter status: {e}")

            return JSONResponse({
                "success": True,
                "metrics": metrics,
                "system": system_metrics
            })

        except Exception as e:
            logger.error(f"❌ Get metrics error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/rate_limits")
    async def set_rate_limits(request: Request):
        """Update rate limits for event processing"""
        try:
            data = await request.json()

            if not hasattr(tiktok_manager, 'event_manager'):
                return JSONResponse({
                    "success": False,
                    "error": "High-volume event manager not available"
                }, status_code=503)

            # Validate limits
            valid_keys = ['comment', 'gift', 'viewer_count', 'follow']
            limits = {}

            for key in valid_keys:
                if key in data:
                    try:
                        value = int(data[key])
                        if value < 1 or value > 1000:
                            raise ValueError(f"Limit must be between 1 and 1000")
                        limits[key] = value
                    except (ValueError, TypeError) as e:
                        return JSONResponse({
                            "success": False,
                            "error": f"Invalid value for {key}: {e}"
                        }, status_code=400)

            if not limits:
                return JSONResponse({
                    "success": False,
                    "error": "No valid limits provided"
                }, status_code=400)

            # Update limits
            tiktok_manager.event_manager.update_rate_limits(limits)

            logger.info(f"✅ Rate limits updated: {limits}")
            return JSONResponse({
                "success": True,
                "limits": limits,
                "message": "Rate limits updated successfully"
            })

        except Exception as e:
            logger.error(f"❌ Set rate limits error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/adaptive")
    async def toggle_adaptive(request: Request):
        """Enable/disable adaptive rate limiting"""
        try:
            data = await request.json()
            enabled = data.get("enabled", False)

            if not hasattr(tiktok_manager, 'rate_limiter'):
                return JSONResponse({
                    "success": False,
                    "error": "Adaptive rate limiter not available"
                }, status_code=503)

            # Store adaptive state
            tiktok_manager._adaptive_enabled = enabled

            logger.info(f"✅ Adaptive rate limiting: {'enabled' if enabled else 'disabled'}")
            return JSONResponse({
                "success": True,
                "enabled": enabled,
                "message": f"Adaptive rate limiting {'enabled' if enabled else 'disabled'}"
            })

        except Exception as e:
            logger.error(f"❌ Toggle adaptive error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/tiktok/config")
    async def get_config():
        """Get current high-volume configuration"""
        try:
            config = {}

            if hasattr(tiktok_manager, 'event_manager'):
                config['rate_limits'] = tiktok_manager.event_manager.rate_limits
                config['batch_windows'] = tiktok_manager.event_manager.batch_windows
                config['sampling_rates'] = tiktok_manager.event_manager.sampling_rates
                config['max_queue_size'] = tiktok_manager.event_manager.max_queue_size

            if hasattr(tiktok_manager, 'rate_limiter'):
                status = tiktok_manager.rate_limiter.get_status()
                config['current_limits'] = status.get('current_limits', {})
                config['base_limits'] = status.get('base_limits', {})

            config['adaptive_enabled'] = getattr(tiktok_manager, '_adaptive_enabled', False)

            return JSONResponse({
                "success": True,
                "config": config
            })

        except Exception as e:
            logger.error(f"❌ Get config error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/reset_metrics")
    async def reset_metrics():
        """Reset event processing metrics"""
        try:
            if not hasattr(tiktok_manager, 'event_manager'):
                return JSONResponse({
                    "success": False,
                    "error": "High-volume event manager not available"
                }, status_code=503)

            tiktok_manager.event_manager.reset_metrics()

            logger.info("✅ Metrics reset")
            return JSONResponse({
                "success": True,
                "message": "Metrics reset successfully"
            })

        except Exception as e:
            logger.error(f"❌ Reset metrics error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)
        # Add these endpoints to tiktok_routes.py

    @app.post("/api/tiktok/pause")
    async def pause_events():
        """Emergency pause - stop processing all events"""
        try:
            if hasattr(tiktok_manager, 'event_manager'):
                tiktok_manager.event_manager.pause()
                logger.info("⏸️ TikTok events PAUSED")
                return JSONResponse({
                    "success": True,
                    "message": "Events paused - your computer can rest now"
                })
            else:
                return JSONResponse({
                    "success": False,
                    "error": "Event manager not available"
                }, status_code=503)
        except Exception as e:
            logger.error(f"❌ Pause error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/tiktok/resume")
    async def resume_events():
        """Resume event processing"""
        try:
            if hasattr(tiktok_manager, 'event_manager'):
                tiktok_manager.event_manager.resume()
                logger.info("▶️ TikTok events RESUMED")
                return JSONResponse({
                    "success": True,
                    "message": "Events resumed"
                })
            else:
                return JSONResponse({
                    "success": False,
                    "error": "Event manager not available"
                }, status_code=503)
        except Exception as e:
            logger.error(f"❌ Resume error: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ============================================================
    # HIGH-VOLUME MONITORING UI (NEW)
    # ============================================================

    @app.get("/tiktok/monitor")
    async def monitor_page():
        """Serve high-volume monitoring dashboard"""
        try:
            # Look for the HTML file in static directory
            static_dir = Path(__file__).parent.parent / "static" / "html"
            html_file = static_dir / "high_volume_ui.html"

            if html_file.exists():
                return FileResponse(html_file)
            else:
                # Fallback: return simple HTML
                return HTMLResponse("""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>TikTok Monitor</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            background: #1a1a2e;
                            color: #fff;
                            padding: 40px;
                            text-align: center;
                        }
                        .error {
                            background: rgba(239, 68, 68, 0.1);
                            border: 1px solid #ef4444;
                            border-radius: 8px;
                            padding: 20px;
                            max-width: 600px;
                            margin: 0 auto;
                        }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>❌ Monitor UI Not Found</h1>
                        <p>The high-volume monitoring UI file is missing.</p>
                        <p>Expected location: <code>core/server/static/html/high_volume_ui.html</code></p>
                        <p><a href="/api/tiktok/metrics" style="color: #667eea;">View raw metrics</a></p>
                    </div>
                </body>
                </html>
                """)

        except Exception as e:
            logger.error(f"❌ Monitor page error: {e}", exc_info=True)
            return HTMLResponse(f"""
            <!DOCTYPE html>
            <html>
            <head><title>Error</title></head>
            <body style="font-family: Arial; background: #1a1a2e; color: #fff; padding: 40px; text-align: center;">
                <h1>❌ Error Loading Monitor</h1>
                <p>{str(e)}</p>
            </body>
            </html>
            """, status_code=500)

    # ============================================================
    # STATUS POLLING THREAD
    # ============================================================

    def _poll_tiktok_status():
        """Poll TikTok manager for status updates and emit WebSocket signals"""
        last_connected = None

        while True:
            try:
                time.sleep(0.5)
                is_connected = tiktok_manager.is_connected()

                if is_connected != last_connected:
                    username = tiktok_manager.get_current_username()
                    message = f"Connected to @{username}" if is_connected and username else (
                        "Connected" if is_connected else "Disconnected"
                    )
                    state = "connected" if is_connected else "disconnected"
                    emitted = False
                    if hasattr(tiktok_manager, "emit_tiktok_status"):
                        emitted = tiktok_manager.emit_tiktok_status(state, message)
                    if not emitted:
                        server.emit_signal_ws("tiktok_status", {
                            "state": state,
                            "message": message
                        })
                    last_connected = is_connected

            except Exception:
                time.sleep(2)

    # Start polling thread
    threading.Thread(target=_poll_tiktok_status, daemon=True, name="TikTokStatusPoller").start()

    logger.info("✅ TikTok routes configured (Enhanced with High-Volume Monitoring)")
