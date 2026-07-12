"""
LightpandaManager — headless browser management for JS-heavy page rendering.

Extracted from coordinators/fetch_coordinator.py (Sprint 45 refactor).
Manages Lightpanda process lifecycle, CDP endpoint, and nodriver-based JS rendering.
"""

import asyncio
import hashlib
import logging
import os
import re
from typing import Any

import httpx

from hledac.universal.paths import DB_ROOT

logger = logging.getLogger(__name__)


# Sprint F259C: Lazy nodriver import.
# nodriver >= 0.50 has a non-UTF-8 byte in cdp/network.py:1345 that triggers
# SyntaxError on Python 3.14 (strict UTF-8 enforcement). Importing nodriver
# at module level pulls in the broken cdp/network.py via nodriver/__init__.py
# → nodriver/cdp/__init__.py chain, which then breaks ANY module that imports
# tools.lightpanda_manager (e.g. runtime/sprint_scheduler.py via the
# intelligence → knowledge → tools import chain).
#
# Fix: defer the import to first use, catch both ImportError AND SyntaxError,
# expose a lazy `get_nodriver_available()` for callers to gate on.
_NODRIVER_AVAILABLE: bool | None = None


def get_nodriver_available() -> bool:
    """Lazy check for nodriver availability.

    Catches ImportError (package not installed) AND SyntaxError
    (nodriver/cdp/network.py has UTF-8 issue on Py 3.14). Result is
    memoized for the process lifetime — cheap repeated checks.
    """
    global _NODRIVER_AVAILABLE
    if _NODRIVER_AVAILABLE is None:
        try:
            import nodriver  # noqa: F401

            _NODRIVER_AVAILABLE = True
        except (ImportError, SyntaxError):
            _NODRIVER_AVAILABLE = False
    return _NODRIVER_AVAILABLE


# Backwards-compat alias — some callers may import this name directly.
# Resolved lazily on first attribute access; if they import at module
# level they get the unresolved sentinel, which is fine because they
# should be using get_nodriver_available() instead.
def __getattr__(name: str) -> bool:
    if name == "NODRIVER_AVAILABLE":
        return get_nodriver_available()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


HTTPX_AVAILABLE = httpx is not None


class LightpandaManager:
    """Manages Lightpanda headless browser for JS-heavy page rendering."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._endpoint = os.environ.get("CDP_ENDPOINT", "ws://127.0.0.1:9222")
        self._bin_path = DB_ROOT / "bin" / "lightpanda"

    async def _download_if_missing(self) -> None:
        """Download Lightpanda binary if missing."""
        if self._bin_path.exists():
            return
        os.makedirs(self._bin_path.parent, exist_ok=True)

        if not HTTPX_AVAILABLE:
            logger.warning("[LIGHTPANDA] httpx not available, cannot download")
            raise ImportError("httpx not available")

        url = "https://github.com/lightpanda-io/browser/releases/latest/download/lightpanda-aarch64-macos"
        try:
            async with httpx.AsyncClient() as session:
                async with session.get(url) as response:
                    if response.status_code == 200:
                        content = await response.read()
                        # P3-7 fix: compute hash for security auditing
                        # Expected hash from trusted source stored in LIGHTPANDA_SHA256 env var
                        actual_hash = hashlib.sha256(content).hexdigest()
                        expected_hash = os.environ.get("LIGHTPANDA_SHA256")
                        if not expected_hash:
                            raise ValueError(
                                "[LIGHTPANDA] LIGHTPANDA_SHA256 env var must be set to verify "
                                "binary integrity before download. Set it to the trusted SHA256 hash."
                            )
                        if actual_hash != expected_hash:
                            raise ValueError(
                                f"[LIGHTPANDA] Hash mismatch! expected={expected_hash}, actual={actual_hash}"
                            )
                        logger.info(f"[LIGHTPANDA] Hash verified: {actual_hash[:16]}...")
                        with open(self._bin_path, "wb") as f:
                            f.write(content)
                        # SECURITY: 0o755 is correct for a downloaded binary.
                        # The file is verified against LIGHTPANDA_SHA256 (trusted hash
                        # from env var) before chmod is called — no symlink or path
                        # injection risk at this point. Executable bit is required for
                        # asyncio.create_subprocess_exec to spawn the process.
                        # Semgrep warning is a false positive; 0o755 is standard for
                        # executables in $PATH-accessible dirs.
                        # nosemgrep: python.lang.security.audit.insecure-file-permissions
                        os.chmod(self._bin_path, 0o755)  # nosemgrep
                    else:
                        logger.warning(f"[LIGHTPANDA] Download failed: {resp.status}")
        except Exception as e:
            logger.warning(f"[LIGHTPANDA] Download error: {e}")
            raise

    async def ensure_running(self) -> None:
        """Ensure Lightpanda process is running."""
        if self._proc is None or self._proc.returncode is not None:
            await self._download_if_missing()
            self._proc = await asyncio.create_subprocess_exec(
                str(self._bin_path),
                "serve",
                "--port",
                "9222",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Wait for port to be open
            for _ in range(50):  # max 5s
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", 9222)
                    writer.close()
                    await writer.wait_closed()
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Lightpanda failed to start")

    # SEC-07: Guard for tab.evaluate() - prevents JS injection if made dynamic
    # Currently uses static string literal (safe). If refactored to accept dynamic input,
    # validate with _SAFE_JS_IDENTIFIER_PATTERN below.
    _SAFE_JS_PATTERN = re.compile(r"^[a-zA-Z0-9_.\[\]]+$")

    def _validate_js_expression(self, expr: str) -> str:
        """Validate a JavaScript expression is safe for tab.evaluate()."""
        if not self._SAFE_JS_PATTERN.match(expr):
            raise ValueError(f"Unsafe JS expression rejected: {expr!r}")
        return expr

    async def fetch_js(self, url: str, proxy: str | None = None) -> bytes:
        """Fetch URL with JS rendering using nodriver."""
        if not get_nodriver_available():
            logger.warning("[LIGHTPANDA] nodriver not installed, falling back")
            raise ImportError("nodriver not available")

        await self.ensure_running()

        from nodriver import Config, start

        # `browserWSEndpoint` is a Playwright/Puppeteer convention; nodriver's
        # Config forwards arbitrary **kwargs to add_argument() which expects
        # `bool | None`. The endpoint value is a string by design — silence
        # the static type check (runtime semantics unchanged).
        _endpoint_dict: dict[str, Any] = {"browserWSEndpoint": self._endpoint}
        config = Config(**_endpoint_dict)
        browser = await start(config)

        try:
            if proxy:
                await browser.settings.set_proxy(proxy)

            tab = await browser.get(url)
            await tab.wait_domcontentloaded()
            # SEC-07: Static string - safe from injection. Guard validates if ever made dynamic.
            js_expr = self._validate_js_expression("document.documentElement.outerHTML")
            content = await tab.evaluate(js_expr)
            await browser.stop()
            return content.encode()
        except Exception:
            await browser.stop()
            raise

    async def close(self) -> None:
        """Terminate the Lightpanda process."""
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                async with asyncio.timeout(2.0):
                    await self._proc.wait()
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
