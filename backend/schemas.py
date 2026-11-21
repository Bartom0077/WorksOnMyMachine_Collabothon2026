"""Pydantic models of the REST contract. Amounts are EUR numbers rounded to 2 decimals, dates are ISO strings.

Response models are built straight from the domain dataclasses (``from_attributes``).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from config import (
    CHAT_MESSAGE_MAX_CHARS,
    DEFAULT_AS_OF_DATE,
    DEFAULT_BUFFER_EUR,
    DEFAULT_HORIZON_DAYS,
    MAX_BUFFER_EUR,
    SERVICE_NAME,
)
from domain import DashboardQuery, DecisionStatus, EventSource, EventType, ItemKind, RiskStatus
from kpi_engine.money import cents, money_json

ALLOWED_HORIZON_DAYS = (30, 60, 90)

Eur = Annotated[Decimal, PlainSerializer(money_json, return_type=float)]
OptionalEur = Annotated[Optional[Decimal], PlainSerializer(money_json, return_type=Optional[float])]


def _check_horizon(value: int) -> int:
    if value not in ALLOWED_HORIZON_DAYS:
        raise ValueError(f"must be one of {', '.join(str(days) for days in ALLOWED_HORIZON_DAYS)}")
    return value


HorizonDays = Annotated[int, AfterValidator(_check_horizon)]


class SimulationParams(BaseModel):
    """The simulation settings shared by every endpoint (query string or JSON body)."""

    as_of_date: date = DEFAULT_AS_OF_DATE
    horizon_days: HorizonDays = DEFAULT_HORIZON_DAYS
    buffer_eur: Decimal = Field(default=DEFAULT_BUFFER_EUR, ge=0, le=MAX_BUFFER_EUR)

    def to_query(self) -> DashboardQuery:
        return DashboardQuery(self.as_of_date, self.horizon_days, cents(self.buffer_eur))


class DecisionRequest(SimulationParams):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(pattern=r"^[A-E]$")
    status: DecisionStatus
    llm_explanation: Optional[str] = Field(default=None, max_length=10000)


class ChatRequest(SimulationParams):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    # The id of an OGX response (e.g. "resp_3f2a…"). It becomes part of an OGX URL, so only id characters pass.
    conversation_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,200}$")


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    service: str = SERVICE_NAME
    status: str = "ok"
    time: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


class DataRangeOut(_Response):
    first_date: date
    last_date: date


class BalancePointOut(_Response):
    date: date
    balance_eur: Eur


class ForecastItemOut(_Response):
    label: str
    category: str
    kind: ItemKind
    amount_eur: Eur


class ForecastDayOut(_Response):
    date: date
    balance_eur: Eur
    inflow_eur: Eur
    outflow_eur: Eur
    items: list[ForecastItemOut]


class DailyAveragesOut(_Response):
    revenue_eur: Eur
    variable_costs_eur: Eur
    lookback_days: int
    open_days: int


class ForecastOut(_Response):
    days: list[ForecastDayOut]
    daily_averages: DailyAveragesOut


class RiskWindowOut(_Response):
    start: date
    end: date


class RiskOut(_Response):
    status: RiskStatus
    min_balance_eur: Eur
    min_balance_date: date
    floor_eur: Eur
    credit_limit_eur: OptionalEur
    days_until_min: int
    risk_window: Optional[RiskWindowOut]


class RiskDriverOut(_Response):
    category: str
    label: str
    total_eur: Eur
    deferrable: bool


class EventOut(_Response):
    id: int
    event_type: EventType
    date_from: date
    date_to: date
    amount_eur: OptionalEur
    category: Optional[str]
    description: str
    original_utterance: Optional[str]
    source: EventSource
    as_of_date: date
    decision_id: Optional[int]
    created_at: datetime


class OptionOut(_Response):
    id: str
    title: str
    description: str
    new_min_balance_eur: Eur
    new_min_balance_date: date
    new_status: RiskStatus
    cost_eur: Eur
    cost_note: Optional[str]
    non_financial_cost: Optional[str]
    requires_approval: bool
    credit_limit_eur: OptionalEur
    simulated_actions: list[str]
    forecast: list[BalancePointOut]
    rejected: bool


class InsightOut(_Response):
    kind: str
    title: str
    detail: str
    amount_eur: Eur


class DashboardResponse(_Response):
    as_of_date: date
    horizon_days: int
    buffer_eur: Eur
    data_range: DataRangeOut
    current_balance_eur: Eur
    history: list[BalancePointOut]
    forecast: ForecastOut
    risk: RiskOut
    risk_drivers: list[RiskDriverOut]
    headline: str
    events: list[EventOut]
    options: list[OptionOut]
    code_recommended_option_id: Optional[str]
    insights: list[InsightOut]


class EventsResponse(BaseModel):
    events: list[EventOut]


class DecisionSnapshotOut(_Response):
    status_before: RiskStatus
    min_balance_before_eur: Eur
    min_balance_date_before: date
    status_after: RiskStatus
    min_balance_after_eur: Eur
    credit_limit_after_eur: OptionalEur


class DecisionOut(_Response):
    id: int
    as_of_date: date
    horizon_days: int
    buffer_eur: Eur
    option_id: str
    option_title: str
    option_description: str
    status: DecisionStatus
    execution: str
    llm_explanation: Optional[str]
    execution_messages: list[str]
    decided_at: datetime
    withdrawn_at: Optional[datetime]
    snapshot: DecisionSnapshotOut


class DecisionsResponse(BaseModel):
    decisions: list[DecisionOut]


class DecisionCreatedResponse(BaseModel):
    decision: DecisionOut
    execution_messages: list[str]


class RecommendationResponse(_Response):
    source: Literal["llm", "fallback"]
    fallback_reason: Optional[str]
    headline: str
    explanation: str
    recommended_option_id: Optional[str]
    rationale: str
    option_comments: dict[str, str]


class ChatEventDraftOut(_Response):
    """A plan the assistant has checked and is asking the owner to confirm; nothing is stored yet."""

    event_type: Literal[EventType.CLOSURE, EventType.EXPENSE, EventType.INFLOW]
    date_from: date
    date_to: date
    amount_eur: OptionalEur
    description: str


class ToolCallOut(_Response):
    name: str
    ok: bool


class ChatResponse(_Response):
    conversation_id: str
    reply: str
    pending_events: list[ChatEventDraftOut]
    saved_events: list[EventOut]
    tool_calls: list[ToolCallOut]
    events_changed: bool


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
