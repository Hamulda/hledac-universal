"""
runtime/protocols/enrichment_protocol.py — F270: Enrichment Interface
===================================================================

Protocol for enrichment service orchestration.
Extracted from SprintScheduler's ENRICHMENT group (~2 attributes).

GHOST_INVARIANTS:
- Fail-safe: enrich returns {} on error
- Bounded: evidence_log size limited
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EnrichmentProtocol(Protocol):
    """
    Enrichment services protocol.

    Implementations:
        - EnrichmentServicesAdapter: wraps enrichment services

    Key methods:
        - enrich_finding: add external context to finding
        - log_evidence: record evidence for audit
    """

    async def enrich_finding(self, finding: Any, enrichment_types: list[str]) -> dict[str, Any]:
        """Enrich finding with external data."""
        ...

    async def log_evidence(self, finding: Any, evidence: dict[str, Any]) -> None:
        """Log evidence for forensic audit."""
        ...
