"""
Sprint 8AU: Aho-Corasick Shadow Pilot Tests

Tests:
  1. automaton cached singleton (built once, reused)
  2. non-overlapping pattern set verified
  3. output normalization (exclusive end)
  4. matches ground-truth substring scan on representative cases
  5. handles no-match cases
  6. handles multiple matches in one pass
  7. pyahocorasick NOT imported on orchestrator boot
  8. aho module NOT imported on orchestrator boot
  9. A/B parity on deterministic inputs
  10. benchmark: build time + scan time vs regex
"""

import subprocess
import sys
import time

import pytest

# Import the shadow module directly (not via hledac package path)
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
from utils.aho_extractor import (
    PILOT_PATTERNS,
    aho_scan_text,
    compare_aho_vs_regex,
    get_suspicious_keywords_automaton,
    normalize_aho_match,
    regex_scan_suspicious_keywords,
)

# Module reference for cache manipulation in benchmark
aho_module = sys.modules["utils.aho_extractor"]


# ---------------------------------------------------------------------------
# Test 1: singleton automaton — built once, reused
# ---------------------------------------------------------------------------

class TestAutomatonCaching:
    def test_automaton_is_cached_singleton(self):
        """Automaton must be built exactly once and reused."""
        auto1 = get_suspicious_keywords_automaton()
        auto2 = get_suspicious_keywords_automaton()
        assert auto1 is auto2, "Automaton must be the same cached object"


# ---------------------------------------------------------------------------
# Test 2: non-overlapping pattern set
# ---------------------------------------------------------------------------

class TestPatternSetNonOverlap:
    def test_pattern_subset_is_non_overlapping(self):
        """
        The 9 pilot patterns must not contain prefix collisions
        at the same position (no pattern is a prefix of another).
        """
        for i, p1 in enumerate(PILOT_PATTERNS):
            for p2 in PILOT_PATTERNS[i + 1 :]:
                # neither should be a prefix of the other
                assert not p2.startswith(p1), f"{p2!r} starts with {p1!r}"
                assert not p1.startswith(p2), f"{p1!r} starts with {p2!r}"


# ---------------------------------------------------------------------------
# Test 3: output normalization (exclusive end)
# ---------------------------------------------------------------------------

class TestOutputNormalization:
    def test_normalize_aho_match_exclusive_end(self):
        """Normalized match must have exclusive end (end = start + len(match))."""
        # "classified" at position 5 in "The classified doc"
        norm = normalize_aho_match(14, "classified")
        assert norm["start"] == 5
        assert norm["end"] == 15  # 5 + len("classified") = 5 + 10 = 15 (EXCLUSIVE)
        assert norm["match"] == "classified"

    def test_regex_span_parity(self):
        """Normalized output must match regex .span() convention (exclusive end)."""
        text = "This document is classified and secret"
        aho = aho_scan_text(get_suspicious_keywords_automaton(), text)

        for m in aho:
            # Extract the matched text from original
            extracted = text[m["start"] : m["end"]]
            assert extracted == m["match"], f"Mismatch at {m['start']}:{m['end']}"
            # Exclusive end check
            assert m["end"] > m["start"]


# ---------------------------------------------------------------------------
# Test 4: parity with ground-truth substring scan
# ---------------------------------------------------------------------------

class TestParityWithGroundTruth:
    @pytest.mark.parametrize(
        "text",
        [
            "This document is classified and secret",
            "The file is marked confidential — do not distribute",
            "A draft report marked proprietary was leaked",
            "Nothing suspicious here, just normal text",
            "SECRET and CONFIDENTIAL in uppercase too",
            "Internal use only: the draft contains redacted text",
            "sensitive data — secret formula — classified info",
            "Multiple instances: confidential confidential",
            "Edge: classified is a substring of reclassified (not a full match)",
            "  spaces  and  internal use only  padding  ",
        ],
    )
    def test_aho_matches_regex_on_representative_cases(self, text):
        """Aho-Corasick must produce identical normalized output to substring scan."""
        _, _, are_identical = compare_aho_vs_regex(text)
        assert are_identical, f"Mismatch on: {text!r}"

    def test_aho_handles_no_match_cases(self):
        """No matches → empty list for both methods."""
        text = "This is completely normal content with no indicators"
        aho, regex, identical = compare_aho_vs_regex(text)
        assert aho == []
        assert regex == []
        assert identical is True

    def test_aho_handles_multiple_matches_in_one_pass(self):
        """Multiple distinct keywords in one text must all be found."""
        text = "The confidential report was marked classified and secret and sensitive"
        aho, regex, identical = compare_aho_vs_regex(text)
        assert len(aho) == 4, f"Expected 4 matches, got {len(aho)}: {aho}"
        assert identical is True


# ---------------------------------------------------------------------------
# Test 7-8: boot isolation
# ---------------------------------------------------------------------------

class TestBootIsolation:
    def test_py_ahocorasick_not_imported_on_orchestrator_boot(self):
        """pyahocorasick must NOT be imported when orchestrator boots."""
        code = (
            "import sys; "
            "import hledac.universal.autonomous_orchestrator; "
            "print(int('ahocorasick' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
        )
        lines = [l for l in r.stdout.strip().split("\n") if l]
        val = int(lines[-1])
        assert val == 0, f"ahocorasick was loaded during boot: {r.stdout}"

    def test_aho_extractor_module_not_imported_on_orchestrator_boot(self):
        """aho_extractor must NOT be imported when orchestrator boots."""
        code = (
            "import sys; "
            "import hledac.universal.autonomous_orchestrator; "
            "print(int('hledac.universal.utils.aho_extractor' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
        )
        lines = [l for l in r.stdout.strip().split("\n") if l]
        val = int(lines[-1])
        assert val == 0, f"aho_extractor was loaded during boot: {r.stdout}"


# ---------------------------------------------------------------------------
# Test 10: benchmark (scan time vs regex, build time)
# ---------------------------------------------------------------------------

class TestBenchmark:
    def test_benchmark_build_and_scan(self):
        """
        Benchmark automaton build time and scan time vs regex.

        Reports:
          - aho_build_time_ms
          - aho_scan_time_us_per_kb
          - regex_scan_time_us_per_kb
          - parity (must be True)
        """
        # --- Build time ---
        # Clear cache to measure fresh build
        aho_module._automaton_cache = None

        t0 = time.perf_counter()
        auto = get_suspicious_keywords_automaton()
        aho_build_ms = (time.perf_counter() - t0) * 1000

        # --- Scan time (vary text size) ---
        # Deterministic 1KB text
        base_text = (
            "This document is classified and confidential. "
            "It contains secret and proprietary information. "
            "Internal use only — do not distribute. "
            "The draft is marked sensitive and redacted. "
        )
        # ~175 chars, repeat to ~1KB
        text_1k = base_text * 6  # ~1050 chars

        # Aho scan (multiple iterations for measurable time)
        n_iters = 100
        t0 = time.perf_counter()
        for _ in range(n_iters):
            aho_scan_text(auto, text_1k)
        aho_scan_total_us = (time.perf_counter() - t0) * 1_000_000
        aho_scan_us_per_kb = aho_scan_total_us / n_iters / (len(text_1k) / 1024)

        # Regex scan (multiple iterations)
        t0 = time.perf_counter()
        for _ in range(n_iters):
            regex_scan_suspicious_keywords(text_1k)
        regex_scan_total_us = (time.perf_counter() - t0) * 1_000_000
        regex_scan_us_per_kb = regex_scan_total_us / n_iters / (len(text_1k) / 1024)

        # Verify parity on the test text
        _, _, parity = compare_aho_vs_regex(text_1k)

        print(
            f"\nBenchmark results:\n"
            f"  aho_build_time_ms:        {aho_build_ms:.3f} ms\n"
            f"  aho_scan_time_us_per_kb:  {aho_scan_us_per_kb:.1f} µs/KB\n"
            f"  regex_scan_time_us_per_kb: {regex_scan_us_per_kb:.1f} µs/KB\n"
            f"  scan speedup:              {regex_scan_us_per_kb / aho_scan_us_per_kb:.1f}x\n"
            f"  A/B parity:               {parity}"
        )

        assert parity is True, "Aho vs regex parity must hold"
        # Sanity: aho build should be < 50ms (M1)
        assert aho_build_ms < 50, f"Build time {aho_build_ms}ms seems too high"
        # Aho should be faster than N×regex for this pattern density
        # (not a hard requirement, just informational)

    def test_aho_module_lazy_import_flag(self):
        """ahocorasick module is loaded only on first scan call."""
        # Force fresh import to test lazy flag
        import importlib

        import utils.aho_extractor
        importlib.reload(utils.aho_extractor)

        # Before first call, the lazy module should be None
        assert utils.aho_extractor._AhoCorasickModule is None
        # After first call it should be set
        auto = get_suspicious_keywords_automaton()
        assert utils.aho_extractor._AhoCorasickModule is not None
        assert auto is not None
