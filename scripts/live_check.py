"""
Live check of the deployed Space after a deploy. Run by .github/workflows/live-check.yml.

  1. The Space is up (/health), / still serves the current page and /case serves the case page.
  2. If PRESCREEN_ACCESS_TOKEN is set: re-run the 9 evaluation companies with the same proposals as the
     19 Oct live runs, one after another (9 of the 30-an-hour limit), and compare each route with the
     last live route under rules 19e/19f. Also checks the case-page fields are on every live report.

Env: SPACE_URL, HF_TOKEN (the Space is private), PRESCREEN_ACCESS_TOKEN (optional).
Writes live-check.json and a Markdown table to $GITHUB_STEP_SUMMARY. Exit 1 on any failure.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

SPACE = os.environ.get("SPACE_URL", "https://aitoolstack-agentic-uk-company-due-diligence.hf.space").rstrip("/")
HF = os.environ.get("HF_TOKEN", "")
CODE = os.environ.get("PRESCREEN_ACCESS_TOKEN", "")
EXPECTED_RULES = "draft-2026-10-20h"

# Same proposals as the last live runs (Supabase reports, 19 Oct), and the route each got.
COMPANIES = [
    ("09446231", "Monzo", "refer", {"cover_lines": ["management_liability", "cyber"], "declared_trade": "Retail bank",
                                    "expects_fca_authorisation": True}),
    ("09563205", "Wilko", "refer", {"cover_lines": ["property", "liability"], "declared_trade": "Homeware retail",
                                    "declared_turnover_gbp": 5000000}),
    ("02366661", "Thames Water", "refer", {"cover_lines": ["liability", "property"],
                                           "declared_trade": "Water supply and sewerage"}),
    ("03782379", "Carillion", "outside_appetite", {"cover_lines": ["liability"],
                                                   "declared_trade": "Construction and facilities services"}),
    ("SC311560", "BrewDog", "outside_appetite", {"cover_lines": []}),
    ("00445790", "Tesco", "note", {"cover_lines": ["property", "liability", "cyber"], "declared_trade": "Grocery retail"}),
    ("08804411", "Revolut", "refer", {"cover_lines": ["professional_indemnity", "cyber"],
                                      "declared_trade": "Payments and e-money", "expects_fca_authorisation": True}),
    ("00502851", "Greggs", "note", {"cover_lines": ["property", "liability"],
                                    "declared_trade": "Bakery and food-to-go shops"}),
    ("OC326345", "Linklaters", "refer", {"cover_lines": ["professional_indemnity"], "declared_trade": "Legal services"}),
]
CASE_FIELDS = ["sources_read", "news_view", "broker_email", "proposal_read", "coverage"]


def headers() -> dict[str, str]:
    h = {"User-Agent": "prescreen-live-check/1.0"}
    if HF:
        h["Authorization"] = f"Bearer {HF}"
    if CODE:
        h["X-Access-Token"] = CODE
    return h


def main() -> int:
    out: dict = {"space": SPACE, "checks": [], "companies": []}
    failed = False

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        failed |= not ok
        out["checks"].append({"check": name, "ok": ok, "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)

    with httpx.Client(timeout=200, headers=headers(), follow_redirects=True) as c:
        for _ in range(30):  # a fresh build or a sleeping Space takes a while
            try:
                if c.get(f"{SPACE}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(15)
        h = c.get(f"{SPACE}/health")
        check("Space is up (/health)", h.status_code == 200, str(h.status_code))
        home = c.get(f"{SPACE}/")
        check("/ serves the case page", home.status_code == 200 and "parseMoney" in home.text,
              f"{home.status_code}, {len(home.text)} bytes")
        old = c.get(f"{SPACE}/old")
        check("/old serves the previous page", old.status_code == 200 and "parseMoney" not in old.text,
              f"{old.status_code}, {len(old.text)} bytes")
        new = c.get(f"{SPACE}/case")
        check("/case serves the case page", new.status_code == 200 and "Read as" in new.text and "parseMoney" in new.text,
              f"{new.status_code}, {len(new.text)} bytes")
        deep = c.get(f"{SPACE}/case/198c0e28c6ca4be0")
        check("/case/<report id> deep link serves the page", deep.status_code == 200 and "parseMoney" in deep.text,
              str(deep.status_code))

        if not CODE:
            print("PRESCREEN_ACCESS_TOKEN isn't a repository secret, so the 9 live companies were not run.")
            out["companies_skipped"] = "no PRESCREEN_ACCESS_TOKEN secret"
        else:
            cfg = c.get(f"{SPACE}/api/prescreen/config")
            ok = cfg.status_code == 200
            data = cfg.json() if ok else {}
            check(f"API answers with the access code; live data; rules {EXPECTED_RULES}",
                  ok and data.get("data") == "live" and data.get("rules_version") == EXPECTED_RULES,
                  f"{cfg.status_code} {data.get('data')} {data.get('rules_version')}")
            cases = c.get(f"{SPACE}/api/prescreen/cases")
            check("cases API (Desk) answers", cases.status_code == 200 and "rows" in cases.json(), str(cases.status_code))
            if ok:
                for number, name, expected, proposal in COMPANIES:
                    t0 = time.time()
                    r = c.post(f"{SPACE}/api/prescreen", json={"company_number": number, **proposal})
                    secs = round(time.time() - t0, 1)
                    if r.status_code != 200:
                        check(f"{name}: run", False, f"HTTP {r.status_code} {r.text[:200]}")
                        out["companies"].append({"name": name, "number": number, "error": r.status_code})
                        continue
                    rep = r.json()
                    missing = [f for f in CASE_FIELDS if not rep.get(f)]
                    row = {"name": name, "number": number, "report_id": rep["report_id"], "route": rep["route"],
                           "expected": expected, "rules": rep["rules_version"], "coverage": rep["coverage"]["text"],
                           "reasons": rep.get("route_reasons", [])[:3], "news": rep["news_view"].get("summary", ""),
                           "seconds": secs, "missing_fields": missing,
                           "sources_read": [f"{s['label']} {s['state']}" for s in rep.get("sources_read", [])]}
                    out["companies"].append(row)
                    check(f"{name}: route {rep['route']} (expected {expected}), case fields present",
                          rep["route"] == expected and rep["rules_version"] == EXPECTED_RULES and not missing,
                          f"{secs} s, {rep['coverage']['text']}" + (f", missing {missing}" if missing else ""))

    with open("live-check.json", "w") as f:
        json.dump(out, f, indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Live check\n\n| | Check | Detail |\n|---|---|---|\n")
            for x in out["checks"]:
                f.write(f"| {'✅' if x['ok'] else '❌'} | {x['check']} | {x['detail']} |\n")
            if out["companies"]:
                f.write("\n| Company | Route | Expected | Report | Coverage | s |\n|---|---|---|---|---|---|\n")
                for r in out["companies"]:
                    f.write(f"| {r['name']} | {r.get('route', 'error')} | {r.get('expected', '')} | "
                            f"{r.get('report_id', '')} | {r.get('coverage', '')} | {r.get('seconds', '')} |\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
