from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field


class NormalizationError(ValueError):
    pass


class NormalizationResult(BaseModel):
    raw_value: Any
    normalized_value: Any | None = None
    normalized_type: str
    normalized_unit: str | None = None
    original_unit: str | None = None
    confidence: Decimal = Decimal("1.0")
    status: Literal["valid", "ambiguous", "failed"] = "valid"
    message: str | None = None


CURRENCY_PATTERNS = [
    r"^(?P<symbol>₹|Rs\.?|INR|Rupees?)\s*(?P<amount>.+)$",
    r"^(?P<amount>.+)\s*(?P<unit>Cr|Cr\.|Crore|Lakh|Lac|M|K)$",
    r"^(?P<amount>\d[\d,]*\.?\d*)$",
]

PERCENTAGE_PATTERN = r"^(?P<number>[\d.,]+)\s*(%|percent|percentage)?$"

DATE_PATTERNS = [
    r"^(?P<day>\d{1,2})[-/](?P<month>\d{1,2})[-/](?P<year>\d{4})$",
    r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})$",
    r"^(?P<day>\d{1,2})\s+(?P<month_name>[A-Za-z]+)\s+(?P<year>\d{4})$",
    r"^(?P<month_name>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})$",
]

UNIT_PATTERNS = {
    "sqm": [r"^sq\s*m$", r"^sqm$"],
    "m": [r"^m$"],
    "km": [r"^km$"],
    "days": [r"^days?$"],
    "months": [r"^months?$"],
    "years": [r"^years?$"],
    "lakh": [r"^lakh$", r"^lac$", r"^lacs?$"],
    "crore": [r"^crore$", r"^cr\.?$"],
}


def _normalize_indian_number_grouping(text: str) -> str:
    text = text.strip()
    if "," not in text:
        return text

    parts = text.split(".")
    integer_part = parts[0]
    decimal_part = parts[1] if len(parts) > 1 else None
    digits = integer_part.replace(",", "")
    if len(integer_part.split(",")[-1]) == 3 and len(integer_part.split(",")[:-1]) > 1:
        return digits + ("." + decimal_part if decimal_part else "")
    return text.replace(",", "")


def _parse_decimal(text: str) -> Decimal:
    text = text.strip()
    text = text.replace("\u00A0", " ").replace("\u202F", " ")
    text = text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise NormalizationError(f"Unable to parse decimal value: {text}") from exc


def normalize_money(value: Any) -> NormalizationResult:
    raw = str(value).strip()
    if not raw:
        raise NormalizationError("Empty money value")

    cleaned = raw.replace("\u00A0", " ").replace("\u202F", " ").strip()
    cleaned = re.sub(r"Rs\.?\s*|INR\s*|Rupees?\s*|₹\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)

    multiplier = Decimal("1")
    normalized_unit = "INR"
    original_unit = "INR"

    if re.search(r"(?i)\b(cr|crore)\b", cleaned):
        multiplier = Decimal("10000000")
        cleaned = re.sub(r"(?i)\b(cr|crore)\b\.?", "", cleaned)
    elif re.search(r"(?i)\b(lakh|lac|lacs)\b", cleaned):
        multiplier = Decimal("100000")
        cleaned = re.sub(r"(?i)\b(lakh|lac|lacs)\b\.?", "", cleaned)
    elif re.search(r"(?i)\b(m)\b", cleaned) and not re.search(r"\d+\s*m\b", cleaned):
        multiplier = Decimal("1000000")
        cleaned = re.sub(r"(?i)\b(m)\b", "", cleaned)
    elif re.search(r"(?i)\b(k)\b", cleaned) and not re.search(r"\d+\s*k\b", cleaned):
        multiplier = Decimal("1000")
        cleaned = re.sub(r"(?i)\b(k)\b", "", cleaned)

    cleaned = _normalize_indian_number_grouping(cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        raise NormalizationError(f"Unable to parse money value: {raw}")

    amount = _parse_decimal(cleaned)
    normalized_value = amount * multiplier
    return NormalizationResult(
        raw_value=raw,
        normalized_value=normalized_value.quantize(Decimal("1")),
        normalized_type="currency_inr",
        normalized_unit=normalized_unit,
        original_unit=original_unit,
        confidence=Decimal("1.0"),
        status="valid",
    )


def normalize_percentage(value: Any) -> NormalizationResult:
    raw = str(value).strip()
    if not raw:
        raise NormalizationError("Empty percentage value")

    cleaned = raw.replace("%", "").replace("percent", "").replace("percentage", "")
    cleaned = cleaned.strip()
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    try:
        normalized_value = _parse_decimal(cleaned)
    except NormalizationError as exc:
        raise NormalizationError(f"Unable to parse percentage: {raw}") from exc

    return NormalizationResult(
        raw_value=raw,
        normalized_value=normalized_value,
        normalized_type="percentage",
        original_unit="%",
        normalized_unit="%",
        confidence=Decimal("1.0"),
        status="valid",
    )


def normalize_date(value: Any) -> NormalizationResult:
    raw = str(value).strip()
    if not raw:
        raise NormalizationError("Empty date")

    normalized = raw.replace("/", "/").replace("-", "-").strip()
    normalized = re.sub(r"\s+", " ", normalized)

    for pattern in DATE_PATTERNS:
        match = re.match(pattern, normalized)
        if not match:
            continue

        parts = match.groupdict()
        try:
            if "month_name" in parts and parts["month_name"]:
                month_name = parts["month_name"].lower()
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
                month = month_map.get(month_name[:3])
                if not month:
                    raise NormalizationError(f"Unknown month name: {parts['month_name']}")
                dt = date(int(parts["year"]), month, int(parts["day"]))
            else:
                day = int(parts["day"])
                month = int(parts["month"])
                year = int(parts["year"])
                if month > 12 or day > 31:
                    raise NormalizationError(f"Invalid date values: {raw}")
                dt = date(year, month, day)
            return NormalizationResult(
                raw_value=raw,
                normalized_value=dt.isoformat(),
                normalized_type="date",
                confidence=Decimal("1.0"),
                status="valid",
            )
        except ValueError as exc:
            raise NormalizationError(f"Invalid date: {raw}") from exc

    raise NormalizationError(f"Unable to normalize date: {raw}")


def normalize_unit(value: Any) -> NormalizationResult:
    raw = str(value).strip()
    if not raw:
        raise NormalizationError("Empty unit")

    candidate_unit = raw.lower().strip()
    for normalized_unit, patterns in UNIT_PATTERNS.items():
        for pattern in patterns:
            if re.match(pattern, candidate_unit):
                return NormalizationResult(
                    raw_value=raw,
                    normalized_value=normalized_unit,
                    normalized_type="unit",
                    normalized_unit=normalized_unit,
                    original_unit=candidate_unit,
                    confidence=Decimal("1.0"),
                    status="valid",
                )

    raise NormalizationError(f"Unsupported unit: {raw}")


def infer_normalization(value: Any) -> NormalizationResult:
    raw = str(value).strip()
    if not raw:
        raise NormalizationError("Empty value")

    try:
        if re.search(r"\b(Rs\.?|INR|₹|Rupees?)\b", raw, flags=re.IGNORECASE) or re.search(r"\b(Cr|Crore|Lakh|Lac|M|K)\b", raw, flags=re.IGNORECASE):
            return normalize_money(raw)
        if re.search(r"%|percent|percentage", raw, flags=re.IGNORECASE):
            return normalize_percentage(raw)
        if re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", raw) or re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", raw) or re.search(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}", raw):
            return normalize_date(raw)
        if re.fullmatch(r"[\d,]+(\.\d+)?", raw):
            return NormalizationResult(
                raw_value=raw,
                normalized_value=_parse_decimal(_normalize_indian_number_grouping(raw)),
                normalized_type="number",
                confidence=Decimal("1.0"),
                status="valid",
            )
    except NormalizationError:
        pass

    raise NormalizationError(f"Unable to infer normalization for: {raw}")
