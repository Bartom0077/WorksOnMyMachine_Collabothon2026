"""Decimal helpers: all money is quantized to cents with ROUND_HALF_UP."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
ZERO = Decimal("0.00")


def to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def cents(value) -> Decimal:
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money_json(value: Optional[Decimal]) -> Optional[float]:
    """Serialize a money/ratio value for JSON (2 decimals)."""
    return None if value is None else float(cents(value))


def pct_json(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(to_decimal(value).quantize(TENTH, rounding=ROUND_HALF_UP))


def safe_div(numerator: Decimal, denominator: Decimal) -> Optional[Decimal]:
    if denominator == 0:
        return None
    return numerator / denominator
