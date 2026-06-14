"""
api_server.py
Uvicorn entry point for the FastAPI due diligence API.

Usage:
    # Development (auto-reload on code changes):
    uvicorn api_server:app --reload --port 8000

    # Direct run (same as above):
    python api_server.py

    # Production (multiple workers):
    uvicorn api_server:app --workers 4 --port 8000

Endpoints:
    GET  http://localhost:8000/health          -- liveness probe
    POST http://localhost:8000/research/sync   -- blocking JSON response
    POST http://localhost:8000/research/stream -- SSE streaming response
    GET  http://localhost:8000/docs            -- Swagger UI
"""

from __future__ import annotations

from src.api.app import app  # noqa: F401 -- re-exported for uvicorn

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
