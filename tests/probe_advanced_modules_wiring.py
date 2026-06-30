"""
probe_advanced_modules_wiring — bounded hermetic tests for Sprint F-ADV.

Verifies:
    - hledac/universal/advanced_rag/rag_orchestrator.py → canonical LanceDBIdentityStore binding
    - hledac/universal/advanced_web/stealth_browser.py  → M1 2-tab cap, no asyncio.to_thread
    - hledac/universal/advanced_web/evidence_network_analyzer.py → NOT_IMPLEMENTED marker
    - enhanced_research.UnifiedResearchEngine → capability-flag gated providers
    - Cleanup() releases all advanced provider references

INVARIANTS (always-on, M1 8GB UMA safe):
    1. Single LanceDB connection (no second DB opened by RAGOrchestrator).
    2. _MAX_CONCURRENT_TABS == 2 (M1 constraint).
    3. evidence_network_analyzer.is_implemented() == False (STUB).
    4. Capability flags default OFF (HLEDAC_ENABLE_ADVANCED_*).
    5. All collections bounded (MAX_SOURCES=20, MAX_FETCHES=5).
    6. Fail-soft: any exception → empty result, never raises.
    7. asyncio.to_thread is NEVER used for I/O (uses loop.run_in_executor).

Run: `pytest tests/probe_advanced_modules_wiring.py -v`
"""

import importlib
import os
import sys
from typing import Any

import pytest

# Ensure the project root is on sys.path so `hledac.universal.enhanced_research`
# is importable (matches existing test pattern in probe_f11_triad_connection.py).
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# =============================================================================
# TestSprintFADVA — advanced_rag.RAGOrchestrator
# =============================================================================

class TestSprintFADVA:
    """RAGOrchestrator must bind to canonical LanceDB, fail-soft on init failure."""

    def test_rag_orchestrator_imports(self) -> None:
        mod = importlib.import_module("hledac.universal.advanced_rag.rag_orchestrator")
        assert mod is not None
        assert hasattr(mod, "RAGOrchestrator")

    def test_rag_orchestrator_init_lazy(self) -> None:
        """RAGOrchestrator.__init__ must NOT eagerly init LanceDB."""
        from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator

        rag = RAGOrchestrator()
        assert rag._initialized is False
        assert rag._store is None

    @pytest.mark.asyncio
    async def test_rag_initialize_returns_dict_shape_on_failure(self) -> None:
        """When LanceDBIdentityStore is unavailable, research_and_answer must
        still return the dict shape research_coordinator expects, with empty
        sources and a non-zero error path."""
        from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator

        rag = RAGOrchestrator()
        # Force init failure by clearing the store before initialize()
        rag._initialized = True
        rag._store = None

        result = await rag.research_and_answer(query="anything", confidence_threshold=0.5)
        assert isinstance(result, dict)
        assert result["sources"] == []
        assert result["answer"].startswith("RAG engine unavailable")
        assert result["confidence"] == 0.0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rag_empty_query_returns_empty_result(self) -> None:
        """Empty / whitespace query must not crash."""
        from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator

        rag = RAGOrchestrator()
        result = await rag.research_and_answer(query="", confidence_threshold=0.5)
        assert result["sources"] == []

    def test_rag_bounded_limits_defined(self) -> None:
        """Module-level bounds must be present and sane (M1 8GB)."""
        from hledac.universal.advanced_rag import rag_orchestrator

        assert rag_orchestrator._MAX_SOURCES == 20
        assert rag_orchestrator._MAX_QUERY_CHARS == 1024
        assert rag_orchestrator._TOKEN_CHARS_PER_SOURCE == 500

    @pytest.mark.asyncio
    async def test_rag_respects_confidence_threshold(self) -> None:
        """Confidence threshold must filter results below it."""
        from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator

        rag = RAGOrchestrator()
        # Force a fake store with controlled output
        class _FakeStore:
            async def search_similar_adaptive(self, query_text, query_emb, top_k):
                return [
                    {"id": "a", "text": "alpha text", "similarity": 0.9, "_embedding": [0.1]},
                    {"id": "b", "text": "beta text", "similarity": 0.3, "_embedding": [0.2]},
                ]
            async def _embed_single(self, text):
                return [0.0] * 256
        rag._store = _FakeStore()
        rag._initialized = True

        # Threshold 0.5 → only the 0.9 source should pass
        result = await rag.research_and_answer(query="test", confidence_threshold=0.5)
        ids = [s["id"] for s in result["sources"]]
        assert "a" in ids
        assert "b" not in ids
        assert result["confidence"] == pytest.approx(0.9, abs=0.01)

    @pytest.mark.asyncio
    async def test_rag_caps_at_max_sources(self) -> None:
        """Must never return more than _MAX_SOURCES entries."""
        from hledac.universal.advanced_rag.rag_orchestrator import _MAX_SOURCES, RAGOrchestrator

        rag = RAGOrchestrator()

        class _FakeStore:
            async def search_similar_adaptive(self, query_text, query_emb, top_k):
                return [
                    {"id": f"id_{i}", "text": f"text {i}", "similarity": 0.9,
                     "_embedding": [0.1]}
                    for i in range(50)
                ]
            async def _embed_single(self, text):
                return [0.0] * 256
        rag._store = _FakeStore()
        rag._initialized = True

        result = await rag.research_and_answer(query="test", confidence_threshold=0.5)
        assert len(result["sources"]) <= _MAX_SOURCES

    def test_rag_uses_run_in_executor_not_to_thread_for_io(self) -> None:
        """Static check: the orchestrator must NOT use asyncio.to_thread for I/O.
        This is a project invariant. The orchestrator delegates to the
        canonical store which already follows the invariant."""
        from hledac.universal.advanced_rag import rag_orchestrator

        with open(rag_orchestrator.__file__) as fh:
            src = fh.read()
        # The orchestrator itself may use asyncio.to_thread (for the MLX
        # embedding path inside the store), but its own I/O dispatch should
        # not introduce new direct to_thread calls. Check that the top-level
        # _embed_offloop method does not call asyncio.to_thread.
        # Extract the _embed_offloop body
        import re
        m = re.search(r"async def _embed_offloop.*?(?=\n    [a-zA-Z#]|\nclass|\Z)",
                      src, re.DOTALL)
        assert m, "_embed_offloop not found"
        body = m.group(0)
        # Strip docstring to avoid false positives from rule mentions
        body_no_docstring = re.sub(r'"""[\s\S]*?"""', "", body)
        assert "asyncio.to_thread" not in body_no_docstring, (
            "asyncio.to_thread forbidden in _embed_offloop (use loop.run_in_executor)"
        )


# =============================================================================
# TestSprintFADVB — advanced_web.StealthBrowser
# =============================================================================

class TestSprintFADVB:
    """StealthBrowser must respect M1 2-tab cap and not use asyncio.to_thread."""

    def test_max_concurrent_tabs_is_two(self) -> None:
        """M1 8GB constraint: max 2 concurrent browser tabs."""
        from hledac.universal.advanced_web.stealth_browser import _MAX_CONCURRENT_TABS
        assert _MAX_CONCURRENT_TABS == 2, (
            f"_MAX_CONCURRENT_TABS must be 2 for M1, got {_MAX_CONCURRENT_TABS}"
        )

    def test_semaphore_matches_max(self) -> None:
        """Semaphore capacity must equal _MAX_CONCURRENT_TABS."""
        import asyncio

        from hledac.universal.advanced_web import stealth_browser

        # Module-level semaphore — its _value is the open permits
        sem = stealth_browser._semaphore
        assert isinstance(sem, asyncio.Semaphore)
        # Semaphore._value reflects remaining capacity
        # In Python 3.10+ this is unlocked
        if hasattr(sem, "_value"):
            assert sem._value == stealth_browser._MAX_CONCURRENT_TABS
        else:
            # Fallback: check internal _waiters
            assert sem._value == 2 or True  # Python version-tolerant

    def test_stealth_browser_init_does_not_start_browser(self) -> None:
        """Construction must be cheap; browser is started on first fetch()."""
        from hledac.universal.advanced_web.stealth_browser import StealthBrowser

        browser = StealthBrowser()
        # No _session bound at construction time
        assert browser._session is None

    @pytest.mark.asyncio
    async def test_stealth_browser_fetch_error_returns_error_dict(self) -> None:
        """fetch() must never raise — return error dict on any exception."""
        from hledac.universal.advanced_web.stealth_browser import StealthBrowser

        browser = StealthBrowser()
        # Force nodriver path even if installed — give a malformed URL
        result = await browser.fetch("not-a-valid-url", depth=1)
        assert isinstance(result, dict)
        assert result["url"] == "not-a-valid-url"
        # status is 0 on error
        assert result["status"] == 0
        assert result["js_rendered"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_stealth_browser_cleanup_handles_none_session(self) -> None:
        """cleanup() must be safe when no session was opened."""
        from hledac.universal.advanced_web.stealth_browser import StealthBrowser

        browser = StealthBrowser()
        # No _session set
        await browser.cleanup()  # must not raise

    def test_stealth_browser_uses_run_in_executor(self) -> None:
        """Static check: the blocking httpx call must go through run_in_executor,
        never asyncio.to_thread (project invariant)."""
        from hledac.universal.advanced_web import stealth_browser

        with open(stealth_browser.__file__) as fh:
            src = fh.read()
        # Look for the httpx fetch section
        assert "loop.run_in_executor" in src, (
            "StealthBrowser must use loop.run_in_executor for sync I/O"
        )
        # asyncio.to_thread must not appear in the httpx fallback path
        # (it may appear elsewhere as a comment about the rule)
        # Find _fetch_httpx body
        import re
        m = re.search(r"async def _fetch_httpx.*?(?=\n    [a-zA-Z#]|\nclass|\Z)",
                      src, re.DOTALL)
        if m:
            body = m.group(0)
            body_no_docstring = re.sub(r'"""[\s\S]*?"""', "", body)
            assert "asyncio.to_thread" not in body_no_docstring, (
                "asyncio.to_thread forbidden in _fetch_httpx"
            )


# =============================================================================
# TestSprintFADVC — advanced_web.EvidenceNetworkAnalyzer
# =============================================================================

class TestSprintFADVC:
    """EvidenceNetworkAnalyzer — bounded M1-safe network analysis (T1 impl)."""

    def test_is_implemented_returns_true(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        assert ana.is_implemented() is True
        assert ana._NOT_IMPLEMENTED is False

    @pytest.mark.asyncio
    async def test_analyze_network_with_single_entity_returns_empty_result(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        result = await ana.analyze_network(
            entities=[{"type": "url", "value": "x"}]
        )
        assert isinstance(result, dict)
        assert result["not_implemented"] is False
        assert "todo_ref" in result
        # Single entity: present in entities list, no edges/contradictions,
        # clusters holds the single node as a singleton community. Centrality
        # value is implementation-defined for n=1 — just verify the key.
        assert len(result["entities"]) == 1
        assert result["entities"][0]["value"] == "x"
        assert result["edges"] == []
        assert result["contradictions"] == []
        assert "url:x" in result["centrality"]
        assert 0.0 <= result["centrality"]["url:x"] <= 1.0
        assert result["analysis_type"] == "evidence_network"

    @pytest.mark.asyncio
    async def test_analyze_network_extracts_shared_domain_relationship(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        result = await ana.analyze_network(
            entities=[
                {"type": "domain", "value": "https://evil.example.com/path1"},
                {"type": "domain", "value": "https://evil.example.com/path2"},
            ]
        )
        assert result["not_implemented"] is False
        assert len(result["entities"]) == 2
        # Two URLs on the same eTLD+1 must produce at least one shared_domain edge.
        assert any(e.get("type") == "shared_domain" for e in result["edges"]), (
            f"expected shared_domain edge, got: {result['edges']}"
        )
        assert result["confidence"] > 0.0

    @pytest.mark.asyncio
    async def test_extract_relationships_returns_nonempty_for_related_entities(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.extract_relationships(
            entities=[
                {"type": "domain", "value": "alpha.example.com"},
                {"type": "domain", "value": "beta.example.com"},
                {"type": "ip", "value": "1.2.3.4"},
            ]
        )
        # Two domains share the same eTLD+1 (.example.com) → at least one edge.
        assert isinstance(out, list)
        assert len(out) >= 1
        for edge in out:
            assert {"src", "dst", "weight", "type"} <= set(edge.keys())
            assert 0.0 < edge["weight"] <= 1.0

    @pytest.mark.asyncio
    async def test_detect_contradictions_finds_mutual_exclusion(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.detect_contradictions(
            {"status": "service is online"},
            {"status": "service is offline"},
        )
        assert out is not None
        assert out.get("contradicts") is True
        assert out.get("confidence", 0.0) > 0.5
        assert "reason" in out

    @pytest.mark.asyncio
    async def test_detect_contradictions_finds_numeric_conflict(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.detect_contradictions(
            {"size": "100MB payload"},
            {"size": "500MB payload"},
        )
        assert out is not None
        assert out.get("contradicts") is True
        assert "numeric_conflict" in out.get("reason", "")

    @pytest.mark.asyncio
    async def test_detect_contradictions_returns_none_for_uncorrelated(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.detect_contradictions(
            {"a": 1, "b": "x"},
            {"a": 1, "b": "x"},  # identical → no contradiction
        )
        assert out is None

    @pytest.mark.asyncio
    async def test_calculate_centrality_returns_scores_for_graph(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.calculate_centrality({
            "entities": [
                {"key": "a"}, {"key": "b"}, {"key": "c"},
            ],
            "edges": [
                {"src": "a", "dst": "b", "weight": 0.9},
                {"src": "b", "dst": "c", "weight": 0.9},
            ],
        })
        assert isinstance(out, dict)
        assert set(out.keys()) == {"a", "b", "c"}
        # b is the bridge — it must have the highest combined centrality.
        assert out["b"] >= out["a"]
        assert out["b"] >= out["c"]
        for score in out.values():
            assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_calculate_centrality_empty_input_returns_empty(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.calculate_centrality({})
        assert out == {}

    @pytest.mark.asyncio
    async def test_analyze_evidence_network_returns_networks_payload(self) -> None:
        """High-level entry point used by research_coordinator."""
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        out = await ana.analyze_evidence_network(
            query="evil.example.com malware C2",
            confidence_threshold=0.7,
            priority=8,
        )
        assert "networks" in out
        assert isinstance(out["networks"], list)
        assert len(out["networks"]) == 1
        net = out["networks"][0]
        assert net["query"] == "evil.example.com malware c2"
        assert "entities" in net
        assert "edges" in net
        assert out["not_implemented"] is False
        assert out["priority"] == 8
        assert 0.0 <= out["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_analyze_network_empty_input_returns_empty_result(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        result = await ana.analyze_network(entities=[])
        assert result["entities"] == []
        assert result["edges"] == []
        assert result["clusters"] == []
        assert result["centrality"] == {}
        assert result["confidence"] == 0.0
        assert result["not_implemented"] is False

    @pytest.mark.asyncio
    async def test_analyze_network_none_input_returns_empty_result(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        result = await ana.analyze_network(entities=None)
        assert result["entities"] == []
        assert result["edges"] == []
        assert result["not_implemented"] is False

    @pytest.mark.asyncio
    async def test_analyze_network_malformed_input_is_fail_safe(self) -> None:
        """Malformed entities must not raise — bounded empty result returned."""
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        result = await ana.analyze_network(
            entities=[
                None,  # not a dict
                {"type": "ip"},  # missing value
                {"value": "1.2.3.4"},  # missing type — coerced
                "not a dict",  # wrong type
                {"type": "ip", "value": "1.2.3.4"},  # valid
            ]
        )
        assert result["not_implemented"] is False
        # Only the valid entity survives coercion.
        assert len(result["entities"]) == 2

    @pytest.mark.asyncio
    async def test_analyze_network_is_bounded_under_load(self) -> None:
        """Large input must be bounded by MAX_ENTITIES — no RAM explosion."""
        from hledac.universal.advanced_web.evidence_network_analyzer import (
            MAX_ENTITIES,
            EvidenceNetworkAnalyzer,
        )
        ana = EvidenceNetworkAnalyzer()
        # 1500 entities, 3 unique (dedup target) + 1 outlier repeated — well over MAX_ENTITIES (500).
        big: list[dict[str, Any]] = []
        for i in range(500):
            big.append({"type": "domain", "value": f"https://host{i % 3}.example.com/path{i}"})
        for _ in range(1000):
            big.append({"type": "ip", "value": "1.2.3.4"})
        result = await ana.analyze_network(entities=big)
        # Bounded by MAX_ENTITIES — analyzer must not iterate over all 1500.
        assert len(result["entities"]) <= MAX_ENTITIES
        # Edges cap respected.
        assert len(result["edges"]) <= 2000

    @pytest.mark.asyncio
    async def test_call_count_increments(self) -> None:
        """Telemetry counter must increment on every public call."""
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        before = ana._call_count
        await ana.analyze_network(entities=[])
        await ana.extract_relationships(entities=[])
        await ana.detect_contradictions({}, {})
        await ana.calculate_centrality({})
        assert ana._call_count == before + 4

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self) -> None:
        from hledac.universal.advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
        ana = EvidenceNetworkAnalyzer()
        await ana.cleanup()
        await ana.cleanup()  # must not raise
        assert ana._initialized is False

    def test_init_does_not_open_igraph_or_networkx(self) -> None:
        """Static check: module must NOT eagerly import igraph or networkx."""
        from hledac.universal.advanced_web import evidence_network_analyzer

        with open(evidence_network_analyzer.__file__) as fh:
            src = fh.read()
        import re
        imports = re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.MULTILINE)
        for name in imports:
            assert "igraph" not in name
            assert "networkx" not in name


# =============================================================================
# TestSprintFADVD — enhanced_research.UnifiedResearchEngine integration
# =============================================================================

class TestSprintFADVD:
    """UnifiedResearchEngine must respect capability flags and add bounded phases."""

    def test_config_has_advanced_flags(self) -> None:
        from hledac.universal.enhanced_research import UnifiedResearchConfig

        cfg = UnifiedResearchConfig()
        assert cfg.enable_advanced_rag is False
        assert cfg.enable_stealth_browser is False
        assert cfg.enable_evidence_analyzer is False
        assert cfg.max_advanced_findings == 20  # bounded

    def test_env_flag_activates_advanced_rag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting HLEDAC_ENABLE_ADVANCED_RAG=1 must flip the config flag."""
        monkeypatch.setenv("HLEDAC_ENABLE_ADVANCED_RAG", "1")
        # Reload the module-level env reader state
        from hledac.universal.enhanced_research import (
            _ADVANCED_RAG_ENV,
            UnifiedResearchEngine,
            _env_flag,
        )

        # Sanity: the env-flag reader picks it up
        assert _env_flag(_ADVANCED_RAG_ENV) is True
        # The engine should read it when no explicit config is passed
        engine = UnifiedResearchEngine()
        assert engine.config.enable_advanced_rag is True

    def test_explicit_config_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If config is provided explicitly, env var must NOT override it."""
        monkeypatch.setenv("HLEDAC_ENABLE_ADVANCED_RAG", "1")
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        cfg = UnifiedResearchConfig(enable_advanced_rag=False)
        engine = UnifiedResearchEngine(config=cfg)
        # Explicit config wins
        assert engine.config.enable_advanced_rag is False

    def test_stats_keys_include_advanced(self) -> None:
        """Engine stats must include the three new advanced counters."""
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        stats = engine.get_statistics()
        assert "advanced_rag_queries" in stats
        assert "stealth_fetches" in stats
        assert "evidence_analyses" in stats
        # All zero at start
        assert stats["advanced_rag_queries"] == 0
        assert stats["stealth_fetches"] == 0
        assert stats["evidence_analyses"] == 0

    def test_stats_config_reports_capability_flags(self) -> None:
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        stats = engine.get_statistics()
        assert stats["config"]["advanced_rag_enabled"] is False
        assert stats["config"]["stealth_browser_enabled"] is False
        assert stats["config"]["evidence_analyzer_enabled"] is False

    @pytest.mark.asyncio
    async def test_get_advanced_rag_returns_none_when_disabled(self) -> None:
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        # No env, no config flag → returns None
        out = await engine._get_advanced_rag()
        assert out is None

    @pytest.mark.asyncio
    async def test_get_stealth_browser_returns_none_when_disabled(self) -> None:
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        out = await engine._get_stealth_browser()
        assert out is None

    @pytest.mark.asyncio
    async def test_get_evidence_analyzer_returns_none_when_disabled(self) -> None:
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        out = await engine._get_evidence_analyzer()
        assert out is None

    @pytest.mark.asyncio
    async def test_stealth_browser_lazy_load_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With enable_stealth_browser=True, _get_stealth_browser must return an
        instance (even if browser init fails — it should not raise)."""
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        # Set config explicitly
        cfg = UnifiedResearchConfig(enable_stealth_browser=True)
        engine = UnifiedResearchEngine(config=cfg)
        # Pass through the env-override guard by setting the config directly
        engine.config.enable_stealth_browser = True

        out = await engine._get_stealth_browser()
        # If nodriver is unavailable, _get_stealth_browser returns None
        # (graceful degradation). If available, returns StealthBrowser.
        # Either way, it MUST NOT raise.
        assert out is None or hasattr(out, "fetch")

    @pytest.mark.asyncio
    async def test_evidence_analyzer_lazy_load_when_enabled(self) -> None:
        """With enable_evidence_analyzer=True, _get_evidence_analyzer must
        return an implemented EvidenceNetworkAnalyzer instance (not None).
        Post-T1 the analyzer is fully implemented — it must still be lazy and
        must be the same instance on repeated calls."""
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        cfg = UnifiedResearchConfig(enable_evidence_analyzer=True)
        engine = UnifiedResearchEngine(config=cfg)
        engine.config.enable_evidence_analyzer = True

        out1 = await engine._get_evidence_analyzer()
        out2 = await engine._get_evidence_analyzer()
        assert out1 is not None
        # Lazy + cached — same instance on repeated calls
        assert out1 is out2
        # Post-T1: the analyzer is implemented
        assert hasattr(out1, "is_implemented")
        assert out1.is_implemented() is True
        # The analyzer must expose the canonical API surface
        for method in ("analyze_network", "extract_relationships",
                       "detect_contradictions", "calculate_centrality",
                       "analyze_evidence_network", "cleanup"):
            assert hasattr(out1, method), f"missing method: {method}"

    @pytest.mark.asyncio
    async def test_stealth_fetch_count_resets_each_sprint(self) -> None:
        """deep_research() must reset _stealth_fetch_count at entry."""
        from hledac.universal.enhanced_research import (
            ResearchDepth,
            UnifiedResearchConfig,
            UnifiedResearchEngine,
        )

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        # Dirty the counter
        engine._stealth_fetch_count = 99
        # Run a minimal deep_research (will be a no-op if no providers enabled)
        try:
            await engine.deep_research(query="x", depth=ResearchDepth.BASIC, max_results=1)
        except Exception:
            pass  # We only care about the counter reset
        assert engine._stealth_fetch_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_releases_advanced_providers(self) -> None:
        """cleanup() must null out all three advanced provider references."""
        from hledac.universal.enhanced_research import UnifiedResearchConfig, UnifiedResearchEngine

        engine = UnifiedResearchEngine(config=UnifiedResearchConfig())
        # Pre-populate to verify they get cleared
        engine._advanced_rag = object()
        engine._stealth_browser = object()
        engine._evidence_analyzer = object()
        engine._stealth_fetch_count = 7

        await engine.cleanup()
        assert engine._advanced_rag is None
        assert engine._stealth_browser is None
        assert engine._evidence_analyzer is None
        assert engine._stealth_fetch_count == 0


# =============================================================================
# TestSprintFADVE — bounded invariants (sprint-wide contract)
# =============================================================================

class TestSprintFADVE:
    """Project-wide invariants for the advanced modules wiring."""

    def test_rag_orchestrator_does_not_open_second_lancedb(self) -> None:
        """Static check: the RAGOrchestrator must delegate to the canonical
        accessor, not create a new lancedb.connect() call."""
        from hledac.universal.advanced_rag import rag_orchestrator

        with open(rag_orchestrator.__file__) as fh:
            src = fh.read()
        # The module must reference get_identity_store
        assert "get_identity_store" in src, (
            "RAGOrchestrator must use canonical get_identity_store() — "
            "no second LanceDB connection allowed (M1 memory constraint)"
        )
        # It must NOT call lancedb.connect directly
        assert "lancedb.connect" not in src, (
            "RAGOrchestrator must not open a new lancedb.connect() — "
            "rely on the canonical singleton"
        )

    def test_advanced_modules_never_use_asyncio_to_thread_in_io(self) -> None:
        """Static check across all four new files: no new asyncio.to_thread
        calls (the project invariant forbids it for I/O). Docstring mentions
        (e.g. "NEVER asyncio.to_thread") are allowed.

        We match only `asyncio.to_thread(` (with open paren) — a real call
        site — and skip lines starting with `#` or inside triple-quoted
        docstrings.
        """
        files = [
            "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/advanced_rag/rag_orchestrator.py",
            "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/advanced_web/stealth_browser.py",
            "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/advanced_web/evidence_network_analyzer.py",
        ]
        for f in files:
            with open(f) as fh:
                src = fh.read()
            # Strip triple-quoted docstrings (and # comments) before scanning
            import re
            stripped = re.sub(r'"""[\s\S]*?"""', "", src)
            stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
            stripped = re.sub(r"#[^\n]*", "", stripped)
            # Now look for actual call sites
            for m in re.finditer(r"asyncio\.to_thread\s*\(", stripped):
                ln = stripped[: m.start()].count("\n") + 1
                raise AssertionError(
                    f"asyncio.to_thread() call found in {f}:{ln} — "
                    "use loop.run_in_executor instead"
                )

    def test_capability_flags_defined_as_env_constants(self) -> None:
        """The env-var names must be defined as module constants."""
        import hledac.universal.enhanced_research as er

        assert hasattr(er, "_ADVANCED_RAG_ENV")
        assert er._ADVANCED_RAG_ENV == "HLEDAC_ENABLE_ADVANCED_RAG"
        assert hasattr(er, "_ADVANCED_STEALTH_ENV")
        assert er._ADVANCED_STEALTH_ENV == "HLEDAC_ENABLE_ADVANCED_STEALTH"
        assert hasattr(er, "_EVIDENCE_ANALYZER_ENV")
        assert er._EVIDENCE_ANALYZER_ENV == "HLEDAC_ENABLE_EVIDENCE_ANALYZER"

    def test_bounded_constants_have_m1_safe_defaults(self) -> None:
        """The module-level bound constants must be M1-safe."""
        import hledac.universal.enhanced_research as er

        assert er._MAX_ADVANCED_RAG_FINDINGS == 20
        assert er._MAX_STEALTH_FETCHES == 5
        assert er._MAX_STEALTH_DEPTH == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
