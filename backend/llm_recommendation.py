"""POST /recommendation: the model explains the forecast and picks ONE of the options computed by code.

The model gets no tools here. Everything it may quote is in the dossier, which the backend computes from the
same database the MCP server reads; the MCP KPI snapshot carries its own 30-day forecast that ignores planned
events and would contradict the dossier. Whatever goes wrong, the caller gets the deterministic texts instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import LLM_RECOMMENDATION_MAX_OUTPUT_TOKENS, RECOMMENDATION_CACHE_SIZE
from domain import Dashboard, FallbackRecommendation
from llm import JsonSchemaFormat, LlmClient, LlmError, LlmRequest, LlmResponse
from llm_prompts import RECOMMENDATION_INSTRUCTIONS, recommendation_dossier, recommendation_input
from service import build_fallback_recommendation

logger = logging.getLogger(__name__)

SOURCE_LLM = "llm"
SOURCE_FALLBACK = "fallback"

_TEXT = Field(min_length=1, max_length=2000)
_AMOUNT = r"\d[\d,]*(?:\.\d+)?"
_CURRENCY_AMOUNT = re.compile(rf"(?:€|EUR)\s?[-−]?\s?({_AMOUNT})(k\b)?|({_AMOUNT})(k)?\s?(?:€|EUR\b|euros?\b)", re.IGNORECASE)
_NUMBER = re.compile(_AMOUNT)
_THOUSAND = Decimal("1000")


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

    headline: str = _TEXT
    explanation: str = _TEXT
    recommended_option_id: Optional[str]
    rationale: str = _TEXT
    option_comments: dict[str, str]


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
        all_ids = list(fallback.option_comments)
        response = self._client.respond(LlmRequest(
            instructions=RECOMMENDATION_INSTRUCTIONS,
            input=recommendation_input(dossier),
            max_output_tokens=LLM_RECOMMENDATION_MAX_OUTPUT_TOKENS,
            json_schema=JsonSchemaFormat("recommendation", recommendation_schema(allowed_ids, all_ids)),
        ))
        parsed = validate_recommendation(response, allowed_ids, dossier)
        return Recommendation(
            source=SOURCE_LLM,
            fallback_reason=None,
            headline=parsed.headline,
            explanation=parsed.explanation,
            recommended_option_id=parsed.recommended_option_id,
            rationale=parsed.rationale,
            option_comments=merged_option_comments(parsed.option_comments, fallback.option_comments),
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


def recommendation_schema(allowed_ids: list[str], all_ids: list[str]) -> dict:
    """JSON schema for guided decoding: the option id can only be one that is really on offer."""
    option_id: dict[str, Any] = {"type": "string", "enum": allowed_ids} if allowed_ids else {"type": "null"}
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "headline": text,
            "explanation": text,
            "recommended_option_id": option_id,
            "rationale": text,
            "option_comments": {
                "type": "object",
                "properties": {option: text for option in all_ids},
                "required": all_ids,
                "additionalProperties": False,
            },
        },
        "required": ["headline", "explanation", "recommended_option_id", "rationale", "option_comments"],
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
    invented = ungrounded_amounts(_all_texts(parsed), dossier)
    if invented:
        raise InvalidRecommendationError(
            "llm_ungrounded_numbers", f"The model quoted amounts that are not in the dossier: {invented}.")
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


def merged_option_comments(from_model: dict[str, str], from_code: dict[str, str]) -> dict[str, str]:
    """One comment per offered option: the model's when it gave a non-empty one, otherwise the code's."""
    return {option_id: from_model.get(option_id) or comment for option_id, comment in from_code.items()}


def ungrounded_amounts(text: str, dossier: dict) -> list[str]:
    """Money amounts in the model's text that do not appear in the dossier (rounding of a real figure is fine)."""
    known = sorted(set(_dossier_numbers(dossier)))
    return [raw for raw, amount in _quoted_amounts(text) if not any(_matches(amount, value) for value in known)]


def _all_texts(parsed: LlmRecommendation) -> str:
    return " ".join([parsed.headline, parsed.explanation, parsed.rationale, *parsed.option_comments.values()])


def _quoted_amounts(text: str) -> Iterator[tuple[str, Decimal]]:
    for match in _CURRENCY_AMOUNT.finditer(text):
        digits = match.group(1) or match.group(3)
        thousands = match.group(2) or match.group(4)
        amount = _to_decimal(digits)
        if amount is not None:
            yield match.group(0).strip(), amount * _THOUSAND if thousands else amount


def _dossier_numbers(node: Any) -> Iterator[Decimal]:
    """Every number in the dossier, including those written inside its texts, as absolute values."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _dossier_numbers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _dossier_numbers(value)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        yield abs(Decimal(str(node)))
    elif isinstance(node, str):
        for match in _NUMBER.finditer(node):
            amount = _to_decimal(match.group(0))
            if amount is not None:
                yield amount


def _to_decimal(digits: str) -> Optional[Decimal]:
    try:
        return Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None


def _matches(quoted: Decimal, known: Decimal) -> bool:
    """Equal, or the known figure rounded to whole euros, tens, hundreds or thousands."""
    for step in (Decimal("1000"), Decimal("100"), Decimal("10")):
        if quoted % step == 0 and quoted != 0:
            return abs(quoted - known) <= step / 2
    return abs(quoted - known) < 1
