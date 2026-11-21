"""OGX client: the only door from the backend to the language model (ROZWIAZANIE.md §11).

The backend talks to OGX's OpenAI-compatible Responses API and never to vLLM directly. The model reaches the
data through the MCP server passed as a tool (fixed queries, no SQL); the ``X-As-Of-Date`` header pins the
simulated "today" so the model cannot see data from the future. Conversation history is stored by OGX and
continued with ``previous_response_id``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

MCP_SERVER_LABEL = "amalfi"
_CONTEXT_OVERFLOW_MARKER = "maximum context length"
_ERROR_DETAIL_LOG_CHARS = 500
_CONNECTION_ATTEMPTS = 2


class LlmError(Exception):
    """The model could not produce a usable response. ``reason`` is a stable machine-readable code."""

    reason = "llm_error"


class LlmTimeoutError(LlmError):
    reason = "llm_timeout"


class LlmUnreachableError(LlmError):
    reason = "llm_unreachable"


class LlmHttpError(LlmError):
    reason = "llm_http_error"


class LlmContextOverflowError(LlmError):
    """The conversation no longer fits into the model's context window."""

    reason = "llm_context_overflow"


class LlmResponseNotFoundError(LlmError):
    """The response to continue from (``previous_response_id``) is not known to OGX."""

    reason = "llm_conversation_not_found"


class LlmProtocolError(LlmError):
    """OGX answered with a body that is not a Responses API document."""

    reason = "llm_invalid_response"


@dataclass(frozen=True)
class McpAccess:
    """The MCP server offered to the model for one request, limited to the tools that request needs.

    Every listed tool schema costs context tokens (all nine tools take about a third of the window).
    """

    server_url: str
    as_of_date: date
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class JsonSchemaFormat:
    """Constrain the answer to one JSON document (guided decoding on the model server)."""

    name: str
    schema: Mapping[str, Any]


@dataclass(frozen=True)
class LlmRequest:
    instructions: str
    input: str
    max_output_tokens: int
    previous_response_id: Optional[str] = None
    mcp: Optional[McpAccess] = None
    json_schema: Optional[JsonSchemaFormat] = None


@dataclass(frozen=True)
class McpCall:
    """One tool call the model made through OGX. ``output`` is the tool's raw text (JSON for our server)."""

    name: str
    arguments: Mapping[str, Any]
    output: Optional[str]
    error: Optional[str]

    def result(self) -> Optional[dict]:
        """The tool output as a JSON object, or None when the call failed or returned something else."""
        if self.error is not None or self.output is None:
            return None
        try:
            parsed = json.loads(self.output)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


@dataclass(frozen=True)
class LlmResponse:
    id: str
    text: str
    mcp_calls: tuple[McpCall, ...]
    truncated: bool  # the answer hit max_output_tokens


class Deadline:
    """Time budget of one unit of work that makes several model calls; each call gets what is left of it."""

    def __init__(self, budget_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._ends_at = clock() + budget_seconds

    def remaining(self) -> float:
        return self._ends_at - self._clock()

    def timeout(self, cap_seconds: float) -> float:
        """Timeout for the next call: the cap or the rest of the budget, whichever is smaller."""
        remaining = self.remaining()
        if remaining <= 0:
            raise LlmTimeoutError("The time budget for this request is used up.")
        return min(cap_seconds, remaining)


class LlmClient(Protocol):
    """``timeout_seconds`` overrides the client's default read timeout for one call."""

    def respond(self, request: LlmRequest, timeout_seconds: Optional[float] = None) -> LlmResponse: ...

    def get_response(self, response_id: str, timeout_seconds: Optional[float] = None) -> LlmResponse: ...


class OgxClient:
    """Synchronous Responses API client (the endpoints are sync and run in FastAPI's threadpool)."""

    def __init__(self, http: httpx.Client, model: str):
        self._http = http
        self._model = model

    def respond(self, request: LlmRequest, timeout_seconds: Optional[float] = None) -> LlmResponse:
        payload = build_payload(self._model, request)
        return parse_response(self._send("POST", "/v1/responses", payload, timeout_seconds))

    def get_response(self, response_id: str, timeout_seconds: Optional[float] = None) -> LlmResponse:
        # The id comes from the browser: quoted, so it can never address another OGX path or add a query string.
        path = f"/v1/responses/{quote(response_id, safe='')}"
        return parse_response(self._send("GET", path, None, timeout_seconds))

    def _send(self, method: str, path: str, payload: Optional[dict], timeout_seconds: Optional[float]) -> Any:
        response = self._request_with_retry(method, path, payload, timeout_seconds)
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise LlmProtocolError("OGX returned a body that is not JSON.") from exc

    def _request_with_retry(self, method: str, path: str, payload: Optional[dict],
                            timeout_seconds: Optional[float]) -> httpx.Response:
        """One retry for a dropped or refused connection (a stale keep-alive, an OGX restart). Timeouts are not
        retried. Repeating a request is safe: the only write the model can make, save_planned_event, is idempotent."""
        timeout = self._http.timeout if timeout_seconds is None else self._timeout(timeout_seconds)
        for attempt in range(_CONNECTION_ATTEMPTS):
            try:
                return self._http.request(method, path, json=payload, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise LlmTimeoutError(f"OGX did not answer in time: {exc!r}") from exc
            except httpx.TransportError as exc:
                if attempt + 1 == _CONNECTION_ATTEMPTS:
                    raise LlmUnreachableError(f"OGX is unreachable: {exc!r}") from exc
                logger.warning("Connection to OGX failed (%r); retrying once.", exc)
        raise AssertionError("unreachable: the loop returns or raises")

    def _timeout(self, seconds: float) -> httpx.Timeout:
        connect = self._http.timeout.connect
        return httpx.Timeout(seconds, connect=seconds if connect is None else min(seconds, connect))


def create_http_client(base_url: str, timeout_seconds: float, connect_timeout_seconds: float) -> httpx.Client:
    timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)


def build_payload(model: str, request: LlmRequest) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_output_tokens": request.max_output_tokens,
        "instructions": request.instructions,
        "input": request.input,
    }
    if request.previous_response_id is not None:
        payload["previous_response_id"] = request.previous_response_id
    if request.mcp is not None:
        payload["tools"] = [_mcp_tool(request.mcp)]
    if request.json_schema is not None:
        payload["text"] = {"format": {"type": "json_schema", "name": request.json_schema.name, "strict": True,
                                      "schema": request.json_schema.schema}}
    return payload


def parse_response(body: Any) -> LlmResponse:
    """Typed view of a Responses API document: the assistant's text and the MCP calls, in order."""
    if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not isinstance(body.get("output"), list):
        raise LlmProtocolError("OGX returned a document without 'id' and 'output'.")
    if body.get("error"):
        raise LlmHttpError(f"OGX reported a failed response: {body['error']}")
    items = [item for item in body["output"] if isinstance(item, dict)]
    texts = [text for item in items if item.get("type") == "message" for text in _message_texts(item)]
    return LlmResponse(
        id=body["id"],
        text="\n\n".join(texts).strip(),
        mcp_calls=tuple(_mcp_call(item) for item in items if item.get("type") == "mcp_call"),
        truncated=body.get("status") == "incomplete",
    )


def _mcp_tool(access: McpAccess) -> dict:
    return {
        "type": "mcp",
        "server_label": MCP_SERVER_LABEL,
        "server_url": access.server_url,
        "headers": {"X-As-Of-Date": access.as_of_date.isoformat()},
        "require_approval": "never",
        "allowed_tools": list(access.allowed_tools),
    }


def _message_texts(item: dict) -> list[str]:
    content = item.get("content")
    if not isinstance(content, list):
        return []
    return [part["text"] for part in content
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)]


def _mcp_call(item: dict) -> McpCall:
    error = item.get("error")
    output = item.get("output")
    return McpCall(
        name=str(item.get("name", "")),
        arguments=_json_object(item.get("arguments")),
        output=output if isinstance(output, str) else None,
        error=None if error is None else str(error),
    )


def _json_object(raw: Any) -> dict:
    """Tool arguments arrive as a JSON string; anything unparsable counts as "no arguments"."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _http_error(response: httpx.Response) -> LlmError:
    detail = response.text[:_ERROR_DETAIL_LOG_CHARS]
    if _CONTEXT_OVERFLOW_MARKER in response.text:
        return LlmContextOverflowError(f"OGX rejected the request (context window exceeded): {detail}")
    if response.status_code == 404:
        return LlmResponseNotFoundError(f"OGX does not know the referenced response: {detail}")
    return LlmHttpError(f"OGX answered HTTP {response.status_code}: {detail}")
