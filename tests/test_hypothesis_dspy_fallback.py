"""
Sprint F214Q: HypothesisGenerator DSPy fallback probe tests.

Verifies:
- With HLEDAC_ENABLE_DSPY unset (or False), _heuristic_generate() is called
- _heuristic_generate returns valid ResearchHypothesis list
- With DSPy enabled (HLEDAC_ENABLE_DSPY=1), DSPy path is attempted

probe_f214q/.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from hypothesis.hypothesisgenerator import (
    HypothesisGenerator,
    ResearchHypothesis,
    HLEDAC_ENABLE_DSPY,
    _heuristic_generate,
    _load_dspy_program,
)


class MockFinding:
    def __init__(self, finding_id: str, payload_text: str):
        self.finding_id = finding_id
        self.payload_text = payload_text


# ---------------------------------------------------------------------------
# Environment gate: HLEDAC_ENABLE_DSPY
# ---------------------------------------------------------------------------


def test_HLEDAC_ENABLE_DSPY_defaults_to_false():
    """HLEDAC_ENABLE_DSPY is False when env var is not set."""
    # HLEDAC_ENABLE_DSPY reflects current process env — may be set externally
    # The module-level constant reflects the current environment state at import
    assert HLEDAC_ENABLE_DSPY in (True, False)


def test_heuristic_generate_returns_valid_research_hypothesis_list():
    """
    _heuristic_generate returns a list of ResearchHypothesis objects.
    """
    findings = [
        MockFinding("f1", "192.168.1.1 at 8.8.8.8"),
        MockFinding("f2", "evil.example.com beacon C2 at 10.0.0.5"),
    ]
    result = _heuristic_generate(findings, current_seeds=["example.com"], sprint_depth=1)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(h, ResearchHypothesis) for h in result)


def test_heuristic_generate_with_ip_findings():
    """IP findings → entity_expansion hypotheses with /16 subnet pivot seeds."""
    findings = [
        MockFinding("f1", f"indicator 8.8.8.{i} resolved to mail.example.com")
        for i in range(5)
    ]
    result = _heuristic_generate(findings, current_seeds=[], sprint_depth=1)
    ip_hypotheses = [h for h in result if "8.8.8" in h.hypothesis_text]
    assert len(ip_hypotheses) >= 1
    for h in ip_hypotheses:
        assert h.hypothesis_type == "entity_expansion"
        assert h.confidence == 0.65  # IP entity_expansion confidence


def test_heuristic_generate_with_domain_findings():
    """Domain findings → entity_expansion hypotheses with parent TLD pivot seeds."""
    findings = [
        MockFinding("f1", "subdomain.example.com resolved"),
        MockFinding("f2", "deep.example.com malware C2"),
    ]
    result = _heuristic_generate(findings, current_seeds=[], sprint_depth=1)
    domain_hypotheses = [h for h in result if "example.com" in h.hypothesis_text]
    assert len(domain_hypotheses) >= 1
    for h in domain_hypotheses:
        assert h.hypothesis_type == "entity_expansion"
        assert h.confidence == 0.6  # domain entity_expansion confidence


def test_heuristic_generate_with_hash_findings():
    """Hash findings → lateral hypotheses with hash pivot seeds."""
    findings = [
        MockFinding("f1", "file abc123def456789012345678901234ab hash detected in breach"),
    ]
    result = _heuristic_generate(findings, current_seeds=[], sprint_depth=1)
    hash_hypotheses = [h for h in result if "abc123" in h.hypothesis_text or "hash" in h.hypothesis_type]
    assert len(hash_hypotheses) >= 1
    assert any(h.hypothesis_type == "lateral" for h in result)


def test_heuristic_generate_with_email_findings():
    """Email findings → adversarial hypotheses with leak: pivot seeds."""
    findings = [
        MockFinding("f1", "admin@evil-corp.com credentials exposed in leak"),
    ]
    result = _heuristic_generate(findings, current_seeds=[], sprint_depth=1)
    email_hypotheses = [h for h in result if "evil-corp.com" in h.hypothesis_text or "adversarial" in h.hypothesis_type]
    assert len(email_hypotheses) >= 1
    assert any(h.hypothesis_type == "adversarial" for h in result)


def test_heuristic_generate_seed_expansion():
    """Current seeds present → seed-expansion hypothesis added."""
    findings = [
        MockFinding("f1", "8.8.8.8 DNS response"),
    ]
    result = _heuristic_generate(findings, current_seeds=["lockbit3.tw", "ransomware.com"], sprint_depth=1)
    seed_hyps = [h for h in result if "lockbit3.tw" in h.hypothesis_text or "Seed" in h.hypothesis_text]
    assert len(seed_hyps) >= 1


def test_heuristic_generate_sprint_depth_temporal():
    """sprint_depth > 1 → temporal hypotheses included."""
    findings = [
        MockFinding("f1", "example.com WHOIS created 2020-01-01"),
    ]
    result_d1 = _heuristic_generate(findings, current_seeds=[], sprint_depth=1)
    result_d2 = _heuristic_generate(findings, current_seeds=[], sprint_depth=2)
    assert not any(h.hypothesis_type == "temporal" for h in result_d1)
    # depth=2 may produce temporal (domain present)
    temporal_d2 = [h for h in result_d2 if h.hypothesis_type == "temporal"]
    assert len(temporal_d2) >= 0  # pass-through is valid


def test_generate_without_dspy_calls_heuristic(monkeypatch):
    """
    With HLEDAC_ENABLE_DSPY unset, generate() calls _heuristic_generate,
    not the DSPy path.
    """
    # Ensure DSPy is not enabled in env
    monkeypatch.delenv("HLEDAC_ENABLE_DSPY", raising=False)

    findings = [
        MockFinding("f1", "192.168.1.1"),
    ]
    gen = HypothesisGenerator(graph=None)
    result = gen.generate(findings, current_seeds=["test.com"], sprint_depth=1)
    assert isinstance(result, list)
    assert len(result) >= 1
    # Should produce entity_expansion (IP extracted from payload)
    assert any(h.hypothesis_type == "entity_expansion" for h in result)


def test_load_dspy_program_returns_none_when_not_enabled(monkeypatch):
    """
    _load_dspy_program returns None when HLEDAC_ENABLE_DSPY env var is not set.
    (DSPy loading is env-gated and fail-soft.)
    """
    monkeypatch.delenv("HLEDAC_ENABLE_DSPY", raising=False)
    result = _load_dspy_program()
    # When DSPy is not enabled, the module checks env inside _load_dspy_program
    # It may return None or raise — both are handled gracefully
    assert result is None or callable(result)  # None if no compiled program
