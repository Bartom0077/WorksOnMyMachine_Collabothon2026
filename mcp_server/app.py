"""Amalfi data MCP server — read tools for bank data and KPI snapshots, plus two constrained writes
(saving and removing a planned event the owner confirmed in the chat) for the CFO agent.

The product's chat offers the model only the planned-event tools and get_fixed_obligations; balances, the
forecast and rescue options come from the backend (see README.md, "Which tools the product uses").

Run:  python app.py            (streamable HTTP on 0.0.0.0:8080, MCP endpoint /mcp, health /health)
Env:  DATABASE_URL or PG* vars; AMALFI_AS_OF_DATE (optional server-wide cutoff); MCP_HOST / MCP_PORT.
Per-request cutoff: HTTP header ``X-As-Of-Date: YYYY-MM-DD`` (set by the agent orchestrator); it is the
simulated "today" and is required by the tools that create planned events. ``X-Removable-Event-Ids`` (comma
separated ids, also set by the orchestrator) lists the plans the owner confirmed for removal in this turn.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Annotated, Callable, List, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

import planned_events
import tools
from db import ToolInputError, connect, connect_write, effective_cutoff, parse_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("amalfi-mcp")

INSTRUCTIONS = """\
Financial data of Pizzeria Amalfi (single EUR business bank account, year 2025).
All data is limited to the analysis date (no data after it is visible). Everything is read-only except
planned events.
* The current balance, the balance forecast, the risk status and the rescue options are computed by the
  application backend and given to you in the conversation (the SITUATION). That is the only source for
  those numbers: never derive them from the tools below.
* Planned events (closure, expense, inflow) the owner mentions: call propose_planned_event first, ask the
  owner to confirm, and only after an explicit yes call save_planned_event. list_planned_events shows what
  is already saved.
* Removing a saved plan works the same way: propose_planned_event_removal with the plan's id, ask the owner
  to confirm, and only after an explicit yes call remove_planned_event.
* get_fixed_obligations lists the documented recurring payments.
* Analyst tools (get_data_overview, get_kpi_snapshot, get_kpi_history, get_cashflow_summary,
  get_transactions) describe the past. The KPI snapshot carries its own 30-day outlook that ignores planned
  events; it does not replace the backend forecast.
* Dates are YYYY-MM-DD. Amounts are EUR unless a row's currency says otherwise.
"""

mcp = FastMCP(
    "amalfi-data",
    instructions=INSTRUCTIONS,
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8080")),
    stateless_http=True,
    json_response=True,
)


def _header(ctx: Context, name: str) -> Optional[str]:
    try:
        request = ctx.request_context.request
    except (LookupError, ValueError):  # no HTTP request in scope (e.g. stdio transport)
        return None
    return request.headers.get(name) if request is not None else None


def _as_of_header(ctx: Context) -> Optional[str]:
    return _header(ctx, "x-as-of-date")


def _removable_event_ids(ctx: Context) -> frozenset[int]:
    """Ids from the X-Removable-Event-Ids header: the plans the owner confirmed for removal. No header, no removal."""
    parts = (part.strip() for part in (_header(ctx, "x-removable-event-ids") or "").split(","))
    return frozenset(int(part) for part in parts if part.isdecimal())


def _cutoff(ctx: Context) -> Optional[date]:
    return effective_cutoff(_as_of_header(ctx), os.environ.get("AMALFI_AS_OF_DATE"))


def _today(ctx: Context) -> date:
    """The simulated "today" for planned events: the X-As-Of-Date header, which is mandatory here."""
    today = parse_date(_as_of_header(ctx), "X-As-Of-Date header")
    if today is None:
        raise ToolInputError("The X-As-Of-Date request header (YYYY-MM-DD) is required for planned events; "
                             "the orchestrator must set it. This cannot be fixed by changing the arguments.")
    return today


def _run(ctx: Context, fn, **kwargs) -> dict:
    cutoff = _cutoff(ctx)
    log.info("tool=%s cutoff=%s args=%s", fn.__name__, cutoff, kwargs)
    with connect() as conn:
        return fn(conn, cutoff, **kwargs)


def _checked(flag: str, tool: str, run: Callable[[], dict]) -> dict:
    """Run a planned-event tool; a validation failure becomes a normal result ``{flag: false, "error": ...}``.

    OGX reduces MCP tool errors to "Error (code 1): None" in its mcp_call output items, which would hide
    the reason from the backend; a regular result keeps the message visible to the model and the backend.
    """
    try:
        return run()
    except ToolInputError as exc:
        log.info("tool=%s rejected: %s", tool, exc)
        return {flag: False, "error": str(exc)}


DateStr = Annotated[str, Field(description="Date in YYYY-MM-DD format.")]
OptDateStr = Annotated[Optional[str], Field(description="Date in YYYY-MM-DD format.")]
ChatEventType = Annotated[Literal["closure", "expense", "inflow"], Field(
    description="closure = restaurant closed (no revenue, no variable costs); expense = one-off payment; "
                "inflow = one-off money in.")]
EventStart = Annotated[str, Field(
    description="YYYY-MM-DD. Payment day of an expense/inflow: tomorrow or later (today's payments are already "
                "in the bank balance). First day of a closure: today or later.")]
EventEnd = Annotated[Optional[str], Field(
    description="Last closure day (YYYY-MM-DD), tomorrow or later. Omit for expense/inflow.")]
EventAmount = Annotated[Optional[float], Field(
    description="Positive EUR amount; required for expense/inflow, omit for closure.")]
EventDescription = Annotated[str, Field(description="Short label, e.g. 'Summer holiday closure' or 'New oven'.")]
SavedEventId = Annotated[int, Field(description="The \"id\" of the plan as returned by list_planned_events.")]


@mcp.tool()
def get_data_overview(ctx: Context) -> dict:
    """Analyst tool. Overview of the available data: analysis date, transaction date range and count,
    transaction types with their cash-flow class, and which KPI snapshot dates exist."""
    return _run(ctx, tools.get_data_overview)


@mcp.tool()
def get_kpi_snapshot(
    ctx: Context,
    date: Annotated[Optional[str], Field(description="Snapshot date YYYY-MM-DD; default = the analysis date. "
                                                     "If missing, the latest earlier snapshot is returned.")] = None,
    include_forecast: Annotated[bool, Field(description="Add the 30-day day-by-day balance forecast, run-rates "
                                                        "and scheduled fixed payments.")] = False,
    include_kpi_details: Annotated[bool, Field(description="Add per-KPI calculation details.")] = False,
) -> dict:
    """Analyst tool. KPI snapshot of the deterministic KPI engine for a date: current cash balance,
    7/14/30-day outlook, minimum cash buffer, 30-day liquidity gap, deployable surplus, net cash flow,
    inflow/outflow trend, fixed cost coverage — each with status (HEALTHY/WARNING/CRITICAL), plus scenario
    signals and data quality flags. Its outlook ignores planned events, so it is not the forecast the owner
    sees: that one comes from the application backend."""
    return _run(ctx, tools.get_kpi_snapshot, date_=date, include_forecast=include_forecast,
                include_kpi_details=include_kpi_details)


@mcp.tool()
def get_kpi_history(
    ctx: Context,
    date_from: DateStr,
    date_to: OptDateStr = None,
    kpi_names: Annotated[Optional[List[str]], Field(
        description="KPI keys to include, e.g. ['current_cash_balance_eur', 'net_cash_flow_30d_eur']; "
                    "default = all KPIs.")] = None,
    step_days: Annotated[int, Field(description="Return every N-th day (7 = weekly). Long ranges are "
                                                "sampled automatically.", ge=1, le=31)] = 1,
) -> dict:
    """Analyst tool. Time series of KPI values from daily snapshots between two dates (inclusive), with
    overall status and scenario hint per day. Use it to see trends, e.g. how the cash balance evolved."""
    return _run(ctx, tools.get_kpi_history, date_from=date_from, date_to=date_to, kpi_names=kpi_names,
                step_days=step_days)


@mcp.tool()
def get_cashflow_summary(
    ctx: Context,
    date_from: DateStr,
    date_to: OptDateStr = None,
    group_by: Annotated[Literal["day", "week", "month", "transaction_type", "counterparty", "cashflow_class"],
                        Field(description="How to group the sums.")] = "month",
    transaction_type: Annotated[Optional[str], Field(description="Filter: exact transaction type, e.g. "
                                                                 "'SUPPLIER PAYMENT'.")] = None,
    counterparty: Annotated[Optional[str], Field(description="Filter: text contained in payer/beneficiary "
                                                             "or payment reference.")] = None,
) -> dict:
    """Analyst tool. Inflow, outflow, net and transaction count of the bank account, grouped by period, type,
    counterparty or cash-flow class (OPERATING_INFLOW, FIXED_OUTFLOW, VARIABLE_OUTFLOW, OTHER_*). Use it to
    find what drives revenue and costs and how they change over time."""
    return _run(ctx, tools.get_cashflow_summary, date_from=date_from, date_to=date_to, group_by=group_by,
                transaction_type=transaction_type, counterparty=counterparty)


@mcp.tool()
def get_transactions(
    ctx: Context,
    date_from: DateStr,
    date_to: OptDateStr = None,
    transaction_type: Annotated[Optional[str], Field(description="Exact transaction type, e.g. 'RENT PAYMENT'.")] = None,
    counterparty: Annotated[Optional[str], Field(description="Text contained in payer/beneficiary or payment "
                                                             "reference (case-insensitive).")] = None,
    direction: Annotated[Optional[Literal["inflow", "outflow"]], Field(description="Only money in or out.")] = None,
    min_abs_amount: Annotated[Optional[float], Field(description="Only transactions with |amount| >= this.",
                                                     ge=0)] = None,
    sort: Annotated[Literal["date_desc", "date_asc", "amount_desc"],
                    Field(description="amount_desc = largest absolute amounts first.")] = "date_desc",
    limit: Annotated[int, Field(description="Max rows returned.", ge=1, le=200)] = 50,
) -> dict:
    """Analyst tool. Individual bank transactions matching the filters, plus totals of all matches per currency.
    Use it to explain a specific movement (large payment, missing revenue, unusual counterparty)."""
    return _run(ctx, tools.get_transactions, date_from=date_from, date_to=date_to,
                transaction_type=transaction_type, counterparty=counterparty, direction=direction,
                min_abs_amount=min_abs_amount, sort=sort, limit=limit)


@mcp.tool()
def get_fixed_obligations(
    ctx: Context,
    active_on: Annotated[Optional[str], Field(description="Only obligations started on or before this date "
                                                          "(YYYY-MM-DD); default = the analysis date.")] = None,
    category: Annotated[Optional[str], Field(description="Filter by category: rent, utilities, net_payroll, "
                                                         "payroll_tax, social_security, owner_withdrawal, "
                                                         "income_tax_advance, trade_tax_advance, "
                                                         "investment_outflow.")] = None,
) -> dict:
    """Documented recurring payments (rent, utilities, salaries, payroll taxes, tax prepayments, owner
    withdrawal, silent-partnership investment tranches in CHF): payee, amount,
    currency, frequency, payment day and months. Use it to anticipate upcoming fixed costs."""
    return _run(ctx, tools.get_fixed_obligations, active_on=active_on, category=category)


@mcp.tool()
def list_planned_events(ctx: Context) -> dict:
    """Planned events already saved for the forecast (closures, one-off expenses and inflows, approved
    rescue actions and CFO recommendations) that end after the analysis date, i.e. still affect the forecast.
    Each has an "id" and a "source": "chat" = told by the owner in the chat, "agent" = measure of an approved
    rescue option or CFO recommendation."""
    return _run(ctx, planned_events.list_planned_events)


@mcp.tool()
def propose_planned_event(
    ctx: Context,
    event_type: ChatEventType,
    date_from: EventStart,
    description: EventDescription,
    date_to: EventEnd = None,
    amount_eur: EventAmount = None,
) -> dict:
    """Validate a plan the owner mentioned (closure, one-off expense or inflow) WITHOUT saving it. Call it
    once dates and amount are known, then ask the owner to confirm the returned confirmation_prompt.
    If it returns valid=false, fix the call or ask the owner; never guess missing dates or amounts."""
    log.info("tool=propose_planned_event type=%s from=%s to=%s amount=%s", event_type, date_from, date_to, amount_eur)
    return _checked("valid", "propose_planned_event", lambda: planned_events.propose_planned_event(
        _today(ctx), event_type=event_type, date_from=date_from, date_to=date_to, amount_eur=amount_eur,
        description=description))


@mcp.tool()
def save_planned_event(
    ctx: Context,
    event_type: ChatEventType,
    date_from: EventStart,
    description: EventDescription,
    original_utterance: Annotated[str, Field(description="The owner's own words describing the plan, verbatim.")],
    date_to: EventEnd = None,
    amount_eur: EventAmount = None,
) -> dict:
    """Save a planned event into the forecast. Call ONLY after the owner explicitly confirmed the proposal
    from propose_planned_event; use the same values. saved=false with an error means nothing was written."""
    log.info("tool=save_planned_event type=%s from=%s to=%s amount=%s", event_type, date_from, date_to, amount_eur)

    def save() -> dict:
        today = _today(ctx)
        with connect_write() as conn:
            return planned_events.save_planned_event(conn, today, event_type=event_type, date_from=date_from,
                                                     date_to=date_to, amount_eur=amount_eur, description=description,
                                                     original_utterance=original_utterance)

    return _checked("saved", "save_planned_event", save)


@mcp.tool()
def propose_planned_event_removal(ctx: Context, event_id: SavedEventId) -> dict:
    """Look up a saved plan the owner wants to remove, WITHOUT removing it. Then ask the owner to confirm the
    returned confirmation_prompt. valid=false with an error means the plan cannot be removed in the chat."""
    log.info("tool=propose_planned_event_removal id=%s", event_id)

    def propose() -> dict:
        with connect() as conn:
            return planned_events.propose_planned_event_removal(conn, event_id)

    return _checked("valid", "propose_planned_event_removal", propose)


@mcp.tool()
def remove_planned_event(ctx: Context, event_id: SavedEventId) -> dict:
    """Remove a saved plan from the forecast. Call ONLY after the owner explicitly confirmed the removal shown
    by propose_planned_event_removal. removed=false with an error means nothing was removed;
    already_removed=true means the plan was gone before this call."""
    confirmed_ids = _removable_event_ids(ctx)
    log.info("tool=remove_planned_event id=%s confirmed_ids=%s", event_id, sorted(confirmed_ids))

    def remove() -> dict:
        with connect_write() as conn:
            result = planned_events.remove_planned_event(conn, event_id, confirmed_ids)
        # The DELETE is physical and committed here: the log line is the only record of what the plan was.
        if result.get("removed"):
            log.info("tool=remove_planned_event removed event=%s", result["event"])
        else:
            log.info("tool=remove_planned_event id=%s was already gone; nothing removed", event_id)
        return result

    return _checked("removed", "remove_planned_event", remove)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"service": "amalfi-mcp", "status": "ok"})


if __name__ == "__main__":
    log.info("amalfi-mcp listening on %s:%s (MCP endpoint /mcp)", mcp.settings.host, mcp.settings.port)
    mcp.run(transport="streamable-http")
