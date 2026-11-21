"""LLM-backed endpoints (POST /recommendation, POST /chat). Routes are registered on ``router``."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

import service
from config import LLM_CONNECT_TIMEOUT_SECONDS, LLM_TIMEOUT_SECONDS, Settings
from deps import get_repository, get_settings
from llm import LlmClient, OgxClient, create_http_client
from llm_chat import ChatService
from llm_recommendation import RecommendationCache, RecommendationService
from repository import Repository
from schemas import ChatRequest, ChatResponse, ErrorResponse, RecommendationResponse, SimulationParams

router = APIRouter()

RepositoryDep = Annotated[Repository, Depends(get_repository)]


@lru_cache(maxsize=1)
def get_llm_client() -> LlmClient:
    """One shared OGX client (httpx keeps a thread-safe connection pool) for the life of the process."""
    settings = get_settings()
    http = create_http_client(settings.ogx_url, LLM_TIMEOUT_SECONDS, LLM_CONNECT_TIMEOUT_SECONDS)
    return OgxClient(http, settings.ogx_model)


@lru_cache(maxsize=1)
def get_recommendation_cache() -> RecommendationCache:
    return RecommendationCache()


def get_recommendation_service(client: Annotated[LlmClient, Depends(get_llm_client)],
                               cache: Annotated[RecommendationCache, Depends(get_recommendation_cache)],
                               ) -> RecommendationService:
    return RecommendationService(client, cache)


def get_chat_service(client: Annotated[LlmClient, Depends(get_llm_client)],
                     settings: Annotated[Settings, Depends(get_settings)]) -> ChatService:
    return ChatService(client, settings.mcp_server_url)


@router.post("/recommendation", response_model=RecommendationResponse, responses={422: {"model": ErrorResponse}})
def recommend(params: SimulationParams, repository: RepositoryDep,
              recommendations: Annotated[RecommendationService, Depends(get_recommendation_service)],
              ) -> RecommendationResponse:
    """The model's plain-language explanation and its pick among the options computed by code."""
    dashboard = service.build_dashboard(repository, params.to_query())
    return RecommendationResponse.model_validate(recommendations.recommend(dashboard))


@router.post("/chat", response_model=ChatResponse,
             responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def chat(request: ChatRequest, repository: RepositoryDep,
         assistant: Annotated[ChatService, Depends(get_chat_service)]) -> ChatResponse:
    """One turn with the scenario assistant; planned events are written by the MCP server, never by this code."""
    turn = assistant.handle(repository, request.to_query(), request.message, request.conversation_id)
    return ChatResponse.model_validate(turn)
