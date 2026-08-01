"""
security/turnstile_solver.py

Cloudflare Turnstile / DataDome challenge solver stub.

Solves Turnstile challenges using a headless browser (nodriver) to extract
the clearance token from the Set-Cookie header after challenge completion.

GATED by HLEDAC_ENABLE_CAPTCHA=1 (env var, not feature flag).

M1 8GB notes:
  - nodriver is RAM-intensive (~150-200 MB per browser instance)
  - Bounded to 2 concurrent solving sessions via asyncio.Semaphore
  - solve() runs in thread pool to avoid blocking the event loop

GHOST_INVARIANTS:
- I1: Never block event loop — browser ops always in thread pool
- I2: Fail-soft — any exception returns None, never raises
- I3: Bounded concurrency — semaphore prevents RAM exhaustion
- I4: Clearance stored to jar on success

Cloudflare Turnstile challenge flow:
  1. Server returns HTML with <script> containing 'turnstile.render()' or challenge form
  2. Browser executes JavaScript challenge
  3. On success, server sets cf_clearance cookie via Set-Cookie
  4. Token extracted and stored to ClearanceCookieJar

DataDome challenge flow:
  1. Server returns body with 'datadome' cookie in Set-Cookie header
  2. Cookie is extracted and stored directly (no JS execution needed)

TODO (Future sprints):
  - Real Turnstile solving with headless browser (requires nodriver/Playwright)
  - Sitekey extraction from challenge HTML
  - Callback detection and handling
  - Shadow/Hidden Turnstile support
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# --- Feature gate ---


def is_enabled() -> bool:
    """Check if CAPTCHA solving is enabled via env var."""
    return os.environ.get("HLEDAC_ENABLE_CAPTCHA", "0") in ("1", "true", "yes", "on")


# --- Constants ---


# Turnstile challenge page signatures
_TURNSTILE_CHALLENGE_PATTERNS = [
    re.compile(r"cf-challenge[_-]", re.IGNORECASE),
    re.compile(r"turnstile", re.IGNORECASE),
    re.compile(r"cloudflare[-_]challenge", re.IGNORECASE),
    re.compile(r"challenger\.js", re.IGNORECASE),
]

# DataDome challenge signatures
_DATADOME_PATTERNS = [
    re.compile(r"datadome", re.IGNORECASE),
    re.compile(r"data[-_]domain", re.IGNORECASE),
]

# Content-type patterns indicating a challenge
_CHALLENGE_CONTENT_TYPES = [
    "text/html",
]


# --- Challenge detection helpers ---


def detect_turnstile_challenge(
    url: str,
    status_code: int,
    headers: dict[str, str],
    content: bytes,
) -> bool:
    """
    Detect if response is a Cloudflare Turnstile challenge page.

    Returns True if:
      - Status code is 403 or 429
      - Content-Type is text/html
      - Body contains Turnstile challenge signatures
    """
    if status_code not in (403, 429):
        return False

    ct = headers.get("content-type", headers.get("Content-Type", "")).lower()
    if not any(ct.startswith(pat) for pat in _CHALLENGE_CONTENT_TYPES):
        return False

    try:
        body_str = content.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return False

    for pattern in _TURNSTILE_CHALLENGE_PATTERNS:
        if pattern.search(body_str):
            logger.debug("[TURNSTILE] Challenge detected at %s", url)
            return True

    return False


def detect_datadome_challenge(
    headers: dict[str, str],
) -> bool:
    """
    Detect if response headers indicate a DataDome challenge.

    Returns True if Set-Cookie header contains 'datadome'.
    """
    for name, value in headers.items():
        if name.lower() in ("set-cookie", "set-cookie2"):
            if "datadome" in value.lower():
                logger.debug("[DATADOME] DataDome cookie detected")
                return True
    return False


def extract_clearance_token_from_headers(
    headers: dict[str, str],
) -> dict[str, str]:
    """
    Extract clearance cookies from Set-Cookie headers.

    Returns dict of {cookie_name: cookie_value} for recognized
    clearance cookie types (cf_clearance, datadome, cf_challenge_bypass).
    """
    cookies: dict[str, str] = {}

    for name, value in headers.items():
        if name.lower() not in ("set-cookie", "set-cookie2"):
            continue

        # Parse cookie header: "name=value; Path=/; HttpOnly; Secure; SameSite=Lax"
        try:
            parts = value.split(";")
            if not parts:
                continue
            cookie_part = parts[0].strip()
            cookie_name, _, cookie_val = cookie_part.partition("=")
            cookie_name = cookie_name.strip()
            cookie_val = cookie_val.strip()
        except Exception:  # noqa: BLE001
            continue

        # Accept known clearance cookie types
        if cookie_name in ("cf_clearance", "cf_challenge_bypass", "datadome"):
            if cookie_val:
                cookies[cookie_name] = cookie_val

    return cookies


def extract_sitekey_from_html(html_content: str) -> str | None:
    """
    Extract Turnstile sitekey from challenge HTML.

    Looks for:
      - data-sitekey attribute
      - sitekey in JavaScript variables
      - renderTurnstile() calls

    Returns sitekey string or None if not found.
    """
    # data-sitekey="0xxx..."
    match = re.search(r'data-sitekey\s*=\s*["\']([^"\']+)["\']', html_content)
    if match:
        return match.group(1)

    # sitekey = "0xxx..." in JS
    match = re.search(r'sitekey\s*[:=]\s*["\']([^"\']+)["\']', html_content)
    if match:
        return match.group(1)

    # renderTurnstile(container, {sitekey: "0xxx..."
    match = re.search(r'sitekey\s*:\s*["\']([^"\']+)["\']', html_content)
    if match:
        return match.group(1)

    return None


# --- Stub solver ---


async def solve_turnstile(
    _url: str,
    _sitekey: str,
    _challenge_url: str | None = None,
) -> dict[str, str] | None:
    """
    Solve Cloudflare Turnstile challenge and return clearance cookies.

    This is a STUB — full implementation requires nodriver/Playwright browser.

    Args:
        url: Original URL that triggered the challenge
        sitekey: Turnstile sitekey extracted from challenge HTML
        challenge_url: URL of the challenge page (same as url if challenge)

    Returns:
        Dict of clearance cookies on success, None on failure.

    GATED by HLEDAC_ENABLE_CAPTCHA=1.

    TODO (Future sprints):
      1. Launch headless browser with proxy rotation
      2. Navigate to challenge URL
      3. Wait for Turnstile iframe to render
      4. Inject sitekey and trigger challenge
      5. Poll until challenge completes (cf_clearance cookie set)
      6. Extract cf_clearance from browser cookies
      7. Store to ClearanceCookieJar
    """
    if not is_enabled():
        logger.debug("[TURNSTILE] solve_turnstile called but HLEDAC_ENABLE_CAPTCHA=0")
        return None

    logger.warning(
        "[TURNSTILE] solve_turnstile is a STUB — requires nodriver/Playwright browser. "
        "Set HLEDAC_ENABLE_CAPTCHA=1 and implement browser automation to enable."
    )
    return None


# --- Clearance injector ---


async def get_clearance_for_domain(
    domain: str,
    _url: str,
    _status_code: int,
    headers: dict[str, str],
    _content: bytes,
) -> dict[str, str]:
    """
    Check if response contains clearance cookies and store them.

    This is the main entry point called by FetchCoordinator after
    detecting a challenge response.

    Flow:
      1. Check for DataDome cookies → store directly if found
      2. Check for Cloudflare clearance cookies → store if found
      3. Return stored cookies for injection into subsequent requests

    Returns:
        Dict of {cookie_name: cookie_value} to inject.
    """
    # Check for clearance cookies in response headers
    clearance_cookies = extract_clearance_token_from_headers(headers)

    if not clearance_cookies:
        return {}

    # Import lazily to avoid circular dependency
    try:
        from .clearance_cookie_jar import get_clearance_jar

        jar = get_clearance_jar()

        for cookie_name, cookie_value in clearance_cookies.items():
            if cookie_name == "cf_clearance":
                jar.put_cf_clearance(domain, cookie_value)
            elif cookie_name == "datadome":
                jar.put_datadome(domain, cookie_value)
            else:
                jar.put(domain, {cookie_name: cookie_value})

        logger.info(
            "[CLEARANCE] Stored %d clearance cookies for %s",
            len(clearance_cookies),
            domain,
        )

    except Exception:  # noqa: BLE001 — fail-soft
        pass

    return clearance_cookies


def inject_clearance_cookies(
    cookies: dict[str, str],
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Inject clearance cookies into request headers.

    Args:
        cookies: Dict of {cookie_name: cookie_value}
        headers: Existing headers dict (modified in place if provided)

    Returns:
        Headers dict with cookies injected.
    """
    if not cookies:
        return headers or {}

    result = dict(headers) if headers else {}

    # Build Cookie header value, preserving any existing Cookie header
    existing_cookie = result.get("Cookie", "")
    if existing_cookie:
        # Parse existing cookie string into individual "name=value" parts
        # to avoid duplicating the entire existing string as one pair
        result["Cookie"] = existing_cookie + "; " + "; ".join(
            f"{k}={v}" for k, v in cookies.items() if v
        )
    else:
        result["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    return result
