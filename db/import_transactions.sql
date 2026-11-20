-- Import the bank statement CSV into bank_transactions.
-- Usage (inside the DB pod, CSV copied to /tmp/statement.csv):
--   psql -d amalfi -f /tmp/import_transactions.sql
-- CSV format: ';' separator, '"' quotes, header row, decimal comma, dates DD.MM.YY.
-- Re-runnable: rows already present (same row_hash) are skipped.

BEGIN;

CREATE TEMP TABLE staging_transactions (
    account_number TEXT, booking_date TEXT, value_date TEXT, transaction_type TEXT,
    payment_reference TEXT, beneficiary_payer TEXT, counterparty_account TEXT,
    bank_code TEXT, amount TEXT, currency TEXT, info TEXT
) ON COMMIT DROP;

\copy staging_transactions FROM '/tmp/statement.csv' WITH (FORMAT csv, DELIMITER ';', QUOTE '"', HEADER true, ENCODING 'UTF8')

INSERT INTO bank_transactions (
    account_number, booking_date, value_date, transaction_type, payment_reference,
    beneficiary_payer, counterparty_account, bank_code, amount, currency, info, row_hash
)
SELECT
    account_number,
    to_date(booking_date, 'DD.MM.YY'),
    to_date(value_date, 'DD.MM.YY'),
    transaction_type,
    NULLIF(payment_reference, ''),
    NULLIF(beneficiary_payer, ''),
    NULLIF(counterparty_account, ''),
    NULLIF(bank_code, ''),
    replace(amount, ',', '.')::NUMERIC(12, 2),
    currency,
    NULLIF(info, ''),
    encode(sha256(convert_to(concat_ws(';',
        account_number, booking_date, value_date, transaction_type, payment_reference,
        beneficiary_payer, counterparty_account, bank_code, amount, currency, info), 'UTF8')), 'hex')
FROM staging_transactions
ON CONFLICT (row_hash) DO NOTHING;

SELECT count(*) AS total_rows, min(booking_date) AS first_day, max(booking_date) AS last_day
FROM bank_transactions;

COMMIT;
