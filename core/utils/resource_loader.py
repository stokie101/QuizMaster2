"""
Bulletproof Resource Loader - Secure resource handling for dev and production

Key improvements:
- Thread-safe path resolution
- Better error handling
- Path caching for performance
- Validation of resource existence
- Cross-platform support
"""

import logging
import os
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class ResourceLoader:
    """
    Thread-safe resource loader with caching.
    Handles resources in both development and PyInstaller environments.
    """

    _cache = {}
    _cache_lock = threading.RLock()
    _base_path = None
    _base_path_lock = threading.Lock()

    @classmethod
    def get_base_path(cls) -> Path:
        """
        Get application base path (cached).
        Thread-safe.
        """
        if cls._base_path is not None:
            return cls._base_path

        with cls._base_path_lock:
            if cls._base_path is not None:
                return cls._base_path

            try:
                # PyInstaller frozen app
                if getattr(sys, 'frozen', False):
                    cls._base_path = Path(sys._MEIPASS)
                    logger.debug(f"Frozen mode base: {cls._base_path}")
                else:
                    # Development mode
                    cls._base_path = Path(__file__).parent.parent.parent
                    logger.debug(f"Dev mode base: {cls._base_path}")

            except Exception as e:
                logger.error(f"Failed to determine base path: {e}")
                cls._base_path = Path.cwd()

            return cls._base_path

    @classmethod
    def get_resource_path(cls, relative_path: str, validate: bool = True) -> Path:
        """
        Get absolute path to resource with caching.

        Args:
            relative_path: Path relative to application root
            validate: If True, warn if resource doesn't exist

        Returns:
            Absolute path to resource
        """
        if not relative_path:
            logger.warning("Empty relative path provided")
            return Path()

        # Check cache first
        with cls._cache_lock:
            if relative_path in cls._cache:
                cached_path = cls._cache[relative_path]
                # Verify cached path still exists
                if cached_path.exists():
                    return cached_path
                else:
                    # Remove stale cache entry
                    del cls._cache[relative_path]

        try:
            base_path = cls.get_base_path()

            # Try direct path
            resource = base_path / relative_path
            if resource.exists():
                logger.debug(f"Found resource: {resource}")
                with cls._cache_lock:
                    cls._cache[relative_path] = resource
                return resource

            # For frozen apps, try alternative locations
            if getattr(sys, 'frozen', False):
                # Check _internal folder (onedir builds)
                internal_path = base_path / "_internal" / relative_path
                if internal_path.exists():
                    logger.debug(f"Found in _internal: {internal_path}")
                    with cls._cache_lock:
                        cls._cache[relative_path] = internal_path
                    return internal_path

                # Try without 'core/' prefix (flattened structure)
                if relative_path.startswith('core/'):
                    alt_path = base_path / relative_path[5:]
                    if alt_path.exists():
                        logger.debug(f"Found without core/ prefix: {alt_path}")
                        with cls._cache_lock:
                            cls._cache[relative_path] = alt_path
                        return alt_path

            # Resource not found
            if validate:
                logger.warning(f"Resource not found: {relative_path}")

            # Return best guess path
            with cls._cache_lock:
                cls._cache[relative_path] = resource
            return resource

        except Exception as e:
            logger.error(f"Error resolving resource path '{relative_path}': {e}")
            return Path()

    @classmethod
    def get_data_dir(cls) -> Path:
        """
        Get writable data directory.
        Thread-safe with caching.

        Returns:
            Path to writable data directory
        """
        cache_key = '_data_dir'

        with cls._cache_lock:
            if cache_key in cls._cache:
                return cls._cache[cache_key]

        try:
            # Check environment variable first
            data_path = os.environ.get('LIVEFORGE_DATA_PATH')
            if data_path:
                data_dir = Path(data_path)
                logger.debug(f"Using LIVEFORGE_DATA_PATH: {data_dir}")
                with cls._cache_lock:
                    cls._cache[cache_key] = data_dir
                return data_dir

            # Determine based on platform and frozen status
            if getattr(sys, 'frozen', False):
                # Installed app - use AppData
                if sys.platform == 'win32':
                    appdata = os.environ.get('APPDATA', '')
                    if appdata:
                        data_dir = Path(appdata) / "QuizMaster"
                    else:
                        data_dir = Path.home() / "AppData" / "Roaming" / "QuizMaster"
                elif sys.platform == 'darwin':
                    data_dir = Path.home() / "Library" / "Application Support" / "QuizMaster"
                else:
                    data_dir = Path.home() / ".quizmaster_lite"

                logger.debug(f"Frozen app data dir: {data_dir}")
            else:
                # Development - use local directory
                data_dir = cls.get_base_path() / "data"
                logger.debug(f"Dev data dir: {data_dir}")

            with cls._cache_lock:
                cls._cache[cache_key] = data_dir

            return data_dir

        except Exception as e:
            logger.error(f"Error determining data directory: {e}")
            # Fallback
            fallback = Path.home() / ".QuizMaster"
            with cls._cache_lock:
                cls._cache[cache_key] = fallback
            return fallback

    @classmethod
    def ensure_data_dirs(cls) -> Path:
        """
        Create all necessary writable directories.
        Thread-safe.

        Returns:
            Path to main data directory
        """
        data_dir = cls.get_data_dir()

        subdirs = [
            'logs',
            'avatar_cache',
            'config',
            'keys',
            'data',
            'data/leaderboards',
            'assets/sessions'
        ]

        for subdir in subdirs:
            try:
                dir_path = data_dir / subdir
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Ensured directory: {dir_path}")
            except Exception as e:
                logger.error(f"Failed to create directory {subdir}: {e}")

        return data_dir

    @classmethod
    def get_asset_path(cls, asset_type: str, filename: str) -> Path:
        """
        Get path to asset file (images, sounds, fonts).

        Args:
            asset_type: 'images', 'sounds', or 'fonts'
            filename: Name of the asset file

        Returns:
            Full path to asset
        """
        if not asset_type or not filename:
            logger.warning("Invalid asset type or filename")
            return Path()

        # Try with 'core/' prefix first
        path = cls.get_resource_path(f"core/assets/{asset_type}/{filename}", validate=False)

        if path.exists():
            return path

        # Try without 'core/' prefix (frozen apps)
        if getattr(sys, 'frozen', False):
            alt_path = cls.get_resource_path(f"assets/{asset_type}/{filename}", validate=False)
            if alt_path.exists():
                return alt_path

        # Warn if not found
        logger.warning(f"Asset not found: {asset_type}/{filename}")
        return path

    @classmethod
    def clear_cache(cls):
        """Clear resource path cache."""
        with cls._cache_lock:
            cls._cache.clear()
            logger.debug("Resource cache cleared")

    @classmethod
    def get_cache_stats(cls) -> dict:
        """Get cache statistics for debugging."""
        with cls._cache_lock:
            return {
                'cache_size': len(cls._cache),
                'cached_paths': list(cls._cache.keys()),
                'base_path': str(cls._base_path) if cls._base_path else None,
                'frozen': getattr(sys, 'frozen', False)
            }


# Convenience functions for backward compatibility
def get_resource_path(relative_path: str) -> Path:
    """Get absolute path to resource."""
    return ResourceLoader.get_resource_path(relative_path)


def get_data_dir() -> Path:
    """Get writable data directory."""
    return ResourceLoader.get_data_dir()


def ensure_data_dirs():
    """Create all necessary directories."""
    return ResourceLoader.ensure_data_dirs()


def get_asset_path(asset_type: str, filename: str) -> Path:
    """Get path to asset file."""
    return ResourceLoader.get_asset_path(asset_type, filename)
