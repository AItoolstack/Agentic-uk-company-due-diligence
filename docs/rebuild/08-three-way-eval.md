# Three-way evaluation: agent graph vs rules + one call vs rules + agents (19 Oct 2026)

**Question.** Is the rebuild better because the *architecture* is better, or only because the old app ran on a
free model tier? And what does the agent layer add on top of the rules?

**Set-ups (9 real UK companies, one run each, 21:35–22:05 BST):**

| | What runs | Model |
|---|---|---|
| **G** old graph | `src/graph.py`: 10-node LangGraph research graph (query understanding → entity resolution → planner → source selector → collectors → news classifier → critic → gap detector → follow-up loop → contradictions → synthesis). The model decides the risk level. | `gpt-6-luna`, non-strict structured output (its best shot, see below) |
| **P** pre-screen | Written rules (draft-2026-10-19e) over registers; one model call reads the news | Groq `gpt-oss-20b` |
| **A** pre-screen + agents | P, then the agent layer over MCP: supervisor, specialists, guardrail. The route is copied from P | `gpt-6-luna` |

Running G on the same model as the agents takes the free tier out of the argument. On the first try (Greggs) it
failed anyway: OpenAI's strict `json_schema` mode rejected two of its Pydantic schemas, the graph swallowed both
errors, and the brief still came out "low risk, confidence 0.68". That's a porting problem, so the scored runs
use non-strict function calling: G got 0 failed calls in all 9.

**Ground truth** (`eval/ground_truth.json`) was written before the runs: status, key facts and expected band per
company, from Companies House, the FCA Register and the Gazette, re-checked against the registers during the run.
One fact was corrected after verification (Wilko: micro-entity accounts and SIC 99999, not "dormant accounts");
the correction is recorded in the file. **Scores** with quoted evidence: `eval/scores.json`. Raw runs: Supabase
`reports`, `agent_runs`, `eval_runs` (ids in scores.json).

## Results

| | G old graph | P pre-screen | A pre-screen + agents |
|---|---|---|---|
| Right company number | 9/9 | 9/9 | 9/9 |
| Status correct | 9/9 | 9/9 | 9/9 |
| Key facts found (9 in all) | **3 of 9** (1 found + 4 half-found) | **9 of 9** | 9 of 9 (from P) |
| Outcome in the expected band | 8/9 (Tesco: refer on 2018 notices) | 9/9 | 9/9, route unchanged 9/9 |
| Wrong statements | **6** | 1 | 0 added; caught P's 1 |
| A check that didn't run, presented as clear | 2 (+ sanctions never ran, 9/9) | 0 | 0 |
| Model calls | 134 (14.9 a run) | 9 (1 a run) | 63 (7 a run) |
| Tokens in / out | 131k / 61k | 16k / 7k | 103k / 14k |
| Cost, 9 companies (list price) | $0.0436 | $0.0033 | $0.0155 |
| Cost per company | $0.0048 | $0.0004 | $0.0017 |
| Time, median (min–max) | 89 s (68–106) | 9 s (5–151*) | 23 s (13–157*) |

\* Linklaters: 336 members; the pre-screen spends 150 s reading appointments and still says it couldn't check them all.
Costs use third-party list prices ($0.10/$0.50 per M tokens for gpt-6-luna; $0.075/$0.30 for gpt-oss-20b on Groq;
OpenAI's own page couldn't be reached from here). P actually ran on Groq's free tier.

## What the old graph got wrong, with a good model

The model upgrade fixed what the baseline (01) blamed on the free tier: every company resolved, no schema
defaults, no rate-limit failures. The writing is careful; it hedges and flags its own contradictions. What's left
is what a model can't fix, because it isn't in the model:

1. **Its sanctions screen never ran.** "Entities screened: 0" in 9 of 9 runs, and every brief says "no sanctions
   hits". Eight also say nothing was screened; Linklaters' doesn't. (Corrected after the independent rescoring: Wilko's does.) That's the coverage failure from the
   baseline, still there under a better model.
2. **Its FCA check returned "unknown" for every firm**, including an authorised bank (Monzo, FRN 730427) and an
   e-money institution (Revolut, FRN 900562). It found Monzo's £21m fine only through web search.
3. **The phoenix score counts the company under review.** BrewDog scores 1.00 because a director's one "failed"
   company is BrewDog itself; Carillion's 1.00 includes Carillion. Linklaters scores 0.78 from one dissolved
   company in 17. Dissolutions still count as failures.
4. **It doesn't know today's date.** Filings from Sept–Oct 2026 are called "future-dated" (Wilko, Tesco, Revolut).
5. **No time rule for old news.** Tesco: medium risk and three referral triggers, led by 2018 final notices.
6. **Missed register facts the rules read directly:** Revolut's overdue confirmation statement; Wilko's non-trading
   record (G gets no proposal, so it can't compare; but it didn't report SIC 99999 either); dates for Carillion's
   liquidation and BrewDog's administration; the amount of Thames Water's Ofwat fine.

Fair to G: it gets no proposal (trade, turnover, directors), so it can't do proposal-vs-record checks; and its data
connectors are part of the system as built. Its *reasoning* was rarely the problem. Its *plumbing* was, and an
agent graph gave nothing a place to say "this didn't run".

## What the pre-screen got wrong

One wrong statement: **Revolut's €3.5m AML fine** is counted as serious adverse news about Revolut Ltd. The Bank of
Lithuania fined Revolut Bank UAB, a different group company. The news-reading call accepted "Revolut" as the
company. The route stays refer because the confirmation statement is overdue, but the reason is wrong. Also one
cosmetic bug: Revolut's FCA check is "info" with an empty summary line. Both logged for a fix.

## What the agents added

- **They caught the core's one error.** On Revolut, the news agent wrote "Unclear ... does not say which Revolut
  entity was affected". The rules still route on it, because agents can't change a route. That's the design
  working, and it's also the limit of the design: the catch is only useful if a human reads it.
- **Earlier today they exposed a rule bug** (director timing, rules 19e). Over the day, the agent layer found two
  real defects in the core, which is the best argument for it.
- **Cost: 4× the pre-screen's tokens, a third of the old graph's cost, a quarter of its time.**
- **The guardrail's judge rewrote 2 of 15 sections** this batch. One was right (Monzo's email said "companies";
  there's one). One exposed a tool gap: the rules say De Brunner joined Babylon "1 month before" its administration,
  measured from the Companies House insolvency record, but no MCP tool returns insolvency dates, so the agent only
  saw the Gazette date and the judge disputed it. `company_record` should return insolvency case dates.

## Verdict

| | Use it for |
|---|---|
| Rules + one call | The decision support: facts, gaps, route. 9/9 facts, 9/9 band, 1 error, 9 s, $0.0004. |
| + agents | Explanation and drafting on top. +$0.0013 and ~14 s a case. (The "second reader that catches the core's mistakes" claim is withdrawn: see the independent rescoring below.) |
| Agent graph alone | Not for this. A strong model writes careful prose about checks that never ran. |

## Limits of this evaluation

- 9 companies, one run each; no variance. The demo cases aren't included: the old graph can't run them (they're fixtures for the pre-screen's data layer), and they're covered by the test suite.
- Scored first by the system's builder, then rescored blind by a separate agent (below). Where they differ, the independent score is the one to quote.
- P's band score benefits from rules 19e, changed today partly because of the agent runs on these same companies (Tesco moved from refer to note). On 19c, P's band would be 8/9.
- The three set-ups use different models for the core (G and A on gpt-6-luna; P on gpt-oss-20b).

## Independent rescoring (19 Oct, 22:30–22:45)

A separate agent that hadn't seen the scoring rescored all 27 runs from the raw payloads and the ground truth,
writing its scores before opening `scores.json`. Full scores, evidence and every disagreement:
`eval/scores_independent.json`. Two of its claims were spot-checked against Supabase and hold.

| | G old graph | P pre-screen | A + agents |
|---|---|---|---|
| Key facts (of 9) | **2** (builder 3) | 9 | 9 (from P) |
| Outcome in expected band | 8/9 | 9/9 | 9/9, route kept 9/9 |
| Wrong statements | **4** (builder 6) | **2** (builder 1) | **1 added + 2 minor** (builder 0) |
| Gap shown as clear | **1** (builder 2) | **1, borderline** (builder 0) | 1, passed through from P |
| Caught P's error | – | – | **partly** (builder: yes) |

**What changes.**
- *G is a little better than written.* The circular phoenix score is a design flaw (point 3 above) but G states
  its basis and hedges it, so it isn't a false statement. Monzo's FCA authorisation was missed, so G's facts are 2.
- *P had a second error.* Revolut's trade check passed "Payments and e-money" as consistent with SIC 62090, which
  is "Other information technology service activities". The model was given bare SIC numbers and guessed. Fixed
  (rules 19f): it now gets the labels, and the live re-run refers Revolut on the mismatch.
- *P's borderline gap.* Linklaters' FCA check said "not a regulated trade by SIC code"; an LLP has no SIC codes.
  The answer is probably right for a law firm, but its stated basis didn't exist. Wording fixed.
- *The agents' headline was overstated.* The Revolut news agent said the article didn't say which entity was
  affected; it didn't say it was a different one, and the same run's broker email asked the broker about "the
  serious adverse news", passing P's error on. And the Wilko broker email said "files as dormant". That wording
  came from the core's own broker question, which said "files as dormant" whether the accounts or only the SIC
  code (99999) were dormant; Wilko files micro-entity accounts. Fixed at the source; the guardrail passed it
  because it was in the evidence.
- *The guardrail is inconsistent on plurals.* It blocked "companies" for one company (Monzo) but passed "directors
  left" for one director (Tesco) and "directors who joined" for one (Revolut). Logged.

**Missed by both scorings, found by the rescorer:** P's news search found nothing adverse for Linklaters while G
found a tribunal claim (outside the ground truth); P counted KPMG's audit fine as adverse news about Carillion.

**Revised verdict.** The ranking holds: rules + one call is the most accurate and cheapest; the old graph is worst
on facts and plumbing. The agent layer's value is explanation and drafting, not error correction: in this batch it
flagged doubt about one core error but didn't correct it, and its one external output (the broker email) can
carry a core error past the guardrail. "A second reader that catches the core's mistakes" is withdrawn.
