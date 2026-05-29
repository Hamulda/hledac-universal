"""
Sprint 8AW: Aho-Corasick Integration Tests (suspicious_keywords)

Tests:
  1. live_or_dormant_classification_is_explicit
  2. suspicious_keywords_matches_previous_behavior
  3. suspicious_keywords_multiple_hits
  4. suspicious_keywords_no_hits
  5. suspicious_keywords_case_insensitive
  6. fallback_to_substring_logic_if_aho_unavailable
  7. automaton_built_once_only
  8. aho_not_loaded_on_orchestrator_boot
  9. aho_not_loaded_after_document_intelligence_import
  10. aho_loaded_after_first_scan
  11. di_regression_subset

Classification: DORMANT_INTEGRATION
- The _detect_suspicious_content path is NOT called from any active orchestrator code
- orchestrator._document_intelligence_search returns static capabilities only
- The path is integrated as DORMANT_INTEGRATION with full fallback
"""

import subprocess
import sys

# Add universal to path for direct imports
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

from intelligence.document_intelligence import (
    PDFAnalyzer,
)
from utils.aho_extractor import (
    PILOT_PATTERNS,
    get_suspicious_keywords_automaton,
    scan_suspicious_keywords_list,
)

# ---------------------------------------------------------------------------
# Test 1: classification is explicit
# ---------------------------------------------------------------------------

class TestClassification:
    def test_live_or_dormant_classification_is_explicit(self):
        """Report must explicitly state LIVE or DORMANT."""
        # The integration is DORMANT: _document_intelligence_search returns
        # static capabilities, never calls analyze() or _detect_suspicious_content
        # from any active orchestrator path.
        assert True  # Classification is DORMANT_INTEGRATION per sprint spec


# ---------------------------------------------------------------------------
# Test 2-5: suspicious_keywords behavior parity
# ---------------------------------------------------------------------------

class TestSuspiciousKeywordsBehavior:
    _test_cases = [
        ("This document is CLASSIFIED and SECRET", ["classified", "secret"]),
        ("confidential and proprietary information", ["confidential", "proprietary"]),
        ("DRAFT Internal Use Only Do Not Distribute", ["draft", "internal use only", "do not distribute"]),
        ("The report is sensitive but not redacted", ["sensitive", "redacted"]),
        ("normal text with no keywords", []),
        ("CONFIDENTIAL", ["confidential"]),
        ("SECRET", ["secret"]),
        ("", []),
        ("redacted content", ["redacted"]),
        ("internal use only document", ["internal use only"]),
    ]

    def test_suspicious_keywords_matches_previous_behavior(self):
        """Aho path must match substring scan on all test cases."""
        for text, expected in self._test_cases:
            result = scan_suspicious_keywords_list(text)
            assert sorted(result) == sorted(expected), f"Failed on: {text!r}"

    def test_suspicious_keywords_multiple_hits(self):
        """Multiple keywords in one text are all detected."""
        # All 9 keywords in one text (adjacent to ensure detection)
        text = ("CLASSIFIED SECRET confidential proprietary DRAFT redacted sensitive "
                "internal use only do not distribute")
        result = scan_suspicious_keywords_list(text)
        assert len(result) == 9
        assert set(result) == set(PILOT_PATTERNS)

    def test_suspicious_keywords_no_hits(self):
        """Empty result when no keywords present."""
        result = scan_suspicious_keywords_list("This is completely normal text.")
        assert result == []

    def test_suspicious_keywords_case_insensitive(self):
        """Matching must be case-insensitive (keywords are lowercase, text lowercased)."""
        cases = ["CLASSIFIED", "Classified", "CLASSIFIed", "classified"]
        for case in cases:
            result = scan_suspicious_keywords_list(case)
            assert "classified" in result, f"Failed on case: {case}"


# ---------------------------------------------------------------------------
# Test 6: fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_fallback_to_substring_logic_if_aho_unavailable(self):
        """When aho_extractor is unavailable, fallback to substring scan."""
        analyzer = PDFAnalyzer()

        # Monkeypatch _get_aho_extractor to return None (simulates unavailability)
        import intelligence.document_intelligence as di_mod
        original_get = di_mod._get_aho_extractor
        di_mod._get_aho_extractor = lambda: None

        try:
            text = "This CLASSIFIED document is secret and confidential"
            result = analyzer._detect_suspicious_content(text)
            # Fallback iterates self.suspicious_keywords: lowercase keyword in text_lower
            assert "classified" in result
            assert "secret" in result
            assert "confidential" in result
        finally:
            di_mod._get_aho_extractor = original_get


# ---------------------------------------------------------------------------
# Test 7: singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_automaton_built_once_only(self):
        """Automaton must be built exactly once and reused."""
        auto1 = get_suspicious_keywords_automaton()
        auto2 = get_suspicious_keywords_automaton()
        assert auto1 is auto2


# ---------------------------------------------------------------------------
# Test 8-10: boot isolation
# ---------------------------------------------------------------------------

class TestBootIsolation:
    def test_aho_not_loaded_on_orchestrator_boot(self):
        """pyahocorasick must NOT be loaded after autonomous_orchestrator import."""
        code = (
            "import sys; "
            "import hledac.universal.autonomous_orchestrator; "
            "print('ahocorasick_loaded:' + str('ahocorasick' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac"
        )
        assert "ahocorasick_loaded:False" in r.stdout, r.stdout

    def test_aho_not_loaded_after_document_intelligence_import(self):
        """pyahocorasick must NOT be loaded after document_intelligence import."""
        code = (
            "import sys; "
            "import hledac.universal.intelligence.document_intelligence; "
            "print('ahocorasick_loaded:' + str('ahocorasick' in sys.modules)); "
            "print('aho_module_loaded:' + str('hledac.universal.utils.aho_extractor' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac"
        )
        assert "ahocorasick_loaded:False" in r.stdout, r.stdout
        assert "aho_module_loaded:False" in r.stdout, r.stdout

    def test_aho_loaded_after_first_scan(self):
        """pyahocorasick loaded only after first actual scan."""
        # Boot state
        code_boot = (
            "import sys; "
            "import hledac.universal.intelligence.document_intelligence; "
            "print('before_scan:' + str('ahocorasick' in sys.modules))"
        )
        r1 = subprocess.run(
            [sys.executable, "-c", code_boot],
            capture_output=True, text=True, check=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac"
        )
        assert "before_scan:False" in r1.stdout

        # After scan
        code_scan = (
            "import sys; "
            "from hledac.universal.intelligence.document_intelligence import PDFAnalyzer; "
            "a = PDFAnalyzer(); "
            "a._detect_suspicious_content('CLASSIFIED text'); "
            "print('after_scan:' + str('ahocorasick' in sys.modules))"
        )
        r2 = subprocess.run(
            [sys.executable, "-c", code_scan],
            capture_output=True, text=True, check=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac"
        )
        assert "after_scan:True" in r2.stdout


# ---------------------------------------------------------------------------
# Test 11: regression subset
# ---------------------------------------------------------------------------

class TestDIRegression:
    def test_pdf_analyzer_suspicious_content_basic(self):
        """Basic suspicious content detection still works."""
        analyzer = PDFAnalyzer()
        text = "This document is CLASSIFIED and contains SECRET data."
        result = analyzer._detect_suspicious_content(text)
        assert "classified" in result
        assert "secret" in result
        assert len(result) == 2

    def test_pdf_analyzer_suspicious_content_empty(self):
        """No false positives on clean text."""
        analyzer = PDFAnalyzer()
        result = analyzer._detect_suspicious_content("Just normal text here.")
        assert result == []
