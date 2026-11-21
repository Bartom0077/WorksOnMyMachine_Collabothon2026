"""Tax agent: income tax only. Rising profits make the fixed quarterly prepayments too low; the agent
estimates the shortfall from the cash profit so far and proposes to set it aside.

The only rate used is derived from the documented obligation (prepayments per year / prior-year profit). The
data says nothing about when the shortfall is settled, so no text here names a date for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Sequence

from advisors.model import TAX_ADVICE_ID, TAX_RESERVE_ID, ActionType, Agent, Proposal
from advisors.proposals import (
    TAX_RESERVE_CATEGORY,
    baseline,
    content_fingerprint,
    deposit_proposal,
    opening_day,
    term_deposit_event,
)
from config import (
    ANNUAL_INCOME_TAX_PREPAYMENTS_EUR,
    BANK_NAME,
    DAYS_PER_YEAR,
    INCOME_TAX_PREPAYMENT_EUR,
    PRIOR_YEAR_PROFIT_BASE_EUR,
    TAX_PROFIT_EXCLUDED_CATEGORIES,
    TAX_RESERVE_DEPOSIT_TERM_MONTHS,
    TAX_RESERVE_MIN_EUR,
)
from domain import EventDraft, EventType, LedgerEntry
from kpi_engine.money import ZERO, cents
from service import Workspace
from texts import format_day, format_eur

_INCOME_TAX_PREPAYMENT_CATEGORY = "income_tax_advance"

_ESTIMATE_ASSUMPTION = ("The expected tax is an estimate: your cash profit so far this year, multiplied by the "
                        "share of profit that the current prepayments stand for.")
_TERM_ASSUMPTION = (f"A {TAX_RESERVE_DEPOSIT_TERM_MONTHS}-month term is assumed, short enough to keep the money "
                    f"within reach.")
_NO_CASH_MOVES = "No money moves and the balance forecast stays as it is."

# The two wordings of the advice; the variant is part of its fingerprint.
_RAISE_PREPAYMENTS = "raise_prepayments"
_SAVE_LATER = "save_once_recovered"


@dataclass(frozen=True)
class TaxPosition:
    """Where the business stands on income tax on the simulated "today" (year to date)."""

    year: int
    days_elapsed: int  # 1 January up to and including today
    cash_profit_eur: Decimal
    pro_rata_base_eur: Decimal  # the profit the prepayments assume for this part of the year
    estimated_tax_eur: Decimal
    prepayments_paid_eur: Decimal
    set_aside_eur: Decimal  # principal of the tax-reserve deposits that are already planned

    @property
    def reserve_eur(self) -> Decimal:
        """The expected shortfall: estimated tax that the prepayments paid so far do not cover."""
        return max(ZERO, self.estimated_tax_eur - self.prepayments_paid_eur)

    @property
    def still_needed_eur(self) -> Decimal:
        return max(ZERO, self.reserve_eur - self.set_aside_eur)

    @property
    def all_prepayments_paid(self) -> bool:
        return self.prepayments_paid_eur >= ANNUAL_INCOME_TAX_PREPAYMENTS_EUR

    @property
    def is_active(self) -> bool:
        """The agent speaks only when profit runs ahead of the prepayment base and the shortfall matters."""
        return self.cash_profit_eur > self.pro_rata_base_eur and self.reserve_eur >= TAX_RESERVE_MIN_EUR

    @property
    def wants_reserve_deposit(self) -> bool:
        return self.is_active and self.still_needed_eur >= TAX_RESERVE_MIN_EUR


def assess(workspace: Workspace) -> TaxPosition:
    dashboard = workspace.dashboard
    return tax_position(workspace.ledger, dashboard.as_of_date, dashboard.events)


def tax_position(ledger: Sequence[LedgerEntry], as_of: date, events: Sequence[EventDraft]) -> TaxPosition:
    """Year-to-date figures from the booked transactions up to ``as_of`` and the reserve deposits in ``events``."""
    year_start = date(as_of.year, 1, 1)
    this_year = [entry for entry in ledger if year_start <= entry.date <= as_of]
    profit = sum((entry.amount_eur for entry in this_year
                  if entry.category not in TAX_PROFIT_EXCLUDED_CATEGORIES), ZERO)
    days_elapsed = (as_of - year_start).days + 1
    return TaxPosition(
        year=as_of.year,
        days_elapsed=days_elapsed,
        cash_profit_eur=profit,
        pro_rata_base_eur=cents(PRIOR_YEAR_PROFIT_BASE_EUR * days_elapsed / DAYS_PER_YEAR),
        estimated_tax_eur=cents(profit * ANNUAL_INCOME_TAX_PREPAYMENTS_EUR / PRIOR_YEAR_PROFIT_BASE_EUR),
        prepayments_paid_eur=-sum((entry.amount_eur for entry in this_year
                                   if entry.category == _INCOME_TAX_PREPAYMENT_CATEGORY), ZERO),
        set_aside_eur=sum((event.amount_eur for event in events if _is_tax_reserve(event)), ZERO),
    )


def facts_document(position: TaxPosition) -> dict[str, Any]:
    """The tax agent's small JSON-ready facts; every amount is a formatted string."""
    return {
        "tax_year": position.year,
        "cash_profit_so_far": format_eur(position.cash_profit_eur),
        "profit_the_prepayments_assume_so_far": format_eur(position.pro_rata_base_eur),
        "prior_year_profit_behind_the_prepayments": format_eur(PRIOR_YEAR_PROFIT_BASE_EUR),
        "quarterly_prepayment": format_eur(INCOME_TAX_PREPAYMENT_EUR),
        "prepayments_per_year": format_eur(ANNUAL_INCOME_TAX_PREPAYMENTS_EUR),
        "estimated_tax_so_far": format_eur(position.estimated_tax_eur),
        "prepayments_paid_so_far": format_eur(position.prepayments_paid_eur),
        "expected_shortfall": format_eur(position.reserve_eur),
        "already_set_aside": format_eur(position.set_aside_eur),
        "still_to_set_aside": format_eur(position.still_needed_eur),
        "method": ("Estimated tax = cash profit so far x (prepayments per year / prior-year profit). The owner's "
                   "salary, the transfers to Zurich and the tax prepayments are not counted as costs."),
    }


def reserve_deposit(workspace: Workspace, position: TaxPosition, amount_eur: Decimal) -> Proposal:
    """T1: set ``amount_eur`` of the expected shortfall aside in a short term deposit."""
    event = term_deposit_event(opening_day(workspace), TAX_RESERVE_DEPOSIT_TERM_MONTHS, amount_eur,
                               TAX_RESERVE_CATEGORY, "Tax reserve")
    return deposit_proposal(
        workspace,
        proposal_id=TAX_RESERVE_ID,
        agent=Agent.TAX,
        action_type=ActionType.TAX_RESERVE,
        title=f"Tax reserve deposit of {format_eur(amount_eur)}",
        description=" ".join(filter(None, [
            _shortfall_sentence(position),
            _set_aside_sentence(position),
            f"Put {format_eur(amount_eur)} into a {TAX_RESERVE_DEPOSIT_TERM_MONTHS}-month term deposit with "
            f"{BANK_NAME} now, to keep it apart from your day-to-day money for the extra tax you may owe. The "
            f"deposit ends on {format_day(event.date_to)}; renew it then if the tax is still outstanding.",
            _coverage_sentence(position, amount_eur),
        ])),
        event=event,
        assumptions=(_TERM_ASSUMPTION, _ESTIMATE_ASSUMPTION),
    )


def prepayment_advice(workspace: Workspace, position: TaxPosition, liquidity_first: bool) -> Proposal:
    """T2: advice only. Cash is tight -> save once the balance recovers; otherwise -> ask for higher prepayments."""
    if liquidity_first:
        return _advice(
            workspace, position, _SAVE_LATER,
            title="Plan for an extra income tax bill",
            description=(f"{_shortfall_sentence(position)} Cash is tight right now, so do not lock any money "
                         f"away yet: start setting money aside for it once the balance recovers."),
            action="Reminder saved: start setting money aside for income tax once the balance recovers "
                   "(simulated).",
        )
    return _advice(
        workspace, position, _RAISE_PREPAYMENTS,
        title="Ask for higher income tax prepayments",
        description=(f"{_shortfall_sentence(position)} {_prepayments_paid_sentence(position)}Ask your tax "
                     f"advisor or the tax office to raise the quarterly prepayments (now "
                     f"{format_eur(INCOME_TAX_PREPAYMENT_EUR)}), {_raise_purpose(position)}."),
        action="Note saved: ask the tax advisor to have the income tax prepayments raised (simulated).",
    )


def _advice(workspace: Workspace, position: TaxPosition, variant: str, *, title: str, description: str,
            action: str) -> Proposal:
    return Proposal(
        id=TAX_ADVICE_ID,
        agent=Agent.TAX,
        action_type=ActionType.ADVICE,
        title=title,
        description=description,
        amount_eur=None,
        date_from=None,
        date_to=None,
        interest_eur=None,
        risk_removed_eur=None,
        effect=_NO_CASH_MOVES,
        assumptions=(_ESTIMATE_ASSUMPTION,),
        simulated_actions=(action,),
        events_to_add=(),
        scenario=baseline(workspace),
        fingerprint=content_fingerprint(TAX_ADVICE_ID, str(position.year), variant),
    )


def _is_tax_reserve(event: EventDraft) -> bool:
    return event.event_type is EventType.TERM_DEPOSIT and event.category == TAX_RESERVE_CATEGORY


def _shortfall_sentence(position: TaxPosition) -> str:
    return (f"Your cash profit so far this year is {format_eur(position.cash_profit_eur)}, while your income tax "
            f"prepayments assume only {format_eur(position.pro_rata_base_eur)} for this part of the year, so an "
            f"extra tax bill of about {format_eur(position.reserve_eur)} is likely.")


def _prepayments_paid_sentence(position: TaxPosition) -> str:
    return "All of this year's prepayments are paid. " if position.all_prepayments_paid else ""


def _raise_purpose(position: TaxPosition) -> str:
    """Why higher prepayments help. Once the year's last prepayment is paid, the known schedule holds no further
    one through which this year's difference could still be paid, so nothing of the kind is claimed."""
    if position.all_prepayments_paid:
        return "so they match your higher profit"
    return "so the difference is paid in steps and not as one large bill"


def _set_aside_sentence(position: TaxPosition) -> str:
    if position.set_aside_eur == 0:
        return ""
    return (f"You have already set aside {format_eur(position.set_aside_eur)}, which leaves "
            f"{format_eur(position.still_needed_eur)}.")


def _coverage_sentence(position: TaxPosition, amount_eur: Decimal) -> str:
    if amount_eur >= position.still_needed_eur:
        return ""
    return (f"This covers {format_eur(amount_eur)} of the {format_eur(position.still_needed_eur)} still needed: "
            f"more would cut into the cash you need for day-to-day payments and your safety buffer.")
