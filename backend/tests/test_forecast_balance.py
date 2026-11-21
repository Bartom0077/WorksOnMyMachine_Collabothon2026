"""forecast_balance: arbitrary-horizon balance forecast (KPI 2 method) as of a date.

Uses the synthetic ledger from test_kpis_math: I = 100, V = 28 (net +72/day), rent 1000 on the 1st,
payroll 500 on the 15th, as_of = 2025-02-28, anchor 5000 -> B0 = 6980, buffer 1696.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import FIXED_TIME
from kpi_engine import forecast_balance
from kpi_engine.engine import MAX_FORECAST_DAYS, compute_metrics
from kpi_engine.models import BalanceAnchor, Transaction
from test_kpis_math import AS_OF, OBLIGATIONS, ledger

ANCHORS = [BalanceAnchor(date(2024, 11, 30), Decimal("5000.00"))]


def fc(days, txs=None, obligations=OBLIGATIONS, as_of=AS_OF, anchors=ANCHORS):
    return forecast_balance(as_of, days, txs if txs is not None else ledger(), obligations, anchors,
                            computed_at=FIXED_TIME)


def test_45_day_forecast_is_exact():
    f = fc(45)  # Mar 1 .. Apr 14: rent Mar 1 + Apr 1, payroll Mar 15
    assert f["target_date"] == "2025-04-14"
    assert f["opening_balance_eur"] == 6980.0
    assert f["forecast_balance_eur"] == 6980 + 45 * 72 - 2 * 1000 - 500   # 7720
    assert f["summary"]["min_balance_eur"] == 6980 + 72 - 1000             # 6052
    assert f["summary"]["min_balance_date"] == "2025-03-01"
    assert f["minimum_cash_buffer_eur"] == 1696.0
    assert f["status"] == "HEALTHY"
    assert len(f["daily"]) == 46
    assert [s["date"] for s in f["scheduled_obligations"]] == ["2025-03-01", "2025-03-15", "2025-04-01"]
    assert f["scheduled_obligations_total_eur"] == 2500.0
    assert f["data_quality"]["status"] == "OK"


@pytest.mark.parametrize("days", [7, 14, 30])
def test_matches_kpi_snapshot_horizons(days):
    m = compute_metrics(AS_OF, ledger(), OBLIGATIONS, ANCHORS, computed_at=FIXED_TIME)
    f = fc(days)
    assert f["forecast_balance_eur"] == m["kpis"][f"forecast_balance_{days}d_eur"]
    assert f["minimum_cash_buffer_eur"] == m["kpis"]["minimum_cash_buffer_eur"]
    sens = m["forecast"]["sensitivity"]["horizons"]
    assert {k: v for k, v in f["sensitivity"].items() if k != "description"} == \
        next(h for h in sens if h["horizon_days"] == days)
    base = next(x for x in m["kpi_metadata"] if x["id"] == "forecast_balance_30d_eur")["details"]["horizons"]
    assert f["summary"] == next(h for h in base if h["horizon_days"] == days)
    assert f["daily"] == m["forecast"]["daily"][: days + 1]


def test_matches_kpi_snapshot_on_real_data(statement, obligations, anchors):
    as_of = date(2025, 6, 30)
    m = compute_metrics(as_of, statement, obligations, anchors, computed_at=FIXED_TIME)
    f = forecast_balance(as_of, 30, statement, obligations, anchors, computed_at=FIXED_TIME)
    assert f["forecast_balance_eur"] == m["kpis"]["forecast_balance_30d_eur"]
    assert f["summary"]["min_balance_eur"] == m["kpis"]["forecast_min_balance_30d_eur"]
    assert f["daily"] == m["forecast"]["daily"]


def test_short_horizon_keeps_full_buffer_and_filters_schedule():
    f = fc(3)
    assert f["forecast_balance_eur"] == 6980 + 3 * 72 - 1000
    assert f["minimum_cash_buffer_eur"] == 1696.0  # buffer still uses the 30-day obligation window
    assert [s["expense_id"] for s in f["scheduled_obligations"]] == ["R1"]


def test_future_transactions_are_ignored():
    future = [Transaction(AS_OF + timedelta(days=d), Decimal("99999.00"), "EUR", "CARD SETTLEMENT (DEBIT)", id=900 + d)
              for d in range(1, 10)]
    f = fc(20, txs=ledger() + future)
    clean = fc(20)
    assert f["daily"] == clean["daily"]
    assert "FUTURE_TRANSACTIONS_IGNORED" in {x["code"] for x in f["data_quality"]["flags"]}


def test_without_schedule_fixed_costs_are_averaged():
    f = fc(10, obligations=None)
    assert f["scheduled_obligations"] == []
    assert "OBLIGATION_SCHEDULE_MISSING" in {x["code"] for x in f["data_quality"]["flags"]}
    # fixed outflows of the last 28 days (Feb 1 rent + Feb 15 payroll) spread per day
    rr = f["run_rates"]["base"]
    assert rr["daily_fixed_outflow_historical_eur"] == round(1500 / 28, 2)


def test_long_horizon_flag():
    assert "LONG_FORECAST_HORIZON" not in {x["code"] for x in fc(90)["data_quality"]["flags"]}
    f = fc(120)
    assert "LONG_FORECAST_HORIZON" in {x["code"] for x in f["data_quality"]["flags"]}
    assert len(f["daily"]) == 121


@pytest.mark.parametrize("days", [0, -5, MAX_FORECAST_DAYS + 1, 2.5, "30", True])
def test_invalid_days_rejected(days):
    with pytest.raises(ValueError):
        fc(days)
