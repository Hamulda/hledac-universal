"""
P1 gate: aiohttp_socks must not be imported anywhere in the codebase.

Issue #C5: Verifies that no Python file performs:
  - import aiohttp_socks
  - from aiohttp_socks import ...
  - from x import aiohttp_socks

Comments and docstrings mentioning aiohttp_socks in the context of
migration documentation are acceptable — they document what was replaced.
"""

import ast
import os
from pathlib import Path

import pytest
from core import aclose


def _find_aiohttp_socks_imports(file_path: Path) -> list[str]:
    """
    Find actual aiohttp_socks import statements in a Python file (not docstrings/comments).

    Returns list of problem lines with their numbers.
    """
    content = file_path.read_text()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Skip files with syntax errors — they can't be analyzed, but also can't
        # contain valid aiohttp_socks imports (syntax errors prevent execution).
        return []

    problems = []

    for node in ast.walk(tree):
        # import aiohttp_socks
        if isinstance(node, ast.Import):
            for alias in node.names:
                if 'aiohttp_socks' in alias.name:
                    problems.append(f"  import: {ast.unparse(node)}")
        # from x import aiohttp_socks, from aiohttp_socks import x
        elif isinstance(node, ast.ImportFrom):
            if node.module and 'aiohttp_socks' in node.module:
                problems.append(f"  import from: {ast.unparse(node)}")
            for alias in node.names:
                if 'aiohttp_socks' in alias.name:
                    problems.append(f"  import from: {ast.unparse(node)}")

    return problems


def _iter_python_files(repo_root: Path) -> list[Path]:
    """Iterate all Python files in the project, excluding test/app fixtures."""
    paths = []
    # Exclude common non-source directories
    exclude_dirs = {
        '__pycache__', '.pytest_cache', '.mypy_cache',
        '.venv', '.git', 'node_modules', 'htmlcov',
        '.tox', 'build', 'dist', '.egg-info',
        '.claude', '.vtcode', 'archive',
    }
    # Use os.walk to avoid following symlinks (rglob can traverse outside project via symlinks)
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith('.')]
        for fname in filenames:
            if fname.endswith('.py'):
                p = Path(dirpath) / fname
                if 'probe_' not in p.name and not p.name.startswith('test_imports'):
                    paths.append(p)
    return sorted(paths)


def test_no_aiohttp_socks_anywhere() -> None:
    """
    P1 gate: no Python file in the project may import aiohttp_socks.

    F4XX/C5: aiohttp-socks removed from dependencies. Tor/I2P now use
    httpx-socks exclusively. Any remaining references are dead code/docstrings.
    """
    repo_root = Path(__file__).parent.parent
    py_files = _iter_python_files(repo_root)

    failures = []
    for pf in py_files:
        problems = _find_aiohttp_socks_imports(pf)
        if problems:
            failures.append(f"{pf.relative_to(repo_root)}:\n" + "\n".join(problems))

    assert not failures, (
        "The following files still import aiohttp_socks:\n\n"
        + "\n\n".join(failures)
        + "\n\nF4XX/C5: Use httpx + httpx-socks only."
    )


def test_pyproject_toml_no_aiohttp_socks_dep() -> None:
    """
    P1 gate: pyproject.toml must not list aiohttp-socks as a dependency.

    F4XX/C5: aiohttp-socks removed. Tor/I2P now use httpx-socks exclusively.
    """
    repo_root = Path(__file__).parent.parent
    toml_path = repo_root / "pyproject.toml"
    content = toml_path.read_text()

    # Check for any aiohttp-socks reference (but not in comments)
    lines = content.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if 'aiohttp-socks' in stripped or 'aiohttp_socks' in stripped:
            pytest.fail(
                f"pyproject.toml still references 'aiohttp-socks': {line.strip()}\n"
                "F4XX/C5: Remove aiohttp-socks from pyproject.toml dependencies."
            )


def test_httpx_socks_available() -> None:
    """
    Verify httpx-socks is importable (required for Tor/I2P after migration).
    """
    try:
        import httpx_socks  # noqa: F401
    except ImportError:
        pytest.fail(
            "httpx-socks not installed — required for Tor/I2P after F4XX/C5 migration. "
            "Install with: uv add httpx-socks"
        )
