"""
Sprint F235D: Source Family Canonicalization — Standalone Unit Tests

Run via: python -m pytest tests/test_sprint66/test_source_family_canonicalization.py -v
         (or pytest ... -v from .venv-py3135)
"""
from __future__ import annotations

import sys

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

from hledac.universal.runtime.acquisition_strategy import (
    canonicalize_source_family_outcomes,
    normalize_source_family_name,
    normalize_source_family_outcome,
)


class TestNormalizeSourceFamilyName:
    """Assertion 1-4: normalize_source_family_name correctness."""

    def test_ct_variants(self):
        assert normalize_source_family_name("CT") == "ct"
        assert normalize_source_family_name("ct") == "ct"
        assert normalize_source_family_name("Ct") == "ct"
        assert normalize_source_family_name("CT_LOG") == "ct"

    def test_public_variants(self):
        assert normalize_source_family_name("PUBLIC") == "public"
        assert normalize_source_family_name("public") == "public"
        assert normalize_source_family_name("Public") == "public"

    def test_passive_dns_variants(self):
        assert normalize_source_family_name("PASSIVE_DNS") == "passive_dns"
        assert normalize_source_family_name("passive_dns") == "passive_dns"
        assert normalize_source_family_name("passivedns") == "passive_dns"
        assert normalize_source_family_name("passive-dns") == "passive_dns"

    def test_feed_variants(self):
        assert normalize_source_family_name("FEED") == "feed"
        assert normalize_source_family_name("feed") == "feed"

    def test_other_families(self):
        assert normalize_source_family_name("wayback") == "wayback"
        assert normalize_source_family_name("academic") == "academic"
        assert normalize_source_family_name("ipfs") == "ipfs"
        assert normalize_source_family_name("pivot") == "pivot"
        assert normalize_source_family_name("blockchain") == "blockchain"

    def test_unknown_unchanged(self):
        assert normalize_source_family_name("unknown") == "unknown"
        assert normalize_source_family_name("xyz") == "xyz"
        assert normalize_source_family_name(None) == "unknown"
        assert normalize_source_family_name(123) == "unknown"


class TestCanonicalizeSourceFamilyOutcomes:
    """Assertion 5-8: canonicalize dedup and merge logic."""

    def test_no_op_empty(self):
        assert canonicalize_source_family_outcomes([]) == []
        assert canonicalize_source_family_outcomes(None) == []

    def test_single_entry_unchanged(self):
        outcomes = [{"family": "ct", "attempted": True, "skipped": False,
                     "raw_count": 5, "accepted_count": 3, "error": None}]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["family"] == "ct"

    def test_dedup_ct_ct_merge(self):
        """Assertion 6: CT + ct → single ct with merged state."""
        outcomes = [
            {"family": "CT", "attempted": False, "skipped": True,
             "raw_count": 0, "accepted_count": 0,
             "error": "no_candidates", "terminal_state": "SKIPPED"},
            {"family": "ct", "attempted": True, "skipped": False,
             "raw_count": 0, "accepted_count": 0,
             "error": "http_502", "timeout": False, "terminal_state": "ATTEMPTED_ERROR"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1, f"Expected 1 merged ct, got {len(result)}: {result}"
        assert result[0]["family"] == "ct"
        assert result[0]["attempted"] is True, "attempted=True must win"
        assert result[0]["skipped"] is False
        assert result[0]["error"] == "http_502", "provider error must win over synthetic"
        assert result[0]["terminal_state"] == "ATTEMPTED_ERROR"

    def test_error_priority_http_502_over_no_candidates(self):
        """Assertion 7: real provider error wins over synthetic."""
        outcomes = [
            {"family": "CT", "attempted": True, "skipped": False,
             "error": "no_candidates", "terminal_state": "ATTEMPTED_NO_RESULTS"},
            {"family": "ct", "attempted": True, "skipped": False,
             "error": "http_502", "terminal_state": "ATTEMPTED_ERROR"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["error"] == "http_502"

    def test_attempted_true_wins_over_false(self):
        """Assertion 8: attempted=True wins over False."""
        outcomes = [
            {"family": "CT", "attempted": False, "skipped": True,
             "terminal_state": "SKIPPED", "error": "no_candidates"},
            {"family": "ct", "attempted": True, "skipped": False,
             "terminal_state": "ATTEMPTED_ERROR", "error": "http_502"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert result[0]["attempted"] is True

    def test_timeout_wins_over_generic_error(self):
        """Assertion 9: timeout=True wins over generic error."""
        outcomes = [
            {"family": "public", "attempted": True, "skipped": False,
             "error": "http_502", "timeout": False, "terminal_state": "ATTEMPTED_ERROR"},
            {"family": "PUBLIC", "attempted": True, "skipped": False,
             "error": "timeout", "timeout": True, "terminal_state": "ATTEMPTED_TIMEOUT"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["timeout"] is True
        assert result[0]["terminal_state"] == "ATTEMPTED_TIMEOUT"

    def test_public_discovery_error_preserved(self):
        """Assertion 10: public DISCOVERY_ERROR outcome preserved."""
        outcomes = [
            {"family": "public", "attempted": True, "skipped": False,
             "error": "DISCOVERY_ERROR", "timeout": False,
             "terminal_state": "ATTEMPTED_ERROR", "raw_count": 0, "accepted_count": 0},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["family"] == "public"
        assert result[0]["error"] == "DISCOVERY_ERROR"

    def test_feed_accepted_outcome_preserved(self):
        """Assertion 11: feed accepted outcome preserved."""
        outcomes = [
            {"family": "feed", "attempted": True, "skipped": False,
             "raw_count": 100, "accepted_count": 42,
             "terminal_state": "ATTEMPTED_ACCEPTED"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["family"] == "feed"
        assert result[0]["accepted_count"] == 42

    def test_count_max_not_sum(self):
        outcomes = [
            {"family": "ct", "attempted": True, "raw_count": 10, "accepted_count": 3},
            {"family": "CT", "attempted": True, "raw_count": 20, "accepted_count": 7},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert result[0]["raw_count"] == 20
        assert result[0]["accepted_count"] == 7  # max, not sum

    def test_duration_s_max(self):
        outcomes = [
            {"family": "ct", "attempted": True, "duration_s": 1.5},
            {"family": "CT", "attempted": True, "duration_s": 3.2},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert result[0]["duration_s"] == 3.2

    def test_mixed_case_same_family(self):
        outcomes = [
            {"family": "Public", "attempted": True, "terminal_state": "ATTEMPTED_ACCEPTED"},
            {"family": "PUBLIC", "attempted": True, "terminal_state": "SKIPPED"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        assert result[0]["family"] == "public"
        assert result[0]["terminal_state"] == "ATTEMPTED_ACCEPTED"


class TestCTDuplicateMergedOutcome:
    """CT-specific required result from sprint spec."""

    def test_ct_ct_duplicate_required_result(self):
        outcomes = [
            {"family": "CT", "attempted": False, "skipped": True,
             "raw_count": 0, "accepted_count": 0,
             "error": "no_candidates", "terminal_state": "SKIPPED"},
            {"family": "ct", "attempted": True, "skipped": False,
             "raw_count": 0, "accepted_count": 0,
             "error": "http_502", "timeout": False, "terminal_state": "ATTEMPTED_ERROR"},
        ]
        result = canonicalize_source_family_outcomes(outcomes)
        assert len(result) == 1
        r = result[0]
        assert r["family"] == "ct"
        assert r["attempted"] is True
        assert r["skipped"] is False
        assert r["raw_count"] == 0
        assert r["accepted_count"] == 0
        assert r["error"] == "http_502"
        assert r["terminal_state"] == "ATTEMPTED_ERROR"


class TestLiveReportFixture:
    """Assertion 12: live report with CT+ct normalizes to exactly one ct."""

    def test_live_report_ct_ct_fixture(self):
        live_outcomes = [
            {"family": "FEED", "attempted": True, "skipped": False,
             "raw_count": 50, "accepted_count": 15,
             "terminal_state": "ATTEMPTED_ACCEPTED"},
            {"family": "PUBLIC", "attempted": True, "skipped": False,
             "raw_count": 5, "accepted_count": 2,
             "terminal_state": "ATTEMPTED_ACCEPTED"},
            {"family": "CT", "attempted": False, "skipped": True,
             "raw_count": 0, "accepted_count": 0,
             "error": "no_candidates", "terminal_state": "SKIPPED"},
            {"family": "ct", "attempted": True, "skipped": False,
             "raw_count": 0, "accepted_count": 0,
             "error": "http_502", "terminal_state": "ATTEMPTED_ERROR"},
            {"family": "wayback", "attempted": False, "skipped": True,
             "terminal_state": "SKIPPED_BY_POLICY"},
        ]
        result = canonicalize_source_family_outcomes(live_outcomes)
        families = [o["family"] for o in result]
        ct_count = sum(1 for f in families if f == "ct")
        assert ct_count == 1, f"Expected exactly 1 'ct', got {ct_count}: {result}"
        assert "CT" not in families, f"'CT' must not appear as separate entry: {families}"


class TestNormalizeSourceFamilyOutcomeUsesCanonicalFamily:
    """normalize_source_family_outcome() sets family to canonical lowercase form."""

    def test_normalize_uses_lowercase_family(self):
        result = normalize_source_family_outcome("CT", {
            "attempted": True, "skipped": False, "raw_count": 5,
            "accepted_count": 3, "error": "http_502"
        })
        assert result["family"] == "ct"

    def test_normalize_wayback_lowercase(self):
        result = normalize_source_family_outcome("WAYBACK", {
            "attempted": True, "skipped": False,
        })
        assert result["family"] == "wayback"

    def test_normalize_public_lowercase(self):
        result = normalize_source_family_outcome("PUBLIC", {
            "attempted": True, "skipped": False,
        })
        assert result["family"] == "public"


class TestEvidenceDeltaMemoryDownstream:
    """Assertion 14: evidence_delta_memory sees ct attempted=True via lowercase check."""

    def test_get_ct_public_info_ct_attempted_true(self):
        from tools.evidence_delta_memory import _get_ct_public_info
        kpi = {
            "acquisition_report": {
                "source_family_outcomes": [
                    {"family": "CT", "attempted": False, "terminal_state": "SKIPPED"},
                    {"family": "ct", "attempted": True, "terminal_state": "ATTEMPTED_ERROR"},
                ]
            }
        }
        ct_att, _ = _get_ct_public_info(kpi)
        assert ct_att is True


class TestLiveArtifactTriageDownstream:
    """Assertion 15: live_artifact_triage reads canonical family field case-insensitively."""

    def test_has_ct_lowercase(self):
        from tools.live_artifact_triage import _has_ct
        data = {
            "live_kpi": {
                "source_family_outcomes": [
                    {"family": "ct", "attempted": True, "accepted": 5},
                ]
            }
        }
        assert _has_ct(data) is True

    def test_has_ct_uppercase_normalized(self):
        from tools.live_artifact_triage import _has_ct
        data = {
            "live_kpi": {
                "source_family_outcomes": [
                    {"family": "CT", "attempted": True, "accepted": 5},
                ]
            }
        }
        assert _has_ct(data) is True

    def test_has_feed_canonical_family_key(self):
        """_has_feed must read 'family' key (F235D canonical), not legacy 'source_family'."""
        from tools.live_artifact_triage import _has_feed
        data = {
            "live_kpi": {
                "source_family_outcomes": [
                    {"family": "feed", "attempted": True, "accepted": 5},
                ]
            }
        }
        assert _has_feed(data) is True

    def test_has_feed_legacy_source_family_fallback(self):
        """_has_feed must also handle legacy 'source_family' key for backward compat."""
        from tools.live_artifact_triage import _has_feed
        data = {
            "live_kpi": {
                "source_family_outcomes": [
                    {"source_family": "feed", "attempted": True, "accepted": 5},
                ]
            }
        }
        assert _has_feed(data) is True
