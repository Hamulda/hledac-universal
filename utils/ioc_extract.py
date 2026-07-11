"""
IOC Extraction utilities — pure Python, no Kuzu dependency.

Moved from knowledge/ioc_graph.py (F300-GRAPH) to decouple IOC extraction
from the Kuzu-backed graph. These functions are used by DuckDB canonical
write path (duckdb_store.py) and must remain Kuzu-free.
"""


from concurrent.futures import ThreadPoolExecutor, as_completed
from re import compile as re_compile

# IOC type enumeration
IOC_TYPES: frozenset[str] = frozenset(
    ("cve", "ip", "hash_sha256", "hash_md5", "onion", "i2p", "domain", "apt", "malware", "info_hash", "magnet_uri", "threat_actor", "malware_family", "pending")
)

# ---------------------------------------------------------------------------
# Compiled regex constants — module-level (never inside functions)
# ---------------------------------------------------------------------------
_RE_IP_PUBLIC = re_compile(
    r"\b(?!10\.|127\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)"
    r"(?:\d{1,3}\.){3}\d{1,3}\b"
)
_RE_SHA256 = re_compile(r"\b[0-9a-fA-F]{64}\b")
_RE_ONION_V3 = re_compile(r"\b[a-z2-7]{56}\.onion\b")
_RE_ONION_V2 = re_compile(r"\b[a-z2-7]{16}\.onion\b")


def _make_ioc_id(ioc_type: str, value: str) -> str:
    """Generate a deterministic 64-bit hex ID for an IOC."""
    import xxhash

    return f"{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}"


def extract_iocs_from_text(
    text: str, pattern_matches: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """
    Extract IOCs from raw text and PatternMatcher hits.

    Returns list of (value, ioc_type) tuples, deduplicated.
    Private/routable IPs are filtered out.
    """
    results: list[tuple[str, str]] = []

    # From PatternMatcher labeled hits
    for match_value, label in pattern_matches:
        if label == "vulnerability_id":
            results.append((match_value, "cve"))
        elif label == "offensive_tool":
            results.append((match_value, "malware"))
        elif label == "attack_technique":
            results.append((match_value, "apt"))
        elif label == "ransomware_group":
            results.append((match_value, "malware"))

    # From regex extraction
    for m in _RE_IP_PUBLIC.finditer(text):
        results.append((m.group(), "ip"))
    for m in _RE_SHA256.finditer(text):
        results.append((m.group().lower(), "hash_sha256"))
    for m in _RE_ONION_V3.finditer(text):
        results.append((m.group(), "onion"))
    for m in _RE_ONION_V2.finditer(text):
        results.append((m.group(), "onion"))

    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in results:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


# Thread pool for parallel IOC extraction — M1 8GB: 4 workers
# Lazily initialized to avoid import-time overhead
_ioc_extractor: ThreadPoolExecutor | None = None


def _get_ioc_extractor() -> ThreadPoolExecutor:
    """Lazy singleton for IOC extractor thread pool."""
    global _ioc_extractor
    if _ioc_extractor is None:
        _ioc_extractor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ioc_extractor")
    return _ioc_extractor


# IOC classification — seed vs discovered
# Sprint F320: classify-before-insert pipeline step
# Only actual query seeds are seeds — ct_log/feed/public are discovered intelligence
_SEED_SOURCE_TYPES: frozenset[str] = frozenset(
    ("query", "seed", "onion_seed", "domain_seed", "ip_seed")
)
# High-confidence IOC types that are always seeds when from query context
_SEED_IOC_TYPES: frozenset[str] = frozenset(("domain", "ip", "onion", "i2p"))


def classify_ioc(ioc_type: str, source_type: str) -> str:
    """
    Classify an IOC as 'seed' or 'discovered'.

    Sprint F320: identity classify-before-insert pipeline step.
    Called during graph accumulation — BEFORE upsert_ioc().

    Args:
        ioc_type:   IOC type from finding (e.g. "ip", "domain", "hash_sha256").
        source_type: Source type from finding (e.g. "public", "ct_log", "feed").

    Returns:
        "seed" or "discovered"
    """
    # Query-seeded IOC types are always seeds (the query IS the seed)
    if ioc_type in _SEED_IOC_TYPES and source_type == "query":
        return "seed"
    if source_type in _SEED_SOURCE_TYPES:
        return "seed"
    return "discovered"


MAX_EXTRACT_BATCH: int = 500  # max findings per extraction batch


def extract_iocs_batch(
    items: list[tuple[str, list[tuple[str, str]]]],
) -> list[list[tuple[str, str]]]:
    """
    Batch extract IOCs from multiple texts in parallel using ThreadPoolExecutor.

    Architecture:
        - Parallel O(n) regex scans across N texts (4 worker threads)
        - Bounded: MAX_EXTRACT_BATCH per call (memory guard)
        - Fail-soft: individual text failures return [] not exceptions
        - Returns: list of result lists, matching input order

    Args:
        items: List of (text, pattern_matches) tuples.

    Returns:
        List of (ioc_value, ioc_type) lists per input text.
    """
    if not items:
        return []

    # Memory guard: cap batch size
    items = items[:MAX_EXTRACT_BATCH]

    def _extract_one(
        item: tuple[str, list[tuple[str, str]]],
    ) -> list[tuple[str, str]]:
        text, matches = item
        try:
            return extract_iocs_from_text(text, matches)
        except Exception:
            return []

    results: list[list[tuple[str, str]] | None] = [None] * len(items)

    futures = {
        _get_ioc_extractor().submit(_extract_one, item): i for i, item in enumerate(items)
    }
    for future in as_completed(futures):
        idx = futures[future]
        try:
            results[idx] = future.result()
        except Exception:
            results[idx] = []

    # Replace None placeholders with empty lists
    return [r if r is not None else [] for r in results]
