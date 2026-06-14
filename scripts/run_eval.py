#!/usr/bin/env python3
"""
scripts/run_eval.py
--------------------
CLI evaluation runner.

Runs each query in SAMPLE_QUERIES through the compiled graph (with mock data)
and validates the resulting DueDiligenceBrief using validate_brief.

Usage:
    python scripts/run_eval.py               # run all sample queries
    python scripts/run_eval.py monzo_full    # run one query by id

Exit code 0 = all passed, 1 = one or more failures.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

# Ensure project root is on path when run directly
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from src.config import settings
from src.evaluation.sample_queries import SAMPLE_QUERIES
from src.evaluation.trace_validator import validate_brief
from src.graph import compiled_graph
from src.schemas import DueDiligenceBrief
from src.state import create_initial_state

console = Console()
settings.use_mock_data = True


def _extract_brief(result: dict[str, Any]) -> DueDiligenceBrief | None:
    """Pull DueDiligenceBrief from graph output state."""
    brief = result.get("due_diligence_brief")
    if isinstance(brief, DueDiligenceBrief):
        return brief
    return None


def run_query(sample: dict[str, Any]) -> dict[str, Any]:
    """Run a single sample query through the graph and return eval metrics."""
    console.print(f"\n[bold]Running:[/bold] {sample['id']} -- {sample['query'][:70]}...")
    t0 = time.perf_counter()

    initial_state = create_initial_state(sample["query"])

    try:
        result = asyncio.run(compiled_graph.ainvoke(initial_state))
    except Exception as e:
        return {
            "id": sample["id"],
            "passed": False,
            "failures": [f"Graph raised exception: {e}"],
            "warnings": [],
            "elapsed": time.perf_counter() - t0,
            "errors_in_state": [],
        }

    elapsed = time.perf_counter() - t0
    brief = _extract_brief(result)

    if brief is None:
        return {
            "id": sample["id"],
            "passed": False,
            "failures": ["No DueDiligenceBrief in output state"],
            "warnings": [],
            "elapsed": elapsed,
            "errors_in_state": result.get("errors", []),
        }

    validation = validate_brief(brief, sample)
    return {
        "id": sample["id"],
        "passed": validation.passed,
        "failures": validation.failures,
        "warnings": validation.warnings,
        "elapsed": elapsed,
        "errors_in_state": result.get("errors", []),
        "confidence": brief.overall_confidence,
        "risk_level": brief.overall_risk_level.value,
        "dimensions_covered": len(brief.dimensions_covered),
    }


def main(query_ids: list[str] | None = None) -> int:
    """Run eval and return exit code (0=pass, 1=fail)."""
    samples = SAMPLE_QUERIES
    if query_ids:
        samples = [s for s in samples if s["id"] in query_ids]
        if not samples:
            console.print(f"[red]No samples found for ids: {query_ids}[/red]")
            return 1

    console.print(f"[bold cyan]Evaluation harness[/bold cyan] -- {len(samples)} queries (USE_MOCK_DATA=true)")
    results = [run_query(s) for s in samples]

    # Summary table
    table = Table(title="Eval Results", show_lines=True)
    table.add_column("ID", style="cyan")
    table.add_column("Pass", justify="center")
    table.add_column("Confidence", justify="right")
    table.add_column("Risk", justify="center")
    table.add_column("Dims", justify="right")
    table.add_column("Time (s)", justify="right")
    table.add_column("Failures")

    all_passed = True
    for r in results:
        passed = r["passed"]
        all_passed = all_passed and passed
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        failures_str = "; ".join(r.get("failures", [])) or "-"
        table.add_row(
            r["id"],
            status,
            f"{r.get('confidence', 0):.2f}",
            r.get("risk_level", "-"),
            str(r.get("dimensions_covered", 0)),
            f"{r['elapsed']:.1f}",
            failures_str,
        )
        for w in r.get("warnings", []):
            console.print(f"  [yellow]Warning ({r['id']}):[/yellow] {w}")
        for e in r.get("errors_in_state", []):
            console.print(f"  [red]State error ({r['id']}):[/red] {e}")

    console.print(table)

    if all_passed:
        console.print("[bold green]All queries passed.[/bold green]")
        return 0
    else:
        failed = [r["id"] for r in results if not r["passed"]]
        console.print(f"[bold red]Failed: {failed}[/bold red]")
        return 1


if __name__ == "__main__":
    ids = sys.argv[1:] or None
    sys.exit(main(ids))
