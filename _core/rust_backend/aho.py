# aho.py — Aho-Corasick domain
"""
Aho-Corasick multi-pattern string matching.
Used for IOC extraction from unstructured text.


"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# Optional import for Python fallback
try:
    import ahocorasick

    _AHOCORASICK_AVAILABLE = True
except ImportError:
    _AHOCORASICK_AVAILABLE = False


class _RustAhoDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def AhoCorasickMatcher(self, patterns: list[str], labels: list[str] | None = None) -> Any:
        """Build Aho-Corasick automaton from patterns."""
        return self._ext.aho_matcher_new(patterns, labels)

    def aho_search(self, matcher: Any, text: str) -> list[tuple[int, int, str]]:
        """Search for all pattern matches in text. Returns list of (start, end, label)."""
        return self._ext.aho_search(matcher, text)


class _PythonAhoDomain:
    __slots__ = ()

    def AhoCorasickMatcher(self, patterns: list[str]) -> _PythonAhoCorasick:
        """Build Python Aho-Corasick automaton."""
        return _PythonAhoCorasick(patterns)

    def aho_search(self, matcher: _PythonAhoCorasick, text: str) -> list[tuple[int, int, str]]:
        """Search for all pattern matches in text."""
        return matcher.search(text)


class _PythonAhoCorasick:
    """Python fallback for Aho-Corasick using ahocorasick library."""

    __slots__ = ("_automaton", "_patterns")

    def __init__(self, patterns: list[str]) -> None:
        self._patterns = patterns
        if _AHOCORASICK_AVAILABLE:
            self._automaton = ahocorasick.Automaton()
            for i, pattern in enumerate(patterns):
                self._automaton.add_word(pattern, (i, pattern))
            self._automaton.make_automaton()
        else:
            # Pure Python fallback: simple substring search
            self._automaton = None

    def search(self, text: str) -> list[tuple[int, int, str]]:
        """Search for patterns in text."""
        if self._automaton is not None:
            # Use ahocorasick library
            results: list[tuple[int, int, str]] = []
            for end, (_idx, pattern) in self._automaton.iter(text):
                start = end - len(pattern) + 1
                results.append((start, end + 1, pattern))
            return results
        else:
            # Pure Python fallback: O(n*m) substring search
            results: list[tuple[int, int, str]] = []
            for _i, pattern in enumerate(self._patterns):
                start = 0
                while True:
                    pos = text.find(pattern, start)
                    if pos == -1:
                        break
                    results.append((pos, pos + len(pattern), pattern))
                    start = pos + 1
            return sorted(results, key=lambda x: x[0])


def get_aho_domain(ext: object | None) -> _RustAhoDomain | _PythonAhoDomain:
    """Factory: return Rust or Python AhoDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustAhoDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonAhoDomain()
