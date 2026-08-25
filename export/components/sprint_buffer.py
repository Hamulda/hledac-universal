"""
PHYSICS-08: SprintExportBuffer — Continuous Background Serialization
===================================================================

Maintains a running in-memory buffer that accumulates findings as they're
accepted during the sprint. At TEARDOWN, the pre-serialized data eliminates
the triple-serialization bottleneck in the export phase.

Architecture:
  1. During sprint: append() accumulates individual findings as bytes
  2. Background flusher: periodically compresses accumulated bytes via
     compression.zstd streaming compressor and writes to a temp JSONL file
  3. At TEARDOWN: finalize() reads the JSONL file, wraps in JSON envelope,
     and returns the pre-serialized bytes — O(1) serialization cost

M1 8GB bounds:
  - _MAX_RAM_BYTES = 64 MiB (in-memory accumulation ceiling)
  - Background flush every 5s or when buffer reaches 32 MiB
  - zstd level 3 (fast, ~3-5x compression on JSON)
  - Temp file in ~/.hledac/buffers/ — cleaned on finalize()

Thread safety: append() and flush() are async-safe under asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

# Lazy imports for compression.zstd (Python 3.14 stdlib)
_zstd: object = None

def _get_zstd() -> object:
    global _zstd
    if _zstd is None:
        import compression.zstd as _mod
        _zstd = _mod
    return _zstd

class SprintExportBuffer:
    """PHYSICS-08: Accumulates serialized findings during sprint for zero-cost export.

    Replace the triple serialization in export/sprint_exporter.py with a single
    shared pre-serialized byte buffer. Findings are appended as they're accepted
    (during the sprint), not at teardown.

    Usage:
        buf = SprintExportBuffer(sprint_id="abc123")
        await buf.start()

        # During sprint: append each finding as it's accepted
        for finding in accepted_findings:
            await buf.append(orjson.dumps(finding))

        # At TEARDOWN: get pre-serialized JSON bytes
        json_bytes = await buf.finalize()
        # Write json_bytes to report file — no more orjson serialization

        await buf.close()
    """

    __slots__ = (
        '_sprint_id', '_buffer', '_lock', '_flush_task', '_shutdown',
        '_temp_dir', '_temp_file', '_compressor', '_total_appended',
        '_total_flushed', '_finalized', '_started', '_closed',
        '_MAX_RAM_BYTES', '_FLUSH_INTERVAL_S', '_FLUSH_THRESHOLD_BYTES',
        '_COMPRESS_LEVEL',
    )

    _MAX_RAM_BYTES: int = 64 * 1024 * 1024  # 64 MiB in-memory ceiling
    _FLUSH_INTERVAL_S: float = 5.0  # Background flush every 5s
    _FLUSH_THRESHOLD_BYTES: int = 32 * 1024 * 1024  # Flush at 32 MiB
    _COMPRESS_LEVEL: int = 3  # Fast zstd level

    def __init__(self, sprint_id: str, *, temp_dir: str | Path | None = None):
        self._sprint_id = sprint_id
        self._buffer: bytearray = bytearray()
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()
        self._compressor: object | None = None  # ZstdCompressor

        # Temp file for accumulating compressed data
        if temp_dir is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            self._temp_dir = Path(EVIDENCE_ROOT) / '.export_buffers'
        else:
            self._temp_dir = Path(temp_dir)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._temp_file = self._temp_dir / f'{sprint_id}_export_buffer.jsonl.zst'

        # Telemetry
        self._total_appended: int = 0
        self._total_flushed: int = 0
        self._finalized = False
        self._started = False
        self._closed = False

    @property
    def total_appended(self) -> int:
        """Total findings appended since start."""
        return self._total_appended

    @property
    def total_flushed(self) -> int:
        """Total findings flushed to disk since start."""
        return self._total_flushed

    @property
    def ram_buffer_bytes(self) -> int:
        """Current in-memory buffer size in bytes."""
        return len(self._buffer)

    async def start(self) -> None:
        """Start background flush worker.

        Safe to call multiple times — idempotent.
        """
        if self._started:
            return
        self._started = True
        self._compressor = _get_zstd().ZstdCompressor(level=self._COMPRESS_LEVEL)

        # Clear any stale temp file from previous run
        try:
            if self._temp_file.exists():
                self._temp_file.unlink()
        except OSError:  # noqa: BLE001
            pass

        self._flush_task = safe_create_task(
            self._flush_loop(), name=f'sprint_buffer_flush_{self._sprint_id}'
    )

    async def append(self, data: bytes) -> None:
        """Append serialized finding bytes to the buffer.

        Each call adds one JSONL line (finding as JSON line + newline).
        This is the hot path — called for every accepted finding during the sprint.

        Args:
            data: Pre-serialized finding as UTF-8 bytes (without trailing newline).
                  Caller should NOT include newline — we add it here.

        M1 8GB guard: if buffer exceeds _MAX_RAM_BYTES, flushes synchronously
        before appending (backpressure).
        """
        if self._closed:
            return

        async with self._lock:
            self._buffer.extend(data)
            self._buffer.extend(b'\n')
            self._total_appended += 1

            # Backpressure: flush if over RAM limit
            if len(self._buffer) >= self._MAX_RAM_BYTES:
                await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Flush current buffer to temp file (MUST be called under _lock)."""
        if not self._buffer:
            return

        data = bytes(self._buffer)
        self._buffer.clear()

        try:
            if self._compressor is not None:
                compressed = self._compressor.compress(data)
                if compressed:
                    with open(self._temp_file, 'ab') as f:
                        f.write(compressed)
        except Exception:
            # Fail-soft: data stays in buffer, will retry on next flush
            self._buffer = bytearray(data)
            return

        self._total_flushed += self._total_appended - self._total_flushed + 1

    async def _flush_loop(self) -> None:
        """Background worker: periodically flush buffer to temp file."""
        last_flush = time.monotonic()

        while not self._shutdown.is_set():
            try:
                async with asyncio.timeout(self._FLUSH_INTERVAL_S):
                    await self._shutdown.wait()
                    break
            except TimeoutError:  # noqa: BLE001
                pass

            now = time.monotonic()
            elapsed = now - last_flush

            async with self._lock:
                if len(self._buffer) >= self._FLUSH_THRESHOLD_BYTES or (
                    self._buffer and elapsed >= self._FLUSH_INTERVAL_S
                ):
                    await self._flush_locked()
                    last_flush = now

        # Final drain on shutdown
        async with self._lock:
            if self._buffer:
                await self._flush_locked()

    async def finalize(self) -> bytes | None:
        """Flush remaining buffer + finalize zstd frame, return complete compressed bytes.

        Called at TEARDOWN. After this, the buffer is ready to be written to the
        final JSON report. Returns the complete zstd-compressed JSONL data that
        can be decompressed and wrapped in the JSON envelope.

        Returns:
            Complete compressed bytes from temp file, or None if no data was appended.
        """
        if self._finalized:
            # Re-read from temp file
            try:
                if self._temp_file.exists():
                    return self._temp_file.read_bytes()
            except OSError:  # noqa: BLE001
                pass
            return None

        self._finalized = True
        self._shutdown.set()

        # Wait for background flush to complete
        if self._flush_task is not None:
            try:
                self._flush_task.cancel()
                await asyncio.wait_for(
                    asyncio.shield(self._flush_task), timeout=3.0
    )
            except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001
                pass
            self._flush_task = None

        # Final flush + zstd frame flush
        async with self._lock:
            if self._buffer or self._compressor is not None:
                try:
                    if self._compressor is not None:
                        if self._buffer:
                            compressed = self._compressor.compress(bytes(self._buffer))
                            if compressed:
                                with open(self._temp_file, 'ab') as f:
                                    f.write(compressed)
                            self._buffer.clear()

                        # Flush zstd frame (finalizes the stream)
                        flush_data = self._compressor.flush()
                        if flush_data:
                            with open(self._temp_file, 'ab') as f:
                                f.write(flush_data)
                except Exception:  # noqa: BLE001
                    pass

        # Read complete compressed data
        try:
            if self._temp_file.exists():
                return self._temp_file.read_bytes()
        except OSError:  # noqa: BLE001
            pass
        return None

    async def get_uncompressed_lines(self) -> list[bytes]:
        """Decompress the accumulated data and return individual JSONL lines.

        Used at TEARDOWN to get the individual findings for wrapping in the
        final JSON envelope. This is a convenience method that reads from the
        temp file and decompresses.

        Returns:
            List of JSON line bytes (one per finding), empty if no data.
        """
        compressed = await self.finalize()
        if not compressed:
            return []

        try:
            dctx = _get_zstd().ZstdDecompressor()
            decompressed = dctx.decompress(compressed)
            return [line for line in decompressed.split(b'\n') if line]
        except Exception:
            return []

    async def close(self) -> None:
        """Clean up resources. Idempotent."""
        if self._closed:
            return

        self._closed = True
        self._shutdown.set()

        if self._flush_task is not None:
            try:
                self._flush_task.cancel()
                await asyncio.wait_for(
                    asyncio.shield(self._flush_task), timeout=2.0
    )
            except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001
                pass
            self._flush_task = None

        try:
            if self._temp_file.exists():
                self._temp_file.unlink()
        except OSError:  # noqa: BLE001
            pass

        self._compressor = None
        self._buffer.clear()
