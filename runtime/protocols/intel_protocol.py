"""
runtime/protocols/intel_protocol.py — F270: Intel Interface
==========================================================

Protocol for threat intelligence feeds and policy management.
Extracted from SprintScheduler's INTEL group (~4 attributes).

GHOST_INVARIANTS:
- Fail-safe: query returns [] on error
- Bounded: feed cache TTL enforced
"""
from __future__ import annotations



from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IntelProtocol(Protocol):
    """
    Threat intelligence protocol.

    Implementations:
        - CTLogClientAdapter: CT log lookups
        - PolicyManagerAdapter: OPSEC policy enforcement

    Key methods:
        - query_ct_logs: Certificate Transparency lookups
        - check_policy: OPSEC policy validation
    """

    async def query_ct_logs(
        self, domain: str
    ) -> list[dict[str, Any]]:
        """Query Certificate Transparency logs."""
        ...

    def check_policy(self, operation: str) -> bool:
        """Check if operation is allowed by OPSEC policy."""
        ...

    async def get_analyst_brief(self, query: str) -> dict[str, Any] | None:
        """Get analyst brief for query."""
        ...
