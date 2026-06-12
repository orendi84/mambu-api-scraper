import json
import logging

from .common import github_anchor, md_cell, resolve_ref, slugify_filename

log = logging.getLogger(__name__)


def merge_allof(schema, spec, seen):
    """Flatten a schema's allOf composition into (properties, required_set).

    Recurses into sub-schemas that themselves use allOf, so inherited
    properties from nested compositions are not dropped. The schema's own
    sibling properties/required are applied last and win over allOf members.
    seen guards against circular $ref chains.
    """
    properties = {}
    required = set()
    for sub in schema.get("allOf", []):
        if not isinstance(sub, dict):
            continue
        if "$ref" in sub:
            ref_name = sub["$ref"].split("/")[-1]
            if ref_name in seen:
                continue
            seen = seen | {ref_name}
            resolved = resolve_ref(spec, sub["$ref"])
            if not isinstance(resolved, dict):
                continue
            sub = resolved
        if "allOf" in sub:
            sub_props, sub_req = merge_allof(sub, spec, seen)
            properties.update(sub_props)
            required.update(sub_req)
        properties.update(sub.get("properties", {}))
        required.update(sub.get("required", []))
    properties.update(schema.get("properties", {}))
    required.update(schema.get("required", []))
    return properties, required


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

    # Handle allOf: flatten the composition, then render as a plain schema
    if "allOf" in schema:
        merged_properties, merged_required = merge_allof(schema, spec, seen)
        merged_schema = {
            "properties": merged_properties,
            "required": list(merged_required),
        }
        return schema_to_markdown(merged_schema, spec, indent, seen)

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
            desc = prop_schema.get("description", "")
            if prop_schema.get("enum"):
                desc += f" Enum: {', '.join(md_cell(v) for v in prop_schema['enum'])}"
            if prop_schema.get("example") is not None:
                desc += f" Example: `{prop_schema['example']}`"
            lines.append(
                f"{prefix}| {md_cell(prop_name)} | {md_cell(ptype)} | {md_cell(req)} | {md_cell(desc)} |"
            )
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
                        lines.append(f"| {md_cell(ref_name)} | - | - | - | (unresolvable ref) |")
                        continue
                    name = p.get("name", "")
                    location = p.get("in", "")
                    pschema = p.get("schema", {})
                    ptype = pschema.get("type", p.get("type", ""))
                    required = "Yes" if p.get("required") else "No"
                    desc = p.get("description", "")
                    if pschema.get("enum"):
                        desc += f" Enum: {', '.join(md_cell(v) for v in pschema['enum'])}"
                    if pschema.get("example") is not None:
                        desc += f" Example: `{pschema['example']}`"
                    lines.append(
                        f"| {md_cell(name)} | {md_cell(location)} | {md_cell(ptype)} | {md_cell(required)} | {md_cell(desc)} |"
                    )
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
                for code, resp_info in sorted(responses.items(), key=lambda kv: str(kv[0])):
                    code_str = str(code)
                    # resp_info itself may be a $ref
                    if isinstance(resp_info, dict) and "$ref" in resp_info:
                        resolved_ri = resolve_ref(spec, resp_info["$ref"])
                        resp_info = resolved_ri if isinstance(resolved_ri, dict) else {}
                    desc = resp_info.get("description", "").replace("\n", " ")
                    lines.append(f"**{code_str}** - {desc}")
                    lines.append("")
                    content = resp_info.get("content", {})
                    for ct, ct_info in content.items():
                        resp_schema = ct_info.get("schema", {})
                        if resp_schema:
                            # Show schema for success responses
                            if code_str.startswith("2") or code_str == "102":
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
        lines.append("### Schemas")
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


def write_split_markdown(output, specs_with_labels, output_dir, timestamp):
    """Write per-resource .md files plus an index.md into a split directory.

    Returns the split directory Path.
    """
    split_dir = output_dir / f"mambu_api_docs_{timestamp}_split"
    split_dir.mkdir(parents=True, exist_ok=True)

    seen_slug_counts = {}
    slug_map = []  # list of (label, slug) in order

    for label, _ in specs_with_labels:
        slug = slugify_filename(label, seen_slug_counts)
        slug_map.append((label, slug))

    # Write per-resource files
    for (label, slug), (_, spec) in zip(slug_map, specs_with_labels):
        content = spec_to_markdown(label, spec)
        file_path = split_dir / f"{slug}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.write("\n")

    # Write index.md
    index_lines = []
    index_lines.append("# Mambu API Documentation - Index")
    index_lines.append("")
    index_lines.append(f"*Generated: {output['timestamp']}*")
    index_lines.append(f"*Tenant: {output['tenant']}*")
    index_lines.append(f"*Resources: {output['resources_total']} | Endpoints: {output['endpoints_total']}*")
    index_lines.append("")
    index_lines.append("## Resources")
    index_lines.append("")
    for label, slug in slug_map:
        index_lines.append(f"- [{label}]({slug}.md)")
    index_lines.append("")

    index_path = split_dir / "index.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    return split_dir
