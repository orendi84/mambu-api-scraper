import copy
import json
import logging
import re

log = logging.getLogger(__name__)

# All component sections we merge and collision-check.
_COMPONENT_SECTIONS = (
    "schemas",
    "parameters",
    "responses",
    "requestBodies",
    "headers",
    "examples",
    "links",
    "callbacks",
    "securitySchemes",
)


def _canon(obj):
    """Canonical JSON for json-normalized equality."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _camel_prefix(label):
    """Reduce a resource label to CamelCase alphanumerics.

    'Loan Accounts (v2)' -> 'LoanAccounts'
    """
    tokens = re.findall(r"[A-Za-z0-9]+", label)
    return "".join(t[:1].upper() + t[1:] for t in tokens)


def _ptr_escape(name):
    """JSON-pointer escape a component name (~ then /)."""
    return name.replace("~", "~0").replace("/", "~1")


def _raw_pointer(section, name):
    """Construct a raw local $ref pointer with JSON-pointer escaping."""
    return f"#/components/{section}/{_ptr_escape(name)}"


_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def _dedupe_operation_ids(spec, prefix, seen_op_ids):
    """Rename operationIds already used by an earlier spec.

    Mambu's per-resource specs reuse generic operationIds (e.g. 'getAll'),
    which makes the merged document invalid. First occurrence keeps the id;
    later ones become '{id}_{prefix}' (then '_2', '_3'... if needed).
    Mutates spec in place; returns {old_id: new_id} for this spec.
    """
    renames = {}
    for item in spec.get("paths", {}).values():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not isinstance(op_id, str):
                continue
            if op_id not in seen_op_ids:
                seen_op_ids.add(op_id)
                continue
            new_id = f"{op_id}_{prefix}"
            suffix = 2
            while new_id in seen_op_ids:
                new_id = f"{op_id}_{prefix}_{suffix}"
                suffix += 1
            op["operationId"] = new_id
            seen_op_ids.add(new_id)
            renames[op_id] = new_id
    return renames


def _update_link_operation_ids(node, renames):
    """Update link objects that reference a renamed operationId.

    Operations carry a 'responses' key and are skipped; OpenAPI link
    objects reference operations by 'operationId' and have no 'responses'.
    """
    if isinstance(node, dict):
        op_id = node.get("operationId")
        if isinstance(op_id, str) and op_id in renames and "responses" not in node:
            node["operationId"] = renames[op_id]
        for value in node.values():
            _update_link_operation_ids(value, renames)
    elif isinstance(node, list):
        for item in node:
            _update_link_operation_ids(item, renames)


def _rewrite_refs(node, rename_map):
    """Recursively replace any $ref value that EXACTLY matches a mapped raw pointer.

    Walks dicts and lists at all nesting depths. Never substring-replaces.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value in rename_map:
                node[key] = rename_map[value]
            else:
                _rewrite_refs(value, rename_map)
    elif isinstance(node, list):
        for item in node:
            _rewrite_refs(item, rename_map)


def build_merged_spec(specs_with_labels, tenant, timestamp):
    """Merge multiple OpenAPI specs into a single OpenAPI 3.0.3 document.

    specs_with_labels: list of (label, spec) tuples.
    Returns the merged dict.
    """
    labels = [label for label, _ in specs_with_labels]
    merged = {
        "openapi": "3.0.3",
        "info": {
            "title": "Mambu API (merged)",
            "version": timestamp,
            "description": (
                f"Merged Mambu API documentation for tenant {tenant}, "
                f"combining {len(specs_with_labels)} resources."
            ),
            "x-merged-from": labels,
        },
        "servers": [{"url": f"https://{tenant}/api"}],
        "paths": {},
        "components": {},
    }

    merge_warnings = []
    seen_op_ids = set()

    for label, spec in specs_with_labels:
        if not isinstance(spec, dict):
            continue
        spec = copy.deepcopy(spec)
        prefix = _camel_prefix(label)

        # Deduplicate operationIds across specs (Mambu reuses e.g. 'getAll').
        op_renames = _dedupe_operation_ids(spec, prefix, seen_op_ids)
        if op_renames:
            _update_link_operation_ids(spec, op_renames)

        # Per-spec rename map: raw pointer string -> new raw pointer string.
        rename_map = {}

        for section in _COMPONENT_SECTIONS:
            incoming = spec.get("components", {}).get(section, {})
            if not isinstance(incoming, dict):
                continue
            merged_section = merged["components"].setdefault(section, {})
            for name, definition in incoming.items():
                if name not in merged_section:
                    merged_section[name] = definition
                    continue
                # Name collision: share if json-normalized equal.
                if _canon(merged_section[name]) == _canon(definition):
                    continue
                # Otherwise rename with prefix, then _2, _3... if still colliding.
                new_name = f"{prefix}_{name}"
                suffix = 2
                while new_name in merged_section and _canon(merged_section[new_name]) != _canon(definition):
                    new_name = f"{prefix}_{name}_{suffix}"
                    suffix += 1
                # If new_name already holds an identical definition, share it.
                if new_name in merged_section:
                    # equal by the loop condition
                    pass
                else:
                    merged_section[new_name] = definition
                old_ptr = _raw_pointer(section, name)
                new_ptr = _raw_pointer(section, new_name)
                rename_map[old_ptr] = new_ptr

        # Rewrite refs in the (deep-copied) spec for renamed components.
        if rename_map:
            _rewrite_refs(spec, rename_map)

        # Path merging: first path wins; skip the entire incoming path item on collision.
        for path, item in spec.get("paths", {}).items():
            if path in merged["paths"]:
                warning = f"Path collision: '{path}' from resource '{label}' skipped (already defined)"
                log.warning(warning)
                merge_warnings.append(warning)
                continue
            merged["paths"][path] = item

    # Drop empty components sections for cleanliness.
    merged["components"] = {k: v for k, v in merged["components"].items() if v}

    if merge_warnings:
        merged["info"]["x-merge-warnings"] = merge_warnings

    return merged
