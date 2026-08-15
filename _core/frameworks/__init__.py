"""
core.frameworks — Darwin-only PyObjC framework lazy imports (PEP 810).

Centralizovaný lazy import pro 6 Apple frameworků:
    Vision, NaturalLanguage, CoreML, Quartz, Cocoa(AppKit), WebKit

M1 8GB: všechny jsou lazy-loaded — žádný framework se neimportuje
při startu, pouze při prvním použití přes getattr.

Použití:
    from hledac.universal._core.frameworks import Vision, NaturalLanguage, CoreML, WebKit
    from hledac.universal._core.frameworks import COCOA_AVAILABLE, VISION_AVAILABLE, ...

Pro kontrolu bez importu:
    from hledac.universal._core.frameworks import is_framework_available
    if is_framework_available("Vision"):
        from hledac.universal._core.frameworks import Vision
"""

from __future__ import annotations

__all__ = [
    "Vision",
    "NaturalLanguage",
    "CoreML",
    "Quartz",
    "Cocoa",
    "WebKit",
    "is_framework_available",
    "VISION_AVAILABLE",
    "NATURALLANGUAGE_AVAILABLE",
    "COREML_AVAILABLE",
    "QUARTZ_AVAILABLE",
    "COCOA_AVAILABLE",
    "WEBKIT_AVAILABLE",
]

# ── TYPE_CHECKING: stub declarations so pyright/ty can resolve module names ───
# These are only for type checking; at runtime they are set by __getattr__.
# We use module-level assignments under TYPE_CHECKING to give pyright a binding.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import Vision
    import NaturalLanguage
    import CoreML
    import Quartz
    import Cocoa
    import WebKit

# ── availability flags (set on first access) ──────────────────────────────────

VISION_AVAILABLE: bool = False
NATURALLANGUAGE_AVAILABLE: bool = False
COREML_AVAILABLE: bool = False
QUARTZ_AVAILABLE: bool = False
COCOA_AVAILABLE: bool = False
WEBKIT_AVAILABLE: bool = False


def is_framework_available(name: str) -> bool:
    """Check if a Darwin framework is available without triggering import."""
    return getattr(is_framework_available, f"{name.upper()}_AVAILABLE", False)


# ── PEP 810 lazy imports ───────────────────────────────────────────────────────
# NOTE: No module-level imports here — this is intentional (M1 8GB, lazy load).
# The _AVAILABLE flags gate all real imports. At runtime, attributes are set
# via globals() inside __getattr__.

from typing import Any
from _core._util import aclose

# Framework metadata: (module_name, available_flag_name)
_FRAMEWORK_DEFS: dict[str, tuple[str, str]] = {
    "Vision": ("Vision", "VISION_AVAILABLE"),
    "NaturalLanguage": ("NaturalLanguage", "NATURALLANGUAGE_AVAILABLE"),
    "CoreML": ("CoreML", "COREML_AVAILABLE"),
    "Quartz": ("Quartz", "QUARTZ_AVAILABLE"),
    "Cocoa": ("Cocoa", "COCOA_AVAILABLE"),
    "WebKit": ("WebKit", "WEBKIT_AVAILABLE"),
}


def _lazy_load_framework(name: str) -> Any:
    """Load a framework lazily, caching the result in globals()."""
    meta = _FRAMEWORK_DEFS[name]
    module_name, flag_name = meta
    available = globals().get(flag_name, False)
    if available:
        return globals()[name]
    try:
        mod = __import__(module_name, fromlist=[name])
        globals()[name] = mod
        globals()[flag_name] = True
        return mod
    except ImportError:
        globals()[name] = None
        globals()[flag_name] = False
        return None


def __getattr__(name: str) -> Any:
    if name in _FRAMEWORK_DEFS:
        return _lazy_load_framework(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
