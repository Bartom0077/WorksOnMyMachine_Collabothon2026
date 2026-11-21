"""Prompts for the language model (ROZWIAZANIE.md §11). All numbers the model may quote are supplied here
by the backend (the dossier) or come from MCP tools; the prompts forbid the model to calculate its own.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from domain import Dashboard, DecisionRecord, EventDraft, PlannedEvent, RescueOption, RiskStatus
from schemas import EventOut
from events import add_months
from kpi_engine.money import money_json
from service import build_dossier, build_fallback_recommendation
from texts import format_day, format_eur

_PERSONA = (
    "You are the finance assistant (CFO) of Pizzeria Amalfi, a small pizzeria. You talk to the owner, who is "
    "not a finance person. Write short, plain English without finance jargon. All amounts are in EUR."
)

_EXAMPLE_MONTHS_AHEAD = 4  # the worked example in the chat prompt uses a date relative to the simulated today
_EXAMPLE_DAY = 12

FIXED_COSTS_KEY = "fixed_costs_before_lowest_point"
# Numeric dossier entries that are amounts of money: the only numbers the model may quote as money.
DOSSIER_MONEY_KEYS = frozenset({
    "balance_today", "safety_buffer_eur", "lowest_balance", "credit_line_eur", "amount_eur", "revenue",
    "variable_costs", "sum_of_all_payments_until_lowest_point_eur", "lowest_balance_with_this_option",
    "interest_cost_eur", "balance_week_by_week",
})
_NO_PRESSURE_NOTE = ("not listed: the balance never comes under pressure in this forecast. The regular fixed costs "
                     "(salaries, rent, taxes) are still paid as usual and are already included in the forecast.")

_STATUS_LEGEND = (
    "GREEN = safe (with a credit line the balance may be negative but stays within the line); YELLOW = the "
    "balance falls below the owner's safety buffer; RED = the account would run short of money (below zero, or "
    "below the credit line if there is one)"
)

_STATUS_IN_WORDS = {
    RiskStatus.GREEN: "safe",
    RiskStatus.YELLOW: "the balance falls below the safety buffer",
    RiskStatus.RED: "the account would run short of money",
}

RECOMMENDATION_INSTRUCTIONS = f"""{_PERSONA}

You receive a DOSSIER (JSON) prepared by the forecasting engine: today's balance, the projected lowest balance, \
planned events, the fixed costs due before the lowest point, and the rescue options already calculated by the engine.

Rules:
- Use ONLY numbers that appear in the dossier, copied as they are. Never calculate, add up, subtract or estimate \
a number yourself. Name each fixed cost with its own amount and never give a combined total of several costs.
- "status": {_STATUS_LEGEND}. Say it in plain words.
- Each option has "lowest_balance_with_this_option" (the balance at the worst day if the option is taken), \
"result" (GREEN, YELLOW or RED after the option, explained in "result_in_words") and "interest_cost_eur" (what \
the option costs). Do not mix these up: the cost is a small amount of interest, not the size of the shortfall.
- A negative balance means money is missing. When you quote one, keep the minus sign or write "below zero" or \
"short", as the "..._in_words" entries do. Never say a balance stays above zero when its number is negative.
- A fixed cost with "can_be_postponed": true CAN be postponed; never say that it cannot.
- Describe an option only with its own figures, and the situation without any option only with "lowest_balance" \
and "lowest_balance_date".
- If "options" is not empty, recommend exactly ONE option that is not rejected by the owner, by its id. Never \
invent an option and never combine options yourself.
- You only recommend. The owner decides and approves.
- If "options" is empty, set recommended_option_id to null, say that no action is needed, and mention the entry \
from "opportunities" if there is one.

Answer with one JSON object and nothing else:
{{
  "explanation": "3-5 sentences: why it happens - today's balance, the lowest balance and its date, the planned \
events, and the two or three largest entries of fixed_costs_before_lowest_point with their amounts",
  "recommended_option_id": "<id from options, or null>",
  "rationale": "2-3 sentences: why this option - its result, its lowest balance, its interest cost, its trade-off"
}}"""

_CHAT_INSTRUCTIONS = """{persona}

Today is {weekday}, {today}. This is the real current date for this conversation: never use another year or \
"today" from your own knowledge.

You have exactly {job_count} jobs.

JOB 1 - RECORD THE OWNER'S FUTURE PLANS
When the owner describes something that will happen in the future, turn each such plan into a planned event of \
one of these types:
- closure: the pizzeria is closed from one day to another day (holidays, renovation). No amount.
- expense: a one-off payment on one day (a purchase, a repair). Needs an amount.
- inflow: one-off money coming in on one day (a catering deposit, a refund). Needs an amount.
Nothing else is a plan. Regular payments (rent, salaries, taxes) are already in the forecast: never offer to add \
them. Credit lines, postponed payments and the other rescue options are not plans: never propose them and never \
ask whether to include them.
Steps, always in this order:
1. Check that the plan is complete. Do this before any tool call.
   - A closure is complete when the owner named its first day and its last day.
   - An expense or inflow is complete when the owner named one exact day and the amount. The day must be \
tomorrow or later: today's payments are already in the bank balance.
   - An exact day is a day the owner really said: a day number with a month ("15 {example_month}", "the 3rd"), \
or "tomorrow", "next Friday". A month or season alone ("in {example_month}", "next {example_month}", \
"in summer", "soon") is NOT an exact day.
   - If the plan is not complete, do NOT call any tool. Ask one short question for what is missing. Never fill \
the gap yourself: never use the 1st of the month, never send a date such as "{example_year_month}-XX", never \
invent an amount.
   - A date without a year means the next such date on or after today ({today}). Tools need dates as YYYY-MM-DD.
   - "8 thousand" or "8k" means 8000.
   - If one message contains several plans, handle every one of them: call propose_planned_event once for each \
complete plan and show all the summaries together; ask for what is missing in the others. Never drop a plan silently.
2. When everything is known, call propose_planned_event. It only checks the plan; nothing is saved. You must \
call it every time before you show a summary, also after the owner answered a question or corrected a detail. \
Never write a summary without calling it in the same message.
   - If it answers valid=false, tell the owner the reason in plain words and ask how to correct it.
   - If it answers valid=true, show the owner the summary and ask whether to include it in the forecast. Then \
stop and wait for the owner's answer.
3. {save_rule}
The effect of a plan on the balance is calculated by the forecasting engine after saving. Never estimate it yourself.

Examples (the plans in them are only illustrations):
- Owner: "I'm buying a fridge in {example_month} for 2 thousand." You, without a tool: "Which day in \
{example_month} will you pay for the fridge?" Owner: "On the 12th." You call propose_planned_event with \
event_type "expense", date_from "{example_date}", amount_eur 2000, description "New fridge", then show the summary.
- Owner: "We will close for renovation in the autumn." You, without a tool: "From which day to which day will \
you be closed?"
- Owner: "A customer will pay a catering deposit next week." You, without a tool: "On which day, and how much?"

JOB 2 - EXPLAIN THE SITUATION
For questions such as "why?", "what happens?", "which option?", answer from the SITUATION below. It is calculated \
by the forecasting engine and already includes all saved plans.
- Quote numbers exactly as they appear in SITUATION or in a tool result. Never calculate or estimate a number.
- "status": {status_legend}.
- "options" are the only rescue options. Never invent another one. "engine_recommendation" is the option \
the engine prefers. If "options" is empty, there is nothing to choose.
- The owner approves or rejects an option with the buttons on the option cards, not in this chat.
- When asked why the balance drops, why there is a problem or what will happen, retell "engine_explanation" \
in your own simple words and keep every amount and date from it: today's balance, the lowest balance and its \
date, the closure dates, and each of the largest fixed payments with its amount.
- When asked about an option, give that option's figures: what it does, its "lowest_balance_with_this_option", \
its "result", its "interest_cost_eur" (the cost, not the shortfall) and its trade-off. For the option in \
"engine_recommendation" also use "engine_recommendation_reason".
- Tools you may use without asking for permission: list_planned_events (the plans already saved) and \
get_fixed_obligations (the regular fixed payments: who, how much, which day). Call the tool right away and answer \
with its result. For the forecast, the lowest balance and the options, SITUATION is the only source.
- SITUATION does not contain the amount of a single regular payment. The fixed payments named in \
"engine_explanation" are sums over the whole period until the lowest point, not monthly amounts. Whenever the \
owner asks how much a regular payment is (rent, a salary, a tax) or when it is due, you MUST call \
get_fixed_obligations first and answer only from its result.
- You cannot look up single past bank transactions or past totals (for example last month's revenue) here. If \
the owner asks for them, say that this is not available in the chat. Do not guess and do not point to buttons or \
screens.
- "recent_decisions" lists the rescue options the owner approved or rejected most recently (newest first), with \
what each one does and its interest cost. Use it when the owner asks what was approved or decided. If it is empty, \
nothing has been decided yet.
- Answer in at most 5 short sentences and do not end an explanation with a question.

{removal_job}LIMITS
- You cannot move money. If the owner asks you to pay, transfer or send money now or today, to take a loan or to \
contact the bank, do not call any tool. Say clearly that you cannot execute payments and that you only plan and \
simulate, and offer to add it to the forecast as a planned expense instead.
{saved_plan_limit}- You never decide for the owner. Rescue options are not plans: you cannot approve, reject, start or save \
them, and you never ask whether to include an option in the forecast. If the owner asks you to approve or choose \
one, answer like this: "I can't approve options for you. To go ahead with an option, press Approve on its card."
- If a question is not about the pizzeria's money or plans, say briefly that you can only help with those.

SITUATION (JSON):
{situation}"""

_REMOVAL_JOB = """JOB 3 - REMOVE A SAVED PLAN
This job is only for a message in which the owner asks to remove, delete, cancel or forget a plan that is \
already saved. A message that describes something new that will happen is JOB 1, also when plans are saved.
"planned_events" in the SITUATION below lists every plan that is saved right now, each with its "id" and its \
"source". Find the plan the owner means in that list by its description, its date or its amount. Then exactly \
one of these cases applies:
- No saved plan matches (or the list is empty): say that there is no such plan and name the plans that are \
saved. Call no tool and do not point to any button.
- Several plans could be meant, or the owner says "my plans", "the plan" or "everything" while more than one \
plan is saved: name the saved plans and ask which one to remove. Call no tool.
- The plan has source "agent" (a credit line, a postponed payment): it belongs to a rescue option the owner \
approved. Call no tool. Say that it cannot be removed in the chat and that the owner can withdraw it with the \
"Withdraw option" button next to it in the "My plans" list.
- Exactly one plan with source "chat" is meant: {removal_rule}
You cannot change a saved plan. If the owner wants to change one, remove it as described here and then record \
the new plan with JOB 1.

"""
# The removal job, the plan ids and the removal tools are part of a turn only when the owner asks for a removal:
# with them in the prompt the small model handles new plans worse (wrong dates, tool calls written as text).
# Otherwise one of these two lines remains. While no plan from the chat is saved there is nothing to remove:
_NOTHING_REMOVABLE_LIMIT = (
    "- No plan from the chat is saved right now, so you cannot remove or change a saved plan in this message, and "
    "no tool can. If the owner asks to remove, delete, cancel or change a plan, do not call any tool: say that "
    "there is no such plan among the saved plans and name the plans in \"planned_events\" (or say that no plans "
    "are saved). A plan that belongs to an approved rescue option (a credit line, a postponed payment) is "
    "withdrawn by the owner with the \"Withdraw option\" button next to it in the \"My plans\" list.\n"
)
# While chat plans are saved, the model hands a removal request back to the backend with this word, and the
# backend asks again with the removal job (``llm_chat``). The owner never sees it.
REMOVAL_REQUEST_SIGNAL = "REMOVE_PLAN_REQUEST"
_REMOVAL_ON_REQUEST_LIMIT = (
    "- Removing a saved plan is handled in a separate step, not in this message. If the owner asks to remove, "
    "delete, cancel, drop or forget a plan that is already saved, or to change one, or says that something that "
    "is planned will not happen after all, do not call any tool and do not explain anything: reply with exactly "
    f"{REMOVAL_REQUEST_SIGNAL} and nothing else.\n"
)

_SAVE_ALLOWED_RULE = (
    "These checked plans have been shown to the owner and are waiting for a yes. They are NOT saved yet: "
    "{drafts}\n"
    "Only when the owner's latest message clearly says yes to one of them (for example \"yes\", \"include it\", "
    "\"go ahead\") call save_planned_event with exactly the values of that plan and with original_utterance set "
    "to the owner's own words that described the plan. Save only the plans the owner said yes to. If the owner "
    "changes any detail (a date, an amount), do not save: call propose_planned_event with the new values and ask "
    "again. If the owner says no or asks something else, do not save."
)
_SAVE_NOT_ALLOWED_RULE = (
    "Saving is not possible in this message, because your previous message did not contain a checked summary. "
    "Never say that a plan was saved, planned or included. If the owner says yes to a plan now, call "
    "propose_planned_event for that plan and ask once more."
)

_PROPOSE_REMOVAL_RULE = (
    "call propose_planned_event_removal with its id right away, in the same message, before you answer. This "
    "only checks the plan; nothing is removed. Never ask whether to remove a plan without calling it first. "
    "Then tell the owner which plan it is and ask whether to remove it. Stop and wait for the owner's answer, "
    "also when the owner said \"immediately\" or confirmed in advance."
)
_REMOVAL_ALLOWED_RULE = (
    "if it is not one of the plans waiting below, " + _PROPOSE_REMOVAL_RULE + "\n"
    "These saved plans have been shown to the owner for removal and are waiting for a yes. They are NOT removed "
    "yet: {removals}\n"
    "Only when the owner's latest message clearly says yes to removing one of them (for example \"yes\", "
    "\"remove it\", \"go ahead\") call remove_planned_event with the id of that plan. Remove only the plans the "
    "owner said yes to. If the owner says no or wants to keep the plan, call no tool and say that the plan stays "
    "in the forecast. If the owner asks something else, do not remove."
)
_REMOVAL_NOT_ALLOWED_RULE = (
    _PROPOSE_REMOVAL_RULE + "\n"
    "Removing itself is not possible in this message, because the owner has not yet been shown which plan would "
    "be removed. Never say that a plan was removed or deleted."
)

_UPDATE_COMMENT_TEMPLATE = f"""{_PERSONA}

{{what_happened}} and the forecasting engine has recalculated the forecast. Answer the owner in \
at most 3 short sentences:
1. Start with "Done" and say {{what_to_say}}.
2. Say what the forecast shows now, using the UPDATED FORECAST facts. Copy amounts and dates exactly (keep \
"below zero" when it is there); never calculate a number.
3. Only if rescue options are on the screen: say that the owner can approve or reject them there.
Do not ask a question and do not call tools."""

UPDATE_COMMENT_INSTRUCTIONS = _UPDATE_COMMENT_TEMPLATE.format(
    what_happened="The owner's plan has just been saved",
    what_to_say="which plan is now included in the forecast")
REMOVAL_COMMENT_INSTRUCTIONS = _UPDATE_COMMENT_TEMPLATE.format(
    what_happened="The owner's plan has just been removed from the saved plans",
    what_to_say="which plan was removed and is no longer in the forecast")
PLANS_CHANGED_COMMENT_INSTRUCTIONS = _UPDATE_COMMENT_TEMPLATE.format(
    what_happened="The owner's plans have just been changed (one saved, one removed)",
    what_to_say="which plan is now included in the forecast and which one was removed")


def update_comment_instructions(saved: bool, removed: bool) -> str:
    """Instructions for the comment on the recalculated forecast, matching what the turn really changed."""
    if saved and removed:
        return PLANS_CHANGED_COMMENT_INSTRUCTIONS
    return REMOVAL_COMMENT_INSTRUCTIONS if removed else UPDATE_COMMENT_INSTRUCTIONS


@dataclass(frozen=True)
class RemovalContext:
    """The owner asks to remove a plan in this turn, or was asked to confirm a removal. ``confirmable`` are the saved plans the owner has
    been shown for removal and may say yes to now; the removal itself is only described if there are any.
    ``saved_plans`` are all stored plans; the model picks the one the owner means from them by id."""

    confirmable: Sequence[EventOut] = ()
    saved_plans: Sequence[PlannedEvent] = ()


def chat_instructions(dashboard: Dashboard, confirmable: Sequence[EventDraft], removal: Optional[RemovalContext],
                      recent_decisions: Sequence[DecisionRecord], removal_on_request: bool = False) -> str:
    """``confirmable`` are the checked plans the owner may say yes to now; saving is only described if there are
    any. The job of removing a saved plan (and the plan ids it needs) is part of the prompt only with ``removal``.
    Without it, ``removal_on_request`` (a plan from the chat is saved) tells the model to hand a removal request
    back with ``REMOVAL_REQUEST_SIGNAL``; otherwise the prompt says that there is nothing to remove."""
    example_day = add_months(dashboard.as_of_date, _EXAMPLE_MONTHS_AHEAD).replace(day=_EXAMPLE_DAY)
    return _CHAT_INSTRUCTIONS.format(
        persona=_PERSONA,
        weekday=dashboard.as_of_date.strftime("%A"),
        today=dashboard.as_of_date.isoformat(),
        example_month=example_day.strftime("%B"),
        example_date=example_day.isoformat(),
        example_year_month=example_day.strftime("%Y-%m"),
        status_legend=_STATUS_LEGEND,
        save_rule=_save_rule(confirmable),
        job_count="two" if removal is None else "three",
        removal_job="" if removal is None else _REMOVAL_JOB.format(removal_rule=_removal_rule(removal.confirmable)),
        saved_plan_limit=("" if removal is not None
                          else _REMOVAL_ON_REQUEST_LIMIT if removal_on_request else _NOTHING_REMOVABLE_LIMIT),
        situation=situation_json(dashboard, recent_decisions, None if removal is None else removal.saved_plans),
    )


def _save_rule(confirmable: Sequence[EventDraft]) -> str:
    if not confirmable:
        return _SAVE_NOT_ALLOWED_RULE
    drafts = json.dumps([draft_json(draft) for draft in confirmable], ensure_ascii=False)
    return _SAVE_ALLOWED_RULE.format(drafts=drafts)


def _removal_rule(removable: Sequence[EventOut]) -> str:
    if not removable:
        return _REMOVAL_NOT_ALLOWED_RULE
    return _REMOVAL_ALLOWED_RULE.format(removals=_removals_json(removable))


def _removals_json(removable: Sequence[EventOut]) -> str:
    return json.dumps([{"id": event.id, **draft_json(event)} for event in removable], ensure_ascii=False)


def recommendation_input(dossier: dict) -> str:
    return f"DOSSIER:\n{json.dumps(dossier, ensure_ascii=False)}"


def update_comment_input(dashboard: Dashboard) -> str:
    risk = dashboard.risk
    option_ids = ", ".join(option.id for option in dashboard.options)
    return "\n".join([
        "[Automatic message from the forecasting engine, not written by the owner]",
        "UPDATED FORECAST:",
        f"- status: {risk.status.value} ({_STATUS_IN_WORDS[risk.status]})",
        f"- in one sentence: {dashboard.headline}",
        f"- lowest balance: {_balance_in_words(risk.min_balance_eur)} on {format_day(risk.min_balance_date)}",
        f"- rescue options on the screen: {option_ids or 'none, nothing to decide'}",
    ])


def describe_draft(draft: EventDraft | EventOut) -> str:
    """One plan from the chat in plain words, for confirmation questions written by code."""
    if draft.amount_eur is None:
        return f"{draft.description}: closed from {_full_day(draft.date_from)} to {_full_day(draft.date_to)}"
    return (f"{draft.description}: {draft.event_type.value} of {format_eur(draft.amount_eur)} on "
            f"{_full_day(draft.date_from)}")


def _full_day(day: date) -> str:
    return f"{format_day(day)} {day.year}"


def _balance_in_words(balance_eur: Decimal) -> str:
    """A small model tends to drop a leading minus sign, so a negative balance is spelled out."""
    return f"{format_eur(-balance_eur)} below zero" if balance_eur < 0 else format_eur(balance_eur)


def carried_context_input(message: str, last_reply: str, pending: Sequence[EventDraft],
                          removable: Sequence[EventOut]) -> str:
    """First message of a fresh thread that replaces a conversation too long for the model's context window."""
    lines = ["[Summary of the earlier conversation]", f"Your last message to the owner was: {last_reply}"]
    if pending:
        drafts = json.dumps([draft_json(draft) for draft in pending], ensure_ascii=False)
        lines.append(f"Checked plans the owner has seen and has not yet confirmed: {drafts}")
    if removable:
        lines.append("Saved plans the owner was asked whether to remove and has not yet answered about: "
                     f"{_removals_json(removable)}")
    lines += ["[The owner's new message]", message]
    return "\n".join(lines)


def recommendation_dossier(dashboard: Dashboard) -> dict:
    """The §11 dossier from the core, relabelled so that a small model cannot confuse an option's cost with
    its remaining shortfall, plus the opportunities found by code (the surplus insight when all is GREEN)."""
    dossier = build_dossier(dashboard)
    return {
        "today": dossier["today"],
        "balance_today": dossier["balance_today"],
        "forecast_horizon_days": dossier["horizon_days"],
        "safety_buffer_eur": dossier["buffer_eur"],
        "status": dossier["status"],
        "headline": dashboard.headline,
        "lowest_balance": dossier["min_balance"],
        "lowest_balance_in_words": _balance_in_words(dashboard.risk.min_balance_eur),
        "lowest_balance_date": dossier["min_balance_date"],
        "credit_line_eur": dossier["credit_limit_eur"],
        "planned_events": dossier["events"],
        "average_per_open_day": dossier["daily_averages"],
        FIXED_COSTS_KEY: _fixed_costs_view(dossier["fixed_costs_in_risk_window"]),
        "balance_week_by_week": dossier["weekly_balance"],
        "options": [_option_view(view, option) for view, option in zip(dossier["options"], dashboard.options)],
        "engine_recommendation": dossier["code_recommendation"],
        "opportunities": [{"title": insight.title, "detail": insight.detail,
                           "amount_eur": money_json(insight.amount_eur)} for insight in dashboard.insights],
    }


def _fixed_costs_view(costs: Sequence[dict]) -> list[dict] | str:
    """The engine lists fixed costs only for a period under pressure. A small model reads an empty list as
    "there are no fixed costs", so the safe case is spelled out instead."""
    if not costs:
        return _NO_PRESSURE_NOTE
    return [{"label": cost["label"], "sum_of_all_payments_until_lowest_point_eur": cost["total"],
             "can_be_postponed": cost["deferrable"]} for cost in costs]


def _option_view(view: dict, option: RescueOption) -> dict:
    return {
        "id": view["id"],
        "title": view["title"],
        "what_it_does": view["description"],
        "result": view["new_status"],
        "result_in_words": _result_in_words(option),
        "lowest_balance_with_this_option": view["new_min_balance"],
        "lowest_balance_in_words": _balance_in_words(option.new_min_balance_eur),
        "lowest_balance_date": view["new_min_balance_date"],
        "interest_cost_eur": view["cost_eur"],
        "credit_line_eur": view["credit_limit_eur"],
        "trade_off": view["non_financial_cost"],
        "rejected_by_owner": view["rejected"],
    }


def _result_in_words(option: RescueOption) -> str:
    if option.new_status is RiskStatus.RED:
        return "still short of money"
    if option.new_status is RiskStatus.YELLOW:
        return "not short of money, but below the safety buffer"
    if option.new_min_balance_eur < 0:
        return "safe: the balance goes below zero but stays within the credit line"
    return "safe"


def situation_json(dashboard: Dashboard, recent_decisions: Sequence[DecisionRecord],
                   removable_from: Optional[Sequence[PlannedEvent]] = None) -> str:
    """What the chat model knows: the dossier, the engine's own explanation texts and the latest decisions.
    ``removable_from`` (all stored plans) replaces "planned_events" by what removing a saved plan needs: every
    plan with its id and its source, also those outside the forecast horizon."""
    engine_texts = build_fallback_recommendation(dashboard)
    situation = {**recommendation_dossier(dashboard), "engine_explanation": engine_texts.explanation,
                 "engine_recommendation_reason": engine_texts.rationale,
                 "recent_decisions": [_decision_view(decision) for decision in recent_decisions]}
    if removable_from is not None:
        situation["planned_events"] = [_saved_plan_view(event) for event in removable_from]
    del situation[FIXED_COSTS_KEY]  # per-period sums the chat model mistook for monthly amounts
    return json.dumps(situation, ensure_ascii=False)


def _saved_plan_view(event: PlannedEvent) -> dict:
    """A saved plan with the id and the source the removal tools need."""
    return {"id": event.id, "source": event.source.value, "type": event.event_type.value,
            "from": _iso(event.date_from), "to": _iso(event.date_to), "amount_eur": money_json(event.amount_eur),
            "description": event.description}


def _decision_view(decision: DecisionRecord) -> dict:
    return {"option": decision.option_id, "title": decision.option_title, "decision": decision.status.value,
            "decided_for_date": decision.as_of_date.isoformat(), "what_it_does": decision.option_description,
            "interest_cost_eur": money_json(decision.snapshot.cost_eur)}


def draft_json(draft: EventDraft | EventOut) -> dict:
    return {"event_type": draft.event_type.value, "date_from": _iso(draft.date_from), "date_to": _iso(draft.date_to),
            "amount_eur": money_json(draft.amount_eur), "description": draft.description}


def _iso(day: date) -> str:
    return day.isoformat()
