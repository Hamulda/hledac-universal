"""
transport/prewarm_pool.py — 4-slot prewarm pool for curl_cffi AsyncSession.

Sprint F265B (2026-06-10) + F265B-ext (2026-06-11) + F320-3.2 (2026-07-02).
Eliminates cold-start TLS handshake latency on the first request to a new
(host, profile) tuple. M1 8GB safe: 4 sessions ≈ 60 MB resident.

F320-3.2 changes
----------------
* Staleness guard: sessions older than _STALE_SESSION_TTL_S are evicted
  before reuse. Without this, a "warm" session whose server closed the
  TCP connection forces a full TLS re-handshake (200-500 ms) on the very
  first request after pool acquisition — defeating the purpose of prewarm.
* Probe runs directly in event loop via ``await session.head()``.
  curl_cffi releases the GIL during TLS I/O; no thread needed.
  MODERN-14: Simplified from asyncio.to_thread() + asyncio.Runner().

Design invariants
-----------------
* Always-on, opt-out via env flag HLEDAC_CURL_CFFI_PREWARM=0.
* Bounded: 4 slots, evict-on-acquire, never grows.
* M1 8GB: 4 sessions × ~15 MB each ≈ 60 MB resident.
* Staleness TTL: default 60 s. Configurable via
  HLEDAC_CURL_CFFI_PREWARM_STALE_TTL. Sessions older than this are
  considered potentially stale (server may have closed the TCP conn).
* Fail-soft: any error in prewarm (import, create, probe) is caught;
  the lazy session path is used as fallback. The fetch never fails
  because prewarm failed.
* Background probe via direct ``await`` — never blocks event loop.
  MODERN-14: Uses native async, no GIL ping-pong overhead.
* Circuit-breaker for probe hosts — skip repeatedly failing CDN endpoints.
"""


import asyncio
import contextvars
import functools
import logging
import os
import time

from hledac.universal.core.env_config import ENV  # noqa: E402
from hledac.universal.utils.asyncx import safe_create_task, parallel
from typing import Any

logger = logging.getLogger("hledac.universal.transport.prewarm_pool")

# ---------------------------------------------------------------------------
# Bounded constants (M1 8GB tuned).
# ---------------------------------------------------------------------------
# Pool size from env; opt-out via HLEDAC_CURL_CFFI_PREWARM=0
# M1 8GB recommended: HLEDAC_CURL_CFFI_POOL_SIZE=2
_POOL_SIZE: int = ENV.get_int("HLEDAC_CURL_CFFI_POOL_SIZE", default=4)
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
# F320-3.2: Session staleness threshold. A session older than this is
# considered potentially stale — the server may have closed the TCP connection,
# forcing a new TLS handshake (200-500 ms blocking cost) on the first request.
# Default: 60 s. Operators on slow connections may increase.
# Configurable via HLEDAC_CURL_CFFI_PREWARM_STALE_TTL.
_STALE_SESSION_TTL_S: float = ENV.get_float(
    "HLEDAC_CURL_CFFI_PREWARM_STALE_TTL", default=60.0
)
# Per-host circuit-breaker state: host -> (consecutive_failures, last_failure_time)
_probe_circuit_var: contextvars.ContextVar[dict[str, tuple[int, float]]] = contextvars.ContextVar(
    "_probe_circuit", default={}
)

# Lazy reference to the runtime module to avoid a circular import
# (curl_cffi_runtime imports prewarm_pool, not the other way around).
_runtime_module: Any = None
_pool_var: contextvars.ContextVar[dict[int, dict[str, Any]]] = contextvars.ContextVar(
    "_pool", default={}
)  # slot_idx -> {session, profile, warmed_at}
_next_slot_var: contextvars.ContextVar[int] = contextvars.ContextVar("_next_slot", default=0)


@functools.cache
def _get_lock() -> asyncio.Lock:
    """Lazy singleton asyncio.Lock — functools.cache serializes init under a single internal lock.

    Only one asyncio.Lock is ever created even under concurrent first-access.
    asyncio.Lock is task-local by design; storing in a contextvar would give each
    task its own lock, breaking synchronization across tasks.
    """
    return asyncio.Lock()


_stats_var: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "_stats",
    default={
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
        "stale_evictions": 0,  # F320-3.2: sessions evicted as stale
    },
)


def _resolve_enabled() -> bool:
    """Prewarm gate. Default ON; opt-out via HLEDAC_CURL_CFFI_PREWARM=0.

    Allowed values for ON: "1", "true", "yes", "on" (case-insensitive).
    Anything else (including unset) -> disabled.
    """
    return ENV.get_bool("HLEDAC_CURL_CFFI_PREWARM")


def _is_session_stale(warmed_at: float | None) -> bool:
    """Return True if the session is considered potentially stale.

    A session with no warm timestamp is always considered fresh (it was
    just created — the probe is running in the background).

    A session older than _STALE_SESSION_TTL_S is considered stale because
    the server may have closed the keepalive TCP connection, forcing a
    new TLS handshake on the next request (200-500 ms blocking cost).
    """
    if warmed_at is None:
        return False
    return (time.monotonic() - warmed_at) >= _STALE_SESSION_TTL_S


def get_stats() -> dict[str, int]:
    """Return a snapshot of prewarm telemetry. Cheap O(1)."""
    stats = _stats_var.get()
    out = dict(stats)
    out["prewarm_enabled"] = 1 if _resolve_enabled() else 0
    out["pool_size"] = len(_pool_var.get())
    out["pool_capacity"] = _POOL_SIZE
    out["stale_session_ttl_s"] = int(_STALE_SESSION_TTL_S)
    return out


def reset_stats() -> None:
    """Reset counters (tests only). Does NOT close sessions."""
    stats = _stats_var.get()
    new_stats = dict(stats)
    for k in list(new_stats.keys()):
        if k not in ("prewarm_enabled", "stale_session_ttl_s"):
            new_stats[k] = 0
    _stats_var.set(new_stats)


async def _create_session(profile: str) -> Any | None:
    """Lazy import + create AsyncSession. Fail-soft: returns None on error.

    Mirrors ``_get_or_create_session`` in curl_cffi_runtime but without
    the LRU machinery — prewarm owns its own 2-slot ring buffer.

    ISSUE-P6-001: TCP keep-alive is injected via curl_options so that
    prewarmed sessions (which hold TCP connections open) detect dead
    peers proactively and avoid holding slots in TIME_WAIT indefinitely.

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

    # ISSUE-P6-001: TCP keep-alive options — imported lazily to avoid
    # circular import. Single source of truth is _tcp_keepalive module.
    try:
        from hledac.universal.transport._tcp_keepalive import (
            TCP_KEEPALIVE_CURL_OPTIONS,
        )
        _tcp_opts = TCP_KEEPALIVE_CURL_OPTIONS
    except Exception:  # noqa: BLE001
        _tcp_opts = {}

    try:
        max_clients = ENV.get_int("HLEDAC_CURL_CFFI_MAX_CLIENTS", default=5)
        sess = AsyncSession(
            impersonate=profile,
            timeout=10.0,
            max_clients=max_clients,
            curl_options=_tcp_opts,  # ISSUE-P6-001: TCP keep-alive on prewarmed sockets
        )
        stats = _stats_var.get()
        stats["sessions_created"] += 1
        _stats_var.set(stats)
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
    probe_circuit = _probe_circuit_var.get()
    # Periodic TTL eviction: remove expired entries on every call
    stale_keys = [
        h for h, (_, last_fail) in probe_circuit.items()
        if now - last_fail >= _PROBE_FAILURE_RESET_AFTER_S * 2
    ]
    for h in stale_keys:
        probe_circuit.pop(h, None)
    # Persist evicted stale entries back to contextvar
    _probe_circuit_var.set(probe_circuit)
    for _ in range(2):  # try each host at most once per probe
        for host in _PROBE_HOSTS:
            if host in probe_circuit:
                failures, last_fail = probe_circuit[host]
                if failures >= _PROBE_FAILURE_THRESHOLD:
                    if now - last_fail >= _PROBE_FAILURE_RESET_AFTER_S:
                        # Auto-reset after cooldown
                        probe_circuit.pop(host, None)
                        # Persist the deletion back to contextvar
                        _probe_circuit_var.set(probe_circuit)
                    else:
                        stats = _stats_var.get()
                        stats["probe_circuit_skipped"] += 1
                        _stats_var.set(stats)
                        continue  # skip circuit-broken host
            yield host


async def _probe_warm(session: Any) -> bool:
    """Send a bounded HEAD to a probe host to establish TCP+TLS state.

    Uses circuit-breaker to skip repeatedly failing hosts.
    Returns True if the probe completed (any status code is success —
    we only care that the connection is now warm for re-use). Returns
    False on any error or timeout. Never raises.

    F320-3.2: Originally ran in ``asyncio.to_thread()`` to avoid blocking
    the event loop. curl_cffi wraps libcurl (C); its async methods
    can block the OS network stack during TLS handshake even when
    awaited. Thread isolation ensures the event loop stays responsive.

    MODERN-14: Simplified — session.head() is an async coroutine, just
    await it directly. The GIL is released during I/O (TLS handshake)
    via curl_cffi's internal async I/O. No need for asyncio.Runner() or
    asyncio.to_thread() wrappers.
    """
    stats = _stats_var.get()
    stats["probe_attempts"] += 1
    _stats_var.set(stats)
    if session is None:
        stats = _stats_var.get()
        stats["probe_failures"] += 1
        _stats_var.set(stats)
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

    # MODERN-14: session.head() is already an async coroutine from curl_cffi.
    # Just await it directly — curl_cffi releases the GIL during I/O.
    # No need for asyncio.Runner(), asyncio.to_thread(), or threading fallback.
    try:
        result = await session.head(probe_url, timeout=_PROBE_TIMEOUT_S)
        ok = result.status_code is not None
    except Exception:  # noqa: BLE001
        ok = False

    if ok:
        stats = _stats_var.get()
        stats["probe_success"] += 1
        _stats_var.set(stats)
        # Reset circuit on success
        probe_circuit = _probe_circuit_var.get()
        probe_circuit.pop(probe_host, None)
        _probe_circuit_var.set(probe_circuit)
    else:
        stats = _stats_var.get()
        stats["probe_failures"] += 1
        _stats_var.set(stats)
        _record_probe_failure(probe_host)
    return ok


def _record_probe_failure(host: str) -> None:
    """Record a probe failure for circuit-breaker tracking."""
    now = time.monotonic()
    probe_circuit = _probe_circuit_var.get()
    failures, _ = probe_circuit.get(host, (0, now))
    probe_circuit[host] = (failures + 1, now)
    _probe_circuit_var.set(probe_circuit)


def _pool_pop_slot(slot_idx: int) -> dict[str, Any] | None:
    """Synchronously remove an entry from the pool.

    Idempotent. Call OUTSIDE lock; use when you need to schedule
    aclose() yourself (fire-and-forget).
    """
    pool = _pool_var.get()
    result = pool.pop(slot_idx, None)
    _pool_var.set(pool)
    return result


async def _evict_slot(slot_idx: int) -> None:
    """Close the session in the given slot and remove it from the pool.

    Idempotent. CancelledError is re-raised.
    Used by close_pool() where we need to await aclose() properly.
    """
    pool = _pool_var.get()
    entry = pool.pop(slot_idx, None)
    _pool_var.set(pool)
    if entry is None:
        return
    sess = entry.get("session")
    if sess is None:
        return
    try:
        await sess.aclose()
        stats = _stats_var.get()
        stats["sessions_closed"] += 1
        _stats_var.set(stats)
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
    pool = _pool_var.get()
    old_entry = pool.pop(slot_idx, None)
    _pool_var.set(pool)
    # Fire-and-forget eviction of the old session (if any)
    if old_entry is not None:
        old_sess = old_entry.get("session")
        if old_sess is not None:
            try:
                safe_create_task(
                    old_sess.aclose(),
                    name=f"prewarm:evict:{slot_idx}",
                )
            except RuntimeError:  # noqa: BLE001
                pass
    # Create new session (async but fast — curl_cffi init)
    sess = await _create_session(profile)
    if sess is None:
        return
    pool = _pool_var.get()
    pool[slot_idx] = {
        "session": sess,
        "profile": profile,
        "warmed_at": None,
    }
    _pool_var.set(pool)
    # Background probe — never blocks the caller. The probe updates
    # ``warmed_at`` on success. If the probe fails, the session is
    # still usable (just cold) — same behaviour as the lazy path.
    async def _probe_and_mark() -> None:
        try:
            ok = await _probe_warm(sess)
            if ok:
                pool = _pool_var.get()
                if slot_idx in pool:
                    pool[slot_idx]["warmed_at"] = time.monotonic()
                    _pool_var.set(pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("prewarm_pool: probe_and_mark failed: %s", e)

    try:
        safe_create_task(_probe_and_mark(), name=f"prewarm:probe:{profile}")
    except RuntimeError:  # noqa: BLE001
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
      * (False, None, "stale") when the cached session was stale
        (F320-3.2: server may have closed keepalive conn, forcing
        TLS re-handshake — we evict and fall back to lazy path)

    Round-robin across the pool slots. On a hit, the next slot is
    re-prewarmed in the background so the pool stays warm.
    On a staleness hit, the slot is evicted and the lazy path is used.
    """
    if not _resolve_enabled():
        stats = _stats_var.get()
        stats["fallback_lazy"] += 1
        _stats_var.set(stats)
        return False, None, "prewarm_disabled"

    lock = _get_lock()
    try:
        async with lock:
            next_slot = _next_slot_var.get()
            slot_idx = next_slot
            _next_slot_var.set((next_slot + 1) % _POOL_SIZE)

            pool = _pool_var.get()
            entry = pool.get(slot_idx)
            if entry is not None and entry.get("profile") == profile:
                # Hit: a session is already in this slot for this profile.
                sess = entry.get("session")
                warmed_at = entry.get("warmed_at")
                # F320-3.2: staleness check — a "warm" session whose server
                # closed the TCP connection forces TLS re-handshake (200-500 ms
                # blocking) on the first request. Evict it proactively.
                if _is_session_stale(warmed_at):
                    stats = _stats_var.get()
                    stats["stale_evictions"] += 1
                    _stats_var.set(stats)
                    # Evict and fall back to lazy path — do NOT reuse stale session
                    pool.pop(slot_idx, None)
                    _pool_var.set(pool)
                    if sess is not None:
                        try:
                            safe_create_task(
                                sess.aclose(),
                                name=f"prewarm:evict:stale:{slot_idx}",
                            )
                        except RuntimeError:  # noqa: BLE001
                            pass
                    stats = _stats_var.get()
                    stats["fallback_lazy"] += 1
                    _stats_var.set(stats)
                    return False, None, "stale"
                if sess is not None and (not hasattr(sess, "closed") or not sess.closed):
                    stats = _stats_var.get()
                    stats["round_robin_hits"] += 1
                    _stats_var.set(stats)
                    # Re-prewarm the OTHER slot in the background. This
                    # is what keeps the pool warm: every acquire kicks
                    # off a background prewarm of the next slot.
                    other = (slot_idx + 1) % _POOL_SIZE
                    pool = _pool_var.get()
                    if other not in pool or pool[other].get("profile") != profile:
                        try:
                            safe_create_task(
                                _fill_slot(other, profile),
                                name=f"prewarm:fill:{profile}:{other}",
                            )
                        except RuntimeError:  # noqa: BLE001
                            pass
                    stats = _stats_var.get()
                    stats["slots_used"] = max(stats["slots_used"], len(_pool_var.get()))
                    _stats_var.set(stats)
                    return True, sess, profile
            # Miss: fill this slot. We do it inline (await) so the caller
            # gets a session back; the probe still runs in the background.
            await _fill_slot(slot_idx, profile)
            pool = _pool_var.get()
            entry = pool.get(slot_idx)
            if entry is None:
                stats = _stats_var.get()
                stats["fallback_lazy"] += 1
                _stats_var.set(stats)
                return False, None, "create_failed"
            sess = entry.get("session")
            if sess is None:
                stats = _stats_var.get()
                stats["fallback_lazy"] += 1
                _stats_var.set(stats)
                return False, None, "create_failed"
            stats = _stats_var.get()
            stats["slots_used"] = max(stats["slots_used"], len(_pool_var.get()))
            _stats_var.set(stats)
            return True, sess, profile
    except Exception as e:  # noqa: BLE001
        logger.debug("prewarm_pool: acquire_session outer error: %s", e)
        stats = _stats_var.get()
        stats["fallback_lazy"] += 1
        _stats_var.set(stats)
        return False, None, "lock_error"


async def fill_all_slots() -> None:
    """Fill all pool slots in parallel via asyncio.gather.

    P2-13: Replaces sequential per-slot fill with parallel fill.
    Each slot ~= 100-300ms (TCP + TLS handshake). 4 parallel ~= 1 slot latency.

    Fails softly: individual slot failures are caught and logged;
    successful slots remain usable.
    """
    if not _resolve_enabled():
        return

    lock = _get_lock()
    async with lock:
        pool = _pool_var.get()
        # Determine which slots need filling
        async def _ensure_slot(slot_idx: int) -> None:
            if slot_idx in pool:
                return  # already filled
            await _fill_slot(slot_idx, "chrome110")

        await parallel(
            [_ensure_slot(i) for i in range(_POOL_SIZE)],
            policy="log",
            ctx="prewarm_pool:fill_all_slots",
        )


async def close_pool() -> None:
    """Close all sessions in the pool. Idempotent. CancelledError re-raised."""
    lock = _get_lock()
    try:
        async with lock:
            slot_ids = list(_pool_var.get().keys())
    except Exception:
        slot_ids = list(_pool_var.get().keys())
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
        pool = _pool_var.get()
        now = time.monotonic()
        return {
            idx: {
                "profile": entry.get("profile"),
                "warmed": entry.get("warmed_at") is not None,
                "stale": _is_session_stale(entry.get("warmed_at")),
                "age_s": round(now - entry["warmed_at"], 1)
                if entry.get("warmed_at") is not None
                else None,
            }
            for idx, entry in pool.items()
        }
    except Exception:
        return {}


def clear_pool_for_tests() -> None:
    """Drop all pool state. Tests only. Does NOT close sessions — the
    caller is expected to ``await close_pool()`` first if it cares
    about session lifecycle.
    """
    _pool_var.set({})
    _next_slot_var.set(0)
    _probe_circuit_var.set({})


# Backward-compat shim for tests that access _pool / _next_slot directly.
# The internal state is now stored in ContextVars; this shim gives
# tests a mutable dict/set proxy without breaking encapsulation.
def __getattr__(name: str) -> Any:
    if name == "_pool":
        return _pool_var.get()
    if name == "_next_slot":
        return _next_slot_var.get()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



__all__ = [
    "acquire_session",
    "close_pool",
    "fill_all_slots",
    "get_stats",
    "get_pool_snapshot",
    "reset_stats",
    "clear_pool_for_tests",
]
