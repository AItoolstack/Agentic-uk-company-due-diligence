"""
api/app.py
FastAPI application factory.

Usage:
    from src.api.app import app           # direct import
    from src.api.app import create_app    # factory (for testing with overrides)
"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from src.api.prescreen_routes import jobs_router, router as prescreen_router
from src.api.routes import router

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def _mcp_hosts() -> tuple[list[str], list[str]]:
    """Hosts the MCP endpoint answers on. The SDK's DNS-rebinding guard allows only localhost by default,
    which rejects every request to a public host such as *.hf.space. Hugging Face sets SPACE_HOST."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"]
    hosts += [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if os.environ.get("SPACE_HOST"):
        hosts.append(os.environ["SPACE_HOST"])
    origins = [f"https://{h}" for h in hosts if "*" not in h and h != "testserver"]
    origins += ["http://127.0.0.1:*", "http://localhost:*"]
    return hosts, origins


class _RequireAccessToken:
    """Same access rule as the REST API: when PRESCREEN_ACCESS_TOKEN is set, MCP clients must send it,
    as `Authorization: Bearer <token>` or `X-Access-Token: <token>`."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        token = os.environ.get("PRESCREEN_ACCESS_TOKEN", "")
        if scope["type"] == "http" and token:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            given = headers.get("x-access-token") or headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given, token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body",
                            "body": b'{"error": "access token needed: send Authorization: Bearer <token>"}'})
                return
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    from src.config import settings
    from src.prescreen.mcp_server import server as mcp_server

    # The SDK's session manager can only be run once, and its HTTP handler is bound to one manager. So each
    # startup builds a fresh MCP app (tests and uvicorn --reload start the same FastAPI app more than once), and
    # the /mcp route forwards to whichever is current.
    current: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hosts, origins = _mcp_hosts()
        # Stateless: each MCP request stands alone, so it survives restarts and needs no sticky sessions.
        current["asgi"] = _RequireAccessToken(mcp_server.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                         allowed_hosts=hosts, allowed_origins=origins),
        ))
        # The MCP app's own lifespan never runs when it's routed inside FastAPI, so start its session manager here.
        async with mcp_server.session_manager.run():
            yield
        current.pop("asgi", None)

    class _Current:
        # A callable object, not a function: Starlette treats a plain function endpoint as request -> response,
        # but passes raw ASGI to anything else.
        async def __call__(self, scope, receive, send) -> None:
            asgi = current.get("asgi")
            if asgi is None:
                await send({"type": "http.response.start", "status": 503,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"MCP endpoint is starting"})
                return
            await asgi(scope, receive, send)

    mcp_endpoint = _Current()

    app = FastAPI(
        lifespan=lifespan,
        title="UK Company Pre-Screen API",
        description=(
            "Public-record pre-screen of UK companies for commercial underwriters. "
            "REST API under /api/prescreen; the same checks as MCP tools at /mcp."
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

    # An exact route, not a Mount: a Mount at /mcp redirects /mcp to /mcp/, and MCP clients don't follow
    # redirects on POST.
    app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]))
    app.include_router(prescreen_router)
    app.include_router(jobs_router)
    app.include_router(router)
    app.mount(
        "/frontend",
        StaticFiles(directory=str(_FRONTEND_DIR)),
        name="frontend",
    )
    return app


# Module-level singleton -- used by uvicorn: uvicorn src.api.app:app
app = create_app()
