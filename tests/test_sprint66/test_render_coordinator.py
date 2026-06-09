import pytest

from hledac.universal.coordinators.render_coordinator import RenderCoordinator


@pytest.mark.asyncio
async def test_render_coordinator_fallback():
    """Test RenderCoordinator fallback returns no_backend."""
    rc = RenderCoordinator()
    result = await rc.render("https://example.com")
    assert result.status == "no_backend"
    assert result.html is None


@pytest.mark.asyncio
async def test_render_coordinator_cache():
    """Test RenderCoordinator caching."""
    rc = RenderCoordinator()
    r1 = await rc.render("https://example.com")
    r2 = await rc.render("https://example.com")
    # Cache should return same content (not necessarily same object)
    assert r1.status == r2.status
    assert r1.html == r2.html


@pytest.mark.asyncio
async def test_render_result_debug_limits():
    """Test RenderResult debug dict limits."""
    from hledac.universal.coordinators.render_coordinator import RenderResult

    # Large debug should be truncated
    large_debug = {f"key{i}": "x" * 600 for i in range(10)}
    result = RenderResult(None, "error", large_debug)
    assert len(result.debug) <= 4  # max 4 keys


def test_render_coordinator_init():
    """Test RenderCoordinator initialization."""
    rc = RenderCoordinator()
    assert rc._cache_max == 200
    assert len(rc._backends) == 3
