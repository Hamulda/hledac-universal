# rendering/__init__.py
# Sprint F214AC: macOS WKWebView Subprocess JS Renderer
# Always-on, isolated subprocess, fail-soft, macOS-only.
"""macOS WKWebView-based JS renderer via isolated subprocess.

Used as a lightweight fallback AFTER static hydration is insufficient
but BEFORE heavy browser (camoufox/nodriver) is attempted.

Rendering order:
    1. normal HTTP fetch
    2. static hydration extractor
    3. macOS WKWebView renderer          ← this module
    4. camoufox/nodriver (only if explicitly enabled)

Invariant: max 1 concurrent render via module-level semaphore.
"""

from __future__ import annotations

from hledac.universal.rendering.macos_webkit_renderer import (
    WebKitRenderResult,
    is_macos_webkit_available,
    fetch_with_macos_webkit,
    MACOS_WEBKIT_REASONS,
)

__all__ = [
    "WebKitRenderResult",
    "is_macos_webkit_available",
    "fetch_with_macos_webkit",
    "MACOS_WEBKIT_REASONS",
]