"""
Probe tests for Issue E2: Feed Pipeline Rust Integration.

Tests the feed_entry_pipeline Rust function for:
1. RSS 2.0 XML parsing
2. Atom 1.0 XML parsing  
3. Pattern scanning via Aho-Corasick
4. Batch processing via rayon
5. Graceful degradation when Rust unavailable
"""
from __future__ import annotations

import pytest
from _core import aclose


class TestFeedPipelineImports:
    """Test import availability."""

    def test_rust_module_import(self):
        """Rust feed_pipeline module should be importable."""
        try:
            from hledac_rust_extensions import feed_entry_pipeline, feed_batch_pipeline
            assert callable(feed_entry_pipeline)
            assert callable(feed_batch_pipeline)
        except ImportError as e:
            pytest.skip(f"Rust feed_pipeline not available: {e}")

    def test_wrapper_import(self):
        """Python wrapper should import without errors."""
        from hledac.universal.utils.patterns.feed_pipeline_wrapper import (
            feed_entry_pipeline_fast,
            feed_batch_pipeline_fast,
            is_feed_pipeline_available,
    )
        assert callable(feed_entry_pipeline_fast)
        assert callable(feed_batch_pipeline_fast)
        assert callable(is_feed_pipeline_available)


class TestRSSParsing:
    """Test RSS 2.0 XML parsing."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>Threat Intelligence Feed</title>
<link>https://example.com/feed</link>
<description>OSINT threat feed</description>
<item>
<title>APT29 Campaign Targets Government</title>
<link>https://example.com/apt29</link>
<description>Advanced persistent threat group APT29 has been observed targeting government agencies with sophisticated malware.</description>
<guid>urn:entry:001</guid>
<pubDate>Sat, 12 Jul 2025 10:00:00 GMT</pubDate>
<dc:creator>CTI Team</dc:creator>
</item>
<item>
<title>Phishing Kit Distribution Network</title>
<link>https://example.com/phishing</link>
<description>New phishing campaign uses malicious domains to steal credentials.</description>
<guid>urn:entry:002</guid>
<pubDate>Sat, 12 Jul 2025 09:00:00 GMT</pubDate>
</item>
<item>
<title>Ransomware Attack on Healthcare</title>
<link>https://example.com/ransomware</link>
<description>Healthcare sector targeted by LockBit ransomware operators.</description>
<guid>urn:entry:003</guid>
</item>
</channel>
</rss>"""

    def test_parse_rss_entries(self, rust_pipeline):
        """Should parse all RSS entries correctly."""
        patterns = ["apt", "phishing", "ransomware", "malware"]
        labels = ["apt", "phishing", "ransomware", "threat"]
        
        results = rust_pipeline(self.RSS_SAMPLE, max_entries=0, patterns=patterns, labels=labels)
        
        assert len(results) == 3, f"Expected 3 entries, got {len(results)}"
        
        # Check first entry has hits for "apt" pattern
        entry0 = results[0]
        assert entry0[0] == 0  # entry_idx
        assert "apt29" in entry0[1].lower()  # entry_url
        
        # Check hits contain pattern matches
        combined_hits = entry0[2]
        assert len(combined_hits) > 0, "Expected pattern hits"
        hit_patterns = [h[2] for h in combined_hits]
        assert "apt" in hit_patterns

    def test_parse_rss_max_entries(self, rust_pipeline):
        """Should respect max_entries limit."""
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(self.RSS_SAMPLE, max_entries=2, patterns=patterns, labels=labels)
        assert len(results) <= 2

    def test_parse_rss_namespace_elements(self, rust_pipeline):
        """Should handle XML namespace elements like dc:creator."""
        patterns = ["cti"]
        labels = ["source"]
        
        results = rust_pipeline(self.RSS_SAMPLE, max_entries=1, patterns=patterns, labels=labels)
        assert len(results) >= 1


class TestAtomParsing:
    """Test Atom 1.0 XML parsing."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Security Alerts</title>
<link href="https://example.com/atom"/>
<entry>
<title>CVE-2025-1234 Critical Vulnerability</title>
<link href="https://example.com/cve-2025-1234"/>
<summary>Critical buffer overflow in OpenSSL allows remote code execution.</summary>
<id>urn:atom:001</id>
<updated>2025-07-12T10:00:00Z</updated>
</entry>
<entry>
<title>Zero-Day in Apache HTTP Server</title>
<link href="https://example.com/apache-0day"/>
<content>Security researchers discovered a critical zero-day vulnerability affecting Apache HTTP Server versions 2.4.x.</content>
<id>urn:atom:002</id>
<updated>2025-07-11T15:30:00Z</updated>
</entry>
</feed>"""

    def test_parse_atom_entries(self, rust_pipeline):
        """Should parse Atom entries with summary and content elements."""
        patterns = ["vulnerability", "cve", "zero-day", "openssl", "apache"]
        labels = ["vuln", "cve", "vuln", "software", "software"]
        
        results = rust_pipeline(self.ATOM_SAMPLE, max_entries=0, patterns=patterns, labels=labels)
        
        assert len(results) == 2, f"Expected 2 entries, got {len(results)}"
        
        # Check both entries parsed
        urls = [r[1] for r in results]
        assert any("cve" in url.lower() for url in urls)
        assert any("apache" in url.lower() for url in urls)

    def test_parse_atom_link_href(self, rust_pipeline):
        """Should extract link href from Atom link elements."""
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(self.ATOM_SAMPLE, max_entries=1, patterns=patterns, labels=labels)
        assert len(results) >= 1
        
        # Entry URL should be extracted from link href
        entry_url = results[0][1]
        assert entry_url.startswith("https://")


class TestPatternScanning:
    """Test Aho-Corasick pattern scanning."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    def test_pattern_case_insensitive(self, rust_pipeline):
        """Should match patterns case-insensitively."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Test MALWARE Detection</title><link>https://ex.com/1</link><description>malware detected</description><guid>g1</guid></item>
</channel></rss>"""
        
        patterns = ["malware", "APT", "PHISHING"]
        labels = ["malware", "apt", "phishing"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        # Should find "malware" in both title and description
        assert len(results) == 1
        combined_hits = results[0][2]
        hit_patterns = [h[2] for h in combined_hits]
        assert "malware" in hit_patterns

    def test_multiple_patterns_same_entry(self, rust_pipeline):
        """Should detect multiple patterns in single entry."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>APT29 Phishing with Ransomware</title><link>https://ex.com/1</link><description>Advanced threat group using phishing to deliver ransomware</description><guid>g1</guid></item>
</channel></rss>"""
        
        patterns = ["apt29", "phishing", "ransomware", "malware"]
        labels = ["apt", "phishing", "ransomware", "malware"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        assert len(results) == 1
        combined_hits = results[0][2]
        hit_patterns = [h[2] for h in combined_hits]
        
        # Should match multiple patterns
        assert "apt29" in hit_patterns
        assert "phishing" in hit_patterns
        assert "ransomware" in hit_patterns
        assert "malware" not in hit_patterns  # not present

    def test_empty_patterns(self, rust_pipeline):
        """Should handle empty pattern list."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Test</title><link>https://ex.com/1</link><description>Content</description><guid>g1</guid></item>
</channel></rss>"""
        
        results = rust_pipeline(xml, max_entries=0, patterns=[], labels=[])
        
        # Should still return entries, just with no hits
        assert len(results) == 1
        assert results[0][2] == []  # no hits


class TestDeduplication:
    """Test GUID-based deduplication."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    def test_duplicate_guids(self, rust_pipeline):
        """Should deduplicate entries with same GUID."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>First</title><link>https://ex.com/1</link><description>Content 1</description><guid>same-guid</guid></item>
<item><title>Second</title><link>https://ex.com/2</link><description>Content 2</description><guid>same-guid</guid></item>
<item><title>Third</title><link>https://ex.com/3</link><description>Content 3</description><guid>unique-guid-3</guid></item>
</channel></rss>"""
        
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        # Should have at most 2 unique entries
        assert len(results) <= 2

    def test_guid_case_sensitivity(self, rust_pipeline):
        """GUID comparison should be case-insensitive."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>First</title><link>https://ex.com/1</link><description>Content</description><guid>Test-GUID-123</guid></item>
<item><title>Second</title><link>https://ex.com/2</link><description>Content</description><guid>test-guid-123</guid></item>
</channel></rss>"""
        
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        # Both have same GUID (case-insensitive), should dedupe to 1
        assert len(results) == 1


class TestBatchProcessing:
    """Test batch processing via feed_batch_pipeline."""

    @pytest.fixture
    def rust_batch(self):
        try:
            from hledac_rust_extensions import feed_batch_pipeline
            return feed_batch_pipeline
        except ImportError:
            pytest.skip("Rust feed_batch_pipeline not available")

    def test_batch_multiple_feeds(self, rust_batch):
        """Should process multiple feeds in parallel."""
        feeds = [
            ("<?xml version='1.0'?><rss version='2.0'><channel><item><title>Feed1</title><link>https://f1.com</link><description>malware here</description><guid>f1-1</guid></item></channel></rss>", 0),
            ("<?xml version='1.0'?><rss version='2.0'><channel><item><title>Feed2</title><link>https://f2.com</link><description>phishing here</description><guid>f2-1</guid></item></channel></rss>", 0),
        ]
        
        patterns = ["malware", "phishing"]
        labels = ["malware", "phishing"]
        
        results = rust_batch(feeds, patterns, labels)
        
        assert len(results) == 2
        
        # First feed should have malware hit
        assert len(results[0]) == 1
        hit_patterns = [h[2] for h in results[0][0][2]]
        assert "malware" in hit_patterns
        
        # Second feed should have phishing hit
        assert len(results[1]) == 1
        hit_patterns = [h[2] for h in results[1][0][2]]
        assert "phishing" in hit_patterns

    def test_batch_empty_feeds(self, rust_batch):
        """Should handle empty feed list."""
        results = rust_batch([], ["test"], ["label"])
        assert results == []

    def test_batch_invalid_xml(self, rust_batch):
        """Should handle invalid XML gracefully."""
        feeds = [
            ("not xml at all", 0),
            ("<?xml version='1.0'?><rss version='2.0'><channel><item><title>Valid</title><link>https://v.com</link><description>content</description><guid>v1</guid></item></channel></rss>", 0),
        ]
        
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_batch(feeds, patterns, labels)
        
        assert len(results) == 2
        # First should be empty (invalid XML)
        assert results[0] == []
        # Second should have entry
        assert len(results[1]) == 1


class TestWrapperFallback:
    """Test Python wrapper fallback when Rust unavailable."""

    def test_wrapper_fallback(self):
        """Wrapper should return empty list when Rust unavailable."""
        from hledac.universal.utils.patterns.feed_pipeline_wrapper import (
            feed_entry_pipeline_fast,
            feed_batch_pipeline_fast,
    )
        
        # Even if Rust unavailable, should not raise
        result = feed_entry_pipeline_fast("<xml/>", patterns=[], labels=[])
        assert isinstance(result, list)
        
        batch_result = feed_batch_pipeline_fast([("<xml/>", 0)], [], [])
        assert isinstance(batch_result, list)

    def test_wrapper_availability_check(self):
        """Should correctly report availability."""
        from hledac.universal.utils.patterns.feed_pipeline_wrapper import (
            is_feed_pipeline_available,
    )
        
        # Just check it runs without error
        result = is_feed_pipeline_available()
        assert isinstance(result, bool)


class TestAssemblyPhase:
    """Test assembly phase detection."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    def test_title_only_phase(self, rust_pipeline):
        """Should detect title-only assembly phase."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>No Description Here</title><link>https://ex.com/1</link><guid>g1</guid></item>
</channel></rss>"""
        
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        assert len(results) == 1
        assert results[0][5] == "title_only"

    def test_title_description_phase(self, rust_pipeline):
        """Should detect title+description assembly phase."""
        xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>With Description</title><link>https://ex.com/1</link><description>Some description content</description><guid>g1</guid></item>
</channel></rss>"""
        
        patterns = ["test"]
        labels = ["keyword"]
        
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        
        assert len(results) == 1
        assert results[0][5] == "title_description"


class TestPerformance:
    """Performance-related tests."""

    @pytest.fixture
    def rust_pipeline(self):
        try:
            from hledac_rust_extensions import feed_entry_pipeline
            return feed_entry_pipeline
        except ImportError:
            pytest.skip("Rust feed_pipeline not available")

    def test_large_feed_parsing(self, rust_pipeline):
        """Should handle large feeds efficiently."""
        # Generate a feed with many entries
        entries = ""
        for i in range(100):
            entries += f"""<item>
<title>Entry {i}: APT Group Activity Detected</title>
<link>https://ex.com/{i}</link>
<description>Security researchers observed suspicious activity related to APT groups targeting critical infrastructure.</description>
<guid>entry-{i}</guid>
</item>"""
        
        xml = f"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Large Feed Test</title>
{entries}
</channel></rss>"""
        
        patterns = ["apt", "malware", "phishing", "ransomware", "infrastructure", "suspicious"]
        labels = ["apt", "malware", "phishing", "ransomware", "target", "indicator"]
        
        import time
        start = time.perf_counter()
        results = rust_pipeline(xml, max_entries=0, patterns=patterns, labels=labels)
        elapsed = time.perf_counter() - start
        
        assert len(results) == 100
        # Should complete in reasonable time (< 100ms for 100 entries)
        assert elapsed < 0.1, f"Parsing took {elapsed:.3f}s, expected < 0.1s"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-q"])
