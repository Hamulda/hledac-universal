"""
DHT Discovery Adapter — Sprint F214Q / F229

BEP-5 BitTorrent DHT discovery as a discovery source.
Queries DHT network for torrent metadata matching a keyword query.

Requires: HLEDAC_ENABLE_DHT=1
Timeout: 30s max (DHT is slow)
Tier: 3 (experimental)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING

from .duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

if TYPE_CHECKING:
    from dht.kademlia_node import KademliaNode

logger = logging.getLogger(__name__)

# Gate — no-op unless explicitly enabled (expanded set matches kademlia_node.py)
_DHT_ENABLED = os.getenv("HLEDAC_ENABLE_DHT", "").lower() in ("1", "true", "yes", "on")

# Default bootstrap nodes (BitTorrent DHT router nodes)
_DHT_BOOTSTRAP_NODES: list[tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("dht.aelitis.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]


def _make_node_id() -> str:
    """Generate a random 20-byte node ID for DHT participation."""
    return hashlib.sha256(os.urandom(32)).hexdigest()[:20]


# ---------------------------------------------------------------------------
# KademliaNode lifecycle (process-global singleton)
# ---------------------------------------------------------------------------
_node_instance: KademliaNode | None = None
_node_lock = asyncio.Lock()


async def _get_dht_node() -> KademliaNode | None:
    """Lazily create and start a shared KademliaNode instance."""
    global _node_instance
    if not _DHT_ENABLED:
        return None

    if _node_instance is not None:
        return _node_instance

    async with _node_lock:
        if _node_instance is not None:
            return _node_instance

        try:
            from core.resource_governor import ResourceGovernor
            from dht.kademlia_node import KademliaNode
            from dht.local_graph import LocalGraphStore

            lgs = LocalGraphStore()
            governor = ResourceGovernor()  # type: ignore[unused-assignment]
            node_id = _make_node_id()
            node = KademliaNode(
                node_id=node_id,
                governor=governor,
                bootstrap_nodes=_DHT_BOOTSTRAP_NODES,
                local_graph_store=lgs,
            )
            await node.start()
            _node_instance = node
            logger.debug("[DHT] KademliaNode started (shared singleton)")
            return node
        except Exception as e:
            logger.debug(f"[DHT] KademliaNode start failed (non-fatal): {e}")
            return None


async def _stop_dht_node() -> None:
    """Stop the shared KademliaNode instance on shutdown."""
    global _node_instance
    if _node_instance is not None:
        try:
            await _node_instance.stop()
        except Exception as e:
            logger.debug(f"[DHT] KademliaNode stop error (ignored): {e}")
        finally:
            _node_instance = None
            logger.debug("[DHT] KademliaNode stopped")


# ---------------------------------------------------------------------------
# Query-to-infohash conversion (BEP-05 keyword search simulation)
# ---------------------------------------------------------------------------

def _query_to_infohash_candidates(query: str, max_candidates: int = 20) -> list[str]:
    """
    Generate infohash candidates from a query string.

    DHT doesn't support keyword search natively. We simulate it by:
    1. Generating candidate infohashes from query tokens
    2. Using DHT find_value to check if any routing table entry matches

    Note: This is an approximation. Real BT DHT keyword search requires
    crawling the DHT and collecting metadata from peers (BEP-9/BEP-10).
    """
    tokens = query.lower().split()
    if not tokens:
        return []

    candidates = []
    # Use query as a single candidate — sha256 → 40 hex chars = BTIH-20
    raw = query.lower().strip()
    ih = hashlib.sha256(raw.encode()).hexdigest()[:40]
    candidates.append(f"urn:btih:{ih}")

    # Also add per-token candidates (limited)
    for token in tokens[:max_candidates]:
        if len(token) >= 3:
            ih_tok = hashlib.sha256(token.encode()).hexdigest()[:40]
            candidates.append(f"urn:btih:{ih_tok}")

    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------

async def async_search_dht(
    query: str,
    max_results: int = 50,
    timeout_s: float = 30.0,
) -> DiscoveryBatchResult:
    """
    Search BitTorrent DHT network for torrents matching query.

    Returns DiscoveryBatchResult with:
    - hits: DHT peer addresses discovered for query-related infohashes
    - source_family: "dht_discovery"
    - provider_name: "dht"

    Each hit represents a peer advertising the infohash.
    Actual torrent metadata (name, files) requires BEP-9/BEP-10 extension
    and is not fetched here (too slow for discovery pass).

    Invariants:
    - HLEDAC_ENABLE_DHT=1 gate at entry (returns empty result if disabled)
    - 30s timeout max
    - fail-soft: never propagates exceptions, returns empty hits on any error
    """
    start = time.monotonic()

    if not _DHT_ENABLED:
        return DiscoveryBatchResult(
            hits=(),
            error=None,
            fallback_triggered=None,
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=time.monotonic() - start,
            error_type=None,
        )

    node = await _get_dht_node()
    if node is None:
        return DiscoveryBatchResult(
            hits=(),
            error="dht_node_unavailable",
            fallback_triggered=None,
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=time.monotonic() - start,
            error_type="node_start_failed",
        )

    try:
        candidates = _query_to_infohash_candidates(query, max_candidates=max_results)
        hits: list[DiscoveryHit] = []
        seen_peers: set[tuple[str, int]] = set()

        async with asyncio.timeout(timeout_s):
            for ih_candidate in candidates[:max_results]:
                if len(hits) >= max_results:
                    break

                # Strip urn:btih: prefix for get_peers
                info_hash = ih_candidate.replace("urn:btih:", "")

                try:
                    peers = await asyncio.wait_for(
                        node.get_peers(info_hash),
                        timeout=5.0,
                    )
                except (TimeoutError, asyncio.CancelledError):
                    continue
                except Exception:
                    continue

                # Deduplicate peers across infohashes
                for peer_ip, peer_port in peers[:20]:
                    if (peer_ip, peer_port) in seen_peers:
                        continue
                    seen_peers.add((peer_ip, peer_port))

                    # Compose a synthetic URL for the hit
                    #Torrent peer address as pseudo-URL
                    hit_url = f"bt://{peer_ip}:{peer_port}/{info_hash[:16]}"

                    hits.append(DiscoveryHit(
                        url=hit_url,
                        title=f"BT peer {peer_ip}:{peer_port}",
                        snippet=f"infohash={info_hash[:16]}… via DHT",
                        src="dht",
                        retrieved_ts=time.time(),
                        score=0.5,
                        reason="dht_peer_match",
                    ))

                    if len(hits) >= max_results:
                        break

        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=tuple(hits),
            error=None,
            fallback_triggered=None,
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=elapsed,
            error_type=None,
        )

    except TimeoutError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="dht_timeout",
            fallback_triggered=None,
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=elapsed,
            error_type="timeout",
        )
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.debug(f"[DHT] async_search_dht error (non-fatal): {e}")
        return DiscoveryBatchResult(
            hits=(),
            error=str(e),
            fallback_triggered=None,
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=elapsed,
            error_type="exception",
        )


# ---------------------------------------------------------------------------
# BEP-9 Metadata Fetcher (Sprint F229)
# ---------------------------------------------------------------------------

_METADATA_FETCHER: "TorrentMetadataFetcher | None" = None


async def _get_metadata_fetcher() -> "TorrentMetadataFetcher":
    """Lazily create shared TorrentMetadataFetcher instance."""
    global _METADATA_FETCHER
    if _METADATA_FETCHER is None:
        from dht.metadata_fetcher import TorrentMetadataFetcher
        _METADATA_FETCHER = TorrentMetadataFetcher()
    return _METADATA_FETCHER


async def async_fetch_dht_metadata(
    info_hash: str,
    max_results: int = 5,
    timeout_s: float = 30.0,
) -> dict:
    """
    Fetch torrent metadata via BEP-9 extension protocol.

    Args:
        info_hash: 40-char hex info hash (with or without urn:btih: prefix)
        max_results: Max number of peers to try
        timeout_s: Request timeout

    Returns:
        dict with source_type="dht_metadata", metadata fields, and findings
    """
    start = time.monotonic()

    if not _DHT_ENABLED:
        return {
            "source_type": "dht_metadata",
            "infohash": info_hash,
            "success": False,
            "error": "dht_disabled",
            "elapsed_s": time.monotonic() - start,
            "findings": []
        }

    # Normalize infohash
    ih_hex = info_hash.replace("urn:btih:", "").lower()
    if len(ih_hex) != 40:
        return {
            "source_type": "dht_metadata",
            "infohash": info_hash,
            "success": False,
            "error": "invalid_infohash",
            "elapsed_s": time.monotonic() - start,
            "findings": []
        }

    node = await _get_dht_node()
    if node is None:
        return {
            "source_type": "dht_metadata",
            "infohash": info_hash,
            "success": False,
            "error": "dht_node_unavailable",
            "elapsed_s": time.monotonic() - start,
            "findings": []
        }

    try:
        # Get peers from BEP-5
        ih_bytes = bytes.fromhex(ih_hex)
        peers = await asyncio.wait_for(
            node.get_peers(ih_hex),
            timeout=min(10.0, timeout_s)
        )

        if not peers:
            return {
                "source_type": "dht_metadata",
                "infohash": info_hash,
                "success": False,
                "error": "no_peers_found",
                "elapsed_s": time.monotonic() - start,
                "findings": []
            }

        # Fetch metadata via BEP-9
        fetcher = await _get_metadata_fetcher()
        info = await asyncio.wait_for(
            fetcher.fetch_metadata(ih_bytes, peers[:max_results], timeout=timeout_s),
            timeout=timeout_s
        )

        if not info:
            return {
                "source_type": "dht_metadata",
                "infohash": info_hash,
                "success": False,
                "error": "metadata_fetch_failed",
                "elapsed_s": time.monotonic() - start,
                "findings": []
            }

        # Extract OSINT findings
        findings = fetcher.extract_intel_from_torrent(info, ih_hex)

        return {
            "source_type": "dht_metadata",
            "infohash": ih_hex,
            "success": True,
            "name": info.name,
            "total_size": info.total_size,
            "file_count": len(info.files),
            "trackers": info.trackers,
            "elapsed_s": time.monotonic() - start,
            "findings": findings
        }

    except TimeoutError:
        return {
            "source_type": "dht_metadata",
            "infohash": info_hash,
            "success": False,
            "error": "timeout",
            "elapsed_s": time.monotonic() - start,
            "findings": []
        }
    except Exception as e:
        logger.debug(f"[DHT] async_fetch_dht_metadata error (non-fatal): {e}")
        return {
            "source_type": "dht_metadata",
            "infohash": info_hash,
            "success": False,
            "error": str(e),
            "elapsed_s": time.monotonic() - start,
            "findings": []
        }
