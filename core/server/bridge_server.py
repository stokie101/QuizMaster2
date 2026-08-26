import asyncio
import bisect
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Any
from urllib.parse import parse_qs, urlparse

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.utils.resource_loader import get_resource_path
from config.config_manager import ConfigManager
from core.server.url_config import PUBLIC_BASE_URL, LOCAL_BASE_URL, HOSTED_WIDGETS_BASE_URL, as_dict as quizmaster_url_config
# Service Imports
from core.services.service_locator import ServiceLocator

logger = logging.getLogger('HTTPBridge')


def _first_string(mapping: Dict[str, Any] | None, *keys: str) -> str:
    for key in keys:
        value = (mapping or {}).get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ''
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def _public_widget_id_from_path(path: str) -> str:
    parsed_path = urlparse(path or '').path
    parts = [part for part in parsed_path.split('/') if part]
    if len(parts) >= 2 and parts[0] == 'u':
        return parts[1].strip()
    return ''


def _public_widget_socket_decision(
    *,
    path: str,
    query: Dict[str, Any] | None,
    auth: Dict[str, Any] | None,
    active_runtime_id: str | None,
    host: str = '',
) -> Dict[str, Any]:
    """Resolve a socket's account room without allowing public traffic into default."""
    auth = auth if isinstance(auth, dict) else {}
    query = query or {}
    candidate_paths = [
        path,
        _first_string(auth, 'path', 'widget_path', 'page_path', 'url', 'href'),
        _first_string(query, 'path', 'widget_path', 'page_path', 'url', 'href'),
    ]
    path_public_widget_id = next(
        (candidate for candidate in map(_public_widget_id_from_path, candidate_paths) if candidate),
        '',
    )
    explicit_public_widget_ids = [
        candidate for candidate in (
            _first_string(auth, 'public_widget_id', 'publicWidgetId'),
            _first_string(query, 'public_widget_id', 'publicWidgetId'),
        )
        if candidate
    ]
    requested_public_widget_id = (
        path_public_widget_id
        or next(iter(explicit_public_widget_ids), '')
    )
    public_path_indicated = any('/u/' in urlparse(candidate or '').path for candidate in candidate_paths)
    requested_room = _first_string(query, 'room', 'room_id')
    requested_widget_type = _first_string(query, 'widget_type', 'widgetType')
    requested_session_id = _first_string(query, 'session_id', 'live_session_id', 'sessionId', 'liveSessionId')
    public_widget_indicated = bool(
        public_path_indicated
        or _first_string(auth, 'public_widget_id', 'publicWidgetId', 'public_widget', 'is_public_widget')
        or _first_string(query, 'public_widget_id', 'publicWidgetId', 'public_widget', 'is_public_widget')
        or requested_room == 'chess_game_room'
        or (requested_widget_type in {'quiz', 'chess'} and requested_session_id)
        or host.split(':', 1)[0].lower() == urlparse(HOSTED_WIDGETS_BASE_URL).hostname
    )
    active_runtime_id = str(active_runtime_id or '').strip()

    if public_widget_indicated and not requested_public_widget_id:
        return {
            'requested_public_widget_id': None,
            'resolved_public_widget_id': None,
            'join_room': None,
            'reason': 'missing_public_widget_id_rejected',
        }
    supplied_public_widget_ids = {
        candidate for candidate in [path_public_widget_id, *explicit_public_widget_ids] if candidate
    }
    if public_widget_indicated and (
        not active_runtime_id
        or requested_public_widget_id != active_runtime_id
        or supplied_public_widget_ids != {active_runtime_id}
    ):
        mismatched_public_widget_id = next(
            (candidate for candidate in supplied_public_widget_ids if candidate != active_runtime_id),
            requested_public_widget_id,
        )
        return {
            'requested_public_widget_id': mismatched_public_widget_id or None,
            'resolved_public_widget_id': None,
            'join_room': None,
            'reason': 'public_widget_id_mismatch_rejected',
        }
    if public_widget_indicated:
        return {
            'requested_public_widget_id': requested_public_widget_id,
            'resolved_public_widget_id': requested_public_widget_id,
            'join_room': f'profile:{requested_public_widget_id}',
            'reason': 'public_widget_profile_room',
        }
    return {
        'requested_public_widget_id': None,
        'resolved_public_widget_id': None,
        'join_room': 'default',
        'reason': 'internal_ui_default',
    }


def _parse_trusted_origins(raw: str | None = None):
    # # Fix: Parse strict trusted origins list from TRUSTED_ORIGINS env and default to QuizMaster public/local origins.
    source = raw if raw is not None else os.getenv('TRUSTED_ORIGINS', '')
    if not source.strip():
        return [LOCAL_BASE_URL, 'http://localhost:5555', PUBLIC_BASE_URL]
    return [origin.strip() for origin in source.split(',') if origin.strip()]


class HTTPBridgeServer:
    _instance = None
    _lock = threading.Lock()

    # CRITICAL MEMORY LIMITS
    MAX_EVENT_STORE = 500
    MAX_CONNECTED_CLIENTS = 50
    EVENT_CLEANUP_INTERVAL = 60
    CLIENT_MONITOR_INTERVAL = 30
    STALE_CLIENT_TIMEOUT = 3160

    def __new__(cls, host: str = 'localhost', port: int = 5555):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(HTTPBridgeServer, cls).__new__(cls)
                    cls._instance._initialized = False
                    cls._instance.running = False
        return cls._instance

    def __init__(self, host: str = 'localhost', port: int = 5555):
        if getattr(self, "_initialized", False):
            return

        from contextlib import asynccontextmanager

        self.public_url = PUBLIC_BASE_URL
        self.host = '127.0.0.1'
        self.port = port

        # state used in lifespan
        self.main_loop = None
        self._is_shutting_down = False

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup
            self.main_loop = asyncio.get_running_loop()
            self._is_shutting_down = False

            # QuizMaster does not start unrelated gift or relay background services.

            yield

            # Shutdown
            self._is_shutting_down = True

        # create the app ONCE, with lifespan
        self.app = FastAPI(
            title="QuizMaster-HTTPBridge",
            lifespan=lifespan
        )

        self._initialized = True

        # --- ROBUST CONFIG MANAGER INITIALIZATION ---
        try:
            sl = ServiceLocator.get_instance()
            sl.register_service("Server", self)
            self.config_manager = None

            if hasattr(sl, 'has_service') and sl.has_service("ConfigManager"):
                if hasattr(sl, 'get_service'):
                    self.config_manager = sl.get_service("ConfigManager")
                elif hasattr(sl, 'get'):
                    self.config_manager = sl.get("ConfigManager")

            if not self.config_manager:
                logger.info("⚠️ ConfigManager not in ServiceLocator, creating singleton instance")
                self.config_manager = ConfigManager.get_instance()

                if hasattr(sl, 'register_service'):
                    sl.register_service("ConfigManager", self.config_manager)
                elif hasattr(sl, 'register'):
                    sl.register("ConfigManager", self.config_manager)
            else:
                logger.info("✅ ConfigManager found in ServiceLocator")

        except Exception as e:
            logger.warning(f"ConfigManager setup fallback due to: {e}")
            self.config_manager = ConfigManager.get_instance()

        # DO NOT recreate self.app here
        self.trusted_origins = _parse_trusted_origins()

        from fastapi.staticfiles import StaticFiles
        from core.utils.embedded_web_assets import has_embedded_assets

        self.BASE_DIR = Path(__file__).resolve().parent

        # In release/frozen builds the frontend ships inside the generated asset
        # bundle and is served by the embedded routes in embedded_frontend_routes.
        # Do NOT also mount the on-disk folders then: under Nuitka, package dirs
        # such as core/assets and core/server/overlays get materialized in the
        # dist without their image/asset files, so a disk StaticFiles mount would
        # exist, shadow the embedded /core/assets route, and 404 the logo. Only
        # mount from disk in development, where no bundle exists.
        if has_embedded_assets():
            logger.info("📦 Serving frontend assets from the embedded bundle (no on-disk mounts)")
        else:
            # Mount standard static folders (development / source runs).
            static_dirs = ["static", "themes", "overlays"]
            for folder in static_dirs:
                path = self.BASE_DIR / folder
                if path.exists():
                    self.app.mount(f"/{folder}", StaticFiles(directory=str(path)), name=folder)
                    logger.info(f"📁 Mounted static folder: /{folder} → {path}")
                else:
                    logger.warning(f"⚠️ Static folder '{folder}' not found at {path}")

            # Core assets — works in source and PyInstaller builds.
            core_assets_path = get_resource_path("core/assets")

            if core_assets_path.exists():
                self.app.mount(
                    "/core/assets",
                    StaticFiles(directory=str(core_assets_path)),
                    name="core_assets",
                )
                logger.info("📁 Mounted Core Assets: /core/assets → %s", core_assets_path)

            else:
                logger.warning("⚠️ Core Assets not found at %s", core_assets_path)


        @self.app.get("/api/quizmaster/url-config")
        async def get_quizmaster_url_config():
            return {"success": True, "config": quizmaster_url_config()}

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=self.trusted_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"]
        )

        self.socketio = socketio.AsyncServer(
            async_mode='asgi',
            cors_allowed_origins=self.trusted_origins,
            ping_interval=25,
            ping_timeout=60,
            max_http_buffer_size=1000000,
            logger=False,
            engineio_logger=False,
            always_connect=True,
            compression_threshold=1024,
        )

        self.asgi_app = socketio.ASGIApp(self.socketio, other_asgi_app=self.app)

        self._setup_paths()

        self._snapshot_lock = threading.Lock()
        self._clients_lock = threading.Lock()
        self.thread = None
        self.running = False
        self._server = None
        self._startup_exception = None
        self._ready_event = threading.Event()
        self.connected_clients = {}
        self._socket_disconnect_counts = {}
        self.quiz_manager = None

        self.LOCKED_ROOM = "default"
        self._protocol_version = "1.0.0"
        self._event_seq = 0
        self._event_seq_lock = threading.Lock()
        self.last_genre_wheel_winner = None

        self._event_store = deque(maxlen=self.MAX_EVENT_STORE)
        self._event_seq_index = deque(maxlen=self.MAX_EVENT_STORE)
        self._last_event_cleanup = time.time()
        self._emit_loop = None
        self._emit_loop_thread = None
        self._emit_loop_lock = threading.Lock()

        self._reset_version = 0
        self._reset_lock = threading.Lock()
        self._last_reset_time = 0

        self.snapshot = {
            "leaderboard": [],
            "state": "IDLE",
            "quiz_loaded": False,
            "reset_version": self._reset_version
        }

        self._timer_state = {
            "running": False,
            "total": 0,
            "remaining": 0,
            "started_at": None
        }

        self._client_last_seen = {}
        self._client_heartbeat_lock = threading.Lock()
        self._client_monitor_thread = None

        # then your existing register_routes + _setup_socketio_handlers
        from core.server.routes.bridge_routes import register_routes
        register_routes(self.app, self)
        self._setup_socketio_handlers()


    @classmethod
    def get_instance(cls, host: str = 'localhost', port: int = 5555):
        if cls._instance is None:
            cls._instance = cls(host, port)
        return cls._instance

    def _setup_paths(self):
        self.BASE_DIR = Path(__file__).resolve().parent
        self.HTML_DIR = self.BASE_DIR / 'static' / 'html'
        self.JS_DIR = self.BASE_DIR / 'static' / 'js'

    def _start_client_monitor(self):
        if self._client_monitor_thread and self._client_monitor_thread.is_alive():
            return

        def monitor():
            logger.info("Client monitor thread started")
            while self.running:
                try:
                    time.sleep(self.CLIENT_MONITOR_INTERVAL)
                    now = time.time()

                    with self._client_heartbeat_lock:
                        stale_clients = []
                        total_tracked_clients = len(self._client_last_seen)
                        logger.info(
                            "[DIAGNOSTIC] stale-scan started total_clients=%s monitor_interval_s=%s stale_timeout_s=%s now=%s",
                            total_tracked_clients,
                            self.CLIENT_MONITOR_INTERVAL,
                            self.STALE_CLIENT_TIMEOUT,
                            now,
                        )

                        for sid, last_seen in list(self._client_last_seen.items()):
                            age_seconds = now - last_seen
                            if age_seconds > self.STALE_CLIENT_TIMEOUT:
                                logger.warning(
                                    "[DIAGNOSTIC] stale-client candidate sid=%s last_seen=%s age_s=%.2f threshold_s=%s reason=heartbeat_timeout_exceeded",
                                    sid,
                                    last_seen,
                                    age_seconds,
                                    self.STALE_CLIENT_TIMEOUT,
                                )
                                stale_clients.append(sid)

                        for sid in stale_clients:
                            logger.warning(f"⚠️ Removing stale client: {sid}")
                            self._client_last_seen.pop(sid, None)

                            with self._clients_lock:
                                self.connected_clients.pop(sid, None)

                        logger.info(
                            "[DIAGNOSTIC] stale-scan complete removed=%s remaining_tracked_clients=%s",
                            len(stale_clients),
                            len(self._client_last_seen),
                        )

                except Exception as e:
                    logger.error(f"Client monitor error: {e}")

        self._client_monitor_thread = threading.Thread(target=monitor, daemon=True, name="ClientMonitor")
        self._client_monitor_thread.start()

    def _make_envelope(self, signal_name: str, args: tuple) -> Dict[str, Any]:
        with self._event_seq_lock:
            self._event_seq += 1
            seq = self._event_seq

        envelope: Dict[str, Any] = {
            'signal': signal_name,
            'name': signal_name,
            'seq': seq,
            'ts': int(time.time()),
            'protocol_version': self._protocol_version,
            'reset_version': self._reset_version
        }

        if args:
            envelope['args'] = list(args)

        return envelope

    def _get_events_since(self, since_seq: int):
        self._maybe_cleanup_events()

        with self._event_seq_lock:
            if since_seq <= 0:
                return list(self._event_store)
            seq_index = list(self._event_seq_index)
            start_idx = bisect.bisect_right(seq_index, since_seq)
            if start_idx >= len(seq_index):
                return []
            return list(self._event_store)[start_idx:]

    def _ensure_emit_loop(self):
        with self._emit_loop_lock:
            if self._emit_loop and self._emit_loop.is_running():
                return self._emit_loop

            self._emit_loop = asyncio.new_event_loop()

            def _run_emit_loop(loop):
                asyncio.set_event_loop(loop)
                loop.run_forever()

            self._emit_loop_thread = threading.Thread(
                target=_run_emit_loop,
                args=(self._emit_loop,),
                daemon=True,
                name="BridgeEmitLoop"
            )
            self._emit_loop_thread.start()
            return self._emit_loop

    def _submit_emit_coro(self, coro):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
            return
        except RuntimeError:
            pass

        loop = getattr(self, "main_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
            return

        emit_loop = self._ensure_emit_loop()
        asyncio.run_coroutine_threadsafe(coro, emit_loop)

    def emit_soft_cleanup(self, reason: str = "WARNING", memory_mb: float = 0.0):
        payload = {"ts": int(time.time()), "reason": reason, "memory": round(float(memory_mb), 1)}
        logger.info(f"📡 Emitting SOFT cleanup → {payload}")
        self.emit_signal_ws("tiktok_soft_cleanup", payload)

    def notify_frontend_cleanup(self) -> None:
        try:
            logger.info("📡 Emitting FRONTEND hard cleanup")
            self.emit_signal_ws('tiktok_frontend_cleanup', {'ts': int(time.time())})
        except Exception as e:
            logger.warning(f"Failed to notify frontend cleanup: {e}")

    def _maybe_cleanup_events(self):
        now = time.time()
        if now - self._last_event_cleanup < self.EVENT_CLEANUP_INTERVAL:
            return

        self._last_event_cleanup = now

        with self._event_seq_lock:
            old_size = len(self._event_store)
            cutoff_time = now - 300
            new_store = deque(
                (e for e in self._event_store if e.get('ts', 0) > cutoff_time),
                maxlen=self.MAX_EVENT_STORE
            )
            self._event_store = new_store
            self._event_seq_index = deque(
                (int(e.get('seq', 0)) for e in self._event_store),
                maxlen=self.MAX_EVENT_STORE
            )

            cleaned = old_size - len(self._event_store)
            if cleaned > 0:
                logger.debug(f"Cleaned {cleaned} old events from store")

    def _update_snapshot(self, key, value):
        with self._snapshot_lock:
            if key == 'leaderboard' and isinstance(value, list):
                value = value[:100]
            self.snapshot[key] = value

    def _get_snapshot_copy(self):
        with self._snapshot_lock:
            return self.snapshot.copy()

    def _update_timer_state(self, signal_name, *args):
        try:
            if signal_name == 'timer_started':
                duration = args[0] if args else 30
                self._timer_state = {
                    'running': True,
                    'total': duration,
                    'remaining': duration,
                    'started_at': time.time()
                }
            elif signal_name == 'timer_expired':
                self._timer_state['running'] = False
                self._timer_state['remaining'] = 0
            elif signal_name == 'timer_paused':
                if self._timer_state['running']:
                    elapsed = time.time() - self._timer_state['started_at']
                    self._timer_state['remaining'] = max(0, self._timer_state['total'] - elapsed)
                    self._timer_state['running'] = False
            elif signal_name == 'timer_resumed':
                if not self._timer_state['running']:
                    self._timer_state['started_at'] = time.time()
                    self._timer_state['running'] = True

            with self._snapshot_lock:
                self.snapshot['timer'] = dict(self._timer_state)

        except Exception as e:
            logger.error(f"Error updating timer state: {e}")


    def _canonical_signal_name(self, signal_name: str) -> str:
        # # Fix: Normalize legacy aliases to canonical names for deterministic downstream handling.
        aliases = {
            'timer_ended': 'timer_expired',
            'tiktok:gift': 'tiktok_gift',
            'tiktok:comment': 'tiktok_chat_message',
            'tiktok:follow': 'tiktok_follow',
            'tiktok:share': 'tiktok_share',
            'tiktok:like': 'tiktok_like',
            'tiktok:join': 'tiktok_join',
        }
        return aliases.get(signal_name, signal_name)

    def emit_signal_ws(self, signal_name: str, *args):
        signal_name = self._canonical_signal_name(signal_name)

        critical_signals = {
            'quiz_started', 'quiz_ended', 'question_loaded', 'question_changed',
            'timer_started', 'timer_expired', 'timer_tick',
            'answer_received', 'answers_highlighted', 'showing_answers',
            'answer_display_complete',
            'leaderboard_updated', 'leaderboard_reset_requested',
            'tiktok_soft_cleanup', 'tiktok_frontend_cleanup',
            'tiktok_chat_message', 'state_changed',
            'quiz_data_loaded'
        }

        if signal_name in ['quiz_started', 'question_changed', 'timer_started', 'answers_highlighted']:
            logger.info(f"📡 Emitting critical signal: {signal_name}")

        if len(self._event_store) > (self.MAX_EVENT_STORE * 0.9):
            if signal_name not in critical_signals:
                logger.debug(f"Event store near capacity, dropping non-critical signal: {signal_name}")
                return

        timer_signals = {'timer_started', 'timer_expired', 'timer_paused', 'timer_resumed'}
        if signal_name in timer_signals:
            self._update_timer_state(signal_name, *args)

        evt = self._make_envelope(signal_name, args)

        with self._event_seq_lock:
            self._event_store.append(evt)
            self._event_seq_index.append(evt['seq'])

        async def _async_emit():
            try:
                await self.socketio.emit("signal", evt, to=self.LOCKED_ROOM)
                # Public per-user widgets join account rooms (profile:<public_widget_id>)
                # instead of the internal default room, so mirror every live signal
                # into each connected widget room. This is what makes the hosted
                # /u/<public_widget_id>/... widgets update live -- no session id needed.
                for room in self._active_widget_rooms():
                    await self.socketio.emit("signal", evt, room=room)
            except Exception as e:
                logger.error(f"❌ async emit failed: {e}")

        self._submit_emit_coro(_async_emit())

    def _active_widget_rooms(self) -> set:
        """Distinct non-default rooms that currently have a connected widget."""
        try:
            with self._clients_lock:
                return {
                    info.get('room')
                    for info in self.connected_clients.values()
                    if info.get('room') and info.get('room') != self.LOCKED_ROOM
                }
        except Exception:
            return set()

    def emit_to_room(self, signal_name: str, data: Any, room: str):
        async def _async_emit():
            try:
                await self.socketio.emit(signal_name, data, room=room)
            except Exception as e:
                logger.error(f"emit_to_room failed: {e}")

        self._submit_emit_coro(_async_emit())

    def sync_snapshot_from_quiz(self, quiz_manager):
        try:
            snap = quiz_manager.get_current_state()
            if not isinstance(snap, dict):
                raise ValueError("QuizManager.get_current_state() must return a dict")

            snapshot = {
                "state": snap.get("state", "IDLE"),
                "quiz_loaded": snap.get("total_questions", 0) > 0,
                "current_question": snap.get("current_question"),
                "question_index": snap.get("question_number", 0),
                "total_questions": snap.get("total_questions", 0),
                "time_remaining": snap.get("time_remaining"),
                "reset_version": self._reset_version,
            }

            with self._snapshot_lock:
                self.snapshot.update(snapshot)

            return snapshot

        except Exception as e:
            logging.error(f"sync_snapshot_from_quiz failed: {e}")
            return None

    def _reset_leaderboard_atomically(self):
        with self._reset_lock:
            now = time.time()
            if now - self._last_reset_time < 1.0:
                logger.warning("⏸️ Ignoring rapid reset request")
                return self._reset_version

            self._last_reset_time = now
            self._reset_version += 1
            new_version = self._reset_version

            logger.info(f"🔄 LEADERBOARD RESET v{new_version}")

            with self._snapshot_lock:
                self.snapshot['leaderboard'] = []
                self.snapshot['reset_version'] = new_version

            if hasattr(self.quiz_manager, 'leaderboard_manager') and self.quiz_manager.leaderboard_manager:
                try:
                    lb_mgr = self.quiz_manager.leaderboard_manager
                    if hasattr(lb_mgr, 'reset_leaderboard'):
                        lb_mgr.reset_leaderboard()
                    if hasattr(lb_mgr, 'user_stats'):
                        lb_mgr.user_stats.clear()
                except Exception as e:
                    logger.warning(f"LeaderboardManager reset warning: {e}")

            self.emit_signal_ws('leaderboard_reset_requested', new_version)
            self.emit_signal_ws('leaderboard_updated', {
                'version': new_version,
                'entries': [],
                'timestamp': int(time.time())
            })

            return new_version

    @staticmethod
    def _diagnostic_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def get_room_client_count(self, room: str) -> int:
        """Return Socket.IO membership without making action execution depend on it."""
        try:
            participants = self.socketio.manager.get_participants('/', room)
            return sum(1 for _ in participants)
        except Exception as exc:
            logger.warning(
                "public_widget_diagnostic timestamp=%s stage=client_count_failed room=%s error=%r",
                self._diagnostic_timestamp(), room, exc,
            )
            return -1

    def _account_room_for_socket(self, sid: str, public_widget_id: str) -> str:
        """Room for a widget that identifies itself by account, with no session.

        The connect handler already resolved (and validated) this socket's room
        from the request path and the signed-in identity, so reuse it. Falling
        back to the account room keeps hosted widgets out of the internal
        default room.
        """
        existing = self.socket_diagnostic_info(sid).get('room')
        if existing:
            return str(existing)
        return f'profile:{public_widget_id}' if public_widget_id else self.LOCKED_ROOM

    def update_socket_diagnostics(self, sid: str, **fields) -> Dict[str, Any]:
        with self._clients_lock:
            info = self.connected_clients.setdefault(sid, {'connected_at': time.time(), 'type': 'unknown'})
            info.update({key: value for key, value in fields.items() if value is not None})
            return dict(info)

    def socket_diagnostic_info(self, sid: str) -> Dict[str, Any]:
        with self._clients_lock:
            return dict(self.connected_clients.get(sid, {}))

    def _setup_socketio_handlers(self):
        @self.socketio.event
        async def connect(sid, environ, auth=None) -> bool:
            with self._clients_lock:
                if len(self.connected_clients) >= self.MAX_CONNECTED_CLIENTS:
                    logger.warning(f"Max clients reached, rejecting {sid}")
                    return False

            query_string = environ.get('QUERY_STRING', '')
            query = parse_qs(query_string)
            widget_type = str((query.get('widget_type') or [''])[0])
            widget_session_id = str((query.get('session_id') or [''])[0])
            is_scoped_widget = widget_type in {'quiz', 'chess'} and bool(widget_session_id)
            is_chess_client = widget_type == 'chess' or 'room=chess_game_room' in query_string
            asgi_scope = environ.get('asgi.scope') or {}
            headers = dict(asgi_scope.get('headers') or [])
            host = headers.get(b'host', b'').decode('utf-8', errors='ignore')
            user_agent = headers.get(b'user-agent', b'').decode('utf-8', errors='ignore')
            socket_path = str(asgi_scope.get('path') or environ.get('PATH_INFO') or '/socket.io')
            referer = headers.get(b'referer', b'').decode('utf-8', errors='ignore')
            path = (
                _first_string(auth if isinstance(auth, dict) else {}, 'path', 'widget_path', 'page_path', 'url', 'href')
                or _first_string(query, 'path', 'widget_path', 'page_path', 'url', 'href')
                or referer
                or socket_path
            )
            transport = str((query.get('transport') or ['unknown'])[0])
            live_session_id = str((query.get('live_session_id') or [''])[0])
            from core.services.identity_resolver import resolve_identity

            try:
                active_runtime_id = resolve_identity().active_runtime_id
            except Exception as exc:
                active_runtime_id = None
                logger.info("public_widget_identity_lookup_failed path=%s error=%s", path, exc)

            decision = _public_widget_socket_decision(
                path=path,
                query=query,
                auth=auth,
                active_runtime_id=active_runtime_id,
                host=host,
            )
            public_widget_id = decision['resolved_public_widget_id']
            join_room = decision['join_room']
            decision_reason = decision['reason']

            logger.info(f"🔌 Client connected: {sid} (chess: {is_chess_client})")
            logger.info(
                "public_widget_connection timestamp=%s event=connected host=%s path=%s transport=%s socket_id=%s public_widget_id=%s live_session_id=%s room_joined=%s room_join_acknowledged=%s reconnect_attempt=%s reconnect_success=%s rejoined_room=%s user_agent=%s",
                self._diagnostic_timestamp(), host, path, transport, sid, public_widget_id or None,
                live_session_id or None, None, False, None, None, None, user_agent,
            )

            with self._client_heartbeat_lock:
                self._client_last_seen[sid] = time.time()

            if decision_reason == 'missing_public_widget_id_rejected':
                logger.warning(
                    "public_widget_missing_profile_id path=%s query=%s active_runtime_id=%s",
                    path, query_string or None, active_runtime_id,
                )
            elif decision_reason == 'public_widget_id_mismatch_rejected':
                logger.warning(
                    "public_widget_id_mismatch path=%s query=%s requested_public_widget_id=%s active_runtime_id=%s",
                    path, query_string or None, decision['requested_public_widget_id'], active_runtime_id,
                )
            elif join_room:
                await self.socketio.enter_room(sid, join_room)
                logger.info("✅ Added %s to socket room (%s)", sid, join_room)

            decision_logger = logger.warning if join_room is None else logger.info
            decision_logger(
                "public_widget_connection_decision path=%s query=%s "
                "resolved_public_widget_id=%s active_runtime_id=%s join_room=%s reason=%s",
                path,
                query_string or None,
                public_widget_id,
                active_runtime_id,
                join_room,
                decision_reason,
            )

            with self._clients_lock:
                self.connected_clients[sid] = {
                    'connected_at': time.time(),
                    'type': 'chess' if is_chess_client else 'quiz',
                    'host': host,
                    'path': path,
                    'transport': transport,
                    'public_widget_id': public_widget_id or None,
                    'live_session_id': live_session_id or widget_session_id or None,
                    'widget_type': widget_type or None,
                    'room': join_room,
                    'room_join_acknowledged': bool(join_room),
                }

            await self.socketio.emit("client_registered", {
                "sid": sid,
                "room": join_room,
                "locked": bool(join_room),
                "acknowledged": bool(join_room),
                "error": None if join_room else decision_reason,
            }, to=sid)

            return True

        @self.app.middleware("http")
        async def allow_iframe(request, call_next):
            host = (request.url.hostname or "").lower()
            path = request.url.path
            is_public_widget_host = host == urlparse(HOSTED_WIDGETS_BASE_URL).hostname
            is_unscoped = not path.startswith("/u/")
            is_control_page = path in {"/quiz_controls", "/chess/controls"}
            is_quiz_command = request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/api/quiz/")
            is_chess_command = request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/chess/api/")
            if is_public_widget_host and is_unscoped and (is_control_page or is_quiz_command or is_chess_command):
                from fastapi.responses import JSONResponse
                logger.warning("unscoped_public_control_rejected host=%s path=%s method=%s", host, path, request.method)
                return JSONResponse({"detail": "Account-scoped session authorization required"}, status_code=403)
            try:
                response = await call_next(request)
            except ConnectionResetError as e:
                logger.info(
                    "[DIAGNOSTIC] Expected client disconnect (ConnectionResetError) path=%s method=%s client=%s error=%s",
                    request.url.path,
                    request.method,
                    request.client,
                    e,
                )
                from fastapi import Response
                return Response(status_code=499)
            if response.status_code in {403, 429} or response.headers.get("cf-mitigated"):
                logger.warning(
                    "public_widget_http timestamp=%s host=%s path=%s method=%s status=%s cf_ray=%s cf_mitigated=%s cf_connecting_ip=%s user_agent=%s",
                    self._diagnostic_timestamp(), request.url.hostname, request.url.path, request.method,
                    response.status_code, request.headers.get("cf-ray") or response.headers.get("cf-ray"),
                    response.headers.get("cf-mitigated"), request.headers.get("cf-connecting-ip"), request.headers.get("user-agent"),
                )
            # # Fix: Restrict framing to configured trusted origins only.
            allowed = " ".join(self.trusted_origins)
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {allowed}"
            return response

        @self.socketio.event
        async def disconnect(sid, reason=None):
            disconnect_reason = reason or ("server_shutdown" if self._is_shutting_down else "client_or_transport_disconnect")
            info = self.socket_diagnostic_info(sid)
            logger.info(
                "public_widget_connection timestamp=%s event=disconnected host=%s path=%s transport=%s socket_id=%s public_widget_id=%s live_session_id=%s room_joined=%s room_join_acknowledged=%s disconnect_reason=%s reconnect_attempt=%s reconnect_success=%s rejoined_room=%s",
                self._diagnostic_timestamp(), info.get('host'), info.get('widget_path') or info.get('path'),
                info.get('transport'), sid, info.get('public_widget_id'), info.get('live_session_id'),
                info.get('room'), info.get('room_join_acknowledged', False), disconnect_reason,
                info.get('reconnect_attempt'), info.get('reconnect_success'), info.get('rejoined_room'),
            )
            with self._clients_lock:
                if sid in self.connected_clients:
                    self.connected_clients.pop(sid)
                    logger.info(f"❌ Client disconnected: {sid} ({disconnect_reason})")

            with self._client_heartbeat_lock:
                self._client_last_seen.pop(sid, None)

        @self.socketio.on("heartbeat")
        async def heartbeat(sid, data):
            logger.debug("[DIAGNOSTIC] heartbeat sid=%s payload=%s", sid, data)
            with self._client_heartbeat_lock:
                self._client_last_seen[sid] = time.time()

            await self.socketio.emit("heartbeat_ack", {"t": int(time.time() * 1000)}, to=sid)

        @self.socketio.on("join_room")
        async def join_room(sid, data):
            payload = dict(data or {})
            widget_type = str(payload.get('widget_type') or '')
            session_id = str(payload.get('session_id') or '')
            public_widget_id = str(payload.get('public_widget_id') or '')

            control_authorized = False
            if widget_type in {'quiz', 'chess'}:
                # An account's widgets are keyed by public_widget_id alone -- the
                # same credential the connect handler already validated, and the
                # only one the published browser-source URLs carry. A session id
                # is optional extra isolation; requiring one rejected every real
                # dock and overlay with "unauthorized" and left them roomless.
                try:
                    from core.server.widget_sessions import WidgetSessionStore
                    if session_id:
                        store = WidgetSessionStore.get_instance()
                        session = store.resolve_public(widget_type, public_widget_id, session_id)
                        control_token = str(payload.get('control_token') or '')
                        if control_token:
                            store.authorize_control(widget_type, session_id, control_token)
                            control_authorized = True
                        room = session.room
                    else:
                        room = self._account_room_for_socket(sid, public_widget_id)
                except Exception as exc:
                    logger.warning(
                        "widget_subscription_denied socket_id=%s widget_type=%s session_id=%s error=%s",
                        sid, widget_type, session_id, exc,
                    )
                    await self.socketio.emit(
                        "room_joined",
                        {"acknowledged": False, "error": str(exc)},
                        to=sid,
                    )
                    return
            else:
                # Preserve unrelated existing widgets while refusing arbitrary
                # client-selected rooms for Quiz and Chess.
                room = payload.get('room_id', self.LOCKED_ROOM)

            await self.socketio.enter_room(sid, room)
            self.update_socket_diagnostics(
                sid, room=room, room_join_acknowledged=True,
                public_widget_id=public_widget_id or None, live_session_id=session_id or None,
                widget_type=widget_type or None, control_authorized=control_authorized,
            )
            await self.socketio.emit("room_joined", {"room_id": room, "acknowledged": True}, to=sid)
            info = self.socket_diagnostic_info(sid)
            logger.info(
                "public_widget_connection timestamp=%s event=room_join_acknowledged host=%s path=%s transport=%s socket_id=%s public_widget_id=%s live_session_id=%s room_joined=%s room_join_acknowledged=true rejoined_room=%s",
                self._diagnostic_timestamp(), info.get('host'), info.get('widget_path') or info.get('path'),
                info.get('transport'), sid, info.get('public_widget_id'), info.get('live_session_id'), room,
                info.get('rejoined_room'),
            )

        @self.socketio.on("public_widget:diagnostic")
        async def public_widget_diagnostic(sid, data):
            payload = dict(data or {})
            info = self.update_socket_diagnostics(
                sid,
                widget_path=payload.get('path'),
                public_widget_id=payload.get('public_widget_id'),
                live_session_id=payload.get('live_session_id'),
                reconnect_attempt=payload.get('reconnect_attempt'),
                reconnect_success=payload.get('reconnect_success'),
                rejoined_room=payload.get('rejoined_room'),
            )
            logger.info(
                "public_widget_connection timestamp=%s event=%s host=%s path=%s transport=%s socket_id=%s public_widget_id=%s live_session_id=%s room_joined=%s room_join_acknowledged=%s disconnect_reason=%s reconnect_attempt=%s reconnect_success=%s rejoined_room=%s details=%s",
                self._diagnostic_timestamp(), payload.get('event'), info.get('host'), info.get('widget_path') or info.get('path'),
                info.get('transport'), sid, info.get('public_widget_id'), info.get('live_session_id'), info.get('room'),
                info.get('room_join_acknowledged', False), payload.get('disconnect_reason'), info.get('reconnect_attempt'),
                info.get('reconnect_success'), info.get('rejoined_room'), payload.get('details'),
            )

        @self.socketio.on("request_snapshot")
        async def request_snapshot(sid, data):
            info = self.socket_diagnostic_info(sid)
            widget_type = info.get("widget_type")
            session_id = info.get("live_session_id")
            public_widget_id = info.get("public_widget_id")
            if widget_type in {"quiz", "chess"} and session_id and public_widget_id:
                try:
                    from core.server.widget_sessions import WidgetSessionStore
                    session = WidgetSessionStore.get_instance().resolve_public(
                        widget_type, public_widget_id, session_id
                    )
                    await self.socketio.emit(
                        "snapshot",
                        {"snapshot": session.snapshot, "version": session.version, "session_id": session.session_id},
                        to=sid,
                    )
                    return
                except Exception as exc:
                    logger.warning("scoped_snapshot_denied socket_id=%s error=%s", sid, exc)
                    await self.socketio.emit("snapshot", {"error": "unauthorized"}, to=sid)
                    return
            snapshot = self._get_snapshot_copy()
            await self.socketio.emit("snapshot", {"snapshot": snapshot}, to=sid)

        @self.socketio.on("soft_cleanup_ack")
        async def soft_cleanup_ack(sid, data):
            logger.info(f"✅ Frontend confirmed soft cleanup: {data}")

        @self.socketio.on("request_events_since")
        async def request_events_since(sid, data):
            since_seq = data.get('since_seq', 0)
            events = self._get_events_since(since_seq)
            last_seq = self._event_seq
            await self.socketio.emit("replay", {
                "events": events,
                "last_seq": last_seq
            }, to=sid)

    def get_connected_clients_info(self):
        with self._clients_lock:
            return {'count': len(self.connected_clients)}

    def force_cleanup(self):
        logger.info("Force cleanup requested")
        self.notify_frontend_cleanup()

        with self._event_seq_lock:
            old_size = len(self._event_store)
            self._event_store = deque(
                list(self._event_store)[-100:],
                maxlen=self.MAX_EVENT_STORE
            )
            self._event_seq_index = deque(
                (int(e.get('seq', 0)) for e in self._event_store),
                maxlen=self.MAX_EVENT_STORE
            )
            logger.info(f"Cleaned {old_size - len(self._event_store)} events")

        with self._clients_lock:
            now = time.time()
            old_count = len(self.connected_clients)
            self.connected_clients = {
                sid: info for sid, info in self.connected_clients.items()
                if (now - info.get('connected_at', now)) < 3600
            }
            logger.info(f"Cleaned {old_count - len(self.connected_clients)} stale client records")

    def start(self):
        import threading, asyncio, sys, io
        import uvicorn

        if self.running:
            logger.info("Bridge already running")
            return None

        self.running = True
        self._startup_exception = None
        self._ready_event.clear()
        logger.info("🚀 Starting HTTP Bridge...")
        self._start_client_monitor()

        def run_server():
            try:
                sys.stderr = sys.stderr or io.StringIO()
                sys.stdout = sys.stdout or io.StringIO()

                if sys.platform.startswith("win"):
                    try:
                        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                    except Exception:
                        pass

                config = uvicorn.Config(
                    self.asgi_app,
                    host="127.0.0.1",
                    port=self.port,
                    log_config=None,
                    log_level="warning",
                    lifespan="on",
                    loop="asyncio",
                    workers=1,
                )

                self._server = uvicorn.Server(config)
                self._server.install_signal_handlers = False
                self._ready_event.set()
                self._server.run()

            except Exception as e:
                self._startup_exception = e
                logger.error(f"Bridge startup failed: {e}")
            finally:
                self.running = False
                self._ready_event.set()

        self.thread = threading.Thread(target=run_server, daemon=True, name="HTTPBridge")
        self.thread.start()

        # The hosted OBS control dock posts its commands to the LiveForge widget
        # host, not to this machine. This bridge is the desktop half of that
        # channel: without it the hosted dock has nothing behind it.
        try:
            from core.services.hosted_bridge import start_hosted_bridge

            start_hosted_bridge(f"http://127.0.0.1:{self.port}")
        except Exception as exc:
            logger.warning("hosted_bridge_start_failed error=%s", exc)

        logger.info("✅ HTTP Bridge started")
        return None

    def stop(self):
        if not self.running:
            return None

        logger.info("🛑 Stopping HTTP Bridge server...")
        self.running = False
        self._is_shutting_down = True

        try:
            from core.services.hosted_bridge import stop_hosted_bridge

            stop_hosted_bridge()
        except Exception as exc:
            logger.debug("hosted_bridge_stop_skipped error=%s", exc)

        async def _graceful_disconnect_clients():
            # Best-effort pre-shutdown notification to make disconnect origin explicit.
            try:
                await self.socketio.emit("server_shutdown", {"reason": "bridge_stopping"}, room=self.LOCKED_ROOM)
            except Exception as e:
                logger.debug(f"server_shutdown emit skipped: {e}")

            with self._clients_lock:
                client_ids = list(self.connected_clients.keys())

            for sid in client_ids:
                try:
                    await self.socketio.disconnect(sid)
                except Exception as e:
                    logger.debug(f"Client disconnect skipped sid={sid}: {e}")

        try:
            loop = getattr(self, "main_loop", None)
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(_graceful_disconnect_clients(), loop)
                fut.result(timeout=2.0)
        except Exception as e:
            logger.debug(f"Graceful client disconnect phase skipped: {e}")

        if hasattr(self, '_server') and self._server:
            try:
                logger.info("Shutting down uvicorn server...")
                self._server.should_exit = True

                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=5.0)
                    if self.thread.is_alive():
                        logger.warning("Bridge thread did not stop within timeout")
                    else:
                        logger.info("✅ Bridge thread stopped")
            except Exception as e:
                logger.error(f"Error stopping server: {e}")

        return None

    def cleanup(self):
        logger.info("🧹 Cleaning up HTTP Bridge...")
        self.stop()
        try:
            with self._event_seq_lock:
                self._event_store.clear()
                self._event_seq_index.clear()
        except Exception as e:
            logger.warning(f"Error clearing event store: {e}")

        try:
            with self._emit_loop_lock:
                if self._emit_loop and self._emit_loop.is_running():
                    async def _cancel_emit_tasks():
                        current = asyncio.current_task()
                        tasks = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
                        for task in tasks:
                            task.cancel()
                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)

                    fut = asyncio.run_coroutine_threadsafe(_cancel_emit_tasks(), self._emit_loop)
                    try:
                        fut.result(timeout=1.5)
                    except Exception:
                        pass
                    self._emit_loop.call_soon_threadsafe(self._emit_loop.stop)
                self._emit_loop = None
            if self._emit_loop_thread and self._emit_loop_thread.is_alive():
                self._emit_loop_thread.join(timeout=2.0)
                logger.info("Bridge emit loop thread stopped")
            self._emit_loop_thread = None
        except Exception as e:
            logger.warning(f"Error stopping emit loop: {e}")

        logger.info("✅ HTTP Bridge cleanup complete (connections preserved)")
        return None
