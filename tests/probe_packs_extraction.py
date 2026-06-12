"""
Hypothesis engine — C4 Tier-4 extraction: SourceHint + HypothesisPack
======================================================================

Sprint F262OBS-Tier4: Verify that :class:`SourceHint` and
:class:`HypothesisPack` were successfully extracted from
:mod:`brain.hypothesis_engine_engine` into :mod:`brain.hypothesis_engine.packs`,
with byte-for-byte class equivalence and 100% backward compatibility
(legacy import path still works).

Run: ``uv run pytest tests/probe_packs_extraction.py -v``
"""
from __future__ import annotations

import sys

sys.path.insert(0, "hledac/universal")


# ── Backward compatibility: every old import still works ─────────────────


class TestBackwardCompat:
    def test_legacy_source_hint_import_works(self) -> None:
        """`from brain.research_hypothesis_engine import SourceHint` must work."""
        from brain.research_hypothesis_engine import SourceHint  # noqa: F401

        assert SourceHint is not None
        assert SourceHint.__name__ == "SourceHint"

    def test_legacy_hypothesis_pack_import_works(self) -> None:
        """`from brain.research_hypothesis_engine import HypothesisPack` must work."""
        from brain.research_hypothesis_engine import HypothesisPack  # noqa: F401

        assert HypothesisPack is not None
        assert HypothesisPack.__name__ == "HypothesisPack"

    def test_legacy_source_hint_is_same_object(self) -> None:
        """Legacy and new import paths return the SAME class object."""
        from brain.hypothesis_engine.packs import SourceHint as New
        from brain.research_hypothesis_engine import SourceHint as Old

        assert New is Old, "Legacy and new SourceHint must resolve to same class"
        assert New.__module__ == "brain.hypothesis_engine.packs"

    def test_legacy_hypothesis_pack_is_same_object(self) -> None:
        """Legacy and new import paths return the SAME class object."""
        from brain.hypothesis_engine.packs import HypothesisPack as New
        from brain.research_hypothesis_engine import HypothesisPack as Old

        assert New is Old, "Legacy and new HypothesisPack must resolve to same class"
        assert New.__module__ == "brain.hypothesis_engine.packs"


# ── Forward import path: brain.hypothesis_engine.packs ──────────────────────────


class TestForwardImport:
    def test_package_exports_packs(self) -> None:
        """`from brain.hypothesis_engine import SourceHint, HypothesisPack` must work."""
        import brain.hypothesis_engine as pkg

        assert hasattr(pkg, "SourceHint")
        assert hasattr(pkg, "HypothesisPack")
        assert "SourceHint" in pkg.__all__
        assert "HypothesisPack" in pkg.__all__


# ── Functional smoke tests ────────────────────────────────────────────────


class TestFunctionalBehavior:
    def test_source_hint_construction(self) -> None:
        """SourceHint instantiates with source/quality/hint_type defaults."""
        from brain.hypothesis_engine.packs import SourceHint

        # Defaults: hint_type = "general"
        h = SourceHint(source="example.com", quality=0.85)
        assert h.source == "example.com"
        assert h.quality == 0.85
        assert h.hint_type == "general"

        # Explicit hint_type
        h2 = SourceHint(source="arxiv.org", quality=0.95, hint_type="trusted_source")
        assert h2.hint_type == "trusted_source"

    def test_empty_hypothesis_pack(self) -> None:
        """Empty HypothesisPack: is_empty=True, signal_quality=weak, summary=empty."""
        from brain.hypothesis_engine.packs import HypothesisPack

        pack = HypothesisPack()
        assert pack.is_empty() is True
        assert pack.signal_quality == "weak"
        assert pack.summary() == "empty"
        assert pack.best_first_path() is None
        assert pack.next_best_actions(max_actions=4) == []
        assert pack.what_matters_first == "No immediate action — empty hypothesis pack"

    def test_rich_hypothesis_pack_signal_quality(self) -> None:
        """3+ hypotheses, 2+ queries, 1+ IOC → signal_quality = strong."""
        from brain.hypothesis_engine.packs import HypothesisPack

        pack = HypothesisPack(
            hypotheses=[{"hypothesis": f"h{i}"} for i in range(3)],
            suggested_queries=[{"query": f"q{i}", "priority": 0.5} for i in range(2)],
            ioc_follow_ups=[{"query": "ioc1", "priority": 0.8}],
        )
        assert pack.is_empty() is False
        assert pack.signal_quality == "strong"
        # 3+2+1=6 → "moderate pack" (rich pack threshold is 8)
        assert "moderate pack" in pack.confidence_note
        assert pack.what_matters_first.startswith("Pivot on IOC")

    def test_best_first_path_prefers_ioc(self) -> None:
        """best_first_path returns IOC pivot over queries when both present."""
        from brain.hypothesis_engine.packs import HypothesisPack

        pack = HypothesisPack(
            suggested_queries=[{"query": "broad_query", "priority": 0.9}],
            ioc_follow_ups=[{"query": "ioc_query", "priority": 0.7, "from": "ip:1.2.3.4", "to": "domain"}],
        )
        path = pack.best_first_path()
        assert path is not None
        assert path["action_type"] == "ioc_pivot"
        assert path["query"] == "ioc_query"
        assert path["pivot_type"] == "ioc"

    def test_actionable_shortlist_respects_max(self) -> None:
        """actionable_shortlist(max_items=2) returns at most 2 items."""
        from brain.hypothesis_engine.packs import HypothesisPack

        pack = HypothesisPack(
            ioc_follow_ups=[
                {"query": f"ioc{i}", "priority": 0.9 - i * 0.1, "from": "a", "to": "b"}
                for i in range(5)
            ],
        )
        shortlist = pack.actionable_shortlist(max_items=2)
        assert len(shortlist) == 2
        # Highest priority first
        assert shortlist[0]["query"] == "ioc0"
        assert shortlist[1]["query"] == "ioc1"

    def test_source_hint_used_in_action_confidence(self) -> None:
        """action_confidence uses SourceHint quality for source_check actions."""
        from brain.hypothesis_engine.packs import HypothesisPack, SourceHint

        pack = HypothesisPack(
            source_hints=[SourceHint(source="nytimes.com", quality=0.9)],
            provenance="heuristic",
        )
        # Query must be exactly '"nytimes.com"' (with quotes) so .strip('"') yields 'nytimes.com'
        action = {
            "action_type": "source_check",
            "query": '"nytimes.com"',
            "priority": 0.5,
            "pivot_type": "source",
        }
        confidence = pack.action_confidence(action)
        # source pivot_type → pt_bonus=0.0; priority=0.5; source_bonus=(0.9-0.5)*0.2=0.08
        # confidence = 0.5*0.4 + min(0.5, 1.0)*0.4 + 0.08 + 0.0 = 0.48
        assert 0.0 <= confidence <= 1.0
        assert abs(confidence - 0.48) < 0.01, f"Expected ~0.48, got {confidence}"

        # Compare to an empty pack (no source_hints) — same action, lower confidence
        empty_pack = HypothesisPack()
        empty_conf = empty_pack.action_confidence(action)
        # Empty pack: source_bonus=0.0 (no matching hint) → 0.4
        assert empty_conf < confidence, "Source hint should boost confidence"
        assert abs(empty_conf - 0.4) < 0.01
