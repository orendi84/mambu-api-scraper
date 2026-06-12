import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    DEFAULT_TENANT,
    MAX_FAILURE_RATIO,
    build_session,
    parse_auth,
    setup_logging,
)
from .fetch import (
    count_endpoints,
    fetch_all_specs,
    fetch_resource_index,
    fetch_streaming_api,
    filter_resources,
    load_saved_output,
)
from .render import build_markdown, write_split_markdown

log = logging.getLogger(__name__)


def build_fetch_parser(subparsers=None):
    """Build and return the argument parser for the fetch subcommand.

    If subparsers is None, builds a standalone ArgumentParser.
    """
    if subparsers is not None:
        parser = subparsers.add_parser(
            "fetch",
            help="Fetch Mambu API documentation via OpenAPI specs",
            description="Fetch Mambu API documentation via OpenAPI specs",
        )
    else:
        parser = argparse.ArgumentParser(description="Fetch Mambu API documentation via OpenAPI specs")

    parser.add_argument("--tenant", default=DEFAULT_TENANT, help=f"Mambu tenant hostname (default: {DEFAULT_TENANT})")
    parser.add_argument(
        "--auth",
        default=None,
        help="Basic auth as user:password (optional). Prefer MAMBU_AUTH env var to avoid shell history leakage.",
    )
    parser.add_argument("--output-dir", default=".", help="Output directory (default: current)")
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="Exclude Streaming API (included by default)",
    )
    # Deprecated no-op kept so existing callers that passed it don't break;
    # streaming was always on by default.
    parser.add_argument("--include-streaming", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers for fetching specs (default: 4, min: 1)",
    )
    parser.add_argument(
        "--pretty-json",
        action="store_true",
        help="Write JSON output with indent=2 (default: compact)",
    )
    parser.add_argument(
        "--resources",
        default=None,
        help="Comma-separated case-insensitive substring filter against resource labels (e.g. 'clients,loans')",
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="Fetch the resource index, print each label and jsonPath, then exit",
    )
    parser.add_argument(
        "--split-md",
        action="store_true",
        help="Also write a split directory with one .md per resource plus an index.md",
    )
    parser.add_argument(
        "--from-json",
        default=None,
        metavar="FILE",
        help="Skip network fetching; load a previously saved output JSON and regenerate markdown",
    )
    return parser


def run_fetch(args):
    """Execute the fetch subcommand with parsed args."""
    # Mutual exclusion: --from-json and --list-resources
    if args.from_json and args.list_resources:
        log.error("--from-json and --list-resources are mutually exclusive")
        sys.exit(1)

    # Clamp workers to minimum 1
    workers = max(1, args.workers)

    include_streaming = not args.no_streaming
    json_indent = 2 if args.pretty_json else None

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(output_dir)

    # --from-json mode: skip all network fetching
    if args.from_json:
        if args.tenant != DEFAULT_TENANT:
            log.info("--tenant is ignored in --from-json mode")
        if args.resources:
            log.info("--resources is ignored in --from-json mode")

        log.info(f"Loading saved output from {args.from_json}")
        try:
            saved = load_saved_output(args.from_json)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            log.error(f"Failed to load {args.from_json}: {e}")
            sys.exit(1)

        tenant = saved.get("tenant", "(from-json)")
        raw_specs = saved["resources"]

        # Recompute totals from loaded specs
        specs_with_labels = []
        total_endpoints = 0
        for entry in raw_specs:
            label = entry.get("label", "Unknown")
            spec = entry.get("spec", {})
            ep_count = count_endpoints(spec)
            total_endpoints += ep_count
            specs_with_labels.append((label, spec))

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant": tenant,
            "resources_total": len(specs_with_labels),
            "resources_failed": saved.get("resources_failed", 0),
            "endpoints_total": total_endpoints,
            "resources": raw_specs,
        }

        # Markdown only: re-writing the JSON we just loaded would duplicate it
        # Write combined markdown
        md_content = build_markdown(output, specs_with_labels)
        md_file = output_dir / f"mambu_api_docs_{timestamp}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)
        log.info(f"Markdown saved: {md_file} ({md_file.stat().st_size / 1024:.0f} KB)")

        # Write split markdown if requested
        if args.split_md:
            split_dir = write_split_markdown(output, specs_with_labels, output_dir, timestamp)
            split_count = len(list(split_dir.glob("*.md")))
            log.info(f"Split markdown written: {split_dir} ({split_count} files)")

        log.info("=" * 60)
        log.info(f"Done (from-json). {len(specs_with_labels)} resources, {total_endpoints} endpoints")
        log.info(f"Markdown: {md_file}")
        log.info("=" * 60)
        return

    log.info(f"Fetching Mambu API docs from {args.tenant}")

    # Auth: CLI flag takes priority; fall back to env var
    raw_auth = args.auth or os.environ.get("MAMBU_AUTH")
    auth_tuple = None
    if raw_auth:
        try:
            auth_tuple = parse_auth(raw_auth)
        except ValueError as e:
            log.error(str(e))
            sys.exit(1)

    session = build_session(auth_tuple)
    base_url = f"https://{args.tenant}"

    # Step 1: Get resource index
    try:
        resources = fetch_resource_index(session, base_url)
    except Exception as e:
        log.error(f"Failed to fetch resource index: {e}")
        sys.exit(1)

    if not resources:
        log.error("Resource index is empty")
        sys.exit(1)

    # --list-resources mode: print and exit
    if args.list_resources:
        for r in resources:
            label = r.get("label", "")
            json_path = r.get("jsonPath", "")
            print(f"{label}\t{json_path}")
        sys.exit(0)

    # --resources filter (applied after --list-resources check)
    if args.resources:
        resources, available_labels = filter_resources(resources, args.resources)
        if not resources:
            log.error(
                f"--resources filter '{args.resources}' matched no resources. "
                f"Available labels: {', '.join(available_labels)}"
            )
            sys.exit(1)
        log.info(f"Filtered to {len(resources)} resources matching '{args.resources}'")

    # Step 2: Fetch specs concurrently
    specs, failures, total_endpoints = fetch_all_specs(
        resources, base_url, auth_tuple, workers
    )

    resources_attempted = len(resources)

    # Quality gate: denominator = resources actually attempted
    failure_ratio = failures / resources_attempted if resources_attempted else 1
    if failure_ratio > MAX_FAILURE_RATIO:
        log.error(
            f"Too many failures: {failures}/{resources_attempted} ({failure_ratio:.0%}) "
            f"exceeds {MAX_FAILURE_RATIO:.0%} threshold"
        )
        sys.exit(1)

    # Step 3: Streaming API (outside the failure gate, not subject to --resources filter)
    streaming_spec = None
    if include_streaming:
        streaming_spec = fetch_streaming_api(session)
        if streaming_spec:
            ep_count = count_endpoints(streaming_spec)
            total_endpoints += ep_count
            specs.append({
                "label": "Streaming API",
                "version": "v1",
                "endpoints": ep_count,
                "spec": streaming_spec,
            })

    # Step 4: Build output
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant": args.tenant,
        "resources_total": len(specs),
        "resources_failed": failures,
        "endpoints_total": total_endpoints,
        "resources": specs,
    }

    # Save JSON (compact by default, --pretty-json restores indent=2)
    json_file = output_dir / f"mambu_api_docs_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=json_indent, ensure_ascii=False)
    json_size = json_file.stat().st_size
    log.info(f"JSON saved: {json_file} ({json_size / 1024 / 1024:.1f} MB)")

    # Sanity check on file size
    if json_size > 100 * 1024 * 1024:  # 100 MB
        log.warning(f"Output file is suspiciously large ({json_size / 1024 / 1024:.0f} MB)")

    # Save combined Markdown
    specs_with_labels = [(s["label"], s["spec"]) for s in specs]
    md_content = build_markdown(output, specs_with_labels)
    md_file = output_dir / f"mambu_api_docs_{timestamp}.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    log.info(f"Markdown saved: {md_file} ({md_file.stat().st_size / 1024:.0f} KB)")

    # Save split markdown if requested
    if args.split_md:
        split_dir = write_split_markdown(output, specs_with_labels, output_dir, timestamp)
        split_count = len(list(split_dir.glob("*.md")))
        log.info(f"Split markdown written: {split_dir} ({split_count} files)")

    # Summary
    log.info("=" * 60)
    log.info(f"Done. {len(specs)} resources, {total_endpoints} endpoints, {failures} failures")
    log.info(f"JSON: {json_file}")
    log.info(f"Markdown: {md_file}")
    log.info("=" * 60)


def _build_diff_parser(subparsers):
    """diff subcommand: structural spec diff between two saved envelopes."""
    parser = subparsers.add_parser(
        "diff",
        help="Compare two saved output JSON files and emit a structural changelog",
        description="Compare two saved output JSON files and emit a structural changelog",
    )
    parser.add_argument("old", help="Path to older JSON output")
    parser.add_argument("new", help="Path to newer JSON output")
    parser.add_argument("--output", default=None, metavar="FILE", help="Write markdown to FILE instead of stdout")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit 1 when any structural change is present, 0 when none (for cron automation)",
    )
    return parser


def run_diff(args):
    """Execute the diff subcommand with parsed args."""
    from .diff import compute_diff, has_changes, render_diff

    try:
        old_env = load_saved_output(args.old)
        new_env = load_saved_output(args.new)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        log.error(f"Failed to load diff inputs: {e}")
        sys.exit(2)

    model = compute_diff(old_env, new_env)
    markdown = render_diff(model)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(markdown)
            f.write("\n")
        log.info(f"Diff written: {args.output}")
    else:
        print(markdown)

    if args.exit_code and has_changes(model):
        sys.exit(1)
    sys.exit(0)


def _build_scorecard_parser(subparsers):
    """Placeholder scorecard subcommand."""
    parser = subparsers.add_parser(
        "scorecard",
        help="[Coming soon] Generate API coverage scorecard",
    )
    parser.add_argument("json_file", help="Path to saved JSON output")
    return parser


def main():
    """Entry point for the mambu-api-tools CLI."""
    parser = argparse.ArgumentParser(
        prog="mambu-api-tools",
        description="Mambu API tools",
    )
    subparsers = parser.add_subparsers(dest="command")

    build_fetch_parser(subparsers)
    _build_diff_parser(subparsers)
    _build_scorecard_parser(subparsers)

    args = parser.parse_args()

    if args.command == "fetch":
        run_fetch(args)
    elif args.command == "diff":
        run_diff(args)
    elif args.command == "scorecard":
        log.error("scorecard subcommand is not yet implemented")
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
