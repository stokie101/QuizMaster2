"""
core/server/bridge_routes.py — Main Route Registration (Refactored & Modular)

This file orchestrates all route registrations by delegating to specialized modules.
Clean, maintainable, and easy to extend.
"""

import logging

from fastapi import FastAPI

from config.config_manager import ConfigManager
from core.services.service_locator import ServiceLocator

logger = logging.getLogger(__name__)


# ============================================================
# SERVICE ACCESS HELPERS (single-instance ALWAYS)
# ============================================================

def _get_sl_service(name):
    """Helper to get service from ServiceLocator safely."""
    try:
        sl = ServiceLocator.get_instance()
        # Try 'get_service' (standard)
        if hasattr(sl, 'get_service'):
            return sl.get_service(name)
        # Try 'get' (legacy/alternative)
        if hasattr(sl, 'get'):
            return sl.get(name)
        return None
    except Exception:
        return None


def QM():
    """Return the single QuizManager instance from ServiceLocator."""
    return _get_sl_service("QuizManager")


def CSV():
    """Return the single CSVHandler instance from ServiceLocator."""
    return _get_sl_service("CSVHandler")


def TM():
    """Return the single TikTokLiveManager instance if available."""
    return _get_sl_service("TikTokLiveManager")


def CM():
    """Return the ConfigManager instance."""
    # Try ServiceLocator first
    cm = _get_sl_service("ConfigManager")
    if cm:
        return cm

    # Fallback to direct singleton if ServiceLocator fails (Safety net)
    try:
        return ConfigManager.get_instance()
    except Exception:
        return None



# ============================================================
# MAIN ROUTE REGISTRATION ENTRYPOINT
# ============================================================

def register_routes(app: FastAPI, server):
    """Register the lightweight QuizMaster routes."""

    logger.info("=" * 60)
    logger.info("REGISTERING QUIZMASTER ROUTES")
    logger.info("=" * 60)

    from core.server.routes.static_routes import register_static_routes
    register_static_routes(app, server)
    logger.info("✅ Static routes registered")

    from core.server.routes.settings_routes import register_settings_routes
    register_settings_routes(app, server)
    logger.info("✅ Settings routes registered")

    from core.server.routes.account_routes import register_account_routes
    register_account_routes(app, server)
    logger.info("✅ Account routes registered")

    # Quiz leaderboard overlays still use the widget-session snapshot endpoint.
    from core.server.widget_session_routes import register_widget_session_routes
    register_widget_session_routes(app, server)
    logger.info("✅ Widget session routes registered")

    from core.quiz.manager.quiz_routes import register_quiz_routes
    register_quiz_routes(app, server)
    logger.info("✅ Quiz routes registered")

    from core.quiz.manager.leaderboard_routes import register_leaderboard_routes
    register_leaderboard_routes(app, server)
    logger.info("✅ Leaderboard routes registered")

    # Remove filesystem-backed frontend routes and re-register them from the
    # embedded asset bundle so release installs do not expose editable HTML/CSS/JS.
    from core.server.routes.embedded_frontend_routes import register_embedded_frontend_routes
    register_embedded_frontend_routes(app)
    logger.info("✅ Embedded frontend route overrides registered")

    from core.server.routes.tiktok_routes import register_tiktok_routes
    register_tiktok_routes(app, server)
    logger.info("✅ TikTok routes registered")

    from core.server.obs.obs_routes import register_obs_routes
    register_obs_routes(app, getattr(server, 'socketio', None), server)
    logger.info("✅ OBS routes registered")

    from core.server.routes.hosted_control_routes import register_hosted_control_routes
    register_hosted_control_routes(app, server)
    logger.info("✅ Hosted control dock routes registered")

    from core.server.routes.client_routes import register_client_routes
    register_client_routes(app, server)
    logger.info("✅ Client routes registered")
