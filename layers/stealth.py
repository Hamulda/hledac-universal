"""
Stealth Layer - Browser Evasion, CAPTCHA Solving, and Behavior Simulation
=======================================================================

Consolidated from:
- stealth_layer.py: StealthLayer + AdvancedCaptchaSolver + BehaviorSimulator
- evasion_pipeline.py: Evasion scripts and fingerprint generation

Features:
- StealthBrowser: Playwright wrapper with anti-detection
- DetectionEvader: 10+ evasion scripts, behavior simulation
- CaptchaSolver: Multi-provider CAPTCHA solving
- BehaviorSimulator: Human-like behavior with Bézier curves
- Fingerprint randomization

M1 8GB: Uses __slots__ for memory efficiency, lightweight models only.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from enum import Enum
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import (
    StealthConfig,
    StealthSession,
)

logger = logging.getLogger(__name__)

__all__ = [
    "StealthLayer",
    "BehaviorSimulator",
    "BehaviorPattern",
    "ProfileGenerator",
    "FingerprintProfile",
    "EvasionCategory",
    "EvasionScript",
    "SimulationConfig",
    "MouseMovement",
]

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()


class EvasionCategory(Enum):
    """Categories for evasion scripts."""

    WEBDRIVER = "webdriver"
    AUTOMATION = "automation"
    PLUGINS = "plugins"
    PERMISSIONS = "permissions"
    WEBRTC = "webrtc"
    CANVAS = "canvas"
    WEBGL = "webgl"
    FONTS = "fonts"
    EVENTS = "events"
    DETECTION = "detection"
    GLOBALS = "globals"
    CHROME_RUNTIME = "chrome_runtime"
    CHROME_PLUGINS = "chrome_plugins"
    SCREEN = "screen"
    TIMEZONE = "timezone"
    HARDWARE = "hardware"
    AUDIO = "audio"


class EvasionScript(Struct, gc=False):
    """Evasion script with metadata."""

    script_id: str
    script: str
    category: EvasionCategory
    priority: int = 0


class FingerprintProfile(Struct, gc=False):
    """Browser fingerprint profile."""

    canvas_noise: tuple[int, int, int]
    webgl_vendor: str
    webgl_renderer: str
    screen_resolution: tuple[int, int, int, float]
    timezone: tuple[str, int]
    fonts: list[str]
    plugins: list[dict[str, str]]
    user_agent: str
    platform: str


class ProfileGenerator:
    """Generate realistic browser fingerprint profiles."""

    SCREEN_RESOLUTIONS = [
        (1920, 1080, 1.0),
        (2560, 1440, 1.0),
        (1366, 768, 1.0),
        (1536, 864, 1.0),
        (1440, 900, 1.0),
    ]

    TIMEZONES = [
        ("America/New_York", -5),
        ("America/Los_Angeles", -8),
        ("Europe/London", 0),
        ("Europe/Paris", 1),
        ("Asia/Tokyo", 9),
    ]

    WEBGL_PROFILES = [
        ("Intel Inc.", "Intel Iris OpenGL Engine"),
        ("Apple Inc.", "Apple M1"),
        ("NVIDIA Corporation", "NVIDIA GeForce GTX 1080"),
    ]

    COMMON_FONTS = [
        "Arial",
        "Helvetica",
        "Times New Roman",
        "Courier New",
        "Georgia",
        "Verdana",
        "Tahoma",
        "Trebuchet MS",
    ]

    COMMON_PLUGINS = [
        {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer"},
        {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
    ]

    __slots__ = (
        "_current_profile",
        "consistent_per_session",
        "platform",
        "randomize_canvas",
        "randomize_fonts",
        "randomize_screen",
        "randomize_timezone",
        "randomize_webgl",
        "session_duration",
    )

    def __init__(
        self,
        platform: str | None = None,
        session_duration: float = 3600.0,
        consistent_per_session: bool = True,
        randomize_canvas: bool = True,
        randomize_webgl: bool = True,
        randomize_fonts: bool = True,
        randomize_screen: bool = True,
        randomize_timezone: bool = True,
    ) -> None:
        self.platform = platform or "MacIntel"
        self.session_duration = session_duration
        self.consistent_per_session = consistent_per_session
        self.randomize_canvas = randomize_canvas
        self.randomize_webgl = randomize_webgl
        self.randomize_fonts = randomize_fonts
        self.randomize_screen = randomize_screen
        self.randomize_timezone = randomize_timezone
        self._current_profile: FingerprintProfile | None = None

    def generate(self, force_new: bool = False) -> FingerprintProfile:
        """Generate new fingerprint profile."""
        if not force_new and self._current_profile and self.consistent_per_session:
            return self._current_profile

        self._current_profile = FingerprintProfile(
            canvas_noise=self._generate_canvas_noise(),
            webgl_vendor=(
                self._generate_webgl(self.platform)[0] if self.randomize_webgl else ("Apple Inc.", "Apple M1")
            ),
            webgl_renderer=(self._generate_webgl(self.platform)[1] if self.randomize_webgl else ("Apple M1")),
            screen_resolution=(self._generate_screen() if self.randomize_screen else (1920, 1080, 1.0)),
            timezone=(self._generate_timezone() if self.randomize_timezone else ("America/New_York", -5)),
            fonts=self._generate_fonts() if self.randomize_fonts else self.COMMON_FONTS[:5],
            plugins=self._generate_plugins(),
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            platform=self.platform,
        )
        return self._current_profile

    def _generate_canvas_noise(self) -> tuple[int, int, int]:
        """Generate canvas noise RGB values."""
        return (
            _RNG.randint(-10, 10),
            _RNG.randint(-10, 10),
            _RNG.randint(-10, 10),
        )

    def _generate_screen(self) -> tuple[int, int, float]:
        """Generate screen resolution."""
        return _RNG.choice(self.SCREEN_RESOLUTIONS)

    def _generate_timezone(self) -> tuple[str, int]:
        """Generate timezone."""
        return _RNG.choice(self.TIMEZONES)

    def _generate_webgl(self, platform: str) -> tuple[str, str]:
        """Generate WebGL profile."""
        profile = _RNG.choice(self.WEBGL_PROFILES)
        return profile[0], profile[1]

    def _generate_fonts(self) -> list[str]:
        """Generate font list."""
        count = _RNG.randint(5, 10)
        return _RNG.sample(self.COMMON_FONTS, min(count, len(self.COMMON_FONTS)))

    def _generate_plugins(self) -> list[dict[str, str]]:
        """Generate plugin list."""
        return list(self.COMMON_PLUGINS)

    def get(self) -> FingerprintProfile:
        """Get current or generate new profile."""
        if self._current_profile is None:
            return self.generate()
        return self._current_profile

    def rotate(self) -> FingerprintProfile:
        """Force rotation to new fingerprint."""
        self._current_profile = None
        return self.generate(force_new=True)


class BehaviorPattern(Enum):
    """Pre-defined behavior patterns."""

    CASUAL = "casual"
    RESEARCHER = "researcher"
    QUICK = "quick"
    CAREFUL = "careful"


class SimulationConfig(Struct, gc=False):
    """Configuration for behavior simulation."""

    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
    min_delay: float = 0.5
    max_delay: float = 3.0
    mouse_speed: float = 1.0
    scroll_min: int = 100
    scroll_max: int = 800
    scroll_pause: float = 0.1
    randomness: float = 0.3


class MouseMovement(Struct, gc=False):
    """Mouse movement point."""

    x: float
    y: float
    timestamp: float


class BehaviorSimulator:
    """
    Simulate human-like web browsing behavior.

    M1-Optimized: Minimal CPU usage, efficient randomization.
    """

    PATTERNS: dict[BehaviorPattern, dict[str, Any]] = {
        BehaviorPattern.CASUAL: {
            "min_delay": 1.0,
            "max_delay": 5.0,
            "mouse_speed": 0.7,
            "scroll_min": 200,
            "scroll_max": 1000,
            "scroll_pause": 0.2,
            "randomness": 0.4,
        },
        BehaviorPattern.RESEARCHER: {
            "min_delay": 0.8,
            "max_delay": 2.5,
            "mouse_speed": 1.0,
            "scroll_min": 300,
            "scroll_max": 800,
            "scroll_pause": 0.15,
            "randomness": 0.25,
        },
        BehaviorPattern.QUICK: {
            "min_delay": 0.3,
            "max_delay": 1.2,
            "mouse_speed": 1.3,
            "scroll_min": 400,
            "scroll_max": 1200,
            "scroll_pause": 0.05,
            "randomness": 0.35,
        },
        BehaviorPattern.CAREFUL: {
            "min_delay": 2.0,
            "max_delay": 8.0,
            "mouse_speed": 0.5,
            "scroll_min": 100,
            "scroll_max": 400,
            "scroll_pause": 0.3,
            "randomness": 0.2,
        },
    }

    __slots__ = (
        "action_count",
        "config",
        "last_action_time",
        "mouse_position",
        "scroll_position",
        "viewport_height",
        "viewport_width",
    )

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self._apply_pattern()
        self.last_action_time: float = time.time()
        self.mouse_position: tuple[int, int] = (0, 0)
        self.scroll_position: int = 0
        self.action_count: int = 0
        self.viewport_width: int = 1920
        self.viewport_height: int = 1080

    def _apply_pattern(self) -> None:
        """Apply pattern preset to config."""
        if self.config.pattern in self.PATTERNS:
            preset = self.PATTERNS[self.config.pattern]
            for key, value in preset.items():
                setattr(self.config, key, value)

    def _random_delay(self, min_mult: float = 0.8, max_mult: float = 1.2) -> float:
        """Generate random delay with variation."""
        base = _RNG.uniform(self.config.min_delay, self.config.max_delay)
        return base * _RNG.uniform(min_mult, max_mult)

    def _generate_mouse_path(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        num_points: int = 20,
    ) -> list[MouseMovement]:
        """Generate human-like mouse path using Bézier curve."""
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2
        offset_range = abs(end[0] - start[0]) + abs(end[1] - start[1])
        offset_range *= 0.2 * self.config.randomness
        control = (
            mid_x + _RNG.uniform(-offset_range, offset_range),
            mid_y + _RNG.uniform(-offset_range, offset_range),
        )

        points = []
        now = time.time()
        for i in range(num_points):
            t = i / (num_points - 1)
            x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0]
            y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1]
            jitter = self.config.randomness * 2
            x += _RNG.uniform(-jitter, jitter)
            y += _RNG.uniform(-jitter, jitter)
            speed_variation = _RNG.uniform(0.8, 1.2) / self.config.mouse_speed
            timestamp = now + i * 0.01 * speed_variation
            points.append(MouseMovement(x=x, y=y, timestamp=timestamp))
        return points

    async def simulate_mouse_move(
        self,
        target_x: int,
        target_y: int,
        callback: Any | None = None,
    ) -> None:
        """Simulate mouse movement to target position."""
        path = self._generate_mouse_path(self.mouse_position, (target_x, target_y))
        for point in path:
            self.mouse_position = (int(point.x), int(point.y))
            if callback:
                await callback(self.mouse_position)
            await asyncio.sleep(0.005)
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_click(
        self,
        x: int | None = None,
        y: int | None = None,
        callback: Any | None = None,
    ) -> None:
        """Simulate mouse click."""
        if x is not None and y is not None:
            await self.simulate_mouse_move(x, y, callback)
        await asyncio.sleep(self._random_delay(0.1, 0.3))
        if callback:
            await callback(("click", self.mouse_position))
        await asyncio.sleep(self._random_delay(0.2, 0.5))
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_scroll(
        self,
        direction: str = "down",
        amount: int | None = None,
        callback: Any | None = None,
    ) -> None:
        """Simulate scrolling."""
        if amount is None:
            amount = _RNG.randint(self.config.scroll_min, self.config.scroll_max)
        if direction == "up":
            amount = -amount
        chunk_size = 100
        remaining = amount
        while abs(remaining) > 0:
            chunk = min(chunk_size, abs(remaining))
            if remaining < 0:
                chunk = -chunk
            if callback:
                await callback(("scroll", chunk))
            self.scroll_position += chunk
            remaining -= chunk
            await asyncio.sleep(
                _RNG.uniform(
                    self.config.scroll_pause * 0.8,
                    self.config.scroll_pause * 1.2,
                )
            )
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_typing(
        self,
        text: str,
        callback: Any | None = None,
        wpm: int = 60,
    ) -> None:
        """Simulate human-like typing."""
        chars_per_minute = wpm * 5
        base_delay = 60 / chars_per_minute
        for char in text:
            delay = base_delay * _RNG.uniform(0.7, 1.3)
            if callback:
                await callback(("type", char))
            await asyncio.sleep(delay)
            if _RNG.random() < 0.05:  # Occasional pause
                await asyncio.sleep(_RNG.uniform(0.2, 0.5))
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_reading(
        self,
        duration: float = 10.0,
        scroll_probability: float = 0.3,
    ) -> None:
        """Simulate reading a page."""
        start_time = time.time()
        while time.time() - start_time < duration:
            await asyncio.sleep(self._random_delay(0.5, 1.5))
            if _RNG.random() < scroll_probability:
                direction = "down" if _RNG.random() > 0.3 else "up"
                await self.simulate_scroll(direction)

    def get_statistics(self) -> dict[str, Any]:
        """Get simulation statistics."""
        return {
            "action_count": self.action_count,
            "mouse_position": self.mouse_position,
            "scroll_position": self.scroll_position,
            "pattern": self.config.pattern.value,
        }


class StealthLayer:
    """
    Stealth layer for web browsing with anti-detection and behavior simulation.

    Features:
    - Fingerprint randomization
    - Evasion scripts
    - Behavior simulation
    - CAPTCHA solving (optional)

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    layer_name: str = "stealth"
    _priority: int = 60  # Medium-high priority

    __slots__ = (
        "_behavior_simulator",
        "_captcha_solver",
        "_ctx",
        "_evasion_scripts",
        "_fingerprint_randomizer",
        "_initialized",
        "_profile_generator",
        "_sessions",
        "config",
    )

    def __init__(self, config: StealthConfig | None = None) -> None:
        self.config = config or StealthConfig()
        self._profile_generator = ProfileGenerator()
        self._fingerprint_randomizer = None
        self._behavior_simulator: BehaviorSimulator | None = None
        self._captcha_solver = None
        self._evasion_scripts: list[str] = []
        self._sessions: dict[str, StealthSession] = {}
        self._initialized = False
        self._ctx: Any | None = None

    async def mount(self, ctx: Any) -> None:
        """Mount the stealth layer."""
        await self.initialize()
        ctx.set("stealth", self)

    async def unmount(self, ctx: Any) -> None:
        """Unmount the stealth layer."""
        await self.cleanup()

    async def process(self, ctx: Any, data: Any) -> Any:
        """Process data through stealth layer."""
        return data

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Rollback on error."""
        logger.warning(f"StealthLayer rollback: {error}")

    async def initialize(self) -> bool:
        """Initialize stealth components."""
        try:
            logger.info("🚀 Initializing StealthLayer...")
            self._generate_fingerprint()
            self._generate_evasion_scripts()
            self._behavior_simulator = BehaviorSimulator()
            logger.info("✅ StealthLayer initialized successfully")
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"❌ StealthLayer initialization failed: {e}")
            return False

    def _generate_fingerprint(self) -> FingerprintProfile:
        """Generate browser fingerprint."""
        profile = self._profile_generator.generate()
        self._fingerprint_randomizer = profile
        return profile

    def _generate_evasion_scripts(self) -> list[str]:
        """Generate evasion scripts based on profile."""
        scripts = []
        profile = self._fingerprint_randomizer
        if profile is None:
            profile = self._profile_generator.get()

        # Canvas noise
        noise = profile.canvas_noise
        scripts.append(f"""
(function() {{
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {{
        const ctx = this.getContext('2d');
        if (ctx) {{
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) {{
                imageData.data[i] += {noise[0]};
                imageData.data[i+1] += {noise[1]};
                imageData.data[i+2] += {noise[2]};
            }}
            ctx.putImageData(imageData, 0, 0);
        }}
        return originalToDataURL.apply(this, arguments);
    }};
}})();
""")

        # WebGL spoofing
        scripts.append(f"""
(function() {{
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return '{profile.webgl_vendor}';
        if (param === 37446) return '{profile.webgl_renderer}';
        return getParameter.apply(this, arguments);
    }};
}})();
""")

        # Hide webdriver
        scripts.append("""
(function() {
    Object.defineProperty(navigator, 'webdriver', {
        get: function() { return false; },
        configurable: true
    });
    navigator.webdriver = false;
})();
""")

        self._evasion_scripts = scripts
        return scripts

    def get_timing_jitter(self) -> float:
        """Return random jitter delay for fetch timing."""
        return _RNG.gauss(0.5, 0.2)

    def get_random_delay(self) -> float:
        """Return random delay for behavior simulation."""
        return _RNG.uniform(0.3, 2.0)

    async def simulate_behavior(
        self,
        behavior_type: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Simulate human-like behavior."""
        if not self._behavior_simulator:
            self._behavior_simulator = BehaviorSimulator()

        if behavior_type == "mouse_move":
            target_x = kwargs.get("x", 100)
            target_y = kwargs.get("y", 100)
            await self._behavior_simulator.simulate_mouse_move(target_x, target_y)
        elif behavior_type == "click":
            x = kwargs.get("x")
            y = kwargs.get("y")
            await self._behavior_simulator.simulate_click(x, y)
        elif behavior_type == "scroll":
            direction = kwargs.get("direction", "down")
            amount = kwargs.get("amount")
            await self._behavior_simulator.simulate_scroll(direction, amount)
        elif behavior_type == "typing":
            text = kwargs.get("text", "")
            await self._behavior_simulator.simulate_typing(text)
        elif behavior_type == "reading":
            duration = kwargs.get("duration", 10.0)
            await self._behavior_simulator.simulate_reading(duration)

        return self._behavior_simulator.get_statistics()

    def get_fingerprint(self) -> FingerprintProfile | None:
        """Get current fingerprint profile."""
        return self._fingerprint_randomizer

    def rotate_fingerprint(self) -> FingerprintProfile:
        """Rotate to new fingerprint."""
        self._profile_generator.rotate()
        self._generate_fingerprint()
        self._generate_evasion_scripts()
        return self._fingerprint_randomizer

    def get_evasion_scripts(self) -> list[str]:
        """Get all evasion scripts."""
        return list(self._evasion_scripts)

    def get_statistics(self) -> dict[str, Any]:
        """Get stealth layer statistics."""
        return {
            "initialized": self._initialized,
            "fingerprint": {
                "has_profile": self._fingerprint_randomizer is not None,
                "platform": self._fingerprint_randomizer.platform if self._fingerprint_randomizer else None,
            },
            "evasion_scripts_count": len(self._evasion_scripts),
            "behavior": (self._behavior_simulator.get_statistics() if self._behavior_simulator else None),
        }

    async def cleanup(self) -> None:
        """Cleanup resources."""
        logger.info("🧹 Cleaning up StealthLayer...")
        self._initialized = False
        logger.info("✅ StealthLayer cleanup complete")


__all__ = [
    "StealthLayer",
    "BehaviorSimulator",
    "BehaviorPattern",
    "ProfileGenerator",
    "FingerprintProfile",
    "EvasionScript",
    "EvasionCategory",
]
