# Baseline: the old app on live data (18 Oct 2026, 01:35–01:45 BST)

Before changing anything, I ran the deployed Hugging Face Space (private, `USE_MOCK_DATA=false`,
Groq `openai/gpt-oss-120b` / `gpt-oss-20b`, free tier) on four real company names through
`POST /research/sync`. Wilko ran alone; Monzo, Thames Water and Apex ran at the same time,
which made Groq's 8,000 tokens-per-minute limit bite harder than a single user would see.
That is a fair caveat, but a two-user afternoon would hit the same wall.

| Run | Time | Resolved company number | What the report said | What was actually true |
|---|---|---|---|---|
| Wilko Limited | 16 s | 09563205 (active, 2015) | High risk; refer: "Companies House says active but news says collapsed" | 09563205 is the company The Range bought the Wilko brand into. The collapsed retailer is a different legal entity. The news was about the wrong company. |
| Monzo Bank Limited | 74 s | 09446231 | Medium risk, confidence 0.1, no referral reasons, FCA status "unknown", regulatory risk "low" | Monzo is an FCA-authorised bank, fined £21m by the FCA. The web search found the fine; the regulatory check didn't. News classification failed (malformed tool call) and synthesis hit a rate limit, so "medium" is just the schema default. |
| Thames Water Utilities Limited | 151 s | none | High risk from news; 5 of 8 dimensions missing; ownership "high quality, no adverse PSC flags" | Entity resolution silently failed (no company number), so Companies House calls went to `/company//officers`. The PSC check then ran with an empty number and reported a clean register at "high" quality. |
| Apex Building Services Ltd | 125 s | none | Medium risk, confidence 0.1, no referrals; ownership "high quality, no adverse flags" | Same silent resolution failure. A report with nothing checked still came out "medium" with a clean ownership section. |

## What the runs show, in underwriter terms

1. **Wrong company, confident report.** Nothing stops the run when the company number is missing or
   the news is about a different entity with the same name. An underwriter would be reading another
   company's file.
2. **Couldn't check looks like clear.** Thames Water and Apex both show a "clean" ownership register
   that was never actually looked up. Sanctions say "0 hits" with "entities screened: 0".
3. **Errors become a medium risk.** When synthesis fails, the schema default (`MEDIUM`) is what the
   underwriter sees, with no referral reasons.
4. **The FCA check doesn't find the FCA's own bank.** Monzo comes back "FCA status unknown",
   regulatory risk "low", while the web results carry the FCA's £21m fine.
5. **Phoenix scoring flags big groups.** Wilko's directors (The Range group) score 0.33 because many
   group subsidiaries are dissolved. Dissolving dormant subsidiaries is housekeeping, not failure.
   The rule must count insolvencies (liquidation, administration), not dissolutions.
6. **The free LLM tier is a hard ceiling.** Groq's free tier allows 8,000 tokens a minute for each model.
   The old graph makes 8–11 LLM calls per run. Two people at once break it, and the failure is silent.
7. **The follow-up loop retried the same failures.** Thames Water and Apex ran 2 passes, with the same
   empty company number both times.

## Design consequences
- Identity first, and the run stops (or asks) if the company isn't pinned to one number.
- Every check returns found / clear / couldn't check, and the report counts what couldn't be checked.
- Rules decide the referral route; the model only reads news and web text, and writes a short summary.
- At most 2–3 LLM calls per run, with rate-limit retries, so a free tier is usable.
