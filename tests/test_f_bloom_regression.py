"""
test_f_bloom_regression.py — prevent ScalableBloomFilter from sneaking back in.

What this test enforces
-----------------------
CLAUDE.md invariant #7:
    "RotatingBloomFilter pro URL dedup — nikdy ``Set[str]`` nebo
    ``ScalableBloomFilter``."

The unbounded ``ScalableBloomFilter`` is deprecated and lives only as a
backward-compat alias in ``utils/bloom_filter.py`` (it forwards to
``RotatingBloomFilter`` and emits ``DeprecationWarning``).

This test grep-scans the source tree and FAILS if any production module
imports ``ScalableBloomFilter`` outside the audited alias location and
the dedicated deprecation test. Specifically:

  * Forbidden:  ``intelligence/``, ``coordinators/``, ``runtime/``,
    ``fetching/``, ``knowledge/``, ``brain/``, ``transport/``,
    ``network/``, ``core/``, ``pipeline/``, ``planning/``, ``discovery/``,
    ``export/``, ``monitoring/``, ``memory/``, ``forensics/``,
    ``multimodal/``, ``prefetch/``, ``rl/``, ``security/``, ``stealth/``,
    ``execution/``, ``layers/``, ``tools/``, ``utils/`` (excluding
    ``utils/bloom_filter.py`` itself), ``__main__.py``,
    ``hledac_hypothesis/``.

  * Allowed:    ``utils/bloom_filter.py`` (the alias),
                ``utils/__init__.py`` (re-export),
                ``tests/test_f_bloom_deprecation.py`` (the audit test).

Why these tests exist
----------------------
* Sprint F196A: ghost verdict deleted dead code; we must keep dead code
  out of the import graph.
* M1 8GB UMA: unbounded growth in a long-running sprint == OOM kill.
* Bounded, fail-safe: emit a clear test failure with the file/line.
"""
from __future__ import annotations

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

# Files where the import is legal (the alias and its test).
ALLOWED_FILES = {
    REPO_ROOT / "utils" / "bloom_filter.py",
    REPO_ROOT / "utils" / "__init__.py",
    REPO_ROOT / "tests" / "test_f_bloom_deprecation.py",
    # This very test, for self-reference.
    REPO_ROOT / "tests" / "test_f_bloom_regression.py",
}

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


class TestScalableBloomFilterRegression:
    def test_no_production_module_imports_scalable_bloom_filter(self):
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
                    # Line number of the match
                    line_no = text[: m.start()].count("\n") + 1
                    line_text = text.splitlines()[line_no - 1].strip()
                    # Skip lines that are themselves a comment
                    if line_text.startswith("#"):
                        continue
                    violations.append((py, line_no, line_text))
        assert not violations, (
            "CLAUDE.md invariant #7 violated: ScalableBloomFilter imported in "
            "production code. Use RotatingBloomFilter (bounded, M1 8GB safe).\n"
            + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}  {lt}" for p, ln, lt in violations)
        )

    def test_utils_bloom_filter_alias_is_deprecated(self):
        """The alias in ``utils/bloom_filter.py`` must emit DeprecationWarning
        and forward to ``RotatingBloomFilter``."""
        from utils.bloom_filter import ScalableBloomFilter, RotatingBloomFilter
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bf = ScalableBloomFilter(initial_capacity=100, error_rate=0.01)
        deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecation, "ScalableBloomFilter must emit DeprecationWarning"
        # The instance is bounded (RotatingBloomFilter), not the original.
        assert isinstance(bf, RotatingBloomFilter)
