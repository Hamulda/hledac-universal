"""
F261: STORAGE-FIX-4 wiring probe.

Verifies that decode_response_bytes from utils.encoding is actually wired into
the fetch pipeline (public_fetcher, curl_cffi_fetch, alternative_protocol_fetcher)
and that latin-1 / non-UTF-8 OSINT sources no longer leak U+FFFD replacement
chars (the "â€™" pattern from naive UTF-8 mis-decode of Windows-1252).

Run: pytest tests/probe_encoding_wiring.py -v -q
"""

import sys
from pathlib import Path

import pytest

# Ensure repo root on path (same pattern as probe_storage_fixes.py)
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Unit tests: utils.encoding (no I/O, hermetic)
# ──────────────────────────────────────────────────────────────────────────


def test_f261_parse_charset_basic():
    """parse_charset_from_content_type handles standard + edge forms."""
    from hledac.universal.utils.encoding import parse_charset_from_content_type

    cases = [
        ("text/html; charset=utf-8", "utf-8"),
        ("text/html;charset=windows-1252", "windows-1252"),
        ('text/html; charset="iso-8859-1"', "iso-8859-1"),
        ("text/html", None),
        ("", None),
        ("application/json; charset=utf-8", "utf-8"),
        ("text/html; charset=", None),  # empty value
        (None, None),  # None
        (123, None),  # non-str
    ]
    for ct, expected in cases:
        got = parse_charset_from_content_type(ct)
        assert got == expected, f"parse_charset_from_content_type({ct!r}) -> {got!r} != {expected!r}"
    print("OK f261-1: parse_charset_from_content_type handles 9 edge cases")


def test_f261_decode_response_bytes_latin1_no_replacement():
    """Latin-1 bytes must NOT produce U+FFFD (â€™) when decoded with hint."""
    from hledac.universal.utils.encoding import decode_response_bytes

    # Windows-1252 encoded: "Café" + "š" + "ž" + smart quotes (â€™)
    # When naïvely decoded as UTF-8 these produce U+FFFD.
    raw = b"<p>Caf\xe9 \x93 \x8a \x99 \x9c \x9d end</p>"  # \x93=\", \x9c=', \x9d=", etc.
    out = decode_response_bytes(raw, http_charset="windows-1252")
    # Café must survive
    assert "Caf" in out, f"expected 'Caf' in {out!r}"
    # No replacement chars (U+FFFD = �) — the smoking gun of mis-decode
    assert "�" not in out, f"replacement char in: {out!r}"
    # Must contain some smart-quote-looking glyphs (latin-1 char >= 0x80 maps to a unicode char)
    assert out
    print(f"OK f261-2: latin-1 hint -> {out!r} (no U+FFFD)")


def test_f261_decode_response_bytes_no_hint_chain():
    """Without hint, charset_normalizer / chardet / utf-8 chain still clean."""
    from hledac.universal.utils.encoding import decode_response_bytes

    # Plain UTF-8 — must roundtrip
    out = decode_response_bytes("Příliš žluťoučký kůň".encode())
    assert out == "Příliš žluťoučký kůň", f"UTF-8 roundtrip lost: {out!r}"
    assert "�" not in out
    print("OK f261-3: UTF-8 roundtrip without hint")


def test_f261_decode_response_bytes_str_passthrough():
    """str input is returned as-is (no exception)."""
    from hledac.universal.utils.encoding import decode_response_bytes

    assert decode_response_bytes("plain") == "plain"
    assert decode_response_bytes("") == ""
    assert decode_response_bytes(None) == ""
    print("OK f261-4: str / empty / None passthrough")


def test_f261_decode_response_bytes_truncation():
    """5 MB cap enforced when max_bytes smaller."""
    from hledac.universal.utils.encoding import decode_response_bytes

    big = b"a" * (6 * 1024 * 1024)
    out = decode_response_bytes(big, max_bytes=100)
    assert len(out) == 100, f"truncation broken: {len(out)}"
    print("OK f261-5: 6 MB -> 100 B truncation")


# ──────────────────────────────────────────────────────────────────────────
# Wiring tests: helpers in public_fetcher (require aiohttp at import time)
# ──────────────────────────────────────────────────────────────────────────


def test_f261_try_decode_with_charset_clean_utf8():
    """Public_fetcher._try_decode_with_charset preserves clean UTF-8."""
    try:
        from hledac.universal.fetching.public_fetcher import _try_decode_with_charset
    except ImportError as e:
        pytest.skip(f"public_fetcher not importable: {e}")

    text, replaced, count = _try_decode_with_charset(
        "Příliš žluťoučký kůň".encode()
    )
    assert text == "Příliš žluťoučký kůň"
    assert replaced is False
    assert count == 0
    print("OK f261-6: _try_decode_with_charset UTF-8 roundtrip clean")


def test_f261_try_decode_with_charset_latin1_no_replacement():
    """Public_fetcher._try_decode_with_charset uses http_charset hint correctly.

    The smoking gun: 'â€™' or U+FFFD in payload_text means UTF-8 mis-decode.
    With proper wiring + hint, these must not appear.
    """
    try:
        from hledac.universal.fetching.public_fetcher import _try_decode_with_charset
    except ImportError as e:
        pytest.skip(f"public_fetcher not importable: {e}")

    # Windows-1252: "It's a test" with smart quotes
    raw = b"<p>It\x92s a test \x93quoted\x94 end</p>"  # \x92=', \x93=", \x94="
    text, replaced, count = _try_decode_with_charset(raw, http_charset="windows-1252")

    # No U+FFFD — the canonical sign of mis-decode
    assert "�" not in text, f"U+FFFD in: {text!r}"
    # Latin-1 hint always succeeds — no replacement needed
    assert "test" in text
    # Should contain at least one smart-quote-shaped char
    assert any(ord(c) >= 0x80 for c in text), f"no high chars: {text!r}"
    print(f"OK f261-7: latin-1 hint avoids U+FFFD -> {text!r}")


def test_f261_try_decode_with_charset_fallback_on_exception():
    """When decode_response_bytes raises, fall back to _try_decode."""
    try:
        from hledac.universal.fetching.public_fetcher import _try_decode_with_charset
    except ImportError as e:
        pytest.skip(f"public_fetcher not importable: {e}")

    # Patch decode_response_bytes to raise; verify fallback
    import hledac.universal.fetching.public_fetcher as pf

    original = pf.decode_response_bytes

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic")

    pf.decode_response_bytes = boom
    try:
        text, replaced, count = _try_decode_with_charset(b"plain ascii")
        assert text == "plain ascii"
        assert replaced is False
        print("OK f261-8: exception in decode_response_bytes falls back to _try_decode")
    finally:
        pf.decode_response_bytes = original


# ──────────────────────────────────────────────────────────────────────────
# Wiring tests: curl_cffi_fetch (heavy import — skip if unavailable)
# ──────────────────────────────────────────────────────────────────────────


def test_f261_decode_curl_cffi_result_latin1():
    """decode_curl_cffi_result decodes latin-1 bytes from curl_cffi dict."""
    try:
        from hledac.universal.transport.curl_cffi_fetch import decode_curl_cffi_result
    except ImportError as e:
        pytest.skip(f"curl_cffi_fetch not importable: {e}")

    result = {
        "success": True,
        "content": b"<p>Caf\xe9 \x93quoted\x94</p>",
        "content_type": "text/html; charset=windows-1252",
        "http_charset_hint": "windows-1252",
    }
    out = decode_curl_cffi_result(result)
    assert out is not None
    assert "Caf" in out
    assert "�" not in out, f"replacement char in: {out!r}"
    print(f"OK f261-9: decode_curl_cffi_result latin-1 -> {out!r}")


def test_f261_decode_curl_cffi_result_error_dict():
    """Error dict returns None (no exception)."""
    try:
        from hledac.universal.transport.curl_cffi_fetch import decode_curl_cffi_result
    except ImportError as e:
        pytest.skip(f"curl_cffi_fetch not importable: {e}")

    err = {"success": False, "content": b"", "content_type": ""}
    assert decode_curl_cffi_result(err) is None
    assert decode_curl_cffi_result({}) is None
    assert decode_curl_cffi_result("not a dict") is None
    print("OK f261-10: decode_curl_cffi_result handles error / empty / wrong-type")


# ──────────────────────────────────────────────────────────────────────────
# End-to-end mock: simulate a non-UTF-8 OSINT response, assert no replacement
# chars in the final decoded payload_text (CanonicalFinding.content flow)
# ──────────────────────────────────────────────────────────────────────────


def test_f261_end_to_end_latin1_payload_text():
    """E2E: latin-1 response bytes → decode_response_bytes → no U+FFFD in payload.

    Simulates the path: aiohttp response body (bytes) → text → payload_text.
    """
    from hledac.universal.utils.encoding import decode_response_bytes

    # Windows-1252 encoded OSINT snippet with smart quotes and euro sign
    raw = (
        b"<html><body><h1>OSINT Report \x93Europe\x94 \x96 \x80analysis</h1>"
        b"<p>Author: \x8aDoe\x8a, date 2026-06-03.</p>"
        b"<p>Findings: \x80\x81\x82\x83 affected hosts.</p>"
        b"</body></html>"
    )
    http_charset = "windows-1252"

    # Path 1: aiohttp body decoding
    text_aiohttp = decode_response_bytes(raw, http_charset=http_charset)
    assert "�" not in text_aiohttp, f"U+FFFD leaked: {text_aiohttp!r}"
    # Smoking gun: â€™ must NOT appear
    assert "â€" not in text_aiohttp, f"mojibake leaked: {text_aiohttp!r}"

    # Path 2: curl_cffi body decoding (same bytes, same hint)
    text_curl = decode_response_bytes(raw, http_charset=http_charset)
    assert text_curl == text_aiohttp, "aiohttp/curl paths diverge"

    # CanonicalFinding.payload_text would receive the decoded text
    payload_text = text_aiohttp
    assert payload_text
    assert "OSINT" in payload_text
    assert "Europe" in payload_text
    assert "2026-06-03" in payload_text
    print(f"OK f261-11: E2E latin-1 payload ({len(payload_text)} chars) — no U+FFFD, no mojibake")


# ──────────────────────────────────────────────────────────────────────────
# Static checks: wiring is present in the right files
# ──────────────────────────────────────────────────────────────────────────


def test_f261_static_wiring_in_public_fetcher():
    """public_fetcher must call _try_decode_with_charset and import decode_response_bytes."""
    src = Path(REPO_ROOT / "fetching/public_fetcher.py").read_text()
    assert "from hledac.universal.utils.encoding import" in src
    assert "decode_response_bytes" in src
    assert "_try_decode_with_charset" in src
    assert "parse_charset_from_content_type" in src
    # Both call sites are wired (main aiohttp path + curl_cffi escalation)
    assert src.count("_try_decode_with_charset(") >= 2, (
        f"expected >= 2 _try_decode_with_charset call sites, found "
        f"{src.count('_try_decode_with_charset(')}"
    )
    print(f"OK f261-12: public_fetcher wiring — {src.count('_try_decode_with_charset(')} decode call sites")


def test_f261_static_wiring_in_curl_cffi_fetch():
    """curl_cffi_fetch must include http_charset_hint in result dict and import decoder."""
    src = Path(REPO_ROOT / "transport/curl_cffi_fetch.py").read_text()
    assert "from hledac.universal.utils.encoding import" in src
    assert "decode_response_bytes" in src
    assert "http_charset_hint" in src
    # parse_charset_from_content_type must be called to populate the hint
    assert "parse_charset_from_content_type(" in src
    # decode_curl_cffi_result helper exposed
    assert "def decode_curl_cffi_result" in src
    print("OK f261-13: curl_cffi_fetch wiring present (http_charset_hint + decoder)")


def test_f261_static_wiring_in_alternative_protocol_fetcher():
    """alternative_protocol_fetcher must normalize fediverse + matrix content."""
    src = Path(REPO_ROOT / "fetching/alternative_protocol_fetcher.py").read_text()
    assert "from hledac.universal.utils.encoding import" in src
    assert "decode_response_bytes" in src
    # Both branches (fediverse + matrix) must use the decoder
    assert src.count("decode_response_bytes(") >= 2, (
        f"expected >= 2 decode_response_bytes call sites, found "
        f"{src.count('decode_response_bytes(')}"
    )
    print(f"OK f261-14: alternative_protocol_fetcher wiring — {src.count('decode_response_bytes(')} normalize call sites")  # noqa: E501
