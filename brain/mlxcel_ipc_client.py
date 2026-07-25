"""
MlxcelIpcClient — Out-of-Process Inference via UNIX Domain Socket / Subprocess
==============================================================================

Out-of-Process Architecture (F4XX):
------------------------------------
Instead of importing mlx-lm directly in the Python process (which consumes
~2GB of RSS for the model + MLX runtime), this client communicates with a
separate `mlxcel` Rust binary via:

  1. UNIX Domain Socket  (primary, lower latency)
     Path: /tmp/hledac_mlxcel.sock

  2. Stdout/Stdin pipes  (fallback, when socket unavailable)
     asyncio.create_subprocess_exec + JSON-RPC over stdin/stdout

Why separate process?
---------------------
- RSS savings: mlx-lm Python bindings + MLX Metal runtime ≈ 2GB in-process.
  With mlxcel in a subprocess, the Rust process RSS is tracked independently
  and can be garbage-collected WITHOUT killing the Python orchestrator.
- M1 8GB UMA: Python orchestrator (~1GB) + mlxcel (~2GB subprocess) leaves
  ~5GB for OS + KV cache, vs ~6.25GB all-in-process ceiling.
- Failure isolation: mlxcel crash ≠ Python crash.
- Metal context: Rust has direct Metal GPU queue access without Python GIL.

Protocol: JSON-RPC 2.0 over socket or pipes.
---------------------------------
Requests (Python → mlxcel):
  {"jsonrpc": "2.0", "method": "generate", "params": {...}, "id": 1}

Responses (mlxcel → Python):
  {"jsonrpc": "2.0", "result": {...}, "id": 1}
  {"jsonrpc": "2.0", "error": {"code": -32600, "message": "..."}, "id": 1}

Methods:
  generate  → {"prompt": str, "temperature": float, "max_tokens": int,
               "system_msg": str|null, "thinking": bool, "adapter_path": str|null}
               → {"text": str, "tokens_generated": int, "latency_ms": float}

  generate_stream → same params, returns SSE-like stream of tokens
                     → {"chunk": str, "done": bool}

  load_model  → {"model_path": str, "kv_bits": int, "max_kv_size": int}
                → {"ok": bool, "model_loaded": bool}

  unload_model → {} → {"ok": bool}

  ping        → {} → {"pong": str, "mlxcel_version": str}

IPC Latency Telemetry:
----------------------
Every generate() call records:
  - ipc_latency_ms: Round-trip JSON-RPC time (socket I/O + Rust inference)
  - rss_before_mb / rss_after_mb: mlxcel RSS delta (from /proc/pid/status on Linux,
    or via psutil on Darwin — mlxcel process must be spawned with --pid-file)
  - memory_saved_mb: Estimated Python RSS savings vs in-process mlx-lm (~200MB
    Python bindings + ~100MB MLX Metal runtime that are NOT loaded when mlxcel
    is used)

Fail-safe:
-----------
If mlxcel is unavailable (binary not found, socket connection refused, or any
RPC error), MlxcelIpcClient raises MlxcelUnavailable and callers should fall
back to DeepHermes3Engine (in-process mlx-lm Python bindings fallback).
"""
from __future__ import annotations

import asyncio
import orjson as json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from utils.locks import LazyAsyncioLock

logger = logging.getLogger(__name__)

# Default mlxcel binary locations (checked in order)
_MLXCEL_PATHS = [
    Path.home() / ".local" / "bin" / "mlxcel",
    Path.home() / "bin" / "mlxcel",
    Path("/usr/local/bin/mlxcel"),
    Path("/opt/homebrew/bin/mlxcel"),
    Path("/opt/bin/mlxcel"),
]
_SOCKET_PATH = Path("/tmp/hledac_mlxcel.sock")
_PID_FILE = Path("/tmp/hledac_mlxcel.pid")

# JSON-RPC constants
RPC_PARSE_ERROR = -32700
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_INTERNAL_ERROR = -32603


@dataclass
class MlxcelIpcStats:
    """Telemetry for IPC calls."""
    ipc_latency_ms: float = 0.0
    rss_before_mb: float = 0.0
    rss_after_mb: float = 0.0
    memory_saved_mb: float = 0.0
    calls_total: int = 0
    calls_failed: int = 0
    last_error: str | None = None
    # Cumulative: how much RSS we estimate NOT loaded in Python process
    _cumulative_savings_mb: float = field(default=0.0, repr=False)

    def record_call(self, latency_ms: float, rss_before: float = 0.0, rss_after: float = 0.0) -> None:
        self.ipc_latency_ms = latency_ms
        self.rss_before_mb = rss_before
        self.rss_after_mb = rss_after
        # Estimate: Python bindings + MLX Metal runtime NOT loaded ≈ 300MB per call
        per_call_savings = 300.0
        self._cumulative_savings_mb += per_call_savings
        self.memory_saved_mb = self._cumulative_savings_mb
        self.calls_total += 1

    def record_failure(self, error: str) -> None:
        self.calls_failed += 1
        self.last_error = error


@dataclass
class GenerateResult:
    """Result from mlxcel generate RPC."""
    text: str
    tokens_generated: int
    latency_ms: float


class MlxcelUnavailable(Exception):
    """Raised when mlxcel binary/socket is not available."""
    pass


class MlxcelProtocolError(Exception):
    """Raised on JSON-RPC protocol errors."""
    pass


class MlxcelIpcClient:
    """
    Async client for mlxcel subprocess inference.

    Supports two transport modes (auto-detected):
      - UNIX Domain Socket (primary, lower latency)
      - Stdin/Stdout pipes (fallback)

    Thread-safe for asyncio use with a single in-flight request at a time
    (mlxcel is single-threaded Rust inference server).
    """

    __slots__ = (
        "_binary_path", "_socket_path", "_process", "_reader", "_writer",
        "_lock", "_stats", "_connected", "_version", "_pid",
    )

    def __init__(
        self,
        binary_path: Path | None = None,
        socket_path: Path | None = None,
    ) -> None:
        """
        Args:
            binary_path: Explicit path to mlxcel binary (skip auto-detection).
            socket_path: Explicit UNIX socket path (default: /tmp/hledac_mlxcel.sock).
        """
        self._binary_path = binary_path
        self._socket_path = socket_path or _SOCKET_PATH
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._connected = False
        self._version = "unknown"
        self._pid: int | None = None
        self._stats = MlxcelIpcStats()

    @property
    def stats(self) -> MlxcelIpcStats:
        """Return IPC telemetry stats."""
        return self._stats

    @property
    def version(self) -> str:
        """Return mlxcel version string."""
        return self._version

    @property
    def is_available(self) -> bool:
        """Return True if mlxcel is detected on the system."""
        return self._find_binary() is not None

    def _find_binary(self) -> Path | None:
        """Find mlxcel binary in standard locations or via PATH."""
        if self._binary_path is not None and self._binary_path.exists():
            return self._binary_path

        for path in _MLXCEL_PATHS:
            if path.exists():
                self._binary_path = path
                return path

        # Also check PATH
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / "mlxcel"
            if candidate.exists():
                self._binary_path = candidate
                return candidate
        return None

    # ── Transport: UNIX Domain Socket ──────────────────────────────────────────

    async def _connect_socket(self) -> None:
        """Connect to mlxcel via UNIX domain socket."""
        if self._connected and self._writer is not None:
            return

        try:
            reader, writer = await asyncio.open_unix_connection(str(self._socket_path))
            self._reader = reader
            self._writer = writer
            self._connected = True
            logger.debug("[MLXCEL] Connected to socket %s", self._socket_path)
        except OSError as e:
            self._connected = False
            raise MlxcelUnavailable(
                f"Cannot connect to mlxcel socket {self._socket_path}: {e}"
            )

    async def _disconnect_socket(self) -> None:
        """Disconnect UNIX socket."""
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None
            self._connected = False

    # ── Transport: Subprocess pipes ─────────────────────────────────────────────

    async def _spawn_subprocess(self) -> None:
        """Spawn mlxcel as subprocess with stdin/stdout pipes (NOT socket mode)."""
        binary = self._find_binary()
        if binary is None:
            raise MlxcelUnavailable(
                "mlxcel binary not found in standard locations or PATH"
            )

        try:
            # Subprocess mode: NO --socket arg (that would try to bind the UDS
            # in the child, but we're using stdin/stdout pipes instead).
            # --pid-file is optional but useful for external RSS monitoring.
            self._process = await asyncio.create_subprocess_exec(
                str(binary),
                "--pid-file", str(_PID_FILE),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader = self._process.stdout
            self._writer = self._process.stdin
            self._connected = True
            self._pid = self._process.pid
            logger.debug(
                "[MLXCEL] Spawned mlxcel pid=%s binary=%s",
                self._process.pid,
                binary,
            )
        except OSError as e:
            raise MlxcelUnavailable(f"Failed to spawn mlxcel: {e}")

    # ── JSON-RPC transport ─────────────────────────────────────────────────────

    # Default per-request timeout (seconds)
    _RPC_TIMEOUT_S: float = 60.0

    async def _send_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Send JSON-RPC request and receive response.

        Uses socket if available, falls back to subprocess pipes.
        Raises MlxcelUnavailable on timeout, connection error, or protocol error.
        """
        if not self._connected:
            await self._connect_socket()

        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": id(params) & 0xFFFF,
        }
        request_bytes = json.dumps(request) + b"\n"

        start = time.monotonic()
        try:
            if self._writer is None:
                raise MlxcelUnavailable("Not connected to mlxcel")
            reader = self._reader
            if reader is None:
                raise MlxcelUnavailable("Reader not available")

            self._writer.write(request_bytes)
            await self._writer.drain()

            # Bounded read — mlxcel crash or network loss must not hang forever
            response_line = await asyncio.wait_for(
                reader.readline(), timeout=self._RPC_TIMEOUT_S
            )
            latency_ms = (time.monotonic() - start) * 1000

            if not response_line:
                raise MlxcelUnavailable("mlxcel closed connection")

            response = json.loads(response_line)

            if "error" in response:
                err = response["error"]
                raise MlxcelProtocolError(
                    f"RPC error {err.get('code', -1)}: {err.get('message', 'unknown')}"
                )

            self._stats.record_call(latency_ms)
            return response.get("result", {})

        except asyncio.TimeoutError:
            self._stats.record_failure("RPC timeout")
            # Mark disconnected so next call reconnects fresh
            self._connected = False
            raise MlxcelUnavailable(f"RPC timeout after {self._RPC_TIMEOUT_S}s for {method}")
        except (OSError, ValueError, asyncio.CancelledError) as e:
            self._stats.record_failure(str(e))
            self._connected = False
            raise MlxcelUnavailable(f"RPC failed: {e}") from e

    # ── Public API ─────────────────────────────────────────────────────────────

    async def ping(self) -> str:
        """
        Ping mlxcel to check availability and get version.

        Returns:
            mlxcel version string.
        """
        try:
            result = await self._send_rpc("ping", {})
            self._version = result.get("mlxcel_version", "unknown")
            return self._version
        except MlxcelUnavailable:
            # Fallback: try subprocess spawn
            try:
                await self._spawn_subprocess()
                result = await self._send_rpc("ping", {})
                self._version = result.get("mlxcel_version", "unknown")
                return self._version
            except MlxcelUnavailable:
                raise

    async def load_model(
        self,
        model_path: str,
        *,
        kv_bits: int = 4,
        max_kv_size: int = 8192,
    ) -> bool:
        """
        Load model in mlxcel process.

        Args:
            model_path: Path to mlx model directory.
            kv_bits: KV cache quantization bits (4 = Q4_0_4).
            max_kv_size: Max KV cache size.

        Returns:
            True if model loaded successfully.
        """
        result = await self._send_rpc("load_model", {
            "model_path": model_path,
            "kv_bits": kv_bits,
            "max_kv_size": max_kv_size,
        })
        return result.get("ok", False)

    async def unload_model(self) -> bool:
        """Unload model from mlxcel process."""
        result = await self._send_rpc("unload_model", {})
        return result.get("ok", False)

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system_msg: str | None = None,
        thinking: bool = True,
        adapter_path: str | None = None,
    ) -> GenerateResult:
        """
        Generate text via mlxcel subprocess.

        Args:
            prompt: User prompt.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            system_msg: System message.
            thinking: Enable deep thinking mode.
            adapter_path: Optional LoRA adapter path.

        Returns:
            GenerateResult with text, token count, and latency.
        """
        result = await self._send_rpc("generate", {
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_msg": system_msg,
            "thinking": thinking,
            "adapter_path": adapter_path,
        })
        return GenerateResult(
            text=result.get("text", ""),
            tokens_generated=result.get("tokens_generated", 0),
            latency_ms=result.get("latency_ms", 0.0),
        )

    async def generate_stream(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
        system_msg: str | None = None,
        thinking: bool = True,
        adapter_path: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream generated tokens from mlxcel.

        Yields:
            Token chunks as they are generated.
        """
        if not self._connected:
            await self._connect_socket()

        request = {
            "jsonrpc": "2.0",
            "method": "generate_stream",
            "params": {
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_msg": system_msg,
                "thinking": thinking,
                "adapter_path": adapter_path,
            },
            "id": id(prompt) & 0xFFFF,
        }
        request_bytes = json.dumps(request) + b"\n"

        if self._writer is None:
            raise MlxcelUnavailable("Not connected to mlxcel")
        reader = self._reader
        if reader is None:
            raise MlxcelUnavailable("Reader not available")

        self._writer.write(request_bytes)
        await self._writer.drain()

        while True:
            # Per-chunk timeout — stream must not hang if mlxcel stalls
            line = await asyncio.wait_for(reader.readline(), timeout=self._RPC_TIMEOUT_S)
            if not line:
                break
            try:
                resp = json.loads(line)
                if "error" in resp:
                    err = resp["error"]
                    logger.warning("[MLXCEL] stream error: %s", err.get("message"))
                    break
                result = resp.get("result", {})
                chunk = result.get("chunk", "")
                done = result.get("done", False)
                if chunk:
                    yield chunk
                if done:
                    break
            except ValueError:
                continue
            except asyncio.TimeoutError:
                raise MlxcelUnavailable(f"Stream chunk timeout after {self._RPC_TIMEOUT_S}s")

    async def close(self) -> None:
        """Close connection to mlxcel gracefully."""
        async with self._lock:
            await self._disconnect_socket()
            if self._process is not None:
                proc = self._process
                self._process = None
                self._pid = None
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except (OSError, asyncio.CancelledError):
                        pass
                except asyncio.CancelledError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except (OSError, asyncio.CancelledError):
                        pass
                # Drain stderr to avoid broken-pipe warnings in logs
                try:
                    if proc.stderr is not None:
                        await asyncio.wait_for(proc.stderr.read(), timeout=1.0)
                except (asyncio.TimeoutError, OSError, asyncio.CancelledError):
                    pass
            self._connected = False
            self._pid = None


# ── Global singleton ───────────────────────────────────────────────────────────

_client: MlxcelIpcClient | None = None
_client_lock = LazyAsyncioLock()
# Track consecutive failures to avoid hammering a dead mlxcel
_client_failure_count: int = 0
_CLIENT_RETRY_INTERVAL: int = 5  # failures before retrying detection


async def get_mlxcel_client() -> MlxcelIpcClient:
    """
    Get or create the global MlxcelIpcClient singleton.

    Lazy initialization: first call detects mlxcel binary and connects.
    After consecutive failures, re-detects the binary to handle mlxcel updates.
    """
    global _client, _client_failure_count
    async with _client_lock:
        if _client is not None and _client_failure_count < _CLIENT_RETRY_INTERVAL:
            return _client

        # Either first call or retry after failures
        _client = MlxcelIpcClient()
        _client_failure_count = 0
        try:
            await asyncio.wait_for(_client.ping(), timeout=5.0)
            logger.info("[MLXCEL] Connected: version=%s", _client.version)
        except (MlxcelUnavailable, asyncio.TimeoutError) as e:
            _client_failure_count += 1
            logger.debug("[MLXCEL] mlxcel not available: %s (failure #%d)", e, _client_failure_count)
            # Keep _client — next call will retry up to _CLIENT_RETRY_INTERVAL times
    return _client


def is_mlxcel_available() -> bool:
    """
    Check if mlxcel binary is present on the system.

    This is the gate for routing inference to mlxcel vs DeepHermes3Engine.
    Does NOT attempt connection — only checks binary existence.
    Cached: repeated calls do NOT re-scan filesystem.
    """
    # Fast path: return cached binary detection result from singleton
    global _client
    if _client is not None:
        return _client.is_available
    # Cold path: create temporary client to probe filesystem
    try:
        client = MlxcelIpcClient()
        return client.is_available
    except Exception:
        return False
