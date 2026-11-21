"""Booked transactions as a EUR ledger: conversion, categorisation and account balance (ROZWIAZANIE.md §4)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from categories import categorize
from domain import BalancePoint, LedgerEntry
from kpi_engine.fx import FxConverter
from kpi_engine.models import BalanceAnchor, Transaction
from kpi_engine.money import cents


class LedgerError(RuntimeError):
    """The stored bank data cannot be turned into a consistent EUR ledger."""


def build_ledger(transactions: Sequence[Transaction]) -> tuple[LedgerEntry, ...]:
    """Convert every transaction to EUR (rate embedded in the payment reference) and categorise it."""
    return tuple(_to_entry(converted) for converted in FxConverter().convert_all(transactions))


def _to_entry(converted) -> LedgerEntry:
    tx = converted.tx
    if converted.amount_eur is None:
        raise LedgerError(
            f"Transaction {tx.id} on {tx.booking_date} is in {tx.currency} and carries no EUR exchange rate."
        )
    return LedgerEntry(
        date=tx.booking_date,
        amount_eur=converted.amount_eur,
        category=categorize(tx.transaction_type, tx.beneficiary_payer, tx.payment_reference, tx.amount),
        counterparty=(tx.beneficiary_payer or "").strip(),
        reference=tx.payment_reference or "",
    )


def opening_anchor(anchors: Sequence[BalanceAnchor]) -> BalanceAnchor:
    """The earliest known balance; every later balance is rolled forward from it."""
    if not anchors:
        raise LedgerError("No opening balance is available (account_balance_anchors has no usable row).")
    return min(anchors, key=lambda anchor: anchor.anchor_date)


def daily_balances(ledger: Sequence[LedgerEntry], anchor: BalanceAnchor, start: date, end: date
                   ) -> tuple[BalancePoint, ...]:
    """End-of-day balance for each day in [start, end]; ``start`` must not precede the anchor date."""
    if start < anchor.anchor_date:
        raise ValueError("Balance history cannot start before the opening balance date.")
    movements: dict[date, Decimal] = {}
    balance = cents(anchor.balance)
    for entry in ledger:
        if entry.date <= anchor.anchor_date or entry.date > end:
            continue
        if entry.date < start:
            balance += entry.amount_eur
        else:
            movements[entry.date] = movements.get(entry.date, Decimal("0")) + entry.amount_eur
    points = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        balance += movements.get(day, Decimal("0"))
        points.append(BalancePoint(day, balance))
    return tuple(points)


def balance_on(ledger: Sequence[LedgerEntry], anchor: BalanceAnchor, day: date) -> Decimal:
    """End-of-day balance on ``day``: opening balance plus every EUR movement up to that day inclusive."""
    return daily_balances(ledger, anchor, day, day)[0].balance_eur
