import time
from unittest.mock import patch

import pytest
from hledac.universal.utils.capability_prober import CapabilityProber


@pytest.mark.asyncio
async def test_aget_module_happy_path():
    """Test async module loading - happy path."""
    prober = CapabilityProber()  # fresh instance
    module = await prober.aget_module("time", timeout=0.5)
    assert module is not None


@pytest.mark.asyncio
async def test_aget_module_timeout():
    """Test async module loading with timeout."""
    prober = CapabilityProber()
    with patch("importlib.import_module", side_effect=lambda x: time.sleep(0.2)):
        module = await prober.aget_module("any", timeout=0.01)
        assert module is None  # timeout must expire


@pytest.mark.asyncio
async def test_aget_module_import_error():
    """Test async module loading with import error."""
    prober = CapabilityProber()
    with patch("importlib.import_module", side_effect=ImportError):
        module = await prober.aget_module("nonexistent")
        assert module is None


def test_has_module_cache():
    """Test has_module caching."""
    prober = CapabilityProber()
    # First time - find_spec
    assert prober.has_module("sys") is True
    # Second time - cache hit
    assert prober.has_module("sys") is True
    # hits should be 2 (first hit + second hit)
    assert prober.stats()["hits"] == 2


def test_has_module_missing():
    """Test has_module with missing module."""
    prober = CapabilityProber()
    assert prober.has_module("totally.nonexistent.module") is False
    assert prober.stats()["misses"] == 1


def test_has_ane_cached_property():
    """Test has_ane cached property."""
    prober = CapabilityProber()
    # Just access - should not raise
    result = prober.has_ane
    assert isinstance(result, bool)


def test_has_metal_cached_property():
    """Test has_metal cached property."""
    prober = CapabilityProber()
    # Just access - should not raise
    result = prober.has_metal
    assert isinstance(result, bool)


def test_stats_no_blocking():
    """Test stats returns without blocking operations."""
    prober = CapabilityProber()
    prober.has_module("sys")
    stats = prober.stats()
    assert "hits" in stats
    assert "cache_size" in stats
    assert "has_ane" in stats
    assert "has_metal" in stats
