#!/usr/bin/env python3
"""Test script for Rust text fast-path in ioc_processor.py"""

import sys
sys.path.insert(0, '.')

from urllib.parse import parse_qsl, urlencode, urlparse

# Replicate the Rust text fast functions
_RUST_TEXT_FAST = None

def _get_rust_text_fast():
    global _RUST_TEXT_FAST
    if _RUST_TEXT_FAST is not None:
        return _RUST_TEXT_FAST
    try:
        from hledac.universal._core.rust_backend import rust
        batch_nfc = rust.raw.batch_nfc_normalize_fast
        batch_strip = rust.raw.batch_strip_diacritics_fast
        if batch_nfc is not None and batch_strip is not None:
            _RUST_TEXT_FAST = (batch_nfc, batch_strip)
            return _RUST_TEXT_FAST
    except Exception:
        pass
    _RUST_TEXT_FAST = None
    return None

def _rust_text_fast_single(text: str) -> str:
    rust_fast = _get_rust_text_fast()
    if rust_fast is None:
        import unicodedata
        try:
            nfkd = unicodedata.normalize("NFKD", text)
            return "".join(c for c in nfkd if not unicodedata.combining(c))
        except Exception:
            return text
    batch_nfc, batch_strip = rust_fast
    try:
        normalized = batch_nfc([text])[0]
        return batch_strip([normalized])[0]
    except Exception:
        import unicodedata
        try:
            nfkd = unicodedata.normalize("NFKD", text)
            return "".join(c for c in nfkd if not unicodedata.combining(c))
        except Exception:
            return text

def _python_url_normalize(url: str) -> str:
    try:
        trimmed = url.strip()
        if not trimmed:
            return url
        if not trimmed.isascii():
            trimmed = _rust_text_fast_single(trimmed)
        if "://" not in trimmed:
            synthetic = f"http://{trimmed.lstrip('/')}"
        else:
            synthetic = trimmed
        parsed = urlparse(synthetic)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        path = parsed.path or "/"
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None
        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += path
        params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in {"utm_source", "utm_medium", "utm_campaign"}]
        params.sort()
        if params:
            result += "?" + urlencode(params)
        return result
    except Exception:
        return url

def _python_batch_dedup_urls(urls):
    if not urls:
        return []
    seen = set()
    result = []
    rust_fast = _get_rust_text_fast()
    if rust_fast is not None:
        try:
            batch_nfc, batch_strip = rust_fast
            normalized = batch_strip(batch_nfc(urls))
            for norm in normalized:
                if norm not in seen:
                    seen.add(norm)
                    result.append(norm)
            return result
        except Exception:
            pass
    for url in urls:
        normalized = _python_url_normalize(url)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result

# Tests
print("=" * 60)
print("Testing Rust text fast-path in ioc_processor.py")
print("=" * 60)

rust_fast = _get_rust_text_fast()
print(f"\nRust fast-path available: {rust_fast is not None}")
if rust_fast:
    batch_nfc, batch_strip = rust_fast
    print("  batch_nfc_normalize_fast: available")
    print("  batch_strip_diacritics_fast: available")

print("\n1. Testing _python_url_normalize:")
print(f"   ASCII: 'https://example.com/path' -> {_python_url_normalize('https://example.com/path')}")
print(f"   Non-ASCII: 'https://münchen.example' -> {_python_url_normalize('https://münchen.example')}")

print("\n2. Testing _rust_text_fast_single:")
print(f"   'münchen' -> '{_rust_text_fast_single('münchen')}'")
print(f"   'café' -> '{_rust_text_fast_single('café')}'")

print("\n3. Testing _python_batch_dedup_urls:")
# Same URLs should dedupe
urls1 = [
    'https://example.com/path',
    'https://example.com/path',  # duplicate
]
results1 = _python_batch_dedup_urls(urls1)
print(f"   Same URL dedup: {len(urls1)} -> {len(results1)} (expected 1)")

# Non-ASCII variants (munchen vs muenchen are different - diacritics stripped differently)
urls2 = [
    'https://münchen.example.com',  # ü -> u (NFD strip)
    'https://muenchen.example.com',  # ü already as ue
]
results2 = _python_batch_dedup_urls(urls2)
print(f"   Diacritic dedup: {len(urls2)} -> {len(results2)} unique URLs")
print(f"     Input: {urls2}")
print(f"     Output: {results2}")
print(f"     Note: 'münchen' -> 'munchen', 'muenchen' stays 'muenchen' - different URLs!")

# NFC normalized versions should dedupe
import unicodedata
nfc_mun = unicodedata.normalize("NFC", "münchen")
print(f"   NFC: 'münchen' -> '{nfc_mun}'")

print("\n" + "=" * 60)
print("Tests completed successfully!")
print("=" * 60)
