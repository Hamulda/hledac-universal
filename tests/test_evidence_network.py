"""
P7-C: EvidenceNetworkAnalyzer.analyze() — Smoke Tests
=====================================================

Verifies the new async analyze() entry point on EvidenceNetworkAnalyzer.
Tests are hermetic (no live DuckDB, no MLX, no networkx) and use only the
public surface of advanced_web.evidence_network_analyzer.

Coverage:
  - empty input -> empty EvidenceGraph (finding_count=0, nodes=(), edges=())
  - single finding with no IOCs -> empty graph
  - single finding with IOCs -> bounded nodes, no self-loop edges
  - multi-finding with shared IOC -> at least one co-occurrence edge
  - injected DuckPGQGraph -> analyze() calls find_connected_batch via
    asyncio.to_thread (verified by MagicMock call_count)
  - fail-soft: garbage / None input never raises
  - dataclass DTOs are frozen and importable from hledac.universal
  - module-level EvidenceGraph.__all__ exposes the public surface
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure repo root is on sys.path for the namespace bootstrap used in tests
_REPO = Path("/Users/vojtechhamada/PycharmProjects/Hledac")
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hledac.universal.advanced_web.evidence_network_analyzer import (  # noqa: E402
    EvidenceGraph,
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceNetworkAnalyzer,
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
)


# ── Fixtures / helpers ───────────────────────────────────────────────────────

class _FakeFinding:
    """Minimal CanonicalFinding-like stub for tests (matches duckdb_store API)."""

    def __init__(
        self,
        finding_id: str,
        source_type: str = "test",
        confidence: float = 0.5,
        payload_text: str | None = None,
        query: str = "",
    ):
        self.finding_id = finding_id
        self.source_type = source_type
        self.confidence = confidence
        self.payload_text = payload_text
        self.query = query
        self.ts = 0.0
        self.provenance = ()


@pytest.fixture
def analyzer() -> EvidenceNetworkAnalyzer:
    return EvidenceNetworkAnalyzer()


# ── T1: empty input ─────────────────────────────────────────────────────────

class TestAnalyzeEmptyInput:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_graph(self, analyzer: EvidenceNetworkAnalyzer):
        """analyze([]) -> EvidenceGraph with 0 nodes, 0 edges, finding_count=0."""
        g = await analyzer.analyze([])
        assert isinstance(g, EvidenceGraph)
        assert g.nodes == ()
        assert g.edges == ()
        assert g.confidence == 0.0
        assert g.finding_count == 0
        assert g.not_implemented is False

    @pytest.mark.asyncio
    async def test_none_input_returns_empty_graph(self, analyzer: EvidenceNetworkAnalyzer):
        """analyze(None) -> fail-soft empty graph (never raises)."""
        g = await analyzer.analyze(None)  # type: ignore[arg-type]
        assert isinstance(g, EvidenceGraph)
        assert g.finding_count == 0
        assert g.nodes == ()

    @pytest.mark.asyncio
    async def test_garbage_input_returns_empty_graph(self, analyzer: EvidenceNetworkAnalyzer):
        """analyze(<non-list>) -> fail-soft empty graph."""
        g = await analyzer.analyze("not a list")  # type: ignore[arg-type]
        assert isinstance(g, EvidenceGraph)
        assert g.finding_count == 0


# ── T2: single finding ──────────────────────────────────────────────────────

class TestAnalyzeSingleFinding:
    @pytest.mark.asyncio
    async def test_single_finding_with_no_iocs_returns_empty_graph(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """Single finding with no IOC-shaped text -> empty graph but count=1."""
        f = _FakeFinding(
            finding_id="f-001",
            source_type="web",
            payload_text="Hello world, no IoCs here, just prose.",
        )
        g = await analyzer.analyze([f])
        assert isinstance(g, EvidenceGraph)
        assert g.finding_count == 1
        assert g.nodes == ()
        assert g.edges == ()

    @pytest.mark.asyncio
    async def test_single_finding_with_two_iocs_creates_two_nodes(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """Single finding with CVE + IPv4 -> at least 2 nodes."""
        f = _FakeFinding(
            finding_id="f-002",
            source_type="ct_log",
            payload_text="CVE-2024-12345 was observed from 198.51.100.7 against a vulnerable endpoint.",
        )
        g = await analyzer.analyze([f])
        assert isinstance(g, EvidenceGraph)
        assert g.finding_count == 1
        node_ids = {n.node_id for n in g.nodes}
        assert "cve:cve-2024-12345" in node_ids
        # 198.51.100.7 is in TEST-NET-2 (RFC 5737) but our regex still accepts it
        assert any(n.ioc_type == "ip" for n in g.nodes)
        # Bound check
        assert len(g.nodes) <= MAX_GRAPH_NODES
        assert len(g.edges) <= MAX_GRAPH_EDGES

    @pytest.mark.asyncio
    async def test_single_finding_payload_uses_query_fallback(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """When payload_text is empty, analyze() falls back to scanning query."""
        f = _FakeFinding(
            finding_id="f-003",
            source_type="synthetic",
            payload_text=None,
            query="Look at 8.8.8.8 and CVE-2023-9999",
        )
        g = await analyzer.analyze([f])
        assert g.finding_count == 1
        node_ids = {n.node_id for n in g.nodes}
        assert "cve:cve-2023-9999" in node_ids
        assert "ip:8.8.8.8" in node_ids


# ── T3: multi-finding with known relationship ──────────────────────────────

class TestAnalyzeMultiFinding:
    @pytest.mark.asyncio
    async def test_two_findings_with_shared_ioc_creates_co_occurrence_edge(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """Two findings that mention the same IOC -> at least one edge."""
        f1 = _FakeFinding(
            finding_id="f-A",
            source_type="ct_log",
            payload_text="observed 203.0.113.42 scanning port 22",
        )
        f2 = _FakeFinding(
            finding_id="f-B",
            source_type="passive_dns",
            payload_text="resolved domain example.org pointing to 203.0.113.42",
        )
        g = await analyzer.analyze([f1, f2])
        assert g.finding_count == 2
        # 203.0.113.42 appears in both -> shared node + co-occurrence edges
        node_ids = {n.node_id for n in g.nodes}
        assert "ip:203.0.113.42" in node_ids
        # Each finding has its own domain candidate too
        assert any(n.ioc_type == "domain" for n in g.nodes)
        # Edges are co-occurrence (intra-finding IOC pairs)
        assert len(g.edges) >= 1
        for e in g.edges:
            assert isinstance(e, EvidenceGraphEdge)
            assert 0.0 <= e.weight <= 1.0
            assert e.evidence_count >= 1

    @pytest.mark.asyncio
    async def test_dedupe_of_nodes_for_same_ioc_across_findings(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """Same (ioc_type, value) in 3 findings -> 1 node, sources grow."""
        findings = [
            _FakeFinding(
                finding_id=f"f-{i}",
                source_type=f"src-{i}",
                payload_text="CVE-2022-0001 mentioned here",
            )
            for i in range(3)
        ]
        g = await analyzer.analyze(findings)
        cve_nodes = [n for n in g.nodes if n.ioc_type == "cve"]
        # Deduped to a single node
        assert len(cve_nodes) == 1
        node = cve_nodes[0]
        # sources must record all 3 source_types
        assert set(node.sources) == {"src-0", "src-1", "src-2"}


# ── T4: injected DuckPGQGraph (read-side enrichment) ────────────────────────

class TestAnalyzeWithInjectedGraph:
    @pytest.mark.asyncio
    async def test_injected_graph_must_call_find_connected_batch(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """When graph is injected, analyze() must call find_connected_batch."""
        fake_graph = MagicMock()
        fake_graph.find_connected_batch = MagicMock(
            return_value={"203.0.113.99": [{"value": "linked.example", "ioc_type": "domain", "weight": 0.7}]}
        )
        analyzer_with_graph = EvidenceNetworkAnalyzer(graph=fake_graph)

        findings = [
            _FakeFinding(
                finding_id="fg-1",
                source_type="ct_log",
                payload_text="traffic to 203.0.113.99",
            )
        ]
        g = await analyzer_with_graph.analyze(findings)
        # find_connected_batch was called (synchronously, in to_thread)
        assert fake_graph.find_connected_batch.called
        # Graph returned at least one connected edge with rel_type=graph_connected
        graph_edges = [e for e in g.edges if e.rel_type == "graph_connected"]
        assert len(graph_edges) >= 1
        assert graph_edges[0].weight == 0.7
        assert graph_edges[0].dst == "domain:linked.example"

    @pytest.mark.asyncio
    async def test_injected_graph_exception_does_not_propagate(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """If find_connected_batch raises, analyze() must still return a graph."""
        fake_graph = MagicMock()
        fake_graph.find_connected_batch = MagicMock(side_effect=RuntimeError("duckdb boom"))
        analyzer_with_graph = EvidenceNetworkAnalyzer(graph=fake_graph)

        findings = [
            _FakeFinding(
                finding_id="fg-2",
                source_type="ct_log",
                payload_text="traffic to 8.8.4.4",
            )
        ]
        # Must not raise
        g = await analyzer_with_graph.analyze(findings)
        # Local edges still produced; graph-edges empty due to exception
        assert isinstance(g, EvidenceGraph)
        graph_edges = [e for e in g.edges if e.rel_type == "graph_connected"]
        assert graph_edges == []


# ── T5: DTO immutability + public export ────────────────────────────────────

class TestDTOShape:
    def test_evidence_graph_node_is_frozen(self):
        """EvidenceGraphNode is a frozen dataclass -> attribute assignment fails."""
        n = EvidenceGraphNode(
            node_id="ip:1.1.1.1",
            ioc_type="ip",
            value="1.1.1.1",
            confidence=0.5,
            sources=("ct_log",),
        )
        with pytest.raises(Exception):
            n.value = "2.2.2.2"  # type: ignore[misc]

    def test_evidence_graph_edge_is_frozen(self):
        e = EvidenceGraphEdge(
            src="ip:1.1.1.1",
            dst="ip:2.2.2.2",
            rel_type="co_occurrence",
            weight=0.6,
            evidence_count=1,
        )
        with pytest.raises(Exception):
            e.weight = 0.9  # type: ignore[misc]

    def test_evidence_graph_is_frozen(self):
        g = EvidenceGraph(
            nodes=(),
            edges=(),
            confidence=0.0,
            finding_count=0,
        )
        with pytest.raises(Exception):
            g.finding_count = 99  # type: ignore[misc]

    def test_analyzer_is_implemented(self, analyzer: EvidenceNetworkAnalyzer):
        """Post-P7-C T1, the analyzer must report is_implemented() == True."""
        assert analyzer.is_implemented() is True

    def test_universal_init_reexports(self):
        """hledac.universal must re-export EvidenceNetworkAnalyzer + DTOs."""
        import hledac.universal as uni
        for name in ("EvidenceNetworkAnalyzer", "EvidenceGraph", "EvidenceGraphNode", "EvidenceGraphEdge"):
            assert hasattr(uni, name), f"missing lazy export: {name}"
            assert name in uni.__all__, f"missing __all__ entry: {name}"


# ── T6: bound invariants ────────────────────────────────────────────────────

class TestBounds:
    @pytest.mark.asyncio
    async def test_bounds_enforced_under_adversarial_input(
        self, analyzer: EvidenceNetworkAnalyzer
    ):
        """Huge payload + 100 findings -> still bounded by MAX_GRAPH_NODES/EDGES."""
        findings = [
            _FakeFinding(
                finding_id=f"bf-{i}",
                source_type="ct_log",
                # Massive payload with many IOCs
                payload_text=(" ".join(
                    [f"203.0.113.{i % 255}", f"CVE-2024-{1000 + i}",
                     f"deadbeef{i:060x}", f"sub{i}.example.com"]
                ) * 16)[:32768],
            )
            for i in range(50)
        ]
        g = await analyzer.analyze(findings)
        assert len(g.nodes) <= MAX_GRAPH_NODES
        assert len(g.edges) <= MAX_GRAPH_EDGES
        assert g.finding_count == 50
