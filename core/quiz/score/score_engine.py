import logging
from typing import Dict, Any


class ScoreEngine:
    """
    Quiz scoring engine.
    - Multiple choice / true-false / picture: accepts A/B/C/D letters or the visible answer text.
    - Short answer / fill blank: ignores spaces + case.
    - Matching: exact dict equality.
    - Ordering: exact list equality.
    - Never mutates question data.
    """

    logger = logging.getLogger("ScoreEngine")

    def __init__(self):
        self._matching_map_cache = {}
        self._ordering_cache = {}

    def check_answer(self, user_answer: str, question: Dict[str, Any]) -> bool:
        """Validate a user's answer."""
        if not isinstance(user_answer, str) or not user_answer.strip():
            return False

        qtype = question.get("question_type")

        if qtype in ["multiple_choice", "true_false", "picture"]:
            return self._check_choice_answer(user_answer, question)

        if qtype in ["fill_in_blank", "short_answer"]:
            return self._check_text_answer(user_answer, question)

        if qtype == "matching":
            return self._check_matching_answer(user_answer, question)

        if qtype == "ordering":
            return self._check_ordering_answer(user_answer, question)

        return False

    @staticmethod
    def _normalise_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        if "," in text and len(text.split(",", 1)[0].strip()) == 1:
            text = text.split(",", 1)[1].strip().lower()
        text = text.replace(".", "")
        return " ".join(text.split())

    @staticmethod
    def _normalise_letter(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        # Accept common chat forms: "a", "A", "a.", "A)", "answer a".
        text = text.replace("answer", "").strip()
        while text and not text[0].isalnum():
            text = text[1:].strip()
        if len(text) >= 1 and text[0].isalpha():
            return text[0]
        return ""

    @classmethod
    def _check_choice_answer(cls, user_answer: str, question: Dict[str, Any]) -> bool:
        answers = question.get("answers", []) or []
        raw_correct_index = question.get("correct_index", None)
        correct_index = None
        if raw_correct_index not in (None, ""):
            try:
                correct_index = int(raw_correct_index)
            except (TypeError, ValueError):
                correct_index = None

        if correct_index is None or correct_index < 0 or correct_index >= len(answers):
            cls.logger.debug(
                "No valid correct_index for answer check: correct_index=%r answers=%s question=%r",
                raw_correct_index,
                len(answers),
                question.get("question"),
            )
            return False

        user_letter = cls._normalise_letter(user_answer)
        if user_letter in {"a", "b", "c", "d"}:
            user_index = ord(user_letter) - 97
            if user_index == correct_index:
                return True

        # Also accept the exact visible answer text. This is required for TikTok chat,
        # where viewers often type the answer itself instead of A/B/C/D.
        user_text = cls._normalise_text(user_answer)
        correct_text = cls._normalise_text(answers[correct_index])
        if user_text and correct_text and user_text == correct_text:
            return True

        # True/false convenience aliases.
        if len(answers) == 2:
            answer_texts = [cls._normalise_text(a) for a in answers]
            if answer_texts == ["true", "false"]:
                aliases = {"t": "true", "y": "true", "yes": "true", "1": "true", "f": "false", "n": "false", "no": "false", "0": "false"}
                mapped = aliases.get(user_text, user_text)
                return mapped == answer_texts[correct_index]

        cls.logger.debug(
            "Incorrect choice answer: user_answer=%r normalised=%r correct_index=%s correct_text=%r",
            user_answer,
            user_text,
            correct_index,
            correct_text,
        )
        return False

    @staticmethod
    def _normalize_string(s: str) -> str:
        """Convert to lowercase and remove ALL spaces."""
        return "".join(str(s or "").lower().split())

    def _check_text_answer(self, user_answer: str, question: Dict[str, Any]) -> bool:
        ua = self._normalize_string(user_answer)
        ca = self._normalize_string(question.get("correct_answer", ""))
        if not ua or not ca:
            return False
        return ua == ca

    def _check_matching_answer(self, user_answer: str, question: Dict[str, Any]) -> bool:
        import json
        try:
            if isinstance(user_answer, str):
                user_map = json.loads(user_answer)
            else:
                user_map = user_answer
        except Exception:
            return False

        cache = getattr(self, "_matching_map_cache", None)
        qkey = id(question)
        if cache is not None and qkey in cache:
            correct_map = cache[qkey]
        else:
            correct_map = dict(zip(question.get("items", []), question.get("matches", [])))
            if cache is not None:
                cache[qkey] = correct_map
        return user_map == correct_map

    def _check_ordering_answer(self, user_answer: str, question: Dict[str, Any]) -> bool:
        import json
        try:
            if isinstance(user_answer, str):
                user_list = json.loads(user_answer)
            else:
                user_list = user_answer
        except Exception:
            return False

        cache = getattr(self, "_ordering_cache", None)
        qkey = id(question)
        if cache is not None and qkey in cache:
            correct_order = cache[qkey]
        else:
            correct_order = question.get("correct_order", [])
            if cache is not None:
                cache[qkey] = correct_order
        return user_list == correct_order
