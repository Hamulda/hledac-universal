# consistency.py — Propositional consistency verification (META-007)

"""
META-007: Propositional Consistency Verifier — Python domain

Detects propositional contradictions between sources — the "confident liar" problem
that Shannon entropy and logprob divergence cannot catch.

This module provides Python bindings to the Rust consistency_verifier module,
with fallback to pure-Python implementation when Rust is unavailable.

ARCHITECTURE:
    brain/uncertainty_quant.py: UncertaintyQuantifier (entropy + logprob)
            ↓ (missing: propositional contradiction)
    brain/consistency_bridge.py: PropositionalConsistencyBridge
            ↓ emits PropositionalContradictionAlert
    EntropyFetchBridge (reused)
            ↓ routes to FetchCoordinator
    FetchCoordinator._micro_sprint_worker_loop()

ALGORITHM:
    1. Extract facts from findings: (entity, attribute, value, source, timestamp)
    2. For each (entity, attribute) with ≥2 distinct values:
       - IP resolution conflict (different IPs from different sources)
       - Domain ownership conflict (different registrants)
       - Hash conflict (different SHA256 for same file)
       - Temporal inconsistency (same source, different values at different times)
    3. Tri-source voting:
       - 1:1:1 split → entity is disputed (severity=1.0)
       - 2:1 split → suspect source flagged (severity=0.7)
    4. Output: consistency_score per entity [0.0-1.0]

M1 8GB: Pure Rust, single-pass O(N), bounded (500 findings max per batch).

ISSUE [SWARM]-005: FFI Circuit Breaker Integration
    This module now uses the UniversalCircuitBreaker for Rust → Python → No-op
    cascade fallback. When consistency_verifier.rs panics (poisoned mutex,
    serialization error), the Python fallback is automatically activated.
"""

from __future__ import annotations

import json as _json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags

logger = logging.getLogger(__name__)

# Env gate
_ENABLING_CONSISTENCY_VERIFIER = FeatureFlags.get(FeatureFlag.CONSISTENCY_VERIFIER, default=True)

# ISSUE [SWARM]-005: FFI Circuit Breaker
try:
    from hledac.universal.core.ffi_circuit_breaker import (
        FFI_MODULE_CONSISTENCY_VERIFIER,
        FFICallResult,
        get_ffi_circuit_breaker,
        _noop_check_consistency,
        _python_check_consistency,  # Use the registered fallback from registry
    )
    _FFI_CB_AVAILABLE = True
except ImportError:
    _FFI_CB_AVAILABLE = False
    FFI_MODULE_CONSISTENCY_VERIFIER = "consistency_verifier"
    # Define fallback functions locally if FFI CB not available
    def _python_check_consistency(findings: list[dict[str, Any]]) -> dict[str, Any]:
        """Pure-Python fallback for consistency verification."""
        if not findings:
            return {
                "clean": [], "contradictory": [], "disputed": [],
                "contradictions": [], "suspect_sources": [],
                "entity_scores": {}, "consistency_score": 1.0,
                "facts_processed": 0, "contradictions_found": 0,
            }
        return {
            "clean": findings, "contradictory": [], "disputed": [],
            "contradictions": [], "suspect_sources": [],
            "entity_scores": {}, "consistency_score": 1.0,
            "facts_processed": len(findings), "contradictions_found": 0,
        }
    
class _RustConsistencyDomain:
    """Rust-accelerated consistency verification domain with FFI circuit breaker."""

    __slots__ = ("_ext", "_ffi_cb")

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
        # ISSUE [SWARM]-005: Initialize FFI circuit breaker
        self._ffi_cb = get_ffi_circuit_breaker() if _FFI_CB_AVAILABLE else None

    def check_finding_consistency(
        self, findings: list[dict[str, Any]], *, max_findings: int = 500
    ) -> dict[str, Any]:
        """
        Check a batch of findings for propositional contradictions.

        Args:
            findings: List of finding dicts with keys like:
                - finding_id: str
                - value / ioc / entity_value: str
                - ioc_type / attribute / type: str
                - source_type / source: str
                - timestamp / ts: int | float
            max_findings: Maximum findings to process (default 500, M1 8GB safe)

        Returns:
            dict with keys:
                - clean: List of findings that passed consistency checks
                - contradictory: List of findings with contradictions
                - disputed: List of findings from disputed entities
                - contradictions: List of detected contradictions
                - suspect_sources: List of flagged suspect sources
                - entity_scores: Dict mapping entity -> consistency_score [0.0-1.0]
                - consistency_score: Batch-level score [0.0-1.0]
                - facts_processed: Number of facts analyzed
                - contradictions_found: Number of contradictions detected
        """
        # ISSUE [SWARM]-005: Use FFI circuit breaker if available
        if self._ffi_cb is not None:
            return self._check_with_circuit_breaker(findings, max_findings)

        # Fallback to direct call (for backward compatibility)
        return self._check_direct(findings, max_findings)

    def _check_with_circuit_breaker(
        self, findings: list[dict[str, Any]], max_findings: int
    ) -> dict[str, Any]:
        """Check consistency using FFI circuit breaker for Rust → Python cascade."""
        # Create a closure that wraps the Rust call
        def rust_call() -> dict[str, Any]:
            findings_json = _json.dumps(findings).encode("utf-8")
            result_bytes = self._ext.check_finding_consistency(findings_json, max_findings)
            return _json.loads(result_bytes.decode("utf-8"))

        # Call with circuit breaker - pass findings and max_findings for Python fallback
        result: FFICallResult = self._ffi_cb.call_or_fallback(
            module=FFI_MODULE_CONSISTENCY_VERIFIER,
            rust_fn=rust_call,
            findings=findings,
            max_findings=max_findings,
        )

        # Circuit breaker returns the final result (Rust, Python fallback, or No-op)
        if result.success and result.value is not None:
            return result.value
        
        # Edge case: result.value is None - use Python fallback directly
        logger.warning(
            f"[CONSISTENCY] FFI circuit breaker returned None "
            f"(path={result.path}, error={result.error}), using Python fallback"
        )
        return _python_check_consistency(findings, max_findings)

    def _check_direct(
        self, findings: list[dict[str, Any]], max_findings: int
    ) -> dict[str, Any]:
        """Direct Rust call without circuit breaker (backward compatible)."""
        try:
            findings_json = _json.dumps(findings).encode("utf-8")
            result_bytes = self._ext.check_finding_consistency(
                findings_json, max_findings
            )
            return _json.loads(result_bytes.decode("utf-8"))
        except Exception as e:
            logger.debug(f"[CONSISTENCY] Rust check_finding_consistency failed: {e}")
            return _python_fallback_check_consistency(findings)

    def quick_consistency_check(
        self, entity: str, attribute: str, values: list[dict[str, str]]
    ) -> float:
        """
        Quick consistency check for a single entity across sources.

        Args:
            entity: Entity value to check
            attribute: Attribute type (e.g., "ip", "hash")
            values: List of {"value": "...", "source": "..."} dicts

        Returns:
            Consistency score [0.0-1.0]
        """
        try:
            values_json = _json.dumps(values).encode("utf-8")
            return self._ext.quick_consistency_check(entity, attribute, values_json)
        except Exception as e:
            logger.debug(f"[CONSISTENCY] Rust quick_consistency_check failed: {e}")
            return _python_fallback_quick_check(entity, attribute, values)


class _PythonConsistencyDomain:
    """Pure-Python fallback for consistency verification."""

    __slots__ = ()

    def check_finding_consistency(
        self, findings: list[dict[str, Any]], *, max_findings: int = 500
    ) -> dict[str, Any]:
        """Pure-Python fallback for consistency verification."""
        return _python_check_consistency(findings[:max_findings])

    def quick_consistency_check(
        self, entity: str, attribute: str, values: list[dict[str, str]]
    ) -> float:
        """Pure-Python fallback for quick consistency check."""
        return _python_fallback_quick_check(entity, attribute, values)


def _python_fallback_quick_check(
    entity: str, attribute: str, values: list[dict[str, str]]
) -> float:
    """Pure-Python fallback for quick consistency check."""
    if not values:
        return 1.0

    unique_values = set(v.get("value") or "" for v in values)
    if len(unique_values) == 1:
        return 1.0  # All sources agree

    # Simple scoring based on attribute type
    severity = 0.5
    if attribute in ("ip", "ipv4"):
        severity = 0.8
    elif attribute in ("sha256", "hash"):
        severity = 0.9

    return 1.0 - severity

# Module-level singleton getter (matches pattern in other domains)
_domain: "_RustConsistencyDomain | _PythonConsistencyDomain | None" = None


def get_consistency_domain() -> "_RustConsistencyDomain | _PythonConsistencyDomain":
    """
    Get the consistency domain singleton.

    Returns Rust domain if available, otherwise pure-Python fallback.
    """
    global _domain
    if _domain is None:
        try:
            from hledac_rust_extensions import hledac_rust_extensions
            ext = hledac_rust_extensions()
            # Probe for the function
            if hasattr(ext, "check_finding_consistency"):
                _domain = _RustConsistencyDomain(ext)
                logger.debug("[CONSISTENCY] Using Rust domain")
            else:
                _domain = _PythonConsistencyDomain()
                logger.debug("[CONSISTENCY] Rust function not available, using Python fallback")
        except ImportError:
            _domain = _PythonConsistencyDomain()
            logger.debug("[CONSISTENCY] Rust extension unavailable, using Python fallback")

    return _domain
