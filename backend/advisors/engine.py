"""Entry point of the deterministic advisors: one call computes everything for one simulated "today"."""
from __future__ import annotations

from advisors.coordinator import coordinate
from advisors.model import Advice
from domain import DashboardQuery
from repository import Repository
from service import build_workspace


def build_advice(repository: Repository, query: DashboardQuery) -> Advice:
    """The coordinated proposals of the tax, FX and investment agents. Deterministic, no language model."""
    workspace = build_workspace(repository, query)
    return coordinate(workspace, repository.decision_fingerprint_statuses())
