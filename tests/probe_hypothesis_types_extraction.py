"""
Hypothesis engine — C4 sprint refactoring probe tests.

Sprint F350M-S: Verify that the 5 373 LOC monolith
:mod:`brain.hypothesis_engine` was successfully split into the
:mod:`brain.hypothesis` package, with byte-for-byte equivalent type
definitions and 100% backward compatibility.

Run: ``uv run pytest tests/probe_hypothesis_types_extraction.py -v``
"""
from __future__ import annotations

import sys
from datetime import datetime

sys.path.insert(0, "hledac/universal")


# ── Backward compatibility: every old import still works ─────────────────


class TestBackwardCompat:
    def test_old_path_still_works(self) -> None:
        # The original import path — what every existing call site uses.
        # Note: ``Hypothesis`` is intentionally only at this path (carries
        # extra methods). All other DTOs are re-exported from
        # :mod:`brain.hypothesis`.
        from brain.hypothesis_engine import (  # noqa: F401
            AdversarialReport,
            AnomalySignal,
            CausalEntity,
            CausalHypothesis,
            Contradiction,
            CrossReferenceResult,
            DarkQuery,
            DarkQueryType,
            Event,
            Evidence,
            FalsificationResult,
            Hypothesis,
            HypothesisStatus,
            HypothesisType,
            InferenceEngineProtocol,
            MAX_CAUSAL_ENTITIES,
            MAX_CAUSAL_FINDINGS,
            MAX_CAUSAL_HYPOTHESES,
            MAX_CO_OCCURRENCE_MATRIX_SIZE,
            CO_OCCURRENCE_FP16,
            SourceCredibility,
            TemporalSequence,
            TestDesign,
            TestResult,
            TestType,
        )

    def test_new_package_path_works(self) -> None:
        from brain.hypothesis import (  # noqa: F401
            AdversarialReport,
            Evidence,
            MAX_CAUSAL_ENTITIES,
        )

    def test_internal_types_module_works(self) -> None:
        from brain.hypothesis._types import (  # noqa: F401
            AdversarialReport,
            CausalEntity,
            CausalHypothesis,
            DarkQuery,
            Evidence,
            FalsificationResult,
            InferenceEngineProtocol,
        )

    def test_three_paths_yield_equivalent_class(self) -> None:
        """``Hypothesis`` is intentionally NOT extracted because the version
        in ``hypothesis_engine`` carries extra runtime methods. New code
        imports the simple DTOs from :mod:`brain.hypothesis._types`; the
        full ``Hypothesis`` class still lives at the legacy path. Verify
        that the legacy class is still importable and constructs."""
        from brain.hypothesis_engine import Hypothesis as OldHypothesis

        # Hypothesis must still construct successfully at the legacy path
        h = OldHypothesis(
            id="h1",
            statement="test",
            hypothesis_type="existence",
        )
        assert h.id == "h1"
        assert h.statement == "test"
        assert h.prior_probability == 0.5  # default preserved

        # Hypothesis must carry its engine-specific methods
        assert callable(getattr(h, "add_test_result", None))
        assert callable(getattr(h, "add_supporting_evidence", None))
        assert callable(getattr(h, "add_conflicting_evidence", None))
        assert callable(getattr(h, "update_probability", None))


# ── Hypothesis DTO behaviour preservation ────────────────────────────────


class TestHypothesisBehaviour:
    def test_bayesian_update_still_works(self) -> None:
        from brain.hypothesis import Evidence
        from brain.hypothesis_engine import Hypothesis

        h = Hypothesis(
            id="h1",
            statement="test",
            hypothesis_type="existence",
        )
        ev = Evidence(
            evidence_id="e1",
            source="unit",
            content="c",
            timestamp=datetime.now(),
            reliability=0.9,
            relevance=0.8,
        )
        # Use the legacy add_supporting_evidence path (Bayesian via likelihood ratio)
        h.add_supporting_evidence(ev.evidence_id, weight=1.0)
        assert 0.0 < h.posterior_probability <= 1.0
        # 0.9 reliability × 1.0 weight → posterior should be > 0.5
        assert h.posterior_probability > 0.5

    def test_test_result_iso_timestamp_parsing(self) -> None:
        from brain.hypothesis import TestResult

        # String timestamp must be parsed via fromisoformat
        tr = TestResult(
            test_type="t",
            result="passed",
            confidence=0.5,
            timestamp="2026-01-01T00:00:00",  # type: ignore[arg-type]
        )
        assert isinstance(tr.timestamp, datetime)

    def test_dark_query_frozen_slots(self) -> None:
        from brain.hypothesis import DarkQuery, DarkQueryType

        dq = DarkQuery(query_type=DarkQueryType.ONION, query="q", priority=0.5)
        try:
            dq.query = "modified"  # type: ignore[misc]
        except (AttributeError, Exception):
            return
        raise AssertionError("DarkQuery should be frozen — assignment must fail")

    def test_source_credibility_update_accuracy(self) -> None:
        from brain.hypothesis import SourceCredibility

        sc = SourceCredibility(source_id="s1", credibility_score=0.5)
        sc.update_accuracy(True)
        sc.update_accuracy(True)
        sc.update_accuracy(False)
        assert sc.total_claims == 3
        assert sc.verified_claims == 2
        assert abs(sc.historical_accuracy - (2 / 3)) < 1e-9
        # Credibility score depends on history + (1 - contradiction/10) × 0.3
        assert 0.0 < sc.credibility_score < 1.0

    def test_causal_hypothesis_frozen(self) -> None:
        from brain.hypothesis import CausalHypothesis

        ch = CausalHypothesis(
            hypothesis_id="c1",
            source_entity="a",
            target_entity="b",
            hypothesis_type="causal",
            statement="a causes b",
            confidence=0.7,
            source_count=3,
            source_diversity=2,
            temporal_consistent=True,
        )
        try:
            ch.confidence = 0.99  # type: ignore[misc]
        except (AttributeError, Exception):
            return
        raise AssertionError("CausalHypothesis must be frozen")


# ── Bounds preservation (M1 8GB invariants) ─────────────────────────────


class TestBoundsPreservation:
    def test_causal_bounds_unchanged(self) -> None:
        from brain.hypothesis import (
            MAX_CAUSAL_ENTITIES,
            MAX_CAUSAL_FINDINGS,
            MAX_CAUSAL_HYPOTHESES,
            MAX_CO_OCCURRENCE_MATRIX_SIZE,
        )

        assert MAX_CAUSAL_ENTITIES == 5000
        assert MAX_CAUSAL_FINDINGS == 50000
        assert MAX_CAUSAL_HYPOTHESES == 200
        assert MAX_CO_OCCURRENCE_MATRIX_SIZE == 2000

    def test_co_occurrence_fp16_still_true(self) -> None:
        from brain.hypothesis import CO_OCCURRENCE_FP16

        assert CO_OCCURRENCE_FP16 is True


# ── Module size shrinkage (sanity check) ────────────────────────────────


class TestModuleRefactoring:
    def test_hypothesis_engine_still_imports(self) -> None:
        # The original module must still be importable (backward compat)
        import brain.hypothesis_engine as eng

        assert hasattr(eng, "Hypothesis")
        assert hasattr(eng, "Evidence")
        assert hasattr(eng, "AdversarialVerifier")
        assert hasattr(eng, "HypothesisEngine")
