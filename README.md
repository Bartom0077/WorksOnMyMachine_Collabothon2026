# Autonomous CFO — liquidity agent and CFO advisors for Pizzeria Amalfi

Team WorksOnMyMachine, Collabothon 2026, challenge **"Autonomous CFO — An AI Finance Organization on
Autopilot"** (Red Hat / Commerzbank).

A small-business owner has no time for finance. This application watches the bank account of Pizzeria
Amalfi, forecasts the balance day by day, warns before the money runs out, computes rescue options with
their cost, explains them in plain language, and carries out the option the owner approves (simulated).
The owner can also tell the assistant about plans in a chat ("we close for holidays from 1 to 21 June") and
immediately see what they do to the forecast, and ask it to remove such a plan again ("remove the oven
purchase").
When the cash position is safe, three advisors (income tax, CHF transfers, term deposits) propose what to do
with the spare cash, and a coordinator shows only proposals that can be approved in any combination.

The full specification is [`ROZWIAZANIE.md`](ROZWIAZANIE.md) (Polish). Everything else in the repository
is English.

## Who does what

| Step | Done by | How |
|---|---|---|
| Balance, forecast, risk status, rescue options and their cost | **code** | deterministic Python (`backend/`), the same input always gives the same numbers |
| Explaining the situation, recommending one of the computed options | **LLM** | receives the numbers as a JSON dossier; its answer is validated, with a deterministic fallback text if the model is unavailable or invalid |
| CFO recommendations: tax reserve, CHF purchase or rate lock, term deposits, and the rules that keep them compatible | **code** | `backend/advisors/`: three domain agents compute facts and candidates, the coordinator's fixed rule table (R1–R6) removes every conflict |
| Wording the CFO recommendations, extra tips, the coordinator's summary | **LLM** | one call per advisor plus one for the summary, in parallel; each is validated and falls back to the code texts on its own; tips cannot be selected |
| Turning the owner's words into a planned event, or finding the plan the owner wants removed | **LLM** | through MCP tools that validate the request; the owner confirms before anything is saved or removed |
| Approving or rejecting an option or any set of CFO recommendations | **owner** | explicit approval dialog; every decision is stored in an audit trail |
| Execution (credit line request, postponed payments, term deposits, CHF purchase, rate lock) | **simulated** | recorded as planned events and messages; no money is moved |

The LLM never calculates a number and cannot move money.

## Architecture

```
 browser
    |  HTTPS (Route, the only public entry point)
    v
+---------------------+  /api/*   +----------------------------+   SQL    +--------------+
| frontend            | --------> | backend (FastAPI)          | -------> | PostgreSQL   |
| React + TypeScript  |           | ledger, forecast, events,  |          | "amalfi"     |
| served by nginx     |           | risk, options, advisors,   |          +--------------+
+---------------------+           | decisions, LLM calls       |                 ^
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

A separate development stack lives in the namespace `autonomous-cfo-dev` (see "Deploy to OpenShift").

## Repository layout

```
frontend/            React 18 + TypeScript + Vite + recharts; nginx image that proxies /api to the backend
  src/               App.tsx, api/ (REST client), components/ (incl. AdviceSection, ProposalCard,
                     AdviceApprovalDialog), hooks/, lib/ (formatting, chart model, advice cards), types/
  e2e/               Playwright: mocked/ (no backend needed), live/ (deployed stack), fixtures/
backend/             FastAPI application
  app.py, routes.py, llm_routes.py, schemas.py, errors.py, deps.py, config.py     REST layer
  advice_routes.py, advice_schemas.py                                              REST layer of the CFO recommendations
  ledger.py, categories.py, forecast.py, events.py, risk.py, options.py, texts.py  deterministic core
  service.py, domain.py, repository.py                                             orchestration, data access
  llm.py, llm_prompts.py, llm_recommendation.py, llm_chat.py                       OGX client and prompts
  advisors/          CFO advisors: tax.py, fx.py, investment.py (facts and candidates), coordinator.py (rule
                     table), engine.py (entry point), prompts.py + llm_service.py + grounding.py (model calls and
                     their validation), decisions.py
  kpi_engine/        deterministic KPI engine (see KPI_SPEC.md); the backend reuses its data access and money helpers
  tests/             pytest (unit tests on the real bank statement; optional database tests)
mcp_server/          MCP server: planned-event tools and read tools (see mcp_server/README.md)
db/                  schema.sql, CSV import, reference data, setup scripts
deploy/              OpenShift manifests: frontend, backend, mcp-server, postgres, ogx, ci-poller;
                     deploy-dev.sh deploys the working tree to the development namespace
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
| `DELETE /events/{id}` | remove a planned event (204); an event created by an approved option or recommendation withdraws that whole decision |
| `GET /decisions` | decision history |
| `POST /decisions` | approve or reject an option: `{as_of_date, horizon_days, buffer_eur, option_id, status, llm_explanation?}` (201) |
| `POST /recommendation` | LLM explanation and recommended option for the given parameters; `source` is `llm` or `fallback` |
| `POST /chat` | one chat turn: `{as_of_date, horizon_days, buffer_eur, message, conversation_id?}` |
| `GET /advice?as_of_date&horizon_days&buffer_eur` | CFO recommendations computed by code: baseline status, `liquidity_first`, `surplus_eur`, the `proposals` on offer (each with amount, dates, effect, assumptions and its own forecast) and the `suppressed` candidates with a reason code (debugging only) |
| `POST /advice/recommendation` | LLM wording for the same parameters: `summary`, per-advisor `agents` (`source` is `llm` or `fallback`, plus non-selectable `tips`) and a text per proposal |
| `POST /advice/decisions` | approve or reject several proposals at once: `{as_of_date, horizon_days, buffer_eur, proposal_ids, status, explanations?, fingerprints?}` (201); one decision per proposal, stored in one transaction. `fingerprints` names the proposals as the owner saw them (`fingerprint` of each proposal in `GET /advice`); a proposal that is no longer offered, or has changed since, is refused with 409 |
| `POST /demo/reset` | delete all planned events and decisions (204) |

Every failure has the same body: `{"error": {"code": "<machine_code>", "message": "<English text>"}}`.
Codes: `validation_error`, `as_of_date_out_of_range` (422), `not_found` (404), `method_not_allowed` (405),
`option_not_available`, `proposal_not_available` (409), `assistant_unavailable`, `data_unavailable`, `database_unavailable` (503),
`inconsistent_bank_data`, `internal_error` (500). Interactive API documentation: `/docs` on the backend.

Planned events have one of seven types. `closure`, `expense` and `inflow` come from the chat;
`payment_deferral` and `credit_line` from an approved rescue option; `term_deposit` (category `investment` or
`tax_reserve`) and `fx_prebuy` from an approved CFO recommendation. In the forecast a term deposit takes its
principal out on the opening day and pays it back with interest at maturity; an advance CHF purchase takes
its price out on the purchase day and removes the Zurich instalments it covers.

## CFO recommendations

Below the rescue options the dashboard shows the proposals of three narrow advisors
(`backend/advisors/`, specification: `ROZWIAZANIE.md` §18):

* **Tax** (income tax only): estimated tax = cash profit so far this year × (yearly prepayments / prior-year
  profit, both from the documented obligation, about 15.3%). The part the prepayments do not cover can be
  set aside in a 1-month term deposit (`T1`); a code-written piece of advice (`T2`) suggests asking for
  higher prepayments. No statutory rate and no settlement date is ever stated.
* **Currency** (the CHF instalments to Zurich only): buy the francs for all remaining instalments now (`F1`)
  or lock the rate with a forward (`F2`), together with how far the EUR cost could move if the rate moved
  as it has so far.
* **Investments**: a term deposit (`I1`) over cash that is needed neither for payments nor for the buffer.

The coordinator applies a fixed rule table in code, so that any subset of the shown proposals can be approved
together: liquidity comes first (nothing ties up cash while the status is not "On track" or a credit line is
in use), all cash-blocking proposals share the surplus (tax reserve, then CHF purchase, then investment
deposit; a tax reserve larger than the surplus is capped to it), only one FX proposal and one investment
deposit are shown, and nothing already decided is proposed again. The language model only rewords the
proposals, may add tips and writes the summary; every amount, date and percentage it quotes must appear in
its dossier.

Illustrative assumptions, not bank quotes or tax rules (`backend/config.py`): deposit interest of 2% p.a.,
deposit terms of 1, 3 and 6 months, a 1-month term for the tax reserve, a minimum tax shortfall of €1,000,
at least 2 remaining instalments for a rate lock, an FX swing below €500 called small, and an investment
deposit only from €10,000 of surplus. The forward and the CHF purchase are assumed to happen at the last
rate paid.

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

# MCP server (KPI_TEST_DATABASE_URL adds its optional database tests in the same way)
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
both before rolling out a new MCP server or backend image (the schema script also widens the event type
check of `planned_events` to `term_deposit` and `fx_prebuy`):

```bash
POD=$(oc get pod -n autonomous-cfo -l app=amalfi-db -o jsonpath='{.items[0].metadata.name}')
oc exec -i -n autonomous-cfo "$POD" -- bash -c \
  'PGPASSWORD="$POSTGRESQL_PASSWORD" psql -h localhost -U "$POSTGRESQL_USER" -d "$POSTGRESQL_DATABASE" -v ON_ERROR_STOP=1 -q' \
  < db/schema.sql
bash db/setup_mcp_user.sh
```

Installations created before the backend lost its public Route still have it; remove it with
`oc delete route autonomous-cfo-backend -n autonomous-cfo`.

**Development namespace.** The dev deployment lives in the separate namespace `autonomous-cfo-dev`: a complete
stack of its own (PostgreSQL, MCP server, OGX, backend, frontend) that shares only the model. The namespace
must already exist. `deploy/deploy-dev.sh` re-targets the manifests of `deploy/` to it and builds the three
images from the working tree with binary builds, so nothing has to be on `main`:

```bash
bash deploy/deploy-dev.sh            # everything
bash deploy/deploy-dev.sh infra      # database, OGX server and manifests only
bash deploy/deploy-dev.sh images     # rebuild and roll out the three images only
```

## Demo script (3 minutes)

All figures below are computed by the code from the 2025 bank statement, with the defaults: horizon
60 days, safety buffer €3,000. Start with **Reset demo**. The first screen shows the status, the chart and
the recommended option; "Options", "CFO advice", "My plans" and "History" are tabs of one card below the
chart.

1. **"Vincenzo runs a pizzeria and has no time for finance."** Today is 10 May 2025 and the balance is
   **€3,205.69**. Even without any plans the agent finds a marginal problem: the lowest projected balance is
   **−€325.55** on 1 July, so the status is "Shortfall ahead". The chart shows why (fixed payments at the
   start of the month); "Why?" in the status card lists those fixed costs.
2. **The owner tells the assistant a plan.** In the chat: *"We are closing for holidays from 1 to 21 June."*
   The assistant repeats what it understood and asks for confirmation; after "yes" the count on the
   "My plans" tab goes up (open the tab to see the plan) and the forecast is recalculated: the lowest
   balance drops to **−€15,728.42 on 1 July**. Three weeks without revenue, while salaries, rent and the
   transfers to Zurich continue. (A plan from the chat
   can be taken out the same way: *"Remove the oven purchase"* shows a "Remove this plan?" card, and nothing
   is removed before "Yes, remove it".)
3. **Options computed by code, explained by the model.** The agent shows the options with their new lowest
   balance and cost, the recommended one first: A (credit line), B (postpone the transfer to Zurich),
   C (shorter closure) and E (postpone payments + a smaller credit line). "Details" on an option holds its
   description, cost notes and the "Reject" button. The explanation in the status card is marked
   "AI explanation" (or "Standard explanation" when the model is unavailable). Ask in the chat: *"Why E?"*
4. **The owner approves.** Approve option E in the dialog ("Approve (simulated)"). The transfers are
   postponed and a **€15,000 credit line** is recorded; the lowest balance becomes −€7,061.74, which the
   credit line covers, so the status turns to **"On track"**. The decision, with the status before and
   after, is in the "History" tab. No money was moved.
5. **Bonus: the opposite situation.** Move "Today" to 31 December 2025: the balance is **€33,266.35**, the
   status is "On track", and the agent points out about **€27,000** that is not needed for payments or the
   buffer and could go to a short-term deposit. The "CFO advice" tab offers three steps that can be ticked
   and approved together: a **tax reserve deposit of €14,849.65** (1 month), the advice to ask for higher
   income tax prepayments, and an **investment deposit of €12,000** for 6 months. With all three approved
   the status stays "On track" (lowest balance €3,581.16) and both deposits appear in the "My plans" tab.

## Known limits

* Execution is simulated by design: approving an option records events and messages only.
* No authentication: the demo has a single owner, and "Reset demo" is open to anyone who can reach the URL.
* The recommendation caches are in memory (per backend pod, lost on restart).
* The interest rates used for option costs and term deposits, the deposit terms and the advisors' thresholds
  are illustrative assumptions, not bank quotes (`backend/config.py`).
* The CFO advisors are narrow on purpose: income tax only, estimated from cash profit with a rate derived
  from the documented prepayments (not a tax calculation); CHF only, without fees, spreads, forward points
  or rate forecasts; term deposits only.
* An approved rate lock or piece of advice creates no planned event, so it cannot be withdrawn.
* The chat's "confirm before saving or removing" rule is enforced by backend code (`backend/llm_chat.py`: the
  writing tools are offered only for plans the owner was shown, saves are checked against the confirmed
  proposal, removals are limited to the ids on the confirmation card), not by an approval gate in OGX. Whether
  the owner's answer means "yes" is still judged by the model.
* A plan removed through the chat is deleted for good; the only record is a log line of the MCP server.
* The forecast uses averages of the last 28 days and the documented recurring payments; there is no
  seasonality or public-holiday model.
* One bank account, EUR; the CHF transfers are converted with the rates in the payment references.
