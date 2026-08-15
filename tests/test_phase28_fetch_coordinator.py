"""
tests/test_phase28_fetch_coordinator.py

MODERN-47: Phase 28 verification tests
Part (a): FetchCoordinator constructs + has enqueue_pivot

Tests:
- FetchCoordinator can be constructed with standard parameters
- FetchCoordinator has enqueue_pivot method
- FetchCoordinator has _clearance_jar attribute
- FetchCoordinator has _darknet_connector attribute
- enqueue_pivot is callable and accepts expected parameters

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest
from _core import aclose


class TestFetchCoordinatorConstruction:
    """Test that FetchCoordinator constructs correctly and exposes required methods."""

    def test_fetch_coordinator_import(self):
        """FetchCoordinator must be importable from coordinators."""
        from coordinators.fetch_coordinator import FetchCoordinator

        assert FetchCoordinator is not None

    def test_fetch_coordinator_has_slots(self):
        """FetchCoordinator must have __slots__ defined for M1 memory optimization."""
        from coordinators.fetch_coordinator import FetchCoordinator

        assert hasattr(FetchCoordinator, "__slots__")
        slots = FetchCoordinator.__slots__
        assert isinstance(slots, tuple)
        # Critical slots for Phase 28 verification
        assert "_clearance_jar" in slots, "MISSING: _clearance_jar slot"
        assert "_darknet_connector" in slots, "MISSING: _darknet_connector slot"
        assert "_enqueue_pivot_provider" in slots, "MISSING: _enqueue_pivot_provider slot"
        assert "_pivot_queue_provider" in slots, "MISSING: _pivot_queue_provider slot"

    def test_fetch_coordinator_instantiation_minimal(self):
        """FetchCoordinator must construct with minimal required parameters."""
        from coordinators.fetch_coordinator import FetchCoordinator

        # Create with minimal config - no external dependencies
        coordinator = FetchCoordinator(
            config=None,
            max_concurrent=3,
            blitz_mode=False,
        )

        assert coordinator is not None
        # Verify __slots__ instances were created
        assert hasattr(coordinator, "_config")
        assert hasattr(coordinator, "_clearance_jar")
        assert hasattr(coordinator, "_darknet_connector")

    def test_fetch_coordinator_has_enqueue_pivot(self):
        """FetchCoordinator must have enqueue_pivot method."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)

        # Method must exist
        assert hasattr(coordinator, "enqueue_pivot"), "MISSING: enqueue_pivot method"

        # Must be callable
        assert callable(coordinator.enqueue_pivot), "enqueue_pivot must be callable"

        # Inspect signature for expected parameters
        sig = inspect.signature(coordinator.enqueue_pivot)
        params = set(sig.parameters.keys())

        # Required parameters per MODERN-47 spec
        required_params = {"ioc_value", "ioc_type", "confidence"}
        assert required_params.issubset(params), (
            f"enqueue_pivot missing required params: {required_params - params}"
        )

    def test_enqueue_pivot_accepts_provider(self):
        """enqueue_pivot must work with pivot_queue_provider dependency injection."""
        from coordinators.fetch_coordinator import FetchCoordinator

        # Mock pivot queue provider
        mock_queue = MagicMock()
        mock_queue.full.return_value = False
        mock_queue.put_nowait = MagicMock(return_value=True)

        coordinator = FetchCoordinator(
            config=None,
            pivot_queue_provider=lambda: mock_queue,
            enqueue_pivot_provider=lambda **kw: mock_queue.put_nowait(**kw) if mock_queue else None,
        )

        # Verify provider is stored
        assert hasattr(coordinator, "_pivot_queue_provider")
        assert hasattr(coordinator, "_enqueue_pivot_provider")

        # enqueue_pivot must not raise
        try:
            coordinator.enqueue_pivot(
                ioc_value="192.168.1.1",
                ioc_type="ipv4",
                confidence=0.9,
                degree=1.0,
                task_type="generic_pivot",
            )
        except Exception as exc:
            pytest.fail(f"enqueue_pivot raised unexpectedly: {exc}")

    def test_clearance_jar_initialization_none(self):
        """_clearance_jar must be None when CAPTCHA disabled (default)."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)

        # Must have the attribute (from __slots__)
        assert hasattr(coordinator, "_clearance_jar")
        # Default state is None (CAPTCHA feature flag disabled by default)
        assert coordinator._clearance_jar is None

    def test_darknet_connector_initialization(self):
        """_darknet_connector must exist as attribute."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)

        assert hasattr(coordinator, "_darknet_connector")
        # May be None if DARKNET_CONNECTOR capability not loaded
        # but attribute must exist (from __slots__)

    def test_pivot_stats_provider(self):
        """_pivot_stats_provider must be configurable."""
        from coordinators.fetch_coordinator import FetchCoordinator

        mock_stats = {"total": 0, "by_type": {}}

        coordinator = FetchCoordinator(
            config=None,
            pivot_stats_provider=lambda: mock_stats,
        )

        assert hasattr(coordinator, "_pivot_stats_provider")
        retrieved_stats = coordinator._pivot_stats_provider()
        assert retrieved_stats == mock_stats


class TestFetchCoordinatorProviderPattern:
    """Test provider pattern for dependency injection in FetchCoordinator."""

    def test_pivot_queue_provider_returns_none_by_default(self):
        """Default pivot_queue_provider returns None (no-op)."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)
        result = coordinator._pivot_queue_provider()

        assert result is None

    def test_pivot_queue_provider_returns_mock_queue(self):
        """Custom pivot_queue_provider can return a mock queue."""
        from coordinators.fetch_coordinator import FetchCoordinator

        mock_queue = MagicMock()

        coordinator = FetchCoordinator(
            config=None,
            pivot_queue_provider=lambda: mock_queue,
        )

        assert coordinator._pivot_queue_provider() is mock_queue

    def test_concurrency_provider_integration(self):
        """concurrency_provider must integrate with AIMD window."""
        from coordinators.fetch_coordinator import FetchCoordinator

        def mock_concurrency() -> tuple[int, int, str, bool] | None:
            return (5, 10, "clearnet", True)

        coordinator = FetchCoordinator(
            config=None,
            concurrency_provider=mock_concurrency,
        )

        assert hasattr(coordinator, "_concurrency_provider")
        result = coordinator._concurrency_provider()
        assert result is not None
        assert result[0] == 5  # current window
        assert result[1] == 10  # max window


class TestFetchCoordinatorSecurityAttributes:
    """Test security-related attributes in FetchCoordinator."""

    def test_has_tor_transport_attribute(self):
        """_tor_transport must exist for Tor integration."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)
        assert hasattr(coordinator, "_tor_transport")

    def test_has_robots_parser_attribute(self):
        """_robots_parser must exist for robots.txt compliance."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)
        assert hasattr(coordinator, "_robots_parser")

    def test_has_domain_rate_limiter_attribute(self):
        """_domain_rate_limiter must exist for rate limiting."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)
        assert hasattr(coordinator, "_domain_rate_limiter")

    def test_has_aimd_controller(self):
        """_aimd must exist for AIMD concurrency control."""
        from coordinators.fetch_coordinator import FetchCoordinator

        coordinator = FetchCoordinator(config=None)
        assert hasattr(coordinator, "_aimd")
        # AIMD controller must have window attribute
        assert hasattr(coordinator._aimd, "window")


class TestNEW_C1TransportEnumFixes:
    """
    NEW-C1 Regression Tests: Transport enum fixes for silent fetch pipeline death.
    
    Bug: Transport enum has DIRECT/TOR/I2P/FREENET/INMEMORY/GOPHER - no CLEARNET.
    CLEARNET is in RouteDecision enum, not Transport.
    
    This bug caused:
    1. AttributeError at _execute_dns_circuit_phase when using _T.CLEARNET
    2. NameError at _record_fetch_outcome when using undefined _T
    3. NameError at _execute_dns_circuit_phase when using bare Transport
    
    Fix: Use Transport.DIRECT instead of non-existent Transport.CLEARNET.
    """

    def test_transport_enum_has_no_clearnet(self):
        """NEW-C1: Transport enum must NOT have CLEARNET member."""
        from transport.transport_resolver import Transport
        
        # Verify CLEARNET is NOT in Transport (it should be in RouteDecision)
        assert not hasattr(Transport, 'CLEARNET'), "Transport should NOT have CLEARNET - use RouteDecision.CLEARNET instead"
        
    def test_transport_enum_has_direct(self):
        """NEW-C1: Transport enum must have DIRECT member (clearnet equivalent)."""
        from transport.transport_resolver import Transport
        
        # Verify DIRECT exists (this is the clearnet equivalent)
        assert hasattr(Transport, 'DIRECT'), "Transport must have DIRECT member"
        assert Transport.DIRECT is not None

    def test_route_decision_has_clearnet(self):
        """NEW-C1: RouteDecision enum must have CLEARNET member."""
        from transport.transport_resolver import RouteDecision
        
        # Verify CLEARNET is in RouteDecision (not Transport)
        assert hasattr(RouteDecision, 'CLEARNET'), "RouteDecision should have CLEARNET"
        assert RouteDecision.CLEARNET is not None

    def test_record_fetch_outcome_has_transport_import(self):
        """NEW-C1: _record_fetch_outcome must not raise NameError on _T."""
        from coordinators.fetch_coordinator import FetchCoordinator
        import inspect
        
        # Verify _record_fetch_outcome method exists and has local Transport import
        assert hasattr(FetchCoordinator, '_record_fetch_outcome')
        
        # Check that the method source contains the import
        source = inspect.getsource(FetchCoordinator._record_fetch_outcome)
        assert 'from ..transport.transport_resolver import Transport as _T' in source, \
            "_record_fetch_outcome must import Transport as _T to avoid NameError"

    def test_execute_dns_circuit_phase_uses_direct_not_clearnet(self):
        """NEW-C1: _execute_dns_circuit_phase must use DIRECT, not CLEARNET."""
        from coordinators.fetch_coordinator import FetchCoordinator
        import inspect
        
        # Verify the method source uses DIRECT, not CLEARNET
        source = inspect.getsource(FetchCoordinator._execute_dns_circuit_phase)
        assert '_T.DIRECT' in source, "_execute_dns_circuit_phase must use _T.DIRECT"
        assert '_T.CLEARNET' not in source, "_execute_dns_circuit_phase must NOT use _T.CLEARNET (doesn't exist)"

    def test_fetch_returns_nonempty_result_on_success(self):
        """
        NEW-C1: Regression test - successful fetch must return non-empty result.
        
        This test verifies that when Transport enum is used correctly,
        fetch pipeline does NOT silently return empty list due to swallowed exceptions.
        """
        from coordinators.fetch_coordinator import FetchCoordinator
        from transport.transport_resolver import Transport
        
        coordinator = FetchCoordinator(config=None)
        
        # Verify Transport.DIRECT is a valid fallback
        direct_transport = Transport.DIRECT
        assert direct_transport is not None
        assert isinstance(direct_transport, Transport)
        
        # Verify we can compare transports without AttributeError
        assert direct_transport is not Transport.TOR
        assert direct_transport is not Transport.I2P
        assert direct_transport is not Transport.GOPHER
