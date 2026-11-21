"""Shared fixtures: the real 2025 data loaded straight from the repository CSV files.

Parsing mirrors db/import_transactions.sql (';' separator, decimal comma, DD.MM.YY dates), and the
obligation/anchor fixtures mirror db/kpi_reference_data.sql (verified by test_integration_db.py).
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from kpi_engine.models import BalanceAnchor, FixedObligation, Transaction  # noqa: E402

STATEMENT_CSV = REPO / "Challenge" / "pizzeria-amalfi-bank-statement-2025-v6.csv"
OBLIGATIONS_CSV = REPO / "Knowledge" / "pizzeria_amalfi_fixed_outflows_2025.csv"
ACCOUNT = "DE89370400440532013000"
FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load_statement_rows():
    with open(STATEMENT_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def load_statement() -> list:
    out = []
    for i, r in enumerate(load_statement_rows(), start=1):
        out.append(Transaction(
            id=i,
            booking_date=datetime.strptime(r["Booking Date"], "%d.%m.%y").date(),
            amount=Decimal(r["Amount"].replace(",", ".")),
            currency=r["Currency"],
            transaction_type=r["Transaction Type"],
            payment_reference=r["Payment Reference"] or None,
            beneficiary_payer=r["Beneficiary/Payer"] or None,
        ))
    return out


def load_obligations() -> list:
    out = []
    with open(OBLIGATIONS_CSV, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            is_inv = r["expense_id"] == "INV001"
            out.append(FixedObligation(
                expense_id=r["expense_id"], payee=r["payee"], category=r["category"],
                cashflow_class=r["cashflow_class"], payment_day=int(r["payment_day"]),
                payment_months=r["payment_months"], currency=r["currency"],
                amount_per_payment=Decimal(r["amount_per_payment"]),
                estimated_eur_per_payment=Decimal(r["estimated_eur_per_payment"]),
                valid_from=date.fromisoformat(r["valid_from"]), valid_to=date.fromisoformat(r["valid_to"]),
                total_commitment=Decimal("50000.00") if is_inv else None,
                match_keyword="Silent Partnership" if is_inv else None,
                description=r["description"],
            ))
    return out


ANCHORS = [
    BalanceAnchor(date(2024, 12, 31), Decimal("2500.00"), is_derived=True, source="derived"),
    BalanceAnchor(date(2025, 6, 30), Decimal("-20098.69"), source="knowledge §8"),
    BalanceAnchor(date(2025, 12, 31), Decimal("33266.35"), source="knowledge §8"),
]


@pytest.fixture(scope="session")
def statement():
    return load_statement()


@pytest.fixture(scope="session")
def obligations():
    return load_obligations()


@pytest.fixture(scope="session")
def anchors():
    return list(ANCHORS)


@pytest.fixture(scope="session")
def ledger(statement):
    from ledger import build_ledger

    return build_ledger(statement)


@pytest.fixture()
def repo(statement, anchors):
    """CSV-backed in-memory repository (fresh planned events / decisions per test)."""
    from fakes import InMemoryRepository

    return InMemoryRepository(statement, anchors)
