"""Presentation units only: source observations and calculations remain absolute."""

import math
from typing import Any

UNIT_DEFINITIONS = {
    "crore": ("crore", 10_000_000),
    "lakh": ("lakh", 100_000),
    "million": ("million", 1_000_000),
    "absolute": ("absolute units", 1),
}


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def resolve_unit(currency: str | None, requested: str = "auto") -> dict:
    """Resolve a display divisor without changing currency or filling absent data."""
    currency = str(currency or "Unverified currency").upper()
    key = (
        requested
        if requested in UNIT_DEFINITIONS
        else ("crore" if currency == "INR" else "million")
    )
    label, divisor = UNIT_DEFINITIONS[key]
    return {
        "key": key,
        "label": label,
        "divisor": divisor,
        "currency": currency,
        "amount_label": f"{currency} {label}" if key != "absolute" else currency,
    }


def format_number(value: Any, decimals: int = 2) -> str:
    number = finite_number(value)
    if number is None:
        return "N/A"
    if number == 0:
        number = 0.0  # Avoid a misleading negative zero in the rendered report.
    return f"{number:,.{decimals}f}"


def format_amount(value: Any, unit: dict) -> str:
    number = finite_number(value)
    return format_number(number / unit["divisor"]) if number is not None else "N/A"


def format_percent(value: Any, ratio: bool = False) -> str:
    number = finite_number(value)
    if number is None:
        return "N/A"
    return format_number(number * 100 if ratio else number) + "%"
