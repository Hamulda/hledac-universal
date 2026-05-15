"""
Feed-to-Pivot Seed Extractor — Sprint F220A.

Lightweight pure-stdlib extractor for bounded pivot seed extraction from
feed finding payloads. Bounded: max 1000 texts, 20k chars/text, 256 seeds.
No network, no ML, no heavy imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "PivotSeed",
    "PivotSeedExtractionResult",
    "extract_pivot_seeds_from_texts",
]

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
MAX_TEXTS: Final[int] = 1000
MAX_TEXT_CHARS: Final[int] = 20_000
MAX_SEEDS: Final[int] = 256

# ----------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PivotSeed:
    """A single pivot seed extracted from feed payload text."""

    value: str
    seed_type: str  # domain | url | ip | email | hash | entity
    source_family: str
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class PivotSeedExtractionResult:
    """Result of a pivot seed extraction run."""

    seeds: tuple[PivotSeed, ...]
    scanned_items: int
    truncated: bool
    reason: str


# ----------------------------------------------------------------------
# Regex patterns — conservative, bounded
# ----------------------------------------------------------------------
_RE_DOMAIN: Final[re.Pattern[str]] = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
)
_RE_IPV4: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\."
    r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\."
    r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\."
    r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9]))\b"
)
_RE_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_RE_URL: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s<>\"]+"
)
_RE_MD5: Final[re.Pattern[str]] = re.compile(r"\b[a-fA-F0-9]{32}\b")
_RE_SHA1: Final[re.Pattern[str]] = re.compile(r"\b[a-fA-F0-9]{40}\b")
_RE_SHA256: Final[re.Pattern[str]] = re.compile(r"\b[a-fA-F0-9]{64}\b")

# Entity fallback — only activated if text contains ransomware/threat keywords
_RE_ENTITY_STRICT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ransomware|lockbit|conti|alphv|blackcat|babuk|clop|hellokitty|"
    r"revil|grief|sodinokibi|azov|moonlock|lac，减|bing|loader|c2|infrastructure|"
    r"threat.actor|victim|hive|breach|leak|exfil)\b",
    flags=re.IGNORECASE,
)
# Loose entity — extracts capitalised multi-word org-like strings (2-5 words)
_RE_ENTITY_LOOSE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z][a-zA-Z0-9]?(?:[A-Za-z0-9][\ \-]?){1,20}[A-Za-z0-9]\b"
)
# Additional loose patterns: all-caps acronyms + quoted strings
_RE_ENTITY_QUOTED: Final[re.Pattern[str]] = re.compile(
    r'"[^"]{3,80}"'
)

# ----------------------------------------------------------------------
# Known false-positive domains (must not be emitted as seeds)
# ----------------------------------------------------------------------
_FALSE_DOMAINS: Final[frozenset[str]] = frozenset({
    "example.com",
    "test.com",
    "localhost",
    "localhost.localdomain",
    "example.org",
    "example.net",
    "invalid.com",
    "example.edu",
    "example.gov",
    "example.mil",
})

# ----------------------------------------------------------------------
# Internal extraction helpers
# ----------------------------------------------------------------------


def _extract_domains(text: str) -> list[str]:
    """Extract domain seeds, reject known false positives."""
    raw = _RE_DOMAIN.findall(text)
    return [d for d in raw if d.lower() not in _FALSE_DOMAINS and len(d) <= 253]


def _extract_urls(text: str) -> list[str]:
    """Extract URL seeds (full URL string)."""
    return _RE_URL.findall(text)


def _extract_ips(text: str) -> list[str]:
    """Extract IPv4 seeds."""
    return _RE_IPV4.findall(text)


def _extract_emails(text: str) -> list[str]:
    """Extract email seeds."""
    return _RE_EMAIL.findall(text)


def _extract_hashes(text: str) -> list[str]:
    """Extract MD5/SHA1/SHA256 hashes, deduplicated."""
    seen: set[str] = set()
    result: list[str] = []
    for pat in (_RE_MD5, _RE_SHA1, _RE_SHA256):
        for h in pat.findall(text):
            if h not in seen:
                seen.add(h)
                result.append(h)
    return result


def _extract_entities(text: str) -> list[str]:
    """
    Extract entity strings only if text contains known threat/ransomware keywords.
    Falls back to empty list if no keywords detected — fail-safe.
    """
    if not _RE_ENTITY_STRICT.search(text):
        return []

    # quoted strings — high signal
    quoted = _RE_ENTITY_QUOTED.findall(text)
    # loose multi-word capitalised strings
    loose = _RE_ENTITY_LOOSE.findall(text)

    # Filter: must be between 3 and 80 chars, no pure digits, not a domain/IP
    result: list[str] = []
    for s in quoted + loose:
        s = s.strip()
        if 3 <= len(s) <= 80 and not s.isdigit() and not _RE_DOMAIN.match(s) and not _RE_IPV4.match(s):
            if s not in result:
                result.append(s)
    return result


# ----------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------


def _normalise_domain(d: str) -> str:
    """Lowercase + strip trailing dot."""
    return d.rstrip(".").lower()


def _normalise_email(e: str) -> str:
    """Lowercase email address."""
    return e.lower()




def _normalise_url(url: str) -> str:
    """Lowercase URL scheme + host, preserve path/query."""
    try:
        parts = url.split("://", 1)
        if len(parts) == 2:
            scheme, rest = parts
            host_path = rest.split("/", 1)
            host = host_path[0].lower()
            path = "/" + host_path[1] if len(host_path) > 1 else "/"
            return f"{scheme.lower()}://{host}{path}"
    except Exception:
        pass
    return url.lower()


def _normalise_hash(h: str) -> str:
    """Lowercase hash."""
    return h.lower()


# ----------------------------------------------------------------------
# Ranking constants
# ----------------------------------------------------------------------
_TYPE_RANK: Final[dict[str, int]] = {
    "domain": 1,
    "url": 2,
    "ip": 3,
    "hash": 4,
    "email": 5,
    "entity": 6,
}


def _confidence_for(seed_type: str) -> float:
    """Return confidence in (0.55, 0.95] range."""
    if seed_type == "entity":
        return 0.55
    if seed_type in ("domain", "url", "ip"):
        return 0.85
    if seed_type == "hash":
        return 0.90
    if seed_type == "email":
        return 0.80
    return 0.70


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def extract_pivot_seeds_from_texts(
    texts: list[str] | tuple[str, ...],
    *,
    source_family: str = "feed",
    max_texts: int = MAX_TEXTS,
    max_text_chars: int = MAX_TEXT_CHARS,
    max_seeds: int = MAX_SEEDS,
) -> PivotSeedExtractionResult:
    """
    Extract bounded pivot seeds from a list of text payloads.

    Ranking (highest first): domain > url > ip > hash > email > entity.
    Entity seeds are only emitted if the text contains known threat keywords.

    Args:
        texts: iterable of text strings (e.g. payload_text fields from feed findings)
        source_family: label for source family (default "feed")
        max_texts: maximum number of texts to scan (default 1000)
        max_text_chars: maximum chars per text (default 20_000)
        max_seeds: maximum seeds to emit (default 256)

    Returns:
        PivotSeedExtractionResult with bounded seeds and metadata
    """
    # Clamp inputs
    max_texts = min(max_texts, MAX_TEXTS)
    max_text_chars = min(max_text_chars, MAX_TEXT_CHARS)
    max_seeds = min(max_seeds, MAX_SEEDS)

    texts = tuple(texts)
    scanned = min(len(texts), max_texts)
    texts_to_scan = texts[:max_texts]

    raw_seeds: list[tuple[int, PivotSeed]] = []  # (rank, seed)

    for text in texts_to_scan:
        if not text:
            continue
        # Truncate text to max_text_chars before processing
        work = text[:max_text_chars]

        # Domains
        for d in _extract_domains(work):
            nd = _normalise_domain(d)
            raw_seeds.append((
                _TYPE_RANK["domain"],
                PivotSeed(
                    value=nd,
                    seed_type="domain",
                    source_family=source_family,
                    confidence=_confidence_for("domain"),
                    reason="domain_regex",
                ),
            ))

        # URLs
        for u in _extract_urls(work):
            nu = _normalise_url(u)
            raw_seeds.append((
                _TYPE_RANK["url"],
                PivotSeed(
                    value=nu,
                    seed_type="url",
                    source_family=source_family,
                    confidence=_confidence_for("url"),
                    reason="url_regex",
                ),
            ))

        # IPs
        for ip in _extract_ips(work):
            raw_seeds.append((
                _TYPE_RANK["ip"],
                PivotSeed(
                    value=ip,
                    seed_type="ip",
                    source_family=source_family,
                    confidence=_confidence_for("ip"),
                    reason="ipv4_regex",
                ),
            ))

        # Hashes
        for h in _extract_hashes(work):
            nh = _normalise_hash(h)
            raw_seeds.append((
                _TYPE_RANK["hash"],
                PivotSeed(
                    value=nh,
                    seed_type="hash",
                    source_family=source_family,
                    confidence=_confidence_for("hash"),
                    reason="hash_regex",
                ),
            ))

        # Emails
        for e in _extract_emails(work):
            ne = _normalise_email(e)
            raw_seeds.append((
                _TYPE_RANK["email"],
                PivotSeed(
                    value=ne,
                    seed_type="email",
                    source_family=source_family,
                    confidence=_confidence_for("email"),
                    reason="email_regex",
                ),
            ))

        # Entities — only if threat keywords present
        for ent in _extract_entities(work):
            raw_seeds.append((
                _TYPE_RANK["entity"],
                PivotSeed(
                    value=ent,
                    seed_type="entity",
                    source_family=source_family,
                    confidence=_confidence_for("entity"),
                    reason="entity_keyword_fallback",
                ),
            ))

    # Deduplicate by (seed_type, normalised_value)
    seen: set[tuple[str, str]] = set()
    deduped: list[PivotSeed] = []
    for rank, seed in raw_seeds:
        key = (seed.seed_type, seed.value)
        if key not in seen:
            seen.add(key)
            deduped.append(seed)

    # Sort by rank then confidence
    deduped.sort(key=lambda s: (rank, -s.confidence))

    # Apply max_seeds cap
    truncated = len(deduped) > max_seeds
    result_seeds = tuple(deduped[:max_seeds])

    reason = f"extracted {len(result_seeds)} seeds from {scanned} texts"
    if truncated:
        reason += f" (truncated from {len(deduped)})"

    return PivotSeedExtractionResult(
        seeds=result_seeds,
        scanned_items=scanned,
        truncated=truncated,
        reason=reason,
    )