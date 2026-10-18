# Data sources: what to use and why (checked 18 Oct 2026)

[C] = confirmed from the source's own page this session; [U] = unconfirmed, test before relying on it.

## Companies House (keep — the backbone)
- 600 requests per 5 minutes per key; 429 above that [C]. One pre-screen uses roughly 6 + 1 per director.
- Look the company up **by number** (`GET /company/{number}`). Name search is relevance-ranked, there is no exact-match
  endpoint, so the app shows candidates and the user picks one.
- Use `accounts.next_accounts.overdue` and `confirmation_statement.overdue`. `accounts.overdue`, `has_insolvency_history`,
  `has_charges` and `has_been_liquidated` are deprecated [C]; use `links.insolvency` / `links.charges` and the sub-resources.
- `company_status_detail = active-proposal-to-strike-off` is the strike-off signal.
- `previous_company_names[].ceased_on` gives recent renames — this is what explains the Wilko mix-up.
- **Bug in the old code:** `/disqualified-officers/natural/{id}` uses a *different* officer ID from the appointments
  ID [C, CH forum]. The old app passed the appointments ID, so every director came back "not disqualified" (404).
  The correct route is `/search/disqualified-officers?q=name`, then match on name **and** date of birth.

## Sanctions — switch to the UK Sanctions List
- The OFSI Consolidated List closed on 28 Jan 2026; the **UK Sanctions List (FCDO)** is now the only UK list [C].
  It's a free CSV/XML download, updated on every designation. That's the list an auditor will ask about.
- OpenSanctions is excellent, but its free terms are non-commercial only, and insurer screening is commercial [C].
  Keep it as an optional provider; default to the UK list matched locally (company, directors, PSCs).
- `gb_hmt_sanctions` on OpenSanctions is deprecated in favour of `gb_fcdo_sanctions` [C].

## FCA Register (keep, but only when it matters)
- `X-AUTH-EMAIL` / `X-AUTH-KEY`; 50 requests per 10 s [C]. Search is substring, and the register isn't keyed on the
  company number, so a name match is "possible" until the FRN is confirmed.
- Run it only for regulated trades (SIC 64–66) or when the user says the firm should be authorised. Otherwise "not applicable".

## News and web (Brave vs alternatives)
| Provider | Cost | Free | Fit |
|---|---|---|---|
| Brave | $5 / 1k | $5 monthly credit since Feb 2026 | Independent index, news endpoint, `country=GB`, date bounds. Results can't be stored without a storage-rights plan. |
| Tavily | ~$8 / 1k PAYG | 1,000 credits/month, no card | LLM-ready content, commercial agent use allowed. |
| GDELT DOC 2.0 | free, no key | — | ~3 months of history, noisy matching. Good free fallback for "anything recent?". |
| Bing Search API | retired Aug 2025 | — | Gone. |
| Google Custom Search | closed to new users, ends 1 Jan 2027 | — | Don't build on it. |
| NewsAPI.org dev plan | not allowed in production | — | Not suitable. |

**Decision:** keep Brave (the key is already in the Space) behind a small provider interface, add GDELT as the free
fallback, and make Tavily a drop-in option. Store only the headline, URL, date and our classification — not page text —
which keeps within Brave's storage terms. Adverse media can only ever *refer*, never decline.

## Free additions worth it
- **The Gazette**: winding-up petitions (notice 2450) often appear before Companies House changes status. Linked-data
  feeds exist [C]; the exact JSON search URL is [U] and needs a live test. Built as optional; a failure shows "couldn't check".
- **HSE convictions and notices**: very relevant for EL/PL, but no API [U] — linked for the underwriter, not automated yet.
- **Environment Agency public registers API** (enforcement) and **FSA food hygiene API**: free, trade-specific. Later.
- **CCJs**: paid (TrustOnline / credit reference agency). Always shown as "not checked — paid source".
