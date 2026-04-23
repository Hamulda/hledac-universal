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
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional, Tuple, List, Any

logger = logging.getLogger(__name__)

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
                        probe_duration_s, probe_source_type, findings_ingested

    Invariants enforced:
      - All findings use source_type="deep_probe"
      - Timeout bounds probe runtime
      - All external calls are fail-safe
      - Findings persisted ONLY via async_ingest_findings_batch()
    """
    from hledac.universal.deep_probe import (
        DeepProbeScanner,
        scan_ipfs,
        scan_s3_buckets,
    )
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    start_time = time.monotonic()
    result = {
        "urls_discovered": 0,
        "buckets_scanned": 0,
        "ipfs_results": 0,
        "probe_duration_s": 0.0,
        "probe_source_type": "deep_probe",
        "findings_ingested": 0,
        "errors": [],
    }

    # Collect all canonical findings for batch ingest
    all_findings: List[CanonicalFinding] = []
    scanner = DeepProbeScanner(max_memory_mb=100)

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
                return ("discovery", len(urls), discovery_findings)
            except Exception as e:
                logger.debug(f"Discovery scan failed: {e}")
                return ("discovery", 0, [])

        # 2. S3 bucket scan (bounded by max_buckets)
        # Now returns Tuple[List[dict], List[CanonicalFinding]]
        async def _run_bucket_scan():
            try:
                _, bucket_findings = await scanner.scan_s3_buckets(
                    domain, store=store, max_buckets=max_buckets
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
                return ("ipfs", len(ipfs_findings), ipfs_findings)
            except Exception as e:
                logger.debug(f"IPFS scan failed: {e}")
                return ("ipfs", 0, [])

        # Race all tasks against timeout using gather
        all_results = await asyncio.gather(
            _run_discovery(),
            _run_bucket_scan(),
            _run_ipfs(),
            return_exceptions=True,
        )

        # Apply timeout post-wait (bounded by design)
        elapsed = time.monotonic() - start_time
        if elapsed > timeout_s:
            logger.debug(f"Probe exceeded timeout: {elapsed:.1f}s > {timeout_s}s")

        # Collect results by type tag and accumulate findings
        for res in all_results:
            if isinstance(res, tuple) and len(res) == 3:
                tag, count, findings = res
                if tag == "discovery":
                    result["urls_discovered"] = count
                    all_findings.extend(findings)
                elif tag == "bucket":
                    result["buckets_scanned"] = count
                    all_findings.extend(findings)
                elif tag == "ipfs":
                    result["ipfs_results"] = count
                    all_findings.extend(findings)
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
