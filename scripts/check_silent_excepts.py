#!/usr/bin/env python3
"""
hledac.universal.scripts.check_silent_excepts — CLI entry point stub

This module provides the console script entry point declared in pyproject.toml:
    check-silent-excepts = "hledac.universal.scripts.check_silent_excepts:main"

The logic is a direct copy from tools/scripts/check_silent_excepts.py
(avoids namespace import issues in flat-layout package).

Usage:
    hledac check-silent-excepts            # CLI script entry
    python -m hledac.universal.scripts.check_silent_excepts  # module invocation
"""

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # hledac/universal/
SKIP_PARTS = {
    "tests", "legacy", "archive", "_shims", "_deprecated",
    "build", "benchmark_results", ".venv", ".venv-test",
    "__pycache__", ".git", "graphify-out", "node_modules",
    "probe_",  # probe test fixtures
}


def iter_production_files() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*.py"):
        if any(part in p.parts for part in SKIP_PARTS):
            continue
        out.append(p)
    return out


def find_unmarked_sites(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, method_name, except_signature)] for every
    `except ...: pass` whose `pass` line is missing the noqa marker."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(text)
    except (SyntaxError, UnicodeDecodeError):
        return []

    lines = text.splitlines(keepends=True)

    # Map: pass line -> enclosing function name
    func_for_line: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            name = node.name
            for ln in range(node.lineno, end + 1):
                cur = func_for_line.get(ln)
                if cur is None or len(name) > len(cur):  # prefer deeper
                    func_for_line[ln] = name

    sites: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
            continue
        pass_idx = node.body[0].lineno - 1
        line = lines[pass_idx]
        # Check both pass line and except line for noqa marker
        except_line = lines[node.lineno - 1]
        if "noqa: BARE-EXCEPT" in line or "noqa: BLE001" in line:
            continue
        if "noqa: BARE-EXCEPT" in except_line or "noqa: BLE001" in except_line:
            continue
        method = func_for_line.get(pass_idx + 1, "<module>")
        except_sig = lines[node.lineno - 1].rstrip()
        sites.append((pass_idx + 1, method, except_sig))
    return sites


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CI check: detect silent `except: pass` blocks WITHOUT noqa comment."
    )
    parser.add_argument("--stats", action="store_true",
                        help="print counts only, do not fail")
    args = parser.parse_args()

    files = iter_production_files()
    total_sites = 0
    total_files = 0
    examples_per_file: dict[str, int] = {}

    for p in files:
        sites = find_unmarked_sites(p)
        if not sites:
            continue
        total_sites += len(sites)
        total_files += 1
        examples_per_file[str(p.relative_to(ROOT))] = len(sites)

    if args.stats:
        print(f"production files scanned : {len(files)}")
        print(f"files with unmarked pass : {total_files}")
        print(f"total unmarked sites     : {total_sites}")
        return 0

    if total_sites == 0:
        print(f"OK: no unmarked silent excepts in {len(files)} production files")
        return 0

    print(f"FAIL: {total_sites} unmarked silent excepts in {total_files} files")
    print("Add `# noqa: BLE001` or `# noqa: BARE-EXCEPT` to each pass line to suppress.")
    print()
    # Show top 10 offenders
    for path, n in sorted(examples_per_file.items(), key=lambda x: -x[1])[:10]:
        print(f"  {n:3d}  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
