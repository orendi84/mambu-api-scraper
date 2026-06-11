# Mambu API Documentation Fetcher

Fetches structured OpenAPI specs directly from Mambu's discovery API. Produces a merged JSON file and formatted Markdown documentation.

The old Selenium-based scraper has been replaced. Mambu exposes per-resource OpenAPI 3.x specs through a discovery endpoint, so there's no need to scrape rendered HTML.

## How it works

1. Queries `GET /api/swagger/resources` on a Mambu tenant to discover all 87+ API resources
2. Fetches each resource's OpenAPI 3.x spec concurrently via `GET /api/openapi/resources/{name}/v2`
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

Prefer the `MAMBU_AUTH` environment variable over `--auth` when running in scripts or CI. CLI
arguments appear in shell history and `ps` output; the env var does not:

```bash
export MAMBU_AUTH=user:password
python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com
```

### List available resources without fetching specs

```bash
python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com --list-resources
```

Prints each resource label and its `jsonPath` to stdout, then exits. Useful for finding the
exact label names to use with `--resources`.

### Fetch only specific resources

```bash
python mambu_openapi_fetcher.py --resources "clients,loan accounts"
```

Comma-separated, case-insensitive substring match against resource labels. Exits with an error
listing all available labels if the filter matches nothing. The Streaming API is not affected by
this filter; use `--no-streaming` to exclude it.

### Replay a previously saved JSON

```bash
python mambu_openapi_fetcher.py --from-json output/mambu_api_docs_20260402_155845.json --output-dir /tmp/replay
```

Skips all network fetching. Loads the saved JSON, recomputes totals, regenerates markdown (and
optionally split files) with a fresh timestamp. Useful for regenerating docs after rendering
fixes without re-hitting the API. `--tenant` and `--resources` are ignored in this mode.

### Write per-resource split files

```bash
python mambu_openapi_fetcher.py --split-md
```

In addition to the combined markdown file (always written), writes a
`mambu_api_docs_{timestamp}_split/` directory containing one `<slug>.md` per resource and an
`index.md` with relative links to all of them.

## Options

```
--tenant         Mambu tenant hostname (default: demotenant.dev.mambucloud.com)
--auth           Basic auth as user:password (optional; MAMBU_AUTH env var preferred)
--output-dir     Output directory (default: current)
--no-streaming   Exclude the Streaming API (included by default)
--workers N      Number of parallel workers for fetching specs (default: 4, min: 1)
--pretty-json    Write JSON with indent=2 (default: compact, no indentation)
--resources CSV  Comma-separated label substring filter (e.g. 'clients,loans')
--list-resources Print resource labels and jsonPaths, then exit
--split-md       Also write a per-resource split directory with index.md
--from-json FILE Skip network; regenerate docs from a previously saved JSON
```

### JSON output format change

The JSON output is now compact by default (no indentation). This roughly halves the file size.
Pass `--pretty-json` to restore the previous `indent=2` behaviour.

## Output

- `mambu_api_docs_{timestamp}.json` - All OpenAPI specs merged into one JSON file
- `mambu_api_docs_{timestamp}.md` - Formatted Markdown documentation (combined)
- `mambu_api_docs_{timestamp}_split/` - Per-resource .md files + index.md (with `--split-md`)
- `mambu_openapi_fetcher.log` - Execution log (written into `--output-dir`)

Runtime: under 30 seconds with default 4 workers.

## Quality gates

- Fails if more than 10% of resources can't be fetched
- Validates each spec has an `openapi` or `swagger` field
- Warns if output exceeds 100 MB (likely a bug)
- Logs endpoint count per resource for verification

## Running tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Or with the repo's virtualenv:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest tests/ -q
```

The test suite covers only pure functions (no network calls).

## License

MIT
