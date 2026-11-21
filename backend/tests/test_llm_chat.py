"""POST /chat: what the backend derives from the model's MCP calls, the save gate, threads and failures."""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import create_app
from deps import get_repository
import service
from domain import DashboardQuery, DecisionStatus, EventType
from errors import AssistantUnavailableError
from fakes import closure, expense
from llm import LlmContextOverflowError, LlmHttpError, LlmTimeoutError
import llm_chat
from llm_chat import ChatService, PendingPlansStore, pending_drafts, saved_events, tool_call_ok
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
from llm_routes import get_llm_client, get_pending_plans_store

MCP_URL = "http://amalfi-mcp.test/mcp"
MAY_10 = DashboardQuery(date(2025, 5, 10), 60, Decimal("3000"))
OVEN = {"event_type": "expense", "date_from": "2025-07-15", "date_to": "2025-07-15", "amount_eur": 8000.0,
        "description": "New oven"}
SCHEMA_ERROR = mcp_call("save_planned_event", None, error="Error (code 1): None")


def handle(repo, client: FakeLlmClient, message: str = "Hello", conversation_id=None):
    """One chat turn; turns made with the same fake client share one store of pending plans."""
    store = client.__dict__.setdefault("pending_store", PendingPlansStore())
    return ChatService(client, MCP_URL, store).handle(repo, MAY_10, message, conversation_id)


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


def test_save_is_offered_while_a_proposal_waits_and_survives_one_side_question(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Your rent is 3,800 a month.", response_id="resp_b"),
        response("Which option do you mean?", response_id="resp_c"),
        response("Nothing to save.", response_id="resp_d"),
    )
    first = handle(repo, client, "We close 1 to 21 June.")
    side_question = handle(repo, client, "How much is my rent?", "resp_a")
    handle(repo, client, "And which option is best?", "resp_b")
    handle(repo, client, "Yes", "resp_c")
    offered = ["save_planned_event" in request.mcp.allowed_tools for request in client.requests]
    assert offered == [False, True, True, False]   # dropped after two turns without a proposal or a save
    assert [request.previous_response_id for request in client.requests] == [None, "resp_a", "resp_b", "resp_c"]
    assert '"date_from": "2025-06-01"' in client.requests[1].instructions   # the model is told what is waiting
    assert "NOT saved yet" in client.requests[1].instructions
    assert len(first.pending_events) == 1 and side_question.pending_events == ()


def test_partial_yes_keeps_the_other_proposal_waiting_across_the_forecast_comment(repo):
    """Finding: after "yes to the flyers only" the refund draft was lost and a later "yes" saved nothing."""
    flyers = {"event_type": "expense", "date_from": "2025-07-19", "date_to": "2025-07-19", "amount_eur": 300.0,
              "description": "Flyers"}
    refund = {"event_type": "inflow", "date_from": "2025-07-21", "date_to": "2025-07-21", "amount_eur": 450.0,
              "description": "Insurance refund"}
    client = FakeLlmClient(
        response("Should I include both?", propose_ok(**flyers), propose_ok(**refund), response_id="resp_a"),
        response("Saved the flyers.", save_ok(7, **flyers), response_id="resp_b"),
        response("Done. The flyers are included.", response_id="resp_c"),
        response("Saved the refund.", save_ok(8, **refund), response_id="resp_d"),
        response("Done. The refund is included.", response_id="resp_e"),
    )
    handle(repo, client, "On 19 July I pay 300 for flyers and on 21 July I get 450 back from the insurer.")
    partial = handle(repo, client, "Yes to the flyers only.", "resp_a")
    assert [event.id for event in partial.saved_events] == [7] and partial.conversation_id == "resp_c"
    assert [(draft.event_type, draft.amount_eur) for draft in partial.pending_events] == [
        (EventType.INFLOW, Decimal("450"))]
    rest = handle(repo, client, "OK, now yes to the refund too.", "resp_c")
    assert "save_planned_event" in client.requests[3].mcp.allowed_tools
    assert [event.id for event in rest.saved_events] == [8] and rest.pending_events == ()


@pytest.mark.parametrize("message, saved_values", [
    ("Yes, but make it 9700 euros and save it right away.", {**OVEN, "amount_eur": 9700.0}),
    ("Make it 16 July instead.", {**OVEN, "date_from": "2025-07-16", "date_to": "2025-07-16"}),
])
def test_a_save_that_differs_from_the_confirmed_proposal_is_removed_and_shown_for_confirmation(
        repo, message, saved_values):
    """Findings: "yes, but make it 9700" and the "Correct it" flow stored values the owner never saw as a proposal."""
    client = FakeLlmClient(
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"),
        response("It has been saved.", save_ok(7, **saved_values), response_id="resp_b"),
        response("Saved.", save_ok(8, **saved_values), response_id="resp_c"),
        response("Done.", response_id="resp_d"),
    )
    handle(repo, client, "I buy an oven on 15 July for 8k.")
    turn = handle(repo, client, message, "resp_a")
    assert turn.saved_events == () and turn.events_changed is False
    assert len(client.requests) == 2                        # no forecast comment: nothing was saved
    (draft,) = turn.pending_events
    assert (draft.date_from.isoformat(), float(draft.amount_eur)) == (
        saved_values["date_from"], saved_values["amount_eur"])
    assert turn.reply.startswith("I have not saved the following yet") and "Shall I include it" in turn.reply
    confirmed = handle(repo, client, "Yes", "resp_b")      # now the owner has seen exactly these values
    assert [event.id for event in confirmed.saved_events] == [8]


def test_the_backend_removes_the_unconfirmed_row_from_the_database(repo):
    client = FakeLlmClient(
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"),
        response("Saved.", save_ok(1, **{**OVEN, "amount_eur": 9700.0}), response_id="resp_b"),
    )
    handle(repo, client, "I buy an oven on 15 July for 8k.")
    repo.add_event(expense(date(2025, 7, 15), "9700"))      # what the MCP server wrote during the save call (id 1)
    handle(repo, client, "Yes, but 9700.", "resp_a")
    assert repo.events == []


@pytest.mark.parametrize("claim", [
    "The inflow of EUR 450.00 on 2025-07-21 from the insurer has been included in the forecast.",
    "Great, I have saved the plan.", "Your closure is now included in the forecast.",
])
def test_a_reply_claiming_a_save_that_did_not_happen_is_replaced(repo, claim):
    """Finding: the assistant said a plan was included while no tool was called and nothing was stored."""
    turn = handle(repo, FakeLlmClient(response(claim, response_id="resp_a")), "OK, now yes to the refund too.")
    assert turn.reply.startswith("To be clear: nothing new was saved") and turn.saved_events == ()


def test_a_false_save_claim_while_a_proposal_waits_asks_for_confirmation_again(repo):
    client = FakeLlmClient(
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"),
        response("The oven has been added to your plans.", response_id="resp_b"),
    )
    handle(repo, client, "I buy an oven on 15 July for 8k.")
    turn = handle(repo, client, "Yes", "resp_a")
    assert turn.reply == ("To be clear: nothing has been saved yet. Please check: New oven: expense of €8,000 on "
                          "15 July 2025. Shall I include it in the forecast?")
    assert len(turn.pending_events) == 1


def test_statements_about_existing_plans_are_not_mistaken_for_a_false_claim(repo):
    listed = mcp_call("list_planned_events", {"analysis_date": "2025-05-10", "events": []})
    for scripted in (response("Your closure has been saved earlier.", listed), response("Rent is paid on the 1st.")):
        assert handle(repo, FakeLlmClient(scripted)).reply == scripted.text


def test_the_model_sees_the_latest_decisions(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    service.record_decision(repo, MAY_10, "A", DecisionStatus.APPROVED, None)
    client = FakeLlmClient(response("You approved the credit line.", response_id="resp_a"))
    handle(repo, client, "What did I just approve?")
    situation = json.loads(client.requests[0].instructions.split("SITUATION (JSON):\n")[1])
    assert situation["recent_decisions"] == [{
        "option": "A", "title": "Credit line", "decision": "approved", "decided_for_date": "2025-05-10",
        "what_it_does": repo.decisions[0].option_description, "interest_cost_eur": 67.6}]


# --- time budget -------------------------------------------------------------------------------------------

def test_every_model_call_gets_a_timeout_within_the_turn_budget(repo):
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Saved.", save_ok(7), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    handle(repo, client, "Yes", "resp_a")
    answer_timeout, _, comment_timeout = client.timeouts
    assert 0 < answer_timeout <= 90 and comment_timeout == 20


def test_the_forecast_comment_is_skipped_when_the_budget_is_nearly_used_up(repo, monkeypatch):
    monkeypatch.setattr(llm_chat, "CHAT_TURN_BUDGET_SECONDS", 15.0)
    client = FakeLlmClient(
        response("Should I include it?", propose_ok(), response_id="resp_a"),
        response("Saved.", save_ok(7), response_id="resp_b"),
    )
    handle(repo, client, "We close 1 to 21 June.")
    turn = handle(repo, client, "Yes", "resp_a")
    assert (turn.conversation_id, turn.reply, turn.events_changed) == ("resp_b", "Saved.", True)
    assert all(timeout <= 15 for timeout in client.timeouts)


def test_a_used_up_budget_is_reported_as_unavailable(repo, monkeypatch):
    monkeypatch.setattr(llm_chat, "CHAT_TURN_BUDGET_SECONDS", 0.0)
    with pytest.raises(AssistantUnavailableError):
        handle(repo, FakeLlmClient(response("Hello")))


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
    store = PendingPlansStore()
    application.dependency_overrides[get_pending_plans_store] = lambda: store
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
                                     {"message": "Hi", "horizon_days": 45}, {"message": "Hi", "unknown": 1},
                                     {"message": "Hi", "conversation_id": "../models"},
                                     {"message": "Hi", "conversation_id": "../../v1/models?x=1"},
                                     {"message": "Hi", "conversation_id": "resp_1/extra"}])
def test_chat_endpoint_validates_the_request(repo, payload):
    reply = api_client(repo, FakeLlmClient()).post("/chat", json=payload)
    assert (reply.status_code, reply.json()["error"]["code"]) == (422, "validation_error")
