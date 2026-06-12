"""
Subprocess tests verifying the mambu_openapi_fetcher.py shim still works
as a CLI entry point and that public symbols are importable from the shim.
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, **kwargs):
    return subprocess.run(
        [sys.executable] + args,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        **kwargs,
    )


def test_shim_help_exits_zero():
    """Running the shim with --help should exit 0."""
    result = _run(["mambu_openapi_fetcher.py", "--help"])
    assert result.returncode == 0
    assert "Mambu" in result.stdout or "tenant" in result.stdout


def test_shim_imports_derive_openapi_path():
    """derive_openapi_path is importable from the shim and works correctly."""
    result = _run([
        "-c",
        (
            "from mambu_openapi_fetcher import derive_openapi_path; "
            "assert derive_openapi_path('json/clients_v2_swagger.json') == 'openapi/resources/clients/v2'"
        ),
    ])
    assert result.returncode == 0, result.stderr


def test_shim_imports_count_endpoints():
    """count_endpoints is importable from the shim and works correctly."""
    result = _run([
        "-c",
        (
            "from mambu_openapi_fetcher import count_endpoints; "
            "spec = {'paths': {'/x': {'get': {}, 'post': {}}}}; "
            "assert count_endpoints(spec) == 2"
        ),
    ])
    assert result.returncode == 0, result.stderr


def test_shim_imports_parse_auth():
    """parse_auth is importable from the shim and raises on bad input."""
    result = _run([
        "-c",
        (
            "from mambu_openapi_fetcher import parse_auth; "
            "assert parse_auth('u:p') == ('u', 'p')"
        ),
    ])
    assert result.returncode == 0, result.stderr
