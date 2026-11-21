"""Building blocks shared by the domain agents: scenario evaluation, fingerprints, wording helpers and the
term-deposit proposal (an investment deposit and a tax reserve are the same instrument with a different purpose).

Like ``options._evaluate``, the effect of a proposal always comes from re-running the workspace's ``simulate``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from advisors.model import ActionType, Agent, Proposal
from config import BANK_NAME, TERM_DEPOSIT_INTEREST_RATE
from domain import EventDraft, EventType, RiskStatus, Scenario
from events import add_months, term_deposit_interest
from options import apply_changes, option_fingerprint
from service import Workspace
from texts import format_day, format_eur

# ``planned_events.category`` of a term deposit: what the money is set aside for.
INVESTMENT_CATEGORY = "investment"
TAX_RESERVE_CATEGORY = "tax_reserve"


def baseline(workspace: Workspace) -> Scenario:
    """The forecast as it stands, without any new proposal."""
    return Scenario(workspace.dashboard.forecast, workspace.dashboard.risk)


def scenario_with(workspace: Workspace, events_to_add: tuple[EventDraft, ...]) -> Scenario:
    """The forecast if ``events_to_add`` were approved on top of the plans that already exist."""
    if not events_to_add:
        return baseline(workspace)
    return workspace.simulate(apply_changes(workspace.dashboard.events, events_to_add, ()))


def keeps_buffer(scenario: Scenario, buffer_eur: Decimal) -> bool:
    """GREEN, and the real balance never drops below the buffer: tied-up cash must not lean on a credit line."""
    return (scenario.risk.status is RiskStatus.GREEN
            and min(day.balance_eur for day in scenario.forecast.days) >= buffer_eur)


def content_fingerprint(proposal_id: str, *content: str) -> str:
    """Fingerprint of a proposal that creates no planned event (see ``options.option_fingerprint`` for the rest)."""
    document = {"proposal": proposal_id, "content": list(content)}
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


def opening_day(workspace: Workspace) -> date:
    """Nothing can be arranged for a day that is already over: every action starts tomorrow."""
    return workspace.dashboard.as_of_date + timedelta(days=1)


def format_percent(rate: Decimal) -> str:
    return f"{(rate * 100).normalize():f}%"


def lowest_balance_sentence(scenario: Scenario) -> str:
    risk = scenario.risk
    return f"Lowest projected balance: {format_eur(risk.min_balance_eur)} on {format_day(risk.min_balance_date)}."


INTEREST_ASSUMPTION = (f"Interest at {format_percent(TERM_DEPOSIT_INTEREST_RATE)} p.a. "
                       f"(assumed rate, not a bank quote).")


def term_deposit_event(opening: date, months: int, amount_eur: Decimal, category: str, purpose: str) -> EventDraft:
    """A deposit of ``amount_eur`` opened on ``opening`` for ``months`` months; ``purpose`` starts its label."""
    return EventDraft(
        event_type=EventType.TERM_DEPOSIT,
        date_from=opening,
        date_to=add_months(opening, months),
        amount_eur=amount_eur,
        category=category,
        description=f"{purpose}: {months}-month term deposit of {format_eur(amount_eur)} with {BANK_NAME}",
    )


def deposit_proposal(workspace: Workspace, *, proposal_id: str, agent: Agent, action_type: ActionType, title: str,
                     description: str, event: EventDraft, assumptions: tuple[str, ...]) -> Proposal:
    """A term deposit as a proposal: simulated, with its interest, effect sentence and execution message."""
    scenario = scenario_with(workspace, (event,))
    interest = term_deposit_interest(event)
    return Proposal(
        id=proposal_id,
        agent=agent,
        action_type=action_type,
        title=title,
        description=description,
        amount_eur=event.amount_eur,
        date_from=event.date_from,
        date_to=event.date_to,
        interest_eur=interest,
        risk_removed_eur=None,
        effect=(f"{format_eur(event.amount_eur)} leaves the account on {format_day(event.date_from)} and comes "
                f"back on {format_day(event.date_to)} with {format_eur(interest)} interest. "
                f"{lowest_balance_sentence(scenario)}"),
        assumptions=(INTEREST_ASSUMPTION, *assumptions),
        simulated_actions=(f"{event.description} opened on {format_day(event.date_from)}, maturing on "
                           f"{format_day(event.date_to)} (simulated).",),
        events_to_add=(event,),
        scenario=scenario,
        fingerprint=option_fingerprint(proposal_id, (event,), ()),
    )
