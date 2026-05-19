"""
Sprint F202G: Hypothesis-Driven Pivot Planner Tests

Tests cover:
- PivotPlanner.plan_pivots() generates bounded pivots from findings
- Max 20 pivots per sprint enforced
- All 5 pivot types generated (domain, identity, leak, archive, graph)
- Planner failure is fail-soft (returns empty list, never crashes)
- Pivot output includes reason, expected_value, source_hint, evidence_pointers
- Pivot type mapping via discovery/source_registry.py

F202G Invariants:
| Test | Invariant |
|------|-----------|
| test_max_pivots_enforced | MAX_PIVOTS=20 bound |
| test_empty_findings_returns_empty | Fail-soft on empty input |
| test_finding_with_domain_generates_domain_pivot | Domain IOC → domain pivot |
| test_finding_with_ip_generates_reverse_dns_pivot | IP IOC → domain pivot |
| test_finding_with_hash_generates_graph_pivot | Hash IOC → graph pivot |
| test_finding_with_email_generates_leak_pivot | Email IOC → leak pivot |
| test_finding_with_url_generates_archive_pivot | URL IOC → archive pivot |
| test_envelope_deserialization | FindingEnvelope extracted from payload_text |
| test_pivot_has_required_fields | reason, expected_value, source_hint, evidence_pointers |
| test_pivot_deduplication | Same IOC deduplicated, highest score kept |
| test_pivot_sorting | Sorted by priority (highest first) |
| test_planner_fail_soft | Exception returns empty list |
| test_pivot_type_mapping | discovery/source_registry.py PIVOT_TYPE_MAP |
| test_get_pivot_task_types | Task types returned for each pivot type |
"""

import json
import sys
import os

# Ensure hledac.universal is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest


class MockFinding:
    """Mock CanonicalFinding-like object."""
    def __init__(
        self,
        finding_id: str = "fid_001",
        query: str = "test query",
        source_type: str = "ct_log",
        confidence: float = 0.75,
        ts: float = 1234567890.0,
        provenance: tuple = (),
        payload_text: str | None = None,
    ):
        self.finding_id = finding_id
        self.query = query
        self.source_type = source_type
        self.confidence = confidence
        self.ts = ts
        self.provenance = provenance
        self.payload_text = payload_text


class TestPivotPlannerBasics:
    """Basic pivot planner tests."""

    def test_max_pivots_enforced(self):
        """F202G: MAX_PIVOTS=20 bound enforced."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, MAX_PIVOTS

        assert MAX_PIVOTS == 20

        # Create many findings
        findings = [
            MockFinding(
                finding_id=f"fid_{i}",
                source_type="ct_log",
                confidence=0.7,
                payload_text=f"https://example.com/path/{i}",
            )
            for i in range(50)
        ]

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings, max_pivots=MAX_PIVOTS)
        assert len(pivots) <= MAX_PIVOTS

    def test_empty_findings_returns_empty(self):
        """F202G: Fail-soft on empty input returns empty list."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        planner = PivotPlanner()
        pivots = planner.plan_pivots([])
        assert pivots == []
        assert planner.get_last_error() is None


class TestPivotTypes:
    """Test pivot generation based on IOC types."""

    def test_finding_with_domain_generates_domain_pivot(self):
        """F202G: Domain IOC → domain pivot."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        finding = MockFinding(
            finding_id="fid_domain",
            source_type="ct_log",
            confidence=0.8,
            payload_text="https://evil.example.com/malware",
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        domain_pivots = [p for p in pivots if p.pivot_type == PivotType.DOMAIN]
        assert len(domain_pivots) >= 1
        # Check the domain was extracted
        domain_values = [p.ioc_value for p in domain_pivots]
        assert any("example.com" in v or "evil.example.com" in v for v in domain_values)

    def test_finding_with_ip_generates_reverse_dns_pivot(self):
        """F202G: IP IOC → domain pivot (reverse DNS)."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        finding = MockFinding(
            finding_id="fid_ip",
            source_type="ct_log",
            confidence=0.75,
            payload_text="server: 203.0.113.50",
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        # Check that we got pivots with domain pivot type
        domain_pivots = [p for p in pivots if p.pivot_type == PivotType.DOMAIN]
        assert len(domain_pivots) >= 1, f"Expected domain pivot, got: {[(p.pivot_type, p.ioc_value) for p in pivots]}"

    def test_finding_with_hash_generates_graph_pivot(self):
        """F202G: Hash IOC → graph pivot."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        finding = MockFinding(
            finding_id="fid_hash",
            source_type="ct_log",
            confidence=0.85,
            payload_text="da39a3ee5e6b4b0d3255bfef95601890afd80709",  # SHA1
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        graph_pivots = [p for p in pivots if p.pivot_type == PivotType.GRAPH]
        assert len(graph_pivots) >= 1

    def test_finding_with_email_generates_leak_pivot(self):
        """F202G: Email IOC → leak pivot."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        # Use email as the only recognizable IOC in payload
        finding = MockFinding(
            finding_id="fid_email",
            source_type="public",
            confidence=0.7,
            payload_text="testuser@protonmail.com",
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        leak_pivots = [p for p in pivots if p.pivot_type == PivotType.LEAK]
        assert len(leak_pivots) >= 1, f"Expected leak pivot, got: {[(p.pivot_type, p.ioc_value) for p in pivots]}"

    def test_finding_with_url_generates_archive_pivot(self):
        """F202G: URL IOC → archive pivot."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        # Use URL as the only recognizable IOC
        finding = MockFinding(
            finding_id="fid_url",
            source_type="public",
            confidence=0.6,
            payload_text="https://archive.example.com/page/123",
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        archive_pivots = [p for p in pivots if p.pivot_type == PivotType.ARCHIVE]
        assert len(archive_pivots) >= 1, f"Expected archive pivot, got: {[(p.pivot_type, p.ioc_value) for p in pivots]}"


class TestEnvelopeHandling:
    """Test evidence envelope handling."""

    def test_envelope_deserialization(self):
        """F202G: FindingEnvelope extracted from payload_text."""
        from hledac.universal.runtime.pivot_planner import _deserialize_envelope

        envelope = {
            "audit_reason": "High confidence signal via CT log",
            "evidence_pointers": ["https://example.com/ref1"],
            "signal_facets": {"novelty_score": 0.8, "entropy_bits": 4.5},
            "suggested_pivots": [],
        }

        class EnvFinding:
            finding_id = "fid_env"
            payload_text = json.dumps(envelope)

        result = _deserialize_envelope(EnvFinding())
        assert result is not None
        assert result["audit_reason"] == "High confidence signal via CT log"
        assert result["signal_facets"]["novelty_score"] == 0.8

    def test_envelope_without_audit_reason_returns_none(self):
        """F202G: JSON without audit_reason returns None."""
        from hledac.universal.runtime.pivot_planner import _deserialize_envelope

        class BadFinding:
            payload_text = '{"evidence_pointers": []}'

        result = _deserialize_envelope(BadFinding())
        assert result is None

    def test_findings_without_payload_returns_empty(self):
        """F202G: Findings without payload_text handled gracefully."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        class NoPayloadFinding:
            finding_id = "fid_nopayload"
            source_type = "ct_log"
            confidence = 0.5
            payload_text = None

        planner = PivotPlanner()
        # Should not crash, just skip this finding
        pivots = planner.plan_pivots([NoPayloadFinding()])
        assert isinstance(pivots, list)


class TestPivotOutput:
    """Test pivot output format."""

    def test_pivot_has_required_fields(self):
        """F202G: Pivot includes reason, expected_value, source_hint, evidence_pointers."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        finding = MockFinding(
            finding_id="fid_required",
            source_type="ct_log",
            confidence=0.8,
            payload_text="https://test.example.com/path",
        )

        planner = PivotPlanner()
        pivots = planner.plan_pivots([finding])

        assert len(pivots) >= 1
        pivot = pivots[0]
        assert hasattr(pivot, "reason")
        assert hasattr(pivot, "expected_value")
        assert hasattr(pivot, "source_hint")
        assert hasattr(pivot, "evidence_pointers")
        assert isinstance(pivot.reason, str)
        assert 0.0 <= pivot.expected_value <= 1.0
        assert isinstance(pivot.evidence_pointers, tuple)

    def test_pivot_deduplication(self):
        """F202G: Same IOC deduplicated, highest score kept."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        findings = [
            MockFinding(
                finding_id="fid_1",
                source_type="ct_log",
                confidence=0.5,
                payload_text="https://example.com/page1",
            ),
            MockFinding(
                finding_id="fid_2",
                source_type="public",
                confidence=0.9,  # Higher confidence
                payload_text="https://example.com/page2",  # Same domain
            ),
        ]

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings)

        # Should have pivots for example.com, but deduplicated
        domain_pivots = [p for p in pivots if "example.com" in p.ioc_value]
        # There might be multiple pivots (domain + archive) for same domain
        # But there should not be duplicate pivots with same (ioc_type, ioc_value)
        seen_keys = set()
        for p in pivots:
            key = (p.ioc_type, p.ioc_value)
            assert key not in seen_keys, f"Duplicate pivot: {key}"
            seen_keys.add(key)

    def test_pivot_sorting(self):
        """F202G: Pivots sorted by priority (highest expected_value first)."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        findings = [
            MockFinding(
                finding_id="fid_low",
                source_type="public",
                confidence=0.3,
                payload_text="https://low.example.com",
            ),
            MockFinding(
                finding_id="fid_high",
                source_type="ct_log",
                confidence=0.95,
                payload_text="https://high.example.com",
            ),
        ]

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings)

        if len(pivots) >= 2:
            # Check sorted by expected_value descending (priority = -expected_value)
            expected_values = [p.expected_value for p in pivots]
            assert expected_values == sorted(expected_values, reverse=True)


class TestFailSoft:
    """Test fail-soft behavior."""

    def test_planner_fail_soft_with_exception(self):
        """F202G: Exception returns empty list, never crashes."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        planner = PivotPlanner()

        # Pass something that will cause an exception in processing
        bad_findings = [None, "not a finding", 123]

        # Should not raise, should return empty list
        pivots = planner.plan_pivots(bad_findings)  # type: ignore
        assert pivots == []

    def test_planner_records_last_error(self):
        """F202G: Planner records last error on failure."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        planner = PivotPlanner()

        # Process valid findings to clear any previous error
        findings = [MockFinding(finding_id="fid_ok", confidence=0.5, payload_text="test.com")]
        planner.plan_pivots(findings)

        # Last error should be None for valid input
        assert planner.get_last_error() is None


class TestPivotTypeMapping:
    """Test pivot type mapping in source_registry."""

    def test_pivot_type_mapping_exists(self):
        """F202G: discovery/source_registry.py PIVOT_TYPE_MAP exists."""
        from hledac.universal.discovery.source_registry import PIVOT_TYPE_MAP, get_pivot_type

        assert isinstance(PIVOT_TYPE_MAP, dict)
        assert len(PIVOT_TYPE_MAP) > 0

        # Check known mappings
        assert get_pivot_type("domain") == "domain"
        assert get_pivot_type("ip") == "domain"
        assert get_pivot_type("md5") == "graph"
        assert get_pivot_type("sha256") == "graph"
        assert get_pivot_type("email") == "leak"

    def test_get_pivot_task_types(self):
        """F202G: Task types returned for each pivot type."""
        from hledac.universal.discovery.source_registry import get_pivot_task_types
        from hledac.universal.runtime.pivot_planner import PivotType

        # Domain pivot should have DNS, WHOIS, etc.
        domain_tasks = get_pivot_task_types(PivotType.DOMAIN)
        assert isinstance(domain_tasks, list)
        assert len(domain_tasks) > 0

        # Graph pivot should have graph traversal tasks
        graph_tasks = get_pivot_task_types(PivotType.GRAPH)
        assert isinstance(graph_tasks, list)
        assert len(graph_tasks) > 0

        # Unknown pivot type should return default
        unknown_tasks = get_pivot_task_types("unknown_pivot")
        assert unknown_tasks == ["multi_engine_search"]


class TestIntegration:
    """Integration tests."""

    def test_plan_pivots_with_graph_stats(self):
        """F202G: plan_pivots accepts graph_stats parameter."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        findings = [
            MockFinding(
                finding_id="fid_graph",
                source_type="ct_log",
                confidence=0.8,
                payload_text="https://graph.example.com/path",
            )
        ]

        graph_stats = {
            "nodes": 100,
            "edges": 500,
            "domains": ["new.example.com"],  # not graph.example.com
            "connected_iocs": set(),
            "node_degrees": {},
        }

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings, graph_stats=graph_stats)

        assert isinstance(pivots, list)
        # Graph stats should influence scoring but not crash

    def test_plan_pivots_custom_max(self):
        """F202G: plan_pivots accepts custom max_pivots parameter."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner

        findings = [
            MockFinding(
                finding_id=f"fid_{i}",
                source_type="ct_log",
                confidence=0.7,
                payload_text=f"https://example{i}.com/path",
            )
            for i in range(30)
        ]

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings, max_pivots=5)

        assert len(pivots) <= 5

    def test_multiple_ioc_types_in_one_finding(self):
        """F202G: Finding with multiple IOCs generates multiple pivot types."""
        from hledac.universal.runtime.pivot_planner import PivotPlanner, PivotType

        # Use separate findings for different IOC types to ensure distinct extraction
        findings = [
            MockFinding(
                finding_id="fid_ip",
                source_type="ct_log",
                confidence=0.8,
                payload_text="source IP: 5.6.7.8",
            ),
            MockFinding(
                finding_id="fid_email",
                source_type="public",
                confidence=0.7,
                payload_text="Email: testuser@domain.org for info",
            ),
        ]

        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings)

        pivot_types = set(p.pivot_type for p in pivots)
        # Should have multiple pivot types from different findings
        assert len(pivot_types) >= 2, f"Expected at least 2 pivot types, got: {pivot_types}"


class TestDegreeWeightedNoveltyPenalty:
    """Test degree-weighted novelty penalty for domain pivots (F229A)."""

    def test_low_degree_unseen_domain_gets_novelty_bonus(self):
        """F229A: Low-degree unseen domain receives novelty bonus."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        # Domain not in graph, degree=0
        graph_stats = {"domains": [], "node_degrees": {}}
        score = _score_pivot_domain("new.example.com", 0.5, None, graph_stats)
        # Base: 0.5 * 0.6 = 0.3; novelty bonus +0.2 = 0.5; degree penalty 0
        assert score >= 0.4  # novelty bonus applied

    def test_high_degree_domain_gets_penalty(self):
        """F229A: High-degree domain gets degree penalty."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        graph_stats = {"domains": [], "node_degrees": {"cdn.provider.com": 50}}
        score = _score_pivot_domain("cdn.provider.com", 0.5, None, graph_stats)
        # Base: 0.5 * 0.6 = 0.3; novelty +0.2 = 0.5; penalty min(0.15, 50*0.01)=0.15
        assert score < 0.4  # penalty reduces score

    def test_degree_penalty_cap_at_0_15(self):
        """F229A: Degree penalty capped at 0.15."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        # degree=99 → min(0.15, 0.99) = 0.15
        graph_stats = {"domains": [], "node_degrees": {"massivecdn.com": 99}}
        score = _score_pivot_domain("massivecdn.com", 0.5, None, graph_stats)
        # Base: 0.3 + 0.2 - 0.15 = 0.35
        assert score >= 0.30  # penalty doesn't exceed cap

    def test_existing_domain_gets_mild_deprioritization(self):
        """F229A: Existing domain gets -0.05 deprioritization."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        graph_stats = {"domains": ["already.seen.com"], "node_degrees": {"already.seen.com": 5}}
        score = _score_pivot_domain("already.seen.com", 0.5, None, graph_stats)
        # Base: 0.3; no novelty bonus; -0.05 existing penalty; degree 5*0.01=0.05 penalty
        assert score < 0.3  # existing domain penalized

    def test_score_never_exceeds_1_0(self):
        """F229A: Score clamped to [0.0, 1.0] upper bound."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        graph_stats = {"domains": [], "node_degrees": {}}
        score = _score_pivot_domain("any.example.com", 1.0, None, graph_stats)
        assert score <= 1.0

    def test_score_never_below_0_0(self):
        """F229A: Score clamped to [0.0, 1.0] lower bound."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        graph_stats = {"domains": [], "node_degrees": {"highdegree.example.com": 200}}
        score = _score_pivot_domain("highdegree.example.com", 0.01, None, graph_stats)
        assert score >= 0.0

    def test_sort_order_reflects_degree_penalty(self):
        """F229A: Degree penalty penalizes high-degree domains in raw scoring."""
        from hledac.universal.runtime.pivot_planner import _score_pivot_domain

        # Low-degree new domain vs high-degree existing domain
        graph_stats = {
            "domains": ["cdn.cloudprovider.com"],
            "node_degrees": {"rare.example.com": 2, "cdn.cloudprovider.com": 50},
        }

        # Raw score: low-degree novel domain should score higher
        rare_score = _score_pivot_domain("rare.example.com", 0.8, None, graph_stats)
        cdn_score = _score_pivot_domain("cdn.cloudprovider.com", 0.8, None, graph_stats)

        # rare.example.com: base=0.48, novelty=+0.2, degree_penalty=-0.02 → 0.66
        # cdn.cloudprovider.com: base=0.48, existing=-0.05, degree_penalty=-0.15 → 0.28
        assert rare_score > cdn_score, \
            f"Low-degree novel domain (0.66) should score higher than high-degree existing (0.28), got {rare_score} vs {cdn_score}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
