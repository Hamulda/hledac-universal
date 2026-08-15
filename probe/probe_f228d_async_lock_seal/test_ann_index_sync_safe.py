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
from core import aclose


class TestANNIndexLockSafety:
    """Tests for ann_index.py lock classification."""

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
        assert not inspect.iscoroutinefunction(idx.ann_search), (
            "ann_search() should be sync def — called from embedding_pipeline sync context"
        )

    def test_upsert_is_sync_def(self):
        """upsert() is sync def — no async def."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        assert not inspect.iscoroutinefunction(idx.upsert), (
            "upsert() should be sync def"
        )

    def test_no_await_inside_lock_ann_search(self):
        """No await inside lock block in ann_search()."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        source = inspect.getsource(idx.ann_search)
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
                    pytest.fail(f"await found inside _lock block in ann_search(): {stripped}")

    def test_no_await_inside_lock_upsert(self):
        """No await inside lock block in upsert()."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        source = inspect.getsource(idx.upsert)
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
                    pytest.fail(f"await found inside _lock block in upsert(): {stripped}")

    def test_get_ann_index_is_sync(self):
        """get_ann_index() is sync def — no async def."""
        from knowledge.ann_index import get_ann_index
        assert not inspect.iscoroutinefunction(get_ann_index), (
            "get_ann_index() should be sync def"
        )

    def test_close_is_sync_def(self):
        """close() is sync def."""
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        idx = _ANNIndex(Path("/tmp/test_ann"))
        assert not inspect.iscoroutinefunction(idx.close), (
            "close() should be sync def"
        )
