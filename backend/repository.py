"""Data access for the liquidity agent (psycopg 3). No business logic lives here.

Bank data (transactions, balance anchors) is read through the existing ``kpi_engine.repository`` helpers;
this module adds planned events and owner decisions. Connection settings come from the libpq ``PG*``
environment variables (or ``DATABASE_URL``).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import ContextManager, Iterator, Optional, Protocol, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from domain import (
    DataRange,
    DecisionRecord,
    DecisionSnapshot,
    DecisionStatus,
    EventDraft,
    EventSource,
    EventType,
    EventUpdate,
    NewDecision,
    PlannedEvent,
    RiskStatus,
)
from kpi_engine import repository as kpi_repository
from kpi_engine.models import BalanceAnchor, Transaction
from kpi_engine.money import to_decimal


class StaleEventError(RuntimeError):
    """A planned event that an option wanted to change no longer exists."""


class Repository(Protocol):
    """Everything the service layer needs from storage (implemented by PostgreSQL and by test fakes)."""

    def data_range(self) -> Optional[DataRange]: ...

    def load_transactions(self, as_of: date) -> Sequence[Transaction]: ...

    def load_anchors(self, as_of: date) -> Sequence[BalanceAnchor]: ...

    def list_events(self) -> Sequence[PlannedEvent]: ...

    def delete_event(self, event_id: int) -> bool: ...

    def list_decisions(self) -> Sequence[DecisionRecord]: ...

    def rejected_option_fingerprints(self) -> frozenset[str]:
        """Fingerprints of proposals whose latest decision is a rejection (see ``options.option_fingerprint``)."""
        ...

    def decision_lock(self) -> ContextManager[None]:
        """Serialises decision making: checking that an option is still offered and storing the decision."""
        ...

    def withdraw_decision(self, decision_id: int, note: str) -> None:
        """Remove every planned event an approved decision created, mark the decision as withdrawn and add
        ``note`` to its audit messages."""
        ...

    def record_decision(self, decision: NewDecision, events_to_add: Sequence[EventDraft],
                        event_updates: Sequence[EventUpdate]) -> DecisionRecord: ...

    def reset_demo(self) -> None: ...


_EVENT_COLUMNS = ("id, event_type, date_from, date_to, amount_eur, category, description, original_utterance, "
                  "source, as_of_date, decision_id, created_at")
_DECISION_COLUMNS = ("id, as_of_date, horizon_days, buffer_eur, option_id, option_title, option_description, "
                     "status, execution, llm_explanation, execution_messages, forecast_snapshot, decided_at, "
                     "withdrawn_at")
FINGERPRINT_KEY = "option_fingerprint"  # key inside the ``forecast_snapshot`` document
_DECISION_LOCK_KEY = 2026_0001  # pg_advisory_xact_lock key shared by all backend instances


def open_connection() -> psycopg.Connection:
    """A new autocommit connection; multi-statement writes use explicit ``conn.transaction()`` blocks."""
    connection = kpi_repository.connect()
    connection.autocommit = True
    return connection


class PostgresRepository:
    """Repository bound to one connection (one instance per request)."""

    def __init__(self, connection: psycopg.Connection, account_number: Optional[str] = None):
        self._connection = connection
        self._configured_account = account_number
        self._account: Optional[str] = None

    def data_range(self) -> Optional[DataRange]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT min(booking_date), max(booking_date) FROM bank_transactions WHERE account_number = %s",
                        (self._account_number(),))
            first, last = cur.fetchone()
        return None if first is None else DataRange(first, last)

    def load_transactions(self, as_of: date) -> Sequence[Transaction]:
        return kpi_repository.load_transactions(self._connection, as_of, self._account_number())

    def load_anchors(self, as_of: date) -> Sequence[BalanceAnchor]:
        return kpi_repository.load_anchors(self._connection, as_of, self._account_number())

    def list_events(self) -> Sequence[PlannedEvent]:
        with self._connection.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_EVENT_COLUMNS} FROM planned_events ORDER BY date_from, id")
            return [_event_from_row(row) for row in cur.fetchall()]

    def delete_event(self, event_id: int) -> bool:
        with self._connection.cursor() as cur:
            cur.execute("DELETE FROM planned_events WHERE id = %s", (event_id,))
            return cur.rowcount == 1

    def list_decisions(self) -> Sequence[DecisionRecord]:
        with self._connection.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_DECISION_COLUMNS} FROM owner_decisions ORDER BY decided_at DESC, id DESC")
            return [_decision_from_row(row) for row in cur.fetchall()]

    def rejected_option_fingerprints(self) -> frozenset[str]:
        with self._connection.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (fingerprint) fingerprint, status
                FROM (SELECT forecast_snapshot ->> %s AS fingerprint, status, decided_at, id
                      FROM owner_decisions) AS decisions
                WHERE fingerprint IS NOT NULL
                ORDER BY fingerprint, decided_at DESC, id DESC
                """,
                (FINGERPRINT_KEY,),
            )
            return frozenset(fingerprint for fingerprint, status in cur.fetchall()
                             if status == DecisionStatus.REJECTED.value)

    @contextmanager
    def decision_lock(self) -> Iterator[None]:
        """One transaction holding an advisory lock: concurrent decisions wait and then see each other's writes."""
        with self._connection.transaction():
            self._connection.execute("SELECT pg_advisory_xact_lock(%s)", (_DECISION_LOCK_KEY,))
            yield

    def withdraw_decision(self, decision_id: int, note: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cur:
            cur.execute("DELETE FROM planned_events WHERE decision_id = %s", (decision_id,))
            cur.execute("UPDATE owner_decisions SET execution_messages = execution_messages || %s, withdrawn_at = now() "
                        "WHERE id = %s", (Jsonb([note]), decision_id))

    def record_decision(self, decision: NewDecision, events_to_add: Sequence[EventDraft],
                        event_updates: Sequence[EventUpdate]) -> DecisionRecord:
        """Store the decision and its effect on planned events atomically."""
        with self._connection.transaction(), self._connection.cursor(row_factory=dict_row) as cur:
            record = _insert_decision(cur, decision)
            for event in events_to_add:
                _insert_agent_event(cur, event, decision.as_of_date, record.id)
            for update in event_updates:
                _update_event_dates(cur, update)
        return record

    def reset_demo(self) -> None:
        with self._connection.transaction(), self._connection.cursor() as cur:
            cur.execute("DELETE FROM planned_events")
            cur.execute("DELETE FROM owner_decisions")

    def _account_number(self) -> str:
        if self._account is None:
            self._account = kpi_repository.resolve_account(self._connection, self._configured_account)
        return self._account


def _insert_decision(cur: psycopg.Cursor, decision: NewDecision) -> DecisionRecord:
    cur.execute(
        f"""
        INSERT INTO owner_decisions (as_of_date, horizon_days, buffer_eur, option_id, option_title,
                                     option_description, status, llm_explanation, execution_messages,
                                     forecast_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {_DECISION_COLUMNS}
        """,
        (decision.as_of_date, decision.horizon_days, decision.buffer_eur, decision.option_id, decision.option_title,
         decision.option_description, decision.status.value, decision.llm_explanation,
         Jsonb(list(decision.execution_messages)), Jsonb(decision.forecast_snapshot)),
    )
    return _decision_from_row(cur.fetchone())


def _insert_agent_event(cur: psycopg.Cursor, event: EventDraft, as_of: date, decision_id: int) -> None:
    cur.execute(
        """
        INSERT INTO planned_events (event_type, date_from, date_to, amount_eur, category, description, source,
                                    as_of_date, decision_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (event.event_type.value, event.date_from, event.date_to, event.amount_eur, event.category,
         event.description, EventSource.AGENT.value, as_of, decision_id),
    )


def _update_event_dates(cur: psycopg.Cursor, update: EventUpdate) -> None:
    cur.execute("UPDATE planned_events SET date_from = %s, date_to = %s WHERE id = %s",
                (update.date_from, update.date_to, update.event_id))
    if cur.rowcount != 1:
        raise StaleEventError(f"Planned event {update.event_id} no longer exists.")


def _event_from_row(row: dict) -> PlannedEvent:
    return PlannedEvent(
        id=row["id"],
        event_type=EventType(row["event_type"]),
        date_from=row["date_from"],
        date_to=row["date_to"],
        amount_eur=row["amount_eur"],
        category=row["category"],
        description=row["description"],
        original_utterance=row["original_utterance"],
        source=EventSource(row["source"]),
        as_of_date=row["as_of_date"],
        decision_id=row["decision_id"],
        created_at=row["created_at"],
    )


def _decision_from_row(row: dict) -> DecisionRecord:
    return DecisionRecord(
        id=row["id"],
        as_of_date=row["as_of_date"],
        horizon_days=row["horizon_days"],
        buffer_eur=row["buffer_eur"],
        option_id=row["option_id"],
        option_title=row["option_title"],
        option_description=row["option_description"],
        status=DecisionStatus(row["status"]),
        execution=row["execution"],
        llm_explanation=row["llm_explanation"],
        execution_messages=tuple(row["execution_messages"]),
        decided_at=row["decided_at"],
        snapshot=snapshot_from_json(row["forecast_snapshot"]),
        withdrawn_at=row["withdrawn_at"],
    )


def snapshot_from_json(document: dict) -> DecisionSnapshot:
    """Read the summary part of a stored ``forecast_snapshot`` document."""
    credit_limit_after = document.get("credit_limit_after_eur")  # absent in decisions recorded before it existed
    return DecisionSnapshot(
        status_before=RiskStatus(document["status_before"]),
        min_balance_before_eur=to_decimal(document["min_balance_before_eur"]),
        min_balance_date_before=date.fromisoformat(document["min_balance_date_before"]),
        status_after=RiskStatus(document["status_after"]),
        min_balance_after_eur=to_decimal(document["min_balance_after_eur"]),
        credit_limit_after_eur=None if credit_limit_after is None else to_decimal(credit_limit_after),
        cost_eur=to_decimal(document["cost_eur"]),
    )
