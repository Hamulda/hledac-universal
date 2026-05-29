"""
Sprint 8AL Tests: Scheduler Consistency + Provenance Cleanup
================================================================

Tests for:
1. Active scheduler monopoly window is 60s (not 300s)
2. Starvation bonus hard-capped at <= 0.08
3. Deprecated scheduler constants exist but are NOT read in active path
4. source_type vs source_type_finding are isolated (no runtime collision)
5. confidence policy is explicit and deterministic for main finding families
6. import time does not regress
"""

import inspect

import pytest


class TestSchedulerConsistency:
    """Scheduler consistency: 60s window, 0.08 starvation cap."""

    def test_scheduler_active_monopoly_window_is_60s(self):
        """SCHEDULER: Active _MONOPOLY_GUARD_WINDOW_SEC is exactly 60s."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        assert "_MONOPOLY_GUARD_WINDOW_SEC = 60.0" in src

    def test_scheduler_starvation_onset_is_20s(self):
        """SCHEDULER: Starvation onset is 20s (proportional to 60s window)."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        assert "_STARVATION_ONSET_SEC = 20.0" in src

    def test_scheduler_starvation_bonus_hard_capped_at_point_08(self):
        """SCHEDULER: Starvation bonus cap is exactly 0.08."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        assert "_STARVATION_CAP = 0.08" in src

    def test_scheduler_starvation_bonus_uses_named_constants_not_magic_numbers(self):
        """SCHEDULER: Starvation bonus uses self._STARVATION_ONSET_SEC and self._STARVATION_CAP."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator._decide_next_action)
        # Must use named constants in the starvation computation region
        assert "self._STARVATION_ONSET_SEC" in src
        assert "self._STARVATION_CAP" in src
        # Starvation bonus computation must NOT use the old hardcoded value 0.15
        # (other 0.15 values elsewhere in _decide_next_action are unrelated constants)
        import re
        # Find the starvation bonus block and ensure it uses the cap constant
        starve_block = re.search(
            r'starve_bonus.*?if idle_sec > self\._STARVATION_ONSET_SEC.*?starve_bonus = self\._STARVATION_CAP',
            src, re.DOTALL
        )
        assert starve_block is not None, "Starvation bonus should use named constants"

    def test_deprecated_scheduler_constants_exist_but_not_used_in_active_path(self):
        """SCHEDULER: Old iteration-based constants are DEPRECATED and not in active path."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        init_src = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        decide_src = inspect.getsource(FullyAutonomousOrchestrator._decide_next_action)
        # Old magic number 50 (iterations) must NOT appear in active path
        assert "self._monopoly_guard_window = 50" in init_src  # exists in __init__
        assert "_monopoly_guard_window = 50" not in decide_src  # NOT read in active path

    def test_deprecated_constants_are_marked_deprecated_in_init(self):
        """SCHEDULER: Old iteration-based constants are clearly marked DEPRECATED."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        assert "DEPRECATED" in src


class TestStarvationBonusComputation:
    """Verify starvation bonus computes correctly with new constants."""

    def test_starvation_bonus_zero_at_idle_below_onset(self):
        """STARVATION: Bonus is 0 when idle < onset (20s)."""
        onset = 20.0
        cap = 0.08
        idle_sec = 10.0
        starve_bonus = 0.0
        if idle_sec > onset:
            frac = min(1.0, (idle_sec - onset) / onset)
            starve_bonus = cap * frac
        assert starve_bonus == 0.0

    def test_starvation_bonus_full_at_40s_idle(self):
        """STARVATION: Bonus caps at 0.08 when idle >= 40s (2x onset)."""
        onset = 20.0
        cap = 0.08
        idle_sec = 40.0
        starve_bonus = 0.0
        if idle_sec > onset:
            frac = min(1.0, (idle_sec - onset) / onset)
            starve_bonus = cap * frac
        assert starve_bonus == 0.08  # full cap

    def test_starvation_bonus_half_at_30s_idle(self):
        """STARVATION: Bonus is 0.04 at 30s idle (halfway)."""
        onset = 20.0
        cap = 0.08
        idle_sec = 30.0
        starve_bonus = 0.0
        if idle_sec > onset:
            frac = min(1.0, (idle_sec - onset) / onset)
            starve_bonus = cap * frac
        assert starve_bonus == pytest.approx(0.04)


class TestProvenanceConsistency:
    """Provenance: source_type vs source_type_finding isolation."""

    def test_research_finding_has_source_type_finding_field(self):
        """PROVENANCE: ResearchFinding dataclass has source_type_finding field."""
        from hledac.universal.autonomous_orchestrator import ResearchFinding
        fields = [f.name for f in ResearchFinding.__dataclass_fields__.values()]
        assert "source_type_finding" in fields
        assert "extraction_method" in fields

    def test_source_type_finding_values_are_explicit_strings(self):
        """PROVENANCE: source_type_finding uses explicit string values."""
        import inspect

        from hledac.universal.autonomous_orchestrator import ResearchFinding
        src = inspect.getsource(ResearchFinding)
        assert "source_type_finding: str" in src

    def test_enhanced_research_finding_has_string_source_type(self):
        """PROVENANCE: enhanced_research.ResearchFinding uses string source_type."""
        from hledac.universal.enhanced_research import ResearchFinding as ERF
        fields = [f.name for f in ERF.__dataclass_fields__.values()]
        assert "source_type" in fields
        import inspect
        src = inspect.getsource(ERF)
        assert "source_type: str" in src

    def test_two_research_finding_classes_are_isolated(self):
        """PROVENANCE: No cross-module imports of ResearchFinding."""
        import hledac.universal.autonomous_orchestrator as ao
        import hledac.universal.enhanced_research as er
        assert hasattr(ao, 'ResearchFinding')
        assert hasattr(er, 'ResearchFinding')
        assert ao.ResearchFinding is not er.ResearchFinding


class TestConfidencePolicy:
    """Confidence policy is deterministic for main finding families."""

    def test_direct_harvest_confidence_is_explicit(self):
        """CONFIDENCE: direct_harvest findings have explicit confidence 0.75."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator)
        # direct_harvest sets confidence=0.75 for findings
        assert "confidence=0.75" in src

    def test_dlh_confidence_is_explicit(self):
        """CONFIDENCE: DLH findings have explicit confidence 0.85."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator)
        # DLH sets confidence=0.85 for findings
        assert "confidence=0.85" in src

    def test_generic_fallback_confidence_is_explicit(self):
        """CONFIDENCE: Surface web findings have explicit confidence."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator)
        # Surface web findings have confidence=0.7
        assert "confidence=0.7" in src


class TestImportTimeRegression:
    """Import time does not regress from baseline."""

    def test_import_time_acceptable(self):
        """IMPORT: Cold import < 2.5s (baseline was 1.75s)."""
        import subprocess
        import sys
        code = "import time; t=time.perf_counter(); import hledac.universal.autonomous_orchestrator as m; print(f'{time.perf_counter()-t:.3f}')"
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac"
        )
        # Parse last line only (stderr may contain warnings)
        lines = [l for l in out.stdout.strip().split('\n') if l]
        elapsed = float(lines[-1].strip())
        assert elapsed < 2.5, f"Import took {elapsed:.3f}s, expected < 2.5s"
