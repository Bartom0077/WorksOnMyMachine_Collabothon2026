"""Validation rules for planned events (spec section 8.3); no database needed."""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import planned_events as pe  # noqa: E402
from db import ToolInputError  # noqa: E402

TODAY = date(2025, 5, 10)


class RecordingConnection:
    """Stands in for psycopg: returns canned rows per statement and records what was executed."""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        self.executed.append((" ".join(str(query).split()), tuple(params)))
        self._columns, self._rows = self._results.pop(0)

    @property
    def description(self):
        return [type("Column", (), {"name": name}) for name in self._columns]

    def fetchall(self):
        return self._rows


def test_closure_proposal():
    out = pe.propose_planned_event(TODAY, "closure", "2025-06-01", "2025-06-21", None, "  Summer   holiday ")
    assert out["valid"] is True
    assert out["proposal"] == {"event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21",
                               "amount_eur": None, "description": "Summer holiday"}
    assert "2025-06-01 to 2025-06-21 (21 days)" in out["confirmation_prompt"]


def test_expense_defaults_date_to_and_rounds_amount():
    event = pe.validate_event(TODAY, "expense", "2025-07-15", None, 7999.999, "New oven")
    assert (event.date_from, event.date_to) == (date(2025, 7, 15), date(2025, 7, 15))
    assert event.amount_eur == Decimal("8000.00")
    assert "expense of EUR 8,000.00 on 2025-07-15" in pe.confirmation_prompt(event)


@pytest.mark.parametrize("event_type", ["expense", "inflow"])
def test_payment_dated_today_is_rejected_because_the_forecast_starts_tomorrow(event_type):
    with pytest.raises(ToolInputError, match="already in the bank balance"):
        pe.validate_event(TODAY, event_type, "2025-05-10", None, 500, "Catering deposit")
    assert pe.validate_event(TODAY, event_type, "2025-05-11", None, 500, "Catering deposit").date_from == date(2025, 5, 11)


def test_closure_must_end_after_today():
    with pytest.raises(ToolInputError, match="ends today"):
        pe.validate_event(TODAY, "closure", "2025-05-10", "2025-05-10", None, "Closed today")
    event = pe.validate_event(TODAY, "closure", "2025-05-10", "2025-05-11", None, "Closed for the weekend")
    assert (event.date_from, event.date_to) == (TODAY, date(2025, 5, 11))


@pytest.mark.parametrize("event_type", ["payment_deferral", "credit_line", "zamkniecie", "", "DROP TABLE"])
def test_only_chat_event_types_are_accepted(event_type):
    with pytest.raises(ToolInputError, match="event_type must be one of: closure, expense, inflow"):
        pe.validate_event(TODAY, event_type, "2025-06-01", None, 100, "x")


def test_past_date_is_rejected():
    with pytest.raises(ToolInputError, match="before today"):
        pe.validate_event(TODAY, "closure", "2025-05-09", "2025-05-20", None, "Holiday")


def test_missing_or_malformed_dates_are_rejected():
    with pytest.raises(ToolInputError, match="date_from is required"):
        pe.validate_event(TODAY, "closure", None, None, None, "Holiday")
    with pytest.raises(ToolInputError, match="YYYY-MM-DD"):
        pe.validate_event(TODAY, "closure", "June 1st", None, None, "Holiday")


def test_date_to_before_date_from_is_rejected():
    with pytest.raises(ToolInputError, match="before date_from"):
        pe.validate_event(TODAY, "closure", "2025-06-21", "2025-06-01", None, "Holiday")


def test_date_too_far_ahead_is_rejected():
    with pytest.raises(ToolInputError, match="Check the year"):
        pe.validate_event(TODAY, "expense", "2027-06-01", None, 100, "Oven")


def test_closure_length_limit():
    pe.validate_event(TODAY, "closure", "2025-06-01", "2025-09-28", None, "Renovation")  # exactly 120 days
    with pytest.raises(ToolInputError, match="maximum is 120 days"):
        pe.validate_event(TODAY, "closure", "2025-06-01", "2025-09-29", None, "Renovation")


def test_closure_must_not_have_an_amount():
    with pytest.raises(ToolInputError, match="omit amount_eur"):
        pe.validate_event(TODAY, "closure", "2025-06-01", "2025-06-21", 5000, "Holiday")


@pytest.mark.parametrize("event_type", ["expense", "inflow"])
def test_amount_is_required_for_money_events(event_type):
    with pytest.raises(ToolInputError, match="amount_eur is required"):
        pe.validate_event(TODAY, event_type, "2025-07-15", None, None, "Oven")


@pytest.mark.parametrize("amount", [0, -8000, 0.004, float("nan"), float("inf"), "eight thousand", 10_000_000.01])
def test_bad_amounts_are_rejected(amount):
    with pytest.raises(ToolInputError, match="amount_eur"):
        pe.validate_event(TODAY, "expense", "2025-07-15", None, amount, "Oven")


def test_money_events_are_single_day():
    with pytest.raises(ToolInputError, match="single-day"):
        pe.validate_event(TODAY, "expense", "2025-07-15", "2025-07-20", 8000, "Oven")


def test_description_is_required_and_bounded():
    with pytest.raises(ToolInputError, match="description is required"):
        pe.validate_event(TODAY, "closure", "2025-06-01", None, None, "   ")
    with pytest.raises(ToolInputError, match="maximum is 200"):
        pe.validate_event(TODAY, "closure", "2025-06-01", None, None, "x" * 201)


def test_save_validates_before_touching_the_database():
    conn = RecordingConnection([])
    with pytest.raises(ToolInputError, match="before today"):
        pe.save_planned_event(conn, TODAY, "expense", "2025-01-01", None, 100, "Oven", "I bought an oven")
    with pytest.raises(ToolInputError, match="original_utterance is required"):
        pe.save_planned_event(conn, TODAY, "expense", "2025-07-15", None, 100, "Oven", "")
    assert conn.executed == []


def test_save_inserts_a_chat_event_with_the_as_of_date():
    saved = {"id": 7, "event_type": "expense"}
    conn = RecordingConnection([(["id"], []), (list(saved), [tuple(saved.values())])])
    out = pe.save_planned_event(conn, TODAY, "expense", "2025-07-15", None, 8000, "New oven",
                                "in July I am buying an oven for 8k")
    assert out == {"saved": True, "event": saved}
    statement, params = conn.executed[1]
    assert statement.startswith("INSERT INTO planned_events")
    assert params == ("expense", date(2025, 7, 15), date(2025, 7, 15), Decimal("8000.00"), "New oven",
                      "in July I am buying an oven for 8k", "chat", TODAY)


def test_save_does_not_insert_an_identical_event_twice():
    existing = {"id": 7, "event_type": "closure"}
    conn = RecordingConnection([(list(existing), [tuple(existing.values())])])
    out = pe.save_planned_event(conn, TODAY, "closure", "2025-06-01", "2025-06-21", None, "Holiday", "we close")
    assert out["saved"] is False and out["already_saved"] is True and out["event"] == existing
    assert len(conn.executed) == 1 and conn.executed[0][0].startswith("SELECT")


def test_tool_signature_exposes_exactly_the_chat_event_types():
    import app
    literal = get_args(app.ChatEventType)[0]
    assert set(get_args(literal)) == {t.value for t in pe.EventType}


class FakeContext:
    """A tool Context carrying only the HTTP headers of the request."""

    def __init__(self, headers):
        request = type("Request", (), {"headers": headers})
        self.request_context = type("RequestContext", (), {"request": request})


def test_propose_tool_returns_the_proposal():
    import app
    out = app.propose_planned_event(FakeContext({"x-as-of-date": "2025-05-10"}), "expense", "2025-07-15", "New oven",
                                    None, 8000)
    assert out["valid"] is True and out["proposal"]["amount_eur"] == 8000.0


def test_propose_tool_reports_a_rejection_as_a_result():
    import app
    out = app.propose_planned_event(FakeContext({"x-as-of-date": "2025-05-10"}), "expense", "2025-07-15", "New oven")
    assert out["valid"] is False and "amount_eur is required" in out["error"]


def test_save_tool_rejects_before_connecting(monkeypatch):
    import app

    def no_connection():
        raise AssertionError("an invalid event must not open a database connection")

    monkeypatch.setattr(app, "connect_write", no_connection)
    out = app.save_planned_event(FakeContext({}), "closure", "2025-06-01", "Holiday", "we close in June")
    assert out["saved"] is False and "X-As-Of-Date" in out["error"]


def test_as_of_header_is_required_outside_a_request():
    import app

    class NoRequestContext:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")

    with pytest.raises(ToolInputError, match="X-As-Of-Date"):
        app._today(NoRequestContext())


def test_list_returns_only_events_that_end_after_the_analysis_date():
    """Same boundary as the backend forecast (date_to > as-of): an event that ended today is not listed."""
    conn = RecordingConnection([(["id"], [(1,)])])
    out = pe.list_planned_events(conn, TODAY)
    statement, params = conn.executed[0]
    assert "WHERE date_to > %s" in statement and params[0] == TODAY
    assert out == {"analysis_date": "2025-05-10", "events": [{"id": 1}]}
