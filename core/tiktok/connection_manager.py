import logging
import threading
import time
from typing import Dict, Any

from core.services.service_locator import ServiceLocator


class ConnectionManager:
    _instance = None
    _initialized = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.manager = ServiceLocator.get_instance().get_service("TikTokLiveManager")
        self.service_locator = ServiceLocator.get_instance()
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self.connection_monitor = None
        self._get_connection_monitor()
        self._cleanup_lock = threading.Lock()
        self._is_cleaning_up = False

    # --------------------------------------------------------------
    def _get_connection_monitor(self):
        if self.service_locator.has_service("ConnectionMonitor"):
            self.connection_monitor = self.service_locator.get_service("ConnectionMonitor")

    # --------------------------------------------------------------
    def _emit_debug_message(self, message: str, level: str = "info"):
        """Send debug info or errors to frontend via WebSocket."""
        try:
            bridge = self.service_locator.get_service("HTTPBridgeServer")
            if bridge:
                bridge.emit_signal_ws("tiktok_debug", {
                    "level": level.lower(),
                    "message": message
                })
                self.logger.debug(f"Debug → {level.upper()}: {message}")
        except Exception as e:
            self.logger.warning(f"Failed to send tiktok_debug message: {e}")

    # --------------------------------------------------------------
    def _update_connection_status(self, status: str):
        """Update connection status in both manager state, monitor, and frontend."""
        try:
            # --- Update internal flags ---
            if status == "connected":
                self.manager._is_connected = True
            elif status in ["disconnected", "error", "connecting"]:
                self.manager._is_connected = False

            # --- Update ConnectionMonitor widget (if present) ---
            if self.connection_monitor:
                try:
                    self.connection_monitor.update_status("TikTok", status)
                except Exception as e:
                    self.logger.error(f"Error updating connection monitor: {e}")

            # --- Notify frontend via bridge ---
            try:
                bridge = self.service_locator.get_service("HTTPBridgeServer")
                if bridge:
                    message = f"TikTok {status.capitalize()}"
                    emitted = False
                    if hasattr(self.manager, "emit_tiktok_status"):
                        emitted = self.manager.emit_tiktok_status(status, message, bridge=bridge)
                    else:
                        bridge.emit_signal_ws("tiktok_status", {
                            "state": status,
                            "message": message
                        })
                        emitted = True

                    if emitted:
                        self.logger.info(f"Emitted TikTok status to frontend: {message}")
            except Exception as e:
                self.logger.warning(f"Could not emit TikTok status signal: {e}")

        except Exception as e:
            self.logger.error(f"Failed to update internal connection status: {e}")

    # --------------------------------------------------------------
    def emit_signal(self, signal_name: str, *args):
        """Unified signal emission method (safe, logs on failure)."""
        try:
            signal = getattr(self.manager, signal_name, None)
            if signal is None:
                self.logger.warning(f"Tried to emit missing signal: {signal_name}")
                return
            emit_fn = getattr(signal, "emit", None)
            if callable(emit_fn):
                emit_fn(*args)
            else:
                self.logger.error(f"Attribute {signal_name} exists but is not a signal with 'emit'")
        except Exception as e:
            self.logger.error(f"Error emitting signal {signal_name}: {e}")

    # --------------------------------------------------------------
    def _handle_connection_error(self, error: Exception) -> str:
        """Interpret connection errors and classify them."""
        try:
            error_str = str(error).lower()
        except Exception:
            error_str = ""

        self.logger.debug(f"Raw error: {repr(error)}")
        self.logger.debug(f"Error string: {error_str}")

        if any(code in error_str for code in ["19881007", "19881005", "user_not_found", "user_not_live"]):
            self._emit_debug_message("User not live or not found", "error")
            return "user_not_live"

        if any(ind in error_str for ind in ["timeout", "connection reset", "503", "502", "504"]):
            self._emit_debug_message(f"Transient connection issue: {error}", "warning")
            return "retryable"

        self._emit_debug_message(f"Unhandled connection error: {error}", "error")
        return "unknown"

    # --------------------------------------------------------------
    def connect_to_user(self, username: str, max_retries: int = 3, attempt_timeout: int = 30) -> bool:
        """Attempt to connect to a TikTok user with retry and cleanup."""
        with self._cleanup_lock:
            self._is_cleaning_up = False

        self._update_connection_status("connecting")
        self._emit_debug_message(f"Connecting to @{username}...", "info")
        self.manager._username = username

        for attempt in range(max_retries):
            if getattr(self.manager, "_manual_disconnect", False) or getattr(self.manager, "_shutdown_requested",
                                                                             False):
                self._emit_debug_message("Manual disconnect or shutdown requested", "info")
                self._update_connection_status("disconnected")
                return False

            try:
                self._cleanup_existing_connection()
            except Exception as e:
                self.logger.warning(f"Pre-attempt cleanup error: {e}")

            try:
                success = self._attempt_connection(username, timeout=attempt_timeout)
                if success:
                    self._update_connection_status("connected")
                    self._emit_debug_message(f"Connected successfully to @{username}", "success")
                    self._start_heartbeat()
                    return True
            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                self._emit_debug_message(f"Attempt {attempt + 1} failed: {e}", "warning")

                error_type = self._handle_connection_error(e)
                if error_type == "user_not_live":
                    self._update_connection_status("error")
                    self._emit_debug_message("User not live — stopping retries", "error")
                    return False
                elif error_type == "retryable" and attempt < max_retries - 1:
                    backoff = 2 ** attempt
                    self._emit_debug_message(f"Retrying in {backoff}s...", "info")
                    time.sleep(backoff)
                    continue
                elif error_type == "unknown" and attempt < max_retries - 1:
                    backoff = 2 ** attempt
                    self._emit_debug_message(f"Unknown error, retrying in {backoff}s...", "info")
                    time.sleep(backoff)
                    continue

        self._update_connection_status("error")
        msg = f"Failed to connect to @{username} after {max_retries} attempts"
        self._emit_debug_message(msg, "error")
        self.logger.error(msg)
        return False

    # --------------------------------------------------------------
    def _attempt_connection(self, username: str, timeout: int = 30) -> bool:
        """Attempt connection in a thread with enforced timeout."""
        self.logger.info(f"=== Starting connection attempt to @{username} ===")
        result: Dict[str, Any] = {"success": False, "error": None}

        def worker():
            try:
                started = False
                if hasattr(self.manager, "client_manager") and callable(
                        getattr(self.manager.client_manager, "start_client", None)):
                    started = self.manager.client_manager.start_client()
                else:
                    raise RuntimeError("client_manager.start_client not available")
                result["success"] = bool(started)
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout)

        if thread.is_alive():
            self.logger.warning("Connection attempt timed out.")
            self._emit_debug_message("Connection attempt timed out", "error")
            try:
                if hasattr(self.manager, "client_manager") and callable(
                        getattr(self.manager.client_manager, "cleanup_client_internal", None)):
                    self.manager.client_manager.cleanup_client_internal()
            except Exception as e:
                self.logger.error(f"Error cleaning up after timeout: {e}")
            return False

        if result["error"]:
            raise result["error"]

        if result["success"]:
            self.logger.info("✓ Client started successfully")
            return True

        self.logger.info("Client did not start (start_client returned False)")
        return False

    # --------------------------------------------------------------
    def _cleanup_existing_connection(self):
        with self._cleanup_lock:
            if self._is_cleaning_up:
                return
            self._is_cleaning_up = True
        try:
            self._stop_heartbeat()
            if hasattr(self.manager, "client_manager") and callable(
                    getattr(self.manager.client_manager, "has_client", None)):
                if self.manager.client_manager.has_client():
                    self.manager.client_manager.cleanup_client_internal()
        finally:
            with self._cleanup_lock:
                self._is_cleaning_up = False

    # --------------------------------------------------------------
    def _start_heartbeat(self, interval: float = 30.0):
        if getattr(self.manager, "_manual_disconnect", False) or getattr(self.manager, "_shutdown_requested", False):
            return
        self._stop_heartbeat()
        try:
            self.manager._heartbeat_timer = threading.Timer(interval, self._check_connection_status)
            self.manager._heartbeat_timer.daemon = True
            self.manager._heartbeat_timer.start()
        except Exception as e:
            self.logger.error(f"Failed to start heartbeat: {e}")

    def _stop_heartbeat(self):
        try:
            if hasattr(self.manager, "_heartbeat_timer") and self.manager._heartbeat_timer:
                self.manager._heartbeat_timer.cancel()
                self.manager._heartbeat_timer = None
        except Exception as e:
            self.logger.error(f"Error stopping heartbeat: {e}")

    def _check_connection_status(self):
        if getattr(self.manager, "_manual_disconnect", False) or getattr(self.manager, "_shutdown_requested", False):
            self._update_connection_status("disconnected")
            return
        if getattr(self.manager, "_is_connected", False):
            self._update_connection_status("connected")
            self._start_heartbeat()
        else:
            username = getattr(self.manager, "_username", None)
            if username and not getattr(self.manager, "_manual_disconnect", False):
                self._emit_debug_message(f"Lost connection to @{username}", "error")
                self._update_connection_status("disconnected")
                try:
                    self.manager.client_manager.cleanup_client_internal()
                except Exception as e:
                    self.logger.error(f"Error cleaning client after lost connection: {e}")

    # --------------------------------------------------------------
    def disconnect(self):
        self.manager._manual_disconnect = True
        self._stop_heartbeat()
        try:
            if hasattr(self.manager, "client_manager"):
                self.manager.client_manager.cleanup_client_internal()
        except Exception as e:
            self.logger.error(f"Error during manual disconnect cleanup: {e}")
        self._update_connection_status("disconnected")
        self._emit_debug_message("Disconnected from TikTok", "info")

    # --------------------------------------------------------------
    def deep_cleanup(self):
        self.logger.info("Performing deep cleanup")
        with self._cleanup_lock:
            if self._is_cleaning_up:
                return
            self._is_cleaning_up = True
        try:
            self._stop_heartbeat()
            if hasattr(self.manager, "client_manager"):
                try:
                    self.manager.client_manager.cleanup_client_internal()
                    if hasattr(self.manager.client_manager, "force_cleanup"):
                        self.manager.client_manager.force_cleanup()
                except Exception as e:
                    self.logger.error(f"Error during deep cleanup: {e}")
            self.connection_monitor = None
        finally:
            with self._cleanup_lock:
                self._is_cleaning_up = False
