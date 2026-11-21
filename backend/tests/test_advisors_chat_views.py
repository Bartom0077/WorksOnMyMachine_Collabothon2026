"""What the language model and the REST layer see of the CFO advisors' events and decisions.

Term deposits and advance currency purchases are created by the backend (source ``agent``) when the owner
approves an advisor proposal. The chat and the recommendation prompt must pass them on with their description
and stored figures only: any effect on the balance comes from the forecast, never from these views.
"""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import service
from app import create_app
from deps import get_repository
from domain import DashboardQuery, DecisionStatus, EventDraft, EventSource, EventType, NewDecision
from fakes import expense
from llm_chat import ChatService, PendingPlansStore
from llm_fakes import FakeLlmClient, mcp_call, propose_removal_rejected, response
from llm_prompts import chat_instructions, recommendation_dossier, situation_json
from repository import FINGERPRINT_KEY
from schemas import DecisionOut, EventOut

MCP_URL = "http://amalfi-mcp.test/mcp"
OCTOBER_15 = DashboardQuery(date(2025, 10, 15), 60, Decimal("3000"))
PARAMS = {"as_of_date": "2025-10-15", "horizon_days": 60, "buffer_eur": 3000}

TAX_RESERVE = EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=date(2025, 10, 16), date_to=date(2025, 11, 16),
                         amount_eur=Decimal("9000.00"), category="tax_reserve", description="Tax reserve deposit")
INVESTMENT = EventDraft(event_type=EventType.TERM_DEPOSIT, date_from=date(2025, 10, 16), date_to=date(2026, 4, 16),
                        amount_eur=Decimal("2000.00"), category="investment", description="Investment deposit")
FX_PREBUY = EventDraft(event_type=EventType.FX_PREBUY, date_from=date(2025, 10, 16), date_to=date(2025, 12, 15),
                       amount_eur=Decimal("8716.67"), description="CHF bought for the remaining Zurich instalments")
ADVISOR_EVENTS = (TAX_RESERVE, INVESTMENT, FX_PREBUY)

# (proposal id, title, description, events created on approval) in the order the owner decided them.
ADVISOR_DECISIONS = (
    ("T1", "Tax reserve deposit", "Set €9,000 aside for income tax in a one-month deposit.", (TAX_RESERVE,)),
    ("T2", "Raise the tax prepayments", "Ask your tax advisor to raise the quarterly prepayments.", ()),
    ("F1", "Buy the Swiss francs now", "Buy the francs for the remaining Zurich instalments today.", (FX_PREBUY,)),
    ("F2", "Lock the exchange rate", "Agree a forward contract for the remaining Zurich instalments.", ()),
    ("I1", "Investment deposit", "Put €2,000 of spare cash into a six-month deposit.", (INVESTMENT,)),
)


@pytest.fixture
def advised(repo):
    """The owner approved every advisor proposal on 15 October; the deposits and the purchase are saved plans."""
    repo.record_decisions([(_decision(*decided[:3]), decided[3]) for decided in ADVISOR_DECISIONS])
    return repo


def _decision(proposal_id: str, title: str, description: str) -> NewDecision:
    snapshot = {FINGERPRINT_KEY: f"fingerprint-{proposal_id}", "status_before": "GREEN",
                "min_balance_before_eur": 20000.0, "min_balance_date_before": "2025-10-16", "status_after": "GREEN",
                "min_balance_after_eur": 4500.0, "credit_limit_after_eur": None, "cost_eur": 0.0}
    return NewDecision(as_of_date=OCTOBER_15.as_of_date, horizon_days=OCTOBER_15.horizon_days,
                       buffer_eur=OCTOBER_15.buffer_eur, option_id=proposal_id, option_title=title,
                       option_description=description, status=DecisionStatus.APPROVED, llm_explanation=None,
                       execution_messages=(f"{title} (simulated).",), forecast_snapshot=snapshot)


def _stored_view(event: EventDraft) -> dict:
    return {"type": event.event_type.value, "from": event.date_from.isoformat(), "to": event.date_to.isoformat(),
            "amount_eur": float(event.amount_eur), "description": event.description}


def _situation(instructions: str) -> dict:
    return json.loads(instructions.split("SITUATION (JSON):\n")[1])


def _chat(repository, client: FakeLlmClient, message: str):
    return ChatService(client, MCP_URL, PendingPlansStore()).handle(repository, OCTOBER_15, message, None)


# --- the forecast and the dossier ---------------------------------------------------------------------------

def test_the_forecast_runs_with_the_advisor_events_and_books_them_under_their_own_type(advised):
    dashboard = service.build_dashboard(advised, OCTOBER_15)
    assert [event.event_type for event in dashboard.events] == [
        EventType.TERM_DEPOSIT, EventType.FX_PREBUY, EventType.TERM_DEPOSIT]
    booked = {item.category for day in dashboard.forecast.days for item in day.items}
    assert {"term_deposit", "fx_prebuy"} <= booked and "investment_outflow" not in booked


def test_the_dossier_describes_an_advisor_event_by_its_stored_fields_only(advised):
    dossier = recommendation_dossier(service.build_dashboard(advised, OCTOBER_15))
    assert sorted(dossier["planned_events"], key=lambda event: event["description"]) == sorted(
        (_stored_view(event) for event in ADVISOR_EVENTS), key=lambda event: event["description"])
    json.dumps(dossier)  # the whole document is JSON-ready


def test_the_dossier_takes_its_balances_from_the_forecast_that_includes_the_events(advised):
    """Nothing is added on top: the lowest balance in the dossier is the engine's, with the events applied."""
    dashboard = service.build_dashboard(advised, OCTOBER_15)
    dossier = recommendation_dossier(dashboard)
    assert dossier["lowest_balance"] == float(min(day.balance_eur for day in dashboard.forecast.days))
    assert dossier["status"] == dashboard.risk.status.value


# --- the chat's SITUATION -----------------------------------------------------------------------------------

def test_the_situation_lists_the_advisor_events_with_description_and_no_computed_effect(advised):
    situation = json.loads(situation_json(service.build_dashboard(advised, OCTOBER_15), ()))
    assert {event["description"]: set(event) for event in situation["planned_events"]} == {
        event.description: {"type", "from", "to", "amount_eur", "description"} for event in ADVISOR_EVENTS}


def test_the_removal_view_marks_every_advisor_event_as_an_agent_plan(advised):
    dashboard = service.build_dashboard(advised, OCTOBER_15)
    situation = json.loads(situation_json(dashboard, (), advised.list_events()))
    assert situation["planned_events"] == [
        {"id": event.id, "source": "agent", **_stored_view(event)} for event in advised.list_events()]
    assert [event["type"] for event in situation["planned_events"]] == ["term_deposit", "fx_prebuy", "term_deposit"]


def test_recent_decisions_name_every_advisor_proposal_by_id_title_and_description(advised):
    situation = json.loads(situation_json(service.build_dashboard(advised, OCTOBER_15), advised.list_decisions()))
    assert situation["recent_decisions"] == [
        {"option": proposal_id, "title": title, "decision": "approved", "decided_for_date": "2025-10-15",
         "what_it_does": description, "interest_cost_eur": 0.0}
        for proposal_id, title, description, _ in reversed(ADVISOR_DECISIONS)]


def test_the_chat_prompt_is_built_with_advisor_events_and_decisions(advised):
    instructions = chat_instructions(service.build_dashboard(advised, OCTOBER_15), (), None,
                                     advised.list_decisions()[:3])
    situation = _situation(instructions)
    assert [decision["option"] for decision in situation["recent_decisions"]] == ["I1", "F2", "F1"]
    assert {event["type"] for event in situation["planned_events"]} == {"term_deposit", "fx_prebuy"}


# --- chat turns ---------------------------------------------------------------------------------------------

def test_a_chat_turn_works_while_advisor_events_are_saved(advised):
    client = FakeLlmClient(response("You set €9,000 aside for income tax.", response_id="resp_a"))
    turn = _chat(advised, client, "What did I just approve?")
    request = client.requests[0]
    assert turn.reply == "You set €9,000 aside for income tax." and turn.events_changed is False
    assert [decision["option"] for decision in _situation(request.instructions)["recent_decisions"]] == [
        "I1", "F2", "F1"]


def test_advisor_events_alone_never_bring_the_removal_job(advised):
    """They are agent plans: with no plan from the chat there is nothing the chat could remove."""
    client = FakeLlmClient(response("That deposit cannot be removed here."))
    _chat(advised, client, "Remove the tax reserve deposit.")
    request = client.requests[0]
    assert "exactly two jobs" in request.instructions and '"source":' not in request.instructions
    assert "remove_planned_event" not in request.mcp.allowed_tools and request.mcp.removable_event_ids == ()


def test_a_refused_removal_of_an_advisor_event_changes_nothing(advised):
    advised.add_event(expense(date(2025, 11, 5), "500", "New mixer"), as_of=OCTOBER_15.as_of_date)
    deposit = advised.list_events()[0]
    listed = mcp_call("list_planned_events", {"analysis_date": "2025-10-15", "events": []})
    refused = propose_removal_rejected(
        f"Planned event {deposit.id} (Tax reserve deposit) belongs to a CFO recommendation the owner approved, so "
        "it cannot be removed in the chat.")
    client = FakeLlmClient(response('Use "Withdraw option" in "My plans".', listed, refused, response_id="resp_a"))
    turn = _chat(advised, client, "Remove the tax reserve deposit.")
    plans = _situation(client.requests[0].instructions)["planned_events"]
    assert {"id": deposit.id, "source": "agent", **_stored_view(TAX_RESERVE)} in plans
    assert turn.pending_removals == () and turn.removed_events == () and turn.events_changed is False
    assert len(advised.list_events()) == len(ADVISOR_EVENTS) + 1


# --- response models ----------------------------------------------------------------------------------------

def test_the_response_models_accept_advisor_events_and_decisions(advised):
    events = [EventOut.model_validate(event) for event in advised.list_events()]
    assert [(event.event_type, event.category, event.source) for event in events] == [
        (EventType.TERM_DEPOSIT, "tax_reserve", EventSource.AGENT), (EventType.FX_PREBUY, None, EventSource.AGENT),
        (EventType.TERM_DEPOSIT, "investment", EventSource.AGENT)]
    decisions = [DecisionOut.model_validate(decision) for decision in advised.list_decisions()]
    assert [decision.option_id for decision in decisions] == ["I1", "F2", "F1", "T2", "T1"]


def test_the_rest_api_serves_advisor_events_and_decisions(advised):
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: advised
    client = TestClient(application, raise_server_exceptions=False)
    dashboard = client.get("/dashboard", params=PARAMS)
    assert dashboard.status_code == 200
    assert sorted(event["event_type"] for event in dashboard.json()["events"]) == [
        "fx_prebuy", "term_deposit", "term_deposit"]
    assert [event["category"] for event in client.get("/events").json()["events"]] == [
        "tax_reserve", None, "investment"]
    assert [decision["option_id"] for decision in client.get("/decisions").json()["decisions"]] == [
        "I1", "F2", "F1", "T2", "T1"]
