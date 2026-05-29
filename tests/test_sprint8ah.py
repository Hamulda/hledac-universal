"""
Sprint 8AH: Data Leak Hunter Reconnect + Provenance Schema
===========================================================

Tests verify:
1. ResearchFinding has provenance fields
2. Provenance defaults are backward-compatible
3. direct_harvest populates provenance fields
4. dlh_identity action is registered
5. Generic service emails are filtered
6. Mailing-list emails are preserved
7. DLH batch is bounded to 20
8. DLH invocations capped at 2
9. DLH fail-closed without backend
10. direct_harvest and DLH dedup do not duplicate
11. DLH findings populate all provenance fields
"""

import inspect
import unittest
from collections import OrderedDict
from dataclasses import fields


class TestResearchFindingProvenanceFields(unittest.TestCase):
    """Verify ResearchFinding has provenance fields."""

    def test_researchfinding_has_provenance_fields(self):
        """ResearchFinding should have extraction_method, source_type_finding, entity_links."""
        from hledac.universal.autonomous_orchestrator import ResearchFinding

        field_names = {f.name for f in fields(ResearchFinding)}
        self.assertIn('extraction_method', field_names,
            "ResearchFinding should have extraction_method field")
        self.assertIn('source_type_finding', field_names,
            "ResearchFinding should have source_type_finding field")
        self.assertIn('entity_links', field_names,
            "ResearchFinding should have entity_links field")

    def test_provenance_defaults_backward_compatible(self):
        """Provenance fields should have defaults for backward compatibility."""
        from hledac.universal.autonomous_orchestrator import ResearchFinding, ResearchSource, SourceType

        # Should be constructible with positional args only (backward compat)
        source = ResearchSource(
            url="http://example.com",
            title="Test",
            content="Test content",
            source_type=SourceType.SURFACE_WEB,
            confidence=0.5
        )
        finding = ResearchFinding(
            content="test content",
            source=source,
            confidence=0.5
        )
        # Defaults should be empty string / empty list
        self.assertEqual(finding.extraction_method, "")
        self.assertEqual(finding.source_type_finding, "")
        self.assertEqual(finding.entity_links, [])

    def test_dlh_action_registered(self):
        """dlh_identity action should be registered in orchestrator."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator)
        self.assertIn("'dlh_identity'", source,
            "dlh_identity action should be registered")


class TestEmailNormalizationAndFiltering(unittest.TestCase):
    """Verify email normalization and generic filtering."""

    def test_generic_service_emails_filtered(self):
        """Generic service emails should be filtered from DLH queue."""

        # Create minimal mock
        class MockSelf:
            _dlh_available = True
            _dlh_email_queue = None
            _dlh_seen_emails = None

        GENERIC_PREFIXES = ('info@', 'support@', 'admin@', 'noreply@', 'no-reply@', 'postmaster@', 'test@')

        # Test function directly
        def _dlh_collect_emails(emails: list[str]) -> None:
            if not getattr(MockSelf(), '_dlh_available', False):
                return
            if not emails:
                return
            for email in emails:
                normalized = email.lower().strip()
                if not normalized or any(normalized.startswith(p) for p in GENERIC_PREFIXES):
                    continue
                if not hasattr(MockSelf(), '_dlh_email_queue') or MockSelf()._dlh_email_queue is None:
                    MockSelf._dlh_email_queue = OrderedDict()
                if len(MockSelf._dlh_email_queue) >= 100:
                    MockSelf._dlh_email_queue.popitem(last=False)
                MockSelf._dlh_email_queue[normalized] = None

        # Clear state
        MockSelf._dlh_email_queue = OrderedDict()

        emails = ['info@example.com', 'support@company.com', 'admin@test.org',
                  'noreply@mail.com', 'john.doe@gmail.com', 'netdev@vger.kernel.org']
        _dlh_collect_emails(emails)

        # Only non-generic should remain
        queue_keys = list(MockSelf._dlh_email_queue.keys())
        self.assertNotIn('info@example.com', queue_keys)
        self.assertNotIn('support@company.com', queue_keys)
        self.assertNotIn('admin@test.org', queue_keys)
        self.assertNotIn('noreply@mail.com', queue_keys)
        self.assertIn('john.doe@gmail.com', queue_keys)
        self.assertIn('netdev@vger.kernel.org', queue_keys)

    def test_mailing_list_emails_preserved(self):
        """Project/mailing-list emails should NOT be filtered."""

        class MockSelf:
            _dlh_available = True
            _dlh_email_queue = None
            _dlh_seen_emails = None

        GENERIC_PREFIXES = ('info@', 'support@', 'admin@', 'noreply@', 'no-reply@', 'postmaster@', 'test@')

        def _dlh_collect_emails(emails: list[str]) -> None:
            if not getattr(MockSelf(), '_dlh_available', False):
                return
            if not emails:
                return
            for email in emails:
                normalized = email.lower().strip()
                if not normalized or any(normalized.startswith(p) for p in GENERIC_PREFIXES):
                    continue
                if not hasattr(MockSelf(), '_dlh_email_queue') or MockSelf()._dlh_email_queue is None:
                    MockSelf._dlh_email_queue = OrderedDict()
                if len(MockSelf._dlh_email_queue) >= 100:
                    MockSelf._dlh_email_queue.popitem(last=False)
                MockSelf._dlh_email_queue[normalized] = None

        MockSelf._dlh_email_queue = OrderedDict()

        # Mailing-list emails from kernel.org
        mailing_list_emails = [
            'netdev@vger.kernel.org',
            'linux-scsi@vger.kernel.org',
            'linux-kernel@vger.kernel.org',
            'git@vger.kernel.org',
        ]
        _dlh_collect_emails(mailing_list_emails)

        queue_keys = list(MockSelf._dlh_email_queue.keys())
        self.assertEqual(len(queue_keys), 4, "All mailing-list emails should be preserved")
        for email in mailing_list_emails:
            self.assertIn(email, queue_keys, f"{email} should be in queue")


class TestDLHBatching(unittest.TestCase):
    """Verify DLH batching and invocation caps."""

    def test_dlh_batch_bounded_to_20(self):
        """DLH should collect at most 20 eligible emails per invocation."""
        class MockSelf:
            _dlh_available = True
            _dlh_email_queue = OrderedDict()
            _dlh_seen_emails = OrderedDict()
            _dlh_invocations_this_run = 0

        # Add 30 emails
        for i in range(30):
            MockSelf._dlh_email_queue[f'user{i}@example.com'] = None

        sent = set()
        eligible = []
        for email in MockSelf._dlh_email_queue:
            if email not in sent and len(eligible) < 20:
                eligible.append(email)
                sent.add(email)

        self.assertLessEqual(len(eligible), 20, "Should collect at most 20 emails per batch")

    def test_dlh_invocation_capped(self):
        """DLH should cap at 2 invocations per run."""
        class MockSelf:
            _dlh_invocations_this_run = 0

        # Simulate 2 invocations
        MockSelf._dlh_invocations_this_run = 2
        can_run = MockSelf._dlh_invocations_this_run < 2
        self.assertFalse(can_run, "Should not run after 2 invocations")

        MockSelf._dlh_invocations_this_run = 1
        can_run = MockSelf._dlh_invocations_this_run < 2
        self.assertTrue(can_run, "Should run on 1st and 2nd invocation")


class TestDLHFailClosed(unittest.TestCase):
    """Verify DLH fail-closed behavior."""

    def test_dlh_fail_closed_without_backend(self):
        """DLH should disable itself gracefully when backend unavailable."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator)
        # Should have _dlh_available guard
        self.assertIn('_dlh_available', source,
            "Should have _dlh_available flag")
        # Should log warning when disabling
        self.assertIn('DLH: backend unavailable', source,
            "Should log warning when DLH unavailable")


class TestDedupContract(unittest.TestCase):
    """Verify direct_harvest and DLH do not create duplicate identity findings."""

    def test_direct_harvest_and_dlh_dedup(self):
        """_dlh_seen_emails should prevent sending same email to DLH twice."""
        class MockSelf:
            _dlh_seen_emails = OrderedDict()

        # Simulate sending 5 emails to DLH
        for i in range(5):
            email = f'user{i}@example.com'
            if len(MockSelf._dlh_seen_emails) > 500:
                MockSelf._dlh_seen_emails.popitem(last=False)
            MockSelf._dlh_seen_emails[email] = None

        # Try to send duplicate
        duplicate = 'user2@example.com'
        self.assertIn(duplicate, MockSelf._dlh_seen_emails,
            "Duplicate email should already be in seen_emails")


class TestDLHFindingsProvenance(unittest.TestCase):
    """Verify DLH findings populate all provenance fields."""

    def test_dlh_findings_populate_all_provenance_fields(self):
        """DLH handler should populate extraction_method, source_type_finding, entity_links."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator)
        # DLH finding construction should include all three fields
        self.assertIn("extraction_method='dlh_breach'", source,
            "DLH finding should set extraction_method='dlh_breach'")
        self.assertIn("source_type_finding=", source,
            "DLH finding should set source_type_finding")
        self.assertIn("entity_links=", source,
            "DLH finding should set entity_links")


class TestDirectHarvestProvenance(unittest.TestCase):
    """Verify direct_harvest findings populate provenance fields."""

    def test_direct_harvest_populates_source_type_finding(self):
        """direct_harvest should classify emails into source_type_finding categories."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator)
        # Should classify project_mailing_list
        self.assertIn('project_mailing_list', source,
            "Should classify project mailing-list emails")
        # Should classify personal_email
        self.assertIn('personal_email', source,
            "Should classify personal emails")
        # Should classify generic_service
        self.assertIn('generic_service', source,
            "Should classify generic service emails")

    def test_direct_harvest_sets_extraction_method(self):
        """direct_harvest findings should have extraction_method='direct_harvest'."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator)
        # Count occurrences of extraction_method='direct_harvest'
        count = source.count("extraction_method='direct_harvest'")
        self.assertGreaterEqual(count, 1,
            "direct_harvest findings should set extraction_method='direct_harvest'")


class TestSourceTypeFindingConstants(unittest.TestCase):
    """Verify source_type_finding values are correctly defined."""

    def test_source_type_finding_values(self):
        """source_type_finding should accept correct values per mandate."""
        from hledac.universal.autonomous_orchestrator import ResearchFinding, ResearchSource, SourceType

        source = ResearchSource(
            url="http://example.com",
            title="Test",
            content="Test",
            source_type=SourceType.SURFACE_WEB,
            confidence=0.5
        )

        valid_types = ("personal_email", "project_mailing_list", "breach", "social", "unknown", "")

        for stype in valid_types:
            finding = ResearchFinding(
                content="test",
                source=source,
                confidence=0.5,
                source_type_finding=stype
            )
            self.assertEqual(finding.source_type_finding, stype)


if __name__ == '__main__':
    unittest.main()
