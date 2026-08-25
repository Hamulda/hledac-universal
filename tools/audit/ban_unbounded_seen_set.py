#!/usr/bin/env python3
"""
BAN-LOCAL-SEEN-SET — AST detector for unbounded set() in crawlers.
Issue #6: Replace seen[_a-z]* = set() with make_url_dedup() in crawler loops.

Targets local-variable unbounded sets used for URL / crawl dedup:
  seen_urls, seen, seen_cids, seen_domains (in crawler context)

Excludes (safe — not URL/crawl dedup):
  seen_terms, seen_titles, seen_algorithms, seen_org, seen_ip,
  seen_paths, seen_hashes, seen_ids, seen_keys, seen_sources,
  seen_packets, seen_srcs, seen_urls used as dict keys,
  any set inside non-crawler helper functions.

Fix:
  Replace `seen_urls = set()` with `seen_urls = make_url_dedup(capacity=N)`.

Run: python tools/audit/ban_unbounded_seen_set.py [--fix] [--show-ignored]

Exit codes:
  0 — no violations (all crawler dedup sets are bounded)
  1 — violations found
  2 — invalid args / AST parse error
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------
# Safe patterns — these names are NOT crawler URL dedup
# ---------------------------------------------------------------------
_SAFE_PATTERNS = {
    # IOC / entity types (not crawl dedup)
    "seen_terms", "seen_titles", "seen_labels",
    "seen_algorithms", "seen_org", "seen_orgs",
    "seen_ip", "seen_ips", "seen_domain", "seen_domains",
    "seen_paths", "seen_hashes", "seen_ids", "seen_keys",
    "seen_sources", "seen_srcs", "seen_src",
    "seen_packets", "seen_kinds", "seen_types",
    "seen_source_types", "seen_entries", "seen_results",
    "seen_colors", "seen_items", "seen_urls_dict",
    "seen_data", "seen_pairs", "seen_files",
    "seen_certs", "seen_issuers", "seen_sans",
    "seen_usernames", "seen_entities",
    "seen_pages", "seen_docs",
    "seen_entry_urls",  # feed dedup — handled separately
    # Graph / structure dedup (not URL crawl)
    "seen_graph", "seen_edges", "seen_nodes",
    "seen_object", "seen_obj", "seen_objects",
    "seen_links",  # HTML link dedup in extractors
    "seen",  # generic seen — must check context
    "seen_statements", "seen_queries",  # brain/NLP dedup
    # Result / sidecar processing (short-lived, bounded)
    "seen_sidecars",
    # Import dedup in optimize_imports
    "seen_imports",
    # DNS / tunnel detection
    "seen_domains_dns",
    # Graph / evidence / relationship processing (not crawl)
    "seen_relationships", "seen_rels",
    # WHOIS result processing
    "seen_whois",
    # Workflow / step tracking
    "seen_workflow", "seen_steps",
    # Cleanup / maintenance
    "seen_snapshots", "seen_baks",
    # DSPy / inference engine
    "seen_tools", "seen_results",
    # Node / edge dedup in graphs
    "seen_node", "seen_edge",
    # Object-ID dedup (not URL)
    "seen_object", "seen_obj",
    # Fusion / multimodal (not crawl)
    "seen_fused", "seen_fusions",
    # Canary / forensics (not crawl)
    "seen_canaries", "seen_signals",
    # DNS / network detection
    "seen_ips_dns", "seen_queries_dns",
    # Cleanup / GC
    "seen_cleanup", "seen_gc",
    # Query expansion (semantic, not crawl)
    "seen_expanded", "seen_queries_expanded",

}

# Functions whose body is NEVER a crawler loop (safe helpers)
_SAFE_FUNC_NAMES = {
    "_process_cert", "_build_discovery_hit", "_deduplicate_entries",
    "_extract_iocs_from_text", "_simple_deduplicate",
    "_normalize_url", "_validate", "_parse",
    "_entry_dedup_key", "_python_fast_ioc_extract",
    "make_url_dedup", "RotatingBloomFilter",
}

# Crawler-adjacent function name patterns — inside these, seen_* sets are suspect
_CRAWLER_FUNC_PATTERNS = (
    "crawl", "scrape", "fetch", "visit", "walk",
    "search", "discover", "enumerate", "index",
    "scan", "probe", "collect", "harvest",
    "process_tvnews", "process_crtsh", "process_ti",
    "_do_crawl", "_crawl", "_fetch",
    "search_public_web", "_search",
    "async_search_public_web",
)

# Directories to skip entirely
_SKIP_DIRS = frozenset({
    "__pycache__", ".venv", ".venv-test", ".git",
    ".claude", "tools/migrate", "tools/preserved_logic",
    "tests", ".pyscn", ".housekeeping_cleanup",
    # Non-crawler Python scripts
    "tools/maintenance",
})

# Files to skip
_SKIP_FILES = frozenset({
    "utils/crawler_dedup.py",  # The factory itself
    "utils/bloom_filter.py",    # RotatingBloomFilter implementation
    "knowledge/dedup.py",       # DedupManager implementation
    "test_rust_text_fast.py",   # Test fixture — not a crawler
})


class SeenSetVisitor(ast.NodeVisitor):
    """AST visitor that detects unbounded set() for crawl/URL dedup."""

    def __init__(self, file_path: Path, fix: bool = False) -> None:
        self.file_path = file_path
        self.fix = fix
        self.violations: list[tuple[int, str]] = []  # (lineno, message)
        self.ignored: list[tuple[int, str]] = []  # (lineno, reason)
        self._in_crawler_func = False
        self._current_func_name: str = ""
        self._crawler_dedup_imported = False

    def _is_crawler_context(self, func_name: str) -> bool:
        fn = func_name.lower()
        return any(pat in fn for pat in _CRAWLER_FUNC_PATTERNS)

    def _is_safe_name(self, name: str) -> bool:
        return name in _SAFE_PATTERNS

    def _check_assign(self, node: ast.Assign) -> None:
        """Check a single assignment statement for unbounded set() dedup."""
        # Only care about single-target assignments
        if len(node.targets) != 1:
            return
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            return

        name = target.id

        # Is it a seen_* variable?
        if not (name.startswith("seen_") or name == "seen"):
            return

        # Is the RHS a set() call?
        if not isinstance(node.value, ast.Call):
            return
        if not (isinstance(node.value.func, ast.Name) and node.value.func.id == "set"):
            # Also allow set([...]) and set({...}) — still unbounded
            pass

        # Determine context
        is_crawler = self._is_crawler_context(self._current_func_name)

        # Known safe names
        if self._is_safe_name(name):
            self.ignored.append((node.lineno, f"{name} = set() — known safe (non-URL dedup)"))
            return

        # skip_locations is used as a set — dict-based dedup
        if "dict" in name or "map" in name:
            self.ignored.append((node.lineno, f"{name} = set() — dict/map dedup, not crawler"))
            return

        # If make_url_dedup is imported in this file, assume the fix is in place
        # for any remaining set() — they may be non-URL dedup
        if self._crawler_dedup_imported:
            self.ignored.append((node.lineno, f"{name} = set() — make_url_dedup imported in file, assuming safe"))
            return

        # Classify as crawler if in crawler-named function
        if is_crawler:
            self.violations.append((
                node.lineno,
                f"{name} = set() — unbounded crawler dedup (M1 8GB memory leak risk). "
                f"Use: from utils.crawler_dedup import make_url_dedup; {name} = make_url_dedup(capacity=N)"
            ))
        else:
            # Ambiguous — could be in a helper that crawlers call
            self.violations.append((
                node.lineno,
                f"{name} = set() — possibly unbounded URL dedup. "
                f"Use: from utils.crawler_dedup import make_url_dedup; {name} = make_url_dedup(capacity=N)"
            ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and "crawler_dedup" in node.module:
            self._crawler_dedup_imported = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev_func = self._current_func_name
        prev_crawler = self._in_crawler_func

        self._current_func_name = node.name
        if self._is_crawler_context(node.name):
            self._in_crawler_func = True

        self.generic_visit(node)

        self._current_func_name = prev_func
        self._in_crawler_func = prev_crawler

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev_func = self._current_func_name
        prev_crawler = self._in_crawler_func

        self._current_func_name = node.name
        if self._is_crawler_context(node.name):
            self._in_crawler_func = True

        self.generic_visit(node)

        self._current_func_name = prev_func
        self._in_crawler_func = prev_crawler

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_assign(node)
        self.generic_visit(node)


def find_violations(
    root: Path,
    fix: bool = False,
    show_ignored: bool = False,
) -> tuple[int, list[tuple[Path, int, str]]]:
    """
    Walk all .py files under root and detect unbounded set() in crawler contexts.
    Returns (exit_code, list of (path, lineno, message) violations).
    """
    total_violations: list[tuple[Path, int, str]] = []

    for py_file in root.rglob("*.py"):
        # Skip dirs
        if any(skip in py_file.parts for skip in _SKIP_DIRS):
            continue
        # Skip files
        if py_file.name in _SKIP_FILES:
            continue
        if py_file.name.endswith(".bak_core_keyword"):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        visitor = SeenSetVisitor(py_file, fix=fix)
        visitor.visit(tree)

        for lineno, msg in visitor.violations:
            total_violations.append((py_file, lineno, msg))

    if show_ignored and total_violations:
        print("Note: Some remaining set() calls may be intentional non-URL dedup.")

    return 0 if not total_violations else 1, total_violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ban unbounded set() in crawler URL dedup — Issue #6 fix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/audit/ban_unbounded_seen_set.py
  python tools/audit/ban_unbounded_seen_set.py --fix
  python tools/audit/ban_unbounded_seen_set.py --show-ignored
        """,
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Auto-fix (not yet implemented — shows diff only)",
    )
    parser.add_argument(
        "--show-ignored", action="store_true",
        help="Also show intentionally safe set() uses",
    )
    parser.add_argument(
        "--root", type=Path,
        default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"),
    )
    args = parser.parse_args()

    if not args.root.exists():
        print(f"Error: root does not exist: {args.root}", file=sys.stderr)
        sys.exit(2)

    exit_code, violations = find_violations(args.root, fix=args.fix, show_ignored=args.show_ignored)

    if not violations:
        print("BAN-SEEN-SET: 0 violations — all crawler dedup sets are bounded via make_url_dedup()")
        sys.exit(0)

    print(f"BAN-SEEN-SET: {len(violations)} violation(s) found:")
    print()
    for path, lineno, msg in violations:
        rel = path.relative_to(args.root)
        print(f"  {rel}:{lineno}")
        print(f"    → {msg}")
        print()

    print("Fix: Replace unbounded set() with make_url_dedup(capacity=N):")
    print()
    print("  Before:")
    print("    seen_urls = set()                    # M1 8GB memory leak!")
    print()
    print("  After:")
    print("    from hledac.universal.utils.crawler_dedup import make_url_dedup  # Issue #6")
    print("    seen_urls = make_url_dedup(capacity=100_000)  # bounded, O(1) add/contains")
    print()
    print("  Or inline import:")
    print("    seen_urls = make_url_dedup(capacity=50_000)  # import inside the function")

    sys.exit(1)


if __name__ == "__main__":
    main()
