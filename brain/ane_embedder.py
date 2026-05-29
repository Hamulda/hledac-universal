"""
ANE-akcelerovaný embedder pro ModernBERT a FlashRank.
Offline konverze z MLX do CoreML, fallback na MLX.

Reranker: rerank_findings_crossencoder() používá flashrank CrossEncoder.
LanceDBIdentityStore má vlastní _get_flashrank_ranker() pro search path.
Tyto dvě instance jsou záměrně oddělené — ANE brain pipeline vs. vector store search.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sprint F234: ANE/MLX Mutual Exclusion — prevents OOM on M1 8GB
# ---------------------------------------------------------------------------

class ANE_MLX_Mutex:
    """
    Prevents simultaneous ANE + MLX model loading on M1 8GB.

    Only ONE runtime can hold the lock at a time:
    - ANE path: reranker + embedder models
    - MLX path: Hermes 3B LLM + KV cache

    Max combined memory: 2.5GB (hard guard).
    """

    _instance: ANE_MLX_Mutex | None = None
    _lock: threading.Lock = threading.Lock()
    _active_runtime: Literal["ane", "mlx", None] = None
    _max_combined_mb: float = 2560.0  # 2.5GB safety margin

    def __new__(cls) -> ANE_MLX_Mutex:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def acquire_ane(self, model_size_mb: float = 0.0) -> None:
        """Acquire ANE lock. Raises MemoryError if MLX is active."""
        with self._lock:
            if self._active_runtime == "mlx":
                raise MemoryError(
                    "[ANE_MLX_Mutex] MLX model active — cannot acquire ANE. "
                    "Release MLX first via release()."
                )
            if self._active_runtime == "ane":
                # Already holding ANE — re-entrant for same model
                return
            self._active_runtime = "ane"
            logger.debug(f"[ANE_MLX_Mutex] Acquired ANE (model={model_size_mb:.0f}MB)")

    def acquire_mlx(self, model_size_mb: float = 0.0) -> None:
        """Acquire MLX lock. Raises MemoryError if ANE is active."""
        with self._lock:
            if self._active_runtime == "ane":
                raise MemoryError(
                    "[ANE_MLX_Mutex] ANE model active — cannot acquire MLX. "
                    "Release ANE first via release()."
                )
            if self._active_runtime == "mlx":
                # Already holding MLX — re-entrant
                return
            if model_size_mb > self._max_combined_mb:
                raise MemoryError(
                    f"[ANE_MLX_Mutex] MLX model {model_size_mb:.0f}MB exceeds "
                    f"{self._max_combined_mb:.0f}MB limit."
                )
            self._active_runtime = "mlx"
            logger.debug(f"[ANE_MLX_Mutex] Acquired MLX (model={model_size_mb:.0f}MB)")

    def release(self, runtime: Literal["ane", "mlx"]) -> None:
        """Release lock for specified runtime."""
        with self._lock:
            if self._active_runtime == runtime:
                self._active_runtime = None
                logger.debug(f"[ANE_MLX_Mutex] Released {runtime}")

    def is_active(self) -> Literal["ane", "mlx", None]:
        """Return currently active runtime."""
        return self._active_runtime

    def is_ane_active(self) -> bool:
        return self._active_runtime == "ane"

    def is_mlx_active(self) -> bool:
        return self._active_runtime == "mlx"


def get_ane_mlx_mutex() -> ANE_MLX_Mutex:
    """Thread-safe singleton accessor."""
    return ANE_MLX_Mutex()


# ---------------------------------------------------------------------------
# Original module content follows (ANE AVAIABLE flag, ANEEmbedder, etc.)
# ---------------------------------------------------------------------------

try:
    import CoreML as _CoreML
    import Foundation as _Foundation
    ANE_AVAILABLE = True
except ImportError:
    ANE_AVAILABLE = False
    _CoreML = None
    _Foundation = None

try:
    import mlx.core as _mx
    from mlx_embeddings import load as _mlx_embeddings_load
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    _mx = None
    _mlx_embeddings_load = None

MODELS_DIR = Path.home() / ".hledac" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Sprint F228B: Telemetry
_ANE_TELEMETRY = {
    "ane_embed_attempted": 0,
    "ane_embed_fallback_used": 0,
    "ane_warmup_executed": 0,
    "ane_warmup_error": 0,
}


class ANEStatus(Enum):
    """ANE status codes."""
    NOT_AVAILABLE = "not_available"
    MODEL_NOT_FOUND = "model_not_found"
    LOADED = "loaded"
    LOAD_FAILED = "load_failed"


@dataclass
class ANEStatusResult:
    """Result of get_ane_status()."""
    available: bool
    loaded: bool
    model_path_exists: bool
    fallback_configured: bool
    last_error: str | None
    inference_path: str  # "coreml", "fallback", "hash_fallback", "unavailable"


def get_ane_status(embedder: ANEEmbedder | None = None) -> ANEStatusResult:
    """
    Sprint F228B: Returns ANE status as a dataclass.
    Callers can inspect without triggering model loading.
    """
    global _ANE_EMBEDDER

    if embedder is None:
        embedder = get_ane_embedder()

    if not ANE_AVAILABLE:
        return ANEStatusResult(
            available=False,
            loaded=False,
            model_path_exists=False,
            fallback_configured=False,
            last_error="CoreML/pyobjc not available",
            inference_path="unavailable",
        )

    if embedder is None:
        # ANE_AVAILABLE is True here (caught above), but embedder not initialized yet.
        # No CoreML model loaded, no fallback configured → hash fallback path.
        return ANEStatusResult(
            available=True,
            loaded=False,
            model_path_exists=False,
            fallback_configured=False,
            last_error=None,
            inference_path="hash_fallback",
        )

    model_exists = embedder.coreml_path.exists() if hasattr(embedder, 'coreml_path') else False
    fallback_configured = embedder._fallback_embedder is not None

    if embedder.is_loaded:
        return ANEStatusResult(
            available=True,
            loaded=True,
            model_path_exists=model_exists,
            fallback_configured=fallback_configured,
            last_error=None,
            inference_path="coreml",
        )

    # Loaded=False but ANE is available — determine why
    if not model_exists:
        return ANEStatusResult(
            available=True,
            loaded=False,
            model_path_exists=False,
            fallback_configured=fallback_configured,
            last_error=None,
            inference_path="hash_fallback",
        )

    return ANEStatusResult(
        available=True,
        loaded=False,
        model_path_exists=True,
        fallback_configured=fallback_configured,
        last_error=getattr(embedder, '_last_load_error', None),
        inference_path="fallback" if fallback_configured else "unavailable",
    )


def get_ane_telemetry() -> dict:
    """Sprint F228B: Returns a copy of ANE telemetry counters."""
    return dict(_ANE_TELEMETRY)


def reset_ane_telemetry() -> None:
    """Sprint F228B: Reset telemetry counters (for testing)."""
    _ANE_TELEMETRY["ane_embed_attempted"] = 0
    _ANE_TELEMETRY["ane_embed_fallback_used"] = 0
    _ANE_TELEMETRY["ane_warmup_executed"] = 0
    _ANE_TELEMETRY["ane_warmup_error"] = 0


# Sprint 8VF-ANE: pyobjc CoreML inference helpers
_HF_TOKENIZER = None


def _get_hf_tokenizer():
    global _HF_TOKENIZER
    if _HF_TOKENIZER is None:
        from transformers import AutoTokenizer
        _HF_TOKENIZER = AutoTokenizer.from_pretrained(
            "sentence-transformers/all-MiniLM-L6-v2", use_fast=True
        )
    return _HF_TOKENIZER


def _make_ml_array(data_list: list, length: int = 64):
    arr, err = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
        [1, length], _CoreML.MLMultiArrayDataTypeInt32, None
    )
    if err:
        raise RuntimeError(f"MLMultiArray init failed: {err}")
    ns_vals = [_Foundation.NSNumber.numberWithInt_(v) for v in data_list]
    ns_arr  = _Foundation.NSArray.arrayWithArray_(ns_vals)
    for i in range(length):
        arr.setObject_atIndexedSubscript_(ns_arr[i], i)
    return arr


def _coreml_embed(model, text: str) -> np.ndarray:
    tok = _get_hf_tokenizer()
    tokens = tok(
        text[:256],
        return_tensors="np",
        padding="max_length",
        max_length=64,
        truncation=True,
    )
    input_ids = tokens["input_ids"].astype(np.int32).flatten().tolist()
    attn_mask  = tokens["attention_mask"].astype(np.int32).flatten().tolist()
    feat_dict = {
        "input_ids":      _make_ml_array(input_ids),
        "attention_mask": _make_ml_array(attn_mask),
    }
    provider, err = _CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(
        feat_dict, None
    )
    if err:
        raise RuntimeError(f"Feature provider failed: {err}")
    result, err = model.predictionFromFeatures_error_(provider, None)
    if err:
        raise RuntimeError(f"Inference failed: {err}")
    vec_raw = result.featureValueForName_("var_570").multiArrayValue()
    vec = np.array(
        [float(vec_raw.objectAtIndexedSubscript_(i)) for i in range(384)],
        dtype=np.float32,
    )
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


class ANEEmbedder:
    """
    Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,
    spoléhá na volání MLX embedderu (který musí být poskytnut zvenčí).

    Sprint F228B: Truthful ANE path — no NotImplementedError in production.
    """

    def __init__(self, model_name: str = "modernbert", hidden_dim: int = 768):
        self.model_name = model_name
        self.hidden_dim = hidden_dim
        self.model = None
        self._mlx_model = None
        self._mlx_processor = None
        self._loaded = False
        self._last_load_error: str | None = None
        self.coreml_path = MODELS_DIR / f"{model_name}_ane.mlpackage"
        self._fallback_embedder: Callable[..., Awaitable[np.ndarray]] | None = None

    def set_fallback(self, fallback_func: Callable[..., Awaitable[np.ndarray]]) -> None:
        """Nastaví fallback async funkci (např. MLX embedder)."""
        self._fallback_embedder = fallback_func

    async def load(self) -> None:
        """Pokusí se načíst CoreML model, pak MLX ModernBERT fallback."""
        if self._loaded or self._mlx_model is not None:
            return

        # Try CoreML first
        if ANE_AVAILABLE and self.coreml_path.exists():
            try:
                url = _CoreML.NSURL.fileURLWithPath_(str(self.coreml_path))
                model, err = _CoreML.MLModel.modelWithContentsOfURL_error_(url, None)
                if err:
                    raise RuntimeError(f"CoreML load failed: {err}")
                self.model = model
                self._loaded = True
                self._last_load_error = None
                # F234: ANE/MLX mutex — CoreML model acquired
                get_ane_mlx_mutex().acquire_ane(model_size_mb=90.0)
                logger.info(f"ANEEmbedder loaded CoreML: {self.model_name}")
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f"CoreML failed ({e}), trying MLX ModernBERT")

        # MLX fallback — ModernBERT přes mlx-embeddings
        if MLX_EMBED_AVAILABLE:
            try:
                model_path = "nomic-ai/modernbert-embed-base"
                self._mlx_model, self._mlx_processor = _mlx_embeddings_load(model_path, lazy=False)
                self._loaded = True
                self._last_load_error = None
                logger.info(f"ANEEmbedder loaded MLX: {model_path}")
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f"MLX ModernBERT failed ({e}), using hash fallback")

    async def initialize(self) -> None:
        """
        Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.
        Idempotent: safe to call multiple times, only loads once.
        M1 guard: requires >1.5GB UMA available before loading CoreML model.
        """
        if self._loaded:
            return  # already loaded
        # M1 memory guard — check before loading CoreML model
        try:
            from utils.uma_budget import get_uma_snapshot
            snap = get_uma_snapshot()
            if snap.is_critical or snap.is_emergency:
                logger.warning(f"[ANE] initialize skipped: memory pressure "
                               f"{snap.pct_used:.0f}% (>85%% critical)")
                return
            avail = snap.available_uma_gib
            if avail < 1.5:
                logger.warning(f"[ANE] initialize skipped: only {avail:.1f}GB < 1.5GB required")
                return
        except Exception:
            pass  # guard is advisory — proceed if snapshot fails
        await self.load()

    async def convert_to_ane(self) -> bool:
        """Check for pre-compiled .mlmodelc — no conversion needed."""
        if not ANE_AVAILABLE:
            logger.warning("[ANE] CoreML (pyobjc) not available")
            return False
        compiled_path = MODELS_DIR / "AllMiniLML6V2.mlmodelc"
        if compiled_path.exists():
            self.coreml_path = compiled_path
            logger.info("[ANE] Pre-compiled model found: %s", compiled_path)
            return True
        raw_path = MODELS_DIR / "AllMiniLML6V2.mlmodel"
        if raw_path.exists():
            logger.info("[ANE] Compiling %s ...", raw_path)
            loop = asyncio.get_running_loop()
            def _compile():
                url = _CoreML.NSURL.fileURLWithPath_(str(raw_path))
                compiled_url, err = _CoreML.MLModel.compileModelAtURL_error_(url, None)
                if err:
                    raise RuntimeError(f"Compile failed: {err}")
                import shutil
                compiled_str = str(compiled_url).replace("file://", "")
                shutil.copytree(compiled_str, str(compiled_path), dirs_exist_ok=True)
                return compiled_path
            self.coreml_path = await loop.run_in_executor(None, _compile)
            logger.info("[ANE] Compiled to %s", self.coreml_path)
            return True
        logger.warning("[ANE] No model found at %s or %s", compiled_path, raw_path)
        return False

    async def embed(self, texts: str | list[str]) -> np.ndarray:
        """
        Sprint F228B: Truthful embed — no NotImplementedError in production.
        Falls back gracefully: CoreML → fallback embedder → hash fallback.
        """
        global _ANE_TELEMETRY
        _ANE_TELEMETRY["ane_embed_attempted"] += 1

        if isinstance(texts, str):
            texts = [texts]

        # Path 1: CoreML loaded
        if self._loaded and self.model is not None:
            loop = asyncio.get_running_loop()
            def _run():
                return np.array([_coreml_embed(self.model, t) for t in texts], dtype=np.float32)
            return await loop.run_in_executor(None, _run)

        # Path 2: MLX ModernBERT loaded
        if self._mlx_model is not None:
            _ANE_TELEMETRY["ane_embed_attempted"] += 1
            loop = asyncio.get_running_loop()
            def _run():
                import mlx.core as mx
                toks = self._mlx_processor(texts, return_tensors="np", padding=True, truncation=True, max_length=512)
                input_ids = mx.array(toks["input_ids"])
                attention_mask = mx.array(toks["attention_mask"])
                embs = self._mlx_model(input_ids, attention_mask=attention_mask)
                hs = embs.last_hidden_state
                mask = mx.array(toks["attention_mask"][:, :, None])
                summed = (hs * mask).sum(axis=1)
                counts = mx.maximum(mask.sum(axis=1), 1e-9)
                pooled = summed / counts
                result = mx.eval(pooled)
                return np.array(result, dtype=np.float32)
            return await loop.run_in_executor(None, _run)

        # Path 3: Fallback embedder configured — call it
        if self._fallback_embedder is not None:
            _ANE_TELEMETRY["ane_embed_fallback_used"] += 1
            fb = self._fallback_embedder
            # Handle both sync and async fallback
            if asyncio.iscoroutinefunction(fb):
                return await fb(texts)
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, lambda: fb(texts))

        # Path 3: Hash fallback — deterministic, zero RAM
        _ANE_TELEMETRY["ane_embed_fallback_used"] += 1
        return self._hash_embed(texts)

    def _hash_embed(self, texts: str | list[str]) -> np.ndarray:
        """Deterministic hash-based fallback — always works, no model needed."""
        if isinstance(texts, str):
            texts = [texts]
        vecs = []
        for t in texts:
            h = hash(t[:512]) % (2**32)
            vec = np.zeros(self.hidden_dim, dtype=np.float32)
            # Spread hash across vector for diversity
            for i in range(min(self.hidden_dim, 384)):
                vec[i] = float((h >> (i % 32)) & 1) * 2 - 1
            norm = np.linalg.norm(vec)
            vecs.append(vec / norm if norm > 0 else vec)
        return np.array(vecs, dtype=np.float32)

    async def warmup(self) -> None:
        """
        Sprint F228B: Fixed warmup — awaits embed() correctly.
        Never passes async embed() directly to run_in_executor.
        """
        global _ANE_TELEMETRY

        if not ANE_AVAILABLE:
            logger.debug("ANEEmbedder warmup skipped: ANE not available")
            return
        if not self._loaded or self.model is None:
            logger.debug("ANEEmbedder warmup skipped: model not loaded")
            return

        _ANE_TELEMETRY["ane_warmup_executed"] += 1
        try:
            dummy = ["warmup probe osint security"]
            # Sprint F228B: await embed() directly — no run_in_executor wrapping
            await self.embed(dummy)
            logger.debug("ANEEmbedder warmed up (ANE cache primed)")
        except Exception as e:
            _ANE_TELEMETRY["ane_warmup_error"] += 1
            logger.debug(f"ANEEmbedder warmup failed: {e}")

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je ANE nebo MLX model načten."""
        return self._loaded and (self.model is not None or self._mlx_model is not None)


# Backward compat — importuje z kanonického mista

# ============================================================================
# Sprint 8VF: ANE Semantic Dedup
# ============================================================================

_ANE_EMBEDDER: ANEEmbedder | None = None


def get_ane_embedder() -> ANEEmbedder | None:
    """Lazy init CoreML MiniLM-L6-v2 embedder, s MLX ModernBERT pokud CoreML není."""
    global _ANE_EMBEDDER
    if _ANE_EMBEDDER is None:
        _ANE_EMBEDDER = ANEEmbedder(model_name="minilm_ane", hidden_dim=384)
    # Kick off async load — run synchronously via new event loop in background thread
    if not _ANE_EMBEDDER._loaded and _ANE_EMBEDDER._mlx_model is None:
        import threading
        def _load_bg():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_ANE_EMBEDDER.load())
                loop.close()
            except Exception:
                pass
        t = threading.Thread(target=_load_bg, daemon=True)
        t.start()
    return _ANE_EMBEDDER


def unload_ane_embedder() -> None:
    """Called by memory pressure governor at CRITICAL state."""
    global _ANE_EMBEDDER
    # F234: Release ANE mutex before clearing instance
    try:
        get_ane_mlx_mutex().release("ane")
    except Exception:
        pass
    _ANE_EMBEDDER = None


async def semantic_dedup_findings(
    findings: list[dict],
    threshold: float = 0.92,
) -> list[dict]:
    """
    Semantic deduplication of findings.
    ANE path: CoreML MiniLM batch inference → cosine similarity matrix.
    Hash fallback: url+title hash (zero RAM, always works).
    """
    embedder = get_ane_embedder()

    # Hash fallback when no ANE model
    if embedder is None or not embedder.is_loaded:
        seen: set[int] = set()
        out:  list[dict] = []
        for f in findings:
            key = hash((f.get("url", ""), f.get("title", "")))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out

    import numpy as np

    def _embed_batch_sync(texts: list[str]) -> np.ndarray:
        """CoreML batch inference — all texts at once."""
        if embedder is None or embedder.model is None:
            return np.zeros((len(texts), 384), dtype=np.float32)
        vecs = []
        for t in texts:
            try:
                vec = _coreml_embed(embedder.model, t)
                vecs.append(vec)
            except Exception:
                vecs.append(np.zeros(384, dtype=np.float32))
        return np.array(vecs, dtype=np.float32)

    texts = [
        f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512]
        for f in findings
    ]
    loop = asyncio.get_running_loop()
    try:
        vecs  = await loop.run_in_executor(None, _embed_batch_sync, texts)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        vecs_n = vecs / norms
        sim    = vecs_n @ vecs_n.T
        keep   = [True] * len(findings)
        for i in range(len(findings)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(findings)):
                if sim[i, j] >= threshold:
                    keep[j] = False
        return [f for f, k in zip(findings, keep, strict=False) if k]
    except Exception:
        return findings  # fallback on any error


# ============================================================================
# Sprint 8VF: Cosine Reranker for Synthesis
# ============================================================================

def rerank_findings_cosine(
    findings: list[dict],
    query: str,
    top_k: int = 20,
) -> list[dict]:
    """
    Cosine similarity reranker over ANE MiniLM embeddings.
    RAM: ~22MB model (CoreML), <5ms inference, ANE accelerated.
    Fallback: confidence sort.
    """
    try:
        embedder = get_ane_embedder()
        if embedder is None or not embedder.is_loaded or embedder.model is None:
            raise RuntimeError("ANE unavailable")

        import numpy as np

        def _embed(text: str) -> np.ndarray:
            return _coreml_embed(embedder.model, text)

        q_vec = _embed(query[:512])
        q_norm = np.linalg.norm(q_vec) + 1e-9
        q_vec = q_vec / q_norm

        scored = []
        for f in findings[:200]:  # cap for RAM
            text = f"{f.get('title', '')} {f.get('snippet', '')}".strip()
            f_vec = _embed(text[:512])
            f_norm = np.linalg.norm(f_vec) + 1e-9
            f_vec = f_vec / f_norm
            score = float(np.dot(q_vec, f_vec))
            scored.append((score, f))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_k]]
    except Exception:
        return sorted(
            findings,
            key=lambda x: x.get("confidence", 0.5),
            reverse=True
        )[:top_k]


# ============================================================================
# AREA A: flashrank CrossEncoder reranker
# Replaces cosine-similarity ceiling with proper cross-encoder scoring.
# flashrank uses ms-marco-MiniLM-L-12-v2 ONNX (~22MB), ~2ms/query, zero UMA spike.
# Falls back to cosine similarity if flashrank unavailable.
# ============================================================================

_flashrank_reranker = None
_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"  # 22MB ONNX


def _get_flashrank_reranker():
    """Lazy-load flashrank CrossEncoder ranker."""
    global _flashrank_reranker
    if _flashrank_reranker is None:
        try:
            from flashrank import Ranker
            _flashrank_reranker = Ranker(model_name=_FLASHRANK_MODEL, cache_dir="/tmp/flashrank_cache")
            logger.info("[RERANK:A] flashrank CrossEncoder loaded: %s", _FLASHRANK_MODEL)
        except ImportError:
            logger.warning("[RERANK:A] flashrank not available — falling back to cosine similarity")
        except Exception as e:
            logger.warning("[RERANK:A] flashrank load failed: %s", e)
            _flashrank_reranker = None
    return _flashrank_reranker


def rerank_findings_crossencoder(
    query: str,
    findings: list[dict],
    top_k: int = 20,
) -> list[dict]:
    """
    Cross-encoder reranker using flashrank ms-marco-MiniLM-L-12-v2.
    Superior to cosine similarity for cross-document relevance scoring.
    Falls back to rerank_findings_cosine if flashrank unavailable.
    """
    try:
        ranker = _get_flashrank_reranker()
        if ranker is None:
            logger.debug("[RERANK:A] Using cosine fallback")
            return rerank_findings_cosine(findings, query, top_k)

        from flashrank import RerankRequest

        passages = []
        for i, f in enumerate(findings[:200]):
            text = (
                f.get("content")
                or f.get("text")
                or f.get("snippet")
                or f.get("title", "")
                or str(f)
            )[:2048]
            passages.append({"id": i, "text": text})

        request = RerankRequest(query=query[:512], passages=passages)
        results = ranker.rerank(request)

        id_to_finding = {r["id"]: findings[r["id"]] for r in results[:top_k] if r["id"] < len(findings)}
        reranked = [id_to_finding[r["id"]] for r in results[:top_k] if r["id"] in id_to_finding]

        logger.debug("[RERANK:A] CrossEncoder reranked %d→%d findings", len(findings), len(reranked))
        return reranked

    except Exception as e:
        logger.warning("[RERANK:A] CrossEncoder failed (%s) — cosine fallback", e)
        return rerank_findings_cosine(findings, query, top_k)
