#!/usr/bin/env python3
"""
Bounded Resources Audit — P1-2 / F207N-D
Scans for:
  1. asyncio.Queue() without maxsize  (unbounded queue → memory leak)
  2. @lru_cache(maxsize=None)        (unbounded cache  → M1 swap on 24h sprint)

Allowed exceptions (by design):
  - utils/lazy_singleton.py  — bounded LRUCache, intentionally unbounded singleton
  - core/env_config.py       — @lru_cache(maxsize=512), bounded

No imports with heavy side effects. Pure stdlib + pathlib.
"""
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from core import aclose

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "probe_f207n_bounded_queue"

# Files/patterns that are allowed exceptions
LRU_CACHE_ALLOWED = {
    "utils/lazy_singleton.py",   # LazySingleton intentional; bounded LRUCache underneath
    "core/env_config.py",         # @lru_cache(maxsize=512) — bounded
    # tools/ excluded — audit/documentation scripts, not production code
}


def classify(path: str) -> str:
    """Classify resource by file path category."""
    if "sprint_scheduler" in path:
        return "runtime_critical"
    if any(x in path for x in ["pipeline/", "fetching/", "discovery/"]):
        return "pipeline"
    if "brain/" in path:
        return "brain"
    if "coordinators/" in path:
        return "coordinator"
    if any(x in path for x in ["intelligence/", "dark_web"]):
        return "intelligence"
    if any(x in path for x in ["tests/", "probe_"]):
        return "test_only"
    if "transport/" in path:
        return "transport"
    if "layers/" in path:
        return "layers"
    if ".venv/" in path or "site-packages/" in path:
        return "external"
    return "unknown"


# ── Queue scanner ──────────────────────────────────────────────────────────────

def scan_unbounded_queues() -> list[dict[str, Any]]:
    """Find all asyncio.Queue() without maxsize."""
    results = []

    for py_file in REPO_ROOT.rglob("*.py"):
        try:
            content = py_file.read_text()
        except Exception:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            if "asyncio.Queue" not in line:
                continue
            # Match asyncio.Queue() with no maxsize argument
            # Exclude: asyncio.Queue(maxsize=...), Queue(maxsize=...)
            if re.search(r'asyncio\.Queue\(\s*(?!maxsize\s*=)', line):
                rel = str(py_file.relative_to(REPO_ROOT))
                results.append({
                    "file": rel,
                    "line": lineno,
                    "category": classify(rel),
                    "code": line.strip(),
                })

    return results


# ── lru_cache scanner ─────────────────────────────────────────────────────────

def scan_unbounded_lru() -> list[dict[str, Any]]:
    """
    Find @lru_cache(maxsize=None) and @cache (unbounded) in project source.

    P1-2: unbounded lru_cache grows unbounded over 24h sprint → M1 8GB swap.
    Allowed exceptions: utils/lazy_singleton.py, core/env_config.py (see LRU_CACHE_ALLOWED).

    Detects three patterns:
      1. @lru_cache(maxsize=None)           — explicit unbounded
      2. @cache                             — functools.cache = unbounded lru_cache (py39+)
      3. Multi-line @lru_cache(\n  maxsize=None  ) — wrapped across lines
    """
    results = []

    for py_file in REPO_ROOT.rglob("*.py"):
        try:
            content = py_file.read_text()
        except Exception:
            continue

        rel = str(py_file.relative_to(REPO_ROOT))
        if rel in LRU_CACHE_ALLOWED:
            continue
        # tools/ contains audit/documentation scripts, not production code
        if rel.startswith("tools/") or any(p in rel for p in ["/tools/", "\\tools\\"]):
            continue

        # ── Pattern 1: single-line @lru_cache(maxsize=None) ────────────────
        for lineno, line in enumerate(content.splitlines(), 1):
            if 'lru_cache' not in line:
                continue
            if re.search(r'@\w*\.?lru_cache\s*\(\s*maxsize\s*=\s*None\s*\)', line):
                results.append({
                    "file": rel,
                    "line": lineno,
                    "category": classify(rel),
                    "code": line.strip(),
                })

        # ── Pattern 2: @cache (py39+), equivalent to unbounded lru_cache ──
        for lineno, line in enumerate(content.splitlines(), 1):
            if '@' not in line:
                continue
            if re.search(r'@\s*\.?cache\b', line):
                results.append({
                    "file": rel,
                    "line": lineno,
                    "category": classify(rel),
                    "code": line.strip(),
                })

        # ── Pattern 3: multi-line lru_cache(maxsize=None) ───────────────────
        # Match @...lru_cache( ... maxsize=None ... ) spanning newlines
        multi = re.search(
            r'@[\w.]*lru_cache\s*\([^)]*maxsize\s*=\s*None[^)]*\)',
            content,
            re.DOTALL,
        )
        if multi:
            # approximate line number: count newlines before match
            line_num = content[:multi.start()].count('\n') + 1
            # Avoid duplicate if already caught by single-line pattern
            snippet = content[multi.start(): multi.end()].replace('\n', '↵')
            existing = any(
                r["file"] == rel and r["line"] == line_num
                for r in results
            )
            if not existing:
                results.append({
                    "file": rel,
                    "line": line_num,
                    "category": classify(rel),
                    "code": f"@lru_cache(...maxsize=None...) = {snippet!r}",
                })

    return results


# ── Reporting ─────────────────────────────────────────────────────────────────

def to_markdown(queues: list[dict[str, Any]], lru_hits: list[dict[str, Any]]) -> str:
    """Render combined audit results as Markdown."""
    lines = [
        "# Bounded Resources Audit Report",
        f"**Generated:** {datetime.now(UTC).isoformat()}",  # noqa: DTZ005
        f"**Repository:** {REPO_ROOT.name}",
        "",
        "## 1. Unbounded asyncio.Queue()",
        f"**Total:** {len(queues)}\n",
    ]

    categories_q = {}
    for r in queues:
        categories_q.setdefault(r["category"], []).append(r)

    for cat in [
        "runtime_critical", "pipeline", "brain", "coordinator",
        "intelligence", "layers", "transport", "unknown", "test_only", "external",
    ]:
        items = categories_q.get(cat, [])
        if not items:
            continue
        lines.append(f"### {cat} ({len(items)})")
        for item in items:
            lines.append(f"- `{item['file']}:{item['line']}`")
            lines.append("  ```python")
            lines.append(f"  {item['code']}")
            lines.append("  ```")
        lines.append("")

    lines.append("## 2. Unbounded @lru_cache(maxsize=None)")
    lines.append(f"**Total:** {len(lru_hits)}\n")

    categories_l = {}
    for r in lru_hits:
        categories_l.setdefault(r["category"], []).append(r)

    for cat in [
        "runtime_critical", "pipeline", "brain", "coordinator",
        "intelligence", "layers", "transport", "unknown", "test_only", "external",
    ]:
        items = categories_l.get(cat, [])
        if not items:
            continue
        lines.append(f"### {cat} ({len(items)})")
        for item in items:
            lines.append(f"- `{item['file']}:{item['line']}`")
            lines.append("  ```python")
            lines.append(f"  {item['code']}")
            lines.append("  ```")
        lines.append("")

    return "\n".join(lines)


def run_audit() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """
    Run full bounded-resources audit.

    Returns (queue_results, lru_results, queue_counts, lru_counts).
    """
    queue_results = scan_unbounded_queues()
    lru_results = scan_unbounded_lru()

    queue_counts: dict[str, int] = {}
    for r in queue_results:
        queue_counts[r["category"]] = queue_counts.get(r["category"], 0) + 1

    lru_counts: dict[str, int] = {}
    for r in lru_results:
        lru_counts[r["category"]] = lru_counts.get(r["category"], 0) + 1

    return queue_results, lru_results, queue_counts, lru_counts


if __name__ == "__main__":
    queues, lrus, q_counts, lru_counts = run_audit()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "generated": datetime.now(UTC).isoformat(),  # noqa: DTZ005
        "repo": str(REPO_ROOT),
        "total_unbounded_queues": len(queues),
        "total_unbounded_lru": len(lrus),
        "queues_by_category": q_counts,
        "lru_by_category": lru_counts,
        "unbounded_queues": queues,
        "unbounded_lru": lrus,
    }
    json_path = OUTPUT_DIR / "bounded_resources.json"
    json_path.write_text(json.dumps(report, indent=2))

    md_path = OUTPUT_DIR / "REPORT_BOUNDED_RESOURCES.md"
    md_path.write_text(to_markdown(queues, lrus))

    print(f"Audit complete:")
    print(f"  unbounded queues: {len(queues)}")
    for cat, n in sorted(q_counts.items()):
        print(f"    {cat}: {n}")
    print(f"  unbounded lru_cache(maxsize=None): {len(lrus)}")
    for cat, n in sorted(lru_counts.items()):
        print(f"    {cat}: {n}")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
