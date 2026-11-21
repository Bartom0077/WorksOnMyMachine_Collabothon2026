"""Module 4: GREEN / YELLOW / RED, the credit-line floor and the risk window."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

import service
from domain import DashboardQuery, ForecastDay, ForecastItem, ItemKind, RiskStatus, RiskWindow
from risk import assess_risk, classify, risk_drivers

AS_OF = date(2025, 5, 10)
BUFFER = Decimal("3000")


def _days(balances, floor="0", items=()):
    return [ForecastDay(AS_OF + timedelta(days=i + 1), Decimal(str(b)), Decimal(0), Decimal(0), tuple(items),
                        Decimal(floor)) for i, b in enumerate(balances)]


@pytest.mark.parametrize("min_balance, floor, expected", [
    ("3000", "0", RiskStatus.GREEN),
    ("2999.99", "0", RiskStatus.YELLOW),
    ("0", "0", RiskStatus.YELLOW),
    ("-0.01", "0", RiskStatus.RED),
    ("-12000", "-15000", RiskStatus.GREEN),     # −15000 + 3000 = −12000
    ("-12000.01", "-15000", RiskStatus.YELLOW),
    ("-15000", "-15000", RiskStatus.YELLOW),
    ("-15000.01", "-15000", RiskStatus.RED),
])
def test_status_thresholds(min_balance, floor, expected):
    risk = assess_risk(_days([5000, min_balance, 6000], floor), AS_OF, BUFFER)
    assert risk.status is expected
    assert risk.min_balance_eur == Decimal(min_balance)
    assert risk.min_balance_date == AS_OF + timedelta(days=2) and risk.days_until_min == 2


def test_classify_is_the_spec_rule():
    assert classify(Decimal("-1"), BUFFER) is RiskStatus.RED
    assert classify(Decimal("0"), BUFFER) is RiskStatus.YELLOW
    assert classify(BUFFER, BUFFER) is RiskStatus.GREEN


def test_risk_window_runs_from_first_day_below_buffer_to_the_minimum():
    risk = assess_risk(_days([4000, 2500, 3500, -100, -900, 200]), AS_OF, BUFFER)
    assert risk.risk_window == RiskWindow(AS_OF + timedelta(days=2), AS_OF + timedelta(days=5))
    assert (risk.floor_eur, risk.credit_limit_eur) == (0, None)


def test_green_has_no_risk_window_and_earliest_minimum_wins():
    risk = assess_risk(_days([4000, 3500, 3500]), AS_OF, BUFFER)
    assert risk.status is RiskStatus.GREEN and risk.risk_window is None
    assert risk.min_balance_date == AS_OF + timedelta(days=2)


def test_credit_limit_is_reported():
    risk = assess_risk(_days([1000, -7000], floor="-15000"), AS_OF, BUFFER)
    assert (risk.status, risk.floor_eur, risk.credit_limit_eur) == (RiskStatus.GREEN, Decimal("-15000"), Decimal("15000"))
    assert risk.headroom_eur == Decimal("8000")


def test_days_before_the_credit_line_starts_use_their_own_floor():
    days = _days([-500]) + [ForecastDay(AS_OF + timedelta(days=2), Decimal("-7000"), Decimal(0), Decimal(0), (),
                                        Decimal("-15000"))]
    risk = assess_risk(days, AS_OF, BUFFER)
    assert risk.status is RiskStatus.RED and risk.min_balance_date == AS_OF + timedelta(days=1)


def test_risk_drivers_sum_fixed_costs_in_the_window():
    items = (ForecastItem("Rent", "rent", ItemKind.FIXED, Decimal("-3800")),
             ForecastItem("Zurich", "investment_outflow", ItemKind.FIXED, Decimal("-4333.34")),
             ForecastItem("Revenue", "variable_revenue", ItemKind.VARIABLE_REVENUE, Decimal("1000")),
             ForecastItem("Oven", "expense", ItemKind.EVENT, Decimal("-8000")))
    days = _days([1, 2, 3, 4], items=items)
    drivers = risk_drivers(days, RiskWindow(days[1].date, days[2].date))
    assert [(d.category, d.total_eur, d.deferrable) for d in drivers] == [
        ("investment_outflow", Decimal("8666.68"), True), ("rent", Decimal("7600"), False)]
    assert drivers[1].label == "Rent"
    assert risk_drivers(days, None) == ()


@pytest.mark.parametrize("as_of, expected", [
    (date(2025, 5, 10), "In about 7 weeks you will be about €300 short."),
    (date(2025, 7, 10), "You are already about €16,000 short today, and in about 3 weeks this grows to about "
                        "€20,000."),
    (date(2025, 7, 28), "You are already about €12,000 short today. The next 60 days do not get worse than that, "
                        "but the forecast still shows about €11,000 short in 4 days."),
    (date(2025, 1, 5), "Your balance (about €100) is already below your €3,000 safety buffer today. It is not "
                       "projected to fall further in the next 60 days: the lowest projected balance is about "
                       "€400, in 1 day."),
])
def test_headline_takes_todays_balance_into_account(repo, as_of, expected):
    """Finding: the headline said "will drop to" / "in 5 days you will be short" when today was already worse."""
    dashboard = service.build_dashboard(repo, DashboardQuery(as_of, 60, Decimal("3000")))
    assert dashboard.headline == expected
