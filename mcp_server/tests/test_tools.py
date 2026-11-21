"""Unit tests that need no database:  cd mcp_server && python -m pytest -q"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools  # noqa: E402
from db import ToolInputError, effective_cutoff, parse_date, to_jsonable  # noqa: E402


def test_cutoff_takes_earliest_source():
    assert effective_cutoff("2025-12-31", "2025-06-30") == date(2025, 6, 30)
    assert effective_cutoff("2025-06-30", None) == date(2025, 6, 30)
    assert effective_cutoff(None, "2025-12-31") == date(2025, 12, 31)
    assert effective_cutoff(None, None) is None


def test_bad_header_is_rejected():
    with pytest.raises(ToolInputError):
        effective_cutoff("30.06.2025", None)


def test_range_is_clipped_to_cutoff():
    d_from, d_to, notes = tools.resolve_range("2025-06-01", "2025-07-31", date(2025, 6, 30))
    assert (d_from, d_to) == (date(2025, 6, 1), date(2025, 6, 30))
    assert any("clipped" in n for n in notes)


def test_range_defaults_date_to_cutoff():
    _, d_to, _ = tools.resolve_range("2025-06-01", None, date(2025, 6, 30))
    assert d_to == date(2025, 6, 30)


def test_range_entirely_in_future_fails():
    with pytest.raises(ToolInputError, match="Empty range"):
        tools.resolve_range("2025-07-01", "2025-07-31", date(2025, 6, 30))


def test_range_too_long_fails():
    with pytest.raises(ToolInputError, match="maximum"):
        tools.resolve_range("2024-01-01", "2025-12-31", None)


def test_range_requires_date_from():
    with pytest.raises(ToolInputError, match="date_from"):
        tools.resolve_range(None, "2025-06-30", None)


def test_parse_and_serialize():
    from decimal import Decimal
    assert parse_date("2025-06-30", "d") == date(2025, 6, 30)
    assert to_jsonable({"a": Decimal("1.50"), "d": date(2025, 1, 2), "s": "EUR "}) == {"a": 1.5, "d": "2025-01-02",
                                                                                        "s": "EUR"}


def test_group_by_options_are_whitelisted():
    assert set(tools.GROUP_BY_SQL) == {"day", "week", "month", "transaction_type", "counterparty", "cashflow_class"}


def test_classification_matches_kpi_engine():
    config_py = ROOT.parent / "backend" / "kpi_engine" / "config.py"
    if not config_py.exists():
        pytest.skip("kpi_engine not available (built outside the repo)")
    spec = importlib.util.spec_from_file_location("kpi_engine_config", config_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    assert tools.TRANSACTION_TYPE_CLASSES == module.TRANSACTION_TYPE_CLASSES
