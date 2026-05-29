"""
intelligence/ct_lane.py
======================

Sprint F234: CT intelligence lane as async-native module.

Data sources (zero API key):
    - crt.sh JSON API: https://crt.sh/?q=<domain>&output=json
      Rate limit: ~1 req/s, max 1000 results per query

Output per finding:
    domain (issuer_cn → SAN → common_name hierarchie)
    org_name (z issuer O= field)
    not_before / not_after (certificate lifetime)
    san_list (Subject Alternative Names → cross-domain discovery)
    issuer_ca (CA fingerprint)
    serial_number

Architecture: async-native, compatible with source_finding_bridge.py lane pattern.
Rate limiting and deduplication are built-in. No external API keys required.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

import aiohttp  # runtime import — aiohttp available in execution environment

logger = logging.getLogger(__name__)

# From runtime/source_finding_bridge.py — used for cap
try:
    from hledac.universal.runtime.source_finding_bridge import MAX_BRIDGE_OUTPUT
except ImportError:
    MAX_BRIDGE_OUTPUT = 500

# Rate limit: crt.sh allows ~1 req/s
_CT_RATE_LIMIT_S = 1.0


@dataclass
class CTFinding:
    """Single Certificate Transparency log entry."""

    domain: str
    org_name: str | None
    not_before: str | None
    not_after: str | None
    san_list: list[str] = field(default_factory=list)
    issuer_ca: str | None = None
    serial_number: str | None = None

    def to_finding_dict(self) -> dict:
        """Convert to finding dict for bridge integration."""
        return {
            "source": "certificate_transparency",
            "domain": self.domain,
            "org": self.org_name,
            "valid_from": self.not_before,
            "valid_to": self.not_after,
            "sans": self.san_list,
            "ca": self.issuer_ca,
        }


async def fetch_ct_findings(
    query: str,
    session: aiohttp.ClientSession,
    *,
    limit: int = MAX_BRIDGE_OUTPUT,
    deduplicate: bool = True,
) -> list[CTFinding]:
    """
    Fetch CT log entries for domain/org query.

    Respects MAX_BRIDGE_OUTPUT cap. Rate: 1 req/s (crt.sh limit).
    Uses wildcard subdomain query (%.<domain>) to capture all subdomains.

    Args:
        query: domain or org to query
        session: aiohttp.ClientSession for HTTP requests
        limit: maximum findings to return (capped at MAX_BRIDGE_OUTPUT)
        deduplicate: skip duplicate domains within this batch

    Returns:
        list of CTFinding objects, bounded by MAX_BRIDGE_OUTPUT
    """
    url = f"https://crt.sh/?q=%.{query}&output=json"
    findings: list[CTFinding] = []
    seen: set[str] = set()

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data: list[dict] = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"ct_lane fetch failed for {query}: {e}")
        return findings

    for entry in data[:limit]:
        # Hierarchie: issuer_cn → name_value (SAN) → common_name
        domain = entry.get("common_name", "") or entry.get("name_value", "")
        if not domain:
            continue

        if deduplicate and domain in seen:
            continue
        seen.add(domain)

        # Parse issuer O= field for org_name
        issuer_name = entry.get("issuer_name", "") or ""
        org_name: str | None = None
        if issuer_name:
            for part in issuer_name.split(","):
                part = part.strip()
                if part.startswith("O="):
                    org_name = part[2:].strip()
                    break

        # SAN list: name_value contains all SANs newline-separated
        san_raw = entry.get("name_value", "")
        sans = [s.strip() for s in san_raw.split("\n") if s.strip() and "." in s]

        findings.append(
            CTFinding(
                domain=domain,
                org_name=org_name,
                not_before=entry.get("not_before"),
                not_after=entry.get("not_after"),
                san_list=sans,
                issuer_ca=entry.get("issuer_cn"),
                serial_number=entry.get("serial_number"),
            )
        )

        if len(findings) >= limit:
            break

    return findings


async def stream_ct_findings(
    query: str,
    session: aiohttp.ClientSession,
    *,
    rate_limit_s: float = _CT_RATE_LIMIT_S,
    max_findings: int = MAX_BRIDGE_OUTPUT,
) -> AsyncIterator[CTFinding]:
    """
    Stream CT findings one-by-one with rate limiting.

    Yields CTFinding objects as they are parsed, respecting rate limits.
    """
    last_request: float = 0.0
    count = 0

    url = f"https://crt.sh/?q=%.{query}&output=json"

    # Rate limit
    elapsed = time.time() - last_request
    if elapsed < rate_limit_s:
        await asyncio.sleep(rate_limit_s - elapsed)

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data: list[dict] = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"ct_lane stream failed for {query}: {e}")
        return

    last_request = time.time()

    seen: set[str] = set()
    for entry in data:
        if count >= max_findings:
            break

        domain = entry.get("common_name", "") or entry.get("name_value", "")
        if not domain or domain in seen:
            continue
        seen.add(domain)

        issuer_name = entry.get("issuer_name", "") or ""
        org_name: str | None = None
        if issuer_name:
            for part in issuer_name.split(","):
                part = part.strip()
                if part.startswith("O="):
                    org_name = part[2:].strip()
                    break

        san_raw = entry.get("name_value", "")
        sans = [s.strip() for s in san_raw.split("\n") if s.strip() and "." in s]

        yield CTFinding(
            domain=domain,
            org_name=org_name,
            not_before=entry.get("not_before"),
            not_after=entry.get("not_after"),
            san_list=sans,
            issuer_ca=entry.get("issuer_cn"),
            serial_number=entry.get("serial_number"),
        )
        count += 1


def ct_findings_to_bridge_candidates(
    findings: list[CTFinding],
    query: str,
    sprint_id: str,
) -> tuple[list[dict], list[str]]:
    """
    Convert CTFinding list to bridge-compatible candidates + rejections.

    This matches the pattern used in source_finding_bridge.py for
    ct_results_to_findings(), wayback_results_to_findings(), etc.

    Returns:
        (candidates, rejection_reasons)
    """
    candidates: list[dict] = []
    rejections: list[str] = []

    for finding in findings[:MAX_BRIDGE_OUTPUT]:
        if not finding.domain:
            rejections.append("missing_domain")
            continue

        if "." not in finding.domain:
            rejections.append("low_information")
            continue

        candidate = {
            "source_type": "ct_lane",
            "domain": finding.domain,
            "org": finding.org_name,
            "valid_from": finding.not_before,
            "valid_to": finding.not_after,
            "sans": finding.san_list,
            "ca": finding.issuer_ca,
            "serial": finding.serial_number,
            "query": query,
            "sprint_id": sprint_id,
        }
        candidates.append(candidate)

    return candidates, rejections


__all__ = [
    "CTFinding",
    "fetch_ct_findings",
    "stream_ct_findings",
    "ct_findings_to_bridge_candidates",
    "MAX_BRIDGE_OUTPUT",
]
