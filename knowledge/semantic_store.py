"""
Sprint 8SB — SemanticStore: MLX + LanceDB Semantic IOC Search
Sprint F228B: CoreML/ANE embedder as preferred backend.
Sprint SWARM-002: Multilingual Support (BGE-M3 + dual-index)


Singleton lifecycle — initialize() v BOOT, close() v TEARDOWN.
ROLE: Consumer/Enrichment (NOT backend owner, NOT grounding authority)

MLXEmbeddingManager (ModernBERT, unified memory) + LanceDB ANN index.
LanceDB ANN index pod ~/.hledac/lancedb/ — append mode, nikdy drop+recreate.

Multilingual architecture (SWARM-002):
- English texts → 256d ModernBERT → english_index (primary)
- Non-English texts → 256d BGE-M3 (via MRL truncation) → multilingual_index
- Language detection at ingest time (fasttext/langdetect)
- Cross-lingual search: query language detection → route to appropriate index

ANE path (preferred): CoreMLEmbedder → CoreML (.mlmodelc) → ANE
Hash fallback: deterministic zero-RAM hash when MLX/ANE unavailable

NENÍ owner backend storage → persistent_layer (depr!)
NENÍ owner embedding computation → MLXEmbeddingManager singleton
NENÍ owner primary retrieval → rag_engine
"""


import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from operator import attrgetter, itemgetter
import numpy as np

if TYPE_CHECKING:
    import lancedb
    from lancedb.query import LanceVectorQueryBuilder


logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────────
_EMBED_DIM = 256  # E-22 FIX: standardize to 256d to match EmbeddingCache + pipeline MRL
_MAX_PENDING = 2000  # Bounded pending buffer
_MAX_TEXT_LEN = 4096
_TABLE_NAME = "semantic_ioc_v1"
_TABLE_NAME_MULTILINGUAL = "semantic_ioc_multilingual_v1"  # SWARM-002

# Sprint F228B: CPU executor for embed (never block event loop)
from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore  # noqa: E402
from core import aclose

CPU_EXECUTOR = get_semaphore(ConcurrencyCategory.MLX_INFERENCE)

# ── CoreML/ANE availability ────────────────────────────────────────────────────
try:
    from hledac.universal.brain.coreml_embedder import (
        CoreMLEmbedder,
    )
    from hledac.universal.brain.coreml_embedder import (
        get_coreml_embedder as _get_coreml_embedder_impl,
    )
    from hledac.universal.brain.coreml_embedder import (
        is_ane_available as _COREML_ANE_AVAILABLE,
    )

    _COREML_AVAILABLE = True
    _get_coreml_embedder: Any = _get_coreml_embedder_impl
except ImportError:
    _COREML_AVAILABLE = False
    _COREML_ANE_AVAILABLE: bool = False
    _get_coreml_embedder: Any = None
    # NOTE: CoreMLEmbedder left as undefined (TypeError at runtime if accessed)
    # — callers guard with _COREML_AVAILABLE or isinstance checks


# ── SWARM-002: Multilingual support ─────────────────────────────────────────────
_MULTILINGUAL_AVAILABLE = False
_lang_detector: Any = None
_bge_m3_embedder: Any = None

try:
    from hledac.universal.core.multilingual import (
        LangDetector,
        detect_language,
        get_lang_detector,
        BGEM3Embedder,
        get_bge_m3_embedder,
    )
    _MULTILINGUAL_AVAILABLE = True
except ImportError:
    # Multilingual modules not available (missing dependencies)
    LangDetector = None
    BGEM3Embedder = None
    logger.debug('[SEMSTORE] Multilingual modules not available (install requirements)')


class SemanticStore:
    """
    MLX + LanceDB pro sémantické vyhledávání findings.

    SWARM-002: Dual-index multilingual architecture:
    - English texts → 256d ModernBERT → english_index (primary)
    - Non-English texts → 256d BGE-M3 (via MRL truncation) → multilingual_index

    ANE path (F228B): CoreMLEmbedder.embed() → CoreML → ANE (preferred)
    Hash fallback: deterministic zero-RAM hash when MLX/ANE unavailable.

    Lifecycle:
        await store.initialize()  # BOOT — load model + open LanceDB
        store.add_text(...)        # Buffer (sync, no I/O)
        await store.flush()        # Batch embed + LanceDB upsert
        await store.semantic_pivot(...)  # ANN search
        await store.close()        # TEARDOWN
    """

    # -------------------------------------------------------------------------
    # Fields
    # -------------------------------------------------------------------------
    # SAFE-2.4/2.5: Embedding validation constants
    _SAFE_EMBED_DIM: int = _EMBED_DIM  # Expected embedding dimension (256)
    _SAFE_EMBED_DIM_TOLERANCE: int = 2  # Allow 2-dim tolerance for LanceDB schema
    _SAFE_EMBED_VALUE_MAX: float = 100.0  # Max value to prevent NaN/Inf in vector DB
    _SAFE_EMBED_VALUE_MIN: float = -100.0  # Min value
    _SAFE_EMBED_NORM_MAX: float = 2.0  # Max L2 norm before renormalization

    __slots__ = (
        "_db_path",
        "_db",
        "_table",
        "_table_multilingual",  # SWARM-002: multilingual LanceDB table
        "_vec_db",  # Issue 4.3: sqlite-vec fallback when LanceDB unavailable
        "_vec_db_multilingual",  # SWARM-002: sqlite-vec fallback for multilingual
        "_model",
        "_coreml_embedder",
        "_mlx_embedder",
        "_lang_detector",  # SWARM-002: language detection
        "_bge_m3_embedder",  # SWARM-002: multilingual embedder
        "_pending_texts",
        "_pending_meta",
        "_pending_languages",  # SWARM-002: detected languages
        "_embed_dim",
        "_initialized",
        "_multilingual_enabled",  # SWARM-002: multilingual feature flag
        "_embed_validation_stats",  # SAFE-2.5: validation statistics
        # SAFE-4: Buffer overflow observability
        "_buffer_overflow_drops",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: lancedb.LanceDBConnection | None = None  # lancedb.LanceDBConnection
        self._table: lancedb.Table | None = None  # lancedb.Table
        self._table_multilingual: lancedb.Table | None = None  # SWARM-002
        self._vec_db: Any = None  # Issue 4.3: sqlite-vec.Connection fallback
        self._vec_db_multilingual: Any = None  # SWARM-002
        self._model: Any = None  # removed FastEmbed (E-23) — hash fallback only
        # Sprint F228B: CoreML/ANE embedder — lazy async init in initialize()
        # (get_coreml_embedder() is now async; __init__ cannot await)
        self._coreml_embedder: CoreMLEmbedder | None = None
        self._pending_texts: deque = deque()
        self._pending_meta: deque = deque()
        self._pending_languages: deque = deque()  # SWARM-002
        self._embed_dim: int = _EMBED_DIM
        self._initialized: bool = False
        # SWARM-002: Multilingual components
        self._lang_detector: LangDetector | None = None
        self._bge_m3_embedder: BGEM3Embedder | None = None
        self._multilingual_enabled: bool = False
        # SAFE-2.5: Embedding validation statistics
        self._embed_validation_stats = {
            'total_checked': 0,
            'dimension_errors': 0,
            'nan_inf_errors': 0,
            'value_outliers': 0,
            'renormalized': 0,
        }
        # SAFE-4: Buffer overflow drop counter for observability
        # Tracks OSINT evidence silently lost due to bounded buffer
        self._buffer_overflow_drops: int = 0
        # F2 FIX: Track which ANN backend is active for consistency checking
        self._ann_backend: str = "unknown"

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """BOOT — load MLX/CoreML embedder + open LanceDB conn + multilingual."""
        if self._initialized:
            return

        asyncio.get_running_loop()

        # Sprint F228B: Try CoreMLEmbedder ANE path first (async lazy init)
        if _COREML_AVAILABLE:
            try:
                from hledac.universal.utils.coreml import CoreMLServiceManager
                await CoreMLServiceManager.ensure_running_async()
            except Exception:  # noqa: BLE001
                pass
            # async get — safe to call from async context (DCLP singleton)
            self._coreml_embedder = await _get_coreml_embedder()

        # Sprint F228B: Try CoreMLEmbedder ANE path first
        if self._coreml_embedder is not None:
            try:
                await self._coreml_embedder.load()
                logger.info(
                    "[SEMSTORE] CoreMLEmbedder loaded (ANE path=%s, backend=%s)",
                    _COREML_ANE_AVAILABLE,
                    getattr(self._coreml_embedder, '_backend', None) or "hash",
                )
            except Exception as e:
                logger.warning("[SEMSTORE] CoreMLEmbedder load failed: %s", e)
                self._coreml_embedder = None

        # MLX path: Use MLXEmbeddingManager singleton (modernbert-embed-base)
        # This uses mlx_embeddings package via compat/core_mlx_embeddings shim
        self._mlx_embedder = None
        try:
            from hledac.universal.core.mlx_embeddings import get_embedding_manager

            self._mlx_embedder = get_embedding_manager()
            # Ensure loaded
            if not self._mlx_embedder.is_loaded:
                await asyncio.to_thread(self._mlx_embedder._load_model)
            logger.info("[SEMSTORE] MLXEmbeddingManager loaded (ModernBERT, unified memory)")
        except Exception as e:
            logger.debug("[SEMSTORE] MLXEmbeddingManager not available: %s", e)
            self._mlx_embedder = None

        # E-23 FIX: FastEmbed removed — zero-RAM hash fallback is sufficient when MLX/ANE unavailable.
        # FastEmbed (ONNX Runtime ~100MB + BGE model ~33MB) was redundant with MLXEmbeddingManager.
        self._model = None

        # SWARM-002: Initialize multilingual components
        await self._initialize_multilingual()

        # F2 FIX: Consistent ANN backend selection with vector_index.py
        # Respect HLEDAC_VECTOR_BACKEND env var, auto-select based on M1 detection
        from hledac.universal.knowledge.vector_index import _resolve_backend, _is_m1
        resolved_backend = _resolve_backend()
        if resolved_backend == "auto":
            resolved_backend = "sqlite-vec" if _is_m1() else "lancedb"
        
        # F2 FIX: Startup consistency marker - invalidate old store on backend change
        # This prevents orphaned vectors when switching between LanceDB and sqlite-vec
        _backend_marker_path = self._db_path.parent / ".ann_backend_marker"
        try:
            if _backend_marker_path.exists():
                stored_backend = _backend_marker_path.read_text().strip()
                if stored_backend != resolved_backend:
                    logger.warning(
                        "[SEMSTORE] ANN backend changed from '%s' to '%s'. "
                        "Invalidating semantic store to prevent orphaned vectors.",
                        stored_backend, resolved_backend
                    )
                    # F2 FIX COMPLETE: Delete BOTH sqlite-vec .db files AND LanceDB directory
                    # sqlite-vec stores at: self._db_path.parent / "semantic_vec.db"
                    # LanceDB stores at: self._db_path (directory with .lance data)
                    for db_file in self._db_path.parent.glob("*.db"):
                        db_name = db_file.name
                        if "semantic_vec" in db_name or "lancedb" in db_name:
                            try:
                                db_file.unlink()
                                logger.info("[SEMSTORE] Deleted stale .db file: %s", db_file)
                            except Exception:
                                pass
                    # Clean up LanceDB directory if switching away from LanceDB
                    if stored_backend == "lancedb" and self._db_path.exists():
                        import shutil
                        try:
                            shutil.rmtree(self._db_path)
                            logger.info("[SEMSTORE] Deleted stale LanceDB directory: %s", self._db_path)
                        except Exception:
                            pass
            # Write new marker
            _backend_marker_path.write_text(resolved_backend)
        except Exception as e:
            logger.debug("[SEMSTORE] Backend marker check failed: %s", e)
        
        self._ann_backend = resolved_backend
        logger.info("[SEMSTORE] ANN backend resolved: %s", resolved_backend)

        # Open LanceDB (primary) — falls back to sqlite-vec on failure
        # F2 FIX: Only try LanceDB if backend is explicitly lancedb
        # Note: resolved_backend is already resolved (not "auto") at this point
        _lance_enabled = resolved_backend == "lancedb"
        try:
            if _lance_enabled:
                import lancedb

                db_path_str = str(self._db_path.expanduser())
                self._db = lancedb.connect(db_path_str)
        except Exception as e:
            logger.warning("[SEMSTORE] LanceDB connect failed: %s", e)
            self._db = None
            _lance_enabled = False

        # Open or create LanceDB table (append mode — B.6)
        _table_opened = False
        try:
            if self._db is not None:
                self._table = self._db.open_table(_TABLE_NAME)
                assert self._table is not None
                logger.info(
                    f"SemanticStore: LanceDB table open: {self._table.count_rows()} rows"
                )
                _table_opened = True
            else:
                self._table = None
        except Exception:
            self._table = None  # Will be created on first flush

        # SWARM-002: Open multilingual LanceDB table
        try:
            if self._db is not None:
                self._table_multilingual = self._db.open_table(_TABLE_NAME_MULTILINGUAL)
                assert self._table_multilingual is not None
                logger.info(
                    f"SemanticStore: Multilingual table open: {self._table_multilingual.count_rows()} rows"
                )
            else:
                self._table_multilingual = None
        except Exception:
            self._table_multilingual = None

        # Issue 4.3: sqlite-vec — zero-RAM ANN search via SQLite extension.
        # On M1 8GB: avoids LanceDB process overhead (~50MB resident).
        # sqlite-vec is a single-file SQLite extension (<1MB), loaded in-process.
        # F2 FIX: Use sqlite-vec when explicitly requested or as fallback when LanceDB fails
        if not _table_opened or resolved_backend == "sqlite-vec":
            try:
                import sqlite_vec

                vec_db_path = str(self._db_path.parent / "semantic_vec.db")
                self._vec_db = sqlite_vec.connect(vec_db_path)
                # Create virtual table for vectors
                self._vec_db.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE_NAME} USING vec0("
                    f"finding_id TEXT PRIMARY KEY, text TEXT, source_type TEXT, "
                    f"finding_id_idx TEXT, ts REAL, ioc_types TEXT, "
                    f"embedding float[{self._embed_dim}])"
                )
                logger.info(f"[SEMSTORE] sqlite-vec active: {vec_db_path}")
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec failed: %s", e)
                self._vec_db = None

        # SWARM-002: sqlite-vec for multilingual
        _multilingual_table_opened = self._table_multilingual is not None
        if not _multilingual_table_opened or resolved_backend == "sqlite-vec":
            try:
                import sqlite_vec

                vec_db_path = str(self._db_path.parent / "semantic_vec_multilingual.db")
                self._vec_db_multilingual = sqlite_vec.connect(vec_db_path)
                self._vec_db_multilingual.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE_NAME_MULTILINGUAL} USING vec0("
                    f"finding_id TEXT PRIMARY KEY, text TEXT, source_type TEXT, "
                    f"finding_id_idx TEXT, ts REAL, ioc_types TEXT, language TEXT, "
                    f"embedding float[{self._embed_dim}])"
                )
                logger.info(f"[SEMSTORE] sqlite-vec multilingual active: {vec_db_path}")
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec multilingual failed: %s", e)
                self._vec_db_multilingual = None

        self._initialized = True
        logger.info(
            f"SemanticStore initialized: dim={self._embed_dim}, "
            f"coreml_ane={_COREML_ANE_AVAILABLE}, "
            f"vec_backend={'lancedb' if self._table else 'sqlite-vec' if self._vec_db else 'memory'}, "
            f"multilingual_enabled={self._multilingual_enabled}"
        )

    async def _initialize_multilingual(self) -> None:
        """SWARM-002: Initialize multilingual components (BGE-M3 + language detection)."""
        if not _MULTILINGUAL_AVAILABLE:
            logger.debug('[SEMSTORE] Multilingual modules not available')
            return

        try:
            # Initialize language detector
            self._lang_detector = get_lang_detector(
                use_fasttext=True,
                use_langdetect=True,
                confidence_threshold=0.7
            )
            logger.info('[SEMSTORE] Language detector initialized')

            # Initialize BGE-M3 embedder (lazy load)
            self._bge_m3_embedder = get_bge_m3_embedder(
                mrl_target_dim=self._embed_dim,  # 256d for USEARCH compatibility
                lazy_load=True
            )

            self._multilingual_enabled = True
            logger.info('[SEMSTORE] BGE-M3 embedder initialized (multilingual enabled)')

        except Exception as e:
            logger.warning(f'[SEMSTORE] Multilingual initialization failed: {e}')
            self._multilingual_enabled = False

    # -------------------------------------------------------------------------
    # Buffering (no I/O)
    # -------------------------------------------------------------------------

    def add_text(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ioc_types: list[str] | None = None,
        ts: float | None = None,
    ) -> None:
        """
        Buffer a finding for batch embed — ŽÁDNÉ I/O.

        SWARM-002: Language is detected at add_text() time for accurate routing.

        Args:
            text: Raw text to embed.
            source_type: e.g. "certificate_transparency", "public_hunter".
            finding_id: Unique identifier.
            ioc_types: List of IOC type strings for filtering.
            ts: Optional timestamp (defaults to current loop time if not provided).
        """
        if not text.strip():
            return
        # Enforce bounded pending buffer (M1 8GB safety)
        # SAFE-4 FIX: Changed from logger.debug to logger.warning + metrics
        # Silent drops were causing invisible OSINT evidence loss
        if len(self._pending_texts) >= _MAX_PENDING:
            self._buffer_overflow_drops += 1
            # Log at WARNING level for visibility - this is data loss, not debug info
            logger.warning(
                f"[SEMSTORE] Buffer overflow: dropping oldest finding "
                f"(drops_since_start={self._buffer_overflow_drops}, max={_MAX_PENDING})"
            )
            self._pending_texts.popleft()
            self._pending_meta.popleft()
            self._pending_languages.popleft()
        self._pending_texts.append(text[:_MAX_TEXT_LEN])
        if ts is None:
            try:
                ts = asyncio.get_running_loop().time()
            except RuntimeError:
                ts = 0.0

        # SWARM-002: Detect language at ingest time
        lang_result = None
        if self._multilingual_enabled and self._lang_detector is not None:
            try:
                lang_result = self._lang_detector.detect(text)
            except Exception:  # noqa: BLE001
                pass

        self._pending_meta.append(
            {
                "source_type": source_type,
                "finding_id": finding_id,
                "ts": ts,
                "ioc_types": ",".join(ioc_types) if ioc_types else "",
                "language": lang_result.language if lang_result else "en",
            }
        )
        self._pending_languages.append(lang_result)

    # -------------------------------------------------------------------------
    # Flush — batch embed + LanceDB append (split into helpers)
    # -------------------------------------------------------------------------

    def _split_by_language(
        self,
        texts: list[str],
        meta: list[dict],
        languages: list,
    ) -> tuple[list[int], list[int], list[str], list[str], list[str]]:
        """Split texts by language into English and multilingual groups."""
        english_indices, multilingual_indices = [], []
        english_texts, multilingual_texts = [], []
        multilingual_langs = []

        for i, lang_result in enumerate(languages):
            if lang_result is not None and lang_result.is_english:
                english_indices.append(i)
                english_texts.append(texts[i])
            else:
                multilingual_indices.append(i)
                multilingual_texts.append(texts[i])
                multilingual_langs.append(lang_result.language if lang_result else "unknown")

        return english_indices, multilingual_indices, english_texts, multilingual_texts, multilingual_langs

    def _build_lance_english_record(self, emb: np.ndarray, idx: int, texts: list[str], meta: list[dict]) -> dict:
        """Build a LanceDB record for English embedding."""
        return {
            "vector": emb.tolist(),
            "text": texts[idx][: _MAX_TEXT_LEN],
            "source_type": meta[idx]["source_type"],
            "finding_id": meta[idx]["finding_id"],
            "ts": meta[idx]["ts"],
            "ioc_types": meta[idx]["ioc_types"],
        }

    def _build_lance_multilingual_record(self, emb: np.ndarray, idx: int, texts: list[str], meta: list[dict], lang: str) -> dict:
        """Build a LanceDB record for multilingual embedding."""
        return {
            "vector": emb.tolist(),
            "text": texts[idx][: _MAX_TEXT_LEN],
            "source_type": meta[idx]["source_type"],
            "finding_id": meta[idx]["finding_id"],
            "ts": meta[idx]["ts"],
            "ioc_types": meta[idx]["ioc_types"],
            "language": lang,
        }

    def _build_sqlite_vec_row(self, emb_idx: int, texts: list[str], meta: list[dict], emb: np.ndarray) -> tuple:
        """Build a sqlite-vec row tuple for English embedding."""
        return (
            meta[emb_idx]["finding_id"],
            texts[emb_idx][: _MAX_TEXT_LEN],
            meta[emb_idx]["source_type"],
            meta[emb_idx]["finding_id"],
            meta[emb_idx]["ts"],
            meta[emb_idx]["ioc_types"],
            emb.tolist(),
        )

    def _build_sqlite_vec_multilingual_row(self, emb_idx: int, texts: list[str], meta: list[dict], emb: np.ndarray, lang: str) -> tuple:
        """Build a sqlite-vec row tuple for multilingual embedding."""
        return (
            meta[emb_idx]["finding_id"],
            texts[emb_idx][: _MAX_TEXT_LEN],
            meta[emb_idx]["source_type"],
            meta[emb_idx]["finding_id"],
            meta[emb_idx]["ts"],
            meta[emb_idx]["ioc_types"],
            lang,
            emb.tolist(),
        )

    async def flush(self) -> dict[str, int | dict]:
        """
        Batch embed + LanceDB upsert.

        SWARM-002: Dual-index routing:
        - English texts → ModernBERT 256d → english_index
        - Non-English texts → BGE-M3 256d (MRL) → multilingual_index

        ANE path: CoreMLEmbedder.embed() → CoreML → ANE (F228B, preferred)
        Hash fallback: deterministic zero-RAM hash when MLX/ANE unavailable

        SAFE-4: Returns detailed dict with counts and error info for observability.
        """
        if not self._initialized:
            return {'total': 0, 'english': 0, 'multilingual': 0, 'errors': {}}
        if not self._pending_texts:
            return {'total': 0, 'english': 0, 'multilingual': 0, 'errors': {}}

        # Capture items BEFORE clearing to enable retry on failure
        texts = list(self._pending_texts)
        meta = list(self._pending_meta)
        languages = list(self._pending_languages)

        english_indices, multilingual_indices, english_texts, multilingual_texts, multilingual_langs = \
            self._split_by_language(texts, meta, languages)

        logger.debug(
            f"[SEMSTORE] Language split: {len(english_texts)} English, "
            f"{len(multilingual_texts)} multilingual"
        )

        # Embed texts
        embeddings_english = await self._embed_english(english_texts) if english_texts else None
        embeddings_multilingual = await self._embed_multilingual(multilingual_texts) if multilingual_texts else None

        # Write English embeddings
        english_count = 0
        english_errors = {}
        if embeddings_english is not None:
            english_count = self._write_english_embeddings(
                embeddings_english, english_indices, texts, meta,
            )
            if english_count < len(english_indices):
                english_errors['partial_write'] = len(english_indices) - english_count

        # Write multilingual embeddings
        multilingual_count = 0
        multilingual_errors = {}
        if embeddings_multilingual is not None:
            multilingual_count = self._write_multilingual_embeddings(
                embeddings_multilingual, multilingual_indices, multilingual_langs, texts, meta,
            )
            if multilingual_count < len(multilingual_indices):
                multilingual_errors['partial_write'] = len(multilingual_indices) - multilingual_count

        # Only clear buffers after successful write
        # SAFE-4: If write fails, items remain for retry
        total_written = english_count + multilingual_count
        if total_written > 0:
            self._pending_texts.clear()
            self._pending_meta.clear()
            self._pending_languages.clear()

        return {
            'total': total_written,
            'english': english_count,
            'multilingual': multilingual_count,
            'pending_items': len(texts),
            'errors': {
                'english': english_errors,
                'multilingual': multilingual_errors,
            }
        }

    def _write_english_embeddings(
        self,
        embeddings_english: np.ndarray | None,
        english_indices: list[int],
        texts: list[str],
        meta: list[dict],
    ) -> int:
        """Write English embeddings to LanceDB or sqlite-vec.
        
        SAFE-2.4: Validates all embeddings before storage to prevent corrupted data.
        """
        if embeddings_english is None or not english_indices:
            return 0

        # SAFE-2.4: Validate embeddings before writing
        safe_embeddings, failed_indices = self._safe_validate_embeddings_batch(
            embeddings_english, texts
        )
        if safe_embeddings is None:
            return 0

        if self._table is not None:
            records = [
                self._build_lance_english_record(safe_embeddings[i], english_indices[i], texts, meta)
                for i in range(len(english_indices))
            ]
            try:
                self._table.add(records)
                logger.debug("[SEMSTORE] English LanceDB upserted %d records", len(records))
                return len(records)
            except Exception as e:
                logger.warning("[SEMSTORE] English LanceDB add failed: %s", e)

        elif self._vec_db is not None:
            rows = [
                self._build_sqlite_vec_row(english_indices[i], texts, meta, safe_embeddings[i])
                for i in range(len(english_indices))
            ]
            try:
                self._vec_db.executemany(
                    f"INSERT OR REPLACE INTO {_TABLE_NAME} "
                    f"(finding_id, text, source_type, finding_id_idx, ts, ioc_types, embedding) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._vec_db.commit()
                logger.debug("[SEMSTORE] English sqlite-vec upserted %d records", len(rows))
                return len(rows)
            except Exception as e:
                logger.warning("[SEMSTORE] English sqlite-vec upsert failed: %s", e)

        return 0

    def _write_multilingual_embeddings(
        self,
        embeddings_multilingual: np.ndarray | None,
        multilingual_indices: list[int],
        multilingual_langs: list[str],
        texts: list[str],
        meta: list[dict],
    ) -> int:
        """Write multilingual embeddings to LanceDB or sqlite-vec.
        
        SAFE-2.4: Validates all embeddings before storage to prevent corrupted data.
        """
        if embeddings_multilingual is None or not multilingual_indices:
            return 0

        # SAFE-2.4: Validate embeddings before writing
        safe_embeddings, failed_indices = self._safe_validate_embeddings_batch(
            embeddings_multilingual, texts
        )
        if safe_embeddings is None:
            return 0

        if self._table_multilingual is not None:
            records = [
                self._build_lance_multilingual_record(
                    safe_embeddings[i], multilingual_indices[i], texts, meta, multilingual_langs[i],
                )
                for i in range(len(multilingual_indices))
            ]
            try:
                self._table_multilingual.add(records)
                logger.debug("[SEMSTORE] Multilingual LanceDB upserted %d records", len(records))
                return len(records)
            except Exception as e:
                logger.warning("[SEMSTORE] Multilingual LanceDB add failed: %s", e)

        elif self._vec_db_multilingual is not None:
            rows = [
                self._build_sqlite_vec_multilingual_row(
                    multilingual_indices[i], texts, meta, safe_embeddings[i], multilingual_langs[i],
                )
                for i in range(len(multilingual_indices))
            ]
            try:
                self._vec_db_multilingual.executemany(
                    f"INSERT OR REPLACE INTO {_TABLE_NAME_MULTILINGUAL} "
                    f"(finding_id, text, source_type, finding_id_idx, ts, ioc_types, language, embedding) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._vec_db_multilingual.commit()
                logger.debug("[SEMSTORE] Multilingual sqlite-vec upserted %d records", len(rows))
                return len(rows)
            except Exception as e:
                logger.warning("[SEMSTORE] Multilingual sqlite-vec upsert failed: %s", e)

        return 0

    async def _embed_english(self, texts: list[str]) -> np.ndarray | None:
        """SWARM-002: Embed English texts via ModernBERT/MLX."""
        loop = asyncio.get_running_loop()
        backend_name = "unknown"

        # MLX path preferred — Apple Silicon native, unified memory
        mlx_mgr = self._mlx_embedder
        if mlx_mgr is not None:
            backend_name = "mlx"
            try:
                def batch_encode(manager, txts: list[str]) -> np.ndarray:
                    return manager.encode(txts, normalize=True)

                embeddings = await loop.run_in_executor(
                    None, lambda: batch_encode(mlx_mgr, texts)
                )
                # Ensure 256d dimension (truncate or pad)
                embeddings = self._ensure_dim(embeddings, self._embed_dim)
                logger.debug(
                    "[SEMSTORE] English batch embed via MLXEmbeddingManager: %d texts", len(texts)
                )
                return embeddings
            except Exception as e:
                logger.warning("[SEMSTORE] MLXEmbeddingManager embed failed: %s", e)

        # Sprint F228B: ANE path fallback
        if self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            backend_name = "ane"
            try:
                embeddings = await loop.run_in_executor(
                    None, lambda: self._coreml_embedder.embed(texts, batch_size=64)  # type: ignore[union-attr]
                )
                embeddings = self._ensure_dim(embeddings, self._embed_dim)
                logger.debug(
                    "[SEMSTORE] English batch embed via CoreMLEmbedder: %d texts", len(texts)
                )
                return embeddings
            except Exception as e:
                logger.warning("[SEMSTORE] CoreMLEmbedder embed failed: %s", e)

        # Hash fallback
        return self._hash_fallback_embeddings(texts)

    async def _embed_multilingual(self, texts: list[str]) -> np.ndarray | None:
        """SWARM-002: Embed multilingual texts via BGE-M3 with MRL truncation."""
        if not self._multilingual_enabled or self._bge_m3_embedder is None:
            logger.debug("[SEMSTORE] Multilingual disabled, using hash fallback")
            return self._hash_fallback_embeddings(texts)

        loop = asyncio.get_running_loop()
        backend_name = "bge_m3"

        try:
            # BGE-M3 embed_batch is async, so we need to run it directly
            embeddings = await self._bge_m3_embedder.embed_batch(
                texts, truncate_to=self._embed_dim
            )
            logger.debug("[SEMSTORE] Multilingual batch embed via BGE-M3: %d texts", len(texts))
            return embeddings

        except Exception as e:
            logger.warning("[SEMSTORE] BGE-M3 embed failed: %s, falling back to hash", e)
            return self._hash_fallback_embeddings(texts)

    def _ensure_dim(self, embeddings: np.ndarray, target_dim: int) -> np.ndarray:
        """Ensure embeddings have target dimension (truncate or pad).

        MRL-2 FIX: Truncation now includes L2 normalization to preserve semantic similarity.
        MRL property: First target_dim dimensions contain most semantic information.
        After truncation, L2 normalization ensures cosine similarity is consistent.
        """
        if embeddings.shape[-1] == target_dim:
            return embeddings

        current_dim = embeddings.shape[-1]
        if current_dim > target_dim:
            # Truncate to target_dim (MRL prefix)
            truncated = embeddings[..., :target_dim]
            # MRL-2 FIX: Normalize after truncation to preserve similarity metrics
            norms = np.linalg.norm(truncated, axis=-1, keepdims=True)
            norms = np.where(norms > 1e-9, norms, 1.0)
            return truncated / norms
        else:
            # Pad with zeros
            result = np.zeros((*embeddings.shape[:-1], target_dim), dtype=embeddings.dtype)
            result[..., :current_dim] = embeddings
            return result

    def _hash_fallback_embeddings(self, texts: list[str]) -> np.ndarray:
        """Generate hash-based embeddings as fallback."""
        import hashlib

        emb_dim = self._embed_dim
        embeddings = []
        for t in texts:
            h = int(hashlib.sha256(t[:512].encode()).hexdigest()[:16], 16)
            vec = np.zeros(emb_dim, dtype=np.float32)
            for j in range(min(emb_dim, 384)):
                vec[j] = float((h >> (j % 32)) & 1) * 2.0 - 1.0
            norm = np.linalg.norm(vec)
            vec = vec / norm if norm > 1e-9 else vec
            embeddings.append(vec)
        return np.array(embeddings, dtype=np.float32)

    def _safe_validate_embedding(self, emb: np.ndarray) -> np.ndarray | None:
        """SAFE-2.4/2.5: Validate embedding before LanceDB storage.
        
        Prevents:
        - Wrong-dimension embeddings corrupting LanceDB index
        - NaN/Inf from propagating to vector similarity search
        - Value outliers from causing numerical instability
        
        Returns validated embedding or None if unrecoverable.
        """
        import math
        
        self._embed_validation_stats['total_checked'] += 1
        
        # Dimension validation with tolerance
        current_dim = emb.shape[-1] if emb.ndim > 0 else len(emb)
        min_dim = self._SAFE_EMBED_DIM - self._SAFE_EMBED_DIM_TOLERANCE
        max_dim = self._SAFE_EMBED_DIM + self._SAFE_EMBED_DIM_TOLERANCE
        
        if not (min_dim <= current_dim <= max_dim):
            logger.warning(
                "[SAFE-2.4] Embedding dim %d outside valid range [%d, %d]",
                current_dim, min_dim, max_dim
            )
            self._embed_validation_stats['dimension_errors'] += 1
            return None
        
        # SAFE-2.5: OOM guard - check memory footprint before store
        # M1 8GB: max 1M embeddings * 256 * 4 bytes = ~1GB
        if emb.nbytes > 1024 * 1024:  # > 1MB single embedding
            logger.warning("[SAFE-2.5] Embedding size %d bytes exceeds safety threshold", emb.nbytes)
            self._embed_validation_stats['dimension_errors'] += 1
            return None
        
        # Check for NaN/Inf
        if np.any(np.isnan(emb)) or np.any(np.isinf(emb)):
            logger.warning("[SAFE-2.5] Embedding contains NaN/Inf")
            self._embed_validation_stats['nan_inf_errors'] += 1
            return None
        
        # Value range validation - prevent numerical instability
        outliers_mask = (emb < self._SAFE_EMBED_VALUE_MIN) | (emb > self._SAFE_EMBED_VALUE_MAX)
        if np.any(outliers_mask):
            self._embed_validation_stats['value_outliers'] += 1
            # Clamp outliers to prevent numerical instability
            emb = np.clip(emb, self._SAFE_EMBED_VALUE_MIN, self._SAFE_EMBED_VALUE_MAX)
        
        # Ensure exact dimension
        if current_dim != self._SAFE_EMBED_DIM:
            if current_dim > self._SAFE_EMBED_DIM:
                emb = emb[..., :self._SAFE_EMBED_DIM]
            else:
                # Pad with zeros
                result = np.zeros((*emb.shape[:-1], self._SAFE_EMBED_DIM), dtype=emb.dtype)
                result[..., :current_dim] = emb
                emb = result
        
        # L2 normalize to prevent magnitude issues in cosine similarity
        norm = np.linalg.norm(emb)
        if norm > self._SAFE_EMBED_NORM_MAX or norm < 1e-6:
            self._embed_validation_stats['renormalized'] += 1
            emb = emb / (norm if norm > 1e-6 else 1.0)
        
        return emb

    def _safe_validate_embeddings_batch(
        self, embeddings: np.ndarray, texts: list[str]
    ) -> tuple[np.ndarray | None, list[int]]:
        """SAFE-2.4/2.5: Validate batch of embeddings before LanceDB storage.

        Returns (validated_embeddings, failed_indices) tuple.
        failed_indices contains indices of embeddings that failed validation.

        SAFE-4: Ensures validated array maintains same length as input for safe indexing.
        """
        if embeddings is None or len(embeddings) == 0:
            return None, []

        failed_indices = []
        validated = []

        for i, emb in enumerate(embeddings):
            safe_emb = self._safe_validate_embedding(np.asarray(emb))
            if safe_emb is not None:
                validated.append(safe_emb)
            else:
                failed_indices.append(i)
                # Use zero embedding as fallback for failed ones
                validated.append(np.zeros(self._SAFE_EMBED_DIM, dtype=np.float32))

        # SAFE-4: Ensure output length matches input for safe indexing
        if len(validated) != len(embeddings):
            logger.error(
                "[SAFE-4] Validation output length mismatch: input=%d, output=%d",
                len(embeddings), len(validated)
            )
            return None, failed_indices

        if failed_indices:
            logger.warning(
                "[SAFE-2.4] %d/%d embeddings failed validation",
                len(failed_indices), len(embeddings)
            )

        return np.array(validated, dtype=np.float32), failed_indices

    def _validate_search_result(self, result: dict) -> dict | None:
        """FIX-4: Validate LanceDB search result structure and values.
        
        Prevents:
        - Missing required fields
        - Invalid scores (NaN/Inf, out of range)
        - Malformed text fields
        
        Returns validated result or None if invalid.
        """
        import math
        
        # Required fields
        required_fields = ["text", "source_type", "finding_id", "ts", "ioc_types", "score"]
        for field in required_fields:
            if field not in result:
                logger.debug("[FIX-4] Search result missing required field: %s", field)
                return None
        
        # Validate score
        score = result.get("score")
        if score is None or not isinstance(score, (int, float)):
            logger.debug("[FIX-4] Search result has invalid score type: %s", type(score))
            return None
        
        if math.isnan(score) or math.isinf(score):
            logger.debug("[FIX-4] Search result has NaN/Inf score")
            return None
        
        # Score should be in [0, 1] for cosine similarity (after transformation)
        # But allow some tolerance for floating point errors
        if not (-0.01 <= score <= 1.01):
            logger.debug("[FIX-4] Search result score out of range: %f", score)
            return None
        
        # Validate text field
        text = result.get("text")
        if text is not None and len(text) > _MAX_TEXT_LEN * 2:  # Sanity check for text length
            # Truncate to prevent memory issues
            result["text"] = text[:_MAX_TEXT_LEN * 2]
        
        # Validate finding_id
        fid = result.get("finding_id")
        if not fid or not isinstance(fid, str) or len(fid) > 1024:
            logger.debug("[FIX-4] Search result has invalid finding_id")
            return None
        
        return result

    def _validate_search_results_batch(self, results: list[dict]) -> list[dict]:
        """FIX-4: Validate batch of search results, filtering out invalid ones."""
        validated = []
        for r in results:
            safe_r = self._validate_search_result(r)
            if safe_r is not None:
                validated.append(safe_r)
        if len(validated) < len(results):
            logger.debug("[FIX-4] Filtered %d invalid search results", len(results) - len(validated))
        return validated

    # -------------------------------------------------------------------------
    # Semantic pivot — ANN search
    # -------------------------------------------------------------------------

    async def semantic_pivot(
        self, query: str, top_k: int = 10
    ) -> list[dict]:
        """
        ANN search — vrátí top-k sémanticky podobných findings.

        SWARM-002: Language-aware routing:
        - English queries → search english_index (ModernBERT)
        - Non-English queries → search multilingual_index (BGE-M3)
        - Cross-lingual: both indexes if query language detection uncertain

        Uses cosine metric (LanceDB converts L2 distance internally).
        Returns list of dicts with keys: text, source_type, finding_id, ts,
        ioc_types, score (0.0–1.0 where 1.0 = identical), language.
        """
        # SWARM-002: Detect query language
        query_lang = None
        if self._multilingual_enabled and self._lang_detector is not None:
            try:
                query_lang = self._lang_detector.detect(query)
            except Exception:  # noqa: BLE001
                pass

        is_english_query = query_lang is not None and query_lang.is_english

        # Get query embedding based on language
        if is_english_query:
            query_vector = await self._embed_query_english(query)
        else:
            query_vector = await self._embed_query_multilingual(query)

        all_results = []

        # Search English index for English queries or cross-lingual
        if is_english_query and self._table is not None:
            english_results = await self._search_english_index(query_vector, top_k)
            all_results.extend(english_results)

        # Search multilingual index for non-English queries or cross-lingual
        if not is_english_query and self._table_multilingual is not None:
            multilingual_results = await self._search_multilingual_index(query_vector, top_k)
            all_results.extend(multilingual_results)

        # Sort by score and return top-k
        all_results.sort(key=itemgetter("score"), reverse=True)
        return all_results[:top_k]

    async def _embed_query_english(self, query: str) -> np.ndarray:
        """Embed English query via ModernBERT/MLX."""
        loop = asyncio.get_running_loop()

        mlx_mgr = self._mlx_embedder
        if mlx_mgr is not None:
            try:
                def single_encode(manager, text: str) -> np.ndarray:
                    return manager.encode([text], normalize=True)

                result = await loop.run_in_executor(
                    None, lambda: single_encode(mlx_mgr, query)
                )
                return self._ensure_dim(result, self._embed_dim)[0]
            except Exception:  # noqa: BLE001
                pass

        if self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            try:
                emb = await loop.run_in_executor(
                    None, lambda: self._coreml_embedder.embed([query], batch_size=1)  # type: ignore[union-attr]
                )
                return self._ensure_dim(emb, self._embed_dim)[0]
            except Exception:  # noqa: BLE001
                pass

        # Hash fallback
        return self._hash_fallback_embeddings([query])[0]

    async def _embed_query_multilingual(self, query: str) -> np.ndarray:
        """Embed multilingual query via BGE-M3."""
        if not self._multilingual_enabled or self._bge_m3_embedder is None:
            return self._hash_fallback_embeddings([query])[0]

        try:
            # BGE-M3 embed is async, call directly
            return await self._bge_m3_embedder.embed(query, truncate_to=self._embed_dim)
        except Exception as e:
            logger.warning(f"[SEMSTORE] BGE-M3 query embed failed: {e}")
            return self._hash_fallback_embeddings([query])[0]

    async def _search_english_index(
        self, query_vector: np.ndarray, top_k: int
    ) -> list[dict]:
        """Search English LanceDB index."""
        if self._table is not None:
            try:
                _qv = cast("LanceVectorQueryBuilder", self._table.search(query_vector))
                results = (
                    _qv.metric("cosine")
                    .limit(top_k)
                    .to_list()
                )
                # FIX-4: Transform results before validation
                transformed = [
                    {
                        "text": r["text"],
                        "source_type": r["source_type"],
                        "finding_id": r["finding_id"],
                        "ts": r["ts"],
                        "ioc_types": r["ioc_types"],
                        "score": 1.0 - r["_distance"],
                        "language": "en",
                    }
                    for r in results
                ]
                # FIX-4: Validate all results
                return self._validate_search_results_batch(transformed)
            except Exception as e:
                logger.warning("[SEMSTORE] LanceDB English ANN search failed: %s", e)
                return []

        elif self._vec_db is not None:
            try:
                rows = self._vec_db.execute(
                    f"SELECT finding_id, text, source_type, ts, ioc_types, "
                    f"vec_distance_cosine(embedding, ?) AS score "
                    f"FROM {_TABLE_NAME} ORDER BY score DESC LIMIT ?",
                    [query_vector.tolist(), top_k],
                ).fetchall()
                transformed = [
                    {
                        "text": r[1],
                        "source_type": r[2],
                        "finding_id": r[0],
                        "ts": r[3],
                        "ioc_types": r[4] or "",
                        "score": r[5],
                        "language": "en",
                    }
                    for r in rows
                ]
                # FIX-4: Validate all results
                return self._validate_search_results_batch(transformed)
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec English ANN search failed: %s", e)
                return []

        return []

    async def _search_multilingual_index(
        self, query_vector: np.ndarray, top_k: int
    ) -> list[dict]:
        """SWARM-002: Search multilingual LanceDB index."""
        if self._table_multilingual is not None:
            try:
                _qv = cast("LanceVectorQueryBuilder", self._table_multilingual.search(query_vector))
                results = (
                    _qv.metric("cosine")
                    .limit(top_k)
                    .to_list()
                )
                transformed = [
                    {
                        "text": r["text"],
                        "source_type": r["source_type"],
                        "finding_id": r["finding_id"],
                        "ts": r["ts"],
                        "ioc_types": r["ioc_types"],
                        "score": 1.0 - r["_distance"],
                        "language": r.get("language", "unknown"),
                    }
                    for r in results
                ]
                # FIX-4: Validate all results
                return self._validate_search_results_batch(transformed)
            except Exception as e:
                logger.warning("[SEMSTORE] LanceDB multilingual ANN search failed: %s", e)
                return []

        elif self._vec_db_multilingual is not None:
            try:
                rows = self._vec_db_multilingual.execute(
                    f"SELECT finding_id, text, source_type, ts, ioc_types, language, "
                    f"vec_distance_cosine(embedding, ?) AS score "
                    f"FROM {_TABLE_NAME_MULTILINGUAL} ORDER BY score DESC LIMIT ?",
                    [query_vector.tolist(), top_k],
                ).fetchall()
                transformed = [
                    {
                        "text": r[1],
                        "source_type": r[2],
                        "finding_id": r[0],
                        "ts": r[3],
                        "ioc_types": r[4] or "",
                        "score": r[6],
                        "language": r[5] or "unknown",
                    }
                    for r in rows
                ]
                # FIX-4: Validate all results
                return self._validate_search_results_batch(transformed)
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec multilingual ANN search failed: %s", e)
                return []

        return []

    # -------------------------------------------------------------------------
    # Embed query (direct, no buffer)
    # -------------------------------------------------------------------------

    async def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a single query string — uses MLX path if available.

        SWARM-002: Detects query language and routes to appropriate embedder.

        Returns:
            ndarray dtype=float32, shape=(256,)
        """
        # SWARM-002: Language detection
        query_lang = None
        if self._multilingual_enabled and self._lang_detector is not None:
            try:
                query_lang = self._lang_detector.detect(query)
            except Exception:  # noqa: BLE001
                pass

        if query_lang is not None and query_lang.is_english:
            return await self._embed_query_english(query)
        else:
            return await self._embed_query_multilingual(query)

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def get_buffer_stats(self) -> dict[str, Any]:
        """
        Return buffer statistics for observability.

        SAFE-4: Exposes buffer overflow metrics to detect silent data loss.
        Call this periodically or on flush to monitor OSINT evidence integrity.

        Returns:
            dict with keys:
            - pending_count: current pending items in buffer
            - max_pending: configured maximum buffer size
            - overflow_drops: total items dropped due to buffer overflow since init
            - embed_validation_stats: SAFE-2.5 validation metrics
            - ann_backend: currently active ANN backend (F2 FIX)
        """
        return {
            'pending_count': len(self._pending_texts),
            'max_pending': _MAX_PENDING,
            'overflow_drops': self._buffer_overflow_drops,
            'embed_validation_stats': self._embed_validation_stats.copy(),
            'ann_backend': getattr(self, '_ann_backend', 'unknown'),
        }

    async def close(self) -> None:
        """TEARDOWN — final flush + close connections."""
        await self.flush()
        self._model = None
        # Sprint F228B: unload CoreMLEmbedder
        if self._coreml_embedder is not None:
            self._coreml_embedder.unload()
        # SWARM-002: unload BGE-M3
        if self._bge_m3_embedder is not None:
            try:
                self._bge_m3_embedder.unload()
            except Exception:  # noqa: BLE001
                pass
        self._table = None
        self._table_multilingual = None
        if self._db is not None:
            try:
                close_fn = getattr(self._db, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:  # noqa: BLE001
                pass
            self._db = None
        # Issue 4.3: sqlite-vec fallback close
        if self._vec_db is not None:
            try:
                self._vec_db.close()
            except Exception:  # noqa: BLE001
                pass
            self._vec_db = None
        # SWARM-002: sqlite-vec multilingual fallback close
        if self._vec_db_multilingual is not None:
            try:
                self._vec_db_multilingual.close()
            except Exception:  # noqa: BLE001
                pass
            self._vec_db_multilingual = None
        self._initialized = False
        logger.info("SemanticStore closed")
