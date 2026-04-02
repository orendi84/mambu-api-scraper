# Mambu API Documentation Fetcher

Fetches structured OpenAPI specs directly from Mambu's discovery API. Produces a merged JSON file and formatted Markdown documentation.

The old Selenium-based scraper has been replaced. Mambu exposes per-resource OpenAPI 3.x specs through a discovery endpoint, so there's no need to scrape rendered HTML.

## How it works

1. Queries `GET /api/swagger/resources` on a Mambu tenant to discover all 87+ API resources
2. Fetches each resource's OpenAPI 3.x spec via `GET /api/openapi/resources/{name}/v2`
3. Optionally fetches the Streaming API spec (static URL)
4. Merges everything into a single JSON file and a formatted Markdown file

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Default (uses Mambu's public demo tenant, no auth needed):

```bash
python mambu_openapi_fetcher.py
```

With a specific tenant:

```bash
python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com
```

With authentication (for enriched specs with custom fields):

```bash
python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com --auth user:password
```

Options:

```
--tenant         Mambu tenant hostname (default: demotenant.dev.mambucloud.com)
--auth           Basic auth as user:password (optional)
--output-dir     Output directory (default: current)
--no-streaming   Exclude Streaming API
```

## Output

- `mambu_api_docs_{timestamp}.json` - All OpenAPI specs merged into one JSON file (~5-10 MB)
- `mambu_api_docs_{timestamp}.md` - Formatted Markdown documentation
- `mambu_openapi_fetcher.log` - Execution log

Runtime: under 2 minutes.

## Quality gates

- Fails if more than 10% of resources can't be fetched
- Validates each spec has an `openapi` or `swagger` field
- Warns if output exceeds 100 MB (likely a bug)
- Logs endpoint count per resource for verification

## License

MIT
