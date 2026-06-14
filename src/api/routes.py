"""
api/routes.py
FastAPI router: all research endpoints.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ResearchRequest,
    ResearchResponse,
)
from src.state import create_initial_state
from src.llm import get_llm
from src.tracing import langsmith_tracing_context

router = APIRouter()
_FRONTEND_HTML = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

# Nodes that emit node_complete SSE events
_GRAPH_NODES = {
    "query_understanding",
    "entity_resolution",
    "research_planner",
    "source_selector",
    "parallel_source_collector",
    "news_classifier",
    "evidence_critic",
    "gap_detector",
    "followup_planner",
    "contradiction_detector",
    "synthesis",
}


def get_research_graph() -> Any:
    """Build the graph; kept as a dependency boundary for offline API tests."""
    from src.graph import build_graph

    return build_graph()


# ---------------------------------------------------------------------------
# Health + frontend
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/", response_class=HTMLResponse, tags=["meta"])
async def serve_frontend() -> HTMLResponse:
    if _FRONTEND_HTML.exists():
        return HTMLResponse(_FRONTEND_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>frontend/index.html not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Sync research endpoint
# ---------------------------------------------------------------------------

@router.post("/research/sync", response_model=ResearchResponse, tags=["research"])
async def research_sync(req: ResearchRequest) -> ResearchResponse:
    """Run the full agent graph synchronously and return the complete brief."""
    t0 = time.perf_counter()
    graph = get_research_graph()
    initial = create_initial_state(req.query)
    with langsmith_tracing_context():
        result_state = await graph.ainvoke(initial)

    brief_obj = result_state.get("due_diligence_brief")
    brief_dict = brief_obj.model_dump(mode="json") if brief_obj else None
    raw_errors = result_state.get("errors") or []
    errors = [
        e.get("message", str(e)) if isinstance(e, dict) else str(e)
        for e in raw_errors
    ]

    return ResearchResponse(
        brief=brief_dict,
        errors=errors,
        elapsed_seconds=round(time.perf_counter() - t0, 2),
    )


# ---------------------------------------------------------------------------
# Streaming SSE endpoint
# ---------------------------------------------------------------------------

@router.post("/research/stream", tags=["research"])
async def research_stream(req: ResearchRequest) -> StreamingResponse:
    """Stream agent progress as Server-Sent Events, then emit the final brief."""
    return StreamingResponse(
        _sse_generator(req.query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )




def _extract_node_detail(node_name: str, node_update: dict) -> str:
    """Return a one-line human-readable summary of what a node found.

    Displayed in the frontend live-log alongside the completed node.
    Silently returns "" on any error so the SSE stream is never affected.
    """
    try:
        def _val(obj, *attrs):
            for a in attrs:
                if obj is None:
                    return None
                obj = obj.get(a) if isinstance(obj, dict) else getattr(obj, a, None)
            return obj

        if node_name == "query_understanding":
            qu = node_update.get("query_understanding")
            name = _val(qu, "entity_name") or ""
            lines = _val(qu, "coverage_lines") or []
            if isinstance(lines, list) and lines:
                return f"{name} -- {', '.join(str(l) for l in lines[:3])}"
            return str(name)

        if node_name == "entity_resolution":
            er = node_update.get("entity_resolution")
            parts = []
            cn = _val(er, "company_name")
            if cn: parts.append(str(cn))
            num = _val(er, "company_number")
            if num: parts.append(f"CH {num}")
            frn = _val(er, "fca_firm_reference")
            if frn: parts.append(f"FCA FRN {frn}")
            return " - ".join(parts)

        if node_name == "research_planner":
            plan = node_update.get("research_plan")
            dims = _val(plan, "dimensions_to_investigate") or []
            if isinstance(dims, list) and dims:
                names = [str(getattr(d, "value", d)) for d in dims[:5]]
                return f"{len(dims)} dimensions: {', '.join(names)}"
            return ""

        if node_name == "source_selector":
            selection = node_update.get("source_selection")
            source_map = _val(selection, "dimension_to_connector") or {}
            if isinstance(source_map, dict):
                noun = "route" if len(source_map) == 1 else "routes"
                return f"{len(source_map)} source {noun} selected"
            return ""

        if node_name == "parallel_source_collector":
            collected = node_update.get("collected_dimensions_this_pass") or []
            if isinstance(collected, list) and collected:
                noun = "dimension" if len(collected) == 1 else "dimensions"
                detail = f"Collected {len(collected)} {noun} in parallel"
                footprint = node_update.get("source_footprint_this_pass") or {}
                if isinstance(footprint, dict):
                    connectors = sorted({
                        str(connector)
                        for sources in footprint.values()
                        if isinstance(sources, list)
                        for connector in sources
                    })
                    if connectors:
                        detail += f" via {', '.join(connectors)}"
                return detail
            return ""

        if node_name == "news_classifier":
            classification = node_update.get("news_classification")
            classified = _val(classification, "classifications") or []
            confidence = _val(classification, "confidence")
            if isinstance(classified, list) and classified:
                noun = "signal" if len(classified) == 1 else "signals"
                detail = f"Classified {len(classified)} news {noun}"
                if confidence is not None:
                    detail += f" at {round(float(confidence) * 100)}% confidence"
                return detail
            evidence = node_update.get("evidence_by_dimension") or {}
            news = evidence.get("news_signals") if isinstance(evidence, dict) else None
            status = _val(news, "raw_data", "classification_status")
            if status == "failed":
                return "News classification unavailable"
            return "No news candidates to classify"

        if node_name == "evidence_critic":
            crit = node_update.get("evidence_critique")
            score = _val(crit, "overall_quality_score")
            if score is not None:
                return f"Evidence quality {round(float(score) * 100)}%"
            return ""

        if node_name == "gap_detector":
            gap = node_update.get("gap_detection")
            gs = _val(gap, "gap_score")
            missing = _val(gap, "missing_dimensions") or []
            if gs is not None:
                pct = round(float(gs) * 100)
                if isinstance(missing, list) and missing:
                    names = [str(getattr(d, "value", d)) for d in missing]
                    return f"Gap score {pct}% -- missing: {', '.join(names)}"
                return f"Gap score {pct}% -- all dimensions covered"
            return ""

        if node_name == "followup_planner":
            fp = node_update.get("followup_plan")
            iterate = _val(fp, "should_iterate")
            rationale = str(_val(fp, "rationale") or "")
            if iterate:
                return f"Iterating -- {rationale[:80]}"
            return "No further iteration needed"

        if node_name == "contradiction_detector":
            cd = node_update.get("contradiction_detection")
            count = _val(cd, "contradiction_count")
            if count is not None:
                n = int(count)
                return f"{n} contradiction{'s' if n != 1 else ''} found" if n else "No contradictions found"
            return ""

        if node_name == "synthesis":
            brief = node_update.get("due_diligence_brief")
            rl = _val(brief, "overall_risk_level")
            conf = _val(brief, "overall_confidence")
            if rl:
                conf_s = f", {round(float(conf) * 100)}% confidence" if conf else ""
                return f"Risk level: {str(rl).upper()}{conf_s}"
            return ""

    except Exception:
        pass
    return ""

async def _sse_generator(query: str) -> AsyncGenerator[str, None]:
    def _event(name: str, payload: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(payload)}\n\n"

    errors: list[str] = []
    brief_dict = None

    try:
        graph = get_research_graph()
        initial = create_initial_state(query)

        # astream(mode="updates") yields {node_name: state_update} after each node.
        # Accumulate updates to reconstruct the full final state without a second invoke.
        full_state: dict = dict(initial)

        with langsmith_tracing_context():
            async for chunk in graph.astream(initial, stream_mode="updates"):
                for node_name, node_update in chunk.items():
                    # Merge update into accumulated state
                    if isinstance(node_update, dict):
                        full_state.update(node_update)
                        # Collect any errors the node reported
                        for e in node_update.get("errors") or []:
                            msg = e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            errors.append(msg)

                    # Emit SSE tick for recognised graph nodes
                    if node_name in _GRAPH_NODES:
                        detail = _extract_node_detail(
                            node_name,
                            node_update if isinstance(node_update, dict) else {},
                        )
                        yield _event("node_complete", {"node": node_name, "detail": detail})

        # Extract the brief from the accumulated final state
        brief_obj = full_state.get("due_diligence_brief")
        if brief_obj is not None:
            if hasattr(brief_obj, "model_dump"):
                brief_dict = brief_obj.model_dump(mode="json")
            elif isinstance(brief_obj, dict):
                brief_dict = brief_obj

    except Exception as exc:
        yield _event("error", {"error": str(exc)})
        return

    yield _event("result", {"brief": brief_dict, "errors": errors})
    yield _event("done", {})


# ---------------------------------------------------------------------------
# Follow-up chat endpoint
# Uses get_llm() -- same factory as all agents -- so the provider API key is
# read from settings and passed explicitly, not looked up in os.environ.
# (pydantic-settings loads .env into Settings but does NOT inject into
#  os.environ, so bare init_chat_model() calls without api_key= would 500
#  for any provider whose SDK reads the key from the environment directly.)
# ---------------------------------------------------------------------------

_CHAT_SYSTEM = (
    "You are a UK company due-diligence assistant. Answer the user's question "
    "using only the provided due diligence brief. Be concise and specific. If "
    "the brief does not contain the answer, say so plainly rather than "
    "speculating."
)


@router.post("/research/chat", response_model=ChatResponse, tags=["research"])
async def research_chat(req: ChatRequest) -> ChatResponse:
    """Answer a follow-up question about an existing brief with one LLM call.

    No graph re-run: the previously computed brief is passed back in and used
    as the sole context for the answer.
    """
    llm = get_llm()
    brief_context = json.dumps(req.brief, default=str)[:12000]
    messages = [
        SystemMessage(content=_CHAT_SYSTEM),
        HumanMessage(
            content=(
                f"Due diligence brief (JSON):\n{brief_context}\n\n"
                f"Question: {req.question}"
            )
        ),
    ]
    try:
        result = await llm.ainvoke(messages)
        answer = getattr(result, "content", None) or str(result)
    except Exception as e:  # never 500 the UI chat strip
        answer = f"Unable to answer from the current brief: {e}"
    return ChatResponse(answer=answer)
