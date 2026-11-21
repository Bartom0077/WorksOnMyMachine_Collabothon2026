"""Planned events from the owner's chat: validation, the single write of this server, and listing.

A planned event is a future fact the owner told the assistant about (a closure, a one-off expense or a
one-off inflow). The forecast engine in the backend applies it; nothing here computes balances.

Flow: ``propose_planned_event`` validates only, the owner confirms in the chat, then ``save_planned_event``
validates again and inserts one row into ``planned_events`` (source ``chat``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Union

import psycopg

from db import ToolInputError, fetch_dicts, parse_date

MAX_CLOSURE_DAYS = 120
MAX_DAYS_AHEAD = 366
MAX_AMOUNT_EUR = Decimal("10000000")
MAX_DESCRIPTION_CHARS = 200
MAX_UTTERANCE_CHARS = 1000
MAX_LISTED_EVENTS = 50
CHAT_SOURCE = "chat"

# The forecast starts on the day after today (today's bookings are already in the bank balance), so an event
# that is over by the end of today would be saved without changing any number.
CLOSURE_ENDS_TODAY = ("A closure that ends today ({today}) changes nothing: the forecast starts tomorrow. "
                      "Set date_to to tomorrow or later.")
PAYMENT_DATED_TODAY = ("Today's ({today}) payments are already in the bank balance and the forecast starts "
                       "tomorrow. Put tomorrow or a later date in date_from; ask the owner if unsure.")

EVENT_COLUMNS = ("id, event_type, date_from, date_to, amount_eur, category, description, original_utterance, "
                 "source, as_of_date, decision_id, created_at")


class EventType(str, Enum):
    """Event types the chat assistant may create (payment_deferral / credit_line come only from approved
    rescue options and are written by the backend, never through this server)."""
    CLOSURE = "closure"
    EXPENSE = "expense"
    INFLOW = "inflow"


@dataclass(frozen=True)
class PlannedEvent:
    event_type: EventType
    date_from: date
    date_to: date
    amount_eur: Optional[Decimal]
    description: str

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "amount_eur": float(self.amount_eur) if self.amount_eur is not None else None,
            "description": self.description,
        }


# --------------------------------------------------------------------------- validation

def _event_type(value: str) -> EventType:
    try:
        return EventType(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(t.value for t in EventType)
        raise ToolInputError(f"event_type must be one of: {allowed}; got {value!r}.") from exc


def _text(value: Optional[str], name: str, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ToolInputError(f"{name} is required and must not be empty.")
    if len(text) > max_chars:
        raise ToolInputError(f"{name} is {len(text)} characters long; the maximum is {max_chars}. Shorten it.")
    return text


def _dates(event_type: EventType, date_from: Optional[str], date_to: Optional[str], as_of: date) -> tuple:
    d_from = parse_date(date_from, "date_from")
    if d_from is None:
        raise ToolInputError("date_from is required (YYYY-MM-DD). Ask the owner for the exact date.")
    d_to = parse_date(date_to, "date_to") or d_from
    today = as_of.isoformat()
    if d_from < as_of:
        raise ToolInputError(f"date_from {d_from.isoformat()} is before today ({today}). Planned events must be "
                             f"in the future; check the year and ask the owner if unsure.")
    if d_to < d_from:
        raise ToolInputError(f"date_to {d_to.isoformat()} is before date_from {d_from.isoformat()}.")
    if (d_from - as_of).days > MAX_DAYS_AHEAD:
        raise ToolInputError(f"date_from {d_from.isoformat()} is more than {MAX_DAYS_AHEAD} days after today "
                             f"({today}). Check the year.")
    if d_to <= as_of:
        message = CLOSURE_ENDS_TODAY if event_type is EventType.CLOSURE else PAYMENT_DATED_TODAY
        raise ToolInputError(message.format(today=today))
    length = (d_to - d_from).days + 1
    if event_type is EventType.CLOSURE:
        if length > MAX_CLOSURE_DAYS:
            raise ToolInputError(f"A closure of {length} days is too long; the maximum is {MAX_CLOSURE_DAYS} days.")
    elif length > 1:
        raise ToolInputError(f"An {event_type.value} is a single-day event: omit date_to and put the payment date "
                             "in date_from.")
    return d_from, d_to


def _amount(event_type: EventType, amount_eur: Union[float, int, str, Decimal, None]) -> Optional[Decimal]:
    if event_type is EventType.CLOSURE:
        if amount_eur is not None:
            raise ToolInputError("A closure has no amount: omit amount_eur (the forecast engine computes the "
                                 "lost revenue). Use a separate expense or inflow event for a one-off amount.")
        return None
    if amount_eur is None:
        raise ToolInputError(f"amount_eur is required for an {event_type.value} (EUR, greater than 0). "
                             "Ask the owner for the amount instead of guessing.")
    try:
        amount = Decimal(str(amount_eur))
    except InvalidOperation as exc:
        raise ToolInputError(f"amount_eur must be a number in EUR, got {amount_eur!r}.") from exc
    if not amount.is_finite():
        raise ToolInputError(f"amount_eur must be a finite number in EUR, got {amount_eur!r}.")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ToolInputError("amount_eur must be greater than 0: pass a positive number; the event_type "
                             "(expense or inflow) decides the direction.")
    if amount > MAX_AMOUNT_EUR:
        raise ToolInputError(f"amount_eur {amount} exceeds the maximum of {MAX_AMOUNT_EUR} EUR.")
    return amount


def validate_event(as_of: date, event_type: str, date_from: Optional[str], date_to: Optional[str],
                   amount_eur: Union[float, int, str, Decimal, None], description: Optional[str]) -> PlannedEvent:
    """Check one event against the rules of spec section 8.3; raises ToolInputError with a fixable message."""
    kind = _event_type(event_type)
    d_from, d_to = _dates(kind, date_from, date_to, as_of)
    return PlannedEvent(
        event_type=kind,
        date_from=d_from,
        date_to=d_to,
        amount_eur=_amount(kind, amount_eur),
        description=_text(description, "description", MAX_DESCRIPTION_CHARS),
    )


def confirmation_prompt(event: PlannedEvent) -> str:
    """The question to put to the owner before saving."""
    if event.event_type is EventType.CLOSURE:
        days = (event.date_to - event.date_from).days + 1
        what = (f"closure from {event.date_from.isoformat()} to {event.date_to.isoformat()} "
                f"({days} day{'s' if days != 1 else ''})")
    else:
        what = f"{event.event_type.value} of EUR {event.amount_eur:,.2f} on {event.date_from.isoformat()}"
    return f"I understood: {what} - {event.description}. Should I include it in the forecast?"


# --------------------------------------------------------------------------- tools

def propose_planned_event(as_of: date, event_type: str, date_from: Optional[str], date_to: Optional[str] = None,
                          amount_eur: Optional[float] = None, description: Optional[str] = None) -> dict:
    event = validate_event(as_of, event_type, date_from, date_to, amount_eur, description)
    return {"valid": True, "proposal": event.as_dict(), "confirmation_prompt": confirmation_prompt(event)}


def _find_saved(conn: psycopg.Connection, event: PlannedEvent) -> list:
    return fetch_dicts(conn, f"""
        SELECT {EVENT_COLUMNS} FROM planned_events
        WHERE source = %s AND event_type = %s AND date_from = %s AND date_to = %s
          AND amount_eur IS NOT DISTINCT FROM %s AND lower(description) = lower(%s)
        ORDER BY id LIMIT 1""",
                       (CHAT_SOURCE, event.event_type.value, event.date_from, event.date_to, event.amount_eur,
                        event.description))


def save_planned_event(conn: psycopg.Connection, as_of: date, event_type: str, date_from: Optional[str],
                       date_to: Optional[str] = None, amount_eur: Optional[float] = None,
                       description: Optional[str] = None, original_utterance: Optional[str] = None) -> dict:
    """Insert one confirmed chat event. ``conn`` must be the read-write connection (db.connect_write).

    A repeated call with an identical event does not insert a second row (an LLM retry must not
    double-count an expense): the existing row is returned with ``saved: false``.
    """
    event = validate_event(as_of, event_type, date_from, date_to, amount_eur, description)
    utterance = _text(original_utterance, "original_utterance", MAX_UTTERANCE_CHARS)
    existing = _find_saved(conn, event)
    if existing:
        return {"saved": False, "already_saved": True, "event": existing[0],
                "note": "This event is already in the plans; nothing was added."}
    rows = fetch_dicts(conn, f"""
        INSERT INTO planned_events
            (event_type, date_from, date_to, amount_eur, description, original_utterance, source, as_of_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {EVENT_COLUMNS}""",
                       (event.event_type.value, event.date_from, event.date_to, event.amount_eur, event.description,
                        utterance, CHAT_SOURCE, as_of))
    return {"saved": True, "event": rows[0]}


def list_planned_events(conn: psycopg.Connection, cutoff: Optional[date]) -> dict:
    """Planned events that still affect the forecast on the analysis date: they end after it, the same rule the
    backend forecast applies (all events when there is no cutoff)."""
    rows = fetch_dicts(conn, """
        SELECT id, event_type, date_from, date_to, amount_eur, category, description, source
        FROM planned_events WHERE date_to > %s
        ORDER BY date_from, id LIMIT %s""", (cutoff or date.min, MAX_LISTED_EVENTS + 1))
    out = {"analysis_date": cutoff.isoformat() if cutoff else None, "events": rows[:MAX_LISTED_EVENTS]}
    if len(rows) > MAX_LISTED_EVENTS:
        out["notes"] = [f"Only the first {MAX_LISTED_EVENTS} events are shown."]
    return out

