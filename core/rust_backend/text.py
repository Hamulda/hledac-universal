# text.py — Text domain (NFC, diacritics)
"""
Unicode text normalization and diacritic handling.
Used for consistent text comparison across different Unicode representations.

"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Text Domain
# =============================================================================


class _RustTextDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def nfc_normalize(self, text: str) -> str:
        """Unicode NFC normalization."""
        return self._ext.text_nfc_normalize(text)

    def nfd_normalize(self, text: str) -> str:
        """Unicode NFD normalization."""
        return self._ext.text_nfd_normalize(text)

    def strip_diacritics(self, text: str) -> str:
        """Remove diacritical marks from text."""
        return self._ext.text_strip_diacritics(text)

    def batch_nfc_normalize(self, texts: list[str]) -> list[str]:
        """Batch NFC normalization."""
        return self._ext.text_batch_nfc_normalize(texts)

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        """Fast batch NFC normalization."""
        return self._ext.text_batch_nfc_normalize_fast(texts)

    def batch_strip_diacritics(self, texts: list[str]) -> list[str]:
        """Batch diacritic stripping."""
        return self._ext.text_batch_strip_diacritics(texts)

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        """Fast batch diacritic stripping."""
        return self._ext.text_batch_strip_diacritics_fast(texts)


class _PythonTextDomain:
    __slots__ = ()

    def nfc_normalize(self, text: str) -> str:
        """Python fallback: NFC normalization."""
        return _python_nfc_normalize(text)

    def nfd_normalize(self, text: str) -> str:
        """Python fallback: NFD normalization."""
        return unicodedata.normalize("NFD", text)

    def strip_diacritics(self, text: str) -> str:
        """Python fallback: remove diacritical marks."""
        return _python_strip_diacritics(text)

    def batch_nfc_normalize(self, texts: list[str]) -> list[str]:
        """Python fallback: batch NFC normalization."""
        return [_python_nfc_normalize(t) for t in texts]

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        """Python fallback: fast batch NFC normalization."""
        return [_python_nfc_normalize(t) for t in texts]

    def batch_strip_diacritics(self, texts: list[str]) -> list[str]:
        """Python fallback: batch diacritic stripping."""
        return [_python_strip_diacritics(t) for t in texts]

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        """Python fallback: fast batch diacritic stripping."""
        return [_python_strip_diacritics(t) for t in texts]


def _python_nfc_normalize(text: str) -> str:
    """Python fallback: NFC normalization."""
    return unicodedata.normalize("NFC", text)


def _python_strip_diacritics(text: str) -> str:
    """Python fallback: remove diacritical marks from text."""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def get_text_domain(ext: object | None) -> _RustTextDomain | _PythonTextDomain:
    """Factory: return Rust or Python TextDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustTextDomain(ext)
        except Exception:
            pass
    return _PythonTextDomain()
