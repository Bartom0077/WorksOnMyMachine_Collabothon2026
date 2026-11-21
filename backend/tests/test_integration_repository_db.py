"""PostgresRepository + REST API against a real PostgreSQL (throw-away schema, see test_integration_db.py).

Runs only when KPI_TEST_DATABASE_URL is set; the real tables are never touched.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import create_app
from deps import get_repository
from domain import DecisionStatus, EventSource, EventType, EventUpdate, NewDecision
from repository import PostgresRepository, StaleEventError
from test_integration_db import DSN, schema  # noqa: F401  (pytest fixture)

pytestmark = pytest.mark.skipif(not DSN, reason="KPI_TEST_DATABASE_URL not set")

psycopg = pytest.importorskip("psycopg")

PARAMS = {"as_of_date": "2025-05-10", "horizon_days": 60, "buffer_eur": 3000}


@pytest.fixture
def connection(schema):  # noqa: F811
    conn = psycopg.connect(DSN, options=f"-c search_path={schema}", autocommit=True)
    yield conn
    conn.execute("DELETE FROM planned_events")
    conn.execute("DELETE FROM owner_decisions")
    conn.close()


@pytest.fixture
def client(connection):
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: PostgresRepository(connection)
    return TestClient(application, raise_server_exceptions=False)


def _add_chat_closure(connection) -> int:
    row = connection.execute(
        """INSERT INTO planned_events (event_type, date_from, date_to, description, original_utterance, source,
                                       as_of_date)
           VALUES ('closure', '2025-06-01', '2025-06-21', 'Summer holiday',
                   'In June we close for holidays from the 1st to the 21st', 'chat', '2025-05-10') RETURNING id"""
    ).fetchone()
    return row[0]


def test_balance_and_data_range_from_the_database(client):
    body = client.get("/dashboard", params=PARAMS).json()
    assert body["current_balance_eur"] == 3205.69
    assert body["data_range"] == {"first_date": "2025-01-01", "last_date": "2025-12-31"}
    assert body["events"] == []


def test_events_round_trip(client, connection):
    event_id = _add_chat_closure(connection)
    [event] = PostgresRepository(connection).list_events()
    assert (event.id, event.event_type, event.source, event.amount_eur) == (
        event_id, EventType.CLOSURE, EventSource.CHAT, None)
    assert event.original_utterance.startswith("In June") and event.created_at.tzinfo is not None
    assert client.delete(f"/events/{event_id}").status_code == 204
    assert client.delete(f"/events/{event_id}").status_code == 404


def test_approve_flow_is_atomic_and_turns_green(client, connection):
    closure_id = _add_chat_closure(connection)
    before = client.get("/dashboard", params=PARAMS).json()
    assert before["risk"]["status"] == "RED" and before["risk"]["min_balance_eur"] == -15728.42

    rejected = client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "rejected"})
    assert rejected.status_code == 201 and rejected.json()["execution_messages"] == []

    approved = client.post("/decisions", json={**PARAMS, "option_id": "E", "status": "approved",
                                               "llm_explanation": "Cheapest safe option."})
    assert approved.status_code == 201
    decision = approved.json()["decision"]
    assert decision["execution"] == "SIMULATED" and len(decision["execution_messages"]) == 3
    again = client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "rejected"})
    assert again.status_code == 409   # already answered: the option is gone and A's rejection is on record
    assert decision["snapshot"]["status_after"] == "GREEN"

    events = PostgresRepository(connection).list_events()
    agent_events = [event for event in events if event.source is EventSource.AGENT]
    assert sorted(event.event_type.value for event in agent_events) == [
        "credit_line", "payment_deferral", "payment_deferral"]
    assert all(event.decision_id == decision["id"] and event.as_of_date == date(2025, 5, 10) for event in agent_events)
    credit_line = next(event for event in agent_events if event.event_type is EventType.CREDIT_LINE)
    assert credit_line.amount_eur == Decimal("15000.00")

    after = client.get("/dashboard", params=PARAMS).json()
    assert after["risk"]["status"] == "GREEN" and after["options"] == []
    stored = connection.execute("SELECT forecast_snapshot FROM owner_decisions WHERE id = %s",
                                (decision["id"],)).fetchone()[0]
    assert len(stored["forecast_before"]) == len(stored["forecast_after"]) == 60

    decisions = client.get("/decisions").json()["decisions"]
    assert [d["status"] for d in decisions] == ["approved", "rejected"]   # newest first
    assert closure_id in {event.id for event in events}


def test_option_update_of_a_deleted_event_rolls_everything_back(client, connection):
    """If the event an option wants to change disappears mid-way, no decision row is left behind."""
    decision = NewDecision(
        as_of_date=date(2025, 5, 10), horizon_days=60, buffer_eur=Decimal("3000"), option_id="C",
        option_title="Shorter closure", option_description="-", status=DecisionStatus.APPROVED, llm_explanation=None,
        execution_messages=(), forecast_snapshot={
            "status_before": "RED", "min_balance_before_eur": -1.0, "min_balance_date_before": "2025-07-01",
            "status_after": "RED", "min_balance_after_eur": -1.0, "cost_eur": 0.0},
    )
    with pytest.raises(StaleEventError):
        PostgresRepository(connection).record_decision(
            decision, (), (EventUpdate(987654, date(2025, 6, 1), date(2025, 6, 7)),))
    assert connection.execute("SELECT count(*) FROM owner_decisions").fetchone()[0] == 0


def test_demo_reset(client, connection):
    _add_chat_closure(connection)
    client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "approved"})
    assert client.post("/demo/reset").status_code == 204
    assert client.get("/events").json() == {"events": []} and client.get("/decisions").json() == {"decisions": []}


def test_rejection_is_stored_per_proposal_and_cannot_be_repeated(client, connection):
    closure_id = _add_chat_closure(connection)
    reject = {**PARAMS, "option_id": "E", "status": "rejected"}
    assert client.post("/decisions", json=reject).status_code == 201
    assert client.post("/decisions", json=reject).status_code == 409
    assert connection.execute("SELECT count(*) FROM owner_decisions").fetchone()[0] == 1
    flagged = {option["id"]: option["rejected"] for option in client.get("/dashboard", params=PARAMS).json()["options"]}
    assert flagged == {"A": False, "B": False, "C": False, "E": True}
    # another situation offers a different option E: the rejection does not carry over
    connection.execute("DELETE FROM planned_events WHERE id = %s", (closure_id,))
    other = client.get("/dashboard", params={**PARAMS, "buffer_eur": 0}).json()["options"]
    assert {option["id"]: option["rejected"] for option in other}["E"] is False


def test_removing_an_event_of_an_approved_option_withdraws_the_whole_option(client, connection):
    closure_id = _add_chat_closure(connection)
    decision = client.post("/decisions", json={**PARAMS, "option_id": "E", "status": "approved"}).json()["decision"]
    repository = PostgresRepository(connection)
    credit_line = next(event for event in repository.list_events() if event.event_type is EventType.CREDIT_LINE)
    assert client.delete(f"/events/{credit_line.id}").status_code == 204
    assert [event.id for event in repository.list_events()] == [closure_id]
    [logged] = client.get("/decisions").json()["decisions"]
    assert logged["id"] == decision["id"] and logged["execution_messages"][-1].startswith("Withdrawn by the owner")
    assert decision["withdrawn_at"] is None and logged["withdrawn_at"] is not None
    assert client.get("/dashboard", params=PARAMS).json()["risk"]["status"] == "RED"


def test_simultaneous_approvals_of_the_same_option_store_it_once(schema):  # noqa: F811
    """Two tabs approving the credit line at the same moment: the decision lock lets exactly one through."""
    connections = [psycopg.connect(DSN, options=f"-c search_path={schema}", autocommit=True) for _ in range(2)]
    try:
        _add_chat_closure(connections[0])
        barrier = threading.Barrier(2)

        def approve(connection) -> int:
            application = create_app()
            application.dependency_overrides[get_repository] = lambda: PostgresRepository(connection)
            barrier.wait()
            return TestClient(application, raise_server_exceptions=False).post(
                "/decisions", json={**PARAMS, "option_id": "A", "status": "approved"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(approve, connections))
        assert statuses == [201, 409]
        lines = connections[0].execute("SELECT count(*) FROM planned_events WHERE event_type = 'credit_line'")
        assert lines.fetchone()[0] == 1
    finally:
        connections[0].execute("DELETE FROM planned_events")
        connections[0].execute("DELETE FROM owner_decisions")
        for connection in connections:
            connection.close()
