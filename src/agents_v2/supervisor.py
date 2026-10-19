"""
The supervisor: a LangGraph graph over the MCP server.

    load_case ──> plan ──> specialist (one per chosen agent, in parallel) ──> assemble

load_case  get_report_section(summary) over MCP, or find_company + prescreen_company for a new case.
plan       eligibility is code (specialists.eligible); one model call may drop an eligible specialist (with a
           reason) and give each a focus line. It can't add one. If the model is down, every eligible one runs.
specialist each runs its own tool loop and is checked by the guardrail.
assemble   code, not a model. The route, its meaning and the gaps are copied from the pre-screen word for word;
           agent text goes underneath, each section labelled with who wrote it and how it was checked.

The route is read once, at load_case, and never written. The assembled review carries it unchanged, and a
test checks that for every demo case.
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.agents_v2.model import AgentModel, ModelError
from src.agents_v2.specialists import SPECIALISTS, Ctx, eligible, run_specialist, skipped
from src.agents_v2.toolbox import Toolbox

SUPERVISOR_TOOLS = frozenset({"find_company", "prescreen_company", "get_report_section"})

PLAN_SYSTEM = """You coordinate specialist assistants for a commercial insurance underwriter reviewing one
pre-screened company. The pre-screen's written rules have already set the route; nobody changes it.

You get the case summary and the specialists that are eligible (each has something to work on). Decide which
to run. Run a specialist unless its work would clearly add nothing the summary doesn't already say; if you
drop one, say why in one sentence. You can't add a specialist that isn't listed. Give each one you run a
focus: one sentence on what matters most in this case.

Reply as JSON: {"run": ["name", ...], "focus": {"name": "..."}, "dropped": {"name": "why"}}"""


class State(TypedDict, total=False):
    report_id: str
    company_query: str
    proposal: dict[str, Any]
    summary: dict[str, Any]
    stop: str
    plan: dict[str, Any]
    sections: Annotated[list[dict[str, Any]], operator.add]
    review: dict[str, Any]


def build_graph(toolbox: Toolbox, model: AgentModel, judge_model: AgentModel | None):
    trace = toolbox.trace

    async def load_case(state: State) -> State:
        rid = state.get("report_id") or ""
        if not rid:
            found = await toolbox.call("supervisor", "find_company", {"query": state.get("company_query", "")},
                                       SUPERVISOR_TOOLS)
            if found.get("tool_error") or not found.get("resolved"):
                return {"stop": found.get("tool_error") or "The company wasn't resolved to one match. "
                        "A person must choose from the candidates.", "summary": {"candidates": found.get("candidates")}}
            args = {"company_number": found["company_number"], **(state.get("proposal") or {})}
            summary = await toolbox.call("supervisor", "prescreen_company", args, SUPERVISOR_TOOLS)
        else:
            summary = await toolbox.call("supervisor", "get_report_section", {"report_id": rid, "section": "summary"},
                                         SUPERVISOR_TOOLS)
        if summary.get("tool_error"):
            return {"stop": summary["tool_error"], "summary": {}}
        return {"summary": summary, "report_id": summary.get("report_id", rid)}

    async def plan(state: State) -> State:
        summary = state["summary"]
        can = eligible(summary)
        result: dict[str, Any] = {"eligible": can, "not_run": skipped(summary), "focus": {}, "dropped": {},
                                  "planned_by": "rules"}
        if not can:
            result["run"] = []
            return {"plan": result}
        result["run"] = list(can)
        if not model.available:
            result["planned_by"] = "rules (no model available, so every eligible specialist runs)"
            return {"plan": result}
        user = ("CASE SUMMARY:\n" + json.dumps({k: summary.get(k) for k in (
                    "company", "route", "route_reasons", "couldnt_check", "flags", "broker_questions")}, default=str)
                + "\n\nELIGIBLE SPECIALISTS:\n" + json.dumps(
                    {n: {"does": SPECIALISTS[n].title, "why_eligible": why} for n, why in can.items()}))
        try:
            out = await model.json("supervisor", PLAN_SYSTEM, user, purpose="plan", max_output_tokens=800)
        except ModelError as err:
            result["planned_by"] = f"rules (planner failed: {err}; every eligible specialist runs)"
            return {"plan": result}
        chosen = [n for n in out.get("run") or [] if n in can]
        dropped = {n: str((out.get("dropped") or {}).get(n) or "no reason given") for n in can if n not in chosen}
        unasked = [n for n in out.get("run") or [] if n not in can]
        result.update(run=chosen, dropped=dropped, planned_by="supervisor model",
                      focus={n: str(v)[:300] for n, v in (out.get("focus") or {}).items() if n in chosen})
        if unasked:
            result["refused_additions"] = unasked  # the model tried to add an ineligible specialist
        return {"plan": result}

    def fan_out(state: State):
        if state.get("stop"):
            return "assemble"
        runs = (state.get("plan") or {}).get("run") or []
        if not runs:
            return "assemble"
        return [Send("specialist", {"name": n, "focus": state["plan"]["focus"].get(n, ""),
                                    "report_id": state["report_id"], "summary": state["summary"]}) for n in runs]

    async def specialist(payload: dict[str, Any]) -> State:
        ctx = Ctx(report_id=payload["report_id"], summary=payload["summary"], toolbox=toolbox, model=model,
                  judge_model=judge_model)
        return {"sections": [await run_specialist(SPECIALISTS[payload["name"]], ctx, payload.get("focus", ""))]}

    def assemble(state: State) -> State:
        s = state.get("summary") or {}
        order = list(SPECIALISTS)
        sections = sorted(state.get("sections") or [], key=lambda x: order.index(x["agent"]))
        review = {
            "stopped": state.get("stop", ""),
            "company": s.get("company"),
            "route": s.get("route"),
            "route_meaning": s.get("route_meaning"),
            "route_reasons": s.get("route_reasons") or [],
            "couldnt_check": s.get("couldnt_check") or [],
            "rules_version": s.get("rules_version"),
            "plan": state.get("plan") or {},
            "sections": sections,
            "decision_needed_from": s.get("decision_needed_from") or "a named underwriter, in the pre-screen UI",
        }
        return {"review": review}

    g = StateGraph(State)
    g.add_node("load_case", load_case)
    g.add_node("plan", plan)
    g.add_node("specialist", specialist)
    g.add_node("assemble", assemble)
    g.add_edge(START, "load_case")
    g.add_conditional_edges("load_case", lambda st: "assemble" if st.get("stop") else "plan", ["plan", "assemble"])
    g.add_conditional_edges("plan", fan_out, ["specialist", "assemble"])
    g.add_edge("specialist", "assemble")
    g.add_edge("assemble", END)
    return g.compile()
