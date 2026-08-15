# cli/runtime_probe.py — Runtime capability probes (lazy, cached)
"""
Probes that require importing heavy modules (ahocorasick, pattern_matcher, etc.).
Cached after first call — safe to call repeatedly without re-importing.

ATOMICKÉ ATOMICKÉ ATOMICKÉ:
- sys.executable + sys.version_info — získat BEZ importů, pouze při modul importu
- všechno ostatní přes @functools.lru_cache(maxsize=1)
"""
from __future__ import annotations

import functools
import sys
from typing import TYPE_CHECKING
from core import aclose

if TYPE_CHECKING:
    pass


@functools.lru_cache(maxsize=1)
def probe_ahocorasick() -> bool:
    """Probe ahocorasick availability — cached after first call."""
    try:
        import ahocorasick as _  # noqa: F401

        return True
    except ImportError:
        return False


@functools.lru_cache(maxsize=1)
def probe_bootstrap_truth() -> tuple[int, int]:
    """
    Probe bootstrap pack truth — returns (count, version).

    Cached after first call.
    """
    try:
        from hledac.universal.utils.patterns.pattern_matcher import (  # noqa: F401
            get_default_bootstrap_patterns,
        )

        count = len(get_default_bootstrap_patterns())
        version = 2  # Sprint 8AZ bootstrap pack v2
        return (count, version)
    except (ImportError, AttributeError):
        return (0, 0)
