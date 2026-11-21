"""Module 2: ledger balance, recurring payments, averages and the day-by-day forecast."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from domain import ItemKind, LedgerEntry
from fakes import closure
from forecast import build_forecast, daily_averages, project_recurring
from kpi_engine.models import Transaction
from ledger import LedgerError, balance_on, build_ledger, daily_balances, opening_anchor

AS_OF = date(2025, 5, 10)


def _entry(day, amount, category, counterparty="X", reference=""):
    return LedgerEntry(day, Decimal(amount), category, counterparty, reference)


def test_balance_matches_documented_values(ledger, anchors):
    anchor = opening_anchor(anchors)
    assert anchor.anchor_date == date(2024, 12, 31)
    assert balance_on(ledger, anchor, AS_OF) == Decimal("3205.69")
    assert balance_on(ledger, anchor, date(2025, 7, 3)) == Decimal("-22097.60")
    # rolling the opening balance forward reproduces the later documented anchors
    assert balance_on(ledger, anchor, date(2025, 6, 30)) == Decimal("-20098.69")
    assert balance_on(ledger, anchor, date(2025, 12, 31)) == Decimal("33266.35")


def test_daily_balances_are_continuous(ledger, anchors):
    points = daily_balances(ledger, opening_anchor(anchors), date(2025, 2, 11), AS_OF)
    assert len(points) == 89 and points[-1].balance_eur == Decimal("3205.69")
    assert [p.date for p in points] == sorted(p.date for p in points)


def test_chf_wire_is_converted_with_the_embedded_rate(ledger):
    wire = next(e for e in ledger if e.category == "investment_outflow" and e.date == date(2025, 12, 15))
    assert wire.amount_eur == Decimal("-4362.50")  # 4166.67 CHF * 1.047


def test_foreign_currency_without_rate_is_an_error():
    tx = Transaction(date(2025, 1, 5), Decimal("-10"), "USD", "SUPPLIER PAYMENT", "no rate", "X", id=1)
    with pytest.raises(LedgerError):
        build_ledger([tx])


def test_missing_opening_balance_is_an_error():
    with pytest.raises(LedgerError):
        opening_anchor([])


def test_recurring_payments_repeat_the_last_occurrence(ledger):
    payments = project_recurring(ledger, AS_OF, date(2025, 7, 9))
    by_category = {}
    for payment in payments:
        by_category.setdefault(payment.category, []).append(payment.date)
    assert by_category["rent"] == [date(2025, 6, 1), date(2025, 7, 1)]
    assert by_category["income_tax_advance"] == [date(2025, 6, 10)]          # quarterly: 10 March + 3 months
    assert "trade_tax_advance" not in {p.category for p in payments if p.date > date(2025, 5, 15)}
    assert by_category["trade_tax_advance"] == [date(2025, 5, 15)]           # 15 February + 3 months
    assert by_category["investment_outflow"] == [date(2025, 5, 15), date(2025, 6, 15)]
    assert len(by_category["net_payroll"]) == 6                              # 3 employees x 28 May, 28 June
    assert len(by_category["owner_withdrawal"]) == 2


def test_recurring_amount_is_the_last_paid_amount_in_eur(ledger):
    payments = project_recurring(ledger, AS_OF, date(2025, 5, 31))
    wire = next(p for p in payments if p.category == "investment_outflow")
    last_paid = max((e for e in ledger if e.category == "investment_outflow" and e.date <= AS_OF),
                    key=lambda e: e.date)
    assert wire.amount_eur == last_paid.amount_eur < 0


def test_finite_commitment_stops_after_the_last_instalment(ledger):
    payments = project_recurring(ledger, date(2025, 12, 31), date(2026, 3, 31))
    assert not [p for p in payments if p.category == "investment_outflow"]
    remaining = project_recurring(ledger, date(2025, 10, 20), date(2026, 3, 31))
    assert [p.date for p in remaining if p.category == "investment_outflow"] == [date(2025, 11, 15), date(2025, 12, 15)]


def test_discontinued_item_is_not_projected_and_overdue_one_is_due_tomorrow():
    stale = _entry(date(2025, 2, 28), "-900", "net_payroll", "Former Employee")
    overdue = _entry(date(2025, 4, 5), "-500", "utilities", "Energy")
    payments = project_recurring([stale, overdue], AS_OF, date(2025, 6, 30))
    assert [(p.counterparty, p.date) for p in payments] == [("Energy", date(2025, 5, 11)), ("Energy", date(2025, 6, 5))]


def test_month_end_dates_are_clamped():
    last = _entry(date(2025, 1, 31), "-100", "rent")
    assert [p.date for p in project_recurring([last], date(2025, 2, 1), date(2025, 4, 30))] == [
        date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30)]


def test_daily_averages_use_open_days_only():
    open_days = [AS_OF - timedelta(days=2 * offset) for offset in range(14)]   # every other day: 14 open days
    entries = [_entry(day, "1000", "card_revenue_debit") for day in open_days] + [
        _entry(AS_OF, "400", "card_revenue_credit"),
        _entry(AS_OF, "-4200", "supplier_invoices"),
        _entry(date(2025, 4, 12), "9999", "card_revenue_debit"),   # outside the 28-day window
        _entry(date(2025, 5, 8), "-3800", "rent"),                 # fixed cost: ignored
    ]
    averages = daily_averages(entries, AS_OF)
    assert (averages.open_days, averages.lookback_days) == (14, 28)
    assert averages.revenue_eur == Decimal("1028.57")              # 14,400 / 14 open days
    assert averages.variable_costs_eur == Decimal("300.00")


def test_daily_averages_look_further_back_when_the_window_has_too_few_open_days():
    """A long closure: only one open day in the last 28 days, so the window grows back to 14 open days."""
    before_closure = [date(2025, 3, 1) + timedelta(days=offset) for offset in range(20)]   # 1–20 March
    entries = [_entry(day, "1000", "card_revenue_debit") for day in before_closure] + [
        _entry(AS_OF, "300", "card_revenue_debit"),
        _entry(date(2025, 3, 10), "-2800", "supplier_invoices"),
        _entry(date(2025, 3, 2), "-9999", "supplier_invoices"),    # before the extended window
    ]
    averages = daily_averages(entries, AS_OF)
    assert averages.open_days == 14                                 # 10 May plus 8–20 March
    assert averages.lookback_days == (AS_OF - date(2025, 3, 8)).days + 1
    assert averages.revenue_eur == Decimal("950.00")                # (13 × 1,000 + 300) / 14
    assert averages.variable_costs_eur == Decimal("200.00")


@pytest.mark.parametrize("as_of", [date(2025, 6, 15), date(2025, 6, 27), date(2025, 6, 28), date(2025, 6, 30),
                                   date(2025, 7, 1), date(2025, 7, 3)])
def test_real_closure_does_not_spoil_the_averages(ledger, as_of):
    """The pizzeria was really closed in June 2025: the averages must stay representative during and after it."""
    averages = daily_averages(ledger, as_of)
    assert averages.open_days >= 14
    assert Decimal("1000") < averages.revenue_eur < Decimal("2000")
    assert Decimal("500") < averages.variable_costs_eur < Decimal("1000")


def test_deferred_instalment_is_skipped_without_ending_the_commitment():
    """The May and June tranches were postponed and really not paid: the later tranches are still expected."""
    last = _entry(date(2025, 4, 15), "-4333.34", "investment_outflow", "Luigi", "Capital Tranche 04/12")
    deferred = frozenset({("investment_outflow", date(2025, 5, 15)), ("investment_outflow", date(2025, 6, 15))})
    end = date(2025, 9, 30)
    for as_of in (date(2025, 5, 16), date(2025, 5, 26), date(2025, 6, 20)):
        payments = project_recurring([last], as_of, end, deferred)
        assert [payment.date for payment in payments] == [date(2025, 7, 15), date(2025, 8, 15), date(2025, 9, 15)]
    # without the deferral the missing May payment ends the commitment after the grace period
    assert project_recurring([last], date(2025, 5, 26), end) == []
    overdue = project_recurring([last], date(2025, 5, 16), end)[0]
    assert (overdue.date, overdue.due) == (date(2025, 5, 17), date(2025, 5, 15))


def test_no_open_days_gives_zero_averages():
    averages = daily_averages([_entry(date(2025, 5, 8), "-3800", "rent")], AS_OF)
    assert (averages.revenue_eur, averages.variable_costs_eur, averages.open_days) == (0, 0, 0)


def test_real_data_averages_on_10_may(ledger):
    averages = daily_averages(ledger, AS_OF)
    assert (averages.revenue_eur, averages.variable_costs_eur, averages.open_days) == (
        Decimal("1474.87"), Decimal("741.40"), 28)


def test_forecast_days_balance_and_items(ledger):
    forecast = build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), ())
    assert len(forecast.days) == 60
    assert (forecast.days[0].date, forecast.days[-1].date) == (date(2025, 5, 11), date(2025, 7, 9))
    balance = Decimal("3205.69")
    for day in forecast.days:
        assert day.inflow_eur - day.outflow_eur == sum(item.amount_eur for item in day.items)
        balance += day.inflow_eur - day.outflow_eur
        assert day.balance_eur == balance
    first = forecast.days[0]
    assert first.balance_eur == Decimal("3939.16")
    assert {item.kind for item in first.items} == {ItemKind.VARIABLE_REVENUE, ItemKind.VARIABLE_COST}
    payday = next(day for day in forecast.days if day.date == date(2025, 5, 28))
    assert sum(1 for item in payday.items if item.kind is ItemKind.FIXED) == 4


def test_forecast_never_uses_future_transactions(ledger):
    known_only = [entry for entry in ledger if entry.date <= AS_OF]
    assert build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), ()) == \
        build_forecast(known_only, AS_OF, 60, Decimal("3205.69"), ())


def test_forecast_is_deterministic(ledger):
    events = (closure(date(2025, 6, 1), date(2025, 6, 21)),)
    first = build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), events)
    second = build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), events)
    assert first == second


def test_three_week_closure_on_real_data(ledger):
    forecast = build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), (closure(date(2025, 6, 1), date(2025, 6, 21)),))
    lowest = min(forecast.days, key=lambda day: day.balance_eur)
    assert (lowest.date, lowest.balance_eur) == (date(2025, 7, 1), Decimal("-15728.42"))
    # at the end of the closure the forecast is within 1,000 EUR of what really happened (−8,848.69)
    closure_end = next(day for day in forecast.days if day.date == date(2025, 6, 21))
    assert abs(closure_end.balance_eur - Decimal("-8848.69")) < 1000


def test_closure_as_long_as_the_real_one_reproduces_the_real_trough(ledger):
    """The pizzeria was really closed 1–30 June; forecasting that from 10 May gives about −22k (actual −22,097.60)."""
    forecast = build_forecast(ledger, AS_OF, 60, Decimal("3205.69"), (closure(date(2025, 6, 1), date(2025, 6, 30)),))
    lowest = min(forecast.days, key=lambda day: day.balance_eur)
    assert lowest.date == date(2025, 7, 1)
    assert abs(lowest.balance_eur - Decimal("-22097.60")) < 500
