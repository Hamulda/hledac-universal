"""
embeddings/ane/ — Unified ANE/MLX Embedder Factory (F330-MLX-DUP-007)

Jediný vstupní bod pro embedding inference na M1:

- CoreML ANE engine (preferovaný, pokud dostupný)
- MLX/Metal fallback (ModernBERTEmbedder)
- Sdílený UnifiedMemoryBudget(max=3.5GB) s LRU evict
- mx.metal.clear_cache() hook po každém batchi

Usage:
    from hledac.universal.embeddings.ane import ane_embedder

    embedder = ane_embedder()  # factory — vrací správný typ
    embeddings = embedder.embed(["text1", "text2"])

Memory budget (M1 8GB):
    Model (ANE):     ~300 MB (fixed, ANE dedicated memory)
    Model (MLX):     ~400 MB (ModernBERT)
    KV cache:        ~750 MB
    Metal cache:     ~500 MB–1.1 GB
    Embedder buffers: ~256 MB
    ─────────────────────────────
    Total:           ~2.2–2.8 GB (within 3.5 GB budget)

Canonical import: from hledac.universal.embeddings.ane import ane_embedder

ISSUE-003 FIX: Module-level locks registered via @auto_register decorator.
"""

import logging
import threading
from typing import Any

from _core.lock_registry import LockCategory, auto_register

logger = logging.getLogger(__name__)
_UNIFIED_BUDGET_BYTES: int = int(3.5 * 1024 * 1024 * 1024)
_LRU_MAX_ENTRIES: int = 4
_ANE_AVAILABLE: bool | None = None
_ANE_CHECKED: bool = False


@auto_register(LockCategory.CACHE)
def _ane_lock():
    """Module-level lock for ANE availability checking (shared state)."""
    return threading.Lock()


# Lazy mlx.core singleton
_MLX_CORE: Any | None = None


def _get_mx() -> Any | None:
    """Lazy accessor for mlx.core — imports once and caches. Returns None if unavailable."""
    global _MLX_CORE
    if _MLX_CORE is None:
        try:
            import mlx.core as mx

            _MLX_CORE = mx
        except ImportError:
            _MLX_CORE = False
    return _MLX_CORE if _MLX_CORE is not False else None


def _check_ane_available() -> bool:
    """Lazily check ANE availability."""
    global _ANE_AVAILABLE, _ANE_CHECKED
    if _ANE_CHECKED:
        return _ANE_AVAILABLE
    with _ane_lock():
        # Double-check after acquiring lock
        if _ANE_CHECKED:
            return _ANE_AVAILABLE
        _ANE_CHECKED = True
        import platform
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        _ANE_AVAILABLE = False
        return False
    try:
        import coremltools as ct

        if ct.__version__ < "6.0":
            logger.debug(f"[ANE] coremltools {ct.__version__} < 6.0")
            _ANE_AVAILABLE = False
            return False
    except ImportError:
        logger.debug("[ANE] coremltools not installed")
        _ANE_AVAILABLE = False
        return False
    from pathlib import Path

    model_path = Path.home() / ".hledac" / "models" / "modernbert_ane.mlpackage"
    if not model_path.exists():
        logger.debug(f"[ANE] Model not found at {model_path}")
        _ANE_AVAILABLE = False
        return False
    _ANE_AVAILABLE = True
    return True


class _UnifiedEmbedder:
    """
    Unified embedder with shared memory budget and LRU eviction.

    Tries CoreML ANE first, falls back to MLX/Metal ModernBERT.
    Both paths share a unified memory budget and track usage for LRU eviction.
    """

    __slots__ = (
        "_ane_active",
        "_ane_embedder",
        "_batch_size",
        "_budget_used_bytes",
        "_lru_order",
        "_mlx_embedder",
        "_normalize",
    )

    def __init__(self, normalize: bool = True, batch_size: int = 16) -> None:
        self._ane_embedder: Any = None
        self._mlx_embedder: Any = None
        self._ane_active: bool = False
        self._normalize = normalize
        self._batch_size = batch_size
        # NOTE: No per-instance lock - uses module-level _ane_lock() instead
        self._budget_used_bytes: int = 0
        self._lru_order: list[str] = []

    def _ensure_ane(self) -> bool:
        """Lazily load ANE embedder."""
        if self._ane_embedder is not None:
            return self._ane_active
        with _ane_lock():
            if self._ane_embedder is not None:
                return self._ane_active
            if _check_ane_available():
                try:
                    from ._encoder import CoreMLModernBERTEncoder

                    self._ane_embedder = CoreMLModernBERTEncoder(
                        lazy_load=True, normalize=self._normalize, batch_size=self._batch_size, fallback_to_mlx=True
                    )
                    self._ane_active = self._ane_embedder._ensure_ane()
                    if self._ane_active:
                        self._budget_used_bytes += 300 * 1024 * 1024
                        self._lru_order.append("ane")
                        logger.info("[ANE] CoreML ANE embedder loaded")
                        return True
                except Exception as e:
                    logger.debug(f"[ANE] ANE load failed: {e}")
            self._ane_active = False
            return False

    def _ensure_mlx(self) -> Any:
        """Lazily load MLX fallback embedder."""
        if self._mlx_embedder is not None:
            return self._mlx_embedder
        with _ane_lock():
            if self._mlx_embedder is not None:
                return self._mlx_embedder
            try:
                from ..modernbert_embedder import ModernBERTEmbedder

                self._mlx_embedder = ModernBERTEmbedder(
                    lazy_load=True, normalize=self._normalize, batch_size=self._batch_size
                )
                self._budget_used_bytes += 400 * 1024 * 1024
                self._lru_order.append("mlx")
                logger.info("[ANE] MLX/Metal ModernBERT embedder loaded")
                return self._mlx_embedder
            except Exception as e:
                logger.error(f"[ANE] MLX fallback load failed: {e}")
                return None

    def _evict_lru(self) -> None:
        """Evict LRU embedder when budget exceeded."""
        if len(self._lru_order) > _LRU_MAX_ENTRIES:
            evicted = self._lru_order.pop(0)
            if evicted == "ane" and self._ane_embedder is not None:
                self._ane_embedder = None
                self._ane_active = False
                self._budget_used_bytes -= 300 * 1024 * 1024
                logger.info("[ANE] LRU evicted ANE embedder")
            elif evicted == "mlx" and self._mlx_embedder is not None:
                self._mlx_embedder = None
                self._budget_used_bytes -= 400 * 1024 * 1024
                logger.info("[ANE] LRU evicted MLX embedder")

    def _clear_metal_cache_hook(self) -> None:
        """Hook: mx.metal.clear_cache() after each batch."""
        try:
            mx = _get_mx()
            if mx is None:
                return
            mx.eval([])
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    @property
    def is_loaded(self) -> bool:
        return self._ane_active or self._mlx_embedder is not None

    def embed(self, texts: list[str]) -> Any | None:
        """
        Embed texts using available backend (ANE or MLX).

        Returns embedding matrix or None on failure.
        """
        if self._budget_used_bytes >= _UNIFIED_BUDGET_BYTES:
            self._evict_lru()
        if self._ensure_ane():
            try:
                result = self._ane_embedder.embed(texts)
                self._clear_metal_cache_hook()
                return result
            except Exception as e:
                logger.debug(f"[ANE] ANE embed failed: {e}")
        mlx_eng = self._ensure_mlx()
        if mlx_eng is not None:
            try:
                result = mlx_eng.embed(texts)
                self._clear_metal_cache_hook()
                return result
            except Exception as e:
                logger.error(f"[ANE] MLX embed failed: {e}")
        return None

    def get_stats(self) -> dict[str, Any]:
        """Return embedder statistics."""
        return {
            "budget_used_bytes": self._budget_used_bytes,
            "budget_max_bytes": _UNIFIED_BUDGET_BYTES,
            "ane_active": self._ane_active,
            "mlx_loaded": self._mlx_embedder is not None,
            "lru_order": list(self._lru_order),
        }


_ane_embedder_instance: _UnifiedEmbedder | None = None


@auto_register(LockCategory.CACHE)
def _factory_lock():
    """Module-level lock for singleton factory (ane_embedder)."""
    return threading.Lock()


def ane_embedder(normalize: bool = True, batch_size: int = 16) -> _UnifiedEmbedder:
    """
    Factory: get or create the shared _UnifiedEmbedder singleton.

    All callers share the same instance — memory budget is global.

    Args:
        normalize: L2-normalize embeddings (default True for retrieval).
        batch_size: Batch size for encoding (default 16).

    Returns:
        _UnifiedEmbedder instance.
    """
    global _ane_embedder_instance
    if _ane_embedder_instance is None:
        with _factory_lock():
            if _ane_embedder_instance is None:
                _ane_embedder_instance = _UnifiedEmbedder(normalize=normalize, batch_size=batch_size)
    return _ane_embedder_instance


__all__ = ["ane_embedder", "_UnifiedEmbedder"]
