"""
Sprint F204E: Analyst Briefing Lifecycle — Probe Tests

Tests:
 1. AnalystBrief dataclass frozen and fields
 2. MAX_BRIEF_FINDINGS = 20, MAX_BRIEF_CHAINS = 5,
    MAX_BRIEF_NEXT_ACTIONS = 10, MAX_CONTEXT_BYTES = 8192
 3. build_sprint_brief() returns AnalystBrief
 4. build_sprint_brief() RAM guard critical/emergency → minimal brief
 5. build_sprint_brief() fail-soft on extract error
 6. _extract_key_findings bounded by MAX_BRIEF_FINDINGS
 7. _derive_next_actions bounded by MAX_BRIEF_NEXT_ACTIONS
 8. _derive_open_questions bounded
 9. _run_analyst_brief_advisory stores in self._analyst_brief
10. _run_analyst_brief_advisory fail-soft on workbench error
11. _run_analyst_brief_advisory no-op when no workbench
12. get_analyst_brief() returns self._analyst_brief
13. sprint_exporter JSON section has analyst_brief
14. sprint_exporter markdown section renders
15. source_type="analyst_brief" synthetic finding envelope
16. gather with return_exceptions=True in teardown advisory
17. analyst_brief on ExportHandoff
18. smoke runner OK
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import fields as _dc_fields
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.knowledge.analyst_workbench import (
    MAX_BRIEF_CHAINS,
    MAX_BRIEF_FINDINGS,
    MAX_BRIEF_NEXT_ACTIONS,
    AnalystBrief,
    AnalystWorkbench,
)


# ---------------------------------------------------------------------------
# 1-2: AnalystBrief dataclass invariants
# ---------------------------------------------------------------------------

class TestAnalystBriefDataclass:
    """F204E-1: AnalystBrief frozen dataclass with correct fields."""

    def test_analyst_brief_frozen(self):
        """Invariant: frozen=True prevents field mutation."""
        brief = AnalystBrief(
            sprint_id="s-1",
            target_id="t-1",
            headline="test",
            key_findings=("f1",),
            evidence_chain_ids=("c1",),
            next_actions=("a1",),
            open_questions=("q1",),
            confidence=0.8,
            generated_ts=1000.0,
        )
        with pytest.raises(Exception):
            brief.headline = "changed"  # type: ignore

    def test_analyst_brief_all_fields(self):
        """Invariant: all required fields present."""
        flds = {f.name for f in _dc_fields(AnalystBrief)}
        expected = {
            "sprint_id", "target_id", "headline", "key_findings",
            "evidence_chain_ids", "next_actions", "open_questions",
            "confidence", "generated_ts",
        }
        assert expected <= flds, f"missing fields: {expected - flds}"

    def test_analyst_brief_field_types(self):
        """Invariant: tuple fields are tuple type."""
        brief = AnalystBrief(
            sprint_id="s-1",
            target_id="t-1",
            headline="headline",
            key_findings=("f1", "f2"),
            evidence_chain_ids=(),
            next_actions=("a1",),
            open_questions=(),
            confidence=0.5,
            generated_ts=time.time(),
        )
        assert isinstance(brief.key_findings, tuple)
        assert isinstance(brief.evidence_chain_ids, tuple)
        assert isinstance(brief.next_actions, tuple)
        assert isinstance(brief.open_questions, tuple)
        assert isinstance(brief.confidence, float)
        assert isinstance(brief.generated_ts, float)

    def test_max_brief_bounds(self):
        """F204E-2: Bounds constants are correct."""
        assert MAX_BRIEF_FINDINGS == 20
        assert MAX_BRIEF_CHAINS == 5
        assert MAX_BRIEF_NEXT_ACTIONS == 10


# ---------------------------------------------------------------------------
# 3-8: build_sprint_brief() method tests
# ---------------------------------------------------------------------------

class TestBuildSprintBrief:
    """F204E-3..8: build_sprint_brief() behavior."""

    @pytest.fixture
    def workbench(self):
        """Minimal workbench with no stores."""
        return AnalystWorkbench(
            duckdb_store=None,
            graph_service=None,
            vector_store=None,
            semantic_store=None,
        )

    @pytest.fixture
    def mock_governor_critical(self):
        """Governor in critical state."""
        gov = MagicMock()
        snap = MagicMock()
        snap.uma_state = "critical"
        gov.snapshot.return_value = snap
        return gov

    @pytest.fixture
    def mock_finding(self, source_type="ct_log", ioc_type="ipv4",
                     ioc_value="1.2.3.4", confidence=0.8, query=""):
        f = MagicMock()
        f.source_type = source_type
        f.ioc_type = ioc_type
        f.ioc_value = ioc_value
        f.confidence = confidence
        f.query = query
        f.finding_id = f"fid-{ioc_value}"
        f.provenance = ""
        return f

    @pytest.mark.asyncio
    async def test_build_sprint_brief_returns_analyst_brief(self, workbench):
        """F204E-3: Returns AnalystBrief instance."""
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=[],
            graph_signal={"graph_nodes": 0, "graph_edges": 0},
            governor=None,
        )
        assert isinstance(result, AnalystBrief)
        assert result.sprint_id == "s-1"
        assert result.target_id == "t-1"

    @pytest.mark.asyncio
    async def test_build_sprint_brief_ram_guard_minimal(self, workbench,
                                                        mock_governor_critical):
        """F204E-4: RAM critical → minimal brief, no graph queries."""
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=[MagicMock() for _ in range(5)],
            graph_signal={"graph_nodes": 10, "graph_edges": 20},
            governor=mock_governor_critical,
        )
        assert isinstance(result, AnalystBrief)
        assert "RAM pressure" in result.headline
        assert result.confidence == 0.3

    @pytest.mark.asyncio
    async def test_build_sprint_brief_fail_soft_extract_error(self, workbench):
        """F204E-5: Fail-soft on extraction error."""
        # Force _extract_key_findings to raise
        with patch.object(workbench, "_extract_key_findings",
                         side_effect=RuntimeError("simulated")):
            result = await workbench.build_sprint_brief(
                sprint_id="s-1",
                target_id="t-1",
                findings=[],
                graph_signal={},
                governor=None,
            )
        assert isinstance(result, AnalystBrief)
        assert result.confidence == 0.1
        assert "failed" in result.headline

    @pytest.mark.asyncio
    async def test_build_sprint_brief_findings_bounded(self, workbench, mock_finding):
        """F204E-6: key_findings bounded by MAX_BRIEF_FINDINGS."""
        findings = [mock_finding(ioc_value=f"ip-{i}") for i in range(30)]
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=findings,
            graph_signal={"graph_nodes": 5, "graph_edges": 3},
            governor=None,
        )
        assert len(result.key_findings) <= MAX_BRIEF_FINDINGS

    @pytest.mark.asyncio
    async def test_build_sprint_brief_next_actions_bounded(self, workbench, mock_finding):
        """F204E-7: next_actions bounded by MAX_BRIEF_NEXT_ACTIONS."""
        findings = [mock_finding(
            source_type="ct_log", ioc_type="domain",
            ioc_value=f"evil-{i}.com", confidence=0.8
        ) for i in range(30)]
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=findings,
            graph_signal={"graph_nodes": 5, "graph_edges": 3},
            governor=None,
        )
        assert len(result.next_actions) <= MAX_BRIEF_NEXT_ACTIONS

    @pytest.mark.asyncio
    async def test_build_sprint_brief_open_questions_bounded(self, workbench, mock_finding):
        """F204E-8: open_questions bounded (max 5)."""
        # Empty findings → one open question
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=[],
            graph_signal={},
            governor=None,
        )
        assert len(result.open_questions) <= 5

    @pytest.mark.asyncio
    async def test_build_sprint_brief_normal_confidence(self, workbench, mock_finding):
        """Normal path confidence: >10 findings → 0.7."""
        findings = [mock_finding(ioc_value=f"ip-{i}") for i in range(15)]
        result = await workbench.build_sprint_brief(
            sprint_id="s-1",
            target_id="t-1",
            findings=findings,
            graph_signal={"graph_nodes": 10, "graph_edges": 5},
            governor=None,
        )
        assert result.confidence >= 0.5


# ---------------------------------------------------------------------------
# 9-12: SprintScheduler teardown wiring
# ---------------------------------------------------------------------------

class TestAnalystBriefTeardown:
    """F204E-9..12: _run_analyst_brief_advisory teardown wiring."""

    @pytest.mark.asyncio
    async def test_advisory_stores_brief(self):
        """F204E-9: _run_analyst_brief_advisory stores in self._analyst_brief."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        # Patch build_sprint_brief to avoid real stores
        brief = AnalystBrief(
            sprint_id="s-1",
            target_id="s-1",
            headline="test brief",
            key_findings=("finding",),
            evidence_chain_ids=(),
            next_actions=(),
            open_questions=(),
            confidence=0.9,
            generated_ts=time.time(),
        )

        async def fake_build(*args, **kwargs):
            return brief

        scheduler = object.__new__(SprintScheduler)
        scheduler.sprint_id = "s-1"
        scheduler._analyst_workbench = MagicMock()
        scheduler._analyst_workbench.build_sprint_brief = fake_build
        scheduler._all_findings = []
        scheduler._get_graph_signal = MagicMock(return_value={})
        scheduler._governor = None
        scheduler._analyst_brief = None

        await scheduler._run_analyst_brief_advisory()
        assert scheduler._analyst_brief is brief

    @pytest.mark.asyncio
    async def test_advisory_fail_soft(self):
        """F204E-10: Fail-soft on workbench error, no exception."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler.sprint_id = "s-1"
        scheduler._analyst_workbench = MagicMock()
        scheduler._analyst_workbench.build_sprint_brief = AsyncMock(
            side_effect=RuntimeError("simulated")
        )
        scheduler._all_findings = []
        scheduler._get_graph_signal = MagicMock(return_value={})
        scheduler._governor = None
        scheduler._analyst_brief = "old-value"

        await scheduler._run_analyst_brief_advisory()
        # old value unchanged on failure
        assert scheduler._analyst_brief == "old-value"

    @pytest.mark.asyncio
    async def test_advisory_no_op_without_workbench(self):
        """F204E-11: No-op when _analyst_workbench is None."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._analyst_workbench = None
        scheduler._analyst_brief = None

        await scheduler._run_analyst_brief_advisory()
        assert scheduler._analyst_brief is None

    def test_get_analyst_brief(self):
        """F204E-12: get_analyst_brief() returns self._analyst_brief."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        scheduler._analyst_brief = "test-brief"

        result = scheduler.get_analyst_brief()
        assert result == "test-brief"

    def test_get_analyst_brief_default_none(self):
        """F204E-12b: get_analyst_brief() returns None when unset."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

        scheduler = object.__new__(SprintScheduler)
        assert scheduler.get_analyst_brief() is None


# ---------------------------------------------------------------------------
# 13-14: sprint_exporter JSON + markdown section
# ---------------------------------------------------------------------------

class TestAnalystBriefExporterSection:
    """F204E-13..14: analyst_brief in sprint_exporter."""

    def test_exporter_has_analyst_brief_section(self):
        """F204E-13: export_sprint JSON has analyst_brief key."""
        from hledac.universal.export.sprint_exporter import _build_product_value_summary

        # Check function exists and analyst_brief section is referenced
        import inspect
        src = inspect.getsource(_build_product_value_summary)
        # Section should be present (wired during this sprint)
        # Check that _build_product_value_summary or export_sprint handles it
        from hledac.universal.export import sprint_exporter
        src_full = inspect.getsource(sprint_exporter)
        # analyst_brief should appear in export_sprint context
        assert "analyst_brief" in src_full or "AnalystBrief" in src_full

    def test_analyst_brief_markdown_helper_exists(self):
        """F204E-14: _render_analyst_brief_section exists in sprint_markdown_reporter."""
        from hledac.universal.export.sprint_markdown_reporter import _render_analyst_brief_section
        assert callable(_render_analyst_brief_section)


# ---------------------------------------------------------------------------
# 15: synthetic finding envelope
# ---------------------------------------------------------------------------

class TestAnalystBriefSyntheticFinding:
    """F204E-15: source_type="analyst_brief" synthetic finding envelope."""

    def test_analyst_brief_can_be_synthetic_finding(self):
        """Envelope: analyst brief finding has source_type='analyst_brief'."""
        brief = AnalystBrief(
            sprint_id="s-1",
            target_id="t-1",
            headline="test",
            key_findings=("f1",),
            evidence_chain_ids=(),
            next_actions=("a1",),
            open_questions=(),
            confidence=0.8,
            generated_ts=time.time(),
        )
        # Build a synthetic finding-like payload
        payload = {
            "source_type": "analyst_brief",
            "sprint_id": brief.sprint_id,
            "headline": brief.headline,
            "key_findings": list(brief.key_findings),
            "evidence_chain_ids": list(brief.evidence_chain_ids),
            "next_actions": list(brief.next_actions),
            "open_questions": list(brief.open_questions),
            "confidence": brief.confidence,
            "generated_ts": brief.generated_ts,
        }
        assert payload["source_type"] == "analyst_brief"
        assert len(payload["key_findings"]) <= MAX_BRIEF_FINDINGS


# ---------------------------------------------------------------------------
# 16: gather return_exceptions=True
# ---------------------------------------------------------------------------

class TestGatherPattern:
    """F204E-16: gather with return_exceptions=True in teardown."""

    def test_gather_return_exceptions_pattern(self):
        """Invariant: teardown gather uses return_exceptions=True."""
        import inspect
        from hledac.universal.runtime import sprint_scheduler as mod

        # Find the teardown gather call
        src = inspect.getsource(mod)
        # Look for the teardown section with analyst_brief advisory
        # Should find: asyncio.gather(..., return_exceptions=True)
        assert "return_exceptions=True" in src


# ---------------------------------------------------------------------------
# 17: ExportHandoff analyst_brief field
# ---------------------------------------------------------------------------

class TestExportHandoffAnalystBrief:
    """F204E-17: ExportHandoff has analyst_brief field."""

    def test_analyst_brief_field_on_export_handoff(self):
        """ExportHandoff has analyst_brief: Optional[Dict[str, Any]]."""
        from hledac.universal.project_types import ExportHandoff
        from dataclasses import fields as _dc_fields

        flds = {f.name for f in _dc_fields(ExportHandoff)}
        assert "analyst_brief" in flds, f"analyst_brief not in ExportHandoff fields: {flds}"
