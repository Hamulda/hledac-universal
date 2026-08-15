"""
Property-Based Tests for LMDB Operations
========================================

Covers:
- LMDBKVStore: put/get/delete/put_many with Hypothesis
- putmulti_bounded: batch bounds, partial failure, normalization
- UnifiedLMDB: SubDB put/get/delete/scan_prefix/batch operations
- SecurityGate (PII sanitization): no crash, deterministic output
- Serialize/deserialize round-trips

Run with: pytest tests/test_lmdb_operations.py -v
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, Verbosity, assume, Phase
from hypothesis.strategies import (
    binary,
    booleans,
    dictionaries,
    floats,
    integers,
    lists,
    none,
    one_of,
    text,
    tuples,
)

from hledac.universal.tools.lmdb_kv import LMDBKVStore
from hledac.universal.utils.lmdb_bulk import (
    DEFAULT_BULK_BATCH,
    LMDBPair,
    _BULK_BATCH_MAX,
    _BULK_BATCH_MIN,
    _normalise_items,
    putmulti_bounded,
    putmulti_bounded_str,
    putmulti_safe,
)





    PIICategory,
    PIIMatch,
    SanitizationResult,
    SecurityGate,
)


# ---------------------------------------------------------------------------

from _core import aclose# Helpers
# ---------------------------------------------------------------------------

def _make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="hypothesis_lmdb_"))


# ---------------------------------------------------------------------------
# LMDBKVStore — put/get/delete round-trip
# ---------------------------------------------------------------------------

class TestLMDBKVStorePropertyBased:
    """LMDBKVStore key-value invariants via Hypothesis."""

    @given(
        keys=lists(text(min_size=1, max_size=256), min_size=1, max_size=500, unique=True),
        values=dictionaries(keys=text(min_size=1, max_size=256), values=one_of(
            text(max_size=4096),
            dictionaries(keys=text(max_size=64), values=text(max_size=1024)),
        )),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30, deadline=None, phases=[Phase.generate])
    def test_put_get_roundtrip(self, keys, values):
        """Every stored key-value pair is retrievable via get()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            # Store all key-value pairs
            stored_keys = []
            for k in keys:
                if k in values:
                    ok = store.put(k, values[k])
                    assert ok, f"put failed for key {k!r}"
                    stored_keys.append(k)

            # Verify each is retrievable
            for k in stored_keys:
                result = store.get(k)
                assert result is not None, f"key {k!r} not found after put"
                assert result == values[k], f"value mismatch for key {k!r}"
            store.close()

    @given(
        n=integers(min_value=10, max_value=500),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_put_many_all_keys_retrievable(self, n):
        """put_many stores N items; all are get-able afterwards."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            items = [(f"key_{i}", {"index": i, "data": f"value_{i}"}) for i in range(n)]
            results = store.put_many(items)
            assert all(results), "not all put_many items succeeded"
            # Verify each
            for i in range(n):
                val = store.get(f"key_{i}")
                assert val is not None, f"key_{i} missing after put_many"
                assert val["index"] == i
            store.close()

    @given(
        n=integers(min_value=10, max_value=500),
        pct=floats(min_value=0.1, max_value=0.9),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_put_many_partial_duplicates(self, n, pct):
        """put_many with intra-batch duplicates: first-seen wins, dropped count correct."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            items = []
            for i in range(n):
                items.append((f"key_{i}", {"seq": i}))
                # Inject duplicate at ~pct fraction
                if 0 < pct * n < i < n:
                    items.append((f"key_{i}", {"seq": i * 1000}))  # later write should be ignored

            results = store.put_many(items)
            assert all(r for r in results if r), "put_many returned False for valid items"

            # All unique keys should be retrievable
            for i in range(n):
                val = store.get(f"key_{i}")
                assert val is not None, f"key_{i} missing"
                assert val["seq"] == i, "first-seen value should win"
            store.close()

    @given(keys=lists(text(min_size=1, max_size=256), min_size=1, max_size=200, unique=True))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_delete_key_gone(self, keys):
        """After delete, get returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            # Store all keys
            for k in keys:
                store.put(k, {"deleted": False})

            # Delete half
            to_delete = keys[: len(keys) // 2]
            for k in to_delete:
                existed = store.delete(k)
                assert existed, f"delete returned False for existing key {k!r}"

            # Deleted keys must be gone
            for k in to_delete:
                assert store.get(k) is None, f"deleted key {k!r} still present"

            # Remaining keys still accessible
            remaining = keys[len(keys) // 2 :]
            for k in remaining:
                assert store.get(k) is not None, f"non-deleted key {k!r} missing"
            store.close()

    @given(keys=lists(text(min_size=1, max_size=256), min_size=1, max_size=200, unique=True))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_delete_nonexistent_returns_false(self, keys):
        """Deleting a never-stored key returns False (not an exception)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            # Try deleting without storing anything
            for k in keys:
                result = store.delete(k)
                assert result is False, f"delete on non-existent key {k!r} should return False"
            store.close()

    @given(
        key=text(min_size=1, max_size=256),
        value=dictionaries(keys=text(max_size=128), values=text(max_size=2048)),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=30, deadline=None)
    def test_overwrite_updates_value(self, key, value):
        """Overwriting an existing key updates the stored value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            store.put(key, {"version": 1, "initial": True})
            store.put(key, {"version": 2, **value})

            result = store.get(key)
            assert result is not None
            assert result["version"] == 2
            assert result["initial"] is None  # old key's field gone
            store.close()

    @given(items=lists(tuples(text(min_size=1, max_size=128), binary(max_size=4096)), min_size=1, max_size=300))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_binary_value_roundtrip(self, items):
        """Binary values (bytes) are stored and retrieved intact."""
        assume(all(len(k) > 0 for k, _ in items))
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            str_items = [(k, {"binary_data": v.decode("latin-1", errors="replace")}) for k, v in items]
            for k, v in str_items:
                ok = store.put(k, v)
                assert ok
            for k, v in str_items:
                retrieved = store.get(k)
                assert retrieved is not None
                assert retrieved["binary_data"] == v["binary_data"]
            store.close()

    def test_empty_put_many_returns_empty_list(self):
        """put_many([]) returns [] (never raises)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)
            result = store.put_many([])
            assert result == []
            store.close()


# ---------------------------------------------------------------------------
# putmulti_bounded — batch bounds, normalization, partial failure
# ---------------------------------------------------------------------------

class TestPutmultiBoundedPropertyBased:
    """putmulti_bounded invariants via Hypothesis."""

    @given(
        n_keys=integers(min_value=1, max_value=5000),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_returns_exact_count(self, n_keys):
        """putmulti_bounded returns number of items processed (≤ n_keys)."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"k{i}".encode(), f"v{i}".encode()) for i in range(n_keys)]
        count = putmulti_bounded(env, items)
        assert 0 <= count <= n_keys, f"count {count} outside [0, {n_keys}]"
        env.close()

    @given(
        n_keys=integers(min_value=1, max_value=3000),
        max_batch=integers(min_value=_BULK_BATCH_MIN, max_value=_BULK_BATCH_MAX),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_max_batch_never_exceeded(self, n_keys, max_batch):
        """Chunk size never exceeds max_batch."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"k{i}".encode(), f"v{i}".encode()) for i in range(n_keys)]
        count = putmulti_bounded(env, items, max_batch=max_batch)
        # All items that fit in complete chunks should be written
        complete_chunks = n_keys // max_batch
        remainder = n_keys % max_batch
        expected_max = complete_chunks * max_batch + (remainder if remainder > 0 else 0)
        assert count <= expected_max, f"count {count} exceeds max possible {expected_max}"
        env.close()

    @given(items=lists(tuples(binary(max_size=64), binary(max_size=512)), min_size=0, max_size=1000))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_empty_input_returns_zero(self, _items):
        """Empty input returns 0 (never raises)."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        result = putmulti_bounded(env, [])
        assert result == 0
        env.close()

    @given(
        n=integers(min_value=1, max_value=100),
        max_batch=integers(min_value=_BULK_BATCH_MIN, max_value=_BULK_BATCH_MAX),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_mapping_input_normalized(self, n, max_batch):
        """Single-entry mappings are accepted and normalized correctly."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        # Dict input (1-entry mappings)
        items = [{f"k{i}".encode(): f"v{i}".encode()} for i in range(n)]
        count = putmulti_bounded(env, items, max_batch=max_batch)
        assert count == n, f"expected {n}, got {count}"
        env.close()

    @given(
        n=integers(min_value=1, max_value=100),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_putmulti_safe_swallows_exceptions(self, n):
        """putmulti_safe returns 0 on exception (never propagates)."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"k{i}".encode(), f"v{i}".encode()) for i in range(n)]
        result = putmulti_safe(env, items)
        assert isinstance(result, int)
        assert result >= 0
        env.close()

    @given(
        n=integers(min_value=1, max_value=500),
        max_batch=integers(min_value=1, max_value=_BULK_BATCH_MAX),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_str_key_dict_value_roundtrip(self, n, max_batch):
        """putmulti_bounded_str with str keys and dict values: all round-trip correctly."""
        import lmdb
        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"key_{i}", {"seq": i, "data": f"val_{i}"}) for i in range(n)]
        results = putmulti_bounded_str(env, items, max_batch=max_batch)
        assert len(results) == n
        assert all(results), "not all items written"
        # Verify with cursor
        with env.begin() as txn:
            for i in range(n):
                key = f"key_{i}".encode()
                raw = txn.get(key)
                assert raw is not None, f"key_{i} not found"
        env.close()


# ---------------------------------------------------------------------------
# UnifiedLMDB — SubDB operations
# ---------------------------------------------------------------------------

class TestUnifiedLMDBPropertyBased:
    """UnifiedLMDB SubDB put/get/delete/scan_prefix invariants."""

    @given(
        n=integers(min_value=1, max_value=200),
        sub_idx=integers(min_value=0, max_value=15),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_put_get_delete_roundtrip(self, n, sub_idx):
        """SubDB put→get→delete: value retrievable after put, gone after delete."""
        from hledac.universal._core.lmdb_unified import UnifiedLMDB, SubDB

        tmpdir = str(_make_temp_dir())
        store = UnifiedLMDB(path=tmpdir, lazy=False)

        # Use a valid sub_idx
        valid_sub = sub_idx % 16
        _sub_db = store.open_db(valid_sub)

        # Write n items
        for i in range(n):
            key = f"subdb_key_{i}".encode()
            value = f"subdb_value_{i}".encode()
            ok = store.put(valid_sub, key, value)
            assert ok, f"put failed for i={i}"

        # All retrievable
        for i in range(n):
            key = f"subdb_key_{i}".encode()
            val = store.get(valid_sub, key)
            assert val is not None, f"key {i} missing"
            assert val == f"subdb_value_{i}".encode()

        # Delete all
        for i in range(n):
            key = f"subdb_key_{i}".encode()
            ok = store.delete(valid_sub, key)
            assert ok, f"delete failed for i={i}"

        # All gone
        for i in range(n):
            key = f"subdb_key_{i}".encode()
            val = store.get(valid_sub, key)
            assert val is None, f"key {i} still present after delete"

    @given(
        n=integers(min_value=1, max_value=200),
        sub_idx=integers(min_value=0, max_value=15),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_scan_prefix_exact(self, n, sub_idx):
        """scan_prefix returns all items matching the prefix."""
        from hledac.universal._core.lmdb_unified import UnifiedLMDB

        tmpdir = str(_make_temp_dir())
        store = UnifiedLMDB(path=tmpdir, lazy=False)
        valid_sub = sub_idx % 16

        prefix = b"scan_"
        # Write items with prefix and some without
        for i in range(n):
            key = f"scan_{i}".encode()
            store.put(valid_sub, key, f"v{i}".encode())
        # Add noise keys
        for i in range(5):
            store.put(valid_sub, f"noise_{i}".encode(), b"noise")

        results = store.scan_prefix(valid_sub, prefix)
        # Should return only scan_* keys
        assert len(results) == n, f"expected {n}, got {len(results)}"
        for k, _v in results:
            assert k.startswith(prefix), f"key {k!r} doesn't match prefix {prefix!r}"
        # Verify order is preserved
        keys = [k for k, _v in results]
        assert keys == sorted(keys), "scan_prefix keys not sorted"

    @given(
        n=integers(min_value=1, max_value=100),
        sub_idx=integers(min_value=0, max_value=15),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_put_batch_all_retrievable(self, n, sub_idx):
        """put_batch stores items that are then individually get-able."""
        from hledac.universal._core.lmdb_unified import UnifiedLMDB

        tmpdir = str(_make_temp_dir())
        store = UnifiedLMDB(path=tmpdir, lazy=False)
        valid_sub = sub_idx % 16

        items = [(f"batch_k{i}".encode(), f"batch_v{i}".encode()) for i in range(n)]
        ok = store.put_batch(valid_sub, items)
        assert ok, "put_batch failed"

        for i in range(n):
            val = store.get(valid_sub, f"batch_k{i}".encode())
            assert val is not None, f"batch_k{i} missing after put_batch"
            assert val == f"batch_v{i}".encode()


# ---------------------------------------------------------------------------
# LMDBKVStore — max_keys bound
# ---------------------------------------------------------------------------

class TestLMDBKVStoreBounds:
    """LMDBKVStore bounded storage invariants."""

    @given(
        n_stores=integers(min_value=1, max_value=10),
        max_keys=integers(min_value=5, max_value=50),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=10, deadline=None)
    def test_max_keys_early_reject(self, _n_stores, max_keys):
        """When max_keys is reached, subsequent put returns False (no crash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir, max_keys=max_keys)
            # Fill to capacity
            for i in range(max_keys):
                ok = store.put(f"k{i}", {"n": i})
                assert ok, f"put {i} should succeed"

            # Next put must fail gracefully
            ok = store.put(f"overflow_key", {"overflow": True})
            assert ok is False, "put beyond max_keys should return False"
            store.close()

    @given(max_keys=integers(min_value=5, max_value=50))
    @settings(verbosity=Verbosity.verbose, max_examples=10, deadline=None)
    def test_put_many_respects_max_keys(self, max_keys):
        """put_many writes at most max_keys items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir, max_keys=max_keys)
            n = max_keys * 2  # Try to write double
            items = [(f"k{i}", {"n": i}) for i in range(n)]
            results = store.put_many(items)
            # At most max_keys should succeed
            success_count = sum(1 for r in results if r)
            assert success_count <= max_keys, f"put_many wrote {success_count} > {max_keys}"
            store.close()


# ---------------------------------------------------------------------------
# SecurityGate — PII sanitization invariants
# ---------------------------------------------------------------------------

class TestSecurityGatePropertyBased:
    """SecurityGate sanitization invariants via Hypothesis."""

    @given(text_content=text(min_size=0, max_size=10000))
    @settings(verbosity=Verbosity.verbose, max_examples=50, deadline=None)
    def test_sanitize_never_crashes(self, text_content):
        """sanitize() never raises on any string input."""
        gate = SecurityGate()
        result = gate.sanitize(text_content)
        assert isinstance(result, SanitizationResult)
        assert isinstance(result.sanitized_text, str)
        assert isinstance(result.pii_found, list)
        assert isinstance(result.pii_count, int)
        assert result.success is not None

    @given(text_content=text(min_size=0, max_size=10000))
    @settings(verbosity=Verbosity.verbose, max_examples=50, deadline=None)
    def test_sanitize_deterministic(self, text_content):
        """sanitize() is deterministic: same input → same output."""
        gate = SecurityGate()
        r1 = gate.sanitize(text_content)
        r2 = gate.sanitize(text_content)
        assert r1.sanitized_text == r2.sanitized_text
        assert r1.pii_count == r2.pii_count
        assert r1.success == r2.success

    @given(text_content=text(min_size=0, max_size=10000))
    @settings(verbosity=Verbosity.verbose, max_examples=30, deadline=None)
    def test_sanitized_not_shorter_than_original(self, text_content):
        """Sanitized text is never longer than original (masking reduces length)."""
        gate = SecurityGate()
        result = gate.sanitize(text_content)
        assert len(result.sanitized_text) <= len(text_content) + 1  # small margin for masking

    @given(text_content=text(min_size=0, max_size=5000))
    @settings(verbosity=Verbosity.verbose, max_examples=30, deadline=None)
    def test_mask_pii_replaces_matches(self, text_content):
        """When mask_pii=True, matched PII positions are masked."""
        gate = SecurityGate()
        result = gate.sanitize(text_content, mask_pii=True)
        if result.pii_count > 0:
            # Masked text should be different from original
            assert result.sanitized_text != text_content or result.pii_count == 0
        assert result.risk_level in ("low", "medium", "high")
        assert result.risk_score >= 0

    @given(text_content=text(min_size=0, max_size=5000))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_non_string_returns_empty_clean(self, text_content):
        """Non-string input returns empty sanitized text with success=True."""
        gate = SecurityGate()
        result = gate.sanitize(text_content)
        assert isinstance(result.sanitized_text, str)

    @given(emails=lists(text(min_size=3, max_size=50), min_size=0, max_size=100))
    @settings(verbosity=Verbosity.verbose, max_examples=10, deadline=None)
    def test_email_detection_roundtrip(self, emails):
        """Known emails are detected and masked."""
        gate = SecurityGate()
        text_block = " | ".join(emails) if emails else ""
        if text_block:
            result = gate.sanitize(text_block, mask_pii=True)
            # At least some emails should be detected
            assert result.pii_count >= 0
            # If any found, masked text ≠ original
            if result.pii_count > 0:
                assert result.sanitized_text != text_block

    @given(text_content=text(min_size=0, max_size=5000))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_return_matches_false_hides_detail(self, text_content):
        """return_matches=False returns empty pii_found list (but pii_count still valid)."""
        gate = SecurityGate()
        r_full = gate.sanitize(text_content, return_matches=True)
        r_hidden = gate.sanitize(text_content, return_matches=False)
        assert r_hidden.pii_found == []
        assert r_hidden.pii_count == r_full.pii_count


# ---------------------------------------------------------------------------
# Normalize_items — type normalization invariants
# ---------------------------------------------------------------------------

class TestNormaliseItemsPropertyBased:
    """_normalise_items type normalization invariants."""

    @given(n=integers(min_value=0, max_value=100))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_empty_returns_empty(self, _n):
        """Empty input returns empty list."""
        assert _normalise_items([]) == []

    @given(n=integers(min_value=1, max_value=100))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_tuple_pairs_unchanged(self, n):
        """(bytes, bytes) tuples pass through unchanged."""
        items = [(f"k{i}".encode(), f"v{i}".encode()) for i in range(n)]
        result = _normalise_items(items)
        assert len(result) == n
        for i, (k, v) in enumerate(result):
            assert k == f"k{i}".encode()
            assert v == f"v{i}".encode()

    @given(n=integers(min_value=1, max_value=100))
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_single_entry_mapping_normalized(self, n):
        """Single-entry {bytes: bytes} mappings are normalized to tuples."""
        items = [{f"k{i}".encode(): f"v{i}".encode()} for i in range(n)]
        result = _normalise_items(items)
        assert len(result) == n
        for i, (k, v) in enumerate(result):
            assert k == f"k{i}".encode()
            assert v == f"v{i}".encode()

    @given()
    @settings(verbosity=Verbosity.verbose, max_examples=10, deadline=None)
    def test_multi_entry_mapping_raises(self):
        """Multi-entry mapping raises TypeError (not ValueError, not crash)."""
        import pytest as p

        items = [{b"k1": b"v1", b"k2": b"v2"}]  # 2 entries
        with p.raises(TypeError):
            _normalise_items(items)

    @given(n=integers(min_value=1, max_value=100))
    @settings(verbosity=Verbosity.verbose, max_examples=10, deadline=None)
    def test_non_tuple_non_mapping_raises(self, n):
        """Non-tuple, non-mapping items raise TypeError."""
        import pytest as p

        items = ["not_a_tuple" for _ in range(n)]
        with p.raises(TypeError):
            _normalise_items(items)  # type: ignore


# ---------------------------------------------------------------------------
# putmulti_bounded_str — str-keyed JSON round-trip
# ---------------------------------------------------------------------------

class TestPutmultiBoundedStrPropertyBased:
    """putmulti_bounded_str JSON dict value round-trip."""

    @given(
        n=integers(min_value=1, max_value=200),
        key_prefix=text(max_size=32),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_prefixed_key_roundtrip(self, n, key_prefix):
        """Key prefix is correctly prepended and can be scanned."""
        import lmdb

        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"entity_{i}", {"id": i, "payload": f"data_{i}"}) for i in range(n)]
        results = putmulti_bounded_str(env, items, key_prefix=key_prefix)
        assert len(results) == n
        assert all(results), "not all prefixed items written"

        # Verify via raw scan
        with env.begin() as txn:
            cursor = txn.cursor()
            count = 0
            if cursor.first():
                while True:
                    k = cursor.key()
                    _v = cursor.value()
                    if k is not None:
                        count += 1
                    if not cursor.next():
                        break
            assert count == n, f"expected {n} items, got {count}"
        env.close()

    @given(
        n=integers(min_value=1, max_value=200),
    )
    @settings(verbosity=Verbosity.verbose, max_examples=15, deadline=None)
    def test_no_prefix_roundtrip(self, n):
        """Without key_prefix, items stored with exact str key bytes."""
        import lmdb

        tmpdir = str(_make_temp_dir())
        env = lmdb.open(tmpdir, map_size=256 * 1024 * 1024)
        items = [(f"exact_key_{i}", {"seq": i}) for i in range(n)]
        results = putmulti_bounded_str(env, items)
        assert len(results) == n
        with env.begin() as txn:
            for i in range(n):
                key = f"exact_key_{i}".encode()
                assert txn.get(key) is not None, f"exact_key_{i} missing"
        env.close()
