"""
transport/http3_lane.py

P1-2 (Sprint 2026-06-08): Centralized HTTP/3 (QUIC) lane.

Two strategies, one bounded layer:

1. ``curl_cffi_opportunistic`` (default, no extra deps)
   - Reuses curl_cffi >= 0.7's ``HttpVersion.v3`` kwarg.
   - Activates only AFTER the server has advertised ``h3`` via Alt-Svc.
   - Cached per host; bounded LRU (512 entries) so an unbounded discovery
     crawl cannot exhaust the UMA budget on M1 8GB.
   - This is what F260 shipped as the opportunistic Alt-Svc path in
     ``fetching/public_fetcher.py`` (lines 223-336), now consolidated.

2. ``aioquic_stealth`` (opt-in, requires ``[http3]`` extra)
   - Real HTTP/3 over QUIC via ``aioquic``. Heavier (pulls cryptography
     and OpenSSL bindings, ~50-80 MB resident).
   - Per-request ``asyncio.wait_for(timeout=...)`` so a stuck UDP handshake
     can never block the fetch loop.
   - Concurrency capped at ``_H3_CONCURRENCY_MAX = 3`` to keep UDP
     receive buffers + OpenSSL contexts inside the M1 8GB envelope.
   - Memory guard: psutil RSS sample blocks the lane at 5.5 GiB
     (matches sprint mission budget documented in ``utils/uma_budget.py``).
   - Lazy import so the cost is paid only when the lane is requested.

Fail-soft invariants (enforced by every code path below):
- No bare ``except:``; always ``except Exception``.
- aioquic missing -> fall back to opportunistic path (which itself
  falls back to HTTP/1.1 inside curl_cffi).
- Any error -> return ``None`` and let the caller continue without
  the upgrade; never propagate exceptions to the fetch path.
- Cache overflow -> LRU eviction in O(1) using ``OrderedDict``;
  never raise on trim failure.
- Semaphore acquisition -> non-blocking with timeout; if not acquired
  in ``_H3_WAIT_TIMEOUT_S``, the lane returns ``None`` and the
  caller proceeds on the prior HTTP/1.1 / HTTP/2 path.

Env gates (project convention: ``HLEDAC_ENABLE_<LANE>``):
- ``HLEDAC_ENABLE_HTTPX_H3 = 1`` enables BOTH strategies; default 0.
  (Parallels the existing ``HLEDAC_ENABLE_HTTPX_H2`` for HTTP/2.)
- ``HLEDAC_HTTP3 = 1`` (legacy F260 alias) is also accepted; the
  new explicit ``HLEDAC_ENABLE_HTTPX_H3`` takes precedence.

Why this module exists separately from ``curl_cffi_fetch.py``:
The opportunistic Alt-Svc cache and the real-QUIC path have completely
different failure modes, timeouts, and dependency footprints. Combining
them with the curl_cffi wrapper forced every reader to reason about all
three at once. Splitting the lane into a dedicated module also gives
us a single seam to test in isolation, gate with a single env var, and
expose telemetry from a single counter dictionary.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounds (M1 8GB tuned; do NOT loosen without re-running the
# M1 8GB mission budget probe in ``benchmarks/m1_phase4_budget.py``).
# ---------------------------------------------------------------------------
_H3_CACHE_MAX: int = 512                # host -> (timestamp, supports_h3)
_H3_CONCURRENCY_MAX: int = 3            # M1 8GB: 3 concurrent QUIC handshakes
_H3_TIMEOUT_S: float = 8.0              # per-request hard cap
_H3_WAIT_TIMEOUT_S: float = 2.0         # how long to wait for the semaphore
_H3_CACHE_TTL_S: int = 86_400           # 24h, same as stealth_manager F194
_H3_RSS_BLOCK_GIB: float = 5.5          # mission budget; matches uma_budget
_H3_RSS_PROBE_TIMEOUT_S: float = 0.05   # psutil is fast but never block fetch

# Process handle is created lazily; ``os.getpid()`` is the cheap key.
_psutil_proc: Any = None
_psutil_import_failed: bool = False


def _get_psutil_proc() -> Any:
    """Return a cached ``psutil.Process`` handle, or ``None`` if unavailable.

    Importing psutil at module load is undesirable on M1 8GB: it pulls in
    ``psutil._psutil_osx`` and the Mach kernel interface even for sprints
    that never use HTTP/3. Fail-soft: missing psutil disables the memory
    guard but leaves everything else operational.
    """
    global _psutil_proc, _psutil_import_failed
    if _psutil_proc is not None:
        return _psutil_proc
    if _psutil_import_failed:
        return None
    try:
        import psutil  # type: ignore[import-not-found]

        _psutil_proc = psutil.Process(os.getpid())
        return _psutil_proc
    except Exception as e:
        # Lazy import invariant: never raise on first import attempt.
        logger.debug("http3_lane: psutil unavailable, memory guard off: %s", e)
        _psutil_import_failed = True
        return None


# ---------------------------------------------------------------------------
# Env-gate resolution (project convention: HLEDAC_ENABLE_<LANE>).
# ---------------------------------------------------------------------------
def _resolve_enabled() -> bool:
    """Resolve HTTP/3 gate. ``HLEDAC_ENABLE_HTTPX_H3=1`` takes precedence;
    ``HLEDAC_HTTP3=1`` (legacy F260 alias) is honored for back-compat.
    Anything else (including unset) -> disabled.
    """
    v = os.environ.get("HLEDAC_ENABLE_HTTPX_H3", "")
    if v == "1":
        return True
    return os.environ.get("HLEDAC_HTTP3", "") == "1"


_ENABLED: bool = _resolve_enabled()

# Lazy singletons (created on first request, not at import).
#
# The LRU cache does NOT need a lock: we run in a single-threaded asyncio
# loop, and ``OrderedDict`` operations are atomic with respect to ``await``.
# Adding a lock here would only protect against a future migration to
# ``asyncio.to_thread`` callers, which we explicitly forbid in CLAUDE.md
# (``Nepoužívej asyncio.run() v ThreadPoolExecutor — M1 crash vector``).
_lru_cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()
_semaphore: asyncio.Semaphore | None = None
_aioquic_checked: bool = False
_aioquic_available: bool = False


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_H3_CONCURRENCY_MAX)
    return _semaphore


# ---------------------------------------------------------------------------
# Telemetry (small, bounded, no I/O; safe to read in tight loops).
# ---------------------------------------------------------------------------
_stats: dict[str, int] = {
    "enabled": 1 if _ENABLED else 0,
    "altsvc_hits": 0,
    "altsvc_misses": 0,
    "altsvc_records": 0,
    "altsvc_evictions": 0,
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
    proc = _get_psutil_proc()
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
    gib = rss / (1024 ** 3)
    return gib > _H3_RSS_BLOCK_GIB


# ---------------------------------------------------------------------------
# Alt-Svc cache (bounded LRU).
# ---------------------------------------------------------------------------
def _cache_get(host: str) -> bool | None:
    """Return cached H3 support for ``host`` (respects TTL), or ``None`` on miss.

    Touches the LRU position (move-to-end) on hit so hot hosts stay hot.
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
        except Exception:
            pass
        _stats["altsvc_misses"] += 1
        return None
    # Move to end (LRU touch); dicts preserve insertion order in 3.7+.
    try:
        _lru_cache.move_to_end(host)
    except Exception:
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


def extract_host(url: str) -> str:
    """Return lowercased hostname from URL, or empty string on parse failure."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


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
    # Lazy import: never load curl_cffi at module import.
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
import asyncio  # noqa: E402  — late import to keep module-load cheap
_HEAD_PROBE_TIMEOUT_S: float = 4.0  # bounded; M1 8GB friendly


async def _speculative_altsvc_probe_inner(url: str) -> None:
    """Inner coroutine for the speculative Alt-Svc probe. Sends a
    single HEAD request, parses the response headers, and updates
    the LRU if the server advertises h3. Never raises.

    Uses the same bounded AsyncSession as the curl_cffi runtime —
    the session is borrowed (not owned) so we don't multiply
    connection pools. If no session is available, the probe falls
    through to a fresh AsyncSession.
    """
    try:
        host = extract_host(url)
        if not host:
            return
        # Use a per-probe session to keep the probe isolated from
        # the live fetch session. The session is closed at the end
        # of the probe to avoid leaking connections.
        from curl_cffi.requests import AsyncSession  # type: ignore

        sess = AsyncSession(
            impersonate="chrome124",
            timeout=_HEAD_PROBE_TIMEOUT_S,
            max_clients=2,
        )
        try:
            try:
                resp = await asyncio.wait_for(
                    sess.head(url, timeout=_HEAD_PROBE_TIMEOUT_S),
                    timeout=_HEAD_PROBE_TIMEOUT_S + 1.0,
                )
            except TimeoutError:
                return
            except Exception as e:  # noqa: BLE001
                logger.debug("http3_lane: speculative probe to %s failed: %s", host, e)
                return
            try:
                if resp is not None and resp.headers:
                    if _altsvc_advertises_h3(resp.headers):
                        _cache_put(host, True)
                        logger.debug(
                            "http3_lane: speculative probe primed H3 for %s", host
                        )
            except Exception as e:  # noqa: BLE001
                logger.debug("http3_lane: speculative header parse failed: %s", e)
        finally:
            try:
                await sess.aclose()
            except Exception:  # noqa: BLE001
                pass
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
    try:
        asyncio.create_task(
            _speculative_altsvc_probe_inner(url),
            name=f"http3_lane:speculative_probe:{host}",
        )
    except RuntimeError:
        # No event loop in this context. The probe simply does not
        # happen; the next reactive fetch will populate the LRU.
        logger.debug(
            "http3_lane: speculative probe skipped (no event loop) for %s", host
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("http3_lane: speculative probe scheduling failed: %s", e)


# ---------------------------------------------------------------------------
# Public API: real QUIC via aioquic (stealth / DA+ profile lane).
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
        # ``reportMissingImports`` is a known false-positive here.
        import aioquic.asyncio  # type: ignore[import-not-found]
        import aioquic.h3.connection  # type: ignore[import-not-found]
        import aioquic.quic.configuration  # type: ignore[import-not-found]

        # Assign to module-level names so the imports are referenced and
        # tools like vulture / pyright don't flag them as F401.
        globals()["aioquic_asyncio"] = aioquic.asyncio
        globals()["aioquic_h3_connection"] = aioquic.h3.connection
        globals()["aioquic_quic_configuration"] = aioquic.quic.configuration
        _aioquic_available = True
    except Exception as e:
        _aioquic_available = False
        logger.debug("http3_lane: aioquic not available: %s", e)
    return _aioquic_available


async def fetch_http3_aioquic(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = _H3_TIMEOUT_S,
) -> bytes | None:
    """Perform a real HTTP/3 request over QUIC via aioquic.

    Returns response body as ``bytes`` on success, or ``None`` on ANY
    failure (timeout, semaphore exhaustion, aioquic missing, server
    unreachable, protocol error). Never raises.

    The caller (typically ``stealth_manager._http3_request``) is
    responsible for content-type decoding and size capping; this
    function returns the raw body bytes.
    """
    if not _resolve_enabled():
        return None
    # Memory guard runs BEFORE the aioquic availability probe: the guard
    # is an M1 8GB mission-budget invariant, not a feature flag, so it
    # must be evaluated regardless of whether aioquic is installed.
    if _rss_over_budget():
        _stats["http3_memory_blocks"] += 1
        logger.debug("http3_lane: memory budget exceeded, skipping aioquic")
        return None
    if not _probe_aioquic():
        return None

    host = extract_host(url)
    if not host:
        return None

    # Only attempt H3 against hosts we have already seen advertise it,
    # OR hosts that pass the opportunistic path's known-h3 predicate.
    # This avoids a wasteful QUIC handshake against non-h3 servers.
    if _cache_get(host) is not True:
        return None

    _stats["http3_aioquic_attempts"] += 1
    sem = _get_semaphore()
    try:
        # Non-blocking acquire with wall-clock timeout: if all 3
        # handshakes are in flight, return None rather than queue.
        _stats["http3_semaphore_waits"] += 1
        try:
            await asyncio.wait_for(sem.acquire(), timeout=_H3_WAIT_TIMEOUT_S)
        except TimeoutError:
            _stats["http3_semaphore_timeouts"] += 1
            logger.debug("http3_lane: semaphore saturated, skipping %s", host)
            return None
    except Exception as e:
        logger.debug("http3_lane: semaphore acquire failed (fail-soft): %s", e)
        return None

    try:
        # The actual QUIC handshake + H3 request. All imports are
        # INSIDE the try block so a missing aioquic surfaces as
        # ``None`` instead of a top-level ImportError on M1 8GB.
        # ``reportMissingImports`` is a known false-positive: aioquic
        # lives in the ``[http3]`` extra and is intentionally absent
        # from the default closure (F207N-C invariant).
        try:
            from aioquic.asyncio import connect as _quic_connect  # type: ignore[import-not-found]
            from aioquic.h3.connection import H3Connection  # type: ignore[import-not-found]
            from aioquic.quic.configuration import QuicConfiguration  # type: ignore[import-not-found]
        except Exception as e:
            logger.debug("http3_lane: aioquic import inside call failed: %s", e)
            return None

        parsed = urlparse(url)
        port = parsed.port or 443
        cfg = QuicConfiguration(is_client=True)

        async def _do_quic_request() -> bytes:
            """Inner coroutine: handshake + H3 GET, wrapped in wait_for
            so a stuck UDP handshake can never block the fetch path
            beyond ``timeout_s`` (default 8s).
            """
            async with _quic_connect(
                host, port, configuration=cfg, create_protocol=H3Connection
            ) as protocol:
                req_headers: list[tuple[bytes, bytes]] = [
                    (b":method", b"GET"),
                    (b":path", (parsed.path or "/").encode("ascii", "ignore")),
                    (b":authority", host.encode("ascii", "ignore")),
                ]
                if headers:
                    for k, v in headers.items():
                        try:
                            req_headers.append(
                                (k.encode("ascii", "ignore"), v.encode("ascii", "ignore"))
                            )
                        except Exception:
                            pass
                stream_id = protocol.make_request(req_headers)
                await protocol.wait_for_response(stream_id)
                return await protocol.receive_data(stream_id)

        try:
            data = await asyncio.wait_for(_do_quic_request(), timeout=timeout_s)
            _stats["http3_aioquic_success"] += 1
            return data
        except TimeoutError:
            _stats["http3_timeouts"] += 1
            logger.debug("http3_lane: aioquic request exceeded %.1fs for %s", timeout_s, host)
            return None
        except Exception as e:
            _stats["http3_aioquic_failures"] += 1
            logger.debug("http3_lane: aioquic request failed for %s: %s", host, e)
            return None
    except asyncio.CancelledError:
        # Honour cooperative cancellation: don't swallow it.
        raise
    except Exception as e:
        _stats["http3_aioquic_failures"] += 1
        logger.debug("http3_lane: unexpected aioquic error (fail-soft): %s", e)
        return None
    finally:
        try:
            sem.release()
        except Exception:
            # Already-released or uninitialized; fail-soft.
            pass


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
    "fetch_http3_aioquic",
    "http_version_for_curl_cffi",
    "record_from_curl_cffi_result",
    "record_h3_support",
    "extract_host",
    "is_enabled",
    "get_stats",
    "reset_stats",
    "clear_cache",
]
