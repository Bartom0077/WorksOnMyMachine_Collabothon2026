"""Module 1 — transaction categorisation (ROZWIAZANIE.md §6).

The category is decided by ``transaction_type``. Text matching on the counterparty / payment reference is
used only for the two types that mix different costs: ``SALARY PAYMENT`` and ``DIRECT DEBIT``.
Category keys equal the ones used by the fixed-obligation reference data and the MCP server.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Optional


class CategoryGroup(StrEnum):
    VARIABLE_REVENUE = "variable_revenue"
    VARIABLE_COST = "variable_cost"
    FIXED_COST = "fixed_cost"
    OTHER = "other"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: CategoryGroup
    cadence_months: Optional[int] = None  # recurring fixed costs only: 1 = monthly, 3 = quarterly
    deferrable: bool = False


_CATEGORY_LIST = (
    Category("card_revenue_debit", "Revenue – debit cards", CategoryGroup.VARIABLE_REVENUE),
    Category("card_revenue_credit", "Revenue – credit cards", CategoryGroup.VARIABLE_REVENUE),
    Category("supplier_invoices", "Suppliers (Asimex, Fattoria La Vino)", CategoryGroup.VARIABLE_COST),
    Category("fresh_produce", "Market purchases (Central Wholesale)", CategoryGroup.VARIABLE_COST),
    Category("rent", "Rent", CategoryGroup.FIXED_COST, cadence_months=1),
    Category("payroll_tax", "Payroll tax", CategoryGroup.FIXED_COST, cadence_months=1),
    Category("investment_outflow", "Transfer to Zurich (Luigi)", CategoryGroup.FIXED_COST, cadence_months=1,
             deferrable=True),
    Category("owner_withdrawal", "Owner's salary", CategoryGroup.FIXED_COST, cadence_months=1),
    Category("net_payroll", "Staff salaries", CategoryGroup.FIXED_COST, cadence_months=1),
    Category("social_security", "Social security (Techniker Krankenkasse)", CategoryGroup.FIXED_COST,
             cadence_months=1),
    Category("utilities", "Energy and gas", CategoryGroup.FIXED_COST, cadence_months=1),
    Category("income_tax_advance", "Income tax prepayment", CategoryGroup.FIXED_COST, cadence_months=3),
    Category("trade_tax_advance", "Trade tax prepayment (Gewerbesteuer)", CategoryGroup.FIXED_COST,
             cadence_months=3),
    Category("other_inflow", "Other income", CategoryGroup.OTHER),
    Category("other_outflow", "Other expenses", CategoryGroup.OTHER),
)

CATEGORIES: dict[str, Category] = {category.key: category for category in _CATEGORY_LIST}

_TYPE_TO_CATEGORY = {
    "CARD SETTLEMENT (DEBIT)": "card_revenue_debit",
    "CREDIT CARD PAYOUT": "card_revenue_credit",
    "SUPPLIER PAYMENT": "supplier_invoices",
    "GIRO CARD PAYMENT": "fresh_produce",
    "RENT PAYMENT": "rent",
    "TAX OFFICE TRANSFER": "payroll_tax",
    "INTERNATIONAL WIRE TRANSFER": "investment_outflow",
}

_OWNER_SALARY_MARKERS = ("owner salary withdrawal", "vincenzo amalfi")

_DIRECT_DEBIT_MARKERS = (
    ("social security contributions", "social_security"),
    ("energy & gas", "utilities"),
    ("income tax prepayment", "income_tax_advance"),
    ("trade tax prepayment", "trade_tax_advance"),
)


def categorize(transaction_type: str, beneficiary_payer: Optional[str], payment_reference: Optional[str],
               amount: Decimal) -> str:
    """Return the category key of one bank transaction."""
    normalized_type = transaction_type.strip().upper()
    direct = _TYPE_TO_CATEGORY.get(normalized_type)
    if direct is not None:
        return direct
    text = f"{beneficiary_payer or ''} {payment_reference or ''}".lower()
    if normalized_type == "SALARY PAYMENT":
        return _salary_category(text)
    if normalized_type == "DIRECT DEBIT":
        return _direct_debit_category(text)
    return "other_inflow" if amount >= 0 else "other_outflow"


def _salary_category(text: str) -> str:
    is_owner = any(marker in text for marker in _OWNER_SALARY_MARKERS)
    return "owner_withdrawal" if is_owner else "net_payroll"


def _direct_debit_category(text: str) -> str:
    for marker, category in _DIRECT_DEBIT_MARKERS:
        if marker in text:
            return category
    return "other_outflow"

