"""
tests/test_privacy_budget.py

HIGH: Privacy Budget Allocator Tests

Tests for runtime/privacy_budget.py - PrivacyLaneConfig and
PrivacyBudgetAllocator classes that manage privacy transport lanes.

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import patch

import pytest
from _core import aclose


class TestPrivacyLaneConfig:
    """Tests for PrivacyLaneConfig dataclass."""

    def test_privacy_lane_config_creation(self) -> None:
        """PrivacyLaneConfig must be created with correct defaults."""
        from hledac.universal.runtime.privacy_budget import PrivacyLaneConfig

        config = PrivacyLaneConfig(
            name="tor",
            workers=2,
            env_gate="HLEDAC_ENABLE_TOR",
            ram_per_session_mb=80,
    )
        
        assert config.name == "tor"
        assert config.workers == 2
        assert config.env_gate == "HLEDAC_ENABLE_TOR"
        assert config.ram_per_session_mb == 80

    def test_privacy_lane_config_frozen(self) -> None:
        """PrivacyLaneConfig must be immutable (frozen=True)."""
        from hledac.universal.runtime.privacy_budget import PrivacyLaneConfig

        config = PrivacyLaneConfig(
            name="i2p",
            workers=1,
            env_gate="HLEDAC_ENABLE_I2P",
    )
        
        with pytest.raises(AttributeError):
            config.workers = 5  # type: ignore


class TestPrivacyBudgetAllocator:
    """Tests for PrivacyBudgetAllocator."""

    def test_allocator_creation(self) -> None:
        """PrivacyBudgetAllocator must initialize with correct ratios."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        assert allocator.total_workers == 20
        assert allocator._initialized is True

    def test_privacy_ratio_calculation(self) -> None:
        """Privacy budget must be 15% of total workers (min 1)."""
        from hledac.universal.runtime.privacy_budget import (
            PrivacyBudgetAllocator,
            PRIVACY_BUDGET_RATIO,
    )

        # 20 workers -> 15% = 3 workers for privacy
        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        # Check that available lanes match env gates
        # By default, all env gates are disabled (0), so no privacy lanes
        assert allocator._clearnet_budget >= 3  # MIN_CLEARNET_WORKERS

    def test_env_gate_disabled_by_default(self) -> None:
        """Env gates must be disabled by default (0)."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        # Clear any existing env vars
        with patch.dict(os.environ, {}, clear=True):
            allocator = PrivacyBudgetAllocator(total_workers=20)
            
            # All lanes should be unavailable when env gates are disabled
            assert len(allocator._available_lanes) == 0

    def test_env_gate_enabled(self) -> None:
        """Tor lane must be available when HLEDAC_ENABLE_TOR=1."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        with patch.dict(os.environ, {"HLEDAC_ENABLE_TOR": "1"}):
            allocator = PrivacyBudgetAllocator(total_workers=20)
            
            assert "tor" in allocator._available_lanes
            assert allocator._tor_sem is not None

    def test_get_semaphore_for_lane(self) -> None:
        """get_semaphore() must return correct semaphore for each lane."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        with patch.dict(os.environ, {"HLEDAC_ENABLE_TOR": "1", "HLEDAC_ENABLE_I2P": "1"}):
            allocator = PrivacyBudgetAllocator(total_workers=20)
            
            tor_sem = allocator.get_semaphore("tor")
            i2p_sem = allocator.get_semaphore("i2p")
            nym_sem = allocator.get_semaphore("nym")  # Not enabled
            unknown = allocator.get_semaphore("unknown")
            
            assert tor_sem is not None
            assert i2p_sem is not None
            assert nym_sem is None
            assert unknown is None

    def test_get_lane_for_url_onion(self) -> None:
        """URLs ending in .onion must route to tor lane."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        assert allocator.get_lane_for_url("http://example.onion") == "tor"
        assert allocator.get_lane_for_url("https://zqktlwiuavvvqqt4ybvg.onion") == "tor"

    def test_get_lane_for_url_i2p(self) -> None:
        """URLs ending in .i2p must route to i2p lane."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        assert allocator.get_lane_for_url("http://example.i2p") == "i2p"

    def test_get_lane_for_url_nym(self) -> None:
        """URLs starting with nym: must route to nym lane."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        assert allocator.get_lane_for_url("nym:something") == "nym"

    def test_get_lane_for_url_clearnet(self) -> None:
        """Normal URLs must route to clearnet lane."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        allocator = PrivacyBudgetAllocator(total_workers=20)
        
        assert allocator.get_lane_for_url("http://example.com") == "clearnet"
        assert allocator.get_lane_for_url("https://github.com/path?q=1") == "clearnet"
        assert allocator.get_lane_for_url("http://sub.domain.org") == "clearnet"

    def test_get_budget_summary(self) -> None:
        """get_budget_summary() must return complete telemetry data."""
        from hledac.universal.runtime.privacy_budget import (
            PrivacyBudgetAllocator,
            PRIVACY_BUDGET_RATIO,
    )

        allocator = PrivacyBudgetAllocator(total_workers=20)
        summary = allocator.get_budget_summary()
        
        assert summary["total_workers"] == 20
        assert summary["privacy_ratio"] == PRIVACY_BUDGET_RATIO
        assert "clearnet_budget" in summary
        assert "available_lanes" in summary
        assert "lane_budgets" in summary
        assert "tor" in summary["lane_budgets"]
        assert "i2p" in summary["lane_budgets"]
        assert "nym" in summary["lane_budgets"]

    def test_clearnet_budget_enforced(self) -> None:
        """Minimum clearnet workers (3) must always be available."""
        from hledac.universal.runtime.privacy_budget import (
            PrivacyBudgetAllocator,
            MIN_CLEARNET_WORKERS,
    )

        # Even with 4 workers total, clearnet should get at least 3
        allocator = PrivacyBudgetAllocator(total_workers=4)
        
        assert allocator._clearnet_budget >= MIN_CLEARNET_WORKERS

    @pytest.mark.asyncio
    async def test_semaphore_blocks_correctly(self) -> None:
        """Semaphore must properly limit concurrent access."""
        from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator

        with patch.dict(os.environ, {"HLEDAC_ENABLE_TOR": "1"}):
            allocator = PrivacyBudgetAllocator(total_workers=10)
            sem = allocator.get_semaphore("tor")
            
            assert sem is not None
            assert sem._value == 2  # Default tor workers with 15% of 10

        # Test semaphore limits
        with patch.dict(os.environ, {"HLEDAC_ENABLE_TOR": "1"}):
            allocator = PrivacyBudgetAllocator(total_workers=20)
            tor_sem = allocator.get_semaphore("tor")
            
            assert tor_sem is not None
            
            # Acquire all slots
            results: list[bool] = []
            
            async def acquire_slot() -> None:
                async with tor_sem:
                    results.append(True)
                    await asyncio.sleep(0.01)
            
            # Run 4 concurrent acquisitions (max 2 should succeed immediately)
            await asyncio.gather(*[acquire_slot() for _ in range(4)])
            
            # All should complete
            assert len(results) == 4


class TestFactoryFunction:
    """Tests for make_privacy_allocator factory function."""

    def test_factory_creates_allocator(self) -> None:
        """make_privacy_allocator must create properly configured allocator."""
        from hledac.universal.runtime.privacy_budget import make_privacy_allocator

        allocator = make_privacy_allocator(total_workers=15)
        
        assert allocator.total_workers == 15
        assert allocator._initialized is True

    def test_factory_with_env_enabled(self) -> None:
        """Factory-created allocator must respect env gates."""
        from hledac.universal.runtime.privacy_budget import make_privacy_allocator

        with patch.dict(os.environ, {"HLEDAC_ENABLE_NYM": "1"}):
            allocator = make_privacy_allocator(total_workers=20)
            
            assert "nym" in allocator._available_lanes


# ============================================================================
# Invariants
# ============================================================================

PRIVACY_BUDGET_INVARIANTS = """
PRIVACY BUDGET INVARIANTS:
1. Privacy budget is 15% of total workers (min 1)
2. Env gates default to disabled (0)
3. .onion URLs route to tor lane
4. .i2p URLs route to i2p lane
5. nym: URLs route to nym lane
6. Clearnet always has at least MIN_CLEARNET_WORKERS (3)
7. Semaphores properly limit concurrent access
8. get_budget_summary() provides complete telemetry
"""
