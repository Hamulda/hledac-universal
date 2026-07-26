"""core — F350M-R A-04 — PEP 810 lazy imports for cold-start optimization."""
from __future__ import annotations

__all__ = [
    "Priority",
    "MLXEmbeddingManager",
    "EmbeddingTask",
    "apply_task_prefix",
    "should_normalize",
    "SystemDetector",
    "get_system_detector",
    "get_hardware_capabilities",
    "HardwareCapabilities",
    "LockCategory",
    "LockInfo",
    "register_lock",
    "acquire_in_order",
    "get_registered_locks",
    "get_locks_by_category",
    "AsyncLockDCLP",
    "make_counter",
    "Watchdog",
    "rust_backend",
]

# ── PEP 810 lazy imports — nothing imported at module load time ───────────────
# NOTE: M1 8GB cold-start budget is precious. Every ms counts.
# All real imports are deferred to __getattr__ on first access.

# Cached lazy-loaders (module-level state, cleared only at process exit)
_lock_cache: dict[str, object] | None = None
_embed_cache: dict[str, object] | None = None
_rgov_cache: dict[str, object] | None = None
_sysdet_cache: dict[str, object] | None = None
_uma_cache: dict[str, object] | None = None
_rb: object | None = None


def __getattr__(name: str):
    global _lock_cache, _embed_cache, _rgov_cache, _sysdet_cache, _uma_cache, _rb

    # ── rust_backend (already lazy, keep existing pattern) ───────────────────
    if name == "rust_backend":
        if _rb is None:
            from hledac.universal.core.rust_backend import rust as _r
            _rb = _r
        return _rb

    # ── locks ────────────────────────────────────────────────────────────────
    if name in (
        "LockCategory",
        "LockInfo",
        "register_lock",
        "acquire_in_order",
        "get_registered_locks",
        "get_locks_by_category",
        "AsyncLockDCLP",
        "make_counter",
    ):
        if _lock_cache is None:
            from hledac.universal.core.locks import (
                LockCategory,
                LockInfo,
                register_lock,
                acquire_in_order,
                get_registered_locks,
                get_locks_by_category,
                AsyncLockDCLP,
                make_counter,
            )
            # Build cache dict explicitly (locals() won't capture imports)
            _lock_cache = {
                "LockCategory": LockCategory,
                "LockInfo": LockInfo,
                "register_lock": register_lock,
                "acquire_in_order": acquire_in_order,
                "get_registered_locks": get_registered_locks,
                "get_locks_by_category": get_locks_by_category,
                "AsyncLockDCLP": AsyncLockDCLP,
                "make_counter": make_counter,
            }
        return _lock_cache[name]  # type: ignore[return-value]

    # ── embeddings.legacy (CONTAINS import mlx.core — the cold-start culprit) ─
    if name in ("MLXEmbeddingManager", "EmbeddingTask", "apply_task_prefix", "should_normalize"):
        if _embed_cache is None:
            from hledac.universal.core.embeddings.legacy import (
                MLXEmbeddingManager,
                EmbeddingTask,
                apply_task_prefix,
                should_normalize,
            )
            _embed_cache = {
                "MLXEmbeddingManager": MLXEmbeddingManager,
                "EmbeddingTask": EmbeddingTask,
                "apply_task_prefix": apply_task_prefix,
                "should_normalize": should_normalize,
            }
        return _embed_cache[name]  # type: ignore[return-value]

    # ── resource_governor ────────────────────────────────────────────────────
    if name == "Priority":
        if _rgov_cache is None:
            from hledac.universal.core.resource_governor import Priority
            _rgov_cache = {"Priority": Priority}
        return _rgov_cache[name]  # type: ignore[return-value]

    # ── system_detector ───────────────────────────────────────────────────────
    if name in ("SystemDetector", "get_system_detector", "get_hardware_capabilities", "HardwareCapabilities"):
        if _sysdet_cache is None:
            from hledac.universal.core.system_detector import (
                SystemDetector,
                get_system_detector,
                get_hardware_capabilities,
                HardwareCapabilities,
            )
            _sysdet_cache = {
                "SystemDetector": SystemDetector,
                "get_system_detector": get_system_detector,
                "get_hardware_capabilities": get_hardware_capabilities,
                "HardwareCapabilities": HardwareCapabilities,
            }
        return _sysdet_cache[name]  # type: ignore[return-value]

    # ── uma_budget ────────────────────────────────────────────────────────────
    if name == "Watchdog":
        if _uma_cache is None:
            from utils.uma_budget import Watchdog
            _uma_cache = {"Watchdog": Watchdog}
        return _uma_cache[name]  # type: ignore[return-value]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
