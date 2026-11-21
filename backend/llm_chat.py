"""POST /chat: the scenario assistant (ROZWIAZANIE.md §8.1 and §11).

The model turns the owner's words into planned events through the MCP tools and explains the situation the
backend computed. OGX stores the chat history; ``conversation_id`` is the id of the latest OGX response.

Writing a plan takes two turns, and code enforces it:
- ``save_planned_event`` is only offered while checked proposals the owner has seen are waiting (``PendingPlans``,
  kept per conversation in this process);
- after the model's turn every saved plan is compared with those proposals. A plan the owner never saw in that
  exact form (other dates, another amount) is removed again and shown for confirmation instead;
- a reply that claims a save although nothing was stored is replaced by a statement written by code.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Sequence

from pydantic import ValidationError

import service
from config import (
    CHAT_TURN_BUDGET_SECONDS,
    LLM_CHAT_MAX_OUTPUT_TOKENS,
    LLM_COMMENT_MAX_OUTPUT_TOKENS,
    LLM_COMMENT_TIMEOUT_SECONDS,
    LLM_LOOKUP_TIMEOUT_SECONDS,
    LLM_TIMEOUT_SECONDS,
    PENDING_PLANS_CACHE_SIZE,
)
from domain import Dashboard, DashboardQuery, DecisionRecord, EventDraft
from errors import AssistantUnavailableError
from llm import (
    Deadline,
    LlmClient,
    LlmContextOverflowError,
    LlmError,
    LlmRequest,
    LlmResponse,
    LlmResponseNotFoundError,
    McpAccess,
    McpCall,
)
from llm_prompts import (
    UPDATE_COMMENT_INSTRUCTIONS,
    carried_context_input,
    chat_instructions,
    describe_draft,
    update_comment_input,
)
from repository import Repository
from schemas import ChatEventDraftOut, EventOut

logger = logging.getLogger(__name__)

PROPOSE_TOOL = "propose_planned_event"
SAVE_TOOL = "save_planned_event"
LIST_TOOL = "list_planned_events"
# Read tools offered in chat: saved plans and the regular fixed payments. Two kinds are left out on purpose.
# The KPI snapshot carries its own 30-day forecast that ignores planned events and contradicts the situation
# computed by the backend. The transaction query tools need filter values the model invents (it answered
# "no rent was paid" after filtering by a transaction type that does not exist), and a wrong fact is worse
# than no answer.
READ_TOOLS = (LIST_TOOL, "get_fixed_obligations")

_UNAVAILABLE_MESSAGE = "The assistant is currently unavailable. Your plans and the forecast are not affected."
_RECENT_DECISIONS_IN_CHAT = 3
# A checked proposal stays confirmable through this many turns that neither propose nor save anything
# (a side question between the summary and the owner's "yes").
_MAX_IDLE_TURNS = 1
# Phrases with which the model reports a completed save ("has been included", "I have saved", "is now saved").
_SAVE_CLAIM = re.compile(
    r"\b(?:has|have)\s+(?:now\s+|just\s+)?been\s+(?:saved|included|added|recorded)\b"
    r"|\bI(?:'ve|\s+have)?\s+(?:now\s+|just\s+)?(?:saved|included|added|recorded)\b"
    r"|\b(?:is|are)\s+now\s+(?:saved|included|added|recorded|part\s+of)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class ToolCall:
    name: str
    ok: bool


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: str
    reply: str
    pending_events: tuple[EventDraft, ...]
    saved_events: tuple[EventOut, ...]
    tool_calls: tuple[ToolCall, ...]

    @property
    def events_changed(self) -> bool:
        return bool(self.saved_events)


@dataclass(frozen=True)
class PendingPlans:
    """Checked proposals of one conversation that the owner has seen and not yet answered."""

    drafts: tuple[EventDraft, ...] = ()
    idle_turns: int = 0


class PendingPlansStore:
    """Small thread-safe LRU: conversation id -> proposals waiting for the owner's yes.

    Process-local on purpose. If an entry is missing (restart, another replica) saving is simply not offered
    and the assistant asks again, which is the safe direction.
    """

    def __init__(self, max_entries: int = PENDING_PLANS_CACHE_SIZE):
        self._entries: OrderedDict[str, PendingPlans] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def get(self, conversation_id: str) -> PendingPlans:
        with self._lock:
            return self._entries.get(conversation_id, PendingPlans())

    def put(self, conversation_id: str, plans: PendingPlans) -> None:
        with self._lock:
            self._entries[conversation_id] = plans
            self._entries.move_to_end(conversation_id)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


@dataclass(frozen=True)
class _Outcome:
    """What one model turn really changed, after the saves were checked against the confirmed proposals."""

    saved: tuple[EventOut, ...]
    reverted: tuple[EventDraft, ...]  # saved by the model without the owner's yes to these values; removed again
    proposed: tuple[EventDraft, ...]
    still_waiting: tuple[EventDraft, ...]  # earlier proposals neither saved nor replaced in this turn


class ChatService:
    def __init__(self, client: LlmClient, mcp_server_url: str, pending: PendingPlansStore):
        self._client = client
        self._mcp_server_url = mcp_server_url
        self._pending = pending

    def handle(self, repository: Repository, query: DashboardQuery, message: str,
               conversation_id: Optional[str]) -> ChatTurn:
        deadline = Deadline(CHAT_TURN_BUDGET_SECONDS)
        dashboard = service.build_dashboard(repository, query)
        decisions = repository.list_decisions()[:_RECENT_DECISIONS_IN_CHAT]
        try:
            previous = self._previous_response(conversation_id, deadline)
            waiting = self._pending.get(previous.id) if previous else PendingPlans()
            response = self._ask(dashboard, decisions, message, previous, waiting.drafts, deadline)
        except LlmError as exc:
            logger.error("Chat failed (%s): %s", exc.reason, exc)
            raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        for call in response.mcp_calls:
            logger.info("Chat tool call %s %s ok=%s", call.name, dict(call.arguments), tool_call_ok(call))
        outcome = self._checked_outcome(repository, response, waiting.drafts)
        final = self._comment_on_update(repository, query, response, deadline) if outcome.saved else response
        if not final.text:
            logger.error("Chat failed: the model returned no text (response %s).", final.id)
            raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE)
        plans = _plans_after_turn(outcome, waiting)
        reply = final.text
        if outcome.reverted:
            reply = _reverted_reply(outcome)
        elif not outcome.saved and _claims_a_save_that_did_not_happen(final.text, response):
            logger.warning("Replacing a reply that claims a save although nothing was stored: %r", final.text)
            plans = PendingPlans(plans.drafts)  # the owner is asked again right now
            reply = _nothing_saved_reply(plans.drafts)
        self._pending.put(final.id, plans)
        return ChatTurn(
            conversation_id=final.id,
            reply=reply,
            pending_events=plans.drafts if plans.idle_turns == 0 else (),
            saved_events=outcome.saved,
            tool_calls=tuple(ToolCall(call.name, tool_call_ok(call)) for call in response.mcp_calls),
        )

    def _previous_response(self, conversation_id: Optional[str], deadline: Deadline) -> Optional[LlmResponse]:
        """The turn to continue from. An id OGX does not know (expired, restarted) simply starts a new thread."""
        if conversation_id is None:
            return None
        try:
            return self._client.get_response(conversation_id, deadline.timeout(LLM_LOOKUP_TIMEOUT_SECONDS))
        except LlmResponseNotFoundError:
            logger.info("Conversation %s is unknown to OGX; starting a new thread.", conversation_id)
            return None

    def _ask(self, dashboard: Dashboard, decisions: Sequence[DecisionRecord], message: str,
             previous: Optional[LlmResponse], waiting: Sequence[EventDraft], deadline: Deadline) -> LlmResponse:
        instructions = chat_instructions(dashboard, waiting, decisions)
        request = self._request(dashboard, instructions, message, bool(waiting), previous.id if previous else None)
        try:
            return self._client.respond(request, deadline.timeout(LLM_TIMEOUT_SECONDS))
        except LlmContextOverflowError:
            if previous is None:
                raise
            logger.info("Conversation %s no longer fits the context window; continuing in a new thread.", previous.id)
            carried = carried_context_input(message, previous.text, waiting)
            restarted = self._request(dashboard, instructions, carried, bool(waiting), None)
            return self._client.respond(restarted, deadline.timeout(LLM_TIMEOUT_SECONDS))

    def _request(self, dashboard: Dashboard, instructions: str, user_input: str, save_allowed: bool,
                 previous_response_id: Optional[str]) -> LlmRequest:
        write_tools = (PROPOSE_TOOL, SAVE_TOOL) if save_allowed else (PROPOSE_TOOL,)
        return LlmRequest(
            instructions=instructions,
            input=user_input,
            max_output_tokens=LLM_CHAT_MAX_OUTPUT_TOKENS,
            previous_response_id=previous_response_id,
            mcp=McpAccess(self._mcp_server_url, dashboard.as_of_date, write_tools + READ_TOOLS),
        )

    def _checked_outcome(self, repository: Repository, response: LlmResponse,
                         waiting: Sequence[EventDraft]) -> _Outcome:
        """Keep only saves of plans the owner confirmed; remove every other row the model wrote."""
        confirmed, reverted = [], []
        for event in saved_events(response.mcp_calls):
            draft = _as_draft(event)
            if any(_same_plan(draft, shown) for shown in waiting):
                confirmed.append(event)
                continue
            logger.warning("Removing planned event %s: the model saved %s, which the owner had not confirmed.",
                           event.id, describe_draft(draft))
            repository.delete_event(event.id)
            reverted.append(draft)
        stored = [draft for draft in map(_stored_draft, response.mcp_calls) if draft is not None]
        return _Outcome(
            saved=tuple(confirmed),
            reverted=tuple(reverted),
            proposed=pending_drafts(response.mcp_calls),
            still_waiting=tuple(draft for draft in waiting if not any(_same_plan(draft, done) for done in stored)),
        )

    def _comment_on_update(self, repository: Repository, query: DashboardQuery, response: LlmResponse,
                           deadline: Deadline) -> LlmResponse:
        """After a plan was saved: let the model tell the owner what the recalculated forecast shows (§8.1 step 7).

        The plan is already stored, so a failure here must not fail the turn; the model's own reply is kept.
        The comment is skipped when the turn's time budget cannot cover it.
        """
        if deadline.remaining() < LLM_COMMENT_TIMEOUT_SECONDS:
            logger.warning("No forecast comment after saving: the time budget of the turn is nearly used up.")
            return response
        updated = service.build_dashboard(repository, query)
        try:
            comment = self._client.respond(LlmRequest(
                instructions=UPDATE_COMMENT_INSTRUCTIONS,
                input=update_comment_input(updated),
                max_output_tokens=LLM_COMMENT_MAX_OUTPUT_TOKENS,
                previous_response_id=response.id,
            ), LLM_COMMENT_TIMEOUT_SECONDS)
        except LlmError as exc:
            logger.warning("No forecast comment after saving (%s): %s", exc.reason, exc)
            return response
        return comment if comment.text else response


def _plans_after_turn(outcome: _Outcome, waiting: PendingPlans) -> PendingPlans:
    """Proposals the owner can say yes to in the next turn."""
    shown_now = _without_duplicates((*outcome.reverted, *outcome.proposed))
    if shown_now:
        return PendingPlans(shown_now)  # the newest summary replaces older ones
    if outcome.saved:
        return PendingPlans(outcome.still_waiting)  # a partial yes: the rest keeps waiting
    if waiting.idle_turns >= _MAX_IDLE_TURNS:
        return PendingPlans()
    return PendingPlans(outcome.still_waiting, waiting.idle_turns + 1)


def _reverted_reply(outcome: _Outcome) -> str:
    """Written by code: what was kept, what was not saved and why, and the question to confirm it."""
    kept = "".join(f"Included in the forecast: {describe_draft(_as_draft(event))}. " for event in outcome.saved)
    return (f"{kept}I have not saved the following yet, because it is not exactly what you confirmed. "
            f"{_confirmation_question(outcome.reverted)}")


def _claims_a_save_that_did_not_happen(reply: str, response: LlmResponse) -> bool:
    return bool(_SAVE_CLAIM.search(reply)) and not _consulted_saved_plans(response)


def _nothing_saved_reply(confirmable: Sequence[EventDraft]) -> str:
    if confirmable:
        return f"To be clear: nothing has been saved yet. {_confirmation_question(confirmable)}"
    return ("To be clear: nothing new was saved in this step. To add a plan, tell me what it is, on which day and "
            "how much, and I will show it to you for confirmation first.")


def _confirmation_question(drafts: Sequence[EventDraft]) -> str:
    plans = "; ".join(describe_draft(draft) for draft in drafts)
    return f"Please check: {plans}. Shall I include {'it' if len(drafts) == 1 else 'them'} in the forecast?"


def _consulted_saved_plans(response: LlmResponse) -> bool:
    """The model looked up plans that already exist, so "has been saved" may describe one of those."""
    return any((call.name == LIST_TOOL and tool_call_ok(call)) or _stored_draft(call) is not None
               for call in response.mcp_calls)


def _without_duplicates(drafts: Sequence[EventDraft]) -> tuple[EventDraft, ...]:
    unique: list[EventDraft] = []
    for draft in drafts:
        if not any(_same_plan(draft, other) for other in unique):
            unique.append(draft)
    return tuple(unique)


def _as_draft(event: EventOut) -> EventDraft:
    return EventDraft(event_type=event.event_type, date_from=event.date_from, date_to=event.date_to,
                      amount_eur=event.amount_eur, description=event.description)


def tool_call_ok(call: McpCall) -> bool:
    """A call succeeded when the MCP layer reported no error and the tool did not reject the input."""
    result = call.result()
    return result is not None and "error" not in result


def saved_events(calls: Sequence[McpCall]) -> tuple[EventOut, ...]:
    """Events written to planned_events in this turn (an "already saved" duplicate wrote nothing)."""
    events = []
    for call in calls:
        result = call.result() if call.name == SAVE_TOOL else None
        if result is None or result.get("saved") is not True:
            continue
        try:
            events.append(EventOut.model_validate(result.get("event")))
        except ValidationError as exc:
            logger.error("save_planned_event reported an event the backend cannot read: %s", exc)
    return tuple(events)


def pending_drafts(calls: Sequence[McpCall]) -> tuple[EventDraft, ...]:
    """Valid proposals of this turn that were not saved later in the same turn, without duplicates."""
    pending: list[EventDraft] = []
    for index, call in enumerate(calls):
        draft = _proposed_draft(call)
        if draft is None or any(_same_plan(draft, other) for other in pending):
            continue
        stored_later = (_stored_draft(later) for later in calls[index + 1:])
        if not any(stored is not None and _same_plan(draft, stored) for stored in stored_later):
            pending.append(draft)
    return tuple(pending)


def _same_plan(first: EventDraft, second: EventDraft) -> bool:
    """Same type, days and amount; the wording of the description may differ between propose and save."""
    return ((first.event_type, first.date_from, first.date_to, first.amount_eur)
            == (second.event_type, second.date_from, second.date_to, second.amount_eur))


def _proposed_draft(call: McpCall) -> Optional[EventDraft]:
    result = call.result() if call.name == PROPOSE_TOOL else None
    if result is None or result.get("valid") is not True:
        return None
    return _draft(result.get("proposal"))


def _stored_draft(call: McpCall) -> Optional[EventDraft]:
    """The plan a save call stored, or found already stored."""
    result = call.result() if call.name == SAVE_TOOL else None
    if result is None or not (result.get("saved") is True or result.get("already_saved") is True):
        return None
    return _draft(result.get("event"))


def _draft(document: object) -> Optional[EventDraft]:
    try:
        parsed = ChatEventDraftOut.model_validate(document)
    except ValidationError as exc:
        logger.error("The MCP server returned a planned event the backend cannot read: %s", exc)
        return None
    return EventDraft(event_type=parsed.event_type, date_from=parsed.date_from, date_to=parsed.date_to,
                      amount_eur=parsed.amount_eur, description=parsed.description)
