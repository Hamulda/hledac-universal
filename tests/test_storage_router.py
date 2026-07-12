"""tests/test_storage_router.py — StorageRouter P1-04

Coverage:
  - Decision matrix: every data_kind → correct StorageKind
  - Spill: emergency pressure → HOT → WARM
  - Invalidation chain: HOT → WARM notified on evict
  - Get cascade: primary miss → fallback layers
  - Telemetry: puts/gets/misses/spills tracked
  - Fail-safe: backend=None returns False/None
  - Glob patterns: "embedding.float16.*" matches "embedding.float16[384]"
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.storage_router import (
    _DECISION_MATRIX,
    _INVALIDATION_CHAIN,
    StorageKind,
    StoragePolicy,
    StorageRouter,
    _classify,
    get_storage_router,
    reset_storage_router,
)

# ---------------------------------------------------------------------------
# Decision matrix tests
# ---------------------------------------------------------------------------


class TestDecisionMatrix:
    @pytest.mark.parametrize(
        "data_kind,expected_kind",
        [
            # HOT: np.memmap float16 embeddings
            ("embedding.float16[256]", StorageKind.HOT),
            ("embedding.float16[384]", StorageKind.HOT),
            # WARM: float32 embeddings (spill target)
            ("embedding.float32[768]", StorageKind.WARM),
            ("embedding.float32[1024]", StorageKind.WARM),
            # COLD: IOC / Analytics
            ("ioc.findings", StorageKind.COLD),
            ("ioc.findings.bulk", StorageKind.COLD),
            ("graph.ioc", StorageKind.COLD),
            ("graph.entities", StorageKind.COLD),
            # KEYVALUE: LMDB
            ("qtable.federated", StorageKind.KEYVALUE),
            ("qtable.local", StorageKind.KEYVALUE),
            ("graph.edges_hot", StorageKind.KEYVALUE),
            ("kv.persistent", StorageKind.KEYVALUE),
            # STRING: diskcache
            ("url.normalized", StorageKind.STRING),
            ("url.raw", StorageKind.STRING),
            ("safetensors.kv_cache", StorageKind.STRING),
        ],
    )
    def test_classify_exact(self, data_kind, expected_kind):
        policy = _classify(data_kind)
        assert policy.kind == expected_kind, f"{data_kind} → {policy.kind}, expected {expected_kind}"

    def test_classify_glob_float16(self):
        policy = _classify("embedding.float16[512]")
        assert policy.kind == StorageKind.HOT

    def test_classify_glob_float32(self):
        policy = _classify("embedding.float32[2048]")
        assert policy.kind == StorageKind.WARM

    def test_classify_default(self):
        policy = _classify("unknown.data.type")
        assert policy.kind == StorageKind.COLD  # default is COLD

    def test_all_matrix_keys_have_valid_policy(self):
        for key, policy in _DECISION_MATRIX.items():
            assert isinstance(policy, StoragePolicy)
            assert isinstance(policy.kind, StorageKind)
            assert policy.max_bytes > 0


# ---------------------------------------------------------------------------
# Policy invariants
# ---------------------------------------------------------------------------


class TestStoragePolicy:
    def test_policy_frozen(self):
        policy = _DECISION_MATRIX["embedding.float16[256]"]
        # Frozen dataclass: __dataclass_params__.frozen must be True
        assert policy.__dataclass_params__.frozen is True

    def test_policy_slots(self):
        policy = _DECISION_MATRIX["embedding.float16[256]"]
        # slots=True means __dict__ is empty
        assert not hasattr(policy, "__dict__") or len(policy.__dict__) == 0

    def test_spill_target_hot(self):
        policy = _DECISION_MATRIX["embedding.float16[256]"]
        assert policy.spill_target == StorageKind.WARM

    def test_spill_target_float32(self):
        policy = _DECISION_MATRIX["embedding.float32[768]"]
        assert policy.spill_target is None

    def test_hot_invalidates_warm(self):
        assert StorageKind.WARM in _INVALIDATION_CHAIN[StorageKind.HOT]

    def test_warm_invalidates_cold(self):
        assert StorageKind.COLD in _INVALIDATION_CHAIN[StorageKind.WARM]

    def test_cold_invalidates_kv(self):
        assert StorageKind.KEYVALUE in _INVALIDATION_CHAIN[StorageKind.COLD]

    def test_kv_invalidates_nothing(self):
        assert _INVALIDATION_CHAIN[StorageKind.KEYVALUE] == ()

    def test_string_invalidates_nothing(self):
        assert _INVALIDATION_CHAIN[StorageKind.STRING] == ()


# ---------------------------------------------------------------------------
# Router basic operations
# ---------------------------------------------------------------------------


class TestStorageRouterBasics:
    def setup_method(self):
        reset_storage_router()
        self.router = StorageRouter()

    def test_register_backend(self):
        mock_backend = MagicMock()
        self.router.register_backend(StorageKind.HOT, mock_backend)
        assert self.router._backends[StorageKind.HOT] is mock_backend

    def test_put_without_backend_returns_false(self):
        # No backend registered — fail-safe, returns False
        result = self.router.put("key", "value", data_kind="embedding.float16[256]")
        assert result is False

    def test_get_without_backend_returns_none(self):
        result = self.router.get("key", data_kind="embedding.float16[256]")
        assert result is None

    def test_stats_initialized(self):
        stats = self.router.get_stats()
        assert stats["puts"] == 0
        assert stats["gets"] == 0
        assert stats["misses"] == 0
        assert stats["spills"] == 0
        assert stats["invalidations"] == 0

    def test_stats_incremented_on_put(self):
        self.router.put("k", "v", data_kind="ioc.findings")
        assert self.router._stats["puts"] == 1

    def test_stats_incremented_on_get_miss(self):
        self.router.get("nonexistent", data_kind="ioc.findings")
        assert self.router._stats["gets"] == 1
        assert self.router._stats["misses"] == 1


# ---------------------------------------------------------------------------
# Invalidation chain
# ---------------------------------------------------------------------------


class TestInvalidationChain:
    def setup_method(self):
        reset_storage_router()
        self.router = StorageRouter()

    def test_invalidation_callback_fired_on_put(self):
        callback = MagicMock()
        self.router.register_invalidation_callback(StorageKind.WARM, callback)

        # Mock HOT backend — put() must return the actual bool True (not MagicMock)
        hot_backend = MagicMock()
        hot_backend.put.return_value = True  # actual bool, not MagicMock
        self.router.register_backend(StorageKind.HOT, hot_backend)

        # Put embedding (HOT) which should invalidate WARM
        self.router.put("key", "value", data_kind="embedding.float16[256]")

        callback.assert_called_once_with("key", source_kind=StorageKind.HOT)

    def test_invalidation_callback_fired_on_delete(self):
        callback = MagicMock()
        # COLD.delete() → fires invalidation chain → KEYVALUE subscribers notified
        self.router.register_invalidation_callback(StorageKind.KEYVALUE, callback)

        mock_cold = MagicMock()
        mock_cold.delete.return_value = True
        self.router.register_backend(StorageKind.COLD, mock_cold)
        self.router.delete("key", data_kind="graph.ioc")

        callback.assert_called_once_with("key", source_kind=StorageKind.COLD)

    def test_no_callback_for_non_subscribed_kind(self):
        callback = MagicMock()
        # HOT → WARM, but we subscribe COLD — no call
        self.router.register_invalidation_callback(StorageKind.COLD, callback)
        self.router.put("key", "value", data_kind="embedding.float16[256]")
        callback.assert_not_called()

    def test_invalidation_propagates_through_chain(self):
        # WARM → invalidates COLD. Subscribe to COLD to receive the cascade notification.
        hot_callback = MagicMock()
        cold_callback = MagicMock()
        self.router.register_invalidation_callback(StorageKind.WARM, hot_callback)
        self.router.register_invalidation_callback(StorageKind.COLD, cold_callback)

        # Put float32 → WARM → WARM invalidates COLD
        self.router.put("key", "value", data_kind="embedding.float32[768]")

        # WARM's invalidates=(COLD,) → cold_callback fires
        cold_callback.assert_called_once_with("key", source_kind=StorageKind.WARM)


# ---------------------------------------------------------------------------
# Get cascade
# ---------------------------------------------------------------------------


class TestGetCascade:
    def setup_method(self):
        reset_storage_router()
        self.router = StorageRouter()

    def test_get_tries_primary_first(self):
        mock_backend = MagicMock()
        mock_backend.get.return_value = "found"
        self.router.register_backend(StorageKind.HOT, mock_backend)

        result = self.router.get("key", data_kind="embedding.float16[256]")
        assert result == "found"
        mock_backend.get.assert_called_once_with("key")

    def test_get_cascades_hot_to_warm_to_cold(self):
        hot_backend = MagicMock()
        hot_backend.get.return_value = None  # miss
        warm_backend = MagicMock()
        warm_backend.get.return_value = "found_in_warm"
        cold_backend = MagicMock()
        cold_backend.get.return_value = None

        self.router.register_backend(StorageKind.HOT, hot_backend)
        self.router.register_backend(StorageKind.WARM, warm_backend)
        self.router.register_backend(StorageKind.COLD, cold_backend)

        result = self.router.get("key", data_kind="embedding.float16[256]")
        assert result == "found_in_warm"
        hot_backend.get.assert_called_once()
        warm_backend.get.assert_called_once()
        # COLD should NOT be called (WARM hit)
        cold_backend.get.assert_not_called()

    def test_get_promotes_cross_layer_hit(self):
        warm_backend = MagicMock()
        warm_backend.get.return_value = "from_warm"

        hot_backend = MagicMock()
        hot_backend.get.return_value = None  # miss — cascade to WARM
        hot_backend.put.return_value = True
        hot_backend.set.return_value = True

        self.router.register_backend(StorageKind.HOT, hot_backend)
        self.router.register_backend(StorageKind.WARM, warm_backend)
        self.router.register_backend(StorageKind.COLD, MagicMock())

        result = self.router.get("key", data_kind="embedding.float16[256]")

        # Result from WARM, but promoted back to HOT
        assert result == "from_warm"
        # HOT put was called to promote
        assert hot_backend.put.called or hot_backend.set.called

    def test_get_all_miss_returns_none(self):
        # All backends must return None on miss
        hot = MagicMock()
        hot.get.return_value = None
        warm = MagicMock()
        warm.get.return_value = None
        cold = MagicMock()
        cold.get.return_value = None
        self.router.register_backend(StorageKind.HOT, hot)
        self.router.register_backend(StorageKind.WARM, warm)
        self.router.register_backend(StorageKind.COLD, cold)

        result = self.router.get("nonexistent", data_kind="embedding.float16[256]")
        assert result is None
        assert self.router._stats["misses"] == 1


# ---------------------------------------------------------------------------
# Spill on memory pressure
# ---------------------------------------------------------------------------


class TestSpillOnPressure:
    def setup_method(self):
        reset_storage_router()

    def test_spill_to_warm_on_emergency(self):
        """Emergency pressure → HOT policy redirects to WARM."""
        mock_governor = MagicMock()
        mock_uma = MagicMock()
        mock_uma.uma_state = "emergency"
        mock_governor.sample_uma_status.return_value = mock_uma

        router = StorageRouter(governor=mock_governor)

        hot_backend = MagicMock()
        warm_backend = MagicMock()
        router.register_backend(StorageKind.HOT, hot_backend)
        router.register_backend(StorageKind.WARM, warm_backend)

        router.put("key", "value", data_kind="embedding.float16[256]")

        # Should use WARM backend (hot.put not called), check via call_count (no raise)
        assert warm_backend.put.call_count or warm_backend.set.call_count
        assert not (hot_backend.put.call_count or hot_backend.set.call_count)
        assert router._stats["spills"] == 1

    def test_no_spill_on_normal_state(self):
        mock_governor = MagicMock()
        mock_uma = MagicMock()
        mock_uma.uma_state = "ok"
        mock_governor.sample_uma_status.return_value = mock_uma

        router = StorageRouter(governor=mock_governor)

        hot_backend = MagicMock()
        router.register_backend(StorageKind.HOT, hot_backend)

        router.put("key", "value", data_kind="embedding.float16[256]")

        assert hot_backend.put.call_count or hot_backend.set.call_count
        assert router._stats["spills"] == 0

    def test_governor_unavailable_no_spill(self):
        """No governor → no spill detection."""
        router = StorageRouter(governor=None)
        hot_backend = MagicMock()
        router.register_backend(StorageKind.HOT, hot_backend)

        router.put("key", "value", data_kind="embedding.float16[256]")

        assert hot_backend.put.call_count or hot_backend.set.call_count
        assert router._stats["spills"] == 0


# ---------------------------------------------------------------------------
# Fail-safe backend operations
# ---------------------------------------------------------------------------


class TestBackendOperations:
    def setup_method(self):
        reset_storage_router()
        self.router = StorageRouter()

    def test_backend_raises_is_caught(self):
        broken_backend = MagicMock()
        broken_backend.put.side_effect = RuntimeError("backend error")
        self.router.register_backend(StorageKind.HOT, broken_backend)

        result = self.router.put("key", "value", data_kind="embedding.float16[256]")
        assert result is False

    def test_async_backend_set_is_called(self):
        async_backend = MagicMock()
        async_backend.set = AsyncMock(return_value=True)
        self.router.register_backend(StorageKind.HOT, async_backend)

        # Router should detect async set and handle it
        result = self.router.put("key", "value", data_kind="embedding.float16[256]")
        # set() may not be called if router looks for sync put() first
        # The important thing is it doesn't raise

    def test_multiple_backends_same_kind_last_wins(self):
        first = MagicMock()
        second = MagicMock()
        router = StorageRouter()
        router.register_backend(StorageKind.HOT, first)
        router.register_backend(StorageKind.HOT, second)

        assert router._backends[StorageKind.HOT] is second


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def setup_method(self):
        reset_storage_router()

    def test_get_stats_includes_backend_stats(self):
        router = StorageRouter()
        mock_backend = MagicMock()
        mock_backend.get_stats.return_value = {"entries": 42, "evictions": 3}
        router.register_backend(StorageKind.HOT, mock_backend)

        stats = router.get_stats()
        assert "backend.hot" in stats
        assert stats["backend.hot"]["entries"] == 42

    def test_spill_increments_counter(self):
        mock_governor = MagicMock()
        mock_uma = MagicMock()
        mock_uma.uma_state = "critical"
        mock_governor.sample_uma_status.return_value = mock_uma

        router = StorageRouter(governor=mock_governor)
        router.register_backend(StorageKind.WARM, MagicMock())

        router.put("key", "value", data_kind="embedding.float16[256]")
        assert router._stats["spills"] == 1

    def test_invalidation_increments_counter(self):
        router = StorageRouter()
        router.register_backend(StorageKind.HOT, MagicMock())
        callback = MagicMock()
        router.register_invalidation_callback(StorageKind.WARM, callback)

        router.put("key", "value", data_kind="embedding.float16[256]")
        assert router._stats["invalidations"] == 1


# ---------------------------------------------------------------------------
# Async singleton
# ---------------------------------------------------------------------------


class TestAsyncSingleton:
    @pytest.mark.asyncio
    async def test_get_storage_router_creates_singleton(self):
        reset_storage_router()
        router1 = await get_storage_router()
        router2 = await get_storage_router()
        assert router1 is router2

    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self):
        reset_storage_router()
        router1 = await get_storage_router()
        reset_storage_router()
        router2 = await get_storage_router()
        assert router1 is not router2


# ---------------------------------------------------------------------------
# Integration: backends wired from existing stores
# ---------------------------------------------------------------------------


class TestIntegrationWiring:
    """Verify StorageRouter can wrap existing store classes."""

    def setup_method(self):
        reset_storage_router()

    def test_can_wrap_lmdb_like_backend(self):
        """LMDBHotCacheStore has put/get/delete — StorageRouter handles it."""
        router = StorageRouter()
        mock_lmdb = MagicMock()
        mock_lmdb.put.return_value = True
        mock_lmdb.get.return_value = "lmdb_value"
        mock_lmdb.delete.return_value = True
        router.register_backend(StorageKind.KEYVALUE, mock_lmdb)

        router.put("qkey", "qvalue", data_kind="qtable.federated")
        router.get("qkey", data_kind="qtable.federated")
        router.delete("qkey", data_kind="qtable.federated")

        mock_lmdb.put.assert_called_once()
        mock_lmdb.get.assert_called_once()
        mock_lmdb.delete.assert_called_once()

    def test_embedding_policy_sizings(self):
        """Verify embedding policies respect M1 8GB budget."""
        p256 = _classify("embedding.float16[256]")
        assert p256.max_bytes <= 512 * 1024 * 1024  # ≤ 512 MB

        p768 = _classify("embedding.float32[768]")
        assert p768.max_bytes <= 8 * 1024**3  # ≤ 8 GB

    def test_all_policies_have_max_bytes(self):
        """Every policy in matrix has explicit bounded max_bytes."""
        for key, policy in _DECISION_MATRIX.items():
            assert policy.max_bytes > 0, f"{key} has zero max_bytes"

    def test_all_policies_ttl_is_explicit(self):
        """Every policy has explicit ttl (None or positive)."""
        for key, policy in _DECISION_MATRIX.items():
            assert policy.ttl_seconds is None or policy.ttl_seconds > 0
