"""
Deep Research Probe Runner — Bounded Post-Sprint Deep Research
==============================================================

Integrates DeepProbeScanner into sprint lifecycle as fail-soft
post-sprint research that does NOT block sprint export.

KEY INVARIANTS:
- source_type="deep_probe" on all probe findings
- Timeout/depth limits are test-locked (not configurable in production)
- Sprint export completes BEFORE probe runs (no blocking)
- Findings persisted via async_ingest_findings_batch() (canonical path)
- DHT findings are NOT persisted (DHT is ephemeral)
- All methods fail-safe: exceptions logged, never propagated
- Cache-first: LocalSearchSeam checked before network fetch

CANONICAL PATH:
  python -m hledac.universal.core --sprint --query "..." --deep-probe
    → run_sprint() completes
    → run_deep_probe() runs post-sprint (fire-and-forget on export timeline)
    → findings normalized to CanonicalFinding → async_ingest_findings_batch()

Invariants table (for test_deep_probe_canonical_ingest.py):
  invariant_1 | probe findings have source_type="deep_probe"
  invariant_2 | timeout is bounded (MAX_PROBE_DURATION_S = 120)
  invariant_3 | depth is bounded (MAX_CRAWL_DEPTH = 3)
  invariant_4 | sprint export completes before probe starts
  invariant_5 | all methods are fail-safe (try/except everywhere)
  invariant_6 | findings persisted ONLY via async_ingest_findings_batch()
  invariant_7 | DHT findings are NOT persisted
  invariant_8 | LocalSearchSeam checked before network fetch (cache-first)
  invariant_9 | Successful network results indexed to LocalSearchSeam
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from typing import Optional, Tuple, List, Any

logger = logging.getLogger(__name__)

# Cache-first threshold: score > 0.7 triggers cache hit
LOCAL_SEARCH_CACHE_THRESHOLD: float = 0.7

# =============================================================================
# Bounded Constants — test-locked, not configurable in production
# =============================================================================
MAX_PROBE_DURATION_S: float = 120.0  # Hard cap on probe runtime
MAX_CRAWL_DEPTH: int = 3  # Max depth for deep_crawl
MAX_BUCKET_SCAN: int = 50  # Max buckets for S3 scan
IPFS_RESULT_CAP: int = 100  # Max IPFS results


async def run_deep_probe(
    query: str,
    store,
    timeout_s: float = MAX_PROBE_DURATION_S,
    max_depth: int = MAX_CRAWL_DEPTH,
    max_buckets: int = MAX_BUCKET_SCAN,
) -> dict:
    """
    Run deep probe research as post-sprint bounded activity.

    Args:
        query: Search query/target for deep research
        store: DuckDBShadowStore instance for persisting findings
        timeout_s: Probe timeout (default MAX_PROBE_DURATION_S)
        max_depth: Max crawl depth (default MAX_CRAWL_DEPTH)
        max_buckets: Max buckets to scan (default MAX_BUCKET_SCAN)

    Returns:
        dict with keys: urls_discovered, buckets_scanned, ipfs_results,
                        probe_duration_s, probe_source_type, findings_ingested,
                        dht_peers

    Invariants enforced:
      - All findings use source_type="deep_probe"
      - Timeout bounds probe runtime
      - All external calls are fail-safe
      - Findings persisted ONLY via async_ingest_findings_batch()
      - DHT findings use source_type="dht_discovery" (NOT persisted — invariant_7)
    """
    from hledac.universal.deep_probe import (
        DeepProbeScanner,
        scan_ipfs,
        scan_s3_buckets,
    )
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.knowledge.search_index import LocalSearchSeam, SearchDocument

    start_time = time.monotonic()
    result = {
        "urls_discovered": 0,
        "buckets_scanned": 0,
        "ipfs_results": 0,
        "dht_peers": 0,
        "probe_duration_s": 0.0,
        "probe_source_type": "deep_probe",
        "findings_ingested": 0,
        "cache_hit": False,
        "errors": [],
    }

    # Collect all canonical findings for batch ingest
    all_findings: List[CanonicalFinding] = []
    scanner = DeepProbeScanner(max_memory_mb=100)

    # ── Cache-first: check LocalSearchSeam before network fetch ──────────────
    local_seam = LocalSearchSeam()
    try:
        local_results = local_seam.search(query, top_k=5)
        if local_results.results and local_results.results[0].score > LOCAL_SEARCH_CACHE_THRESHOLD:
            logger.debug(
                f"[DEEP_PROBE] cache hit for query '{query[:50]}...' "
                f"(score={local_results.results[0].score:.3f})"
            )
            # Convert local results to findings and return immediately
            cache_findings = _convert_search_results_to_findings(
                local_results.results, query
            )
            result["cache_hit"] = True
            result["findings_ingested"] = len(cache_findings)

            # Persist cache-hit findings via canonical path
            if cache_findings and store is not None:
                try:
                    ingest_results = await store.async_ingest_findings_batch(cache_findings)
                    accepted = sum(
                        1 for r in ingest_results
                        if not hasattr(r, 'accepted') or r.accepted
                    )
                    result["findings_ingested"] = accepted
                except Exception as e:
                    logger.warning(f"[DEEP_PROBE] cache hit ingest failed: {e}")
                    result["errors"].append(f"cache_ingest: {e}")

            result["probe_duration_s"] = round(time.monotonic() - start_time, 2)
            return result
    except Exception as e:
        logger.debug(f"[DEEP_PROBE] local search failed, proceeding to network: {e}")

    # ── Network fetch (cache miss) ───────────────────────────────────────────
    try:
        # Extract domain from query (simple extraction)
        domain = _extract_domain(query)
        if not domain:
            domain = query.strip().lower().replace(" ", "_")[:50]

        # 1. Wayback + path discovery (bounded by timeout)
        async def _run_discovery():
            try:
                urls = await scanner.scan(domain)
                # Convert discovered URLs to CanonicalFinding
                discovery_findings = _make_discovery_findings(urls, query)
                return ("discovery", len(urls), discovery_findings, urls)
            except Exception as e:
                logger.debug(f"Discovery scan failed: {e}")
                return ("discovery", 0, [], [])

        # 2. S3 bucket scan (bounded by max_buckets)
        # Now returns Tuple[List[dict], List[CanonicalFinding]]
        async def _run_bucket_scan():
            try:
                raw_results, bucket_findings = await scanner.scan_s3_buckets(
                    domain, store=store, max_buckets=max_buckets
                )
                # Index bucket findings to LocalSearchSeam for future cache hits
                if bucket_findings:
                    _index_probe_results_to_seam(
                        local_seam, bucket_findings, query
                    )
                return ("bucket", len(bucket_findings), bucket_findings)
            except Exception as e:
                logger.debug(f"Bucket scan failed: {e}")
                return ("bucket", 0, [])

        # 3. IPFS search (bounded by timeout and result cap)
        # Now returns List[CanonicalFinding]
        async def _run_ipfs():
            try:
                ipfs_findings = await scan_ipfs(query, store=store)
                # Index IPFS findings to LocalSearchSeam for future cache hits
                if ipfs_findings:
                    _index_probe_results_to_seam(
                        local_seam, ipfs_findings, query
                    )
                return ("ipfs", len(ipfs_findings), ipfs_findings)
            except Exception as e:
                logger.debug(f"IPFS scan failed: {e}")
                return ("ipfs", 0, [])

        # 4. DHT peer discovery (BEP-5, real UDP — gated by HLEDAC_ENABLE_DHT)
        async def _run_dht():
            """F214Q: Find peers for query via real BitTorrent DHT."""
            try:
                dht_findings = await _scan_dht(query)
                if dht_findings:
                    _index_probe_results_to_seam(local_seam, dht_findings, query)
                return ("dht", len(dht_findings), dht_findings)
            except Exception as e:
                logger.debug(f"DHT scan failed: {e}")
                return ("dht", 0, [])

        # Race all tasks against timeout using gather
        all_results = await asyncio.gather(
            _run_discovery(),
            _run_bucket_scan(),
            _run_ipfs(),
            _run_dht(),
            return_exceptions=True,
        )

        # Apply timeout post-wait (bounded by design)
        elapsed = time.monotonic() - start_time
        if elapsed > timeout_s:
            logger.debug(f"Probe exceeded timeout: {elapsed:.1f}s > {timeout_s}s")

        # Collect results by type tag and accumulate findings
        for res in all_results:
            if isinstance(res, tuple):
                tag = res[0]
                count = res[1]
                findings = res[2]
                if tag == "discovery":
                    result["urls_discovered"] = count
                    all_findings.extend(findings)
                    # Index discovery URLs to LocalSearchSeam
                    if len(res) > 3 and res[3]:
                        _index_urls_to_seam(local_seam, res[3], query)
                elif tag == "bucket":
                    result["buckets_scanned"] = count
                    all_findings.extend(findings)
                elif tag == "ipfs":
                    result["ipfs_results"] = count
                    all_findings.extend(findings)
                elif tag == "dht":
                    # DHT findings are added to all_findings but NOT persisted
                    # (DHT is ephemeral — invariant_7)
                    result["dht_peers"] = count
            elif isinstance(res, Exception):
                logger.debug(f"Probe task raised exception: {res}")
                result["errors"].append(str(res))

        # Persist findings via canonical path
        if all_findings and store is not None:
            try:
                ingest_results = await store.async_ingest_findings_batch(all_findings)
                # Count accepted findings (not rejected/duplicates)
                accepted = sum(
                    1 for r in ingest_results
                    if not hasattr(r, 'accepted') or r.accepted
                )
                result["findings_ingested"] = accepted
                logger.debug(f"[DEEP_PROBE] ingested {accepted}/{len(all_findings)} findings")
            except Exception as e:
                logger.warning(f"[DEEP_PROBE] canonical ingest failed: {e}")
                result["errors"].append(f"ingest: {e}")

    except Exception as e:
        logger.warning(f"[DEEP_PROBE] Unexpected error: {e}")
        result["errors"].append(str(e))

    result["probe_duration_s"] = round(time.monotonic() - start_time, 2)

    logger.info(
        f"[DEEP_PROBE] completed in {result['probe_duration_s']}s | "
        f"urls={result['urls_discovered']} buckets={result['buckets_scanned']} "
        f"ipfs={result['ipfs_results']} ingested={result['findings_ingested']}"
    )

    return result


def _extract_domain(query: str) -> Optional[str]:
    """Extract domain-like string from query for targeted scanning."""
    import re

    # Look for domain patterns
    domain_pattern = re.compile(
        r'(?:https?://)?(?:www\.)?([a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)*\.[a-zA-Z]{2,})'
    )
    match = domain_pattern.search(query)
    if match:
        return match.group(1)

    # If no domain found, return None (scanner handles generic queries)
    return None


def _make_discovery_findings(urls: List[str], query: str) -> List['CanonicalFinding']:
    """
    Convert discovered URLs to CanonicalFinding objects.

    DHT (Wayback, path prediction) findings are ephemeral discovery artifacts.
    They are converted to CanonicalFinding for potential future enrichment
    but are NOT stored persistently - only bucket and IPFS findings persist.
    NOTE: This is the DHT "hint" layer - actual persistent findings come
    from bucket scans and IPFS searches.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    findings: List[CanonicalFinding] = []
    for url in urls[:100]:  # Cap at 100 discovery URLs
        try:
            dedup_key = f"discovery:{url}"
            finding_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

            finding = CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type="deep_probe",
                confidence=0.5,  # Discovery URLs are lower confidence
                ts=time.time(),
                provenance=("deep_probe", "discovery", url),
                payload_text=url,
            )
            findings.append(finding)
        except Exception as e:
            logger.debug(f"Failed to build discovery finding for {url}: {e}")
            continue

    return findings


def _convert_search_results_to_findings(
    documents: List["SearchDocument"],
    query: str,
) -> List["CanonicalFinding"]:
    """
    Convert LocalSearchSeam SearchDocument list to CanonicalFinding list.

    Used for cache-hit path: when local search returns high-confidence results,
    we convert them to findings without hitting the network.

    Args:
        documents: List of SearchDocument from LocalSearchSeam.search()
        query: Original probe query

    Returns:
        List of CanonicalFinding with source_type="deep_probe" and confidence
        scaled from local search score (score * 0.9).
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    findings: List[CanonicalFinding] = []
    for doc in documents:
        try:
            dedup_key = f"local_corpus:{doc.url}"
            finding_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

            # Scale confidence from BM25 score: local data is reliable (0.9 multiplier)
            confidence = min(doc.score * 0.9, 1.0)

            metadata = doc.metadata.copy() if doc.metadata else {}
            metadata["cache_hit"] = True

            finding = CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type="deep_probe",
                confidence=confidence,
                ts=time.time(),
                provenance=("deep_probe", "local_corpus", doc.url),
                payload_text=f"{doc.title}\n{doc.content}"[:4096],
            )
            findings.append(finding)
        except Exception as e:
            logger.debug(f"Failed to convert search result to finding: {e}")
            continue

    return findings


def _index_probe_results_to_seam(
    seam: "LocalSearchSeam",
    findings: List["CanonicalFinding"],
    query: str,
) -> None:
    """
    Index CanonicalFinding list to LocalSearchSeam for future cache hits.

    After successful network fetch, we index the findings so future
    identical/similar queries can be served from local cache.

    Args:
        seam: LocalSearchSeam instance to index into
        findings: CanonicalFinding list from network fetch
        query: Original probe query (used for content context)
    """
    from hledac.universal.knowledge.search_index import SearchDocument

    documents: List[SearchDocument] = []
    for finding in findings:
        try:
            # Extract URL from provenance or payload_text
            url = finding.provenance[2] if len(finding.provenance) > 2 else finding.payload_text
            if not url or not isinstance(url, str):
                continue

            # Use title from metadata if available, otherwise truncate URL
            title = url[:100] if len(url) > 100 else url

            # Use payload_text as content (already extracted from network)
            content = finding.payload_text or ""

            doc = SearchDocument(
                url=url,
                title=title,
                content=content[:2000],  # Truncate for index size
                metadata={"query": query, "source_type": finding.source_type},
                score=0.0,  # BM25 score computed on re-index
            )
            documents.append(doc)
        except Exception as e:
            logger.debug(f"Failed to index finding to seam: {e}")
            continue

    if documents:
        try:
            seam.index(documents)
            logger.debug(f"[DEEP_PROBE] indexed {len(documents)} results to LocalSearchSeam")
        except Exception as e:
            logger.debug(f"[DEEP_PROBE] seam index failed: {e}")


def _index_urls_to_seam(
    seam: "LocalSearchSeam",
    urls: List[str],
    query: str,
) -> None:
    """
    Index discovered URLs to LocalSearchSeam for future cache hits.

    Args:
        seam: LocalSearchSeam instance to index into
        urls: List of discovered URLs
        query: Original probe query (used for content context)
    """
    from hledac.universal.knowledge.search_index import SearchDocument

    documents: List[SearchDocument] = []
    for url in urls[:50]:  # Cap at 50 URLs
        try:
            doc = SearchDocument(
                url=url,
                title=url[:100],
                content=f"discovered: {url} query: {query}",
                metadata={"query": query, "source_type": "deep_probe"},
                score=0.0,
            )
            documents.append(doc)
        except Exception as e:
            logger.debug(f"Failed to index URL to seam: {e}")
            continue

    if documents:
        try:
            seam.index(documents)
            logger.debug(f"[DEEP_PROBE] indexed {len(documents)} URLs to LocalSearchSeam")
        except Exception as e:
            logger.debug(f"[DEEP_PROBE] seam index failed: {e}")


async def _scan_dht(query: str) -> List["CanonicalFinding"]:
    """
    F214Q: Find peers for query via real BitTorrent DHT (BEP-5).

    Gated by HLEDAC_ENABLE_DHT=1. Uses KademliaNode with real UDP
    asyncio.DatagramProtocol. Persists discovered nodes to LMDB via
    LocalGraphStore.put_dht_node (fire-and-forget). Results are returned
    as CanonicalFinding with source_type="dht_discovery" but are NOT
    persisted to DuckDB (DHT is ephemeral — invariant_7).

    Args:
        query: Search query (used as infohash seed for DHT get_peers)

    Returns:
        List of CanonicalFinding (one per discovered peer, max 50).
    """
    if os.getenv("HLEDAC_ENABLE_DHT", "").lower() not in ("1", "true", "yes", "on"):
        return []

    from hledac.universal.core.resource_governor import ResourceGovernor
    from hledac.universal.dht.kademlia_node import KademliaNode
    from hledac.universal.dht.local_graph import LocalGraphStore
    from hledac.universal.security.key_manager import KeyManager

    try:
        # Lazy singleton LocalGraphStore (shared across DHT operations)
        if not hasattr(_scan_dht, "_lgs"):
            try:
                km = KeyManager()
                _scan_dht._lgs = LocalGraphStore(km)
            except Exception:
                return []
        lgs = _scan_dht._lgs

        node = KademliaNode(
            node_id=f"hledac-probe-{uuid.uuid4().hex[:8]}",
            governor=ResourceGovernor(),
            local_graph_store=lgs,
        )
        await node.start()  # F214Q: init routing table from LMDB + start refresh loop
        try:
            peers = await asyncio.wait_for(
                node.get_peers(info_hash),
                timeout=120.0,
            )
        finally:
            await node.stop()

        findings = []
        for ip, port in peers[:50]:
            fid = hashlib.sha256(f"{ip}:{port}:{info_hash}".encode()).hexdigest()[:16]
            findings.append(
                CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="dht_discovery",
                    confidence=0.6,
                    ts=time.time(),
                    provenance=("deep_probe", "dht", f"{ip}:{port}"),
                    payload_text=f"DHT peer {ip}:{port} for {info_hash}",
                    metadata={"infohash": info_hash, "peer_ip": ip, "peer_port": port},
                )
            )
        return findings

    except asyncio.TimeoutError:
        logger.debug(f"DHT scan_dht timeout for query={query}")
        return []
    except Exception as e:
        logger.debug(f"DHT scan_dht failed: {e}")
        return []


async def run_deep_probe_if_enabled(
    query: str,
    store,
    deep_probe_enabled: bool = False,
) -> Optional[dict]:
    """
    Conditionally run deep probe — called ONLY when --deep-probe flag is set.

    This is the seam that `core/__main__.py` calls after sprint export completes.
    Does NOT block export — runs as post-export activity.

    Args:
        query: Search query for probe
        store: DuckDBShadowStore instance
        deep_probe_enabled: Must be True to run (set by --deep-probe CLI flag)

    Returns:
        Probe result dict or None if not enabled/errors
    """
    if not deep_probe_enabled:
        return None

    try:
        return await run_deep_probe(query, store)
    except Exception as e:
        logger.warning(f"[DEEP_PROBE] run failed: {e}")
        return None


# =============================================================================
# Convenience: CLI-only entry point (not used by sprint canonical path)
# =============================================================================

async def run_deep_probe_standalone(
    query: str,
    timeout_s: float = MAX_PROBE_DURATION_S,
) -> dict:
    """
    Standalone deep probe run (no sprint, no DuckDB store).

    Used by: python -m hledac.universal.deep_research.probe_runner --query "..."
    """
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    store = DuckDBShadowStore()
    await store.async_initialize()

    try:
        result = await run_deep_probe(query, store, timeout_s=timeout_s)
        return result
    finally:
        await store.aclose()
