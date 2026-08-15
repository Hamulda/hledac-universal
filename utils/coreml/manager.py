"""
CoreML service lifecycle manager.
Starts/stops the FastAPI microservice as a subprocess.
"""

import asyncio
from functools import lru_cache
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from core import aclose

# Cached lazy import — httpx loaded once on first call, cached thereafter.
# Avoids repeated module-lookup cost inside polling loops (~20 calls per startup).
@lru_cache(maxsize=1)
def _httpx_get_sync(url: str, timeout: float) -> int | None:
    """
    Sync HTTP GET → status_code or None on error.
    Cached per (url, timeout) — safe for repeated calls with same args.
    """
    import httpx  # noqa: F401 — loaded lazily, cached after first call
    try:
        return httpx.get(url, timeout=timeout).status_code
    except Exception:
        return None

logger = logging.getLogger('coreml-manager')

# E-16 FIX: auto-detect python3.12 — hardcoded py3.14-only path broke on python3.12 systems
# HLEDAC_COREML_PYTHON env var overrides everything; otherwise try shutil.which('python3.12'),
# then fall back to legacy venv path.
_venv_python = Path.home() / 'coremltools' / 'envs' / 'coremltools-py3.12' / 'bin' / 'python'
_which_312 = shutil.which('python3.12')
_COREML_PYTHON: Path = (
    Path(os.environ['HLEDAC_COREML_PYTHON'])
    if 'HLEDAC_COREML_PYTHON' in os.environ
    else (Path(_which_312) if _which_312 else _venv_python)
)
_SERVICE_SCRIPT = Path(__file__).resolve().parent / 'service.py'
_LOG_DIR = Path.home() / 'Library' / 'Logs' / 'hledac'
_LOG_FILE = _LOG_DIR / 'coreml-service.log'
_HEALTH_URL = 'http://127.0.0.1:8765/health'
_STARTUP_TIMEOUT = 10.0
_SHUTDOWN_GRACE = 5.0

class CoreMLServiceError(Exception):
    """Raised when the service fails to start or is unreachable."""

class CoreMLServiceManager:
    """
    Manages the CoreML microservice lifecycle.

    Singleton pattern — one manager per process.
    Auto-starts the service if client requests arrive and it's not running.
    Context manager support for clean teardown.
    """
    _instance: CoreMLServiceManager | None = None
    __slots__ = tuple(('_proc',))

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    @classmethod
    def get_instance(cls) -> CoreMLServiceManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_running(self) -> bool:
        """Check if the service process is alive and responding to /health (sync, blocking)."""
        if self._proc is None or self._proc.poll() is not None:
            return False
        try:
            resp = _httpx_get_sync(_HEALTH_URL, 2.0)
            return resp == 200
        except Exception:
            return False

    async def is_running_async(self) -> bool:
        """
        Check if the service process is alive and responding to /health (non-blocking).

        Wraps the sync is_running() in asyncio.to_thread() so it never blocks
        the event loop. Use this in async contexts instead of is_running().
        """
        return await asyncio.to_thread(self.is_running)

    def start(self) -> None:
        """
        Start the CoreML service subprocess synchronously.

        DEPRECATED: In async contexts, use start_async() instead.
        This sync variant blocks the event loop during the 0.5s polling intervals.

        B2-FIX: The 500ms polling sleep is a one-time startup cost (~10s max timeout).
        For async callers, wrap with asyncio.to_thread() or use start_async().
        """
        import warnings as _w
        _w.warn(
            "CoreMLServiceManager.start() is deprecated in async contexts. "
            "Use start_async() or asyncio.to_thread(start).",
            DeprecationWarning,
            stacklevel=2,
        )
        if self.is_running():
            logger.info('CoreML service already running')
            return
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fd = open(_LOG_FILE, 'a')
        try:
            self._proc = subprocess.Popen([str(_COREML_PYTHON), str(_SERVICE_SCRIPT)], stdout=log_fd, stderr=subprocess.STDOUT)
            log_fd.close()  # E-17: child inherited fd via Popen; close parent copy so EMFILE never accumulates
        except FileNotFoundError:
            log_fd.close()
            raise CoreMLServiceError(f'CoreML Python not found at {_COREML_PYTHON}. Ensure the coremltools py3.12 venv exists.')
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < _STARTUP_TIMEOUT:
            if self._proc.poll() is not None:
                raise CoreMLServiceError(f'Service process exited immediately with code {self._proc.returncode}')
            try:
                resp = _httpx_get_sync(_HEALTH_URL, 2.0)
                if resp == 200:
                    logger.info('CoreML service started (pid=%d, log=%s)', self._proc.pid, _LOG_FILE)
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        self.stop()
        raise CoreMLServiceError(f'Service did not respond to /health within {_STARTUP_TIMEOUT}s')

    async def start_async(self) -> None:
        """Start the CoreML service subprocess — non-blocking for async contexts."""
        if await self.is_running_async():
            logger.info('CoreML service already running')
            return
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fd = open(_LOG_FILE, 'a')
        try:
            self._proc = subprocess.Popen([str(_COREML_PYTHON), str(_SERVICE_SCRIPT)], stdout=log_fd, stderr=subprocess.STDOUT)
            log_fd.close()  # E-17: child inherited fd via Popen; close parent copy so EMFILE never accumulates
        except FileNotFoundError:
            log_fd.close()
            raise CoreMLServiceError(f'CoreML Python not found at {_COREML_PYTHON}. Ensure the coremltools py3.12 venv exists.')
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < _STARTUP_TIMEOUT:
            if self._proc.poll() is not None:
                raise CoreMLServiceError(f'Service process exited immediately with code {self._proc.returncode}')
            try:
                resp = await asyncio.to_thread(_httpx_get_sync, _HEALTH_URL, 2.0)
                if resp == 200:
                    logger.info('CoreML service started (pid=%d, log=%s)', self._proc.pid, _LOG_FILE)
                    return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)
        self.stop()
        raise CoreMLServiceError(f'Service did not respond to /health within {_STARTUP_TIMEOUT}s')

    def stop(self) -> None:
        """Graceful SIGTERM, force SIGKILL after 5s."""
        if self._proc is None:
            return
        pid = self._proc.pid
        self._proc.terminate()
        try:
            self._proc.wait(timeout=_SHUTDOWN_GRACE)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        logger.info('CoreML service stopped (pid=%d)', pid)

    def restart(self) -> None:
        """Stop and start the service."""
        self.stop()
        self.start()

    async def restart_async(self) -> None:
        """
        Async restart — non-blocking for event loop.

        Stops the current service and starts it asynchronously using
        start_async(). Uses is_running_async() for non-blocking health checks.
        """
        self.stop()
        await self.start_async()

    async def __aenter__(self) -> CoreMLServiceManager:
        await self.start_async()
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.stop()

    @classmethod
    async def ensure_running_async(cls) -> None:
        """Auto-start helper — starts the service if not already running (async)."""
        mgr = cls.get_instance()
        if not await mgr.is_running_async():
            await mgr.start_async()

    def ensure_running(self) -> None:
        """
        Auto-start helper — starts the service if not already running (sync).

        DEPRECATED: In async contexts, use ensure_running_async() instead.
        """
        import warnings as _w
        _w.warn(
            "CoreMLServiceManager.ensure_running() is deprecated in async contexts. "
            "Use ensure_running_async() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        mgr = self.get_instance()
        if not mgr.is_running():
            mgr.start()
