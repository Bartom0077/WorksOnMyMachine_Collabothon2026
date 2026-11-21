"""In-memory repository for unit tests: real bank data from the CSV, planned events and decisions in lists."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import replace
from typing import Iterator, Optional, Sequence

from domain import (
    DataRange,
    DecisionRecord,
    DecisionStatus,
    EventDraft,
    EventSource,
    EventType,
    EventUpdate,
    EXECUTION_SIMULATED,
    NewDecision,
    PlannedEvent,
)
from kpi_engine.models import BalanceAnchor, Transaction
from repository import FINGERPRINT_KEY, StaleEventError, snapshot_from_json

FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class InMemoryRepository:
    def __init__(self, transactions: Sequence[Transaction], anchors: Sequence[BalanceAnchor]):
        self._transactions = list(transactions)
        self._anchors = list(anchors)
        self.events: list[PlannedEvent] = []
        self.decisions: list[DecisionRecord] = []
        self._fingerprint_status: dict[str, DecisionStatus] = {}  # latest decision per proposal
        self._next_event_id = 1

    def data_range(self) -> Optional[DataRange]:
        if not self._transactions:
            return None
        dates = [tx.booking_date for tx in self._transactions]
        return DataRange(min(dates), max(dates))

    def load_transactions(self, as_of: date) -> Sequence[Transaction]:
        return [tx for tx in self._transactions if tx.booking_date <= as_of]

    def load_anchors(self, as_of: date) -> Sequence[BalanceAnchor]:
        return [anchor for anchor in self._anchors if anchor.anchor_date <= as_of]

    def list_events(self) -> Sequence[PlannedEvent]:
        return sorted(self.events, key=lambda event: (event.date_from, event.id))

    def delete_event(self, event_id: int) -> bool:
        remaining = [event for event in self.events if event.id != event_id]
        deleted = len(remaining) != len(self.events)
        self.events = remaining
        return deleted

    def list_decisions(self) -> Sequence[DecisionRecord]:
        return sorted(self.decisions, key=lambda decision: decision.id, reverse=True)

    def rejected_option_fingerprints(self) -> frozenset[str]:
        return frozenset(fingerprint for fingerprint, status in self._fingerprint_status.items()
                         if status is DecisionStatus.REJECTED)

    @contextmanager
    def decision_lock(self) -> Iterator[None]:
        yield

    def withdraw_decision(self, decision_id: int, note: str) -> None:
        self.events = [event for event in self.events if event.decision_id != decision_id]
        self.decisions = [replace(decision, execution_messages=(*decision.execution_messages, note))
                          if decision.id == decision_id else decision for decision in self.decisions]

    def record_decision(self, decision: NewDecision, events_to_add: Sequence[EventDraft],
                        event_updates: Sequence[EventUpdate]) -> DecisionRecord:
        known_ids = {event.id for event in self.events}
        for update in event_updates:
            if update.event_id not in known_ids:
                raise StaleEventError(f"Planned event {update.event_id} no longer exists.")
        record = DecisionRecord(
            id=len(self.decisions) + 1, as_of_date=decision.as_of_date, horizon_days=decision.horizon_days,
            buffer_eur=decision.buffer_eur, option_id=decision.option_id, option_title=decision.option_title,
            option_description=decision.option_description, status=decision.status, execution=EXECUTION_SIMULATED,
            llm_explanation=decision.llm_explanation, execution_messages=decision.execution_messages,
            decided_at=FIXED_NOW, snapshot=snapshot_from_json(decision.forecast_snapshot),
        )
        self.decisions.append(record)
        self._fingerprint_status[decision.forecast_snapshot[FINGERPRINT_KEY]] = decision.status
        for event in events_to_add:
            self.add_event(event, EventSource.AGENT, decision.as_of_date, decision_id=record.id)
        updates = {update.event_id: update for update in event_updates}
        self.events = [self._updated(event, updates.get(event.id)) for event in self.events]
        return record

    def reset_demo(self) -> None:
        self.events = []
        self.decisions = []
        self._fingerprint_status = {}

    def add_event(self, draft: EventDraft, source: EventSource = EventSource.CHAT,
                  as_of: date = date(2025, 5, 10), decision_id: Optional[int] = None) -> PlannedEvent:
        event = PlannedEvent(
            id=self._next_event_id, event_type=draft.event_type, date_from=draft.date_from, date_to=draft.date_to,
            amount_eur=draft.amount_eur, category=draft.category, description=draft.description,
            original_utterance=None, source=source, as_of_date=as_of, decision_id=decision_id, created_at=FIXED_NOW,
        )
        self._next_event_id += 1
        self.events.append(event)
        return event

    @staticmethod
    def _updated(event: PlannedEvent, update: Optional[EventUpdate]) -> PlannedEvent:
        if update is None:
            return event
        values = {**event.__dict__, "date_from": update.date_from, "date_to": update.date_to}
        return PlannedEvent(**values)


def closure(date_from: date, date_to: date, description: str = "Summer holiday") -> EventDraft:
    return EventDraft(event_type=EventType.CLOSURE, date_from=date_from, date_to=date_to, description=description)


def expense(day: date, amount: str, description: str = "New oven") -> EventDraft:
    return EventDraft(event_type=EventType.EXPENSE, date_from=day, date_to=day, amount_eur=Decimal(amount),
                      description=description)


def inflow(day: date, amount: str, description: str = "Catering deposit") -> EventDraft:
    return EventDraft(event_type=EventType.INFLOW, date_from=day, date_to=day, amount_eur=Decimal(amount),
                      description=description)
