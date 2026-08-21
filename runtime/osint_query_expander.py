"""
runtime/osint_query_expander.py — P1-1: Static OSINT Query Expansion
====================================================================

Bounded static expansion for broad OSINT queries without MLX dependency.
Generates 2-4 query variants per keyword category for PUBLIC lane coverage.

M1 safe: Pure Python dict, 0 MB RAM, 0 MB VRAM.

Invariant: Always-on, no feature flags, deterministic, fail-safe.
"""

__all__ = ["expand_osint_query", "OSINT_EXPANSION_TERMS", "MAX_VARIANTS"]

# Max variants per expansion to stay within lane budget
MAX_VARIANTS = 4

# OSINT expansion terms — domain-specific synonyms/antonyms for threat queries
# Format: keyword -> list of variant terms
OSINT_EXPANSION_TERMS: dict[str, list[str]] = {
    # Ransomware ecosystem
    "ransomware": [
        "ransom",
        "ransomware group",
        "RaaS",
        "ransom-as-a-service",
        "LockBit",
        "Conti",
        "ALPHV",
        "BlackCat",
        " Rhysida",
        "DarkSide",
    ],
    # Data leak / breach
    "leak": [
        "breach",
        "exposed",
        "dumped",
        "leaked",
        "torrent",
        "data leak",
        "db dump",
        "data dump",
    ],
    "breach": [
        "leak",
        "exposed",
        "dumped",
        "data breach",
        "corporate breach",
    ],
    # Dark web / Tor
    "dark web": [
        "darkweb",
        "dark web",
        "tor site",
        "onion",
        "hidden service",
        "tor website",
    ],
    "darkweb": [
        "dark web",
        "tor site",
        ".onion",
        "dark web marketplace",
    ],
    # Threat intelligence
    "threat intelligence": [
        "TI",
        "IOC",
        "indicator of compromise",
        "threat intel",
        "threat indicators",
    ],
    "threat intel": [
        "threat intelligence",
        "IOC",
        "indicator of compromise",
        "TIs",
    ],
    # Exposure / disclosure
    "exposure": [
        "exposed",
        "leaked",
        "public exposure",
        "disclosure",
        "data exposure",
    ],
    # Underground / cybercrime
    "cybercrime": [
        "cybercriminal",
        "underground forum",
        "hacker forum",
        "RaaS",
    ],
    # Specific indicators
    "CVE": [
        "vulnerability",
        "exploit",
        "CVE-",
        "NVD",
    ],
    "VPN": [
        " VPN",
        "virtual private network",
        "dedicated IP",
    ],
    # Additional OSINT terms
    "OSINT": [
        "open source intelligence",
        "reconnaissance",
        "threat hunting",
    ],
    "infostealer": [
        "info stealer",
        "malware",
        "trojan",
        "RAT",
        "stealer",
    ],
}


def expand_osint_query(query: str, max_variants: int = MAX_VARIANTS) -> list[str]:
    """
    Expand a broad OSINT query into bounded variants for PUBLIC lane coverage.

    Strategy:
      1. Extract keywords that have OSINT expansion terms
      2. For each matched keyword, generate 1-2 variant phrases
      3. Return up to max_variants unique variants

    Args:
        query: Original sprint query string.
        max_variants: Max number of variants to return (default 4, M1 lane budget).

    Returns:
        List of expanded query variants (may be empty).
        Does NOT include the original query — caller adds it separately.

    Invariants:
      - Always-on, no feature flags
      - Deterministic (sorted output)
      - Fail-safe (returns [] on any error)
      - Bounded (max_variants cap)
      - No network I/O, no MLX
    """
    if not query or not query.strip():
        return []

    try:
        query_lower = query.lower()
        tokens = _tokenize(query_lower)
        variants: list[str] = []
        seen: set[str] = set()

        for token in tokens:
            if token in OSINT_EXPANSION_TERMS:
                expansions = OSINT_EXPANSION_TERMS[token]
                # Take up to 2 expansions per matched token
                for expansion in expansions[:2]:
                    # Build variant: replace token with expansion in original query
                    variant = query_lower.replace(token, expansion, 1)
                    if variant != query_lower and variant not in seen:
                        seen.add(variant)
                        variants.append(variant)
                    if len(variants) >= max_variants:
                        return sorted(variants)

        # Also check for multi-word phrases in the query
        for phrase, expansions in OSINT_EXPANSION_TERMS.items():
            if phrase in query_lower:
                for expansion in expansions[:2]:
                    variant = query_lower.replace(phrase, expansion, 1)
                    if variant != query_lower and variant not in seen:
                        seen.add(variant)
                        variants.append(variant)
                    if len(variants) >= max_variants:
                        return sorted(variants)

        return sorted(variants)

    except Exception:
        return []


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenization."""
    return text.split()


def get_expansion_pairs(query: str) -> list[tuple[str, str]]:
    """
    Get all (original_term, expanded_term) pairs for a query.
    Useful for telemetry/debugging.

    Returns:
        List of (matched_keyword, expansion) tuples.
    """
    if not query or not query.strip():
        return []

    try:
        query_lower = query.lower()
        pairs: list[tuple[str, str]] = []

        for token in _tokenize(query_lower):
            if token in OSINT_EXPANSION_TERMS:
                for expansion in OSINT_EXPANSION_TERMS[token][:2]:
                    pairs.append((token, expansion))

        for phrase, expansions in OSINT_EXPANSION_TERMS.items():
            if phrase in query_lower:
                for expansion in expansions[:2]:
                    pairs.append((phrase, expansion))

        return pairs
    except Exception:
        return []
