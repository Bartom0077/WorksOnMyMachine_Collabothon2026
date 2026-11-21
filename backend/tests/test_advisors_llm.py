"""POST /advice/recommendation, language-model layer: dossiers, schemas, validation, fallbacks, cache, parallelism.

The ``Advice`` objects are built by hand on top of a real dashboard, so these tests do not depend on the engine.
"""
import json
import threading
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Optional, Union

import pytest

import service
from advisors import grounding
from advisors.llm_service import (
    NO_RECOMMENDATIONS_HEADLINE,
    SUMMARY_SCHEMA_NAME,
    AdviceCache,
    AdviceRecommendation,
    AdviceRecommendationService,
    ProposalText,
    Tip,
    agent_answer_schema,
    agent_schema_name,
    fallback_summary,
    summary_schema,
)
from advisors.model import ActionType, Advice, Agent, Proposal
from advisors.prompts import (
    COORDINATOR_INSTRUCTIONS,
    MAX_TIPS,
    PERSONA,
    agent_dossier,
    agent_instructions,
    coordinator_dossier,
)
from domain import DashboardQuery
from llm import LlmError, LlmHttpError, LlmRequest, LlmResponse, LlmTimeoutError
from llm_fakes import FakeLlmClient, response
from llm_recommendation import ungrounded_amounts

OCT_15 = DashboardQuery(date(2025, 10, 15), 60, Decimal("3000"))
TAX, FX, INVESTMENT = (agent_schema_name(agent) for agent in Agent)
ALL_CALLS = {TAX, FX, INVESTMENT, SUMMARY_SCHEMA_NAME}
MAX_DOSSIER_CHARS = 3000  # roughly 750 tokens: far below the 1,500-token bound of one advisor call

FACTS = {
    Agent.TAX: {"tax_year": 2025, "cash_profit_this_year": "€86,432", "prepayments_paid_this_year": "€3,450",
                "reserve_needed": "€9,803", "already_set_aside": "€0"},
    Agent.FX: {"remaining_instalments": 2, "remaining_dates": ["2025-11-15", "2025-12-15"],
               "remaining_chf": "8,333.34 CHF", "last_rate": "1.046 EUR/CHF", "lowest_rate": "1.038 EUR/CHF",
               "highest_rate": "1.050 EUR/CHF", "exposure": "€8,717", "possible_swing": "€100",
               "sensitivity_is_small": True},
    Agent.INVESTMENT: {"surplus": "€19,000", "interest_rate": "2%"},
}

Step = Union[LlmResponse, LlmError]


class RoutingLlmClient:
    """One scripted ``FakeLlmClient`` per call, chosen by the JSON schema name: the calls run in parallel, so
    their order is not defined. A call without a script fails like an over-used ``FakeLlmClient``."""

    def __init__(self, **scripts: Union[Step, list[Step]]):
        self._routes = {name: FakeLlmClient(*(script if isinstance(script, list) else [script]))
                        for name, script in scripts.items()}

    def respond(self, request: LlmRequest, timeout_seconds: Optional[float] = None) -> LlmResponse:
        route = self._routes.get(request.json_schema.name, FakeLlmClient())
        self._routes[request.json_schema.name] = route
        return route.respond(request, timeout_seconds)

    def requests(self, name: str) -> list[LlmRequest]:
        return self._routes[name].requests if name in self._routes else []

    @property
    def called(self) -> set[str]:
        return {name for name, route in self._routes.items() if route.requests}

    @property
    def call_count(self) -> int:
        return sum(len(route.requests) for route in self._routes.values())


def agent_answer(*recommendations: dict, tips: tuple[dict, ...] = ()) -> LlmResponse:
    return response(json.dumps({"recommendations": list(recommendations), "tips": list(tips)}))


def reworded(proposal_id: str, recommended: bool = True, **overrides) -> dict:
    return {"proposal_id": proposal_id, "recommended": recommended, "title": f"Model title {proposal_id}",
            "explanation": f"Model explanation for {proposal_id}.", **overrides}


def tip(title: str = "Ask your tax advisor", text: str = "Ask whether the prepayments can be raised.") -> dict:
    return {"title": title, "text": text}


IN_DOMAIN_TIP_TEXT = {
    Agent.TAX: "Ask whether the tax prepayments can be raised.",
    Agent.FX: "Ask Luigi whether the CHF transfers could be paid quarterly.",
    Agent.INVESTMENT: "Ask the bank whether the deposit can be ended early.",
}


def summary_answer(headline: str = "Three steps for your spare cash",
                   text: str = "Set aside €9,000 for tax and put €10,000 on deposit. You can combine them freely."
                   ) -> LlmResponse:
    return response(json.dumps({"headline": headline, "text": text}))


def good_answers(**overrides: Union[Step, list[Step]]) -> RoutingLlmClient:
    """Every call answers well unless overridden: tax rewords T1 and T2, fx rewords F2, investment rewords I1."""
    scripts = {
        TAX: agent_answer(reworded("T1"), reworded("T2", recommended=False), tips=(tip(),)),
        FX: agent_answer(reworded("F2")),
        INVESTMENT: agent_answer(reworded("I1")),
        SUMMARY_SCHEMA_NAME: summary_answer(),
    }
    return RoutingLlmClient(**{**scripts, **overrides})


@pytest.fixture()
def workspace(repo):
    return service.build_workspace(repo, OCT_15)


@pytest.fixture()
def advice(workspace) -> Advice:
    """A hand-built advice for 15 October: tax reserve + tax advice, a rate lock and an investment deposit."""
    baseline = workspace.simulate(workspace.dashboard.events)

    def proposal(proposal_id: str, agent: Agent, action: ActionType, title: str, description: str,
                 amount: Optional[str] = None, opens: Optional[date] = None, matures: Optional[date] = None,
                 effect: str = "Nothing changes in the forecast.") -> Proposal:
        return Proposal(
            id=proposal_id, agent=agent, action_type=action, title=title, description=description,
            amount_eur=None if amount is None else Decimal(amount), date_from=opens, date_to=matures,
            interest_eur=None, risk_removed_eur=None, effect=effect,
            assumptions=("Assumed deposit rate: 2% per year (illustrative).",) if amount else (),
            simulated_actions=(f"{title} (simulated).",), events_to_add=(), scenario=baseline,
            fingerprint=f"fingerprint-{proposal_id}")

    proposals = (
        proposal("T1", Agent.TAX, ActionType.TAX_RESERVE, "Tax reserve deposit €9,000",
                 "Set aside €9,000 of the €9,803 you will probably owe in extra income tax.", "9000",
                 date(2025, 10, 16), date(2025, 11, 16), "You get back €9,000 plus €15 interest on 16 November."),
        proposal("T2", Agent.TAX, ActionType.ADVICE, "Ask to raise your tax prepayments",
                 "Your profit is higher than last year. Ask your tax advisor to raise the quarterly prepayments."),
        proposal("F2", Agent.FX, ActionType.FX_FORWARD, "Lock the exchange rate for Zurich",
                 "Fix the rate of 1.046 EUR/CHF for the 2 remaining instalments.", matures=date(2025, 12, 15),
                 effect="The €8,717 for Zurich can no longer change; the possible swing of €100 disappears."),
        proposal("I1", Agent.INVESTMENT, ActionType.TERM_DEPOSIT, "Investment deposit €10,000",
                 "Put €10,000 you do not need on a 6-month deposit.", "10000", date(2025, 10, 16),
                 date(2026, 4, 16), "You earn €100 interest; the money is back on 16 April."),
    )
    return Advice(dashboard=workspace.dashboard, liquidity_first=False, surplus_eur=Decimal("19000"),
                  proposals=proposals, suppressed=(), facts=FACTS, combined=baseline)


def without_proposals(advice: Advice, *agents: Agent) -> Advice:
    return replace(advice, proposals=tuple(p for p in advice.proposals if p.agent not in agents))


def recommend(advice: Advice, client: RoutingLlmClient, cache: Optional[AdviceCache] = None) -> AdviceRecommendation:
    return AdviceRecommendationService(client, cache or AdviceCache()).recommend(advice)


def agent_result(recommendation: AdviceRecommendation, agent: Agent):
    return next(result for result in recommendation.agents if result.agent is agent)


def code_text(advice: Advice, proposal_id: str, recommended: bool = True) -> ProposalText:
    proposal = advice.proposal(proposal_id)
    return ProposalText(proposal.title, proposal.description, recommended)


# --- prompts and dossiers ----------------------------------------------------------------------------------

@pytest.mark.parametrize("agent, must_mention", [
    (Agent.TAX, ["income tax prepayments", "Never state a tax rate from the law", "settled or decided"]),
    (Agent.FX, ["CHF instalments to Zurich", "No other currency", "fees or spreads", "possible swing is small"]),
    (Agent.INVESTMENT, ["term deposits with the bank", "No funds, shares",
                        "Never suggest depositing more than the amount of your proposal",
                        "if you have no proposal, do not suggest any deposit"]),
])
def test_agent_instructions_keep_each_agent_inside_its_domain(agent, must_mention):
    instructions = agent_instructions(agent)
    assert instructions.startswith(PERSONA)
    assert "Use ONLY amounts that appear in the dossier" in instructions
    assert f"at most {MAX_TIPS} extra pieces of advice" in instructions
    for phrase in must_mention:
        assert phrase in instructions
    other_domains = [agent_instructions(other) for other in Agent if other is not agent]
    assert all(must_mention[0] not in other for other in other_domains)


def test_coordinator_instructions_ask_for_a_grounded_summary():
    assert COORDINATOR_INSTRUCTIONS.startswith(PERSONA)
    for phrase in ("Use ONLY amounts that appear in the dossier", "approve all of them together", "NOT alternatives",
                   "at most 12 words",
                   "2-4 sentences"):
        assert phrase in COORDINATOR_INSTRUCTIONS


def test_coordinator_instructions_carry_the_prohibitions_of_every_agent():
    """The summary rewords the proposals of all three agents, so it is bound by what binds each of them."""
    for phrase in ("never state a tax rate from the law", "never say when the extra tax will be settled or decided",
                   "bank fees, spreads, other currencies, other products", "where the exchange rate will go",
                   "State no date and no percentage that is not in the dossier"):
        assert phrase in COORDINATOR_INSTRUCTIONS.replace("\n", " ")


def test_agent_dossier_holds_the_facts_and_only_that_agents_proposals(advice):
    dossier = agent_dossier(advice, Agent.TAX)
    assert dossier["today"] == "2025-10-15" and dossier["agent"] == "tax"
    assert dossier["facts"] == FACTS[Agent.TAX]
    assert dossier["liquidity"] == "No liquidity problem is open."
    assert dossier["proposals"] == [
        {"id": "T1", "action": "tax_reserve", "title": "Tax reserve deposit €9,000",
         "description": "Set aside €9,000 of the €9,803 you will probably owe in extra income tax.",
         "amount": "€9,000", "date_from": "2025-10-16", "date_to": "2025-11-16",
         "effect": "You get back €9,000 plus €15 interest on 16 November.",
         "assumptions": ["Assumed deposit rate: 2% per year (illustrative)."]},
        {"id": "T2", "action": "advice", "title": "Ask to raise your tax prepayments",
         "description": "Your profit is higher than last year. Ask your tax advisor to raise the quarterly "
                        "prepayments.",
         "effect": "Nothing changes in the forecast.", "assumptions": []},
    ]


def test_agent_dossier_says_when_a_liquidity_problem_is_open(advice):
    dossier = agent_dossier(replace(advice, liquidity_first=True), Agent.TAX)
    assert dossier["liquidity"].startswith("A liquidity problem is open")


def test_coordinator_dossier_lists_every_proposal_with_its_agent(advice):
    dossier = coordinator_dossier(advice)
    assert {key: dossier[key] for key in ("today", "status", "liquidity_problem_open", "surplus")} == {
        "today": "2025-10-15", "status": advice.dashboard.risk.status.value, "liquidity_problem_open": False,
        "surplus": "€19,000"}
    assert [(view["id"], view["agent"], view.get("amount")) for view in dossier["proposals"]] == [
        ("T1", "tax", "€9,000"), ("T2", "tax", None), ("F2", "fx", None), ("I1", "investment", "€10,000")]
    assert all(set(view) <= {"id", "agent", "title", "amount", "ties_up_cash", "effect"} for view in dossier["proposals"])


def test_dossier_amounts_ground_exactly_the_engine_figures(advice):
    dossier = agent_dossier(advice, Agent.TAX)
    assert ungrounded_amounts("Set aside €9,000 of the €9,803; you paid €3,450 so far.", dossier) == []
    assert ungrounded_amounts("Put €10,000 on deposit.", dossier) == ["€10,000"]  # another agent's figure


def test_every_dossier_is_small(advice):
    dossiers = [agent_dossier(advice, agent) for agent in Agent] + [coordinator_dossier(advice)]
    for dossier in dossiers:
        assert len(json.dumps(dossier, ensure_ascii=False)) < MAX_DOSSIER_CHARS


# --- grounding: dates, percentages and small figures -------------------------------------------------------

GROUNDING_DOSSIER = {"today": "2025-10-15", "facts": {"rate": "1.050 EUR/CHF", "set_aside": "€0", "interest": "2%"},
                     "proposals": [{"date_to": "2025-12-15", "effect": "Back on 16 November with €15 interest."}]}


@pytest.mark.parametrize("text, invented", [
    ("Due on 2025-12-15, 15.12.2025, 15 December, 15th of December 2025 or December 15, 2025.", []),
    ("Back on 16 November. Today is October 15.", []),
    ("Settled by 31 May 2026.", ["31 May 2026"]),
    ("Due on 15 December 2026.", ["15 December 2026"]),  # the right day in another year
    ("Pay on 2026-01-10 or on 10.1.2026 or on January 10.", ["2026-01-10", "10.1.2026", "January 10"]),
    ("In May 2026 you may owe more.", []),  # a month is not a date; nothing to check
])
def test_dates_must_be_in_the_dossier(text, invented):
    assert grounding.ungrounded_dates(text, GROUNDING_DOSSIER) == invented


def test_a_date_is_not_read_as_an_amount():
    text = "The money is back on 16.11.2025 with €15."
    assert grounding.ungrounded_dates(text, GROUNDING_DOSSIER) == []
    assert ungrounded_amounts(text, GROUNDING_DOSSIER) == ["16.11"]  # the shared check alone stumbles here
    assert grounding.ungrounded_amounts(grounding.without_dates(text), GROUNDING_DOSSIER) == []


@pytest.mark.parametrize("text, invented", [
    ("Interest at 2%, that is 2 percent or 2.0 per cent.", []),
    ("The rate is 42% plus 5.5 %.", ["42%", "5.5 %"]),
    ("About 15,33 percent.", ["15,33 percent"]),
    ("Interest at 20%.", ["20%"]),
])
def test_percentages_must_be_in_the_dossier(text, invented):
    assert grounding.ungrounded_percentages(text, GROUNDING_DOSSIER) == invented


@pytest.mark.parametrize("text, invented", [
    ("The rate is 1.050 EUR/CHF, or 1.05.", []),
    ("You set aside €0 and earn €15.", []),
    ("The rate may reach 1.10 EUR/CHF.", ["1.10 EUR"]),
    ("A fee of EUR 0.80 and a spread of 1.5 EUR.", ["EUR 0.80", "1.5 EUR"]),
    ("About €0.90 a day.", ["€0.90"]),
    ("You earn €15.40.", []),  # from ten euros on, cents may be dropped as for the liquidity agent
    ("You earn €150.", ["€150"]),
])
def test_small_figures_must_match_the_dossier_exactly(text, invented):
    assert grounding.ungrounded_amounts(text, GROUNDING_DOSSIER) == invented


# --- schemas -----------------------------------------------------------------------------------------------

def test_agent_schema_allows_exactly_the_proposals_on_offer():
    schema = agent_answer_schema(["T1", "T2"])
    recommendations = schema["properties"]["recommendations"]
    assert recommendations["items"]["properties"]["proposal_id"] == {"type": "string", "enum": ["T1", "T2"]}
    assert recommendations["maxItems"] == 2
    assert schema["properties"]["tips"]["maxItems"] == MAX_TIPS
    assert schema["required"] == ["recommendations", "tips"] and schema["additionalProperties"] is False


def test_agent_schema_without_proposals_allows_only_tips():
    assert agent_answer_schema([])["properties"]["recommendations"] == {"type": "array", "maxItems": 0}


def test_requests_carry_the_schema_of_their_own_agent(advice):
    client = good_answers()
    recommend(advice, client)
    expected_ids = {TAX: ["T1", "T2"], FX: ["F2"], INVESTMENT: ["I1"]}
    for agent in Agent:
        (request,) = client.requests(agent_schema_name(agent))
        items = request.json_schema.schema["properties"]["recommendations"]["items"]
        assert items["properties"]["proposal_id"]["enum"] == expected_ids[request.json_schema.name]
        assert request.instructions == agent_instructions(agent)
        assert request.input == f"DOSSIER:\n{json.dumps(agent_dossier(advice, agent), ensure_ascii=False)}"
        assert request.mcp is None
    (summary_request,) = client.requests(SUMMARY_SCHEMA_NAME)
    assert summary_request.json_schema.schema == summary_schema()
    assert summary_request.instructions == COORDINATOR_INSTRUCTIONS


# --- happy path --------------------------------------------------------------------------------------------

def test_model_texts_are_used_for_every_agent_and_the_summary(advice):
    recommendation = recommend(advice, good_answers())

    assert recommendation.summary.source == "llm" and recommendation.summary.fallback_reason is None
    assert recommendation.summary.headline == "Three steps for your spare cash"
    assert recommendation.summary.text.startswith("Set aside €9,000")
    assert [(result.agent, result.source, result.fallback_reason) for result in recommendation.agents] == [
        (Agent.TAX, "llm", None), (Agent.FX, "llm", None), (Agent.INVESTMENT, "llm", None)]
    assert list(recommendation.proposals) == ["T1", "T2", "F2", "I1"]
    assert recommendation.proposals["T1"] == ProposalText("Model title T1", "Model explanation for T1.", True)
    assert recommendation.proposals["T2"] == ProposalText("Model title T2", "Model explanation for T2.", False)
    assert recommendation.proposals["F2"].recommended and recommendation.proposals["I1"].recommended


def test_the_model_may_recommend_several_proposals_or_none(advice):
    several = recommend(advice, good_answers(**{TAX: agent_answer(reworded("T1"), reworded("T2"))}))
    assert several.proposals["T1"].recommended and several.proposals["T2"].recommended

    none = recommend(advice, good_answers(**{TAX: agent_answer(reworded("T1", False), reworded("T2", False))}))
    assert not none.proposals["T1"].recommended and not none.proposals["T2"].recommended
    assert agent_result(none, Agent.TAX).source == "llm"


def test_a_proposal_the_model_did_not_mention_keeps_the_code_text_unrecommended(advice):
    recommendation = recommend(advice, good_answers(**{TAX: agent_answer(reworded("T2"))}))
    assert recommendation.proposals["T1"] == code_text(advice, "T1", recommended=False)
    assert recommendation.proposals["T2"].title == "Model title T2"
    assert agent_result(recommendation, Agent.TAX).source == "llm"


def test_tips_are_reported_per_agent(advice):
    tips = (tip(), tip("Keep the notice", "Keep the tax office's letter about the €3,450 you already paid."))
    recommendation = recommend(advice, good_answers(**{TAX: agent_answer(reworded("T1"), tips=tips)}))
    assert agent_result(recommendation, Agent.TAX).tips == (
        Tip("Ask your tax advisor", "Ask whether the prepayments can be raised."),
        Tip("Keep the notice", "Keep the tax office's letter about the €3,450 you already paid."))
    assert agent_result(recommendation, Agent.FX).tips == ()


def test_an_agent_without_proposals_can_still_give_tips(advice):
    client = good_answers(**{FX: agent_answer(tips=(tip("Small risk", "The possible swing on the CHF transfers is only €100."),))})
    recommendation = recommend(without_proposals(advice, Agent.FX), client)
    assert agent_result(recommendation, Agent.FX).tips == (
        Tip("Small risk", "The possible swing on the CHF transfers is only €100."),)
    assert list(recommendation.proposals) == ["T1", "T2", "I1"]
    (request,) = client.requests(FX)
    assert request.json_schema.schema["properties"]["recommendations"] == {"type": "array", "maxItems": 0}


# --- validation failures: only the affected part falls back -----------------------------------------------

@pytest.mark.parametrize("bad_answer, reason", [
    (response(agent_answer(reworded("T1")).text, truncated=True), "llm_truncated_output"),
    (response("I would set the money aside."), "llm_invalid_json"),
    (response(json.dumps({"recommendations": "T1", "tips": []})), "llm_invalid_json"),
    (response(json.dumps({"recommendations": []})), "llm_invalid_json"),
    (agent_answer(reworded("T1", recommended="yes")), "llm_invalid_json"),
    (agent_answer(reworded("T1", title="  ")), "llm_invalid_json"),
    (agent_answer(reworded("T1", explanation="")), "llm_invalid_json"),
    (agent_answer(reworded("T1", title="Set money aside " * 20)), "llm_invalid_json"),
    (agent_answer(reworded("T1", explanation="It is wise. " * 100)), "llm_invalid_json"),
    (agent_answer(reworded("T1"), tips=(tip(), tip(), tip())), "llm_invalid_json"),
    (agent_answer(reworded("T1"), tips=(tip(text=""),)), "llm_invalid_json"),
    (agent_answer(reworded("I1")), "llm_unknown_option"),
    (agent_answer(reworded("T9")), "llm_unknown_option"),
    (agent_answer(reworded("T1"), reworded("T1", recommended=False)), "llm_duplicate_proposal"),
    (agent_answer(reworded("T1", explanation="Set aside €12,500 now.")), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", title="Park €4,321 for tax")), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1"), tips=(tip(text="Expect a bill of €7,777."),)), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", explanation="Like the €10,000 deposit.")), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", explanation="The statutory income tax rate is 42%, the surcharge 5.5%.")),
     "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", explanation="Your effective tax rate is 15.33%.")), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1"), tips=(tip(text="Late payment interest of 0.5 percent per month applies."),)),
     "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", explanation="The tax office will settle the extra tax by 31 May 2026.")),
     "llm_ungrounded_numbers"),
    (agent_answer(reworded("T2", explanation="Ask before the next prepayment on December 10.")),
     "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1"), tips=(tip(text="Settle it by 2026-05-31."),)), "llm_ungrounded_numbers"),
    (agent_answer(reworded("T1", explanation="That is about €0.90 a day.")), "llm_ungrounded_numbers"),
    (LlmTimeoutError("no answer in time"), "llm_timeout"),
    (LlmHttpError("HTTP 500"), "llm_http_error"),
])
def test_a_bad_agent_answer_falls_back_for_that_agent_only(advice, bad_answer, reason):
    recommendation = recommend(advice, good_answers(**{TAX: bad_answer}))

    tax = agent_result(recommendation, Agent.TAX)
    assert (tax.source, tax.fallback_reason, tax.tips) == ("fallback", reason, ())
    assert recommendation.proposals["T1"] == code_text(advice, "T1")
    assert recommendation.proposals["T2"] == code_text(advice, "T2")
    assert [agent_result(recommendation, agent).source for agent in (Agent.FX, Agent.INVESTMENT)] == ["llm", "llm"]
    assert recommendation.proposals["F2"].title == "Model title F2"
    assert recommendation.proposals["I1"].title == "Model title I1"
    assert recommendation.summary.source == "llm"


@pytest.mark.parametrize("agent, call, proposal_id, invented", [
    (Agent.FX, FX, "F2", "The rate could rise to 1.10 EUR/CHF next month."),
    (Agent.FX, FX, "F2", "The bank charges a fee of EUR 0.80 per transfer."),
    (Agent.FX, FX, "F2", "The bank adds a spread of 1.5 EUR per transfer."),
    (Agent.FX, FX, "F2", "Consider dollars instead: 1.08 USD per EUR."),
    (Agent.FX, FX, "F2", "The rate will not move by more than 3%."),
    (Agent.INVESTMENT, INVESTMENT, "I1", "Put the €10,000 into a 6-month deposit at 3.5% p.a."),
    (Agent.INVESTMENT, INVESTMENT, "I1", "A 12-month deposit at 4% is better."),
    (Agent.INVESTMENT, INVESTMENT, "I1", "Open it on 21.11.2025."),
])
def test_an_invented_rate_fee_percentage_or_date_falls_back(advice, agent, call, proposal_id, invented):
    """Figures close to a real rate, percentages and dates were once waved through as "grounded"."""
    for answer in (agent_answer(reworded(proposal_id, explanation=invented)),
                   agent_answer(reworded(proposal_id), tips=(tip(text=invented),))):
        recommendation = recommend(advice, good_answers(**{call: answer}))
        result = agent_result(recommendation, agent)
        assert (result.source, result.fallback_reason, result.tips) == ("fallback", "llm_ungrounded_numbers", ())
        assert recommendation.proposals[proposal_id] == code_text(advice, proposal_id)


@pytest.mark.parametrize("agent, call, proposal_id, grounded", [
    (Agent.FX, FX, "F2", "The last rate was 1.046 EUR/CHF; so far it moved between 1.038 and 1.050 (1.05 EUR/CHF "
                         "at most). The 8,333.34 CHF cost €8,717 and could move by €100."),
    (Agent.FX, FX, "F2", "The last transfer is due on 15 December 2025 (15.12.2025, or 2025-12-15); the one "
                         "before it on November 15."),
    (Agent.INVESTMENT, INVESTMENT, "I1", "The €10,000 earns 2% a year (2 percent, an assumed rate) until "
                                         "16 April and is opened on 16.10.2025."),
    (Agent.TAX, TAX, "T1", "You have set aside €0 so far; the €9,000 comes back on 16 November with interest "
                           "at 2%."),
])
def test_figures_dates_and_percentages_from_the_dossier_are_accepted(advice, agent, call, proposal_id, grounded):
    answer = agent_answer(reworded(proposal_id, explanation=grounded))
    recommendation = recommend(advice, good_answers(**{call: answer}))
    assert agent_result(recommendation, agent).source == "llm"
    assert recommendation.proposals[proposal_id].explanation == grounded


@pytest.mark.parametrize("bad_answer, reason", [
    (response(summary_answer().text, truncated=True), "llm_truncated_output"),
    (response("Approve everything."), "llm_invalid_json"),
    (summary_answer(headline=""), "llm_invalid_json"),
    (summary_answer(headline="A very long headline " * 10), "llm_invalid_json"),
    (summary_answer(text="Do it. " * 200), "llm_invalid_json"),
    (summary_answer(text="Together this ties up €19,500."), "llm_ungrounded_numbers"),
    (summary_answer(text="Tax rate 30%. You can combine them freely."), "llm_ungrounded_numbers"),
    (summary_answer(text="Set aside €9,000 and settle the tax before 1 March 2026."), "llm_ungrounded_numbers"),
    (summary_answer(headline="Lock 1.10 EUR/CHF today"), "llm_ungrounded_numbers"),
    (LlmTimeoutError("no answer in time"), "llm_timeout"),
])
def test_a_bad_summary_falls_back_to_the_code_summary_only(advice, bad_answer, reason):
    recommendation = recommend(advice, good_answers(**{SUMMARY_SCHEMA_NAME: bad_answer}))

    assert recommendation.summary == fallback_summary(advice, reason)
    assert recommendation.summary.source == "fallback" and recommendation.summary.fallback_reason == reason
    assert all(result.source == "llm" for result in recommendation.agents)
    assert recommendation.proposals["T1"].title == "Model title T1"


def test_everything_falls_back_when_the_model_is_down(advice):
    down = {name: LlmTimeoutError("no answer in time") for name in ALL_CALLS}
    recommendation = recommend(advice, RoutingLlmClient(**down))

    assert recommendation.summary.fallback_reason == "llm_timeout"
    assert [result.fallback_reason for result in recommendation.agents] == ["llm_timeout"] * 3
    assert recommendation.proposals == {proposal.id: code_text(advice, proposal.id) for proposal in advice.proposals}


# --- the summary written by code ---------------------------------------------------------------------------

def test_code_summary_counts_the_proposals_and_the_cash_they_tie_up(advice):
    summary = fallback_summary(advice, "llm_timeout")
    assert summary.headline == "4 recommendations for you today"
    assert summary.text == ("Your tax, currency and investment advice for today: 4 steps you can approve. "
                            "Approving everything would tie up €19,000 of your spare cash. "
                            "You can combine them freely.")


def test_code_summary_for_a_single_proposal_that_ties_up_nothing(advice):
    only_rate_lock = without_proposals(advice, Agent.TAX, Agent.INVESTMENT)
    summary = fallback_summary(only_rate_lock, "llm_timeout")
    assert summary.headline == "1 recommendation for you today"
    assert summary.text == ("Your currency advice for today: 1 step you can approve. "
                            "None of this takes cash out of your account.")


def test_code_summary_puts_liquidity_first(advice):
    liquidity_first = replace(without_proposals(advice, Agent.FX, Agent.INVESTMENT), liquidity_first=True,
                              proposals=(advice.proposal("T2"),))
    assert "Solving the cash shortage comes first" in fallback_summary(liquidity_first, "llm_timeout").text
    nothing = fallback_summary(replace(liquidity_first, proposals=()), None)
    assert nothing.headline == NO_RECOMMENDATIONS_HEADLINE
    assert "Solving the cash shortage comes first" in nothing.text


# --- situations without a model call -----------------------------------------------------------------------

def test_no_model_call_when_there_is_nothing_to_say(advice):
    client = RoutingLlmClient()
    recommendation = recommend(replace(advice, proposals=(), facts={}), client)

    assert client.call_count == 0
    assert recommendation.agents == () and recommendation.proposals == {}
    assert recommendation.summary.source == "fallback" and recommendation.summary.fallback_reason is None
    assert recommendation.summary.headline == NO_RECOMMENDATIONS_HEADLINE


def test_no_model_call_for_an_agent_without_facts(advice):
    client = good_answers()
    only_tax_facts = replace(advice, facts={Agent.TAX: FACTS[Agent.TAX]})
    recommendation = recommend(only_tax_facts, client)

    assert client.called == {TAX, SUMMARY_SCHEMA_NAME}
    assert [result.agent for result in recommendation.agents] == [Agent.TAX]
    assert recommendation.proposals["F2"] == code_text(advice, "F2")
    assert recommendation.proposals["I1"] == code_text(advice, "I1")


def test_no_summary_call_when_no_proposal_is_on_offer(advice):
    client = good_answers(**{name: agent_answer() for name in (TAX, FX, INVESTMENT)})
    recommendation = recommend(replace(advice, proposals=()), client)

    assert client.called == {TAX, FX, INVESTMENT}
    assert recommendation.summary.headline == NO_RECOMMENDATIONS_HEADLINE
    assert recommendation.summary.fallback_reason is None
    assert [result.source for result in recommendation.agents] == ["llm"] * 3


# --- cache -------------------------------------------------------------------------------------------------

def test_a_second_call_for_the_same_advice_asks_no_model(advice):
    cache = AdviceCache()
    first = recommend(advice, good_answers(), cache)
    silent = RoutingLlmClient()
    assert recommend(advice, silent, cache) == first
    assert silent.call_count == 0


def test_a_changed_dossier_is_asked_again_and_the_others_stay_cached(advice):
    cache = AdviceCache()
    recommend(advice, good_answers(), cache)
    changed_tax = replace(advice, facts={**FACTS, Agent.TAX: {**FACTS[Agent.TAX], "reserve_needed": "€9,900"}})
    client = RoutingLlmClient(**{TAX: agent_answer(reworded("T1", title="Fresh title"))})
    recommendation = recommend(changed_tax, client, cache)

    assert client.called == {TAX}
    assert recommendation.proposals["T1"].title == "Fresh title"
    assert recommendation.proposals["F2"].title == "Model title F2"
    assert recommendation.summary.source == "llm"


def test_fallbacks_are_never_cached(advice):
    cache = AdviceCache()
    failed = recommend(advice, good_answers(**{TAX: LlmTimeoutError("no answer in time")}), cache)
    assert agent_result(failed, Agent.TAX).source == "fallback"

    retry = RoutingLlmClient(**{TAX: agent_answer(reworded("T1"))})
    recovered = recommend(advice, retry, cache)
    assert retry.called == {TAX}
    assert agent_result(recovered, Agent.TAX).source == "llm"


def test_cache_drops_the_least_recently_used_answer():
    cache = AdviceCache(max_entries=2)
    cache.put("a", "answer a")
    cache.put("b", "answer b")
    assert cache.get("a") == "answer a"
    cache.put("c", "answer c")
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == ("answer a", None, "answer c")


# --- parallelism -------------------------------------------------------------------------------------------

class MeetingLlmClient(RoutingLlmClient):
    """Answers only once all four calls are in flight at the same time: proof that they run in parallel."""

    def __init__(self, **scripts: Union[Step, list[Step]]):
        super().__init__(**scripts)
        self._all_in_flight = threading.Barrier(len(ALL_CALLS), timeout=10)

    def respond(self, request: LlmRequest, timeout_seconds: Optional[float] = None) -> LlmResponse:
        self._all_in_flight.wait()
        return super().respond(request, timeout_seconds)


def test_calls_run_in_parallel_and_answers_are_not_mixed_between_agents(advice):
    answers = {agent_schema_name(agent): agent_answer(
        *(reworded(proposal.id, title=f"{agent.value} speaks about {proposal.id}")
          for proposal in advice.of_agent(agent)),
        tips=(tip(title=f"Tip from {agent.value}", text=IN_DOMAIN_TIP_TEXT[agent]),)) for agent in Agent}
    recommendation = recommend(advice, MeetingLlmClient(**answers, **{SUMMARY_SCHEMA_NAME: summary_answer()}))

    for agent in Agent:
        assert agent_result(recommendation, agent).tips[0].title == f"Tip from {agent.value}"
        for proposal in advice.of_agent(agent):
            assert recommendation.proposals[proposal.id].title == f"{agent.value} speaks about {proposal.id}"
    assert recommendation.summary.source == "llm"


def test_a_tip_outside_the_agents_area_is_dropped_and_the_rest_is_kept(advice):
    client = good_answers(**{INVESTMENT: agent_answer(reworded("I1"), tips=(
        tip("Check inventory", "Do not hold too much unsold food."),
        tip("Ending early", IN_DOMAIN_TIP_TEXT[Agent.INVESTMENT])))})
    result = agent_result(recommend(advice, client), Agent.INVESTMENT)
    assert result.source == "llm"
    assert result.tips == (Tip("Ending early", IN_DOMAIN_TIP_TEXT[Agent.INVESTMENT]),)


@pytest.mark.parametrize("text", [
    "You can either set aside €9,000 for tax or put €10,000 on deposit.",
    "Set aside €9,000 for tax. Alternatively, put €10,000 on deposit.",
    "Choose between the tax reserve and the deposit.",
])
def test_a_summary_that_presents_the_proposals_as_alternatives_falls_back(advice, text):
    summary = recommend(advice, good_answers(**{SUMMARY_SCHEMA_NAME: summary_answer(text=text)})).summary
    assert (summary.source, summary.fallback_reason) == ("fallback", "llm_contradicts_engine")
    assert "You can combine them freely." in summary.text


def test_a_single_proposal_may_be_summed_up_with_the_word_or(advice):
    alone = replace(advice, proposals=advice.of_agent(Agent.FX))
    client = good_answers(**{SUMMARY_SCHEMA_NAME: summary_answer(text="Lock the rate now or leave it; it is your call.")})
    assert recommend(alone, client).summary.source == "llm"


def test_an_agent_whose_candidates_were_held_back_by_a_cash_shortage_stays_silent(advice):
    shortage = replace(without_proposals(advice, Agent.FX, Agent.INVESTMENT), liquidity_first=True)
    client = good_answers()
    recommendation = recommend(shortage, client)
    assert [result.agent for result in recommendation.agents] == [Agent.TAX]
    assert client.called == {TAX, SUMMARY_SCHEMA_NAME}
