"""POST /advice/recommendation: the model words the advisors' proposals, may add tips, and sums everything up.

One model call per domain agent that has facts plus one for the coordinator, all in parallel and without tools.
Each call sees only its own small dossier, is cached by the hash of that dossier, is validated on its own and
falls back on its own to the texts written by code, so one bad answer never costs the other agents theirs.
The model never decides what is on offer: proposals, amounts and dates always come from the engine.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from advisors.grounding import ungrounded_amounts, ungrounded_dates, ungrounded_percentages, without_dates
from advisors.model import CASH_BLOCKING_ACTIONS, Advice, Agent, Proposal
from advisors.prompts import (
    COORDINATOR_INSTRUCTIONS,
    MAX_TIPS,
    agent_dossier,
    agent_instructions,
    coordinator_dossier,
)
from config import ADVICE_CACHE_SIZE, LLM_ADVICE_MAX_OUTPUT_TOKENS, LLM_ADVICE_SUMMARY_MAX_OUTPUT_TOKENS
from llm import JsonSchemaFormat, LlmClient, LlmError, LlmRequest, LlmResponse
from llm_prompts import recommendation_input
from llm_recommendation import (
    SOURCE_FALLBACK,
    SOURCE_LLM,
    InvalidRecommendationError,
    dossier_key,
    extract_json_object,
    shortfalls_quoted_as_positive,
)
from texts import format_eur

logger = logging.getLogger(__name__)

# The coordinator never offers conflicting proposals, so a summary that makes the owner choose between them
# contradicts the engine.
_PRESENTED_AS_ALTERNATIVES = re.compile(r"\b(?:either|or|alternatively|instead|choose between)\b", re.IGNORECASE)
# Words at least one of which a tip must use to count as advice from that agent's area.
_DOMAIN_WORDS: Mapping[Agent, tuple[str, ...]] = {
    Agent.TAX: ("tax", "prepayment"),
    Agent.FX: ("chf", "franc", "zurich", "exchange rate", "transfer"),
    Agent.INVESTMENT: ("deposit", "interest"),
}

NO_RECOMMENDATIONS_HEADLINE = "No further recommendations right now."
SUMMARY_SCHEMA_NAME = "advice_summary"

_TITLE = Field(min_length=1, max_length=120)
_BODY = Field(min_length=1, max_length=600)
_SUMMARY_TEXT = Field(min_length=1, max_length=800)
_ANSWER_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)

_AGENT_IN_WORDS: Mapping[Agent, str] = {Agent.TAX: "tax", Agent.FX: "currency", Agent.INVESTMENT: "investment"}

_Answer = TypeVar("_Answer")
_Document = TypeVar("_Document", bound=BaseModel)


@dataclass(frozen=True)
class Tip:
    """Free advice from the model. Not selectable and without any effect on the forecast."""

    title: str
    text: str


@dataclass(frozen=True)
class AdviceSummary:
    source: str
    fallback_reason: Optional[str]
    headline: str
    text: str


@dataclass(frozen=True)
class AgentRecommendation:
    agent: Agent
    source: str
    fallback_reason: Optional[str]
    tips: tuple[Tip, ...]


@dataclass(frozen=True)
class ProposalText:
    title: str
    explanation: str
    recommended: bool


@dataclass(frozen=True)
class AdviceRecommendation:
    summary: AdviceSummary
    agents: tuple[AgentRecommendation, ...]  # agents that have facts, ordered tax, fx, investment
    proposals: Mapping[str, ProposalText]  # one entry per proposal on offer, in the order of the advice


@dataclass(frozen=True)
class AgentOutcome:
    """What one agent's call produced: how it went, its tips and the texts of its proposals."""

    recommendation: AgentRecommendation
    texts: Mapping[str, ProposalText]


class LlmTip(BaseModel):
    model_config = _ANSWER_CONFIG

    title: str = _TITLE
    text: str = _BODY


class LlmProposalRecommendation(BaseModel):
    model_config = _ANSWER_CONFIG

    proposal_id: str
    recommended: StrictBool
    title: str = _TITLE
    explanation: str = _BODY


class LlmAgentAnswer(BaseModel):
    """The JSON document a domain agent's call must return."""

    model_config = _ANSWER_CONFIG

    recommendations: list[LlmProposalRecommendation]
    tips: list[LlmTip] = Field(max_length=MAX_TIPS)


class LlmSummary(BaseModel):
    """The JSON document the coordinator's call must return."""

    model_config = _ANSWER_CONFIG

    headline: str = _TITLE
    text: str = _SUMMARY_TEXT


class AdviceCache:
    """Small thread-safe LRU keyed by the hash of one call's dossier. It holds model answers only."""

    def __init__(self, max_entries: int = ADVICE_CACHE_SIZE):
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, answer: Any) -> None:
        with self._lock:
            self._entries[key] = answer
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class AdviceRecommendationService:
    def __init__(self, client: LlmClient, cache: AdviceCache):
        self._client = client
        self._cache = cache

    def recommend(self, advice: Advice) -> AdviceRecommendation:
        """Never raises for model problems: each part that failed carries its own fallback reason."""
        agents = [agent for agent in Agent if _has_something_to_say(advice, agent)]
        if not agents and not advice.proposals:
            return AdviceRecommendation(fallback_summary(advice, None), (), {})
        with ThreadPoolExecutor(max_workers=len(agents) + 1) as pool:
            summary = pool.submit(self._summary, advice)
            outcomes = [pool.submit(self._agent_outcome, advice, agent) for agent in agents]
        texts = {proposal.id: _code_text(proposal, recommended=True) for proposal in advice.proposals}
        for outcome in outcomes:
            texts.update(outcome.result().texts)
        return AdviceRecommendation(
            summary=summary.result(),
            agents=tuple(outcome.result().recommendation for outcome in outcomes),
            proposals=texts,
        )

    def _summary(self, advice: Advice) -> AdviceSummary:
        if not advice.proposals:  # nothing to sum up: the code text says so without a model call
            return fallback_summary(advice, None)
        dossier = coordinator_dossier(advice)
        return self._cached_or_asked(
            dossier, "summary",
            ask=lambda: self._ask_coordinator(dossier),
            fallback=lambda reason: fallback_summary(advice, reason))

    def _agent_outcome(self, advice: Advice, agent: Agent) -> AgentOutcome:
        dossier = agent_dossier(advice, agent)
        proposals = advice.of_agent(agent)
        return self._cached_or_asked(
            dossier, agent.value,
            ask=lambda: self._ask_agent(agent, proposals, dossier),
            fallback=lambda reason: fallback_outcome(agent, proposals, reason))

    def _cached_or_asked(self, dossier: dict, part: str, ask: Callable[[], _Answer],
                         fallback: Callable[[str], _Answer]) -> _Answer:
        key = dossier_key(dossier)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            answer = ask()
        except (LlmError, InvalidRecommendationError) as exc:
            logger.warning("Advice (%s) falls back to code texts (%s): %s", part, exc.reason, exc)
            return fallback(exc.reason)
        self._cache.put(key, answer)
        return answer

    def _ask_agent(self, agent: Agent, proposals: Sequence[Proposal], dossier: dict) -> AgentOutcome:
        proposal_ids = [proposal.id for proposal in proposals]
        response = self._client.respond(LlmRequest(
            instructions=agent_instructions(agent),
            input=recommendation_input(dossier),
            max_output_tokens=LLM_ADVICE_MAX_OUTPUT_TOKENS,
            json_schema=JsonSchemaFormat(agent_schema_name(agent), agent_answer_schema(proposal_ids)),
        ))
        answer = validate_agent_answer(response, proposal_ids, dossier)
        tips = [tip for tip in answer.tips if _in_domain(agent, f"{tip.title} {tip.text}")]
        reworded = {item.proposal_id: ProposalText(item.title, item.explanation, item.recommended)
                    for item in answer.recommendations}
        return AgentOutcome(
            recommendation=AgentRecommendation(agent, SOURCE_LLM, None,
                                               tuple(Tip(tip.title, tip.text) for tip in tips)),
            texts={proposal.id: reworded.get(proposal.id, _code_text(proposal, recommended=False))
                   for proposal in proposals},
        )

    def _ask_coordinator(self, dossier: dict) -> AdviceSummary:
        response = self._client.respond(LlmRequest(
            instructions=COORDINATOR_INSTRUCTIONS,
            input=recommendation_input(dossier),
            max_output_tokens=LLM_ADVICE_SUMMARY_MAX_OUTPUT_TOKENS,
            json_schema=JsonSchemaFormat(SUMMARY_SCHEMA_NAME, summary_schema()),
        ))
        summary = validate_summary(response, dossier, proposal_count=len(dossier["proposals"]))
        return AdviceSummary(SOURCE_LLM, None, summary.headline, summary.text)


def agent_schema_name(agent: Agent) -> str:
    return f"advice_{agent.value}"


def agent_answer_schema(proposal_ids: Sequence[str]) -> dict:
    """JSON schema for guided decoding: a recommendation can only name a proposal that is really on offer, and
    without proposals the agent can only give tips."""
    text = {"type": "string"}
    recommendations: dict[str, Any] = {"type": "array", "maxItems": len(proposal_ids)}
    if proposal_ids:
        recommendations["items"] = _object_schema({
            "proposal_id": {"type": "string", "enum": list(proposal_ids)},
            "recommended": {"type": "boolean"},
            "title": text,
            "explanation": text,
        })
    tips = {"type": "array", "maxItems": MAX_TIPS, "items": _object_schema({"title": text, "text": text})}
    return _object_schema({"recommendations": recommendations, "tips": tips})


def summary_schema() -> dict:
    text = {"type": "string"}
    return _object_schema({"headline": text, "text": text})


def validate_agent_answer(response: LlmResponse, proposal_ids: Sequence[str], dossier: dict) -> LlmAgentAnswer:
    """Strict checks on one agent's answer; any failure means that agent shows the texts written by code."""
    answer = _parsed(response, LlmAgentAnswer)
    named = [item.proposal_id for item in answer.recommendations]
    unknown = [proposal_id for proposal_id in named if proposal_id not in proposal_ids]
    if unknown:
        raise InvalidRecommendationError(
            "llm_unknown_option", f"The model named proposals {unknown}; on offer: {list(proposal_ids)}.")
    if len(set(named)) != len(named):
        raise InvalidRecommendationError(
            "llm_duplicate_proposal", f"The model named a proposal more than once: {named}.")
    texts = [text for item in answer.recommendations for text in (item.title, item.explanation)]
    texts += [text for tip in answer.tips for text in (tip.title, tip.text)]
    _check_figures(" ".join(texts), dossier)
    return answer


def validate_summary(response: LlmResponse, dossier: dict, proposal_count: int) -> LlmSummary:
    """Strict checks on the coordinator's answer; any failure means the summary written by code is shown."""
    summary = _parsed(response, LlmSummary)
    text = f"{summary.headline} {summary.text}"
    _check_figures(text, dossier)
    if proposal_count > 1 and _PRESENTED_AS_ALTERNATIVES.search(text):
        raise InvalidRecommendationError(
            "llm_contradicts_engine", "The model presents proposals as alternatives; they can all be approved.")
    return summary


def _has_something_to_say(advice: Advice, agent: Agent) -> bool:
    """An agent with facts is asked - except one without a proposal while a cash shortage is open: its
    candidates were all held back, and tips about them would only distract from the shortage."""
    if agent not in advice.facts:
        return False
    return bool(advice.of_agent(agent)) or not advice.liquidity_first


def _in_domain(agent: Agent, text: str) -> bool:
    """A tip belongs to the agent's narrow area only when it talks about it in so many words."""
    lowered = text.lower()
    return any(word in lowered for word in _DOMAIN_WORDS[agent])


def fallback_outcome(agent: Agent, proposals: Sequence[Proposal], reason: str) -> AgentOutcome:
    """The agent's proposals exactly as the engine worded them, all on offer as recommended, and no tips."""
    return AgentOutcome(
        recommendation=AgentRecommendation(agent, SOURCE_FALLBACK, reason, ()),
        texts={proposal.id: _code_text(proposal, recommended=True) for proposal in proposals},
    )


def fallback_summary(advice: Advice, reason: Optional[str]) -> AdviceSummary:
    """The summary written by code from the proposals on offer. ``reason`` is None when no model was asked."""
    if not advice.proposals:
        return AdviceSummary(SOURCE_FALLBACK, reason, NO_RECOMMENDATIONS_HEADLINE, _nothing_to_do_text(advice))
    count = len(advice.proposals)
    advisors = _in_words([_AGENT_IN_WORDS[agent] for agent in Agent if advice.of_agent(agent)])
    plural = "s" if count != 1 else ""
    sentences = [f"Your {advisors} advice for today: {count} step{plural} you can approve.", _cash_sentence(advice)]
    if count > 1:
        sentences.append("You can combine them freely.")
    return AdviceSummary(SOURCE_FALLBACK, reason, f"{count} recommendation{plural} for you today", " ".join(sentences))


def _nothing_to_do_text(advice: Advice) -> str:
    if advice.liquidity_first:
        return "Solving the cash shortage comes first, so nothing that ties up cash is proposed today."
    return "Your advisors found nothing that needs your decision today."


def _cash_sentence(advice: Advice) -> str:
    if advice.liquidity_first:
        return "Solving the cash shortage comes first, so none of this ties up cash."
    tied_up = sum((proposal.amount_eur for proposal in advice.proposals
                   if proposal.action_type in CASH_BLOCKING_ACTIONS and proposal.amount_eur is not None), Decimal("0"))
    if tied_up == 0:
        return "None of this takes cash out of your account."
    return f"Approving everything would tie up {format_eur(tied_up)} of your spare cash."


def _in_words(names: Sequence[str]) -> str:
    """``tax``; ``tax and currency``; ``tax, currency and investment``."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _code_text(proposal: Proposal, recommended: bool) -> ProposalText:
    return ProposalText(proposal.title, proposal.description, recommended)


def _parsed(response: LlmResponse, document_type: type[_Document]) -> _Document:
    if response.truncated:
        raise InvalidRecommendationError("llm_truncated_output", "The model's answer was cut off.")
    document = extract_json_object(response.text)
    if document is None:
        raise InvalidRecommendationError("llm_invalid_json", "The model's answer contains no JSON object.")
    try:
        return document_type.model_validate(document)
    except ValidationError as exc:
        raise InvalidRecommendationError("llm_invalid_json", f"The model's JSON has the wrong shape: {exc}") from exc


def _check_figures(text: str, dossier: dict) -> None:
    """Every date, percentage and amount in the model's text must be in the dossier of its call."""
    amounts_text = without_dates(text)
    invented = [*ungrounded_dates(text, dossier), *ungrounded_percentages(text, dossier),
                *ungrounded_amounts(amounts_text, dossier)]
    if invented:
        raise InvalidRecommendationError(
            "llm_ungrounded_numbers", f"The model quoted figures that are not in the dossier: {invented}.")
    unsigned = shortfalls_quoted_as_positive(amounts_text, dossier)
    if unsigned:
        raise InvalidRecommendationError(
            "llm_sign_dropped", f"The model quoted negative balances without saying they are negative: {unsigned}.")


def _object_schema(properties: dict[str, Any]) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
