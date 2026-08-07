"""
brain/kv_cache_config.py — Sprint G2: KV Cache Single Source of Truth
========================================================================




Extracted from:
- DeepHermes3Engine._get_kv_cache_kwargs() + _get_adaptive_kv_bits()
- SynthesisRunner._probe_metal_memory() + _get_adaptive_kv_bits() + _get_kv_cache_kwargs()

M1 8GB invariant: kv_bits + max_kv_size are the primary knobs for staying
under the 5.5 GiB soft ceiling. Having a single implementation ensures
consistency when Metal memory pressure changes.

Usage:
    from brain.kv_cache_config import get_kv_cache_config

    config = get_kv_cache_config(
        input_tokens=512,
        max_tokens=512,
        metal_active_bytes=mx.get_active_memory(),
        uma_state=uma_status.state,
    )
    # config.kv_bits, config.max_kv_size, config.tier
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Memory Tier — shared vocabulary across brain bundle
# ---------------------------------------------------------------------------


class MemoryTier(Enum):
    """Metal memory pressure tiers (shared by all brain components)."""
    NORMAL = "normal"
    WARN = "warn"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


# ---------------------------------------------------------------------------
# KV Cache Config — immutable output of the probing logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KVCacheConfig:
    """
    Immutable KV cache configuration for MLX inference on M1 8GB.

    Single source of truth for kv_bits and max_kv_size parameters
    that go into mlx_lm.generate() — never hardcode these inline.
    """
    kv_bits: int  # 4, 6, or 8 — quantization granularity
    max_kv_size: int  # 0 = KV cache off, else token count
    tier: MemoryTier

    def as_kwargs(self) -> dict[str, Any]:
        """mlx_lm.generate() kwargs — drop max_kv_size when 0 (cache off)."""
        if self.max_kv_size == 0:
            return {"kv_bits": self.kv_bits}
        return {"kv_bits": self.kv_bits, "max_kv_size": self.max_kv_size}

    def __repr__(self) -> str:
        return f"KVCacheConfig(kv_bits={self.kv_bits}, max_kv_size={self.max_kv_size}, tier={self.tier.value})"


# ---------------------------------------------------------------------------
# Metal Tier Thresholds — extracted from SynthesisRunner + DeepHermes3Engine
# ---------------------------------------------------------------------------


def get_metal_tier_thresholds() -> tuple[int, int, int]:
    """
    Probe Rust FFI get_metal_limit_bytes_py() for dynamic M1 Metal cache ceiling.
    Fallback: static M1 8GB values from CLAUDE.md invariant.

    Returns:
        (emergency_bytes, critical_bytes, warn_bytes)
    """
    try:
        from hledac.universal.rust_extensions import rust_extensions as _rust

        limit_bytes = _rust.get_metal_limit_bytes_py()
        if limit_bytes > 0:
            return (
                int(limit_bytes * 1.75),  # emergency — 1.75× limit
                int(limit_bytes * 1.05),  # critical — at limit
                int(limit_bytes * 0.70),  # warn — 70% of limit
            )
    except Exception:  # noqa: BLE001
        pass

    # Fallback: M1 8GB static values (CLAUDE.md invariant)
    return (
        2_684_354_560,  # emergency = 2.5 GiB
        1_610_612_736,  # critical = 1.5 GiB
        1_073_741_824,  # warn = 1.0 GiB
    )


# ---------------------------------------------------------------------------
# Metal Probe — unified memory probing with TTL caching
# ---------------------------------------------------------------------------


@dataclass
class MetalProbeResult:
    """Result of a Metal memory probe."""
    active_bytes: int
    utilization_fraction: float  # active / limit
    tier: MemoryTier
    cached_at: float  # monotonic timestamp


class MetalProbe:
    """
    Unified Metal memory probing with 100ms TTL cache.

    Used by both DeepHermes3Engine and SynthesisRunner to get
    consistent memory readings without redundant mx.metal calls.

    M1 8GB invariant: probes are expensive (~1-5ms each),
    so we cache the result for 100ms to avoid repeated calls
    within the same inference batch.
    """

    __slots__ = ("_cache", "_cache_ttl_s")

    def __init__(self, cache_ttl_ms: int = 100) -> None:
        self._cache: MetalProbeResult | None = None
        self._cache_ttl_s: float = cache_ttl_ms / 1000.0

    def probe(self) -> MetalProbeResult:
        """
        Probe Metal memory — returns cached result if within TTL.

        Returns:
            MetalProbeResult with active_bytes, utilization_fraction, tier
        """
        now = time.monotonic()
        if self._cache is not None and (now - self._cache.cached_at) < self._cache_ttl_s:
            return self._cache

        active_bytes = 0
        try:
            # G2: Use centralized mlx_interface for consistency
            from brain.mlx_interface import get_mlx
            mx = get_mlx()
            if hasattr(mx.metal, "get_active_memory"):
                active_bytes = int(mx.metal.get_active_memory())
            elif hasattr(mx, "get_active_memory"):
                active_bytes = int(mx.get_active_memory())
        except Exception:  # noqa: BLE001
            pass

        emergency_bytes, critical_bytes, warn_bytes = get_metal_tier_thresholds()
        utilization = active_bytes / critical_bytes if critical_bytes > 0 else 0.0

        if active_bytes >= emergency_bytes:
            tier = MemoryTier.EMERGENCY
        elif active_bytes >= critical_bytes:
            tier = MemoryTier.CRITICAL
        elif active_bytes >= warn_bytes:
            tier = MemoryTier.WARN
        else:
            tier = MemoryTier.NORMAL

        self._cache = MetalProbeResult(
            active_bytes=active_bytes,
            utilization_fraction=utilization,
            tier=tier,
            cached_at=now,
        )
        return self._cache

    def clear_cache(self) -> None:
        """Force cache invalidation (call after model swap / unload)."""
        self._cache = None

    @property
    def active_bytes(self) -> int:
        """Cached active Metal memory in bytes."""
        return self.probe().active_bytes

    @property
    def tier(self) -> MemoryTier:
        """Cached memory tier."""
        return self.probe().tier


# ---------------------------------------------------------------------------
# UMA State probe — extracted for reuse
# ---------------------------------------------------------------------------


_UMA_STATE_CACHED: tuple[str, float, int] | None = None  # (state, monotonic, rss_gib * 100)
_UMA_CACHE_TTL_S: float = 1.0  # 1s TTL for UMA reads


def probe_uma_state() -> tuple[str, float]:
    """
    Probe UMA (Unified Memory Architecture) state with 1s TTL cache.

    Returns:
        (uma_state: str, rss_gib: float)
    """
    global _UMA_STATE_CACHED
    now = time.monotonic()

    if _UMA_STATE_CACHED is not None:
        state, ts, rss_x100 = _UMA_STATE_CACHED
        if (now - ts) < _UMA_CACHE_TTL_S:
            return state, rss_x100 / 100.0

    try:
        from hledac.universal.core.resource_governor import sample_uma_status

        status = sample_uma_status()
        state = getattr(status, "state", "ok")
        rss_gib = getattr(status, "rss_gib", 0.0)
    except Exception:
        state, rss_gib = "ok", 0.0

    _UMA_STATE_CACHED = (state, now, int(rss_gib * 100))
    return state, rss_gib


# ---------------------------------------------------------------------------
# Core factory — single entry point for KV cache config
# ---------------------------------------------------------------------------

# Default values — must match DeepHermes3Engine.__init__
_DEFAULT_KV_BITS: int = 4
_DEFAULT_MAX_KV_SIZE: int = 8192


def get_kv_cache_config(
    input_tokens: int | None = None,
    max_tokens: int | None = None,
    *,
    metal_active_bytes: int | None = None,
    uma_state: str | None = None,
    kv_bits_override: int | None = None,
) -> KVCacheConfig:
    """
    Compute KV cache configuration for mlx_lm.generate() on M1 8GB.

    This is the SINGLE SOURCE OF TRUTH for KV cache parameters.
    Replaces duplicate logic in:
    - DeepHermes3Engine._get_kv_cache_kwargs() + _get_adaptive_kv_bits()
    - SynthesisRunner._probe_metal_memory() + _get_adaptive_kv_bits()

    O1 adaptive sizing: min(input_tokens + headroom, memory_tier_cap)

    Memory-pressure tiers (Metal active memory fraction of 1.5 GiB):
      - < 0.60  → "normal"   → max_kv_size = min(input+headroom, 8192)
      - 0.60-0.80 → "warn"    → max_kv_size = min(input+headroom, 4096)
      - 0.80-0.95 → "critical" → max_kv_size = min(input+headroom, 2048)
      - > 0.95    → "emergency" → max_kv_size = 0 (KV cache off)

    Args:
        input_tokens: Number of tokens in the input prompt (after tokenization).
                      Used for O1 adaptive sizing. If None, uses 0.
        max_tokens: Maximum expected output tokens. Used for headroom.
                    If None, uses 512.
        metal_active_bytes: Override Metal active memory reading.
                             If None, probes via MetalProbe.
        uma_state: Override UMA state string.
                   If None, probes via probe_uma_state().
        kv_bits_override: Force specific kv_bits value (for LoRA, testing).
                           If None, uses metal memory tier.

    Returns:
        KVCacheConfig (immutable) with kv_bits, max_kv_size, tier

    M1 8GB invariant: never returns kv_bits < 4 (F265C-METAL).
    """
    # ── Probe memory state ────────────────────────────────────────────────
    if metal_active_bytes is None:
        metal_active_bytes = MetalProbe().active_bytes

    if uma_state is None:
        uma_state, _ = probe_uma_state()

    # ── Compute tier from Metal + UMA ────────────────────────────────────
    emergency_bytes, critical_bytes, warn_bytes = get_metal_tier_thresholds()
    metal_tier = MemoryTier.NORMAL

    if metal_active_bytes >= emergency_bytes or uma_state == "emergency":
        metal_tier = MemoryTier.EMERGENCY
    elif metal_active_bytes >= critical_bytes or uma_state == "critical":
        metal_tier = MemoryTier.CRITICAL
    elif metal_active_bytes >= warn_bytes or uma_state == "warn":
        metal_tier = MemoryTier.WARN
    else:
        metal_tier = MemoryTier.NORMAL

    # Override from env (for testing/debug)
    if os.getenv("HLEDAC_KV_QUANTIZE", "0") == "1":
        kv_bits = max(4, kv_bits_override or _DEFAULT_KV_BITS)
        tier = MemoryTier.NORMAL  # forced on, no tier reduction
        max_kv_size = _DEFAULT_MAX_KV_SIZE
        return KVCacheConfig(kv_bits=kv_bits, max_kv_size=max_kv_size, tier=tier)

    # ── Compute kv_bits from Metal tier ─────────────────────────────────
    active_gib = metal_active_bytes / (1024**3) if metal_active_bytes else 0.0
    if active_gib > 2.0:
        kv_bits = 8
    elif active_gib > 1.5:
        kv_bits = 6
    else:
        kv_bits = max(4, kv_bits_override or _DEFAULT_KV_BITS)

    # ── Override kv_bits if caller specified it ───────────────────────────
    if kv_bits_override is not None:
        kv_bits = max(4, kv_bits_override)

    # ── Compute max_kv_size from tier + O1 adaptive sizing ───────────────
    _in_tokens = input_tokens if input_tokens is not None else 0
    _max_tok = max_tokens if max_tokens is not None else 512
    _headroom = min(_max_tok, 1024)
    _min_cache = _in_tokens + _headroom  # O1: guarantees output space

    # Emergency: KV cache off
    if metal_tier == MemoryTier.EMERGENCY or uma_state == "emergency":
        return KVCacheConfig(kv_bits=kv_bits, max_kv_size=0, tier=MemoryTier.EMERGENCY)

    # Critical: aggressive reduction
    if metal_tier == MemoryTier.CRITICAL or uma_state == "critical":
        if uma_state == "critical":
            factor = 0.35 if metal_tier == MemoryTier.NORMAL else 0.2
            base = max(256, int(_DEFAULT_MAX_KV_SIZE * factor))
        else:
            factor = 0.6 if metal_tier == MemoryTier.WARN else 0.35
            base = max(256, int(_DEFAULT_MAX_KV_SIZE * factor))
        max_kv_size = max(_min_cache, base)
        return KVCacheConfig(kv_bits=kv_bits, max_kv_size=max_kv_size, tier=MemoryTier.CRITICAL)

    # Warn: moderate reduction
    if metal_tier == MemoryTier.WARN or uma_state == "warn":
        factor = 0.8 if metal_tier == MemoryTier.NORMAL else 0.5
        base = max(1024, int(_DEFAULT_MAX_KV_SIZE * factor))
        max_kv_size = max(_min_cache, base)
        return KVCacheConfig(kv_bits=kv_bits, max_kv_size=max_kv_size, tier=MemoryTier.WARN)

    # Normal: full size (O1 capped)
    base = _DEFAULT_MAX_KV_SIZE
    max_kv_size = max(_min_cache, base)
    return KVCacheConfig(kv_bits=kv_bits, max_kv_size=max_kv_size, tier=MemoryTier.NORMAL)


# ---------------------------------------------------------------------------
# Convenience — probe + config in one call for inference loops
# ---------------------------------------------------------------------------


def get_kv_cache_kwargs(
    input_tokens: int | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Convenience wrapper: probe + return mlx_lm.generate() kwargs.

    Usage:
        from mlx_lm import generate
        config = get_kv_cache_kwargs(input_tokens=512, max_tokens=512)
        result = generate(model, tokenizer, prompt=..., **config)

    Equivalent to:
        config = get_kv_cache_config(input_tokens, max_tokens)
        kwargs = config.as_kwargs()
    """
    config = get_kv_cache_config(input_tokens=input_tokens, max_tokens=max_tokens)
    return config.as_kwargs()


# ---------------------------------------------------------------------------
# Module-level probe singleton — for hot paths that can't afford allocation
# ---------------------------------------------------------------------------

_metal_probe: MetalProbe | None = None


def get_metal_probe() -> MetalProbe:
    """Get the module-level MetalProbe singleton (lazy, thread-safe enough)."""
    global _metal_probe
    if _metal_probe is None:
        _metal_probe = MetalProbe()
    return _metal_probe
