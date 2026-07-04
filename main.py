"""
QuizMaster - Streaming Quiz Command Center
Entry Point - Production Version
"""

import logging
import os
import sys
import traceback
from pathlib import Path

# Disable features that cause GPU errors on some Windows setups
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-direct-composition "
    "--disable-features=VizDisplayCompositor "
    "--ignore-gpu-blocklist "
    "--use-gl=angle "
    "--log-level=3"
)

# Force ANGLE with D3D11 (best compatibility)
os.environ["QT_ANGLE_PLATFORM"] = "d3d11"
os.environ["QT_OPENGL"] = "angle"

PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_PATH = PROJECT_ROOT if (PROJECT_ROOT / "core").exists() else PROJECT_ROOT.parent
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from core.utils.logging_config import (  # noqa: E402
    close_quizmaster_logging,
    crash_log_path,
    ensure_appdata_dirs,
    setup_quizmaster_logging,
)

DATA_DIR = ensure_appdata_dirs()
os.environ["QUIZMASTER_RESOURCE_ROOT"] = str((Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else PROJECT_ROOT).resolve())
os.environ.setdefault("LIVEFORGE_RESOURCE_ROOT", os.environ["QUIZMASTER_RESOURCE_ROOT"])
os.environ.setdefault("QUIZMASTER_AUTH_MODE", "account")
QUIZMASTER_RUNTIME_LOG_PATH = setup_quizmaster_logging(DATA_DIR)
logger = logging.getLogger("QuizMaster")

from core.services.identity_resolver import log_runtime_identity  # noqa: E402

log_runtime_identity("after_central_logging_setup")


def get_resource_path(relative_path: str) -> str:
    """Get path to bundled or local resource."""
    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent
    return str(base_path / relative_path)


try:
    from core.services.local_identity import LocalIdentityService

    LOCAL_IDENTITY_STATUS = LocalIdentityService().ensure_profile()
except Exception as identity_error:
    LOCAL_IDENTITY_STATUS = {"ready": False, "error": str(identity_error)}
    logger.warning("Local identity setup failed without blocking startup: %s", identity_error, exc_info=True)

try:
    from core.services.account_service import AccountService

    ACCOUNT_STATE_STATUS = AccountService().ensure_account_state()
except Exception as account_state_error:
    ACCOUNT_STATE_STATUS = {"ready": False, "error": str(account_state_error)}
    logger.warning("Local account state setup failed without blocking startup: %s", account_state_error, exc_info=True)

try:
    import core.resources.frontend_resources_rc  # noqa: F401
except Exception:
    pass


def log_exceptions(exc_type, exc_value, exc_traceback):
    """Capture crashes in the central AppData log directory."""
    log_path = crash_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("QUIZMASTER CRASH REPORT\n")
            f.write("=" * 80 + "\n\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
            f.write("\n")
    except Exception:
        pass
    logger.critical("Unhandled exception written to crash log: %s", log_path, exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = log_exceptions


def log_system_info():
    logger.debug("=" * 80)
    logger.debug("QUIZMASTER - STREAMING QUIZ COMMAND CENTER")
    logger.debug("=" * 80)
    logger.debug("Python: %s", sys.version)
    logger.debug("Working dir: %s", os.getcwd())
    logger.debug("Process ID: %s", os.getpid())
    logger.debug("Platform: %s", sys.platform)
    logger.debug("Frozen: %s", getattr(sys, "frozen", False))
    logger.debug("Resource root: %s", os.environ.get("QUIZMASTER_RESOURCE_ROOT"))
    logger.debug("Data dir: %s", os.environ.get("QUIZMASTER_DATA_DIR"))
    logger.debug("Runtime log: %s", QUIZMASTER_RUNTIME_LOG_PATH)
    logger.debug("=" * 80)


def main():
    try:
        from core.application import Application

        log_system_info()
        logger.debug("Initializing QuizMaster...")
        log_runtime_identity("before_application_startup")
        app = Application()
        logger.debug("Starting QuizMaster application loop...")
        return app.run()
    except KeyboardInterrupt:
        logger.warning("QuizMaster interrupted by user (Ctrl+C)")
        return 130
    except Exception as e:
        logger.critical("FATAL ERROR IN QUIZMASTER: %s", e, exc_info=True)
        return 1
    finally:
        logger.debug("=" * 80)
        logger.debug("QUIZMASTER SHUTDOWN COMPLETE")
        logger.debug("=" * 80)
        close_quizmaster_logging()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
