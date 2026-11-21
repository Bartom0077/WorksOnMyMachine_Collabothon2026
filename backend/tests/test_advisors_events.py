"""The planned-event types the CFO advisors create: term deposits and CHF purchases in the forecast."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

import service
from domain import DashboardQuery, EventDraft, EventType, ForecastItem, ItemKind
from events import DayPlan, apply_events, term_deposit_interest

START = date(2025, 6, 1)
INSTALMENT = Decimal("-4333.34")


def _plan(days=90):
    plan = {}
    for offset in range(days):
        day = START + timedelta(days=offset)
        plan[day] = DayPlan(day, [ForecastItem("Revenue", "variable_revenue", ItemKind.VARIABLE_REVENUE,
                                               Decimal("1000"))])
        if day.day == 15:
            plan[day].items.append(ForecastItem("Transfer to Zurich", "investment_outflow", ItemKind.FIXED,
                                                INSTALMENT))
    return plan


def _total(day: DayPlan) -> Decimal:
    return sum(item.amount_eur for item in day.items)


def _totals(plan) -> dict:
    return {day: _total(value) for day, value in plan.items()}


def _deposit(opening: date, maturity: date, amount="9000", category="investment") -> EventDraft:
    return EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=opening, date_to=maturity,
                      amount_eur=Decimal(amount), category=category, description="Investment: term deposit")


def _prebuy(purchase: date, last_instalment: date, amount="8666.68") -> EventDraft:
    return EventDraft(event_type=EventType.FX_PREBUY, date_from=purchase, date_to=last_instalment,
                      amount_eur=Decimal(amount), description="Swiss francs bought in advance")


def _deferral(due: date, new_due: date) -> EventDraft:
    return EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=due, date_to=new_due,
                      amount_eur=-INSTALMENT, category="investment_outflow", description="Transfer postponed")


# --------------------------------------------------------------------------- term deposit

def test_interest_is_principal_times_rate_times_days_over_365():
    # 9,000 * 2% * 31 / 365 = 15.287...
    assert term_deposit_interest(_deposit(date(2025, 10, 16), date(2025, 11, 16))) == Decimal("15.29")
    assert term_deposit_interest(_deposit(date(2026, 1, 1), date(2026, 7, 1), "12000")) == Decimal("119.01")


def test_deposit_takes_the_principal_out_and_returns_it_with_interest():
    plan = _plan()
    deposit = _deposit(date(2025, 6, 10), date(2025, 7, 10))
    apply_events(plan, [deposit])
    interest = term_deposit_interest(deposit)
    assert interest == Decimal("14.79")
    assert _total(plan[date(2025, 6, 10)]) == Decimal("1000") - Decimal("9000")
    assert _total(plan[date(2025, 7, 10)]) == Decimal("1000") + Decimal("9000") + interest
    assert plan[date(2025, 6, 10)].items[-1] == ForecastItem("Investment: term deposit", "term_deposit",
                                                             ItemKind.EVENT, Decimal("-9000"))
    assert plan[date(2025, 7, 10)].items[-1] == ForecastItem(
        "Investment: term deposit: paid back with €15 interest", "term_deposit", ItemKind.EVENT, Decimal("9014.79"))
    assert sum(_totals(plan).values()) == 90 * Decimal("1000") + 3 * INSTALMENT + interest


def test_deposit_maturing_beyond_the_horizon_only_takes_the_money_out():
    plan = _plan(days=30)
    apply_events(plan, [_deposit(date(2025, 6, 10), date(2025, 12, 10))])
    assert sum(_totals(plan).values()) == 30 * Decimal("1000") + INSTALMENT - Decimal("9000")


def test_deposit_opened_in_the_past_keeps_the_money_tied_up_from_the_first_forecast_day():
    plan = _plan()
    deposit = _deposit(date(2025, 5, 20), date(2025, 6, 20))
    apply_events(plan, [deposit])
    opening = plan[START].items[-1]
    assert opening.amount_eur == Decimal("-9000")
    assert opening.label == "Investment: term deposit (opened on 20 May, money still tied up)"
    assert _total(plan[date(2025, 6, 20)]) == Decimal("1000") + Decimal("9000") + term_deposit_interest(deposit)


@pytest.mark.parametrize("opening, maturity", [
    (date(2025, 4, 1), date(2025, 5, 31)),  # paid back before the first forecast day
    (date(2026, 1, 1), date(2026, 2, 1)),  # opened after the horizon
])
def test_deposit_outside_the_plan_has_no_effect(opening, maturity):
    plan = _plan()
    before = _totals(plan)
    apply_events(plan, [_deposit(opening, maturity)])
    assert _totals(plan) == before


def test_deposit_needs_a_positive_amount():
    broken = EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=START, date_to=START + timedelta(days=30),
                        category="investment", description="No amount")
    with pytest.raises(ValueError):
        apply_events(_plan(), [broken])


# --------------------------------------------------------------------------- CHF purchase

def test_prebuy_books_the_price_and_removes_the_covered_instalments():
    plan = _plan()
    apply_events(plan, [_prebuy(date(2025, 6, 2), date(2025, 7, 15))])
    assert _total(plan[date(2025, 6, 2)]) == Decimal("1000") - Decimal("8666.68")
    assert plan[date(2025, 6, 2)].items[-1] == ForecastItem("Swiss francs bought in advance", "fx_prebuy",
                                                            ItemKind.EVENT, Decimal("-8666.68"))
    assert _total(plan[date(2025, 6, 15)]) == Decimal("1000") == _total(plan[date(2025, 7, 15)])
    assert _total(plan[date(2025, 8, 15)]) == Decimal("1000") + INSTALMENT  # after the last covered instalment


def test_prebuy_also_removes_postponed_instalments_whatever_the_event_order():
    deferral = _deferral(date(2025, 6, 15), date(2025, 7, 20))
    prebuy = _prebuy(date(2025, 6, 2), date(2025, 7, 20))
    first, second = _plan(), _plan()
    apply_events(first, [deferral, prebuy])
    apply_events(second, [prebuy, deferral])
    assert first == second
    assert _total(first[date(2025, 7, 20)]) == Decimal("1000")
    assert not [item for day in first.values() for item in day.items
                if item.category == "investment_outflow" and day.date <= date(2025, 7, 20)]


def test_prebuy_made_in_the_past_books_what_the_balance_does_not_show_yet():
    plan = _plan()
    apply_events(plan, [_prebuy(date(2025, 5, 16), date(2025, 7, 15), amount="13000.02")])
    booked = plan[START].items[-1]
    assert booked.amount_eur == 2 * INSTALMENT  # June and July are still in the plan; May is already booked
    assert booked.label == "Swiss francs bought in advance (bought on 16 May, not yet booked)"
    assert _total(plan[date(2025, 6, 15)]) == Decimal("1000")
    assert sum(_totals(plan).values()) == 90 * Decimal("1000") + 3 * INSTALMENT


def test_prebuy_made_in_the_past_with_nothing_left_to_cover_adds_nothing():
    plan = _plan()
    before = _totals(plan)
    apply_events(plan, [_prebuy(date(2025, 5, 16), date(2025, 6, 10))])
    assert _totals(plan) == before


# --------------------------------------------------------------------------- in the real forecast

def _balances(repo, as_of, horizon=60):
    dashboard = service.build_dashboard(repo, DashboardQuery(as_of, horizon, Decimal("3000")))
    return {day.date: day.balance_eur for day in dashboard.forecast.days}, dashboard


def test_deposit_in_the_real_forecast_lowers_the_balance_until_maturity(repo):
    as_of = date(2025, 10, 15)
    before, _ = _balances(repo, as_of)
    deposit = _deposit(date(2025, 10, 16), date(2025, 11, 16), category="tax_reserve")
    repo.add_event(deposit)
    after, _ = _balances(repo, as_of)
    assert after[date(2025, 10, 16)] == before[date(2025, 10, 16)] - Decimal("9000")
    assert after[date(2025, 11, 15)] == before[date(2025, 11, 15)] - Decimal("9000")
    assert after[date(2025, 11, 16)] == before[date(2025, 11, 16)] + Decimal("15.29")
    assert after[max(after)] == before[max(before)] + Decimal("15.29")


def test_deposit_opened_before_today_stays_out_of_the_real_balance(repo):
    as_of = date(2025, 10, 20)
    before, dashboard_before = _balances(repo, as_of)
    repo.add_event(_deposit(date(2025, 10, 16), date(2025, 11, 16)))
    after, dashboard_after = _balances(repo, as_of)
    assert dashboard_after.current_balance_eur == dashboard_before.current_balance_eur  # booked data is untouched
    assert after[date(2025, 10, 21)] == before[date(2025, 10, 21)] - Decimal("9000")
    assert after[date(2025, 11, 16)] == before[date(2025, 11, 16)] + Decimal("15.29")


def test_matured_deposit_no_longer_touches_the_forecast(repo):
    as_of = date(2025, 11, 20)
    before, _ = _balances(repo, as_of)
    repo.add_event(_deposit(date(2025, 10, 16), date(2025, 11, 16)))
    assert _balances(repo, as_of)[0] == before


def test_prebuy_in_the_real_forecast_replaces_regular_and_postponed_instalments(repo):
    as_of = date(2025, 10, 15)
    before, _ = _balances(repo, as_of, horizon=120)
    repo.add_event(EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=date(2025, 11, 15),
                              date_to=date(2026, 1, 15), amount_eur=Decimal("4358.34"),
                              category="investment_outflow", description="Transfer postponed"))
    repo.add_event(_prebuy(date(2025, 10, 16), date(2026, 1, 15), amount="8716.67"))
    after, dashboard = _balances(repo, as_of, horizon=120)
    assert not [item for day in dashboard.forecast.days for item in day.items
                if item.category == "investment_outflow"]
    assert after[date(2025, 10, 16)] == before[date(2025, 10, 16)] - Decimal("8716.67")
    # two instalments of 4,358.34 are gone; the purchase of all the francs at once is one cent cheaper
    assert after[max(after)] == before[max(before)] + Decimal("0.01")


def test_prebuy_made_before_today_books_the_instalments_still_in_the_forecast(repo):
    as_of = date(2025, 11, 20)  # the November instalment is booked; December is still to come
    before, _ = _balances(repo, as_of)
    repo.add_event(_prebuy(date(2025, 10, 16), date(2025, 12, 15), amount="8716.67"))
    after, _ = _balances(repo, as_of)
    assert after[date(2025, 11, 21)] == before[date(2025, 11, 21)] - Decimal("4350.00")
    assert after[date(2025, 12, 15)] == before[date(2025, 12, 15)]
