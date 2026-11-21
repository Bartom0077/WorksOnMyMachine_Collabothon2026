"""Module 2 — deterministic balance forecast (ROZWIAZANIE.md §7).

Step A projects recurring fixed payments, step B adds 28-day averages of variable revenue and costs,
step C applies planned events, step D rolls the balance forward day by day.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from categories import CATEGORIES, CategoryGroup
from config import AVERAGES_LOOKBACK_DAYS, RECURRING_GRACE_DAYS
from domain import DailyAverages, EventDraft, Forecast, ForecastDay, ForecastItem, ItemKind, LedgerEntry
from events import DayPlan, Plan, add_months, apply_events
from kpi_engine.money import ZERO, cents

# Finite commitments state their progress in the payment reference, e.g. "Capital Tranche 05/12".
INSTALMENT_PATTERN = re.compile(r"Tranche\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

REVENUE_ITEM_LABEL = f"Expected card revenue ({AVERAGES_LOOKBACK_DAYS}-day average)"
VARIABLE_COST_ITEM_LABEL = f"Expected purchases ({AVERAGES_LOOKBACK_DAYS}-day average)"


@dataclass(frozen=True)
class RecurringPayment:
    date: date
    category: str
    counterparty: str
    amount_eur: Decimal  # signed (negative)


def build_forecast(ledger: Sequence[LedgerEntry], as_of: date, horizon_days: int, opening_balance: Decimal,
                   events: Sequence[EventDraft]) -> Forecast:
    """Forecast the end-of-day balance for the ``horizon_days`` days after ``as_of``."""
    known = [entry for entry in ledger if entry.date <= as_of]
    averages = daily_averages(known, as_of)
    plan = _base_plan(known, as_of, horizon_days, averages)
    apply_events(plan, events)
    return Forecast(days=_roll_balance(plan, opening_balance), daily_averages=averages)


def project_recurring(ledger: Sequence[LedgerEntry], as_of: date, end: date) -> list[RecurringPayment]:
    """Step A: repeat the last occurrence of every recurring item (category + counterparty) up to ``end``."""
    payments: list[RecurringPayment] = []
    for last in _last_occurrences(ledger, as_of).values():
        payments.extend(_project_item(last, as_of, end))
    return sorted(payments, key=lambda p: (p.date, p.category, p.counterparty))


def daily_averages(ledger: Sequence[LedgerEntry], as_of: date) -> DailyAverages:
    """Step B: average variable revenue and cost per *open* day (a day with revenue) in the lookback window."""
    start = as_of - timedelta(days=AVERAGES_LOOKBACK_DAYS - 1)
    window = [entry for entry in ledger if start <= entry.date <= as_of]
    revenue = [entry for entry in window if _group(entry) is CategoryGroup.VARIABLE_REVENUE]
    costs = [entry for entry in window if _group(entry) is CategoryGroup.VARIABLE_COST]
    open_days = len({entry.date for entry in revenue if entry.amount_eur > 0})
    if open_days == 0:
        return DailyAverages(ZERO, ZERO, AVERAGES_LOOKBACK_DAYS, 0)
    revenue_total = sum((entry.amount_eur for entry in revenue), ZERO)
    cost_total = -sum((entry.amount_eur for entry in costs), ZERO)
    return DailyAverages(cents(revenue_total / open_days), cents(cost_total / open_days),
                         AVERAGES_LOOKBACK_DAYS, open_days)


def _group(entry: LedgerEntry) -> CategoryGroup:
    return CATEGORIES[entry.category].group


def _last_occurrences(ledger: Sequence[LedgerEntry], as_of: date) -> dict[tuple[str, str], LedgerEntry]:
    latest: dict[tuple[str, str], LedgerEntry] = {}
    for entry in ledger:
        if entry.date > as_of or _group(entry) is not CategoryGroup.FIXED_COST:
            continue
        key = (entry.category, entry.counterparty)
        if key not in latest or entry.date >= latest[key].date:
            latest[key] = entry
    return latest


def _project_item(last: LedgerEntry, as_of: date, end: date) -> list[RecurringPayment]:
    cadence = CATEGORIES[last.category].cadence_months
    first_forecast_day = as_of + timedelta(days=1)
    if cadence is None or _is_discontinued(last, cadence, as_of):
        return []
    remaining = _remaining_instalments(last.reference)
    payments: list[RecurringPayment] = []
    step = 1
    while remaining is None or len(payments) < remaining:
        due = add_months(last.date, cadence * step)
        if due > end:
            break
        # A payment that is slightly overdue is still expected: it is scheduled for the first forecast day.
        payments.append(RecurringPayment(max(due, first_forecast_day), last.category, last.counterparty,
                                         last.amount_eur))
        step += 1
    return payments


def _is_discontinued(last: LedgerEntry, cadence: int, as_of: date) -> bool:
    next_due = add_months(last.date, cadence)
    return next_due + timedelta(days=RECURRING_GRACE_DAYS) < as_of


def _remaining_instalments(reference: str) -> Optional[int]:
    """Instalments still to pay for a finite commitment, or None for an open-ended recurring payment."""
    match = INSTALMENT_PATTERN.search(reference)
    if match is None:
        return None
    paid, total = int(match.group(1)), int(match.group(2))
    return max(total - paid, 0)


def _base_plan(ledger: Sequence[LedgerEntry], as_of: date, horizon_days: int, averages: DailyAverages) -> Plan:
    days = [as_of + timedelta(days=offset) for offset in range(1, horizon_days + 1)]
    plan: Plan = {day: DayPlan(day) for day in days}
    for payment in project_recurring(ledger, as_of, days[-1]):
        label = CATEGORIES[payment.category].label
        if payment.counterparty:
            label = f"{label} – {payment.counterparty}"
        plan[payment.date].items.append(ForecastItem(label, payment.category, ItemKind.FIXED, payment.amount_eur))
    for day in plan.values():
        day.items.extend(_variable_items(averages))
    return plan


def _variable_items(averages: DailyAverages) -> list[ForecastItem]:
    items = []
    if averages.revenue_eur:
        items.append(ForecastItem(REVENUE_ITEM_LABEL, CategoryGroup.VARIABLE_REVENUE.value,
                                  ItemKind.VARIABLE_REVENUE, averages.revenue_eur))
    if averages.variable_costs_eur:
        items.append(ForecastItem(VARIABLE_COST_ITEM_LABEL, CategoryGroup.VARIABLE_COST.value,
                                  ItemKind.VARIABLE_COST, -averages.variable_costs_eur))
    return items


def _roll_balance(plan: Plan, opening_balance: Decimal) -> tuple[ForecastDay, ...]:
    """Step D: balance = previous balance + inflows − outflows, day by day."""
    balance = opening_balance
    days = []
    for day in sorted(plan.values(), key=lambda d: d.date):
        inflow = sum((item.amount_eur for item in day.items if item.amount_eur > 0), ZERO)
        outflow = -sum((item.amount_eur for item in day.items if item.amount_eur < 0), ZERO)
        balance = balance + inflow - outflow
        days.append(ForecastDay(day.date, balance, inflow, outflow, tuple(day.items), day.floor_eur))
    return tuple(days)
