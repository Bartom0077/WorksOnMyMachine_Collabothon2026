"""Autonomous CFO backend — REST API (FastAPI). Run with ``python app.py`` (uvicorn on 0.0.0.0:8080)."""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from config import SERVICE_NAME
from deps import get_settings
from errors import register_error_handlers
from llm_routes import router as llm_router
from routes import router as core_router


def create_app() -> FastAPI:
    application = FastAPI(title="Autonomous CFO – Pizzeria Amalfi", version="1.0.0")
    register_error_handlers(application)
    application.include_router(core_router)
    application.include_router(llm_router)
    return application


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    logging.getLogger(SERVICE_NAME).info("Listening on %s:%s", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port)
