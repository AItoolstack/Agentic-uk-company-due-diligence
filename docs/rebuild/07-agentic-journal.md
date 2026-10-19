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

---

## First live deploy (18–19 Oct, 23:30–00:30)

**Deploy path.** PR merged; a GitHub Action pushes a one-commit snapshot to the Space with images in
Git LFS (Hugging Face rejects plain binaries; the GitHub history has three PNGs as plain objects).

**Database: three failures, one lesson.**
1. Wrong password. Supabase's pooler logged "password authentication failed"; each page load retried
   several times until the pooler's circuit breaker blocked *all* new connections for minutes.
2. Then the direct connection string: `db.<ref>.supabase.co` is IPv6-only and a Space can't reach it.
   The app said "host couldn't be reached"; the pooler logs showed no attempts at all.
3. Then the value was saved as a public Space *variable* rather than a secret.
**Change made:** `FallbackStore`. On a connection-level error (psycopg OperationalError only, so
real bugs still raise) it switches to files for 5 minutes, says why in plain words (config + banner),
and stops hammering the database. Lesson: a dependency failing *loudly and repeatedly* can turn one
misconfiguration into an outage for everyone sharing the pooler.
**Debugging route worth a post:** the app's own error was vague; the provider's logs (read through
the Supabase MCP connector) named the cause each time in one query.

**First five live runs (Monzo, Wilko, Thames Water, Carillion, BrewDog), what they showed:**
| Found | Cause | Fix |
|---|---|---|
| Sanctions "couldn't check" on every company, so every company was Refer | Live CSV calls the type column "Designation Type"; parser looked for "entity type", got blanks, and (correctly) refused | Column name fixed against the real header, read from the FCDO site in the browser; test uses the real header line |
| Thames Water director checks "couldn't check" | Officers read one page (100); old companies have more | Officers paged |
| Carillion: 7 reasons for one collapse; "accounts 3,032 days overdue" under "in liquidation" | One finding per article; consequences listed as causes | One news finding per severity; consequences of an ended company leave the reasons |
| Monzo: a paragraph of FCA text as a reason | Register text used verbatim | Kind, date and gist; final notice linked |
| Wilko: 2023 collapse article marked "about this company" | The rename guard passes it (published after the rename) | Collapse news about an active company with no insolvency record → "possibly about", never "about" |
| Wilko trade check "not asked" though a trade was declared | Model chose the escape value | Prompt forbids it when a trade is given |

Note the pattern: the coverage invariant did its job. Sanctions failing showed as "couldn't check",
never as "clear". The cost was a Refer on every case — noisy, but safe. That's the trade-off the design
chose on purpose, and the live run is the evidence for it.

## Second live batch: from "wrong" to "noisy" (19 Oct, 00:30–01:30)

Ten real companies on rules draft-2026-10-19a. Nothing was *wrong* any more; the theme was the
**same fact said twice**, or a question that no longer mattered. Each fix has a test in
`tests/test_live_noise.py` / `test_live_noise_2.py`, and none changes a route.

| Company | Noise | Fix (rules 19b / 19c) |
|---|---|---|
| Thames Water | Three director lines, one each, as three reasons | One reason: "3 directors … (6 in all): De Brunner (4), …" |
| Monzo | FCA fine as a reason, then news of the same fine as a second reason; 7 broker questions | News of an FCA action already found is a note; questions capped at 5, notes only fill spare places |
| Carillion | "Why are accounts overdue?" for a company in liquidation since 2018 | Ended company keeps only the questions that still matter |
| Wilko | "Dormant but trading" and "trade doesn't match SIC" as two reasons | One reason, one question |
| BrewDog | One administration = case on record + two Gazette notices, one named "Gazette: BREWDOG PLC" | Gazette notices back up an insolvency on record instead of adding reasons; a notice is never named after the company |
| Tesco | 2017 accounting fine as a Refer in 2026; a shoplifter in a Tesco store as adverse news | Serious news older than 5 years is a note (`media_old`); prompt: a victim or bystander isn't adverse |
| Greggs | Summary "nothing adverse" above a job-cuts finding | Summary built from the findings |
| Revolut | "Not on the FCA Register" for an EMI that is | See below |

**Revolut, a debugging story worth its own post.** The check said no Register firm had the exact name.
The engine can't tell you why; the MCP server can. Calling `fca_firm_search` on the live endpoint from
the browser showed 15 fuzzy matches (Revolution Scooters, Skinny Revolution…) and no Revolut Ltd.
Second call, with paging info exposed in the tool's output: `total_count: 87`, `per_page: 20`, and a
`Next` link. Fix: follow `Next` (Register host only). Third call: found "Revolut Ltd" on page 2,
but FRN 975775, an *appointed representative*. Stopping at the first exact name would have turned
"not found" into "not authorised", which is worse. Final fix: keep paging until an *authorised* record
with that exact name turns up (page 4: FRN 900562, Registered), and prefer it.
Lessons: (1) the MCP tools double as a live debugging console for the agent that built them;
(2) "exact name match" is not identity; a name can belong to several Register records.

---

## Step 2: the agent layer (19 Oct, 08:00–)

**What was built** (`src/agents_v2/`): a LangGraph supervisor that reaches the pre-screen *only* through
the MCP server, three specialists, a guardrail on everything they write, and a trace of every call.

```
load_case (MCP) ──> plan ──> specialists, in parallel ──> assemble (code)
                              director_history  news  broker_email
                              each: tool loop over its own MCP allowlist ──> guardrail ──> one rewrite ──> show or withhold
```

**Decisions, and what was rejected:**

| Decision | Rejected alternative | Why |
|---|---|---|
| Who *can* run is code (`eligible`): a director flag, a news flag, broker questions. The supervisor model can drop one with a reason, never add one | A free supervisor choosing from all agents | An agent with nothing to work on writes something anyway. Tested: a supervisor that asks for an ineligible agent gets `refused_additions` |
| The final review is assembled by code; route, meaning and gaps copied word for word from the MCP summary | A model-written brief | Every retelling of the route is a chance to change it. Agent text goes underneath, labelled with who wrote it and how it was checked |
| Agent loop in JSON mode (`{"call": …}` / `{"final": …}`), 3–6 steps | Native tool calling / `create_react_agent` | gpt-oss tool-call JSON already failed on the baseline; JSON turns run on either provider, and the step limit is ours |
| Allowlist per agent, enforced in the MCP client wrapper; refusals are trace spans | Trusting the prompt | The broker-email agent has no `sanctions_screen`. A test makes it try; the call never reaches the server |
| Agents can't pick the case: any `report_id` they pass is overwritten | Passing it through | One case per run; an agent can't wander into another report |
| On the Space, agents call `/mcp` over real HTTP (127.0.0.1:7860), with the same access token as any client | In-process | The point is to show MCP as the integration surface. Locally and in tests it's in-process (same protocol, memory streams) |
| Guardrail rules in code; model judge on top; judge outage shown as "rules only" | Judge only, or rules only | Rules are exact and free for the four known failures; a judge catches "alleged" stated as fact. If the judge can't run, that's a gap, and gaps are shown — the pre-screen's own rule, applied to the agents |
| Broker email never *starts* on a compliance hold (code), and the guardrail blocks any external text on a hold | Guardrail only | Two independent locks. The first one means the cheapest case (0 model calls) is the most sensitive one |

**Guardrail rules** (each with tests, `tests/test_agents_v2.py`):
1. `couldnt_check_as_clear`: a sentence ties a check that couldn't run to a clearing phrase ("no CCJs",
   "news is clean", "all checks clear"). Negated sentences ("couldn't be checked") pass.
2. `route_changed`: names another route ("outside appetite" on a refer) or recommends a decision ("safe to
   quote", "should be declined"). Explaining the route the rules gave is fine.
3. `sanctions_leak`: anything about sanctions/compliance in broker text; any broker text on a hold; list detail
   in internal text on a hold.
4. `unsupported_fact`: company numbers, £ amounts, dates, years and percentages must appear in the evidence the
   agent was given (ISO dates in the evidence match "14 September 2023" in prose).
5. Judge: `unsupported_claim`, `allegation_as_fact`.
Blocked → one rewrite with the problems listed → blocked again → withheld, reason shown, draft kept in the run
for audit.

**Things that broke or surprised while building:**
1. *False positive on day one:* "decline" matched "sales were in decline". The route rule now needs
   "decline the case/risk/quote" or "should be declined". Kept as a test.
2. *The hold case runs nothing.* On Atlas (sanctions hold) no specialist is eligible, so the planner isn't
   even called: zero model calls, zero cost. The safest path is also the cheapest.
3. *Calder (Companies House down) runs nothing either*, and says why: "directors' other companies couldn't be
   checked, so there's nothing to narrate". An agent layer that quietly did nothing would read as "nothing found".
4. *A FK on agent runs.* `agent_runs.report_id` references `reports`; a report saved to files during a database
   outage can't take a database agent run. The save failure is caught and shown (`not_saved`), never fatal.
5. *Migration naming:* applied through the Supabase connector first, then the file renamed to the version
   Supabase recorded (20261019071411), per the CI lesson from Phase A.

**Numbers (scripted model, in-process vs HTTP, Northern Bake demo):** 14 spans; 3 MCP calls for the supervisor
over HTTP (connect, find_company 0.2 s, prescreen_company 0.06 s on demo data), summary 3.5 KB; the
broker_questions section 1.8 KB. Live numbers follow.

**Tests:** 33 new (agents) + 2 (agent-run storage, files and Postgres). Suite 214.

## Step 2, live: nine real companies through the agents (19 Oct, 20:50–21:30)

On the Space, `gpt-6-luna` for every agent and the judge, agents reaching the pre-screen over HTTP MCP
(`127.0.0.1:7860/mcp`, same access token as any client). Saved live reports, run one after another.

**First run broke, and the trace couldn't say why.** Monzo's director-history agent made four good tool calls
(two `company_record`, two `gazette_notices`), then three model calls with no usable answer, then gave up
("the agent didn't give a final answer"). The loop only accepted `{"final": {...}}`, and an unusable turn
left no trace of what the model *had* said. Fix: accept the common drift (final as a list or a JSON string,
the answer's own keys with no wrapper, a tool call with no `call` wrapper), and record any other reply as a
trace note with its keys and a preview. Tested with a scripted drifting agent. Re-run: Monzo finished.
Lesson: **a trace that records only successes can't explain a failure.** Log the shape of what you rejected.

**The batch (after the fix):**

| Company | Route (rules) | Agents run | Model / MCP calls | Tokens in / out | Cost | Time | Guardrail |
|---|---|---|---|---|---|---|---|
| Monzo | refer | directors, email | 11 / 6 | 27.4k / 1.7k | $0.0036 | 27.8 s | 1 rewrite (judge) |
| Wilko | refer | email | 3 / 2 | 3.0k / 0.2k | $0.0004 | 7.4 s | – |
| Thames Water | refer | directors, news, email | 16 / 9 | 56.9k / 3.3k | $0.0074 | 49.5 s | 1 rewrite (judge) |
| Carillion | outside appetite | news, email | 8 / 3 | 17.0k / 1.9k | $0.0027 | 26.5 s | 1 rewrite (judge) |
| BrewDog | outside appetite | news, email | 6 / 3 | 11.1k / 1.1k | $0.0017 | 16.8 s | – |
| Tesco | refer | directors, email | 7 / 4 | 9.2k / 0.8k | $0.0013 | 14.9 s | – |
| Revolut | refer | news, email | 6 / 3 | 8.2k / 0.7k | $0.0012 | 15.8 s | – |
| Greggs | note | email | 3 / 2 | 1.8k / 0.2k | $0.0003 | 6.8 s | – |
| Linklaters | refer | email | 5 / 2 | 5.4k / 0.5k | $0.0008 | 14.1 s | 1 rewrite (judge) |
| **9 runs** | **unchanged in 9/9** | 16 sections | **65 / 34** | 140k / 10.5k | **$0.019** | median 15.8 s, max 49.5 s | 4 rewrites, 0 withheld |

Per agent: director history 17 calls ($0.0073), judge 20 calls ($0.0058, **30% of spend**), news 9 ($0.0033),
email 10 ($0.0016), supervisor 9 ($0.0013). Median MCP call over HTTP: 1.5 s (live sources behind it).
No cached tokens, no rejected parameters; every call served by `gpt-6-luna`. The supervisor dropped nothing:
with eligibility already in code, every eligible specialist was worth running.

**What the guardrail caught (all four by the model judge; the code rules fired zero times live):**
1. *Monzo, directors.* Draft: "the record does not show whether she was still in post near the administration
   notice". The record did: Burbidge left SYMBIOCO in May 2022; the notice was Dec 2025. Rewrite: "she was not in
   post". The judge made the text *more* accurate, not just safer.
2. *Thames Water, directors.* "two holding companies", inferred from the names (HAYA HOLDCO). Not in the record. Removed.
3. *Carillion, news.* "a former Carillion chief executive was fined"; the result only said "ex-boss". Narrowed.
4. *Linklaters, email.* "the board" for an LLP. LLPs have members, not a board. Removed.

Honest reading: the code rules guard known failure modes (they pass 20+ scripted attacks in tests); on real
output the risk was subtler, and the judge found it. Rules are cheap insurance; the judge is where the
quality came from, at 30% of the cost.

**Things the agents exposed in the core, logged for a human decision (no rule changed):**
- *Director-link rule over-counts.* For a linked company in a *current* insolvency status, the event date is
  unknown, so any director in post in the 5-year window counts. Burbidge left SYMBIOCO 3.5 years before its
  administration notice and still counted towards Monzo's refer. The agent's narrative made that visible.
- *LLP wording.* Broker questions say "directors" for an LLP (Linklaters); should be "members".
- *Step budget.* Thames Water's director agent spent its 6 calls on De Brunner's four companies; the other two
  directors' companies weren't looked up. It said so plainly ("No company record … was provided"), which is
  the right failure, but a budget per director (or parallel lookups) would close it.

**Not tested live:** a compliance hold. None of the live companies is on one, so the "broker email never
starts" path is proven on demo data only (0 model calls, 0 cost).

**The comparison so far** (old graph numbers from 01):
| | Old LangGraph agent graph | Rules + 1 call (pre-screen) | Pre-screen + agent layer |
|---|---|---|---|
| Model calls | 8–11, deciding the outcome | 1 (news read) | 1 + 3–16, explaining only |
| Who sets the route | the agents | written rules | written rules (unchanged 9/9) |
| "Couldn't check" | became "clean" | shown, never clear | shown; agents blocked from calling it clear |
| Cost per case | – | ~$0 (Groq free tier) | $0.0003–$0.0074 extra |
| Extra time | – | 10–60 s | 7–50 s |

## Rules 19e: when a director left matters (19 Oct, 21:13–22:00)

**Where it came from.** The director-history agent's live narrative for Monzo showed what the rule couldn't:
Burbidge left SYMBIOCO in May 2022, and its administration began in Dec 2025. The rule counted it anyway,
because for a company *currently* in an insolvency status it had no event date, so anyone in post at any time
in the 5-year window counted. Tej asked for an underwriter's view; the reasoning agreed (logged with the commit):

| Director's position, measured from when the insolvency **began** | 19e | Anchor |
|---|---|---|
| In post, or left < 12 months before | counts fully; 2+ such links = outside appetite | s.216 IA 1986 (prohibited names) covers directors in the 12 months before liquidation |
| Left 12–36 months before | counts, refer at most | the insolvency practitioner's conduct report covers directors in the 3 years before |
| Left > 36 months before, or joined after it began | not counted, **shown** | can't have caused or traded through it |
| Start date not on record / record unreadable | counted, refer at most, broker asked for dates | a gap is never resolved in the company's favour |
| Members' voluntary liquidation | not counted, shown | a solvent wind-up: debts paid in full |
| Joined < 6 months before | counted, "possibly a rescue appointment" | turnaround and lender-appointed directors |

The start date comes from the Companies House insolvency record of each linked company (earliest petition,
winding-up, administration, CVA or receivership date): one extra call per linked insolvent company, capped at 40.
The 5-year lookback now applies to that date. Phoenix with unknown timing refers instead of declining.
Agents didn't change a route; the evidence they surfaced led a human to change a rule, with tests, as a new
rules version. That's the intended loop.

**Re-run of the 9 live companies, same proposals:**

| Company | Route 19c/d → 19e | What changed in the director check |
|---|---|---|
| Monzo | refer → refer | Burbidge/SYMBIOCO dropped ("left 3 years 6 months before it began"); WIREWAX was a **members' voluntary liquidation**; Lake/Nivaura stays ("in post when it began") |
| Thames Water | refer → refer | De Brunner 4 → 2: FAGUS HOLDCO and PLATAFORMA were **solvent MVLs**. The two Babylon companies stay, flagged "joined 1–3 months before, possibly a rescue appointment". Gething's HARRABEL was an MVL; Pearson's link began Nov 2020, outside the 5 years |
| **Tesco** | **refer → note** | Gilliland/Curious Drinks: the insolvency began around Apr 2021 (Gazette), outside 5 years; the old rule dated it by dissolution (Aug 2022) |
| Wilko, Carillion, Revolut | unchanged | MVLs now shown as not counted (Wilko: TRADING OLD; Carillion: WEIR USA INVESTMENTS) |
| BrewDog, Greggs | unchanged | nothing new |
| Linklaters | refer → refer | broker questions now say "members" |

**The bigger finding wasn't the one we went looking for.** The rule fix was about *timing*. The re-run showed
the old rule also counted **solvent wind-ups** as insolvencies: Companies House status "liquidation" covers a
members' voluntary liquidation, which is a company closing with its debts paid. Across the batch the old rule
counted 9 director links and 19e counts 3. Of the 6 that dropped: 3 were MVLs, 2 began more than 5 years ago,
1 the director had left 3.5 years before. MVLs appear 13 times in the "not counted" notes across 5 companies:
group restructures and investment vehicles (a VCT, holdcos) close this way all the time.

**Something the re-run caught in the fix itself.** Tesco's route changed because its only director link fell
outside the lookback, and that exclusion was *silent*: not in the "not counted" note, just gone. Correct by the
rule, wrong by the product's own principle (never drop something without saying so). Fixed: a link that would
have counted but is older than the lookback is listed with its date. Without the live re-run comparison
(old findings vs new, company by company) this would have shipped.

**Remaining:** the disqualification check's text still says "directors" for LLPs (only the director-link check
and broker questions were changed); cover-line weighting (director history should weigh most on D&O/management
liability and trade credit) is not done.

**Tests:** 22 new in `tests/test_director_timing.py` (each tier, MVL, unknown/unreadable, rescue, lookback,
phoenix, Northern Bake still declines). Suite 242.

## Step 3: the three-way evaluation (19 Oct, 21:27–22:10)

Full write-up: `08-three-way-eval.md`; ground truth and scores with evidence in `eval/`.

**How it was made fair.** The old graph ran on the same model as the agents (`gpt-6-luna`), through a new
eval-only endpoint that meters every LangChain model call. Its first run failed for a reason unrelated to the
architecture (OpenAI's strict schema mode rejects two of its schemas; errors swallowed, "low risk" anyway), so
the scored runs give it non-strict structured output. Ground truth was written before any run.

**Headline (9 real companies):** key facts 3/9 (old graph) vs 9/9 (rules); wrong statements 6 vs 1; outcome
band 8/9 vs 9/9; $0.0048 and 89 s vs $0.0004 and 9 s per company. Agents on top: +$0.0013 and ~14 s, route
unchanged 9/9, and they caught the pre-screen's one error (Revolut: a Lithuanian group company's fine counted as
Revolut Ltd's).

**The lesson for the series.** A better model fixed everything the baseline blamed on the free tier (identity,
defaults, rate limits) and nothing that was structural: the old sanctions screen screened 0 entities in 9/9
runs and every brief still said "no sanctions hits"; its FCA check said "unknown" for an authorised bank;
its phoenix score counted the company itself. Careful prose on top of checks that never ran is the worst
combination: it reads as diligence.

**Instrumentation built for this:** `/api/prescreen/eval/legacy` (old graph, metered, saved to `eval_runs`),
token usage on the pre-screen's call (`llm_usage`).

**Found and logged, not yet fixed:** (1) pre-screen news reading accepts a group company's fine as the
company's (needs an entity check on regulator fines); (2) Revolut's FCA check has an empty summary;
(3) `company_record` doesn't return insolvency case dates, so agents can't verify rule 19e's timing;
(4) the broker question for director links says "companies" even when there's one.

## Blog angles, updated

10. "I gave the old agent graph a better model. It got more polite, not more right." (08)
11. "My agent caught my rules' mistake, and couldn't do anything about it" (Revolut; agents explain, rules decide).
12. "The agent's narrative found a rule bug: when a director left matters" (rules 19e).
