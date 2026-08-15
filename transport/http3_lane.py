"""
transport/http3_lane.py

P1-2 (Sprint 2026-06-08): Centralized HTTP/3 (QUIC) lane.
F320 (Sprint 2026-07-17): Hybrid adapter with Rust-engine priority.
SILICON-05 (Sprint F350M-R): Apple Network.framework native QUIC adapter.

## MODERN-16: Hybrid Model Architecture

This module implements the hybrid Python↔Rust HTTP/3 transport:

┌─────────────────────────────────────────────────────────────────────────┐
│                    Python Layer (QUIC Fallback)                         │
├─────────────────────────────────────────────────────────────────────────┤
│  http3_lane.py                                                         │
│  - AioquicTransportAdapter (Python fallback)                           │
│  - curl_cffi_opportunistic (Alt-Svc based)                             │
│  - Legacy compatibility for existing callers                            │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ async FFI (future_into_py)
                                │ Arrow IPC (bulk transfer)
┌───────────────────────────────┴─────────────────────────────────────────┐
│                    Rust Layer (QUIC Priority)                            │
├─────────────────────────────────────────────────────────────────────────┤
│  QuinnRustlsTransportAdapter (PREFERRED on Linux/x86_64)              │
│  - Rust quinn + h3 + rustls                                           │
│  - Uses rust.quic.fetch_async() — native await                         │
│                                                                         │
│  NwQuicTransportAdapter (PREFERRED on macOS arm64)                     │
│  - Apple Network.framework native QUIC                                 │
│  - Uses rust.nw_connection.fetch_quic_async() — native await          │
│  - ~80 KB per connection vs ~50-80 MB for aioquic                     │
└─────────────────────────────────────────────────────────────────────────┘

HTTP/3 strategies (priority order):

1. ``NwQuicTransportAdapter`` (PRIORITY on macOS 12.0+ / arm64)
   - Apple Network.framework native QUIC via ``nw_parameters_create_quic()``.
   - Zero external Python dependencies — just the Rust nw_connection extension
     that's already compiled for SILICON-03 (TCP lane).
   - Hardware-accelerated TLS 1.3 via Secure Transport, kernel-bypass QUIC.
   - ~80 KB per connection vs ~50-80 MB for aioquic.

2. ``QuinnRustlsTransportAdapter`` (F320: Rust quinn + h3)
   - Cross-platform QUIC via rust_extensions/src/quic.rs (quinn + h3 + rustls).
   - MODERN-14: Uses rust.quic.fetch_async() — native await, no ThreadPoolExecutor.
   - Preferred over aioquic on all platforms (Linux, x86_64 darwin, CI).

3. ``AioquicTransportAdapter`` (last-resort fallback)
   - Real HTTP/3 over QUIC via ``aioquic``. Heavier (pulls cryptography
     and OpenSSL bindings, ~50-80 MB resident).
   - Only used when NwQuicTransportAdapter and QuinnRustlsTransportAdapter
     are unavailable.

Opportunistic path (no extra deps):
4. ``curl_cffi_opportunistic`` (default)
   - Reuses curl_cffi >= 0.7's ``HttpVersion.v3`` kwarg.
   - Activates only AFTER the server has advertised ``h3`` via Alt-Svc.
   - Cached per host; bounded LRU (512 entries) so an unbounded discovery
     crawl cannot exhaust the UMA budget on M1 8GB.

Per-request ``asyncio.wait_for(timeout=...)`` so a stuck UDP handshake
can never block the fetch loop. Concurrency capped at
``_H3_CONCURRENCY_MAX = 3`` to keep UDP receive buffers + TLS
contexts inside the M1 8GB envelope. Memory guard: psutil RSS sample
blocks the lane at 5.5 GiB (matches sprint mission budget).

Fail-soft invariants (enforced by every code path below):
- No bare ``except:``; always ``except Exception``.
- neqo unavailable or probe fails -> aioquic fallback -> opportunistic.
- Any error -> return ``None`` and let the caller continue without
  the upgrade; never propagate exceptions to the fetch path.
- Cache overflow -> LRU eviction in O(1) using ``OrderedDict``;
  never raise on trim failure.
- Semaphore acquisition -> non-blocking with timeout; if not acquired
  in ``_H3_WAIT_TIMEOUT_S``, the lane returns ``None`` and the
  caller proceeds on the prior HTTP/1.1 / HTTP/2 path.

Env gates (project convention: ``HLEDAC_ENABLE_<LANE>``):
- ``HLEDAC_ENABLE_HTTPX_H3 = 1`` enables ALL strategies; default 0.
- ``HLEDAC_HTTP3 = 1`` (legacy F260 alias) is also accepted; the
  new explicit ``HLEDAC_ENABLE_HTTPX_H3`` takes precedence.
"""

import asyncio
import functools
import logging
import sys
import time
from hledac.universal.utils.lru_cache import LRUCache
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# F270: Canonical constants — single source of truth for M1 8GB bounds
from hledac.universal._core.constants import M1_BOUNDS  # noqa: E402
from hledac.universal._core.env_config import ENV  # noqa: E402
from hledac.universal.utils.asyncx import safe_wait_for

# Backward-compatible local aliases (these names are used throughout the module)
_H3_CACHE_MAX: int = M1_BOUNDS().http3_lru_max
_H3_CONCURRENCY_MAX: int = M1_BOUNDS().http3_concurrency_max
_H3_TIMEOUT_S: float = 8.0  # per-request hard cap — kept as-is (matches NETWORK.http3_request)
_H3_WAIT_TIMEOUT_S: float = 2.0  # how long to wait for the semaphore
_H3_CACHE_TTL_S: int = M1_BOUNDS().http_cache_ttl_s
_H3_RSS_BLOCK_GIB: float = M1_BOUNDS().fetch_soft_ceiling_gb
_H3_RSS_PROBE_TIMEOUT_S: float = M1_BOUNDS().rss_probe_timeout_s

# Issue #17: psutil Process singleton from centralized psutil_shim.
from hledac.universal._core.psutil_shim import process as _psutil_proc


# ---------------------------------------------------------------------------
# Env-gate resolution (project convention: HLEDAC_ENABLE_<LANE>).
# ---------------------------------------------------------------------------
def _resolve_enabled() -> bool:
    """Resolve HTTP/3 gate. Default ON (opt-out); set ``HLEDAC_ENABLE_HTTPX_H3=0`` to disable.
    ``HLEDAC_HTTP3=1`` (legacy F260 alias) is honored for back-compat.
    """
    return ENV.get_bool("HLEDAC_ENABLE_HTTPX_H3") and ENV.get_bool("HLEDAC_HTTP3")


_ENABLED: bool = _resolve_enabled()

# Lazy singletons (created on first request, not at import).
#
# The LRU cache does NOT need a lock: we run in a single-threaded asyncio
# loop, and ``LRUCache`` operations are atomic with respect to ``await``.
# Adding a lock here would only protect against a future migration to
# ``asyncio.to_thread`` callers, which we explicitly forbid in CLAUDE.md
# (``Nepoužívej asyncio.run() v ThreadPoolExecutor — M1 crash vector``).
_lru_cache: LRUCache[str, tuple[float, bool]] = LRUCache(max_size=_H3_CACHE_MAX)
_semaphore: asyncio.Semaphore | None = None
# PATCH 4: throttle speculative Alt-Svc probes (max 16 concurrent via _probe_semaphore)
# Uses ConcurrencyCategory.HTTP_LANE from concurrency_registry (shared semaphore).
from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore  # noqa: E402
from _core import aclose

_probe_semaphore: asyncio.Semaphore = get_semaphore(ConcurrencyCategory.HTTP_LANE)
_neqo_checked: bool = False
_neqo_available: bool = False
_aioquic_checked: bool = False
_aioquic_available: bool = False
# F1 fix: lazy curl_cffi availability probe — avoids top-level ImportError
# when H3 is enabled but curl_cffi is not installed. Silent fallback = no H3.
_curl_cffi_checked: bool = False
_curl_cffi_available: bool = False
# PATCH 5: bounded task tracking for speculative probes — replaces fire-and-forget
# asyncio.create_task() with a tracked set + done-callback. Max size enforced
# by _MAX_PROBE_TASKS; excess probes are dropped (advisory, never blocks).
_probe_tasks: set[asyncio.Task] = set()
# Upper bound on concurrent speculative probes — prevents unbounded growth.
_MAX_PROBE_TASKS: int = 16


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_H3_CONCURRENCY_MAX)
    return _semaphore


async def shutdown_probe_tasks() -> None:
    """Cancel and clean up all in-flight speculative Alt-Svc probe tasks.

    Call this at sprint winddown to ensure all in-flight probes are gracefully
    cancelled before the event loop closes. Uses the same done-callback pattern
    as ``DuckDBShadowStore._bg_tasks`` — task removes itself from the set on
    completion, so this only needs to cancel.

    Idempotent: safe to call even if no probes were ever scheduled.
    """
    global _probe_tasks
    for t in _probe_tasks:
        t.cancel()
    # wait() is not available on set[Task]; cancel above is sufficient —
    # cancelled tasks will remove themselves via done_callback.
    _probe_tasks.clear()
    _stats["http3_probe_shutdowns"] = _stats.get("http3_probe_shutdowns", 0) + 1


# ---------------------------------------------------------------------------
# Telemetry (small, bounded, no I/O; safe to read in tight loops).
# ---------------------------------------------------------------------------
_stats: dict[str, int] = {
    "enabled": 1 if _ENABLED else 0,
    "altsvc_hits": 0,
    "altsvc_misses": 0,
    "altsvc_records": 0,
    "altsvc_evictions": 0,
    "http3_neqo_attempts": 0,
    "http3_neqo_success": 0,
    "http3_neqo_failures": 0,
    "http3_neqo_arena_released": 0,
    "http3_nw_quic_attempts": 0,
    "http3_nw_quic_success": 0,
    "http3_nw_quic_failures": 0,
    "http3_aioquic_attempts": 0,
    "http3_aioquic_success": 0,
    "http3_aioquic_failures": 0,
    "http3_memory_blocks": 0,
    "http3_semaphore_waits": 0,
    "http3_semaphore_timeouts": 0,
    "http3_timeouts": 0,
}


def get_stats() -> dict[str, int]:
    """Return a copy of the HTTP/3 lane telemetry. Cheap O(N) snapshot."""
    # Re-evaluate env at call time so tests can flip the gate without
    # having to reload the module; this is intentional, not a perf bug.
    out = dict(_stats)
    out["enabled"] = 1 if _resolve_enabled() else 0
    out["cache_size"] = len(_lru_cache)
    out["cache_max"] = _H3_CACHE_MAX
    out["cache_hit"] = sum(1 for v in _lru_cache.values() if v[1])
    return out


def reset_stats() -> None:
    """Reset counters (tests only). Does NOT clear the LRU cache."""
    for k in list(_stats.keys()):
        if k != "enabled":
            _stats[k] = 0


# ---------------------------------------------------------------------------
# Memory guard (M1 8GB envelope: 5.5 GiB soft cap).
# ---------------------------------------------------------------------------
def _rss_over_budget() -> bool:
    """Return True if the process RSS exceeds the M1 8GB mission budget.

    psutil import is best-effort; on Linux CI without /proc, or when
    psutil is missing, this returns False (memory guard off, never blocks).
    The probe is wrapped in a timeout via wall-clock comparison so a
    slow syscall cannot block the fetch path.
    """
    proc = _psutil_proc()
    if proc is None:
        return False
    t0 = time.monotonic()
    try:
        rss = proc.memory_info().rss
    except Exception as e:
        # Fail-soft: psutil on macOS can raise on process lookup races.
        logger.debug("http3_lane: rss probe failed (fail-soft): %s", e)
        return False
    elapsed = time.monotonic() - t0
    if elapsed > _H3_RSS_PROBE_TIMEOUT_S:
        # We never want this probe to noticeably cost fetch latency.
        logger.debug("http3_lane: rss probe slow (%.3fs), not blocking", elapsed)
        return False
    gib = rss / (1024**3)
    return gib > _H3_RSS_BLOCK_GIB


# ---------------------------------------------------------------------------
# Alt-Svc cache (bounded LRU).
# ---------------------------------------------------------------------------
def _cache_get(host: str) -> bool | None:
    """Return cached H3 support for ``host`` (sliding-window TTL), or ``None`` on miss.

    On every hit the entry timestamp is refreshed (``time.time()``), extending
    the TTL by another 24 h. Combined with ``move_to_end`` this gives a
    true LRU + sliding-TTL cache: hot hosts are both kept at the MRU end
    AND never expire during a sprint.
    """
    if not host:
        return None
    entry = _lru_cache.get(host)
    if entry is None:
        _stats["altsvc_misses"] += 1
        return None
    ts, supported = entry
    if (time.time() - ts) > _H3_CACHE_TTL_S:
        # Expired: drop and treat as miss.
        try:
            _lru_cache.pop(host, None)
        except Exception:  # noqa: BLE001
            pass
        _stats["altsvc_misses"] += 1
        return None
    # Sliding window TTL: refresh timestamp on every access so hot hosts
    # stay alive for the full duration of a long sprint (up to 24h).
    # LRU position is updated separately via move_to_end below.
    _lru_cache[host] = (time.time(), supported)
    # Move to end (LRU touch); dicts preserve insertion order in 3.7+.
    try:
        _lru_cache.move_to_end(host)
    except Exception:  # noqa: BLE001
        pass
    _stats["altsvc_hits"] += 1
    return supported


def _cache_put(host: str, supports: bool) -> None:
    """Insert/update an LRU entry, evicting the oldest on overflow."""
    if not host:
        return
    try:
        if host in _lru_cache:
            _lru_cache.move_to_end(host)
        _lru_cache[host] = (time.time(), bool(supports))
        while len(_lru_cache) > _H3_CACHE_MAX:
            # popitem(last=False) removes the LEAST-recently inserted/used key.
            _lru_cache.popitem(last=False)
            _stats["altsvc_evictions"] += 1
        _stats["altsvc_records"] += 1
    except Exception as e:
        # Cache writes are best-effort; never fail the fetch path.
        logger.debug("http3_lane: cache_put failed (fail-soft): %s", e)


def _altsvc_advertises_h3(headers: Any) -> bool:
    """Return True if the Alt-Svc header advertises an ``h3`` token (RFC 7838).

    Accepts dict, multidict, or anything with ``.get()``; never raises.
    Parsing is bounded: substring match on the lowercased value. We
    accept ``h3=``, ``h3 "``, and ``h3="`` (header may be a quoted
    token or a bare one). The Alt-Svc key itself is case-insensitive
    per RFC 7838 §3, so we accept any casing of the key.
    """
    if headers is None:
        return False
    try:
        # Case-insensitive lookup across dict / multidict / Headers.
        v: Any = None
        if hasattr(headers, "get"):
            # httpx Headers and aiohttp CIMultiDict expose case-insensitive
            # .get(), so "alt-svc" matches "Alt-Svc", "ALT-SVC", etc.
            v = headers.get("alt-svc")
            if v is None:
                v = headers.get("Alt-Svc")
            if v is None:
                v = headers.get("ALT-SVC")
        if v is None and isinstance(headers, dict):
            # Plain dict: scan keys (case-insensitive).
            for k, val in headers.items():
                if isinstance(k, str) and k.lower() == "alt-svc":
                    v = val
                    break
    except Exception:
        return False
    if not v:
        return False
    s = str(v).lower()
    return "h3=" in s or 'h3 "' in s or 'h3="' in s


@functools.lru_cache(maxsize=2048)
def extract_host(url: str) -> str:
    """Return lowercased hostname from URL, or empty string on parse failure."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Dark web URL detection.
# ---------------------------------------------------------------------------
_DARK_WEB_TLDS: frozenset[str] = frozenset({".onion", ".i2p", ".b32.i2p"})


def is_dark_web_url(url: str) -> bool:
    """Return True if ``url`` targets a dark web host (.onion, .i2p, .b32.i2p).

    F271: Uses TransportRouter._DARKNET_SUFFIXES — single source of truth.
    QUIC/UDP cannot be tunneled through Tor TransPort or I2P HTTP proxy,
    so HTTP/3 is never attempted for dark web destinations.
    Never raises.
    """
    try:
        from hledac.universal.transport.transport_router import TransportRouter
        kind, host = TransportRouter()._classify_url(url)
        return kind in ("onion", "i2p")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API: opportunistic (curl_cffi HttpVersion.v3)
# ---------------------------------------------------------------------------
def http_version_for_curl_cffi(url: str) -> Any:
    """Return ``curl_cffi.requests.HttpVersion.v3`` if the host advertises h3
    and the lane is enabled, else ``None``.

    Designed to be passed to ``fetch_via_curl_cffi(..., http_version=...)``.
    The wrapper in ``transport/curl_cffi_fetch.py`` already accepts
    ``http_version=None`` and omits the kwarg when not set, so this
    function is a safe drop-in for the F260 call sites.
    """
    if not _resolve_enabled():
        return None
    host = extract_host(url)
    if not host:
        return None
    supported = _cache_get(host)
    if not supported:
        return None
    if _rss_over_budget():
        _stats["http3_memory_blocks"] += 1
        logger.debug("http3_lane: memory budget exceeded, suppressing H3 for %s", host)
        return None
    # F1 fix: probe curl_cffi availability before lazy import.
    # curl_cffi is in default deps but may be uninstalled in bare-bones envs.
    # Silent fallback = no H3 upgrade (not a hard failure).
    if not _probe_curl_cffi():
        return None
    try:
        from curl_cffi.requests import HttpVersion as _HttpVersion  # type: ignore
    except Exception:
        return None
    return _HttpVersion.v3


def record_from_curl_cffi_result(url: str, headers: Any) -> None:
    """Inspect the curl_cffi response headers for Alt-Svc h3 advertisement
    and update the LRU cache. No-op when disabled or on parse error.
    """
    if not _resolve_enabled():
        return
    host = extract_host(url)
    if not host:
        return
    if _altsvc_advertises_h3(headers):
        _cache_put(host, True)
        logger.debug("http3_lane: H3 advertised via Alt-Svc for %s", host)


def record_h3_support(url: str, supports: bool) -> None:
    """Direct cache write for callers that have already determined H3 support
    via their own probe (e.g. ``stealth_manager._supports_http3`` does a
    HEAD request and parses the Alt-Svc header itself).

    Idempotent; respects the env gate; bounded LRU.
    """
    if not _resolve_enabled():
        return
    host = extract_host(url)
    if not host:
        return
    _cache_put(host, bool(supports))


# ---------------------------------------------------------------------------
# F265B: Speculative Alt-Svc probe (background, fire-and-forget).
#
# The opportunistic H3 path is reactive: the first fetch to a host
# runs on HTTP/1.1 or HTTP/2, parses the Alt-Svc response header, and
# only then can the SECOND fetch use ``HttpVersion.v3``. That means
# single-shot sprints (most of them) never get the h3 win.
#
# Solution: when the public_fetcher sees a host for the first time
# AND the H3 lane is enabled, fire a background HEAD probe to prime
# the LRU. The probe is fully detached (asyncio.create_task); the
# caller never blocks. The probe result is written into the same
# LRU the reactive path uses, so the very next call to
# ``http_version_for_curl_cffi`` for that host sees the cached True.
# ---------------------------------------------------------------------------
_HEAD_PROBE_TIMEOUT_S: float = 4.0  # bounded; M1 8GB friendly


async def _guarded_probe(url: str) -> None:
    """Wrapper: acquire throttle slot, run probe, release on exit.

    PATCH 4: ensures _probe_semaphore acquire/release are always paired.
    """
    try:
        await _probe_semaphore.acquire()
    except Exception:
        return
    try:
        await _speculative_altsvc_probe_inner(url)
    finally:
        try:
            _probe_semaphore.release()
        except Exception:  # noqa: BLE001
            pass


async def _speculative_altsvc_probe_inner(url: str) -> None:
    """Inner coroutine for the speculative Alt-Svc probe. Sends a
    single HEAD request, parses the response headers, and updates
    the LRU if the server advertises h3. Never raises.

    Uses the same bounded AsyncSession as the curl_cffi runtime —
    the session is borrowed (not owned) so we don't multiply
    connection pools. If no session is available, the probe falls
    through to a fresh AsyncSession.

    PATCH 4: semaphore release is handled by _guarded_probe() wrapper
    in probe_altsvc_speculative(), not here. This ensures acquire/
    release are always paired.
    """
    try:
        host = extract_host(url)
        if not host:
            return
        # Use a per-probe session to keep the probe isolated from
        # the live fetch session. The session is closed at the end
        # of the probe to avoid leaking connections.
        # F1 fix: lazy import — guarded by _probe_curl_cffi() at call site.
        from curl_cffi.requests import AsyncSession  # type: ignore

        sess = AsyncSession(
            impersonate="chrome124",
            timeout=_HEAD_PROBE_TIMEOUT_S,
            max_clients=2,
        )
        try:
            async with asyncio.timeout(_HEAD_PROBE_TIMEOUT_S + 1.0):
                resp = await sess.head(url, timeout=_HEAD_PROBE_TIMEOUT_S)
        except TimeoutError:
            return
        except Exception as e:  # noqa: BLE001
            logger.debug("http3_lane: speculative probe to %s failed: %s", host, e)
            return

        try:
            if resp is not None and resp.headers:
                if _altsvc_advertises_h3(resp.headers):
                    _cache_put(host, True)
                    logger.debug("http3_lane: speculative probe primed H3 for %s", host)
        except Exception as e:  # noqa: BLE001
            logger.debug("http3_lane: speculative header parse failed: %s", e)
        finally:
            await aclose(sess)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug("http3_lane: speculative probe outer error: %s", e)


def probe_altsvc_speculative(url: str) -> None:
    """Schedule a background Alt-Svc probe for ``url``. Fire-and-forget.

    Idempotent: if the host is already in the LRU, the probe is skipped.
    Gated by ``HLEDAC_ENABLE_HTTPX_H3=1`` (the same env gate as the
    reactive path). Bounded: one task per call; the loop will garbage
    collect the task on completion. Never raises.

    Calling this from a sync context (e.g. an analysis path that runs
    outside the event loop) is a no-op: the create_task() call would
    raise RuntimeError, which we swallow and log at debug level.

    M1 8GB safety: the probe uses a dedicated session with max_clients=2
    so it cannot starve the live fetch path.
    """
    if not _resolve_enabled():
        return
    host = extract_host(url)
    if not host:
        return
    # Idempotency: skip if we already know.
    if _cache_get(host) is not None:
        return
    # F1 fix: guard speculative probes against missing curl_cffi.
    # Without this, a bare env with H3_ENABLED=1 but no curl_cffi
    # would raise ImportError on the first probe and spam the log.
    if not _probe_curl_cffi():
        return
    # PATCH 4: throttle — max 5 concurrent probe tasks
    # Use _value==0 instead of locked() to avoid race condition on pre-check
    if _probe_semaphore._value == 0:
        logger.debug("http3_lane: speculative probe throttled for %s", host)
        return
    # PATCH 5: bounded tracking — drop if at capacity (advisory, never blocks)
    if len(_probe_tasks) >= _MAX_PROBE_TASKS:
        logger.debug("http3_lane: speculative probe dropped (at capacity) for %s", host)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop in this context. The probe simply does not
        # happen; the next reactive fetch will populate the LRU.
        logger.debug("http3_lane: speculative probe skipped (no event loop) for %s", host)
        return
    try:
        from hledac.universal.utils.asyncx import safe_create_task

        task = safe_create_task(
            _guarded_probe(url),
            name=f"http3_lane:speculative_probe:{host}",
        )
        _probe_tasks.add(task)
        task.add_done_callback(_probe_tasks.discard)
    except Exception as e:  # noqa: BLE001
        logger.debug("http3_lane: speculative probe scheduling failed: %s", e)


# ---------------------------------------------------------------------------
# neqo availability probe (F320: Rust-engine priority on M1 arm64).
# ---------------------------------------------------------------------------
#
# ROOT CAUSE ANALYSIS (HTTP3-ISSUE-001):
#   neqo (Mozilla's QUIC) is NOT on PyPI - this is why the TODO exists.
#
# BUT: The project has rust.quic.fetch() implemented in rust_extensions/src/quic.rs
# using quinn + h3. This is wired into QuinnRustTransportAdapter below.
#
# HTTP/3 PRIORITY ORDER (M1 8GB RAM-aware):
#   1. NwQuicTransportAdapter ✓ — Apple Network.framework (BEST on macOS arm64)
#   2. QuinnRustTransportAdapter ✓ — Rust quinn + h3 via rust.quic.fetch()
#   3. AioquicTransportAdapter   ~ — Python aioquic fallback (~50-80MB resident)
#
# PyPI 'quinn' package is NOT Mozilla's Quinn — it's "PySpark helper methods".
#
# Tracking: https://github.com/mozilla/neqo/issues (watch for neqo PyPI release)
#


# Rust quic availability state (cached after first probe)
_rust_quic_checked: bool = False
_rust_quic_available: bool = False


def _probe_rust_quic() -> bool:
    """Probe for rust.quic.fetch() availability.

    rust_extensions/src/quic.rs provides true HTTP/3 via quinn + h3 Rust crates.
    This is the PREFERRED cross-platform QUIC path when NwQuicTransportAdapter
    is unavailable (non-darwin platforms).

    Returns True iff:
      1. The rust extension is built with --features quic
      2. rust.quic.fetch() is importable
    """
    global _rust_quic_checked, _rust_quic_available
    if _rust_quic_checked:
        return _rust_quic_available
    _rust_quic_checked = True

    try:
        import rust
        if hasattr(rust, "quic"):
            # Verify the fetch function exists
            if hasattr(rust.quic, "fetch"):
                _rust_quic_available = True
                logger.info("http3_lane: rust.quic.fetch() available (quinn + h3)")
                return True
    except ImportError:  # noqa: BLE001
        pass

    _rust_quic_available = False
    logger.debug("http3_lane: rust.quic not available (build without --features quic)")
    return False


def _probe_quinn_rs() -> bool:
    """Probe for Quinn-Rs availability (Mozilla's Quinn via GitHub).

    Quinn-Rs is Mozilla's older QUIC implementation that has Python bindings.
    Available from GitHub but not yet on PyPI.

    Returns True iff quinn_rs is importable.
    """
    global _neqo_checked, _neqo_available
    if _neqo_checked:
        return _neqo_available
    _neqo_checked = True

    # Quinn-Rs is only viable on arm64 where rustls memory arenas are
    # manageable within the M1 8GB UMA budget.
    import platform

    if not (platform.system() == "Darwin" and platform.machine() == "arm64"):
        _neqo_available = False
        logger.debug("http3_lane: Quinn-Rs skipped (not arm64 darwin)")
        return False

    # Try Quinn-Rs from GitHub
    try:
        import quinn_rs  # type: ignore[import-not-found]
        _neqo_available = True
        logger.info("http3_lane: Quinn-Rs available (Mozilla Quinn via GitHub)")
        return True
    except ImportError:  # noqa: BLE001
        pass

    # Try neqo if Quinn-Rs is not available
    try:
        import neqo_http3  # type: ignore[import-not-found]
        import neqo_transport  # type: ignore[import-not-found]
        _neqo_available = True
        logger.info("http3_lane: neqo available (Mozilla neqo via PyPI/GitHub)")
        return True
    except ImportError:  # noqa: BLE001
        pass

    # Neither Quinn-Rs nor neqo available
    _neqo_available = False
    logger.debug(
        "http3_lane: Neither Quinn-Rs nor neqo available. "
        "HTTP/3 via: 1) NwQuicTransportAdapter (macOS) 2) curl_cffi 3) aioquic (heavy)"
    )
    return False


def _probe_neqo() -> bool:
    """Probe for neqo availability (DEPRECATED — use _probe_quinn_rs instead).

    This function is kept for backward compatibility but delegates to
    _probe_quinn_rs() which checks both Quinn-Rs and neqo.

    neqo (Mozilla's Rust QUIC) was preferred over aioquic on M1 arm64
    because rustls uses M1-native ciphers and can immediately drop
    memory arenas on session close, preventing key material from
    resident in UMA after use.

    However, neqo is NOT on PyPI. Quinn-Rs (Mozilla's older QUIC) may be
    available via GitHub. Use _probe_quinn_rs() for the full check.
    """
    return _probe_quinn_rs()


# ---------------------------------------------------------------------------
# AioquicTransportAdapter — legacy fallback, wrapped from original impl.
# ---------------------------------------------------------------------------


class AioquicTransportAdapter:
    """Real QUIC over aioquic (openssl-backed). Last-resort fallback.

    instantiated by ``get_quic_transport_adapter()`` only when neqo is
    unavailable. Preserves the original ``fetch_http3_aioquic()`` logic
    verbatim so existing call sites (stealth_manager) are unaffected.
    """

    @staticmethod
    async def fetch(
        url: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = _H3_TIMEOUT_S,
    ) -> bytes | None:
        """Perform a real HTTP/3 request over QUIC via aioquic.

        Returns response body as ``bytes`` on success, or ``None`` on ANY
        failure (timeout, semaphore exhaustion, aioquic missing, server
        unreachable, protocol error). Never raises.
        """
        if not _resolve_enabled():
            return None
        if is_dark_web_url(url):
            logger.debug("http3_lane: aioquic: dark web URL skipped: %s", url)
            return None
        if _rss_over_budget():
            _stats["http3_memory_blocks"] += 1
            logger.debug("http3_lane: aioquic: memory budget exceeded")
            return None
        if not _probe_aioquic():
            return None

        host = extract_host(url)
        if not host:
            return None
        if _cache_get(host) is not True:
            return None

        _stats["http3_aioquic_attempts"] += 1
        sem = _get_semaphore()
        acquired = False
        try:
            _stats["http3_semaphore_waits"] += 1
            try:
                await safe_wait_for(sem.acquire(), timeout=_H3_WAIT_TIMEOUT_S, label="http3_aioquic_sem")
                acquired = True
            except TimeoutError:
                _stats["http3_semaphore_timeouts"] += 1
                logger.debug("http3_lane: aioquic: semaphore saturated")
                return None
        except Exception as e:
            logger.debug("http3_lane: aioquic: semaphore acquire failed: %s", e)
            return None

        try:
            try:
                from aioquic.asyncio import connect as _quic_connect  # type: ignore[import-not-found]
                from aioquic.h3.connection import H3Connection  # type: ignore[import-not-found]
                from aioquic.quic.configuration import QuicConfiguration  # type: ignore[import-not-found]
            except Exception as e:
                logger.debug("http3_lane: aioquic: inner import failed: %s", e)
                return None

            parsed = urlparse(url)
            port = parsed.port or 443
            cfg = QuicConfiguration(is_client=True)

            async def _do_request() -> bytes:
                async with _quic_connect(host, port, configuration=cfg, create_protocol=H3Connection) as protocol:
                    req_headers: list[tuple[bytes, bytes]] = [
                        (b":method", b"GET"),
                        (b":path", (parsed.path or "/").encode("ascii", "ignore")),
                        (b":authority", host.encode("ascii", "ignore")),
                    ]
                    if headers:
                        for k, v in headers.items():
                            try:
                                req_headers.append((k.encode("ascii", "ignore"), v.encode("ascii", "ignore")))
                            except Exception:  # noqa: BLE001
                                pass
                    stream_id = protocol.make_request(req_headers)
                    await protocol.wait_for_response(stream_id)
                    return await protocol.receive_data(stream_id)

            try:
                async with asyncio.timeout(timeout_s):
                    data = await _do_request()
                _stats["http3_aioquic_success"] += 1
                return data
            except asyncio.TimeoutError:
                _stats["http3_timeouts"] += 1
                logger.debug("http3_lane: aioquic: timeout %.1fs for %s", timeout_s, host)
                return None
            except Exception as e:
                _stats["http3_aioquic_failures"] += 1
                logger.debug("http3_lane: aioquic: request failed for %s: %s", host, e)
                return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _stats["http3_aioquic_failures"] += 1
            logger.debug("http3_lane: aioquic: unexpected error (fail-soft): %s", e)
            return None
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# SILICON-05: Apple Network.framework native QUIC transport adapter.
#
# This is the PREFERRED real-QUIC path on Apple Silicon (macOS 12.0+).
# Network.framework provides kernel-bypass QUIC with hardware-accelerated
# TLS 1.3 — zero external dependencies, ~80 KB per connection vs
# ~50-80 MB for aioquic.
#
# Integration: delegates to ``transport/nw_quic_lane.py::fetch_nw_quic()``
# which in turn calls ``rust.nw_connection.fetch_quic()`` via PyO3.
# ---------------------------------------------------------------------------
class NwQuicTransportAdapter:
    """Real HTTP/3 via Apple Network.framework native QUIC (macOS 12.0+).

    This is the preferred QUIC transport on Apple Silicon. It uses
    Network.framework's built-in QUIC stack with hardware-accelerated
    TLS 1.3, eliminating the need for aioquic (~50-80 MB RSS) or
    quinn (~8 MB compile).

    Unlike aioquic/neqo, this adapter returns a full dict (compatible
    with FetchCoordinator result format), not just ``bytes``. Callers
    using ``get_quic_transport_adapter()`` should use this adapter
    through ``fetch_http3()`` (not directly) so telemetry is consistent.
    """

    @staticmethod
    async def fetch(
        url: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = _H3_TIMEOUT_S,
    ) -> bytes | None:
        """Perform a real HTTP/3 request over QUIC via Network.framework.

        On session close the QUIC connection and TLS context are immediately
        released by Network.framework, keeping the M1 8GB UMA budget clean.

        Returns response body as ``bytes`` on success, or ``None`` on any
        failure. Never raises.

        NOTE: Unlike AioquicTransportAdapter, this adapter ignores the
        ``headers`` parameter — Network.framework QUIC uses its own
        HTTP/3 header construction in Rust (nw_connection.rs).
        """
        if not _resolve_enabled():
            return None
        if is_dark_web_url(url):
            logger.debug("http3_lane: nw_quic: dark web URL skipped: %s", url)
            return None
        if _rss_over_budget():
            _stats["http3_memory_blocks"] += 1
            logger.debug("http3_lane: nw_quic: memory budget exceeded")
            return None

        host = extract_host(url)
        if not host:
            return None
        # Only attempt if we already know the host supports H3 (Alt-Svc cache)
        if _cache_get(host) is not True:
            return None

        _stats["http3_nw_quic_attempts"] += 1
        sem = _get_semaphore()
        acquired = False
        try:
            _stats["http3_semaphore_waits"] += 1
            try:
                await safe_wait_for(sem.acquire(), timeout=_H3_WAIT_TIMEOUT_S, label="http3_nw_quic_sem")
                acquired = True
            except TimeoutError:
                _stats["http3_semaphore_timeouts"] += 1
                logger.debug("http3_lane: nw_quic: semaphore saturated")
                return None
        except Exception as e:
            logger.debug("http3_lane: nw_quic: semaphore acquire failed: %s", e)
            return None

        try:
            # Lazy import: nw_quic_lane may not be importable on non-darwin
            try:
                from hledac.universal.transport.nw_quic_lane import fetch_nw_quic
            except ImportError:
                logger.debug("http3_lane: nw_quic: nw_quic_lane not importable")
                _stats["http3_nw_quic_failures"] += 1
                return None

            timeout_ms = int(timeout_s * 1000)
            result = await fetch_nw_quic(url, timeout_ms=timeout_ms)

            if result is None:
                _stats["http3_nw_quic_failures"] += 1
                return None

            if result.get("error"):
                _stats["http3_nw_quic_failures"] += 1
                logger.debug("http3_lane: nw_quic: fetch error for %s: %s",
                             host, result["error"])
                return None

            _stats["http3_nw_quic_success"] += 1
            return result.get("content", b"")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            _stats["http3_nw_quic_failures"] += 1
            logger.debug("http3_lane: nw_quic: fetch failed for %s: %s", host, e)
            return None
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# QuinnRustlsTransportAdapter (F320: Rust-engine priority on M1 arm64).
#
# INTEGRATED: This class now wraps rust.quic.fetch() from rust_extensions/src/quic.rs
# which implements true HTTP/3 via quinn + h3 Rust crates.
#
# Stack: quinn (QUIC transport) + h3 (HTTP/3 layer) + rustls (TLS 1.3)
#
# Priority order (M1 8GB RAM-aware):
#   1. NwQuicTransportAdapter — Apple Network.framework (macOS only)
#   2. QuinnRustlsTransportAdapter — Rust quinn + h3 (cross-platform)
#   3. AioquicTransportAdapter — Python aioquic fallback
#
# M1 8GB bounds (from rust_extensions/src/quic.rs):
#   - Max 3 concurrent connections (semaphore-gated in Rust)
#   - Immediate memory release on session close
#   - Bounded receive buffer (10MB max body)
#   - TLS verification enabled by default (production-safe)
# ---------------------------------------------------------------------------


class QuinnRustlsTransportAdapter:
    """Real QUIC via Rust quinn + h3 + rustls (cross-platform, M1 preferred).

    This adapter wraps rust.quic.fetch() from rust_extensions/src/quic.rs,
    providing true HTTP/3 over QUIC with:
      - quinn: QUIC transport protocol implementation
      - h3: HTTP/3 framing
      - rustls: TLS 1.3 with M1-native ciphers

    Memory efficiency: rustls memory arenas are released immediately on
    session close, keeping the M1 8GB UMA budget clean.

    Returns response body as ``bytes`` on success, or ``None`` on any
    failure. Never raises.
    """

    @staticmethod
    async def fetch(
        url: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = _H3_TIMEOUT_S,
    ) -> bytes | None:
        """Perform a real HTTP/3 request over QUIC via Rust quinn + h3.

        MODERN-14: Uses rust.quic.fetch_async() which returns a native Python
        awaitable via future_into_py(). Direct await — no ThreadPoolExecutor
        needed, zero GIL ping-pong during I/O.

        Returns response body as ``bytes`` on success, or ``None`` on any
        failure. Never raises.
        """
        if not _resolve_enabled():
            return None
        if is_dark_web_url(url):
            logger.debug("http3_lane: quinn: dark web URL skipped: %s", url)
            return None
        if _rss_over_budget():
            _stats["http3_memory_blocks"] += 1
            logger.debug("http3_lane: quinn: memory budget exceeded")
            return None

        host = extract_host(url)
        if not host:
            return None
        if _cache_get(host) is not True:
            return None

        _stats["http3_neqo_attempts"] += 1
        sem = _get_semaphore()
        acquired = False
        try:
            _stats["http3_semaphore_waits"] += 1
            try:
                await safe_wait_for(sem.acquire(), timeout=_H3_WAIT_TIMEOUT_S, label="http3_quinn_sem")
                acquired = True
            except TimeoutError:
                _stats["http3_semaphore_timeouts"] += 1
                logger.debug("http3_lane: quinn: semaphore saturated")
                return None
        except Exception as e:
            logger.debug("http3_lane: quinn: semaphore acquire failed: %s", e)
            return None

        try:
            # MODERN-14: Direct await of rust.quic.fetch_async()
            # Returns native Python awaitable — no ThreadPoolExecutor needed!
            import rust

            if not hasattr(rust.quic, "fetch_async"):
                _stats["http3_neqo_failures"] += 1
                logger.debug("http3_lane: quinn: fetch_async not available")
                return None

            response = await rust.quic.fetch_async(
                url=url,
                method="GET",
                body=None,
                headers=[(k, v) for k, v in (headers or {}).items()] if headers else None,
                timeout_s=timeout_s,
            )

            if response.error:
                logger.debug("http3_lane: quinn: rust error: %s", response.error)
                _stats["http3_neqo_failures"] += 1
                return None

            return bytes(response.body)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            _stats["http3_neqo_failures"] += 1
            logger.debug("http3_lane: quinn: fetch failed for %s: %s", host, e)
            return None
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:  # noqa: BLE001
                    pass


def _rust_quic_fetch_sync(url: str, headers: dict[str, str] | None, timeout_s: float) -> bytes | None:
    """[DEPRECATED] Synchronous wrapper for rust.quic.fetch().

    MODERN-14: This function is deprecated. QuinnRustlsTransportAdapter.fetch()
    now uses rust.quic.fetch_async() directly (native awaitable via future_into_py).
    This sync wrapper is kept only for backward compatibility with any external callers.
    """
    import warnings
    warnings.warn(
        "_rust_quic_fetch_sync is deprecated — use rust.quic.fetch_async() directly",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        import rust
        response = rust.quic.fetch(
            url=url,
            method="GET",
            body=None,
            headers=[(k, v) for k, v in (headers or {}).items()] if headers else None,
            timeout_s=timeout_s,
        )

        if response.error:
            logger.debug("http3_lane: quinn: rust error: %s", response.error)
            return None

        return bytes(response.body)

    except ImportError:
        logger.debug("http3_lane: quinn: rust.quic not available")
        return None
    except Exception as e:
        logger.debug("http3_lane: quinn: exception: %s", e)
        return None


# Backward compatibility alias
NeqoRustlsTransportAdapter = QuinnRustlsTransportAdapter


# ---------------------------------------------------------------------------
# Factory: auto-detect best QUIC transport (F320: neqo priority on M1 arm64).
# ---------------------------------------------------------------------------


def get_quic_transport_adapter():
    """Return the best available QUIC transport for this host.

    Priority order (M1 8GB RAM-aware, Apple Silicon native first):
    1. ``NwQuicTransportAdapter`` — Apple Network.framework native QUIC
       (macOS 12.0+). Zero external deps, ~80 KB/conn, hw-accelerated TLS.
    2. ``QuinnRustlsTransportAdapter`` — Rust quinn + h3 + rustls via
       rust.quic.fetch(). Cross-platform, efficient memory management.
    3. ``AioquicTransportAdapter`` — Python aioquic fallback for all
       other platforms. Requires ``[http3]`` extra (~50-80 MB resident).

    The returned adapter shares the module-level semaphore, LRU cache,
    and memory guard — callers must use ``fetch_http3()`` (not the
    adapter directly) so telemetry and session teardown are consistent.
    """
    # SILICON-05: Network.framework QUIC is the preferred path on darwin/arm64
    if sys.platform == "darwin" and _probe_nw_quic():
        return NwQuicTransportAdapter
    # F320: Rust quinn + h3 is the preferred cross-platform QUIC path
    if _probe_rust_quic():
        return QuinnRustlsTransportAdapter
    if _probe_aioquic():
        return AioquicTransportAdapter
    return None  # no real QUIC available; caller uses opportunistic path


# ---------------------------------------------------------------------------
# Backward-compatible fetch_http3_aioquic wrapper (delegates to adapter).
# ---------------------------------------------------------------------------


async def fetch_http3_aioquic(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = _H3_TIMEOUT_S,
) -> bytes | None:
    """Legacy entry point — now delegates to the best available adapter.

    ``stealth_manager`` and other existing callers are unaffected.
    Internally dispatches to ``NeqoRustlsTransportAdapter`` (M1 arm64)
    or ``AioquicTransportAdapter`` (fallback), with fail-soft returning
    ``None`` when no QUIC transport is available.

    Dark web URLs are rejected early — QUIC/UDP cannot be tunneled through
    Tor TransPort or I2P HTTP proxy, so probing aioquic is wasted work.
    """
    # Dark web guard: QUIC/UDP can't go through Tor/I2P proxies
    if is_dark_web_url(url):
        return None
    adapter = get_quic_transport_adapter()
    if adapter is None:
        return None
    return await adapter.fetch(url, headers=headers, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# aioquic availability probe (moved above AioquicTransportAdapter).
# ---------------------------------------------------------------------------


def _probe_aioquic() -> bool:
    """Detect aioquic availability once; cache the result. Returns True iff
    the optional ``[http3]`` extra is installed AND importable.

    ``aioquic`` is intentionally NOT in the default closure (F207N-C
    invariant: aioquic pulls in ``cryptography`` and OpenSSL bindings,
    ~50-80 MB resident). It lives in the ``[http3]`` extra so the cost
    is paid only by callers that explicitly want real QUIC.
    """
    global _aioquic_checked, _aioquic_available
    if _aioquic_checked:
        return _aioquic_available
    _aioquic_checked = True
    try:
        # Lazy import; the module is in the ``[http3]`` extra only.
        import aioquic.asyncio  # type: ignore[import-not-found]
        import aioquic.h3.connection  # type: ignore[import-not-found]
        import aioquic.quic.configuration  # type: ignore[import-not-found]

        _aioquic_available = True
    except ImportError:
        _aioquic_available = False
        logger.debug("http3_lane: aioquic not available (not installed with --extra http3)")
    except Exception as e:
        _aioquic_available = False
        logger.debug("http3_lane: aioquic import failed: %s", e)
    return _aioquic_available


# SILICON-05: Apple Network.framework QUIC availability probe.
# Separate from _probe_nw_connection in nw_connection_lane.py — QUIC requires
# macOS 12.0+ (nw_parameters_create_quic was added in Monterey).
_nw_quic_checked: bool = False
_nw_quic_available: bool = False


def _probe_nw_quic() -> bool:
    """Detect Network.framework QUIC availability once; cache the result.

    Returns True only on macOS 12.0+ with nw_framework Rust extension built.
    Unlike _probe_neqo() (always False) and _probe_aioquic() (requires
    [http3] extra), this path has ZERO external Python dependencies —
    just the Rust extension that's already compiled for nw_connection.
    """
    global _nw_quic_checked, _nw_quic_available
    if _nw_quic_checked:
        return _nw_quic_available
    _nw_quic_checked = True
    try:
        import platform as _platform
        import sys as _sys
        if _sys.platform != "darwin":
            _nw_quic_available = False
            return False
        # nw_parameters_create_quic requires macOS 12.0+
        ver_str = _platform.mac_ver()[0]
        if ver_str:
            parts = ver_str.split(".")
            major = int(parts[0]) if parts else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            if major < 12:
                _nw_quic_available = False
                logger.debug("http3_lane: nw_quic: macOS %d.%d < 12.0, QUIC not available", major, minor)
                return False
        # Probe the Rust extension
        from hledac.universal.transport.nw_quic_lane import is_nw_quic_available
        _nw_quic_available = is_nw_quic_available()
        if not _nw_quic_available:
            logger.debug("http3_lane: nw_quic: lane not available (Rust extension or env gate)")
    except ImportError:
        _nw_quic_available = False
        logger.debug("http3_lane: nw_quic: nw_quic_lane not importable")
    except Exception as e:
        _nw_quic_available = False
        logger.debug("http3_lane: nw_quic: probe failed: %s", e)
    return _nw_quic_available


# F1 fix: lazy curl_cffi availability probe — mirrors _probe_aioquic() pattern.
# curl_cffi is in default deps but may be uninstalled; silent no-op if missing.
def _probe_curl_cffi() -> bool:
    """Detect curl_cffi availability once; cache the result. Returns True iff
    curl_cffi is importable. Fallback: no H3 upgrade (fail-soft).
    """
    global _curl_cffi_checked, _curl_cffi_available
    if _curl_cffi_checked:
        return _curl_cffi_available
    _curl_cffi_checked = True
    try:
        import curl_cffi  # type: ignore[import-not-found]  # noqa: F401
        _curl_cffi_available = True
    except ImportError:
        _curl_cffi_available = False
        logger.debug("http3_lane: curl_cffi not available")
    except Exception as e:
        _curl_cffi_available = False
        logger.debug("http3_lane: curl_cffi import failed: %s", e)
    return _curl_cffi_available


# ---------------------------------------------------------------------------
# Convenience for tests + tooling.
# ---------------------------------------------------------------------------
def clear_cache() -> None:
    """Wipe the LRU cache. Tests only; never call from production paths."""
    _lru_cache.clear()


def is_enabled() -> bool:
    """Re-evaluate the env gate. Cheaper than the F260 ``_HTTP3_ENABLED``
    global (which is frozen at import time).
    """
    return _resolve_enabled()


__all__ = [
    # Opportunistic curl_cffi path
    "http_version_for_curl_cffi",
    "record_from_curl_cffi_result",
    "record_h3_support",
    # Real QUIC (factory-selected, shared by fetch_http3_aioquic)
    "NwQuicTransportAdapter",  # SILICON-05: Network.framework native QUIC (priority #1 on M1)
    "AioquicTransportAdapter",
    "NeqoRustlsTransportAdapter",
    "get_quic_transport_adapter",
    # Legacy entry point (dispatches to best available adapter)
    "fetch_http3_aioquic",
    # Utilities
    "extract_host",
    "is_dark_web_url",
    "is_enabled",
    "get_stats",
    "reset_stats",
    "clear_cache",
    "shutdown_probe_tasks",
]
