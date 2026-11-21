"""REST contract tests with FastAPI's TestClient and the CSV-backed in-memory repository (no live database)."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import service
from app import create_app
from deps import get_repository
from domain import DashboardQuery
from fakes import closure
from kpi_engine.repository import RepositoryError

PARAMS = {"as_of_date": "2025-05-10", "horizon_days": 60, "buffer_eur": 3000}


@pytest.fixture()
def client(repo):
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: repo
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture()
def closure_event(repo):
    return repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))


def test_health(client):
    body = client.get("/health").json()
    assert body["service"] == "autonomous-cfo-backend" and body["status"] == "ok" and body["time"].endswith("Z")


def test_dashboard_defaults_and_shape(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"as_of_date", "horizon_days", "buffer_eur", "data_range", "current_balance_eur", "history",
                         "forecast", "risk", "risk_drivers", "headline", "events", "options",
                         "code_recommended_option_id", "insights"}
    assert (body["as_of_date"], body["horizon_days"], body["buffer_eur"]) == ("2025-05-10", 60, 3000.0)
    assert body["data_range"] == {"first_date": "2025-01-01", "last_date": "2025-12-31"}
    assert body["current_balance_eur"] == 3205.69
    assert len(body["history"]) == 90 and body["history"][-1] == {"date": "2025-05-10", "balance_eur": 3205.69}
    assert len(body["forecast"]["days"]) == 60
    assert body["forecast"]["daily_averages"] == {"revenue_eur": 1474.87, "variable_costs_eur": 741.4,
                                                  "lookback_days": 28, "open_days": 28}
    day = body["forecast"]["days"][0]
    assert set(day) == {"date", "balance_eur", "inflow_eur", "outflow_eur", "items"}
    assert set(day["items"][0]) == {"label", "category", "kind", "amount_eur"}
    assert day["items"][1]["amount_eur"] < 0 and day["items"][1]["kind"] == "variable_cost"


def test_dashboard_with_closure_goes_red_with_options(client, closure_event):
    body = client.get("/dashboard", params=PARAMS).json()
    risk = body["risk"]
    assert risk == {"status": "RED", "min_balance_eur": -15728.42, "min_balance_date": "2025-07-01",
                    "floor_eur": 0.0, "credit_limit_eur": None, "days_until_min": 52,
                    "risk_window": {"start": "2025-05-15", "end": "2025-07-01"}}
    assert body["headline"] == "In about 7 weeks you will be about €16,000 short."
    assert [event["id"] for event in body["events"]] == [closure_event.id]
    assert body["events"][0]["event_type"] == "closure" and body["events"][0]["source"] == "chat"
    assert [option["id"] for option in body["options"]] == ["A", "B", "C", "E"]
    option = body["options"][0]
    assert set(option) == {"id", "title", "description", "new_min_balance_eur", "new_min_balance_date", "new_status",
                           "cost_eur", "cost_note", "non_financial_cost", "requires_approval", "credit_limit_eur",
                           "simulated_actions", "forecast", "rejected"}
    assert len(option["forecast"]) == 60 and set(option["forecast"][0]) == {"date", "balance_eur"}
    assert body["code_recommended_option_id"] == "E"
    assert body["risk_drivers"][0] == {"category": "net_payroll", "label": "Staff salaries", "total_eur": 10800.0,
                                       "deferrable": False}
    assert body["risk_drivers"][1]["deferrable"] is True
    assert body["insights"] == []


def test_dashboard_surplus_insight_in_december(client):
    body = client.get("/dashboard", params={"as_of_date": "2025-12-31"}).json()
    assert body["risk"]["status"] == "GREEN" and body["risk"]["risk_window"] is None
    assert body["options"] == [] and body["risk_drivers"] == []
    [insight] = body["insights"]
    assert insight["kind"] == "surplus" and insight["amount_eur"] == 27000.0 and "€33,266" in insight["detail"]


def test_surplus_insight_is_kept_when_an_unused_credit_line_exists(client, closure_event):
    assert client.post("/decisions", json={**PARAMS, "option_id": "E", "status": "approved"}).status_code == 201
    covered_by_credit = client.get("/dashboard", params=PARAMS).json()
    assert covered_by_credit["risk"]["status"] == "GREEN" and covered_by_credit["insights"] == []
    december = client.get("/dashboard", params={"as_of_date": "2025-12-31"}).json()
    assert december["risk"]["credit_limit_eur"] == 15000.0
    assert [insight["amount_eur"] for insight in december["insights"]] == [27000.0]


@pytest.mark.parametrize("params, code", [
    ({"horizon_days": 45}, "validation_error"),
    ({"buffer_eur": -1}, "validation_error"),
    ({"as_of_date": "10.05.2025"}, "validation_error"),
    ({"as_of_date": "2026-03-01"}, "as_of_date_out_of_range"),
    ({"as_of_date": "2024-12-31"}, "as_of_date_out_of_range"),
])
def test_dashboard_rejects_invalid_parameters(client, params, code):
    response = client.get("/dashboard", params=params)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == code and error["message"]


def test_events_list_and_delete(client, closure_event):
    events = client.get("/events").json()["events"]
    assert [event["id"] for event in events] == [closure_event.id]
    assert set(events[0]) == {"id", "event_type", "date_from", "date_to", "amount_eur", "category", "description",
                              "original_utterance", "source", "as_of_date", "decision_id", "created_at"}
    assert client.delete(f"/events/{closure_event.id}").status_code == 204
    assert client.get("/events").json() == {"events": []}
    missing = client.delete(f"/events/{closure_event.id}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_approving_option_e_turns_the_light_green(client, repo, closure_event):
    response = client.post("/decisions", json={**PARAMS, "option_id": "E", "status": "approved",
                                               "llm_explanation": "Because it is cheapest."})
    assert response.status_code == 201
    body = response.json()
    decision = body["decision"]
    assert body["execution_messages"] == decision["execution_messages"] == [
        "Credit line request for €15,000 sent to Commerzbank (simulated).",
        "Transfer to Zurich (Luigi): payment of 15 May postponed to 15 July (simulated).",
        "Transfer to Zurich (Luigi): payment of 15 June postponed to 15 August (simulated).",
    ]
    assert (decision["status"], decision["execution"], decision["option_id"]) == ("approved", "SIMULATED", "E")
    assert decision["llm_explanation"] == "Because it is cheapest."
    assert decision["snapshot"] == {"status_before": "RED", "min_balance_before_eur": -15728.42,
                                    "min_balance_date_before": "2025-07-01", "status_after": "GREEN",
                                    "min_balance_after_eur": -7061.74}
    agent_events = [event for event in repo.events if event.source == "agent"]
    assert {event.event_type for event in agent_events} == {"payment_deferral", "credit_line"}
    assert all(event.decision_id == decision["id"] for event in agent_events)

    after = client.get("/dashboard", params=PARAMS).json()
    assert after["risk"]["status"] == "GREEN" and after["risk"]["credit_limit_eur"] == 15000.0
    assert after["risk"]["min_balance_eur"] == -7061.74 and after["options"] == []
    assert len(after["events"]) == 3
    assert client.get("/decisions").json()["decisions"][0]["id"] == decision["id"]


def test_approving_option_c_updates_the_existing_closure(client, repo, closure_event):
    response = client.post("/decisions", json={**PARAMS, "option_id": "C", "status": "approved"})
    assert response.status_code == 201
    [event] = repo.events
    assert (event.id, event.date_from, event.date_to) == (closure_event.id, date(2025, 6, 1), date(2025, 6, 7))
    assert response.json()["decision"]["snapshot"]["min_balance_after_eur"] == \
        client.get("/dashboard", params=PARAMS).json()["risk"]["min_balance_eur"]


def test_rejecting_an_option_changes_nothing_but_flags_it(client, repo, closure_event):
    response = client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "rejected"})
    assert response.status_code == 201
    assert response.json()["execution_messages"] == []
    assert len(repo.events) == 1
    options = {option["id"]: option for option in client.get("/dashboard", params=PARAMS).json()["options"]}
    assert options["A"]["rejected"] is True and options["E"]["rejected"] is False


def test_decision_for_an_option_that_is_not_offered_is_a_conflict(client, closure_event):
    response = client.post("/decisions", json={**PARAMS, "option_id": "D", "status": "approved"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "option_not_available"
    green = client.post("/decisions", json={"as_of_date": "2025-12-31", "option_id": "A", "status": "approved"})
    assert green.status_code == 409


def test_decision_ignores_client_supplied_numbers(client, closure_event):
    response = client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "approved",
                                               "new_min_balance_eur": 99999})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    bad_status = client.post("/decisions", json={**PARAMS, "option_id": "A", "status": "maybe"})
    assert bad_status.status_code == 422


def test_demo_reset_clears_events_and_decisions(client, repo, closure_event):
    client.post("/decisions", json={**PARAMS, "option_id": "E", "status": "approved"})
    assert client.post("/demo/reset").status_code == 204
    assert client.get("/events").json() == {"events": []}
    assert client.get("/decisions").json() == {"decisions": []}


def test_database_down_is_503(repo):
    def broken_repository():
        raise RepositoryError("Cannot connect to PostgreSQL: connection refused")

    application = create_app()
    application.dependency_overrides[get_repository] = broken_repository
    response = TestClient(application, raise_server_exceptions=False).get("/dashboard")
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"


def test_unknown_route_and_unexpected_errors_use_the_error_shape(client, repo, monkeypatch):
    assert client.get("/nope").json() == {"error": {"code": "not_found", "message": "Not Found"}}
    monkeypatch.setattr(repo, "list_events", lambda: 1 / 0)
    response = client.get("/events")
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"


def test_dossier_and_fallback_recommendation(repo, closure_event):
    dashboard = service.build_dashboard(repo, DashboardQuery(date(2025, 5, 10), 60, Decimal("3000")))
    dossier = service.build_dossier(dashboard)
    assert list(dossier) == ["today", "balance_today", "horizon_days", "buffer_eur", "status", "min_balance",
                             "min_balance_date", "credit_limit_eur", "events", "daily_averages",
                             "fixed_costs_in_risk_window", "weekly_balance", "options", "code_recommendation"]
    assert (dossier["today"], dossier["balance_today"], dossier["status"]) == ("2025-05-10", 3205.69, "RED")
    assert dossier["events"] == [{"type": "closure", "from": "2025-06-01", "to": "2025-06-21", "amount_eur": None,
                                  "description": "Summer holiday"}]
    assert dossier["weekly_balance"][0] == 3206 and len(dossier["weekly_balance"]) == 9
    assert all("forecast" not in option for option in dossier["options"])
    assert dossier["code_recommendation"] == "E"

    fallback = service.build_fallback_recommendation(dashboard)
    assert fallback.headline == dashboard.headline and fallback.recommended_option_id == "E"
    assert set(fallback.option_comments) == {"A", "B", "C", "E"}
    assert "closed (1 June – 21 June)" in fallback.explanation and "Option E" in fallback.rationale
