"""FastAPI dependencies shared by all routers."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from config import Settings, load_settings
from repository import PostgresRepository, Repository, open_connection


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def get_repository() -> Iterator[Repository]:
    """One PostgreSQL connection per request, closed when the response is done."""
    connection = open_connection()
    try:
        yield PostgresRepository(connection, get_settings().account_number)
    finally:
        connection.close()
