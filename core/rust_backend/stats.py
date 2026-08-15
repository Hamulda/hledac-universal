# core/rust_backend/stats.py
"""
StatCollector — explicit stat registry for Rust extension telemetry.


Replaces the hasattr/try-except pattern in sprint_entrypoint._get_rust_stats()
with a single registry + contextlib.suppress() pass.

Registry-driven: the available stats are documented in one place.
No hasattr calls, no bare except: pass, no exception object allocation on miss.

Usage:
    from hledac.universal.core.rust_backend.stats import StatCollector
    stats = StatCollector().collect(hledac_rust_extensions)
"""
from __future__ import annotations

__all__ = ["StatCollector"]

import contextlib
from typing import Any
from core._util import aclose

# Registry entry: (attribute_name, optional_callable_invoker_or_None)
#   invoker is None         → call attribute as no-arg callable (property or fn)
#   invoker is callable     → call invoker(fn) to produce the value
_StatEntry = tuple[str, Any | None]


class StatCollector:
    """
    Typed stat registry — no hasattr, no bare excepts.

    Registry format:
        stat_key → (attribute_name, invoker_fn_or_None)
        invoker is None  → call attribute as a no-arg callable
        invoker is set   → call invoker(fn) to produce the value

    The invoker receives the raw callable and returns the stat value.
    """

    __slots__ = ("_registry",)

    def __init__(self) -> None:
        self._registry: dict[str, _StatEntry] = {
            # TelemetryAggregator — real-time counters/histograms/gauges
            "telemetry": ("create_telemetry_aggregator", lambda fn: fn().snapshot()),
            # Memory probe stats — no-arg callables
            "process_rss_gib": ("get_process_rss_gib", None),
            "available_memory_gib": ("get_available_memory_gib", None),
            "memory_pressure_level": ("memory_pressure_level", None),
            # Adaptive scheduler state — no-arg callables
            # (Rust takes no args even though .pyi erroneously shows memory_pressure: int)
            "adaptive_cpu_threads": ("get_adaptive_cpu_threads", None),
            "adaptive_io_threads": ("get_adaptive_io_threads", None),
            # Metal availability — no-arg callable
            "metal_available": ("check_metal_availability", None),
        }

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def collect(self, mod: Any) -> dict[str, Any]:
        """
        Collect all available stats from a Rust extension module.

        Fails softly — any error returns the stats gathered so far.
        Missing attributes are silently skipped.
        Version is stored only if it is a str (defensive — some modules
        expose __version__ as a tuple or int).
        """
        stats: dict[str, Any] = {}

        for stat_key, (attr, invoker) in self._registry.items():
            fn = getattr(mod, attr, None)
            if fn is None:
                continue
            with contextlib.suppress(Exception):
                if invoker is not None:
                    stats[stat_key] = invoker(fn)
                else:
                    # No invoker — call as no-arg callable
                    stats[stat_key] = fn()

        # Version — str only; guard against tuple/int/__version__ from older modules
        with contextlib.suppress(Exception):
            ver: Any = getattr(mod, "__version__", None)
            if ver is None:
                ver = getattr(mod, "version", None)
            if isinstance(ver, str):
                stats["version"] = ver

        return stats
