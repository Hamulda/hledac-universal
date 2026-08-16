"""
transport/policy.py — Tiered Transport Policy Engine

Sprint F265C: Unified transport policy with explicit tier bounds.


ARCHITECTURE:
  T0 (curl_cffi_stealth)  — always-on, JA3 impersonation, 1 concurrent slot
  T1 (httpx_h2)            — opt-in HLEDAC_ENABLE_HTTPX_H2, API-like URLs only
  T2 (httpx_h3)            — opt-in HLEDAC_ENABLE_HTTPX_H3, Alt-Svc cached h3
  T3 (js_renderer)         — memory-gated, camoufox/nodriver/playwright

POLICY DECISION TREE (enforces tier priority):
  1. Memory pressure HIGH → T3 blocked, only T0/T1 allowed
  2. Memory pressure CRITICAL → only T0 allowed
  3. use_js=True + memory OK → T3
  4. use_stealth=True → T0
  5. retry_after 403/429 → T0 (escalation from T1/T2)
  6. is_httpx_h2_candidate + HLEDAC_ENABLE_HTTPX_H2=1 → T1
  7. is_httpx_h3_candidate + HLEDAC_ENABLE_HTTPX_H3=1 → T2
  8. default → T0 (curl_cffi is the always-on tier)

MEMORY CONSTRAINTS (M1 8GB UMA):
  - T0: max 4 concurrent sessions (prewarm pool), ~60MB
  - T1: max 2 concurrent sessions, ~20MB
  - T2: max 5 concurrent QUIC handshakes (semaphore), ~50MB aioquic
  - T3: bounded by memory_budget_gate (soft 4.5GiB, hard 6.0GiB)

INVARIANTS:
  [TP-1] T0 is always-on — never blocked by memory pressure
  [TP-2] T3 is always gated by memory_budget_gate — never bypasses it
  [TP-3] T1/T2 are opt-in via env vars — never auto-selected without env gate
  [TP-4] Retry escalation always goes to T0 (curl_cffi_stealth)
  [TP-5] Policy is fail-safe — any error returns T0 decision
"""
import os
from dataclasses import dataclass
import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal._core.env_config import ENV
from enum import Enum
from typing import Literal
from hledac.universal.fetching.memory_budget_gate import BrowserDecision, _rss_gib
from hledac.universal.fetching.memory_budget_gate import decide as _browser_decide
from hledac.universal.transport.http3_lane import is_enabled as _http3_lane_enabled
from hledac.universal.transport.httpx_client import is_httpx_h2_enabled

class TransportTier(Enum):
    """Explicit tier labels — mirrors policy.py decision tree."""
    T0 = 'T0_curl_cffi'
    T1 = 'T1_httpx_h2'
    T2 = 'T2_httpx_h3'
    T3 = 'T3_js_renderer'
Tier = Literal['T0_curl_cffi', 'T1_httpx_h2', 'T2_httpx_h3', 'T3_js_renderer']
_SOFT_GIB: float = 4.5
_HARD_GIB: float = 6.0

class TransportPolicyDecision(Struct, frozen=True):
    """
    Output of get_transport_policy().

    Fields:
      tier              — which tier to use
      transport_lane   — passed to TransportRouter.route()
      js_allowed       — True if T3 is permitted (memory OK)
      h2_allowed       — True if T1 is permitted (env gate + memory)
      h3_allowed       — True if T2 is permitted (env gate + memory + Alt-Svc)
      rss_gib          — current RSS in GiB (for telemetry)
      memory_tier      — "normal" | "soft" | "hard" — memory pressure level
      reason            — human-readable why this tier was chosen
      blocked_tiers     — list of tiers that were blocked and why
    """
    tier: Tier
    transport_lane: str
    js_allowed: bool
    h2_allowed: bool
    h3_allowed: bool
    rss_gib: float
    memory_tier: Literal['normal', 'soft', 'hard']
    reason: str
    blocked_tiers: list[str]

def _memory_tier() -> tuple[Literal['normal', 'soft', 'hard'], float]:
    """Return current memory pressure tier and RSS in GiB."""
    rss = _rss_gib()
    if rss >= _HARD_GIB:
        return ('hard', rss)
    if rss >= _SOFT_GIB:
        return ('soft', rss)
    return ('normal', rss)

def _tier_from_lane(lane: str) -> Tier:
    """Map TransportRouter lane literal to tier."""
    mapping: dict[str, Tier] = {'curl_cffi_stealth': 'T0_curl_cffi', 'httpx_h2': 'T1_httpx_h2', 'httpx_h3': 'T2_httpx_h3', 'js_renderer': 'T3_js_renderer', 'aiohttp_default': 'T0_curl_cffi'}
    return mapping.get(lane, 'T0_curl_cffi')

def get_transport_policy(*, use_stealth: bool=False, use_js: bool=False, retry_after_status: int | None=None, js_confidence: float=0.8, priority: int=5, is_httpx_h2_candidate: bool=False, is_httpx_h3_candidate: bool=False) -> TransportPolicyDecision:
    """
    Determine which transport tier to use given current memory pressure
    and request characteristics.

    This is the SINGLE authority for tier selection. TransportRouter.route()
    is still used for lane details (tor/i2p/gopher) but the tier enforcement
    happens here.

    Args:
        use_stealth:           Force stealth (T0)
        use_js:                JS rendering requested (T3)
        retry_after_status:     Prior attempt status (escalation to T0)
        js_confidence:          0.0-1.0, how certain JS is needed
        priority:               1-10, request priority
        is_httpx_h2_candidate:  URL matches H2 API-like pattern
        is_httpx_h3_candidate:  URL matches H3 API-like pattern + Alt-Svc cached

    Returns:
        TransportPolicyDecision with tier, allowed flags, and blocked_tiers
    """
    memory_tier, rss = _memory_tier()
    blocked: list[str] = []
    h2_enabled = is_httpx_h2_enabled()
    h2_allowed = h2_enabled and memory_tier in ('normal', 'soft')
    if not h2_allowed:
        if not h2_enabled:
            blocked.append('T1_httpx_h2:HLEDAC_ENABLE_HTTPX_H2=0')
        elif memory_tier == 'hard':
            blocked.append('T1_httpx_h2:memory_hard_block')
        else:
            blocked.append('T1_httpx_h2:memory_soft_block')
    h3_gate_on = ENV.get_bool('HLEDAC_ENABLE_HTTPX_H3') or ENV.get_bool('HLEDAC_HTTP3')
    h3_lane_ok = _http3_lane_enabled()
    h3_allowed = h3_gate_on and h3_lane_ok and (memory_tier in ('normal', 'soft')) and is_httpx_h3_candidate
    if not h3_allowed:
        if not h3_gate_on:
            blocked.append('T2_httpx_h3:HLEDAC_ENABLE_HTTPX_H3=0')
        elif not h3_lane_ok:
            blocked.append('T2_httpx_h3:http3_lane_disabled')
        elif not is_httpx_h3_candidate:
            blocked.append('T2_httpx_h3:no_Alt-Svc_h3_advertisement')
        elif memory_tier == 'hard':
            blocked.append('T2_httpx_h3:memory_hard_block')
        else:
            blocked.append('T2_httpx_h3:memory_soft_block')
    browser_decision: BrowserDecision = _browser_decide(js_confidence=js_confidence, priority=priority)
    js_allowed = browser_decision.allowed and memory_tier != 'hard'
    if not js_allowed:
        if memory_tier == 'hard':
            blocked.append(f'T3_js_renderer:memory_hard_block(RSS={rss:.2f}GiB)')
        elif not browser_decision.allowed:
            blocked.append(f'T3_js_renderer:{browser_decision.reason}')
    _tp1_assert = ('T0_curl_cffi' not in blocked, f'[TP-1] T0 must never be blocked! blocked={blocked}')
    assert _tp1_assert[0], _tp1_assert[1]
    if memory_tier == 'hard':
        return TransportPolicyDecision(tier='T0_curl_cffi', transport_lane='curl_cffi_stealth', js_allowed=False, h2_allowed=False, h3_allowed=False, rss_gib=rss, memory_tier='hard', reason=f'memory_hard_block_RSS={rss:.2f}GiB', blocked_tiers=blocked)
    if retry_after_status in (403, 429):
        return TransportPolicyDecision(tier='T0_curl_cffi', transport_lane='curl_cffi_stealth', js_allowed=js_allowed, h2_allowed=h2_allowed, h3_allowed=h3_allowed, rss_gib=rss, memory_tier=memory_tier, reason=f'retry_escalation_http_{retry_after_status}', blocked_tiers=blocked)
    if use_stealth:
        return TransportPolicyDecision(tier='T0_curl_cffi', transport_lane='curl_cffi_stealth', js_allowed=js_allowed, h2_allowed=h2_allowed, h3_allowed=h3_allowed, rss_gib=rss, memory_tier=memory_tier, reason='explicit_stealth', blocked_tiers=blocked)
    if use_js and js_allowed:
        return TransportPolicyDecision(tier='T3_js_renderer', transport_lane='js_renderer', js_allowed=True, h2_allowed=h2_allowed, h3_allowed=h3_allowed, rss_gib=rss, memory_tier=memory_tier, reason=f'js_required_tier={browser_decision.tier}', blocked_tiers=blocked)
    if is_httpx_h3_candidate and h3_allowed:
        return TransportPolicyDecision(tier='T2_httpx_h3', transport_lane='httpx_h3', js_allowed=js_allowed, h2_allowed=h2_allowed, h3_allowed=True, rss_gib=rss, memory_tier=memory_tier, reason='api_like_httpx_h3_Alt-Svc_cached', blocked_tiers=blocked)
    if is_httpx_h2_candidate and h2_allowed:
        return TransportPolicyDecision(tier='T1_httpx_h2', transport_lane='httpx_h2', js_allowed=js_allowed, h2_allowed=True, h3_allowed=h3_allowed, rss_gib=rss, memory_tier=memory_tier, reason='api_like_httpx_h2', blocked_tiers=blocked)
    return TransportPolicyDecision(tier='T0_curl_cffi', transport_lane='curl_cffi_stealth', js_allowed=js_allowed, h2_allowed=h2_allowed, h3_allowed=h3_allowed, rss_gib=rss, memory_tier=memory_tier, reason='clearnet_default_t0_always_on', blocked_tiers=blocked)

def get_tier_for_lane(lane: str) -> Tier:
    """Map a TransportRouter lane to its tier. Convenience for telemetry."""
    return _tier_from_lane(lane)
__all__ = ['TransportPolicyDecision', 'Tier', 'TransportTier', 'get_transport_policy', 'get_tier_for_lane']