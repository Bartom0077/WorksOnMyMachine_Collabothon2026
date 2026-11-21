"""Grounding checks on the texts the language model writes for the advisors: every date, percentage and amount
it states must come from the dossier of that call.

``llm_recommendation.ungrounded_amounts`` (shared with the liquidity agent) accepts any figure within one euro
of a dossier amount and ignores percentages and dates. That is too loose here: the FX dossier holds exchange
rates around 1, so an invented rate, fee or spread would pass, and nothing would stop an invented tax rate,
deposit rate or settlement date. The checks below close those gaps for the advisors only.
"""
from __future__ import annotations

import calendar
import re
from decimal import Decimal
from typing import Any, Iterator, Optional

import llm_recommendation
from llm_recommendation import dossier_amounts, quoted_amounts

# Below this, a figure is a rate, a fee or pocket money: it must match a dossier figure exactly, not roughly.
EXACT_MATCH_BELOW = Decimal("10")

_MONTH_NUMBERS = {name: number for number, name in enumerate(calendar.month_name) if name}
_MONTH = "|".join(_MONTH_NUMBERS)
_ORDINAL = r"(?:st|nd|rd|th)?"
_YEAR = r"\d{4}"
# 2025-12-15 | 15.12.2025 | 15 December [2025] | December 15[, 2025]
_DATE = re.compile(
    rf"\b(?P<iso_year>{_YEAR})-(?P<iso_month>\d{{2}})-(?P<iso_day>\d{{2}})\b"
    rf"|\b(?P<dot_day>\d{{1,2}})\.(?P<dot_month>\d{{1,2}})\.(?P<dot_year>{_YEAR})\b"
    rf"|\b(?P<day>\d{{1,2}}){_ORDINAL}\s+(?:of\s+)?(?P<month>{_MONTH})\b(?:,?\s+(?P<year>{_YEAR})\b)?"
    rf"|\b(?P<us_month>{_MONTH})\s+(?P<us_day>\d{{1,2}}){_ORDINAL}(?!\d|[.,]\d)(?:,?\s+(?P<us_year>{_YEAR})\b)?")
_DATE_FORMS = (("iso_day", "iso_month", "iso_year"), ("dot_day", "dot_month", "dot_year"),
               ("day", "month", "year"), ("us_day", "us_month", "us_year"))
_PERCENTAGE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:%|percent\b|per cent\b)", re.IGNORECASE)

_Day = tuple[int, int, Optional[int]]  # day, month, year (None when the text names no year)


def ungrounded_dates(text: str, dossier: dict) -> list[str]:
    """Calendar dates in the model's text that the dossier does not contain (a missing year matches any year)."""
    known = [day for string in _strings(dossier) for _, day in _dates(string)]
    return [raw for raw, day in _dates(text) if not any(_same_day(day, other) for other in known)]


def without_dates(text: str) -> str:
    """The text with its calendar dates blanked, so that "15.12.2025" is not read as an amount of 15.12."""
    return _DATE.sub(" ", text)


def ungrounded_percentages(text: str, dossier: dict) -> list[str]:
    """Percentages in the model's text that are not stated in the dossier, digit for digit."""
    known = {value for string in _strings(dossier) for _, value in _percentages(string)}
    return [raw for raw, value in _percentages(text) if value not in known]


def ungrounded_amounts(text: str, dossier: dict) -> list[str]:
    """Amounts that are not in the dossier: the shared check, plus an exact match for small figures."""
    exact = {abs(value) for value in dossier_amounts(dossier)}
    rough = llm_recommendation.ungrounded_amounts(text, dossier)
    small = [quoted.raw for quoted in quoted_amounts(text)
             if quoted.is_money and abs(quoted.value) < EXACT_MATCH_BELOW and abs(quoted.value) not in exact]
    return rough + [raw for raw in small if raw not in rough]


def _dates(text: str) -> Iterator[tuple[str, _Day]]:
    for match in _DATE.finditer(text):
        day, month, year = next(form for form in _DATE_FORMS if match.group(form[0]))
        month_text = match.group(month)
        yield match.group(0), (int(match.group(day)), _MONTH_NUMBERS.get(month_text) or int(month_text),
                               int(match.group(year)) if match.group(year) else None)


def _same_day(day: _Day, other: _Day) -> bool:
    return day[:2] == other[:2] and (day[2] is None or other[2] is None or day[2] == other[2])


def _percentages(text: str) -> Iterator[tuple[str, Decimal]]:
    for match in _PERCENTAGE.finditer(text):
        yield match.group(0), Decimal(match.group(1).replace(",", "."))


def _strings(node: Any) -> Iterator[str]:
    """Every text value in the dossier, however deeply nested."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
