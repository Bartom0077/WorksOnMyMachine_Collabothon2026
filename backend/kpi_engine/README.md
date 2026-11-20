# KPI engine (`kpi_engine`)

Deterministic calculation of the eight Autonomous CFO KPIs for a given `as_of_date`, persisted to
`kpi_snapshots`. Methodology and JSON contract: [`KPI_SPEC.md`](../../KPI_SPEC.md).

```
kpi_engine/
  models.py      dataclasses: Transaction, FixedObligation, BalanceAnchor
  config.py      EngineConfig (all thresholds/windows), transaction-type classification
  money.py       Decimal helpers (cents, ROUND_HALF_UP)
  fx.py          CHF→EUR conversion from rates embedded in payment references
  schedule.py    fixed-obligation projection (rollover, finite commitments, monthly run-rate)
  kpis.py        pure KPI formulas + status rules
  engine.py      compute_metrics(): assembles the metrics JSON (pure, no I/O)
  repository.py  PostgreSQL reads/writes (psycopg 3) — no calculation logic
  runner.py      run_kpi_engine() entry point + CLI
```

## Prerequisites

```bash
pip install -r backend/requirements.txt          # psycopg[binary]
# DB tables + reference data (idempotent, additive):
psql "$DATABASE_URL" -f db/schema.sql -f db/kpi_reference_data.sql
```

`db/setup.sh` already runs both files when (re)deploying the database on OpenShift.

## Running

Connection: `--dsn`, else `DATABASE_URL`, else libpq env vars (`PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, …).

```bash
cd backend
# local access to the cluster DB
oc port-forward -n autonomous-cfo svc/amalfi-db 15432:5432 &
export DATABASE_URL="postgresql://amalfi:<password>@localhost:15432/amalfi"

python -m kpi_engine --as-of-date 2025-06-30            # compute + upsert snapshot, print summary
python -m kpi_engine --as-of-date 2025-12-31 --print    # print full metrics JSON
python -m kpi_engine --as-of-date 2025-06-30 --dry-run  # compute only
python -m kpi_engine --as-of-date 2025-01-01 --to-date 2025-12-31   # daily backfill (dashboard history)
```

Re-running a date updates the existing row (same `id`, so `agent_proposals.snapshot_id` stays valid).

## Backend integration

```python
from kpi_engine import run_kpi_engine, KpiEngineError

result = run_kpi_engine(as_of_date)            # "YYYY-MM-DD" or datetime.date
result["snapshot_id"]                          # kpi_snapshots.id  → agent_proposals.snapshot_id
result["created"]                              # False if the day was recalculated
metrics = result["metrics"]                    # same JSON as stored in kpi_snapshots.metrics
metrics["signals"]["scenario_hint"]            # LIQUIDITY_SHORTAGE | EXCESS_LIQUIDITY | OPTIMAL
metrics["data_quality"]["status"]              # OK | DEGRADED | UNRELIABLE
```

Pass `connection=<psycopg connection>` to reuse an existing connection/transaction (the caller then
commits). `KpiEngineError` is raised for invalid dates or DB/configuration problems.

For the AI agent, the useful inputs are `kpis` (numbers), `kpi_metadata[].status`, `signals`
(scenario facts) and `data_quality.flags` (what is estimated or missing). The agent should never
recompute from transactions (SPEC.md §5.1).

The OpenShift backend deployment gets `PG*` variables from the `amalfi-db` secret (`deploy/backend.yaml`).

## Tests

```bash
cd backend
python -m pytest -q                                   # unit + real-data tests (no DB needed)
KPI_TEST_DATABASE_URL="postgresql://…" python -m pytest -q tests/test_integration_db.py
```

Integration tests create and drop a temporary schema `kpi_test_*`; real tables are not touched.
