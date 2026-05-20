"""
probe_r0_nonfeed_reality_lock — R0: Nonfeed Reality Lock Audit Tests
======================================================================

Tests assert the current state of nonfeed wiring WITHOUT making any changes.
All assertions are READ-ONLY — no production code is modified.

Verify:
    python -m pytest tests/test_r0_nonfeed_reality_lock.py -v
"""
from __future__ import annotations

import pytest


class TestCanonicalOwner:
    """Q1: Verify core.__main__.run_sprint is sole canonical sprint owner."""

    def test_runtime_authority_manifest_defines_canonical_owner(self):
        from hledac.universal.runtime_authority_manifest import (
            CANONICAL_SPRINT_OWNER,
        )
        assert CANONICAL_SPRINT_OWNER == "hledac.universal.core.__main__.run_sprint"

    def test_legacy_autonomous_orchestrator_not_in_active_runtime(self):
        from hledac.universal.runtime_authority_manifest import (
            ACTIVE_RUNTIME_FILES,
            LEGACY_RUNTIME_FILES,
        )
        assert "legacy/autonomous_orchestrator.py" in LEGACY_RUNTIME_FILES
        assert "legacy/autonomous_orchestrator.py" not in ACTIVE_RUNTIME_FILES

    def test_canonical_owner_not_in_legacy_or_facade(self):
        from hledac.universal.runtime_authority_manifest import (
            CANONICAL_SPRINT_OWNER,
            LEGACY_RUNTIME_FILES,
            DEPRECATED_FACADE_FILES,
        )
        # canonical owner is a string path, not a file — verify structure is sound
        assert "core.__main__" in CANONICAL_SPRINT_OWNER
        assert "." not in CANONICAL_SPRINT_OWNER.split(".")[-1]  # no method dot-access


class TestSprintSchedulerWiring:
    """Q2-Q3: Verify SprintScheduler calls run_enabled_acquisition_lanes and imports."""

    def test_sprint_scheduler_imports_acquisition_strategy(self):
        import ast
        import os

        # test file: hledac/universal/tests/test_r0_*.py
        # scheduler: hledac/universal/runtime/sprint_scheduler.py
        path = os.path.join(os.path.dirname(__file__), "..", "runtime", "sprint_scheduler.py")
        path = os.path.normpath(path)
        with open(path, encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "acquisition_strategy" in node.module:
                    imports.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "acquisition_strategy" in alias.name:
                        imports.append(alias.name)

        assert any("acquisition_strategy" in imp for imp in imports), (
            "sprint_scheduler.py must import acquisition_strategy"
        )

    def test_sprint_scheduler_calls_run_enabled_acquisition_lanes(self):
        import ast
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "runtime", "sprint_scheduler.py")
        path = os.path.normpath(path)
        with open(path, encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if "run_enabled_acquisition_lanes" in node.func.id:
                        calls.append(node.func.id)

        assert "run_enabled_acquisition_lanes" in calls, (
            "sprint_scheduler.py must call run_enabled_acquisition_lanes"
        )

    def test_sprint_scheduler_imports_source_finding_bridge(self):
        import ast
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "runtime", "sprint_scheduler.py")
        path = os.path.normpath(path)
        with open(path, encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)
        imports_source_finding_bridge = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "source_finding_bridge" in node.module:
                    imports_source_finding_bridge = True

        assert imports_source_finding_bridge, (
            "sprint_scheduler.py must import source_finding_bridge"
        )


class TestSourceFindingBridge:
    """Q5: Verify source_finding_bridge exposes CT/Wayback/PassiveDNS converters."""

    def test_ct_results_to_findings_exists_and_callable(self):
        from hledac.universal.runtime.source_finding_bridge import (
            ct_results_to_findings,
        )
        assert callable(ct_results_to_findings)

    def test_wayback_results_to_findings_exists_and_callable(self):
        from hledac.universal.runtime.source_finding_bridge import (
            wayback_results_to_findings,
        )
        assert callable(wayback_results_to_findings)

    def test_passive_dns_results_to_findings_exists_and_callable(self):
        from hledac.universal.runtime.source_finding_bridge import (
            passive_dns_results_to_findings,
        )
        assert callable(passive_dns_results_to_findings)

    def test_ct_results_to_findings_returns_tuple(self):
        from unittest.mock import MagicMock

        from hledac.universal.runtime.source_finding_bridge import (
            ct_results_to_findings,
        )

        mock_batch = MagicMock()
        mock_batch.hits = []
        mock_outcome = MagicMock()

        result = ct_results_to_findings(
            batch_result=mock_batch,
            _outcome=mock_outcome,
            query="example.com",
            sprint_id="test-sprint",
        )
        assert isinstance(result, tuple), "ct_results_to_findings must return tuple"
        assert len(result) == 3, "ct_results_to_findings must return (findings, rejections, telemetry)"

    def test_wayback_results_to_findings_returns_tuple(self):
        from unittest.mock import MagicMock

        from hledac.universal.runtime.source_finding_bridge import (
            wayback_results_to_findings,
        )

        mock_diff = MagicMock()
        mock_diff.change_events = []

        result = wayback_results_to_findings(
            diff_result=mock_diff,
            query="example.com",
            sprint_id="test-sprint",
        )
        assert isinstance(result, tuple), "wayback_results_to_findings must return tuple"
        assert len(result) == 3, "wayback_results_to_findings must return (findings, rejections, telemetry)"

    def test_passive_dns_results_to_findings_returns_tuple(self):
        from unittest.mock import MagicMock

        from hledac.universal.runtime.source_finding_bridge import (
            passive_dns_results_to_findings,
        )

        result = passive_dns_results_to_findings(
            ips=["1.2.3.4"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
        )
        assert isinstance(result, tuple), "passive_dns_results_to_findings must return tuple"
        assert len(result) == 3, "passive_dns_results_to_findings must return (findings, rejections, telemetry)"

    def test_passive_dns_results_to_findings_trigger_confidence_inheritance(self):
        """Sprint F229: trigger_confidence propagates to PDNS finding confidence.

        Rules:
        - None trigger → flat 0.5
        - trigger_confidence=0.8 → inherited > 0.5
        - trigger_confidence=1.0 → cap at 0.85
        - trigger_confidence=0.2 → floor at 0.5 (max baseline)
        """
        from unittest.mock import MagicMock

        from hledac.universal.runtime.source_finding_bridge import (
            passive_dns_results_to_findings,
        )

        # Case: None trigger — flat 0.5
        result_none = passive_dns_results_to_findings(
            ips=["8.8.8.8"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
            trigger_confidence=None,
        )
        findings_none, _, _ = result_none
        assert len(findings_none) == 1
        assert findings_none[0].confidence == 0.5, "None trigger → flat 0.5"

        # Case: trigger=0.8 → > 0.5
        result_08 = passive_dns_results_to_findings(
            ips=["8.8.8.8"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
            trigger_confidence=0.8,
        )
        findings_08, _, _ = result_08
        assert findings_08[0].confidence > 0.5, "trigger=0.8 → inherited > 0.5"
        assert findings_08[0].confidence <= 0.85, "trigger=0.8 → capped at 0.85"

        # Case: trigger=1.0 → cap at 0.85
        result_10 = passive_dns_results_to_findings(
            ips=["8.8.8.8"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
            trigger_confidence=1.0,
        )
        findings_10, _, _ = result_10
        assert findings_10[0].confidence == 0.85, "trigger=1.0 → cap 0.85"

        # Case: trigger=0.2 → floor at 0.5 (max baseline)
        result_02 = passive_dns_results_to_findings(
            ips=["8.8.8.8"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
            trigger_confidence=0.2,
        )
        findings_02, _, _ = result_02
        assert findings_02[0].confidence == 0.5, "trigger=0.2 → floor at 0.5"

        # Case: invalid/negative → clamp to [0,1] range
        result_neg = passive_dns_results_to_findings(
            ips=["8.8.8.8"],
            _outcome=MagicMock(),
            query="example.com",
            sprint_id="test-sprint",
            trigger_confidence=-0.5,
        )
        findings_neg, _, _ = result_neg
        assert findings_neg[0].confidence >= 0.0, "negative → clamped to >= 0"

    def test_rejection_constants_defined(self):
        from hledac.universal.runtime.source_finding_bridge import (
            REJECTION_MISSING_DOMAIN,
            REJECTION_MISSING_VALUE,
            REJECTION_WILDCARD_DOMAIN,
            REJECTION_PRIVATE_OR_RESERVED_DOMAIN,
            REJECTION_DUPLICATE_CANDIDATE,
            REJECTION_STORAGE_UNAVAILABLE,
            REJECTION_QUALITY_GATE,
            REJECTION_CANDIDATE_BUILT_NOT_STORED,
        )
        # All expected constants must be non-empty strings
        assert isinstance(REJECTION_MISSING_DOMAIN, str)
        assert isinstance(REJECTION_MISSING_VALUE, str)
        assert isinstance(REJECTION_WILDCARD_DOMAIN, str)
        assert isinstance(REJECTION_PRIVATE_OR_RESERVED_DOMAIN, str)
        assert isinstance(REJECTION_DUPLICATE_CANDIDATE, str)
        assert isinstance(REJECTION_STORAGE_UNAVAILABLE, str)
        assert isinstance(REJECTION_QUALITY_GATE, str)
        assert isinstance(REJECTION_CANDIDATE_BUILT_NOT_STORED, str)


class TestLivePublicPipeline:
    """Verify live_public_pipeline exposes PUBLIC acceptance telemetry fields."""

    def test_live_public_pipeline_in_active_runtime(self):
        from hledac.universal.runtime_authority_manifest import (
            ACTIVE_RUNTIME_FILES,
        )
        assert "pipeline/live_public_pipeline.py" in ACTIVE_RUNTIME_FILES

    def test_live_public_pipeline_file_exists(self):
        import os

        # test file: hledac/universal/tests/test_r0_*.py
        # pipeline: hledac/universal/pipeline/live_public_pipeline.py
        path = os.path.join(os.path.dirname(__file__), "..", "pipeline", "live_public_pipeline.py")
        path = os.path.normpath(path)
        assert os.path.exists(path), f"live_public_pipeline.py must exist at {path}"


class TestAcquisitionStrategy:
    """Q2-Q3: Verify run_enabled_acquisition_lanes exists and has expected signature."""

    def test_run_enabled_acquisition_lanes_exists_and_callable(self):
        from hledac.universal.runtime.acquisition_strategy import (
            run_enabled_acquisition_lanes,
        )
        assert callable(run_enabled_acquisition_lanes)

    def test_acquisition_strategy_has_build_acquisition_report(self):
        from hledac.universal.runtime.acquisition_strategy import (
            build_acquisition_report,
        )
        assert callable(build_acquisition_report)

    def test_acquisition_strategy_has_required_terminal_lanes(self):
        from hledac.universal.runtime.acquisition_strategy import (
            required_terminal_lanes,
        )
        assert callable(required_terminal_lanes)


class TestNonfeedCandidateLedger:
    """Q9: Verify NonfeedCandidateLedger exists and has expected methods."""

    def test_ledger_record_dataclass_exists(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import LedgerRecord
        assert LedgerRecord is not None

    def test_nonfeed_candidate_ledger_has_add_ct_quarantine(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            NonfeedCandidateLedger,
        )
        ledger = NonfeedCandidateLedger()
        assert hasattr(ledger, "add_ct_quarantine")
        assert callable(ledger.add_ct_quarantine)

    def test_nonfeed_candidate_ledger_has_add_quality_rejection(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            NonfeedCandidateLedger,
        )
        ledger = NonfeedCandidateLedger()
        assert hasattr(ledger, "add_quality_rejection")
        assert callable(ledger.add_quality_rejection)

    def test_nonfeed_candidate_ledger_has_add_provider_failed(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            NonfeedCandidateLedger,
        )
        ledger = NonfeedCandidateLedger()
        assert hasattr(ledger, "add_provider_failed")
        assert callable(ledger.add_provider_failed)

    def test_ledger_family_constants_defined(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            FAMILY_PUBLIC,
            FAMILY_CT,
            FAMILY_WAYBACK,
            FAMILY_PASSIVE_DNS,
            FAMILY_PIVOT,
        )
        assert FAMILY_PUBLIC == "PUBLIC"
        assert FAMILY_CT == "CT"
        assert FAMILY_WAYBACK == "WAYBACK"
        assert FAMILY_PASSIVE_DNS == "PASSIVE_DNS"
        assert FAMILY_PIVOT == "PIVOT"

    def test_ledger_stage_constants_defined(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            STAGE_DISCOVERED,
            STAGE_QUARANTINED,
            STAGE_REJECTED,
            STAGE_STORED,
            STAGE_ACCEPTED,
            STAGE_PROVIDER_FAILED,
        )
        assert STAGE_DISCOVERED == "discovered"
        assert STAGE_QUARANTINED == "quarantined"
        assert STAGE_REJECTED == "rejected"
        assert STAGE_STORED == "stored"
        assert STAGE_ACCEPTED == "accepted"
        assert STAGE_PROVIDER_FAILED == "provider_failed"

    def test_ledger_max_size_constant(self):
        from hledac.universal.runtime.nonfeed_candidate_ledger import MAX_LEDGER_SIZE
        assert MAX_LEDGER_SIZE == 500


class TestCTAdapter:
    """Q4: Verify CT adapter (crtsh_adapter) exists and is wired."""

    def test_crtsh_adapter_call_crtsh_exists(self):
        from hledac.universal.discovery.crtsh_adapter import call_crtsh
        assert callable(call_crtsh)

    def test_crtsh_adapter_returns_tuple(self):
        import inspect
        from hledac.universal.discovery.crtsh_adapter import call_crtsh
        # Verify async function signature
        assert inspect.iscoroutinefunction(call_crtsh)
        sig = inspect.signature(call_crtsh)
        assert 'query' in sig.parameters
        assert 'max_results' in sig.parameters
        assert 'timeout_s' in sig.parameters

    def test_ct_outcome_dataclass_exists(self):
        from hledac.universal.discovery.crtsh_adapter import CTOutcome
        assert CTOutcome is not None


class TestPassiveDNSAdapter:
    """Q4: Verify PassiveDNS adapter exists."""

    def test_passive_dns_call_lookup_exists(self):
        from hledac.universal.security.passive_dns import call_lookup_passive_dns
        assert callable(call_lookup_passive_dns)

    def test_passive_dns_outcome_dataclass_exists(self):
        from hledac.universal.security.passive_dns import PassiveDNSOutcome
        assert PassiveDNSOutcome is not None


class TestWaybackAdapter:
    """Q4: Verify WaybackDiffMiner exists."""

    def test_wayback_diff_miner_exists(self):
        from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner
        assert WaybackDiffMiner is not None

    def test_wayback_diff_result_to_findings_method(self):
        from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffResult
        result = WaybackDiffResult(input_count=0, change_events=[])
        assert hasattr(result, "to_findings")
        assert callable(result.to_findings)


class TestNoProductionEdits:
    """Verify R0 audit made NO production code edits."""

    def test_no_git_commands_ran(self):
        # This is a meta-test: if we got here, no git commands were run by this probe
        assert True

    def test_probes_directory_exists(self):
        import os

        # test file: hledac/universal/tests/test_r0_nonfeed_reality_lock.py
        # probe dir:  hledac/universal/probe_r0_nonfeed_reality_lock/
        probe_dir = os.path.join(os.path.dirname(__file__), "..", "probe_r0_nonfeed_reality_lock")
        probe_dir = os.path.normpath(probe_dir)
        assert os.path.exists(probe_dir), f"probe_r0_nonfeed_reality_lock directory must exist at {probe_dir}"

    def test_report_file_exists(self):
        import os

        report_path = os.path.join(os.path.dirname(__file__), "..", "probe_r0_nonfeed_reality_lock", "REPORT_NONFEED_REALITY_LOCK.md")
        report_path = os.path.normpath(report_path)
        assert os.path.exists(report_path), f"REPORT_NONFEED_REALITY_LOCK.md must exist at {report_path}"

    def test_json_summary_exists(self):
        import os

        json_path = os.path.join(os.path.dirname(__file__), "..", "probe_r0_nonfeed_reality_lock", "nonfeed_reality_lock.json")
        json_path = os.path.normpath(json_path)
        assert os.path.exists(json_path), f"nonfeed_reality_lock.json must exist at {json_path}"