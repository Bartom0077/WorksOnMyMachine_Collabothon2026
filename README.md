# Project Name

FinGuard

---

## One-Line Summary

FinGuard is an AI CFO for small and medium business owners: it forecasts the bank balance up to 90 days ahead, warns about cash gaps before they happen and proposes costed fixes the owner approves.

---

## Technology Stack

RedHat OpenShift AI, Python, FastAPI, PostgreSQL, MCP, OGX, vLLM, React, TypeScript, llm-d, qwen-7b-fp8, Nemo guardrails

---

## Project Overview

### THE PROBLEM

Small and medium business owners run their companies, not their finances. A business can be profitable over the year and still run out of cash in a specific week. Our demo client, Pizzeria Amalfi in Frankfurt, has ~21,000 EUR of fixed monthly costs (rent, salaries, taxes, a CHF transfer to a partner in Zurich). A 3-week summer closure cuts revenue to zero while costs keep running, so the balance quietly drops to -22,098 EUR on 3 July, although the year ends at +33,266 EUR. The owner only sees it when payments bounce. Banks see the data, but nobody translates it into an early, actionable warning.

### YOUR SOLUTION

FinGuard is an autonomous liquidity agent that acts like a personal CFO. It reads the bank statement, builds a day-by-day e.g. 60-day balance forecast and shows a traffic light (green / yellow / red). The owner describes plans in plain language in a chat ("we close for holidays June 1-21", "in July I buy an oven for 8k"); the AI turns them into structured events, asks for confirmation and the forecast is recalculated. When a gap appears, FinGuard simulates rescue options (credit line, deferring the Zurich transfers, shorter holiday, moving a purchase, or a combination), costs each one and recommends the best in simple words. Key principle: code computes every number deterministically; the LLM only understands intent, explains and recommends from options the code calculated. The owner always makes the final decision.

### THE WORKING PROTOTYPE

Implemented and running on OpenShift AI, end to end:

- choose the simulated "today" date, horizon (30/60/90 days) and safety buffer;
- dashboard with balance history, forecast without action (red) and with the selected option (green), zero line, credit limit and planned events;
- chat assistant that recognises closures, expenses and inflows, asks for missing data and saves events only after explicit confirmation;
- risk detection with a risk window, and rescue options A-E with new minimum balance, cost in EUR and non-financial cost;
- AI explanation + recommendation with a deterministic fallback if the LLM fails;
- approve / reject: execution is simulated (e.g. credit line request), the decision and a forecast snapshot are stored as an audit trail, the chart and light turn green;
- "My plans" list and decision history.

Not implemented: real bank integration and real payments (all execution is simulated).

### BEHIND THE BUILD

Pods on OpenShift AI: React/TypeScript frontend (nginx), Python FastAPI backend, MCP data server and OGX in front of vLLM. The backend holds all deterministic logic: transaction categorisation, CHF->EUR conversion from rates in payment references, balance reconstruction from an opening anchor, recurring-payment and 28-day-average forecast, event overlay, risk scoring and option simulation. It talks to the model only through OGX (Responses API) and passes the MCP server as a tool. The MCP server exposes whitelisted, parameterised read tools (KPI snapshots, cash-flow summaries, transactions, fixed obligations) and one narrowly scoped write: saving a confirmed planned event. An X-As-Of-Date header cuts off data after the simulated "today", so the agent cannot see the future.

### CHALLENGES AND DISCOVERIES

- LLMs are unreliable with numbers, so we split responsibilities: code calculates, the LLM explains and picks only from validated options; invalid JSON or unknown option IDs fall back to the code ranking.
- The statement has no balance column and mixes EUR with CHF; we reconstruct the balance from an anchor and parse FX rates from payment references.
- OGX reduced MCP tool errors to "Error (code 1): None", so validation errors are returned as normal results the model can read and act on (e.g. ask for a missing amount).
- Model retries could save the same event twice, so saving is idempotent.
- Least privilege: the MCP database role can only insert planned events; everything else is read-only.

### RESULTS AND NEXT STEPS

The full demo story works on the cluster: on 10 May 2025 the owner announces the June closure, the light turns red with the -22k EUR dip on 3 July, FinGuard recommends an option, the owner approves it and the forecast turns green. Validation: 214 backend unit/API tests and 43 MCP server tests pass, plus 27 Playwright end-to-end tests on a mocked API (including accessibility checks with axe) and 9 live end-to-end tests against the deployed system. We are most proud of the clean split between deterministic finance and AI explanation, which makes every recommendation traceable. Limitations: small open source model, no seasonality, a single demo company, an assumed 9% credit-line rate, confirmation enforced by the prompt rather than by tool-level approval. Next: real open-banking data, seasonality-aware forecasting, tool-level approval in OGX, MLflow tracing of LLM calls, and additional agents (FX, taxes, investing surplus cash).

---

## Source Code Repository

<https://github.com/Bartom0077/WorksOnMyMachine_Collabothon2026>
