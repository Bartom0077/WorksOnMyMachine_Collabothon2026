"""Data tools exposed to the LLM. Pure functions of (connection, cutoff, arguments) -> JSON-able dict.

Rules (ROZWIAZANIE.md section 11, KPI_SPEC.md):
* Nothing here computes balances or forecasts. The forecast the owner sees is computed by the backend and
  handed to the model in the prompt; KPI values are only read from ``kpi_snapshots`` (deterministic engine).
* Every read is capped at the as-of ``cutoff`` so the agent never sees future data.
* Only whitelisted tables/columns, parameterised SQL, bounded result sizes.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import psycopg
from psycopg import sql

from db import ToolInputError, fetch_dicts, parse_date

MAX_RANGE_DAYS = 400
MAX_TRANSACTIONS = 200
MAX_HISTORY_POINTS = 120
MAX_GROUPS = 400
MAX_COUNTERPARTY_GROUPS = 50

# Mirror of backend/kpi_engine/config.py TRANSACTION_TYPE_CLASSES (KPI_SPEC.md §0.4);
# tests/test_tools.py fails if the two drift apart.
TRANSACTION_TYPE_CLASSES = {
    "CARD SETTLEMENT (DEBIT)": "OPERATING_INFLOW",
    "CREDIT CARD PAYOUT": "OPERATING_INFLOW",
    "SALARY PAYMENT": "FIXED_OUTFLOW",
    "RENT PAYMENT": "FIXED_OUTFLOW",
    "TAX OFFICE TRANSFER": "FIXED_OUTFLOW",
    "DIRECT DEBIT": "FIXED_OUTFLOW",
    "INTERNATIONAL WIRE TRANSFER": "FIXED_OUTFLOW",
    "SUPPLIER PAYMENT": "VARIABLE_OUTFLOW",
    "GIRO CARD PAYMENT": "VARIABLE_OUTFLOW",
}

RAW_AMOUNTS_NOTE = ("Amounts are in the original transaction currency (non-EUR rows are NOT converted). "
                    "Do not derive balances or forecasts from these sums.")


def _cashflow_class_sql() -> sql.Composable:
    whens = [sql.SQL("WHEN {} THEN {}").format(sql.Literal(t), sql.Literal(c))
             for t, c in TRANSACTION_TYPE_CLASSES.items()]
    return sql.SQL("CASE transaction_type {} ELSE CASE WHEN amount >= 0 THEN 'OTHER_INFLOW' "
                   "ELSE 'OTHER_OUTFLOW' END END").format(sql.SQL(" ").join(whens))


GROUP_BY_SQL = {
    "day": sql.SQL("to_char(booking_date, 'YYYY-MM-DD')"),
    "week": sql.SQL("to_char(date_trunc('week', booking_date), 'YYYY-MM-DD')"),  # ISO week, Monday start
    "month": sql.SQL("to_char(booking_date, 'YYYY-MM')"),
    "transaction_type": sql.SQL("transaction_type"),
    "counterparty": sql.SQL("COALESCE(NULLIF(beneficiary_payer, ''), '(unknown)')"),
    "cashflow_class": _cashflow_class_sql(),
}


# --------------------------------------------------------------------------- helpers

def resolve_range(date_from: Optional[str], date_to: Optional[str], cutoff: Optional[date],
                  max_days: int = MAX_RANGE_DAYS) -> tuple:
    """Validate a date range and clip it to the cutoff. Returns (from, to, notes)."""
    d_from = parse_date(date_from, "date_from")
    d_to = parse_date(date_to, "date_to")
    notes = []
    if d_from is None:
        raise ToolInputError("date_from is required (YYYY-MM-DD).")
    if d_to is None:
        d_to = cutoff if cutoff else d_from
        notes.append(f"date_to not given; using {d_to.isoformat()}.")
    if cutoff and d_to > cutoff:
        notes.append(f"date_to {d_to.isoformat()} is after the analysis date; clipped to {cutoff.isoformat()} "
                     "(future data is not available).")
        d_to = cutoff
    if d_from > d_to:
        raise ToolInputError(f"Empty range: date_from {d_from.isoformat()} is after date_to {d_to.isoformat()}"
                             + (f" (analysis date is {cutoff.isoformat()})." if cutoff else "."))
    if (d_to - d_from).days + 1 > max_days:
        raise ToolInputError(f"Range is {(d_to - d_from).days + 1} days; maximum is {max_days}. Narrow the range "
                             "or use get_cashflow_summary with group_by='month'.")
    return d_from, d_to, notes


def _cap(requested: Optional[date], cutoff: Optional[date]) -> Optional[date]:
    if requested is None:
        return cutoff
    if cutoff is None:
        return requested
    return min(requested, cutoff)


def _meta(cutoff: Optional[date], notes: Optional[list] = None) -> dict:
    out = {"analysis_date": cutoff.isoformat() if cutoff else None}
    if notes:
        out["notes"] = notes
    return out


# --------------------------------------------------------------------------- tools

def get_data_overview(conn: psycopg.Connection, cutoff: Optional[date]) -> dict:
    limit = cutoff or date.max
    tx = fetch_dicts(conn, """
        SELECT count(*) AS transaction_count, min(booking_date) AS first_booking_date,
               max(booking_date) AS last_booking_date,
               array_agg(DISTINCT account_number) AS accounts, array_agg(DISTINCT currency) AS currencies
        FROM bank_transactions WHERE booking_date <= %s""", (limit,))[0]
    types = fetch_dicts(conn, """
        SELECT transaction_type, count(*) AS count, sum(CASE WHEN amount >= 0 THEN 1 ELSE 0 END) AS inflow_count
        FROM bank_transactions WHERE booking_date <= %s GROUP BY 1 ORDER BY 2 DESC""", (limit,))
    for t in types:
        t["cashflow_class"] = TRANSACTION_TYPE_CLASSES.get(t["transaction_type"], "OTHER")
    snaps = fetch_dicts(conn, """
        SELECT count(*) AS count, min(snapshot_date) AS first_date, max(snapshot_date) AS last_date
        FROM kpi_snapshots WHERE snapshot_date <= %s""", (limit,))[0]
    obligations = fetch_dicts(conn, "SELECT count(*) AS count FROM fixed_obligations")[0]
    return {
        **_meta(cutoff),
        "bank_transactions": tx,
        "transaction_types": types,
        "kpi_snapshots": snaps,
        "fixed_obligations": obligations,
        "hint": "get_kpi_snapshot describes the analysis date; history and transactions explain causes.",
    }


def get_kpi_snapshot(conn: psycopg.Connection, cutoff: Optional[date], date_: Optional[str] = None,
                     include_forecast: bool = False, include_kpi_details: bool = False) -> dict:
    requested = parse_date(date_, "date")
    target = _cap(requested, cutoff)
    notes = []
    if requested and cutoff and requested > cutoff:
        notes.append(f"Requested {requested.isoformat()} is after the analysis date; using {cutoff.isoformat()}.")
    if target is None:
        rows = fetch_dicts(conn, "SELECT snapshot_date, metrics FROM kpi_snapshots "
                                 "ORDER BY snapshot_date DESC LIMIT 1")
    else:
        rows = fetch_dicts(conn, "SELECT snapshot_date, metrics FROM kpi_snapshots WHERE snapshot_date <= %s "
                                 "ORDER BY snapshot_date DESC LIMIT 1", (target,))
    if not rows:
        return {**_meta(cutoff, notes), "found": False,
                "error": "No KPI snapshot on or before this date. The KPI engine has to be run for it first."}
    snap_date, m = rows[0]["snapshot_date"], rows[0]["metrics"]
    if target and snap_date != target.isoformat():
        notes.append(f"No snapshot for {target.isoformat()}; returning the latest earlier one ({snap_date}).")
    kpi_status = []
    for k in m.get("kpi_metadata", []):
        item = {key: k.get(key) for key in ("id", "label_en", "value", "unit", "status", "is_estimate")}
        if include_kpi_details:
            item["details"] = k.get("details")
        kpi_status.append(item)
    dq = m.get("data_quality", {})
    out = {
        **_meta(cutoff, notes),
        "found": True,
        "snapshot_date": snap_date,
        "currency": m.get("currency"),
        "overall_status": m.get("overall_status"),
        "kpis": m.get("kpis"),
        "kpi_status": kpi_status,
        "signals": m.get("signals"),
        "data_quality": {"status": dq.get("status"), "flags": dq.get("flags", [])},
    }
    if include_forecast:
        out["forecast"] = m.get("forecast")
    return out


def get_kpi_history(conn: psycopg.Connection, cutoff: Optional[date], date_from: str, date_to: Optional[str] = None,
                    kpi_names: Optional[List[str]] = None, step_days: int = 1) -> dict:
    d_from, d_to, notes = resolve_range(date_from, date_to, cutoff)
    if step_days < 1:
        raise ToolInputError("step_days must be >= 1.")
    rows = fetch_dicts(conn, """
        SELECT snapshot_date, metrics->'kpis' AS kpis, metrics->>'overall_status' AS overall_status,
               metrics->'signals'->>'scenario_hint' AS scenario_hint,
               metrics->'data_quality'->>'status' AS data_quality_status
        FROM kpi_snapshots WHERE snapshot_date BETWEEN %s AND %s ORDER BY snapshot_date""", (d_from, d_to))
    if not rows:
        return {**_meta(cutoff, notes), "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
                "points": [], "error": "No KPI snapshots in this range."}
    available = list(rows[-1]["kpis"].keys())
    if kpi_names:
        unknown = [n for n in kpi_names if n not in available]
        if unknown:
            raise ToolInputError(f"Unknown KPI names {unknown}. Available: {available}")
    selected = kpi_names or available
    while (len(rows) + step_days - 1) // step_days > MAX_HISTORY_POINTS:
        step_days += 1
    if step_days > 1:
        sampled = rows[::step_days]
        if sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])  # always keep the most recent point
        notes.append(f"Sampled every {step_days} days (last point always included).")
        rows = sampled
    points = [{"date": r["snapshot_date"], "overall_status": r["overall_status"],
               "scenario_hint": r["scenario_hint"], "data_quality": r["data_quality_status"],
               **{n: r["kpis"].get(n) for n in selected}} for r in rows]
    return {**_meta(cutoff, notes), "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
            "kpi_names": selected, "points": points}


def get_transactions(conn: psycopg.Connection, cutoff: Optional[date], date_from: str, date_to: Optional[str] = None,
                     transaction_type: Optional[str] = None, counterparty: Optional[str] = None,
                     direction: Optional[str] = None, min_abs_amount: Optional[float] = None,
                     sort: str = "date_desc", limit: int = 50) -> dict:
    d_from, d_to, notes = resolve_range(date_from, date_to, cutoff)
    if not 1 <= limit <= MAX_TRANSACTIONS:
        raise ToolInputError(f"limit must be between 1 and {MAX_TRANSACTIONS}.")
    order = {"date_desc": "booking_date DESC, id DESC", "date_asc": "booking_date, id",
             "amount_desc": "abs(amount) DESC, booking_date DESC"}
    if sort not in order:
        raise ToolInputError(f"sort must be one of {list(order)}.")
    where = [sql.SQL("booking_date BETWEEN %s AND %s")]
    params: list = [d_from, d_to]
    if transaction_type:
        where.append(sql.SQL("transaction_type = %s"))
        params.append(transaction_type.strip().upper())
    if counterparty:
        where.append(sql.SQL("(beneficiary_payer ILIKE %s OR payment_reference ILIKE %s)"))
        params += [f"%{counterparty.strip()}%"] * 2
    if direction:
        if direction not in ("inflow", "outflow"):
            raise ToolInputError("direction must be 'inflow' or 'outflow'.")
        where.append(sql.SQL("amount >= 0" if direction == "inflow" else "amount < 0"))
    if min_abs_amount is not None:
        where.append(sql.SQL("abs(amount) >= %s"))
        params.append(min_abs_amount)
    cond = sql.SQL(" AND ").join(where)

    rows = fetch_dicts(conn, sql.SQL("""
        SELECT id, booking_date, value_date, transaction_type, {cls} AS cashflow_class, beneficiary_payer,
               payment_reference, amount, currency, info
        FROM bank_transactions WHERE {cond} ORDER BY {order} LIMIT %s""").format(
        cls=_cashflow_class_sql(), cond=cond, order=sql.SQL(order[sort])), (*params, limit))
    totals = fetch_dicts(conn, sql.SQL("""
        SELECT currency, count(*) AS count, COALESCE(sum(amount) FILTER (WHERE amount >= 0), 0) AS inflow,
               COALESCE(sum(amount) FILTER (WHERE amount < 0), 0) AS outflow, sum(amount) AS net
        FROM bank_transactions WHERE {cond} GROUP BY currency ORDER BY currency""").format(cond=cond), params)
    matching = sum(t["count"] for t in totals)
    if matching > len(rows):
        notes.append(f"Showing {len(rows)} of {matching} matching transactions; narrow the filters to see more.")
    notes.append(RAW_AMOUNTS_NOTE)
    return {**_meta(cutoff, notes), "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
            "matching_count": matching, "totals_by_currency": totals, "transactions": rows}


def get_cashflow_summary(conn: psycopg.Connection, cutoff: Optional[date], date_from: str,
                         date_to: Optional[str] = None, group_by: str = "month",
                         transaction_type: Optional[str] = None, counterparty: Optional[str] = None) -> dict:
    d_from, d_to, notes = resolve_range(date_from, date_to, cutoff)
    if group_by not in GROUP_BY_SQL:
        raise ToolInputError(f"group_by must be one of {list(GROUP_BY_SQL)}.")
    where = [sql.SQL("booking_date BETWEEN %s AND %s")]
    params: list = [d_from, d_to]
    if transaction_type:
        where.append(sql.SQL("transaction_type = %s"))
        params.append(transaction_type.strip().upper())
    if counterparty:
        where.append(sql.SQL("(beneficiary_payer ILIKE %s OR payment_reference ILIKE %s)"))
        params += [f"%{counterparty.strip()}%"] * 2
    by_counterparty = group_by == "counterparty"
    order = sql.SQL("abs(sum(amount)) DESC") if by_counterparty else sql.SQL("1, 2")
    max_groups = MAX_COUNTERPARTY_GROUPS if by_counterparty else MAX_GROUPS
    rows = fetch_dicts(conn, sql.SQL("""
        SELECT {grp} AS "group", currency, count(*) AS tx_count,
               COALESCE(sum(amount) FILTER (WHERE amount >= 0), 0) AS inflow,
               COALESCE(sum(amount) FILTER (WHERE amount < 0), 0) AS outflow,
               sum(amount) AS net
        FROM bank_transactions WHERE {cond}
        GROUP BY 1, 2 ORDER BY {order} LIMIT %s""").format(
        grp=GROUP_BY_SQL[group_by], cond=sql.SQL(" AND ").join(where), order=order), (*params, max_groups + 1))
    if len(rows) > max_groups:
        rows = rows[:max_groups]
        notes.append(f"Only the first {max_groups} groups are shown"
                     + (" (largest absolute net first)." if by_counterparty else "; narrow the range."))
    notes.append(RAW_AMOUNTS_NOTE)
    return {**_meta(cutoff, notes), "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
            "group_by": group_by, "groups": rows}


def get_fixed_obligations(conn: psycopg.Connection, cutoff: Optional[date], active_on: Optional[str] = None,
                          category: Optional[str] = None) -> dict:
    day = parse_date(active_on, "active_on") or cutoff
    where = [sql.SQL("TRUE")]
    params: list = []
    if day:
        where.append(sql.SQL("valid_from <= %s"))
        params.append(day)
    if category:
        where.append(sql.SQL("category ILIKE %s"))
        params.append(category.strip())
    rows = fetch_dicts(conn, sql.SQL("""
        SELECT expense_id, payee, description, category, cashflow_class, frequency, payment_day, payment_months,
               currency, amount_per_payment, estimated_eur_per_payment, amount_type, valid_from, valid_to,
               total_commitment, notes
        FROM fixed_obligations WHERE {cond} ORDER BY payment_day, expense_id""").format(
        cond=sql.SQL(" AND ").join(where)), params)
    if day:
        for r in rows:
            r["documented_period_ended"] = bool(r["valid_to"] and r["valid_to"] < day.isoformat())
    notes = ["payment_months is 'all' or a ';'-separated list of months (e.g. '03;06;09;12'). valid_to is the end "
             "of the documented period; recurring costs are assumed to continue after it unless total_commitment "
             "is set."]
    if day:
        notes.insert(0, f"Obligations started on or before {day.isoformat()}.")
    return {**_meta(cutoff, notes), "obligations": rows}
