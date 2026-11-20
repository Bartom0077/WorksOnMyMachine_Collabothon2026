"""Missing or incomplete inputs: the engine must degrade gracefully and say so, never invent data."""
from datetime import date
from decimal import Decimal

from conftest import FIXED_TIME
from kpi_engine.engine import compute_metrics
from kpi_engine.models import BalanceAnchor, Transaction


def codes(m):
    return {f["code"]: f["severity"] for f in m["data_quality"]["flags"]}


def test_no_anchor_gives_relative_balance_and_critical_flag(statement, obligations):
    m = compute_metrics(date(2025, 6, 30), statement, obligations, [], computed_at=FIXED_TIME)
    assert m["kpis"]["current_cash_balance_eur"] == -20098.69 - 2500.00  # sum of flows only
    assert codes(m)["BALANCE_ANCHOR_MISSING"] == "CRITICAL"
    assert m["data_quality"]["status"] == "UNRELIABLE"
    assert m["data_quality"]["balance_source"]["method"] == "SUM_OF_TRANSACTIONS"


def test_no_obligation_schedule_falls_back_to_historical_fixed_outflows(statement, anchors):
    m = compute_metrics(date(2025, 12, 31), statement, None, anchors, computed_at=FIXED_TIME)
    assert codes(m)["OBLIGATION_SCHEDULE_MISSING"] == "WARNING"
    assert m["forecast"]["scheduled_obligations"] == []
    base = m["forecast"]["run_rates"]["base"]
    # fixed-type outflows are folded into the daily outflow rate
    assert m["kpi_metadata"][1]["value"] is not None
    daily = m["forecast"]["daily"][1]
    assert daily["variable_outflow_eur"] == round(base["daily_variable_outflow_eur"] + base["daily_fixed_outflow_historical_eur"], 2)
    assert m["kpis"]["minimum_cash_buffer_eur"] > 0


def test_no_transactions_at_all():
    m = compute_metrics(date(2025, 1, 1), [], [], [BalanceAnchor(date(2024, 12, 31), Decimal("2500"))], computed_at=FIXED_TIME)
    assert m["kpis"]["current_cash_balance_eur"] == 2500.0
    assert m["kpis"]["forecast_balance_30d_eur"] == 2500.0
    assert m["kpis"]["cash_inflow_trend_pct"] is None
    assert m["kpis"]["fixed_cost_coverage_ratio"] is None
    assert "NO_TRANSACTIONS" in codes(m) and "NO_FIXED_OBLIGATIONS" in codes(m)


def test_empty_database_is_unreliable():
    m = compute_metrics(date(2025, 1, 1), [], None, [], computed_at=FIXED_TIME)
    assert m["data_quality"]["status"] == "UNRELIABLE"
    assert m["kpis"]["current_cash_balance_eur"] == 0.0


def test_short_history_flags_insufficient(statement, obligations, anchors):
    m = compute_metrics(date(2025, 1, 10), statement, obligations, anchors, computed_at=FIXED_TIME)
    assert codes(m)["INSUFFICIENT_HISTORY"] == "WARNING"
    assert m["forecast"]["run_rates"]["base"]["days_covered"] == 10
    assert m["kpis"]["cash_inflow_trend_pct"] is None


def test_stale_data(statement, obligations, anchors):
    m = compute_metrics(date(2026, 2, 15), statement, obligations, anchors, computed_at=FIXED_TIME)
    assert codes(m)["STALE_DATA"] == "WARNING"
    assert m["kpis"]["current_cash_balance_eur"] == 33266.35  # last known balance, nothing invented


def test_unknown_transaction_type_is_flagged_and_not_projected_as_revenue():
    txs = [Transaction(date(2025, 1, d), Decimal("100"), "EUR", "CARD SETTLEMENT (DEBIT)", id=d) for d in range(1, 29)]
    txs.append(Transaction(date(2025, 1, 28), Decimal("5000"), "EUR", "LOAN DISBURSEMENT", id=100))
    m = compute_metrics(date(2025, 1, 28), txs, [], [BalanceAnchor(date(2024, 12, 31), Decimal("0"))], computed_at=FIXED_TIME)
    assert "UNCLASSIFIED_TRANSACTIONS" in codes(m)
    assert m["kpis"]["current_cash_balance_eur"] == 7800.0
    assert m["forecast"]["run_rates"]["base"]["daily_operating_inflow_eur"] == 100.0


def test_reconciliation_mismatch_is_flagged(statement, obligations, anchors):
    bad = [a if a.anchor_date.month != 6 else BalanceAnchor(a.anchor_date, Decimal("-20000.00")) for a in anchors]
    m = compute_metrics(date(2025, 7, 10), statement, obligations, bad, computed_at=FIXED_TIME)
    assert codes(m)["BALANCE_RECONCILIATION_MISMATCH"] == "WARNING"
