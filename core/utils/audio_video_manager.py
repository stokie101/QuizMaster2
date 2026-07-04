"""
Bulletproof Audio/Video Manager - Thread-safe file management

Key improvements:
- Complete thread safety
- Better initialization
- Path caching for performance
- Safe file validation
- No blocking operations
"""

import logging
import os
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject


class AudioVideoManager(QObject):
    """
    Thread-safe singleton for audio and video file management.

    Features:
    - Safe path resolution
    - File caching for performance
    - Automatic directory creation
    - Format validation
    """

    _instance = None
    _instance_lock = threading.Lock()

    # Base paths
    ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    AUDIO_BASE_PATH = os.path.join(ROOT_DIR, "core", "assets", "sounds")
    VIDEO_BASE_PATH = os.path.join(ROOT_DIR, "core", "assets", "videos")

    # Audio categories
    AUDIO_CATEGORIES = {
        "background": os.path.join(AUDIO_BASE_PATH, "background"),
        "sound_effects": os.path.join(AUDIO_BASE_PATH, "sound_effects"),
        "timer": os.path.join(AUDIO_BASE_PATH, "timer")
    }

    # Supported formats
    SUPPORTED_AUDIO_FORMATS = {".mp3", ".wav", ".ogg", ".aac"}
    SUPPORTED_VIDEO_FORMATS = {".mp4", ".avi", ".mov", ".mkv"}

    @classmethod
    def get_instance(cls):
        """Thread-safe singleton access."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        if hasattr(self, '_initialized'):
            return

        super().__init__(parent)
        self.logger = logging.getLogger(__name__)

        # Thread safety
        self._lock = threading.RLock()

        # File cache
        self._file_cache = {}
        self._cache_dirty = True

        # State
        self._initialized = False

        # Verify paths (non-blocking)
        self._verify_and_create_paths()

        self.logger.info("✅ AudioVideoManager created")

    def _verify_and_create_paths(self):
        """Verify and create necessary directories."""
        try:
            # Create base directories
            for name, path in [
                ("Audio base", self.AUDIO_BASE_PATH),
                ("Video base", self.VIDEO_BASE_PATH)
            ]:
                self._ensure_directory(path, name)

            # Create category directories
            for category, path in self.AUDIO_CATEGORIES.items():
                self._ensure_directory(path, f"Audio category '{category}'")

        except Exception as e:
            self.logger.error(f"Path verification failed: {e}")

    def _ensure_directory(self, path: str, description: str):
        """Ensure directory exists, create if needed."""
        try:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
                self.logger.info(f"Created {description}: {path}")
            else:
                self.logger.debug(f"{description} exists: {path}")
        except Exception as e:
            self.logger.warning(f"Could not create {description}: {e}")

    def initialize(self) -> bool:
        """
        Initialize manager with thread safety.
        Idempotent - safe to call multiple times.

        Returns:
            bool: True if initialized successfully
        """
        with self._lock:
            if self._initialized:
                return True

            try:
                self.logger.info("Initializing AudioVideoManager...")

                # Refresh file cache
                self._refresh_file_cache()

                self._initialized = True
                self.logger.info("✅ AudioVideoManager initialized")
                return True

            except Exception as e:
                self.logger.error(f"Initialization failed: {e}")
                self.logger.debug(traceback.format_exc())
                return False

    def _refresh_file_cache(self):
        """
        Refresh internal file cache.
        Thread-safe and non-blocking.
        """
        try:
            with self._lock:
                self._file_cache.clear()

                for category, path in self.AUDIO_CATEGORIES.items():
                    if not os.path.isdir(path):
                        continue

                    try:
                        files = os.listdir(path)
                        for file in files:
                            file_path = os.path.join(path, file)
                            if os.path.isfile(file_path):
                                cache_key = f"{category}/{file}"
                                self._file_cache[cache_key] = file_path

                    except (OSError, PermissionError) as e:
                        self.logger.warning(f"Could not list files in {path}: {e}")

                self._cache_dirty = False
                self.logger.debug(f"File cache refreshed: {len(self._file_cache)} entries")

        except Exception as e:
            self.logger.error(f"Cache refresh failed: {e}")

    def is_initialized(self) -> bool:
        """Check if manager is initialized."""
        with self._lock:
            return self._initialized

    def is_supported_audio_format(self, file_path: str) -> bool:
        """
        Check if file is a supported audio format.
        Thread-safe.
        """
        if not file_path:
            return False

        try:
            file_ext = Path(file_path).suffix.lower()
            return file_ext in self.SUPPORTED_AUDIO_FORMATS
        except Exception as e:
            self.logger.error(f"Error checking audio format for {file_path}: {e}")
            return False

    def is_supported_video_format(self, file_path: str) -> bool:
        """
        Check if file is a supported video format.
        Thread-safe.
        """
        if not file_path:
            return False

        try:
            file_ext = Path(file_path).suffix.lower()
            return file_ext in self.SUPPORTED_VIDEO_FORMATS
        except Exception as e:
            self.logger.error(f"Error checking video format for {file_path}: {e}")
            return False

    def get_audio_file_path(self, sound_type: str, file_name: str) -> str:
        """
        Get full path to audio file.
        Thread-safe with caching.

        Args:
            sound_type: Category (background, sound_effects, timer)
            file_name: Filename

        Returns:
            Full path or empty string if not found
        """
        if not sound_type or not file_name:
            return ""

        try:
            cache_key = f"{sound_type}/{file_name}"

            # Try cache first
            with self._lock:
                if not self._cache_dirty and cache_key in self._file_cache:
                    cached_path = self._file_cache[cache_key]
                    # Verify file still exists
                    if os.path.isfile(cached_path):
                        return cached_path
                    else:
                        # Remove stale entry
                        del self._file_cache[cache_key]

            # Get category directory
            category_dir = self.AUDIO_CATEGORIES.get(sound_type)
            if not category_dir:
                self.logger.warning(f"Invalid sound type: {sound_type}")
                return ""

            # Build file path
            file_path = os.path.join(category_dir, file_name)

            # Check if exists
            if os.path.isfile(file_path):
                # Update cache
                with self._lock:
                    self._file_cache[cache_key] = file_path
                return file_path
            else:
                self.logger.warning(f"File not found: {file_path}")
                return ""

        except Exception as e:
            self.logger.error(f"Error getting audio file path for {sound_type}/{file_name}: {e}")
            return ""

    def debug_list_all_audio_files(self) -> dict:
        """
        List all available audio files.
        Thread-safe, uses cache when possible.

        Returns:
            Dict of {category: [files]}
        """
        result = {}

        try:
            # Try cache first
            with self._lock:
                if not self._cache_dirty and self._file_cache:
                    # Build from cache
                    for cache_key, file_path in self._file_cache.items():
                        if '/' in cache_key:
                            category, filename = cache_key.split('/', 1)
                            if self.is_supported_audio_format(filename):
                                if category not in result:
                                    result[category] = []
                                result[category].append(filename)

                    if result:
                        return result

            # Fallback to filesystem scan
            for category, path in self.AUDIO_CATEGORIES.items():
                try:
                    if os.path.isdir(path):
                        files = [
                            f for f in os.listdir(path)
                            if os.path.isfile(os.path.join(path, f))
                               and self.is_supported_audio_format(f)
                        ]
                        result[category] = files
                    else:
                        result[category] = []

                except (OSError, PermissionError) as e:
                    self.logger.warning(f"Could not access {path}: {e}")
                    result[category] = []

        except Exception as e:
            self.logger.error(f"Error listing audio files: {e}")

        return result

    def refresh_cache(self):
        """
        Manually refresh file cache.
        Thread-safe.
        """
        try:
            with self._lock:
                self._cache_dirty = True
            self._refresh_file_cache()
            self.logger.info("✅ Audio file cache refreshed")
        except Exception as e:
            self.logger.error(f"Cache refresh failed: {e}")

    def get_cache_stats(self) -> dict:
        """Get cache statistics for debugging."""
        with self._lock:
            return {
                'cache_size': len(self._file_cache),
                'cache_dirty': self._cache_dirty,
                'categories': list(self.AUDIO_CATEGORIES.keys()),
                'initialized': self._initialized
            }

    def cleanup(self):
        """Cleanup on shutdown."""
        self.logger.info("Cleaning up AudioVideoManager...")

        with self._lock:
            self._file_cache.clear()
            self._initialized = False

        self.logger.info("✅ AudioVideoManager cleanup complete")

    def __del__(self):
        """Destructor - ensure cleanup."""
        try:
            if hasattr(self, '_file_cache'):
                self._file_cache.clear()
        except Exception:
            pass


# Backward compatibility helper
def resource_path(relative_path: str) -> str:
    """Get absolute path to resource (dev and PyInstaller)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("../managers"), relative_path)
