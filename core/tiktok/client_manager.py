import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, Optional, List

import requests
from PySide6.QtCore import QGenericArgument
from PySide6.QtGui import QPixmap

from core.services.service_locator import ServiceLocator


class ClientManager:
    """
    GUI-side helper for avatar caching and lightweight message caching.
    Enhanced with aggressive memory management for high-traffic rooms.
    Implements a thread-safe Singleton pattern and auto-registers
    itself in the global ServiceLocator.
    """

    # --- Singleton Pattern ---
    _instance: Optional["ClientManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, manager=None, project_root: Optional[Path] = None) -> "ClientManager":
        """Thread-safe global singleton accessor."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(manager, project_root)

                # Auto-register in ServiceLocator if not already registered
                try:
                    sl = ServiceLocator.get_instance()
                    already_registered = hasattr(sl, "has_service") and sl.has_service("ClientManager")
                    if not already_registered and hasattr(sl, "register_service"):
                        sl.register_service("ClientManager", cls._instance)
                except Exception as exc:
                    logging.getLogger(cls.__name__).warning(
                        f"ServiceLocator registration failed: {exc}"
                    )

        return cls._instance

    # --- Core Configuration ---
    MAX_MEMORY_AVATARS = 100
    MAX_DISK_AVATARS = 500
    MAX_CACHED_MESSAGES = 20
    MAX_USER_INFO = 200

    CLEANUP_THRESHOLD_AVATARS = 80
    CLEANUP_THRESHOLD_MESSAGES = 40
    CLEANUP_THRESHOLD_USER_INFO = 150

    def __init__(self, manager, project_root: Optional[Path] = None) -> None:
        # Prevent reinitialization in singleton
        if getattr(self, "_initialized", False):
            return

        self.service_locator = ServiceLocator.get_instance()
        self.manager = manager
        self.logger = logging.getLogger(self.__class__.__name__)
        self.project_root = project_root or Path.cwd()
        self._shutting_down = False
        self._initialized = True

        self._avatar_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._avatar_cache_lock = threading.Lock()
        self._avatar_disk_cache_dir: Optional[Path] = None
        self._avatar_download_locks: Dict[str, threading.Lock] = {}
        self._avatar_lock_dict_lock = threading.Lock()

        self._user_info: "OrderedDict[str, Any]" = OrderedDict()
        self._user_info_lock = threading.Lock()

        from concurrent.futures import ThreadPoolExecutor

        self._avatar_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="AvatarDownload",
        )

        self._message_cache: List[Dict[str, Any]] = []
        self._message_cache_lock = threading.RLock()
        self._max_cached_messages = self.MAX_CACHED_MESSAGES

        self._quiz_active = False
        self._quiz_question_active = False

        self._last_cleanup = time.time()
        self._cleanup_interval = 30  # seconds

        self._avatar_download_queue_size = 0
        self._max_download_queue = 10  # Limit concurrent downloads

        try:
            self._avatar_disk_cache_dir = self._init_avatar_cache_dir()
            self._cleanup_old_disk_cache()
        except Exception as exc:
            self.logger.error(f"Failed to initialize disk cache: {exc}")
            self._avatar_disk_cache_dir = None

        self.header_profiles = [
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        ]

        self.logger.info(
            "ClientManager initialized with memory limits: "
            f"{self.MAX_MEMORY_AVATARS} avatars, "
            f"{self.MAX_CACHED_MESSAGES} messages"
        )

    # =========================================================================
    # AVATAR CACHE WITH AGGRESSIVE LRU MANAGEMENT
    # =========================================================================
    def _init_avatar_cache_dir(self) -> Optional[Path]:
        try:
            cache_dir = self.project_root / "avatar_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir
        except Exception as exc:
            self.logger.error(f"Failed to init avatar cache dir: {exc}")
            return None

    def _cleanup_old_disk_cache(self) -> None:
        """Remove old avatar files on startup."""
        if not self._avatar_disk_cache_dir:
            return

        try:
            files = sorted(
                self._avatar_disk_cache_dir.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for file in files[self.MAX_DISK_AVATARS:]:
                try:
                    file.unlink()
                except Exception:
                    pass

            removed = max(0, len(files) - self.MAX_DISK_AVATARS)
            if removed > 0:
                self.logger.info(f"Cleaned up {removed} old avatar files")
        except Exception as exc:
            self.logger.error(f"Error cleaning old disk cache: {exc}")

    def _cleanup_avatar_cache(self, force: bool = False) -> None:
        """Aggressively clean avatar cache when threshold reached."""
        with self._avatar_cache_lock:
            current_size = len(self._avatar_cache)
            if not force and current_size < self.CLEANUP_THRESHOLD_AVATARS:
                return

            to_remove = current_size // 2
            if to_remove > 0:
                for _ in range(to_remove):
                    try:
                        self._avatar_cache.popitem(last=False)
                    except KeyError:
                        break
                self.logger.info(
                    f"Cleaned {to_remove} avatars from memory cache (was {current_size})"
                )

    def _cleanup_disk_cache_aggressive(self) -> None:
        """Remove old disk files when limit reached."""
        if not self._avatar_disk_cache_dir:
            return

        try:
            files = sorted(
                self._avatar_disk_cache_dir.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if len(files) <= self.MAX_DISK_AVATARS:
                return

            removed = 0
            for file in files[self.MAX_DISK_AVATARS:]:
                try:
                    file.unlink()
                    removed += 1
                except Exception:
                    pass

            if removed > 0:
                self.logger.info(f"Cleaned {removed} old disk avatar files")
        except Exception as exc:
            self.logger.error(f"Error in disk cache cleanup: {exc}")

    def _get_avatar_cache_path(self, user_id: str) -> Path:
        safe_user_id = "".join(c if c.isalnum() else "_" for c in user_id)[:50]
        assert self._avatar_disk_cache_dir is not None
        return self._avatar_disk_cache_dir / f"{safe_user_id}.png"

    def _get_download_lock(self, user_id: str) -> threading.Lock:
        with self._avatar_lock_dict_lock:
            if user_id not in self._avatar_download_locks:
                self._avatar_download_locks[user_id] = threading.Lock()
            return self._avatar_download_locks[user_id]

    @staticmethod
    def _create_circular_avatar(pixmap: QPixmap, size: int = 48) -> QPixmap:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter, QPainterPath

        if not pixmap or pixmap.isNull():
            return QPixmap()

        output = QPixmap(size, size)
        output.fill(Qt.GlobalColor.transparent)

        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        scaled = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            (size - scaled.width()) // 2,
            (size - scaled.height()) // 2,
            scaled,
        )
        painter.end()
        return output

    @staticmethod
    def _create_initials_avatar(display_name: str, size: int = 48) -> QPixmap:
        from PySide6.QtCore import Qt, QRect
        from PySide6.QtGui import QPainter, QPainterPath, QBrush, QColor, QFont

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.fillPath(path, QBrush(QColor("#4A90E2")))

        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Arial", int(size * 0.4), QFont.Weight.Bold)
        painter.setFont(font)

        initials = (display_name or "U")[0].upper()
        painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()
        return pixmap

    @staticmethod
    def _extract_avatar_url(avatar_url: Any) -> Optional[str]:
        if not avatar_url:
            return None

        if isinstance(avatar_url, str):
            return avatar_url if avatar_url.startswith("http") else None

        if isinstance(avatar_url, dict):
            for key in ("urls", "url_list", "m_urls"):
                urls = avatar_url.get(key)
                if urls and isinstance(urls, (list, tuple)):
                    first = urls[0]
                    if isinstance(first, str) and first.startswith("http"):
                        return first

            uri = avatar_url.get("uri")
            if isinstance(uri, str) and uri:
                return uri if uri.startswith("http") else f"https://p16-sign.tiktokcdn.com/{uri}"

        for attr in ("m_urls", "urls", "url_list"):
            if hasattr(avatar_url, attr):
                urls = getattr(avatar_url, attr, None)
                if urls and isinstance(urls, (list, tuple)):
                    first = urls[0]
                    if isinstance(first, str) and first.startswith("http"):
                        return first

        for attr in ("m_uri", "uri"):
            if hasattr(avatar_url, attr):
                uri = getattr(avatar_url, attr, None)
                if isinstance(uri, str) and uri:
                    return uri if uri.startswith("http") else f"https://p16-sign.tiktokcdn.com/{uri}"

        return None

    def get_avatar(self, user_id: str, avatar_url: Any, display_name: str) -> QPixmap:
        """Get avatar with aggressive memory management."""
        self._maybe_cleanup()

        # Memory cache
        with self._avatar_cache_lock:
            if user_id in self._avatar_cache:
                pixmap = self._avatar_cache.pop(user_id)
                self._avatar_cache[user_id] = pixmap
                return pixmap

        # Disk cache
        if self._avatar_disk_cache_dir:
            disk_path = self._get_avatar_cache_path(user_id)
            if disk_path.exists():
                try:
                    pixmap = QPixmap(str(disk_path))
                    if not pixmap.isNull():
                        self._add_to_avatar_cache(user_id, pixmap)
                        return pixmap
                except Exception:
                    pass

        url = self._extract_avatar_url(avatar_url)
        fallback = self._create_initials_avatar(display_name)

        if url and self._avatar_download_queue_size < self._max_download_queue:
            self._safe_submit(self._download_avatar_async, user_id, url, display_name)
            return fallback

        self._add_to_avatar_cache(user_id, fallback)
        return fallback

    def _add_to_avatar_cache(self, user_id: str, pixmap: QPixmap) -> None:
        """Add avatar to cache with automatic LRU eviction."""
        with self._avatar_cache_lock:
            if len(self._avatar_cache) >= self.MAX_MEMORY_AVATARS:
                try:
                    oldest_id, _ = self._avatar_cache.popitem(last=False)
                    self.logger.debug(f"Evicted old avatar: {oldest_id}")
                except KeyError:
                    pass
            self._avatar_cache[user_id] = pixmap

    def _download_avatar_async(self, user_id: str, url: str, display_name: str) -> None:
        """Thread-safe avatar download with queue tracking."""
        self._avatar_download_queue_size += 1
        try:
            lock = self._get_download_lock(user_id)
            if not lock.acquire(blocking=False):
                self.logger.debug(f"Avatar download already in progress for {user_id}")
                return

            try:
                headers = {
                    "User-Agent": self.header_profiles[0]["User-Agent"],
                    "Referer": "https://www.tiktok.com/",
                }
                response = requests.get(url, headers=headers, timeout=5)
                response.raise_for_status()

                pixmap = QPixmap()
                if not pixmap.loadFromData(response.content) or pixmap.isNull():
                    self.logger.debug(f"Failed to load avatar image for {user_id}")
                    fallback = self._create_initials_avatar(display_name)
                    self._add_to_avatar_cache(user_id, fallback)
                    return

                circular = self._create_circular_avatar(pixmap)
                if circular.isNull():
                    self.logger.debug(f"Failed to create circular avatar for {user_id}")
                    fallback = self._create_initials_avatar(display_name)
                    self._add_to_avatar_cache(user_id, fallback)
                    return

                if self._avatar_disk_cache_dir:
                    try:
                        disk_path = self._get_avatar_cache_path(user_id)
                        circular.save(str(disk_path), "PNG")
                        self._cleanup_disk_cache_aggressive()
                    except Exception as exc:
                        self.logger.debug(f"Failed to save avatar to disk: {exc}")

                with self._avatar_cache_lock:
                    if user_id in self._avatar_cache:
                        self._avatar_cache.pop(user_id)

                    if len(self._avatar_cache) >= self.MAX_MEMORY_AVATARS:
                        try:
                            oldest_id, _ = self._avatar_cache.popitem(last=False)
                            self.logger.debug(f"Evicted old avatar: {oldest_id}")
                        except KeyError:
                            pass

                    self._avatar_cache[user_id] = circular

                self._safe_emit_avatar_update(user_id, circular)
                self.logger.debug(f"Avatar downloaded and cached for {user_id}")

            finally:
                try:
                    lock.release()
                except Exception:
                    pass

        except requests.Timeout:
            self.logger.debug(f"Avatar download timeout for {user_id}")
            fallback = self._create_initials_avatar(display_name)
            self._add_to_avatar_cache(user_id, fallback)
        except Exception as exc:
            self.logger.debug(f"Avatar download failed for {user_id}: {exc}")
            fallback = self._create_initials_avatar(display_name)
            self._add_to_avatar_cache(user_id, fallback)
        finally:
            self._avatar_download_queue_size -= 1

    def _safe_emit_avatar_update(self, user_id: str, avatar_pixmap: QPixmap) -> None:
        """Thread-safe avatar update emission."""
        try:
            if not self.manager or self._shutting_down:
                return

            if getattr(self.manager, "_shutdown_requested", False) or getattr(
                self.manager, "_cleanup_in_progress", False
            ):
                return

            from PySide6.QtCore import QMetaObject, Qt, Q_ARG

            QMetaObject.invokeMethod(
                self.manager,
                b"_emit_avatar_updated_internal",  # must be bytes
                Qt.ConnectionType.QueuedConnection,
                QGenericArgument("QString", user_id),
                QGenericArgument("QPixmap", avatar_pixmap),
            )

        except Exception as exc:
            self.logger.error(f"Error emitting avatar update: {exc}")

    def _safe_submit(self, fn, *args, **kwargs) -> None:
        """Submit to executor with shutdown check."""
        if self._shutting_down:
            return
        try:
            self._avatar_executor.submit(fn, *args, **kwargs)
        except RuntimeError:
            pass
        except Exception as exc:
            self.logger.error(f"Failed to submit task: {exc}")

    # =========================================================================
    # MESSAGE CACHE WITH AGGRESSIVE CLEANUP
    # =========================================================================
    def add_message_to_cache(self, username: str, message: str, timestamp: Optional[float] = None) -> None:
        """Add message with automatic cleanup (keep only last N)."""
        if timestamp is None:
            timestamp = time.time()

        with self._message_cache_lock:
            self._message_cache.append(
                {
                    "username": username,
                    "message": message,
                    "timestamp": timestamp,
                }
            )
            if len(self._message_cache) > self.MAX_CACHED_MESSAGES:
                del self._message_cache[:-self.MAX_CACHED_MESSAGES]

    def _cleanup_message_cache(self) -> None:
        """Hard trim to last N messages."""
        with self._message_cache_lock:
            if len(self._message_cache) > self.MAX_CACHED_MESSAGES:
                self._message_cache = self._message_cache[-self.MAX_CACHED_MESSAGES :]

    def purge_old_messages(self, keep_last: int = 20) -> int:
        """Trim message cache to last N, return count purged."""
        with self._message_cache_lock:
            before = len(self._message_cache)
            if before > keep_last:
                self._message_cache = self._message_cache[-keep_last:]
            return max(0, before - len(self._message_cache))

    def set_quiz_state(self, quiz_active: bool, question_active: bool = False) -> None:
        """Update quiz state and trigger cleanup if quiz ended."""
        self._quiz_active = quiz_active
        self._quiz_question_active = question_active

        if not quiz_active and not question_active:
            self._cleanup_message_cache()
            self._cleanup_avatar_cache(force=False)

    def get_recent_messages(self, count: int = 50) -> List[Dict[str, Any]]:
        with self._message_cache_lock:
            return self._message_cache[-count:] if count > 0 else []

    # =========================================================================
    # USER INFO CACHE WITH LRU
    # =========================================================================
    def update_user_info(self, user_id: str, info: Dict[str, Any]) -> None:
        """Store user info with LRU eviction."""
        with self._user_info_lock:
            if len(self._user_info) >= self.MAX_USER_INFO:
                try:
                    self._user_info.popitem(last=False)
                except KeyError:
                    pass
            self._user_info[user_id] = info

    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user info and mark as recently used."""
        with self._user_info_lock:
            if user_id in self._user_info:
                info = self._user_info.pop(user_id)
                self._user_info[user_id] = info
                return info
        return None

    # =========================================================================
    # PERIODIC CLEANUP
    # =========================================================================
    def _maybe_cleanup(self) -> None:
        """Aggressive memory cleanup without interrupting quiz or live flow."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now

        with self._message_cache_lock:
            msg_count = len(self._message_cache)
            if msg_count > 20:
                del self._message_cache[:-20]
                self.logger.debug(
                    f"Trimmed message cache from {msg_count} → {len(self._message_cache)}"
                )

        with self._avatar_cache_lock:
            avatar_count = len(self._avatar_cache)
            if avatar_count > self.MAX_MEMORY_AVATARS:
                excess = avatar_count - self.MAX_MEMORY_AVATARS
                for _ in range(excess):
                    try:
                        self._avatar_cache.popitem(last=False)
                    except Exception:
                        break
                self.logger.debug(
                    f"Trimmed avatar cache from {avatar_count} → {len(self._avatar_cache)}"
                )

        with self._user_info_lock:
            info_count = len(self._user_info)
            if info_count > 150:
                excess = info_count - 150
                for _ in range(excess):
                    try:
                        self._user_info.popitem(last=False)
                    except Exception:
                        break
                self.logger.debug(
                    f"Trimmed user info from {info_count} → {len(self._user_info)}"
                )

        try:
            self._cleanup_disk_cache_aggressive()
        except Exception as exc:
            self.logger.warning(f"Disk cache cleanup skipped: {exc}")

        with self._avatar_cache_lock:
            avatar_count = len(self._avatar_cache)
        with self._message_cache_lock:
            message_count = len(self._message_cache)
        with self._user_info_lock:
            user_info_count = len(self._user_info)

        self.logger.info(
            "Memory Snapshot → "
            f"Avatars: {avatar_count}, Messages: {message_count}, Users: {user_info_count}"
        )

    # =========================================================================
    # CLEANUP & SHUTDOWN
    # =========================================================================
    def force_cleanup(self) -> None:
        """Aggressive cleanup of all caches."""
        self.logger.info("Force cleanup requested")

        with self._message_cache_lock:
            old_size = len(self._message_cache)
            self._message_cache = self._message_cache[-20:]
            self.logger.info(f"Cleaned {old_size - len(self._message_cache)} messages")

        with self._avatar_cache_lock:
            old_size = len(self._avatar_cache)
            items = list(self._avatar_cache.items())
            self._avatar_cache.clear()
            for user_id, pixmap in items[-20:]:
                self._avatar_cache[user_id] = pixmap
            self.logger.info(f"Cleaned {old_size - len(self._avatar_cache)} avatars")

        with self._user_info_lock:
            old_size = len(self._user_info)
            items = list(self._user_info.items())
            self._user_info.clear()
            for user_id, info in items[-50:]:
                self._user_info[user_id] = info
            self.logger.info(
                f"Cleaned {old_size - len(self._user_info)} user info entries"
            )

    def shutdown(self) -> None:
        """Thread-safe shutdown."""
        self.logger.info("Shutting down ClientManager...")
        self._shutting_down = True

        try:
            try:
                self._avatar_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

            with self._avatar_cache_lock:
                self._avatar_cache.clear()
            with self._message_cache_lock:
                self._message_cache.clear()
            with self._user_info_lock:
                self._user_info.clear()
            with self._avatar_lock_dict_lock:
                self._avatar_download_locks.clear()

            self.logger.info("ClientManager shutdown complete")
        except Exception as exc:
            self.logger.error(f"Error during shutdown: {exc}")

    def clear_cache(self, disk_cache: bool = False) -> None:
        """Clear all caches."""
        with self._message_cache_lock:
            self._message_cache.clear()
        with self._avatar_cache_lock:
            self._avatar_cache.clear()
        with self._user_info_lock:
            self._user_info.clear()

        if disk_cache and self._avatar_disk_cache_dir:
            try:
                for file in self._avatar_disk_cache_dir.glob("*.png"):
                    file.unlink()
            except Exception as exc:
                self.logger.error(f"Error clearing disk cache: {exc}")

    def get_debug_info(self) -> Dict[str, Any]:
        disk_files = 0
        try:
            if self._avatar_disk_cache_dir:
                disk_files = len(list(self._avatar_disk_cache_dir.glob("*.png")))
        except Exception:
            pass

        return {
            "is_connected": getattr(self.manager, "_is_connected", False),
            "cached_avatars": len(self._avatar_cache),
            "cached_messages": len(self._message_cache),
            "cached_user_info": len(self._user_info),
            "disk_cached_files": disk_files,
            "download_queue_size": self._avatar_download_queue_size,
        }

    @staticmethod
    def start_client() -> bool:
        return False

    @staticmethod
    def setup_event_handlers() -> bool:
        return True

    def cleanup_client(self) -> None:
        pass

    def cleanup_client_internal(self) -> None:
        pass
