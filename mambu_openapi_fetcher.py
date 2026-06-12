#!/usr/bin/env python3
"""
Mambu API Documentation Fetcher - compatibility shim.

The implementation has moved to the mambu_api_tools package.
This module re-exports all public symbols so existing callers continue to work.

Deprecated: import directly from mambu_api_tools.{common,fetch,render} instead.

Usage (unchanged):
    python mambu_openapi_fetcher.py
    python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com --auth user:pass
    python mambu_openapi_fetcher.py --from-json output/mambu_api_docs_20260402_155845.json
    python mambu_openapi_fetcher.py --list-resources
    python mambu_openapi_fetcher.py --resources "clients,loans"
"""

# Re-export everything the tests and external callers depend on.
# noqa comments are required because these are intentional re-exports.
from mambu_api_tools.cli import (
    build_fetch_parser,
    run_fetch,  # noqa: F401
)
from mambu_api_tools.common import (
    _LOG_FILENAME,  # noqa: F401
    DEFAULT_TENANT,  # noqa: F401
    MAX_FAILURE_RATIO,  # noqa: F401
    STREAMING_API_URL,  # noqa: F401
    build_session,  # noqa: F401
    github_anchor,  # noqa: F401
    md_cell,  # noqa: F401
    parse_auth,  # noqa: F401
    resolve_ref,  # noqa: F401
    setup_logging,  # noqa: F401
    slugify_filename,  # noqa: F401
)
from mambu_api_tools.fetch import (
    _OPENAPI_PATH_RE,  # noqa: F401
    count_endpoints,  # noqa: F401
    derive_openapi_path,  # noqa: F401
    fetch_all_specs,  # noqa: F401
    fetch_resource_index,  # noqa: F401
    fetch_spec,  # noqa: F401
    fetch_streaming_api,  # noqa: F401
    filter_resources,  # noqa: F401
    load_saved_output,  # noqa: F401
)
from mambu_api_tools.render import (
    _resolve_param,  # noqa: F401
    build_markdown,  # noqa: F401
    merge_allof,  # noqa: F401
    schema_to_markdown,  # noqa: F401
    spec_to_markdown,  # noqa: F401
    write_split_markdown,  # noqa: F401
)


def main():
    """Delegate to the package CLI fetch subcommand."""
    parser = build_fetch_parser()
    args = parser.parse_args()
    run_fetch(args)


if __name__ == "__main__":
    main()
