"""
Probe tests for DuckDB FTS Store — Issue #11.

Tests:
- DuckDB FTS schema creation
- Arrow zero-copy ingest
- FTS5 search + BM25 ranking
- RRF fusion s LanceDB mock
- M1 8GB memory bounds
- Health check
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from hledac.universal.discovery.duckdb_fts_store import (
    BM25_K,
    DuckDBFTSStore,
    FTSDocument,
    FTSSearchResult,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def fts_store(tmp_path: Path) -> DuckDBFTSStore:
    """Clean FTS store per test."""
    store = DuckDBFTSStore(db_path=tmp_path / "test_fts.duckdb")
    return store


@pytest.fixture
async def initialized_fts(fts_store: DuckDBFTSStore) -> DuckDBFTSStore:
    """Initialized FTS store."""
    await fts_store.initialize()
    return fts_store


# --------------------------------------------------------------------------- #
# Schema tests
# --------------------------------------------------------------------------- #

class TestFTSStoreSchema:
    """FTS schema creation and extension loading."""

    @pytest.mark.asyncio
    async def test_initialize_creates_schema(self, fts_store: DuckDBFTSStore) -> None:
        """initialize() vytvori FTS5 virtual table + metadata tabulku."""
        await fts_store.initialize()
        assert fts_store._initialized is True
        assert fts_store._conn is not None

        # Verify DuckDB table exists
        result = fts_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='doc_bm25';"
        ).fetchall()
        table_names = [r[0] for r in result]
        assert "doc_bm25" in table_names

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, fts_store: DuckDBFTSStore) -> None:
        """initialize() je idempotentni — druhe volani nehazi."""
        await fts_store.initialize()
        await fts_store.initialize()  # no-op
        assert fts_store._initialized is True

    @pytest.mark.asyncio
    async def test_close(self, initialized_fts: DuckDBFTSStore) -> None:
        """close() uzavre connection."""
        await initialized_fts.close()
        assert initialized_fts._conn is None
        assert initialized_fts._initialized is False


# --------------------------------------------------------------------------- #
# Ingest tests — Arrow zero-copy
# --------------------------------------------------------------------------- #

class TestFTSIngest:
    """Arrow zero-copy ingest pres DuckDB register() + INSERT...SELECT."""

    @pytest.mark.asyncio
    async def test_upsert_empty_list(self, initialized_fts: DuckDBFTSStore) -> None:
        """upsert([]) vraci 0 a nic neudela."""
        count = await initialized_fts.upsert([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_upsert_single_document(self, initialized_fts: DuckDBFTSStore) -> None:
        """Upsert jednoho dokumentu pres Arrow."""
        doc = FTSDocument(
            doc_id="doc1",
            title="Certificate Transparency Log",
            body="example.com certificate issued by Let's Encrypt",
            source="crtsh",
            url="https://crt.sh/?id=123",
        )
        count = await initialized_fts.upsert([doc])
        assert count == 1
        assert await initialized_fts.count() == 1

    @pytest.mark.asyncio
    async def test_upsert_batch(self, initialized_fts: DuckDBFTSStore) -> None:
        """Upsert 100 dokumentu v batchi."""
        docs = [
            FTSDocument(
                doc_id=f"doc{i}",
                title=f"Document {i}",
                body=f"Content for document {i} with searchable terms",
                source="test",
            )
            for i in range(100)
        ]
        count = await initialized_fts.upsert(docs)
        assert count == 100
        assert await initialized_fts.count() == 100

    @pytest.mark.asyncio
    async def test_upsert_duplicate_replace(self, initialized_fts: DuckDBFTSStore) -> None:
        """Upsert stejneho doc_id prepisuje existujici dokument."""
        doc1 = FTSDocument(doc_id="doc1", title="Original", body="Body 1")
        doc2 = FTSDocument(doc_id="doc1", title="Updated", body="Body 2")
        await initialized_fts.upsert([doc1])
        await initialized_fts.upsert([doc2])
        assert await initialized_fts.count() == 1
        results = await initialized_fts.search("Original")
        assert len(results) == 0
        results = await initialized_fts.search("Updated")
        assert len(results) == 1
        assert results[0].title == "Updated"

    @pytest.mark.asyncio
    async def test_upsert_large_batch_m1_safe(self, initialized_fts: DuckDBFTSStore) -> None:
        """Velky batch (5000) — M1 8GB memory-safe s MAX_BATCH_SIZE limit."""
        docs = [
            FTSDocument(
                doc_id=f"doc{i}",
                title=f"Big doc {i}",
                body="X" * 500,  # 500 chars body
                source="stress",
            )
            for i in range(5000)
        ]
        count = await initialized_fts.upsert(docs)
        assert count == 5000
        assert await initialized_fts.count() == 5000


# --------------------------------------------------------------------------- #
# FTS5 search + BM25
# --------------------------------------------------------------------------- #

class TestFTSSearch:
    """FTS5 search s BM25 ranking."""

    async def _seed_crtsh_docs(self, store: DuckDBFTSStore) -> None:
        """Seed CRTSH-style certifikatni dokumenty."""
        docs = [
            FTSDocument(
                doc_id="cert1",
                title="example.com certificate",
                body="Subject: example.com, Issuer: Let's Encrypt CA, SAN: example.com www.example.com",
                source="crtsh",
                url="https://crt.sh/?id=1",
            ),
            FTSDocument(
                doc_id="cert2",
                title="test.org certificate",
                body="Subject: test.org, Issuer: DigiCert, SAN: test.org",
                source="crtsh",
                url="https://crt.sh/?id=2",
            ),
            FTSDocument(
                doc_id="cert3",
                title="example.net certificate",
                body="Subject: example.net, Issuer: Let's Encrypt, SAN: example.net api.example.net",
                source="crtsh",
                url="https://crt.sh/?id=3",
            ),
            FTSDocument(
                doc_id="doc_gopher",
                title="Gopher menu — old resources",
                body="Gopher gateway to university archives. Select 1 for Computer Science, 2 for Mathematics.",
                source="gopher",
            ),
        ]
        await store.upsert(docs)

    @pytest.mark.asyncio
    async def test_search_exact_term(self, initialized_fts: DuckDBFTSStore) -> None:
        """FTS5 hleda presny term — example.com vs example.net."""
        await self._seed_crtsh_docs(initialized_fts)
        results = await initialized_fts.search("example.com")
        assert len(results) >= 1
        assert any(r.doc_id == "cert1" for r in results)

    @pytest.mark.asyncio
    async def test_search_excludes_non_matching(self, initialized_fts: DuckDBFTSStore) -> None:
        """Term example.com nevrati cert3 (example.net)."""
        await self._seed_crtsh_docs(initialized_fts)
        results = await initialized_fts.search("example.com")
        doc_ids = [r.doc_id for r in results]
        assert "cert3" not in doc_ids

    @pytest.mark.asyncio
    async def test_search_gopher(self, initialized_fts: DuckDBFTSStore) -> None:
        """Gopher content — nalezne pres Gopher query."""
        await self._seed_crtsh_docs(initialized_fts)
        results = await initialized_fts.search("Gopher")
        assert len(results) >= 1
        assert results[0].source == "gopher"

    @pytest.mark.asyncio
    async def test_search_source_filter(self, initialized_fts: DuckDBFTSStore) -> None:
        """source_filter omeji vysledky na specificky provider."""
        await self._seed_crtsh_docs(initialized_fts)
        results = await initialized_fts.search("certificate", source_filter="gopher")
        assert all(r.source == "gopher" for r in results)

    @pytest.mark.asyncio
    async def test_search_top_k_limit(self, initialized_fts: DuckDBFTSStore) -> None:
        """top_k limituje pocet vysledku."""
        docs = [
            FTSDocument(
                doc_id=f"doc{i}",
                title=f"Document {i}",
                body="Searchable content with keyword",
                source="test",
            )
            for i in range(50)
        ]
        await initialized_fts.upsert(docs)
        results = await initialized_fts.search("keyword", top_k=10)
        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_search_empty_query(self, initialized_fts: DuckDBFTSStore) -> None:
        """Prazdny query vraci prazdny seznam."""
        results = await initialized_fts.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_results(self, initialized_fts: DuckDBFTSStore) -> None:
        """Hledani neexistujiciho terminu vraci prazdny seznam."""
        await initialized_fts.upsert(
            [FTSDocument(doc_id="d1", title="Foo", body="Bar")]
        )
        results = await initialized_fts.search("nonexistent_term_xyz")
        assert results == []


# --------------------------------------------------------------------------- #
# RRF Fusion
# --------------------------------------------------------------------------- #

class TestRRFFusion:
    """Reciprocal Rank Fusion — FTS + ANN hybrid scoring."""

    def test_rrf_fuse_empty(self) -> None:
        """Prazdne vstupy vraci prazdny seznam."""
        result = DuckDBFTSStore.rrf_fuse([], [])
        assert result == []

    def test_rrf_fuse_fts_only(self) -> None:
        """FTS-only vstup vraci FTS results s match_type=fts."""
        fts = [
            FTSSearchResult(
                doc_id="d1", title="T1", body_snippet="S1",
                url=None, source="c", rank=1.0, fetched_at=0.0,
            ),
            FTSSearchResult(
                doc_id="d2", title="T2", body_snippet="S2",
                url=None, source="c", rank=0.8, fetched_at=0.0,
            ),
        ]
        result = DuckDBFTSStore.rrf_fuse(fts, [], top_k=5)
        assert len(result) == 2
        assert result[0]["match_type"] == "fts"
        assert result[0]["doc_id"] == "d1"

    def test_rrf_fuse_ann_only(self) -> None:
        """ANN-only vstup vraci ANN results s match_type=ann."""
        ann = [
            {"doc_id": "a1", "title": "A1", "snippet": "S1", "source": "ann"},
            {"doc_id": "a2", "title": "A2", "snippet": "S2", "source": "ann"},
        ]
        result = DuckDBFTSStore.rrf_fuse([], ann, top_k=5)
        assert len(result) == 2
        assert result[0]["match_type"] == "ann"

    def test_rrf_fuse_both_weights(self) -> None:
        """RRF kombinuje FTS (0.7) + ANN (0.3) s k=60."""
        fts = [
            FTSSearchResult(
                doc_id="d1", title="T1", body_snippet="S1",
                url=None, source="c", rank=1.0, fetched_at=0.0,
            ),
        ]
        ann = [
            {"doc_id": "a1", "title": "A1", "snippet": "S1", "source": "ann"},
        ]
        result = DuckDBFTSStore.rrf_fuse(fts, ann, top_k=5, k=BM25_K)
        assert len(result) == 2
        # d1: FTS rank 0 → 0.7/(60+0)=0.01148; a1: ANN rank 0 → 0.3/(60+0)=0.00492
        # d1 ma vyssi score
        assert result[0]["doc_id"] == "d1"
        assert result[0]["match_type"] == "fts"

    def test_rrf_fuse_top_k(self) -> None:
        """top_k omeji vystup."""
        fts = [
            FTSSearchResult(
                doc_id=f"d{i}", title=f"T{i}", body_snippet=f"S{i}",
                url=None, source="c", rank=1.0 - i * 0.1, fetched_at=0.0,
            )
            for i in range(10)
        ]
        result = DuckDBFTSStore.rrf_fuse(fts, [], top_k=3)
        assert len(result) == 3


# --------------------------------------------------------------------------- #
# Health check + count
# --------------------------------------------------------------------------- #

class TestFTSHealth:
    """Health check a utility methods."""

    @pytest.mark.asyncio
    async def test_count_empty(self, initialized_fts: DuckDBFTSStore) -> None:
        """Prazdny store vraci 0."""
        assert await initialized_fts.count() == 0

    @pytest.mark.asyncio
    async def test_count_after_upsert(self, initialized_fts: DuckDBFTSStore) -> None:
        """count() vraci spravny pocet."""
        docs = [
            FTSDocument(doc_id=f"d{i}", title=f"T{i}", body="B")
            for i in range(7)
        ]
        await initialized_fts.upsert(docs)
        assert await initialized_fts.count() == 7

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, initialized_fts: DuckDBFTSStore) -> None:
        """health_check() vraci healthy stav."""
        await initialized_fts.upsert(
            [FTSDocument(doc_id="d1", title="T", body="B")]
        )
        health = await initialized_fts.health_check()
        assert health["status"] == "healthy"
        assert health["doc_count"] == 1
        assert "size_mb" in health

    @pytest.mark.asyncio
    async def test_delete(self, initialized_fts: DuckDBFTSStore) -> None:
        """delete() odstrani dokumenty z FTS indexu."""
        await initialized_fts.upsert(
            [FTSDocument(doc_id="d1", title="T1", body="B1")]
        )
        deleted = await initialized_fts.delete(["d1"])
        assert deleted == 1
        assert await initialized_fts.count() == 0


# --------------------------------------------------------------------------- #
# Async iterator
# --------------------------------------------------------------------------- #

class TestFTSIterator:
    """aiter_all() async iterator pres vsechny dokumenty."""

    @pytest.mark.asyncio
    async def test_aiter_empty(self, initialized_fts: DuckDBFTSStore) -> None:
        """Prazdny store — zadny item."""
        docs = [d async for d in initialized_fts.aiter_all()]
        assert docs == []

    @pytest.mark.asyncio
    async def test_aiter_all_docs(self, initialized_fts: DuckDBFTSStore) -> None:
        """Vsechny dokumenty jsou vraceny pres iterator."""
        docs = [
            FTSDocument(doc_id=f"d{i}", title=f"T{i}", body=f"B{i}", source="test")
            for i in range(25)
        ]
        await initialized_fts.upsert(docs)
        recovered = [d async for d in initialized_fts.aiter_all()]
        assert len(recovered) == 25
        assert all(d.source == "test" for d in recovered)
