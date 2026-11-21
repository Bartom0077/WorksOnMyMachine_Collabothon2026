"""Module 3 — applying planned events to the forecast plan (ROZWIAZANIE.md §8.2).

Each event type is one small function that edits the per-day plan *before* the running balance is computed.
Besides the owner's plans this covers what the CFO advisors create: term deposits and currency purchases.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Iterator, Sequence

from config import DAYS_PER_YEAR, TERM_DEPOSIT_INTEREST_RATE
from domain import EventDraft, EventType, ForecastItem, ItemKind
from kpi_engine.money import ZERO, cents
from texts import format_day, format_eur

ZURICH_TRANSFER_CATEGORY = "investment_outflow"  # the CHF instalments an ``fx_prebuy`` pays for in advance

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
    """A currency purchase comes last: it removes instalments that other events (deferrals) put into the plan."""
    return (event.event_type is EventType.FX_PREBUY, event.date_from, event.date_to, event.event_type.value,
            event.description)


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


def deferred_dues(events: Sequence[EventDraft]) -> frozenset[tuple[str, date]]:
    """(category, original due date) of every postponed payment; the recurring projection leaves these out."""
    return frozenset((event.category, event.date_from) for event in events
                     if event.event_type is EventType.PAYMENT_DEFERRAL and event.category is not None)


def apply_payment_deferral(plan: Plan, event: EventDraft) -> None:
    """A postponed payment is still owed: it is paid on the new due date (``date_to``).

    The original instalment is left out by the recurring projection (see ``deferred_dues``), so the obligation
    holds on its stored dates no matter how far "today" has moved.
    """
    new_day = plan.get(event.date_to)
    if new_day is not None:
        new_day.items.append(ForecastItem(event.description, event.category or event.event_type.value,
                                          ItemKind.EVENT, -_required_amount(event)))


def apply_credit_line(plan: Plan, event: EventDraft) -> None:
    """A credit line leaves the balance untouched; it lowers the safe floor to −limit while it is active."""
    for day in _days_in_range(plan, event):
        day.floor_eur -= _required_amount(event)


def term_deposit_interest(event: EventDraft) -> Decimal:
    """Interest a term deposit earns from its opening to its maturity, derived from the event alone.

    The one formula behind both the forecast and the texts of the advisors' proposals.
    """
    days = (event.date_to - event.date_from).days
    return cents(_required_amount(event) * TERM_DEPOSIT_INTEREST_RATE * days / DAYS_PER_YEAR)


def apply_term_deposit(plan: Plan, event: EventDraft) -> None:
    """The principal leaves on the opening day and returns with interest at maturity (if within the horizon).

    The deposit is simulated, so the booked balance never shows it: a deposit opened before the first forecast
    day takes its principal out on that first day instead, which keeps the money tied up until maturity.
    """
    principal = _required_amount(event)
    interest = term_deposit_interest(event)
    first_day = min(plan)
    if event.date_from < first_day <= event.date_to:
        opened = f"{event.description} (opened on {format_day(event.date_from)}, money still tied up)"
        _add_item(plan, first_day, event, opened, -principal)
    else:
        _add_item(plan, event.date_from, event, event.description, -principal)
    _add_item(plan, event.date_to, event,
              f"{event.description}: paid back with {format_eur(interest)} interest", principal + interest)


def apply_fx_prebuy(plan: Plan, event: EventDraft) -> None:
    """The CHF for the remaining Zurich instalments is bought at once: the instalments up to ``date_to`` leave
    the plan (regular and postponed ones) and the purchase price leaves the account on the purchase day.

    The purchase is simulated, so the booked balance never shows it: once the purchase day has passed, the
    instalments still in the plan are the part of the price the balance does not show yet, and exactly that
    sum is taken out on the first forecast day.
    """
    price = _required_amount(event)
    still_planned = ZERO
    for day in _days_in_range(plan, event):
        covered = [item for item in day.items if item.category == ZURICH_TRANSFER_CATEGORY]
        day.items = [item for item in day.items if item.category != ZURICH_TRANSFER_CATEGORY]
        still_planned -= sum((item.amount_eur for item in covered), ZERO)
    first_day = min(plan)
    if event.date_from >= first_day:
        _add_item(plan, event.date_from, event, event.description, -price)
    elif still_planned > 0:
        bought = f"{event.description} (bought on {format_day(event.date_from)}, not yet booked)"
        _add_item(plan, first_day, event, bought, -still_planned)


def _add_one_off(plan: Plan, event: EventDraft, signed_amount: Decimal) -> None:
    _add_item(plan, event.date_from, event, event.description, signed_amount)


def _add_item(plan: Plan, on: date, event: EventDraft, label: str, signed_amount: Decimal) -> None:
    day = plan.get(on)
    if day is not None:
        day.items.append(ForecastItem(label, event.event_type.value, ItemKind.EVENT, signed_amount))


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
    EventType.TERM_DEPOSIT: apply_term_deposit,
    EventType.FX_PREBUY: apply_fx_prebuy,
}
