"""
Run the old LangGraph research graph (src/graph.py, pre-rebuild) with a chosen model and measure it:
every chat-model call with tokens and time, errors, and wall time. Used only by the three-way evaluation.

Why a model override: the old graph ran on Groq's free tier in the baseline (01), where rate limits caused
some of its failures. Running it on the same model as the agent layer separates "the architecture" from
"the free tier". The override swaps the model settings for the duration of one run, under a lock, and
puts them back.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

_LOCK = asyncio.Lock()


class CallMeter(BaseCallbackHandler):
    """Counts chat-model calls, with tokens, time and errors per call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._open: dict[UUID, tuple[float, str]] = {}

    def _start(self, run_id: UUID, serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> None:
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or ""
        self._open[run_id] = (time.perf_counter(), str(model))

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):  # noqa: ANN001
        self._start(run_id, serialized, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):  # noqa: ANN001
        self._start(run_id, serialized, kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):  # noqa: ANN001
        t0, model = self._open.pop(run_id, (time.perf_counter(), ""))
        usage: dict[str, Any] = {}
        try:
            msg = response.generations[0][0].message
            usage = dict(getattr(msg, "usage_metadata", None) or {})
            model = (getattr(msg, "response_metadata", None) or {}).get("model_name") or model
        except (IndexError, AttributeError):
            pass
        details = usage.get("output_token_details") or {}
        self.calls.append({"ok": True, "model": model, "ms": round((time.perf_counter() - t0) * 1000),
                           "input_tokens": int(usage.get("input_tokens") or 0),
                           "output_tokens": int(usage.get("output_tokens") or 0),
                           "reasoning_tokens": int(details.get("reasoning") or 0)})

    def on_llm_error(self, error, *, run_id, **kwargs):  # noqa: ANN001
        t0, model = self._open.pop(run_id, (time.perf_counter(), ""))
        self.calls.append({"ok": False, "model": model, "ms": round((time.perf_counter() - t0) * 1000),
                           "error": f"{type(error).__name__}: {str(error)[:200]}"})


_METER: ContextVar[CallMeter | None] = ContextVar("legacy_call_meter", default=None)
register_configure_hook(_METER, inheritable=True)


async def run_legacy(query: str, provider: str = "", model: str = "", fast_model: str = "") -> dict[str, Any]:
    from src import llm as llm_module
    from src.api.routes import get_research_graph
    from src.config import settings
    from src.state import create_initial_state

    async with _LOCK:
        saved = (settings.chat_model_provider, settings.chat_model, settings.chat_model_fast,
                 settings.openai_temperature)
        meter = CallMeter()
        token = _METER.set(meter)
        try:
            if provider or model:
                settings.chat_model_provider = provider or settings.chat_model_provider
                settings.chat_model = model or settings.chat_model
                settings.chat_model_fast = fast_model or model or settings.chat_model_fast
                if settings.chat_model_provider == "openai":
                    settings.openai_temperature = None  # newer OpenAI models reject temperature
            llm_module.get_llm.cache_clear()
            t0 = time.perf_counter()
            error = ""
            state: dict[str, Any] = {}
            try:
                state = await get_research_graph().ainvoke(create_initial_state(query))
            except Exception as err:  # noqa: BLE001 - a crash is a result here
                error = f"{type(err).__name__}: {str(err)[:400]}"
            elapsed = time.perf_counter() - t0
        finally:
            _METER.reset(token)
            (settings.chat_model_provider, settings.chat_model, settings.chat_model_fast,
             settings.openai_temperature) = saved
            llm_module.get_llm.cache_clear()
    brief = state.get("due_diligence_brief") if state else None
    errors = [e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in (state.get("errors") or [])]
    calls = meter.calls
    return {
        "query": query,
        "model": {"provider": provider or saved[0], "quality": model or saved[1],
                  "fast": fast_model or model or saved[2]},
        "elapsed_seconds": round(elapsed, 2),
        "crash": error,
        "brief": brief.model_dump(mode="json") if brief is not None else None,
        "errors": errors,
        "iterations": state.get("iteration_count") if state else None,
        "resolved_company_number": getattr(state.get("entity_resolution"), "company_number", None) if state else None,
        "calls": calls,
        "totals": {"model_calls": len(calls), "failed_calls": sum(not c["ok"] for c in calls),
                   "input_tokens": sum(c.get("input_tokens", 0) for c in calls),
                   "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
                   "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in calls),
                   "model_ms": sum(c["ms"] for c in calls)},
    }
