"""REST contract of the CFO advisors (GET /advice, POST /advice/recommendation, POST /advice/decisions) with
FastAPI's TestClient, the CSV-backed in-memory repository and a scripted language model."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from advice_routes import get_advice_cache
from advisors.llm_service import NO_RECOMMENDATIONS_HEADLINE, SUMMARY_SCHEMA_NAME, AdviceCache
from app import create_app
from deps import get_repository
from llm import LlmTimeoutError
from fakes import expense
from llm_routes import get_llm_client
from test_advisors_llm import FX, TAX, RoutingLlmClient, agent_answer, reworded, summary_answer, tip

MAY_10 = {"as_of_date": "2025-05-10", "horizon_days": 60, "buffer_eur": 3000}
OCT_15 = {"as_of_date": "2025-10-15", "horizon_days": 60, "buffer_eur": 3000}
NOV_20 = {"as_of_date": "2025-11-20", "horizon_days": 60, "buffer_eur": 3000}
DEC_31 = {"as_of_date": "2025-12-31", "horizon_days": 60, "buffer_eur": 3000}

PROPOSAL_KEYS = {"id", "agent", "action_type", "title", "description", "amount_eur", "date_from", "date_to",
                 "interest_eur", "risk_removed_eur", "effect", "assumptions", "simulated_actions",
                 "new_min_balance_eur", "new_min_balance_date", "new_status", "forecast", "fingerprint"}


def api_client(repo, llm=None) -> TestClient:
    """The application on the in-memory repository; without ``llm`` any model call fails the test."""
    application = create_app()
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_llm_client] = lambda: llm or RoutingLlmClient()
    application.dependency_overrides[get_advice_cache] = AdviceCache
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture()
def client(repo):
    return api_client(repo)


def advice(client, params) -> dict:
    response = client.get("/advice", params=params)
    assert response.status_code == 200
    return response.json()


def shown(body: dict) -> dict:
    return {proposal["id"]: proposal for proposal in body["proposals"]}


def suppressed(body: dict) -> list[tuple[str, str]]:
    return [(candidate["id"], candidate["reason_code"]) for candidate in body["suppressed"]]


def decide(client, params, proposal_ids, status="approved", **extra):
    return client.post("/advice/decisions", json={**params, "proposal_ids": proposal_ids, "status": status, **extra})


# --- GET /advice -------------------------------------------------------------------------------------------

def test_advice_defaults_and_shape(client):
    response = client.get("/advice")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"as_of_date", "horizon_days", "buffer_eur", "status", "liquidity_first", "surplus_eur",
                         "proposals", "suppressed"}
    assert (body["as_of_date"], body["horizon_days"], body["buffer_eur"]) == ("2025-05-10", 60, 3000.0)
    assert all(set(proposal) == PROPOSAL_KEYS for proposal in body["proposals"])
    assert all(set(candidate) == {"id", "agent", "action_type", "reason_code", "reason"}
               for candidate in body["suppressed"])


def test_red_day_shows_only_the_tax_warning(client):
    body = advice(client, MAY_10)
    assert (body["status"], body["liquidity_first"], body["surplus_eur"]) == ("RED", True, 0.0)
    [warning] = body["proposals"]
    assert (warning["id"], warning["agent"], warning["action_type"]) == ("T2", "tax", "advice")
    assert warning["title"] == "Plan for an extra income tax bill"
    assert "about €3,793" in warning["description"] and "once the balance recovers" in warning["description"]
    assert (warning["amount_eur"], warning["date_from"], warning["date_to"]) == (None, None, None)
    assert (warning["new_min_balance_eur"], warning["new_min_balance_date"], warning["new_status"]) == (
        -325.55, "2025-07-01", "RED")
    assert suppressed(body) == [("T1", "liquidity_first"), ("F1", "liquidity_first"), ("F2", "liquidity_first")]


def test_mid_october_offers_a_partial_tax_reserve_and_the_rate_lock(client):
    body = advice(client, OCT_15)
    assert (body["status"], body["liquidity_first"], body["surplus_eur"]) == ("GREEN", False, 9000.0)
    proposals = shown(body)
    assert list(proposals) == ["T1", "T2", "F2"]

    reserve = proposals["T1"]
    assert {key: reserve[key] for key in PROPOSAL_KEYS - {"description", "forecast", "assumptions", "fingerprint"}} == {
        "id": "T1", "agent": "tax", "action_type": "tax_reserve", "title": "Tax reserve deposit of €9,000",
        "amount_eur": 9000.0, "date_from": "2025-10-16", "date_to": "2025-11-16", "interest_eur": 15.29,
        "risk_removed_eur": None,
        "effect": ("€9,000 leaves the account on 16 October and comes back on 16 November with €15 interest. "
                   "Lowest projected balance: €4,534 on 16 October."),
        "simulated_actions": ["Tax reserve: 1-month term deposit of €9,000 with Commerzbank opened on 16 October, "
                              "maturing on 16 November (simulated)."],
        "new_min_balance_eur": 4534.46, "new_min_balance_date": "2025-10-16", "new_status": "GREEN"}
    assert "This covers €9,000 of the €9,803 still needed" in reserve["description"]
    assert reserve["assumptions"][0] == "Interest at 2% p.a. (assumed rate, not a bank quote)."
    assert len(reserve["forecast"]) == 60
    assert reserve["forecast"][0] == {"date": "2025-10-16", "balance_eur": 4534.46}

    lock = proposals["F2"]
    assert (lock["agent"], lock["action_type"], lock["amount_eur"], lock["date_from"], lock["date_to"]) == (
        "fx", "fx_forward", None, None, "2025-12-15")
    assert (lock["risk_removed_eur"], lock["interest_eur"], lock["new_min_balance_eur"]) == (100.0, None, 13534.46)
    assert proposals["T2"]["title"] == "Ask for higher income tax prepayments"
    assert suppressed(body) == [("F1", "over_budget"), ("I1", "below_threshold")]


def test_year_end_offers_the_tax_reserve_and_an_investment_deposit_but_no_fx(client):
    body = advice(client, DEC_31)
    assert (body["status"], body["liquidity_first"], body["surplus_eur"]) == ("GREEN", False, 27000.0)
    proposals = shown(body)
    assert list(proposals) == ["T1", "T2", "I1"]
    assert all(proposal["agent"] != "fx" for proposal in body["proposals"] + body["suppressed"])
    reserve, deposit = proposals["T1"], proposals["I1"]
    assert (reserve["amount_eur"], reserve["date_from"], reserve["date_to"], reserve["interest_eur"]) == (
        14849.65, "2026-01-01", "2026-02-01", 25.22)
    assert (deposit["agent"], deposit["action_type"]) == ("investment", "term_deposit")
    assert (deposit["amount_eur"], deposit["date_from"], deposit["date_to"], deposit["interest_eur"]) == (
        12000.0, "2026-01-01", "2026-07-01", 119.01)
    assert (reserve["new_min_balance_eur"], deposit["new_min_balance_eur"]) == (15581.16, 18430.81)
    assert suppressed(body) == [("I1", "single_deposit"), ("I1", "single_deposit")]


def test_november_offers_the_chf_purchase_once_it_fits_the_budget(client):
    purchase = shown(advice(client, NOV_20))["F1"]
    assert (purchase["action_type"], purchase["amount_eur"], purchase["date_from"], purchase["date_to"]) == (
        "fx_prebuy", 4350.0, "2025-11-21", "2025-12-15")
    assert (purchase["risk_removed_eur"], purchase["new_min_balance_eur"]) == (50.0, 16710.43)


@pytest.mark.parametrize("horizon_days", [30, 90])
def test_year_end_proposals_do_not_depend_on_the_horizon(client, horizon_days):
    proposals = shown(advice(client, {**DEC_31, "horizon_days": horizon_days}))
    assert {proposal_id: proposal["amount_eur"] for proposal_id, proposal in proposals.items()} == {
        "T1": 14849.65, "T2": None, "I1": 12000.0}
    assert len(proposals["I1"]["forecast"]) == horizon_days


def test_reading_advice_stores_nothing(client, repo):
    assert advice(client, DEC_31) == advice(client, DEC_31)
    assert repo.events == [] and repo.decisions == []


@pytest.mark.parametrize("params, code", [
    ({"horizon_days": 45}, "validation_error"),
    ({"buffer_eur": -1}, "validation_error"),
    ({"as_of_date": "15.10.2025"}, "validation_error"),
    ({"as_of_date": "2026-03-01"}, "as_of_date_out_of_range"),
])
def test_advice_rejects_invalid_parameters(client, params, code):
    response = client.get("/advice", params=params)
    assert response.status_code == 422 and response.json()["error"]["code"] == code


# --- POST /advice/decisions --------------------------------------------------------------------------------

def test_approving_several_proposals_at_once(client, repo):
    response = decide(client, DEC_31, ["I1", "T1", "T2"], explanations={"T1": "Keeps the tax money safe."})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"decisions", "execution_messages"}
    decisions = body["decisions"]
    assert [decision["option_id"] for decision in decisions] == ["T1", "T2", "I1"]  # the order they are shown in
    assert all((decision["status"], decision["execution"], decision["as_of_date"]) == (
        "approved", "SIMULATED", "2025-12-31") for decision in decisions)
    assert [decision["llm_explanation"] for decision in decisions] == ["Keeps the tax money safe.", None, None]
    assert decisions[0]["option_title"] == "Tax reserve deposit of €14,850"
    assert body["execution_messages"] == [
        "Tax reserve: 1-month term deposit of €14,850 with Commerzbank opened on 1 January, maturing on 1 February "
        "(simulated).",
        "Note saved: ask the tax advisor to have the income tax prepayments raised (simulated).",
        "Investment: 6-month term deposit of €12,000 with Commerzbank opened on 1 January, maturing on 1 July "
        "(simulated).",
    ]
    assert body["execution_messages"] == [message for decision in decisions
                                          for message in decision["execution_messages"]]
    # every decision records the outcome of all approved proposals together
    assert all(decision["snapshot"] == {
        "status_before": "GREEN", "min_balance_before_eur": 30430.81, "min_balance_date_before": "2026-01-01",
        "status_after": "GREEN", "min_balance_after_eur": 3581.16, "credit_limit_after_eur": None}
        for decision in decisions)

    assert [(event.event_type, event.category, float(event.amount_eur), event.source, event.decision_id)
            for event in repo.events] == [
        ("term_deposit", "tax_reserve", 14849.65, "agent", decisions[0]["id"]),
        ("term_deposit", "investment", 12000.0, "agent", decisions[2]["id"])]
    history = client.get("/decisions").json()["decisions"]
    assert [decision["option_id"] for decision in history] == ["I1", "T2", "T1"]  # newest first
    assert history[::-1] == decisions


def test_approved_proposals_are_gone_and_part_of_the_baseline(client):
    before = client.get("/dashboard", params=DEC_31).json()
    assert before["events"] == [] and [insight["amount_eur"] for insight in before["insights"]] == [27000.0]
    assert decide(client, DEC_31, ["T1", "T2", "I1"]).status_code == 201

    body = advice(client, DEC_31)
    assert (body["status"], body["surplus_eur"], body["proposals"]) == ("GREEN", 0.0, [])
    assert suppressed(body) == [("T2", "already_decided")]
    after = client.get("/dashboard", params=DEC_31).json()
    assert (after["risk"]["status"], after["risk"]["min_balance_eur"], after["risk"]["min_balance_date"]) == (
        "GREEN", 3581.16, "2026-01-01")
    assert [(event["event_type"], event["category"], event["amount_eur"], event["date_from"], event["date_to"],
             event["source"]) for event in after["events"]] == [
        ("term_deposit", "tax_reserve", 14849.65, "2026-01-01", "2026-02-01", "agent"),
        ("term_deposit", "investment", 12000.0, "2026-01-01", "2026-07-01", "agent")]
    assert after["insights"] == [] and after["options"] == []
    assert after["current_balance_eur"] == before["current_balance_eur"]


@pytest.mark.parametrize("params", [OCT_15, NOV_20, DEC_31])
def test_approving_everything_on_offer_keeps_the_light_green(client, params):
    offered = advice(client, params)
    response = decide(client, params, [proposal["id"] for proposal in offered["proposals"]])
    assert response.status_code == 201
    risk = client.get("/dashboard", params=params).json()["risk"]
    assert risk["status"] == "GREEN" and risk["min_balance_eur"] >= params["buffer_eur"]
    assert all(decision["snapshot"]["min_balance_after_eur"] == risk["min_balance_eur"]
               for decision in response.json()["decisions"])


def test_approving_one_proposal_leaves_the_others_on_offer(client):
    response = decide(client, DEC_31, ["T1"])
    assert response.json()["decisions"][0]["snapshot"]["min_balance_after_eur"] == 15581.16
    body = advice(client, DEC_31)
    assert body["surplus_eur"] == 12000.0
    assert {proposal_id: proposal["amount_eur"] for proposal_id, proposal in shown(body).items()} == {
        "T2": None, "I1": 12000.0}


def test_decisions_without_events_are_recorded_too(client, repo):
    response = decide(client, OCT_15, ["T2", "F2"])
    assert response.status_code == 201
    assert response.json()["execution_messages"] == [
        "Note saved: ask the tax advisor to have the income tax prepayments raised (simulated).",
        "Forward contract request sent to Commerzbank: 8,333.34 CHF at 1.046 EUR/CHF (simulated)."]
    assert repo.events == []
    assert all(decision["snapshot"]["min_balance_after_eur"] == decision["snapshot"]["min_balance_before_eur"]
               for decision in response.json()["decisions"])
    body = advice(client, OCT_15)
    assert list(shown(body)) == ["T1"]  # the rate is locked: the FX agent has nothing more to say
    assert suppressed(body) == [("T2", "already_decided"), ("I1", "below_threshold")]


def test_rejecting_proposals_changes_no_plan_and_hides_them(client, repo):
    response = decide(client, DEC_31, ["T1", "T2"], status="rejected", explanations={"T2": "Not now."})
    assert response.status_code == 201
    body = response.json()
    assert body["execution_messages"] == []
    assert [(decision["option_id"], decision["status"], decision["execution_messages"], decision["llm_explanation"])
            for decision in body["decisions"]] == [("T1", "rejected", [], None), ("T2", "rejected", [], "Not now.")]
    # a rejection records what the proposal alone would have done
    assert [decision["snapshot"]["min_balance_after_eur"] for decision in body["decisions"]] == [15581.16, 30430.81]
    assert repo.events == []
    after = advice(client, DEC_31)
    assert list(shown(after)) == ["I1"]
    assert suppressed(after)[:2] == [("T1", "rejected"), ("T2", "rejected")]
    again = decide(client, DEC_31, ["T1"], status="rejected")
    assert again.status_code == 409 and len(repo.decisions) == 2


def test_a_proposal_that_is_not_on_offer_is_a_conflict_and_nothing_is_stored(client, repo):
    response = decide(client, DEC_31, ["T1", "F1"])  # T1 is on offer, the CHF purchase is not
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "proposal_not_available",
                                         "message": "Not currently offered for 2025-12-31: F1."}}
    assert repo.events == [] and repo.decisions == []
    assert client.get("/decisions").json() == {"decisions": []}

    suppressed_by_liquidity = decide(client, MAY_10, ["T1"])
    assert suppressed_by_liquidity.status_code == 409 and repo.decisions == []


def test_a_proposal_that_changed_since_it_was_shown_is_a_conflict_and_nothing_is_stored(client, repo):
    """An id is a slot, not a proposal: the owner confirms the €12,000 deposit on screen, and a new plan (from
    another tab or the chat) shrinks the deposit behind the same id before the approval arrives."""
    on_screen = shown(advice(client, DEC_31))
    assert on_screen["I1"]["amount_eur"] == 12000.0
    seen = {proposal_id: on_screen[proposal_id]["fingerprint"] for proposal_id in ("T1", "I1")}
    repo.add_event(expense(date(2026, 1, 10), "8000"))
    assert shown(advice(client, DEC_31))["I1"]["amount_eur"] == 11000.0

    stale = decide(client, DEC_31, ["T1", "I1"], fingerprints=seen,
                   explanations={"I1": "Put €12,000 on deposit."})
    assert stale.status_code == 409
    assert stale.json() == {"error": {"code": "proposal_not_available",
                                      "message": "Changed since it was shown, for 2025-12-31: I1."}}
    assert len(repo.events) == 1 and repo.decisions == []  # only the new plan; no deposit, no decision

    refreshed = shown(advice(client, DEC_31))
    current = {proposal_id: refreshed[proposal_id]["fingerprint"] for proposal_id in ("T1", "I1")}
    assert current["T1"] == seen["T1"] and current["I1"] != seen["I1"]
    assert decide(client, DEC_31, ["T1", "I1"], fingerprints=current).status_code == 201
    assert [decision.option_title for decision in repo.decisions][-1] == "Investment deposit of €11,000"


def test_fingerprints_of_unchanged_proposals_are_accepted_for_every_kind_of_proposal(client, repo):
    on_screen = shown(advice(client, OCT_15))
    assert list(on_screen) == ["T1", "T2", "F2"]
    seen = {proposal_id: proposal["fingerprint"] for proposal_id, proposal in on_screen.items()}
    assert decide(client, OCT_15, list(seen), fingerprints=seen).status_code == 201
    assert len(repo.decisions) == 3


def test_a_proposal_cannot_be_approved_twice(client, repo):
    assert decide(client, DEC_31, ["I1"]).status_code == 201
    again = decide(client, DEC_31, ["I1"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "proposal_not_available"
    assert len(repo.events) == 1 and len(repo.decisions) == 1


@pytest.mark.parametrize("body", [
    {"proposal_ids": []},
    {"proposal_ids": ["T1", "T1"]},
    {"proposal_ids": ["T1", "T2", "F1", "F2", "I1", "T3"]},
    {"proposal_ids": ["A"]},
    {"proposal_ids": ["X1"]},
    {"proposal_ids": ["T10"]},
    {"proposal_ids": "T1"},
    {},
    {"proposal_ids": ["T1"], "status": "maybe"},
    {"proposal_ids": ["T1"], "explanations": {"T2": "for a proposal that was not chosen"}},
    {"proposal_ids": ["T1"], "explanations": {"T1": "x" * 10001}},
    {"proposal_ids": ["T1"], "fingerprints": {"T2": "of a proposal that was not chosen"}},
    {"proposal_ids": ["T1"], "fingerprints": {"T1": ""}},
    {"proposal_ids": ["T1"], "fingerprints": {"T1": "f" * 65}},
    {"proposal_ids": ["T1"], "amount_eur": 99999},
    {"proposal_ids": ["T1"], "horizon_days": 45},
])
def test_decision_request_validation(client, repo, body):
    response = client.post("/advice/decisions", json={**DEC_31, "status": "approved", **body})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    assert repo.events == [] and repo.decisions == []


def test_decision_outside_the_data_range_is_rejected(client):
    response = decide(client, {**DEC_31, "as_of_date": "2026-03-01"}, ["T1"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "as_of_date_out_of_range"


# --- POST /advice/recommendation ---------------------------------------------------------------------------

def test_recommendation_mixes_model_texts_and_fallbacks_per_agent(repo):
    llm = RoutingLlmClient(**{
        TAX: agent_answer(reworded("T1", title="Park €9,000 for the tax office"),
                          reworded("T2", recommended=False), tips=(tip(),)),
        FX: agent_answer(reworded("F2", explanation="It saves you €4,321.")),  # an invented amount
        SUMMARY_SCHEMA_NAME: summary_answer("Two things to sort out", "Set aside €9,000 for tax and lock the rate."),
    })
    response = api_client(repo, llm).post("/advice/recommendation", json=OCT_15)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"summary", "agents", "proposals"}
    assert body["summary"] == {"source": "llm", "fallback_reason": None, "headline": "Two things to sort out",
                               "text": "Set aside €9,000 for tax and lock the rate."}
    assert body["agents"] == [
        {"agent": "tax", "source": "llm", "fallback_reason": None,
         "tips": [{"title": "Ask your tax advisor", "text": "Ask whether the prepayments can be raised."}]},
        {"agent": "fx", "source": "fallback", "fallback_reason": "llm_ungrounded_numbers", "tips": []}]
    code = shown(advice(api_client(repo), OCT_15))
    assert body["proposals"] == {
        "T1": {"title": "Park €9,000 for the tax office", "explanation": "Model explanation for T1.",
               "recommended": True},
        "T2": {"title": "Model title T2", "explanation": "Model explanation for T2.", "recommended": False},
        "F2": {"title": code["F2"]["title"], "explanation": code["F2"]["description"], "recommended": True}}
    assert llm.called == {TAX, FX, SUMMARY_SCHEMA_NAME}  # 9,000 of surplus: the investment agent has no facts


def test_recommendation_is_200_with_code_texts_when_the_model_is_down(repo):
    down = LlmTimeoutError("no answer in time")
    llm = RoutingLlmClient(**{TAX: down, FX: down, SUMMARY_SCHEMA_NAME: down})
    body = api_client(repo, llm).post("/advice/recommendation", json=OCT_15).json()
    assert (body["summary"]["source"], body["summary"]["fallback_reason"]) == ("fallback", "llm_timeout")
    assert body["summary"]["headline"] == "3 recommendations for you today"
    assert [(agent["agent"], agent["source"], agent["fallback_reason"], agent["tips"])
            for agent in body["agents"]] == [("tax", "fallback", "llm_timeout", []),
                                             ("fx", "fallback", "llm_timeout", [])]
    code = shown(advice(api_client(repo), OCT_15))
    assert body["proposals"] == {
        proposal_id: {"title": proposal["title"], "explanation": proposal["description"], "recommended": True}
        for proposal_id, proposal in code.items()}


def test_recommendation_answers_are_cached_between_requests(repo):
    llm = RoutingLlmClient(**{TAX: agent_answer(reworded("T1"), reworded("T2")), FX: agent_answer(reworded("F2")),
                              SUMMARY_SCHEMA_NAME: summary_answer("Two things to sort out", "Both are safe.")})
    cache = AdviceCache()
    http = api_client(repo, llm)
    http.app.dependency_overrides[get_advice_cache] = lambda: cache
    first = http.post("/advice/recommendation", json=OCT_15).json()
    assert http.post("/advice/recommendation", json=OCT_15).json() == first
    assert llm.call_count == 3


def test_recommendation_without_proposals_asks_no_summary(repo):
    http = api_client(repo, RoutingLlmClient(**{TAX: agent_answer(tips=(tip(),))}))
    assert decide(http, DEC_31, ["T1", "T2", "I1"]).status_code == 201
    body = http.post("/advice/recommendation", json=DEC_31).json()
    assert body["summary"] == {"source": "fallback", "fallback_reason": None,
                               "headline": NO_RECOMMENDATIONS_HEADLINE,
                               "text": "Your advisors found nothing that needs your decision today."}
    assert [(agent["agent"], agent["source"], len(agent["tips"])) for agent in body["agents"]] == [("tax", "llm", 1)]
    assert body["proposals"] == {}


def test_recommendation_validates_parameters(client):
    assert client.post("/advice/recommendation", json={"horizon_days": 45}).json()["error"]["code"] == \
        "validation_error"
    out_of_range = client.post("/advice/recommendation", json={"as_of_date": "2030-01-01"})
    assert (out_of_range.status_code, out_of_range.json()["error"]["code"]) == (422, "as_of_date_out_of_range")
