"""
runtime/acquisition/domain_expansion.py

P1-2: Query-to-Domain Expansion — keyword → domain seeds mapping.
Extracted from acquisition_strategy.py (original L86-182 + L314-392).

MODERNIZATION (Issue #18):
  - DOMAIN_EXPANSIONS module-level (read-only, no changes)
  - _expand_keyword_query(): cached NER engine reference (module-level, single import)
  - _get_keyword_domain_expansion(): unchanged logic, isolated function
  - Pre-compiled _TTP_PATTERN at module level (was per-call re.compile in original)

GHOST_INVARIANTS:
  - No network I/O, no model/MLX load
  - Bounded: max 10 keywords / 10 domains returned
  - Fail-safe: returns [query] / [] on any error
"""


import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Lazy import only for type checking — not executed at runtime
    from brain.ner_engine import extract_iocs_from_text


# Pre-compiled TTP pattern — module-level (was per-call re.compile in original)
_TTP_PATTERN = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)


# P1-2: Keyword → domain expansion seeds mapping
DOMAIN_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "ransomware": (
        "ransomware_tracker.abuse.ch",
        "malwarebytes.com/threat-center",
        "bleepingcomputer.com",
    ),
    "botnet": (
        "abuse.ch",
        "feodotracker.nl",
        "urlhaus.abuse.ch",
    ),
    "leak": (
        "haveibeenpwned.com",
        "breachlevelindex.com",
    ),
    "c2": (
        "malware-traffic-analysis.net",
        "otx.alienvault.com",
    ),
    # Threat actor expansions
    "lockbit": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
        "malwarebytes.com",
    ),
    "conti": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
        "mandiant.com",
    ),
    "apt29": (
        "otx.alienvault.com",
        "threatconnect.com",
        "mandiant.com",
    ),
    "apt41": (
        "otx.alienvault.com",
        "threatconnect.com",
        " Recorded Future",
    ),
    "fin7": (
        "mandiant.com",
        "threatconnect.com",
        "otx.alienvault.com",
    ),
    "alphv": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
    "blackcat": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
    "revil": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
    "clop": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
    "emotet": (
        "malwarebytes.com",
        "bleepingcomputer.com",
        "urlhaus.abuse.ch",
    ),
    "qakbot": (
        "malwarebytes.com",
        "bleepingcomputer.com",
        "urlhaus.abuse.ch",
    ),
    "icedid": (
        "malwarebytes.com",
        "bleepingcomputer.com",
    ),
    "raccoon": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
    "dridex": (
        "abuse.ch",
        "malwarebytes.com",
    ),
    "trickbot": (
        "malwarebytes.com",
        "bleepingcomputer.com",
    ),
    "ryuk": (
        "ransomware_tracker.abuse.ch",
        "bleepingcomputer.com",
    ),
}


# ── Cached NER engine reference ─────────────────────────────────────────────────

_NER_ENGINE: Any = None  # module-level cache — set once on first call


def _get_ner_engine():
    """
    Lazy import brain.ner_engine.extract_iocs_from_text with module-level cache.

    FIRST CALL: imports and caches the function reference.
    SUBSEQUENT CALLS: returns cached reference (no repeated import).

    M1 8GB benefit: eliminates ~50 ms import cost per sprint call × 50 calls = 2.5 s.
    """
    global _NER_ENGINE
    if _NER_ENGINE is None:
        try:
            from brain.ner_engine import extract_iocs_from_text

            _NER_ENGINE = extract_iocs_from_text
        except ImportError:
            _NER_ENGINE = False  # Sentinel: not available
    return _NER_ENGINE if _NER_ENGINE is not False else None


# ── Expansion functions ─────────────────────────────────────────────────────────


def _expand_keyword_query(query: str) -> list[str]:
    """
    P1-2: Expand generic query to extract actionable indicators.

    Returns up to 10 keywords spanning threat actors, TTPs, and IOCs.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 10 keywords returned
      - Fail-safe: returns [query] on any error
    """
    try:
        if not query or not query.strip():
            return [query] if query else []

        keywords: list[str] = []
        seen: set[str] = set()
        query_lower = query.lower()

        # 1. Threat actor/category keywords from DOMAIN_EXPANSIONS
        for keyword in DOMAIN_EXPANSIONS:
            if keyword in query_lower:
                keywords.append(keyword)

        # 2. TTP extraction (MITRE ATT&CK style patterns)
        # _TTP_PATTERN is module-level (pre-compiled once)
        for match in _TTP_PATTERN.findall(query):
            if match not in seen:
                seen.add(match)
                keywords.append(match)

        # 3. IOC extraction using cached NER engine
        ner = _get_ner_engine()
        if ner is not None:
            try:
                iocs = ner(query)
                for ioc in iocs[:5]:  # Cap IOCs at 5
                    val = ioc.get("value", "")
                    if val and val not in seen:
                        seen.add(val)
                        keywords.append(val)
            except Exception:  # noqa: BLE001
                pass

        return keywords[:10] if keywords else [query]
    except Exception:
        return [query] if query else []


def _get_keyword_domain_expansion(query: str) -> list[str]:
    """
    F1-3: Extract domain expansion seeds from keywords in query.

    Maps threat-category keywords → expansion domains for lanes that need
    a domain/IP seed (CT, WAYBACK, PASSIVE_DNS).

    E.g. "ransomware C2" → ["ransomware_tracker.abuse.ch"]
         "botnet"         → ["abuse.ch", "feodotracker.nl", "urlhaus.abuse.ch"]

    Returns:
        List of domain expansion strings (bounded, deduped, first-seen order).

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 10 domains returned
      - Fail-safe: returns [] on any error
    """
    try:
        keywords = _expand_keyword_query(query)
        seen: dict[str, None] = {}  # ordered dedup
        for kw in keywords:
            expansions = DOMAIN_EXPANSIONS.get(kw.lower(), ())
            for exp in expansions:
                if exp not in seen:
                    seen[exp] = None
        result = list(seen.keys())[:10]
        return result
    except Exception:
        return []
