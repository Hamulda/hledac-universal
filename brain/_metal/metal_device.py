"""
metal_device.py — Metal GPU Device Abstraction
==============================================



PEP 698: Extracted from DeepHermes3Engine._get_gpu_memory, _get_metal_tier_thresholds
M1 8GB UMA-aware memory management.

PEP 749 (import self) patterns for clean circular import resolution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

# Module-level state
_MLX_AVAILABLE_GLOBAL = False


def _check_mlx_availability() -> bool:
    """Check MLX availability at module load time (lazy)."""
    global _MLX_AVAILABLE_GLOBAL
    if not _MLX_AVAILABLE_GLOBAL:
        try:
            import mlx.core as _mx
            _ = _mx.metal.get_active_memory
            _MLX_AVAILABLE_GLOBAL = True
        except Exception:
            _MLX_AVAILABLE_GLOBAL = False
    return _MLX_AVAILABLE_GLOBAL


@dataclass(frozen=True)
class MetalMemoryStats:
    """Immutable snapshot of Metal memory state."""
    active_bytes: int = 0
    active_gb: float = 0.0
    peak_bytes: int = 0
    peak_gb: float = 0.0
    metal_tier: str = "unknown"
    pressure_level: str = "normal"


@dataclass
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
    _mlx_available: bool = field(default_factory=_check_mlx_availability)

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
            import mlx.core as mx
            if hasattr(mx, 'get_active_memory'):
                return mx.get_active_memory()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        return 0

    def get_peak_memory(self) -> int:
        """Get peak Metal memory usage."""
        if not self._mlx_available:
            return 0
        try:
            import mlx.core as mx
            if hasattr(mx, 'get_peak_memory'):
                return mx.get_peak_memory()  # type: ignore[union-attr]
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
            True if memory available within soft ceiling (5.5GB)
        """
        active = self.get_active_memory()
        soft_ceiling = 5.5 * 1024**3  # 5.5 GiB per M1 8GB UMA budget
        return (active + required_bytes) <= soft_ceiling


# Singleton accessor
_device_instance: MetalDevice | None = None


def get_metal_device() -> MetalDevice:
    """Get singleton MetalDevice instance."""
    global _device_instance
    if _device_instance is None:
        _device_instance = MetalDevice()
    return _device_instance
