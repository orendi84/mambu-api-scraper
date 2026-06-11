"""
Pure-function tests for mambu_openapi_fetcher.
No network calls, no mocking of requests.
"""
import pytest
import sys
import os

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
