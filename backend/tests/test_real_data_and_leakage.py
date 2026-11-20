"""Validation on the real 2025 statement: anchors, demo dates, no future-data leakage, JSON contract."""
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from conftest import FIXED_TIME
from kpi_engine.engine import compute_metrics
from kpi_engine.fx import extract_rate
from kpi_engine.models import Transaction

STATUSES = {"HEALTHY", "WARNING", "CRITICAL"}


def compute(as_of, statement, obligations, anchors, **kw):
    return compute_metrics(as_of, statement, obligations, anchors, computed_at=FIXED_TIME, **kw)


def strip_volatile(m):
    m = json.loads(json.dumps(m))
    m["data_quality"]["flags"] = [f for f in m["data_quality"]["flags"] if f["code"] != "FUTURE_TRANSACTIONS_IGNORED"]
    m["data_quality"]["status"] = None
    return m


# --------------------------------------------------------------------------- balances

def test_documented_balances_reproduced_from_opening_anchor_only(statement, obligations, anchors):
    opening_only = [a for a in anchors if a.is_derived]
    for as_of, documented in [(date(2025, 6, 30), -20098.69), (date(2025, 12, 31), 33266.35)]:
        m = compute(as_of, statement, obligations, opening_only)
        assert m["kpis"]["current_cash_balance_eur"] == documented


def test_anchor_reconciliation_has_zero_difference(statement, obligations, anchors):
    m = compute(date(2025, 12, 31), statement, obligations, anchors)
    rec = m["data_quality"]["balance_source"]["reconciliation"]
    assert [r["difference_eur"] for r in rec] == [0.0, 0.0]
    assert "BALANCE_RECONCILIATION_MISMATCH" not in {f["code"] for f in m["data_quality"]["flags"]}


def test_daily_balance_changes_equal_daily_net_flow(statement, obligations, anchors):
    """Replay every day of 2025: B0(d) - B0(d-1) must equal the EUR net of day d."""
    def eur(t):
        if t.currency == "EUR":
            return t.amount
        return (t.amount * extract_rate(t.payment_reference, t.currency)).quantize(Decimal("0.01"), ROUND_HALF_UP)

    prev = compute(date(2024, 12, 31), statement, obligations, anchors)["kpis"]["current_cash_balance_eur"]
    assert prev == 2500.0
    day = date(2025, 1, 1)
    while day <= date(2025, 12, 31):
        bal = compute(day, statement, obligations, anchors)["kpis"]["current_cash_balance_eur"]
        expected = sum((eur(t) for t in statement if t.booking_date == day), Decimal("0"))
        assert Decimal(str(bal)) - Decimal(str(prev)) == expected, day
        prev = bal
        day += timedelta(days=1)


# --------------------------------------------------------------------------- demo dates (SPEC.md DoD)

def test_demo_2025_06_30_liquidity_shortage(statement, obligations, anchors):
    m = compute(date(2025, 6, 30), statement, obligations, anchors)
    k, s = m["kpis"], m["signals"]
    assert k["current_cash_balance_eur"] == -20098.69
    assert s["scenario_hint"] == "LIQUIDITY_SHORTAGE"
    assert s["liquidity_shortage"]["severity"] == "CRITICAL"
    assert s["liquidity_shortage"]["financing_required"] is True
    assert k["liquidity_gap_30d_eur"] == round(k["minimum_cash_buffer_eur"] - k["forecast_min_balance_30d_eur"], 2)
    assert k["deployable_cash_surplus_eur"] == 0.0
    assert k["cash_inflow_trend_pct"] == -100.0
    assert "REVENUE_INTERRUPTION" in {f["code"] for f in m["data_quality"]["flags"]}


def test_demo_2025_12_31_no_shortage_and_positive_surplus(statement, obligations, anchors):
    m = compute(date(2025, 12, 31), statement, obligations, anchors)
    k = m["kpis"]
    assert k["current_cash_balance_eur"] == 33266.35
    assert k["liquidity_gap_30d_eur"] == 0.0
    assert k["deployable_cash_surplus_eur"] > 0
    assert k["deployable_cash_surplus_eur"] == round(k["forecast_min_balance_30d_eur"] - k["minimum_cash_buffer_eur"], 2)
    assert k["deployable_cash_surplus_eur"] < k["current_cash_balance_eur"] - k["minimum_cash_buffer_eur"]
    assert m["overall_status"] == "HEALTHY"
    # signal follows the documented threshold, whatever the value is
    expected = "EXCESS_LIQUIDITY" if k["deployable_cash_surplus_eur"] >= 10000 else "OPTIMAL"
    assert m["signals"]["scenario_hint"] == expected


# --------------------------------------------------------------------------- leakage

@pytest.mark.parametrize("as_of", [date(2025, 3, 14), date(2025, 6, 30), date(2025, 9, 1), date(2025, 11, 27)])
def test_future_transactions_do_not_change_results(statement, obligations, anchors, as_of):
    past_only = [t for t in statement if t.booking_date <= as_of]
    poisoned = list(statement) + [
        Transaction(as_of + timedelta(days=1), Decimal("1000000.00"), "EUR", "CARD SETTLEMENT (DEBIT)", id=999_999),
        Transaction(as_of + timedelta(days=2), Decimal("-5000.00"), "CHF", "INTERNATIONAL WIRE TRANSFER",
                    "Silent Partnership (5000 CHF @ 9.999 EUR/CHF)", id=999_998),
    ]
    a = strip_volatile(compute(as_of, past_only, obligations, anchors))
    b = strip_volatile(compute(as_of, poisoned, obligations, anchors))
    assert a == b
    assert "FUTURE_TRANSACTIONS_IGNORED" in {f["code"] for f in compute(as_of, poisoned, obligations, anchors)["data_quality"]["flags"]}


def test_future_anchors_are_ignored(statement, obligations, anchors):
    as_of = date(2025, 5, 20)
    with_future = compute(as_of, statement, obligations, anchors)
    tampered = [replace(a, balance=a.balance + 99999) if a.anchor_date > as_of else a for a in anchors]
    assert compute(as_of, statement, obligations, tampered)["kpis"] == with_future["kpis"]


def test_future_fx_rates_not_used(statement, obligations, anchors):
    # As of 2025-06-30 the July tranche must use June's rate (1.050), not July's (1.043).
    m = compute(date(2025, 6, 30), statement, obligations, anchors)
    assert m["data_quality"]["fx"]["latest_rates"]["CHF"]["eur_per_unit"] == 1.05


# --------------------------------------------------------------------------- contract

def test_metrics_contract(statement, obligations, anchors):
    m = compute(date(2025, 12, 31), statement, obligations, anchors)
    for key in ("schema_version", "as_of_date", "currency", "overall_status", "kpis", "kpi_metadata",
                "forecast", "signals", "data_quality", "engine"):
        assert key in m
    assert m["as_of_date"] == "2025-12-31" and m["currency"] == "EUR"
    assert [x["id"] for x in m["kpi_metadata"]] == list(m["kpis"].keys())
    assert sorted({x["kpi_number"] for x in m["kpi_metadata"]}) == list(range(1, 9))
    for x in m["kpi_metadata"]:
        assert {"id", "label", "value", "unit", "status", "is_estimate", "details"} <= set(x)
        assert x["status"] in STATUSES and x["unit"] in {"EUR", "%", "ratio"}
        assert x["value"] == m["kpis"][x["id"]]
        assert x["value"] is None or isinstance(x["value"], float)
    assert m["overall_status"] in STATUSES
    assert m["signals"]["scenario_hint"] in {"LIQUIDITY_SHORTAGE", "EXCESS_LIQUIDITY", "OPTIMAL"}
    assert m["data_quality"]["status"] in {"OK", "DEGRADED", "UNRELIABLE"}
    assert len(m["forecast"]["daily"]) == 31
    assert json.loads(json.dumps(m)) == m  # pure JSON types


def test_deterministic(statement, obligations, anchors):
    a = compute(date(2025, 8, 17), statement, obligations, anchors)
    b = compute(date(2025, 8, 17), list(reversed(statement)), obligations, list(reversed(anchors)))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
