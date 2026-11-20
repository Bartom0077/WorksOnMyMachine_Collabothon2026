"""Projection of documented fixed obligations into dated future payments (KPI_SPEC.md §0.6)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, List, Optional

from .fx import FX_MISSING, FxConverter
from .models import FixedObligation, Transaction
from .money import cents


@dataclass(frozen=True)
class ScheduledPayment:
    payment_date: date
    expense_id: str
    payee: str
    category: str
    amount: Decimal  # positive, in obligation currency
    currency: str
    amount_eur: Optional[Decimal]  # positive; None if no FX rate
    fx_source: str
    extrapolated: bool  # True when the date is after the obligation's valid_to


@dataclass
class ScheduleResult:
    payments: List[ScheduledPayment] = field(default_factory=list)
    extrapolated_ids: List[str] = field(default_factory=list)
    completed_commitments: List[dict] = field(default_factory=list)
    open_commitments: List[dict] = field(default_factory=list)
    missing_fx_ids: List[str] = field(default_factory=list)
    # Monthly-equivalent run-rate of obligations still active after as_of (KPI_SPEC.md KPI 3 / KPI 8)
    monthly_items: List[dict] = field(default_factory=list)
    monthly_total_eur: Decimal = Decimal("0.00")

    def total_eur(self, start: date, end: date) -> Decimal:
        return sum(
            (p.amount_eur for p in self.payments if start <= p.payment_date <= end and p.amount_eur is not None),
            Decimal("0"),
        )

    def on(self, day: date) -> List[ScheduledPayment]:
        return [p for p in self.payments if p.payment_date == day]


def payment_months(obligation: FixedObligation) -> List[int]:
    spec = (obligation.payment_months or "all").strip().lower()
    if spec in ("all", "*", ""):
        return list(range(1, 13))
    return sorted({int(part) for part in spec.replace(",", ";").split(";") if part.strip()})


def occurrence_dates(obligation: FixedObligation, start: date, end: date) -> List[date]:
    """All due dates of ``obligation`` in [start, end] (valid_to is NOT applied here)."""
    months = payment_months(obligation)
    out: List[date] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        if month in months:
            day = min(obligation.payment_day, calendar.monthrange(year, month)[1])
            due = date(year, month, day)
            if start <= due <= end and due >= obligation.valid_from:
                out.append(due)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def paid_towards_commitment(obligation: FixedObligation, history: Iterable[Transaction], as_of: date) -> Decimal:
    """Sum of already-paid instalments (obligation currency) identified by ``match_keyword``."""
    keyword = (obligation.match_keyword or "").lower()
    if not keyword:
        return Decimal("0")
    total = Decimal("0")
    for tx in history:
        if tx.booking_date > as_of or tx.amount >= 0:
            continue
        if tx.currency.strip().upper() != obligation.currency.upper():
            continue
        text = f"{tx.payment_reference or ''} {tx.beneficiary_payer or ''}".lower()
        if keyword in text:
            total += -tx.amount
    return total


def project_schedule(
    obligations: Iterable[FixedObligation],
    history: Iterable[Transaction],
    as_of: date,
    horizon_days: int,
    fx: FxConverter,
) -> ScheduleResult:
    """Payments due in (as_of, as_of + horizon_days]. Uses only history booked on/before as_of."""
    history = [t for t in history if t.booking_date <= as_of]
    start, end = as_of + timedelta(days=1), as_of + timedelta(days=horizon_days)
    result = ScheduleResult()

    for ob in sorted(obligations, key=lambda o: o.expense_id):
        remaining: Optional[Decimal] = None
        if ob.total_commitment is not None:
            paid = paid_towards_commitment(ob, history, as_of)
            remaining = max(Decimal("0"), ob.total_commitment - paid)
            info = {
                "expense_id": ob.expense_id,
                "currency": ob.currency,
                "total_commitment": float(ob.total_commitment),
                "paid_to_date": float(cents(paid)),
                "remaining": float(cents(remaining)),
            }
            if remaining <= 0:
                result.completed_commitments.append(info)
                continue
            result.open_commitments.append(info)

        rate, fx_source, _ = fx.rate_for_forecast(ob.currency)

        # Monthly-equivalent amount: per-payment amount x payments per year / 12.
        if ob.valid_from <= end:
            per_payment = ob.amount_per_payment if remaining is None else min(ob.amount_per_payment, remaining)
            if rate is not None:
                per_payment_eur = per_payment * rate
            elif ob.estimated_eur_per_payment is not None and ob.amount_per_payment:
                per_payment_eur = per_payment * ob.estimated_eur_per_payment / ob.amount_per_payment
            else:
                per_payment_eur = None
            if per_payment_eur is not None:
                monthly = cents(per_payment_eur * len(payment_months(ob)) / 12)
                result.monthly_total_eur += monthly
                result.monthly_items.append({"expense_id": ob.expense_id, "category": ob.category,
                                             "monthly_equivalent_eur": float(monthly)})

        for due in occurrence_dates(ob, start, end):
            extrapolated = ob.valid_to is not None and due > ob.valid_to
            amount = ob.amount_per_payment
            if remaining is not None:
                if remaining <= 0:
                    break
                amount = min(amount, remaining)
                remaining -= amount
            if rate is not None:
                amount_eur = cents(amount * rate)
            elif ob.estimated_eur_per_payment is not None and ob.amount_per_payment:
                amount_eur = cents(amount * ob.estimated_eur_per_payment / ob.amount_per_payment)
            else:
                amount_eur = None
                if ob.expense_id not in result.missing_fx_ids:
                    result.missing_fx_ids.append(ob.expense_id)
            if extrapolated and ob.expense_id not in result.extrapolated_ids:
                result.extrapolated_ids.append(ob.expense_id)
            result.payments.append(
                ScheduledPayment(
                    payment_date=due,
                    expense_id=ob.expense_id,
                    payee=ob.payee,
                    category=ob.category,
                    amount=cents(amount),
                    currency=ob.currency,
                    amount_eur=amount_eur,
                    fx_source=fx_source if amount_eur is not None else FX_MISSING,
                    extrapolated=extrapolated,
                )
            )
    result.payments.sort(key=lambda p: (p.payment_date, p.expense_id))
    return result
