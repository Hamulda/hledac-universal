"""
Dedup Path Resolution Service — P1-14

Centralized path resolver for all dedup storage (LMDB + Bloom filter + semantic cache).
Honours env overrides or returns defaults.

Env vars:
  HLEDAC_DEDUP_LMDB_PATH   → custom LMDB path (overrides full dedup.lmdb location)
  HLEDAC_DEDUP_BLOOM_DIR   → custom Bloom filter mmap directory

Returns dict with canonical dedup paths:
  lmdb_root       — base LMDB directory
  dedup_lmdb      — full path to dedup.lmdb
  bloom_dir       — Bloom filter mmap directory
  bloom_active    — active generation mmap path
  bloom_previous  — previous generation mmap path
  bloom_lock      — fcntl lock file for bloom init race prevention

P1-14 invariants:
  - Always-on: no feature flag, no env var toggle
  - Fail-safe: any error returns defaults
  - M1 8GB safe: bounded paths, no unbounded growth
  - Single source of truth: one function resolves all dedup paths
  - Backward compat: DedupManager/dedup_lmdb_path takes precedence over env if provided
  - Thread-safe: singleton initialized once, read-only thereafter

P3-06 fix: Returns str paths instead of pathlib.Path for hot-path performance.
pathlib.Path is 5-10× slower than os.path on M1. Type hints keep Path for
compatibility, but internal resolution uses os.path.join.
"""

import os
import threading
from typing import Final, cast

from _core.lock_registry import LockCategory, register_lock

# Default base under ~/.hledac/ — co-located with LMDB_STORE_ROOT
_LMDB_STORE_DEFAULT: Final[str] = "~/.hledac/lmdb_store"

# Hard fallback if both env + LMDB_STORE_ROOT unavailable
_LMDB_ROOT_FALLBACK_STR: Final[str] = os.path.expanduser("~/.hledac/lmdb_store")

# P1-14: Sentinel for "not yet resolved"
_UNRESOLVED: Final[object] = object()

# Module-level singleton (thread-safe via GIL, immutable after init)
_DEFAULT_PATHS: dict[str, str] | object  # type: ignore[valid-type]


@register_lock(LockCategory.CONFIG)
def _DEFAULT_PATHS_LOCK() -> threading.Lock:
    """Module-level lock for dedup paths singleton."""
    return threading.Lock()


def resolve_dedup_paths(env_prefix: str = "HLEDAC_DEDUP") -> dict[str, str]:
    """
    Resolve all dedup storage paths.

    Env precedence for LMDB root:
      1. HLEDAC_DEDUP_LMDB_PATH (full path override)
      2. HLEDAC_LMDB_STORE (LMDB_STORE_ROOT env)
      3. ~/.hledac/lmdb_store (default)

    Env precedence for Bloom directory:
      1. HLEDAC_DEDUP_BLOOM_DIR
      2. <lmdb_root>/bloom (co-located)

    Returns:
        dict with keys: lmdb_root, dedup_lmdb, bloom_dir, bloom_active,
                        bloom_previous, bloom_lock (all as str for os.path speed)
    """
    # LMDB root resolution (mirrors paths.py LMDB_STORE_ROOT logic)
    env_lmdb_override = os.environ.get(f"{env_prefix}_LMDB_PATH")
    env_store_root = os.environ.get("HLEDAC_LMDB_STORE")

    if env_lmdb_override:
        lmdb_root = os.path.expanduser(env_lmdb_override)
    elif env_store_root:
        lmdb_root = os.path.expanduser(env_store_root)
    else:
        lmdb_root = os.path.expanduser(_LMDB_STORE_DEFAULT)

    # Bloom directory — co-located under lmdb_root by default
    bloom_dir = os.environ.get(f"{env_prefix}_BLOOM_DIR") or os.path.join(lmdb_root, "bloom")

    # Ensure directories exist
    try:
        os.makedirs(lmdb_root, exist_ok=True)
    except Exception:
        lmdb_root = _LMDB_ROOT_FALLBACK_STR
        os.makedirs(lmdb_root, exist_ok=True)

    try:
        os.makedirs(bloom_dir, exist_ok=True)
    except Exception:
        # Fall back to lmdb_root/BLOOM if bloom_dir is inaccessible
        bloom_dir = os.path.join(lmdb_root, "bloom")
        os.makedirs(bloom_dir, exist_ok=True)

    return {
        "lmdb_root": lmdb_root,
        "dedup_lmdb": os.path.join(lmdb_root, "dedup.lmdb"),
        "bloom_dir": bloom_dir,
        "bloom_active": os.path.join(bloom_dir, "bloom_active.mmap"),
        "bloom_previous": os.path.join(bloom_dir, "bloom_previous.mmap"),
        "bloom_lock": os.path.join(bloom_dir, "bloom.lock"),
    }


def get_dedup_paths() -> dict[str, str]:
    """
    Thread-safe singleton accessor for default dedup paths.

    Resolves once on first call; subsequent calls return the cached dict.
    Safe for use across multiple DedupManager/RotatingBloomFilter instances.
    """
    global _DEFAULT_PATHS
    if _DEFAULT_PATHS is _UNRESOLVED:
        with _DEFAULT_PATHS_LOCK():
            # Double-check after acquiring lock
            if _DEFAULT_PATHS is _UNRESOLVED:
                _DEFAULT_PATHS = resolve_dedup_paths()
    return cast(dict[str, str], _DEFAULT_PATHS)


def reset_dedup_paths() -> None:
    """
    Reset the singleton — for testing only.

    Clears the cached paths so next get_dedup_paths() call re-resolves.
    NOT thread-safe for concurrent use; call only in isolated test contexts.
    """
    global _DEFAULT_PATHS
    with _DEFAULT_PATHS_LOCK():
        _DEFAULT_PATHS = _UNRESOLVED
