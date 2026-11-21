"""The advisors end to end on the real 2025 statement: the demo dates, the "any combination is safe" promise
over many dates, determinism, no future-data leakage and approvals becoming part of the baseline."""
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

import pytest

from advisors.engine import build_advice
from advisors.model import CASH_BLOCKING_ACTIONS, Advice, Agent
from advisors.proposals import scenario_with
from domain import DashboardQuery, DecisionStatus, EventDraft, EventSource, EventType, NewDecision, RiskStatus
from fakes import InMemoryRepository, closure, expense
from kpi_engine.models import Transaction
from kpi_engine.money import money_json
from repository import FINGERPRINT_KEY
from service import build_workspace

HORIZONS = (30, 60, 90)
BUFFERS = (Decimal("0"), Decimal("3000"), Decimal("8000"))
EVERY_FIFTH_DAY = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(0, 365, 5)]


def _advice(repo, as_of, horizon=60, buffer=Decimal("3000")) -> Advice:
    return build_advice(repo, DashboardQuery(as_of, horizon, buffer))


def _ids(advice: Advice) -> list[str]:
    return [proposal.id for proposal in advice.proposals]


def _cash_blocking(advice: Advice) -> list:
    return [proposal for proposal in advice.proposals if proposal.action_type in CASH_BLOCKING_ACTIONS]


def _outcome(advice: Advice) -> tuple:
    """Everything the advisors produce (the dashboard itself also reports the range of the loaded data)."""
    return (advice.liquidity_first, advice.surplus_eur, advice.proposals, advice.suppressed, dict(advice.facts),
            advice.combined)


def _approve(repo, advice: Advice) -> None:
    risk = advice.dashboard.risk
    decisions = []
    for proposal in advice.proposals:
        snapshot = {
            FINGERPRINT_KEY: proposal.fingerprint,
            "status_before": risk.status.value,
            "min_balance_before_eur": money_json(risk.min_balance_eur),
            "min_balance_date_before": risk.min_balance_date.isoformat(),
            "status_after": advice.combined.risk.status.value,
            "min_balance_after_eur": money_json(advice.combined.risk.min_balance_eur),
            "cost_eur": 0.0,
        }
        decisions.append((NewDecision(
            as_of_date=advice.dashboard.as_of_date, horizon_days=advice.dashboard.horizon_days,
            buffer_eur=advice.dashboard.buffer_eur, option_id=proposal.id, option_title=proposal.title,
            option_description=proposal.description, status=DecisionStatus.APPROVED, llm_explanation=None,
            execution_messages=proposal.simulated_actions, forecast_snapshot=snapshot), proposal.events_to_add))
    repo.record_decisions(decisions)


# --------------------------------------------------------------------------- the demo dates

def test_may_red_only_the_tax_warning(repo):
    advice = _advice(repo, date(2025, 5, 10))
    assert advice.dashboard.risk.status is RiskStatus.RED and advice.liquidity_first
    assert _ids(advice) == ["T2"] and not _cash_blocking(advice)
    assert [item.id for item in advice.suppressed] == ["T1", "F1", "F2"]
    assert list(advice.facts) == [Agent.TAX, Agent.FX]


def test_mid_october_tax_reserve_and_rate_lock_for_two_instalments(repo):
    advice = _advice(repo, date(2025, 10, 15))
    assert advice.dashboard.risk.status is RiskStatus.GREEN and not advice.liquidity_first
    assert advice.surplus_eur == Decimal("9000")
    assert _ids(advice) == ["T1", "T2", "F2"]
    reserve = advice.proposal("T1")
    assert (reserve.amount_eur, reserve.date_from, reserve.date_to, reserve.interest_eur) == (
        Decimal("9000"), date(2025, 10, 16), date(2025, 11, 16), Decimal("15.29"))
    assert (reserve.new_min_balance_eur, reserve.new_min_balance_date) == (Decimal("4534.46"), date(2025, 10, 16))
    assert advice.facts[Agent.FX]["remaining_transfers"] == 2
    assert list(advice.facts) == [Agent.TAX, Agent.FX]


def test_late_november_tax_reserve_and_chf_purchase(repo):
    advice = _advice(repo, date(2025, 11, 20))
    assert _ids(advice) == ["T1", "T2", "F1"]
    assert list(advice.facts) == [Agent.TAX, Agent.FX, Agent.INVESTMENT]


def test_year_end_no_fx_agent_tax_reserve_and_investment_deposit(repo):
    advice = _advice(repo, date(2025, 12, 31))
    assert advice.surplus_eur == Decimal("27000")
    assert _ids(advice) == ["T1", "T2", "I1"]
    assert list(advice.facts) == [Agent.TAX, Agent.INVESTMENT]
    assert (advice.proposal("T1").amount_eur, advice.proposal("I1").amount_eur) == (
        Decimal("14849.65"), Decimal("12000"))
    assert not [item for item in advice.suppressed if item.agent is Agent.FX]


def test_code_written_texts_follow_the_house_rules(repo):
    for as_of in (date(2025, 5, 10), date(2025, 10, 15), date(2025, 11, 20), date(2025, 12, 31)):
        for proposal in _advice(repo, as_of).proposals:
            assert proposal.simulated_actions
            assert all(action.endswith("(simulated).") for action in proposal.simulated_actions)
            assert proposal.title and proposal.description and proposal.effect
            if proposal.action_type in CASH_BLOCKING_ACTIONS or proposal.id == "F2":
                assert "Commerzbank" in proposal.description
                assert any("assumed rate, not a bank quote" in assumption for assumption in proposal.assumptions)


# --------------------------------------------------------------------------- any combination is safe

def _check_invariants(repo, query: DashboardQuery) -> int:
    advice = build_advice(repo, query)
    context = (query.as_of_date, query.horizon_days, query.buffer_eur)
    assert advice == build_advice(repo, query), context  # (d) deterministic
    ids = _ids(advice)
    assert len(ids) == len(set(ids)) and not {"F1", "F2"} <= set(ids), context  # (c)
    blocking = _cash_blocking(advice)
    if advice.liquidity_first:
        assert not blocking and advice.surplus_eur == 0, context  # (c)
    assert sum((proposal.amount_eur for proposal in blocking), Decimal("0")) <= advice.surplus_eur, context  # (b)
    if not blocking:
        return 0
    workspace = build_workspace(repo, query)
    subsets = [subset for size in range(1, len(advice.proposals) + 1)
               for subset in combinations(advice.proposals, size)
               if any(proposal in blocking for proposal in subset)]
    for subset in subsets:  # (a)
        events = tuple(event for proposal in subset for event in proposal.events_to_add)
        scenario = scenario_with(workspace, events)
        assert scenario.risk.status is RiskStatus.GREEN, (context, [proposal.id for proposal in subset])
        assert min(day.balance_eur for day in scenario.forecast.days) >= query.buffer_eur, context
    return len(subsets)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_every_subset_of_the_shown_proposals_is_safe(repo, horizon):
    checked = sum(_check_invariants(repo, DashboardQuery(as_of, horizon, buffer))
                  for as_of in EVERY_FIFTH_DAY for buffer in BUFFERS)
    assert checked > 50  # the property was really exercised


def test_invariants_hold_with_plans_and_a_credit_line_in_the_baseline(repo):
    repo.add_event(closure(date(2025, 6, 1), date(2025, 6, 21)))
    repo.add_event(expense(date(2025, 11, 10), "6000"))
    repo.add_event(EventDraft(event_type=EventType.CREDIT_LINE, date_from=date(2025, 5, 11),
                              date_to=date(2026, 5, 11), amount_eur=Decimal("30000"), description="Credit line"))
    for as_of in EVERY_FIFTH_DAY:
        _check_invariants(repo, DashboardQuery(as_of, 60, Decimal("3000")))


# --------------------------------------------------------------------------- no future leakage

@pytest.mark.parametrize("as_of", [date(2025, 5, 10), date(2025, 10, 15), date(2025, 11, 20)])
def test_transactions_after_today_do_not_change_the_advice(statement, anchors, repo, as_of):
    expected = _outcome(_advice(repo, as_of))
    known_only = InMemoryRepository([tx for tx in statement if tx.booking_date <= as_of], anchors)
    assert _outcome(_advice(known_only, as_of)) == expected
    windfall = Transaction(id=10 ** 6, booking_date=as_of + timedelta(days=1), amount=Decimal("250000"),
                           currency="EUR", transaction_type="CREDIT CARD PAYOUT",
                           payment_reference="Unexpected payout", beneficiary_payer="Card acquirer")
    late_transfer = Transaction(id=10 ** 6 + 1, booking_date=as_of + timedelta(days=2), amount=Decimal("-4166.67"),
                                currency="CHF", transaction_type="INTERNATIONAL WIRE TRANSFER",
                                payment_reference="Silent Partnership Capital Tranche 12/12 "
                                                  "(4166.67 CHF @ 1.900 EUR/CHF)",
                                beneficiary_payer="Luigi Amalfi")
    with_future = InMemoryRepository([*statement, windfall, late_transfer], anchors)
    assert _outcome(_advice(with_future, as_of)) == expected


# --------------------------------------------------------------------------- approvals become the baseline

@pytest.mark.parametrize("as_of, approved_ids", [
    (date(2025, 10, 15), ["T1", "T2", "F2"]),
    (date(2025, 11, 20), ["T1", "T2", "F1"]),
    (date(2025, 12, 31), ["T1", "T2", "I1"]),
])
def test_approved_proposals_join_the_baseline_and_are_not_proposed_again(repo, as_of, approved_ids):
    before = _advice(repo, as_of)
    assert _ids(before) == approved_ids
    _approve(repo, before)
    after = _advice(repo, as_of)
    planned = [event for proposal in before.proposals for event in proposal.events_to_add]
    assert [(event.event_type, event.amount_eur, event.source) for event in after.dashboard.events] == [
        (event.event_type, event.amount_eur, EventSource.AGENT) for event in planned]
    assert after.dashboard.forecast.days == before.combined.forecast.days
    assert after.dashboard.risk == before.combined.risk
    assert after.proposals == () and not _cash_blocking(after)
    assert [(item.id, item.reason_code) for item in after.suppressed] in (
        [("T2", "already_decided")], [("T2", "already_decided"), ("I1", "below_threshold")])
    assert after.combined.forecast == after.dashboard.forecast
