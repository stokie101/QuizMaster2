import threading
import time
from enum import Enum


class QuizState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    RUNNING = "running"
    # Backward-compatible alias used by newer UI naming.
    QUESTION_ACTIVE = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    # Backward-compatible alias used by newer UI naming.
    ENDED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


class QuizPhase(Enum):
    GET_READY = "get_ready"
    FIRST_QUESTION = "first_question"
    EARLY_GAME = "early_game"
    MID_GAME = "mid_game"
    LATE_GAME = "late_game"
    FINAL_QUESTION = "final_question"


class QuizStateMachine:
    """Single-source lifecycle state machine for quiz orchestration."""

    def __init__(self):
        self._lock = threading.RLock()
        self.state: QuizState = QuizState.IDLE
        self.phase: QuizPhase = QuizPhase.GET_READY
        self.question_number = 0
        self.total_questions = 0
        self.start_time = None
        self.last_change = time.time()

        self.allowed = {
            QuizState.IDLE: {QuizState.LOADING, QuizState.RUNNING, QuizState.ERROR},
            QuizState.LOADING: {QuizState.IDLE, QuizState.ERROR},
            QuizState.RUNNING: {QuizState.PAUSED, QuizState.COMPLETED, QuizState.STOPPED, QuizState.ERROR},
            QuizState.PAUSED: {QuizState.RUNNING, QuizState.STOPPED, QuizState.ERROR},
            QuizState.COMPLETED: {QuizState.IDLE, QuizState.LOADING, QuizState.RUNNING},
            QuizState.STOPPED: {QuizState.IDLE, QuizState.LOADING, QuizState.RUNNING},
            QuizState.ERROR: {QuizState.IDLE, QuizState.LOADING},
        }

    def transition(self, new_state: QuizState, *, force: bool = False) -> bool:
        with self._lock:
            if not force:
                allowed = self.allowed.get(self.state, set())
                if new_state not in allowed:
                    return False

            prev_state = self.state
            self.state = new_state
            self.last_change = time.time()

            if new_state == QuizState.RUNNING and prev_state != QuizState.PAUSED:
                self.start_time = time.time()

            return True


    def set_state(self, new_state, *, force: bool = False) -> bool:
        """Backward-compatible setter used by legacy callers."""
        if isinstance(new_state, str):
            try:
                new_state = QuizState[new_state.upper()]
            except KeyError:
                return False
        if not isinstance(new_state, QuizState):
            return False
        return self.transition(new_state, force=force)

    def can_transition(self, new_state: QuizState) -> bool:
        with self._lock:
            return new_state in self.allowed.get(self.state, set())

    def set_question_progress(self, question_number: int, total_questions: int):
        with self._lock:
            self.question_number = question_number
            self.total_questions = total_questions
            self._update_phase()

    def _update_phase(self):
        if self.total_questions <= 1 or self.question_number <= 0:
            self.phase = QuizPhase.GET_READY
            return

        if self.question_number == 1:
            self.phase = QuizPhase.FIRST_QUESTION
            return

        if self.question_number == self.total_questions:
            self.phase = QuizPhase.FINAL_QUESTION
            return

        pct = (self.question_number - 1) / (self.total_questions - 1) * 100
        if pct <= 33:
            self.phase = QuizPhase.EARLY_GAME
        elif pct <= 66:
            self.phase = QuizPhase.MID_GAME
        else:
            self.phase = QuizPhase.LATE_GAME

    def get_elapsed_time(self) -> float:
        if not self.start_time:
            return 0.0
        return time.time() - self.start_time

    def get_time_in_state(self) -> float:
        if not self.last_change:
            return 0.0
        return time.time() - self.last_change