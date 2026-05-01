"""
Provider Stats — EWMA-tracked discovery provider statistics.

Sprint F206AQ: Budget-Aware Multi-Source Expansion

Tracks per-provider:
  - success / fail / timeout counts
  - avg latency, hits, unique hosts
  - EWMA reliability and novelty scores
  - last error type

Persistence: DuckDB (canonical) or JSON snapshot, env-gated.

No ML hot path. Pure Python. M1-safe.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# EWMA decay factor — controls how fast trust decays
# ---------------------------------------------------------------------------
_EWMA_ALPHA = 0.3  # 30% weight on new observation

# Bounds
_MAX_ERRORS_STORED = 50
_MIN_RELIABILITY = 0.01


# ---------------------------------------------------------------------------
# Data record
# ---------------------------------------------------------------------------

@dataclass
class ProviderStats:
    """
    Per-provider EWMA statistics.

    reliability_ewma : [0, 1] — success rate EWMA
    novelty_ewma    : [0, 1] — hit uniqueness EWMA (1 = all new hosts)
    cost_ewma       : ms — expected latency EWMA
    """
    name: str

    success_count: int = 0
    fail_count: int = 0
    timeout_count: int = 0

    total_latency_ms: float = 0.0
    total_hits: int = 0
    total_unique_hits: int = 0

    last_error_type: str | None = None
    last_error_time: float | None = None

    reliability_ewma: float = 1.0
    novelty_ewma: float = 1.0
    cost_ewma: float = 2000.0  # ms — default assumption

    call_count: int = 0
    last_call_time: float | None = None

    # Sprint F206X: deque with maxlen for O(1) eviction instead of list.pop(0) O(n)
    recent_errors: deque = field(default_factory=lambda: deque(maxlen=_MAX_ERRORS_STORED))

    # Snapshot version for migration safety
    _version: int = 1

    # -------------------------------------------------------------------------
    # Score — higher = better candidate for selection
    # -------------------------------------------------------------------------

    def score(self) -> float:
        """
        Composite score: reliability * novelty / cost.

        Returns 0 if cost_ewma is 0 (avoid div-zero).
        """
        if self.cost_ewma <= 0:
            return 0.0
        return (self.reliability_ewma * self.novelty_ewma) / self.cost_ewma * 1000.0

    # -------------------------------------------------------------------------
    # Update from a call result
    # -------------------------------------------------------------------------

    def record_success(
        self,
        latency_ms: float,
        hits: tuple[Any, ...],
        unique_hosts: int,
    ) -> None:
        """Record a successful call."""
        self.success_count += 1
        self.call_count += 1
        self._update_latency(latency_ms)
        self._update_reliability(success=True)
        self._update_novelty(hits, unique_hosts)
        self.last_call_time = time.monotonic()
        self.last_error_type = None

    def record_failure(self, error_type: str) -> None:
        """Record a failed call."""
        self.fail_count += 1
        self.call_count += 1
        self._update_reliability(success=False)
        self.last_error_type = error_type
        self.last_error_time = time.monotonic()
        # Sprint F206X: deque(maxlen=N) auto-evicts oldest on append - O(1)
        self.recent_errors.append(error_type)

    def record_timeout(self) -> None:
        """Record a timeout."""
        self.timeout_count += 1
        self.call_count += 1
        self._update_reliability(success=False)
        self.last_error_type = "timeout"
        self.last_error_time = time.monotonic()
        # Sprint F206X: deque(maxlen=N) auto-evicts oldest on append - O(1)
        self.recent_errors.append("timeout")

    # -------------------------------------------------------------------------
    # EWMA helpers
    # -------------------------------------------------------------------------

    def _update_latency(self, latency_ms: float) -> None:
        self.total_latency_ms += latency_ms
        count = self.success_count
        if count <= 1:
            self.cost_ewma = latency_ms
        else:
            self.cost_ewma = _EWMA_ALPHA * latency_ms + (1 - _EWMA_ALPHA) * self.cost_ewma

    def _update_reliability(self, success: bool) -> None:
        if success:
            # bump toward 1
            self.reliability_ewma = _EWMA_ALPHA * 1.0 + (1 - _EWMA_ALPHA) * self.reliability_ewma
        else:
            # decay toward 0
            self.reliability_ewma = max(_MIN_RELIABILITY, _EWMA_ALPHA * 0.0 + (1 - _EWMA_ALPHA) * self.reliability_ewma)

    def _update_novelty(self, hits: tuple[Any, ...], unique_hosts: int) -> None:
        """Novelty EWMA: per-call uniqueness ratio, decayed exponentially."""
        self.total_hits += len(hits)
        self.total_unique_hits += unique_hosts
        count = self.success_count
        if count <= 1:
            self.novelty_ewma = 1.0
        else:
            # Per-call uniqueness ratio for this call
            call_unique_ratio = unique_hosts / max(len(hits), 1)
            # EWMA update using same alpha as reliability
            self.novelty_ewma = _EWMA_ALPHA * call_unique_ratio + (1 - _EWMA_ALPHA) * self.novelty_ewma
            self.novelty_ewma = max(0.0, min(1.0, self.novelty_ewma))

    # -------------------------------------------------------------------------
    # Serialisation
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "timeout_count": self.timeout_count,
            "total_latency_ms": self.total_latency_ms,
            "total_hits": self.total_hits,
            "total_unique_hits": self.total_unique_hits,
            "last_error_type": self.last_error_type,
            "last_error_time": self.last_error_time,
            "reliability_ewma": self.reliability_ewma,
            "novelty_ewma": self.novelty_ewma,
            "cost_ewma": self.cost_ewma,
            "call_count": self.call_count,
            "last_call_time": self.last_call_time,
            "recent_errors": self.recent_errors,
            "_version": self._version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderStats:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    # -------------------------------------------------------------------------
    # Computed accessors
    # -------------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """Provider is worth trying (reliability above minimum)."""
        return self.reliability_ewma >= _MIN_RELIABILITY

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count + self.timeout_count
        if total == 0:
            return 1.0
        return self.success_count / total


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_NAMES: tuple[str, ...] = (
    "ddg_mojeek",
    "historical_frontier",
    "wayback_cdx",
    "feed_pivots",
    "ct_pivots",
    "commoncrawl_cdx",
)

# Default cost estimates (ms) — used for cold-start EWMA initialisation
PROVIDER_COST_ESTIMATE: dict[str, float] = {
    "ddg_mojeek": 800.0,
    "historical_frontier": 500.0,
    "wayback_cdx": 600.0,
    "feed_pivots": 300.0,
    "ct_pivots": 400.0,
    "commoncrawl_cdx": 1200.0,
}

# ---------------------------------------------------------------------------
# Provider capability metadata — controls which providers are production-safe
# ---------------------------------------------------------------------------

PROVIDER_CAPABILITIES: dict[str, dict[str, object]] = {
    "ddg_mojeek": {
        "production_enabled": True,
        "is_stub": False,
        "requires_context": False,
    },
    "historical_frontier": {
        "production_enabled": True,
        "is_stub": False,
        "requires_context": False,
    },
    "wayback_cdx": {
        "production_enabled": True,
        "is_stub": False,
        "requires_context": False,
    },
    "commoncrawl_cdx": {
        "production_enabled": False,
        "is_stub": True,
        "requires_context": False,
        "disabled_reason": "adapter_not_implemented",
    },
    "feed_pivots": {
        "production_enabled": False,
        "is_stub": True,
        "requires_context": True,
        "disabled_reason": "pipeline_context_not_wired",
    },
    "ct_pivots": {
        "production_enabled": True,
        "is_stub": False,
        "requires_context": False,
        "enabled_reason": "F206AU_crtsh_adapter",
    },
}


def is_production_provider(name: str) -> bool:
    """True if provider is production-enabled (not a quarantined stub)."""
    cap = PROVIDER_CAPABILITIES.get(name)
    if cap is None:
        return False
    return bool(cap.get("production_enabled", False))


def is_stub_provider(name: str) -> bool:
    """True if provider is explicitly marked as stub/not-implemented."""
    cap = PROVIDER_CAPABILITIES.get(name)
    if cap is None:
        return False
    return bool(cap.get("is_stub", False))


class ProviderStatsRegistry:
    """
    Thread-safe(ish) registry of ProviderStats, one per provider.

    In-process only — persistence via to_json / from_json.
    """

    def __init__(self) -> None:
        self._stats: dict[str, ProviderStats] = {}
        for name in PROVIDER_NAMES:
            self._stats[name] = ProviderStats(
                name=name,
                cost_ewma=PROVIDER_COST_ESTIMATE.get(name, 1000.0),
            )
        self._last_save_time: float | None = None
        self._snapshot_path: Path | None = None

    # -------------------------------------------------------------------------
    # Access
    # -------------------------------------------------------------------------

    def get(self, name: str) -> ProviderStats | None:
        return self._stats.get(name)

    def all_stats(self) -> dict[str, ProviderStats]:
        return dict(self._stats)

    def providers(self) -> list[str]:
        return list(self._stats.keys())

    # -------------------------------------------------------------------------
    # Record outcomes
    # -------------------------------------------------------------------------

    def record_success(
        self,
        provider: str,
        latency_ms: float,
        hits: tuple[Any, ...] = (),
        unique_hosts: int = 0,
    ) -> None:
        stats = self._stats.get(provider)
        if stats is None:
            stats = ProviderStats(name=provider)
            self._stats[provider] = stats
        stats.record_success(latency_ms, hits, unique_hosts)

    def record_failure(self, provider: str, error_type: str = "unknown") -> None:
        stats = self._stats.get(provider)
        if stats is None:
            stats = ProviderStats(name=provider)
            self._stats[provider] = stats
        stats.record_failure(error_type)

    def record_timeout(self, provider: str) -> None:
        stats = self._stats.get(provider)
        if stats is None:
            stats = ProviderStats(name=provider)
            self._stats[provider] = stats
        stats.record_timeout()

    # -------------------------------------------------------------------------
    # Persistence (env-gated)
    # -------------------------------------------------------------------------

    def _snapshot_path_for_env(self) -> Path | None:
        path_str = os.environ.get("HLEDAC_DISCOVERY_STATS_PATH", "").strip()
        if not path_str:
            return None
        return Path(path_str)

    def to_json(self, path: Path | None = None) -> None:
        """Persist all stats to JSON snapshot."""
        if path is None:
            path = self._snapshot_path_for_env()
        if path is None:
            return
        payload = {
            name: stats.to_dict()
            for name, stats in self._stats.items()
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(path)
        self._snapshot_path = path
        self._last_save_time = time.monotonic()

    def from_json(self, path: Path | None = None) -> None:
        """Load stats from JSON snapshot. Silently succeeds if file absent."""
        if path is None:
            path = self._snapshot_path_for_env()
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text())
            for name, d in payload.items():
                self._stats[name] = ProviderStats.from_dict(d)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Corrupt file — start fresh, don't propagate
            pass

    # -------------------------------------------------------------------------
    # Bulk operations
    # -------------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all stats and reinitialise with defaults."""
        self._stats.clear()
        for name in PROVIDER_NAMES:
            self._stats[name] = ProviderStats(
                name=name,
                cost_ewma=PROVIDER_COST_ESTIMATE.get(name, 1000.0),
            )

    def top_k(self, k: int = 3) -> list[ProviderStats]:
        """Return top-k providers by composite score."""
        sorted_providers = sorted(
            self._stats.values(),
            key=lambda s: s.score(),
            reverse=True,
        )
        return sorted_providers[:k]

    def healthy_providers(self) -> list[ProviderStats]:
        """Return providers with reliability_ewma above minimum."""
        return [s for s in self._stats.values() if s.is_healthy]


# ---------------------------------------------------------------------------
# Global singleton (lazily initialised)
# ---------------------------------------------------------------------------

_registry: ProviderStatsRegistry | None = None


def get_provider_stats_registry() -> ProviderStatsRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderStatsRegistry()
    return _registry