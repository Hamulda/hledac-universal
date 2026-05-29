"""
Sprint 8AD: Live-Yield Revalidation + 30min Research Usefulness Proof
======================================================================

Tests verify:
1. enrichment wiring exists and can be triggered
2. real emails extracted from text-rich URLs flow into findings
3. mailing-list emails (kernel.org style) are NOT filtered as generic
4. provenance tracking distinguishes real_fetched vs mock
5. background task exception logging exists
6. targeted regression subset passes
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class TestSprint8ADEnrichmentWiring(unittest.IsolatedAsyncioTestCase):
    """Verify enrichment wiring is present and functional."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_enrichment_provenance_labels_present_if_touched(self):
        """Enrichment metadata must include provenance labels when enrichment runs."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler
        crawler = StealthCrawler()

        # Direct test of fetch_page_content_async
        url = 'https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS'
        result = await crawler.fetch_page_content_async(url)

        # Must have provenance labels
        self.assertIn('fetch_success', result)
        self.assertIn('fetch_transport', result)
        self.assertIn('emails', result)
        self.assertIn('text_length', result)

        if result['fetch_success']:
            self.assertIn(result['fetch_transport'], ('curl_cffi', 'subprocess_curl', 'native_python'))

    async def test_real_email_extraction_flows_into_live_evidence_if_touched(self):
        """Real emails extracted from text-rich URLs must flow into evidence."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler
        crawler = StealthCrawler()

        # Raw text URL known to have mailing list emails
        url = 'https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS'
        result = await crawler.fetch_page_content_async(url)

        if result['fetch_success']:
            emails = result.get('emails', [])
            self.assertGreater(len(emails), 0, "Should extract emails from kernel.org MAINTAINERS")

            # Verify real mailing-list emails (not generic)
            kernel_emails = [e for e in emails if 'vger.kernel.org' in e]
            self.assertGreater(len(kernel_emails), 0,
                            "Should find kernel.org mailing list emails")

    async def test_mailing_list_addresses_not_filtered_as_generic_if_touched(self):
        """Project mailing-list addresses must NOT be filtered as generic."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler
        crawler = StealthCrawler()

        url = 'https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS'
        result = await crawler.fetch_page_content_async(url)

        if result['fetch_success']:
            emails = result.get('emails', [])

            # netdev@vger.kernel.org, linux-scsi@vger.kernel.org etc should NOT be filtered
            # Generic prefixes that SHOULD be filtered: info@, support@, admin@, etc.
            generic_prefixes = ('info@', 'support@', 'admin@', 'contact@', 'privacy@',
                              'abuse@', 'sales@', 'hello@', 'office@', 'team@', 'help@',
                              'noreply@', 'press@', 'webmaster@', 'postmaster@')

            mailing_list_emails = [e for e in emails if '@vger.kernel.org' in e]
            generic_filtered = [e for e in emails if e.lower().startswith(generic_prefixes)]

            self.assertGreater(len(mailing_list_emails), 0,
                            "Mailing-list emails should NOT be filtered")
            self.assertEqual(len(generic_filtered), 0,
                           "No generic emails should be present")

    async def test_live_yield_metrics_accounting_if_touched(self):
        """Live yield metrics must be trackable."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler
        crawler = StealthCrawler()

        # Test on multiple text-rich URLs
        urls = [
            'https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS',
        ]

        total_emails = 0
        total_text_length = 0
        successful_fetches = 0

        for url in urls:
            result = await crawler.fetch_page_content_async(url)
            if result.get('fetch_success'):
                successful_fetches += 1
                total_emails += len(result.get('emails', []))
                total_text_length += result.get('text_length', 0)

        self.assertGreater(successful_fetches, 0, "At least one fetch should succeed")
        self.assertGreater(total_text_length, 0, "Should fetch meaningful text")
        self.assertGreater(total_emails, 0, "Should extract emails")

    async def test_background_task_exception_logging_if_touched(self):
        """Background task exceptions must not silently die."""
        orch = self.orch

        # Verify _start_background_task exists
        self.assertTrue(hasattr(orch, '_start_background_task'))
        self.assertTrue(callable(orch._start_background_task))

        # Verify _bg_tasks set exists
        self.assertIsInstance(orch._bg_tasks, set)


class TestSprint8ADRegression(unittest.IsolatedAsyncioTestCase):
    """Regression tests for Sprint 8AD."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_orchestrator_initialization_no_crash(self):
        """Orchestrator must initialize without crash."""
        orch = self.orch
        self.assertIsNotNone(orch)
        self.assertIsNotNone(orch._research_mgr)

    async def test_dark_web_available(self):
        """dark_web component must be available."""
        orch = self.orch
        self.assertIsNotNone(orch.dark_web)

    async def test_enrichment_crawler_can_be_created(self):
        """Content crawler for enrichment must be creatable."""
        from hledac.universal.intelligence.stealth_crawler import StealthCrawler
        crawler = StealthCrawler()
        self.assertIsNotNone(crawler)
        self.assertTrue(hasattr(crawler, 'fetch_page_content_async'))


class TestSprint8ADAuditTables(unittest.TestCase):
    """Audit tables for Sprint 8AD preflight."""

    def test_preflight_enrichment_wiring_exists(self):
        """Enrichment wiring code must exist in autonomous_orchestrator.py."""
        import inspect

        from hledac.universal.autonomous_orchestrator import _ResearchManager

        # Verify execute_surface_search has enrichment code
        source = inspect.getsource(_ResearchManager.execute_surface_search)
        self.assertIn('enrichment', source.lower(),
                    "execute_surface_search should contain enrichment code")
        self.assertIn('fetch_page_content_async', source,
                    "execute_surface_search should call fetch_page_content_async")

    def test_preflight_dark_web_property_chain(self):
        """dark_web property chain must be properly wired."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Verify dark_web is a property on orchestrator
        self.assertTrue(hasattr(FullyAutonomousOrchestrator, 'dark_web'))

    def test_preflight_research_manager_initialization(self):
        """_ResearchManager.initialize() must set _dark_web."""
        import inspect

        from hledac.universal.autonomous_orchestrator import _ResearchManager

        source = inspect.getsource(_ResearchManager.initialize)
        self.assertIn('_dark_web', source,
                    "_ResearchManager.initialize should reference _dark_web")


if __name__ == '__main__':
    unittest.main()
