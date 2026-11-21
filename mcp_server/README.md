# Amalfi data MCP server (`mcp_server`)

[MCP](https://modelcontextprotocol.io) server that gives the CFO agent (OGX on OpenShift AI) read access to
KPI snapshots and bank data in the `amalfi` PostgreSQL database, plus one constrained write: saving a
planned event that the owner confirmed in the chat.

```
app.py             FastMCP wiring: tool signatures/descriptions, X-As-Of-Date header, /health
tools.py           read tools (pure functions, whitelisted parameterised SQL)
planned_events.py  planned-event validation, the single INSERT, listing
db.py              read-only connection, the one read-write connection, as-of cutoff, JSON serialisation
tests/             unit tests (no DB)
```

Transport: streamable HTTP, endpoint **`/mcp`** (stateless, JSON responses). Health: `GET /health`.
In-cluster URL: `http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp`

## Tools

| Tool | Purpose |
|---|---|
| `get_data_overview()` | analysis date, data ranges, transaction types + cash-flow class, snapshot dates |
| `get_kpi_snapshot(date?, include_forecast?, include_kpi_details?)` | authoritative KPIs + status, signals, data quality (main input) |
| `get_kpi_history(date_from, date_to?, kpi_names?, step_days?)` | KPI time series from daily snapshots |
| `get_cashflow_summary(date_from, date_to?, group_by?, transaction_type?, counterparty?)` | inflow/outflow/net by day/week/month/type/counterparty/cash-flow class |
| `get_transactions(date_from, date_to?, transaction_type?, counterparty?, direction?, min_abs_amount?, sort?, limit?)` | individual transactions (max 200) + totals of all matches |
| `get_fixed_obligations(active_on?, category?)` | documented recurring payments |
| `list_planned_events()` | saved planned events that are ongoing or upcoming on the analysis date |
| `propose_planned_event(event_type, date_from, description, date_to?, amount_eur?)` | validates a plan from the chat, **writes nothing**; returns the proposal and a confirmation question |
| `save_planned_event(event_type, date_from, description, original_utterance, date_to?, amount_eur?)` | **the only write**: validates and inserts one row into `planned_events` |

KPIs are never recomputed here (ROZWIAZANIE.md section 11): they come from `kpi_snapshots`. Transaction sums are in the
original currency (CHF rows are not converted).

## Planned events (the write side)

The owner describes a plan in the chat ("we close for holidays June 1-21", "in July I buy an oven for 8k").
The assistant calls `propose_planned_event`, asks the owner to confirm, and only after an explicit yes calls
`save_planned_event` (confirmation is enforced by the system prompt, see ROZWIAZANIE.md section 20).
The forecast engine in the backend applies the saved events; this server never computes their effect.

Rules, checked by both tools. A violation is returned as a normal result with the reason
(`{"valid": false, "error": "..."}` / `{"saved": false, "error": "..."}`) rather than as an MCP tool error,
because OGX reduces tool errors to `Error (code 1): None` in its `mcp_call` output items:

* `event_type` is `closure`, `expense` or `inflow` only (`payment_deferral` and `credit_line` are created
  by the backend from approved rescue options, never from the chat);
* the `X-As-Of-Date` header is required: it is the simulated "today" and is stored as `as_of_date`;
* `date_from` is today or later and at most 366 days ahead; `date_to` defaults to `date_from`;
* `closure`: no amount, at most 120 days; `expense` / `inflow`: `amount_eur` > 0 (max 10,000,000) and a
  single day;
* `description` (max 200 characters) and, for saving, `original_utterance` (max 1000) are required.

Results:

```jsonc
// propose_planned_event
{"valid": true,
 "proposal": {"event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21",
              "amount_eur": null, "description": "Summer holiday closure"},
 "confirmation_prompt": "I understood: closure from 2025-06-01 to 2025-06-21 (21 days) - Summer holiday closure. Should I include it in the forecast?"}

// save_planned_event
{"saved": true,
 "event": {"id": 12, "event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21",
           "amount_eur": null, "category": null, "description": "Summer holiday closure",
           "original_utterance": "we close for holidays June 1-21", "source": "chat",
           "as_of_date": "2025-05-10", "decision_id": null, "created_at": "2026-01-01T10:00:00+00:00"}}

// save_planned_event called again with an identical event (e.g. a model retry): nothing is inserted
{"saved": false, "already_saved": true, "event": {...}, "note": "This event is already in the plans; nothing was added."}

// rejected input (nothing written)
{"valid": false, "error": "date_from 2025-04-01 is before today (2025-05-10). Planned events must be today or later; check the year and ask the owner if unsure."}
{"saved": false, "error": "amount_eur is required for an expense (EUR, greater than 0). Ask the owner for the amount instead of guessing."}

// list_planned_events
{"analysis_date": "2025-05-10",
 "events": [{"id": 12, "event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21",
             "amount_eur": null, "category": null, "description": "Summer holiday closure", "source": "chat"}]}
```

## No future data: the as-of cutoff

The agent analyses a specific day (e.g. 2025-06-30), so it must not see later data. Every read tool caps
its dates at the cutoff, which is the earlier of:

* HTTP header **`X-As-Of-Date: YYYY-MM-DD`**, set per agent run by the orchestrator;
* env **`AMALFI_AS_OF_DATE`**, an optional server-wide cap.

With neither set, all data is visible to the read tools; the planned-event tools refuse to run without
the header. Example OGX / OpenAI Responses call:

```python
client.responses.create(
    model="vllm-cfo/cfo-llm", instructions=SYSTEM_PROMPT, input="Analyse the situation as of 2025-06-30.",
    tools=[{"type": "mcp", "server_label": "amalfi",
            "server_url": "http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp",
            "headers": {"X-As-Of-Date": "2025-06-30"}, "require_approval": "never"}],
)
```

## Security

Read tools plus one constrained write tool:

* DB role `amalfi_mcp` (created by `db/setup_mcp_user.sh`, credentials in the OpenShift secret
  `amalfi-mcp-db`, never in git):
  * `SELECT` on `bank_transactions`, `kpi_snapshots`, `fixed_obligations`, `account_balance_anchors`,
    `planned_events`, `owner_decisions`;
  * `INSERT` on `planned_events`, limited to the columns of a chat event (not `id`, `category`,
    `decision_id`, `created_at`), and `USAGE` on its id sequence;
  * nothing else: no `UPDATE` / `DELETE` anywhere, no access to `agent_proposals`. Transactions, KPIs and
    owner decisions are read-only for this server.
* The role and every session default to `default_transaction_read_only=on` with a 10 s statement timeout.
  All read tools use that read-only connection. Only `save_planned_event` opens a separate connection with a
  single explicit `READ WRITE` transaction (`db.connect_write`), committed on success, rolled back on error.
* Event type is a closed enum in the tool schema and is validated again in code; dates and amounts are
  validated before any SQL runs; `source` is always written as `chat`.
* The LLM never sends SQL: only fixed, parameterised queries with whitelisted options and bounded result
  sizes.
* No Route: the server is reachable only inside the cluster.

## Run locally

```bash
oc port-forward -n autonomous-cfo svc/amalfi-db 15432:5432 &
cd mcp_server && pip install -r requirements.txt
PGHOST=localhost PGPORT=15432 PGDATABASE=amalfi PGUSER=amalfi_mcp PGPASSWORD=... MCP_PORT=18080 python app.py
npx @modelcontextprotocol/inspector     # connect to http://localhost:18080/mcp (streamable HTTP)
python -m pytest -q                     # unit tests
```

## Deploy (OpenShift)

```bash
bash db/setup_mcp_user.sh                # DB role + grants + secret amalfi-mcp-db (idempotent)
oc apply -f deploy/mcp-server.yaml       # ImageStream, BuildConfig, Deployment, Service
```

After merge to `main` the CI poller (`deploy/ci-poller.yaml`) rebuilds `amalfi-mcp` whenever `mcp_server/`
changes. To deploy unmerged code, start a binary build from a directory that contains `mcp_server/`
(the BuildConfig uses `contextDir: mcp_server`):

```bash
mkdir -p /tmp/mcp-build && cp -r mcp_server /tmp/mcp-build/
oc start-build amalfi-mcp -n autonomous-cfo --from-dir=/tmp/mcp-build --follow
```
