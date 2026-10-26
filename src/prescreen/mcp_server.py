"""
prescreen/mcp_server.py
The pre-screen as an MCP server, so any agent can use it as a tool.

Design rules (these are the point, not decoration):

1. The route is decided by the rules, never by the calling agent. Tools return
   it; no tool lets an agent change it.
2. No tool records an underwriting decision. Proceed / refer / decline belongs
   to a named person in the UI. An agent can prepare the case; it can't sign it.
3. "Couldn't check" is returned explicitly, with the reason, on every result
   that has one. An agent must never be able to read absence as "clear".
4. Outputs are bounded. A full report is ~30-60 KB of JSON; prescreen_company
   returns a summary of a few KB and get_report_section returns one part.
5. Lookup tools (company record, Gazette, sanctions, FCA) return evidence, not
   verdicts, for specialist agents that need one source.

Transports:
  - stdio:  python -m src.prescreen.mcp_server        (Claude Desktop / Code, local agents)
  - HTTP:   mounted on the API at /mcp                 (remote agents; see api/app.py)
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from src.prescreen import casefile, demo_cases
from src.prescreen.engine import STAGES, CompanyNotFound, SourceDown, run_prescreen
from src.prescreen.checks import FCA_AUTHORISED, _went_insolvent as has_insolvency_record, insolvency_summary
from src.prescreen.identity import normalise_name, resolve
from src.prescreen.models import COVER_LINES, PreScreenRequest
from src.prescreen.rules import RULES_VERSION, THRESHOLDS
from src.prescreen.service import build_services, demo_mode, load_report, save_report, watch_from_report
from src.prescreen.sources.http import NOT_FOUND, USER_AGENT, SourceError
from src.prescreen.sources.sanctions import Screened, SanctionsList, screen
from src.prescreen.store import get_store

ROUTE_MEANING = {
    "note": "No referral: nothing checked needs one. Read anything marked for the file, then quote as normal.",
    "refer": "Refer: needs an answer from the broker or a senior underwriter before quoting.",
    "outside_appetite": "Outside appetite: decline unless a senior underwriter overrides with reasons on file.",
    "compliance_hold": ("Compliance hold: potential sanctions match. Do not quote, bind or pay, and do not tell the "
                        "broker or client why. Compliance decides."),
}

INSTRUCTIONS = """UK Company Pre-Screen: public-record checks on a UK company for a commercial insurance underwriter.

How to use it:
1. find_company with the name or number from the proposal. If it isn't resolved, show the candidates to a human;
   never pick one yourself.
2. prescreen_company with the number and whatever the proposal says (trade, turnover, years trading, directors,
   cover lines). It returns a recommended route decided by written rules, the reasons, what couldn't be checked,
   and questions for the broker.
3. Read `couldnt_check` before anything else you write. A check that couldn't run is NOT a clear result; say so.
4. Use get_report_section for detail. Use the lookup tools only for follow-up questions on one source.

Hard rules for agents:
- Don't restate the route as something else. You may explain it; you may not upgrade or downgrade it.
- On a compliance_hold, don't draft anything for the broker or client, and don't include sanctions detail in any
  message meant to leave the firm.
- You can't record an underwriting decision with these tools; a named underwriter does that in the UI.
"""

server: MCPServer = MCPServer(
    name="uk-company-prescreen",
    title="UK Company Pre-Screen",
    description="Public-record pre-screen of UK companies for commercial insurance underwriters.",
    instructions=INSTRUCTIONS,
    version=RULES_VERSION,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES_REPORT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                open_world_hint=True)
IDEMPOTENT_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                   open_world_hint=False)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT})


def _limit() -> None:
    """Share the API's hourly run limit, so MCP can't be used to get round it."""
    from fastapi import HTTPException

    from src.api.prescreen_routes import _limit as api_limit
    try:
        api_limit()
    except HTTPException as err:
        raise ToolError(str(err.detail)) from err


def _gap(c: dict[str, Any]) -> dict[str, Any]:
    out_of_scope = casefile.is_out_of_scope(c)
    return {"check": c["title"], "check_id": c["check_id"], "group": casefile.GROUP_TITLES.get(c.get("group", ""), ""),
            "kind": "not_covered_by_this_tool" if out_of_scope else "source_failed_or_incomplete",
            "reason": c.get("not_checked_reason") or c.get("summary", ""),
            "critical": bool(c.get("critical")),
            "means": ("Not checked. This is not a clear result." +
                      (" This tool doesn't do this check; the underwriter uses another service." if out_of_scope else "") +
                      (" A critical check: the route can't be 'note' while it's missing." if c.get("critical") else ""))}


def _news_counts(payload: dict[str, Any]) -> dict[str, int]:
    """What the news reading found, flags and notes alike. A serious item can be a note (an FCA action already
    found by the FCA check), and an agent must still see it (Monzo, live: "no news finding" under a £21m fine)."""
    media = payload.get("media") or []
    adverse = [m for m in media if m.get("category") not in ("", "neutral", None) and m.get("severity") not in ("low", None)]
    return {"results": len(media),
            "adverse_about_this_company": sum(m.get("about_this_company") in ("yes", "unsure") for m in adverse),
            "adverse_about_related_company": sum(m.get("about_this_company") == "related" for m in adverse),
            "not_reviewed": sum(m.get("about_this_company") == "not_reviewed" for m in media)}


def summarise(payload: dict[str, Any]) -> dict[str, Any]:
    """The few KB an agent needs to act on a report. Everything else is one get_report_section away."""
    checks = payload.get("checks") or []
    hold = payload.get("route") == "compliance_hold"
    flags = []
    for c in checks:
        for f in c.get("findings") or []:
            if f.get("route") == "note":
                continue
            text = f.get("text", "")
            if f.get("rule_id") in casefile.COMPLIANCE_ONLY:
                text = "Potential sanctions match. Details are in the report for compliance only."
            flags.append({"check": c["title"], "group": casefile.GROUP_TITLES.get(c.get("group", ""), ""),
                          "rule_id": f.get("rule_id"), "route": f.get("route"), "text": text,
                          "fraud_indicators": f.get("tags") or []})
    counts: dict[str, int] = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    company = payload.get("company") or {}
    return {
        "report_id": payload.get("report_id"),
        "data": (payload.get("meta") or {}).get("data", ""),
        "company": {"number": payload.get("company_number"), "name": payload.get("company_name"),
                    "status": company.get("status", ""), "status_detail": company.get("status_detail", ""),
                    "incorporated": company.get("incorporated", ""), "registered_office": company.get("registered_office", ""),
                    "sic": company.get("sic", [])},
        "identity_reminder": "Check the number and address against the proposal before relying on this.",
        "route": payload.get("route"),
        "route_meaning": ROUTE_MEANING.get(payload.get("route", ""), ""),
        "route_reasons": [r if not (hold and casefile._is_sanctions_text(r, checks))
                          else "Potential sanctions match (details for compliance only)."
                          for r in (payload.get("route_reasons") or [])[:8]],
        "couldnt_check": [_gap(c) for c in checks if c.get("status") == "couldnt_check"],
        "flags": flags,
        "check_counts": counts,
        "coverage": payload.get("coverage") or casefile.coverage(checks),
        "news": _news_counts(payload),
        "proposal_differences": [r for r in payload.get("proposal_vs_record") or [] if r.get("verdict") in ("differs", "unknown")],
        "broker_questions": [] if hold else [q["question"] for q in payload.get("broker_questions") or []],
        "broker_questions_held": bool(hold and payload.get("broker_questions")),
        "rules_version": payload.get("rules_version"),
        "model_calls": payload.get("llm_calls", 0),
        "decision_needed_from": "a named underwriter, in the pre-screen UI",
    }


# ---------------------------------------------------------------------------
# Core workflow tools
# ---------------------------------------------------------------------------

@server.tool(annotations=READ_ONLY)
async def find_company(query: str) -> dict[str, Any]:
    """Resolve a company name or Companies House number to one company.

    Returns resolved=true with the number when there's exactly one match (a number, or one active company with
    that exact registered name). Otherwise returns candidates: show them to a human and let them choose.
    """
    async with _client() as client:
        services, _ = build_services(client)
        result = await resolve(services.ch, query)
    out = result.model_dump(mode="json")
    out["candidates"] = out.get("candidates", [])[:10]
    return out


@server.tool(annotations=WRITES_REPORT)
async def prescreen_company(
    company_number: str,
    ctx: Context,
    declared_trade: str = "",
    declared_turnover_gbp: int | None = None,
    declared_years_trading: int | None = None,
    declared_directors: str = "",
    cover_lines: list[Literal["property", "liability", "professional_indemnity", "management_liability",
                              "financial_institutions", "cyber", "motor_fleet"]] | None = None,
    expects_fca_authorisation: bool = False,
) -> dict[str, Any]:
    """Run a full pre-screen on one company and save the report.

    Pass what the broker's proposal says so it can be compared with the public record. Returns a summary: the
    recommended route (decided by rules), its reasons, what couldn't be checked, flags, proposal differences and
    broker questions. Takes 10-60 seconds on live data. Use get_report_section(report_id, ...) for detail.
    """
    try:
        req = PreScreenRequest(company_number=company_number, declared_trade=declared_trade,
                               declared_turnover_gbp=declared_turnover_gbp,
                               declared_years_trading=declared_years_trading, declared_directors=declared_directors,
                               cover_lines=list(cover_lines or []), expects_fca_authorisation=expects_fca_authorisation)
    except ValueError as err:
        msgs = [e.get("msg", "") for e in getattr(err, "errors", lambda: [])()] or [str(err)]
        raise ToolError("; ".join(m.removeprefix("Value error, ") for m in msgs)) from err
    _limit()
    done: list[str] = []
    stages = [s for s, _ in STAGES]

    def progress(stage: str, state: str, detail: str) -> None:
        if state in ("done", "error", "skip") and stage not in done:
            done.append(stage)
            label = dict(STAGES).get(stage, stage)
            # Progress is a notification; the engine's callback is sync, so schedule it.
            import asyncio
            try:
                asyncio.get_running_loop().create_task(ctx.report_progress(len(done), len(stages), label))
            except RuntimeError:
                pass

    async with _client() as client:
        services, meta = build_services(client, req.company_number)
        try:
            report = await run_prescreen(req, services, progress=progress)
        except CompanyNotFound as err:
            raise ToolError(str(err)) from err
        except SourceDown as err:
            raise ToolError(f"Couldn't start the pre-screen: {err}. Try again shortly.") from err
    meta["news_queries"] = services.news.queries
    meta["source"] = "mcp"
    try:
        payload = await save_report(report, meta)
    except Exception as err:  # noqa: BLE001 - a storage outage shouldn't lose the screen
        payload = report.model_dump(mode="json")
        payload["meta"] = {**meta, "not_saved": f"couldn't save the report ({type(err).__name__})"}
    out = summarise(payload)
    if (payload.get("meta") or {}).get("not_saved"):
        out["warning"] = "The report wasn't saved, so get_report_section won't find it."
    return out


SECTIONS = ("summary", "checks", "proposal", "broker_questions", "timeline", "news", "company", "file_note")


@server.tool(annotations=READ_ONLY)
async def get_report_section(
    report_id: str,
    section: Literal["summary", "checks", "proposal", "broker_questions", "timeline", "news", "company", "file_note"],
) -> dict[str, Any]:
    """Read one part of a saved report.

    summary: as prescreen_company returns. checks: every check with status, summary and findings.
    proposal: proposal vs public record. timeline: dated events. news: results and how each was read.
    company: the identity card. file_note: the internal plain-text note (not for the broker or client).
    """
    payload = await load_report(report_id)
    if payload is None:
        raise ToolError(f"No report {report_id}. Run prescreen_company first.")
    hold = payload.get("route") == "compliance_hold"
    if section == "summary":
        return summarise(payload)
    if section == "checks":
        out = []
        for c in payload.get("checks") or []:
            findings = [{"rule_id": f.get("rule_id"), "route": f.get("route"),
                         "text": ("Potential sanctions match (details for compliance only)."
                                  if f.get("rule_id") in casefile.COMPLIANCE_ONLY else f.get("text")),
                         "fraud_indicators": f.get("tags") or [],
                         "sources": [s.get("url") for s in f.get("sources") or [] if s.get("url")][:2]}
                        for f in c.get("findings") or []]
            sanctions_hit = any(f.get("rule_id") in casefile.COMPLIANCE_ONLY for f in c.get("findings") or [])
            out.append({"check_id": c["check_id"], "title": c["title"],
                        "group": casefile.GROUP_TITLES.get(c.get("group", ""), ""), "status": c["status"],
                        "summary": ("Potential sanctions match (details for compliance only)." if sanctions_hit
                                    else c.get("summary", "")), "not_checked_reason": c.get("not_checked_reason", ""),
                        "critical": c.get("critical", False), "findings": findings})
        return {"report_id": report_id, "checks": out,
                "statuses": "clear = looked, found nothing; info = worth reading; flag = changes the route; "
                            "couldnt_check = NOT checked (never treat as clear); not_applicable"}
    if section == "proposal":
        return {"report_id": report_id, "request": payload.get("request"), "rows": payload.get("proposal_vs_record") or []}
    if section == "broker_questions":
        if hold:
            return {"report_id": report_id, "questions": [], "held": True,
                    "why": "Compliance hold: nothing goes to the broker until compliance clears the case."}
        return {"report_id": report_id, "questions": payload.get("broker_questions") or [], "held": False}
    if section == "timeline":
        return {"report_id": report_id, "events": payload.get("timeline") or []}
    if section == "news":
        return {"report_id": report_id, "items": (payload.get("media") or [])[:25]}
    if section == "company":
        c = dict(payload.get("company") or {})
        c.pop("officer_names", None)
        c.pop("owner_names", None)
        return {"report_id": report_id, "company_number": payload.get("company_number"),
                "company_name": payload.get("company_name"), **c}
    return {"report_id": report_id, "internal_only": True, "text": casefile.file_note(payload)}


# ---------------------------------------------------------------------------
# Lookup tools: one source each, evidence not verdicts
# ---------------------------------------------------------------------------

def _err(source: str, err: Any) -> dict[str, Any]:
    plain = err.plain if isinstance(err, SourceError) else str(err)
    return {"source": source, "couldnt_check": True, "reason": plain,
            "means": "Not checked. This is not a clear result."}


@server.tool(annotations=READ_ONLY)
async def company_record(company_number: str) -> dict[str, Any]:
    """Companies House record for one company: profile, current officers, people with significant control and,
    when it has one, its insolvency record (case types and dates, plus the date the insolvency began as the
    pre-screen's director-link rule measures it). Raw evidence for follow-up questions; use prescreen_company
    for an assessment."""
    async with _client() as client:
        services, _ = build_services(client, company_number.upper())
        ch = services.ch
        try:
            profile = await ch.profile(company_number.upper())
        except SourceError as err:
            return _err("Companies House", err)
        if profile is NOT_FOUND:
            raise ToolError(f"Companies House has no company with number {company_number}.")
        out: dict[str, Any] = {"source": "Companies House", "profile": {
            k: profile.get(k) for k in ("company_number", "company_name", "company_status", "company_status_detail",
                                         "type", "date_of_creation", "sic_codes", "registered_office_address",
                                         "previous_company_names", "accounts", "confirmation_statement")}}
        for key, call in (("officers", ch.officers), ("pscs", ch.pscs)):
            try:
                r = await call(company_number.upper())
                items = r.get("items") if isinstance(r, dict) else []
                out[key] = [{k: i.get(k) for k in ("name", "officer_role", "appointed_on", "resigned_on", "kind",
                                                     "notified_on", "ceased_on", "natures_of_control", "nationality",
                                                     "country_of_residence")
                             if i.get(k) is not None} for i in (items or [])[:30]]
            except SourceError as err:
                out[key] = _err("Companies House", err)
        if has_insolvency_record(profile):
            try:
                out["insolvency"] = insolvency_summary(await ch.insolvency(company_number.upper()))
            except SourceError as err:
                out["insolvency"] = _err("Companies House", err)
        return out


@server.tool(annotations=READ_ONLY)
async def gazette_notices(company_number: str, company_name: str = "") -> dict[str, Any]:
    """Insolvency notices in The Gazette for one company (winding-up petitions, liquidations, administrations)."""
    async with _client() as client:
        services, _ = build_services(client, company_number.upper())
        if services.gazette is None:
            return _err("The Gazette", "not connected")
        try:
            notices = await services.gazette.insolvency_notices(company_number.upper(), company_name)
        except SourceError as err:
            return _err("The Gazette", err)
    return {"source": "The Gazette", "company_number": company_number.upper(), "notices": notices[:20],
            "note": "No notices means none were found for this number, not that the company is solvent."}


@server.tool(annotations=READ_ONLY)
async def sanctions_screen(name: str, is_person: bool = True, dob_year: int | None = None,
                           dob_month: int | None = None) -> dict[str, Any]:
    """Screen one name against the UK Sanctions List (FCDO). Give the date of birth for people when you have it:
    without it, a name match stays 'possible'. A possible match is not a confirmed match; refer it to compliance."""
    async with _client() as client:
        services, _ = build_services(client)
        sl = services.sanctions
        if not isinstance(sl, (SanctionsList, SourceError)):
            try:
                sl = await sl()
            except SourceError as err:
                sl = err
    if isinstance(sl, SourceError):
        return _err("UK Sanctions List", sl)
    hits = screen([Screened(name=name, role="queried", is_person=is_person, dob_year=dob_year, dob_month=dob_month)], sl)
    return {"source": "UK Sanctions List", "list_date": sl.fetched_at, "list_entries": sl.size,
            "result": "no_match" if not hits else max((h.strength for h in hits),
                                                       key=["cleared", "possible", "strong"].index),
            "hits": [{"listed_name": h.matched_name, "strength": h.strength, "reason": h.reason,
                      "list_id": h.entry.unique_id, "regime": h.entry.regime} for h in hits[:10]],
            "handling": "Any 'possible' or 'strong' result goes to compliance. Don't tell the broker or client."}


@server.tool(annotations=READ_ONLY)
async def fca_firm_search(name: str) -> dict[str, Any]:
    """Search the FCA Register for firms by name. Returns reference numbers (FRN) and status, not an assessment."""
    async with _client() as client:
        services, _ = build_services(client)
        try:
            target = normalise_name(name)
            firms = await services.fca.search_firms(
                name, want=lambda f: normalise_name(f["name"]) == target and f["status"].lower() in FCA_AUTHORISED)
        except SourceError as err:
            return _err("FCA Register", err)
    # The Register's own order is fuzzy; put exact name matches first so they survive the cut.
    firms.sort(key=lambda f: (normalise_name(f["name"]) != target, f["status"].lower() not in FCA_AUTHORISED))
    return {"source": "FCA Register", "firms": firms[:15], "exact_matches": sum(
                normalise_name(f["name"]) == target for f in firms),
            "search": getattr(services.fca, "last_result_info", {}),
            "note": "No firms found means no match on that name, not that the firm doesn't need authorisation."}


# ---------------------------------------------------------------------------
# Watching
# ---------------------------------------------------------------------------

@server.tool(annotations=IDEMPOTENT_WRITE)
async def watch_company(report_id: str) -> dict[str, Any]:
    """Add the company in a saved report to the watchlist. Its registers are re-checked every morning."""
    row = await watch_from_report(report_id)
    if row is None:
        raise ToolError(f"No report {report_id}.")
    return {k: v for k, v in row.items() if k != "snapshot"}


@server.tool(annotations=READ_ONLY)
async def list_changes(company_number: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Changes found by the daily re-check on watched companies, newest first."""
    rows = await get_store().list_changes(limit=max(1, min(limit, 100)),
                                          number=company_number.upper() if company_number else None)
    return {"changes": [{k: r.get(k) for k in ("company_number", "company_name", "detected_at", "kind", "severity",
                                                "summary", "seen", "report_id")} for r in rows]}


# ---------------------------------------------------------------------------
# Resources and a prompt
# ---------------------------------------------------------------------------

@server.resource("prescreen://rules", name="rules", title="Routes and thresholds", mime_type="application/json")
def rules_resource() -> str:
    import json
    return json.dumps({"rules_version": RULES_VERSION, "routes": ROUTE_MEANING, "thresholds": THRESHOLDS,
                       "draft": "Thresholds are draft judgement, not a cited rule."}, indent=1)


@server.resource("prescreen://checks", name="checks", title="What is checked, by underwriter question",
                 mime_type="application/json")
def checks_resource() -> str:
    import json
    groups = {g["id"]: {**g, "checks": []} for g in casefile.GROUPS}
    for check_id, gid in casefile.CHECK_GROUP.items():
        groups[gid]["checks"].append(check_id)
    return json.dumps({"groups": list(groups.values()), "cover_lines": COVER_LINES,
                       "fraud_indicators": {k: sorted(v) for k, v in casefile.FRAUD_TAGS.items()},
                       "demo_mode": demo_mode(),
                       "demo_companies": list(demo_cases.cases()) if demo_mode() else []}, indent=1)


@server.prompt(name="prescreen_case", title="Pre-screen a company for an underwriter")
def prescreen_case(company: str, proposal_notes: str = "") -> str:
    return (f"Pre-screen this company for a commercial insurance underwriter: {company}.\n"
            f"What the broker's proposal says: {proposal_notes or 'nothing given'}.\n\n"
            "1. find_company. If it isn't resolved, list the candidates and stop.\n"
            "2. prescreen_company with the number and the proposal details.\n"
            "3. Write a short brief for the underwriter: the route and what it means; the main reasons; "
            "everything that couldn't be checked (say plainly these are not clear results); proposal differences; "
            "the broker questions.\n"
            "4. Don't change the route, don't record a decision, and on a compliance hold don't draft anything "
            "for the broker.")


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
