"""Module 3: each event type edits the forecast plan in its own way."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from domain import EventDraft, EventType, ForecastItem, ItemKind
from events import DayPlan, add_months, apply_events
from fakes import closure, expense, inflow

START = date(2025, 6, 1)


def _plan(days=90):
    plan = {}
    for offset in range(days):
        day = START + timedelta(days=offset)
        plan[day] = DayPlan(day, [
            ForecastItem("Revenue", "variable_revenue", ItemKind.VARIABLE_REVENUE, Decimal("1000")),
            ForecastItem("Purchases", "variable_cost", ItemKind.VARIABLE_COST, Decimal("-400")),
        ])
    for day in plan.values():
        if day.date.day == 15:
            day.items.append(ForecastItem("Transfer to Zurich", "investment_outflow", ItemKind.FIXED,
                                          Decimal("-4333.34")))
        if day.date.day == 1:
            day.items.append(ForecastItem("Rent", "rent", ItemKind.FIXED, Decimal("-3800")))
    return plan


def _total(day: DayPlan) -> Decimal:
    return sum(item.amount_eur for item in day.items)


def test_closure_removes_variable_flows_but_keeps_fixed_costs():
    plan = _plan()
    apply_events(plan, [closure(date(2025, 6, 1), date(2025, 6, 21))])
    assert _total(plan[date(2025, 6, 1)]) == Decimal("-3800")
    assert _total(plan[date(2025, 6, 2)]) == 0
    assert _total(plan[date(2025, 6, 15)]) == Decimal("-4333.34")
    assert _total(plan[date(2025, 6, 22)]) == Decimal("600")


def test_closure_partly_outside_the_plan_is_clipped():
    plan = _plan()
    apply_events(plan, [closure(date(2025, 5, 20), date(2025, 6, 2)), closure(date(2025, 8, 29), date(2025, 9, 30))])
    assert _total(plan[date(2025, 6, 2)]) == 0 and _total(plan[date(2025, 6, 3)]) == Decimal("600")
    assert _total(plan[max(plan)]) == 0


def test_expense_and_inflow_hit_their_start_day_only():
    plan = _plan()
    apply_events(plan, [expense(date(2025, 6, 20), "8000"), inflow(date(2025, 6, 25), "2500")])
    assert _total(plan[date(2025, 6, 20)]) == Decimal("-7400")
    assert _total(plan[date(2025, 6, 25)]) == Decimal("3100")
    assert plan[date(2025, 6, 20)].items[-1] == ForecastItem("New oven", "expense", ItemKind.EVENT, Decimal("-8000"))


def test_one_off_event_outside_the_plan_has_no_effect():
    plan = _plan()
    before = {day: _total(value) for day, value in plan.items()}
    apply_events(plan, [expense(date(2026, 1, 1), "8000")])
    assert before == {day: _total(value) for day, value in plan.items()}


def test_payment_deferral_moves_the_payment_two_months_later():
    plan = _plan()
    deferral = EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=date(2025, 6, 10),
                          date_to=date(2025, 6, 20), category="investment_outflow", description="Postpone")
    apply_events(plan, [deferral])
    assert _total(plan[date(2025, 6, 15)]) == Decimal("600")                       # removed
    assert _total(plan[date(2025, 7, 15)]) == Decimal("600") - Decimal("4333.34")   # untouched
    august = plan[date(2025, 8, 15)]
    assert _total(august) == Decimal("600") - 2 * Decimal("4333.34")               # regular + postponed
    assert august.items[-1].kind is ItemKind.EVENT and "postponed from 2025-06-15" in august.items[-1].label
    assert _total(plan[date(2025, 7, 1)]) == Decimal("-3200")                      # other categories untouched


def test_deferral_beyond_the_horizon_only_removes_the_payment():
    plan = _plan(days=30)
    deferral = EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=date(2025, 6, 1),
                          date_to=date(2025, 6, 30), category="investment_outflow", description="Postpone")
    apply_events(plan, [deferral])
    assert sum(_total(day) for day in plan.values()) == 30 * Decimal("600") - Decimal("3800")


def test_credit_line_sets_the_floor_without_changing_cash():
    plan = _plan()
    line = EventDraft(event_type=EventType.CREDIT_LINE, date_from=date(2025, 6, 10), date_to=date(2026, 6, 10),
                      amount_eur=Decimal("15000"), description="Credit line")
    before = {day: _total(value) for day, value in plan.items()}
    apply_events(plan, [line])
    assert before == {day: _total(value) for day, value in plan.items()}
    assert plan[date(2025, 6, 9)].floor_eur == 0
    assert plan[date(2025, 6, 10)].floor_eur == Decimal("-15000") == plan[max(plan)].floor_eur


def test_event_order_does_not_change_the_result():
    events = [closure(date(2025, 6, 1), date(2025, 6, 21)), expense(date(2025, 6, 20), "8000"),
              inflow(date(2025, 6, 20), "100")]
    first, second = _plan(), _plan()
    apply_events(first, events)
    apply_events(second, list(reversed(events)))
    assert first == second


def test_amount_is_required_for_money_events():
    broken = EventDraft(event_type=EventType.EXPENSE, date_from=START, date_to=START, description="No amount")
    with pytest.raises(ValueError):
        apply_events(_plan(), [broken])


def test_add_months_clamps_to_month_end():
    assert add_months(date(2025, 12, 31), 2) == date(2026, 2, 28)
    assert add_months(date(2025, 5, 15), 2) == date(2025, 7, 15)
