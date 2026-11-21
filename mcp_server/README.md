# Amalfi data MCP server (`mcp_server`)

[MCP](https://modelcontextprotocol.io) server that gives the CFO agent (OGX on OpenShift AI) read access to
bank data and KPI snapshots in the `amalfi` PostgreSQL database, plus two constrained writes: saving a
planned event that the owner confirmed in the chat, and removing such a chat plan again after the owner
confirmed the removal.

The server never computes a balance or a forecast. The forecast, risk status and rescue options the owner
sees are computed by the backend (`backend/forecast.py`, `risk.py`, `options.py`) and handed to the model
in the prompt; that is the only source of truth for those numbers.

```
app.py             FastMCP wiring: tool signatures/descriptions, X-As-Of-Date and X-Removable-Event-Ids headers, /health
tools.py           read tools (pure functions, whitelisted parameterised SQL)
planned_events.py  planned-event validation, the single INSERT, the single DELETE, listing
db.py              read-only connection, the read-write connection of the two write tools, as-of cutoff, JSON serialisation
tests/             unit tests (no DB); test_new_event_types.py adds optional database tests
```

Transport: streamable HTTP, endpoint **`/mcp`** (stateless, JSON responses). Health: `GET /health`.
In-cluster URL: `http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp`

## Tools

| Tool | Purpose | Offered to the model by the product |
|---|---|---|
| `list_planned_events()` | saved planned events that end after the analysis date (still affect the forecast); a term deposit or an advance CHF purchase of the CFO advisors carries a plain-English `summary` | yes |
| `propose_planned_event(event_type, date_from, description, date_to?, amount_eur?)` | validates a plan from the chat, **writes nothing**; returns the proposal and a confirmation question | yes |
| `save_planned_event(event_type, date_from, description, original_utterance, date_to?, amount_eur?)` | **write**: validates and inserts one row into `planned_events` | yes, only while a proposal the owner has seen is waiting |
| `propose_planned_event_removal(event_id)` | looks up a saved chat plan the owner wants to remove, **removes nothing**; returns the plan and a confirmation question. Refuses unknown ids and plans created by an approved option | yes, only in a turn about removing a plan |
| `remove_planned_event(event_id)` | **write**: deletes one `source = 'chat'` row, and only if its id is listed in the `X-Removable-Event-Ids` request header | yes, only in the turn directly after the removal card was shown |
| `get_fixed_obligations(active_on?, category?)` | documented recurring payments | yes |
| `get_data_overview()` | analysis date, data ranges, transaction types + cash-flow class, snapshot dates | no (analyst tool) |
| `get_kpi_snapshot(date?, include_forecast?, include_kpi_details?)` | KPI engine snapshot: KPIs + status, signals, data quality | no (analyst tool) |
| `get_kpi_history(date_from, date_to?, kpi_names?, step_days?)` | KPI time series from daily snapshots | no (analyst tool) |
| `get_cashflow_summary(date_from, date_to?, group_by?, transaction_type?, counterparty?)` | inflow/outflow/net by day/week/month/type/counterparty/cash-flow class | no (analyst tool) |
| `get_transactions(date_from, date_to?, transaction_type?, counterparty?, direction?, min_abs_amount?, sort?, limit?)` | individual transactions (max 200) + totals of all matches | no (analyst tool) |

### Which tools the product uses

The backend passes an `allowed_tools` list with every chat call (`backend/llm_chat.py`): the planned-event
tools and `get_fixed_obligations`. The two tools that write are offered per turn, only when a proposal the
owner has seen is waiting for an answer (see "Removing a plan" below). The five analyst tools are
deliberately withheld:

* a KPI snapshot carries its own 30-day outlook that ignores planned events, so it contradicts the forecast
  the backend computed and put into the prompt;
* with the transaction tools the small model stated wrong facts (for example "no rent was paid" after
  filtering by a transaction type that does not exist).

They stay in the server for inspection with an MCP client (see "Run locally") and for a larger model. KPI
values are only read from `kpi_snapshots` (written by `backend/kpi_engine`), never recomputed here.
Transaction sums are in the original currency (CHF rows are not converted).

## Planned events (the write side)

The owner describes a plan in the chat ("we close for holidays June 1-21", "in July I buy an oven for 8k").
The assistant calls `propose_planned_event`, asks the owner to confirm, and only after an explicit yes calls
`save_planned_event`. The backend enforces the confirmation in code (`backend/llm_chat.py`): the save tool is
offered only while a checked proposal the owner has seen is waiting, and a saved plan that differs from that
proposal is deleted again and shown for confirmation.
The forecast engine in the backend applies the saved events; this server never computes their effect.

Rules, checked by both tools. A violation is returned as a normal result with the reason
(`{"valid": false, "error": "..."}` / `{"saved": false, "error": "..."}`) rather than as an MCP tool error,
because OGX reduces tool errors to `Error (code 1): None` in its `mcp_call` output items:

* `event_type` is `closure`, `expense` or `inflow` only (`payment_deferral` and `credit_line` are created
  by the backend from approved rescue options, `term_deposit` and `fx_prebuy` from approved CFO
  recommendations, never from the chat);
* the `X-As-Of-Date` header is required: it is the simulated "today" and is stored as `as_of_date`;
* the event must lie in the forecast, which starts **tomorrow** (today's bookings are already in the bank
  balance): an `expense` / `inflow` is dated tomorrow or later, a `closure` starts today or later and ends
  tomorrow or later. This is the rule the backend applies (`date_to` after the as-of date), so a saved plan
  always changes the forecast. `date_from` is at most 366 days ahead; `date_to` defaults to `date_from`;
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
{"valid": false, "error": "date_from 2025-04-01 is before today (2025-05-10). Planned events must be in the future; check the year and ask the owner if unsure."}
{"valid": false, "error": "Today's (2025-05-10) payments are already in the bank balance and the forecast starts tomorrow. Put tomorrow or a later date in date_from; ask the owner if unsure."}
{"saved": false, "error": "amount_eur is required for an expense (EUR, greater than 0). Ask the owner for the amount instead of guessing."}

// list_planned_events
{"analysis_date": "2025-05-10",
 "events": [{"id": 12, "event_type": "closure", "date_from": "2025-06-01", "date_to": "2025-06-21",
             "amount_eur": null, "category": null, "description": "Summer holiday closure", "source": "chat"},
            // term_deposit / fx_prebuy rows (approved CFO recommendations) also say in words what they are;
            // the server still computes no effect, the forecast comes from the backend
            {"id": 31, "event_type": "term_deposit", "date_from": "2025-10-16", "date_to": "2025-11-16",
             "amount_eur": 9000.0, "category": "tax_reserve", "description": "Tax reserve deposit", "source": "agent",
             "summary": "Money set aside for income tax: EUR 9,000.00 is tied up in a term deposit with the bank from 2025-10-16 until 2025-11-16, when it returns to the account with interest. The owner approved it; the forecast in the SITUATION already includes it."}]}
```

### Removing a plan

The owner can ask the assistant to take a plan out again ("remove the oven purchase"). It follows the same
propose, confirm, execute scheme as adding one, and the backend enforces the two turns in code:

1. The assistant picks the plan from the saved plans the backend lists in its prompt (each with id and source)
   and calls `propose_planned_event_removal(event_id)`. Nothing is deleted; the backend returns the plan to
   the UI (`pending_removals` in the `POST /chat` response), which shows a "Remove this plan?" card.
   `remove_planned_event` is not offered in this turn.
2. In the next turn, and only in that one, the backend offers `remove_planned_event` and sends the header
   **`X-Removable-Event-Ids`** (comma-separated ids) with exactly the plans on the card. The server refuses
   every other id before touching the database, so the model cannot remove something the owner was not shown.
   Without the header nothing can be removed. The removed plan comes back as `removed_events`.

A removal cannot be undone, so the backend is stricter here than for saving: if the owner answers "no", asks
something else or changes the topic, the pending removal is gone and has to be proposed again; an old
conversation id cannot be replayed to confirm it later.

The removal instructions, the plan ids and `propose_planned_event_removal` are part of a chat turn only when
the turn is about removing: a plan from the chat is saved and the owner asks for a removal (recognised by the
usual words, or handed back by the model itself with the internal word `REMOVE_PLAN_REQUEST`, after which the
backend asks again with the removal instructions), or the owner answers a removal question. All other turns
keep the shorter prompt, because with the removal instructions always present the small model handled new
plans measurably worse. Every deleted row is written to the server log
(`tool=remove_planned_event removed event={...}`), the only record of a removed plan.

Only plans that came from the chat (`source = 'chat'`) can be removed this way. A measure created by an
approved rescue option or an approved CFO recommendation (a term deposit, an advance CHF purchase) is refused
with an explanation that names the button under "My plans" with which the owner withdraws the whole decision
("Withdraw option" for a rescue option, "Withdraw recommendation" for a CFO recommendation). When no saved plan
matches the request, the assistant says so and names the current plans.

```jsonc
// propose_planned_event_removal (nothing removed)
{"valid": true, "event": {"id": 12, "event_type": "expense", "date_from": "2025-06-10", ...},
 "confirmation_prompt": "You want to remove this plan: expense of EUR 8,000.00 on 2025-06-10 - New oven. Should I remove it from your plans?"}

// remove_planned_event with the id in X-Removable-Event-Ids
{"removed": true, "event": {"id": 12, ...}}

// a confirmed id that is already gone (repeated request, or removed in "My plans" meanwhile): not an error,
// the backend reports the plan as removed
{"removed": false, "already_removed": true, "event_id": 12, "note": "..."}

// unknown id, a plan of an approved option, or an id the owner did not confirm (nothing removed)
{"valid": false, "error": "..."}
{"removed": false, "error": "..."}
```

## No future data: the as-of cutoff

The agent analyses a specific day (e.g. 2025-06-30), so it must not see later data. Every read tool caps
its dates at the cutoff, which is the earlier of:

* HTTP header **`X-As-Of-Date: YYYY-MM-DD`**, set per agent run by the orchestrator;
* env **`AMALFI_AS_OF_DATE`**, an optional server-wide cap.

With neither set, all data is visible to the read tools; the tools that propose or save a planned event
refuse to run without the header. Example OGX / OpenAI Responses call:

```python
client.responses.create(
    model="vllm-cfo/cfo-llm", instructions=SYSTEM_PROMPT, input="Analyse the situation as of 2025-06-30.",
    tools=[{"type": "mcp", "server_label": "amalfi",
            "server_url": "http://amalfi-mcp.autonomous-cfo.svc.cluster.local:8080/mcp",
            "headers": {"X-As-Of-Date": "2025-06-30"}, "require_approval": "never"}],
)
```

## Security

Read tools plus two constrained write tools (save a chat plan, remove a chat plan):

* DB role `amalfi_mcp` (created by `db/setup_mcp_user.sh`, credentials in the OpenShift secret
  `amalfi-mcp-db`, never in git):
  * `SELECT` on `bank_transactions`, `kpi_snapshots`, `fixed_obligations`, `account_balance_anchors`,
    `planned_events`, `owner_decisions`;
  * `INSERT` on `planned_events`, limited to the columns of a chat event (not `id`, `category`,
    `decision_id`, `created_at`), and `USAGE` on its id sequence;
  * `DELETE` on `planned_events`;
  * row level security policies on `planned_events` let the role insert and delete only rows with
    `source = 'chat'` (the delete policy is `mcp_delete_chat_events`), and the table constraint
    `planned_events_chat_scope` (`db/schema.sql`) limits chat rows to `closure`, `expense` and `inflow`.
    The role therefore can neither create nor remove a `credit_line`, `payment_deferral`, `term_deposit`
    or `fx_prebuy`, even with hand-written SQL;
  * nothing else: no `UPDATE` anywhere, no `DELETE` on any other table. Transactions, KPIs and owner
    decisions are read-only for this server.
* The role and every session default to `default_transaction_read_only=on` with a 10 s statement timeout.
  All read tools use that read-only connection. Only `save_planned_event` and `remove_planned_event` open a
  separate connection with a single explicit `READ WRITE` transaction (`db.connect_write`), committed on
  success, rolled back on error.
* `remove_planned_event` deletes only an id listed in the `X-Removable-Event-Ids` header. The backend sets
  that header per chat turn to the plans the owner was shown and confirmed; the model cannot set it. The
  `DELETE` statement itself is also limited to `source = 'chat'`.
* Event type is a closed enum in the tool schema and is validated again in code; dates and amounts are
  validated before any SQL runs; `source` is always written as `chat`.
* The LLM never sends SQL: only fixed, parameterised queries with whitelisted options and bounded result
  sizes.
* No Route: the server is reachable only inside the cluster.

## Run locally

```bash
oc port-forward -n autonomous-cfo svc/amalfi-db 15432:5432 &
cd mcp_server && pip install -r requirements-dev.txt   # requirements.txt + pytest
python -m pytest -q                                    # unit tests (no database needed)
KPI_TEST_DATABASE_URL=postgresql://... python -m pytest -q   # plus the database tests (own throw-away schema)
PGHOST=localhost PGPORT=15432 PGDATABASE=amalfi PGUSER=amalfi_mcp PGPASSWORD=... MCP_PORT=18080 python app.py
npx @modelcontextprotocol/inspector     # connect to http://localhost:18080/mcp (streamable HTTP)
```

## Deploy (OpenShift)

```bash
bash db/setup_mcp_user.sh                # DB role + grants + row level security + secret amalfi-mcp-db (idempotent)
oc apply -f deploy/mcp-server.yaml       # ImageStream, BuildConfig, Deployment, Service
```

After merge to `main` the CI poller (`deploy/ci-poller.yaml`) rebuilds `amalfi-mcp` whenever `mcp_server/`
changes. To deploy unmerged code, start a binary build from a directory that contains `mcp_server/`
(the BuildConfig uses `contextDir: mcp_server`):

```bash
mkdir -p /tmp/mcp-build && cp -r mcp_server /tmp/mcp-build/
oc start-build amalfi-mcp -n autonomous-cfo --from-dir=/tmp/mcp-build --follow
```
