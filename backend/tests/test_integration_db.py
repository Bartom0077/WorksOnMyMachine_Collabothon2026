"""PostgreSQL integration: schema + seed SQL, persistence, idempotent re-runs, DB == CSV results.

Runs only when KPI_TEST_DATABASE_URL is set (e.g. via `oc port-forward svc/amalfi-db 5432:5432`).
Everything happens in a throw-away schema `kpi_test_<random>` that is dropped afterwards, so the
real tables are never touched.
"""
import hashlib
import json
import os
import uuid
from datetime import date

import pytest

from conftest import REPO, load_statement_rows, load_statement, load_obligations, ANCHORS, FIXED_TIME
from kpi_engine.engine import compute_metrics

DSN = os.environ.get("KPI_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="KPI_TEST_DATABASE_URL not set")

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def schema():
    name = f"kpi_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(f"CREATE SCHEMA {name}")
    admin.execute(f"SET search_path TO {name}")
    admin.execute((REPO / "db" / "schema.sql").read_text(encoding="utf-8"))
    admin.execute((REPO / "db" / "kpi_reference_data.sql").read_text(encoding="utf-8"))
    rows = load_statement_rows()
    with admin.cursor() as cur:
        cur.executemany(
            """INSERT INTO bank_transactions (account_number, booking_date, value_date, transaction_type,
                   payment_reference, beneficiary_payer, counterparty_account, bank_code, amount, currency, info, row_hash)
               VALUES (%s, to_date(%s, 'DD.MM.YY'), to_date(%s, 'DD.MM.YY'), %s, NULLIF(%s, ''), NULLIF(%s, ''),
                       NULLIF(%s, ''), NULLIF(%s, ''), replace(%s, ',', '.')::numeric, %s, NULLIF(%s, ''), %s)""",
            [(r["Account Number"], r["Booking Date"], r["Value Date"], r["Transaction Type"], r["Payment Reference"],
              r["Beneficiary/Payer"], r["Counterparty Account"], r["Bank Code"], r["Amount"], r["Currency"], r["Info"],
              hashlib.sha256(";".join(r.values()).encode()).hexdigest()) for r in rows],
        )
    yield name
    admin.execute(f"DROP SCHEMA {name} CASCADE")
    admin.close()


@pytest.fixture
def conn(schema):
    c = psycopg.connect(DSN, options=f"-c search_path={schema}")
    yield c
    c.rollback()
    c.close()


def comparable(m):
    m = json.loads(json.dumps(m))
    m.pop("engine")
    m.pop("account_number")
    m["data_quality"]["flags"] = [f for f in m["data_quality"]["flags"] if f["code"] != "FUTURE_TRANSACTIONS_IGNORED"]
    m["data_quality"]["status"] = None
    for a in [m["data_quality"]["balance_source"].get("anchor")] + [
            x["details"].get("balance_source", {}).get("anchor") for x in m["kpi_metadata"]]:
        if a:
            a.pop("source", None)
    for f in m["data_quality"]["flags"]:
        if f["code"] == "BALANCE_ANCHOR_DERIVED":
            f["message"] = f["message"].split(" EUR).")[0]
    return m


@pytest.mark.parametrize("as_of", ["2025-06-30", "2025-12-31", "2025-09-14"])
def test_db_run_matches_csv_computation(conn, as_of):
    from kpi_engine import run_kpi_engine
    result = run_kpi_engine(as_of, connection=conn, persist=False)
    expected = compute_metrics(date.fromisoformat(as_of), load_statement(), load_obligations(), ANCHORS, computed_at=FIXED_TIME)
    assert comparable(result["metrics"]) == comparable(expected)


def test_persist_and_idempotent_rerun(conn, schema):
    from kpi_engine import run_kpi_engine
    first = run_kpi_engine("2025-06-30", connection=conn)
    conn.commit()
    assert first["created"] is True and first["snapshot_id"] is not None

    # A proposal referencing the snapshot must survive a recalculation (row is updated, not replaced).
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO agent_proposals (snapshot_id, as_of_date, scenario_type, title, plain_language_summary,
                       reasoning_details, recommended_action) VALUES (%s, '2025-06-30', 'LIQUIDITY_SHORTAGE', 't', 's', '{}', '{}')""",
                    (first["snapshot_id"],))
    conn.commit()

    second = run_kpi_engine("2025-06-30", connection=conn)
    conn.commit()
    assert second["created"] is False
    assert second["snapshot_id"] == first["snapshot_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(id) FROM kpi_snapshots WHERE snapshot_date = '2025-06-30'")
        assert cur.fetchone() == (1, first["snapshot_id"])
        cur.execute("SELECT count(*) FROM agent_proposals WHERE snapshot_id = %s", (first["snapshot_id"],))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT metrics FROM kpi_snapshots WHERE id = %s", (first["snapshot_id"],))
        stored = cur.fetchone()[0]
    assert stored["kpis"] == second["metrics"]["kpis"]
    assert stored["as_of_date"] == "2025-06-30"
    assert stored["signals"]["scenario_hint"] == "LIQUIDITY_SHORTAGE"


def test_jsonb_queryable_by_backend(conn):
    from kpi_engine import run_kpi_engine
    run_kpi_engine("2025-12-31", connection=conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("""SELECT (metrics->'kpis'->>'current_cash_balance_eur')::numeric,
                              jsonb_array_length(metrics->'kpi_metadata'), metrics->'signals'->>'scenario_hint'
                       FROM kpi_snapshots WHERE snapshot_date = '2025-12-31'""")
        balance, n_meta, hint = cur.fetchone()
    assert float(balance) == 33266.35 and n_meta == 14 and hint in {"OPTIMAL", "EXCESS_LIQUIDITY"}


def test_seed_sql_is_idempotent(schema):
    with psycopg.connect(DSN, options=f"-c search_path={schema}", autocommit=True) as c:
        c.execute((REPO / "db" / "schema.sql").read_text(encoding="utf-8"))
        c.execute((REPO / "db" / "kpi_reference_data.sql").read_text(encoding="utf-8"))
        assert c.execute("SELECT count(*) FROM fixed_obligations").fetchone()[0] == 11
        assert c.execute("SELECT count(*) FROM account_balance_anchors").fetchone()[0] == 3


def test_missing_reference_tables_degrade_gracefully(schema):
    """An environment without the new tables still gets a snapshot, flagged as unreliable."""
    from kpi_engine import run_kpi_engine
    bare = f"{schema}_bare"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {bare}")
        try:
            admin.execute(f"""CREATE TABLE {bare}.bank_transactions AS SELECT * FROM {schema}.bank_transactions;
                              CREATE TABLE {bare}.kpi_snapshots (id SERIAL PRIMARY KEY, snapshot_date DATE UNIQUE NOT NULL,
                                  metrics JSONB NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);""")
            with psycopg.connect(DSN, options=f"-c search_path={bare}") as c:
                r = run_kpi_engine("2025-12-31", connection=c)
                codes = {f["code"] for f in r["metrics"]["data_quality"]["flags"]}
                assert {"BALANCE_ANCHOR_MISSING", "OBLIGATION_SCHEDULE_MISSING"} <= codes
                assert r["snapshot_id"] is not None
        finally:
            admin.execute(f"DROP SCHEMA {bare} CASCADE")


def test_run_forecast_reads_db_and_writes_nothing(conn):
    from kpi_engine import run_forecast
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kpi_snapshots")
        before = cur.fetchone()[0]
    f = run_forecast("2025-06-30", 45, connection=conn)
    expected = compute_metrics(date(2025, 6, 30), load_statement(), load_obligations(), ANCHORS, computed_at=FIXED_TIME)
    assert f["daily"][:31] == expected["forecast"]["daily"]
    assert len(f["daily"]) == 46 and f["target_date"] == "2025-08-14"
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kpi_snapshots")
        assert cur.fetchone()[0] == before
