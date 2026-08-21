"""Deobfuscation domain — Rust backend bridge + pure Python fallback.

Wraps Rust extension for:
  - decode_ioc_candidates(text, max_depth?) -> DeobfuscateResult
  - batch_decode_ioc_candidates(texts, max_depth?) -> list[DeobfuscateResult]
  - deobfuscate_telemetry() -> (passes, layers_stripped, bytes_decoded)
  - deobfuscate_telemetry_reset() -> None

DeobfuscateResult has:
  - candidates: list[str] — decoded IOC candidates
  - layers_stripped: int
  - encodings_detected: list[str]
  - bytes_decoded: int
"""

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class DeobfuscateResult(NamedTuple):
    """Result of deobfuscation pass."""

    candidates: list[str]
    layers_stripped: int
    encodings_detected: list[str]
    bytes_decoded: int


class _RustDeobfuscateDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def decode(self, text: str, max_depth: int | None = None) -> DeobfuscateResult:
        result = self._ext.decode_ioc_candidates(text, max_depth)
        return DeobfuscateResult(
            candidates=result.candidates,
            layers_stripped=result.layers_stripped,
            encodings_detected=result.encodings_detected,
            bytes_decoded=result.bytes_decoded,
        )

    def batch_decode(self, texts: list[str], max_depth: int | None = None) -> list[DeobfuscateResult]:
        raw = self._ext.batch_decode_ioc_candidates(texts, max_depth)
        return [
            DeobfuscateResult(
                candidates=r.candidates,
                layers_stripped=r.layers_stripped,
                encodings_detected=r.encodings_detected,
                bytes_decoded=r.bytes_decoded,
            )
            for r in raw
        ]

    def telemetry(self) -> tuple[int, int, int]:
        return self._ext.deobfuscate_telemetry()

    def reset_telemetry(self) -> None:
        self._ext.deobfuscate_telemetry_reset()


# Module-level imports — shared across all decode() calls
import base64 as _base64
import binascii as _binascii
from urllib.parse import unquote as _unquote


class _PythonDeobfuscateDomain:
    """Pure-Python fallback for deobfuscation.

    Uses base64/hex/urllib unquote to peel encoding layers.
    Less complete than Rust (no ROT13/XOR, no entropy probe), but
    covers the common Base64→Hex cases.

    ponytail: global state-free, no caching, O(n) per call.
    """

    __slots__ = ()  # stateless — all methods are static/conventional

    def decode(self, text: str, max_depth: int | None = None) -> DeobfuscateResult:
        depth = min(max_depth or 3, 5)
        candidates: list[str] = []
        encodings: list[str] = []
        total_layers = 0
        bytes_decoded = 0

        # Stage 1: Try URL decode
        if "%" in text:
            try:
                decoded = _unquote(text)
                if decoded != text and decoded.isprintable():
                    candidates.append(decoded)
                    encodings.append("url_percent")
                    bytes_decoded += len(decoded.encode())
            except Exception:
                pass

        # Stage 2: Try hex decode (IPv6-like, MAC, UUID patterns)
        try:
            clean = text.replace(" ", "").replace("-", "")
            if len(clean) >= 8 and clean.isalnum() and all(c in "0123456789abcdefABCDEF" for c in clean):
                decoded = bytes.fromhex(clean).decode("utf-8", errors="ignore")
                if decoded and decoded.isprintable() and decoded != text:
                    candidates.append(decoded)
                    encodings.append("hex")
                    bytes_decoded += len(decoded.encode())
                    total_layers += 1
        except ValueError, UnicodeDecodeError:
            pass

        # Stage 3: Try base64 with recursive peeling
        try:
            clean64 = text.replace(" ", "")
            if len(clean64) >= 4:
                decoded = _base64.b64decode(clean64, validate=True).decode("utf-8", errors="ignore")
                if decoded and decoded.isprintable() and decoded != text:
                    candidates.append(decoded)
                    encodings.append("base64")
                    bytes_decoded += len(decoded.encode())
                    total_layers += 1

                    # Recursive: peel another layer
                    if depth > 1:
                        inner = self._decode_recursive(decoded, depth - 1)
                        candidates.extend(inner.candidates)
                        encodings.extend(f"{e}+" for e in inner.encodings_detected)
                        total_layers += inner.layers_stripped
                        bytes_decoded += inner.bytes_decoded
        except _binascii.Error:
            pass

        return DeobfuscateResult(
            candidates=candidates,
            layers_stripped=total_layers,
            encodings_detected=encodings,
            bytes_decoded=bytes_decoded,
        )

    def _decode_recursive(self, text: str, depth: int) -> DeobfuscateResult:
        """Recursive inner decode — no URL stage (already peeled)."""
        candidates: list[str] = []
        encodings: list[str] = []
        total_layers = 0
        bytes_decoded = 0

        # Try hex
        try:
            clean = text.replace(" ", "").replace("-", "")
            if len(clean) >= 8 and clean.isalnum() and all(c in "0123456789abcdefABCDEF" for c in clean):
                decoded = bytes.fromhex(clean).decode("utf-8", errors="ignore")
                if decoded and decoded.isprintable() and decoded != text:
                    candidates.append(decoded)
                    encodings.append("hex")
                    bytes_decoded += len(decoded.encode())
                    total_layers += 1
        except ValueError, UnicodeDecodeError:
            pass

        # Try base64 (may nest)
        try:
            clean64 = text.replace(" ", "")
            if len(clean64) >= 4:
                decoded = _base64.b64decode(clean64, validate=True).decode("utf-8", errors="ignore")
                if decoded and decoded.isprintable() and decoded != text:
                    candidates.append(decoded)
                    encodings.append("base64")
                    bytes_decoded += len(decoded.encode())
                    total_layers += 1
                    if depth > 1:
                        inner = self._decode_recursive(decoded, depth - 1)
                        candidates.extend(inner.candidates)
                        encodings.extend(f"{e}+" for e in inner.encodings_detected)
                        total_layers += inner.layers_stripped
                        bytes_decoded += inner.bytes_decoded
        except _binascii.Error:
            pass

        return DeobfuscateResult(
            candidates=candidates,
            layers_stripped=total_layers,
            encodings_detected=encodings,
            bytes_decoded=bytes_decoded,
        )

    def batch_decode(self, texts: list[str], max_depth: int | None = None) -> list[DeobfuscateResult]:
        return [self.decode(t, max_depth) for t in texts]

    def telemetry(self) -> tuple[int, int, int]:
        return (0, 0, 0)  # Python fallback has no telemetry

    def reset_telemetry(self) -> None:
        pass  # No-op for Python fallback


def get_domain(ext: object | None) -> _RustDeobfuscateDomain | _PythonDeobfuscateDomain:
    if ext is not None:
        return _RustDeobfuscateDomain(ext)
    return _PythonDeobfuscateDomain()
