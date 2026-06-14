"""
api/app.py
FastAPI application factory.

Usage:
    from src.api.app import app           # direct import
    from src.api.app import create_app    # factory (for testing with overrides)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes import router

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    from src.config import settings

    app = FastAPI(
        title="UK Company Due Diligence API",
        description=(
            "Agentic company research and risk intelligence. "
            "Powered by LangGraph + Groq/OpenAI."
        ),
        version="0.5.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    allowed_origins = [
        origin.strip()
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.mount(
        "/frontend",
        StaticFiles(directory=str(_FRONTEND_DIR)),
        name="frontend",
    )
    return app


# Module-level singleton -- used by uvicorn: uvicorn src.api.app:app
app = create_app()
