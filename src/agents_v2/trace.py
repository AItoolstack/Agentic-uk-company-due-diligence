"""
A trace of one agent review: every model call, tool call and guardrail check, in order.

Kept deliberately plain (a list of dicts) so it can be stored as JSON, shown in the UI and compared in
the evaluation without a tracing service. Cost uses list prices from settings (USD per million tokens).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


def price_per_million(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens. Override with AGENT_PRICE_<IN|OUT> for the default model, or
    AGENT_PRICES='model=in/out,model2=in/out' for several."""
    table = {
        "gpt-6-luna": (0.10, 0.50),
    }
    for item in os.environ.get("AGENT_PRICES", "").split(","):
        if "=" in item and "/" in item:
            name, prices = item.split("=", 1)
            try:
                p_in, p_out = (float(x) for x in prices.split("/", 1))
                table[name.strip()] = (p_in, p_out)
            except ValueError:
                pass
    if model in table:
        return table[model]
    for name, prices in table.items():  # dated variants, e.g. gpt-6-luna-2026-09-30
        if model.startswith(name):
            return prices
    return (float(os.environ.get("AGENT_PRICE_IN", "0") or 0), float(os.environ.get("AGENT_PRICE_OUT", "0") or 0))


@dataclass
class Trace:
    spans: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def add(self, kind: str, name: str, agent: str, ms: float, **extra: Any) -> dict[str, Any]:
        span = {"seq": len(self.spans) + 1, "kind": kind, "name": name, "agent": agent,
                "at_ms": round((time.monotonic() - self.started) * 1000 - ms), "ms": round(ms), **extra}
        self.spans.append(span)
        return span

    @contextmanager
    def timed(self, kind: str, name: str, agent: str, **extra: Any) -> Iterator[dict[str, Any]]:
        """Times a block. The yielded dict collects fields to add to the span (tokens, ok, error...)."""
        info: dict[str, Any] = {}
        t0 = time.monotonic()
        try:
            yield info
        except Exception as err:
            info.setdefault("ok", False)
            info.setdefault("error", f"{type(err).__name__}: {str(err)[:200]}")
            raise
        finally:
            info.setdefault("ok", True)
            self.add(kind, name, agent, (time.monotonic() - t0) * 1000, **extra, **info)

    def totals(self) -> dict[str, Any]:
        model = [s for s in self.spans if s["kind"] == "model"]
        tools = [s for s in self.spans if s["kind"] == "tool"]
        by_agent: dict[str, dict[str, Any]] = {}
        for s in self.spans:
            a = by_agent.setdefault(s["agent"], {"model_calls": 0, "tool_calls": 0, "input_tokens": 0,
                                                 "output_tokens": 0, "cost_usd": 0.0})
            if s["kind"] == "model":
                a["model_calls"] += 1
                a["input_tokens"] += s.get("input_tokens", 0)
                a["output_tokens"] += s.get("output_tokens", 0)
                a["cost_usd"] += s.get("cost_usd", 0.0)
            elif s["kind"] == "tool":
                a["tool_calls"] += 1
        for a in by_agent.values():
            a["cost_usd"] = round(a["cost_usd"], 6)
        return {
            "wall_ms": round((time.monotonic() - self.started) * 1000),
            "model_calls": len(model),
            "tool_calls": len(tools),
            "input_tokens": sum(s.get("input_tokens", 0) for s in model),
            "output_tokens": sum(s.get("output_tokens", 0) for s in model),
            "reasoning_tokens": sum(s.get("reasoning_tokens", 0) for s in model),
            "cost_usd": round(sum(s.get("cost_usd", 0.0) for s in model), 6),
            "model_ms": sum(s["ms"] for s in model),
            "tool_ms": sum(s["ms"] for s in tools),
            "tool_bytes": sum(s.get("bytes", 0) for s in tools),
            "failed_calls": sum(1 for s in self.spans if s.get("ok") is False),
            "by_agent": by_agent,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"spans": self.spans, "totals": self.totals()}
