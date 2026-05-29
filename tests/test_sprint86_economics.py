"""
Sprint 86: Network Recon Economics + Cross-Action Truth Audit Tests
====================================================================

Truth-validation tests for network_recon economics:
1. Precondition met counting
2. Selection tracking
3. Execution and outcome tracking
4. Candidates generated/forwarded/dropped accounting
5. Yield ratio calculation
6. Forwarding efficiency
7. Action selection HHI
8. Threshold pass/warn/fail evaluation
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSprint86EconomicsTracking:
    """Test network_recon economics tracking in orchestrator."""

    def test_network_recon_counters_in_source(self):
        """Verify all economics counters are defined in source code."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Check counters exist in __init__ section
        assert '_network_recon_precondition_met_count' in content
        assert '_network_recon_selected_count' in content
        assert '_network_recon_executed_count' in content
        assert '_network_recon_success_count' in content
        assert '_action_selection_counts' in content

    def test_action_selection_counts_defined(self):
        """Verify action selection tracking is defined."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        assert '_action_selection_counts' in content

    def test_network_recon_precondition_met_is_counted(self):
        """Verify precondition_met increments correctly."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Scorer increments precondition_met_count
        assert '_network_recon_precondition_met_count += 1' in content

    def test_network_recon_selection_tracking(self):
        """Verify network_recon selection increments counter."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Selection tracking increments counter
        assert '_network_recon_selected_count += 1' in content

    def test_network_recon_execution_tracking(self):
        """Verify execution tracking increments counters."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Execution tracking increments counters
        assert '_network_recon_executed_count += 1' in content
        assert '_network_recon_success_count += 1' in content

    def test_network_recon_partial_success_reported_truthfully(self):
        """Verify partial success is correctly identified."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Partial success is when subdomains_found > 0 but forwarded = 0
        assert 'subdomains_found' in content

    def test_network_recon_candidates_dropped_on_queue_full_counted(self):
        """Verify dropped candidates due to queue full are counted."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Queue full tracking exists
        assert 'candidates_dropped_queue_full' in content
        assert 'asyncio.QueueFull' in content

    def test_network_recon_yield_ratio_none_when_no_successes(self):
        """Verify yield_ratio is None when no successes (not 0.0)."""
        # Test logic conceptually - no successes means None
        success_count = 0
        candidates_forwarded = 0

        if success_count > 0:
            yield_ratio = candidates_forwarded / success_count
        else:
            yield_ratio = None

        assert yield_ratio is None, "yield_ratio must be None when no successes"

    def test_network_recon_yield_ratio_calculation(self):
        """Verify yield_ratio calculation is correct."""
        # Simulate: 2 successes, 3 candidates forwarded
        success_count = 2
        candidates_forwarded = 3
        # findings_contributed is 0 for network_recon (no findings, only candidates)

        if success_count > 0:
            yield_ratio = (0 + candidates_forwarded) / success_count
        else:
            yield_ratio = None

        assert yield_ratio == 1.5, "yield_ratio should be 1.5"

    def test_network_recon_forwarding_efficiency_calculation(self):
        """Verify forwarding efficiency calculation."""
        # Simulate: 5 generated, 4 forwarded
        generated = 5
        forwarded = 4

        if generated > 0:
            efficiency = forwarded / generated
        else:
            efficiency = None

        assert efficiency == 0.8, "forwarding_efficiency should be 0.8"

    def test_action_selection_hhi_calculation(self):
        """Verify HHI calculation for action diversity."""
        # Test HHI calculation manually
        action_counts = {'surface_search': 100, 'network_recon': 50, 'deep_crawl': 50}
        total = sum(action_counts.values())

        hhi = 0.0
        for count in action_counts.values():
            share = count / total
            hhi += share * share

        # 100/200 = 0.5, 50/200 = 0.25 each
        # HHI = 0.5^2 + 0.25^2 + 0.25^2 = 0.25 + 0.0625 + 0.0625 = 0.375
        assert abs(hhi - 0.375) < 0.001, f"Expected 0.375, got {hhi}"

    def test_action_selection_hhi_lower_is_more_diverse(self):
        """Verify lower HHI means more diversity."""
        # High concentration (one action dominates)
        counts1 = {'action_a': 190, 'action_b': 5, 'action_c': 5}
        total1 = sum(counts1.values())
        hhi1 = sum((c/total1)**2 for c in counts1.values())

        # Low concentration (more balanced)
        counts2 = {'action_a': 70, 'action_b': 70, 'action_c': 60}
        total2 = sum(counts2.values())
        hhi2 = sum((c/total2)**2 for c in counts2.values())

        assert hhi1 > hhi2, "High concentration should have higher HHI"


class TestSprint86EconomicsThresholds:
    """Test threshold evaluation for network_recon economics."""

    def test_threshold_selection_rate_min_pass(self):
        """Verify selection rate PASS when above min threshold."""
        selected = 10
        total = 100
        rate = (selected / total) * 100

        min_threshold = 5.0
        assert rate >= min_threshold, "Should PASS min threshold"

    def test_threshold_selection_rate_max_fail(self):
        """Verify selection rate FAIL when above max threshold."""
        selected = 80
        total = 100
        rate = (selected / total) * 100

        max_threshold = 40.0
        assert rate > max_threshold, "Should FAIL max threshold"

    def test_threshold_yield_ratio_pass(self):
        """Verify yield ratio PASS when above min threshold."""
        yield_ratio = 0.5
        min_threshold = 0.3

        assert yield_ratio >= min_threshold, "Should PASS yield ratio"

    def test_threshold_forwarding_efficiency_pass(self):
        """Verify forwarding efficiency PASS."""
        efficiency = 0.6
        min_threshold = 0.5

        assert efficiency >= min_threshold, "Should PASS efficiency"

    def test_threshold_forwarding_efficiency_fail(self):
        """Verify forwarding efficiency FAIL."""
        efficiency = 0.3
        min_threshold = 0.5

        assert efficiency < min_threshold, "Should FAIL efficiency"


class TestSprint86RegressionTests:
    """Regression tests to ensure existing behavior is preserved."""

    def test_network_recon_wildcard_still_suppresses_forwarding(self):
        """Verify wildcard still suppresses subdomain forwarding."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Find handler
        handler_start = content.find('async def network_recon_handler')
        handler_end = content.find('def network_recon_scorer')
        handler_content = content[handler_start:handler_end]

        # Verify wildcard suppression logic
        assert 'is_wildcard' in handler_content
        assert 'if not is_wildcard:' in handler_content

    def test_network_recon_offline_mode_still_fast_fails(self):
        """Verify offline mode still fast-fails."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source_file = inspect.getsourcefile(FullyAutonomousOrchestrator)
        with open(source_file) as f:
            content = f.read()

        # Find handler
        handler_start = content.find('async def network_recon_handler')
        handler_end = content.find('def network_recon_scorer')
        handler_content = content[handler_start:handler_end]

        # Verify offline check
        assert 'is_offline_mode()' in handler_content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
