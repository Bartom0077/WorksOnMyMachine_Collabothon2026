"""The coordinator: turns the candidates of the three agents into proposals that can be approved in ANY
combination. Conflicts are resolved here by a fixed rule table in code, never by the language model.

    R1  Liquidity first: while a liquidity problem is open nothing may tie up cash, and while the status is
        not GREEN the rate lock is withheld too (it would collide with postponing the Zurich transfers).
    R2  One shared budget: the cash-blocking proposals together never exceed the surplus. It is handed out
        in the order tax reserve -> CHF purchase -> investment deposit, and all of them are simulated together.
    R3  A deposit can be the tax reserve: T1 and I1 are two deposits over different money.
    R4  One proposal per CHF instalment: the purchase if it fits the budget, otherwise the rate lock.
    R5  One investment deposit: the longest term that keeps the buffer.
    R6  Nothing is proposed twice: approved plans are part of the baseline, and a proposal the owner already
        approved or rejected is not shown again.

Every candidate a rule removes is recorded in ``Advice.suppressed`` with a stable reason code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional

from advisors import fx, investment, tax
from advisors.fx import FxFacts
from advisors.model import (
    FX_FORWARD_ID,
    FX_PREBUY_ID,
    INVESTMENT_DEPOSIT_ID,
    TAX_ADVICE_ID,
    TAX_RESERVE_ID,
    ActionType,
    Advice,
    Agent,
    Proposal,
    SuppressedProposal,
)
from advisors.proposals import keeps_buffer, scenario_with
from advisors.tax import TaxPosition
from config import SURPLUS_INSIGHT_MIN_EUR, TAX_RESERVE_MIN_EUR
from domain import Dashboard, DecisionStatus, EventType, RiskStatus, Scenario
from kpi_engine.money import ZERO
from service import Workspace
from texts import floor_to_surplus_step, format_eur

# Reason codes of ``SuppressedProposal`` and the sentence that explains each (debugging only, never shown).
LIQUIDITY_FIRST = "liquidity_first"
OVER_BUDGET = "over_budget"
BELOW_THRESHOLD = "below_threshold"
BELOW_BUFFER = "below_buffer"
COMBINED_BELOW_BUFFER = "combined_below_buffer"
EXCLUSIVE_FX = "exclusive_fx"
SINGLE_DEPOSIT = "single_deposit"
ALREADY_DECIDED = "already_decided"
REJECTED = "rejected"

REASONS: Mapping[str, str] = {
    LIQUIDITY_FIRST: "A liquidity problem is open: nothing may tie up cash or compete with the rescue options.",
    OVER_BUDGET: "The amount does not fit into the free cash that is left after the proposals ranked before it.",
    BELOW_THRESHOLD: (f"Less than {format_eur(SURPLUS_INSIGHT_MIN_EUR)} of free cash is left for an investment "
                      f"deposit."),
    BELOW_BUFFER: "With this proposal the balance would fall below the safety buffer.",
    COMBINED_BELOW_BUFFER: "Together with the other proposals the balance would fall below the safety buffer.",
    EXCLUSIVE_FX: "The CHF purchase is on offer for the same instalments; only one of the two is shown.",
    SINGLE_DEPOSIT: ("Only one investment deposit is shown: the longest term that keeps the safety buffer within "
                     "the forecast."),
    ALREADY_DECIDED: "The owner has already approved this proposal.",
    REJECTED: "The owner has rejected this proposal.",
}

# The proposals in the order they are shown, with the agent and the action behind each id.
_CATALOGUE: Mapping[str, tuple[Agent, ActionType]] = {
    TAX_RESERVE_ID: (Agent.TAX, ActionType.TAX_RESERVE),
    TAX_ADVICE_ID: (Agent.TAX, ActionType.ADVICE),
    FX_PREBUY_ID: (Agent.FX, ActionType.FX_PREBUY),
    FX_FORWARD_ID: (Agent.FX, ActionType.FX_FORWARD),
    INVESTMENT_DEPOSIT_ID: (Agent.INVESTMENT, ActionType.TERM_DEPOSIT),
}

# R2: when the proposals do not fit together, the budget is taken back in the reverse order it was handed out.
_GIVE_UP_ORDER = (INVESTMENT_DEPOSIT_ID, FX_PREBUY_ID, TAX_RESERVE_ID)

_DECIDED_REASONS = {DecisionStatus.APPROVED: ALREADY_DECIDED, DecisionStatus.REJECTED: REJECTED}


@dataclass
class _Board:
    """Working state of one coordination run: what is on offer so far and what the rules removed."""

    workspace: Workspace
    decided: Mapping[str, DecisionStatus]
    liquidity_first: bool
    shown: dict[str, Proposal] = field(default_factory=dict)
    suppressed: list[SuppressedProposal] = field(default_factory=list)

    @property
    def dashboard(self) -> Dashboard:
        return self.workspace.dashboard

    def offer(self, proposal: Proposal) -> bool:
        """R6: show the proposal unless the owner has already decided on exactly this one."""
        status = self.decided.get(proposal.fingerprint)
        if status is not None:
            self.suppress(proposal.id, _DECIDED_REASONS[status])
            return False
        self.shown[proposal.id] = proposal
        return True

    def suppress(self, proposal_id: str, reason_code: str, detail: str = "") -> None:
        agent, action_type = _CATALOGUE[proposal_id]
        reason = " ".join(filter(None, [REASONS[reason_code], detail]))
        self.suppressed.append(SuppressedProposal(proposal_id, agent, action_type, reason_code, reason))

    def withdraw(self, proposal_id: str, reason_code: str) -> None:
        del self.shown[proposal_id]
        self.suppress(proposal_id, reason_code)

    def amount_of(self, proposal_id: str) -> Decimal:
        """The cash a proposal on offer takes; zero when it is not on offer."""
        proposal = self.shown.get(proposal_id)
        return ZERO if proposal is None or proposal.amount_eur is None else proposal.amount_eur

    def why_no_investment_deposit(self) -> Optional[str]:
        """The reason the investment deposit was removed, or None while one is on offer."""
        if INVESTMENT_DEPOSIT_ID in self.shown:
            return None
        removals = [item.reason for item in self.suppressed if item.id == INVESTMENT_DEPOSIT_ID]
        return removals[-1] if removals else None  # the last rule that spoke is the one that took it away

    def combined(self) -> Scenario:
        events = tuple(event for proposal in self.shown.values() for event in proposal.events_to_add)
        return scenario_with(self.workspace, events)


def coordinate(workspace: Workspace, decided: Mapping[str, DecisionStatus]) -> Advice:
    """Apply the rule table to the agents' candidates. ``decided`` maps proposal fingerprints to the owner's
    latest decision (``Repository.decision_fingerprint_statuses``)."""
    dashboard = workspace.dashboard
    liquidity_first = is_liquidity_first(dashboard)
    surplus = ZERO if liquidity_first else investment.free_cash(workspace)
    board = _Board(workspace, decided, liquidity_first)
    position = tax.assess(workspace)
    fx_facts = fx.assess(workspace, decided)

    budget = surplus
    budget -= _tax_reserve(board, position, budget)
    _tax_advice(board, position)
    budget -= _fx_prebuy(board, fx_facts, budget)
    _investment_deposit(board, surplus, budget)
    _fit_together(board)
    _fx_forward(board, fx_facts)

    return Advice(
        dashboard=dashboard,
        liquidity_first=liquidity_first,
        surplus_eur=surplus,
        proposals=tuple(board.shown[proposal_id] for proposal_id in _CATALOGUE if proposal_id in board.shown),
        suppressed=tuple(board.suppressed),
        facts=_facts(board, position, fx_facts, surplus),
        combined=board.combined(),
    )


def is_liquidity_first(dashboard: Dashboard) -> bool:
    """A liquidity problem is open: the status is not GREEN, or a credit line is in use within the horizon."""
    if dashboard.risk.status is not RiskStatus.GREEN:
        return True
    has_credit_line = any(event.event_type is EventType.CREDIT_LINE for event in dashboard.events)
    return has_credit_line and any(day.balance_eur < 0 for day in dashboard.forecast.days)


def _tax_reserve(board: _Board, position: TaxPosition, budget: Decimal) -> Decimal:
    """T1 (R1, R2, R3): the reserve still needed, capped to the budget. Returns the budget it takes."""
    if not position.wants_reserve_deposit:
        return ZERO
    if board.liquidity_first:
        board.suppress(TAX_RESERVE_ID, LIQUIDITY_FIRST)
        return ZERO
    amount = min(position.still_needed_eur, budget)
    if amount < TAX_RESERVE_MIN_EUR:
        board.suppress(TAX_RESERVE_ID, OVER_BUDGET)
        return ZERO
    return amount if board.offer(tax.reserve_deposit(board.workspace, position, amount)) else ZERO


def _tax_advice(board: _Board, position: TaxPosition) -> None:
    """T2: advice ties up no cash, so it is on offer whenever the tax agent has something to say."""
    if position.is_active:
        board.offer(tax.prepayment_advice(board.workspace, position, board.liquidity_first))


def _fx_prebuy(board: _Board, facts: Optional[FxFacts], budget: Decimal) -> Decimal:
    """F1 (R1, R2): the purchase cannot be reduced; it is shown only if all of it fits. Returns the budget
    it takes."""
    if facts is None:
        return ZERO
    if board.liquidity_first:
        board.suppress(FX_PREBUY_ID, LIQUIDITY_FIRST)
        return ZERO
    if facts.exposure_eur > budget:
        board.suppress(FX_PREBUY_ID, OVER_BUDGET)
        return ZERO
    return facts.exposure_eur if board.offer(fx.prebuy(board.workspace, facts)) else ZERO


def _investment_deposit(board: _Board, surplus: Decimal, budget: Decimal) -> None:
    """I1 (R2, R3, R5): what is left of the budget, in one deposit with the longest term that keeps the buffer."""
    if surplus <= 0:
        return  # no free cash at all: the investment agent has no candidate
    amount = floor_to_surplus_step(budget)
    if min(surplus, amount) < SURPLUS_INSIGHT_MIN_EUR:
        board.suppress(INVESTMENT_DEPOSIT_ID, BELOW_THRESHOLD)
        return
    candidates = investment.deposit_candidates(board.workspace, amount)
    if not candidates:
        board.suppress(INVESTMENT_DEPOSIT_ID, BELOW_BUFFER)
        return
    longest, *shorter = candidates
    for candidate in shorter:
        board.suppress(INVESTMENT_DEPOSIT_ID, SINGLE_DEPOSIT, f"Not shown: the term until {candidate.date_to}.")
    board.offer(longest)


def _fit_together(board: _Board) -> None:
    """R2, last step: all shown proposals are simulated together and must keep the buffer; otherwise the
    budget is taken back from the end of the priority order until they do."""
    for proposal_id in _GIVE_UP_ORDER:
        if keeps_buffer(board.combined(), board.dashboard.buffer_eur):
            return
        if proposal_id in board.shown:
            board.withdraw(proposal_id, COMBINED_BELOW_BUFFER)


def _fx_forward(board: _Board, facts: Optional[FxFacts]) -> None:
    """F2 (R1, R4): the rate lock is the fallback for the purchase and never appears next to it."""
    if facts is None or not facts.forward_is_worthwhile:
        return
    if board.dashboard.risk.status is not RiskStatus.GREEN:
        board.suppress(FX_FORWARD_ID, LIQUIDITY_FIRST)
    elif FX_PREBUY_ID in board.shown:
        board.suppress(FX_FORWARD_ID, EXCLUSIVE_FX)
    else:
        board.offer(fx.forward(board.workspace, facts))


def _facts(board: _Board, position: TaxPosition, fx_facts: Optional[FxFacts], surplus: Decimal
           ) -> dict[Agent, dict[str, Any]]:
    """The facts of every agent that has something to say, in the order tax, fx, investment. The investment
    facts are written from the finished board, so they show what the other proposals left of the free cash."""
    facts: dict[Agent, dict[str, Any]] = {}
    if position.is_active:
        facts[Agent.TAX] = tax.facts_document(position)
    if fx_facts is not None:
        facts[Agent.FX] = fx.facts_document(fx_facts)
    if surplus >= SURPLUS_INSIGHT_MIN_EUR:
        facts[Agent.INVESTMENT] = investment.facts_document(
            board.workspace,
            claimed_first_eur=board.amount_of(TAX_RESERVE_ID) + board.amount_of(FX_PREBUY_ID),
            deposit_eur=board.amount_of(INVESTMENT_DEPOSIT_ID),
            no_deposit_reason=board.why_no_investment_deposit())
    return facts
