"""
ANE-akcelerovaný embedder pro ModernBERT a FlashRank.
Offline konverze z MLX do CoreML, fallback na MLX.




Reranker: rerank_findings_crossencoder() používá flashrank CrossEncoder.
LanceDBIdentityStore má vlastní _get_flashrank_ranker() pro search path.
Tyto dvě instance jsou záměrně oddělené — ANE brain pipeline vs. vector store search.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import fcntl
import inspect
import itertools
import logging
import os
import shutil
import stat
import threading
import warnings
from collections.abc import Awaitable, Callable
from enum import Enum
from operator import attrgetter
from pathlib import Path
from typing import Any, Literal

import numpy as np

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)

# [FINAL]-019-07: Capability cost registration for QoS ladder triage.
# ANE embedder: rss_mb=90, peak_mb=200 (CoreML model + ANE buffer)
from hledac.universal._core.capability_cost import register_capability_cost

register_capability_cost("aneembedder", rss_mb=90, peak_mb=200, tier="medium", tags=("embedding", "gpu", "ane"))
register_capability_cost("modernbert", rss_mb=400, peak_mb=600, tier="heavy", tags=("embedding", "gpu"))


class _MLXFamilyMutex:
    """
    R-4: Koordinace ANE/MLX/CoreML na M1 8GB.

    Tři sloty pro vzájemné vyloučení:
      - LLM:      Hermes 3B LLM + KV cache (MLX)
      - EMBED_ANE: ANE embedder (CoreML/neural engine)
      - EMBED_COREML: CoreML embedder (mlx-embeddings)

    Cross-process file lock /tmp/hledac_mlx_family.lock zabraňuje kolizi
    s externím mlxcel subprocess.

    Max combined memory: 2.5GB (hard guard).
    """

    _instance: _MLXFamilyMutex | None = None
    _lock: threading.Lock = threading.Lock()
    _active_runtime: Literal["llm", "embed_ane", "embed_coreml", None] = None
    _max_combined_mb: float = 2560.0
    _cross_lock_path: str = "/tmp/hledac_mlx_family.lock"
    _cross_lock_fd: Any = None  # file object for cross-process lock (open file handle)

    # Per-slot model sizes (MB)
    _SLOT_SIZES: dict[Literal["llm", "embed_ane", "embed_coreml"], float] = {
        "llm": 2048.0,  # Hermes 3B
        "embed_ane": 90.0,  # ANE CoreML embedder
        "embed_coreml": 50.0,  # MLX embedder
    }

    def __new__(cls) -> _MLXFamilyMutex:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── Cross-process file lock ──────────────────────────────────────────────

    def _acquire_cross_lock(self, slot: Literal["llm", "embed_ane", "embed_coreml"]) -> None:
        """Acquire cross-process file lock (non-blocking). Fails silently — telemetry only."""
        try:
            import fcntl

            fd = open(self._cross_lock_path, "a")
            # LOCK_NB | LOCK_EX = non-blocking exclusive
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Store fd in instance — released on unlock
            _MLXFamilyMutex._cross_lock_fd = fd
        except (FileNotFoundError, PermissionError, OSError) as exc:
            # Cross-lock unavailable — log but don't fail (intra-process guard still active)
            logger.debug(f"[_MLXFamilyMutex] Cross-lock unavailable: {exc}")

    def _release_cross_lock(self) -> None:
        """Release cross-process file lock."""
        fd = getattr(_MLXFamilyMutex, "_cross_lock_fd", None)
        if fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                fd.close()
            except OSError, AttributeError:  # noqa: BLE001
                pass
            _MLXFamilyMutex._cross_lock_fd = None

    # ── Intra-process guard ─────────────────────────────────────────────────

    def _check_slot_conflict(self, slot: Literal["llm", "embed_ane", "embed_coreml"]) -> MemoryError | None:
        """Return MemoryError if slot conflicts with active runtime."""
        conflict_map: dict[Literal["llm", "embed_ane", "embed_coreml"], str | None] = {
            "llm": "embed_ane",  # LLM blocks ANE embedder (GPU bandwidth)
            "embed_ane": "llm",  # ANE blocks LLM
            "embed_coreml": "llm",  # CoreML embed blocks LLM
        }
        blocker = conflict_map.get(slot)
        if blocker is not None and self._active_runtime == blocker:
            return MemoryError(
                f"[_MLXFamilyMutex] {self._active_runtime} is active — cannot acquire {slot}. Release {self._active_runtime} first."
            )
        if self._active_runtime == slot:
            return None  # Already this slot — re-entrant OK
        return None

    def acquire_llm(self, model_size_mb: float = 0.0) -> None:
        """Acquire LLM slot. Raises MemoryError if ANE/CoreML embedder is active."""
        with self._lock:
            err = self._check_slot_conflict("llm")
            if err:
                raise err
            if model_size_mb > self._max_combined_mb:
                raise MemoryError(
                    f"[_MLXFamilyMutex] LLM model {model_size_mb:.0f}MB exceeds {self._max_combined_mb:.0f}MB limit."
                )
            self._active_runtime = "llm"
            logger.debug(f"[_MLXFamilyMutex] Acquired LLM (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("llm")

    def acquire_embed_ane(self, model_size_mb: float = 0.0) -> None:
        """Acquire ANE embedder slot. Raises MemoryError if LLM is active."""
        with self._lock:
            err = self._check_slot_conflict("embed_ane")
            if err:
                raise err
            self._active_runtime = "embed_ane"
            logger.debug(f"[_MLXFamilyMutex] Acquired EMBED_ANE (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("embed_ane")

    def acquire_embed_coreml(self, model_size_mb: float = 0.0) -> None:
        """Acquire CoreML/MLX embedder slot. Raises MemoryError if LLM is active."""
        with self._lock:
            err = self._check_slot_conflict("embed_coreml")
            if err:
                raise err
            self._active_runtime = "embed_coreml"
            logger.debug(f"[_MLXFamilyMutex] Acquired EMBED_COREML (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("embed_coreml")

    # ── Non-blocking try-acquire (for embedder fallback) ───────────────────────

    def try_acquire_llm(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire LLM slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict("llm")
            if err:
                return False
            if model_size_mb > self._max_combined_mb:
                return False
            self._active_runtime = "llm"
            logger.debug(f"[_MLXFamilyMutex] Acquired LLM (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("llm")
        return True

    def try_acquire_embed_ane(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire ANE embedder slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict("embed_ane")
            if err:
                return False
            self._active_runtime = "embed_ane"
            logger.debug(f"[_MLXFamilyMutex] Acquired EMBED_ANE (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("embed_ane")
        return True

    def try_acquire_embed_coreml(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire CoreML/MLX embedder slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict("embed_coreml")
            if err:
                return False
            self._active_runtime = "embed_coreml"
            logger.debug(f"[_MLXFamilyMutex] Acquired EMBED_COREML (model={model_size_mb:.0f}MB)")
        self._acquire_cross_lock("embed_coreml")
        return True

    def release(self, runtime: Literal["llm", "embed_ane", "embed_coreml"]) -> None:
        """Release lock for specified runtime.

        P3-6 FIX: Ownership check MUST happen BEFORE releasing cross-lock.
        Previous code dropped cross-lock first, allowing race conditions where:
        1. Thread A release(llm) enters _release_cross_lock()
        2. Thread B release(embed_ane) enters _release_cross_lock()
        3. Both threads manipulate cross-lock without proper synchronization

        Correct order: check ownership first, then release resources.
        """
        # P3-6 FIX: Check ownership first while holding the lock
        with self._lock:
            if self._active_runtime != runtime:
                logger.debug(f"[_MLXFamilyMutex] Release({runtime}) ignored — currently active: {self._active_runtime}")
                return
            # Only release if we own it
            self._active_runtime = None
            logger.debug(f"[_MLXFamilyMutex] Released {runtime}")
        # P3-6 FIX: Release cross-lock AFTER clearing ownership
        self._release_cross_lock()

    def is_active(self) -> Literal["llm", "embed_ane", "embed_coreml", None]:
        """Return currently active runtime."""
        return self._active_runtime

    def is_llm_active(self) -> bool:
        return self._active_runtime == "llm"

    def is_embed_ane_active(self) -> bool:
        return self._active_runtime == "embed_ane"

    def is_embed_coreml_active(self) -> bool:
        return self._active_runtime == "embed_coreml"

    @property
    def is_metal_busy_with_other_process(self) -> bool:
        """R-4: Cross-process check — True if external mlxcel is holding the Metal lock."""
        try:
            import fcntl

            fd = open(self._cross_lock_path)
            try:
                # LOCK_EX | LOCK_NB = non-blocking exclusive — fails if locked by another
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                return False  # Lock acquired = no other process holds it
            except OSError:
                return True  # EWOULDBLOCK = another process holds the lock
            finally:
                fd.close()
        except FileNotFoundError, PermissionError, OSError:
            return False  # Lock file missing/unavailable = no external process


# ── Backward-compat alias ────────────────────────────────────────────────────
class ANE_MLX_Mutex(_MLXFamilyMutex):
    """R-4: DEPRECATED — use _MLXFamilyMutex directly. ANE_MLX_Mutex preserved for compat."""

    def acquire_ane(self, model_size_mb: float = 0.0) -> None:
        """Deprecated alias for acquire_embed_ane."""
        warnings.warn(
            "ANE_MLX_Mutex.acquire_ane() is deprecated. Use acquire_embed_ane() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.acquire_embed_ane(model_size_mb)

    def acquire_mlx(self, model_size_mb: float = 0.0) -> None:
        """Deprecated alias for acquire_llm."""
        warnings.warn(
            "ANE_MLX_Mutex.acquire_mlx() is deprecated. Use acquire_llm() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.acquire_llm(model_size_mb)

    def release(self, runtime: Literal["ane", "mlx"] | Literal["llm", "embed_ane", "embed_coreml"]) -> None:  # type: ignore[override]
        """Deprecated — maps old 'ane'/'mlx' to new slot names."""
        warnings.warn(
            "ANE_MLX_Mutex.release() is deprecated. Use release() with llm/embed_ane/embed_coreml instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if runtime == "ane":
            return super().release("embed_ane")
        if runtime == "mlx":
            return super().release("llm")
        return super().release(runtime)  # type: ignore[arg-type]

    def is_ane_active(self) -> bool:
        return self.is_embed_ane_active()

    def is_mlx_active(self) -> bool:
        return self.is_llm_active()


def get_ane_mlx_mutex() -> _MLXFamilyMutex:  # type: ignore[return-value]
    """Thread-safe singleton accessor — returns _MLXFamilyMutex (formerly ANE_MLX_Mutex)."""
    return _MLXFamilyMutex()


try:
    import CoreML as _CoreML
    import Foundation as _Foundation

    ANE_AVAILABLE = True
except ImportError:
    ANE_AVAILABLE = False
    _CoreML = None
    _Foundation = None

# Rust ANE module — model registry, batch validation, telemetry
_RUST_ANE_AVAILABLE = False
try:
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal._core.rust_backend import rust

    _rust = rust.raw.module
    if hasattr(_rust, "ane"):
        _RUST_ANE_AVAILABLE = True
        _rust_ane = _rust.ane
    else:
        _rust_ane = None
except ImportError:
    _rust_ane = None

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


_CLONEFILE_available: bool | None = None  # cached check result
_LIBC: ctypes.CDLL | None = None  # cached libc handle


class _StatFs(ctypes.Structure):
    """APFS statfs structure — defined once at module level."""

    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_uint64),
        ("f_owner", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fsubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
    ]


def _get_libc() -> ctypes.CDLL | None:
    """Lazily cache libc handle to avoid repeated find_library calls."""
    global _LIBC
    if _LIBC is not None:
        return _LIBC
    try:
        import ctypes.util

        lib_c = ctypes.util.find_library("c")
        if lib_c:
            _LIBC = ctypes.CDLL(lib_c, use_errno=True)
            return _LIBC
    except Exception:  # noqa: BLE001
        pass
    return None


def _is_apfs_volume(path: str | Path) -> bool:
    """Detect if path is on APFS via statfs.f fstypename."""
    libc = _get_libc()
    if libc is None:
        return False
    try:
        stfs = _StatFs()
        path_bytes = os.fsencode(str(path))
        if libc.statfs(path_bytes, ctypes.byref(stfs)) == 0:
            fstype = stfs.f_fstypename.decode("utf-8", errors="replace")
            return fstype == "apfs"
    except OSError:  # noqa: BLE001
        pass
    return False


def _clonefile_single(src: Path, dst: Path) -> bool:
    """
    Clone a single file via APFS clonefile(2).
    Returns True on success, False if clonefile not available or failed.
    On success dst has identical content as src but is a COW copy.
    """
    global _CLONEFILE_available
    if _CLONEFILE_available is False:
        return False

    libc = _get_libc()
    if libc is None:
        _CLONEFILE_available = False
        return False

    src_bytes = os.fsencode(str(src))
    dst_bytes = os.fsencode(str(dst))

    # clonefile(src, dst, flags) — flags=0 for default semantics
    try:
        clonefile_fn = libc.clonefile
        clonefile_fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        clonefile_fn.restype = ctypes.c_int

        result = clonefile_fn(src_bytes, dst_bytes, 0)
        if result == 0:
            return True
        # EPERM can mean: cross-volume, immutable file, or snapshot
        err = ctypes.get_errno()
        if err in (errno.EPERM, errno.EXDEV, errno.EINVAL):
            _CLONEFILE_available = False
            return False
        # Other error — log and fall back
        logger.debug("[ANE] clonefile(%s, %s) errno=%d", src, dst, err)
    except OSError as e:
        logger.debug("[ANE] clonefile unavailable: %s", e)
        _CLONEFILE_available = False

    return False


def _copy_dir_recursive(src: Path, dst: Path) -> None:
    """
    Copy directory tree using APFS clonefile per file where possible.
    Falls back to shutil.copytree for files that can't be cloned.
    """
    dst.mkdir(parents=True, exist_ok=True)

    # Fast path: scandir to enumerate entries
    try:
        entries = list(os.scandir(src))
    except OSError as e:
        logger.warning("[ANE] scandir failed: %s, falling back to shutil", e)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return

    # Sequential fallback is fine — this is 100MB once per model lifecycle
    for entry in entries:
        src_path = Path(entry.path)
        dst_path = dst / entry.name

        if entry.is_dir(follow_symlinks=False):
            _copy_dir_recursive(src_path, dst_path)
        else:
            # Try clonefile first
            if not _clonefile_single(src_path, dst_path):
                # Fallback: copy file + permissions
                try:
                    # Preserve file mode (executable, etc.)
                    src_stat = entry.stat(follow_symlinks=False)
                    shutil.copy2(src_path, dst_path)
                    # Restore mode if copy2 didn't preserve it correctly
                    os.chmod(dst_path, stat.S_IMODE(src_stat.st_mode))
                except OSError as e:
                    logger.warning("[ANE] file copy failed: %s -> %s: %s", src_path, dst_path, e)
                    shutil.copy2(src_path, dst_path)


def _clone_dir(src: Path, dst: Path) -> None:
    """
    ISSUE-012: Clone directory tree using APFS clonefile for O(1) copies.
    Falls back to shutil.copytree on failure (cross-volume, non-APFS, etc.).
    """
    # Log APFS detection only when we haven't already proven clonefile unavailable
    if _CLONEFILE_available is not False:
        is_apfs = _is_apfs_volume(src)
        logger.debug("[ANE] _clone_dir: %s (APFS=%s) -> %s", src, is_apfs, dst)

    try:
        _copy_dir_recursive(src, dst)
    except OSError as e:
        logger.warning("[ANE] clone-based copy failed: %s, falling back to shutil", e)
        shutil.copytree(src, dst, dirs_exist_ok=True)


# E-34: itertools.count() is atomic in CPython — GIL protects += operation.
# Using dict values with += from concurrent asyncio + ThreadPoolExecutor caused
# undercounting (non-atomic read-modify-write). count() objects are thread-safe.
_ANE_COUNTER_ATTEMPTED: itertools.count[int] = itertools.count()
_ANE_COUNTER_FALLBACK: itertools.count[int] = itertools.count()
_ANE_COUNTER_WARMUP_OK: itertools.count[int] = itertools.count()
_ANE_COUNTER_WARMUP_ERR: itertools.count[int] = itertools.count()


class ANEStatus(Enum):
    """ANE status codes."""

    NOT_AVAILABLE = "not_available"
    MODEL_NOT_FOUND = "model_not_found"
    LOADED = "loaded"
    LOAD_FAILED = "load_failed"


class ANEStatusResult(Struct):
    """Sprint F300: msgspec.Struct for ANE status result.

    Result of get_ane_status().
    """

    available: bool
    loaded: bool
    model_path_exists: bool
    fallback_configured: bool
    last_error: str | None
    inference_path: str


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
        return ANEStatusResult(
            available=True,
            loaded=False,
            model_path_exists=False,
            fallback_configured=False,
            last_error=None,
            inference_path="hash_fallback",
        )
    model_exists = embedder.coreml_path.exists() if hasattr(embedder, "coreml_path") else False
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
        last_error=getattr(embedder, "_last_load_error", None),
        inference_path="fallback" if fallback_configured else "unavailable",
    )


def get_ane_telemetry() -> dict:
    """Sprint F228B: Returns a copy of ANE telemetry counters."""
    # E-34: Read atomic counters — next() is atomic in CPython (GIL)
    return {
        "ane_embed_attempted": next(_ANE_COUNTER_ATTEMPTED),
        "ane_embed_fallback_used": next(_ANE_COUNTER_FALLBACK),
        "ane_warmup_executed": next(_ANE_COUNTER_WARMUP_OK),
        "ane_warmup_error": next(_ANE_COUNTER_WARMUP_ERR),
    }


def reset_ane_telemetry() -> None:
    """Sprint F228B: Reset telemetry counters (for testing)."""
    # E-34: Recreate atomic counters — old objects become garbage
    global _ANE_COUNTER_ATTEMPTED, _ANE_COUNTER_FALLBACK, _ANE_COUNTER_WARMUP_OK, _ANE_COUNTER_WARMUP_ERR
    _ANE_COUNTER_ATTEMPTED = itertools.count()
    _ANE_COUNTER_FALLBACK = itertools.count()
    _ANE_COUNTER_WARMUP_OK = itertools.count()
    _ANE_COUNTER_WARMUP_ERR = itertools.count()


_HF_TOKENIZER = None


def _get_hf_tokenizer():
    global _HF_TOKENIZER
    if _HF_TOKENIZER is None:
        from transformers import AutoTokenizer

        _HF_TOKENIZER = AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2", use_fast=True)
    return _HF_TOKENIZER


def _make_ml_array(data_list: list, length: int = 64):
    arr, err = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
        [1, length], _CoreML.MLMultiArrayDataTypeInt32, None
    )
    if err:
        raise RuntimeError(f"MLMultiArray init failed: {err}")
    ns_vals = [_Foundation.NSNumber.numberWithInt_(v) for v in data_list]
    ns_arr = _Foundation.NSArray.arrayWithArray_(ns_vals)
    for i in range(length):
        arr.setObject_atIndexedSubscript_(ns_arr[i], i)
    return arr


def _make_ml_array_batch(np_array: np.ndarray) -> Any:
    """Create MLMultiArray with shape [batch, seq_len] from NumPy array.

    F4 FIX: Batch CoreML inference — one ANE dispatch for N texts.
    Uses vectorized ctypes.memmove for fast memory transfer.
    """
    batch, seq_len = np_array.shape
    arr, err = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
        [batch, seq_len], _CoreML.MLMultiArrayDataTypeInt32, None
    )
    if err:
        raise RuntimeError(f"MLMultiArray batch init failed: {err}")
    # Vectorized fill — much faster than per-element loop
    arr_data_ptr = arr.dataPointer()
    np_array_flat = np_array.flatten().astype(np.int32)
    ctypes.memmove(arr_data_ptr, np_array_flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)), batch * seq_len * 4)
    return arr


def _coreml_embed(model, text: str) -> np.ndarray:
    tok = _get_hf_tokenizer()
    tokens = tok(text[:256], return_tensors="np", padding="max_length", max_length=64, truncation=True)
    input_ids = tokens["input_ids"].flatten().tolist()
    attn_mask = tokens["attention_mask"].flatten().tolist()
    feat_dict = {"input_ids": _make_ml_array(input_ids), "attention_mask": _make_ml_array(attn_mask)}
    provider, err = _CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(feat_dict, None)
    if err:
        raise RuntimeError(f"Feature provider failed: {err}")
    result, err = model.predictionFromFeatures_error_(provider, None)
    if err:
        raise RuntimeError(f"Inference failed: {err}")
    vec_raw = result.featureValueForName_("var_570").multiArrayValue()
    vec = np.array([float(vec_raw.objectAtIndexedSubscript_(i)) for i in range(384)], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _coreml_embed_batch(model, texts: list[str], hidden_dim: int = 384) -> np.ndarray:
    """Batch CoreML embedding — F4 FIX: one ANE dispatch for N texts.

    Uses MLDictionaryFeatureProvider with batched [N, 64] MLMultiArray tensors.
    Single ANE dispatch instead of N sequential calls — significant speedup on ANE.

    CoreML's predictionFromFeatures_error_ accepts MLDictionaryFeatureProvider
    with MLMultiArray values where the first dimension represents the batch.
    This allows ANE to process the entire batch in one hardware dispatch.

    Args:
        model: CoreML model instance
        texts: List of text strings to embed
        hidden_dim: Embedding dimension (default 384)

    Returns:
        np.ndarray shape (N, hidden_dim), L2 normalized
    """
    if not texts:
        return np.zeros((0, hidden_dim), dtype=np.float32)

    tok = _get_hf_tokenizer()
    # Batch tokenization — HuggingFace handles batching efficiently
    tokens = tok(texts, return_tensors="np", padding="max_length", max_length=64, truncation=True)
    input_ids = tokens["input_ids"]  # shape: [batch, 64]
    attn_mask = tokens["attention_mask"]  # shape: [batch, 64]
    batch_size = len(texts)

    # Create batched MLMultiArrays [batch, 64]
    input_ids_ml = _make_ml_array_batch(input_ids)
    attn_mask_ml = _make_ml_array_batch(attn_mask)

    # MLDictionaryFeatureProvider with batched MLMultiArray values
    # CoreML interprets [N, 64] tensors as N independent sequences
    arr_dict = _Foundation.NSMutableDictionary.alloc().init()
    arr_dict.setObject_forKey_(input_ids_ml, _Foundation.NSString.stringWithString_("input_ids"))
    arr_dict.setObject_forKey_(attn_mask_ml, _Foundation.NSString.stringWithString_("attention_mask"))

    provider, err = _CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(arr_dict, None)
    if err:
        raise RuntimeError(f"Batch feature provider failed: {err}")

    # F4 FIX: Single ANE inference call for entire batch (one dispatch vs N)
    result, err = model.predictionFromFeatures_error_(provider, None)
    if err:
        raise RuntimeError(f"Batch inference failed: {err}")

    # Extract batched output [batch, hidden_dim]
    vec_raw = result.featureValueForName_("var_570").multiArrayValue()
    vec_data_ptr = vec_raw.dataPointer()

    # Efficient NumPy copy from MLMultiArray using ctypes.memmove (vectorized)
    result_np = np.zeros((batch_size, hidden_dim), dtype=np.float32)
    ctypes.memmove(result_np.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), vec_data_ptr, batch_size * hidden_dim * 4)

    # L2 normalize each embedding
    norms = np.linalg.norm(result_np, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-09)
    result_np = result_np / norms

    return result_np


class ANEEmbedder:
    """
    Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,
    spoléhá na volání MLX embedderu (který musí být poskytnut zvenčí).

    Sprint F228B: Truthful ANE path — no NotImplementedError in production.
    """

    __slots__ = (
        "_fallback_embedder",
        "_last_load_error",
        "_loaded",
        "_mlx_model",
        "_mlx_processor",
        "coreml_path",
        "hidden_dim",
        "model",
        "model_name",
        "_ane_mutex_acquired",
    )

    # Model constants for AllMiniLML6V2 CoreML embedder
    # AllMiniLM-L6-v2: 384 dimensions (not 768 like ModernBERT)
    _DEFAULT_HIDDEN_DIM: int = 384
    _DEFAULT_MODEL_NAME: str = "AllMiniLML6V2"
    # CoreML model filename (may differ from model_name for legacy compatibility)
    _COREML_MODEL_FILENAME: str = "AllMiniLML6V2.mlmodelc"

    def __init__(self, model_name: str = _DEFAULT_MODEL_NAME, hidden_dim: int = _DEFAULT_HIDDEN_DIM) -> None:
        self.model_name = model_name
        self.hidden_dim = hidden_dim
        self.model = None
        self._mlx_model = None
        self._mlx_processor = None
        self._loaded = False
        self._last_load_error: str | None = None
        # Use actual CoreML model filename for legacy compatibility
        self.coreml_path = MODELS_DIR / self._COREML_MODEL_FILENAME
        self._fallback_embedder: Callable[..., Awaitable[np.ndarray]] | None = None
        self._ane_mutex_acquired = False  # P3-6 FIX: Track mutex ownership

    def set_fallback(self, fallback_func: Callable[..., Awaitable[np.ndarray]]) -> None:
        """Nastaví fallback async funkci (např. MLX embedder)."""
        self._fallback_embedder = fallback_func

    async def load(self) -> None:
        """Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.

        CoreML→MLX migration: MLX is now the primary path. CoreML is only attempted
        if mlx-embeddings is unavailable (e.g. non-AppleSilicon).

        Rust ANE integration: When Rust ane module is available, registers model
        in the ANE model registry for hardware-aware scheduling.
        """
        if self._loaded or self._mlx_model is not None:
            return
        if MLX_EMBED_AVAILABLE:
            try:
                model_path = "nomic-ai/modernbert-embed-base"
                self._mlx_model, self._mlx_processor = _mlx_embeddings_load(model_path, lazy=False)
                self._loaded = True
                self._last_load_error = None

                # Register in Rust ANE registry if available
                if _RUST_ANE_AVAILABLE and _rust_ane is not None:
                    try:
                        _rust_ane.init()
                        _rust_ane.load_model(self.model_name, str(self.coreml_path), self.hidden_dim, 512)
                        logger.info(f"[ANE] Registered in Rust ANE registry: {self.model_name}")
                    except Exception as rust_err:
                        logger.debug(f"[ANE] Rust registry skipped: {rust_err}")

                logger.info(f"ANEEmbedder loaded MLX: {model_path}")
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f"MLX ModernBERT failed ({e}), trying CoreML fallback")
        if ANE_AVAILABLE and self.coreml_path.exists():
            try:
                url = _CoreML.NSURL.fileURLWithPath_(str(self.coreml_path))
                model, err = _CoreML.MLModel.modelWithContentsOfURL_error_(url, None)
                if err:
                    raise RuntimeError(f"CoreML load failed: {err}")
                self.model = model
                self._loaded = True
                self._last_load_error = None
                # P3-6 FIX: Track mutex ownership so unload() can release it
                get_ane_mlx_mutex().acquire_embed_ane(model_size_mb=90.0)
                self._ane_mutex_acquired = True
                logger.info(f"ANEEmbedder loaded CoreML: {self.model_name}")
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f"CoreML failed ({e}), using hash fallback")

    async def initialize(self) -> None:
        """
        Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.
        Idempotent: safe to call multiple times, only loads once.
        M1 guard: requires >1.5GB UMA available before loading CoreML model.
        """
        if self._loaded:
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            snap = get_uma_snapshot()
            if snap.is_critical or snap.is_emergency:
                logger.warning(f"[ANE] initialize skipped: memory pressure {snap.pct_used:.0f}% (>85%% critical)")
                return
            avail = snap.available_uma_gib
            if avail < 1.5:
                logger.warning(f"[ANE] initialize skipped: only {avail:.1f}GB < 1.5GB required")
                return
        except Exception:  # noqa: BLE001
            pass
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

            def _compile():
                url = _CoreML.NSURL.fileURLWithPath_(str(raw_path))
                compiled_url, err = _CoreML.MLModel.compileModelAtURL_error_(url, None)
                if err:
                    raise RuntimeError(f"Compile failed: {err}")
                compiled_str = str(compiled_url).replace("file://", "")
                _clone_dir(Path(compiled_str), compiled_path)
                return compiled_path

            self.coreml_path = await asyncio.to_thread(_compile)
            logger.info("[ANE] Compiled to %s", self.coreml_path)
            return True
        logger.warning("[ANE] No model found at %s or %s", compiled_path, raw_path)
        return False

    async def embed(self, texts: str | list[str]) -> np.ndarray:
        """
        Sprint F228B: Truthful embed — no NotImplementedError in production.
        Falls back gracefully: CoreML → fallback embedder → hash fallback.

        R-4: If LLM is active on Metal GPU, skip Metal-backed embedding
        and use hash fallback to avoid GPU bandwidth contention.
        """
        try:
            if _MLXFamilyMutex().is_llm_active():
                next(_ANE_COUNTER_FALLBACK)
                return self._hash_embed(texts if isinstance(texts, list) else [texts])
        except Exception:  # noqa: BLE001
            pass  # Mutex unavailable — proceed with normal path

        if isinstance(texts, str):
            texts = [texts]
        if self._loaded and self.model is not None:
            # E-34: Count once per actual embed call (CoreML path)
            next(_ANE_COUNTER_ATTEMPTED)

            # F4 FIX: Batch CoreML inference — one ANE dispatch for N texts
            def _run():
                return _coreml_embed_batch(self.model, texts, hidden_dim=self.hidden_dim)

            return await asyncio.to_thread(_run)
        if self._mlx_model is not None:
            # E-34: Count once per actual embed call (MLX path)

            def _run():
                import mlx.core as mx

                toks = self._mlx_processor(texts, return_tensors="np", padding=True, truncation=True, max_length=512)
                input_ids = mx.array(toks["input_ids"])
                attention_mask = mx.array(toks["attention_mask"])
                embs = self._mlx_model(input_ids, attention_mask=attention_mask)
                hs = embs.last_hidden_state
                mask = mx.array(toks["attention_mask"][:, :, None])
                summed = (hs * mask).sum(axis=1)
                counts = mx.maximum(mask.sum(axis=1), 1e-09)
                pooled = summed / counts
                result = mx.eval(pooled)
                return np.array(result, dtype=np.float32)

            return await asyncio.to_thread(_run)
        if self._fallback_embedder is not None:
            next(_ANE_COUNTER_FALLBACK)
            fb = self._fallback_embedder
            if inspect.iscoroutinefunction(fb):
                return await fb(texts)
            else:
                return await asyncio.to_thread(fb, texts)
        next(_ANE_COUNTER_FALLBACK)
        return self._hash_embed(texts)

    def _hash_embed(self, texts: str | list[str]) -> np.ndarray:
        """Deterministic hash-based fallback — always works, no model needed."""
        import xxhash

        if isinstance(texts, str):
            texts = [texts]
        vecs = []
        for t in texts:
            h = xxhash.xxh3_64(t[:512].encode("utf-8")).intdigest() % (2**32)
            vec = np.zeros(self.hidden_dim, dtype=np.float32)
            for i in range(min(self.hidden_dim, 384)):
                vec[i] = float(h >> i % 32 & 1) * 2 - 1
            norm = np.linalg.norm(vec)
            vecs.append(vec / norm if norm > 0 else vec)
        return np.array(vecs, dtype=np.float32)

    async def warmup(self) -> None:
        """
        Sprint F228B: Fixed warmup — awaits embed() correctly.
        Never passes async embed() directly to run_in_executor.
        """
        if not ANE_AVAILABLE:
            logger.debug("ANEEmbedder warmup skipped: ANE not available")
            return
        if not self._loaded or self.model is None:
            logger.debug("ANEEmbedder warmup skipped: model not loaded")
            return
        next(_ANE_COUNTER_WARMUP_OK)
        try:
            dummy = ["warmup probe osint security"]
            await self.embed(dummy)
            logger.debug("ANEEmbedder warmed up (ANE cache primed)")
        except Exception as e:
            next(_ANE_COUNTER_WARMUP_ERR)
            logger.debug(f"ANEEmbedder warmup failed: {e}")

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je ANE nebo MLX model načten."""
        return self._loaded and (self.model is not None or self._mlx_model is not None)

    def unload(self) -> None:
        """
        P3-6 FIX: Unload ANE model and release mutex if acquired.

        This method properly releases the embed_ane mutex that was acquired
        during load() when using the CoreML path.
        """
        # Release ANE mutex if we acquired it
        if self._ane_mutex_acquired:
            try:
                get_ane_mlx_mutex().release("embed_ane")
                logger.debug(f"[ANEEmbedder] Released embed_ane mutex for {self.model_name}")
            except Exception as e:
                logger.warning(f"[ANEEmbedder] Failed to release embed_ane mutex: {e}")
            finally:
                self._ane_mutex_acquired = False

        # Clear model references
        self.model = None
        self._mlx_model = None
        self._mlx_processor = None
        self._loaded = False
        logger.debug(f"[ANEEmbedder] Unloaded: {self.model_name}")


_ANE_EMBEDDER: ANEEmbedder | None = None


def get_ane_embedder() -> ANEEmbedder | None:
    """
    CoreML→MLX migration: ANEEmbedder is deprecated.

    .. deprecated::
        Use ``get_embedding_manager()`` from ``compat.core_mlx_embeddings`` instead.
        This function now returns None and logs a deprecation warning.
    """
    warnings.warn(
        "get_ane_embedder() is deprecated. Use get_embedding_manager() from compat.core_mlx_embeddings instead. ANEEmbedder will be removed in a future sprint.",
        DeprecationWarning,
        stacklevel=2,
    )
    return None


def unload_ane_embedder() -> None:
    """Release ANE mutex (no-op since ANE path is disabled)."""
    try:
        get_ane_mlx_mutex().release("embed_ane")
    except Exception:  # noqa: BLE001
        pass


async def semantic_dedup_findings(findings: list[dict], threshold: float = 0.92) -> list[dict]:
    """
    Semantic deduplication of findings using MLXEmbeddingManager.

    MLX path: MLXEmbeddingManager batch embedding → Rust SIMD cosine similarity.
    Rust path: embeddings.reranker.batch_rerank() uses NEON/SSE3 SIMD on M1.
    Hash fallback: url+title hash (zero RAM, always works).
    """
    try:
        from hledac.universal._core.embeddings.legacy import get_embedding_manager

        mgr = get_embedding_manager()
    except Exception:
        mgr = None
    if mgr is None or not mgr.is_loaded:
        seen: set[int] = set()
        out: list[dict] = []
        for f in findings:
            key = hash((f.get("url", ""), f.get("title", "")))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out
    texts = [f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512] for f in findings]
    try:
        vecs = await asyncio.to_thread(mgr.encode, texts, 32, True)
        # Rust SIMD cosine: batch_rerank normalizes candidates in-place (O(N×D)),
        # then computes dot products with query normalization per query (O(Q×N×D)).
        # mgr.encode with normalize=True produces normalized vectors — Rust's extra
        # normalization is idempotent so result is correct even if redundant.
        try:
            from embeddings.reranker import batch_rerank

            sim = batch_rerank(vecs, vecs)  # (N, N) cosine similarity matrix
        except Exception:
            # Fallback: numpy normalization + matmul (CPU)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-09
            vecs_n = vecs / norms
            sim = vecs_n @ vecs_n.T
        keep = [True] * len(findings)
        for i, _finding_i in enumerate(findings):
            if not keep[i]:
                continue
            for j, _finding_j in enumerate(findings):
                if i < j and sim[i, j] >= threshold:
                    keep[j] = False
        return [f for f, k in zip(findings, keep, strict=False) if k]
    except Exception:
        return findings


def rerank_findings_cosine(findings: list[dict], query: str, top_k: int = 20) -> list[dict]:
    """
    Cosine similarity reranker over MLX embeddings.
    Uses Rust SIMD batch cosine from embeddings.reranker (NEON on M1).
    Fallback: embeddings.reranker batch_rerank_topk() → Rust SIMD → top-k extraction.
    """
    try:
        from hledac.universal._core.embeddings.legacy import get_embedding_manager

        mgr = get_embedding_manager()
        if mgr is None or not mgr.is_loaded:
            raise RuntimeError("MLXEmbeddingManager unavailable")
    except Exception:
        return sorted(findings, key=attrgetter("get")("confidence", 0.5), reverse=True)[:top_k]
    try:
        from embeddings.reranker import batch_rerank_topk

        # ISSUE-BIRD-EYE: cap corpus to 200, store original length for index mapping
        capped_findings = findings[:200]
        corpus = [f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512] for f in capped_findings]
        all_texts = [query[:512]] + corpus
        embeddings = mgr.encode(all_texts, batch_size=32, normalize=True)
        # batch_rerank_topk: Rust SIMD cosine → rayon parallel top-K
        # Returns (scores, indices) — scores[0] = query vs all corpus, indices = top-k positions
        q_vecs = embeddings[0:1]  # shape (1, D) — single query
        corp_vecs = embeddings[1:]  # shape (N, D) — corpus, N = len(corpus) ≤ 200
        _, top_indices = batch_rerank_topk(q_vecs, corp_vecs, top_k=top_k)
        top_indices_list = top_indices[0].tolist()  # flatten to list of ints
        # CRITICAL FIX: indices are into capped_findings (first 200), not full findings list.
        # Guard: idx < len(corpus) ensures we never index into uncapped range.
        return [capped_findings[idx] for idx in top_indices_list if idx < len(corpus)]
    except Exception:
        return sorted(findings, key=attrgetter("get")("confidence", 0.5), reverse=True)[:top_k]


_flashrank_reranker = None
_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"


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


def rerank_findings_crossencoder(query: str, findings: list[dict], top_k: int = 20) -> list[dict]:
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
            text = (f.get("content") or f.get("text") or f.get("snippet") or f.get("title", "") or str(f))[:2048]
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


import re as _re

_IOC_PATTERNS: list[tuple[str, str]] = [
    ("ipv4", "\\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?:\\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}\\b"),
    ("ipv6", "\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b"),
    ("cve", "\\bCVE-\\d{4}-\\d{4,7}\\b"),
    ("sha256", "\\b[a-fA-F0-9]{64}\\b"),
    ("sha1", "\\b[a-fA-F0-9]{40}\\b"),
    ("md5", "\\b[a-fA-F0-9]{32}\\b"),
    ("url", "\\bhttps?://[^\\s<>\\\"']+"),
    ("email", "\\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}\\b"),
    ("domain", "\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b"),
]
_DOMAIN_TLD_DENYLIST: frozenset[str] = frozenset(
    {
        "exe",
        "dll",
        "bin",
        "so",
        "dylib",
        "lib",
        "o",
        "a",
        "obj",
        "deb",
        "rpm",
        "dmg",
        "pkg",
        "apk",
        "ipa",
        "jar",
        "war",
        "ear",
        "class",
        "cab",
        "msi",
        "lnk",
        "tar",
        "gz",
        "zip",
        "rar",
        "7z",
        "iso",
        "img",
        "dat",
        "tmp",
        "bak",
        "log",
        "conf",
        "cfg",
        "ini",
        "env",
        "py",
        "js",
        "ts",
        "html",
        "htm",
        "json",
        "xml",
        "yaml",
        "yml",
        "toml",
        "md",
        "txt",
        "csv",
        "sh",
        "bat",
        "ps1",
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
    }
)


def extract_iocs_from_text(text: object) -> list[dict[str, str]]:
    """Extract Indicators of Compromise from text using regex patterns.

    Always-on, fail-safe, no external deps. Returns list of dicts with keys
    ``ioc_type`` and ``value``. Never raises; returns ``[]`` on bad input.
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ioc_type, pattern in _IOC_PATTERNS:
            for m in _re.finditer(pattern, text):
                value = m.group(0)
                if ioc_type == "domain":
                    tld = value.rsplit(".", 1)[-1].lower()
                    if not tld.isalpha():
                        continue
                    if tld in _DOMAIN_TLD_DENYLIST:
                        continue
                elif ioc_type == "url":
                    value = value.rstrip(".,;:!?)")
                key = (ioc_type, value.lower() if ioc_type in {"url", "email", "domain"} else value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"ioc_type": ioc_type, "value": value})
        return out
    except Exception:
        return []
