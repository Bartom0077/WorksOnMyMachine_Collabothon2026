"""Backend configuration: environment-driven settings and the business constants of the liquidity agent.

The database connection is configured with the standard libpq variables (``PGHOST``, ``PGPORT``,
``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``) or ``DATABASE_URL``; psycopg reads them directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

SERVICE_NAME = "autonomous-cfo-backend"

# Request defaults (ROZWIAZANIE.md §12: top bar).
DEFAULT_AS_OF_DATE = date(2025, 5, 10)
DEFAULT_HORIZON_DAYS = 60
DEFAULT_BUFFER_EUR = Decimal("3000")
MAX_BUFFER_EUR = Decimal("1000000")

# Forecast (Module 2).
AVERAGES_LOOKBACK_DAYS = 28
AVERAGES_MIN_OPEN_DAYS = 14  # with fewer open days in the window (a long closure) the lookback is extended
HISTORY_DAYS = 90
RECURRING_GRACE_DAYS = 10  # a recurring payment overdue by more than this is treated as discontinued

# Events (Module 3).
DEFERRAL_MONTHS = 2

# Rescue options (Module 5). The interest rates are illustrative assumptions, not bank quotes.
CREDIT_LINE_INTEREST_RATE = Decimal("0.09")
UNARRANGED_OVERDRAFT_RATE = Decimal("0.13")
CREDIT_LINE_ROUNDING_EUR = Decimal("5000")
CREDIT_LINE_TERM_DAYS = 365
SHORT_CLOSURE_DAYS = 7
DAYS_PER_YEAR = Decimal("365")
BANK_NAME = "Commerzbank"

# Insights: a surplus above the buffer is worth mentioning from this amount upwards.
SURPLUS_INSIGHT_MIN_EUR = Decimal("10000")
SURPLUS_ROUNDING_EUR = Decimal("1000")

# CFO advisors (backend/advisors): income tax, CHF transfers, term deposits.
# Income tax. The figures come from the documented obligation TAX002 (Knowledge/pizzeria_amalfi_fixed_outflows_2025.csv:
# quarterly prepayments "based on estimated previous year profit of ~EUR 30000"). The effective rate is derived
# from them (prepayments per year / prior-year profit); no statutory tax rate is used anywhere.
PRIOR_YEAR_PROFIT_BASE_EUR = Decimal("30000")
INCOME_TAX_PREPAYMENT_EUR = Decimal("1150")
INCOME_TAX_PREPAYMENTS_PER_YEAR = 4
ANNUAL_INCOME_TAX_PREPAYMENTS_EUR = INCOME_TAX_PREPAYMENT_EUR * INCOME_TAX_PREPAYMENTS_PER_YEAR
# Cash movements that are not part of the taxable profit: the owner's drawings, the capital paid to Zurich and
# the tax prepayments themselves.
TAX_PROFIT_EXCLUDED_CATEGORIES = frozenset({
    "owner_withdrawal", "investment_outflow", "income_tax_advance", "trade_tax_advance",
})
TAX_RESERVE_MIN_EUR = Decimal("1000")  # illustrative assumption: a smaller expected shortfall is not raised
TAX_RESERVE_DEPOSIT_TERM_MONTHS = 1  # illustrative assumption: the settlement date is unknown, so keep it short

# Term deposits. Illustrative assumptions, not bank quotes (like the credit line rate above).
TERM_DEPOSIT_INTEREST_RATE = Decimal("0.02")
TERM_DEPOSIT_TERMS_MONTHS = (1, 3, 6)

# CHF transfers to Zurich. Illustrative assumptions: the data holds no forward quotes, fees or spreads, so a
# forward is assumed to lock the last observed rate.
FX_FORWARD_MIN_TRANCHES = 2  # locking the rate is only worth offering for at least this many instalments
FX_SMALL_SENSITIVITY_EUR = Decimal("500")  # below this the possible swing is called small in plain words

# Language model (Module 6). The model's context window is 16,384 tokens shared by input and output, so the
# output caps below also decide how much room the prompt, the tool schemas and the chat history have.
LLM_TIMEOUT_SECONDS = 90.0
LLM_CONNECT_TIMEOUT_SECONDS = 5.0
# A chat turn makes several model calls in a row (look up the previous turn, answer, retry in a new thread,
# comment on the updated forecast). The whole turn gets one budget, and every call gets what is left of it.
# Keep: CHAT_TURN_BUDGET_SECONDS < nginx proxy_read_timeout (frontend/nginx-default-cfg/api.conf, 180 s)
#       <= route timeout (deploy/openshift.yaml, 180 s), so the browser gets the backend's answer, not a 504.
CHAT_TURN_BUDGET_SECONDS = 150.0
LLM_LOOKUP_TIMEOUT_SECONDS = 10.0   # reading the previous response back from OGX
LLM_COMMENT_TIMEOUT_SECONDS = 20.0  # the optional forecast comment after a save
LLM_RECOMMENDATION_MAX_OUTPUT_TOKENS = 900
LLM_CHAT_MAX_OUTPUT_TOKENS = 600
LLM_COMMENT_MAX_OUTPUT_TOKENS = 250
RECOMMENDATION_CACHE_SIZE = 128
LLM_ADVICE_MAX_OUTPUT_TOKENS = 700  # one advisor: reworded proposals plus optional tips
LLM_ADVICE_SUMMARY_MAX_OUTPUT_TOKENS = 350  # the coordinator's headline and 2-4 sentences
ADVICE_CACHE_SIZE = 256  # cached advisor answers, keyed by the hash of each small dossier
PENDING_PLANS_CACHE_SIZE = 512  # conversations whose unconfirmed plan proposals are remembered
CHAT_MESSAGE_MAX_CHARS = 2000


@dataclass(frozen=True)
class Settings:
    ogx_url: str
    ogx_model: str
    mcp_server_url: str
    nemo_guardrails_url: str
    account_number: Optional[str]
    host: str
    port: int


def load_settings() -> Settings:
    return Settings(
        ogx_url=os.environ.get("OGX_URL", "http://cfo-ogx-service.autonomous-cfo.svc.cluster.local:8321"),
        ogx_model=os.environ.get("OGX_MODEL", "vllm-cfo/cfo-llm"),
        mcp_server_url=os.environ.get("MCP_SERVER_URL", "http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp"),
        # Empty disables the check. The NeMo service lives next to the model, shared by every app namespace.
        nemo_guardrails_url=os.environ.get(
            "NEMO_GUARDRAILS_URL", "http://cfo-nemo.cfo-models.svc.cluster.local").rstrip("/"),
        account_number=os.environ.get("ACCOUNT_NUMBER") or None,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
    )
