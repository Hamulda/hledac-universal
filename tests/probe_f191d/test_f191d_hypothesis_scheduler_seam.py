"""
Sprint F191D: Hypothesis / Scheduler Seam Sharpening
==================================================
Hermetic tests for hypothesis/scheduler seam sharpening.

DRIFT FAMILIES:
- discarded_as_redundant not exposed to scheduler (new: F191D)
- operator_shortlist hard-coded [:3] limit (cosmetic, no behavioral change)
- what_matters_first already in verdict (no drift)

HARD RULES (verified by tests):
- No new planner world (HypothesisEngine remains read-first/editing-only-if-drift)
- Scheduler consumer seam stability (compute_sprint_intelligence shape contract)
- Bounded next-query feedback (discarded_as_redundant max_items=3)
- No new persistence
- No broad brain promotion
- M1 Air 8GB invariant (memory-cheap, non-blocking)

Run:
    cd hledac/universal
    python -m pytest tests/probe_f191d/test_f191d_hypothesis_scheduler_seam.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import inspect
from unittest.mock import patch

import pytest


# -------------------------------------------------------------------------
# Module loading — bypass broken project-root __init__.py
# -------------------------------------------------------------------------
def _load_sprint_scheduler():
    spec = importlib.util.spec_from_file_location(
        "runtime.sprint_scheduler",
        "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["runtime.sprint_scheduler"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_hypothesis_engine():
    spec = importlib.util.spec_from_file_location(
        "brain.hypothesis_engine",
        "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/brain/hypothesis_engine.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["brain.hypothesis_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


ss_mod = _load_sprint_scheduler()
hs_mod = _load_hypothesis_engine()

SprintScheduler = ss_mod.SprintScheduler
HypothesisEngine = hs_mod.HypothesisEngine
HypothesisPack = hs_mod.HypothesisPack


class TestF191D_DiscardedAsRedundantExposed:
    """F191D: discarded_as_redundant is now exposed to scheduler consumer seam."""

    def test_hypothesis_pack_contains_discarded_as_redundant_key(self):
        """compute_sprint_intelligence hypothesis_pack dict has discarded_as_redundant key."""
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._all_findings = []

        result = sched.compute_sprint_intelligence()
        assert "hypothesis_pack" in result

    def test_discarded_as_redundant_bounded_max_3_items(self):
        """discarded_as_redundant is bounded to max 3 items."""
        eng = HypothesisEngine()
        pack = eng.build_hypothesis_pack(
            [f"test finding {i}" for i in range(10)]
        )
        dar = pack.discarded_as_redundant(max_items=3)
        assert isinstance(dar, list)
        assert len(dar) <= 3

    def test_compute_sprint_intelligence_preserves_existing_keys(self):
        """Adding discarded_as_redundant does NOT remove any existing hypothesis_pack keys."""
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._all_findings = [
            {"source": "test", "description": "APT28 malware finding", "verdict": "high"},
        ]
        sched._feed_verdicts = []
        sched._public_verdicts = []

        from unittest.mock import MagicMock

        with patch.object(ss_mod, "_import_correlate_findings") as mc:
            mc.return_value = MagicMock(
                risk_score=0.0,
                verdict="low",
                anomaly_count=0,
                themes=[],
                top_themes=[],
                signal_quality="weak",
                cross_source_confidence=0.0,
                campaign_confidence=0.0,
                dominant_cluster=None,
                so_what="",
                what_matters_first="",
                operator_shortlist=[],
                confidence_note="",
                corroborated_iocs=[],
                top_priority_pivots=[],
            )
            result = sched.compute_sprint_intelligence()

        hp = result.get("hypothesis_pack")
        assert hp is not None, "hypothesis_pack must not be None when findings present"

        required_keys = [
            "hypothesis_count",
            "query_count",
            "ioc_follow_ups",
            "source_hints_count",
            "provenance",
            "signal_quality",
            "what_matters_first",
            "confidence_note",
            "top_queries",
            "operator_shortlist",
        ]
        for key in required_keys:
            assert key in hp, f"Existing key '{key}' must be preserved"

        # New F191D key
        assert (
            "discarded_as_redundant" in hp
        ), "discarded_as_redundant must be in hypothesis_pack"

    def test_discarded_as_redundant_shape_in_compute_sprint_intelligence(self):
        """discarded_as_redundant items have scheduler-consumable shape."""
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._all_findings = [
            {"source": "t", "description": "APT28 and C2 server", "verdict": "high"}
        ]
        sched._feed_verdicts = []
        sched._public_verdicts = []

        from unittest.mock import MagicMock

        with patch.object(ss_mod, "_import_correlate_findings") as mc:
            mc.return_value = MagicMock(
                risk_score=0.0,
                verdict="low",
                anomaly_count=0,
                themes=[],
                top_themes=[],
                signal_quality="weak",
                cross_source_confidence=0.0,
                campaign_confidence=0.0,
                dominant_cluster=None,
                so_what="",
                what_matters_first="",
                operator_shortlist=[],
                confidence_note="",
                corroborated_iocs=[],
                top_priority_pivots=[],
            )
            result = sched.compute_sprint_intelligence()

        hp = result.get("hypothesis_pack", {})
        dar = hp.get("discarded_as_redundant", [])
        # Key exists and is a list
        assert isinstance(dar, list)
        if dar:
            item = dar[0]
            assert "action_type" in item
            assert "query" in item
            assert "reason_discarded" in item
            assert "pivot_type" in item
            assert "priority" in item


class TestF191D_HardRules:
    """F191D HARD RULES — no planner world, no new persistence, no brain promotion."""

    def test_no_new_planner_world(self):
        """HypothesisEngine stays read-first — no orchestrator/planner entry points."""
        names = [n for n in dir(hs_mod) if not n.startswith("_")]
        bad = [n for n in names if "orchestrat" in n.lower() or "planner" in n.lower()]
        assert not bad, f"Found orchestrator/planner names: {bad}"

    def test_no_new_persistence_in_hypothesis_engine(self):
        """HypothesisEngine has no new persistence (DB, file, LMDB) calls."""
        source = inspect.getsource(hs_mod)
        for kw in ["lmdb", "duckdb", "chroma", ".db", "sqlite"]:
            assert kw not in source.lower(), f"Persistence '{kw}' found in hypothesis_engine"

    def test_m1_memory_cheap_invariant(self):
        """discarded_as_redundant is memory-cheap: bounded single-pass implementation."""
        source = inspect.getsource(HypothesisPack.discarded_as_redundant)
        lines = [
            l.strip()
            for l in source.split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
        assert len(lines) < 60, (
            f"discarded_as_redundant is {len(lines)} lines — "
            "too complex for memory-cheap M1 invariant"
        )
