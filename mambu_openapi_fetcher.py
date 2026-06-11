#!/usr/bin/env python3
"""
Mambu API Documentation Fetcher

Fetches structured OpenAPI specs directly from Mambu's discovery API
instead of scraping rendered HTML. Produces a merged JSON file and
formatted Markdown documentation.

Usage:
    python mambu_openapi_fetcher.py
    python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com
    python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com --auth user:pass
    python mambu_openapi_fetcher.py --no-streaming

Authentication via environment variable (preferred - avoids shell history leakage):
    export MAMBU_AUTH=user:password
    python mambu_openapi_fetcher.py --tenant your-tenant.mambu.com
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Module-level: stream-only logger so functions work without setup_logging().
# setup_logging() in main() adds the file handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DEFAULT_TENANT = "demotenant.dev.mambucloud.com"
STREAMING_API_URL = "https://api.mambu.com/streaming-api/mambu-streaming-api-spec-oas3.json"
MAX_FAILURE_RATIO = 0.10

_LOG_FILENAME = "mambu_openapi_fetcher.log"


def setup_logging(output_dir):
    """Add a file handler that writes into output_dir. Called from main()."""
    log_path = Path(output_dir) / _LOG_FILENAME
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(file_handler)
    log.info(f"Log file: {log_path}")


def parse_auth(auth_str):
    """Parse 'user:password' into (user, password). Raises ValueError if no colon."""
    if ":" not in auth_str:
        raise ValueError(
            "--auth / MAMBU_AUTH must be in user:password format; got no colon in provided value"
        )
    user, password = auth_str.split(":", 1)
    return (user, password)


def build_session(auth_tuple=None):
    """Create a requests session with retry and optional basic auth.

    auth_tuple: (user, password) or None.
    """
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers["User-Agent"] = "mambu-openapi-fetcher/1.0"
    if auth_tuple:
        session.auth = auth_tuple
    return session


def fetch_resource_index(session, base_url):
    """Fetch the list of all API resources from Mambu's discovery endpoint."""
    url = f"{base_url}/api/swagger/resources"
    log.info(f"Fetching resource index from {url}")
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # Response is {"items": [...]} on some tenants, plain list on others.
    if isinstance(data, dict):
        if "items" not in data:
            raise RuntimeError(
                f"Unexpected resource index shape: dict with no 'items' key. Keys: {list(data.keys())}"
            )
        resources = data["items"]
    else:
        resources = data
    if not isinstance(resources, list):
        raise RuntimeError(
            f"Unexpected resource index shape: expected list, got {type(resources).__name__}"
        )
    log.info(f"Found {len(resources)} API resources")
    return resources


_OPENAPI_PATH_RE = re.compile(r"^json/([^/]+)_v(\d+)_swagger\.json$")


def derive_openapi_path(json_path):
    """Derive the unauthenticated openapi path from the discovery jsonPath.

    Example: json/clients_v2_swagger.json -> openapi/resources/clients/v2

    Returns None if json_path does not match the expected pattern.
    """
    m = _OPENAPI_PATH_RE.match(json_path)
    if not m:
        return None
    name, version = m.group(1), m.group(2)
    return f"openapi/resources/{name}/v{version}"


def fetch_spec(session, base_url, json_path, label):
    """Fetch a single OpenAPI spec via the unauthenticated openapi path.

    Falls back to the authenticated jsonPath if the openapi path returns 404
    or if derive_openapi_path cannot produce a valid path.
    """
    # Try unauthenticated openapi path first
    openapi_path = derive_openapi_path(json_path)
    if openapi_path is None:
        log.debug(f"Cannot derive openapi path for {label} (jsonPath={json_path!r}); skipping openapi attempt")
    else:
        url = f"{base_url}/api/{openapi_path}"
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                spec = resp.json()
                if "openapi" in spec or "swagger" in spec:
                    return spec
        except Exception as e:
            log.debug(f"openapi path attempt failed for {url}: {e}")

    # Fall back to the jsonPath directly (may require auth)
    url = f"{base_url}/api/{json_path}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            spec = resp.json()
            if "openapi" in spec or "swagger" in spec:
                return spec
    except Exception as e:
        log.debug(f"jsonPath fallback failed for {url}: {e}")

    log.warning(f"Failed to fetch spec for {label} from both paths")
    return None


def fetch_streaming_api(session):
    """Fetch the Streaming API spec from the static URL."""
    log.info(f"Fetching Streaming API spec from {STREAMING_API_URL}")
    try:
        resp = session.get(STREAMING_API_URL, timeout=30)
        resp.raise_for_status()
        spec = resp.json()
        log.info(f"Streaming API: {len(spec.get('paths', {}))} endpoints")
        return spec
    except Exception as e:
        log.warning(f"Failed to fetch Streaming API: {e}")
        return None


def count_endpoints(spec):
    """Count total endpoints (path + method combinations) in a spec."""
    count = 0
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if not isinstance(details, dict):
                continue
            count += 1
    return count


def resolve_ref(spec, ref):
    """Resolve a $ref JSON pointer within the spec.

    Handles JSON-pointer escapes: ~1 -> /, ~0 -> ~ (in that order).
    Returns None if the ref is missing, invalid, or the target cannot be reached.
    Never raises.
    """
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    obj = spec
    for part in parts:
        # Unescape JSON Pointer encoding (RFC 6901): ~1 first, then ~0
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(obj, list):
            try:
                idx = int(part)
                obj = obj[idx]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
            if obj is None:
                return None
        else:
            return None
    return obj if obj is not None else None


def github_anchor(label, seen_counts):
    """Produce a GitHub-style heading anchor slug from label.

    Lowercases, strips characters that are not alphanumeric/space/hyphen,
    replaces spaces with hyphens. Deduplicates by appending -1, -2, ...
    on second and subsequent occurrences (GitHub convention).

    seen_counts is a dict mutated in place to track usage counts.
    """
    slug = label.lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"\s+", "-", slug)
    # \w keeps digits and underscores, which GitHub also keeps in anchors
    slug = slug.strip("-")

    base = slug
    count = seen_counts.get(base, 0)
    seen_counts[base] = count + 1
    if count == 0:
        return base
    return f"{base}-{count}"


def schema_to_markdown(schema, spec, indent=0, seen=None):
    """Render a schema as a markdown property table."""
    if seen is None:
        seen = set()

    lines = []
    prefix = "  " * indent

    # Resolve $ref
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        if ref_name in seen:
            return [f"{prefix}*{ref_name} (circular reference)*"]
        seen = seen | {ref_name}
        resolved = resolve_ref(spec, schema["$ref"])
        if resolved:
            schema = resolved
        else:
            return [f"{prefix}*{ref_name}*"]

    # Handle array type
    if schema.get("type") == "array" and "items" in schema:
        lines.append(f"{prefix}*Array of:*")
        lines.extend(schema_to_markdown(schema["items"], spec, indent, seen))
        return lines

    # Handle object with properties
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))
    if properties:
        lines.append(f"{prefix}| Property | Type | Required | Description |")
        lines.append(f"{prefix}|----------|------|----------|-------------|")
        for prop_name, prop_schema in sorted(properties.items()):
            ptype = prop_schema.get("type", "")
            if "$ref" in prop_schema:
                ptype = prop_schema["$ref"].split("/")[-1]
            elif ptype == "array" and "items" in prop_schema:
                items = prop_schema["items"]
                if "$ref" in items:
                    ptype = f"array[{items['$ref'].split('/')[-1]}]"
                else:
                    ptype = f"array[{items.get('type', 'object')}]"
            req = "Yes" if prop_name in required_fields else "No"
            desc = prop_schema.get("description", "").replace("\n", " ").replace("|", "\\|")
            if prop_schema.get("enum"):
                desc += f" Enum: {', '.join(str(v) for v in prop_schema['enum'])}"
            if prop_schema.get("example") is not None:
                desc += f" Example: `{prop_schema['example']}`"
            lines.append(f"{prefix}| {prop_name} | {ptype} | {req} | {desc} |")
        lines.append("")

    return lines


def _resolve_param(spec, p):
    """Resolve a parameter object that may be a $ref."""
    if "$ref" in p:
        resolved = resolve_ref(spec, p["$ref"])
        if resolved and isinstance(resolved, dict):
            return resolved
        # Unresolvable - return a minimal placeholder so callers can skip gracefully
        return None
    return p


def spec_to_markdown(label, spec):
    """Convert a single OpenAPI spec to markdown."""
    lines = []
    lines.append(f"## {label}")
    lines.append("")

    info = spec.get("info", {})
    if info.get("description"):
        lines.append(info["description"].strip())
        lines.append("")

    paths = spec.get("paths", {})
    if not paths:
        lines.append("*No endpoints defined.*")
        lines.append("")
        return "\n".join(lines)

    for path, methods in sorted(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method, details in sorted(methods.items()):
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if not isinstance(details, dict):
                continue

            summary = details.get("summary", "")
            lines.append(f"### `{method.upper()}` {path}")
            lines.append("")
            if summary:
                lines.append(summary)
                lines.append("")
            if details.get("description") and details["description"] != summary:
                lines.append(details["description"].strip())
                lines.append("")

            # Parameters
            params = details.get("parameters", [])
            if params:
                lines.append("**Parameters:**")
                lines.append("")
                lines.append("| Name | In | Type | Required | Description |")
                lines.append("|------|-----|------|----------|-------------|")
                for raw_p in params:
                    p = _resolve_param(spec, raw_p)
                    if p is None:
                        ref_name = raw_p.get("$ref", "?").split("/")[-1]
                        lines.append(f"| {ref_name} | - | - | - | (unresolvable ref) |")
                        continue
                    name = p.get("name", "")
                    location = p.get("in", "")
                    pschema = p.get("schema", {})
                    ptype = pschema.get("type", p.get("type", ""))
                    required = "Yes" if p.get("required") else "No"
                    desc = p.get("description", "").replace("\n", " ").replace("|", "\\|")
                    if pschema.get("enum"):
                        desc += f" Enum: {', '.join(str(v) for v in pschema['enum'])}"
                    if pschema.get("example") is not None:
                        desc += f" Example: `{pschema['example']}`"
                    lines.append(f"| {name} | {location} | {ptype} | {required} | {desc} |")
                lines.append("")

            # Request body with schema details
            request_body = details.get("requestBody", {})
            if request_body:
                # requestBody itself may be a $ref
                if "$ref" in request_body:
                    resolved_rb = resolve_ref(spec, request_body["$ref"])
                    request_body = resolved_rb if isinstance(resolved_rb, dict) else {}
                content = request_body.get("content", {})
                for content_type, schema_info in content.items():
                    req_schema = schema_info.get("schema", {})
                    ref = req_schema.get("$ref", "")
                    if ref:
                        schema_name = ref.split("/")[-1]
                        lines.append(f"**Request Body:** `{content_type}` - {schema_name}")
                        lines.append("")
                        resolved = resolve_ref(spec, ref)
                        if resolved:
                            lines.extend(schema_to_markdown(resolved, spec))
                    elif req_schema:
                        lines.append(f"**Request Body:** `{content_type}`")
                        lines.append("")
                        lines.extend(schema_to_markdown(req_schema, spec))
                    # Render example if present
                    example = schema_info.get("example") or req_schema.get("example")
                    if example:
                        lines.append("**Request Example:**")
                        lines.append("")
                        lines.append("```json")
                        lines.append(json.dumps(example, indent=2))
                        lines.append("```")
                        lines.append("")

            # Responses with schema details
            responses = details.get("responses", {})
            if responses:
                lines.append("**Responses:**")
                lines.append("")
                for code, resp_info in sorted(responses.items()):
                    # resp_info itself may be a $ref
                    if isinstance(resp_info, dict) and "$ref" in resp_info:
                        resolved_ri = resolve_ref(spec, resp_info["$ref"])
                        resp_info = resolved_ri if isinstance(resolved_ri, dict) else {}
                    desc = resp_info.get("description", "").replace("\n", " ")
                    lines.append(f"**{code}** - {desc}")
                    lines.append("")
                    content = resp_info.get("content", {})
                    for ct, ct_info in content.items():
                        resp_schema = ct_info.get("schema", {})
                        if resp_schema:
                            # Show schema for success responses
                            if code.startswith("2") or code == "102":
                                if "$ref" in resp_schema:
                                    resolved = resolve_ref(spec, resp_schema["$ref"])
                                    if resolved:
                                        lines.extend(schema_to_markdown(resolved, spec))
                                elif resp_schema.get("type") == "array" and "items" in resp_schema:
                                    items = resp_schema["items"]
                                    if "$ref" in items:
                                        ref_name = items["$ref"].split("/")[-1]
                                        lines.append(f"*Array of {ref_name}:*")
                                        lines.append("")
                                        resolved = resolve_ref(spec, items["$ref"])
                                        if resolved:
                                            lines.extend(schema_to_markdown(resolved, spec))
                                    else:
                                        lines.extend(schema_to_markdown(resp_schema, spec))
                        # Render example if present
                        example = ct_info.get("example") or resp_schema.get("example")
                        if example:
                            lines.append("**Response Example:**")
                            lines.append("")
                            lines.append("```json")
                            lines.append(json.dumps(example, indent=2))
                            lines.append("```")
                            lines.append("")

    # Component schemas section
    schemas = spec.get("components", {}).get("schemas", {})
    if schemas:
        lines.append("---")
        lines.append("")
        lines.append(f"### Schemas")
        lines.append("")
        for schema_name, schema_def in sorted(schemas.items()):
            lines.append(f"#### {schema_name}")
            lines.append("")
            if schema_def.get("description"):
                lines.append(schema_def["description"].strip())
                lines.append("")
            lines.extend(schema_to_markdown(schema_def, spec))

    return "\n".join(lines)


def build_markdown(output, specs_with_labels):
    """Build the full markdown documentation."""
    lines = []
    lines.append("# Mambu API Documentation")
    lines.append("")
    lines.append(f"*Generated: {output['timestamp']}*")
    lines.append(f"*Tenant: {output['tenant']}*")
    lines.append(f"*Resources: {output['resources_total']} | Endpoints: {output['endpoints_total']}*")
    lines.append("")

    # Table of contents - GitHub-style anchors with deduplication
    lines.append("## Table of Contents")
    lines.append("")
    seen_counts = {}
    for label, _ in specs_with_labels:
        anchor = github_anchor(label, seen_counts)
        lines.append(f"- [{label}](#{anchor})")
    lines.append("")

    # Each resource
    for label, spec in specs_with_labels:
        lines.append(spec_to_markdown(label, spec))
        lines.append("")

    return "\n".join(lines)


def main():
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
    args = parser.parse_args()

    include_streaming = not args.no_streaming

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(output_dir)

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

    # Step 2: Fetch each spec
    specs = []
    failures = 0
    total_endpoints = 0

    for i, resource in enumerate(resources, 1):
        label = resource.get("label", f"Resource {i}")
        json_path = resource.get("jsonPath", "")

        if not json_path:
            log.warning(f"[{i}/{len(resources)}] Skipping {label}: no jsonPath found")
            failures += 1
            continue

        log.info(f"[{i}/{len(resources)}] Fetching {label}...")
        spec = fetch_spec(session, base_url, json_path, label)

        if spec:
            ep_count = count_endpoints(spec)
            total_endpoints += ep_count
            specs.append({
                "label": label,
                "json_path": json_path,
                "endpoints": ep_count,
                "spec": spec,
            })
            log.info(f"  -> {ep_count} endpoints")
        else:
            failures += 1
            log.warning(f"  -> FAILED")

        # Be polite
        time.sleep(0.2)

    # Quality gate
    failure_ratio = failures / len(resources) if resources else 1
    if failure_ratio > MAX_FAILURE_RATIO:
        log.error(f"Too many failures: {failures}/{len(resources)} ({failure_ratio:.0%}) exceeds {MAX_FAILURE_RATIO:.0%} threshold")
        sys.exit(1)

    # Step 3: Streaming API
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

    # Save JSON
    json_file = output_dir / f"mambu_api_docs_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    json_size = json_file.stat().st_size
    log.info(f"JSON saved: {json_file} ({json_size / 1024 / 1024:.1f} MB)")

    # Sanity check on file size
    if json_size > 100 * 1024 * 1024:  # 100 MB
        log.warning(f"Output file is suspiciously large ({json_size / 1024 / 1024:.0f} MB)")

    # Save Markdown
    specs_with_labels = [(s["label"], s["spec"]) for s in specs]
    md_content = build_markdown(output, specs_with_labels)
    md_file = output_dir / f"mambu_api_docs_{timestamp}.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    log.info(f"Markdown saved: {md_file} ({md_file.stat().st_size / 1024:.0f} KB)")

    # Summary
    log.info("=" * 60)
    log.info(f"Done. {len(specs)} resources, {total_endpoints} endpoints, {failures} failures")
    log.info(f"JSON: {json_file}")
    log.info(f"Markdown: {md_file}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
