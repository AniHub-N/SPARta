from decimal import Decimal
import pytest

from jaw_ingest.utils import normalize_currency, normalize_number, parse_date, NormalizationError


def test_normalize_number_basic_integer():
    assert normalize_number("12345") == Decimal("12345")


def test_normalize_number_indian_currency_formats():
    assert normalize_currency("INR 33.38 Cr") == 333800000
    assert normalize_currency("3,338.00 Lakh") == 333800000
    assert normalize_currency("33,38,00,000") == 333800000
    assert normalize_currency("₹12.5 M") == 12500000


def test_normalize_number_invalid_format():
    with pytest.raises(NormalizationError):
        normalize_number("12.34.56")


def test_parse_date_various_formats():
    assert parse_date("10-03-2021").isoformat() == "2021-03-10"
    assert parse_date("10/03/2021").isoformat() == "2021-03-10"
    assert parse_date("March 10, 2021").isoformat() == "2021-03-10"
    assert parse_date("10 Mar 2021").isoformat() == "2021-03-10"


def test_parse_date_invalid():
    with pytest.raises(NormalizationError):
        parse_date("invalid-date")
