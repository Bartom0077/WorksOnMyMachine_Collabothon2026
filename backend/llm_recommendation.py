"""POST /recommendation: the model explains the forecast and picks ONE of the options computed by code.

The model gets no tools here. Everything it may quote is in the dossier, which the backend computes from the
same database the MCP server reads; the MCP KPI snapshot carries its own 30-day forecast that ignores planned
events and would contradict the dossier. Whatever goes wrong, the caller gets the deterministic texts instead.

The model writes only what code can check: the explanation, its choice and the reason for it. The headline and
the one-line comment per option always come from the engine, so the most prominent sentence and every option's
stated outcome can never contradict the figures next to them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import LLM_RECOMMENDATION_MAX_OUTPUT_TOKENS, RECOMMENDATION_CACHE_SIZE
from domain import Dashboard, FallbackRecommendation
from llm import JsonSchemaFormat, LlmClient, LlmError, LlmRequest, LlmResponse
from llm_prompts import (
    DOSSIER_MONEY_KEYS,
    FIXED_COSTS_KEY,
    RECOMMENDATION_INSTRUCTIONS,
    recommendation_dossier,
    recommendation_input,
)
from service import build_fallback_recommendation

logger = logging.getLogger(__name__)

SOURCE_LLM = "llm"
SOURCE_FALLBACK = "fallback"

_TEXT = Field(min_length=1, max_length=2000)
_THOUSAND = Decimal("1000")
_WHOLE_EURO = Decimal("1")
# A number in running text with its optional sign, currency marker and "k" suffix.
_AMOUNT_TOKEN = re.compile(
    r"(?P<minus>[-−]\s?)?(?P<lead>(?:€|EUR|\$)\s?)?(?P<inner_minus>[-−]\s?)?"
    r"(?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<thousands>k\b)?(?P<trail>\s?(?:€|EUR\b|euros?\b))?(?P<percent>\s?%)?",
    re.IGNORECASE)
# Words that make the amount right next to them a shortfall ("€300 short", "a shortfall of €300").
_SHORT_BEFORE = re.compile(
    r"(?:shortfall|short|overdrawn|deficit|gap|minus|in the red|below zero|negative balance)"
    r"(?:\s+(?:of|by|is|at))?(?:\s+(?:about|around|roughly|nearly|almost))?\s*$", re.IGNORECASE)
_SHORT_AFTER = re.compile(r"\s*(?:short\b|below zero|overdrawn|in the red)", re.IGNORECASE)
_NOT_POSTPONABLE = re.compile(
    r"(?:cannot|can't|can not|could not|couldn't|must not|not possible to|impossible to)\s+(?:be\s+)?"
    r"(?:postponed?|deferred|defer|delayed|delay|moved|move)|not postponable|non-postponable", re.IGNORECASE)
_ABOVE_ZERO_CLAIMS = ("above zero", "positive balance", "stays positive", "stay positive", "remains positive",
                      "out of the red", "in the black", "no shortfall")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MIN_KEYWORD_CHARS = 4


@dataclass(frozen=True)
class Recommendation:
    source: str
    fallback_reason: Optional[str]
    headline: str
    explanation: str
    recommended_option_id: Optional[str]
    rationale: str
    option_comments: dict[str, str]


class LlmRecommendation(BaseModel):
    """The JSON document the model must return."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    explanation: str = _TEXT
    recommended_option_id: Optional[str]
    rationale: str = _TEXT


class InvalidRecommendationError(Exception):
    """The model answered, but not with a recommendation the backend can show. ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


class RecommendationCache:
    """Small thread-safe LRU keyed by the dossier hash: reloading the same situation is instant and stable."""

    def __init__(self, max_entries: int = RECOMMENDATION_CACHE_SIZE):
        self._entries: OrderedDict[str, Recommendation] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Recommendation]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, recommendation: Recommendation) -> None:
        with self._lock:
            self._entries[key] = recommendation
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class RecommendationService:
    def __init__(self, client: LlmClient, cache: RecommendationCache):
        self._client = client
        self._cache = cache

    def recommend(self, dashboard: Dashboard) -> Recommendation:
        """Never raises for model problems: the fallback carries the reason. Only LLM answers are cached."""
        dossier = recommendation_dossier(dashboard)
        key = dossier_key(dossier)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fallback = build_fallback_recommendation(dashboard)
        try:
            recommendation = self._ask_model(dashboard, dossier, fallback)
        except LlmError as exc:
            logger.warning("Recommendation falls back to code texts (%s): %s", exc.reason, exc)
            return from_fallback(fallback, exc.reason)
        except InvalidRecommendationError as exc:
            logger.warning("Recommendation falls back to code texts (%s): %s", exc.reason, exc)
            return from_fallback(fallback, exc.reason)
        self._cache.put(key, recommendation)
        return recommendation

    def _ask_model(self, dashboard: Dashboard, dossier: dict, fallback: FallbackRecommendation) -> Recommendation:
        allowed_ids = selectable_option_ids(dashboard)
        response = self._client.respond(LlmRequest(
            instructions=RECOMMENDATION_INSTRUCTIONS,
            input=recommendation_input(dossier),
            max_output_tokens=LLM_RECOMMENDATION_MAX_OUTPUT_TOKENS,
            json_schema=JsonSchemaFormat("recommendation", recommendation_schema(allowed_ids)),
        ))
        parsed = validate_recommendation(response, allowed_ids, dossier)
        return Recommendation(
            source=SOURCE_LLM,
            fallback_reason=None,
            headline=fallback.headline,
            explanation=parsed.explanation,
            recommended_option_id=parsed.recommended_option_id,
            rationale=parsed.rationale,
            option_comments=dict(fallback.option_comments),
        )


def from_fallback(fallback: FallbackRecommendation, reason: str) -> Recommendation:
    return Recommendation(
        source=SOURCE_FALLBACK,
        fallback_reason=reason,
        headline=fallback.headline,
        explanation=fallback.explanation,
        recommended_option_id=fallback.recommended_option_id,
        rationale=fallback.rationale,
        option_comments=dict(fallback.option_comments),
    )


def dossier_key(dossier: dict) -> str:
    canonical = json.dumps(dossier, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def selectable_option_ids(dashboard: Dashboard) -> list[str]:
    """Options the model may recommend: everything on offer that the owner has not already rejected."""
    return [option.id for option in dashboard.options if not option.rejected]


def recommendation_schema(allowed_ids: list[str]) -> dict:
    """JSON schema for guided decoding: the option id can only be one that is really on offer."""
    option_id: dict[str, Any] = {"type": "string", "enum": allowed_ids} if allowed_ids else {"type": "null"}
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {"explanation": text, "recommended_option_id": option_id, "rationale": text},
        "required": ["explanation", "recommended_option_id", "rationale"],
        "additionalProperties": False,
    }


def validate_recommendation(response: LlmResponse, allowed_ids: list[str], dossier: dict) -> LlmRecommendation:
    """Strict checks on the model's answer; any failure means the deterministic fallback is used."""
    if response.truncated:
        raise InvalidRecommendationError("llm_truncated_output", "The model's answer was cut off.")
    document = extract_json_object(response.text)
    if document is None:
        raise InvalidRecommendationError("llm_invalid_json", "The model's answer contains no JSON object.")
    try:
        parsed = LlmRecommendation.model_validate(document)
    except ValidationError as exc:
        raise InvalidRecommendationError("llm_invalid_json", f"The model's JSON has the wrong shape: {exc}") from exc
    if parsed.recommended_option_id not in (allowed_ids or [None]):
        raise InvalidRecommendationError(
            "llm_unknown_option", f"The model chose {parsed.recommended_option_id!r}; on offer: {allowed_ids}.")
    text = f"{parsed.explanation} {parsed.rationale}"
    invented = ungrounded_amounts(text, dossier)
    if invented:
        raise InvalidRecommendationError(
            "llm_ungrounded_numbers", f"The model quoted amounts that are not in the dossier: {invented}.")
    unsigned = shortfalls_quoted_as_positive(text, dossier)
    if unsigned:
        raise InvalidRecommendationError(
            "llm_sign_dropped", f"The model quoted negative balances without saying they are negative: {unsigned}.")
    contradiction = contradiction_with_engine(parsed, dossier)
    if contradiction:
        raise InvalidRecommendationError("llm_contradicts_engine", contradiction)
    return parsed


def extract_json_object(text: str) -> Optional[dict]:
    """The first JSON object in the text, tolerating code fences and prose around it."""
    decoder = json.JSONDecoder()
    for start in (match.start() for match in re.finditer(r"\{", text)):
        try:
            document, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            return document
    return None


@dataclass(frozen=True)
class QuotedAmount:
    raw: str
    value: Decimal  # signed: negative when written with a minus or described as a shortfall
    is_money: bool  # carries a currency marker, a thousands separator or decimals (not "7 weeks", "9%")


def ungrounded_amounts(text: str, dossier: dict) -> list[str]:
    """Money amounts in the model's text that do not appear in the dossier (rounding of a real figure is fine)."""
    known = dossier_amounts(dossier)
    return [quoted.raw for quoted in quoted_amounts(text)
            if quoted.is_money and not any(_matches(quoted.value, value) for value in known)]


def shortfalls_quoted_as_positive(text: str, dossier: dict) -> list[str]:
    """Amounts that exist in the dossier only as negative figures but are written as if they were positive."""
    known = dossier_amounts(dossier)
    dropped = []
    for quoted in quoted_amounts(text):
        matching = [value for value in known if _matches(quoted.value, value)]
        if quoted.is_money and quoted.value > 0 and matching and all(value < 0 for value in matching):
            dropped.append(quoted.raw)
    return dropped


def contradiction_with_engine(parsed: LlmRecommendation, dossier: dict) -> Optional[str]:
    """A statement that contradicts a non-numeric fact of the dossier, or None. Deliberately narrow: it covers
    the two kinds of false statement seen from the model (a postponable cost called fixed for good, an option
    said to keep the balance above zero while its lowest balance is negative)."""
    text = f"{parsed.explanation} {parsed.rationale}"
    for label in _postponable_cost_labels(dossier):
        keywords = _keywords(label)
        for sentence in _SENTENCE_END.split(text):
            if _NOT_POSTPONABLE.search(sentence) and any(keyword in sentence.lower() for keyword in keywords):
                return f"The model says that \"{label}\" cannot be postponed, but the engine marks it postponable."
    option = _dossier_option(dossier, parsed.recommended_option_id)
    if option is None:
        return None
    rationale = parsed.rationale.lower()
    if option["lowest_balance_with_this_option"] < 0 and any(claim in rationale for claim in _ABOVE_ZERO_CLAIMS):
        return f"The model says option {option['id']} keeps the balance above zero; its lowest balance is negative."
    if option["result"] != "GREEN" and "green" in rationale:
        return f"The model calls option {option['id']} green; the engine's result is {option['result']}."
    return None


def dossier_amounts(dossier: dict) -> list[Decimal]:
    """Every amount of money in the dossier, signed: the money-typed numbers and the amounts in its texts.
    Dates, ids, day counts and other numbers are not money and can never ground a quoted amount."""
    return sorted(set(_money_values(dossier, key=None)))


def quoted_amounts(text: str) -> Iterator[QuotedAmount]:
    for match in _AMOUNT_TOKEN.finditer(text):
        if match.group("percent"):
            continue
        digits = match.group("digits")
        amount = Decimal(digits.replace(",", ""))
        if match.group("thousands"):
            amount *= _THOUSAND
        has_currency = bool(match.group("lead") or match.group("trail"))
        negative = bool(match.group("minus") or match.group("inner_minus")) or _described_as_shortfall(text, match)
        yield QuotedAmount(match.group(0).strip(), -amount if negative else amount,
                           is_money=has_currency or "," in digits or "." in digits)


def _described_as_shortfall(text: str, match: re.Match) -> bool:
    return bool(_SHORT_BEFORE.search(text[:match.start()]) or _SHORT_AFTER.match(text[match.end():]))


def _money_values(node: Any, key: Optional[str]) -> Iterator[Decimal]:
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _money_values(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _money_values(value, key)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        if key in DOSSIER_MONEY_KEYS:
            yield Decimal(str(node))
    elif isinstance(node, str):
        yield from (quoted.value for quoted in quoted_amounts(node) if quoted.is_money)


def _matches(quoted: Decimal, known: Decimal) -> bool:
    """The same figure, cents dropped, or rounded to tens, hundreds or thousands - but never by more than its
    second significant digit (22,098 may become 22,000; 2,025 may not become 2,000). The sign is ignored here."""
    quoted, known = abs(quoted), abs(known)
    if abs(quoted - known) < _WHOLE_EURO:
        return True
    for step in (Decimal("1000"), Decimal("100"), Decimal("10")):
        if quoted % step == 0 and quoted != 0:
            return known >= step * 10 and abs(quoted - known) <= step / 2
    return False


def _postponable_cost_labels(dossier: dict) -> Sequence[str]:
    costs = dossier.get(FIXED_COSTS_KEY)
    if not isinstance(costs, list):
        return ()
    return [cost["label"] for cost in costs if cost["can_be_postponed"]]


def _keywords(label: str) -> list[str]:
    return [word for word in re.findall(r"[a-z]+", label.lower()) if len(word) >= _MIN_KEYWORD_CHARS]


def _dossier_option(dossier: dict, option_id: Optional[str]) -> Optional[dict]:
    return next((option for option in dossier.get("options", []) if option["id"] == option_id), None)
