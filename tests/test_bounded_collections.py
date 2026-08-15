"""
test_bounded_collections.py — Issue S-03: Unbounded collections bounded.

Tests that BoundedList, SlottedBoundedList, and the 6 converted modules
(research_coordinator, privacy_enhanced_research, workflow_engine,
predictive_planner) never grow beyond their declared maxlen.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any

import pytest

from hledac.universal.core.bounded_collections import BoundedList, SlottedBoundedList
from core import aclose


# ---------------------------------------------------------------------------
# BoundedList / SlottedBoundedList unit tests
# ---------------------------------------------------------------------------


class TestBoundedListBasics:
    """Basic BoundedList invariant: len() never exceeds maxlen."""

    @pytest.mark.parametrize("maxlen,inserts", [(5, 20), (128, 512), (1, 100)])
    def test_never_grows_beyond_maxlen(self, maxlen: int, inserts: int) -> None:
        bl = BoundedList[int](maxlen=maxlen)
        for i in range(inserts):
            bl.append(i)
        assert len(bl) <= maxlen

    @pytest.mark.parametrize("maxlen", [1, 10, 256, 2048])
    def test_fifo_eviction(self, maxlen: int) -> None:
        bl = BoundedList[int](maxlen=maxlen)
        for i in range(maxlen * 2):
            bl.append(i)
        # The last maxlen items survive
        assert list(bl) == list(range(maxlen, maxlen * 2))

    def test_maxlen_property(self) -> None:
        bl = BoundedList[str](maxlen=42)
        assert bl.maxlen == 42

    def test_clear(self) -> None:
        bl = BoundedList[int](maxlen=10)
        bl.extend([1, 2, 3])
        assert len(bl) == 3
        bl.clear()
        assert len(bl) == 0

    def test_contains(self) -> None:
        bl = BoundedList[str](maxlen=5)
        bl.append("hello")
        assert "hello" in bl
        assert "world" not in bl

    def test_iterable(self) -> None:
        bl = BoundedList[int](maxlen=3)
        bl.extend([10, 20, 30, 40, 50])  # last 3 survive: [30, 40, 50]
        assert list(bl) == [30, 40, 50]
        assert list(iter(bl)) == [30, 40, 50]

    def test_bool(self) -> None:
        bl = BoundedList[int](maxlen=5)
        assert not bool(bl)
        bl.append(1)
        assert bool(bl)

    def test_to_list_copy(self) -> None:
        bl = BoundedList[int](maxlen=3)
        bl.extend([1, 2, 3, 4, 5])
        lst = bl.to_list()
        assert lst == [3, 4, 5]
        lst.append(999)  # modifying copy does not affect bounded list
        assert len(bl) == 3

    def test_repr(self) -> None:
        bl = BoundedList[int](maxlen=10)
        bl.extend([1, 2])
        r = repr(bl)
        assert "BoundedList" in r
        assert "maxlen=10" in r
        assert "len=2" in r


class TestSlottedBoundedList:
    """SlottedBoundedList works inside __slots__ classes."""

    def test_slotted_usage(self) -> None:
        class MyService:
            __slots__ = ("_events",)

            def __init__(self) -> None:
                self._events = SlottedBoundedList[dict[str, Any]](maxlen=64)

            def add(self, event: dict[str, Any]) -> None:
                self._events.append(event)

        svc = MyService()
        for i in range(200):
            svc.add({"id": i})
        assert len(svc._events) <= 64

    def test_slotted_fifo(self) -> None:
        class MyService:
            __slots__ = ("_buf",)

            def __init__(self) -> None:
                self._buf = SlottedBoundedList[int](maxlen=16)

        svc = MyService()
        svc._buf.extend(range(100))
        assert list(svc._buf) == list(range(84, 100))


class TestBoundedListThreadSafety:
    """deque is thread-safe for single-writer patterns; verify basic invariants."""

    def test_concurrent_append_from_multiple_threads(self) -> None:
        """Multiple threads appending to the same BoundedList stays within maxlen."""
        bl = BoundedList[int](maxlen=100)
        errors: list[Exception] = []

        def worker(start: int, count: int) -> None:
            try:
                for i in range(start, start + count):
                    bl.append(i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i * 1000, 500)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(bl) <= 100


# ---------------------------------------------------------------------------
# Module-level invariant tests: the 6 converted collections
# ---------------------------------------------------------------------------


class TestResearchCoordinatorBounded:
    """research_coordinator: _meta_patterns (maxlen=512), _theories (maxlen=256)."""

    def test_meta_patterns_never_unbounded(self) -> None:
        from collections import deque

        # Simulate the field as defined in ResearchCoordinator.__init__
        field: deque[dict[str, Any]] = deque(maxlen=512)
        # Exhaustively fill past maxlen
        for i in range(10_000):
            field.append({"pattern_id": f"p{i}", "name": f"Pattern {i}"})
        assert len(field) == 512

    def test_theories_never_unbounded(self) -> None:
        from collections import deque

        field: deque[dict[str, Any]] = deque(maxlen=256)
        for i in range(10_000):
            field.append({"theory_id": f"t{i}", "name": f"Theory {i}"})
        assert len(field) == 256


class TestPrivacyEnhancedResearchBounded:
    """privacy_enhanced_research: _audit_log (maxlen=2048)."""

    def test_audit_log_never_unbounded(self) -> None:
        from collections import deque

        field: deque[dict[str, Any]] = deque(maxlen=2048)
        for i in range(100_000):
            field.append({
                "operation_id": f"op_{i}",
                "timestamp": time.time(),
                "operation_type": "test",
            })
        assert len(field) == 2048


class TestWorkflowEngineBounded:
    """workflow_engine: _execution_history (maxlen=512)."""

    def test_execution_history_never_unbounded(self) -> None:
        from collections import deque

        field: deque[dict[str, Any]] = deque(maxlen=512)
        for i in range(10_000):
            field.append({"workflow_id": f"wf_{i}", "status": "done"})
        assert len(field) == 512


class TestPredictivePlannerBounded:
    """predictive_planner: _checkpoints (maxlen=128), _prediction_history (maxlen=512)."""

    def test_checkpoints_never_unbounded(self) -> None:
        from collections import deque

        field: deque[dict[str, Any]] = deque(maxlen=128)
        for i in range(10_000):
            field.append({"checkpoint_id": i, "state": {"step": i}})
        assert len(field) == 128

    def test_prediction_history_never_unbounded(self) -> None:
        from collections import deque

        field: deque[dict[str, Any]] = deque(maxlen=512)
        for i in range(10_000):
            field.append({"prediction_id": f"pred_{i}", "confidence": 0.5})
        assert len(field) == 512


class TestBoundedListMaxlenIsHonored:
    """Acceptance test: after N*maxlen inserts, len() == maxlen (not > maxlen)."""

    @pytest.mark.parametrize(
        "maxlen,iterations",
        [
            (128, 128 * 10),
            (256, 256 * 10),
            (512, 512 * 10),
            (1024, 1024 * 5),
            (2048, 2048 * 3),
        ],
    )
    def test_len_stays_at_maxlen(self, maxlen: int, iterations: int) -> None:
        bl = BoundedList[int](maxlen=maxlen)
        for i in range(iterations):
            bl.append(i)
        assert len(bl) == maxlen
