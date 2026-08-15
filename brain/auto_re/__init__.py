"""
brain/auto_re/__init__.py — ADVERSARY-004: Hermes3 Auto-RE Module
===============================================================

Stage A — Magic-byte router
Stage B — Hermes3 parser generation
Stage C — Sandboxed execution (ast.parse + subprocess restricted)
Stage D — IOC validation gate (Rust SIMD extractor)
Stage E — Audit trail (disk cache, NOT re-executed)

Opt-in: HLEDAC_ENABLE_AUTO_RE=1 (default OFF)
Max 3 attempts per sprint (3×4s = 12s Hermes3 budget on M1 MLX)

Pattern: Hermes3 generates Python parser → AST validate → restricted exec → IOC gate.
Generated code stored in ~/.cache/hledac/auto_re/<sha256>.py for 24h audit.
"""

from __future__ import annotations

from hledac.universal.brain.auto_re.parser_forge import (
from core import aclose
    AutoREEngine,
    AutoRECatalog,
    MAGIC_ROUTER,
    AutoREResult,
    ParsedIOC,
    get_auto_re_engine,
    is_auto_re_enabled,
)

__all__ = [
    "AutoREEngine",
    "AutoRECatalog",
    "MAGIC_ROUTER",
    "AutoREResult",
    "ParsedIOC",
    "get_auto_re_engine",
    "is_auto_re_enabled",
]
