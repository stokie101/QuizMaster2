import logging
import threading
import time
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, Set

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap

from core.quiz.leaderboard.leaderboard_save_worker import SaveProcessController
from core.quiz.leaderboard.leaderboard_utils import (
    SAVE_DIR,
    LEADERBOARD_FOLDER,
    AVATARS_FOLDER,
    LeaderboardUtils
)
from core.server.bridge_server import HTTPBridgeServer


class LeaderboardManager:
    """
    Manages leaderboard data - PASSIVE MODEL: only receives updates from QuizManager.
    Process-safe: saving is offloaded to a separate process (no Qt in child).
    Debounced updates to UI via QuizSignals.
    """

    _instance: Optional['LeaderboardManager'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'LeaderboardManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self._initialized = True

        def debug_watch(attr, value):
            self.logger.error(
                f"🔥 LEADERBOARD_DATA CHANGED TO {type(value)} from: \n{''.join(traceback.format_stack())}")

        orig_setattr = self.__setattr__

        def tracking_setattr(name, value):
            if name == "leaderboard_data":
                debug_watch(name, value)
            orig_setattr(name, value)

        self.__setattr__ = tracking_setattr

        self._debounce_timer: Optional[QTimer] = None
        self._debounce_lock = threading.Lock()
        self._update_pending = False
        self._last_update_time = 0.0
        self._min_update_interval = 0.1
        self._update_timer: Optional[threading.Timer] = None

        self.bridge = HTTPBridgeServer.get_instance()
        self.leaderboard_data: Dict[str, Dict[str, Any]] = {}
        self.chat_participants: Set[str] = set()
        self.quiz_participants: Set[str] = set()
        self.question_history: Dict[str, Dict[str, bool]] = {}
        self.current_question: Optional[str] = None

        self._data_lock = threading.Lock()
        self.validate_leaderboard_data()

        self.quiz_started = False
        self.session_number = 1
        self.session_start_time: Optional[datetime] = None
        self.session_stats = self._create_empty_stats()
        self._previous_session_stats: Optional[Dict[str, Any]] = None

        self._setup_directories()
        self._save_controller: Optional[SaveProcessController] = None
        self.logger.info("LeaderboardManager initialized ...")

    def _initialize_services(self):
        pass

    def _setup_directories(self):
        import os
        self.save_dir = SAVE_DIR
        self.leaderboard_dir = LEADERBOARD_FOLDER
        self.avatars_dir = AVATARS_FOLDER
        for directory, name in [
            (self.save_dir, "save directory"),
            (self.leaderboard_dir, "leaderboard directory"),
            (self.avatars_dir, "avatars directory")
        ]:
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                self.logger.error(f"Failed to create {name}: {e}")

    def _connect_quiz_signals(self):
        pass

    def _schedule_debounced_update(self):
        with self._debounce_lock:
            if self._debounce_timer and self._debounce_timer.isActive():
                self._debounce_timer.stop()
                self._debounce_timer = None
            self._debounce_timer = QTimer()
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(self._emit_leaderboard_update)
            self._debounce_timer.start(100)
            self.logger.debug("Debounced update scheduled")

    def _build_entries_locked(self):
        leaderboard_list = []
        for uid, udata in self.leaderboard_data.items():
            display_name = self._get_safe_display_name(uid, udata)
            entry = {
                "user_id": uid,
                "username": display_name,
                "display_name": display_name,
                "name": display_name,
                "score": int(udata.get("score", 0) or 0),
                "correct_answers": int(udata.get("correct_answers", 0) or 0),
                "incorrect_answers": int(udata.get("incorrect_answers", 0) or 0),
                "correct": int(udata.get("correct_answers", 0) or 0),
                "incorrect": int(udata.get("incorrect_answers", 0) or 0),
                "streak": int(udata.get("streak", 0) or 0),
                "rank": 0,
                "avatar_url": udata.get("avatar_url"),
                "avatar": udata.get("avatar_url"),
            }
            leaderboard_list.append(entry)
            self.logger.info(f"   User: {uid} ({display_name}) - Score: {entry['score']}")
        leaderboard_list.sort(key=lambda x: x["score"], reverse=True)
        for i, entry in enumerate(leaderboard_list, 1):
            entry["rank"] = i
        return leaderboard_list

    def _publish_leaderboard_entries(self, entries):
        payload = {
            "entries": entries,
            "timestamp": datetime.now().isoformat()
        }
        try:
            if self.bridge and hasattr(self.bridge, "_update_snapshot"):
                self.bridge._update_snapshot("leaderboard", entries)
        except Exception as exc:
            self.logger.warning("Failed to update bridge leaderboard snapshot: %s", exc)
        self.bridge.emit_signal_ws('leaderboard_updated', payload)

    def _emit_leaderboard_update(self):
        try:
            current_time = time.time()
            since = current_time - self._last_update_time
            if since < self._min_update_interval:
                if not self._update_pending:
                    self._update_pending = True
                    delay = self._min_update_interval - since
                    if self._update_timer:
                        self._update_timer.cancel()
                    self._update_timer = threading.Timer(delay, self._do_emit_leaderboard_update)
                    self._update_timer.start()
                    self.logger.debug(f"Debounced update scheduled in {delay:.3f}s")
                return
            self._do_emit_leaderboard_update()
        except Exception as e:
            self.logger.error(f"Error in debounced update: {e}", exc_info=True)

    def _do_emit_leaderboard_update(self):
        try:
            with self._data_lock:
                self.logger.info("=" * 60)
                self.logger.info("🟢 EMITTING LEADERBOARD UPDATE")
                self.logger.info(f"   Total users: {len(self.leaderboard_data)}")
                leaderboard_list = self._build_entries_locked()

            self.logger.info("   📡 Emitting leaderboard_updated (dict + list) and updating snapshot...")
            self._publish_leaderboard_entries(leaderboard_list)
            self._last_update_time = time.time()
            self._update_pending = False
            self._update_timer = None
            self.logger.info("=" * 60)
        except Exception as e:
            self.logger.error(f"Error emitting leaderboard update: {e}", exc_info=True)
            self._update_pending = False

    @staticmethod
    def _create_empty_stats() -> Dict[str, Any]:
        return {
            "total_participants": 0,
            "quiz_participants": 0,
            "chat_participants": 0,
            "session_duration": 0,
            "peak_viewers": 0,
            "total_correct": 0,
            "participation_percentage": 0.0,
            "average_score": 0.0,
            "questions_asked": 0
        }

    @staticmethod
    def _create_user_record(user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        if not display_name or not display_name.strip():
            display_name = f"User_{str(user_id)[:8]}"
        return {
            "user_id": user_id,
            "display_name": display_name,
            "username": display_name,
            "score": 0,
            "correct_answers": 0,
            "incorrect_answers": 0,
            "answered_questions": {},
            "attempted_current_question": False,
            "answered_correctly_current_question": False
        }

    @staticmethod
    def _get_safe_display_name(user_id: str, udata: Dict[str, Any]) -> str:
        name = udata.get("display_name") or udata.get("username") or udata.get("name") or None
        if name and str(name).strip() and not str(name).strip().isdigit():
            return str(name).strip()
        uid_str = str(user_id)
        return f"User_{uid_str[:8]}" if uid_str.isdigit() else uid_str[:20]

    def register_chat_user_with_pixmap(self, user_id: str, display_name: Optional[str] = None,
                                       avatar_pixmap: Optional[QPixmap] = None):
        if not user_id:
            self.logger.warning("register_chat_user_with_pixmap called without user_id")
            return
        try:
            self.logger.info("=" * 60)
            self.logger.info("🟢 LEADERBOARD: Registering user")
            self.logger.info(f"   user_id: {user_id}")
            self.logger.info(f"   display_name: {display_name}")
            self.logger.info(f"   has_avatar: {avatar_pixmap is not None and not avatar_pixmap.isNull()}")
            with self._data_lock:
                if not isinstance(self.leaderboard_data, dict):
                    self.logger.error(f"❌ leaderboard_data is {type(self.leaderboard_data)}, resetting to dict")
                    self.leaderboard_data = {}
                self.chat_participants.add(user_id)
                if user_id not in self.leaderboard_data:
                    self.leaderboard_data[user_id] = self._create_user_record(user_id, display_name)
                    self.logger.info("   ✅ Created new user record")
                else:
                    if display_name and display_name.strip():
                        self.leaderboard_data[user_id]["display_name"] = display_name
                        self.leaderboard_data[user_id]["username"] = display_name
                    self.logger.info("   ✅ Updated existing user record")
                if avatar_pixmap and isinstance(avatar_pixmap, QPixmap) and not avatar_pixmap.isNull():
                    avatar_url = self._pixmap_to_base64(avatar_pixmap)
                    if avatar_url:
                        self.leaderboard_data[user_id]["avatar_url"] = avatar_url
                        self.bridge.emit_signal_ws('avatar_updated', {'user_id': user_id, 'avatar_url': avatar_url})
                        self.logger.info("   ✅ Avatar saved")
                self.logger.info(f"   📊 Users in leaderboard: {len(self.leaderboard_data)}")
                if user_id in self.leaderboard_data:
                    self.logger.info(f"   ✅ VERIFIED: User {user_id} is in leaderboard")
                else:
                    self.logger.error(f"   ❌ FAILED: User {user_id} NOT in leaderboard after registration!")
            self._emit_leaderboard_update()
            self.logger.info("=" * 60)
        except Exception as e:
            self.logger.exception(f"Error registering chat user: {e}")

    def validate_leaderboard_data(self):
        try:
            if not isinstance(self.leaderboard_data, dict):
                self.logger.error(f"❌ leaderboard_data is {type(self.leaderboard_data)}, expected dict")
                self.logger.error("   Converting to dict...")
                self.leaderboard_data = {}
                return False
            self.logger.info(f"✅ leaderboard_data is valid dict with {len(self.leaderboard_data)} entries")
            return True
        except Exception as e:
            self.logger.error(f"Error validating leaderboard: {e}", exc_info=True)
            return False

    def update_user_avatar(self, user_id: str, avatar_pixmap: QPixmap):
        if not user_id or not isinstance(avatar_pixmap, QPixmap) or avatar_pixmap.isNull():
            return
        try:
            avatar_url = self._pixmap_to_base64(avatar_pixmap)
            if not avatar_url:
                return
            with self._data_lock:
                if user_id not in self.leaderboard_data:
                    self.leaderboard_data[user_id] = self._create_user_record(user_id)
                self.leaderboard_data[user_id]["avatar_url"] = avatar_url
            self._emit_leaderboard_update()
            self.bridge.emit_signal_ws('avatar_updated', {'user_id': user_id, 'avatar_url': avatar_url})
        except Exception as e:
            self.logger.exception(f"Error updating avatar: {e}")

    def update_user_score(self, user_id: str, points: int):
        if not user_id:
            return
        try:
            with self._data_lock:
                if user_id not in self.leaderboard_data:
                    self.leaderboard_data[user_id] = self._create_user_record(user_id)
                self.leaderboard_data[user_id]["score"] = self.leaderboard_data[user_id].get("score", 0) + int(points)
            self._emit_leaderboard_update()
            self.logger.info(f"Updated score for {user_id}: +{points} points")
        except Exception as e:
            self.logger.exception(f"Error updating user score: {e}")

    def start_quiz(self):
        self.logger.info("Starting new quiz session")
        try:
            self.quiz_started = True
            self.session_start_time = datetime.now()
            with self._data_lock:
                for uid in self.leaderboard_data:
                    self.leaderboard_data[uid].update({
                        "score": 0,
                        "correct_answers": 0,
                        "incorrect_answers": 0,
                        "answered_questions": {},
                        "attempted_current_question": False,
                        "answered_correctly_current_question": False
                    })
                self.quiz_participants.clear()
                self.question_history.clear()
                self.current_question = None
            self.bridge.emit_signal_ws('quiz_started', {
                "timestamp": datetime.now().isoformat(),
                "session_number": self.session_number
            })
            self._emit_stats_update()
            self._emit_leaderboard_update()
            self.logger.info("Quiz started - scores reset and signals emitted")
        except Exception as e:
            self.logger.exception(f"Error starting quiz: {e}")

    def stop_quiz(self):
        self.logger.info("Stopping quiz session")
        try:
            self.quiz_started = False
            utils = LeaderboardUtils()
            payload = {
                "leaderboard_data": self.get_leaderboard_data(),
                "session_number": self.session_number,
                "leaderboard_dir": utils.leaderboard_dir,
                "avatars_dir": utils.avatars_dir,
                "session_stats": dict(self.session_stats),
                "previous_session_stats": dict(self._previous_session_stats) if self._previous_session_stats else None,
                "chat_participants": list(self.chat_participants),
                "quiz_participants": list(self.quiz_participants),
                "question_history": dict(self.question_history),
            }
            self._save_controller = SaveProcessController()
            self._save_controller.started.connect(lambda: self.logger.info("🚀 Save process started"))
            self._save_controller.completed.connect(self._on_save_completed)
            self._save_controller.start(payload)
        except Exception as e:
            self.logger.exception(f"Error stopping quiz: {e}")

    def start_new_question(self, question_id: str):
        if not question_id:
            return
        try:
            with self._data_lock:
                self.current_question = question_id
                self.question_history[question_id] = {}
                for uid in self.leaderboard_data:
                    self.leaderboard_data[uid]["attempted_current_question"] = False
                    self.leaderboard_data[uid]["answered_correctly_current_question"] = False
            self._emit_leaderboard_update()
            self.logger.info(f"Started new question: {question_id}")
        except Exception as e:
            self.logger.exception(f"Error starting new question: {e}")

    def record_correct_answer(self, user_id: str, question_id: str, points: int,
                              display_name: Optional[str] = None) -> bool:
        if not user_id or not question_id or points < 0:
            return False
        try:
            already = False
            with self._data_lock:
                if user_id not in self.leaderboard_data:
                    self.logger.error("User not in leaderboard (should have been registered via chat)")
                    return False
                self.quiz_participants.add(user_id)
                if "answered_questions" not in self.leaderboard_data[user_id]:
                    self.leaderboard_data[user_id]["answered_questions"] = {}
                already = (
                    question_id in self.leaderboard_data[user_id]["answered_questions"] and
                    self.leaderboard_data[user_id]["answered_questions"][question_id].get("correct", False)
                )
                if not already:
                    if question_id not in self.question_history:
                        self.question_history[question_id] = {}
                    self.question_history[question_id][user_id] = True
                    self.leaderboard_data[user_id]["answered_questions"][question_id] = {
                        "attempted": True,
                        "correct": True,
                        "timestamp": datetime.now().isoformat()
                    }
                    self.leaderboard_data[user_id]["correct_answers"] += 1
                    self.leaderboard_data[user_id]["attempted_current_question"] = True
                    self.leaderboard_data[user_id]["answered_correctly_current_question"] = True
                    current_score = self.leaderboard_data[user_id].get("score", 0)
                    self.leaderboard_data[user_id]["score"] = current_score + points
                    self.logger.info(f"✅ {user_id} answered correctly (+{points}, total: {current_score + points})")
            self._emit_leaderboard_update()
            with self._data_lock:
                self.session_stats["quiz_participants"] = len(self.quiz_participants)
                self.session_stats["chat_participants"] = len(self.chat_participants)
                self.session_stats["total_participants"] = len(self.chat_participants | self.quiz_participants)
            self._emit_stats_update()
            return not already
        except Exception as e:
            self.logger.exception(f"Error recording correct answer: {e}")
            return False

    def record_incorrect_answer(self, user_id: str, question_id: str, display_name: Optional[str] = None) -> bool:
        if not user_id or not question_id:
            return False
        try:
            already = False
            with self._data_lock:
                if user_id not in self.leaderboard_data:
                    self.logger.error("User not in leaderboard (should have been registered via chat)")
                    return False
                self.quiz_participants.add(user_id)
                if "answered_questions" not in self.leaderboard_data[user_id]:
                    self.leaderboard_data[user_id]["answered_questions"] = {}
                already = (
                    question_id in self.leaderboard_data[user_id]["answered_questions"] and
                    self.leaderboard_data[user_id]["answered_questions"][question_id].get("attempted")
                )
                if not already:
                    if question_id not in self.question_history:
                        self.question_history[question_id] = {}
                    self.question_history[question_id][user_id] = False
                    self.leaderboard_data[user_id]["answered_questions"][question_id] = {
                        "attempted": True,
                        "correct": False,
                        "timestamp": datetime.now().isoformat()
                    }
                    self.leaderboard_data[user_id]["incorrect_answers"] += 1
                    self.leaderboard_data[user_id]["attempted_current_question"] = True
                    self.logger.info(f"❌ {user_id} answered incorrectly")
            self._emit_leaderboard_update()
            with self._data_lock:
                self.session_stats["quiz_participants"] = len(self.quiz_participants)
                self.session_stats["chat_participants"] = len(self.chat_participants)
                self.session_stats["total_participants"] = len(self.chat_participants | self.quiz_participants)
            self._emit_stats_update()
            return not already
        except Exception as e:
            self.logger.exception(f"Error recording incorrect answer: {e}")
            return False

    def _emit_stats_update(self):
        try:
            stats = dict(self.session_stats)
            stats['session_number'] = self.session_number
            self.bridge.emit_signal_ws('session_stats_updated', stats)
        except Exception as e:
            self.logger.error(f"Error emitting stats update: {e}")

    def _pixmap_to_base64(self, pixmap: QPixmap) -> Optional[str]:
        if not pixmap or pixmap.isNull():
            return None
        try:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            import base64
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buf, "PNG")
            buf.close()
            return f"data:image/png;base64,{base64.b64encode(ba.data()).decode('utf-8')}"
        except Exception as e:
            self.logger.error(f"Error converting pixmap to base64: {e}")
            return None

    def reset_leaderboard(self):
        try:
            self.logger.info("🔄 Resetting leaderboard...")
            with self._data_lock:
                old_count = len(self.leaderboard_data)
                for user_id in self.leaderboard_data:
                    self.leaderboard_data[user_id].update({
                        "score": 0,
                        "correct_answers": 0,
                        "incorrect_answers": 0,
                        "answered_questions": {},
                        "attempted_current_question": False,
                        "answered_correctly_current_question": False,
                        "streak": 0
                    })
                self.quiz_participants.clear()
                self.question_history.clear()
                self.current_question = None
                self.logger.info(f"✅ Cleared scores for {old_count} users")
            self.bridge.emit_signal_ws('leaderboard_reset', {
                "timestamp": datetime.now().isoformat()
            })
            self._emit_leaderboard_update()
            with self._data_lock:
                self.session_stats = self._create_empty_stats()
            self._emit_stats_update()
            self.logger.info("✅ Leaderboard reset complete")
            return True
        except Exception as e:
            self.logger.error(f"Failed to reset leaderboard: {e}", exc_info=True)
            return False

    def sync_leaderboard_to_clients(self):
        try:
            with self._data_lock:
                if isinstance(self.leaderboard_data, dict):
                    entries = self._build_entries_locked()
                elif isinstance(self.leaderboard_data, list):
                    self.logger.warning("⚠️ leaderboard_data is a list, expected dict!")
                    entries = self.leaderboard_data
                else:
                    self.logger.error(f"❌ Unexpected leaderboard_data type: {type(self.leaderboard_data)}")
                    return
            self._publish_leaderboard_entries(entries)
            self.logger.info(f"✅ Synced {len(entries)} leaderboard entries to clients")
        except AttributeError as e:
            self.logger.error(f"Error syncing leaderboard (AttributeError): {e}", exc_info=True)
            self.logger.error(f"leaderboard_data type: {type(self.leaderboard_data)}")
        except Exception as e:
            self.logger.error(f"Error syncing leaderboard: {e}", exc_info=True)

    def get_leaderboard_data(self):
        try:
            with self._data_lock:
                entries = []
                for user_id, udata in self.leaderboard_data.items():
                    display_name = self._get_safe_display_name(user_id, udata)
                    entries.append({
                        "user_id": user_id,
                        "username": display_name,
                        "display_name": display_name,
                        "name": display_name,
                        "score": udata.get("score", 0),
                        "correct": udata.get("correct_answers", 0),
                        "incorrect": udata.get("incorrect_answers", 0),
                        "streak": udata.get("streak", 0),
                        "avatar_url": udata.get("avatar_url"),
                        "avatar": udata.get("avatar_url"),
                    })
                entries.sort(key=lambda x: x.get("score", 0), reverse=True)
                for i, entry in enumerate(entries, 1):
                    entry["rank"] = i
                return entries
        except Exception as e:
            self.logger.error(f"Failed to get leaderboard data: {e}")
            return []

    def cleanup(self):
        try:
            if self._update_timer:
                self._update_timer.cancel()
                self._update_timer = None
            self.logger.info("LeaderboardManager cleaned up")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    def _on_save_completed(self, success: bool, message: str):
        if success:
            self.logger.info(f"✅ Leaderboard saved: {message}")
            try:
                self._previous_session_stats = dict(self.session_stats)
            except Exception:
                pass
        else:
            self.logger.error(f"❌ Leaderboard save failed: {message}")
