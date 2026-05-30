#!/usr/bin/env python3
"""
Alternative Protocol Fetcher — Unified access to beyond-indexed content.

Orchestrates IPFS, Gopher, Gemini, and I2P protocols for accessing content
invisible to standard web crawlers.

F230: Alternative Protocol Stack integration.

Gating:
  - HLEDAC_ENABLE_ALT_PROTOCOLS=1 enables all protocols
  - Max 2 concurrent alt-protocol requests (M1 memory constraint)
  - Fail-soft: individual protocol failures don't block others

Returns list[CanonicalFinding] with appropriate source_type per protocol.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import NamedTuple

logger = logging.getLogger(__name__)

# =============================================================================
# Gate
# =============================================================================
ALT_PROTOCOLS_ENABLED: bool = os.getenv("HLEDAC_ENABLE_ALT_PROTOCOLS", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Memory constraint: max 2 concurrent alt-protocol requests
MAX_CONCURRENT_ALT: int = 2

# Per-protocol timeouts
IPFS_TIMEOUT: int = 30
GOPHER_TIMEOUT: int = 15
GEMINI_TIMEOUT: int = 20
I2P_TIMEOUT: int = 30
FEDIVERSE_TIMEOUT: int = 10
MATRIX_TIMEOUT: int = 10


class AltProtocolResult(NamedTuple):
    """Result from a single alt-protocol source."""

    source_type: str
    findings_count: int
    success: bool
    error: str | None


# =============================================================================
# Protocol Imports (lazy)
# =============================================================================
def _get_ipfs_client():
    """Lazy import IPFS client."""
    from network import ipfs_client
    return ipfs_client


def _get_gopher_transport():
    """Lazy import Gopher transport (canonical: transport/gopher_transport.py)."""
    from transport.gopher_transport import get_gopher_transport
    return get_gopher_transport()


def _get_gemini_transport():
    """Lazy import Gemini transport."""
    from network import gemini_transport
    return gemini_transport


def _get_i2p_client():
    """Lazy import I2P client."""
    from network import i2p_client
    return i2p_client


def _get_fediverse_adapter():
    """Lazy import Fediverse adapter."""
    from discovery import fediverse_adapter
    return fediverse_adapter


def _get_matrix_adapter():
    """Lazy import Matrix adapter."""
    from discovery import matrix_adapter
    return matrix_adapter


# =============================================================================
# Per-Protocol Fetchers
# =============================================================================
async def _fetch_from_ipfs(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via IPFS.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    ipfs = _get_ipfs_client()

    async with semaphore:
        try:
            # Search IPFS for CIDs
            cids = await asyncio.wait_for(
                ipfs.find_via_ipfs_search(query),
                timeout=IPFS_TIMEOUT,
            )

            findings: list[CanonicalFinding] = []

            for cid in cids[:10]:  # Cap results
                content = await asyncio.wait_for(
                    ipfs.fetch_ipfs(cid),
                    timeout=IPFS_TIMEOUT,
                )
                if content:
                    finding = CanonicalFinding(
                        finding_id=f"ipfs-alt-{cid[:12]}-{int(time.time() * 1000)}",
                        query=query,
                        source_type="ipfs_content",
                        confidence=0.75,
                        ts=time.time(),
                        provenance=(f"ipfs://{cid}",),
                        payload_text=content.decode("utf-8", errors="replace")[:4096]
                        if isinstance(content, bytes)
                        else str(content)[:4096],
                    )
                    findings.append(finding)

            return findings, AltProtocolResult(
                source_type="ipfs",
                findings_count=len(findings),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="ipfs", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"IPFS alt fetch error: {e}")
            return [], AltProtocolResult(source_type="ipfs", findings_count=0, success=False, error=str(e))


async def _fetch_from_gopher(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Gopher protocol.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    gopher = _get_gopher_transport()

    async with semaphore:
        try:
            # Search gopherspace via Veronica-2
            findings = await asyncio.wait_for(
                gopher.search_as_findings(query),
                timeout=GOPHER_TIMEOUT,
            )

            return findings, AltProtocolResult(
                source_type="gopher",
                findings_count=len(findings),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="gopher", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"Gopher alt fetch error: {e}")
            return [], AltProtocolResult(source_type="gopher", findings_count=0, success=False, error=str(e))


async def _fetch_from_gemini(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Gemini protocol.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    gemini = _get_gemini_transport()

    async with semaphore:
        try:
            # Search and crawl geminispace
            findings = await asyncio.wait_for(
                gemini.geminispace_to_findings(query, max_pages=10),
                timeout=GEMINI_TIMEOUT,
            )

            return findings, AltProtocolResult(
                source_type="gemini",
                findings_count=len(findings),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="gemini", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"Gemini alt fetch error: {e}")
            return [], AltProtocolResult(source_type="gemini", findings_count=0, success=False, error=str(e))


async def _fetch_from_i2p(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via I2P eepsites.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    i2p = _get_i2p_client()

    async with semaphore:
        try:
            # Check I2P availability
            available = await i2p.is_i2p_available()
            if not available:
                return [], AltProtocolResult(
                    source_type="i2p", findings_count=0, success=True, error="i2p_unavailable"
                )

            # Fetch I2P eepsites
            findings = await asyncio.wait_for(
                i2p.i2p_to_findings(query),
                timeout=I2P_TIMEOUT,
            )

            return findings, AltProtocolResult(
                source_type="i2p",
                findings_count=len(findings),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="i2p", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"I2P alt fetch error: {e}")
            return [], AltProtocolResult(source_type="i2p", findings_count=0, success=False, error=str(e))


async def _fetch_from_fediverse(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Fediverse/Mastodon public API.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    fediverse = _get_fediverse_adapter()

    async with semaphore:
        try:
            adapter = fediverse.FediverseAdapter()
            try:
                statuses = await asyncio.wait_for(
                    adapter.search_public_timeline(query, max_results=50),
                    timeout=FEDIVERSE_TIMEOUT,
                )

                findings: list[CanonicalFinding] = []
                for status in statuses[:20]:  # Cap results
                    content = status.get("content", "")
                    account = status.get("account", {})
                    acct = account.get("acct", "unknown")

                    finding = CanonicalFinding(
                        finding_id=f"fediverse-{status.get('id', int(time.time() * 1000))}",
                        query=query,
                        source_type="fediverse",
                        confidence=0.6,
                        ts=status.get("created_at", time.time()),
                        provenance=(f"https://infosec.exchange/@{acct}",),
                        payload_text=content[:4096],
                    )
                    findings.append(finding)

                return findings, AltProtocolResult(
                    source_type="fediverse",
                    findings_count=len(findings),
                    success=True,
                    error=None,
                )
            finally:
                await adapter.close()
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="fediverse", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"Fediverse alt fetch error: {e}")
            return [], AltProtocolResult(source_type="fediverse", findings_count=0, success=False, error=str(e))


async def _fetch_from_matrix(
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list, AltProtocolResult]:
    """
    Fetch content via Matrix public rooms API.

    Returns:
        (list[CanonicalFinding], AltProtocolResult)
    """
    from knowledge.duckdb_store import CanonicalFinding

    matrix = _get_matrix_adapter()

    async with semaphore:
        try:
            adapter = matrix.MatrixPublicAdapter()
            try:
                # Search public rooms
                rooms = await asyncio.wait_for(
                    adapter.search_public_rooms(query, limit=5),
                    timeout=MATRIX_TIMEOUT,
                )

                findings: list[CanonicalFinding] = []
                for room in rooms[:3]:  # Top 3 rooms
                    messages = await asyncio.wait_for(
                        adapter.get_room_messages(room.room_id, limit=50),
                        timeout=MATRIX_TIMEOUT,
                    )

                    for msg in messages[:10]:  # Cap per room
                        content = msg.get("content", {}).get("body", "")

                        finding = CanonicalFinding(
                            finding_id=f"matrix-{msg.get('event_id', int(time.time() * 1000))}",
                            query=query,
                            source_type="matrix_public",
                            confidence=0.5,
                            ts=msg.get("origin_server_ts", time.time()) / 1000,
                            provenance=(f"https://matrix.to/#/{room.room_id}",),
                            payload_text=content[:4096],
                        )
                        findings.append(finding)

                return findings, AltProtocolResult(
                    source_type="matrix",
                    findings_count=len(findings),
                    success=True,
                    error=None,
                )
            finally:
                await adapter.close()
        except asyncio.TimeoutError:
            return [], AltProtocolResult(source_type="matrix", findings_count=0, success=False, error="timeout")
        except Exception as e:
            logger.debug(f"Matrix alt fetch error: {e}")
            return [], AltProtocolResult(source_type="matrix", findings_count=0, success=False, error=str(e))


# =============================================================================
# Main Orchestrator
# =============================================================================
async def fetch_all_alt_protocols(
    query: str,
    max_concurrent: int = MAX_CONCURRENT_ALT,
) -> tuple[list, list[AltProtocolResult]]:
    """
    Fetch content from all alternative protocols in parallel.

    Args:
        query: Search query string
        max_concurrent: Max concurrent protocol requests (default 2 for M1)

    Returns:
        (all_findings, protocol_results) — tuple of findings list and per-protocol results
    """
    if not ALT_PROTOCOLS_ENABLED:
        logger.debug("Alt protocols disabled (HLEDAC_ENABLE_ALT_PROTOCOLS != 1)")
        return [], []

    all_findings: list = []
    protocol_results: list[AltProtocolResult] = []

    sem = asyncio.Semaphore(max_concurrent)

    # Run all protocol fetchers in parallel with semaphore
    tasks = [
        _fetch_from_ipfs(query, sem),
        _fetch_from_gopher(query, sem),
        _fetch_from_gemini(query, sem),
        _fetch_from_i2p(query, sem),
    ]

    # Add social sources if enabled
    if os.getenv("HLEDAC_ENABLE_SOCIAL", "").strip() == "1":
        tasks.append(_fetch_from_fediverse(query, sem))
        tasks.append(_fetch_from_matrix(query, sem))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.debug(f"Alt protocol task exception: {result}")
            continue

        if not isinstance(result, tuple):
            continue

        findings, proto_result = result
        all_findings.extend(findings)
        protocol_results.append(proto_result)

    logger.info(
        f"Alt protocols: {len(all_findings)} findings from "
        f"{sum(1 for r in protocol_results if r.success)} protocols"
    )

    return all_findings, protocol_results


async def fetch_fediverse_only(query: str) -> list:
    """
    Fetch only from Fediverse (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    sem = asyncio.Semaphore(1)
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
    sem = asyncio.Semaphore(1)
    findings, _ = await _fetch_from_matrix(query, sem)
    return findings


# =============================================================================
# Convenience Functions
# =============================================================================
async def fetch_ipfs_only(query: str) -> list:
    """
    Fetch only from IPFS (for targeted use).

    Args:
        query: Search query

    Returns:
        list[CanonicalFinding]
    """
    sem = asyncio.Semaphore(1)
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
    sem = asyncio.Semaphore(1)
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
    sem = asyncio.Semaphore(1)
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
    sem = asyncio.Semaphore(1)
    findings, _ = await _fetch_from_i2p(query, sem)
    return findings


# =============================================================================
# Stats
# =============================================================================
def get_alt_protocols_status() -> dict:
    """
    Get status of all alternative protocols.

    Returns:
        Dict with protocol availability and last result info
    """
    return {
        "enabled": ALT_PROTOCOLS_ENABLED,
        "max_concurrent": MAX_CONCURRENT_ALT,
        "protocols": {
            "ipfs": {"enabled": True, "gate": "HLEDAC_ENABLE_ALT_PROTOCOLS"},
            "gopher": {"enabled": True, "gate": "HLEDAC_ENABLE_ALT_PROTOCOLS"},
            "gemini": {"enabled": True, "gate": "HLEDAC_ENABLE_ALT_PROTOCOLS"},
            "i2p": {"enabled": True, "gate": "HLEDAC_ENABLE_ALT_PROTOCOLS", "requires_daemon": True},
            "fediverse": {"enabled": True, "gate": "HLEDAC_ENABLE_SOCIAL"},
            "matrix": {"enabled": True, "gate": "HLEDAC_ENABLE_SOCIAL"},
        },
    }