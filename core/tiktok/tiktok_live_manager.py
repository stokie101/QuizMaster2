import gc
import logging
import multiprocessing as mp
import queue
import threading
import time
from typing import Optional, List, Dict, Any, Tuple

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QPixmap

from core.tiktok.tiktok_worker import _tiktok_worker_main


class TikTokLiveManager(QObject):
    """QuizMaster-only TikTok LIVE manager.

    The old app routed TikTok comments through chess, genre wheel, live-event dispatcher,
    Spotify, and TTS handlers. QuizMaster only needs TikTok LIVE chat/gift events for
    quiz answers, leaderboard users, and frontend status/chat updates.
    """

    connection_status_changed = Signal(bool, str)
    debug_message_signal = Signal(str, str)
    comment_received = Signal(str, str, QPixmap, str)
    live_ended = Signal()
    viewer_count_updated = Signal(int)
    avatar_updated = Signal(str, QPixmap)
    gift_received = Signal(str, str, str, int, int, str, str)

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls):
        return cls() if cls._instance is None else cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self._initialized = True

        self._username: Optional[str] = None
        self._is_connected = False
        self._connection_attempted = False
        self._viewer_count = 0
        self._exact_live_follower_count: Optional[int] = None
        self._manual_disconnect = False
        self._shutdown_requested = False
        self._cleanup_in_progress = False
        self._disconnect_lock = threading.Lock()

        self._proc: Optional[mp.Process] = None
        self._event_q: Optional[mp.Queue] = None
        self._stop_ev: Optional[mp.Event] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._pump_running = threading.Event()

        self._library_checker = None
        self._connection_manager = None
        self._client_manager = None
        self._obs_handler = None
        self._gift_widgets_handler = None
        self._signal_connections: List[Tuple] = []
        self._quiz_signals: Optional[Dict[str, Any]] = None
        self._last_emitted_status: Optional[Tuple[str, str]] = None
        self._stream_pulse_bridge = None
        self._user_info: Dict[str, Any] = {}
        self._user_access_order: List[str] = []

        from core.services.service_locator import ServiceLocator
        self.service_locator = ServiceLocator.get_instance()

        try:
            self.avatar_updated.connect(self._on_avatar_updated)
        except Exception as e:
            self.logger.warning(f"Could not connect avatar_updated handler: {e}")

        try:
            from core.tiktok.tiktok_gift_database import TikTokGiftDatabase
            import os
            db_path = os.path.join(os.path.dirname(__file__), "tiktok_gifts.db")
            self.gift_db = TikTokGiftDatabase.get_instance(db_path)
            self.logger.info("🎁 Gift database initialized")
        except Exception as e:
            self.logger.warning(f"Gift database not available: {e}")
            self.gift_db = None

        self.logger.info("TikTokLiveManager initialized for QuizMaster-only events")

    def _get_bridge(self):
        try:
            return self.service_locator.get_service("HTTPBridgeServer")
        except Exception:
            return None

    def emit_tiktok_status(self, status: str, message: str, bridge=None, force: bool = False) -> bool:
        normalized = (status, message)
        if not force and normalized == self._last_emitted_status:
            return False
        bridge = bridge or self._get_bridge()
        if not bridge:
            return False
        try:
            bridge.emit_signal_ws("tiktok_status", {
                "state": status,
                "message": message,
                "connected": status == "connected",
                "username": self._username,
                "viewer_count": self._viewer_count,
            })
            self._last_emitted_status = normalized
            return True
        except Exception:
            return False

    @Slot(str, QPixmap)
    def _on_avatar_updated(self, user_id: str, avatar_pixmap: QPixmap):
        try:
            leaderboard = self.service_locator.get_service("LeaderboardManager")
            if leaderboard and isinstance(avatar_pixmap, QPixmap) and not avatar_pixmap.isNull():
                leaderboard.update_user_avatar(user_id, avatar_pixmap)
        except Exception as e:
            self.logger.debug(f"Error forwarding avatar update for {user_id}: {e}")

    @property
    def library_checker(self):
        if self._library_checker is None and not self._shutdown_requested:
            try:
                from core.tiktok.library_checker import LibraryChecker
                self._library_checker = LibraryChecker(self)
            except Exception as e:
                self.logger.error(f"Error creating LibraryChecker: {e}")
        return self._library_checker

    @property
    def connection_manager(self):
        if self._connection_manager is None and not self._shutdown_requested:
            try:
                from core.tiktok.connection_manager import ConnectionManager
                self._connection_manager = ConnectionManager()
            except Exception as e:
                self.logger.debug(f"ConnectionManager unavailable: {e}")
        return self._connection_manager

    @property
    def client_manager(self):
        if self._client_manager is None and not self._shutdown_requested:
            try:
                from core.tiktok.client_manager import ClientManager
                self._client_manager = ClientManager(self)
            except Exception as e:
                self.logger.debug(f"ClientManager unavailable: {e}")
        return self._client_manager

    @property
    def signals(self):
        return self

    def _connect_quiz_signals(self):
        self._quiz_signals = {"transport": "http_bridge", "connected": True}

    def _disconnect_signals(self):
        self._signal_connections.clear()
        self._quiz_signals = None

    def _sync_streampulse_widget_event(self, event_name: str, payload: Dict[str, Any]):
        try:
            if self._stream_pulse_bridge is None:
                from core.server.streampulse.streampulse_bridge import StreamPulseTikTokBridge
                self._stream_pulse_bridge = StreamPulseTikTokBridge.get_instance()
            self._stream_pulse_bridge.process_event(event_name, payload)
        except Exception as exc:
            self.logger.debug(f"StreamPulse sync skipped for {event_name}: {exc}")

    def get_exact_live_follower_count(self) -> Optional[int]:
        return self._exact_live_follower_count

    def initialize(self) -> bool:
        if self._shutdown_requested or self._cleanup_in_progress:
            return False
        try:
            if self.library_checker:
                self.library_checker.check_and_import_tiktoklive()
            self.logger.info("TikTokLiveManager initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error initializing: {e}")
            return False

    def connect_to_user(self, username: str, max_retries: int = 3, mock_mode: bool = False) -> bool:
        if self._cleanup_in_progress:
            self.logger.warning("Cannot connect: cleanup in progress")
            return False
        self._shutdown_requested = False
        self._manual_disconnect = False
        self._connection_attempted = True
        self._exact_live_follower_count = None

        try:
            from core.server.session_identity import RuntimeSessionIdentity
            from core.tiktok.profile_stats import TikTokProfileStatsService
            TikTokProfileStatsService.get_instance().refresh_in_background(
                RuntimeSessionIdentity.profile_id(), username, force=True,
            )
        except Exception as exc:
            self.logger.debug("TikTok profile stats refresh could not start: %s", exc)

        if mock_mode:
            return self._mock_connect(username)

        if self.library_checker and not self.library_checker.has_tiktok_live:
            self.emit_connection_error("TikTokLive library not available")
            return False

        try:
            self._start_process(username)
            self._username = username
            self._is_connected = True
            self._connect_quiz_signals()
            msg = f"Connecting to @{username}…"
            try:
                self.connection_status_changed.emit(True, msg)
            except Exception:
                pass
            self.emit_tiktok_status("connecting", msg, force=True)
            return True
        except Exception as e:
            self.logger.error(f"Error during connection: {e}", exc_info=True)
            self.emit_connection_error(f"Connection failed: {e}")
            return False

    def disconnect(self):
        if self._cleanup_in_progress:
            return
        self.logger.info("Disconnecting from TikTok Live")
        self._manual_disconnect = True
        self._shutdown_requested = True
        try:
            self._stop_process()
            old_username = self._username
            self._reset_state()
            msg = f"Disconnected from @{old_username}" if old_username else "Disconnected"
            try:
                self.connection_status_changed.emit(False, msg)
            except Exception:
                pass
            self.emit_tiktok_status("disconnected", msg, force=True)
            gc.collect()
        except Exception as e:
            self.logger.error(f"Error during disconnect: {e}", exc_info=True)

    def force_reconnect(self, max_retries: int = 3) -> bool:
        if not self._username or self._shutdown_requested or self._cleanup_in_progress:
            return False
        username = self._username
        self._manual_disconnect = False
        self._shutdown_requested = False
        try:
            self._stop_process()
        except Exception:
            pass
        return self.connect_to_user(username, max_retries)

    def _start_process(self, username: str):
        self._stop_process()
        ctx = mp.get_context("spawn")
        self._event_q = ctx.Queue(maxsize=1000)
        self._stop_ev = ctx.Event()
        header_profiles = [
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
            },
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
            },
            {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Referer": "https://www.tiktok.com/",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ]
        self._proc = ctx.Process(
            target=_tiktok_worker_main,
            name=f"TikTokWorker-{username}",
            args=(self._stop_ev, self._event_q, username, header_profiles),
            daemon=True,
        )
        self._proc.start()
        self.logger.info(f"TikTok worker process started (pid={self._proc.pid})")
        self._pump_running.set()
        self._pump_thread = threading.Thread(target=self._event_pump_loop, name="TikTokEventPump", daemon=True)
        self._pump_thread.start()

    def _stop_process(self):
        if self._pump_thread and self._pump_thread.is_alive():
            self._pump_running.clear()
            self._pump_thread.join(timeout=2.0)
        self._pump_thread = None
        if self._stop_ev:
            try:
                self._stop_ev.set()
            except Exception:
                pass
        if self._proc and self._proc.is_alive():
            self._proc.join(timeout=5.0)
            if self._proc.is_alive():
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc.join(timeout=3.0)
        if self._event_q:
            try:
                self._event_q.close()
            except Exception:
                pass
        self._proc = None
        self._event_q = None
        self._stop_ev = None

    def _event_pump_loop(self):
        self.logger.debug("Event pump started")
        bridge = self._get_bridge()
        while self._pump_running.is_set():
            if not self._event_q:
                break
            try:
                evt = self._event_q.get(timeout=0.5)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                break
            try:
                etype = evt.get("type")
                if etype == "connected":
                    self._is_connected = True
                    msg = evt.get("message", "Connected")
                    try:
                        self.connection_status_changed.emit(True, msg)
                        self.debug_message_signal.emit("Connection established", "success")
                    except RuntimeError:
                        pass
                    self.emit_tiktok_status("connected", msg, bridge=bridge, force=True)

                elif etype == "disconnected":
                    self._is_connected = False
                    msg = evt.get("message", "Disconnected")
                    try:
                        self.connection_status_changed.emit(False, msg)
                        if not self._manual_disconnect and not self._shutdown_requested:
                            self.live_ended.emit()
                    except RuntimeError:
                        pass
                    self.emit_tiktok_status("disconnected", msg, bridge=bridge, force=True)

                elif etype == "error":
                    msg = evt.get("message", "Client error")
                    try:
                        self.debug_message_signal.emit(msg, "error")
                    except RuntimeError:
                        pass
                    self.emit_tiktok_status("error", msg, bridge=bridge, force=True)

                elif etype == "viewer_count":
                    count = int(evt.get("count", 0))
                    self._viewer_count = count
                    try:
                        self.viewer_count_updated.emit(count)
                    except RuntimeError:
                        pass
                    self._emit_bridge(bridge, "tiktok_viewer_count", {"viewer_count": count, "count": count})
                    self._sync_streampulse_widget_event("viewer_count", {"viewer_count": count, "count": count})

                elif etype == "live_follower_count":
                    count = evt.get("follower_count")
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        self._exact_live_follower_count = count

                elif etype == "status":
                    msg = evt.get("message", "")
                    level = evt.get("level", "info")
                    if msg:
                        try:
                            self.debug_message_signal.emit(msg, level)
                        except RuntimeError:
                            pass
                        self._emit_bridge(bridge, "tiktok_debug", {"level": level, "message": msg})

                elif etype == "comment":
                    self._handle_comment_event(evt, bridge)

                elif etype == "gift":
                    self._handle_gift_event(evt, bridge)

                elif etype in {"follow", "share", "like", "join"}:
                    self._handle_simple_social_event(etype, evt, bridge)
            except Exception as e:
                self.logger.error(f"Error handling TikTok worker event: {e}", exc_info=True)
        self.logger.debug("Event pump stopped")

    def _emit_bridge(self, bridge, event_name: str, payload: Dict[str, Any]):
        if not bridge:
            bridge = self._get_bridge()
        if not bridge:
            return
        try:
            bridge.emit_signal_ws(event_name, payload)
        except Exception as exc:
            self.logger.debug(f"Bridge emit skipped for {event_name}: {exc}")

    def _handle_comment_event(self, evt: Dict[str, Any], bridge=None):
        username = evt.get("username", "Anonymous")
        comment = evt.get("comment", "")
        unique_id = evt.get("unique_id", username)
        avatar_url = evt.get("avatar_url") or ""
        user_id = unique_id or username
        payload = {
            "platform": "tiktok",
            "username": username,
            "uniqueId": unique_id,
            "unique_id": unique_id,
            "userId": user_id,
            "user_id": user_id,
            "comment": comment,
            "message": comment,
            "profilePictureUrl": avatar_url,
            "avatar_url": avatar_url,
        }
        if self.client_manager and hasattr(self.client_manager, "_user_info"):
            self.client_manager._user_info.setdefault(user_id, {})["avatar_url"] = avatar_url
            self.client_manager._user_info.setdefault(user_id, {})["username"] = username
        self._emit_bridge(bridge, "tiktok_chat_message", payload)
        self.handle_comment(username, comment, None, None, unique_id)
        self._sync_streampulse_widget_event("tiktok_chat_message", payload)

    def _handle_gift_event(self, evt: Dict[str, Any], bridge=None):
        username = evt.get("username", "Anonymous")
        unique_id = evt.get("unique_id", username)
        gift_name = evt.get("gift_name", "Unknown")
        gift_id = int(evt.get("gift_id", 0) or 0)
        gift_count = int(evt.get("gift_count", 1) or 1)
        avatar_url = evt.get("avatar_url") or ""
        gift_image_url = evt.get("gift_image_url") or ""
        payload = {
            "username": username,
            "unique_id": unique_id,
            "uniqueId": unique_id,
            "gift_name": gift_name,
            "giftName": gift_name,
            "gift_id": gift_id,
            "gift_count": gift_count,
            "giftCount": gift_count,
            "avatar_url": avatar_url,
            "profilePictureUrl": avatar_url,
            "gift_image_url": gift_image_url,
            "giftImageUrl": gift_image_url,
        }
        try:
            self.gift_received.emit(username, unique_id, gift_name, gift_id, gift_count, avatar_url, gift_image_url)
        except RuntimeError:
            pass
        self._emit_bridge(bridge, "tiktok_gift", payload)
        self._sync_streampulse_widget_event("tiktok_gift", payload)

    def _handle_simple_social_event(self, etype: str, evt: Dict[str, Any], bridge=None):
        username = evt.get("username", "Anonymous")
        unique_id = evt.get("unique_id", username)
        avatar_url = evt.get("avatar_url") or ""
        payload = {
            "username": username,
            "unique_id": unique_id,
            "uniqueId": unique_id,
            "avatar_url": avatar_url,
            "profilePictureUrl": avatar_url,
        }
        if etype == "like":
            payload["like_count"] = evt.get("count", 1)
        self._emit_bridge(bridge, f"tiktok_{etype}", payload)
        self._sync_streampulse_widget_event(f"tiktok_{etype}", payload)

    def handle_comment(self, username: str, comment: str, event_obj: Any = None,
                       avatar_pixmap: QPixmap = None, unique_id: str = ""):
        if self._shutdown_requested or self._cleanup_in_progress:
            return
        try:
            user_id = unique_id if unique_id else username
            display_name = username
            if avatar_pixmap is None or not isinstance(avatar_pixmap, QPixmap) or avatar_pixmap.isNull():
                if self.client_manager:
                    avatar_url = None
                    if hasattr(self.client_manager, "_user_info"):
                        avatar_url = self.client_manager._user_info.get(user_id, {}).get("avatar_url")
                    avatar_pixmap = self.client_manager.get_avatar(user_id, avatar_url, display_name)
                if not avatar_pixmap or not isinstance(avatar_pixmap, QPixmap):
                    avatar_pixmap = QPixmap()
            self._register_user_with_leaderboard(user_id, display_name, avatar_pixmap)
            try:
                self.comment_received.emit(display_name, comment, avatar_pixmap, user_id)
            except RuntimeError:
                pass
            self._process_as_quiz_answer(avatar_pixmap, user_id, comment, display_name)
        except Exception as e:
            self.logger.error(f"Error handling TikTok comment: {e}", exc_info=True)

    def _register_user_with_leaderboard(self, user_id: str, display_name: str, avatar_pixmap: QPixmap):
        try:
            leaderboard = self.service_locator.get_service("LeaderboardManager")
            if leaderboard and hasattr(leaderboard, "register_chat_user_with_pixmap"):
                leaderboard.register_chat_user_with_pixmap(user_id, display_name, avatar_pixmap)
        except Exception as e:
            self.logger.debug(f"Leaderboard register skipped for {display_name}: {e}")

    def _process_as_quiz_answer(self, avatar_pixmap: QPixmap, user_id: str, comment: str, display_name: str = None):
        try:
            quiz_manager = self.service_locator.get_service("QuizManager")
            if not quiz_manager or not comment:
                return
            ok = quiz_manager.process_answer(user_id, comment, display_name or user_id)
            if ok:
                self.logger.debug("TikTok quiz answer accepted user_id=%s comment=%s", user_id, comment)
        except Exception as e:
            self.logger.error(f"Error processing TikTok answer: {e}", exc_info=True)

    def _emit_avatar_updated_internal(self, user_id: str, avatar_pixmap: QPixmap):
        if self._shutdown_requested or self._cleanup_in_progress:
            return
        try:
            self.avatar_updated.emit(user_id, avatar_pixmap)
        except RuntimeError:
            pass

    def is_connected(self) -> bool:
        return self._is_connected and not self._manual_disconnect and not self._shutdown_requested

    def get_current_username(self) -> Optional[str]:
        return self._username

    def get_viewer_count(self) -> int:
        return self._viewer_count

    def get_connection_state(self) -> str:
        if self._shutdown_requested or self._cleanup_in_progress:
            return "disconnected"
        if self._is_connected:
            return "connected"
        return "disconnected"

    def update_viewer_count(self, count: int):
        if self._shutdown_requested or self._cleanup_in_progress:
            return
        self._viewer_count = count
        try:
            self.viewer_count_updated.emit(count)
        except Exception:
            pass

    def emit_connection_error(self, error_msg, error_type=None):
        if self._shutdown_requested or self._cleanup_in_progress:
            return
        user_friendly_msg = str(error_msg)
        self._is_connected = False
        try:
            self.connection_status_changed.emit(False, user_friendly_msg)
            self.debug_message_signal.emit(user_friendly_msg, "error")
        except Exception:
            pass
        self.emit_tiktok_status("error", user_friendly_msg, force=True)
        self.logger.error(f"Connection error: {user_friendly_msg}")

    def emit_signal(self, signal_name: str, *args):
        if self._shutdown_requested or self._cleanup_in_progress:
            return
        try:
            if hasattr(self, signal_name):
                signal = getattr(self, signal_name)
                if hasattr(signal, "emit"):
                    signal.emit(*args)
            bridge = self._get_bridge()
            if bridge:
                payload = args[0] if len(args) == 1 else list(args)
                bridge.emit_signal_ws(signal_name, payload)
        except Exception as e:
            self.logger.error(f"Error emitting signal {signal_name}: {e}", exc_info=True)

    def _mock_connect(self, username: str) -> bool:
        if self._shutdown_requested or self._cleanup_in_progress:
            return False
        self._username = username
        self._is_connected = True
        self._viewer_count = 100
        msg = f"Mock connected to @{username}"
        self.connection_status_changed.emit(True, msg)
        self.emit_tiktok_status("connected", msg, force=True)
        return True

    def get_library_status(self) -> Dict[str, Any]:
        try:
            if self.library_checker:
                return self.library_checker.get_library_status()
        except Exception as e:
            self.logger.debug(f"Error getting library status: {e}")
        return {"status": "not_initialized"}

    def get_debug_info(self) -> Dict[str, Any]:
        return {
            "connection_status": {
                "is_connected": self._is_connected,
                "username": self._username,
                "viewer_count": self._viewer_count,
                "manual_disconnect": self._manual_disconnect,
                "shutdown_requested": self._shutdown_requested,
                "cleanup_in_progress": self._cleanup_in_progress,
                "connection_attempted": self._connection_attempted,
            },
            "library_status": self.get_library_status(),
            "connection_state": self.get_connection_state(),
            "signal_connections": len(self._signal_connections),
            "quiz_signals_connected": self._quiz_signals is not None,
            "worker_alive": self._proc.is_alive() if self._proc else False,
        }

    def _reset_state(self):
        self._is_connected = False
        self._viewer_count = 0
        self._username = None
        self._connection_attempted = False
        self._last_emitted_status = None
        self._user_info.clear()
        self._user_access_order.clear()

    def shutdown(self):
        if self._cleanup_in_progress:
            return
        self.logger.info("Shutting down TikTokLiveManager")
        self._cleanup_in_progress = True
        self._shutdown_requested = True
        self._manual_disconnect = True
        try:
            self._stop_process()
            self._disconnect_signals()
        finally:
            self._reset_state()
            self._cleanup_in_progress = False
