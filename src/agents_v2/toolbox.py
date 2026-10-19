"""
The agents' only way to the pre-screen: an MCP client session.

Two transports:
  - in-process (default for tests and when AGENT_MCP_URL is unset): the real MCP protocol over memory
    streams, against the same server object the API serves at /mcp.
  - HTTP: AGENT_MCP_URL, e.g. http://127.0.0.1:7860/mcp on the Space, sending the same access token as the UI.
    This is the path a remote orchestrator (or Claude Desktop, or Copilot Studio) would take.

Each agent gets an allowlist. A call outside it never reaches the server: it's refused here and logged in
the trace, so a test can prove an agent was stopped rather than trusted.
"""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client

from src.agents_v2.trace import Trace

ALL_TOOLS = {"find_company", "prescreen_company", "get_report_section", "company_record", "gazette_notices",
             "sanctions_screen", "fca_firm_search", "watch_company", "list_changes"}


class ToolRefused(Exception):
    pass


class Toolbox:
    def __init__(self, trace: Trace, url: str | None = None, token: str | None = None,
                 server: Any = None, timeout: float = 240) -> None:
        self.trace = trace
        self.url = url if url is not None else os.environ.get("AGENT_MCP_URL", "")
        self.token = token if token is not None else os.environ.get("PRESCREEN_ACCESS_TOKEN", "")
        self._server = server
        self.timeout = timeout
        self._stack: AsyncExitStack | None = None
        self.client: Client | None = None
        self.transport = ""

    async def __aenter__(self) -> "Toolbox":
        self._stack = AsyncExitStack()
        if self.url:
            from mcp.client import streamable_http as sh
            headers = {"X-Access-Token": self.token} if self.token else {}
            http = sh.create_mcp_http_client(headers=headers, timeout=sh.httpx2.Timeout(self.timeout))
            self.client = Client(sh.streamable_http_client(self.url, http_client=http),
                                 read_timeout_seconds=self.timeout)
            self.transport = "http"
        else:
            server = self._server
            if server is None:
                from src.prescreen.mcp_server import server as default_server
                server = default_server
            self.client = Client(server, read_timeout_seconds=self.timeout)
            self.transport = "in-process"
        with self.trace.timed("tool", "connect", "supervisor", transport=self.transport):
            await self._stack.enter_async_context(self.client)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def list_tools(self) -> list[str]:
        assert self.client is not None
        return [t.name for t in (await self.client.list_tools()).tools]

    async def call(self, agent: str, tool: str, args: dict[str, Any],
                   allowed: set[str] | frozenset[str]) -> dict[str, Any]:
        """Call one MCP tool for an agent. Returns the tool's structured result, or {"tool_error": ...}."""
        assert self.client is not None
        if tool not in allowed:
            self.trace.add("tool", tool, agent, 0, ok=False, refused=True,
                           error=f"{tool} isn't on {agent}'s allowlist", args=_short(args))
            raise ToolRefused(f"{tool} isn't available to {agent}")
        with self.trace.timed("tool", tool, agent, args=_short(args)) as span:
            result = await self.client.call_tool(tool, args)
            if result.is_error:
                text = " ".join(getattr(c, "text", "") for c in result.content or [])[:400]
                span.update(ok=False, error=text, bytes=len(text))
                return {"tool_error": text or "the tool returned an error"}
            out = result.structured_content
            if out is None:
                text = " ".join(getattr(c, "text", "") for c in result.content or [])
                try:
                    out = json.loads(text)
                except ValueError:
                    out = {"text": text}
            span["bytes"] = len(json.dumps(out, default=str))
            return out


def _short(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if not isinstance(v, str) or len(v) < 80 else v[:77] + "...") for k, v in args.items()}
