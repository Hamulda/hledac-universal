"""
core.capabilities — Centralized optional dependency registry.
F350M-R: Replaces scattered try/except ImportError chains.

Usage:
    from core.capabilities import CAPS, ZSTD, AIOHTTP, LIGHTPANDA

    class MyClass:
        def __init__(self):
            self._zstd = CAPS.require(ZSTD)
            self._aio = CAPS.require(AIOHTTP)

    # Telemetry
    unavailable = [k for k, v in CAPS.dump().items() if not v]
    logger.debug(f"[CAP] Unavailable: {unavailable}")

Adding a new dependency — one line:
    MY_DEP = Cap("my_dep", "my_package.module", install_hint="pip install my-package")
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "CAPS",
    "ZSTD",
    "AIOHTTP",
    "CURL_CFFI",
    "LIGHTPANDA",
    "SESSION",
    "PAYWALL_BYPASS",
    "DARKNET_CONNECTOR",
    "HINTS",
    "OTEL",
    "DEEP_WEB_HINTS",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Capability:
    """Frozen descriptor for one optional dependency."""
    name: str
    import_path: str  # "module" or "module:attr"
    is_optional: bool = True
    install_hint: str = ""


class CapabilityRegistry:
    """Lazy, cached registry — require() resolves each cap once, cache thereafter."""

    __slots__ = ("_resolved", "_availability", "_missing_logged")

    def __init__(self) -> None:
        self._resolved: dict[str, Any] = {}
        self._availability: dict[str, bool] = {}
        self._missing_logged: set[str] = set()

    def require(self, cap: Capability) -> Any:
        """Resolve cap, cache result. Returns None if unavailable."""
        if cap.name in self._resolved:
            return self._resolved[cap.name]

        available, result = self._resolve_one(cap)
        self._availability[cap.name] = available
        self._resolved[cap.name] = result

        if not available and cap.install_hint and cap.name not in self._missing_logged:
            self._missing_logged.add(cap.name)
            logger.debug(f"[CAP] {cap.name} unavailable — install: {cap.install_hint}")
        return result

    def is_available(self, name: str) -> bool:
        return self._availability.get(name, False)

    def dump(self) -> dict[str, bool]:
        return dict(self._availability)

    @staticmethod
    def _resolve_one(cap: Capability) -> tuple[bool, Any]:
        """Import cap.import_path. Never raises."""
        parts = cap.import_path.split(":", 1)
        module_path, attr_name = parts[0], parts[1] if len(parts) > 1 else None
        spec = importlib.util.find_spec(module_path)
        if spec is None:
            return False, None
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            return False, None
        if attr_name:
            result: Any = getattr(module, attr_name, None)
            return (True, result) if result is not None else (False, None)
        return True, module


CAPS = CapabilityRegistry()

# Pre-defined capabilities
ZSTD = Capability("zstd", "zstandard:zstd", install_hint="Python 3.14+ stdlib; older: pip install zstandard")
AIOHTTP = Capability("aiohttp", "aiohttp", install_hint="pip install aiohttp")
CURL_CFFI = Capability("curl_cffi", "curl_cffi", install_hint="pip install curl_cffi")
LIGHTPANDA = Capability("lightpanda", "hledac.universal.tools.lightpanda_manager:LightpandaManager")
SESSION = Capability("session", "hledac.universal.tools.session_manager:SessionManager")
PAYWALL_BYPASS = Capability("paywall_bypass", "hledac.universal.tools.paywall:PaywallBypass")
DARKNET_CONNECTOR = Capability("darknet_connector", "hledac.universal.tools.darknet:DarknetConnector")
DEEP_WEB_HINTS = Capability("deep_web_hints", "hledac.universal.tools.deep_web_hints:DeepWebHintsExtractor")
OTEL = Capability("otel", "otel:instrumented", install_hint="pip install otel")
HINTS = DEEP_WEB_HINTS  # backward compat alias
