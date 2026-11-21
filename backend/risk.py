"""Module 4 — liquidity risk detection (ROZWIAZANIE.md §9).

    floor  = −credit limit (if a credit line is arranged) else 0
    GREEN  if min balance >= floor + buffer
    YELLOW if floor <= min balance < floor + buffer
    RED    if min balance < floor

The floor can change during the horizon (a credit line has a start date), so each day is compared with its
own floor; with a constant floor this is exactly the rule above. The "minimum" is the day with the least
headroom above its floor (earliest on ties).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from categories import CATEGORIES
from domain import ForecastDay, ItemKind, RiskAssessment, RiskDriver, RiskStatus, RiskWindow
from kpi_engine.money import ZERO


def assess_risk(days: Sequence[ForecastDay], as_of: date, buffer_eur: Decimal) -> RiskAssessment:
    lowest = min(days, key=lambda day: (_headroom(day), day.date))
    status = classify(_headroom(lowest), buffer_eur)
    return RiskAssessment(
        status=status,
        min_balance_eur=lowest.balance_eur,
        min_balance_date=lowest.date,
        floor_eur=lowest.floor_eur,
        credit_limit_eur=-lowest.floor_eur if lowest.floor_eur < 0 else None,
        days_until_min=(lowest.date - as_of).days,
        risk_window=_risk_window(days, lowest, buffer_eur) if status is not RiskStatus.GREEN else None,
    )


def classify(headroom_eur: Decimal, buffer_eur: Decimal) -> RiskStatus:
    if headroom_eur < 0:
        return RiskStatus.RED
    if headroom_eur < buffer_eur:
        return RiskStatus.YELLOW
    return RiskStatus.GREEN


def risk_drivers(days: Sequence[ForecastDay], window: Optional[RiskWindow]) -> tuple[RiskDriver, ...]:
    """Fixed costs that fall inside the risk window, summed per category, largest first."""
    if window is None:
        return ()
    totals: dict[str, Decimal] = {}
    for day in days:
        if not window.start <= day.date <= window.end:
            continue
        for item in day.items:
            if item.kind is ItemKind.FIXED:
                totals[item.category] = totals.get(item.category, ZERO) - item.amount_eur
    drivers = [RiskDriver(key, CATEGORIES[key].label, total, CATEGORIES[key].deferrable)
               for key, total in totals.items()]
    return tuple(sorted(drivers, key=lambda driver: (-driver.total_eur, driver.category)))


def _headroom(day: ForecastDay) -> Decimal:
    return day.balance_eur - day.floor_eur


def _risk_window(days: Sequence[ForecastDay], lowest: ForecastDay, buffer_eur: Decimal) -> RiskWindow:
    """From the first day below the buffer to the day of the minimum."""
    first_below = next(day for day in days if _headroom(day) < buffer_eur)
    return RiskWindow(start=first_below.date, end=lowest.date)
