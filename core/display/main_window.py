"""
MainWindow — QuizMaster
Simple, fast, and production-ready.
Serves your HTML/CSS/JS UI from the local HTTP bridge.
"""

import logging
import os
import time
from typing import Optional

from PySide6.QtCore import QEvent, Qt, QUrl, QTimer
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QProgressBar

from core.server.url_config import get_internal_url
from core.services.service_locator import ServiceLocator
from core.utils.memory_widget import MemoryMonitorWidget




class LoadingOverlay(QWidget):
    """Lightweight internal loading overlay shown during deferred startup."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 150);")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Loading QuizMaster services…", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: white; font-size: 16px; font-weight: 600;")

        progress = QProgressBar(self)
        progress.setRange(0, 0)
        progress.setFixedWidth(320)
        progress.setTextVisible(False)

        layout.addWidget(title)
        layout.addWidget(progress)

        self.hide()

class MainWindow(QMainWindow):
    """Main application window — loads UI via local HTTP bridge"""

    def __init__(self):
        super().__init__()

        self.logger = logging.getLogger(self.__class__.__name__)
        self.service_locator: Optional[ServiceLocator] = None
        self.web_view: Optional[QWebEngineView] = None
        self.bridge_url = get_internal_url("")
        self.loading_overlay: Optional[LoadingOverlay] = None
        self._diagnostic_load_count = 0
        self._diagnostic_last_load_ts = None
        self._diagnostic_last_url = None
        self._last_valid_url = None

        self.logger.info("Initializing QuizMaster MainWindow...")
        self._get_services()
        self._setup_ui()
        self._load_main_window()
        self.logger.info("QuizMaster MainWindow initialized.")

    # ---------------------------------------------------------

    def _get_services(self):
        self.service_locator = ServiceLocator.get_instance()

    def _setup_ui(self):
        """Prepare WebEngine view"""
        title_suffix = " — Local mode" if os.environ.get("QUIZMASTER_AUTH_MODE", "local").lower() == "local" else ""
        self.setWindowTitle(f"QuizMaster{title_suffix}")
        self.setWindowState(Qt.WindowState.WindowMaximized)
        self.setMinimumSize(900, 600)

        self.web_view = QWebEngineView()
        self.loading_overlay = LoadingOverlay(self)

        # ✅ Enable clipboard access
        from PySide6.QtWebEngineCore import QWebEngineSettings
        settings = self.web_view.page().settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)

        # ✅ Handle window.open() popups
        self.web_view.page().newWindowRequested.connect(self._on_new_window_requested)
        self.memory_monitor_widget = MemoryMonitorWidget(self.web_view)

        self.setCentralWidget(self.web_view)
        self.web_view.loadFinished.connect(self._on_page_loaded)
        self.web_view.loadStarted.connect(self._on_page_load_started)
        self.web_view.urlChanged.connect(self._on_url_changed)

    # ---------------------------------------------------------
    def _on_new_window_requested(self, request):
        """Handle window.open() requests by opening in external browser"""
        import webbrowser
        url = request.requestedUrl().toString()
        self.logger.info(f"Opening URL in browser: {url}")
        webbrowser.open(url)
        request.action = request.WebWindowType.WebBrowserWindow

    # ---------------------------------------------------------


    def _on_page_load_started(self):
        now = time.time()
        self._diagnostic_load_count += 1
        elapsed = None
        if self._diagnostic_last_load_ts is not None:
            elapsed = now - self._diagnostic_last_load_ts
        self._diagnostic_last_load_ts = now

        self.logger.warning(
            "[DIAGNOSTIC] QWebEngineView loadStarted count=%s elapsed_since_last_s=%s current_url=%s",
            self._diagnostic_load_count,
            f"{elapsed:.3f}" if elapsed is not None else "first_load",
            self.web_view.url().toString() if self.web_view else "none",
        )

    def _on_url_changed(self, url: QUrl):
        now = time.time()
        previous = self._diagnostic_last_url
        new_url = url.toString().strip()
        self._diagnostic_last_url = new_url
        if new_url:
            self._last_valid_url = new_url

        self.logger.warning(
            "[DIAGNOSTIC] QWebEngineView urlChanged ts=%s previous_url=%s new_url=%s",
            now,
            previous,
            new_url,
        )

        # Guard against empty/invalid target navigation that can appear during transport hiccups.
        if not new_url and self.web_view:
            fallback = self._last_valid_url or f"{self.bridge_url}/main_window.html"
            self.logger.warning("[DIAGNOSTIC] Ignoring empty URL navigation, restoring fallback=%s", fallback)
            QTimer.singleShot(0, lambda: self.web_view.setUrl(QUrl(fallback)))

    def _load_main_window(self):
        use_qrc_frontend = os.environ.get("LIVEFORGE_USE_QRC_FRONTEND", "0") == "1"

        if use_qrc_frontend:
            # Protection mode: load HTML from Qt resources to avoid shipping clear-text
            # front-end files in the release directory.
            url = QUrl("qrc:/frontend/core/server/static/html/main_window.html")
        else:
            # Default development/runtime behavior remains unchanged.
            url = QUrl(f"{self.bridge_url}/main_window.html")

        self.web_view.setUrl(url)
        self.logger.info(f"Loading {url.toString()}")

    def _on_page_loaded(self, success: bool):
        self.logger.warning(
            "[DIAGNOSTIC] QWebEngineView loadFinished success=%s url=%s total_loads=%s",
            success,
            self.web_view.url().toString() if self.web_view else "none",
            self._diagnostic_load_count,
        )
        if success:
            self.logger.info("✓ QuizMaster main window loaded successfully")
        else:
            self.logger.error("✗ Failed to load QuizMaster main window HTML")
            self._load_error_page()

    def _load_error_page(self):
        """Display a minimal inline error page"""
        error_html = f"""
        <html><body style="font-family:sans-serif;background:#0a0a0f;color:#eee;text-align:center;margin-top:15%;">
        <h2>⚠️ Could not connect to QuizMaster server</h2>
        <p>The desktop interface service is still starting or unavailable.</p>
        <p>No local data has been changed. Try again in a moment.</p>
        <button onclick="location.reload()" 
                style="background:#00ffcc;color:#000;border:none;padding:12px 24px;
                       border-radius:8px;cursor:pointer;margin-top:20px;font-weight:600;">Retry Connection</button>
        </body></html>
        """
        self.web_view.setHtml(error_html)

    # ---------------------------------------------------------

    def update_connection_status(self, connected: bool):
        js = f"if (window.updateConnectionStatus) updateConnectionStatus({str(connected).lower()});"
        if self.web_view and self.web_view.page():
            self.web_view.page().runJavaScript(js)

    def update_status_message(self, message: str):
        escaped = message.replace("'", "\\'").replace('"', '\\"')
        js = f"if (window.updateStatusMessage) updateStatusMessage('{escaped}');"
        if self.web_view and self.web_view.page():
            self.web_view.page().runJavaScript(js)


    def show_loading_overlay(self):
        if self.loading_overlay:
            self.loading_overlay.setGeometry(self.rect())
            self.loading_overlay.raise_()
            self.loading_overlay.show()

    def hide_loading_overlay(self):
        if self.loading_overlay:
            self.loading_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.loading_overlay:
            self.loading_overlay.setGeometry(self.rect())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.pause_noncritical_updates()
            else:
                self.resume_noncritical_updates()

    def pause_noncritical_updates(self):
        if getattr(self, "memory_monitor_widget", None):
            self.memory_monitor_widget.stop()

    def resume_noncritical_updates(self):
        if getattr(self, "memory_monitor_widget", None):
            self.memory_monitor_widget.start()

    def closeEvent(self, event):
        """Handle window close event - ensure proper cleanup"""
        self.logger.info("QuizMaster MainWindow close event triggered")

        try:
            # Stop non-critical update timers
            if getattr(self, "memory_monitor_widget", None):
                self.memory_monitor_widget.cleanup()

            # Stop any page loading
            if self.web_view:
                self.web_view.stop()

            # Clear the page to release resources
            if self.web_view and self.web_view.page():
                self.web_view.page().deleteLater()

            self.logger.info("✓ QuizMaster MainWindow cleanup complete")
        except Exception as e:
            self.logger.warning(f"Error during window cleanup: {e}")

        # Accept the close event
        event.accept()
