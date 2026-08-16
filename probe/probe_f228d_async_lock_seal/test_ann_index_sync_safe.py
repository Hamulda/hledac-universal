"""
test_ann_index_sync_safe.py
==========================
Verifies ann_index.py locks are SAFE_SYNC_BOUNDARY:
- _ANNIndex._lock guards LanceDB table operations from sync context
- _ann_index_lock guards module-level singleton init from sync context
No async callers exist; threading.Lock is correct.
"""

import inspect
import threading

import pytest
from _core import aclose


class TestANNIndexLockSafety:
    """Tests for ann_index.py lock classification."""

    # ── Shared test helpers ──────────────────────────────────────────────────

    @staticmethod
    def _assert_is_sync_def(class_or_module, method_name: str) -> None:
        """Assert that a method or function is synchronous (not a coroutine)."""
        method = getattr(class_or_module, method_name, None)
        assert method is not None, f"{method_name!r} not found"
        assert not inspect.iscoroutinefunction(method), (
            f"{method_name}() should be sync def"
    )

    @staticmethod
    def _assert_no_await_in_lock(class_or_module, method_name: str) -> None:
        """Assert that a method has no await calls inside its lock block."""
        method = getattr(class_or_module, method_name)
        source = inspect.getsource(method)
        lines = source.split("\n")
        in_lock_block = False
        lock_indent = 0
        for line in lines:
            stripped = line.strip()
            if "with self._lock:" in stripped:
                in_lock_block = True
                lock_indent = len(line) - len(line.lstrip())
                continue
            if in_lock_block:
                current_indent = len(line) - len(line.lstrip())
                if line.strip() and current_indent <= lock_indent and not line.strip().startswith("#"):
                    in_lock_block = False
                    continue
                if stripped.startswith("await ") and "self._lock" not in stripped:
                    pytest.fail(f"await found inside _lock block in {method_name}(): {stripped}")

    def test_annindex_lock_is_threading_lock(self):
        """_ANNIndex._lock must be threading.Lock (not asyncio.Lock)."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        assert isinstance(idx._lock, threading.Lock), (
            "_ANNIndex._lock should be threading.Lock — guards LanceDB ops in sync context"
    )

    def test_module_lock_is_threading_lock(self):
        """Module-level _ann_index_lock must be threading.Lock."""
        from knowledge import ann_index
        assert isinstance(ann_index._ann_index_lock, threading.Lock), (
            "_ann_index_lock should be threading.Lock"
    )

    def test_ann_search_is_sync_def(self):
        """ann_search() is sync def — no async def."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        self._assert_is_sync_def(idx, "ann_search")

    def test_upsert_is_sync_def(self):
        """upsert() is sync def — no async def."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        self._assert_is_sync_def(idx, "upsert")

    def test_no_await_inside_lock_ann_search(self):
        """No await inside lock block in ann_search()."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        self._assert_no_await_in_lock(idx, "ann_search")

    def test_no_await_inside_lock_upsert(self):
        """No await inside lock block in upsert()."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        self._assert_no_await_in_lock(idx, "upsert")

    def test_get_ann_index_is_sync(self):
        """get_ann_index() is sync def — no async def."""
        from knowledge import ann_index
        self._assert_is_sync_def(ann_index, "get_ann_index")

    def test_close_is_sync_def(self):
        """close() is sync def."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        self._assert_is_sync_def(idx, "close")
