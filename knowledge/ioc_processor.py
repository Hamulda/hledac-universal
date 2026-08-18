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

from hledac.universal._core.rust_backend import get_accel as _get_accel

# ─── Python fallback regexes (pre-compiled, module-level) ──────────────────────

from forensics.ioc_patterns_generated import (  # noqa: F401,E402
    _IOC_COMBINED,
    _IOC_TYPE_NAMES,
    _HASH_VALIDATORS,
    _TRACKING_PARAMS,
)

# ─── IOC type constants ───────────────────────────────────────────────────────

IOC_TYPES: frozenset[str] = frozenset(
    ("cve", "ip", "ipv4", "ipv6", "hash_sha256", "hash_md5", "onion", "i2p",
     "domain", "apt", "malware", "info_hash", "magnet_uri", "threat_actor",
     "malware_family", "email", "mac", "btc", "eth", "pending")
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


def _python_batch_extract(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Batch extract IOCs from multiple texts using Python fallback.

    This function is designed to be called from rayon CPU pool for parallel execution.
    It processes a chunk of texts and returns the results.

    Args:
        texts: List of texts to extract IOCs from.

    Returns:
        List of IOC lists, one per input text.
    """
    return [_python_fast_ioc_extract(text) for text in texts]


# ─── Rust text normalization fast-path (lazy import, M1 NEON SIMD) ────────────

# Cache entry: (combined_func, batch_nfc, batch_strip)
# combined_func = batch_nfc_and_strip_diacritics_fast (single pass, preferred)
# batch_nfc, batch_strip = separate fallback functions
_RUST_TEXT_FAST: tuple[object, object, object] | None = None  # (combined, batch_nfc, batch_strip)


def _get_rust_text_fast() -> tuple[object, object, object] | None:
    """Lazy-load Rust NEON fast-path for text normalization.

    Returns (combined, batch_nfc, batch_strip) where:
    - combined: batch_nfc_and_strip_diacritics_fast (single pass, preferred)
    - batch_nfc: batch_nfc_normalize_fast
    - batch_strip: batch_strip_diacritics_fast

    Returns None if Rust unavailable.
    """
    global _RUST_TEXT_FAST
    if _RUST_TEXT_FAST is not None:
        return _RUST_TEXT_FAST
    try:
        from hledac.universal._core.rust_backend import rust
        combined = rust.raw.batch_nfc_and_strip_diacritics_fast
        batch_nfc = rust.raw.batch_nfc_normalize_fast
        batch_strip = rust.raw.batch_strip_diacritics_fast
        if combined is not None:
            # Prefer combined single-pass function
            _RUST_TEXT_FAST = (combined, batch_nfc, batch_strip)
            return _RUST_TEXT_FAST
        elif batch_nfc is not None and batch_strip is not None:
            # Fallback to separate functions
            _RUST_TEXT_FAST = (None, batch_nfc, batch_strip)
            return _RUST_TEXT_FAST
    except Exception:  # noqa: BLE001
        pass
    _RUST_TEXT_FAST = None
    return None


def _rust_text_fast_single(text: str) -> str:
    """Fast single-text normalization via Rust NEON SIMD (NFC + strip diacritics).

    Falls back to Python on any error. M1 8GB safe: GIL released during rayon.
    """
    rust_fast = _get_rust_text_fast()
    if rust_fast is None:
        import unicodedata
        try:
            nfkd = unicodedata.normalize("NFKD", text)
            return "".join(c for c in nfkd if not unicodedata.combining(c))
        except Exception:  # noqa: BLE001
            return text

    combined, batch_nfc, batch_strip = rust_fast
    try:
        if combined is not None:
            # Use combined single-pass function (most efficient)
            return combined([text])[0]
        else:
            # Fallback: two separate calls
            normalized = batch_nfc([text])[0]
            return batch_strip([normalized])[0]
    except Exception:  # noqa: BLE001
        import unicodedata
        try:
            nfkd = unicodedata.normalize("NFKD", text)
            return "".join(c for c in nfkd if not unicodedata.combining(c))
        except Exception:  # noqa: BLE001
            return text


def _python_url_normalize(url: str) -> str:
    """Pure-Python URL normalization — mirrors Rust url_engine::normalize()."""
    try:
        trimmed = url.strip()
        if not trimmed:
            return url

        # NEON SIMD fast-path: NFC normalize + strip diacritics for non-ASCII IOCs
        # 3-5× faster than Python unicodedata on M1 for multikulturní IOC datasety
        if not trimmed.isascii():
            trimmed = _rust_text_fast_single(trimmed)

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
    """Batch URL dedup with normalization.

    Uses Rust NEON SIMD fast-path (batch_nfc_and_strip_diacritics_fast when available,
    otherwise batch_nfc_normalize_fast + batch_strip_diacritics_fast) for 3-5× speedup
    on non-ASCII IOC datasets. Falls back to sequential Python.
    """
    if not urls:
        return []

    seen: set[str] = set()
    result: list[str] = []

    # Rust fast-path: batch NFC + strip diacritics for all non-ASCII URLs
    rust_fast = _get_rust_text_fast()
    if rust_fast is not None:
        try:
            combined, batch_nfc, batch_strip = rust_fast
            if combined is not None:
                # Use combined single-pass function (most efficient)
                normalized = combined(urls)
            else:
                # Fallback: two separate batch calls
                normalized = batch_strip(batch_nfc(urls))
            for norm in normalized:
                if norm not in seen:
                    seen.add(norm)
                    result.append(norm)
            return result
        except Exception:  # noqa: BLE001
            pass  # Fall through to sequential Python

    # Python fallback: sequential normalization
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

        # Python fallback — rayon parallel extraction
        # ISSUE-10 FIX: Use asyncio.Runner() instead of deprecated get_event_loop().run_until_complete()
        import asyncio as _asyncio

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running — use asyncio.Runner() (Python 3.11+)
            with _asyncio.Runner() as runner:
                return runner.run(self.extract_indexed_async(texts))
        else:
            # Running loop detected — schedule on running loop
            return _asyncio.run_coroutine_threadsafe(
                self.extract_indexed_async(texts), loop
            ).result()

    async def extract_indexed_async(
        self, texts: list[str]
    ) -> list[tuple[int, str, str]]:
        """Async indexed batch extract with rayon parallelism.

        ISSUE-006 FIX: Added rayon parallelization for Python fallback.
        Uses Rust indexed batch extractor when available (hot path).
        Falls back to rayon-based parallel extraction when Rust unavailable.

        Args:
            texts: List of texts to extract IOCs from.

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

        # Python fallback with rayon parallelism
        try:
            from hledac.universal.utils.sync_bridge import to_thread_rayon

            def _extract_with_index(args: tuple[int, str]) -> list[tuple[int, str, str]]:
                """Extract IOCs from a single text with its index."""
                idx, text = args
                return [(idx, value, ioc_type) for value, ioc_type in _python_fast_ioc_extract(text)]

            # Process in chunks to avoid memory pressure on M1 8GB
            chunk_size = min(100, len(texts))
            results: list[tuple[int, str, str]] = []

            for i in range(0, len(texts), chunk_size):
                chunk = [(i + j, texts[i + j]) for j in range(min(chunk_size, len(texts) - i))]
                # Dispatch to rayon CPU pool for parallel extraction
                chunk_results = await to_thread_rayon(
                    "cpu",
                    _extract_with_index,
                    (chunk,),
                    timeout=30.0,
                )
                results.extend(chunk_results)

            return results
        except (ImportError, RuntimeError):
            # Fallback: rayon unavailable — sequential extraction
            results: list[tuple[int, str, str]] = []
            for idx, text in enumerate(texts):
                for value, ioc_type in _python_fast_ioc_extract(text):
                    results.append((idx, value, ioc_type))
            return results

    async def extract_batch_async(
        self, texts: list[str]
    ) -> list[list[tuple[str, str]]]:
        """Async batch extract with rayon parallelism for Python fallback.

        Uses Rust batch extractor when available (hot path).
        Falls back to rayon-based parallel extraction when Rust unavailable.

        ISSUE-006: Uses to_thread_rayon() from utils/sync_bridge.py for
        ~5μs/task dispatch overhead vs ~500μs/task with ThreadPoolExecutor.

        Args:
            texts: List of texts to extract IOCs from.

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

        # Python fallback with rayon parallelism
        try:
            from hledac.universal.utils.sync_bridge import to_thread_rayon

            # Process in chunks to avoid memory pressure on M1 8GB
            chunk_size = min(100, len(texts))
            results: list[list[tuple[str, str]]] = []

            for i in range(0, len(texts), chunk_size):
                chunk = texts[i : i + chunk_size]
                # Dispatch to rayon CPU pool for parallel extraction
                result = await to_thread_rayon(
                    "cpu",
                    _python_batch_extract,
                    (chunk,),
                    timeout=30.0,
                )
                results.extend(result)

            return results
        except (ImportError, RuntimeError):
            # Fallback: rayon unavailable — use ThreadPoolExecutor
            import os as _os
            from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

            n_workers = min(4, _os.cpu_count() or 2)
            with _ThreadPoolExecutor(max_workers=n_workers) as ex:
                return list(ex.map(_python_fast_ioc_extract, texts))

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

        ISSUE-006 FIX: Uses extract_batch_async() for rayon parallelism instead
        of sync extract_batch(). Falls back to ThreadPoolExecutor if no event loop.

        M1 8GB safe: parallel extraction via rayon or ThreadPoolExecutor.
        """
        if not texts or not source_finding_ids or not queries:
            return []
        n = len(texts)
        if len(source_finding_ids) != n or len(queries) != n:
            raise ValueError(
                f"texts, source_finding_ids, and queries must have same length. "
                f"Got {n}, {len(source_finding_ids)}, {len(queries)}"
            )

        # ISSUE-006 FIX: Use async batch extract for rayon parallelism
        # ISSUE-10 FIX: Use asyncio.Runner() instead of deprecated get_event_loop().run_until_complete()
        import asyncio as _asyncio

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running — use asyncio.Runner() (Python 3.11+)
            with _asyncio.Runner() as runner:
                batch_results = runner.run(self.extract_batch_async(texts))
        else:
            # Running loop detected — schedule on running loop
            batch_results = _asyncio.run_coroutine_threadsafe(
                self.extract_batch_async(texts), loop
            ).result()

        results: list[list] = []
        for i in range(n):
            iocs = batch_results[i] if i < len(batch_results) else []
            findings = _iocs_to_findings(
                iocs, source_finding_ids[i], queries[i], min_confidence
            )
            results.append(findings)
        return results

    async def extract_to_findings_bulk_async(
        self,
        texts: list[str],
        source_finding_ids: list[str],
        queries: list[str],
        min_confidence: float = 0.5,
    ) -> list[list]:
        """
        Async bulk extract IOCs and convert to CanonicalFinding lists.

        ISSUE-006 FIX: Full async version using extract_batch_async() for rayon parallelism.

        For each text: extract IOCs → build CanonicalFinding list.

        M1 8GB safe: parallel extraction via rayon CPU pool.
        """
        if not texts or not source_finding_ids or not queries:
            return []
        n = len(texts)
        if len(source_finding_ids) != n or len(queries) != n:
            raise ValueError(
                f"texts, source_finding_ids, and queries must have same length. "
                f"Got {n}, {len(source_finding_ids)}, {len(queries)}"
            )

        # Use async batch extract for rayon parallelism
        batch_results = await self.extract_batch_async(texts)

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


async def batch_ioc_extract_async(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Async batch extract IOCs with rayon parallelism for Python fallback.

    Uses Rust batch extractor when available (hot path).
    Falls back to rayon-based parallel extraction when Rust unavailable.

    ISSUE-006: Uses to_thread_rayon() from utils/sync_bridge.py for
    ~5μs/task dispatch overhead vs ~500μs/task with ThreadPoolExecutor.

    Args:
        texts: List of texts to extract IOCs from.

    Returns:
        List of IOC lists, one per input text, matching input order.
    """
    return await _get_processor().extract_batch_async(texts)


def indexed_ioc_extract(texts: list[str]) -> list[tuple[int, str, str]]:
    """Indexed batch extract — returns (text_idx, value, type)."""
    return _get_processor().extract_indexed(texts)


async def indexed_ioc_extract_async(texts: list[str]) -> list[tuple[int, str, str]]:
    """Async indexed batch extract with rayon parallelism for Python fallback.

    ISSUE-006: Added rayon parallelization for Python fallback.
    """
    return await _get_processor().extract_indexed_async(texts)


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


async def extract_to_findings_bulk_async(
    texts: list[str],
    source_finding_ids: list[str],
    queries: list[str],
    min_confidence: float = 0.5,
) -> list[list]:
    """Async bulk extract IOCs with rayon parallelism.

    ISSUE-006: Full async version using extract_batch_async() for rayon parallelism.
    """
    return await _get_processor().extract_to_findings_bulk_async(
        texts, source_finding_ids, queries, min_confidence
    )


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
    "batch_ioc_extract_async",
    "indexed_ioc_extract",
    "indexed_ioc_extract_async",
    "url_normalize",
    "batch_dedup_urls",
    "extract_to_findings",
    "extract_to_findings_bulk",
    "extract_to_findings_bulk_async",
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
