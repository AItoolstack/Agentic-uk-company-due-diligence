"""
tracing.py
Observability and tracing helpers.

Wraps LangSmith tracing config and provides a lightweight console
trace logger using rich for local development.

Usage:
    from src.tracing import tracer
    tracer.log_agent_start("query_understanding", state)
    tracer.log_agent_end("query_understanding", output)
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.config import settings

console = Console()


def langsmith_tracing_context() -> AbstractContextManager[None]:
    """Return an explicit LangSmith tracing context when configured."""
    if not settings.langchain_tracing_v2 or not settings.langchain_api_key:
        return nullcontext()

    from langsmith import Client, tracing_context

    client = Client(api_key=settings.langchain_api_key)
    return tracing_context(
        enabled=True,
        project_name=settings.langchain_project,
        client=client,
    )


class AgentTracer:
    """Lightweight agent execution tracer for console output."""

    def log_agent_start(self, agent_name: str, inputs: dict[str, Any]) -> None:
        console.print(
            Panel(
                f"[bold cyan]-> {agent_name}[/bold cyan]  [dim]{datetime.utcnow().isoformat()}[/dim]",
                expand=False,
            )
        )

    def log_agent_end(self, agent_name: str, output: Any) -> None:
        console.print(Text(f"  OK {agent_name} complete", style="green"))

    def log_error(self, agent_name: str, error: Exception) -> None:
        console.print(f"  [bold red]ERROR {agent_name}:[/bold red] {error}")

    def log_event(self, agent_name: str, message: str) -> None:
        console.print(f"  [dim]{agent_name}:[/dim] {message}")

    def log_iteration(self, iteration: int, reason: str) -> None:
        console.print(f"\n[yellow]RETRY iteration {iteration}[/yellow]: {reason}\n")


tracer = AgentTracer()
