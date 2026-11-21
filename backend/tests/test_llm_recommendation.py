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
    recommendation_schema,
    shortfalls_quoted_as_positive,
    ungrounded_amounts,
    validate_recommendation,
)
from llm_routes import get_llm_client, get_recommendation_cache

MAY_10 = DashboardQuery(date(2025, 5, 10), 60, Decimal("3000"))
DEC_31 = DashboardQuery(date(2025, 12, 31), 60, Decimal("3000"))
DOSSIER = {
    "today": "2025-05-10", "forecast_horizon_days": 60, "balance_today": 3205.69, "lowest_balance": -15728.42,
    "lowest_balance_date": "2025-07-01", "headline": "In about 7 weeks you will be about €16,000 short.",
    "fixed_costs_before_lowest_point": [
        {"label": "Staff salaries", "sum_of_all_payments_until_lowest_point_eur": 10800.0, "can_be_postponed": False},
        {"label": "Transfer to Zurich (Luigi)", "sum_of_all_payments_until_lowest_point_eur": 8666.68,
         "can_be_postponed": True}],
    "options": [
        {"id": "A", "result": "GREEN", "lowest_balance_with_this_option": -15728.42, "interest_cost_eur": 67.6,
         "credit_line_eur": 20000.0},
        {"id": "B", "result": "RED", "lowest_balance_with_this_option": -7061.74, "interest_cost_eur": 18.28,
         "credit_line_eur": None}],
}


def answer(option_id="E", **overrides) -> str:
    document = {"explanation": "The pizzeria is closed in June while the fixed costs keep running.",
                "recommended_option_id": option_id,
                "rationale": "It is the cheapest safe option."}
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
    assert parsed.recommended_option_id == "A" and parsed.rationale == "It is the cheapest safe option."


@pytest.mark.parametrize("text, reason", [
    ("Option A looks best.", "llm_invalid_json"),
    (json.dumps({"headline": "x"}), "llm_invalid_json"),
    (answer("A", explanation=""), "llm_invalid_json"),
    (answer("A", rationale=["none"]), "llm_invalid_json"),
    (answer("Z"), "llm_unknown_option"),
    (answer(None), "llm_unknown_option"),
    (answer("A", rationale="It costs only €4,321."), "llm_ungrounded_numbers"),
    (answer("A", explanation="The lowest balance will be €15,728.42 on July 1st."), "llm_sign_dropped"),
    (answer("A", explanation="Salaries (€10,800) and the transfer to Zurich (Luigi) (€8,667), which cannot be "
                             "postponed, are due."), "llm_contradicts_engine"),
    (answer("B", rationale="Option B keeps the balance above zero."), "llm_contradicts_engine"),
    (answer("B", rationale="It keeps the balance in the green zone (-€7,061.74)."), "llm_contradicts_engine"),
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
    nothing_to_choose = response(answer(None))
    assert validate_recommendation(nothing_to_choose, [], DOSSIER).recommended_option_id is None
    with pytest.raises(InvalidRecommendationError):
        validate_recommendation(response(answer("A")), [], DOSSIER)


def test_true_statements_about_postponing_and_outcomes_are_accepted():
    text = answer("A", explanation="Staff salaries of €10,800 cannot be postponed. The transfer to Zurich can be "
                                   "postponed.",
                  rationale="A €20,000 credit line keeps you safe although the balance falls to -€15,728.42.")
    assert validate_recommendation(response(text), ["A", "B"], DOSSIER).recommended_option_id == "A"


def test_schema_only_allows_offered_option_ids_and_the_texts_code_can_check():
    schema = recommendation_schema(["A", "E"])
    assert schema["properties"]["recommended_option_id"] == {"type": "string", "enum": ["A", "E"]}
    assert schema["required"] == ["explanation", "recommended_option_id", "rationale"]
    assert recommendation_schema([])["properties"]["recommended_option_id"] == {"type": "null"}


# --- grounding ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The lowest balance is -€15,728.42 and today you have €3,205.69.",
    "You will be about €16,000 short; the balance drops to €15,728 below zero.",
    "A €20,000 credit line costs 67.60 EUR in interest, about 68 euros.",
    "A 20k EUR credit line; in 7 weeks, on 1 July 2025, with 2 payments postponed at 9% interest.",
])
def test_amounts_from_the_dossier_are_grounded(text):
    assert ungrounded_amounts(text, DOSSIER) == [] and shortfalls_quoted_as_positive(text, DOSSIER) == []


@pytest.mark.parametrize("text, invented", [
    ("It costs €4,321 and saves 950 EUR.", ["€4,321", "950 EUR"]),
    ("You will be €2,000 short.", ["€2,000"]),                    # not the year 2025 rounded
    ("The plan costs €2,025.", ["€2,025"]),                       # dates are not money
    ("The cost is €60, then €10, €7 and €1.", ["€60", "€10", "€7", "€1"]),   # horizon days, day and month digits
    ("You will be 22,100 short.", ["22,100"]),                    # no currency marker, still an amount
    ("That is 22.1 thousand.", ["22.1"]),
    ("It costs $9,999.", ["$9,999"]),
    ("Today you have €3,000.", ["€3,000"]),                       # 3,205.69 rounded too coarsely
])
def test_invented_amounts_are_reported(text, invented):
    assert ungrounded_amounts(text, DOSSIER) == invented


@pytest.mark.parametrize("text, dropped", [
    ("You will have €15,728.42 left over.", ["€15,728.42"]),
    ("The lowest balance will be €15,728 on July 1st.", ["€15,728"]),
    ("The lowest balance is -€15,728.42.", []),
    ("A shortfall of about €16,000 is coming.", []),
    ("You will be €15,728.42 below zero.", []),
    ("Today you have €3,205.69.", []),
])
def test_a_negative_balance_must_be_quoted_as_negative(text, dropped):
    assert shortfalls_quoted_as_positive(text, DOSSIER) == dropped


# --- service: happy path, cache, fallbacks -----------------------------------------------------------------

def test_llm_writes_explanation_choice_and_rationale_only(red_dashboard):
    option_ids = [option.id for option in red_dashboard.options]
    result, client = recommend(red_dashboard, response(answer("A")))
    assert (result.source, result.fallback_reason, result.recommended_option_id) == ("llm", None, "A")
    engine_texts = service.build_fallback_recommendation(red_dashboard)
    assert result.explanation == "The pizzeria is closed in June while the fixed costs keep running."
    # the headline and the per-option comments always come from the engine, never from the model
    assert result.headline == red_dashboard.headline and result.option_comments == engine_texts.option_comments
    assert list(result.option_comments) == option_ids
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
    text = answer(None, rationale="No action is needed; consider a short-term deposit.")
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
