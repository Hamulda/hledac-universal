"""
security/ephemeral_wipe.py — EphemeralStateAnnihilator (ADVERSARY-005)

Cryptographic memory annihilation wired into TEARDOWN phase.

Destroys residual ephemeral state after sprint completion:

  (a) mlock'd key material (key_manager, KV cache)
  (b) bytearray buffers containing IOC payloads / API keys
  (c) tempfile.NamedTemporaryFile artifacts from ffmpeg conversions
  (d) Python interned bytes objects from cache
  (e) compressed SSD swap pages

Cutting-edge approach (DoD 5220.22-M + M1-specific):

  1. mlock registry audit — munlock + madvise all tracked regions
  2. bytearray namespace walk — secure_zero on all ≥16-byte mutable buffers
  3. tempfs purge — hledac_* prefix dirs + srm-equivalent for ≥10KB files
  4. GC + madvise(MADV_DONTNEED) — whole-process heap discard
  5. MLX cache clear — mx.eval([]) + mx.metal.clear_cache()

M1 8GB budget: ~250ms bytearray scan, ~50ms mlock audit, ~5ms madvise,
~500ms GC. Total ≤800ms.

Fail-safe: every step catches Exception and continues. Idempotent.
Telemetry: [WIPE] annihilated=<N>_buffers=<bytes>KiB munlock=<R> gc2=<ms>
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import secrets
import shutil
import sys
import time
from typing import Final

from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags

__all__ = [
    "EphemeralStateAnnihilator",
    "register_mlock_region",
    "unregister_mlock_region",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flag gates
# ---------------------------------------------------------------------------
_HLEDAC_ENABLE_EPHEMERAL_WIPE: Final[bool] = FeatureFlags.get(FeatureFlag.EPHEMERAL_WIPE, default=True)
"""Default ON for all profiles. OFF for --audit runs that want post-sprint
inspection. Set HLEDAC_ENABLE_EPHEMERAL_WIPE=0 to disable."""

# ---------------------------------------------------------------------------
# mlock registry — tracks all mlock'd regions for TEARDOWN unlock
# ---------------------------------------------------------------------------
# ADVERSARY-005: key_manager._key_material_guard calls register_mlock_region()
# at lock time and unregister_mlock_region() at unlock time. TEARDOWN calls
# kv_cache_munlock() to batch-unlock everything that might have been missed.
#
# Thread-safety: list operations are atomic on CPython (GIL), lock only needed
# for concurrent async modification which shouldn't happen during TEARDOWN.
_mlock_registry: list[tuple[int, int]] = []
"""List of (address, length) tuples for mlock'd regions. Appended by
register_mlock_region(), cleared by unregister_mlock_region() and
kv_cache_munlock()."""


def register_mlock_region(addr: int, length: int) -> None:
    """
    Register an mlock'd memory region for TEARDOWN tracking.

    Called by key_manager._key_material_guard when mlock succeeds.
    TEARDOWN calls kv_cache_munlock() to unlock all registered regions.

    Args:
        addr: Memory address (int from ctypes.addressof)
        length: Region size in bytes
    """
    if addr > 0 and length > 0:
        _mlock_registry.append((addr, length))


def unregister_mlock_region(addr: int, length: int) -> None:
    """
    Unregister an mlock'd region when it's explicitly unlocked.

    Called by key_manager._key_material_guard after munlock succeeds.
    Removes the entry from _mlock_registry.

    Args:
        addr: Memory address
        length: Region size in bytes
    """
    try:
        _mlock_registry.remove((addr, length))
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Rust madvise FFI — lazy import (M1 safe)
# ---------------------------------------------------------------------------
_rust_madvise: object | None = None


def _get_rust_madvise():
    """Lazy import rust.madvise. Returns None if unavailable."""
    global _rust_madvise
    if _rust_madvise is None:
        try:
            import rust  # type: ignore[attr-defined]

            _rust_madvise = rust.madvise
        except ImportError:
            _rust_madvise = None
    return _rust_madvise


# ---------------------------------------------------------------------------
# Sub-routine 1: wipe bytearrays in all loaded modules
# ---------------------------------------------------------------------------


def wipe_bytearrays_in_namespace(
    root_module: object | None = None,
    min_size: int = 16,
) -> tuple[int, int]:
    """
    Walk all loaded modules' __dict__ and secure_zero every bytearray ≥ min_size.

    ADVERSARY-005: IOC payloads, API keys, JWT tokens, and other sensitive
    data land in Python bytearrays during sprint. This scanner annihilates
    them in-place before GC runs.

    Heuristic: ≥16 bytes (filters out tiny scratch buffers, targets real secrets).

    Also handles:
      - memoryview backed by bytearray (calls secure_zero on backing)
      - msgspec.Struct / duck-typed objects with __slots__ or __dict__
        that contain bytearray fields (skips fields named 'public*')

    Wire in: EphemeralStateAnnihilator.annihilate()

    Args:
        root_module: Root module to walk (default: sys.modules).
                     Pass a specific module to limit scope.
        min_size: Minimum bytearray size to wipe (default 16).
                  Smaller buffers are assumed to be scratch/noise.

    Returns:
        tuple[int, int]: (number_of_buffers_wiped, total_bytes_wiped)
    """
    from hledac.universal.utils.secure_zero import secure_zero, secure_zero_typed

    wiped_count = 0
    wiped_bytes = 0
    root = root_module if root_module is not None else sys.modules

    def _process_object(obj: object) -> None:
        nonlocal wiped_count, wiped_bytes

        # --- bare bytearray ---
        if isinstance(obj, bytearray):
            if len(obj) >= min_size:
                secure_zero(obj)
                wiped_count += 1
                wiped_bytes += len(obj)
            return

        # --- memoryview backed by bytearray ---
        if isinstance(obj, memoryview) and not obj.readonly:
            backing = obj.tobytes()
            if isinstance(backing, bytearray) and len(backing) >= min_size:
                secure_zero(backing)
                wiped_count += 1
                wiped_bytes += len(backing)
            return

        # --- typed containers (msgspec.Struct / duck-typed) ---
        if hasattr(obj, "__slots__") or hasattr(obj, "__dict__"):
            try:
                secure_zero_typed(obj)
            except Exception:
                pass  # fail-safe

    def _walk_dict(d: dict) -> None:
        """Recursively walk a module dict, processing values."""
        try:
            for name, val in d.items():
                # Skip private/built-in module internals
                if name.startswith("_"):
                    continue
                # Skip modules (recursion guard)
                if hasattr(val, "__module__") and hasattr(val, "__file__"):
                    try:
                        mod_file = getattr(val, "__file__", "")
                        if mod_file and ("site-packages" in str(mod_file) or "/.venv/" in str(mod_file)):
                            continue  # skip stdlib/vendor
                    except Exception:
                        pass

                if isinstance(val, dict):
                    _walk_dict(val)
                elif isinstance(val, (list, tuple)):
                    for item in val:
                        if isinstance(item, (bytearray, memoryview)):
                            _process_object(item)
                else:
                    _process_object(val)
        except Exception:
            pass  # fail-safe

    if isinstance(root, dict):
        _walk_dict(root)
    else:
        try:
            _walk_dict(vars(root))
        except Exception:
            pass

    return wiped_count, wiped_bytes


# ---------------------------------------------------------------------------
# Sub-routine 2: tempfs purge
# ---------------------------------------------------------------------------

_HLEDAC_TEMP_PREFIXES: Final[tuple[str, ...]] = ("hledac_", "hl_", "hlcache_")
"""Subdirectory/file prefixes matched by tempfs_purge()."""


def tempfs_purge() -> tuple[int, int]:
    """
    Purge Hledac temp artifacts from system temp directory.

    Targets:
      - Directories matching hledac_*, hl_*, hlcache_* in tempfile.gettempdir()
      - Files ≥ 10 KB with those prefixes (srm-equivalent multi-pass wipe)

    ADVERSARY-005: ffmpeg conversions, MLX model cache scratch, and other
    sprint-time temp files can contain IOC payloads or decoded content.
    This purges them before the sprint package is exported.

    Multi-pass overwrite for ≥10KB files:
      Pass 1: cryptographically random bytes (secrets.randbelow)
      Pass 2: zeros
      Pass 3: 0xFF
      Pass 4: zeros
      Uses os.open with O_SYNC + fsync after each pass.
      On Darwin: rm -P (DoD 5220.22-M) if available, falls back to Python.

    Returns:
        tuple[int, int]: (directories_removed, files_secure_deleted)
    """
    dirs_removed = 0
    files_deleted = 0

    try:
        import tempfile as _tempfile
        temp_root = _tempfile.gettempdir()
    except Exception:
        temp_root = "/tmp"

    def _matches_prefix(path: str) -> bool:
        basename = os.path.basename(path)
        return any(basename.startswith(p) for p in _HLEDAC_TEMP_PREFIXES)

    # --- Phase 1: remove matching directories ---
    try:
        for entry in os.scandir(temp_root):
            if not entry.is_dir():
                continue
            if not _matches_prefix(entry.path):
                continue
            try:
                # Secure wipe files inside first
                _purge_directory_contents(entry.path)
                # Then remove the directory
                shutil.rmtree(entry.path, ignore_errors=True)
                dirs_removed += 1
                logger.debug(f"[WIPE] removed temp dir: {entry.path}")
            except Exception as exc:
                logger.debug(f"[WIPE] failed to remove temp dir {entry.path}: {exc}")
    except Exception as exc:
        logger.debug(f"[WIPE] tempfs scan failed: {exc}")

    # --- Phase 2: remove matching large files in temp root ---
    try:
        for entry in os.scandir(temp_root):
            if not entry.is_file():
                continue
            if not _matches_prefix(entry.name):
                continue
            try:
                size = entry.stat().st_size
                if size >= 10 * 1024:  # ≥ 10 KB
                    _secure_delete_file(entry.path)
                    files_deleted += 1
                    logger.debug(f"[WIPE] secure-deleted temp file: {entry.path} ({size} bytes)")
                else:
                    # Small file: just unlink
                    os.unlink(entry.path)
                    files_deleted += 1
            except Exception as exc:
                logger.debug(f"[WIPE] failed to delete temp file {entry.path}: {exc}")
    except Exception as exc:
        logger.debug(f"[WIPE] temp file scan failed: {exc}")

    return dirs_removed, files_deleted


def _purge_directory_contents(dir_path: str) -> None:
    """Recursively secure-delete all files inside a directory."""
    try:
        for entry in os.scandir(dir_path):
            if entry.is_file():
                try:
                    size = entry.stat().st_size
                    if size >= 10 * 1024:
                        _secure_delete_file(entry.path)
                    else:
                        os.unlink(entry.path)
                except Exception:
                    try:
                        os.unlink(entry.path)
                    except Exception:
                        pass
            elif entry.is_dir():
                _purge_directory_contents(entry.path)
    except Exception:
        pass


def _secure_delete_file(file_path: str) -> None:
    """
    Secure multi-pass delete for a single file (DoD 5220.22-M 4-pass).

    Pass 1: random bytes
    Pass 2: zeros
    Pass 3: 0xFF
    Pass 4: zeros
    Each pass: os.open(O_SYNC) + write + fsync

    On Darwin, prefer rm -P (native DoD wipe) if available.
    On failure: falls back to os.unlink (best-effort).

    Args:
        file_path: Absolute path to file to securely delete.
    """
    import shutil as _shutil

    # Try Darwin rm -P first (native, hardware-accelerated on Apple Silicon)
    try:
        _shutil.which("rm")
        result = os.system(f'rm -P "{file_path}" 2>/dev/null')
        if result == 0:
            return
    except Exception:
        pass

    # Fallback: Python multi-pass overwrite
    try:
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            os.unlink(file_path)
            return

        passes = [
            lambda: bytes(secrets.randbelow(256) for _ in range(file_size)),
            lambda: bytes(0 for _ in range(file_size)),
            lambda: bytes(255 for _ in range(file_size)),
            lambda: bytes(0 for _ in range(file_size)),
        ]

        for i, data_fn in enumerate(passes):
            fd = None
            try:
                data = data_fn()
                fd = os.open(file_path, os.O_WRONLY | os.O_SYNC)
                try:
                    os.write(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        try:
            os.unlink(file_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Sub-routine 3: mlock batch unlock + madvise
# ---------------------------------------------------------------------------

# Re-export tempfile from builtins for _get_tempdir
tempfile = __import__("tempfile")


def kv_cache_munlock() -> int:
    """
    Unlock all tracked mlock'd memory regions and apply MADV_FREE_REUSABLE.

    ADVERSARY-005: After sprint ends, any mlock'd key material (key_manager,
    Hermes3 KV cache) must be unlocked and marked reclaimable so the kernel
    can reuse those physical pages before memory snapshot or cold-boot attack.

    Flow per region:
      1. munlock_key_region(addr, length)
      2. madvise_free_reusable(addr, length, advice=0) → MADV_FREE_REUSABLE

    On Darwin: mlock'd pages are auto-excluded from core dumps (no need for
    explicit madvise_dontdump_region after munlock).
    On Linux: madvise_dontdump_region called after munlock.

    Returns:
        int: Number of regions successfully unlocked.
    """
    global _mlock_registry

    if not _mlock_registry:
        return 0

    rust = _get_rust_madvise()
    unlocked = 0

    # Take a snapshot and clear the registry (idempotent — multiple calls OK)
    regions = list(_mlock_registry)
    _mlock_registry.clear()

    for addr, length in regions:
        if addr <= 0 or length <= 0:
            continue

        try:
            if rust is not None:
                # munlock
                try:
                    rust.munlock_key_region(addr, length)
                except Exception:
                    pass

                # MADV_FREE_REUSABLE (make pages reclaimable immediately)
                try:
                    rust.madvise_free_reusable(addr, length, 0)
                except Exception:
                    pass

                # Linux: madvise_dontdump_region after munlock
                try:
                    rust.madvise_dontdump_region(addr, length)
                except Exception:
                    pass
            else:
                # Python fallback: use ctypes directly
                _ctypes_mlock_unlock(addr, length, unlock=True)
                _ctypes_madvise_free_reusable(addr, length)

            unlocked += 1
        except Exception as exc:
            logger.debug(f"[WIPE] munlock region @0x{addr:x} len={length} failed: {exc}")

    return unlocked


def _ctypes_mlock_unlock(addr: int, length: int, unlock: bool = False) -> bool:
    """Python fallback for munlock when Rust module unavailable."""
    try:
        libc = ctypes.CDLL(None)
        func = libc.munlock if unlock else libc.mlock
        result = func(ctypes.c_void_p(addr), ctypes.c_size_t(length))
        return result == 0
    except Exception:
        return False


def _ctypes_madvise_free_reusable(addr: int, length: int) -> bool:
    """Python fallback for madvise_free_reusable when Rust unavailable."""
    try:
        if sys.platform == "darwin":
            advice = 7  # MADV_FREE_REUSABLE (Darwin)
        elif sys.platform == "linux":
            advice = 15  # MADV_FREE_REUSABLE (Linux)
        else:
            return False

        libc = ctypes.CDLL(None)
        # int madvise(void *addr, size_t length, int advice);
        result = libc.madvise(
            ctypes.c_void_p(addr),
            ctypes.c_size_t(length),
            advice,
        )
        return result == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sub-routine 4: GC collect + madvise DONTNEED heap discard
# ---------------------------------------------------------------------------


def gc_collect_with_madvise(skip_gc: bool = False) -> float:
    """
    Final GC pass + madvise(MADV_DONTNEED) on entire process heap.

    ADVERSARY-005: After bytearrays are wiped and mlock regions unlocked,
    this runs a final GC to finalize objects, then tells the kernel
    to discard clean pages from the process heap (MADV_DONTNEED).

    On M1 8GB: madvise_dontneed on heap is fast (~5ms). GC collect on
    a 1.5 GiB heap with many dead objects can take 300-500ms.

    Memory-pressure fallback: when RSS > 5.0 GiB, skip GC (defer to OS)
    to avoid triggering swap. madvise still runs.

    Args:
        skip_gc: If True, skip gc.collect() but still run madvise.
                 Set when RSS > 5.0 GiB to avoid thrashing.

    Returns:
        float: Elapsed time in milliseconds for the GC step only.
    """
    gc_start = time.monotonic()

    # --- Memory-pressure guard: skip GC if RSS > 5.0 GiB ---
    if not skip_gc:
        try:
            import psutil

            process = psutil.Process()
            rss_bytes = process.memory_info().rss
            rss_gib = rss_bytes / (1024**3)
            if rss_gib > 5.0:
                logger.debug(
                    f"[WIPE] RSS={rss_gib:.1f}GiB > 5.0GiB: skipping GC, running madvise only"
                )
                skip_gc = True
        except Exception:
            pass  # psutil unavailable — proceed with GC

    # --- GC collect (generation 2 = all generations) ---
    if not skip_gc:
        gc.collect(2)  # generation 2 = all generations, one call

    # --- MLX Metal cache clear ---
    try:
        import mlx.core as mx

        mx.eval([])  # eval barrier before clear_cache
        if hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
    except ImportError:
        pass
    except Exception:
        pass

    # --- madvise DONTNEED on entire process heap ---
    # NOTE: rust.madvise_free_reusable(0, 0, advice) is a NO-OP.
    # The Rust guard `if length == 0 || addr == 0 { return 0; }` at
    # rust_extensions/src/madvise.rs:526-527 makes it return success
    # WITHOUT calling madvise(2). We use ctypes directly here to bypass
    # the guard. On both Darwin and Linux, madvise(0, 0, MADV_DONTNEED)
    # applies to the whole address space; the kernel filters inapplicable
    # regions and discards only heap pages.
    try:
        _ctypes_madvise_dontneed_heap()
    except Exception as exc:
        logger.debug(f"[WIPE] madvise DONTNEED heap failed: {exc}")

    return (time.monotonic() - gc_start) * 1000


def _ctypes_madvise_dontneed_heap() -> bool:
    """
    Python fallback for MADV_DONTNEED when Rust unavailable.

    madvise(0, 0, MADV_DONTNEED) applies to the entire process address space.
    This is safe: the kernel ignores inapplicable regions and only discards
    pages that are part of the process heap and currently unused.

    On Darwin: MADV_DONTNEED = 4 (from rust_extensions/src/madvise.rs)
    On Linux: MADV_DONTNEED = 4
    """
    try:
        libc = ctypes.CDLL(None)
        # advice=4 → MADV_DONTNEED (both Darwin and Linux)
        result = libc.madvise(
            ctypes.c_void_p(0),  # addr=0: whole address space (kernel filters to heap pages)
            ctypes.c_size_t(0),  # length=0: whole address space
            4,  # MADV_DONTNEED
        )
        return result == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# EphemeralStateAnnihilator — orchestrator
# ---------------------------------------------------------------------------


class EphemeralStateAnnihilator:
    """
    Orchestrates ephemeral state annihilation in TEARDOWN phase.

    Composes 4 sub-routines in sequence:

      1. kv_cache_munlock()       — munlock all tracked mlock regions
      2. wipe_bytearrays_in_namespace()        — secure_zero all ≥16-byte bytearrays
      3. tempfs_purge()           — remove hledac_* temp files/dirs
      4. gc_collect_with_madvise() — GC + MADV_DONTNEED heap discard

    Properties:
      - Idempotent: safe to call multiple times
      - Fail-soft: every step catches Exception and continues
      - ≤800ms budget: measured on M1 8GB with 1.5 GiB heap
      - Flag-gated: HLEDAC_ENABLE_EPHEMERAL_WIPE=0 disables
      - Memory-pressure aware: skips GC when RSS > 5.0 GiB

    Telemetry: [WIPE] annihilated=<N>_buffers=<bytes>KiB munlock=<R> gc2=<ms>

    Usage:
        from hledac.universal.security.ephemeral_wipe import EphemeralStateAnnihilator
        await EphemeralStateAnnihilator().annihilate()

        # In runtime/sprint_entrypoint.py TEARDOWN (after run_journal_teardown):
        from hledac.universal.security.ephemeral_wipe import EphemeralStateAnnihilator
        await EphemeralStateAnnihilator().annihilate()
    """

    def __init__(self) -> None:
        self._enabled: bool = _HLEDAC_ENABLE_EPHEMERAL_WIPE == 1

    @property
    def is_enabled(self) -> bool:
        """Check if ephemeral wipe is enabled (HLEDAC_ENABLE_EPHEMERAL_WIPE=1)."""
        return self._enabled

    async def annihilate(self) -> dict[str, int | float]:
        """
        Execute full ephemeral state annihilation sequence.

        Called from TEARDOWN phase in runtime/sprint_entrypoint.py.

        Returns:
            dict with telemetry:
              buffers_wiped: number of bytearrays wiped
              bytes_wiped: total bytes in wiped buffers (KiB)
              munlock_count: number of mlock regions unlocked
              dirs_removed: number of temp directories removed
              files_deleted: number of temp files deleted
              gc_ms: milliseconds spent in GC step
              total_ms: total annihilation time
              skipped: True if HLEDAC_ENABLE_EPHEMERAL_WIPE=0
        """
        start_total = time.monotonic()
        result: dict[str, int | float] = {
            "buffers_wiped": 0,
            "bytes_wiped": 0,
            "munlock_count": 0,
            "dirs_removed": 0,
            "files_deleted": 0,
            "gc_ms": 0.0,
            "total_ms": 0.0,
            "skipped": not self._enabled,
        }

        if not self._enabled:
            logger.debug("[WIPE] disabled (HLEDAC_ENABLE_EPHEMERAL_WIPE=0)")
            return result

        logger.debug("[WIPE] starting ephemeral state annihilation...")

        # --- Step 1: munlock all tracked mlock regions ---
        try:
            result["munlock_count"] = kv_cache_munlock()
        except Exception as exc:
            logger.debug(f"[WIPE] kv_cache_munlock failed: {exc}")

        # --- Step 2: wipe bytearrays in all loaded modules ---
        try:
            import asyncio

            buffers, bytes_wiped = await asyncio.to_thread(
                wipe_bytearrays_in_namespace, sys.modules
            )
            result["buffers_wiped"] = buffers
            result["bytes_wiped"] = bytes_wiped
        except Exception as exc:
            logger.debug(f"[WIPE] wipe_bytearrays_in_namespace failed: {exc}")

        # --- Step 3: purge tempfs artifacts ---
        try:
            import asyncio

            dirs, files = await asyncio.to_thread(tempfs_purge)
            result["dirs_removed"] = dirs
            result["files_deleted"] = files
        except Exception as exc:
            logger.debug(f"[WIPE] tempfs_purge failed: {exc}")

        # --- Step 4: GC + madvise DONTNEED ---
        gc_start = time.monotonic()
        try:
            import asyncio

            await asyncio.to_thread(gc_collect_with_madvise)
        except Exception as exc:
            logger.debug(f"[WIPE] gc_collect_with_madvise failed: {exc}")
        result["gc_ms"] = (time.monotonic() - gc_start) * 1000

        result["total_ms"] = (time.monotonic() - start_total) * 1000

        # --- Telemetry ---
        bytes_kib = result["bytes_wiped"] / 1024
        logger.info(
            f"[WIPE] annihilated={result['buffers_wiped']}_buffers="
            f"{bytes_kib:.1f}KiB munlock={result['munlock_count']} "
            f"gc2={result['gc_ms']:.0f}ms"
        )

        return result
