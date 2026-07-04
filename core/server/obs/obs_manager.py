import asyncio
import base64
import hashlib
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)


class OBSManager:
    _instance = None
    _instance_lock = threading.Lock()

    DEFAULT_CONFIG = {
        "host": "localhost",
        "port": 4455,
        "password": "",
        "enabled": False,
        "autoReconnect": True,
        "sceneTriggers": [],
        "defaultScene": "",
        "returnAfterDelay": 0,
    }

    def __init__(self, server=None):
        self.server = server
        self.config_path = Path("data/obs.json")
        self.lock = threading.RLock()
        self.config: Dict[str, Any] = {}

        self._ws = None
        self.connected = False
        self.scenes: List[str] = []
        self.current_scene = ""
        self.pending_return_task: Optional[asyncio.Task] = None
        self.reconnect_task: Optional[asyncio.Task] = None

        self.load_config()

    @classmethod
    def get_instance(cls, server=None):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(server)
            elif server is not None:
                cls._instance.server = server
        return cls._instance

    def load_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = dict(self.DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                loaded.update(json.loads(self.config_path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.error(f"Failed loading OBS config: {exc}")
        self.config = loaded
        if not self.config_path.exists():
            self.save_config()

    def save_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def _masked_password(self, raw: str) -> str:
        if not raw:
            return ""
        return f"{raw[:4]}..."

    def get_config(self):
        with self.lock:
            cfg = dict(self.config)
            cfg["password"] = self._masked_password(str(self.config.get("password", "") or ""))
            cfg["connected"] = self.connected
            return cfg

    def update_config(self, data: Dict[str, Any]):
        allowed_keys = {
            "host", "port", "password", "enabled", "autoReconnect",
            "sceneTriggers", "defaultScene", "returnAfterDelay"
        }
        with self.lock:
            for key, value in (data or {}).items():
                if key in allowed_keys:
                    self.config[key] = value
            self.save_config()
        return self.get_config()

    def _emit(self, event: str, payload: Dict[str, Any]):
        try:
            if self.server:
                self.server.emit_to_room(event, payload, "default")
        except Exception as exc:
            logger.debug(f"OBS emit failed {event}: {exc}")

    @staticmethod
    def _make_auth(password: str, salt: str, challenge: str) -> str:
        secret_bytes = hashlib.sha256((password + salt).encode("utf-8")).digest()
        secret_b64 = base64.b64encode(secret_bytes).decode()
        auth_bytes = hashlib.sha256((secret_b64 + challenge).encode("utf-8")).digest()
        return base64.b64encode(auth_bytes).decode()

    async def _send_request(self, ws, request_type: str, request_data: Optional[Dict[str, Any]] = None):
        req_id = str(uuid.uuid4())[:8]
        payload: Dict[str, Any] = {
            "op": 6,
            "d": {
                "requestType": request_type,
                "requestId": req_id,
            },
        }
        if request_data:
            payload["d"]["requestData"] = request_data

        await ws.send(json.dumps(payload))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("op") == 7:
                d = msg.get("d", {})
                if d.get("requestId") == req_id:
                    if not d.get("requestStatus", {}).get("result"):
                        code = d.get("requestStatus", {}).get("code")
                        comment = d.get("requestStatus", {}).get("comment", "")
                        raise RuntimeError(f"OBS request failed: {code} {comment}")
                    return d.get("responseData", {})

    async def _connect_ws(self):
        with self.lock:
            host = str(self.config.get("host") or "localhost")
            port = int(self.config.get("port") or 4455)

        ws = await websockets.connect(
            f"ws://{host}:{port}",
            open_timeout=5,
            close_timeout=3,
        )

        hello_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        hello = json.loads(hello_raw)
        if hello.get("op") != 0:
            await ws.close()
            raise RuntimeError("Invalid OBS hello packet")

        d = hello.get("d", {})
        identify_data: Dict[str, Any] = {
            "rpcVersion": 1,
            "eventSubscriptions": 0,
        }

        auth_info = d.get("authentication")
        if auth_info:
            with self.lock:
                password = str(self.config.get("password", "") or "")
            if not password:
                await ws.close()
                raise ValueError("OBS requires a password but none is set")
            identify_data["authentication"] = self._make_auth(
                password,
                auth_info["salt"],
                auth_info["challenge"],
            )

        await ws.send(json.dumps({"op": 1, "d": identify_data}))

        identified_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        identified = json.loads(identified_raw)
        if identified.get("op") != 2:
            await ws.close()
            raise RuntimeError("OBS did not confirm connection")

        return ws

    async def connect(self):
        if self.connected and self._ws:
            return True
        try:
            self._ws = await self._connect_ws()
            self.connected = True

            scenes_data = await self._send_request(self._ws, "GetSceneList")
            self.scenes = [s["sceneName"] for s in scenes_data.get("scenes", []) if s.get("sceneName")]

            current_data = await self._send_request(self._ws, "GetCurrentProgramScene")
            self.current_scene = current_data.get("currentProgramSceneName", "")

            if self.server:
                self.server.emit_to_room(
                    "obs:connected",
                    {
                        "obsVersion": "connected",
                        "scenes": self.scenes,
                        "currentScene": self.current_scene,
                    },
                    "default",
                )

            logger.info("✅ OBS WebSocket connected")
            return True
        except Exception as e:
            self.connected = False
            self._ws = None
            logger.error(f"OBS connect failed: {e}")
            if self.server:
                self.server.emit_to_room("obs:disconnected", {"error": str(e)}, "default")
            if self.config.get("autoReconnect", True):
                self._ensure_reconnect_task()
            raise

    async def disconnect(self):
        try:
            if self.reconnect_task and not self.reconnect_task.done():
                self.reconnect_task.cancel()
            self.reconnect_task = None

            if self.pending_return_task and not self.pending_return_task.done():
                self.pending_return_task.cancel()

            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
        except Exception as exc:
            logger.debug(f"OBS disconnect warning: {exc}")
        finally:
            self._ws = None
            self.connected = False
            if self.server:
                self.server.emit_to_room("obs:disconnected", {}, "default")

    async def test_connection(self):
        try:
            ws = await self._connect_ws()
            data = await self._send_request(ws, "GetVersion")
            await ws.close()
            return {
                "success": True,
                "obsVersion": data.get("obsVersion", "unknown"),
                "websocketVersion": data.get("obsWebSocketVersion", "unknown"),
            }
        except ConnectionRefusedError:
            return {
                "success": False,
                "error": "Connection refused — is OBS running?",
            }
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Timed out — check host and port",
            }
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_scenes(self):
        try:
            if self._ws and self.connected:
                data = await self._send_request(self._ws, "GetSceneList")
                self.scenes = [s["sceneName"] for s in data.get("scenes", []) if s.get("sceneName")]
            return list(self.scenes)
        except Exception as exc:
            logger.warning(f"Failed to fetch OBS scenes: {exc}")
            return list(self.scenes)

    async def load_current_scene_cache(self):
        try:
            self.current_scene = await self.get_current_scene()
        except Exception:
            pass

    async def get_current_scene(self):
        try:
            if self._ws and self.connected:
                data = await self._send_request(self._ws, "GetCurrentProgramScene")
                self.current_scene = data.get("currentProgramSceneName", "")
            return self.current_scene
        except Exception as exc:
            logger.warning(f"Failed to fetch current OBS scene: {exc}")
            return self.current_scene

    async def switch_scene(self, scene_name: str):
        if not self.connected or not self._ws:
            raise ConnectionError("Not connected to OBS")

        await self._send_request(
            self._ws,
            "SetCurrentProgramScene",
            {"sceneName": scene_name},
        )
        previous = self.current_scene
        self.current_scene = scene_name
        if self.server:
            self.server.emit_to_room(
                "obs:scene_changed",
                {
                    "sceneName": scene_name,
                    "previousScene": previous,
                },
                "default",
            )
        return True

    async def schedule_return(self, scene_name: str, delay_seconds: int):
        if self.pending_return_task and not self.pending_return_task.done():
            self.pending_return_task.cancel()

        async def _return_later():
            try:
                await asyncio.sleep(max(0, int(delay_seconds)))
                if scene_name:
                    await self.switch_scene(scene_name)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"OBS return scheduling failed: {exc}")

        self.pending_return_task = asyncio.create_task(_return_later())

    def is_connected(self) -> bool:
        return self.connected and self._ws is not None

    def _ensure_reconnect_task(self):
        if self.reconnect_task and not self.reconnect_task.done():
            return

        async def _reconnect_loop():
            while self.config.get("autoReconnect", True) and not self.connected:
                await asyncio.sleep(5)
                try:
                    await self.connect()
                    return
                except Exception:
                    continue

        self.reconnect_task = asyncio.create_task(_reconnect_loop())
