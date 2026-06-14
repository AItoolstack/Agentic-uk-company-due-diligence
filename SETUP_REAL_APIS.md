# Live Source Setup

The application uses local fixtures when `USE_MOCK_DATA=true`. This is the
recommended starting point for development and tests.

Live research requires an LLM key plus the source credentials relevant to the
dimensions you want to run.

## 1. Copy the environment template

Windows:

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

Never commit `.env`.

## 2. Companies House

Companies House supports:

- entity search;
- company profile;
- officers;
- filing history;
- PSC records;
- officer appointment and disqualification research.

Create a key at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/)
and configure:

```ini
COMPANIES_HOUSE_API_KEY=your_key
COMPANIES_HOUSE_BASE_URL=https://api.company-information.service.gov.uk
```

Connector smoke test:

```bash
python -c "from src.connectors.companies_house import CompaniesHouseConnector; c=CompaniesHouseConnector(); c.use_mock=False; print(c.fetch_profile('09446231'))"
```

`09446231` is Monzo Bank Limited.

## 3. Brave Search

Brave Search supports the news and supplementary web-evidence dimensions.

Create a key at
[api.search.brave.com](https://api.search.brave.com/) and configure:

```ini
BRAVE_SEARCH_API_KEY=your_key
```

Connector smoke test:

```bash
python -c "from src.connectors.brave_search import BraveSearchConnector; c=BraveSearchConnector(); c.use_mock=False; print(c.search('Monzo Bank regulatory risk UK', count=3))"
```

The news retriever performs several category-specific searches per company, so
review your Brave plan and request allowance before batch use.

## 4. FCA Register

The FCA Register connector uses the public register endpoints and normally does
not require a key:

```ini
FCA_API_KEY=
FCA_BASE_URL=https://register.fca.org.uk/services/V0.1
```

Connector smoke test:

```bash
python -c "from src.connectors.fca_register import FCARegisterConnector; c=FCARegisterConnector(); c.use_mock=False; print(c.fetch_firm('730427'))"
```

`730427` is the FCA firm reference number used for the Monzo test case.

The public FCA service can change its access controls or response shape. Treat
availability failures as missing regulatory evidence, not as a clean result.

## 5. OpenSanctions

OpenSanctions enriches fraud and beneficial-ownership research.

Configure:

```ini
OPEN_SANCTIONS_API_KEY=your_key
OPEN_SANCTIONS_BASE_URL=https://api.opensanctions.org
```

The connector may work without a key depending on the service tier, but an API
key is recommended for predictable use.

Smoke test:

```bash
python -c "from src.connectors.open_sanctions import OpenSanctionsConnector; c=OpenSanctionsConnector(); c.use_mock=False; print(c.screen_entities('MONZO BANK LIMITED', []))"
```

Potential matches require human identity verification. A name match alone is
not a confirmed sanctions designation.

## 6. Verify configured sources

The bundled connectivity script checks Companies House, the FCA Register, and
Brave Search:

```bash
python scripts/check_apis.py
```

It intentionally performs live requests. Run it only after configuring the
required credentials.

## 7. Enable live mode

After the individual checks succeed:

```ini
USE_MOCK_DATA=false
```

Start the API:

```bash
python api_server.py
```

Or run the CLI:

```bash
python app.py --query "Create a due diligence assessment for Monzo Bank Limited"
```

## 8. Troubleshooting

### Companies House returns 401

- Confirm the API key is present.
- Confirm `.env` is in the project root.
- Confirm the key is used as the Basic Auth username.

### Brave Search returns no live results

- Confirm `BRAVE_SEARCH_API_KEY` is configured.
- Confirm `USE_MOCK_DATA=false`.
- Check plan limits and HTTP errors.

### FCA evidence is missing

- Confirm a firm reference number was resolved.
- Test the firm directly with `fetch_firm`.
- The public FCA endpoint may temporarily block or change automated access.

### OpenSanctions returns an error field

- Confirm the base URL and key.
- Check service limits.
- Treat the research dimension as incomplete until screening succeeds.

### The graph runs but produces weak evidence

- Inspect the returned `errors` list.
- Review `dimensions_missing`.
- Confirm each live connector independently.
- Re-enable mock mode while diagnosing source-specific failures.
