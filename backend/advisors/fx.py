"""FX agent: only the CHF instalments paid to Zurich. It knows what was paid so far (amount and rate are in
the payment reference) and which instalments the forecast still expects, and offers two ways to stop the EUR
amount of the remaining ones from moving: buy the francs now, or lock the rate.

The data holds no quotes, so both actions are assumed to happen at the last observed rate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Optional, Sequence

from advisors.model import FX_FORWARD_ID, FX_PREBUY_ID, ActionType, Agent, Proposal
from advisors.proposals import baseline, content_fingerprint, lowest_balance_sentence, opening_day, scenario_with
from config import BANK_NAME, FX_FORWARD_MIN_TRANCHES, FX_SMALL_SENSITIVITY_EUR
from domain import DecisionStatus, EventDraft, EventType, LedgerEntry
from events import ZURICH_TRANSFER_CATEGORY, add_months, deferred_dues
from forecast import project_recurring
from kpi_engine.fx import extract_rate
from kpi_engine.money import cents
from options import option_fingerprint
from service import Workspace
from texts import format_day, format_eur

_CURRENCY = "CHF"
_FOREIGN_AMOUNT = re.compile(rf"(\d+(?:[.,]\d+)?)\s*{_CURRENCY}", re.IGNORECASE)
_LOOKAHEAD_MONTHS = 12  # far enough to see every remaining monthly instalment, also beyond the forecast horizon
_AVERAGE_RATE_STEP = Decimal("0.0001")
_MIN_RATES_FOR_A_RANGE = 2
_NO_RATE_HISTORY = ("Only one transfer has been paid so far, so there is no rate history yet to judge how much "
                    "this cost could move.")


@dataclass(frozen=True)
class _PaidInstalment:
    chf: Decimal
    rate: Decimal  # EUR per 1 CHF


@dataclass(frozen=True)
class _RemainingInstalment:
    pay_date: date  # when the forecast expects it to be paid (the new date of a postponed instalment)
    due: date  # its regular due date, which a postponement does not change


@dataclass(frozen=True)
class FxFacts:
    """The remaining Zurich instalments and the exchange rates seen so far (never later than "today")."""

    remaining_dates: tuple[date, ...]  # when the forecast expects each remaining instalment to be paid
    last_regular_due: date  # regular due date of the last remaining instalment, however far it was postponed
    instalment_chf: Decimal
    last_rate: Decimal
    min_rate: Decimal
    max_rate: Decimal
    average_rate: Decimal
    observed_rates: int  # how many transfers the rates above come from

    @property
    def remaining_count(self) -> int:
        return len(self.remaining_dates)

    @property
    def last_date(self) -> date:
        return self.remaining_dates[-1]

    @property
    def remaining_chf(self) -> Decimal:
        return self.instalment_chf * self.remaining_count

    @property
    def exposure_eur(self) -> Decimal:
        """What the remaining instalments cost at the last observed rate."""
        return cents(self.remaining_chf * self.last_rate)

    @property
    def sensitivity_eur(self) -> Decimal:
        """How far that cost could move if the rate moved as much as it has so far."""
        return cents(self.remaining_chf * (self.max_rate - self.min_rate))

    @property
    def has_rate_history(self) -> bool:
        """One rate is no range: only from the second transfer on can the cost be said to have moved."""
        return self.observed_rates >= _MIN_RATES_FOR_A_RANGE

    @property
    def sensitivity_is_small(self) -> bool:
        return self.has_rate_history and self.sensitivity_eur < FX_SMALL_SENSITIVITY_EUR

    @property
    def risk_removed_eur(self) -> Optional[Decimal]:
        """The swing that fixing the cost takes away, or None while there is no rate history to measure it."""
        return self.sensitivity_eur if self.has_rate_history else None

    @property
    def forward_is_worthwhile(self) -> bool:
        return self.remaining_count >= FX_FORWARD_MIN_TRANCHES


def assess(workspace: Workspace, decided: Mapping[str, DecisionStatus]) -> Optional[FxFacts]:
    """The FX facts, or None when the agent has nothing to say: no instalment is left, or the remaining ones
    are already covered by an approved purchase or an approved rate lock."""
    events = workspace.active_events  # not only those in the horizon: the instalments reach beyond it
    if any(event.event_type is EventType.FX_PREBUY for event in events):
        return None
    facts = fx_facts(workspace.ledger, workspace.dashboard.as_of_date, events)
    if facts is None or decided.get(_forward_fingerprint(facts)) is DecisionStatus.APPROVED:
        return None
    return facts


def fx_facts(ledger: Sequence[LedgerEntry], as_of: date, events: Sequence[EventDraft]) -> Optional[FxFacts]:
    """Facts from the transfers booked up to ``as_of`` and the instalments the forecast engine would still pay.
    ``events`` are all plans that are not over yet, whether or not they lie in the forecast horizon."""
    paid = _paid_instalments(ledger, as_of)
    remaining = _remaining_instalments(ledger, as_of, events)
    if not paid or not remaining:
        return None
    rates = [instalment.rate for instalment in paid]
    return FxFacts(
        remaining_dates=tuple(instalment.pay_date for instalment in remaining),
        last_regular_due=max(instalment.due for instalment in remaining),
        instalment_chf=paid[-1].chf,
        last_rate=rates[-1],
        min_rate=min(rates),
        max_rate=max(rates),
        average_rate=(sum(rates) / len(rates)).quantize(_AVERAGE_RATE_STEP, rounding=ROUND_HALF_UP),
        observed_rates=len(rates),
    )


def facts_document(facts: FxFacts) -> dict[str, Any]:
    """The FX agent's small JSON-ready facts; amounts and rates are formatted strings."""
    document = {
        "remaining_transfers": facts.remaining_count,
        "remaining_transfer_dates": [day.isoformat() for day in facts.remaining_dates],
        "chf_per_transfer": _format_chf(facts.instalment_chf),
        "remaining_chf": _format_chf(facts.remaining_chf),
        "last_rate_paid": _format_rate(facts.last_rate),
        "remaining_cost_at_last_rate": format_eur(facts.exposure_eur),
    }
    if not facts.has_rate_history:
        return document | {"rate_history": _NO_RATE_HISTORY}
    return document | {
        "lowest_rate_paid_so_far": _format_rate(facts.min_rate),
        "highest_rate_paid_so_far": _format_rate(facts.max_rate),
        "average_rate_paid_so_far": _format_rate(facts.average_rate),
        "possible_swing": format_eur(facts.sensitivity_eur),
        "possible_swing_is_small": facts.sensitivity_is_small,
    }


def prebuy(workspace: Workspace, facts: FxFacts) -> Proposal:
    """F1: buy the francs for every remaining instalment tomorrow, at the last observed rate."""
    purchase_day = opening_day(workspace)
    transfers = "transfer" if facts.remaining_count == 1 else f"{facts.remaining_count} transfers"
    event = EventDraft(
        event_type=EventType.FX_PREBUY,
        date_from=purchase_day,
        date_to=facts.last_date,
        amount_eur=facts.exposure_eur,
        description=f"Swiss francs bought in advance for the remaining {transfers} to Zurich",
    )
    scenario = scenario_with(workspace, (event,))
    return Proposal(
        id=FX_PREBUY_ID,
        agent=Agent.FX,
        action_type=ActionType.FX_PREBUY,
        title="Buy the Swiss francs for Zurich now",
        description=(f"{_remaining_sentence(facts)} Buy all the francs from {BANK_NAME} now: "
                     f"{format_eur(event.amount_eur)} leaves "
                     f"the account on {format_day(purchase_day)} and the euro cost of the remaining {transfers} "
                     f"no longer depends on the exchange rate. {_swing_sentence(facts)}"),
        amount_eur=event.amount_eur,
        date_from=purchase_day,
        date_to=facts.last_date,
        interest_eur=None,
        risk_removed_eur=facts.risk_removed_eur,
        effect=(f"{format_eur(event.amount_eur)} leaves the account on {format_day(purchase_day)} in place of "
                f"the remaining {transfers}. {_risk_removed_sentence(facts)} {lowest_balance_sentence(scenario)}"),
        assumptions=(f"The francs are assumed to be bought at the last rate you paid, "
                     f"{_format_rate(facts.last_rate)} (assumed rate, not a bank quote).",),
        simulated_actions=(f"Order placed with {BANK_NAME}: buy {_format_chf(facts.remaining_chf)} for "
                           f"{format_eur(event.amount_eur)} on {format_day(purchase_day)} (simulated).",),
        events_to_add=(event,),
        scenario=scenario,
        fingerprint=option_fingerprint(FX_PREBUY_ID, (event,), ()),
    )


def forward(workspace: Workspace, facts: FxFacts) -> Proposal:
    """F2: lock the rate for the remaining instalments. A decision only: the balance forecast does not change."""
    return Proposal(
        id=FX_FORWARD_ID,
        agent=Agent.FX,
        action_type=ActionType.FX_FORWARD,
        title="Lock the exchange rate for Zurich",
        description=(f"{_remaining_sentence(facts)} Agree a forward contract with {BANK_NAME} that fixes the rate "
                     f"for all of them, so you know today that they cost {format_eur(facts.exposure_eur)} in "
                     f"total. No money leaves the account now. {_swing_sentence(facts)}"),
        amount_eur=None,
        date_from=None,
        date_to=facts.last_date,
        interest_eur=None,
        risk_removed_eur=facts.risk_removed_eur,
        effect=f"The balance forecast stays as it is. {_risk_removed_sentence(facts)}",
        assumptions=(f"The locked rate is assumed to equal the last rate you paid, "
                     f"{_format_rate(facts.last_rate)} (assumed rate, not a bank quote).",),
        simulated_actions=(f"Forward contract request sent to {BANK_NAME}: {_format_chf(facts.remaining_chf)} at "
                           f"{_format_rate(facts.last_rate)} (simulated).",),
        events_to_add=(),
        scenario=baseline(workspace),
        fingerprint=_forward_fingerprint(facts),
    )


def _forward_fingerprint(facts: FxFacts) -> str:
    """Stable while the same instalments are outstanding: it is tied to the regular due date of the last one,
    so postponing an instalment does not turn a locked rate into a new proposal."""
    return content_fingerprint(FX_FORWARD_ID, facts.last_regular_due.isoformat())


def _paid_instalments(ledger: Sequence[LedgerEntry], as_of: date) -> list[_PaidInstalment]:
    """Transfers to Zurich booked up to ``as_of`` whose reference states the CHF amount and the rate."""
    paid = []
    for entry in sorted(ledger, key=lambda item: item.date):
        if entry.date > as_of or entry.category != ZURICH_TRANSFER_CATEGORY:
            continue
        rate = extract_rate(entry.reference, _CURRENCY)
        amount = _FOREIGN_AMOUNT.search(entry.reference)
        if rate is not None and amount is not None:
            paid.append(_PaidInstalment(Decimal(amount.group(1).replace(",", ".")), rate))
    return paid


def _remaining_instalments(ledger: Sequence[LedgerEntry], as_of: date, events: Sequence[EventDraft]
                           ) -> list[_RemainingInstalment]:
    """The instalments still to come, in the order they are paid: the regular ones and the ones the owner
    postponed.

    The bank statement is historical and knows nothing of a simulated postponement: once the regular due date
    of a postponed instalment has passed, the statement shows it as paid, so it is no longer remaining.
    """
    projected = project_recurring(ledger, as_of, add_months(as_of, _LOOKAHEAD_MONTHS), deferred_dues(events))
    regular = [_RemainingInstalment(payment.date, payment.due) for payment in projected
               if payment.category == ZURICH_TRANSFER_CATEGORY]
    last_booked = max((entry.date for entry in ledger
                       if entry.category == ZURICH_TRANSFER_CATEGORY and entry.date <= as_of), default=date.min)
    postponed = [_RemainingInstalment(event.date_to, event.date_from) for event in events
                 if event.event_type is EventType.PAYMENT_DEFERRAL and event.category == ZURICH_TRANSFER_CATEGORY
                 and event.date_from > last_booked]
    return sorted(regular + postponed, key=lambda instalment: instalment.pay_date)


def _remaining_sentence(facts: FxFacts) -> str:
    if facts.remaining_count == 1:
        return (f"One transfer to Zurich is still to come: {_format_chf(facts.remaining_chf)} on "
                f"{format_day(facts.last_date)}.")
    return (f"{facts.remaining_count} transfers to Zurich are still to come: {_format_chf(facts.remaining_chf)} "
            f"in total, the last one on {format_day(facts.last_date)}.")


def _swing_sentence(facts: FxFacts) -> str:
    if not facts.has_rate_history:
        return _NO_RATE_HISTORY
    swing = (f"if the rate moved as much as it has so far this year, the cost would change by about "
             f"{format_eur(facts.sensitivity_eur)}")
    if facts.sensitivity_is_small:
        return f"To be honest, the possible swing is small: {swing}."
    return f"What is at stake: {swing}."


def _risk_removed_sentence(facts: FxFacts) -> str:
    if not facts.has_rate_history:
        return "The euro cost of what is still to be paid no longer depends on the exchange rate."
    return f"About {format_eur(facts.sensitivity_eur)} of possible exchange-rate swing disappears."


def _format_chf(amount: Decimal) -> str:
    return f"{amount:,.2f} {_CURRENCY}"


def _format_rate(rate: Decimal) -> str:
    return f"{rate} EUR/{_CURRENCY}"
