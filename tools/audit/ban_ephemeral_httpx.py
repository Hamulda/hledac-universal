#!/usr/bin/env python3
"""
BAN-EPHEMERAL-HTTPX — AST detector for per-call httpx.AsyncClient creation.

ISSUE #8: ``async with httpx.AsyncClient(...)`` outside ``transport/`` destroys
HTTP/2 multiplexing and burns a full TLS handshake per call. On M1 8GB each
ephemeral client also allocates ~2MB (SSL context + pool scaffolding), and
httpx DEFAULT limits (max_connections=100, max_keepalive_connections=20) mean
a handful of concurrent ephemeral clients exhausts the macOS FD ceiling
(``ulimit -n`` = 256).

VIOLATION
    async with httpx.AsyncClient(timeout=10.0) as client:   # <-- banned
        resp = await client.get(url)

FIX
    from hledac.universal.transport.client_pool import get_or_create_httpx_client

    client = await get_or_create_httpx_client("clearnet")
    resp = await client.get(url, timeout=10.0)   # per-request override

Profiles: clearnet | onion | i2p | stealth | darknet

WHY ``async with`` SPECIFICALLY
    ``async with httpx.AsyncClient(...)`` is *provably* ephemeral: the client is
    aclose()d at scope exit, so no connection can ever be reused. A bare
    assignment (``self._client = httpx.AsyncClient(...)``) may legitimately be a
    long-lived pool, so it is reported separately under ``--strict`` rather than
    failing the default gate.

EXEMPT
    * ``transport/`` — owns client construction (session_pool, client_pool, ...)
    * ``tests/`` — fixtures legitimately build throwaway clients
    * ``tools/audit/`` — this file references the pattern in strings/docs

Run: python tools/audit/ban_ephemeral_httpx.py [--strict] [--show-exempt]

Exit codes:
  0 — no violations
  1 — violations found
  2 — invalid args / AST parse error
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Directories that legitimately construct httpx clients.
_EXEMPT_DIR_PARTS = frozenset(
    {
        "transport",  # client ownership layer (session_pool, client_pool)
        "tests",  # throwaway fixtures
        "__pycache__",
        ".venv",
        ".venv-test",
        ".git",
        ".claude",
        "archive",
        ".pyscn",
        ".housekeeping_cleanup",
    }
)

# Specific files exempt regardless of directory.
_EXEMPT_FILES = frozenset(
    {
        "tools/audit/ban_ephemeral_httpx.py",  # this detector
    }
)

# Recommended profile per path hint — drives the fix suggestion.
_PROFILE_HINTS: tuple[tuple[str, str], ...] = (
    ("stealth", "stealth"),
    ("tor", "onion"),
    ("onion", "onion"),
    ("darknet", "darknet"),
    ("i2p", "i2p"),
)


def _suggest_profile(path: Path) -> str:
    """Suggest a client_pool profile based on the file's path."""
    lowered = str(path).lower()
    for needle, profile in _PROFILE_HINTS:
        if needle in lowered:
            return profile
    return "clearnet"


def _is_exempt(py_file: Path, root: Path) -> bool:
    """True if the file is in an exempt directory or on the exempt list."""
    try:
        rel = py_file.relative_to(root)
    except ValueError:
        rel = py_file
    if rel.as_posix() in _EXEMPT_FILES:
        return True
    # Backup/scratch artifacts are not live code.
    if py_file.suffix != ".py" or ".bak" in py_file.name:
        return True
    return any(part in _EXEMPT_DIR_PARTS for part in rel.parts)


def _is_httpx_async_client(node: ast.expr) -> bool:
    """
    True if the expression constructs an httpx AsyncClient.

    Matches both ``httpx.AsyncClient(...)`` (attribute) and a bare
    ``AsyncClient(...)`` imported via ``from httpx import AsyncClient``.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "AsyncClient" and isinstance(func.value, ast.Name) and func.value.id == "httpx"
    if isinstance(func, ast.Name):
        return func.id == "AsyncClient"
    return False


class _EphemeralHttpxVisitor(ast.NodeVisitor):
    """Collects ephemeral (``async with``) and bare httpx.AsyncClient sites."""

    def __init__(self) -> None:
        self.ephemeral: list[tuple[int, str]] = []
        self.bare: list[tuple[int, str]] = []

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802 — ast API
        for item in node.items:
            if _is_httpx_async_client(item.context_expr):
                self.ephemeral.append((node.lineno, "async with httpx.AsyncClient(...)"))
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802 — ast API
        # `with httpx.AsyncClient()` is a bug in its own right, but it is still
        # scope-bound construction — report it in the same bucket.
        for item in node.items:
            if _is_httpx_async_client(item.context_expr):
                self.ephemeral.append((node.lineno, "with httpx.AsyncClient(...)"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — ast API
        if _is_httpx_async_client(node):
            self.bare.append((node.lineno, "httpx.AsyncClient(...) assignment"))
        self.generic_visit(node)


def _collect(py_file: Path) -> tuple[list[tuple[int, str]], list[tuple[int, str]]] | None:
    """Parse a file and return (ephemeral, bare) sites, or None on read/parse failure."""
    try:
        source = py_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    # Cheap pre-filter: skip files that never mention AsyncClient.
    if "AsyncClient" not in source:
        return [], []
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError as e:
        print(f"::warning:: AST parse failed: {py_file}:{e.lineno}: {e.msg}", file=sys.stderr)
        return None

    visitor = _EphemeralHttpxVisitor()
    visitor.visit(tree)
    # `async with` sites also appear in `bare` (the Call node is nested) —
    # deduplicate by line so a single site is not double-reported.
    ephemeral_lines = {line for line, _ in visitor.ephemeral}
    bare = [(line, kind) for line, kind in visitor.bare if line not in ephemeral_lines]
    return visitor.ephemeral, bare


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ban per-call httpx.AsyncClient creation outside transport/ (ISSUE #8).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root to scan (default: hledac/universal).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also fail on bare httpx.AsyncClient(...) assignments (possible private pools).",
    )
    parser.add_argument(
        "--show-exempt",
        action="store_true",
        help="List exempt files that construct clients (informational).",
    )
    args = parser.parse_args()

    root: Path = args.root
    if not root.is_dir():
        print(f"error: root is not a directory: {root}", file=sys.stderr)
        return 2

    ephemeral_hits: list[tuple[Path, int, str]] = []
    bare_hits: list[tuple[Path, int, str]] = []
    exempt_hits: list[Path] = []
    parse_failures = 0

    for py_file in sorted(root.rglob("*.py")):
        if _is_exempt(py_file, root):
            if args.show_exempt:
                result = _collect(py_file)
                if result and (result[0] or result[1]):
                    exempt_hits.append(py_file)
            continue

        result = _collect(py_file)
        if result is None:
            parse_failures += 1
            continue
        ephemeral, bare = result
        rel = py_file.relative_to(root) if py_file.is_relative_to(root) else py_file
        ephemeral_hits.extend((rel, line, kind) for line, kind in ephemeral)
        bare_hits.extend((rel, line, kind) for line, kind in bare)

    if args.show_exempt and exempt_hits:
        print("Exempt files constructing httpx clients (expected):")
        for path in exempt_hits:
            print(f"  {path.relative_to(root) if path.is_relative_to(root) else path}")
        print()

    if ephemeral_hits:
        print(f"BAN-EPHEMERAL-HTTPX: {len(ephemeral_hits)} violation(s) — per-call client destroys HTTP/2 reuse\n")
        for rel, line, kind in ephemeral_hits:
            profile = _suggest_profile(rel)
            print(f"  {rel}:{line}: {kind}")
            print(f"      fix: client = await get_or_create_httpx_client({profile!r})")
        print(
            "\n  import: from hledac.universal.transport.client_pool import get_or_create_httpx_client"
            "\n  note:   pass per-request overrides (timeout=, headers=) instead of building a client."
        )

    if bare_hits:
        label = "violation(s)" if args.strict else "candidate(s) — informational, use --strict to enforce"
        print(f"\nBare httpx.AsyncClient(...) assignments: {len(bare_hits)} {label}")
        for rel, line, kind in bare_hits:
            print(f"  {rel}:{line}: {kind}")

    if parse_failures:
        print(f"\n{parse_failures} file(s) could not be parsed (see warnings above).", file=sys.stderr)

    failed = bool(ephemeral_hits) or (args.strict and bool(bare_hits))
    if not failed:
        print("BAN-EPHEMERAL-HTTPX: OK — no per-call httpx.AsyncClient outside transport/")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
