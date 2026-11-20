"""PostgreSQL access for the KPI engine (psycopg 3). No calculation logic lives here.

Connection settings: an explicit DSN, else ``DATABASE_URL``, else the standard libpq
environment variables (``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``).
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import List, Optional, Tuple

import psycopg

from .models import BalanceAnchor, FixedObligation, Transaction


class RepositoryError(RuntimeError):
    pass


def connect(dsn: Optional[str] = None) -> psycopg.Connection:
    conninfo = dsn or os.environ.get("DATABASE_URL", "")
    try:
        return psycopg.connect(conninfo, connect_timeout=10)
    except psycopg.Error as exc:  # pragma: no cover - depends on environment
        raise RepositoryError(f"Cannot connect to PostgreSQL: {exc}") from exc


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
        return bool(cur.fetchone()[0])


def resolve_account(conn: psycopg.Connection, account_number: Optional[str]) -> str:
    """Use the given account, or the single account present in bank_transactions."""
    if account_number:
        return account_number
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT account_number FROM bank_transactions ORDER BY 1")
        accounts = [r[0] for r in cur.fetchall()]
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise RepositoryError("bank_transactions is empty — import the bank statement first.")
    raise RepositoryError(f"Multiple accounts found ({', '.join(accounts)}); pass account_number explicitly.")


def load_transactions(conn: psycopg.Connection, as_of: date, account_number: str) -> List[Transaction]:
    """Transactions booked on or before ``as_of`` only (future-data leakage guard #1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, booking_date, amount, currency, transaction_type, payment_reference, beneficiary_payer
            FROM bank_transactions
            WHERE booking_date <= %s AND account_number = %s
            ORDER BY booking_date, id
            """,
            (as_of, account_number),
        )
        return [
            Transaction(id=r[0], booking_date=r[1], amount=r[2], currency=r[3].strip(), transaction_type=r[4],
                        payment_reference=r[5], beneficiary_payer=r[6])
            for r in cur.fetchall()
        ]


def load_obligations(conn: psycopg.Connection) -> Optional[List[FixedObligation]]:
    """Fixed obligation schedule, or None when the table does not exist."""
    if not _table_exists(conn, "fixed_obligations"):
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT expense_id, payee, category, cashflow_class, payment_day, payment_months, currency,
                   amount_per_payment, valid_from, valid_to, estimated_eur_per_payment, total_commitment,
                   match_keyword, description
            FROM fixed_obligations ORDER BY expense_id
            """
        )
        return [
            FixedObligation(expense_id=r[0], payee=r[1], category=r[2], cashflow_class=r[3], payment_day=int(r[4]),
                            payment_months=r[5], currency=r[6].strip(), amount_per_payment=r[7], valid_from=r[8],
                            valid_to=r[9], estimated_eur_per_payment=r[10], total_commitment=r[11],
                            match_keyword=r[12], description=r[13])
            for r in cur.fetchall()
        ]


def load_anchors(conn: psycopg.Connection, as_of: date, account_number: str) -> List[BalanceAnchor]:
    """Balance anchors dated on or before ``as_of`` only (future-data leakage guard #1)."""
    if not _table_exists(conn, "account_balance_anchors"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT anchor_date, balance, currency, is_derived, source
            FROM account_balance_anchors
            WHERE anchor_date <= %s AND account_number = %s
            ORDER BY anchor_date
            """,
            (as_of, account_number),
        )
        return [BalanceAnchor(anchor_date=r[0], balance=r[1], currency=r[2].strip(), is_derived=r[3], source=r[4])
                for r in cur.fetchall()]


def upsert_snapshot(conn: psycopg.Connection, snapshot_date: date, metrics: dict) -> Tuple[int, bool]:
    """Insert or update the daily snapshot. Returns (snapshot_id, created). Keeps the row id on update."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kpi_snapshots (snapshot_date, metrics)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (snapshot_date) DO UPDATE
                SET metrics = EXCLUDED.metrics, created_at = CURRENT_TIMESTAMP
            RETURNING id, (xmax = 0) AS created
            """,
            (snapshot_date, json.dumps(metrics, ensure_ascii=False)),
        )
        row = cur.fetchone()
    return int(row[0]), bool(row[1])


def load_snapshot(conn: psycopg.Connection, snapshot_date: date) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, snapshot_date, metrics FROM kpi_snapshots WHERE snapshot_date = %s", (snapshot_date,))
        row = cur.fetchone()
    return None if row is None else {"id": row[0], "snapshot_date": row[1], "metrics": row[2]}
