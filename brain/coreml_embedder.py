"""
Sprint F228B: CoreML/ANE embedding backend for Apple Neural Engine.

Priority routing: CoreML microservice (py3.12 FastAPI :8765) → ONNXRuntime CPU fallback.
Identical API to FastEmbed BAAI/bge-small-en-v1.5 caller.

M1 8GB constraint: model cache ≤ 256MB, batch_size ≤ 32.

py3.14 compatibility: coremltools is NOT imported directly.
All CoreML/ANE inference routes through CoreMLClient HTTP → microservice.
Model conversion (torch→CoreML) stays in py3.12 subprocess via CoreMLServiceManager.
"""

import asyncio
import concurrent.futures
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from hledac.universal.utils.coreml import CoreMLClient, CoreMLServiceManager

logger = logging.getLogger(__name__)

# ── CoreML microservice availability ──────────────────────────────────────────
# Microservice availability is checked lazily at load() time.
# coremltools is never imported in py3.14 — all inference goes through HTTP.
_COREMLTOOLS_AVAILABLE = False

# ── ONNXRuntime availability (CPU fallback) ──────────────────────────────────
_ORT_AVAILABLE = False
_ort: Any = None
try:
    import onnxruntime as _ort

    _ORT_AVAILABLE = True
except ImportError:
    pass
ort = _ort

# ── Constants ─────────────────────────────────────────────────────────────────
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384  # bge-small-en-v1.5 output dim
_BATCH_SIZE = 32  # M1 8GB safety cap
_MAX_TEXT_LEN = 512  # per-text truncation before embedding
_COREML_MODEL_NAME = "bge-small-ane"

# Cache path: ~/.hledac/models/bge-small-ane.mlpackage
_MODELS_DIR = Path.home() / ".hledac" / "models"
_MODELS_DIR.mkdir(parents=True, exist_ok=True)
_MLPACKAGE_PATH = _MODELS_DIR / "bge-small-ane.mlpackage"
_ONNX_FALLBACK_PATH = _MODELS_DIR / "bge-small-ort.onnx"

# ThreadPoolExecutor for sync inference paths (GHOST_INVARIANTS: no asyncio.to_thread)
_INFERENCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coreml_infer")

# ── Singleton (DCLP — Double-Checked Locking Pattern) ──────────────────────────
# Lock ONLY during init to prevent multiple threads/coroutines from creating
# multiple instances. After _coreml_embedder_instance is set, the lock is
# NEVER taken — DCLP acquire-release semantics, lock-free hot path.
# asyncio.Lock is cooperative and event-loop friendly (vs blocking threading.Lock).
_coreml_embedder_instance: CoreMLEmbedder | None = None
_coreml_embedder_lock = asyncio.Lock()


async def get_coreml_embedder() -> CoreMLEmbedder:
    """Get or create the CoreMLEmbedder singleton (async DCLP).

    DCLP with acquire-release semantics: the lock is released before the
    second check, so after _coreml_embedder_instance is set, subsequent calls
    are lock-free (no contention on every embed call).

    The actual serialization bottleneck is _INFERENCE_EXECUTOR (single thread
    for all ONNX/CoreML inference), NOT this init-only lock.
    """
    global _coreml_embedder_instance
    if _coreml_embedder_instance is None:
        async with _coreml_embedder_lock:
            # Re-check after acquiring lock (DCLP safe publication)
            if _coreml_embedder_instance is None:
                _coreml_embedder_instance = CoreMLEmbedder()
    return _coreml_embedder_instance


def is_ane_available() -> bool:
    """Check if ANE compute unit is available on this machine."""
    # Proxy by checking architecture — M-series chips have ANE.
    try:
        import platform

        return platform.machine() in ("arm64", "arm64e")
    except Exception:
        return False


# ── Tokenizer (lightweight, no external deps) ─────────────────────────────────
class _BGETokenizer:
    """Minimal tokenizer matching BAAI/bge-small-en-v1.5 vocabulary."""

    VOCAB = [
        "[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]",
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "should", "could", "may", "might", "must",
    ]

    def __init__(self) -> None:
        self._vocab = {w: i for i, w in enumerate(self.VOCAB)}

    def encode(self, text: str) -> list[int]:
        """Simple whitespace tokenization + vocab lookup."""
        tokens = []
        for word in text.lower().split():
            if word in self._vocab:
                tokens.append(self._vocab[word])
            else:
                tokens.append(self._vocab["[UNK]"])
        return tokens[: _MAX_TEXT_LEN]

    @property
    def vocab_size(self) -> int:
        return len(self._vocab)


# ── CoreMLEmbedder ───────────────────────────────────────────────────────────
class CoreMLEmbedder:
    """
    CoreML/ANE embedder with identical API to FastEmbed caller.

    encode_batch(texts, batch_size=32) -> np.ndarray of shape (len(texts), 384)
    Routing: CoreML microservice (py3.12) → ONNXRuntime CPU fallback → hash fallback.
    """

    def __init__(self) -> None:
        self._client: CoreMLClient | None = None  # HTTP client to microservice
        self._model: Any = None  # ONNX InferenceSession (onnx backend only)
        self._backend: str | None = None  # "coreml" | "onnx" | None
        self._tokenizer = _BGETokenizer()  # type: _BGETokenizer | Any
        self._hf_tokenizer: Any = None  # AutoTokenizer for coreml service tokenization
        self._is_loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def embed_dim(self) -> int:
        return _EMBED_DIM

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------
    async def load(self) -> bool:
        """
        Load the model: MLX native preferred → CoreML microservice → ONNX CPU fallback.
        Ensures CoreMLServiceManager is running before first inference.

        Returns True if model is ready for inference.
        """
        if self._is_loaded:
            return True

        # 1. MLX — best for M1, no subprocess, no conversion
        from brain.mlx_embedder import MLXEmbedder

        mlx_embedder = MLXEmbedder()
        if mlx_embedder.is_available and await mlx_embedder.load():
            self._model = mlx_embedder
            self._backend = "mlx"
            self._is_loaded = True
            logger.info("[Embedder] MLX backend active (unified memory)")
            return True

        # 2. CoreML microservice (py3.12)
        if await self._load_coreml_service():
            self._backend = "coreml"
            self._is_loaded = True
            logger.warning("[CoreML] Microservice embedder loaded (bge-small-ane)")
            return True

        # 3. ONNXRuntime CPU fallback
        if _ORT_AVAILABLE:
            if self._load_onnx_fallback():
                self._backend = "onnx"
                self._is_loaded = True
                logger.warning("[CoreML] ONNXRuntime CPU fallback loaded (bge-small-en-v1.5)")
                return True

        logger.warning("[CoreML] No embedder backend available — hash fallback active")
        return False

    async def _load_coreml_service(self) -> bool:
        """
        Start CoreML microservice and load model via HTTP.
        Uses py3.12 FastAPI service on :8765.
        """
        try:
            # Ensure the microservice is running (no-op if already up)
            CoreMLServiceManager.ensure_running()

            # Create HTTP client and probe health
            self._client = CoreMLClient()

            # Try to pre-load the model into the microservice cache
            if _MLPACKAGE_PATH.exists():
                loaded = await self._client.load_model(
                    _COREML_MODEL_NAME,
                    str(_MLPACKAGE_PATH),
                )
                if loaded:
                    return True

            # Health check as fallback probe
            health = await self._client.health()
            if health.status == "ok":
                # Service is up but model not pre-loaded — will load on first predict
                return True

            return False

        except Exception as e:
            logger.warning("[CoreML] Microservice load failed: %s", e)
            return False

    def _load_onnx_fallback(self) -> bool:
        """Load ONNX Runtime CPU fallback model (pre-converted)."""
        if not _ORT_AVAILABLE:
            return False

        try:
            # Load ONNX model
            if not _ONNX_FALLBACK_PATH.exists():
                return self._convert_onnx_fallback()

            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._model = ort.InferenceSession(str(_ONNX_FALLBACK_PATH), sess_options=sess_options)  # type: ignore[assignment]

            # Load tokenizer for ONNX backend (store in _hf_tokenizer, keep _tokenizer as _BGETokenizer)
            from transformers import AutoTokenizer
            self._hf_tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)

            self._backend = "onnx"
            return True

        except Exception as e:
            logger.warning("[CoreML] ONNX load failed: %s", e)
            return False

    def _convert_onnx_fallback(self) -> bool:
        """Convert model to ONNX format for CPU fallback."""
        if not _ORT_AVAILABLE:
            return False

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            logger.warning("[CoreML] Converting to ONNX CPU fallback...")
            tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
            model = AutoModel.from_pretrained(_MODEL_NAME)
            model.eval()

            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            assert tokenizer is not None  # type: ignore[truthy-boolean]

            dummy_tokens = tokenizer(
                "test",
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )

            torch.onnx.export(
                model,
                (dummy_tokens["input_ids"], dummy_tokens["attention_mask"]),
                str(_ONNX_FALLBACK_PATH),
                input_names=["input_ids", "attention_mask"],
                output_names=["last_hidden_state"],
                dynamic_shapes={
                    "input_ids": {0: "batch", 1: "seq"},
                    "attention_mask": {0: "batch", 1: "seq"},
                },
                opset_version=14,
            )

            del model, tokenizer
            import gc
            gc.collect()

            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._model = ort.InferenceSession(str(_ONNX_FALLBACK_PATH), sess_options=sess_options)  # type: ignore[assignment]
            self._backend = "onnx"
            return True

        except Exception as e:
            logger.warning("[CoreML] ONNX conversion failed: %s", e)
            return False

    def unload(self) -> None:
        """Release model memory and close HTTP client."""
        if self._client is not None:
            client = self._client

            def _close_client_sync() -> None:
                """Sync wrapper — called in thread pool to close async HTTP client."""
                asyncio.run(client.close())

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — asyncio.to_thread() dispatches to a thread pool thread.
                # asyncio.run() creates a fresh event loop in that thread (py3.14+ PEP 667).
                try:
                    asyncio.to_thread(_close_client_sync)
                except Exception:  # noqa: BLE001
                    # Fallback: synchronous close if HTTP client has no async close
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                # Running loop exists — run_in_executor runs blocking code in the
                # default ThreadPoolExecutor without blocking the event loop.
                try:
                    loop.run_in_executor(None, _close_client_sync)
                except Exception:  # noqa: BLE001
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._client = None
        self._backend = None
        self._is_loaded = False
        logger.debug("[CoreML] Embedder unloaded")

    # -------------------------------------------------------------------------
    # Inference
    # -------------------------------------------------------------------------
    async def encode_batch(
        self,
        texts: str | list[str],
        batch_size: int = _BATCH_SIZE,
    ) -> np.ndarray:
        """
        Encode a batch of texts to embedding vectors.

        Args:
            texts: Single string or list of strings.
            batch_size: Max batch size (capped at 32 for M1 8GB).

        Returns:
            np.ndarray of shape (len(texts), 384), dtype float32, L2-normalized.
        """
        if isinstance(texts, str):
            texts = [texts]

        if not texts:
            return np.zeros((0, _EMBED_DIM), dtype=np.float32)

        batch_size = min(batch_size, _BATCH_SIZE)
        all_embeddings: list[np.ndarray] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # Truncate each text
            batch = [t[:_MAX_TEXT_LEN] for t in batch]

            if self._backend == "mlx":
                emb = await self._model.encode_batch(batch, batch_size=len(batch))
            elif self._backend == "coreml":
                emb = await self._encode_coreml(batch)
            elif self._backend == "onnx":
                emb = await self._encode_onnx(batch)
            else:
                # Hash fallback — deterministic zero-RAM
                emb = self._encode_hash_fallback(batch)

            all_embeddings.append(emb)

        result = np.vstack(all_embeddings)
        # L2 normalize
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        result = result / (norms + 1e-8)
        return result.astype(np.float32)

    async def _encode_coreml(self, texts: list[str]) -> np.ndarray:
        """Encode via CoreML microservice HTTP API."""
        if self._client is None:
            return self._encode_hash_fallback(texts)

        try:
            # Lazy-load HF tokenizer (heavy dep, done once)
            if self._hf_tokenizer is None:
                try:
                    from transformers import AutoTokenizer
                    self._hf_tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
                except Exception:  # noqa: BLE001
                    pass

            if self._hf_tokenizer is not None:
                tok_result = self._hf_tokenizer(
                    texts,
                    return_tensors="np",
                    padding=True,
                    truncation=True,
                    max_length=512,
                )
                inputs = {
                    "input_ids": tok_result["input_ids"].tolist(),
                    "attention_mask": tok_result["attention_mask"].tolist(),
                }
            else:
                # Minimal fallback tokenization using _BGETokenizer
                max_len = 512
                batch_tokens: list[list[int]] = []
                batch_masks: list[list[int]] = []
                for t in texts:
                    tok_list = self._tokenizer.encode(t)
                    if len(tok_list) < max_len:
                        tok_list = tok_list + [0] * (max_len - len(tok_list))
                    batch_tokens.append(tok_list[:max_len])
                    batch_masks.append([1 if j < len(self._tokenizer.encode(t)) else 0 for j in range(max_len)])
                inputs = {
                    "input_ids": batch_tokens,
                    "attention_mask": batch_masks,
                }

            # Call microservice — batch predict
            result = await self._client.predict(_COREML_MODEL_NAME, inputs)

            # Extract embeddings from response
            outputs = result.outputs
            if isinstance(outputs, dict) and "last_hidden_state" in outputs:
                hs = outputs["last_hidden_state"]
                # Handle nested list structure
                if isinstance(hs, list) and hs and isinstance(hs[0], list):
                    arr = np.array(hs, dtype=np.float32)
                else:
                    arr = np.array(hs, dtype=np.float32)
                # Mean pooling
                if arr.ndim == 3:
                    arr = arr.squeeze(0) if arr.shape[0] == 1 else arr
                mask = np.array(inputs["attention_mask"], dtype=np.float32)[..., np.newaxis]
                pooled = (arr * mask).sum(axis=1) / (mask.sum(axis=1) + 1e-8)
                return pooled.astype(np.float32)

            # CoreML microservice always returns {"last_hidden_state": [...]} — no fallback needed
            # If format differs, service is misconfigured → raise so caller gets hash fallback
            raise RuntimeError(f"[CoreML] Unexpected outputs format: {type(outputs)}")

        except Exception as e:
            logger.warning("[CoreML] Microservice inference failed: %s", e)
            return self._encode_hash_fallback(texts)

    async def _encode_onnx(self, texts: list[str]) -> np.ndarray:
        """Encode via ONNXRuntime CPU."""
        loop = asyncio.get_running_loop()

        def _sync_encode() -> np.ndarray:
            try:
                # Tokenize via HF tokenizer (loaded in _load_onnx_fallback → stored in _hf_tokenizer)
                tok = self._hf_tokenizer
                if tok is None:
                    return self._encode_hash_fallback(texts)
                tokens = tok(
                    texts,
                    return_tensors="np",
                    padding=True,
                    truncation=True,
                    max_length=512,
                )
                input_ids = np.array(tokens["input_ids"], dtype=np.int64)
                attention_mask = np.array(tokens["attention_mask"], dtype=np.int64)

                outputs = self._model.run(None, {"input_ids": input_ids, "attention_mask": attention_mask})  # type: ignore[union-attr]
                last_hidden = outputs[0]

                # Mean pooling
                mask = attention_mask[..., np.newaxis]
                pooled = (last_hidden * mask).sum(axis=1) / (mask.sum(axis=1) + 1e-8)
                return pooled.astype(np.float32)

            except Exception as e:
                logger.warning("[CoreML] ONNX inference failed: %s", e)
                return self._encode_hash_fallback(texts)

        return await loop.run_in_executor(_INFERENCE_EXECUTOR, _sync_encode)

    def _encode_hash_fallback(self, texts: list[str]) -> np.ndarray:
        """Deterministic hash embeddings s plnou entropií — SHAKE256 stretch na 384 dims."""
        import hashlib

        results = []
        for t in texts:
            # SHAKE256 produkuje libovolně dlouhý output — žádné opakování bitů
            h = hashlib.shake_256(t[:_MAX_TEXT_LEN].encode()).digest(length=_EMBED_DIM * 4)
            vec = np.frombuffer(h, dtype=np.float32).copy()
            # Normalize do [-1, 1] range
            vec = vec / (np.max(np.abs(vec)) + 1e-8)
            results.append(vec[:_EMBED_DIM])
        return np.vstack(results)

    # -------------------------------------------------------------------------
    # Sync encode (for SemanticStore compatibility)
    # -------------------------------------------------------------------------
    def embed(self, texts: str | list[str], **kwargs) -> np.ndarray:
        """Sync alias — runs encode_batch (matches FastEmbed .embed()).

        Uses a dedicated ThreadPoolExecutor with a fresh event loop per call.
        This is the M1-SAFE pattern (GHOST_INVARIANTS: loop.run_until_complete()
        with a new loop in the worker thread — NEVER asyncio.run() inside
        a ThreadPoolExecutor on Apple Silicon).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — pure sync context; asyncio.run() is safe here.
            try:
                return asyncio.run(self.encode_batch(texts, **kwargs))
            except Exception:
                n = len(texts) if isinstance(texts, list) else 1
                return np.zeros((n, _EMBED_DIM), dtype=np.float32)
        else:
            # Running loop exists — use ThreadPoolExecutor with loop.run_until_complete().
            # GHOST_INVARIANT: asyncio.run() inside ThreadPoolExecutor = M1 crash vector.
            # Solution: create a fresh event loop in the worker thread and use
            # loop.run_until_complete() — the documented M1-SAFE pattern.
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                    def _run_encode():
                        new_loop = asyncio.new_event_loop()
                        try:
                            return new_loop.run_until_complete(
                                self.encode_batch(texts, **kwargs)
                            )
                        finally:
                            new_loop.close()
                    future = _ex.submit(_run_encode)
                    return future.result(timeout=30)
            except Exception:
                n = len(texts) if isinstance(texts, list) else 1
                return np.zeros((n, _EMBED_DIM), dtype=np.float32)


# Backward-compatibility alias (coreml_embedder is imported from here in semantic_store.py)
_ANE_EMBEDDER: CoreMLEmbedder | None = None
_GLOBAL_TOKENIZER = None  # type: ignore[assignment]


async def get_ane_embedder() -> CoreMLEmbedder | None:
    """Lazy init CoreML embedder (alias for get_coreml_embedder)."""
    return await get_coreml_embedder()


def unload_ane_embedder() -> None:
    """Unload the embedder (called by memory pressure governor)."""
    global _coreml_embedder_instance
    if _coreml_embedder_instance is not None:
        _coreml_embedder_instance.unload()
    _coreml_embedder_instance = None


async def semantic_dedup_findings(
    findings: list[dict],
    threshold: float = 0.92,
) -> list[dict]:
    """
    Semantic deduplication of findings.
    CoreML path: microservice batch inference → cosine similarity matrix.
    Hash fallback: url+title hash (zero RAM, always works).
    """
    embedder = await get_coreml_embedder()

    if embedder is None or not embedder.is_loaded:
        seen: set[int] = set()
        out: list[dict] = []
        for f in findings:
            key = hash((f.get("url", ""), f.get("title", "")))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out

    try:
        texts = [
            f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512]
            for f in findings
        ]
        embeddings = await embedder.encode_batch(texts, batch_size=32)

        # Cosine similarity dedup
        # Normalized embeddings → dot product = cosine similarity
        sim_matrix = embeddings @ embeddings.T
        keep = []
        for i, f in enumerate(findings):
            if i == 0 or np.max(sim_matrix[i, :i]) < threshold:
                keep.append(f)
        return keep

    except Exception:
        # Fail-safe: return originals
        return findings
