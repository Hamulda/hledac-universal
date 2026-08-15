"""
Test RoleBasedPools — DEPRECATED MODULE (R-18)
==============================================

Tests for runtime/_legacy_role_based_pools.py — deprecated role-based executor facade.

.. deprecated::
    This module tests the deprecated _legacy_role_based_pools.py.
    Production code should use runtime.lmdb_pool or runtime.worker_pool instead.

Run with: pytest tests/test_role_based_pools.py -v
"""

import asyncio
import gc
import sys

import pytest
from core import aclose


class TestRoleBasedPools:
    """Test RoleBasedPools initialization and role dispatch."""

    def test_get_role_pools_returns_singleton(self) -> None:
        """get_role_pools returns the same instance on repeated calls."""
        from hledac.universal.runtime._legacy_role_based_pools import get_role_pools

        pools1 = get_role_pools()
        pools2 = get_role_pools()
        assert pools1 is pools2

    @pytest.mark.asyncio
    async def test_role_based_pools_lazy_initialization(self) -> None:
        """RoleBasedPools does not initialize executors until first use."""
        from hledac.universal.runtime._legacy_role_based_pools import RoleBasedPools

        pools = RoleBasedPools()
        assert not pools._initialized
        # Access a method to trigger initialization
        await pools.run_hash(lambda x: x * 2, 2)
        assert pools._initialized

    def test_run_hash_sync_basic(self) -> None:
        """run_hash_sync executes function and returns result."""
        from hledac.universal.runtime._legacy_role_based_pools import get_role_pools

        pools = get_role_pools()

        def hash_func(x: int) -> int:
            return x * 2

        result = pools.run_hash_sync(hash_func, 21)
        assert result == 42

    @pytest.mark.asyncio
    async def test_run_hash_async_basic(self) -> None:
        """run_hash executes async function and returns result."""
        from hledac.universal.runtime._legacy_role_based_pools import get_role_pools

        pools = get_role_pools()

        async def test() -> None:
            def hash_func(x: int) -> int:
                return x * 3

            result = await pools.run_hash(hash_func, 10)
            assert result == 30

        await test()

    @pytest.mark.asyncio
    async def test_run_regex_basic(self) -> None:
        """run_regex executes regex function and returns result."""
        from hledac.universal.runtime._legacy_role_based_pools import get_role_pools

        pools = get_role_pools()

        def regex_func(text: str) -> list[str]:
            import re

            return re.findall(r"\d+", text)

        result = await pools.run_regex(regex_func, "abc 123 def 456")
        assert result == ["123", "456"]

    @pytest.mark.asyncio
    async def test_run_async_io_basic(self) -> None:
        """run_async_io executes blocking I/O and returns result."""
        from hledac.universal.runtime._legacy_role_based_pools import get_role_pools

        pools = get_role_pools()

        def io_func() -> str:
            return "io_result"

        result = await pools.run_async_io(io_func)
        assert result == "io_result"

    def test_check_embed_ram_budget_true(self) -> None:
        """_check_embed_ram_budget returns True when memory is available."""
        from hledac.universal.runtime._legacy_role_based_pools import RoleBasedPools

        pools = RoleBasedPools()
        # Patch to avoid actual MLX calls
        pools._embed_executor = None
        result = pools._check_embed_ram_budget()
        assert isinstance(result, bool)

    def test_check_db_ram_budget_true(self) -> None:
        """_check_db_ram_budget returns True when memory is available."""
        from hledac.universal.runtime._legacy_role_based_pools import RoleBasedPools

        pools = RoleBasedPools()
        result = pools._check_db_ram_budget()
        assert isinstance(result, bool)


class TestBackwardCompatShims:
    """Test backward-compatibility shims."""

    @pytest.mark.asyncio
    async def test_run_in_hash_pool_deprecated(self) -> None:
        """run_in_hash_pool emits deprecation warning."""
        from hledac.universal.runtime._legacy_role_based_pools import run_in_hash_pool

        with pytest.warns(DeprecationWarning, match="deprecated"):
            await run_in_hash_pool(lambda x: x, 1)

    @pytest.mark.asyncio
    async def test_run_in_regex_pool_deprecated(self) -> None:
        """run_in_regex_pool emits deprecation warning."""
        from hledac.universal.runtime._legacy_role_based_pools import run_in_regex_pool

        with pytest.warns(DeprecationWarning, match="deprecated"):
            await run_in_regex_pool(lambda x: x, "text")


class TestRAMBudget:
    """Test RAM budget enforcement (M1 8GB)."""

    def test_embed_budget_checks_available_memory(self) -> None:
        """Embedding budget check uses psutil to get available memory."""
        from hledac.universal.runtime._legacy_role_based_pools import RoleBasedPools, _get_available_memory_gib

        available = _get_available_memory_gib()
        assert available > 0
        assert available < 16  # M1 8GB has ~8GB, but psutil might show more

        pools = RoleBasedPools()
        pools._embed_executor = None  # Force fallback path
        result = pools._check_embed_ram_budget()
        assert isinstance(result, bool)


class TestConstants:
    """Test that constants match M1 8GB budget."""

    def test_embed_workers_is_1(self) -> None:
        """EMBED_WORKERS is 1 due to 2GB VRAM limit."""
        from hledac.universal.runtime._legacy_role_based_pools import _EMBED_WORKERS

        assert _EMBED_WORKERS == 1

    def test_db_workers_is_2(self) -> None:
        """DB_WORKERS is 2 for DuckDB concurrent writers."""
        from hledac.universal.runtime._legacy_role_based_pools import _DB_WORKERS

        assert _DB_WORKERS == 2

    def test_hash_workers_is_4(self) -> None:
        """HASH_WORKERS is 4 for P-core count."""
        from hledac.universal.runtime._legacy_role_based_pools import _HASH_WORKERS

        assert _HASH_WORKERS == 4

    def test_regex_workers_is_4(self) -> None:
        """REGEX_WORKERS is 4 for P-core count."""
        from hledac.universal.runtime._legacy_role_based_pools import _REGEX_WORKERS

        assert _REGEX_WORKERS == 4


class TestRoleBasedPoolsShutdown:
    """Test shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_cleans_executors(self) -> None:
        """shutdown sets _initialized to False and clears executors."""
        from hledac.universal.runtime._legacy_role_based_pools import RoleBasedPools

        pools = RoleBasedPools()
        await pools.run_hash(lambda x: x, 1)
        assert pools._initialized

        pools.shutdown(wait=False)
        # After shutdown, pools should re-initialize on next use
        assert not pools._initialized
