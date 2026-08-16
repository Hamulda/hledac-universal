"""
Alternative Protocol Fetcher — Unified access to beyond-indexed content.

Orchestrates IPFS, Gopher, Gemini, I2P, ZeroNet, and Freenet/Hyphanet protocols

for accessing content invisible to standard web crawlers.

F230: Alternative Protocol Stack integration.
ISSUE-P8-004: Per-protocol concurrency — head-of-line blocking eliminated.
ISSUE-005: ZeroNet & Freenet/Hyphanet content mining added.

Gating:
  - HLEDAC_ENABLE_ALT_PROTOCOLS=1 enables core protocols (IPFS, Gopher, Gemini, I2P)
  - HLEDAC_ENABLE_ZERONET=1 enables ZeroNet protocol
  - HLEDAC_ENABLE_FREENET=1 enables Freenet/Hyphanet protocol
  - HLEDAC_ENABLE_SOCIAL=1 enables social protocols (Fediverse, Matrix)
  - Per-protocol concurrency limits (IPFS=3, Gemini=2, Gopher=2, I2P=1, ZeroNet=2, Freenet=1, Fediverse=2, Matrix=1)
  - Total max concurrent: 14 I/O-bound operations (M1 8GB safe, all are I/O-bound)
  - Fail-soft: individual protocol failures don't block others

Returns list[CanonicalFinding] with appropriate source_type per protocol.
"""
import asyncio
import logging
import os
import time
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.encoding import decode_response_bytes
try:
    from hledac.universal.utils.source_types import SourceType
except ImportError:
    SourceType = None
from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from _core import aclose
logger = logging.getLogger(__name__)
ALT_PROTOCOLS_ENABLED: bool = os.getenv('HLEDAC_ENABLE_ALT_PROTOCOLS', '0').lower() in ('1', 'true', 'yes', 'on')

# ISSUE-P8-004: Per-protocol concurrency slots — head-of-line blocking eliminated.
# IPFS: 3 slots (CID resolution is slow 2-5s, parallel CID fetching beneficial)
# Gemini: 2 slots (caps at 10 pages internally, reasonable bound)
# Gopher: 2 slots (typically fast, bounded result sets)
# I2P: 1 slot (daemon is slow; avoid overwhelming)
# ZeroNet: 2 slots (JSON API lightweight, parallel site enumeration beneficial)
# Freenet: 1 slot (FProxy is single-threaded; avoid overwhelming)
# Fediverse: 2 slots (public timeline API rate limits apply)
# Matrix: 1 slot (room message fetching is sequential by nature)
# Total max concurrent operations: 3+2+2+1+2+1+2+1 = 14 (M1 8GB safe, all are I/O-bound)
IPFS_CONCURRENCY: int = 3
GEMINI_CONCURRENCY: int = 2
GOPHER_CONCURRENCY: int = 2
I2P_CONCURRENCY: int = 1
ZERONET_CONCURRENCY: int = 2
FREENET_CONCURRENCY: int = 1
FEDIVERSE_CONCURRENCY: int = 2
MATRIX_CONCURRENCY: int = 1

IPFS_TIMEOUT: int = 30
GOPHER_TIMEOUT: int = 15
GEMINI_TIMEOUT: int = 20
I2P_TIMEOUT: int = 30
ZERONET_TIMEOUT: int = 20
FREENET_TIMEOUT: int = 30
FEDIVERSE_TIMEOUT: int = 10
MATRIX_TIMEOUT: int = 10

# ISSUE-005: Per-network enable gates (separate from core alt protocols)
_ZERONET_ENABLED: bool = os.getenv('HLEDAC_ENABLE_ZERONET', '0').lower() in ('1', 'true', 'yes', 'on')
_FREENET_ENABLED: bool = os.getenv('HLEDAC_ENABLE_FREENET', '0').lower() in ('1', 'true', 'yes', 'on')

class AltProtocolResult(Struct, frozen=True):
    """Result from a single alt-protocol source. M1 8GB: msgspec.Struct for 5-7× faster init."""
    source_type: str
    findings_count: int
    success: bool
    error: str | None

def _get_ipfs_client():
    """Lazy import IPFS client."""
    from hledac.universal.network import ipfs_client
    return ipfs_client

def _get_gopher_transport():
    """Lazy import Gopher transport (canonical: transport/gopher_transport.py)."""
    from hledac.universal.transport.gopher_transport import get_gopher_transport
    return get_gopher_transport()

def _get_gemini_transport():
    """Lazy import Gemini transport."""
    from hledac.universal.network import gemini_transport
    return gemini_transport

def _get_i2p_client():
    """Lazy import I2P client."""
    from hledac.universal.network import i2p_client
    return i2p_client

def _get_zeronet_client():
    """Lazy import ZeroNet client (ISSUE-005)."""
    from hledac.universal.network import zeronet_client
    return zeronet_client

def _get_freenet_client():
    """Lazy import Freenet/Hyphanet client (ISSUE-005)."""
    from hledac.universal.network import freenet_client
    return freenet_client

def _get_fediverse_adapter():
    """Lazy import Fediverse adapter."""
    from hledac.universal.discovery import fediverse_adapter
    return fediverse_adapter

def _get_matrix_adapter():
    """Lazy import Matrix adapter."""
    from hledac.universal.discovery import matrix_adapter
    return matrix_adapter

async def _fetch_from_ipfs(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via IPFS.

    ISSUE-P8-005: Now supports ipfs:// and ipns:// URI schemes.
    Resolution order:
      1. Native DHT via ipfs CLI subprocess (if available)
      2. HTTP gateway fallback

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    ipfs = _get_ipfs_client()
    async with semaphore:
        try:
            async with asyncio.timeout(IPFS_TIMEOUT):
                # ISSUE-P8-005: Check if query is a direct URI
                if query.startswith('ipfs://') or query.startswith('ipns://'):
                    # Direct URI resolution via native DHT + gateway fallback
                    content = await ipfs.resolve_ipfs_uri(query)
                    if content:
                        cid = query.replace('ipfs://', '').replace('ipns://', '').split('/')[0]
                        finding = CanonicalFinding(
                            finding_id=f'ipfs-alt-{cid[:12]}-{int(time.time() * 1000)}',
                            query=query,
                            source_type=SourceType.IPFS_CONTENT,
                            confidence=0.85,  # Higher confidence for direct URI
                            ts=time.time(),
                            provenance=(query,),
                            payload_text=decode_response_bytes(content)[:4096] if isinstance(content, bytes) else str(content)[:4096]
    )
                        return ([finding], AltProtocolResult(source_type=SourceType.IPFS_CONTENT, findings_count=1, success=True, error=None))
                    return ([], AltProtocolResult(source_type=SourceType.IPFS_CONTENT, findings_count=0, success=False, error='uri_resolution_failed'))

                cids = await ipfs.find_via_ipfs_search(query)
            from hledac.universal.utils.asyncx import parallel

            async def _fetch_one_cid(cid: str) -> CanonicalFinding | None:
                try:
                    async with asyncio.timeout(IPFS_TIMEOUT):
                        content = await ipfs.fetch_ipfs(cid)
                    if not content:
                        return None
                    return CanonicalFinding(finding_id=f'ipfs-alt-{cid[:12]}-{int(time.time() * 1000)}', query=query, source_type=SourceType.IPFS_CONTENT, confidence=0.75, ts=time.time(), provenance=(f'ipfs://{cid}',), payload_text=decode_response_bytes(content)[:4096] if isinstance(content, bytes) else str(content)[:4096])
                except Exception:
                    return None
            # ISSUE-P8-004: Removed nested parallel() - semaphore handles concurrency at orchestrator level
            # This eliminates mixed concurrency model (Semaphore + parallel()) for M1 8GB optimization
            findings = []
            for cid in cids[:10]:
                finding = await _fetch_one_cid(cid)
                if finding:
                    findings.append(finding)
            return (findings, AltProtocolResult(source_type=SourceType.IPFS_CONTENT, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.IPFS_CONTENT, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'IPFS alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.IPFS_CONTENT, findings_count=0, success=False, error=str(e)))

async def _fetch_from_gopher(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Gopher protocol.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    gopher = _get_gopher_transport()
    async with semaphore:
        try:
            async with asyncio.timeout(GOPHER_TIMEOUT):
                findings = await gopher.search_as_findings(query)
            return (findings, AltProtocolResult(source_type=SourceType.GOPHER_CONTENT, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.GOPHER_CONTENT, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'Gopher alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.GOPHER_CONTENT, findings_count=0, success=False, error=str(e)))

async def _fetch_from_gemini(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Gemini protocol.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    gemini = _get_gemini_transport()
    async with semaphore:
        try:
            async with asyncio.timeout(GEMINI_TIMEOUT):
                findings = await gemini.geminispace_to_findings(query, max_pages=10)
            return (findings, AltProtocolResult(source_type=SourceType.GEMINI_CONTENT, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.GEMINI_CONTENT, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'Gemini alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.GEMINI_CONTENT, findings_count=0, success=False, error=str(e)))

async def _fetch_from_i2p(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via I2P eepsites.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    i2p = _get_i2p_client()
    async with semaphore:
        try:
            available = await i2p.is_i2p_available()
            if not available:
                return ([], AltProtocolResult(source_type=SourceType.I2P_DISCOVERY, findings_count=0, success=True, error='i2p_unavailable'))
            async with asyncio.timeout(I2P_TIMEOUT):
                findings = await i2p.i2p_to_findings(query)
            return (findings, AltProtocolResult(source_type=SourceType.I2P_DISCOVERY, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.I2P_DISCOVERY, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'I2P alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.I2P_DISCOVERY, findings_count=0, success=False, error=str(e)))

# ── ISSUE-005: ZeroNet fetch ────────────────────────────────────────────────

async def _fetch_from_zeronet(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via ZeroNet decentralized network (ISSUE-005).

    Uses ZeroNet JSON API at http://127.0.0.1:43110/ to search and
    enumerate content from ZeroNet sites (1ZeroMe..., 1Talk..., etc.).

    Gate: HLEDAC_ENABLE_ZERONET=1

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    if not _ZERONET_ENABLED:
        return ([], AltProtocolResult(source_type=SourceType.ZERONET, findings_count=0, success=True, error='zeronet_disabled'))
    zeronet = _get_zeronet_client()
    async with semaphore:
        try:
            available = await zeronet.is_zeronet_available()
            if not available:
                return ([], AltProtocolResult(source_type=SourceType.ZERONET, findings_count=0, success=True, error='zeronet_unavailable'))
            async with asyncio.timeout(ZERONET_TIMEOUT):
                findings = await zeronet.zeronet_to_findings(query)
            return (findings, AltProtocolResult(source_type=SourceType.ZERONET_CONTENT, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.ZERONET_CONTENT, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'ZeroNet alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.ZERONET_CONTENT, findings_count=0, success=False, error=str(e)))

# ── ISSUE-005: Freenet/Hyphanet fetch ────────────────────────────────────────

async def _fetch_from_freenet(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Freenet/Hyphanet decentralized network (ISSUE-005).

    Uses Freenet FProxy HTTP gateway at http://127.0.0.1:8888/ to access
    content via USK (Updatable Subspace Key), CHK (Content Hash Key),
    and SSK (Signed Subspace Key) URI schemes.

    Gate: HLEDAC_ENABLE_FREENET=1

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    if not _FREENET_ENABLED:
        return ([], AltProtocolResult(source_type=SourceType.FREENET, findings_count=0, success=True, error='freenet_disabled'))
    freenet = _get_freenet_client()
    async with semaphore:
        try:
            available = await freenet.is_freenet_available()
            if not available:
                return ([], AltProtocolResult(source_type=SourceType.FREENET, findings_count=0, success=True, error='freenet_unavailable'))
            async with asyncio.timeout(FREENET_TIMEOUT):
                findings = await freenet.freenet_to_findings(query)
            return (findings, AltProtocolResult(source_type=SourceType.FREENET_CONTENT, findings_count=len(findings), success=True, error=None))
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.FREENET_CONTENT, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'Freenet alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.FREENET_CONTENT, findings_count=0, success=False, error=str(e)))

# ── Social protocol fetches ─────────────────────────────────────────────────

async def _fetch_from_fediverse(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Fediverse/Mastodon public API.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    fediverse = _get_fediverse_adapter()
    async with semaphore:
        try:
            adapter = fediverse.FediverseAdapter()
            try:
                async with asyncio.timeout(FEDIVERSE_TIMEOUT):
                    statuses = await adapter.search_public_timeline(query, max_results=50)
                findings: list[CanonicalFinding] = []
                for status in statuses[:20]:
                    content = status.get('content', '')
                    account = status.get('account', {})
                    acct = account.get('acct', 'unknown')
                    finding = CanonicalFinding(finding_id=f"fediverse-{status.get('id', int(time.time() * 1000))}", query=query, source_type=SourceType.FEDIVERSE, confidence=0.6, ts=status.get('created_at', time.time()), provenance=(f'https://infosec.exchange/@{acct}',), payload_text=content[:4096])
                    findings.append(finding)
                return (findings, AltProtocolResult(source_type=SourceType.FEDIVERSE, findings_count=len(findings), success=True, error=None))
            finally:
                await adapter.close()
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.FEDIVERSE, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'Fediverse alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.FEDIVERSE, findings_count=0, success=False, error=str(e)))

async def _fetch_from_matrix(query: str, semaphore: asyncio.Semaphore) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Matrix public rooms API.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from hledac.universal.utils.asyncx import parallel
    matrix = _get_matrix_adapter()
    async with semaphore:
        try:
            adapter = matrix.MatrixPublicAdapter()
            try:
                async with asyncio.timeout(MATRIX_TIMEOUT):
                    rooms = await adapter.search_public_rooms(query, limit=5)
                findings: list[CanonicalFinding] = []

                async def _fetch_room_messages(room: Any) -> list[CanonicalFinding]:
                    """Fetch messages from one Matrix room."""
                    try:
                        async with asyncio.timeout(MATRIX_TIMEOUT):
                            messages = await adapter.get_room_messages(room.room_id, limit=50)
                        room_findings = []
                        for msg in messages[:10]:
                            content = msg.get('content', {}).get('body', '')
                            room_findings.append(CanonicalFinding(finding_id=f"matrix-{msg.get('event_id', int(time.time() * 1000))}", query=query, source_type=SourceType.MATRIX_PUBLIC, confidence=0.5, ts=msg.get('origin_server_ts', time.time()) / 1000, provenance=(f'https://matrix.to/#/{room.room_id}',), payload_text=content[:4096]))
                        return room_findings
                    except Exception:
                        return []
                results = await parallel([_fetch_room_messages(room) for room in rooms[:3]], policy='collect', concurrency=3, ctx='alt_protocol:matrix_fetch_rooms')
                for room_findings in results.ok:
                    findings.extend(room_findings)
                return (findings, AltProtocolResult(source_type=SourceType.MATRIX_PUBLIC, findings_count=len(findings), success=True, error=None))
            finally:
                await adapter.close()
        except TimeoutError:
            return ([], AltProtocolResult(source_type=SourceType.MATRIX_PUBLIC, findings_count=0, success=False, error='timeout'))
        except Exception as e:
            logger.debug(f'Matrix alt fetch error: {e}')
            return ([], AltProtocolResult(source_type=SourceType.MATRIX_PUBLIC, findings_count=0, success=False, error=str(e)))

# ── Orchestrator ────────────────────────────────────────────────────────────

async def fetch_all_alt_protocols(query: str, max_concurrent: int | None = None) -> tuple[list, list[AltProtocolResult]]:
    """
    Fetch content from all alternative protocols in parallel.

    ISSUE-P8-004: Replaced global semaphore bottleneck with per-protocol semaphores.
    ISSUE-005: Added ZeroNet and Freenet/Hyphanet protocol support.
    Each protocol now runs with its own concurrency limit, eliminating head-of-line
    blocking where slow protocols (IPFS) starved faster ones.

    Args:
        query: Search query string
        max_concurrent: Deprecated parameter (ignored). Kept for backwards compatibility.
                        Per-protocol concurrency is now controlled by IPFS_CONCURRENCY,
                        GEMINI_CONCURRENCY, GOPHER_CONCURRENCY, I2P_CONCURRENCY,
                        ZERONET_CONCURRENCY, FREENET_CONCURRENCY,
                        FEDIVERSE_CONCURRENCY, MATRIX_CONCURRENCY constants.

    Returns:
        (all_findings, protocol_results) — tuple of findings list and per-protocol results
    """
    if not ALT_PROTOCOLS_ENABLED:
        logger.debug('Alt protocols disabled (HLEDAC_ENABLE_ALT_PROTOCOLS != 1)')
        return ([], [])
    all_findings: list = []
    protocol_results: list[AltProtocolResult] = []
    # Per-protocol semaphores — ISSUE-P8-004: eliminates head-of-line blocking
    sem_ipfs = asyncio.Semaphore(IPFS_CONCURRENCY)
    sem_gopher = asyncio.Semaphore(GOPHER_CONCURRENCY)
    sem_gemini = asyncio.Semaphore(GEMINI_CONCURRENCY)
    sem_i2p = asyncio.Semaphore(I2P_CONCURRENCY)
    tasks = [
        _fetch_from_ipfs(query, sem_ipfs),
        _fetch_from_gopher(query, sem_gopher),
        _fetch_from_gemini(query, sem_gemini),
        _fetch_from_i2p(query, sem_i2p),
    ]
    # ISSUE-005: ZeroNet & Freenet gated behind separate env vars
    if _ZERONET_ENABLED:
        sem_zeronet = asyncio.Semaphore(ZERONET_CONCURRENCY)
        tasks.append(_fetch_from_zeronet(query, sem_zeronet))
    if _FREENET_ENABLED:
        sem_freenet = asyncio.Semaphore(FREENET_CONCURRENCY)
        tasks.append(_fetch_from_freenet(query, sem_freenet))
    if os.getenv('HLEDAC_ENABLE_SOCIAL', '').strip() == '1':
        sem_fediverse = asyncio.Semaphore(FEDIVERSE_CONCURRENCY)
        sem_matrix = asyncio.Semaphore(MATRIX_CONCURRENCY)
        tasks.append(_fetch_from_fediverse(query, sem_fediverse))
        tasks.append(_fetch_from_matrix(query, sem_matrix))
    results = await parallel_ok(*tasks, label='alternative_protocol_fetcher:parallel')
    for result in results:
        if isinstance(result, Exception):
            logger.debug(f'Alt protocol task exception: {result}')
            continue
        if not isinstance(result, tuple):
            continue
        findings, proto_result = result
        all_findings.extend(findings)
        protocol_results.append(proto_result)
    logger.info(f'Alt protocols: {len(all_findings)} findings from {sum((1 for r in protocol_results if r.success))} protocols')
    return (all_findings, protocol_results)

# ── Convenience single-protocol fetchers ────────────────────────────────────

async def fetch_fediverse_only(query: str) -> list:
    """
    Fetch only from Fediverse (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_fediverse(query, sem)
    return findings

async def fetch_matrix_only(query: str) -> list:
    """
    Fetch only from Matrix public rooms (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_matrix(query, sem)
    return findings

async def fetch_ipfs_only(query: str) -> list:
    """
    Fetch only from IPFS (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_ipfs(query, sem)
    return findings

async def fetch_gopher_only(query: str) -> list:
    """
    Fetch only from Gopherspace (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_gopher(query, sem)
    return findings

async def fetch_gemini_only(query: str) -> list:
    """
    Fetch only from Geminispace (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_gemini(query, sem)
    return findings

async def fetch_i2p_only(query: str) -> list:
    """
    Fetch only from I2P eepsites (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
    findings, _ = await _fetch_from_i2p(query, sem)
    return findings

async def fetch_zeronet_only(query: str) -> list:
    """
    Fetch only from ZeroNet (for targeted use). ISSUE-005.

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.ZERONET_FETCH)
    findings, _ = await _fetch_from_zeronet(query, sem)
    return findings

async def fetch_freenet_only(query: str) -> list:
    """
    Fetch only from Freenet/Hyphanet (for targeted use). ISSUE-005.

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.FREENET_FETCH)
    findings, _ = await _fetch_from_freenet(query, sem)
    return findings

def get_alt_protocols_status() -> dict:
    """
    Get status of all alternative protocols.

    Returns:
        Dict with protocol availability and per-protocol concurrency limits.
        ISSUE-P8-004: Replaced global max_concurrent with per-protocol concurrency.
        ISSUE-005: Added ZeroNet and Freenet/Hyphanet status entries.
    """
    return {
        'enabled': ALT_PROTOCOLS_ENABLED,
        'protocols': {
            'ipfs': {'enabled': True, 'gate': 'HLEDAC_ENABLE_ALT_PROTOCOLS', 'concurrency': IPFS_CONCURRENCY},
            'gopher': {'enabled': True, 'gate': 'HLEDAC_ENABLE_ALT_PROTOCOLS', 'concurrency': GOPHER_CONCURRENCY},
            'gemini': {'enabled': True, 'gate': 'HLEDAC_ENABLE_ALT_PROTOCOLS', 'concurrency': GEMINI_CONCURRENCY},
            'i2p': {'enabled': True, 'gate': 'HLEDAC_ENABLE_ALT_PROTOCOLS', 'requires_daemon': True, 'concurrency': I2P_CONCURRENCY},
            'zeronet': {'enabled': _ZERONET_ENABLED, 'gate': 'HLEDAC_ENABLE_ZERONET', 'requires_daemon': True, 'concurrency': ZERONET_CONCURRENCY},
            'freenet': {'enabled': _FREENET_ENABLED, 'gate': 'HLEDAC_ENABLE_FREENET', 'requires_daemon': True, 'concurrency': FREENET_CONCURRENCY},
            'fediverse': {'enabled': True, 'gate': 'HLEDAC_ENABLE_SOCIAL', 'concurrency': FEDIVERSE_CONCURRENCY},
            'matrix': {'enabled': True, 'gate': 'HLEDAC_ENABLE_SOCIAL', 'concurrency': MATRIX_CONCURRENCY},
        }
    }
