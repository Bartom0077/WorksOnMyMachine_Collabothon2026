"""Pydantic models of the advisors' REST contract (GET /advice, POST /advice/recommendation, POST /advice/decisions).

Same conventions as ``schemas``: EUR numbers rounded to 2 decimals, ISO dates, responses built from the
dataclasses of ``advisors`` (``from_attributes``).
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advisors.model import ActionType, Advice, Agent
from domain import DecisionRecord, DecisionStatus, RiskStatus
from schemas import BalancePointOut, DecisionOut, Eur, OptionalEur, SimulationParams, _Response

MAX_PROPOSALS_PER_DECISION = 5  # T1, T2, F1 | F2 and I1 can be on offer together; one more leaves headroom

ProposalId = Annotated[str, Field(pattern=r"^[TFI][0-9]$")]
Explanation = Annotated[str, Field(max_length=10000)]
Fingerprint = Annotated[str, Field(min_length=1, max_length=64)]  # a sha256 hex digest


class AdviceDecisionRequest(SimulationParams):
    """The owner's answer to one or more proposals. Only ids travel: every figure is re-computed by the backend.

    An id names a slot (T1, I1, ...), not a concrete proposal. ``fingerprints`` says which proposal the owner
    looked at (``ProposalOut.fingerprint``); if the plans changed in between and the slot now holds another
    amount or other dates, the request is refused instead of storing a proposal the owner never saw.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_ids: list[ProposalId] = Field(min_length=1, max_length=MAX_PROPOSALS_PER_DECISION)
    status: DecisionStatus
    explanations: dict[ProposalId, Explanation] = Field(default_factory=dict)  # the text shown for each proposal
    fingerprints: dict[ProposalId, Fingerprint] = Field(default_factory=dict)  # of the proposals as shown

    @model_validator(mode="after")
    def _check_proposal_ids(self) -> AdviceDecisionRequest:
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("proposal_ids must not contain duplicates")
        for name, per_proposal in (("explanations", self.explanations), ("fingerprints", self.fingerprints)):
            unknown = sorted(set(per_proposal) - set(self.proposal_ids))
            if unknown:
                raise ValueError(f"{name} given for proposals that are not in proposal_ids: {', '.join(unknown)}")
        return self


class ProposalOut(_Response):
    id: str
    agent: Agent
    action_type: ActionType
    title: str
    description: str
    amount_eur: OptionalEur
    date_from: Optional[date]
    date_to: Optional[date]
    interest_eur: OptionalEur
    risk_removed_eur: OptionalEur
    effect: str
    assumptions: list[str]
    simulated_actions: list[str]
    new_min_balance_eur: Eur
    new_min_balance_date: date
    new_status: RiskStatus
    forecast: list[BalancePointOut]
    fingerprint: str  # identifies this concrete proposal; sent back with the decision


class SuppressedProposalOut(_Response):
    """A candidate the coordinator's rules removed. For debugging; the frontend does not show it."""

    id: str
    agent: Agent
    action_type: ActionType
    reason_code: str
    reason: str


class AdviceResponse(BaseModel):
    as_of_date: date
    horizon_days: int
    buffer_eur: Eur
    status: RiskStatus  # of the forecast as it stands, without any proposal
    liquidity_first: bool
    surplus_eur: Eur
    proposals: list[ProposalOut]
    suppressed: list[SuppressedProposalOut]

    @classmethod
    def from_advice(cls, advice: Advice) -> AdviceResponse:
        dashboard = advice.dashboard
        return cls(
            as_of_date=dashboard.as_of_date,
            horizon_days=dashboard.horizon_days,
            buffer_eur=dashboard.buffer_eur,
            status=dashboard.risk.status,
            liquidity_first=advice.liquidity_first,
            surplus_eur=advice.surplus_eur,
            proposals=[ProposalOut.model_validate(proposal) for proposal in advice.proposals],
            suppressed=[SuppressedProposalOut.model_validate(candidate) for candidate in advice.suppressed],
        )


class AdviceSummaryOut(_Response):
    source: Literal["llm", "fallback"]
    fallback_reason: Optional[str]
    headline: str
    text: str


class TipOut(_Response):
    title: str
    text: str


class AgentRecommendationOut(_Response):
    agent: Agent
    source: Literal["llm", "fallback"]
    fallback_reason: Optional[str]
    tips: list[TipOut]


class ProposalTextOut(_Response):
    title: str
    explanation: str
    recommended: bool


class AdviceRecommendationResponse(_Response):
    summary: AdviceSummaryOut
    agents: list[AgentRecommendationOut]
    proposals: dict[str, ProposalTextOut]


class AdviceDecisionsResponse(BaseModel):
    decisions: list[DecisionOut]
    execution_messages: list[str]  # of all decisions, in the order of ``decisions``

    @classmethod
    def from_records(cls, records: list[DecisionRecord]) -> AdviceDecisionsResponse:
        decisions = [DecisionOut.model_validate(record) for record in records]
        return cls(decisions=decisions,
                   execution_messages=[message for decision in decisions for message in decision.execution_messages])
