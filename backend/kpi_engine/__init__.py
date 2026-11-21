"""Autonomous CFO — deterministic KPI engine. Methodology: KPI_SPEC.md (repo root)."""
from .config import EngineConfig
from .engine import compute_metrics, forecast_balance
from .runner import KpiEngineError, run_forecast, run_kpi_engine, run_kpi_engine_range

__all__ = ["EngineConfig", "KpiEngineError", "compute_metrics", "forecast_balance", "run_forecast",
           "run_kpi_engine", "run_kpi_engine_range"]
