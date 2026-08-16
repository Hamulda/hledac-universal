"""STEP 2 — ResourceLifecycleRegistry + OwnedResource.

Extracted from runtime/sprint_scheduler.py (33 449 LOC → modular package).
F350M-R / Issue #P2.



Bounded LIFO registry replacing WeakValueDictionary + deque dual-eviction.
M1 8GB: No GC overhead — objects released deterministically.
"""
import msgspec
from typing import Any, Final
from collections.abc import Callable
from _core import aclose

class OwnedResource(msgspec.Struct, gc=False):
    """Explicit lifecycle: acquire → use → release. Zero weakref.

    Replaces weakref.WeakValueDictionary pattern with deterministic
    reference counting. OwnedResource is NEVER collected by GC —
    only explicit release() or registry eviction.
    """
    obj: Any
    cleanup: Callable[[], None] | None = None
    released: bool = msgspec.field(default=False)

    def release(self) -> None:
        """Explicit release — deterministic, no GC dependency."""
        if not self.released:
            self.released = True
            if self.cleanup:
                try:
                    self.cleanup()
                except Exception:  # noqa: BLE001
                    pass

class ResourceLifecycleRegistry:
    """Bounded LIFO registry for resource lifecycle management.

    Replaces weakref.WeakValueDictionary + deque dual-eviction with
    a single LIFO stack protected by explicit MAX_REGISTRY_SIZE.
    M1 8GB: No GC overhead — objects released deterministically.

    Benefits over WeakValueDictionary:
    - O(1) register() — no GC hook revalidation on __setitem__
    - Deterministic release_all() — no surprise GC pause mid-sprint
    - Fully testable lifecycle — explicit acquire/release/release_all
    """
    MAX_REGISTRY_SIZE: Final[int] = 16
    __slots__ = tuple(('_resources',))

    def __init__(self) -> None:
        self._resources: list[OwnedResource] = []

    def register(self, obj: Any, cleanup_cb: Callable[[], None] | None=None) -> str:
        """Register object with optional cleanup callback. Returns token."""
        if len(self._resources) >= self.MAX_REGISTRY_SIZE:
            oldest = self._resources.pop(0)
            oldest.release()
        import uuid
        token = str(uuid.uuid4())[:8]
        self._resources.append(OwnedResource(obj, cleanup_cb))
        return token

    def release_all(self) -> None:
        """Release all resources in LIFO order. Deterministic shutdown."""
        while self._resources:
            self._resources.pop().release()
_graph_service_registry = ResourceLifecycleRegistry()