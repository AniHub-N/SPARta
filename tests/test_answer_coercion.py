from __future__ import annotations

import pytest

from jaw_ingest.answer_coercion import coerce_numeric_answer, format_submission_value


@pytest.mark.parametrize(
    "answer,answer_type,expected",
    [
        (2942400000, "money", 2942400000.0),
        (90.19, "percent", 90.19),
        ("2942400000", "money", 2942400000.0),
        ("INR 33.38 Cr", "money", 333800000.0),
        ("3,338.00 Lakh", "money", 333800000.0),
        ("33,38,00,000", "money", 333800000.0),
        ("90.19%", "percent", 90.19),
        (True, "count", 1.0),
        ([1, 2, 3], "count", 3.0),
        ([42], "money", 42.0),
        ({"result": 5}, "money", 5.0),
        ({"value": 5}, "money", 5.0),
        ({"total": 5}, "money", 5.0),
        ({"count": 7}, "count", 7.0),
    ],
)
def test_coerce_numeric_answer_success_cases(answer, answer_type, expected) -> None:
    assert coerce_numeric_answer(answer, answer_type) == expected


@pytest.mark.parametrize(
    "answer,answer_type",
    [
        (None, "money"),
        ("", "money"),
        ("not a number at all", "money"),
        ([1, 2, 3], "money"),  # ambiguous list for a non-count type
        ({}, "money"),
        ({"unrelated_key": 5}, "money"),
    ],
)
def test_coerce_numeric_answer_returns_none_when_unrecoverable(answer, answer_type) -> None:
    assert coerce_numeric_answer(answer, answer_type) is None


def test_format_submission_value_whole_numbers_have_no_decimal() -> None:
    assert format_submission_value(2942400000.0, "money") == "2942400000"
    assert format_submission_value(5.0, "count") == "5"


def test_format_submission_value_percent_always_two_decimals() -> None:
    assert format_submission_value(90.19, "percent") == "90.19"
    assert format_submission_value(33.333333, "percent") == "33.33"
    assert format_submission_value(50.0, "percent") == "50.00"


def test_format_submission_value_no_floating_point_noise() -> None:
    # 0.1 + 0.2 style artifacts must not leak into the submitted value.
    value = 100000000.0 / 3 * 3  # a value prone to float noise
    formatted = format_submission_value(value, "money")
    assert formatted == "100000000"
