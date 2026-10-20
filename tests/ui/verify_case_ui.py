"""
Offline check of the case page (/case) against the blueprint's acceptance list.

Run the app in demo mode first, then this script:
    USE_MOCK_DATA=true PRESCREEN_DATA_DIR=/tmp/ps uvicorn src.api.app:app --port 8765
    python tests/ui/verify_case_ui.py http://localhost:8765 docs/rebuild/ui

Not part of pytest (it needs a browser and a running server). Prints one line per check and writes
screenshots plus results.json into the output folder. Exit code 1 if any check fails.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/rebuild/ui")
OUT.mkdir(parents=True, exist_ok=True)
WIDTHS = [390, 563, 768, 1024, 1440]
DEMOS = ["09446231", "09563205", "ZZ000001", "ZZ000002", "ZZ000003", "ZZ000004", "OC000005"]
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


async def no_sideways(page: Page) -> tuple[bool, str]:
    w = await page.evaluate("[document.scrollingElement.scrollWidth, window.innerWidth]")
    return w[0] <= w[1] + 1, f"scrollWidth {w[0]} vs {w[1]}"


async def run_case(page: Page, number: str, cover: list[str] | None = None, turnover: str = "") -> str:
    await page.goto(BASE + "/case#new")
    await page.wait_for_selector("#q")
    await page.fill("#q", number)
    for c in cover or []:
        await page.check(f"input[name=cover][value={c}]")
    if turnover:
        await page.click("#prop summary")
        await page.fill("#turnover", turnover)
    await page.click("#run-btn")
    await page.wait_for_selector("#tab-overview", timeout=30000)
    return page.url.rsplit("/", 1)[-1]


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- Run every demo case once, at 1440, and keep the report ids
        ids: dict[str, str] = {}
        for n in DEMOS:
            cover = ["management_liability", "cyber"] if n == "09446231" else (["professional_indemnity"] if n == "OC000005" else [])
            ids[n] = await run_case(page, n, cover, "£2.4m" if n == "09446231" else "")
            await page.screenshot(path=str(OUT / f"case-{n}-1440.png"), full_page=True)
        check("all 7 demo cases open as cases", len(ids) == 7 and all(len(v) == 16 for v in ids.values()), json.dumps(ids))

        # ---- Routes (the rules decide; the page only shows them)
        routes = {}
        for n, rid in ids.items():
            r = await (await page.request.get(f"{BASE}/api/prescreen/{rid}")).json()
            routes[n] = r["route"]
        check("demo routes as before", routes == {"09446231": "refer", "09563205": "note", "ZZ000001": "outside_appetite",
                                                  "ZZ000002": "outside_appetite", "ZZ000003": "refer",
                                                  "ZZ000004": "compliance_hold", "OC000005": "note"}, json.dumps(routes))

        # ---- Desk with cases, candidates
        await page.goto(BASE + "/case")
        await page.wait_for_selector("#cases-body table")
        await page.screenshot(path=str(OUT / "desk-1440.png"), full_page=True)
        await page.fill("#q", "ltd")
        await page.click("#run-btn")
        await page.wait_for_selector("#h-cand")
        cands = await page.locator("#cands a[data-run]").count()
        check("a name with several matches shows candidates, nothing auto-picked", cands >= 2, f"{cands} candidates")
        await page.screenshot(path=str(OUT / "desk-candidates-1440.png"), full_page=True)
        # keyboard only: focus the first candidate link and press Enter
        await page.locator("#cands a[data-run]").first.focus()
        await page.keyboard.press("Enter")
        await page.wait_for_selector("#tab-overview", timeout=30000)
        await page.wait_for_timeout(300)
        live = await page.locator("#live").text_content()
        check("candidate opens with the keyboard; live region announces the result", "Pre-screen ready" in (live or ""), live or "")

        # ---- Proposal parsing: the JS agrees with the Python table
        from tests.test_caseview import MONEY_BAD, MONEY_OK
        await page.goto(BASE + "/case#new")
        await page.wait_for_selector("#q")
        js_ok = await page.evaluate("cases => cases.map(([t, v]) => { const r = parseMoney(t); return r.ok && r.value === v; })", MONEY_OK)
        js_bad = await page.evaluate("cases => cases.map(t => !parseMoney(t).ok)", MONEY_BAD)
        check("JS money parsing matches the server's table", all(js_ok) and all(js_bad),
              f"ok {sum(js_ok)}/{len(js_ok)}, refused {sum(js_bad)}/{len(js_bad)}")
        await page.fill("#q", "09446231")
        await page.click("#prop summary")
        await page.fill("#turnover", "£2.4m")
        read_as = await page.locator("#turnover-r").text_content()
        await page.fill("#years", "1.5")
        years_err = await page.locator("#years-r").text_content()
        disabled = await page.locator("#run-btn").is_disabled()
        await page.screenshot(path=str(OUT / "desk-parsing-1440.png"), full_page=False)
        await page.fill("#years", "")
        await page.fill("#turnover", "two million")
        two = await page.locator("#turnover-r").text_content()
        disabled2 = await page.locator("#run-btn").is_disabled()
        check("£2.4m reads as £2,400,000; 1.5 years and 'two million' refused inline, run disabled",
              "£2,400,000" in (read_as or "") and "Whole years" in (years_err or "") and disabled and "for example" in (two or "") and disabled2,
              f"{read_as} | {years_err} | {two}")

        # ---- One coverage count everywhere (Overview, Checks tab, Analysis, file note, MCP)
        from src.prescreen.mcp_server import summarise
        rid = ids["09446231"]
        await page.goto(f"{BASE}/case/{rid}")
        await page.wait_for_selector("#h-out")
        ov = await page.locator(".outcome .r b").first.text_content()
        await page.click("#tab-checks")
        ck = await page.locator("#h-checks + span").text_content()
        await page.click("#tab-analysis")
        an = await page.locator("#panel-analysis .pb .s13").first.text_content()
        note = (await (await page.request.get(f"{BASE}/api/prescreen/{rid}/note")).json())["text"]
        payload = await (await page.request.get(f"{BASE}/api/prescreen/{rid}")).json()
        mcp = summarise(payload)["coverage"]["text"]
        want = f"{payload['coverage']['completed']} of {payload['coverage']['applies']}"
        check("one coverage count on Overview, Checks, Analysis, file note and MCP",
              all(want in (x or "") for x in (ov, ck, an, note, mcp)), f"{want}: {ov} | {ck[:30]} | {an[:40]} | MCP {mcp[:30]}")
        for t in ("checks", "news", "people", "history", "analysis", "proposal"):
            await page.click(f"#tab-{t}")
            await page.wait_for_selector(f"#panel-{t}")
            await page.screenshot(path=str(OUT / f"case-09446231-{t}-1440.png"), full_page=True)

        # ---- Evidence: each referral reason reaches its primary source in one click; the drawer opens and Esc closes
        await page.click("#tab-overview")
        srcs = await page.eval_on_selector_all('[aria-labelledby="h-find"] tbody tr:not(.drawer) td[data-l=Source] a',
                                               "els => els.map(a => a.href)")
        rows = await page.locator('[aria-labelledby="h-find"] tbody tr:not(.drawer)').count()
        check("each referral reason links to its primary source from the Overview",
              rows > 0 and len(srcs) == rows and all(s.startswith("http") for s in srcs), f"{len(srcs)} of {rows}")
        await page.locator("[data-drawer]").first.click()
        opened = await page.locator("tr.drawer").count()
        await page.screenshot(path=str(OUT / "case-09446231-drawer-1440.png"), full_page=True)
        await page.keyboard.press("Escape")
        closed = await page.locator("tr.drawer").count()
        check("evidence drawer opens in place, one at a time, Esc closes it", opened == 1 and closed == 0)

        # ---- Decision: disabled until identity; override labelled and needs 20 characters; recorded block + history
        rec = page.locator("#record")
        dis1 = await rec.is_disabled()
        pre = await page.locator("input[name=dec]:checked").get_attribute("value")
        await page.check("#id-ok")
        dis2 = await rec.is_disabled()
        await page.check("input[name=dec][value=proceed]")
        ov_text = await page.locator("#override").text_content()
        await page.fill("#reason", "too short")
        await page.fill("#uw", "A. Smith")
        await rec.click()
        err = await page.locator("#dec-err").text_content()
        await page.fill("#reason", "Broker confirmed the FCA remediation is complete, in writing")
        await rec.click()
        await page.wait_for_selector(".recorded")
        recorded = await page.locator(".recorded").text_content()
        await page.screenshot(path=str(OUT / "case-09446231-decided-1440.png"), full_page=True)
        check("Record decision disabled until identity is confirmed; outcome preselected to the route",
              dis1 and not dis2 and pre == "refer", f"preselected {pre}")
        check("an override is labelled and needs a 20-character reason",
              "Overrides the recommended route (Refer)" in (ov_text or "") and "20 characters" in (err or "")
              and "Overrides the recommended route" in (recorded or ""), (err or "")[:80])
        await page.click("#another")
        await page.check("#id-ok")
        await page.fill("#reason", "Referred after all, pending a call")
        await page.fill("#uw", "A. Smith")
        await page.click("#record")
        await page.wait_for_selector(".recorded")
        hist = await page.locator(".hist li").count()
        check("decisions are added, never overwritten (history shows both)", hist == 2, f"{hist} in history")

        # ---- Copy is honest
        await ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        await page.click("#copy-btn")
        await page.wait_for_timeout(300)
        said = await page.locator("#copy-btn").text_content()
        pasted = await page.evaluate("() => navigator.clipboard.readText()")
        await page.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error('blocked')); }")
        await page.click("[data-bview=e]")
        await page.click("#copy-btn")
        await page.wait_for_timeout(300)
        said2 = await page.locator("#copy-btn").text_content()
        hint = await page.locator("#copy-msg").text_content()
        selected = await page.evaluate("getSelection().toString().length")
        check("Copy says Copied only on success; a failed copy selects the text with a hint",
              said == "Copied" and pasted.startswith("1. ") and said2 != "Copied" and "Ctrl+C" in (hint or "") and selected > 20,
              f"{said} / {said2} / selected {selected}")
        email = await page.locator("#copy-src").text_content()
        check("broker email built in code: subject, cover, numbered questions",
              "Monzo Bank Limited (09446231): information needed" in email and "D&O / management liability and cyber" in email
              and "1. " in email, email.splitlines()[0])

        # ---- Compliance hold: no broker text anywhere, no sanctions detail outside compliance
        hold = ids["ZZ000004"]
        await page.goto(f"{BASE}/case/{hold}")
        await page.wait_for_selector("#h-out")
        hp = await (await page.request.get(f"{BASE}/api/prescreen/{hold}")).json()
        sanction_texts = [f["text"] for c in hp["checks"] for f in c["findings"] if f["rule_id"].startswith("sanctions")]
        questions = [q["question"] for q in hp["broker_questions"]]
        leaks = []
        for t in ("overview", "checks", "news", "people", "history", "analysis", "proposal"):
            await page.click(f"#tab-{t}")
            body = await page.locator("body").inner_text()
            leaks += [f"{t}: sanctions" for s in sanction_texts if s[:40] in body]
            leaks += [f"{t}: question" for q in questions if q[:40] in body]
            leaks += [f"{t}: list" for w in ("Fictional Regime", "FIC000", "12/04/1966") if w in body]
            await page.screenshot(path=str(OUT / f"case-ZZ000004-{t}-1440.png"), full_page=True)
        await page.click("#tab-overview")
        dis = await page.locator("input[name=dec][value=proceed]").is_disabled() and await page.locator("input[name=dec][value=decline]").is_disabled()
        check("compliance hold: no broker text, no sanctions detail, only a referral to compliance",
              not leaks and dis and not hp["broker_email"]["available"], ", ".join(leaks) or "clean")

        # ---- Route variants and system states (stale, ended, failed run, run limit, running)
        async def patched(route, mutate):
            resp = await route.fetch()
            data = await resp.json()
            mutate(data)
            await route.fulfill(response=resp, json=data)

        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        await page.route(f"**/api/prescreen/{ids['ZZ000002']}", lambda r: patched(r, lambda d: d.update(created_at=old, stale=True)))
        await page.goto(f"{BASE}/case/{ids['ZZ000002']}")
        await page.wait_for_selector("#h-out")
        stale = await page.locator(".staleline").text_content()
        await page.screenshot(path=str(OUT / "state-stale-1440.png"), full_page=False)
        check("a case over 24 h old shows the stale line with Re-run", "Re-run before deciding" in (stale or ""), stale or "")

        def ended(d):
            d["company"]["status"] = "liquidation"
        await page.route(f"**/api/prescreen/{ids['09563205']}", lambda r: patched(r, ended))
        await page.goto(f"{BASE}/case/{ids['09563205']}")
        await page.wait_for_selector(".ended")
        await page.screenshot(path=str(OUT / "state-ended-company-1440.png"), full_page=False)
        check("ended company: the ended line comes first", "This company has ended" in await page.locator(".ended").text_content())

        await page.route("**/api/prescreen/stream", lambda r: r.fulfill(status=429, json={"detail": "30 pre-screens an hour on this demo; next slot in 12 min."}))
        await page.goto(BASE + "/case#new")
        await page.fill("#q", "09446231")
        await page.click("#run-btn")
        await page.wait_for_selector("#h-fail")
        await page.screenshot(path=str(OUT / "state-run-limit-1440.png"), full_page=False)
        await page.wait_for_timeout(300)
        lim = await page.locator("#h-fail").text_content()
        live = await page.locator("#live").text_content()
        check("run limit reached: says when the next slot is; live region announces the error",
              lim == "Run limit reached" and "failed" in (live or ""), live or "")
        await page.unroute("**/api/prescreen/stream")
        await page.route("**/api/prescreen/stream", lambda r: r.fulfill(status=500, json={"detail": "Unexpected error (RuntimeError)."}))
        await page.goto(BASE + "/case#new")
        await page.fill("#q", "09446231")
        await page.click("#run-btn")
        await page.wait_for_selector("#retry")
        await page.screenshot(path=str(OUT / "state-run-failed-1440.png"), full_page=False)
        check("whole run failed: error panel with Try again and Back to Desk", await page.locator("#retry").count() == 1)
        await page.unroute("**/api/prescreen/stream")

        async def slow(route):
            await asyncio.sleep(22)
            await route.continue_()
        await page.route("**/api/prescreen/stream", slow)
        await page.goto(BASE + "/case#new")
        await page.fill("#q", "09446231")
        await page.click("#run-btn")
        await page.wait_for_selector(".stages")
        await page.wait_for_timeout(21000)
        await page.screenshot(path=str(OUT / "state-running-1440.png"), full_page=False)
        slow_note = await page.locator("#slow").is_visible()
        await page.wait_for_selector("#tab-overview", timeout=30000)
        check("running: stage list, elapsed seconds, the 2-minute note after 20 s", slow_note)
        await page.unroute("**/api/prescreen/stream")

        # source failed (ZZ000003): couldn't-check in coverage and a Re-run line in Outstanding
        await page.goto(f"{BASE}/case/{ids['ZZ000003']}")
        await page.wait_for_selector("#h-out")
        out = await page.locator('[aria-labelledby="h-todo"]').inner_text()
        check("a source failed: the case opens; Outstanding offers Re-run", "couldn't check" in out and "Re-run" in out)

        # ---- Widths: evidence before the decision, no sideways scroll, Desk <-> case without a reload
        for w in WIDTHS:
            await page.set_viewport_size({"width": w, "height": 900})
            await page.goto(BASE + "/case")
            await page.wait_for_selector("#cases-body table")
            loads = await page.evaluate("window.__pageLoads")
            await page.screenshot(path=str(OUT / f"desk-{w}.png"), full_page=True)
            ok_desk, d1 = await no_sideways(page)
            link = page.locator("#cases-body a[data-nav]").first
            await link.click()
            await page.wait_for_selector("#h-out")
            for n in ("09446231", "ZZ000004", "OC000005", "ZZ000001"):
                # in-app navigation, the way a link inside the page moves (no reload)
                await page.evaluate("p => { history.pushState(null, '', p); dispatchEvent(new PopStateEvent('popstate')); }",
                                    f"/case/{ids[n]}")
                await page.wait_for_function("id => document.querySelector('#tab-overview') && location.pathname.endsWith(id)"
                                             " && document.title.length > 0", arg=ids[n])
                await page.wait_for_timeout(150)
                order = await page.evaluate("""() => {
                  const y = s => { const e = document.querySelector(s); return e ? e.getBoundingClientRect().top + scrollY : null; };
                  const pos = (a, b) => !!(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
                  return {domOk: pos('#h-out', '#decision') && pos('#h-find', '#decision'), out: y('#h-out'), find: y('#h-find'), dec: y('#decision'), one: innerWidth < 1024};
                }""")
                ok_case, d2 = await no_sideways(page)
                in_order = order["domOk"] and (not order["one"] or (order["out"] < order["dec"] and order["find"] < order["dec"]))
                check(f"{w}px {n}: route, coverage and reasons before the decision; no sideways scroll", in_order and ok_case, d2)
                await page.screenshot(path=str(OUT / f"case-{n}-{w}.png"), full_page=True)
            # back to the Desk (browser Back) and start another, without a reload
            await page.go_back()
            await page.wait_for_function("id => location.pathname.endsWith(id)", arg=ids["OC000005"])
            await page.wait_for_selector("#tab-overview")
            cases_link = page.locator('a[href="/case#cases"]').first if w < 1024 else page.locator('.nav a[data-navid=cases]')
            await cases_link.click()
            await page.wait_for_selector("#cases-body")
            await page.fill("#q", "OC000005")
            await page.click("#run-btn")
            await page.wait_for_selector("#h-out", timeout=30000)
            loads2 = await page.evaluate("window.__pageLoads")
            check(f"{w}px: open a case, back to the Desk, start another without a reload", loads == loads2 == 1 and ok_desk, d1)
            if w < 1024:
                vis = await page.locator(".decidebar").is_visible()
                check(f"{w}px: sticky 'Review and decide' button", vis)

        check("no script errors on any page", not errors, "; ".join(errors[:3]))
        await browser.close()
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2))
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
