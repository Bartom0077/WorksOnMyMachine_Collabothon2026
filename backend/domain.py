"""Domain objects of the liquidity agent. Plain immutable data, no I/O. All money is ``Decimal`` EUR."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional


class EventType(StrEnum):
    CLOSURE = "closure"
    EXPENSE = "expense"
    INFLOW = "inflow"
    PAYMENT_DEFERRAL = "payment_deferral"
    CREDIT_LINE = "credit_line"


class EventSource(StrEnum):
    CHAT = "chat"
    AGENT = "agent"


class ItemKind(StrEnum):
    FIXED = "fixed"
    VARIABLE_REVENUE = "variable_revenue"
    VARIABLE_COST = "variable_cost"
    EVENT = "event"


class RiskStatus(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class DecisionStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


EXECUTION_SIMULATED = "SIMULATED"


@dataclass(frozen=True)
class LedgerEntry:
    """One booked transaction, converted to EUR and categorised."""

    date: date
    amount_eur: Decimal  # signed: + inflow, − outflow
    category: str
    counterparty: str
    reference: str


@dataclass(frozen=True, kw_only=True)
class EventDraft:
    """The part of a planned event that influences the forecast."""

    event_type: EventType
    date_from: date
    date_to: date
    amount_eur: Optional[Decimal] = None
    category: Optional[str] = None
    description: str


@dataclass(frozen=True, kw_only=True)
class PlannedEvent(EventDraft):
    """A planned event stored in ``planned_events``."""

    id: int
    original_utterance: Optional[str]
    source: EventSource
    as_of_date: date
    decision_id: Optional[int]
    created_at: datetime


@dataclass(frozen=True)
class EventUpdate:
    """New dates for an existing planned event (rescue options C and D)."""

    event_id: int
    date_from: date
    date_to: date


@dataclass(frozen=True)
class ForecastItem:
    label: str
    category: str
    kind: ItemKind
    amount_eur: Decimal  # signed: + inflow, − outflow


@dataclass(frozen=True)
class ForecastDay:
    date: date
    balance_eur: Decimal
    inflow_eur: Decimal
    outflow_eur: Decimal  # positive number
    items: tuple[ForecastItem, ...]
    floor_eur: Decimal  # lowest allowed balance that day: −(arranged credit limit), 0 without a credit line


@dataclass(frozen=True)
class DailyAverages:
    revenue_eur: Decimal
    variable_costs_eur: Decimal  # positive number
    lookback_days: int
    open_days: int


@dataclass(frozen=True)
class Forecast:
    days: tuple[ForecastDay, ...]
    daily_averages: DailyAverages


@dataclass(frozen=True)
class RiskWindow:
    start: date
    end: date


@dataclass(frozen=True)
class RiskAssessment:
    status: RiskStatus
    min_balance_eur: Decimal
    min_balance_date: date
    floor_eur: Decimal
    credit_limit_eur: Optional[Decimal]
    days_until_min: int
    risk_window: Optional[RiskWindow]

    @property
    def headroom_eur(self) -> Decimal:
        """Distance between the lowest balance and the floor (negative = below the floor)."""
        return self.min_balance_eur - self.floor_eur


@dataclass(frozen=True)
class RiskDriver:
    category: str
    label: str
    total_eur: Decimal  # positive number
    deferrable: bool


@dataclass(frozen=True)
class Scenario:
    """A forecast together with its risk assessment."""

    forecast: Forecast
    risk: RiskAssessment


@dataclass(frozen=True)
class RescueOption:
    id: str
    title: str
    description: str
    cost_eur: Decimal
    cost_note: Optional[str]
    non_financial_cost: Optional[str]
    simulated_actions: tuple[str, ...]
    events_to_add: tuple[EventDraft, ...]
    event_updates: tuple[EventUpdate, ...]
    scenario: Scenario
    pain_rank: int
    rejected: bool
    requires_approval: bool = True

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
    def credit_limit_eur(self) -> Optional[Decimal]:
        """Total arranged credit limit if this option is taken (None without a credit line)."""
        return self.scenario.risk.credit_limit_eur

    @property
    def forecast(self) -> tuple[ForecastDay, ...]:
        return self.scenario.forecast.days


@dataclass(frozen=True)
class BalancePoint:
    date: date
    balance_eur: Decimal


@dataclass(frozen=True)
class DataRange:
    first_date: date
    last_date: date


@dataclass(frozen=True)
class Insight:
    kind: str
    title: str
    detail: str
    amount_eur: Decimal


@dataclass(frozen=True)
class DashboardQuery:
    as_of_date: date
    horizon_days: int
    buffer_eur: Decimal


@dataclass(frozen=True)
class Dashboard:
    as_of_date: date
    horizon_days: int
    buffer_eur: Decimal
    data_range: DataRange
    current_balance_eur: Decimal
    history: tuple[BalancePoint, ...]
    forecast: Forecast
    risk: RiskAssessment
    risk_drivers: tuple[RiskDriver, ...]
    headline: str
    events: tuple[PlannedEvent, ...]
    options: tuple[RescueOption, ...]
    code_recommended_option_id: Optional[str]
    insights: tuple[Insight, ...]

    def option(self, option_id: str) -> Optional[RescueOption]:
        return next((option for option in self.options if option.id == option_id), None)


@dataclass(frozen=True)
class DecisionSnapshot:
    status_before: RiskStatus
    min_balance_before_eur: Decimal
    min_balance_date_before: date
    status_after: RiskStatus
    min_balance_after_eur: Decimal


@dataclass(frozen=True)
class NewDecision:
    """A decision about to be stored; ``forecast_snapshot`` is the JSON document for the audit trail."""

    as_of_date: date
    horizon_days: int
    buffer_eur: Decimal
    option_id: str
    option_title: str
    option_description: str
    status: DecisionStatus
    llm_explanation: Optional[str]
    execution_messages: tuple[str, ...]
    forecast_snapshot: dict


@dataclass(frozen=True)
class DecisionRecord:
    id: int
    as_of_date: date
    horizon_days: int
    buffer_eur: Decimal
    option_id: str
    option_title: str
    option_description: str
    status: DecisionStatus
    execution: str
    llm_explanation: Optional[str]
    execution_messages: tuple[str, ...]
    decided_at: datetime
    snapshot: DecisionSnapshot


@dataclass(frozen=True)
class FallbackRecommendation:
    """Deterministic texts used when the LLM is unavailable or returns an invalid answer."""

    headline: str
    explanation: str
    recommended_option_id: Optional[str]
    rationale: str
    option_comments: dict[str, str]
