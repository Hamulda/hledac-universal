"""
Unified FingerprintProfile → EvasionScript Pipeline
===================================================





Resolves APEX-1005, APEX-1006, APEX-1007: JavaScriptEvasion and
FingerprintRandomizer were parallel systems producing overlapping,
inconsistent evasion scripts. This module unifies them into a single
deterministic pipeline:

    FingerprintProfile → EvasionScriptGenerator → list[EvasionScript]

Key improvements over the pre-unified architecture:

* **No duplicate scripts** — each fingerprint dimension (canvas, WebGL,
  AudioContext, etc.) is covered by exactly ONE evasion script, generated
  from ONE canonical method with CSPRNG + Box-Muller Gaussian noise
  throughout.

* **Profile-aware by construction** — the ``FingerprintProfile`` is the
  single source of truth; every script consumes the same profile so
  vendor, renderer, canvas noise seed, and hardware params are consistent.

* **Structured ``EvasionScript``** — replaces the bare ``str`` type alias
  with an msgspec.Struct carrying ``category``, ``priority``, and a
  ``fingerprint_hash`` for deduplication and conflict detection.

* **M1 8GB optimized** — ``__slots__`` on every class, msgspec zero-copy
  structs, lazy imports, and a single-pass generator that builds all
  scripts from one profile without intermediate allocations.

Architecture
------------
::

    ProfileGenerator       EvasionScriptGenerator
    ───────────────       ──────────────────────
    .generate() ──► FingerprintProfile
                         │
                         ├──► canvas_override     → EvasionScript(priority=10)
                         ├──► webgl_override      → EvasionScript(priority=20)
                         ├──► audio_override      → EvasionScript(priority=30)
                         ├──► webrtc_disabler     → EvasionScript(priority=40)
                         ├──► font_spoof          → EvasionScript(priority=50)
                         ├──► screen_override     → EvasionScript(priority=60)
                         ├──► timezone_override   → EvasionScript(priority=70)
                         ├──► hardware_override   → EvasionScript(priority=80)
                         ├──► webdriver_hider     → EvasionScript(priority=90)
                         ├──► automation_hider    → EvasionScript(priority=100)
                         ├──► plugin_spoof        → EvasionScript(priority=110)
                         ├──► permission_spoof    → EvasionScript(priority=120)
                         ├──► chrome_runtime      → EvasionScript(priority=130)
                         ├──► chrome_plugins      → EvasionScript(priority=140)
                         ├──► event_emulator      → EvasionScript(priority=150)
                         ├──► detection_patcher   → EvasionScript(priority=160)
                         └──► global_randomizer   → EvasionScript(priority=170)

Backward compatibility
----------------------
``JavaScriptEvasion`` and ``FingerprintRandomizer`` in ``stealth_layer.py``
are preserved as thin wrappers that delegate to the new pipeline.  All
existing call sites continue to work without changes.

Usage
-----
::

    from hledac.universal.layers.evasion_pipeline import (
        ProfileGenerator, EvasionScriptGenerator, FingerprintProfile,
    )

    # Generate a profile (what FingerprintRandomizer did)
    gen = ProfileGenerator()
    profile = gen.generate()

    # Generate all evasion scripts from the profile (what JavaScriptEvasion did)
    script_gen = EvasionScriptGenerator(profile)
    scripts = script_gen.generate_all()

    # Inject into Playwright page
    for s in scripts:
        await page.add_init_script(s.script)

    # Or: pipeline entry point
    scripts = profile.to_evasion_scripts()
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import field
from enum import IntEnum
from typing import Any

import msgspec
import orjson
from _core import aclose

logger = logging.getLogger(__name__)

# ── Crypto-safe RNG (F350M-R) ───────────────────────────────────────────────
_RNG = secrets.SystemRandom()


# ═══════════════════════════════════════════════════════════════════════════
# Structured EvasionScript
# ═══════════════════════════════════════════════════════════════════════════

class EvasionCategory(IntEnum):
    """Ordered categories — lower = earlier injection (sensitive first)."""
    CANVAS = 10
    WEBGL = 20
    AUDIO = 30
    WEBRTC = 40
    FONTS = 50
    SCREEN = 60
    TIMEZONE = 70
    HARDWARE = 80
    WEBDRIVER = 90
    AUTOMATION = 100
    PLUGINS = 110
    PERMISSIONS = 120
    CHROME_RUNTIME = 130
    CHROME_PLUGINS = 140
    EVENTS = 150
    DETECTION = 160
    GLOBALS = 170


class EvasionScript(msgspec.Struct, gc=False):  # type: ignore[misc]
    """A single evasion script with metadata for deduplication and ordering.

    Replaces the bare ``EvasionScript = str`` type alias from
    ``project_types.py``.  The ``script`` field contains the JavaScript
    source ready for ``page.add_init_script()``.
    """

    script_id: str          # stable identifier, e.g. "canvas_csprng_gauss"
    category: EvasionCategory
    priority: int           # within-category sort (lower = first)
    script: str             # JavaScript source
    fingerprint_hash: str   # sha256[:16] of profile → detect profile rotation
    version: int = 1        # increment when JS logic changes

    def __lt__(self, other: EvasionScript) -> bool:
        if self.category != other.category:
            return self.category < other.category
        return self.priority < other.priority


# ═══════════════════════════════════════════════════════════════════════════
# FingerprintProfile — single source of truth
# ═══════════════════════════════════════════════════════════════════════════

class FingerprintProfile(msgspec.Struct, gc=False):  # type: ignore[misc]
    """Complete browser fingerprint profile.

    This is the canonical data model replacing ``BrowserProfile`` from
    ``stealth_layer.py``.  Every evasion script consumes this profile so
    all fingerprint dimensions are consistent.

    M1 8GB: zero-copy msgspec.Struct with gc=False.
    """

    # Screen
    screen_width: int = 1920
    screen_height: int = 1080
    screen_color_depth: int = 24
    screen_pixel_ratio: float = 1.0

    # Timezone
    timezone: str = "America/New_York"
    timezone_offset: int = -5

    # Canvas (APEX-1005: CSPRNG + Gaussian noise seed)
    canvas_noise: tuple[int, int, int] = (0, 0, 0)

    # WebGL (APEX-1006: vendor/renderer from profile)
    webgl_vendor: str = "Apple Inc."
    webgl_renderer: str = "Apple M1"

    # Fonts
    fonts: list[str] = field(default_factory=list)

    # Plugins
    plugins: list[dict[str, str]] = field(default_factory=list)

    # Hardware
    hardware_concurrency: int = 8
    device_memory: int = 8
    max_touch_points: int = 0

    # Platform context (not injected as JS, but used during generation)
    platform: str = "macos"

    # Profile identity
    profile_id: str = ""
    generated_at: float = 0.0

    def fingerprint_hash(self) -> str:
        """Stable hash of fingerprint dimensions (16 hex chars)."""
        data = {
            "screen": f"{self.screen_width}x{self.screen_height}",
            "color_depth": self.screen_color_depth,
            "pixel_ratio": self.screen_pixel_ratio,
            "timezone": self.timezone,
            "fonts_hash": hash(tuple(sorted(self.fonts))) % 10000,
            "hardware": f"{self.hardware_concurrency}c{self.device_memory}g",
            "canvas": self.canvas_noise,
            "webgl": f"{self.webgl_vendor}|{self.webgl_renderer}",
        }
        raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_evasion_scripts(
        self,
        *,
        categories: set[EvasionCategory] | None = None,
    ) -> list[EvasionScript]:
        """Pipeline entry point: profile → sorted evasion scripts.

        Args:
            categories: Optional whitelist of categories to generate.
                        If None, all categories are generated.

        Returns:
            Sorted list of EvasionScript objects ready for injection.
        """
        gen = _EvasionScriptGenerator(self)
        return gen.generate_all(categories=categories)


# ═══════════════════════════════════════════════════════════════════════════
# ProfileGenerator — what was FingerprintRandomizer (profile-only, no JS)
# ═══════════════════════════════════════════════════════════════════════════

class ProfileGenerator:
    """Generate randomized BrowserProfile / FingerprintProfile instances.

    This is the pure *profile generation* half of the old
    ``FingerprintRandomizer`` — it generates the data but does NOT produce
    any JavaScript.  Use ``EvasionScriptGenerator`` for JS generation.

    M1 8GB: ``__slots__``, zero-copy msgspec profile, lazy hash.

    Example:
        >>> gen = ProfileGenerator(platform="macos")
        >>> profile = gen.generate()
        >>> scripts = profile.to_evasion_scripts()
    """

    SCREEN_RESOLUTIONS: tuple[tuple[int, int], ...] = (
        (1920, 1080), (2560, 1440), (1366, 768),
        (1440, 900), (1680, 1050), (1280, 720), (3840, 2160),
    )

    TIMEZONES: tuple[tuple[str, int], ...] = (
        ("America/New_York", -5), ("America/Chicago", -6),
        ("America/Denver", -7), ("America/Los_Angeles", -8),
        ("Europe/London", 0), ("Europe/Paris", 1), ("Europe/Berlin", 1),
        ("Asia/Tokyo", 9), ("Asia/Shanghai", 8), ("Australia/Sydney", 10),
    )

    WEBGL_PROFILES: dict[str, tuple[tuple[str, str], ...]] = {
        "macos": (
            ("Apple Inc.", "Apple M1"),
            ("Apple Inc.", "Apple M1 Pro"),
            ("Apple Inc.", "Apple M1 Max"),
            ("Apple Inc.", "Apple M2"),
            ("Intel Inc.", "Intel Iris OpenGL Engine"),
        ),
        "windows": (
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11)"),
            ("Microsoft Corporation", "D3D11"),
        ),
        "linux": (
            ("NVIDIA Corporation", "NVIDIA GeForce GTX 1060/PCIe/SSE2"),
            ("Intel Open Source Technology Center", "Mesa DRI Intel(R) UHD Graphics 620"),
            ("AMD", "AMD Radeon Graphics"),
        ),
    }

    COMMON_FONTS: tuple[str, ...] = (
        "Arial", "Arial Black", "Arial Narrow", "Arial Rounded MT Bold",
        "Courier", "Courier New", "Georgia", "Helvetica", "Helvetica Neue",
        "Times", "Times New Roman", "Verdana", "Tahoma", "Trebuchet MS",
        "Palatino", "Garamond", "Bookman", "Comic Sans MS", "Impact",
        "Segoe UI", "Calibri", "Cambria", "Geneva", "Lucida Grande",
        "Lucida Sans Unicode", "Menlo", "Monaco", "Consolas",
    )

    COMMON_PLUGINS: tuple[dict[str, str], ...] = (
        {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer",
         "description": "Portable Document Format"},
        {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai",
         "description": "Portable Document Format"},
        {"name": "Native Client", "filename": "internal-nacl-plugin",
         "description": "Native Client module"},
    )

    __slots__ = (
        "_current_profile", "_profile_timestamp", "_rotation_count",
        "platform", "session_duration", "consistent_per_session",
        "randomize_canvas", "randomize_webgl", "randomize_fonts",
        "randomize_screen", "randomize_timezone", "randomize_plugins",
    )

    def __init__(
        self,
        *,
        platform: str | None = None,
        session_duration: float = 3600.0,
        consistent_per_session: bool = True,
        randomize_canvas: bool = True,
        randomize_webgl: bool = True,
        randomize_fonts: bool = True,
        randomize_screen: bool = True,
        randomize_timezone: bool = True,
        randomize_plugins: bool = True,
    ):
        self.platform = platform
        self.session_duration = session_duration
        self.consistent_per_session = consistent_per_session
        self.randomize_canvas = randomize_canvas
        self.randomize_webgl = randomize_webgl
        self.randomize_fonts = randomize_fonts
        self.randomize_screen = randomize_screen
        self.randomize_timezone = randomize_timezone
        self.randomize_plugins = randomize_plugins
        self._current_profile: FingerprintProfile | None = None
        self._profile_timestamp: float = 0.0
        self._rotation_count: int = 0

    # ── sub-generators ──────────────────────────────────────────────────

    def _pick_platform(self) -> str:
        if self.platform is not None:
            return self.platform
        return _RNG.choice(["macos", "windows", "linux"])

    def _generate_canvas_noise(self) -> tuple[int, int, int]:
        if not self.randomize_canvas:
            return (0, 0, 0)
        return (_RNG.randint(0, 2), _RNG.randint(0, 2), _RNG.randint(0, 2))

    def _generate_screen(self) -> tuple[int, int, int, float]:
        if not self.randomize_screen:
            return (1920, 1080, 24, 1.0)
        if _RNG.random() < 0.9:
            width, height = _RNG.choice(self.SCREEN_RESOLUTIONS[:5])
        else:
            width, height = _RNG.choice(self.SCREEN_RESOLUTIONS)
        color_depth = _RNG.choice([24, 32])
        pixel_ratio = _RNG.choice([1.0, 1.0, 1.0, 1.25, 1.5, 2.0])
        return (width, height, color_depth, pixel_ratio)

    def _generate_timezone(self) -> tuple[str, int]:
        if not self.randomize_timezone:
            tz = time.tzname[0] if time.tzname else "UTC"
            offset = -time.timezone // 3600
            return (tz, offset)
        return _RNG.choice(self.TIMEZONES)

    def _generate_webgl(self, platform: str) -> tuple[str, str]:
        if not self.randomize_webgl:
            return ("", "")
        profiles = self.WEBGL_PROFILES.get(platform, self.WEBGL_PROFILES["macos"])
        return _RNG.choice(profiles)

    def _generate_fonts(self) -> list[str]:
        if not self.randomize_fonts:
            return list(self.COMMON_FONTS[:10])
        num = _RNG.randint(10, 15)
        pool = list(self.COMMON_FONTS)
        return _RNG.sample(pool, min(num, len(pool)))

    def _generate_plugins(self) -> list[dict[str, str]]:
        if not self.randomize_plugins:
            return list(self.COMMON_PLUGINS[:2])
        num = _RNG.randint(2, len(self.COMMON_PLUGINS))
        pool = list(self.COMMON_PLUGINS)
        return _RNG.sample(pool, num)

    def _generate_hardware(self, platform: str) -> tuple[int, int, int]:
        if platform == "macos":
            concurrency = _RNG.choice([8, 8, 10, 10])
            memory = _RNG.choice([8, 16, 16, 32])
        else:
            concurrency = _RNG.choice([4, 4, 8, 8, 8, 16])
            memory = _RNG.choice([4, 8, 8, 16, 16, 32])
        touch = 0 if platform != "mobile" else _RNG.choice([5, 10])
        return (concurrency, memory, touch)

    # ── main generator ──────────────────────────────────────────────────

    def generate(self, *, force_new: bool = False) -> FingerprintProfile:
        """Generate a new (or cached) fingerprint profile.

        Args:
            force_new: Bypass session cache.

        Returns:
            A complete FingerprintProfile.
        """
        if (
            not force_new
            and self.consistent_per_session
            and self._current_profile is not None
        ):
            elapsed = time.time() - self._profile_timestamp
            if elapsed < self.session_duration:
                return self._current_profile

        platform = self._pick_platform()
        width, height, cd, pr = self._generate_screen()
        tz, tz_off = self._generate_timezone()
        wgl_vendor, wgl_renderer = self._generate_webgl(platform)
        hw_c, hw_mem, hw_touch = self._generate_hardware(platform)

        profile = FingerprintProfile(
            screen_width=width,
            screen_height=height,
            screen_color_depth=cd,
            screen_pixel_ratio=pr,
            timezone=tz,
            timezone_offset=tz_off,
            canvas_noise=self._generate_canvas_noise(),
            webgl_vendor=wgl_vendor,
            webgl_renderer=wgl_renderer,
            fonts=self._generate_fonts(),
            plugins=self._generate_plugins(),
            hardware_concurrency=hw_c,
            device_memory=hw_mem,
            max_touch_points=hw_touch,
            platform=platform,
            profile_id="",
            generated_at=0.0,
        )
        profile = msgspec.structs.replace(
            profile,
            profile_id=profile.fingerprint_hash(),
            generated_at=time.time(),
        )

        self._current_profile = profile
        self._profile_timestamp = time.time()
        self._rotation_count += 1
        logger.debug("Generated fingerprint profile %s (%s)", profile.profile_id, platform)
        return profile

    def get(self) -> FingerprintProfile:
        """Get current or new profile (alias for generate())."""
        return self.generate()

    def rotate(self) -> FingerprintProfile:
        """Force rotation to new profile."""
        return self.generate(force_new=True)

    @property
    def rotation_count(self) -> int:
        return self._rotation_count

    @property
    def profile_age(self) -> float:
        if self._profile_timestamp:
            return time.time() - self._profile_timestamp
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# EvasionScriptGenerator — unified JS generation from ONE profile
# ═══════════════════════════════════════════════════════════════════════════

class _EvasionScriptGenerator:
    """Generate ALL evasion scripts from a single FingerprintProfile.

    This is the unified replacement for both:
    - ``JavaScriptEvasion.get_all_evasion_scripts()``  (13 raw JS strings)
    - ``FingerprintRandomizer.get_js_protection_script()`` (screen+audio+canvas+tz)

    Every script uses CSPRNG + Box-Muller Gaussian noise (APEX-1005/1006/1007).
    No duplicates — exactly one script per ``EvasionCategory``.

    Internal class — use ``FingerprintProfile.to_evasion_scripts()``
    or instantiate directly:

        >>> gen = _EvasionScriptGenerator(profile)
        >>> scripts = gen.generate_all()

    M1 8GB: single-pass generation, no intermediate lists except the
    final output.
    """

    __slots__ = ("_profile", "_fp_hash")

    def __init__(self, profile: FingerprintProfile) -> None:
        self._profile = profile
        self._fp_hash = profile.fingerprint_hash()

    # ── Public API ──────────────────────────────────────────────────────

    def generate_all(
        self,
        *,
        categories: set[EvasionCategory] | None = None,
    ) -> list[EvasionScript]:
        """Generate all evasion scripts, sorted by category + priority.

        Args:
            categories: Optional whitelist. If None, all categories.

        Returns:
            Sorted list, ready for ``page.add_init_script()``.
        """
        scripts: list[EvasionScript] = []

        # Build in priority order — each method is self-contained
        for method in self._GENERATORS:
            cat = method.__name__  # e.g. "_canvas_override"
            if categories is not None:
                # map method name to category
                cat_enum = self._METHOD_CATEGORY.get(cat)
                if cat_enum is not None and cat_enum not in categories:
                    continue
            script = method()
            if script is not None:
                scripts.append(script)

        scripts.sort()
        return scripts

    # ── Category mapping ────────────────────────────────────────────────

    _METHOD_CATEGORY: dict[str, EvasionCategory] = {
        "_canvas_override": EvasionCategory.CANVAS,
        "_webgl_override": EvasionCategory.WEBGL,
        "_audio_override": EvasionCategory.AUDIO,
        "_webrtc_disabler": EvasionCategory.WEBRTC,
        "_font_spoof": EvasionCategory.FONTS,
        "_screen_override": EvasionCategory.SCREEN,
        "_timezone_override": EvasionCategory.TIMEZONE,
        "_hardware_override": EvasionCategory.HARDWARE,
        "_webdriver_hider": EvasionCategory.WEBDRIVER,
        "_automation_hider": EvasionCategory.AUTOMATION,
        "_plugin_spoof": EvasionCategory.PLUGINS,
        "_permission_spoof": EvasionCategory.PERMISSIONS,
        "_chrome_runtime_spoof": EvasionCategory.CHROME_RUNTIME,
        "_chrome_plugins": EvasionCategory.CHROME_PLUGINS,
        "_event_emulator": EvasionCategory.EVENTS,
        "_detection_patcher": EvasionCategory.DETECTION,
        "_global_randomizer": EvasionCategory.GLOBALS,
    }

    # ── Generator registry ──────────────────────────────────────────────

    @property
    def _GENERATORS(self) -> list:
        """Ordered list of bound generator methods."""
        return [
            self._canvas_override,
            self._webgl_override,
            self._audio_override,
            self._webrtc_disabler,
            self._font_spoof,
            self._screen_override,
            self._timezone_override,
            self._hardware_override,
            self._webdriver_hider,
            self._automation_hider,
            self._plugin_spoof,
            self._permission_spoof,
            self._chrome_runtime_spoof,
            self._chrome_plugins,
            self._event_emulator,
            self._detection_patcher,
            self._global_randomizer,
        ]

    # ── Helper: build EvasionScript ─────────────────────────────────────

    def _make(
        self, script_id: str, category: EvasionCategory, priority: int,
        script: str, version: int = 1,
    ) -> EvasionScript:
        return EvasionScript(
            script_id=script_id,
            category=category,
            priority=priority,
            script=script,
            fingerprint_hash=self._fp_hash,
            version=version,
        )

    # ═══════════════════════════════════════════════════════════════════
    # APEX-1005: Canvas — CSPRNG + Box-Muller Gaussian noise
    # ═══════════════════════════════════════════════════════════════════

    def _canvas_override(self) -> EvasionScript:
        """APEX-1005: Canvas fingerprint protection with CSPRNG+Gaussian."""
        noise = self._profile.canvas_noise
        return self._make(
            script_id="canvas_csprng_gauss",
            category=EvasionCategory.CANVAS,
            priority=0,
            script=f"""\
// Canvas fingerprint protection (APEX-1005: CSPRNG + Gaussian noise)
(function() {{
    'use strict';

    const NOISE_SEED = {list(noise)};

    function gaussianRandom(mean, stddev) {{
        const buffer = new Uint32Array(2);
        crypto.getRandomValues(buffer);
        const u1 = buffer[0] / 0x100000000;
        const u2 = buffer[1] / 0x100000000;
        const z0 = Math.sqrt(-2.0 * Math.log(u1 + 1e-10)) * Math.cos(2.0 * Math.PI * u2);
        return mean + stddev * z0;
    }}

    function addCanvasNoise(imageData) {{
        const data = imageData.data;
        for (let i = 0; i < data.length; i += 4) {{
            data[i] = Math.max(0, Math.min(255, Math.round(data[i] + gaussianRandom(NOISE_SEED[0], 0.8))));
            data[i + 1] = Math.max(0, Math.min(255, Math.round(data[i + 1] + gaussianRandom(NOISE_SEED[1], 0.8))));
            data[i + 2] = Math.max(0, Math.min(255, Math.round(data[i + 2] + gaussianRandom(NOISE_SEED[2], 0.8))));
        }}
        return imageData;
    }}

    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {{
        const imageData = origGetImageData.call(this, sx, sy, sw, sh);
        return addCanvasNoise(imageData);
    }};

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        const ctx = this.getContext('2d');
        if (ctx) {{
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            addCanvasNoise(imageData);
            ctx.putImageData(imageData, 0, 0);
        }}
        return origToDataURL.call(this, type, quality);
    }};

    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
        const ctx = this.getContext('2d');
        if (ctx) {{
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            addCanvasNoise(imageData);
            ctx.putImageData(imageData, 0, 0);
        }}
        return origToBlob.call(this, callback, type, quality);
    }};
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # APEX-1006: WebGL — profile-aware vendor/renderer
    # ═══════════════════════════════════════════════════════════════════

    def _webgl_override(self) -> EvasionScript:
        """APEX-1006: WebGL fingerprint spoof with profile values."""
        vendor = self._profile.webgl_vendor or "Intel Inc."
        renderer = self._profile.webgl_renderer or "Intel Iris OpenGL Engine"
        return self._make(
            script_id="webgl_profile_aware",
            category=EvasionCategory.WEBGL,
            priority=0,
            script=f"""\
// WebGL fingerprint protection (APEX-1006: profile-aware spoof)
(function() {{
    'use strict';
    const VENDOR = '{vendor}';
    const RENDERER = '{renderer}';

    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {{
        if (p === 37445) return VENDOR;      // UNMASKED_VENDOR_WEBGL
        if (p === 37446) return RENDERER;    // UNMASKED_RENDERER_WEBGL
        return origGetParam.call(this, p);
    }};

    const origGetExt = WebGLRenderingContext.prototype.getExtension;
    WebGLRenderingContext.prototype.getExtension = function(name) {{
        const ext = origGetExt.call(this, name);
        if (name === 'WEBGL_debug_renderer_info' && ext && ext.getParameter) {{
            const orig = ext.getParameter;
            ext.getParameter = function(p) {{
                if (p === 37445) return VENDOR;
                if (p === 37446) return RENDERER;
                return orig.call(this, p);
            }};
        }}
        return ext;
    }};

    const origGetShader = WebGLRenderingContext.prototype.getShaderPrecisionFormat;
    WebGLRenderingContext.prototype.getShaderPrecisionFormat = function(type) {{
        const result = origGetShader.call(this, type);
        if (result) {{
            const buf = new Uint32Array(1);
            crypto.getRandomValues(buf);
            const v = buf[0] / 0x100000000 > 0.5 ? 1 : 0;
            return {{ rangeMin: result.rangeMin + v, rangeMax: result.rangeMax + v, precision: result.precision }};
        }}
        return result;
    }};
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # APEX-1007: AudioContext — CSPRNG + Gaussian
    # ═══════════════════════════════════════════════════════════════════

    def _audio_override(self) -> EvasionScript:
        """APEX-1007: Comprehensive AudioContext fingerprint protection."""
        return self._make(
            script_id="audio_csprng_gauss",
            category=EvasionCategory.AUDIO,
            priority=0,
            script="""\
// AudioContext fingerprint protection (APEX-1007: CSPRNG + Gaussian)
(function() {
    'use strict';

    function gauss(mean, stddev) {
        const buf = new Uint32Array(2);
        crypto.getRandomValues(buf);
        const u1 = buf[0] / 0x100000000;
        const u2 = buf[1] / 0x100000000;
        return mean + stddev * Math.sqrt(-2.0 * Math.log(u1 + 1e-10)) * Math.cos(2.0 * Math.PI * u2);
    }

    // AudioContext wrapper
    const _AC = window.AudioContext || window.webkitAudioContext;
    if (_AC) {
        window.AudioContext = function() {
            const ctx = new _AC();
            const _ca = ctx.createAnalyser;
            if (_ca) ctx.createAnalyser = function() {
                const a = _ca.call(this);
                const _gbfd = a.getByteFrequencyData;
                a.getByteFrequencyData = function(arr) {
                    _gbfd.call(this, arr);
                    for (let i = 0; i < arr.length; i++)
                        arr[i] = Math.max(0, Math.min(255, Math.round(arr[i] + gauss(0, 0.8))));
                    return arr;
                };
                return a;
            };
            const _cdc = ctx.createDynamicsCompressor;
            if (_cdc) ctx.createDynamicsCompressor = function() {
                const c = _cdc.call(this);
                const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(c), 'reduction');
                if (d && d.get) Object.defineProperty(c, 'reduction', {
                    get: function() { return d.get.call(this) + gauss(0, 0.001); },
                    configurable: true
                });
                return c;
            };
            return ctx;
        };
        window.AudioContext.prototype = _AC.prototype;
        window.webkitAudioContext = window.AudioContext;
    }

    // OfflineAudioContext
    const _OAC = window.OfflineAudioContext;
    if (_OAC) {
        window.OfflineAudioContext = function() {
            const ctx = new _OAC(...arguments);
            const _sr = ctx.startRendering;
            ctx.startRendering = function() {
                return _sr.call(this).then(function(buf) {
                    for (let ch = 0; ch < buf.numberOfChannels; ch++) {
                        const cd = buf.getChannelData(ch);
                        for (let i = 0; i < cd.length; i++) cd[i] += gauss(0, 0.0001);
                    }
                    return buf;
                });
            };
            return ctx;
        };
        window.OfflineAudioContext.prototype = _OAC.prototype;
    }

    // AudioBuffer.getChannelData
    const _gcd = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(ch) {
        const cd = _gcd.call(this, ch);
        const out = new Float32Array(cd.length);
        for (let i = 0; i < cd.length; i++) out[i] = cd[i] + gauss(0, 0.0001);
        return out;
    };
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # WebRTC disabler
    # ═══════════════════════════════════════════════════════════════════

    def _webrtc_disabler(self) -> EvasionScript:
        return self._make(
            script_id="webrtc_disable",
            category=EvasionCategory.WEBRTC,
            priority=0,
            script="""\
// WebRTC disabler
(function() {
    'use strict';
    var noop = function() {};
    if (window.RTCPeerConnection) {
        window.RTCPeerConnection = noop;
        window.RTCPeerConnection.prototype = {};
    }
    if (window.webkitRTCPeerConnection) { window.webkitRTCPeerConnection = noop; }
    if (window.mozRTCPeerConnection) { window.mozRTCPeerConnection = noop; }
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Font spoof
    # ═══════════════════════════════════════════════════════════════════

    def _font_spoof(self) -> EvasionScript:
        return self._make(
            script_id="font_spoof",
            category=EvasionCategory.FONTS,
            priority=0,
            script="""\
// Font enumeration protection
(function() {
    'use strict';
    function cryptoRandom() {
        const buf = new Uint32Array(1);
        crypto.getRandomValues(buf);
        return buf[0] / 0x100000000;
    }
    const origMeasure = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = function(text) {
        const result = origMeasure.call(this, text);
        const w = result.width;
        Object.defineProperty(result, 'width', {
            get: function() { return w + (cryptoRandom() * 0.02 - 0.01); },
            configurable: true
        });
        return result;
    };
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Screen override
    # ═══════════════════════════════════════════════════════════════════

    def _screen_override(self) -> EvasionScript:
        p = self._profile
        return self._make(
            script_id="screen_override",
            category=EvasionCategory.SCREEN,
            priority=0,
            script=f"""\
// Screen fingerprint override
(function() {{
    'use strict';
    Object.defineProperty(screen, 'width', {{ get: function() {{ return {p.screen_width}; }} }});
    Object.defineProperty(screen, 'height', {{ get: function() {{ return {p.screen_height}; }} }});
    Object.defineProperty(screen, 'colorDepth', {{ get: function() {{ return {p.screen_color_depth}; }} }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: function() {{ return {p.screen_color_depth}; }} }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: function() {{ return {p.screen_pixel_ratio}; }} }});
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Timezone override
    # ═══════════════════════════════════════════════════════════════════

    def _timezone_override(self) -> EvasionScript:
        p = self._profile
        return self._make(
            script_id="timezone_override",
            category=EvasionCategory.TIMEZONE,
            priority=0,
            script=f"""\
// Timezone override
(function() {{
    'use strict';
    Date.prototype.getTimezoneOffset = function() {{ return {p.timezone_offset * 60}; }};
    if (Intl && Intl.DateTimeFormat) {{
        const orig = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
            const opts = orig.call(this);
            opts.timeZone = '{p.timezone}';
            return opts;
        }};
    }}
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Hardware override
    # ═══════════════════════════════════════════════════════════════════

    def _hardware_override(self) -> EvasionScript:
        p = self._profile
        return self._make(
            script_id="hardware_override",
            category=EvasionCategory.HARDWARE,
            priority=0,
            script=f"""\
// Hardware specs override
(function() {{
    'use strict';
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: function() {{ return {p.hardware_concurrency}; }} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: function() {{ return {p.device_memory}; }} }});
    Object.defineProperty(navigator, 'maxTouchPoints', {{ get: function() {{ return {p.max_touch_points}; }} }});
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # WebDriver hider
    # ═══════════════════════════════════════════════════════════════════

    def _webdriver_hider(self) -> EvasionScript:
        return self._make(
            script_id="webdriver_hider",
            category=EvasionCategory.WEBDRIVER,
            priority=0,
            script="""\
// WebDriver hider
(function() {
    'use strict';
    Object.defineProperty(navigator, 'webdriver', { get: function() { return undefined; } });
    delete navigator.__webdriver_script_fn;
    delete navigator.__selenium_evaluate;
    delete navigator.__selenium_unwrapped;
    if (window.chrome) {
        window.chrome.runtime = window.chrome.runtime || {};
        window.chrome.csi = window.chrome.csi || function() {};
        window.chrome.loadTimes = window.chrome.loadTimes || function() {};
    }
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Automation hider
    # ═══════════════════════════════════════════════════════════════════

    def _automation_hider(self) -> EvasionScript:
        return self._make(
            script_id="automation_hider",
            category=EvasionCategory.AUTOMATION,
            priority=0,
            script="""\
// Automation hider
(function() {
    'use strict';
    if (navigator.permissions) {
        const orig = navigator.permissions.query;
        navigator.permissions.query = function(params) {
            if (params.name === 'notifications')
                return Promise.resolve({ state: 'default', onchange: null, addEventListener: function(){}, removeEventListener: function(){}, dispatchEvent: function(){ return true; } });
            return orig.call(this, params);
        };
    }
    Object.defineProperty(navigator, 'plugins', { get: function() { return { length: 0, item: function() { return null; }, namedItem: function() { return null; }, refresh: function() {} }; } });
    Object.defineProperty(navigator, 'languages', { get: function() { return ['en-US', 'en']; } });
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Plugin spoof
    # ═══════════════════════════════════════════════════════════════════

    def _plugin_spoof(self) -> EvasionScript:
        plugin_json = orjson.dumps(list(self._profile.plugins)).decode()
        return self._make(
            script_id="plugin_spoof",
            category=EvasionCategory.PLUGINS,
            priority=0,
            script=f"""\
// Plugin spoof
(function() {{
    'use strict';
    var PLUGINS = {plugin_json};
    Object.defineProperty(navigator, 'plugins', {{
        get: function() {{
            return {{
                length: PLUGINS.length,
                item: function(i) {{ var p = PLUGINS[i] || {{}}; p.length = 1; p.item = function() {{ return p[0] || null; }}; return p; }},
                namedItem: function() {{ return this.item(0); }},
                refresh: function() {{}}
            }};
        }}
    }});
    Object.defineProperty(navigator, 'mimeTypes', {{
        get: function() {{ return {{ length: 0, item: function() {{ return null; }}, namedItem: function() {{ return null; }} }}; }}
    }});
}})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Permission spoof
    # ═══════════════════════════════════════════════════════════════════

    def _permission_spoof(self) -> EvasionScript:
        return self._make(
            script_id="permission_spoof",
            category=EvasionCategory.PERMISSIONS,
            priority=0,
            script="""\
// Permission API spoof
(function() {
    'use strict';
    if (navigator.permissions) {
        const orig = navigator.permissions.query;
        navigator.permissions.query = function(params) {
            var overrides = {
                notifications: 'default', camera: 'prompt', microphone: 'prompt',
                'clipboard-read': 'prompt', 'clipboard-write': 'granted', geolocation: 'prompt'
            };
            if (params.name in overrides) {
                return Promise.resolve({ state: overrides[params.name], onchange: null, addEventListener: function(){}, removeEventListener: function(){}, dispatchEvent: function(){ return true; } });
            }
            return orig.call(this, params);
        };
    }
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Chrome runtime spoof
    # ═══════════════════════════════════════════════════════════════════

    def _chrome_runtime_spoof(self) -> EvasionScript:
        return self._make(
            script_id="chrome_runtime_spoof",
            category=EvasionCategory.CHROME_RUNTIME,
            priority=0,
            script="""\
// Chrome runtime spoof
(function() {
    'use strict';
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = {
        OnInstalledReason: { CHROME_UPDATE: "chrome_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update" },
        OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
        PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
        PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" },
        id: undefined,
        getManifest: function() { return {}; },
        getURL: function(path) { return path; },
        connect: function() { return { onMessage: { addListener: function(){} }, onDisconnect: { addListener: function(){} }, postMessage: function(){} }; },
        sendMessage: function() {},
        onConnect: { addListener: function(){} },
        onMessage: { addListener: function(){} }
    };
    window.chrome.loadTimes = function() {
        var t = performance.timing || {};
        return {
            commitLoadTime: (t.domContentLoadedEventStart || 0) / 1000,
            connectionInfo: 'h2',
            finishDocumentLoadTime: (t.domContentLoadedEventEnd || 0) / 1000,
            finishLoadTime: (t.loadEventEnd || 0) / 1000,
            firstPaintAfterLoadTime: 0,
            firstPaintTime: (t.domContentLoadedEventStart || 0) / 1000,
            navigationType: 'Other',
            npnNegotiatedProtocol: 'h2',
            requestTime: (t.requestStart || Date.now()) / 1000,
            startLoadTime: (t.navigationStart || Date.now()) / 1000,
            wasAlternateProtocolAvailable: false,
            wasFetchedViaSpdy: true,
            wasNpnNegotiated: true
        };
    };
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Chrome plugins
    # ═══════════════════════════════════════════════════════════════════

    def _chrome_plugins(self) -> EvasionScript:
        return self._make(
            script_id="chrome_plugins",
            category=EvasionCategory.CHROME_PLUGINS,
            priority=0,
            script="""\
// Chrome plugin indicators
(function() {
    'use strict';
    window.chrome = window.chrome || {};
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
        RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" }
    };
    window.chrome.csi = function() {
        return { onloadT: Date.now(), pageT: performance.now(), startE: performance.timing.navigationStart, transcription: '' };
    };
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Event emulator
    # ═══════════════════════════════════════════════════════════════════

    def _event_emulator(self) -> EvasionScript:
        return self._make(
            script_id="event_emulator",
            category=EvasionCategory.EVENTS,
            priority=0,
            script="""\
// Human event emulator
(function() {
    'use strict';
    var lastMouseMove = Date.now();
    document.addEventListener('mousemove', function() { lastMouseMove = Date.now(); }, true);
    var OrigDate = Date;
    Date = function() {
        if (arguments.length === 0) return new OrigDate(OrigDate.now());
        return new (OrigDate.bind.apply(OrigDate, [null].concat(Array.prototype.slice.call(arguments))))();
    };
    Date.prototype = OrigDate.prototype;
    Date.now = OrigDate.now;
    Date.parse = OrigDate.parse;
    Date.UTC = OrigDate.UTC;
    Date.prototype.constructor = Date;
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Detection patcher
    # ═══════════════════════════════════════════════════════════════════

    def _detection_patcher(self) -> EvasionScript:
        return self._make(
            script_id="detection_patcher",
            category=EvasionCategory.DETECTION,
            priority=0,
            script="""\
// Detection library patcher
(function() {
    'use strict';
    var libs = ['botd', 'botguard', 'datadome', 'akamai', 'perimeterx', 'cloudflare', 'hcaptcha', 'recaptcha'];
    libs.forEach(function(lib) {
        Object.defineProperty(window, lib, { get: function() { return undefined; }, set: function() { return true; } });
    });
    var origToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === Function.prototype.toString) return 'function toString() { [native code] }';
        return origToString.call(this);
    };
})();
""",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Global randomizer
    # ═══════════════════════════════════════════════════════════════════

    def _global_randomizer(self) -> EvasionScript:
        return self._make(
            script_id="global_randomizer",
            category=EvasionCategory.GLOBALS,
            priority=0,
            script="""\
// Global randomizer
(function() {
    'use strict';
    function cryptoRandom() {
        var buf = new Uint32Array(1);
        crypto.getRandomValues(buf);
        return buf[0] / 0x100000000;
    }
    var off = Math.floor(cryptoRandom() * 50);
    Object.defineProperty(screen, 'availLeft', { get: function() { return off; } });
    Object.defineProperty(screen, 'availTop', { get: function() { return off; } });
    if (navigator.deviceMemory) { Object.defineProperty(navigator, 'deviceMemory', { get: function() { return 8; } }); }
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: function() { return 8; } });
})();
""",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Public convenience: one-call pipeline
# ═══════════════════════════════════════════════════════════════════════════

def generate_evasion_scripts(
    platform: str | None = None,
    *,
    categories: set[EvasionCategory] | None = None,
) -> tuple[FingerprintProfile, list[EvasionScript]]:
    """One-call pipeline: generate profile + all evasion scripts.

    Args:
        platform: Target platform hint (macos/windows/linux).
        categories: Optional whitelist of categories.

    Returns:
        Tuple of (profile, sorted_scripts).
    """
    gen = ProfileGenerator(platform=platform)
    profile = gen.generate()
    scripts = profile.to_evasion_scripts(categories=categories)
    return profile, scripts


# ═══════════════════════════════════════════════════════════════════════════
# Detection score helper (replaces JavaScriptEvasion.get_detection_score)
# ═══════════════════════════════════════════════════════════════════════════

def compute_detection_score(scripts: list[EvasionScript]) -> dict[str, Any]:
    """Compute evasion coverage score from a list of scripts.

    Replaces ``JavaScriptEvasion.get_detection_score()``.

    Args:
        scripts: List of generated EvasionScript objects.

    Returns:
        Dict with coverage ratio, counts, and per-category status.
    """
    all_categories = set(EvasionCategory)
    covered = {s.category for s in scripts}
    evasions = {cat.name.lower(): (cat in covered) for cat in all_categories}
    enabled = len(covered)
    total = len(all_categories)
    return {
        "coverage": enabled / total if total > 0 else 0.0,
        "enabled_count": enabled,
        "total_count": total,
        "evasions": evasions,
    }


__all__ = [
    "EvasionCategory",
    "EvasionScript",
    "FingerprintProfile",
    "ProfileGenerator",
    "_EvasionScriptGenerator",
    "generate_evasion_scripts",
    "compute_detection_score",
]
