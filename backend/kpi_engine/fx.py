"""Currency conversion to EUR (KPI_SPEC.md §0.3).

Foreign-currency rows carry the applied rate in the payment reference, e.g.
``Silent Partnership Capital Tranche 01/12 (4166.67 CHF @ 1.042 EUR/CHF)``.
The rate is read as EUR per 1 unit of the foreign currency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from .config import REPORTING_CURRENCY
from .models import Transaction
from .money import cents

RATE_PATTERN = re.compile(r"@\s*(\d+(?:[.,]\d+)?)\s*EUR\s*/\s*([A-Z]{3})", re.IGNORECASE)

FX_NATIVE = "NATIVE"
FX_EMBEDDED = "EMBEDDED_RATE"
FX_LAST_OBSERVED = "LAST_OBSERVED_RATE"
FX_REFERENCE = "REFERENCE_ESTIMATE"
FX_MISSING = "MISSING"


def extract_rate(reference: Optional[str], currency: str) -> Optional[Decimal]:
    """Return the EUR-per-unit rate embedded in ``reference`` for ``currency``, if any."""
    if not reference:
        return None
    for match in RATE_PATTERN.finditer(reference):
        if match.group(2).upper() == currency.upper():
            rate = Decimal(match.group(1).replace(",", "."))
            if rate > 0:
                return rate
    return None


@dataclass(frozen=True)
class ConvertedTransaction:
    tx: Transaction
    amount_eur: Optional[Decimal]  # None when no rate could be determined
    fx_rate: Optional[Decimal]
    fx_source: str

    @property
    def booking_date(self) -> date:
        return self.tx.booking_date


class FxConverter:
    """Converts transactions in chronological order so fallbacks never use later rates."""

    def __init__(self, reference_rates: Optional[Dict[str, Decimal]] = None):
        self.reference_rates = {k.upper(): v for k, v in (reference_rates or {}).items()}
        self.observed: Dict[str, Tuple[Decimal, date]] = {}

    def convert_all(self, transactions: Iterable[Transaction]) -> List[ConvertedTransaction]:
        ordered = sorted(transactions, key=lambda t: (t.booking_date, t.id or 0))
        return [self.convert(t) for t in ordered]

    def convert(self, tx: Transaction) -> ConvertedTransaction:
        ccy = tx.currency.strip().upper()
        if ccy == REPORTING_CURRENCY:
            return ConvertedTransaction(tx, cents(tx.amount), None, FX_NATIVE)
        rate = extract_rate(tx.payment_reference, ccy)
        if rate is not None:
            self.observed[ccy] = (rate, tx.booking_date)
            return ConvertedTransaction(tx, cents(tx.amount * rate), rate, FX_EMBEDDED)
        if ccy in self.observed:
            rate = self.observed[ccy][0]
            return ConvertedTransaction(tx, cents(tx.amount * rate), rate, FX_LAST_OBSERVED)
        if ccy in self.reference_rates:
            rate = self.reference_rates[ccy]
            return ConvertedTransaction(tx, cents(tx.amount * rate), rate, FX_REFERENCE)
        return ConvertedTransaction(tx, None, None, FX_MISSING)

    def rate_for_forecast(self, currency: str) -> Tuple[Optional[Decimal], str, Optional[date]]:
        """Latest known rate (observed so far) for converting future obligations."""
        ccy = currency.upper()
        if ccy == REPORTING_CURRENCY:
            return Decimal(1), FX_NATIVE, None
        if ccy in self.observed:
            rate, observed_on = self.observed[ccy]
            return rate, FX_LAST_OBSERVED, observed_on
        if ccy in self.reference_rates:
            return self.reference_rates[ccy], FX_REFERENCE, None
        return None, FX_MISSING, None
