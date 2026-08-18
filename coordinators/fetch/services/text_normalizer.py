# SPDX-License-Identifier: Apache-2.0
"""
Text Normalizer Service — Unicode NFC normalization for DuckDB storage.

C10: text_norm::nfc_normalize → coordinators/fetch/services/text_normalizer.py

Purpose:
    Normalize text to Unicode NFC form before storing in DuckDB.
    Ensures consistent text comparison across different Unicode representations.

Architecture:
    - Tier 0: Rust nfc_normalize (NEON SIMD, GIL released, 3× faster on M1)
    - Tier 1: Rust batch_nfc_normalize_fast (parallel, for batch operations)
    - Fallback: Python unicodedata.normalize('NFC', text)

M1 8GB Safety:
    - Lazy import to avoid loading Rust if not needed
    - GIL released during rayon parallel processing
    - Graceful fallback on any error

Usage:
    from coordinators.fetch.services import TextNormalizerService

    normalizer = TextNormalizerService()
    normalized = normalizer.normalize("café")  # "café" (NFC)
    normalized_batch = normalizer.normalize_batch(["café", "žluťoučký"])
"""
from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from hledac.universal._core.rust_backend import rust_backend as _rust_backend

__all__ = [
    "TextNormalizerService",
    "get_text_normalizer",
]


# =============================================================================
# Constants
# =============================================================================

# Maximum batch size before chunking to avoid Rust's BATCH_HARD_CAP (50,000)
_BATCH_SOFT_CAP: int = 40_000  # Leave headroom for Rust internal processing

# Chunk size for large batches
_BATCH_CHUNK_SIZE: int = 10_000


# =============================================================================
# Rust Fast-Path (Lazy Import)
# =============================================================================

# Module-level cache for Rust text normalization functions
# Tuple: (nfc_normalize, batch_nfc_normalize, batch_nfc_normalize_fast)
_RUST_TEXT_NORM: tuple[
    "Callable[[str], str]",  # nfc_normalize
    "Callable[[list[str]], list[str]] | None",  # batch_nfc_normalize
    "Callable[[list[str]], list[str]] | None",  # batch_nfc_normalize_fast
] | None = None

# Type alias for better readability
_NfcFunc = Callable[[str], str]
_BatchNfcFunc = Callable[[list[str]], list[str]]


def _get_rust_text_norm() -> tuple[object, object, object] | None:
    """
    Lazy-load Rust text normalization functions.

    Returns (nfc_normalize, batch_nfc_normalize, batch_nfc_normalize_fast)
    or None if Rust unavailable.

    M1 8GB: Only loads Rust if actually needed.
    """
    global _RUST_TEXT_NORM
    if _RUST_TEXT_NORM is not None:
        return _RUST_TEXT_NORM

    try:
        # R6: Centralized Rust access via _core.rust_backend
        from hledac.universal._core.rust_backend import rust

        nfc_normalize = rust.raw.nfc_normalize
        batch_nfc_normalize = rust.raw.batch_nfc_normalize
        batch_nfc_normalize_fast = rust.raw.batch_nfc_normalize_fast

        # Verify at least nfc_normalize is available
        if nfc_normalize is not None:
            _RUST_TEXT_NORM = (nfc_normalize, batch_nfc_normalize, batch_nfc_normalize_fast)
            return _RUST_TEXT_NORM
    except Exception:  # noqa: BLE001
        pass

    _RUST_TEXT_NORM = None
    return None


# =============================================================================
# Python Fallback
# =============================================================================


def _python_nfc_normalize(text: str) -> str:
    """
    Python fallback: NFC normalization via unicodedata.

    Optimizations:
    - ASCII-only text is returned as-is (NFC is identity for ASCII)
    - Empty string returns immediately

    Args:
        text: Input text (may contain decomposed Unicode)

    Returns:
        NFC-normalized text
    """
    # Empty string: identity
    if not text:
        return text
    # ASCII-only: NFC is identity (matching Rust batch_nfc_normalize_fast behavior)
    if text.isascii():
        return text
    try:
        return unicodedata.normalize("NFC", text)
    except Exception:  # noqa: BLE001
        return text


def _python_batch_nfc_normalize(texts: list[str]) -> list[str]:
    """
    Python fallback: batch NFC normalization.

    Args:
        texts: List of input texts

    Returns:
        List of NFC-normalized texts
    """
    return [_python_nfc_normalize(t) for t in texts]


# =============================================================================
# Text Normalizer Service
# =============================================================================


class TextNormalizerService:
    """
    Service for Unicode NFC normalization.

    C10: Uses Rust nfc_normalize for fast-path with Python fallback.

    Attributes:
        _use_rust: Whether Rust is available (determined at init)
        _nfc: NFC normalization function (Rust or Python)
        _batch_nfc: Batch NFC normalization function (Rust or Python)

    M1 8GB: All fields are lightweight; no heavy state.
    """

    __slots__ = ("_use_rust", "_nfc", "_batch_nfc", "_batch_nfc_fast")

    def __init__(self) -> None:
        """Initialize with Rust fast-path if available."""
        self._use_rust: bool = False
        self._nfc: object = _python_nfc_normalize
        self._batch_nfc: object = _python_batch_nfc_normalize
        self._batch_nfc_fast: object | None = None

        # Probe Rust availability
        rust_funcs = _get_rust_text_norm()
        if rust_funcs is not None:
            nfc_normalize, batch_nfc, batch_nfc_fast = rust_funcs
            self._use_rust = True
            self._nfc = nfc_normalize
            self._batch_nfc = batch_nfc if batch_nfc is not None else _python_batch_nfc_normalize
            self._batch_nfc_fast = batch_nfc_fast

    @property
    def is_rust_available(self) -> bool:
        """Whether Rust acceleration is available."""
        return self._use_rust

    def normalize(self, text: str) -> str:
        """
        Normalize text to NFC form.

        Args:
            text: Input text (may contain decomposed Unicode)

        Returns:
            NFC-normalized text

        Example:
            >>> normalizer = TextNormalizerService()
            >>> normalizer.normalize("cafe\\u0301")  # decomposed
            'café'  # composed NFC
        """
        # Empty string: identity
        if not text:
            return text
        try:
            return self._nfc(text)
        except Exception:  # noqa: BLE001
            # Fail-soft: return original on any error
            return _python_nfc_normalize(text)

    def normalize_batch(self, texts: list[str]) -> list[str]:
        """
        Normalize a batch of texts to NFC form.

        Uses batch_nfc_normalize_fast if Rust available for better performance.
        Large batches (>40k items) are automatically chunked to avoid Rust's
        hard cap (50k) and prevent memory pressure on M1 8GB.

        Args:
            texts: List of input texts

        Returns:
            List of NFC-normalized texts

        Example:
            >>> normalizer = TextNormalizerService()
            >>> normalizer.normalize_batch(["café", "žluťoučký"])
            ['café', 'žluťoučký']
        """
        if not texts:
            return []

        # Large batch: chunk to avoid hitting Rust's BATCH_HARD_CAP (50k)
        if len(texts) > _BATCH_SOFT_CAP:
            return self._normalize_batch_chunked(texts)

        # Try fast-path first (batch_nfc_normalize_fast)
        if self._batch_nfc_fast is not None:
            try:
                return self._batch_nfc_fast(texts)
            except Exception:  # noqa: BLE001
                pass

        # Fall back to standard batch
        try:
            return self._batch_nfc(texts)
        except Exception:  # noqa: BLE001
            # Ultimate fallback: sequential Python
            return _python_batch_nfc_normalize(texts)

    def _normalize_batch_chunked(self, texts: list[str]) -> list[str]:
        """Process large batches in chunks to avoid memory pressure."""
        results: list[str] = []
        for i in range(0, len(texts), _BATCH_CHUNK_SIZE):
            chunk = texts[i : i + _BATCH_CHUNK_SIZE]
            # Process each chunk without the fast-path to avoid cap issues
            try:
                if self._batch_nfc is not None:
                    results.extend(self._batch_nfc(chunk))
                else:
                    results.extend(_python_batch_nfc_normalize(chunk))
            except Exception:  # noqa: BLE001
                results.extend(_python_batch_nfc_normalize(chunk))
        return results

    def normalize_single(self, text: str) -> str:
        """
        Alias for normalize() — single text normalization.

        Args:
            text: Input text

        Returns:
            NFC-normalized text
        """
        return self.normalize(text)


# =============================================================================
# Module-Level Singleton (Lazy Initialization)
# =============================================================================

_TEXT_NORMALIZER: TextNormalizerService | None = None


def get_text_normalizer() -> TextNormalizerService:
    """
    Get or create the module-level TextNormalizerService singleton.

    Returns:
        TextNormalizerService instance (singleton)
    """
    global _TEXT_NORMALIZER
    if _TEXT_NORMALIZER is None:
        _TEXT_NORMALIZER = TextNormalizerService()
    return _TEXT_NORMALIZER
