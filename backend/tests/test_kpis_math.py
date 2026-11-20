"""Mathematical correctness of every KPI on a small synthetic ledger with hand-computed results.

Ledger (anchor 2024-11-30, history 2024-12-01 .. 2025-02-28 = 90 days):
  * daily card settlement +100.00, daily supplier payment -28.00  -> I = 100, V = 28
  * rent -1000 on the 1st, payroll -500 on the 15th (also in the obligation schedule)
  as_of = 2025-02-28:  B0 = anchor + 90*72 - 3*1000 - 3*500 = anchor + 1980
  Forecast (Mar 1..Mar 30): B_d = B0 + 72 d - 1000 [d>=1] - 500 [d>=15]
  Buffer = scheduled next 30d (1500) + 7 * 28 = 1696
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import FIXED_TIME
from kpi_engine import kpis as K
from kpi_engine.engine import compute_metrics
from kpi_engine.models import BalanceAnchor, FixedObligation, Transaction

AS_OF = date(2025, 2, 28)
START = date(2024, 12, 1)


def ledger():
    txs, i, day = [], 0, START
    while day <= AS_OF:
        i += 1
        txs.append(Transaction(day, Decimal("100.00"), "EUR", "CARD SETTLEMENT (DEBIT)", "Daily settlement", id=i))
        i += 1
        txs.append(Transaction(day, Decimal("-28.00"), "EUR", "SUPPLIER PAYMENT", "Invoice", id=i))
        if day.day == 1:
            i += 1
            txs.append(Transaction(day, Decimal("-1000.00"), "EUR", "RENT PAYMENT", "Rent", id=i))
        if day.day == 15:
            i += 1
            txs.append(Transaction(day, Decimal("-500.00"), "EUR", "SALARY PAYMENT", "Salary", id=i))
        day += timedelta(days=1)
    return txs


OBLIGATIONS = [
    FixedObligation("R1", "Landlord", "rent", "operating", 1, "all", "EUR", Decimal("1000.00"), date(2024, 1, 1)),
    FixedObligation("P1", "Employee", "net_payroll", "operating", 15, "all", "EUR", Decimal("500.00"), date(2024, 1, 1)),
]


def run(anchor_balance, **kw):
    anchors = [BalanceAnchor(date(2024, 11, 30), Decimal(anchor_balance))]
    return compute_metrics(AS_OF, ledger(), OBLIGATIONS, anchors, computed_at=FIXED_TIME, **kw)


def meta(m, kpi_id):
    return next(x for x in m["kpi_metadata"] if x["id"] == kpi_id)


def test_healthy_case_all_kpis():
    m = run("5000.00")
    k = m["kpis"]
    assert k["current_cash_balance_eur"] == 6980.00                    # KPI 1
    assert k["forecast_balance_7d_eur"] == 6980 + 7 * 72 - 1000         # KPI 2 = 6484
    assert k["forecast_balance_14d_eur"] == 6980 + 14 * 72 - 1000       # 6988
    assert k["forecast_balance_30d_eur"] == 6980 + 30 * 72 - 1500       # 7640
    assert k["forecast_min_balance_30d_eur"] == 6980 + 72 - 1000        # 6052 on Mar 1
    assert k["minimum_cash_buffer_eur"] == 1500 + 7 * 28                # KPI 3 = 1696
    assert k["liquidity_gap_30d_eur"] == 0.0                            # KPI 4
    assert k["projected_overdraft_30d_eur"] == 0.0
    assert k["deployable_cash_surplus_eur"] == 6052 - 1696              # KPI 5 = 4356
    assert k["net_cash_flow_7d_eur"] == 7 * 72                          # KPI 6 = 504
    assert k["net_cash_flow_30d_eur"] == 30 * 72 - 1500                 # 660
    assert k["cash_inflow_trend_pct"] == 0.0                            # KPI 7 (identical windows)
    assert k["cash_outflow_trend_pct"] == 0.0
    assert k["fixed_cost_coverage_ratio"] == round(6980 / 1500, 2)      # KPI 8 = 4.65
    assert meta(m, "fixed_cost_coverage_ratio")["details"]["operating_coverage_ratio"] == round((3000 - 840) / 1500, 2)
    assert meta(m, "forecast_min_balance_30d_eur")["details"]["min_balance_date"] == "2025-03-01"
    assert m["overall_status"] == "HEALTHY"
    assert m["signals"]["scenario_hint"] == "OPTIMAL"  # surplus 4356 < 10000 threshold
    assert m["data_quality"]["status"] == "OK"


def test_forecast_daily_path_is_exact():
    m = run("5000.00")
    daily = m["forecast"]["daily"]
    assert len(daily) == 31 and daily[0]["balance_eur"] == 6980.0
    for prev, cur in zip(daily, daily[1:]):
        assert round(prev["balance_eur"] + cur["inflow_eur"] - cur["variable_outflow_eur"] - cur["scheduled_outflow_eur"], 2) == cur["balance_eur"]
    assert daily[1]["scheduled_outflow_eur"] == 1000.0 and daily[15]["scheduled_outflow_eur"] == 500.0


def test_warning_gap_without_overdraft():
    m = run("520.00")  # B0 = 2500, B1 = 1572 < buffer 1696
    k = m["kpis"]
    assert k["current_cash_balance_eur"] == 2500.0
    assert k["forecast_min_balance_30d_eur"] == 1572.0
    assert k["liquidity_gap_30d_eur"] == 1696 - 1572
    assert k["projected_overdraft_30d_eur"] == 0.0
    gap = meta(m, "liquidity_gap_30d_eur")
    assert gap["status"] == "WARNING"
    assert gap["details"]["first_breach_date"] == "2025-03-01"
    assert gap["details"]["days_until_breach"] == 1
    assert gap["details"]["financing_required"] is False
    assert meta(m, "deployable_cash_surplus_eur")["status"] == "WARNING"
    assert m["signals"]["scenario_hint"] == "LIQUIDITY_SHORTAGE"
    assert m["signals"]["liquidity_shortage"]["severity"] == "WARNING"


def test_critical_shortage_with_overdraft():
    m = run("-2000.00")  # B0 = -20, B1 = -948
    k = m["kpis"]
    assert k["current_cash_balance_eur"] == -20.0
    assert k["forecast_min_balance_30d_eur"] == -948.0
    assert k["liquidity_gap_30d_eur"] == 1696 + 948
    assert k["projected_overdraft_30d_eur"] == 948.0
    assert k["deployable_cash_surplus_eur"] == 0.0
    s = m["signals"]["liquidity_shortage"]
    assert s["detected"] and s["severity"] == "CRITICAL" and s["financing_required"]
    assert s["first_breach_date"] == AS_OF.isoformat() and s["days_until_breach"] == 0
    assert meta(m, "current_cash_balance_eur")["status"] == "CRITICAL"
    assert meta(m, "fixed_cost_coverage_ratio")["status"] == "CRITICAL"


def test_excess_liquidity_signal():
    m = run("20000.00")  # B0 = 21980, min = 21052, surplus = 19356
    assert m["kpis"]["deployable_cash_surplus_eur"] == 21052 - 1696
    assert m["signals"]["scenario_hint"] == "EXCESS_LIQUIDITY"
    assert m["signals"]["excess_liquidity"]["detected"] is True


@pytest.mark.parametrize("cur,prev,expected", [
    (Decimal("110"), Decimal("100"), Decimal("10")),
    (Decimal("80"), Decimal("100"), Decimal("-20")),
    (Decimal("5"), Decimal("0"), None),
])
def test_pct_change(cur, prev, expected):
    assert K.pct_change(cur, prev) == expected


def test_trend_detects_declining_inflows():
    txs = [t for t in ledger() if not (t.transaction_type.startswith("CARD") and t.booking_date > date(2025, 1, 29))]
    # Last 30 days: half the revenue
    txs += [Transaction(d, Decimal("50.00"), "EUR", "CARD SETTLEMENT (DEBIT)", id=10_000 + i)
            for i, d in enumerate(date(2025, 1, 30) + timedelta(days=n) for n in range(30))]
    m = compute_metrics(AS_OF, txs, OBLIGATIONS, [BalanceAnchor(date(2024, 11, 30), Decimal("5000"))], computed_at=FIXED_TIME)
    assert m["kpis"]["cash_inflow_trend_pct"] == -50.0
    assert meta(m, "cash_inflow_trend_pct")["status"] == "CRITICAL"
    assert meta(m, "cash_inflow_trend_pct")["details"]["direction"] == "DECLINING"


def test_status_rules():
    cfg_cov = K.status_coverage
    from kpi_engine.config import EngineConfig
    cfg = EngineConfig()
    assert cfg_cov(Decimal("1.5"), cfg) == "HEALTHY"
    assert cfg_cov(Decimal("1.0"), cfg) == "WARNING"
    assert cfg_cov(Decimal("0.99"), cfg) == "CRITICAL"
    assert K.status_buffer(Decimal("50"), Decimal("100"), cfg.buffer_funding_warning) == "WARNING"
    assert K.status_buffer(Decimal("49"), Decimal("100"), cfg.buffer_funding_warning) == "CRITICAL"
    nf = K.NetFlow(30, AS_OF, AS_OF, Decimal("900"), Decimal("-1000"))
    assert K.status_net_flow(nf, cfg.burn_ratio_critical_pct) == "WARNING"   # burn 10 %
    nf = K.NetFlow(30, AS_OF, AS_OF, Decimal("899"), Decimal("-1000"))
    assert K.status_net_flow(nf, cfg.burn_ratio_critical_pct) == "CRITICAL"  # burn 10.1 %


def test_classification_sign_sanity():
    m = {"SUPPLIER PAYMENT": "VARIABLE_OUTFLOW", "CREDIT CARD PAYOUT": "OPERATING_INFLOW"}
    assert K.classify("SUPPLIER PAYMENT", Decimal("10"), m) == "OTHER_INFLOW"   # refund
    assert K.classify("CREDIT CARD PAYOUT", Decimal("-10"), m) == "OTHER_OUTFLOW"  # charge-back
    assert K.classify("supplier payment ", Decimal("-10"), m) == "VARIABLE_OUTFLOW"
    assert K.classify("LOTTERY WIN", Decimal("10"), m) is None
