"""
Sprint F222D: NonfeedSeed Extractor — Feed → Pivot → Nonfeed Bridge
=================================================================

runtime/nonfeed_seed_extractor.py
--------------------------------
Deterministic IOC extraction from feed findings → bounded seeds for
nonfeed lanes (CT, PassiveDNS, Wayback, DOH).

Rules:
  - regex/deterministic only — no model calls
  - bounded (max_seeds)
  - deduplicate by (kind, value)
  - filter publisher feed domains (krebsonsecurity.com etc.)
  - prefer IOCs in finding body/title, not source URL of feed itself

API:
    NonfeedSeed(value, kind, source, confidence, reason)
    extract_nonfeed_seeds_from_text(text, max_seeds=50) -> list[NonfeedSeed]
    extract_nonfeed_seeds_from_findings(findings, max_seeds=100) -> list[NonfeedSeed]
    compute_lane_unlocks(seeds) -> dict[str, list[str]]

Publisher domains (feed aggregators — excluded as seeds unless real indicators):
    krebsonsecurity.com, thehackernews.com, bleedingcomputer.com,
    welivesecurity.com, sans.edu, darkreading.com, zdnet.com,
    theregister.com, arstechnica.com, securityweek.com
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "NonfeedSeed",
    "extract_nonfeed_seeds_from_text",
    "extract_nonfeed_seeds_from_findings",
    "compute_lane_unlocks",
    "PUBLISHER_DOMAINS",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PUBLISHER_DOMAINS: frozenset[str] = frozenset([
    "krebsonsecurity.com",
    "thehackernews.com",
    "bleepingcomputer.com",
    "welivesecurity.com",
    "sans.edu",
    "darkreading.com",
    "zdnet.com",
    "theregister.com",
    "arstechnica.com",
    "securityweek.com",
    "infoworld.com",
    "threatpost.com",
    "darknet.com.au",
    "journalofcloudsecurity.com",
])
"""Publisher/aggregator domains filtered from seed extraction."""

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=False)
class NonfeedSeed:
    """
    Sprint F222D: Bounded IOC seed for nonfeed lanes.

    Fields:
        value:       The IOC value (domain, IP, URL, hash, CVE)
        kind:        IOC kind: domain | ip | url | hash | cve | email | unknown
        source:      Source tag: body | title | query | url
        confidence:  Extraction confidence [0.0, 1.0]
        reason:      Why this was extracted
    """
    value: str
    kind: str
    source: str
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in (
            "domain", "ip", "url", "hash", "cve", "email", "unknown"
        ):
            object.__setattr__(self, "kind", "unknown")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

_HASH_RE = re.compile(r"\b([a-fA-F0-9]{32,64})\b")

_CVE_RE = re.compile(r"\b(CVE-\d{4}-\d{4,})\b", re.IGNORECASE)

_DOMAIN_RE = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}[a-zA-Z0-9/_\-]*"
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")

_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")

_OBFUSCATED_RE = re.compile(r"\[(\.)\]|\((\.)\)")


def _is_publisher_domain(domain: str) -> bool:
    """Return True if domain is a known publisher/aggregator."""
    return domain.lower() in PUBLISHER_DOMAINS


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_nonfeed_seeds_from_text(
    text: str,
    *,
    max_seeds: int = 50,
) -> list[NonfeedSeed]:
    """
    Sprint F222D: Extract IOC seeds from arbitrary text.

    Extraction order (most specific first):
      1. URL   (has :// prefix)           → url
      2. Email  (has @)                   → email
      3. IP     (dotted quad)             → ip
      4. Hash   (32-64 hex chars)         → hash (md5/sha1/sha256)
      5. CVE    (CVE-YYYY-NNNN)            → cve
      6. Domain (generic fallback)         → domain

    Args:
        text:     Text to scan (body, title, etc.)
        max_seeds: Hard cap on returned seeds (default 50)

    Returns:
        Deduplicated list of NonfeedSeed, newest-first.
        Publisher domains filtered out.
        Bounded to max_seeds.
    """
    if not text or not isinstance(text, str):
        return []

    # ── Pre-process: deobfuscate all bracket/parens patterns ──────────────
    cleaned = _OBFUSCATED_RE.sub(".", text)

    seen: dict[tuple[str, str], NonfeedSeed] = {}  # (kind, value) → seed

    # ── 1. URLs ──────────────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _URL_RE.finditer(cleaned):
            val = m.group(0)
            key = ("url", val)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=val,
                    kind="url",
                    source="body",
                    confidence=0.9,
                    reason="url_in_body",
                )
            if len(seen) >= max_seeds:
                break

    # ── 2. Emails ────────────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _EMAIL_RE.finditer(cleaned):
            raw = m.group(1).lower()
            key = ("email", raw)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=raw,
                    kind="email",
                    source="body",
                    confidence=0.85,
                    reason="email_in_body",
                )
            if len(seen) >= max_seeds:
                break

    # ── 3. IP addresses ────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _IP_RE.finditer(cleaned):
            val = m.group(0)
            key = ("ip", val)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=val,
                    kind="ip",
                    source="body",
                    confidence=0.95,
                    reason="ip_in_body",
                )
            if len(seen) >= max_seeds:
                break

    # ── 4. Hashes ──────────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _HASH_RE.finditer(cleaned):
            raw = m.group(1).lower()
            if len(raw) == 32:
                kind_str = "md5"
            elif len(raw) == 40:
                kind_str = "sha1"
            elif len(raw) == 64:
                kind_str = "sha256"
            else:
                kind_str = "unknown"
            key = ("hash", raw)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=raw,
                    kind="hash",
                    source="body",
                    confidence=0.8,
                    reason=f"hash_in_body_{kind_str}",
                )
            if len(seen) >= max_seeds:
                break

    # ── 5. CVEs ────────────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _CVE_RE.finditer(cleaned):
            raw = m.group(1).upper()
            key = ("cve", raw)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=raw,
                    kind="cve",
                    source="body",
                    confidence=0.9,
                    reason="cve_in_body",
                )
            if len(seen) >= max_seeds:
                break

    # ── 6. Domains ──────────────────────────────────────────────────────────
    if len(seen) < max_seeds:
        for m in _DOMAIN_RE.finditer(cleaned):
            raw = m.group(0).lower()
            # Block domains ending in .gov.X, .edu.X, .mil.X, .onion (not useful seeds)
            if raw.endswith((".gov.", ".edu.", ".mil.", ".onion")):
                continue
            # Skip publisher/aggregator domains (they are feed sources, not real IOCs)
            if _is_publisher_domain(raw):
                continue
            key = ("domain", raw)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=raw,
                    kind="domain",
                    source="body",
                    confidence=0.7,
                    reason="domain_in_body",
                )
            if len(seen) >= max_seeds:
                break

    return list(seen.values())


def extract_nonfeed_seeds_from_findings(
    findings: list[dict],
    *,
    max_seeds: int = 100,
) -> list[NonfeedSeed]:
    """
    Sprint F222D: Extract IOC seeds from a list of finding dicts.

    Scans each finding's:
      - payload_text  (body content)
      - title          (article title)
      - query          (search query / domain pivot)

    Args:
        findings:  List of CanonicalFinding-like dicts
        max_seeds: Hard cap on total returned seeds (default 100)

    Returns:
        Deduplicated list of NonfeedSeed across all findings.
        Publisher domains filtered.
        Bounded to max_seeds.
    """
    if not findings:
        return []

    seen: dict[tuple[str, str], NonfeedSeed] = {}
    total = 0

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if len(seen) >= max_seeds:
            break

        # payload_text
        payload = finding.get("payload_text", "") or ""
        if isinstance(payload, str) and payload:
            seeds = extract_nonfeed_seeds_from_text(payload, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(
                        value=s.value,
                        kind=s.kind,
                        source="body",
                        confidence=s.confidence,
                        reason=s.reason,
                    )

        # title
        title = finding.get("title", "") or ""
        if isinstance(title, str) and title:
            seeds = extract_nonfeed_seeds_from_text(title, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(
                        value=s.value,
                        kind=s.kind,
                        source="title",
                        confidence=min(s.confidence + 0.05, 1.0),
                        reason="domain_in_title" if s.kind == "domain" else s.reason,
                    )

        # query (often contains domain/IP as search term)
        query = finding.get("query", "") or ""
        if isinstance(query, str) and query:
            seeds = extract_nonfeed_seeds_from_text(query, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(
                        value=s.value,
                        kind=s.kind,
                        source="query",
                        confidence=0.85,
                        reason=f"ioc_in_query_{s.kind}",
                    )

        total += 1

    result = list(seen.values())
    return result[:max_seeds]


def compute_lane_unlocks(
    seeds: list[NonfeedSeed],
) -> dict[str, list[str]]:
    """
    Sprint F222D: Map seeds → which nonfeed lanes they unlock.

    Returns:
        {
            "ct":          [domain seeds],
            "passive_dns": [domain + ip seeds],
            "wayback":     [domain + url seeds],
            "doh":         [domain seeds],
            "graph":       [hash seeds],
        }
    """
    domains = [s for s in seeds if s.kind == "domain"]
    ips = [s for s in seeds if s.kind == "ip"]
    urls = [s for s in seeds if s.kind == "url"]
    hashes = [s for s in seeds if s.kind == "hash"]
    cves = [s for s in seeds if s.kind == "cve"]

    return {
        "ct": [s.value for s in domains],
        "passive_dns": [s.value for s in domains + ips],
        "wayback": [s.value for s in domains + urls],
        "doh": [s.value for s in domains],
        "graph": [s.value for s in hashes],
        "cve": [s.value for s in cves],
    }