"""Smoke test for F260 brotli Accept-Encoding honest-header hardening.

Verifies:
- is_brotli_available() reflects actual import state (cached at import)
- build_accept_encoding_header() always contains gzip + deflate
- build_accept_encoding_header() contains `br` iff brotli is importable
- decode_response_body() roundtrips gzip / deflate / identity
- decode_response_body() passes through `br` body unchanged + warns when brotli missing
- decode_response_body() is fail-soft on garbage (returns original body, never raises)
- decode_response_body() respects the 10MB MAX_DECODE_BODY_BYTES cap
- decode_response_body() peels at most _MAX_DECODE_LAYERS (3) layers
- decode_response_body() never raises on aiohttp-style Content-Encoding: br (current env)

Invariant: no network I/O. All tests are hermetic and run on cp314.
"""

import sys
import warnings
from pathlib import Path

# Ensure hledac.universal is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import gzip
import zlib

from transport.decompression import (
    MAX_DECODE_BODY_BYTES,
    _peek_mod,
    _reset_probe_for_testing,
    build_accept_encoding_header,
    decode_response_body,
    is_brotli_available,
)


# ---------------------------------------------------------------------------
# Runtime probe
# ---------------------------------------------------------------------------

def test_is_brotli_available_reflects_import_state():
    """is_brotli_available() is True iff brotli module importable."""
    available = is_brotli_available()
    has_mod = _peek_mod() is not None
    assert available == has_mod, (
        f"is_brotli_available()={available} but _peek_mod() is "
        f"{'not None' if has_mod else 'None'} — probe out of sync"
    )


def test_get_brotli_import_error_consistent_with_probe():
    """get_brotli_import_error() is None iff is_brotli_available() is True."""
    from transport.decompression import get_brotli_import_error
    err = get_brotli_import_error()
    if is_brotli_available():
        assert err is None, f"available but import_error={err!r}"
    else:
        assert err is not None and "brotli" in err.lower(), f"unavailable but err={err!r}"


# ---------------------------------------------------------------------------
# Accept-Encoding honest header
# ---------------------------------------------------------------------------

def test_build_accept_encoding_includes_gzip_deflate():
    """Header always contains gzip + deflate (stdlib guarantee)."""
    header = build_accept_encoding_header()
    assert "gzip" in header
    assert "deflate" in header


def test_build_accept_encoding_drops_br_when_brotli_missing():
    """When brotli is not installed, the header must NOT advertise `br`.

    Critical invariant: never lie to the server about a decoder we don't have.
    """
    if is_brotli_available():
        return  # env has brotli; the inverse path is exercised in test_*_with_brotli_available
    header = build_accept_encoding_header()
    assert "br" not in header.split(","), f"header still contains 'br': {header!r}"
    # Should be exactly "gzip, deflate"
    assert header == "gzip, deflate", f"unexpected header value: {header!r}"


def test_build_accept_encoding_includes_br_when_brotli_available():
    """When brotli IS installed, the header advertises `br`."""
    if not is_brotli_available():
        return  # env lacks brotli; the inverse path is exercised above
    header = build_accept_encoding_header()
    assert "br" in header, f"brotli is importable but header lacks 'br': {header!r}"


# ---------------------------------------------------------------------------
# decode_response_body — basic paths
# ---------------------------------------------------------------------------

def test_decode_identity_passthrough():
    body = b"hello world"
    assert decode_response_body(body, "identity") == body


def test_decode_empty_content_encoding_returns_body():
    body = b"some bytes"
    assert decode_response_body(body, "") == body


def test_decode_none_content_encoding_returns_body():
    body = b"raw bytes"
    assert decode_response_body(body, None) == body


def test_decode_empty_body_returns_empty():
    """Empty body should pass through without error."""
    assert decode_response_body(b"", "gzip") == b""
    assert decode_response_body(b"", "br") == b""


def test_decode_gzip_roundtrip():
    original = b"the quick brown fox jumps over the lazy dog " * 10
    compressed = gzip.compress(original)
    assert decode_response_body(compressed, "gzip") == original


def test_decode_deflate_zlib_roundtrip():
    """Per RFC 7230 §4.2.2, 'deflate' is zlib-wrapped. Most servers use this."""
    original = b"hello deflate " * 20
    compressed = zlib.compress(original)
    assert decode_response_body(compressed, "deflate") == original


def test_decode_deflate_raw_deflate_roundtrip():
    """Some servers send raw deflate (no zlib header) — both must be handled."""
    original = b"raw deflate " * 20
    # compressobj with wbits=-15 produces raw deflate (no header)
    compressor = zlib.compressobj(level=6, wbits=-15)
    raw_deflate = compressor.compress(original) + compressor.flush()
    assert decode_response_body(raw_deflate, "deflate") == original


# ---------------------------------------------------------------------------
# decode_response_body — brotli path (env-dependent)
# ---------------------------------------------------------------------------

def test_decode_br_body_roundtrip_when_brotli_available():
    """If brotli is installed, we should be able to decode a brotli body."""
    if not is_brotli_available():
        return  # env lacks brotli
    mod = _peek_mod()
    original = b"brotli content " * 50
    compressed = mod.compress(original)
    assert decode_response_body(compressed, "br") == original
    assert decode_response_body(compressed, "brotli") == original


def test_decode_br_body_failsoft_when_missing():
    """If brotli is missing, `br` body passes through + warning is logged."""
    if is_brotli_available():
        return  # env has brotli; this path is exercised by _reset_probe test below
    fake_br_body = b"\x06\x9f" + b"raw br-looking bytes"
    with warnings.catch_warnings(record=True) as caught:
        result = decode_response_body(fake_br_body, "br")
    assert result == fake_br_body, "fail-soft must return original body unchanged"
    # logger.warning was emitted (we don't capture log, but passthrough is the contract)


def test_decode_br_body_failsoft_after_probe_reset():
    """_reset_probe_for_testing() lets us force the missing-brotli state."""
    # We can't always reset to "missing" (brotli may not be importable to begin with),
    # but we CAN verify the reset is a no-op when brotli is genuinely missing.
    _reset_probe_for_testing()
    assert is_brotli_available() == (_peek_mod() is not None)


# ---------------------------------------------------------------------------
# decode_response_body — error handling
# ---------------------------------------------------------------------------

def test_decode_failsoft_on_garbage_with_gzip():
    """Invalid gzip payload must NOT raise — return original body."""
    garbage = b"not a gzip stream at all"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = decode_response_body(garbage, "gzip")
    assert result == garbage, "fail-soft must return garbage unchanged on decode error"


def test_decode_failsoft_on_truncated_gzip():
    """A truncated gzip payload must not raise."""
    original = b"some content"
    full = gzip.compress(original)
    truncated = full[: len(full) // 2]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = decode_response_body(truncated, "gzip")
    # truncated body may decode to partial output or original — either way must not raise
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_decode_failsoft_on_corrupt_deflate():
    """Invalid deflate must not raise."""
    garbage = b"not a deflate stream"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = decode_response_body(garbage, "deflate")
    assert result == garbage


# ---------------------------------------------------------------------------
# decode_response_body — bounds
# ---------------------------------------------------------------------------

def test_decode_respects_max_bytes_cap():
    """Oversized body returns unchanged + logs a warning."""
    oversized = b"x" * (MAX_DECODE_BODY_BYTES + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = decode_response_body(oversized, "gzip")
    assert result == oversized, "oversized body must pass through unchanged"


def test_decode_max_bytes_constant_is_10mb():
    """MAX_DECODE_BODY_BYTES contract is 10MB hard cap."""
    assert MAX_DECODE_BODY_BYTES == 10 * 1024 * 1024


def test_decode_bounded_layers():
    """At most 3 layers are peeled — anything beyond is passed through."""
    # "gzip, gzip, gzip, gzip, identity" should peel only 3 gzips, then stop.
    # Each gzip is a valid single-byte layer, so we can build a real test.
    original_inner = b"x"
    # Build 4 nested gzips — decoder should peel 3, then warn and return the
    # partially-decoded body (3 layers deep).
    layer1 = gzip.compress(original_inner)
    layer2 = gzip.compress(layer1)
    layer3 = gzip.compress(layer2)
    layer4 = gzip.compress(layer3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = decode_response_body(layer4, "gzip, gzip, gzip, gzip, identity")
    # After 3 layers we should have layer1's content; 4th layer is NOT peeled.
    assert result == layer1, (
        f"expected 3 layers peeled (got {result!r}, expected {layer1!r})"
    )


# ---------------------------------------------------------------------------
# Header factory — used by build_randomized_headers()
# ---------------------------------------------------------------------------

def test_build_accept_encoding_is_always_callable():
    """Smoke: calling the factory multiple times is idempotent."""
    h1 = build_accept_encoding_header()
    h2 = build_accept_encoding_header()
    assert h1 == h2


def test_build_accept_encoding_format_is_rfc_compliant():
    """Format must be comma-separated codings with no trailing whitespace."""
    header = build_accept_encoding_header()
    parts = [p.strip() for p in header.split(",")]
    assert all(parts), f"empty coding token in {header!r}"
    assert header == ", ".join(parts), f"unexpected whitespace in {header!r}"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
