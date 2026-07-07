# ioc.py — IOC extraction domain
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustIocDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        return self._ext.extract_iocs(text)

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        return self._ext.batch_extract_iocs(texts)

    def nfc_normalize(self, text: str) -> str:
        return self._ext.nfc_normalize(text)

    def extract_iocs_flat(self, text: str) -> list[tuple[str, str]]:
        return self._ext.extract_iocs_flat(text)

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_nfc_normalize_fast(texts)

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_strip_diacritics_fast(texts)

    def extract_iocs_simd(self, text: str) -> list[tuple[str, str]]:
        return self._ext.extract_iocs_simd(text)

    def batch_extract_iocs_simd(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        return self._ext.batch_extract_iocs_simd(texts)

    def batch_extract_iocs_simd_indexed(
        self, texts: list[str]
    ) -> list[tuple[int, str, str]]:
        return self._ext.batch_extract_iocs_simd_indexed(texts)


class _PythonIocDomain:
    """Pure-Python IOC extraction fallback."""

    __slots__ = ()

    @staticmethod
    def extract_iocs(text: str) -> dict[str, list[str]]:
        return _python_extract_iocs(text)

    @staticmethod
    def batch_extract_iocs(texts: list[str]) -> list[dict[str, list[str]]]:
        return [_python_extract_iocs(t) for t in texts]

    @staticmethod
    def nfc_normalize(self, text: str) -> str:
        return _python_nfc_normalize(text)

    @staticmethod
    def extract_iocs_flat(self, text: str) -> list[tuple[str, str]]:
        return _python_extract_iocs_flat(text)

    @staticmethod
    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    @staticmethod
    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]

    @staticmethod
    def extract_iocs_simd(self, text: str) -> list[tuple[str, str]]:
        return _python_extract_iocs_flat(text)

    @staticmethod
    def batch_extract_iocs_simd(texts: list[str]) -> list[list[tuple[str, str]]]:
        """Batch extraction — uses serial Python extraction per text."""
        return [_python_extract_iocs_flat(t) for t in texts]

    @staticmethod
    def batch_extract_iocs_simd_indexed(
        texts: list[str]
    ) -> list[tuple[int, str, str]]:
        """Batch extraction with index — uses serial Python extraction."""
        result: list[tuple[int, str, str]] = []
        for idx, t in enumerate(texts):
            for ioc_type, value in _python_extract_iocs_flat(t):
                result.append((idx, value, ioc_type))
        return result


# ------------------------------------------------------------------
# Pure-Python IOC helpers (moved from top of rust_backend.py)
# ------------------------------------------------------------------


def _python_extract_iocs(text: str) -> dict[str, list[str]]:
    """Pure-Python IOC extraction: URLs, IPs, emails, domains, hashes."""
    import re

    urls = re.findall(
        r"https?://[^\s<>\"]+", text
    )
    emails = re.findall(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
    )
    ips = re.findall(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
        text,
    )
    domains = re.findall(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
        text,
    )
    hashes = re.findall(
        r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b",
        text,
    )
    return {
        "urls": list(dict.fromkeys(urls)),
        "domains": list(dict.fromkeys(domains)),
        "emails": list(dict.fromkeys(emails)),
        "ipv4s": list(dict.fromkeys(ips)),
        "sha256s": list(dict.fromkeys(hashes)),
    }


def _python_extract_iocs_flat(text: str) -> list[tuple[str, str]]:
    """Extract flat IOC list [(type, value), ...]."""
    iocs = _python_extract_iocs(text)
    result: list[tuple[str, str]] = []
    for ioc_type, values in iocs.items():
        for v in values:
            result.append((ioc_type, v))
    return result


def _python_nfc_normalize(text: str) -> str:
    """NFC Unicode normalization (fallback: identity)."""
    import unicodedata
    try:
        return unicodedata.normalize("NFC", text)
    except Exception:
        return text


def _python_strip_diacritics(text: str) -> str:
    """Strip diacritics from text."""
    import unicodedata
    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))
    except Exception:
        return text


def get_domain(ext: object | None) -> _RustIocDomain | _PythonIocDomain:
    if ext is not None:
        return _RustIocDomain(ext)
    return _PythonIocDomain()
