"""
Stealth Strategies — Strategy Protocol + 5 Concrete Strategies

Design:











- Each strategy is a Protocol (PEP 544) + concrete implementation
- Strategies are instantiated lazily inside StealthLayer.init_*()
- Heavy deps (torch, transformers) stay inside strategy constructors (lazy)
- CaptchaSolvingStrategy delegates to captcha_solver.py (Vision/CoreML + 2captcha)
- Local OCR is off-by-default (HLEDAC_ENABLE_CAPTCHA_LOCAL=1 to enable)

M1 8GB: Third-party API (2captcha) is primary path.
Vision/CoreML fallback is secondary. Local OCR is off-by-default.
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any, Protocol, runtime_checkable

from hledac.universal.utils.asyncx import safe_create_task
from _core import aclose

logger = logging.getLogger(__name__)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

__all__ = [
    'StealthStrategy',
    'UARotationConfig',
    'UARotationStrategy',
    'HeaderRandomizationConfig',
    'HeaderRandomizationStrategy',
    'CircuitManagementConfig',
    'CircuitManagementStrategy',
    'FingerprintMuterConfig',
    'FingerprintMuterStrategy',
    'CaptchaSolvingConfig',
    'CaptchaSolvingStrategy',
]


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Protocol
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class StealthStrategy(Protocol):
    """Strategy Protocol — all stealth sub-concerns implement this."""

    strategy_name: str

    async def mount(self, _ctx: Any) -> None:
        """Mount strategy — called by StealthLayer.initialize()."""
        ...

    async def unmount(self, _ctx: Any) -> None:
        """Unmount strategy — called by StealthLayer.cleanup()."""
        ...

    async def on_event(self, _ctx: Any, event: Any) -> Any:
        """Handle stealth events (optional)."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# 1. UA Rotation Strategy
# ─────────────────────────────────────────────────────────────────────────────


class UARotationConfig(Struct):
    rotate_on_each_request: bool = False
    min_rotation_interval: float = 300.0  # 5 minutes
    pool: tuple[str, ...] = field(
        default_factory=lambda: (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    )


class UARotationStrategy:
    """Rotate User-Agent headers per-request or on interval."""

    __slots__ = ("_config", "_current_ua", "_rotation_count", "_last_rotation")
    strategy_name: str = "ua_rotation"

    def __init__(self, config: UARotationConfig | None = None) -> None:
        self._config = config or UARotationConfig()
        self._current_ua: str = ""
        self._rotation_count: int = 0
        self._last_rotation: float = 0.0

    async def mount(self, ctx: Any) -> None:
        self._current_ua = _RNG.choice(self._config.pool)
        self._last_rotation = asyncio.get_running_loop().time()
        logger.debug(f"UARotationStrategy mounted: {self._current_ua[:60]}...")

    async def unmount(self, ctx: Any) -> None:
        logger.debug(f"UARotationStrategy unmounted: {self._rotation_count} rotations")

    async def on_event(self, ctx: Any, event: Any) -> Any:
        # Rotate UA on request events if configured
        if self._config.rotate_on_each_request:
            if event.type == "pre_fetch" or event.type == "pre_request":
                await self.rotate()
        return event

    async def rotate(self) -> str:
        """Generate new UA and return it."""
        self._current_ua = _RNG.choice(self._config.pool)
        self._rotation_count += 1
        self._last_rotation = asyncio.get_running_loop().time()
        return self._current_ua

    def get_ua(self) -> str:
        """Get current UA (no rotation)."""
        return self._current_ua

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "rotations": self._rotation_count,
            "current_ua": self._current_ua[:80],
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Header Randomization Strategy
# ─────────────────────────────────────────────────────────────────────────────


class HeaderRandomizationConfig(Struct):
    enabled: bool = True
    randomize_order: bool = True
    add_chaff_headers: bool = False
    chaff_count: int = 3


class HeaderRandomizationStrategy:
    """Randomize HTTP request headers to defeat header fingerprinting."""

    __slots__ = ("_config", "_header_count", "_chaff_count")
    strategy_name: str = "header_randomization"

    # Common browser headers to randomize
    _REAL_HEADERS = (
        "Accept-Language",
        "Accept-Encoding",
        "Accept",
        "Sec-Ch-Ua",
        "Sec-Ch-Ua-Mobile",
        "Sec-Ch-Ua-Platform",
        "Sec-Fetch-Dest",
        "Sec-Fetch-Mode",
        "Sec-Fetch-Site",
        "Sec-Fetch-User",
        "Sec-Fetch-Cross-Site",
    )

    # Chaff headers that look plausible
    _CHAFF_HEADERS = (
        "X-Requested-With",
        "X-Forwarded-For",
        "X-Request-ID",
        "Cache-Control",
        "Pragma",
        "Expires",
    )

    def __init__(self, config: HeaderRandomizationConfig | None = None) -> None:
        self._config = config or HeaderRandomizationConfig()
        self._header_count = 0
        self._chaff_count = 0

    async def mount(self, ctx: Any) -> None:
        logger.debug("HeaderRandomizationStrategy mounted")

    async def unmount(self, ctx: Any) -> None:
        logger.debug(f"HeaderRandomizationStrategy: {self._header_count} requests, {self._chaff_count} chaff headers")

    async def on_event(self, ctx: Any, event: Any) -> Any:
        if event.type == "pre_fetch" and self._config.enabled:
            headers = event.data.get("headers", {})
            headers = self._randomize(headers)
            event.data["headers"] = headers
            self._header_count += 1
        return event

    def _randomize(self, headers: dict[str, str]) -> dict[str, str]:
        """Randomize header order and add chaff headers."""
        if self._config.randomize_order and headers:
            items = list(headers.items())
            _RNG.shuffle(items)
            headers = dict(items)

        if self._config.add_chaff_headers:
            for _ in range(self._config.chaff_count):
                key = _RNG.choice(self._CHAFF_HEADERS)
                headers[key] = f"{_RNG.randint(1, 999)}"
                self._chaff_count += 1

        return headers

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "requests": self._header_count,
            "chaff_headers": self._chaff_count,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Circuit / Tor Management Strategy
# ─────────────────────────────────────────────────────────────────────────────


class CircuitManagementConfig(Struct):
    enabled: bool = False
    tor_control_port: int = 9051
    tor_control_host: str = "127.0.0.1"
    circuit_rotation_interval: float = 600.0  # 10 minutes
    new_circuit_on_403: bool = True


class CircuitManagementStrategy:
    """Manage Tor circuits / proxy rotation.

    On M1 8GB: Lightweight — no Tor binary management here,
    just circuit signaling via existing stealth_session state.
    Heavy Tor management lives in transport/tor_transport.py.
    """

    __slots__ = ("_config", "_circuit_id", "_request_count", "_last_circuit_change")
    strategy_name: str = "circuit_management"

    def __init__(self, config: CircuitManagementConfig | None = None) -> None:
        self._config = config or CircuitManagementConfig()
        self._circuit_id: int = 0
        self._request_count: int = 0
        self._last_circuit_change: float = 0.0

    async def mount(self, ctx: Any) -> None:
        if not self._config.enabled:
            logger.debug("CircuitManagementStrategy: disabled (no Tor)")
            return
        self._circuit_id = 1
        self._last_circuit_change = asyncio.get_running_loop().time()
        logger.debug("CircuitManagementStrategy mounted")

    async def unmount(self, ctx: Any) -> None:
        logger.debug(f"CircuitManagementStrategy: {self._request_count} requests, circuit changes")

    async def on_event(self, ctx: Any, event: Any) -> None:
        if not self._config.enabled:
            return

        # Rotate on 403 response
        if self._config.new_circuit_on_403 and event.type == "response":
            status = event.data.get("status")
            if status == 403:
                await self.rotate_circuit()

        # Periodic rotation
        current = asyncio.get_running_loop().time()
        if current - self._last_circuit_change >= self._config.circuit_rotation_interval:
            await self.rotate_circuit()

        if event.type in ("pre_fetch", "pre_request"):
            self._request_count += 1

    async def rotate_circuit(self) -> bool:
        """Request a new Tor circuit."""
        if not self._config.enabled:
            return False

        self._circuit_id += 1
        self._last_circuit_change = asyncio.get_running_loop().time()
        logger.debug(f"CircuitManagementStrategy: rotated to circuit {self._circuit_id}")

        # Signal Tor control port (async, fire-and-forget)
        # F320: asyncio.ensure_future deprecated in Python 3.14+
        # safe_create_task: eager_start=True (3.12+), loop probe (F228G)
        safe_create_task(self._signal_tor_control(), name="stealth:tor_control_signal")
        return True

    async def _signal_tor_control(self) -> None:
        """Send TOR ControlPort NEWNYM signal (fire-and-forget)."""
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self._config.tor_control_host, self._config.tor_control_port))
            sock.sendall(b"AUTHENTICATE\r\n")
            resp = sock.recv(1024)
            if resp.startswith(b"250"):
                sock.sendall(b"signal NEWNYM\r\n")
                sock.recv(1024)
            sock.close()
        except Exception as e:
            logger.debug(f"Tor control signal failed (non-fatal): {e}")

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "enabled": self._config.enabled,
            "current_circuit": self._circuit_id,
            "requests": self._request_count,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fingerprint Muting Strategy
# ─────────────────────────────────────────────────────────────────────────────


class FingerprintMuterConfig(Struct):
    enabled: bool = True
    mute_canvas: bool = True
    mute_webgl: bool = True
    mute_audio: bool = True
    mute_font: bool = True
    randomize_screen: bool = True
    randomize_timezone: bool = True


class FingerprintMuterStrategy:
    """Inject JS to mute browser fingerprinting vectors.

    APEX-1005/1006/1007: Uses the unified ``ProfileGenerator`` +
    ``FingerprintProfile.to_evasion_scripts()`` pipeline from
    ``evasion_pipeline.py``.  No longer delegates to separate
    FingerprintRandomizer + JavaScriptEvasion.
    """

    __slots__ = ("_config", "_profile_gen", "_current_profile", "_evasions_applied", "_profile_rotations")
    strategy_name: str = "fingerprint_muter"

    def __init__(self, config: FingerprintMuterConfig | None = None) -> None:
        self._config = config or FingerprintMuterConfig()
        self._profile_gen: Any = None
        self._current_profile: Any = None
        self._evasions_applied: int = 0
        self._profile_rotations: int = 0

    async def mount(self, ctx: Any) -> None:
        # Lazy import unified pipeline — APEX-1005/1006/1007
        from hledac.universal.layers.evasion_pipeline import (
            ProfileGenerator,
            _EvasionScriptGenerator,
            FingerprintProfile,
    )

        self._profile_gen = ProfileGenerator()
        self._current_profile = self._profile_gen.generate()

        logger.debug("FingerprintMuterStrategy mounted (unified pipeline)")

    async def unmount(self, ctx: Any) -> None:
        logger.debug(
            f"FingerprintMuterStrategy: {self._evasions_applied} evasions, {self._profile_rotations} rotations"
    )

    async def on_event(self, ctx: Any, event: Any) -> Any:
        if event.type == "pre_fetch" and self._config.enabled:
            self._evasions_applied += 1
        return event

    def get_js_protection_script(self) -> str:
        """Get JS fingerprint protection script (unified pipeline).

        APEX-1005/1006/1007: Uses the unified EvasionScriptGenerator from
        the current profile — all 17 categories, CSPRNG+Gaussian, no duplicates.
        """
        if self._current_profile is None:
            return ""
        scripts = self._current_profile.to_evasion_scripts()
        return "\n".join(s.script for s in scripts)

    def rotate_profile(self) -> None:
        """Generate new fingerprint profile."""
        if self._profile_gen is not None:
            self._current_profile = self._profile_gen.rotate()
            self._profile_rotations += 1

    def get_detection_score(self) -> dict[str, Any]:
        if self._current_profile is not None:
            from hledac.universal.layers.evasion_pipeline import compute_detection_score
            return compute_detection_score(
                self._current_profile.to_evasion_scripts()
    )
        return {}

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "evasions_applied": self._evasions_applied,
            "profile_rotations": self._profile_rotations,
            "config": {
                "mute_canvas": self._config.mute_canvas,
                "mute_webgl": self._config.mute_webgl,
                "mute_audio": self._config.mute_audio,
                "mute_font": self._config.mute_font,
                "randomize_screen": self._config.randomize_screen,
                "randomize_timezone": self._config.randomize_timezone,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# 5. CAPTCHA Solving Strategy
# ─────────────────────────────────────────────────────────────────────────────


class CaptchaSolvingConfig(Struct):
    """Configuration for CAPTCHA solving strategy.

    M1 8GB: Primary = third-party API (2captcha), Secondary = Vision/CoreML.
    Local OCR (pytesseract, transformers/torch) is OFF BY DEFAULT.
    Enable with HLEDAC_ENABLE_CAPTCHA_LOCAL=1.
    """

    enabled: bool = True
    # Third-party API (primary path on M1 8GB)
    provider_api_key: str | None = None  # 2captcha key
    provider_endpoint: str = "http://2captcha.com/in.php"
    provider_poll_interval: float = 3.0
    provider_max_polls: int = 10
    # Vision/CoreML (secondary path)
    use_vision_coreml: bool = True  # requires captcha_solver.py VisionCaptchaSolver
    # Local OCR (off by default — heavy, M1 8GB unfriendly)
    use_local_ocr: bool = False  # Enable with HLEDAC_ENABLE_CAPTCHA_LOCAL=1


class CaptchaSolvingStrategy:
    """Solve CAPTCHAs using third-party API or Vision/CoreML.

    Delegates to captcha_solver.py (VisionCaptchaSolver) or direct 2captcha API.
    Local OCR is off-by-default — see captcha_solver_local.py to enable.
    """

    __slots__ = ("_config", "_vision_solver", "_initialized", "_solved", "_failed")
    strategy_name: str = "captcha_solving"

    def __init__(self, config: CaptchaSolvingConfig | None = None) -> None:
        self._config = config or CaptchaSolvingConfig()
        self._vision_solver: Any = None
        self._initialized = False
        self._solved = 0
        self._failed = 0

    async def mount(self, ctx: Any) -> None:
        # Initialize Vision/CoreML solver lazily (lightweight until first use)
        if self._config.use_vision_coreml:
            await self._init_vision_solver()

        logger.debug("CaptchaSolvingStrategy mounted")
        self._initialized = True

    async def unmount(self, ctx: Any) -> None:
        if self._vision_solver and hasattr(self._vision_solver, "clear_cache"):
            self._vision_solver.clear_cache()
        logger.debug(f"CaptchaSolvingStrategy: solved={self._solved}, failed={self._failed}")

    async def on_event(self, ctx: Any, event: Any) -> Any:
        return event

    async def _init_vision_solver(self) -> None:
        """Lazily init VisionCaptchaSolver from security/captcha_solver.py."""
        try:
            from hledac.universal.security.captcha_solver import VisionCaptchaSolver

            self._vision_solver = VisionCaptchaSolver()
            logger.debug("VisionCaptchaSolver initialized")
        except ImportError as e:
            logger.debug(f"VisionCaptchaSolver not available: {e}")

    async def solve(
        self,
        image_bytes: bytes,
        captcha_type: str = "image",
    ) -> str | None:
        """Solve a CAPTCHA image.

        Priority: 2captcha API > Vision/CoreML > VisionCaptchaSolver.solve()
        Local OCR is off-by-default (captcha_solver_local.py).
        """
        # 1. Try third-party API (primary, most reliable on M1 8GB)
        if self._config.provider_api_key:
            result = await self._solve_via_api(image_bytes)
            if result:
                self._solved += 1
                return result

        # 2. Try Vision/CoreML (secondary)
        if self._vision_solver:
            try:
                # VisionCaptchaSolver has .solve() which tries OCR then 2captcha
                result = await self._vision_solver.solve(image_bytes)
                if result:
                    self._solved += 1
                    return result
            except Exception as e:
                logger.debug(f"Vision/CoreML solve failed: {e}")

        self._failed += 1
        return None

    async def _solve_via_api(self, image_bytes: bytes) -> str | None:
        """Solve via third-party API (2captcha or compatible)."""
        import base64

        api_key = self._config.provider_api_key
        if not api_key:
            return None

        # F-01: Canonical session pool
        try:
            from hledac.universal.transport.session_pool import session_pool
            import httpx
        except Exception:
            return None

        b64 = base64.b64encode(image_bytes).decode()

        try:
            # F-01: session_pool.httpx() returns shared singleton — reuse for POST + poll loop
            session = await session_pool.httpx()
            # Submit
            response = await session.post(
                self._config.provider_endpoint,
                data={"key": api_key, "method": "base64", "body": b64},
                timeout=httpx.Timeout(30.0),
    )
            result = response.text

            if not result.startswith("OK|"):
                return None

            captcha_id = result.split("|")[1]

            # Poll
            for _ in range(self._config.provider_max_polls):
                await asyncio.sleep(self._config.provider_poll_interval)
                poll_response = await session.get(
                    f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}",
                    timeout=httpx.Timeout(10.0),
    )
                res = poll_response.text

                if res.startswith("OK|"):
                    return res.split("|")[1]
                if res == "CAPCHA_NOT_READY":
                    continue
                break

        except Exception as e:
            logger.debug(f"2Captcha API error: {e}")

        return None

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "solved": self._solved,
            "failed": self._failed,
            "use_vision_coreml": self._config.use_vision_coreml,
            "use_local_ocr": self._config.use_local_ocr,
            "provider_configured": self._config.provider_api_key is not None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Registry
# ─────────────────────────────────────────────────────────────────────────────

STEALTH_STRATEGIES: tuple[type[StealthStrategy], ...] = (
    UARotationStrategy,
    HeaderRandomizationStrategy,
    CircuitManagementStrategy,
    FingerprintMuterStrategy,
    CaptchaSolvingStrategy,
    )
"""All registered stealth strategies — used by StealthLayer to instantiate."""
