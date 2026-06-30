"""
test_f_bloom_regression.py — enforce complete removal of ScalableBloomFilter.

What this test enforces
-----------------------
CLAUDE.md invariant #7:
    "RotatingBloomFilter pro URL dedup — nikdy ``Set[str]`` nebo
    ``ScalableBloomFilter``."

The unbounded ``ScalableBloomFilter`` was a deprecated alias that forwarded
to ``RotatingBloomFilter``. It has been completely removed.

This test enforces:
1. ScalableBloomFilter is NOT exported from utils.bloom_filter
2. ScalableBloomFilter is NOT exported from utils.__init__
3. No production module imports ScalableBloomFilter

Why these tests exist
----------------------
* Sprint F196A: ghost verdict deleted dead code; we must keep dead code
  out of the import graph.
* M1 8GB UMA: unbounded growth in a long-running sprint == OOM kill.
* Bounded, fail-safe: emit a clear test failure with the file/line.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Production modules that must NEVER import ScalableBloomFilter.
PROD_DIRS = [
    "intelligence",
    "coordinators",
    "runtime",
    "fetching",
    "knowledge",
    "brain",
    "transport",
    "network",
    "core",
    "pipeline",
    "planning",
    "discovery",
    "export",
    "monitoring",
    "memory",
    "forensics",
    "multimodal",
    "prefetch",
    "rl",
    "security",
    "stealth",
    "execution",
    "layers",
    "tools",
    "hledac_hypothesis",
]

# ScalableBloomFilter no longer exists anywhere — no allowed files.
ALLOWED_FILES: set[Path] = set()

# Strict patterns: an actual import, not a docstring/comment.
IMPORT_PATTERNS = [
    re.compile(r"^\s*from\s+\S+\s+import\s+[^\n]*\bScalableBloomFilter\b", re.MULTILINE),
    re.compile(r"^\s*import\s+\S*[Ss]calableBloomFilter\b", re.MULTILINE),
]


def _all_python_files() -> list[Path]:
    out: list[Path] = []
    for d in PROD_DIRS:
        full = REPO_ROOT / d
        if not full.exists():
            continue
        out.extend(full.rglob("*.py"))
    # Top-level entry points
    for fname in ("__main__.py", "core/__main__.py"):
        p = REPO_ROOT / fname
        if p.exists():
            out.append(p)
    return out


class TestScalableBloomFilterRemoval:
    def test_no_production_module_imports_scalable_bloom_filter(self):
        """Production code must not import ScalableBloomFilter."""
        violations: list[tuple[Path, int, str]] = []
        for py in _all_python_files():
            if py in ALLOWED_FILES:
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for pattern in IMPORT_PATTERNS:
                for m in pattern.finditer(text):
                    line_no = text[: m.start()].count("\n") + 1
                    line_text = text.splitlines()[line_no - 1].strip()
                    if line_text.startswith("#"):
                        continue
                    violations.append((py, line_no, line_text))
        assert not violations, (
            "CLAUDE.md invariant #7 violated: ScalableBloomFilter imported in "
            "production code. Use RotatingBloomFilter (bounded, M1 8GB safe).\n"
            + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}  {lt}" for p, ln, lt in violations)
        )

    def test_scalable_bloom_filter_not_in_bloom_filter_module(self):
        """ScalableBloomFilter must be completely removed from bloom_filter.py."""
        from utils import bloom_filter

        assert not hasattr(bloom_filter, "ScalableBloomFilter"), (
            "ScalableBloomFilter must be removed from utils.bloom_filter"
        )

    def test_scalable_bloom_filter_not_exported_from_utils(self):
        """ScalableBloomFilter must not be exported from utils.__init__."""
        import pytest

        with pytest.raises(ImportError):
            from utils import ScalableBloomFilter  # noqa: F401

    def test_not_in_bloom_filter_all(self):
        """ScalableBloomFilter must not appear in bloom_filter.__all__."""
        from utils import bloom_filter

        assert "ScalableBloomFilter" not in getattr(bloom_filter, "__all__", [])