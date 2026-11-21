"""Application errors and the handlers that turn every failure into the contract's error body:
``{"error": {"code": "<machine_code>", "message": "<human readable English>"}}``.
"""
from __future__ import annotations

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kpi_engine.repository import RepositoryError
from ledger import LedgerError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An expected failure with a stable machine-readable code."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AsOfDateOutOfRangeError(ApiError):
    status_code = 422
    code = "as_of_date_out_of_range"


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class OptionNotAvailableError(ApiError):
    status_code = 409
    code = "option_not_available"


class ProposalNotAvailableError(ApiError):
    status_code = 409
    code = "proposal_not_available"


class DataUnavailableError(ApiError):
    status_code = 503
    code = "data_unavailable"


class AssistantUnavailableError(ApiError):
    status_code = 503
    code = "assistant_unavailable"


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RepositoryError, _handle_database_unavailable)
    app.add_exception_handler(psycopg.OperationalError, _handle_database_unavailable)
    app.add_exception_handler(LedgerError, _handle_ledger_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = "; ".join(_describe_validation_problem(error) for error in exc.errors())
    return error_response(422, "validation_error", f"Invalid request: {problems}")


def _describe_validation_problem(error: dict) -> str:
    location = ".".join(str(part) for part in error["loc"] if part not in ("body", "query", "path"))
    return f"{location}: {error['msg']}" if location else error["msg"]


def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


def _handle_database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return error_response(503, "database_unavailable", "The database is currently unavailable. Please try again.")


def _handle_ledger_error(request: Request, exc: LedgerError) -> JSONResponse:
    logger.error("Inconsistent bank data on %s %s: %s", request.method, request.url.path, exc)
    return error_response(500, "inconsistent_bank_data", str(exc))


def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "An unexpected error occurred.")
