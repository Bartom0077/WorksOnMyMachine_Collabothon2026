-- Autonomous CFO — schema for database "amalfi" (ROZWIAZANIE.md §5, KPI_SPEC.md §2)
-- Idempotent: safe to run multiple times.

CREATE TABLE IF NOT EXISTS bank_transactions (
    id SERIAL PRIMARY KEY,
    account_number VARCHAR(34) NOT NULL,
    booking_date DATE NOT NULL,
    value_date DATE NOT NULL,
    transaction_type VARCHAR(100) NOT NULL,
    payment_reference TEXT,
    beneficiary_payer VARCHAR(255),
    counterparty_account VARCHAR(64),
    bank_code VARCHAR(32),
    amount NUMERIC(12, 2) NOT NULL,
    currency CHAR(3) NOT NULL,
    info VARCHAR(100),
    row_hash VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bank_transactions_booking_date ON bank_transactions (booking_date);

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE UNIQUE NOT NULL,
    metrics JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Legacy: proposal table of the first design. No application code reads or writes it (owner decisions are in
-- owner_decisions below); it is kept only because backend/tests/test_integration_db.py uses it to prove that
-- a re-run of the KPI engine keeps kpi_snapshots.id stable.
CREATE TABLE IF NOT EXISTS agent_proposals (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER REFERENCES kpi_snapshots(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    agent_type VARCHAR(50) NOT NULL DEFAULT 'LIQUIDITY_AGENT',
    scenario_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    title VARCHAR(255) NOT NULL,
    plain_language_summary TEXT NOT NULL,
    reasoning_details JSONB NOT NULL,
    recommended_action JSONB NOT NULL,
    user_note TEXT,
    decided_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- KPI engine reference data (KPI_SPEC.md §0.1). Seeded by db/kpi_reference_data.sql.
-- ---------------------------------------------------------------------------

-- Documented recurring outflows (source: Knowledge/pizzeria_amalfi_fixed_outflows_2025.csv).
CREATE TABLE IF NOT EXISTS fixed_obligations (
    expense_id VARCHAR(20) PRIMARY KEY,
    payee VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(50) NOT NULL,
    cashflow_class VARCHAR(50) NOT NULL,
    frequency VARCHAR(20) NOT NULL,               -- monthly | quarterly
    payment_day SMALLINT NOT NULL CHECK (payment_day BETWEEN 1 AND 31),
    payment_months VARCHAR(50) NOT NULL,          -- 'all' or e.g. '03;06;09;12'
    currency CHAR(3) NOT NULL,
    amount_per_payment NUMERIC(12, 2) NOT NULL,   -- in obligation currency
    estimated_eur_per_payment NUMERIC(12, 2),
    amount_type VARCHAR(30),
    valid_from DATE NOT NULL,
    valid_to DATE,                                -- end of documented period (projection may roll over)
    total_commitment NUMERIC(12, 2),              -- finite commitments only (obligation currency)
    match_keyword TEXT,                           -- identifies already-paid instalments in bank_transactions
    source VARCHAR(100),
    notes TEXT
);

-- Known end-of-day balances of the bank account (the statement has no balance column).
CREATE TABLE IF NOT EXISTS account_balance_anchors (
    id SERIAL PRIMARY KEY,
    account_number VARCHAR(34) NOT NULL,
    anchor_date DATE NOT NULL,                    -- balance at END of this day
    balance NUMERIC(12, 2) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'EUR',
    is_derived BOOLEAN NOT NULL DEFAULT FALSE,    -- TRUE = reconstructed, not stated in a source document
    source TEXT NOT NULL,
    UNIQUE (account_number, anchor_date)
);

-- ---------------------------------------------------------------------------
-- Liquidity agent state (ROZWIAZANIE.md §5): owner decisions and planned events.
-- Forecasts, averages and transaction categories are never stored; they are recomputed on demand.
-- ---------------------------------------------------------------------------

-- Audit trail of the owner's decisions on rescue options and on the CFO advisors' proposals. Execution is
-- always simulated.
CREATE TABLE IF NOT EXISTS owner_decisions (
    id SERIAL PRIMARY KEY,
    as_of_date DATE NOT NULL,                     -- simulated "today" when the decision was taken
    horizon_days SMALLINT NOT NULL,
    buffer_eur NUMERIC(12, 2) NOT NULL,
    option_id VARCHAR(5) NOT NULL,                -- rescue option A..E or advisor proposal T1, T2, F1, F2, I1
    option_title VARCHAR(255) NOT NULL,
    option_description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('approved', 'rejected')),
    execution VARCHAR(20) NOT NULL DEFAULT 'SIMULATED' CHECK (execution = 'SIMULATED'),
    llm_explanation TEXT,
    execution_messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    forecast_snapshot JSONB NOT NULL,             -- risk + forecast at the moment of the decision
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    withdrawn_at TIMESTAMPTZ                      -- set when the owner later withdrew an approved option
);
ALTER TABLE owner_decisions ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;

-- Future plans: described by the owner in chat (source 'chat') or created from an approved rescue option or
-- advisor proposal ('agent').
-- Chat events are written by the MCP server, so the database itself limits them to what the owner may plan
-- in the chat (ROZWIAZANIE.md §8.2/§8.3): closure, expense or inflow, never tied to a category or a decision.
-- db/setup_mcp_user.sh adds row level security so that the MCP role can insert only source = 'chat'.
CREATE TABLE IF NOT EXISTS planned_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(30) NOT NULL
        CONSTRAINT planned_events_event_type CHECK (event_type IN (
            'closure', 'expense', 'inflow', 'payment_deferral', 'credit_line', 'term_deposit', 'fx_prebuy')),
    -- closure: first and last closed day. expense / inflow: date_to = date_from. credit_line: valid from..to.
    -- payment_deferral: ONE row per postponed payment; date_from = original due date, date_to = new due date.
    -- term_deposit: date_from = opening day, date_to = maturity.
    -- fx_prebuy: date_from = purchase day, date_to = date of the last instalment the purchase covers.
    date_from DATE NOT NULL,
    date_to DATE NOT NULL CHECK (date_to >= date_from),
    -- NULL for closure. payment_deferral: the payment. term_deposit: the principal. fx_prebuy: the EUR paid.
    amount_eur NUMERIC(12, 2) CHECK (amount_eur > 0),
    -- payment_deferral: category of the postponed payment (e.g. investment_outflow).
    -- term_deposit: 'investment' or 'tax_reserve'. NULL for every other type.
    category VARCHAR(50),
    description TEXT NOT NULL,
    original_utterance TEXT,                               -- owner's chat message (audit trail)
    source VARCHAR(10) NOT NULL CHECK (source IN ('chat', 'agent')),
    as_of_date DATE NOT NULL,                              -- simulated "today" when the event was saved
    decision_id INTEGER REFERENCES owner_decisions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT planned_events_chat_scope CHECK (
        source <> 'chat'
        OR (event_type IN ('closure', 'expense', 'inflow') AND category IS NULL AND decision_id IS NULL))
);

-- Databases created before the constraint existed: add it without re-checking old rows.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_constraint
                   WHERE conname = 'planned_events_chat_scope' AND conrelid = 'planned_events'::regclass) THEN
        ALTER TABLE planned_events ADD CONSTRAINT planned_events_chat_scope CHECK (
            source <> 'chat'
            OR (event_type IN ('closure', 'expense', 'inflow') AND category IS NULL AND decision_id IS NULL))
            NOT VALID;
    END IF;
END
$$;

-- Databases created before term_deposit and fx_prebuy existed: their check on event_type is an unnamed inline
-- check (PostgreSQL generated its name), so it is found by what it constrains (the event_type column alone) and
-- replaced by the named constraint of the CREATE TABLE above. The new list contains the old one, so every
-- existing row passes. A second run finds nothing to drop and nothing to add.
DO $$
DECLARE
    outdated_check name;
BEGIN
    FOR outdated_check IN
        SELECT con.conname FROM pg_constraint con
        WHERE con.conrelid = 'planned_events'::regclass AND con.contype = 'c'
          AND con.conname <> 'planned_events_event_type'
          AND con.conkey = ARRAY(SELECT att.attnum FROM pg_attribute att
                                 WHERE att.attrelid = con.conrelid AND att.attname = 'event_type')
    LOOP
        EXECUTE format('ALTER TABLE planned_events DROP CONSTRAINT %I', outdated_check);
    END LOOP;
    IF NOT EXISTS (SELECT FROM pg_constraint
                   WHERE conname = 'planned_events_event_type' AND conrelid = 'planned_events'::regclass) THEN
        ALTER TABLE planned_events ADD CONSTRAINT planned_events_event_type CHECK (event_type IN (
            'closure', 'expense', 'inflow', 'payment_deferral', 'credit_line', 'term_deposit', 'fx_prebuy'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_planned_events_dates ON planned_events (date_from, date_to);
