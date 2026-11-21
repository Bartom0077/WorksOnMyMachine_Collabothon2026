"""The advisors next to the liquidity agent, end to end through the REST API on the real 2025 statement:
approved proposals in later forecasts, withdrawal, the demo reset, and the existing endpoints left unchanged."""
from datetime import date

import pytest

from fakes import closure, expense
from llm import LlmTimeoutError
from llm_fakes import FakeLlmClient
from test_advisors_api import DEC_31, MAY_10, NOV_20, OCT_15, advice, api_client, decide, shown, suppressed

NOV_25 = {"as_of_date": "2025-11-25", "horizon_days": 60, "buffer_eur": 3000}
OCT_20 = {"as_of_date": "2025-10-20", "horizon_days": 60, "buffer_eur": 3000}
ADVISOR_CATEGORIES = {"term_deposit", "fx_prebuy", "investment_outflow"}


@pytest.fixture()
def client(repo):
    return api_client(repo)


def dashboard(client, params) -> dict:
    response = client.get("/dashboard", params=params)
    assert response.status_code == 200
    return response.json()


def advisor_items(body: dict) -> list[tuple[str, str, float]]:
    """(day, category, amount) of every forecast item a deposit or the CHF instalments account for."""
    return [(day["date"], item["category"], item["amount_eur"]) for day in body["forecast"]["days"]
            for item in day["items"] if item["category"] in ADVISOR_CATEGORIES]


@pytest.fixture()
def reserve_and_francs(repo):
    """20 November: the tax reserve deposit and the CHF purchase are approved."""
    assert decide(api_client(repo), NOV_20, ["T1", "F1"]).status_code == 201


# --- approved proposals in the forecast --------------------------------------------------------------------

def test_approved_deposit_and_chf_purchase_shape_the_forecast(client, reserve_and_francs):
    body = dashboard(client, NOV_20)
    assert (body["risk"]["status"], body["risk"]["min_balance_eur"], body["risk"]["min_balance_date"]) == (
        "GREEN", 3741.08, "2025-12-01")
    assert [(event["event_type"], event["category"], event["amount_eur"], event["source"])
            for event in body["events"]] == [("term_deposit", "tax_reserve", 12969.35, "agent"),
                                             ("fx_prebuy", None, 4350.0, "agent")]
    # the money leaves tomorrow, the deposit returns with interest, and the December instalment is gone
    assert advisor_items(body) == [("2025-11-21", "term_deposit", -12969.35), ("2025-11-21", "fx_prebuy", -4350.0),
                                   ("2025-12-21", "term_deposit", 12990.67)]
    assert body["options"] == [] and body["insights"] == []
    assert body["headline"] == ("You are in the clear for the next 60 days: the lowest projected balance is about "
                                "€3,700.")


def test_dashboard_on_a_later_day_keeps_the_approved_money_tied_up(client, reserve_and_francs):
    body = dashboard(client, NOV_25)
    assert body["current_balance_eur"] == 32987.74  # the booked balance never shows the simulated actions
    assert (body["risk"]["status"], body["risk"]["min_balance_eur"], body["risk"]["min_balance_date"]) == (
        "GREEN", 6475.47, "2025-12-01")
    assert advisor_items(body) == [("2025-11-26", "term_deposit", -12969.35), ("2025-11-26", "fx_prebuy", -4350.0),
                                   ("2025-12-21", "term_deposit", 12990.67)]
    assert [event["event_type"] for event in body["events"]] == ["term_deposit", "fx_prebuy"]

    later = advice(client, NOV_25)
    assert (later["status"], later["surplus_eur"]) == ("GREEN", 3000.0)
    proposals = shown(later)
    assert list(proposals) == ["T1", "T2"]  # the francs are bought: the FX agent is silent
    assert proposals["T1"]["amount_eur"] == 1161.0  # only the reserve that has built up since the deposit
    assert suppressed(later) == [("I1", "below_threshold")]


def test_reserve_grows_with_the_profit_after_an_earlier_approval(client):
    offered = advice(client, OCT_15)
    assert decide(client, OCT_15, list(shown(offered))).status_code == 201
    assert advice(client, OCT_15)["proposals"] == []
    later = advice(client, OCT_20)
    assert {proposal_id: proposal["amount_eur"] for proposal_id, proposal in shown(later).items()} == {"T1": 1806.92}
    assert suppressed(later) == [("T2", "already_decided"), ("I1", "below_threshold")]


# --- the liquidity agent with advisor plans present --------------------------------------------------------

def test_rescue_options_still_work_with_advisor_plans_in_the_forecast(client, repo, reserve_and_francs):
    repo.add_event(expense(date(2025, 12, 5), "25000"), as_of=date(2025, 11, 25))
    body = dashboard(client, NOV_25)
    assert (body["risk"]["status"], body["risk"]["min_balance_eur"], body["risk"]["min_balance_date"]) == (
        "RED", -14619.81, "2025-12-05")
    assert [(option["id"], option["new_status"]) for option in body["options"]] == [("A", "GREEN"), ("D", "RED")]
    assert body["code_recommended_option_id"] == "A"

    blocked = advice(client, NOV_25)
    assert (blocked["status"], blocked["liquidity_first"], blocked["surplus_eur"]) == ("RED", True, 0.0)
    assert list(shown(blocked)) == ["T2"] and suppressed(blocked) == [("T1", "liquidity_first")]

    approved = client.post("/decisions", json={**NOV_25, "option_id": "A", "status": "approved"})
    assert approved.status_code == 201
    assert approved.json()["execution_messages"] == ["Credit line request for €20,000 sent to Commerzbank (simulated)."]
    assert approved.json()["decision"]["snapshot"] == {
        "status_before": "RED", "min_balance_before_eur": -14619.81, "min_balance_date_before": "2025-12-05",
        "status_after": "GREEN", "min_balance_after_eur": -14619.81, "credit_limit_after_eur": 20000.0}
    covered = dashboard(client, NOV_25)
    assert (covered["risk"]["status"], covered["risk"]["credit_limit_eur"], covered["options"]) == (
        "GREEN", 20000.0, [])
    assert [event["event_type"] for event in covered["events"]] == ["term_deposit", "fx_prebuy", "credit_line",
                                                                    "expense"]
    assert [decision["option_id"] for decision in client.get("/decisions").json()["decisions"]] == ["A", "F1", "T1"]

    # GREEN, but the credit line is in use: nothing that ties up cash is offered
    with_credit = advice(client, NOV_25)
    assert (with_credit["status"], with_credit["liquidity_first"]) == ("GREEN", True)
    assert list(shown(with_credit)) == ["T2"]


def test_liquidity_recommendation_with_advisor_plans_in_the_forecast(repo, reserve_and_francs):
    repo.add_event(expense(date(2025, 12, 5), "25000"), as_of=date(2025, 11, 25))
    llm = FakeLlmClient(LlmTimeoutError("no answer in time"))
    body = api_client(repo, llm).post("/recommendation", json=NOV_25).json()
    assert (body["source"], body["fallback_reason"], body["recommended_option_id"]) == ("fallback", "llm_timeout", "A")
    assert set(body["option_comments"]) == {"A", "D"}
    [request] = llm.requests
    assert '"type": "term_deposit"' in request.input and '"type": "fx_prebuy"' in request.input


def test_advisor_decisions_leave_the_liquidity_agent_of_other_days_untouched(client, repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    before = dashboard(client, MAY_10)
    assert decide(client, NOV_20, ["T1", "F1"]).status_code == 201
    assert decide(client, DEC_31, ["T2"], status="rejected").status_code == 201
    assert decide(client, MAY_10, ["T2"]).status_code == 201
    assert dashboard(client, MAY_10) == before  # no advisor plan reaches into this forecast, no option is flagged

    approved = client.post("/decisions", json={**MAY_10, "option_id": "E", "status": "approved"})
    assert approved.status_code == 201
    assert approved.json()["decision"]["snapshot"] == {
        "status_before": "RED", "min_balance_before_eur": -15728.42, "min_balance_date_before": "2025-07-01",
        "status_after": "GREEN", "min_balance_after_eur": -7061.74, "credit_limit_after_eur": 15000.0}
    assert [decision["option_id"] for decision in client.get("/decisions").json()["decisions"]] == [
        "E", "T2", "T2", "F1", "T1"]


def test_rejecting_a_rescue_option_does_not_touch_the_advisors(client, repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    before = advice(client, MAY_10)
    assert client.post("/decisions", json={**MAY_10, "option_id": "A", "status": "rejected"}).status_code == 201
    assert advice(client, MAY_10) == before


# --- withdrawal and reset ----------------------------------------------------------------------------------

def test_removing_the_plan_of_an_approved_proposal_withdraws_it_and_offers_it_again(client):
    offered = advice(client, DEC_31)
    [reserve, deposit] = decide(client, DEC_31, ["T1", "I1"]).json()["decisions"]
    events = {event["category"]: event for event in client.get("/events").json()["events"]}
    assert events["tax_reserve"]["decision_id"] == reserve["id"]

    assert client.delete(f"/events/{events['tax_reserve']['id']}").status_code == 204
    assert [event["category"] for event in client.get("/events").json()["events"]] == ["investment"]
    history = {decision["option_id"]: decision for decision in client.get("/decisions").json()["decisions"]}
    assert history["T1"]["withdrawn_at"] is not None and history["I1"]["withdrawn_at"] is None
    assert history["T1"]["execution_messages"][-1].startswith("Withdrawn by the owner")
    assert history["I1"] == deposit

    again = shown(advice(client, DEC_31))
    assert list(again) == ["T1", "T2"]
    assert again["T1"]["amount_eur"] == shown(offered)["T1"]["amount_eur"] == 14849.65
    assert decide(client, DEC_31, ["T1"]).status_code == 201
    assert dashboard(client, DEC_31)["risk"]["min_balance_eur"] == 3581.16
    assert [(decision["option_id"], decision["withdrawn_at"] is None)
            for decision in client.get("/decisions").json()["decisions"]] == [("T1", True), ("I1", True),
                                                                              ("T1", False)]


def test_withdrawing_the_chf_purchase_brings_the_instalment_and_the_fx_agent_back(client, reserve_and_francs):
    purchase = next(event for event in client.get("/events").json()["events"] if event["event_type"] == "fx_prebuy")
    assert client.delete(f"/events/{purchase['id']}").status_code == 204
    assert ("2025-12-15", "investment_outflow", -4350.0) in advisor_items(dashboard(client, NOV_20))
    assert "F1" in shown(advice(client, NOV_20))


def test_demo_reset_clears_advisor_plans_and_decisions(client):
    fresh = advice(client, DEC_31)
    assert decide(client, DEC_31, ["T1", "I1"]).status_code == 201
    assert decide(client, DEC_31, ["T2"], status="rejected").status_code == 201
    assert advice(client, DEC_31)["proposals"] == []

    assert client.post("/demo/reset").status_code == 204
    assert client.get("/events").json() == {"events": []}
    assert client.get("/decisions").json() == {"decisions": []}
    assert advice(client, DEC_31) == fresh
