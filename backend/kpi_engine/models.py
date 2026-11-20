"""Plain data structures used by the KPI engine (no database or I/O dependencies)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Transaction:
    """One booked bank transaction. ``amount`` is in ``currency`` (negative = outflow)."""

    booking_date: date
    amount: Decimal
    currency: str
    transaction_type: str
    payment_reference: Optional[str] = None
    beneficiary_payer: Optional[str] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class FixedObligation:
    """A documented recurring outflow (rent, payroll, taxes, investment tranches)."""

    expense_id: str
    payee: str
    category: str
    cashflow_class: str
    payment_day: int
    payment_months: str  # "all" or "03;06;09;12"
    currency: str
    amount_per_payment: Decimal
    valid_from: date
    valid_to: Optional[date] = None
    estimated_eur_per_payment: Optional[Decimal] = None
    total_commitment: Optional[Decimal] = None
    match_keyword: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class BalanceAnchor:
    """A known end-of-day account balance (documented or derived)."""

    anchor_date: date
    balance: Decimal
    currency: str = "EUR"
    is_derived: bool = False
    source: str = ""
