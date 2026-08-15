"""
Power assertion module — prevents macOS sleep during sprint execution.

APEX-1001: MacBook Air M1 with closed lid enters sleep after ~30s (default pmset).

This kills active sprints, losing all in-progress work with no evidence artifacts.

This module provides:
  1. Primary: PyObjC IOKit IOPMAssertionCreateWithName / IOPMAssertionRelease
     — native macOS power assertion, zero subprocess overhead
  2. Fallback: caffeinate -dimsu -w <pid> subprocess guard
     — works without PyObjC, ~2MB RSS overhead

Usage:
    assertion = PowerAssertion.acquire("sprint_8sa_xxx")
    try:
        ... sprint work ...
    finally:
        assertion.release()

Or as context manager:
    with PowerAssertion("sprint_8sa_xxx") as assertion:
        ... sprint work ...

ALWAYS-ON: No ENV toggle. Sprints ALWAYS need uninterrupted execution.
M1 8GB UMA safe: IOPMAssertion is a kernel-level flag, zero memory overhead.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# Assertion type constants (IOKit Power Management)
# kIOPMAssertionTypeNoIdleSleep — prevents system idle sleep (lid close, display off timer)
_IOPM_ASSERTION_TYPE_NO_IDLE_SLEEP = "NoIdleSleepAssertion"
# kIOPMAssertionTypePreventUserIdleSystemSleep — prevents user-initiated sleep
_IOPM_ASSERTION_TYPE_PREVENT_USER_SLEEP = "PreventUserIdleSystemSleep"

# IOPMAssertionCreateWithName return codes
_KIOReturn_SUCCESS = 0

# Module-level availability cache
_pyobjc_available: bool | None = None
_IOKit_pwr_mgt = None


def _check_pyobjc_availability() -> bool:
    """Check if PyObjC IOKit.pwr_mgt is importable. Cached after first call."""
    global _pyobjc_available, _IOKit_pwr_mgt
    if _pyobjc_available is not None:
        return _pyobjc_available
    try:
        from IOKit.pwr_mgt import (
            IOPMAssertionCreateWithName,
            IOPMAssertionRelease,
        )
        _IOKit_pwr_mgt = (IOPMAssertionCreateWithName, IOPMAssertionRelease)
        _pyobjc_available = True
        logger.debug("[PowerAssertion] PyObjC IOKit.pwr_mgt available")
    except ImportError:
        _pyobjc_available = False
        logger.debug("[PowerAssertion] PyObjC IOKit.pwr_mgt not available — will use caffeinate fallback")
    return _pyobjc_available


class PowerAssertion:
    """
    Prevents macOS from entering sleep during sprint execution.

    Uses IOPMAssertionCreateWithName (PyObjC) when available,
    falls back to caffeinate subprocess guard process.

    Thread-safe: acquire/release are protected by an internal lock.
    Idempotent: multiple release() calls are safe.
    """

    __slots__ = (
        "_reason",
        "_assertion_ids",
        "_caffeinate_proc",
        "_method",
        "_lock",
        "_released",
        "_pid",
    )

    def __init__(self, reason: str) -> None:
        self._reason = reason
        self._assertion_ids: list[int] = []
        self._caffeinate_proc: subprocess.Popen | None = None
        self._method: str = "none"
        self._lock = threading.Lock()
        self._released = False
        self._pid = os.getpid()

    @classmethod
    def acquire(cls, reason: str) -> PowerAssertion:
        """
        Acquire a power assertion to prevent macOS sleep.

        Args:
            reason: Human-readable reason (shown in `pmset -g assertions`).
                    Use sprint_id for traceability.

        Returns:
            PowerAssertion instance. Call .release() when done.
        """
        instance = cls(reason=reason)
        instance._do_acquire()
        return instance

    def _do_acquire(self) -> None:
        """Internal acquire — tries IOPMAssertion first, caffeinate fallback."""
        if sys.platform != "darwin":
            logger.debug("[PowerAssertion] Not macOS — skipping power assertion")
            self._method = "skipped_non_darwin"
            return

        # Attempt 1: PyObjC IOKit native assertion
        if _check_pyobjc_availability():
            try:
                self._acquire_iokit_assertions()
                if self._assertion_ids:
                    self._method = "iokit"
                    logger.info(
                        "[PowerAssertion] IOPMAssertion acquired (%d assertions) — "
                        "sleep prevented (pid=%d, reason=%s)",
                        len(self._assertion_ids),
                        self._pid,
                        self._reason,
                    )
                    return
            except Exception as exc:
                logger.warning("[PowerAssertion] IOKit assertion failed: %s — trying caffeinate", exc)

        # Attempt 2: caffeinate subprocess guard
        try:
            self._acquire_caffeinate()
            if self._caffeinate_proc is not None:
                self._method = "caffeinate"
                logger.info(
                    "[PowerAssertion] caffeinate guard started (pid=%d, guard_pid=%d, reason=%s)",
                    self._pid,
                    self._caffeinate_proc.pid,
                    self._reason,
                )
                return
        except Exception as exc:
            logger.warning("[PowerAssertion] caffeinate fallback also failed: %s", exc)

        # Both failed — log but don't crash
        self._method = "failed"
        logger.error(
            "[PowerAssertion] FAILED to acquire power assertion — sprint may be "
            "interrupted by macOS sleep on lid close. Install PyObjC for native support: "
            "pip install pyobjc-framework-IOKit"
        )

    def _acquire_iokit_assertions(self) -> None:
        """Create IOPMAssertion assertions for NoIdleSleep + PreventUserSleep."""
        if _IOKit_pwr_mgt is None:
            return
        create_fn, _ = _IOKit_pwr_mgt

        for assertion_type in (_IOPM_ASSERTION_TYPE_NO_IDLE_SLEEP, _IOPM_ASSERTION_TYPE_PREVENT_USER_SLEEP):
            try:
                # IOPMAssertionCreateWithName(assertion_type, level, reason) -> (status, assertion_id)
                # level=255 (kIOPMAssertionLevelOn)
                result = create_fn(assertion_type, 255, self._reason)
                # PyObjC returns tuple (kern_return, assertion_id) or just assertion_id
                if isinstance(result, tuple):
                    status, assertion_id = result
                    if status == _KIOReturn_SUCCESS:
                        self._assertion_ids.append(assertion_id)
                elif isinstance(result, int) and result > 0:
                    # Some PyObjC versions return just the assertion_id on success
                    self._assertion_ids.append(result)
            except Exception as exc:
                logger.debug("[PowerAssertion] Failed to create %s: %s", assertion_type, exc)

    def _acquire_caffeinate(self) -> None:
        """Start caffeinate subprocess as sleep prevention guard."""
        try:
            # caffeinate -dimsu -w <pid>
            # -d: prevent display sleep
            # -i: prevent system idle sleep
            # -m: prevent disk idle sleep
            # -s: prevent system sleep (AC power only)
            # -u: treat activity as user-initiated
            # -w <pid>: exit when <pid> exits
            self._caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-dimsu", "-w", str(self._pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.debug("[PowerAssertion] caffeinate subprocess failed: %s", exc)
            self._caffeinate_proc = None

    def release(self) -> None:
        """
        Release the power assertion.

        Idempotent: safe to call multiple times.
        Thread-safe: protected by internal lock.
        """
        with self._lock:
            if self._released:
                return
            self._released = True

        if self._method == "iokit":
            self._release_iokit_assertions()
        elif self._method == "caffeinate":
            self._release_caffeinate()
        elif self._method == "failed":
            return  # Nothing to release

        logger.info("[PowerAssertion] Released (method=%s, pid=%d)", self._method, self._pid)

    def _release_iokit_assertions(self) -> None:
        """Release all IOPMAssertion assertions."""
        if _IOKit_pwr_mgt is None:
            return
        _, release_fn = _IOKit_pwr_mgt

        for assertion_id in self._assertion_ids:
            try:
                release_fn(assertion_id)
            except Exception as exc:
                logger.debug("[PowerAssertion] Failed to release assertion %d: %s", assertion_id, exc)
        self._assertion_ids.clear()

    def _release_caffeinate(self) -> None:
        """Terminate caffeinate subprocess guard."""
        if self._caffeinate_proc is None:
            return
        try:
            # Send SIGTERM — caffeinate exits cleanly
            self._caffeinate_proc.terminate()
            # Wait up to 2s for graceful exit
            try:
                self._caffeinate_proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't exit
                self._caffeinate_proc.kill()
                self._caffeinate_proc.wait(timeout=1.0)
        except Exception as exc:
            logger.debug("[PowerAssertion] Failed to terminate caffeinate: %s", exc)
        finally:
            self._caffeinate_proc = None

    def __enter__(self) -> PowerAssertion:
        """Context manager entry — assertion already acquired in __init__."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit — release assertion."""
        self.release()

    @property
    def method(self) -> str:
        """Return the method used for power assertion: 'iokit', 'caffeinate', 'failed', 'skipped_non_darwin'."""
        return self._method

    @property
    def is_active(self) -> bool:
        """Return True if assertion is currently active (not released)."""
        return not self._released and self._method in ("iokit", "caffeinate")
