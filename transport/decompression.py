"""transport/decompression.py

Bounded, fail-soft HTTP response body decoder.
Honest Accept-Encoding advertisement: only advertise `br` if a decoder is importable.

Invariants:
- No top-level side effects beyond a try/except import of brotlicffi.
- Runtime probe is cached at first call.
- decode_response_body() never raises — on any error it returns the original body
  and logs a warning. This is the canonical fail-soft contract for transport.
- Bounds: input body must already be ≤ MAX_DECODE_BODY_BYTES (10MB); oversized
  input is rejected up-front, not partially decompressed.

Why brotlicffi and not brotli:
- brotlicffi is a pure CFFI binding with M1 arm64 wheels.
- brotli (the CFFI-named package) has the same name conflict — brotlicffi
  disambiguates and works on platforms where the Brotli C extension is missing.
"""



import gzip
import logging
import zlib
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# Mirrors MAX_BYTES_HARD in fetching/public_fetcher.py — kept here as a
# self-contained contract so the decoder can be used from any transport lane.
MAX_DECODE_BODY_BYTES: int = 10 * 1024 * 1024  # 10MB

# Runtime probe — cached at first import. Any ImportError becomes a clean False.
try:
    import brotlicffi as _brotli_mod  # type: ignore[import-not-found]

    _BROTLI_AVAILABLE: bool = True
    _BROTLI_IMPORT_ERROR: str | None = None
except ImportError as _brotli_err:
    _brotli_mod = None  # type: ignore[assignment]
    _BROTLI_AVAILABLE = False
    _BROTLI_IMPORT_ERROR = str(_brotli_err)

# Maximum codings we are willing to attempt in one body. Defends against
# pathological multi-layered encodings (e.g. "gzip, br, gzip, br, ...") that
# some misconfigured test servers emit.
_MAX_DECODE_LAYERS: int = 3


def is_brotli_available() -> bool:
    """True iff a brotli decoder is importable in the current environment."""
    return _BROTLI_AVAILABLE


def get_brotli_import_error() -> str | None:
    """Return the original ImportError if brotli is unavailable, else None.

    Useful for telemetry — never expose to callers without an explicit ask.
    """
    return _BROTLI_IMPORT_ERROR


def _decode_one_layer(body: bytes, coding: str) -> bytes | None:
    """Try to decode one layer. Returns None if the coding is unsupported
    or the input is not valid for that coding (caller will treat None as
    'passthrough and warn')."""
    coding_norm = coding.strip().lower()
    if coding_norm in ("identity", ""):
        return body
    if coding_norm == "gzip":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as exc:
            logger.debug(f"gzip decode failed: {exc}")
            return None
    if coding_norm == "deflate":
        # Per RFC 7230 §4.2.2, "deflate" is technically zlib-wrapped deflate.
        # Some servers send raw deflate (no zlib header). Try zlib first, then raw.
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error as exc:
                logger.debug(f"deflate decode failed: {exc}")
                return None
    if coding_norm in ("br", "brotli"):
        if _brotli_mod is None:
            return None
        try:
            decompressor = _brotli_mod.Decompressor()
            return decompressor.process(body)
        except Exception as exc:  # brotlicffi raises a mix of ValueError/Exception
            logger.debug(f"brotli decode failed: {exc}")
            return None
    # Compress / x-gzip / etc. — not supported. Return None to passthrough.
    return None


def decode_response_body(body: bytes, content_encoding: str | None) -> bytes:
    """Decode a response body according to its Content-Encoding header.

    Content-Encoding grammar (RFC 7231 §3.1.2.2):
        Content-Encoding = 1#encoding
    where "encoding" is a comma-separated list of codings in the order they
    were applied (innermost first).

    Bounded:
        - Input body must be ≤ MAX_DECODE_BODY_BYTES (10MB). If larger, return
          the body unchanged and log a warning — we do NOT partially decode
          oversized bodies, because the upstream cap should have caught it.
        - At most _MAX_DECODE_LAYERS (3) layers are peeled. Anything beyond
          that is a server misconfiguration; pass through the partial result.

    Fail-soft:
        - Any decode error → return the body at that layer unchanged + warn.
        - Unsupported coding → leave that layer intact, move on to the next.
        - Never raises.
    """
    if not content_encoding:
        return body
    if not body:
        return body
    if len(body) > MAX_DECODE_BODY_BYTES:
        logger.warning(
            f"decode_response_body: body {len(body)} bytes exceeds cap "
            f"{MAX_DECODE_BODY_BYTES}, returning original"
        )
        return body

    codings = [c.strip() for c in content_encoding.split(",") if c.strip()]
    if not codings:
        return body

    current = body
    for i, coding in enumerate(codings[:_MAX_DECODE_LAYERS]):
        decoded = _decode_one_layer(current, coding)
        if decoded is None:
            if coding in ("br", "brotli") and not _BROTLI_AVAILABLE:
                logger.warning(
                    f"server sent Content-Encoding: br but brotli is not installed "
                    f"(install with: uv pip install '.[osint-compression]'); "
                    f"returning body unchanged ({len(current)} bytes)"
                )
            else:
                logger.warning(
                    f"decode_response_body: failed to decode layer {i} "
                    f"({coding!r}), returning partial result"
                )
            return current
        current = decoded
    return current


def build_accept_encoding_header() -> str:
    """Build an honest Accept-Encoding header value.

    Always advertises gzip + deflate (stdlib guarantee).
    Only adds `br` when a brotli decoder is actually importable — never lie
    to the server about capabilities we don't have.
    """
    codings = ["gzip", "deflate"]
    if _BROTLI_AVAILABLE:
        codings.append("br")
    return ", ".join(codings)


__all__ = [
    "MAX_DECODE_BODY_BYTES",
    "build_accept_encoding_header",
    "decode_response_body",
    "get_brotli_import_error",
    "is_brotli_available",
]


# Internal helper, exposed for tests only
def _reset_probe_for_testing() -> None:
    """Test-only: re-run the import probe. Not part of the public API."""
    global _BROTLI_AVAILABLE, _BROTLI_IMPORT_ERROR, _brotli_mod
    try:
        import brotlicffi as mod  # type: ignore[import-not-found]
        _brotli_mod = mod
        _BROTLI_AVAILABLE = True
        _BROTLI_IMPORT_ERROR = None
    except ImportError as err:
        _brotli_mod = None  # type: ignore[assignment]
        _BROTLI_AVAILABLE = False
        _BROTLI_IMPORT_ERROR = str(err)


def _peek_mod() -> Any:
    """Test-only: return the cached brotli module (or None). Not part of public API."""
    return _brotli_mod
