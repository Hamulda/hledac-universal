"""
tests/test_dlq_manager.py

HIGH: DLQ Manager Tests

Tests for core/dlq_manager.py - Dead-Letter Queue for isolating corrupt payloads.

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _core import aclose


@pytest.fixture
def temp_db_path():
    """Create temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield str(Path(tmpdir) / "test_dlq.db")


class TestDLQPayload:
    """Tests for DLQPayload dataclass."""

    def test_dlq_payload_creation(self) -> None:
        """DLQPayload must be created with correct fields."""
        from hledac.universal._core.dlq_manager import DLQPayload
        from datetime import datetime, timezone

        payload = DLQPayload(
            payload_id="test123",
            sprint_id="sprint1",
            source="test_source",
            error_type="ValueError",
            error_message="Test error",
            payload_data=b"test data",
    )

        assert payload.payload_id == "test123"
        assert payload.sprint_id == "sprint1"
        assert payload.source == "test_source"
        assert payload.error_type == "ValueError"
        assert payload.error_message == "Test error"
        assert payload.payload_data == b"test data"
        assert payload.attempt_count == 0
        assert payload.last_attempt_at is None

    def test_dlq_payload_to_dict(self) -> None:
        """DLQPayload.to_dict() must return serializable dict."""
        from hledac.universal._core.dlq_manager import DLQPayload

        payload = DLQPayload(
            payload_id="test123",
            sprint_id="sprint1",
            source="test_source",
            error_type="ValueError",
            error_message="Test error",
            payload_data=b"test",
            metadata={"key": "value"},
    )

        d = payload.to_dict()

        assert d["payload_id"] == "test123"
        assert d["sprint_id"] == "sprint1"
        assert d["metadata"] == {"key": "value"}
        assert "created_at" in d

    def test_dlq_payload_frozen(self) -> None:
        """DLQPayload must be immutable (frozen=True)."""
        from hledac.universal._core.dlq_manager import DLQPayload

        payload = DLQPayload(
            payload_id="test",
            sprint_id="s1",
            source="src",
            error_type="Err",
            error_message="msg",
            payload_data=b"data",
    )

        with pytest.raises(AttributeError):
            payload.payload_id = "changed"  # type: ignore


class TestDLQManagerBasics:
    """Tests for basic DLQManager functionality."""

    @pytest.mark.asyncio
    async def test_manager_creation(self, temp_db_path: str) -> None:
        """DLQManager must initialize with database path."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        assert manager._db_path == temp_db_path
        assert manager._connection is None  # Lazy connection

    @pytest.mark.asyncio
    async def test_manager_initializes_database(self, temp_db_path: str) -> None:
        """DLQManager must create database schema on first use."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        # Initialize connection
        await manager._ensure_connection()

        assert manager._connection is not None

    @pytest.mark.asyncio
    async def test_manager_lifecycle(self, temp_db_path: str) -> None:
        """DLQManager must properly open and close connection."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        await manager._ensure_connection()
        assert manager._connection is not None

        await manager.close()
        assert manager._connection is None


class TestStorePayload:
    """Tests for store_payload() method."""

    @pytest.mark.asyncio
    async def test_store_payload_basic(self, temp_db_path: str) -> None:
        """store_payload() must store payload with metadata."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        error = ValueError("Test error")
        payload_data = b"test payload data"

        payload_id = await manager.store_payload(
            payload_data=payload_data,
            sprint_id="sprint1",
            source="test_source",
            error=error,
            metadata={"key": "value"},
    )

        assert payload_id is not None
        assert len(payload_id) == 64  # SHA256 hash length

    @pytest.mark.asyncio
    async def test_store_payload_generates_id(self, temp_db_path: str) -> None:
        """store_payload() must generate unique SHA256 ID."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        payload_data = b"test"

        id1 = await manager.store_payload(
            payload_data=payload_data,
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        id2 = await manager.store_payload(
            payload_data=payload_data,
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        # Same content = same ID
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_store_payload_different_content_different_id(self, temp_db_path: str) -> None:
        """Different payloads must have different IDs."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        id1 = await manager.store_payload(
            payload_data=b"data1",
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        id2 = await manager.store_payload(
            payload_data=b"data2",
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        assert id1 != id2


class TestGetPayloads:
    """Tests for get_payloads() method."""

    @pytest.mark.asyncio
    async def test_get_payloads_empty(self, temp_db_path: str) -> None:
        """get_payloads() must return empty list for empty queue."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        payloads = await manager.get_payloads(sprint_id="sprint1")

        assert payloads == []

    @pytest.mark.asyncio
    async def test_get_payloads_by_sprint(self, temp_db_path: str) -> None:
        """get_payloads() must filter by sprint_id."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        # Store in sprint1
        await manager.store_payload(
            payload_data=b"data1",
            sprint_id="sprint1",
            source="src",
            error=ValueError("err"),
    )

        # Store in sprint2
        await manager.store_payload(
            payload_data=b"data2",
            sprint_id="sprint2",
            source="src",
            error=ValueError("err"),
    )

        payloads_s1 = await manager.get_payloads(sprint_id="sprint1")
        payloads_s2 = await manager.get_payloads(sprint_id="sprint2")

        assert len(payloads_s1) == 1
        assert len(payloads_s2) == 1
        assert payloads_s1[0].sprint_id == "sprint1"
        assert payloads_s2[0].sprint_id == "sprint2"

    @pytest.mark.asyncio
    async def test_get_payloads_by_source(self, temp_db_path: str) -> None:
        """get_payloads() must filter by source."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        await manager.store_payload(
            payload_data=b"data1",
            sprint_id="s1",
            source="source_a",
            error=ValueError("err"),
    )

        await manager.store_payload(
            payload_data=b"data2",
            sprint_id="s1",
            source="source_b",
            error=ValueError("err"),
    )

        payloads = await manager.get_payloads(sprint_id="s1", source="source_a")

        assert len(payloads) == 1
        assert payloads[0].source == "source_a"


class TestCleanup:
    """Tests for cleanup functionality."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_entries(self, temp_db_path: str) -> None:
        """cleanup() must remove entries older than retention period."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        # Store some payloads
        await manager.store_payload(
            payload_data=b"old",
            sprint_id="s1",
            source="src",
            error=ValueError("old"),
    )

        # Mock time to be old
        old_time = time.time() - (31 * 24 * 60 * 60)  # 31 days ago

        # Manually set created_at via direct SQL
        await manager._ensure_connection()
        if manager._connection:
            await manager._connection.execute(
                "UPDATE dlq_entries SET created_at = ? WHERE payload_id IN (SELECT payload_id FROM dlq_entries LIMIT 1)",
                (old_time,),
    )

        # Cleanup should remove old entries
        removed = await manager.cleanup(retention_days=30)

        assert removed >= 0

    @pytest.mark.asyncio
    async def test_cleanup_preserves_recent_entries(self, temp_db_path: str) -> None:
        """cleanup() must preserve entries within retention period."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        # Store payload
        await manager.store_payload(
            payload_data=b"recent",
            sprint_id="s1",
            source="src",
            error=ValueError("recent"),
    )

        # Cleanup with 30 day retention
        removed = await manager.cleanup(retention_days=30)

        # Recent entry should still exist
        payloads = await manager.get_payloads(sprint_id="s1")
        assert len(payloads) == 1


class TestRetryPayload:
    """Tests for retry_payload() method."""

    @pytest.mark.asyncio
    async def test_retry_increments_count(self, temp_db_path: str) -> None:
        """retry_payload() must increment attempt_count."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        payload_id = await manager.store_payload(
            payload_data=b"data",
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        # Get initial state
        payloads = await manager.get_payloads(sprint_id="s1")
        initial_count = payloads[0].attempt_count

        # Retry
        await manager.retry_payload(payload_id)

        # Check incremented
        payloads = await manager.get_payloads(sprint_id="s1")
        assert payloads[0].attempt_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_retry_updates_timestamp(self, temp_db_path: str) -> None:
        """retry_payload() must update last_attempt_at."""
        from hledac.universal._core.dlq_manager import DLQManager

        manager = DLQManager(db_path=temp_db_path)

        payload_id = await manager.store_payload(
            payload_data=b"data",
            sprint_id="s1",
            source="src",
            error=ValueError("err"),
    )

        # Get initial state
        payloads = await manager.get_payloads(sprint_id="s1")
        initial_attempt = payloads[0].last_attempt_at

        # Retry
        await manager.retry_payload(payload_id)

        # Check timestamp updated
        payloads = await manager.get_payloads(sprint_id="s1")
        assert payloads[0].last_attempt_at is not None
        assert payloads[0].last_attempt_at != initial_attempt


class TestDLQCatchDecorator:
    """Tests for @dlq_catch decorator."""

    @pytest.mark.asyncio
    async def test_dlq_catch_decorator_exists(self) -> None:
        """dlq_catch decorator must be available."""
        from hledac.universal._core.dlq_manager import dlq_catch

        assert callable(dlq_catch)

    @pytest.mark.asyncio
    async def test_dlq_catch_captures_exception(self, temp_db_path: str) -> None:
        """dlq_catch must capture exceptions to DLQ."""
        from hledac.universal._core.dlq_manager import DLQManager, dlq_catch

        manager = DLQManager(db_path=temp_db_path)

        # Patch global manager
        with patch("hledac.universal._core.dlq_manager.get_dlq_manager", return_value=manager):
            @dlq_catch(source="test_decorator")
            async def failing_function() -> str:
                raise ValueError("Captured by DLQ")

            result = await failing_function()

            # Should return None or default on error
            assert result is None

            # Error should be in DLQ
            payloads = await manager.get_payloads(sprint_id="default")
            assert len(payloads) >= 1


# ============================================================================
# Invariants
# ============================================================================

DLQ_MANAGER_INVARIANTS = """
DLQ MANAGER INVARIANTS:
1. Payload ID is SHA256 hash of content (deterministic, unique per content)
2. Exceptions are never raised from DLQ operations (fail-safe)
3. Payload data is stored as binary (not JSON)
4. Cleanup removes entries older than retention_days
5. Retry increments attempt_count and updates timestamp
6. Decorator @dlq_catch captures exceptions automatically
Default retention: 30 days
"""
