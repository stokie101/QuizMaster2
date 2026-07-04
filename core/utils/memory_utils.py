import gc
import logging

logging.getLogger('memory.utils.memory_utils').setLevel(logging.WARNING)
import platform
import psutil
import threading
import time
from typing import Dict, List, Any, Optional, Callable
from PySide6.QtCore import Signal, QTimer, QObject
from core.services.service_locator import ServiceLocator


class AppMetrics:
    """Class to store application metrics data."""

    def __init__(self, memory_mb: float = 0.0, cpu_percent: float = 0.0,
                 memory_percent: float = 0.0, timestamp: Optional[float] = None):
        """
        Initialize AppMetrics with provided values.

        Args:
            memory_mb: Memory usage in MB
            cpu_percent: CPU usage percentage
            memory_percent: Memory usage as percentage of system memory
            timestamp: Timestamp of the metrics (defaults to current time)
        """
        self.memory_mb = memory_mb
        self.cpu_percent = cpu_percent
        self.memory_percent = memory_percent
        self.timestamp = timestamp if timestamp is not None else time.time()


class OptimizedRateLimiter:
    """
    Rate limiter that optimizes resource usage.
    """

    def __init__(self, max_calls: int = 1, time_window: float = 1.0):
        """
        Initialize the rate limiter.

        Args:
            max_calls: Maximum number of calls allowed in the time window
            time_window: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
        self._lock = threading.RLock()

    def can_proceed(self) -> bool:
        """
        Check if a call can proceed based on the rate limit.

        Returns:
            bool: True if the call can proceed, False otherwise
        """
        with self._lock:
            now = time.time()

            # Remove expired calls
            self.calls = [t for t in self.calls if now - t < self.time_window]

            # Check if we're under the limit
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True

            return False


class AppMetricsCollector:
    """
    Singleton collector for application metrics.
    """

    _instance = None
    _lock = threading.RLock()

    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        """Initialize the metrics collector."""
        if AppMetricsCollector._instance is not None:
            raise RuntimeError("AppMetricsCollector is a singleton. Use get_instance() instead.")

        self._current_metrics = AppMetrics()
        self._collection_active = False

    def start_collection(self):
        """Start metrics collection."""
        self._collection_active = True

    def stop_collection(self):
        """Stop metrics collection."""
        self._collection_active = False

    def update_metrics(self, metrics: AppMetrics):
        """
        Update current metrics.

        Args:
            metrics: New metrics data
        """
        self._current_metrics = metrics

    def get_current_metrics(self) -> AppMetrics:
        """
        Get current metrics.

        Returns:
            AppMetrics: Current application metrics
        """
        return self._current_metrics


class MemoryUtils(QObject):
    """
    Enhanced Memory Utility that combines functionality from:
    - MemoryManager
    - MemoryUtils
    - MemoryPurgeUtility
    - HealthCalculator
    - Common utilities

    This class provides comprehensive memory management for the entire application.
    """

    # Signals
    memory_purged = Signal(dict)  # Memory purge statistics
    resource_cleaned = Signal(dict)  # Resource cleanup statistics
    memory_usage_updated = Signal(dict)  # Memory usage update
    memory_warning = Signal(str)  # Memory warning
    memory_critical = Signal(str)  # Memory critical alert
    _cleanup_in_progress = False

    _instance = None
    _lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> 'MemoryUtils':
        """Get singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        """Initialize the enhanced memory utility."""
        # Check singleton FIRST - before ANY attribute assignment
        if MemoryUtils._instance is not None:
            raise RuntimeError("MemoryUtils is a singleton. Use get_instance() instead.")

        super().__init__()
        self.logger = logging.getLogger(__name__)

        # Initialize service locator reference
        self._service_locator = None

        # Initialize quiz state tracking
        self._is_quiz_active = False

        # Initialize cleanup state flag
        self._cleanup_in_progress = False

        # Initialize signal connection tracking
        self._signal_connection_lock = threading.RLock()
        self._connected_signals = set()

        # Initialize cache-related attributes
        self._cpu_cache_duration = 2.0
        self._cpu_cache = 0.0
        self._cpu_cache_time = 0
        self._cpu_process = None

        # Initialize memory monitoring attributes
        self._memory_threshold_mb = None
        self._memory_monitor_timer = None
        self._memory_process = None

        # Initialize connection/signal timers
        self._connection_retry_timer = None
        self._connect_to_signals_timer = None

        # Initialize memory tracking
        self._current_metrics = AppMetrics()
        self._metrics_history = []
        self._max_history_size = 100
        self._previous_type_counts = {}

        # Initialize purge settings
        self._purge_enabled = False
        self._purge_interval = 300000  # 5 minutes
        self._purge_timer = QTimer(self)
        self._purge_timer.timeout.connect(self._on_purge_timer)

        # Initialize resource cleanup settings
        self._resource_cleanup_enabled = False
        self._resource_cleanup_interval = 600000  # 10 minutes
        self._resource_cleanup_timer = QTimer(self)
        self._resource_cleanup_timer.timeout.connect(self._on_resource_cleanup_timer)

        # Initialize chat message cleanup settings
        self._chat_cleanup_enabled = False
        self._chat_cleanup_interval = 120000  # 2 minutes
        self._chat_cleanup_timer = QTimer(self)
        self._chat_cleanup_timer.timeout.connect(self._on_chat_cleanup_timer)
        self._chat_message_limit = 500  # Maximum number of chat messages to keep
        self._chat_message_age_limit = 300  # Maximum age of chat messages in seconds

        # Initialize tracked resources
        self._tracked_resources = {}
        self._max_tracked_resources = 1000  # Limit to prevent unbounded growth

        # Health calculation thresholds
        self._warning_threshold = 70
        self._critical_threshold = 90

        # Window widget support
        self._window_widget = None
        self._window_update_timer = None

        # Initialize rate limiters
        self._memory_stats_rate_limiter = None
        self._cached_memory_stats = {}

        # Set the singleton instance
        MemoryUtils._instance = self

        # Now attempt to connect to signals (after all attributes are initialized)
        self._connect_to_signals()

        self.logger.info("MemoryUtils initialized")

    def _schedule_retry_connection(self):
        """Schedule a retry for connecting to QuizSignals."""
        if not hasattr(self, '_connection_retry_timer') or self._connection_retry_timer is None:
            self._connection_retry_timer = QTimer(self)
            self._connection_retry_timer.setSingleShot(True)

        self._connection_retry_timer.start(5000)  # Retry in 5 seconds

    def start_memory_monitoring(self, threshold_mb: float = 500.0):
        """
        Start monitoring memory usage and trigger cleanup when threshold is exceeded.

        Args:
            threshold_mb: Memory threshold in MB to trigger cleanup
        """
        if not hasattr(self, '_memory_monitor_timer'):
            self._memory_monitor_timer = QTimer(self)
            self._memory_monitor_timer.timeout.connect(self._check_memory_threshold)

        self._memory_threshold_mb = threshold_mb
        self._memory_monitor_timer.start(30000)  # Check every 30 seconds
        self.logger.info(f"Memory monitoring started with threshold {threshold_mb} MB")

    def _check_memory_threshold(self):
        """Check if memory usage exceeds threshold and trigger cleanup if needed."""
        try:
            memory_info = self.get_memory_usage()
            memory_mb = memory_info.get('rss', 0) / (1024 * 1024)

            if memory_mb > self._memory_threshold_mb:
                pressure = self.get_memory_pressure()

                if pressure == 'critical':
                    self.logger.warning(f"Critical memory usage detected: {memory_mb:.1f} MB")
                elif pressure == 'high' and not self._is_quiz_active:
                    self.logger.warning(f"High memory usage detected: {memory_mb:.1f} MB")
                    self.purge_memory(aggressive=True)
                elif pressure in ['medium', 'high'] and not self._is_quiz_active:
                    self.logger.info(f"Moderate memory usage detected: {memory_mb:.1f} MB")
                    self.purge_memory(aggressive=False)

        except Exception as e:
            self.logger.error(f"Error checking memory threshold: {e}")

    def _on_quiz_started(self):
        """Handler for when the quiz starts."""
        self.logger.info("Quiz has started. MemoryUtils will be less aggressive.")
        self._is_quiz_active = True

    def _on_quiz_ended(self):
        """Handler for when the quiz ends."""
        self.logger.info("Quiz has ended. Resuming normal MemoryUtils operations.")
        self._is_quiz_active = False
        # Optionally, run an aggressive cleanup now that the quiz is over.
        QTimer.singleShot(1000, self.cleanup_after_quiz)

    def _get_service_locator(self):
        """Get service locator instance safely with retry logic."""
        if self._service_locator is None:
            try:
                self._service_locator = ServiceLocator.get_instance()
            except Exception as e:
                self.logger.debug(f"ServiceLocator not yet available: {e}")
                return None
        return self._service_locator

    # Memory Usage Functions
    def get_memory_usage(self) -> Dict[str, int]:
        """Get current process memory usage."""
        try:
            # Fallback to direct psutil usage
            process = psutil.Process()
            memory_info = process.memory_info()

            return {
                'rss': memory_info.rss,  # Resident Set Size
                'vms': memory_info.vms,  # Virtual Memory Size
                'shared': getattr(memory_info, 'shared', 0),
                'text': getattr(memory_info, 'text', 0),
                'data': getattr(memory_info, 'data', 0),
                'percent': process.memory_percent(),
                'available': psutil.virtual_memory().available,
                'total': psutil.virtual_memory().total
            }
        except Exception as e:
            self.logger.error(f"Error getting memory usage: {e}")
            return {'rss': 0, 'vms': 0, 'shared': 0, 'text': 0, 'data': 0, 'percent': 0}

    def get_system_memory_info(self) -> Dict[str, Any]:
        """Get system memory information."""
        try:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return {
                'total': mem.total,
                'available': mem.available,
                'used': mem.used,
                'free': mem.free,
                'percent': mem.percent,
                'swap_total': swap.total,
                'swap_used': swap.used,
                'swap_free': swap.free,
                'swap_percent': swap.percent
            }
        except Exception as e:
            self.logger.error(f"Error getting system memory info: {e}")
            return {}

    def get_cpu_usage(self) -> float:
        """Get current process CPU utilization as a percentage with proper caching."""
        try:
            # Initialize cache if needed
            if not hasattr(self, '_cpu_cache'):
                self._cpu_cache = 0.0
                self._cpu_cache_time = 0
                self._cpu_cache_duration = 1.0  # ✅ REDUCED: Cache for only 1 second
                self._cpu_process = None
                self._cpu_last_interval = 0  # ✅ NEW: Track last interval call

            current_time = time.time()

            # Check if we have a cached value that's still valid
            if (current_time - self._cpu_cache_time) < self._cpu_cache_duration:
                return self._cpu_cache

            # Initialize process if needed
            if self._cpu_process is None:
                self._cpu_process = psutil.Process()

            # ✅ CRITICAL FIX: Use interval-based measurement
            # First call needs a baseline, subsequent calls compare to previous
            time_since_last = current_time - self._cpu_last_interval

            if time_since_last < 0.1:
                # Too soon since last call, return cached value
                return self._cpu_cache

            # Use appropriate interval based on how long since last call
            if self._cpu_last_interval == 0:
                # First call - use small interval to get initial reading
                cpu_percent = self._cpu_process.cpu_percent(interval=0.1)
            else:
                # Subsequent calls - use None to get value since last call
                cpu_percent = self._cpu_process.cpu_percent(interval=None)

                # If we get 0, force a small interval measurement
                if cpu_percent == 0.0 and time_since_last > 0.5:
                    cpu_percent = self._cpu_process.cpu_percent(interval=0.05)

            # Update cache and tracking
            self._cpu_cache = cpu_percent
            self._cpu_cache_time = current_time
            self._cpu_last_interval = current_time

            return cpu_percent

        except Exception as e:
            self.logger.error(f"Error getting CPU usage: {e}")
            return getattr(self, '_cpu_cache', 0.0)  # Return cached value on error

    def get_window_metrics(self) -> Dict[str, Any]:
        """
        Get metrics specifically formatted for window widgets.
        ✅ FIXED: Proper CPU measurement
        """
        try:
            # Get current memory usage
            memory_info = self.get_memory_usage()
            memory_mb = memory_info.get('rss', 0) / (1024 * 1024)

            # ✅ FIX: Get CPU usage with forced fresh reading
            cpu_percent = self.get_cpu_usage()

            # ✅ FALLBACK: If still 0, try system-wide CPU
            if cpu_percent == 0.0:
                try:
                    cpu_percent = psutil.cpu_percent(interval=0.1)
                except Exception:
                    pass

            # Get system memory percentage
            system_memory = self.get_system_memory_info()
            memory_percent = system_memory.get('percent', 0)

            # Calculate health
            health_info = self.calculate_health(memory_mb, memory_percent, cpu_percent)

            # Format for window display
            return {
                'memory_mb': memory_mb,
                'memory_formatted': self.format_size(memory_info.get('rss', 0)),
                'memory_percent': memory_percent,
                'cpu_percent': cpu_percent,
                'health_score': health_info.get('score', 0),
                'health_status': health_info.get('status', 'Unknown'),
                'health_color': health_info.get('color', '#888888'),
                'recommendations': self.get_recommendations(health_info)
            }
        except Exception as e:
            self.logger.error(f"Error getting window metrics: {e}")
            return {
                'memory_mb': 0,
                'memory_formatted': '0 MB',
                'memory_percent': 0,
                'cpu_percent': 0,
                'health_score': 0,
                'health_status': 'Error',
                'health_color': '#ff0000',
                'recommendations': ['Error getting metrics']
            }

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Format size in bytes to human-readable string."""
        if size_bytes < 0:
            return "Invalid size"

        if size_bytes < 1024:
            return f"{size_bytes} B"

        # Start with bytes and go up
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        size = float(size_bytes)
        unit_index = 0

        # Keep dividing by 1024 until we get a reasonable number
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        return f"{size:.2f} {units[unit_index]}"

    @staticmethod
    def format_size_kb_input(size_kb: int) -> str:
        """Format size assuming input is in KB (for systems where psutil returns KB)."""
        if size_kb < 0:
            return "Invalid size"

        if size_kb < 1024:
            return f"{size_kb} KB"

        # Start with KB and go up
        units = ['KB', 'MB', 'GB', 'TB', 'PB']
        size = float(size_kb)
        unit_index = 0

        # Keep dividing by 1024 until we get a reasonable number
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        return f"{size:.2f} {units[unit_index]}"

    # Memory Purging Functions
    def start_purging(self) -> None:
        """Start memory purging."""
        with threading.RLock():
            if not self._purge_enabled:
                self._purge_enabled = True
                self._purge_timer.start(self._purge_interval)
                self.logger.info(f"Memory purging started with interval {self._purge_interval}ms")

    def stop_purging(self) -> None:
        """Stop memory purging."""
        with threading.RLock():
            if self._purge_enabled:
                self._purge_timer.stop()
                self._purge_enabled = False
                self.logger.info("Memory purging stopped")

    def set_purge_interval(self, interval: int) -> None:
        """Set memory purge interval in milliseconds."""
        with threading.RLock():
            self._purge_interval = max(60000, interval)  # Minimum 1 minute
            if self._purge_enabled:
                self._purge_timer.setInterval(self._purge_interval)
            self.logger.info(f"Memory purge interval set to {self._purge_interval}ms")

    def _on_purge_timer(self) -> None:
        """Handle purge timer event."""
        self.purge_memory()

    def purge_memory(self, aggressive: bool = False) -> Dict[str, Any]:
        """Purge memory with thread-safe recursion prevention."""
        with self._lock:  # Use class-level lock
            if self._cleanup_in_progress:
                self.logger.warning("Memory purge already in progress")
                return {'error': 'Cleanup already in progress'}

            self._cleanup_in_progress = True

        try:
            self.logger.info(f"Starting memory purge (aggressive={aggressive})")

            # Get memory usage before purging
            before = self.get_memory_usage()

            # Run garbage collection first
            gc_result = self._run_gc(aggressive)

            # Clear Python caches
            self._clear_python_caches()

            # Try to release memory back to the OS
            self._release_memory_to_os()

            # Purge specific resources
            resources_purged = self._purge_resources(aggressive)

            # Get memory usage after purging
            after = self.get_memory_usage()

            # Calculate memory saved
            memory_saved = before.get('rss', 0) - after.get('rss', 0)

            result = {
                'memory_before': before.get('rss', 0),
                'memory_after': after.get('rss', 0),
                'memory_saved': memory_saved,
                'memory_saved_formatted': self.format_size(memory_saved),
                'gc_result': gc_result,
                'resources_purged': resources_purged
            }

            # Log purge results
            self.logger.info(f"Memory purge saved {self.format_size(memory_saved)}")

            # Emit signal
            self.memory_purged.emit(result)

            return result
        except Exception as e:  # ← FIXED: Align with 'try'
            self.logger.error(f"Error purging memory: {e}")
            return {'error': str(e)}
        finally:
            with self._lock:
                self._cleanup_in_progress = False

    def _run_gc(self, aggressive: bool = False) -> Dict[str, Any]:
        """
        Run garbage collection.

        Args:
            aggressive: Whether to use aggressive garbage collection

        Returns:
            Dict with garbage collection statistics
        """
        try:
            # Run full collection
            start_time = time.time()

            # Get object count before collection
            before_count = len(gc.get_objects())

            # Run multiple GC passes for more thorough collection
            collected = 0

            if aggressive:
                # Aggressive collection: multiple passes with different generations
                for _ in range(2):  # Two full cycles
                    for i in range(3):  # All three generations
                        collected += gc.collect(i)
                        time.sleep(0.05)  # Small delay between collections
            else:
                # Standard collection: one pass of each generation
                for i in range(3):
                    collected += gc.collect(i)
                    time.sleep(0.02)  # Smaller delay

            # Get object count after collection
            after_count = len(gc.get_objects())

            # Calculate duration
            end_time = time.time()
            duration = end_time - start_time

            result = {
                'collected': collected,
                'duration': duration,
                'objects_before': before_count,
                'objects_after': after_count,
                'objects_diff': before_count - after_count
            }

            # Log GC results
            self.logger.info(f"GC collected {collected} objects in {duration:.2f}s")

            return result
        except Exception as e:
            self.logger.error(f"Error running GC: {e}")
            return {'error': str(e)}

    def _clear_python_caches(self) -> None:
        """Clear various Python caches to free memory."""
        try:
            import sys

            # Clear function caches
            for module_name, module in sys.modules.items():
                if hasattr(module, 'cache_clear'):
                    try:
                        module.cache_clear()
                    except Exception:
                        pass

            # Clear regex cache
            import re
            if hasattr(re, '_cache'):
                re._cache.clear()
            if hasattr(re, '_compile_repl'):
                re._compile_repl.cache_clear()

            # Clear path cache
            import os.path
            if hasattr(os.path, 'cache_clear'):
                os.path.cache_clear()

            # Clear type lookup cache
            if hasattr(sys, '_clear_type_cache'):
                sys._clear_type_cache()

            # Clear urllib cache
            try:
                import urllib.parse
                if hasattr(urllib.parse, 'clear_cache'):
                    urllib.parse.clear_cache()
            except Exception:
                pass

            # Clear importlib cache
            try:
                import importlib
                if hasattr(importlib, 'invalidate_caches'):
                    importlib.invalidate_caches()
            except Exception:
                pass

            # Clear pickle cache
            try:
                import pickle
                if hasattr(pickle, '_Pickler') and hasattr(pickle._Pickler, 'dispatch'):
                    pickle._Pickler.dispatch.clear()
            except Exception:
                pass

            # Clear json cache
            try:
                import json
                if hasattr(json, '_default_decoder'):
                    json._default_decoder.memo.clear()
            except Exception:
                pass

            # Clear functools lru_cache
            try:
                import functools
                if hasattr(functools, '_CacheInfo'):
                    # Find all lru_cache decorated functions and clear them
                    for module_name, module in sys.modules.items():
                        for attr_name in dir(module):
                            try:
                                attr = getattr(module, attr_name)
                                if hasattr(attr, 'cache_clear') and hasattr(attr, 'cache_info'):
                                    attr.cache_clear()
                            except Exception:
                                pass
            except Exception:
                pass

            self.logger.info("Cleared Python caches")
        except Exception as e:
            self.logger.error(f"Error clearing Python caches: {e}")

    def _release_memory_to_os(self) -> None:
        """Attempt to release memory back to the OS using platform-specific methods."""
        try:
            # Run multiple GC passes
            for _ in range(3):
                gc.collect()

            # Try to release memory to OS using platform-specific methods
            system = platform.system()

            if system == 'Linux':
                try:
                    import ctypes
                    libc = ctypes.CDLL('libc.so.6')
                    if hasattr(libc, 'malloc_trim'):
                        libc.malloc_trim(0)
                        self.logger.info("Released memory to OS using malloc_trim")
                except Exception as e:
                    self.logger.error(f"Error releasing memory to OS (Linux): {e}")

            elif system == 'Windows':
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    kernel32.SetProcessWorkingSetSize(-1, -1)
                    self.logger.info("Released memory to OS using SetProcessWorkingSetSize")
                except Exception as e:
                    self.logger.error(f"Error releasing memory to OS (Windows): {e}")

            # For macOS, there's no direct equivalent, but we can try to force memory compaction
            elif system == 'Darwin':  # macOS
                try:
                    # Run extra GC cycles
                    for _ in range(5):
                        gc.collect()
                    self.logger.info("Attempted memory compaction on macOS")
                except Exception as e:
                    self.logger.error(f"Error compacting memory on macOS: {e}")

        except Exception as e:
            self.logger.error(f"Error releasing memory to OS: {e}")

    def _purge_resources(self, aggressive: bool = False) -> Dict[str, Any]:
        """
        Purge specific resources across the entire application.

        Args:
            aggressive: Whether to use aggressive resource purging

        Returns:
            Dict with resource purge statistics
        """
        try:
            resources_purged = {
                'chat_messages': 0,
                'other_resources': 0,
                'components_cleaned': []
            }

            # Get service locator
            service_locator = self._get_service_locator()
            if not service_locator:
                return resources_purged

            # Check if quiz is active
            if self._is_quiz_active:
                # During quiz, only do minimal cleanup
                self.logger.info("Quiz is active - doing minimal resource purging")

                # Still clean TikTok chat messages as they can accumulate quickly
                tiktok_manager = service_locator.get_service("TikTokLiveManager")
                if tiktok_manager:
                    try:
                        if hasattr(tiktok_manager, 'purge_old_chat_messages'):
                            # Use a specialized method that only purges old messages
                            messages_purged = tiktok_manager.purge_old_chat_messages(keep_last=100)
                            resources_purged['chat_messages'] += messages_purged
                        elif hasattr(tiktok_manager, 'purge_chat_messages'):
                            messages_purged = tiktok_manager.purge_chat_messages()
                            resources_purged['chat_messages'] += messages_purged
                    except Exception as e:
                        self.logger.error(f"Error purging TikTok chat messages: {e}")

                # Run garbage collection
                gc.collect()

                return resources_purged

            # Clean TikTok chat messages
            tiktok_manager = service_locator.get_service("TikTokLiveManager")
            if tiktok_manager:
                try:
                    if hasattr(tiktok_manager, 'purge_chat_messages'):
                        messages_purged = tiktok_manager.purge_chat_messages()
                        resources_purged['chat_messages'] += messages_purged
                        resources_purged['components_cleaned'].append("TikTokLiveManager.purge_chat_messages")
                except Exception as e:
                    self.logger.error(f"Error purging TikTok chat messages: {e}")

            safe_services = {
                "CSVHandler": ["clear_cache", "cleanup_temp_files"],
                "ConfigManager": ["clear_cache"],
                "ThemeApplicator": ["clear_cache"],
                "AudioHandler": ["clear_cache", "cleanup_resources"],
                "TikTokLiveManager": ["clear_message_cache", "cleanup_resources"],
                "SignalTracker": ["clear_history"],

                "AudioVideoManager": ["clear_cache", "cleanup_resources"],
                "AvatarManager": ["clear_cache"],
                "ConnectionMonitor": ["clear_history"],
                "ErrorHandler": ["clear_error_history"],

                "AsyncHelper": ["clear_completed_tasks"],
            }

            for service_name, methods in safe_services.items():
                service = service_locator.get_service(service_name)

                if service is None and service_name in ["", ""]:
                    for alt_name in ["", ""]:
                        if alt_name != service_name:
                            service = service_locator.get_service(alt_name)
                            if service:
                                self.logger.debug(f"Found OBS service as {alt_name} instead of {service_name}")
                                break

                if service:
                    for method_name in methods:
                        try:
                            if hasattr(service, method_name):
                                method = getattr(service, method_name)
                                if callable(method):
                                    # For OBS services, only call methods that don't affect active connections
                                    if "OBS" in service_name and method_name in ["disconnect", "cleanup"]:
                                        self.logger.debug(
                                            f"Skipping {method_name} on {service_name} to preserve connection")
                                        continue

                                    result = method()
                                    if isinstance(result, int):
                                        resources_purged['other_resources'] += result
                                    else:
                                        resources_purged['other_resources'] += 1
                                    resources_purged['components_cleaned'].append(f"{service_name}.{method_name}")
                        except Exception as e:
                            self.logger.error(f"Error calling {method_name} on {service_name}: {e}")
            self._clear_safe_caches(resources_purged)
            self._clean_avatar_cache(resources_purged)
            gc.collect()
            return resources_purged
        except Exception as e:
            self.logger.error(f"Error purging resources: {e}")
            return {'error': str(e)}

    def light_cleanup(self):
        """Perform light cleanup without destroying active services."""
        try:
            self.logger.info("Starting light memory cleanup...")

            import gc

            # Create resources dict for tracking
            resources_purged = {
                'chat_messages': 0,
                'other_resources': 0,
                'components_cleaned': []
            }

            self._clean_avatar_cache(resources_purged)  # ← FIXED: Correct method name
            self._clear_temp_data()

            collected = gc.collect()
            self.logger.info(f"Light cleanup: collected {collected} objects")

            return resources_purged  # Return the tracking dict

        except Exception as e:
            self.logger.error(f"Error during light cleanup: {e}")
            return {}

    def _clear_temp_data(self):
        """Clear temporary data without affecting services."""
        try:

            pass
        except Exception as e:
            self.logger.error(f"Error clearing temp data: {e}")

    def _clear_safe_caches(self, resources_purged):
        """Clear specific caches that are known to be safe."""
        try:
            # Clear Python caches
            import re
            if hasattr(re, '_cache'):
                re._cache.clear()
                resources_purged['other_resources'] += 1
                resources_purged['components_cleaned'].append("re._cache.clear")

            # Clear QPixmapCache
            try:
                from PySide6.QtGui import QPixmapCache
                QPixmapCache.clear()
                resources_purged['other_resources'] += 1
                resources_purged['components_cleaned'].append("QPixmapCache.clear")
            except Exception:
                pass

            # Clear urllib cache
            try:
                import urllib.parse
                if hasattr(urllib.parse, 'clear_cache'):
                    urllib.parse.clear_cache()
                    resources_purged['other_resources'] += 1
                    resources_purged['components_cleaned'].append("urllib.parse.clear_cache")
            except Exception:
                pass

            # Clear importlib cache
            try:
                import importlib
                if hasattr(importlib, 'invalidate_caches'):
                    importlib.invalidate_caches()
                    resources_purged['other_resources'] += 1
                    resources_purged['components_cleaned'].append("importlib.invalidate_caches")
            except Exception:
                pass

            # Clear json cache
            try:
                import json
                if hasattr(json, '_default_decoder') and hasattr(json._default_decoder, 'memo'):
                    json._default_decoder.memo.clear()
                    resources_purged['other_resources'] += 1
                    resources_purged['components_cleaned'].append("json._default_decoder.memo.clear")
            except Exception:
                pass

            # Clear functools lru_cache for specific modules
            try:
                import functools
                import sys

                safe_modules = ['re', 'json', 'urllib', 'os.path', 'pathlib']

                for module_name in safe_modules:
                    if module_name in sys.modules:
                        module = sys.modules[module_name]
                        for attr_name in dir(module):
                            try:
                                attr = getattr(module, attr_name)
                                if hasattr(attr, 'cache_clear') and hasattr(attr, 'cache_info'):
                                    attr.cache_clear()
                                    resources_purged['other_resources'] += 1
                                    resources_purged['components_cleaned'].append(
                                        f"{module_name}.{attr_name}.cache_clear")
                            except Exception:
                                pass
            except Exception:
                pass

            # Release memory to OS on Windows
            try:
                if platform.system() == 'Windows':
                    import ctypes
                    ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1)
                    resources_purged['other_resources'] += 1
                    resources_purged['components_cleaned'].append("SetProcessWorkingSetSize")
            except Exception:
                pass

        except Exception as e:
            self.logger.error(f"Error clearing safe caches: {e}")

    def _clean_avatar_cache(self, resources_purged):
        """Clean avatar cache files that are older than a certain threshold."""
        try:
            import os
            from datetime import datetime, timedelta

            # Get avatar cache directory
            avatar_cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'avatar_cache')

            if not os.path.exists(avatar_cache_dir):
                return

            # Keep files newer than this threshold
            threshold = datetime.now() - timedelta(days=7)

            # Count of removed files
            removed_count = 0

            # Iterate through files in the avatar cache directory
            for filename in os.listdir(avatar_cache_dir):
                file_path = os.path.join(avatar_cache_dir, filename)

                # Skip directories
                if os.path.isdir(file_path):
                    continue

                # Skip non-image files
                if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    continue

                # Get file modification time
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))

                    # Remove old files
                    if mtime < threshold:
                        os.remove(file_path)
                        removed_count += 1
                except Exception as e:
                    self.logger.debug(f"Error processing avatar cache file {filename}: {e}")

            if removed_count > 0:
                resources_purged['other_resources'] += removed_count
                resources_purged['components_cleaned'].append(f"avatar_cache_cleanup ({removed_count} files)")

        except Exception as e:
            self.logger.error(f"Error cleaning avatar cache: {e}")

    def _clear_component_caches(self, service_locator, resources_purged):
        """Clear caches from components without causing recursion."""
        try:
            # Get list of services safely
            if hasattr(service_locator, 'list_services'):
                service_names = service_locator.list_services()
            else:
                # If no list_services method, try common service names
                service_names = [
                    "QuizManager", "CSVHandler", "LeaderboardManager",
                    "TikTokLiveManager", "QuizDisplay",
                    "ConfigManager", "ThemeApplicator", "AudioManager"
                ]

            for component_name in service_names:
                try:
                    component = service_locator.get_service(component_name)
                    if component:
                        # Look for cache-like attributes and clear them
                        for attr_name in dir(component):
                            if (attr_name.endswith('_cache') or
                                    attr_name.endswith('_buffer') or
                                    attr_name.endswith('_history')):
                                try:
                                    attr = getattr(component, attr_name)
                                    if attr and hasattr(attr, 'clear') and callable(attr.clear):
                                        attr.clear()
                                        resources_purged['other_resources'] += 1
                                        resources_purged['components_cleaned'].append(
                                            f"{component_name}.{attr_name}.clear")
                                except Exception:
                                    pass
                except Exception as e:
                    self.logger.debug(f"Error clearing caches for {component_name}: {e}")

        except Exception as e:
            self.logger.error(f"Error clearing component caches: {e}")

    # Resource Cleanup Functions
    def start_resource_cleanup(self) -> None:
        """Start resource cleanup."""
        with threading.RLock():
            if not self._resource_cleanup_enabled:
                self._resource_cleanup_enabled = True
                self._resource_cleanup_timer.start(self._resource_cleanup_interval)
                self.logger.info(f"Resource cleanup started with interval {self._resource_cleanup_interval}ms")

    def stop_resource_cleanup(self) -> None:
        """Stop resource cleanup."""
        with threading.RLock():
            if self._resource_cleanup_enabled:
                self._resource_cleanup_timer.stop()
                self._resource_cleanup_enabled = False
                self.logger.info("Resource cleanup stopped")

    def set_resource_cleanup_interval(self, interval: int) -> None:
        """Set resource cleanup interval in milliseconds."""
        with threading.RLock():
            self._resource_cleanup_interval = max(60000, interval)  # Minimum 1 minute
            if self._resource_cleanup_enabled:
                self._resource_cleanup_timer.setInterval(self._resource_cleanup_interval)
            self.logger.info(f"Resource cleanup interval set to {self._resource_cleanup_interval}ms")

    def _on_resource_cleanup_timer(self) -> None:
        """Handle resource cleanup timer event."""
        self.cleanup_resources()

    def cleanup_resources(self) -> Dict[str, Any]:
        """
        Clean up resources with comprehensive approach.

        Returns:
            Dict with cleanup statistics
        """
        try:
            self.logger.info("Starting comprehensive resource cleanup")

            # Get memory usage before cleanup
            before = self.get_memory_usage()

            # Clean up resources
            resources_cleaned = self._cleanup_resources()

            # Run garbage collection
            gc_result = self._run_gc(False)

            # Get memory usage after cleanup
            after = self.get_memory_usage()

            # Calculate memory saved
            memory_saved = before.get('rss', 0) - after.get('rss', 0)

            result = {
                'memory_before': before.get('rss', 0),
                'memory_after': after.get('rss', 0),
                'memory_saved': memory_saved,
                'memory_saved_formatted': self.format_size(memory_saved),
                'gc_result': gc_result,
                'resources_cleaned': resources_cleaned
            }

            # Log cleanup results
            self.logger.info(f"Resource cleanup saved {self.format_size(memory_saved)}")

            # Emit signal
            self.resource_cleaned.emit(result)

            return result
        except Exception as e:
            self.logger.error(f"Error cleaning up resources: {e}")
            return {'error': str(e)}

    def _cleanup_resources(self) -> Dict[str, Any]:
        """Clean up resources."""
        try:
            resources_cleaned = {
                'chat_messages': 0,
                'other_resources': 0,
                'components_cleaned': []
            }

            # Clean up chat messages
            chat_messages_cleaned = self._cleanup_chat_messages()
            resources_cleaned['chat_messages'] = chat_messages_cleaned

            # Clean up other resources
            other_resources_cleaned = self._cleanup_other_resources(resources_cleaned)
            resources_cleaned['other_resources'] = other_resources_cleaned

            return resources_cleaned
        except Exception as e:
            self.logger.error(f"Error cleaning up resources: {e}")
            return {'error': str(e)}

    def _service_exists(self, service_name: str) -> bool | Callable | Any:
        """Check if a service exists in the service locator."""
        try:
            service_locator = self._get_service_locator()
            if not service_locator:
                return False

            # Check if service locator has a has_service method
            if hasattr(service_locator, 'has_service') and callable(service_locator.has_service):
                return service_locator.has_service(service_name)

            # Fallback: try to get the service and see if it's None
            return service_locator.get_service(service_name) is not None
        except Exception:
            return False

    def _cleanup_chat_messages(self) -> int:
        """
        Clean up chat messages from TikTok and other chat systems.

        Returns:
            int: Number of chat messages cleaned up
        """
        try:
            messages_cleaned = 0

            # Get service locator
            service_locator = self._get_service_locator()
            if not service_locator:
                return messages_cleaned

            # Get TikTokLiveManager
            tiktok_manager = service_locator.get_service("TikTokLiveManager")
            if tiktok_manager:
                # Check if TikTokLiveManager has a cleanup_chat_messages method
                if hasattr(tiktok_manager, 'cleanup_chat_messages') and callable(tiktok_manager.cleanup_chat_messages):
                    try:
                        messages_cleaned = tiktok_manager.cleanup_chat_messages()
                    except Exception as e:
                        self.logger.debug(f"Error cleaning up chat messages: {e}")

                # If no dedicated method, try to clean up chat messages manually
                elif hasattr(tiktok_manager, 'chat_messages') and tiktok_manager.chat_messages is not None:
                    # Get current message count
                    current_count = len(tiktok_manager.chat_messages) if hasattr(tiktok_manager.chat_messages,
                                                                                 '__len__') else 0

                    # If we have messages, clean up old ones
                    if current_count > 0:
                        # If it's a list with timestamps, remove messages older than the age limit
                        if hasattr(tiktok_manager.chat_messages, '__iter__'):
                            current_time = time.time()
                            new_messages = []
                            removed = 0

                            for msg in tiktok_manager.chat_messages:
                                # Check if message has a timestamp
                                if msg is not None and hasattr(msg, 'timestamp'):
                                    # Keep message if it's newer than the age limit
                                    if current_time - msg.timestamp <= self._chat_message_age_limit:
                                        new_messages.append(msg)
                                    else:
                                        removed += 1
                                else:
                                    # If no timestamp, keep the message
                                    new_messages.append(msg)

                            # Update chat messages
                            tiktok_manager.chat_messages = new_messages
                            messages_cleaned = removed

            chat_components = ["TikTokLiveManager"]
            for component_name in chat_components:
                component = service_locator.get_service(component_name)
                if component:
                    try:
                        if hasattr(component, 'cleanup_messages'):
                            cleaned = component.cleanup_messages()
                            if isinstance(cleaned, int):
                                messages_cleaned += cleaned
                        elif hasattr(component, 'clear_messages'):
                            component.clear_messages()
                            messages_cleaned += 1
                    except Exception as e:
                        self.logger.debug(f"Error cleaning messages from {component_name}: {e}")

            self.logger.info(f"Cleaned up {messages_cleaned} chat messages")
            return messages_cleaned
        except Exception as e:
            self.logger.error(f"Error cleaning up chat messages: {e}")
            return 0

    def _cleanup_other_resources(self, resources_cleaned) -> int:
        """
        Clean up other resources across the entire application.

        Args:
            resources_cleaned: Dictionary to track cleaned components

        Returns:
            int: Number of resources cleaned up
        """
        try:
            resources_cleaned_count = 0

            # Get service locator
            service_locator = self._get_service_locator()
            if not service_locator:
                return resources_cleaned_count

            # Get all available services
            all_services = []
            if hasattr(service_locator, 'list_services'):
                all_services = service_locator.list_services()
            else:
                # Fallback to common service names
                all_services = [
                    "QuizManager", "CSVHandler", "LeaderboardManager", "TikTokLiveManager",
                    "CircleTimerWidget", "QuizDisplay", "ConnectionManager",
                    "SettingsManager", "ThemeApplicator", "AudioVideoManager",
                ]

            # Clean up resources from all services
            for service_name in all_services:
                service = service_locator.get_service(service_name)
                if service:
                    try:
                        # Try different cleanup methods
                        if hasattr(service, 'cleanup_resources'):
                            cleaned = service.cleanup_resources()
                            if isinstance(cleaned, int):
                                resources_cleaned_count += cleaned
                                resources_cleaned['components_cleaned'].append(f"{service_name}.cleanup_resources")
                        elif hasattr(service, 'cleanup'):
                            service.cleanup()
                            resources_cleaned_count += 1
                            resources_cleaned['components_cleaned'].append(f"{service_name}.cleanup")
                        elif hasattr(service, 'clear_cache'):
                            service.clear_cache()
                            resources_cleaned_count += 1
                            resources_cleaned['components_cleaned'].append(f"{service_name}.clear_cache")

                        # Clean specific attributes that might hold resources
                        self._clean_component_attributes(service, service_name, resources_cleaned)

                    except Exception as e:
                        self.logger.error(f"Error cleaning up resources from {service_name}: {e}")

            self.logger.info(f"Cleaned up {resources_cleaned_count} other resources")
            return resources_cleaned_count
        except Exception as e:
            self.logger.error(f"Error cleaning up other resources: {e}")
            return 0

    def _clean_component_attributes(self, component, component_name, resources_cleaned):
        """Clean specific attributes that might hold resources."""
        try:
            # List of attribute names that might hold clearable resources
            resource_attributes = [
                'cache', 'buffer', 'history', 'messages', 'data_cache',
                'message_cache', 'chat_messages', 'message_history', 'items',
                'queue', 'pending', 'results', 'temp_data'
            ]

            cleaned_count = 0

            for attr_name in dir(component):
                # Check if attribute name contains any resource attribute name
                if any(resource_name in attr_name.lower() for resource_name in resource_attributes):
                    try:
                        attr = getattr(component, attr_name)
                        if attr and hasattr(attr, 'clear') and callable(attr.clear):
                            attr.clear()
                            cleaned_count += 1
                            resources_cleaned['components_cleaned'].append(f"{component_name}.{attr_name}.clear")
                    except Exception:
                        pass

            return cleaned_count
        except Exception as e:
            self.logger.debug(f"Error cleaning attributes for {component_name}: {e}")
            return 0

    # Chat Message Cleanup Functions
    def start_chat_cleanup(self) -> None:
        """Start chat message cleanup."""
        with threading.RLock():
            if not self._chat_cleanup_enabled:
                self._chat_cleanup_enabled = True
                self._chat_cleanup_timer.start(self._chat_cleanup_interval)
                self.logger.info(f"Chat message cleanup started with interval {self._chat_cleanup_interval}ms")

    def stop_chat_cleanup(self) -> None:
        """Stop chat message cleanup."""
        with threading.RLock():
            if self._chat_cleanup_enabled:
                self._chat_cleanup_timer.stop()
                self._chat_cleanup_enabled = False
                self.logger.info("Chat message cleanup stopped")

    def set_chat_cleanup_interval(self, interval: int) -> None:
        """Set chat message cleanup interval in milliseconds."""
        with threading.RLock():
            self._chat_cleanup_interval = max(30000, interval)  # Minimum 30 seconds
            if self._chat_cleanup_enabled:
                self._chat_cleanup_timer.setInterval(self._chat_cleanup_interval)
            self.logger.info(f"Chat message cleanup interval set to {self._chat_cleanup_interval}ms")

    def set_chat_message_limit(self, limit: int) -> None:
        """Set chat message limit."""
        with threading.RLock():
            self._chat_message_limit = max(100, limit)  # Minimum 100 messages
            self.logger.info(f"Chat message limit set to {self._chat_message_limit}")

    def set_chat_message_age_limit(self, limit: int) -> None:
        """Set chat message age limit in seconds."""
        with threading.RLock():
            self._chat_message_age_limit = max(60, limit)  # Minimum 60 seconds
            self.logger.info(f"Chat message age limit set to {self._chat_message_age_limit} seconds")

    def _on_chat_cleanup_timer(self) -> None:
        """Handle chat message cleanup timer event."""
        self.cleanup_chat_messages()

    def cleanup_chat_messages(self) -> Dict[str, Any]:
        """
        Clean up chat messages.

        Returns:
            Dict with cleanup statistics
        """
        try:
            self.logger.info("Starting chat message cleanup")

            # Clean up chat messages
            messages_cleaned = self._cleanup_chat_messages()

            result = {
                'messages_cleaned': messages_cleaned
            }

            # Log cleanup results
            self.logger.info(f"Chat message cleanup removed {messages_cleaned} messages")

            return result
        except Exception as e:
            self.logger.error(f"Error cleaning up chat messages: {e}")
            return {'error': str(e)}

    # In memory_utils.py, update cleanup methods

    def _purge_chat_messages_advanced(self) -> int:
        """Enhanced with lower limits for busy rooms"""
        messages_purged = 0
        service_locator = self._get_service_locator()
        if not service_locator:
            return messages_purged

        tiktok_manager = service_locator.get_service("TikTokLiveManager")
        if not tiktok_manager or not hasattr(tiktok_manager, 'client_manager'):
            return messages_purged

        client_manager = tiktok_manager.client_manager

        # Use aggressive limits from ClientManager
        with client_manager._message_cache_lock:
            old_size = len(client_manager._message_cache)
            if old_size > client_manager.MAX_CACHED_MESSAGES:
                client_manager._message_cache = client_manager._message_cache[-client_manager.MAX_CACHED_MESSAGES:]
                messages_purged = old_size - len(client_manager._message_cache)

        return messages_purged

    @staticmethod
    def _is_important_message(msg) -> bool:
        """Determine if a message is important and should be retained longer."""
        try:
            # Check message type or content for importance
            if hasattr(msg, 'type'):
                important_types = ['gift', 'follow', 'share', 'like']
                return msg.type.lower() in important_types

            if hasattr(msg, 'content'):
                # Keep messages with certain keywords
                important_keywords = ['@', 'gift', 'follow', 'subscribe']
                return any(keyword in msg.content.lower() for keyword in important_keywords)

            return False
        except Exception:
            return False

    # Health Calculation Functions
    @staticmethod
    def calculate_health(memory_mb: float, memory_percent: float, cpu_percent: float) -> Dict[str, Any]:
        """
        Calculate health status based on memory and CPU metrics.

        Args:
            memory_mb: Memory usage in MB
            memory_percent: Memory usage as percentage of system memory
            cpu_percent: CPU usage as percentage

        Returns:
            Dict with health status information
        """
        # Calculate health score
        health_score = 100
        status_text = "Healthy"
        color = "#00aa00"  # Green

        # Deduct points for high memory usage
        if memory_mb > 750:
            health_score -= 40
            status_text = "Critical"
            color = "#ff4444"  # Red
        elif memory_mb > 500:
            health_score -= 20
            status_text = "Warning"
            color = "#ffaa00"  # Orange

        # Deduct points for high CPU usage
        if cpu_percent > 80:
            health_score -= 30
            if status_text == "Healthy":
                status_text = "Critical"
                color = "#ff4444"
        elif cpu_percent > 50:
            health_score -= 15
            if status_text == "Healthy":
                status_text = "Warning"
                color = "#ffaa00"

        # Ensure minimum score
        health_score = max(0, health_score)

        return {
            'score': health_score,
            'status': status_text,
            'color': color,
            'memory_mb': memory_mb,
            'memory_percent': memory_percent,
            'cpu_percent': cpu_percent
        }

    @staticmethod
    def get_health_description(health_score: int) -> str:
        """
        Get a description of the health score.

        Args:
            health_score: Health score (0-100)

        Returns:
            str: Description of the health score
        """
        if health_score >= 90:
            return "Excellent - The application is running optimally."
        elif health_score >= 75:
            return "Good - The application is running well with minimal resource usage."
        elif health_score >= 50:
            return "Fair - The application is using moderate resources."
        elif health_score >= 25:
            return "Poor - The application is using significant resources."
        else:
            return "Critical - The application is using excessive resources."

    @staticmethod
    def get_recommendations(health_info: Dict[str, Any]) -> list:
        """
        Get recommendations based on health information.

        Args:
            health_info: Health information dictionary

        Returns:
            list: List of recommendation strings
        """
        recommendations = []

        # Memory recommendations
        memory_mb = health_info.get('memory_mb', 0)
        if memory_mb > 750:
            recommendations.append(
                "Memory usage is critical. Run garbage collection and consider restarting the application.")
        elif memory_mb > 500:
            recommendations.append("Memory usage is high. Run garbage collection to free up memory.")

        # CPU recommendations
        cpu_percent = health_info.get('cpu_percent', 0)
        if cpu_percent > 80:
            recommendations.append(
                "CPU usage is critical. Check for CPU-intensive operations and consider optimizing them.")
        elif cpu_percent > 50:
            recommendations.append("CPU usage is high. Monitor for performance issues.")

        # General recommendations
        if not recommendations:
            recommendations.append("No issues detected. The application is running well.")

        return recommendations

    # Memory Leak Detection
    def detect_memory_leaks(self) -> Dict[str, Any]:
        """Detect potential memory leaks by tracking object growth."""
        try:
            import gc
            from collections import defaultdict

            # Count objects by type
            type_counts = defaultdict(int)
            for obj in gc.get_objects():
                type_counts[type(obj).__name__] += 1

            # Compare with previous counts to detect growth
            leaks = []
            if hasattr(self, '_previous_type_counts') and self._previous_type_counts:
                growing_types = {}
                for type_name, count in type_counts.items():
                    prev_count = self._previous_type_counts.get(type_name, 0)
                    growth = count - prev_count
                    if growth > 100:  # Significant growth threshold
                        growth_factor = count / max(prev_count, 1)
                        leaks.append({
                            'type': type_name,
                            'count': count,
                            'growth_factor': growth_factor,
                            'history': [(time.time() - 60, prev_count), (time.time(), count)]
                        })

                        growing_types[type_name] = {
                            'current': count,
                            'previous': prev_count,
                            'growth': growth
                        }

                if growing_types:
                    self.logger.warning(f"Potential memory leaks detected: {growing_types}")

            self._previous_type_counts = dict(type_counts)

            return {
                'leaks_detected': len(leaks) > 0,
                'leaks': leaks,
                'type_counts': dict(type_counts)
            }

        except Exception as e:
            self.logger.error(f"Error detecting memory leaks: {e}")
            return {'error': str(e)}

    # Memory Management Integration
    def start_memory_management(self) -> bool:
        """
        Start all memory management services.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("Starting all memory management services")

            # Start memory purging
            self.start_purging()

            # Start resource cleanup
            self.start_resource_cleanup()

            # Start chat cleanup
            self.start_chat_cleanup()

            self.logger.info("All memory management services started successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error starting memory management services: {e}")
            return False

    def stop_memory_management(self) -> bool:
        """
        Stop all memory management services.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("Stopping all memory management services")

            # Stop memory purging
            self.stop_purging()

            # Stop resource cleanup
            self.stop_resource_cleanup()

            # Stop chat cleanup
            self.stop_chat_cleanup()

            self.logger.info("All memory management services stopped successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error stopping memory management services: {e}")
            return False

    def update_window_display(self, window_widget) -> bool:
        """
        Update a window widget with current metrics.

        Args:
            window_widget: The window widget to update

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not window_widget:
                return False

            # Get metrics for display
            metrics = self.get_window_metrics()

            # Update widget if it has the necessary methods
            if hasattr(window_widget, 'update_memory_display'):
                window_widget.update_memory_display(
                    metrics['memory_formatted'],
                    metrics['memory_percent']
                )

            if hasattr(window_widget, 'update_cpu_display'):
                window_widget.update_cpu_display(metrics['cpu_percent'])

            if hasattr(window_widget, 'update_health_display'):
                window_widget.update_health_display(
                    metrics['health_score'],
                    metrics['health_status'],
                    metrics['health_color']
                )

            if hasattr(window_widget, 'update_recommendations'):
                window_widget.update_recommendations(metrics['recommendations'])

            return True
        except Exception as e:
            self.logger.error(f"Error updating window display: {e}")
            return False

    def register_window_widget(self, widget) -> bool:
        """
        Register a window widget for automatic updates.

        Args:
            widget: The window widget to register

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Store reference to widget for updates
            self._window_widget = widget

            # Do initial update
            self.update_window_display(widget)

            return True
        except Exception as e:
            self.logger.error(f"Error registering window widget: {e}")
            return False

    def start_window_updates(self, interval_ms: int = 1000) -> bool:
        """
        Start automatic window updates.

        Args:
            interval_ms: Update interval in milliseconds

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Create timer if not exists
            if not hasattr(self, '_window_update_timer') or not self._window_update_timer:
                self._window_update_timer = QTimer(self)
                self._window_update_timer.timeout.connect(self._update_window_timer_tick)

            # Set interval and start
            self._window_update_timer.setInterval(interval_ms)
            self._window_update_timer.start()

            self.logger.info(f"Window updates started with interval {interval_ms}ms")
            return True
        except Exception as e:
            self.logger.error(f"Error starting window updates: {e}")
            return False

    def stop_window_updates(self) -> bool:
        """
        Stop automatic window updates.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if hasattr(self, '_window_update_timer') and self._window_update_timer:
                self._window_update_timer.stop()
                self.logger.info("Window updates stopped")
            return True
        except Exception as e:
            self.logger.error(f"Error stopping window updates: {e}")
            return False

    def _update_window_timer_tick(self) -> None:
        """Handle window update timer tick."""
        try:
            if hasattr(self, '_window_widget') and self._window_widget:
                self.update_window_display(self._window_widget)
        except Exception as e:
            self.logger.error(f"Error in window update timer: {e}")

    # Configuration
    def load_config(self, config: Dict[str, Any]) -> None:
        """Load configuration from dictionary."""
        try:
            # Memory purging settings
            if 'purge_interval' in config:
                self.set_purge_interval(config['purge_interval'])

            # Resource cleanup settings
            if 'resource_cleanup_interval' in config:
                self.set_resource_cleanup_interval(config['resource_cleanup_interval'])

            # Chat cleanup settings
            if 'chat_cleanup_interval' in config:
                self.set_chat_cleanup_interval(config['chat_cleanup_interval'])
            if 'chat_message_limit' in config:
                self.set_chat_message_limit(config['chat_message_limit'])
            if 'chat_message_age_limit' in config:
                self.set_chat_message_age_limit(config['chat_message_age_limit'])

            # Auto-start settings
            if config.get('auto_start_purging', False):
                self.start_purging()
            if config.get('auto_start_resource_cleanup', False):
                self.start_resource_cleanup()
            if config.get('auto_start_chat_cleanup', False):
                self.start_chat_cleanup()

            self.logger.info("Configuration loaded successfully")

        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")

    # Utility Functions
    def optimize_component(self, component_name: str) -> Dict[str, Any]:
        """
        Optimize memory usage for a specific component.

        Args:
            component_name: Name of the component to optimize

        Returns:
            Dict with optimization results
        """
        try:
            self.logger.info(f"Optimizing component: {component_name}")

            service_locator = self._get_service_locator()
            if not service_locator:
                return {'success': False, 'error': 'Service locator not available'}

            component = service_locator.get_service(component_name)
            if not component:
                return {'success': False, 'error': f'Component {component_name} not found'}

            # Try different optimization methods
            if hasattr(component, 'optimize_memory'):
                result = component.optimize_memory()
                return {'success': True, 'method': 'optimize_memory', 'result': result}
            elif hasattr(component, 'purge_resources'):
                result = component.purge_resources()
                return {'success': True, 'method': 'purge_resources', 'result': result}
            elif hasattr(component, 'cleanup'):
                component.cleanup()
                return {'success': True, 'method': 'cleanup'}
            else:
                return {'success': False, 'error': f'No optimization method found for {component_name}'}
        except Exception as e:
            self.logger.error(f"Error optimizing component {component_name}: {e}")
            return {'success': False, 'error': str(e)}

    def collect_garbage(self, generation: Optional[int] = None) -> Dict[str, Any]:
        """
        Collect garbage for the specified generation.

        Args:
            generation: The generation to collect (0, 1, 2) or None for full collection

        Returns:
            Dict with collection statistics
        """
        try:
            before_count = len(gc.get_objects())

            if generation is None:
                # Full collection
                collected = gc.collect()
            else:
                # Specific generation
                collected = gc.collect(generation)

            after_count = len(gc.get_objects())

            return {
                'collected': collected,
                'objects_before': before_count,
                'objects_after': after_count,
                'counts': gc.get_count(),
                'objects': after_count,
                'garbage': len(gc.garbage)
            }
        except Exception as e:
            self.logger.error(f"Error collecting garbage: {e}")
            return {
                'collected': 0,
                'objects_before': 0,
                'objects_after': 0,
                'counts': [0, 0, 0],
                'objects': 0,
                'garbage': 0,
                'error': str(e)
            }

    @staticmethod
    def get_referrers(obj: Any) -> List[Any]:
        """Get objects that refer to the given object."""
        return gc.get_referrers(obj)

    @staticmethod
    def get_referents(obj: Any) -> List[Any]:
        """Get objects that the given object refers to."""
        return gc.get_referents(obj)

    def update_metrics(self, metrics: AppMetrics) -> None:
        """
        Update current metrics.

        Args:
            metrics: New metrics data
        """
        self._current_metrics = metrics
        self._metrics_history.append(metrics)

        # Keep history manageable
        if len(self._metrics_history) > self._max_history_size:
            self._metrics_history.pop(0)

    def get_current_metrics(self) -> AppMetrics:
        """
        Get current metrics.

        Returns:
            AppMetrics: Current application metrics
        """
        return self._current_metrics

    def get_metrics_history(self) -> List[AppMetrics]:
        """
        Get metrics history.

        Returns:
            List[AppMetrics]: History of application metrics
        """
        return self._metrics_history

    def set_thresholds(self, warning: float, critical: float) -> None:
        """
        Set memory thresholds.

        Args:
            warning: Warning threshold as percentage
            critical: Critical threshold as percentage
        """
        self._warning_threshold = warning
        self._critical_threshold = critical
        self.logger.info(f"Memory thresholds set - Warning: {warning}%, Critical: {critical}%")

    def cleanup_after_quiz(self) -> Dict[str, Any]:
        if self._cleanup_in_progress:
            self.logger.warning("Cleanup already in progress, skipping cleanup_after_quiz to prevent recursion")
            return {'error': 'Cleanup already in progress'}

        self._cleanup_in_progress = True

        try:
            self.logger.info("Starting post-quiz cleanup")
            before = self.get_memory_usage()

            gc_result = self._run_gc(aggressive=True)
            self._clear_python_caches()
            self._release_memory_to_os()
            quiz_components = ["QuizManager", "LeaderboardManager", "CSVHandler", "QuizDisplay", "QuizControls",
                               ]
            components_cleaned = []

            service_locator = self._get_service_locator()
            if service_locator:
                for component_name in quiz_components:
                    component = service_locator.get_service(component_name)
                    if component:
                        try:
                            # Try different cleanup methods
                            if hasattr(component, 'reset_quiz_state') and component_name == "QuizManager":
                                # DISABLED - was causing quiz restart issues
                                pass
                            elif hasattr(component, 'reset'):
                                component.reset()
                                components_cleaned.append(f"{component_name}.reset")
                            elif hasattr(component, 'cleanup'):
                                component.cleanup()
                                components_cleaned.append(f"{component_name}.cleanup")
                            elif hasattr(component, 'clear'):
                                component.clear()
                                components_cleaned.append(f"{component_name}.clear")

                            # Special handling for QuizDisplay
                            if component_name == "QuizDisplay" and hasattr(component, 'clear_answer_labels'):
                                component.clear_answer_labels()
                                components_cleaned.append(f"{component_name}.clear_answer_labels")
                        except Exception as e:
                            self.logger.error(f"Error cleaning up {component_name}: {e}")

            # Get memory usage after cleanup
            after = self.get_memory_usage()

            # Calculate memory saved
            memory_saved = before.get('rss', 0) - after.get('rss', 0)

            result = {
                'memory_before': before.get('rss', 0),
                'memory_after': after.get('rss', 0),
                'memory_saved': memory_saved,
                'memory_saved_formatted': self.format_size(memory_saved),
                'components_cleaned': components_cleaned,
                'gc_result': gc_result
            }

            # Log cleanup results
            self.logger.info(f"Post-quiz cleanup saved {self.format_size(memory_saved)}")

            return result
        except Exception as e:
            self.logger.error(f"Error in post-quiz cleanup: {e}")
            return {'error': str(e)}
        finally:
            # CRITICAL: Always reset the flag when done
            self._cleanup_in_progress = False

    def cleanup_before_quiz(self) -> Dict[str, Any]:
        """
        Perform cleanup before starting a new quiz.

        Returns:
            Dict with cleanup statistics
        """
        # CRITICAL FIX: Check if cleanup is already in progress to prevent recursion
        if self._cleanup_in_progress:
            self.logger.warning("Cleanup already in progress, skipping cleanup_before_quiz to prevent recursion")
            return {'error': 'Cleanup already in progress'}

        # Set the flag to indicate cleanup is in progress
        self._cleanup_in_progress = True

        try:
            self.logger.info("Starting pre-quiz cleanup")

            # Get memory usage before cleanup
            before = self.get_memory_usage()

            # Clean up chat messages - directly, not through another method that might call back
            chat_messages_cleaned = self._cleanup_chat_messages()

            # Run standard memory purge - directly, not by calling purge_memory
            gc_result = self._run_gc(False)
            self._clear_python_caches()

            # Get memory usage after cleanup
            after = self.get_memory_usage()

            # Calculate memory saved
            memory_saved = before.get('rss', 0) - after.get('rss', 0)

            result = {
                'memory_before': before.get('rss', 0),
                'memory_after': after.get('rss', 0),
                'memory_saved': memory_saved,
                'memory_saved_formatted': self.format_size(memory_saved),
                'chat_messages_cleaned': chat_messages_cleaned,
                'gc_result': gc_result
            }

            # Log cleanup results
            self.logger.info(f"Pre-quiz cleanup saved {self.format_size(memory_saved)}")

            return result
        except Exception as e:
            self.logger.error(f"Error in pre-quiz cleanup: {e}")
            return {'error': str(e)}
        finally:
            # CRITICAL: Always reset the flag when done
            self._cleanup_in_progress = False

    def get_memory_pressure(self) -> str:
        """
        Determine current memory pressure level.

        Returns:
            str: 'low', 'medium', 'high', or 'critical'
        """
        try:
            memory_info = self.get_memory_usage()
            memory_percent = memory_info.get('percent', 0)

            if memory_percent >= 90:
                return 'critical'
            elif memory_percent >= 75:
                return 'high'
            elif memory_percent >= 50:
                return 'medium'
            else:
                return 'low'
        except Exception:
            return 'unknown'

    def should_run_aggressive_cleanup(self) -> bool:
        """
        Determine if aggressive cleanup should be run based on memory pressure.

        Returns:
            bool: True if aggressive cleanup is recommended
        """
        if self._is_quiz_active:
            # Only run aggressive cleanup during quiz if memory is critical
            return self.get_memory_pressure() == 'critical'
        else:
            # Run aggressive cleanup if memory pressure is high or critical
            return self.get_memory_pressure() in ['high', 'critical']

    def cleanup_all_components(self) -> Dict[str, Any]:
        """
        Clean up all components in the application.
        This is the most comprehensive cleanup method.

        Returns:
            Dict with cleanup statistics
        """
        # Check if cleanup is already in progress
        if self._cleanup_in_progress:
            self.logger.warning("Cleanup already in progress, skipping cleanup_all_components")
            return {'error': 'Cleanup already in progress'}

        # Set the flag to indicate cleanup is in progress
        self._cleanup_in_progress = True

        try:
            self.logger.info("Starting comprehensive cleanup of all components")

            # Get memory usage before cleanup
            before = self.get_memory_usage()

            components_cleaned = []
            total_resources_cleaned = 0

            # Get service locator
            service_locator = self._get_service_locator()
            if service_locator:
                # Get all available services
                all_services = []
                if hasattr(service_locator, 'list_services'):
                    all_services = service_locator.list_services()
                else:
                    # Comprehensive list of potential services
                    all_services = [
                        "QuizManager", "CSVHandler", "LeaderboardManager", "TikTokLiveManager",
                        "CircleTimerWidget", "QuizDisplay", "ConnectionManager",
                        "ConfigManager", "ThemeApplicator", "AudioHandler",

                        "VideoHandler", "AudioVideoManager",
                        "AvatarManager", "ConnectionMonitor", "ErrorHandler",
                    ]

                # Clean each service
                for service_name in all_services:
                    try:
                        service = service_locator.get_service(service_name)
                        if service:
                            cleaned_count = self._cleanup_single_component(service, service_name)
                            if cleaned_count > 0:
                                total_resources_cleaned += cleaned_count
                                components_cleaned.append(f"{service_name} ({cleaned_count} resources)")
                    except Exception as e:
                        self.logger.error(f"Error cleaning component {service_name}: {e}")

            # Run comprehensive garbage collection
            gc_result = self._run_gc(aggressive=True)

            # Clear all caches
            self._clear_python_caches()

            # Release memory to OS
            self._release_memory_to_os()

            # Get memory usage after cleanup
            after = self.get_memory_usage()

            # Calculate memory saved
            memory_saved = before.get('rss', 0) - after.get('rss', 0)

            result = {
                'memory_before': before.get('rss', 0),
                'memory_after': after.get('rss', 0),
                'memory_saved': memory_saved,
                'memory_saved_formatted': self.format_size(memory_saved),
                'components_cleaned': components_cleaned,
                'total_resources_cleaned': total_resources_cleaned,
                'gc_result': gc_result
            }

            self.logger.info(f"Comprehensive cleanup saved {self.format_size(memory_saved)}")
            return result

        except Exception as e:
            self.logger.error(f"Error in comprehensive cleanup: {e}")
            return {'error': str(e)}
        finally:
            # Always reset the flag
            self._cleanup_in_progress = False

    def _connect_to_signals(self):
        try:
            locator = ServiceLocator.get_instance()
            if hasattr(locator, 'has_service') and not locator.has_service("QuizSignals"):
                self.logger.debug("QuizSignals service not available yet")
                if not self._connect_to_signals_timer:
                    from PySide6.QtCore import QTimer
                    self._connect_to_signals_timer = QTimer()
                    self._connect_to_signals_timer.timeout.connect(self._connect_to_signals)
                    self._connect_to_signals_timer.setSingleShot(True)
                    self._connect_to_signals_timer.start(1000)
                return False

            self.quiz_signals = locator.get_service("QuizSignals")
            if self.quiz_signals:  # Check if we got the service
                # Connect signals here
                self.logger.info("Successfully connected to QuizSignals")
                return True

            self.logger.debug("QuizSignals service not available yet")
            return False
        except Exception as e:
            self.logger.warning(f"Could not connect MemoryUtils to QuizSignals: {e}")
            # Set up retry timer
            if not self._connect_to_signals_timer:
                from PySide6.QtCore import QTimer
                self._connect_to_signals_timer = QTimer()
                self._connect_to_signals_timer.timeout.connect(self._connect_to_signals)
                self._connect_to_signals_timer.setSingleShot(True)
                self._connect_to_signals_timer.start(1000)
            return False

    def _cleanup_single_component(self, component, component_name: str) -> int:
        """
        Clean up a single component using all available methods.

        Args:
            component: The component to clean
            component_name: Name of the component for logging

        Returns:
            int: Number of resources cleaned
        """
        cleaned_count = 0

        try:
            # Try different cleanup methods in order of preference
            cleanup_methods = [
                'cleanup_resources',
                'purge_resources',
                'clear_cache',
                'cleanup',
                'clear',
                'reset'
            ]

            for method_name in cleanup_methods:
                if hasattr(component, method_name):
                    try:
                        method = getattr(component, method_name)
                        if callable(method):
                            result = method()
                            if isinstance(result, int):
                                cleaned_count += result
                            else:
                                cleaned_count += 1
                            break  # Only use the first available method
                    except Exception as e:
                        self.logger.debug(f"Error calling {method_name} on {component_name}: {e}")

            # Clean specific attributes
            cleaned_count += self._clean_component_attributes(component, component_name, {})

        except Exception as e:
            self.logger.error(f"Error cleaning single component {component_name}: {e}")

        return cleaned_count

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get detailed memory statistics with rate limiting.

        Returns:
            Dict with memory statistics
        """
        # Use rate limiter to prevent excessive calls
        if not hasattr(self, '_memory_stats_rate_limiter') or self._memory_stats_rate_limiter is None:
            try:
                self._memory_stats_rate_limiter = OptimizedRateLimiter(max_calls=1, time_window=5.0)
            except Exception as e:
                self.logger.warning(f"Could not create rate limiter: {e}, disabling rate limiting")
                self._memory_stats_rate_limiter = None
            self._cached_memory_stats = {}

        # Return cached stats if we're being called too frequently
        if self._memory_stats_rate_limiter and not self._memory_stats_rate_limiter.can_proceed():
            return self._cached_memory_stats

        try:
            # Get process memory info
            if not hasattr(self, '_memory_process') or self._memory_process is None:
                self._memory_process = psutil.Process()

            memory_info = self._memory_process.memory_info()
            system_memory = psutil.virtual_memory()

            # Get garbage collector stats
            gc_stats = {
                'counts': gc.get_count(),
                'objects': len(gc.get_objects()),
                'garbage': len(gc.garbage),
                'enabled': gc.isenabled(),
                'thresholds': gc.get_threshold()
            }

            # Format memory sizes
            formatted = {
                'rss': self.format_size(memory_info.rss),
                'vms': self.format_size(memory_info.vms),
                'system_total': self.format_size(system_memory.total),
                'system_available': self.format_size(system_memory.available),
                'system_used': self.format_size(system_memory.used)
            }

            result = {
                'process': {
                    'rss': memory_info.rss,
                    'vms': memory_info.vms,
                    'percent': self._memory_process.memory_percent()
                },
                'system': {
                    'total': system_memory.total,
                    'available': system_memory.available,
                    'used': system_memory.used,
                    'percent': system_memory.percent
                },
                'gc': gc_stats,
                'formatted': formatted,
                'method': 'psutil_direct'
            }

            # Cache the result
            self._cached_memory_stats = result
            return result
        except Exception as e:
            self.logger.error(f"Error getting memory stats: {e}")
            return self._cached_memory_stats if hasattr(self, '_cached_memory_stats') else {'error': str(e)}

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get comprehensive performance metrics."""
        try:
            memory_info = self.get_memory_usage()

            # Calculate memory efficiency
            total_memory = memory_info.get('total', 1)
            used_memory = memory_info.get('rss', 0)
            efficiency = (total_memory - used_memory) / total_memory * 100

            # Get cleanup statistics
            metrics = {
                'memory_usage': memory_info,
                'memory_efficiency': efficiency,
                'purge_enabled': self._purge_enabled,
                'resource_cleanup_enabled': self._resource_cleanup_enabled,
                'chat_cleanup_enabled': self._chat_cleanup_enabled,
                'chat_message_limit': self._chat_message_limit,
                'chat_message_age_limit': self._chat_message_age_limit,
                'tracked_resources_count': len(self._tracked_resources),
                'gc_stats': {
                    'counts': gc.get_count(),
                    'stats': gc.get_stats() if hasattr(gc, 'get_stats') else None
                }
            }

            return metrics

        except Exception as e:
            self.logger.error(f"Error getting performance metrics: {e}")
            return {'error': str(e)}

    @staticmethod
    def _check_memory_pressure() -> bool:
        """Check if system is under memory pressure."""
        try:
            memory = psutil.virtual_memory()
            # Trigger aggressive cleanup if memory usage > 85%
            return memory.percent > 85.0
        except Exception:
            return False

    def _adaptive_cleanup_interval(self) -> None:
        """Adjust cleanup intervals based on memory pressure."""
        if self._check_memory_pressure():
            # More frequent cleanup under pressure
            self._purge_interval = min(self._purge_interval, 60000)  # 1 minute
            self._chat_cleanup_interval = min(self._chat_cleanup_interval, 30000)  # 30 seconds
        else:
            # Normal intervals when memory is fine
            self._purge_interval = 300000  # 5 minutes
            self._chat_cleanup_interval = 120000  # 2 minutes

    def cleanup(self) -> bool:
        """Clean up resources."""
        try:
            self.logger.info("Cleaning up MemoryUtils")

            # Stop and delete all timers
            timers = [
                '_purge_timer',
                '_resource_cleanup_timer',
                '_chat_cleanup_timer',
                '_window_update_timer',
                '_connect_to_signals_timer',
                '_connection_retry_timer',
                '_memory_monitor_timer'
            ]

            for timer_name in timers:
                if hasattr(self, timer_name):
                    timer = getattr(self, timer_name)
                    if timer is not None:
                        try:
                            timer.stop()
                            timer.deleteLater()
                        except Exception as e:
                            self.logger.debug(f"Error stopping {timer_name}: {e}")
                        setattr(self, timer_name, None)

            # Clear collections
            self._tracked_resources.clear()
            self._metrics_history.clear()
            if hasattr(self, '_previous_type_counts'):
                self._previous_type_counts.clear()

            # Clear references
            self._window_widget = None
            self._service_locator = None

            gc.collect()

            self.logger.info("MemoryUtils cleaned up successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error cleaning up MemoryUtils: {e}")
            return False

    def __del__(self):
        """Clean up when the object is deleted."""
        try:
            # Stop all timers
            if hasattr(self, '_purge_timer'):
                self._purge_timer.stop()
            if hasattr(self, '_resource_cleanup_timer'):
                self._resource_cleanup_timer.stop()
            if hasattr(self, '_chat_cleanup_timer'):
                self._chat_cleanup_timer.stop()
            if hasattr(self, '_window_update_timer'):
                self._window_update_timer.stop()

            # Clear collections
            if hasattr(self, '_tracked_resources'):
                self._tracked_resources.clear()
            if hasattr(self, '_metrics_history'):
                self._metrics_history.clear()
            if hasattr(self, '_previous_type_counts'):
                self._previous_type_counts.clear()

        except Exception as e:
            # Cannot use logger here as it might be gone already
            print(f"Error in MemoryUtils.__del__: {e}")
