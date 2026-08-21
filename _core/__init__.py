"""core — F350M-R A-04 — PEP 810 lazy imports for cold-start optimization.

ISSUE-002: Global State Pollution Fix
This module uses PEP 810 lazy imports and maintains backward compatibility
with global state patterns. The lazy caches are now managed via ModuleState
for proper memory management between sprints.

Migration path:
    Old: global _cache; _cache[key] = value
    New: from _core.module_state import _state; _state.set(key, value)
"""

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
    # F350M-R: Type-4 clone elimination — shared async cleanup helpers
    "aclose",
    "aclose_many",
    # ROADMAP-005: Centralized tenacity-based retry decorators
    "async_retry",
    "retry_if_exception",
    "retry_if_exception_type",
    "retry_if_result",
    "blitz_aware_stop",
    "exponential_backoff",
    "jitter_wait",
    "network_retry",
    "http_retry",
    # ISSUE-002: Sprint winddown hook
    "clear_between_sprints",
    "clear_core_caches",
]

# ── PEP 810 lazy imports — nothing imported at module load time ───────────────
# NOTE: M1 8GB cold-start budget is precious. Every ms counts.
# All real imports are deferred to __getattr__ on first access.

# ISSUE-002: Use ModuleState for centralized lazy cache management
from _core.module_state import ModuleState

# Module state singleton (replaces scattered global _cache variables)
_state: ModuleState = ModuleState()

# State keys for lazy loaders (ISSUE-002 fix)
_STATE_KEYS = {
    "locks": "core.locks",
    "embeddings": "core.embeddings",
    "resource_governor": "core.resource_governor",
    "resource_lifecycle": "core.resource_lifecycle",
    "system_detector": "core.system_detector",
    "uma_budget": "core.uma_budget",
    "rust_backend": "core.rust_backend",
    "main": "core.main",
    "concurrency": "core.concurrency",
    "feature_flags": "core.feature_flags",
    "duckdb_pool": "core.duckdb_pool",
    "util": "core.util",
    "async_retry": "core.async_retry",
}

# ── Loader functions for __getattr__ dispatch table ─────────────────────────
#
# ISSUE-002: All loaders now use ModuleState for thread-safe, clearable caching.
# This replaces the old pattern of module-level globals with global statements.


def _load_rust_backend() -> object:
    """Load rust backend (thread-safe via ModuleState)."""
    return _state.get_or_create(
        _STATE_KEYS["rust_backend"],
        lambda: __import__("hledac.universal._core.rust_backend", fromlist=["rust"]).rust,
    )


def _load_locks() -> dict[str, object]:
    """Load locks module (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["locks"], _make_locks_loader())


def _make_locks_loader() -> dict[str, object]:
    """Factory for locks loader (isolated for lazy evaluation)."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.locks import (
            AsyncLockDCLP,
            LockCategory,
            LockInfo,
            acquire_in_order,
            get_locks_by_category,
            get_registered_locks,
            make_counter,
            register_lock,
        )

        return {
            "LockCategory": LockCategory,
            "LockInfo": LockInfo,
            "register_lock": register_lock,
            "acquire_in_order": acquire_in_order,
            "get_registered_locks": get_registered_locks,
            "get_locks_by_category": get_locks_by_category,
            "AsyncLockDCLP": AsyncLockDCLP,
            "make_counter": make_counter,
        }

    return loader


def _load_embeddings() -> dict[str, object]:
    """Load embeddings module (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["embeddings"], _make_embeddings_loader())


def _make_embeddings_loader() -> dict[str, object]:
    """Factory for embeddings loader (isolated for lazy evaluation)."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.embeddings.legacy import (
            EmbeddingTask,
            MLXEmbeddingManager,
            apply_task_prefix,
            should_normalize,
        )

        return {
            "MLXEmbeddingManager": MLXEmbeddingManager,
            "EmbeddingTask": EmbeddingTask,
            "apply_task_prefix": apply_task_prefix,
            "should_normalize": should_normalize,
        }

    return loader


def _load_resource_governor() -> dict[str, object]:
    """Load resource governor (thread-safe via ModuleState)."""
    return _state.get_or_create(
        _STATE_KEYS["resource_governor"],
        lambda: {"Priority": __import__("hledac.universal._core.resource_governor", fromlist=["Priority"]).Priority},
    )


def _load_resource_lifecycle() -> dict[str, object]:
    """Load resource lifecycle (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["resource_lifecycle"], _make_resource_lifecycle_loader())


def _make_resource_lifecycle_loader() -> dict[str, object]:
    """Factory for resource lifecycle loader."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.resource_lifecycle import (
            ResourceLifecycleManager,
            get_current_rlm,
            require_rlm,
        )

        return {
            "ResourceLifecycleManager": ResourceLifecycleManager,
            "require_rlm": require_rlm,
            "get_current_rlm": get_current_rlm,
        }

    return loader


def _load_system_detector() -> dict[str, object]:
    """Load system detector (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["system_detector"], _make_system_detector_loader())


def _make_system_detector_loader() -> dict[str, object]:
    """Factory for system detector loader."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.system_detector import (
            HardwareCapabilities,
            SystemDetector,
            get_hardware_capabilities,
            get_system_detector,
        )

        return {
            "SystemDetector": SystemDetector,
            "get_system_detector": get_system_detector,
            "get_hardware_capabilities": get_hardware_capabilities,
            "HardwareCapabilities": HardwareCapabilities,
        }

    return loader


def _load_uma_budget() -> dict[str, object]:
    """Load UMA budget (thread-safe via ModuleState)."""
    return _state.get_or_create(
        _STATE_KEYS["uma_budget"],
        lambda: {"Watchdog": __import__("hledac.universal.utils.uma_budget", fromlist=["Watchdog"]).Watchdog},
    )


def _load_main() -> object:
    """Load main module (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["main"], _make_main_loader())


def _make_main_loader() -> object:
    """Factory for main loader."""
    import importlib
    import sys

    def loader() -> object:
        main_mod = importlib.import_module("hledac.universal.runtime.sprint_entrypoint")
        sys.modules["hledac.universal._core.__main__"] = main_mod
        return main_mod

    return loader


def _load_concurrency() -> dict[str, object]:
    """Load concurrency (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["concurrency"], _make_concurrency_loader())


def _make_concurrency_loader() -> dict[str, object]:
    """Factory for concurrency loader."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.concurrency import (
            ConcurrencyCategory,
            get_semaphore,
        )

        return {
            "ConcurrencyCategory": ConcurrencyCategory,
            "get_semaphore": get_semaphore,
        }

    return loader


def _load_feature_flags() -> dict[str, object]:
    """Load feature flags (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["feature_flags"], _make_feature_flags_loader())


def _make_feature_flags_loader() -> dict[str, object]:
    """Factory for feature flags loader."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.feature_flags import (
            FeatureFlag,
            FeatureFlags,
            FlagCategory,
            FlagInfo,
            FlagValidationError,
            validate_sprint_flags,
        )

        return {
            "FeatureFlags": FeatureFlags,
            "FeatureFlag": FeatureFlag,
            "FlagCategory": FlagCategory,
            "FlagInfo": FlagInfo,
            "FlagValidationError": FlagValidationError,
            "validate_sprint_flags": validate_sprint_flags,
        }

    return loader


# ISSUE-04: DuckDB connection pool loader (ISSUE-002: Uses ModuleState)
def _load_duckdb_pool() -> dict[str, object]:
    """Load DuckDB pool (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["duckdb_pool"], _make_duckdb_pool_loader())


def _make_duckdb_pool_loader() -> dict[str, object]:
    """Factory for DuckDB pool loader."""

    def loader() -> dict[str, object]:
        from hledac.universal._core.duckdb_pool import (
            close_all_pools,
            duckdb_ro_acquire,
            duckdb_ro_connection,
            duckdb_ro_pool,
            duckdb_rw_pool,
            get_pool_stats,
        )

        return {
            "duckdb_ro_pool": duckdb_ro_pool,
            "duckdb_rw_pool": duckdb_rw_pool,
            "duckdb_ro_acquire": duckdb_ro_acquire,
            "duckdb_ro_connection": duckdb_ro_connection,
            "close_all_pools": close_all_pools,
            "get_pool_stats": get_pool_stats,
        }

    return loader


# F350M-R: Type-4 clone elimination — shared async cleanup helpers
# ISSUE-002: Uses ModuleState instead of raw globals
def _load_util() -> dict[str, object]:
    """Load util helpers (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["util"], _make_util_loader())


def _make_util_loader() -> dict[str, object]:
    """Factory for util loader."""

    def loader() -> dict[str, object]:
        from _core._util import aclose, aclose_many

        return {"aclose": aclose, "aclose_many": aclose_many}

    return loader


def _load_async_retry() -> dict[str, object]:
    """ROADMAP-005: Load retry utilities (thread-safe via ModuleState)."""
    return _state.get_or_create(_STATE_KEYS["async_retry"], _make_async_retry_loader())


def _make_async_retry_loader() -> dict[str, object]:
    """Factory for async retry loader."""

    def loader() -> dict[str, object]:
        from _core.async_retry import (
            async_retry,
            blitz_aware_stop,
            exponential_backoff,
            http_retry,
            jitter_wait,
            network_retry,
            retry_if_exception,
            retry_if_exception_type,
            retry_if_result,
        )

        return {
            "async_retry": async_retry,
            "retry_if_exception": retry_if_exception,
            "retry_if_exception_type": retry_if_exception_type,
            "retry_if_result": retry_if_result,
            "blitz_aware_stop": blitz_aware_stop,
            "exponential_backoff": exponential_backoff,
            "jitter_wait": jitter_wait,
            "network_retry": network_retry,
            "http_retry": http_retry,
        }

    return loader


# ── Dispatch table: name → loader ───────────────────────────────────────────

_LOADER_DISPATCH: tuple[
    tuple[
        frozenset[str],
        _load_locks
        | _load_embeddings
        | _load_resource_governor
        | _load_resource_lifecycle
        | _load_system_detector
        | _load_uma_budget
        | _load_concurrency
        | _load_feature_flags
        | _load_duckdb_pool
        | _load_async_retry,
    ],
    ...,
] = (
    (
        frozenset(
            (
                "LockCategory",
                "LockInfo",
                "register_lock",
                "acquire_in_order",
                "get_registered_locks",
                "get_locks_by_category",
                "AsyncLockDCLP",
                "make_counter",
            )
        ),
        _load_locks,
    ),
    (frozenset(("MLXEmbeddingManager", "EmbeddingTask", "apply_task_prefix", "should_normalize")), _load_embeddings),
    (frozenset(("Priority",)), _load_resource_governor),
    (frozenset(("ResourceLifecycleManager", "require_rlm", "get_current_rlm")), _load_resource_lifecycle),
    (
        frozenset(("SystemDetector", "get_system_detector", "get_hardware_capabilities", "HardwareCapabilities")),
        _load_system_detector,
    ),
    (frozenset(("Watchdog",)), _load_uma_budget),
    (frozenset(("ConcurrencyCategory", "get_semaphore")), _load_concurrency),
    (
        frozenset(
            ("FeatureFlags", "FeatureFlag", "FlagCategory", "FlagInfo", "FlagValidationError", "validate_sprint_flags")
        ),
        _load_feature_flags,
    ),
    (
        frozenset(
            (
                "duckdb_ro_pool",
                "duckdb_rw_pool",
                "duckdb_ro_acquire",
                "duckdb_ro_connection",
                "close_all_pools",
                "get_pool_stats",
            )
        ),
        _load_duckdb_pool,
    ),
    (frozenset(("aclose", "aclose_many")), _load_util),
    (
        frozenset(
            (
                "async_retry",
                "retry_if_exception",
                "retry_if_exception_type",
                "retry_if_result",
                "blitz_aware_stop",
                "exponential_backoff",
                "jitter_wait",
                "network_retry",
                "http_retry",
            )
        ),
        _load_async_retry,
    ),
)

# ISSUE-002: Sprint winddown hook using ModuleState
# MODERN-36 PERFORMANCE FIX: Cache cleanup for memory leak prevention


def clear_core_caches() -> dict[str, int]:
    """
    Clear all module-level caches in _core to free memory.

    Returns:
        Dict of cleared cache names to number of items cleared.

    MODERN-36 PERFORMANCE FIX / ISSUE-002: Call this from shutdown hooks
    to prevent memory leaks from accumulated lazy-loaded modules.
    Uses ModuleState for centralized, thread-safe cache management.
    """
    results = {"caches_cleared": _state.cache_size, "engines_cleared": _state.engine_count}

    # Delegate to ModuleState for proper cleanup
    _state.clear()

    return results


def clear_between_sprints() -> None:
    """
    ISSUE-002: Sprint winddown hook - deep clear all state.

    MUST be called at sprint winddown to prevent memory leaks.
    This clears:
    - All lazy caches
    - All loaded engines (with unload if supported)
    - Attribute index
    - Triggers garbage collection

    Usage:
        from _core import clear_between_sprints
        clear_between_sprints()  # Call at sprint end
    """
    _state.clear_between_sprints()
    print(f"Sprint cleanup: {_state.cache_size} caches, {_state.engine_count} engines cleared")


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
