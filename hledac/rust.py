# hledac/rust.py — Hledac Rust Extension Facade
"""
Hledac Rust extension facade providing typed access to Rust modules.

This module provides the `hledac.rust` namespace that other parts of the
codebase expect. It delegates to `core.rust_backend` or directly to
`hledac_rust_extensions` as appropriate.

Usage:
    from hledac.universal.hledac.rust import (
        get_preset_for_profile,
        get_safari18_settings,
        get_safari17_settings,
        get_safari16_settings,
        needs_webkit_preset,
        get_webkit_window_increment,
        get_webkit_initial_window_size,
        get_curl_default_initial_window_size,
        validate_safari_fingerprint,
        get_webkit_profiles,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Try to import from hledac_rust_extensions (the actual Rust extension)
try:
    from hledac_rust_extensions import h2_safari_preset as _h2_safari_preset
    from hledac_rust_extensions import anti_analysis as _anti_analysis
    from hledac_rust_extensions import tls13 as _tls13
    from hledac_rust_extensions import stealth_bridge as _stealth_bridge
    _HLEDAC_RUST_AVAILABLE = True
except ImportError:
    _h2_safari_preset = None  # type: ignore[assignment]
    _anti_analysis = None  # type: ignore[assignment]
    _tls13 = None  # type: ignore[assignment]
    _stealth_bridge = None  # type: ignore[assignment]
    _HLEDAC_RUST_AVAILABLE = False

# Re-export h2_safari_preset functions
try:
    from hledac_rust_extensions.h2_safari_preset import (
        get_preset_for_profile,
        get_safari18_settings,
        get_safari17_settings,
        get_safari16_settings,
        needs_webkit_preset,
        get_webkit_window_increment,
        get_webkit_initial_window_size,
        get_curl_default_initial_window_size,
        validate_safari_fingerprint,
        get_webkit_profiles,
    )
    __all__ = [
        "get_preset_for_profile",
        "get_safari18_settings",
        "get_safari17_settings",
        "get_safari16_settings",
        "needs_webkit_preset",
        "get_webkit_window_increment",
        "get_webkit_initial_window_size",
        "get_curl_default_initial_window_size",
        "validate_safari_fingerprint",
        "get_webkit_profiles",
    ]
except ImportError:
    # Provide stub functions when Rust extension unavailable
    def _stub_fn(*args: Any, **kwargs: Any) -> Any:
        raise ImportError(
            "hledac_rust_extensions.h2_safari_preset not available. "
            "Ensure Rust extension is built with h2_safari_preset feature."
        )

    get_preset_for_profile = _stub_fn
    get_safari18_settings = _stub_fn
    get_safari17_settings = _stub_fn
    get_safari16_settings = _stub_fn
    needs_webkit_preset = _stub_fn
    get_webkit_window_increment = _stub_fn
    get_webkit_initial_window_size = _stub_fn
    get_curl_default_initial_window_size = _stub_fn
    validate_safari_fingerprint = _stub_fn
    get_webkit_profiles = _stub_fn

    __all__ = []


class _HledacRustModule:
    """
    Lazy-loading facade for hledac.rust submodules.

    Provides attribute-style access to Rust modules:
        from hledac.universal.hledac.rust import rust
        rust.anti_analysis.quick_probe_async(...)
        rust.stealth_bridge.dns_resolve_async(...)
    """

    __slots__ = ("_anti_analysis", "_stealth_bridge", "_tls13", "_h2_safari_preset")

    def __init__(self) -> None:
        self._anti_analysis = None
        self._stealth_bridge = None
        self._tls13 = None
        self._h2_safari_preset = None

    @property
    def anti_analysis(self) -> Any:
        """Lazy access to anti_analysis module."""
        if self._anti_analysis is None and _anti_analysis is not None:
            self._anti_analysis = _anti_analysis
        return self._anti_analysis

    @property
    def stealth_bridge(self) -> Any:
        """Lazy access to stealth_bridge module."""
        if self._stealth_bridge is None and _stealth_bridge is not None:
            self._stealth_bridge = _stealth_bridge
        return self._stealth_bridge

    @property
    def tls(self) -> Any:
        """Lazy access to tls13 module."""
        if self._tls13 is None and _tls13 is not None:
            self._tls13 = _tls13
        return self._tls13

    @property
    def h2_safari_preset(self) -> Any:
        """Lazy access to h2_safari_preset module."""
        if self._h2_safari_preset is None and _h2_safari_preset is not None:
            self._h2_safari_preset = _h2_safari_preset
        return self._h2_safari_preset

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"module 'hledac.rust' has no attribute {name!r}. "
            f"Available: anti_analysis, stealth_bridge, tls, h2_safari_preset"
        )


# Module-level singleton
rust = _HledacRustModule()
