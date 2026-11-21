"""Owner decisions on the advisors' proposals: several of them approved or rejected in one step.

Like ``service.record_decision`` the proposals are re-computed here and client-side numbers are never trusted.
All chosen proposals are stored together or not at all.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from advisors.coordinator import coordinate
from advisors.model import Advice, Proposal
from advisors.proposals import scenario_with
from domain import DashboardQuery, DecisionRecord, DecisionStatus, NewDecision, RescueOption, Scenario
from errors import ProposalNotAvailableError
from kpi_engine.money import ZERO
from repository import Repository
from service import Workspace, _forecast_snapshot, build_workspace


def record_advice_decisions(repository: Repository, query: DashboardQuery, proposal_ids: Sequence[str],
                            status: DecisionStatus, explanations: Mapping[str, str],
                            fingerprints: Optional[Mapping[str, str]] = None) -> list[DecisionRecord]:
    """Store one decision per chosen proposal, in the order the proposals are shown.

    The check and the write run under the decision lock, so two simultaneous approvals cannot both pass. If any
    chosen proposal is not on offer, nothing is stored. ``fingerprints`` (proposal id -> the fingerprint of the
    proposal the owner saw) makes "on offer" exact: an id whose proposal changed since then is not on offer.
    """
    with repository.decision_lock():
        workspace = build_workspace(repository, query)
        advice = coordinate(workspace, repository.decision_fingerprint_statuses())
        chosen = _chosen(advice, proposal_ids, fingerprints or {})
        approved = status is DecisionStatus.APPROVED
        together = _approved_together(workspace, chosen) if approved else None
        return repository.record_decisions([
            (_decision(advice, proposal, status, explanations.get(proposal.id), together or proposal.scenario),
             proposal.events_to_add if approved else ())
            for proposal in chosen
        ])


def _chosen(advice: Advice, proposal_ids: Sequence[str], seen: Mapping[str, str]) -> tuple[Proposal, ...]:
    missing = sorted(set(proposal_ids) - {proposal.id for proposal in advice.proposals})
    if missing:
        raise ProposalNotAvailableError(
            f"Not currently offered for {advice.dashboard.as_of_date}: {', '.join(missing)}.")
    chosen = tuple(proposal for proposal in advice.proposals if proposal.id in proposal_ids)
    changed = [proposal.id for proposal in chosen
               if seen.get(proposal.id, proposal.fingerprint) != proposal.fingerprint]
    if changed:
        raise ProposalNotAvailableError(
            f"Changed since it was shown, for {advice.dashboard.as_of_date}: {', '.join(changed)}.")
    return chosen


def _approved_together(workspace: Workspace, chosen: Sequence[Proposal]) -> Scenario:
    """The forecast once every approved proposal is in the plan: the outcome each of their decisions records."""
    return scenario_with(workspace, tuple(event for proposal in chosen for event in proposal.events_to_add))


def _decision(advice: Advice, proposal: Proposal, status: DecisionStatus, explanation: Optional[str],
              outcome: Scenario) -> NewDecision:
    dashboard = advice.dashboard
    return NewDecision(
        as_of_date=dashboard.as_of_date,
        horizon_days=dashboard.horizon_days,
        buffer_eur=dashboard.buffer_eur,
        option_id=proposal.id,
        option_title=proposal.title,
        option_description=proposal.description,
        status=status,
        llm_explanation=explanation,
        execution_messages=proposal.simulated_actions if status is DecisionStatus.APPROVED else (),
        forecast_snapshot=_forecast_snapshot(dashboard, _audit_view(proposal, outcome)),
    )


def _audit_view(proposal: Proposal, outcome: Scenario) -> RescueOption:
    """The proposal in the shape ``service._forecast_snapshot`` documents, so the decision log has one format
    for rescue options and advisor proposals. A proposal costs no interest and changes no existing plan."""
    return RescueOption(
        id=proposal.id,
        title=proposal.title,
        description=proposal.description,
        cost_eur=ZERO,
        cost_note=None,
        non_financial_cost=None,
        simulated_actions=proposal.simulated_actions,
        events_to_add=proposal.events_to_add,
        event_updates=(),
        scenario=outcome,
        pain_rank=0,
        rejected=False,
        fingerprint=proposal.fingerprint,
    )
