# hledac/universal/utils/action_result.py
"""
ActionResult — unified result from any research action.


Migrated from dataclass to msgspec.Struct for:
- ~2-3× faster instantiation on hot path
- Zero-copy encoding via msgspec
- Python 3.14 compatible (no __slots__ issues)
"""
from __future__ import annotations


import msgspec
from compat.msgspec_gc_compat import Struct
from _core import aclose


class ActionResult(Struct):
    """Unified result from any research action.

    Msgspec.Struct benefits:
    - ~2-3× faster instantiation vs dataclass
    - Zero-GC overhead with gc=False (no tracing for cycle detection)
    - Python 3.14 ready
    """
    success: bool = False
    findings: list = msgspec.field(default_factory=list)
    sources: list = msgspec.field(default_factory=list)
    hypotheses: list = msgspec.field(default_factory=list)
    contradictions: list = msgspec.field(default_factory=list)
    metadata: dict = msgspec.field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility."""
        return msgspec.to_builtins(self)

    @classmethod
    def from_dict(cls, data: dict) -> ActionResult:
        """Create from dict for backward compatibility."""
        return msgspec.convert(data, cls)
