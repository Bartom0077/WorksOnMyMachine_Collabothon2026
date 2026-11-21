"""Module 1: every 2025 transaction gets the category from ROZWIAZANIE.md §6.1."""
from collections import Counter, defaultdict
from decimal import Decimal

import pytest

from categories import CATEGORIES, CategoryGroup, categorize

# category -> (count, total in transaction currency) from the §6.1 table
EXPECTED_2025 = {
    "card_revenue_debit": (335, "109105.08"),
    "card_revenue_credit": (143, "435319.19"),
    "supplier_invoices": (239, "-180193.06"),
    "fresh_produce": (292, "-76825.66"),
    "rent": (12, "-45600.00"),
    "payroll_tax": (12, "-10560.00"),
    "investment_outflow": (12, "-50000.04"),
    "owner_withdrawal": (12, "-38400.00"),
    "net_payroll": (36, "-64800.00"),
    "social_security": (12, "-31800.00"),
    "utilities": (12, "-7800.00"),
    "income_tax_advance": (4, "-4600.00"),
    "trade_tax_advance": (4, "-900.00"),
}


def _categorize(tx) -> str:
    return categorize(tx.transaction_type, tx.beneficiary_payer, tx.payment_reference, tx.amount)


def test_all_real_rows_get_a_specific_category(statement):
    keys = [_categorize(tx) for tx in statement]
    assert len(keys) == 1125
    assert not [key for key in keys if key.startswith("other_")]


def test_counts_and_totals_match_the_specification(statement):
    counts, totals = Counter(), defaultdict(Decimal)
    for tx in statement:
        key = _categorize(tx)
        counts[key] += 1
        totals[key] += tx.amount
    assert {key: (counts[key], str(totals[key])) for key in counts} == EXPECTED_2025


def test_groups_follow_the_specification():
    groups = defaultdict(set)
    for category in CATEGORIES.values():
        groups[category.group].add(category.key)
    assert groups[CategoryGroup.VARIABLE_REVENUE] == {"card_revenue_debit", "card_revenue_credit"}
    assert groups[CategoryGroup.VARIABLE_COST] == {"supplier_invoices", "fresh_produce"}
    assert groups[CategoryGroup.OTHER] == {"other_inflow", "other_outflow"}
    assert len(groups[CategoryGroup.FIXED_COST]) == 9
    assert [c.key for c in CATEGORIES.values() if c.deferrable] == ["investment_outflow"]
    assert {c.key for c in CATEGORIES.values() if c.cadence_months == 3} == {"income_tax_advance", "trade_tax_advance"}


@pytest.mark.parametrize("transaction_type, payer, reference, amount, expected", [
    ("SALARY PAYMENT", "Vincenzo Amalfi", "Owner Salary Withdrawal", "-3200", "owner_withdrawal"),
    ("salary payment", "Rosa Pavese", "Net Salary Rosa Pavese", "-2100", "net_payroll"),
    ("DIRECT DEBIT", "City Energy", "Monthly ENERGY & GAS Advance", "-650", "utilities"),
    ("DIRECT DEBIT", "Gym", "Membership", "-30", "other_outflow"),
    ("CASH DEPOSIT", None, None, "100", "other_inflow"),
    ("BANK FEE", None, None, "-5", "other_outflow"),
])
def test_matching_rules(transaction_type, payer, reference, amount, expected):
    assert categorize(transaction_type, payer, reference, Decimal(amount)) == expected
