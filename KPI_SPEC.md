# KPI_SPEC — Deterministic KPI Engine (Autonomous CFO / Pizzeria Amalfi)

**Version:** 1.0 · **Status:** implemented in `backend/kpi_engine/` · **Referenced from:** `SPEC.md` §4

This document is the single source of truth for *how* the eight financial KPIs are calculated,
persisted to `kpi_snapshots.metrics` and interpreted by downstream AI agents. Every number the
AI CFO uses must be reproducible from this document, the bank transactions and the reference data.

---

## 0. General principles

| Principle | Rule |
|---|---|
| Determinism | Same inputs + same `as_of_date` + same parameters ⇒ identical `metrics` (except `engine.computed_at`). No randomness, no LLM, no wall-clock dependence. |
| Analysis date | `as_of_date` is interpreted as **end of day**: all transactions with `booking_date <= as_of_date` have happened. Forecast day 1 is `as_of_date + 1`. |
| No future-data leakage | Transactions are queried with `booking_date <= as_of_date` and filtered again in the calculation layer. Balance anchors are used only if `anchor_date <= as_of_date`. FX rates used are the latest observed **on or before** the date in question. The only forward-looking input is the schedule of **contractual / documented fixed obligations** (rent, payroll, taxes, investment tranches), which a business owner knows in advance. |
| Money arithmetic | Python `Decimal`, amounts quantized to cents with `ROUND_HALF_UP`. Daily run-rates are quantized to cents *before* they are projected, so every forecast value is an exact sum of cent amounts. Values are emitted as JSON numbers with 2 decimals; ratios with 2 decimals; percentages with 1 decimal. |
| Reporting currency | **EUR** (the account `DE89370400440532013000` is an EUR account). All KPI values are in EUR. |
| Facts vs estimates | Every KPI in `kpi_metadata` carries `is_estimate`. KPIs 1, 6, 7 are facts derived from booked transactions (plus documented balance anchors); KPIs 2–5 and 8 include forward-looking estimates. |
| Don't invent | Missing data is never filled with made-up transactions. Gaps are surfaced via `data_quality.flags` and conservative fallbacks that are explicitly named. |

### 0.1 Source data

| Source | Table | Origin | Used for |
|---|---|---|---|
| Bank statement 2025 | `bank_transactions` | `Challenge/pizzeria-amalfi-bank-statement-2025-v6.csv` | All historical flows, balance roll-forward, run-rates, FX rates |
| Fixed obligation schedule | `fixed_obligations` (new) | `Knowledge/pizzeria_amalfi_fixed_outflows_2025.csv` + knowledge doc §5 (CHF 50,000 total commitment) | Scheduled outflows in forecast, buffer, coverage |
| Balance anchors | `account_balance_anchors` (new) | `Knowledge/pizzeria_amalfi_ai_knowledge_2025.md` §8 | Absolute cash balance |

Both new tables are created by `db/schema.sql` and seeded by `db/kpi_reference_data.sql` (idempotent).

### 0.2 Balance anchors — why and how

The bank statement contains **flows only, no balance column**. The knowledge document states two
balances: **−20,098.69 EUR at end of 2025-06-30** and **+33,266.35 EUR at end of 2025-12-31**.

Rolling the transactions backwards from either documented balance (with CHF converted at the rate
printed in each transfer) yields the **same** opening balance: **2,500.00 EUR at end of 2024-12-31**.
Because two independent documented facts reconcile to the cent, this opening balance is stored as a
*derived* anchor (`is_derived = true`). It is a value the business itself knew on 2025-01-01, so using
it for any 2025 analysis date is not future-data leakage.

Rule: `balance(D) = A.balance + Σ amount_eur(t) for A.anchor_date < t.booking_date <= D`, where `A` is the
**latest anchor with `anchor_date <= D`**. When more than one anchor is `<= D`, the engine re-checks that
consecutive anchors reconcile and raises `BALANCE_RECONCILIATION_MISMATCH` otherwise.

If no anchor exists, the balance is the plain sum of transactions (relative balance, i.e. opening
balance assumed 0) and the critical flag `BALANCE_ANCHOR_MISSING` is raised.

### 0.3 Currency treatment

* EUR transactions are taken as-is.
* Foreign-currency rows (12 CHF transfers to the Swiss silent partnership) store the **CHF** amount in
  `amount`. The applied rate is embedded in `payment_reference`, e.g. `(4166.67 CHF @ 1.042 EUR/CHF)`.
  The engine parses `@ <rate> EUR/<CCY>` and interprets it as **EUR per 1 unit of CCY**
  (4,166.67 × 1.042 = 4,341.67 EUR, consistent with the documented €4,325–4,375 monthly range and
  with both balance anchors).
* `amount_eur = round_half_up(amount × rate, 2)`.
* Fallback order when a rate is missing: (1) latest rate observed for that currency on/before the
  transaction date → flag `FX_RATE_FALLBACK`; (2) reference estimate from the obligation schedule
  (`estimated_eur_per_payment / amount_per_payment`) → flag `FX_RATE_FALLBACK`; (3) none → the transaction
  is **excluded** from all sums and `FX_RATE_MISSING` (critical) is raised.
* Future CHF obligations are converted at the latest rate observed on/before `as_of_date`.

### 0.4 Transaction classification

Classification is by `transaction_type` (deterministic, auditable):

| Class | `transaction_type` | Role |
|---|---|---|
| `OPERATING_INFLOW` | `CARD SETTLEMENT (DEBIT)`, `CREDIT CARD PAYOUT` | Sales receipts → inflow run-rate |
| `FIXED_OUTFLOW` | `SALARY PAYMENT`, `RENT PAYMENT`, `TAX OFFICE TRANSFER`, `DIRECT DEBIT`, `INTERNATIONAL WIRE TRANSFER` | Covered by the obligation schedule; **excluded** from the variable run-rate to avoid double counting |
| `VARIABLE_OUTFLOW` | `SUPPLIER PAYMENT`, `GIRO CARD PAYMENT` | Purchases → variable outflow run-rate |
| `OTHER_INFLOW` | any other type with amount > 0 | Counted in balance and historical net flow; **not** projected (non-recurring by assumption) |
| `OTHER_OUTFLOW` | any other type with amount < 0 | Counted everywhere; projected as variable (conservative) |

Unknown types raise `UNCLASSIFIED_TRANSACTIONS`.

### 0.5 Run-rates

* **Forecast run-rate** (`run_rate_lookback_days = 28`, four full weeks so weekday patterns cancel):
  `I = Σ operating inflows / N`, `V = Σ (variable + other) outflows / N`, over the window
  `[as_of − 27, as_of]`, with `N` = number of calendar days of that window that lie inside the available
  history (closed days count as zero). Quantized to cents.
* **Sensitivity run-rate** (`alt_run_rate_lookback_days = 90`): same formula over 90 days, used only for
  the sensitivity forecast and for the buffer's variable reserve (more stable through short disruptions).
* If `N` < window length ⇒ `INSUFFICIENT_HISTORY`.
* If there were no operating inflows in the last 7 days ⇒ `REVENUE_INTERRUPTION` (the base forecast then
  assumes the interruption continues — worst case; the sensitivity forecast shows the 90-day view).

### 0.6 Scheduled fixed obligations

For each row of `fixed_obligations` the engine generates payment dates on `payment_day` of every month
in `payment_months` (`all` or e.g. `03;06;09;12`; day clamped to month end), starting at `valid_from`.

* **Rollover:** dates after `valid_to` are still projected (flag `OBLIGATION_EXTRAPOLATED`, estimate)
  for open-ended obligations, because `valid_to = 2025-12-31` marks the end of the documented accounting
  period, not the end of the rent contract or employment.
* **Finite commitments** (`total_commitment` not null, e.g. CHF 50,000 investment): already-paid amount =
  Σ |amount| of outflows in the obligation currency whose reference/payee contains `match_keyword`, up to
  `as_of_date`. Remaining = commitment − paid. Future tranches are capped at the remaining amount and stop
  at zero (`COMMITMENT_COMPLETED`).
* **Monthly fixed-obligation run-rate** `M` (used by KPI 3 and KPI 8): for every obligation still active
  after `as_of_date` (not a completed commitment, `valid_from` within the horizon),
  `amount_eur_per_payment × payments_per_year / 12` (rent 3,800 → 3,800; quarterly income tax 1,150 → 383.33;
  a commitment's per-payment amount is capped at its remaining balance). Unlike a "next 30 days" window this
  never drops a payment that falls on day 31 (e.g. the payroll of the 28th seen from Dec 28).
* If the table is missing or empty ⇒ `OBLIGATION_SCHEDULE_MISSING`; fixed-type outflows are then
  projected as part of the daily run-rate (timing is lost, totals are preserved) and `M` = fixed-type
  outflows booked in the last 30 days.

---

## 1. KPI catalogue

Status vocabulary (per `SPEC.md`): `HEALTHY` | `WARNING` | `CRITICAL`.
Notation: `B₀` current balance; `B_d` forecast end-of-day balance on day `d` (`d = 1…30`); `F_d` scheduled
obligations on day `d`; `I`, `V` daily inflow / variable outflow run-rates; `M` monthly fixed-obligation
run-rate (§0.6); `MCB` minimum cash buffer.

### KPI 1 — Current Cash Balance

| | |
|---|---|
| **ID** | `current_cash_balance_eur` (EUR) |
| **Definition** | Cash on the business account at end of `as_of_date`. Starting point for every other KPI. |
| **Data** | `bank_transactions` (≤ as_of), `account_balance_anchors` (≤ as_of), FX rates |
| **Formula** | `B₀ = anchor.balance + Σ amount_eur(t)` for `anchor_date < booking_date ≤ as_of` (§0.2) |
| **Missing data** | No anchor → relative balance + `BALANCE_ANCHOR_MISSING` (critical). No transactions → anchor balance only + `NO_TRANSACTIONS`. Last transaction > 3 days before as_of → `STALE_DATA`. |
| **Status** | `B₀ < 0` → CRITICAL (overdraft) · `B₀ < MCB` → WARNING · else HEALTHY |
| **Estimate?** | No (fact), unless the anchor is derived (flag `BALANCE_ANCHOR_DERIVED`, info) |

### KPI 2 — Forecasted Cash Balance (7 / 14 / 30 days)

| | |
|---|---|
| **IDs** | `forecast_balance_7d_eur`, `forecast_balance_14d_eur`, `forecast_balance_30d_eur`, `forecast_min_balance_30d_eur` (EUR) |
| **Definition** | Expected end-of-day balance path for the next 30 days, so that temporary dips inside the horizon (e.g. on payroll day) are visible, not just the end value. |
| **Data** | `B₀`, run-rates (§0.5), obligation schedule (§0.6), FX |
| **Formula** | `B_d = B_{d−1} + I − V − F_d`, `d = 1…30`. For each horizon `h ∈ {7,14,30}`: end balance `B_h`; minimum `min(B₀…B_h)` and its date. |
| **Also reported** | `forecast.daily[]` (31 points: date, inflow, variable outflow, scheduled outflow, balance, below_buffer, below_zero, scheduled items), `forecast.sensitivity` (same model with 90-day run-rates). |
| **Assumptions** | Receipts and purchases continue at the recent average pace and are spread evenly over calendar days; no new loans, investments or one-off inflows; scheduled obligations paid on their due day. |
| **Limitations** | Does not model seasonality beyond the 28-day window, planned closures or price changes. Card payouts are lumpy (twice weekly) but modelled as daily averages. |
| **Status** | `min₃₀ < 0` → CRITICAL · `min₃₀ < MCB` → WARNING · else HEALTHY |
| **Estimate?** | Yes |

### KPI 3 — Minimum Cash Buffer

| | |
|---|---|
| **ID** | `minimum_cash_buffer_eur` (EUR) |
| **Definition** | Cash the business should keep at all times as a shock reserve: enough to pay one month of fixed obligations even if sales stop (as in the June 2025 closure), plus one week of purchases already ordered. |
| **Formula** | `MCB = M + 7 × V₉₀` where `M` is the monthly fixed-obligation run-rate (§0.6) and `V₉₀` the 90-day average daily variable outflow. |
| **Rationale** | June 2025 showed that revenue can fall to zero while fixed costs continue; variable purchases fall with revenue, so only a short (7-day) reserve of them is kept. `M` is forward-looking (completed commitments such as the CHF investment drop out) but, unlike a "next 30 days" window, it is not distorted when a monthly payment lands on day 31 of the window. A first version used the 30-day window and the buffer fell by 8,600 EUR on 2025-12-28 (Jan 28 payroll just outside); this was rejected. |
| **Details** | `monthly_fixed_obligations_breakdown`, `scheduled_obligations_next_30d_eur` (informational), `variable_reserve_eur`, `buffer_funding_ratio`. |
| **Missing data** | No schedule → `M` = fixed-type outflows of the last 30 days (`OBLIGATION_SCHEDULE_MISSING`). |
| **Status** | Funding ratio `B₀ / MCB`: `≥ 1` HEALTHY · `≥ 0.5` WARNING · else CRITICAL |
| **Estimate?** | Yes (policy threshold built from schedule + averages) |

### KPI 4 — Liquidity Gap

| | |
|---|---|
| **IDs** | `liquidity_gap_30d_eur` (primary), `projected_overdraft_30d_eur` (EUR, both ≥ 0) |
| **Definition** | How much cash is missing (at the worst forecast point) to keep the minimum buffer, and how deep the account would go into overdraft. |
| **Formula** | `gap_h = max(0, MCB − min(B₀…B_h))`; `overdraft_h = max(0, −min(B₀…B_h))`. Details per horizon 7/14/30, `first_breach_date` (first `d` with `B_d < MCB`), `days_until_breach`, `first_overdraft_date`, `peak_shortfall_date`. |
| **Financing interpretation** | `financing_required = overdraft₃₀ > 0`. A credit line sized at `gap₃₀` restores the full buffer at the worst point of the horizon. |
| **Status** | `gap₃₀ = 0` HEALTHY · `gap₃₀ > 0` and `overdraft₃₀ = 0` WARNING · `overdraft₃₀ > 0` CRITICAL |
| **Estimate?** | Yes |

### KPI 5 — Deployable Cash Surplus

| | |
|---|---|
| **ID** | `deployable_cash_surplus_eur` (EUR, ≥ 0) |
| **Definition** | Cash that can be moved to an interest-bearing product today without the forecast balance ever dropping below the minimum buffer during the next 30 days. |
| **Formula** | `surplus = max(0, min(B₀…B₃₀) − MCB)`. Because the minimum includes `B₀`, the surplus never exceeds today's cash above the buffer; because it includes `B₁…B₃₀`, it accounts for all known obligations (payroll, rent, taxes, tranches) in the horizon. |
| **Details** | `binding_date` (date of the forecast minimum), `sensitivity_surplus_eur` (same formula on the 90-day sensitivity forecast), `horizon_days = 30`. |
| **Limitations** | Horizon is 30 days. For instruments longer than 30 days (e.g. a 90-day deposit) the agent should use the lower of `deployable_cash_surplus_eur` and `sensitivity_surplus_eur` and/or keep part of the amount liquid. |
| **Status** | `surplus > 0` HEALTHY · `surplus = 0` and `gap₃₀ = 0` HEALTHY · `surplus = 0` and `gap₃₀ > 0` WARNING |
| **Estimate?** | Yes |

### KPI 6 — Net Cash Flow (7 / 30 days)

| | |
|---|---|
| **IDs** | `net_cash_flow_7d_eur`, `net_cash_flow_30d_eur` (EUR) |
| **Definition** | Historical cash generated (positive) or consumed (negative) in the trailing window. |
| **Formula** | Window `[as_of − (n−1), as_of]`, `n ∈ {7, 30}`: `net = Σ inflows_eur + Σ outflows_eur` (outflows negative). Details: inflows, outflows, breakdown by class. |
| **Limitations** | Calendar windows are lumpy: a 30-day window may contain 0 or 2 rent payments around 31-day months. Use KPI 7 for direction. |
| **Status** | 30d: `net₃₀ ≥ 0` HEALTHY · burn ratio `−net₃₀ / outflows₃₀ ≤ 10 %` WARNING · `> 10 %` CRITICAL. 7d: WARNING only if `net₇ < 0` **and** `net₃₀ < 0`, else HEALTHY (a payroll week inside a cash-generating month is normal; the 7-day KPI never escalates to CRITICAL on its own). |
| **Estimate?** | No |

### KPI 7 — Cash Inflow / Outflow Trend

| | |
|---|---|
| **IDs** | `cash_inflow_trend_pct`, `cash_outflow_trend_pct` (%) |
| **Definition** | Change in operating inflows and total outflows of the last 30 days vs the preceding 30 days. |
| **Formula** | `trend = (cur − prev) / prev × 100`, `cur = [as_of−29, as_of]`, `prev = [as_of−59, as_of−30]`. Details: variable-outflow trend, net flow both windows, direction label (`RISING` ≥ +5 %, `DECLINING` ≤ −5 %, else `STABLE`), per-class changes. |
| **Missing data** | `prev = 0` → value `null` + `TREND_BASELINE_ZERO`; previous window partly before history → `INSUFFICIENT_HISTORY`. |
| **Status** | inflow trend `≤ −20 %` CRITICAL · `≤ −10 %` WARNING · outflow trend − inflow trend `≥ 15 pp` WARNING · else HEALTHY |
| **Estimate?** | No |

### KPI 8 — Fixed Cost Coverage Ratio

| | |
|---|---|
| **ID** | `fixed_cost_coverage_ratio` (ratio, "months of fixed costs in the bank") |
| **Definition** | How many months of fixed obligations the current cash covers. |
| **Formula** | `coverage = B₀ / M`. Negative when the account is overdrawn. Details: `operating_coverage_ratio = (operating inflows₃₀ − variable outflows₃₀) / M` — whether the business *earns* its fixed costs from operations (≥ 1) or eats into reserves (< 1). |
| **Missing data** | `M = 0` → `null` with `NO_FIXED_OBLIGATIONS`. |
| **Status** | `≥ 1.5` HEALTHY · `≥ 1.0` WARNING · `< 1.0` CRITICAL |
| **Estimate?** | Yes (denominator is scheduled) |

---

## 2. Scenario signals (input for the agent, not decisions)

`metrics.signals` summarises the two Autonomous CFO scenarios. The agent remains responsible for the
recommendation; the engine never recommends.

| Signal | Rule |
|---|---|
| `liquidity_shortage.detected` | `liquidity_gap_30d_eur > 0`; `severity` = KPI 4 status; includes `first_breach_date`, `days_until_breach`, `financing_required`, `suggested_min_financing_eur = liquidity_gap_30d_eur`. |
| `excess_liquidity.detected` | no shortage **and** `deployable_cash_surplus_eur ≥ excess_liquidity_min_eur` (default **10,000 EUR**, from SPEC §5.2). |
| `scenario_hint` | `LIQUIDITY_SHORTAGE` if shortage detected, else `EXCESS_LIQUIDITY` if excess detected, else `OPTIMAL`. |

---

## 3. Output contract — `kpi_snapshots.metrics` (JSONB)

One row per `snapshot_date` (= `as_of_date`). Re-running a date **updates** the row in place
(`INSERT … ON CONFLICT (snapshot_date) DO UPDATE`), keeping its `id` so `agent_proposals.snapshot_id`
references stay valid.

```jsonc
{
  "schema_version": "1.0",
  "as_of_date": "2025-12-31",
  "currency": "EUR",
  "overall_status": "HEALTHY",                // worst status of all KPIs
  "kpis": {                                   // flat map, numbers (or null)
    "current_cash_balance_eur": 33266.35,
    "forecast_balance_7d_eur": 0.0, "forecast_balance_14d_eur": 0.0, "forecast_balance_30d_eur": 0.0,
    "forecast_min_balance_30d_eur": 0.0,
    "minimum_cash_buffer_eur": 0.0,
    "liquidity_gap_30d_eur": 0.0, "projected_overdraft_30d_eur": 0.0,
    "deployable_cash_surplus_eur": 0.0,
    "net_cash_flow_7d_eur": 0.0, "net_cash_flow_30d_eur": 0.0,
    "cash_inflow_trend_pct": 0.0, "cash_outflow_trend_pct": 0.0,
    "fixed_cost_coverage_ratio": 0.0
  },
  "kpi_metadata": [                           // one entry per key of "kpis", same order
    { "id": "current_cash_balance_eur", "kpi_number": 1, "kpi_group": "current_cash_balance",
      "label": "Saldo bieżące (EUR)", "label_en": "Current cash balance",
      "value": 33266.35, "unit": "EUR",       // "EUR" | "%" | "ratio"
      "status": "HEALTHY", "is_estimate": false,
      "description": "…", "details": { … } }
  ],
  "forecast":     { "method": "…", "horizon_days": 30, "run_rates": {…}, "scheduled_obligations": […],
                    "daily": [ {"day": 0, "date": "…", "balance_eur": …, …} ], "sensitivity": {…} },
  "signals":      { "scenario_hint": "OPTIMAL", "liquidity_shortage": {…}, "excess_liquidity": {…} },
  "data_quality": { "status": "OK", "flags": [ {"code": "…", "severity": "INFO|WARNING|CRITICAL",
                    "message": "…", "affected_kpis": […]} ], "coverage": {…}, "balance_source": {…}, "fx": {…} },
  "engine":       { "name": "amalfi-kpi-engine", "version": "1.0.0", "computed_at": "…", "parameters": {…} }
}
```

The keys `as_of_date`, `currency`, `kpis`, `kpi_metadata` and the meta fields `id/label/value/unit/status`
follow `SPEC.md` §4; everything else is an additive extension. `data_quality.status` is `OK`, `DEGRADED`
(warnings) or `UNRELIABLE` (any critical flag) — agents should not recommend financial actions on
`UNRELIABLE` snapshots without saying so.

---

## 4. Parameters (defaults in `kpi_engine/config.py`)

| Parameter | Default | Meaning |
|---|---:|---|
| `forecast_horizons` | 7, 14, 30 | Forecast horizons (days) |
| `run_rate_lookback_days` | 28 | Base forecast run-rate window |
| `alt_run_rate_lookback_days` | 90 | Sensitivity run-rate / buffer variable reserve window |
| `buffer_fixed_horizon_days` | 30 | Window for the informational "scheduled next N days" figure and the no-schedule fallback of `M` |
| `buffer_variable_reserve_days` | 7 | Days of variable outflows in the buffer |
| `trend_window_days` | 30 | KPI 7 comparison window |
| `trend_inflow_warning_pct` / `critical` | −10 / −20 | KPI 7 thresholds |
| `trend_divergence_warning_pp` | 15 | Outflow growth − inflow growth threshold |
| `trend_direction_threshold_pct` | 5 | STABLE band |
| `burn_ratio_critical_pct` | 10 | KPI 6 threshold |
| `coverage_healthy` / `coverage_warning` | 1.5 / 1.0 | KPI 8 thresholds |
| `buffer_funding_warning` | 0.5 | KPI 3 threshold |
| `stale_data_days` | 3 | Flag when the last transaction is older |
| `revenue_interruption_days` | 7 | Flag when no operating inflows for this long |
| `excess_liquidity_min_eur` | 10,000 | Minimum surplus for the excess-liquidity signal |

Parameters are written into `metrics.engine.parameters` so every snapshot is reproducible.

---

## 5. Validation on 2025 data (demo dates from SPEC.md)

Produced by `python -m kpi_engine --as-of-date …` against the `amalfi` database (identical to the
CSV-based test computation, see `tests/test_integration_db.py`). Nothing is hard-coded; these are outputs.

| KPI | 2025-06-30 | 2025-12-31 |
|---|---:|---:|
| 1 `current_cash_balance_eur` | **−20,098.69** (= documented) | **33,266.35** (= documented) |
| 2 `forecast_balance_7d / 14d / 30d_eur` | −23,898.69 / −25,428.69 / −41,053.69 | 36,217.57 / 41,438.79 / 45,620.15 |
| 2 `forecast_min_balance_30d_eur` | −41,053.69 (2025-07-30) | 30,430.81 (2026-01-01, rent day) |
| 3 `minimum_cash_buffer_eur` | 24,957.71 (M 21,413.33 + 7 × 506.34) | 22,369.46 (M 17,038.33 + 7 × 761.59) |
| 4 `liquidity_gap_30d_eur` / overdraft | **66,011.40** / 41,053.69 | 0.00 / 0.00 |
| 5 `deployable_cash_surplus_eur` | 0.00 | **8,061.35** (sensitivity 8,090.86) |
| 6 `net_cash_flow_7d / 30d_eur` | −11,250.00 / −22,105.00 | −4,603.34 / 9,386.83 |
| 7 `cash_inflow / outflow_trend_pct` | −100.0 / −46.4 | −6.5 / −6.9 |
| 8 `fixed_cost_coverage_ratio` | −0.94 | 1.95 |
| `overall_status` / `scenario_hint` | CRITICAL / **LIQUIDITY_SHORTAGE** | HEALTHY / **OPTIMAL** |
| data quality | DEGRADED (`REVENUE_INTERRUPTION`) | OK (info: obligations extrapolated into 2026, CHF commitment completed) |

### 5.1 Scenario A (2025-06-30) — emerges naturally

The account is already overdrawn and no sales were received for the whole of June. The base forecast
(28-day run-rate = closure continues) projects −41,053.69 EUR on 2025-07-30; the gap to the buffer is
66,011.40 EUR. The 90-day sensitivity view (pre-closure sales pattern) still shows −27,852.81 EUR and a
52,810.52 EUR gap — **financing is required under both views**. The agent should present the range,
since the base case assumes the restaurant stays closed (the knowledge doc mentions a three-week closure,
but the statement shows no receipts for all of June, so the engine does not assume a reopening date).

### 5.2 Scenario B (2025-12-31) — surplus exists, but below the SPEC §5.2 trigger

Year-end cash (33,266.35 EUR) covers **1.95 months** of fixed obligations. After keeping one month of
fixed obligations plus one week of purchases (22,369.46 EUR) at the lowest point of the next 30 days
(1 Jan rent), **8,061.35 EUR** can be deployed safely. This is below the illustrative 10,000 EUR trigger in
SPEC §5.2, so `signals.excess_liquidity.detected = false` and the hint is `OPTIMAL`.

A daily replay of 2025 shows the business under its buffer every day from January to early December
(thin 2,500 EUR opening balance, near-zero monthly net flow in H1, June closure). The largest surplus of
the year is 9,443.26 EUR on 2025-12-29. The SPEC example figures (82,945.55 EUR balance, 140 days runway)
are illustrative and do not come from the statement. Whether the agent proposes a deposit for a ~8k
surplus (e.g. by lowering its trigger) is a product decision for the agent layer — the engine reports
the number and does **not** bend the methodology to reach a threshold.

### 5.3 Reproducibility checks (automated)

* Both documented balances are reproduced exactly from the opening anchor alone; anchor reconciliation difference 0.00.
* Daily replay of all 365 days: `B₀(d) − B₀(d−1)` equals the EUR-converted net of day `d`.
* Results are unchanged when future transactions, future FX rates or future anchors are injected.
