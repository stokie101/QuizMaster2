"""Global lifecycle state for application shutdown coordination."""

import threading

_lock = threading.Lock()
app_is_shutting_down = False


def begin_shutdown() -> None:
    """Mark the application as shutting down."""
    global app_is_shutting_down
    with _lock:
        app_is_shutting_down = True


def is_shutting_down() -> bool:
    with _lock:
        return app_is_shutting_down
