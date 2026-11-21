"""Shared types of the CFO advisors. Plain immutable data, no I/O. All money is ``Decimal`` EUR.

``Advice`` is the contract between the deterministic part (agents + coordinator, ``advisors.engine``), the
language-model part (``advisors.llm_service``), the decision service and the REST layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Optional

from domain import Dashboard, EventDraft, ForecastDay, RiskStatus, Scenario


class Agent(StrEnum):
    TAX = "tax"
    FX = "fx"
    INVESTMENT = "investment"


class ActionType(StrEnum):
    """The closed list of actions an advisor may propose."""

    TERM_DEPOSIT = "term_deposit"  # investment deposit with the bank
    TAX_RESERVE = "tax_reserve"  # money set aside for income tax, held as a term deposit
    FX_PREBUY = "fx_prebuy"  # buy today the CHF for all remaining Zurich instalments
    FX_FORWARD = "fx_forward"  # lock the exchange rate for the remaining instalments (decision only)
    ADVICE = "advice"  # something to do or to discuss; no effect on the forecast (decision only)


# Actions that take cash out of the account; they share the surplus budget and yield to liquidity problems.
CASH_BLOCKING_ACTIONS = frozenset({ActionType.TERM_DEPOSIT, ActionType.TAX_RESERVE, ActionType.FX_PREBUY})

# Proposal ids. They are stored in ``owner_decisions.option_id`` (VARCHAR(5)) next to the options A..E.
TAX_RESERVE_ID = "T1"
TAX_ADVICE_ID = "T2"
FX_PREBUY_ID = "F1"
FX_FORWARD_ID = "F2"
INVESTMENT_DEPOSIT_ID = "I1"


@dataclass(frozen=True)
class Proposal:
    """One proposal the owner can approve or reject, alone or together with any other proposal on offer."""

    id: str
    agent: Agent
    action_type: ActionType
    title: str  # written by code; the language model may reword it
    description: str  # written by code (the fallback text); the language model may reword it
    amount_eur: Optional[Decimal]  # cash the action takes out of the account; None for fx_forward and advice
    date_from: Optional[date]  # deposit opening / purchase day; None for fx_forward and advice
    date_to: Optional[date]  # deposit maturity / last covered instalment; None for advice
    interest_eur: Optional[Decimal]  # deposits: interest earned until maturity (computed by code)
    # FX: the EUR amount that could no longer move with the exchange rate (None while only one rate was observed)
    risk_removed_eur: Optional[Decimal]
    effect: str  # one plain sentence about the outcome, written by code from the figures above
    assumptions: tuple[str, ...]  # illustrative assumptions behind the figures (assumed rate, ...)
    simulated_actions: tuple[str, ...]  # what approving it does; every line ends with "(simulated)."
    events_to_add: tuple[EventDraft, ...]  # planned events created on approval (empty for fx_forward / advice)
    scenario: Scenario  # the forecast with this proposal alone (equal to the baseline when it adds no event)
    fingerprint: str  # identifies the concrete proposal for the decision log (approved / rejected)

    @property
    def new_min_balance_eur(self) -> Decimal:
        return self.scenario.risk.min_balance_eur

    @property
    def new_min_balance_date(self) -> date:
        return self.scenario.risk.min_balance_date

    @property
    def new_status(self) -> RiskStatus:
        return self.scenario.risk.status

    @property
    def forecast(self) -> tuple[ForecastDay, ...]:
        return self.scenario.forecast.days


@dataclass(frozen=True)
class SuppressedProposal:
    """A candidate the coordinator's rules removed. Never shown to the owner; kept for debugging and tests."""

    id: str
    agent: Agent
    action_type: ActionType
    reason_code: str  # stable machine-readable code, e.g. "liquidity_first", "over_budget"
    reason: str


@dataclass(frozen=True)
class Advice:
    """Everything the advisors found for one simulated "today", after the coordinator resolved the conflicts.

    Any subset of ``proposals`` can be approved together: ``combined`` is the forecast with all of them.
    """

    dashboard: Dashboard
    liquidity_first: bool  # a liquidity problem is open: no proposal may tie up cash
    surplus_eur: Decimal  # shared budget of the cash-blocking proposals (0 when there is none)
    proposals: tuple[Proposal, ...]  # ordered tax, fx, investment
    suppressed: tuple[SuppressedProposal, ...]
    # Per agent: the small JSON-ready facts document for the language model. Present only for agents that have
    # something to say. Every amount of money in it is a STRING formatted with ``texts.format_eur`` (for
    # example "€9,803"), rates and foreign amounts are strings too ("1.046 EUR/CHF", "4,166.67 CHF"), so that
    # ``llm_recommendation.ungrounded_amounts`` accepts exactly the figures that appear in the document.
    facts: Mapping[Agent, Mapping[str, Any]]
    combined: Scenario

    def proposal(self, proposal_id: str) -> Optional[Proposal]:
        return next((proposal for proposal in self.proposals if proposal.id == proposal_id), None)

    def of_agent(self, agent: Agent) -> tuple[Proposal, ...]:
        return tuple(proposal for proposal in self.proposals if proposal.agent is agent)
