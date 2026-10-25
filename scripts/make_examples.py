"""
Make the public example cases: the 9 evaluation companies, pre-screened fresh and reviewed once by the agents
on the owner's model, so each example shows one complete agent review (not a partial one).

    $env:PRESCREEN_ACCESS_TOKEN = "<owner code>"      # PowerShell; nothing is printed or saved
    $env:HF_TOKEN = "<HF read token>"                 # only while the Space is private
    python scripts/make_examples.py

Uses 9 pre-screens (of 30 an hour) and 9 agent reviews on AGENT_PROVIDER (OpenAI on the Space; about
$0.002 each at list price). Prints each case and, at the end, the PRESCREEN_PUBLIC_EXAMPLES line to set on
the Space. A company whose review doesn't come back "ok" is left out of that line, with the reason.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from live_check import COMPANIES, SPACE, headers  # noqa: E402

GAP = float(os.environ.get("GAP", "10"))


def main() -> int:
    if not os.environ.get("PRESCREEN_ACCESS_TOKEN"):
        print("Set PRESCREEN_ACCESS_TOKEN (the owner code) first.")
        return 1
    keep: list[str] = []
    total = 0.0
    with httpx.Client(timeout=300, headers=headers(), follow_redirects=True) as c:
        cfg = c.get(f"{SPACE}/api/prescreen/config")
        if cfg.status_code != 200:
            print(f"The API didn't answer: HTTP {cfg.status_code} {cfg.text[:120]}")
            return 1
        print(f"Space rules {cfg.json().get('rules_version')}, caller {(cfg.json().get('caller') or {}).get('role')}")
        for number, name, _expected, proposal in COMPANIES:
            r = c.post(f"{SPACE}/api/prescreen", json={"company_number": number, **proposal})
            if r.status_code != 200:
                print(f"{name:13} pre-screen failed: HTTP {r.status_code} {r.text[:120]}")
                continue
            rep = r.json()
            rid, route = rep["report_id"], rep["route"]
            if route == "compliance_hold":
                print(f"{name:13} {rid} {route}: no agent review on a hold; kept")
                keep.append(rid)
                continue
            g = c.post(f"{SPACE}/api/prescreen/{rid}/agent-review")
            if g.status_code != 200:
                print(f"{name:13} {rid} {route}: agent review HTTP {g.status_code} {g.text[:120]}; left out")
                continue
            run = g.json()
            sections = (run.get("review") or {}).get("sections") or []
            ok = sum(1 for s in sections if s.get("status") == "ok")
            t = (run.get("trace") or {}).get("totals") or {}
            total += float(t.get("cost_usd") or 0)
            line = (f"{name:13} {rid} {route:16} review {run.get('status')} {ok}/{len(sections)} sections, "
                    f"{run.get('model')}, {round((t.get('wall_ms') or 0) / 1000)} s, ${t.get('cost_usd', 0)}")
            if run.get("status") == "ok":
                keep.append(rid)
            else:
                line += "; left out"
            print(line, flush=True)
            time.sleep(GAP)
    print(f"\nModel cost at list price: ${total:.4f}")
    print(f"{len(keep)} examples. Set this on the Space (Variables):")
    print(f"PRESCREEN_PUBLIC_EXAMPLES={','.join(keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
