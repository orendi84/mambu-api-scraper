"""Tests for the API quality scorecard."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mambu_api_tools.scorecard import compute_scorecard, render_scorecard


def _envelope(resources, timestamp="2026-01-01T00:00:00+00:00", tenant="t.example.com"):
    return {
        "timestamp": timestamp,
        "tenant": tenant,
        "resources_total": len(resources),
        "resources_failed": 0,
        "endpoints_total": 0,
        "resources": resources,
    }


def _resource(label, paths, schemas=None):
    spec = {"openapi": "3.0.3", "info": {"title": label, "version": "1"}, "paths": paths}
    if schemas is not None:
        spec["components"] = {"schemas": schemas}
    return {"label": label, "json_path": "json/x_v2_swagger.json", "endpoints": 0, "spec": spec}


# ---------------------------------------------------------------------------
# hand-computed values
# ---------------------------------------------------------------------------

def test_hand_computed_metrics():
    # Op1 GET /clients: described, 200 has content schema, response example, 1 param described.
    # Op2 POST /clients: not described, 201 no content schema, no example, no params.
    paths = {
        "/clients": {
            "get": {
                "summary": "List clients",
                "parameters": [
                    {"name": "limit", "in": "query", "description": "Max items", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"},
                                "example": {"a": 1},
                            }
                        },
                    }
                },
            },
            "post": {
                "responses": {"201": {"description": "created"}},
            },
        }
    }
    model = compute_scorecard(_envelope([_resource("Clients", paths)]))
    row = model["rows"][0]
    assert row["ops_total"] == 2
    assert row["pct_described"] == 50.0
    assert row["pct_2xx_schema"] == 50.0
    assert row["pct_examples"] == 50.0
    assert row["pct_params_described"] == 100.0
    assert row["deprecated_ops"] == 0
    assert row["deprecation_hygiene"] == 100.0
    # Composite = (25*50 + 25*50 + 20*50 + 20*100 + 10*100)/100 = 65.0
    assert row["composite"] == 65.0

    overall = model["overall"]
    assert overall["composite"] == 65.0
    assert overall["ops_total"] == 2
    assert overall["resources_total"] == 1


# ---------------------------------------------------------------------------
# zero-params -> None handling and weight renormalization
# ---------------------------------------------------------------------------

def test_zero_params_none_and_renormalization():
    # One op: described, 200 has content schema, has example, NO params, not deprecated.
    paths = {
        "/ping": {
            "get": {
                "summary": "Ping",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"type": "string"}, "example": "pong"}},
                    }
                },
            }
        }
    }
    model = compute_scorecard(_envelope([_resource("Ping", paths)]))
    row = model["rows"][0]
    assert row["pct_params_described"] is None
    # Present metrics: described 100, 2xx_schema 100, examples 100, hygiene 100.
    # Params weight (20) dropped; renormalized over 25+25+20+10 = 80.
    # Composite = (25*100 + 25*100 + 20*100 + 10*100)/80 = 8000/80 = 100.0
    assert row["composite"] == 100.0
    assert model["overall"]["pct_params_described"] is None


# ---------------------------------------------------------------------------
# CRUD detection: collection vs item paths
# ---------------------------------------------------------------------------

def test_crud_detection_full():
    paths = {
        "/clients": {
            "get": {"summary": "list", "responses": {"200": {"description": "ok"}}},
            "post": {"summary": "create", "responses": {"201": {"description": "ok"}}},
        },
        "/clients/{id}": {
            "get": {"summary": "read", "responses": {"200": {"description": "ok"}}},
            "put": {"summary": "update", "responses": {"200": {"description": "ok"}}},
            "delete": {"summary": "delete", "responses": {"204": {"description": "ok"}}},
        },
    }
    model = compute_scorecard(_envelope([_resource("Clients", paths)]))
    assert model["rows"][0]["crud"] == "LRCUD"


def test_crud_detection_partial():
    # Only a collection GET and a collection POST; no item routes.
    paths = {
        "/clients": {
            "get": {"summary": "list", "responses": {"200": {"description": "ok"}}},
            "post": {"summary": "create", "responses": {"201": {"description": "ok"}}},
        },
    }
    model = compute_scorecard(_envelope([_resource("Clients", paths)]))
    assert model["rows"][0]["crud"] == "L-C--"


def test_crud_item_get_via_param_tail():
    paths = {
        "/clients/{clientId}": {
            "get": {"summary": "read", "responses": {"200": {"description": "ok"}}},
        },
    }
    model = compute_scorecard(_envelope([_resource("Clients", paths)]))
    assert model["rows"][0]["crud"] == "-R---"


# ---------------------------------------------------------------------------
# deprecation hygiene
# ---------------------------------------------------------------------------

def test_deprecation_hygiene():
    paths = {
        "/a": {"get": {"summary": "a", "deprecated": True, "responses": {"200": {"description": "ok"}}}},
        "/b": {"get": {"summary": "b", "responses": {"200": {"description": "ok"}}}},
    }
    model = compute_scorecard(_envelope([_resource("R", paths)]))
    # 1 of 2 deprecated -> 100*(1 - 1/2) = 50.0
    assert model["rows"][0]["deprecation_hygiene"] == 50.0
    assert model["rows"][0]["deprecated_ops"] == 1


# ---------------------------------------------------------------------------
# param $ref resolution
# ---------------------------------------------------------------------------

def test_param_ref_resolution():
    paths = {
        "/clients": {
            "get": {
                "summary": "list",
                "parameters": [{"$ref": "#/components/parameters/Limit"}],
                "responses": {"200": {"description": "ok"}},
            }
        }
    }
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Clients", "version": "1"},
        "paths": paths,
        "components": {"parameters": {"Limit": {"name": "limit", "in": "query", "description": "max"}}},
    }
    env = _envelope([{"label": "Clients", "spec": spec}])
    model = compute_scorecard(env)
    assert model["rows"][0]["pct_params_described"] == 100.0


# ---------------------------------------------------------------------------
# golden render
# ---------------------------------------------------------------------------

def test_golden_render():
    good = {
        "/clients": {
            "get": {
                "summary": "List clients",
                "parameters": [
                    {"name": "limit", "in": "query", "description": "Max", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"type": "object"}, "example": {"a": 1}}},
                    }
                },
            },
            "post": {"responses": {"201": {"description": "created"}}},
        }
    }
    poor = {
        "/loans/{id}": {
            "delete": {"deprecated": True, "responses": {"204": {"description": "gone"}}},
        }
    }
    env = _envelope(
        [_resource("Clients", good), _resource("Loans", poor)],
        timestamp="2026-02-01T00:00:00+00:00",
        tenant="demo.example.com",
    )
    model = compute_scorecard(env)
    rendered = render_scorecard(model)

    golden_path = os.path.join(os.path.dirname(__file__), "golden", "scorecard_sample.md")
    expected = open(golden_path, encoding="utf-8").read()
    assert rendered == expected
