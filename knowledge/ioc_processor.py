"""IOC Processor — single unified facade for IOC extraction and URL normalization.

Architecture (F350M-R):
    Single facade replacing dual-path architecture:

        - forensics/ioc_extractor.py (cold path) — had broken Rust check
        - core/rust_backend/ioc.py Python domain (cold path fallback)

    This module provides one canonical entry point:
        from hledac.universal.knowledge.ioc_processor import IOCProcessor, fast_ioc_extract

    Hot path (canonical write): knowledge/duckdb_store.py uses
        batch_ioc_extract_unified directly from hledac_rust_extensions.
        NOT routed through here — hot path bypasses Python entirely.

    Cold path (forensic analysis): forensics/ioc_extractor.py delegates to
        this module for fast_ioc_extract, url_normalize, batch_dedup_urls.

M1 8GB: All bounded, fail-safe, no recursion.
"""

from __future__ import annotations

import re as _re
import time as _time
from typing import TYPE_CHECKING

from urllib.parse import parse_qsl as _parse_qsl, urlencode as _urlencode, urlparse as _urlparse

# ─── AccelBackend facade (properly lazy, single probe) ────────────────────────

from hledac.universal.core.rust_backend import get_accel as _get_accel

# ─── Python fallback regexes (pre-compiled, module-level) ──────────────────────

from forensics.ioc_patterns_generated import (  # noqa: F401,E402
    _IOC_COMBINED,
    _IOC_TYPE_NAMES,
    _HASH_VALIDATORS,
    _TRACKING_PARAMS,
)

# ─── IOC type constants ───────────────────────────────────────────────────────

# MODERN-25: "pending" REMOVED from IOC_TYPES — unknown IOC types should be
# preserved with their original type and classification_status="pending_review".
# Using "pending" as an IOC type causes provenance/type information loss.
IOC_TYPES: frozenset[str] = frozenset(
    ("cve", "ip", "ipv4", "ipv6", "hash_sha256", "hash_md5", "onion", "i2p",
     "domain", "apt", "malware", "info_hash", "magnet_uri", "threat_actor",
     "malware_family", "email", "mac", "btc", "eth")
)

# ─── Helpers ───────────────────────────────────────────────────────────────────


def _looks_like_mac(value: str) -> bool:
    """Disambiguate IPv6 compressed form vs MAC address (aa:bb:cc:dd:ee:ff)."""
    sep = ":" if ":" in value else "-"
    parts = value.split(sep)
    return len(parts) == 6 and all(
        len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts
    )


# ─── Python-only IOC extraction (fallback when Rust unavailable) ────────────────


def _python_fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Pure-Python IOC extraction using forensics/ioc_patterns_generated combined regex.

    Named-group single-pass regex — IOC type determined by m.lastgroup,
    no per-type nested loops.
    """
    if not text:
        return []

    iocs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for m in _IOC_COMBINED.finditer(text):
        name = m.lastgroup
        if name is None:
            continue
        value = m.group()
        # Validate hex hashes to prevent false positives (e.g. 40-char string as SHA1)
        if name in _HASH_VALIDATORS and not _HASH_VALIDATORS[name](value):
            continue
        # Disambiguate IPv6 vs MAC
        ioc_type = name
        if name == "ipv6" and _looks_like_mac(value):
            ioc_type = "mac"
        # Normalize: domain, email, MAC, BTC, ETH to lowercase
        if ioc_type in ("domain", "email", "mac", "btc", "eth"):
            value = value.lower()
        key = f"{ioc_type}:{value}"
        if key not in seen:
            seen.add(key)
            iocs.append((value, ioc_type))

    return iocs


def _python_url_normalize(url: str) -> str:
    """Pure-Python URL normalization — mirrors Rust url_engine::normalize()."""
    try:
        trimmed = url.strip()
        if not trimmed:
            return url

        if "://" not in trimmed:
            synthetic = f"http://{trimmed.lstrip('/')}"
        else:
            synthetic = trimmed

        parsed = _urlparse(synthetic)
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
        params = [(k, v) for k, v in _parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
        params.sort()
        if params:
            result += "?" + _urlencode(params)

        return result
    except Exception:
        return url


def _python_batch_dedup_urls(urls: list[str]) -> list[str]:
    """Pure-Python URL dedup with normalization."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = _python_url_normalize(url)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


# ─── IOCProcessor — single unified facade ──────────────────────────────────────


class IOCProcessor:
    """
    Unified IOC extraction facade.

    Uses AccelBackend (get_accel()) for Rust acceleration when available.
    Falls back to pure Python automatically.

    Usage:
        processor = IOCProcessor()
        iocs = processor.extract("example.com 192.168.1.1 test@example.com")

    Or functional API (same underlying logic):
        from hledac.universal.knowledge.ioc_processor import fast_ioc_extract
        iocs = fast_ioc_extract(text)
    """

    __slots__ = ("_accel",)

    def __init__(self) -> None:
        self._accel = _get_accel()

    @property
    def is_rust_available(self) -> bool:
        """True if Rust extension IOC domain is available."""
        try:
            return self._accel.is_available and self._accel.ioc is not None
        except Exception:
            return False

    def extract(self, text: str) -> list[tuple[str, str]]:
        """Extract IOCs from text.

        Uses Rust SIMD extractor when available (Teddy/NEON on M1 ~5× faster).
        Falls back to pure Python named-group combined regex.

        Returns:
            List of (ioc_value, ioc_type) tuples.
        """
        if not text:
            return []

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            # Try SIMD path first (fastest on M1)
            try:
                return ioc_domain.extract_iocs_simd(text)
            except Exception:  # noqa: BLE001
                pass
            # Fallback to basic Rust regex extractor
            try:
                return ioc_domain.extract_iocs_flat(text)
            except Exception:  # noqa: BLE001
                pass

        # Python fallback — named-group combined regex, single pass
        return _python_fast_ioc_extract(text)

    def extract_batch(
        self, texts: list[str]
    ) -> list[list[tuple[str, str]]]:
        """Batch extract IOCs from multiple texts.

        Uses Rust batch extractor when available.
        Falls back to sequential Python extraction.

        Returns:
            List of IOC lists, one per input text, matching input order.
        """
        if not texts:
            return []

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                return ioc_domain.batch_extract_iocs_simd(texts)
            except Exception:  # noqa: BLE001
                pass

        # Python fallback — parallel via ThreadPoolExecutor
        import os as _os
        from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

        n_workers = min(4, _os.cpu_count() or 2)
        with _ThreadPoolExecutor(max_workers=n_workers) as ex:
            return list(ex.map(_python_fast_ioc_extract, texts))

    def extract_indexed(
        self, texts: list[str]
    ) -> list[tuple[int, str, str]]:
        """Indexed batch extract — returns (text_idx, ioc_value, ioc_type).

        Uses Rust indexed batch extractor when available.
        Falls back to sequential Python extraction.

        Returns:
            List of (text_index, ioc_value, ioc_type) tuples.
        """
        if not texts:
            return []

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                return ioc_domain.batch_extract_iocs_simd_indexed(texts)
            except Exception:  # noqa: BLE001
                pass

        # Python fallback
        results: list[tuple[int, str, str]] = []
        for idx, text in enumerate(texts):
            for value, ioc_type in _python_fast_ioc_extract(text):
                results.append((idx, value, ioc_type))
        return results

    def normalize_url(self, url: str) -> str:
        """Normalize URL to canonical form.

        Uses Rust url_normalize when available.
        Falls back to pure Python normalization.
        """
        try:
            url_domain = self._accel.url
            if url_domain is not None:
                return url_domain.normalize(url)
        except Exception:  # noqa: BLE001
            pass

        return _python_url_normalize(url)

    def dedup_urls(self, urls: list[str]) -> list[str]:
        """Deduplicate URLs with normalization.

        Uses Rust batch_dedup_urls when available.
        Falls back to pure Python normalization + dedup.
        """
        if self.is_rust_available:
            try:
                # is_rust_available already guarantees ioc is not None
                return self._accel.ioc.batch_dedup_urls(urls)
            except Exception:  # noqa: BLE001
                pass

        return _python_batch_dedup_urls(urls)

    def extract_to_findings(
        self,
        text: str,
        source_finding_id: str,
        query: str,
        min_confidence: float = 0.5,
    ) -> list:
        """
        Extract IOCs and convert to CanonicalFinding objects.

        Each finding has payload_text in format:
            ioc_type=<type>; value=<value>; parent=<source_finding_id>

        This format is parsed by export/markdown_reporter.py:_parse_forensic_payload().

        M1 8GB safe: bounded at 100 IOCs per extraction.
        """
        iocs = self.extract(text)
        return _iocs_to_findings(iocs, source_finding_id, query, min_confidence)

    def extract_to_findings_bulk(
        self,
        texts: list[str],
        source_finding_ids: list[str],
        queries: list[str],
        min_confidence: float = 0.5,
    ) -> list[list]:
        """
        Bulk extract IOCs and convert to CanonicalFinding lists.

        For each text: extract IOCs → build CanonicalFinding list.

        M1 8GB safe: parallel extraction via ThreadPoolExecutor.
        """
        if not texts or not source_finding_ids or not queries:
            return []
        n = len(texts)
        if len(source_finding_ids) != n or len(queries) != n:
            raise ValueError(
                f"texts, source_finding_ids, and queries must have same length. "
                f"Got {n}, {len(source_finding_ids)}, {len(queries)}"
            )

        # Batch extract all texts
        batch_results = self.extract_batch(texts)

        results: list[list] = []
        for i in range(n):
            iocs = batch_results[i] if i < len(batch_results) else []
            findings = _iocs_to_findings(
                iocs, source_finding_ids[i], queries[i], min_confidence
            )
            results.append(findings)
        return results


# ─── CanonicalFinding builder ───────────────────────────────────────────────────


def _ioc_confidence(ioc_type: str) -> float:
    """Return confidence score for an IOC type."""
    if ioc_type in ("ipv4", "ipv6", "md5", "sha1", "sha256"):
        return 0.9
    if ioc_type in ("domain", "email", "cve", "mac", "btc", "eth"):
        return 0.85
    return 0.7


def _iocs_to_findings(
    iocs: list[tuple[str, str]],
    source_finding_id: str,
    query: str,
    min_confidence: float,
) -> list:
    """Convert IOC list to CanonicalFinding objects."""
    # Import here to avoid circular dependency
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    MAX_IOC_FINDINGS = 100
    findings: list = []
    ts = _time.time()
    provenance: tuple[str, ...] = ("ioc_processor",)

    for ioc_value, ioc_type in iocs[:MAX_IOC_FINDINGS]:
        confidence = _ioc_confidence(ioc_type)
        if confidence < min_confidence:
            continue

        finding = CanonicalFinding(
            finding_id=f"{source_finding_id}_ioc_{len(findings)}",
            query=query,
            source_type="ioc_extraction",
            confidence=confidence,
            ts=ts,
            provenance=provenance,
            payload_text=f"ioc_type={ioc_type}; value={ioc_value}; parent={source_finding_id}",
        )
        findings.append(finding)

    return findings


# ─── Functional API (delegates to IOCProcessor) ───────────────────────────────

# Module-level singleton processor — thread-safe, lazy
_processor: IOCProcessor | None = None
_processor_lock: _threading.Lock | None = None


def _get_processor() -> IOCProcessor:
    global _processor, _processor_lock
    if _processor_lock is None:
        import threading as _threading

        _processor_lock = _threading.Lock()
    if _processor is None:
        with _processor_lock:
            # Double-check after acquiring lock
            if _processor is None:
                _processor = IOCProcessor()
    return _processor


def fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Extract IOCs from text. Uses Rust SIMD or Python fallback."""
    return _get_processor().extract(text)


def batch_ioc_extract(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Batch extract IOCs from multiple texts."""
    return _get_processor().extract_batch(texts)


def indexed_ioc_extract(texts: list[str]) -> list[tuple[int, str, str]]:
    """Indexed batch extract — returns (text_idx, value, type)."""
    return _get_processor().extract_indexed(texts)


def url_normalize(url: str) -> str:
    """Normalize URL to canonical form."""
    return _get_processor().normalize_url(url)


def batch_dedup_urls(urls: list[str]) -> list[str]:
    """Deduplicate URLs with normalization."""
    return _get_processor().dedup_urls(urls)


def extract_to_findings(
    text: str,
    source_finding_id: str,
    query: str,
    min_confidence: float = 0.5,
) -> list:
    """Extract IOCs and convert to CanonicalFinding objects."""
    return _get_processor().extract_to_findings(text, source_finding_id, query, min_confidence)


def extract_to_findings_bulk(
    texts: list[str],
    source_finding_ids: list[str],
    queries: list[str],
    min_confidence: float = 0.5,
) -> list[list]:
    """Bulk extract IOCs and convert to CanonicalFinding objects."""
    return _get_processor().extract_to_findings_bulk(texts, source_finding_ids, queries, min_confidence)


# ─── Backward-compatibility aliases ────────────────────────────────────────────

# Aliases for forensics/ioc_extractor.py backward compatibility
ioc_extract_to_canonical_findings = extract_to_findings
ioc_extract_to_canonical_findings_bulk = extract_to_findings_bulk

# ─── Backward-compatibility re-exports ─────────────────────────────────────────
# forensics/ioc_extractor.py imports these from here — must be present
from forensics.ioc_patterns_generated import (  # noqa: F401,E402,F811
    _IOC_PATTERNS,
    _IOC_COMBINED,
    _HASH_VALIDATORS,
    _TRACKING_PARAMS,
    _IOC_TYPE_NAMES,
)

__all__ = [
    # Classes
    "IOCProcessor",
    # Functional API
    "fast_ioc_extract",
    "batch_ioc_extract",
    "indexed_ioc_extract",
    "url_normalize",
    "batch_dedup_urls",
    "extract_to_findings",
    "extract_to_findings_bulk",
    # Backward compat aliases (forensics/ioc_extractor.py)
    "ioc_extract_to_canonical_findings",
    "ioc_extract_to_canonical_findings_bulk",
    # Constants
    "IOC_TYPES",
    # Forensics patterns re-export (backward compat)
    "_IOC_PATTERNS",
    "_IOC_COMBINED",
    "_HASH_VALIDATORS",
    "_TRACKING_PARAMS",
    "_IOC_TYPE_NAMES",
]
