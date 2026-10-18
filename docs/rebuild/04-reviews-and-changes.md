# Two independent reviews, and what changed (18 Oct 2026, 02:30–05:00)

After the first working version (commit 6861e96), two reviewers who hadn't seen it being built
read the branch: one as a senior UK commercial underwriter, one as a senior AI engineer.
Both found the same weakness from different sides.

## The shared finding
"Clear" was being reached by *not finding a match*. So every partial fetch, cap, threshold or
parse fallback quietly widened it. Examples they found in a version written specifically to stop
false-clean results:

- News results the model didn't return were counted as reviewed and clear. In the Monzo demo,
  the £21m FCA fine article sat in the results while the check said "nothing adverse".
- Directors after the 15th were never checked, but the summary said "N directors checked".
- A common-name disqualification search only read the first 10 hits.
- A sanctions-list date of birth that couldn't be parsed ("c. 1966") made an exact name match "cleared".
- If the sanctions CSV's type column wasn't where expected, every entry became neither person nor
  company, so directors were compared with nothing: clear.
- Corporate directors were screened as people, so a sanctioned company as director could never match.
- LLP members don't have "director" roles, so LLPs had no one checked at all.

**The rule now:** a check is Clear only when coverage is complete. Anything less is "couldn't check",
with the reason in words. Tests enforce it for each case above.

## Underwriter changes
| Was | Now | Why (reviewer's words, paraphrased) |
|---|---|---|
| Company < 12 months = refer | Note; refer only with a director-history finding | At 40 quotes a day it refers a big share of SME start-ups |
| Accounts overdue = refer | 1–30 days note, >30 refer, >90 + another distress signal = outside | Late by a week isn't distress |
| Strike-off proposal = outside | Voluntary (GAZ1(A)) outside; compulsory (GAZ1) refer | Compulsory is usually caused by late filings and gets suspended |
| Director history counted only companies *currently* in liquidation | Also companies that went through insolvency and are now dissolved | Most liquidations end in dissolution within 1–2 years: the old rule missed them |
| 3+ new charges | 3+ different lenders in 12 months | A refinance creates several charges in one day |
| Any overseas corporate owner = refer | Secrecy jurisdictions refer; mainstream ones note; UK parent's owners looked up | "Its own register shows the owners" was the tool admitting it didn't look |
| FCA history = refer | Refer for PI / D&O / FI cover in the last 5 years; note otherwise | Irrelevant to a property risk |
| Exact sanctions match = outside appetite | Compliance hold, its own route | Not an appetite decision |
| Green "Clear" with notes underneath | Neutral "Info" | Green hid the dormant flag on Wilko |
| — | Gazette insolvency notices (winding-up petitions) | Petitions appear before Companies House changes status |
| — | Board and PSC changes in 12 months | The renewal question: was this presented? |
| — | Declared turnover vs accounts size; dormant company with a declared trade | Day-one context |
| — | Cover lines on the request | The same finding matters differently for property and D&O |

Wording: "Not the same person" became "Likely a different person"; "Recorded as cleared" became
"Discounted on date of birth; confirm"; "Possible phoenix (and s.216 name re-use)" became "s.216
prohibited-name rules may apply: ask the broker"; "draft rule" moved from findings to the footer.

## Engineering changes
- Groq returns HTTP 400 `json_validate_failed` for bad JSON in JSON mode, which skipped the repair
  retry; now handled. A `finish_reason` of "length" is an error, not partial JSON. `reasoning_effort: low`
  for gpt-oss (reasoning tokens count against `max_tokens`). One model call at a time per process.
- Companies House calls throttled (6 at a time); appointments paged.
- An unexpected bug in one check becomes "couldn't check" for that check, not a lost report.
- Company number validated before it reaches a URL path (`../search` is rejected).
- Atomic writes for reports and the sanctions cache; the parsed list is kept in memory.
- Decisions are append-only. Optional access code (`PRESCREEN_ACCESS_TOKEN`). A service-wide
  hourly cap replaced a per-IP cap that trusted a header the client sets.
- Profile lookup failure is a 503 ("couldn't start"), not a 404 ("no such company").

## Checked live in the browser
- The Gazette search JSON works with no key: `/insolvency/notice/data.json?text=<company number>`.
  Searching by *name* is noisy: "Thames Water Utilities" returns a 2001 petition Thames Water brought
  against another company. So the app searches by number only.

- FCA Register search items use `Reference Number` and `Name` (confirmed from the Register API client
  docs). The old app read `FRN`, which doesn't exist: that is why Monzo came back "FCA status unknown".

## Still not verified against live services (needs a deploy)
- UK Sanctions List CSV header names (parser refuses rather than guesses).
- Brave news endpoint fields; Groq accepting `reasoning_effort` on gpt-oss-20b (falls back without it).
- Companies House disqualified-officer search `date_of_birth` format.
