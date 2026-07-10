"""
P1 gate: aiohttp_socks should be removed from the primary Tor/I2P pool managers.

Issue #9: Verifies that connection_pool_manager.py and i2p_transport.py
(the two files explicitly listed in the issue) no longer import or use aiohttp_socks.

Note: Comments and docstrings mentioning aiohttp_socks in the context of
migration documentation are acceptable — they document what was replaced.
"""

import ast
from pathlib import Path

import pytest


def _find_aiohttp_socks_in_code(file_path: Path) -> list[str]:
    """
    Find actual aiohttp_socks usage in Python file (not docstrings/comments).

    Returns list of problem lines with their numbers.
    """
    content = file_path.read_text()

    # Parse the AST to find non-docstring/comment references
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [f"  PARSE ERROR: cannot analyze {file_path}"]

    problems = []

    # Check module docstring first
    docstring = ast.get_docstring(tree)
    if docstring and 'aiohttp_socks' in docstring:
        # Docstring mentions are OK (migration docs)
        pass

    # Walk the AST looking for aiohttp_socks in actual code
    for node in ast.walk(tree):
        # Check for imports: import aiohttp_socks, from x import aiohttp_socks
        if isinstance(node, ast.Import):
            for alias in node.names:
                if 'aiohttp_socks' in alias.name:
                    problems.append(f"  import: {ast.unparse(node)}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and 'aiohttp_socks' in node.module:
                problems.append(f"  import from: {ast.unparse(node)}")
        # Check for attribute access: aiohttp_socks.ProxyConnector
        elif isinstance(node, ast.Attribute):
            if node.attr == 'aiohttp_socks':
                problems.append(f"  attribute access: {ast.unparse(node)}")
        # Check for name references
        elif isinstance(node, ast.Name):
            if node.id == 'aiohttp_socks':
                problems.append(f"  name ref: {ast.unparse(node)}")

    return problems


def test_connection_pool_manager_no_code_aiohttp_socks() -> None:
    """
    P1 gate: connection_pool_manager.py must not use aiohttp_socks in code.

    F3XX: Migrated from aiohttp_socks.ProxyConnector to
    httpx_socks.AsyncProxyTransport in TorConnectionPool and I2PConnectionPool.
    """
    repo_root = Path(__file__).parent.parent
    file_path = repo_root / "transport" / "connection_pool_manager.py"
    problems = _find_aiohttp_socks_in_code(file_path)

    assert not problems, (
        f"connection_pool_manager.py uses aiohttp_socks in code:\n"
        + "\n".join(problems)
        + "\n\nF3XX: Use httpx_socks.AsyncProxyTransport.from_url() instead."
    )


def test_i2p_transport_no_code_aiohttp_socks() -> None:
    """
    P1 gate: i2p_transport.py must not use aiohttp_socks in code.

    F3XX: Top-level import removed. The module now uses httpx + httpx-socks only.
    """
    repo_root = Path(__file__).parent.parent
    file_path = repo_root / "transport" / "i2p_transport.py"
    problems = _find_aiohttp_socks_in_code(file_path)

    assert not problems, (
        f"i2p_transport.py uses aiohttp_socks in code:\n"
        + "\n".join(problems)
        + "\n\nF3XX: Use httpx + httpx-socks only (see tor_transport.py reference impl)."
    )


def test_httpx_socks_available() -> None:
    """
    Verify httpx-socks is importable (required for Tor/I2P after migration).
    """
    try:
        import httpx_socks  # noqa: F401
    except ImportError:
        pytest.fail(
            "httpx-socks not installed — required for Tor/I2P after F3XX migration. "
            "Install with: uv add httpx-socks"
        )


def test_pyproject_toml_no_aiohttp_socks_dep() -> None:
    """
    P1 gate: pyproject.toml must not list aiohttp-socks as a dependency.

    F3XX: aiohttp-socks removed. Tor/I2P now use httpx-socks exclusively.
    """
    import subprocess
    import sys

    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        ["grep", "-q", "aiohttp-socks", str(repo_root / "pyproject.toml")],
    )
    # grep -q returns 0 when match found, 1 when no match
    assert result.returncode == 1, (
        "pyproject.toml still lists 'aiohttp-socks' as a dependency.\n"
        "F3XX: Remove aiohttp-socks from pyproject.toml dependencies."
    )
