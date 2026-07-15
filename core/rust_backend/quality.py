# quality.py — Text quality assessment domain (entropy, dedup fingerprint)

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustQualityDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def normalize_quality_text(self, text: str) -> str:
        return self._ext.normalize_quality_text(text)

    def batch_normalize_quality_text(self, texts: list[str]) -> list[str]:
        return self._ext.batch_normalize_quality_text(texts)

    def compute_entropy(self, text: str) -> float:
        return self._ext.compute_entropy(text)

    def batch_entropy(self, texts: list[str]) -> list[float]:
        return self._ext.batch_entropy(texts)

    def dedup_fingerprint(self, text: str) -> str:
        return self._ext.dedup_fingerprint(text)

    def batch_dedup_fingerprints(self, texts: list[str]) -> list[str]:
        return self._ext.batch_dedup_fingerprints(texts)

    def url_fingerprint(self, url: str) -> str:
        return self._ext.url_fingerprint(url)

    def batch_url_fingerprints(self, urls: list[str]) -> list[str]:
        return self._ext.batch_url_fingerprints(urls)

    def batch_entropy_zc(self, texts: list[str]) -> list[float]:
        """Zero-copy batch entropy — GIL held during rayon scope.

        NOTE: These _zc variants are intentionally NOT wired in duckdb_store.py.
        zero_copy.rs holds GIL for the entire rayon scope (mixed_pool, 2 threads),
        while quality_gate.rs releases GIL during rayon work (cpu_pool, 4 threads).
        For duckdb_store batch operations, the non-zc (owned-data) variants from
        quality_gate.rs are preferred — GIL release allows better M1 concurrency.
        """
        return self._ext.batch_entropy_zc(texts)

    def batch_dedup_fingerprints_zc(self, texts: list[str]) -> list[str]:
        """Zero-copy batch dedup fingerprints — GIL held during rayon scope.

        See batch_entropy_zc docstring for design rationale.
        """
        return self._ext.batch_dedup_fingerprints_zc(texts)

    def assess_findings_quality_batch(self, findings: list[dict]) -> list[dict]:
        """
        ISSUE-022: Parallel batch quality assessment.

        Calls Rust assess_findings_quality_batch() which does pure-compute
        in one rayon-parallel call per chunk: URL fp, normalize, entropy,
        dedup fp — all in a single pass through rayon, no Python overhead
        between stages.

        Returns list[dict] with keys: accepted(bool), reason(str|None),
        rejection_reason(str|None), entropy(float), normalized_hash(str),
        duplicate(bool).

        Stateful checks (hot_cache, LMDB, semantic dedup) remain in Python
        after this call — this function only provides pure-compute decisions.
        """
        return self._ext.assess_findings_quality_batch(findings)


class _PythonQualityDomain:
    """Pure-Python text quality assessment fallback."""

    __slots__ = ()

    @staticmethod
    def normalize_quality_text(text: str) -> str:
        return _python_normalize_quality_text(text)

    @staticmethod
    def batch_normalize_quality_text(texts: list[str]) -> list[str]:
        return [_python_normalize_quality_text(t) for t in texts]

    @staticmethod
    def compute_entropy(text: str) -> float:
        return _python_compute_entropy(text)

    @staticmethod
    def batch_entropy(texts: list[str]) -> list[float]:
        return [_python_compute_entropy(t) for t in texts]

    @staticmethod
    def dedup_fingerprint(text: str) -> str:
        return _python_dedup_fingerprint(text)

    @staticmethod
    def batch_dedup_fingerprints(texts: list[str]) -> list[str]:
        return [_python_dedup_fingerprint(t) for t in texts]

    @staticmethod
    def url_fingerprint(url: str) -> str:
        return _python_url_fingerprint_b2b(url)

    @staticmethod
    def batch_url_fingerprints(urls: list[str]) -> list[str]:
        return [_python_url_fingerprint_b2b(u) for u in urls]

    @staticmethod
    def batch_entropy_zc(texts: list[str]) -> list[float]:
        return [_python_compute_entropy(t) for t in texts]

    @staticmethod
    def batch_dedup_fingerprints_zc(texts: list[str]) -> list[str]:
        return [_python_dedup_fingerprint(t) for t in texts]


# ------------------------------------------------------------------
# Pure-Python quality helpers (moved from top of rust_backend.py)
# ------------------------------------------------------------------


def _python_normalize_quality_text(text: str) -> str:
    """Normalize text for quality assessment: lowercase, strip."""
    import re
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _python_compute_entropy(text: str) -> float:
    """Shannon entropy of text characters."""
    from collections import Counter
    if not text:
        return 0.0
    freq = Counter(text)
    total = len(text)
    entropy = 0.0
    for count in freq.values():
        p = count / total
        if p > 0:
            import math
            entropy -= p * math.log2(p)
    return entropy


def _python_batch_entropy(texts: list[str]) -> list[float]:
    return [_python_compute_entropy(t) for t in texts]


def _python_dedup_fingerprint(text: str) -> str:
    """Deduplication fingerprint: normalized text hash."""
    import hashlib
    normalized = _python_normalize_quality_text(text)
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _python_batch_dedup_fingerprints(texts: list[str]) -> list[str]:
    return [_python_dedup_fingerprint(t) for t in texts]


def _python_url_fingerprint_b2b(url: str) -> str:
    """URL fingerprint for deduplication."""
    import hashlib
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        key = f"{parsed.netloc}{parsed.path}".lower().strip("/")
    except Exception:
        key = url.lower().strip("/")
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def get_domain(ext: object | None) -> _RustQualityDomain | _PythonQualityDomain:
    if ext is not None:
        return _RustQualityDomain(ext)
    return _PythonQualityDomain()
