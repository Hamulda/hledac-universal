"""
ruff_ext — RUFF022: Banned Import Paths

Custom lint rule for banning dual-namespace / bare-package imports.

Rule ID: RUFF022 (banned-imports)

Banned patterns:
  - from <pkg> (bare package import without hledac.universal prefix)
  - from runtime, from brain, from knowledge, from core, from coordinators
  - from intelligence, from transport, from network, from export, from report
  - from rendering, from layers, from prefetch, from cli

Allowed patterns:
  - from hledac.universal.<pkg> (canonical form)
  - from pathlib, from typing, from asyncio (stdlib)
  - from unittest, from pytest (test-only)
  - Internal aliases: _asyncio, _threading (explicit internal intent)

Installation:
  uv sync --group dev  # installs ruff_ext as local editable package

CI gate:
  python -m ruff_ext --ci
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file: Path
    line: int
    col: int
    name: str
    message: str


# Banned import root modules (bare package imports)
BANNED_ROOTS: frozenset[str] = frozenset(
    {
        # Dual-namespace sources — must use hledac.universal.<pkg>
        "runtime",
        "brain",
        "knowledge",
        "coordinators",
        "intel",
        "intelligence",
        "transport",
        "network",
        "export",
        "report",
        "rendering",
        "layers",
        "prefetch",
        "cli",
        "tools",
        "discovery",
        "federated",
        "security",
        "infrastructure",
        "memory",
        "multimodal",
        "monitoring",
        "evidence_log",
        # Legacy bare packages
        "graph",
        "prefilt",
        "hledac",
        # Also banned: core/utils/recon/fetching/rl — dual-load with hledac.universal.*
        "core",
        "utils",
        "recon",
        "fetching",
        "rl",
    }
)

# Allowed import roots (stdlib, test frameworks, internal)
ALLOWED_ROOTS: frozenset[str] = frozenset(
    {
        # Stdlib
        "asyncio",
        "typing",
        "pathlib",
        "os",
        "sys",
        "re",
        "json",
        "abc",
        "argparse",
        "ast",
        "contextlib",
        "copy",
        "dataclasses",
        "enum",
        "functools",
        "inspect",
        "io",
        "itertools",
        "logging",
        "math",
        "pickle",
        "queue",
        "random",
        "struct",
        "tempfile",
        "threading",
        "time",
        "traceback",
        "types",
        "unittest",
        "warnings",
        "weakref",
        "collections",
        "operator",
        "signal",
        "socket",
        "statistics",
        "string",
        "textwrap",
        "tokenize",
        "tracemalloc",
        "uuid",
        "zipfile",
        # Test
        "pytest",
        "coverage",
        "hypothesis",
        # DNS resolution library (external, not part of project)
        "dns",
        # Internal aliases (explicit internal intent via underscore prefix)
        "_asyncio",
        "_threading",
        "_io",
        "_collections",
    }
)

# Modules that live under hledac.universal but are accessed bare
LEGACY_BARE_REMAP: dict[str, str] = {
    "runtime": "hledac.universal.runtime",
    "brain": "hledac.universal.brain",
    "knowledge": "hledac.universal.knowledge",
    "coordinators": "hledac.universal.coordinators",
    "intel": "hledac.universal.intel",
    "intelligence": "hledac.universal.intelligence",
    "transport": "hledac.universal.transport",
    "network": "hledac.universal.network",
    "export": "hledac.universal.export",
    "report": "hledac.universal.report",
    "rendering": "hledac.universal.rendering",
    "layers": "hledac.universal.layers",
    "prefetch": "hledac.universal.prefetch",
    "cli": "hledac.universal.cli",
    "tools": "hledac.universal.tools",
    "discovery": "hledac.universal.discovery",
    "federated": "hledac.universal.federated",
    "security": "hledac.universal.security",
    "infrastructure": "hledac.universal.infrastructure",
    "memory": "hledac.universal.memory",
    "multimodal": "hledac.universal.multimodal",
    "monitoring": "hledac.universal.monitoring",
    "graph": "hledac.universal.graph",
    "prefilt": "hledac.universal.prefilt",
    "hledac": "hledac.universal.hledac",
    # Also banned: core/utils/recon/fetching/rl — dual-load with hledac.universal.*
    "core": "hledac.universal._core",
    "utils": "hledac.universal.utils",
    "recon": "hledac.universal.recon",
    "fetching": "hledac.universal.fetching",
    "rl": "hledac.universal.rl",
}


def check_file(path: Path) -> list[Violation]:
    """Check a single Python file for banned import patterns."""
    violations = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return violations

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "hledac.universal" or alias.name.startswith("hledac.universal."):
                    continue
                name = alias.name.split(".")[0]
                if name in BANNED_ROOTS and name not in ALLOWED_ROOTS:
                    violations.append(
                        Violation(
                            file=path,
                            line=node.lineno or 0,
                            col=node.col_offset or 0,
                            name=alias.name,
                            message=f"RUFF022 banned bare import: `{alias.name}` "
                            f"(use `{LEGACY_BARE_REMAP.get(alias.name, f'hledac.universal.{alias.name}')}` instead)",
                        )
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import — allowed (intra-package)
                continue
            if node.module is None:
                continue

            module = node.module

            # Canonical form: from hledac.universal.<pkg> import ... — always allowed
            if module == "hledac.universal" or module.startswith("hledac.universal."):
                continue

            root = module.split(".")[0]

            if root in BANNED_ROOTS and root not in ALLOWED_ROOTS:
                for alias in node.names:
                    violations.append(
                        Violation(
                            file=path,
                            line=node.lineno or 0,
                            col=node.col_offset or 0,
                            name=f"{module}.{alias.name}" if alias.name != "*" else module,
                            message=f"RUFF022 banned bare import: `from {module} import {alias.name}` "
                            f"(use `from hledac.universal.{module} import {alias.name}` instead)",
                        )
                    )

    return violations


def check_directory(root: Path, exclude_dirs: frozenset[str] | None = None) -> list[Violation]:
    """Recursively check a directory for banned imports."""
    if exclude_dirs is None:
        exclude_dirs = frozenset(
            {
                "__pycache__",
                ".venv",
                ".venv-test",
                ".git",
                ".claude",
                "archive",
                "tests",
                "benchmarks",
                ".mypy_cache",
                ".pytest_cache",
                "stubs",
                "tools/audit",
            }
        )

    # Substring-based exclusions (prefix / partial path matches)
    EXCLUDE_SUBSTRINGS: frozenset[str] = frozenset(
        {
            "tools/probe/",
            "tools/_archive/",
            "tools/probe_",
            "probe/",  # probe/ subdirectories (e.g. probe/probe_f229g_...)
        }
    )

    all_violations = []
    for py_file in root.rglob("*.py"):
        rel = str(py_file.relative_to(root))
        # Exact part-match exclusions
        if any(excluded in py_file.parts for excluded in exclude_dirs):
            continue
        # Substring exclusions (partial path)
        if any(excluded in rel for excluded in EXCLUDE_SUBSTRINGS):
            continue
        violations = check_file(py_file)
        all_violations.extend(violations)
    return all_violations


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RUFF022: Banned import paths checker")
    parser.add_argument(
        "--root", type=Path, default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
    )
    parser.add_argument("--fix", action="store_true", help="Not yet implemented")
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on violations")
    args = parser.parse_args()

    violations = check_directory(args.root)

    if not violations:
        print("RUFF022: 0 violations — all imports use hledac.universal.<pkg> canonical form")
        sys.exit(0)

    print(f"RUFF022: {len(violations)} violation(s) found:")
    for v in violations:
        rel = v.file.relative_to(args.root)
        print(f"  {rel}:{v.line}: {v.message}")

    print("\nFix: Replace bare imports with hledac.universal.<pkg> canonical form.")
    print("Allowed: from hledac.universal.runtime import ...")
    print("Banned: from runtime import ... (creates dual-namespace risk)")

    if args.ci:
        sys.exit(1)


if __name__ == "__main__":
    main()
