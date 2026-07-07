"""runtime/health.py — Unified runtime health collector (Issue #22)

Collects health and metrics from all subsystems:
  - Rust extensions (cpu/io/mixed pools, dedup bloom, URL sets, memory)
  - Python layer (DuckDB, LMDB, telemetry)
  - UMA state (M1 memory pressure)

This is the canonical health endpoint consumed by:
  - SprintScheduler telemetry → OpenTelemetry traces
  - /health HTTP endpoint (future)
  - Memory pressure alarms (critical → abort sprint)
"""

from __future__ import annotations

from typing import Any

# Rust extension health — single PyO3 call, <1ms
try:
    from hledac_rust_extensions import health_check as _rust_health_check
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    _rust_health_check = None


async def collect_runtime_health() -> dict[str, Any]:
    """
    Collect full runtime health snapshot from all subsystems.

    Returns a flat dict with the following top-level keys:

    rust_extensions : dict
        Direct output of `hledac_rust_extensions.health_check()`.
        Fields: version, health_checks_total, cpu_pool_threads,
        io_pool_threads, mixed_pool_threads, mixed_pool_threshold,
        rss_bytes, peak_rss_bytes, memory_pressure, available_memory_gib,
        metal_active_bytes, dedup_bloom_instances, dedup_bloom_items,
        dedup_bloom_capacity, dedup_bloom_capacity_pct,
        url_set_instances, url_set_items, url_mmap_instances,
        url_mmap_items, telemetry_counters, timestamp_ms.

    rust_available : bool
        True when the Rust extension wheel is installed.

    uma_state : dict
        UMA memory state derived from Rust health data:
        - rss_gib : float
        - available_gib : float
        - pressure_level : int  (0=normal, 1=elevated, 2=critical)
        - metal_gib : float
        - dedup_bloom_pct : float

    Telemetry is emitted via OpenTelemetry span events when
    ``setup_instrumentation`` has been called.
    """
    result: dict[str, Any] = {
        "rust_available": _RUST_AVAILABLE,
        "rust_extensions": {},
        "uma_state": {},
    }

    # ── Rust extensions ────────────────────────────────────────────────────
    if _RUST_AVAILABLE and _rust_health_check is not None:
        try:
            rust_dict = _rust_health_check()
            result["rust_extensions"] = rust_dict

            # Derive UMA state for convenience
            rss_bytes = rust_dict.get("rss_bytes", 0)
            avail_gib = rust_dict.get("available_memory_gib", 0.0)
            pressure = rust_dict.get("memory_pressure", 0)
            metal_bytes = rust_dict.get("metal_active_bytes", 0)
            bloom_pct = rust_dict.get("dedup_bloom_capacity_pct", 0.0)

            result["uma_state"] = {
                "rss_gib": rss_bytes / (1024**3),
                "available_gib": avail_gib,
                "pressure_level": pressure,
                "metal_gib": metal_bytes / (1024**3),
                "dedup_bloom_pct": bloom_pct,
            }
        except Exception:
            # Fail-soft: health endpoint must never raise
            result["rust_extensions"] = {}
            result["uma_state"] = {}

    # ── OpenTelemetry span event ───────────────────────────────────────────
    try:
        from runtime import get_logfire_logger

        logger = get_logfire_logger("health")
        # Emit key UMA metrics as structured log
        if result["uma_state"]:
            uma = result["uma_state"]
            logger.info(
                "runtime_health",
                rss_gib=uma["rss_gib"],
                pressure_level=uma["pressure_level"],
                metal_gib=uma["metal_gib"],
                dedup_bloom_pct=uma["dedup_bloom_pct"],
            )
    except Exception:
        # Telemetry hook is best-effort — never fail the health endpoint
        pass

    return result
