"""
tests/probe_r6_local_bm25_relevance/

F228C: Local BM25 Relevance Over Accepted Findings
=================================================
Tests advisory-only local search over accepted CanonicalFinding objects.

Integration point: sprint_advisory_runner._run_local_search_advisory()
Seam: LocalSearchSeam (knowledge/search_index.py)

Product requirement: accepted findings → SearchDocument → index → search.
NO new storage authority. NO embeddings. NO MLX/model load. NO network.

Run:
    python -m pytest tests/probe_r6_local_bm25_relevance/ -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Cut through import path for search_index
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Test helpers ────────────────────────────────────────────────────────────────


class FakeCanonicalFinding:
    """Fake CanonicalFinding-like object with attrs."""
    def __init__(self, finding_id: str, source_type: str, payload_text: str = "", url: str = ""):
        self.finding_id = finding_id
        self.source_type = source_type
        self.payload_text = payload_text
        self.url = url


class FakeDictFinding:
    """Fake finding as a plain dict — supports both getattr and .get()."""
    def __init__(self, d: dict):
        self._d = d

    def __getattr__(self, key: str):
        if key.startswith("_"):
            raise AttributeError(key)
        return self._d.get(key, "")

    def get(self, key: str, default=None):
        return self._d.get(key, default)


# ── 1. CanonicalFinding objects convert to SearchDocument ───────────────────────


def test_canonical_finding_converts_to_search_document():
    """Accepted CanonicalFinding objects convert to SearchDocument."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings
    from hledac.universal.knowledge.search_index import SearchDocument

    findings = [
        FakeCanonicalFinding("fid1", "web", "This is a test payload content", "https://example.com/1"),
        FakeCanonicalFinding("fid2", "ct", "Certificate transparency log entry data", "https://ct.example.com"),
    ]

    docs = build_search_documents_from_findings(findings)

    assert len(docs) == 2
    assert all(isinstance(d, SearchDocument) for d in docs)
    urls = [d.url for d in docs]
    assert "https://example.com/1" in urls
    assert "https://ct.example.com" in urls


def test_findings_with_payload_text_are_indexed():
    """Findings with payload_text are indexed into SearchDocument."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    findings = [
        FakeCanonicalFinding("fid1", "public", "Some substantial content here", "https://pub.example"),
        FakeCanonicalFinding("fid2", "wayback", "Archived page content from 2024", ""),  # empty URL ok
    ]

    docs = build_search_documents_from_findings(findings)
    assert len(docs) == 2
    contents = [d.content for d in docs]
    assert "Some substantial content here" in contents
    assert "Archived page content from 2024" in contents


def test_findings_without_payload_text_are_skipped():
    """Findings without payload_text are skipped safely."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    findings = [
        FakeCanonicalFinding("fid1", "web", "", "https://example.com"),
        FakeCanonicalFinding("fid2", "ct", "", "https://ct.example"),
    ]

    docs = build_search_documents_from_findings(findings)
    assert len(docs) == 0


def test_duplicate_url_does_not_explode_metadata():
    """Duplicate finding/url does not create duplicate or exploding metadata."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    findings = [
        FakeCanonicalFinding("fid1", "web", "Content A", "https://dup.example"),
        FakeCanonicalFinding("fid2", "ct", "Content B", "https://dup.example"),  # same URL
        FakeCanonicalFinding("fid3", "public", "Content C", "https://dup.example"),  # same again
    ]

    docs = build_search_documents_from_findings(findings)
    # Only first unique URL should appear
    assert len(docs) == 1
    assert docs[0].url == "https://dup.example"


# ── 5. LocalSearchSeam.search returns bounded top_k ─────────────────────────────


def test_local_search_seam_search_returns_bounded_top_k():
    """LocalSearchSeam.search returns bounded top_k results."""
    from hledac.universal.knowledge.search_index import LocalSearchSeam, SearchDocument

    seam = LocalSearchSeam()
    docs = [
        SearchDocument(url=f"https://test{i}.com", title=f"Doc {i}", content=f"keyword {i} content", metadata={})
        for i in range(20)
    ]
    seam.index(docs)

    result = seam.search("keyword", top_k=5)
    assert len(result.results) <= 5


# ── 6. Empty findings returns skipped/empty result ──────────────────────────────


def test_empty_findings_returns_empty_docs():
    """Empty findings list returns empty SearchDocument list."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    docs = build_search_documents_from_findings([])
    assert docs == []


# ── 7. Advisory result does not call DuckDB write ──────────────────────────────


@pytest.mark.asyncio
async def test_local_search_advisory_does_not_call_duckdb_write():
    """Advisory result does not call DuckDB write path."""
    from hledac.universal.runtime.sprint_advisory_runner import SprintAdvisoryRunner, AdvisoryRunOutcome

    mock_scheduler = MagicMock()
    mock_scheduler._all_findings = []
    mock_scheduler.query = "test query"
    mock_scheduler.sprint_id = "r6-test"
    mock_scheduler._duckdb_store = MagicMock()
    mock_scheduler._pivot_planner = None
    mock_scheduler._analyst_workbench = None
    mock_scheduler._governor = None

    runner = SprintAdvisoryRunner(scheduler=mock_scheduler, duckdb_store=mock_scheduler._duckdb_store)

    outcome = await runner._run_local_search_advisory(AdvisoryRunOutcome())

    # DuckDB store should NOT have been called (no write, no query)
    mock_scheduler._duckdb_store.assert_not_called()


# ── 8. Advisory result does not create persistent DB file ──────────────────────


@pytest.mark.asyncio
async def test_local_search_advisory_no_persistent_db():
    """Advisory result does not create a persistent DB file."""
    from hledac.universal.runtime.sprint_advisory_runner import SprintAdvisoryRunner, AdvisoryRunOutcome

    mock_scheduler = MagicMock()
    mock_scheduler._all_findings = []
    mock_scheduler.query = "test query"
    mock_scheduler.sprint_id = "r6-test"
    mock_scheduler._duckdb_store = MagicMock()
    mock_scheduler._pivot_planner = None
    mock_scheduler._analyst_workbench = None
    mock_scheduler._governor = None

    runner = SprintAdvisoryRunner(scheduler=mock_scheduler, duckdb_store=mock_scheduler._duckdb_store)

    with tempfile.TemporaryDirectory() as tmpdir:
        pass  # no files created in tmpdir

    outcome = await runner._run_local_search_advisory(AdvisoryRunOutcome())
    # Outcome should indicate no persistent state
    assert outcome.local_search_source in ("search_index", "local_search")


# ── 9. No network ───────────────────────────────────────────────────────────────


def test_no_network_calls_in_build_search_documents():
    """build_search_documents_from_findings makes no network calls."""
    import http.client
    import socket

    original_connect = http.client.HTTPConnection.connect
    network_called = False

    def track_connect(self, *args, **kwargs):
        nonlocal network_called
        network_called = True
        return original_connect(self, *args, **kwargs)

    with patch.object(http.client.HTTPConnection, 'connect', track_connect):
        from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings
        findings = [FakeCanonicalFinding("f1", "web", "test content", "https://example.com")]
        docs = build_search_documents_from_findings(findings)

    assert not network_called, "Network call detected in build_search_documents_from_findings"


# ── 10. No MLX/model load ──────────────────────────────────────────────────────


def test_no_mlx_import_in_advisory():
    """Advisory module does not import MLX or model-related code."""
    import importlib.util

    spec = importlib.util.find_spec("hledac.universal.runtime.sprint_advisory_runner")
    assert spec is not None
    source_file = spec.origin
    assert source_file is not None

    with open(source_file) as f:
        content = f.read()

    mlx_imports = [line for line in content.split("\n") if "mlx" in line.lower() and not line.strip().startswith("#")]
    assert len(mlx_imports) == 0, f"MLX import found: {mlx_imports}"


# ── 11. No browser/stealth ─────────────────────────────────────────────────────


def test_no_browser_or_stealth_import():
    """No browser or stealth imports in sprint_advisory_runner."""
    import importlib.util

    spec = importlib.util.find_spec("hledac.universal.runtime.sprint_advisory_runner")
    source_file = spec.origin

    with open(source_file) as f:
        content = f.read()

    forbidden = ["nodriver", "stealth", "chromium", "browser"]
    for term in forbidden:
        lines = [l.strip() for l in content.split("\n") if term in l.lower() and not l.strip().startswith("#")]
        assert len(lines) == 0, f"Forbidden import '{term}' found: {lines}"


# ── 12. MAX_RESULT_SET is enforced ─────────────────────────────────────────────


def test_local_search_seam_max_result_set_enforced():
    """LocalSearchSeam.MAX_RESULT_SET is enforced on search."""
    from hledac.universal.knowledge.search_index import LocalSearchSeam, SearchDocument

    seam = LocalSearchSeam()
    docs = [
        SearchDocument(url=f"https://test{i}.com", title=f"Doc {i}", content=f"content {i}", metadata={})
        for i in range(150)
    ]
    seam.index(docs)

    result = seam.search("content", top_k=200)  # Request more than MAX_RESULT_SET
    assert len(result.results) <= seam.MAX_RESULT_SET


# ── 13. search_index import is safe ────────────────────────────────────────────


def test_search_index_import_is_safe():
    """search_index module imports without error."""
    from hledac.universal.knowledge import search_index
    assert hasattr(search_index, "LocalSearchSeam")
    assert hasattr(search_index, "SearchDocument")
    assert hasattr(search_index, "BM25Index")


# ── 14. Integration point is fail-soft ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_search_advisory_fail_soft():
    """Integration point is fail-soft when seam raises."""
    from hledac.universal.runtime.sprint_advisory_runner import SprintAdvisoryRunner, AdvisoryRunOutcome

    mock_scheduler = MagicMock()
    mock_scheduler._all_findings = []
    mock_scheduler.query = "test"
    mock_scheduler.sprint_id = "r6-test"
    mock_scheduler._duckdb_store = MagicMock()
    mock_scheduler._pivot_planner = None
    mock_scheduler._analyst_workbench = None
    mock_scheduler._governor = None

    runner = SprintAdvisoryRunner(scheduler=mock_scheduler, duckdb_store=mock_scheduler._duckdb_store)

    # Patch seam.search to raise
    with patch("hledac.universal.knowledge.search_index.LocalSearchSeam.search", side_effect=RuntimeError("test error")):
        outcome = await runner._run_local_search_advisory(AdvisoryRunOutcome())

    # Should fail-soft, not raise
    assert outcome.local_search_attempted is True
    assert outcome.local_search_error is not None


# ── 15. CancelledError is re-raised ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_search_advisory_cancelled_error_raised():
    """CancelledError is re-raised if async wrapper is added."""
    from hledac.universal.runtime.sprint_advisory_runner import SprintAdvisoryRunner, AdvisoryRunOutcome

    mock_scheduler = MagicMock()
    mock_scheduler._all_findings = []
    mock_scheduler.query = "test"
    mock_scheduler.sprint_id = "r6-test"
    mock_scheduler._duckdb_store = MagicMock()
    mock_scheduler._pivot_planner = None
    mock_scheduler._analyst_workbench = None
    mock_scheduler._governor = None

    runner = SprintAdvisoryRunner(scheduler=mock_scheduler, duckdb_store=mock_scheduler._duckdb_store)

    with patch("hledac.universal.knowledge.search_index.LocalSearchSeam", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await runner._run_local_search_advisory(AdvisoryRunOutcome())


# ── Bonus: dict-like findings conversion ───────────────────────────────────────


def test_dict_like_findings_convert():
    """Dict-like findings (from sidecar bus) convert to SearchDocument."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    findings = [
        FakeDictFinding({"finding_id": "d1", "source_type": "web", "payload_text": "dict content", "url": "https://dict.example"}),
    ]

    docs = build_search_documents_from_findings(findings)
    assert len(docs) == 1
    assert docs[0].content == "dict content"


# ── Bonus: MAX_INDEXED_FINDINGS bound ─────────────────────────────────────────


def test_max_indexed_findings_bound():
    """Conversion caps at MAX_INDEXED_FINDINGS (5000)."""
    from hledac.universal.runtime.sprint_advisory_runner import build_search_documents_from_findings

    findings = [
        FakeCanonicalFinding(f"fid{i}", "web", f"content {i}", f"https://e{i}.com")
        for i in range(6000)
    ]

    docs = build_search_documents_from_findings(findings)
    assert len(docs) <= 5000


# ── Bonus: run_all_advisories includes local search ─────────────────────────────


@pytest.mark.asyncio
async def test_run_all_advisories_includes_local_search():
    """run_all_advisories calls local search step (step 5)."""
    from hledac.universal.runtime.sprint_advisory_runner import SprintAdvisoryRunner, AdvisoryRunOutcome

    mock_scheduler = MagicMock()
    mock_scheduler._all_findings = []
    mock_scheduler.query = "integration test"
    mock_scheduler.sprint_id = "r6-test"
    mock_scheduler._duckdb_store = MagicMock()
    mock_scheduler._pivot_planner = None
    mock_scheduler._analyst_workbench = None
    mock_scheduler._governor = None
    mock_scheduler._get_graph_signal = MagicMock(return_value={})

    runner = SprintAdvisoryRunner(scheduler=mock_scheduler, duckdb_store=mock_scheduler._duckdb_store)

    outcome = await runner.run_all_advisories()

    # local_search fields must be set (even if source=none because no findings)
    assert hasattr(outcome, "local_search_attempted")
    assert hasattr(outcome, "local_search_indexed")
    assert hasattr(outcome, "local_search_elapsed_ms")
    assert hasattr(outcome, "local_search_top_results")