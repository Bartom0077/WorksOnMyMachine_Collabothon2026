"""POST /chat: the scenario assistant (ROZWIAZANIE.md §8.1 and §11).

The model turns the owner's words into planned events through the MCP tools and explains the situation the
backend computed. OGX stores the chat history; ``conversation_id`` is the id of the latest OGX response.

Writing a plan takes two turns, and code enforces it:
- ``save_planned_event`` is only offered while checked proposals the owner has seen are waiting (``PendingPlans``,
  kept per conversation in this process);
- after the model's turn every saved plan is compared with those proposals. A plan the owner never saw in that
  exact form (other dates, another amount) is removed again and shown for confirmation instead;
- a reply that claims a save although nothing was stored is replaced by a statement written by code.

Removing a saved plan follows the same two turns (``propose_planned_event_removal`` -> the owner's yes ->
``remove_planned_event``), enforced more strictly, because a removal cannot be taken back:
- the removal instructions, the plan ids and ``propose_planned_event_removal`` are given to the model only in a
  turn about removing (``_removal_context``): a removal waits for the owner's answer, or a plan from the chat is
  saved and the owner asks for a removal. With them always present the small model handled new plans worse.
  The request is recognised by its wording (``_REMOVAL_REQUEST``) or, for any other wording, by the model itself:
  the usual prompt tells it to answer a removal request with ``REMOVAL_REQUEST_SIGNAL``, and the backend then
  asks again with the removal instructions. While no chat plan is saved, none of this is in the prompt;
- ``remove_planned_event`` is offered only in the turn directly after the one that showed the removal to the owner
  (``PendingPlans.removals``, exactly what the response returned as ``pending_removals``). A "no", a side question
  or a new topic ends it: the plan has to be proposed again;
- the request to the MCP server then carries exactly those ids (``X-Removable-Event-Ids``), and the server refuses
  to delete any other plan, so the model cannot remove something the owner was not shown;
- a reply that claims a removal although nothing was removed is replaced by a statement written by code;
- if the model fails in a turn that could write, the database is checked before the owner is told anything.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
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
from domain import Dashboard, DashboardQuery, DecisionRecord, EventDraft, EventSource, PlannedEvent
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
    REMOVAL_REQUEST_SIGNAL,
    RemovalContext,
    carried_context_input,
    chat_instructions,
    describe_draft,
    update_comment_input,
    update_comment_instructions,
)
from repository import Repository
from schemas import ChatEventDraftOut, EventOut

logger = logging.getLogger(__name__)

PROPOSE_TOOL = "propose_planned_event"
SAVE_TOOL = "save_planned_event"
PROPOSE_REMOVAL_TOOL = "propose_planned_event_removal"
REMOVE_TOOL = "remove_planned_event"
LIST_TOOL = "list_planned_events"
# Read tools offered in chat: saved plans and the regular fixed payments. Two kinds are left out on purpose.
# The KPI snapshot carries its own 30-day forecast that ignores planned events and contradicts the situation
# computed by the backend. The transaction query tools need filter values the model invents (it answered
# "no rent was paid" after filtering by a transaction type that does not exist), and a wrong fact is worse
# than no answer.
READ_TOOLS = (LIST_TOOL, "get_fixed_obligations")

_UNAVAILABLE_MESSAGE = "The assistant is currently unavailable. Your plans and the forecast are not affected."
_RECENT_DECISIONS_IN_CHAT = 3
# A checked proposal to save a plan stays confirmable through this many turns that neither propose nor change
# anything (a side question between the summary and the owner's "yes"). A wrong save is undone by code
# (``_checked_outcome``); a removal cannot be undone, so a proposed removal gets no such turn.
_MAX_IDLE_TURNS = 1
# Phrases with which the model reports a completed save ("has been included", "I have saved", "is now saved").
_SAVE_CLAIM = re.compile(
    r"\b(?:has|have)\s+(?:now\s+|just\s+)?been\s+(?:saved|included|added|recorded)\b"
    r"|\bI(?:'ve|\s+have)?\s+(?:now\s+|just\s+)?(?:saved|included|added|recorded)\b"
    r"|\b(?:is|are)\s+now\s+(?:saved|included|added|recorded|part\s+of)\b",
    re.IGNORECASE)
# Words with which the owner usually asks to remove a saved plan: such a turn gets the removal job right away.
# Other wordings reach it through the model's ``REMOVAL_REQUEST_SIGNAL``, so this list can stay narrow.
_REMOVAL_REQUEST = re.compile(
    r"\b(?:remov\w*|delet\w*|cancel\w*|eras\w*|scrap\w*|discard\w*|forget|undo|get\s+rid)\b", re.IGNORECASE)
# "Did the refund get removed?", "Is the oven deleted?": a question about the state of the plans, not a request.
_STATUS_QUESTION = re.compile(r"^\s*(?:did|do|does|is|are|was|were|has|have|had)\b.*\?\s*$",
                              re.IGNORECASE | re.DOTALL)
_GONE = r"(?:removed|deleted|cancell?ed|erased|scrapped|dropped|gone|taken\s+out|taken\s+off)"
_ADVERBS = r"(?:(?:now|just|already|also|been|\w+ly)\s+)*"
# Phrases with which the model reports a completed removal ("has been removed", "I've deleted", "was taken out",
# "Removed.", "is no longer in your plans"). Checked per sentence; see ``_claims_a_removal``.
_REMOVAL_CLAIM = re.compile(
    rf"\b(?:has|have|had|is|are|was|were|got|gets)\s+{_ADVERBS}{_GONE}\b"
    rf"|\bI(?:'ve|\s+have)?\s+{_ADVERBS}(?:went\s+ahead\s+and\s+)?"
    rf"(?:removed|deleted|cancell?ed|erased|scrapped|dropped|took\s+(?:\w+\s+){{1,5}}?(?:out|off)\b"
    rf"|taken\s+(?:\w+\s+){{1,5}}?(?:out|off)\b)"
    rf"|^\W*(?:(?:done|ok|okay|sure|all\s+right)\W+)?(?:successfully\s+)?(?:removed|deleted|cancell?ed)\b"
    r"|\b(?:is|are)\s+no\s+longer\s+(?:in|part\s+of|included\s+in|on)\s+(?:your|the)\s+"
    r"(?:plans?|forecast|list)\b",
    re.IGNORECASE)
# A sentence with one of these does not report a removal ("nothing was removed", "has not been removed", "the
# closure stays", "the removal was cancelled", "it may have been removed earlier").
_NO_REMOVAL_MARKER = re.compile(
    r"\b(?:not|nothing|never|cannot|no\s+(?:plans?|such|one)|stays?|still|keep|kept|remains?|removal|may|might|if"
    r"|whether)\b|n't\b|\?\s*$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


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
    pending_removals: tuple[EventOut, ...]
    removed_events: tuple[EventOut, ...]
    tool_calls: tuple[ToolCall, ...]

    @property
    def events_changed(self) -> bool:
        return bool(self.saved_events or self.removed_events)


@dataclass(frozen=True)
class PendingPlans:
    """What the owner of one conversation has been asked to confirm and has not yet answered. The two kinds are
    independent of each other:

    - ``drafts``: checked proposals to save. They survive ``_MAX_IDLE_TURNS`` turns that do not show them again.
    - ``removals``: saved plans proposed for removal. Always exactly what the latest response showed the owner
      (``pending_removals``); they never survive a turn that neither proposes nor removes one of them.
    """

    drafts: tuple[EventDraft, ...] = ()
    drafts_idle_turns: int = 0
    removals: tuple[EventOut, ...] = ()
    # The latest turn was about removing and ended with a question instead of a proposal ("which one?", or the
    # small model asked "shall I remove it?" without the tool call): the owner's next message is the answer, so
    # it gets the removal job too. This only decides what the model is told; nothing becomes removable by it.
    removal_choice_asked: bool = False

    @property
    def shown_drafts(self) -> tuple[EventDraft, ...]:
        """The proposals to save that the latest reply put in front of the owner."""
        return self.drafts if self.drafts_idle_turns == 0 else ()

    def drafts_asked_again(self) -> PendingPlans:
        """The same proposals to save, shown to the owner once more in this turn."""
        return replace(self, drafts_idle_turns=0)


class PendingPlansStore:
    """Small thread-safe LRU: conversation id -> proposals waiting for the owner's yes.

    Process-local on purpose. If an entry is missing (restart, another replica) saving and removing are simply
    not offered and the assistant asks again, which is the safe direction.
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

    def discard(self, conversation_id: str) -> None:
        """The conversation moved on from this turn: what it showed can no longer be confirmed from it."""
        with self._lock:
            self._entries.pop(conversation_id, None)


@dataclass(frozen=True)
class _Outcome:
    """What one model turn really changed, after the saves were checked against the confirmed proposals."""

    saved: tuple[EventOut, ...]
    reverted: tuple[EventDraft, ...]  # saved by the model without the owner's yes to these values; removed again
    proposed: tuple[EventDraft, ...]
    still_waiting: tuple[EventDraft, ...]  # earlier proposals neither saved nor replaced in this turn
    removed: tuple[EventOut, ...]  # deleted in this turn, or confirmed for removal and found already gone
    removals_proposed: tuple[EventOut, ...]
    removals_still_waiting: tuple[EventOut, ...]  # earlier removal proposals not carried out in this turn

    @property
    def plans_changed(self) -> bool:
        return bool(self.saved or self.removed)


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
        saved_plans = tuple(repository.list_events())  # all of them; the dashboard only has those in the horizon
        chat_plans = _chat_plans(saved_plans)
        previous, waiting = None, PendingPlans()
        try:
            previous = self._previous_response(conversation_id, deadline)
            waiting = self._pending.get(previous.id) if previous else PendingPlans()
            removal = _removal_context(message, waiting, saved_plans)
            response = self._ask(dashboard, decisions, message, previous, waiting, removal, bool(chat_plans),
                                 deadline)
            if removal is None and REMOVAL_REQUEST_SIGNAL in response.text:
                logger.info("The model recognised a removal request; asking again with the removal job.")
                removal = RemovalContext((), saved_plans)
                response = self._ask(dashboard, decisions, message, previous, waiting, removal, True, deadline)
        except LlmError as exc:
            logger.error("Chat failed (%s): %s", exc.reason, exc)
            recovered = self._turn_after_failure(repository, saved_plans, previous, waiting)
            if recovered is None:
                raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
            return recovered
        for call in response.mcp_calls:
            logger.info("Chat tool call %s %s ok=%s", call.name, dict(call.arguments), tool_call_ok(call))
        outcome = self._checked_outcome(repository, response, waiting)
        for event in outcome.removed:
            logger.info("Conversation %s: planned event %s was removed on the owner's confirmation: %s",
                        response.id, event.id, event.model_dump_json())
        final = self._comment_on_update(repository, query, response, outcome, deadline)
        if not final.text and not outcome.plans_changed:
            logger.error("Chat failed: the model returned no text (response %s).", final.id)
            raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE)
        plans = _plans_after_turn(outcome, waiting)
        if removal is not None and not (outcome.removed or outcome.removals_proposed) and "?" in final.text:
            plans = replace(plans, removal_choice_asked=True)
        reply = final.text or _plans_changed_reply(outcome.saved, outcome.removed, interrupted=False)
        if outcome.reverted:
            reply = _reverted_reply(outcome)
        elif not outcome.saved and _claims_a_save_that_did_not_happen(final.text, response):
            logger.warning("Replacing a reply that claims a save although nothing was stored: %r", final.text)
            plans = plans.drafts_asked_again()
            reply = _nothing_saved_reply(plans.drafts)
        elif not outcome.removed and _removal_in_play(message, removal, response) and _claims_a_removal(final.text):
            logger.warning("Replacing a reply that claims a removal although nothing was removed: %r", final.text)
            # The code-written reply asks about the waiting plans once more, so they stay confirmable.
            plans = replace(plans, removals=plans.removals or outcome.removals_still_waiting,
                            removal_choice_asked=True)
            reply = _nothing_removed_reply(plans.removals, chat_plans)
        elif REMOVAL_REQUEST_SIGNAL in reply:  # the model's note to the backend must never reach the owner
            logger.warning("The model repeated the removal signal instead of answering: %r", final.text)
            reply = _which_plan_reply(chat_plans)
            plans = replace(plans, removal_choice_asked=True)
        if outcome.removed and outcome.removals_still_waiting:  # whatever the model said about "all of them"
            reply = f"{reply} Still in your plans, not removed: {_listed(outcome.removals_still_waiting)}."
        if previous is not None:
            self._pending.discard(previous.id)
        self._pending.put(final.id, plans)
        return ChatTurn(
            conversation_id=final.id,
            reply=reply,
            pending_events=plans.shown_drafts,
            saved_events=outcome.saved,
            pending_removals=plans.removals,
            removed_events=outcome.removed,
            tool_calls=tuple(ToolCall(call.name, tool_call_ok(call)) for call in response.mcp_calls),
        )

    def _turn_after_failure(self, repository: Repository, plans_before: Sequence[PlannedEvent],
                            previous: Optional[LlmResponse], waiting: PendingPlans) -> Optional[ChatTurn]:
        """The model call failed (a timeout, an OGX error). If a write tool was offered, OGX may have run it
        before the failure, so the database decides what the owner is told: a plan that was waiting for removal
        and is gone, or a waiting proposal that is stored now, is reported as changed. ``None``: nothing changed.
        """
        if previous is None or not (waiting.drafts or waiting.removals):
            return None
        known_ids = {event.id for event in plans_before}
        events_now = repository.list_events()
        ids_now = {event.id for event in events_now}
        removed = tuple(event for event in waiting.removals if event.id not in ids_now)
        saved = tuple(EventOut.model_validate(event) for event in events_now
                      if event.id not in known_ids and event.source is EventSource.CHAT
                      and any(_same_plan(event, shown) for shown in waiting.drafts))
        if not (saved or removed):
            return None
        for event in (*saved, *removed):
            logger.warning("Conversation %s: the model call failed after planned event %s was %s: %s", previous.id,
                           event.id, "saved" if event in saved else "removed", event.model_dump_json())
        self._pending.put(previous.id, PendingPlans())  # whatever else was waiting has to be proposed again
        return ChatTurn(conversation_id=previous.id, reply=_plans_changed_reply(saved, removed, interrupted=True),
                        pending_events=(), saved_events=saved, pending_removals=(), removed_events=removed,
                        tool_calls=())

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
             previous: Optional[LlmResponse], waiting: PendingPlans, removal: Optional[RemovalContext],
             removal_on_request: bool, deadline: Deadline) -> LlmResponse:
        instructions = chat_instructions(dashboard, waiting.drafts, removal, decisions, removal_on_request)
        request = self._request(dashboard, instructions, message, waiting, removal is not None,
                                previous.id if previous else None)
        try:
            return self._client.respond(request, deadline.timeout(LLM_TIMEOUT_SECONDS))
        except LlmContextOverflowError:
            if previous is None:
                raise
            logger.info("Conversation %s no longer fits the context window; continuing in a new thread.", previous.id)
            carried = carried_context_input(message, previous.text, waiting.drafts, waiting.removals)
            restarted = self._request(dashboard, instructions, carried, waiting, removal is not None, None)
            return self._client.respond(restarted, deadline.timeout(LLM_TIMEOUT_SECONDS))

    def _request(self, dashboard: Dashboard, instructions: str, user_input: str, waiting: PendingPlans,
                 removal_possible: bool, previous_response_id: Optional[str]) -> LlmRequest:
        """The executing tools are offered only for what the owner has been shown: saving while proposals wait,
        removing while removal proposals wait, and then only for exactly those plans. The tool that proposes a
        removal is offered only in a turn about removing (every tool schema costs context)."""
        allowed_tools = (PROPOSE_TOOL, *((SAVE_TOOL,) if waiting.drafts else ()),
                         *((PROPOSE_REMOVAL_TOOL,) if removal_possible else ()),
                         *((REMOVE_TOOL,) if waiting.removals else ()), *READ_TOOLS)
        return LlmRequest(
            instructions=instructions,
            input=user_input,
            max_output_tokens=LLM_CHAT_MAX_OUTPUT_TOKENS,
            previous_response_id=previous_response_id,
            mcp=McpAccess(self._mcp_server_url, dashboard.as_of_date, allowed_tools,
                          removable_event_ids=tuple(event.id for event in waiting.removals)),
        )

    def _checked_outcome(self, repository: Repository, response: LlmResponse, waiting: PendingPlans) -> _Outcome:
        """Keep only saves of plans the owner confirmed; remove every other row the model wrote."""
        confirmed, reverted = [], []
        for event in saved_events(response.mcp_calls):
            draft = _as_draft(event)
            if any(_same_plan(draft, shown) for shown in waiting.drafts):
                confirmed.append(event)
                continue
            logger.warning("Removing planned event %s: the model saved %s, which the owner had not confirmed.",
                           event.id, describe_draft(draft))
            repository.delete_event(event.id)
            reverted.append(draft)
        stored = [draft for draft in map(_stored_draft, response.mcp_calls) if draft is not None]
        removed = _removed_in_turn(response.mcp_calls, waiting.removals)
        removed_ids = {event.id for event in removed}
        for event in removed:
            if all(event.id != shown.id for shown in waiting.removals):  # the MCP server refuses this; never expected
                logger.error("Planned event %s was removed although the owner had not been shown it: %s",
                             event.id, event.model_dump_json())
        return _Outcome(
            saved=tuple(confirmed),
            reverted=tuple(reverted),
            proposed=pending_drafts(response.mcp_calls),
            still_waiting=tuple(draft for draft in waiting.drafts
                                if not any(_same_plan(draft, done) for done in stored)),
            removed=removed,
            removals_proposed=tuple(event for event in pending_removals(response.mcp_calls)
                                    if event.id not in removed_ids),
            removals_still_waiting=tuple(event for event in waiting.removals if event.id not in removed_ids),
        )

    def _comment_on_update(self, repository: Repository, query: DashboardQuery, response: LlmResponse,
                           outcome: _Outcome, deadline: Deadline) -> LlmResponse:
        """After a plan was saved or removed: let the model tell the owner what the recalculated forecast shows
        (§8.1 step 7). A turn that changed no plan keeps the model's own reply.

        The change is already stored, so a failure here must not fail the turn; the model's own reply is kept.
        The comment is skipped when the turn's time budget cannot cover it.
        """
        if not outcome.plans_changed:
            return response
        if deadline.remaining() < LLM_COMMENT_TIMEOUT_SECONDS:
            logger.warning("No forecast comment after the change: the time budget of the turn is nearly used up.")
            return response
        updated = service.build_dashboard(repository, query)
        try:
            comment = self._client.respond(LlmRequest(
                instructions=update_comment_instructions(bool(outcome.saved), bool(outcome.removed)),
                input=update_comment_input(updated),
                max_output_tokens=LLM_COMMENT_MAX_OUTPUT_TOKENS,
                previous_response_id=response.id,
            ), LLM_COMMENT_TIMEOUT_SECONDS)
        except LlmError as exc:
            logger.warning("No forecast comment after the change (%s): %s", exc.reason, exc)
            return response
        return comment if comment.text else response


def _plans_after_turn(outcome: _Outcome, waiting: PendingPlans) -> PendingPlans:
    """What the owner can say yes to in the next turn. Each kind follows its own rule; a proposal of one kind
    does not replace a waiting proposal of the other kind."""
    removals = outcome.removals_proposed or (outcome.removals_still_waiting if outcome.removed else ())
    shown_now = _without_duplicates((*outcome.reverted, *outcome.proposed))
    if shown_now:  # the newest summary replaces older ones
        return PendingPlans(shown_now, 0, removals)
    if outcome.saved:  # a partial yes: the rest keeps waiting
        return PendingPlans(outcome.still_waiting, 0, removals)
    if not outcome.still_waiting or waiting.drafts_idle_turns >= _MAX_IDLE_TURNS:
        return PendingPlans(removals=removals)
    return PendingPlans(outcome.still_waiting, waiting.drafts_idle_turns + 1, removals)


def _chat_plans(saved_plans: Sequence[PlannedEvent]) -> tuple[PlannedEvent, ...]:
    """The saved plans the owner told the assistant about: the only ones that can be removed in the chat."""
    return tuple(event for event in saved_plans if event.source is EventSource.CHAT)


def _removed_in_turn(calls: Sequence[McpCall], shown: Sequence[EventOut]) -> tuple[EventOut, ...]:
    """Plans deleted in this turn, plus plans the owner was shown for removal that ``remove_planned_event``
    found already gone (the request was repeated after a dropped connection, or the owner removed the plan in
    "My plans" in the meantime): for the owner both mean "it is no longer in the plans"."""
    removed = {event.id: event for event in removed_events(calls)}
    already_gone = _already_removed_ids(calls)
    for event in shown:
        if event.id in already_gone:
            removed.setdefault(event.id, event)
    return tuple(removed.values())


def _already_removed_ids(calls: Sequence[McpCall]) -> frozenset[int]:
    ids = set()
    for call in calls:
        result = call.result() if call.name == REMOVE_TOOL else None
        if result is not None and result.get("already_removed") is True and isinstance(result.get("event_id"), int):
            ids.add(result["event_id"])
    return frozenset(ids)


def _reverted_reply(outcome: _Outcome) -> str:
    """Written by code: what was kept, what was removed, what was not saved and why, and the question to
    confirm it."""
    kept = "".join(f"Included in the forecast: {describe_draft(event)}. " for event in outcome.saved)
    removed = "".join(f"Removed from your plans: {describe_draft(event)}. " for event in outcome.removed)
    return (f"{kept}{removed}I have not saved the following yet, because it is not exactly what you confirmed. "
            f"{_confirmation_question(outcome.reverted)}")


def _plans_changed_reply(saved: Sequence[EventOut], removed: Sequence[EventOut], interrupted: bool) -> str:
    """Written by code when the model gave no usable answer although the plans did change."""
    start = "I could not finish my answer, but this change went through. " if interrupted else "Done. "
    changes = "".join(f"Included in the forecast: {describe_draft(event)}. " for event in saved)
    changes += "".join(f"Removed from your plans: {describe_draft(event)}. " for event in removed)
    return f"{start}{changes}The forecast has been recalculated."


def _removal_context(message: str, waiting: PendingPlans, saved_plans: Sequence[PlannedEvent]
                     ) -> Optional[RemovalContext]:
    """The removal job for this turn, if it is about removing: the owner answers a removal card, or asks in the
    usual words to remove something while a plan from the chat is saved."""
    asked = _asks_for_removal(message) or waiting.removal_choice_asked
    if waiting.removals or (asked and _chat_plans(saved_plans)):
        return RemovalContext(waiting.removals, saved_plans)
    return None


def _asks_for_removal(message: str) -> bool:
    return bool(_REMOVAL_REQUEST.search(message)) and not _STATUS_QUESTION.match(message)


def _removal_in_play(message: str, removal: Optional[RemovalContext], response: LlmResponse) -> bool:
    """Was removing a plan the subject of this turn? Only then a reply that reports a removal is checked; an
    explanation such as "the catering order has been cancelled" in any other turn is left alone."""
    return (removal is not None or _asks_for_removal(message)
            or any(call.name in (PROPOSE_REMOVAL_TOOL, REMOVE_TOOL) for call in response.mcp_calls))


def _claims_a_removal(reply: str) -> bool:
    """Some sentence of the reply reports a completed removal and does not negate it."""
    return any(_REMOVAL_CLAIM.search(sentence) and not _NO_REMOVAL_MARKER.search(sentence)
               for sentence in _SENTENCE_END.split(reply.strip()))


def _claims_a_save_that_did_not_happen(reply: str, response: LlmResponse) -> bool:
    return bool(_SAVE_CLAIM.search(reply)) and not _consulted_saved_plans(response)


def _nothing_saved_reply(confirmable: Sequence[EventDraft]) -> str:
    if confirmable:
        return f"To be clear: nothing has been saved yet. {_confirmation_question(confirmable)}"
    return ("To be clear: nothing new was saved in this step. To add a plan, tell me what it is, on which day and "
            "how much, and I will show it to you for confirmation first.")


def _nothing_removed_reply(removable: Sequence[EventOut], chat_plans: Sequence[PlannedEvent]) -> str:
    if removable:
        return (f"To be clear: nothing has been removed yet. {_please_check(removable)} Shall I remove "
                f"{_it_or_them(removable)} from your plans?")
    return f"To be clear: no plan was removed in this step. {_which_plan_reply(chat_plans)}"


def _which_plan_reply(chat_plans: Sequence[PlannedEvent]) -> str:
    if not chat_plans:
        return "There is no plan from our chat in your plans that I could remove."
    return (f"Your plans from our chat: {_listed(chat_plans)}. To remove one, tell me which, and I will show it "
            "to you for confirmation first.")


def _confirmation_question(drafts: Sequence[EventDraft]) -> str:
    return f"{_please_check(drafts)} Shall I include {_it_or_them(drafts)} in the forecast?"


def _please_check(plans: Sequence[EventDraft | EventOut]) -> str:
    return f"Please check: {_listed(plans)}."


def _listed(plans: Sequence[EventDraft | EventOut]) -> str:
    return "; ".join(describe_draft(plan) for plan in plans)


def _it_or_them(plans: Sequence[object]) -> str:
    return "it" if len(plans) == 1 else "them"


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
    return tuple(event for call in calls if (event := _event_of(call, SAVE_TOOL, "saved")) is not None)


def removed_events(calls: Sequence[McpCall]) -> tuple[EventOut, ...]:
    """Plans deleted from planned_events in this turn."""
    return tuple(event for call in calls if (event := _event_of(call, REMOVE_TOOL, "removed")) is not None)


def pending_removals(calls: Sequence[McpCall]) -> tuple[EventOut, ...]:
    """Saved plans proposed for removal in this turn and not removed in the same turn, without duplicates."""
    gone = {event.id for event in removed_events(calls)}
    pending: dict[int, EventOut] = {}
    for call in calls:
        event = _event_of(call, PROPOSE_REMOVAL_TOOL, "valid")
        if event is not None and event.id not in gone:
            pending.setdefault(event.id, event)
    return tuple(pending.values())


def _event_of(call: McpCall, tool: str, success_flag: str) -> Optional[EventOut]:
    """The saved plan in the result of a successful call of ``tool``."""
    result = call.result() if call.name == tool else None
    if result is None or result.get(success_flag) is not True:
        return None
    try:
        return EventOut.model_validate(result.get("event"))
    except ValidationError as exc:
        logger.error("%s reported an event the backend cannot read: %s", tool, exc)
        return None


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
