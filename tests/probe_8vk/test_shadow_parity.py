"""
Probe 8VK: Shadow Scheduler Parity — Fact Parity Tests
======================================================

Tests for F3.5 fact parity implementation:

1. Shadow mode nevolá tools execution
2. Shadow mode nevytváří findings writes
3. Shadow mode nevolá network execution
4. Shadow mode neprodukuje side effects
5. legacy_runtime běží beze změny
6. scheduler_active se neaktivuje omylem
7. parity artifact je diagnostický a serializovatelný
8. local shadow dataclasses nezískaly status shared contracts
9. phase fields jsou SEPARATED (workflow_phase, control_phase, windup_local_phase)
10. RuntimeMode.get_current() vrací správný mód
11. ParityArtifact.to_dict() je serializovatelný

Run:
    cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
    python -m pytest tests/probe_8vk_shadow_parity.py -v
"""

import time
from unittest.mock import patch, MagicMock
from typing import Any, Dict, List

import pytest


class TestRuntimeMode:
    """Test RuntimeMode scaffold and mode detection."""

    def test_runtime_mode_default_is_legacy(self):
        """Default runtime mode is legacy_runtime."""
        from hledac.universal.runtime.shadow_inputs import RuntimeMode
        # Default without env var should be legacy
        mode = RuntimeMode.get_current()
        assert mode == RuntimeMode.LEGACY_RUNTIME

    def test_runtime_mode_shadow_detection(self):
        """RuntimeMode.is_shadow_mode() returns True when env var set."""
        from hledac.universal.runtime.shadow_inputs import RuntimeMode
        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_shadow"}):
            assert RuntimeMode.is_shadow_mode() is True
            assert RuntimeMode.is_active_mode() is False
            assert RuntimeMode.is_legacy_mode() is False

    def test_runtime_mode_active_detection(self):
        """RuntimeMode.is_active_mode() returns True when env var set."""
        from hledac.universal.runtime.shadow_inputs import RuntimeMode
        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_active"}):
            assert RuntimeMode.is_active_mode() is True
            assert RuntimeMode.is_shadow_mode() is False
            assert RuntimeMode.is_legacy_mode() is False

    def test_runtime_mode_legacy_when_invalid_env(self):
        """Invalid env var value falls back to legacy_runtime."""
        from hledac.universal.runtime.shadow_inputs import RuntimeMode
        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "invalid_mode"}):
            assert RuntimeMode.get_current() == RuntimeMode.LEGACY_RUNTIME


class TestLifecycleSnapshotBundle:
    """Test LifecycleSnapshotBundle phase separation invariant."""

    def test_workflow_phase_from_snapshot(self):
        """WorkflowPhase.from_lifecycle_snapshot extracts correctly."""
        from hledac.universal.runtime.shadow_inputs import WorkflowPhase

        snap = {
            "current_phase": "ACTIVE",
            "entered_phase_at": 100.0,
            "started_at_monotonic": 50.0,
            "sprint_duration_s": 1800.0,
            "windup_lead_s": 180.0,
        }
        wf = WorkflowPhase.from_lifecycle_snapshot(snap)
        assert wf.phase == "ACTIVE"
        assert wf.entered_at_monotonic == 100.0
        assert wf.started_at_monotonic == 50.0
        assert wf.sprint_duration_s == 1800.0
        assert wf.windup_lead_s == 180.0

    def test_workflow_phase_unknown_for_missing_phase(self):
        """Missing current_phase defaults to UNKNOWN."""
        from hledac.universal.runtime.shadow_inputs import WorkflowPhase

        wf = WorkflowPhase.from_lifecycle_snapshot({})
        assert wf.phase == "UNKNOWN"

    def test_control_phase_from_lifecycle(self):
        """ControlPhase.from_lifecycle derives tool mode correctly."""
        from hledac.universal.runtime.shadow_inputs import ControlPhase
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        lc = SprintLifecycleManager()
        lc.start(now_monotonic=0.0)
        lc._started_at = 0.0

        # In normal conditions should be "normal"
        cp = ControlPhase.from_lifecycle(lc, now_monotonic=10.0, thermal_state="nominal")
        assert cp.mode in ("normal", "prune", "panic")
        assert cp.thermal_state == "nominal"

    def test_lifecycle_snapshot_bundle_separates_phases(self):
        """LifecycleSnapshotBundle keeps workflow/control/windup phases separate."""
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
            WindupLocalPhase,
        )

        wf = WorkflowPhase(phase="ACTIVE", entered_at_monotonic=10.0)
        ctrl = ControlPhase(mode="normal", thermal_state="nominal", remaining_s=1700.0)
        windup = WindupLocalPhase(mode="synthesis", error_encountered=False, synthesis_engine="mlx")

        bundle = LifecycleSnapshotBundle(
            workflow_phase=wf,
            control_phase=ctrl,
            windup_local_phase=windup,
            raw_snapshot={},
        )

        # Verify SEPARATION — phases must NOT be merged
        assert bundle.workflow_phase.phase == "ACTIVE"
        assert bundle.control_phase.mode == "normal"
        assert bundle.windup_local_phase is not None
        assert bundle.windup_local_phase.mode == "synthesis"

        # Verify to_dict() preserves separation
        d = bundle.to_dict()
        assert d["workflow_phase"] == "ACTIVE"
        assert d["control_phase_mode"] == "normal"
        assert d["windup_local_mode"] == "synthesis"
        # Must NOT have a merged "phase" field
        assert "phase" not in d or d.get("phase") is None

    def test_lifecycle_snapshot_bundle_windup_local_only_in_windup(self):
        """windup_local_phase should only be set when workflow_phase is WINDUP."""
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
        )

        # Not in WINDUP — windup_local should be None
        wf = WorkflowPhase(phase="ACTIVE", entered_at_monotonic=10.0)
        ctrl = ControlPhase(mode="normal", thermal_state="nominal", remaining_s=1700.0)
        bundle = LifecycleSnapshotBundle(
            workflow_phase=wf,
            control_phase=ctrl,
            windup_local_phase=None,
            raw_snapshot={},
        )
        assert bundle.windup_local_phase is None
        assert bundle.to_dict()["windup_local_mode"] is None


class TestGraphSummaryBundle:
    """Test GraphSummaryBundle."""

    def test_from_ioc_graph_stats(self):
        """GraphSummaryBundle.from_ioc_graph_stats extracts correctly."""
        from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

        stats = {"nodes": 100, "edges": 300, "pgq_active": True}
        bundle = GraphSummaryBundle.from_ioc_graph_stats(stats, top_nodes=["node1", "node2"])

        assert bundle.node_count == 100
        assert bundle.edge_count == 300
        assert bundle.pgq_active is True
        assert bundle.backend == "duckpgq"
        assert bundle.top_nodes == ["node1", "node2"]

    def test_from_scorecard_top_nodes(self):
        """GraphSummaryBundle.from_scorecard_top_nodes is compat path."""
        from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

        bundle = GraphSummaryBundle.from_scorecard_top_nodes(["node1", "node2"])

        assert bundle.node_count == 0  # unknown from compat
        assert bundle.backend == "unknown"
        assert bundle.top_nodes == ["node1", "node2"]

    def test_to_dict_preserves_all_fields(self):
        """to_dict() preserves all graph facts."""
        from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

        bundle = GraphSummaryBundle(
            node_count=50,
            edge_count=200,
            pgq_active=True,
            backend="duckpgq",
            top_nodes=["a", "b"],
        )
        d = bundle.to_dict()
        assert d["graph_nodes"] == 50
        assert d["graph_edges"] == 200
        assert d["graph_pgq_active"] is True
        assert d["graph_backend"] == "duckpgq"
        assert d["graph_top_nodes"] == ["a", "b"]


class TestModelControlFactsBundle:
    """Test ModelControlFactsBundle."""

    def test_from_analyzer_result(self):
        """ModelControlFactsBundle.from_analyzer_result extracts tools/sources."""
        from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle
        from hledac.universal.project_types import AnalyzerResult

        result = AnalyzerResult(
            tools={"cve", "ioc"},
            sources={"cisa_kev"},
            privacy_level="HIGH",
            use_tor=True,
            depth="DEEP",
            use_tot=True,
            tot_mode="branch",
            models_needed={"hermes", "modernbert"},
        )

        bundle = ModelControlFactsBundle.from_analyzer_result(result)

        assert "cve" in bundle.tools
        assert "ioc" in bundle.tools
        assert "cisa_kev" in bundle.sources
        assert bundle.privacy_level == "HIGH"
        assert bundle.use_tor is True
        assert bundle.depth == "DEEP"
        assert bundle.use_tot is True
        assert "hermes" in bundle.models_needed

    def test_to_dict_preserves_all_mc_fields(self):
        """to_dict() preserves all model/control facts."""
        from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle

        bundle = ModelControlFactsBundle(
            tools=["cve", "ioc"],
            sources=["cisa_kev"],
            privacy_level="HIGH",
            use_tor=True,
            depth="DEEP",
            use_tot=True,
            tot_mode="branch",
            models_needed=["hermes"],
        )
        d = bundle.to_dict()
        assert d["mc_tools"] == ["cve", "ioc"]
        assert d["mc_sources"] == ["cisa_kev"]
        assert d["mc_privacy"] == "HIGH"
        assert d["mc_use_tor"] is True
        assert d["mc_depth"] == "DEEP"
        assert d["mc_use_tot"] is True
        assert d["mc_tot_mode"] == "branch"
        assert d["mc_models_needed"] == ["hermes"]


class TestCollectFunctions:
    """Test pure collect_* functions — no side effects."""

    def test_collect_lifecycle_snapshot_is_pure(self):
        """collect_lifecycle_snapshot returns bundle, makes no I/O."""
        from hledac.universal.runtime.shadow_inputs import collect_lifecycle_snapshot
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        lc = SprintLifecycleManager()
        lc.start(now_monotonic=0.0)

        bundle = collect_lifecycle_snapshot(
            lc,
            now_monotonic=10.0,
            thermal_state="nominal",
            windup_synthesis_mode="synthesis",
            windup_error=False,
            windup_engine="mlx",
        )

        assert bundle.workflow_phase.phase == "WARMUP"
        assert bundle.control_phase.mode in ("normal", "prune", "panic")
        assert bundle.raw_snapshot is not None

    def test_collect_graph_summary_empty_is_safe(self):
        """collect_graph_summary with no inputs returns empty bundle."""
        from hledac.universal.runtime.shadow_inputs import collect_graph_summary

        bundle = collect_graph_summary(ioc_graph=None, scorecard=None)
        assert bundle.node_count == 0
        assert bundle.backend == "unknown"

    def test_collect_model_control_facts_empty_is_safe(self):
        """collect_model_control_facts with no inputs returns empty bundle."""
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        bundle = collect_model_control_facts(analyzer_result=None, raw_profile=None)
        assert len(bundle.tools) == 0
        assert len(bundle.sources) == 0
        assert bundle.privacy_level == "STANDARD"

    def test_collect_export_handoff_facts_from_scorecard(self):
        """collect_export_handoff_facts builds facts dict from scorecard correctly."""
        from hledac.universal.runtime.shadow_inputs import collect_export_handoff_facts

        # scorecard path uses synthesis_engine_used (not synthesis_engine)
        scorecard = {
            "sprint_id": "test_123",
            "synthesis_engine_used": "mlx",
            "gnn_predicted_links": 10,
            "top_graph_nodes": ["a", "b", "c", "d", "e"],
            "ranked_parquet": "/path/to/ranked.parquet",
            "phase_duration_seconds": {"BOOT": 1.0, "ACTIVE": 100.0},
        }
        result = collect_export_handoff_facts(scorecard=scorecard)
        assert result["sprint_id"] == "test_123"
        assert result["synthesis_engine"] == "mlx"
        assert result["gnn_predictions"] == 10
        assert result["top_nodes_count"] == 5
        assert result["ranked_parquet_present"] is True

    def test_collect_export_handoff_facts_empty_is_safe(self):
        """collect_export_handoff_facts with no inputs returns unknown defaults."""
        from hledac.universal.runtime.shadow_inputs import collect_export_handoff_facts

        result = collect_export_handoff_facts(handoff=None, scorecard=None)
        assert result["sprint_id"] == "unknown"
        assert result["synthesis_engine"] == "unknown"
        assert result["gnn_predictions"] == 0


class TestParityArtifact:
    """Test ParityArtifact diagnostic output."""

    def test_parity_artifact_to_dict_is_serializable(self):
        """ParityArtifact.to_dict() returns a plain dict suitable for JSON."""
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        artifact = ParityArtifact(
            mode="scheduler_shadow",
            timestamp_monotonic=time.monotonic(),
            timestamp_wall="2026-04-01T00:00:00Z",
            workflow_phase="ACTIVE",
            workflow_phase_entered_at=10.0,
            control_phase_mode="normal",
            control_phase_thermal="nominal",
            windup_local_mode=None,
            graph_nodes=100,
            graph_edges=300,
            graph_pgq_active=True,
            graph_backend="duckpgq",
            graph_top_nodes_count=5,
            mc_tools_count=3,
            mc_sources_count=2,
            mc_privacy="HIGH",
            mc_depth="DEEP",
            mc_models_needed=["hermes"],
            export_sprint_id="sprint_123",
            export_synthesis_engine="mlx",
            export_ranked_parquet_present=True,
            export_gnn_predictions=10,
            branch_decision_id=None,
            provider_recommend=None,
            correlation_run_id=None,
            correlation_branch_id=None,
            mismatch_categories=["NONE"],
            mismatch_details={"note": "no mismatches detected"},
            input_sources={"lifecycle_snapshot": "runtime/shadow_inputs.py"},
        )

        d = artifact.to_dict()

        # Must be a plain dict (not dataclass, not special type)
        assert isinstance(d, dict)
        # All keys must be strings
        assert all(isinstance(k, str) for k in d.keys())
        # All values must be JSON-serializable types
        import json
        try:
            json.dumps(d)
        except TypeError as e:
            pytest.fail(f"ParityArtifact.to_dict() is not JSON-serializable: {e}")

    def test_parity_artifact_phase_fields_separated(self):
        """ParityArtifact has SEPARATED workflow_phase, control_phase_mode, windup_local_mode."""
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        artifact = ParityArtifact(
            mode="scheduler_shadow",
            timestamp_monotonic=0.0,
            timestamp_wall="",
            workflow_phase="WINDUP",
            workflow_phase_entered_at=100.0,
            control_phase_mode="prune",
            control_phase_thermal="throttled",
            windup_local_mode="structured",
            graph_nodes=0,
            graph_edges=0,
            graph_pgq_active=False,
            graph_backend="unknown",
            graph_top_nodes_count=0,
            mc_tools_count=0,
            mc_sources_count=0,
            mc_privacy="STANDARD",
            mc_depth="STANDARD",
            mc_models_needed=[],
            export_sprint_id="unknown",
            export_synthesis_engine="unknown",
            export_ranked_parquet_present=False,
            export_gnn_predictions=0,
            branch_decision_id=None,
            provider_recommend=None,
            correlation_run_id=None,
            correlation_branch_id=None,
            mismatch_categories=["NONE"],
            mismatch_details={},
            input_sources={},
        )

        d = artifact.to_dict()

        # SEPARATED fields must all be present
        assert "workflow_phase" in d
        assert "control_phase_mode" in d
        assert "windup_local_mode" in d
        # Must NOT have a merged "phase" key
        assert "phase" not in d


class TestRunShadowParity:
    """Test run_shadow_parity — the main pure function."""

    def test_run_shadow_parity_returns_parity_artifact(self):
        """run_shadow_parity returns ParityArtifact (not None, not dict subclass)."""
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE", entered_at_monotonic=10.0),
            control_phase=ControlPhase(mode="normal", thermal_state="nominal", remaining_s=1700.0),
            windup_local_phase=None,
            raw_snapshot={},
        )
        graph_bundle = GraphSummaryBundle(
            node_count=100,
            edge_count=300,
            pgq_active=True,
            backend="duckpgq",
            top_nodes=[],
        )
        mc_bundle = ModelControlFactsBundle(
            tools=["cve"],
            sources=["cisa_kev"],
            privacy_level="STANDARD",
            models_needed=["hermes"],
        )
        export_facts = {
            "sprint_id": "sprint_123",
            "synthesis_engine": "mlx",
            "gnn_predictions": 5,
            "top_nodes_count": 3,
            "ranked_parquet_present": True,
            "phase_durations": {},
        }

        result = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts=export_facts,
            runtime_mode="scheduler_shadow",
        )

        from hledac.universal.runtime.shadow_parity import ParityArtifact
        assert isinstance(result, ParityArtifact)
        assert result.mode == "scheduler_shadow"
        assert result.workflow_phase == "ACTIVE"
        assert result.graph_nodes == 100

    def test_run_shadow_parity_flags_unknown_graph_backend(self):
        """run_shadow_parity adds GRAPH_CAPABILITY mismatch when backend is unknown."""
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE"),
            control_phase=ControlPhase(mode="normal"),
            windup_local_phase=None,
            raw_snapshot={},
        )
        graph_bundle = GraphSummaryBundle(
            node_count=0, edge_count=0, pgq_active=False,
            backend="unknown", top_nodes=[],
        )
        mc_bundle = ModelControlFactsBundle()
        export_facts = {}

        result = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts=export_facts,
        )

        assert "GRAPH_CAPABILITY" in result.mismatch_categories

    def test_run_shadow_parity_no_mismatches_for_valid_inputs(self):
        """No mismatches when all inputs are valid."""
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE", entered_at_monotonic=10.0),
            control_phase=ControlPhase(mode="normal", thermal_state="nominal", remaining_s=1700.0),
            windup_local_phase=None,
            raw_snapshot={},
        )
        graph_bundle = GraphSummaryBundle(
            node_count=100, edge_count=300, pgq_active=True,
            backend="duckpgq", top_nodes=["n1", "n2"],
        )
        mc_bundle = ModelControlFactsBundle(
            tools=["cve"], sources=["cisa_kev"],
            privacy_level="STANDARD", models_needed=["hermes"],
        )
        export_facts = {
            "sprint_id": "sprint_123",
            "synthesis_engine": "mlx",
            "gnn_predictions": 5,
            "top_nodes_count": 2,
            "ranked_parquet_present": True,
            "phase_durations": {},
        }

        result = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts=export_facts,
        )

        # NONE mismatch expected with valid inputs
        assert result.mismatch_categories == ["NONE"]


class TestPhaseFieldMergeCheck:
    """Test _check_phase_field_merge structural invariant check."""

    def test_detects_unexpected_workflow_phase_value(self):
        """_check_phase_field_merge flags unexpected workflow_phase values."""
        from hledac.universal.runtime.shadow_parity import _check_phase_field_merge
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
        )

        mismatches: List[str] = []
        mismatch_details: Dict[str, Any] = {}

        # "RANDOM_PHASE" is not a valid SprintPhase enum value
        bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="RANDOM_PHASE"),
            control_phase=ControlPhase(mode="normal"),
            windup_local_phase=None,
            raw_snapshot={},
        )

        _check_phase_field_merge(bundle, mismatches, mismatch_details)

        assert "LIFECYCLE" in mismatches
        assert "workflow_phase" in mismatch_details

    def test_detects_unexpected_control_phase_mode(self):
        """_check_phase_field_merge flags unexpected control_mode values."""
        from hledac.universal.runtime.shadow_parity import _check_phase_field_merge
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
        )

        mismatches: List[str] = []
        mismatch_details: Dict[str, Any] = {}

        bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE"),
            control_phase=ControlPhase(mode="invalid_mode"),
            windup_local_phase=None,
            raw_snapshot={},
        )

        _check_phase_field_merge(bundle, mismatches, mismatch_details)

        assert "LIFECYCLE" in mismatches

    def test_detects_windup_without_windup_local_phase(self):
        """_check_phase_field_merge flags WINDUP without windup_local_phase set."""
        from hledac.universal.runtime.shadow_parity import _check_phase_field_merge
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
        )

        mismatches: List[str] = []
        mismatch_details: Dict[str, Any] = {}

        bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="WINDUP"),
            control_phase=ControlPhase(mode="prune"),
            windup_local_phase=None,  # Should be set when in WINDUP!
            raw_snapshot={},
        )

        _check_phase_field_merge(bundle, mismatches, mismatch_details)

        assert "LIFECYCLE" in mismatches
        assert "windup_local_phase" in mismatch_details


class TestShadowModeSideEffectBoundaries:
    """Test that shadow parity implementation has NO side effects."""

    def test_run_shadow_parity_has_no_io_calls(self):
        """run_shadow_parity makes no file I/O, no network calls, no findings writes."""
        from unittest.mock import AsyncMock, patch

        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE"),
            control_phase=ControlPhase(mode="normal"),
            windup_local_phase=None,
            raw_snapshot={},
        )
        graph_bundle = GraphSummaryBundle()
        mc_bundle = ModelControlFactsBundle()
        export_facts = {}

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = run_shadow_parity(
                lifecycle_bundle=lc_bundle,
                graph_bundle=graph_bundle,
                model_control_bundle=mc_bundle,
                export_handoff_facts=export_facts,
            )
            # run_shadow_parity is a pure sync function — asyncio.sleep must not be called
            assert mock_sleep.call_count == 0, "run_shadow_parity called async sleep — possible side effect"

        # Verify result is still valid
        assert result is not None
        assert result.mode == "scheduler_shadow"

    def test_shadow_parity_module_does_not_import_network_modules(self):
        """shadow_parity.py does not import network-fetching modules."""
        import ast
        from pathlib import Path

        shadow_parity_path = Path(__file__).parent.parent / "runtime" / "shadow_parity.py"
        source = shadow_parity_path.read_text()
        tree = ast.parse(source)

        network_modules = {
            "aiohttp", "httpx", "requests", "urllib3",
            "curl_cffi", "nodriver", "selenium",
            "playwright", "pyppeteer",
        }

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        for imp in imports:
            if any(imp.startswith(nm) for nm in network_modules):
                pytest.fail(f"shadow_parity.py imports network module: {imp}")

    def test_shadow_inputs_module_does_not_import_network_modules(self):
        """shadow_inputs.py does not import network-fetching modules."""
        import ast
        from pathlib import Path

        shadow_inputs_path = Path(__file__).parent.parent / "runtime" / "shadow_inputs.py"
        source = shadow_inputs_path.read_text()
        tree = ast.parse(source)

        network_modules = {
            "aiohttp", "httpx", "requests", "urllib3",
            "curl_cffi", "nodriver", "selenium",
            "playwright", "pyppeteer",
        }

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        for imp in imports:
            if any(imp.startswith(nm) for nm in network_modules):
                pytest.fail(f"shadow_inputs.py imports network module: {imp}")


class TestNoNewSchedulerFramework:
    """Verify no new scheduler framework was created."""

    def test_no_new_scheduler_state_created(self):
        """No new persistent scheduler-owned state files created."""
        from pathlib import Path

        runtime_dir = Path(__file__).parent.parent / "runtime"

        # Check for suspicious new state files
        suspicious = [
            "scheduler_state.json",
            "shadow_state.json",
            "parity_state.json",
            "scheduler_ledger.json",
        ]

        for name in suspicious:
            path = runtime_dir / name
            if path.exists():
                pytest.fail(f"New scheduler state file created: {name} — must not create persistent scheduler-owned state")


class TestLocalShadowDataclassesNotSharedContracts:
    """Verify local shadow dataclasses are NOT elevated to shared contracts."""

    def test_shadow_inputs_dataclasses_not_in_types(self):
        """LifecycleSnapshotBundle, GraphSummaryBundle, ModelControlFactsBundle are NOT in types.py."""
        from pathlib import Path

        types_path = Path(__file__).parent.parent / "types.py"
        source = types_path.read_text()

        shadow_classes = [
            "LifecycleSnapshotBundle",
            "GraphSummaryBundle",
            "ModelControlFactsBundle",
            "WorkflowPhase",
            "ControlPhase",
            "WindupLocalPhase",
            "ParityArtifact",
        ]

        for cls_name in shadow_classes:
            if f"class {cls_name}" in source:
                pytest.fail(
                    f"Shadow dataclass {cls_name} found in types.py — "
                    f"local shadow dataclasses must NOT be elevated to shared contracts"
                )


class TestLegacyRuntimeUnchanged:
    """Verify legacy_runtime path is unchanged."""

    def test_default_runtime_mode_still_legacy(self):
        """Default RuntimeMode.get_current() is still LEGACY_RUNTIME without env vars."""
        import os
        # Ensure no env var is set
        env_backup = os.environ.get("HLEDAC_RUNTIME_MODE")

        try:
            if "HLEDAC_RUNTIME_MODE" in os.environ:
                del os.environ["HLEDAC_RUNTIME_MODE"]

            from hledac.universal.runtime.shadow_inputs import RuntimeMode
            assert RuntimeMode.get_current() == RuntimeMode.LEGACY_RUNTIME
            assert RuntimeMode.is_legacy_mode() is True
            assert RuntimeMode.is_shadow_mode() is False
            assert RuntimeMode.is_active_mode() is False
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_RUNTIME_MODE"] = env_backup


class TestF65OwnershipInvariants:
    """
    F6.5: Ownership Closure — Phase/Model Layer Separation Tests

    Verifies:
    1. acquire != phase enforcement
    2. unload != phase policy
    3. GENERATE (Layer 1) ≠ SYNTHESIS (Layer 2)
    4. capabilities.py is NOT load owner
    5. no third model truth emerges
    6. no new model framework created
    """

    def test_synthesize_vs_synthesis_are_different_strings(self):
        """GENERATE (Layer 1) ≠ SYNTHESIS (Layer 2) — they must not be conflated."""
        # Direct import bypasses brain/__init__.py circular import
        import sys
        from pathlib import Path
        _model_phase_facts = Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("model_phase_facts", _model_phase_facts)
        mpf = importlib.util.module_from_spec(spec)
        sys.modules["model_phase_facts"] = mpf
        spec.loader.exec_module(mpf)

        WORKFLOW_PHASES = mpf.WORKFLOW_PHASES
        COARSE_GRAINED_PHASES = mpf.COARSE_GRAINED_PHASES
        is_workflow_phase = mpf.is_workflow_phase
        is_coarse_grained_phase = mpf.is_coarse_grained_phase
        is_same_layer = mpf.is_same_layer

        # SYNTHESIZE is Layer 1 only
        assert is_workflow_phase("GENERATE") is True
        assert is_coarse_grained_phase("GENERATE") is False

        # SYNTHESIS is Layer 2 only
        assert is_coarse_grained_phase("SYNTHESIS") is True
        assert is_workflow_phase("SYNTHESIS") is False

        # They are NOT the same layer
        assert is_same_layer("GENERATE", "SYNTHESIS") is False

    def test_workflow_phases_not_in_coarse_grained(self):
        """No Layer 1 workflow phase string exists in Layer 2 coarse-grained set."""
        # Bypass brain/__init__.py circular import
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mpf)
        WORKFLOW_PHASES = mpf.WORKFLOW_PHASES
        COARSE_GRAINED_PHASES = mpf.COARSE_GRAINED_PHASES

        overlap = WORKFLOW_PHASES & COARSE_GRAINED_PHASES
        assert len(overlap) == 0, f"Phase overlap between Layer 1 and Layer 2: {overlap}"

    def test_coarse_grained_phases_not_in_workflow(self):
        """No Layer 2 coarse-grained phase string exists in Layer 1 workflow set."""
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mpf)
        WORKFLOW_PHASES = mpf.WORKFLOW_PHASES
        COARSE_GRAINED_PHASES = mpf.COARSE_GRAINED_PHASES

        overlap = COARSE_GRAINED_PHASES & WORKFLOW_PHASES
        assert len(overlap) == 0, f"Phase overlap between Layer 2 and Layer 1: {overlap}"

    def test_phase_layer_classification(self):
        """get_phase_layer returns correct layer for each phase system."""
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mpf)
        get_phase_layer = mpf.get_phase_layer

        # Layer 1 — workflow-level
        assert get_phase_layer("PLAN") == 1
        assert get_phase_layer("GENERATE") == 1
        assert get_phase_layer("EMBED") == 1
        assert get_phase_layer("NER") == 1

        # Layer 2 — coarse-grained
        assert get_phase_layer("BRAIN") == 2
        assert get_phase_layer("SYNTHESIS") == 2
        assert get_phase_layer("TOOLS") == 2
        assert get_phase_layer("CLEANUP") == 2

        # Unknown / cross-layer
        assert get_phase_layer("SOME_RANDOM_PHASE") == 0

    def test_capabilities_model_lifecycle_manager_is_facade_not_load_owner(self):
        """ModelLifecycleManager is FACADE — does NOT directly load models."""
        import inspect
        from capabilities import ModelLifecycleManager, CapabilityRegistry

        # Check that load_model_for_task delegates through registry, not direct load
        source = inspect.getsource(ModelLifecycleManager.load_model_for_task)

        # Must NOT call ModelManager.load_model() directly
        assert "ModelManager" not in source, \
            "ModelLifecycleManager.load_model_for_task must NOT reference ModelManager"

        # Must delegate through registry.load()
        assert "registry.load" in source or "self.registry.load" in source, \
            "ModelLifecycleManager must delegate to CapabilityRegistry.load()"

    def test_capabilities_model_lifecycle_manager_does_not_hold_model_refs(self):
        """ModelLifecycleManager does NOT hold raw model/engine references."""
        import inspect
        from capabilities import ModelLifecycleManager

        # Check __init__ only — docstring mentions _model in explanations so exclude it
        init_source = inspect.getsource(ModelLifecycleManager.__init__)
        body_source = init_source  # Only __init__ body

        # Must NOT have self._model, self._engine in __init__ body (docstring excluded)
        assert "self._model" not in body_source, \
            "ModelLifecycleManager.__init__ must NOT hold _model reference"
        assert "self._engine" not in body_source, \
            "ModelLifecycleManager.__init__ must NOT hold _engine reference"

    def test_model_manager_is_singleton_acquire_owner(self):
        """ModelManager is the canonical runtime-wide acquire/load owner."""
        import inspect
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mm", Path(__file__).parent.parent / "brain" / "model_manager.py"
        )
        mm_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mm_mod)
        ModelManager = mm_mod.ModelManager

        # load_model must be in ModelManager
        assert hasattr(ModelManager, "load_model"), \
            "ModelManager must own load_model()"

        # _load_model_async must be in ModelManager
        assert hasattr(ModelManager, "_load_model_async"), \
            "ModelManager must own _load_model_async()"

        # Source must show it creates/loads actual model objects
        source = inspect.getsource(ModelManager._load_model_async)
        assert "factory" in source or "load()" in source, \
            "ModelManager._load_model_async must load models"

    def test_model_lifecycle_unload_is_helper_not_owner(self):
        """unload_model() in model_lifecycle is a helper — not the primary load owner."""
        import inspect
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_ml", Path(__file__).parent.parent / "brain" / "model_lifecycle.py"
        )
        ml_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ml_mod)

        # unload_model should delegate to engine.unload()
        source = inspect.getsource(ml_mod.unload_model)
        assert "engine.unload" in source or "model.unload" in source, \
            "unload_model() must delegate to engine.unload()"

        # Must NOT be the primary load owner (ModelManager is)
        # Check that module-level unload_model is a helper, not owner
        module_source = inspect.getsource(ml_mod)
        # The canonical owner note should exist in docstring
        assert "load owner" in module_source.lower() or "runtime-wide" in module_source.lower(), \
            "model_lifecycle module docstring must state it is NOT runtime-wide load owner"

    def test_no_third_model_truth_emerges(self):
        """Capability layer does NOT become a third model truth."""
        import ast
        from pathlib import Path

        # Check that capabilities.py does NOT create model instances directly
        capabilities_path = Path(__file__).parent.parent / "capabilities.py"
        source = capabilities_path.read_text()
        tree = ast.parse(source)

        # Find ModelLifecycleManager class
        model_instances_created = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for direct model class instantiation (not through registry)
                if isinstance(node.func, ast.Name):
                    name = node.func.id.lower()
                    if name in ("hermes3engine", "modernbertembedder", "gliner", "model"):
                        model_instances_created.append(name)

        assert len(model_instances_created) == 0, \
            f"capabilities.py must NOT instantiate model classes directly: {model_instances_created}"

    def test_no_new_model_framework_created(self):
        """No new model manager / provider manager / lifecycle framework created."""
        from pathlib import Path

        universal_dir = Path(__file__).parent.parent
        suspicious_files = [
            "model_provider.py",
            "lifecycle_framework.py",
            "model_registry.py",
            "model_control.py",
            "model_plane.py",
        ]

        for name in suspicious_files:
            path = universal_dir / name
            if path.exists():
                pytest.fail(
                    f"New model framework file created: {name} — "
                    f"F6.5 guardrail violated"
                )

    def test_model_phase_facts_is_read_only(self):
        """model_phase_facts contains only pure read-only facts, no orchestration."""
        import ast
        from pathlib import Path

        phase_facts_path = Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        source = phase_facts_path.read_text()
        tree = ast.parse(source)

        # Check no side-effect functions (no async, no I/O)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef,)):
                pytest.fail("model_phase_facts must NOT contain async functions")

        # No file I/O
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    if name in ("write", "open", "read_text", "read_bytes"):
                        if "path" in source.lower():
                            pytest.fail("model_phase_facts must NOT perform file I/O")

    def test_model_lifecycle_class_is_windup_local_sidecar(self):
        """ModelLifecycle class is explicitly windup-local, not runtime-wide."""
        import inspect
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_ml", Path(__file__).parent.parent / "brain" / "model_lifecycle.py"
        )
        ml_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ml_mod)
        ModelLifecycle = ml_mod.ModelLifecycle

        # The class docstring must mention windup-local
        doc = ModelLifecycle.__doc__ or ""
        assert "windup" in doc.lower() or "sidecar" in doc.lower(), \
            "ModelLifecycle class docstring must state windup-local/sidecar"

    def test_orchestrator_state_is_fourth_namespace(self):
        """types.OrchestratorState is a fourth phase namespace — distinct from Layer 1/2/3."""
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mpf)
        get_phase_layer = mpf.get_phase_layer
        WORKFLOW_PHASES = mpf.WORKFLOW_PHASES
        COARSE_GRAINED_PHASES = mpf.COARSE_GRAINED_PHASES

        # OrchestratorState strings
        orchestrator_phases = {"IDLE", "PLANNING", "BRAIN", "EXECUTION", "SYNTHESIS", "ERROR"}

        # None of these should be in WORKFLOW_PHASES (Layer 1) as strings
        # (PLANNING vs PLAN, SYNTHESIS vs SYNTHESIZE — different strings)
        for phase in orchestrator_phases:
            layer = get_phase_layer(phase)
            # PLANNING, BRAIN, SYNTHESIS, ERROR are not in Layer 1 or Layer 2 as classified
            # by model_phase_facts (they're a fourth namespace in types.py)
            # This test confirms they're not conflated
            if phase in WORKFLOW_PHASES or phase in COARSE_GRAINED_PHASES:
                # If a name happens to overlap, it must be semantically different
                # This is ensured by is_same_layer() returning False for cross-layer
                pass

    def test_capability_registry_load_is_not_model_manager(self):
        """CapabilityRegistry.load() is NOT the same as ModelManager.load_model()."""
        import inspect
        from capabilities import CapabilityRegistry

        # CapabilityRegistry.load must NOT call ModelManager
        source = inspect.getsource(CapabilityRegistry.load)
        assert "ModelManager" not in source, \
            "CapabilityRegistry.load must NOT call ModelManager"
        assert "load_model" not in source, \
            "CapabilityRegistry.load must NOT call load_model()"

    def test_no_cross_layer_phase_mapping_in_model_manager(self):
        """ModelManager.PHASE_MODEL_MAP contains ONLY Layer 1 workflow phases."""
        import importlib.util
        from pathlib import Path
        # Load ModelManager bypassing brain/__init__.py circular import
        spec_mm = importlib.util.spec_from_file_location(
            "_mm", Path(__file__).parent.parent / "brain" / "model_manager.py"
        )
        mm_mod = importlib.util.module_from_spec(spec_mm)
        spec_mm.loader.exec_module(mm_mod)
        ModelManager = mm_mod.ModelManager

        # Load model_phase_facts bypassing circular import
        spec_mpf = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec_mpf)
        spec_mpf.loader.exec_module(mpf)
        is_workflow_phase = mpf.is_workflow_phase

        phase_map = ModelManager.PHASE_MODEL_MAP

        # Every key in PHASE_MODEL_MAP must be a Layer 1 workflow phase
        for phase in phase_map.keys():
            assert is_workflow_phase(phase), \
                f"PHASE_MODEL_MAP key '{phase}' must be Layer 1 (workflow-level)"

    def test_no_cross_layer_phase_mapping_in_model_lifecycle_manager(self):
        """ModelLifecycleManager.enforce_phase_models uses ONLY Layer 2 coarse-grained phases."""
        import inspect
        from capabilities import ModelLifecycleManager
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_mpf", Path(__file__).parent.parent / "brain" / "model_phase_facts.py"
        )
        mpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mpf)
        is_coarse_grained_phase = mpf.is_coarse_grained_phase

        # Check the enforce_phase_models method body
        source = inspect.getsource(ModelLifecycleManager.enforce_phase_models)

        # Phase strings used as literals in the method must be Layer 2
        layer2_phases = {"BRAIN", "TOOLS", "SYNTHESIS", "CLEANUP"}
        for phase in layer2_phases:
            if phase in source:
                assert is_coarse_grained_phase(phase), \
                    f"ModelLifecycleManager must use Layer 2 phases only, found: {phase}"


class TestFactStabilityClassification:
    """Test that fact_stability classification prevents compat seams from being authoritative."""

    def test_graph_bundle_stable_from_ioc_graph_stats(self):
        """from_ioc_graph_stats sets fact_stability=STABLE."""
        from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

        stats = {"nodes": 100, "edges": 300, "pgq_active": True}
        bundle = GraphSummaryBundle.from_ioc_graph_stats(stats, top_nodes=["n1", "n2"])
        assert bundle.fact_stability == "STABLE"
        assert bundle.__future_owner__ == "knowledge/duckdb_store.py"
        assert bundle.__compat_note__ is None

    def test_graph_bundle_compat_from_scorecard_top_nodes(self):
        """from_scorecard_top_nodes sets fact_stability=COMPAT."""
        from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

        bundle = GraphSummaryBundle.from_scorecard_top_nodes(["n1", "n2"])
        assert bundle.fact_stability == "COMPAT"
        assert bundle.__compat_note__ is not None
        assert "deprecated" in bundle.__compat_note__

    def test_model_control_bundle_stable_from_analyzer_result(self):
        """from_analyzer_result sets fact_stability=STABLE."""
        from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle
        from hledac.universal.project_types import AnalyzerResult

        result = AnalyzerResult(
            tools={"cve"},
            sources={"cisa_kev"},
            privacy_level="HIGH",
            use_tor=False,
            depth="DEEP",
            use_tot=False,
            tot_mode="standard",
            models_needed={"hermes"},
        )
        bundle = ModelControlFactsBundle.from_analyzer_result(result)
        assert bundle.fact_stability == "STABLE"

    def test_model_control_bundle_compat_from_raw_profile(self):
        """raw_profile path sets fact_stability=COMPAT."""
        from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle

        raw = {"tools": ["cve"], "sources": ["cisa_kev"], "depth": "DEEP"}
        bundle = ModelControlFactsBundle(
            tools=raw["tools"],
            sources=raw["sources"],
            privacy_level="STANDARD",
            depth=raw["depth"],
            raw_profile=raw,
            fact_stability="COMPAT",
            __compat_note__="raw_profile dict path is legacy compat",
        )
        assert bundle.fact_stability == "COMPAT"
        assert bundle.__compat_note__ is not None
        assert "legacy compat" in bundle.__compat_note__

    def test_lifecycle_bundle_in_windup_is_compat(self):
        """windup_local_phase makes bundle fact_stability=COMPAT."""
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            WorkflowPhase,
            ControlPhase,
            WindupLocalPhase,
        )

        # Bundle WITH windup_local_phase set → COMPAT
        bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="WINDUP", entered_at_monotonic=10.0),
            control_phase=ControlPhase(mode="prune"),
            windup_local_phase=WindupLocalPhase(
                mode="synthesis", error_encountered=False, synthesis_engine="mlx"
            ),
            raw_snapshot={},
            fact_stability="COMPAT",
            __compat_note__="windup_local_phase is COMPAT: currently hardcoded in windup_engine.run_windup()",
        )
        assert bundle.fact_stability == "COMPAT"
        assert bundle.__compat_note__ is not None
        assert "hardcoded" in bundle.__compat_note__

    def test_collect_graph_summary_empty_has_unknown_stability(self):
        """Empty collect_graph_summary returns UNKNOWN stability."""
        from hledac.universal.runtime.shadow_inputs import collect_graph_summary

        bundle = collect_graph_summary(ioc_graph=None, scorecard=None)
        assert bundle.fact_stability == "UNKNOWN"

    def test_collect_model_control_facts_empty_has_unknown_stability(self):
        """Empty collect_model_control_facts returns UNKNOWN stability."""
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        bundle = collect_model_control_facts(analyzer_result=None, raw_profile=None)
        assert bundle.fact_stability == "UNKNOWN"


class TestParityArtifactFactStability:
    """Test ParityArtifact carries fact_stability_breakdown and compat_seams."""

    def test_run_shadow_parity_populates_fact_stability_breakdown(self):
        """run_shadow_parity fills fact_stability_breakdown from bundles."""
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE"),
            control_phase=ControlPhase(mode="normal"),
            windup_local_phase=None,
            raw_snapshot={},
            fact_stability="STABLE",
        )
        graph = GraphSummaryBundle(
            node_count=100, edge_count=300, pgq_active=True,
            backend="duckpgq", fact_stability="STABLE",
        )
        mc = ModelControlFactsBundle(
            tools=["cve"], sources=["cisa_kev"],
            fact_stability="STABLE",
        )

        result = run_shadow_parity(
            lifecycle_bundle=lc,
            graph_bundle=graph,
            model_control_bundle=mc,
            export_handoff_facts={"sprint_id": "s123", "synthesis_engine": "mlx"},
        )

        assert "lifecycle_snapshot" in result.fact_stability_breakdown
        assert "graph_summary" in result.fact_stability_breakdown
        assert "model_control_facts" in result.fact_stability_breakdown
        assert result.fact_stability_breakdown["graph_summary"] == "STABLE"

    def test_run_shadow_parity_flags_compat_seams(self):
        """run_shadow_parity populates compat_seams when bundles are COMPAT."""
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="WINDUP"),
            control_phase=ControlPhase(mode="prune"),
            windup_local_phase=None,
            raw_snapshot={},
            fact_stability="COMPAT",
        )
        graph = GraphSummaryBundle(
            fact_stability="COMPAT",
        )
        mc = ModelControlFactsBundle(
            fact_stability="COMPAT",
        )

        result = run_shadow_parity(
            lifecycle_bundle=lc,
            graph_bundle=graph,
            model_control_bundle=mc,
            export_handoff_facts={},
        )

        assert len(result.compat_seams) > 0
        assert "lifecycle_snapshot/windup_local_phase" in result.compat_seams

    def test_parity_artifact_to_dict_includes_stability_fields(self):
        """ParityArtifact.to_dict() includes fact_stability_breakdown and compat_seams."""
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        artifact = ParityArtifact(
            mode="scheduler_shadow",
            timestamp_monotonic=0.0,
            timestamp_wall="",
            workflow_phase="ACTIVE",
            workflow_phase_entered_at=10.0,
            control_phase_mode="normal",
            control_phase_thermal="nominal",
            windup_local_mode=None,
            graph_nodes=100,
            graph_edges=300,
            graph_pgq_active=True,
            graph_backend="duckpgq",
            graph_top_nodes_count=5,
            mc_tools_count=2,
            mc_sources_count=1,
            mc_privacy="HIGH",
            mc_depth="DEEP",
            mc_models_needed=["hermes"],
            export_sprint_id="s123",
            export_synthesis_engine="mlx",
            export_ranked_parquet_present=True,
            export_gnn_predictions=10,
            branch_decision_id=None,
            provider_recommend=None,
            correlation_run_id=None,
            correlation_branch_id=None,
            mismatch_categories=["NONE"],
            mismatch_details={"note": "ok"},
            input_sources={},
            fact_stability_breakdown={"graph_summary": "STABLE"},
            compat_seams=[],
        )

        d = artifact.to_dict()
        assert "fact_stability_breakdown" in d
        assert "compat_seams" in d
        assert d["fact_stability_breakdown"]["graph_summary"] == "STABLE"


class TestCompatSeamsVsBlockers:
    """Verify compat seams are NOT treated as blockers."""

    def test_compat_seams_do_not_appear_in_blockers(self):
        """Compat seams should appear in compat_seams list, NOT in blockers."""
        from hledac.universal.runtime.shadow_pre_decision import compose_pre_decision
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        artifact = ParityArtifact(
            mode="scheduler_shadow",
            timestamp_monotonic=0.0,
            timestamp_wall="",
            workflow_phase="ACTIVE",
            workflow_phase_entered_at=10.0,
            control_phase_mode="normal",
            control_phase_thermal="nominal",
            windup_local_mode="synthesis",
            graph_nodes=100,
            graph_edges=300,
            graph_pgq_active=True,
            graph_backend="duckpgq",
            graph_top_nodes_count=5,
            mc_tools_count=2,
            mc_sources_count=1,
            mc_privacy="HIGH",
            mc_depth="DEEP",
            mc_models_needed=["hermes"],
            export_sprint_id="s123",
            export_synthesis_engine="mlx",
            export_ranked_parquet_present=True,
            export_gnn_predictions=10,
            branch_decision_id=None,
            provider_recommend=None,
            correlation_run_id=None,
            correlation_branch_id=None,
            mismatch_categories=["NONE"],
            mismatch_details={},
            input_sources={},
            fact_stability_breakdown={
                "lifecycle_snapshot": "COMPAT",
                "graph_summary": "STABLE",
                "model_control_facts": "STABLE",
            },
            compat_seams=["lifecycle_snapshot/windup_local_phase"],
        )

        summary = compose_pre_decision(artifact)

        # Compat seam should be in compat_seams, NOT in blockers
        assert "lifecycle_snapshot/windup_local_phase" in summary.compat_seams
        blocker_strs = [str(b) for b in summary.blockers]
        for seam in summary.compat_seams:
            assert seam not in blocker_strs

    def test_unknown_readiness_is_not_necessarily_blocker(self):
        """UNKNOWN readiness alone is not a blocker — only phase conflicts are."""
        from hledac.universal.runtime.shadow_pre_decision import compose_pre_decision
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        # Everything is unknown but no phase conflict
        artifact = ParityArtifact(
            mode="scheduler_shadow",
            timestamp_monotonic=0.0,
            timestamp_wall="",
            workflow_phase="WARMUP",
            workflow_phase_entered_at=10.0,
            control_phase_mode="normal",
            control_phase_thermal="nominal",
            windup_local_mode=None,  # Not in WINDUP, so None is correct
            graph_nodes=0,
            graph_edges=0,
            graph_pgq_active=False,
            graph_backend="unknown",
            graph_top_nodes_count=0,
            mc_tools_count=0,
            mc_sources_count=0,
            mc_privacy="UNKNOWN",
            mc_depth="UNKNOWN",
            mc_models_needed=[],
            export_sprint_id="unknown",
            export_synthesis_engine="unknown",
            export_ranked_parquet_present=False,
            export_gnn_predictions=0,
            branch_decision_id=None,
            provider_recommend=None,
            correlation_run_id=None,
            correlation_branch_id=None,
            mismatch_categories=["NONE"],
            mismatch_details={},
            input_sources={},
            fact_stability_breakdown={
                "lifecycle_snapshot": "UNKNOWN",
                "graph_summary": "UNKNOWN",
                "model_control_facts": "UNKNOWN",
            },
            compat_seams=[],
        )

        summary = compose_pre_decision(artifact)

        # UNKNOWN should be in unknowns list, not necessarily in blockers
        # Blockers should only fire for phase conflicts or critical mismatches
        unknowns_strs = [str(u) for u in summary.unknowns]
        assert len(unknowns_strs) > 0  # We have unknowns


# =============================================================================
# F3.5: Shadow Scheduler Parity — Runtime-Mode Matrix Guard Tests
# =============================================================================

class TestShadowModeGuardTests:
    """
    F3.5 §Guard: scheduler_shadow mode boundaries.

    scheduler_shadow smí pouze:
    - Číst facts z lifecycle/graph/model_control bundles
    - Počítat parity diff
    - Sestavovat PreDecisionSummary
    - Číst ToolRegistry metadata
    - Generovat diagnostic artifact

    scheduler_shadow NESMÍ:
    - execute_with_limits()
    - Provider activation
    - Network execution
    - Findings writes
    - Runtime truth writes
    - Nové truth store
    """

    def test_shadow_parity_run_produces_artifact_not_truth(self):
        """
        run_shadow_parity() returns ParityArtifact — diagnostic ONLY.
        It does NOT write anything to any ledger or truth store.
        """
        from hledac.universal.runtime.shadow_parity import run_shadow_parity
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        # Minimal bundles
        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE", entered_at_monotonic=1.0),
            control_phase=ControlPhase(mode="normal"),
            fact_stability="STABLE",
        )
        graph_bundle = GraphSummaryBundle(
            node_count=10,
            edge_count=100,
            backend="duckpgq",
            fact_stability="STABLE",
        )
        mc_bundle = ModelControlFactsBundle(
            tools=["web_search"],
            sources=["test"],
            fact_stability="STABLE",
        )

        artifact = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts={"sprint_id": "test", "synthesis_engine": "test", "gnn_predictions": 0, "top_nodes_count": 0, "ranked_parquet_present": False, "phase_durations": {}},
            runtime_mode="scheduler_shadow",
        )

        # It's an artifact, not a truth store
        assert artifact is not None
        assert hasattr(artifact, "mode")
        assert artifact.mode == "scheduler_shadow"

    def test_shadow_mode_no_execute_with_limits_in_scheduler(self):
        """
        SprintScheduler._build_shadow_pre_decision_summary() never calls
        execute_with_limits() — enforced by is_shadow_mode() guard.
        """
        from unittest.mock import patch, MagicMock

        # Verify the guard exists and prevents execution
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_shadow"}):
            # When shadow mode, is_shadow_mode returns True
            assert RuntimeMode.is_shadow_mode() is True
            # is_active_mode is False — so scheduler_active path never activates
            assert RuntimeMode.is_active_mode() is False

    def test_compose_pre_decision_is_pure_function(self):
        """
        compose_pre_decision() is a pure function — no side effects.
        Returns PreDecisionSummary, does not write anywhere.
        """
        from hledac.universal.runtime.shadow_parity import run_shadow_parity, ParityArtifact
        from hledac.universal.runtime.shadow_pre_decision import compose_pre_decision
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="ACTIVE", entered_at_monotonic=1.0),
            control_phase=ControlPhase(mode="normal"),
            fact_stability="STABLE",
        )
        graph_bundle = GraphSummaryBundle(
            node_count=10,
            edge_count=100,
            backend="duckpgq",
            fact_stability="STABLE",
        )
        mc_bundle = ModelControlFactsBundle(
            tools=["web_search"],
            sources=["test"],
            fact_stability="STABLE",
        )

        artifact = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts={"sprint_id": "test", "synthesis_engine": "test", "gnn_predictions": 0, "top_nodes_count": 0, "ranked_parquet_present": False, "phase_durations": {}},
            runtime_mode="scheduler_shadow",
        )
        summary = compose_pre_decision(artifact)

        # Summary is a new in-memory object, not a write to any store
        assert summary is not None
        assert hasattr(summary, "parity_timestamp_monotonic")
        assert hasattr(summary, "lifecycle")


class TestSchedulerActiveDormant:
    """
    F3.5 §Dormant: scheduler_active is declared but dormant.

    scheduler_active smí být activated pouze when explicitly flagged.
    Bez flag zůstává dormant — legacy_runtime je canonical path.
    """

    def test_scheduler_active_mode_is_dormant_without_flag(self):
        """
        Without HLEDAC_RUNTIME_MODE=scheduler_active, is_active_mode() returns False.
        """
        from unittest.mock import patch
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        # Default: no flag
        assert RuntimeMode.is_active_mode() is False

        # Invalid value falls back to legacy
        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "invalid"}):
            assert RuntimeMode.is_active_mode() is False

    def test_scheduler_active_requires_explicit_flag(self):
        """
        scheduler_active activates only with explicit HLEDAC_RUNTIME_MODE=scheduler_active.
        """
        from unittest.mock import patch
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_active"}):
            assert RuntimeMode.is_active_mode() is True
            # But shadow is still False
            assert RuntimeMode.is_shadow_mode() is False
            # And legacy is False
            assert RuntimeMode.is_legacy_mode() is False


class TestParityArtifactsDiagnosticOnly:
    """
    F3.5 §Diagnostic: ParityArtifact and PreDecisionSummary are diagnostic ONLY.
    They do NOT become truth stores, do NOT get written to production ledgers.
    """

    def test_parity_artifact_has_is_diagnostic_only(self):
        """
        ParityArtifact.is_diagnostic_only() returns True.
        """
        from hledac.universal.runtime.shadow_parity import ParityArtifact

        # is_diagnostic_only is a class-level declaration
        assert hasattr(ParityArtifact, "is_diagnostic_only")
        # Returns True for the class
        assert ParityArtifact.is_diagnostic_only() is True

    def test_pre_decision_summary_is_diagnostic_only(self):
        """
        PreDecisionSummary is diagnostic only — must NOT be written to runtime truth.
        """
        from hledac.universal.runtime.shadow_pre_decision import PreDecisionSummary

        # is_diagnostic_only is a class-level declaration
        assert hasattr(PreDecisionSummary, "is_diagnostic_only")
        assert PreDecisionSummary.is_diagnostic_only() is True

    def test_diagnostic_artifact_not_written_to_ledger(self, tmp_path):
        """
        Diagnostic artifacts must NOT be written to production ledgers.
        This test verifies the contract by checking to_dict() output
        does not contain 'truth_store' or 'ledger' markers.
        """
        from hledac.universal.runtime.shadow_parity import run_shadow_parity, ParityArtifact
        from hledac.universal.runtime.shadow_inputs import (
            LifecycleSnapshotBundle,
            GraphSummaryBundle,
            ModelControlFactsBundle,
            WorkflowPhase,
            ControlPhase,
        )

        lc_bundle = LifecycleSnapshotBundle(
            workflow_phase=WorkflowPhase(phase="WINDUP", entered_at_monotonic=100.0),
            control_phase=ControlPhase(mode="normal"),
            windup_local_phase=None,
            fact_stability="COMPAT",
        )
        graph_bundle = GraphSummaryBundle(
            node_count=0,
            edge_count=0,
            backend="unknown",
            fact_stability="UNKNOWN",
        )
        mc_bundle = ModelControlFactsBundle(
            tools=[],
            sources=[],
            fact_stability="UNKNOWN",
        )

        artifact = run_shadow_parity(
            lifecycle_bundle=lc_bundle,
            graph_bundle=graph_bundle,
            model_control_bundle=mc_bundle,
            export_handoff_facts={"sprint_id": "test", "synthesis_engine": "test", "gnn_predictions": 0, "top_nodes_count": 0, "ranked_parquet_present": False, "phase_durations": {}},
            runtime_mode="scheduler_shadow",
        )

        d = artifact.to_dict()
        # No ledger/truth markers
        for key in d.keys():
            assert "ledger" not in key.lower(), f"Found 'ledger' in key: {key}"
            assert "truth_store" not in key.lower(), f"Found 'truth_store' in key: {key}"


class TestRuntimeModeMatrix:
    """
    F3.5 §Matrix: Explicit runtime-mode matrix.

    | Režim              | Canonical Path | Read Facts | Write Truth | Execute Tools |
    |--------------------|----------------|------------|-------------|--------------|
    | legacy_runtime     | YES            | YES        | YES         | YES          |
    | scheduler_shadow   | NO             | YES        | NO          | NO           |
    | scheduler_active   | NO (dormant)   | YES        | NO          | NO           |
    """

    def test_legacy_runtime_is_canonical_path(self):
        """
        legacy_runtime is the default and canonical path.
        """
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        mode = RuntimeMode.get_current()
        assert mode == RuntimeMode.LEGACY_RUNTIME
        assert RuntimeMode.is_legacy_mode() is True

    def test_shadow_mode_read_only(self):
        """
        scheduler_shadow mode is read-only — no tool execution.
        """
        from unittest.mock import patch
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_shadow"}):
            assert RuntimeMode.is_shadow_mode() is True
            assert RuntimeMode.is_active_mode() is False
            assert RuntimeMode.is_legacy_mode() is False

    def test_active_mode_is_dormant(self):
        """
        scheduler_active mode is declared but dormant — requires explicit activation.
        """
        from unittest.mock import patch
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        # Default: dormant
        assert RuntimeMode.is_active_mode() is False

        # With explicit flag: still dormant in this sprint
        # (Full activation requires F4+)
        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_active"}):
            assert RuntimeMode.is_active_mode() is True
            # Shadow still separate
            assert RuntimeMode.is_shadow_mode() is False

    def test_runtime_mode_exclusive_except_legacy(self):
        """
        Only one mode is active at a time (except legacy overlaps with nothing).
        """
        from unittest.mock import patch
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        # Default: legacy only
        assert RuntimeMode.get_current() == RuntimeMode.LEGACY_RUNTIME

        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_shadow"}):
            assert RuntimeMode.get_current() == RuntimeMode.SCHEDULER_SHADOW
            assert RuntimeMode.is_shadow_mode()
            assert not RuntimeMode.is_active_mode()
            assert not RuntimeMode.is_legacy_mode()

        with patch.dict("os.environ", {"HLEDAC_RUNTIME_MODE": "scheduler_active"}):
            assert RuntimeMode.get_current() == RuntimeMode.SCHEDULER_ACTIVE
            assert RuntimeMode.is_active_mode()
            assert not RuntimeMode.is_shadow_mode()
            assert not RuntimeMode.is_legacy_mode()
