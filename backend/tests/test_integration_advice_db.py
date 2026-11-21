"""Advisor decisions against a real PostgreSQL: ``record_decisions`` / ``decision_fingerprint_statuses`` of
PostgresRepository and the /advice endpoints on top of them (throw-away schema, see test_integration_db.py).

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
from domain import DecisionStatus, EventDraft, EventSource, EventType, NewDecision
from repository import FINGERPRINT_KEY, PostgresRepository
from test_integration_db import DSN, schema  # noqa: F401  (pytest fixture)

pytestmark = pytest.mark.skipif(not DSN, reason="KPI_TEST_DATABASE_URL not set")

psycopg = pytest.importorskip("psycopg")

DEC_31 = {"as_of_date": "2025-12-31", "horizon_days": 60, "buffer_eur": 3000}
DEPOSIT = EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=date(2026, 1, 1), date_to=date(2026, 7, 1),
                     amount_eur=Decimal("12000.00"), category="investment", description="Investment deposit")
TOO_LONG_ID = "T10000"  # owner_decisions.option_id is VARCHAR(5)


def _connect(schema_name: str):
    return psycopg.connect(DSN, options=f"-c search_path={schema_name}", autocommit=True)


def _clear(conn) -> None:
    conn.execute("DELETE FROM planned_events")
    conn.execute("DELETE FROM owner_decisions")


@pytest.fixture
def connection(schema):  # noqa: F811
    conn = _connect(schema)
    yield conn
    _clear(conn)
    conn.close()


@pytest.fixture
def repository(connection) -> PostgresRepository:
    return PostgresRepository(connection)


def _client(connection) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: PostgresRepository(connection)
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def client(connection):
    return _client(connection)


def _decision(proposal_id: str, fingerprint: str, status: DecisionStatus = DecisionStatus.APPROVED) -> NewDecision:
    snapshot = {FINGERPRINT_KEY: fingerprint, "status_before": "GREEN", "min_balance_before_eur": 30430.81,
                "min_balance_date_before": "2026-01-01", "status_after": "GREEN", "min_balance_after_eur": 18430.81,
                "credit_limit_after_eur": None, "cost_eur": 0.0}
    return NewDecision(as_of_date=date(2025, 12, 31), horizon_days=60, buffer_eur=Decimal("3000"),
                       option_id=proposal_id, option_title=f"Proposal {proposal_id}", option_description="-",
                       status=status, llm_explanation=None, execution_messages=(), forecast_snapshot=snapshot)


def _count(connection, table: str) -> int:
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _decide(client, proposal_ids, status="approved"):
    return client.post("/advice/decisions", json={**DEC_31, "proposal_ids": proposal_ids, "status": status})


def _shown(client) -> list[str]:
    return [proposal["id"] for proposal in client.get("/advice", params=DEC_31).json()["proposals"]]


# --- the repository -----------------------------------------------------------------------------------------

def test_record_decisions_links_every_event_to_its_own_decision(repository):
    records = repository.record_decisions([(_decision("I1", "deposit"), (DEPOSIT,)), (_decision("T2", "note"), ())])
    assert [(record.option_id, record.status, record.execution) for record in records] == [
        ("I1", DecisionStatus.APPROVED, "SIMULATED"), ("T2", DecisionStatus.APPROVED, "SIMULATED")]
    assert records[0].id != records[1].id and records[0].decided_at.tzinfo is not None
    [event] = repository.list_events()
    assert (event.event_type, event.category, event.amount_eur, event.source, event.as_of_date,
            event.decision_id) == (EventType.TERM_DEPOSIT, "investment", Decimal("12000.00"), EventSource.AGENT,
                                   date(2025, 12, 31), records[0].id)


def test_record_decisions_stores_all_or_nothing(repository, connection):
    with pytest.raises(psycopg.errors.StringDataRightTruncation):
        repository.record_decisions([(_decision("I1", "deposit"), (DEPOSIT,)), (_decision(TOO_LONG_ID, "refused"), ())])
    assert _count(connection, "owner_decisions") == 0 and _count(connection, "planned_events") == 0
    assert repository.decision_fingerprint_statuses() == {}


def test_fingerprint_statuses_keep_the_latest_decision_and_drop_withdrawn_ones(repository):
    assert repository.decision_fingerprint_statuses() == {}
    [deposit, note] = repository.record_decisions([(_decision("I1", "deposit"), (DEPOSIT,)),
                                                   (_decision("T2", "note", DecisionStatus.REJECTED), ())])
    assert repository.decision_fingerprint_statuses() == {"deposit": DecisionStatus.APPROVED,
                                                          "note": DecisionStatus.REJECTED}
    repository.record_decisions([(_decision("T2", "note"), ())])  # decided again later: the latest one counts
    assert repository.decision_fingerprint_statuses()["note"] is DecisionStatus.APPROVED

    repository.withdraw_decision(deposit.id, "Withdrawn by the owner.")
    assert repository.decision_fingerprint_statuses() == {"note": DecisionStatus.APPROVED}
    assert repository.list_events() == [] and note.withdrawn_at is None


def test_fingerprint_statuses_ignore_decisions_without_a_fingerprint(repository):
    legacy = _decision("A", "unused")
    del legacy.forecast_snapshot[FINGERPRINT_KEY]  # rescue-option decisions recorded before fingerprints existed
    repository.record_decisions([(legacy, ())])
    assert repository.decision_fingerprint_statuses() == {}


# --- the endpoints on PostgreSQL ----------------------------------------------------------------------------

def test_approve_withdraw_and_reset_through_the_api(client, connection):
    assert _shown(client) == ["T1", "T2", "I1"]
    approved = _decide(client, ["T1", "T2", "I1"])
    assert approved.status_code == 201
    decisions = approved.json()["decisions"]
    assert [decision["option_id"] for decision in decisions] == ["T1", "T2", "I1"]
    assert all(decision["snapshot"]["min_balance_after_eur"] == 3581.16 for decision in decisions)
    stored = connection.execute("SELECT forecast_snapshot FROM owner_decisions WHERE id = %s",
                                (decisions[0]["id"],)).fetchone()[0]
    assert len(stored["forecast_before"]) == len(stored["forecast_after"]) == 60 and stored[FINGERPRINT_KEY]
    events = PostgresRepository(connection).list_events()
    assert [(event.event_type, event.category, event.amount_eur, event.decision_id) for event in events] == [
        (EventType.TERM_DEPOSIT, "tax_reserve", Decimal("14849.65"), decisions[0]["id"]),
        (EventType.TERM_DEPOSIT, "investment", Decimal("12000.00"), decisions[2]["id"])]
    assert _shown(client) == []
    risk = client.get("/dashboard", params=DEC_31).json()["risk"]
    assert (risk["status"], risk["min_balance_eur"]) == ("GREEN", 3581.16)

    assert client.delete(f"/events/{events[0].id}").status_code == 204  # withdraws the tax reserve
    assert _shown(client) == ["T1"]
    history = client.get("/decisions").json()["decisions"]
    assert [(decision["option_id"], decision["withdrawn_at"] is None) for decision in history] == [
        ("I1", True), ("T2", True), ("T1", False)]

    assert client.post("/demo/reset").status_code == 204
    assert _count(connection, "owner_decisions") == 0 and _count(connection, "planned_events") == 0
    assert _shown(client) == ["T1", "T2", "I1"]


def test_conflict_stores_nothing(client, connection):
    response = _decide(client, ["T1", "F1"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "proposal_not_available"
    assert _count(connection, "owner_decisions") == 0 and _count(connection, "planned_events") == 0


def test_rejection_is_remembered(client, connection):
    assert _decide(client, ["I1"], status="rejected").status_code == 201
    assert _shown(client) == ["T1", "T2"]
    assert _decide(client, ["I1"]).status_code == 409
    assert _count(connection, "owner_decisions") == 1 and _count(connection, "planned_events") == 0


def test_simultaneous_approvals_of_the_same_proposals_store_them_once(schema):  # noqa: F811
    """Two tabs approving the same deposits at the same moment: the decision lock lets exactly one through."""
    connections = [_connect(schema) for _ in range(2)]
    try:
        barrier = threading.Barrier(2)

        def approve(connection) -> int:
            client = _client(connection)
            barrier.wait()
            return _decide(client, ["T1", "I1"]).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(approve, connections))
        assert statuses == [201, 409]
        assert _count(connections[0], "planned_events") == 2 and _count(connections[0], "owner_decisions") == 2
    finally:
        _clear(connections[0])
        for connection in connections:
            connection.close()
