"""POST /chat: what the backend derives from the model's MCP calls, the save gate, threads and failures."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import create_app
from deps import get_repository
from domain import DashboardQuery, EventType
from errors import AssistantUnavailableError
from fakes import closure
from llm import LlmContextOverflowError, LlmHttpError, LlmTimeoutError
from llm_chat import ChatService, pending_drafts, saved_events, tool_call_ok
from llm_fakes import (
    FakeLlmClient,
    mcp_call,
    propose_ok,
    propose_rejected,
    response,
    save_duplicate,
    save_ok,
)
from llm_prompts import UPDATE_COMMENT_INSTRUCTIONS
from llm_routes import get_llm_client

MCP_URL = "http://amalfi-mcp.test/mcp"
MAY_10 = DashboardQuery(date(2025, 5, 10), 60, Decimal("3000"))
OVEN = {"event_type": "expense", "date_from": "2025-07-15", "date_to": "2025-07-15", "amount_eur": 8000.0,
        "description": "New oven"}
SCHEMA_ERROR = mcp_call("save_planned_event", None, error="Error (code 1): None")


def handle(repo, client: FakeLlmClient, message: str = "Hello", conversation_id=None):
    return ChatService(client, MCP_URL).handle(repo, MAY_10, message, conversation_id)


# --- derivation from MCP calls -----------------------------------------------------------------------------

def test_tool_call_ok_needs_no_mcp_error_and_no_rejection():
    assert tool_call_ok(propose_ok()) and tool_call_ok(save_ok()) and tool_call_ok(save_duplicate())
    assert tool_call_ok(mcp_call("list_planned_events", {"analysis_date": "2025-05-10", "events": []}))
    assert not tool_call_ok(propose_rejected())
    assert not tool_call_ok(SCHEMA_ERROR)
    assert not tool_call_ok(mcp_call("save_planned_event", {"saved": False, "error": "amount_eur is required"}))


def test_a_valid_proposal_is_pending():
    (draft,) = pending_drafts([propose_rejected(), propose_ok()])
    assert (draft.event_type, draft.date_from, draft.date_to, draft.amount_eur, draft.description) == (
        EventType.CLOSURE, date(2025, 6, 1), date(2025, 6, 21), None, "Holidays")


def test_rejected_and_failed_proposals_are_not_pending():
    failed = mcp_call("propose_planned_event", None, error="Error (code 1): None")
    assert pending_drafts([propose_rejected(), failed]) == ()


def test_a_proposal_saved_later_in_the_same_turn_is_not_pending():
    assert pending_drafts([propose_ok(), save_ok()]) == ()
    assert pending_drafts([propose_ok(), save_duplicate()]) == ()
    assert pending_drafts([propose_ok(description="Holidays"), save_ok(description="Summer holidays")]) == ()


def test_a_proposal_made_after_a_save_or_for_another_plan_stays_pending():
    assert len(pending_drafts([save_ok(), propose_ok()])) == 1
    (draft,) = pending_drafts([propose_ok(), propose_ok(**OVEN), save_ok()])
    assert (draft.event_type, draft.amount_eur) == (EventType.EXPENSE, Decimal("8000"))


def test_repeated_identical_proposals_are_reported_once():
    assert len(pending_drafts([propose_ok(), propose_ok(), propose_ok(**OVEN)])) == 2


def test_a_proposal_the_backend_cannot_read_is_dropped():
    broken = mcp_call("propose_planned_event", {"valid": True, "proposal": {"event_type": "credit_line"}})
    assert pending_drafts([broken]) == ()


def test_saved_events_come_only_from_successful_saves():
    calls = [propose_ok(), save_ok(7), save_duplicate(8), SCHEMA_ERROR,
             mcp_call("save_planned_event", {"saved": False, "error": "date_from is before today"}),
             save_ok(9, **OVEN)]
    events = saved_events(calls)
    assert [(event.id, event.event_type, event.source) for event in events] == [
        (7, EventType.CLOSURE, "chat"), (9, EventType.EXPENSE, "chat")]
    assert events[0].original_utterance == "We close 1-21 June." and events[1].amount_eur == Decimal("8000")


# --- one turn ----------------------------------------------------------------------------------------------

def test_first_turn_offers_propose_but_not_save(repo):
    client = FakeLlmClient(response("Should I include it in the forecast?", propose_ok(), response_id="resp_a"))
    turn = handle(repo, client, "We close 1 to 21 June.")
    request = client.requests[0]
    assert request.input == "We close 1 to 21 June." and request.previous_response_id is None
    assert request.mcp.server_url == MCP_URL and request.mcp.as_of_date == date(2025, 5, 10)
    assert "propose_planned_event" in request.mcp.allowed_tools
    assert "save_planned_event" not in request.mcp.allowed_tools
    assert "Today is Saturday, 2025-05-10" in request.instructions
    assert '"balance_today": 3205.69' in request.instructions
    assert (turn.conversation_id, turn.reply) == ("resp_a", "Should I include it in the forecast?")
    assert len(turn.pending_events) == 1 and turn.saved_events == () and turn.events_changed is False
    assert [(call.name, call.ok) for call in turn.tool_calls] == [("propose_planned_event", True)]


def test_save_is_offered_only_after_a_turn_that_ended_with_a_proposal(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Which option do you mean?", response_id="resp_b"),
        response("Still nothing to save.", response_id="resp_c"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    handle(repo, client, "Why is the balance dropping?", "resp_a")
    handle(repo, client, "Yes", "resp_b")
    offered = ["save_planned_event" in request.mcp.allowed_tools for request in client.requests]
    assert offered == [False, True, False]
    assert [request.previous_response_id for request in client.requests] == [None, "resp_a", "resp_b"]


def test_confirmed_plan_is_reported_and_the_model_comments_on_the_new_forecast(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Saved.", save_ok(7), response_id="resp_b"),
        response("Done. The closure is included; you would be short of money on 1 July.", response_id="resp_c"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))  # what the MCP server did during the save call
    turn = handle(repo, client, "Yes, include it.", "resp_a")
    comment_request = client.requests[2]
    assert comment_request.instructions == UPDATE_COMMENT_INSTRUCTIONS
    assert comment_request.previous_response_id == "resp_b" and comment_request.mcp is None
    assert "status: RED" in comment_request.input and "below zero on 1 July" in comment_request.input
    assert turn.conversation_id == "resp_c" and turn.reply.startswith("Done.")
    assert [event.id for event in turn.saved_events] == [7] and turn.events_changed is True
    assert turn.pending_events == ()
    assert [(call.name, call.ok) for call in turn.tool_calls] == [("save_planned_event", True)]


def test_a_failed_forecast_comment_does_not_fail_the_turn(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Saved.", save_ok(7), response_id="resp_b"),
        LlmTimeoutError("slow"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    turn = handle(repo, client, "Yes", "resp_a")
    assert (turn.conversation_id, turn.reply, turn.events_changed) == ("resp_b", "Saved.", True)


def test_a_duplicate_save_changes_nothing(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("That plan is already included.", save_duplicate(7), response_id="resp_b"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    turn = handle(repo, client, "Yes", "resp_a")
    assert turn.saved_events == () and turn.events_changed is False and len(client.requests) == 2
    assert [(call.name, call.ok) for call in turn.tool_calls] == [("save_planned_event", True)]


# --- threads -----------------------------------------------------------------------------------------------

def test_unknown_conversation_id_starts_a_new_thread(repo):
    client = FakeLlmClient(response("Hello! How can I help?", response_id="resp_new"))
    turn = handle(repo, client, "Yes", "resp_expired")
    request = client.requests[0]
    assert request.previous_response_id is None and "save_planned_event" not in request.mcp.allowed_tools
    assert turn.conversation_id == "resp_new"


def test_context_overflow_continues_in_a_new_thread_that_carries_the_pending_proposal(repo):
    client = FakeLlmClient(
        response("Should I include the closure?", propose_ok(), response_id="resp_a"),
        LlmContextOverflowError("maximum context length"),
        response("Saved.", save_ok(7), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    turn = handle(repo, client, "Yes, include it.", "resp_a")
    overflowing, restarted = client.requests[1], client.requests[2]
    assert overflowing.previous_response_id == "resp_a" and restarted.previous_response_id is None
    assert "save_planned_event" in restarted.mcp.allowed_tools
    assert "Should I include the closure?" in restarted.input
    assert '"date_from": "2025-06-01"' in restarted.input and restarted.input.endswith("Yes, include it.")
    assert turn.events_changed is True and turn.conversation_id == "resp_c"


def test_context_overflow_on_a_first_message_is_a_failure(repo):
    with pytest.raises(AssistantUnavailableError):
        handle(repo, FakeLlmClient(LlmContextOverflowError("maximum context length")), "x" * 2000)


# --- failures ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("step", [LlmTimeoutError("slow"), LlmHttpError("HTTP 500"), response("")])
def test_model_failure_or_empty_answer_is_assistant_unavailable(repo, step):
    with pytest.raises(AssistantUnavailableError):
        handle(repo, FakeLlmClient(step))


# --- HTTP contract -----------------------------------------------------------------------------------------

def api_client(repo, llm: FakeLlmClient) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_llm_client] = lambda: llm
    return TestClient(application, raise_server_exceptions=False)


def test_chat_endpoint_returns_the_contract_shape(repo):
    llm = FakeLlmClient(response("Should I include it in the forecast?", propose_rejected(), propose_ok(**OVEN),
                                 response_id="resp_a"))
    reply = api_client(repo, llm).post("/chat", json={"message": "  I buy an oven on 15 July for 8k.  "})
    assert reply.status_code == 200
    assert llm.requests[0].input == "I buy an oven on 15 July for 8k."
    assert reply.json() == {
        "conversation_id": "resp_a",
        "reply": "Should I include it in the forecast?",
        "pending_events": [OVEN],
        "saved_events": [],
        "tool_calls": [{"name": "propose_planned_event", "ok": False}, {"name": "propose_planned_event", "ok": True}],
        "events_changed": False,
    }


def test_chat_endpoint_reports_saved_events_in_the_event_shape(repo):
    llm = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Saved.", save_ok(7), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    http = api_client(repo, llm)
    first = http.post("/chat", json={"message": "We close 1 to 21 June."}).json()
    body = http.post("/chat", json={"message": "Yes", "conversation_id": first["conversation_id"]}).json()
    assert (body["conversation_id"], body["reply"], body["events_changed"]) == ("resp_c", "Done.", True)
    assert body["saved_events"] == [{
        "id": 7, "event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21", "amount_eur": None,
        "category": None, "description": "Holidays", "original_utterance": "We close 1-21 June.", "source": "chat",
        "as_of_date": "2025-05-10", "decision_id": None, "created_at": "2026-11-21T10:27:53.432984Z",
    }]


def test_chat_endpoint_answers_503_when_the_model_is_down(repo):
    reply = api_client(repo, FakeLlmClient(LlmTimeoutError("slow"))).post("/chat", json={"message": "Hello"})
    assert reply.status_code == 503
    assert reply.json()["error"]["code"] == "assistant_unavailable" and reply.json()["error"]["message"]


@pytest.mark.parametrize("payload", [{}, {"message": "   "}, {"message": "x" * 2001},
                                     {"message": "Hi", "horizon_days": 45}, {"message": "Hi", "unknown": 1}])
def test_chat_endpoint_validates_the_request(repo, payload):
    reply = api_client(repo, FakeLlmClient()).post("/chat", json=payload)
    assert (reply.status_code, reply.json()["error"]["code"]) == (422, "validation_error")
