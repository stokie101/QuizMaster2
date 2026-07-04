from typing import Dict

from core.services.service_config import ServiceConfig, ServicePriority


def get_service_configs() -> Dict[str, ServiceConfig]:
    """Define service configurations required by QuizMaster."""
    return {
        "QApplication": ServiceConfig(
            module_path="PySide6.QtWidgets",
            class_name="QApplication",
            priority=ServicePriority.CRITICAL
        ),
        "ConfigManager": ServiceConfig(
            module_path="config.config_manager",
            class_name="ConfigManager",
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance"
        ),
        "AuthService": ServiceConfig(
            module_path="core.services.auth_service",
            class_name="AuthService",
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance"
        ),
        "HTTPBridgeServer": ServiceConfig(
            module_path="core.server.bridge_server",
            class_name="HTTPBridgeServer",
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance"
        ),
        "ServiceLocator": ServiceConfig(
            module_path="core.services.service_locator",
            class_name="ServiceLocator",
            dependencies=[],
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance"
        ),
        "CSVHandler": ServiceConfig(
            module_path="core.quiz.csv.csv_handler",
            class_name="CSVHandler",
            dependencies=[],
            priority=ServicePriority.CRITICAL,
            factory_method=None,
            optional=False
        ),
        "ConnectionManager": ServiceConfig(
            module_path="core.tiktok.connection_manager",
            class_name="ConnectionManager",
            priority=ServicePriority.NORMAL,
            factory_method="get_instance"
        ),
        "TikTokLiveManager": ServiceConfig(
            module_path="core.tiktok.tiktok_live_manager",
            class_name="TikTokLiveManager",
            dependencies=[],
            priority=ServicePriority.HIGH,
            factory_method="get_instance",
            optional=False
        ),
        "SignalTracker": ServiceConfig(
            module_path="core.utils.signal_tracker",
            class_name="SignalTracker",
            dependencies=[],
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance",
            optional=True
        ),
        "MemoryUtils": ServiceConfig(
            module_path="core.utils.memory_utils",
            class_name="MemoryUtils",
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance",
            optional=False
        ),
        "MemoryMonitor": ServiceConfig(
            module_path="core.utils.memory_monitor",
            class_name="MemoryMonitor",
            priority=ServicePriority.CRITICAL,
            factory_method="get_instance",
            optional=False
        ),
        "ClientManager": ServiceConfig(
            module_path="core.tiktok.client_manager",
            class_name="ClientManager",
            priority=ServicePriority.HIGH,
            factory_method="get_instance",
            optional=False
        ),
        "LeaderboardManager": ServiceConfig(
            module_path="core.quiz.leaderboard.leaderboard_manager",
            class_name="LeaderboardManager",
            dependencies=["ConfigManager"],
            priority=ServicePriority.HIGH,
            factory_method="get_instance",
            optional=False
        ),
        "LeaderboardUtils": ServiceConfig(
            module_path="core.quiz.leaderboard.leaderboard_utils",
            class_name="LeaderboardUtils",
            dependencies=["ConfigManager"],
            priority=ServicePriority.NORMAL,
            optional=False
        ),
        "QuizManager": ServiceConfig(
            module_path="core.quiz.manager.quiz_manager",
            class_name="QuizManager",
            dependencies=[],
            priority=ServicePriority.NORMAL
        ),
        "AudioVideoManager": ServiceConfig(
            module_path="core.utils.audio_video_manager",
            class_name="AudioVideoManager",
            dependencies=["ConfigManager"],
            priority=ServicePriority.HIGH,
            factory_method="get_instance",
            optional=False
        ),
        "AudioHandler": ServiceConfig(
            module_path="core.utils.audio_handler",
            class_name="AudioHandler",
            dependencies=["ConfigManager"],
            priority=ServicePriority.HIGH,
            factory_method="get_instance",
            optional=False
        ),
        "MainWindow": ServiceConfig(
            module_path="core.display.main_window",
            class_name="MainWindow",
            dependencies=["QApplication", "ConfigManager"],
            priority=ServicePriority.NORMAL
        ),
        "OBSHandler": ServiceConfig(
            module_path="core.server.obs.obs_handler",
            class_name="OBSHandler",
            dependencies=["HTTPBridgeServer"],
            priority=ServicePriority.NORMAL,
            optional=False
        ),
    }
