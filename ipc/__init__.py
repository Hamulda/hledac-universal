"""
IPC — zero-copy inter-process communication primitives.

Modules:
    ring_mmap_ipc — msgspec.msgpack + POSIX shared memory ring buffer

Exports:
    RingMMapIPC — high-level IPC over mmap + msgspec.msgpack
    RingMMap — low-level mmap ring buffer
    RingMMapChannel — channel descriptor for worker process
    run_worker — module-level worker entry point

M1 8GB: All IPC is zero-copy via mmap. posix_ipc is Darwin-only.
Always-on, fail-safe, bounded.

Author: Issue #22
"""

from __future__ import annotations

from .ring_mmap_ipc import (
    RingMMapIPC,
    RingMMap,
    RingMMapChannel,
    run_worker,
)

__all__ = [
    "RingMMapIPC",
    "RingMMap",
    "RingMMapChannel",
    "run_worker",
]
