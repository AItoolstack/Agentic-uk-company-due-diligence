# Company Pre-Screen: UI blueprint

_Markdown copy of the Claude Docs blueprint (rev 14), made 20 Oct 2026 for the repo._


2026-10-19 · Tej


## What's still not right in Direction B


Direction B has the right grammar (shell, case header, tabs, decision rail) but eight gaps, ranked by how much they would trip up an underwriter or a reviewer.


| # | Gap | Why it matters | Fix in this blueprint |
|---|---|---|---|
| 1 | Identity is a sentence ("Confirm number and address"), not a step | Wrong-company is the failure the project started from (Wilko) | An identity confirmation the decision can't be recorded without (Right rail) |
| 2 | Departing from the route looks the same as agreeing | An override is an authority question, not a click | Choosing another outcome shows "Overrides the recommended route" and requires a reason |
| 3 | Referral rows link out, nothing opens in place | The reader leaves the case to check one fact | Each row expands into an evidence drawer: what, when, relationship, source, question |
| 4 | No sense of how fresh the data is | A case reopened next week looks as current as today's | "Registers read 22:37" in the header; a stale banner after 24 h with Re-run |
| 5 | Checks pills don't say what they found or go anywhere | The matrix is decoration until it's clickable | Each pill opens the Checks tab filtered to it; hover shows its one-line result |
| 6 | News grouping is drawn, not built | The backend has no event grouping today | Group by matched finding (same regulator and date) in code; everything else by type |
| 7 | No loading, error, hold or clean-case design | Those are where trust is won or lost | Route variants and system states sections below |
| 8 | After "Record decision" nothing changes | No confirmation, no history, no audit feel | A recorded block (who, when, outcome, reason) and a decision history list |


Smaller: the parent name truncates in the key-facts strip; "Mgmt liability" abbreviates; the Analysis rail card duplicates the tab. All fixed below.


## Principles and visual system


The tool should feel like a system of record: dense, aligned, calm, with colour reserved for status.


Principles, in the order they win a conflict:

1. **Evidence before action.** On every width the reader meets route, coverage and findings before any decision control.
2. **A gap is never clear.** "Couldn't check" and "Not covered by this tool" always have their own glyph and count.
3. **Every claim has its source one click away**, with the event date, not just the publication date.
4. **The tool recommends; the underwriter decides**, and an override is visibly an override.
5. **Plain words.** No model names, scores or jargon outside the Analysis tab.


**Tokens**


| Token | Value | Use |
|---|---|---|
| Type | IBM Plex Sans 400/500/600/700; IBM Plex Mono for numbers and ids | Tabular figures everywhere |
| Scale | 12 / 13 / 14 (body) / 16 / 22 (case title) / 24 (outcome) | No other sizes |
| Ink | #101828; secondary #5B6576; borders #DCDFE4; rules #EEF0F3 | Secondary text passes 4.5:1 on white |
| Surfaces | Page #F3F4F6; panel #FFFFFF; table head #F9FAFB; rail #101828 |  |
| Action | Blue #1D4ED8, hover #1E40AF | Buttons and links only, never status |
| Spacing | 4-px base: 4, 8, 12, 16, 20, 28 | Panel padding 16; page gutter 28 (16 on phone) |
| Radius | 6 panels and buttons; 4 pills |  |
| Focus | 2 px #1D4ED8 ring, 2 px offset | Every interactive element |


**Status, one look everywhere** (pill, ledger, recent cases, timeline)


| State | Glyph | Pill colours | Words |
|---|---|---|---|
| Compliance hold | filled square | #101828 on white text | Compliance hold |
| Outside appetite | stop octagon | #FEE4E2 / #B42318 | Outside appetite |
| Refer | triangle | #FEF0C7 / #93370D | Refer |
| Note | ring | #F2F4F7 / #344054 | Note |
| Completed, nothing found | tick | white / #344054 | Nothing found |
| Couldn't check | hatched box | white, hatched / #344054 | Couldn't check |
| Not covered by this tool | dashed box | white, dashed / #344054 | Not covered |
| Not applicable | dash | none / #5B6576 | Not applicable |


No green anywhere: a clean result is neutral, so "nothing found" never reads as "safe".


**Copy rules**: British English; dates as 7 Jul 2025; "Refer" not "medium risk"; "No referral triggered by completed checks" for a clean route; never "clear", "clean" or "no issues" for anything that wasn't checked; LLPs say members.


## App shell and responsive layout


One shell for every screen: left rail, a header for the current screen, the content, and a status bar.

- **Left rail (216 px, #101828):** product name and "Commercial underwriting"; New pre-screen; Cases; Watchlist with a count of unread changes; at the foot, the screening policy version (links to the rules page) and "Demo data" when in demo mode.
- **Screen header (white):** breadcrumb, title, status pills, actions on the right, then tabs where the screen has them.
- **Content:** main column (fluid, min 600 px) and, on a case, the right rail (360 px, sticky from the tabs down).
- **Status bar (white, 32 px):** report id, run time and duration, each source with the time it was read, model calls.
- **Live region:** one visually hidden `aria-live=polite` region announces "Pre-screen started", "Pre-screen ready: Refer", errors.


| Width | Rail | Case layout | Decision |
|---|---|---|---|
| 1280 px and up | Full, 216 px | Main column + right rail | In the rail, sticky |
| 1024–1279 | Icons only, 64 px, labels as tooltips | Main column + right rail (320 px) | In the rail |
| 768–1023 | Top bar with menu button | One column: rail panels move below the evidence | Sticky footer button "Review and decide" jumps to it |
| Under 768 | Top bar: name, Cases, New | One column; key facts become 2 per row; tabs scroll sideways | Same sticky footer button |


On every width: New pre-screen and Cases are one tap away, browser Back returns to the Desk, and nothing scrolls sideways except wide tables inside their own box.


## Desk


The Desk is where a pre-screen starts and where open work waits: a new pre-screen panel on the left, recent cases and watchlist changes on the right.


**New pre-screen panel**


| Field | Behaviour |
|---|---|
| Company number or name | Required. A number (8 characters, or OC/SC/NI prefixes) goes straight to the run; a name opens the candidates list. Never auto-picks a near match. |
| Cover lines | Checkboxes: Property, EL / PL, Management liability, Professional indemnity, Cyber, Trade credit, Financial institutions. None ticked = "General pre-screen", labelled as such on the case. |
| What the proposal says (folded) | Declared trade, turnover, years trading, directors named, "should be FCA authorised". |
| Turnover | Accepts 2400000, 2,400,000, £2.4m, 750k, 1.2bn. Shows "Read as £2,400,000" under the field as you type. Anything else: inline error with an example; the run button stays disabled. Never strips characters silently. |
| Years trading | Whole years only; "1.5" gets an inline error, not 15. |
| Run pre-screen | Primary button. Disabled until the number/name is valid. |


**Candidates list** (a name with more than one match): table of name, number, status, incorporated, registered town; each row a real link that runs the pre-screen on that number. Dissolved companies are shown, greyed, below a divider. No match: "No company matches. Try the Companies House number." with the field kept.


**Recent cases**: table, newest first, 25 per page.


| Column | Content |
|---|---|
| Company | Name as a link to the case (keyboard reachable), number in mono under it |
| Route | Status pill |
| Coverage | "13 of 14", with a hatched mark when anything couldn't be checked |
| Decision | Outcome pill + underwriter, or "Pending" |
| Cover | Short cover-line list or "General" |
| Run | Date and time; "Stale" tag after 24 h |


Filters above it: All · Decision pending · Refer · Outside appetite · Hold. Empty state: one line and the New pre-screen field focused.


**Watchlist changes**: one row per change, newest first: date, company link, what changed (new director, filing overdue, Gazette notice, owner change), severity pill, "Open case". Header line: "Re-checked every morning at 06:17 from the registers; news isn't re-read." Empty: "Watch a company from its case to see changes here."


## Case header and Overview tab


The header answers "which company, what scope, how fresh"; the Overview answers "what's the outcome, why, and what wasn't looked at", in that order.


**Case header**

1. Breadcrumb: Cases / company name.
2. Title row: company name (22 px), route pill, decision pill ("Decision pending" or "Referred by A. Smith, 20 Oct"), and on the right: Watch (toggle), Copy file note, Re-run with this proposal.
3. Key facts strip, six columns, full words: Company number (link to Companies House), Status and type, Incorporated with age, Activity (first SIC code and label; "+1" opens the rest), Owner (parent name in full, wraps), Scope (cover lines in full, or "General pre-screen").
4. Registered office on one line, then "Registers read 19 Oct 2026, 22:37". After 24 h: an amber line "Data is 3 days old. Re-run before deciding."
5. Tabs: Overview · Checks 14 · News 12 · People 7 · History · Analysis · Proposal. Counts are what's inside, not findings.


**Overview, top to bottom**


| Section | Content | Default |
|---|---|---|
| Screening outcome | Route word (24 px, route colour), one-line meaning ("to a senior underwriter before quoting"), "2 referral reasons · draft policy 19f · not approval to quote". Top border 3 px in the route colour. | Open |
| Coverage (beside it) | "13 of 14 checks completed", a segmented bar (completed / couldn't check / not covered), and one line naming each gap and what to do about it | Open |
| Referral reasons | Table: #, finding (bold) + one-line "why it matters for this cover", area, event date, primary source. Each row expands in place (below). Rows ordered by route severity, then event date. | Open |
| Notes worth reading | Same table shape, ring pills: board changes, charges, ownership, news worth a look | Folded, with count |
| Checks by question | Five rows (Identity & ownership, Financial standing, Management, Regulatory & legal, Reputation); each check a pill with its glyph, name and a 2-word result ("15 people, none"). Pill click opens the Checks tab filtered to it. | Open |
| News | Material events only: date, event title, article count, entity chips, link to the finding it backs. Line: "12 read · 1 event · 2 worth a look · 1 other group company · 6 background" | Open, max 3 events |


**Evidence drawer** (a referral row, expanded): what happened (2–3 sentences), relationship to this company (current or former, with dates), event date vs publication date, the primary source and up to 2 more, what the record doesn't show, the broker question it creates, and for director links the rows "shown but not counted" with their reason. One drawer open at a time; Esc closes it.


Note-only cases: the Referral reasons table is replaced by "No referral triggered by completed checks" and the notes table opens by default.


## Right rail


Four panels, in this order: Decision, Broker requests, Outstanding, Watch. The Analysis card goes; Analysis is a tab.


**Decision**

- Identity step first: a checkbox "I've matched the company number and registered address to the proposal". Record decision stays disabled until it's ticked.
- Outcome as three large radio rows: Proceed to quote · Refer · Decline. The one matching the route is preselected and marked "matches route".
- Picking a different outcome shows a line "Overrides the recommended route (Refer)" and makes Reason required, minimum 20 characters.
- Reason (required, 5–2,000 characters), Underwriter (required, remembered in the browser).
- Record decision (primary). On success the panel becomes a recorded block: outcome pill, name, time, reason, and "Record another decision" (records are added, never overwritten). On failure: the error in the panel, the typed reason kept.
- Decision history below the block: every record, newest first.
- On a compliance hold: Proceed and Decline are disabled with "Held for compliance"; only "Referred to compliance" can be recorded.


**Broker requests**

- Header: count of questions, segmented switch Questions | Email.
- Questions: numbered, each with a "why" link that opens its evidence drawer.
- Email: subject "<Company> (<number>): information needed", greeting with [Broker name], one line naming the cover in scope, the numbered questions, sign-off with the underwriter's name if entered. Built in code from the questions, so it's always available.
- After Analysis has run: a third option "Drafted" with the agent's email, labelled "Drafted by the analysis; read before sending".
- Copy: shows "Copied" only when the clipboard write succeeded; on failure the text is selected in place with "Press Ctrl+C to copy".
- On a compliance hold the panel shows "Held until compliance clears the case" and no text at all.


**Outstanding** (generated, not ticked): each not-covered check with what to use instead, each couldn't-check with its reason and Re-run, identity confirmation until ticked, FRN confirmation for regulated firms, "Proposal not given" when no proposal fields were entered.


**Watch**: a switch "Watch this company" and the line "Re-checked each morning from Companies House, the Gazette and the FCA Register. Changes appear on the Desk."


## Other tabs


Each tab is the full record behind one part of the Overview; the Overview never shows anything a tab can't back up.


| Tab | Layout | Rows / content | Controls |
|---|---|---|---|
| Checks | Table grouped by underwriting question | Check, status pill, one-line result, sources (with read time), why it couldn't run | Filter chips: All, Refer, Note, Couldn't check, Not covered, Nothing found; filter set from Overview pills |
| News | Sections in this order: Events about this company · May be this company (brand only) · Other companies in the group · Set aside (different business) · Background · Not reviewed | Date, outlet, title link, entity chip, category, severity, one-line reason | Search provider and "12 results read" in the header; background and set-aside folded |
| People | Directors / members table, then former (last 12 months), then owners (PSCs and parent chain) | Name, role, appointed, resigned, disqualification result, sanctions screened, insolvency links: counted / shown not counted, each with reason and dates | Row opens the person's appointments on Companies House; LLPs say members |
| History | Vertical timeline, newest first | Date, kind (people, filings, owners, money, insolvency, regulator), one line; material events bold | Kind filter; "Material only" on by default |
| Analysis | Run button, then plan (what ran, what didn't and why), sections, guardrail result per section, agent email draft | "Route unchanged: Refer" and the coverage line at the top; sections marked internal or for the broker | "How this run worked" folded: model, calls, tools, tokens, cost, time, full trace |
| Proposal | Proposal vs public record table | Item, proposal, record, verdict (matches / differs / not given / unknown) | Edit proposal and Re-run: a new case, linked to this one |


A tab with something needing attention shows a small count pill in the route colour ("News 12" stays grey; "Checks 14" gets an amber 2).


## Route variants and system states


The same case layout carries five outcomes and seven system states; only the outcome panel, pills and rail change.


| Route | Outcome panel | Rail changes |
|---|---|---|
| No referral triggered | Grey top border; "No referral triggered by completed checks"; "Not approval to quote" | Proceed preselected; notes table open |
| Refer | Amber; "Refer to a senior underwriter before quoting" | Refer preselected |
| Outside appetite | Red; "Outside appetite" + the reason; "A senior underwriter can override with a reason" | Decline preselected; Proceed requires an override reason |
| Compliance hold | Black panel, white text; "Held for compliance. Don't contact the broker about this case." No sanctions detail shown outside compliance. | Broker requests and email hidden; only "Referred to compliance" can be recorded |
| Ended company (dissolved, in liquidation) | Red; "This company has ended: <status, date>" before anything else | Only questions that still matter |


| State | What the user sees |
|---|---|
| Running | Case header with the company name, then a stage list (Companies House, appointments, FCA, Gazette, sanctions, news) ticking as each source returns, elapsed seconds, and "Large boards take up to 2 minutes" after 20 s |
| A source failed | The case still opens; that check shows hatched "Couldn't check: <plain reason>" in coverage, Outstanding gets a Re-run line |
| Whole run failed | Error panel in the case area: what failed, "Try again", and Back to Desk; the proposal is kept |
| No match / several matches | Candidates list on the Desk (Desk section) |
| Run limit reached | "30 pre-screens an hour on this demo; next slot in 12 min" |
| Stale case (over 24 h) | Amber line under the key facts with Re-run |
| Demo data | A "Demo data" tag in the header and rail foot; company numbers start ZZ |


All states announce through the live region; focus moves to the case title when a run completes.


## Backend changes the UI needs


Nine changes, none of which touch how a route is decided.


| # | Change | Why the UI needs it |
|---|---|---|
| 1 | Fix `officers_left`: Monzo lists 5 resignations, the timeline shows 8 (Peacock, Runham, Tummalapalli missing) | Board-change note and History must agree |
| 2 | Per finding: `event_date`, `relationship` (current / former, with dates), `primary_source`, `why_for_cover` | Referral table and evidence drawer |
| 3 | News events: group items that back a finding (same regulator, event date ±14 days) into one event; others by section | News tab and Overview news block |
| 4 | `sources_read_at` per source on the report | Freshness line and status bar |
| 5 | Decision record: `identity_confirmed`, `overrides_route`, list of records per case | Decision panel and history |
| 6 | Proposal parsing on the server too (same rules as the field) and echoed on the report | "Read as" and the Proposal tab agree |
| 7 | Broker email built server-side from the questions (`broker_email`) | Email view and file note use one text |
| 8 | Recent cases API: route, coverage, decision, cover, run time, stale flag | Recent cases table and filters |
| 9 | Case links: `/case/<report id>` deep links; re-run creates a new case linked to the old | Real links, Back button, shareable within the team |


## Build order and acceptance tests


Build in five steps; each ends with tests passing and a deploy, so a half-finished night still leaves a working app.

1. Backend changes 1–9, with tests.
2. Shell, tokens, Desk (form, parsing, candidates, recent cases, watchlist).
3. Case header, Overview, evidence drawer, right rail.
4. Checks, News, People, History, Analysis, Proposal tabs; route variants and system states.
5. Responsive pass at 390, 563, 768, 1024, 1440 px; keyboard and screen-reader pass; re-run the 9 live companies; retest the external UAT table.


The new design ships at `/case` beside the current page; it moves to `/` only after you approve it.


**Acceptance tests** (from the external review, plus this blueprint)

- At 390, 563, 768 and 1440 px a user opens a case, returns to the Desk and starts another without a reload
- Route, coverage and referral reasons appear before any decision control at every width
- One coverage count on Overview, Checks tab, Analysis, file note and MCP for the same case
- £2.4m reads as £2,400,000; 1.5 years and "two million" are refused inline; nothing changes silently
- Copy says Copied only on success; a failed copy leaves the text selected with a hint
- Recent cases and candidates open with keyboard only; the live region announces start, finish and errors
- Record decision is disabled until identity is confirmed; an override requires a reason and is labelled
- Each referral reason reaches its primary source in one click from the Overview
- A group company's news never appears under "Events about this company"
- Compliance hold: no broker text anywhere, no sanctions detail outside compliance
- A case over 24 h old shows the stale line
- The 9 live companies keep their 19f routes


## Decisions for Tej


Agreed with Tej on 19 Oct, 23:15: all three as below (the default in brackets).

1. **Identity confirmation blocks the decision?** (Yes: Record decision stays disabled until it's ticked.)
2. **Per-reason tracking** (mark each referral reason "answered by broker" with a note)? (Not now: park it; the decision reason carries this.)
3. **Replace the page at `/` tonight, or keep the new design at `/case` until you've seen it?** (Keep it beside the old one until you approve.)


Already decided (19 Oct, 23:00): outcome preselected to match the route; notes folded; ledger by underwriting question with each check named; broker email always available.

