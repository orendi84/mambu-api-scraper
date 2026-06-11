"""
Pure-function tests for mambu_openapi_fetcher.
No network calls, no mocking of requests.
"""
import json
import os
import sys
import tempfile

import pytest

# Ensure the repo root is on the path so we can import the fetcher
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mambu_openapi_fetcher import (
    derive_openapi_path,
    resolve_ref,
    count_endpoints,
    schema_to_markdown,
    parse_auth,
    github_anchor,
    build_markdown,
    spec_to_markdown,
    md_cell,
    slugify_filename,
    filter_resources,
    load_saved_output,
)


# ---------------------------------------------------------------------------
# derive_openapi_path
# ---------------------------------------------------------------------------

def test_derive_openapi_path_v2():
    assert derive_openapi_path("json/clients_v2_swagger.json") == "openapi/resources/clients/v2"


def test_derive_openapi_path_v1():
    assert derive_openapi_path("json/loans_v1_swagger.json") == "openapi/resources/loans/v1"


def test_derive_openapi_path_nonmatching_returns_none():
    assert derive_openapi_path("swagger/something_else.json") is None


def test_derive_openapi_path_empty_returns_none():
    assert derive_openapi_path("") is None


def test_derive_openapi_path_no_version_returns_none():
    # Missing _vN_ segment
    assert derive_openapi_path("json/clients_swagger.json") is None


# ---------------------------------------------------------------------------
# resolve_ref
# ---------------------------------------------------------------------------

def _simple_spec():
    return {
        "components": {
            "schemas": {
                "Client": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                    },
                }
            }
        }
    }


def test_resolve_ref_simple():
    spec = _simple_spec()
    result = resolve_ref(spec, "#/components/schemas/Client")
    assert result == {"type": "object", "properties": {"id": {"type": "string"}}}


def test_resolve_ref_missing_target_returns_none():
    spec = _simple_spec()
    assert resolve_ref(spec, "#/components/schemas/NoSuchSchema") is None


def test_resolve_ref_non_dict_traversal_returns_none():
    spec = {"info": "just a string"}
    assert resolve_ref(spec, "#/info/title") is None


def test_resolve_ref_no_hash_prefix_returns_none():
    assert resolve_ref({}, "components/schemas/Foo") is None


def test_resolve_ref_empty_returns_none():
    assert resolve_ref({}, "") is None


def test_resolve_ref_escaped_tilde1():
    # ~1 should be decoded to /
    spec = {"paths": {"/foo/bar": {"type": "path-object"}}}
    result = resolve_ref(spec, "#/paths/~1foo~1bar")
    assert result == {"type": "path-object"}


def test_resolve_ref_escaped_tilde0():
    # ~0 should be decoded to ~
    spec = {"keys": {"a~b": "value"}}
    result = resolve_ref(spec, "#/keys/a~0b")
    assert result == "value"


def test_resolve_ref_through_list_index():
    spec = {"items": [{"name": "first"}, {"name": "second"}]}
    result = resolve_ref(spec, "#/items/1")
    assert result == {"name": "second"}


def test_resolve_ref_list_index_out_of_range_returns_none():
    spec = {"items": [{"name": "first"}]}
    assert resolve_ref(spec, "#/items/99") is None


def test_resolve_ref_list_non_integer_index_returns_none():
    spec = {"items": [{"name": "first"}]}
    assert resolve_ref(spec, "#/items/notanint") is None


# ---------------------------------------------------------------------------
# count_endpoints
# ---------------------------------------------------------------------------

def test_count_endpoints_normal():
    spec = {
        "paths": {
            "/clients": {"get": {}, "post": {}},
            "/clients/{id}": {"get": {}, "delete": {}},
        }
    }
    assert count_endpoints(spec) == 4


def test_count_endpoints_path_level_parameters_key_ignored():
    # "parameters" is a valid path-level key in OpenAPI but not an HTTP method
    spec = {
        "paths": {
            "/clients": {
                "parameters": [{"name": "X-Tenant"}],
                "get": {},
            }
        }
    }
    assert count_endpoints(spec) == 1


def test_count_endpoints_malformed_non_dict_methods_skipped():
    # A path whose value is not a dict (malformed) is skipped
    spec = {
        "paths": {
            "/clients": "not-a-dict",
            "/loans": {"get": {}},
        }
    }
    assert count_endpoints(spec) == 1


def test_count_endpoints_non_dict_method_value_skipped():
    # Method value is not a dict - should be skipped, not counted or blown up
    spec = {
        "paths": {
            "/clients": {
                "get": "not-a-dict",
                "post": {},
            }
        }
    }
    assert count_endpoints(spec) == 1


def test_count_endpoints_no_paths():
    assert count_endpoints({}) == 0


# ---------------------------------------------------------------------------
# schema_to_markdown
# ---------------------------------------------------------------------------

def _schema_spec():
    return {
        "components": {
            "schemas": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "zip": {"type": "string"},
                    },
                    "required": ["city"],
                }
            }
        }
    }


def test_schema_to_markdown_property_table():
    spec = _schema_spec()
    schema = spec["components"]["schemas"]["Address"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "| Property |" in combined
    assert "city" in combined
    assert "zip" in combined


def test_schema_to_markdown_required_flag():
    spec = _schema_spec()
    schema = spec["components"]["schemas"]["Address"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    # city is required, zip is not
    city_row = [l for l in lines if "city" in l and "|" in l][0]
    zip_row = [l for l in lines if "zip" in l and "|" in l][0]
    assert "Yes" in city_row
    assert "No" in zip_row


def test_schema_to_markdown_enum_in_description():
    spec = {}
    schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["ACTIVE", "INACTIVE"],
            }
        },
    }
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "ACTIVE" in combined
    assert "INACTIVE" in combined
    assert "Enum:" in combined


def test_schema_to_markdown_example_in_description():
    spec = {}
    schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "example": "ABC123",
            }
        },
    }
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "ABC123" in combined
    assert "Example:" in combined


def test_schema_to_markdown_circular_ref():
    # The circular-reference guard fires when the top-level schema is a $ref
    # and the ref name is already in the `seen` set.
    spec = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                }
            }
        }
    }
    # Simulate calling with a $ref schema where Node is already in seen
    ref_schema = {"$ref": "#/components/schemas/Node"}
    lines = schema_to_markdown(ref_schema, spec, seen={"Node"})
    combined = "\n".join(lines)
    assert "circular reference" in combined


def test_schema_to_markdown_array_of_ref():
    spec = {
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            }
        }
    }
    schema = {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "Array of:" in combined
    assert "name" in combined


# ---------------------------------------------------------------------------
# parse_auth
# ---------------------------------------------------------------------------

def test_parse_auth_valid():
    assert parse_auth("user:password") == ("user", "password")


def test_parse_auth_password_contains_colon():
    # Only the first colon splits
    assert parse_auth("user:pass:word") == ("user", "pass:word")


def test_parse_auth_missing_colon_raises():
    with pytest.raises(ValueError, match="user:password"):
        parse_auth("useronly")


# ---------------------------------------------------------------------------
# github_anchor
# ---------------------------------------------------------------------------

def test_github_anchor_basic():
    seen = {}
    assert github_anchor("Clients", seen) == "clients"


def test_github_anchor_spaces_to_hyphens():
    seen = {}
    assert github_anchor("Loan Accounts", seen) == "loan-accounts"


def test_github_anchor_special_chars_stripped():
    seen = {}
    # parentheses and slashes should be stripped
    slug = github_anchor("GET /clients/{id} (v2)", seen)
    assert "(" not in slug
    assert ")" not in slug
    assert "/" not in slug


def test_github_anchor_dedupe_first_repeat():
    seen = {}
    github_anchor("Clients", seen)
    second = github_anchor("Clients", seen)
    assert second == "clients-1"


def test_github_anchor_dedupe_second_repeat():
    seen = {}
    github_anchor("Clients", seen)
    github_anchor("Clients", seen)
    third = github_anchor("Clients", seen)
    assert third == "clients-2"


def test_github_anchor_independent_labels():
    seen = {}
    a = github_anchor("Clients", seen)
    b = github_anchor("Loans", seen)
    assert a == "clients"
    assert b == "loans"


# ---------------------------------------------------------------------------
# TOC anchors match section headings
# ---------------------------------------------------------------------------

def test_build_markdown_toc_anchors_match_headings():
    """TOC links must resolve to the ## headings generated by spec_to_markdown."""
    labels_and_specs = [
        ("Clients", {"paths": {}}),
        ("Loan/Accounts", {"paths": {}}),
        ("Clients", {"paths": {}}),  # duplicate
        ("Events (v2)", {"paths": {}}),
    ]

    output = {
        "timestamp": "2024-01-01T00:00:00Z",
        "tenant": "test.mambu.com",
        "resources_total": len(labels_and_specs),
        "endpoints_total": 0,
    }

    md = build_markdown(output, labels_and_specs)
    lines = md.split("\n")

    # Collect TOC anchor hrefs
    toc_anchors = set()
    for line in lines:
        if line.startswith("- [") and "](#" in line:
            anchor = line.split("](#")[1].rstrip(")")
            toc_anchors.add(anchor)

    # Collect heading slugs the way GitHub renders ## headings
    # GitHub lowercases, strips non-alphanumeric-space-hyphen, replaces spaces with hyphens
    heading_slugs = set()
    seen = {}
    for line in lines:
        if line.startswith("## ") and line != "## Table of Contents":
            label = line[3:]
            slug = github_anchor(label, seen)
            heading_slugs.add(slug)

    # Every TOC anchor must have a matching heading slug
    missing = toc_anchors - heading_slugs
    assert not missing, f"TOC anchors with no matching heading: {missing}"


# ---------------------------------------------------------------------------
# md_cell
# ---------------------------------------------------------------------------

def test_md_cell_plain_string():
    assert md_cell("hello") == "hello"


def test_md_cell_pipe_escaped():
    assert md_cell("a|b") == "a\\|b"


def test_md_cell_newline_replaced():
    assert md_cell("line1\nline2") == "line1 line2"


def test_md_cell_multiple_pipes():
    assert md_cell("a|b|c") == "a\\|b\\|c"


def test_md_cell_non_string_coerced():
    assert md_cell(42) == "42"
    assert md_cell(None) == "None"
    assert md_cell(3.14) == "3.14"


def test_md_cell_pipe_and_newline_combined():
    assert md_cell("a|b\nc|d") == "a\\|b c\\|d"


# ---------------------------------------------------------------------------
# slugify_filename
# ---------------------------------------------------------------------------

def test_slugify_filename_basic():
    seen = {}
    assert slugify_filename("Clients", seen) == "clients"


def test_slugify_filename_spaces_to_hyphens():
    seen = {}
    assert slugify_filename("Loan Accounts", seen) == "loan-accounts"


def test_slugify_filename_special_chars_to_hyphens():
    seen = {}
    slug = slugify_filename("GET /clients/{id}", seen)
    assert "/" not in slug
    assert "{" not in slug
    assert "}" not in slug


def test_slugify_filename_punctuation_only_becomes_resource():
    # A label that is purely punctuation collapses to empty then falls back to "resource"
    seen = {}
    slug = slugify_filename("!@#$%", seen)
    assert slug == "resource"


def test_slugify_filename_dedup_collision():
    # Two labels that differ only by punctuation should collide and get -1 suffix
    seen = {}
    first = slugify_filename("Foo Bar", seen)   # -> "foo-bar"
    second = slugify_filename("Foo-Bar", seen)  # -> "foo-bar" -> collision -> "foo-bar-1"
    assert first == "foo-bar"
    assert second == "foo-bar-1"


def test_slugify_filename_dedup_three():
    seen = {}
    a = slugify_filename("Clients", seen)
    b = slugify_filename("Clients", seen)
    c = slugify_filename("Clients", seen)
    assert a == "clients"
    assert b == "clients-1"
    assert c == "clients-2"


def test_slugify_filename_numeric_label():
    seen = {}
    slug = slugify_filename("API v2", seen)
    assert slug == "api-v2"


# ---------------------------------------------------------------------------
# allOf merge in schema_to_markdown
# ---------------------------------------------------------------------------

def _allof_spec():
    return {
        "components": {
            "schemas": {
                "Base": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique id"},
                        "created": {"type": "string"},
                    },
                    "required": ["id"],
                },
                "Extended": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {
                            "type": "object",
                            "properties": {
                                "extra": {"type": "integer", "description": "Extra field"},
                            },
                            "required": ["extra"],
                        },
                    ],
                    "properties": {
                        "local": {"type": "boolean", "description": "Local sibling field"},
                    },
                    "required": ["local"],
                },
            }
        }
    }


def test_allof_merge_includes_base_properties():
    spec = _allof_spec()
    schema = spec["components"]["schemas"]["Extended"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "id" in combined
    assert "created" in combined


def test_allof_merge_includes_allof_sub_properties():
    spec = _allof_spec()
    schema = spec["components"]["schemas"]["Extended"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "extra" in combined


def test_allof_merge_includes_sibling_properties():
    # Sibling properties (outside allOf) must not be dropped
    spec = _allof_spec()
    schema = spec["components"]["schemas"]["Extended"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "local" in combined


def test_allof_merge_required_union():
    spec = _allof_spec()
    schema = spec["components"]["schemas"]["Extended"]
    lines = schema_to_markdown(schema, spec)
    # "id" from Base, "extra" from inline sub, "local" from sibling - all should be Yes
    id_row = [l for l in lines if "| id " in l or l.startswith("| id |")][0]
    extra_row = [l for l in lines if "| extra " in l or l.startswith("| extra |")][0]
    local_row = [l for l in lines if "| local " in l or l.startswith("| local |")][0]
    assert "Yes" in id_row
    assert "Yes" in extra_row
    assert "Yes" in local_row


def test_allof_no_ref_sub_schemas():
    """allOf with only inline sub-schemas (no $ref) should still merge."""
    spec = {}
    schema = {
        "allOf": [
            {"properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"properties": {"b": {"type": "integer"}}},
        ]
    }
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "a" in combined
    assert "b" in combined


def test_allof_circular_ref_skipped():
    """A circular $ref inside allOf should be skipped gracefully."""
    spec = {
        "components": {
            "schemas": {
                "Self": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Self"},
                        {"properties": {"name": {"type": "string"}}},
                    ]
                }
            }
        }
    }
    schema = spec["components"]["schemas"]["Self"]
    # Pass "Self" in seen to simulate circular detection
    lines = schema_to_markdown(schema, spec, seen={"Self"})
    # Should not raise and should render the non-circular part
    combined = "\n".join(lines)
    assert "name" in combined


def test_allof_circular_ref_terminates_without_preseeding():
    """A self-referencing allOf must terminate even with an empty seen set."""
    spec = {
        "components": {
            "schemas": {
                "Self": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Self"},
                        {"properties": {"name": {"type": "string"}}},
                    ]
                }
            }
        }
    }
    schema = spec["components"]["schemas"]["Self"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "name" in combined


def test_allof_nested_allof_properties_not_dropped():
    """Properties inherited through a nested allOf chain must survive the merge."""
    spec = {
        "components": {
            "schemas": {
                "Base": {
                    "allOf": [
                        {"properties": {"id": {"type": "string"}}, "required": ["id"]},
                    ]
                },
                "Ext": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {"properties": {"name": {"type": "string"}}},
                    ]
                },
            }
        }
    }
    schema = spec["components"]["schemas"]["Ext"]
    lines = schema_to_markdown(schema, spec)
    combined = "\n".join(lines)
    assert "id" in combined
    assert "name" in combined
    id_row = [l for l in lines if l.startswith("| id ")][0]
    assert "Yes" in id_row


# ---------------------------------------------------------------------------
# Response code integer key coercion
# ---------------------------------------------------------------------------

def test_spec_to_markdown_integer_response_code_no_crash():
    """spec_to_markdown must not crash when responses dict has integer keys."""
    spec = {
        "paths": {
            "/test": {
                "get": {
                    "summary": "Test endpoint",
                    "responses": {
                        200: {"description": "OK"},
                        404: {"description": "Not found"},
                    },
                }
            }
        }
    }
    md = spec_to_markdown("Test", spec)
    assert "200" in md
    assert "404" in md
    assert "OK" in md


def test_spec_to_markdown_integer_200_shows_schema():
    """Integer 200 key should be treated as a success code for schema rendering."""
    spec = {
        "components": {
            "schemas": {
                "MyObj": {
                    "type": "object",
                    "properties": {"field": {"type": "string"}},
                }
            }
        },
        "paths": {
            "/test": {
                "get": {
                    "responses": {
                        200: {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/MyObj"}
                                }
                            },
                        }
                    }
                }
            }
        },
    }
    md = spec_to_markdown("Test", spec)
    assert "field" in md


# ---------------------------------------------------------------------------
# filter_resources
# ---------------------------------------------------------------------------

def _sample_resources():
    return [
        {"label": "Clients", "jsonPath": "json/clients_v2_swagger.json"},
        {"label": "Loan Accounts", "jsonPath": "json/loanaccounts_v2_swagger.json"},
        {"label": "Deposits", "jsonPath": "json/deposits_v1_swagger.json"},
        {"label": "Credit Arrangements", "jsonPath": "json/creditarrangements_v2_swagger.json"},
    ]


def test_filter_resources_single_match():
    resources = _sample_resources()
    matched, _ = filter_resources(resources, "clients")
    assert len(matched) == 1
    assert matched[0]["label"] == "Clients"


def test_filter_resources_case_insensitive():
    resources = _sample_resources()
    matched, _ = filter_resources(resources, "LOAN")
    assert len(matched) == 1
    assert matched[0]["label"] == "Loan Accounts"


def test_filter_resources_multiple_csv():
    resources = _sample_resources()
    matched, _ = filter_resources(resources, "clients,deposits")
    labels = [r["label"] for r in matched]
    assert "Clients" in labels
    assert "Deposits" in labels
    assert len(matched) == 2


def test_filter_resources_substring_match():
    resources = _sample_resources()
    matched, _ = filter_resources(resources, "credit")
    assert len(matched) == 1
    assert matched[0]["label"] == "Credit Arrangements"


def test_filter_resources_no_match_returns_empty():
    resources = _sample_resources()
    matched, available = filter_resources(resources, "nonexistent")
    assert matched == []
    assert len(available) == 4


def test_filter_resources_returns_available_labels():
    resources = _sample_resources()
    _, available = filter_resources(resources, "clients")
    assert "Clients" in available
    assert "Deposits" in available


def test_filter_resources_preserves_order():
    resources = _sample_resources()
    matched, _ = filter_resources(resources, "loan,clients")
    # Should be in original order: Clients first, then Loan Accounts
    assert matched[0]["label"] == "Clients"
    assert matched[1]["label"] == "Loan Accounts"


# ---------------------------------------------------------------------------
# load_saved_output
# ---------------------------------------------------------------------------

def _make_saved_json(tmp_path, resources=None, extra_fields=None):
    """Write a minimal saved output JSON and return its path."""
    data = {
        "timestamp": "2026-04-02T15:58:45Z",
        "tenant": "test.mambu.com",
        "resources_total": 2,
        "resources_failed": 0,
        "endpoints_total": 5,
        "resources": resources or [
            {
                "label": "Clients",
                "json_path": "json/clients_v2_swagger.json",
                "endpoints": 3,
                "spec": {
                    "openapi": "3.0.0",
                    "info": {"title": "Clients", "version": "v2"},
                    "paths": {
                        "/clients": {
                            "get": {"summary": "List clients", "responses": {"200": {"description": "OK"}}},
                            "post": {"summary": "Create client", "responses": {"201": {"description": "Created"}}},
                        },
                        "/clients/{id}": {
                            "get": {"summary": "Get client", "responses": {"200": {"description": "OK"}}},
                        },
                    },
                },
            },
            {
                "label": "Deposits",
                "json_path": "json/deposits_v1_swagger.json",
                "endpoints": 2,
                "spec": {
                    "openapi": "3.0.0",
                    "info": {"title": "Deposits", "version": "v1"},
                    "paths": {
                        "/deposits": {
                            "get": {"summary": "List deposits", "responses": {"200": {"description": "OK"}}},
                            "post": {"summary": "Create deposit", "responses": {"201": {"description": "Created"}}},
                        },
                    },
                },
            },
        ],
    }
    if extra_fields:
        data.update(extra_fields)
    p = tmp_path / "saved_output.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_load_saved_output_basic(tmp_path):
    path = _make_saved_json(tmp_path)
    data = load_saved_output(path)
    assert "resources" in data
    assert len(data["resources"]) == 2
    assert data["tenant"] == "test.mambu.com"


def test_load_saved_output_recomputes_endpoint_count(tmp_path):
    """Verify count_endpoints on loaded specs gives correct totals."""
    from mambu_openapi_fetcher import count_endpoints
    path = _make_saved_json(tmp_path)
    data = load_saved_output(path)
    total = sum(count_endpoints(r["spec"]) for r in data["resources"])
    assert total == 5  # 3 from Clients, 2 from Deposits


def test_load_saved_output_missing_resources_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"tenant": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'resources'"):
        load_saved_output(str(p))


def test_load_saved_output_non_list_resources_raises(tmp_path):
    p = tmp_path / "bad2.json"
    p.write_text(json.dumps({"resources": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        load_saved_output(str(p))


def test_load_saved_output_non_object_raises(tmp_path):
    p = tmp_path / "bad3.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="Expected a JSON object"):
        load_saved_output(str(p))


def test_load_saved_output_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        load_saved_output(str(tmp_path / "nonexistent.json"))
