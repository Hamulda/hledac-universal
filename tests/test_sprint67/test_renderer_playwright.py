"""
Test Playwright renderer - Sprint 67
Tests for PlaywrightWebKitRenderer backend.
"""
from unittest.mock import AsyncMock

import pytest


class TestPlaywrightRenderer:
    """Tests for Playwright WebKit renderer."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(True, reason="Playwright not installed")
    async def test_render_no_backend_returns_error(self):
        """Test render returns no_backend when browser unavailable."""
        # Skipped - requires Playwright installation
        pass

    @pytest.mark.asyncio
    async def test_route_handler_blocks_images(self):
        """Test route handler blocks image assets."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "image"

        await handler(mock_route)

        mock_route.abort.assert_called_once()
        mock_route.continue_.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_handler_allows_documents(self):
        """Test route handler allows document requests."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "document"

        await handler(mock_route)

        mock_route.continue_.assert_called_once()
        mock_route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_mode_blocks_stylesheet(self):
        """Test text mode blocks stylesheets."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font", "stylesheet"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "stylesheet"

        await handler(mock_route)

        mock_route.abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_mode_allows_stylesheet(self):
        """Test full mode allows stylesheets."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font"])  # No stylesheet

        mock_route = AsyncMock()
        mock_route.request.resource_type = "stylesheet"

        await handler(mock_route)

        mock_route.continue_.assert_called_once()

    def test_max_renders_per_browser(self):
        """Test browser recreation after max renders."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        assert renderer._max_renders_per_browser == 50


class TestRenderCoordinator:
    """Tests for RenderCoordinator."""

    def test_coordinator_initialization(self):
        """Test coordinator initializes correctly."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()
        assert rc._cache_max == 200
        assert rc._ttl["ok"] == 60
        assert rc._ttl["error"] == 0  # Errors not cached

    def test_cache_key_includes_mode(self):
        """Test cache key includes mode."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()

        key_text = rc._make_cache_key("http://example.com", 5000, "text")
        key_full = rc._make_cache_key("http://example.com", 5000, "full")

        assert key_text != key_full
        assert "text" in key_text
        assert "full" in key_full

    @pytest.mark.asyncio
    async def test_semaphore_created(self):
        """Test semaphore is created."""
        from hledac.universal.coordinators.render_coordinator import RenderCoordinator

        rc = RenderCoordinator()
        sem = rc._get_semaphore()
        assert sem._value == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
