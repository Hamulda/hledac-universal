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
"""

from __future__ import annotations

import json as _json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

logger = logging.getLogger(__name__)

# Env gate
import os
_ENABLING_CONSISTENCY_VERIFIER = (
    os.environ.get("HLEDAC_ENABLE_CONSISTENCY_VERIFIER", "1").lower()
    in ("1", "true", "yes", "on")
)


class _RustConsistencyDomain:
    """Rust-accelerated consistency verification domain."""

    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

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
        try:
            # Serialize findings to JSON
            findings_json = _json.dumps(findings).encode("utf-8")

            # Call Rust function
            result_bytes = self._ext.check_finding_consistency(
                findings_json, max_findings
            )

            # Deserialize result
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
        return _python_fallback_check_consistency(findings[:max_findings])

    def quick_consistency_check(
        self, entity: str, attribute: str, values: list[dict[str, str]]
    ) -> float:
        """Pure-Python fallback for quick consistency check."""
        return _python_fallback_quick_check(entity, attribute, values)


def _python_fallback_check_consistency(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Pure-Python fallback for consistency verification.

    Implements basic contradiction detection without tri-source voting or
    sophisticated entity normalization.
    """
    if not findings:
        return {
            "clean": [],
            "contradictory": [],
            "disputed": [],
            "contradictions": [],
            "suspect_sources": [],
            "entity_scores": {},
            "consistency_score": 1.0,
            "facts_processed": 0,
            "contradictions_found": 0,
        }

    clean: list[dict[str, Any]] = []
    contradictory: list[dict[str, Any]] = []
    entity_scores: dict[str, float] = {}
    contradictions: list[dict[str, Any]] = []

    # Group findings by (entity, attribute)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in findings:
        entity = f.get("value") or f.get("ioc") or f.get("entity_value", "")
        attribute = f.get("ioc_type") or f.get("attribute") or f.get("type", "unknown")
        if not entity:
            continue
        key = (entity, attribute)
        if key not in groups:
            groups[key] = []
        groups[key].append(f)

    # Check each group for contradictions
    for (entity, attribute), group_facts in groups.items():
        if len(group_facts) < 2:
            # Single source — no contradiction possible
            clean.extend(group_facts)
            entity_scores[entity] = 1.0
            continue

        # Extract values per source
        source_values: dict[str, set[str]] = {}
        for f in group_facts:
            source = f.get("source_type") or f.get("source", "unknown")
            value = f.get("value") or f.get("ioc") or entity
            if source not in source_values:
                source_values[source] = set()
            source_values[source].add(value)

        # Check for contradictions (different values from different sources)
        all_values = set()
        for values in source_values.values():
            all_values.update(values)

        if len(all_values) > 1:
            # Contradiction detected
            severity = 0.7  # Default severity
            if attribute in ("ip", "ipv4"):
                severity = 0.8
            elif attribute in ("sha256", "hash"):
                severity = 0.9

            contradictions.append({
                "entity": entity,
                "attribute": attribute,
                "contradiction_type": "source_conflict",
                "severity": severity,
                "claim_a": list(all_values)[0],
                "claim_b": list(all_values)[1] if len(all_values) > 1 else "",
                "source_a": "multiple",
                "source_b": "multiple",
                "resolution_hint": f"Multiple sources claim different values for {entity}: {all_values}",
            })

            # Mark all facts as contradictory
            for f in group_facts:
                contradictory.append({
                    **f,
                    "consistency_score": 1.0 - severity,
                    "contradiction_type": "source_conflict",
                    "severity": severity,
                    "conflicting_value": list(all_values - {f.get("value") or f.get("ioc")})[0] if len(all_values) > 1 else "",
                    "conflicting_source": "other_source",
                })

            entity_scores[entity] = 1.0 - severity
        else:
            # All sources agree
            clean.extend(group_facts)
            entity_scores[entity] = 1.0

    # Compute batch consistency score
    total = len(findings)
    if total > 0:
        clean_count = len(clean)
        contradictory_count = len(contradictory)
        consistency_score = (clean_count - contradictory_count * 0.3) / total
        consistency_score = max(0.0, min(1.0, consistency_score))
    else:
        consistency_score = 1.0

    return {
        "clean": clean,
        "contradictory": contradictory,
        "disputed": [],
        "contradictions": contradictions,
        "suspect_sources": [],
        "entity_scores": entity_scores,
        "consistency_score": consistency_score,
        "facts_processed": len(findings),
        "contradictions_found": len(contradictions),
    }


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
