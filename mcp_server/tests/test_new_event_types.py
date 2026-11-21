"""Events of the CFO advisors (term_deposit, fx_prebuy) seen through this server: listed with a summary in
words, never creatable from the chat, never removable in the chat.

The database tests at the end run only when KPI_TEST_DATABASE_URL is set; they work in a throw-away schema
built from db/schema.sql, so the real tables are never touched.
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from typing import get_args

import pytest

import planned_events as pe
from db import ToolInputError
from test_planned_events import ROOT, TODAY, RecordingConnection, event_row

LISTED_COLUMNS = ["id", "event_type", "date_from", "date_to", "amount_eur", "category", "description", "source"]
TAX_RESERVE = (31, "term_deposit", date(2025, 10, 16), date(2025, 11, 16), Decimal("9000.00"), "tax_reserve",
               "Tax reserve deposit", "agent")
INVESTMENT = (32, "term_deposit", date(2025, 10, 16), date(2026, 4, 16), Decimal("12000.00"), "investment",
              "Investment deposit", "agent")
FX_PREBUY = (33, "fx_prebuy", date(2025, 10, 16), date(2025, 12, 15), Decimal("8716.67"), None,
             "CHF bought for the remaining Zurich instalments", "agent")
OCTOBER_15 = date(2025, 10, 15)


def listed(*rows):
    return pe.list_planned_events(RecordingConnection([(LISTED_COLUMNS, list(rows))]), OCTOBER_15)["events"]


# --- listing ------------------------------------------------------------------------------------------------

def test_a_tax_reserve_deposit_is_listed_with_its_description_and_a_summary_in_words():
    [event] = listed(TAX_RESERVE)
    assert event == {
        "id": 31, "event_type": "term_deposit", "date_from": "2025-10-16", "date_to": "2025-11-16",
        "amount_eur": 9000.0, "category": "tax_reserve", "description": "Tax reserve deposit", "source": "agent",
        "summary": "Money set aside for income tax: EUR 9,000.00 is tied up in a term deposit with the bank from "
                   "2025-10-16 until 2025-11-16, when it returns to the account with interest. The owner approved "
                   "it; the forecast in the SITUATION already includes it."}


def test_an_investment_deposit_is_told_apart_from_a_tax_reserve():
    [event] = listed(INVESTMENT)
    assert event["description"] == "Investment deposit"
    assert event["summary"].startswith("Spare cash invested: EUR 12,000.00 is tied up in a term deposit with the "
                                       "bank from 2025-10-16 until 2026-04-16")


def test_an_advance_currency_purchase_is_listed_as_one_payment_that_replaces_the_instalments():
    [event] = listed(FX_PREBUY)
    assert event["description"] == "CHF bought for the remaining Zurich instalments"
    assert event["summary"] == (
        "Swiss francs bought in advance: one payment of EUR 8,716.67 on 2025-10-16 covers every Zurich instalment "
        "due up to 2025-12-15, so those instalments no longer leave the account. The owner approved it; the "
        "forecast in the SITUATION already includes it.")


@pytest.mark.parametrize("row", [TAX_RESERVE, INVESTMENT, FX_PREBUY])
def test_a_summary_states_no_figure_beyond_the_stored_amount(row):
    """The server computes nothing: no interest, no balance, no exchange rate."""
    [event] = listed(row)
    figures = {word.strip(".,;:") for word in event["summary"].split() if word[0].isdigit()}
    assert figures == {f"{row[4]:,.2f}", row[2].isoformat(), row[3].isoformat()}


def test_events_of_the_other_types_are_listed_unchanged():
    closure = (12, "closure", date(2025, 11, 1), date(2025, 11, 7), None, None, "Autumn break", "chat")
    credit_line = (22, "credit_line", date(2025, 10, 16), date(2025, 12, 14), Decimal("20000.00"), None,
                   "Credit line", "agent")
    events = listed(closure, TAX_RESERVE, credit_line)
    assert [event["id"] for event in events] == [12, 31, 22]
    assert ["summary" in event for event in events] == [False, True, False]


# --- the chat can neither create nor remove them --------------------------------------------------------------

@pytest.mark.parametrize("event_type", ["term_deposit", "fx_prebuy"])
def test_the_chat_cannot_create_an_advisor_event(event_type):
    with pytest.raises(ToolInputError, match="event_type must be one of: closure, expense, inflow"):
        pe.validate_event(TODAY, event_type, "2025-06-01", None, 5000, "Deposit")


def test_the_tool_schema_offers_only_the_chat_event_types():
    import app
    literal = get_args(app.ChatEventType)[0]
    assert get_args(literal) == ("closure", "expense", "inflow")


@pytest.mark.parametrize("event_type, description", [("term_deposit", "Tax reserve deposit"),
                                                     ("fx_prebuy", "CHF bought in advance")])
@pytest.mark.parametrize("tool", [
    lambda conn: pe.propose_planned_event_removal(conn, 31),
    lambda conn: pe.remove_planned_event(conn, 31, {31}),
])
def test_an_advisor_event_cannot_be_removed_in_the_chat(tool, event_type, description):
    conn = RecordingConnection([event_row(31, source="agent", event_type=event_type, description=description)])
    with pytest.raises(ToolInputError) as refusal:
        tool(conn)
    assert str(refusal.value) == (
        f"Planned event 31 ({description}) belongs to a CFO recommendation the owner approved, so it cannot be "
        "removed in the chat. Tell the owner to withdraw it with the \"Withdraw recommendation\" button next to it "
        "in the \"My plans\" list.")
    assert len(conn.executed) == 1 and conn.executed[0][0].startswith("SELECT")


def test_a_measure_of_a_rescue_option_keeps_its_refusal_text():
    conn = RecordingConnection([event_row(22, source="agent", event_type="credit_line", description="Credit line")])
    with pytest.raises(ToolInputError, match=r"\(Credit line\) belongs to a rescue option the owner approved, so "
                                             r"it cannot be removed in the chat\. Tell the owner to withdraw it "
                                             r"with the \"Withdraw option\" button"):
        pe.propose_planned_event_removal(conn, 22)


# --- against PostgreSQL -------------------------------------------------------------------------------------

DSN = os.environ.get("KPI_TEST_DATABASE_URL")
needs_database = pytest.mark.skipif(not DSN, reason="KPI_TEST_DATABASE_URL not set")

INSERT_EVENT = """INSERT INTO planned_events (event_type, date_from, date_to, amount_eur, category, description,
                                              source, as_of_date)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"""


@pytest.fixture
def database():
    """A connection to a throw-away schema that holds the current db/schema.sql."""
    import psycopg
    name = f"mcp_test_{uuid.uuid4().hex[:10]}"
    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute(f"CREATE SCHEMA {name}")
    conn.execute(f"SET search_path TO {name}")
    conn.execute((ROOT.parent / "db" / "schema.sql").read_text(encoding="utf-8"))
    yield conn
    conn.execute(f"DROP SCHEMA {name} CASCADE")
    conn.close()


def stored(conn, row, source="agent") -> int:
    _, event_type, date_from, date_to, amount, category, description, _ = row
    return conn.execute(INSERT_EVENT, (event_type, date_from, date_to, amount, category, description, source,
                                       OCTOBER_15)).fetchone()[0]


@needs_database
def test_stored_advisor_events_are_listed_with_summaries(database):
    ids = [stored(database, row) for row in (TAX_RESERVE, INVESTMENT, FX_PREBUY)]
    events = pe.list_planned_events(database, OCTOBER_15)["events"]
    assert [(event["id"], event["event_type"], event["category"], event["amount_eur"]) for event in events] == [
        (ids[0], "term_deposit", "tax_reserve", 9000.0), (ids[1], "term_deposit", "investment", 12000.0),
        (ids[2], "fx_prebuy", None, 8716.67)]
    assert [event["summary"].split(":")[0] for event in events] == [
        "Money set aside for income tax", "Spare cash invested", "Swiss francs bought in advance"]


@needs_database
def test_a_matured_deposit_is_no_longer_listed(database):
    stored(database, TAX_RESERVE)
    assert pe.list_planned_events(database, date(2025, 11, 16))["events"] == []


@needs_database
@pytest.mark.parametrize("row", [TAX_RESERVE, FX_PREBUY])
def test_a_stored_advisor_event_survives_both_removal_tools(database, row):
    event_id = stored(database, row)
    with pytest.raises(ToolInputError, match="belongs to a CFO recommendation the owner approved"):
        pe.propose_planned_event_removal(database, event_id)
    with pytest.raises(ToolInputError, match="belongs to a CFO recommendation the owner approved"):
        pe.remove_planned_event(database, event_id, {event_id})
    assert database.execute("SELECT count(*) FROM planned_events WHERE id = %s", (event_id,)).fetchone()[0] == 1


@needs_database
@pytest.mark.parametrize("row", [TAX_RESERVE, FX_PREBUY])
def test_the_database_refuses_an_advisor_event_written_as_a_chat_row(database, row):
    import psycopg
    chat_row = (*row[:5], None, *row[6:])  # even without a category: the type alone is outside the chat scope
    with pytest.raises(psycopg.errors.CheckViolation, match="planned_events_chat_scope"):
        stored(database, chat_row, source="chat")


@needs_database
def test_the_database_refuses_an_unknown_event_type(database):
    import psycopg
    unknown = (0, "bond_purchase", *TAX_RESERVE[2:])
    with pytest.raises(psycopg.errors.CheckViolation, match="planned_events_event_type"):
        stored(database, unknown)
