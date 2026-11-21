"""OGX client: request payload, typed parsing of Responses API documents, error mapping. No network."""
import json
from datetime import date

import httpx
import pytest

from llm import (
    Deadline,
    GUARDRAIL_REPLY,
    Guardrails,
    JsonSchemaFormat,
    LlmContextOverflowError,
    LlmGuardrailBlockedError,
    LlmHttpError,
    LlmProtocolError,
    LlmRequest,
    LlmResponseNotFoundError,
    LlmTimeoutError,
    LlmUnreachableError,
    McpAccess,
    McpCall,
    OgxClient,
    build_payload,
    parse_response,
)

MCP_URL = "http://amalfi-mcp.test/mcp"

# Shape captured from the real OGX deployment (tool list shortened).
OGX_DOCUMENT = {
    "id": "resp_0dc827c6",
    "object": "response",
    "status": "completed",
    "error": None,
    "output": [
        {"id": "mcp_list_1", "type": "mcp_list_tools", "server_label": "amalfi",
         "tools": [{"name": "propose_planned_event", "input_schema": {"type": "object"}}]},
        {"id": "fc_1", "type": "mcp_call", "name": "propose_planned_event", "server_label": "amalfi", "error": None,
         "arguments": "{\"event_type\": \"closure\", \"date_from\": \"2023-06-01\", \"date_to\": \"2023-06-21\"}",
         "output": "{\n  \"valid\": false,\n  \"error\": \"date_from 2023-06-01 is before today (2025-05-10).\"\n}"},
        {"id": "fc_2", "type": "mcp_call", "name": "propose_planned_event", "server_label": "amalfi", "error": None,
         "arguments": "{\"event_type\": \"closure\", \"date_from\": \"2025-06-01\", \"date_to\": \"2025-06-21\"}",
         "output": "{\n  \"valid\": true,\n  \"proposal\": {\"event_type\": \"closure\"}\n}"},
        {"id": "fc_3", "type": "mcp_call", "name": "save_planned_event", "server_label": "amalfi",
         "arguments": "{\"event_type\": \"credit_line\"}", "error": "Error (code 1): None", "output": None},
        {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Should I include it in the forecast?", "annotations": []}]},
    ],
}


def client_answering(handler) -> OgxClient:
    http = httpx.Client(base_url="http://ogx.test", transport=httpx.MockTransport(handler))
    return OgxClient(http, "vllm-cfo/cfo-llm")


def simple_request(**overrides) -> LlmRequest:
    return LlmRequest(**{"instructions": "Be brief.", "input": "Hello", "max_output_tokens": 300, **overrides})


def test_payload_is_deterministic_and_minimal_without_tools():
    payload = build_payload("vllm-cfo/cfo-llm", simple_request())
    assert payload == {"model": "vllm-cfo/cfo-llm", "temperature": 0, "max_output_tokens": 300,
                       "instructions": "Be brief.", "input": "Hello"}


def test_payload_passes_the_mcp_server_with_as_of_header_and_restricted_tools():
    access = McpAccess(MCP_URL, date(2025, 5, 10), ("propose_planned_event", "list_planned_events"))
    payload = build_payload("m", simple_request(mcp=access, previous_response_id="resp_prev"))
    assert payload["previous_response_id"] == "resp_prev"
    assert payload["tools"] == [{
        "type": "mcp", "server_label": "amalfi", "server_url": MCP_URL,
        "headers": {"X-As-Of-Date": "2025-05-10"}, "require_approval": "never",
        "allowed_tools": ["propose_planned_event", "list_planned_events"],
    }]


def test_payload_names_the_plans_confirmed_for_removal_in_a_header():
    access = McpAccess(MCP_URL, date(2025, 5, 10), ("remove_planned_event",), removable_event_ids=(7, 12))
    (tool,) = build_payload("m", simple_request(mcp=access))["tools"]
    assert tool["headers"] == {"X-As-Of-Date": "2025-05-10", "X-Removable-Event-Ids": "7,12"}


def test_payload_requests_guided_json_when_a_schema_is_given():
    schema = {"type": "object", "properties": {"headline": {"type": "string"}}}
    payload = build_payload("m", simple_request(json_schema=JsonSchemaFormat("recommendation", schema)))
    assert payload["text"] == {"format": {"type": "json_schema", "name": "recommendation", "strict": True,
                                          "schema": schema}}


def test_parse_response_extracts_text_and_mcp_calls_in_order():
    parsed = parse_response(OGX_DOCUMENT)
    assert parsed.id == "resp_0dc827c6" and parsed.truncated is False
    assert parsed.text == "Should I include it in the forecast?"
    assert [call.name for call in parsed.mcp_calls] == ["propose_planned_event", "propose_planned_event",
                                                        "save_planned_event"]
    rejected, accepted, failed = parsed.mcp_calls
    assert rejected.arguments["date_from"] == "2023-06-01"
    assert rejected.result() == {"valid": False, "error": "date_from 2023-06-01 is before today (2025-05-10)."}
    assert accepted.result()["valid"] is True
    assert failed.error == "Error (code 1): None" and failed.result() is None


def test_parse_response_flags_truncated_output():
    document = {"id": "resp_2", "status": "incomplete", "incomplete_details": {"reason": "length"},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "In the heart of"}]}]}
    assert parse_response(document).truncated is True


@pytest.mark.parametrize("body", [None, [], {"id": "resp_1"}, {"output": []}, {"id": 5, "output": []}])
def test_parse_response_rejects_documents_that_are_not_responses(body):
    with pytest.raises(LlmProtocolError):
        parse_response(body)


def test_parse_response_reports_a_failed_response():
    with pytest.raises(LlmHttpError):
        parse_response({"id": "resp_1", "output": [], "error": {"code": "server_error", "message": "boom"}})


def test_mcp_call_result_is_none_for_non_json_or_non_object_output():
    assert McpCall("t", {}, "plain text", None).result() is None
    assert McpCall("t", {}, "[1, 2]", None).result() is None
    assert McpCall("t", {}, None, None).result() is None


def test_respond_posts_to_the_responses_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json=OGX_DOCUMENT)

    parsed = client_answering(handler).respond(simple_request())
    assert seen["url"] == "http://ogx.test/v1/responses"
    assert seen["body"]["model"] == "vllm-cfo/cfo-llm" and seen["body"]["temperature"] == 0
    assert parsed.id == "resp_0dc827c6"


def test_get_response_reads_a_stored_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/v1/responses/resp_0dc827c6"
        return httpx.Response(200, json=OGX_DOCUMENT)

    assert len(client_answering(handler).get_response("resp_0dc827c6").mcp_calls) == 3


def test_get_response_cannot_be_steered_to_another_ogx_path():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, json={"detail": "not found"})

    for hostile in ("../models", "../../v1/models?x=1"):
        with pytest.raises(LlmResponseNotFoundError):
            client_answering(handler).get_response(hostile)
    assert seen == ["/v1/responses/..%2Fmodels", "/v1/responses/..%2F..%2Fv1%2Fmodels%3Fx%3D1"]


def test_a_call_can_override_the_read_timeout():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions["timeout"])
        return httpx.Response(200, json=OGX_DOCUMENT)

    http = httpx.Client(base_url="http://ogx.test", transport=httpx.MockTransport(handler),
                        timeout=httpx.Timeout(90.0, connect=5.0))
    OgxClient(http, "m").respond(simple_request(), timeout_seconds=12.5)
    assert (seen["read"], seen["connect"]) == (12.5, 5.0)
    OgxClient(http, "m").respond(simple_request())
    assert (seen["read"], seen["connect"]) == (90.0, 5.0)


def test_deadline_hands_out_what_is_left_of_the_budget():
    now = [100.0]
    deadline = Deadline(150.0, clock=lambda: now[0])
    assert deadline.timeout(90.0) == 90.0
    now[0] += 100
    assert deadline.remaining() == 50.0 and deadline.timeout(90.0) == 50.0
    now[0] += 50
    with pytest.raises(LlmTimeoutError):
        deadline.timeout(90.0)


def test_context_overflow_is_recognised():
    detail = ("Invalid value for 'model': vllm-cfo/cfo-llm. This model's maximum context length is 16384 tokens. "
              "However, you requested 4096 output tokens and your prompt contains at least 12289 input tokens.")
    client = client_answering(lambda request: httpx.Response(400, json={"detail": detail}))
    with pytest.raises(LlmContextOverflowError):
        client.respond(simple_request())


def test_unknown_previous_response_is_recognised():
    client = client_answering(lambda request: httpx.Response(404, json={"detail": "Response 'resp_x' not found."}))
    with pytest.raises(LlmResponseNotFoundError):
        client.respond(simple_request(previous_response_id="resp_x"))


def test_other_http_errors_and_bad_bodies_are_reported():
    with pytest.raises(LlmHttpError):
        client_answering(lambda request: httpx.Response(500, text="boom")).respond(simple_request())
    with pytest.raises(LlmProtocolError):
        client_answering(lambda request: httpx.Response(200, text="<html>")).respond(simple_request())


def test_timeouts_and_connection_failures_are_distinguished():
    def timing_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refusing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmTimeoutError) as timeout:
        client_answering(timing_out).respond(simple_request())
    with pytest.raises(LlmUnreachableError) as unreachable:
        client_answering(refusing).respond(simple_request())
    assert (timeout.value.reason, unreachable.value.reason) == ("llm_timeout", "llm_unreachable")


def guarded(ogx_handler, nemo_handler) -> OgxClient:
    ogx = httpx.Client(base_url="http://ogx.test", transport=httpx.MockTransport(ogx_handler))
    nemo = httpx.Client(base_url="http://nemo.test", transport=httpx.MockTransport(nemo_handler))
    return OgxClient(ogx, "vllm-cfo/cfo-llm", Guardrails(nemo))


def test_a_blocked_message_never_reaches_ogx():
    def ogx(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OGX must not be called")

    def nemo(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/guardrail/checks"
        assert json.loads(request.content)["messages"] == [{"role": "user", "content": "My email is a@b.com"}]
        return httpx.Response(200, json={"status": "blocked"})

    with pytest.raises(LlmGuardrailBlockedError):
        guarded(ogx, nemo).respond(simple_request(input="My email is a@b.com"))


def test_a_blocked_reply_is_replaced_and_the_tool_calls_stay():
    checks = {"n": 0}

    def nemo(request: httpx.Request) -> httpx.Response:
        checks["n"] += 1
        status = "success" if checks["n"] == 1 else "blocked"
        return httpx.Response(200, json={"status": status})

    answer = guarded(lambda request: httpx.Response(200, json=OGX_DOCUMENT), nemo).respond(simple_request())
    assert answer.text == GUARDRAIL_REPLY
    assert [call.name for call in answer.mcp_calls] == [
        "propose_planned_event", "propose_planned_event", "save_planned_event"]


def test_allowed_text_is_passed_through_unchanged():
    def nemo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success"})

    answer = guarded(lambda request: httpx.Response(200, json=OGX_DOCUMENT), nemo).respond(simple_request())
    assert answer.text == "Should I include it in the forecast?"


def test_an_unreachable_guardrail_stops_the_call():
    def nemo(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmUnreachableError):
        guarded(lambda request: httpx.Response(200, json=OGX_DOCUMENT), nemo).respond(simple_request())
