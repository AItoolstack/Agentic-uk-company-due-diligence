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

## Logged items and the external review (19 Oct, 22:20–22:45)

**Input.** The four items logged in step 3, plus an independent review Tej commissioned from another AI agent
(an underwriter-style UI review, a UI designer review and a UAT table, run against live Monzo report
93d29f9e008044a7). Three of the reviewers' P1 findings turned out to share causes with the logged items.

**Same bug, two disguises.** Revolut's €3.5m fine (on Revolut Bank UAB) counted as Revolut Ltd's, and a
profile of Monzo Bank Europe DAC listed under "About this company" for Monzo Bank Limited, are one defect:
the news reading had "this company / not this company" and nothing in between. Group companies sharing a
brand fell into "yes". Fix: the model now also returns `entity_named` (the legal entity the article names,
the fined entity for a fine) and may answer `related`. A code check backs it up: if the named entity starts
with the company's brand and adds words the registered name doesn't have ("Revolut" + "Bank UAB"), it's
related whatever the model said. A brand alone ("Monzo") can't be told apart by name, so the model's answer
stands there. Related news is shown under its own heading and as a note; it can't be a reason for the route.
Trade-off accepted: Tesco Stores Limited news is "related" when screening Tesco PLC. Legally right; an
underwriter still sees it.

**The agents said "no news finding" under a £21m fine.** Not a model error. The agent layer decided
eligibility from *flags*, and earlier on 19 Oct we demoted "news of an FCA action already found" to a note
so the same fact wasn't a reason twice. That dedup silently removed the news from the agents' view, and the
skip message filled the gap with a sentence that wasn't true. Fix: the MCP summary now carries news counts
(adverse about this company, about a related company, not reviewed); the news agent runs on any adverse
item, flag or note; the skip message says what was read ("read 12 results: nothing adverse to explain").
Lesson for the series: a dedup in the core changed what the agents could see, and nothing failed loudly.

**Three different coverage counts on one report.** "13 of 13 checks applied", "0 couldn't check", and the
agent review's "1 check couldn't run" were all describing the CCJ check, which the tool doesn't do (paid
data). Fix: one coverage function (`casefile.coverage`) with four states: completed (ran over everything; not
the same as cleared), couldn't check (a source failed), not covered by this tool, not applicable. CheckResult
gets `out_of_scope`; CCJ stays `couldnt_check` (never clear) but counts as not covered. The report stores
`coverage`, and the ledger header, rules summary, file note, MCP summary and agent review all print its text.

**Smaller fixes.**
- Revolut's FCA check: "info" status with an empty summary. Any info check with no summary now uses its notes.
- `company_record` (MCP) returns the insolvency record: cases, dates and `began_on`, the date rule 19e
  measures from. The director agent is told to use it rather than a Gazette date. That closes the gap where
  the judge disputed a correct "1 month before" (Thames Water / Babylon).
- The director broker question counts people and companies: "A director was involved with a company that…".

**Tests:** 277 (34 new). Commit 0ff4551.

**Independent rescoring** of `eval/scores.json` started in parallel: a separate agent, blind to the
builder's scores until its own are written (`eval/scores_independent.json`).

## Live re-run, rules 19f, and the independent rescoring (19 Oct, 22:35–23:00)

**The fix met a new article.** Re-running Revolut live after the entity fix, Brave returned a different story:
"Italy fines Revolut over $13 million". The model named the entity "Revolut", which for REVOLUT LTD normalises
to the company's own name, so the code check called it exact and the fine became a reason again. Italy's
regulator acts on the EU entities, not the UK one, but nothing in the headline says so. Rule 19f: for a company
with a corporate owner on its PSC register (so other companies share the brand), a regulator's action counts
only when the article names this entity *with its legal form*; otherwise it's "may be about this company",
shown and never a reason. Standalone companies keep the old behaviour ("HSE fines Smith Builders" still counts).
A UK regulator's action on this firm still reaches the route through the FCA check, which reads the Register,
not the news. On the 19f re-run the model itself answered "related" for the Italy fine.

**Live re-runs, same proposals as the eval (rules 19f):**
- Revolut 1b6456a0087b4455: refer; reasons now the overdue confirmation statement and the trade/SIC mismatch.
  The Italy fine is listed under another company in the group. Coverage line identical everywhere.
- Monzo 198c0e28c6ca4be0: refer; the FCA's own release names Monzo Bank Ltd, so it stays this company's news
  (a note under the FCA action); a Guardian piece naming only "Monzo" is "may be about". Agent review: the news
  agent now runs (it was skipped with "no news finding" before); the director agent dates Nivaura's insolvency
  from the Companies House record (21 Nov 2023) via the new `began_on`. $0.0028, 27.6 s, 0 rewrites.

**The independent rescoring** (separate agent, blind until its scores were written; `eval/scores_independent.json`,
summary in 08) changed four headline numbers: G wrong 6 → 4, G facts 3 → 2, P wrong 1 → 2, A added 0 → 1, and
"agents caught P's error" → partly. Two of its findings were checked against Supabase and hold. What it found
that matters most:
- *P's second error:* the trade check passed "Payments and e-money" against SIC 62090 (IT services). The model
  got bare codes and guessed what they cover. Now it gets the labels and is told to read them literally.
- *The agents' one wrong statement came from the core.* Wilko's broker email said "files as dormant"; the core's
  broker question said that whether the accounts or only the SIC code (99999) were dormant. The guardrail passed
  it because it was "in the evidence". A guardrail that checks agents against the report can't catch the
  report's own mistakes. Fixed at the source: the finding and question say which signal fired.
- *The guardrail's judge is inconsistent:* it blocked "companies" for one company, passed "directors" for one
  director twice.
- *My write-up overstated the agents.* "A second reader that catches the core's mistakes" is withdrawn in 08.
  This is the most useful thing the rescoring did, and it's the reason to have one.

**Blog note.** The honest story for angle 11 is now: the agent flagged doubt about the core's mistake, couldn't
act on it by design, and its broker email still repeated the mistake. That is a better post than the one I was
going to write.

**Tests:** 284. Commits d5fc673 and the dormant-wording commit.

## Overnight: the case page at /case (20 Oct, 00:50–03:00)

Spec: `09-ui-blueprint.md` (a markdown copy of the Claude Docs blueprint, rev 14). Design: Direction B boards
(Pro, ProDesk, ProPhone, ProHold) on the design canvas. Run unattended; no route-affecting change; rules stay
**draft-2026-10-19f**.

**Backend, blueprint changes 3–9** (`src/prescreen/caseview.py`, commit 7497a0b):
- *News events.* Articles that back a finding (same kind of event, published within 14 days of the finding's
  event date, or cited by a media finding) become one event; leftover adverse articles about this company are
  one event per category; the rest go to sections: may be this company, other group companies, set aside,
  background, not reviewed. Only "about this company" articles (and brand-only ones inside a finding's own
  event) can be in an event, so a group company's news can't reach "Events about this company". Tested.
- *Sources read.* `sources_read` on the report: each register with its state and the time it returned
  (taken from the engine's progress ticks). The news source is labelled "web news search"; the provider name
  stays in the Analysis tab.
- *Decisions.* `identity_confirmed`, a new outcome `compliance` (the only one on a hold), and `overrides_route`
  worked out on the server from the route instead of trusted from the client. From the case page an override
  needs 20 characters. The page at / doesn't send `identity_confirmed`, so for it the old behaviour holds
  (decision allowed, recorded as not confirmed). That's deliberate, so the old page keeps working; when /case
  replaces /, make the field required (one line in `caseview.check_decision`). Supabase migration
  `20261020005756_decisions_identity_and_compliance` applied (additive column, check widened).
- *Proposal parsing on the server.* `parse_money` / `parse_years`, the same rules as the page: 2400000,
  2,400,000, £2.4m, 750k, 1.2bn; "1.5" years, "two million", "£2,40,000" and "12 34" refused with an example.
  The request validators use them, so the API reads "£2.4m" the same way the field does. `proposal_read` echoes
  what was read.
- *Broker email* (`broker_email`) built from the broker questions: subject "<Company> (<number>): information
  needed", the cover in scope, the numbered questions, "[Your name]" for the page to fill. None at all on a
  compliance hold.
- *Cases API.* `GET /api/prescreen/cases?filter=&page=`: route, coverage, latest decision, cover, run time,
  stale (>24 h), 25 a page, counts per filter. Postgres reads it with one query (checked against the live
  database read-only before use).
- *Deep links.* `/case` and `/case/<report id>` serve the page; a re-run carries `rerun_of` and links back.

**The page** (`frontend/case.html`, one file, vanilla JS, about 1506 lines; commit dbb712b). Desk, case header,
Overview, evidence drawer, right rail, six tabs, five route variants, seven system states, as the blueprint
lists them. Two small backend additions it needed: the company card now carries people with appointed/resigned
dates and owner detail (People tab), and the run-limit 429 says when the next slot is.

**What broke on the way (all fixed before the commit):**
- *Unread news showed as "background".* In demo mode there's no model, so articles keep the default
  `unsure` with no category; the grouping read "unsure + not adverse" as background. Unclassified now means
  not reviewed. Same lesson as the coverage contract: something nobody read is never a result.
- *Provider names leaked through check summaries* ("12 results reviewed (Brave Search)"). The page rewrites
  them to "web news search" outside the Analysis tab. The summaries themselves are unchanged for the old page.
- *The hold panel said "nothing on this page names the person".* The Owner key fact (public PSC data) does name
  the PSC who matched. Reworded to what's true: the page doesn't say *which* name matched or which list entry.
- *A sticky rail with its own scroll box* hid the Watch panel below the fold. Now the rail is sticky only when
  it fits the window.
- *Findings table squeezed to one word a line* at 1024 px. Fixed column widths, finding column takes the rest;
  the director-link row's primary source is now the linked company's insolvency record, as on the design board.

**Verification** (`tests/ui/verify_case_ui.py`, Playwright, demo data): 50 acceptance checks over the 7 demo
cases at 390, 563, 768, 1024 and 1440 px, **50 of 50 pass**. They include: evidence before the decision at
every width; no sideways scroll; Desk → case → Back → new run with one page load; "£2.4m" → £2,400,000 and the JS
parser agreeing with the Python table case for case; one coverage count on Overview, Checks, Analysis, file
note and MCP; each referral reason's primary source one click away; identity blocks recording; an override is
labelled and needs 20 characters; two decisions both kept; Copy honest both ways (granted and blocked
clipboard); hold case with no broker text and no list detail on any tab; stale, ended, run-limit, run-failed,
running and source-failed states. Stale and ended were produced by patching the API response in the browser
(no demo case is three days old or in liquidation). The Analysis tab with a run was shown with a *scripted*
agent run (`tests/ui/seed_scripted_analysis.py`: real MCP tools, guardrail and trace; the agents' replies come
from the test script). Screenshots: `docs/rebuild/ui/` (53 PNGs, quantised to 128 colours).

**Deploy.** `deploy-space.yml` dispatched on main at dbb712b: success (the snapshot reached the Space). The
sandbox's proxy refuses `*.hf.space`, so I couldn't load the Space to confirm the build or re-run the nine live
companies. Those wait for the morning.

**Numbers.** Tests 284 → 318. Rules version unchanged. No route changed on any demo case.

**Blog angles from tonight.** (13) "Unread is not background": the same coverage bug, one layer up, in the UI.
(14) "The server decides whether you overrode it": why `overrides_route` moved off the client.

## Direction D and rules 20g (20 Oct, morning)

**Design.** Tej's review of the live `/case` page: the layout was right, the look too plain in places and too busy in
others (the outcome block cramped, every check group drawn the same way whether or not anything was wrong). Two canvas
rounds: C (a colour per underwriting question, charts) was rejected as one-off; D was approved: quiet by default,
colour only where something needs review. Built into `frontend/case.html`:
- cool grey ramp, one blue for actions, amber/red only on findings, rows and tabs that need the underwriter;
- a four-cell summary strip (recommendation, referral reasons, coverage as one segment per check, basis) in place
  of the outcome and coverage panels;
- checks by question as neutral rows; a row with a flagged check gets an amber tint and "N to review";
- key facts as one divided line; tabs with a small amber dot instead of count pills; a 64 px icon rail
  (labels in the phone drawer); policy version moved to the status bar;
- dates always "13 Sep 2026" (Chrome's en-GB short month is "Sept").
Offline checks: 50 of 50 still pass; screenshots in `docs/rebuild/ui/` replaced.

**Rules 20g (route-affecting, so a new version).** Today's live Revolut run counted "Revolut Fined €3.5 Million for
AML Failures" (the fine was on Revolut Bank UAB) as serious news about Revolut Ltd: the model said the article named
"Revolut Ltd". 19f trusted that claim. 20g checks it: in a group, a regulator's action counts only when the named
entity, with its legal form, appears in the headline or snippet the model was shown (`media.entity_in_text`).
Otherwise it's "may be about this company": shown, never a reason. The FCA's own action on a firm still reaches the
route through the FCA check. Expected effect on the nine live companies: no route change (Revolut stays refer on its
overdue confirmation statement; Monzo stays refer on the FCA Register's final notice). Tests 318 → 321.
