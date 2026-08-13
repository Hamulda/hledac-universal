"""
Torrent Metadata Harvester — DHT Keyword Hit -> Metadata -> IOC Pipeline.

ISSUE-006: Connects DHT keyword crawl (crawl_dht_for_keyword) to
TorrentMetadataFetcher (BEP-9) to extract IOCs from discovered
torrent metadata (file names, tracker URLs, creator comments).

Pipeline:
  1. DHT keyword crawl discovers info_hashes
  2. TorrentMetadataFetcher downloads metadata via BEP-9/BEP-10
  3. IOC extraction from metadata fields (Rust SIMD when available)
  4. Findings tagged with source_type="dht_metadata" / "dht_ioc"
  5. Bounded: max 100 concurrent metadata fetches, 15s per-hash timeout

Architecture (M1 8GB-safe):
  - Semaphore: TorrentMetadataFetcher.MAX_CONCURRENT_FETCHES (5)
  - Per-hash timeout: 15s
  - Hard cap: 100 info_hashes per harvest call
  - Fail-soft: individual metadata fetch failures don't block others
  - Gate: HLEDAC_ENABLE_DHT_METADATA_HARVEST=1
  - RAM governor: skips if UMA state is critical/emergency
  - TTLCache: 24h metadata cache to avoid re-fetching
"""

import asyncio
import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING
from collections import OrderedDict
from cachetools import TTLCache

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.source_types import SourceType

if TYPE_CHECKING:
    from hledac.universal.dht.metadata_fetcher import (
        TorrentInfo,
        TorrentMetadataFetcher,
    )

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_INFO_HASHES_PER_HARVEST: int = 100  # Hard cap — M1 8GB memory ceiling
METADATA_FETCH_TIMEOUT: float = 15.0  # Per-hash BEP-9 fetch timeout
IOC_EXTRACTION_MAX_TEXT: int = 65536  # Max payload text for IOC extraction (64KB)
MAX_COMPOUND_FINDINGS: int = 500  # Hard cap on total findings per harvest
METADATA_CACHE_MAXSIZE: int = 1000
METADATA_CACHE_TTL: int = 86400  # 24h

# Gate: renamed from HLEDAC_ENABLE_DHT_LEAK_HARVEST for clarity
_DHT_HARVEST_ENABLED: bool = os.getenv(
    "HLEDAC_ENABLE_DHT_METADATA_HARVEST", "0"
).lower() in ("1", "true", "yes", "on")

# Shared metadata cache — survives across harvest calls
_metadata_cache: TTLCache = TTLCache(maxsize=METADATA_CACHE_MAXSIZE, ttl=METADATA_CACHE_TTL)


async def harvest_torrent_metadata(
    info_hashes: list[str],
    keyword: str = "",
    max_concurrent: int = 5,
) -> list[CanonicalFinding]:
    """Harvest torrent metadata from discovered info_hashes and extract IOCs.

    Connects the DHT keyword crawl pipeline to the metadata fetcher:
      DHT crawl -> info_hashes -> BEP-9 metadata fetch -> IOC extraction -> findings

    Args:
        info_hashes: List of 40-char hex info_hash strings discovered via DHT
        keyword: Original search keyword (for finding tagging)
        max_concurrent: Max concurrent metadata fetches (default 5)

    Returns:
        List of CanonicalFinding with source_type="dht_metadata" or "dht_ioc".
        Returns empty list if HLEDAC_ENABLE_DHT_METADATA_HARVEST is not "1".
    """
    if not _DHT_HARVEST_ENABLED:
        return []

    # Memory governor check
    try:
        from hledac.universal.core.protocols import get_governor
        governor = get_governor()
        decision = await governor.evaluate()
        if decision.uma_state in ("critical", "emergency"):
            logger.debug(
                "Torrent harvest skipped: memory %s", decision.uma_state,
            )
            return []
    except Exception:  # noqa: BLE001
        pass

    if not info_hashes:
        return []

    # Deduplicate and cap
    unique_hashes = list(dict.fromkeys(info_hashes))[:MAX_INFO_HASHES_PER_HARVEST]
    logger.info(
        "Torrent harvest: %d unique info_hashes (capped from %d)",
        len(unique_hashes), len(info_hashes),
    )

    # Lazy import to avoid circular deps at module level
    from hledac.universal.dht.metadata_fetcher import TorrentMetadataFetcher

    fetcher = TorrentMetadataFetcher()
    all_findings: list[CanonicalFinding] = []

    async def _harvest_one(info_hash_hex: str) -> list[CanonicalFinding]:
        """Fetch metadata for one info_hash and extract findings."""
        ih_key = info_hash_hex.strip()

        # Check cache first
        if ih_key in _metadata_cache:
            cached = _metadata_cache[ih_key]
            if cached is not None:
                return cached.get("findings", []) if isinstance(cached, dict) else []
            return []

        try:
            ih_bytes = bytes.fromhex(info_hash_hex)
            if len(ih_bytes) != 20:
                ih_bytes = ih_bytes[:20].ljust(20, b"\x00")
        except ValueError:
            logger.debug("Invalid info_hash hex: %s", info_hash_hex[:20])
            _metadata_cache[info_hash_hex] = None
            return []

        # For metadata fetch we need peers — use DHT get_peers first
        peers = await _get_peers_for_hash(info_hash_hex)
        if not peers:
            # Use known bootstrap peers as fallback
            peers = _fallback_peers()

        try:
            async with asyncio.timeout(METADATA_FETCH_TIMEOUT):
                metadata = await fetcher.fetch_metadata(ih_bytes, peers)
        except asyncio.TimeoutError:
            logger.debug("Metadata fetch timeout: %s", info_hash_hex[:12])
            _metadata_cache[info_hash_hex] = None
            return []
        except Exception as e:
            logger.debug("Metadata fetch error %s: %s", info_hash_hex[:12], e)
            _metadata_cache[info_hash_hex] = None
            return []

        if metadata is None:
            _metadata_cache[info_hash_hex] = None
            return []

        result_findings = _metadata_to_findings(
            metadata, info_hash_hex, keyword,
        )
        _metadata_cache[info_hash_hex] = {
            "findings": result_findings,
            "ts": time.time(),
        }
        return result_findings

    # Parallel harvest with concurrency cap
    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded_harvest(ih: str) -> list[CanonicalFinding]:
        async with sem:
            return await _harvest_one(ih)

    tasks = [_bounded_harvest(ih) for ih in unique_hashes]
    results = await parallel_ok(*tasks, label="torrent_harvester:_harvest_all")

    for result in results:
        if isinstance(result, list):
            all_findings.extend(result)

    # Hard cap on total findings
    if len(all_findings) > MAX_COMPOUND_FINDINGS:
        logger.warning(
            "Torrent harvest capped: %d findings -> %d (max)",
            len(all_findings), MAX_COMPOUND_FINDINGS,
        )
        all_findings = all_findings[:MAX_COMPOUND_FINDINGS]

    logger.info(
        "Torrent harvest complete: %d findings from %d info_hashes",
        len(all_findings), len(unique_hashes),
    )
    return all_findings


async def _get_peers_for_hash(info_hash_hex: str) -> list[tuple[str, int]]:
    """Get peers for an info_hash via DHT get_peers.

    Uses existing KademliaNode infrastructure if available.

    Args:
        info_hash_hex: 40-char hex info_hash string

    Returns:
        List of (ip, port) tuples, empty on failure.
    """
    try:
        from hledac.universal.dht.kademlia_node import (
            BOOTSTRAP_PEERS,
            KademliaNode,
            DHT_REAL_UDP,
        )
        from hledac.universal.core.resource_governor import ResourceGovernor

        if not DHT_REAL_UDP:
            return []

        governor = ResourceGovernor()
        node = KademliaNode(
            node_id=f"harvest-{info_hash_hex[:8]}",
            governor=governor,
            bootstrap_nodes=BOOTSTRAP_PEERS,
        )
        try:
            await node.start_udp()
            if node._bep5_protocol is not None:
                peers = await node.get_peers(info_hash_hex)
                return peers[:10]
        finally:
            await node.stop()
    except Exception as e:
        logger.debug("get_peers_for_hash failed for %s: %s", info_hash_hex[:12], e)

    return []


def _fallback_peers() -> list[tuple[str, int]]:
    """Return known DHT bootstrap peers as fallback for metadata fetch."""
    return [
        ("router.bittorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
        ("router.utorrent.com", 6881),
    ]


def _metadata_to_findings(
    metadata: "TorrentInfo",
    info_hash_hex: str,
    keyword: str,
) -> list[CanonicalFinding]:
    """Convert TorrentInfo metadata to CanonicalFinding list with IOC extraction.

    Extracts:
      - File names (paths) from torrent file list
      - Tracker URLs from announce/announce-list
      - Creator / comment metadata
      - Total size info

    Args:
        metadata: TorrentInfo from TorrentMetadataFetcher
        info_hash_hex: 40-char hex info_hash
        keyword: Original search keyword

    Returns:
        List of CanonicalFinding.
    """
    findings: list[CanonicalFinding] = []
    ts = time.time()

    # ── Metadata finding (summary) ─────────────────────────────────────────
    metadata_finding = CanonicalFinding(
        finding_id=f"dht-meta-{info_hash_hex[:16]}-{int(ts * 1000)}",
        query=keyword,
        source_type=SourceType.DHT_METADATA,
        confidence=0.75,
        ts=ts,
        provenance=(f"btih:{info_hash_hex}",),
        payload_text=_build_metadata_payload(metadata, info_hash_hex),
    )
    findings.append(metadata_finding)

    # ── IOC extraction from file names ─────────────────────────────────────
    file_names_text = " ".join(
        f.get("path", "") for f in metadata.files if f.get("path")
    )
    if file_names_text:
        ioc_findings = _extract_iocs_from_text(
            file_names_text, info_hash_hex, keyword, ts, context="file_names",
        )
        findings.extend(ioc_findings)

    # ── IOC extraction from tracker URLs ───────────────────────────────────
    tracker_text = " ".join(metadata.trackers)
    if tracker_text:
        ioc_findings = _extract_iocs_from_text(
            tracker_text, info_hash_hex, keyword, ts, context="trackers",
        )
        findings.extend(ioc_findings)

    # ── IOC extraction from comment/creator ────────────────────────────────
    comment_text = " ".join(
        part for part in (metadata.comment, metadata.created_by) if part
    )
    if comment_text:
        ioc_findings = _extract_iocs_from_text(
            comment_text, info_hash_hex, keyword, ts, context="comment",
        )
        findings.extend(ioc_findings)

    return findings


def _build_metadata_payload(metadata: "TorrentInfo", info_hash_hex: str) -> str:
    """Build a compact payload text from torrent metadata.

    Args:
        metadata: TorrentInfo struct
        info_hash_hex: 40-char hex info_hash

    Returns:
        Truncated payload string (max 4096 chars).
    """
    parts: list[str] = [
        f"Name: {metadata.name}",
        f"InfoHash: {info_hash_hex}",
        f"TotalSize: {metadata.total_size} bytes "
        f"({_format_size(metadata.total_size)})",
        f"Files: {len(metadata.files)}",
        f"PieceLength: {metadata.piece_length}",
    ]

    # File listing (first 20 files)
    for f in metadata.files[:20]:
        path = f.get("path", "unknown")
        length = f.get("length", 0)
        parts.append(f"  {path} ({_format_size(length)})")

    # Trackers (first 10)
    if metadata.trackers:
        parts.append("Trackers:")
        for tracker in metadata.trackers[:10]:
            parts.append(f"  {tracker}")

    if metadata.comment:
        parts.append(f"Comment: {metadata.comment[:200]}")
    if metadata.created_by:
        parts.append(f"CreatedBy: {metadata.created_by[:100]}")

    payload = "\n".join(parts)
    return payload[:4096]


def _extract_iocs_from_text(
    text: str,
    info_hash_hex: str,
    keyword: str,
    ts: float,
    context: str = "",
) -> list[CanonicalFinding]:
    """Extract IOCs from text and return as CanonicalFinding list.

    Uses Rust SIMD extractor when available (fastest on M1),
    falls back to pure-Python combined regex.

    Args:
        text: Text to extract IOCs from
        info_hash_hex: Associated info_hash
        keyword: Original search keyword
        ts: Timestamp for findings
        context: Context label (file_names, trackers, comment)

    Returns:
        List of CanonicalFinding with source_type="dht_ioc".
    """
    findings: list[CanonicalFinding] = []
    truncated = text[:IOC_EXTRACTION_MAX_TEXT]

    iocs = _call_ioc_extractor(truncated)
    if not iocs:
        return findings

    seen: set[str] = set()
    for ioc_value, ioc_type in iocs:
        dedup_key = f"{ioc_type}:{ioc_value}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        finding_id = f"dht-ioc-{info_hash_hex[:12]}-{ioc_type}-{hash(dedup_key) & 0xFFFF:04x}"
        finding = CanonicalFinding(
            finding_id=finding_id,
            query=keyword,
            source_type=SourceType.DHT_METADATA,
            confidence=0.7,
            ts=ts,
            provenance=(f"btih:{info_hash_hex}", f"context:{context}"),
            payload_text=f"IOC: {ioc_value} (type: {ioc_type})\nInfoHash: {info_hash_hex}\nContext: {context}",
        )
        findings.append(finding)

    return findings


def _call_ioc_extractor(text: str) -> list[tuple[str, str]]:
    """Extract IOCs from text — Rust SIMD preferred, Python fallback.

    Returns:
        List of (ioc_value, ioc_type) tuples.
    """
    # Try Rust SIMD extractor (fastest on M1)
    try:
        from hledac.universal.core.rust_backend.ioc import (
            _python_extract_iocs_simd_single,
        )
        return _python_extract_iocs_simd_single(text)
    except Exception:  # noqa: BLE001
        pass

    # Fallback: forensics/ioc_extractor combined regex
    try:
        from forensics.ioc_extractor import _IOC_COMBINED
        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in _IOC_COMBINED.finditer(text):
            name = m.lastgroup
            if name is None:
                continue
            value = m.group()
            key = f"{name}:{value}"
            if key in seen:
                continue
            seen.add(key)
            if name.startswith("ipv6"):
                results.append((value.lower(), "ipv6"))
            elif name == "ipv4":
                results.append((value, "ipv4"))
            elif name in ("md5", "sha1", "sha256"):
                results.append((value.lower(), name))
            elif name == "email":
                results.append((value.lower(), name))
            else:
                results.append((value, name))
        return results
    except Exception:  # noqa: BLE001
        pass

    return []


def _format_size(size_bytes: int | float) -> str:
    """Format bytes to human readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ── Pipeline integration helpers ───────────────────────────────────────────────


async def harvest_from_dht_crawl_results(
    crawl_results: list[dict],
    keyword: str,
    max_concurrent: int = 5,
) -> list[CanonicalFinding]:
    """Harvest metadata from DHT crawl results (crawl_dht_for_keyword output).

    Takes the output of crawl_dht_for_keyword() or KademliaNode.crawl()
    and extracts info_hashes for metadata harvesting.

    Args:
        crawl_results: List of dicts from DHT crawl (each has 'info_hash' key)
        keyword: Original search keyword
        max_concurrent: Max concurrent metadata fetches

    Returns:
        List of CanonicalFinding with source_type="dht_metadata" or "dht_ioc".
    """
    info_hashes = collect_info_hashes_from_crawl_results(crawl_results)
    if not info_hashes:
        return []

    return await harvest_torrent_metadata(info_hashes, keyword, max_concurrent)


def collect_info_hashes_from_crawl_results(
    crawl_results: list[dict],
) -> list[str]:
    """Extract unique info_hash strings from DHT crawl results (ISSUE-006).

    Accepts results from crawl_dht_for_keyword() in kademlia_node.py
    and returns deduplicated info_hash hex strings ready for harvesting.

    Args:
        crawl_results: List of dicts with 'info_hash' key from crawl_dht_for_keyword()

    Returns:
        List of unique 40-char hex info_hash strings.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in crawl_results:
        ih = item.get("info_hash", "") if isinstance(item, dict) else ""
        if not ih:
            continue
        # Strip urn:btih: prefix if present
        if ih.startswith("urn:btih:"):
            ih = ih[len("urn:btih:"):]
        ih = ih.strip().lower()
        if ih and ih not in seen and len(ih) == 40:
            seen.add(ih)
            result.append(ih)
    return result


def get_harvester_status() -> dict:
    """Get harvester status for monitoring (ISSUE-006)."""
    return {
        'enabled': _DHT_HARVEST_ENABLED,
        'gate': 'HLEDAC_ENABLE_DHT_METADATA_HARVEST',
        'max_concurrent_fetches': 5,  # from TorrentMetadataFetcher
        'max_info_hashes': MAX_INFO_HASHES_PER_HARVEST,
        'cache_size': len(_metadata_cache),
        'cache_maxsize': METADATA_CACHE_MAXSIZE,
        'cache_ttl_s': METADATA_CACHE_TTL,
    }
