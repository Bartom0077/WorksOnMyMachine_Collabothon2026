"""The decision service of the CFO advisors: what is stored when the owner approves or rejects proposals."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest

from advisors.decisions import record_advice_decisions
from advisors.engine import build_advice
from domain import DashboardQuery, DecisionStatus, EventSource, EventType, NewDecision
from errors import ProposalNotAvailableError
from fakes import expense
from kpi_engine.money import money_json
from repository import FINGERPRINT_KEY

DEC_31 = DashboardQuery(date(2025, 12, 31), 60, Decimal("3000"))
MAY_10 = DashboardQuery(date(2025, 5, 10), 60, Decimal("3000"))
APPROVED, REJECTED = DecisionStatus.APPROVED, DecisionStatus.REJECTED


def _balance_line(forecast) -> list[dict]:
    return [{"date": day.date.isoformat(), "balance_eur": money_json(day.balance_eur)} for day in forecast.days]


@pytest.fixture()
def stored(repo, monkeypatch) -> list[tuple[NewDecision, tuple]]:
    """Everything handed to ``Repository.record_decisions``: the fake keeps only the summary of a snapshot."""
    calls: list[tuple[NewDecision, tuple]] = []
    record_decisions = repo.record_decisions

    def spy(decisions):
        calls.extend(decisions)
        return record_decisions(decisions)

    monkeypatch.setattr(repo, "record_decisions", spy)
    return calls


def test_one_decision_per_proposal_in_the_order_they_are_shown(repo):
    advice = build_advice(repo, DEC_31)
    records = record_advice_decisions(repo, DEC_31, ["I1", "T2", "T1"], APPROVED, {"I1": "Earns a little interest."})
    assert [record.option_id for record in records] == ["T1", "T2", "I1"]
    for record in records:
        proposal = advice.proposal(record.option_id)
        assert (record.option_title, record.option_description) == (proposal.title, proposal.description)
        assert record.execution_messages == proposal.simulated_actions
        assert (record.as_of_date, record.horizon_days, record.buffer_eur, record.status) == (
            DEC_31.as_of_date, 60, Decimal("3000"), APPROVED)
    assert [record.llm_explanation for record in records] == [None, None, "Earns a little interest."]


def test_approval_creates_the_events_of_the_approved_proposals_only(repo):
    advice = build_advice(repo, DEC_31)
    [reserve, note] = record_advice_decisions(repo, DEC_31, ["T1", "T2"], APPROVED, {})
    [event] = repo.events
    [draft] = advice.proposal("T1").events_to_add
    assert (event.event_type, event.category, event.amount_eur, event.date_from, event.date_to, event.description) == (
        EventType.TERM_DEPOSIT, "tax_reserve", draft.amount_eur, draft.date_from, draft.date_to, draft.description)
    assert (event.source, event.as_of_date, event.decision_id) == (EventSource.AGENT, DEC_31.as_of_date, reserve.id)
    assert note.option_id == "T2"


def test_snapshot_has_the_audit_format_of_the_rescue_options(repo, stored):
    advice = build_advice(repo, DEC_31)
    record_advice_decisions(repo, DEC_31, ["T1", "T2", "I1"], APPROVED, {})
    assert [decision.option_id for decision, _ in stored] == ["T1", "T2", "I1"]
    for decision, events_to_add in stored:
        proposal = advice.proposal(decision.option_id)
        assert events_to_add == proposal.events_to_add
        assert decision.forecast_snapshot == {
            FINGERPRINT_KEY: proposal.fingerprint,
            "status_before": "GREEN",
            "min_balance_before_eur": 30430.81,
            "min_balance_date_before": "2026-01-01",
            "status_after": "GREEN",
            "min_balance_after_eur": 3581.16,
            "min_balance_date_after": "2026-01-01",
            "credit_limit_after_eur": None,
            "cost_eur": 0.0,
            "current_balance_eur": 33266.35,
            "forecast_before": _balance_line(advice.dashboard.forecast),
            "forecast_after": _balance_line(advice.combined.forecast),
        }


def test_outcome_of_an_approval_is_the_forecast_with_exactly_the_approved_proposals(repo, stored):
    advice = build_advice(repo, DEC_31)
    record_advice_decisions(repo, DEC_31, ["T1", "T2"], APPROVED, {})
    reserve_alone = advice.proposal("T1").scenario
    for decision, _ in stored:
        assert decision.forecast_snapshot["min_balance_after_eur"] == 15581.16
        assert decision.forecast_snapshot["forecast_after"] == _balance_line(reserve_alone.forecast)


def test_rejection_records_each_proposals_own_outcome_and_changes_no_plan(repo, stored):
    advice = build_advice(repo, DEC_31)
    records = record_advice_decisions(repo, DEC_31, ["T1", "I1"], REJECTED, {"T1": "Not now."})
    assert [(record.option_id, record.status, record.execution_messages, record.llm_explanation)
            for record in records] == [("T1", REJECTED, (), "Not now."), ("I1", REJECTED, (), None)]
    assert repo.events == []
    assert [events_to_add for _, events_to_add in stored] == [(), ()]
    for decision, _ in stored:
        proposal = advice.proposal(decision.option_id)
        assert decision.forecast_snapshot[FINGERPRINT_KEY] == proposal.fingerprint
        assert decision.forecast_snapshot["forecast_after"] == _balance_line(proposal.scenario.forecast)
    assert [decision.forecast_snapshot["min_balance_after_eur"] for decision, _ in stored] == [15581.16, 18430.81]


def test_decisions_are_remembered_by_fingerprint(repo):
    advice = build_advice(repo, DEC_31)
    record_advice_decisions(repo, DEC_31, ["T1"], APPROVED, {})
    record_advice_decisions(repo, DEC_31, ["T2"], REJECTED, {})
    assert repo.decision_fingerprint_statuses() == {advice.proposal("T1").fingerprint: APPROVED,
                                                    advice.proposal("T2").fingerprint: REJECTED}


@pytest.mark.parametrize("proposal_ids, missing", [
    (["F1"], "F1"),            # no CHF instalment is left at the end of the year
    (["T1", "F1", "F2"], "F1, F2"),  # one chosen proposal is on offer, two are not
    (["T9"], "T9"),
])
def test_nothing_is_stored_when_a_chosen_proposal_is_not_on_offer(repo, stored, proposal_ids, missing):
    with pytest.raises(ProposalNotAvailableError) as error:
        record_advice_decisions(repo, DEC_31, proposal_ids, APPROVED, {})
    assert error.value.message == f"Not currently offered for 2025-12-31: {missing}."
    assert (error.value.status_code, error.value.code) == (409, "proposal_not_available")
    assert stored == [] and repo.events == [] and repo.decisions == []


@pytest.mark.parametrize("status", [APPROVED, REJECTED])
def test_nothing_is_stored_when_a_chosen_proposal_changed_since_it_was_shown(repo, stored, status):
    seen = {proposal.id: proposal.fingerprint for proposal in build_advice(repo, DEC_31).proposals}
    repo.add_event(expense(date(2026, 1, 10), "8000"))  # leaves less free cash: I1 shrinks, T1 and T2 stay
    with pytest.raises(ProposalNotAvailableError) as error:
        record_advice_decisions(repo, DEC_31, ["T1", "T2", "I1"], status, {}, seen)
    assert error.value.message == "Changed since it was shown, for 2025-12-31: I1."
    assert stored == [] and repo.decisions == [] and len(repo.events) == 1

    records = record_advice_decisions(repo, DEC_31, ["T1", "T2"], status, {}, seen)  # these are what was shown
    assert [record.option_id for record in records] == ["T1", "T2"]


def test_a_suppressed_proposal_cannot_be_decided(repo):
    assert [proposal.id for proposal in build_advice(repo, MAY_10).proposals] == ["T2"]
    for status in (APPROVED, REJECTED):
        with pytest.raises(ProposalNotAvailableError):
            record_advice_decisions(repo, MAY_10, ["T1"], status, {})  # withheld: the liquidity problem comes first
    assert repo.decisions == []


def test_a_decided_proposal_is_no_longer_available(repo):
    record_advice_decisions(repo, DEC_31, ["I1"], REJECTED, {})
    for status in (APPROVED, REJECTED):
        with pytest.raises(ProposalNotAvailableError):
            record_advice_decisions(repo, DEC_31, ["I1"], status, {})
    assert len(repo.decisions) == 1


def test_check_and_write_run_under_the_decision_lock(repo, monkeypatch):
    held = []
    seen_inside_lock = []

    @contextmanager
    def lock():
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    def watched(name):
        original = getattr(repo, name)

        def call(*args):
            seen_inside_lock.append((name, bool(held)))
            return original(*args)

        monkeypatch.setattr(repo, name, call)

    monkeypatch.setattr(repo, "decision_lock", lock)
    for name in ("list_events", "decision_fingerprint_statuses", "record_decisions"):
        watched(name)
    record_advice_decisions(repo, DEC_31, ["T1"], APPROVED, {})
    assert {name for name, _ in seen_inside_lock} == {"list_events", "decision_fingerprint_statuses",
                                                      "record_decisions"}
    assert all(inside for _, inside in seen_inside_lock) and held == []
