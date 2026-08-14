"""
metal_device.py — Metal GPU Device Abstraction
==============================================



PEP 698: Extracted from DeepHermes3Engine._get_gpu_memory, _get_metal_tier_thresholds
M1 8GB UMA-aware memory management.

PEP 749 (import self) patterns for clean circular import resolution.

C1-X FIX: Import MLX_AVAILABLE from SSOT (utils.mlx_memory) instead of duplicate detection.
Uses importlib.metadata.version("mlx") — no mlx.core import at module load.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

# C1-X FIX: Import from SSOT instead of duplicate detection
# Uses importlib.metadata.version("mlx") — no mlx.core import at module load
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE

# C1-X FIX: Remove duplicate _MLX_AVAILABLE_GLOBAL and _check_mlx_availability()
# Now uses SSOT MLX_AVAILABLE from utils.mlx_memory (zero-import detection)


@dataclass(frozen=True)
class MetalMemoryStats:
    """Immutable snapshot of Metal memory state."""
    active_bytes: int = 0
    active_gb: float = 0.0
    peak_bytes: int = 0
    peak_gb: float = 0.0
    metal_tier: str = "unknown"
    pressure_level: str = "normal"


@dataclass(frozen=True, slots=True)
class MetalDevice:
    """
    Metal GPU device with M1 8GB UMA awareness.

    Extracted from DeepHermes3Engine GPU management to enable:
    1. Testability in isolation
    2. Consistent GPU memory policy across brain engines
    3. Proper resource tracking without scattered self._* references

    M1 8GB UMA Budget (CLAUDE.md F350M-R):
    - macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB = ~6.25GB max
    - Soft ceiling: 5.5 GiB → hard cap fetch concurrency
    """
    # C1-X FIX: Use SSOT MLX_AVAILABLE instead of local detection
    _mlx_available: bool = field(default_factory=lambda: MLX_AVAILABLE)

    # M1 Metal memory thresholds (bytes)
    _TIER_THRESHOLDS: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "low": (256 * 1024**2, 512 * 1024**2),       # 256MB - 512MB
        "medium": (512 * 1024**2, 1024 * 1024**2),  # 512MB - 1GB
        "high": (1024 * 1024**2, 2 * 1024**3),       # 1GB - 2GB
        "critical": (2 * 1024**3, float('inf')),      # >2GB
    })

    def get_active_memory(self) -> int:
        """
        Get current Metal active memory in bytes.

        Returns 0 if MLX unavailable (CI, non-Metal environment).
        """
        if not self._mlx_available:
            return 0
        try:
            # C1-X FIX: Use centralized get_mx() from mlx_memory SSOT
            from hledac.universal.utils.mlx_memory._core import get_mx
            mx = get_mx()
            if mx is not None and hasattr(mx, 'get_active_memory'):
                return mx.get_active_memory()
        except Exception:  # noqa: BLE001
            pass
        return 0

    def get_peak_memory(self) -> int:
        """Get peak Metal memory usage."""
        if not self._mlx_available:
            return 0
        try:
            # C1-X FIX: Use centralized get_mx() from mlx_memory SSOT
            from hledac.universal.utils.mlx_memory._core import get_mx
            mx = get_mx()
            if mx is not None and hasattr(mx, 'get_peak_memory'):
                return mx.get_peak_memory()
        except Exception:  # noqa: BLE001
            pass
        return 0

    def get_stats(self) -> MetalMemoryStats:
        """Get comprehensive Metal memory statistics."""
        active = self.get_active_memory()
        peak = self.get_peak_memory()
        tier = self.get_metal_tier(active)

        return MetalMemoryStats(
            active_bytes=active,
            active_gb=round(active / 1024**3, 3),
            peak_bytes=peak,
            peak_gb=round(peak / 1024**3, 3),
            metal_tier=tier,
            pressure_level=self._get_pressure_level(active),
        )

    def get_metal_tier_thresholds(self) -> dict[str, tuple[int, int]]:
        """Return tier threshold configuration for compatibility."""
        return self._TIER_THRESHOLDS.copy()

    def get_metal_tier(self, memory_bytes: int | None = None) -> str:
        """
        Determine Metal memory pressure tier.

        Args:
            memory_bytes: Override memory value (for testing)

        Returns:
            Tier string: "low", "medium", "high", "critical"
        """
        if memory_bytes is None:
            memory_bytes = self.get_active_memory()

        for tier, (low, high) in self._TIER_THRESHOLDS.items():
            if low <= memory_bytes < high:
                return tier
        return "low"

    def _get_pressure_level(self, memory_bytes: int) -> str:
        """Map memory to pressure level string."""
        if memory_bytes >= 2 * 1024**3:
            return "critical"
        elif memory_bytes >= 1024 * 1024**2:
            return "high"
        elif memory_bytes >= 512 * 1024**2:
            return "medium"
        return "normal"

    def is_memory_available(self, required_bytes: int) -> bool:
        """
        Check if required memory is available.

        Args:
            required_bytes: Memory requirement in bytes

        Returns:
            True if memory available within MISSION_PEAK_RSS_GIB (5.5GB)
        """
        active = self.get_active_memory()
        # SSOT: Use UmaBudget.MISSION_PEAK_RSS_GIB instead of hardcoded 5.5 GiB
        from hledac.universal.utils.uma_budget import UmaBudget
        soft_ceiling = UmaBudget.MISSION_PEAK_RSS_GIB * 1024**3  # 5.5 GiB (SSOT)
        return (active + required_bytes) <= soft_ceiling


# Singleton accessor
_device_instance: MetalDevice | None = None


def get_metal_device() -> MetalDevice:
    """Get singleton MetalDevice instance."""
    global _device_instance
    if _device_instance is None:
        _device_instance = MetalDevice()
    return _device_instance
