from decimal import Decimal

import pytest

from jaw_ingest.normalization import (
    NormalizationError,
    infer_normalization,
    normalize_date,
    normalize_money,
    normalize_percentage,
    normalize_unit,
)


def test_normalize_money_variants() -> None:
    assert normalize_money("INR 33.38 Cr").normalized_value == Decimal("333800000")
    assert normalize_money("Rs. 33.38 Cr").normalized_value == Decimal("333800000")
    assert normalize_money("₹33.38 Cr").normalized_value == Decimal("333800000")
    assert normalize_money("33.38 Crore").normalized_value == Decimal("333800000")
    assert normalize_money("3,338.00 Lakh").normalized_value == Decimal("333800000")
    assert normalize_money("333800000").normalized_value == Decimal("333800000")
    assert normalize_money("33,38,00,000").normalized_value == Decimal("333800000")


def test_normalize_percentage() -> None:
    assert normalize_percentage("33.33%").normalized_value == Decimal("33.33")
    assert normalize_percentage("33.33 %").normalized_value == Decimal("33.33")
    assert normalize_percentage("33.33 percent").normalized_value == Decimal("33.33")


def test_normalize_date_formats() -> None:
    assert normalize_date("10-03-2021").normalized_value == "2021-03-10"
    assert normalize_date("2021-03-10").normalized_value == "2021-03-10"
    assert normalize_date("10 March 2021").normalized_value == "2021-03-10"


def test_normalize_unknown_text_raises() -> None:
    with pytest.raises(NormalizationError):
        infer_normalization("not a number")


def test_normalize_units() -> None:
    assert normalize_unit("sqm").normalized_unit == "sqm"
    assert normalize_unit("sq m").normalized_unit == "sqm"
    assert normalize_unit("Crore").normalized_unit == "crore"
