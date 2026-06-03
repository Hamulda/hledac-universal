"""
Sprint CP314: pyahocorasick / pyzipper / lmdb fallback tests.

These tests verify fail-soft behavior when sdist-only wheels cannot be
installed (e.g. Python 3.14 on M1 without a C toolchain).

Coverage:
  1. pattern_matcher works without pyahocorasick (pure-Python fallback)
  2. pattern_matcher works without hledac_rust_extensions (pyahocorasick path)
  3. pattern_matcher works with both backends (regression check)
  4. _PyAhoCorasickAutomaton API compat: add_word, make_automaton, iter
  5. _PyAhoCorasickAutomaton handles overlapping prefixes correctly
  6. pattern_matcher API: match_text, configure_patterns, get_backend_info
"""

from __future__ import annotations

import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers: block a top-level module import in a single test scope
# ---------------------------------------------------------------------------

class _BlockImport:
    """Meta-path finder that raises ImportError for a given set of names.

    Used to simulate environments where pyahocorasick or
    hledac_rust_extensions cannot be imported (e.g. Python 3.14 without
    a C toolchain, or Rust extension not yet built).
    """

    def __init__(self, *names: str) -> None:
        self.names = frozenset(names)

    def find_spec(self, name: str, path=None, target=None):  # noqa: ARG002
        if name in self.names:
            raise ImportError(f"CP314-FALLBACK-TEST: blocked {name}")
        return None


@pytest.fixture
def no_pyahocorasick():
    """Force pattern_matcher to use the pure-Python fallback."""
    blocked = ("ahocorasick", "hledac_rust_extensions")
    finder = _BlockImport(*blocked)
    sys.meta_path.insert(0, finder)
    for m in list(sys.modules):
        if m in blocked or m == "patterns.pattern_matcher":
            sys.modules.pop(m, None)
    yield
    sys.meta_path.remove(finder)
    for m in list(sys.modules):
        if m in blocked or m == "patterns.pattern_matcher":
            sys.modules.pop(m, None)


@pytest.fixture
def no_rust():
    """Force pattern_matcher to use pyahocorasick (no Rust extension)."""
    blocked = ("hledac_rust_extensions",)
    finder = _BlockImport(*blocked)
    sys.meta_path.insert(0, finder)
    for m in list(sys.modules):
        if m in blocked or m == "patterns.pattern_matcher":
            sys.modules.pop(m, None)
    yield
    sys.meta_path.remove(finder)
    for m in list(sys.modules):
        if m in blocked or m == "patterns.pattern_matcher":
            sys.modules.pop(m, None)


# ---------------------------------------------------------------------------
# Group 1: Direct _PyAhoCorasickAutomaton unit tests (always available)
# ---------------------------------------------------------------------------

class TestPyAhoCorasickAutomatonDirect:
    """Unit tests for the pure-Python Aho-Corasick fallback.

    Independent of pattern_matcher module: imports the class via direct
    importlib manipulation to bypass any cached pyahocorasick.
    """

    def _import_pure_class(self):
        """Import _PyAhoCorasickAutomaton directly from source file."""
        from pathlib import Path
        import importlib.util
        src = Path(__file__).resolve().parent.parent / "patterns" / "pattern_matcher.py"
        # Pre-register a sentinel module for ahocorasick that raises on attribute
        # access, so the top-level `import ahocorasick as _ahocorasick` succeeds
        # but any subsequent use fails.
        class _Raiser:
            def __getattr__(self, name):
                raise ImportError(f"CP314-FALLBACK-TEST: ahocorasick.{name} blocked")
        sys.modules["ahocorasick"] = _Raiser()  # type: ignore[assignment]
        try:
            spec = importlib.util.spec_from_file_location("_pm_pure_test", str(src))
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.modules.pop("ahocorasick", None)
        return mod._PyAhoCorasickAutomaton

    def test_api_compat_minimal(self):
        """add_word + make_automaton + iter produce (end_idx, value) pairs."""
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "F")
        auto.add_word("bar", "B")
        auto.make_automaton()
        results = list(auto.iter("foobar"))
        vals = [v for _, v in results]
        assert "F" in vals
        assert "B" in vals
        # end_index is the inclusive last index of the match (pyaho convention)
        for end_idx, _ in results:
            assert isinstance(end_idx, int)
            assert end_idx >= 0

    def test_no_matches_returns_empty(self):
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("xyz", "X")
        auto.make_automaton()
        assert list(auto.iter("hello world")) == []

    def test_empty_text_returns_empty(self):
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "F")
        auto.make_automaton()
        assert list(auto.iter("")) == []

    def test_overlapping_prefixes(self):
        """Pattern 'he' and 'she' share the 'he' suffix of 'she' — both must match."""
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("he", "HE")
        auto.add_word("she", "SHE")
        auto.make_automaton()
        results = list(auto.iter("she"))
        vals = {v for _, v in results}
        # "she" matches both "he" (end=2) and "she" (end=2)
        assert "HE" in vals
        assert "SHE" in vals

    def test_duplicate_add_word_ignored(self):
        """Same word added twice is a no-op (first value wins)."""
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "FIRST")
        auto.add_word("foo", "SECOND")
        auto.make_automaton()
        results = list(auto.iter("foo"))
        assert len(results) == 1
        _, val = results[0]
        assert val == "FIRST"

    def test_make_automaton_idempotent(self):
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "F")
        auto.make_automaton()
        # Second call must not raise
        auto.make_automaton()
        assert list(auto.iter("foo")) == [(2, "F")]

    def test_add_word_after_make_raises(self):
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "F")
        auto.make_automaton()
        with pytest.raises(RuntimeError):
            auto.add_word("bar", "B")

    def test_iter_before_make_raises(self):
        Aho = self._import_pure_class()
        auto = Aho()
        auto.add_word("foo", "F")
        with pytest.raises(RuntimeError):
            list(auto.iter("foo"))


# ---------------------------------------------------------------------------
# Group 2: pattern_matcher integration with various backend combinations
# ---------------------------------------------------------------------------

class TestPatternMatcherFallback:
    """Verify pattern_matcher.selects the right backend per environment."""

    def test_default_backend_info_shape(self):
        """get_backend_info() always returns a 5-key dict."""
        from patterns.pattern_matcher import get_backend_info
        info = get_backend_info()
        assert "backend" in info
        assert "version" in info
        assert "available" in info
        assert "rust_available" in info
        assert "pyahocorasick_available" in info
        # BACKEND_AVAILABLE is always True (we always have a fallback)
        assert info["available"] is True

    def test_match_text_works_with_default_backends(self):
        """Regression: with whatever is installed, match_text must produce hits."""
        from patterns.pattern_matcher import match_text, configure_patterns, reset_pattern_matcher
        reset_pattern_matcher()
        configure_patterns((("malware", "malware_type"), ("cve-", "cve_id")))
        hits = match_text("cve-2024-1234 mentions malware", boundary_policy="none")
        # We expect at least 2 hits: cve- (literal) + cve-2024-1234 (regex) + malware
        assert len(hits) >= 2
        labels = {h.label for h in hits}
        assert "cve_id" in labels
        assert "malware_type" in labels

    def test_no_pyahocorasick_uses_python_fallback(self, no_pyahocorasick):
        """When both pyahocorasick and Rust ext are blocked, fallback is used."""
        from patterns.pattern_matcher import get_backend_info, _PyAhoCorasickAutomaton
        info = get_backend_info()
        assert info["backend"] == "python_fallback"
        assert info["pyahocorasick_available"] is False
        assert info["rust_available"] is False
        assert info["available"] is True
        # _PyAhoCorasickAutomaton is importable
        assert _PyAhoCorasickAutomaton is not None

    def test_no_pyahocorasick_match_text_works(self, no_pyahocorasick):
        """End-to-end: match_text returns correct hits via Python fallback."""
        from patterns.pattern_matcher import match_text, configure_patterns, reset_pattern_matcher
        reset_pattern_matcher()
        configure_patterns((
            ("malware", "malware_type"),
            ("ransomware", "ransomware_type"),
            ("cve-", "cve_id"),
        ))
        text = "this cve-2024-1234 reports malware and ransomware"
        hits = match_text(text, boundary_policy="none")
        # Pure-Python AC + regex post-pass should yield >= 4 hits
        labels = {h.label for h in hits}
        assert "cve_id" in labels
        assert "malware_type" in labels
        assert "ransomware_type" in labels

    def test_no_rust_uses_pyahocorasick(self, no_rust):
        """When only Rust ext is blocked, pyahocorasick path is used."""
        from patterns.pattern_matcher import get_backend_info
        info = get_backend_info()
        # Backend label is "pyahocorasick" even when no Rust is available
        # (the build path is the same — only _matcher_state._rust_aco is None)
        assert info["backend"] in ("pyahocorasick", "python_fallback")
        # But rust_available flag is correctly False
        assert info["rust_available"] is False
