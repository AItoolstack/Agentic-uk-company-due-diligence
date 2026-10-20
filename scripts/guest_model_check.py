"""
Before guests get agent reviews on the free model: run the 9 evaluation companies through the agents on the
guest model (?model=guest, owner code). Compare the table with the owner-model numbers in
docs/rebuild/08-three-way-eval.md (19 Oct: 16 of 16 sections, 4 rewrites, median 15.8 s).
Run by .github/workflows/guest-model-check.yml, by hand.

What it measures, per company: did the review finish (ok / partial / stopped), sections ok of total, blocked
sections, revisions, judge on or off, model calls, tokens, wall time, failed calls (rate limits show here),
and whether the route stayed the rules' route (it must: the agents can't change it).

It doesn't decide anything. Read the table: if sections finish and rate limits don't stop runs, guests can
have agent reviews on this model; if the judge's extra calls hit the free limit, set GUEST_AGENT_JUDGE=off.

Uses 9 pre-screens (of 30 an hour) and 9 agent reviews (of 20 an hour). Groq's free tier is rate limited per
minute, so runs are spaced out (GAP seconds, default 20).

Env: SPACE_URL, HF_TOKEN (the Space is private), PRESCREEN_ACCESS_TOKEN (the owner code), GAP.
Writes guest-model-check.json and a Markdown table to $GITHUB_STEP_SUMMARY. Exit 1 only if the Space or API
can't be reached; a slow or partial review is a finding, not a failure.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from live_check import COMPANIES, SPACE, headers  # noqa: E402

GAP = float(os.environ.get("GAP", "20"))


def summarise(run: dict) -> dict:
    review = run.get("review") or {}
    sections = review.get("sections") or []
    t = (run.get("trace") or {}).get("totals") or {}
    return {
        "status": run.get("status", ""), "model": run.get("model", ""), "provider": run.get("provider", ""),
        "judge": bool(run.get("judge_model")),
        "sections_ok": sum(1 for s in sections if s.get("status") == "ok"), "sections": len(sections),
        "blocked": sum(1 for s in sections if s.get("status") == "blocked"),
        "errors": [s.get("error", "")[:120] for s in sections if s.get("status") == "error"],
        "revisions": sum(int(s.get("revisions") or 0) for s in sections),
        "route": review.get("route", ""),
        "model_calls": t.get("model_calls", 0), "input_tokens": t.get("input_tokens", 0),
        "output_tokens": t.get("output_tokens", 0), "failed_calls": t.get("failed_calls", 0),
        "seconds": round((t.get("wall_ms") or 0) / 1000, 1), "list_price_usd": t.get("cost_usd", 0),
    }


def main() -> int:
    if not os.environ.get("PRESCREEN_ACCESS_TOKEN"):
        print("PRESCREEN_ACCESS_TOKEN (the owner code) is needed.")
        return 1
    out: dict = {"space": SPACE, "companies": []}
    with httpx.Client(timeout=300, headers=headers(), follow_redirects=True) as c:
        cfg = c.get(f"{SPACE}/api/prescreen/config")
        if cfg.status_code != 200:
            print(f"API didn't answer: {cfg.status_code}")
            return 1
        out["rules_version"] = cfg.json().get("rules_version")
        for number, name, _expected, proposal in COMPANIES:
            row: dict = {"name": name, "number": number}
            r = c.post(f"{SPACE}/api/prescreen", json={"company_number": number, **proposal})
            if r.status_code != 200:
                row["error"] = f"pre-screen HTTP {r.status_code}"
                out["companies"].append(row)
                print(f"{name}: {row['error']}", flush=True)
                continue
            rep = r.json()
            row.update(report_id=rep["report_id"], rules_route=rep["route"])
            if rep["route"] == "compliance_hold":
                row["skipped"] = "compliance hold: the analysis doesn't run"
                out["companies"].append(row)
                continue
            g = c.post(f"{SPACE}/api/prescreen/{rep['report_id']}/agent-review", params={"model": "guest"})
            if g.status_code != 200:
                row["guest"] = {"status": f"HTTP {g.status_code}", "detail": g.text[:200]}
            else:
                row["guest"] = summarise(g.json())
                row["route_unchanged"] = row["guest"]["route"] in ("", rep["route"])
            out["companies"].append(row)
            gs = row["guest"]
            print(f"{name}: {gs.get('status')} {gs.get('sections_ok', '?')}/{gs.get('sections', '?')} sections, "
                  f"{gs.get('seconds', '?')} s, {gs.get('failed_calls', '?')} failed calls", flush=True)
            time.sleep(GAP)

    done = [r for r in out["companies"] if isinstance(r.get("guest"), dict) and "sections" in r["guest"]]
    out["summary"] = {
        "reviews": len(done),
        "finished_ok": sum(1 for r in done if r["guest"]["status"] == "ok"),
        "sections_ok": sum(r["guest"]["sections_ok"] for r in done),
        "sections": sum(r["guest"]["sections"] for r in done),
        "failed_calls": sum(r["guest"]["failed_calls"] for r in done),
        "route_changed": sum(1 for r in done if not r.get("route_unchanged", True)),
        "median_seconds": sorted(r["guest"]["seconds"] for r in done)[len(done) // 2] if done else None,
    }
    with open("guest-model-check.json", "w") as f:
        json.dump(out, f, indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            s = out["summary"]
            f.write(f"## Agents on the guest model\n\nRules {out.get('rules_version')}. {s['finished_ok']} of "
                    f"{s['reviews']} reviews finished ok; sections {s['sections_ok']} of {s['sections']}; "
                    f"failed calls {s['failed_calls']}; route changed {s['route_changed']}; "
                    f"median {s['median_seconds']} s.\n\n")
            f.write("| Company | Status | Sections | Blocked | Revisions | Judge | Calls | Failed | s |\n"
                    "|---|---|---|---|---|---|---|---|---|\n")
            for r in out["companies"]:
                g = r.get("guest") or {}
                f.write(f"| {r['name']} | {g.get('status', r.get('error') or r.get('skipped', ''))} | "
                        f"{g.get('sections_ok', '')}/{g.get('sections', '')} | {g.get('blocked', '')} | "
                        f"{g.get('revisions', '')} | {g.get('judge', '')} | {g.get('model_calls', '')} | "
                        f"{g.get('failed_calls', '')} | {g.get('seconds', '')} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
