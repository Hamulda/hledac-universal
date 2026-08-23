import asyncio
import logging
import os
import threading

from hledac.universal._core.env_config import ENV
from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for

logger = logging.getLogger(__name__)
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass
import msgspec.json as _json

_lz4_fn: Any | None = None


def _init_lz4() -> bool:
    """Lazy init of Rust lz4 via rust.hot_edges domain."""
    global _lz4_fn
    if _lz4_fn is not None:
        return True
    try:
        from hledac.universal._core import rust_backend

        be = rust_backend.rust
        if be.is_available:
            domain = getattr(be, "hot_edges", None)
            if domain is not None:
                fn = getattr(domain, "lz4_compress_jsonl_batch", None)
                if fn is not None:
                    _lz4_fn = fn
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


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


_JSONL_LZ4_ENABLED = ENV.get_bool("HLEDAC_JSONL_LZ4", default=True)
_JSONL_LZ4_BATCH_SIZE = ENV.get_int("HLEDAC_JSONL_LZ4_BATCH", default=256)
_JSONL_LZ4_QUEUE_MAX = ENV.get_int("HLEDAC_JSONL_LZ4_QUEUE", default=1000)
_JSONL_LZ4_FLUSH_BYTES = ENV.get_int("HLEDAC_JSONL_LZ4_FLUSH", default=32 * 1024)
_JSONL_LZ4_COMPRESS_THRESHOLD = ENV.get_int("HLEDAC_JSONL_LZ4_MIN_COMPRESS", default=512)

_JSONL_LZ4_WRITE_TIMEOUT_S = ENV.get_float("HLEDAC_JSONL_LZ4_WRITE_TIMEOUT", default=5.0)


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
        [I1] Queue bounded to 1000 lines — write_line() blocks with 5s timeout if full; returns bool
        [I2] Flush: batch full, byte threshold, explicit close()
        [I3] Fail-soft: any write/compress error -> .jsonl fallback, never raises
        [I4] Idempotent close()
        [I5] Bounded memory: max 1000 lines * ~1 KB ~= 1 MB queue
        [I6] S1-09: write_line() returns False on timeout (caller decides backpressure response); write_stream() returns count of queued lines
    """

    __slots__ = (
        "_active_file",
        "_batch_size",
        "_closed",
        "_compress",
        "_file_jsonl",
        "_file_zst",
        "_flush_bytes",
        "_lock",
        "_lz4_available",
        "_lz4_fn",
        "_path",
        "_path_jsonl",
        "_path_zst",
        "_pending_bytes",
        "_pending_lines",
        "_queue",
        "_writer_task",
        "_write_timeout",
    )

    def __init__(
        self,
        path: Path,
        *,
        compress: bool = _JSONL_LZ4_ENABLED,
        batch_size: int = _JSONL_LZ4_BATCH_SIZE,
        queue_max: int = _JSONL_LZ4_QUEUE_MAX,
        flush_bytes: int = _JSONL_LZ4_FLUSH_BYTES,
        write_timeout: float = _JSONL_LZ4_WRITE_TIMEOUT_S,
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
        self._lz4_fn = _lz4_fn if self._compress else None
        self._file_zst: Any = None
        self._file_jsonl: Any = None
        self._active_file: str = "zst"
        self._path_zst = Path(str(self._path) + ".zst")
        self._path_jsonl = self._path
        self._lock = threading.Lock()
        self._write_timeout = write_timeout

    async def write_line(self, obj: dict[str, Any]) -> bool:
        """Encode one object as JSON line and queue it. Blocks on full queue with timeout.

        Returns:
            True if line was queued, False if queue was full (caller can retry/buffer).
        """
        if self._closed:
            return False
        try:
            line_bytes = _json.encode(obj)
        except Exception:
            import orjson

            line_bytes = orjson.dumps(obj)
        self._ensure_writer_started()
        try:
            await safe_wait_for(self._queue.put(line_bytes), timeout=self._write_timeout)
            return True
        except TimeoutError:
            # S1-09 FIX: return False instead of silently dropping. Caller decides
            # whether to retry, buffer locally, or propagate backpressure upstream.
            logger.warning("[LZ4] write_line timed out after %.1fs, queue full", self._write_timeout)
            return False

    async def write_stream(self, source: AsyncGenerator[dict[str, Any]]) -> int:
        """Stream objects from an async generator into the writer.

        Returns:
            Number of lines successfully queued.
        """
        if self._closed:
            return 0
        self._ensure_writer_started()
        written = 0
        try:
            async for obj in source:
                try:
                    await safe_wait_for(self._queue.put(_json.encode(obj)), timeout=self._write_timeout)
                    written += 1
                except TimeoutError:
                    # S1-09 FIX: count skipped lines instead of silently continuing.
                    # Caller can inspect return value to detect backpressure.
                    logger.warning("[LZ4] write_stream timed out after %.1fs, skipping line", self._write_timeout)
                    continue
        except asyncio.CancelledError:
            await self.close()
            raise
        return written

    async def close(self) -> None:
        """Flush remaining lines and close file handles. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._writer_task is not None:
            await self._queue.put(None)
            try:
                await safe_wait_for(self._writer_task, timeout=5.0, label="_writer_task")
            except TimeoutError:
                self._writer_task.cancel()
            except asyncio.CancelledError:  # noqa: BLE001
                pass
        await self._close_files()

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
            try:
                await self._write_raw_batch(lines)
            except Exception:
                self._pending_bytes = 0

    def _should_compress(self, _lines: list[bytes]) -> bool:
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
            self._file_zst = open(path, "ab", buffering=8192)
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
            self._file_jsonl = open(path, "a", buffering=8192, encoding="utf-8")
            self._active_file = "jsonl"
        with self._lock:
            try:
                for line in lines:
                    self._file_jsonl.write(line.decode("utf-8", errors="replace"))
                    self._file_jsonl.write("\n")
                self._file_jsonl.flush()
            except Exception:  # noqa: BLE001
                pass

    async def _close_files(self) -> None:
        """Close all open file handles."""
        for f in (self._file_zst, self._file_jsonl):
            if f is not None:
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass
        self._file_zst = None
        self._file_jsonl = None


async def stream_to_lz4_jsonl(source: AsyncGenerator[dict[str, Any]], path: Path, **kwargs: Any) -> Path:
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
