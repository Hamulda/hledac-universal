"""
transport/curl_cffi_transport.py

Routing policy for curl_cffi stealth lane.
Determines when to use curl_cffi vs default aiohttp.

Policy rules (in evaluation order):
1. use_js=True → False, "js_required" (JS is handled by separate lane)
2. Darknet URLs (.onion/.i2p/.b32.i2p) → False, "darknet_url"
3. Freenet → False, "freenet_not_supported"
4. HLEDAC_ENABLE_CURL_CFFI != "1" → False, "curl_cffi_disabled_env"
5. curl_cffi missing → False, "curl_cffi_missing"
6. use_stealth=True → True, "explicit_stealth"
7. prior_status in {403, 429} → True, "status_403_or_429"
8. protection_hint in cloudflare/akamai/datadome/imperva → True, "protection_detected"
9. otherwise → False, "default_aiohttp"

Tor/I2P/JS are protected from accidental curl routing.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

# Protection hints that trigger curl_cffi escalation
_PROTECTION_HINTS = {"cloudflare", "akamai", "datadome", "imperva", "perimeterx", "incapsula"}

# Darknet TLDs that must NOT use curl_cffi (handled by tor/i2p transports)
_DARKNET_TLDS = {".onion", ".i2p", ".b32.i2p"}
_FREENET_TLD = ".freenet"


def should_use_curl_cffi(
    url: str,
    *,
    use_stealth: bool = False,
    use_js: bool = False,
    prior_status: Optional[int] = None,
    prior_error: Optional[str] = None,
    protection_hint: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Determine if curl_cffi stealth lane should be used for this URL.

    Returns:
        (should_use_curl_cffi: bool, reason: str)
    """
    # Rule 1: JS requires separate lane (not curl_cffi)
    if use_js:
        return False, "js_required"

    # Rule 2: Darknet URLs use tor/i2p transports, not curl_cffi
    url_lower = url.lower()
    for dark_tld in _DARKNET_TLDS:
        if dark_tld in url_lower:
            return False, "darknet_url"

    # Rule 3: Freenet not supported
    if _FREENET_TLD in url_lower:
        return False, "freenet_not_supported"

    # Rule 4: Env gate
    env_value = os.environ.get("HLEDAC_ENABLE_CURL_CFFI", "")
    if env_value != "1":
        return False, "curl_cffi_disabled_env"

    # Rule 5: curl_cffi availability checked at runtime by caller
    # (we return the reason here as a policy decision, actual availability
    # is checked in the runtime)

    # Rule 6: Explicit stealth flag
    if use_stealth:
        return True, "explicit_stealth"

    # Rule 7: Prior status suggests server-side protection
    if prior_status in {403, 429}:
        return True, "status_403_or_429"

    # Rule 8: Known protection system detected
    if protection_hint and protection_hint.lower() in _PROTECTION_HINTS:
        return True, "protection_detected"

    # Rule 9: Default — use aiohttp hot-path
    return False, "default_aiohttp"
