"""Investment agent: term deposits with the bank for cash that is not needed during the forecast horizon.

The free cash is the surplus the dashboard already reports (``texts.surplus_eur``). A deposit term is a
candidate only if the re-simulated forecast with that deposit still keeps the owner's buffer.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from advisors.model import INVESTMENT_DEPOSIT_ID, ActionType, Agent, Proposal
from advisors.proposals import (
    INVESTMENT_CATEGORY,
    deposit_proposal,
    format_percent,
    keeps_buffer,
    opening_day,
    term_deposit_event,
)
from config import BANK_NAME, TERM_DEPOSIT_INTEREST_RATE, TERM_DEPOSIT_TERMS_MONTHS
from domain import EventDraft
from events import term_deposit_interest
from service import Workspace
from texts import format_day, format_eur, surplus_eur

_TERMS_ASSUMPTION = (f"Deposit terms of {', '.join(str(months) for months in TERM_DEPOSIT_TERMS_MONTHS)} months "
                     f"are assumed and the longest is shown.")


def free_cash(workspace: Workspace) -> Decimal:
    """Cash not needed for payments or the buffer during the horizon (rounded down, zero unless GREEN)."""
    dashboard = workspace.dashboard
    return surplus_eur(dashboard.risk, dashboard.current_balance_eur, dashboard.buffer_eur)


def facts_document(workspace: Workspace, claimed_first_eur: Decimal, deposit_eur: Decimal,
                   no_deposit_reason: Optional[str]) -> dict[str, Any]:
    """The investment agent's small JSON-ready facts; every amount is a formatted string.

    They describe the coordinated result, not the raw surplus: ``claimed_first_eur`` of the free cash is already
    on offer as a tax reserve or a CHF purchase, ``deposit_eur`` is what the deposit on offer takes (zero when
    there is none, with ``no_deposit_reason`` saying why). The whole free cash is left out on purpose, so no
    text can present it as the amount to deposit.
    """
    dashboard = workspace.dashboard
    document = {
        "balance_today": format_eur(dashboard.current_balance_eur),
        "lowest_projected_balance": format_eur(dashboard.risk.min_balance_eur),
        "lowest_projected_balance_date": dashboard.risk.min_balance_date.isoformat(),
        "safety_buffer": format_eur(dashboard.buffer_eur),
        "forecast_days": dashboard.horizon_days,
        "free_cash_already_proposed_for_tax_reserve_or_chf_purchase": format_eur(claimed_first_eur),
        "free_cash_left_for_a_deposit": format_eur(deposit_eur),
        "assumed_interest_rate_per_year": format_percent(TERM_DEPOSIT_INTEREST_RATE),
        "deposit_terms_months": list(TERM_DEPOSIT_TERMS_MONTHS),
    }
    if no_deposit_reason is None:
        return document
    return document | {"why_no_deposit_is_proposed": no_deposit_reason}


def deposit_candidates(workspace: Workspace, amount_eur: Decimal) -> tuple[Proposal, ...]:
    """One deposit of ``amount_eur`` per term that passes the buffer test, longest term first."""
    deposits = (_deposit(workspace, amount_eur, months) for months in sorted(TERM_DEPOSIT_TERMS_MONTHS, reverse=True))
    return tuple(deposit for deposit in deposits if keeps_buffer(deposit.scenario, workspace.dashboard.buffer_eur))


def _deposit(workspace: Workspace, amount_eur: Decimal, months: int) -> Proposal:
    dashboard = workspace.dashboard
    event = term_deposit_event(opening_day(workspace), months, amount_eur, INVESTMENT_CATEGORY, "Investment")
    return deposit_proposal(
        workspace,
        proposal_id=INVESTMENT_DEPOSIT_ID,
        agent=Agent.INVESTMENT,
        action_type=ActionType.TERM_DEPOSIT,
        title=f"Investment deposit of {format_eur(amount_eur)}",
        description=(f"About {format_eur(amount_eur)} is not needed for {_needs(dashboard.buffer_eur)} in the "
                     f"next {dashboard.horizon_days} days. "
                     f"Put it into a {months}-month term deposit with {BANK_NAME}: it earns "
                     f"{format_eur(term_deposit_interest(event))} interest and is back in the account on "
                     f"{format_day(event.date_to)}."),
        event=event,
        assumptions=(f"{_TERMS_ASSUMPTION} {_checked_period(workspace, event)}",),
    )


def _needs(buffer_eur: Decimal) -> str:
    """What the cash is measured against; a buffer of zero is not worth a mention."""
    if buffer_eur == 0:
        return "day-to-day payments"
    return f"day-to-day payments or your {format_eur(buffer_eur)} safety buffer"


def _checked_period(workspace: Workspace, deposit: EventDraft) -> str:
    """How far the buffer test behind the proposal reaches: it never looks past the forecast horizon."""
    dashboard = workspace.dashboard
    covered = f"The forecast only covers the next {dashboard.horizon_days} days"
    if deposit.date_to <= dashboard.forecast.days[-1].date:
        return f"{covered}, which includes the whole term."
    return f"{covered}: whether you can spare the money until {format_day(deposit.date_to)} is not checked."
