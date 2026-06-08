"""
Hypothesis engine — C4 Tier-3 partial extraction: SimpleNodeAblationExplainer
=============================================================================

Sprint F262OBS-Tier3: Verify that :class:`SimpleNodeAblationExplainer` was
successfully extracted from :mod:`brain.hypothesis_engine` into
:mod:`brain.hypothesis.explainer`, with byte-for-byte class equivalence
and 100% backward compatibility (legacy import path still works).

Run: ``uv run pytest tests/probe_explainer_extraction.py -v``
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

sys.path.insert(0, "hledac/universal")


# ── Backward compatibility: every old import still works ─────────────────


class TestBackwardCompat:
    def test_legacy_import_still_works(self) -> None:
        """`from brain.research_hypothesis_engine import SimpleNodeAblationExplainer` must work."""
        from brain.research_hypothesis_engine import SimpleNodeAblationExplainer  # noqa: F401

        assert SimpleNodeAblationExplainer is not None
        assert SimpleNodeAblationExplainer.__name__ == "SimpleNodeAblationExplainer"

    def test_legacy_class_is_same_object(self) -> None:
        """The legacy import and the new import return the SAME class object."""
        from brain.hypothesis.explainer import SimpleNodeAblationExplainer as New
        from brain.research_hypothesis_engine import SimpleNodeAblationExplainer as Old

        # Identity check — same class, not a copy
        assert New is Old, "Legacy and new import paths must resolve to the same class"
        # And the class lives in the new module after extraction
        assert New.__module__ == "brain.hypothesis.explainer"


# ── Forward import path: brain.hypothesis.explainer ───────────────────────


class TestForwardImport:
    def test_package_exports_explainer(self) -> None:
        """`from brain.hypothesis import SimpleNodeAblationExplainer` must work."""
        import brain.hypothesis as pkg

        assert hasattr(pkg, "SimpleNodeAblationExplainer")
        assert "SimpleNodeAblationExplainer" in pkg.__all__


# ── Functional smoke test ────────────────────────────────────────────────


class TestFunctionalBehavior:
    def test_explain_path_too_short(self) -> None:
        """explain_path with path < 2 returns empty dict (M1 fast path)."""
        from brain.hypothesis.explainer import SimpleNodeAblationExplainer

        # Path with single element is rejected immediately
        explainer = SimpleNodeAblationExplainer(graph_rag=MagicMock())
        import asyncio
        result = asyncio.run(explainer.explain_path(["only_one"], "hypothesis"))
        assert result == {}
