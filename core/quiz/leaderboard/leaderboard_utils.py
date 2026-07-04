# leaderboard_utils.py
import json
import logging
import os
import threading
from typing import Dict, Optional, Any


# ---------------------------
# Paths & directories
# ---------------------------

def get_user_data_dir() -> str:
    """Get a reasonable user data directory based on the OS."""
    if os.name == 'nt':  # Windows
        appdata = os.environ.get('APPDATA')
        if appdata:
            return os.path.join(appdata, 'Live Forge')
    else:
        # macOS or Linux
        home = os.path.expanduser("~")
        if os.path.exists(os.path.join(home, "Library")):  # macOS
            return os.path.join(home, "Library", "Application Support", "QuizMaster")
        else:  # Linux/Unix
            return os.path.join(home, ".local", "share", "QuizMaster")

    # Fallback
    return os.path.join(os.path.expanduser("~"), "QuizMaster")


SAVE_DIR = get_user_data_dir()
LEADERBOARD_FOLDER = os.path.join(SAVE_DIR, 'leaderboard')
AVATARS_FOLDER = os.path.join(SAVE_DIR, 'avatars')
DEFAULT_AVATAR = os.path.join(SAVE_DIR, 'default_avatar.png')


# ---------------------------
# Calculations
# ---------------------------

def calculate_comparison_metrics(current_stats: Dict[str, Any], previous_stats: Optional[Dict[str, Any]]):
    """Calculate comparison metrics between current and previous session stats (safe, defensive)."""
    if not previous_stats:
        return {
            "correct_answers_change": 0,
            "participation_change": 0,
            "average_score_change": 0,
            "questions_asked_change": 0
        }

    try:
        return {
            "correct_answers_change": current_stats.get("total_correct", 0) - previous_stats.get("total_correct", 0),
            "participation_change": current_stats.get("participation_percentage", 0) - previous_stats.get(
                "participation_percentage", 0),
            "average_score_change": current_stats.get("average_score", 0) - previous_stats.get("average_score", 0),
            "questions_asked_change": current_stats.get("questions_asked", 0) - previous_stats.get("questions_asked", 0)
        }
    except Exception as e:
        logging.error(f"Error calculating comparison metrics: {e}")
        return {
            "correct_answers_change": 0,
            "participation_change": 0,
            "average_score_change": 0,
            "questions_asked_change": 0
        }


def calculate_participation_stats(chat_participants, quiz_participants, peak_viewers):
    """Calculate participation statistics (no Qt)."""
    try:
        peak = int(peak_viewers or 0)
        chat_count = len(chat_participants or [])
        quiz_count = len(quiz_participants or [])
        unique_total = len(set(chat_participants or []) | set(quiz_participants or []))

        if peak <= 0:
            return {
                "participation_percentage": 0,
                "engagement_rate": 0,
                "total_unique_users": unique_total,
                "chat_participants": chat_count,
                "quiz_participants": quiz_count,
                "participation_rate": 0
            }

        participation_percentage = (quiz_count / peak) * 100.0
        engagement_rate = (chat_count / peak) * 100.0
        return {
            "participation_percentage": round(participation_percentage, 2),
            "engagement_rate": round(engagement_rate, 2),
            "total_unique_users": unique_total,
            "chat_participants": chat_count,
            "quiz_participants": quiz_count,
            "participation_rate": round(participation_percentage, 2)
        }
    except Exception as e:
        logging.error(f"Error calculating participation stats: {e}")
        return {
            "participation_percentage": 0,
            "engagement_rate": 0,
            "total_unique_users": 0,
            "chat_participants": 0,
            "quiz_participants": 0,
            "participation_rate": 0
        }


def format_answer_distribution(answer_percentages: Dict[str, float]) -> str:
    """Format answer percentage dict to display text."""
    try:
        if not answer_percentages:
            return "No answers recorded"
        parts = [f"{answer}: {pct:.1f}%" for answer, pct in answer_percentages.items()]
        return " | ".join(parts)
    except Exception as e:
        logging.error(f"Error formatting answer distribution: {e}")
        return "Error formatting answers"


# ---------------------------
# Utils class (Qt-free)
# ---------------------------

class LeaderboardUtils:
    """
    Utilities for managing leaderboard save/load.
    Qt-free: safe to import in worker processes.
    """

    def __init__(self, custom_data_dir: Optional[str] = None):
        self.logger = logging.getLogger('LeaderboardUtils')
        self._lock = threading.RLock()

        # Resolve data directory (optionally via config, but avoid importing Qt/ServiceLocator here)
        if not custom_data_dir:
            try:
                from core.services.service_locator import ServiceLocator
                config_manager = ServiceLocator.get_instance().get_service('ConfigManager')
                if config_manager:
                    custom_data_dir = config_manager.get("leaderboard", "save_directory", "")
                    self.logger.debug(f"Got save dir from config: {custom_data_dir}")
            except Exception as e:
                self.logger.debug(f"Could not get directory from config: {e}")

        self.save_dir = custom_data_dir.strip() if (
                custom_data_dir and custom_data_dir.strip()) else get_user_data_dir()
        self.leaderboard_dir = os.path.join(self.save_dir, 'leaderboard')
        self.avatars_dir = os.path.join(self.save_dir, 'avatars')
        self.default_avatar = os.path.join(self.save_dir, 'default_avatar.png')

        self._ensure_directories_exist()
        self.logger.info(f"LeaderboardUtils initialized (save_dir={self.save_dir})")

    def _ensure_directories_exist(self):
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            os.makedirs(self.leaderboard_dir, exist_ok=True)
            os.makedirs(self.avatars_dir, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create directories: {e}")
            raise

    def set_custom_save_directory(self, directory_path: str) -> bool:
        if not directory_path or not os.path.exists(directory_path):
            self.logger.error(f"Invalid directory path: {directory_path}")
            return False

        # Test write
        try:
            test_file = os.path.join(directory_path, ".test_write")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
        except Exception as e:
            self.logger.error(f"Directory not writable: {e}")
            return False

        self.save_dir = directory_path
        self.leaderboard_dir = os.path.join(directory_path, 'leaderboard')
        self.avatars_dir = os.path.join(directory_path, 'avatars')
        self.default_avatar = os.path.join(directory_path, 'default_avatar.png')
        os.makedirs(self.leaderboard_dir, exist_ok=True)
        os.makedirs(self.avatars_dir, exist_ok=True)
        self.logger.info(f"Custom save directory set: {directory_path}")
        return True

    def get_next_session_number(self) -> int:
        with self._lock:
            try:
                files = [f for f in os.listdir(self.leaderboard_dir) if f.startswith("S") and f.endswith(".json")]
                if not files:
                    return 1
                numbers = []
                for fn in files:
                    try:
                        base = fn.split("_")[0] if "_" in fn else fn.split(".")[0]
                        if base.startswith("S") and len(base) > 1:
                            numbers.append(int(base[1:]))
                    except Exception:
                        continue
                return max(numbers) + 1 if numbers else 1
            except Exception as e:
                logging.error(f"Error determining session number: {e}")
                return 1

    def auto_load_latest_leaderboard(self):
        try:
            files = [f for f in os.listdir(self.leaderboard_dir) if f.endswith('.json')]
            if not files:
                logging.info("No leaderboard files found")
                return False
            files.sort(key=lambda x: os.path.getmtime(os.path.join(self.leaderboard_dir, x)), reverse=True)
            latest = os.path.join(self.leaderboard_dir, files[0])
            logging.info(f"Loading latest leaderboard: {latest}")
            with open(latest, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading latest leaderboard: {e}")
            return False

    def load_leaderboard(self, file_path: str):
        try:
            if not os.path.exists(file_path):
                self.logger.error(f"File not found: {file_path}")
                return False
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading leaderboard: {e}")
            return False

    # ---- Serialization helpers (Qt-free) ----

    def _clean_data_for_json(self, data):
        """
        Remove objects that aren't JSON-serializable.
        This version avoids importing Qt entirely.
        """
        if isinstance(data, dict):
            clean = {}
            for k, v in data.items():
                cv = self._clean_data_for_json(v)
                if cv is not None:
                    clean[k] = cv
            return clean
        elif isinstance(data, list):
            return [self._clean_data_for_json(v) for v in data]
        elif isinstance(data, (str, int, float, bool)) or data is None:
            return data
        else:
            # Last-resort: stringify
            try:
                json.dumps(data)
                return data
            except Exception:
                return str(data)

    def create_serializable_leaderboard(self, leaderboard_data: Dict[str, Dict[str, Any]],
                                        session_number: Optional[int] = None):
        """
        Create a JSON-serializable copy of leaderboard data.
        Assumes avatars are strings (URLs/base64) already (recommended).
        """
        try:
            result = {}
            for user_id, user_data in (leaderboard_data or {}).items():
                copy = dict(user_data)
                # Normalize avatar field (prefer avatar_url)
                if "avatar_url" in copy and copy.get("avatar_url"):
                    copy["avatar"] = copy["avatar_url"]
                # Clean non-serializable pieces
                copy = self._clean_data_for_json(copy)
                result[str(user_id)] = copy
            return result
        except Exception as e:
            import traceback
            logging.error(f"Error creating serializable leaderboard: {e}")
            logging.error(traceback.format_exc())
            return {}

    @staticmethod
    def safe_write_json(data: Dict[str, Any], folder: str, filename: str) -> str:
        """Atomic JSON write; returns full path if successful, raises otherwise."""
        os.makedirs(folder, exist_ok=True)
        tmp_path = None
        full_path = os.path.join(folder, filename)
        try:
            import tempfile, shutil
            fd, tmp_path = tempfile.mkstemp(prefix="lb_", suffix=".json", dir=folder, text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            shutil.move(tmp_path, full_path)
            return full_path
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def get_session_files(self):
        pass


# Public API
__all__ = [
    "LeaderboardUtils", "calculate_participation_stats", "calculate_comparison_metrics",
    "DEFAULT_AVATAR", "SAVE_DIR", "LEADERBOARD_FOLDER", "AVATARS_FOLDER", "get_user_data_dir"
]
