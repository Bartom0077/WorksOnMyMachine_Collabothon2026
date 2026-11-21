# Autonomous CFO — liquidity agent for Pizzeria Amalfi

Team WorksOnMyMachine, Collabothon 2026, challenge **"Autonomous CFO — An AI Finance Organization on
Autopilot"** (Red Hat / Commerzbank).

A small-business owner has no time for finance. This application watches the bank account of Pizzeria
Amalfi, forecasts the balance day by day, warns before the money runs out, computes rescue options with
their cost, explains them in plain language, and carries out the option the owner approves (simulated).
The owner can also tell the assistant about plans in a chat ("we close for holidays from 1 to 21 June") and
immediately see what they do to the forecast, and ask it to remove such a plan again ("remove the oven
purchase").

The full specification is [`ROZWIAZANIE.md`](ROZWIAZANIE.md) (Polish). Everything else in the repository
is English.

## Who does what

| Step | Done by | How |
|---|---|---|
| Balance, forecast, risk status, rescue options and their cost | **code** | deterministic Python (`backend/`), the same input always gives the same numbers |
| Explaining the situation, recommending one of the computed options | **LLM** | receives the numbers as a JSON dossier; its answer is validated, with a deterministic fallback text if the model is unavailable or invalid |
| Turning the owner's words into a planned event, or finding the plan the owner wants removed | **LLM** | through MCP tools that validate the request; the owner confirms before anything is saved or removed |
| Approving or rejecting an option | **owner** | explicit approval dialog; every decision is stored in an audit trail |
| Execution (credit line request, postponed payments) | **simulated** | recorded as planned events and messages; no money is moved |

The LLM never calculates a number and cannot move money.

## Architecture

```
 browser
    |  HTTPS (Route, the only public entry point)
    v
+---------------------+  /api/*   +----------------------------+   SQL    +--------------+
| frontend            | --------> | backend (FastAPI)          | -------> | PostgreSQL   |
| React + TypeScript  |           | ledger, forecast, events,  |          | "amalfi"     |
| served by nginx     |           | risk, options, decisions,  |          +--------------+
+---------------------+           | LLM orchestration          |                 ^
                                  +----------------------------+                 | SQL: read +
                                        | Responses API                          | INSERT of
                                        v                                        | chat events
                                  +--------------+   MCP    +--------------+     |
                                  | OGX server   | -------> | MCP server   | ----+
                                  +--------------+          | (FastMCP)    |
                                        | OpenAI API        +--------------+
                                        v
                                  +--------------+
                                  | vLLM cfo-llm |
                                  +--------------+
```

Everything runs in the OpenShift namespace `autonomous-cfo`. The backend talks only to OGX (never to vLLM
directly); OGX calls the MCP server when the model uses a tool. The backend sends the simulated "today"
with every call (`X-As-Of-Date`), so the model never sees data from the future.

## Repository layout

```
frontend/            React 18 + TypeScript + Vite + recharts; nginx image that proxies /api to the backend
  src/               App.tsx, api/ (REST client), components/, hooks/, lib/ (formatting, chart model), types/
  e2e/               Playwright: mocked/ (no backend needed), live/ (deployed stack), fixtures/
backend/             FastAPI application
  app.py, routes.py, llm_routes.py, schemas.py, errors.py, deps.py, config.py     REST layer
  ledger.py, categories.py, forecast.py, events.py, risk.py, options.py, texts.py  deterministic core
  service.py, domain.py, repository.py                                             orchestration, data access
  llm.py, llm_prompts.py, llm_recommendation.py, llm_chat.py                       OGX client and prompts
  kpi_engine/        deterministic KPI engine (see KPI_SPEC.md); the backend reuses its data access and money helpers
  tests/             pytest (unit tests on the real bank statement; optional database tests)
mcp_server/          MCP server: planned-event tools and read tools (see mcp_server/README.md)
db/                  schema.sql, CSV import, reference data, setup scripts
deploy/              OpenShift manifests: frontend, backend, mcp-server, postgres, ogx, ci-poller
Challenge/           challenge card and the 2025 bank statement (CSV)
Knowledge/           business knowledge and the documented fixed payments
KPI_SPEC.md          methodology of the KPI engine
ROZWIAZANIE.md       product specification (Polish)
```

## REST API

The frontend calls the backend through the `/api` prefix (nginx strips it). All three simulation
parameters are optional: `as_of_date` (default `2025-05-10`, must lie in the bank data range),
`horizon_days` (30, 60 or 90; default 60) and `buffer_eur` (default 3000).

| Method and path | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /dashboard?as_of_date&horizon_days&buffer_eur` | balance, history, forecast, risk, risk drivers, planned events, options, insights |
| `GET /events` | all planned events |
| `DELETE /events/{id}` | remove a planned event (204) |
| `GET /decisions` | decision history |
| `POST /decisions` | approve or reject an option: `{as_of_date, horizon_days, buffer_eur, option_id, status, llm_explanation?}` (201) |
| `POST /recommendation` | LLM explanation and recommended option for the given parameters; `source` is `llm` or `fallback` |
| `POST /chat` | one chat turn: `{as_of_date, horizon_days, buffer_eur, message, conversation_id?}` |
| `POST /demo/reset` | delete all planned events and decisions (204) |

Every failure has the same body: `{"error": {"code": "<machine_code>", "message": "<English text>"}}`.
Codes: `validation_error`, `as_of_date_out_of_range` (422), `not_found` (404), `method_not_allowed` (405),
`option_not_available` (409), `assistant_unavailable`, `data_unavailable`, `database_unavailable` (503),
`inconsistent_bank_data`, `internal_error` (500). Interactive API documentation: `/docs` on the backend.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` or `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | backend, MCP server | PostgreSQL connection |
| `OGX_URL` | backend | OGX base URL (default: the in-cluster service on port 8321) |
| `OGX_MODEL` | backend | model id in OGX (default `vllm-cfo/cfo-llm`) |
| `MCP_SERVER_URL` | backend | MCP endpoint that OGX calls (default: the in-cluster service, `/mcp`) |
| `ACCOUNT_NUMBER` | backend | bank account to analyse; optional when the data holds one account |
| `HOST`, `PORT` | backend | listen address (default `0.0.0.0:8080`) |
| `MCP_HOST`, `MCP_PORT` | MCP server | listen address (default `0.0.0.0:8080`) |
| `AMALFI_AS_OF_DATE` | MCP server | optional server-wide cap for visible data |
| `VITE_BACKEND_URL` | frontend dev server | backend that `/api` is proxied to (default `http://localhost:8080`) |
| `E2E_BASE_URL` | Playwright | test a deployed instance instead of the local dev server |

## Run locally

Requirements: Python 3.12+, Node.js 22, and either `podman` (local database) or `oc` access to the cluster.

**1. Database.** Either a local PostgreSQL loaded from the repository (run from the repository root):

```bash
podman run -d --name amalfi-pg -e POSTGRES_USER=amalfi -e POSTGRES_PASSWORD=amalfi -e POSTGRES_DB=amalfi \
  -p 127.0.0.1:5432:5432 docker.io/library/postgres:16
podman exec -i amalfi-pg sh -c 'cat > /tmp/statement.csv' < Challenge/pizzeria-amalfi-bank-statement-2025-v6.csv
cat db/schema.sql db/import_transactions.sql db/kpi_reference_data.sql \
  | podman exec -i amalfi-pg psql -v ON_ERROR_STOP=1 -q -U amalfi -d amalfi
export DATABASE_URL=postgresql://amalfi:amalfi@localhost:5432/amalfi
```

or the cluster database (the password is in the secret `amalfi-db`):

```bash
oc port-forward -n autonomous-cfo svc/amalfi-db 5432:5432 &
export DATABASE_URL="postgresql://amalfi:<password>@localhost:5432/amalfi"
```

**2. Backend** on http://localhost:8080:

```bash
cd backend
pip install -r requirements-dev.txt
python app.py
```

Without access to OGX the forecast, options and decisions work fully; the explanation falls back to the
deterministic text and the chat answers that the assistant is unavailable. To use the model locally, work
against the cluster database (the MCP server writes plans there) and forward OGX:

```bash
oc port-forward -n autonomous-cfo svc/cfo-ogx-service 8321:8321 &
OGX_URL=http://localhost:8321 python app.py
```

**3. Frontend** on http://localhost:5173:

```bash
cd frontend
npm ci
npm run dev        # VITE_BACKEND_URL=http://host:port npm run dev  for another backend
```

The MCP server is described in [`mcp_server/README.md`](mcp_server/README.md).

## Tests

```bash
# backend: unit tests on the real 2025 bank statement, no database or LLM needed
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
# optional database tests; they create and drop their own schema, real tables are not touched
KPI_TEST_DATABASE_URL="$DATABASE_URL" python -m pytest -q

# MCP server
cd mcp_server && pip install -r requirements-dev.txt && python -m pytest -q

# frontend
cd frontend && npm ci
npm run typecheck
npm run build
npx playwright install chromium     # once
npm run test:e2e                    # Playwright against the dev server, backend mocked

# the demo story against a deployed stack: real backend, database and LLM (takes several minutes)
E2E_BASE_URL=https://<route-host> npm run test:e2e:live
```

**The live suite starts and ends with "Reset demo": it deletes all planned events and decisions of the
instance it runs against.**

## Deploy to OpenShift

All objects live in the namespace `autonomous-cfo`. Prerequisites that are not part of this repository's
automation: OpenShift AI with the model `cfo-llm` served by vLLM and the OGX operator enabled.

```bash
oc login ...
bash db/setup.sh                 # project, PostgreSQL, schema, bank statement, reference data
bash db/setup_mcp_user.sh        # database role of the MCP server (insert and delete of chat events only) + its secret
oc apply -f deploy/ogx-base-config.yaml -f deploy/ogx.yaml        # OGX in front of vLLM
oc apply -f deploy/mcp-server.yaml -f deploy/backend.yaml -f deploy/frontend.yaml
oc apply -f deploy/ci-poller.yaml                                 # optional: rebuild on every change of main

# first images (the BuildConfigs build the main branch of the GitHub repository)
oc start-build amalfi-mcp -n autonomous-cfo --follow
oc start-build autonomous-cfo-backend -n autonomous-cfo --follow
oc start-build autonomous-cfo -n autonomous-cfo --follow

oc get route autonomous-cfo -n autonomous-cfo -o jsonpath='{.spec.host}'    # the application URL
```

Each manifest holds ImageStream, BuildConfig, Deployment and Service; only the frontend has a Route. The
backend and the MCP server are internal: reach them with
`oc port-forward -n autonomous-cfo svc/autonomous-cfo-backend 8080:8080`. A new image rolls out
automatically (image trigger on the Deployment). The CI poller checks `main` every minute and starts the
build of each component whose directory changed.

**Binary builds** deploy code that is not on `main`. The upload must contain the component directory,
because every BuildConfig has a `contextDir`:

```bash
BUILD_DIR=$(mktemp -d)
rsync -a --exclude __pycache__ --exclude .pytest_cache backend mcp_server "$BUILD_DIR/"
rsync -a --exclude node_modules --exclude dist --exclude test-results frontend "$BUILD_DIR/"
oc start-build amalfi-mcp -n autonomous-cfo --from-dir="$BUILD_DIR" --follow
oc start-build autonomous-cfo-backend -n autonomous-cfo --from-dir="$BUILD_DIR" --follow
oc start-build autonomous-cfo -n autonomous-cfo --from-dir="$BUILD_DIR" --follow
```

**Updating an existing installation.** `db/schema.sql` and `db/setup_mcp_user.sh` are idempotent; apply
both before rolling out a new MCP server or backend image:

```bash
POD=$(oc get pod -n autonomous-cfo -l app=amalfi-db -o jsonpath='{.items[0].metadata.name}')
oc exec -i -n autonomous-cfo "$POD" -- bash -c \
  'PGPASSWORD="$POSTGRESQL_PASSWORD" psql -h localhost -U "$POSTGRESQL_USER" -d "$POSTGRESQL_DATABASE" -v ON_ERROR_STOP=1 -q' \
  < db/schema.sql
bash db/setup_mcp_user.sh
```

Installations created before the backend lost its public Route still have it; remove it with
`oc delete route autonomous-cfo-backend -n autonomous-cfo`.

## Demo script (3 minutes)

All figures below are computed by the code from the 2025 bank statement, with the defaults: horizon
60 days, safety buffer €3,000. Start with **Reset demo**.

1. **"Vincenzo runs a pizzeria and has no time for finance."** Today is 10 May 2025 and the balance is
   **€3,205.69**. Even without any plans the agent finds a marginal problem: the lowest projected balance is
   **−€325.55** on 1 July, so the status is "Shortfall ahead". The chart shows why (fixed payments at the
   start of the month).
2. **The owner tells the assistant a plan.** In the chat: *"We are closing for holidays from 1 to 21 June."*
   The assistant repeats what it understood and asks for confirmation; after "yes" the plan appears under
   "My plans" and the forecast is recalculated: the lowest balance drops to **−€15,728.42 on 1 July**. Three
   weeks without revenue, while salaries, rent and the transfers to Zurich continue. (A plan from the chat
   can be taken out the same way: *"Remove the oven purchase"* shows a "Remove this plan?" card, and nothing
   is removed before "Yes, remove it".)
3. **Options computed by code, explained by the model.** The agent shows the options with their new lowest
   balance and cost: A (credit line), B (postpone the transfer to Zurich), C (shorter closure) and
   E (postpone payments + a smaller credit line). The explanation card is marked "AI explanation" (or
   "Standard explanation" when the model is unavailable). Ask in the chat: *"Why E?"*
4. **The owner approves.** Approve option E in the dialog ("Approve (simulated)"). The transfers are
   postponed and a **€15,000 credit line** is recorded; the lowest balance becomes −€7,061.74, which the
   credit line covers, so the status turns to **"On track"**. The decision, with the status before and
   after, is in the decision history. No money was moved.
5. **Bonus: the opposite situation.** Move "Today" to 31 December 2025: the balance is **€33,266.35**, the
   status is "On track", and the agent points out about **€27,000** that is not needed for payments or the
   buffer and could go to a short-term deposit.

## Known limits

* Execution is simulated by design: approving an option records events and messages only.
* No authentication: the demo has a single owner, and "Reset demo" is open to anyone who can reach the URL.
* The recommendation cache is in memory (per backend pod, lost on restart).
* The interest rates used for option costs are illustrative assumptions, not bank quotes
  (`backend/config.py`).
* The chat's "confirm before saving or removing" rule is enforced by backend code (`backend/llm_chat.py`: the
  writing tools are offered only for plans the owner was shown, saves are checked against the confirmed
  proposal, removals are limited to the ids on the confirmation card), not by an approval gate in OGX. Whether
  the owner's answer means "yes" is still judged by the model.
* A plan removed through the chat is deleted for good; the only record is a log line of the MCP server.
* The forecast uses averages of the last 28 days and the documented recurring payments; there is no
  seasonality or public-holiday model.
* One bank account, EUR; the CHF transfers are converted with the rates in the payment references.
