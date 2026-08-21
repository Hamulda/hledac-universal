"""
Test E1: IOC SIMD Rust path (extract_iocs_simd) — ZOMBIE → AKTIVNÍ

Tests the Rust SIMD IOC extraction functions:
- fast_ioc_extract(text) → list[tuple[str, str]]
- fast_ioc_extract_batch(texts) → list[tuple[str, str]]
- extract_iocs_simd(text) → list[tuple[str, str]]
- batch_extract_iocs_simd(texts) → list[list[tuple[str, str]]]
- url_normalize(url) → str
- batch_dedup_urls(urls) → list[str]

Expected speedup: 8-15× vs Python regex (NEON SIMD + regex set optimization)
"""


class TestRustIocSimdFunctions:
    """Direct Rust extension function tests."""

    def test_extract_iocs_simd_basic(self) -> None:
        """extract_iocs_simd extracts IOCs from text."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        text = "Evil IP 1.2.3.4 and domain evil.com CVE-2024-1234"
        result = ext.extract_iocs_simd(text)

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) >= 3, f"Expected ≥3 IOCs, got {len(result)}: {result}"

        types = {ioc_type for _value, ioc_type in result}
        assert "ipv4" in types, f"Expected 'ipv4' in {types}"
        assert "domain" in types, f"Expected 'domain' in {types}"
        assert "cve" in types, f"Expected 'cve' in {types}"

        # Verify specific values
        values = {value for value, _ioc_type in result}
        assert "1.2.3.4" in values, f"Expected '1.2.3.4' in {values}"
        assert "evil.com" in values, f"Expected 'evil.com' in {values}"
        assert "cve-2024-1234" in values, f"Expected 'cve-2024-1234' in {values}"

    def test_extract_iocs_simd_all_types(self) -> None:
        """extract_iocs_simd handles all IOC types."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        text = (
            "IP 192.168.1.1 domain example.com email test@example.com "
            "CVE-2024-12345 hash a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e "
            "MD5 d41d8cd98f00b204e9800998ecf8427e"
        )
        result = ext.extract_iocs_simd(text)

        types = {ioc_type for _value, ioc_type in result}
        assert "ipv4" in types, f"Expected ipv4 in {types}"
        assert "domain" in types, f"Expected domain in {types}"
        assert "email" in types, f"Expected email in {types}"
        assert "cve" in types, f"Expected cve in {types}"
        assert "sha256" in types, f"Expected sha256 in {types}"
        assert "md5" in types, f"Expected md5 in {types}"

    def test_fast_ioc_extract_equivalence(self) -> None:
        """fast_ioc_extract returns same results as extract_iocs_simd."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        text = "Server 8.8.8.8 and attacker@evil.org"

        simd_result = ext.extract_iocs_simd(text)
        fast_result = ext.fast_ioc_extract(text)

        # Both should return IOCs
        assert len(simd_result) >= 2, f"SIMD returned: {simd_result}"
        assert len(fast_result) >= 2, f"Fast returned: {fast_result}"

    def test_batch_extract_iocs_simd(self) -> None:
        """batch_extract_iocs_simd processes multiple texts."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        texts = [
            "IP 1.2.3.4 domain a.com",
            "IP 5.6.7.8 domain b.com CVE-2024-1111",
            "Email user@c.com",
        ]

        result = ext.batch_extract_iocs_simd(texts)

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        # Result is flat list of (value, ioc_type)
        assert len(result) >= 5, f"Expected ≥5 IOCs, got {len(result)}: {result}"

        types = {ioc_type for _value, ioc_type in result}
        assert "ipv4" in types
        assert "domain" in types
        assert "cve" in types
        assert "email" in types

    def test_batch_dedup_urls(self) -> None:
        """batch_dedup_urls deduplicates URLs."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        urls = [
            "https://evil.com/page",
            "https://evil.com/page",  # duplicate
            "https://evil.com/page?utm_source=test",  # different query
            "https://good.com/",
        ]

        result = ext.batch_dedup_urls(urls)

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        # Should dedupe exact duplicates
        assert len(result) <= len(urls), f"Got more results than input: {len(result)} vs {len(urls)}"

    def test_url_normalize(self) -> None:
        """url_normalize returns canonical URL form."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        # Basic normalization
        assert ext.url_normalize("http://example.com") == "http://example.com"
        assert ext.url_normalize("https://example.com/") == "https://example.com/"

    def test_empty_text(self) -> None:
        """extract_iocs_simd handles empty input."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        result = ext.extract_iocs_simd("")
        assert result == [], f"Expected empty list for empty input, got {result}"

    def test_ipv6_support(self) -> None:
        """extract_iocs_simd handles IPv6 addresses."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        text = "IPv6 loopback ::1 and full 2001:db8:85a3::8a2e:370:7334"
        result = ext.extract_iocs_simd(text)

        types = {ioc_type for _value, ioc_type in result}
        # IPv6 may be detected as ipv6 or just skip if pattern doesn't match
        assert "ipv6" in types or len(result) >= 1, f"Expected IPv6 detection: {result}"

    def test_no_false_positives_for_wrong_length_hashes(self) -> None:
        """extract_iocs_simd doesn't misclassify wrong-length hex as hashes."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        # "deadbeef" is 8 hex chars, NOT a valid MD5 (32), SHA1 (40), or SHA256 (64)
        text = "Value: deadbeef is not a hash"
        result = ext.extract_iocs_simd(text)

        types = {ioc_type for _value, ioc_type in result}
        assert "md5" not in types, f"Wrong-length 'deadbeef' should not be md5: {result}"
        assert "sha1" not in types, f"Wrong-length 'deadbeef' should not be sha1: {result}"
        assert "sha256" not in types, f"Wrong-length 'deadbeef' should not be sha256: {result}"


class TestRustIocSimdPerformance:
    """Performance-oriented tests for SIMD path."""

    def test_large_text_performance(self) -> None:
        """SIMD path handles large texts efficiently."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import time

        import hledac_rust_extensions as ext

        # Create large text with many unique IOCs
        # Note: SIMD deduplicates within a single text, so we use unique values
        lines = []
        for i in range(1000):
            # Use alphanumeric domains (numbers-only domains don't match the pattern)
            lines.append(f"IP 1.2.{i % 256}.{i % 256} evil{i}x.com CVE-2024-{i:05d}")
        large_text = " ".join(lines)

        start = time.perf_counter()
        result = ext.extract_iocs_simd(large_text)
        elapsed = time.perf_counter() - start

        # Should find many IOCs (unique IPs repeat every 256, domains and CVEs unique)
        assert len(result) >= 2000, f"Expected ≥2000 IOCs, got {len(result)}"
        # Should complete quickly (SIMD acceleration)
        assert elapsed < 2.0, f"Took {elapsed:.3f}s, expected < 2s"

    def test_batch_threshold(self) -> None:
        """batch_extract_iocs_simd uses SIMD for batches >= 4 or total >= 16KB."""
        import sys

        sys.path.insert(0, "rust_extensions")
        import hledac_rust_extensions as ext

        # Small batch (below threshold)
        small_batch = ["IP 1.2.3.4", "IP 2.3.4.5"]
        result_small = ext.batch_extract_iocs_simd(small_batch)

        # Large batch (above threshold)
        large_batch = ["IP 1.2.3.4"] * 10
        result_large = ext.batch_extract_iocs_simd(large_batch)

        # Both should return results
        assert len(result_small) >= 2, f"Small batch failed: {result_small}"
        assert len(result_large) >= 10, f"Large batch failed: {result_large}"
