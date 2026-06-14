"""
utils.py
--------
Shared utility functions used across the framework.
No business logic -- pure helpers only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_file(path: str | Path) -> dict[str, Any]:
    """Load and parse a JSON file. Raises FileNotFoundError if absent."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON file not found: {p}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def mock_data_path(filename: str) -> Path:
    """Return the absolute path to a mock data file."""
    # Walk up from this file to find the project root
    here = Path(__file__).resolve().parent.parent
    return here / "data" / "mock_sources" / filename


def truncate(text: str, max_chars: int = 300) -> str:
    """Truncate a string for display purposes."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def safe_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts without KeyError."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None:
            return default
    return current


def flatten_evidence_summary(evidence_by_dimension: dict[str, Any]) -> str:
    """Produce a compact text summary of collected evidence for prompt injection."""
    lines = []
    for dimension, item in evidence_by_dimension.items():
        summary = getattr(item, "summary", "") or str(item)
        quality = getattr(item, "quality", "unknown")
        lines.append(f"[{dimension}] ({quality}): {truncate(summary)}")
    return "\n".join(lines) if lines else "No evidence collected."
