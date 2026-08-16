"""
[NEXUS]-018-03: Mach Kernel Zero-Copy Remapping — Python Bridge

Provides zero-copy file remapping between Hledac orchestrator and sandboxed



subprocesses using Darwin's mach_vm_remap(2) via the Rust extension.

## Architecture

    Hledac (parent)                    Sandbox (child)
    ┌──────────────────┐                ┌──────────────────┐
    │  mmap(file)      │                │  mmap(/dev/zero) │
    │  ↓               │                │  ↓               │
    │  [src_addr] ──── │── COW fork ──→│  [target_addr]   │
    │                  │   handover    │                  │
    │  pipe_write ─────│── pipe ──────→│  pipe_read       │
    └──────────────────┘                └──────────────────┘

Pages count toward CHILD RSS (sandbox), not parent (Hledac).
On M1 8GB, the 500 MB sits in the sandbox's RSS, not Hledac's.

## Usage

    from hledac.universal.security.mach_remap import MachRemapBridge

    bridge = MachRemapBridge()
    result = bridge.remap_for_sandbox("/path/to/large.pdf", 500_000_000)
    if result:
        child_pid, addr, size = result
        # child is ready, exec the analysis binary
    else:
        # fallback to tempfile.NamedTemporaryFile (always available)

## Feature Gates

    HLEDAC_ENABLE_MACH_REMAP=1  — enable (default: OFF)
    HLEDAC_MACH_REMAP_MIN_SIZE  — minimum file size to consider (default: 100 MB)

## Fail-Soft Invariants

    - Returns None on ANY error — caller MUST fall back to tempfile
    - M1 8GB guard: available < 1.5 GiB → None, log skipped
    - Single active remap at a time (Rust semaphore)
    - Opt-in only (HLEDAC_ENABLE_MACH_REMAP=1)
"""
from __future__ import annotations

import asyncio
import logging
from hledac.universal.utils.asyncx import safe_wait_for
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    pass

from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
from _core import aclose

logger = logging.getLogger(__name__)

# ─── Feature Gates ───────────────────────────────────────────────────────────

_HLEDAC_ENABLE_MACH_REMAP: bool = FeatureFlags.get(FeatureFlag.MACH_REMAP, default=False)

# Minimum file size (bytes) before attempting Mach remap.
# Below this, tempfile path is always faster (no fork overhead).
_HLEDAC_MACH_REMAP_MIN_SIZE: int = int(
    os.environ.get("HLEDAC_MACH_REMAP_MIN_SIZE", str(100 * 1024 * 1024))  # 100 MB
    )

# ─── Lazy Import ─────────────────────────────────────────────────────────────

# MachRemapError is raised by the Rust extension.
# We lazy-import the Rust module to avoid compile-time errors when
# the mach feature is not enabled.
_MACH_REMOTE_MODULE: object | None = None


def _get_mach_module() -> object | None:
    """
    Lazily import the Rust mach_remap module.

    Returns None if:
      - HLEDAC_ENABLE_MACH_REMAP != "1"
      - Platform is not macOS
      - Rust extension not compiled with --features mach
      - Any import error
    """
    global _MACH_REMOTE_MODULE
    if _MACH_REMOTE_MODULE is not None:
        return _MACH_REMOTE_MODULE
    if not _HLEDAC_ENABLE_MACH_REMAP:
        return None

    try:
        # Import the Rust extension — requires --features mach during compile
        # and HLEDAC_ENABLE_MACH_REMAP=1 at runtime
        from hledac_rust_extensions import mach_remap as _mod
        _MACH_REMOTE_MODULE = _mod
        return _mod
    except ImportError as exc:
        logger.debug(
            "[MACH-REMAP] Rust extension unavailable: %s (set HLEDAC_ENABLE_MACH_REMAP=1 "
            "and compile with --features mach if needed)",
            exc,
    )
        _MACH_REMOTE_MODULE = None
        return None


# ─── Result Types ────────────────────────────────────────────────────────────


class MachRemapResult(NamedTuple):
    """Result of a successful Mach remap operation."""

    child_pid: int
    """PID of the sandbox child process."""

    file_descriptor: int
    """
    File descriptor (pipe) for passing data to the child.

    After remap, the child waits for data on this pipe, then executes
    the analysis binary with the remapped buffer.
    """

    mapped_addr: int
    """Virtual address in the parent's address space."""

    mapped_size: int
    """Size of the remapped region (page-aligned)."""


class MachRemapError(Exception):
    """
    Raised when Mach remap fails.

    Parent MUST catch this and fall back to tempfile.NamedTemporaryFile.
    This exception is intentionally NOT a HledacError (not actionable for user).
    """

    def __init__(self, message: str, errno_code: str):
        super().__init__(message)
        self.message = message
        self.errno_code = errno_code


# ─── Core Bridge ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _MachRemapBridge:
    """
    Mach vm_remap zero-copy bridge between Hledac and sandboxed subprocesses.

    Thread-safety: this class is async-safe and designed for use from
    the main asyncio event loop. Multiple concurrent remaps are blocked
    by the Rust-level semaphore.

    ## Usage

        bridge = MachRemapBridge()
        result = await bridge.remap_for_sandbox("/path/to/file.pdf", 500_000_000)
        if result:
            # child is waiting on result.file_descriptor
            proc = await asyncio.create_subprocess_exec(
                "/path/to/analyzer",
                stdin=result.file_descriptor,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
    )
        else:
            # fallback to tempfile
    """

    _enabled: bool = True

    def _can_remap(self, file_size: int) -> bool:
        """
        Check all preconditions before attempting Mach remap.

        Returns False (and logs reason) if any guard fails.
        """
        if not _HLEDAC_ENABLE_MACH_REMAP:
            logger.debug(
                "[MACH-REMAP] skipped: HLEDAC_ENABLE_MACH_REMAP != 1"
    )
            return False

        if file_size < _HLEDAC_MACH_REMAP_MIN_SIZE:
            logger.debug(
                "[MACH-REMAP] skipped: file_size=%d < min_size=%d",
                file_size, _HLEDAC_MACH_REMAP_MIN_SIZE,
    )
            return False

        if sys.platform != "darwin":
            logger.debug(
                "[MACH-REMAP] skipped: platform=%r (macOS only)",
                sys.platform,
    )
            return False

        # Check Rust-level can_remap() which probes available memory
        mod = _get_mach_module()
        if mod is None:
            logger.debug("[MACH-REMAP] skipped: Rust module unavailable")
            return False

        try:
            if not mod.can_remap():
                logger.warning(
                    "[MACH-REMAP] skipped: can_remap() returned False "
                    "(memory guard or not enabled)"
    )
                return False
        except Exception as exc:
            logger.debug("[MACH-REMAP] can_remap() raised: %s", exc)
            return False

        return True

    def remap_for_sandbox(
        self,
        file_path: str | Path,
        file_size: int | None = None,
    ) -> MachRemapResult | None:
        """
        Attempt zero-copy Mach remap of a file to a sandboxed subprocess.

        This is the primary public API. It:
          1. Checks preconditions (feature gate, size, platform, memory)
          2. Calls rust.mach_remap.vm_remap_and_exec() with the analysis script
             embedded as a Rust heredoc string
          3. Returns MachRemapResult on success, None on failure

        The complete pipeline:
          Rust vm_remap_and_exec():
            fork()
            child: mmap(file) → mach_vm_remap(self, addr, size)
            child: write handover [pid(4)+addr(8)+size(8)] to response_pipe
            child: exec python -c "<analysis_script>"
            child: python reads remapped file at file_path
            child: python writes results to result file
            child: exit
          Python:
            read response_pipe → get child_pid
            waitpid(child_pid)
            read result file
            return SandboxResult

        On ANY failure, returns None — caller MUST fall back to tempfile.
        """
        file_path = Path(file_path)

        # Determine file size if not provided
        if file_size is None:
            try:
                file_size = file_path.stat().st_size
            except OSError:
                logger.debug(
                    "[MACH-REMAP] skipped: cannot stat %s",
                    file_path,
    )
                return None

        # Check all guards first (early exit)
        if not self._can_remap(file_size):
            return None

        mod = _get_mach_module()
        if mod is None:
            return None

        try:
            child_pid, mapped_addr, mapped_size = mod.vm_remap_and_exec(
                str(file_path),
                file_size,
    )
            logger.info(
                "[MACH-REMAP] vm_remap_and_exec: pid=%d addr=0x%x size=%d path=%s",
                child_pid, mapped_addr, mapped_size, file_path.name,
    )
            return MachRemapResult(
                child_pid=child_pid,
                file_descriptor=-1,
                mapped_addr=mapped_addr,
                mapped_size=mapped_size,
    )
        except MachRemapError as exc:
            logger.debug(
                "[MACH-REMAP] remap failed: %s (%s) — falling back to tempfile",
                exc.message, exc.errno_code,
    )
            return None
        except Exception as exc:
            # Catch everything — fail-soft, never propagate
            logger.debug(
                "[MACH-REMAP] unexpected error: %s — falling back to tempfile",
                exc,
    )
            return None

    def get_stats(self) -> dict:
        """
        Get Mach remap statistics.

        Returns a dict with:
          - enabled: bool
          - total_bytes: int (cumulative bytes remapped this session)
          - in_progress: bool
          - min_size_threshold: int
        """
        mod = _get_mach_module()
        if mod is None:
            return {
                "enabled": False,
                "total_bytes": 0,
                "in_progress": False,
                "min_size_threshold": _HLEDAC_MACH_REMAP_MIN_SIZE,
            }

        try:
            stats = mod.remap_stats()
            return {
                "enabled": stats.enabled,
                "total_bytes": stats.total_bytes,
                "in_progress": stats.in_progress,
                "min_size_threshold": _HLEDAC_MACH_REMAP_MIN_SIZE,
            }
        except Exception:
            return {
                "enabled": _HLEDAC_ENABLE_MACH_REMAP,
                "total_bytes": 0,
                "in_progress": False,
                "min_size_threshold": _HLEDAC_MACH_REMAP_MIN_SIZE,
            }


# ─── Singleton ───────────────────────────────────────────────────────────────

_bridge_instance: _MachRemapBridge | None = None


def get_mach_remap_bridge() -> _MachRemapBridge:
    """Get the singleton MachRemapBridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = _MachRemapBridge()
    return _bridge_instance


# ─── Async Convenience ───────────────────────────────────────────────────────


async def remap_file_async(
    file_path: str | Path,
    file_size: int | None = None,
) -> MachRemapResult | None:
    """
    Async wrapper for MachRemapBridge.remap_for_sandbox.

    Runs the (brief) remap operation in a thread pool to avoid blocking
    the asyncio event loop.
    """
    bridge = get_mach_remap_bridge()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        bridge.remap_for_sandbox,
        str(file_path),
        file_size,
    )


# ─── Fallback Tempfile ───────────────────────────────────────────────────────


def create_tempfile_for_sandbox(
    file_path: str | Path,
    delete: bool = True,
) -> tuple[Path, int]:
    """
    Fallback: copy a file to a temporary location for sandbox consumption.

    This is the traditional approach — slower than Mach remap but always works.

    Args:
        file_path: Source file to copy
        delete: Whether to delete the temp file on process exit (default True)

    Returns:
        (temp_path, file_size) — caller uses temp_path to pass to sandbox
    """
    src = Path(file_path)
    size = src.stat().st_size

    # Use same directory as tempfile for consistency with existing code
    with tempfile.NamedTemporaryFile(
        suffix=src.suffix,
        delete=delete,
        dir=tempfile.gettempdir(),
    ) as tmp:
        tmp.write(src.read_bytes())
        return Path(tmp.name), size


# ─── High-Level Sandbox Helper ───────────────────────────────────────────────


async def run_with_zero_copy_sandbox(
    file_path: str | Path,
    analysis_cmd: list[str],
    timeout_s: float = 30.0,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """
    Run an analysis command in a sandboxed subprocess with zero-copy file transfer.

    Strategy:
      1. Attempt Mach vm_remap (zero-copy, ~0ms I/O)
      2. On failure: fall back to tempfile.NamedTemporaryFile copy (~500ms for 500MB)

    Args:
        file_path: File to analyze
        analysis_cmd: Command to run in sandboxed subprocess
        timeout_s: Execution timeout
        env: Environment variables (stripped of secrets automatically)

    Returns:
        subprocess.CompletedProcess with stdout/stderr/returncode
    """
    file_path = Path(file_path)
    file_size = file_path.stat().st_size

    bridge = get_mach_remap_bridge()

    # Strip sensitive environment variables
    safe_env = {
        k: v for k, v in (env or os.environ).items()
        if not any(
            prefix in k
            for prefix in (
                "API_", "KEY_", "TOKEN", "SECRET", "HLEDAC_",
                "SHODAN", "CENSYS", "GREYNOISE",
    )
        )
    }

    # Strategy 1: Try Mach remap
    remap_result = await remap_file_async(file_path, file_size)

    if remap_result is not None:
        logger.info(
            "[MACH-REMAP] Using zero-copy path: pid=%d addr=0x%x",
            remap_result.child_pid, remap_result.mapped_addr,
    )
        # Note: In the full implementation, the Rust bridge handles
        # exec()ing the analysis_cmd in the child process.
        # For now, fall through to tempfile path (see TODO below).

    # Strategy 2: Fallback to tempfile
    temp_path, _ = await asyncio.to_thread(
        create_tempfile_for_sandbox, file_path
    )

    logger.debug(
        "[MACH-REMAP] Using tempfile fallback: %s (Mach remap unavailable)",
        temp_path,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *analysis_cmd,
            env=safe_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
    )
        stdout, stderr = await safe_wait_for(
            proc.communicate(), timeout=timeout_s
    )
        return subprocess.CompletedProcess(
            args=analysis_cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
    )
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # noqa: BLE001
                pass
