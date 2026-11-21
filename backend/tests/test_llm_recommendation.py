"""POST /recommendation: validation of the model's JSON, option-id checks, grounding, cache and fallbacks."""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import service
from app import create_app
from deps import get_repository
from domain import DashboardQuery, DecisionStatus
from fakes import closure
from llm import LlmHttpError, LlmTimeoutError
from llm_fakes import FakeLlmClient, response
from llm_prompts import recommendation_dossier
from llm_recommendation import (
    InvalidRecommendationError,
    RecommendationCache,
    RecommendationService,
    Recommendation,
    dossier_key,
    extract_json_object,
    merged_option_comments,
    recommendation_schema,
    ungrounded_amounts,
    validate_recommendation,
)
from llm_routes import get_llm_client, get_recommendation_cache

MAY_10 = DashboardQuery(date(2025, 5, 10), 60, Decimal("3000"))
DEC_31 = DashboardQuery(date(2025, 12, 31), 60, Decimal("3000"))
DOSSIER = {"balance_today": 3205.69, "lowest_balance": -15728.42, "headline": "about €16,000 short",
           "options": [{"id": "A", "interest_cost_eur": 67.6, "credit_line_eur": 20000.0}]}


def answer(option_id="E", option_comments=None, **overrides) -> str:
    document = {"headline": "In July you will be short of money.",
                "explanation": "The pizzeria is closed in June while the fixed costs keep running.",
                "recommended_option_id": option_id,
                "rationale": "It is the cheapest safe option.",
                "option_comments": option_comments if option_comments is not None else {"A": "Costs more interest."}}
    return json.dumps({**document, **overrides})


@pytest.fixture()
def red_dashboard(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    return service.build_dashboard(repo, MAY_10)


def recommend(dashboard, *script, cache=None) -> tuple[Recommendation, FakeLlmClient]:
    client = FakeLlmClient(*script)
    return RecommendationService(client, cache or RecommendationCache()).recommend(dashboard), client


# --- JSON extraction ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"headline": "x"}',
    '```json\n{"headline": "x"}\n```',
    'Here is my recommendation:\n{"headline": "x"}\nLet me know if you need more.',
    'Note {not json} first, then {"headline": "x"}',
])
def test_extract_json_object_tolerates_fences_and_prose(text):
    assert extract_json_object(text) == {"headline": "x"}


@pytest.mark.parametrize("text", ["", "I recommend option E.", '["A", "B"]', '{"headline": '])
def test_extract_json_object_returns_none_without_an_object(text):
    assert extract_json_object(text) is None


# --- validation --------------------------------------------------------------------------------------------

def test_validate_accepts_a_complete_answer():
    parsed = validate_recommendation(response(answer("A")), ["A", "B"], DOSSIER)
    assert parsed.recommended_option_id == "A" and parsed.option_comments == {"A": "Costs more interest."}


@pytest.mark.parametrize("text, reason", [
    ("Option A looks best.", "llm_invalid_json"),
    (json.dumps({"headline": "x"}), "llm_invalid_json"),
    (answer("A", headline=""), "llm_invalid_json"),
    (answer("A", option_comments="none"), "llm_invalid_json"),
    (answer("Z"), "llm_unknown_option"),
    (answer(None), "llm_unknown_option"),
    (answer("A", rationale="It costs only €4,321."), "llm_ungrounded_numbers"),
])
def test_validate_rejects_bad_answers_with_a_reason(text, reason):
    with pytest.raises(InvalidRecommendationError) as error:
        validate_recommendation(response(text), ["A", "B"], DOSSIER)
    assert error.value.reason == reason


def test_validate_rejects_a_truncated_answer():
    with pytest.raises(InvalidRecommendationError) as error:
        validate_recommendation(response(answer("A"), truncated=True), ["A", "B"], DOSSIER)
    assert error.value.reason == "llm_truncated_output"


def test_validate_requires_null_when_nothing_can_be_recommended():
    nothing_to_choose = response(answer(None, option_comments={}))
    assert validate_recommendation(nothing_to_choose, [], DOSSIER).recommended_option_id is None
    with pytest.raises(InvalidRecommendationError):
        validate_recommendation(response(answer("A")), [], DOSSIER)


def test_schema_only_allows_offered_option_ids():
    schema = recommendation_schema(["A", "E"], ["A", "B", "E"])
    assert schema["properties"]["recommended_option_id"] == {"type": "string", "enum": ["A", "E"]}
    assert schema["properties"]["option_comments"]["required"] == ["A", "B", "E"]
    assert recommendation_schema([], [])["properties"]["recommended_option_id"] == {"type": "null"}


def test_merged_option_comments_cover_exactly_the_offered_options():
    merged = merged_option_comments({"A": "From the model.", "B": "", "Z": "Invented."},
                                    {"A": "From code A.", "B": "From code B."})
    assert merged == {"A": "From the model.", "B": "From code B."}


# --- grounding ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The lowest balance is -€15,728.42 and today you have €3,205.69.",
    "You will be about €16,000 short; the balance drops to €15,728.",
    "A €20,000 credit line costs 67.60 EUR in interest, about 68 euros.",
    "A 20k EUR credit line; in 7 weeks, on 1 July 2025, with 2 payments postponed.",
])
def test_amounts_from_the_dossier_are_grounded(text):
    assert ungrounded_amounts(text, DOSSIER) == []


def test_invented_amounts_are_reported():
    assert ungrounded_amounts("It costs €4,321 and saves 950 EUR.", DOSSIER) == ["€4,321", "950 EUR"]


# --- service: happy path, cache, fallbacks -----------------------------------------------------------------

def test_llm_answer_is_used_and_missing_comments_are_filled_from_code(red_dashboard):
    option_ids = [option.id for option in red_dashboard.options]
    result, client = recommend(red_dashboard, response(answer("A")))
    assert (result.source, result.fallback_reason, result.recommended_option_id) == ("llm", None, "A")
    assert result.headline == "In July you will be short of money."
    assert list(result.option_comments) == option_ids and result.option_comments["A"] == "Costs more interest."
    assert result.option_comments["B"] == service.build_fallback_recommendation(red_dashboard).option_comments["B"]
    request = client.requests[0]
    assert request.mcp is None and request.previous_response_id is None
    assert request.json_schema.schema["properties"]["recommended_option_id"]["enum"] == option_ids
    assert json.loads(request.input.removeprefix("DOSSIER:\n")) == recommendation_dossier(red_dashboard)


def test_json_wrapped_in_a_code_fence_is_accepted(red_dashboard):
    result, _ = recommend(red_dashboard, response(f"```json\n{answer('E')}\n```"))
    assert (result.source, result.recommended_option_id) == ("llm", "E")


@pytest.mark.parametrize("step, reason", [
    (LlmTimeoutError("slow"), "llm_timeout"),
    (LlmHttpError("HTTP 500"), "llm_http_error"),
    (response("I think option E is best."), "llm_invalid_json"),
    (response(answer("Z")), "llm_unknown_option"),
    (response(answer("E"), truncated=True), "llm_truncated_output"),
    (response(answer("E", explanation="You will lose €99,999.")), "llm_ungrounded_numbers"),
])
def test_any_model_problem_falls_back_to_the_code_texts(red_dashboard, step, reason):
    result, _ = recommend(red_dashboard, step)
    fallback = service.build_fallback_recommendation(red_dashboard)
    assert (result.source, result.fallback_reason) == ("fallback", reason)
    assert result.recommended_option_id == red_dashboard.code_recommended_option_id == "E"
    assert (result.headline, result.explanation, result.rationale) == (
        fallback.headline, fallback.explanation, fallback.rationale)
    assert result.option_comments == fallback.option_comments


def test_a_rejected_option_cannot_be_recommended(repo, red_dashboard):
    service.record_decision(repo, MAY_10, "E", DecisionStatus.REJECTED, None)
    dashboard = service.build_dashboard(repo, MAY_10)
    result, client = recommend(dashboard, response(answer("E")))
    assert "E" not in client.requests[0].json_schema.schema["properties"]["recommended_option_id"]["enum"]
    assert (result.source, result.fallback_reason) == ("fallback", "llm_unknown_option")
    assert result.recommended_option_id == dashboard.code_recommended_option_id != "E"


def test_green_situation_gets_an_explanation_without_an_option(repo):
    dashboard = service.build_dashboard(repo, DEC_31)
    assert dashboard.options == () and dashboard.insights
    text = answer(None, option_comments={}, rationale="No action is needed; consider a short-term deposit.")
    result, client = recommend(dashboard, response(text))
    assert (result.source, result.recommended_option_id, result.option_comments) == ("llm", None, {})
    dossier = json.loads(client.requests[0].input.removeprefix("DOSSIER:\n"))
    assert dossier["opportunities"][0]["amount_eur"] == float(dashboard.insights[0].amount_eur)
    assert client.requests[0].json_schema.schema["properties"]["recommended_option_id"] == {"type": "null"}


def test_identical_dossier_is_answered_from_the_cache(red_dashboard):
    cache = RecommendationCache()
    first, client = recommend(red_dashboard, response(answer("E")), cache=cache)
    second = RecommendationService(client, cache).recommend(red_dashboard)
    assert second is first and len(client.requests) == 1


def test_fallbacks_are_not_cached_and_a_changed_situation_asks_again(repo, red_dashboard):
    cache = RecommendationCache()
    client = FakeLlmClient(LlmTimeoutError("slow"), response(answer("E")), response(answer("A")))
    recommendations = RecommendationService(client, cache)
    assert recommendations.recommend(red_dashboard).source == "fallback"
    assert recommendations.recommend(red_dashboard).source == "llm"
    other_horizon = service.build_dashboard(repo, DashboardQuery(date(2025, 5, 10), 90, Decimal("3000")))
    assert recommendations.recommend(other_horizon).recommended_option_id == "A"
    assert len(client.requests) == 3


def test_cache_evicts_the_least_recently_used_entry(red_dashboard):
    cache = RecommendationCache(max_entries=2)
    entry, _ = recommend(red_dashboard, response(answer("E")))
    for key in ("a", "b"):
        cache.put(key, entry)
    assert cache.get("a") is entry
    cache.put("c", entry)
    assert cache.get("b") is None and cache.get("a") is entry and cache.get("c") is entry


def test_dossier_key_ignores_key_order_but_not_values():
    assert dossier_key({"a": 1, "b": 2}) == dossier_key({"b": 2, "a": 1})
    assert dossier_key({"a": 1}) != dossier_key({"a": 2})


# --- HTTP contract -----------------------------------------------------------------------------------------

def api_client(repo, llm: FakeLlmClient) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_llm_client] = lambda: llm
    application.dependency_overrides[get_recommendation_cache] = RecommendationCache
    return TestClient(application, raise_server_exceptions=False)


def test_recommendation_endpoint_returns_the_contract_shape(repo, red_dashboard):
    http = api_client(repo, FakeLlmClient(response(answer("E"))))
    reply = http.post("/recommendation", json={"as_of_date": "2025-05-10", "horizon_days": 60, "buffer_eur": 3000})
    assert reply.status_code == 200
    body = reply.json()
    assert set(body) == {"source", "fallback_reason", "headline", "explanation", "recommended_option_id",
                         "rationale", "option_comments"}
    assert (body["source"], body["fallback_reason"], body["recommended_option_id"]) == ("llm", None, "E")


def test_recommendation_endpoint_is_200_with_fallback_when_the_model_is_down(repo, red_dashboard):
    http = api_client(repo, FakeLlmClient(LlmTimeoutError("slow")))
    body = http.post("/recommendation", json={}).json()
    assert (body["source"], body["fallback_reason"], body["recommended_option_id"]) == ("fallback", "llm_timeout", "E")
    assert set(body["option_comments"]) == {"A", "B", "C", "E"}


def test_recommendation_endpoint_validates_parameters(repo):
    http = api_client(repo, FakeLlmClient())
    assert http.post("/recommendation", json={"horizon_days": 45}).json()["error"]["code"] == "validation_error"
    out_of_range = http.post("/recommendation", json={"as_of_date": "2030-01-01"})
    assert (out_of_range.status_code, out_of_range.json()["error"]["code"]) == (422, "as_of_date_out_of_range")
