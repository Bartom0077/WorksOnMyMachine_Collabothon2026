"""Module 2 — deterministic balance forecast (ROZWIAZANIE.md §7).

Step A projects recurring fixed payments, step B adds per-open-day averages of variable revenue and costs,
step C applies planned events, step D rolls the balance forward day by day.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from categories import CATEGORIES, CategoryGroup
from config import AVERAGES_LOOKBACK_DAYS, AVERAGES_MIN_OPEN_DAYS, RECURRING_GRACE_DAYS
from domain import DailyAverages, EventDraft, Forecast, ForecastDay, ForecastItem, ItemKind, LedgerEntry
from events import DayPlan, Plan, add_months, apply_events, deferred_dues
from kpi_engine.money import ZERO, cents

# Finite commitments state their progress in the payment reference, e.g. "Capital Tranche 05/12".
INSTALMENT_PATTERN = re.compile(r"Tranche\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

# A deferred instalment: (category, original due date). The projection leaves it out on purpose.
DeferredDues = frozenset[tuple[str, date]]


@dataclass(frozen=True)
class RecurringPayment:
    date: date  # the day it is expected to leave the account
    due: date  # the regular due date (earlier than ``date`` for a slightly overdue payment)
    category: str
    counterparty: str
    amount_eur: Decimal  # signed (negative)


def build_forecast(ledger: Sequence[LedgerEntry], as_of: date, horizon_days: int, opening_balance: Decimal,
                   events: Sequence[EventDraft]) -> Forecast:
    """Forecast the end-of-day balance for the ``horizon_days`` days after ``as_of``."""
    known = [entry for entry in ledger if entry.date <= as_of]
    averages = daily_averages(known, as_of)
    plan = _base_plan(known, as_of, horizon_days, averages, deferred_dues(events))
    apply_events(plan, events)
    return Forecast(days=_roll_balance(plan, opening_balance), daily_averages=averages)


def project_recurring(ledger: Sequence[LedgerEntry], as_of: date, end: date,
                      deferred: DeferredDues = frozenset()) -> list[RecurringPayment]:
    """Step A: repeat the last occurrence of every recurring item (category + counterparty) up to ``end``.

    Instalments in ``deferred`` were postponed by the owner; they are skipped here and paid as planned events.
    """
    payments: list[RecurringPayment] = []
    for last in _last_occurrences(ledger, as_of).values():
        payments.extend(_project_item(last, as_of, end, deferred))
    return sorted(payments, key=lambda p: (p.date, p.category, p.counterparty))


def daily_averages(ledger: Sequence[LedgerEntry], as_of: date) -> DailyAverages:
    """Step B: average variable revenue and cost per *open* day (a day with revenue) in the lookback window.

    The window is the last 28 calendar days. During or right after a long closure it holds too few open days
    to be representative, so it is extended back to the day that makes ``AVERAGES_MIN_OPEN_DAYS`` open days.
    """
    known = [entry for entry in ledger if entry.date <= as_of]
    revenue = [entry for entry in known if _group(entry) is CategoryGroup.VARIABLE_REVENUE]
    start = _averages_window_start({entry.date for entry in revenue if entry.amount_eur > 0}, as_of)
    lookback_days = (as_of - start).days + 1
    revenue = [entry for entry in revenue if entry.date >= start]
    costs = [entry for entry in known if entry.date >= start and _group(entry) is CategoryGroup.VARIABLE_COST]
    open_days = len({entry.date for entry in revenue if entry.amount_eur > 0})
    if open_days == 0:
        return DailyAverages(ZERO, ZERO, lookback_days, 0)
    revenue_total = sum((entry.amount_eur for entry in revenue), ZERO)
    cost_total = -sum((entry.amount_eur for entry in costs), ZERO)
    return DailyAverages(cents(revenue_total / open_days), cents(cost_total / open_days), lookback_days, open_days)


def _averages_window_start(open_dates: set[date], as_of: date) -> date:
    regular_start = as_of - timedelta(days=AVERAGES_LOOKBACK_DAYS - 1)
    recent_first = sorted(open_dates, reverse=True)
    in_regular_window = sum(1 for day in recent_first if day >= regular_start)
    if in_regular_window >= AVERAGES_MIN_OPEN_DAYS or in_regular_window == len(recent_first):
        return regular_start
    return recent_first[:AVERAGES_MIN_OPEN_DAYS][-1]


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


def _project_item(last: LedgerEntry, as_of: date, end: date, deferred: DeferredDues) -> list[RecurringPayment]:
    cadence = CATEGORIES[last.category].cadence_months
    if cadence is None:
        return []
    first_forecast_day = as_of + timedelta(days=1)
    remaining = _remaining_instalments(last.reference)
    payments: list[RecurringPayment] = []
    step = 0
    while remaining is None or step < remaining:
        step += 1
        due = add_months(last.date, cadence * step)
        if due > end:
            break
        if (last.category, due) in deferred:
            continue  # postponed on purpose: still one of the remaining instalments, paid on its new date
        if not payments and _is_overdue_beyond_grace(due, as_of):
            return []  # the next expected payment never came: the commitment is treated as discontinued
        # A payment that is slightly overdue is still expected: it is scheduled for the first forecast day.
        payments.append(RecurringPayment(max(due, first_forecast_day), due, last.category, last.counterparty,
                                         last.amount_eur))
    return payments


def _is_overdue_beyond_grace(due: date, as_of: date) -> bool:
    return due + timedelta(days=RECURRING_GRACE_DAYS) < as_of


def _remaining_instalments(reference: str) -> Optional[int]:
    """Instalments still to pay for a finite commitment, or None for an open-ended recurring payment."""
    match = INSTALMENT_PATTERN.search(reference)
    if match is None:
        return None
    paid, total = int(match.group(1)), int(match.group(2))
    return max(total - paid, 0)


def _base_plan(ledger: Sequence[LedgerEntry], as_of: date, horizon_days: int, averages: DailyAverages,
               deferred: DeferredDues) -> Plan:
    days = [as_of + timedelta(days=offset) for offset in range(1, horizon_days + 1)]
    plan: Plan = {day: DayPlan(day) for day in days}
    for payment in project_recurring(ledger, as_of, days[-1], deferred):
        label = CATEGORIES[payment.category].label
        if payment.counterparty:
            label = f"{label} – {payment.counterparty}"
        plan[payment.date].items.append(
            ForecastItem(label, payment.category, ItemKind.FIXED, payment.amount_eur, due_date=payment.due))
    for day in plan.values():
        day.items.extend(_variable_items(averages))
    return plan


def _variable_items(averages: DailyAverages) -> list[ForecastItem]:
    items = []
    if averages.revenue_eur:
        items.append(ForecastItem(f"Expected card revenue ({_averages_basis(averages)})",
                                  CategoryGroup.VARIABLE_REVENUE.value,
                                  ItemKind.VARIABLE_REVENUE, averages.revenue_eur))
    if averages.variable_costs_eur:
        items.append(ForecastItem(f"Expected purchases ({_averages_basis(averages)})",
                                  CategoryGroup.VARIABLE_COST.value,
                                  ItemKind.VARIABLE_COST, -averages.variable_costs_eur))
    return items


def _averages_basis(averages: DailyAverages) -> str:
    return f"{averages.lookback_days}-day average"


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
