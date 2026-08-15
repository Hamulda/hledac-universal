# tests/test_memory_budget_gate.py
"""
Testy pro fetching/memory_budget_gate.py

Invarianty testované v tomto souboru:
- decide() s mocknutým psutil RSS=3.0 GiB → vrací camoufox, allowed=True
- decide() s mocknutým psutil RSS=6.0 GiB → vrací deferred, allowed=False
- _rss_gib() bez psutil → nesmí vyhodit výjimku
- BrowserDecision je frozen dataclass → ověř immutability
"""

from unittest.mock import patch

import pytest

# Importujeme modul pod testováním





    _HARD_GIB,
    _SOFT_GIB,
    BrowserDecision,
    _rss_gib,
    decide,
)


class TestDecideWithMockedRss:
    """Testy rozhodovací logiky s mocknutou RSS."""

from _core import aclose
    def test_decide_under_soft_limit_allows_camoufox(self):
        """RSS=3.0 GiB pod soft limitem → camoufox, allowed=True."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=3.0):
            result = decide(js_confidence=0.5, priority=5)
        assert result.tier == "camoufox"
        assert result.allowed is True
        assert result.rss_gib == 3.0

    def test_decide_at_soft_limit_deferred(self):
        """RSS=3.5 GiB na soft limitu bez priority override → deferred."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.5, priority=5)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_at_soft_limit_with_high_priority_allows_camoufox(self):
        """RSS=3.5 GiB na soft limitu S priority<=3 AND confidence>=0.75 → camoufox."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.8, priority=2)
        assert result.tier == "camoufox"
        assert result.allowed is True

    def test_decide_at_soft_limit_low_confidence_deferred(self):
        """RSS=3.5 GiB na soft limitu S nízkou confidence → deferred."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.5, priority=2)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_at_soft_limit_low_priority_deferred(self):
        """RSS=3.5 GiB na soft limitu S priority>3 → deferred."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.9, priority=4)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_over_hard_limit_deferred(self):
        """RSS=6.0 GiB nad hard limitem → deferred, allowed=False."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=6.0):
            result = decide(js_confidence=0.9, priority=1)
        assert result.tier == "deferred"
        assert result.allowed is False
        assert "hard limit" in result.reason

    def test_decide_high_memory_high_priority_overrides(self):
        """RSS=4.0 GiB (mezi soft/hard) S priority<=3 AND confidence>=0.75 → camoufox."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=4.0):
            result = decide(js_confidence=0.9, priority=1)
        assert result.tier == "camoufox"
        assert result.allowed is True


class TestRssGibFallback:
    """Testy _rss_gib() — nesmí vyhodit výjimku, vrací float."""

    def test_rss_gib_returns_float_no_exception(self):
        """_rss_gib() musí být fail-safe a vrátit float (psutil available in this env)."""
        val = _rss_gib()
        assert isinstance(val, float)
        assert val >= 0.0

    def test_rss_backend_is_psutil_only(self):
        """Ensure no /proc or getrusage code path exists in the module."""
        import inspect

        import fetching.memory_budget_gate as m
        src = inspect.getsource(m)
        assert "/proc" not in src
        assert "getrusage" not in src
        assert "RUSAGE_SELF" not in src


class TestBrowserDecisionFrozen:
    """Testy že BrowserDecision je immutable (frozen=True)."""

    def test_browser_decision_is_frozen(self):
        """BrowserDecision je frozen dataclass — nesmí jít měnit."""
        decision = BrowserDecision(
            tier="camoufox",
            allowed=True,
            rss_gib=2.5,
            js_confidence=0.7,
            reason="test",
        )
        with pytest.raises(AttributeError):
            decision.tier = "nodriver"  # type: ignore

    def test_browser_decision_fields_immutable(self):
        """Všechny fields BrowserDecision jsou immutable."""
        decision = BrowserDecision(
            tier="camoufox",
            allowed=True,
            rss_gib=2.5,
            js_confidence=0.7,
            reason="test",
        )
        with pytest.raises(AttributeError):
            decision.allowed = False  # type: ignore
        with pytest.raises(AttributeError):
            decision.rss_gib = 5.0  # type: ignore
        with pytest.raises(AttributeError):
            decision.js_confidence = 0.1  # type: ignore
        with pytest.raises(AttributeError):
            decision.reason = "changed"  # type: ignore

    def test_browser_decision_equality(self):
        """Dva stejné BrowserDecision jsou si rovny."""
        d1 = BrowserDecision(
            tier="camoufox",
            allowed=True,
            rss_gib=2.5,
            js_confidence=0.7,
            reason="test",
        )
        d2 = BrowserDecision(
            tier="camoufox",
            allowed=True,
            rss_gib=2.5,
            js_confidence=0.7,
            reason="test",
        )
        assert d1 == d2


class TestDecideEdgeCases:
    """Edge case testy pro rozhodovací logiku."""

    def test_decide_exactly_at_hard_limit(self):
        """RSS=Přesně na hard limitu → deferred."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_HARD_GIB):
            result = decide(js_confidence=1.0, priority=1)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_just_above_soft_limit(self):
        """RSS=3.51 GiB (těsně nad soft limitem) → deferred bez priority."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB + 0.01):
            result = decide(js_confidence=0.6, priority=5)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_zero_rss(self):
        """RSS=0.0 GiB → camoufox."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=0.0):
            result = decide(js_confidence=0.5, priority=5)
        assert result.tier == "camoufox"
        assert result.allowed is True

    def test_decide_priority_boundary(self):
        """Priority=3 je ještě povolena (priority <= 3)."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.8, priority=3)
        assert result.tier == "camoufox"
        assert result.allowed is True

    def test_decide_priority_boundary_rejected(self):
        """Priority=4 už není povolena (priority > 3)."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.8, priority=4)
        assert result.tier == "deferred"
        assert result.allowed is False

    def test_decide_confidence_boundary(self):
        """Confidence=0.75 je ještě povolena (>= 0.75)."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.75, priority=3)
        assert result.tier == "camoufox"
        assert result.allowed is True

    def test_decide_confidence_boundary_rejected(self):
        """Confidence=0.74 už není povolena (< 0.75)."""
        with patch("fetching.memory_budget_gate._rss_gib", return_value=_SOFT_GIB):
            result = decide(js_confidence=0.74, priority=3)
        assert result.tier == "deferred"
        assert result.allowed is False
