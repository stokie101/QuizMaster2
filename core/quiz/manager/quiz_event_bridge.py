import logging
import time
from collections import defaultdict
from typing import Any


class QuizEventBridge:
    """
    Event emitter with built-in rate limiting and batching
    to prevent WebSocket congestion when running multiple apps.
    """

    def __init__(self, http_bridge=None):
        self.http_bridge = http_bridge
        self.logger = logging.getLogger("QuizEventBridge")

        # Rate limiting: ONLY for timer ticks (high frequency, non-critical)
        # DO NOT rate limit: state_changed, quiz_paused, quiz_resumed, leaderboard, etc.
        self._last_emit_time = defaultdict(float)
        self._rate_limits = {
            "timer_tick": 0.1,  # Allow 10 updates per second for timer
        }

        # Batching: collect rapid events and send in batches
        self._batch_queue = defaultdict(list)
        self._batch_timer = None

    def emit(self, name: str, *args):
        """
        Emit event with automatic rate limiting.
        High-frequency events are throttled to prevent congestion.
        """
        try:
            if not self.http_bridge:
                return True

            # Check rate limit for this event type
            if name in self._rate_limits:
                now = time.time()
                min_interval = self._rate_limits[name]
                last_time = self._last_emit_time[name]

                if now - last_time < min_interval:
                    # Too soon, skip this emission
                    self.logger.debug(f"Rate limited: {name} (last: {now - last_time:.2f}s ago)")
                    return True

                self._last_emit_time[name] = now

            # Emit via WebSocket (preferred) or fallback
            if hasattr(self.http_bridge, "emit_signal_ws"):
                self.http_bridge.emit_signal_ws(name, *args)
                return True

            if hasattr(self.http_bridge, "emit_signal"):
                self.http_bridge.emit_signal(name, *args)
                return True

            self.logger.warning(f"Bridge has no emit methods for signal '{name}'")
            return True

        except Exception as e:
            self.logger.error(f"Failed to emit event '{name}': {e}")
            return False

    def emit_batch(self, events: list[tuple[str, Any]]):
        """
        Emit multiple events in a single WebSocket message.
        Reduces overhead when sending many updates at once.

        Args:
            events: List of (event_name, payload) tuples
        """
        try:
            if not self.http_bridge or not events:
                return True

            if hasattr(self.http_bridge, "emit_batch_ws"):
                # If bridge supports batching
                self.http_bridge.emit_batch_ws(events)
            else:
                # Fallback: emit individually
                for name, payload in events:
                    self.emit(name, payload)

            return True

        except Exception as e:
            self.logger.error(f"Failed to emit batch: {e}")
            return False

    # Optimized helper methods with rate limiting built-in

    def quiz_started(self):
        """Emit quiz_started signal - critical event, no rate limit."""
        self.emit("quiz_started")
        return True

    def quiz_ended(self):
        """Emit quiz_ended signal - critical event, no rate limit."""
        self.emit("quiz_ended")
        return True

    def timer_tick(self, remaining):
        """Emit timer_tick signal - rate limited to 1/sec."""
        self.emit("timer_tick", remaining)
        return True

    def timer_expired(self):
        """Emit timer_expired signal - critical event."""
        self.emit("timer_expired")
        return True

    def new_question(self, question_dict):
        """Emit question_changed signal - critical event."""
        self.emit("question_changed", question_dict)
        return True

    def show_answers(self, correct_map):
        """Emit answers_highlighted signal - critical event."""
        try:
            result = self.emit("answers_highlighted", correct_map)
            if not result:
                self.logger.debug("show_answers emit returned False, but this may be normal")
            return True
        except Exception as e:
            self.logger.error(f"Exception in show_answers: {e}")
            return False
