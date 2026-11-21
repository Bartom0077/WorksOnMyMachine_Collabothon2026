"""POST /chat: what the backend derives from the model's MCP calls, the save gate, threads and failures."""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import create_app
from deps import get_repository
import service
from domain import DashboardQuery, DecisionStatus, EventDraft, EventSource, EventType
from errors import AssistantUnavailableError
from fakes import closure, expense
from llm import LlmContextOverflowError, LlmHttpError, LlmTimeoutError
import llm_chat
from llm_chat import (
    ChatService,
    PendingPlansStore,
    pending_drafts,
    pending_removals,
    removed_events,
    saved_events,
    tool_call_ok,
)
from llm_fakes import (
    FakeLlmClient,
    mcp_call,
    propose_ok,
    propose_rejected,
    propose_removal_ok,
    propose_removal_rejected,
    remove_already_gone,
    remove_ok,
    remove_refused,
    response,
    save_duplicate,
    save_ok,
)
from llm_prompts import (
    PLANS_CHANGED_COMMENT_INSTRUCTIONS,
    REMOVAL_COMMENT_INSTRUCTIONS,
    UPDATE_COMMENT_INSTRUCTIONS,
)
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
    assert "remove_planned_event" not in request.mcp.allowed_tools and request.mcp.removable_event_ids == ()
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


# --- removing a saved plan ---------------------------------------------------------------------------------

REFUND = {"event_type": "inflow", "date_from": "2025-06-05", "date_to": "2025-06-05", "amount_eur": 2500.0,
          "description": "Tax refund"}
LISTED = mcp_call("list_planned_events", {"analysis_date": "2025-05-10", "events": []})


@pytest.fixture()
def saved(repo):
    """The repository with two plans from the chat: the holiday closure (id 7) and the tax refund (id 9)."""
    repo._next_event_id = 7
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21), "Holidays"))
    repo._next_event_id = 9
    repo.add_event(EventDraft(event_type=EventType.INFLOW, date_from=date(2025, 6, 5), date_to=date(2025, 6, 5),
                              amount_eur=Decimal("2500"), description="Tax refund"))
    return repo


AGENT_PLAN_REFUSED = propose_removal_rejected(
    "Planned event 22 (Credit line) belongs to a rescue option the owner approved, so it cannot be removed in the "
    "chat.")


def removal_offered(client: FakeLlmClient) -> list[tuple[bool, tuple[int, ...]]]:
    """Per chat request: was remove_planned_event offered, and for which plan ids."""
    return [("remove_planned_event" in request.mcp.allowed_tools, request.mcp.removable_event_ids)
            for request in client.requests if request.mcp is not None]


def test_removal_proposals_and_removals_are_read_from_the_tool_results():
    calls = [LISTED, AGENT_PLAN_REFUSED, propose_removal_ok(7), propose_removal_ok(7), propose_removal_ok(9, **REFUND),
             remove_refused(7), mcp_call("remove_planned_event", None, error="Error (code 1): None"),
             mcp_call("propose_planned_event_removal", {"valid": True, "event": {"id": "broken"}})]
    assert [event.id for event in pending_removals(calls)] == [7, 9]
    assert removed_events(calls) == ()
    assert [event.id for event in removed_events([remove_ok(9, **REFUND)])] == [9]
    assert pending_removals([propose_removal_ok(7), remove_ok(7)]) == ()   # removed later in the same turn
    assert tool_call_ok(propose_removal_ok()) and tool_call_ok(remove_ok())
    assert not tool_call_ok(AGENT_PLAN_REFUSED) and not tool_call_ok(remove_refused())


def test_a_removal_is_proposed_first_and_carried_out_only_after_the_owner_confirms(saved):
    client = FakeLlmClient(
        response("Should I remove the tax refund?", LISTED, propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done. The tax refund is no longer in the forecast.", response_id="resp_c"),
    )
    proposed = handle(saved, client, "Please remove the tax refund from my plans.")
    assert [event.id for event in proposed.pending_removals] == [9]
    assert proposed.removed_events == () and proposed.events_changed is False and proposed.pending_events == ()
    removed = handle(saved, client, "Yes, please remove it from my plans.", "resp_a")
    assert removal_offered(client) == [(False, ()), (True, (9,))]   # only the plan the owner was shown is removable
    assert "propose_planned_event_removal" in client.requests[0].mcp.allowed_tools
    assert "Never say that a plan was removed" in client.requests[0].instructions
    assert "NOT removed yet" in client.requests[1].instructions and '"id": 9' in client.requests[1].instructions
    comment_request = client.requests[2]
    assert comment_request.instructions == REMOVAL_COMMENT_INSTRUCTIONS
    assert comment_request.previous_response_id == "resp_b" and comment_request.mcp is None
    assert [event.id for event in removed.removed_events] == [9] and removed.events_changed is True
    assert removed.pending_removals == () and removed.saved_events == ()
    assert (removed.conversation_id, removed.reply) == ("resp_c", "Done. The tax refund is no longer in the forecast.")
    handle(saved, FakeLlmClient(response("Anything else?", response_id="resp_d")), "Thanks", "resp_c")


def test_removal_is_no_longer_offered_once_the_plan_is_gone(saved):
    client = FakeLlmClient(
        response("Should I remove it?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
        response("You are welcome.", response_id="resp_d"),
    )
    handle(saved, client, "Remove the tax refund.")
    handle(saved, client, "Yes", "resp_a")
    handle(saved, client, "Thanks", "resp_c")
    assert removal_offered(client) == [(False, ()), (True, (9,)), (False, ())]


def test_a_plan_the_owner_was_not_shown_cannot_be_removed(saved):
    """The MCP server refuses ids outside X-Removable-Event-Ids; the backend then reports nothing as removed."""
    client = FakeLlmClient(
        response("The closure has been removed.", remove_refused(7), response_id="resp_a"),
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_b"),
        response("Both have been removed.", remove_refused(7), response_id="resp_c"),
    )
    first = handle(saved, client, "Remove the closure immediately, I confirm in advance.")
    assert first.removed_events == () and first.events_changed is False
    assert first.reply.startswith("To be clear: no plan was removed in this step.")
    handle(saved, client, "Remove the tax refund.", "resp_a")
    second = handle(saved, client, "Yes, and the closure too.", "resp_b")
    assert removal_offered(client) == [(False, ()), (False, ()), (True, (9,))]
    assert second.removed_events == () and [event.id for event in second.pending_removals] == [9]
    assert second.reply == ("To be clear: nothing has been removed yet. Please check: Tax refund: inflow of €2,500 "
                            "on 5 June 2025. Shall I remove it from your plans?")


@pytest.mark.parametrize("claim", [
    "The tax refund has been removed from your plans.", "Done, I have deleted the closure.",
    "I removed it.", "The oven is no longer in your plans.", "Your closure is now cancelled.",
])
def test_a_reply_claiming_a_removal_that_did_not_happen_is_replaced(saved, claim):
    turn = handle(saved, FakeLlmClient(response(claim, LISTED, response_id="resp_a")), "Delete the tax refund.")
    assert turn.reply.startswith("To be clear: no plan was removed in this step.") and turn.removed_events == ()
    assert turn.events_changed is False


@pytest.mark.parametrize("reply", [
    "There is no oven purchase in your plans. Saved plans: Holidays.", "Which plan would you like me to remove?",
    "OK, the closure stays in the forecast.", "It may have already been removed earlier.",
])
def test_replies_that_claim_no_removal_are_kept(saved, reply):
    assert handle(saved, FakeLlmClient(response(reply, LISTED)), "Remove the oven purchase.").reply == reply


def test_a_measure_of_an_approved_option_is_not_offered_for_removal(saved):
    client = FakeLlmClient(
        response('Use "Withdraw option" in "My plans".', LISTED, AGENT_PLAN_REFUSED, response_id="resp_a"),
        response("As I said, withdraw the option.", response_id="resp_b"),
    )
    turn = handle(saved, client, "Remove the credit line.")
    handle(saved, client, "Yes, remove it.", "resp_a")
    assert turn.pending_removals == () and turn.removed_events == () and turn.events_changed is False
    assert [(call.name, call.ok) for call in turn.tool_calls] == [
        ("list_planned_events", True), ("propose_planned_event_removal", False)]
    assert removal_offered(client) == [(False, ()), (False, ())]


def test_declining_drops_the_pending_removal(saved):
    client = FakeLlmClient(
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_a"),
        response("OK, the closure stays in the forecast.", response_id="resp_b"),
        response("Your rent is 3,800 a month. Anything else?", response_id="resp_c"),
        response("Which plan do you mean?", response_id="resp_d"),
    )
    handle(saved, client, "Delete the holiday closure.")
    declined = handle(saved, client, "No, keep it.", "resp_a")
    handle(saved, client, "How much is my rent?", "resp_b")
    handle(saved, client, "Yes please.", "resp_c")
    assert declined.pending_removals == () and declined.removed_events == () and declined.events_changed is False
    assert declined.reply == "OK, the closure stays in the forecast."
    # Unlike a proposal to save, a removal is confirmable only in the turn directly after it was shown: after the
    # owner's "no" a later "yes" to something else finds no removal tool.
    assert removal_offered(client) == [(False, ()), (True, (7,)), (False, ()), (False, ())]


def test_a_new_proposal_replaces_a_pending_removal(saved):
    client = FakeLlmClient(
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_a"),
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_b"),
        response("Saved.", save_ok(8, **OVEN), response_id="resp_c"),
        response("Done.", response_id="resp_d"),
    )
    handle(saved, client, "Delete the holiday closure.")
    changed_topic = handle(saved, client, "Actually, I buy an oven on 15 July for 8k.", "resp_a")
    handle(saved, client, "Yes", "resp_b")
    assert changed_topic.pending_removals == () and len(changed_topic.pending_events) == 1
    assert removal_offered(client) == [(False, ()), (True, (7,)), (False, ())]
    assert "save_planned_event" in client.requests[2].mcp.allowed_tools


def test_removing_one_of_two_shown_plans_keeps_the_other_waiting(saved):
    client = FakeLlmClient(
        response("Should I remove both?", propose_removal_ok(7), propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Removed the refund.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
        response("Removed.", remove_ok(7), response_id="resp_d"),
        response("Done.", response_id="resp_e"),
    )
    shown = handle(saved, client, "Remove the closure and the tax refund.")
    partial = handle(saved, client, "Yes to the refund only.", "resp_a")
    rest = handle(saved, client, "Yes, the closure too.", "resp_c")
    assert [event.id for event in shown.pending_removals] == [7, 9]
    assert [event.id for event in partial.removed_events] == [9]
    assert [event.id for event in partial.pending_removals] == [7]   # still shown: the owner may confirm it next
    assert removal_offered(client) == [(False, ()), (True, (7, 9)), (True, (7,))]
    assert [event.id for event in rest.removed_events] == [7]


def test_a_turn_that_saves_and_removes_gets_a_comment_about_both(saved):
    client = FakeLlmClient(
        response("Shall I?", propose_ok(**OVEN), propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Both done.", save_ok(8, **OVEN), remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    handle(saved, client, "Add the oven on 15 July for 8k and remove the tax refund.")
    turn = handle(saved, client, "Yes to both.", "resp_a")
    assert client.requests[2].instructions == PLANS_CHANGED_COMMENT_INSTRUCTIONS
    assert [event.id for event in turn.saved_events] == [8] and [event.id for event in turn.removed_events] == [9]


def test_context_overflow_carries_the_pending_removal_into_the_new_thread(saved):
    client = FakeLlmClient(
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        LlmContextOverflowError("maximum context length"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    handle(saved, client, "Remove the tax refund.")
    turn = handle(saved, client, "Yes, remove it.", "resp_a")
    restarted = client.requests[2]
    assert restarted.previous_response_id is None and restarted.mcp.removable_event_ids == (9,)
    assert "asked whether to remove" in restarted.input and '"id": 9' in restarted.input
    assert [event.id for event in turn.removed_events] == [9]


def test_chat_endpoint_reports_removals_in_the_event_shape(saved):
    llm = FakeLlmClient(
        response("Should I remove it?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    http = api_client(saved, llm)
    refund = {**REFUND, "id": 9, "category": None, "original_utterance": "We close 1-21 June.", "source": "chat",
              "as_of_date": "2025-05-10", "decision_id": None, "created_at": "2026-11-21T10:27:53.432984Z"}
    first = http.post("/chat", json={"message": "Remove the tax refund."}).json()
    assert first["pending_removals"] == [refund] and first["removed_events"] == []
    assert (first["pending_events"], first["saved_events"], first["events_changed"]) == ([], [], False)
    body = http.post("/chat", json={"message": "Yes", "conversation_id": first["conversation_id"]}).json()
    assert body["removed_events"] == [refund] and body["pending_removals"] == []
    assert (body["saved_events"], body["events_changed"], body["reply"]) == ([], True, "Done.")


# --- when the removal job is part of the turn ---------------------------------------------------------------

REMOVAL_WORDINGS = ["Remove the oven purchase.", "Forget the oven.", "Please delete my holiday closure",
                    "Cancel the closure.", "Get rid of the refund plan."]
OTHER_WORDINGS = ["We also close from 1 to 5 August for renovation.", "I will withdraw 2,000 euros in cash on 5 June.",
                  "I'll take the whole team out for dinner on 12 June, 400 euros.", "Why is June so weak?",
                  "Sales drop in August, so we close from 1 to 5 August.", "Did the tax refund get removed?",
                  "We no longer open on Mondays; we close from 1 to 5 August."]
UNUSUAL_REMOVAL_WORDINGS = ["I don't want the holiday closure any more.", "The oven is off.",
                            "We won't buy the oven after all."]
NOTHING_REMOVABLE = "No plan from the chat is saved right now"
HAND_BACK = "reply with exactly REMOVE_PLAN_REQUEST and nothing else"


def removal_job_offered(request) -> bool:
    """The model can propose a removal: the job, the plan ids and the tool come together or not at all. Without
    them the prompt has exactly one of the two short lines about removing."""
    job = "JOB 3 - REMOVE A SAVED PLAN" in request.instructions
    tool = "propose_planned_event_removal" in request.mcp.allowed_tools
    ids = '"source": "chat"' in request.instructions
    assert job == tool == ids and ("exactly three jobs" in request.instructions) == job
    assert [job, NOTHING_REMOVABLE in request.instructions, HAND_BACK in request.instructions].count(True) == 1
    return job


@pytest.mark.parametrize("message", REMOVAL_WORDINGS + OTHER_WORDINGS + UNUSUAL_REMOVAL_WORDINGS)
def test_without_a_saved_chat_plan_no_wording_brings_the_removal_job(repo, message):
    """An owner with nothing to remove keeps the short two-job prompt, whatever the message says."""
    repo.add_event(expense(date(2025, 6, 20), "500", "Credit line"), EventSource.AGENT)
    client = FakeLlmClient(response("OK."))
    handle(repo, client, message)
    request = client.requests[0]
    assert not removal_job_offered(request) and "exactly two jobs" in request.instructions
    assert NOTHING_REMOVABLE in request.instructions and '"Withdraw option"' in request.instructions
    assert "remove_planned_event" not in request.mcp.allowed_tools and request.mcp.removable_event_ids == ()
    assert '"source":' not in request.instructions


@pytest.mark.parametrize("message", REMOVAL_WORDINGS)
def test_a_removal_request_in_the_usual_words_gets_the_removal_job_at_once(saved, message):
    client = FakeLlmClient(response("OK."))
    handle(saved, client, message)
    request = client.requests[0]
    assert removal_job_offered(request)
    assert '"id": 7, "source": "chat"' in request.instructions and '"id": 9, "source": "chat"' in request.instructions
    assert "remove_planned_event" not in request.mcp.allowed_tools and request.mcp.removable_event_ids == ()


@pytest.mark.parametrize("message", OTHER_WORDINGS)
def test_other_messages_keep_the_short_prompt_also_while_chat_plans_are_saved(saved, message):
    """Finding: add messages containing "withdraw", "drop", "take ... out", "no longer" got the removal job, with
    which the small model handles new plans worse."""
    client = FakeLlmClient(response("OK."))
    handle(saved, client, message)
    assert len(client.requests) == 1 and not removal_job_offered(client.requests[0])
    assert HAND_BACK in client.requests[0].instructions and "exactly two jobs" in client.requests[0].instructions


@pytest.mark.parametrize("message", UNUSUAL_REMOVAL_WORDINGS)
def test_a_removal_request_in_other_words_is_handed_back_by_the_model_and_asked_again(saved, message):
    """Finding: wordings outside the keyword list had no removal tool and dead-ended."""
    client = FakeLlmClient(
        response("REMOVE_PLAN_REQUEST", response_id="resp_signal"),
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_a"),
    )
    turn = handle(saved, client, message)
    first, second = client.requests
    assert not removal_job_offered(first) and removal_job_offered(second)
    assert second.input == message and second.previous_response_id is None   # the hand-back is not in the thread
    assert (turn.conversation_id, turn.reply) == ("resp_a", "Should I remove the closure?")
    assert [event.id for event in turn.pending_removals] == [7]


def test_the_hand_back_word_never_reaches_the_owner(saved):
    client = FakeLlmClient(response("REMOVE_PLAN_REQUEST", response_id="resp_signal"),
                           response("REMOVE_PLAN_REQUEST.", response_id="resp_a"),
                           response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_b"))
    turn = handle(saved, client, "The holiday thing is off.")
    assert turn.reply == (
        "Your plans from our chat: Holidays: closed from 1 June 2025 to 21 June 2025; Tax refund: inflow of €2,500 "
        "on 5 June 2025. To remove one, tell me which, and I will show it to you for confirmation first.")
    handle(saved, client, "The holidays.", "resp_a")
    assert removal_job_offered(client.requests[2])   # the answer to "which one?" can be acted on


def test_the_answer_to_a_question_about_the_removal_still_gets_the_removal_job(saved):
    """The small model sometimes asks "which one?" or "shall I remove it?" without the tool call."""
    client = FakeLlmClient(
        response("Which plan would you like to remove?", response_id="resp_a"),
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_b"),
        response("OK, it stays.", response_id="resp_c"),
        response("OK.", response_id="resp_d"),
    )
    asked = handle(saved, client, "Remove my plans.")
    handle(saved, client, "The holiday closure.", "resp_a")
    handle(saved, client, "No, keep it.", "resp_b")
    handle(saved, client, "We also close from 1 to 5 August for renovation.", "resp_c")
    assert asked.pending_removals == ()
    assert [removal_job_offered(request) for request in client.requests] == [True, True, True, False]
    assert removal_offered(client) == [(False, ()), (False, ()), (True, (7,)), (False, ())]


def test_a_removal_request_answered_without_a_question_does_not_keep_the_removal_job(saved):
    client = FakeLlmClient(response("There is no oven purchase in your plans. Saved: Holidays, Tax refund.",
                                    response_id="resp_a"), response("OK.", response_id="resp_b"))
    handle(saved, client, "Remove the oven.")
    handle(saved, client, "I am buying an oven for 8,000 on 20 June.", "resp_a")
    assert [removal_job_offered(request) for request in client.requests] == [True, False]


def test_a_chat_plan_beyond_the_forecast_horizon_can_still_be_removed(repo):
    oven = repo.add_event(expense(date(2025, 9, 15), "8000"))   # MAY_10 looks 60 days ahead
    client = FakeLlmClient(response("OK."))
    handle(repo, client, "Forget the oven.")
    assert removal_job_offered(client.requests[0])
    assert f'"id": {oven.id}, "source": "chat"' in client.requests[0].instructions


def test_a_new_plan_right_after_a_removal_request_for_a_missing_plan_gets_the_short_prompt(repo):
    """Finding: the turn after "Remove the oven." kept the removal job, and the small model then mishandled
    "We also close from 1 to 5 August for renovation."."""
    client = FakeLlmClient(
        response("There is no oven purchase in your plans. No plans are saved.", response_id="resp_a"),
        response("Should I include the closure?", propose_ok(date_from="2025-08-01", date_to="2025-08-05"),
                 response_id="resp_b"),
    )
    handle(repo, client, "Remove the oven.")
    turn = handle(repo, client, "We also close from 1 to 5 August for renovation.", "resp_a")
    assert [removal_job_offered(request) for request in client.requests] == [False, False]
    assert len(turn.pending_events) == 1


def test_the_code_written_which_one_question_is_followed_by_a_turn_that_can_propose(saved):
    """Finding: "tell me which one" was followed by a turn without the removal tool."""
    client = FakeLlmClient(
        response("Done, I removed it.", response_id="resp_a"),
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_b"),
    )
    first = handle(saved, client, "Forget it, delete the plan.")
    second = handle(saved, client, "The holiday closure.", "resp_a")
    assert first.reply == (
        "To be clear: no plan was removed in this step. Your plans from our chat: Holidays: closed from 1 June 2025 "
        "to 21 June 2025; Tax refund: inflow of €2,500 on 5 June 2025. To remove one, tell me which, and I will "
        "show it to you for confirmation first.")
    assert removal_job_offered(client.requests[1]) and [event.id for event in second.pending_removals] == [7]


# --- a removal is confirmable only from the turn that showed it -------------------------------------------------

def test_a_side_question_or_an_unanswered_add_question_ends_the_pending_removal(saved):
    """Finding: after "wait, I get a refund" (answered with a question) a plain "yes, 2025" could remove plan 7."""
    client = FakeLlmClient(
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_a"),
        response("In which year?", response_id="resp_b"),
        response("Should I include the flyers?", propose_ok(**OVEN), response_id="resp_c"),
    )
    handle(saved, client, "Delete the holiday closure.")
    side = handle(saved, client, "Wait. I pay for an oven, 8k on 15 July.", "resp_a")
    handle(saved, client, "Yes, 2025.", "resp_b")
    assert side.pending_removals == ()
    assert removal_offered(client) == [(False, ()), (True, (7,)), (False, ())]


def test_an_old_turn_cannot_be_used_to_confirm_a_removal_again(saved):
    """Finding: replaying the conversation id of the proposal turn re-armed the removal after a decline."""
    client = FakeLlmClient(
        response("Should I remove the closure?", propose_removal_ok(7), response_id="resp_a"),
        response("OK, it stays.", response_id="resp_b"),
        response("Which plan do you mean?", response_id="resp_c"),
    )
    handle(saved, client, "Delete the holiday closure.")
    handle(saved, client, "No, keep it.", "resp_a")
    handle(saved, client, "Yes, remove it.", "resp_a")   # the old id again
    assert removal_offered(client) == [(False, ()), (True, (7,)), (False, ())]


def test_an_old_turn_cannot_be_used_to_confirm_a_save_again(repo):
    client = FakeLlmClient(
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"),
        response("OK, I will not include it.", response_id="resp_b"),
        response("Which plan?", response_id="resp_c"),
    )
    handle(repo, client, "I buy an oven on 15 July for 8k.")
    handle(repo, client, "No.", "resp_a")
    handle(repo, client, "Yes.", "resp_a")
    assert ["save_planned_event" in request.mcp.allowed_tools for request in client.requests] == [False, True, False]


def test_a_removal_proposal_does_not_cancel_a_waiting_draft(saved):
    """Finding: proposing a removal while a draft waited silently dropped the draft."""
    client = FakeLlmClient(
        response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"),
        response("Should I remove the tax refund first?", propose_removal_ok(9, **REFUND), response_id="resp_b"),
        response("Both done.", save_ok(8, **OVEN), remove_ok(9, **REFUND), response_id="resp_c"),
        response("Done.", response_id="resp_d"),
    )
    handle(saved, client, "I buy an oven on 15 July for 8k.")
    mixed = handle(saved, client, "Before that, remove the tax refund.", "resp_a")
    both = handle(saved, client, "Yes to both.", "resp_b")
    assert [event.id for event in mixed.pending_removals] == [9]
    assert {"save_planned_event", "remove_planned_event"} <= set(client.requests[2].mcp.allowed_tools)
    assert "NOT saved yet" in client.requests[2].instructions and "NOT removed yet" in client.requests[2].instructions
    assert [event.id for event in both.saved_events] == [8] and [event.id for event in both.removed_events] == [9]


def test_no_header_and_no_remove_tool_without_a_pending_removal(saved):
    from llm import _mcp_tool
    client = FakeLlmClient(response("Should I remove it?", propose_removal_ok(7), response_id="resp_a"),
                           response("OK, it stays.", response_id="resp_b"))
    handle(saved, client, "Delete the closure.")
    handle(saved, client, "No.", "resp_a")
    first, second = (_mcp_tool(request.mcp) for request in client.requests)
    assert "X-Removable-Event-Ids" not in first["headers"] and "remove_planned_event" not in first["allowed_tools"]
    assert second["headers"]["X-Removable-Event-Ids"] == "7" and "remove_planned_event" in second["allowed_tools"]


# --- replies about removals ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim", [
    "Your holiday closure was removed from your plans.", "Done - removed the closure.", "The closure is removed.",
    "Removed.", "I've successfully removed the closure.", "I went ahead and took the closure out of your plans.",
    "The plan is gone from your forecast.", "OK. Deleted the closure as you asked.", "The closure got deleted.",
    "Sure!\nThe closure has already been cancelled.",
])
def test_more_wordings_of_a_removal_that_did_not_happen_are_replaced(saved, claim):
    turn = handle(saved, FakeLlmClient(response(claim)), "Delete the closure, I confirm in advance.")
    assert turn.reply.startswith("To be clear: no plan was removed in this step.") and turn.events_changed is False


@pytest.mark.parametrize("message, reply, calls", [
    ("Is the oven still in my plans?", "No, the oven is no longer in your plans.", (LISTED,)),
    ("Why is June so weak?", "The catering order has been cancelled, so no deposit comes in.", ()),
    ("Did the tax refund get removed?", "Yes, the tax refund has been removed and is no longer in the forecast.", ()),
    ("Delete the closure.", "The closure has not been removed. Should I remove it?", ()),
    ("No, keep it.", "OK, I have cancelled the removal. The closure stays in the forecast.", ()),
    ("Remove the oven.", "Nothing was removed: there is no oven in your plans.", ()),
])
def test_statements_that_report_no_removal_in_this_turn_are_kept(saved, message, reply, calls):
    """Finding: the guard ran on every turn and replaced answers to status questions and explanations."""
    assert handle(saved, FakeLlmClient(response(reply, *calls)), message).reply == reply


def test_a_claim_that_all_shown_plans_were_removed_is_corrected_when_one_is_left(saved):
    client = FakeLlmClient(
        response("Remove both?", propose_removal_ok(7), propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Both removed.", remove_ok(9, **REFUND), response_id="resp_b"),
        response("Done. Both plans have been removed.", response_id="resp_c"),
    )
    handle(saved, client, "Remove the closure and the refund.")
    turn = handle(saved, client, "Yes.", "resp_a")
    assert turn.reply == ("Done. Both plans have been removed. Still in your plans, not removed: Holidays: closed "
                          "from 1 June 2025 to 21 June 2025.")
    assert [event.id for event in turn.pending_removals] == [7]


def test_a_turn_that_removes_and_reverts_a_save_names_the_removal(saved):
    client = FakeLlmClient(
        response("Shall I?", propose_ok(**OVEN), propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Both done.", save_ok(8, **{**OVEN, "amount_eur": 9700.0}), remove_ok(9, **REFUND),
                 response_id="resp_b"),
        response("Done.", response_id="resp_c"),
    )
    saved.add_event(expense(date(2025, 7, 15), "9700"))   # the row the model wrote; the backend deletes it again
    handle(saved, client, "Add the oven on 15 July for 8k and remove the tax refund.")
    turn = handle(saved, client, "Yes, but the oven is 9,700.", "resp_a")
    assert turn.reply.startswith("Removed from your plans: Tax refund: inflow of €2,500 on 5 June 2025. I have not "
                                 "saved the following yet")
    assert [event.id for event in turn.removed_events] == [9] and turn.saved_events == ()


def test_a_confirmed_plan_that_was_already_gone_counts_as_removed(saved):
    """Finding: a repeated request (or a removal by hand between the turns) was answered "nothing has been
    removed yet" and the owner was asked again about a plan that no longer existed."""
    client = FakeLlmClient(
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("The tax refund has been removed.", remove_already_gone(9), response_id="resp_b"),
        response("Done. The tax refund is no longer in the forecast.", response_id="resp_c"),
    )
    handle(saved, client, "Remove the tax refund.")
    saved.delete_event(9)
    turn = handle(saved, client, "Yes.", "resp_a")
    assert [event.id for event in turn.removed_events] == [9] and turn.events_changed is True
    assert turn.pending_removals == () and turn.reply == "Done. The tax refund is no longer in the forecast."
    assert [(call.name, call.ok) for call in turn.tool_calls] == [("remove_planned_event", True)]


def test_an_already_gone_answer_for_a_plan_the_owner_was_not_shown_changes_nothing(saved):
    client = FakeLlmClient(response("Removed.", remove_already_gone(7), response_id="resp_a"))
    turn = handle(saved, client, "Remove the closure.")
    assert turn.removed_events == () and turn.events_changed is False
    assert turn.reply.startswith("To be clear: no plan was removed in this step.")


def test_a_removal_is_logged_with_the_whole_plan(saved, caplog):
    caplog.set_level("INFO", logger="llm_chat")
    client = FakeLlmClient(
        response("Should I remove it?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"), response("Done.", response_id="resp_c"))
    handle(saved, client, "Remove the tax refund.")
    handle(saved, client, "Yes.", "resp_a")
    line = next(record.getMessage() for record in caplog.records if "was removed on the owner's" in record.getMessage())
    assert "resp_b" in line and '"description":"Tax refund"' in line and '"original_utterance"' in line


# --- the model fails in a turn that could write -------------------------------------------------------------------

def test_a_timeout_after_the_plan_was_removed_reports_the_removal(saved):
    """Finding: OGX may run remove_planned_event and then time out; the owner was told "your plans are not
    affected"."""
    client = FakeLlmClient(
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        LlmTimeoutError("slow"),
        response("Anything else?", response_id="resp_b"),
    )
    handle(saved, client, "Remove the tax refund.")
    saved.delete_event(9)   # what the MCP server did before the model's answer timed out
    turn = handle(saved, client, "Yes.", "resp_a")
    assert [event.id for event in turn.removed_events] == [9] and turn.events_changed is True
    assert turn.reply == ("I could not finish my answer, but this change went through. Removed from your plans: Tax "
                          "refund: inflow of €2,500 on 5 June 2025. The forecast has been recalculated.")
    assert (turn.conversation_id, turn.pending_removals, turn.tool_calls) == ("resp_a", (), ())
    handle(saved, client, "Thanks", turn.conversation_id)
    assert removal_offered(client)[-1] == (False, ())


def test_a_timeout_after_the_plan_was_saved_reports_the_save(repo):
    client = FakeLlmClient(response("Should I include the oven?", propose_ok(**OVEN), response_id="resp_a"))
    handle(repo, client, "I buy an oven on 15 July for 8k.")

    def save_then_fail(request, timeout_seconds=None):   # the MCP server stored the plan, then OGX failed
        repo.add_event(expense(date(2025, 7, 15), "8000"))
        raise LlmHttpError("HTTP 500")

    client.respond = save_then_fail
    turn = handle(repo, client, "Yes.", "resp_a")
    assert [event.description for event in turn.saved_events] == ["New oven"] and turn.events_changed is True
    assert turn.reply.startswith("I could not finish my answer, but this change went through. Included in the "
                                 "forecast: New oven: expense of €8,000 on 15 July 2025.")


def test_a_timeout_that_changed_nothing_stays_assistant_unavailable(saved):
    client = FakeLlmClient(
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        LlmTimeoutError("slow"),
        response("Removed.", remove_ok(9, **REFUND), response_id="resp_b"), response("Done.", response_id="resp_c"),
    )
    handle(saved, client, "Remove the tax refund.")
    with pytest.raises(AssistantUnavailableError, match="not affected"):
        handle(saved, client, "Yes.", "resp_a")
    retried = handle(saved, client, "Yes.", "resp_a")   # the owner can simply answer again
    assert [event.id for event in retried.removed_events] == [9]


def test_a_removal_followed_by_an_empty_answer_is_still_reported(saved):
    client = FakeLlmClient(
        response("Should I remove the tax refund?", propose_removal_ok(9, **REFUND), response_id="resp_a"),
        response("", remove_ok(9, **REFUND), response_id="resp_b"),
        LlmTimeoutError("slow"),
    )
    handle(saved, client, "Remove the tax refund.")
    turn = handle(saved, client, "Yes.", "resp_a")
    assert turn.reply == ("Done. Removed from your plans: Tax refund: inflow of €2,500 on 5 June 2025. The forecast "
                          "has been recalculated.")
    assert [event.id for event in turn.removed_events] == [9]


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
        "pending_removals": [],
        "removed_events": [],
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
