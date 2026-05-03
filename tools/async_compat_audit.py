"""
Python 3.14 Async Compatibility Audit Tool
==========================================

Scans for async patterns that changed in Python 3.14:
- asyncio.get_event_loop() behavior
- asyncio.wait_for() deprecation
- get_running_loop() preference

Classification:
- SAFE_TEST_ONLY: test-only usage, acceptable
- NEEDS_REVIEW: pattern exists, needs manual review
- SIMPLE_HELPER_FIX: direct replacement possible
- RUNTIME_CRITICAL_DEFER: major runtime change, defer to later sprint
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple


class Finding(NamedTuple):
    file: str
    line: int
    col: int
    pattern: str
    code_snippet: str
    classification: str
    reason: str


ASYNC_PATTERNS = [
    (r"asyncio\.get_event_loop\(\)", "get_event_loop() deprecated in 3.14"),
    (r"asyncio\.wait_for\(", "wait_for() deprecated in 3.14, prefer asyncio.timeout()"),
    (r"get_event_loop\(\)", "get_event_loop() deprecated in 3.14"),
    (r"asyncio\.ensure_future\(", "ensure_future() deprecated in 3.14"),
]


DEFER_PATHS = [
    "runtime/sprint_scheduler",
    "runtime/acquisition_strategy",
    "pipeline",
    "fetching",
    "discovery",
    "intelligence",
]


def classify(path: str) -> str:
    if "test" in path:
        return "SAFE_TEST_ONLY"
    for d in DEFER_PATHS:
        if d in path:
            return "RUNTIME_CRITICAL_DEFER"
    if "utils/async_helpers" in path:
        return "SIMPLE_HELPER_FIX"
    return "NEEDS_REVIEW"


def audit_file(path: Path) -> List[Finding]:
    findings = []
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return findings

    for lineno, line in enumerate(source.splitlines(), start=1):
        for pat, reason in ASYNC_PATTERNS:
            if re.search(pat, line):
                m = re.search(pat, line)
                findings.append(Finding(
                    file=str(path),
                    line=lineno,
                    col=m.start() if m else 0,
                    pattern=pat,
                    code_snippet=line.strip(),
                    classification=classify(str(path)),
                    reason=reason,
                ))
    return findings


def run_audit(root: Path) -> Dict[str, Any]:
    findings: List[Finding] = []

    skip_dirs = {".venv", "__pycache__", "probe_f207o_async314"}

    for py_file in root.rglob("*.py"):
        path_str = str(py_file)
        if any(s in path_str for s in skip_dirs):
            continue
        findings.extend(audit_file(py_file))

    buckets = {"SAFE_TEST_ONLY": [], "NEEDS_REVIEW": [], "SIMPLE_HELPER_FIX": [], "RUNTIME_CRITICAL_DEFER": []}
    for f in findings:
        buckets[f.classification].append(f._asdict())

    return {
        "audit_timestamp": datetime.now(timezone.utc).isoformat(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "total_findings": len(findings),
        "by_classification": {k: len(v) for k, v in buckets.items()},
        "findings": [f._asdict() for f in findings],
        "summary": {k.replace("_", "_"): len(v) for k, v in buckets.items()},
    }


if __name__ == "__main__":
    root = Path(__file__).parent.parent
    report = run_audit(root)

    out_json = root / "probe_f207o_async314" / "async314.json"
    out_md = root / "probe_f207o_async314" / "REPORT_ASYNC314.md"

    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Python 3.14 Async Compatibility Audit",
        f"\n**Audit Date:** {report['audit_timestamp']}",
        f"**Python Version:** {report['python_version']}",
        f"\n## Summary",
    ]

    for classification in ["SIMPLE_HELPER_FIX", "NEEDS_REVIEW", "RUNTIME_CRITICAL_DEFER", "SAFE_TEST_ONLY"]:
        count = report["summary"].get(classification, 0)
        emoji = {"SIMPLE_HELPER_FIX": "🔧", "NEEDS_REVIEW": "👀", "RUNTIME_CRITICAL_DEFER": "🚧", "SAFE_TEST_ONLY": "✅"}.get(classification, "")
        lines.append(f"- **{emoji} {classification}:** {count}")

    lines.append(f"\n## Findings by Classification\n")

    for classification in ["SIMPLE_HELPER_FIX", "NEEDS_REVIEW", "RUNTIME_CRITICAL_DEFER", "SAFE_TEST_ONLY"]:
        items = [f for f in report["findings"] if f["classification"] == classification]
        if items:
            lines.append(f"\n### {classification} ({len(items)})\n")
            for item in items:
                try:
                    rel = Path(item["file"]).relative_to(root)
                except ValueError:
                    rel = item["file"]
                lines.append(f"- **{rel}:{item['line']}** `{item['code_snippet']}` — {item['reason']}")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit complete: {report['total_findings']} findings")
    print(f"  JSON: {out_json}")
    print(f"  MD:   {out_md}")
    print(f"\nBy classification:")
    for k, v in report["by_classification"].items():
        print(f"  {k}: {v}")