"""
core/config/__init__.py — Centralized configuration package.

Sprint F290: Typed configuration with validation for M1 8GB hardware profile.

Modules:
    m1_air_config  — M1AirConfig frozen dataclass with all hardware limits
    storage_config — StorageConfig msgspec.Struct for storage/paths configuration

Usage:
    from hledac.universal.core.config import M1AirConfig, M1_AIR, StorageConfig, get_storage_config

    # Hardware limits (ClassVar)
    M1AirConfig.timeout_clearnet_api  # 20.0
    M1_AIR.memory_budget_gib          # 6.0

    # Storage configuration (msgspec.Struct, frozen)
    cfg = get_storage_config()
    cfg.libc_perf_opt  # False (LIBC_PERF_OPT env var)
    cfg.is_tmp_acceptable()  # True if LIBC_PERF_OPT=1

    # Validate at startup
    M1AirConfig.validate()
"""
import msgspec


from hledac.universal.core.config.m1_air_config import M1AirConfig, M1_AIR
from hledac.universal.core.config.storage_config import StorageConfig, get_storage_config

__all__ = ["M1AirConfig", "M1_AIR", "StorageConfig", "get_storage_config"]
