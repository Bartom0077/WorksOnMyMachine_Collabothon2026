"""Deterministic English texts: money/date formatting, the headline, insights and the fallback recommendation."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Optional, Sequence

from config import SURPLUS_INSIGHT_MIN_EUR, SURPLUS_ROUNDING_EUR
from domain import (
    Dashboard,
    EventType,
    FallbackRecommendation,
    Insight,
    PlannedEvent,
    RescueOption,
    RiskAssessment,
    RiskDriver,
    RiskStatus,
)

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_MAX_DRIVERS_IN_TEXT = 3


def format_eur(amount: Decimal) -> str:
    """Whole euros with thousands separators, e.g. ``€22,098`` or ``−€1,200``."""
    rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sign = "−" if rounded < 0 else ""
    return f"{sign}€{abs(rounded):,}"


def format_approx_eur(amount: Decimal) -> str:
    """Round to a memorable figure: nearest 1,000 from 10,000 upwards, nearest 100 below that."""
    step = Decimal("1000") if abs(amount) >= 10000 else Decimal("100")
    return format_eur((amount / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step)


def format_day(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]}"


def format_period(start: date, end: date) -> str:
    return format_day(start) if start == end else f"{format_day(start)} – {format_day(end)}"


def format_time_until(days: int) -> str:
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''}"
    return f"about {round(days / 7)} weeks"


def build_headline(risk: RiskAssessment, current_balance_eur: Decimal, horizon_days: int, buffer_eur: Decimal) -> str:
    """One plain sentence describing what will happen (also the fallback for the LLM headline)."""
    if risk.status is RiskStatus.RED:
        return _shortfall_headline(risk, current_balance_eur, horizon_days)
    if risk.status is RiskStatus.YELLOW:
        return _thin_buffer_headline(risk, current_balance_eur, horizon_days, buffer_eur)
    lowest = format_approx_eur(risk.min_balance_eur)
    if risk.credit_limit_eur is not None and risk.min_balance_eur < 0:
        return (f"You are covered for the next {horizon_days} days: the lowest projected balance (about {lowest}) "
                f"stays within your {format_eur(risk.credit_limit_eur)} credit line.")
    return f"You are in the clear for the next {horizon_days} days: the lowest projected balance is about {lowest}."


def _shortfall_headline(risk: RiskAssessment, current_balance_eur: Decimal, horizon_days: int) -> str:
    when = format_time_until(risk.days_until_min)
    shortfall = format_approx_eur(-risk.headroom_eur)
    beyond = " beyond your credit line" if risk.credit_limit_eur is not None else ""
    headroom_today = current_balance_eur - risk.floor_eur
    if headroom_today >= 0:
        return f"In {when} you will be about {shortfall} short{beyond}."
    short_today = format_approx_eur(-headroom_today)
    if risk.headroom_eur < headroom_today:
        return (f"You are already about {short_today} short{beyond} today, and in {when} this grows to about "
                f"{shortfall}.")
    return (f"You are already about {short_today} short{beyond} today. The next {horizon_days} days do not get "
            f"worse than that, but the forecast still shows about {shortfall} short in {when}.")


def _thin_buffer_headline(risk: RiskAssessment, current_balance_eur: Decimal, horizon_days: int,
                          buffer_eur: Decimal) -> str:
    when = format_time_until(risk.days_until_min)
    lowest = format_approx_eur(risk.min_balance_eur)
    buffer = format_eur(buffer_eur)
    if current_balance_eur - risk.floor_eur >= buffer_eur:
        return f"In {when} your balance will drop to about {lowest}, leaving less than your {buffer} safety buffer."
    if risk.min_balance_eur < current_balance_eur:
        return (f"Your balance is already below your {buffer} safety buffer today, and in {when} it will drop "
                f"further to about {lowest}.")
    return (f"Your balance (about {format_approx_eur(current_balance_eur)}) is already below your {buffer} safety "
            f"buffer today. It is not projected to fall further in the next {horizon_days} days: the lowest "
            f"projected balance is about {lowest}, in {when}.")


def floor_to_surplus_step(amount_eur: Decimal) -> Decimal:
    """Round down to whole ``SURPLUS_ROUNDING_EUR`` steps; never negative."""
    steps = (amount_eur / SURPLUS_ROUNDING_EUR).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return max(steps, Decimal("0")) * SURPLUS_ROUNDING_EUR


def surplus_eur(risk: RiskAssessment, current_balance_eur: Decimal, buffer_eur: Decimal) -> Decimal:
    """Cash that is not needed to keep the buffer during the horizon, rounded down; zero unless GREEN.

    The surplus is measured against the real balance, never against a credit line, so an arranged but unused
    line does not hide it. Shared by the surplus insight and the CFO advisors' budget.
    """
    if risk.status is not RiskStatus.GREEN:
        return Decimal("0")
    return floor_to_surplus_step(min(current_balance_eur, risk.min_balance_eur) - buffer_eur)


def build_insights(risk: RiskAssessment, current_balance_eur: Decimal, horizon_days: int, buffer_eur: Decimal
                   ) -> tuple[Insight, ...]:
    """Opportunities found by code. Currently: cash that is not needed to keep the buffer during the horizon."""
    surplus = surplus_eur(risk, current_balance_eur, buffer_eur)
    if surplus < SURPLUS_INSIGHT_MIN_EUR:
        return ()
    lowest = min(current_balance_eur, risk.min_balance_eur)
    detail = (f"You have {format_eur(current_balance_eur)} in the account and the balance is projected to stay "
              f"above {format_eur(lowest)} for the next {horizon_days} days. About {format_eur(surplus)} is not "
              f"needed for day-to-day payments or your safety buffer — consider a short-term deposit.")
    return (Insight(kind="surplus", title="Cash you could put to work", detail=detail, amount_eur=surplus),)


def build_fallback_recommendation(dashboard: Dashboard) -> FallbackRecommendation:
    """Texts written by code, shown when the LLM cannot provide a valid recommendation."""
    recommended = _recommended_option(dashboard)
    return FallbackRecommendation(
        headline=dashboard.headline,
        explanation=_explanation(dashboard),
        recommended_option_id=dashboard.code_recommended_option_id,
        rationale=_rationale(dashboard, recommended),
        option_comments={option.id: _option_comment(option) for option in dashboard.options},
    )


def _recommended_option(dashboard: Dashboard) -> Optional[RescueOption]:
    if dashboard.code_recommended_option_id is None:
        return None
    return dashboard.option(dashboard.code_recommended_option_id)


def _explanation(dashboard: Dashboard) -> str:
    risk = dashboard.risk
    opening = (f"Your balance is {format_eur(dashboard.current_balance_eur)} today and is projected to reach "
               f"its lowest point, {format_eur(risk.min_balance_eur)}, on {format_day(risk.min_balance_date)}.")
    if risk.status is RiskStatus.GREEN:
        return f"{opening} That keeps your {format_eur(dashboard.buffer_eur)} safety buffer intact. No action is needed."
    sentences = [opening, *_closure_sentences(dashboard.events, risk), _driver_sentence(dashboard.risk_drivers)]
    return " ".join(sentence for sentence in sentences if sentence)


def _closure_sentences(events: Sequence[PlannedEvent], risk: RiskAssessment) -> list[str]:
    closures = [event for event in events
                if event.event_type is EventType.CLOSURE and event.date_from <= risk.min_balance_date]
    return [f"While the pizzeria is closed ({format_period(event.date_from, event.date_to)}) there is no card "
            f"revenue, but the fixed costs keep running." for event in closures]


def _driver_sentence(drivers: Sequence[RiskDriver]) -> str:
    if not drivers:
        return ""
    listed = ", ".join(f"{driver.label} {format_eur(driver.total_eur)}" for driver in drivers[:_MAX_DRIVERS_IN_TEXT])
    return f"The largest fixed payments before that day: {listed}."


def _rationale(dashboard: Dashboard, recommended: Optional[RescueOption]) -> str:
    if recommended is None:
        if dashboard.options:
            return "Every available option has been rejected, so there is nothing left to recommend."
        return "No action is needed right now."
    outcome = (f"Option {recommended.id} ({recommended.title}) brings the lowest balance to "
               f"{format_eur(recommended.new_min_balance_eur)} at an estimated cost of "
               f"{format_eur(recommended.cost_eur)}.")
    if recommended.new_status is RiskStatus.GREEN:
        return f"{outcome} It is the cheapest option that keeps you safely above your limit."
    return f"{outcome} No single option fully restores a safe balance; this one gets closest at the lowest cost."


def _option_comment(option: RescueOption) -> str:
    """One line per option: its outcome in plain words with the engine's figures, the cost and the trade-off."""
    lowest = f"lowest balance {format_eur(option.new_min_balance_eur)} on {format_day(option.new_min_balance_date)}"
    comment = f"{_outcome_in_words(option)}: {lowest}. Estimated cost {format_eur(option.cost_eur)}."
    if option.non_financial_cost:
        comment = f"{comment} Trade-off: {option.non_financial_cost}."
    return comment


def _outcome_in_words(option: RescueOption) -> str:
    if option.new_status is RiskStatus.RED:
        return "Still short of money"
    if option.new_status is RiskStatus.YELLOW:
        return "No shortfall, but below your safety buffer"
    if option.new_min_balance_eur < 0:
        return "Safe within the credit line"
    return "Safe"
