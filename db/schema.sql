-- Autonomous CFO — schema for database "amalfi" (SPEC.md §3)
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
