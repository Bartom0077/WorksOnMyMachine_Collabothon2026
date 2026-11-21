"""Test doubles for the LLM layer: a scripted OGX client and builders for the MCP call items OGX returns."""
from __future__ import annotations

import json
from typing import Optional, Union

from llm import LlmError, LlmRequest, LlmResponse, LlmResponseNotFoundError, McpCall


class FakeLlmClient:
    """Answers ``respond`` from a script (responses or errors, in order) and remembers every request."""

    def __init__(self, *script: Union[LlmResponse, LlmError]):
        self._script = list(script)
        self.requests: list[LlmRequest] = []
        self.timeouts: list[Optional[float]] = []
        self.stored: dict[str, LlmResponse] = {}

    def respond(self, request: LlmRequest, timeout_seconds: Optional[float] = None) -> LlmResponse:
        self.requests.append(request)
        self.timeouts.append(timeout_seconds)
        if not self._script:
            raise AssertionError("The fake LLM client was called more often than scripted.")
        step = self._script.pop(0)
        if isinstance(step, LlmError):
            raise step
        self.stored[step.id] = step
        return step

    def get_response(self, response_id: str, timeout_seconds: Optional[float] = None) -> LlmResponse:
        if response_id not in self.stored:
            raise LlmResponseNotFoundError(f"unknown response {response_id}")
        return self.stored[response_id]


def response(text: str = "", *calls: McpCall, response_id: str = "resp_1", truncated: bool = False) -> LlmResponse:
    return LlmResponse(id=response_id, text=text, mcp_calls=tuple(calls), truncated=truncated)


def mcp_call(name: str, output: Optional[dict], arguments: Optional[dict] = None, error: Optional[str] = None
             ) -> McpCall:
    """An MCP call as OGX reports it: the tool's JSON result arrives as pretty-printed text."""
    text = None if output is None else json.dumps(output, indent=2)
    return McpCall(name=name, arguments=arguments or {}, output=text, error=error)


def proposal(event_type: str = "closure", date_from: str = "2025-06-01", date_to: str = "2025-06-21",
             amount_eur: Optional[float] = None, description: str = "Holidays") -> dict:
    return {"event_type": event_type, "date_from": date_from, "date_to": date_to, "amount_eur": amount_eur,
            "description": description}


def propose_ok(**fields) -> McpCall:
    draft = proposal(**fields)
    return mcp_call("propose_planned_event", {"valid": True, "proposal": draft,
                                              "confirmation_prompt": "Should I include it in the forecast?"})


def propose_rejected(message: str = "date_from 2025-04-01 is before today (2025-05-10).") -> McpCall:
    return mcp_call("propose_planned_event", {"valid": False, "error": message})


def saved_event(event_id: int = 7, utterance: str = "We close 1-21 June.", **fields) -> dict:
    return {**proposal(**fields), "id": event_id, "category": None, "original_utterance": utterance,
            "source": "chat", "as_of_date": "2025-05-10", "decision_id": None,
            "created_at": "2026-11-21T10:27:53.432984+00:00"}


def save_ok(event_id: int = 7, **fields) -> McpCall:
    return mcp_call("save_planned_event", {"saved": True, "event": saved_event(event_id, **fields)})


def save_duplicate(event_id: int = 7, **fields) -> McpCall:
    return mcp_call("save_planned_event", {"saved": False, "already_saved": True,
                                           "event": saved_event(event_id, **fields),
                                           "note": "This event is already in the plans; nothing was added."})


def propose_removal_ok(event_id: int = 7, **fields) -> McpCall:
    return mcp_call("propose_planned_event_removal", {
        "valid": True, "event": saved_event(event_id, **fields),
        "confirmation_prompt": "Should I remove it from your plans?"}, {"event_id": event_id})


def propose_removal_rejected(message: str) -> McpCall:
    return mcp_call("propose_planned_event_removal", {"valid": False, "error": message})


def remove_ok(event_id: int = 7, **fields) -> McpCall:
    return mcp_call("remove_planned_event", {"removed": True, "event": saved_event(event_id, **fields)},
                    {"event_id": event_id})


def remove_refused(event_id: int = 7) -> McpCall:
    return mcp_call("remove_planned_event", {
        "removed": False, "error": f"Planned event {event_id} was not removed: the owner has not confirmed its "
                                   "removal yet."}, {"event_id": event_id})


def remove_already_gone(event_id: int = 7) -> McpCall:
    return mcp_call("remove_planned_event", {
        "removed": False, "already_removed": True, "event_id": event_id,
        "note": f"Planned event {event_id} is already gone from the plans; nothing was left to remove."},
        {"event_id": event_id})
