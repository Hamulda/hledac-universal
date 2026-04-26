"""
Sprint F204F: Production CTI Export Wiring — Probe Tests

Tests:
  1. CTIExportInputs frozen dataclass frozen invariant
  2. CTIExportInputs all required fields present
  3. MAX_STIX_OBJECTS = 500
  4. MAX_EXPORT_FINDINGS = 300
  5. MAX_EXPORT_CHAINS = 20
  6. MAX_EXPORT_BYTES = 5_000_000
  7. collect_cti_export_inputs returns CTIExportInputs
  8. collect_cti_export_inputs fail-soft on store error
  9. collect_cti_export_inputs bounded by MAX_EXPORT_FINDINGS
 10. collect_cti_export_inputs chains bounded by MAX_EXPORT_CHAINS
 11. _run_export includes CTI STIX path in export_paths
 12. _run_cti_export fail-soft on error
 13. _run_cti_export re-raises CancelledError
 14. _run_cti_export run_in_executor for >1000 objects
 15. render_cti_stix_bundle_to_path in export __all__
 16. collect_cti_export_inputs in stix_exporter __all__
 17. CTIExportInputs in stix_exporter __all__
 18. scorecard has attribution field
 19. scorecard has wayback_diff field
 20. scorecard has embedding field
 21. scorecard has hypothesis_feedback field
 22. scorecard has circuit_state field
 23. smoke runner OK
"""

from __future__ import annotations

import asyncio
from dataclasses import fields as _dc_fields
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.export.stix_exporter import (
    CTIExportInputs,
    MAX_EXPORT_BYTES,
    MAX_EXPORT_CHAINS,
    MAX_EXPORT_FINDINGS,
    MAX_STIX_OBJECTS,
    collect_cti_export_inputs,
    render_cti_stix_bundle_to_path,
)


# ---------------------------------------------------------------------------
# 1-6: Bounds and CTIExportInputs dataclass invariants
# ---------------------------------------------------------------------------

class TestCTIExportInputsDataclass:
    """F204F-1: CTIExportInputs frozen dataclass invariants."""

    def test_cti_export_inputs_frozen(self):
        """Invariant: frozen=True prevents field mutation."""
        inputs = CTIExportInputs(
            findings=(),
            identity_candidates=(),
            attribution_scores={},
            killchain_tags={},
            evidence_chains=(),
            sprint_id="s-1",
        )
        with pytest.raises(Exception):
            inputs.sprint_id = "changed"  # type: ignore

    def test_cti_export_inputs_all_fields(self):
        """Invariant: all required fields present."""
        flds = {f.name for f in _dc_fields(CTIExportInputs)}
        expected = {
            "findings", "identity_candidates", "attribution_scores",
            "killchain_tags", "evidence_chains", "sprint_id",
        }
        assert expected <= flds, f"missing fields: {expected - flds}"

    def test_cti_export_inputs_tuple_fields(self):
        """Invariant: tuple fields are tuple type."""
        inputs = CTIExportInputs(
            findings=("f1",),
            identity_candidates=({"name": "x"},),
            attribution_scores={"a": 0.5},
            killchain_tags={"k": []},
            evidence_chains=({"id": "c1"},),
            sprint_id="s-1",
        )
        assert isinstance(inputs.findings, tuple)
        assert isinstance(inputs.identity_candidates, tuple)
        assert isinstance(inputs.evidence_chains, tuple)
        assert isinstance(inputs.attribution_scores, dict)
        assert isinstance(inputs.killchain_tags, dict)


class TestCTIExportBounds:
    """F204F-3-6: Bounds constants."""

    def test_max_stix_objects(self):
        """Invariant: MAX_STIX_OBJECTS = 500."""
        assert MAX_STIX_OBJECTS == 500

    def test_max_export_findings(self):
        """Invariant: MAX_EXPORT_FINDINGS = 300."""
        assert MAX_EXPORT_FINDINGS == 300

    def test_max_export_chains(self):
        """Invariant: MAX_EXPORT_CHAINS = 20."""
        assert MAX_EXPORT_CHAINS == 20

    def test_max_export_bytes(self):
        """Invariant: MAX_EXPORT_BYTES = 5_000_000."""
        assert MAX_EXPORT_BYTES == 5_000_000


# ---------------------------------------------------------------------------
# 7-10: collect_cti_export_inputs behavior
# ---------------------------------------------------------------------------

class TestCollectCTIExportInputs:
    """F204F-7-10: collect_cti_export_inputs async behavior."""

    @pytest.mark.asyncio
    async def test_returns_cti_export_inputs(self):
        """F204F-7: returns CTIExportInputs with correct fields."""
        mock_store = AsyncMock()
        mock_store.async_query_recent_findings = AsyncMock(return_value=[])

        report = {
            "run_id": "s-1",
            "identity_candidates": [],
            "attribution_scores": {"c1": {"score": 0.9}},
            "killchain_tags": {},
            "evidence_chains": [],
        }

        result = await collect_cti_export_inputs(report, mock_store)

        assert isinstance(result, CTIExportInputs)
        assert result.sprint_id == "s-1"
        assert result.attribution_scores == {"c1": {"score": 0.9}}

    @pytest.mark.asyncio
    async def test_fail_soft_on_store_error(self):
        """F204F-8: fail-soft when store raises."""
        mock_store = AsyncMock()
        mock_store.async_query_recent_findings = AsyncMock(side_effect=RuntimeError("DB error"))

        report = {"run_id": "s-1"}

        result = await collect_cti_export_inputs(report, mock_store)

        assert isinstance(result, CTIExportInputs)
        assert result.findings == ()
        assert result.sprint_id == "s-1"

    @pytest.mark.asyncio
    async def test_bounded_by_max_export_findings(self):
        """F204F-9: findings capped at MAX_EXPORT_FINDINGS=300."""
        mock_store = AsyncMock()
        findings_list = [{"finding_id": f"f{i}"} for i in range(300)]
        mock_store.async_query_recent_findings = AsyncMock(return_value=findings_list)

        report = {"run_id": "s-1"}

        result = await collect_cti_export_inputs(report, mock_store)

        # Store returns 300 items, limit param=MAX_EXPORT_FINDINGS=300
        assert len(result.findings) == 300

    @pytest.mark.asyncio
    async def test_chains_bounded_by_max_export_chains(self):
        """F204F-10: evidence chains capped at MAX_EXPORT_CHAINS=20."""
        mock_store = AsyncMock()
        mock_store.async_query_recent_findings = AsyncMock(return_value=[])

        chains_list = [{"id": f"c{i}"} for i in range(50)]
        report = {"run_id": "s-1", "evidence_chains": chains_list}

        result = await collect_cti_export_inputs(report, mock_store)

        assert len(result.evidence_chains) <= MAX_EXPORT_CHAINS


# ---------------------------------------------------------------------------
# 11-14: SprintScheduler CTI export wiring
# ---------------------------------------------------------------------------

class TestRunExportCTIWiring:
    """F204F-11-14: _run_export CTI wiring in SprintScheduler."""

    @pytest.mark.asyncio
    async def test_run_export_includes_cti_path(self):
        """F204F-11: _run_export appends CTI STIX path to export_paths."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._result = MagicMock()
        scheduler._result.export_paths = []
        scheduler._config = MagicMock()
        scheduler._config.export_dir = None
        scheduler._duckdb_store = AsyncMock()
        scheduler._duckdb_store.async_query_recent_findings = AsyncMock(return_value=[])

        lifecycle = MagicMock()
        lifecycle.current_phase.name = "WINDUP"
        lifecycle.snapshot.return_value = {}
        lifecycle.sprint_id = "s-f204f"

        with patch.object(scheduler, "_build_diagnostic_report", return_value={"run_id": "s-f204f"}):
            with patch("hledac.universal.runtime.sprint_scheduler._import_exporters") as imp:
                mock_cti_to_path = MagicMock(return_value="/tmp/ghost_cti_test.stix.json")
                mock_cti_collect = AsyncMock()
                imp.return_value = (
                    MagicMock(),
                    MagicMock(),
                    MagicMock(),
                    mock_cti_to_path,
                    mock_cti_collect,
                )

                await scheduler._run_export(lifecycle)

        # Should have entries for md, jsonld, stix.json, and cti stix
        cti_paths = [p for p in scheduler._result.export_paths if "ghost_cti" in p]
        assert len(cti_paths) >= 1, f"CTI path not in export_paths: {scheduler._result.export_paths}"

    @pytest.mark.asyncio
    async def test_run_cti_export_fail_soft(self):
        """F204F-12: _run_cti_export logs EXPORT_ERROR on failure."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._result = MagicMock()
        scheduler._result.export_paths = []
        scheduler._duckdb_store = AsyncMock()
        scheduler._duckdb_store.async_query_recent_findings = AsyncMock(return_value=[])

        report = {"run_id": "s-f204f"}
        mock_render = MagicMock(side_effect=RuntimeError("Render failed"))
        mock_collect = AsyncMock()

        await scheduler._run_cti_export(mock_render, mock_collect, report, None)

        error_paths = [p for p in scheduler._result.export_paths if p.startswith("EXPORT_ERROR")]
        assert len(error_paths) >= 1, "fail-soft did not record EXPORT_ERROR"

    @pytest.mark.asyncio
    async def test_run_cti_export_cancelled_error_raised(self):
        """F204F-13: _run_cti_export re-raises CancelledError."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._result = MagicMock()
        scheduler._result.export_paths = []
        scheduler._duckdb_store = AsyncMock()

        report = {"run_id": "s-f204f"}
        mock_render = MagicMock()
        mock_collect = AsyncMock()

        # Simulate CancelledError during render
        mock_render.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await scheduler._run_cti_export(mock_render, mock_collect, report, None)

    @pytest.mark.asyncio
    async def test_run_cti_export_run_in_executor_large_findings(self):
        """F204F-14: >1000 findings uses run_in_executor."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._result = MagicMock()
        scheduler._result.export_paths = []
        scheduler._duckdb_store = AsyncMock()

        large_findings = [{"finding_id": f"f{i}"} for i in range(1500)]
        report = {"run_id": "s-f204f"}
        mock_render = MagicMock(return_value="/tmp/large.stix.json")
        mock_collect = AsyncMock()

        # Provide CTIExportInputs with >1000 findings
        cti_inputs = CTIExportInputs(
            findings=tuple(large_findings),
            identity_candidates=(),
            attribution_scores={},
            killchain_tags={},
            evidence_chains=(),
            sprint_id="s-f204f",
        )
        mock_collect.return_value = cti_inputs

        # Patch run_in_executor to return the path directly
        mock_executor = AsyncMock(return_value="/tmp/large.stix.json")
        mock_loop = MagicMock()
        mock_loop.run_in_executor = mock_executor

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            await scheduler._run_cti_export(mock_render, mock_collect, report, None)

        # Should have called run_in_executor for large payload
        assert mock_executor.called, "run_in_executor not called for >1000 findings"


# ---------------------------------------------------------------------------
# 15-17: __all__ exports
# ---------------------------------------------------------------------------

class TestExportsAll:
    """F204F-15-17: __all__ exports."""

    def test_render_cti_stix_bundle_to_path_in_export_all(self):
        """F204F-15: render_cti_stix_bundle_to_path in export/__init__/__all__."""
        from hledac.universal.export import __all__ as all_exports
        assert "render_cti_stix_bundle_to_path" in all_exports

    def test_collect_cti_export_inputs_in_stix_all(self):
        """F204F-16: collect_cti_export_inputs in stix_exporter.__all__."""
        from hledac.universal.export.stix_exporter import __all__ as all_exports
        assert "collect_cti_export_inputs" in all_exports

    def test_cti_export_inputs_in_stix_all(self):
        """F204F-17: CTIExportInputs in stix_exporter.__all__."""
        from hledac.universal.export.stix_exporter import __all__ as all_exports
        assert "CTIExportInputs" in all_exports


# ---------------------------------------------------------------------------
# 18-22: scorecard enrichment fields
# ---------------------------------------------------------------------------

class TestScorecardEnrichmentFields:
    """F204F-18-22: scorecard F204F fields in product_value_summary."""

    def test_scorecard_has_attribution_field(self):
        """F204F-18: attribution field in scorecard."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = None

        eh = ExportHandoff(
            sprint_id="s-f204f",
            scorecard={"accepted_findings": 5},
            canonical_run_summary={"attribution": {"entity": "Test", "confidence": 0.8}},
        )

        summary = _build_product_value_summary(mock_store, eh, "s-f204f")

        assert "attribution" in summary
        assert summary["attribution"] == {"entity": "Test", "confidence": 0.8}

    def test_scorecard_has_wayback_diff_field(self):
        """F204F-19: wayback_diff field in scorecard."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = None

        eh = ExportHandoff(
            sprint_id="s-f204f",
            scorecard={},
            canonical_run_summary={"wayback_diff": {"added": 3, "removed": 1}},
        )

        summary = _build_product_value_summary(mock_store, eh, "s-f204f")

        assert "wayback_diff" in summary
        assert summary["wayback_diff"] == {"added": 3, "removed": 1}

    def test_scorecard_has_embedding_field(self):
        """F204F-20: embedding field in scorecard."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = None

        eh = ExportHandoff(
            sprint_id="s-f204f",
            scorecard={},
            canonical_run_summary={"embedding": {"dimensions": 384, "model": "test-v1"}},
        )

        summary = _build_product_value_summary(mock_store, eh, "s-f204f")

        assert "embedding" in summary

    def test_scorecard_has_hypothesis_feedback_field(self):
        """F204F-21: hypothesis_feedback field in scorecard."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = None

        eh = ExportHandoff(
            sprint_id="s-f204f",
            scorecard={},
            canonical_run_summary={"hypothesis_feedback": {"confirmed": 2, "rejected": 1}},
        )

        summary = _build_product_value_summary(mock_store, eh, "s-f204f")

        assert "hypothesis_feedback" in summary

    def test_scorecard_has_circuit_state_field(self):
        """F204F-22: circuit_state field in scorecard."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = None

        eh = ExportHandoff(
            sprint_id="s-f204f",
            scorecard={"circuit_state": {"open_domains": ["evil.com"], "last_failure": "2026-04-26"}},
            canonical_run_summary={},
        )

        summary = _build_product_value_summary(mock_store, eh, "s-f204f")

        assert "circuit_state" in summary


# ---------------------------------------------------------------------------
# 23: smoke runner
# ---------------------------------------------------------------------------

class TestSmoke:
    """F204F-23: smoke runner."""

    def test_smoke_collect_cti_inputs_empty(self):
        """F204F-23a: collect_cti_export_inputs smoke — empty inputs."""
        import asyncio

        async def _smoke():
            mock_store = AsyncMock()
            mock_store.async_query_recent_findings = AsyncMock(return_value=[])
            report = {"run_id": "smoke-test"}
            result = await collect_cti_export_inputs(report, mock_store)
            assert isinstance(result, CTIExportInputs)
            return True

        assert asyncio.run(_smoke())

    def test_smoke_cti_export_inputs_creation(self):
        """F204F-23b: CTIExportInputs can be created with all fields."""
        inputs = CTIExportInputs(
            findings=(dict(finding_id="f1", ioc_type="ip", ioc_value="1.2.3.4"),),
            identity_candidates=(dict(candidate_id="c1", primary_name="Test Entity"),),
            attribution_scores={"c1": {"score": 0.9, "method": "rule-based"}},
            killchain_tags={"f1": [{"phase": "reconnaissance", "name": "test"}]},
            evidence_chains=(dict(chain_id="ch1", type="time_based", events=[]),),
            sprint_id="smoke-f204f",
        )
        assert inputs.sprint_id == "smoke-f204f"
        assert len(inputs.findings) == 1
        assert len(inputs.identity_candidates) == 1
        assert len(inputs.evidence_chains) == 1
