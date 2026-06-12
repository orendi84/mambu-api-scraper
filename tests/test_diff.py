"""Tests for the structural spec diff engine."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mambu_api_tools import cli
from mambu_api_tools.diff import compute_diff, has_changes, render_diff


def _envelope(resources, timestamp="2026-01-01T00:00:00+00:00", tenant="t.example.com"):
    return {
        "timestamp": timestamp,
        "tenant": tenant,
        "resources_total": len(resources),
        "resources_failed": 0,
        "endpoints_total": 0,
        "resources": resources,
    }


def _resource(label, paths=None, schemas=None):
    spec = {"openapi": "3.0.3", "info": {"title": label, "version": "1"}, "paths": paths or {}}
    if schemas is not None:
        spec["components"] = {"schemas": schemas}
    return {"label": label, "json_path": "json/x_v2_swagger.json", "endpoints": 0, "spec": spec}


def _op(summary="op", parameters=None, request_body=None, responses=None, deprecated=None):
    d = {"summary": summary}
    if parameters is not None:
        d["parameters"] = parameters
    if request_body is not None:
        d["requestBody"] = request_body
    if responses is not None:
        d["responses"] = responses
    if deprecated is not None:
        d["deprecated"] = deprecated
    return d


# ---------------------------------------------------------------------------
# identical -> zero model
# ---------------------------------------------------------------------------

def test_identical_envelopes_zero_model():
    res = [_resource("Clients", {"/clients": {"get": _op()}})]
    old = _envelope(res)
    new = _envelope(res)
    model = compute_diff(old, new)
    assert not has_changes(model)
    md = render_diff(model)
    assert "No structural changes detected." in md
    # zero-count table still present
    assert "| Resources | 0 | 0 | 0 |" in md


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

def test_endpoint_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op()}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op(), "post": _op()}})])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_added"] == 1
    assert has_changes(model)
    md = render_diff(model)
    assert "Added: `POST /clients`" in md


def test_endpoint_removed():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op(), "delete": _op()}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op()}})])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_removed"] == 1
    md = render_diff(model)
    assert "Removed: `DELETE /clients`" in md


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------

def test_param_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op(parameters=[])}})])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
        ])}})
    ])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "Parameter added: `limit` (in query)" in md


def test_param_type_change():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "limit", "in": "query", "schema": {"type": "string"}},
        ])}})
    ])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
        ])}})
    ])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "type: string -> integer" in md


def test_param_enum_extended():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "state", "in": "query", "schema": {"type": "string", "enum": ["A"]}},
        ])}})
    ])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "state", "in": "query", "schema": {"type": "string", "enum": ["A", "B"]}},
        ])}})
    ])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "enum:" in md


def test_param_enum_order_insensitive():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "state", "in": "query", "schema": {"type": "string", "enum": ["A", "B"]}},
        ])}})
    ])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "state", "in": "query", "schema": {"type": "string", "enum": ["B", "A"]}},
        ])}})
    ])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 0


def test_param_required_transition():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "id", "in": "query", "required": False, "schema": {"type": "string"}},
        ])}})
    ])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[
            {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}},
        ])}})
    ])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "required: False -> True" in md


# ---------------------------------------------------------------------------
# request body
# ---------------------------------------------------------------------------

def test_request_body_property_added():
    old_rb = {"content": {"application/json": {"schema": {"properties": {"a": {"type": "string"}}}}}}
    new_rb = {"content": {"application/json": {"schema": {"properties": {
        "a": {"type": "string"}, "b": {"type": "integer"}}}}}}
    old = _envelope([_resource("Clients", {"/clients": {"post": _op(request_body=old_rb)}})])
    new = _envelope([_resource("Clients", {"/clients": {"post": _op(request_body=new_rb)}})])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "Request body property added: `b`" in md


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------

def test_response_code_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op(responses={"200": {"description": "ok"}})}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op(responses={
        "200": {"description": "ok"}, "404": {"description": "nope"}})}})])
    model = compute_diff(old, new)
    assert model["summary"]["endpoints_changed"] == 1
    md = render_diff(model)
    assert "Response code added: `404`" in md


# ---------------------------------------------------------------------------
# deprecation
# ---------------------------------------------------------------------------

def test_deprecated_transition_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op(deprecated=False)}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op(deprecated=True)}})])
    model = compute_diff(old, new)
    assert model["summary"]["deprecations_added"] == 1
    assert model["summary"]["deprecations_removed"] == 0
    md = render_diff(model)
    assert "Deprecated: False -> True" in md


def test_deprecated_transition_removed():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op(deprecated=True)}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op(deprecated=False)}})])
    model = compute_diff(old, new)
    assert model["summary"]["deprecations_removed"] == 1
    assert model["summary"]["deprecations_added"] == 0


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

def test_schema_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op()}}, schemas={})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op()}}, schemas={
        "Client": {"type": "object", "properties": {"id": {"type": "string"}}}})])
    model = compute_diff(old, new)
    assert model["summary"]["schemas_added"] == 1
    md = render_diff(model)
    assert "Schema added: `Client`" in md


def test_schema_changed_property():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op()}}, schemas={
        "Client": {"properties": {"id": {"type": "string"}}}})])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op()}}, schemas={
        "Client": {"properties": {"id": {"type": "string"}, "name": {"type": "string"}}}})])
    model = compute_diff(old, new)
    assert model["summary"]["schemas_changed"] == 1
    md = render_diff(model)
    assert "Schema changed: `Client`" in md
    assert "Property added: `name`" in md


# ---------------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------------

def test_resource_added():
    old = _envelope([_resource("Clients", {"/clients": {"get": _op()}})])
    new = _envelope([
        _resource("Clients", {"/clients": {"get": _op()}}),
        _resource("Loans", {"/loans": {"get": _op()}}),
    ])
    model = compute_diff(old, new)
    assert model["summary"]["resources_added"] == 1
    md = render_diff(model)
    assert "Loans" in md


def test_resource_removed():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op()}}),
        _resource("Loans", {"/loans": {"get": _op()}}),
    ])
    new = _envelope([_resource("Clients", {"/clients": {"get": _op()}})])
    model = compute_diff(old, new)
    assert model["summary"]["resources_removed"] == 1


# ---------------------------------------------------------------------------
# summary counts == detail lengths (the SSOT invariant)
# ---------------------------------------------------------------------------

def test_summary_counts_equal_detail_lengths():
    old = _envelope([
        _resource("Clients", {"/clients": {"get": _op(parameters=[]), "delete": _op()}}, schemas={
            "Old": {"properties": {}}}),
        _resource("Gone", {"/gone": {"get": _op()}}),
    ])
    new = _envelope([
        _resource("Clients", {"/clients": {
            "get": _op(parameters=[{"name": "q", "in": "query", "schema": {"type": "string"}}]),
            "post": _op(),
        }}, schemas={"New": {"properties": {}}}),
        _resource("Fresh", {"/fresh": {"get": _op()}}),
    ])
    model = compute_diff(old, new)
    s = model["summary"]

    assert s["resources_added"] == len(model["resources_added"])
    assert s["resources_removed"] == len(model["resources_removed"])
    assert s["resources_changed"] == len(model["resources_changed"])

    ep_a = ep_r = ep_c = sc_a = sc_r = sc_c = dep_a = dep_r = 0
    for rc in model["resources_changed"]:
        d = rc["diff"]
        ep_a += len(d["endpoints"]["added"])
        ep_r += len(d["endpoints"]["removed"])
        ep_c += len(d["endpoints"]["changed"])
        sc_a += len(d["schemas"]["added"])
        sc_r += len(d["schemas"]["removed"])
        sc_c += len(d["schemas"]["changed"])
        dep_a += d["deprecations_added"]
        dep_r += d["deprecations_removed"]
    assert s["endpoints_added"] == ep_a
    assert s["endpoints_removed"] == ep_r
    assert s["endpoints_changed"] == ep_c
    assert s["schemas_added"] == sc_a
    assert s["schemas_removed"] == sc_r
    assert s["schemas_changed"] == sc_c
    assert s["deprecations_added"] == dep_a
    assert s["deprecations_removed"] == dep_r


# ---------------------------------------------------------------------------
# CLI --exit-code behavior
# ---------------------------------------------------------------------------

def _write_envelope(tmp_path, name, resources):
    p = tmp_path / name
    p.write_text(json.dumps(_envelope(resources)), encoding="utf-8")
    return str(p)


def test_cli_exit_code_changes(tmp_path, monkeypatch, capsys):
    old = _write_envelope(tmp_path, "old.json", [_resource("Clients", {"/clients": {"get": _op()}})])
    new = _write_envelope(tmp_path, "new.json", [_resource("Clients", {"/clients": {"get": _op(), "post": _op()}})])
    monkeypatch.setattr(sys, "argv", ["mambu-api-tools", "diff", old, new, "--exit-code"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_cli_exit_code_no_changes(tmp_path, monkeypatch, capsys):
    res = [_resource("Clients", {"/clients": {"get": _op()}})]
    old = _write_envelope(tmp_path, "old.json", res)
    new = _write_envelope(tmp_path, "new.json", res)
    monkeypatch.setattr(sys, "argv", ["mambu-api-tools", "diff", old, new, "--exit-code"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_cli_no_exit_code_flag_always_zero(tmp_path, monkeypatch, capsys):
    old = _write_envelope(tmp_path, "old.json", [_resource("Clients", {"/clients": {"get": _op()}})])
    new = _write_envelope(tmp_path, "new.json", [_resource("Clients", {"/clients": {"get": _op(), "post": _op()}})])
    monkeypatch.setattr(sys, "argv", ["mambu-api-tools", "diff", old, new])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_cli_malformed_input_exits_2(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    good = _write_envelope(tmp_path, "good.json", [_resource("Clients", {"/clients": {"get": _op()}})])
    monkeypatch.setattr(sys, "argv", ["mambu-api-tools", "diff", str(bad), good])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# golden render
# ---------------------------------------------------------------------------

def test_golden_render():
    old = _envelope([
        _resource("Clients", {
            "/clients": {"get": _op(parameters=[
                {"name": "limit", "in": "query", "schema": {"type": "string"}},
            ])},
            "/clients/{id}": {"delete": _op()},
        }, schemas={"Client": {"properties": {"id": {"type": "string"}}}}),
        _resource("Legacy", {"/legacy": {"get": _op()}}),
    ], timestamp="2026-01-01T00:00:00+00:00", tenant="demo.example.com")
    new = _envelope([
        _resource("Clients", {
            "/clients": {
                "get": _op(parameters=[
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                ]),
                "post": _op(request_body={"content": {"application/json": {"schema": {
                    "properties": {"name": {"type": "string"}}}}}}),
            },
        }, schemas={"Client": {"properties": {"id": {"type": "string"}, "name": {"type": "string"}}}}),
        _resource("Loans", {"/loans": {"get": _op(deprecated=True)}}),
    ], timestamp="2026-02-01T00:00:00+00:00", tenant="demo.example.com")

    model = compute_diff(old, new)
    rendered = render_diff(model)

    golden_path = os.path.join(os.path.dirname(__file__), "golden", "diff_sample.md")
    expected = open(golden_path, encoding="utf-8").read()
    assert rendered == expected
