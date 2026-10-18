"""
Build a self-contained design preview of the pre-screen UI from the offline demo cases.

Every report in the preview comes from the real engine and rules run on the demo
cases (tests' fixtures), with a scripted model answer. The page is
frontend/prescreen.html with fetch() stubbed, so it opens anywhere without a server.

    python scripts/build_preview.py out.html
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.prescreen import demo_cases  # noqa: E402
from src.prescreen.casefile import file_note  # noqa: E402
from src.prescreen.engine import run_prescreen  # noqa: E402
from src.prescreen.models import PreScreenRequest  # noqa: E402
from src.prescreen.monitor import diff, snapshot  # noqa: E402
from tests.test_prescreen import _services  # noqa: E402

# What a broker's proposal might say for each demo case (invented).
PROPOSALS = {
    "09563205": dict(cover_lines=["property"]),
    "09446231": dict(declared_trade="Retail bank", cover_lines=["management_liability", "cyber"],
                     expects_fca_authorisation=True, declared_directors="Eileen Burbidge, Valerie Dias"),
    "ZZ000001": dict(declared_trade="Commercial bakery, wholesale to cafes", cover_lines=["property", "liability"],
                     declared_turnover_gbp=650_000, declared_years_trading=9, declared_directors="Jordan Example"),
    "ZZ000002": dict(declared_trade="Bespoke joinery and shopfitting", cover_lines=["property", "liability"],
                     declared_turnover_gbp=2_400_000, declared_years_trading=15,
                     declared_directors="Morgan Sample, Alex Sample"),
    "ZZ000003": dict(declared_trade="Domestic plumbing and heating", cover_lines=["liability"]),
    "ZZ000004": dict(declared_trade="Import and wholesale of building materials", cover_lines=["property", "liability"],
                     declared_turnover_gbp=8_000_000),
    "OC000005": dict(declared_trade="Accountancy and tax advice for small firms",
                     cover_lines=["professional_indemnity"], declared_directors="Asha Patel, Owen Hughes"),
}
TRADE_VERDICT = {"ZZ000004": ("inconsistent", "SIC 46900 is non-specialised wholesale; building-materials import "
                                              "would usually be 46730. Ask what the business actually trades.")}


def scripted(number: str, case: dict) -> list[str]:
    news = case.get("news")
    n = len(news) if isinstance(news, list) else 0
    req = PROPOSALS.get(number, {})
    if not n and not req.get("declared_trade"):
        return []
    verdict, reason = TRADE_VERDICT.get(number, ("consistent", "The declared trade fits the registered activity."))
    items = [{"i": i, "about_this_company": "yes", "category": "neutral", "severity": "low",
              "reason": "Routine coverage of the company."} for i in range(n)]
    return [json.dumps({"items": items, "trade_consistency": {"verdict": verdict, "reason": reason}})]


async def build() -> dict:
    cases = demo_cases.cases()
    reports, labels = {}, {}
    for i, (number, case) in enumerate(cases.items()):
        req = PreScreenRequest(company_number=number, **PROPOSALS.get(number, {}))
        r = await run_prescreen(req, _services(number, llm_answers=scripted(number, case)))
        d = r.model_dump(mode="json")
        d["report_id"] = f"demo{i:02d}{number.lower()}"
        d["meta"] = {"data": "demo", "case_label": case["label"]}
        d["decisions"] = []
        reports[number] = d
        labels[number] = {"label": case["label"], "kind": case["kind"]}

    # Day 2: re-check the watched LLP against the demo's next-day data.
    number = "OC000005"
    original = demo_cases.cases
    demo_cases.cases = lambda day=1: original(day=2)
    try:
        r2 = await run_prescreen(PreScreenRequest(company_number=number, **PROPOSALS[number]), _services(number),
                                 skip_media=True)
    finally:
        demo_cases.cases = original
    d2 = r2.model_dump(mode="json")
    d2["report_id"] = "demo99recheck"
    d2["meta"] = {"data": "demo", "case_label": cases[number]["label"] + " (next-day re-check)",
                  "source": "daily re-check"}
    d2["decisions"] = []
    changes = [{**c, "id": i + 1, "company_number": number, "company_name": d2["company_name"],
                "detected_at": "2026-10-19T06:17:00Z", "seen": False, "report_id": d2["report_id"]}
               for i, c in enumerate(diff(snapshot(reports[number]), snapshot(d2)))]
    notes = {d["report_id"]: file_note(d) for d in [*reports.values(), d2]}
    return {"reports": reports, "recheck": d2, "changes": changes, "labels": labels, "notes": notes}


STUB = """
/* Preview only: the API is replaced by data the real engine produced from the offline demo cases. */
(function(){
const DATA = __DATA__;
const byId = {}; Object.values(DATA.reports).forEach(r => byId[r.report_id] = r); byId[DATA.recheck.report_id] = DATA.recheck;
let watched = {"OC000005": {company_number:"OC000005", company_name:DATA.reports.OC000005.company_name, added_at:"2026-10-18T09:00:00Z",
  last_checked_at:"2026-10-19T06:17:00Z", last_route:DATA.recheck.route}};
let recent = [DATA.recheck, DATA.reports.OC000005, DATA.reports.ZZ000002];
const decisions = {};
const ok = b => Promise.resolve(new Response(JSON.stringify(b), {status:200, headers:{"Content-Type":"application/json"}}));
const unseen = n => DATA.changes.filter(c => c.company_number === n && !c.seen).length;
const summary = r => ({report_id:r.report_id, company_number:r.company_number, company_name:r.company_name, route:r.route,
  created_at:r.created_at, decision:((decisions[r.report_id]||[]).slice(-1)[0]||{}).decision||null});
window.fetch = (url, opts = {}) => {
  const u = new URL(String(url), "https://preview.local"), p = u.pathname, m = (opts.method || "GET").toUpperCase();
  const body = opts.body ? JSON.parse(opts.body) : {};
  if (p.endsWith("/config")) return ok({data:"demo", rules_version:DATA.reports.ZZ000002.rules_version,
    demo_cases:Object.entries(DATA.labels).map(([n, c]) => ({company_number:n, label:c.label, kind:c.kind}))});
  if (p.endsWith("/recent")) return ok(recent.map(summary));
  if (p.endsWith("/changes")) return ok(DATA.changes.slice().reverse());
  if (/\\/changes\\/[^/]+\\/seen$/.test(p)) { const n = p.split("/")[4]; DATA.changes.forEach(c => { if (c.company_number === n) c.seen = true; }); return ok({ok:true}); }
  if (p.endsWith("/watch") && m === "POST") { const r = byId[body.report_id]; watched[r.company_number] = {company_number:r.company_number,
    company_name:r.company_name, added_at:new Date().toISOString(), last_route:r.route}; return ok(watched[r.company_number]); }
  if (p.includes("/watch/") && m === "DELETE") { delete watched[decodeURIComponent(p.split("/").pop())]; return ok({ok:true}); }
  if (p.endsWith("/watch")) return ok(Object.values(watched).map(w => ({...w, unseen_changes:unseen(w.company_number)})));
  if (p.endsWith("/search")) { const q = (u.searchParams.get("q") || "").toLowerCase();
    const hits = Object.values(DATA.reports).filter(r => r.company_number.toLowerCase() === q || r.company_name.toLowerCase().includes(q));
    return ok(hits.length === 1 ? {resolved:true, company_number:hits[0].company_number, message:""}
      : {resolved:false, message:hits.length ? "More than one company matches. Pick the right one." : "No demo company matches. Try one of the demo cases.",
         candidates:hits.map(r => ({company_number:r.company_number, name:r.company_name, status:(r.company||{}).status||"", date_of_creation:(r.company||{}).incorporated||"", address:(r.company||{}).registered_office||"", exact_name_match:false}))}); }
  if (p.endsWith("/stream")) { const r = DATA.reports[body.company_number];
    if (!r) return ok({detail:"Not a demo company."});
    const rep = {...r, created_at:new Date().toISOString()}; if (!recent.includes(r)) recent.unshift(r);
    const lines = [{type:"stages", stages:[{id:"companies_house", label:"Companies House: profile, officers, owners, charges"}, {id:"news", label:"News and web"}]},
      {type:"progress", stage:"companies_house", state:"done"}, {type:"progress", stage:"news", state:"done"}, {type:"report", report:rep}];
    return Promise.resolve(new Response(lines.map(x => JSON.stringify(x)).join("\\n") + "\\n", {status:200})); }
  if (p.endsWith("/note")) { const id = p.split("/")[3]; let t = DATA.notes[id] || "";
    const d = (decisions[id] || []).slice(-1)[0];
    if (d) t = t.replace("\\n\\nA recommendation", `\\n\\nDecision: ${d.decision}${d.underwriter ? " by " + d.underwriter : ""}\\nReason: ${d.reason}\\n\\nA recommendation`);
    return ok({report_id:id, text:t}); }
  if (p.endsWith("/decision")) { const id = p.split("/")[3]; const e = {...body, recorded_at:new Date().toISOString()};
    (decisions[id] = decisions[id] || []).push(e); return ok(e); }
  if (p.endsWith("/api/jobs/recheck")) return ok({checked:1});
  const id = p.split("/").pop(); if (byId[id]) return ok({...byId[id], decisions:decisions[id] || []});
  return Promise.resolve(new Response(JSON.stringify({detail:"Not in the preview."}), {status:404}));
};
})();
"""

BANNER = ('<div class="pv">Design preview. The reports were produced by the real rules on offline demo cases; '
          'nothing here calls a live register. Changes, decisions and watching only last until you reload.</div>\n')


def main(out: str) -> None:
    data = asyncio.run(build())
    html = (ROOT / "frontend" / "prescreen.html").read_text(encoding="utf-8")
    stub = STUB.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    html = html.replace("<script>\n", "<script>\n" + stub + "\n", 1)
    # Artifact pages are wrapped in their own document skeleton.
    html = re.sub(r"<!doctype html>\s*<html[^>]*>\s*<head>\s*", "", html)
    html = re.sub(r'<meta charset="utf-8">\n|<meta name="viewport"[^>]*>\n', "", html)
    html = html.replace("</head>\n<body>\n", "").replace("</body>\n</html>\n", "")
    html = re.sub(r"<title>.*?</title>", "<title>UK Company Pre-Screen</title>", html, count=1)
    html = html.replace('<header class="top">', BANNER + '<header class="top">', 1)
    Path(out).write_text(html, encoding="utf-8")
    print(f"wrote {out}: {len(data['reports'])} reports, {len(data['changes'])} changes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "prescreen-preview.html")
