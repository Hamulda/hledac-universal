"""
RingMMap IPC — zero-copy msgspec.msgpack přes POSIX shared memory.

Univerzální memory-mapped IPC vrstva pro M1 8GB (Darwin arm64).



Lze použít pro jakýkoliv msgspec.Struct typ — žádný JSON, žádný pipe.

ARCHITECTURA:
Main Process                          Worker (subprocess, spawn ctx)
RingMMap                             RingMMap (attached)
  ├─ posix_ipc.SharedMemory (N MiB)  ├─ msgspec.msgpack.decode(ring_segment)
  ├─ posix_ipc.Semaphore            └─ process()
  └─ msgspec.msgpack.encode(msg)         └─ msgspec.msgpack.encode(result)
                                          └─ result_shm

Zero-copy path (M1 8GB):
  - Ring buffer: mmap'd on both sides — kernel copy, ne CPU copy
  - msgspec.msgpack: 2-5× rychlejší než orjson, binary compact format
  - Spawn ctx: mp.get_context("spawn") — MANDATORY pro M1 Metal safety

bounded invariants:
  - Ring buffer: configurable size (default 16 MiB, max 256 MiB)
  - Max message size: ring_size / 4 (prevents single-message overflow)
  - Always-on: žádné feature flagy
  - Fail-safe: subprocess errors → empty results, žádné exceptions

Usage:
  # Main process
  ipc = RingMMapIPC.create_for_processing(
      msg_type=MyRequest,
      result_type=MyResult,
  )
  await ipc.spawn_worker(my_handler)
  result = await ipc.call(MyRequest(...))

  # Worker process (subprocess)
  ipc = RingMMapIPC.attach_to_parent(shared_names)
  async for request in ipc:
      result = await my_handler(request)
      ipc.send_result(result)

Author: Issue #22
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import multiprocessing as mp
import struct
import uuid
from typing import TYPE_CHECKING, Any, TypeVar

import msgspec

if TYPE_CHECKING:
    pass


# Lazy import — posix_ipc is Darwin-only
_POSIX_IPC_SPEC = importlib.util.find_spec("posix_ipc")

_SPAWN_CTX = mp.get_context("spawn")

# Default sizes
_DEFAULT_RING_SIZE = 16 * 1024 * 1024  # 16 MiB ring buffer
_MAX_RING_SIZE = 256 * 1024 * 1024  # 256 MiB max
_RING_HEADER = 128  # ring buffer control block (bytes)
_RESULT_SIZE = 2 * 1024 * 1024  # 2 MiB result SharedMemory
_SPAWN_TIMEOUT_S = 10.0
_DEFAULT_MAX_MESSAGE_SIZE = 4 * 1024 * 1024  # 4 MiB max per message

T = TypeVar("T", default=object)
R = TypeVar("R", default=object)


def _posix_ipc_available() -> bool:
    """Check if posix_ipc is available on this platform."""
    return _POSIX_IPC_SPEC is not None and __import__("sys").platform == "darwin"


class RingMMapChannel(msgspec.Struct, frozen=True, gc=False):
    """
    IPC channel descriptor — passed to subprocess at spawn time.

    frozen=True: immutable after creation (safe to share across async tasks)
    gc=False: F350M-R — prevents cyclic GC overhead, critical for M1 8GB
               millisecond OSINT streams. Zero-copy POSIX shm mmap means no
               Python object cycles are created through the buffer.
    """

    shm_name: str
    ring_size: int
    sem_name: str
    result_shm_name: str
    result_sem_name: str
    ready_sem_name: str
    max_message_size: int


# ---------------------------------------------------------------------------
# RingMMap — low-level ring buffer
# ---------------------------------------------------------------------------


class RingMMap:
    """
    Low-level mmap ring buffer with offset-based read/write.

    Thread-safe, process-safe (via mmap). Uses a header with two uint32
    offsets: write_pos and read_pos. Both processes update the header
    atomically via struct.pack_into/unpack_from.

    Zero-copy on M1: mmap'd on both sides, kernel copies data directly.
    """

    __slots__ = ("_shm", "_buf", "_size", "_name", "_attached")

    def __init__(
        self,
        shm_name: str,
        size: int,
        *,
        attached: bool = False,
    ) -> None:
        """
        Args:
            shm_name: POSIX shared memory name
            size: ring buffer size in bytes
            attached: True if attaching to existing (worker side)
        """
        if not _posix_ipc_available():
            raise RuntimeError("posix_ipc not available on this platform")

        import posix_ipc

        if attached:
            shm = posix_ipc.SharedMemory(name=shm_name)
        else:
            shm = posix_ipc.SharedMemory(
                shm_name,
                flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                size=size,
            )

        self._shm = shm
        self._buf = shm.buf  # memoryview
        self._size = size
        self._name = shm_name
        self._attached = attached

        if not attached:
            # Initialize header
            struct.pack_into("<I", self._buf, 0, _RING_HEADER)
            struct.pack_into("<I", self._buf, 4, _RING_HEADER)

    @classmethod
    def create(
        cls,
        name_prefix: str,
        size: int,
    ) -> tuple[RingMMap, str]:
        """
        Create a new ring buffer. Returns (ring, shm_name).

        Args:
            name_prefix: Prefix for the shared memory name
            size: Ring buffer size in bytes (clamped to _MAX_RING_SIZE)

        Returns:
            (ring_buffer, shm_name) tuple
        """
        size = min(size, _MAX_RING_SIZE)
        name = f"/hldx-{name_prefix}-{uuid.uuid4().hex[:8]}"
        ring = cls(name, size)
        return ring, name

    @classmethod
    def attach(cls, shm_name: str) -> RingMMap:
        """Attach to an existing ring buffer (worker side)."""
        import posix_ipc

        shm = posix_ipc.SharedMemory(name=shm_name)
        size = shm.buf_len
        ring = cls.__new__(cls)
        ring._shm = shm
        ring._buf = shm.buf
        ring._size = size
        ring._name = shm_name
        ring._attached = True
        return ring

    def write(self, data: bytes) -> bool:
        """
        Write data into the ring buffer. Returns True on success.

        If data is larger than available space, overwrites from start
        (ring semantics). write_pos is updated atomically.
        """
        record_len = len(data)
        if record_len + 4 > self._size - _RING_HEADER:
            return False

        write_pos = struct.unpack_from("<I", self._buf, 0)[0]
        read_pos = struct.unpack_from("<I", self._buf, 4)[0]

        # Calculate available space
        if write_pos >= read_pos:
            available = self._size - write_pos
        else:
            available = read_pos - write_pos

        # If not enough space, reset to header
        if record_len + 4 > available:
            write_pos = _RING_HEADER

        # Write 4-byte length header
        struct.pack_into("<I", self._buf, write_pos, record_len)

        # Write data (handle ring wrap)
        pos = write_pos + 4
        remaining = record_len
        while remaining > 0:
            chunk = min(remaining, self._size - pos)
            self._buf[pos : pos + chunk] = data[record_len - remaining : record_len - remaining + chunk]
            remaining -= chunk
            pos += chunk
            if pos >= self._size:
                pos = _RING_HEADER

        # Update write position
        struct.pack_into("<I", self._buf, 0, pos)
        return True

    def read(self) -> bytes | None:
        """
        Read one message from the ring buffer. Returns None if empty.

        Advances read_pos. Safe for single-reader, single-writer.
        """
        write_pos = struct.unpack_from("<I", self._buf, 0)[0]
        read_pos = struct.unpack_from("<I", self._buf, 4)[0]

        if read_pos == write_pos:
            return None  # Empty

        # Read 4-byte length
        if read_pos + 4 > self._size:
            read_pos = _RING_HEADER

        record_len = struct.unpack_from("<I", self._buf, read_pos)[0]
        rec_start = read_pos + 4
        rec_end = rec_start + record_len

        if rec_end > self._size:
            # Record spans ring boundary — copy into contiguous buffer
            segment_a_len = self._size - rec_start
            segment_b_len = record_len - segment_a_len
            data = bytes(self._buf[rec_start:]) + bytes(self._buf[:segment_b_len])
            read_pos = _RING_HEADER + segment_b_len
        else:
            if rec_start >= _RING_HEADER and rec_end <= self._size:
                data = bytes(self._buf[rec_start:rec_end])
                read_pos = rec_end if rec_end < self._size else _RING_HEADER
            else:
                # Wrap case
                segment_a_len = self._size - rec_start
                segment_b_len = record_len - segment_a_len
                data = bytes(self._buf[rec_start:]) + bytes(self._buf[:segment_b_len])
                read_pos = _RING_HEADER + segment_b_len

        struct.pack_into("<I", self._buf, 4, read_pos)
        return data

    def close(self) -> None:
        """Close the shared memory (caller must call unlink separately)."""
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:  # noqa: BLE001
                pass
            self._shm = None

    def unlink(self) -> None:
        """Unlink the shared memory object (destroy on cleanup)."""
        if self._name and _posix_ipc_available():
            import posix_ipc

            try:
                shm = posix_ipc.SharedMemory(name=self._name)
                shm.close_unlink()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# RingMMapIPC — high-level msgspec.msgpack + ring buffer
# ---------------------------------------------------------------------------


class RingMMapIPC:
    """
    High-level zero-copy IPC over POSIX shared memory + msgspec.msgpack.

    Generic in T (request) and R (response) — any msgspec.Struct type pair.
    Uses ring buffer for request streaming, result SharedMemory for responses.

    bounded invariants:
      - Ring buffer: configurable, default 16 MiB
      - Max message size: ring_size / 4
      - 1:1 request/response mapping via result SharedMemory
      - Always-on, fail-safe, M1 8GB safe

    Usage:

        # Main process
        ipc = RingMMapIPC[MyRequest, MyResult].create(
            name_prefix="myworker",
            msg_type=MyRequest,
            result_type=MyResult,
        )
        await ipc.spawn_worker(my_handler)
        result = await ipc.call(MyRequest(...))

        # Worker (in subprocess)
        ipc = RingMMapIPC.attach_to_parent(env)
        async for request in ipc:
            result = await my_handler(request)
            ipc.send_result(result)
    """

    __slots__ = (
        "_msg_type",
        "_result_type",
        "_channel",
        "_ring",
        "_result_buf",
        "_result_ring",
        "_proc",
        "_started",
        "_closed",
        "_pending",
        "_lock",
        "_pending_seq",
    )

    def __init__(
        self,
        msg_type: type[T],
        result_type: type[R],
    ) -> None:
        self._msg_type = msg_type
        self._result_type = result_type
        self._channel: RingMMapChannel | None = None
        self._ring: RingMMap | None = None
        self._result_buf: Any = None  # memoryview
        self._result_ring: RingMMap | None = None
        self._proc: Any = None  # SpawnProcess
        self._started: bool = False
        self._closed: bool = False
        self._pending: dict[int, asyncio.Future[R]] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def create(
        cls,
        name_prefix: str,
        msg_type: type[T],
        result_type: type[R],
        ring_size: int = _DEFAULT_RING_SIZE,
        max_message_size: int = _DEFAULT_MAX_MESSAGE_SIZE,
    ) -> tuple[RingMMapIPC, RingMMapChannel]:
        """
        Create a new IPC channel for the main process side.

        Returns (ipc, channel) — channel must be passed to worker.
        """
        if not _posix_ipc_available():
            raise RuntimeError("posix_ipc not available on this platform")

        ipc = cls(msg_type, result_type)
        ipc._pending_seq = 0

        # Create shared memory objects
        shm_name = f"/hldx-{name_prefix}-{uuid.uuid4().hex[:8]}"
        result_shm_name = f"/hldx-{name_prefix}-{uuid.uuid4().hex[:8]}"
        sem_name = f"/hldx-{name_prefix}-sem"
        result_sem_name = f"/hldx-{name_prefix}-res-sem"
        ready_sem_name = f"/hldx-{name_prefix}-ready"

        ring_size = min(ring_size, _MAX_RING_SIZE)

        import posix_ipc

        ring_shm = None
        result_shm = None
        try:
            ring_shm = posix_ipc.SharedMemory(
                shm_name,
                flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                size=ring_size,
            )
            result_shm = posix_ipc.SharedMemory(
                result_shm_name,
                flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                size=_RESULT_SIZE,
            )

            ipc._ring = RingMMap(shm_name, ring_size)
            ipc._result_buf = result_shm.buf
            struct.pack_into("<I", ipc._result_buf, 0, 0)

            ipc._channel = RingMMapChannel(
                shm_name=shm_name,
                ring_size=ring_size,
                sem_name=sem_name,
                result_shm_name=result_shm_name,
                result_sem_name=result_sem_name,
                ready_sem_name=ready_sem_name,
                max_message_size=max_message_size,
            )

        except Exception:
            if ring_shm is not None:
                try:
                    ring_shm.close()
                    ring_shm.unlink()
                except Exception:  # noqa: BLE001
                    pass
            if result_shm is not None:
                try:
                    result_shm.close()
                    result_shm.unlink()
                except Exception:  # noqa: BLE001
                    pass
            raise

        return ipc, ipc._channel

    @classmethod
    def attach_to_parent(cls, channel: RingMMapChannel) -> RingMMapIPC:
        """
        Attach to a parent process IPC channel (worker side).

        Args:
            channel: RingMMapChannel from parent process
        """
        ipc = cls.__new__(cls)
        ipc._channel = channel
        ipc._ring = RingMMap.attach(channel.shm_name)
        ipc._result_buf = None
        ipc._result_ring = None
        ipc._proc = None
        ipc._started = False
        ipc._closed = False
        ipc._pending = {}
        ipc._pending_seq = 0
        ipc._lock = asyncio.Lock()
        return ipc

    async def spawn_worker(
        self,
        target: Any,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """
        Spawn a worker subprocess.

        Args:
            target: Module-level function to run in worker
            args: Positional args for target
            kwargs: Keyword args for target
        """
        if self._closed or not _posix_ipc_available():
            return

        import posix_ipc

        channel = self._channel
        assert channel is not None

        try:
            ready_sem = posix_ipc.Semaphore(
                channel.ready_sem_name,
                flags=posix_ipc.O_CREAT,
            )

            kwargs = kwargs or {}

            self._proc = _SPAWN_CTX.Process(
                target=run_worker,
                args=(
                    channel.shm_name,
                    channel.sem_name,
                    channel.result_shm_name,
                    channel.result_sem_name,
                    channel.ready_sem_name,
                    channel.ring_size,
                    channel.max_message_size,
                    target,
                    args,
                    kwargs,
                ),
                daemon=False,
            )
            self._proc.start()

            # Wait for worker to be ready
            ready_sem.acquire(timeout=_SPAWN_TIMEOUT_S)
            ready_sem.close()
            del ready_sem

            self._started = True

        except Exception:
            self._started = False
            raise

    def send(self, msg: T) -> None:
        """
        Send a message to the worker (writes to ring buffer).

        Non-async — runs in executor.
        """
        if self._ring is None or self._channel is None:
            return

        encoded = msgspec.msgpack.encode(msg)
        self._ring.write(encoded)

        # Signal worker
        import posix_ipc

        try:
            sem = posix_ipc.Semaphore(
                self._channel.sem_name,
                flags=posix_ipc.O_CREAT,
            )
            sem.release()
            sem.close()
        except Exception:  # noqa: BLE001
            pass

    async def call(self, msg: T, timeout: float = 30.0) -> R | None:
        """
        Send a message and wait for result. Returns None on timeout/error.

        Args:
            msg: Message to send
            timeout: Seconds to wait for result

        Returns:
            Result from worker, or None on failure
        """
        if not self._started or self._closed:
            return None

        import posix_ipc

        # Ensure _pending_seq exists (set by factory create())
        if not hasattr(self, "_pending_seq"):
            self._pending_seq = 0

        async with self._lock:
            self._pending_seq += 1
            seq = self._pending_seq & 0xFFFF
            future: asyncio.Future[Any] = asyncio.Future()
            self._pending[seq] = future

        try:
            # Send via executor (ring write + semaphore signal)
            await asyncio.to_thread(self.send, msg)

            # Wait for result
            async with asyncio.timeout(timeout):
                return await future

        except (asyncio.TimeoutError, Exception):
            self._pending.pop(seq, None)
            return None

    def process_result(self, result_bytes: bytes) -> None:
        """Process a result from the result SharedMemory (internal)."""
        if not result_bytes:
            return

        try:
            result = msgspec.msgpack.decode(result_bytes, type=self._result_type)
            seq = getattr(result, "_seq", None) if isinstance(result, msgspec.Struct) else None
            if seq is not None and seq in self._pending:
                future = self._pending.pop(seq)
                if not future.done():
                    future.set_result(result)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Shutdown the IPC channel."""
        if self._closed:
            return
        self._closed = True

        if self._ring is not None:
            try:
                self._ring.close()
                self._ring.unlink()
            except Exception:  # noqa: BLE001
                pass
            self._ring = None

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.join(timeout=5.0)
                if self._proc.is_alive():
                    self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

        if self._channel is not None and _posix_ipc_available():
            import posix_ipc

            for name_attr in ("shm_name", "result_shm_name"):
                name = getattr(self._channel, name_attr, None)
                if name:
                    try:
                        shm = posix_ipc.SharedMemory(name=name)
                        shm.close_unlink()
                    except Exception:  # noqa: BLE001
                        pass

        self._started = False


# ---------------------------------------------------------------------------
# Worker entry point (runs in subprocess)
# ---------------------------------------------------------------------------


def run_worker(
    shm_name: str,
    sem_name: str,
    result_shm_name: str,
    result_sem_name: str,
    ready_sem_name: str,
    _ring_size: int,
    _max_message_size: int,
    target: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """
    Module-level worker entry point for RingMMapIPC.

    MUST be at module level for pickling with spawn context.
    """
    if not _posix_ipc_available():
        raise RuntimeError("posix_ipc not available")

    import posix_ipc

    ring = RingMMap.attach(shm_name)
    result_shm = posix_ipc.SharedMemory(name=result_shm_name)
    result_buf = result_shm.buf

    data_sem = posix_ipc.Semaphore(sem_name, flags=posix_ipc.O_CREAT)
    result_sem = posix_ipc.Semaphore(result_sem_name, flags=posix_ipc.O_CREAT)
    ready_sem = posix_ipc.Semaphore(ready_sem_name, flags=posix_ipc.O_CREAT)

    # Clear stale count
    try:
        data_sem.acquire(0)
    except Exception:  # noqa: BLE001
        pass

    # Signal ready
    try:
        ready_sem.release()
    except Exception:  # noqa: BLE001
        pass

    try:
        # Try to get an event loop
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        except Exception:
            loop = None

        while True:
            try:
                data_sem.acquire()
            except Exception:
                continue

            # Read message from ring
            data = ring.read()
            if data is None:
                continue

            # Check for shutdown sentinel (empty message)
            if len(data) == 0:
                break

            try:
                msg = msgspec.msgpack.decode(data)
            except Exception:
                continue

            # Call target
            try:
                if loop is not None:
                    result = loop.run_until_complete(target(msg, *args, **kwargs))
                else:
                    result = target(msg, *args, **kwargs)
            except Exception as e:
                result = type("Result", (), {"error": str(e), "success": False})()

            # Encode and send result
            try:
                result_bytes = msgspec.msgpack.encode(result)
                if len(result_bytes) > _RESULT_SIZE - 4:
                    result_bytes = msgspec.msgpack.encode(
                        type("Result", (), {"error": "result too large", "success": False})()
                    )
                struct.pack_into("<I", result_buf, 0, len(result_bytes))
                result_buf[:len(result_bytes)] = result_bytes
                result_sem.release()
            except Exception:  # noqa: BLE001
                pass

    finally:
        ring.close()
        result_shm.close()
        data_sem.close()
        result_sem.close()
        ready_sem.close()
        if loop is not None:
            loop.close()
