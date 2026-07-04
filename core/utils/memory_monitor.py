"""
Bulletproof Memory Monitor - Thread-safe memory management and cleanup

Key improvements:
- Complete thread safety with RLock
- Operation guards for cleanup
- Safe CPU measurement
- Proper QTimer cleanup
- No memory leaks
"""

import gc
import logging
import threading
import time
from typing import Optional

import psutil
from PySide6.QtCore import QObject, Signal, QTimer

from core.services.service_locator import ServiceLocator


class MemoryMonitor(QObject):
    """
    Thread-safe singleton for memory management and cleanup.

    Features:
    - Accurate CPU and memory tracking
    - Automatic cleanup at thresholds
    - Thread-safe operations
    - Proper resource cleanup
    """

    # Signals
    memory_warning = Signal(int)
    memory_critical = Signal(int)
    cleanup_triggered = Signal(str)

    # Thresholds (MB)
    WARNING_THRESHOLD = 450
    CRITICAL_THRESHOLD = 550
    TARGET_MEMORY = 250

    # Intervals (seconds)
    NORMAL_INTERVAL = 10
    HIGH_LOAD_INTERVAL = 5

    # Singleton
    _instance = None
    _instance_lock = threading.RLock()

    @classmethod
    def get_instance(cls):
        """Thread-safe singleton access."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        """Initialize monitor - private, use get_instance()."""
        if hasattr(self, "_initialized"):
            return

        super().__init__()
        self._initialized = True

        self.logger = logging.getLogger("MemoryMonitor")

        # Process handle
        try:
            self._process = psutil.Process()
        except Exception as e:
            self.logger.error(f"Failed to get process handle: {e}")
            self._process = None

        # State
        self._monitoring = False
        self._timer: Optional[QTimer] = None
        self._cleanup_in_progress = False

        # Thread safety
        self._state_lock = threading.RLock()

        # Cleanup cooldown
        self._last_cleanup = 0
        self._cleanup_cooldown = 30

        # Memory tracking
        self._memory_history = []
        self._max_history = 10

        # CPU tracking (baseline)
        self._last_cpu_check = 0
        self._cached_cpu = 0.0
        self._cpu_check_interval = 1.0

        # Service references
        self._client_manager = None
        self._bridge_server = None
        self._memory_utils = None

        # Initialize CPU baseline
        self._init_cpu_baseline()

        # Auto-bind services
        self._auto_bind_services()

        self.logger.info("✅ MemoryMonitor initialized")

    def _init_cpu_baseline(self):
        """
        Initialize CPU baseline for accurate measurements.
        CRITICAL: First call to cpu_percent() establishes baseline.
        """
        try:
            if self._process:
                # First call returns 0 but establishes baseline
                self._process.cpu_percent(interval=None)
                self.logger.debug("CPU baseline established")
        except Exception as e:
            self.logger.warning(f"CPU baseline init failed: {e}")

    def _auto_bind_services(self):
        """Link to other services via ServiceLocator."""
        try:
            locator = ServiceLocator.get_instance()
            if not locator:
                return

            # Bind only available services to avoid noisy startup warnings.
            if hasattr(locator, 'has_service') and locator.has_service("HTTPBridgeServer"):
                self._bridge_server = locator.get_service("HTTPBridgeServer")

            if hasattr(locator, 'has_service') and locator.has_service("MemoryUtils"):
                self._memory_utils = locator.get_service("MemoryUtils")

            # Get TikTok client manager if available
            tiktok_mgr = None
            if hasattr(locator, 'has_service') and locator.has_service("TikTokLiveManager"):
                tiktok_mgr = locator.get_service("TikTokLiveManager")
            if tiktok_mgr and hasattr(tiktok_mgr, "client_manager"):
                self._client_manager = tiktok_mgr.client_manager

            self.logger.info("✅ Services bound to MemoryMonitor")

        except Exception as e:
            self.logger.warning(f"Service binding failed: {e}")

    # ==================== START/STOP ====================

    def start_monitoring(self, interval: int = None):
        """
        Start memory monitoring with thread safety.

        Args:
            interval: Check interval in seconds (default: NORMAL_INTERVAL)
        """
        if interval is None:
            interval = self.NORMAL_INTERVAL

        with self._state_lock:
            if self._monitoring:
                self.logger.debug("Monitoring already active")
                return False

            self._monitoring = True

        try:
            # Create timer
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._check_memory)
            self._timer.start(interval * 1000)

            self.logger.info(f"✅ Memory monitoring started ({interval}s interval)")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start monitoring: {e}")
            with self._state_lock:
                self._monitoring = False
            return False

    def stop_monitoring(self):
        """
        Stop monitoring with guaranteed cleanup.
        Thread-safe and idempotent.
        """
        with self._state_lock:
            if not self._monitoring:
                return

            self.logger.info("Stopping memory monitoring...")
            self._monitoring = False

        # Stop and cleanup timer
        if self._timer:
            try:
                self._timer.stop()
                self._timer.deleteLater()
            except Exception as e:
                self.logger.warning(f"Timer cleanup error: {e}")
            finally:
                self._timer = None

        self.logger.info("✅ Memory monitoring stopped")

    # ==================== MEASUREMENTS ====================

    def get_memory_mb(self) -> float:
        """Get current memory usage in MB."""
        try:
            if not self._process:
                return 0.0
            return self._process.memory_info().rss / (1024 * 1024)
        except Exception as e:
            self.logger.error(f"Memory read failed: {e}")
            return 0.0

    def get_cpu_percent(self) -> float:
        """
        Get accurate CPU percentage with caching.
        Uses baseline established in __init__.
        """
        try:
            if not self._process:
                return 0.0

            current_time = time.time()

            # Use cached value if checked recently
            if current_time - self._last_cpu_check < self._cpu_check_interval:
                return self._cached_cpu

            # Get CPU percentage (non-blocking after baseline)
            cpu_value = self._process.cpu_percent(interval=None)

            # Update cache
            self._last_cpu_check = current_time

            # Only update if we got a real reading
            if cpu_value > 0 or self._cached_cpu == 0:
                self._cached_cpu = cpu_value

            return self._cached_cpu

        except Exception as e:
            self.logger.error(f"CPU measurement failed: {e}")
            return self._cached_cpu

    def get_status(self) -> dict:
        """
        Get current memory and CPU status.
        Thread-safe and always succeeds.
        """
        try:
            memory_mb = self.get_memory_mb()
            cpu_percent = self.get_cpu_percent()

            # Determine trend
            trend = "↑" if self._is_increasing() else "↓"

            # Determine status
            if memory_mb >= self.CRITICAL_THRESHOLD:
                status = "CRITICAL"
            elif memory_mb >= self.WARNING_THRESHOLD:
                status = "WARNING"
            else:
                status = "OK"

            return {
                "memory_mb": round(memory_mb, 1),
                "cpu_percent": round(cpu_percent, 1),
                "status": status,
                "trend": trend,
                "monitoring": self._monitoring
            }

        except Exception as e:
            self.logger.error(f"get_status() failed: {e}")
            return {
                "memory_mb": 0,
                "cpu_percent": 0,
                "status": "ERROR",
                "trend": "?",
                "monitoring": False
            }

    # ==================== MONITORING LOOP ====================

    def _check_memory(self):
        """
        Main monitoring loop - checks memory and triggers cleanup.
        Thread-safe and protected against errors.
        """
        try:
            # Get current memory
            mb = self.get_memory_mb()

            # Update history
            with self._state_lock:
                self._memory_history.append(mb)
                if len(self._memory_history) > self._max_history:
                    self._memory_history.pop(0)

            # Get CPU
            cpu = self.get_cpu_percent()

            # Determine if increasing
            increasing = self._is_increasing()

            # Periodic logging
            if len(self._memory_history) % 5 == 0:
                self.logger.info(
                    f"Memory: {mb:.1f}MB ({'↑' if increasing else '↓'}), "
                    f"CPU: {cpu:.1f}%"
                )

            # Check thresholds
            if mb >= self.CRITICAL_THRESHOLD:
                self.memory_critical.emit(int(mb))
                self._emit_soft_cleanup("CRITICAL", mb)
                self._trigger_aggressive_cleanup(mb)
                self._set_interval(self.HIGH_LOAD_INTERVAL)

            elif mb >= self.WARNING_THRESHOLD:
                self.memory_warning.emit(int(mb))
                self._emit_soft_cleanup("WARNING", mb)
                if increasing:
                    self._trigger_standard_cleanup(mb)
                self._set_interval(self.HIGH_LOAD_INTERVAL)

            else:
                self._set_interval(self.NORMAL_INTERVAL)

        except Exception as e:
            self.logger.error(f"Memory check failed: {e}")

    # ==================== CLEANUP ====================

    def _emit_soft_cleanup(self, reason: str, mb: float):
        """
        Emit soft cleanup signal to frontend.
        Always succeeds, multiple fallbacks.
        """
        try:
            # Try primary method
            if self._bridge_server and hasattr(self._bridge_server, "emit_soft_cleanup"):
                self._bridge_server.emit_soft_cleanup(reason, mb)
                self.logger.debug(f"Soft cleanup emitted: {reason} @ {mb:.1f}MB")
                return

            # Fallback: direct signal
            if self._bridge_server and hasattr(self._bridge_server, "emit_signal_ws"):
                self._bridge_server.emit_signal_ws("tiktok_soft_cleanup", {
                    "ts": int(time.time()),
                    "reason": reason,
                    "memory": round(float(mb), 1)
                })
                self.logger.debug(f"Soft cleanup via direct signal: {reason}")
                return

            self.logger.debug("No bridge available for soft cleanup")

        except Exception as e:
            self.logger.warning(f"Soft cleanup emit failed: {e}")

    def _trigger_standard_cleanup(self, mb: float):
        """
        Standard (light) cleanup with operation guard.
        """
        if not self._can_cleanup():
            return

        with self._state_lock:
            if self._cleanup_in_progress:
                return
            self._cleanup_in_progress = True

        try:
            self.logger.warning(f"⚠️ Standard cleanup @ {mb:.1f}MB")

            # Emit to frontend
            self._emit_soft_cleanup("STANDARD", mb)

            # Backend cleanup
            if self._memory_utils and hasattr(self._memory_utils, 'light_cleanup'):
                self._memory_utils.light_cleanup()

            # TikTok message cleanup
            if self._client_manager and hasattr(self._client_manager, 'purge_old_messages'):
                purged = self._client_manager.purge_old_messages(keep_last=50)
                self.logger.info(f"Purged {purged} old messages")

            # Light GC
            gc.collect(generation=0)

        except Exception as e:
            self.logger.error(f"Standard cleanup failed: {e}")
        finally:
            with self._state_lock:
                self._cleanup_in_progress = False

    def _trigger_aggressive_cleanup(self, mb: float):
        """
        Aggressive cleanup with operation guard.
        """
        if not self._can_cleanup():
            return

        with self._state_lock:
            if self._cleanup_in_progress:
                return
            self._cleanup_in_progress = True

        try:
            self.logger.error(f"🚨 Aggressive cleanup @ {mb:.1f}MB")

            # Emit to frontend
            self._emit_soft_cleanup("AGGRESSIVE", mb)

            # Backend cleanup
            if self._memory_utils and hasattr(self._memory_utils, 'purge_memory'):
                self._memory_utils.purge_memory(aggressive=True)

            # TikTok resource cleanup
            if self._client_manager:
                if hasattr(self._client_manager, 'force_cleanup'):
                    self._client_manager.force_cleanup()
                if hasattr(self._client_manager, 'purge_old_messages'):
                    self._client_manager.purge_old_messages(keep_last=20)

            # Full GC
            gc.collect()

        except Exception as e:
            self.logger.error(f"Aggressive cleanup failed: {e}")
        finally:
            with self._state_lock:
                self._cleanup_in_progress = False

    # ==================== HELPERS ====================

    def _can_cleanup(self) -> bool:
        """Check if cleanup is allowed (cooldown check)."""
        with self._state_lock:
            now = time.time()
            if now - self._last_cleanup < self._cleanup_cooldown:
                return False
            self._last_cleanup = now
            return True

    def _is_increasing(self) -> bool:
        """Check if memory is trending upward."""
        with self._state_lock:
            if len(self._memory_history) < 3:
                return False
            recent_avg = sum(self._memory_history[-3:]) / 3
            old_avg = sum(self._memory_history[:3]) / 3
            return recent_avg > old_avg

    def _set_interval(self, seconds: int):
        """Adjust monitoring interval."""
        if self._timer and self._timer.interval() != seconds * 1000:
            self._timer.setInterval(seconds * 1000)

    # ==================== DIAGNOSTIC ====================

    def test_cpu_measurement(self):
        """Manual CPU test for diagnostics."""
        self.logger.info("=" * 70)
        self.logger.info("CPU MEASUREMENT TEST")
        self.logger.info("=" * 70)

        try:
            import os

            self.logger.info(f"PID: {os.getpid()}")
            self.logger.info(f"Process: {self._process}")

            # CPU times
            cpu_times = self._process.cpu_times()
            self.logger.info(f"User time: {cpu_times.user:.2f}s")
            self.logger.info(f"System time: {cpu_times.system:.2f}s")

            # Multiple samples
            self.logger.info("\nContinuous samples:")
            for i in range(5):
                time.sleep(1)
                cpu = self._process.cpu_percent(interval=None)
                self.logger.info(f"  Sample {i + 1}: {cpu:.1f}%")

            self.logger.info("=" * 70)
            self.logger.info("TEST COMPLETE")
            self.logger.info("=" * 70)

        except Exception as e:
            self.logger.error(f"CPU test failed: {e}")

    def get_debug_info(self) -> dict:
        """Get debug information."""
        with self._state_lock:
            return {
                "monitoring": self._monitoring,
                "cleanup_in_progress": self._cleanup_in_progress,
                "memory_mb": self.get_memory_mb(),
                "cpu_percent": self.get_cpu_percent(),
                "history_length": len(self._memory_history),
                "last_cleanup": self._last_cleanup,
                "services_bound": {
                    "bridge": self._bridge_server is not None,
                    "memory_utils": self._memory_utils is not None,
                    "client_manager": self._client_manager is not None
                }
            }

    # ==================== CLEANUP ====================

    def cleanup(self):
        """Complete cleanup on shutdown."""
        self.logger.info("Cleaning up MemoryMonitor...")
        self.stop_monitoring()

        with self._state_lock:
            self._memory_history.clear()

        self.logger.info("✅ MemoryMonitor cleanup complete")
