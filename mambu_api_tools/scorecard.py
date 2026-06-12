import logging
from decimal import ROUND_HALF_UP, Decimal

from .common import resolve_ref

log = logging.getLogger(__name__)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Composite weights (documented here and in the rendered output).
WEIGHTS = {
    "pct_described": 25,
    "pct_2xx_schema": 25,
    "pct_examples": 20,
    "pct_params_described": 20,
    "deprecation_hygiene": 10,
}


def _round1(value):
    """Round half-up to 1 decimal place. Returns a float."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _resolve_param(spec, p):
    if not isinstance(p, dict):
        return None
    if "$ref" in p:
        resolved = resolve_ref(spec, p["$ref"])
        return resolved if isinstance(resolved, dict) else None
    return p


def _iter_operations(spec):
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(details, dict):
                continue
            yield (method.lower(), path, details)


def _op_described(details):
    return bool(details.get("summary", "").strip() or details.get("description", "").strip())


def _success_responses(spec, details):
    """Yield resolved response objects for 2xx/102 codes."""
    responses = details.get("responses", {})
    for code, resp in responses.items():
        code_str = str(code)
        if not (code_str.startswith("2") or code_str == "102"):
            continue
        if isinstance(resp, dict) and "$ref" in resp:
            resolved = resolve_ref(spec, resp["$ref"])
            resp = resolved if isinstance(resolved, dict) else {}
        if isinstance(resp, dict):
            yield resp


def _op_has_2xx(spec, details):
    return any(True for _ in _success_responses(spec, details))


def _op_2xx_has_schema(spec, details):
    for resp in _success_responses(spec, details):
        for _ct, ct_info in resp.get("content", {}).items():
            if isinstance(ct_info, dict) and ct_info.get("schema"):
                return True
    return False


def _resolve_request_body(spec, details):
    rb = details.get("requestBody", {})
    if isinstance(rb, dict) and "$ref" in rb:
        resolved = resolve_ref(spec, rb["$ref"])
        rb = resolved if isinstance(resolved, dict) else {}
    return rb if isinstance(rb, dict) else {}


def _op_has_example(spec, details):
    """True if any param, requestBody content, or response content carries an example."""
    # Parameters
    for raw in details.get("parameters", []):
        p = _resolve_param(spec, raw)
        if p is None:
            continue
        if p.get("example") is not None or p.get("examples"):
            return True
        schema = p.get("schema", {})
        if isinstance(schema, dict) and schema.get("example") is not None:
            return True
    # Request body content
    rb = _resolve_request_body(spec, details)
    for _ct, ct_info in rb.get("content", {}).items():
        if not isinstance(ct_info, dict):
            continue
        if ct_info.get("example") is not None or ct_info.get("examples"):
            return True
        schema = ct_info.get("schema", {})
        if isinstance(schema, dict) and schema.get("example") is not None:
            return True
    # Response content
    responses = details.get("responses", {})
    for _code, resp in responses.items():
        if isinstance(resp, dict) and "$ref" in resp:
            resolved = resolve_ref(spec, resp["$ref"])
            resp = resolved if isinstance(resolved, dict) else {}
        if not isinstance(resp, dict):
            continue
        for _ct, ct_info in resp.get("content", {}).items():
            if not isinstance(ct_info, dict):
                continue
            if ct_info.get("example") is not None or ct_info.get("examples"):
                return True
            schema = ct_info.get("schema", {})
            if isinstance(schema, dict) and schema.get("example") is not None:
                return True
    return False


def _crud_flags(spec):
    """Return a 5-char CRUD string for the resource.

    Slots: [list GET on collection, item GET on {param}-tail path,
    POST on collection, PUT/PATCH on item, DELETE on item].
    Present slots use their letter; absent slots use '-'.
    """
    list_get = item_get = create = update = delete = False
    for method, path, _details in _iter_operations(spec):
        segments = [s for s in path.split("/") if s]
        last = segments[-1] if segments else ""
        is_item = last.startswith("{") and last.endswith("}")
        if method == "get":
            if is_item:
                item_get = True
            else:
                list_get = True
        elif method == "post" and not is_item:
            create = True
        elif method in ("put", "patch") and is_item:
            update = True
        elif method == "delete" and is_item:
            delete = True
    return (
        ("L" if list_get else "-")
        + ("R" if item_get else "-")
        + ("C" if create else "-")
        + ("U" if update else "-")
        + ("D" if delete else "-")
    )


def _pct(numerator, denominator):
    if denominator == 0:
        return None
    return 100.0 * numerator / denominator


def _composite(metrics, deprecation_hygiene):
    """Weighted composite over present metrics; None metrics drop and renormalize."""
    total_weight = 0.0
    acc = 0.0
    for name, weight in WEIGHTS.items():
        if name == "deprecation_hygiene":
            value = deprecation_hygiene
        else:
            value = metrics.get(name)
        if value is None:
            continue
        acc += weight * value
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return acc / total_weight


def _compute_row(label, spec):
    ops = list(_iter_operations(spec))
    ops_total = len(ops)

    described = 0
    ops_with_2xx = 0
    ops_with_2xx_schema = 0
    examples = 0
    deprecated_ops = 0

    params_total = 0
    params_described = 0

    for _method, _path, details in ops:
        if _op_described(details):
            described += 1
        if _op_has_2xx(spec, details):
            ops_with_2xx += 1
            if _op_2xx_has_schema(spec, details):
                ops_with_2xx_schema += 1
        if _op_has_example(spec, details):
            examples += 1
        if details.get("deprecated"):
            deprecated_ops += 1
        for raw in details.get("parameters", []):
            p = _resolve_param(spec, raw)
            if p is None:
                continue
            params_total += 1
            if p.get("description", "").strip():
                params_described += 1

    pct_described = _pct(described, ops_total)
    pct_2xx_schema = _pct(ops_with_2xx_schema, ops_with_2xx)
    pct_examples = _pct(examples, ops_total)
    pct_params_described = _pct(params_described, params_total)  # None when zero params

    if deprecated_ops == 0:
        deprecation_hygiene = 100.0
    else:
        deprecation_hygiene = 100.0 * (1 - deprecated_ops / ops_total) if ops_total else 100.0

    metrics = {
        "pct_described": pct_described,
        "pct_2xx_schema": pct_2xx_schema,
        "pct_examples": pct_examples,
        "pct_params_described": pct_params_described,
    }
    composite = _composite(metrics, deprecation_hygiene)

    return {
        "label": label,
        "ops_total": ops_total,
        "pct_described": _round1(pct_described) if pct_described is not None else None,
        "pct_2xx_schema": _round1(pct_2xx_schema) if pct_2xx_schema is not None else None,
        "pct_examples": _round1(pct_examples) if pct_examples is not None else None,
        "pct_params_described": _round1(pct_params_described) if pct_params_described is not None else None,
        "params_total": params_total,
        "deprecated_ops": deprecated_ops,
        "deprecation_hygiene": _round1(deprecation_hygiene),
        "crud": _crud_flags(spec),
        "composite": _round1(composite),
    }


def compute_scorecard(envelope):
    """Compute a quality scorecard MODEL for an envelope.

    Returns a dict with per-resource rows and overall aggregates.
    """
    rows = []
    for entry in envelope.get("resources", []):
        label = entry.get("label", "Unknown")
        spec = entry.get("spec", {})
        rows.append(_compute_row(label, spec))

    # Overall aggregates pool raw numerators/denominators across all resources so
    # that zero-param resources are excluded from the param aggregate.
    overall = _overall_aggregate(envelope)
    overall["resources_total"] = len(rows)
    overall["ops_total"] = sum(r["ops_total"] for r in rows)

    return {"rows": rows, "overall": overall, "weights": dict(WEIGHTS)}


def _overall_aggregate(envelope):
    """Pool raw numerators/denominators across all resources for exact overall metrics."""
    described = ops_total = 0
    ops_with_2xx = ops_with_2xx_schema = 0
    examples = 0
    params_total = params_described = 0
    deprecated_ops = 0

    for entry in envelope.get("resources", []):
        spec = entry.get("spec", {})
        for _method, _path, details in _iter_operations(spec):
            ops_total += 1
            if _op_described(details):
                described += 1
            if _op_has_2xx(spec, details):
                ops_with_2xx += 1
                if _op_2xx_has_schema(spec, details):
                    ops_with_2xx_schema += 1
            if _op_has_example(spec, details):
                examples += 1
            if details.get("deprecated"):
                deprecated_ops += 1
            for raw in details.get("parameters", []):
                p = _resolve_param(spec, raw)
                if p is None:
                    continue
                params_total += 1
                if p.get("description", "").strip():
                    params_described += 1

    pct_described = _pct(described, ops_total)
    pct_2xx_schema = _pct(ops_with_2xx_schema, ops_with_2xx)
    pct_examples = _pct(examples, ops_total)
    pct_params_described = _pct(params_described, params_total)
    if deprecated_ops == 0:
        deprecation_hygiene = 100.0
    else:
        deprecation_hygiene = 100.0 * (1 - deprecated_ops / ops_total) if ops_total else 100.0

    metrics = {
        "pct_described": pct_described,
        "pct_2xx_schema": pct_2xx_schema,
        "pct_examples": pct_examples,
        "pct_params_described": pct_params_described,
    }
    composite = _composite(metrics, deprecation_hygiene)

    return {
        "pct_described": _round1(pct_described) if pct_described is not None else None,
        "pct_2xx_schema": _round1(pct_2xx_schema) if pct_2xx_schema is not None else None,
        "pct_examples": _round1(pct_examples) if pct_examples is not None else None,
        "pct_params_described": _round1(pct_params_described) if pct_params_described is not None else None,
        "deprecated_ops": deprecated_ops,
        "deprecation_hygiene": _round1(deprecation_hygiene),
        "composite": _round1(composite),
    }


def _fmt(value):
    """Format a percentage cell; 'n/a' for None."""
    return "n/a" if value is None else f"{value:.1f}"


def render_scorecard(model):
    """Render the scorecard MODEL as markdown. No em dashes, no horizontal rules."""
    lines = []
    overall = model["overall"]
    lines.append("# Mambu API Quality Scorecard")
    lines.append("")

    lines.append("## Overall")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Composite | {overall['composite']:.1f} |")
    lines.append(f"| Resources | {overall['resources_total']} |")
    lines.append(f"| Operations | {overall['ops_total']} |")
    lines.append(f"| Described % | {_fmt(overall['pct_described'])} |")
    lines.append(f"| 2xx schema % | {_fmt(overall['pct_2xx_schema'])} |")
    lines.append(f"| Examples % | {_fmt(overall['pct_examples'])} |")
    lines.append(f"| Params described % | {_fmt(overall['pct_params_described'])} |")
    lines.append(f"| Deprecation hygiene | {overall['deprecation_hygiene']:.1f} |")
    lines.append(f"| Deprecated ops | {overall['deprecated_ops']} |")
    lines.append("")

    lines.append("## Per resource")
    lines.append("")
    lines.append("Sorted worst composite first.")
    lines.append("")
    lines.append(
        "| Resource | Composite | Ops | Described % | 2xx schema % | Examples % | Params described % | CRUD | Deprecated |"
    )
    lines.append(
        "|----------|-----------|-----|-------------|--------------|------------|--------------------|------|------------|"
    )
    ordered = sorted(model["rows"], key=lambda r: (r["composite"], r["label"]))
    for r in ordered:
        lines.append(
            f"| {r['label']} | {r['composite']:.1f} | {r['ops_total']} | {_fmt(r['pct_described'])} | "
            f"{_fmt(r['pct_2xx_schema'])} | {_fmt(r['pct_examples'])} | {_fmt(r['pct_params_described'])} | "
            f"{r['crud']} | {r['deprecated_ops']} |"
        )
    lines.append("")

    lines.append("## Metric definitions and weights")
    lines.append("")
    lines.append(
        f"- Described (weight {WEIGHTS['pct_described']}): operations with a non-empty summary or description, "
        "over all operations."
    )
    lines.append(
        f"- 2xx schema (weight {WEIGHTS['pct_2xx_schema']}): among operations with at least one 2xx or 102 response, "
        "those where at least one such response has a content schema."
    )
    lines.append(
        f"- Examples (weight {WEIGHTS['pct_examples']}): operations carrying any example "
        "(parameter, requestBody content, or response content), over all operations."
    )
    lines.append(
        f"- Params described (weight {WEIGHTS['pct_params_described']}): resolved parameters with a non-empty "
        "description, over all parameters; resources with zero parameters are excluded (n/a) and renormalized."
    )
    lines.append(
        f"- Deprecation hygiene (weight {WEIGHTS['deprecation_hygiene']}): 100 if no deprecated operations, "
        "otherwise 100 times (1 minus deprecated divided by total operations)."
    )
    lines.append(
        "- Composite: weighted mean of the metrics above, 0 to 100; n/a metrics are dropped and remaining "
        "weights renormalized. CRUD is reported as flags (L list, R item read, C create, U update, D delete) "
        "and is not part of the composite. All values rounded half-up to 1 decimal."
    )
    lines.append("")

    return "\n".join(lines)
