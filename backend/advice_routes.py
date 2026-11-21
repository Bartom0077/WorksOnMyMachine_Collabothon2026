"""Endpoints of the CFO advisors (tax, FX, investment and their coordinator). Routes are registered on ``router``."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from advice_schemas import (
    AdviceDecisionRequest,
    AdviceDecisionsResponse,
    AdviceRecommendationResponse,
    AdviceResponse,
)
from advisors.decisions import record_advice_decisions
from advisors.engine import build_advice
from advisors.llm_service import AdviceCache, AdviceRecommendationService
from deps import get_repository
from llm import LlmClient
from llm_routes import get_llm_client
from repository import Repository
from schemas import ErrorResponse, SimulationParams

router = APIRouter()

RepositoryDep = Annotated[Repository, Depends(get_repository)]


@lru_cache(maxsize=1)
def get_advice_cache() -> AdviceCache:
    return AdviceCache()


def get_advice_recommendation_service(client: Annotated[LlmClient, Depends(get_llm_client)],
                                      cache: Annotated[AdviceCache, Depends(get_advice_cache)],
                                      ) -> AdviceRecommendationService:
    return AdviceRecommendationService(client, cache)


@router.get("/advice", response_model=AdviceResponse, responses={422: {"model": ErrorResponse}})
def get_advice(params: Annotated[SimulationParams, Query()], repository: RepositoryDep) -> AdviceResponse:
    """The advisors' proposals after the coordinator's rules: any of them can be approved together. No LLM."""
    return AdviceResponse.from_advice(build_advice(repository, params.to_query()))


@router.post("/advice/recommendation", response_model=AdviceRecommendationResponse,
             responses={422: {"model": ErrorResponse}})
def recommend_advice(params: SimulationParams, repository: RepositoryDep,
                     recommendations: Annotated[AdviceRecommendationService,
                                                Depends(get_advice_recommendation_service)],
                     ) -> AdviceRecommendationResponse:
    """The model's wording of the proposals computed by code, its tips and the coordinator's summary."""
    advice = build_advice(repository, params.to_query())
    return AdviceRecommendationResponse.model_validate(recommendations.recommend(advice))


@router.post("/advice/decisions", response_model=AdviceDecisionsResponse, status_code=status.HTTP_201_CREATED,
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def create_advice_decisions(request: AdviceDecisionRequest, repository: RepositoryDep) -> AdviceDecisionsResponse:
    """Approve or reject several proposals at once; all of them are stored or none."""
    records = record_advice_decisions(repository, request.to_query(), request.proposal_ids, request.status,
                                      request.explanations, request.fingerprints)
    return AdviceDecisionsResponse.from_records(records)
