# Build journal: from pre-screen to agentic architecture

A running log for the blog series. Each entry: what was decided, what was rejected and why,
what broke, and the numbers. Newest at the bottom. Earlier context: 01–05 in this folder.

---

## 18 Oct 2026, evening: where this phase starts

The rebuild (01–05) replaced an 8–11-call LangGraph agent graph with written rules plus one
model call. The old graph's failures were quiet: rate limits turned into a "medium" risk default,
missing data into "clean", and a check that could never fire (disqualified directors) passed
every director. The lesson so far: **the agent graph was making decisions that should have been code.**

The question for this phase (Tej, 22:56): stop building insurer product features and show the
technical capability, especially how this connects to multiple agents. The framing agreed:
*agents on top of a trustworthy core*, not agents instead of one.

---

## Phase A: the case file (20:15–22:30)

**Built:** five underwriter groups instead of one list; fraud indicators as tags on findings
(not a sixth group); a broker question per finding; proposal vs public record; a file note;
watching with a daily re-check.

**Things that broke, found by running it rather than reading it:**

1. *The CI Postgres job had been failing since it was written.* The workflow applied migrations
   with `for f in supabase/migrations/*.sql`, and `20261018_changes_report_link.sql` sorts before
   `20261018_prescreen_core.sql`. Fix: name migrations by the version Supabase actually recorded.
   Lesson: glob order is a dependency graph nobody wrote down.
2. *A check vanished silently.* With a declared trade but no news results, the model was never
   called, so "trade vs SIC" didn't appear at all. Same on a news search failure. This broke the
   core rule (a check never disappears without saying so) on a path no test covered. Fix: same
   prompt with an empty result list; still at most one model call.
3. *A summary contradicted its own findings.* "Board and ownership changes" listed three changes
   under the summary "No director or ownership changes". The clear-summary was reused for
   note-level findings.
4. *Tipping off.* Leaving the sanctions question out of the broker questions wasn't enough:
   "who are the ultimate owners?" sent while a sanctions match is open can tip the client off.
   With an open match, all broker questions are now held.
5. *The most important change was the one cut off.* The watchlist feed showed 4 changes per
   company, newest id first; the route change was inserted first, so it was always fifth.

**How these were found:** generating the design preview from real engine output instead of
hand-written mock data. Three of the five only showed up because the preview used the real code.

---

## The redesign and an underwriter agent as reviewer (22:33–23:10)

**Research:** credit tools (Creditsafe, Endole) lead with a 0–100 score and a credit limit;
underwriting workbenches (Hyperexponential, Federato, Cytora) lead with a decision-ready case
view, visible reasons and referral/audit in the flow.

**Decision: no score.** A score hides gaps. The signature element became a coverage ledger:
every check under the five questions, with "couldn't check" as visible as a flag.

**Review by a separate agent acting as a senior commercial underwriter** (it read 11 full-page
screenshots, no code). What it caught that I didn't:
- Proceed/Refer/Decline were still clickable on a compliance hold.
- "Note only" meant two things (a route and a per-finding weight). Now "No referral" and "For the file".
- An unreadable officer list showed as "0 current" directors, which is itself a red flag.
- The daily re-check showed news as "not applicable" when it was "not re-read".
- A watched company is probably bound: new-business routes ("decline") mean nothing mid-term.
- The CCJ gap on every report trains people to skip "couldn't check".

**Pattern worth a post:** a reviewer agent with a persona and screenshots, no code access, finds
domain errors a code reviewer can't. It's cheap (one run, ~2 minutes) and its findings were specific
enough to act on. The ones that would change rules were logged for a human decision, not applied.

---

## MCP server (23:00–)

**Why MCP first:** it's the integration surface everything else builds on. A LangGraph
orchestrator, Claude Desktop and a Copilot Studio agent can all use the same tools, and the
tool contract is where the safety rules live.

**Tools:** `find_company`, `prescreen_company`, `get_report_section`, four one-source lookups
(`company_record`, `gazette_notices`, `sanctions_screen`, `fca_firm_search`), `watch_company`,
`list_changes`. Two resources (rules, check catalogue), one prompt (`prescreen_case`).

**Design rules, each pinned by a test:**
| Rule | Why |
|---|---|
| No tool records a decision | An agent can prepare a case; a named person signs it |
| Route returned, never settable | The rules decide; an agent can explain, not upgrade/downgrade |
| Every gap carries "not checked, which is not a clear result" | Agents summarise; a missing line becomes "clear" in their prose |
| Summary ~3–5 KB; detail by section | A full report is 30–60 KB of JSON; agents pay for every token and lose the thread |
| Compliance hold: no broker questions, no sanctions detail in any section | Anything an agent can read, it can put in an email |
| `find_company` never picks between candidates | Identity is the first failure in 01 (Wilko) |
| Lookups return evidence, not verdicts | Specialist agents need one source; verdicts stay in one place |
| Read-only annotations on lookups | Lets clients auto-approve safe calls and confirm the rest |

**Things that broke or surprised:**

1. *The SDK moved under me.* The installed `mcp` is 2.2 (2.3 on PyPI). `FastMCP` is now
   `MCPServer`; importing the old path raises a pointer to a migration guide. Most tutorials and
   examples online are v1. Pinned `mcp>=2.2,<3`.
2. *DNS-rebinding guard.* The streamable HTTP transport, left at its defaults, enables host
   checking that allows only localhost. Deployed as-is, every request to `*.hf.space` would get
   `421 Invalid Host header`. Fix: allow `SPACE_HOST` (Hugging Face sets it) plus `MCP_ALLOWED_HOSTS`.
   A test checks the Space host passes and another host gets 421.
3. *Mount vs route.* Mounting the MCP app at `/mcp` makes Starlette redirect `/mcp` → `/mcp/`,
   and MCP clients don't follow redirects on POST. Used an exact route instead.
4. *Lifespans don't nest.* A Starlette app routed inside FastAPI never runs its own lifespan,
   so the MCP session manager never started. Started it from FastAPI's lifespan.
5. *A session manager runs once per instance.* The server is a module singleton; the second app
   start in one process (tests, `uvicorn --reload`) failed. Fix: build a fresh MCP HTTP app on each
   startup and route to the current one. Found by the existing API tests, not the new ones.
6. *A plain function isn't ASGI to Starlette.* Starlette treats a function endpoint as
   request→response; a callable object gets raw ASGI.
7. *Redaction leaked through a side door.* Summary, file note and flags were redacted on a hold,
   but the `checks` section still carried the sanctions check's own summary line with the listed
   name. Caught by a test that greps every section for the name.
8. *Shared rate limit made tests order-dependent.* MCP and API share the hourly run cap;
   12 more runs per test session crept towards 30. Reset per test.

**Numbers (demo data, in-process / localhost):** `prescreen_company` summary 2.3–4.6 KB vs a
full report of ~30–60 KB; 9 tools; HTTP round trip for a full demo pre-screen 0.19 s (no network
sources); live timings come after deploy. Protocol negotiated: 2026-07-28 (modern client);
a 2025-11-25 handshake also works.

**Tests:** 13 MCP tests through a real MCP client session (in-process and over HTTP via the API),
plus a stdio launch check. Full suite 156.

---

## Blog angles collected so far

1. "My due-diligence agent reported on the wrong company": the identity gate (01).
2. "Clear was the most dangerous word in the app": the coverage invariant (04).
3. "I took the agents out": 8–11 calls → 1 (01, 02).
4. "The check that could never fire": the disqualified-officer ID bug.
5. "My mock data hid five bugs": real-engine previews (Phase A above).
6. "An AI underwriter reviewed my UI": persona review agents.
7. "Putting the agents back where they belong": the MCP contract and its design rules.
8. "What MCP 2 changed, and four ways a deployed MCP server fails": the SDK and transport gotchas above.
9. Coming: the multi-agent orchestrator, and the three-way comparison (rules + 1 call vs
   multi-agent vs the old graph) on cost, latency, coverage and correctness.
