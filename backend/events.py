"""Module 3 — applying planned events to the forecast plan (ROZWIAZANIE.md §8.2).

Each event type is one small function that edits the per-day plan *before* the running balance is computed.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Iterator, Sequence

from config import DEFERRAL_MONTHS
from domain import EventDraft, EventType, ForecastItem, ItemKind

_VARIABLE_KINDS = (ItemKind.VARIABLE_REVENUE, ItemKind.VARIABLE_COST)


@dataclass
class DayPlan:
    """Mutable working copy of one forecast day: its cash movements and the allowed floor."""

    date: date
    items: list[ForecastItem] = field(default_factory=list)
    floor_eur: Decimal = Decimal("0")


Plan = dict[date, DayPlan]


def add_months(day: date, months: int) -> date:
    """Same day of the month ``months`` later, clamped to the month length."""
    month_index = day.year * 12 + day.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def apply_events(plan: Plan, events: Sequence[EventDraft]) -> None:
    """Apply all events in a stable order (deterministic output for identical input)."""
    for event in sorted(events, key=_event_order):
        _APPLIERS[event.event_type](plan, event)


def _event_order(event: EventDraft) -> tuple:
    return event.date_from, event.date_to, event.event_type.value, event.description


def _days_in_range(plan: Plan, event: EventDraft) -> Iterator[DayPlan]:
    day = max(event.date_from, min(plan))
    last = min(event.date_to, max(plan))
    while day <= last:
        yield plan[day]
        day += timedelta(days=1)


def apply_closure(plan: Plan, event: EventDraft) -> None:
    """Closed days earn no variable revenue and buy no supplies; fixed costs stay."""
    for day in _days_in_range(plan, event):
        day.items = [item for item in day.items if item.kind not in _VARIABLE_KINDS]


def apply_expense(plan: Plan, event: EventDraft) -> None:
    _add_one_off(plan, event, -_required_amount(event))


def apply_inflow(plan: Plan, event: EventDraft) -> None:
    _add_one_off(plan, event, _required_amount(event))


def apply_payment_deferral(plan: Plan, event: EventDraft) -> None:
    """Move the recurring payments of ``category`` in the window two months later (postponed, not cancelled)."""
    for day in list(_days_in_range(plan, event)):
        deferred = [item for item in day.items if item.kind is ItemKind.FIXED and item.category == event.category]
        day.items = [item for item in day.items if item not in deferred]
        new_day = plan.get(add_months(day.date, DEFERRAL_MONTHS))
        if new_day is None:
            continue  # the postponed payment falls outside the forecast horizon
        for item in deferred:
            label = f"{item.label} (postponed from {day.date.isoformat()})"
            new_day.items.append(ForecastItem(label, item.category, ItemKind.EVENT, item.amount_eur))


def apply_credit_line(plan: Plan, event: EventDraft) -> None:
    """A credit line leaves the balance untouched; it lowers the safe floor to −limit while it is active."""
    for day in _days_in_range(plan, event):
        day.floor_eur -= _required_amount(event)


def _add_one_off(plan: Plan, event: EventDraft, signed_amount: Decimal) -> None:
    day = plan.get(event.date_from)
    if day is not None:
        day.items.append(ForecastItem(event.description, event.event_type.value, ItemKind.EVENT, signed_amount))


def _required_amount(event: EventDraft) -> Decimal:
    if event.amount_eur is None or event.amount_eur <= 0:
        raise ValueError(f"A {event.event_type.value} event needs a positive amount.")
    return event.amount_eur


_APPLIERS: dict[EventType, Callable[[Plan, EventDraft], None]] = {
    EventType.CLOSURE: apply_closure,
    EventType.EXPENSE: apply_expense,
    EventType.INFLOW: apply_inflow,
    EventType.PAYMENT_DEFERRAL: apply_payment_deferral,
    EventType.CREDIT_LINE: apply_credit_line,
}
