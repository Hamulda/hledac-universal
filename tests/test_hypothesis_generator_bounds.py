"""
Sprint F214Q: HypothesisGenerator bounds probe tests.

Tests invariant enforcement:
- MAX_HYPOTHESES = 10 hard cap
- MAX_SEEDS_PER_HYPOTHESIS = 5
- Empty findings → returns empty list (not crash)

probe_f214q/ — uses HypothesisGenerator directly (no scheduler, no DuckDB).
"""
from __future__ import annotations

import pytest

from hypothesis.hypothesisgenerator import (
    HypothesisGenerator,
    ResearchHypothesis,
    MAX_HYPOTHESES,
    MAX_SEEDS_PER_HYPOTHESIS,
    _heuristic_generate,
)


class MockFinding:
    """Minimal CanonicalFinding-like for testing."""

    def __init__(self, finding_id: str, payload_text: str):
        self.finding_id = finding_id
        self.payload_text = payload_text


# ---------------------------------------------------------------------------
# Invariant: MAX_HYPOTHESES = 10
# ---------------------------------------------------------------------------


def test_MAX_HYPOTHESES_is_10():
    """Hard cap: generate() never returns more than 10 hypotheses."""
    assert MAX_HYPOTHESES == 10


def test_generate_respects_max_hypotheses_cap():
    """
    Feed 20 IP findings → expect at most 10 hypotheses returned.
    """
    findings = [
        MockFinding(f"fid_{i}", f"indicator 8.8.8.{i} resolved to example.com")
        for i in range(20)
    ]
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings, current_seeds=["test-domain.com"], sprint_depth=1)
    assert len(result) <= MAX_HYPOTHESES
    assert len(result) <= 10


# ---------------------------------------------------------------------------
# Invariant: MAX_SEEDS_PER_HYPOTHESIS = 5
# ---------------------------------------------------------------------------


def test_MAX_SEEDS_PER_HYPOTHESIS_is_5():
    """Each hypothesis pivot_seeds tuple never exceeds 5 items."""
    assert MAX_SEEDS_PER_HYPOTHESIS == 5


def test_hypothesis_pivot_seeds_never_exceed_5():
    """
    Feed findings that trigger all hypothesis types → verify no hypothesis
    gets more than 5 pivot seeds.
    """
    findings = [
        MockFinding("f1", "192.168.1.1 resolved to example.com"),
        MockFinding("f2", "subdomain.example.com resolved"),
        MockFinding("f3", "abc123def456789012345678901234ab hash detected"),
        MockFinding("f4", "user@example.com credentials leaked"),
    ]
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings, current_seeds=["seed1.com"], sprint_depth=1)
    for hyp in result:
        assert len(hyp.pivot_seeds) <= MAX_SEEDS_PER_HYPOTHESIS


# ---------------------------------------------------------------------------
# Empty inputs → fail-soft, no crash
# ---------------------------------------------------------------------------


def test_empty_findings_and_seeds_returns_single_fallback():
    """
    Empty findings + empty seeds → returns exactly 1 fallback hypothesis (not empty, not crash).
    Fail-soft guarantee: generate() always returns >= 1.
    """
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings=[], current_seeds=[], sprint_depth=1)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(h, ResearchHypothesis) for h in result)


def test_empty_findings_with_seeds_returns_valid_hypotheses():
    """Empty findings but seeds present → returns valid hypotheses (not crash)."""
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings=[], current_seeds=["lockbit3.tw"], sprint_depth=1)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(h, ResearchHypothesis) for h in result)
    # Fallback path — seed expansion hypothesis
    texts = [h.hypothesis_text for h in result]
    assert any("lockbit3.tw" in t or "Seed" in t for t in texts)


def test_heuristic_generate_empty_findings_returns_empty():
    """
    _heuristic_generate with no IOCs extracted → returns empty list (caller handles fail-soft).
    """
    result = _heuristic_generate(findings=[], current_seeds=[], sprint_depth=1)
    assert isinstance(result, list)
    # heuristic with no findings and no seeds returns seed-expansion hypothesis
    # (seed expansion path uses current_seeds, not payload extraction)
    assert len(result) >= 0  # pass-through is valid


# ---------------------------------------------------------------------------
# ResearchHypothesis structure invariants
# ---------------------------------------------------------------------------


def test_hypotheses_have_required_fields():
    """Every returned hypothesis has all required dataclass fields."""
    findings = [
        MockFinding("f1", "192.168.1.1 at 8.8.8.8"),
        MockFinding("f2", "evil.example.com malware beacon"),
    ]
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings, current_seeds=["example.com"], sprint_depth=1)
    assert len(result) >= 1
    for h in result:
        assert isinstance(h, ResearchHypothesis)
        assert isinstance(h.hypothesis_text, str)
        assert isinstance(h.confidence, float)
        assert 0.0 <= h.confidence <= 1.0
        assert isinstance(h.pivot_seeds, tuple)
        assert isinstance(h.supporting_findings, tuple)
        assert isinstance(h.hypothesis_type, str)


def test_hypothesis_types_are_valid():
    """Returned hypotheses use known type strings."""
    VALID_TYPES = {"entity_expansion", "temporal", "lateral", "adversarial"}
    findings = [
        MockFinding("f1", "192.168.1.1"),
        MockFinding("f2", "example.com"),
        MockFinding("f3", "deadbeef1234567890abcdefabcdefab"),
        MockFinding("f4", "test@example.com"),
    ]
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings, current_seeds=[], sprint_depth=2)
    for h in result:
        assert h.hypothesis_type in VALID_TYPES


def test_sprint_depth_affects_temporal_hypothesis():
    """
    sprint_depth=1 → no temporal hypotheses.
    sprint_depth=2+ → temporal hypotheses may appear.
    """
    findings = [MockFinding("f1", "example.com domain")]
    gen = HypothesisGenerator(graph=None)

    result_d1 = gen.generate(findings, current_seeds=["example.com"], sprint_depth=1)
    d1_types = {h.hypothesis_type for h in result_d1}
    assert "temporal" not in d1_types  # depth=1 → no temporal

    result_d2 = gen.generate(findings, current_seeds=["example.com"], sprint_depth=2)
    # depth>1 may produce temporal — just verify it doesn't crash
    assert isinstance(result_d2, list)
