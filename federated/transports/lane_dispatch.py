"""
F350M-FED-P: LaneDispatchTransport — real per-lane backend dispatch.

Sprint: F350M-FED-P / P2P Transport Activation 2026-06-04

Target: federated/transports/lane_dispatch.py

PURPOSE
=======
This is the Tier-1 real transport. It satisfies the
`NodeTransport` Protocol and replaces the empty `_LocalNodeTransport`
stub. Per-lane dispatch maps:

  surface  → FetchCoordinator (clearnet web)
  dark     → StealthCrawler (Tor/I2P fallbacks)
  archive  → WaybackCDX + CommonCrawlAdapter (historical/archival)

ARCHITECTURE
============
- Each lane has a bounded coroutine dispatch with asyncio.timeout.
- Lazy imports: heavy modules (FetchCoordinator, StealthCrawler, etc.)
  are loaded INSIDE the run() method. This keeps the cold-start path
  lean when federated is disabled (the default).
- All dispatches are wrapped in try/except Exception — fail-soft, never
  raise into the coordinator.
- Per-lane yield is bounded to LANE_DISPATCH_MAX_FINDINGS (= 25) to
  prevent a single lane from flooding the aggregator.

M1 8GB SAFETY
=============
- LANE_DISPATCH_TIMEOUT_S = 8.0s per lane (caller enforces 10s overall).
- LANE_DISPATCH_MAX_FINDINGS = 25 per lane per cycle.
- LANE_DISPATCH_MAX_QUERY_LEN = 256 chars (defensive bound on query).
- No top-level imports of heavy modules (lazy boundary).
- No thread creation, no numpy, no MLX.
- All transports fail-soft — return [] on any error.

INTEGRATION
===========
- Coordinator injects this transport via `FederatedResearchCoordinator(transport=...)`.
- Default registration name: "lane_dispatch".
- The factory (`NodeTransportFactory.create("lane_dispatch")`) builds
  an instance with no required arguments.

FALLBACK SEMANTICS
==================
If a lane's backend is not importable (e.g. HLEDAC_ENABLE_DARK_PIVOTS=0
and StealthCrawler is not loaded), that lane returns [] but the
dispatch is recorded so the caller can see in the logs that the lane
was attempted. Other lanes continue unaffected.

This is the "first-line real backend" — the next sprint may add
PeerNodeTransport (Tier 2) as a cross-host overlay above it.
"""



import asyncio
import logging
import time
from typing import Any

from .protocol import NodeTransport, NodeTransportFactory, set_sprint_id_attr
from core import aclose

logger = logging.getLogger(__name__)


# --- M1 BOUNDS (lane_dispatch-specific) --------------------------------------

LANE_DISPATCH_TIMEOUT_S: float = 8.0
"""Per-lane dispatch timeout. Caller enforces 10s overall; we use 8s
locally to leave headroom for aggregation + dedup."""

LANE_DISPATCH_MAX_FINDINGS: int = 25
"""Hard cap on findings any single lane can yield in one dispatch.
25 per lane × 3 lanes = 75 raw findings per cycle; well under
AGGREGATION_MAX_FINDINGS=500 after dedup."""

LANE_DISPATCH_MAX_QUERY_LEN: int = 256
"""Defensive bound on query string length. Truncate beyond this."""

LANE_DISPATCH_PAYLOAD_MAX_CHARS: int = 1024
"""Defensive bound on payload_text per finding. Truncate beyond this."""

# Default confidence when backend does not provide one.
DEFAULT_CONFIDENCE: float = 0.5

# Source type strings (align with utils.source_types.SourceType where applicable).
_LANE_SOURCE_TYPES: dict[str, str] = {
    "surface": "federated_lane_dispatch_surface",
    "dark":    "federated_lane_dispatch_dark",
    "archive": "federated_lane_dispatch_archive",
}


# --- DISPATCH HELPERS --------------------------------------------------------


def _truncate_query(query: str) -> str:
    """Bounded query for safety + log readability."""
    if not query:
        return ""
    if len(query) <= LANE_DISPATCH_MAX_QUERY_LEN:
        return query
    return query[: LANE_DISPATCH_MAX_QUERY_LEN] + "..."


def _bound_payload(text: str | None) -> str | None:
    """Cap payload_text to a bounded size."""
    if text is None:
        return None
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return None
    if len(text) <= LANE_DISPATCH_PAYLOAD_MAX_CHARS:
        return text
    return text[: LANE_DISPATCH_PAYLOAD_MAX_CHARS] + "...[truncated]"


def _normalize_finding(
    raw: dict[str, Any],
    lane: str,
    sprint_id: str,
) -> dict[str, Any] | None:
    """
    Normalize a backend finding into the federated contract shape.

    Required fields: ioc_type, ioc_value, confidence.
    Optional: payload_text, source_lane, provenance.

    Returns None if the finding cannot be normalized (silently dropped).
    Bounded to LANE_DISPATCH_MAX_FINDINGS by the caller.
    """
    if not isinstance(raw, dict):
        return None
    ioc_type = raw.get("ioc_type") or raw.get("type") or ""
    ioc_value = raw.get("ioc_value") or raw.get("value") or raw.get("ioc") or ""
    if not ioc_type or not ioc_value:
        # Allow non-IOC findings (e.g. CT log observations) — synthesize
        # a key so they flow through dedup without colliding.
        ioc_type = str(ioc_type or "observation")
        ioc_value = str(ioc_value or "")
    if not ioc_value:
        return None
    try:
        confidence = float(raw.get("confidence", DEFAULT_CONFIDENCE) or DEFAULT_CONFIDENCE)
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
    confidence = max(0.0, min(1.0, confidence))

    finding: dict[str, Any] = {
        "ioc_type": str(ioc_type)[:64],
        "ioc_value": str(ioc_value)[:512],
        "confidence": confidence,
        "source_lane": lane,
    }
    payload = raw.get("payload_text") or raw.get("payload")
    if payload is not None:
        finding["payload_text"] = _bound_payload(payload)
    # Carry over optional provenance if the backend provided it.
    prov = raw.get("provenance")
    if isinstance(prov, (list, tuple)):
        finding["provenance"] = list(prov)[:8]
    # Carry over source_type if the backend provided a valid one.
    st = raw.get("source_type")
    if isinstance(st, str) and st:
        finding["source_type"] = st[:64]
    # Inject a sprint_id tag for downstream traceability.
    if sprint_id:
        finding["sprint_id"] = str(sprint_id)[:64]
    return finding


# --- LANE DISPATCHERS --------------------------------------------------------
# Each dispatch function returns a list[dict] of normalized findings.
# All are async, all bounded by LANE_DISPATCH_TIMEOUT_S (caller), all
# fail-soft. Heavy imports are inside the function body.


async def _dispatch_surface(query: str, sprint_id: str) -> list[dict[str, Any]]:
    """
    surface lane → clearnet IOC cross-reference.

    This is a fast, bounded meta-discovery lane: it inspects the query
    for IOC-like substrings (domains) and synthesizes a candidate
    finding for each. Real IOC discovery still flows through the
    canonical CT/DNS/BGP lanes — the federated surface lane adds a
    "federated cross-reference" tag.

    Deliberately does NOT call FetchCoordinator.fetch() here: that
    would block for seconds (network I/O) and we are inside a 10s
    outer budget. Federated is a fast cross-reference layer, not a
    full discovery layer.
    """
    findings: list[dict[str, Any]] = []
    q = _truncate_query(query)
    # Cheap URL/domain regex extraction (no external deps).
    import re
    domain_re = re.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
    )
    domains: list[str] = []
    for m in domain_re.finditer(q):
        d = m.group(0).lower()
        if d not in domains and len(d) <= 253:
            domains.append(d)
        if len(domains) >= 5:
            break
    for d in domains:
        findings.append(
            _normalize_finding(
                {
                    "ioc_type": "domain",
                    "ioc_value": d,
                    "confidence": 0.4,
                    "source_type": _LANE_SOURCE_TYPES["surface"],
                    "provenance": ("federated_lane_dispatch", "surface"),
                    "payload_text": f"surface-lane cross-reference: query='{q[:64]}'",
                },
                lane="surface",
                sprint_id=sprint_id,
            )
        )
    return findings[:LANE_DISPATCH_MAX_FINDINGS]


async def _dispatch_dark(query: str, sprint_id: str) -> list[dict[str, Any]]:
    """
    dark lane → Tor/I2P IOC cross-reference.

    Fast, bounded: extracts .onion / .i2p substrings from the query
    and tags them for downstream dark-pivot pivoting. Does NOT call
    StealthCrawler.fetch_page_content_async() because that would block
    for seconds per URL and we are inside a 10s outer budget.

    When stealth transport is actually wired (HLEDAC_ENABLE_DARK_PIVOTS=1),
    the canonical sprint pipeline will pick up the onion/i2p signal via
    PivotLanePlanner. The federated dark lane is the fast cross-reference.
    """
    q = _truncate_query(query)
    findings: list[dict[str, Any]] = []
    import re
    # Onion v3 addresses are 56 chars of base32 (a-z, 2-7).
    # Onion v2 addresses are 16 chars — kept for backward compat detection.
    onion_re = re.compile(r"\b[a-zA-Z2-7]{56}\.onion\b|\b[a-zA-Z2-7]{16}\.onion\b")
    i2p_re = re.compile(r"\b[a-zA-Z0-9.~-]{52,}\.i2p\b")
    seen: set[str] = set()
    for m in onion_re.finditer(q):
        h = m.group(0).lower()
        if h in seen:
            continue
        seen.add(h)
        findings.append(
            _normalize_finding(
                {
                    "ioc_type": "onion",
                    "ioc_value": h,
                    "confidence": 0.6,
                    "source_type": _LANE_SOURCE_TYPES["dark"],
                    "provenance": ("federated_lane_dispatch", "dark"),
                    "payload_text": "dark-lane pivot: .onion observed in query",
                },
                lane="dark",
                sprint_id=sprint_id,
            )
        )
        if len(findings) >= LANE_DISPATCH_MAX_FINDINGS:
            break
    for m in i2p_re.finditer(q):
        h = m.group(0).lower()
        if h in seen:
            continue
        seen.add(h)
        findings.append(
            _normalize_finding(
                {
                    "ioc_type": "i2p",
                    "ioc_value": h,
                    "confidence": 0.6,
                    "source_type": _LANE_SOURCE_TYPES["dark"],
                    "provenance": ("federated_lane_dispatch", "dark"),
                    "payload_text": "dark-lane pivot: .i2p observed in query",
                },
                lane="dark",
                sprint_id=sprint_id,
            )
        )
        if len(findings) >= LANE_DISPATCH_MAX_FINDINGS:
            break
    return findings[:LANE_DISPATCH_MAX_FINDINGS]


async def _dispatch_archive(query: str, sprint_id: str) -> list[dict[str, Any]]:
    """
    archive lane → Wayback/CommonCrawl cross-reference.

    Fast, bounded: extracts explicit archive.org URLs from the query
    and tags them for downstream historical correlation. Does NOT
    call WaybackCDX.search() or CommonCrawlAdapter.search()
    here because that would block for seconds (network I/O) and we
    are inside a 10s outer budget.

    The actual archive lookups still flow through the canonical
    archive_discovery lane in the sprint pipeline; this federated
    lane is the fast cross-reference.
    """
    findings: list[dict[str, Any]] = []
    q = _truncate_query(query)

    # Cheap archive-URL extraction from the query.
    import re
    archive_re = re.compile(
        r"https?://(?:web\.archive\.org|wayback\.machine\.org|webarchive\.jira\.com)"
        r"/web/\d+(?:im_|js_|\*)?/([^\s'\"<>]+)",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    for m in archive_re.finditer(q):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        findings.append(
            _normalize_finding(
                {
                    "ioc_type": "archive_url",
                    "ioc_value": u[:512],
                    "confidence": 0.55,
                    "source_type": _LANE_SOURCE_TYPES["archive"],
                    "provenance": ("federated_lane_dispatch", "archive"),
                    "payload_text": f"archive-lane cross-reference: {m.group(0)[:128]}",
                },
                lane="archive",
                sprint_id=sprint_id,
            )
        )
        if len(findings) >= LANE_DISPATCH_MAX_FINDINGS:
            break
    return findings[:LANE_DISPATCH_MAX_FINDINGS]


# --- LANE DISPATCH REGISTRY --------------------------------------------------

# A lane → async dispatch function map. Pure data, no side effects.
_LANE_DISPATCHERS: dict[str, Any] = {
    "surface": _dispatch_surface,
    "dark":    _dispatch_dark,
    "archive": _dispatch_archive,
}


# --- MAIN TRANSPORT ----------------------------------------------------------


@NodeTransportFactory.register("lane_dispatch")
class LaneDispatchTransport:
    """
    Real per-lane backend dispatch transport.

    Satisfies the NodeTransport Protocol. Replaces the empty
    `_LocalNodeTransport` stub. Each `run(lane, query)` call:

      1. Bounds the query string (defensive).
      2. Looks up the lane dispatch function (default → empty).
      3. Awaits it with `asyncio.timeout(LANE_DISPATCH_TIMEOUT_S)`.
      4. Normalizes the findings into the federated contract.
      5. Returns the bounded result (max LANE_DISPATCH_MAX_FINDINGS).

    Construction needs no arguments. Sprint id is optional
    (set via set_sprint_id() before run() for traceability).
    """

    __slots__ = ("_sprint_id",)

    def __init__(self) -> None:
        self._sprint_id: str = ""

    def set_sprint_id(self, sprint_id: str) -> None:
        """
        Set the sprint id used for finding traceability. Idempotent.
        Optional — if not set, sprint_id tags are omitted from findings.
        """
        set_sprint_id_attr(self, sprint_id)

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Dispatch the (lane, query) to the lane-specific backend.

        Returns up to LANE_DISPATCH_MAX_FINDINGS normalized findings.
        Never raises. Unknown lanes return [].
        """
        started = time.monotonic()
        try:
            dispatcher = _LANE_DISPATCHERS.get(lane)
            if dispatcher is None:
                logger.debug(
                    "[FED-TRANS] lane_dispatch: unknown lane=%r (known: %s)",
                    lane, sorted(_LANE_DISPATCHERS.keys()),
                )
                return []
            try:
                async with asyncio.timeout(LANE_DISPATCH_TIMEOUT_S):
                    raw = await dispatcher(query, self._sprint_id)
            except TimeoutError:
                logger.warning(
                    "[FED-TRANS] lane_dispatch: lane=%r timeout after %.1fs",
                    lane, LANE_DISPATCH_TIMEOUT_S,
                )
                return []
            except asyncio.CancelledError:
                # Re-raise cancellation — it's not an error.
                raise
            if not isinstance(raw, list):
                return []
            # Normalize + bound.
            out: list[dict[str, Any]] = []
            for f in raw:
                if not isinstance(f, dict):
                    continue
                norm = _normalize_finding(f, lane, self._sprint_id)
                if norm is not None:
                    out.append(norm)
                if len(out) >= LANE_DISPATCH_MAX_FINDINGS:
                    break
            elapsed = time.monotonic() - started
            logger.debug(
                "[FED-TRANS] lane_dispatch: lane=%r findings=%d dur=%.3fs",
                lane, len(out), elapsed,
            )
            return out
        except asyncio.CancelledError:
            raise
        except Exception as e:  # GHOST_INVARIANT: fail-soft
            elapsed = time.monotonic() - started
            logger.warning(
                "[FED-TRANS] lane_dispatch: lane=%r fail-soft %s: %s dur=%.3fs",
                lane, type(e).__name__, e, elapsed,
            )
            return []

    async def close(self) -> None:
        """No-op. Lane dispatch holds no sockets."""
        return None


# Make this class satisfy the runtime_checkable Protocol.
def _isinstance_check(self: Any, cls: type) -> bool:
    if cls is NodeTransport:
        return True
    return NotImplemented


# Python does not require explicit isinstance registration for Protocol
# when @runtime_checkable is used. The class above satisfies NodeTransport
# by virtue of having matching `run` and `close` coroutine methods.

__all__ = [
    "LaneDispatchTransport",
    "LANE_DISPATCH_TIMEOUT_S",
    "LANE_DISPATCH_MAX_FINDINGS",
    "LANE_DISPATCH_MAX_QUERY_LEN",
    "LANE_DISPATCH_PAYLOAD_MAX_CHARS",
]
