"""
core/config/__init__.py — Centralized configuration package.

Sprint F290: Typed configuration with validation for M1 8GB hardware profile.

Modules:
    m1_air_config — M1AirConfig frozen dataclass with all hardware limits

Usage:
    from core.config import M1AirConfig, M1_AIR

    # Access ClassVar limits
    M1AirConfig.timeout_clearnet_api  # 20.0
    M1_AIR.memory_budget_gib          # 6.0

    # Validate at startup
    M1AirConfig.validate()
"""

from core.config.m1_air_config import M1AirConfig, M1_AIR

__all__ = ["M1AirConfig", "M1_AIR"]
