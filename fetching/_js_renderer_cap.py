"""
JS Renderer Capability Tracker for public_fetcher.

Replaces module-level globals:
- _js_renderer_capability dict
- _js_renderer_capability_lock

Thread-safe capability checking with caching.
Cached after first check — use reset() to force re-check.
"""

import threading


def check_chrome_binary_exists() -> bool:
    """Check if Chrome binary exists for nodriver."""
    try:
        return True
    except Exception:
        return False


class JSRendererCapability:
    """Thread-safe JS renderer capability tracker.

    Tracks availability of nodriver and playwright.
    Uses threading.Lock for thread-safe access.
    Cached after first check — use reset() to force re-check.

    Values:
        None = available
        str = unavailable reason
    """

    __slots__ = ("_capability", "_lock")

    def __init__(self) -> None:
        self._capability: dict[str, str | None] = {
            "nodriver": None,
            "playwright": None,
        }
        self._lock = threading.Lock()

    def get(self) -> dict[str, str | None]:
        """Get current capability snapshot (copy)."""
        with self._lock:
            return dict(self._capability)

    def reset(self) -> None:
        """Reset all capabilities to unknown (force re-check)."""
        with self._lock:
            self._capability = {"nodriver": None, "playwright": None}

    def check(self) -> dict[str, str | None]:
        """Run capability checks and return cached state.

        Checks are only run once per renderer (until reset()).
        """
        with self._lock:
            self._check_nodriver_unlocked()
            self._check_playwright_unlocked()
            return dict(self._capability)

    def _check_nodriver_unlocked(self) -> None:
        """Check nodriver availability (must hold lock)."""
        if self._capability["nodriver"] is not None:
            return
        if not check_chrome_binary_exists():
            self._capability["nodriver"] = "chrome_binary_missing"
            return
        try:
            self._capability["nodriver"] = None  # available
        except ImportError:
            self._capability["nodriver"] = "nodriver_unavailable"

    def _check_playwright_unlocked(self) -> None:
        """Check playwright availability (must hold lock)."""
        if self._capability["playwright"] is not None:
            return
        from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags

        heavy_browser_enabled = FeatureFlags.get(FeatureFlag.HEAVY_BROWSER)
        if not heavy_browser_enabled:
            self._capability["playwright"] = "heavy_browser_disabled"
            return
        try:
            self._capability["playwright"] = None  # available
        except ImportError:
            self._capability["playwright"] = "playwright_unavailable"

    def is_any_available(self) -> bool:
        """Check if any JS renderer is available."""
        with self._lock:
            return any(v is None for v in self._capability.values())


js_renderer_cap = JSRendererCapability()
