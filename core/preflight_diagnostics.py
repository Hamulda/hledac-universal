"""
Preflight Self-Diagnostic Module (D) — Pre-Sprint 2s Gate
============================================================

Synchronous pre-flight checks that MUST complete in <2s total.
All checks are fail-loud (sys.exit(2)) to ensure production safety.

Checks (in order):
1. Native Rust extension — no silent fallback
2. LMDB WAL round-trip — verifies LMDB write/read path
3. RLIMIT_NOFILE >= 4096 — ensures adequate file descriptor pool
4. System memory via sys_metrics — NOT Rust mach (lacks `mach` feature)

Uses function-local imports to avoid circular dependency issues
(pattern: sprint_lifecycle.py:280/293).

Fails with sys.exit(2) on any check failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from core._util import aclose

__all__ = [
    "run_preflight_diagnostics",
    "PreflightResult",
]


class PreflightResult:
    """Result of a single preflight diagnostic check."""

    __slots__ = ("name", "passed", "duration_ms", "error", "details")

    def __init__(
        self,
        name: str,
        passed: bool,
        duration_ms: float,
        error: str | None = None,
        details: str | None = None,
    ) -> None:
        self.name = name
        self.passed = passed
        self.duration_ms = duration_ms
        self.error = error
        self.details = details

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"<PreflightResult {self.name}: {status} ({self.duration_ms:.1f}ms)>"


def _check_rust_extension() -> PreflightResult:
    """Check 1: Native Rust extension — fail-loud if unavailable.

    Verifies:
    1. Module can be imported (no silent fallback)
    2. At least one critical PyO3 symbol is present (not just __version__)
    3. Optional: rust_backend probe passes (ABI compatible + capability score)

    Uses pattern from _prober.py REFERENCE_SYMBOLS for symbol verification.
    """
    start = time.perf_counter()
    try:
        # Direct import — NO silent fallback allowed
        import hledac_rust_extensions

        # Verify the module actually has PyO3 symbols (not just an empty stub)
        # Critical symbols from _prober.py _REFERENCE_SYMBOLS
        _CRITICAL_SYMBOLS = [
            "batch_ioc_extract_unified",
            "batch_xxh3_64_bytes",
            "batch_content_hash_hex",
            "parse_ip_fast",
            "bloom_check_batch",
        ]

        has_any_symbol = any(
            hasattr(hledac_rust_extensions, sym)
            for sym in _CRITICAL_SYMBOLS
        )
        if not has_any_symbol:
            raise ImportError(
                f"Rust extension module is missing all critical PyO3 symbols. "
                f"Expected at least one of: {_CRITICAL_SYMBOLS}"
            )

        # Optional: Try to verify via rust_backend probe for full compatibility check
        # This is wrapped in a separate try/except to not fail if rust_backend
        # itself has issues (preflight should be minimal)
        probe_details = ""
        try:
            from hledac.universal.core.rust_backend import rust
            from hledac.universal.core.rust_backend._prober import _probe

            probe_result = _probe()
            if probe_result.available:
                if probe_result.capability_score < 0.5:
                    raise ImportError(
                        f"Rust extension capability score too low: {probe_result.capability_score:.2f} "
                        f"(expected >= 0.50). Binary may be incomplete or stale."
                    )
                probe_details = f" probe=ok cap={probe_result.capability_score:.2f}"
        except ImportError:
            # rust_backend probe failed — still OK if we have symbols
            # (partial compatibility is acceptable for preflight)
            pass
        except Exception:
            # Any other error — skip probe check, rely on symbol check
            pass

        version = getattr(hledac_rust_extensions, "__version__", "unknown")
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rust_extension",
            passed=True,
            duration_ms=duration_ms,
            details=f"version={version}{probe_details}",
        )
    except ImportError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rust_extension",
            passed=False,
            duration_ms=duration_ms,
            error=f"Rust extension unavailable: {exc}. "
            "Run: cd rust_extensions && maturin develop  (dev)  or  "
            "cd rust_extensions && maturin build --release && uv pip install dist/*.whl  (prod)",
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rust_extension",
            passed=False,
            duration_ms=duration_ms,
            error=f"Rust extension check failed: {exc}",
        )


def _check_lmdb_wal_roundtrip() -> PreflightResult:
    """Check 2: LMDB WAL round-trip — verifies write/read integrity.

    Tests the full LMDB write path used by WAL. Critical fixes applied:
    - metasync=False for M1 8GB optimization (per NEW-C2 pattern)
    - buffers=True returns memoryview, handle with isinstance check before decode
    """
    start = time.perf_counter()
    try:
        # Function-local import to avoid circular dependency
        import lmdb

        # Use temp directory for isolated test
        with tempfile.TemporaryDirectory(prefix="hledac_preflight_lmdb_") as tmpdir:
            test_key = f"preflight_wal_test_{uuid.uuid4().hex[:8]}"
            test_value = {"timestamp": time.time(), "uuid": uuid.uuid4().hex, "check": "wal_roundtrip"}

            # Open LMDB env with WAL-friendly settings
            # FIX: metasync=False for M1 8GB (per NEW-C2 pattern from lmdb_subdb.py:536,558)
            env = lmdb.open(
                tmpdir,
                map_size=10 * 1024 * 1024,  # 10MB — minimal for test
                max_dbs=1,
                writemap=False,
                metasync=False,  # M1 8GB optimization (was True)
                readahead=False,
            )

            try:
                # Write test record (simulates WAL write)
                with env.begin(write=True) as txn:
                    import json

                    txn.put(test_key.encode("utf-8"), json.dumps(test_value).encode("utf-8"))

                # Read back and verify (simulates WAL recovery)
                # FIX: buffers=True returns memoryview, handle correctly (per NEW-C2 pattern)
                with env.begin(write=False, buffers=True) as txn:
                    data = txn.get(test_key.encode("utf-8"))
                    if data is None:
                        raise RuntimeError("WAL read returned None after write")

                    # NEW-C2 pattern: handle memoryview from buffers=True
                    if isinstance(data, bytes):
                        data_str = data.decode("utf-8")
                    elif isinstance(data, memoryview):
                        data_str = data.tobytes().decode("utf-8")
                    else:
                        data_str = str(data)

                    recovered = json.loads(data_str)
                    if recovered.get("check") != "wal_roundtrip":
                        raise RuntimeError("WAL data mismatch after round-trip")

                    if recovered.get("uuid") != test_value["uuid"]:
                        raise RuntimeError("WAL UUID mismatch")

            finally:
                env.close()

        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="lmdb_wal_roundtrip",
            passed=True,
            duration_ms=duration_ms,
            details=f"key={test_key[:20]}... verified",
        )
    except ImportError:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="lmdb_wal_roundtrip",
            passed=False,
            duration_ms=duration_ms,
            error="lmdb package not available",
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="lmdb_wal_roundtrip",
            passed=False,
            duration_ms=duration_ms,
            error=f"LMDB WAL round-trip failed: {exc}",
        )


def _check_rlimit_nofile() -> PreflightResult:
    """Check 3: RLIMIT_NOFILE >= 4096 — ensures adequate file descriptor pool."""
    start = time.perf_counter()
    try:
        # Function-local import (Unix-specific)
        import resource

        soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        MIN_REQUIRED = 4096

        if soft_limit < MIN_REQUIRED:
            duration_ms = (time.perf_counter() - start) * 1000
            return PreflightResult(
                name="rlimit_nofile",
                passed=False,
                duration_ms=duration_ms,
                error=f"RLIMIT_NOFILE soft={soft_limit} < required={MIN_REQUIRED}. "
                f"Hard limit={hard_limit}. "
                "Increase with: ulimit -n 4096  (session)  or  "
                "launchd/limits.conf (permanent)",
            )

        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rlimit_nofile",
            passed=True,
            duration_ms=duration_ms,
            details=f"soft={soft_limit} hard={hard_limit}",
        )
    except ImportError:
        # resource module not available (non-Unix system)
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rlimit_nofile",
            passed=True,  # Pass on non-Unix systems
            duration_ms=duration_ms,
            details="platform does not support resource limits",
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="rlimit_nofile",
            passed=False,
            duration_ms=duration_ms,
            error=f"RLIMIT_NOFILE check failed: {exc}",
        )


def _check_memory_sys_metrics() -> PreflightResult:
    """Check 4: System memory via sys_metrics — NOT Rust mach (lacks `mach` feature).

    Uses utils/sys_metrics.py which provides async wrappers over psutil,
    with Rust-native fallbacks for process RSS. This avoids the missing
    `mach` feature in rust_extensions.
    """
    start = time.perf_counter()
    try:
        # Function-local import to avoid circular dependency
        from hledac.universal.utils import sys_metrics

        # Sync fallback for preflight context
        mem_info = sys_metrics.system_memory_sync()

        # Validate memory snapshot is sane
        if mem_info.total_gib <= 0:
            raise RuntimeError(f"Invalid memory total: {mem_info.total_gib} GiB")

        if mem_info.percent < 0 or mem_info.percent > 100:
            raise RuntimeError(f"Invalid memory percent: {mem_info.percent}%")

        # Check for critical memory pressure (>90% used)
        if mem_info.percent > 90:
            duration_ms = (time.perf_counter() - start) * 1000
            return PreflightResult(
                name="memory_sys_metrics",
                passed=False,
                duration_ms=duration_ms,
                error=f"System memory critical: {mem_info.percent:.1f}% used "
                f"({mem_info.used_gib:.2f}/{mem_info.total_gib:.2f} GiB). "
                "Consider freeing memory before sprint.",
            )

        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="memory_sys_metrics",
            passed=True,
            duration_ms=duration_ms,
            details=f"total={mem_info.total_gib:.2f}GiB used={mem_info.used_gib:.2f}GiB "
            f"({mem_info.percent:.1f}%)",
        )
    except ImportError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="memory_sys_metrics",
            passed=False,
            duration_ms=duration_ms,
            error=f"sys_metrics unavailable: {exc}",
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return PreflightResult(
            name="memory_sys_metrics",
            passed=False,
            duration_ms=duration_ms,
            error=f"Memory check failed: {exc}",
        )


def run_preflight_diagnostics(max_duration_ms: float = 2000.0) -> list[PreflightResult]:
    """
    Run all pre-flight diagnostic checks.

    Designed to complete in <2s total (max_duration_ms).

    Args:
        max_duration_ms: Maximum allowed duration for all checks combined.

    Returns:
        List of PreflightResult objects, one per check.

    Raises:
        SystemExit: If any check fails, exits with code 2.
    """
    # Import logging here to avoid circular imports at module level
    import logging

    logger = logging.getLogger(__name__)

    start_total = time.perf_counter()

    # Run checks in order (most critical first)
    checks: list[tuple[str, callable]] = [
        ("rust_extension", _check_rust_extension),
        ("lmdb_wal_roundtrip", _check_lmdb_wal_roundtrip),
        ("rlimit_nofile", _check_rlimit_nofile),
        ("memory_sys_metrics", _check_memory_sys_metrics),
    ]

    results: list[PreflightResult] = []
    failed_checks: list[PreflightResult] = []

    for name, check_fn in checks:
        result = check_fn()
        results.append(result)

        if not result.passed:
            failed_checks.append(result)
            logger.error("[PREFLIGHT] %s: FAIL — %s", name, result.error)
        else:
            logger.debug("[PREFLIGHT] %s: OK (%s)", name, result.details or f"{result.duration_ms:.1f}ms")

    total_duration_ms = (time.perf_counter() - start_total) * 1000

    # Log summary
    passed_count = sum(1 for r in results if r.passed)
    logger.info(
        "[PREFLIGHT] Diagnostics: %d/%d passed, %.1fms total",
        passed_count,
        len(results),
        total_duration_ms,
    )

    # Check total duration budget
    if total_duration_ms > max_duration_ms:
        logger.warning(
            "[PREFLIGHT] Duration %.1fms exceeded budget %.1fms — optimize checks",
            total_duration_ms,
            max_duration_ms,
        )

    # Fail-loud: exit on any check failure
    if failed_checks:
        logger.error(
            "[PREFLIGHT] FAILED: %d check(s) failed — exiting. "
            "Fix errors above and retry.",
            len(failed_checks),
        )
        # Print detailed failure info to stderr
        for fc in failed_checks:
            print(f"[PREFLIGHT CRITICAL] {fc.name}: {fc.error}", file=sys.stderr)
        sys.exit(2)

    return results
