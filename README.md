# Mambu API Tools

Fetch, diff, and score Mambu's OpenAPI documentation. Mambu exposes per-resource OpenAPI 3.x specs through a discovery endpoint, so the tool pulls structured specs directly rather than scraping rendered HTML.

The package ships a single CLI, `mambu-api-tools`, with three subcommands:

- `fetch` queries a tenant, fetches every resource spec concurrently, and writes JSON, Markdown, and a merged OpenAPI document.
- `diff` compares two saved snapshots and emits a structural changelog, with an optional non-zero exit code for cron automation.
- `scorecard` grades documentation quality per resource and overall.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `mambu-api-tools` console script.

## fetch

Pull all specs from a tenant (the default tenant is Mambu's public demo, no auth needed):

```bash
mambu-api-tools fetch --tenant your-tenant.mambu.com --output-dir ./output
```

With authentication for enriched specs that include custom fields, prefer the `MAMBU_AUTH` environment variable over `--auth`. CLI arguments appear in shell history and `ps` output; the env var does not:

```bash
export MAMBU_AUTH=user:password
mambu-api-tools fetch --tenant your-tenant.mambu.com
```

### fetch flags

```
--tenant         Mambu tenant hostname (default: demotenant.dev.mambucloud.com)
--auth           Basic auth as user:password (optional; MAMBU_AUTH env var preferred)
--output-dir     Output directory (default: current)
--no-streaming   Exclude the Streaming API (included by default)
--workers N      Number of parallel workers for fetching specs (default: 4, min: 1)
--pretty-json    Write JSON with indent=2 (default: compact, no indentation)
--resources CSV  Comma-separated case-insensitive label substring filter (e.g. 'clients,loans')
--list-resources Print resource labels and jsonPaths, then exit
--split-md       Also write a per-resource split directory with index.md
--from-json FILE Skip network; regenerate docs from a previously saved JSON
```

List available resources without fetching specs (useful for finding labels to pass to `--resources`):

```bash
mambu-api-tools fetch --tenant your-tenant.mambu.com --list-resources
```

Fetch only specific resources (comma-separated, case-insensitive substring match against labels). The Streaming API is not affected by this filter; use `--no-streaming` to exclude it:

```bash
mambu-api-tools fetch --resources "clients,loan accounts"
```

Replay a previously saved JSON without hitting the network. This recomputes totals and regenerates the Markdown and merged OpenAPI document with a fresh timestamp. `--tenant` and `--resources` are ignored in this mode, and the saved envelope JSON is intentionally not rewritten:

```bash
mambu-api-tools fetch --from-json output/mambu_api_docs_20260402_155845.json --output-dir /tmp/replay
```

Write per-resource split files in addition to the combined Markdown:

```bash
mambu-api-tools fetch --split-md
```

## diff

Compare two saved snapshots and print a structural changelog to stdout:

```bash
mambu-api-tools diff old.json new.json
```

The diff is structural only. It reports resources, endpoints, and component schemas added, removed, or changed, including parameter, request body, response code, response schema, and deprecation changes. Description and summary text changes are deliberately ignored. The summary count table at the top of the output is the single source of truth; every count below it derives from the same model.

Write to a file instead of stdout with `--output FILE`.

For cron automation, `--exit-code` makes the command exit 1 when any structural change is present and 0 when none. Without the flag the command always exits 0:

```bash
mambu-api-tools diff yesterday.json today.json --exit-code --output drift.md
```

Malformed inputs fail closed: the command logs an error and exits 2.

## scorecard

Grade documentation quality for a saved snapshot:

```bash
mambu-api-tools scorecard output/mambu_api_docs_20260402_155845.json
```

The output leads with an overall summary table, then a per-resource table sorted worst composite first, then a section defining every metric and its weight. Metrics are deterministic: percent of operations described, percent with a 2xx content schema, percent carrying examples, percent of parameters described, and a deprecation hygiene score. CRUD coverage is reported as flags and is not part of the composite. Write to a file with `--output FILE`. Malformed input logs an error and exits 2.

## Outputs

| Artifact | When | Description |
|----------|------|-------------|
| `mambu_api_docs_{timestamp}.json` | fetch (network mode) | Envelope JSON: all specs plus metadata |
| `mambu_api_docs_{timestamp}.md` | fetch | Combined Markdown documentation |
| `mambu_api_docs_{timestamp}_split/` | fetch with `--split-md` | Per-resource `.md` files plus `index.md` |
| `mambu_openapi_{timestamp}.json` | fetch | Single merged OpenAPI 3.0.3 document |
| `mambu_openapi_fetcher.log` | fetch | Execution log, written into `--output-dir` |

The merged OpenAPI document combines every resource spec into one OpenAPI 3.0.3 file. Component name collisions are shared when definitions are identical and otherwise renamed with a resource-derived prefix, with all `$ref` pointers rewritten accordingly. Path collisions keep the first occurrence and record a warning under `info.x-merge-warnings`.

JSON output is compact by default, which roughly halves the file size. Pass `--pretty-json` to restore `indent=2`.

## Quality gates

- fetch fails if more than 10% of resources cannot be fetched
- Each spec is validated to have an `openapi` or `swagger` field
- fetch warns if the JSON output exceeds 100 MB
- Endpoint count per resource is logged for verification

## Deprecated shim

`mambu_openapi_fetcher.py` remains as a working compatibility shim. It re-exports the package's public symbols and runs the `fetch` flow, so existing callers keep working:

```bash
python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com
```

Prefer the `mambu-api-tools fetch` CLI for new usage.

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

Lint with ruff:

```bash
venv/bin/python -m ruff check .
```

The test suite covers pure functions only, with no network calls.

## License

MIT
