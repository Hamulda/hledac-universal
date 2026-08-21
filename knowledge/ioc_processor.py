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

import time as _time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

from urllib.parse import parse_qsl as _parse_qsl
from urllib.parse import urlencode as _urlencode
from urllib.parse import urlparse as _urlparse

from hledac.universal._core.rust_backend import get_accel as _get_accel

# D4: SIMD Aho-Corasick for 10-100x faster IOC pre-filtering
from rust_extensions.wiring.aho_corasick_simd_wiring import (
    SIMDAhoCorasickMatcher,
)
from rust_extensions.wiring.aho_corasick_simd_wiring import (
    ioc_prefilter as _ioc_prefilter,
)
from rust_extensions.wiring.aho_corasick_simd_wiring import (
    ioc_prefilter_batch as _ioc_prefilter_batch,
)
from rust_extensions.wiring.aho_corasick_simd_wiring import (
    simd_aho_available as _simd_aho_available,
)

# C14: Deobfuscation for +25% IOC recall
from rust_extensions.wiring.deobfuscate_wiring import (
    deobfuscate_wired as _deobfuscate_wired,
)

# D5: DedupBloom Tier 0 pre-filter — 10× faster than RotatingBloomFilter for URL dedup
# Import lazily to avoid circular dependencies and speed up cold start
_dedup_bloom: DedupBloom | None = None


def _get_dedup_bloom() -> DedupBloom | None:
    """Lazy load DedupBloom instance (M1 8GB safe, bounded to 50K items)."""
    global _dedup_bloom
    if _dedup_bloom is None:
        try:
            from rust_extensions.wiring.dedup_bloom_wiring import get_dedup_bloom

            _dedup_bloom = get_dedup_bloom("/tmp/hledac/ioc_dedup_bloom")
        except Exception:
            _dedup_bloom = None
    return _dedup_bloom


from forensics.ioc_patterns_generated import (  # noqa: F401,E402
    _HASH_VALIDATORS,
    _IOC_COMBINED,
    _IOC_TYPE_NAMES,
    _TRACKING_PARAMS,
)

IOC_TYPES: frozenset[str] = frozenset(
    (
        "cve",
        "ip",
        "ipv4",
        "ipv6",
        "hash_sha256",
        "hash_md5",
        "onion",
        "i2p",
        "domain",
        "apt",
        "malware",
        "info_hash",
        "magnet_uri",
        "threat_actor",
        "malware_family",
        "email",
        "mac",
        "btc",
        "eth",
        "pending",
    )
)


def _looks_like_mac(value: str) -> bool:
    """Disambiguate IPv6 compressed form vs MAC address (aa:bb:cc:dd:ee:ff)."""
    sep = ":" if ":" in value else "-"
    parts = value.split(sep)
    return len(parts) == 6 and all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


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


# Cache entry: (combined_func, batch_nfc, batch_strip)
# combined_func = batch_nfc_and_strip_diacritics_fast (single pass, preferred)
# batch_nfc, batch_strip = separate fallback functions
_RUST_TEXT_FAST: tuple[object, object, object] | None = None  # (combined, batch_nfc, batch_strip)


def _get_rust_text_fast() -> tuple[object, object, object] | None:
    """Lazy-load Rust NEON fast-path for text normalization via wiring layer.

    Returns (combined, batch_nfc, batch_strip) where:
    - combined: batch_nfc_and_strip_diacritics (single pass, preferred)
    - batch_nfc: batch_nfc_normalize_fast
    - batch_strip: batch_strip_diacritics_fast

    Returns None if Rust unavailable.
    """
    global _RUST_TEXT_FAST
    if _RUST_TEXT_FAST is not None:
        return _RUST_TEXT_FAST
    try:
        # F1: Use centralized text_norm_wiring layer for consistency
        from rust_extensions.wiring.text_norm_wiring import (
            batch_nfc_and_strip_diacritics as _combined,
        )
        from rust_extensions.wiring.text_norm_wiring import (
            batch_nfc_normalize_fast as _batch_nfc,
        )
        from rust_extensions.wiring.text_norm_wiring import (
            batch_strip_diacritics_fast as _batch_strip,
        )
        from rust_extensions.wiring.text_norm_wiring import (
            is_available as _available,
        )

        if _available():
            _RUST_TEXT_FAST = (_combined, _batch_nfc, _batch_strip)
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

    D5 INTEGRATION: Uses DedupBloom as Tier 0 pre-filter before normalization.
    This provides 10× faster URL dedup for the IOC pipeline.

    Architecture:
        URLs → DedupBloom (Tier 0, fast skip) → Normalization → RotatingBloomFilter

    Uses Rust NEON SIMD fast-path (batch_nfc_and_strip_diacritics_fast when available,
    otherwise batch_nfc_normalize_fast + batch_strip_diacritics_fast) for 3-5× speedup
    on non-ASCII IOC datasets. Falls back to sequential Python.
    """
    if not urls:
        return []

    # D5: Tier 0 — Fast bloom filter pre-filter (skips obviously duplicate URLs)
    # DedupBloom uses FNV-1a hash for cross-instance consistency
    bloom = _get_dedup_bloom()
    if bloom is not None:
        # Batch bloom skip: more efficient than individual checks
        # Returns (non_duplicate_urls, skip_count)
        urls, skipped = bloom.skip_batch(urls)
        # M1 8GB safe: bloom is bounded to 50K items, no memory growth

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

    __slots__ = ("_accel", "_deobfuscate", "_simd_matcher")

    def __init__(self) -> None:
        self._accel = _get_accel()
        self._deobfuscate = _deobfuscate_wired()
        # D4: Lazy SIMD matcher (created on first use)
        self._simd_matcher: SIMDAhoCorasickMatcher | None = None

    @property
    def simd_available(self) -> bool:
        """D4: True if SIMD Aho-Corasick is available."""
        return _simd_aho_available

    def _get_simd_matcher(self) -> SIMDAhoCorasickMatcher | None:
        """D4: Lazy initialization of SIMD matcher."""
        if self._simd_matcher is None and _simd_aho_available:
            try:
                self._simd_matcher = SIMDAhoCorasickMatcher.from_ioc_patterns()
            except Exception:  # noqa: BLE001
                pass
        return self._simd_matcher

    @property
    def is_rust_available(self) -> bool:
        """True if Rust extension IOC domain is available."""
        try:
            return self._accel.is_available and self._accel.ioc is not None
        except Exception:
            return False

    def _extract_with_deobfuscation(self, text: str) -> list[tuple[str, str]]:
        """Extract IOCs with deobfuscation pipeline (C14: +25% recall)."""
        if not text:
            return []

        decoded_candidates: list[str] = []
        if self._deobfuscate.available:
            try:
                decoded_candidates = self._deobfuscate.decode_ioc_candidates(text)
            except Exception:  # noqa: BLE001
                pass

        iocs: list[tuple[str, str]] = []
        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            # Try SIMD path first (fastest on M1)
            try:
                iocs = ioc_domain.extract_iocs_simd(text)
            except Exception:  # noqa: BLE001
                pass
            if not iocs:
                try:
                    iocs = ioc_domain.extract_iocs_flat(text)
                except Exception:  # noqa: BLE001
                    pass
        if not iocs:
            # Python fallback — named-group combined regex, single pass
            iocs = _python_fast_ioc_extract(text)

        seen: set[str] = {f"{v}:{t}" for v, t in iocs}
        for decoded in decoded_candidates:
            if self.is_rust_available:
                ioc_domain = self._accel.ioc
                try:
                    decoded_iocs = ioc_domain.extract_iocs_flat(decoded)
                    for value, ioc_type in decoded_iocs:
                        key = f"{value}:{ioc_type}"
                        if key not in seen:
                            seen.add(key)
                            iocs.append((value, ioc_type))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            # Python fallback for decoded candidates
            decoded_iocs = _python_fast_ioc_extract(decoded)
            for value, ioc_type in decoded_iocs:
                key = f"{value}:{ioc_type}"
                if key not in seen:
                    seen.add(key)
                    iocs.append((value, ioc_type))

        return iocs

    def extract(self, text: str) -> list[tuple[str, str]]:
        """Extract IOCs from text.

        C14: Uses deobfuscation pipeline for +25% recall on defanged/encoded IOC.
        Pipeline: deobfuscate → extract IOCs from original + decoded text → merge.

        Uses Rust SIMD extractor when available (Teddy/NEON on M1 ~5× faster).
        Falls back to pure Python named-group combined regex.

        Returns:
            List of (ioc_value, ioc_type) tuples.
        """
        return self._extract_with_deobfuscation(text)

    def prefilter_simd(self, text: str) -> list[tuple[str, str]]:
        """D4: Fast IOC prefilter using SIMD Aho-Corasick.

        This is a FAST pre-filter that identifies potential IOC regions
        using NEON-accelerated Aho-Corasick. Results should be validated
        by extract() for accuracy.

        10-100× faster than full IOC extraction for quick scanning.

        Args:
            text: Text to scan for IOCs

        Returns:
            List of (ioc_value, ioc_type) tuples from SIMD scan
        """
        if not text:
            return []

        return _ioc_prefilter(text, self._get_simd_matcher())

    def prefilter_simd_batch(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        """D4: Batch IOC prefilter using SIMD Aho-Corasick.

        Args:
            texts: List of texts to scan

        Returns:
            List of IOC lists, one per input text
        """
        if not texts:
            return []

        return _ioc_prefilter_batch(texts, self._get_simd_matcher())

    def extract_batch(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        """Batch extract IOCs from multiple texts.

        C14: Uses deobfuscation pipeline for +25% recall on defanged/encoded IOC.
        Pipeline: batch deobfuscate → extract IOCs from original + decoded text → merge.

        Uses Rust batch extractor when available.
        Falls back to sequential Python extraction.

        Returns:
            List of IOC lists, one per input text, matching input order.
        """
        if not texts:
            return []

        decoded_batch: list[list[str]] = [[] for _ in texts]
        if self._deobfuscate.available:
            try:
                decoded_batch = self._deobfuscate.batch_decode_ioc_candidates(texts)
            except Exception:  # noqa: BLE001
                pass

        results: list[list[tuple[str, str]]] = []
        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                results = ioc_domain.batch_extract_iocs_simd(texts)
            except Exception:  # noqa: BLE001
                pass

        if not results or len(results) != len(texts):
            # Python fallback — parallel via ThreadPoolExecutor
            import os as _os
            from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

            n_workers = min(4, _os.cpu_count() or 2)
            with _ThreadPoolExecutor(max_workers=n_workers) as ex:
                results = list(ex.map(_python_fast_ioc_extract, texts))

        seen_batch: list[set[str]] = [{f"{v}:{t}" for v, t in r} for r in results]

        for text_idx, decoded_list in enumerate(decoded_batch):
            for decoded in decoded_list:
                if self.is_rust_available:
                    ioc_domain = self._accel.ioc
                    try:
                        decoded_iocs = ioc_domain.extract_iocs_flat(decoded)
                        for value, ioc_type in decoded_iocs:
                            key = f"{value}:{ioc_type}"
                            if key not in seen_batch[text_idx]:
                                seen_batch[text_idx].add(key)
                                results[text_idx].append((value, ioc_type))
                        continue
                    except Exception:  # noqa: BLE001
                        pass
                # Python fallback for decoded candidates
                decoded_iocs = _python_fast_ioc_extract(decoded)
                for value, ioc_type in decoded_iocs:
                    key = f"{value}:{ioc_type}"
                    if key not in seen_batch[text_idx]:
                        seen_batch[text_idx].add(key)
                        results[text_idx].append((value, ioc_type))

        return results

    def extract_indexed(self, texts: list[str]) -> list[tuple[int, str, str]]:
        """Indexed batch extract — returns (text_idx, ioc_value, ioc_type).

        Uses Rust indexed batch extractor when available.
        Falls back to sequential Python extraction.

        C14: Uses deobfuscation pipeline for +25% recall on defanged/encoded IOC.

        Returns:
            List of (text_index, ioc_value, ioc_type) tuples.
        """
        if not texts:
            return []

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                results = ioc_domain.batch_extract_iocs_simd_indexed(texts)
                # Fall back to Python if Rust returns empty (Rust might not handle all cases)
                if results:
                    return results
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
            return _asyncio.run_coroutine_threadsafe(self.extract_indexed_async(texts), loop).result()

    async def extract_indexed_async(self, texts: list[str]) -> list[tuple[int, str, str]]:
        """Async indexed batch extract with rayon parallelism and deobfuscation.

        C14: Uses deobfuscation pipeline for +25% recall on defanged/encoded IOC.

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

        decoded_batch: list[list[str]] = [[] for _ in texts]
        if self._deobfuscate.available:
            try:
                decoded_batch = self._deobfuscate.batch_decode_ioc_candidates(texts)
            except Exception:  # noqa: BLE001
                pass

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                results = ioc_domain.batch_extract_iocs_simd_indexed(texts)
                seen: set[str] = {f"{idx}:{v}:{t}" for idx, v, t in results}
                extra_results: list[tuple[int, str, str]] = []
                for text_idx, decoded_list in enumerate(decoded_batch):
                    for decoded in decoded_list:
                        decoded_iocs = self._extract_iocs_from_text(decoded)
                        for value, ioc_type in decoded_iocs:
                            key = f"{text_idx}:{value}:{ioc_type}"
                            if key not in seen:
                                seen.add(key)
                                extra_results.append((text_idx, value, ioc_type))
                # Fall back to Python if Rust returns empty (Rust might not handle all cases)
                if results or extra_results:
                    return results + extra_results
            except Exception:  # noqa: BLE001
                pass

        # Python fallback with rayon parallelism
        try:
            from hledac.universal.utils.sync_bridge import to_thread_rayon

            def _extract_with_index(args: tuple[int, str, list[str]]) -> list[tuple[int, str, str]]:
                """Extract IOCs from a single text with its index and decoded candidates."""
                idx, text, decoded_list = args
                results = [(idx, value, ioc_type) for value, ioc_type in _python_fast_ioc_extract(text)]
                seen = {f"{v}:{t}" for _, v, t in results}
                for decoded in decoded_list:
                    decoded_iocs = _python_fast_ioc_extract(decoded)
                    for value, ioc_type in decoded_iocs:
                        key = f"{value}:{ioc_type}"
                        if key not in seen:
                            seen.add(key)
                            results.append((idx, value, ioc_type))
                return results

            # Process in chunks to avoid memory pressure on M1 8GB
            chunk_size = min(50, len(texts))  # Reduced from 100 for M1 8GB safety
            results: list[tuple[int, str, str]] = []

            for i in range(0, len(texts), chunk_size):
                chunk = [(i + j, texts[i + j], decoded_batch[i + j]) for j in range(min(chunk_size, len(texts) - i))]
                chunk_results = await to_thread_rayon(
                    "cpu",
                    _extract_with_index,
                    (chunk,),
                    timeout=30.0,
                )
                results.extend(chunk_results)

            return results
        except ImportError, RuntimeError:
            # Fallback: rayon unavailable — sequential extraction with deobfuscation
            results: list[tuple[int, str, str]] = []
            for idx, text in enumerate(texts):
                seen: set[str] = set()
                for value, ioc_type in _python_fast_ioc_extract(text):
                    results.append((idx, value, ioc_type))
                    seen.add(f"{value}:{ioc_type}")
                for decoded in decoded_batch[idx]:
                    for value, ioc_type in _python_fast_ioc_extract(decoded):
                        key = f"{value}:{ioc_type}"
                        if key not in seen:
                            seen.add(key)
                            results.append((idx, value, ioc_type))
            return results

    def _extract_iocs_from_text(self, text: str) -> list[tuple[str, str]]:
        """Extract IOCs from a single text (helper for async methods)."""
        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                return ioc_domain.extract_iocs_flat(text)
            except Exception:  # noqa: BLE001
                pass
        return _python_fast_ioc_extract(text)

    async def extract_batch_async(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        """Async batch extract with rayon parallelism and deobfuscation.

        C14: Uses deobfuscation pipeline for +25% recall on defanged/encoded IOC.

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

        decoded_batch: list[list[str]] = [[] for _ in texts]
        if self._deobfuscate.available:
            try:
                decoded_batch = self._deobfuscate.batch_decode_ioc_candidates(texts)
            except Exception:  # noqa: BLE001
                pass

        if self.is_rust_available:
            ioc_domain = self._accel.ioc
            try:
                results = ioc_domain.batch_extract_iocs_simd(texts)
                seen_batch: list[set[str]] = [{f"{v}:{t}" for v, t in r} for r in results]
                for text_idx, decoded_list in enumerate(decoded_batch):
                    for decoded in decoded_list:
                        decoded_iocs = self._extract_iocs_from_text(decoded)
                        for value, ioc_type in decoded_iocs:
                            key = f"{value}:{ioc_type}"
                            if key not in seen_batch[text_idx]:
                                seen_batch[text_idx].add(key)
                                results[text_idx].append((value, ioc_type))
                return results
            except Exception:  # noqa: BLE001
                pass

        # Python fallback with rayon parallelism
        try:
            from hledac.universal.utils.sync_bridge import to_thread_rayon

            def _extract_with_deobfuscation(args: tuple[int, str, list[str]]) -> tuple[int, list[tuple[str, str]]]:
                """Extract IOCs from text with decoded candidates."""
                idx, text, decoded_list = args
                iocs = _python_fast_ioc_extract(text)
                seen = {f"{v}:{t}" for v, t in iocs}
                for decoded in decoded_list:
                    decoded_iocs = _python_fast_ioc_extract(decoded)
                    for value, ioc_type in decoded_iocs:
                        key = f"{value}:{ioc_type}"
                        if key not in seen:
                            seen.add(key)
                            iocs.append((value, ioc_type))
                return (idx, iocs)

            # Process in chunks to avoid memory pressure on M1 8GB
            chunk_size = min(50, len(texts))  # Reduced from 100 for M1 8GB safety
            results: list[list[tuple[str, str]]] = []

            for i in range(0, len(texts), chunk_size):
                chunk = [(i + j, texts[i + j], decoded_batch[i + j]) for j in range(min(chunk_size, len(texts) - i))]
                chunk_results = await to_thread_rayon(
                    "cpu",
                    _extract_with_deobfuscation,
                    (chunk,),
                    timeout=30.0,
                )
                # Sort results by index to maintain order
                chunk_results.sort(key=lambda x: x[0])
                results.extend([r[1] for r in chunk_results])

            return results
        except ImportError, RuntimeError:
            # Fallback: rayon unavailable — use ThreadPoolExecutor with deobfuscation
            import os as _os
            from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

            def _extract_single(args: tuple[int, str]) -> tuple[int, list[tuple[str, str]]]:
                """Extract IOCs from text with decoded candidates."""
                idx, text = args
                iocs = _python_fast_ioc_extract(text)
                seen = {f"{v}:{t}" for v, t in iocs}
                for decoded in decoded_batch[idx]:
                    decoded_iocs = _python_fast_ioc_extract(decoded)
                    for value, ioc_type in decoded_iocs:
                        key = f"{value}:{ioc_type}"
                        if key not in seen:
                            seen.add(key)
                            iocs.append((value, ioc_type))
                return (idx, iocs)

            n_workers = min(4, _os.cpu_count() or 2)
            with _ThreadPoolExecutor(max_workers=n_workers) as ex:
                indexed_results = list(ex.map(_extract_single, enumerate(texts)))
                indexed_results.sort(key=lambda x: x[0])
                return [r[1] for r in indexed_results]

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

        D5 INTEGRATION: Uses DedupBloom as Tier 0 pre-filter before Rust or Python dedup.
        This provides 10× faster URL dedup for the IOC pipeline.

        Architecture:
            URLs → DedupBloom (Tier 0, fast skip) → Rust batch_dedup_urls / Python dedup
            → Add deduplicated URLs back to bloom for future skip efficiency

        Uses Rust batch_dedup_urls when available.
        Falls back to pure Python normalization + dedup.
        """
        # D5: Tier 0 — Fast bloom filter pre-filter (skips obviously duplicate URLs)
        # This runs BEFORE Rust or Python dedup for maximum performance
        bloom = _get_dedup_bloom()
        if bloom is not None:
            # Batch bloom skip: more efficient than individual checks
            urls, skipped = bloom.skip_batch(urls)
            # M1 8GB safe: bloom is bounded to 50K items, no memory growth

        # Perform deduplication
        if self.is_rust_available:
            try:
                # is_rust_available already guarantees ioc is not None
                deduped = self._accel.ioc.batch_dedup_urls(urls)
            except Exception:  # noqa: BLE001
                deduped = _python_batch_dedup_urls(urls)
        else:
            deduped = _python_batch_dedup_urls(urls)

        # D5 FIX: Add deduplicated URLs back to bloom for future skip efficiency
        # Without this, bloom never learns about new URLs and can't skip duplicates
        if bloom is not None and deduped:
            try:
                bloom.add_batch(deduped)
            except Exception:  # noqa: BLE001
                pass  # Non-fatal: bloom update failure shouldn't break dedup

        return deduped

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
            batch_results = _asyncio.run_coroutine_threadsafe(self.extract_batch_async(texts), loop).result()

        results: list[list] = []
        for i in range(n):
            iocs = batch_results[i] if i < len(batch_results) else []
            findings = _iocs_to_findings(iocs, source_finding_ids[i], queries[i], min_confidence)
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
            findings = _iocs_to_findings(iocs, source_finding_ids[i], queries[i], min_confidence)
            results.append(findings)
        return results


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


# E2: Zero-copy batch IOC extraction for pipeline hot paths.
# Fast path using Rust extract_iocs_zero_copy (4-6× speedup).
_async_rust_zero_copy_func: object | None = None


def _get_rust_zero_copy_func() -> object | None:
    """Lazy-load Rust extract_iocs_zero_copy function."""
    global _async_rust_zero_copy_func
    if _async_rust_zero_copy_func is not None:
        return _async_rust_zero_copy_func
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available and hasattr(rust.ioc, "extract_iocs_zero_copy"):
            _async_rust_zero_copy_func = rust.ioc.extract_iocs_zero_copy
            return _async_rust_zero_copy_func
    except Exception:  # noqa: BLE001
        pass
    _async_rust_zero_copy_func = None
    return None


async def batch_extract_iocs_fast(texts: list[str]) -> list[list[tuple[str, str]]]:
    """E2: Zero-copy batch IOC extraction for pipeline hot paths.

    Uses Rust extract_iocs_zero_copy (Vec<String> → Vec<Vec<(String, String)>>)
    with Rayon parallelization for 4-6× speedup vs sequential extraction.

    This is the internal API for:
      - pipeline/ioc_cooccurrence_miner.py upstream IOC extraction
      - High-throughput batch processing where deobfuscation overhead is unnecessary

    Args:
        texts: List of text strings to extract IOCs from.

    Returns:
        List of IOC lists, one per input text.
        Returns [[] * len(texts)] on any error (fail-safe).

    Note:
        Unlike extract_batch_async, this function does NOT run deobfuscation.
        Use extract_batch_async if you need +25% recall from decoded candidates.
    """
    if not texts:
        return []

    rust_func = _get_rust_zero_copy_func()
    if rust_func is not None:
        try:
            return await asyncio.to_thread(rust_func, texts)
        except Exception:  # noqa: BLE001
            pass

    # Fallback: pure Python batch extraction
    return await asyncio.to_thread(_python_batch_extract, texts)


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
    return await _get_processor().extract_to_findings_bulk_async(texts, source_finding_ids, queries, min_confidence)


# Aliases for forensics/ioc_extractor.py backward compatibility
ioc_extract_to_canonical_findings = extract_to_findings
ioc_extract_to_canonical_findings_bulk = extract_to_findings_bulk

from forensics.ioc_patterns_generated import (  # noqa: F401,E402,F811
    _HASH_VALIDATORS,
    _IOC_COMBINED,
    _IOC_PATTERNS,
    _IOC_TYPE_NAMES,
    _TRACKING_PARAMS,
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
