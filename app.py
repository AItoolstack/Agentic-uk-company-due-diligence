"""
app.py
CLI entry point for UK company due diligence research.

Usage:
    python app.py
    python app.py --query "Create a due diligence brief for Revolut Ltd"

Uses ainvoke (async) because parallel_source_collector is an async node.
"""

from __future__ import annotations

import argparse
import asyncio
from rich.console import Console
from rich.panel import Panel

from src.state import create_initial_state
from src.tracing import langsmith_tracing_context

console = Console()

DEFAULT_QUERY = (
    "Create a due diligence and risk intelligence brief for Monzo Bank Limited. "
    "Include company status, officers, filing activity, regulatory position, "
    "recent news signals, key risks, and confidence level."
)


async def _run(query: str) -> None:
    try:
        from src.graph import compiled_graph
    except Exception as e:
        console.print(f"[red]Failed to initialise graph: {e}[/red]")
        return

    initial_state = create_initial_state(query)

    try:
        with langsmith_tracing_context():
            result = await compiled_graph.ainvoke(initial_state)
    except Exception as e:
        console.print(f"[red]Research run failed: {e}[/red]")
        return

    errors = result.get("errors", [])
    if errors:
        console.print(f"[yellow]Agent errors during run:[/yellow]")
        for err in errors:
            console.print(f"  {err}")

    brief = result.get("due_diligence_brief")
    if brief:
        console.print_json(brief.model_dump_json(indent=2))
    else:
        console.print("[red]No brief produced. Check agent errors above.[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="UK company due diligence and risk intelligence"
    )
    parser.add_argument(
        "--query",
        type=str,
        default=DEFAULT_QUERY,
        help="The due diligence research query.",
    )
    args = parser.parse_args()

    console.print(
        Panel(
            f"[bold]UK Company Due Diligence[/bold]\n\n[dim]{args.query}[/dim]",
            title="Query",
            expand=False,
        )
    )

    asyncio.run(_run(args.query))


if __name__ == "__main__":
    main()
