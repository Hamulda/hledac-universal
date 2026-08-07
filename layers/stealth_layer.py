"""
Stealth Layer - Stealth Browsing, Detection Evasion, CAPTCHA Solving
====================================================================














Integrates:
- StealthBrowser: Playwright wrapper with anti-detection
- DetectionEvader: 10+ evasion scripts, behavior simulation
- CaptchaSolver: Multi-provider CAPTCHA solving
- BehaviorSimulator: Human-like behavior simulation with Bézier curves
- Chameleon: Process masquerading and anti-debugging (macOS M1)

This is a thin wrapper that imports existing stealth modules
and adds integration logic for the universal orchestrator.

Architecture (Issue 6.3):
- 5 concerns decomposed into StealthStrategy Protocol → layers/stealth_strategies.py
- CaptchaSolvingStrategy: 2captcha API (primary) + Vision/CoreML (secondary)
- Local OCR (torch/transformers) OFF BY DEFAULT — enable HLEDAC_ENABLE_CAPTCHA_LOCAL=1
- AdvancedCaptchaSolver: lazy-loaded only when HLEDAC_ENABLE_CAPTCHA_LOCAL=1

CAPTCHA SOLVER OVERLAP NOTE (F360):
    This module's ``AdvancedCaptchaSolver`` (lines 81-467) is a *parallel
    implementation* to ``VisionCaptchaSolver`` in ``security/captcha_solver.py``.
    See that module's docstring for a full comparison table.  The
    ``stealth_layer`` path is canonical when ``HLEDAC_ENABLE_STEALTH_LAYER=1``.
"""
import asyncio
import logging
import math
import secrets
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import msgspec
import orjson
from hledac.universal.project_types import BrowserType, CaptchaSolution, CaptchaType, RiskLevel, StealthConfig, StealthSession
logger = logging.getLogger(__name__)

def _gauss(mu: float, sigma: float) -> float:
    """Box-Muller transform for Gaussian random numbers using crypto-safe RNG."""
    u1 = _RNG.random()
    u2 = _RNG.random()
    z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z0

class CaptchaSolverConfig(msgspec.Struct, gc=False):
    """Configuration for self-hosted CAPTCHA solving"""
    ocr_model: str = 'microsoft/trocr-small-printed'
    use_mlx: bool = True
    max_image_size: int = 640
    enable_image_ocr: bool = True
    enable_text_logic: bool = True
    enable_rotation_detection: bool = True
    timeout_seconds: float = 30.0
    confidence_threshold: float = 0.6

class CaptchaResult(msgspec.Struct, gc=False):
    """Result of CAPTCHA solving attempt"""
    success: bool
    solution: str | None
    confidence: float
    processing_time_ms: float
    method: str
    alternative_solutions: list[str] = field(default_factory=list)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

class AdvancedCaptchaSolver:
    """
    Self-hosted CAPTCHA solver optimized for M1 8GB.

    Solves common CAPTCHA types without external APIs:
    - Image-based text CAPTCHAs (OCR)
    - Simple logic puzzles (math, sequence)
    - Rotation-based challenges
    - Distorted text with noise

    M1 Optimized:
    - Uses lightweight models (<100MB)
    - MLX acceleration when available
    - Streaming image processing
    - Aggressive memory cleanup

    Example:
        >>> solver = AdvancedCaptchaSolver(config)
        >>> await solver.initialize()
        >>> result = await solver.solve_image_captcha(image_bytes)
        >>> print(f"Solution: {result.solution} (confidence: {result.confidence})")
    """
    MATH_PATTERNS = [('(\\d+)\\s*\\+\\s*(\\d+)', lambda a, b: int(a) + int(b)), ('(\\d+)\\s*-\\s*(\\d+)', lambda a, b: int(a) - int(b)), ('(\\d+)\\s*\\*\\s*(\\d+)', lambda a, b: int(a) * int(b)), ('(\\d+)\\s*×\\s*(\\d+)', lambda a, b: int(a) * int(b)), ('(\\d+)\\s*plus\\s*(\\d+)', lambda a, b: int(a) + int(b)), ('(\\d+)\\s*minus\\s*(\\d+)', lambda a, b: int(a) - int(b))]
    SEQUENCE_PATTERNS = ['(\\d+),\\s*(\\d+),\\s*(\\d+),\\s*\\?', '(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+_']
    OCR_PREPROCESSING = ['grayscale', 'denoise', 'contrast', 'threshold', 'deskew']
    __slots__ = tuple(('_initialized', '_ocr_pipeline', '_solve_stats', 'config'))

    def __init__(self, config: CaptchaSolverConfig | None=None):
        self.config = config or CaptchaSolverConfig()
        self._ocr_pipeline: Any | None = None
        self._initialized = False
        self._solve_stats = {'attempted': 0, 'solved': 0, 'by_method': {}}

    async def initialize(self) -> bool:
        """Initialize CAPTCHA solver with lightweight models."""
        try:
            logger.info('🚀 Initializing AdvancedCaptchaSolver...')
            if self.config.enable_image_ocr:
                await self._init_ocr_pipeline()
            self._initialized = True
            logger.info('✅ AdvancedCaptchaSolver initialized')
            return True
        except Exception as e:
            logger.error(f'❌ CaptchaSolver initialization failed: {e}')
            return False

    async def _init_ocr_pipeline(self) -> None:
        """Initialize OCR pipeline with fallback options."""
        await asyncio.to_thread(self._load_model_sync)

    def _load_model_sync(self) -> None:
        """Synchronous model loading (runs in thread to avoid blocking)."""
        try:
            import importlib.util
            if importlib.util.find_spec('transformers') is None:
                raise ImportError('transformers not installed')
            if importlib.util.find_spec('torch') is None:
                raise ImportError('torch not installed (required by transformers)')
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            self._ocr_pipeline = {'type': 'transformers', 'processor': TrOCRProcessor.from_pretrained(self.config.ocr_model), 'model': VisionEncoderDecoderModel.from_pretrained(self.config.ocr_model)}
            logger.info(f'✅ Loaded OCR model: {self.config.ocr_model}')
        except Exception as e:
            logger.warning(f'⚠️ Transformers OCR not available: {e}')
            try:
                import pytesseract
                self._ocr_pipeline = {'type': 'tesseract', 'engine': pytesseract}
                logger.info('✅ Using Tesseract OCR fallback')
            except ImportError:
                logger.warning('⚠️ No OCR backend available')
                self._ocr_pipeline = None

    async def solve_captcha(self, captcha_type: CaptchaType, image_data: bytes | None=None, text_challenge: str | None=None, **kwargs) -> CaptchaResult:
        """
        Solve CAPTCHA based on type.

        Args:
            captcha_type: Type of CAPTCHA
            image_data: Image bytes for image CAPTCHAs
            text_challenge: Text for logic/text CAPTCHAs
            **kwargs: Additional parameters

        Returns:
            CaptchaResult with solution
        """
        import time
        start_time = time.time()
        self._solve_stats['attempted'] += 1
        try:
            if captcha_type == CaptchaType.IMAGE and image_data:
                result = await self._solve_image_captcha(image_data)
            elif captcha_type == CaptchaType.TEXT and text_challenge:
                result = await self._solve_text_logic(text_challenge)
            elif captcha_type == CaptchaType.MATH and text_challenge:
                result = await self._solve_math_captcha(text_challenge)
            else:
                result = CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='unsupported')
            if result.success:
                self._solve_stats['solved'] += 1
                method = result.method
                self._solve_stats['by_method'][method] = self._solve_stats['by_method'].get(method, 0) + 1
            result.processing_time_ms = (time.time() - start_time) * 1000
            return result
        except Exception as e:
            logger.error(f'❌ CAPTCHA solving error: {e}')
            return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=(time.time() - start_time) * 1000, method='error')

    async def _solve_image_captcha(self, image_data: bytes) -> CaptchaResult:
        """Solve image-based text CAPTCHA using OCR."""
        import io
        from PIL import Image
        try:
            image = Image.open(io.BytesIO(image_data))
            max_size = self.config.max_image_size
            if image.width > max_size or image.height > max_size:
                ratio = min(max_size / image.width, max_size / image.height)
                new_size = (int(image.width * ratio), int(image.height * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            processed = self._preprocess_for_ocr(image)
            if self._ocr_pipeline and self._ocr_pipeline.get('type') == 'transformers':
                text, confidence = await self._run_transformers_ocr(processed)
            elif self._ocr_pipeline and self._ocr_pipeline.get('type') == 'tesseract':
                text, confidence = await self._run_tesseract_ocr(processed)
            else:
                return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='no_ocr_backend')
            text = text.strip().upper()
            text = ''.join((c for c in text if c.isalnum()))
            success = len(text) >= 4 and confidence >= self.config.confidence_threshold
            return CaptchaResult(success=success, solution=text if success else None, confidence=confidence, processing_time_ms=0.0, method='ocr')
        except Exception as e:
            logger.error(f'❌ Image CAPTCHA error: {e}')
            return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='error')

    def _preprocess_for_ocr(self, image: Image.Image) -> Image.Image:
        """Preprocess image for better OCR accuracy."""
        try:
            from PIL import ImageEnhance, ImageFilter
        except ImportError:
            logger.warning('PIL not available for preprocessing')
            return image
        try:
            if image.mode != 'L':
                image = image.convert('L')
            image = image.filter(ImageFilter.MedianFilter(size=3))
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)
            return image
        except Exception as e:
            logger.warning(f'Image preprocessing failed: {e}')
            return image

    async def _run_transformers_ocr(self, image: Image.Image) -> tuple[str, float]:
        """Run OCR using Transformers model (offloaded to thread)."""
        return await asyncio.to_thread(self._run_transformers_ocr_sync, image)

    def _run_transformers_ocr_sync(self, image: Image.Image) -> tuple[str, float]:
        """Synchronous OCR using Transformers model."""
        try:
            import torch
        except ImportError:
            logger.warning('torch not available for transformers OCR')
            return ('', 0.0)
        if self._ocr_pipeline is None:
            logger.warning('OCR pipeline not initialized')
            return ('', 0.0)
        try:
            model = self._ocr_pipeline['model']
            processor = self._ocr_pipeline['processor']
            pixel_values = processor(image, return_tensors='pt').pixel_values
            with torch.no_grad():
                generated_ids = model.generate(pixel_values)
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
            confidence = 0.75
            return (generated_text, confidence)
        except Exception as e:
            logger.warning(f'Transformers OCR failed: {e}')
            return ('', 0.0)

    async def _run_tesseract_ocr(self, image: Image.Image) -> tuple[str, float]:
        """Run OCR using Tesseract (offloaded to thread)."""
        return await asyncio.to_thread(self._run_tesseract_ocr_sync, image)

    def _run_tesseract_ocr_sync(self, image: Image.Image) -> tuple[str, float]:
        """Synchronous OCR using Tesseract."""
        try:
            if self._ocr_pipeline is None:
                raise KeyError('No OCR pipeline')
            pytesseract = self._ocr_pipeline['engine']
        except (KeyError, AttributeError, TypeError):
            logger.warning('pytesseract not available')
            return ('', 0.0)
        try:
            text = pytesseract.image_to_string(image)
            try:
                data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                confidences = [int(c) for c in data['conf'] if int(c) > 0]
                avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.5
            except Exception:
                avg_confidence = 0.5
            return (text, avg_confidence)
        except Exception as e:
            logger.warning(f'Tesseract OCR failed: {e}')
            return ('', 0.0)

    async def _solve_text_logic(self, challenge: str) -> CaptchaResult:
        """Solve text-based logic puzzles."""
        for pattern, solver in self.MATH_PATTERNS:
            match = re.search(pattern, challenge, re.IGNORECASE)
            if match:
                try:
                    result = solver(match.group(1), match.group(2))
                    return CaptchaResult(success=True, solution=str(result), confidence=0.95, processing_time_ms=0.0, method='math_logic')
                except Exception:
                    continue
        for pattern in self.SEQUENCE_PATTERNS:
            match = re.search(pattern, challenge)
            if match:
                try:
                    nums = [int(match.group(i)) for i in range(1, 4)]
                    diff = nums[1] - nums[0]
                    if nums[2] - nums[1] == diff:
                        result = nums[2] + diff
                        return CaptchaResult(success=True, solution=str(result), confidence=0.9, processing_time_ms=0.0, method='sequence_logic')
                except Exception:
                    continue
        return CaptchaResult(success=False, solution=None, confidence=0.0, processing_time_ms=0.0, method='logic_failed')

    async def _solve_math_captcha(self, challenge: str) -> CaptchaResult:
        """Solve math-based CAPTCHA."""
        return await self._solve_text_logic(challenge)

    def get_statistics(self) -> dict[str, Any]:
        """Get solving statistics."""
        attempted = self._solve_stats['attempted']
        solved = self._solve_stats['solved']
        return {'attempted': attempted, 'solved': solved, 'success_rate': solved / attempted if attempted > 0 else 0.0, 'by_method': self._solve_stats['by_method'].copy(), 'ocr_backend': self._ocr_pipeline.get('type') if self._ocr_pipeline else None}


# ═══════════════════════════════════════════════════════════════════════════
# Backward-compatible wrappers — delegate to unified evasion_pipeline
# ═══════════════════════════════════════════════════════════════════════════
# APEX-1005/1006/1007: JavaScriptEvasion and FingerprintRandomizer are now
# thin wrappers around the unified pipeline in evasion_pipeline.py.
# All JS generation happens in ONE place with CSPRNG+Gaussian, no duplicates.
#
# BrowserProfile is a backward-compat re-export of FingerprintProfile.
# JavaScriptEvasionConfig and FingerprintConfig are preserved for API compat
# but no longer drive JS generation (the unified pipeline uses
# EvasionCategory whitelists instead).

# Re-export unified types under the old names for backward compatibility
from .evasion_pipeline import (  # noqa: E402
    EvasionCategory,
    EvasionScript,
    FingerprintProfile,
    ProfileGenerator,
    _EvasionScriptGenerator,
    compute_detection_score,
)

# BrowserProfile = backward-compat alias
BrowserProfile = FingerprintProfile


class JavaScriptEvasionConfig(msgspec.Struct, gc=False):
    """Configuration for JavaScript evasion (backward-compat — APEX-1005/1006).

    Since the unified pipeline (evasion_pipeline.py), this config exists
    only for API compatibility.  The actual JS generation is driven by
    EvasionCategory whitelists, not boolean flags.
    """
    hide_webdriver: bool = True
    hide_automation: bool = True
    spoof_plugins: bool = True
    spoof_permissions: bool = True
    disable_webrtc: bool = True
    override_canvas: bool = True
    override_webgl: bool = True
    spoof_fonts: bool = True
    emulate_human_events: bool = True
    patch_detection_libs: bool = True
    randomize_globals: bool = True
    spoof_chrome_runtime: bool = True
    add_chrome_plugins: bool = True

    def to_category_whitelist(self) -> set[EvasionCategory] | None:
        """Convert legacy boolean flags to EvasionCategory whitelist.

        Returns None if all categories are enabled (= no filtering needed).
        """
        mapping: list[tuple[bool, EvasionCategory]] = [
            (self.hide_webdriver, EvasionCategory.WEBDRIVER),
            (self.hide_automation, EvasionCategory.AUTOMATION),
            (self.spoof_plugins, EvasionCategory.PLUGINS),
            (self.spoof_permissions, EvasionCategory.PERMISSIONS),
            (self.disable_webrtc, EvasionCategory.WEBRTC),
            (self.override_canvas, EvasionCategory.CANVAS),
            (self.override_webgl, EvasionCategory.WEBGL),
            (self.spoof_fonts, EvasionCategory.FONTS),
            (self.emulate_human_events, EvasionCategory.EVENTS),
            (self.patch_detection_libs, EvasionCategory.DETECTION),
            (self.randomize_globals, EvasionCategory.GLOBALS),
            (self.spoof_chrome_runtime, EvasionCategory.CHROME_RUNTIME),
            (self.add_chrome_plugins, EvasionCategory.CHROME_PLUGINS),
        ]
        # Screen, timezone, hardware, audio are always-on in the unified pipeline
        always_on = {
            EvasionCategory.SCREEN, EvasionCategory.TIMEZONE,
            EvasionCategory.HARDWARE, EvasionCategory.AUDIO,
        }
        enabled = {cat for flag, cat in mapping if flag} | always_on
        all_cats = {cat for _, cat in mapping} | always_on
        if enabled == all_cats:
            return None  # no filtering needed
        return enabled


class JavaScriptEvasion:
    """JavaScript evasion (backward-compat wrapper — delegates to evasion_pipeline).

    APEX-1005/1006/1007: This class is now a thin wrapper around
    ``_EvasionScriptGenerator`` from ``evasion_pipeline.py``.  All JS
    generation happens in the unified pipeline with CSPRNG + Gaussian noise.

    Example (unchanged API):
        >>> evasion = JavaScriptEvasion(config, profile=browser_profile)
        >>> scripts = evasion.get_all_evasion_scripts()
    """

    DETECTION_LIBS = ['botd', 'botguard', 'datadome', 'akamai', 'perimeterx', 'cloudflare', 'hcaptcha', 'recaptcha']
    __slots__ = ('config', '_profile', '_generator', '_cached_scripts', '_cached_str_scripts')

    def __init__(
        self,
        config: JavaScriptEvasionConfig | None = None,
        profile: BrowserProfile | None = None,
    ) -> None:
        self.config = config or JavaScriptEvasionConfig()
        self._profile: FingerprintProfile | None = profile
        self._generator: _EvasionScriptGenerator | None = None
        self._cached_scripts: list[EvasionScript] | None = None
        self._cached_str_scripts: list[str] | None = None
        if profile is not None:
            self._generator = _EvasionScriptGenerator(profile)

    def set_profile(self, profile: BrowserProfile) -> None:
        """Update the browser profile (clears cache)."""
        self._profile = profile
        self._generator = _EvasionScriptGenerator(profile)
        self._cached_scripts = None
        self._cached_str_scripts = None

    def get_all_evasion_scripts(self) -> list[str]:
        """Get all enabled evasion scripts (list[str] for API compat)."""
        if self._cached_str_scripts is not None:
            return self._cached_str_scripts

        if self._generator is None:
            # No profile provided — use defaults
            from .evasion_pipeline import ProfileGenerator
            pg = ProfileGenerator()
            profile = pg.generate()
            self._profile = profile
            self._generator = _EvasionScriptGenerator(profile)

        categories = self.config.to_category_whitelist()
        scripts = self._generator.generate_all(categories=categories)
        self._cached_scripts = scripts
        self._cached_str_scripts = [s.script for s in scripts]
        return self._cached_str_scripts

    def get_detection_score(self) -> dict[str, Any]:
        """Get evasion coverage score."""
        scripts = self._cached_scripts
        if scripts is None:
            _ = self.get_all_evasion_scripts()
            scripts = self._cached_scripts
        if scripts is not None:
            return compute_detection_score(scripts)
        return compute_detection_score([])

    # ── Legacy _get_* methods (delegated to pipeline for introspection) ──
    # These exist for API backward-compat; they trigger full script generation
    # on first call but return only the matching script on subsequent calls.

    def _get_webdriver_hider(self) -> str:
        scripts = self.get_all_evasion_scripts()
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'webdriver_hider':
                return s_ev.script
        return ''

    def _get_automation_hider(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'automation_hider':
                return s_ev.script
        return ''

    def _get_plugin_spoof(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'plugin_spoof':
                return s_ev.script
        return ''

    def _get_permission_spoof(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'permission_spoof':
                return s_ev.script
        return ''

    def _get_webrtc_disabler(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'webrtc_disable':
                return s_ev.script
        return ''

    def _get_canvas_override(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'canvas_csprng_gauss':
                return s_ev.script
        return ''

    def _get_webgl_override(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'webgl_profile_aware':
                return s_ev.script
        return ''

    def _get_font_spoof(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'font_spoof':
                return s_ev.script
        return ''

    def _get_event_emulator(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'event_emulator':
                return s_ev.script
        return ''

    def _get_detection_patcher(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'detection_patcher':
                return s_ev.script
        return ''

    def _get_global_randomizer(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'global_randomizer':
                return s_ev.script
        return ''

    def _get_chrome_runtime_spoof(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'chrome_runtime_spoof':
                return s_ev.script
        return ''

    def _get_chrome_plugins(self) -> str:
        for s_ev in self._cached_scripts or []:
            if s_ev.script_id == 'chrome_plugins':
                return s_ev.script
        return ''


# ═══════════════════════════════════════════════════════════════════════════
# FingerprintRandomizer — backward-compat wrapper
# ═══════════════════════════════════════════════════════════════════════════

class FingerprintConfig(msgspec.Struct, gc=False):
    """Configuration for fingerprint randomization (backward-compat)."""
    randomize_canvas: bool = True
    randomize_webgl: bool = True
    randomize_fonts: bool = True
    randomize_screen: bool = True
    randomize_timezone: bool = True
    randomize_plugins: bool = True
    consistent_per_session: bool = True
    session_duration: float = 3600.0
    use_realistic_profiles: bool = True
    platform: str | None = None


class FingerprintRandomizer:
    """Browser fingerprint randomization (backward-compat wrapper).

    APEX-1005/1006/1007: Delegates to ``ProfileGenerator`` from
    ``evasion_pipeline.py`` for profile generation, and uses
    ``_EvasionScriptGenerator`` for JS production.

    Example (unchanged API):
        >>> randomizer = FingerprintRandomizer()
        >>> profile = randomizer.get_profile()
        >>> js = randomizer.get_js_protection_script()
    """

    SCREEN_RESOLUTIONS = ProfileGenerator.SCREEN_RESOLUTIONS
    TIMEZONES = ProfileGenerator.TIMEZONES
    WEBGL_PROFILES = ProfileGenerator.WEBGL_PROFILES
    COMMON_FONTS = list(ProfileGenerator.COMMON_FONTS)
    COMMON_PLUGINS = list(ProfileGenerator.COMMON_PLUGINS)

    __slots__ = ('config', '_profile_gen', '_current_profile')

    def __init__(self, config: FingerprintConfig | None = None) -> None:
        self.config = config or FingerprintConfig()
        self._profile_gen = ProfileGenerator(
            platform=self.config.platform,
            session_duration=self.config.session_duration,
            consistent_per_session=self.config.consistent_per_session,
            randomize_canvas=self.config.randomize_canvas,
            randomize_webgl=self.config.randomize_webgl,
            randomize_fonts=self.config.randomize_fonts,
            randomize_screen=self.config.randomize_screen,
            randomize_timezone=self.config.randomize_timezone,
            randomize_plugins=self.config.randomize_plugins,
        )
        self._current_profile: FingerprintProfile | None = None

    def _generate_canvas_noise(self) -> tuple[int, int, int]:
        return self._profile_gen._generate_canvas_noise()

    def _generate_screen_resolution(self) -> tuple[int, int, int, float]:
        return self._profile_gen._generate_screen()

    def _generate_timezone(self) -> tuple[str, int]:
        return self._profile_gen._generate_timezone()

    def _generate_webgl_profile(self, platform: str) -> tuple[str, str]:
        return self._profile_gen._generate_webgl(platform)

    def _generate_font_list(self) -> list[str]:
        return self._profile_gen._generate_fonts()

    def _generate_plugins(self) -> list[dict[str, str]]:
        return self._profile_gen._generate_plugins()

    def _generate_hardware_specs(self, platform: str) -> tuple[int, int, int]:
        return self._profile_gen._generate_hardware(platform)

    def generate_profile(self, force_new: bool = False) -> BrowserProfile:
        """Generate new browser fingerprint profile."""
        profile = self._profile_gen.generate(force_new=force_new)
        self._current_profile = profile
        return profile

    def get_profile(self) -> BrowserProfile:
        """Get current or new profile."""
        return self._profile_gen.get()

    def get_js_protection_script(self) -> str:
        """Generate JavaScript fingerprint protection.

        Uses the unified pipeline to produce ALL evasion scripts (not just
        the old fingerprint-specific ones), joined into one string.
        """
        profile = self._profile_gen.get()
        if self._current_profile is None:
            self._current_profile = profile
        scripts = profile.to_evasion_scripts()
        return '\n'.join(s.script for s in scripts)

    def _js_header(self, profile_json: str) -> str:
        return ''

    def _js_screen_override(self) -> str:
        return ''

    def _js_hardware_override(self) -> str:
        return ''

    def _js_audio_protection(self) -> str:
        return ''

    def _js_canvas_protection(self, profile: Any) -> str:
        return ''

    def _js_timezone_protection(self) -> str:
        return ''

    def _js_footer(self) -> str:
        return ''

    def get_fingerprint_hash(self) -> str:
        """Get hash of current fingerprint."""
        profile = self._profile_gen.get()
        return profile.fingerprint_hash()

    def rotate(self) -> BrowserProfile:
        """Force rotation to new fingerprint."""
        return self._profile_gen.rotate()

    def get_statistics(self) -> dict[str, Any]:
        """Get randomization statistics."""
        return {
            'rotation_count': self._profile_gen.rotation_count,
            'current_profile_age': self._profile_gen.profile_age,
            'current_fingerprint': self.get_fingerprint_hash(),
            'consistent_mode': self.config.consistent_per_session,
        }


class BehaviorPattern(Enum):
    """Pre-defined behavior patterns"""
    CASUAL = 'casual'
    RESEARCHER = 'researcher'
    QUICK = 'quick'
    CAREFUL = 'careful'

class SimulationConfig(msgspec.Struct, gc=False):
    """Configuration for behavior simulation"""
    pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
    min_delay: float = 0.5
    max_delay: float = 3.0
    mouse_speed: float = 1.0
    scroll_min: int = 100
    scroll_max: int = 800
    scroll_pause: float = 0.1
    randomness: float = 0.3
    viewport_variation: bool = True

class MouseMovement(msgspec.Struct, gc=False):
    """Mouse movement point"""
    x: float
    y: float
    timestamp: float

class ScrollAction(msgspec.Struct, gc=False):
    """Scroll action"""
    delta_y: int
    duration: float
    pause_after: float

class BehaviorSimulator:
    """
    Simulate human-like web browsing behavior.

    M1-Optimized: Minimal CPU usage, efficient randomization

    Example:
        >>> simulator = BehaviorSimulator()
        >>> await simulator.simulate_reading(duration=30)
        >>> await simulator.simulate_scroll(direction='down')
        >>> await simulator.simulate_click(x=100, y=200)
    """
    PATTERNS: dict[BehaviorPattern, dict[str, Any]] = {BehaviorPattern.CASUAL: {'min_delay': 1.0, 'max_delay': 5.0, 'mouse_speed': 0.7, 'scroll_min': 200, 'scroll_max': 1000, 'scroll_pause': 0.2, 'randomness': 0.4}, BehaviorPattern.RESEARCHER: {'min_delay': 0.8, 'max_delay': 2.5, 'mouse_speed': 1.0, 'scroll_min': 300, 'scroll_max': 800, 'scroll_pause': 0.15, 'randomness': 0.25}, BehaviorPattern.QUICK: {'min_delay': 0.3, 'max_delay': 1.2, 'mouse_speed': 1.3, 'scroll_min': 400, 'scroll_max': 1200, 'scroll_pause': 0.05, 'randomness': 0.35}, BehaviorPattern.CAREFUL: {'min_delay': 2.0, 'max_delay': 8.0, 'mouse_speed': 0.5, 'scroll_min': 100, 'scroll_max': 400, 'scroll_pause': 0.3, 'randomness': 0.2}}
    __slots__ = tuple(('action_count', 'config', 'last_action_time', 'mouse_position', 'scroll_position', 'viewport_height', 'viewport_width'))

    def __init__(self, config: SimulationConfig | None=None):
        self.config = config or SimulationConfig()
        self._apply_pattern()
        self.last_action_time: float = time.time()
        self.mouse_position: tuple[int, int] = (0, 0)
        self.scroll_position: int = 0
        self.action_count: int = 0
        self.viewport_width: int = 1920
        self.viewport_height: int = 1080

    def _apply_pattern(self):
        """Apply pattern preset to config"""
        if self.config.pattern in self.PATTERNS:
            preset = self.PATTERNS[self.config.pattern]
            for key, value in preset.items():
                setattr(self.config, key, value)

    def _random_delay(self, min_mult: float=0.8, max_mult: float=1.2) -> float:
        """Generate random delay with variation"""
        base = _RNG.uniform(self.config.min_delay, self.config.max_delay)
        variation = _RNG.uniform(min_mult, max_mult)
        return base * variation

    def _apply_randomness(self, value: float) -> float:
        """Apply randomness factor to value"""
        if self.config.randomness <= 0:
            return value
        variation = value * self.config.randomness
        return value + _RNG.uniform(-variation, variation)

    def _bezier_curve(self, p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], t: float) -> tuple[float, float]:
        """Calculate quadratic Bézier curve point (M1-optimized)"""
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        return (x, y)

    def generate_mouse_path(self, start: tuple[int, int], end: tuple[int, int], num_points: int=20) -> list[MouseMovement]:
        """
        Generate human-like mouse path using Bézier curve.

        M1-Optimized: Efficient numpy-like operations using pure Python
        for minimal memory footprint on constrained systems.

        Args:
            start: Starting position (x, y)
            end: Ending position (x, y)
            num_points: Number of points in path

        Returns:
            List of mouse movement points
        """
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2
        offset_range = abs(end[0] - start[0]) + abs(end[1] - start[1])
        offset_range *= 0.2 * self.config.randomness
        control = (mid_x + _RNG.uniform(-offset_range, offset_range), mid_y + _RNG.uniform(-offset_range, offset_range))
        points = []
        now = time.time()
        for i in range(num_points):
            t = i / (num_points - 1)
            x, y = self._bezier_curve(start, control, end, t)
            jitter = self.config.randomness * 2
            x += _RNG.uniform(-jitter, jitter)
            y += _RNG.uniform(-jitter, jitter)
            speed_variation = _RNG.uniform(0.8, 1.2) / self.config.mouse_speed
            timestamp = now + i * 0.01 * speed_variation
            points.append(MouseMovement(x=x, y=y, timestamp=timestamp))
        return points

    async def simulate_mouse_move(self, target_x: int, target_y: int, callback: Any | None=None) -> None:
        """
        Simulate mouse movement to target position.

        Args:
            target_x: Target X coordinate
            target_y: Target Y coordinate
            callback: Optional callback function for each point
        """
        path = self.generate_mouse_path(self.mouse_position, (target_x, target_y))
        for point in path:
            self.mouse_position = (int(point.x), int(point.y))
            if callback:
                await callback(self.mouse_position)
            await asyncio.sleep(0.005)
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_click(self, x: int | None=None, y: int | None=None, callback: Any | None=None) -> None:
        """
        Simulate mouse click.

        Args:
            x: Click X coordinate (default: current)
            y: Click Y coordinate (default: current)
            callback: Optional callback function
        """
        if x is not None and y is not None:
            await self.simulate_mouse_move(x, y, callback)
        await asyncio.sleep(self._random_delay(0.1, 0.3))
        if callback:
            await callback(('click', self.mouse_position))
        logger.debug(f'Simulated click at {self.mouse_position}')
        await asyncio.sleep(self._random_delay(0.2, 0.5))
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_scroll(self, direction: str='down', amount: int | None=None, callback: Any | None=None) -> None:
        """
        Simulate scrolling.

        Args:
            direction: 'up' or 'down'
            amount: Scroll amount in pixels (default: random)
            callback: Optional callback function
        """
        if amount is None:
            amount = _RNG.randint(self.config.scroll_min, self.config.scroll_max)
        if direction == 'up':
            amount = -amount
        chunk_size = 100
        remaining = amount
        while abs(remaining) > 0:
            chunk = min(chunk_size, abs(remaining))
            if remaining < 0:
                chunk = -chunk
            if callback:
                await callback(('scroll', chunk))
            self.scroll_position += chunk
            remaining -= chunk
            await asyncio.sleep(self._apply_randomness(self.config.scroll_pause))
        logger.debug(f'Simulated scroll {amount}px (total: {self.scroll_position})')
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_typing(self, text: str, callback: Any | None=None, wpm: int=60) -> None:
        """
        Simulate human-like typing.

        Args:
            text: Text to type
            callback: Optional callback function
            wpm: Words per minute (typing speed)
        """
        chars_per_minute = wpm * 5
        base_delay = 60 / chars_per_minute
        for char in text:
            delay = base_delay * _RNG.uniform(0.7, 1.3)
            if callback:
                await callback(('type', char))
            await asyncio.sleep(delay)
            if _RNG.random() < 0.05:
                await asyncio.sleep(_RNG.uniform(0.2, 0.5))
        logger.debug(f'Simulated typing {len(text)} characters')
        self.action_count += 1
        self.last_action_time = time.time()

    async def simulate_reading(self, duration: float=10.0, scroll_probability: float=0.3) -> None:
        """
        Simulate reading a page (idle time with occasional scrolls).

        Args:
            duration: Reading duration in seconds
            scroll_probability: Probability of scrolling during reading
        """
        start_time = time.time()
        while time.time() - start_time < duration:
            await asyncio.sleep(self._random_delay(0.5, 1.5))
            if _RNG.random() < scroll_probability:
                direction = 'down' if _RNG.random() > 0.3 else 'up'
                await self.simulate_scroll(direction)
        logger.debug(f'Simulated reading for {duration}s')

    async def simulate_page_visit(self, num_scrolls: int=3, read_time: float=15.0) -> dict[str, Any]:
        """
        Simulate complete page visit behavior.

        Args:
            num_scrolls: Number of scroll actions
            read_time: Time spent reading

        Returns:
            Statistics about the simulated visit
        """
        start_time = time.time()
        await asyncio.sleep(self._random_delay(0.5, 1.5))
        await self.simulate_reading(duration=read_time, scroll_probability=0.4)
        for _ in range(num_scrolls):
            if _RNG.random() > 0.3:
                direction = _RNG.choice(['up', 'down'])
                await self.simulate_scroll(direction)
                await asyncio.sleep(self._random_delay(1.0, 3.0))
        duration = time.time() - start_time
        return {'duration': duration, 'actions': self.action_count, 'scroll_position': self.scroll_position, 'pattern': self.config.pattern.value}

    def get_statistics(self) -> dict[str, Any]:
        """Get simulation statistics"""
        return {'action_count': self.action_count, 'mouse_position': self.mouse_position, 'scroll_position': self.scroll_position, 'last_action_time': self.last_action_time, 'pattern': self.config.pattern.value, 'config': {'min_delay': self.config.min_delay, 'max_delay': self.config.max_delay, 'randomness': self.config.randomness}}

logger = logging.getLogger(__name__)

class StealthLayer:
    """
    Stealth layer for web browsing with anti-detection and CAPTCHA solving.

    This layer:
    1. Manages stealth browser instances
    2. Applies detection evasion techniques
    3. Solves CAPTCHAs when detected
    4. Simulates human behavior
    5. Protects against debugging (Chameleon)

    Example:
        stealth = StealthLayer(config)
        await stealth.initialize()

        # Create stealth session
        session = await stealth.create_session()

        # Browse with evasion
        page = await stealth.new_page(session)
        await stealth.apply_evasion(page)

        # Solve CAPTCHA if detected
        solution = await stealth.solve_captcha(page, "https://example.com")
    """
    __slots__ = tuple(('_browsers_created', '_captcha_solver', '_captchas_solved', '_chameleon', '_ctx', '_detection_evader', '_evasions_applied', '_fingerprint_randomizer', '_js_evasion', '_session_counter', '_sessions', '_stealth_browser', 'config', 'layer_name'))

    def __init__(self, config: StealthConfig | None=None):
        """
        Initialize StealthLayer.

        Args:
            config: Stealth configuration (uses defaults if None)
        """
        self.config = config or StealthConfig()
        self._stealth_browser = None
        self._detection_evader = None
        self._captcha_solver: AdvancedCaptchaSolver | None = None
        self._js_evasion: JavaScriptEvasion | None = None
        self._chameleon: Chameleon | None = None
        self._fingerprint_randomizer: FingerprintRandomizer | None = None
        self._sessions: dict[str, StealthSession] = {}
        self._session_counter = 0
        self.layer_name: str = 'stealth'
        self._ctx: Any | None = None
        self._browsers_created = 0
        self._captchas_solved = 0
        self._evasions_applied = 0
        logger.info('StealthLayer initialized')

    async def mount(self, ctx: Any) -> None:
        """Layer Protocol: mount."""
        self._ctx = ctx
        await self.initialize()
        ctx.set('stealth', self)

    async def unmount(self, ctx: Any) -> None:
        """Layer Protocol: unmount."""
        await self.cleanup()

    async def on_event(self, ctx: Any, event: Any) -> Any:
        """Layer Protocol: handle stealth events."""
        return event

    def get_timing_jitter(self) -> float:
        """Return random jitter delay in seconds for fetch timing.

        Uses Gaussian distribution to simulate human-like inter-request timing.
        Returns 0.0 if stealth is disabled or unavailable.

        Jitter is NON-BLOCKING when used with asyncio.sleep() — safe for async.
        """
        if not getattr(self, '_enabled', True):
            return 0.0
        try:
            return max(0.0, min(2.0, _gauss(0.5, 0.3)))
        except Exception:
            return 0.0

    async def initialize(self) -> bool:
        """
        Initialize StealthLayer components.

        Returns:
            True if initialization successful
        """
        try:
            logger.info('🚀 Initializing StealthLayer...')
            if self.config.enable_stealth_scripts:
                await self._init_detection_evader()
            if self.config.enable_captcha_solving:
                await self._init_captcha_solver()
            await self._init_chameleon()
            await self._init_fingerprint_randomizer()
            await self._init_js_evasion()
            logger.info('✅ StealthLayer initialized successfully')
            return True
        except Exception as e:
            logger.error(f'❌ StealthLayer initialization failed: {e}')
            return False

    async def _init_stealth_browser(self) -> None:
        """Lazy initialization of StealthBrowser"""
        if self._stealth_browser is None:
            try:
                from hledac.universal.advanced_web.stealth_browser import BrowserConfig, StealthBrowser
                browser_config = BrowserConfig(browser_type=self.config.browser_type, headless=self.config.headless, pool_size=self.config.pool_size, m1_optimized=True)
                self._stealth_browser = StealthBrowser(browser_config)
                await self._stealth_browser.initialize()
                self._browsers_created += 1
                logger.info('✅ StealthBrowser initialized')
            except ImportError as e:
                logger.warning(f'⚠️ StealthBrowser not available: {e}')
                self._stealth_browser = None

    async def _init_detection_evader(self) -> None:
        """Lazy initialization of DetectionEvader"""
        if self._detection_evader is None:
            try:
                from hledac.universal.advanced_web.detection_evader import DetectionEvader
                self._detection_evader = DetectionEvader(detection_threshnew=self.config.detection_threshold, adaptive_mode=self.config.adaptive_mode)
                logger.info('✅ DetectionEvader initialized')
            except ImportError as e:
                logger.warning(f'⚠️ DetectionEvader not available: {e}')
                self._detection_evader = None

    async def _init_captcha_solver(self) -> None:
        """Lazy initialization of AdvancedCaptchaSolver (self-hosted, OFF BY DEFAULT on M1 8GB).

        M1 8GB: Local OCR (torch/transformers) is HEAVY.
        Default: use CaptchaSolvingStrategy (2captcha API primary, Vision/CoreML secondary).
        Only load AdvancedCaptchaSolver if HLEDAC_ENABLE_CAPTCHA_LOCAL=1.
        """
        import os
        if self._captcha_solver is not None:
            return
        if not self.config.enable_captcha_local:
            logger.debug('AdvancedCaptchaSolver: disabled (enable_captcha_local=False)')
            return
        try:
            config = CaptchaSolverConfig(enable_image_ocr=True, enable_text_logic=True, confidence_threshold=0.6)
            self._captcha_solver = AdvancedCaptchaSolver(config)
            await self._captcha_solver.initialize()
            logger.info('✅ AdvancedCaptchaSolver initialized (local OCR, enable_captcha_local=True)')
        except Exception as e:
            logger.warning(f'⚠️ AdvancedCaptchaSolver not available: {e}')
            self._captcha_solver = None

    async def _init_js_evasion(self) -> None:
        """Initialize JavaScript evasion (unified pipeline — APEX-1005/1006/1007).

        Uses the unified ``EvasionScriptGenerator`` pipeline.  The
        ``_fingerprint_randomizer`` provides the profile for consistency.
        """
        if self._js_evasion is None:
            try:
                config = JavaScriptEvasionConfig(
                    hide_webdriver=True, hide_automation=True,
                    spoof_plugins=True, spoof_permissions=True,
                    disable_webrtc=True, override_canvas=True,
                    override_webgl=True, spoof_fonts=True,
                    emulate_human_events=True, patch_detection_libs=True,
                    randomize_globals=True, spoof_chrome_runtime=True,
                    add_chrome_plugins=True,
                )

                # Get profile from FingerprintRandomizer if available
                profile = None
                if self._fingerprint_randomizer is not None:
                    profile = self._fingerprint_randomizer.get_profile()

                self._js_evasion = JavaScriptEvasion(config, profile=profile)
                logger.info(
                    '✅ JavaScriptEvasion initialized (unified pipeline, '
                    '17 categories, CSPRNG+Gaussian)'
                )
            except Exception as e:
                logger.warning(f'⚠️ JavaScriptEvasion initialization failed: {e}')
                self._js_evasion = None

    async def _init_chameleon(self) -> None:
        """Initialize Chameleon for anti-debugging."""
        try:
            self._chameleon = Chameleon()
            self._chameleon.masquerade_process()
            if self._chameleon.initialize_ptrace_protection():
                logger.info('✅ Chameleon anti-debugging initialized (ptrace)')
            else:
                logger.info('✅ Chameleon initialized (ptrace not available)')
        except Exception as e:
            logger.warning(f'⚠️ Chameleon not available: {e}')
            self._chameleon = None

    async def _init_fingerprint_randomizer(self) -> None:
        """Initialize FingerprintRandomizer for browser fingerprint protection."""
        try:
            self._fingerprint_randomizer = FingerprintRandomizer()
            logger.info('✅ FingerprintRandomizer initialized')
        except Exception as e:
            logger.warning(f'⚠️ FingerprintRandomizer initialization failed: {e}')
            self._fingerprint_randomizer = None

    def get_chameleon(self) -> Chameleon | None:
        """Get Chameleon instance for anti-debugging control."""
        return self._chameleon

    def is_debugger_present(self) -> bool:
        """Check if a debugger is attached (macOS only)."""
        if self._chameleon:
            return self._chameleon.is_debugger_present()
        return False

    async def create_session(self, browser_type: BrowserType | None=None, proxy: str | None=None) -> StealthSession:
        """
        Create a new stealth browsing session.

        Args:
            browser_type: Browser type (uses config default if None)
            proxy: Proxy URL (optional)

        Returns:
            StealthSession
        """
        self._session_counter += 1
        session_id = f'stealth_{self._session_counter}'
        browser_type = browser_type or BrowserType(self.config.browser_type)
        logger.info(f'🔒 Creating stealth session: {session_id}')
        if self._stealth_browser is None:
            await self._init_stealth_browser()
        fingerprint = await self._generate_fingerprint()
        session = StealthSession(session_id=session_id, browser_type=browser_type, fingerprint=fingerprint, proxy=proxy, risk_level=RiskLevel.LOW, created_at=time.time())
        self._sessions[session_id] = session
        return session

    async def _generate_fingerprint(self) -> dict[str, Any]:
        """Generate browser fingerprint"""
        if self.config.enable_fingerprint_rotation and self._detection_evader:
            try:
                return {'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0', 'screen': {'width': 1920, 'height': 1080}, 'timezone': 'America/New_York', 'language': 'en-US', 'platform': 'MacIntel', 'plugins': ['Chrome PDF Plugin', 'Native Client']}
            except Exception:  # noqa: BLE001
                pass
        return {'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'screen': {'width': 1920, 'height': 1080}}

    async def new_page(self, session: StealthSession) -> Any:
        """
        Create a new page in the stealth session.

        Args:
            session: StealthSession

        Returns:
            Playwright page object
        """
        if self._stealth_browser is None:
            raise RuntimeError('StealthBrowser not initialized')
        try:
            page = await self._stealth_browser.new_page()
            logger.debug(f'📄 New page created for session {session.session_id}')
            return page
        except Exception as e:
            logger.error(f'❌ Failed to create page: {e}')
            raise

    async def apply_evasion(self, page: Any, risk_level: RiskLevel | None=None) -> None:
        """
        Apply detection evasion scripts to page.

        Args:
            page: Playwright page
            risk_level: Risk level (auto-detect if None)
        """
        if not self.config.enable_stealth_scripts:
            return
        if self._detection_evader is None:
            logger.warning('⚠️ DetectionEvader not available, skipping evasion')
            return
        try:
            if risk_level is None:
                content = await page.content() if hasattr(page, 'content') else ''
                risk_level = self._detection_evader.analyze_page_content(content)
            scripts = self._detection_evader.get_evasion_scripts()
            if self._js_evasion:
                js_scripts = self._js_evasion.get_all_evasion_scripts()
                scripts.extend(js_scripts)
                logger.debug(f'🛡️ Added {len(js_scripts)} unified evasion scripts')
            # NOTE: _fingerprint_randomizer.get_js_protection_script() is NO
            # LONGER called here — APEX-1005/1006/1007: both now delegate to
            # the same unified pipeline, so calling both would inject duplicates.
            for script in scripts:
                try:
                    await page.add_init_script(script)
                except Exception as e:
                    logger.debug(f'⚠️ Failed to add script: {e}')
            self._evasions_applied += 1
            logger.info(f'🛡️ Applied {len(scripts)} evasion scripts (risk: {risk_level.value})')
        except Exception as e:
            logger.warning(f'⚠️ Evasion application failed: {e}')

    async def simulate_human_behavior(self, page: Any) -> None:
        """
        Simulate human-like behavior on page.

        Args:
            page: Playwright page
        """
        if not self.config.enable_behavior_simulation:
            return
        if self._detection_evader is None:
            return
        try:
            await self._detection_evader.simulate_human_behavior(page)
            logger.debug('🎭 Human behavior simulated')
        except Exception as e:
            logger.debug(f'⚠️ Behavior simulation failed: {e}')

    async def solve_captcha(self, page: Any, url: str, captcha_type: CaptchaType | None=None) -> CaptchaSolution | None:
        """
        Detect and solve CAPTCHA on page.

        Args:
            page: Playwright page
            url: Page URL
            captcha_type: CAPTCHA type (auto-detect if None)

        Returns:
            CaptchaSolution or None if no CAPTCHA
        """
        if not self.config.enable_captcha_solving:
            return None
        if self._captcha_solver is None:
            logger.warning('⚠️ CaptchaSolver not available')
            return None
        try:
            html = await page.content() if hasattr(page, 'content') else ''
            detected_type = captcha_type or self._captcha_solver.detect_captcha(html)
            if detected_type == CaptchaType.IMAGE:
                logger.info('🧩 Image CAPTCHA detected')
                import re
                img_match = re.search('<img[^>]+src=["\\\']([^"\\\']+)["\\\'][^>]*>', html, re.IGNORECASE)
                if img_match:
                    img_url = img_match.group(1)
                    if img_url.startswith('/'):
                        from urllib.parse import urljoin
                        img_url = urljoin(url, img_url)
                    try:
                        if img_url.startswith('data:'):
                            import base64
                            img_data = img_url.split(',', 1)[1]
                            image_bytes = base64.b64decode(img_data)
                        else:
                            try:
                                img_response = await page.evaluate('(async () => { const resp = await fetch(arguments[0]); return resp.ok ? await resp.arrayBuffer() : null; })()', img_url)
                                if img_response:
                                    image_bytes = bytes(img_response)
                                else:
                                    image_bytes = None
                            except Exception:
                                image_bytes = None
                        if image_bytes:
                            result = await self.solve_captcha(captcha_type=CaptchaType.IMAGE, image_data=image_bytes)
                            if result.success:
                                self._captchas_solved += 1
                                return CaptchaSolution(solution=result.solution or '', solved_at=time.time(), cost=0.0, confidence=result.confidence, provider='internal_ocr')
                    except Exception as e:
                        logger.warning(f'Image CAPTCHA fetch/solve failed: {e}')
                return None
            elif detected_type in (CaptchaType.RECAPTCHA_V2, CaptchaType.RECAPTCHA_V3):
                logger.info('🧩 reCAPTCHA detected')
                import re
                site_key_match = re.search('data-sitekey="([^"]+)"', html)
                if site_key_match:
                    site_key = site_key_match.group(1)
                    solution = await self._captcha_solver.solve_captcha(captcha_type=detected_type, site_key=site_key, url=url)
                    self._captchas_solved += 1
                    return CaptchaSolution(solution=solution if isinstance(solution, str) else str(solution), solved_at=time.time(), cost=0.002, confidence=0.9, provider=self.config.captcha_providers[0] if self.config.captcha_providers else 'unknown')
            return None
        except Exception as e:
            logger.error(f'❌ CAPTCHA solving failed: {e}')
            return None

    async def close_session(self, session_id: str) -> None:
        """
        Close a stealth session.

        Args:
            session_id: Session ID
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.debug(f'🔒 Session closed: {session_id}')

    def get_fingerprint_protection(self) -> str:
        """Get JavaScript fingerprint protection script (unified pipeline).

        Now delegates to the unified ``EvasionScriptGenerator`` pipeline
        (APEX-1005/1006/1007). Returns ALL evasion scripts joined as one
        string — 17 categories with CSPRNG+Gaussian throughout.
        """
        if self._js_evasion:
            return '\n'.join(self._js_evasion.get_all_evasion_scripts())
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.get_js_protection_script()
        return ''

    def rotate_fingerprint(self) -> BrowserProfile | None:
        """Force rotation to new browser fingerprint.

        Rotates both HTTP headers (via ZeroAttributionEngine) and browser JA3
        fingerprint (via FingerprintRandomizer) for full layer-7 anonymity.
        Fail-soft: never break stealth rotation on any exception.
        """
        from hledac.universal.security.zero_attribution_engine import ZeroAttributionEngine
        try:
            _header_engine = ZeroAttributionEngine()
            _header_engine.fingerprint_rotate_headers()
        except Exception:  # noqa: BLE001
            pass
        if self._fingerprint_randomizer:
            return self._fingerprint_randomizer.rotate()
        return None

    def get_js_evasion_score(self) -> dict[str, Any] | None:
        """Get JavaScript evasion coverage score"""
        if self._js_evasion:
            return self._js_evasion.get_detection_score()
        return None

    def get_captcha_solver_stats(self) -> dict[str, Any] | None:
        """Get CAPTCHA solver statistics"""
        if self._captcha_solver:
            return self._captcha_solver.get_statistics()
        return None

    def get_statistics(self) -> dict[str, Any]:
        """Get stealth layer statistics"""
        return {'browsers_created': self._browsers_created, 'sessions_active': len(self._sessions), 'captchas_solved': self._captchas_solved, 'evasions_applied': self._evasions_applied, 'stealth_browser_available': self._stealth_browser is not None, 'detection_evader_available': self._detection_evader is not None, 'captcha_solver_available': self._captcha_solver is not None, 'js_evasion_available': self._js_evasion is not None, 'chameleon_available': self._chameleon is not None, 'fingerprint_randomizer_available': self._fingerprint_randomizer is not None, 'anti_debugging_active': self._chameleon.is_debugger_protected() if self._chameleon else False, 'fingerprint_stats': self._fingerprint_randomizer.get_statistics() if self._fingerprint_randomizer else None, 'js_evasion_score': self.get_js_evasion_score(), 'captcha_solver_stats': self.get_captcha_solver_stats(), 'config': {'browser_type': self.config.browser_type, 'headless': self.config.headless, 'enable_stealth_scripts': self.config.enable_stealth_scripts, 'enable_captcha_solving': self.config.enable_captcha_solving}}

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info('🧹 Cleaning up StealthLayer...')
        self._sessions.clear()
        if self._stealth_browser and hasattr(self._stealth_browser, 'close'):
            try:
                await self._stealth_browser.close()
            except Exception as e:
                logger.warning(f'⚠️ StealthBrowser cleanup error: {e}')
        if self._captcha_solver and hasattr(self._captcha_solver, 'close'):
            try:
                await self._captcha_solver.close()
            except Exception as e:
                logger.warning(f'⚠️ CaptchaSolver cleanup error: {e}')
        logger.info('✅ StealthLayer cleanup complete')
import ctypes
import ctypes.util
import os

class Chameleon:
    """
    Chameleon - Anti-debugging and process masquerading for macOS M1.

    Integrated from kernel/stealth/chameleon.py - Provides protection
    against debugging and process masquerading for stealth operations.

    Features:
    - Process masquerading (change process name to appear benign)
    - ptrace(PT_DENY_ATTACH) anti-debugging (macOS only)
    - Environment cleanup to remove debugging indicators

    Example:
        chameleon = Chameleon()

        # Apply process masquerading
        chameleon.masquerade_process()

        # Initialize anti-debugging
        chameleon.initialize_ptrace_protection()

        # Check if debugger is present
        if chameleon.is_debugger_present():
            print("Debugger detected!")
    """
    MASQUERADE_TARGETS = [('mdworker_shared', 'Spotlight indexer'), ('mds_stores', 'Metadata server'), ('syslogd', 'System logger'), ('locationd', 'Location services'), ('bluetoothd', 'Bluetooth daemon'), ('coreaudiod', 'Audio daemon'), ('powerd', 'Power management'), ('airportd', 'WiFi daemon')]
    __slots__ = tuple(('_masqueraded', '_original_name', '_ptrace_protected'))

    def __init__(self):
        """Initialize Chameleon."""
        self._original_name: str | None = None
        self._masqueraded = False
        self._ptrace_protected = False
        logger.debug('Chameleon initialized')

    def masquerade_process(self, target_index: int | None=None) -> bool:
        """
        Masquerade process as a benign system process.

        Args:
            target_index: Index of MASQUERADE_TARGETS to use (random if None)

        Returns:
            True if successful
        """
        try:
            if target_index is None:
                target_index = _RNG.randint(0, len(self.MASQUERADE_TARGETS) - 1)
            target_name, target_desc = self.MASQUERADE_TARGETS[target_index]
            self._original_name = sys.argv[0] if sys.argv else 'python'
            try:
                import setproctitle
                setproctitle.setproctitle(target_name)
                self._masqueraded = True
                logger.info(f"Chameleon: Masquerading as '{target_name}' ({target_desc})")
                return True
            except ImportError:
                if sys.argv:
                    sys.argv[0] = target_name
                    self._masqueraded = True
                    logger.info(f"Chameleon: Masquerading as '{target_name}' (via argv)")
                    return True
            return False
        except Exception as e:
            logger.warning(f'Chameleon: Masquerade failed: {e}')
            return False

    def initialize_ptrace_protection(self) -> bool:
        """
        Initialize ptrace anti-debugging protection (macOS only).

        Uses PT_DENY_ATTACH to prevent debugger attachment.

        Returns:
            True if protection was successfully applied
        """
        if sys.platform != 'darwin':
            logger.debug('Chameleon: ptrace protection only available on macOS')
            return False
        try:
            libc = ctypes.CDLL(ctypes.util.find_library('c'))
            PT_DENY_ATTACH = 31
            result = libc.ptrace(PT_DENY_ATTACH, 0, 0, 0)
            if result == 0:
                self._ptrace_protected = True
                logger.info('Chameleon: ptrace anti-debugging enabled (PT_DENY_ATTACH)')
                return True
            else:
                logger.warning(f'Chameleon: ptrace returned {result}')
                return False
        except Exception as e:
            logger.warning(f'Chameleon: ptrace initialization failed: {e}')
            return False

    def is_debugger_present(self) -> bool:
        """
        Check if a debugger is attached (macOS only).

        Returns:
            True if debugger detected
        """
        if sys.platform != 'darwin':
            return False
        try:
            import subprocess
            result = subprocess.run(['sysctl', '-n', 'kern.proc.pid', str(os.getpid())], capture_output=True, text=True, timeout=1)
            if 'P_TRACED' in result.stdout or 'traced' in result.stdout.lower():
                return True
            libc = ctypes.CDLL(ctypes.util.find_library('c'))
            PT_TRACE_ME = 0
            result = libc.ptrace(PT_TRACE_ME, 0, 0, 0)
            if result < 0:
                return True
            return False
        except Exception as e:
            logger.debug(f'Chameleon: Debugger check failed: {e}')
            return False

    def is_debugger_protected(self) -> bool:
        """Check if ptrace protection is active."""
        return self._ptrace_protected

    def cleanup_environment(self) -> None:
        """Clean environment variables that might indicate debugging."""
        debug_vars = ['DEBUG', 'PYTHONBREAKPOINT', 'PYDEVD', 'IDE_PROJECT_ROOTS', 'PYTHONPATH_DEBUG']
        for var in debug_vars:
            if var in os.environ:
                del os.environ[var]
                logger.debug(f'Chameleon: Removed {var} from environment')

    def get_info(self) -> dict[str, Any]:
        """Get Chameleon status information."""
        return {'masqueraded': self._masqueraded, 'original_name': self._original_name, 'current_masquerade': sys.argv[0] if self._masqueraded else None, 'ptrace_protected': self._ptrace_protected, 'debugger_present': self.is_debugger_present(), 'platform': sys.platform}
__all__ = ['StealthLayer', 'StealthConfig', 'StealthSession', 'RiskLevel', 'BrowserType', 'CaptchaType', 'CaptchaSolution', 'BehaviorSimulator', 'BehaviorPattern', 'SimulationConfig', 'MouseMovement', 'ScrollAction', 'FingerprintRandomizer', 'FingerprintConfig', 'BrowserProfile', 'AdvancedCaptchaSolver', 'CaptchaSolverConfig', 'CaptchaResult', 'JavaScriptEvasion', 'JavaScriptEvasionConfig', 'Chameleon']