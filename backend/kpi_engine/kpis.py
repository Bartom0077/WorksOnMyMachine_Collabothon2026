"""Pure KPI formulas (KPI_SPEC.md §1). No I/O, no clock, Decimal arithmetic only."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from .config import (
    FIXED_OUTFLOW,
    OPERATING_INFLOW,
    OTHER_INFLOW,
    OTHER_OUTFLOW,
    VARIABLE_OUTFLOW,
    EngineConfig,
)
from .money import cents, safe_div

HEALTHY, WARNING, CRITICAL = "HEALTHY", "WARNING", "CRITICAL"
_STATUS_RANK = {HEALTHY: 0, WARNING: 1, CRITICAL: 2}


def worst_status(statuses: Sequence[str]) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK[s]) if statuses else HEALTHY


@dataclass(frozen=True)
class Flow:
    """A converted, classified transaction (EUR, signed)."""

    booking_date: date
    amount_eur: Decimal
    flow_class: str


def classify(transaction_type: str, amount: Decimal, mapping: Dict[str, str]) -> Optional[str]:
    """Class per KPI_SPEC.md §0.4; returns None when the type is unknown (caller flags it)."""
    cls = mapping.get((transaction_type or "").strip().upper())
    if cls is None:
        return None
    # Sign sanity: a refund on a supplier type is not a purchase, a charge-back is not revenue.
    if amount > 0 and cls in (FIXED_OUTFLOW, VARIABLE_OUTFLOW):
        return OTHER_INFLOW
    if amount < 0 and cls == OPERATING_INFLOW:
        return OTHER_OUTFLOW
    return cls


def fallback_class(amount: Decimal) -> str:
    return OTHER_INFLOW if amount > 0 else OTHER_OUTFLOW


# --------------------------------------------------------------------------- windows

def window(as_of: date, days: int, offset_days: int = 0):
    """Inclusive window of ``days`` days ending ``offset_days`` before as_of."""
    end = as_of - timedelta(days=offset_days)
    return end - timedelta(days=days - 1), end


def days_covered(start: date, end: date, data_start: Optional[date]) -> int:
    if data_start is None:
        return 0
    effective = max(start, data_start)
    return max(0, (end - effective).days + 1)


def sum_flows(flows: Sequence[Flow], start: date, end: date, classes=None) -> Decimal:
    return sum(
        (f.amount_eur for f in flows if start <= f.booking_date <= end and (classes is None or f.flow_class in classes)),
        Decimal("0"),
    )


@dataclass(frozen=True)
class RunRates:
    lookback_days: int
    days_covered: int
    daily_inflow: Decimal  # >= 0
    daily_variable_outflow: Decimal  # >= 0, variable + other outflows
    daily_fixed_outflow_hist: Decimal  # >= 0, fixed-type outflows (used only without schedule)

    def to_json(self) -> dict:
        return {
            "lookback_days": self.lookback_days,
            "days_covered": self.days_covered,
            "daily_operating_inflow_eur": float(self.daily_inflow),
            "daily_variable_outflow_eur": float(self.daily_variable_outflow),
            "daily_fixed_outflow_historical_eur": float(self.daily_fixed_outflow_hist),
        }


def compute_run_rates(flows: Sequence[Flow], as_of: date, lookback: int, data_start: Optional[date]) -> RunRates:
    start, end = window(as_of, lookback)
    n = days_covered(start, end, data_start)
    if n == 0:
        return RunRates(lookback, 0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
    inflow = sum_flows(flows, start, end, {OPERATING_INFLOW})
    variable = -sum_flows(flows, start, end, {VARIABLE_OUTFLOW, OTHER_OUTFLOW})
    fixed = -sum_flows(flows, start, end, {FIXED_OUTFLOW})
    return RunRates(lookback, n, cents(inflow / n), cents(variable / n), cents(fixed / n))


# --------------------------------------------------------------------------- KPI 1

def current_balance(flows: Sequence[Flow], as_of: date, anchor_date: Optional[date], anchor_balance: Optional[Decimal]) -> Decimal:
    """B0 = anchor + flows in (anchor_date, as_of]; without an anchor, the sum of all flows."""
    if anchor_date is None:
        return cents(sum((f.amount_eur for f in flows if f.booking_date <= as_of), Decimal("0")))
    rolled = sum((f.amount_eur for f in flows if anchor_date < f.booking_date <= as_of), Decimal("0"))
    return cents(anchor_balance + rolled)


def status_current_balance(balance: Decimal, buffer: Decimal) -> str:
    if balance < 0:
        return CRITICAL
    if balance < buffer:
        return WARNING
    return HEALTHY


# --------------------------------------------------------------------------- KPI 2

@dataclass(frozen=True)
class ForecastDay:
    day: int
    on: date
    inflow: Decimal
    variable_outflow: Decimal
    scheduled_outflow: Decimal
    balance: Decimal


def build_forecast(
    opening_balance: Decimal,
    as_of: date,
    horizon_days: int,
    daily_inflow: Decimal,
    daily_outflow: Decimal,
    scheduled_by_day: Dict[date, Decimal],
) -> List[ForecastDay]:
    """B_d = B_{d-1} + I - V - F_d for d = 1..horizon; day 0 is the current balance."""
    path = [ForecastDay(0, as_of, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), cents(opening_balance))]
    balance = cents(opening_balance)
    for d in range(1, horizon_days + 1):
        on = as_of + timedelta(days=d)
        scheduled = cents(scheduled_by_day.get(on, Decimal("0")))
        balance = balance + daily_inflow - daily_outflow - scheduled
        path.append(ForecastDay(d, on, daily_inflow, daily_outflow, scheduled, balance))
    return path


@dataclass(frozen=True)
class HorizonStats:
    horizon: int
    end_balance: Decimal
    min_balance: Decimal
    min_date: date
    gap: Decimal
    overdraft: Decimal
    first_breach_date: Optional[date]
    first_overdraft_date: Optional[date]


def horizon_stats(path: Sequence[ForecastDay], horizon: int, buffer: Decimal) -> HorizonStats:
    segment = path[: horizon + 1]
    low = min(segment, key=lambda p: (p.balance, p.day))  # earliest date on ties
    breach = next((p.on for p in segment if p.balance < buffer), None)
    overdraft_day = next((p.on for p in segment if p.balance < 0), None)
    return HorizonStats(
        horizon=horizon,
        end_balance=segment[-1].balance,
        min_balance=low.balance,
        min_date=low.on,
        gap=liquidity_gap(buffer, low.balance),
        overdraft=projected_overdraft(low.balance),
        first_breach_date=breach,
        first_overdraft_date=overdraft_day,
    )


def status_forecast(min_balance: Decimal, buffer: Decimal) -> str:
    if min_balance < 0:
        return CRITICAL
    if min_balance < buffer:
        return WARNING
    return HEALTHY


# --------------------------------------------------------------------------- KPI 3

def minimum_cash_buffer(scheduled_fixed_next_days: Decimal, daily_variable_outflow: Decimal, reserve_days: int) -> Decimal:
    return cents(scheduled_fixed_next_days + daily_variable_outflow * reserve_days)


def status_buffer(balance: Decimal, buffer: Decimal, warning_ratio: Decimal) -> str:
    ratio = safe_div(balance, buffer)
    if ratio is None or ratio >= 1:
        return HEALTHY
    if ratio >= warning_ratio:
        return WARNING
    return CRITICAL


# --------------------------------------------------------------------------- KPI 4

def liquidity_gap(buffer: Decimal, min_balance: Decimal) -> Decimal:
    return cents(max(Decimal("0"), buffer - min_balance))


def projected_overdraft(min_balance: Decimal) -> Decimal:
    return cents(max(Decimal("0"), -min_balance))


def status_gap(gap: Decimal, overdraft: Decimal) -> str:
    if overdraft > 0:
        return CRITICAL
    if gap > 0:
        return WARNING
    return HEALTHY


# --------------------------------------------------------------------------- KPI 5

def deployable_surplus(min_balance: Decimal, buffer: Decimal) -> Decimal:
    return cents(max(Decimal("0"), min_balance - buffer))


def status_surplus(surplus: Decimal, gap: Decimal) -> str:
    return WARNING if surplus == 0 and gap > 0 else HEALTHY


# --------------------------------------------------------------------------- KPI 6

@dataclass(frozen=True)
class NetFlow:
    days: int
    start: date
    end: date
    inflows: Decimal
    outflows: Decimal  # negative
    by_class: Dict[str, Decimal] = field(default_factory=dict)

    @property
    def net(self) -> Decimal:
        return cents(self.inflows + self.outflows)


def net_cash_flow(flows: Sequence[Flow], as_of: date, days: int, offset_days: int = 0) -> NetFlow:
    start, end = window(as_of, days, offset_days)
    selected = [f for f in flows if start <= f.booking_date <= end]
    by_class: Dict[str, Decimal] = {}
    for f in selected:
        by_class[f.flow_class] = by_class.get(f.flow_class, Decimal("0")) + f.amount_eur
    return NetFlow(
        days=days,
        start=start,
        end=end,
        inflows=cents(sum((f.amount_eur for f in selected if f.amount_eur > 0), Decimal("0"))),
        outflows=cents(sum((f.amount_eur for f in selected if f.amount_eur < 0), Decimal("0"))),
        by_class={k: cents(v) for k, v in sorted(by_class.items())},
    )


def burn_ratio_pct(nf: NetFlow) -> Optional[Decimal]:
    if nf.net >= 0:
        return Decimal("0")
    return safe_div(-nf.net * 100, -nf.outflows)


def status_net_flow(nf: NetFlow, critical_pct: Decimal) -> str:
    if nf.net >= 0:
        return HEALTHY
    ratio = burn_ratio_pct(nf)
    return CRITICAL if ratio is None or ratio > critical_pct else WARNING


def status_net_flow_short(short: NetFlow, long: NetFlow) -> str:
    """7-day status: a payroll/rent week inside a cash-generating month is normal, so the short
    window only warns when the 30-day flow is negative too (never CRITICAL on its own)."""
    return WARNING if short.net < 0 and long.net < 0 else HEALTHY


# --------------------------------------------------------------------------- KPI 7

def pct_change(current: Decimal, previous: Decimal) -> Optional[Decimal]:
    if previous == 0:
        return None
    return (current - previous) * 100 / previous


def direction(change: Optional[Decimal], threshold: Decimal) -> Optional[str]:
    if change is None:
        return None
    if change >= threshold:
        return "RISING"
    if change <= -threshold:
        return "DECLINING"
    return "STABLE"


def status_inflow_trend(change: Optional[Decimal], cfg: EngineConfig) -> str:
    if change is None:
        return HEALTHY
    if change <= cfg.trend_inflow_critical_pct:
        return CRITICAL
    if change <= cfg.trend_inflow_warning_pct:
        return WARNING
    return HEALTHY


def status_outflow_trend(outflow_change: Optional[Decimal], inflow_change: Optional[Decimal], cfg: EngineConfig) -> str:
    if outflow_change is None:
        return HEALTHY
    baseline = inflow_change if inflow_change is not None else Decimal("0")
    return WARNING if outflow_change - baseline >= cfg.trend_divergence_warning_pp else HEALTHY


# --------------------------------------------------------------------------- KPI 8

def fixed_cost_coverage(balance: Decimal, fixed_obligations: Decimal) -> Optional[Decimal]:
    return safe_div(balance, fixed_obligations)


def status_coverage(ratio: Optional[Decimal], cfg: EngineConfig) -> str:
    if ratio is None:
        return HEALTHY
    if ratio >= cfg.coverage_healthy:
        return HEALTHY
    if ratio >= cfg.coverage_warning:
        return WARNING
    return CRITICAL
