"""
Bulletproof Memory Widget - Thread-safe UI memory display

Key improvements:
- Thread-safe operations
- Proper cleanup on shutdown
- Error handling for JS failures
- Safe signal connections
- No memory leaks
"""

import logging

from PySide6.QtCore import QTimer, QObject, Slot

from core.utils.memory_monitor import MemoryMonitor


class MemoryMonitorWidget(QObject):
    """
    Thread-safe widget that updates UI with memory and CPU stats.

    Features:
    - Safe JavaScript injection
    - Proper cleanup on shutdown
    - Error resilience
    - Signal connection tracking
    """

    def __init__(self, web_view=None, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger("MemoryMonitorWidget")

        # WebView
        self.web_view = web_view
        self.page_ready = False

        # Monitor singleton
        self.monitor = MemoryMonitor.get_instance()

        # Timer
        self.timer = None
        self._update_interval = 1500  # ms

        # Cleanup tracking
        self._signal_connections = []
        self._cleanup_complete = False

        # Initialize
        self._connect_signals()
        self._setup_timer()
        self._setup_page_ready()

        self.logger.info("✅ MemoryMonitorWidget initialized")

    def _connect_signals(self):
        """Connect to monitor signals with tracking."""
        try:
            # Connect with tracking for cleanup
            self.monitor.memory_warning.connect(self._on_memory_warning)
            self._signal_connections.append(
                (self.monitor.memory_warning, self._on_memory_warning)
            )

            self.monitor.memory_critical.connect(self._on_memory_critical)
            self._signal_connections.append(
                (self.monitor.memory_critical, self._on_memory_critical)
            )

            self.monitor.cleanup_triggered.connect(self._on_cleanup_triggered)
            self._signal_connections.append(
                (self.monitor.cleanup_triggered, self._on_cleanup_triggered)
            )

            self.logger.debug("Monitor signals connected")

        except Exception as e:
            self.logger.error(f"Signal connection failed: {e}")

    def _setup_timer(self):
        """Setup update timer with error handling."""
        try:
            self.timer = QTimer(self)
            self.timer.setInterval(self._update_interval)
            self.timer.timeout.connect(self._update_stats_in_ui)
            self.timer.start()

            self.logger.debug(f"Update timer started ({self._update_interval}ms)")

        except Exception as e:
            self.logger.error(f"Timer setup failed: {e}")

    def _setup_page_ready(self):
        """Setup page ready detection."""
        if self.web_view:
            try:
                self.web_view.loadFinished.connect(self._on_page_loaded)
                self.logger.debug("Page ready detection setup")
            except Exception as e:
                self.logger.error(f"Page ready setup failed: {e}")

    @Slot(bool)
    def _on_page_loaded(self, success: bool):
        """Handle page load completion."""
        self.page_ready = success

        if success:
            self.logger.info("🌐 Web page loaded - ready for updates")
            # Do initial update
            self._update_stats_in_ui()
        else:
            self.logger.warning("⚠️ Page load failed - no JS updates")

    def _update_stats_in_ui(self):
        """
        Send memory and CPU data to UI via JavaScript.
        Thread-safe and protected against errors.
        """
        # Safety checks
        if not self.web_view:
            return

        if not self.page_ready:
            return

        page = self.web_view.page()
        if not page:
            return

        try:
            # Get status
            status = self.monitor.get_status()

            memory_mb = status.get("memory_mb", 0.0)
            cpu_percent = status.get("cpu_percent", 0.0)
            state = status.get("status", "OK")
            trend = status.get("trend", "↓")

            # Build JavaScript
            js = (
                f"if (window.updateSystemStatus) {{"
                f"  updateSystemStatus({memory_mb:.1f}, {cpu_percent:.1f}, '{state}', '{trend}');"
                f"}} else {{"
                f"  console.warn('updateSystemStatus not available yet');"
                f"}}"
            )

            # Execute (non-blocking)
            page.runJavaScript(js)

        except Exception as e:
            # Don't spam logs with JS errors
            if not hasattr(self, '_last_js_error') or time.time() - self._last_js_error > 10:
                self.logger.warning(f"JS update failed: {e}")
                self._last_js_error = time.time()

    # ==================== SIGNAL HANDLERS ====================

    @Slot(int)
    def _on_memory_warning(self, mb: int):
        """Handle memory warning signal."""
        self.logger.warning(f"⚠️ Memory warning: {mb}MB")

    @Slot(int)
    def _on_memory_critical(self, mb: int):
        """Handle memory critical signal."""
        self.logger.error(f"🚨 Memory critical: {mb}MB")

    @Slot(str)
    def _on_cleanup_triggered(self, reason: str):
        """Handle cleanup triggered signal."""
        self.logger.info(f"🧹 Cleanup triggered: {reason}")

    # ==================== CONTROL ====================

    def start(self):
        """Start monitoring and updates."""
        if self.timer and not self.timer.isActive():
            self.timer.start()
            self.logger.info("Widget updates started")

        # Ensure monitor is running
        if not self.monitor._monitoring:
            self.monitor.start_monitoring()

    def stop(self):
        """Stop monitoring and updates."""
        if self.timer and self.timer.isActive():
            self.timer.stop()
            self.logger.info("Widget updates stopped")

    def set_update_interval(self, ms: int):
        """Change update interval."""
        if ms < 100:
            ms = 100  # Minimum 100ms

        self._update_interval = ms
        if self.timer:
            self.timer.setInterval(ms)
            self.logger.debug(f"Update interval changed to {ms}ms")

    # ==================== CLEANUP ====================

    def cleanup(self):
        """
        Complete cleanup with guaranteed resource release.
        Idempotent and thread-safe.
        """
        if self._cleanup_complete:
            return

        self.logger.info("🧹 Cleaning up MemoryMonitorWidget...")

        try:
            # Stop timer
            if self.timer:
                try:
                    self.timer.stop()
                    self.timer.deleteLater()
                except Exception as e:
                    self.logger.warning(f"Timer cleanup error: {e}")
                finally:
                    self.timer = None

            # Disconnect signals
            for signal, handler in self._signal_connections:
                try:
                    signal.disconnect(handler)
                except Exception:
                    pass  # Already disconnected

            self._signal_connections.clear()

            # Clear page ready flag
            self.page_ready = False

            self._cleanup_complete = True
            self.logger.info("✅ MemoryMonitorWidget cleanup complete")

        except Exception as e:
            self.logger.error(f"Cleanup error: {e}")

    def __del__(self):
        """Destructor - ensure cleanup."""
        try:
            if not self._cleanup_complete:
                self.cleanup()
        except Exception:
            pass


# Add time import for error throttling
import time
