"""Application services: assemble the dashboard, the LLM dossier and owner decisions from the core modules."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from config import HISTORY_DAYS
from domain import (
    Dashboard,
    DataRange,
    DashboardQuery,
    DecisionRecord,
    DecisionStatus,
    EventDraft,
    ForecastDay,
    NewDecision,
    PlannedEvent,
    RescueOption,
    Scenario,
)
from errors import AsOfDateOutOfRangeError, DataUnavailableError, NotFoundError, OptionNotAvailableError
from forecast import build_forecast
from kpi_engine.money import money_json
from ledger import balance_on, build_ledger, daily_balances, opening_anchor
from options import OptionContext, build_options, recommend
from repository import FINGERPRINT_KEY, Repository, StaleEventError
from risk import assess_risk, risk_drivers
from texts import build_fallback_recommendation, build_headline, build_insights

__all__ = ["build_dashboard", "build_dossier", "build_fallback_recommendation", "record_decision", "delete_event"]

DECISION_WITHDRAWN_NOTE = ("Withdrawn by the owner: the planned changes of this option were removed from the "
                           "forecast (simulated).")
_WEEK_DAYS = 7


def build_dashboard(repository: Repository, query: DashboardQuery) -> Dashboard:
    """Compute everything the main screen shows for one simulated "today". Deterministic, no LLM."""
    data_range = _checked_data_range(repository, query.as_of_date)
    ledger = build_ledger(repository.load_transactions(query.as_of_date))
    anchor = opening_anchor(repository.load_anchors(query.as_of_date))
    balance = balance_on(ledger, anchor, query.as_of_date)
    events = _relevant_events(repository.list_events(), query)

    def simulate(scenario_events: Sequence[EventDraft]) -> Scenario:
        forecast = build_forecast(ledger, query.as_of_date, query.horizon_days, balance, scenario_events)
        return Scenario(forecast, assess_risk(forecast.days, query.as_of_date, query.buffer_eur))

    baseline = simulate(events)
    options = build_options(OptionContext(
        as_of=query.as_of_date, buffer_eur=query.buffer_eur, events=events, baseline=baseline, simulate=simulate,
        rejected_fingerprints=repository.rejected_option_fingerprints(),
    ))
    history_start = max(query.as_of_date - timedelta(days=HISTORY_DAYS - 1), anchor.anchor_date)
    return Dashboard(
        as_of_date=query.as_of_date,
        horizon_days=query.horizon_days,
        buffer_eur=query.buffer_eur,
        data_range=data_range,
        current_balance_eur=balance,
        history=daily_balances(ledger, anchor, history_start, query.as_of_date),
        forecast=baseline.forecast,
        risk=baseline.risk,
        risk_drivers=risk_drivers(baseline.forecast.days, baseline.risk.risk_window),
        headline=build_headline(baseline.risk, balance, query.horizon_days, query.buffer_eur),
        events=events,
        options=options,
        code_recommended_option_id=recommend(options),
        insights=build_insights(baseline.risk, balance, query.horizon_days, query.buffer_eur),
    )


def build_dossier(dashboard: Dashboard) -> dict:
    """The compact JSON document handed to the LLM for its recommendation (ROZWIAZANIE.md §11)."""
    risk = dashboard.risk
    averages = dashboard.forecast.daily_averages
    return {
        "today": dashboard.as_of_date.isoformat(),
        "balance_today": money_json(dashboard.current_balance_eur),
        "horizon_days": dashboard.horizon_days,
        "buffer_eur": money_json(dashboard.buffer_eur),
        "status": risk.status.value,
        "min_balance": money_json(risk.min_balance_eur),
        "min_balance_date": risk.min_balance_date.isoformat(),
        "credit_limit_eur": money_json(risk.credit_limit_eur),
        "events": [_dossier_event(event) for event in dashboard.events],
        "daily_averages": {"revenue": money_json(averages.revenue_eur),
                           "variable_costs": money_json(averages.variable_costs_eur)},
        "fixed_costs_in_risk_window": [
            {"category": driver.category, "label": driver.label, "total": money_json(driver.total_eur),
             "deferrable": driver.deferrable}
            for driver in dashboard.risk_drivers
        ],
        "weekly_balance": _weekly_balance(dashboard.current_balance_eur, dashboard.forecast.days),
        "options": [_dossier_option(option) for option in dashboard.options],
        "code_recommendation": dashboard.code_recommended_option_id,
    }


def record_decision(repository: Repository, query: DashboardQuery, option_id: str, status: DecisionStatus,
                    llm_explanation: Optional[str]) -> DecisionRecord:
    """Store the owner's decision. The option is re-computed here; client-side numbers are never trusted.

    The check and the write run under the decision lock, so two simultaneous approvals cannot both pass.
    """
    with repository.decision_lock():
        dashboard = build_dashboard(repository, query)
        option = dashboard.option(option_id)
        if option is None:
            raise OptionNotAvailableError(f"Option {option_id} is not currently offered for {query.as_of_date}.")
        approved = status is DecisionStatus.APPROVED
        if option.rejected and not approved:
            raise OptionNotAvailableError(f"Option {option_id} has already been rejected.")
        decision = NewDecision(
            as_of_date=query.as_of_date,
            horizon_days=query.horizon_days,
            buffer_eur=query.buffer_eur,
            option_id=option.id,
            option_title=option.title,
            option_description=option.description,
            status=status,
            llm_explanation=llm_explanation,
            execution_messages=option.simulated_actions if approved else (),
            forecast_snapshot=_forecast_snapshot(dashboard, option),
        )
        try:
            return repository.record_decision(
                decision,
                events_to_add=option.events_to_add if approved else (),
                event_updates=option.event_updates if approved else (),
            )
        except StaleEventError as exc:
            raise OptionNotAvailableError(f"Option {option_id} is no longer available: {exc}") from exc


def delete_event(repository: Repository, event_id: int) -> None:
    """Remove a plan. An event created by an approved option is never removed alone: the option's changes
    belong together, so all of them go and the decision log says that the option was withdrawn."""
    event = next((event for event in repository.list_events() if event.id == event_id), None)
    if event is None:
        raise NotFoundError(f"Planned event {event_id} does not exist.")
    if event.decision_id is not None:
        repository.withdraw_decision(event.decision_id, DECISION_WITHDRAWN_NOTE)
    elif not repository.delete_event(event_id):
        raise NotFoundError(f"Planned event {event_id} does not exist.")


def _checked_data_range(repository: Repository, as_of: date) -> DataRange:
    data_range = repository.data_range()
    if data_range is None:
        raise DataUnavailableError("No bank transactions are loaded yet.")
    if not data_range.first_date <= as_of <= data_range.last_date:
        raise AsOfDateOutOfRangeError(
            f"as_of_date must be between {data_range.first_date} and {data_range.last_date} (the bank data range)."
        )
    return data_range


def _relevant_events(events: Sequence[PlannedEvent], query: DashboardQuery) -> tuple[PlannedEvent, ...]:
    """Events that still lie (at least partly) in the forecast horizon; past plans no longer matter."""
    horizon_end = query.as_of_date + timedelta(days=query.horizon_days)
    return tuple(event for event in events if event.date_to > query.as_of_date and event.date_from <= horizon_end)


def _dossier_event(event: PlannedEvent) -> dict:
    return {"type": event.event_type.value, "from": event.date_from.isoformat(), "to": event.date_to.isoformat(),
            "amount_eur": money_json(event.amount_eur), "description": event.description}


def _dossier_option(option: RescueOption) -> dict:
    return {
        "id": option.id,
        "title": option.title,
        "description": option.description,
        "new_min_balance": money_json(option.new_min_balance_eur),
        "new_min_balance_date": option.new_min_balance_date.isoformat(),
        "new_status": option.new_status.value,
        "cost_eur": money_json(option.cost_eur),
        "cost_note": option.cost_note,
        "non_financial_cost": option.non_financial_cost,
        "credit_limit_eur": money_json(option.credit_limit_eur),
        "requires_approval": option.requires_approval,
        "rejected": option.rejected,
    }


def _weekly_balance(balance_today: Decimal, days: Sequence[ForecastDay]) -> list[int]:
    """Today's balance followed by the projected balance at the end of each following week (whole euros)."""
    weekly = [day.balance_eur for day in days[_WEEK_DAYS - 1::_WEEK_DAYS]]
    return [round(value) for value in (balance_today, *weekly)]


def _forecast_snapshot(dashboard: Dashboard, option: RescueOption) -> dict:
    """Audit document: the situation before the decision and the option's projected outcome."""
    return {
        FINGERPRINT_KEY: option.fingerprint,
        "status_before": dashboard.risk.status.value,
        "min_balance_before_eur": money_json(dashboard.risk.min_balance_eur),
        "min_balance_date_before": dashboard.risk.min_balance_date.isoformat(),
        "status_after": option.new_status.value,
        "min_balance_after_eur": money_json(option.new_min_balance_eur),
        "min_balance_date_after": option.new_min_balance_date.isoformat(),
        "credit_limit_after_eur": money_json(option.credit_limit_eur),
        "cost_eur": money_json(option.cost_eur),
        "current_balance_eur": money_json(dashboard.current_balance_eur),
        "forecast_before": _balance_line(dashboard.forecast.days),
        "forecast_after": _balance_line(option.forecast),
    }


def _balance_line(days: Sequence[ForecastDay]) -> list[dict]:
    return [{"date": day.date.isoformat(), "balance_eur": money_json(day.balance_eur)} for day in days]
