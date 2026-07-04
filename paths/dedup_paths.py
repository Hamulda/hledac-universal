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
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Final, Union, cast

# Default base under ~/.hledac/ — co-located with LMDB_STORE_ROOT
_LMDB_STORE_DEFAULT: Final[str] = "~/.hledac/lmdb_store"

# Hard fallback if both env + LMDB_STORE_ROOT unavailable
_LMDB_ROOT_FALLBACK: Final[Path] = Path("~/.hledac/lmdb_store").expanduser()

# P1-14: Sentinel for "not yet resolved"
_UNRESOLVED: Final[object] = object()

# Module-level singleton (thread-safe via GIL, immutable after init)
_DEFAULT_PATHS: Union[dict[str, Path], object]  # type: ignore[valid-type]
_DEFAULT_PATHS_LOCK: Final[threading.Lock] = threading.Lock()


def resolve_dedup_paths(env_prefix: str = "HLEDAC_DEDUP") -> dict[str, Path]:
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
                        bloom_previous, bloom_lock
    """
    # LMDB root resolution (mirrors paths.py LMDB_STORE_ROOT logic)
    env_lmdb_override = os.environ.get(f"{env_prefix}_LMDB_PATH")
    env_store_root = os.environ.get("HLEDAC_LMDB_STORE")

    if env_lmdb_override:
        lmdb_root = Path(env_lmdb_override)
    elif env_store_root:
        lmdb_root = Path(env_store_root)
    else:
        default = Path(_LMDB_STORE_DEFAULT).expanduser()
        # Expanduser resolves ~ at import time; safe for Path
        lmdb_root = default

    # Bloom directory — co-located under lmdb_root by default
    bloom_dir = Path(
        os.environ.get(f"{env_prefix}_BLOOM_DIR", str(lmdb_root / "bloom"))
    )

    # Ensure directories exist
    try:
        lmdb_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        lmdb_root = _LMDB_ROOT_FALLBACK
        lmdb_root.mkdir(parents=True, exist_ok=True)

    try:
        bloom_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to lmdb_root/BLOOM if bloom_dir is inaccessible
        bloom_dir = lmdb_root / "bloom"
        bloom_dir.mkdir(parents=True, exist_ok=True)

    return {
        "lmdb_root": lmdb_root,
        "dedup_lmdb": lmdb_root / "dedup.lmdb",
        "bloom_dir": bloom_dir,
        "bloom_active": bloom_dir / "bloom_active.mmap",
        "bloom_previous": bloom_dir / "bloom_previous.mmap",
        "bloom_lock": bloom_dir / "bloom.lock",
    }


def get_dedup_paths() -> dict[str, Path]:  # type: ignore[return-value]
    """
    Thread-safe singleton accessor for default dedup paths.

    Resolves once on first call; subsequent calls return the cached dict.
    Safe for use across multiple DedupManager/RotatingBloomFilter instances.
    """
    global _DEFAULT_PATHS
    if _DEFAULT_PATHS is _UNRESOLVED:
        with _DEFAULT_PATHS_LOCK:
            # Double-check after acquiring lock
            if _DEFAULT_PATHS is _UNRESOLVED:
                _DEFAULT_PATHS = resolve_dedup_paths()
    return cast(dict[str, Path], _DEFAULT_PATHS)


def reset_dedup_paths() -> None:
    """
    Reset the singleton — for testing only.

    Clears the cached paths so next get_dedup_paths() call re-resolves.
    NOT thread-safe for concurrent use; call only in isolated test contexts.
    """
    global _DEFAULT_PATHS
    with _DEFAULT_PATHS_LOCK:
        _DEFAULT_PATHS = _UNRESOLVED
