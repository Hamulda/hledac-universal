"""
UA Rotator — Single Source of Truth for User-Agent rotation
===========================================================

.. deprecated::
    This module is scheduled for integration into layers.communication
    or layers.stealth for unified browser fingerprint management.

Design (Issue 10.2):

  - Canonical UA pool: diverse, modern browsers (Chrome 124-136, Firefox 133-136,
    Safari 17-18, Edge 124-136) across Windows/macOS/Linux/Android/iOS
  - JA3 consistency: get_ua_for_profile() returns UA matching curl_cffi impersonate target
    so TLS fingerprint + HTTP header never mismatch
  - Thread-safe via _RNG.choice (GIL-protected on CPython)
  - Exposed as module-level functions (drop-in replacement for public_fetcher's
    get_random_ua/build_randomized_headers) AND as UARotator class for callers
    that need stateful round-robin or platform/browser filtering

Architecture seam:
  - curl_cffi_fetch.py imports get_ua_for_profile() → canonical JA3 consistency path
  - public_fetcher.py imports get_random_ua() + build_randomized_headers()
  - stealth_crawler.py: keep as-is (independent intelligence lane)
  - advanced_web/stealth_browser.py: keep as-is (nodriver-only path)

M1 8GB: all data is tuples (immutable) — no RAM growth under load.
"""

# Deprecation warning
import warnings
warnings.warn(
    "layers.ua_rotator is deprecated and scheduled for integration into layers.communication or layers.stealth.",
    DeprecationWarning,
    stacklevel=2,
)


import secrets
import threading
from typing import Literal

from hledac.universal._core.locks import LockCategory, register_lock
from _core import aclose

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

__all__ = [
    'get_random_ua',
    'get_ua_for_profile',
    'get_random_accept_language',
    'get_random_accept_encoding',
    'build_randomized_headers',
    'UARotator',
]

# --------------------------------------------------------------------------------
# Canonical UA pool — must match curl_cffi impersonate targets
# --------------------------------------------------------------------------------
# curl_cffi impersonate targets (from F263/F265B):
#   chrome120-136, safari15_5-safari18_0, firefox109-136, edge99-edge101, okhttp4
# Each tuple: (ua_substring_for_detection, full_ua_string)
# UA must match the TLS fingerprint family — Chrome UA + Safari impersonate = detectable.

_ChromeUA = tuple[str, str]   # (version_substring, full_ua)
_FirefoxUA = tuple[str, str]
_SafariUA = tuple[str, str]
_EdgeUA = tuple[str, str]

# Chrome 124-136 stable (Windows/macOS/Linux/Android)
_CHROME_UAS: tuple[_ChromeUA, ...] = (
    ("Chrome/136.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
    ("Chrome/136.0.0.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
    ("Chrome/136.0.0.0", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
    ("Chrome/135.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"),
    ("Chrome/135.0.0.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"),
    ("Chrome/134.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"),
    ("Chrome/134.0.0.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"),
    ("Chrome/133.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
    ("Chrome/133.0.0.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
    ("Chrome/124.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Chrome/124.0.0.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Chrome/124.0.0.0", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Chrome/120.0.0.0", "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    )

# Firefox 133-136 ESR (Windows/macOS/Linux)
_FIREFOX_UAS: tuple[_FirefoxUA, ...] = (
    ("Firefox/136.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0"),
    ("Firefox/136.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:136.0) Gecko/20100101 Firefox/136.0"),
    ("Firefox/136.0", "Mozilla/5.0 (X11; Linux x86_64; rv:136.0) Gecko/20100101 Firefox/136.0"),
    ("Firefox/135.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"),
    ("Firefox/135.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0"),
    ("Firefox/133.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("Firefox/133.0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("Firefox/133.0", "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    )

# Safari 17-18 (macOS Sonoma 14.4 / Sequoia 15 / iOS 17-18)
_SAFARI_UAS: tuple[_SafariUA, ...] = (
    ("Version/18.0 Safari/605.1.15", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"),
    ("Version/17.4 Safari/605.1.15", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    ("Version/17.4 Safari/604.1", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    ("Version/17.4 Safari/604.1", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
    )

# Edge 124-136 (Windows only — Edge is Chromium-based)
_EDGE_UAS: tuple[_EdgeUA, ...] = (
    ("Edg/136.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"),
    ("Edg/135.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0"),
    ("Edg/134.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"),
    ("Edg/133.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0"),
    ("Edg/124.0.0.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    )

# All pools flat — for _RNG.choice across all browsers
_ALL_UAS: tuple[tuple[str, str], ...] = _CHROME_UAS + _FIREFOX_UAS + _SAFARI_UAS + _EDGE_UAS

# curl_cffi profile → UA family mapping (for JA3 consistency)
# Maps curl_cffi impersonate string → (pool, version_substring)
_CURL_CFFI_TO_FAMILY: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    # Chrome family
    "chrome136": (_CHROME_UAS, "Chrome/136"),
    "chrome135": (_CHROME_UAS, "Chrome/135"),
    "chrome134": (_CHROME_UAS, "Chrome/134"),
    "chrome133": (_CHROME_UAS, "Chrome/133"),
    "chrome131": (_CHROME_UAS, "Chrome/131"),
    "chrome124": (_CHROME_UAS, "Chrome/124"),
    "chrome120": (_CHROME_UAS, "Chrome/120"),
    "chrome110": (_CHROME_UAS, "Chrome/120"),   # closest available
    "chrome99_android": (_CHROME_UAS, "Chrome/120"),  # Android UA maps to Chrome pool
    # Firefox family
    "firefox136": (_FIREFOX_UAS, "Firefox/136"),
    "firefox135": (_FIREFOX_UAS, "Firefox/135"),
    "firefox133": (_FIREFOX_UAS, "Firefox/133"),
    "firefox117": (_FIREFOX_UAS, "Firefox/117"),
    "firefox109": (_FIREFOX_UAS, "Firefox/109"),
    # Safari family
    "safari18_0": (_SAFARI_UAS, "Version/18"),
    "safari17_4": (_SAFARI_UAS, "Version/17"),
    "safari17_0": (_SAFARI_UAS, "Version/17"),
    "safari15_5": (_SAFARI_UAS, "Version/15"),
    # Edge family (Chromium-based, uses Chrome UA with Edg/ token)
    "edge101": (_EDGE_UAS, "Edg/101"),
    "edge99": (_EDGE_UAS, "Edg/99"),
    # OkHttp (Android)
    "okhttp4": (_CHROME_UAS, "Chrome/120"),
}

# --------------------------------------------------------------------------------
# Accept-Language pool (F229)
# --------------------------------------------------------------------------------
_ACCEPT_LANGUAGE_POOL: tuple[str, ...] = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
    "en-US,en;q=0.9,ja;q=0.8",
    "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "zh-CN,zh;q=0.9,en;q=0.8",
    )

# --------------------------------------------------------------------------------
# Accept-Encoding
# --------------------------------------------------------------------------------
_ACCEPT_ENCODING_POOL: tuple[str, ...] = (
    "gzip, deflate, br",
    "gzip, deflate",
    "deflate, gzip, br",
    )

# --------------------------------------------------------------------------------
# Module-level thread-safe helpers
# --------------------------------------------------------------------------------


@register_lock(LockCategory.CACHE)
def _ua_lock() -> threading.Lock:
    """Module-level lock for UA rotation."""
    return threading.Lock()


def get_random_ua() -> str:
    """Return a random User-Agent from the canonical pool (thread-safe).

    Use this when you need a browser UA without caring about TLS profile
    alignment. For curl_cffi requests, use get_ua_for_profile() instead.
    """
    with _ua_lock():
        return _RNG.choice(_ALL_UAS)[1]


def get_ua_for_profile(profile: str) -> str:
    """Return a UA that matches the given curl_cffi impersonate profile (thread-safe).

    This is the canonical path for curl_cffi_fetch.py — ensures the HTTP
    User-Agent header matches the TLS (JA3/H2) fingerprint so no mismatch
    is detectable.

    Args:
        profile: curl_cffi impersonate target, e.g. "chrome136", "safari18_0",
            "firefox133", "edge101".

    Returns:
        A UA string from the matching browser family. Falls back to
        chrome136 pool if profile is unknown.
    """
    with _ua_lock():
        entry = _CURL_CFFI_TO_FAMILY.get(profile)
        if entry is None:
            # Unknown profile — fall back to chrome136
            pool: tuple[_ChromeUA, ...] = _CHROME_UAS
            version_substr = "Chrome/136"
        else:
            pool, version_substr = entry

        # Filter to matching version substring, pick random
        matching = [ua for ua in pool if version_substr in ua[0]]
        if not matching:
            # Fallback: any from pool
            matching = list(pool)
        return _RNG.choice(matching)[1]


def get_random_accept_language() -> str:
    """Return a random Accept-Language string (thread-safe)."""
    with _ua_lock():
        return _RNG.choice(_ACCEPT_LANGUAGE_POOL)


def get_random_accept_encoding() -> str:
    """Return a random Accept-Encoding string (thread-safe)."""
    with _ua_lock():
        return _RNG.choice(_ACCEPT_ENCODING_POOL)


# --------------------------------------------------------------------------------
# Header builder
# --------------------------------------------------------------------------------

_OS_CHOICES = ('"Windows"', '"macOS"', '"Linux"', '"Android"', '"iOS"')
_MOBILE_CHOICES = ("?0", "?1")
_CHROME_TOKEN_CHOICES = (
    '"Chromium";v="136"',
    '"Google Chrome";v="136"',
    '"Not-A.Brand";v="99"',
    )


def build_randomized_headers(
    ua: str | None = None,
    accept_language: str | None = None,
    accept_encoding: str | None = None,
) -> dict[str, str]:
    """Build a randomized headers dict for HTTP requests.

    Includes all relevant stealth headers:
      - User-Agent: random browser identity (or provided)
      - Accept-Language: random locale
      - Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
      - Accept-Encoding: gzip, deflate, br
      - Sec-Ch-Ua: random Chrome brand tokens
      - Sec-Ch-Ua-Mobile: random mobile flag
      - Sec-Ch-Ua-Platform: random OS
      - Sec-Fetch-Dest: document
      - Sec-Fetch-Mode: navigate
      - Sec-Fetch-Site: none
      - Connection: keep-alive

    No tracking headers (DNT, X-Tracking-IP, etc.).

    Args:
        ua: Optional UA override. If None, a random UA is picked.
        accept_language: Optional Accept-Language override.
        accept_encoding: Optional Accept-Encoding override.
    """
    _ua = ua if ua is not None else get_random_ua()
    _lang = accept_language if accept_language is not None else get_random_accept_language()
    _enc = accept_encoding if accept_encoding is not None else get_random_accept_encoding()

    return {
        "User-Agent": _ua,
        "Accept-Language": _lang,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": _enc,
        "Sec-Ch-Ua": _RNG.choice(_CHROME_TOKEN_CHOICES),
        "Sec-Ch-Ua-Mobile": _RNG.choice(_MOBILE_CHOICES),
        "Sec-Ch-Ua-Platform": _RNG.choice(_OS_CHOICES),
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Connection": "keep-alive",
    }


# --------------------------------------------------------------------------------
# Stateful UARotator class — for callers needing round-robin or filtering
# --------------------------------------------------------------------------------

class UARotator:
    """Stateful UA rotator with round-robin and platform/browser filtering.

    Use when you need:
      - Deterministic round-robin (vs _RNG.choice)
      - Platform or browser filtering (e.g., "give me only Safari UAs")
      - Persistent state across many requests

    Thread-safe: uses an internal threading.Lock.
    """

    __slots__ = ("_index", "_lock", "_pool")

    def __init__(
        self,
        pool: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self._pool: tuple[tuple[str, str], ...] = pool if pool is not None else _ALL_UAS
        self._index = 0
        self._lock = threading.Lock()

    def rotate(self) -> str:
        """Rotate to next UA and return it (round-robin, thread-safe)."""
        with self._lock:
            ua = self._pool[self._index % len(self._pool)][1]
            self._index += 1
            return ua

    def random(self) -> str:
        """Return a random UA from the current pool (thread-safe)."""
        with self._lock:
            return _RNG.choice(self._pool)[1]

    def peek(self) -> str:
        """Peek at the next UA without advancing the index."""
        with self._lock:
            return self._pool[self._index % len(self._pool)][1]

    @classmethod
    def for_profile(cls, profile: str) -> UARotator:
        """Create a rotator containing only UAs matching the curl_cffi profile.

        Ensures JA3 + HTTP header consistency for the lifetime of the rotator.
        """
        entry = _CURL_CFFI_TO_FAMILY.get(profile)
        if entry is None:
            pool: tuple[tuple[str, str], ...] = _CHROME_UAS
        else:
            pool, _ = entry
        return cls(pool=pool)

    @classmethod
    def for_browser(cls, browser: Literal["chrome", "firefox", "safari", "edge"]) -> UARotator:
        """Create a rotator for a specific browser family."""
        if browser == "chrome":
            pool = _CHROME_UAS
        elif browser == "firefox":
            pool = _FIREFOX_UAS
        elif browser == "safari":
            pool = _SAFARI_UAS
        elif browser == "edge":
            pool = _EDGE_UAS
        else:
            pool = _ALL_UAS
        return cls(pool=pool)

    @classmethod
    def for_platform(cls, platform: Literal["windows", "macos", "linux", "android", "ios"]) -> UARotator:
        """Create a rotator for a specific OS platform."""
        pf = platform.lower()
        filtered = [
            ua for ua in _ALL_UAS
            if (pf == "windows" and "Windows NT" in ua[1]) or
               (pf == "macos" and "Macintosh" in ua[1] and "OS X" in ua[1]) or
               (pf == "linux" and "X11" in ua[1] and "Linux" in ua[1]) or
               (pf == "android" and "Android" in ua[1]) or
               (pf == "ios" and ("iPhone" in ua[1] or "iPad" in ua[1]))
        ]
        return cls(pool=tuple(filtered) if filtered else _ALL_UAS)

    @property
    def count(self) -> int:
        """Number of UAs in the current pool."""
        return len(self._pool)
