"""Assembles the eight KPIs into the ``kpi_snapshots.metrics`` document (KPI_SPEC.md §3).

``compute_metrics`` is a pure function of its inputs: it never touches the database or the clock
(``computed_at`` can be injected), so results are reproducible and unit-testable.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from . import kpis as K
from .config import (
    ENGINE_NAME,
    ENGINE_VERSION,
    FIXED_OUTFLOW,
    OPERATING_INFLOW,
    REPORTING_CURRENCY,
    SCHEMA_VERSION,
    VARIABLE_OUTFLOW,
    EngineConfig,
)
from .fx import FX_EMBEDDED, FX_LAST_OBSERVED, FX_MISSING, FX_NATIVE, FX_REFERENCE, FxConverter
from .models import BalanceAnchor, FixedObligation, Transaction
from .money import cents, money_json, pct_json, safe_div
from .schedule import ScheduleResult, project_schedule

INFO = "INFO"
_SEVERITY_RANK = {INFO: 0, K.WARNING: 1, K.CRITICAL: 2}

LABELS = {
    "current_cash_balance_eur": (1, "current_cash_balance", "Saldo bieżące (EUR)", "Current cash balance", "EUR"),
    "forecast_balance_7d_eur": (2, "forecast_cash_balance", "Prognoza salda za 7 dni", "Forecast balance in 7 days", "EUR"),
    "forecast_balance_14d_eur": (2, "forecast_cash_balance", "Prognoza salda za 14 dni", "Forecast balance in 14 days", "EUR"),
    "forecast_balance_30d_eur": (2, "forecast_cash_balance", "Prognoza salda za 30 dni", "Forecast balance in 30 days", "EUR"),
    "forecast_min_balance_30d_eur": (2, "forecast_cash_balance", "Najniższe prognozowane saldo (30 dni)", "Lowest forecast balance (30 days)", "EUR"),
    "minimum_cash_buffer_eur": (3, "minimum_cash_buffer", "Minimalny bufor gotówki", "Minimum cash buffer", "EUR"),
    "liquidity_gap_30d_eur": (4, "liquidity_gap", "Luka płynności (30 dni)", "Liquidity gap (30 days)", "EUR"),
    "projected_overdraft_30d_eur": (4, "liquidity_gap", "Prognozowany debet (30 dni)", "Projected overdraft (30 days)", "EUR"),
    "deployable_cash_surplus_eur": (5, "deployable_cash_surplus", "Nadwyżka gotówki do ulokowania", "Deployable cash surplus", "EUR"),
    "net_cash_flow_7d_eur": (6, "net_cash_flow", "Przepływ netto (7 dni)", "Net cash flow (7 days)", "EUR"),
    "net_cash_flow_30d_eur": (6, "net_cash_flow", "Przepływ netto (30 dni)", "Net cash flow (30 days)", "EUR"),
    "cash_inflow_trend_pct": (7, "cash_flow_trend", "Trend wpływów (30 vs poprzednie 30 dni)", "Inflow trend (last 30d vs prior 30d)", "%"),
    "cash_outflow_trend_pct": (7, "cash_flow_trend", "Trend wydatków (30 vs poprzednie 30 dni)", "Outflow trend (last 30d vs prior 30d)", "%"),
    "fixed_cost_coverage_ratio": (8, "fixed_cost_coverage", "Pokrycie kosztów stałych", "Fixed cost coverage ratio", "ratio"),
}
ESTIMATE_GROUPS = {2, 3, 4, 5, 8}
FORECAST_METHOD = "B_d = B_{d-1} + daily operating inflow - daily variable outflow - scheduled obligations due on d"
MAX_FORECAST_DAYS = 365


class Flags:
    def __init__(self):
        self.items: List[dict] = []

    def add(self, code: str, severity: str, message: str, affected: Sequence[str] = ()):
        self.items.append({"code": code, "severity": severity, "message": message, "affected_kpis": list(affected)})

    def status(self) -> str:
        worst = max((_SEVERITY_RANK[f["severity"]] for f in self.items), default=0)
        return {0: "OK", 1: "DEGRADED", 2: "UNRELIABLE"}[worst]


def _d(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _reference_rates(obligations: Sequence[FixedObligation]) -> Dict[str, Decimal]:
    rates: Dict[str, Decimal] = {}
    for ob in obligations:
        ccy = ob.currency.upper()
        if ccy != REPORTING_CURRENCY and ob.estimated_eur_per_payment and ob.amount_per_payment:
            rates.setdefault(ccy, ob.estimated_eur_per_payment / ob.amount_per_payment)
    return rates


@dataclass
class _Inputs:
    """Everything derived from the raw inputs before the forecast path is built (steps 1-7)."""
    flags: Flags
    history: List[Transaction]
    flows: List[K.Flow]
    first_tx: Optional[date]
    last_tx: Optional[date]
    missing_fx: list
    fx: FxConverter
    fx_counts: Counter
    balance: Decimal
    balance_source: dict
    base_rr: K.RunRates
    alt_rr: K.RunRates
    has_schedule: bool
    sched: ScheduleResult
    scheduled_by_day: Dict[date, Decimal]
    fixed_monthly: Decimal
    scheduled_next: Optional[Decimal]
    buffer: Decimal

    def daily_outflow(self, rr: K.RunRates) -> Decimal:
        """Without a schedule, fixed-type outflows are projected as a daily average (KPI_SPEC.md §0.6)."""
        return rr.daily_variable_outflow + (Decimal("0") if self.has_schedule else rr.daily_fixed_outflow_hist)


def _prepare(
    as_of: date,
    transactions: Sequence[Transaction],
    obligations: Optional[Sequence[FixedObligation]],
    anchors: Sequence[BalanceAnchor],
    cfg: EngineConfig,
    schedule_days: int,
) -> _Inputs:
    """Leakage guard, FX, classification, B0, run-rates, obligation schedule and buffer."""
    flags = Flags()
    obligations = list(obligations or [])

    # ---- 1. Leakage guard: never use anything booked after as_of.
    future = [t for t in transactions if t.booking_date > as_of]
    history = [t for t in transactions if t.booking_date <= as_of]
    if future:
        flags.add("FUTURE_TRANSACTIONS_IGNORED", K.WARNING,
                  f"{len(future)} transactions booked after {as_of} were supplied and ignored.")
    usable_anchors = sorted((a for a in anchors if a.anchor_date <= as_of), key=lambda a: a.anchor_date)

    # ---- 2. FX conversion (chronological, fallbacks only use earlier rates).
    fx = FxConverter(_reference_rates(obligations))
    converted = fx.convert_all(history)
    fx_counts = Counter(c.fx_source for c in converted)
    missing_fx = [c for c in converted if c.fx_source == FX_MISSING]
    if missing_fx:
        flags.add("FX_RATE_MISSING", K.CRITICAL,
                  f"{len(missing_fx)} foreign-currency transactions have no usable EUR rate and are excluded from all sums.",
                  ["current_cash_balance_eur"])
    fallback_fx = fx_counts.get(FX_LAST_OBSERVED, 0) + fx_counts.get(FX_REFERENCE, 0)
    if fallback_fx:
        flags.add("FX_RATE_FALLBACK", K.WARNING,
                  f"{fallback_fx} foreign-currency transactions converted with a fallback rate (no rate in reference).",
                  ["current_cash_balance_eur"])

    # ---- 3. Classification.
    flows: List[K.Flow] = []
    unclassified = Counter()
    for c in converted:
        if c.amount_eur is None:
            continue
        cls = K.classify(c.tx.transaction_type, c.amount_eur, cfg.transaction_type_classes)
        if cls is None:
            unclassified[c.tx.transaction_type] += 1
            cls = K.fallback_class(c.amount_eur)
        flows.append(K.Flow(c.booking_date, c.amount_eur, cls))
    if unclassified:
        flags.add("UNCLASSIFIED_TRANSACTIONS", K.WARNING,
                  "Unknown transaction types treated as non-recurring inflows / variable outflows: "
                  + ", ".join(f"{k} ({v})" for k, v in sorted(unclassified.items())))

    first_tx = min((t.booking_date for t in history), default=None)
    last_tx = max((t.booking_date for t in history), default=None)

    # ---- 4. KPI 1: current balance via latest anchor <= as_of.
    anchor = usable_anchors[-1] if usable_anchors else None
    balance = K.current_balance(flows, as_of, anchor.anchor_date if anchor else None, anchor.balance if anchor else None)
    reconciliation = []
    for earlier, later in zip(usable_anchors, usable_anchors[1:]):
        rolled = K.current_balance(flows, later.anchor_date, earlier.anchor_date, earlier.balance)
        diff = cents(rolled - later.balance)
        reconciliation.append({"from": _d(earlier.anchor_date), "to": _d(later.anchor_date),
                               "rolled_forward_eur": float(rolled), "anchor_eur": float(later.balance),
                               "difference_eur": float(diff)})
        if diff != 0:
            flags.add("BALANCE_RECONCILIATION_MISMATCH", K.WARNING,
                      f"Rolling {earlier.anchor_date} forward gives {rolled} EUR but the {later.anchor_date} anchor is "
                      f"{later.balance} EUR (difference {diff}). The latest anchor is used.",
                      ["current_cash_balance_eur"])
    if anchor is None:
        flags.add("BALANCE_ANCHOR_MISSING", K.CRITICAL,
                  "No known balance on or before the analysis date; balance is the sum of transactions "
                  "(opening balance assumed 0) and is only a relative figure.", ["current_cash_balance_eur"])
        balance_source = {"method": "SUM_OF_TRANSACTIONS", "anchor": None}
    else:
        if anchor.is_derived:
            flags.add("BALANCE_ANCHOR_DERIVED", INFO,
                      f"Balance rolled forward from a derived anchor ({anchor.anchor_date}: {anchor.balance} EUR). {anchor.source}")
        balance_source = {
            "method": "ANCHOR_ROLL_FORWARD",
            "anchor": {"date": _d(anchor.anchor_date), "balance_eur": float(anchor.balance),
                       "is_derived": anchor.is_derived, "source": anchor.source},
            "reconciliation": reconciliation,
        }
    if not history:
        flags.add("NO_TRANSACTIONS", K.CRITICAL if anchor is None else K.WARNING,
                  f"No transactions booked on or before {as_of}.")
    elif (as_of - last_tx).days > cfg.stale_data_days:
        flags.add("STALE_DATA", K.WARNING,
                  f"Last transaction is from {last_tx} ({(as_of - last_tx).days} days before the analysis date); "
                  "balance and run-rates may be outdated.")

    # ---- 5. Run-rates.
    base_rr = K.compute_run_rates(flows, as_of, cfg.run_rate_lookback_days, first_tx)
    alt_rr = K.compute_run_rates(flows, as_of, cfg.alt_run_rate_lookback_days, first_tx)
    if history and base_rr.days_covered < cfg.run_rate_lookback_days:
        flags.add("INSUFFICIENT_HISTORY", K.WARNING,
                  f"Only {base_rr.days_covered} of {cfg.run_rate_lookback_days} days of history for forecast run-rates.",
                  ["forecast_balance_30d_eur", "minimum_cash_buffer_eur"])
    recent_start, _ = K.window(as_of, cfg.revenue_interruption_days)
    if (K.days_covered(recent_start, as_of, first_tx) >= cfg.revenue_interruption_days
            and K.sum_flows(flows, recent_start, as_of, {OPERATING_INFLOW}) == 0):
        flags.add("REVENUE_INTERRUPTION", K.WARNING,
                  f"No sales receipts in the last {cfg.revenue_interruption_days} days. The base forecast assumes the "
                  "interruption continues (worst case); see forecast.sensitivity for the 90-day view.",
                  ["forecast_balance_30d_eur", "liquidity_gap_30d_eur", "deployable_cash_surplus_eur"])

    # ---- 6. Scheduled obligations.
    has_schedule = bool(obligations)
    if has_schedule:
        sched = project_schedule(obligations, history, as_of, schedule_days, fx)
        if sched.extrapolated_ids:
            flags.add("OBLIGATION_EXTRAPOLATED", INFO,
                      "Obligations projected beyond their documented validity (valid_to) on the assumption they continue: "
                      + ", ".join(sched.extrapolated_ids), ["forecast_balance_30d_eur", "minimum_cash_buffer_eur"])
        for done in sched.completed_commitments:
            flags.add("COMMITMENT_COMPLETED", INFO,
                      f"Commitment {done['expense_id']} fully paid ({done['paid_to_date']} of {done['total_commitment']} "
                      f"{done['currency']}); no further instalments projected.")
        if sched.missing_fx_ids:
            flags.add("FX_RATE_MISSING", K.CRITICAL,
                      "No EUR rate for scheduled obligations: " + ", ".join(sched.missing_fx_ids), ["forecast_balance_30d_eur"])
    else:
        sched = ScheduleResult()
        flags.add("OBLIGATION_SCHEDULE_MISSING", K.WARNING,
                  "No fixed obligation schedule available; fixed-type outflows are projected as a daily average "
                  "(totals preserved, payment timing lost).",
                  ["forecast_balance_30d_eur", "minimum_cash_buffer_eur", "fixed_cost_coverage_ratio"])

    scheduled_by_day: Dict[date, Decimal] = {}
    for p in sched.payments:
        if p.amount_eur is not None:
            scheduled_by_day[p.payment_date] = scheduled_by_day.get(p.payment_date, Decimal("0")) + p.amount_eur

    # ---- 7. KPI 3: buffer = one month of fixed obligations (monthly run-rate) + variable reserve.
    buf_end = as_of + timedelta(days=cfg.buffer_fixed_horizon_days)
    if has_schedule:
        fixed_monthly = cents(sched.monthly_total_eur)
        scheduled_next = cents(sched.total_eur(as_of + timedelta(days=1), buf_end))
    else:
        s, e = K.window(as_of, cfg.buffer_fixed_horizon_days)
        fixed_monthly = cents(-K.sum_flows(flows, s, e, {FIXED_OUTFLOW}))
        scheduled_next = None
    buffer = K.minimum_cash_buffer(fixed_monthly, alt_rr.daily_variable_outflow, cfg.buffer_variable_reserve_days)

    return _Inputs(flags=flags, history=history, flows=flows, first_tx=first_tx, last_tx=last_tx,
                   missing_fx=missing_fx, fx=fx, fx_counts=fx_counts, balance=balance,
                   balance_source=balance_source, base_rr=base_rr, alt_rr=alt_rr, has_schedule=has_schedule,
                   sched=sched, scheduled_by_day=scheduled_by_day, fixed_monthly=fixed_monthly,
                   scheduled_next=scheduled_next, buffer=buffer)


def _horizon_json(hs: K.HorizonStats) -> dict:
    return {
        "horizon_days": hs.horizon,
        "end_balance_eur": money_json(hs.end_balance),
        "min_balance_eur": money_json(hs.min_balance),
        "min_balance_date": _d(hs.min_date),
        "liquidity_gap_eur": money_json(hs.gap),
        "projected_overdraft_eur": money_json(hs.overdraft),
        "first_date_below_buffer": _d(hs.first_breach_date),
        "first_date_below_zero": _d(hs.first_overdraft_date),
    }


def _daily_json(path: Sequence[K.ForecastDay], buffer: Decimal) -> List[dict]:
    return [
        {"day": p.day, "date": _d(p.on), "inflow_eur": money_json(p.inflow),
         "variable_outflow_eur": money_json(p.variable_outflow), "scheduled_outflow_eur": money_json(p.scheduled_outflow),
         "balance_eur": money_json(p.balance), "below_buffer": p.balance < buffer, "below_zero": p.balance < 0}
        for p in path
    ]


def _scheduled_json(payments) -> List[dict]:
    return [
        {"date": _d(p.payment_date), "expense_id": p.expense_id, "payee": p.payee, "category": p.category,
         "amount": money_json(p.amount), "currency": p.currency, "amount_eur": money_json(p.amount_eur),
         "fx_source": p.fx_source, "extrapolated": p.extrapolated}
        for p in payments
    ]


def compute_metrics(
    as_of: date,
    transactions: Sequence[Transaction],
    obligations: Optional[Sequence[FixedObligation]],
    anchors: Sequence[BalanceAnchor],
    config: EngineConfig = EngineConfig(),
    computed_at: Optional[datetime] = None,
    account_number: Optional[str] = None,
) -> dict:
    cfg = config
    obligations = list(obligations or [])
    horizon = cfg.forecast_horizon_days
    p = _prepare(as_of, transactions, obligations, anchors, cfg, horizon)
    flags, history, flows, first_tx, last_tx = p.flags, p.history, p.flows, p.first_tx, p.last_tx
    missing_fx, fx, fx_counts, balance, balance_source = p.missing_fx, p.fx, p.fx_counts, p.balance, p.balance_source
    base_rr, alt_rr, has_schedule, sched = p.base_rr, p.alt_rr, p.has_schedule, p.sched
    scheduled_by_day, fixed_monthly, scheduled_next, buffer = p.scheduled_by_day, p.fixed_monthly, p.scheduled_next, p.buffer
    daily_outflow = p.daily_outflow

    # ---- 8. KPI 2/4/5: forecast.
    path = K.build_forecast(balance, as_of, horizon, base_rr.daily_inflow, daily_outflow(base_rr), scheduled_by_day)
    stats = {h: K.horizon_stats(path, h, buffer) for h in cfg.forecast_horizons}
    s30 = stats[horizon]
    surplus = K.deployable_surplus(s30.min_balance, buffer)

    alt_path = K.build_forecast(balance, as_of, horizon, alt_rr.daily_inflow, daily_outflow(alt_rr), scheduled_by_day)
    alt_stats = {h: K.horizon_stats(alt_path, h, buffer) for h in cfg.forecast_horizons}
    alt_surplus = K.deployable_surplus(alt_stats[horizon].min_balance, buffer)

    # ---- 9. KPI 6/7.
    nf7 = K.net_cash_flow(flows, as_of, 7)
    nf30 = K.net_cash_flow(flows, as_of, 30)
    tw = cfg.trend_window_days
    cur_s, cur_e = K.window(as_of, tw)
    prev_s, prev_e = K.window(as_of, tw, offset_days=tw)
    prev_complete = K.days_covered(prev_s, prev_e, first_tx) == tw

    def trend_block(start, end):
        return {
            "operating_inflows": cents(K.sum_flows(flows, start, end, {OPERATING_INFLOW})),
            "total_outflows": cents(-sum((f.amount_eur for f in flows if start <= f.booking_date <= end and f.amount_eur < 0), Decimal("0"))),
            "variable_outflows": cents(-K.sum_flows(flows, start, end, {VARIABLE_OUTFLOW})),
            "net": cents(K.sum_flows(flows, start, end)),
        }

    cur, prev = trend_block(cur_s, cur_e), trend_block(prev_s, prev_e)
    if prev_complete:
        inflow_trend = K.pct_change(cur["operating_inflows"], prev["operating_inflows"])
        outflow_trend = K.pct_change(cur["total_outflows"], prev["total_outflows"])
        variable_trend = K.pct_change(cur["variable_outflows"], prev["variable_outflows"])
        if inflow_trend is None or outflow_trend is None:
            flags.add("TREND_BASELINE_ZERO", INFO, "Previous trend window has zero inflows or outflows; percentage change undefined.",
                      ["cash_inflow_trend_pct", "cash_outflow_trend_pct"])
    else:
        inflow_trend = outflow_trend = variable_trend = None
        if history:
            flags.add("INSUFFICIENT_HISTORY", K.WARNING,
                      f"Previous trend window {prev_s}..{prev_e} is not fully covered by history; trend not computed.",
                      ["cash_inflow_trend_pct", "cash_outflow_trend_pct"])

    # ---- 10. KPI 8.
    coverage = K.fixed_cost_coverage(balance, fixed_monthly)
    op_inflow30 = cents(K.sum_flows(flows, nf30.start, nf30.end, {OPERATING_INFLOW}))
    var_outflow30 = cents(-K.sum_flows(flows, nf30.start, nf30.end, {VARIABLE_OUTFLOW}))
    operating_coverage = safe_div(op_inflow30 - var_outflow30, fixed_monthly)
    if coverage is None:
        flags.add("NO_FIXED_OBLIGATIONS", INFO, "No active fixed obligations; coverage ratio undefined.",
                  ["fixed_cost_coverage_ratio"])

    # ---- 11. Statuses.
    st = {
        "current_cash_balance_eur": K.status_current_balance(balance, buffer),
        "forecast_balance_7d_eur": K.status_forecast(stats[7].min_balance, buffer) if 7 in stats else K.HEALTHY,
        "forecast_balance_14d_eur": K.status_forecast(stats[14].min_balance, buffer) if 14 in stats else K.HEALTHY,
        "forecast_balance_30d_eur": K.status_forecast(s30.min_balance, buffer),
        "forecast_min_balance_30d_eur": K.status_forecast(s30.min_balance, buffer),
        "minimum_cash_buffer_eur": K.status_buffer(balance, buffer, cfg.buffer_funding_warning),
        "liquidity_gap_30d_eur": K.status_gap(s30.gap, s30.overdraft),
        "projected_overdraft_30d_eur": K.CRITICAL if s30.overdraft > 0 else K.HEALTHY,
        "deployable_cash_surplus_eur": K.status_surplus(surplus, s30.gap),
        "net_cash_flow_7d_eur": K.status_net_flow_short(nf7, nf30),
        "net_cash_flow_30d_eur": K.status_net_flow(nf30, cfg.burn_ratio_critical_pct),
        "cash_inflow_trend_pct": K.status_inflow_trend(inflow_trend, cfg),
        "cash_outflow_trend_pct": K.status_outflow_trend(outflow_trend, inflow_trend, cfg),
        "fixed_cost_coverage_ratio": K.status_coverage(coverage, cfg),
    }

    horizon_json = _horizon_json

    breach = s30.first_breach_date
    values = {
        "current_cash_balance_eur": money_json(balance),
        "forecast_balance_7d_eur": money_json(stats[7].end_balance) if 7 in stats else None,
        "forecast_balance_14d_eur": money_json(stats[14].end_balance) if 14 in stats else None,
        "forecast_balance_30d_eur": money_json(s30.end_balance),
        "forecast_min_balance_30d_eur": money_json(s30.min_balance),
        "minimum_cash_buffer_eur": money_json(buffer),
        "liquidity_gap_30d_eur": money_json(s30.gap),
        "projected_overdraft_30d_eur": money_json(s30.overdraft),
        "deployable_cash_surplus_eur": money_json(surplus),
        "net_cash_flow_7d_eur": money_json(nf7.net),
        "net_cash_flow_30d_eur": money_json(nf30.net),
        "cash_inflow_trend_pct": pct_json(inflow_trend),
        "cash_outflow_trend_pct": pct_json(outflow_trend),
        "fixed_cost_coverage_ratio": money_json(coverage),
    }

    def netflow_json(nf: K.NetFlow) -> dict:
        burn = K.burn_ratio_pct(nf)
        return {"window_start": _d(nf.start), "window_end": _d(nf.end), "inflows_eur": money_json(nf.inflows),
                "outflows_eur": money_json(nf.outflows), "net_eur": money_json(nf.net),
                "burn_ratio_pct": pct_json(burn), "by_class_eur": {k: money_json(v) for k, v in nf.by_class.items()}}

    forecast_details = {
        "horizons": [horizon_json(stats[h]) for h in cfg.forecast_horizons],
        "daily_operating_inflow_eur": money_json(base_rr.daily_inflow),
        "daily_variable_outflow_eur": money_json(daily_outflow(base_rr)),
        "scheduled_obligations_30d_eur": money_json(cents(sum(scheduled_by_day.values(), Decimal("0")))),
    }
    details = {
        "current_cash_balance_eur": {"balance_source": balance_source, "last_transaction_date": _d(last_tx)},
        "forecast_balance_7d_eur": horizon_json(stats[7]) if 7 in stats else {},
        "forecast_balance_14d_eur": horizon_json(stats[14]) if 14 in stats else {},
        "forecast_balance_30d_eur": forecast_details,
        "forecast_min_balance_30d_eur": {"min_balance_date": _d(s30.min_date)},
        "minimum_cash_buffer_eur": {
            "monthly_fixed_obligations_eur": money_json(fixed_monthly),
            "monthly_fixed_obligations_breakdown": sched.monthly_items,
            "fixed_basis": "SCHEDULE_MONTHLY_RUN_RATE" if has_schedule else "TRAILING_30D_FIXED_OUTFLOWS",
            "scheduled_obligations_next_30d_eur": money_json(scheduled_next),
            "variable_reserve_days": cfg.buffer_variable_reserve_days,
            "daily_variable_outflow_90d_eur": money_json(alt_rr.daily_variable_outflow),
            "variable_reserve_eur": money_json(cents(alt_rr.daily_variable_outflow * cfg.buffer_variable_reserve_days)),
            "buffer_funding_ratio": money_json(safe_div(balance, buffer)),
        },
        "liquidity_gap_30d_eur": {
            "horizons": [{"horizon_days": h, "liquidity_gap_eur": money_json(stats[h].gap),
                          "projected_overdraft_eur": money_json(stats[h].overdraft)} for h in cfg.forecast_horizons],
            "first_breach_date": _d(breach),
            "days_until_breach": (breach - as_of).days if breach else None,
            "first_overdraft_date": _d(s30.first_overdraft_date),
            "peak_shortfall_date": _d(s30.min_date) if s30.gap > 0 else None,
            "financing_required": s30.overdraft > 0,
        },
        "projected_overdraft_30d_eur": {"first_overdraft_date": _d(s30.first_overdraft_date)},
        "deployable_cash_surplus_eur": {
            "horizon_days": horizon,
            "binding_date": _d(s30.min_date),
            "forecast_min_balance_eur": money_json(s30.min_balance),
            "minimum_cash_buffer_eur": money_json(buffer),
            "sensitivity_surplus_eur": money_json(alt_surplus),
            "conservative_surplus_eur": money_json(min(surplus, alt_surplus)),
        },
        "net_cash_flow_7d_eur": netflow_json(nf7),
        "net_cash_flow_30d_eur": netflow_json(nf30),
        "cash_inflow_trend_pct": {
            "current_window": [_d(cur_s), _d(cur_e)], "previous_window": [_d(prev_s), _d(prev_e)],
            "current_operating_inflows_eur": money_json(cur["operating_inflows"]),
            "previous_operating_inflows_eur": money_json(prev["operating_inflows"]),
            "direction": K.direction(inflow_trend, cfg.trend_direction_threshold_pct),
        },
        "cash_outflow_trend_pct": {
            "current_total_outflows_eur": money_json(cur["total_outflows"]),
            "previous_total_outflows_eur": money_json(prev["total_outflows"]),
            "variable_outflow_trend_pct": pct_json(variable_trend),
            "current_variable_outflows_eur": money_json(cur["variable_outflows"]),
            "previous_variable_outflows_eur": money_json(prev["variable_outflows"]),
            "current_net_eur": money_json(cur["net"]),
            "previous_net_eur": money_json(prev["net"]),
            "direction": K.direction(outflow_trend, cfg.trend_direction_threshold_pct),
        },
        "fixed_cost_coverage_ratio": {
            "monthly_fixed_obligations_eur": money_json(fixed_monthly),
            "operating_coverage_ratio": money_json(operating_coverage),
            "operating_inflows_30d_eur": money_json(op_inflow30),
            "variable_outflows_30d_eur": money_json(var_outflow30),
        },
    }

    kpi_metadata = []
    for kpi_id, (number, group, label, label_en, unit) in LABELS.items():
        kpi_metadata.append({
            "id": kpi_id, "kpi_number": number, "kpi_group": group, "label": label, "label_en": label_en,
            "value": values[kpi_id], "unit": unit, "status": st[kpi_id], "is_estimate": number in ESTIMATE_GROUPS,
            "details": details[kpi_id],
        })

    # ---- 12. Scenario signals (inputs for the agent, not decisions).
    shortage = s30.gap > 0
    excess = (not shortage) and surplus >= cfg.excess_liquidity_min_eur
    signals = {
        "scenario_hint": "LIQUIDITY_SHORTAGE" if shortage else ("EXCESS_LIQUIDITY" if excess else "OPTIMAL"),
        "liquidity_shortage": {
            "detected": shortage,
            "severity": st["liquidity_gap_30d_eur"],
            "first_breach_date": _d(breach),
            "days_until_breach": (breach - as_of).days if breach else None,
            "liquidity_gap_eur": money_json(s30.gap),
            "projected_overdraft_eur": money_json(s30.overdraft),
            "financing_required": s30.overdraft > 0,
            "suggested_min_financing_eur": money_json(s30.gap),
        },
        "excess_liquidity": {
            "detected": excess,
            "deployable_cash_surplus_eur": money_json(surplus),
            "conservative_surplus_eur": money_json(min(surplus, alt_surplus)),
            "threshold_eur": money_json(cfg.excess_liquidity_min_eur),
            "horizon_days": horizon,
        },
    }

    forecast = {
        "method": FORECAST_METHOD,
        "horizon_days": horizon,
        "run_rates": {"base": base_rr.to_json(), "sensitivity": alt_rr.to_json()},
        "scheduled_obligations": _scheduled_json(sched.payments),
        "open_commitments": sched.open_commitments,
        "completed_commitments": sched.completed_commitments,
        "daily": _daily_json(path, buffer),
        "sensitivity": {
            "description": f"Same model with {cfg.alt_run_rate_lookback_days}-day run-rates",
            "horizons": [horizon_json(alt_stats[h]) for h in cfg.forecast_horizons],
            "deployable_cash_surplus_eur": money_json(alt_surplus),
        },
    }

    data_quality = {
        "status": flags.status(),
        "flags": flags.items,
        "coverage": {
            "transactions_used": len(flows),
            "transactions_excluded": len(missing_fx),
            "first_transaction_date": _d(first_tx),
            "last_transaction_date": _d(last_tx),
            "history_days": (as_of - first_tx).days + 1 if first_tx else 0,
            "obligation_schedule_available": has_schedule,
            "obligations_count": len(obligations),
        },
        "balance_source": balance_source,
        "fx": {
            "conversions_by_source": {k: fx_counts.get(k, 0) for k in (FX_NATIVE, FX_EMBEDDED, FX_LAST_OBSERVED, FX_REFERENCE, FX_MISSING)},
            "latest_rates": {ccy: {"eur_per_unit": float(rate), "observed_on": _d(on)} for ccy, (rate, on) in sorted(fx.observed.items())},
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": as_of.isoformat(),
        "currency": REPORTING_CURRENCY,
        "account_number": account_number,
        "overall_status": K.worst_status(list(st.values())),
        "kpis": values,
        "kpi_metadata": kpi_metadata,
        "forecast": forecast,
        "signals": signals,
        "data_quality": data_quality,
        "engine": {
            "name": ENGINE_NAME,
            "version": ENGINE_VERSION,
            "computed_at": (computed_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "parameters": cfg.to_json(),
        },
    }


def forecast_balance(
    as_of: date,
    days: int,
    transactions: Sequence[Transaction],
    obligations: Optional[Sequence[FixedObligation]],
    anchors: Sequence[BalanceAnchor],
    config: EngineConfig = EngineConfig(),
    computed_at: Optional[datetime] = None,
    account_number: Optional[str] = None,
) -> dict:
    """Forecast end-of-day balance ``days`` ahead of ``as_of`` (KPI 2 method, arbitrary horizon).

    Same inputs, leakage guard and model as ``compute_metrics`` — for ``days`` = 7/14/30 the results
    equal the stored ``forecast_balance_*`` KPIs. Pure function: never touches the database or the clock.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_FORECAST_DAYS:
        raise ValueError(f"days must be an integer between 1 and {MAX_FORECAST_DAYS}, got {days!r}")
    cfg = config
    # The schedule always covers the buffer window too, so MCB is identical to the KPI snapshot.
    p = _prepare(as_of, transactions, list(obligations or []), anchors, cfg,
                 max(days, cfg.buffer_fixed_horizon_days))
    if days > cfg.alt_run_rate_lookback_days:
        p.flags.add("LONG_FORECAST_HORIZON", INFO,
                    f"{days}-day horizon is longer than the {cfg.alt_run_rate_lookback_days}-day run-rate history; "
                    "flat run-rates are extrapolated (no seasonality), treat the far end as indicative.")

    path = K.build_forecast(p.balance, as_of, days, p.base_rr.daily_inflow, p.daily_outflow(p.base_rr), p.scheduled_by_day)
    hs = K.horizon_stats(path, days, p.buffer)
    alt_path = K.build_forecast(p.balance, as_of, days, p.alt_rr.daily_inflow, p.daily_outflow(p.alt_rr), p.scheduled_by_day)
    target = as_of + timedelta(days=days)
    in_horizon = [x for x in p.sched.payments if x.payment_date <= target]

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": as_of.isoformat(),
        "target_date": target.isoformat(),
        "horizon_days": days,
        "currency": REPORTING_CURRENCY,
        "account_number": account_number,
        "opening_balance_eur": money_json(p.balance),
        "forecast_balance_eur": money_json(hs.end_balance),
        "status": K.status_forecast(hs.min_balance, p.buffer),
        "minimum_cash_buffer_eur": money_json(p.buffer),
        "summary": _horizon_json(hs),
        "method": FORECAST_METHOD,
        "run_rates": {"base": p.base_rr.to_json(), "sensitivity": p.alt_rr.to_json()},
        "scheduled_obligations": _scheduled_json(in_horizon),
        "scheduled_obligations_total_eur": money_json(cents(sum(
            (x.amount_eur for x in in_horizon if x.amount_eur is not None), Decimal("0")))),
        "daily": _daily_json(path, p.buffer),
        "sensitivity": {
            "description": f"Same model with {cfg.alt_run_rate_lookback_days}-day run-rates",
            **_horizon_json(K.horizon_stats(alt_path, days, p.buffer)),
        },
        "data_quality": {"status": p.flags.status(), "flags": p.flags.items},
        "engine": {
            "name": ENGINE_NAME,
            "version": ENGINE_VERSION,
            "computed_at": (computed_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
