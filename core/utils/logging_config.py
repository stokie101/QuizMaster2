"""Central QuizMaster runtime logging configuration.

All runtime diagnostics should flow through the Python logging root logger and
land in the user's AppData QuizMaster log directory. Feature modules must not
create their own long-lived file handlers.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_CONFIGURED = False
_LOG_DIR: Optional[Path] = None
_FILE_HANDLERS: list[logging.Handler] = []


def resolve_appdata_root() -> Path:
    """Return the persistent QuizMaster data root for this platform."""
    configured = os.environ.get("QUIZMASTER_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    env_appdata = os.environ.get("APPDATA")
    if env_appdata:
        return Path(env_appdata).expanduser() / "QuizMaster"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "QuizMaster"
    return Path.home() / ".quizmaster"


def ensure_appdata_dirs(appdata_root: Optional[Path] = None) -> Path:
    """Create the standard QuizMaster AppData directories and return the root."""
    root = Path(appdata_root) if appdata_root else resolve_appdata_root()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "cache").mkdir(parents=True, exist_ok=True)
    (root / "exports").mkdir(parents=True, exist_ok=True)
    os.environ["QUIZMASTER_DATA_DIR"] = str(root)
    return root


def _level_from_env(name: str, default: str) -> int:
    raw = (os.environ.get(name) or default).strip().upper()
    return getattr(logging, raw, getattr(logging, default.upper(), logging.INFO))


def setup_quizmaster_logging(appdata_root: Optional[Path] = None, *, force: bool = False) -> Path:
    """Configure root logging once and return the main runtime log path.

    Dev mode is intentionally verbose. Set QUIZMASTER_MODE=release or override
    QUIZMASTER_LOG_LEVEL / QUIZMASTER_CONSOLE_LOG_LEVEL to reduce output.
    """
    global _CONFIGURED, _LOG_DIR, _FILE_HANDLERS

    root_dir = ensure_appdata_dirs(appdata_root)
    log_dir = root_dir / "logs"
    main_log = log_dir / "quizmaster.log"
    error_log = log_dir / "quizmaster_error.log"

    if _CONFIGURED and not force:
        return main_log

    if force:
        close_quizmaster_logging()

    mode = (os.environ.get("QUIZMASTER_MODE") or "dev").strip().lower()
    default_file_level = "DEBUG" if mode in {"dev", "development", "debug"} else "INFO"
    default_console_level = "INFO" if mode in {"dev", "development", "debug"} else "WARNING"
    file_level = _level_from_env("QUIZMASTER_LOG_LEVEL", default_file_level)
    console_level = _level_from_env("QUIZMASTER_CONSOLE_LOG_LEVEL", default_console_level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(threadName)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(min(file_level, console_level, logging.DEBUG))

    # Remove handlers we created previously, and remove old basicConfig stream
    # handlers so startup reloads do not duplicate messages.
    for handler in list(root_logger.handlers):
        if getattr(handler, "_quizmaster_central", False) or isinstance(handler, logging.StreamHandler):
            root_logger.removeHandler(handler)
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(formatter)
    console._quizmaster_central = True  # type: ignore[attr-defined]

    main_handler = RotatingFileHandler(
        main_log,
        maxBytes=int(os.environ.get("QUIZMASTER_LOG_MAX_BYTES", str(10 * 1024 * 1024))),
        backupCount=int(os.environ.get("QUIZMASTER_LOG_BACKUPS", "5")),
        encoding="utf-8",
    )
    main_handler.setLevel(file_level)
    main_handler.setFormatter(formatter)
    main_handler._quizmaster_central = True  # type: ignore[attr-defined]

    error_handler = RotatingFileHandler(
        error_log,
        maxBytes=int(os.environ.get("QUIZMASTER_ERROR_LOG_MAX_BYTES", str(5 * 1024 * 1024))),
        backupCount=int(os.environ.get("QUIZMASTER_ERROR_LOG_BACKUPS", "3")),
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    error_handler._quizmaster_central = True  # type: ignore[attr-defined]

    root_logger.addHandler(console)
    root_logger.addHandler(main_handler)
    root_logger.addHandler(error_handler)

    _FILE_HANDLERS = [main_handler, error_handler]
    _LOG_DIR = log_dir
    _CONFIGURED = True

    logging.getLogger("QuizMaster").info(
        "quizmaster_logging_ready mode=%s log_file=%s error_log_file=%s level=%s console_level=%s",
        mode,
        main_log,
        error_log,
        logging.getLevelName(file_level),
        logging.getLevelName(console_level),
    )
    return main_log


def crash_log_path() -> Path:
    """Return the central crash log path under AppData logs."""
    root = ensure_appdata_dirs()
    return root / "logs" / "crash.log"


def close_quizmaster_logging() -> None:
    """Flush and close central file handlers for Windows-safe shutdown/tests."""
    global _CONFIGURED, _FILE_HANDLERS
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_quizmaster_central", False):
            root_logger.removeHandler(handler)
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass
    _FILE_HANDLERS = []
    _CONFIGURED = False


def current_log_dir() -> Optional[Path]:
    return _LOG_DIR
