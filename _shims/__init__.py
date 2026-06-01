"""
hledac.universal._shims — Shim package for stub implementations

This package contains stub implementations that provide interface compatibility
without depending on real implementations that may have circular import issues.

DO NOT add logic here — stub-only exports.
"""
from __future__ import annotations

__all__ = [
    # Core stubs
    "core_resilience",
    "core_watchdog",
    "core_http",
    "core_mlx_embeddings",
    "core_unified_ai_orchestrator",
    "cortex_director",
    # Security stubs
    "security_key_manager",
    "security_quantum_resistant_crypto",
    "security_stealth_engine",
    "security_temporal_anonymizer",
    "security_threat_intelligence",
    "security_zero_attribution_engine",
    "security_zkp_research_engine",
]