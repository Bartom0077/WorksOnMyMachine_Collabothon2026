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
