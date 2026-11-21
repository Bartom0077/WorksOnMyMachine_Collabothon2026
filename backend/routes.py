"""Thin HTTP handlers for the deterministic core. nginx strips the ``/api`` prefix before requests arrive here."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

import service
from deps import get_repository
from repository import Repository
from schemas import (
    DashboardResponse,
    DecisionCreatedResponse,
    DecisionOut,
    DecisionRequest,
    DecisionsResponse,
    ErrorResponse,
    EventOut,
    EventsResponse,
    HealthResponse,
    SimulationParams,
)

router = APIRouter()

RepositoryDep = Annotated[Repository, Depends(get_repository)]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/dashboard", response_model=DashboardResponse, responses={422: {"model": ErrorResponse}})
def get_dashboard(params: Annotated[SimulationParams, Query()], repository: RepositoryDep) -> DashboardResponse:
    return DashboardResponse.model_validate(service.build_dashboard(repository, params.to_query()))


@router.get("/events", response_model=EventsResponse)
def list_events(repository: RepositoryDep) -> EventsResponse:
    return EventsResponse(events=[EventOut.model_validate(event) for event in repository.list_events()])


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses={404: {"model": ErrorResponse}})
def delete_event(event_id: int, repository: RepositoryDep) -> Response:
    service.delete_event(repository, event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/decisions", response_model=DecisionsResponse)
def list_decisions(repository: RepositoryDep) -> DecisionsResponse:
    decisions = [DecisionOut.model_validate(decision) for decision in repository.list_decisions()]
    return DecisionsResponse(decisions=decisions)


@router.post("/decisions", response_model=DecisionCreatedResponse, status_code=status.HTTP_201_CREATED,
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def create_decision(request: DecisionRequest, repository: RepositoryDep) -> DecisionCreatedResponse:
    record = service.record_decision(repository, request.to_query(), request.option_id, request.status,
                                     request.llm_explanation)
    decision = DecisionOut.model_validate(record)
    return DecisionCreatedResponse(decision=decision, execution_messages=decision.execution_messages)


@router.post("/demo/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_demo(repository: RepositoryDep) -> Response:
    """Demo convenience: delete all planned events and owner decisions."""
    repository.reset_demo()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
