"""IOC Extractor — Rust-powered high-performance IOC extraction.

Rust bindings for:
- fast_ioc_extract: regex-based IOC extraction (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE)
- url_normalize: canonical URL normalization
- batch_dedup_urls: in-memory URL dedup with normalization

Falls back to pure Python if Rust extension unavailable.
"""

import re as _re
from urllib.parse import parse_qsl, urlencode, urlparse

# F265C: Use centralized rust backend
_RUST_IOC_AVAILABLE = False
try:
    from core.rust_backend import rust as _rust_backend  # noqa: E402

    _RUST_IOC_AVAILABLE = (
        _rust_backend.is_available
        and _rust_backend.ioc is not None
        and _rust_backend.url is not None
    )
except ImportError:
    pass

# Python fallback regexes — generated from rust_extensions/src/ioc_patterns.rs (single source of truth).
# Issue #031: Removed duplicate _IOC_PATTERNS, _IOC_COMBINED, _HASH_VALIDATORS, _TRACKING_PARAMS.
# These are now imported from ioc_patterns_generated.py which is codegen'd from Rust.
from forensics.ioc_patterns_generated import (  # noqa: E402,F401
    _IOC_PATTERNS,
    _IOC_COMBINED,
    _HASH_VALIDATORS,
    _TRACKING_PARAMS,
    _IOC_TYPE_NAMES,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def _looks_like_mac(value: str) -> bool:
    """Check if value matches MAC address format (6 hex pairs, : or - separator).

    Issue #4: IPv6 compressed form can match MAC-style strings like aa:bb:cc:dd:ee:ff.
    This disambiguator runs after the regex match to correct false positives.
    """
    sep = ":" if ":" in value else "-"
    parts = value.split(sep)
    return len(parts) == 6 and all(
        len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts
    )


def fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Extract IOCs. Uses Rust SIMD extractor (Teddy/NEON on M1) when available.

    Priority:
    1. extract_iocs_simd — regex-automata with Teddy SIMD (M1 NEON ~5× faster)
    2. fast_ioc_extract — basic regex (fallback if SIMD unavailable)
    3. Python fallback — named-group combined regex single-pass (Issue #3: was 7 sequential loops)
    """
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        ioc = _rust_backend.ioc
        # Prefer SIMD extractor (Teddy/NEON on M1) for bulk text
        if hasattr(ioc, "extract_iocs_simd"):
            return ioc.extract_iocs_simd(text)
        # Fallback to basic Rust regex extractor
        return ioc.fast_ioc_extract(text)
    # Python fallback — Issue #3: named-group combined regex, single finditer pass.
    # Named groups eliminate the nested loop that rescanned text for every RegexSet match.
    iocs = []
    seen: set[str] = set()

    for m in _IOC_COMBINED.finditer(text):
        # Determine which named group matched — this tells us the IOC type directly.
        for name in _IOC_TYPE_NAMES:
            if m.lastgroup == name:
                value = m.group()
                # Validate hex hashes to prevent false positives (e.g. 'abc...40chars' as SHA1)
                if name in _HASH_VALIDATORS and not _HASH_VALIDATORS[name](value):
                    break
                # Issue #4: Disambiguate IPv6 vs MAC — IPv6 compressed form matches
                # MAC-style strings (aa:bb:cc:dd:ee:ff). Reclassify to MAC.
                ioc_type = name
                if name == "ipv6" and _looks_like_mac(value):
                    ioc_type = "mac"
                # Normalize: domain, email, MAC, BTC, ETH to lowercase
                if ioc_type in ("domain", "email", "mac", "btc", "eth"):
                    value = value.lower()
                if value not in seen:
                    seen.add(value)
                    iocs.append((value, ioc_type))
                break

    return iocs


def url_normalize(url: str) -> str:
    """Normalize URL. Uses Rust url_normalize when available."""
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        return _rust_backend.url.normalize(url)
    # Python fallback — mirrors Rust url_engine::normalize() behavior.
    try:
        trimmed = url.strip()
        if not trimmed:
            return url

        # Scheme-less rescue (same as Rust: try synthetic http:// prefix)
        if "://" not in trimmed:
            synthetic = f"http://{trimmed.lstrip('/')}"
        else:
            synthetic = trimmed

        parsed = urlparse(synthetic)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        path = parsed.path or "/"

        # Strip default ports (mirrors Rust url_engine)
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None

        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += path

        # Filter tracking params, sort, encode (mirrors Rust url_engine)
        params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
        params.sort()
        if params:
            result += "?" + urlencode(params)

        return result
    except Exception:
        return url


def batch_dedup_urls(urls: list[str]) -> list[str]:
    """Deduplicate URLs. Uses Rust batch_dedup_urls when available."""
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        return _rust_backend.url.batch_dedup_urls(urls)
    # Python fallback
    seen = set()
    result = []
    for url in urls:
        normalized = url_normalize(url)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def ioc_extract_to_canonical_findings(
    text: str,
    source_finding_id: str,
    query: str,
    min_confidence: float = 0.5,
) -> list:
    """
    Extract IOCs from text and convert to CanonicalFinding objects.

    Each finding has payload_text in format:
        ioc_type=<type>; value=<value>; parent=<source_finding_id>

    This format is parsed by export/markdown_reporter.py:_parse_forensic_payload().

    M1 8GB safe: bounded at 100 IOCs per extraction, no recursion,
    no regex compilation, pure Python path uses pre-compiled regexes.

    ISSUE-018: For bulk extraction (n > 1), prefer ioc_extract_to_canonical_findings_bulk()
    which uses Rust arrow_batch_builder for -90% allocation pressure.
    """
    import time

    # Use fast_ioc_extract for extraction (Rust or Python fallback)
    iocs = fast_ioc_extract(text)

    # M1 8GB: hard cap on number of findings per extraction
    MAX_IOC_FINDINGS = 100

    # Import here to avoid circular dependency at module level
    from knowledge.duckdb_store import CanonicalFinding

    findings = []
    for ioc_value, ioc_type in iocs[:MAX_IOC_FINDINGS]:
        # Confidence based on IOC type certainty
        # Issue #4: Added MAC, BTC, ETH — high-value blockchain/hardware IOCs
        if ioc_type in ("ipv4", "ipv6", "md5", "sha1", "sha256"):
            confidence = 0.9
        elif ioc_type in ("domain", "email", "cve", "mac", "btc", "eth"):
            confidence = 0.85
        else:
            confidence = 0.7

        if confidence < min_confidence:
            continue

        finding = CanonicalFinding(
            finding_id=f"{source_finding_id}_ioc_{len(findings)}",
            query=query,
            source_type="ioc_extraction",
            confidence=confidence,
            ts=time.time(),
            provenance=("ioc_extractor",),
            payload_text=f"ioc_type={ioc_type}; value={ioc_value}; parent={source_finding_id}",
        )
        findings.append(finding)

    return findings


# ISSUE-018: Rust arrow_batch_builder for bulk IOC → CanonicalFinding conversion
# Zero-copy: Rust builds Arrow IPC bytes directly, Python deserializes once.
_RUST_BUILD_FINDINGS_FROM_IOCS_OPT = None


def _get_rust_build_findings_from_iocs():
    """Lazy getter for Rust build_findings_from_iocs."""
    global _RUST_BUILD_FINDINGS_FROM_IOCS_OPT
    if _RUST_BUILD_FINDINGS_FROM_IOCS_OPT is None:
        try:
            from hledac_rust_extensions import build_findings_from_iocs
            _RUST_BUILD_FINDINGS_FROM_IOCS_OPT = build_findings_from_iocs
        except ImportError:
            _RUST_BUILD_FINDINGS_FROM_IOCS_OPT = None
    return _RUST_BUILD_FINDINGS_FROM_IOCS_OPT


def ioc_extract_to_canonical_findings_bulk(
    texts: list[str],
    source_finding_ids: list[str],
    queries: list[str],
    min_confidence: float = 0.5,
) -> list[list]:
    """
    Bulk extract IOCs from multiple texts and convert to CanonicalFinding lists.

    ISSUE-018 fix: Replaces sequential O(n) CanonicalFinding allocation storm
    with a single Rust arrow_batch_builder call per text.

    Architecture:
        For each text:
            fast_ioc_extract(text) → [(value, ioc_type), ...]  # (value, type) tuple order
            build_findings_from_iocs(iocs, source_finding_id, query) → Arrow IPC bytes
            pa.ipc.open_record_batch_reader(bytes) → CanonicalFinding list

    Expected: 5-10x speedup, -90% allocation pressure vs sequential Python.

    Args:
        texts: List of text strings to extract IOCs from
        source_finding_ids: List of parent finding IDs (same length as texts)
        queries: List of research queries (same length as texts)
        min_confidence: Minimum confidence threshold (default 0.5)

    Returns:
        List of lists of CanonicalFinding objects (one list per input text)
    """
    if not texts or not source_finding_ids or not queries:
        return []

    n = len(texts)
    if len(source_finding_ids) != n or len(queries) != n:
        raise ValueError(
            f"texts, source_finding_ids, and queries must have same length. "
            f"Got {n}, {len(source_finding_ids)}, {len(queries)}"
        )

    # Check for pyarrow availability
    try:
        import pyarrow as pa
    except ImportError:
        pa = None

    rust_fn = _get_rust_build_findings_from_iocs()
    results: list[list] = []

    for i in range(n):
        text = texts[i]
        source_finding_id = source_finding_ids[i]
        query = queries[i]

        # Extract IOCs via Rust (or Python fallback)
        iocs = fast_ioc_extract(text)

        MAX_IOC_FINDINGS = 100
        iocs = iocs[:MAX_IOC_FINDINGS]

        if not iocs:
            results.append([])
            continue

        # Filter by confidence before calling Rust
        filtered_iocs = []
        for ioc_value, ioc_type in iocs:
            if ioc_type in ("ipv4", "ipv6", "md5", "sha1", "sha256"):
                confidence = 0.9
            elif ioc_type in ("domain", "email", "cve", "mac", "btc", "eth"):
                confidence = 0.85
            else:
                confidence = 0.7

            if confidence >= min_confidence:
                # fast_ioc_extract returns (value, type), keep consistent ordering
                filtered_iocs.append((ioc_value, ioc_type))

        if not filtered_iocs:
            results.append([])
            continue

        # Try Rust Arrow path first
        if rust_fn is not None and pa is not None:
            try:
                import time

                # Build Arrow IPC bytes via Rust
                ipc_bytes = rust_fn(filtered_iocs, source_finding_id, query)
                if ipc_bytes and len(ipc_bytes) > 8:
                    # Deserialize Arrow IPC bytes to CanonicalFinding list
                    reader = pa.ipc.open_record_batch_reader(ipc_bytes)
                    table = reader.read_next_batch()

                    from knowledge.duckdb_store import CanonicalFinding

                    findings = []
                    ts = time.time()
                    provenance = ("ioc_extractor",)
                    # provenance_json (column 5) contains JSON with ioc_type and value
                    provenance_col = table.column(5) if table.num_columns > 5 else None
                    for row_idx in range(table.num_rows):
                        # provenance_json stores JSON: {"ioc_type":"...", "value":"...", "parent":"..."}
                        prov_json = provenance_col[row_idx].as_py() if provenance_col else ""
                        # Build canonical payload_text from the original format
                        # This format is parsed by export/markdown_reporter.py:_parse_forensic_payload()
                        if prov_json and "ioc_type" in str(prov_json):
                            try:
                                import orjson
                                parsed = orjson.loads(prov_json)
                                ioc_type_str = parsed.get("ioc_type", "")
                                ioc_value_str = parsed.get("value", "")
                                parent_str = parsed.get("parent", source_finding_id)
                                payload_text = f"ioc_type={ioc_type_str}; value={ioc_value_str}; parent={parent_str}"
                            except Exception:
                                payload_text = str(prov_json)
                        else:
                            payload_text = str(prov_json)

                        finding = CanonicalFinding(
                            finding_id=f"{source_finding_id}_ioc_{row_idx + 1}",
                            query=query,
                            source_type="ioc_extraction",
                            confidence=table.column(3)[row_idx].as_py(),
                            ts=ts,
                            provenance=provenance,
                            payload_text=payload_text,
                        )
                        findings.append(finding)

                    results.append(findings)
                    continue
            except Exception:
                pass

        # Fallback: sequential Python (original implementation)
        from knowledge.duckdb_store import CanonicalFinding
        import time

        findings = []
        ts = time.time()
        for idx, (ioc_value, ioc_type) in enumerate(filtered_iocs):
            if ioc_type in ("ipv4", "ipv6", "md5", "sha1", "sha256"):
                confidence = 0.9
            elif ioc_type in ("domain", "email", "cve", "mac", "btc", "eth"):
                confidence = 0.85
            else:
                confidence = 0.7

            finding = CanonicalFinding(
                finding_id=f"{source_finding_id}_ioc_{idx + 1}",
                query=query,
                source_type="ioc_extraction",
                confidence=confidence,
                ts=ts,
                provenance=("ioc_extractor",),
                payload_text=f"ioc_type={ioc_type}; value={ioc_value}; parent={source_finding_id}",
            )
            findings.append(finding)

        results.append(findings)

    return results


# IOC_FINDINGS_MAX was referenced in __all__ but never existed — bounded constant for doc purposes
IOC_FINDINGS_MAX = 100


__all__ = [
    "_RUST_IOC_AVAILABLE",
    "fast_ioc_extract",
    "url_normalize",
    "batch_dedup_urls",
    "ioc_extract_to_canonical_findings",
    "ioc_extract_to_canonical_findings_bulk",
    "IOC_FINDINGS_MAX",
]
