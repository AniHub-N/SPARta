from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, validator


class NormalizationError(ValueError):
    pass


def normalize_number(value: str) -> Decimal:
    text = str(value).strip()
    if not text:
        raise NormalizationError("Empty value cannot be normalized.")

    cleaned = text.replace("\u00A0", " ").replace("\u202F", " ").strip()
    cleaned = cleaned.replace(" Rs.", "").replace(" Rs", "").replace("₹", "").replace("INR", "").replace("Rs", "")
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"\s+", " ", cleaned)

    if cleaned.endswith("Cr") or cleaned.endswith("Cr."):
        value_text = cleaned.replace("Cr", "").replace("Cr.", "").strip()
        return Decimal(value_text) * Decimal(10_000_000)

    if cleaned.endswith("Lakh") or cleaned.endswith("Lac") or cleaned.endswith("Lacs"):
        value_text = re.sub(r"(Lakh|Lac|Lacs)\.?$", "", cleaned).strip()
        return Decimal(value_text) * Decimal(100_000)

    if cleaned.endswith("M") and re.search(r"\d+\.?\d*\s*M$", cleaned):
        value_text = cleaned[:-1].strip()
        return Decimal(value_text) * Decimal(1_000_000)

    if cleaned.endswith("K") and re.search(r"\d+\.?\d*\s*K$", cleaned):
        value_text = cleaned[:-1].strip()
        return Decimal(value_text) * Decimal(1_000)

    if cleaned.count(".") > 1 and "." in cleaned:
        raise NormalizationError(f"Ambiguous numeric format: {value}")

    try:
        return Decimal(cleaned)
    except ArithmeticError as exc:
        raise NormalizationError(f"Unable to parse numeric value: {value}") from exc


def normalize_currency(value: str) -> int:
    amount = normalize_number(value)
    return int(amount.quantize(Decimal("1")))


def parse_date(value: str) -> datetime.date:
    text = str(value).strip()
    if not text:
        raise NormalizationError("Empty date cannot be parsed.")

    patterns = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B, %Y",
        "%d %b, %Y",
        "%B %Y",
        "%b %Y",
    ]

    normalized = text.replace("/", "/").replace("-", "-").replace(".", ".").strip()
    normalized = re.sub(r"\s+", " ", normalized)

    for pattern in patterns:
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            continue

    month_map = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }

    match = re.search(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})", normalized)
    if match:
        day = int(match.group("day"))
        month_name = match.group("month").lower()
        year = int(match.group("year"))
        month = month_map.get(month_name[:3])
        if month:
            return datetime(year, month, day).date()

    raise NormalizationError(f"Unable to parse date: {value}")


from pydantic import BaseModel, field_validator


class Coordinate(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @field_validator("x0", "y0", "x1", "y1")
    def non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Coordinate values must be non-negative")
        return value
