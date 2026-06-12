"""Tests for the merged OpenAPI document builder."""
import os
import sys

from openapi_spec_validator import validate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mambu_api_tools.merge import build_merged_spec

TS = "20260101_000000"
TENANT = "demo.example.com"


# ---------------------------------------------------------------------------
# shared identical component name -> not renamed
# ---------------------------------------------------------------------------

def test_identical_schema_name_is_shared():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    spec_a = {
        "openapi": "3.0.3",
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"Common": schema}},
    }
    spec_b = {
        "openapi": "3.0.3",
        "paths": {"/b": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"Common": dict(schema)}},
    }
    merged = build_merged_spec([("A", spec_a), ("B", spec_b)], TENANT, TS)
    schemas = merged["components"]["schemas"]
    assert "Common" in schemas
    assert len(schemas) == 1  # shared, not duplicated


# ---------------------------------------------------------------------------
# conflicting definitions -> renamed with prefix + all refs rewritten
# ---------------------------------------------------------------------------

def test_conflicting_schema_renamed_and_refs_rewritten():
    spec_a = {
        "openapi": "3.0.3",
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"Money": {"type": "object", "properties": {"amount": {"type": "number"}}}}},
    }
    # spec_b has a different Money definition, referenced from many nested locations.
    spec_b = {
        "openapi": "3.0.3",
        "paths": {
            "/b": {
                "post": {
                    "parameters": [
                        {"name": "p", "in": "query", "schema": {"$ref": "#/components/schemas/Money"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {"$ref": "#/components/schemas/Money"},
                                        {"type": "object"},
                                    ],
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Money"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Money": {"type": "object", "properties": {"value": {"type": "integer"}}},
            }
        },
    }
    merged = build_merged_spec([("Loan Accounts (v2)", spec_a), ("Billing", spec_b)], TENANT, TS)
    schemas = merged["components"]["schemas"]
    # Original Money kept from spec_a; spec_b's renamed to Billing_Money.
    assert "Money" in schemas
    assert "Billing_Money" in schemas
    assert schemas["Billing_Money"]["properties"] == {"value": {"type": "integer"}}

    op = merged["paths"]["/b"]["post"]
    # parameter schema ref rewritten
    assert op["parameters"][0]["schema"]["$ref"] == "#/components/schemas/Billing_Money"
    # allOf ref rewritten
    allof = op["requestBody"]["content"]["application/json"]["schema"]["allOf"]
    assert allof[0]["$ref"] == "#/components/schemas/Billing_Money"
    # array items ref rewritten
    items = op["responses"]["200"]["content"]["application/json"]["schema"]["items"]
    assert items["$ref"] == "#/components/schemas/Billing_Money"


# ---------------------------------------------------------------------------
# component name with "/" -> JSON-pointer escaping exercised
# ---------------------------------------------------------------------------

def test_component_name_with_slash_escaping():
    spec_a = {
        "openapi": "3.0.3",
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"a/b": {"type": "string", "maxLength": 1}}},
    }
    spec_b = {
        "openapi": "3.0.3",
        "paths": {
            "/b": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/a~1b"}}
                            },
                        }
                    }
                }
            }
        },
        "components": {"schemas": {"a/b": {"type": "string", "maxLength": 99}}},
    }
    merged = build_merged_spec([("First", spec_a), ("Second", spec_b)], TENANT, TS)
    schemas = merged["components"]["schemas"]
    assert "a/b" in schemas
    assert "Second_a/b" in schemas
    ref = merged["paths"]["/b"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    # Escaped pointer for the renamed name
    assert ref == "#/components/schemas/Second_a~1b"


# ---------------------------------------------------------------------------
# path collision -> first wins + x-merge-warnings entry
# ---------------------------------------------------------------------------

def test_path_collision_first_wins_with_warning():
    spec_a = {
        "openapi": "3.0.3",
        "paths": {"/shared": {"get": {"summary": "from A", "responses": {"200": {"description": "ok"}}}}},
    }
    spec_b = {
        "openapi": "3.0.3",
        "paths": {"/shared": {"get": {"summary": "from B", "responses": {"200": {"description": "ok"}}}}},
    }
    merged = build_merged_spec([("A", spec_a), ("B", spec_b)], TENANT, TS)
    assert merged["paths"]["/shared"]["get"]["summary"] == "from A"
    warnings = merged["info"]["x-merge-warnings"]
    assert len(warnings) == 1
    assert "/shared" in warnings[0]
    assert "B" in warnings[0]


# ---------------------------------------------------------------------------
# no collision -> no x-merge-warnings key
# ---------------------------------------------------------------------------

def test_no_collision_no_warnings_key():
    spec_a = {
        "openapi": "3.0.3",
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    spec_b = {
        "openapi": "3.0.3",
        "paths": {"/b": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    merged = build_merged_spec([("A", spec_a), ("B", spec_b)], TENANT, TS)
    assert "x-merge-warnings" not in merged["info"]


# ---------------------------------------------------------------------------
# info block + servers + x-merged-from
# ---------------------------------------------------------------------------

def test_info_and_servers():
    spec_a = {"openapi": "3.0.3", "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    merged = build_merged_spec([("Clients", spec_a)], TENANT, TS)
    assert merged["info"]["title"] == "Mambu API (merged)"
    assert merged["info"]["version"] == TS
    assert TENANT in merged["info"]["description"]
    assert merged["info"]["x-merged-from"] == ["Clients"]
    assert merged["servers"] == [{"url": f"https://{TENANT}/api"}]


# ---------------------------------------------------------------------------
# validation: a representative merged document must pass openapi_spec_validator
# ---------------------------------------------------------------------------

def test_merged_document_validates():
    spec_a = {
        "openapi": "3.0.3",
        "info": {"title": "Clients", "version": "1"},
        "paths": {
            "/clients": {
                "get": {
                    "summary": "List clients",
                    "responses": {
                        "200": {
                            "description": "A list of clients",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Client"}}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Client": {"type": "object", "properties": {"id": {"type": "string"}}},
            }
        },
    }
    spec_b = {
        "openapi": "3.0.3",
        "info": {"title": "Loans", "version": "1"},
        "paths": {
            "/loans": {
                "post": {
                    "summary": "Create a loan",
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Client"}}}
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
        "components": {
            "schemas": {
                # Conflicting Client definition -> forces a rename + ref rewrite
                "Client": {"type": "object", "properties": {"loanId": {"type": "string"}}},
            }
        },
    }
    merged = build_merged_spec([("Clients", spec_a), ("Loans", spec_b)], TENANT, TS)
    # The renamed ref must resolve to a real component for validation to pass.
    assert "Loans_Client" in merged["components"]["schemas"]
    rb_ref = merged["paths"]["/loans"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert rb_ref == "#/components/schemas/Loans_Client"
    validate(merged)
