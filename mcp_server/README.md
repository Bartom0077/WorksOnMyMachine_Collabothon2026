# Amalfi data MCP server (`mcp_server`)

Read-only [MCP](https://modelcontextprotocol.io) server that gives the CFO agent (OGX on OpenShift AI)
access to KPI snapshots and bank data in the `amalfi` PostgreSQL database.

```
app.py      FastMCP wiring: tool signatures/descriptions, X-As-Of-Date header, /health
tools.py    tool logic (pure functions, whitelisted parameterised SQL)
db.py       read-only connection, as-of cutoff, JSON serialisation
Dockerfile  image build (context = repo root, bundles backend/kpi_engine)
tests/      unit tests (no DB)
```

Transport: streamable HTTP, endpoint **`/mcp`** (stateless, JSON responses). Health: `GET /health`.
In-cluster URL: `http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp`

## Tools

| Tool | Purpose |
|---|---|
| `get_data_overview()` | analysis date, data ranges, transaction types + cash-flow class, snapshot dates |
| `get_kpi_snapshot(date?, include_forecast?, include_kpi_details?)` | authoritative KPIs + status, signals, data quality (main input) |
| `get_balance_forecast(days, date?, include_daily?)` | balance forecast N days (1–365) ahead, computed on request by `kpi_engine`; **not stored** |
| `get_kpi_history(date_from, date_to?, kpi_names?, step_days?)` | KPI time series from daily snapshots |
| `get_cashflow_summary(date_from, date_to?, group_by?, transaction_type?, counterparty?)` | inflow/outflow/net by day/week/month/type/counterparty/cash-flow class |
| `get_transactions(date_from, date_to?, transaction_type?, counterparty?, direction?, min_abs_amount?, sort?, limit?)` | individual transactions (max 200) + totals of all matches |
| `get_fixed_obligations(active_on?, category?)` | documented recurring payments |

KPIs are never recomputed here (SPEC.md §5.1): they come from `kpi_snapshots`. The only computation is
`get_balance_forecast`, which calls `kpi_engine.run_forecast` (the same model as the 7/14/30-day KPIs, data
booked on/before the start date only) over the read-only connection and returns the result without writing it.
The start date defaults to and is capped at the as-of cutoff. Transaction sums are in the
original currency (CHF rows are not converted).

## No future data: the as-of cutoff

The agent analyses a specific day (e.g. 2025-06-30), so it must not see later data. Every tool caps its
dates at the cutoff, which is the earlier of:

* HTTP header **`X-As-Of-Date: YYYY-MM-DD`**, set per agent run by the orchestrator;
* env **`AMALFI_AS_OF_DATE`**, an optional server-wide cap.

With neither set, all data is visible. Example OGX / OpenAI Responses call:

```python
client.responses.create(
    model="vllm-cfo/cfo-llm", instructions=SYSTEM_PROMPT, input="Analyse the situation as of 2025-06-30.",
    tools=[{"type": "mcp", "server_label": "amalfi",
            "server_url": "http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp",
            "headers": {"X-As-Of-Date": "2025-06-30"}, "require_approval": "never"}],
)
```

## Security

* DB role `amalfi_mcp`: `SELECT` only on `bank_transactions`, `kpi_snapshots`, `fixed_obligations`,
  `account_balance_anchors` (no access to `agent_proposals`). Created by `db/setup_mcp_readonly_user.sh`,
  credentials in the OpenShift secret `amalfi-mcp-db` (never in git).
* Each session is also forced to `default_transaction_read_only=on` with a 10 s statement timeout.
* The LLM never sends SQL: only fixed queries with whitelisted options and bounded result sizes.
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
bash db/setup_mcp_readonly_user.sh       # once: DB role + secret amalfi-mcp-db
oc apply -f deploy/mcp-server.yaml       # ImageStream, BuildConfig, Deployment, Service
```

After merge to `main` the CI poller (`deploy/ci-poller.yaml`) rebuilds `amalfi-mcp` whenever `mcp_server/`
changes. To deploy unmerged code, start a binary build from a directory that contains `mcp_server/`
(the BuildConfig uses the repo root as context and `mcp_server/Dockerfile`, which also copies
`backend/kpi_engine`; the CI poller rebuilds on changes to either directory):

```bash
mkdir -p /tmp/mcp-build/backend && cp -r mcp_server /tmp/mcp-build/ && cp -r backend/kpi_engine /tmp/mcp-build/backend/
oc start-build amalfi-mcp -n autonomous-cfo --from-dir=/tmp/mcp-build --follow
```
