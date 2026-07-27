"""runtime/opsec_policy.py — OPSEC Transport Policy Engine

Sprint F202H — Advisory policy layer for transport posture.

Single read-side policy engine that:
- Prevents M1 model+renderer conflicts (model context active → JS render blocked)
- Provides concurrency/timeout hints to callers
- Acts as advisory layer (read-side) — does NOT own transport execution

Integrates with:
- transport/transport_resolver.py  (policy classification seam)
- fetching/public_fetcher.py       (M1 conflict guard + hints)
- stealth/stealth_session.py      (safe capability flags)

Bounds:
- MAX_CONCURRENT_RENDERERS: 1 (M1 single-JS-renderer constraint)
- MAX_CONCURRENT_STEALTH: 3
- All methods fail-safe — bounded clearnet fallback, never crash

No new sprint owner. asyncio.gather return_exceptions=True + _check_gathered() from utils.async_helpers.
"""
import asyncio
import threading
from dataclasses import dataclass
import msgspec
from typing import Final
_render_active_count: int = 0
_render_count_lock = threading.Lock()
_MAX_CONCURRENT_RENDERERS: Final[int] = 1
_RENDER_TIMEOUT_HINTS: dict[str, float] = {'fast': 8.0, 'standard': 15.0, 'heavy': 30.0}

class RendererPolicy(msgspec.Struct, frozen=True, gc=False):
    """Renderer capability and timeout hints."""
    allowed: bool
    max_concurrent: int
    timeout_hint: float
    blocked_reason: str | None = None

class ConcurrencyHint(msgspec.Struct, frozen=True, gc=False):
    """Concurrency hint for a transport class."""
    max_workers: int
    timeout_s: float

class TransportPolicy(msgspec.Struct, gc=False):
    """Composite transport policy."""
    renderer: RendererPolicy
    concurrency: ConcurrencyHint
    transport: str

class OPSECContext(msgspec.Struct, frozen=True, gc=False):
    """Runtime context for OPSEC policy evaluation."""
    url: str = ''
    has_model_context: bool = False
    has_stealth: bool = False
    transport_hint: str = 'clearnet'
    risk_level: str = 'medium'

def get_renderer_policy(ctx: OPSECContext) -> RendererPolicy:
    """
    Determine JS renderer policy given current runtime context.

    B6: M1 model+renderer conflict guard — if embedding context is active,
    JS renderer is BLOCKED to prevent memory exhaustion on 8GB UMA.

    Args:
        ctx: OPSECContext with current runtime state

    Returns:
        RendererPolicy with allowed flag and blocking reason if applicable
    """
    if ctx.has_model_context:
        return RendererPolicy(allowed=False, max_concurrent=0, timeout_hint=0.0, blocked_reason='M1_model_context_active')
    with _render_count_lock:
        active = _render_active_count
    if active >= _MAX_CONCURRENT_RENDERERS:
        return RendererPolicy(allowed=False, max_concurrent=_MAX_CONCURRENT_RENDERERS, timeout_hint=0.0, blocked_reason='renderer_concurrency_exhausted')
    return RendererPolicy(allowed=True, max_concurrent=_MAX_CONCURRENT_RENDERERS - active, timeout_hint=_RENDER_TIMEOUT_HINTS.get('standard', 15.0), blocked_reason=None)

def get_concurrency_hint(transport_hint: str) -> ConcurrencyHint:
    """
    Return concurrency hint for a transport class.

    Args:
        transport_hint: "clearnet", "tor", "i2p", "stealth"

    Returns:
        ConcurrencyHint with max_workers and timeout_s
    """
    if transport_hint == 'clearnet':
        return ConcurrencyHint(max_workers=3, timeout_s=35.0)
    if transport_hint == 'tor':
        return ConcurrencyHint(max_workers=2, timeout_s=45.0)
    if transport_hint == 'i2p':
        return ConcurrencyHint(max_workers=1, timeout_s=45.0)
    if transport_hint == 'stealth':
        return ConcurrencyHint(max_workers=2, timeout_s=35.0)
    return ConcurrencyHint(max_workers=2, timeout_s=35.0)

def get_transport_policy(ctx: OPSECContext) -> TransportPolicy:
    """
    Composite transport policy — combines renderer + concurrency hints.

    This is the primary entry point for callers needing both renderer
    permission and concurrency configuration.

    Args:
        ctx: OPSECContext with current runtime state

    Returns:
        TransportPolicy with renderer and concurrency components
    """
    renderer_policy = get_renderer_policy(ctx)
    transport = ctx.transport_hint
    if ctx.has_stealth:
        transport = 'stealth'
    conc_hint = get_concurrency_hint(transport)
    if not renderer_policy.allowed:
        conc_hint = ConcurrencyHint(max_workers=min(conc_hint.max_workers, 1), timeout_s=conc_hint.timeout_s * 0.5)
    return TransportPolicy(renderer=renderer_policy, concurrency=conc_hint, transport=transport)

def acquire_renderer_slot() -> bool:
    """
    Attempt to acquire a renderer slot. Returns True on success.

    Thread-safe. Fails softly if max concurrent renderers reached.
    """
    with _render_count_lock:
        global _render_active_count
        if _render_active_count < _MAX_CONCURRENT_RENDERERS:
            _render_active_count += 1
            return True
        return False

def release_renderer_slot() -> None:
    """
    Release a renderer slot. Idempotent — safe to call even if not acquired.
    """
    with _render_count_lock:
        global _render_active_count
        if _render_active_count > 0:
            _render_active_count -= 1

def get_renderer_active_count() -> int:
    """Current active renderer count (for testing/debug)."""
    with _render_count_lock:
        return _render_active_count

def get_stealth_capability_flags(has_model_context: bool) -> dict[str, bool]:
    """
    Return advisory capability flags for StealthSession configuration.

    When model context is active, certain stealth features that allocate
    additional memory should be degraded to reduce pressure.

    Args:
        has_model_context: True if embedding model is loaded

    Returns:
        dict with capability flags (all True unless model context demands reduction)
    """
    if has_model_context:
        return {'ua_rotation': True, 'jitter': True, 'tls_fingerprint': False, 'header_scramble': True}
    return {'ua_rotation': True, 'jitter': True, 'tls_fingerprint': True, 'header_scramble': True}

def get_circuit_state_hint(domain: str) -> str:
    """
    Return circuit state hint for a domain.

    F204B: Read-side circuit state query for transport diagnostics.

    Args:
        domain: Target domain (without port)

    Returns:
        "closed" | "half_open" | "open" | "unknown"
    """
    from hledac.universal.transport.circuit_breaker import get_all_breaker_states
    states = get_all_breaker_states()
    return states.get(domain, 'unknown')
__all__ = ['OPSECContext', 'RendererPolicy', 'ConcurrencyHint', 'TransportPolicy', 'get_renderer_policy', 'get_concurrency_hint', 'get_transport_policy', 'acquire_renderer_slot', 'release_renderer_slot', 'get_renderer_active_count', 'get_stealth_capability_flags', 'get_circuit_state_hint']