"""
Test renderer concurrency - Sprint 67
Tests for semaphore and timeout in RenderCoordinator.
"""

import pytest


class TestRendererConcurrency:
    """Tests for render serialization and timeout."""

    @pytest.mark.asyncio
    async def test_semaphore_created_lazily(self):
        """Test semaphore is created on first use."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()

        # Initially no semaphore
        assert rc._semaphore is None

        # Get semaphore
        sem = rc._get_semaphore()

        # Now it exists
        assert rc._semaphore is not None
        assert sem._value == 1

    def test_ttl_for_errors_is_zero(self):
        """Test errors have TTL=0 (not cached)."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()

        # Errors should not be cached
        assert rc._ttl["error"] == 0
        assert rc._ttl["timeout"] == 5  # Timeouts cached briefly
        assert rc._ttl["ok"] == 60  # Success cached longer

    def test_coordinator_has_backends(self):
        """Test coordinator has all backends."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()

        assert len(rc._backends) == 3


class TestRendererTimeout:
    """Tests for renderer timeout handling."""

    def test_render_method_signature(self):
        """Test render accepts expected parameters."""
        import inspect

        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()

        # Check signature
        sig = inspect.signature(rc.render)
        params = list(sig.parameters.keys())

        assert "url" in params
        assert "deadline_ms" in params
        assert "mode" in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
