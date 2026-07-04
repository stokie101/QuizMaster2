"""Loopback callback transport for website-to-desktop authentication."""

from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urlparse

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/auth/callback"
APP_SLUG = "quizmaster"


def build_desktop_login_url(login_page_url: str, redirect_uri: str, state: str) -> str:
    """Build the website login URL bound to one loopback callback attempt."""
    query = urlencode(
        {
            "desktop": "1",
            "app": APP_SLUG,
            "app_slug": APP_SLUG,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{login_page_url}?{query}"


def callback_state_matches(expected_state: Optional[str], received_state: str) -> bool:
    """Compare OAuth-style state values without leaking comparison timing."""
    return bool(expected_state) and secrets.compare_digest(received_state or "", expected_state)


class DesktopLoginCallbackServer:
    """Temporary loopback server that receives a website desktop-login code."""

    def __init__(self, callback: Callable[[str, str, str], None]):
        self._callback = callback
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def redirect_uri(self) -> str:
        if self._server is None:
            raise RuntimeError("Desktop login callback server has not been started.")
        return f"http://{CALLBACK_HOST}:{self._server.server_port}{CALLBACK_PATH}"

    def start(self) -> str:
        callback = self._callback

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return

                query = parse_qs(parsed.query)
                code = query.get("code", [""])[0]
                state = query.get("state", [""])[0]
                error = query.get("error_description", query.get("error", [""]))[0]

                body = b"Desktop sign-in received. You may close this tab."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                callback(code, state, error)

            def log_message(self, format: str, *args) -> None:
                return

        self._server = ThreadingHTTPServer((CALLBACK_HOST, 0), CallbackHandler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="QuizMasterDesktopAuthCallback",
            daemon=True,
        )
        self._thread.start()
        return self.redirect_uri

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
