"""
The three specialists, and the small loop they share.

Each specialist is a bounded tool-using loop: the model asks for one MCP tool call at a time (from its own
allowlist), sees the result, and finishes with a JSON answer. Then the guardrail checks what it wrote. A
blocked answer gets one rewrite with the problems listed; blocked again, it's withheld and the reason shown.

Who runs is decided by the case, in code (`eligible`): a specialist with nothing to do never starts, and the
broker email never starts on a compliance hold. The supervisor can drop an eligible specialist, never add one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agents_v2 import guardrail
from src.agents_v2.model import AgentModel, ModelError
from src.agents_v2.toolbox import Toolbox, ToolRefused

DIRECTOR_RULES = {"director_insolvent_links", "phoenix_pattern", "director_disqualified", "disq_possible"}
NEWS_RULES = {"media_high", "media_medium", "media_old", "media_adverse"}

SHARED_RULES = """Rules that always apply:
- The route was decided by written rules. You may explain it; never suggest a different route, never say the
  case is safe to quote, should be declined, or similar. A named underwriter decides.
- A check that "couldn't be checked" found nothing because it didn't run. Never describe it as clear or clean.
- Use only facts from the tool results you are given. If something isn't there, say it isn't known.
- Copy company numbers, dates and amounts exactly as the tool results give them.
- Plain British English. Short sentences. No headings, no bullet symbols inside strings."""

LOOP_FORMAT = """Each reply is ONE JSON object, either
  {"call": {"tool": "<tool name>", "args": {...}}, "why": "<a few words>"}
to call one tool (you'll get its result back), or
  {"final": <your answer in the format below>}
when you're done. You may make at most {steps} tool calls; after that you must give "final"."""


@dataclass
class Spec:
    name: str
    title: str
    audience: str                        # "internal" | "external"
    tools: frozenset[str]
    max_steps: int
    system: str
    final_format: str
    render: Callable[[dict[str, Any]], str]
    task: Callable[[dict[str, Any], str], str]


@dataclass
class Ctx:
    report_id: str
    summary: dict[str, Any]
    toolbox: Toolbox
    model: AgentModel
    judge_model: AgentModel | None = None
    revisions: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Eligibility: decided by the case, in code
# ---------------------------------------------------------------------------

def eligible(summary: dict[str, Any]) -> dict[str, str]:
    """name -> why it should run. A specialist not listed has nothing to do (the reason is in `skipped`)."""
    flags = summary.get("flags") or []
    out: dict[str, str] = {}
    director_flags = [f for f in flags if f.get("rule_id") in DIRECTOR_RULES]
    if director_flags:
        out["director_history"] = f"{len(director_flags)} management finding(s) about directors' other companies"
    news_flags = [f for f in flags if (f.get("rule_id") or "").startswith("media")]
    if news_flags:
        out["news"] = f"{len(news_flags)} news finding(s) on the case"
    if summary.get("route") != "compliance_hold" and summary.get("broker_questions"):
        out["broker_email"] = f"{len(summary['broker_questions'])} question(s) for the broker"
    return out


def skipped(summary: dict[str, Any]) -> dict[str, str]:
    """name -> why it didn't run, for every specialist that isn't eligible. Shown to the underwriter."""
    gaps = {g.get("check_id") for g in summary.get("couldnt_check") or []}
    out = {}
    can = eligible(summary)
    if "director_history" not in can:
        out["director_history"] = ("Directors' other companies couldn't be checked, so there's nothing to narrate."
                                   if "directors_insolvency" in gaps else
                                   "No finding about directors' other companies.")
    if "news" not in can:
        out["news"] = ("News couldn't be checked, so there's nothing to read. That is not a clear result."
                       if "media" in gaps else "No news finding on the case.")
    if "broker_email" not in can:
        out["broker_email"] = ("Held: compliance hold. Nothing goes to the broker until compliance clears the case."
                               if summary.get("route") == "compliance_hold" else "No questions for the broker.")
    return out


# ---------------------------------------------------------------------------
# The three specialists
# ---------------------------------------------------------------------------

def _case_brief(summary: dict[str, Any]) -> str:
    keep = {k: summary.get(k) for k in ("report_id", "company", "route", "route_meaning", "route_reasons",
                                         "couldnt_check", "flags", "proposal_differences", "broker_questions")}
    return json.dumps(keep, default=str)


def _render_directors(final: dict[str, Any]) -> str:
    parts = []
    for d in final.get("directors") or []:
        if isinstance(d, dict) and d.get("story"):
            parts.append(f"{d.get('name', 'Director')}: {d['story']}".strip())
    unknown = [str(q) for q in final.get("not_known") or [] if q]
    if unknown:
        parts.append("Not known from the record: " + "; ".join(unknown))
    return "\n\n".join(parts)


def _render_news(final: dict[str, Any]) -> str:
    parts = []
    for it in final.get("items") or []:
        if not isinstance(it, dict):
            continue
        standing = {"established": "On record", "alleged": "Alleged", "unclear": "Unclear"}.get(
            str(it.get("standing", "")).lower(), "Unclear")
        line = f"{standing}: {it.get('what_happened') or it.get('title', '')}".strip()
        if it.get("what_is_alleged"):
            line += f" Alleged, not established: {it['what_is_alleged']}"
        if str(it.get("about_this_company", "")).lower() in ("possibly", "unsure", "no"):
            line += f" (About this company: {it.get('about_this_company')}.)"
        parts.append(line)
    if final.get("summary"):
        parts.append(str(final["summary"]))
    return "\n\n".join(parts)


def _render_email(final: dict[str, Any]) -> str:
    subject = str(final.get("subject") or "").strip()
    body = str(final.get("body") or "").strip()
    return (f"Subject: {subject}\n\n{body}" if subject else body).strip()


DIRECTOR_HISTORY = Spec(
    name="director_history",
    title="Directors' other companies",
    audience="internal",
    tools=frozenset({"get_report_section", "company_record", "gazette_notices"}),
    max_steps=6,
    system=("You explain, for a commercial underwriter, the history behind a flag about a director's other "
            "companies. The pre-screen has already found the flag; your job is the story behind it: what each "
            "linked company was, what happened to it and when, and whether the director was in post near the end. "
            "Look up the linked companies named in the flag (company_record, gazette_notices). Keep each director "
            "to three or four sentences. Say what the record doesn't show (for example, the reason a company failed)."
            "\n\n" + SHARED_RULES),
    final_format=('{"directors": [{"name": "...", "story": "..."}], '
                  '"not_known": ["things the record doesn\'t show"]}'),
    render=_render_directors,
    task=lambda s, focus: ("Flags about directors on this case:\n" + json.dumps(
        [f for f in s.get("flags") or [] if f.get("rule_id") in DIRECTOR_RULES], default=str)
        + (f"\n\nThe supervisor asks you to focus on: {focus}" if focus else "")),
)

NEWS = Spec(
    name="news",
    title="News: what happened and what is alleged",
    audience="internal",
    tools=frozenset({"get_report_section"}),
    max_steps=3,
    system=("You read the news results a pre-screen found about a company and separate, for an underwriter, what "
            "has happened (on record: a fine, a court result, a filed insolvency) from what is alleged (claims, "
            "charges, investigations, reports). Read the news section of the report. Only cover items the "
            "pre-screen marked as about the company or possibly about it, most serious first, at most six. "
            "Say when it isn't clear the item is about this company rather than another with a similar name."
            "\n\n" + SHARED_RULES),
    final_format=('{"items": [{"title": "...", "url": "...", "what_happened": "...", "what_is_alleged": "...", '
                  '"standing": "established|alleged|unclear", "about_this_company": "yes|possibly|no"}], '
                  '"summary": "one or two sentences"}'),
    render=_render_news,
    task=lambda s, focus: ("Read the news for this case and separate facts from allegations."
                           + (f"\n\nThe supervisor asks you to focus on: {focus}" if focus else "")),
)

BROKER_EMAIL = Spec(
    name="broker_email",
    title="Draft email to the broker",
    audience="external",
    tools=frozenset({"get_report_section"}),
    max_steps=3,
    system=("You draft an email from a commercial underwriter to the insurance broker, asking the pre-screen's "
            "broker questions. Read the broker_questions section; you may read proposal for context. Ask every "
            "question given and no others; you may group or reword them, keeping their meaning. Polite and "
            "direct, no accusation. The email goes outside the insurer, so never mention: sanctions, compliance, "
            "routes or referral, internal rules, scores, this tool, or AI. Don't say whether cover will be "
            "offered. End with \"Kind regards,\" and \"[Underwriter name]\".\n\n" + SHARED_RULES),
    final_format='{"subject": "...", "body": "..."}',
    render=_render_email,
    task=lambda s, focus: (f"Draft the email for {((s.get('company') or {}).get('name') or 'this company')}."
                           + (f"\n\nThe supervisor asks you to focus on: {focus}" if focus else "")),
)

SPECIALISTS: dict[str, Spec] = {s.name: s for s in (DIRECTOR_HISTORY, NEWS, BROKER_EMAIL)}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _fix_args(tool: str, args: dict[str, Any], report_id: str) -> dict[str, Any]:
    """Agents work on one case: any report_id they pass is replaced by the case's own."""
    args = dict(args or {})
    if tool in ("get_report_section", "watch_company"):
        args["report_id"] = report_id
    if tool in ("company_record", "gazette_notices") and isinstance(args.get("company_number"), str):
        args["company_number"] = re.sub(r"\s+", "", args["company_number"]).upper()
    return args


async def run_specialist(spec: Spec, ctx: Ctx, focus: str = "") -> dict[str, Any]:
    """Run one specialist to a checked result. Never raises: failures are part of the result."""
    trace = ctx.toolbox.trace
    evidence: list[Any] = [ctx.summary]
    transcript: list[str] = []
    system = (spec.system + "\n\n" + LOOP_FORMAT.replace("{steps}", str(spec.max_steps))
              + "\n\nFinal answer format:\n" + spec.final_format
              + "\n\nTools you may call: " + ", ".join(sorted(spec.tools))
              + ". get_report_section takes section: summary | checks | proposal | broker_questions | timeline | "
                "news | company | file_note. company_record takes company_number. gazette_notices takes "
                "company_number and optional company_name.")
    head = f"CASE (from prescreen summary):\n{_case_brief(ctx.summary)}\n\nTASK:\n{spec.task(ctx.summary, focus)}"
    result: dict[str, Any] = {"agent": spec.name, "title": spec.title, "audience": spec.audience,
                              "status": "error", "text": "", "steps": 0, "revisions": 0}
    final: dict[str, Any] | None = None
    try:
        for step in range(spec.max_steps + 1):
            user = head + ("\n\n" + "\n\n".join(transcript) if transcript else "")
            if step == spec.max_steps:
                user += "\n\nNo tool calls left. Give your final answer now."
            reply = await ctx.model.json(spec.name, system, user, purpose=f"step {step + 1}")
            if isinstance(reply.get("final"), dict):
                final = reply["final"]
                break
            call = reply.get("call") if isinstance(reply.get("call"), dict) else None
            if call is None or step == spec.max_steps:
                transcript.append("SYSTEM: Reply with {\"final\": ...} now.")
                continue
            tool, args = str(call.get("tool", "")), call.get("args") or {}
            result["steps"] += 1
            try:
                out = await ctx.toolbox.call(spec.name, tool, _fix_args(tool, args, ctx.report_id), spec.tools)
            except ToolRefused as err:
                out = {"tool_error": f"{err}. Use only: {', '.join(sorted(spec.tools))}."}
            evidence.append(out)
            transcript.append(f"TOOL {tool}({json.dumps(_fix_args(tool, args, ctx.report_id))}) RESULT:\n"
                              + json.dumps(out, default=str)[:12000])
        if final is None:
            result.update(status="error", error="the agent didn't give a final answer")
            return result
    except ModelError as err:
        result.update(status="error", error=f"model: {err}")
        return result
    except Exception as err:  # noqa: BLE001 - a broken specialist mustn't break the review
        result.update(status="error", error=f"{type(err).__name__}: {str(err)[:200]}")
        return result

    evidence_text = json.dumps(evidence, default=str)
    text = spec.render(final)
    for attempt in range(ctx.revisions + 1):
        with trace.timed("guardrail", "check", spec.name) as span:
            verdict = await guardrail.check(text, agent=spec.name, audience=spec.audience, summary=ctx.summary,
                                            evidence=evidence_text, judge_model=ctx.judge_model)
            span.update(passed=verdict["passed"], violations=[v["rule"] for v in verdict["violations"]],
                        judge=verdict["judge"])
        if verdict["passed"]:
            result.update(status="ok", text=text, data=final, guardrail=verdict)
            return result
        result.setdefault("blocked_drafts", []).append({"text": text, "violations": verdict["violations"]})
        if attempt == ctx.revisions or not text.strip() and spec.audience == "external":
            break
        result["revisions"] += 1
        problems = "\n".join(f"- {v['rule']}: {v['detail']}" + (f" (\"{v['quote']}\")" if v["quote"] else "")
                             for v in verdict["violations"])
        user = (head + "\n\nEVIDENCE YOU GATHERED:\n" + evidence_text[:20000] + "\n\nYOUR ANSWER:\n"
                + json.dumps(final) + "\n\nA checker blocked it for these problems:\n" + problems
                + "\n\nRewrite the final answer so none of these apply. Remove anything you can't support. "
                  "Reply with {\"final\": ...} only.")
        try:
            reply = await ctx.model.json(spec.name, system, user, purpose="revise")
        except ModelError as err:
            result.update(status="error", error=f"model: {err}")
            return result
        final = reply.get("final") if isinstance(reply.get("final"), dict) else final
        text = spec.render(final)
    result.update(status="blocked", text="", data=None, guardrail=verdict,
                  withheld_reason="; ".join(sorted({v["detail"] for v in verdict["violations"]}))[:400])
    return result
