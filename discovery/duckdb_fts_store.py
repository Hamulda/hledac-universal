"""
DuckDB FTS Store — Issue #11.
Full-text index pro discovery/ dokument data.

Pouziva rank_bm25 (pure Python, v deps) + DuckDB pro structured storage.
M1 8GB safe. Zero-copy Arrow ingest.

BM25 je v `rank-bm25>=0.2.2` (deps). Schema:
    DuckDB table: doc_bm25(doc_id PK, title, body, source, url, fetched_at, metadata_json)
    In-memory BM25 index (rank_bm25.BM25Okapi) per source provider

Canonical write path:
    1. upsert do DuckDB pres Arrow zero-copy
    2. BM25 index rebuilt on dirty flag before search

Search:
    BM25 scoring pres rank_bm25.BM25Okapi → ranked results
    Hybrid: RRF fusion s LanceDB ANN pres rrf_fuse()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
from polars import DataFrame
from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_FTS_DB = "discovery_fts.duckdb"
MAX_BATCH_SIZE = 5000  # per Arrow batch (M1 8GB RAM-safe)
BM25_K = 60  # RRF k-param for hybrid FTS+ANN scoring

# Issue #25: WAL write-ahead log for incremental BM25 index updates
# Path: ~/.cache/hledac/<source_hash>.wal
# WAL entry: JSON lines — {"op": "upsert"|"delete", "doc": {...}}
_WAL_SUFFIX = ".wal"
_WAL_MAX_SIZE_MB = 64  # WAL auto-flush threshold (M1 8GB safe)

# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class FTSDocument:
    """Jeden dokument k indexaci."""
    doc_id: str
    title: str = ""
    body: str = ""
    url: str | None = None
    source: str = ""  # provider: crtsh, gopher, academic, wayback
    fetched_at: float = field(default_factory=time.time)
    metadata_json: str = "{}"


@dataclass
class FTSSearchResult:
    """Jeden vysledek FTS dotazu."""
    doc_id: str
    title: str
    body_snippet: str
    url: str | None
    source: str
    rank: float  # BM25 score
    fetched_at: float


# --------------------------------------------------------------------------- #
# DuckDBFTSStore
# --------------------------------------------------------------------------- #

class DuckDBFTSStore:
    """
    Full-text index store s BM25 ranking.

    Inicializace:
        store = DuckDBFTSStore(path="data/discovery_fts.duckdb")
        await store.initialize()

    Indexace dokumentu:
        await store.upsert([doc1, doc2, ...])

    Vyhledavani:
        results = await store.search("query terms", top_k=20)

    Hybrid scoring s LanceDB:
        fts_results = await store.search(query, top_k=100)
        ann_results = await lancedb_store.search_similar(embedding, top_k=100)
        combined = DuckDBFTSStore.rrf_fuse(fts_results, ann_results, top_k=30, k=BM25_K)

    M1 8GB: BM25 index v pameti (typicky <100MB pro 100k dokumentu).

    Issue #25: WAL write-ahead log — discovery indexace je inkrementální.
    WAL file: ~/.cache/hledac/<source_hash>.wal (JSONL, append-only).
    Index worker appenduje do WAL, konsolidace probíhá periodicky nebo při startu.
    Přežívá crash: při restartu se WAL přečte a aplikuje před rebuild.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        max_batch_size: int = MAX_BATCH_SIZE,
        readonly: bool = False,
    ) -> None:
        if db_path is None:
            db_path = DEFAULT_FTS_DB
        self._db_path = Path(db_path)
        self._max_batch_size = max_batch_size
        self._readonly = readonly
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

        # BM25 index per source — rebuilt lazily on dirty flag
        self._bm25_index: dict[str, BM25Okapi | None] = {}  # None = needs rebuild
        self._bm25_doc_ids: dict[str, list[str]] = {}
        self._bm25_combined: dict[str, list[str]] = {}  # source -> combined texts (for get_top_n)
        self._bm25_dirty = False

        # Issue #25: WAL write-ahead log for incremental indexing
        self._wal_path: Path | None = None
        self._wal_dirty = False  # WAL has unflushed entries
        self._wal_flush_size = int(os.environ.get("HLEDAC_FTS_WAL_MAX_MB", str(_WAL_MAX_SIZE_MB))) * 1024 * 1024

    # --------------------------------------------------------------------------- #
    # Lifecycle
    # --------------------------------------------------------------------------- #

    async def initialize(self) -> None:
        """Inicializuje DuckDB schema + nacte existujici BM25 indexy."""
        async with self._lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self._db_path), read_only=self._readonly)
            self._ensure_schema()
            # Issue #25: WAL init — konsolidace pred rebuild
            self._init_wal()
            if self._wal_path and self._wal_path.exists():
                self._consolidate_wal()
            self._rebuild_bm25_index()
            self._initialized = True
            logger.info("DuckDBFTSStore initialized: path=%s", self._db_path)

    def _ensure_schema(self) -> None:
        """Vytvori DuckDB tabulku pro dokumenty."""
        assert self._conn is not None
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_bm25 (
                doc_id        TEXT PRIMARY KEY,
                title         TEXT NOT NULL DEFAULT '',
                body          TEXT NOT NULL DEFAULT '',
                source        TEXT NOT NULL DEFAULT '',
                url           TEXT,
                fetched_at    DOUBLE NOT NULL DEFAULT 0.0,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_bm25_source ON doc_bm25(source);
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_bm25_fetched ON doc_bm25(fetched_at DESC);
        """)

    # --------------------------------------------------------------------------- #
    # Issue #25: WAL Write-Ahead Log
    # --------------------------------------------------------------------------- #

    def _wal_source_hash(self) -> str:
        """Stable hash for WAL file naming — derived from db_path."""
        key = str(self._db_path.absolute()).encode("utf-8")
        return hashlib.sha256(key).hexdigest()[:16]

    def _init_wal(self) -> None:
        """Inicializuje WAL cestu v cache adresari."""
        cache_dir = Path.home() / ".cache" / "hledac"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._wal_path = cache_dir / f"{self._wal_source_hash()}{_WAL_SUFFIX}"

    def _wal_append(self, op: str, doc: FTSDocument) -> None:
        """Append one WAL entry (jsonl)."""
        if self._wal_path is None:
            return
        try:
            entry = json.dumps({"op": op, "doc": {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "body": doc.body,
                "source": doc.source,
                "url": doc.url or "",
                "fetched_at": doc.fetched_at,
                "metadata_json": doc.metadata_json,
            }}, separators=(",", ":"))
            with open(self._wal_path, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
            self._wal_dirty = True
        except OSError:
            pass  # fail-soft: WAL miss ≠ data loss (DuckDB persistuje)

    def _wal_size(self) -> int:
        """Aktuální WAL velikost v bytech."""
        if self._wal_path is None or not self._wal_path.exists():
            return 0
        try:
            return self._wal_path.stat().st_size
        except OSError:
            return 0

    def _consolidate_wal(self) -> None:
        """Precte WAL, aplikuje entries na DuckDB, a vymaze WAL."""
        if self._wal_path is None or not self._wal_path.exists():
            return
        wal_path = self._wal_path
        entries: list[tuple[str, dict]] = []
        try:
            with open(wal_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        entries.append((parsed["op"], parsed["doc"]))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            return

        if not entries:
            return

        # Aplikuj na DuckDB
        for op, doc_dict in entries:
            doc = FTSDocument(
                doc_id=doc_dict["doc_id"],
                title=doc_dict.get("title", ""),
                body=doc_dict.get("body", ""),
                source=doc_dict.get("source", ""),
                url=doc_dict.get("url") or None,
                fetched_at=doc_dict.get("fetched_at", 0.0),
                metadata_json=doc_dict.get("metadata_json", "{}"),
            )
            self._upsert_batch([doc])

        # Kompaktuj WAL
        try:
            wal_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._wal_dirty = False
        logger.info("DuckDBFTSStore: WAL consolidated %d entries", len(entries))

    async def _flush_wal_if_needed(self) -> None:
        """Flush WAL kdyz prekroci _wal_flush_size threshold."""
        if not self._wal_dirty:
            return
        if self._wal_size() < self._wal_flush_size:
            return
        self._consolidate_wal()

    async def close(self) -> None:
        """Uzavre DuckDB connection + flush WAL."""
        async with self._lock:
            if self._wal_dirty:
                self._consolidate_wal()
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._initialized = False
            self._bm25_index.clear()
            self._bm25_doc_ids.clear()
            self._bm25_combined.clear()
        """Uzavre DuckDB connection."""
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._initialized = False
            self._bm25_index.clear()
            self._bm25_doc_ids.clear()
            self._bm25_combined.clear()

    # --------------------------------------------------------------------------- #
    # BM25 Index
    # --------------------------------------------------------------------------- #

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenizace pro BM25 — whitespace split + lowercase."""
        if not text:
            return []
        return text.lower().split()

    def _rebuild_bm25_index(self) -> None:
        """Rebuild BM25 index pro vsechny sources z DuckDB."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT doc_id, title, body, source FROM doc_bm25;"
        ).fetchall()

        by_source: dict[str, tuple[list[str], list[str], list[str]]] = {}
        for doc_id, title, body, source in rows:
            if source not in by_source:
                by_source[source] = ([], [], [])
            by_source[source][0].append(doc_id)
            by_source[source][1].append(title or "")
            by_source[source][2].append(body or "")

        self._bm25_index.clear()
        self._bm25_doc_ids.clear()
        for source, (doc_ids, titles, bodies) in by_source.items():
            if not doc_ids:
                continue
            combined = [f"{t} {b}".strip() for t, b in zip(titles, bodies)]
            tokenized = [self._tokenize(c) for c in combined]
            if tokenized and any(tok for tok in tokenized if tok):
                self._bm25_index[source] = BM25Okapi(tokenized)
                self._bm25_doc_ids[source] = doc_ids
                self._bm25_combined[source] = combined

        self._bm25_dirty = False
        logger.info(
            "DuckDBFTSStore: BM25 rebuilt for %d sources, total %d docs",
            len(self._bm25_index),
            sum(len(v) for v in self._bm25_doc_ids.values()),
        )

    def _invalidate_bm25(self, source: str, doc_id: str) -> None:
        """Invaliduje BM25 cache po upsert/delete."""
        self._bm25_dirty = True
        if source not in self._bm25_doc_ids:
            self._bm25_doc_ids[source] = []
        if doc_id not in self._bm25_doc_ids[source]:
            self._bm25_doc_ids[source].append(doc_id)
        self._bm25_index[source] = None
        # combined is stale too
        if source in self._bm25_combined:
            del self._bm25_combined[source]

    # --------------------------------------------------------------------------- #
    # Indexace — Arrow zero-copy
    # --------------------------------------------------------------------------- #

    async def upsert(self, documents: list[FTSDocument]) -> int:
        """Indexuje dokumenty pres Arrow zero-copy ingest + WAL append.

        Issue #25: Kazdy dokument je append do WAL (pro konsolidaci po restartu).
        WAL je flushnut az prekroci _wal_flush_size threshold.
        """
        if not documents:
            return 0
        async with self._lock:
            assert self._conn is not None
            total = 0
            for start in range(0, len(documents), self._max_batch_size):
                batch = documents[start:start + self._max_batch_size]
                count = self._upsert_batch(batch)
                total += count
                # WAL append per batch (incremental, survives crash)
                for doc in batch:
                    self._wal_append("upsert", doc)
            # Periodicka WAL konsolidace
            await self._flush_wal_if_needed()
            return total

    def _upsert_batch(self, batch: list[FTSDocument]) -> int:
        """Upsert batch pres Arrow register + INSERT OR REPLACE."""
        assert self._conn is not None

        rows = [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "body": d.body,
                "source": d.source,
                "url": d.url,
                "fetched_at": d.fetched_at,
                "metadata_json": d.metadata_json,
            }
            for d in batch
        ]
        df = DataFrame(rows)
        arrow_table = df.to_arrow()

        reg_name = f"fts_batch_{uuid.uuid4().hex[:12]}"
        try:
            self._conn.register(reg_name, arrow_table)
            self._conn.execute(f"""
                INSERT OR REPLACE INTO doc_bm25
                (doc_id, title, body, source, url, fetched_at, metadata_json)
                SELECT doc_id, title, body, source, url, fetched_at, metadata_json
                FROM {reg_name};
            """)
            self._conn.commit()  # ensure DuckDB persists the batch
        finally:
            try:
                self._conn.unregister(reg_name)
            except Exception:
                pass

        for d in batch:
            self._invalidate_bm25(d.source, d.doc_id)

        logger.debug("DuckDBFTSStore: indexed %d documents", len(batch))
        return len(batch)

    # --------------------------------------------------------------------------- #
    # BM25 search
    # --------------------------------------------------------------------------- #

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        source_filter: str | None = None,
    ) -> list[FTSSearchResult]:
        """
        Vyhleda dokumenty pres BM25 ranking.

        query:         plaintext query (tokenized + scored s BM25Okapi)
        top_k:         maximalni pocet vysledku
        source_filter: volitelny filtr na source provider

        Returns seznam FTSSearchResult serazeny sestupne podle BM25 score.
        """
        if not query.strip():
            return []

        # Rebuild if dirty (synchronous, no lock needed for pure computation)
        if self._bm25_dirty:
            self._rebuild_bm25_index()

        async with self._lock:
            assert self._conn is not None

            query_tokens = self._tokenize(query)
            if not query_tokens:
                return []

            if source_filter:
                sources = [source_filter] if source_filter in self._bm25_index else []
            else:
                sources = list(self._bm25_index.keys())

            all_results: list[tuple[float, FTSSearchResult]] = []

            for source in sources:
                bm25 = self._bm25_index.get(source)
                doc_ids = self._bm25_doc_ids.get(source, [])
                if bm25 is None or not doc_ids:
                    continue

                source_combined = self._bm25_combined.get(source, [])
                if not source_combined:
                    continue

                # Score ALL docs in corpus, then filter/rank
                scores = bm25.get_scores(query_tokens)

                # For each doc: count how many query terms appear (handles rare-term problem)
                query_terms = set(query_tokens)
                match_counts = []
                for text in source_combined:
                    text_lower = text.lower()
                    count = sum(1 for term in query_terms if term in text_lower)
                    match_counts.append(count)

                # Build (score, match_count, doc_id, index) tuples
                # For docs with 0 matching terms → skip
                # For docs with matching terms → use match_count as primary, BM25 score as secondary
                scored = []
                for i, (doc_id, score, mc) in enumerate(zip(doc_ids, scores, match_counts)):
                    if mc > 0:
                        scored.append((mc, score, doc_id, i))

                if not scored:
                    continue

                # Sort: primary = match_count desc, secondary = BM25 score desc
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                top_scored = scored[:top_k]

                # Fetch from DuckDB
                top_doc_ids = [s[2] for s in top_scored]
                safe_ids = [did.replace("'", "''") for did in top_doc_ids]
                placeholders = ",".join(f"'{sid}'" for sid in safe_ids)
                rows = self._conn.execute(
                    f"SELECT doc_id, title, body, url, source, fetched_at "
                    f"FROM doc_bm25 WHERE doc_id IN ({placeholders});"
                ).fetchall()

                # Preserve ranking: use (match_count, BM25_score) as composite rank
                doc_score_map = {(s[2],): (s[0], s[1]) for s in top_scored}
                for row in rows:
                    doc_id, title, body, url, src, fetched = row
                    snippet = self._make_snippet(body or title or "", query)
                    mc, bm25_score = doc_score_map.get((doc_id,), (0, 0.0))
                    all_results.append((
                        float(mc) + bm25_score * 0.001,
                        FTSSearchResult(
                            doc_id=str(doc_id),
                            title=str(title),
                            body_snippet=snippet,
                            url=str(url) if url else None,
                            source=str(src),
                            rank=float(mc),
                            fetched_at=float(fetched),
                        ),
                    ))

            all_results.sort(key=lambda x: x[0], reverse=True)
            return [r for _, r in all_results[:top_k]]

    @staticmethod
    def _make_snippet(body: str, query: str, context_chars: int = 120) -> str:
        """Vytvori snippet kolem prvniho matchu query terms."""
        body_lower = body.lower()
        query_lower = query.lower()
        query_terms = query_lower.split()

        first_pos = len(body)
        for term in query_terms:
            pos = body_lower.find(term)
            if pos != -1 and pos < first_pos:
                first_pos = pos

        if first_pos == len(body):
            return body[:context_chars] + ("..." if len(body) > context_chars else "")

        start = max(0, first_pos - context_chars // 2)
        end = min(len(body), start + context_chars)
        snippet = body[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(body):
            snippet = snippet + "..."
        return snippet

    # --------------------------------------------------------------------------- #
    # Hybrid scoring s LanceDB ANN
    # --------------------------------------------------------------------------- #

    @staticmethod
    def rrf_fuse(
        fts_results: list[FTSSearchResult],
        ann_results: list[dict[str, Any]],
        *,
        top_k: int = 20,
        k: int = BM25_K,
    ) -> list[dict[str, Any]]:
        """
        Reciprocal Rank Fusion — kombinuje FTS BM25 + ANN similarity.

        RRF score = sum(w_i / (k + rank_i))
        FTS (BM25) ma prioritu 0.7, ANN similarity 0.3.
        """
        if not fts_results and not ann_results:
            return []

        scores: dict[str, tuple[float, FTSSearchResult | dict[str, Any]]] = {}

        for i, res in enumerate(fts_results):
            rrf_score = 0.7 * (1.0 / (k + i))
            scores[res.doc_id] = (rrf_score, res)

        for i, res in enumerate(ann_results):
            entity_id = res.get("entity_id") or res.get("doc_id", "")
            if not entity_id:
                continue
            rrf_score = 0.3 * (1.0 / (k + i))
            if entity_id in scores:
                scores[entity_id] = (scores[entity_id][0] + rrf_score, scores[entity_id][1])
            else:
                scores[entity_id] = (rrf_score, res)

        ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
        results = []
        for doc_id, (score, original) in ranked[:top_k]:
            if isinstance(original, FTSSearchResult):
                results.append({
                    "doc_id": original.doc_id,
                    "title": original.title,
                    "body_snippet": original.body_snippet,
                    "url": original.url,
                    "source": original.source,
                    "rank": score,
                    "fetched_at": original.fetched_at,
                    "match_type": "fts",
                })
            else:
                results.append({
                    "doc_id": doc_id,
                    "title": original.get("title", ""),
                    "body_snippet": original.get("snippet", ""),
                    "url": original.get("url"),
                    "source": original.get("source", "ann"),
                    "rank": score,
                    "fetched_at": original.get("fetched_at", 0.0),
                    "match_type": "ann",
                })

        return results

    # --------------------------------------------------------------------------- #
    # Utility
    # --------------------------------------------------------------------------- #

    async def count(self) -> int:
        """Pocet indexovanych dokumentu."""
        async with self._lock:
            assert self._conn is not None
            row = self._conn.execute("SELECT COUNT(*) FROM doc_bm25;").fetchone()
            return int(row[0]) if row else 0

    async def health_check(self) -> dict[str, Any]:
        """Health check pro telemetry."""
        try:
            cnt = await self.count()
            size_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
            return {
                "status": "healthy",
                "doc_count": cnt,
                "indexed_sources": len(self._bm25_index),
                "db_path": str(self._db_path),
                "size_mb": round(size_bytes / 1024 / 1024, 2),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    async def delete(self, doc_ids: list[str]) -> int:
        """Odstrani dokumenty z indexu."""
        if not doc_ids:
            return 0
        async with self._lock:
            assert self._conn is not None
            safe_ids = [did.replace("'", "''") for did in doc_ids]
            placeholders = ",".join(f"'{sid}'" for sid in safe_ids)
            self._conn.execute(f"DELETE FROM doc_bm25 WHERE doc_id IN ({placeholders});")
            self._conn.commit()
            self._bm25_dirty = True
            return len(doc_ids)

    async def aiter_all(self, batch_size: int = 1000) -> AsyncIterator[FTSDocument]:
        """Async iterator pres vsechny dokumenty (pro migraci / reindex)."""
        async with self._lock:
            assert self._conn is not None
            offset = 0
            while True:
                rows = self._conn.execute(f"""
                    SELECT doc_id, title, body, source, url, fetched_at, metadata_json
                    FROM doc_bm25
                    ORDER BY fetched_at DESC
                    LIMIT {batch_size} OFFSET {offset};
                """).fetchall()
                if not rows:
                    break
                for row in rows:
                    yield FTSDocument(
                        doc_id=str(row[0]),
                        title=str(row[1]),
                        body=str(row[2]),
                        source=str(row[3]),
                        url=str(row[4]) if row[4] else None,
                        fetched_at=float(row[5]),
                        metadata_json=str(row[6]),
                    )
                offset += batch_size


# --------------------------------------------------------------------------- #
# Global singleton (lazy)
# --------------------------------------------------------------------------- #

_fts_store: DuckDBFTSStore | None = None
_fts_lock = asyncio.Lock()


async def get_fts_store(
    db_path: str | Path | None = None,
) -> DuckDBFTSStore:
    """Lazy singleton — bezpecne pro async context."""
    global _fts_store
    async with _fts_lock:
        if _fts_store is None:
            _fts_store = DuckDBFTSStore(db_path=db_path)
            await _fts_store.initialize()
        return _fts_store
