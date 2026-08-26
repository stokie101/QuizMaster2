"""
core/server/routes/settings_routes.py - unified settings routes.
"""

import logging
import os
import sys
from pathlib import Path

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

from core.services.account_service import AccountService
from core.services.identity_resolver import resolve_identity
from core.services.cloud_service import CloudService
from core.services.local_identity import LocalIdentityService

logger = logging.getLogger(__name__)


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    main_py = Path(__file__).resolve().parents[3] / "main.py"
    return f'"{sys.executable}" "{main_py}"'


def _set_windows_startup(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "QuizMaster", 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, "QuizMaster")
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        logger.error("Failed to update Windows startup setting: %s", exc, exc_info=True)
        return False


def _apply_app_settings(data: dict) -> dict:
    result = {}
    section = data.get("APP_SETTINGS") or data.get("app_settings") or {}
    if not isinstance(section, dict):
        return result
    if "start_with_windows" in section:
        result["windows_startup_applied"] = _set_windows_startup(str(section.get("start_with_windows")).lower() in {"true", "1", "yes", "on"})
    return result


def register_settings_routes(app, server):
    """Register unified settings management routes."""

    def get_cm():
        cm = getattr(server, 'config_manager', None)
        if cm:
            return cm

        try:
            from core.services.service_locator import ServiceLocator
            sl = ServiceLocator.get_instance()
            if hasattr(sl, 'get_service'):
                return sl.get_service("ConfigManager")
            elif hasattr(sl, 'get'):
                return sl.get("ConfigManager")
        except Exception:
            pass

        try:
            from config.config_manager import ConfigManager
            return ConfigManager.get_instance()
        except Exception:
            return None

    @app.get("/api/settings")
    async def get_settings():
        try:
            cm = get_cm()
            if not cm:
                return JSONResponse({"success": False, "error": "ConfigManager unavailable"}, status_code=503)
            settings = cm.get_all_config()
            return JSONResponse({"success": True, "settings": settings})
        except Exception as e:
            logger.error("GET settings error: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/api/settings")
    async def save_settings(request: Request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise HTTPException(400, "Invalid format")

            cm = get_cm()
            if not cm:
                return JSONResponse({"success": False, "error": "ConfigManager unavailable"}, status_code=503)

            success = cm.update_full_config(data)
            if not success:
                return JSONResponse({"success": False, "error": "Config write failed"}, status_code=500)

            side_effects = _apply_app_settings(data)

            await server.socketio.emit('settings_changed', data, room="default")
            await server.socketio.emit('chat_overlay:config_updated', data)

            # Mirror through the signal bus as well: it reaches the account
            # widget rooms (the OBS quiz dock lives there), so a change made in
            # the app shows up in the dock and vice versa.
            try:
                server.emit_signal_ws("settings_changed", data)
                server.emit_signal_ws("config_updated", {"settings": data})
            except Exception as exc:
                logger.warning("settings_signal_broadcast_failed: %s", exc)

            if "TIMER" in data:
                if "duration" in data["TIMER"]:
                    await server.socketio.emit('timer_duration_changed', int(data["TIMER"]["duration"]), room="default")
                if "answer_display_time" in data["TIMER"]:
                    await server.socketio.emit('answer_display_time_changed', int(data["TIMER"]["answer_display_time"]), room="default")

            return JSONResponse({"success": True, "side_effects": side_effects})
        except Exception as e:
            logger.error("Save settings error: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/debug/config_raw")
    async def debug_config():
        try:
            cm = get_cm()
            if not cm:
                return JSONResponse({"error": "ConfigManager unavailable"}, status_code=500)

            config_path = str(getattr(cm, 'config_path', 'unknown'))
            all_data = {}
            for section in cm.config.sections():
                all_data[section] = dict(cm.config.items(section))

            raw_content = "Could not read"
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
            except Exception:
                pass

            return JSONResponse({
                "config_path": config_path,
                "sections": list(cm.config.sections()),
                "all_data": all_data,
                "raw_file": raw_content,
                "timer_keys_check": {
                    "duration": all_data.get("TIMER", {}).get("duration", "MISSING"),
                    "timer_duration": all_data.get("TIMER", {}).get("timer_duration", "MISSING"),
                    "match": all_data.get("TIMER", {}).get("duration") == all_data.get("TIMER", {}).get("timer_duration")
                }
            })
        except Exception as e:
            logger.error("Debug config error: %s", e, exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/local_identity/status")
    async def local_identity_status():
        try:
            return JSONResponse({"success": True, "identity": LocalIdentityService().get_status()})
        except Exception as e:
            logger.error("Error reading local identity status: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/account/status")
    async def account_status():
        try:
            account = AccountService().get_status()
            identity = resolve_identity().to_dict()
            account["identity"] = identity
            account["app_mode"] = "local" if not identity.get("authenticated") else "account"
            account["status_message"] = "Not signed in - local mode active" if not identity.get("authenticated") else "Signed in"
            account["account_state"] = {**(account.get("account_state") or {}), **{
                "account_status": identity.get("account_status"),
                "email": identity.get("email"),
                "subscription_tier": str(identity.get("plan") or "Free").lower(),
                "sync_enabled": False,
            }}
            if identity.get("warning") and not account.get("warning"):
                account["warning"] = identity.get("warning")
            return JSONResponse({"success": True, "account": account})
        except Exception as e:
            logger.error("Error reading local account status: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/cloud/status")
    async def cloud_status():
        try:
            return JSONResponse({"success": True, "cloud": CloudService().get_status()})
        except Exception as e:
            logger.error("Error reading local cloud status: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.get("/api/health")
    async def health():
        return JSONResponse({"status": "ok", "success": True})

    logger.info("Settings routes registered")
