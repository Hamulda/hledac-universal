"""Test feed source expansion (Sprint 8UF B.3)."""
import pytest
from hledac.universal.discovery.rss_atom_adapter import get_default_feed_seeds


class TestFeedSources:
    """Feed source expansion tests."""

    def test_cisa_kev_feed_present(self):
        """CISA KEV JSON feed is included."""
        seeds = get_default_feed_seeds()
        urls = [s.feed_url for s in seeds]
        assert any("cisa.gov" in u and "known_exploited" in u for u in urls), \
            f"CISA KEV not found in {urls}"

    def test_nvd_cve_feed_present(self):
        """NVD CVE JSON feed is included."""
        seeds = get_default_feed_seeds()
        urls = [s.feed_url for s in seeds]
        assert any("nvd.nist.gov" in u and "json" in u for u in urls), \
            f"NVD CVE JSON not found in {urls}"

    def test_bleepingcomputer_feed_present(self):
        """BleepingComputer RSS feed is included."""
        seeds = get_default_feed_seeds()
        urls = [s.feed_url for s in seeds]
        assert any("bleepingcomputer.com" in u for u in urls), \
            f"BleepingComputer not found in {urls}"

    def test_urlhaus_feed_present(self):
        """URLhaus feed is included."""
        seeds = get_default_feed_seeds()
        urls = [s.feed_url for s in seeds]
        assert any("urlhaus" in u or "abuse.ch" in u for u in urls), \
            f"URLhaus not found in {urls}"

    def test_hackernews_feed_present(self):
        """The Hacker News feed is included."""
        seeds = get_default_feed_seeds()
        urls = [s.feed_url for s in seeds]
        assert any("feeds.feedburner.com/TheHackersNews" in u for u in urls), \
            f"The Hacker News not found in {urls}"

    def test_minimum_7_feeds(self):
        """At least 7 curated seed feeds should be present."""
        seeds = get_default_feed_seeds()
        assert len(seeds) >= 7, f"Expected at least 7 feeds, got {len(seeds)}"
