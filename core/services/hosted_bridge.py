"""Outbound bridge from the desktop app to the LiveForge widget host.

The hosted OBS control dock (https://widgets.liveforge.online/u/<pwid>/quiz_controls)
cannot reach this machine: it posts its commands to the widget Worker, which
holds them in a per-account channel, and it reads quiz state from the same
channel. Nothing on the desktop consumed either half, so every hosted Start,
Pause, Skip and Load ended in the dock with nothing behind it.

This bridge is that missing half. While the app is signed in it:

  * holds a WebSocket to ``/api/quiz/commands/stream`` and replays each queued
    command against this app's own local quiz API, exactly as the in-app dock
    would; and
  * publishes quiz state to ``/api/quiz/control-state`` and the display snapshot
    to ``/api/publish/quiz``, so the hosted dock's buttons and the hosted
    overlays reflect what the app is actually doing.

It authenticates with the signed-in account's access token and talks to the
local bridge server over 127.0.0.1, so it needs no privileged access of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Hosted command -> local endpoint. The hosted dock speaks the same vocabulary as
# the in-app dock, so this is a direct map with two exceptions: the recovery
# commands collapse onto the local stop endpoint, which is the closest thing the
# desktop runtime exposes.
COMMAND_ENDPOINTS: Dict[str, str] = {
    "start": "/api/quiz/start",
    "pause": "/api/quiz/pause",
    "resume": "/api/quiz/resume",
    "stop": "/api/quiz/stop",
    "skip": "/api/quiz/skip",
    "load": "/api/quiz/load",
    "force_refresh_display": "/api/quiz/force_refresh_display",
    "save_config": "/api/config/save",
    "reset_quiz_runtime": "/api/quiz/stop",
    "reset": "/api/quiz/stop",
    "unload": "/api/quiz/stop",
    "clear_quiz": "/api/quiz/stop",
}

# The hosted display renders from live signals, not from polled state, so every
# signal the desktop emits locally has to be mirrored to the hosted channel as
# {event, args} -- the shape public/w/quiz_display/hosted-quiz-display-env.js
# delivers to the real display controller.
#
# timer_tick is deliberately not mirrored: the hosted overlay anchors its
# countdown to the deadline carried by timer_started and runs its own clock, so
# forwarding ticks would be a request per second per viewer for nothing.
SIGNAL_QUEUE_MAX = 200
UNMIRRORED_SIGNALS = frozenset({"timer_tick", "heartbeat", "heartbeat_ack"})

STATE_POLL_SECONDS = 2.0
RECONNECT_MIN_SECONDS = 3.0
RECONNECT_MAX_SECONDS = 60.0
LOCAL_TIMEOUT_SECONDS = 30.0
PUBLISH_TIMEOUT_SECONDS = 15.0


class HostedQuizBridge:
    """Connects this app's quiz runtime to its hosted control dock."""

    def __init__(self, local_base_url: str, widget_type: str = "quiz") -> None:
        self._local_base_url = local_base_url.rstrip("/")
        self._widget_type = widget_type
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopping = threading.Event()
        self._handled_versions: set[str] = set()
        self._last_published: Optional[str] = None
        self._last_snapshot: Optional[str] = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._signals: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=SIGNAL_QUEUE_MAX)

    @property
    def connected(self) -> bool:
        """Is the command socket attached right now?"""
        return self._connected

    def status(self) -> Dict[str, Any]:
        return {
            "widget_type": self._widget_type,
            "connected": self._connected,
            "error": self._last_error,
        }

    # -- live signal mirror --------------------------------------------------

    def publish_signal(self, signal_name: str, args: Any = None) -> None:
        """Queue one live signal for the hosted display.

        Called from the quiz runtime's own threads, so it must never block or
        raise: a full queue drops the oldest signal rather than stalling the
        quiz behind a network request.
        """
        name = str(signal_name or "").strip()
        if not name or name in UNMIRRORED_SIGNALS:
            return
        payload = {"event": name, "args": list(args) if args else []}
        try:
            self._signals.put_nowait(payload)
        except queue.Full:
            try:
                self._signals.get_nowait()
                self._signals.put_nowait(payload)
            except queue.Empty:
                pass
            except queue.Full:
                pass

    async def _signal_loop(self) -> None:
        while not self._stopping.is_set():
            payload = await asyncio.to_thread(self._next_signal)
            if payload is None:
                continue
            access_token, public_widget_id = self._credentials()
            if not access_token or not public_widget_id:
                continue
            await asyncio.to_thread(self._publish_signal_blocking, access_token, public_widget_id, payload)

    def _next_signal(self) -> Optional[Dict[str, Any]]:
        try:
            return self._signals.get(timeout=0.5)
        except queue.Empty:
            return None

    @staticmethod
    def _stamp_timer_reading(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp a timer payload with the clock reading it is compared against.

        The overlay derives remaining time from the deadline and this reading,
        so it has to be taken as the payload is SENT. Taken when the signal was
        emitted, time spent queued here counts as time still left to run, and
        the hosted countdown finishes that much after the answer is revealed.
        """
        args = payload.get("args") or []
        first = args[0] if args and isinstance(args[0], dict) else None
        if not first or "deadline_unix_ms" not in first:
            return payload
        now_ms = int(time.time() * 1000)
        stamped = dict(first)
        stamped["server_now_unix_ms"] = now_ms
        remaining_ms = max(0, int(stamped["deadline_unix_ms"]) - now_ms)
        stamped["remaining_ms"] = remaining_ms
        stamped["remaining"] = remaining_ms / 1000.0
        out = dict(payload)
        out["args"] = [stamped] + list(args[1:])
        return out

    def _publish_signal_blocking(self, access_token: str, public_widget_id: str, payload: Dict[str, Any]) -> None:
        import requests

        payload = self._stamp_timer_reading(payload)
        try:
            requests.post(
                f"{self._hosted_base_url()}/api/publish/{self._widget_type}"
                f"?public_widget_id={quote(public_widget_id, safe='')}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "snapshot": payload,
                    "event_type": payload.get("event"),
                    "public_widget_id": public_widget_id,
                },
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.debug(
                "hosted_bridge_signal_publish_failed widget=%s event=%s error=%s",
                self._widget_type, payload.get("event"), exc,
            )

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="HostedQuizBridge")
        self._thread.start()
        logger.info("hosted_bridge_started widget=%s", self._widget_type)

    def stop(self) -> None:
        self._stopping.set()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    # -- thread body --------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(
                asyncio.gather(self._command_loop(), self._state_loop(), self._signal_loop())
            )
        except Exception as exc:
            logger.warning("hosted_bridge_stopped widget=%s error=%s", self._widget_type, exc)
        finally:
            try:
                if self._loop:
                    self._loop.close()
            except Exception:
                pass

    # -- credentials --------------------------------------------------------

    @staticmethod
    def _credentials() -> tuple[Optional[str], Optional[str]]:
        """Return (access_token, public_widget_id) for the signed-in account."""
        try:
            from core.services.auth_service import AuthService

            auth = AuthService.get_instance()
            session = getattr(auth, "current_session", None)
            profile = getattr(auth, "current_profile", None)
            access_token = str(getattr(session, "access_token", "") or "").strip() or None
            public_widget_id = str(getattr(profile, "public_widget_id", "") or "").strip() or None
            return access_token, public_widget_id
        except Exception:
            return None, None

    @staticmethod
    def _hosted_base_url() -> str:
        from core.server.url_config import HOSTED_WIDGETS_BASE_URL

        return HOSTED_WIDGETS_BASE_URL.rstrip("/")

    # -- command channel ----------------------------------------------------

    async def _command_loop(self) -> None:
        backoff = RECONNECT_MIN_SECONDS
        while not self._stopping.is_set():
            access_token, public_widget_id = self._credentials()
            if not access_token or not public_widget_id:
                self._connected = False
                self._last_error = "not_signed_in"
                await asyncio.sleep(RECONNECT_MIN_SECONDS)
                continue
            try:
                await self._consume_commands(access_token, public_widget_id)
                backoff = RECONNECT_MIN_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "hosted_bridge_command_stream_retry widget=%s backoff=%.0fs error=%s",
                    self._widget_type, backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    @staticmethod
    def _connect(url: str, access_token: str):
        """Open the command socket across both websockets client APIs.

        The auth header keyword was renamed in websockets 14 (extra_headers ->
        additional_headers). Passing the wrong one is a TypeError on every
        attempt, which the reconnect loop swallows as just another failure: the
        bridge never attaches, every hosted command queues into nothing, and the
        dock's buttons do nothing at all. Try the current name, fall back to the
        legacy one.
        """
        import websockets

        headers = {"Authorization": f"Bearer {access_token}"}
        options = {"ping_interval": 20, "ping_timeout": 20, "max_queue": 32}
        try:
            return websockets.connect(url, additional_headers=headers, **options)
        except TypeError:
            return websockets.connect(url, extra_headers=headers, **options)

    async def _consume_commands(self, access_token: str, public_widget_id: str) -> None:
        base = self._hosted_base_url()
        ws_url = base.replace("https://", "wss://").replace("http://", "ws://")
        # Name the channel explicitly. An account can hold a LiveForge and a
        # QuizMaster widget id at once, and without this the host picks one by
        # entitlement order -- attaching this app to one channel while its dock
        # commands the other, which looks exactly like a dock with no app behind
        # it.
        url = (
            f"{ws_url}/api/{self._widget_type}/commands/stream"
            f"?public_widget_id={quote(public_widget_id, safe='')}"
        )

        async with self._connect(url, access_token) as socket:
            self._connected = True
            logger.info("hosted_bridge_command_stream_open widget=%s", self._widget_type)
            try:
                while not self._stopping.is_set():
                    raw = await socket.recv()
                    await self._handle_message(raw)
            finally:
                self._connected = False

    async def _handle_message(self, raw: Any) -> None:
        if not isinstance(raw, str):
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        kind = str(payload.get("type") or "")
        if kind in {"ready", "pong", "viewer_census", "widget_event"}:
            return
        command = str(payload.get("command") or "")
        if not command:
            return

        # The hosted channel replays its queue on every (re)connect, so an
        # already-executed command must never run twice.
        version = str(payload.get("id") or payload.get("version") or "")
        if version and version in self._handled_versions:
            return
        if version:
            self._handled_versions.add(version)
            if len(self._handled_versions) > 200:
                self._handled_versions = set(list(self._handled_versions)[-100:])

        await asyncio.to_thread(self._execute_locally, command, payload.get("args") or {})
        await self._publish_state(force=True)

    def _execute_locally(self, command: str, args: Any) -> None:
        import requests

        endpoint = COMMAND_ENDPOINTS.get(command)
        if not endpoint:
            logger.warning("hosted_bridge_unknown_command widget=%s command=%s", self._widget_type, command)
            return

        body: Dict[str, Any] = {}
        if isinstance(args, dict):
            body = dict(args)
        if command == "save_config" and "settings" not in body:
            body = {"settings": body}

        try:
            response = requests.post(
                f"{self._local_base_url}{endpoint}",
                json=body,
                timeout=LOCAL_TIMEOUT_SECONDS,
            )
            logger.info(
                "hosted_bridge_command_executed widget=%s command=%s status=%s",
                self._widget_type, command, response.status_code,
            )
        except Exception as exc:
            logger.warning(
                "hosted_bridge_command_failed widget=%s command=%s error=%s",
                self._widget_type, command, exc,
            )

    # -- state channel ------------------------------------------------------

    async def _state_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._publish_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("hosted_bridge_state_publish_failed widget=%s error=%s", self._widget_type, exc)
            await asyncio.sleep(STATE_POLL_SECONDS)

    async def _publish_state(self, force: bool = False) -> None:
        access_token, public_widget_id = self._credentials()
        if not access_token or not public_widget_id:
            return
        await asyncio.to_thread(self._publish_state_blocking, access_token, public_widget_id, force)

    def _publish_state_blocking(self, access_token: str, public_widget_id: str, force: bool) -> None:
        import requests

        try:
            full = requests.get(f"{self._local_base_url}/api/state/full", timeout=LOCAL_TIMEOUT_SECONDS).json()
        except Exception as exc:
            logger.debug("hosted_bridge_local_state_unavailable error=%s", exc)
            return
        if not isinstance(full, dict) or not full.get("success"):
            return

        state = full.get("state") or {}
        payload = {
            "public_widget_id": public_widget_id,
            "state": {
                "state": state.get("state"),
                "quiz_state": state.get("state"),
                "quiz_loaded": bool(state.get("quiz_loaded")),
                "paused": bool(state.get("paused")),
                "questionCount": int(state.get("total_questions") or 0),
                "questionIndex": int(state.get("question_index") or 0),
                "running": str(state.get("state") or "").upper() == "RUNNING",
                "answer_visible": bool(state.get("answer_visible")),
                "waiting_for_manual_advance": bool(state.get("waiting_for_manual_advance")),
                "config": full.get("config") or {},
                "state_version": state.get("reset_version"),
            },
        }

        fingerprint = json.dumps(payload["state"], sort_keys=True, default=str)
        if not force and fingerprint == self._last_published:
            self._publish_snapshot(access_token, public_widget_id)
            return

        base = self._hosted_base_url()
        try:
            response = requests.post(
                f"{base}/api/{self._widget_type}/control-state",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                self._last_published = fingerprint
            else:
                logger.info(
                    "hosted_bridge_state_refused widget=%s status=%s",
                    self._widget_type, response.status_code,
                )
        except Exception as exc:
            logger.debug("hosted_bridge_state_post_failed widget=%s error=%s", self._widget_type, exc)

        self._publish_snapshot(access_token, public_widget_id)

    def _publish_snapshot(self, access_token: str, public_widget_id: str) -> None:
        """Push the display snapshot so hosted overlays render the live quiz."""
        import requests

        try:
            body = requests.get(f"{self._local_base_url}/api/snapshot", timeout=LOCAL_TIMEOUT_SECONDS).json()
        except Exception:
            return
        snapshot = body.get("snapshot") if isinstance(body, dict) else None
        if snapshot is None:
            return

        fingerprint = json.dumps(snapshot, sort_keys=True, default=str)
        if fingerprint == self._last_snapshot:
            return

        try:
            response = requests.post(
                f"{self._hosted_base_url()}/api/publish/{self._widget_type}"
                f"?public_widget_id={quote(public_widget_id, safe='')}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"snapshot": snapshot, "public_widget_id": public_widget_id},
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
            if response.status_code < 300:
                self._last_snapshot = fingerprint
        except Exception as exc:
            logger.debug("hosted_bridge_snapshot_failed widget=%s error=%s", self._widget_type, exc)


_bridge_lock = threading.Lock()
_bridge: Optional[HostedQuizBridge] = None


def publish_hosted_signal(signal_name: str, args: Any = None) -> None:
    """Mirror one live signal to the hosted display, if the bridge is running."""
    with _bridge_lock:
        bridge = _bridge
    if bridge is not None:
        bridge.publish_signal(signal_name, args)


def hosted_bridge_status() -> Dict[str, Any]:
    """Report whether the hosted control dock has this app behind it."""
    with _bridge_lock:
        if _bridge is None:
            return {"widget_type": "quiz", "connected": False, "error": "bridge_not_started"}
        return _bridge.status()


def start_hosted_bridge(local_base_url: str) -> Optional[HostedQuizBridge]:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = HostedQuizBridge(local_base_url)
        _bridge.start()
        return _bridge


def stop_hosted_bridge() -> None:
    global _bridge
    with _bridge_lock:
        if _bridge is not None:
            _bridge.stop()
            _bridge = None
