"""
LightpandaManager — headless browser management for JS-heavy page rendering.

Extracted from coordinators/fetch_coordinator.py (Sprint 45 refactor).
Manages Lightpanda process lifecycle, CDP endpoint, and nodriver-based JS rendering.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re

import aiohttp

try:
    import nodriver

    NODRIVER_AVAILABLE = True
except ImportError:
    NODRIVER_AVAILABLE = False

import logging

from hledac.universal.paths import DB_ROOT

logger = logging.getLogger(__name__)

AIOHTTP_AVAILABLE = aiohttp is not None


class LightpandaManager:
    """Manages Lightpanda headless browser for JS-heavy page rendering."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._endpoint = os.environ.get(
            "CDP_ENDPOINT", "ws://127.0.0.1:9222"
        )
        self._bin_path = DB_ROOT / 'bin' / 'lightpanda'

    async def _download_if_missing(self) -> None:
        """Download Lightpanda binary if missing."""
        if self._bin_path.exists():
            return
        os.makedirs(self._bin_path.parent, exist_ok=True)

        if not AIOHTTP_AVAILABLE:
            logger.warning("[LIGHTPANDA] aiohttp not available, cannot download")
            raise ImportError("aiohttp not available")

        url = "https://github.com/lightpanda-io/browser/releases/latest/download/lightpanda-aarch64-macos"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        # P3-7 fix: compute hash for security auditing
                        # Expected hash from trusted source stored in LIGHTPANDA_SHA256 env var
                        actual_hash = hashlib.sha256(content).hexdigest()
                        expected_hash = os.environ.get('LIGHTPANDA_SHA256')
                        if not expected_hash:
                            raise ValueError(
                                "[LIGHTPANDA] LIGHTPANDA_SHA256 env var must be set to verify "
                                "binary integrity before download. Set it to the trusted SHA256 hash."
                            )
                        if actual_hash != expected_hash:
                            raise ValueError(
                                f"[LIGHTPANDA] Hash mismatch! "
                                f"expected={expected_hash}, actual={actual_hash}"
                            )
                        logger.info(f"[LIGHTPANDA] Hash verified: {actual_hash[:16]}...")
                        with open(self._bin_path, 'wb') as f:
                            f.write(content)
                        os.chmod(self._bin_path, 0o755)
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
                str(self._bin_path), "serve", "--port", "9222",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            # Wait for port to be open
            for _ in range(50):  # max 5s
                try:
                    reader, writer = await asyncio.open_connection('127.0.0.1', 9222)
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
        if not NODRIVER_AVAILABLE:
            logger.warning("[LIGHTPANDA] nodriver not installed, falling back")
            raise ImportError("nodriver not available")

        await self.ensure_running()

        from nodriver import Config, start

        config = Config(browserWSEndpoint=self._endpoint)
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
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
