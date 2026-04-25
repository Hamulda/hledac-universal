"""
Sprint F203D: Evidence Chain Tracker — Probe Tests
=================================================

Invariant mapping:
  F203D-1  | ChainStep is frozen dataclass with correct fields
  F203D-2  | EvidenceChain is a dataclass with root_finding_id, steps, conclusion
  F203D-3  | EvidenceChain.add_step enforces MAX_CHAIN_DEPTH=10
  F203D-4  | EvidenceChainBuilder records steps and builds chains
  F203D-5  | EvidenceChainBuilder enforces MAX_CHAINS_PER_SPRINT=100
  F203D-6  | EvidenceChainBuilder enforces MAX_CHAIN_DEPTH per chain
  F203D-7  | serialize_chain / deserialize_chain roundtrip
  F203D-8  | serialize_chain returns None for empty chain
  F203D-9  | MAX_CHAIN_JSON_BYTES=4098 bound enforced
  F203D-10 | FindingEnvelope has chain_refs field
  F203D-11 | FindingEnvelope.is_populated considers chain_refs
  F203D-12 | FindingEnvelope serialize/deserialize includes chain_refs
  F203D-13 | SprintSchedulerResult has chain_steps_recorded field
  F203D-14 | reset_global_builder / set_global_builder / get_global_builder work
  F203D-15 | _get_chain_for_finding searches by root and by step output_id
  F203D-16 | get_evidence_chain from analyst_workbench via registry
  F203D-17 | _render_evidence_chains_section produces markdown with chains
"""

import pytest

from hledac.universal.knowledge.evidence_chain import (
    MAX_CHAIN_DEPTH,
    MAX_CHAINS_PER_SPRINT,
    MAX_CHAIN_JSON_BYTES,
    ChainStep,
    EvidenceChain,
    EvidenceChainBuilder,
    deserialize_chain,
    get_all_chains,
    get_global_builder,
    reset_global_builder,
    serialize_chain,
    set_global_builder,
    _get_chain_for_finding,
)
from hledac.universal.knowledge.finding_envelope import (
    FindingEnvelope,
    serialize_envelope,
    deserialize_envelope,
)
from hledac.universal.export.sprint_markdown_reporter import (
    _render_evidence_chains_section,
)


class TestChainStep:
    """F203D-1: ChainStep is a frozen dataclass with correct fields."""

    def test_chain_step_frozen(self):
        """ChainStep instances are frozen (immutable)."""
        step = ChainStep(
            step_type="identity_stitching",
            input_ids=["f1", "f2"],
            output_id="f3",
            confidence=0.85,
            reason="linked via email",
        )
        with pytest.raises(Exception):
            step.confidence = 0.9

    def test_chain_step_fields(self):
        """ChainStep has correct fields."""
        step = ChainStep(
            step_type="exposure_correlation",
            input_ids=["a", "b"],
            output_id="c",
            confidence=0.75,
            reason="correlated assets",
        )
        assert step.step_type == "exposure_correlation"
        assert step.input_ids == ["a", "b"]
        assert step.output_id == "c"
        assert step.confidence == 0.75
        assert step.reason == "correlated assets"


class TestEvidenceChain:
    """F203D-2/3: EvidenceChain structure and depth enforcement."""

    def test_evidence_chain_empty(self):
        """EvidenceChain with no steps is empty."""
        chain = EvidenceChain(root_finding_id="root1")
        assert chain.is_empty
        assert chain.depth == 0
        assert chain.root_finding_id == "root1"
        assert chain.conclusion is None

    def test_evidence_chain_add_step(self):
        """add_step appends a step to the chain."""
        chain = EvidenceChain(root_finding_id="root1")
        step = ChainStep("finding_ingest", [], "root1", 0.9, "initial")
        chain.add_step(step)
        assert chain.depth == 1
        assert not chain.is_empty

    def test_max_chain_depth_enforced(self):
        """Chain depth is capped at MAX_CHAIN_DEPTH=10."""
        chain = EvidenceChain(root_finding_id="root1")
        for i in range(20):
            chain.add_step(
                ChainStep("test", [f"in-{i}"], f"out-{i}", 0.5, f"step {i}")
            )
        assert chain.depth == MAX_CHAIN_DEPTH

    def test_evidence_chain_conclusion(self):
        """EvidenceChain can store a conclusion."""
        chain = EvidenceChain(root_finding_id="root1", conclusion="attributed to actor X")
        assert chain.conclusion == "attributed to actor X"


class TestEvidenceChainBuilder:
    """F203D-4/5/6: Builder records steps and enforces bounds."""

    def test_record_ingest(self):
        """record_ingest creates the ingest step for a root finding."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("finding-1", 0.9, "CT log observed")
        chain = builder.build("finding-1")
        assert chain is not None
        assert chain.root_finding_id == "finding-1"
        assert chain.depth == 1
        assert chain.steps[0].step_type == "finding_ingest"
        assert chain.steps[0].output_id == "finding-1"

    def test_record_identity(self):
        """record_identity adds an identity stitching step."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "initial")
        builder.record_identity("f1", ["f1"], "identity-1", 0.85, "linked via email")
        chain = builder.build("f1")
        assert chain is not None
        assert chain.depth == 2
        assert chain.steps[1].step_type == "identity_stitching"

    def test_max_chains_per_sprint_enforced(self):
        """Builder caps chains at MAX_CHAINS_PER_SPRINT."""
        builder = EvidenceChainBuilder()
        for i in range(MAX_CHAINS_PER_SPRINT + 10):
            builder.record_ingest(f"finding-{i}", 0.5, f"step {i}")
        assert builder.get_chain_count() == MAX_CHAINS_PER_SPRINT

    def test_build_all(self):
        """build_all returns all chains."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "first")
        builder.record_ingest("f2", 0.8, "second")
        chains = builder.build_all()
        assert len(chains) == 2

    def test_get_chain_count_and_total_steps(self):
        """get_chain_count and get_total_steps return correct values."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "first")
        builder.record_identity("f1", ["f1"], "i1", 0.85, "linked")
        assert builder.get_chain_count() == 1
        assert builder.get_total_steps() == 2


class TestSerialization:
    """F203D-7/8/9: Chain serialization roundtrip and bounds."""

    def test_roundtrip(self):
        """serialize then deserialize produces equivalent chain."""
        chain = EvidenceChain(root_finding_id="root-1")
        chain.add_step(ChainStep("finding_ingest", [], "root-1", 0.9, "ingested"))
        chain.add_step(ChainStep("identity_stitching", ["root-1"], "id-1", 0.85, "linked"))

        serialized = serialize_chain(chain)
        assert serialized is not None

        restored = deserialize_chain(serialized)
        assert restored is not None
        assert restored.root_finding_id == "root-1"
        assert restored.depth == 2
        assert restored.steps[0].step_type == "finding_ingest"
        assert restored.steps[1].confidence == 0.85

    def test_empty_chain_returns_none(self):
        """serialize_chain returns None for empty chain."""
        chain = EvidenceChain(root_finding_id="empty")
        assert serialize_chain(chain) is None

    def test_max_chain_json_bytes_enforced(self):
        """Chains exceeding MAX_CHAIN_JSON_BYTES are rejected."""
        chain = EvidenceChain(root_finding_id="root")
        # Add steps with long reasons to exceed limit
        long_reason = "x" * 10000
        for i in range(50):
            chain.add_step(ChainStep("test", [f"in-{i}"], f"out-{i}", 0.5, long_reason))

        # Very large chain should be rejected or not serialized
        result = serialize_chain(chain)
        # Either None (size exceeded) or it should serialize if under limit
        if result is not None:
            assert len(result.encode()) <= MAX_CHAIN_JSON_BYTES


class TestGlobalBuilder:
    """F203D-14: Global builder singleton management."""

    def setup_method(self):
        """Reset global builder before each test."""
        reset_global_builder()

    def teardown_method(self):
        """Reset after each test."""
        reset_global_builder()

    def test_set_and_get_global_builder(self):
        """set_global_builder and get_global_builder work."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "test")
        set_global_builder(builder)
        retrieved = get_global_builder()
        assert retrieved is builder
        assert retrieved.get_chain_count() == 1

    def test_reset_global_builder(self):
        """reset_global_builder clears the registry."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "test")
        set_global_builder(builder)
        reset_global_builder()
        assert get_global_builder().get_chain_count() == 0

    def test_get_all_chains(self):
        """get_all_chains returns chains from global builder."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("f1", 0.9, "first")
        set_global_builder(builder)
        chains = get_all_chains()
        assert len(chains) == 1


class TestChainLookup:
    """F203D-15: _get_chain_for_finding searches by root and step output."""

    def setup_method(self):
        reset_global_builder()

    def teardown_method(self):
        reset_global_builder()

    def test_lookup_by_root(self):
        """_get_chain_for_finding finds chain by root_finding_id."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("root-f1", 0.9, "ingested")
        set_global_builder(builder)

        chain = _get_chain_for_finding("root-f1")
        assert chain is not None
        assert chain.root_finding_id == "root-f1"

    def test_lookup_by_step_output_id(self):
        """_get_chain_for_finding finds chain by a step's output_id."""
        builder = EvidenceChainBuilder()
        builder.record_ingest("root-f1", 0.9, "ingested")
        builder.record_identity("root-f1", ["root-f1"], "identity-output", 0.85, "linked")
        set_global_builder(builder)

        chain = _get_chain_for_finding("identity-output")
        assert chain is not None
        assert chain.root_finding_id == "root-f1"

    def test_lookup_missing_returns_none(self):
        """_get_chain_for_finding returns None for unknown finding_id."""
        reset_global_builder()
        assert _get_chain_for_finding("nonexistent") is None


class TestFindingEnvelopeChainRefs:
    """F203D-10/11/12: FindingEnvelope chain_refs field."""

    def test_chain_refs_field(self):
        """FindingEnvelope accepts chain_refs in __init__."""
        env = FindingEnvelope(
            audit_reason="test reason",
            chain_refs=["chain-1", "chain-2"],
        )
        assert env.chain_refs == ["chain-1", "chain-2"]

    def test_chain_refs_default_empty_list(self):
        """chain_refs defaults to empty list."""
        env = FindingEnvelope(audit_reason="test")
        assert env.chain_refs == []

    def test_is_populated_with_chain_refs(self):
        """is_populated returns True when chain_refs is the only non-empty field."""
        env = FindingEnvelope(chain_refs=["chain-1"])
        assert env.is_populated()

    def test_serialize_deserialize_with_chain_refs(self):
        """serialize/deserialize roundtrip includes chain_refs."""
        env = FindingEnvelope(
            audit_reason="test reason",
            chain_refs=["chain-a", "chain-b"],
        )
        serialized = serialize_envelope(env)
        assert serialized is not None
        restored = deserialize_envelope(serialized)
        assert restored is not None
        assert restored.chain_refs == ["chain-a", "chain-b"]


class TestSprintSchedulerResult:
    """F203D-13: SprintSchedulerResult has chain_steps_recorded field."""

    def test_chain_steps_recorded_field_exists(self):
        """SprintSchedulerResult.chain_steps_recorded exists and defaults to 0."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "chain_steps_recorded")
        assert result.chain_steps_recorded == 0

    def test_chain_steps_recorded_mutable(self):
        """chain_steps_recorded can be incremented."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        result.chain_steps_recorded = 42
        assert result.chain_steps_recorded == 42


class TestMarkdownRendering:
    """F203D-17: Evidence chain markdown rendering."""

    def test_render_empty(self):
        """Empty chain list produces empty string."""
        result = _render_evidence_chains_section([])
        assert result == ""

    def test_render_single_chain(self):
        """Single chain renders as markdown."""
        chains = [
            {
                "root_finding_id": "root-finding-1",
                "steps": [
                    {
                        "step_type": "finding_ingest",
                        "input_ids": [],
                        "output_id": "root-finding-1",
                        "confidence": 0.9,
                        "reason": "CT log observed",
                    },
                    {
                        "step_type": "identity_stitching",
                        "input_ids": ["root-finding-1"],
                        "output_id": "identity-candidate-1",
                        "confidence": 0.85,
                        "reason": "linked via email+username",
                    },
                ],
                "conclusion": None,
            }
        ]
        result = _render_evidence_chains_section(chains)
        assert "Evidence Chains" in result
        assert "Chain 1:" in result
        assert "root-finding-1" in result
        assert "Finding Ingest" in result
        assert "Identity Stitching" in result
        assert "linked via email+username" in result

    def test_render_top5_by_depth(self):
        """Top-5 chains by depth are rendered."""
        chains = []
        for i in range(7):
            chain = {
                "root_finding_id": f"root-{i}",
                "steps": [
                    {
                        "step_type": "finding_ingest",
                        "input_ids": [],
                        "output_id": f"root-{i}",
                        "confidence": 0.9,
                        "reason": "step",
                    }
                    for _ in range(i + 1)
                ],
                "conclusion": None,
            }
            chains.append(chain)

        result = _render_evidence_chains_section(chains)
        # Should show chain 7, 6, 5, 4, 3 (top 5 by depth) — not all 7
        assert "Evidence Chains" in result
        # Count how many "Chain N:" appear
        chain_count = result.count("Chain ")
        assert chain_count == 5  # Top 5 only

    def test_render_with_conclusion(self):
        """Chain with conclusion renders the conclusion."""
        chains = [
            {
                "root_finding_id": "root-1",
                "steps": [
                    {
                        "step_type": "finding_ingest",
                        "input_ids": [],
                        "output_id": "root-1",
                        "confidence": 0.9,
                        "reason": "ingested",
                    }
                ],
                "conclusion": "attributed to APT-X via attribution scoring",
            }
        ]
        result = _render_evidence_chains_section(chains)
        assert "attributed to APT-X via attribution scoring" in result
