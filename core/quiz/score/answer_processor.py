import logging


class AnswerProcessor:
    def __init__(self, score_engine, config_manager, leaderboard_manager):
        self.score_engine = score_engine
        self.config = config_manager
        self.leaderboard = leaderboard_manager
        self.answered_users = set()
        self.correct_users = set()
        self.incorrect_counts = {}
        self.fastest_finger_winners = set()
        self.answer_counts = []
        self.answer_total = 0
        self.on_correct = None
        self.on_incorrect = None
        self.on_fastest_finger = None
        self.on_answer_processed = None
        self.on_votes_updated = None
        self.get_time_remaining = lambda: 0
        self.current_question = None
        self.current_question_id = None
        self.logger = logging.getLogger("AnswerProcessor")

    def reset(self):
        """Reset all answer processor state for a new quiz."""
        self.answered_users.clear()
        self.correct_users.clear()
        self.incorrect_counts.clear()
        self.fastest_finger_winners.clear()
        self.answer_counts = []
        self.answer_total = 0
        self.current_question = None
        self.current_question_id = None

    def start_question(self, question_dict, question_id):
        """Reset tracking for a new question."""
        self.current_question = question_dict
        self.current_question_id = question_id
        self.answered_users.clear()
        self.correct_users.clear()
        self.incorrect_counts.clear()
        answers = question_dict.get("answers", []) if isinstance(question_dict, dict) else []
        self.answer_counts = [0 for _ in answers]
        self.answer_total = 0
        if isinstance(question_dict, dict):
            question_dict["answer_counts"] = list(self.answer_counts)
            question_dict["votes"] = [0 for _ in self.answer_counts]
            question_dict["vote_percentages"] = [0 for _ in self.answer_counts]

    def process(self, user_id, answer, display_name=None):
        """Process a user's answer. Returns True if accepted for this question."""
        if not self.current_question:
            self.logger.warning(f"❌ No current question - cannot process answer from {user_id}")
            return False

        if user_id in self.answered_users:
            self.logger.info(f"Ignored duplicate answer user_id={user_id} answer={answer!r} question_id={self.current_question_id}")
            return False

        self.answered_users.add(user_id)
        self.logger.info(f"📥 Processing answer user_id={user_id} answer={answer!r} question_id={self.current_question_id}")

        self._record_vote(answer)
        is_correct = self.score_engine.check_answer(answer, self.current_question)

        if is_correct:
            self._handle_correct(user_id, answer, display_name)
        else:
            self._handle_incorrect(user_id, answer, display_name)

        if self.on_votes_updated:
            try:
                self.on_votes_updated(self.current_question)
            except Exception as e:
                self.logger.error(f"Error in votes_updated callback: {e}")
        if self.on_answer_processed:
            try:
                self.on_answer_processed()
            except Exception as e:
                self.logger.error(f"Error in answer_processed callback: {e}")
        return True

    @staticmethod
    def _normalise_answer_text(value):
        text = str(value or "").strip().lower()
        if "," in text and len(text.split(",", 1)[0].strip()) == 1:
            text = text.split(",", 1)[1].strip().lower()
        text = text.replace(".", "")
        return " ".join(text.split())

    @staticmethod
    def _normalise_answer_letter(value):
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = text.replace("answer", "").strip()
        while text and not text[0].isalnum():
            text = text[1:].strip()
        return text[0] if text and text[0].isalpha() else ""

    def _answer_index_for(self, answer):
        answers = self.current_question.get("answers", []) or []
        letter = self._normalise_answer_letter(answer)
        if letter in {"a", "b", "c", "d"}:
            idx = ord(letter) - 97
            if 0 <= idx < len(answers):
                return idx

        target = self._normalise_answer_text(answer)
        if target:
            for idx, option in enumerate(answers):
                if self._normalise_answer_text(option) == target:
                    return idx

        option_texts = [self._normalise_answer_text(a) for a in answers]
        aliases = {"t": "true", "y": "true", "yes": "true", "1": "true", "f": "false", "n": "false", "no": "false", "0": "false"}
        mapped = aliases.get(target, target)
        if mapped in option_texts:
            return option_texts.index(mapped)
        return None

    def _record_vote(self, answer):
        if not isinstance(self.current_question, dict):
            return
        answers = self.current_question.get("answers", []) or []
        if len(self.answer_counts) != len(answers):
            self.answer_counts = [0 for _ in answers]
            self.answer_total = 0

        idx = self._answer_index_for(answer)
        if idx is None:
            self.logger.debug(f"Vote ignored for percentages; answer did not map to option: {answer!r}")
            return

        self.answer_counts[idx] += 1
        self.answer_total += 1
        if self.answer_total > 0:
            raw = [(count / self.answer_total) * 100 for count in self.answer_counts]
            votes = [int(round(value)) for value in raw]
            delta = 100 - sum(votes)
            if votes and delta:
                max_idx = max(range(len(votes)), key=lambda i: self.answer_counts[i])
                votes[max_idx] += delta
        else:
            votes = [0 for _ in self.answer_counts]

        self.current_question["answer_counts"] = list(self.answer_counts)
        self.current_question["total_answers"] = self.answer_total
        self.current_question["votes"] = list(votes)
        self.current_question["vote_percentages"] = list(votes)
        self.logger.info(
            "Updated live vote percentages question_id=%s counts=%s votes=%s total=%s",
            self.current_question_id,
            self.answer_counts,
            votes,
            self.answer_total,
        )

    def _handle_correct(self, user_id, answer, display_name):
        self.correct_users.add(user_id)
        base_points = self._calculate_points(user_id)
        bonus = 0
        qualifies_for_fastest_finger = self._is_fastest_finger(user_id)
        if qualifies_for_fastest_finger:
            bonus = self._award_fastest_finger(user_id)
        total_points = base_points + bonus

        self.logger.info(
            "✅ Correct answer user_id=%s base_points=%s fastest_bonus=%s total_points=%s time_remaining=%.3f question_id=%s",
            user_id,
            base_points,
            bonus,
            total_points,
            self._safe_time_remaining(),
            self.current_question_id,
        )

        if self.leaderboard:
            self.leaderboard.record_correct_answer(
                user_id,
                self.current_question_id,
                total_points,
                display_name or user_id
            )
            self.logger.debug(f"📊 Leaderboard updated for {user_id}")

        if bonus and self.on_fastest_finger:
            try:
                self.on_fastest_finger(user_id, bonus)
            except Exception as e:
                self.logger.error(f"Error in on_fastest_finger callback: {e}")

        if self.on_correct:
            try:
                self.on_correct(user_id, answer, total_points)
            except Exception as e:
                self.logger.error(f"Error in on_correct callback: {e}")

    def _handle_incorrect(self, user_id, answer, display_name):
        self.incorrect_counts[user_id] = self.incorrect_counts.get(user_id, 0) + 1
        self.logger.info(f"❌ Incorrect answer user_id={user_id} answer={answer!r} question_id={self.current_question_id}")

        if self.leaderboard:
            self.leaderboard.record_incorrect_answer(
                user_id,
                self.current_question_id,
                display_name or user_id
            )

        if self.on_incorrect:
            try:
                self.on_incorrect(user_id, answer)
            except Exception as e:
                self.logger.error(f"Error in on_incorrect callback: {e}")

    def _safe_time_remaining(self):
        try:
            return float(self.get_time_remaining() or 0)
        except Exception:
            return 0.0

    def _calculate_points(self, user_id):
        """Calculate base points from current settings and precise remaining time."""
        time_left = self._safe_time_remaining()
        if not self.config:
            self.logger.warning("⚠️ No config manager - using default 10 points")
            return 10

        try:
            if hasattr(self.config, "calculate_points_based_on_time"):
                points = int(self.config.calculate_points_based_on_time(time_left))
                self.logger.info("⏱️ ConfigManager scoring time_left=%.3f points=%s", time_left, points)
                return points
        except Exception as e:
            self.logger.error(f"ConfigManager scoring failed, using local fallback: {e}")

        try:
            enabled = bool(self.config.get_bool("POINTS", "timer_responsive_points_enabled", False))
            max_points = int(self.config.get_int("POINTS", "max_points", 10))
            min_points = int(self.config.get_int("POINTS", "min_points", 5))
            duration = int(self.config.get_int("TIMER", "duration", 30))
            if max_points < min_points:
                max_points, min_points = min_points, max_points
            if not enabled:
                self.logger.info("📊 Fixed scoring enabled=false max_points=%s", max_points)
                return max_points
            if duration <= 0:
                duration = 30
            ratio = max(0.0, min(1.0, float(time_left) / float(duration)))
            points = int(round(min_points + ((max_points - min_points) * ratio)))
            points = max(min_points, min(max_points, points))
            self.logger.info(
                "⏱️ Time scoring enabled=%s time_left=%.3f duration=%s min=%s max=%s ratio=%.3f points=%s",
                enabled, time_left, duration, min_points, max_points, ratio, points,
            )
            return points
        except Exception as e:
            self.logger.error(f"Failed to calculate points: {e}")
            return 10

    def _is_fastest_finger(self, user_id):
        if not self.config:
            return False

        try:
            if hasattr(self.config, "get_fastest_finger_enabled"):
                ff_enabled = bool(self.config.get_fastest_finger_enabled())
            else:
                ff_enabled = bool(self.config.get_bool("POINTS", "fastest_finger_enabled", False))
            if not ff_enabled:
                return False
        except Exception as e:
            self.logger.error(f"Failed to check fastest finger enabled: {e}")
            return False

        if user_id in self.fastest_finger_winners:
            self.logger.debug(f"User {user_id} already won Fastest Finger earlier")
            return False

        try:
            if hasattr(self.config, "get_fastest_finger_threshold_seconds"):
                window_seconds = float(self.config.get_fastest_finger_threshold_seconds())
            else:
                window_seconds = float(self.config.get_int("POINTS", "fastest_finger_threshold_seconds", 5))
        except Exception as e:
            self.logger.error(f"Failed to read fastest finger window: {e}")
            window_seconds = 5.0

        try:
            elapsed = self._get_question_elapsed_seconds()
        except Exception as e:
            self.logger.error(f"Failed to calculate fastest finger elapsed time: {e}")
            return False

        qualifies = elapsed is not None and elapsed >= 0 and elapsed <= window_seconds
        self.logger.info(
            "Fastest finger check user_id=%s elapsed=%.3f threshold=%.3f qualifies=%s already_won=%s",
            user_id,
            elapsed if elapsed is not None else -1,
            window_seconds,
            qualifies,
            user_id in self.fastest_finger_winners,
        )
        if qualifies:
            self.fastest_finger_winners.add(user_id)
        return qualifies

    def _get_question_elapsed_seconds(self):
        try:
            if hasattr(self.config, "get_timer_duration"):
                duration = float(self.config.get_timer_duration())
            else:
                duration = float(self.config.get_int("TIMER", "duration", 30))
            remaining = self._safe_time_remaining()
            return max(0.0, duration - remaining)
        except Exception:
            return None

    def _award_fastest_finger(self, user_id):
        try:
            if hasattr(self.config, "get_fastest_finger_bonus"):
                return int(self.config.get_fastest_finger_bonus())
            return int(self.config.get_int("POINTS", "fastest_finger_bonus", 5))
        except Exception as e:
            self.logger.error(f"Failed to award fastest finger: {e}")
            return 0
