"""
runtime/protocols/layers_protocol.py — F270: Layers Interface
============================================================

Protocol for security/privacy/stealth layer management.
Extracted from SprintScheduler's LAYERS group (~5 attributes).

GHOST_INVARIANTS:
- Fail-safe: all layers return Passthrough on error
- No blocking ops in async context
"""



from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LayersProtocol(Protocol):
    """
    Security layers protocol.

    Implementations:
        - LayerManagerAdapter: wraps LayerManager

    Key methods:
        - apply_privacy: privacy policy enforcement
        - check_stealth: stealth mode validation
    """

    async def apply_privacy(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply privacy policy to data."""
        ...

    def check_stealth(self, url: str) -> bool:
        """Check if URL passes stealth mode."""
        ...

    async def enforce_layers(self, finding: Any) -> Any:
        """Enforce all active security layers."""
        ...
