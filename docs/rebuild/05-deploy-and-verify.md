# Deploy and verify (for Tej)

Nothing here has been deployed. The Space still runs the old app.

## Deploy to the existing Space (keeps its URL and secrets)
Use the GitHub Action, not a manual push: Actions → "deploy to Hugging Face Space" → Run workflow
(ref `rebuild/underwriter-prescreen`). It pushes a one-commit snapshot with images in Git LFS,
because Hugging Face rejects plain binary files and this repo's history has three PNGs as plain
objects. The Space's old history is replaced; GitHub's isn't touched.

Needs, once:
1. GitHub repo secret `HF_TOKEN`: a Hugging Face **write** token for the AItoolstack account.
2. Space secrets (Space → Settings → Variables and secrets), keeping the existing ones
   (`COMPANIES_HOUSE_API_KEY`, `GROQ_API_KEY`, FCA, Brave, `USE_MOCK_DATA=false`, model names):
   - `PRESCREEN_ACCESS_TOKEN`: any code; the UI, the API and the MCP endpoint all ask for it.
   - `PRESCREEN_JOB_TOKEN`: any code; for the daily re-check (also add it as a GitHub secret).
   - `SUPABASE_DB_URL`: Supabase → project uk-company-prescreen → Connect → Session pooler string.
     Optional: without it, reports are saved to files and lost when the Space restarts.

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
