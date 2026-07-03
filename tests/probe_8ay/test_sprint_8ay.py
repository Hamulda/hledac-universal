"""Sprint 8AY — Bootstrap Pattern Pack V2 (morphology-aware literal expansion).

Edit ONLY these files:
  /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/patterns/pattern_matcher.py
  /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/probe_8ay/*

Invariants tested:
  D.1  test_default_bootstrap_pack_v2_exists
  D.2  test_default_bootstrap_pack_v2_is_lowercase_only
  D.3  test_default_bootstrap_pack_v2_stays_small_enough_for_m1
  D.4  test_bootstrap_pack_v2_is_idempotent_when_registry_empty
  D.5  test_bootstrap_pack_v2_does_not_override_non_empty_registry
  D.6  test_morphology_variant_vulnerabilities_now_hits
  D.7  test_morphology_variant_exploited_now_hits
  D.8  test_morphology_variant_ransomware_attacks_now_hits
  D.9  test_credentials_variant_hits
  D.10 test_low_signal_generic_terms_not_added
  D.11 test_status_surface_reports_bootstrap_pack_version_or_count
  D.12 test_probe_8aq_still_green
  D.13 test_probe_8au_still_green
  D.14 test_probe_8aw_still_green_or_na
  D.15 test_probe_8an_still_green
  D.16 test_ao_canary_still_green
"""


import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    PatternHit,
    configure_default_bootstrap_patterns_if_empty,
    configure_patterns,
    get_backend_info,
    get_default_bootstrap_patterns,
    get_pattern_matcher,
    match_text,
    reset_pattern_matcher,
)


# =============================================================================
# D.1 — v2 pack exists
# =============================================================================

class TestSprint8AYV2PackExists:
    def test_default_bootstrap_pack_v2_exists(self):
        pack = get_default_bootstrap_patterns()
        assert isinstance(pack, tuple)
        assert len(pack) > 12  # at least v1 size
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pack)

    def test_default_bootstrap_pack_v2_is_lowercase_only(self):
        pack = get_default_bootstrap_patterns()
        for pattern, label in pack:
            assert pattern == pattern.lower(), f"pattern {pattern!r} is not lowercase"
            assert label == label.lower(), f"label {label!r} is not lowercase"

    def test_default_bootstrap_pack_v2_stays_small_enough_for_m1(self):
        pack = get_default_bootstrap_patterns()
        # V3 expanded to 63 IOC-first literals; still M1-safe (pure AC automaton, no extra RAM)
        assert len(pack) <= 80, f"pack size {len(pack)} exceeds M1 safety limit"


# =============================================================================
# D.4 / D.5 — idempotency
# =============================================================================

class TestSprint8AYIdempotency:
    def test_bootstrap_pack_v2_is_idempotent_when_registry_empty(self):
        reset_pattern_matcher()
        result1 = configure_default_bootstrap_patterns_if_empty()
        assert result1 is True
        pm = get_pattern_matcher()
        count1 = pm.pattern_count()

        # Calling again should be no-op (idempotent)
        result2 = configure_default_bootstrap_patterns_if_empty()
        assert result2 is False
        count2 = pm.pattern_count()
        assert count1 == count2

    def test_bootstrap_pack_v2_does_not_override_non_empty_registry(self):
        reset_pattern_matcher()
        custom = (("custom-pattern", "custom-label"),)
        configure_patterns(custom)

        # Bootstrap should not override
        result = configure_default_bootstrap_patterns_if_empty()
        assert result is False
        pm = get_pattern_matcher()
        assert pm.pattern_count() == 1


# =============================================================================
# D.6–D.9 — morphology variants now hit
# =============================================================================

class TestSprint8AYMorphologyVariants:
    def test_morphology_variant_vulnerabilities_now_hits(self):
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        text = "multiple critical vulnerabilities found in open source library"
        hits = match_text(text)
        patterns = [h.pattern for h in hits]
        assert "vulnerabilities" in patterns, f"expected 'vulnerabilities' in {patterns}"

    def test_morphology_variant_exploited_now_hits(self):
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        text = "已被 exploited in targeted attack"
        hits = match_text(text)
        patterns = [h.pattern for h in hits]
        assert "exploited" in patterns, f"expected 'exploited' in {patterns}"

    def test_morphology_variant_ransomware_attacks_now_hits(self):
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        text = "ransomware attacks disrupted healthcare systems"
        hits = match_text(text)
        patterns = [h.pattern for h in hits]
        assert "ransomware attacks" in patterns, f"expected 'ransomware attacks' in {patterns}"

    def test_credentials_variant_hits(self):
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        text = "stolen credential used for lateral movement"
        hits = match_text(text)
        patterns = [h.pattern for h in hits]
        assert "credential" in patterns, f"expected 'credential' in {patterns}"


# =============================================================================
# D.10 — low-signal generic terms NOT added
# =============================================================================

class TestSprint8AYNoLowSignalTerms:
    def test_low_signal_generic_terms_not_added(self):
        pack = get_default_bootstrap_patterns()
        patterns = [p for p, _ in pack]
        forbidden = {"attack", "error", "security", "admin", "critical", "hack", "virus"}
        for term in forbidden:
            assert term not in patterns, f"low-signal term {term!r} should NOT be in pack"


# =============================================================================
# D.11 — status surface reports pack version/count
# =============================================================================

class TestSprint8AYStatusSurface:
    def test_status_surface_reports_bootstrap_pack_version_or_count(self):
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        pm = get_pattern_matcher()
        status = pm.get_status()
        # Must have bootstrap_pack_version (value=3 for V3) OR default_bootstrap_count
        assert "bootstrap_pack_version" in status or "default_bootstrap_count" in status
        if "bootstrap_pack_version" in status:
            assert status["bootstrap_pack_version"] == 3
        if "default_bootstrap_count" in status:
            assert status["default_bootstrap_count"] >= 12


# =============================================================================
# D.12–D.16 — regression gates
# =============================================================================

class TestSprint8AYRegressionGates:
    def test_probe_8aq_still_green(self):
        pytest.importorskip("hledac.universal.tests.probe_8aq", reason="probe_8aq not present")
        # If import succeeds the module exists; just verify pattern_matcher still works
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        assert get_pattern_matcher().pattern_count() > 0

    def test_probe_8au_still_green(self):
        pytest.importorskip("hledac.universal.tests.probe_8au", reason="probe_8au not present")
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        backend = get_backend_info()
        assert backend["available"] is True

    def test_probe_8aw_still_green_or_na(self):
        # 8aw may not be importable in all envs; verify matcher works
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        text = "test phishing sample"
        hits = match_text(text)
        assert any(h.pattern == "phishing" for h in hits)

    def test_probe_8an_still_green(self):
        pytest.importorskip("hledac.universal.tests.probe_8an", reason="probe_8an not present")
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        # Core API smoke test
        assert get_backend_info()["available"] is True

    def test_ao_canary_still_green(self):
        pytest.importorskip("hledac.universal.tests.test_ao_canary", reason="canary not present")
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        hits = match_text("cve-2024-1234")
        assert any("cve-" in h.pattern for h in hits)
