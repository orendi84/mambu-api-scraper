import hashlib
import json
import logging

from .common import resolve_ref

log = logging.getLogger(__name__)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _canon(obj):
    """Canonical JSON string for order-insensitive whole-object equality."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _resolve_param(spec, p):
    """Resolve a top-level parameter $ref. Returns the dict or None."""
    if not isinstance(p, dict):
        return None
    if "$ref" in p:
        resolved = resolve_ref(spec, p["$ref"])
        if isinstance(resolved, dict):
            return resolved
        return None
    return p


def _collect_params(spec, details):
    """Return a dict keyed by (name, in) of resolved parameter objects."""
    out = {}
    for raw in details.get("parameters", []):
        p = _resolve_param(spec, raw)
        if p is None:
            continue
        key = (p.get("name", ""), p.get("in", ""))
        out[key] = p
    return out


def _param_enum(p):
    """Enum set for a parameter (from its schema or inline)."""
    schema = p.get("schema", {})
    enum = schema.get("enum")
    if enum is None:
        enum = p.get("enum")
    if enum is None:
        return None
    return set(_canon(v) for v in enum)


def _param_type(p):
    schema = p.get("schema", {})
    return schema.get("type", p.get("type"))


def _resolve_request_body(spec, details):
    """Resolve a requestBody that may itself be a $ref. Returns dict or {}."""
    rb = details.get("requestBody", {})
    if isinstance(rb, dict) and "$ref" in rb:
        resolved = resolve_ref(spec, rb["$ref"])
        rb = resolved if isinstance(resolved, dict) else {}
    return rb if isinstance(rb, dict) else {}


def _request_body_props(spec, details):
    """One-level-deep property fingerprint of the first requestBody schema.

    Returns a dict {prop_name: type_string} plus a required set, or None if no
    schema is present. Resolves a top-level schema $ref.
    """
    rb = _resolve_request_body(spec, details)
    content = rb.get("content", {})
    for _ct, ct_info in sorted(content.items()):
        schema = ct_info.get("schema", {})
        if not isinstance(schema, dict):
            continue
        if "$ref" in schema:
            resolved = resolve_ref(spec, schema["$ref"])
            schema = resolved if isinstance(resolved, dict) else {}
        props = {}
        for name, pschema in schema.get("properties", {}).items():
            props[name] = _prop_type_string(pschema)
        required = sorted(set(schema.get("required", [])))
        return {"props": props, "required": required}
    return None


def _prop_type_string(pschema):
    """A stable type string for a property schema, one level deep."""
    if not isinstance(pschema, dict):
        return ""
    if "$ref" in pschema:
        return pschema["$ref"].split("/")[-1]
    ptype = pschema.get("type", "")
    if ptype == "array" and "items" in pschema:
        items = pschema["items"]
        if isinstance(items, dict) and "$ref" in items:
            return f"array[{items['$ref'].split('/')[-1]}]"
        if isinstance(items, dict):
            return f"array[{items.get('type', 'object')}]"
    return ptype


def _response_codes(details):
    """Set of response code strings for an operation."""
    return set(str(c) for c in details.get("responses", {}).keys())


def _inline_schema_signature(schema):
    """Short deterministic fingerprint for an inline (non-$ref) schema."""
    canon = json.dumps(schema, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]
    return f"inline:{digest}"


def _2xx_schema_ref(spec, details):
    """Signature of the first 2xx response content schema, or None.

    $ref schemas yield the ref name; inline schemas yield a short content
    fingerprint so structural changes to them still register as changes.
    """
    responses = details.get("responses", {})
    for code in sorted(str(c) for c in responses.keys()):
        if not (code.startswith("2") or code == "102"):
            continue
        resp = responses[code] if code in responses else responses.get(int(code))
        if isinstance(resp, dict) and "$ref" in resp:
            resolved = resolve_ref(spec, resp["$ref"])
            resp = resolved if isinstance(resolved, dict) else {}
        if not isinstance(resp, dict):
            continue
        for _ct, ct_info in sorted(resp.get("content", {}).items()):
            schema = ct_info.get("schema", {})
            if not isinstance(schema, dict) or not schema:
                continue
            if "$ref" in schema:
                return schema["$ref"].split("/")[-1]
            if schema.get("type") == "array":
                items = schema.get("items", {})
                if isinstance(items, dict) and "$ref" in items:
                    return items["$ref"].split("/")[-1]
                if isinstance(items, dict) and items:
                    return f"array[{_inline_schema_signature(items)}]"
            return _inline_schema_signature(schema)
    return None


def _iter_operations(spec):
    """Yield (method_upper, path, details) for each operation in a spec."""
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(details, dict):
                continue
            yield (method.upper(), path, details)


def _schema_props(spec, schema):
    """One-level-deep fingerprint of a component schema definition.

    Returns {"props": {name: type_string}, "required": set, "enum": set|None}.
    """
    if isinstance(schema, dict) and "$ref" in schema:
        resolved = resolve_ref(spec, schema["$ref"])
        schema = resolved if isinstance(resolved, dict) else {}
    if not isinstance(schema, dict):
        schema = {}
    props = {}
    for name, pschema in schema.get("properties", {}).items():
        props[name] = _prop_type_string(pschema)
    required = set(schema.get("required", []))
    enum = schema.get("enum")
    enum_set = set(_canon(v) for v in enum) if enum is not None else None
    return {"props": props, "required": required, "enum": enum_set}


def _diff_endpoint(old_spec, new_spec, old_details, new_details):
    """Compute the per-endpoint change record. Returns a dict or None if unchanged."""
    changes = {}

    # Parameters
    old_params = _collect_params(old_spec, old_details)
    new_params = _collect_params(new_spec, new_details)
    old_pkeys = set(old_params)
    new_pkeys = set(new_params)
    params_added = sorted(new_pkeys - old_pkeys)
    params_removed = sorted(old_pkeys - new_pkeys)
    params_changed = []
    for key in sorted(old_pkeys & new_pkeys):
        op = old_params[key]
        np = new_params[key]
        pc = {}
        ot, nt = _param_type(op), _param_type(np)
        if ot != nt:
            pc["type"] = {"old": ot, "new": nt}
        oreq, nreq = bool(op.get("required", False)), bool(np.get("required", False))
        if oreq != nreq:
            pc["required"] = {"old": oreq, "new": nreq}
        oenum, nenum = _param_enum(op), _param_enum(np)
        if oenum != nenum:
            pc["enum"] = {
                "old": sorted(oenum) if oenum is not None else None,
                "new": sorted(nenum) if nenum is not None else None,
            }
        if pc:
            params_changed.append({"name": key[0], "in": key[1], "changes": pc})
    if params_added or params_removed or params_changed:
        changes["parameters"] = {
            "added": [{"name": k[0], "in": k[1]} for k in params_added],
            "removed": [{"name": k[0], "in": k[1]} for k in params_removed],
            "changed": params_changed,
        }

    # Request body property set (one level deep)
    old_rb = _request_body_props(old_spec, old_details)
    new_rb = _request_body_props(new_spec, new_details)
    if _canon(old_rb) != _canon(new_rb):
        rb_change = {}
        old_props = old_rb["props"] if old_rb else {}
        new_props = new_rb["props"] if new_rb else {}
        added = sorted(set(new_props) - set(old_props))
        removed = sorted(set(old_props) - set(new_props))
        type_changed = []
        for name in sorted(set(old_props) & set(new_props)):
            if old_props[name] != new_props[name]:
                type_changed.append({"name": name, "old": old_props[name], "new": new_props[name]})
        old_req = old_rb["required"] if old_rb else []
        new_req = new_rb["required"] if new_rb else []
        if added:
            rb_change["added"] = added
        if removed:
            rb_change["removed"] = removed
        if type_changed:
            rb_change["type_changed"] = type_changed
        if old_req != new_req:
            rb_change["required"] = {"old": old_req, "new": new_req}
        if rb_change:
            changes["request_body"] = rb_change

    # Response codes
    old_codes = _response_codes(old_details)
    new_codes = _response_codes(new_details)
    codes_added = sorted(new_codes - old_codes)
    codes_removed = sorted(old_codes - new_codes)
    if codes_added or codes_removed:
        changes["responses"] = {"added": codes_added, "removed": codes_removed}

    # 2xx schema ref name
    old_ref = _2xx_schema_ref(old_spec, old_details)
    new_ref = _2xx_schema_ref(new_spec, new_details)
    if old_ref != new_ref:
        changes["response_schema"] = {"old": old_ref, "new": new_ref}

    # Deprecated flag transition
    old_dep = bool(old_details.get("deprecated", False))
    new_dep = bool(new_details.get("deprecated", False))
    if old_dep != new_dep:
        changes["deprecated"] = {"old": old_dep, "new": new_dep}

    return changes or None


def _diff_resource(old_entry, new_entry):
    """Compute endpoint and schema diffs for a common resource. Returns a dict."""
    old_spec = old_entry.get("spec", {})
    new_spec = new_entry.get("spec", {})

    old_ops = {(m, p): d for m, p, d in _iter_operations(old_spec)}
    new_ops = {(m, p): d for m, p, d in _iter_operations(new_spec)}
    old_keys = set(old_ops)
    new_keys = set(new_ops)

    endpoints_added = sorted(new_keys - old_keys)
    endpoints_removed = sorted(old_keys - new_keys)
    endpoints_changed = []
    deprecations_added = 0
    deprecations_removed = 0

    for key in sorted(old_keys & new_keys):
        rec = _diff_endpoint(old_spec, new_spec, old_ops[key], new_ops[key])
        if rec is not None:
            endpoints_changed.append({"method": key[0], "path": key[1], "changes": rec})
            dep = rec.get("deprecated")
            if dep is not None:
                if dep["new"] and not dep["old"]:
                    deprecations_added += 1
                elif dep["old"] and not dep["new"]:
                    deprecations_removed += 1

    # Component schemas
    old_schemas = old_spec.get("components", {}).get("schemas", {})
    new_schemas = new_spec.get("components", {}).get("schemas", {})
    old_snames = set(old_schemas)
    new_snames = set(new_schemas)
    schemas_added = sorted(new_snames - old_snames)
    schemas_removed = sorted(old_snames - new_snames)
    schemas_changed = []
    for name in sorted(old_snames & new_snames):
        of = _schema_props(old_spec, old_schemas[name])
        nf = _schema_props(new_spec, new_schemas[name])
        sc = {}
        added = sorted(set(nf["props"]) - set(of["props"]))
        removed = sorted(set(of["props"]) - set(nf["props"]))
        type_changed = []
        for pn in sorted(set(of["props"]) & set(nf["props"])):
            if of["props"][pn] != nf["props"][pn]:
                type_changed.append({"name": pn, "old": of["props"][pn], "new": nf["props"][pn]})
        if added:
            sc["added"] = added
        if removed:
            sc["removed"] = removed
        if type_changed:
            sc["type_changed"] = type_changed
        if of["required"] != nf["required"]:
            sc["required"] = {"old": sorted(of["required"]), "new": sorted(nf["required"])}
        if of["enum"] != nf["enum"]:
            sc["enum"] = {
                "old": sorted(of["enum"]) if of["enum"] is not None else None,
                "new": sorted(nf["enum"]) if nf["enum"] is not None else None,
            }
        if sc:
            schemas_changed.append({"name": name, "changes": sc})

    return {
        "endpoints": {
            "added": [{"method": k[0], "path": k[1]} for k in endpoints_added],
            "removed": [{"method": k[0], "path": k[1]} for k in endpoints_removed],
            "changed": endpoints_changed,
        },
        "schemas": {
            "added": schemas_added,
            "removed": schemas_removed,
            "changed": schemas_changed,
        },
        "deprecations_added": deprecations_added,
        "deprecations_removed": deprecations_removed,
    }


def _resource_has_changes(rdiff):
    eps = rdiff["endpoints"]
    sch = rdiff["schemas"]
    return bool(
        eps["added"] or eps["removed"] or eps["changed"]
        or sch["added"] or sch["removed"] or sch["changed"]
    )


def compute_diff(old_envelope, new_envelope):
    """Compute a structural diff MODEL between two fetch envelopes.

    Returns a plain dict. Every number in the rendered output is derivable from
    this model; the renderer never recounts.
    """
    old_resources = {e.get("label", ""): e for e in old_envelope.get("resources", [])}
    new_resources = {e.get("label", ""): e for e in new_envelope.get("resources", [])}
    old_labels = set(old_resources)
    new_labels = set(new_resources)

    resources_added = sorted(new_labels - old_labels)
    resources_removed = sorted(old_labels - new_labels)
    changed_resources = []

    endpoints_added = endpoints_removed = endpoints_changed = 0
    schemas_added = schemas_removed = schemas_changed = 0
    deprecations_added = deprecations_removed = 0

    for label in sorted(old_labels & new_labels):
        rdiff = _diff_resource(old_resources[label], new_resources[label])
        if _resource_has_changes(rdiff):
            changed_resources.append({"label": label, "diff": rdiff})
            endpoints_added += len(rdiff["endpoints"]["added"])
            endpoints_removed += len(rdiff["endpoints"]["removed"])
            endpoints_changed += len(rdiff["endpoints"]["changed"])
            schemas_added += len(rdiff["schemas"]["added"])
            schemas_removed += len(rdiff["schemas"]["removed"])
            schemas_changed += len(rdiff["schemas"]["changed"])
            deprecations_added += rdiff["deprecations_added"]
            deprecations_removed += rdiff["deprecations_removed"]

    summary = {
        "resources_added": len(resources_added),
        "resources_removed": len(resources_removed),
        "resources_changed": len(changed_resources),
        "endpoints_added": endpoints_added,
        "endpoints_removed": endpoints_removed,
        "endpoints_changed": endpoints_changed,
        "schemas_added": schemas_added,
        "schemas_removed": schemas_removed,
        "schemas_changed": schemas_changed,
        "deprecations_added": deprecations_added,
        "deprecations_removed": deprecations_removed,
    }

    return {
        "old": {
            "timestamp": old_envelope.get("timestamp", ""),
            "tenant": old_envelope.get("tenant", ""),
        },
        "new": {
            "timestamp": new_envelope.get("timestamp", ""),
            "tenant": new_envelope.get("tenant", ""),
        },
        "resources_added": resources_added,
        "resources_removed": resources_removed,
        "resources_changed": changed_resources,
        "summary": summary,
    }


def has_changes(model):
    """True if the model contains any structural change."""
    s = model["summary"]
    return any(
        s[k]
        for k in (
            "resources_added",
            "resources_removed",
            "resources_changed",
            "endpoints_added",
            "endpoints_removed",
            "endpoints_changed",
            "schemas_added",
            "schemas_removed",
            "schemas_changed",
        )
    )


def _ep_label(ep):
    return f"`{ep['method']} {ep['path']}`"


def render_diff(model):
    """Render the diff MODEL as markdown. No em dashes, no horizontal rules."""
    lines = []
    old = model["old"]
    new = model["new"]
    lines.append("# Mambu API Structural Diff")
    lines.append("")
    lines.append(f"Old: {old['timestamp']} (tenant {old['tenant']})")
    lines.append(f"New: {new['timestamp']} (tenant {new['tenant']})")
    lines.append("")

    s = model["summary"]
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Added | Removed | Changed |")
    lines.append("|----------|-------|---------|---------|")
    lines.append(
        f"| Resources | {s['resources_added']} | {s['resources_removed']} | {s['resources_changed']} |"
    )
    lines.append(
        f"| Endpoints | {s['endpoints_added']} | {s['endpoints_removed']} | {s['endpoints_changed']} |"
    )
    lines.append(
        f"| Schemas | {s['schemas_added']} | {s['schemas_removed']} | {s['schemas_changed']} |"
    )
    lines.append("")
    lines.append(
        f"Deprecations added: {s['deprecations_added']} | Deprecations removed: {s['deprecations_removed']}"
    )
    lines.append("")

    if not has_changes(model):
        lines.append("No structural changes detected.")
        lines.append("")
        return "\n".join(lines)

    if model["resources_added"]:
        lines.append("## Resources added")
        lines.append("")
        for label in model["resources_added"]:
            lines.append(f"- {label}")
        lines.append("")

    if model["resources_removed"]:
        lines.append("## Resources removed")
        lines.append("")
        for label in model["resources_removed"]:
            lines.append(f"- {label}")
        lines.append("")

    for rc in model["resources_changed"]:
        lines.append(f"## {rc['label']}")
        lines.append("")
        rdiff = rc["diff"]
        eps = rdiff["endpoints"]
        for ep in eps["added"]:
            lines.append(f"- Added: {_ep_label(ep)}")
        for ep in eps["removed"]:
            lines.append(f"- Removed: {_ep_label(ep)}")
        for ep in eps["changed"]:
            lines.append(f"- Changed: {_ep_label(ep)}")
            lines.extend(_render_endpoint_changes(ep["changes"]))
        sch = rdiff["schemas"]
        for name in sch["added"]:
            lines.append(f"- Schema added: `{name}`")
        for name in sch["removed"]:
            lines.append(f"- Schema removed: `{name}`")
        for sc in sch["changed"]:
            lines.append(f"- Schema changed: `{sc['name']}`")
            lines.extend(_render_schema_changes(sc["changes"]))
        lines.append("")

    return "\n".join(lines)


def _render_endpoint_changes(changes):
    out = []
    params = changes.get("parameters")
    if params:
        for p in params["added"]:
            out.append(f"  - Parameter added: `{p['name']}` (in {p['in']})")
        for p in params["removed"]:
            out.append(f"  - Parameter removed: `{p['name']}` (in {p['in']})")
        for p in params["changed"]:
            for field, delta in p["changes"].items():
                out.append(
                    f"  - Parameter `{p['name']}` (in {p['in']}) {field}: {delta['old']} -> {delta['new']}"
                )
    rb = changes.get("request_body")
    if rb:
        for name in rb.get("added", []):
            out.append(f"  - Request body property added: `{name}`")
        for name in rb.get("removed", []):
            out.append(f"  - Request body property removed: `{name}`")
        for tc in rb.get("type_changed", []):
            out.append(f"  - Request body property `{tc['name']}` type: {tc['old']} -> {tc['new']}")
        if "required" in rb:
            out.append(
                f"  - Request body required: {rb['required']['old']} -> {rb['required']['new']}"
            )
    resp = changes.get("responses")
    if resp:
        for code in resp["added"]:
            out.append(f"  - Response code added: `{code}`")
        for code in resp["removed"]:
            out.append(f"  - Response code removed: `{code}`")
    rs = changes.get("response_schema")
    if rs:
        out.append(f"  - 2xx response schema: {rs['old']} -> {rs['new']}")
    dep = changes.get("deprecated")
    if dep:
        out.append(f"  - Deprecated: {dep['old']} -> {dep['new']}")
    return out


def _render_schema_changes(changes):
    out = []
    for name in changes.get("added", []):
        out.append(f"  - Property added: `{name}`")
    for name in changes.get("removed", []):
        out.append(f"  - Property removed: `{name}`")
    for tc in changes.get("type_changed", []):
        out.append(f"  - Property `{tc['name']}` type: {tc['old']} -> {tc['new']}")
    if "required" in changes:
        out.append(f"  - Required: {changes['required']['old']} -> {changes['required']['new']}")
    if "enum" in changes:
        out.append(f"  - Enum: {changes['enum']['old']} -> {changes['enum']['new']}")
    return out
