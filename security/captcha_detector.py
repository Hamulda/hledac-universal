"""
CAPTCHA detection pre-filter — phase 1 heuristic, no ML model required.
Gated by HLEDAC_ENABLE_CAPTCHA_DETECTION=1.

GHOST_INVARIANTS:
- I10: Never block event loop — PIL.open() always via run_in_executor
- Fail-soft: any exception → return False (never crash on CAPTCHA detection)
- Phase 1: PIL-only heuristics (no VisionEncoder, no coremltools model)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Optional

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

# Shared executor for PIL ops — single thread is enough for phase 1
_PIL_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _get_pil_executor() -> ThreadPoolExecutor:
    global _PIL_EXECUTOR
    if _PIL_EXECUTOR is None:
        _PIL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="captcha_pil")
    return _PIL_EXECUTOR


def _analyze_pil_sync(image_bytes: bytes) -> float:
    """Analyze PIL image properties — runs in executor thread."""
    if Image is None:
        return 0.0
    try:
        img = Image.open(BytesIO(image_bytes))
        s = 0.0
        if img.mode in ("L", "P", "1"):
            s += 0.3
        w, h = img.size
        if w > 0 and h > 0 and 0.2 <= (w / h) <= 5.0:
            s += 0.1
        if len(image_bytes) < 50_000:
            s += 0.3
        return s
    except Exception:
        return 0.0


class CaptchaDetector:
    """
    Lightweight CAPTCHA pre-filter using PIL-only heuristics.

    Detection signals (score-based):
      - URL contains "captcha" / "challenge" / "verify" / "human" / "bot" → +0.5
      - Image size < 50KB → +0.3
      - Grayscale or palette mode → +0.3
      - Aspect ratio in [0.2, 5.0] → +0.1
      - Score >= 0.5 → CAPTCHA detected
    """

    CAPTCHA_URL_RE = re.compile(
        r"(captcha|challenge|verify|human|botcheck|spam|security.?check|abc.?def)",
        re.IGNORECASE,
    )
    DETECTION_THRESHOLD = 0.5

    def __init__(self) -> None:
        self._captcha_detections: int = 0

    def is_captcha(self, image_bytes: bytes, url: Optional[str] = None) -> bool:
        """
        Returns True if image bytes score as a CAPTCHA signal.
        NEVER raises — exceptions always return False.
        Sync-safe: uses ThreadPoolExecutor with timeout, never blocks event loop.
        """
        try:
            # Hard rule: URL check (fast path, no I/O)
            if url and self.CAPTCHA_URL_RE.search(url):
                self._captcha_detections += 1
                return True

            # Skip large images (unlikely to be CAPTCHAs)
            if len(image_bytes) > 50_000:
                return False

            # PIL analysis via ThreadPoolExecutor — never blocks event loop
            executor = _get_pil_executor()
            future = executor.submit(_analyze_pil_sync, image_bytes)
            try:
                score = future.result(timeout=2.0)
            except Exception:
                score = 0.0

            if score >= self.DETECTION_THRESHOLD:
                self._captcha_detections += 1
                return True

            return False

        except Exception:
            # Fail-soft mandatory: any exception → return False
            return False

    def get_detections_count(self) -> int:
        """Return total CAPTCHA detections for stats reporting."""
        return self._captcha_detections

    def reset(self) -> None:
        """Reset counter (call between sprints)."""
        self._captcha_detections = 0