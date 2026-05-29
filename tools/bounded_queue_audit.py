#!/usr/bin/env python3
"""
Bounded Queue Audit — Sprint F207N-D Wave 1
Scans for asyncio.Queue() without maxsize, classifies by risk category.
No imports with heavy side effects. Pure stdlib + pathlib.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "probe_f207n_bounded_queue"


def classify(path: str) -> str:
    """Classify queue by file path category."""
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


def to_markdown(results: list[dict[str, Any]]) -> str:
    """Render audit results as Markdown."""
    lines = [
        "# Bounded Queue Audit Report",
        f"**Generated:** {datetime.now().isoformat()}",
        f"**Repository:** {REPO_ROOT.name}",
        "",
    ]

    categories = {}
    for r in results:
        categories.setdefault(r["category"], []).append(r)

    total = len(results)
    lines.append(f"**Total unbounded queues:** {total}\n")

    for cat in [
        "runtime_critical", "pipeline", "brain", "coordinator",
        "intelligence", "layers", "transport", "unknown", "test_only", "external",
    ]:
        items = categories.get(cat, [])
        if not items:
            continue
        lines.append(f"## {cat} ({len(items)})")
        for item in items:
            lines.append(f"- `{item['file']}:{item['line']}`")
            lines.append("  ```python")
            lines.append(f"  {item['code']}")
            lines.append("  ```")
        lines.append("")

    return "\n".join(lines)


def run_audit() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run full audit. Returns (results, category_counts)."""
    results = scan_unbounded_queues()
    category_counts = {}
    for r in results:
        category_counts[r["category"]] = category_counts.get(r["category"], 0) + 1
    return results, category_counts


if __name__ == "__main__":
    results, counts = run_audit()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON output
    report = {
        "generated": datetime.now().isoformat(),
        "repo": str(REPO_ROOT),
        "total_unbounded": len(results),
        "by_category": counts,
        "queues": results,
    }
    json_path = OUTPUT_DIR / "bounded_queue.json"
    json_path.write_text(json.dumps(report, indent=2))

    # Markdown output
    md_path = OUTPUT_DIR / "REPORT_BOUNDED_QUEUE.md"
    md_path.write_text(to_markdown(results))

    print(f"Audit complete: {len(results)} unbounded queues found")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")
