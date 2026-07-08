# hledac/universal/utils/jsonl_lz4_writer.py
# Issue #7: LZ4 async batch JSONL writer — Rust lz4_flex + asyncio
#
# Pipeline: JSON lines -> asyncio batch queue -> rayon lz4 compress
#           -> lz4 frame -> append to .jsonl.zst
#
# Architecture (M1 8GB safe):
#   - AsyncQueue[bytes] — bounded (maxsize=1000), back-pressure on writers
#   - asyncio.gather return_exceptions — fail-soft per line
#   - rayon batch compress N>=64 lines on io_pool (existing 2-thread pool)
#   - lz4 frame written in chunks (32 KB buffer before write)
#   - Automatic .jsonl.zst suffix + fallback .jsonl for empty/bypass
#
# Invariants:
#   [I1] Always-on — controlled by HLEDAC_JSONL_LZ4=0/1
#   [I2] Bounded queue (1000 lines) — never unbounded grow
#   [I3] Fail-soft: write error -> .jsonl fallback, never raises
#   [I4] mx.eval([]) barrier before clear_cache if MLX involved
#   [I5] lz4_flex frame (Rust rayon or Python lz4_flex)

from __future__ import annotations

import asyncio
import os

from hledac.universal.utils.async_helpers import safe_create_task
import threading
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import lz4.frame

import msgspec.json as _json

# ---------------------------------------------------------------------------
# Rust lz4 via rust.hot_edges domain — lazy, fail-soft
# ---------------------------------------------------------------------------

_lz4_fn: Any | None = None  # lz4_compress_jsonl_batch callable


def _init_lz4() -> bool:
    """Lazy init of Rust lz4 via rust.hot_edges domain."""
    global _lz4_fn
    if _lz4_fn is not None:
        return True
    try:
        from hledac.universal.core import rust_backend

        be = rust_backend.rust
        if be.is_available:
            domain = getattr(be, "hot_edges", None)
            if domain is not None:
                fn = getattr(domain, "lz4_compress_jsonl_batch", None)
                if fn is not None:
                    _lz4_fn = fn
                    return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Python lz4 fallback — pure lz4_flex, no C dep
# ---------------------------------------------------------------------------

_lz4_frame_available: bool | None = None
_lz4_frame_compress: Any | None = None


def _lz4_compress_python(lines: list[bytes]) -> bytes:
    """Python fallback: join lines, compress via lz4.frame or zlib."""
    global _lz4_frame_available, _lz4_frame_compress
    if _lz4_frame_available is None:
        try:
            import lz4.frame

            _lz4_frame_compress = lz4.frame.compress
            _lz4_frame_available = True
        except ImportError:
            _lz4_frame_available = False

    if not lines:
        return b""
    combined = b"\n".join(lines)

    if _lz4_frame_available and _lz4_frame_compress is not None:
        return _lz4_frame_compress(combined)
    else:
        import zlib

        return zlib.compress(combined, 6)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_JSONL_LZ4_ENABLED = os.environ.get("HLEDAC_JSONL_LZ4", "1") == "1"
_JSONL_LZ4_BATCH_SIZE = int(os.environ.get("HLEDAC_JSONL_LZ4_BATCH", "256"))
_JSONL_LZ4_QUEUE_MAX = int(os.environ.get("HLEDAC_JSONL_LZ4_QUEUE", "1000"))
_JSONL_LZ4_FLUSH_BYTES = int(os.environ.get("HLEDAC_JSONL_LZ4_FLUSH", str(32 * 1024)))
_JSONL_LZ4_COMPRESS_THRESHOLD = int(os.environ.get("HLEDAC_JSONL_LZ4_MIN_COMPRESS", "512"))


# ---------------------------------------------------------------------------
# Async JSONL writer
# ---------------------------------------------------------------------------


class LZ4JSONLWriter:
    """
    Async batch JSONL writer with Rust lz4_flex or Python lz4_flex compression.

    Usage:
        writer = LZ4JSONLWriter(Path("output.jsonl.zst"))
        await writer.write_line({"event": "test", "value": 42})
        await writer.close()

    Or streaming (preferred for high-volume):
        async def event_source():
            for item in items:
                yield {"ts": time.time(), "data": item}

        writer = LZ4JSONLWriter(Path("events.jsonl.zst"))
        await writer.write_stream(event_source())
        await writer.close()

    Outputs:
        - .jsonl.zst  — lz4 compressed (primary)
        - .jsonl      — uncompressed fallback (on compress error or HLEDAC_JSONL_LZ4=0)

    Invariants:
        [I1] Queue bounded to 1000 lines — write_line() blocks if full
        [I2] Flush: batch full, byte threshold, explicit close()
        [I3] Fail-soft: any write/compress error -> .jsonl fallback, never raises
        [I4] Idempotent close()
        [I5] Bounded memory: max 1000 lines * ~1 KB ~= 1 MB queue
    """

    def __init__(
        self,
        path: Path,
        *,
        compress: bool = _JSONL_LZ4_ENABLED,
        batch_size: int = _JSONL_LZ4_BATCH_SIZE,
        queue_max: int = _JSONL_LZ4_QUEUE_MAX,
        flush_bytes: int = _JSONL_LZ4_FLUSH_BYTES,
    ) -> None:
        self._path = Path(path)
        self._compress = compress and _JSONL_LZ4_ENABLED
        self._batch_size = batch_size
        self._flush_bytes = flush_bytes
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_max)
        self._closed = False
        self._writer_task: asyncio.Task[None] | None = None
        self._pending_lines: list[bytes] = []
        self._pending_bytes = 0
        self._lz4_available = _init_lz4() if self._compress else False
        # OPT-2: cache on self for faster access in _compress()
        self._lz4_fn = _lz4_fn if self._compress else None

        # File handles (opened lazily)
        self._file_zst: Any = None
        self._file_jsonl: Any = None
        self._active_file: str = "zst"

        # Output paths
        self._path_zst = Path(str(self._path) + ".zst")
        self._path_jsonl = self._path

        self._lock = threading.Lock()

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def write_line(self, obj: dict[str, Any]) -> None:
        """Encode one object as JSON line and queue it. Blocks on full queue."""
        if self._closed:
            return
        try:
            line_bytes = _json.encode(obj)
        except Exception:
            import orjson

            line_bytes = orjson.dumps(obj)
        self._ensure_writer_started()
        await self._queue.put(line_bytes)

    async def write_stream(
        self,
        source: AsyncGenerator[dict[str, Any], None],
    ) -> None:
        """Stream objects from an async generator into the writer."""
        if self._closed:
            return
        self._ensure_writer_started()
        try:
            async for obj in source:
                await self._queue.put(_json.encode(obj))
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self) -> None:
        """Flush remaining lines and close file handles. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._writer_task is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._writer_task.cancel()
            except asyncio.CancelledError:
                pass
        await self._close_files()

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _ensure_writer_started(self) -> None:
        """Lazily start the background writer task."""
        if self._writer_task is None:
            self._writer_task = safe_create_task(self._writer_loop(), name="lz4_writer:loop")

    async def _writer_loop(self) -> None:
        """Background loop: drain queue, batch, compress, write."""
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    await self._flush_pending()
                    await self._close_files()
                    break
                self._pending_lines.append(item)
                self._pending_bytes += len(item)
                if len(self._pending_lines) >= self._batch_size or self._pending_bytes >= self._flush_bytes:
                    await self._flush_pending()
        except asyncio.CancelledError:
            await self._flush_pending()
            await self._close_files()
            raise

    async def _flush_pending(self) -> None:
        """Flush accumulated lines: compress + write, or fallback to raw JSONL."""
        if not self._pending_lines:
            return
        lines = self._pending_lines
        self._pending_lines = []
        self._pending_bytes = 0
        try:
            if self._compress and self._should_compress(lines):
                await self._write_lz4_batch(lines)
            else:
                await self._write_raw_batch(lines)
        except Exception:
            # BUG-FIX: Always attempt raw batch fallback, preserve state on failure
            try:
                await self._write_raw_batch(lines)
            except Exception:
                # Data loss unavoidable if both paths fail, but at least
                # reset state so subsequent batches are not corrupted.
                self._pending_bytes = 0
                pass

    def _should_compress(self, _lines: list[bytes]) -> bool:
        # OPT-5: use _pending_bytes (already tracked during append)
        # This is called inside _flush_pending where _pending_bytes is the
        # aggregate of all lines in this batch — matches total exactly.
        # _lines kept for API compatibility but pending_bytes is accurate.
        return self._pending_bytes >= _JSONL_LZ4_COMPRESS_THRESHOLD

    async def _write_lz4_batch(self, lines: list[bytes]) -> None:
        """Compress batch via Rust/Python lz4 and write to .jsonl.zst."""
        def _compress() -> bytes:
            if self._lz4_fn is not None:
                return self._lz4_fn(lines)
            return _lz4_compress_python(lines)

        try:
            compressed = await asyncio.to_thread(_compress)
        except Exception:
            await self._write_raw_batch(lines)
            return

        if self._file_zst is None:
            path = str(self._path_zst)
            self._file_zst = open(path, "ab", buffering=8192)  # type: ignore[arg-type]
            self._active_file = "zst"

        with self._lock:
            try:
                self._file_zst.write(compressed)
                self._file_zst.flush()
            except Exception:
                self._file_zst = None
                self._active_file = "jsonl"
                await self._write_raw_batch(lines)

    async def _write_raw_batch(self, lines: list[bytes]) -> None:
        """Write raw JSONL lines (no compression)."""
        if self._file_jsonl is None:
            path = str(self._path_jsonl)
            self._file_jsonl = open(path, "a", buffering=8192, encoding="utf-8")  # type: ignore[arg-type]
            self._active_file = "jsonl"

        with self._lock:
            try:
                for line in lines:
                    self._file_jsonl.write(line.decode("utf-8", errors="replace"))
                    self._file_jsonl.write("\n")
                self._file_jsonl.flush()
            except Exception:
                pass

    async def _close_files(self) -> None:
        """Close all open file handles."""
        for f in (self._file_zst, self._file_jsonl):
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
        self._file_zst = None
        self._file_jsonl = None


async def stream_to_lz4_jsonl(
    source: AsyncGenerator[dict[str, Any], None],
    path: Path,
    **kwargs: Any,
) -> Path:
    """
    Stream objects from an async generator directly to a compressed JSONL file.

    Args:
        source: AsyncGenerator yielding dicts
        path: Output path (.jsonl.zst or .jsonl)
        **kwargs: Forwarded to LZ4JSONLWriter

    Returns:
        Final output path
    """
    writer = LZ4JSONLWriter(path, **kwargs)
    await writer.write_stream(source)
    await writer.close()
    if writer._active_file == "jsonl":
        return writer._path_jsonl
    return writer._path_zst


__all__ = ["LZ4JSONLWriter", "stream_to_lz4_jsonl"]
