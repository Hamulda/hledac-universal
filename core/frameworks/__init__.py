"""
core.frameworks — Darwin-only PyObjC framework lazy imports (PEP 810).

Centralizovaný lazy import pro 6 Apple frameworků:
    Vision, NaturalLanguage, CoreML, Quartz, Cocoa(AppKit), WebKit

M1 8GB: všechny jsou lazy-loaded — žádný framework se neimportuje
při startu, pouze při prvním použití přes getattr.

Použití:
    from core.frameworks import Vision, NaturalLanguage, CoreML, WebKit
    from core.frameworks import COCOA_AVAILABLE, VISION_AVAILABLE, ...

Pro kontrolu bez importu:
    from core.frameworks import is_framework_available
    if is_framework_available("Vision"):
        from core.frameworks import Vision
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

def __getattr__(name: str):
    # ── Vision (VNRecognizeTextRequest, VNDetectBarcodesRequest, etc.) ─────────
    if name == "Vision":
        if not VISION_AVAILABLE:
            try:
                import Vision as _v
                globals()["Vision"] = _v
                globals()["VISION_AVAILABLE"] = True
            except ImportError:
                globals()["Vision"] = None  # type: ignore[assignment]
                globals()["VISION_AVAILABLE"] = False
        return Vision  # type: ignore[return-value]

    # ── NaturalLanguage (NLTagger, NLTokenUnit, NLTagScheme) ──────────────────
    if name == "NaturalLanguage":
        if not NATURALLANGUAGE_AVAILABLE:
            try:
                import NaturalLanguage as _nl
                globals()["NaturalLanguage"] = _nl
                globals()["NATURALLANGUAGE_AVAILABLE"] = True
            except ImportError:
                globals()["NaturalLanguage"] = None  # type: ignore[assignment]
                globals()["NATURALLANGUAGE_AVAILABLE"] = False
        return NaturalLanguage  # type: ignore[return-value]

    # ── CoreML (VNCoreMLModel, MLModel) ───────────────────────────────────────
    if name == "CoreML":
        if not COREML_AVAILABLE:
            try:
                import CoreML as _cml
                globals()["CoreML"] = _cml
                globals()["COREML_AVAILABLE"] = True
            except ImportError:
                globals()["CoreML"] = None  # type: ignore[assignment]
                globals()["COREML_AVAILABLE"] = False
        return CoreML  # type: ignore[return-value]

    # ── Quartz (CGWindowList, CGImage) ──────────────────────────────────────────
    if name == "Quartz":
        if not QUARTZ_AVAILABLE:
            try:
                import Quartz as _q
                globals()["Quartz"] = _q
                globals()["QUARTZ_AVAILABLE"] = True
            except ImportError:
                globals()["Quartz"] = None  # type: ignore[assignment]
                globals()["QUARTZ_AVAILABLE"] = False
        return Quartz  # type: ignore[return-value]

    # ── Cocoa / AppKit ────────────────────────────────────────────────────────
    if name == "Cocoa":
        if not COCOA_AVAILABLE:
            try:
                import Cocoa as _c
                globals()["Cocoa"] = _c
                globals()["COCOA_AVAILABLE"] = True
            except ImportError:
                globals()["Cocoa"] = None  # type: ignore[assignment]
                globals()["COCOA_AVAILABLE"] = False
        return Cocoa  # type: ignore[return-value]

    # ── WebKit (WKWebView, WKWebsiteDataStore) ────────────────────────────────
    if name == "WebKit":
        if not WEBKIT_AVAILABLE:
            try:
                import WebKit as _wk
                globals()["WebKit"] = _wk
                globals()["WEBKIT_AVAILABLE"] = True
            except ImportError:
                globals()["WebKit"] = None  # type: ignore[assignment]
                globals()["WEBKIT_AVAILABLE"] = False
        return WebKit  # type: ignore[return-value]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
