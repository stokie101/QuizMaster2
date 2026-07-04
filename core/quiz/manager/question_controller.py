import logging


class QuestionController:
    def __init__(self, csv_handler, config_manager):
        self.logger = logging.getLogger("QuestionController")
        self.csv = csv_handler
        self.config = config_manager

        self.current_question = None
        self.current_id = None
        self.question_number = 0
        self.total_questions = 0
        self.on_new_question = None
        self.on_question_changed = None
        self.on_question_number_changed = None
        self.on_show_answers = None
        self.on_question_finished = None
        self.on_quiz_completed = None

    def reset(self):
        self.logger.info("Resetting QuestionController...")
        self.current_question = None
        self.current_id = None
        self.question_number = 0
        self.logger.info("✅ QuestionController reset complete")

    def load_quiz_data(self, quiz_list):
        self.logger.info(f"Loading quiz data with {len(quiz_list)} questions")
        self.total_questions = len(quiz_list)
        if self.csv:
            self.csv.quiz_data = quiz_list
            if hasattr(self.csv, "reset_question_index"):
                self.csv.reset_question_index()
            if hasattr(self.csv, "current_question_index"):
                self.csv.current_question_index = -1
            if hasattr(self.csv, "current_index"):
                self.csv.current_index = -1

        self.question_number = 0
        self.current_question = None
        self.current_id = None

    def rewind(self):
        """Rewind quiz progression to allow a completed quiz to start again."""
        if self.csv and hasattr(self.csv, "reset_question_index"):
            self.csv.reset_question_index()
        if self.csv and hasattr(self.csv, "current_question_index"):
            self.csv.current_question_index = -1
        if self.csv and hasattr(self.csv, "current_index"):
            self.csv.current_index = -1
        self.question_number = 0
        self.current_question = None
        self.current_id = None

    def next_question(self):
        if not (self.csv and hasattr(self.csv, "get_next_question")):
            return False
        next_q = self.csv.get_next_question()
        if not next_q:
            return False

        self.question_number += 1
        self.current_question = next_q
        self.current_id = f"q{self.question_number}_{id(next_q)}"

        if self.on_question_number_changed:
            self.on_question_number_changed(self.question_number)
        if self.on_question_changed:
            self.on_question_changed(self.current_question)
        if self.on_new_question:
            self.on_new_question(self.current_question)
        return True

    def show_answers(self):
        if not self.current_question:
            return 0

        answers_map = self._correct_answer_dict(self.current_question)
        if self.on_show_answers:
            self.on_show_answers(answers_map)

        return self._get_answer_display_time()

    @staticmethod
    def _normalise_answer_text(value):
        text = str(value or "").strip().lower()
        if "," in text and len(text.split(",", 1)[0].strip()) == 1:
            text = text.split(",", 1)[1].strip().lower()
        return text

    @classmethod
    def _correct_answer_dict(cls, question):
        answers = question.get("answers", []) or []
        raw_correct_idx = question.get("correct_index", None)
        correct_idx = None
        if raw_correct_idx not in (None, ""):
            try:
                correct_idx = int(raw_correct_idx)
            except (TypeError, ValueError):
                correct_idx = None

        if correct_idx is None and question.get("correct_answer"):
            target = cls._normalise_answer_text(question.get("correct_answer"))
            for i, answer in enumerate(answers):
                if cls._normalise_answer_text(answer) == target:
                    correct_idx = i
                    break

        mapping = {}
        for i, answer in enumerate(answers):
            letter = answer.split(',', 1)[0].strip() if isinstance(answer, str) and ',' in answer else chr(65 + i)
            mapping[letter] = (correct_idx is not None and i == correct_idx)
        return mapping

    def _get_answer_display_time(self) -> int:
        try:
            if hasattr(self.config, "get_answer_display_time"):
                return self.config.get_answer_display_time()
        except Exception:
            pass
        return 3
