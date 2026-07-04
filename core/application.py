"""
core/application.py — QuizMaster application bootstrap
"""

import logging
import os
import signal
import sys
import threading
import traceback
from types import FrameType
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from core.services.lifecycle_state import begin_shutdown
from core.services.service_config import ServicePriority
from core.services.service_registry import ServiceRegistry
from core.services.subsystem_manager import SubsystemManager


class Application:
    def __init__(self):
        self.registry = None
        self.logger = None
        self._shutdown_called = False
        self._shutdown_lock = threading.Lock()
        self.splash = None
        self.subsystems = SubsystemManager.get_instance()
        self._startup_debug("QuizMaster initializing...")

        try:
            self.registry = ServiceRegistry()
            self._startup_debug("ServiceRegistry created")
        except Exception as e:
            logging.getLogger("Application").error(f"ServiceRegistry init failed: {e}")
            raise

        try:
            self.logger = self._setup_logger()
            self._startup_debug("Logger set up")
        except Exception as e:
            logging.getLogger("Application").error(f"Logger setup failed: {e}")
            raise

        from core.tiktok.account_stats import register_cloudflare_tiktok_account_stats_provider

        register_cloudflare_tiktok_account_stats_provider()

        try:
            self._setup_signal_handlers()
            self._startup_debug("Signal handlers ready")
        except Exception as e:
            self.logger.error(f"Signal handler setup failed: {e}")
            raise

        self._ensure_cloud_architecture_foundation()

        self._startup_debug("QuizMaster initialized")

    def _ensure_cloud_architecture_foundation(self) -> None:
        """Create Milestone 6 local cloud state files without blocking startup."""
        try:
            from core.services.cloud_service import CloudService

            status = CloudService().ensure_cloud_architecture()
            self.logger.info(
                "Cloud architecture foundation ready=%s cloud_state=%s sync_queue=%s pending_sync_count=%s",
                status.get("ready"),
                status.get("paths", {}).get("cloud_state_json"),
                status.get("paths", {}).get("sync_queue_json"),
                status.get("pending_sync_count", 0),
            )
        except Exception as e:
            self.logger.warning("Cloud architecture foundation setup failed without blocking startup: %s", e, exc_info=True)

    def _load_services_by_priority(self, priority: ServicePriority):
        """Load services in config order for a given priority."""
        self._startup_debug(f"Loading {priority.name} priority services...")
        for name, cfg in self.registry._configs.items():
            if cfg.priority == priority:
                try:
                    self.registry.get(name)
                    self._startup_debug(f"{name} loaded")
                except Exception as e:
                    self.logger.error(f"{name} failed: {e}")
        self._startup_debug(f"{priority.name} priority done")

    def _initialize_deferred_services(self):
        """Initialize non-critical startup services after window is shown."""
        try:
            self._load_services_by_priority(ServicePriority.HIGH)
            QTimer.singleShot(0, self._initialize_deferred_normal_services)
        except Exception as e:
            self.logger.error(f"Deferred HIGH startup failed: {e}", exc_info=True)
            mw = self._safe_get("MainWindow")
            if mw and hasattr(mw, "hide_loading_overlay"):
                mw.hide_loading_overlay()

    def _initialize_deferred_normal_services(self):
        """Continue deferred initialization for NORMAL services and runtime subsystems."""
        try:
            self._load_services_by_priority(ServicePriority.NORMAL)

            if os.environ.get("QUIZMASTER_NO_BRIDGE") != "1":
                from core.services.identity_resolver import log_runtime_identity

                log_runtime_identity("before_http_bridge_routes_widget_managers")
                self.subsystems.register("http_bridge", start=self._initialize_http_bridge, stop=lambda: getattr(self, "bridge", None) and self.bridge.stop())
                self.subsystems.start("http_bridge")

            self._startup_debug("Initializing Memory Monitor...")
            self.setup_memory_monitoring()
            self._startup_debug("MemoryMonitor initialized")

            qm = self._safe_get("QuizManager")
            if qm:
                self.subsystems.register("quiz", start=lambda: None, stop=qm.cleanup)
                self.subsystems.start("quiz")

        except Exception as e:
            self.logger.error(f"Deferred NORMAL startup failed: {e}", exc_info=True)
        finally:
            mw = self._safe_get("MainWindow")
            if mw and hasattr(mw, "hide_loading_overlay"):
                mw.hide_loading_overlay()

    # ------------------------------------------------------------------
    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger = logging.getLogger("Application")
        logger.setLevel(logging.NOTSET)
        return logger

    def _startup_debug(self, message: str) -> None:
        """Emit startup diagnostics only when debug logging is enabled."""
        logger = self.logger or logging.getLogger("Application")
        logger.debug(message)

    # ------------------------------------------------------------------
    def _setup_signal_handlers(self):
        def handler(signum: int, frame: Optional[FrameType]):
            self.logger.warning(f"Received signal {signum}, initiating shutdown...")
            app = self._safe_get("QApplication")
            if app:
                QTimer.singleShot(0, lambda: self._signal_shutdown())
            else:
                self.shutdown()
                sys.exit(0)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _signal_shutdown(self):
        """Handle shutdown from signal in main thread"""
        self.shutdown()
        app = self._safe_get("QApplication")
        if app:
            app.quit()

    # ------------------------------------------------------------------
    def _safe_get(self, name: str):
        try:
            return self.registry.get(name)
        except Exception as e:
            self.logger.error(f"Failed to get service '{name}': {e}")
            return None


    @staticmethod
    def _is_quizmaster_pro(profile) -> bool:
        """Delegate to the subscription gate module.

        The actual entitlement decision lives in ``core.services.subscription_gate``
        so release builds can compile it to a native extension. Behaviour is
        unchanged; this wrapper keeps the existing call sites intact. Fails closed."""
        try:
            from core.services.subscription_gate import is_quizmaster_pro

            return is_quizmaster_pro(profile)
        except Exception:
            return False

    def _prompt_quizmaster_subscription_required(self, auth_service) -> bool:
        """Signed in without a QuizMaster subscription. Return True to retry with
        another account, False to exit the app."""
        try:
            import webbrowser
            from PySide6.QtWidgets import QMessageBox
            from core.services.auth_service import DASHBOARD_URL

            box = QMessageBox()
            box.setWindowTitle("QuizMaster subscription required")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("This account doesn't have an active QuizMaster subscription.")
            box.setInformativeText("Add QuizMaster from your dashboard, then sign in again.")
            get_btn = box.addButton("Get QuizMaster", QMessageBox.ButtonRole.AcceptRole)
            retry_btn = box.addButton("Use another account", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is get_btn:
                try:
                    webbrowser.open(DASHBOARD_URL)
                except Exception:
                    pass
                return False
            if clicked is retry_btn:
                try:
                    auth_service.logout()
                except Exception:
                    pass
                return True
            return False
        except Exception as exc:
            self.logger.error("QuizMaster subscription prompt failed: %s", exc, exc_info=True)
            return False

    def _ensure_authenticated(self, app, auth_service) -> bool:
        """Require a signed-in, QuizMaster-entitled account before the app opens.

        QuizMaster is a paid app. In the default ``account`` mode the user must
        sign in with an account that holds an active QuizMaster subscription;
        closing or cancelling the login exits the app instead of bypassing into
        it. ``local`` mode remains only as an offline development escape hatch.
        """
        auth_mode = os.environ.get("QUIZMASTER_AUTH_MODE", "local").strip().lower() or "local"

        restored = None
        if auth_service is not None:
            try:
                restored = auth_service.restore_saved_session()
                if restored:
                    from core.services.identity_resolver import log_runtime_identity

                    log_runtime_identity("after_account_session_restore")
            except Exception as exc:
                self.logger.warning("Saved QuizMaster account session could not be restored: %s", exc, exc_info=True)
                restored = None

        if auth_mode != "account":
            self.logger.info("QuizMaster auth mode=%s — subscription gate not enforced (offline/dev)", auth_mode)
            return True

        if auth_service is None:
            self.logger.error("AuthService unavailable — QuizMaster requires sign-in and cannot continue")
            return False

        if restored is not None and self._is_quizmaster_pro(restored):
            self.logger.info("QuizMaster subscription verified for restored session %s", getattr(restored, "email", "account"))
            return True

        try:
            from PySide6.QtWidgets import QDialog
            from core.display.login_dialog import LoginDialog
        except Exception as exc:
            self.logger.error("QuizMaster login gate is unavailable: %s", exc, exc_info=True)
            return False

        while True:
            try:
                dialog = LoginDialog(auth_service=auth_service)
                result = dialog.exec()
            except Exception as exc:
                self.logger.error("QuizMaster login dialog failed: %s", exc, exc_info=True)
                return False

            if result != QDialog.DialogCode.Accepted:
                self.logger.info("QuizMaster sign-in cancelled — exiting without opening the app")
                return False

            profile = getattr(dialog, "profile", None) or getattr(auth_service, "current_profile", None)
            if profile is not None and self._is_quizmaster_pro(profile):
                self.logger.info("QuizMaster subscription verified for %s", getattr(profile, "email", "account"))
                return True

            if not self._prompt_quizmaster_subscription_required(auth_service):
                return False

    # ------------------------------------------------------------------
    def _initialize_http_bridge(self):
        self._startup_debug("Initializing HTTP Bridge...")
        try:
            from core.server.bridge_server import HTTPBridgeServer

            bridge = HTTPBridgeServer(host="127.0.0.1", port=5555)

            bridge.quiz_manager = self._safe_get("QuizManager")
            bridge.config_manager = self._safe_get("ConfigManager")

            self._startup_debug("Starting HTTP Bridge...")
            bridge.start()
            self.bridge = bridge

            bridge._ready_event.wait(timeout=5)
            if bridge._startup_exception:
                raise RuntimeError(f"HTTP Bridge startup failed: {bridge._startup_exception}") from bridge._startup_exception

            self._startup_debug("HTTP Bridge initialized successfully")
        except Exception as e:
            self.logger.error(f"_initialize_http_bridge failed: {e}")
            traceback.print_exc()
            raise

    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    def setup_icon(self):
        self._startup_debug("Setting up icon...")
        try:
            app = self._safe_get("QApplication")
            if not app:
                self.logger.warning("QApplication not available for icon setup")
                return

            from core.utils.resource_loader import get_asset_path
            icon_path = get_asset_path("images", "icon.ico")
            if not icon_path.exists():
                self.logger.warning("icon.ico missing")
                return

            app.setWindowIcon(QIcon(str(icon_path)))
            self._startup_debug(f"App icon set from {icon_path}")
        except Exception as e:
            self.logger.error(f"setup_icon() failed: {e}")

    def setup_memory_monitoring(self):
        """Ensure MemoryMonitor and MemoryUtils start properly."""
        try:
            from core.utils.memory_monitor import MemoryMonitor

            monitor = MemoryMonitor.get_instance()
            monitor.start_monitoring()

            self.logger.debug("MemoryMonitor is active")

            monitor.memory_warning.connect(self.on_memory_warning)
            monitor.memory_critical.connect(self.on_memory_critical)
            monitor.cleanup_triggered.connect(self.on_cleanup_triggered)

        except Exception as e:
            self.logger.error(f"✗ setup_memory_monitoring failed: {e}", exc_info=True)

    @staticmethod
    def on_memory_warning(memory_mb: int):
        logging.getLogger("Application").warning(f"Memory warning: {memory_mb}MB")

    @staticmethod
    def on_memory_critical(memory_mb: int):
        logging.getLogger("Application").error(f"Memory critical: {memory_mb}MB")

    @staticmethod
    def on_cleanup_triggered(reason: str):
        logging.getLogger("Application").warning(f"Cleanup: {reason}")

    # ------------------------------------------------------------------
    def show_splash_screen(self):
        """Show video splash screen before main window"""
        try:
            from core.display.splash_screen import QuizMasterSplashScreen
            from core.utils.resource_loader import get_asset_path

            video_path = get_asset_path("videos", "splash.mp4")

            if not video_path.exists():
                self.logger.warning(f"Splash video not found at {video_path}, skipping splash")
                return

            self.logger.debug("Showing QuizMaster splash screen")
            self.splash = QuizMasterSplashScreen(video_path)
            self.splash.show_and_play()

            # Wait for splash to finish (connect to closed signal)
            from PySide6.QtCore import QEventLoop
            loop = QEventLoop()
            self.splash.destroyed.connect(loop.quit)

            # Also add timeout in case splash hangs
            QTimer.singleShot(15000, loop.quit)  # 15 second max

            loop.exec()
            self.logger.debug("Splash screen finished")

        except Exception as e:
            self.logger.error(f"Error showing splash screen: {e}", exc_info=True)

    # ------------------------------------------------------------------
    def run(self) -> int:
        """Run the main application."""
        self._startup_debug("QuizMaster.run() entered")
        try:
            self.logger.debug("Starting QuizMaster")

            # QApplication and AuthService must be available before the rest of
            # the critical graph. HTTPBridgeServer and its route managers bind
            # runtime identity while they are constructed.
            app = self._safe_get("QApplication")
            if not app:
                self.logger.error("QApplication missing, aborting run()")
                return 1
            self._startup_debug(f"QApplication present: {type(app)}")

            self.setup_icon()

            auth_service = self._safe_get("AuthService")
            if not self._ensure_authenticated(app, auth_service):
                self.logger.info("QuizMaster startup blocked: sign-in with an active subscription is required")
                return 0

            # CRITICAL
            self._startup_debug("Loading critical services...")
            from core.services.identity_resolver import log_runtime_identity

            log_runtime_identity("before_critical_services_http_bridge_routes_widget_managers")
            self.registry.preload_critical()
            self._startup_debug("Critical services loaded")

            sl = self._safe_get("ServiceLocator")
            self.registry.set_service_locator(sl)
            self._startup_debug("ServiceLocator integrated")

            # MainWindow startup first for immediate perceived responsiveness
            mw = self._safe_get("MainWindow")
            if mw:
                mw.show()
                self._startup_debug("MainWindow shown")
                if hasattr(mw, "show_loading_overlay"):
                    QTimer.singleShot(0, mw.show_loading_overlay)
                if hasattr(mw, "update_status_message") and os.environ.get("QUIZMASTER_AUTH_MODE", "local").lower() == "local":
                    QTimer.singleShot(0, lambda: mw.update_status_message("Not signed in — local mode active"))
            else:
                self.logger.warning("MainWindow unavailable")

            # Defer heavier startup work to next event loop ticks
            QTimer.singleShot(0, self._initialize_deferred_services)

            self._startup_debug("Before app.exec()")
            rc = app.exec()
            self._startup_debug(f"After app.exec() → exit_code={rc}")
            return rc

        except Exception as e:
            self.logger.error(f"Exception in Application.run: {e}")
            traceback.print_exc()
            return 1
        finally:
            if not self._shutdown_called:
                self.shutdown()

    # ------------------------------------------------------------------
    def shutdown(self):
        """Graceful shutdown of all services."""
        with self._shutdown_lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            begin_shutdown()

        self.logger.debug("=" * 60)
        self.logger.debug("LIVEFORGE SHUTDOWN SEQUENCE INITIATED")
        self.logger.debug("=" * 60)

        try:
            # Close splash if still open
            if self.splash:
                try:
                    self.splash.close()
                    self._startup_debug("Splash screen closed")
                except Exception as e:
                    self.logger.warning(f"Splash close warning: {e}")

            # 1. Stop event producers/workers first
            self._startup_debug("Stopping event producers and worker subsystems...")
            try:
                self.subsystems.stop_all()
                br = self._safe_get("HTTPBridgeServer")
                if br:
                    br.cleanup()
                    self._startup_debug("HTTP bridge stopped")
            except Exception as e:
                self.logger.warning(f"Bridge stop warning: {e}")

            # Stop Memory Monitor once worker/event sources are stopped
            self._startup_debug("Stopping memory monitor...")
            try:
                from core.utils.memory_monitor import MemoryMonitor

                monitor = MemoryMonitor.get_instance()
                monitor.stop_monitoring()
                self._startup_debug("Memory monitor stopped")
            except Exception as e:
                self.logger.warning(f"Memory monitor stop warning: {e}")

            # Close MainWindow explicitly
            self._startup_debug("Closing main window...")
            try:
                mw = self._safe_get("MainWindow")
                if mw:
                    mw.close()
                    self._startup_debug("Main window closed")
            except Exception as e:
                self.logger.warning(f"Window close warning: {e}")

            # 2. Terminate Qt event loop
            self._startup_debug("Quitting QApplication event loop...")
            try:
                app = self._safe_get("QApplication")
                if app:
                    app.quit()
                    self._startup_debug("QApplication quit")
            except Exception as e:
                self.logger.warning(f"QApplication quit warning: {e}")

            # 3. Delete QApplication instance reference from registry/service locator
            self._startup_debug("Deleting QApplication service references...")
            try:
                if self.registry and "QApplication" in self.registry._services:
                    self.registry._services.pop("QApplication", None)
                sl = self.registry._services.get("ServiceLocator") if self.registry else None
                if sl and hasattr(sl, "_services"):
                    sl._services.pop("QApplication", None)
            except Exception as e:
                self.logger.warning(f"QApplication reference cleanup warning: {e}")

            # Optional manager cleanup while registry still alive
            self._startup_debug("Cleaning up quiz manager...")
            try:
                qm = self._safe_get("QuizManager")
                if qm and hasattr(qm, "cleanup"):
                    qm.cleanup()
                    self._startup_debug("Quiz manager cleaned")
            except Exception as e:
                self.logger.warning(f"Quiz manager cleanup warning: {e}")

            # 4. Cleanup registry last
            self._startup_debug("Cleaning up service registry...")
            try:
                self.registry.cleanup()
                self._startup_debug("Registry cleaned")
            except Exception as e:
                self.logger.warning(f"Registry cleanup warning: {e}")

        except Exception as e:
            self.logger.error(f"Exception during shutdown: {e}")
            traceback.print_exc()

        self.logger.debug("=" * 60)
        self.logger.debug("LIVEFORGE SHUTDOWN COMPLETE")
        self.logger.debug("=" * 60)
