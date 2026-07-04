import logging
import threading
import time
from PySide6.QtCore import QObject, Slot
from core.services.service_locator import ServiceLocator
from .question_controller import QuestionController
from .quiz_event_bridge import QuizEventBridge
from .quiz_state_machine import QuizState, QuizStateMachine
from .quiz_timer import QuizTimer
from ..csv.csv_handler import CSVHandler
from ..score.answer_processor import AnswerProcessor
from ..score.score_engine import ScoreEngine


class QuizManager(QObject):
    """Quiz lifecycle orchestrator backed by a strict single-source state machine."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("QuizManager")
        self._state_lock = threading.RLock()
        self.services = ServiceLocator.get_instance()
        self.csv = self.services.get_service("CSVHandler") or CSVHandler.get_instance()
        self.config = self.services.get_service("ConfigManager")
        self.leaderboard = self.services.get_service("LeaderboardManager")
        self.http_bridge = self.services.get_service("HTTPBridgeServer")
        try:
            from core.utils.audio_handler import AudioHandler
            self.audio = AudioHandler.get_instance()
        except Exception:
            self.audio = None
        self.state = QuizStateMachine()
        self.timer = QuizTimer(audio_handler=self.audio)
        self.questions = QuestionController(self.csv, self.config)
        self.answers = AnswerProcessor(score_engine=ScoreEngine(), config_manager=self.config, leaderboard_manager=self.leaderboard)
        self.events = QuizEventBridge(self.http_bridge)
        self._active_timer_kind = None
        self._answer_timer = None
        self._managed_tasks = set()
        self._listeners_bound = False
        self._question_in_progress = False
        self._lifecycle_generation = 0
        self._question_started_at = 0.0
        self._question_accept_until = 0.0
        self._answer_grace_seconds = 3.0
        self._ff_qid = None
        self._ff_user = None
        self._bind_listeners_once()
        self.answers.on_answer_processed = lambda: self._sync_leaderboard()
        self.answers.on_votes_updated = self._on_votes_updated
        self.answers._is_fastest_finger = self._ff_bonus_allowed

    def _ff_bonus_allowed(self, user_id):
        try:
            if self.config and hasattr(self.config, "get_fastest_finger_enabled"):
                enabled = bool(self.config.get_fastest_finger_enabled())
            elif self.config and hasattr(self.config, "get_bool"):
                enabled = bool(self.config.get_bool("POINTS", "fastest_finger_enabled", False))
            else:
                enabled = False
        except Exception as e:
            self.logger.error("Failed to read bonus setting: %s", e)
            enabled = False
        if not enabled:
            return False
        with self._state_lock:
            qid = self.questions.current_id or self.answers.current_question_id
            if qid != self._ff_qid:
                self._ff_qid = qid
                self._ff_user = None
            if self._ff_user:
                self.logger.info("Bonus already used question_id=%s user=%s skipped=%s", qid, self._ff_user, user_id)
                return False
            self._ff_user = user_id
        self.logger.info("Bonus awarded question_id=%s user=%s", qid, user_id)
        return True

    def _safe_callback(self, callback):
        def wrapper(*args, **kwargs):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"Callback error: {e}", exc_info=True)
        return wrapper

    def _bind_listeners_once(self):
        if self._listeners_bound:
            return
        self.timer.on_tick = self._safe_callback(self._on_timer_tick)
        self.timer.on_expired = self._safe_callback(self._on_question_timer_expired)
        self.questions.on_new_question = self._safe_callback(self._on_new_question)
        self.questions.on_question_number_changed = self._safe_callback(self._on_question_number_changed)
        self.questions.on_show_answers = self._safe_callback(self._on_answers_shown)
        self.answers.on_correct = lambda uid, ans, pts: self.events.emit("correct_answer", uid, ans, pts)
        self.answers.on_incorrect = lambda uid, ans: self.events.emit("incorrect_answer", uid, ans)
        self.answers.on_fastest_finger = lambda uid, bonus: self.events.emit("fastest_finger_awarded", uid, bonus)
        self.answers.get_time_remaining = self._get_time_remaining
        self._listeners_bound = True

    def _unbind_listeners(self):
        self.timer.on_tick = self.timer.on_expired = None
        self.questions.on_new_question = None
        self.questions.on_question_number_changed = None
        self.questions.on_show_answers = None
        self._listeners_bound = False

    def _transition(self, new_state: QuizState, *, emit_state_changed: bool = True) -> bool:
        ok = self.state.transition(new_state)
        if not ok:
            return False
        if emit_state_changed:
            self.events.emit("state_changed", self.state.state.value)
        return True

    def _cancel_active_timer_locked(self):
        if self._active_timer_kind == "question":
            self.timer.stop()
        elif self._active_timer_kind == "answer" and self._answer_timer is not None:
            self._answer_timer.cancel()
            self._answer_timer.join(timeout=2.0)
            self._managed_tasks.discard(self._answer_timer)
            self._answer_timer = None
        self._active_timer_kind = None

    def _cancel_all_tasks_locked(self):
        self._lifecycle_generation += 1
        self._cancel_active_timer_locked()
        self._question_accept_until = 0.0
        self._ff_qid = None
        self._ff_user = None
        for task in list(self._managed_tasks):
            if isinstance(task, threading.Timer):
                task.cancel()
                task.join(timeout=2.0)
            self._managed_tasks.discard(task)

    def _fail_safe_to_idle_locked(self, reason: str):
        self.logger.error("Fail-safe recovery triggered: %s", reason)
        self._cancel_all_tasks_locked()
        self._question_in_progress = False
        if self.state.state != QuizState.IDLE:
            self.state.transition(QuizState.IDLE, force=True)
            self.events.emit("state_changed", self.state.state.value)

    def _start_question_timer_locked(self, duration: int, *, emit_timer_started: bool = True):
        self._cancel_active_timer_locked()
        if self.timer.start(duration):
            self._active_timer_kind = "question"
            if emit_timer_started:
                self.events.emit("timer_started", duration)
            return True
        return False

    def _start_answer_timer_locked(self, duration: int):
        self._cancel_active_timer_locked()
        generation = self._lifecycle_generation
        timer = threading.Timer(duration, self._safe_callback(lambda: self._on_answer_display_expired(generation)))
        timer.daemon = True
        self._answer_timer = timer
        self._managed_tasks.add(timer)
        self._active_timer_kind = "answer"
        timer.start()

    def load(self, quiz_data):
        if not quiz_data:
            return False
        with self._state_lock:
            if self.state.state in {QuizState.RUNNING, QuizState.PAUSED}:
                return False
            if not self._transition(QuizState.LOADING):
                return False
            self._cancel_all_tasks_locked()
            self.questions.reset()
            self.answers.reset()
            self.state.set_question_progress(0, 0)
            self._question_in_progress = False
            try:
                self.questions.load_quiz_data(quiz_data)
                self.state.total_questions = len(quiz_data)
            except Exception as e:
                self.logger.error("Load failed: %s", e, exc_info=True)
                self.state.transition(QuizState.ERROR, force=True)
                self.events.emit("state_changed", self.state.state.value)
                self._fail_safe_to_idle_locked("quiz load failure")
                return False
            return self._transition(QuizState.IDLE)

    def start(self):
        with self._state_lock:
            if self.state.state not in {QuizState.IDLE, QuizState.COMPLETED, QuizState.STOPPED} or self.questions.total_questions == 0:
                return False
            if self.state.state in {QuizState.COMPLETED, QuizState.STOPPED}:
                self.questions.rewind()
                self.state.set_question_progress(0, self.questions.total_questions)
            if not self._transition(QuizState.RUNNING, emit_state_changed=False):
                return False
        if self.leaderboard and hasattr(self.leaderboard, "start_quiz"):
            self.leaderboard.start_quiz()
        self.events.quiz_started()
        if self.questions.next_question():
            return True
        with self._state_lock:
            self.state.transition(QuizState.ERROR, force=True)
            self.events.emit("state_changed", self.state.state.value)
            self._fail_safe_to_idle_locked("failed to start first question")
        return False

    def pause(self):
        with self._state_lock:
            if self.state.state != QuizState.RUNNING or not self._transition(QuizState.PAUSED, emit_state_changed=False):
                return False
            if self._active_timer_kind == "question":
                self.timer.pause()
            elif self._active_timer_kind == "answer":
                self._cancel_active_timer_locked()
        self.events.emit("quiz_paused")
        self.events.emit("state_changed", self.state.state.value)
        return True

    def resume(self):
        with self._state_lock:
            if self.state.state != QuizState.PAUSED or not self._transition(QuizState.RUNNING, emit_state_changed=False):
                return False
            if self.timer.is_paused():
                self.timer.resume()
                self._active_timer_kind = "question"
            elif self._question_in_progress:
                self._start_question_timer_locked(self._get_timer_duration())
        self.events.emit("quiz_resumed")
        self.events.emit("state_changed", self.state.state.value)
        return True

    def stop(self):
        with self._state_lock:
            if self.state.state not in {QuizState.RUNNING, QuizState.PAUSED}:
                return False
            self._cancel_all_tasks_locked()
            self._question_in_progress = False
            if not self._transition(QuizState.STOPPED, emit_state_changed=False):
                return False
        self.events.quiz_ended()
        return True

    def skip_question(self):
        with self._state_lock:
            if self.state.state not in {QuizState.RUNNING, QuizState.PAUSED}:
                return False
            if self.state.state == QuizState.PAUSED and not self._transition(QuizState.RUNNING, emit_state_changed=False):
                return False
            self._cancel_active_timer_locked()
            self._question_in_progress = False
            self._question_accept_until = 0.0
            self._ff_qid = None
            self._ff_user = None
        if self.questions.next_question():
            return True
        with self._state_lock:
            self._cancel_all_tasks_locked()
            self._question_in_progress = False
            self._transition(QuizState.COMPLETED, emit_state_changed=False)
        self.events.quiz_ended()
        return True

    def reset(self):
        with self._state_lock:
            if self.state.state in {QuizState.RUNNING, QuizState.PAUSED, QuizState.LOADING}:
                return False
            self._cancel_all_tasks_locked()
            self.questions.reset()
            self.answers.reset()
            self.state.set_question_progress(0, 0)
            self._question_in_progress = False
            self._unbind_listeners()
            self._bind_listeners_once()
            return self._transition(QuizState.IDLE)

    def set_quiz_data(self, quiz_data): return self.load(quiz_data)
    def start_quiz(self): return self.start()
    def pause_quiz(self): return self.pause()
    def resume_quiz(self): return self.resume()
    def stop_quiz(self): return self.stop()

    def _on_timer_tick(self, remaining):
        self.events.emit("timer_tick", remaining)

    def _on_new_question(self, question_dict):
        duration = self._get_timer_duration()
        with self._state_lock:
            if self.state.state != QuizState.RUNNING:
                return
            self._question_in_progress = True
            self._question_started_at = time.monotonic()
            self._question_accept_until = self._question_started_at + float(duration) + self._answer_grace_seconds
            self._ff_qid = self.questions.current_id
            self._ff_user = None
            self.answers.start_question(question_dict, self.questions.current_id)
            self.logger.info(
                "Answer window opened question_id=%s duration=%ss grace=%ss accept_until=%.3f",
                self.questions.current_id,
                duration,
                self._answer_grace_seconds,
                self._question_accept_until,
            )

        self.events.emit("question_changed", question_dict)

        with self._state_lock:
            if self.state.state != QuizState.RUNNING or not self._question_in_progress:
                return
            if self._start_question_timer_locked(duration, emit_timer_started=False):
                self.events.emit("timer_started", duration)

    def _on_question_number_changed(self, qnum):
        with self._state_lock:
            self.state.set_question_progress(qnum, self.questions.total_questions)
        self.events.emit("question_number_changed", qnum)

    def _on_question_timer_expired(self):
        with self._state_lock:
            if self.state.state != QuizState.RUNNING or self._active_timer_kind != "question":
                return
            self._question_in_progress = False
            self._active_timer_kind = "answer"

        display_time = self.questions.show_answers() or 0
        self.events.timer_expired()

        with self._state_lock:
            if self.state.state == QuizState.RUNNING:
                self._start_answer_timer_locked(display_time)
                self.events.emit("state_changed", self.state.state.value)

    def _on_answers_shown(self, answer_map):
        with self._state_lock:
            if self.state.state == QuizState.RUNNING:
                self._question_in_progress = False
                self._active_timer_kind = "answer"
                self.events.emit("state_changed", self.state.state.value)
        self.events.show_answers(answer_map)

    def _on_votes_updated(self, question_dict):
        with self._state_lock:
            if self.state.state != QuizState.RUNNING or self.questions.current_question is not question_dict:
                return
            payload = dict(question_dict)
        self.events.emit("question_changed", payload)
        if self.http_bridge and hasattr(self.http_bridge, "_update_snapshot"):
            try:
                self.http_bridge._update_snapshot("current_question", payload)
            except Exception as e:
                self.logger.debug("Failed to update vote snapshot: %s", e)

    def _auto_advance_enabled(self):
        try:
            if self.config and hasattr(self.config, "get_bool"):
                return bool(self.config.get_bool("GAMEPLAY", "auto_advance", False))
            if self.config and hasattr(self.config, "getboolean"):
                return bool(self.config.getboolean("GAMEPLAY", "auto_advance", fallback=False))
            if self.config and hasattr(self.config, "config"):
                return self.config.config.getboolean("GAMEPLAY", "auto_advance", fallback=False)
        except Exception as e:
            self.logger.error(f"Failed to read GAMEPLAY.auto_advance: {e}")
        return False

    def _on_answer_display_expired(self, generation: int):
        with self._state_lock:
            if generation != self._lifecycle_generation or self._active_timer_kind != "answer":
                return
            self._active_timer_kind = None
            self._managed_tasks.discard(self._answer_timer)
            self._answer_timer = None
            self._question_accept_until = 0.0
            if self.state.state != QuizState.RUNNING or not self._auto_advance_enabled():
                return
        if not self.questions.next_question():
            with self._state_lock:
                self._cancel_all_tasks_locked()
                self._question_in_progress = False
                self._transition(QuizState.COMPLETED, emit_state_changed=False)
            self.events.quiz_ended()

    def process_answer(self, user_id, answer, username=None):
        now = time.monotonic()
        with self._state_lock:
            state_value = self.state.state
            timer_kind = self._active_timer_kind
            accept_until = self._question_accept_until
            qid = self.questions.current_id
            current_question = self.questions.current_question
            in_question_window = state_value == QuizState.RUNNING and timer_kind == "question"
            in_latency_grace = state_value == QuizState.RUNNING and current_question is not None and now <= accept_until
            if not (in_question_window or in_latency_grace):
                self.logger.info(
                    "Rejected TikTok answer user_id=%s answer=%r state=%s timer_kind=%s now=%.3f accept_until=%.3f question_id=%s reason=outside_answer_window",
                    user_id,
                    answer,
                    state_value.value if hasattr(state_value, "value") else state_value,
                    timer_kind,
                    now,
                    accept_until,
                    qid,
                )
                return False

        accepted = self.answers.process(user_id, answer, username or user_id)
        self.logger.info(
            "%s TikTok answer user_id=%s username=%s answer=%r question_id=%s timer_kind=%s grace=%s",
            "Accepted" if accepted else "Ignored",
            user_id,
            username or user_id,
            answer,
            qid,
            timer_kind,
            bool(in_latency_grace and not in_question_window),
        )
        return accepted

    def _sync_leaderboard(self):
        if self.leaderboard and hasattr(self.leaderboard, "sync_leaderboard_to_clients"):
            try:
                self.leaderboard.sync_leaderboard_to_clients()
            except Exception as e:
                self.logger.error(f"Error syncing leaderboard: {e}")

    def _get_timer_duration(self):
        try:
            if self.config and hasattr(self.config, "get_timer_duration"):
                duration = self.config.get_timer_duration()
                if duration and duration > 0:
                    return duration
            if self.config and hasattr(self.config, "config") and self.config.config.has_option("TIMER", "duration"):
                return self.config.config.getint("TIMER", "duration")
        except Exception as e:
            self.logger.error(f"Failed to read timer duration: {e}")
        return 30

    def _get_time_remaining(self):
        return self.timer.remaining

    @Slot()
    def cleanup(self):
        with self._state_lock:
            self._cancel_all_tasks_locked()
            self._unbind_listeners()

    def get_current_question(self):
        with self._state_lock:
            return self.questions.current_question

    @property
    def total_question_count(self):
        with self._state_lock:
            return self.questions.total_questions

    def get_current_state(self):
        with self._state_lock:
            return {
                "state": self.state.state.value,
                "phase": self.state.phase.value,
                "question_number": self.state.question_number,
                "total_questions": self.state.total_questions,
                "time_remaining": self.timer.remaining,
                "current_question": self.questions.current_question,
                "timer_kind": self._active_timer_kind,
                "answer_accept_until": self._question_accept_until,
            }

    def get_question_progress(self):
        with self._state_lock:
            return self.questions.question_number, self.questions.total_questions
