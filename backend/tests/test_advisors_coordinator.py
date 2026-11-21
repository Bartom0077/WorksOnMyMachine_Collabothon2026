"""The coordinator's rule table R1..R6: every rule on its own, on real data with planned events and decisions."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from advisors import coordinator
from advisors.engine import build_advice
from advisors.model import Advice, Agent
from domain import DashboardQuery, DecisionStatus, EventDraft, EventSource, EventType, NewDecision, RiskStatus
from fakes import closure, expense
from kpi_engine.money import money_json
from repository import FINGERPRINT_KEY
from service import build_workspace, record_decision
from texts import format_eur

RED_DAY = date(2025, 5, 10)
MID_OCTOBER = date(2025, 10, 15)
LATE_OCTOBER = date(2025, 10, 20)
EARLY_NOVEMBER = date(2025, 11, 7)
LATE_NOVEMBER = date(2025, 11, 20)
YEAR_END = date(2025, 12, 31)


def _query(as_of, horizon=60, buffer="3000") -> DashboardQuery:
    return DashboardQuery(as_of, horizon, Decimal(buffer))


def _advice(repo, as_of, horizon=60, buffer="3000") -> Advice:
    return build_advice(repo, _query(as_of, horizon, buffer))


def _ids(advice: Advice) -> list[str]:
    return [proposal.id for proposal in advice.proposals]


def _suppressed(advice: Advice) -> list[tuple[str, str]]:
    return [(item.id, item.reason_code) for item in advice.suppressed]


def _decide(repo, advice: Advice, proposal_ids, status=DecisionStatus.APPROVED) -> None:
    """Store the owner's decision the way the decision service does: decisions plus the events of approvals."""
    approved = status is DecisionStatus.APPROVED
    dashboard = advice.dashboard
    decisions = []
    for proposal in (advice.proposal(proposal_id) for proposal_id in proposal_ids):
        snapshot = {
            FINGERPRINT_KEY: proposal.fingerprint,
            "status_before": dashboard.risk.status.value,
            "min_balance_before_eur": money_json(dashboard.risk.min_balance_eur),
            "min_balance_date_before": dashboard.risk.min_balance_date.isoformat(),
            "status_after": proposal.new_status.value,
            "min_balance_after_eur": money_json(proposal.new_min_balance_eur),
            "cost_eur": 0.0,
        }
        decision = NewDecision(
            as_of_date=dashboard.as_of_date, horizon_days=dashboard.horizon_days, buffer_eur=dashboard.buffer_eur,
            option_id=proposal.id, option_title=proposal.title, option_description=proposal.description,
            status=status, llm_explanation=None,
            execution_messages=proposal.simulated_actions if approved else (), forecast_snapshot=snapshot)
        decisions.append((decision, proposal.events_to_add if approved else ()))
    repo.record_decisions(decisions)


def _zurich_deferral(due: date, new_due: date) -> EventDraft:
    return EventDraft(event_type=EventType.PAYMENT_DEFERRAL, date_from=due, date_to=new_due,
                      amount_eur=Decimal("4358.34"), category="investment_outflow",
                      description="Transfer to Zurich postponed")


def _credit_line(start: date, limit: str) -> EventDraft:
    return EventDraft(event_type=EventType.CREDIT_LINE, date_from=start, date_to=start + timedelta(days=365),
                      amount_eur=Decimal(limit), description="Credit line")


# --------------------------------------------------------------------------- R1: liquidity first

def test_r1_red_status_leaves_only_the_tax_warning(repo):
    advice = _advice(repo, RED_DAY)
    assert advice.dashboard.risk.status is RiskStatus.RED and advice.liquidity_first
    assert advice.surplus_eur == 0
    assert _ids(advice) == ["T2"]
    assert _suppressed(advice) == [("T1", "liquidity_first"), ("F1", "liquidity_first"), ("F2", "liquidity_first")]
    [warning] = advice.proposals
    assert warning.title == "Plan for an extra income tax bill"
    assert "about €3,793" in warning.description and "once the balance recovers" in warning.description
    assert advice.combined.forecast == advice.dashboard.forecast


def test_r1_yellow_status_blocks_cash_and_the_rate_lock_too(repo):
    advice = _advice(repo, date(2025, 1, 20), horizon=30, buffer="8000")
    assert advice.dashboard.risk.status is RiskStatus.YELLOW and advice.liquidity_first
    assert _ids(advice) == ["T2"]
    assert _suppressed(advice) == [("T1", "liquidity_first"), ("F1", "liquidity_first"), ("F2", "liquidity_first")]


def test_r1_green_thanks_to_a_used_credit_line_blocks_cash_but_keeps_the_rate_lock(repo):
    repo.add_event(_credit_line(RED_DAY + timedelta(days=1), "5000"))
    advice = _advice(repo, RED_DAY)
    assert advice.dashboard.risk.status is RiskStatus.GREEN
    assert advice.dashboard.risk.min_balance_eur < 0 and advice.liquidity_first
    assert _ids(advice) == ["T2", "F2"]
    assert _suppressed(advice) == [("T1", "liquidity_first"), ("F1", "liquidity_first")]
    assert "once the balance recovers" in advice.proposal("T2").description
    assert advice.surplus_eur == 0 and Agent.INVESTMENT not in advice.facts


def test_r1_an_unused_credit_line_is_no_liquidity_problem(repo):
    repo.add_event(_credit_line(date(2026, 1, 1), "5000"))
    advice = _advice(repo, YEAR_END)
    assert not advice.liquidity_first
    assert _ids(advice) == ["T1", "T2", "I1"]


def test_r1_suppressed_reasons_are_explained(repo):
    advice = _advice(repo, RED_DAY)
    assert {item.reason for item in advice.suppressed} == {coordinator.REASONS["liquidity_first"]}
    assert [(item.agent, item.action_type.value) for item in advice.suppressed] == [
        (Agent.TAX, "tax_reserve"), (Agent.FX, "fx_prebuy"), (Agent.FX, "fx_forward")]


# --------------------------------------------------------------------------- R2: one shared budget

def test_r2_tax_reserve_is_capped_to_the_budget(repo):
    advice = _advice(repo, MID_OCTOBER)
    assert advice.surplus_eur == Decimal("9000")
    reserve = advice.proposal("T1")
    assert reserve.amount_eur == Decimal("9000")  # the full reserve would be 9,802.91
    assert "covers €9,000 of the €9,803 still needed" in reserve.description
    assert ("F1", "over_budget") in _suppressed(advice) and ("I1", "below_threshold") in _suppressed(advice)
    assert advice.combined.risk.min_balance_eur == advice.dashboard.risk.min_balance_eur - Decimal("9000")


def test_r2_full_reserve_when_it_fits(repo):
    advice = _advice(repo, LATE_OCTOBER)
    assert advice.surplus_eur == Decimal("12000")
    assert advice.proposal("T1").amount_eur == Decimal("10806.92")
    assert ("F1", "over_budget") in _suppressed(advice)  # 8,716.67 does not fit into the 1,193.08 that is left


def test_r2_no_tax_reserve_without_free_cash(repo):
    advice = _advice(repo, date(2025, 1, 25), horizon=30, buffer="8000")
    assert advice.dashboard.risk.status is RiskStatus.GREEN and advice.surplus_eur == 0
    assert _ids(advice) == ["T2", "F2"]
    assert _suppressed(advice) == [("T1", "over_budget"), ("F1", "over_budget")]
    assert advice.proposal("T2").title == "Ask for higher income tax prepayments"


def test_r2_the_purchase_takes_what_the_tax_reserve_leaves(repo):
    advice = _advice(repo, LATE_NOVEMBER)
    assert advice.surplus_eur == Decimal("18000")
    assert _ids(advice) == ["T1", "T2", "F1"]
    assert (advice.proposal("T1").amount_eur, advice.proposal("F1").amount_eur) == (
        Decimal("12969.35"), Decimal("4350.00"))
    assert _suppressed(advice) == [("I1", "below_threshold")]  # 680.65 is left
    assert advice.combined.risk.status is RiskStatus.GREEN
    assert advice.combined.risk.min_balance_eur == Decimal("3741.08")  # 21,060.43 - 12,969.35 - 4,350.00


def test_r2_investment_deposit_gets_the_rest_rounded_down(repo):
    advice = _advice(repo, YEAR_END)
    assert advice.surplus_eur == Decimal("27000")
    assert advice.proposal("I1").amount_eur == Decimal("12000")  # 27,000 - 14,849.65 = 12,150.35
    assert sum(proposal.amount_eur for proposal in advice.proposals if proposal.amount_eur) <= advice.surplus_eur


@pytest.mark.parametrize("as_of, horizon, buffer, surplus", [
    (date(2025, 1, 6), 30, "0", "3000"),  # less than 10,000 of free cash in total
    (YEAR_END, 60, "8000", "22000"),  # enough in total, but only 7,000 are left after the tax reserve
])
def test_r2_investment_deposit_needs_ten_thousand(repo, as_of, horizon, buffer, surplus):
    advice = _advice(repo, as_of, horizon, buffer)
    assert advice.surplus_eur == Decimal(surplus)
    assert "I1" not in _ids(advice) and ("I1", "below_threshold") in _suppressed(advice)
    assert (Agent.INVESTMENT in advice.facts) == (advice.surplus_eur >= 10000)


def test_r2_a_larger_buffer_shrinks_every_amount(repo):
    relaxed, careful = _advice(repo, YEAR_END, buffer="0"), _advice(repo, YEAR_END, buffer="5000")
    assert (relaxed.surplus_eur, careful.surplus_eur) == (Decimal("30000"), Decimal("25000"))
    assert (relaxed.proposal("I1").amount_eur, careful.proposal("I1").amount_eur) == (
        Decimal("15000"), Decimal("10000"))


def _with_trouble_for_combinations(workspace):
    """A workspace whose forecast gets an unexpected bill as soon as two advisor events are simulated together."""
    def simulate(events):
        advisor_events = [event for event in events
                          if event.event_type in (EventType.TERM_DEPOSIT, EventType.FX_PREBUY)]
        bill = [expense(workspace.dashboard.as_of_date + timedelta(days=2), "25000")] if len(advisor_events) > 1 else []
        return workspace.simulate([*events, *bill])
    return replace(workspace, simulate=simulate)


def test_r2_proposals_that_do_not_fit_together_are_given_up_from_the_end(repo):
    workspace = build_workspace(repo, _query(date(2025, 12, 8), 30, "0"))
    assert _ids(coordinator.coordinate(workspace, {})) == ["T1", "T2", "F1", "I1"]
    advice = coordinator.coordinate(_with_trouble_for_combinations(workspace), {})
    assert _ids(advice) == ["T1", "T2"]
    assert _suppressed(advice)[-2:] == [("I1", "combined_below_buffer"), ("F1", "combined_below_buffer")]
    assert advice.combined == advice.proposal("T1").scenario


def test_r2_a_purchase_given_up_falls_back_to_the_rate_lock(repo):
    workspace = build_workspace(repo, _query(EARLY_NOVEMBER, 30, "0"))
    assert _ids(coordinator.coordinate(workspace, {})) == ["T1", "T2", "F1"]
    advice = coordinator.coordinate(_with_trouble_for_combinations(workspace), {})
    assert _ids(advice) == ["T1", "T2", "F2"]
    assert ("F1", "combined_below_buffer") in _suppressed(advice)
    assert ("F2", "exclusive_fx") not in _suppressed(advice)


# --------------------------------------------------------------------------- R3: tax reserve and investment deposit

def test_r3_two_deposits_over_different_money(repo):
    advice = _advice(repo, YEAR_END)
    [reserve_event] = advice.proposal("T1").events_to_add
    [deposit_event] = advice.proposal("I1").events_to_add
    assert (reserve_event.event_type, reserve_event.category) == (EventType.TERM_DEPOSIT, "tax_reserve")
    assert (deposit_event.event_type, deposit_event.category) == (EventType.TERM_DEPOSIT, "investment")
    assert (reserve_event.date_from, reserve_event.date_to) == (date(2026, 1, 1), date(2026, 2, 1))  # one month
    assert reserve_event.amount_eur + deposit_event.amount_eur == Decimal("26849.65") <= advice.surplus_eur
    # both together: 30,430.81 - 14,849.65 - 12,000 still keeps the 3,000 buffer
    assert advice.combined.risk.min_balance_eur == Decimal("3581.16")
    assert advice.combined.risk.status is RiskStatus.GREEN


# --------------------------------------------------------------------------- R4: one proposal per CHF instalment

def test_r4_purchase_when_it_fits_and_the_rate_lock_is_withheld(repo):
    advice = _advice(repo, EARLY_NOVEMBER, horizon=30, buffer="0")
    assert _ids(advice) == ["T1", "T2", "F1"]
    assert ("F2", "exclusive_fx") in _suppressed(advice)
    assert advice.proposal("F1").amount_eur == Decimal("8716.67")


def test_r4_rate_lock_when_the_purchase_does_not_fit(repo):
    advice = _advice(repo, MID_OCTOBER)
    assert _ids(advice) == ["T1", "T2", "F2"]
    assert ("F1", "over_budget") in _suppressed(advice)
    assert advice.proposal("F2").risk_removed_eur == Decimal("100.00")


def test_r4_no_rate_lock_for_a_single_instalment(repo):
    fits = _advice(repo, LATE_NOVEMBER)
    assert "F1" in _ids(fits) and "F2" not in {item.id for item in fits.suppressed}
    does_not_fit = _advice(repo, date(2025, 11, 15), horizon=30, buffer="8000")
    assert _ids(does_not_fit) == ["T1", "T2"]
    assert ("F1", "over_budget") in _suppressed(does_not_fit)
    assert Agent.FX in does_not_fit.facts  # the agent still has facts, just nothing to propose


def test_r4_never_both_fx_proposals(repo):
    for as_of in (RED_DAY, MID_OCTOBER, EARLY_NOVEMBER, LATE_NOVEMBER, YEAR_END):
        for buffer in ("0", "3000"):
            assert not {"F1", "F2"} <= set(_ids(_advice(repo, as_of, buffer=buffer)))


# --------------------------------------------------------------------------- R5: one investment deposit

def test_r5_only_the_longest_term_is_shown(repo):
    advice = _advice(repo, YEAR_END)
    [deposit] = advice.of_agent(Agent.INVESTMENT)
    assert (deposit.date_from, deposit.date_to) == (date(2026, 1, 1), date(2026, 7, 1))  # six months
    assert deposit.interest_eur == Decimal("119.01")
    shorter = [item for item in advice.suppressed if item.id == "I1"]
    assert [item.reason_code for item in shorter] == ["single_deposit", "single_deposit"]
    assert "2026-04-01" in shorter[0].reason and "2026-02-01" in shorter[1].reason


# --------------------------------------------------------------------------- R6: nothing is proposed twice

def test_r6_an_approved_tax_reserve_is_in_the_baseline_and_not_proposed_again(repo):
    before = _advice(repo, MID_OCTOBER)
    _decide(repo, before, ["T1"])
    after = _advice(repo, MID_OCTOBER)
    [event] = after.dashboard.events
    assert (event.event_type, event.category, event.source) == (EventType.TERM_DEPOSIT, "tax_reserve",
                                                                EventSource.AGENT)
    assert after.dashboard.risk.min_balance_eur == before.combined.risk.min_balance_eur
    assert _ids(after) == ["T2", "F2"]  # 802.91 still missing is below the 1,000 threshold
    assert after.facts[Agent.TAX]["already_set_aside"] == "€9,000"
    assert after.facts[Agent.TAX]["still_to_set_aside"] == "€803"
    assert after.surplus_eur == Decimal("1000")  # new forecast minimum 4,534.46 less the buffer, rounded down


def test_r6_a_later_day_asks_only_for_what_is_still_missing(repo):
    _decide(repo, _advice(repo, MID_OCTOBER), ["T1"])
    advice = _advice(repo, LATE_OCTOBER)
    assert advice.surplus_eur == Decimal("3000")  # the 9,000 deposit opened on 16 October stays tied up
    top_up = advice.proposal("T1")
    assert top_up.amount_eur == Decimal("1806.92")  # 10,806.92 - 9,000
    assert "You have already set aside €9,000, which leaves €1,807." in top_up.description


def test_r6_an_approved_investment_deposit_uses_up_the_surplus(repo):
    before = _advice(repo, YEAR_END)
    _decide(repo, before, ["T1", "I1"])
    after = _advice(repo, YEAR_END)
    assert _ids(after) == ["T2"]
    assert after.surplus_eur == 0 and after.suppressed == ()
    assert after.dashboard.risk.min_balance_eur == before.combined.risk.min_balance_eur


def test_r6_an_approved_purchase_silences_the_fx_agent(repo):
    before = _advice(repo, LATE_NOVEMBER)
    _decide(repo, before, ["F1"])
    after = _advice(repo, LATE_NOVEMBER)
    assert Agent.FX not in after.facts
    assert not after.of_agent(Agent.FX) and not [item for item in after.suppressed if item.agent is Agent.FX]
    assert not [item for day in after.dashboard.forecast.days for item in day.items
                if item.category == "investment_outflow"]


def test_r6_an_approved_rate_lock_silences_the_fx_agent_until_the_instalments_are_paid(repo):
    _decide(repo, _advice(repo, MID_OCTOBER), ["F2"])
    for as_of in (MID_OCTOBER, LATE_OCTOBER, LATE_NOVEMBER):
        advice = _advice(repo, as_of)
        assert Agent.FX not in advice.facts and not advice.of_agent(Agent.FX)


@pytest.mark.parametrize("status, reason", [(DecisionStatus.APPROVED, None), (DecisionStatus.REJECTED, "rejected")])
def test_r6_a_decided_rate_lock_stays_decided_when_an_instalment_is_postponed(repo, status, reason):
    _decide(repo, _advice(repo, MID_OCTOBER), ["F2"], status)
    repo.add_event(_zurich_deferral(date(2025, 11, 15), date(2026, 1, 15)))  # what rescue options B and E do
    advice = _advice(repo, MID_OCTOBER)
    assert advice.dashboard.risk.status is RiskStatus.GREEN
    assert "F2" not in _ids(advice)
    assert [code for proposal_id, code in _suppressed(advice) if proposal_id == "F2"] == ([reason] if reason else [])
    assert (Agent.FX in advice.facts) is (status is DecisionStatus.REJECTED)  # an approved lock silences the agent


def test_r6_a_postponement_outside_the_horizon_is_covered_by_the_purchase(repo):
    """A cash shortage in December is solved by postponing the 15 December instalment to February (option B).
    A shorter forecast no longer reaches that instalment; the FX agent must still know it was postponed."""
    november_10 = date(2025, 11, 10)
    repo.add_event(expense(date(2025, 12, 11), "30000"))
    repo.add_event(closure(date(2025, 12, 12), date(2026, 1, 7)))
    record_decision(repo, _query(november_10, 60, "0"), "B", DecisionStatus.APPROVED, None)
    [deferral] = [event for event in repo.list_events() if event.event_type is EventType.PAYMENT_DEFERRAL]
    assert (deferral.date_from, deferral.date_to) == (date(2025, 12, 15), date(2026, 2, 15))

    advice = _advice(repo, november_10, horizon=30, buffer="0")
    assert deferral not in advice.dashboard.events  # outside the 30-day forecast
    assert advice.facts[Agent.FX]["remaining_transfer_dates"] == ["2025-11-15", "2026-02-15"]
    assert advice.facts[Agent.FX] == _advice(repo, november_10, horizon=60, buffer="0").facts[Agent.FX]
    purchase = advice.proposal("F1")
    assert (purchase.amount_eur, purchase.date_to) == (Decimal("8716.67"), date(2026, 2, 15))

    _decide(repo, advice, ["F1"])
    later = build_workspace(repo, _query(date(2025, 12, 1), 90, "0")).dashboard
    assert not [item for day in later.forecast.days for item in day.items if item.category == "investment_outflow"]


def test_r6_approved_advice_is_not_repeated_but_the_other_wording_is_new(repo):
    _decide(repo, _advice(repo, YEAR_END), ["T2"])
    again = _advice(repo, YEAR_END)
    assert "T2" not in _ids(again) and ("T2", "already_decided") in _suppressed(again)
    assert ("T2", "already_decided") in _suppressed(_advice(repo, MID_OCTOBER))  # same tax year, same advice
    assert _ids(_advice(repo, RED_DAY)) == ["T2"]  # "save once the balance recovers" was never decided


def test_r6_a_rejected_tax_reserve_frees_its_budget(repo):
    before = _advice(repo, LATE_OCTOBER)
    assert _ids(before) == ["T1", "T2", "F2"]
    _decide(repo, before, ["T1"], DecisionStatus.REJECTED)
    after = _advice(repo, LATE_OCTOBER)
    assert after.dashboard.events == ()
    assert _ids(after) == ["T2", "F1"]
    assert _suppressed(after) == [("T1", "rejected"), ("I1", "below_threshold"), ("F2", "exclusive_fx")]


def test_r6_a_rejected_rate_lock_stays_away(repo):
    _decide(repo, _advice(repo, MID_OCTOBER), ["F2"], DecisionStatus.REJECTED)
    after = _advice(repo, MID_OCTOBER)
    assert not after.of_agent(Agent.FX) and ("F2", "rejected") in _suppressed(after)
    assert Agent.FX in after.facts


def test_r6_a_rejected_purchase_falls_back_to_the_rate_lock(repo):
    before = _advice(repo, EARLY_NOVEMBER, horizon=30, buffer="0")
    _decide(repo, before, ["F1"], DecisionStatus.REJECTED)
    after = _advice(repo, EARLY_NOVEMBER, horizon=30, buffer="0")
    assert [proposal.id for proposal in after.of_agent(Agent.FX)] == ["F2"]
    assert ("F1", "rejected") in _suppressed(after)


def test_r6_a_rejected_investment_deposit_is_not_shown_again(repo):
    _decide(repo, _advice(repo, YEAR_END), ["I1"], DecisionStatus.REJECTED)
    after = _advice(repo, YEAR_END)
    assert _ids(after) == ["T1", "T2"] and ("I1", "rejected") in _suppressed(after)


def test_r6_a_withdrawn_approval_brings_the_proposal_back(repo):
    before = _advice(repo, MID_OCTOBER)
    _decide(repo, before, ["T1"])
    repo.withdraw_decision(repo.list_decisions()[0].id, "Withdrawn by the owner (simulated).")
    assert _advice(repo, MID_OCTOBER) == before


# --------------------------------------------------------------------------- facts for the language model

def test_investment_facts_show_what_the_other_proposals_left_of_the_free_cash(repo):
    """The whole free cash never appears as the amount to deposit: the tax reserve and the francs come first."""
    november = _advice(repo, LATE_NOVEMBER)
    assert _ids(november) == ["T1", "T2", "F1"] and november.surplus_eur == Decimal("18000")
    facts = november.facts[Agent.INVESTMENT]
    assert facts["free_cash_already_proposed_for_tax_reserve_or_chf_purchase"] == "€17,319"
    assert facts["free_cash_left_for_a_deposit"] == "€0"
    assert facts["why_no_deposit_is_proposed"] == coordinator.REASONS["below_threshold"]

    year_end = _advice(repo, YEAR_END)
    assert _ids(year_end) == ["T1", "T2", "I1"] and year_end.surplus_eur == Decimal("27000")
    facts = year_end.facts[Agent.INVESTMENT]
    assert facts["free_cash_already_proposed_for_tax_reserve_or_chf_purchase"] == "€14,850"
    assert facts["free_cash_left_for_a_deposit"] == "€12,000"
    assert "why_no_deposit_is_proposed" not in facts

    for advice in (november, year_end):
        assert format_eur(advice.surplus_eur) not in advice.facts[Agent.INVESTMENT].values()


def test_investment_facts_name_the_rule_that_took_the_deposit_away(repo):
    _decide(repo, _advice(repo, YEAR_END), ["I1"], DecisionStatus.REJECTED)
    facts = _advice(repo, YEAR_END).facts[Agent.INVESTMENT]
    assert facts["free_cash_left_for_a_deposit"] == "€0"
    assert facts["why_no_deposit_is_proposed"] == coordinator.REASONS["rejected"]


# --------------------------------------------------------------------------- shape of the result

def test_proposals_are_ordered_tax_fx_investment(repo):
    assert _ids(_advice(repo, date(2025, 12, 8), horizon=30, buffer="0")) == ["T1", "T2", "F1", "I1"]


def test_every_reason_code_has_a_text():
    assert set(coordinator.REASONS) == {
        "liquidity_first", "over_budget", "below_threshold", "below_buffer", "combined_below_buffer",
        "exclusive_fx", "single_deposit", "already_decided", "rejected"}


def test_no_candidates_at_all_gives_an_empty_advice(repo):
    advice = _advice(repo, date(2025, 1, 3), horizon=30, buffer="0")
    assert advice.dashboard.risk.status is RiskStatus.GREEN
    assert (advice.proposals, advice.suppressed, dict(advice.facts)) == ((), (), {})
