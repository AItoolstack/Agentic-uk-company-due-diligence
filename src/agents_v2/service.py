"""
Entry point for the agent layer: run a review, trace it, store it.

    run = await run_agent_review(report_id="...")                 # review a saved case
    run = await run_agent_review(company_query="09446231",        # or pre-screen first, over MCP
                                 proposal={"declared_trade": "Bank"})

The run dict is what the API returns and what is stored: review (route copied, sections), trace (spans +
totals), and enough settings to repeat it.
"""

from __future__ import annotations

import os
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

from src.agents_v2.guardrail import judge_enabled
from src.agents_v2.model import AgentModel, Scripted, default_model
from src.agents_v2.supervisor import build_graph
from src.agents_v2.toolbox import Toolbox
from src.agents_v2.trace import Trace

_RUNS: deque = deque()


class AgentLimit(Exception):
    pass


def agent_limit() -> None:
    """Agent reviews cost money per run, so they have their own hourly cap on top of the pre-screen's."""
    cap = int(os.environ.get("AGENT_RUNS_PER_HOUR", "20"))
    now = time.time()
    while _RUNS and now - _RUNS[0] > 3600:
        _RUNS.popleft()
    if len(_RUNS) >= cap:
        raise AgentLimit(f"The limit of {cap} agent reviews an hour has been reached. Try again later.")
    _RUNS.append(now)


def reset_limit() -> None:
    _RUNS.clear()


def config() -> dict[str, Any]:
    from src.agents_v2.model import provider_key
    provider = os.environ.get("AGENT_PROVIDER", "openai")
    return {"model": default_model(), "provider": provider, "available": bool(provider_key(provider)),
            "judge_model": os.environ.get("AGENT_GUARDRAIL_MODEL", "") or default_model(),
            "judge": judge_enabled(),
            "transport": "http" if os.environ.get("AGENT_MCP_URL") or os.environ.get("SPACE_HOST") else "in-process",
            "runs_per_hour": int(os.environ.get("AGENT_RUNS_PER_HOUR", "20"))}


async def run_agent_review(report_id: str = "", company_query: str = "", proposal: dict[str, Any] | None = None,
                           scripted: Scripted | None = None, judge_scripted: Scripted | None = None,
                           mcp_url: str | None = None, server: Any = None, store: bool = True,
                           provider: str | None = None, model_name: str | None = None, judge: bool | None = None,
                           run_by: str = "owner") -> dict[str, Any]:
    """provider / model_name / judge override the environment for one run. The API passes them for guests
    (access.guest_agent_model: Groq or capped OpenRouter, never OpenAI); the judge then uses the same model."""
    trace = Trace()
    created = datetime.now(timezone.utc).isoformat()
    use_judge = judge_enabled() if judge is None else (judge and judge_enabled())
    async with httpx.AsyncClient(timeout=90) as http:
        model = AgentModel(trace, model=model_name, provider=provider, client=http, scripted=scripted)
        judge_model = None
        if use_judge:
            judge_name = model_name if provider else (os.environ.get("AGENT_GUARDRAIL_MODEL") or None)
            judge_model = AgentModel(trace, model=judge_name, provider=provider, client=http,
                                     scripted=judge_scripted if judge_scripted is not None else
                                     (scripted and _no_problems))
        async with Toolbox(trace, url=mcp_url, server=server) as toolbox:
            graph = build_graph(toolbox, model, judge_model)
            final = await graph.ainvoke({"report_id": report_id, "company_query": company_query,
                                         "proposal": proposal or {}, "sections": []})
            transport = toolbox.transport
    review = final.get("review") or {}
    sections = review.get("sections") or []
    status = ("stopped" if review.get("stopped") else
              "partial" if any(s["status"] in ("error", "blocked") for s in sections) else "ok")
    run = {
        "run_id": secrets.token_hex(8),
        "report_id": final.get("report_id") or report_id,
        "created_at": created,
        "status": status,
        "model": model.model,
        "judge_model": judge_model.model if judge_model else "",
        "transport": transport,
        "provider": model.provider,
        "run_by": run_by,
        "scripted": scripted is not None,
        "review": review,
        "trace": trace.to_dict(),
    }
    if store and run["report_id"]:
        from src.prescreen.store import get_store
        try:
            await get_store().save_agent_run(run)
        except Exception as err:  # noqa: BLE001 - a storage failure shouldn't lose the review
            run["not_saved"] = f"couldn't save the agent run ({type(err).__name__})"
    return run


def _no_problems(agent: str, system: str, user: str) -> str:
    return '{"problems": []}'
