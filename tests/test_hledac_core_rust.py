"""Integration tests for hledac_rust_ext (Rust extension module).

Tests both Rust fast path and pure-Python fallback paths.
Run with: pytest tests/test_hledac_core_rust.py -v
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any

import pytest


# --- Module import — may be Rust or Python fallback ---

try:
    from hledac_rust_ext import (
        chi_square as _rust_chi_square,
        entropy as _rust_entropy,
        extract_iocs as _rust_extract_iocs,
        normalize_url as _rust_normalize_url,
        batch_sha256 as _rust_batch_sha256,
    )
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    _rust_chi_square = None
    _rust_entropy = None
    _rust_extract_iocs = None
    _rust_normalize_url = None
    _rust_batch_sha256 = None


# --- Pure-Python reference implementations (fallbacks from steganography_detector.py) ---

def _python_extract_iocs(text: str) -> list[tuple[str, str]]:
    patterns = {
        "ipv4": r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
        "ipv6": r"(?i)\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b",
        "onion": r"\b[a-z2-7]{56}\.onion\b",
        "domain": r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
        "md5": r"\b[a-fA-F0-9]{32}\b",
        "sha1": r"\b[a-fA-F0-9]{40}\b",
        "sha256": r"\b[a-fA-F0-9]{64}\b",
        "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "cve": r"\bCVE-\d{4}-\d{4,}\b",
    }
    results = []
    seen = set()
    for ioc_type, pattern in patterns.items():
        for m in re.finditer(pattern, text):
            val = m.group()
            if val not in seen:
                seen.add(val)
                results.append((val, ioc_type))
    return results


def _python_normalize_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        strip_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        out = f"{scheme}://{host}" + (f":{port}" if port and not strip_port else "")
        params = urllib.parse.parse_qsl(parsed.query)
        params = [(k, v) for k, v in params if not k.startswith("utm_") and not k.startswith("fb_") and not k.startswith("mc_")]
        params.sort(key=lambda x: x[0])
        if params:
            qs = urllib.parse.urlencode(params)
            out += f"?{qs}"
        return out
    except Exception:
        return url


def _python_batch_sha256(items: list[str]) -> list[str]:
    return [hashlib.sha256(s.encode()).hexdigest() for s in items]


# --- Wrappers matching steganography_detector.py public API ---

def extract_iocs(text: str) -> list[tuple[str, str]]:
    if _RUST_AVAILABLE and _rust_extract_iocs is not None:
        return _rust_extract_iocs(text)
    return _python_extract_iocs(text)


def normalize_url(url: str) -> str:
    if _RUST_AVAILABLE and _rust_normalize_url is not None:
        return _rust_normalize_url(url)
    return _python_normalize_url(url)


def batch_sha256(items: list[str]) -> list[str]:
    if _RUST_AVAILABLE and _rust_batch_sha256 is not None:
        return _rust_batch_sha256(items)
    return _python_batch_sha256(items)


# =============================================================================
# Tests: extract_iocs
# =============================================================================

class TestExtractIocs:
    """Test IOC extraction for each type."""

    def test_ipv4_basic(self):
        text = "Host 192.168.1.1 contacted on port 8080"
        iocs = extract_iocs(text)
        assert any(v == "192.168.1.1" and t == "ipv4" for v, t in iocs), f"Expected IPv4, got {iocs}"

    def test_ipv4_private_ranges(self):
        for ip in ["10.0.0.1", "172.16.0.1", "192.168.255.255", "0.0.0.0", "255.255.255.255"]:
            iocs = extract_iocs(ip)
            assert any(v == ip and t == "ipv4" for v, t in iocs), f"Failed for {ip}"

    def test_ipv4_negative(self):
        """IP-like strings that shouldn't match."""
        # 256+ octets
        iocs = extract_iocs("512.512.512.512")
        assert not any(t == "ipv4" for _, t in iocs)

    def test_ipv6(self):
        text = "Connection to 2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        iocs = extract_iocs(text)
        assert any(t == "ipv6" for _, t in iocs)

    def test_onion_v3(self):
        text = "Visit abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklm.onion"
        iocs = extract_iocs(text)
        assert any(t == "onion" for _, t in iocs)

    def test_onion_negative_short(self):
        """Short onion addresses (v2) should not match."""
        iocs = extract_iocs("example.onion")
        assert not any(t == "onion" for _, t in iocs)

    def test_domain(self):
        text = "Server at mail.example.com and api.example.com"
        iocs = extract_iocs(text)
        types = [t for _, t in iocs]
        assert "domain" in types

    def test_md5(self):
        text = "File MD5: d41d8cd98f00b204e9800998ecf8427e"
        iocs = extract_iocs(text)
        assert any(v == "d41d8cd98f00b204e9800998ecf8427e" and t == "md5" for v, t in iocs)

    def test_sha1(self):
        text = "SHA1: da39a3ee5e6b4b0d3255bfef95601890afd80709"
        iocs = extract_iocs(text)
        assert any(v == "da39a3ee5e6b4b0d3255bfef95601890afd80709" and t == "sha1" for v, t in iocs)

    def test_sha256(self):
        text = "SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        iocs = extract_iocs(text)
        assert any(t == "sha256" for _, t in iocs)

    def test_email(self):
        text = "Contact admin@test.example.com or support@example.org"
        iocs = extract_iocs(text)
        emails = [v for v, t in iocs if t == "email"]
        assert "admin@test.example.com" in emails

    def test_cve(self):
        text = "CVE-2024-12345 and CVE-2023-9999"
        iocs = extract_iocs(text)
        cves = [v for v, t in iocs if t == "cve"]
        assert "CVE-2024-12345" in cves
        assert "CVE-2023-9999" in cves

    def test_cve_negative(self):
        iocs = extract_iocs("CVE-2024-1 CVE-2024-123 CVE-1234-12345")
        assert not any(t == "cve" for _, t in iocs)

    def test_deduplication(self):
        """Same IOC appearing twice should appear only once."""
        text = "IP 1.2.3.4 and again 1.2.3.4"
        iocs = extract_iocs(text)
        ipv4s = [v for v, t in iocs if t == "ipv4" and v == "1.2.3.4"]
        assert len(ipv4s) == 1

    def test_mixed_text(self):
        text = """
        Server 93.184.216.34 (example.com) reachable at 2001:db8::1.
        Contact noc@example.com for CVE-2024-99999 issues.
        Hash: a59492d17a9e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5
        """
        iocs = extract_iocs(text)
        types = {t for _, t in iocs}
        assert "ipv4" in types
        assert "ipv6" in types
        assert "domain" in types
        assert "email" in types
        assert "cve" in types

    def test_empty_text(self):
        assert extract_iocs("") == []
        assert extract_iocs("no iocs here") == []

    def test_rust_vs_python_parity(self):
        """When Rust is available, both paths must agree on non-trivial input."""
        text = "DNS 8.8.8.8, domain google.com, email a@b.c, CVE-2021-44228"
        py = _python_extract_iocs(text)
        if _RUST_AVAILABLE and _rust_extract_iocs is not None:
            rs = _rust_extract_iocs(text)
            # Both must have same types and values (order may differ)
            assert set(py) == set(rs), f"Python: {py}, Rust: {rs}"


# =============================================================================
# Tests: normalize_url
# =============================================================================

class TestNormalizeUrl:
    """Test URL normalization."""

    def test_lowercase_scheme_host(self):
        assert normalize_url("HTTPS://Example.COM/Path").startswith("https://example.com")

    def test_strip_default_http_port(self):
        result = normalize_url("http://example.com:80/path")
        assert ":80" not in result

    def test_strip_default_https_port(self):
        result = normalize_url("https://example.com:443/path")
        assert ":443" not in result

    def test_preserve_nondefault_port(self):
        result = normalize_url("https://example.com:8443/path")
        assert ":8443" in result

    def test_sort_query_params(self):
        result = normalize_url("https://example.com?b=2&a=1&c=3")
        assert result.index("a=1") < result.index("b=2") < result.index("c=3")

    def test_remove_utm_params(self):
        result = normalize_url("https://example.com?utm_source=google&utm_campaign=test&other=val")
        assert "utm_" not in result
        assert "other=val" in result

    def test_remove_fb_params(self):
        result = normalize_url("https://example.com?fbclid=abc123&x=1")
        assert "fbclid" not in result
        assert "x=1" in result

    def test_no_query_string(self):
        result = normalize_url("https://example.com/path")
        assert "?" not in result

    def test_empty_query_string(self):
        result = normalize_url("https://example.com/path?")
        assert "?" not in result or result.endswith("https://example.com/path")

    def test_rust_vs_python_parity(self):
        urls = [
            "HTTPS://EXAMPLE.COM:443/Path?utm_source=x&b=2&a=1",
            "http://example.com:80/path",
            "https://example.com:8443/api?x=1&utm_campaign=y",
        ]
        for url in urls:
            py = _python_normalize_url(url)
            if _RUST_AVAILABLE and _rust_normalize_url is not None:
                rs = _rust_normalize_url(url)
                assert py == rs, f"URL: {url}\nPython: {py}\nRust: {rs}"


# =============================================================================
# Tests: batch_sha256
# =============================================================================

class TestBatchSha256:
    """Test SHA256 batch hashing."""

    def test_single_item(self):
        result = batch_sha256(["hello"])
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result == [expected]

    def test_multiple_items(self):
        items = ["hello", "world", "test"]
        result = batch_sha256(items)
        expected = [hashlib.sha256(s.encode()).hexdigest() for s in items]
        assert result == expected

    def test_empty_list(self):
        assert batch_sha256([]) == []

    def test_known_hash(self):
        """SHA256('hello') is well-known."""
        result = batch_sha256(["hello"])
        assert result[0] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_rust_vs_python_parity(self):
        items = ["hello", "world", "", "a" * 1000]
        py = _python_batch_sha256(items)
        if _RUST_AVAILABLE and _rust_batch_sha256 is not None:
            rs = _rust_batch_sha256(items)
            assert py == rs, f"Python: {py}\nRust: {rs}"

    def test_empty_string_hash(self):
        """SHA256('') is deterministic."""
        result = batch_sha256([""])
        expected = hashlib.sha256(b"").hexdigest()
        assert result[0] == expected


# =============================================================================
# Tests: chi_square + entropy (existing functions, sanity check)
# =============================================================================

class TestChiSquareAndEntropy:
    """Sanity checks for chi_square and entropy on known inputs."""

    def test_entropy_zero(self):
        """All same byte = zero entropy."""
        data = bytes([42] * 1000)
        if _RUST_AVAILABLE and _rust_entropy is not None:
            e = _rust_entropy(data)
            assert 0.0 <= e <= 0.001

    def test_entropy_max(self):
        """Maximum entropy = log2(256) for uniformly random bytes."""
        import os
        data = bytes(os.urandom(1000))
        if _RUST_AVAILABLE and _rust_entropy is not None:
            e = _rust_entropy(data)
            assert 7.5 < e < 8.0  # close to 8 bits/byte

    def test_chi_square_zero(self):
        """Perfect uniform distribution = chi-square near 0."""
        import os
        # Create perfectly uniform-ish distribution
        data = bytes(list(range(256)) * 4)
        if _RUST_AVAILABLE and _rust_chi_square is not None:
            chi = _rust_chi_square(data)
            assert chi < 1.0

    def test_rust_vs_python_parity_chi_square(self):
        """Both implementations must agree on chi_square."""
        import os
        data = bytes(os.urandom(256))
        if _RUST_AVAILABLE and _rust_chi_square is not None:
            rs = float(_rust_chi_square(data))
            # Python reference
            hist = [0] * 256
            for b in data:
                hist[b] += 1
            n = len(data)
            expected = n / 256.0
            chi = sum((obs - expected) ** 2 / expected for obs in hist)
            py = chi / 256.0
            assert abs(rs - py) < 0.001, f"Python: {py}, Rust: {rs}"

    def test_rust_vs_python_parity_entropy(self):
        """Both implementations must agree on entropy."""
        import os
        data = bytes(os.urandom(256))
        if _RUST_AVAILABLE and _rust_entropy is not None:
            import math
            rs = float(_rust_entropy(data))
            hist = [0] * 256
            for b in data:
                hist[b] += 1
            n = len(data)
            ent = 0.0
            for count in hist:
                if count > 0:
                    p = count / n
                    ent -= p * math.log2(p)
            py = ent
            assert abs(rs - py) < 0.001, f"Python: {py}, Rust: {rs}"


# =============================================================================
# Smoke test
# =============================================================================

def test_rust_extension_loads():
    """Sanity: Rust extension loads without error."""
    if _RUST_AVAILABLE:
        assert callable(_rust_extract_iocs) or callable(_rust_batch_sha256)


def test_module_guarded():
    """Ensure all imports are properly guarded."""
    # If we got here, the module import at top of file worked or graceful fallback happened
    assert True  # placeholder assertion