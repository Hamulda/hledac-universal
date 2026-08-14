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
    "ResourceLifecycleManager",
    "require_rlm",
    "get_current_rlm",
    # R12: concurrency facade
    "ConcurrencyCategory",
    "get_semaphore",
    # SWARM-010: feature flags
    "FeatureFlags",
    "FeatureFlag",
    "FlagCategory",
    "FlagInfo",
    "FlagValidationError",
    "validate_sprint_flags",
    # ISSUE-04: DuckDB connection pool
    "duckdb_ro_pool",
    "duckdb_rw_pool",
    "duckdb_ro_acquire",
    "duckdb_ro_connection",
    "close_all_pools",
    "get_pool_stats",
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
_main_cache: object | None = None
_rlm_cache: dict[str, object] | None = None
_concurrency_cache: dict[str, object] | None = None
_ff_cache: dict[str, object] | None = None


# ── Loader functions for __getattr__ dispatch table ─────────────────────────

def _load_rust_backend() -> object:
    global _rb
    if _rb is None:
        from hledac.universal.core.rust_backend import rust as _r
        _rb = _r
    return _rb


def _load_locks() -> dict[str, object]:
    global _lock_cache
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
    return _lock_cache


def _load_embeddings() -> dict[str, object]:
    global _embed_cache
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
    return _embed_cache


def _load_resource_governor() -> dict[str, object]:
    global _rgov_cache
    if _rgov_cache is None:
        from hledac.universal.core.resource_governor import Priority
        _rgov_cache = {"Priority": Priority}
    return _rgov_cache


def _load_resource_lifecycle() -> dict[str, object]:
    global _rlm_cache
    if _rlm_cache is None:
        from hledac.universal.core.resource_lifecycle import (
            ResourceLifecycleManager,
            require_rlm,
            get_current_rlm,
        )
        _rlm_cache = {
            "ResourceLifecycleManager": ResourceLifecycleManager,
            "require_rlm": require_rlm,
            "get_current_rlm": get_current_rlm,
        }
    return _rlm_cache


def _load_system_detector() -> dict[str, object]:
    global _sysdet_cache
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
    return _sysdet_cache


def _load_uma_budget() -> dict[str, object]:
    global _uma_cache
    if _uma_cache is None:
        from hledac.universal.utils.uma_budget import Watchdog
        _uma_cache = {"Watchdog": Watchdog}
    return _uma_cache


def _load_main() -> object:
    global _main_cache
    if _main_cache is None:
        import importlib
        import sys
        _main_cache = importlib.import_module("hledac.universal.runtime.sprint_entrypoint")
        sys.modules["hledac.universal.core.__main__"] = _main_cache
    return _main_cache


def _load_concurrency() -> dict[str, object]:
    global _concurrency_cache
    if _concurrency_cache is None:
        from hledac.universal.core.concurrency import (
            ConcurrencyCategory,
            get_semaphore,
        )
        _concurrency_cache = {
            "ConcurrencyCategory": ConcurrencyCategory,
            "get_semaphore": get_semaphore,
        }
    return _concurrency_cache


def _load_feature_flags() -> dict[str, object]:
    global _ff_cache
    if _ff_cache is None:
        from hledac.universal.core.feature_flags import (
            FeatureFlags,
            FeatureFlag,
            FlagCategory,
            FlagInfo,
            FlagValidationError,
            validate_sprint_flags,
        )
        _ff_cache = {
            "FeatureFlags": FeatureFlags,
            "FeatureFlag": FeatureFlag,
            "FlagCategory": FlagCategory,
            "FlagInfo": FlagInfo,
            "FlagValidationError": FlagValidationError,
            "validate_sprint_flags": validate_sprint_flags,
        }
    return _ff_cache


# ISSUE-04: DuckDB connection pool loader
_duckdb_pool_cache: dict[str, object] | None = None


def _load_duckdb_pool() -> dict[str, object]:
    global _duckdb_pool_cache
    if _duckdb_pool_cache is None:
        from hledac.universal.core.duckdb_pool import (
            duckdb_ro_pool,
            duckdb_rw_pool,
            duckdb_ro_acquire,
            duckdb_ro_connection,
            close_all_pools,
            get_pool_stats,
        )
        _duckdb_pool_cache = {
            "duckdb_ro_pool": duckdb_ro_pool,
            "duckdb_rw_pool": duckdb_rw_pool,
            "duckdb_ro_acquire": duckdb_ro_acquire,
            "duckdb_ro_connection": duckdb_ro_connection,
            "close_all_pools": close_all_pools,
            "get_pool_stats": get_pool_stats,
        }
    return _duckdb_pool_cache


# ── Dispatch table: name → loader ───────────────────────────────────────────

_LOADER_DISPATCH: tuple[tuple[frozenset[str], _load_locks | _load_embeddings | _load_resource_governor | _load_resource_lifecycle | _load_system_detector | _load_uma_budget | _load_concurrency | _load_feature_flags | _load_duckdb_pool], ...] = (
    (frozenset(("LockCategory", "LockInfo", "register_lock", "acquire_in_order", "get_registered_locks", "get_locks_by_category", "AsyncLockDCLP", "make_counter")), _load_locks),
    (frozenset(("MLXEmbeddingManager", "EmbeddingTask", "apply_task_prefix", "should_normalize")), _load_embeddings),
    (frozenset(("Priority",)), _load_resource_governor),
    (frozenset(("ResourceLifecycleManager", "require_rlm", "get_current_rlm")), _load_resource_lifecycle),
    (frozenset(("SystemDetector", "get_system_detector", "get_hardware_capabilities", "HardwareCapabilities")), _load_system_detector),
    (frozenset(("Watchdog",)), _load_uma_budget),
    (frozenset(("ConcurrencyCategory", "get_semaphore")), _load_concurrency),
    (frozenset(("FeatureFlags", "FeatureFlag", "FlagCategory", "FlagInfo", "FlagValidationError", "validate_sprint_flags")), _load_feature_flags),
    (frozenset(("duckdb_ro_pool", "duckdb_rw_pool", "duckdb_ro_acquire", "duckdb_ro_connection", "close_all_pools", "get_pool_stats")), _load_duckdb_pool),
)


def __getattr__(name: str):
    # ── rust_backend (special case, returns module directly) ─────────────────
    if name == "rust_backend":
        return _load_rust_backend()

    # ── __main__ (special case, returns module directly) ─────────────────────
    if name == "__main__":
        return _load_main()

    # ── dispatch to loader based on name membership ───────────────────────────
    for names, loader in _LOADER_DISPATCH:
        if name in names:
            return loader()[name]  # type: ignore[return-value]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
