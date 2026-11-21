"""POST /chat: the scenario assistant (ROZWIAZANIE.md §8.1 and §11).

The model turns the owner's words into planned events through the MCP tools and explains the situation the
backend computed. The backend keeps no chat state: ``conversation_id`` is the id of the latest OGX response,
and OGX stores the history. Writing a plan takes two turns by construction: ``save_planned_event`` is only
offered to the model when the previous turn ended with a checked proposal the owner has seen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from pydantic import ValidationError

import service
from config import LLM_CHAT_MAX_OUTPUT_TOKENS, LLM_COMMENT_MAX_OUTPUT_TOKENS
from domain import Dashboard, DashboardQuery, EventDraft
from errors import AssistantUnavailableError
from llm import (
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
    update_comment_input,
)
from repository import Repository
from schemas import ChatEventDraftOut, EventOut

logger = logging.getLogger(__name__)

PROPOSE_TOOL = "propose_planned_event"
SAVE_TOOL = "save_planned_event"
# Read tools offered in chat: saved plans and the regular fixed payments. Two kinds are left out on purpose.
# The KPI snapshot carries its own 30-day forecast that ignores planned events and contradicts the situation
# computed by the backend. The transaction query tools need filter values the model invents (it answered
# "no rent was paid" after filtering by a transaction type that does not exist), and a wrong fact is worse
# than no answer.
READ_TOOLS = ("list_planned_events", "get_fixed_obligations")

_UNAVAILABLE_MESSAGE = "The assistant is currently unavailable. Your plans and the forecast are not affected."


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


class ChatService:
    def __init__(self, client: LlmClient, mcp_server_url: str):
        self._client = client
        self._mcp_server_url = mcp_server_url

    def handle(self, repository: Repository, query: DashboardQuery, message: str,
               conversation_id: Optional[str]) -> ChatTurn:
        dashboard = service.build_dashboard(repository, query)
        try:
            previous = self._previous_response(conversation_id)
            response = self._ask(dashboard, message, previous)
        except LlmError as exc:
            logger.error("Chat failed (%s): %s", exc.reason, exc)
            raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        for call in response.mcp_calls:
            logger.info("Chat tool call %s %s ok=%s", call.name, dict(call.arguments), tool_call_ok(call))
        saved = saved_events(response.mcp_calls)
        final = self._comment_on_update(repository, query, response) if saved else response
        if not final.text:
            logger.error("Chat failed: the model returned no text (response %s).", final.id)
            raise AssistantUnavailableError(_UNAVAILABLE_MESSAGE)
        return ChatTurn(
            conversation_id=final.id,
            reply=final.text,
            pending_events=pending_drafts(response.mcp_calls),
            saved_events=saved,
            tool_calls=tuple(ToolCall(call.name, tool_call_ok(call)) for call in response.mcp_calls),
        )

    def _previous_response(self, conversation_id: Optional[str]) -> Optional[LlmResponse]:
        """The turn to continue from. An id OGX does not know (expired, restarted) simply starts a new thread."""
        if conversation_id is None:
            return None
        try:
            return self._client.get_response(conversation_id)
        except LlmResponseNotFoundError:
            logger.info("Conversation %s is unknown to OGX; starting a new thread.", conversation_id)
            return None

    def _ask(self, dashboard: Dashboard, message: str, previous: Optional[LlmResponse]) -> LlmResponse:
        pending = pending_drafts(previous.mcp_calls) if previous else ()
        request = self._request(dashboard, message, save_allowed=bool(pending),
                                previous_response_id=previous.id if previous else None)
        try:
            return self._client.respond(request)
        except LlmContextOverflowError:
            if previous is None:
                raise
            logger.info("Conversation %s no longer fits the context window; continuing in a new thread.", previous.id)
            carried = carried_context_input(message, previous.text, pending)
            return self._client.respond(self._request(dashboard, carried, save_allowed=bool(pending),
                                                      previous_response_id=None))

    def _request(self, dashboard: Dashboard, user_input: str, save_allowed: bool,
                 previous_response_id: Optional[str]) -> LlmRequest:
        write_tools = (PROPOSE_TOOL, SAVE_TOOL) if save_allowed else (PROPOSE_TOOL,)
        return LlmRequest(
            instructions=chat_instructions(dashboard, save_allowed),
            input=user_input,
            max_output_tokens=LLM_CHAT_MAX_OUTPUT_TOKENS,
            previous_response_id=previous_response_id,
            mcp=McpAccess(self._mcp_server_url, dashboard.as_of_date, write_tools + READ_TOOLS),
        )

    def _comment_on_update(self, repository: Repository, query: DashboardQuery, response: LlmResponse) -> LlmResponse:
        """After a plan was saved: let the model tell the owner what the recalculated forecast shows (§8.1 step 7).

        The plan is already stored, so a failure here must not fail the turn; the model's own reply is kept.
        """
        updated = service.build_dashboard(repository, query)
        try:
            comment = self._client.respond(LlmRequest(
                instructions=UPDATE_COMMENT_INSTRUCTIONS,
                input=update_comment_input(updated),
                max_output_tokens=LLM_COMMENT_MAX_OUTPUT_TOKENS,
                previous_response_id=response.id,
            ))
        except LlmError as exc:
            logger.warning("No forecast comment after saving (%s): %s", exc.reason, exc)
            return response
        return comment if comment.text else response


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
