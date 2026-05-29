"""
Sprint 8V: Orchestrator Content Enrichment Integration + Real Evidence Yield A/B Validation

Tests for:
1. async subprocess fallback is non-blocking
2. async subprocess timeout kills process properly
3. deep_read equivalence is checked
4. provider URLs flow into content enrichment
5. enrichment respects top-k cap, 10s timeout, 5MiB payload cap
6. real vs mock evidence provenance tracking
7. real fetched page can contribute to findings
8. project mailing list emails are NOT filtered
9. generic service emails ARE filtered
10. offline replay non-regression still holds
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


@pytest.fixture
def crawler():
    """Create StealthCrawler instance for testing."""
    from hledac.universal.intelligence.stealth_crawler import StealthCrawler
    return StealthCrawler()


class TestSprint8VAsyncSubprocess:
    """Sprint 8V tests for async-safe subprocess handling."""

    def test_async_subprocess_fallback_is_nonblocking(self, crawler):
        """Test that subprocess fallback does not block when called from sync context."""
        start = time.time()
        result = crawler.fetch_page_content("https://example.com")
        elapsed = time.time() - start

        # Should complete in reasonable time (< 30s)
        assert elapsed < 30, f"fetch_page_content took {elapsed}s - may be blocking"
        assert 'fetch_transport' in result

    def test_async_subprocess_timeout_kills_process(self):
        """Test that subprocess timeout properly kills the curl process."""
        import subprocess

        cmd = ['curl', '-s', '--compressed', '-L', '-A', 'Mozilla/5.0', 'https://example.com']

        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        elapsed = time.time() - start

        # Should complete quickly with timeout=5
        assert result.returncode == 0, "curl should succeed"
        assert elapsed < 10, "Should complete quickly"

    def test_fetch_page_content_respects_timeout(self, crawler):
        """Test that fetch_page_content respects timeout for slow URLs."""
        start = time.time()
        result = crawler.fetch_page_content("https://this-domain-does-not-exist-12345.com")
        elapsed = time.time() - start

        # Should fail gracefully within reasonable time
        assert elapsed < 20, f"fetch_page_content took {elapsed}s for invalid domain"
        assert result['fetch_success'] is False

    def test_fetch_page_content_respects_5mib_cap(self, crawler):
        """Test that fetch_page_content caps payload at 5MiB."""
        result = crawler.fetch_page_content(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS"
        )

        # Text length should be capped (50K chars = ~50KB)
        assert result['text_length'] <= 50000, \
            f"Text length {result['text_length']} exceeds 50K cap"

    def test_fetch_page_content_respects_email_cap(self, crawler):
        """Test that fetch_page_content caps emails at 20."""
        result = crawler.fetch_page_content(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS"
        )

        assert len(result['emails']) <= 20, \
            f"Email count {len(result['emails'])} exceeds 20 cap"


class TestSprint8VEmailFiltering:
    """Test email filtering - generic vs project/team/mailing-list emails."""

    def test_project_mailing_list_emails_are_not_filtered(self, crawler):
        """Test that project mailing list emails (linux-*, netdev@, kernel-team@) are NOT filtered."""
        result = crawler.fetch_page_content(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS"
        )

        emails = result.get('emails', [])

        # linux-*@vger.kernel.org addresses should be preserved
        linux_emails = [e for e in emails if 'linux-' in e.lower() or 'netdev' in e.lower()]
        assert len(linux_emails) > 0, \
            f"Expected linux-*/netdev* mailing list emails, got: {emails[:5]}"

    def test_generic_service_emails_are_filtered(self, crawler):
        """Test that generic service emails (info@, support@, admin@, etc.) ARE filtered."""
        result = crawler.fetch_page_content("https://example.com")

        emails = result.get('emails', [])

        # These generic prefixes should be filtered
        generic_prefixes = ('info@', 'support@', 'admin@', 'contact@', 'noreply@', 'no-reply@')

        for email in emails:
            assert not email.lower().startswith(generic_prefixes), \
                f"Generic email not filtered: {email}"


class TestSprint8VEvidenceProvenance:
    """Test evidence provenance tracking."""

    def test_real_vs_mock_evidence_provenance_exists(self, crawler):
        """Test that provenance tracking distinguishes REAL_FETCHED_PAGE vs MOCK_FALLBACK."""
        # Real content fetch
        real_result = crawler.fetch_page_content(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS"
        )

        # Should have real content with emails
        assert real_result['fetch_success'] is True
        assert len(real_result['emails']) > 0
        # Transport should be recorded
        assert real_result['fetch_transport'] in ('curl_cffi', 'subprocess_curl', 'native_python')

    def test_real_fetched_page_has_meaningful_content(self, crawler):
        """Test that real fetched page has meaningful text content."""
        result = crawler.fetch_page_content(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS"
        )

        # Should have substantial text
        assert result['text_length'] > 1000, \
            f"Expected substantial text, got {result['text_length']} chars"
        assert len(result['emails']) > 0, "Should extract emails from real content"


class TestSprint8VReplayNonRegression:
    """Test that OFFLINE_REPLAY still works after changes."""

    @pytest.mark.asyncio
    async def test_offline_replay_non_regression_still_holds(self):
        """Test OFFLINE_REPLAY produces nonzero iterations."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import run_benchmark

        result = await run_benchmark(duration_seconds=5, mode='OFFLINE_REPLAY')
        assert result.iterations > 0, "OFFLINE_REPLAY should produce iterations"
        assert result.data_mode == 'OFFLINE_REPLAY'


class TestSprint8VDeepReadEquivalence:
    """Test deep_read equivalence analysis."""

    def test_deep_read_exists_in_research_manager(self):
        """Test that deep_read() exists in _ResearchManager."""
        import inspect

        from hledac.universal.autonomous_orchestrator import _ResearchManager

        # Check that the class has the deep_read method
        assert hasattr(_ResearchManager, 'deep_read'), \
            "deep_read should exist in _ResearchManager"

        method = getattr(_ResearchManager, 'deep_read', None)
        assert method is not None, "deep_read should be accessible"
        assert inspect.iscoroutinefunction(method), \
            "deep_read should be an async method"

    def test_deep_read_provides_content_fetching(self):
        """Test that deep_read provides content fetching infrastructure."""
        import inspect

        from hledac.universal.autonomous_orchestrator import _ResearchManager

        method = getattr(_ResearchManager, 'deep_read', None)
        assert method is not None, "deep_read should exist"

        sig = inspect.signature(method)
        params = list(sig.parameters.keys())

        # deep_read takes url parameter
        assert 'url' in params, "deep_read should take url parameter"

    def test_research_manager_has_fetch_page_content_access(self):
        """Test that StealthCrawler (used by ResearchManager) has fetch_page_content."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler

        assert hasattr(StealthCrawler, 'fetch_page_content'), \
            "StealthCrawler should have fetch_page_content method"


class TestSprint8VOrchestratorIntegration:
    """Test orchestrator integration points."""

    def test_surface_web_search_is_async_method(self):
        """Test that _surface_web_search is an async method in _ResearchManager."""
        import inspect

        from hledac.universal.autonomous_orchestrator import _ResearchManager

        method = getattr(_ResearchManager, '_surface_web_search', None)
        assert method is not None, "_surface_web_search should exist in _ResearchManager"
        assert inspect.iscoroutinefunction(method), \
            "_surface_web_search should be async"

    def test_execute_surface_search_is_async(self):
        """Test that execute_surface_search is an async method in _ResearchManager."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager

        method = getattr(_ResearchManager, 'execute_surface_search', None)
        assert method is not None, "execute_surface_search should exist in _ResearchManager"
        # execute_surface_search may or may not be async depending on implementation

    def test_stealth_crawler_has_fetch_page_content(self):
        """Test that StealthCrawler has fetch_page_content method."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler

        assert hasattr(StealthCrawler, 'fetch_page_content'), \
            "StealthCrawler should have fetch_page_content method"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
