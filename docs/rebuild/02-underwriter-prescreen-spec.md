# Underwriter pre-screen spec (draft, public guidance only)

Written from the point of view of a UK SME / mid-market commercial underwriter. Thresholds marked
**[draft]** are judgement, not a cited rule, and need an underwriting authority holder to confirm.

## Who uses it and when
A commercial underwriter (or an underwriting assistant) at **new business pre-screen or renewal**,
before spending time on a quote. The question is not "what is this company's risk score?" but
**"is there anything on the public record that means I should refer this, decline it, or ask the
broker a question before I quote?"** It has to answer in under 2 minutes, on one screen, with links.

## The checks
| Check | Looking for | Source (free unless noted) |
|---|---|---|
| Identity | One company number that matches the name; registered office plausible | Companies House (CH) |
| Status | Active / dissolved / liquidation / administration / proposal to strike off | CH profile `company_status`, `company_status_detail` |
| Age | Incorporation date; very new = no history, possible phoenix | CH profile |
| Filing compliance | Accounts or confirmation statement overdue | CH profile `accounts.overdue`, `confirmation_statement.overdue` |
| Insolvency history | Past or current insolvency cases | CH `has_insolvency_history`, `/insolvency`; The Gazette |
| Charges | Outstanding charges; a recent burst of new debentures | CH `/charges` |
| Directors | Disqualified; linked to insolvent companies (phoenix) | CH disqualified-officers search, `/officers/{id}/appointments` |
| Ownership | No PSC and no statement; overseas corporate PSC; long chains | CH `/persons-with-significant-control` |
| Sanctions | Company, directors, PSCs, >50% owners against the UK Sanctions List | UK Sanctions List (FCDO) / OpenSanctions |
| FCA authorisation | Only where the trade is regulated | FCA Register |
| SIC vs declared trade | Mismatch can mean misdescription | CH `sic_codes` |
| Adverse media & enforcement | Fraud, HSE prosecutions, EA enforcement, regulator fines | News/web search; HSE register |
| CCJs | Unsatisfied judgments | **Paid** (TrustOnline / CRA) — always "not checked" unless connected |

## Routes
The tool **recommends a route**; the underwriter **decides**.

**Note only** — company 12–36 months old with filings up to date [draft]; previously late accounts now filed;
mainstream lender charges; director with other active, solvent appointments; UK corporate PSC with its own visible PSC.

**Refer** — company under 12 months old [draft]; accounts or confirmation statement overdue; director linked to
1 insolvent company in the last 5 years [draft]; possible (unresolved) sanctions match — hold cover until cleared;
no PSC and no statement, or overseas corporate PSC [draft]; SIC materially different from declared trade;
regulated trade but FCA authorisation can't be confirmed; recent director/PSC change at renewal;
any check that **couldn't be completed** on identity, status, sanctions or directors.

**Outside appetite (decline unless a senior underwriter overrides with reasons)** — exact sanctions match;
in liquidation, administration or receivership, or winding-up petition; active proposal to strike off [draft];
dissolved, or number doesn't match the name; director currently disqualified; director linked to 2+ insolvent
companies in the last 5 years [draft]; regulated trade with no FCA authorisation (for PI/D&O/FI).

## What it must not do
- Make or communicate the decision.
- Treat "no data" as "clear", or fold a possible sanctions match into either clear or match.
- Infer anything about people beyond what sanctions screening needs.
- Hide where data came from, or when it was fetched.

## Why it matters (regulatory context, briefly)
- **Insurance Act 2015, fair presentation (s.3–5):** the insured needn't disclose what the insurer ought to know
  in the ordinary course — arguably including public registers. If the tool surfaced something and it was
  ignored, that cuts against the insurer later. So the record must show what was surfaced and what was decided.
- **Sanctions:** civil penalties are strict liability since 2022; ownership and control matter, not just the named
  entity; the list and date screened against should be logged. Since 28 Jan 2026 the UK Sanctions List is the single UK list.
- **Consumer Duty:** after FCA PS25/21 it still applies to smaller commercial customers, so declines must be explainable.

## How results are shown
One route at the top (Note / Refer / Outside appetite), then one line per check:
**Clear · Flag · Couldn't check (why) · Not applicable**, each with the source link and retrieval time.
"Couldn't check" is never green. The underwriter records accept / refer / decline with a reason.

## Personas
1. **Priya — SME property, ~40 quotes a day.** "Northern Bake Ltd", 7 months old; the director's previous
   company at the same postcode went into CVL 9 months ago. Age alone = refer; with the director history = outside appetite.
2. **Mark — mid-market renewal.** 15-year-old engineering firm; two directors resigned, PSC now a BVI holding company,
   new debenture, possible sanctions name match on the ultimate owner. Refer; hold until cleared; ask the broker.
3. **Aisha — PI/D&O.** Mortgage broker; no FCA match on the registered name (trading as something else).
   Refer — couldn't check, not clear. She asks for the FRN and finds an appointed representative.

Sources: CH API docs; The Gazette; Insolvency Service; OFSI general guidance; legislation.gov.uk (Insurance Act 2015 Part 2);
FCA PS25/21 (CMS summary); HSE convictions register; Registry Trust (TrustOnline).
