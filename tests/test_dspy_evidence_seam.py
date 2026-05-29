"""Tests for DSPy optimizer evidence seam fix (F234).

KROK 3: Minimal test verifying _load_training_examples reads from evidence JSONL.

GHOST_INVARIANTS tested:
- async only (aiofiles) — no blocking reads
- fail-safe on empty/corrupt files
- graceful skip when 0 examples
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest


class TestDspyEvidenceSeam:
    """Test DSPyOptimizer._load_training_examples() seam."""

    @pytest.fixture
    def mock_evidence_dir(self, tmp_path):
        """Create 3 sample evidence JSONL files with decision events."""
        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()

        # File 1: 2 decision events with query/result
        f1 = evidence_dir / "run_001.jsonl"
        f1.write_text(
            json.dumps(
                {
                    "event_type": "decision",
                    "payload": {
                        "query": "What infrastructure relates to target.com?",
                        "result": '{"hosts": ["1.2.3.4"], "domains": ["target.com"]}',
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "event_type": "action_executed",
                    "payload": {
                        "action_params": {"query": "Passive DNS for target.com"},
                        "response": {"content": '{"records": 42, "sources": ["ct"]}'},
                    },
                }
            )
            + "\n"
        )

        # File 2: 1 decision event
        f2 = evidence_dir / "run_002.jsonl"
        f2.write_text(
            json.dumps(
                {
                    "event_type": "decision",
                    "payload": {
                        "params": {"query": "Certificate transparency for target.com"},
                        "result": '{"certs": ["cert1", "cert2"]}',
                    },
                }
            )
            + "\n"
        )

        # File 3: non-decision events (should be filtered out)
        f3 = evidence_dir / "run_003.jsonl"
        f3.write_text(
            json.dumps({"event_type": "tool_call", "payload": {"query": "ignored"}})
            + "\n"
            + json.dumps(
                {
                    "event_type": "observation",
                    "payload": {"result": '{"data": "also ignored"}'},
                }
            )
            + "\n"
        )

        return evidence_dir

    @pytest.mark.asyncio
    async def test_load_training_examples_file_reading(self, mock_evidence_dir):
        """Test that _load_training_examples reads from EVIDENCE_ROOT files."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        optimizer = DSPyOptimizer(brain_manager=MagicMock())

        import hledac.universal.paths as paths_mod
        original_ev_root = paths_mod.EVIDENCE_ROOT

        try:
            paths_mod.EVIDENCE_ROOT = mock_evidence_dir
            examples = await optimizer._load_training_examples(limit=100)
        finally:
            paths_mod.EVIDENCE_ROOT = original_ev_root

        assert len(examples) == 3, f"Expected 3, got {len(examples)}: {examples}"
        queries = [q for q, _r in examples]
        assert "What infrastructure relates to target.com?" in queries
        assert "Passive DNS for target.com" in queries
        assert "Certificate transparency for target.com" in queries

    @pytest.mark.asyncio
    async def test_load_training_examples_graceful_skip_empty_file(self, tmp_path):
        """Optimizer necrashuje s 0 examples (graceful skip)."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        evidence_dir = tmp_path / "evidence_empty"
        evidence_dir.mkdir()

        empty_file = evidence_dir / "empty.jsonl"
        empty_file.write_text("")

        corrupt_file = evidence_dir / "corrupt.jsonl"
        corrupt_file.write_text("not valid json\nalso not valid\n")

        optimizer = DSPyOptimizer(brain_manager=MagicMock())

        import hledac.universal.paths as paths_mod
        original_ev_root = paths_mod.EVIDENCE_ROOT

        try:
            paths_mod.EVIDENCE_ROOT = evidence_dir
            examples = await optimizer._load_training_examples(limit=100)
        finally:
            paths_mod.EVIDENCE_ROOT = original_ev_root

        assert examples == [], f"Expected [], got {examples}"

    @pytest.mark.asyncio
    async def test_filter_training_examples_error_results(self):
        """Filter removes results with 'error' or 'failed' in them."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        optimizer = DSPyOptimizer(brain_manager=MagicMock())

        raw = [
            ("query one valid enough", "valid result with enough length here and even more content"),
            ("query two valid enough", "this result also passes quality checks successfully"),
            ("query three has error", "this result contains an error message in it"),
            ("query four has failed", "operation failed the validation checks"),
        ]

        filtered = optimizer._filter_training_examples(raw)

        assert len(filtered) == 2, f"Expected 2, got {len(filtered)}: {[x[0] for x in filtered]}"

    @pytest.mark.asyncio
    async def test_filter_training_examples_valid_inputs(self):
        """Both valid queries pass through filter."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        optimizer = DSPyOptimizer(brain_manager=MagicMock())

        raw = [
            ("What is the IP address?", "valid result with enough length here and more content"),
            (
                "What infrastructure relates to target.com?",
                "valid result with enough length and content here too",
            ),
        ]

        filtered = optimizer._filter_training_examples(raw)

        assert len(filtered) == 2, f"Expected 2, got {len(filtered)}: {[x[0] for x in filtered]}"

    @pytest.mark.asyncio
    async def test_run_optimization_no_crash_on_zero_examples(self):
        """_run_optimization fails gracefully when 0 examples after filtering."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        optimizer = DSPyOptimizer(brain_manager=MagicMock())

        with patch.object(
            optimizer, "_load_training_examples", return_value=[]
        ):
            await optimizer._run_optimization()

        assert optimizer._failure_count == 0

    @pytest.mark.asyncio
    async def test_run_optimization_circuit_breaker_on_dspy_failure(self):
        """Circuit breaker opens after 3 consecutive DSPy failures."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        optimizer = DSPyOptimizer(brain_manager=MagicMock())
        optimizer._failure_count = 2

        valid_examples = [
            (
                f"query {i} needs at least twenty chars",
                f"valid result with enough length here and even more content for testing purposes {i}"
            )
            for i in range(10)
        ]

        with patch.object(
            optimizer, "_load_training_examples", return_value=valid_examples
        ), patch.object(
            optimizer, "_dspy_optimize_mipro", side_effect=RuntimeError("DSPy MIPROv2 failed")
        ):
            await optimizer._run_optimization()

        assert optimizer._failure_count == 3, (
            f"Expected failure_count=3 after DSPy failure, got {optimizer._failure_count}"
        )
        assert time.time() < optimizer._circuit_open_until, (
            "Circuit breaker should be open"
        )
