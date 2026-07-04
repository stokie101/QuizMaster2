import configparser
import json
import logging
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)


def _resolve_appdata_config_path() -> Path:
    configured = os.environ.get("QUIZMASTER_DATA_DIR")
    if configured:
        return Path(configured).expanduser() / "config" / "settings.ini"
    env_appdata = os.environ.get("APPDATA")
    if env_appdata:
        return Path(env_appdata).expanduser() / "QuizMaster" / "config" / "settings.ini"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "QuizMaster" / "config" / "settings.ini"
    return Path.home() / ".quizmaster" / "config" / "settings.ini"


class ConfigManager:
    _instance = None
    _lock = Lock()

    def __new__(cls, config_path: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[Path] = None):
        self.logger = logger
        if self._initialized:
            return

        self._config_lock = Lock()
        self.config = configparser.ConfigParser()

        if config_path:
            self.config_path = Path(config_path).resolve()
        else:
            self.config_path = _resolve_appdata_config_path()

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create config directory: {e}")

        logger.info(f"ConfigManager using user settings path: {self.config_path}")
        self._load_or_create_config()
        self._initialized = True

    @classmethod
    def get_instance(cls, config_path: Optional[Path] = None) -> 'ConfigManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    def _load_or_create_config(self):
        if self.config_path.exists():
            try:
                self.config.read(str(self.config_path), encoding='utf-8')
                self._ensure_sections()
                return
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        self._create_default_config()
        self.save_config()

    def _ensure_sections(self):
        required = ["TIMER", "SOUND", "POINTS", "GAMEPLAY", "ACTIONS_EVENTS", "Leaderboard", "TikTokLive"]
        required_options = {
            "TIMER": {"duration": "30", "timer_duration": "30", "answer_display_time": "5"},
            "SOUND": {
                "enable_timer_sound": "true", "enable_background_sound": "true",
                "enable_effects_sound": "true", "timer_volume": "75",
                "background_volume": "75", "effects_volume": "75",
            },
            "POINTS": {
                "timer_responsive_points_enabled": "true", "fastest_finger_enabled": "true",
                "max_points": "10", "min_points": "5", "fastest_finger_bonus": "5",
                "max_incorrect_attempts": "3", "fastest_finger_threshold_seconds": "5",
            },
            "GAMEPLAY": {"auto_advance": "false"},
            "TikTokLive": {"last_username": "", "auto_connect": "false"},
            "Leaderboard": {"save_directory": "", "auto_save": "true"},
            "ACTIONS_EVENTS": {"enabled": "true", "actions": "[]", "events": "[]"},
        }
        dirty = False
        for sec in required:
            if not self.config.has_section(sec):
                self.config.add_section(sec)
                dirty = True
            for key, value in required_options.get(sec, {}).items():
                if not self.config.has_option(sec, key):
                    self.config.set(sec, key, value)
                    dirty = True
        if self.config.has_option("POINTS", "fastest_finger_percentage"):
            self.config.remove_option("POINTS", "fastest_finger_percentage")
            self.config.set("POINTS", "fastest_finger_threshold_seconds", "5")
            dirty = True
        if dirty:
            self.save_config()

    def _create_default_config(self):
        defaults = {
            "TIMER": {"duration": "30", "timer_duration": "30", "answer_display_time": "5"},
            "SOUND": {
                "enable_timer_sound": "true", "enable_background_sound": "true",
                "enable_effects_sound": "true", "timer_volume": "75",
                "background_volume": "75", "effects_volume": "75"
            },
            "POINTS": {
                "timer_responsive_points_enabled": "true", "fastest_finger_enabled": "true",
                "max_points": "10", "min_points": "5", "fastest_finger_bonus": "5",
                "max_incorrect_attempts": "3", "fastest_finger_threshold_seconds": "5"
            },
            "GAMEPLAY": {"auto_advance": "false"},
            "TikTokLive": {"last_username": "", "auto_connect": "false"},
            "Leaderboard": {"save_directory": "", "auto_save": "true"},
            "ACTIONS_EVENTS": {"enabled": "true", "actions": "[]", "events": "[]"}
        }
        for section, items in defaults.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for k, v in items.items():
                if not self.config.has_option(section, k):
                    self.config.set(section, k, v)

    def save(self):
        return self.save_config()

    def save_config(self):
        with self._config_lock:
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    self.config.write(f)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except Exception as e:
                logger.error(f"Failed to save config: {e}")
                return False

    def update_full_config(self, data: Dict[str, Dict[str, Any]]) -> bool:
        with self._config_lock:
            try:
                for section, settings in data.items():
                    if not self.config.has_section(section):
                        self.config.add_section(section)
                    for key, value in settings.items():
                        if isinstance(value, bool):
                            val_str = "true" if value else "false"
                        elif isinstance(value, (dict, list)):
                            val_str = json.dumps(value)
                        else:
                            val_str = str(value)
                        self.config.set(section, key, val_str)
                        if section == "TIMER" and key == "duration":
                            self.config.set("TIMER", "timer_duration", val_str)
                        if section == "TIMER" and key == "timer_duration":
                            self.config.set("TIMER", "duration", val_str)
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    self.config.write(f)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except Exception as e:
                logger.error(f"Update full config failed: {e}")
                return False

    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)

    def set(self, section, key, value):
        return self.update_full_config({section: {key: value}})

    def get_all_config(self):
        with self._config_lock:
            return {s: dict(self.config.items(s)) for s in self.config.sections()}

    def get_int(self, s, k, f=0):
        return self.config.getint(s, k, fallback=f)

    def get_bool(self, s, k, f=False):
        return self.config.getboolean(s, k, fallback=f)

    def get_json(self, s, k, f=None):
        try:
            return json.loads(self.get(s, k))
        except Exception:
            return f

    def getboolean(self, section, option, fallback=None):
        try:
            if self.config.has_option(section, option):
                return self.config.getboolean(section, option)
            return fallback
        except Exception as e:
            self.logger.error(f"Error getting boolean {section}.{option}: {e}")
            return fallback

    def getint(self, section, option, fallback=None):
        try:
            if self.config.has_option(section, option):
                return self.config.getint(section, option)
            return fallback
        except Exception as e:
            self.logger.error(f"Error getting int {section}.{option}: {e}")
            return fallback

    def getfloat(self, section, option, fallback=None):
        try:
            if self.config.has_option(section, option):
                return self.config.getfloat(section, option)
            return fallback
        except Exception as e:
            self.logger.error(f"Error getting float {section}.{option}: {e}")
            return fallback

    def get_timer_responsive_points_enabled(self) -> bool:
        return self.get_bool("POINTS", "timer_responsive_points_enabled", False)

    def get_max_points(self) -> int:
        return self.get_int("POINTS", "max_points", 10)

    def get_min_points(self) -> int:
        return self.get_int("POINTS", "min_points", 5)

    def calculate_points_based_on_time(self, time_remaining: float) -> int:
        try:
            max_points = self.get_max_points()
            min_points = self.get_min_points()
            if max_points < min_points:
                max_points, min_points = min_points, max_points
            if not self.get_timer_responsive_points_enabled():
                return max_points
            timer_duration = self.get_timer_duration()
            if timer_duration <= 0:
                timer_duration = 30
            ratio = max(0.0, min(1.0, float(time_remaining or 0) / float(timer_duration)))
            points = int(round(min_points + ((max_points - min_points) * ratio)))
            return max(min_points, min(max_points, points))
        except Exception as e:
            self.logger.error(f"Error calculating points: {e}")
            return 10

    def get_fastest_finger_enabled(self) -> bool:
        return self.get_bool("POINTS", "fastest_finger_enabled", False)

    def get_fastest_finger_bonus(self) -> int:
        return self.get_int("POINTS", "fastest_finger_bonus", 5)

    def get_fastest_finger_threshold_seconds(self) -> float:
        return self.getfloat("POINTS", "fastest_finger_threshold_seconds", 5.0)

    def get_fastest_finger_time(self) -> float:
        return self.get_fastest_finger_threshold_seconds()

    def get_max_incorrect_attempts(self) -> int:
        return self.get_int("POINTS", "max_incorrect_attempts", 3)

    def get_timer_duration(self) -> int:
        duration = self.get_int("TIMER", "duration", None)
        if duration is not None:
            return duration
        return self.get_int("TIMER", "timer_duration", 30)

    def get_answer_display_time(self) -> int:
        return self.get_int("TIMER", "answer_display_time", 5)
