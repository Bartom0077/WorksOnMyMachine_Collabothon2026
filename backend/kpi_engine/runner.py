"""Entry point for the backend orchestration layer and the command line.

Python::

    from kpi_engine import run_kpi_engine
    result = run_kpi_engine("2025-06-30")          # computes + upserts kpi_snapshots
    result["snapshot_id"], result["metrics"]["signals"]["scenario_hint"]

CLI::

    python -m kpi_engine.runner --as-of-date 2025-06-30 [--to-date 2025-12-31] [--dry-run] [--print]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from typing import List, Optional, Union

from .config import EngineConfig
from .engine import compute_metrics
from . import repository as repo

log = logging.getLogger("kpi_engine")


class KpiEngineError(RuntimeError):
    pass


def parse_date(value: Union[str, date]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise KpiEngineError(f"Invalid as_of_date {value!r}; expected YYYY-MM-DD") from exc


def run_kpi_engine(
    as_of_date: Union[str, date],
    *,
    dsn: Optional[str] = None,
    connection=None,
    persist: bool = True,
    account_number: Optional[str] = None,
    config: EngineConfig = EngineConfig(),
) -> dict:
    """Compute the eight KPIs for ``as_of_date`` and upsert the ``kpi_snapshots`` row.

    Returns ``{"snapshot_id", "snapshot_date", "created", "persisted", "metrics"}``.
    ``created`` is False when an existing snapshot for that date was updated.
    Pass an open psycopg ``connection`` to reuse it (the caller then owns commit/close),
    otherwise a connection is opened from ``dsn`` / ``DATABASE_URL`` / ``PG*`` env vars.
    """
    as_of = parse_date(as_of_date)
    own_conn = connection is None
    conn = repo.connect(dsn) if own_conn else connection
    try:
        account = repo.resolve_account(conn, account_number)
        transactions = repo.load_transactions(conn, as_of, account)
        obligations = repo.load_obligations(conn)
        anchors = repo.load_anchors(conn, as_of, account)
        metrics = compute_metrics(as_of, transactions, obligations, anchors, config, account_number=account)

        snapshot_id, created = None, None
        if persist:
            snapshot_id, created = repo.upsert_snapshot(conn, as_of, metrics)
        if own_conn:
            conn.commit()
        log.info("KPI snapshot %s for %s (%s, scenario=%s, data_quality=%s)", snapshot_id, as_of,
                 "created" if created else "updated" if persist else "not persisted",
                 metrics["signals"]["scenario_hint"], metrics["data_quality"]["status"])
        return {"snapshot_id": snapshot_id, "snapshot_date": as_of.isoformat(), "created": created,
                "persisted": persist, "metrics": metrics}
    except repo.RepositoryError as exc:
        if own_conn:
            conn.rollback()
        raise KpiEngineError(str(exc)) from exc
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def run_kpi_engine_range(start: Union[str, date], end: Union[str, date], **kwargs) -> List[dict]:
    """Backfill one snapshot per day in [start, end] (e.g. for dashboard history)."""
    first, last = parse_date(start), parse_date(end)
    if last < first:
        raise KpiEngineError("--to-date must not be before --as-of-date")
    results, day = [], first
    while day <= last:
        results.append(run_kpi_engine(day, **kwargs))
        day += timedelta(days=1)
    return results


def _summary(result: dict) -> str:
    m = result["metrics"]
    k = m["kpis"]
    return (f"{result['snapshot_date']}  id={result['snapshot_id']}  {('created' if result['created'] else 'updated') if result['persisted'] else 'dry-run'}"
            f"  status={m['overall_status']}  scenario={m['signals']['scenario_hint']}  dq={m['data_quality']['status']}\n"
            f"  balance={k['current_cash_balance_eur']}  buffer={k['minimum_cash_buffer_eur']}  "
            f"min30={k['forecast_min_balance_30d_eur']}  gap30={k['liquidity_gap_30d_eur']}  "
            f"surplus={k['deployable_cash_surplus_eur']}  coverage={k['fixed_cost_coverage_ratio']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Autonomous CFO — deterministic KPI engine")
    parser.add_argument("--as-of-date", required=True, help="Analysis date YYYY-MM-DD (end of day)")
    parser.add_argument("--to-date", help="Optional last date for a daily backfill")
    parser.add_argument("--dsn", help="PostgreSQL DSN (default: DATABASE_URL / PG* env vars)")
    parser.add_argument("--account", help="Account number (default: the single account in the DB)")
    parser.add_argument("--dry-run", action="store_true", help="Compute only, do not write kpi_snapshots")
    parser.add_argument("--print", dest="print_json", action="store_true", help="Print the metrics JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    kwargs = dict(dsn=args.dsn, persist=not args.dry_run, account_number=args.account)
    try:
        if args.to_date:
            results = run_kpi_engine_range(args.as_of_date, args.to_date, **kwargs)
        else:
            results = [run_kpi_engine(args.as_of_date, **kwargs)]
    except KpiEngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for r in results:
        print(json.dumps(r["metrics"], indent=2, ensure_ascii=False) if args.print_json else _summary(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
