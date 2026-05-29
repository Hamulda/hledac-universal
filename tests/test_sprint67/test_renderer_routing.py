"""
Test renderer routing - Sprint 67
Tests for asset blocking in Playwright renderer.
"""
from unittest.mock import AsyncMock

import pytest


class TestRendererRouting:
    """Tests for asset routing and blocking."""

    @pytest.mark.asyncio
    async def test_blocks_images(self):
        """Test images are blocked."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "image"

        await handler(mock_route)

        mock_route.abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_media(self):
        """Test media are blocked."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "media"

        await handler(mock_route)

        mock_route.abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_fonts(self):
        """Test fonts are blocked."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media", "font"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "font"

        await handler(mock_route)

        mock_route.abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_allows_documents(self):
        """Test documents are allowed."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "document"

        await handler(mock_route)

        mock_route.continue_.assert_called_once()

    @pytest.mark.asyncio
    async def test_allows_xhr(self):
        """Test XHR/fetch requests are allowed."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image", "media"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "xhr"

        await handler(mock_route)

        mock_route.continue_.assert_called_once()

    @pytest.mark.asyncio
    async def test_text_mode_blocks_stylesheet(self):
        """Test text mode blocks stylesheets."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        # Text mode blocks: image, media, font, stylesheet
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
        # Full mode blocks: image, media, font (no stylesheet)
        handler = renderer._make_route_handler(["image", "media", "font"])

        mock_route = AsyncMock()
        mock_route.request.resource_type = "stylesheet"

        await handler(mock_route)

        mock_route.continue_.assert_called_once()


class TestRouteHandler:
    """Additional route handler tests."""

    def test_handler_returns_callable(self):
        """Test _make_route_handler returns callable."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image"])

        assert callable(handler)

    @pytest.mark.asyncio
    async def test_handler_multiple_calls(self):
        """Test handler works for multiple requests."""
        from hledac.universal.coordinators.render_coordinator import PlaywrightWebKitRenderer

        renderer = PlaywrightWebKitRenderer()
        handler = renderer._make_route_handler(["image"])

        # First request - image (should abort)
        route1 = AsyncMock()
        route1.request.resource_type = "image"
        await handler(route1)
        route1.abort.assert_called_once()

        # Second request - document (should continue)
        route2 = AsyncMock()
        route2.request.resource_type = "document"
        await handler(route2)
        route2.continue_.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
