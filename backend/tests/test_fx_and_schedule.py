"""Multi-currency handling and obligation schedule projection."""
from datetime import date
from decimal import Decimal

from conftest import FIXED_TIME
from kpi_engine.engine import compute_metrics
from kpi_engine.fx import (FX_EMBEDDED, FX_LAST_OBSERVED, FX_MISSING, FX_NATIVE, FX_REFERENCE, FxConverter,
                           extract_rate)
from kpi_engine.models import BalanceAnchor, FixedObligation, Transaction
from kpi_engine.schedule import occurrence_dates, project_schedule


def chf(day, amount, ref, i):
    return Transaction(day, Decimal(amount), "CHF", "INTERNATIONAL WIRE TRANSFER", ref, "Luigi", id=i)


# --------------------------------------------------------------------------- FX

def test_extract_rate_formats():
    ref = "Silent Partnership Capital Tranche 01/12 (4166.67 CHF @ 1.042 EUR/CHF) - Luigi's Ristorante"
    assert extract_rate(ref, "CHF") == Decimal("1.042")
    assert extract_rate("x @ 1,05 eur / chf", "CHF") == Decimal("1.05")
    assert extract_rate(ref, "USD") is None
    assert extract_rate(None, "CHF") is None


def test_conversion_and_fallback_order():
    fx = FxConverter(reference_rates={"CHF": Decimal("1.044")})
    out = fx.convert_all([
        chf(date(2025, 1, 10), "-100.00", "no rate here", 1),                 # before any observed rate
        chf(date(2025, 1, 15), "-4166.67", "(4166.67 CHF @ 1.042 EUR/CHF)", 2),
        chf(date(2025, 1, 20), "-100.00", "no rate here", 3),
        Transaction(date(2025, 1, 21), Decimal("12.34"), "EUR", "CARD SETTLEMENT (DEBIT)", id=4),
    ])
    assert [c.fx_source for c in out] == [FX_REFERENCE, FX_EMBEDDED, FX_LAST_OBSERVED, FX_NATIVE]
    assert out[0].amount_eur == Decimal("-104.40")
    assert out[1].amount_eur == Decimal("-4341.67")  # 4166.67 * 1.042 = 4341.67014 -> half-up
    assert out[2].amount_eur == Decimal("-104.20")
    assert out[3].amount_eur == Decimal("12.34")


def test_missing_rate_excludes_transaction_and_flags_critical():
    txs = [Transaction(date(2025, 1, 2), Decimal("1000"), "EUR", "CARD SETTLEMENT (DEBIT)", id=1),
           chf(date(2025, 1, 3), "-500.00", "no rate", 2)]
    m = compute_metrics(date(2025, 1, 3), txs, [], [BalanceAnchor(date(2025, 1, 1), Decimal("0"))], computed_at=FIXED_TIME)
    assert m["kpis"]["current_cash_balance_eur"] == 1000.0
    codes = {f["code"]: f["severity"] for f in m["data_quality"]["flags"]}
    assert codes["FX_RATE_MISSING"] == "CRITICAL"
    assert m["data_quality"]["status"] == "UNRELIABLE"
    assert m["data_quality"]["coverage"]["transactions_excluded"] == 1


def test_real_chf_rows_converted_at_embedded_rate(statement, obligations, anchors):
    m = compute_metrics(date(2025, 12, 31), statement, obligations, anchors, computed_at=FIXED_TIME)
    fx = m["data_quality"]["fx"]
    assert fx["conversions_by_source"]["EMBEDDED_RATE"] == 12
    assert fx["conversions_by_source"]["MISSING"] == 0
    assert fx["latest_rates"]["CHF"] == {"eur_per_unit": 1.047, "observed_on": "2025-12-15"}


# --------------------------------------------------------------------------- schedule

def ob(**kw):
    base = dict(expense_id="X", payee="p", category="c", cashflow_class="operating", payment_day=1,
                payment_months="all", currency="EUR", amount_per_payment=Decimal("100"), valid_from=date(2025, 1, 1))
    base.update(kw)
    return FixedObligation(**base)


def test_occurrences_clamp_month_end_and_quarterly():
    assert occurrence_dates(ob(payment_day=31), date(2025, 2, 1), date(2025, 4, 30)) == [
        date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30)]
    assert occurrence_dates(ob(payment_day=10, payment_months="03;06;09;12"), date(2025, 1, 1), date(2025, 12, 31)) == [
        date(2025, 3, 10), date(2025, 6, 10), date(2025, 9, 10), date(2025, 12, 10)]
    assert occurrence_dates(ob(valid_from=date(2025, 3, 5)), date(2025, 1, 1), date(2025, 4, 30)) == [date(2025, 4, 1)]


def test_rollover_beyond_valid_to_is_flagged():
    res = project_schedule([ob(valid_to=date(2025, 12, 31))], [], date(2025, 12, 31), 30, FxConverter())
    assert [p.payment_date for p in res.payments] == [date(2026, 1, 1)]
    assert res.payments[0].extrapolated and res.extrapolated_ids == ["X"]


def test_commitment_remaining_caps_and_completes():
    inv = ob(expense_id="INV", currency="CHF", amount_per_payment=Decimal("4166.67"), payment_day=15,
             total_commitment=Decimal("10000.00"), match_keyword="Silent Partnership",
             estimated_eur_per_payment=Decimal("4350.00"))
    paid = [chf(date(2025, 1, 15), "-4166.67", "Silent Partnership 1 (4166.67 CHF @ 1.040 EUR/CHF)", 1),
            chf(date(2025, 2, 15), "-4166.67", "Silent Partnership 2 (4166.67 CHF @ 1.050 EUR/CHF)", 2)]
    fx = FxConverter()
    fx.convert_all(paid)
    res = project_schedule([inv], paid, date(2025, 2, 28), 60, fx)
    # remaining 10000 - 8333.34 = 1666.66 -> one capped tranche on Mar 15, nothing on Apr 15
    assert [(p.payment_date, p.amount, p.amount_eur) for p in res.payments] == [
        (date(2025, 3, 15), Decimal("1666.66"), Decimal("1749.99"))]
    assert res.open_commitments[0]["remaining"] == 1666.66
    # A tranche booked after as_of must not count as paid (no leakage)
    res_early = project_schedule([inv], paid, date(2025, 1, 31), 30, fx)
    assert res_early.open_commitments[0]["paid_to_date"] == 4166.67


def test_real_investment_commitment_completed_by_year_end(statement, obligations, anchors):
    m = compute_metrics(date(2025, 12, 31), statement, obligations, anchors, computed_at=FIXED_TIME)
    assert [c["expense_id"] for c in m["forecast"]["completed_commitments"]] == ["INV001"]
    assert all(p["expense_id"] != "INV001" for p in m["forecast"]["scheduled_obligations"])
    m = compute_metrics(date(2025, 6, 30), statement, obligations, anchors, computed_at=FIXED_TIME)
    inv = [p for p in m["forecast"]["scheduled_obligations"] if p["expense_id"] == "INV001"]
    assert inv == [{"date": "2025-07-15", "expense_id": "INV001", "payee": "Luigi Amalfi / Swiss restaurant",
                    "category": "investment_outflow", "amount": 4166.67, "currency": "CHF", "amount_eur": 4375.0,
                    "fx_source": "LAST_OBSERVED_RATE", "extrapolated": False}]


def test_monthly_run_rate_quarterly_and_completed_commitments(obligations, statement):
    fx = FxConverter()
    fx.convert_all([t for t in statement if t.booking_date <= date(2025, 12, 31)])
    res = project_schedule(obligations, statement, date(2025, 12, 31), 30, fx)
    items = {i["expense_id"]: i["monthly_equivalent_eur"] for i in res.monthly_items}
    assert items["TAX002"] == 383.33 and items["TAX003"] == 75.0  # 1150*4/12, 225*4/12
    assert "INV001" not in items  # commitment completed
    assert res.monthly_total_eur == Decimal("17038.33")


def test_buffer_includes_payroll_just_outside_the_30_day_window(statement, obligations, anchors):
    """2025-12-28: the window Dec 29..Jan 27 misses the Jan 28 payroll; the buffer must not drop."""
    on_28 = compute_metrics(date(2025, 12, 28), statement, obligations, anchors, computed_at=FIXED_TIME)
    on_27 = compute_metrics(date(2025, 12, 27), statement, obligations, anchors, computed_at=FIXED_TIME)
    d28 = next(x for x in on_28["kpi_metadata"] if x["id"] == "minimum_cash_buffer_eur")["details"]
    assert d28["scheduled_obligations_next_30d_eur"] < d28["monthly_fixed_obligations_eur"]
    assert d28["monthly_fixed_obligations_eur"] == 17038.33
    assert abs(on_28["kpis"]["minimum_cash_buffer_eur"] - on_27["kpis"]["minimum_cash_buffer_eur"]) < 100
