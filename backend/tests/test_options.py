"""Module 5: rescue options A–E, their re-simulation, costs and the code ranking."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

import service
from domain import DashboardQuery, DecisionStatus, EventType, ForecastDay, RiskStatus, Scenario
from fakes import closure, expense
from options import apply_changes, financing_cost, recommend, required_credit_limit, unarranged_overdraft_cost

AS_OF = date(2025, 5, 10)
QUERY = DashboardQuery(AS_OF, 60, Decimal("3000"))


@pytest.fixture()
def closure_dashboard(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    return service.build_dashboard(repo, QUERY)


def _by_id(dashboard):
    return {option.id: option for option in dashboard.options}


def test_no_options_when_green(repo):
    dashboard = service.build_dashboard(repo, DashboardQuery(date(2025, 12, 31), 60, Decimal("3000")))
    assert dashboard.risk.status is RiskStatus.GREEN
    assert dashboard.options == () and dashboard.code_recommended_option_id is None


def test_closure_scenario_offers_a_b_c_e(closure_dashboard):
    assert closure_dashboard.risk.status is RiskStatus.RED
    assert [option.id for option in closure_dashboard.options] == ["A", "B", "C", "E"]
    assert all(option.requires_approval for option in closure_dashboard.options)


def test_option_a_credit_line_is_rounded_up_to_5000_and_restores_green(closure_dashboard):
    option = _by_id(closure_dashboard)["A"]
    # −min + buffer = 15,728.42 + 3,000 = 18,728.42 → 20,000
    assert option.credit_limit_eur == Decimal("20000")
    assert option.new_status is RiskStatus.GREEN
    assert option.new_min_balance_eur == closure_dashboard.risk.min_balance_eur  # a credit line adds no cash
    assert option.simulated_actions == ("Credit line request for €20,000 sent to Commerzbank (simulated).",)
    [event] = option.events_to_add
    assert (event.event_type, event.amount_eur, event.date_from) == (
        EventType.CREDIT_LINE, Decimal("20000"), AS_OF + timedelta(days=1))
    assert option.cost_eur == financing_cost(option.forecast) > 0
    assert "13%" in option.cost_note and "9%" in option.cost_note


def test_option_b_postpones_both_zurich_transfers(closure_dashboard):
    option = _by_id(closure_dashboard)["B"]
    [event] = option.events_to_add
    window = closure_dashboard.risk.risk_window
    assert (event.event_type, event.category) == (EventType.PAYMENT_DEFERRAL, "investment_outflow")
    assert (event.date_from, event.date_to) == (window.start, window.end)
    assert len(option.simulated_actions) == 2 and "15 May postponed to 15 July" in option.simulated_actions[0]
    improvement = option.new_min_balance_eur - closure_dashboard.risk.min_balance_eur
    assert improvement == Decimal("8666.68")  # two transfers of 4,333.34 EUR
    assert option.new_status is RiskStatus.RED and option.non_financial_cost


def test_option_c_shortens_the_closure_to_seven_days(closure_dashboard):
    option = _by_id(closure_dashboard)["C"]
    [update] = option.event_updates
    assert (update.event_id, update.date_from, update.date_to) == (
        closure_dashboard.events[0].id, date(2025, 6, 1), date(2025, 6, 7))
    assert option.events_to_add == () and option.non_financial_cost == "14 days less holiday"
    averages = closure_dashboard.forecast.daily_averages
    regained = 14 * (averages.revenue_eur - averages.variable_costs_eur)
    assert option.new_min_balance_eur - closure_dashboard.risk.min_balance_eur == regained


def test_option_e_combines_deferral_with_a_smaller_credit_line(closure_dashboard):
    options = _by_id(closure_dashboard)
    option = options["E"]
    assert [event.event_type for event in option.events_to_add] == [EventType.PAYMENT_DEFERRAL, EventType.CREDIT_LINE]
    assert option.credit_limit_eur == Decimal("15000") < options["A"].credit_limit_eur
    assert option.new_status is RiskStatus.GREEN
    assert option.new_min_balance_eur == options["B"].new_min_balance_eur
    assert option.cost_eur < options["A"].cost_eur
    assert option.simulated_actions[0] == "Credit line request for €15,000 sent to Commerzbank (simulated)."


def test_code_ranking_prefers_green_then_cost(closure_dashboard):
    assert closure_dashboard.code_recommended_option_id == "E"
    without_e = [option for option in closure_dashboard.options if option.id != "E"]
    assert recommend(without_e) == "A"                       # the only remaining GREEN option
    rejected = [replace(option, rejected=True) for option in closure_dashboard.options]
    assert recommend(rejected) is None


def test_option_d_only_with_an_expense_in_the_risk_window(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    oven = repo.add_event(expense(date(2025, 6, 20), "8000"))
    late = repo.add_event(expense(date(2025, 7, 5), "500", "Menu boards"))
    dashboard = service.build_dashboard(repo, QUERY)
    option = _by_id(dashboard)["D"]
    new_day = dashboard.risk.min_balance_date + timedelta(days=1)
    assert [(u.event_id, u.date_from, u.date_to) for u in option.event_updates] == [(oven.id, new_day, new_day)]
    assert late.id not in {u.event_id for u in option.event_updates}
    assert option.new_min_balance_eur > dashboard.risk.min_balance_eur


def test_no_option_c_or_d_without_matching_events(repo):
    dashboard = service.build_dashboard(repo, QUERY)   # no planned events at all
    assert [option.id for option in dashboard.options] == ["A", "B"]   # B alone is GREEN, so no E
    assert _by_id(dashboard)["B"].new_status is RiskStatus.GREEN
    assert dashboard.code_recommended_option_id == "B"


def test_short_closure_is_not_offered_for_shortening(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 7)))
    assert "C" not in _by_id(service.build_dashboard(repo, QUERY))


def test_rejected_options_are_flagged_and_not_recommended(repo, closure_dashboard):
    repo.events.clear()
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    service.record_decision(repo, QUERY, "E", DecisionStatus.REJECTED, None)
    dashboard = service.build_dashboard(repo, QUERY)
    assert _by_id(dashboard)["E"].rejected and not _by_id(dashboard)["A"].rejected
    assert dashboard.code_recommended_option_id == "A"


def test_apply_changes_updates_and_appends(closure_dashboard):
    option = _by_id(closure_dashboard)["C"]
    [changed] = apply_changes(closure_dashboard.events, (), option.event_updates)
    assert changed.date_to == date(2025, 6, 7) and closure_dashboard.events[0].date_to == date(2025, 6, 21)


def _day(balance, floor="0"):
    return ForecastDay(AS_OF, Decimal(balance), Decimal(0), Decimal(0), (), Decimal(floor))


def test_financing_cost_uses_both_rates():
    days = [_day("-36500", "-36500"), _day("1000")]
    assert financing_cost(days) == Decimal("9.00")             # 36,500 × 9% / 365
    assert financing_cost([_day("-36500")]) == Decimal("13.00")   # no arranged line: 13%
    assert financing_cost([_day("-36500", "-10000")]) == Decimal("11.90")  # 10,000 @ 9% + 26,500 @ 13%
    assert unarranged_overdraft_cost(days) == Decimal("13.00")
    assert financing_cost([_day("500")]) == 0


def test_required_credit_limit_rounds_up(closure_dashboard):
    baseline = Scenario(closure_dashboard.forecast, closure_dashboard.risk)
    assert required_credit_limit(baseline, Decimal("3000")) == Decimal("20000")
    exact = Scenario(closure_dashboard.forecast, replace(closure_dashboard.risk, min_balance_eur=Decimal("-7000")))
    assert required_credit_limit(exact, Decimal("3000")) == Decimal("10000")   # exactly 10,000: no extra step
