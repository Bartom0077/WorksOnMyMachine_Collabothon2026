"""Planned events from the owner's chat: validation, the two writes of this server (insert, delete), listing.

A planned event is a future fact the owner told the assistant about (a closure, a one-off expense or a
one-off inflow). The forecast engine in the backend applies it; nothing here computes balances.

Flow: ``propose_planned_event`` validates only, the owner confirms in the chat, then ``save_planned_event``
validates again and inserts one row into ``planned_events`` (source ``chat``).

Removing follows the same scheme: ``propose_planned_event_removal`` only looks the plan up, the owner confirms,
then ``remove_planned_event`` deletes that one chat row, and only if the orchestrator listed its id as confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Collection, Optional, Union

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

AGENT_EVENT_NOT_REMOVABLE = ("Planned event {id} ({description}) belongs to a rescue option the owner approved, so it "
                             "cannot be removed in the chat. Tell the owner to withdraw it with the \"Withdraw option\" "
                             "button next to it in the \"My plans\" list.")
UNKNOWN_EVENT = ("There is no planned event with id {id}. Call list_planned_events and use the id of the plan the "
                 "owner means; if no plan matches, tell the owner so and name the plans that exist.")
ALREADY_REMOVED = ("Planned event {id} is already gone from the plans; nothing was left to remove. Tell the owner "
                   "that it is no longer in the plans.")
REMOVAL_NOT_CONFIRMED = ("Planned event {id} was not removed: the owner has not confirmed its removal yet. Call "
                         "propose_planned_event_removal with this id, ask the owner to confirm, and remove it only "
                         "after the owner said yes.")

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


def _in_words(event_type: str, date_from: date, date_to: date, amount_eur: Union[Decimal, float, None]) -> str:
    if event_type == EventType.CLOSURE.value:
        days = (date_to - date_from).days + 1
        return f"closure from {date_from.isoformat()} to {date_to.isoformat()} ({days} day{'s' if days != 1 else ''})"
    return f"{event_type} of EUR {amount_eur:,.2f} on {date_from.isoformat()}"


def confirmation_prompt(event: PlannedEvent) -> str:
    """The question to put to the owner before saving."""
    what = _in_words(event.event_type.value, event.date_from, event.date_to, event.amount_eur)
    return f"I understood: {what} - {event.description}. Should I include it in the forecast?"


def removal_confirmation_prompt(row: dict) -> str:
    """The question to put to the owner before removing a saved plan (``row`` as returned by ``fetch_dicts``)."""
    what = _in_words(row["event_type"], date.fromisoformat(row["date_from"]), date.fromisoformat(row["date_to"]),
                     row["amount_eur"])
    return f"You want to remove this plan: {what} - {row['description']}. Should I remove it from your plans?"


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


def _event_id(value: Union[int, str, None]) -> int:
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ToolInputError(f"event_id must be the numeric id of a plan from list_planned_events, got {value!r}.") from exc


def _stored_event(conn: psycopg.Connection, event_id: int) -> Optional[dict]:
    """The saved plan with this id, if it is a chat plan; a measure of an approved option is refused."""
    rows = fetch_dicts(conn, f"SELECT {EVENT_COLUMNS} FROM planned_events WHERE id = %s", (event_id,))
    if rows and rows[0]["source"] != CHAT_SOURCE:
        raise ToolInputError(AGENT_EVENT_NOT_REMOVABLE.format(id=event_id, description=rows[0]["description"]))
    return rows[0] if rows else None


def propose_planned_event_removal(conn: psycopg.Connection, event_id: Union[int, str, None]) -> dict:
    """Look up the plan the owner wants to remove. Nothing is written."""
    wanted = _event_id(event_id)
    event = _stored_event(conn, wanted)
    if event is None:
        raise ToolInputError(UNKNOWN_EVENT.format(id=wanted))
    return {"valid": True, "event": event, "confirmation_prompt": removal_confirmation_prompt(event)}


def remove_planned_event(conn: psycopg.Connection, event_id: Union[int, str, None],
                         confirmed_ids: Collection[int]) -> dict:
    """Delete one chat plan. ``conn`` must be the read-write connection (db.connect_write).

    ``confirmed_ids`` are the plans the owner was shown for removal (set by the orchestrator, not by the model);
    any other id is refused before the database is touched. A confirmed plan that no longer exists (the request
    was repeated, or the owner removed it in the app meanwhile) is reported as ``already_removed``, not as an
    error: what the owner confirmed is true.
    """
    wanted = _event_id(event_id)
    if wanted not in confirmed_ids:
        raise ToolInputError(REMOVAL_NOT_CONFIRMED.format(id=wanted))
    already_removed = {"removed": False, "already_removed": True, "event_id": wanted,
                       "note": ALREADY_REMOVED.format(id=wanted)}
    if _stored_event(conn, wanted) is None:
        return already_removed
    rows = fetch_dicts(conn, f"DELETE FROM planned_events WHERE id = %s AND source = %s RETURNING {EVENT_COLUMNS}",
                       (wanted, CHAT_SOURCE))
    if not rows:  # removed by someone else between the statements
        return already_removed
    return {"removed": True, "event": rows[0]}


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

