-- Reference data for the KPI engine (KPI_SPEC.md §0.1, §0.2, §0.6). Idempotent (upserts).
-- Usage: psql -d amalfi -f db/schema.sql -f db/kpi_reference_data.sql
--
-- fixed_obligations mirrors Knowledge/pizzeria_amalfi_fixed_outflows_2025.csv. Added columns:
--   total_commitment / match_keyword for INV001: knowledge doc §5 "Total investment: CHF 50,000.00";
--   bank references read "Silent Partnership Capital Tranche NN/12".

BEGIN;

INSERT INTO fixed_obligations (expense_id, payee, description, category, cashflow_class, frequency, payment_day,
    payment_months, currency, amount_per_payment, estimated_eur_per_payment, amount_type, valid_from, valid_to,
    total_commitment, match_keyword, source, notes)
VALUES
    ('FIX001', 'Main Street Real Estate GmbH', 'Restaurant rent', 'rent', 'operating', 'monthly', 1, 'all', 'EUR', 3800.00, 3800.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Rent debited on 1st'),
    ('FIX002', 'City Energy & Utilities AG', 'Utilities and energy', 'utilities', 'operating', 'monthly', 10, 'all', 'EUR', 650.00, 650.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Provided budgeted/monthly amount; actual usage may vary'),
    ('PAY001', 'Vincenzo Amalfi', 'Owner monthly payment / withdrawal', 'owner_withdrawal', 'owner_financing', 'monthly', 28, 'all', 'EUR', 3200.00, 3200.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Owner payment; legal/accounting treatment to verify'),
    ('PAY002', 'Yusuf Akcit', 'Pizza baker net salary', 'net_payroll', 'operating', 'monthly', 28, 'all', 'EUR', 2350.00, 2350.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Net salary, not total employer labor cost'),
    ('PAY003', 'Rosa Pavese', 'Chef net salary', 'net_payroll', 'operating', 'monthly', 28, 'all', 'EUR', 2100.00, 2100.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Net salary, not total employer labor cost'),
    ('PAY004', 'Vanessa Paradi', 'Waitstaff / temporary net salary', 'net_payroll', 'operating', 'monthly', 28, 'all', 'EUR', 950.00, 950.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Net salary; employment status may affect actual costs'),
    ('TAX001', 'Tax Office Frankfurt am Main', 'Wage tax and solidarity surcharge', 'payroll_tax', 'operating', 'monthly', 10, 'all', 'EUR', 880.00, 880.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Payroll tax remittance'),
    ('SOC001', 'Techniker Krankenkasse', 'Social security contributions', 'social_security', 'operating', 'monthly', 27, 'all', 'EUR', 2650.00, 2650.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Health, pension, nursing and unemployment insurance'),
    ('TAX002', 'Tax Office (income tax)', 'Quarterly Einkommensteuer prepayment', 'income_tax_advance', 'tax_financing', 'quarterly', 10, '03;06;09;12', 'EUR', 1150.00, 1150.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Based on estimated previous year profit of ~EUR 30000'),
    ('TAX003', 'Municipal trade tax authority', 'Quarterly Gewerbesteuer prepayment', 'trade_tax_advance', 'tax_financing', 'quarterly', 15, '02;05;08;11', 'EUR', 225.00, 225.00, 'fixed', '2025-01-01', '2025-12-31', NULL, NULL, 'user_provided_scenario', 'Trade tax prepayment'),
    ('INV001', 'Luigi Amalfi / Swiss restaurant', 'Silent partnership investment tranche', 'investment_outflow', 'investing', 'monthly', 15, 'all', 'CHF', 4166.67, 4350.00, 'estimated_fx', '2025-01-01', '2025-12-31', 50000.00, 'Silent Partnership', 'user_provided_scenario', 'Total commitment CHF 50000; monthly EUR actual ~4325-4375')
ON CONFLICT (expense_id) DO UPDATE SET
    payee = EXCLUDED.payee, description = EXCLUDED.description, category = EXCLUDED.category,
    cashflow_class = EXCLUDED.cashflow_class, frequency = EXCLUDED.frequency, payment_day = EXCLUDED.payment_day,
    payment_months = EXCLUDED.payment_months, currency = EXCLUDED.currency,
    amount_per_payment = EXCLUDED.amount_per_payment, estimated_eur_per_payment = EXCLUDED.estimated_eur_per_payment,
    amount_type = EXCLUDED.amount_type, valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
    total_commitment = EXCLUDED.total_commitment, match_keyword = EXCLUDED.match_keyword,
    source = EXCLUDED.source, notes = EXCLUDED.notes;

-- Balance anchors (KPI_SPEC.md §0.2). The two 2025 values are stated in
-- Knowledge/pizzeria_amalfi_ai_knowledge_2025.md §8; the opening balance is derived from them.
INSERT INTO account_balance_anchors (account_number, anchor_date, balance, currency, is_derived, source)
VALUES
    ('DE89370400440532013000', '2024-12-31', 2500.00, 'EUR', TRUE,
     'Derived opening balance: documented 2025-12-31 balance (33,266.35) minus net 2025 transactions (30,766.35 EUR, CHF converted at the rates in the payment references). Cross-check: documented 2025-06-30 balance (-20,098.69) implies the same value.'),
    ('DE89370400440532013000', '2025-06-30', -20098.69, 'EUR', FALSE,
     'Knowledge/pizzeria_amalfi_ai_knowledge_2025.md §8: bank account balance at the end of June 2025'),
    ('DE89370400440532013000', '2025-12-31', 33266.35, 'EUR', FALSE,
     'Knowledge/pizzeria_amalfi_ai_knowledge_2025.md §8: closing balance on December 31, 2025')
ON CONFLICT (account_number, anchor_date) DO UPDATE SET
    balance = EXCLUDED.balance, currency = EXCLUDED.currency, is_derived = EXCLUDED.is_derived, source = EXCLUDED.source;

COMMIT;
