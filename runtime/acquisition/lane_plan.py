"""
from __future__ import annotations
runtime/acquisition/lane_plan.py




Acquisition lane plan structures — AcquisitionLanePlan, LaneSpec, LaneRule.
Extracted from acquisition_strategy.py (original L823-1137).

MODERNIZATION (Issue #18):
  - msgspec.Struct(frozen=True) for hot-path DTOs (AcquisitionLanePlan, LaneSpec, LaneRule)
  - AcquisitionContext stays as @dataclass (has field(default=...) which msgspec doesn't support)
  - All helper functions (_lc, _lane_rule, _disabled_reason) isolated here
"""
from __future__ import annotations
import logging
from collections.abc import Callable
from typing import Any
import msgspec
from hledac.universal.runtime.acquisition.lane_constants import AcquisitionLane, RiskLevel
from core import aclose
logger = logging.getLogger(__name__)

class LaneSpec(msgspec.Struct, frozen=True, gc=False):
    """
    Per-lane specification for acquisition planning.

    GHOST_INVARIANTS:
      - max_items is bounded [1, 10000]
      - timeout_s is bounded [1, 3600]
      - concurrency is bounded [1, 32]
    """
    lane: str
    enabled: bool
    max_items: int
    timeout_s: float
    concurrency: int
    risk_level: str

class LaneRule(msgspec.Struct, frozen=True, gc=False):
    """
    A single lane enable/disable rule with condition functions.
    """
    lane: str
    enabled: bool
    reason: str
    max_items: int
    timeout_s: float
    concurrency: int
    risk_level: str

class AcquisitionContext(msgspec.Struct, gc=False):
    """
    Shared context for lane eligibility evaluation.
    """
    query: str
    uma_state: str
    swap_detected: bool
    duration_s: float
    aggressive_mode: bool
    plan: Any = None
    transport_authority_status: Any = None
    stealth_phase: Any = None
    accepted_findings_so_far: int = 0
    branch_timeout_count: int = 0
    has_domain: bool = False
    has_ip: bool = False
    has_url: bool = False
    has_crypto: bool = False
    has_threat: bool = False
    feed_budget_active: bool = False

def _lc(lane: str, base: int, uma_state: str) -> int:
    """
    Adjust base concurrency based on lane name and UMA state.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: returns 1-32
    """
    heavy_lanes = {'CT', 'WAYBACK', 'PASSIVE_DNS', 'BLOCKCHAIN'}
    if lane in heavy_lanes and uma_state in ('warn', 'critical'):
        base = max(1, base // 2)
    light_lanes = {'PUBLIC', 'FEED', 'PIVOT_EXECUTOR'}
    if lane in light_lanes:
        base = base
    return max(1, min(32, base))

def _lane_rule(lane: str, spec: LaneSpec, ctx: AcquisitionContext, enabled_fn: Callable[[AcquisitionContext], bool], reason_fn: Callable[[AcquisitionContext], str], conc_fn: Callable[[AcquisitionContext], int]) -> LaneRule:
    """
    Build a LaneRule from a LaneSpec + condition functions.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    return LaneRule(lane=lane, enabled=spec.enabled and enabled_fn(ctx), reason=reason_fn(ctx), max_items=spec.max_items, timeout_s=spec.timeout_s, concurrency=conc_fn(ctx), risk_level=spec.risk_level)

def _disabled_reason(lane: str, ctx: AcquisitionContext) -> str:
    """
    Compute why a lane is disabled (for reporting).

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    if ctx.uma_state == 'emergency':
        return f'UMA emergency, {lane} disabled'
    if ctx.uma_state == 'critical':
        if lane in {'CT', 'WAYBACK', 'PASSIVE_DNS', 'BLOCKCHAIN', 'IPFS'}:
            return f'UMA critical, {lane} disabled'
    if ctx.swap_detected:
        if lane in {'CT', 'BLOCKCHAIN', 'IPFS'}:
            return f'swap detected, {lane} disabled'
    if ctx.branch_timeout_count >= 3:
        return f'branch timeouts ({ctx.branch_timeout_count}), {lane} disabled'
    return f'{lane} disabled'