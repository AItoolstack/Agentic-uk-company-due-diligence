# Deploy and verify (for Tej)

Nothing here has been deployed. The Space still runs the old app.

## Deploy to the existing Space (keeps its URL and secrets)
1. Merge the PR, or check out `rebuild/underwriter-prescreen`.
2. Push to the Space remote: `git push hf rebuild/underwriter-prescreen:main`
   (GitHub and the Space have different histories; the Space has a few deployment commits GitHub lacks.
   This branch now carries the Space's README front matter, Dockerfile and .dockerignore, so a
   force push is safe: `git push --force hf rebuild/underwriter-prescreen:main`. Your call.)
3. In Space settings, add a secret `PRESCREEN_ACCESS_TOKEN` (any code). Keep the existing secrets.
   `USE_MOCK_DATA=false` and the gpt-oss model variables are already set.

## Then I verify on live data (about 10 minutes, ~10 runs)
| Company | What it proves |
|---|---|
| 09563205 Wilko Limited | Pre-rename news excluded; dormant shown as Info |
| 09446231 Monzo Bank Limited | FCA found (checks the search key fix) and disciplinary history read |
| Thames Water Utilities Ltd (by name) | Identity gate resolves or asks; no more `/company//officers` |
| Apex Building Services (by name) | Ambiguous name → candidates, not a report |
| A company in liquidation (found via the Gazette search) | Status → outside appetite; Gazette notices read |
| A company with a compulsory strike-off proposal | GAZ1 detection |
| An LLP (e.g. a law firm) | Members checked |
| One with a common director name | Disqualification search coverage |

For each: time taken, route, what couldn't be checked and why, and whether an underwriter
would agree. Results go in `docs/rebuild/06-live-verification.md` as before/after evidence.
Fixtures recorded from those runs replace the hand-written stand-ins in `demo_cases.py`.
