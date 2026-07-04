import logging
import threading
import uuid

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from core.services.service_locator import ServiceLocator

logger = logging.getLogger(__name__)


class _QtFileDialogBridge(QObject):
    _open_file_dialog = Signal(str, str)
    _save_file_dialog = Signal(str, str, str, str)

    def __init__(self):
        super().__init__()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._open_file_dialog.connect(self._run_file_dialog, Qt.ConnectionType.QueuedConnection)
        self._save_file_dialog.connect(self._run_save_file_dialog, Qt.ConnectionType.QueuedConnection)

    def select_local_media_file(self, media_type: str, filetype_filter: str) -> dict:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("QApplication is not available")

        request_id = str(uuid.uuid4())
        wait_event = threading.Event()

        with self._pending_lock:
            self._pending[request_id] = {
                "event": wait_event,
                "path": None,
                "cancelled": True,
                "error": None,
                "filetype_filter": filetype_filter,
            }

        self._open_file_dialog.emit(request_id, media_type)

        if not wait_event.wait(timeout=120):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("Timed out while waiting for file dialog response")

        with self._pending_lock:
            result = self._pending.pop(request_id, None)

        if result is None:
            raise RuntimeError("File dialog result is unavailable")
        if result.get("error"):
            raise RuntimeError(result["error"])

        return {"path": result.get("path"), "cancelled": result.get("cancelled", True)}

    def select_save_file_path(self, title: str, default_filename: str, filetype_filter: str) -> dict:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("QApplication is not available")

        request_id = str(uuid.uuid4())
        wait_event = threading.Event()

        with self._pending_lock:
            self._pending[request_id] = {
                "event": wait_event,
                "path": None,
                "cancelled": True,
                "error": None,
                "filetype_filter": filetype_filter,
            }

        self._save_file_dialog.emit(request_id, title or "Save file", default_filename or "quiz_template.csv", filetype_filter)

        if not wait_event.wait(timeout=120):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("Timed out while waiting for save dialog response")

        with self._pending_lock:
            result = self._pending.pop(request_id, None)

        if result is None:
            raise RuntimeError("Save dialog result is unavailable")
        if result.get("error"):
            raise RuntimeError(result["error"])

        return {"path": result.get("path"), "cancelled": result.get("cancelled", True)}

    def _parent_window(self):
        try:
            return ServiceLocator.get_instance().get_service("MainWindow")
        except Exception:
            return QApplication.activeWindow()

    @Slot(str, str)
    def _run_file_dialog(self, request_id: str, media_type: str):
        with self._pending_lock:
            pending = self._pending.get(request_id)

        if pending is None:
            return

        wait_event = pending["event"]

        try:
            selected_path, _ = QFileDialog.getOpenFileName(
                self._parent_window(),
                "Select local media file",
                "",
                pending["filetype_filter"],
            )

            pending["path"] = selected_path or None
            pending["cancelled"] = not bool(selected_path)
        except Exception as exc:
            logger.error("File dialog failed for media type %s: %s", media_type, exc, exc_info=True)
            pending["error"] = str(exc)
            pending["cancelled"] = True
            pending["path"] = None
        finally:
            wait_event.set()

    @Slot(str, str, str, str)
    def _run_save_file_dialog(self, request_id: str, title: str, default_filename: str, filetype_filter: str):
        with self._pending_lock:
            pending = self._pending.get(request_id)

        if pending is None:
            return

        wait_event = pending["event"]

        try:
            selected_path, _ = QFileDialog.getSaveFileName(
                self._parent_window(),
                title or "Save file",
                default_filename or "quiz_template.csv",
                filetype_filter or "CSV files (*.csv);;All files (*.*)",
            )

            pending["path"] = selected_path or None
            pending["cancelled"] = not bool(selected_path)
        except Exception as exc:
            logger.error("Save file dialog failed: %s", exc, exc_info=True)
            pending["error"] = str(exc)
            pending["cancelled"] = True
            pending["path"] = None
        finally:
            wait_event.set()


_bridge_instance = None
_bridge_lock = threading.Lock()


def _get_bridge() -> _QtFileDialogBridge:
    global _bridge_instance

    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication is not available")

    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = _QtFileDialogBridge()
            _bridge_instance.moveToThread(app.thread())

    return _bridge_instance


def select_local_media_file(media_type: str, filetype_filter: str) -> dict:
    return _get_bridge().select_local_media_file(media_type, filetype_filter)


def select_save_file_path(title: str, default_filename: str, filetype_filter: str) -> dict:
    return _get_bridge().select_save_file_path(title, default_filename, filetype_filter)
