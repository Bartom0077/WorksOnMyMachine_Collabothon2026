"""Engine parameters. Defaults are documented in KPI_SPEC.md §4."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

ENGINE_NAME = "amalfi-kpi-engine"
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
REPORTING_CURRENCY = "EUR"

# Transaction classes (KPI_SPEC.md §0.4)
OPERATING_INFLOW = "OPERATING_INFLOW"
FIXED_OUTFLOW = "FIXED_OUTFLOW"
VARIABLE_OUTFLOW = "VARIABLE_OUTFLOW"
OTHER_INFLOW = "OTHER_INFLOW"
OTHER_OUTFLOW = "OTHER_OUTFLOW"

TRANSACTION_TYPE_CLASSES = {
    "CARD SETTLEMENT (DEBIT)": OPERATING_INFLOW,
    "CREDIT CARD PAYOUT": OPERATING_INFLOW,
    "SALARY PAYMENT": FIXED_OUTFLOW,
    "RENT PAYMENT": FIXED_OUTFLOW,
    "TAX OFFICE TRANSFER": FIXED_OUTFLOW,
    "DIRECT DEBIT": FIXED_OUTFLOW,
    "INTERNATIONAL WIRE TRANSFER": FIXED_OUTFLOW,
    "SUPPLIER PAYMENT": VARIABLE_OUTFLOW,
    "GIRO CARD PAYMENT": VARIABLE_OUTFLOW,
}


@dataclass(frozen=True)
class EngineConfig:
    forecast_horizons: tuple = (7, 14, 30)
    run_rate_lookback_days: int = 28
    alt_run_rate_lookback_days: int = 90
    buffer_fixed_horizon_days: int = 30
    buffer_variable_reserve_days: int = 7
    trend_window_days: int = 30
    trend_inflow_warning_pct: Decimal = Decimal("-10")
    trend_inflow_critical_pct: Decimal = Decimal("-20")
    trend_divergence_warning_pp: Decimal = Decimal("15")
    trend_direction_threshold_pct: Decimal = Decimal("5")
    burn_ratio_critical_pct: Decimal = Decimal("10")
    coverage_healthy: Decimal = Decimal("1.5")
    coverage_warning: Decimal = Decimal("1.0")
    buffer_funding_warning: Decimal = Decimal("0.5")
    stale_data_days: int = 3
    revenue_interruption_days: int = 7
    excess_liquidity_min_eur: Decimal = Decimal("10000")
    transaction_type_classes: dict = field(default_factory=lambda: dict(TRANSACTION_TYPE_CLASSES))

    @property
    def forecast_horizon_days(self) -> int:
        return max(self.forecast_horizons)

    def to_json(self) -> dict:
        out = {}
        for key, value in asdict(self).items():
            if key == "transaction_type_classes":
                continue
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, tuple):
                value = list(value)
            out[key] = value
        return out
