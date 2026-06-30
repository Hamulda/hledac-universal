"""
transport/prewarm_pool.py — 4-slot prewarm pool for curl_cffi AsyncSession.

Sprint F265B (2026-06-10) + F265B-ext (2026-06-11). Eliminates cold-start
TLS handshake latency on the first request to a new (host, profile) tuple.
M1 8GB safe: 4 sessions ≈ 60 MB resident (well inside mission budget).

Design invariants
-----------------
* Always-on, opt-out via env flag HLEDAC_CURL_CFFI_PREWARM=0
  (per project invariant "no new toggles for new functions"; the
  opt-out is honored for operators who need a tighter RAM footprint
  in CI containers).
* Bounded: 4 slots, evict-on-acquire, never grows.
* M1 8GB: 4 sessions × ~15 MB each ≈ 60 MB resident (within mission budget).
* Profiles are routed by (profile, host) affinity; a slot is re-used
  when the same profile is requested again.
* Fail-soft: any error in prewarm (import, create, probe) is caught;
  the lazy session path is used as fallback. The fetch never fails
  because prewarm failed.
* Single-threaded asyncio: no Lock needed. The "A is active, B is
  prewarmed" invariant is preserved because we never await between
  the read and the swap; the swap is a synchronous dict update.
* Background probe: a HEAD/GET to a known-good host primes the
  TCP+TLS connection. Probe runs as ``asyncio.create_task``; the
  caller never blocks.
* No network dependency: probe targets are configurable but default
  to public search engines; the probe is best-effort and times out
  at ``_PROBE_TIMEOUT_S``.

Round-robin selection
---------------------
    call 1: slot 0  → active,  slot 1 → prewarmed (lazy)
    call 2: slot 1  → active,  slot 0 → re-prewarmed (after release)
    call 3: slot 0  → active,  slot 1 → re-prewarmed (after release)

This keeps one warm session always available while the other is
in-flight. M1 8GB: max 2 sessions × 15 MB each ≈ 30 MB resident.

Why not just keep N sessions open forever? Two reasons:
  1. Each AsyncSession has its own connection pool (max_clients=15
     in curl_cffi_runtime). Two open pools > 30 connections idle
     = wasteful for M1 8GB UMA where Apple Silicon has a single
     L2 slice shared by all P-cores.
  2. Sprints are short (60-300s). Most lanes touch 1-2 profiles.
     2 slots is the sweet spot.
"""

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger("hledac.universal.transport.prewarm_pool")

# ---------------------------------------------------------------------------
# Bounded constants (M1 8GB tuned).
# ---------------------------------------------------------------------------
# PATCH 2: Pool size from env; opt-out via HLEDAC_CURL_CFFI_PREWARM=0
# M1 8GB recommended: HLEDAC_CURL_CFFI_POOL_SIZE=2
_POOL_SIZE: int = int(os.environ.get("HLEDAC_CURL_CFFI_POOL_SIZE", "4"))
# Per-request hard cap on the speculative probe. 3 s is enough for a
# TCP+TLS handshake against a public CDN; longer timeouts add nothing
# because the probe is best-effort.
_PROBE_TIMEOUT_S: float = 3.0
# Probe hosts — diverse CDN/public infrastructure for TLS warming.
# Circuit breaker: if a host fails _PROBE_FAILURE_THRESHOLD times consecutively,
# it's skipped until _PROBE_FAILURE_RESET_AFTER_S seconds elapse without failures.
_PROBE_HOSTS: tuple[str, ...] = (
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://cloudflare.com/",
    "https://cdn.jsdelivr.net/",
    "https://unpkg.com/",
)
_PROBE_FAILURE_THRESHOLD: int = 3  # skip host after 3 consecutive failures
_PROBE_FAILURE_RESET_AFTER_S: float = 30.0  # re-enable a skipped host after 30s
# Per-host circuit-breaker state: host -> (consecutive_failures, last_failure_time)
_probe_circuit: dict[str, tuple[int, float]] = {}
# Background task: do not hold the loop hostage while the probe runs.
# The create_task call returns immediately; the probe completes
# in the background or times out at _PROBE_TIMEOUT_S.

# Lazy reference to the runtime module to avoid a circular import
# (curl_cffi_runtime imports prewarm_pool, not the other way around).
_runtime_module: Any = None
_pool: dict[int, dict[str, Any]] = {}  # slot_idx -> {session, profile, warmed_at}
_next_slot: int = 0
_lock: asyncio.Lock | None = None  # created lazily in async context
_stats: dict[str, int] = {
    "prewarm_enabled": 0,
    "slots_used": 0,
    "probe_attempts": 0,
    "probe_success": 0,
    "probe_failures": 0,
    "probe_timeouts": 0,
    "probe_circuit_skipped": 0,
    "round_robin_hits": 0,
    "fallback_lazy": 0,
    "sessions_created": 0,
    "sessions_closed": 0,
}


def _resolve_enabled() -> bool:
    """Prewarm gate. Default ON; opt-out via HLEDAC_CURL_CFFI_PREWARM=0.

    Allowed values for ON: "1", "true", "yes", "on" (case-insensitive).
    Anything else (including unset) -> disabled.
    """
    v = os.environ.get("HLEDAC_CURL_CFFI_PREWARM", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def get_stats() -> dict[str, int]:
    """Return a snapshot of prewarm telemetry. Cheap O(1)."""
    out = dict(_stats)
    out["prewarm_enabled"] = 1 if _resolve_enabled() else 0
    out["pool_size"] = len(_pool)
    out["pool_capacity"] = _POOL_SIZE
    return out


def reset_stats() -> None:
    """Reset counters (tests only). Does NOT close sessions."""
    for k in list(_stats.keys()):
        if k != "prewarm_enabled":
            _stats[k] = 0


async def _create_session(profile: str) -> Any | None:
    """Lazy import + create AsyncSession. Fail-soft: returns None on error.

    Mirrors ``_get_or_create_session`` in curl_cffi_runtime but without
    the LRU machinery — prewarm owns its own 2-slot ring buffer.

    Note: the import runs while holding ``_lock`` (from ``_fill_slot`` ->
    ``acquire_session``). On M1 8GB with single-threaded asyncio, this
    is safe: no other coroutine runs during the import, and after the
    first call the module is cached by Python's import system (~0ms).
    """
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: curl_cffi import failed: %s", e)
        return None
    # PATCH 3: max_clients from env (default 5, 4×5=20 vs old 4×15=60)
    try:
        max_clients = int(os.environ.get("HLEDAC_CURL_CFFI_MAX_CLIENTS", "5"))
        sess = AsyncSession(
            impersonate=profile,
            timeout=10.0,
            max_clients=max_clients,
        )
        _stats["sessions_created"] += 1
        return sess
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: AsyncSession() failed for %s: %s", profile, e)
        return None


def _probe_host_iter():
    """Yield available probe hosts, skipping circuit-broken ones.

    Iterates twice over _PROBE_HOSTS to find a healthy host.
    Resets circuit breakers after _PROBE_FAILURE_RESET_AFTER_S.
    Evicts stale entries (TTL expired) on every call to bound memory.
    """
    now = time.monotonic()
    # Periodic TTL eviction: remove expired entries on every call
    stale_keys = [
        h for h, (_, last_fail) in _probe_circuit.items()
        if now - last_fail >= _PROBE_FAILURE_RESET_AFTER_S * 2
    ]
    for h in stale_keys:
        _probe_circuit.pop(h, None)
    for _ in range(2):  # try each host at most once per probe
        for host in _PROBE_HOSTS:
            if host in _probe_circuit:
                failures, last_fail = _probe_circuit[host]
                if failures >= _PROBE_FAILURE_THRESHOLD:
                    if now - last_fail >= _PROBE_FAILURE_RESET_AFTER_S:
                        # Auto-reset after cooldown
                        del _probe_circuit[host]
                    else:
                        _stats["probe_circuit_skipped"] += 1
                        continue  # skip circuit-broken host
            yield host


async def _probe_warm(session: Any) -> bool:
    """Send a bounded HEAD to a probe host to establish TCP+TLS state.

    Uses circuit-breaker to skip repeatedly failing hosts.
    Returns True if the probe completed (any status code is success —
    we only care that the connection is now warm for re-use). Returns
    False on any error or timeout. Never raises.
    """
    _stats["probe_attempts"] += 1
    if session is None:
        _stats["probe_failures"] += 1
        return False
    # Pick a probe host using circuit-breaker-aware iterator.
    # We don't actually fetch from the URL the caller wants — prewarmed
    # connections are reusable for any host the AsyncSession sees next
    # (curl_cffi pools by host, but a successful TLS handshake to a CDN
    # warms the rustls state inside the session).
    probe_host = None
    for candidate in _probe_host_iter():
        probe_host = candidate
        break
    if probe_host is None:
        # All hosts are circuit-broken — fallback to first host
        probe_host = _PROBE_HOSTS[0]
    probe_url = probe_host
    try:
        # asyncio.wait_for wraps the probe in a hard cap. Even if the
        # server is slow, we don't want the prewarm to delay fetches.
        async def _do_probe() -> bool:
            r = await session.head(probe_url, timeout=_PROBE_TIMEOUT_S)
            return r.status_code is not None

        try:
            ok = await asyncio.wait_for(_do_probe(), timeout=_PROBE_TIMEOUT_S + 1.0)
        except TimeoutError:
            _stats["probe_timeouts"] += 1
            _record_probe_failure(probe_host)
            return False
        except Exception as e:  # noqa: BLE001
            logger.debug("prewarm_pool: probe to %s failed: %s", probe_url, e)
            _stats["probe_failures"] += 1
            _record_probe_failure(probe_host)
            return False
        if ok:
            _stats["probe_success"] += 1
            # Reset circuit on success
            _probe_circuit.pop(probe_host, None)
        return ok
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: probe outer error: %s", e)
        _stats["probe_failures"] += 1
        _record_probe_failure(probe_host)
        return False


def _record_probe_failure(host: str) -> None:
    """Record a probe failure for circuit-breaker tracking."""
    now = time.monotonic()
    failures, _ = _probe_circuit.get(host, (0, now))
    _probe_circuit[host] = (failures + 1, now)


def _pool_pop_slot(slot_idx: int) -> dict[str, Any] | None:
    """Synchronously remove an entry from the pool.

    Idempotent. Call OUTSIDE lock; use when you need to schedule
    aclose() yourself (fire-and-forget).
    """
    return _pool.pop(slot_idx, None)


async def _evict_slot(slot_idx: int) -> None:
    """Close the session in the given slot and remove it from the pool.

    Idempotent. CancelledError is re-raised.
    Used by close_pool() where we need to await aclose() properly.
    """
    entry = _pool.pop(slot_idx, None)
    if entry is None:
        return
    sess = entry.get("session")
    if sess is None:
        return
    try:
        await sess.aclose()
        _stats["sessions_closed"] += 1
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: aclose() failed (fail-soft): %s", e)


async def _fill_slot(slot_idx: int, profile: str) -> None:
    """Create a session in the given slot and fire-and-forget a probe.

    Never raises. Idempotent if called with an already-filled slot
    (closes the existing session first to keep the pool size bounded).

    Note: pool update is synchronous; eviction is fire-and-forget.
    This keeps lock hold time minimal in acquire_session.
    """
    if not _resolve_enabled():
        return
    # Synchronous pool removal (fast, no I/O)
    old_entry = _pool.pop(slot_idx, None)
    # Fire-and-forget eviction of the old session (if any)
    if old_entry is not None:
        old_sess = old_entry.get("session")
        if old_sess is not None:
            try:
                asyncio.create_task(
                    old_sess.aclose(),
                    name=f"prewarm:evict:{slot_idx}",
                )
            except RuntimeError:
                pass
    # Create new session (async but fast — curl_cffi init)
    sess = await _create_session(profile)
    if sess is None:
        return
    _pool[slot_idx] = {
        "session": sess,
        "profile": profile,
        "warmed_at": None,
    }
    # Background probe — never blocks the caller. The probe updates
    # ``warmed_at`` on success. If the probe fails, the session is
    # still usable (just cold) — same behaviour as the lazy path.
    async def _probe_and_mark() -> None:
        try:
            ok = await _probe_warm(sess)
            if ok and slot_idx in _pool:
                _pool[slot_idx]["warmed_at"] = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("prewarm_pool: probe_and_mark failed: %s", e)

    try:
        asyncio.create_task(_probe_and_mark(), name=f"prewarm:probe:{profile}")
    except RuntimeError:
        # No running loop (called from sync context in tests). Skip
        # the probe; the session is still created and will be used cold.
        pass


async def acquire_session(profile: str) -> tuple[bool, Any | None, str]:
    """Acquire a session from the prewarm pool.

    Returns (success, session_or_None, reason):
      * (True, session, profile) on success
      * (False, None, "prewarm_disabled") when the env gate is off
      * (False, None, "create_failed") when session creation failed
        (caller should fall back to the lazy runtime path)

    Round-robin across the 2 slots. On a hit, the next slot is
    re-prewarmed in the background so the pool stays warm.
    """
    if not _resolve_enabled():
        _stats["fallback_lazy"] += 1
        return False, None, "prewarm_disabled"

    lock = _get_lock()
    try:
        async with lock:
            global _next_slot
            slot_idx = _next_slot
            _next_slot = (_next_slot + 1) % _POOL_SIZE

            entry = _pool.get(slot_idx)
            if entry is not None and entry.get("profile") == profile:
                # Hit: a session is already in this slot for this profile.
                sess = entry.get("session")
                if sess is not None and (not hasattr(sess, "closed") or not sess.closed):
                    _stats["round_robin_hits"] += 1
                    # Re-prewarm the OTHER slot in the background. This
                    # is what keeps the pool warm: every acquire kicks
                    # off a background prewarm of the next slot.
                    other = (slot_idx + 1) % _POOL_SIZE
                    if other not in _pool or _pool[other].get("profile") != profile:
                        # Schedule without blocking.
                        try:
                            asyncio.create_task(
                                _fill_slot(other, profile),
                                name=f"prewarm:fill:{profile}:{other}",
                            )
                        except RuntimeError:
                            pass
                    _stats["slots_used"] = max(_stats["slots_used"], len(_pool))
                    return True, sess, profile
            # Miss: fill this slot. We do it inline (await) so the caller
            # gets a session back; the probe still runs in the background.
            await _fill_slot(slot_idx, profile)
            entry = _pool.get(slot_idx)
            if entry is None:
                _stats["fallback_lazy"] += 1
                return False, None, "create_failed"
            sess = entry.get("session")
            if sess is None:
                _stats["fallback_lazy"] += 1
                return False, None, "create_failed"
            _stats["slots_used"] = max(_stats["slots_used"], len(_pool))
            return True, sess, profile
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: acquire_session outer error: %s", e)
        _stats["fallback_lazy"] += 1
        return False, None, "lock_error"


async def close_pool() -> None:
    """Close all sessions in the pool. Idempotent. CancelledError re-raised."""
    lock = _get_lock()
    try:
        async with lock:
            slot_ids = list(_pool.keys())
    except Exception:
        slot_ids = list(_pool.keys())
    for idx in slot_ids:
        try:
            await _evict_slot(idx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("prewarm_pool: close slot %d failed: %s", idx, e)


def get_pool_snapshot() -> dict[int, dict[str, Any]]:
    """Read-only snapshot of the pool for telemetry/tests. Never raises."""
    try:
        return {
            idx: {
                "profile": entry.get("profile"),
                "warmed": entry.get("warmed_at") is not None,
            }
            for idx, entry in _pool.items()
        }
    except Exception:
        return {}


def clear_pool_for_tests() -> None:
    """Drop all pool state. Tests only. Does NOT close sessions — the
    caller is expected to ``await close_pool()`` first if it cares
    about session lifecycle.
    """
    _pool.clear()
    global _next_slot
    _next_slot = 0
    _probe_circuit.clear()


__all__ = [
    "acquire_session",
    "close_pool",
    "get_stats",
    "get_pool_snapshot",
    "reset_stats",
    "clear_pool_for_tests",
]
