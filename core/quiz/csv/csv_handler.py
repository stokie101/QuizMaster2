import csv
import copy
import gc
import logging
import os
import threading
from enum import Enum
from typing import List, Dict, Any, Optional

from PySide6.QtCore import QThread, Signal


class QuestionType(Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    PICTURE = "picture"
    FILL_IN_BLANK = "fill_in_blank"
    SHORT_ANSWER = "short_answer"
    MATCHING = "matching"
    ORDERING = "ordering"


class CSVLoaderThread(QThread):
    finished = Signal(tuple)  # (success, message, file_path)
    progress = Signal(int, int)

    def __init__(self, file_path, csv_handler):
        super().__init__()
        self.file_path = file_path
        self.csv_handler = csv_handler

    def run(self):
        try:
            success, msg = self.csv_handler.load_file(self.file_path, self.progress)
            gc.collect()
            self.finished.emit((success, msg, self.file_path))
        except Exception as e:
            self.finished.emit((False, str(e), self.file_path))


class CSVHandler:
    """Thread-safe CSV quiz file loader with bulletproof index management."""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = CSVHandler()
        return cls._instance

    def __init__(self):
        self.logger = logging.getLogger("CSVHandler")
        self._lock = threading.RLock()
        self.quiz_data: List[Dict[str, Any]] = []
        self.current_index = -1
        self.shuffle_questions = False
        self.shuffle_answers = False
        self._is_loading = False

    def load_file(self, file_path: str, progress_signal=None):
        with self._lock:
            if self._is_loading:
                self.logger.warning("Load already in progress")
                return False, "Load already in progress"
            self._is_loading = True

        try:
            self.logger.info(f"Loading CSV: {file_path}")
            if not os.path.exists(file_path):
                return False, f"File not found: {file_path}"
            if not file_path.lower().endswith('.csv'):
                return False, "File must be a CSV"

            with self._lock:
                self.quiz_data = []
                self.current_index = -1
            gc.collect()

            try:
                with open(file_path, encoding="utf-8") as f:
                    total_rows = sum(1 for _ in f) - 1
            except Exception as e:
                return False, f"Error reading file: {e}"

            if total_rows < 1:
                return False, "CSV contains no questions"
            if progress_signal:
                progress_signal.emit(0, total_rows)

            questions = []
            base_dir = os.path.dirname(file_path)
            with open(file_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for idx, row in enumerate(reader, start=1):
                    try:
                        q = self._process_row(row, base_dir)
                        if q:
                            questions.append(q)
                        else:
                            self.logger.warning(f"Skipped invalid row {idx}")
                    except Exception as e:
                        self.logger.error(f"Error processing row {idx}: {e}")
                    if progress_signal:
                        progress_signal.emit(idx, total_rows)

            if not questions:
                return False, "No valid questions found in CSV"

            if self.shuffle_questions:
                import random
                random.shuffle(questions)
                self.logger.info("Questions shuffled")

            with self._lock:
                self.quiz_data = questions
                self.current_index = -1

            self.logger.info(f"✅ Loaded {len(questions)} questions successfully")
            return True, None
        except Exception as e:
            self.logger.error(f"Error loading CSV: {e}", exc_info=True)
            return False, str(e)
        finally:
            with self._lock:
                self._is_loading = False

    def get_next_question(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            total_questions = len(self.quiz_data)
            self.current_index += 1
            if self.current_index >= total_questions:
                self.logger.debug(f"No more questions (index {self.current_index}/{total_questions})")
                return None
            if self.current_index < 0 or self.current_index >= total_questions:
                self.logger.error(f"Invalid index: {self.current_index}")
                return None
            try:
                question = self.quiz_data[self.current_index]
                self.logger.debug(f"Returning question {self.current_index + 1}/{total_questions}")
                return copy.deepcopy(question)
            except Exception as e:
                self.logger.error(f"Error getting question at index {self.current_index}: {e}")
                return None

    def reset_question_index(self):
        with self._lock:
            old_index = self.current_index
            self.current_index = -1
            self.logger.debug(f"Index reset: {old_index} → -1")

    def get_question_count(self):
        with self._lock:
            return len(self.quiz_data)

    def get_current_index(self):
        with self._lock:
            return self.current_index

    def clear(self):
        with self._lock:
            self.quiz_data = []
            self.current_index = -1
            self.logger.info("Quiz data cleared")

    def _process_row(self, row: Dict[str, str], base_dir: str):
        qtext = row.get("question", "").strip()
        if not qtext:
            return None

        qtype = row.get("type", "multiple_choice").strip().lower()
        difficulty = row.get("difficulty", "").lower() or "medium"
        category = row.get("category", "").strip() or "general"

        if qtype in ["true_false", "true/false", "tf"]:
            return self._make_tf(qtext, row, difficulty, category)
        if qtype in ["picture", "image"]:
            return self._make_picture(qtext, row, difficulty, category, base_dir)
        if qtype in ["fill_in_blank", "blank"]:
            return self._make_fill_blank(qtext, row, difficulty, category)
        if qtype in ["short_answer", "short"]:
            return self._make_short_answer(qtext, row, difficulty, category)
        if qtype in ["matching", "match"]:
            return self._make_matching(qtext, row, difficulty, category)
        if qtype in ["ordering", "order"]:
            return self._make_ordering(qtext, row, difficulty, category)
        return self._make_multiple_choice(qtext, row, difficulty, category)

    @staticmethod
    def _normalise_answer_value(value: str) -> str:
        text = str(value or "").strip().lower()
        if "," in text and len(text.split(",", 1)[0].strip()) == 1:
            text = text.split(",", 1)[1].strip().lower()
        return " ".join(text.replace(".", "").split())

    def _make_multiple_choice(self, qtext, row, difficulty, category):
        raw_correct = (
            row.get("correct_answer")
            or row.get("Correct")
            or row.get("correct")
            or row.get("right_answer")
            or row.get("answer_correct")
            or row.get("correct_option")
            or row.get("Correct Answer")
            or row.get("ï»¿correct_answer")
            or ""
        ).strip()
        correct_value = raw_correct.lower()
        if correct_value and "," in correct_value:
            correct_value = correct_value.split(",", 1)[0].strip().lower()

        raw_answers = [
            (row.get("answer_a") or "").strip(),
            (row.get("answer_b") or "").strip(),
            (row.get("answer_c") or "").strip(),
            (row.get("answer_d") or "").strip(),
        ]

        answers = []
        index_map = []
        for i, ans in enumerate(raw_answers):
            if ans:
                letter = chr(65 + i)
                answers.append(f"{letter},{ans}")
                index_map.append(i)

        if not answers:
            self.logger.warning(f"No valid answers for question: {qtext[:60]}...")
            return None

        letter_map = {"a": 0, "b": 1, "c": 2, "d": 3}
        base_correct_index = letter_map.get(correct_value)

        if base_correct_index is None and raw_correct:
            target = self._normalise_answer_value(raw_correct)
            for original_idx, ans in enumerate(raw_answers):
                if ans and self._normalise_answer_value(ans) == target:
                    base_correct_index = original_idx
                    break

        correct_index = None
        if base_correct_index is not None and base_correct_index in index_map:
            correct_index = index_map.index(base_correct_index)
        else:
            self.logger.warning(f"Correct answer '{raw_correct}' not found for: {qtext[:60]}...")

        if self.shuffle_answers and correct_index is not None:
            import random
            combined = list(zip(index_map, answers))
            random.shuffle(combined)
            index_map = [orig for (orig, _) in combined]
            answers = [txt for (_, txt) in combined]
            correct_index = index_map.index(base_correct_index)

        return {
            "question_type": QuestionType.MULTIPLE_CHOICE.value,
            "question": qtext,
            "answers": answers,
            "correct_index": correct_index,
            "correct_answer": raw_correct,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_tf(qtext, row, difficulty, category):
        answers = ["True", "False"]
        raw_correct = row.get("correct_answer", "").strip()
        correct = raw_correct.lower()
        correct_index = 0 if correct in ["true", "t", "yes", "1"] else 1
        return {
            "question_type": QuestionType.TRUE_FALSE.value,
            "question": qtext,
            "answers": answers,
            "correct_index": correct_index,
            "correct_answer": raw_correct,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_picture(qtext, row, difficulty, category, base_dir):
        answers = [
            row.get("answer_a", "").strip(),
            row.get("answer_b", "").strip(),
            row.get("answer_c", "").strip(),
            row.get("answer_d", "").strip(),
        ]
        answers = [a for a in answers if a]
        if not answers:
            return None

        correct_letter = row.get("correct_answer", "").strip().lower()
        correct_index = {"a": 0, "b": 1, "c": 2, "d": 3}.get(correct_letter)
        if correct_index is None and correct_letter:
            target = CSVHandler._normalise_answer_value(correct_letter)
            for idx, answer in enumerate(answers):
                if CSVHandler._normalise_answer_value(answer) == target:
                    correct_index = idx
                    break

        img = row.get("image_path", "").strip()
        if img and not os.path.isabs(img):
            img = os.path.join(base_dir, img)

        return {
            "question_type": QuestionType.PICTURE.value,
            "question": qtext,
            "answers": answers,
            "correct_index": correct_index,
            "correct_answer": row.get("correct_answer", "").strip(),
            "image_path": img,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_fill_blank(qtext, row, difficulty, category):
        correct = row.get("correct_answer", "").strip()
        if not correct:
            return None
        return {
            "question_type": QuestionType.FILL_IN_BLANK.value,
            "question": qtext,
            "correct_answer": correct,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_short_answer(qtext, row, difficulty, category):
        correct = row.get("correct_answer", "").strip()
        if not correct:
            return None
        return {
            "question_type": QuestionType.SHORT_ANSWER.value,
            "question": qtext,
            "correct_answer": correct,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_matching(qtext, row, difficulty, category):
        items = []
        matches = []
        for i in range(1, 11):
            a = row.get(f"item_{i}", "").strip()
            b = row.get(f"match_{i}", "").strip()
            if a and b:
                items.append(a)
                matches.append(b)
        if not items:
            return None
        return {
            "question_type": QuestionType.MATCHING.value,
            "question": qtext,
            "items": items,
            "matches": matches,
            "difficulty": difficulty,
            "category": category,
        }

    @staticmethod
    def _make_ordering(qtext, row, difficulty, category):
        items = []
        for i in range(1, 11):
            a = row.get(f"item_{i}", "").strip()
            if a:
                items.append(a)
        if not items:
            return None
        return {
            "question_type": QuestionType.ORDERING.value,
            "question": qtext,
            "items": items,
            "correct_order": list(items),
            "difficulty": difficulty,
            "category": category,
        }
