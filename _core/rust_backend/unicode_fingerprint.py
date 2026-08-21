# unicode_fingerprint.py — Unicode Attribution Fingerprint Domain
"""
Zero-Width & Homoglyph Attribution Fingerprint.

ISSUE [ULTIMATE]-005: Extracts invisible character patterns as author-attribution
watermarks for cross-platform identity linking.

Features:
- Zero-width character position patterns (U+200B, U+200C, U+200D, U+FEFF, etc.)
- Homoglyph substitution detection (Cyrillic/Greek → ASCII)
- BIDI override sequence tracking
- SHA-256 fingerprint hash for attribution
- Jaccard similarity for cross-profile matching

M1 8GB safe: ~200 bytes per fingerprint, O(N) single-pass scan.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

ENABLE_UNICODE_ATTRIBUTION: bool = os.environ.get("HLEDAC_ENABLE_UNICODE_ATTRIBUTION", "1") != "0"

ZERO_WIDTH_CHARS: frozenset[int] = frozenset(
    {
        0x200B,  # ZERO_WIDTH_SPACE
        0x200C,  # ZERO_WIDTH_NON_JOINER
        0x200D,  # ZERO_WIDTH_JOINER
        0x200E,  # LEFT_TO_RIGHT_MARK
        0x200F,  # RIGHT_TO_LEFT_MARK
        0x2028,  # LINE_SEPARATOR
        0x2029,  # PARAGRAPH_SEPARATOR
        0x202A,  # LEFT_TO_RIGHT_EMBED
        0x202B,  # RIGHT_TO_LEFT_EMBED
        0x202C,  # POP_DIRECTIONAL_FORMATTING
        0x202D,  # LEFT_TO_RIGHT_OVERRIDE
        0x202E,  # RIGHT_TO_LEFT_OVERRIDE
        0x2060,  # WORD_JOINER
        0x2061,  # FUNCTION_APPLICATION
        0x2062,  # INVISIBLE_TIMES
        0x2063,  # INVISIBLE_SEPARATOR
        0x2064,  # INVISIBLE_PLUS
        0x2066,  # FIRST_STRONG_ISOLATE
        0x2067,  # FIRST_STRONG_ISOLATE
        0x2068,  # FIRST_STRONG_ISOLATE
        0x2069,  # POP_DIRECTIONAL_ISOLATE
        0xFEFF,  # BYTE_ORDER_MARK
        0x034F,  # COMBINING_GRAPHEME_JOINER
        0x061C,  # ARABIC_LETTER_MARK
        0x180E,  # MONGOLIAN_VOWEL_SEPARATOR
        0x200A,  # HAIR_SPACE
        0x205F,  # MEDIUM_MATHEMATICAL_SPACE
    }
)

BIDI_CHARS: dict[int, tuple[str, str]] = {
    0x202A: ("BIDI", "LEFT_TO_RIGHT_EMBED"),
    0x202B: ("BIDI", "RIGHT_TO_LEFT_EMBED"),
    0x202C: ("BIDI", "POP_DIRECTIONAL_FORMATTING"),
    0x202D: ("BIDI", "LEFT_TO_RIGHT_OVERRIDE"),
    0x202E: ("BIDI", "RIGHT_TO_LEFT_OVERRIDE"),
    0x2066: ("BIDI", "FIRST_STRONG_ISOLATE"),
    0x2067: ("BIDI", "FIRST_STRONG_ISOLATE"),
    0x2068: ("BIDI", "FIRST_STRONG_ISOLATE"),
    0x2069: ("BIDI", "POP_DIRECTIONAL_ISOLATE"),
    0x200E: ("BIDI", "LEFT_TO_RIGHT_MARK"),
    0x200F: ("BIDI", "RIGHT_TO_LEFT_MARK"),
    0x061C: ("BIDI", "ARABIC_LETTER_MARK"),
}

# Cyrillic → Latin homoglyphs
CYRILLIC_HOMOGLYPHS: dict[str, str] = {
    "А": "A",
    "а": "a",
    "В": "B",
    "в": "b",
    "С": "C",
    "с": "c",
    "Е": "E",
    "е": "e",
    "Н": "H",
    "н": "h",
    "К": "K",
    "к": "k",
    "М": "M",
    "м": "m",
    "О": "O",
    "о": "o",
    "Р": "P",
    "р": "p",
    "Т": "T",
    "т": "t",
    "Х": "X",
    "х": "x",
    "У": "Y",
    "у": "y",
    "І": "I",
    "і": "i",
    "Ї": "J",
    "ї": "j",
    "Є": "E",
    "є": "e",
    "Ґ": "G",
    "ґ": "g",
}

# Greek → Latin homoglyphs
GREEK_HOMOGLYPHS: dict[str, str] = {
    "Α": "A",
    "α": "a",
    "Β": "B",
    "β": "b",
    "Ε": "E",
    "ε": "e",
    "Κ": "K",
    "κ": "k",
    "Μ": "M",
    "μ": "m",
    "Ν": "N",
    "ν": "n",
    "Ο": "O",
    "ο": "o",
    "Ρ": "P",
    "ρ": "p",
    "Τ": "T",
    "τ": "t",
    "Υ": "Y",
    "υ": "u",
    "Χ": "X",
    "χ": "x",
    "Η": "H",
    "η": "h",
    "Ζ": "Z",
    "ζ": "z",
    "Ι": "I",
    "ι": "i",
}

# Combined homoglyph map
ALL_HOMOGLYPHS: dict[str, str] = {**CYRILLIC_HOMOGLYPHS, **GREEK_HOMOGLYPHS}


@dataclass(frozen=True, slots=True)
class UnicodeFingerprint:
    """
    Zero-Width & Homoglyph Attribution Fingerprint.

    Extracts invisible character patterns as author-attribution watermarks.
    M1 8GB safe: ~200 bytes per fingerprint.
    """

    zero_width_pattern: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    zero_width_density: float = 0.0
    homoglyph_pattern: tuple[tuple[str, str, int], ...] = field(default_factory=tuple)
    unicode_bidi_sequence: tuple[str, ...] = field(default_factory=tuple)
    fingerprint_hash: tuple[int, ...] = field(default_factory=tuple)

    @property
    def fingerprint_hash_hex(self) -> str:
        """Return hex-encoded fingerprint hash."""
        return "".join(f"{b:02x}" for b in self.fingerprint_hash)

    @property
    def is_empty(self) -> bool:
        """Check if fingerprint has any data."""
        return (
            len(self.zero_width_pattern) == 0
            and len(self.homoglyph_pattern) == 0
            and len(self.unicode_bidi_sequence) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "zero_width_pattern": list(self.zero_width_pattern),
            "zero_width_density": self.zero_width_density,
            "homoglyph_pattern": list(self.homoglyph_pattern),
            "unicode_bidi_sequence": list(self.unicode_bidi_sequence),
            "fingerprint_hash": list(self.fingerprint_hash),
            "fingerprint_hash_hex": self.fingerprint_hash_hex,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnicodeFingerprint:
        """Create from dictionary."""
        return cls(
            zero_width_pattern=tuple(data.get("zero_width_pattern", [])),
            zero_width_density=data.get("zero_width_density", 0.0),
            homoglyph_pattern=tuple(data.get("homoglyph_pattern", [])),
            unicode_bidi_sequence=tuple(data.get("unicode_bidi_sequence", [])),
            fingerprint_hash=tuple(data.get("fingerprint_hash", [])),
        )


def _python_extract_fingerprint(text: str) -> UnicodeFingerprint:
    """
    Python fallback: Extract Unicode fingerprint from text.
    O(N) single-pass scan.
    """
    zero_width_pattern: list[tuple[str, int]] = []
    homoglyph_pattern: list[tuple[str, str, int]] = []
    unicode_bidi_sequence: list[str] = []
    visible_char_count = 0

    for pos, char in enumerate(text):
        codepoint = ord(char)

        if codepoint in ZERO_WIDTH_CHARS:
            try:
                name = unicodedata.name(char, "").replace(" ", "_") or f"U+{codepoint:04X}"
            except ValueError:
                name = f"U+{codepoint:04X}"
            zero_width_pattern.append((name, pos))
        elif codepoint not in (0x200B,):  # Don't count zero-width space as visible
            # Check if it's a visible character
            # Python 3 doesn't have str.iscontrol(), so we check manually
            if not char.isspace() and not (0 <= codepoint < 32 or 127 <= codepoint < 160):
                visible_char_count += 1

        if char in ALL_HOMOGLYPHS:
            canonical = ALL_HOMOGLYPHS[char]
            homoglyph_pattern.append((char, canonical, pos))

        if codepoint in BIDI_CHARS:
            _, name = BIDI_CHARS[codepoint]
            unicode_bidi_sequence.append(name)

    # Calculate density
    zero_width_density = (len(zero_width_pattern) / visible_char_count * 1000) if visible_char_count > 0 else 0.0

    # Compute SHA-256 fingerprint hash
    import hashlib

    hasher = hashlib.sha256()
    for name, pos in zero_width_pattern:
        hasher.update(f"{name}:{pos}:".encode())
    for orig, canon, pos in homoglyph_pattern:
        hasher.update(f"{orig}->{canon}:{pos}:".encode())
    for seq in unicode_bidi_sequence:
        hasher.update(f"{seq}:".encode())
    hash_bytes = hasher.digest()
    fingerprint_hash = tuple(hash_bytes)

    return UnicodeFingerprint(
        zero_width_pattern=tuple(zero_width_pattern),
        zero_width_density=zero_width_density,
        homoglyph_pattern=tuple(homoglyph_pattern),
        unicode_bidi_sequence=tuple(unicode_bidi_sequence),
        fingerprint_hash=fingerprint_hash,
    )


def _python_compute_similarity(a: UnicodeFingerprint, b: UnicodeFingerprint) -> float:
    """
    Compute Jaccard similarity between two fingerprints.
    Weighted combination of:
    - Zero-width pattern: 40%
    - Homoglyph pattern: 20%
    - BIDI sequence: 10%
    - Hash match: 30%
    """
    # Zero-width Jaccard
    zw_a = set(a.zero_width_pattern)
    zw_b = set(b.zero_width_pattern)
    if zw_a or zw_b:
        zw_intersection = len(zw_a & zw_b)
        zw_union = len(zw_a | zw_b)
        zw_jaccard = zw_intersection / zw_union if zw_union > 0 else 0.0
    else:
        zw_jaccard = 1.0  # Both empty = perfect match

    # Homoglyph Jaccard
    hg_a = set(a.homoglyph_pattern)
    hg_b = set(b.homoglyph_pattern)
    if hg_a or hg_b:
        hg_intersection = len(hg_a & hg_b)
        hg_union = len(hg_a | hg_b)
        hg_jaccard = hg_intersection / hg_union if hg_union > 0 else 0.0
    else:
        hg_jaccard = 1.0

    # BIDI Jaccard
    bidi_a = set(a.unicode_bidi_sequence)
    bidi_b = set(b.unicode_bidi_sequence)
    if bidi_a or bidi_b:
        bidi_intersection = len(bidi_a & bidi_b)
        bidi_union = len(bidi_a | bidi_b)
        bidi_jaccard = bidi_intersection / bidi_union if bidi_union > 0 else 0.0
    else:
        bidi_jaccard = 1.0

    # Hash match (hash is a 32-byte tuple, not empty tuple)
    ZERO_HASH = (0,) * 32
    hash_match = 1.0 if (a.fingerprint_hash == b.fingerprint_hash and a.fingerprint_hash != ZERO_HASH) else 0.0

    # Weighted combination
    return (zw_jaccard * 0.4) + (hg_jaccard * 0.2) + (bidi_jaccard * 0.1) + (hash_match * 0.3)


class _RustUnicodeFingerprintDomain:
    """Rust-accelerated Unicode fingerprint domain."""

    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def extract_fingerprint(self, text: str) -> UnicodeFingerprint:
        """Extract Unicode fingerprint from text."""
        result = self._ext.extract_fingerprint(text)
        return UnicodeFingerprint(
            zero_width_pattern=tuple(result.zero_width_pattern),
            zero_width_density=result.zero_width_density,
            homoglyph_pattern=tuple((chr(o), chr(c), p) for o, c, p in result.homoglyph_pattern),
            unicode_bidi_sequence=tuple(result.unicode_bidi_sequence),
            fingerprint_hash=tuple(result.fingerprint_hash),
        )

    def compute_similarity(self, a: UnicodeFingerprint, b: UnicodeFingerprint) -> float:
        """
        Compute similarity between two fingerprints.

        Note: Falls back to Python implementation because similarity computation
        requires passing fingerprints back to Rust, which would need to create
        PyUnicodeFingerprint wrapper objects. For M1 8GB, Python O(N) comparison
        is fast enough (<1ms for typical fingerprints).
        """
        return _python_compute_similarity(a, b)

    def batch_extract(self, texts: list[str]) -> list[UnicodeFingerprint]:
        """Extract fingerprints from multiple texts."""
        return [self.extract_fingerprint(text) for text in texts]


class _PythonUnicodeFingerprintDomain:
    """Python fallback Unicode fingerprint domain."""

    __slots__ = ()

    def extract_fingerprint(self, text: str) -> UnicodeFingerprint:
        """Extract Unicode fingerprint from text."""
        return _python_extract_fingerprint(text)

    def compute_similarity(self, a: UnicodeFingerprint, b: UnicodeFingerprint) -> float:
        """Compute similarity between two fingerprints."""
        return _python_compute_similarity(a, b)

    def batch_extract(self, texts: list[str]) -> list[UnicodeFingerprint]:
        """Extract fingerprints from multiple texts."""
        return [_python_extract_fingerprint(text) for text in texts]


def get_unicode_fingerprint_domain(
    ext: object | None,
) -> _RustUnicodeFingerprintDomain | _PythonUnicodeFingerprintDomain:
    """Factory: return Rust or Python domain based on ext availability."""
    if ext is not None and ENABLE_UNICODE_ATTRIBUTION:
        try:
            return _RustUnicodeFingerprintDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonUnicodeFingerprintDomain()


_unicode_domain_singleton: _RustUnicodeFingerprintDomain | _PythonUnicodeFingerprintDomain | None = None


def _get_singleton_domain() -> _RustUnicodeFingerprintDomain | _PythonUnicodeFingerprintDomain:
    """Get or create singleton domain instance."""
    global _unicode_domain_singleton
    if _unicode_domain_singleton is None:
        # Try to get Rust extension
        try:
            from hledac.universal._core.rust_backend import rust

            ext = getattr(rust, "_ext", None)
            _unicode_domain_singleton = get_unicode_fingerprint_domain(ext)
        except Exception:
            _unicode_domain_singleton = _PythonUnicodeFingerprintDomain()
    return _unicode_domain_singleton


def extract_fingerprint(text: str) -> UnicodeFingerprint:
    """
    Extract Unicode fingerprint from text.

    Uses Rust backend if available, falls back to pure Python.
    This is a convenience function for simple usage without domain setup.
    """
    domain = _get_singleton_domain()
    return domain.extract_fingerprint(text)


def compute_similarity(a: UnicodeFingerprint, b: UnicodeFingerprint) -> float:
    """
    Compute similarity between two Unicode fingerprints.

    Returns Jaccard similarity score [0, 1].
    This is a convenience function for simple usage.
    """
    domain = _get_singleton_domain()
    return domain.compute_similarity(a, b)


def batch_extract(texts: list[str]) -> list[UnicodeFingerprint]:
    """
    Extract fingerprints from multiple texts.

    Uses Rust backend if available, falls back to pure Python.
    """
    domain = _get_singleton_domain()
    return domain.batch_extract(texts)


__all__ = [
    "ENABLE_UNICODE_ATTRIBUTION",
    "UnicodeFingerprint",
    "ZERO_WIDTH_CHARS",
    "BIDI_CHARS",
    "ALL_HOMOGLYPHS",
    "extract_fingerprint",
    "compute_similarity",
    "batch_extract",
    "get_unicode_fingerprint_domain",
]
