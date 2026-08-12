from __future__ import annotations

from typing import Any

from .normalization import NormalizationError, infer_normalization

ANSWER_TYPES = ("money", "count", "percent", "days")


def coerce_numeric_answer(answer: Any, answer_type: str) -> float | None:
    """Coerces a FinalAnswer.answer value into the plain number the submission format
    requires (README: "a plain number. No commas, no currency symbols, no units, no
    text"). Reuses the same deterministic normalization as everywhere else in the
    pipeline rather than a bespoke parser, so an LLM-transcribed money string is read
    the same way facts.jsonl already reads it.

    Returns None when no reasonable numeric value can be recovered - the caller decides
    how to handle that (e.g. write a placeholder and flag it), rather than this
    function silently guessing.
    """
    if isinstance(answer, bool):
        return float(answer)
    if isinstance(answer, (int, float)):
        return float(answer)

    if isinstance(answer, list):
        if answer_type == "count":
            return float(len(answer))
        if len(answer) == 1:
            return coerce_numeric_answer(answer[0], answer_type)
        return None

    if isinstance(answer, dict):
        if answer_type == "count" and "count" in answer:
            return coerce_numeric_answer(answer["count"], answer_type)
        for key in ("value", "result", "total"):
            if key in answer:
                return coerce_numeric_answer(answer[key], answer_type)
        return None

    if isinstance(answer, str):
        cleaned = answer.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned.replace(",", ""))
        except ValueError:
            pass
        try:
            result = infer_normalization(cleaned)
        except NormalizationError:
            return None
        if result.normalized_value is None:
            return None
        try:
            return float(result.normalized_value)
        except (TypeError, ValueError):
            return None

    return None


def format_submission_value(value: float, answer_type: str) -> str:
    """Formats a coerced numeric answer as the CSV expects: a plain number, no commas/
    symbols/units. Percent is always shown to 2 decimal places (README: "Round
    percentages to two places"); other types are written as a whole number when they
    land on one ("5", not "5.0", for a count), otherwise a clean decimal.
    """
    if answer_type == "percent":
        return f"{round(value, 2):.2f}"
    rounded = round(value, 6)
    if float(rounded).is_integer():
        return str(int(rounded))
    return f"{rounded:g}"
