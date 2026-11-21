"""PostgreSQL access and the as-of cutoff for the Amalfi MCP server.

Connection settings: ``DATABASE_URL``, else the standard libpq environment variables
(``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``).

Every session is read-only by default (``default_transaction_read_only``) and gets a statement
timeout, independently of the database role's privileges. ``connect_write`` is the single exception:
it opens one explicit READ WRITE transaction and is used only to insert or delete a confirmed planned event.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg

STATEMENT_TIMEOUT_MS = int(os.environ.get("MCP_STATEMENT_TIMEOUT_MS", "10000"))


class ToolInputError(ValueError):
    """Invalid tool arguments; the message is shown to the LLM so it can correct the call."""


SESSION_OPTIONS = f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}"


def connect() -> psycopg.Connection:
    """Read-only autocommit connection used by every read tool."""
    return psycopg.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=10, autocommit=True,
                           options=SESSION_OPTIONS)


def connect_write() -> psycopg.Connection:
    """Connection whose single transaction is READ WRITE; for save_planned_event and remove_planned_event only.

    The session default stays read-only: psycopg opens the transaction with ``BEGIN READ WRITE`` and
    ``with connect_write() as conn`` commits on success and rolls back on any error. What can be written
    is limited by the role's grants (INSERT and DELETE of chat rows in planned_events only).
    """
    conn = psycopg.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=10, autocommit=False,
                           options=SESSION_OPTIONS)
    conn.read_only = False
    return conn


def parse_date(value: Optional[str], name: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ToolInputError(f"{name} must be a date in YYYY-MM-DD format, got {value!r}") from exc


def effective_cutoff(header_value: Optional[str], env_value: Optional[str]) -> Optional[date]:
    """The as-of date the agent is analysing: data after it must stay invisible (no future leakage).

    Sources: the ``X-As-Of-Date`` request header (set per agent run by the orchestrator) and the
    ``AMALFI_AS_OF_DATE`` env var (server-wide cap). When both are set, the earlier one wins.
    """
    candidates = [d for d in (parse_date(header_value, "X-As-Of-Date header"),
                              parse_date(env_value, "AMALFI_AS_OF_DATE")) if d is not None]
    return min(candidates) if candidates else None


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def fetch_dicts(conn: psycopg.Connection, query, params=()) -> list:
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [c.name for c in cur.description]
        return [{c: to_jsonable(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
