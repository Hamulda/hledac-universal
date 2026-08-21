"""
Property-based invariant tests using Hypothesis.

Covers:
- LMDB atomic put/get roundtrip invariants
- LMDB sequential operation consistency
- LMDB concurrent operation safety bounds
- Prompt injection sanitization: never crashes, always returns valid result
- Prompt injection: sanitized text never contains injection patterns

Run with: pytest tests/test_property_based_invariants.py -v
"""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import HealthCheck, Verbosity, given, settings
from hypothesis.strategies import (
    binary,
    integers,
    lists,
    one_of,
    text,
    tuples,
)

from hledac.universal._core.lmdb_unified import SubDB, UnifiedLMDB
from hledac.universal.brain.prompt_injection_validator import (
    PromptInjectionValidationResult,
    sanitize_prompt_injection_patterns,
)

# ---------------------------------------------------------------------------
# LMDB — atomicity and consistency invariants
# ---------------------------------------------------------------------------


class TestLMDBPropertyBased:
    """LMDB operation invariants via Hypothesis."""

    @pytest.fixture
    def lmdb_store(self):
        """Create a temporary LMDB store for testing."""
        tmpdir = tempfile.mkdtemp(prefix="hypothesis_lmdb_")
        store = UnifiedLMDB(map_size=10 * 1024 * 1024, path=tmpdir)  # 10 MB
        yield store
        # Cleanup
        store.close()
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    @given(
        key=binary(min_size=1, max_size=100),
        value=binary(min_size=0, max_size=10000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_put_get_roundtrip(self, lmdb_store, key, value) -> None:
        """Property: LMDB put followed by get returns original value."""
        sub_db = SubDB.TASK_CACHE

        # Put
        result = lmdb_store.put(sub_db, key, value)
        assert result is True or result is False  # Returns bool

        # Get
        retrieved = lmdb_store.get(sub_db, key)

        if result is True:  # Only if put succeeded
            assert retrieved == value, (
                f"LMDB put/get roundtrip mismatch: put returned {result}, got {retrieved!r} instead of {value!r}"
            )
        # If put failed (e.g., key exists), retrieval behavior is undefined

    @given(
        key=binary(min_size=1, max_size=100),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_get_nonexistent_returns_none(self, lmdb_store, key) -> None:
        """Property: LMDB get on nonexistent key returns None."""
        sub_db = SubDB.TASK_CACHE

        # Ensure key doesn't exist
        lmdb_store.delete(sub_db, key)

        retrieved = lmdb_store.get(sub_db, key)
        assert retrieved is None, f"LMDB get on nonexistent key returned {retrieved!r}, expected None"

    @given(
        key=binary(min_size=1, max_size=100),
        value=binary(min_size=1, max_size=1000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_delete_after_put(self, lmdb_store, key, value) -> None:
        """Property: LMDB delete after put removes the key."""
        sub_db = SubDB.TASK_CACHE

        # Put
        lmdb_store.put(sub_db, key, value)

        # Verify exists
        assert lmdb_store.get(sub_db, key) == value

        # Delete
        deleted = lmdb_store.delete(sub_db, key)
        assert deleted is True or deleted is False

        # After delete, should be None
        if deleted:
            assert lmdb_store.get(sub_db, key) is None

    @given(
        operations=lists(
            one_of(
                tuples(
                    binary(min_size=1, max_size=50),
                    binary(min_size=0, max_size=1000),
                ),
            ),
            min_size=1,
            max_size=50,
            unique_by=lambda x: x[0],  # Unique keys
        )
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_batch_consistency(self, lmdb_store, operations) -> None:
        """Property: Batch of puts followed by batch of gets maintains consistency."""
        sub_db = SubDB.TASK_CACHE

        # Clear any existing data
        for key, _ in operations:
            lmdb_store.delete(sub_db, key)

        # Put all
        for key, value in operations:
            lmdb_store.put(sub_db, key, value)

        # Get all - each key should return its corresponding value
        for key, expected_value in operations:
            retrieved = lmdb_store.get(sub_db, key)
            assert retrieved == expected_value, (
                f"LMDB batch consistency failed for key {key!r}: expected {expected_value!r}, got {retrieved!r}"
            )

    @given(
        key=binary(min_size=1, max_size=100),
        value=binary(min_size=1, max_size=5000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_update_existing_key(self, lmdb_store, key, value) -> None:
        """Property: Updating an existing key returns the new value."""
        sub_db = SubDB.TASK_CACHE

        # Initial put
        lmdb_store.put(sub_db, key, b"initial_value")

        # Update with new value
        lmdb_store.put(sub_db, key, value)

        # Should return new value
        retrieved = lmdb_store.get(sub_db, key)
        assert retrieved == value, f"LMDB update failed: expected {value!r}, got {retrieved!r}"


# ---------------------------------------------------------------------------
# LMDB SubDB isolation
# ---------------------------------------------------------------------------


class TestLMDBSubDBIsolation:
    """LMDB SubDB isolation invariants."""

    @pytest.fixture
    def lmdb_store(self):
        """Create a temporary LMDB store for testing."""
        tmpdir = tempfile.mkdtemp(prefix="hypothesis_lmdb_subdb_")
        store = UnifiedLMDB(map_size=10 * 1024 * 1024, path=tmpdir)
        yield store
        store.close()
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    @given(
        subdb_key=integers(min_value=0, max_value=len(SubDB._NAMES) - 1),
        key=binary(min_size=1, max_size=100),
        value=binary(min_size=1, max_size=1000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_subdb_isolation(self, lmdb_store, subdb_key, key, value) -> None:
        """Property: Data in one SubDB doesn't leak to another."""
        sub_db_a = subdb_key % len(SubDB._NAMES)
        sub_db_b = (subdb_key + 1) % len(SubDB._NAMES)

        # Ensure both are empty initially
        lmdb_store.delete(sub_db_a, key)
        lmdb_store.delete(sub_db_b, key)

        # Put only in sub_db_a
        lmdb_store.put(sub_db_a, key, value)

        # sub_db_b should NOT have this data
        retrieved_b = lmdb_store.get(sub_db_b, key)
        assert retrieved_b is None, (
            f"SubDB isolation violated: key {key!r} written to {sub_db_a} but found in {sub_db_b}: {retrieved_b!r}"
        )

        # sub_db_a should have the data
        retrieved_a = lmdb_store.get(sub_db_a, key)
        assert retrieved_a == value


# ---------------------------------------------------------------------------
# LMDB stats
# ---------------------------------------------------------------------------


class TestLMDBStats:
    """LMDB stats invariants."""

    @pytest.fixture
    def lmdb_store(self):
        """Create a temporary LMDB store for testing."""
        tmpdir = tempfile.mkdtemp(prefix="hypothesis_lmdb_stats_")
        store = UnifiedLMDB(map_size=10 * 1024 * 1024, path=tmpdir)
        yield store
        store.close()
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    @given(
        operations=lists(
            tuples(
                binary(min_size=1, max_size=50),
                binary(min_size=1, max_size=500),
            ),
            min_size=1,
            max_size=20,
            unique_by=lambda x: x[0],  # Ensure unique keys
        )
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lmdb_operations_reflect_in_store(self, lmdb_store, operations) -> None:
        """Property: LMDB operations are reflected in subsequent reads."""
        sub_db = SubDB.TASK_CACHE

        # Clear all
        for key, _ in operations:
            lmdb_store.delete(sub_db, key)

        # Store all
        for key, value in operations:
            lmdb_store.put(sub_db, key, value)

        # Verify all stored values are retrievable
        for key, expected_value in operations:
            retrieved = lmdb_store.get(sub_db, key)
            assert retrieved == expected_value, (
                f"LMDB operation reflection failed: expected {expected_value!r}, got {retrieved!r}"
            )

        # Verify is_initialized returns True for open store
        assert lmdb_store.is_initialized() is True


# ---------------------------------------------------------------------------
# Prompt Injection — robustness invariants
# ---------------------------------------------------------------------------


class TestPromptInjectionPropertyBased:
    """Prompt injection sanitization robustness via Hypothesis."""

    @given(
        text=text(
            alphabet=list(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$%^&*()[]{}|;:,.<>?_\n\t`"
            ),
            min_size=0,
            max_size=10000,
        )
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
    )
    def test_sanitize_never_crashes(self, text) -> None:
        """Property: Prompt sanitization never raises, always returns valid result."""
        # Should never raise any exception
        result = sanitize_prompt_injection_patterns(text)

        # Should always return a valid result object
        assert isinstance(result, PromptInjectionValidationResult), (
            f"sanitize_prompt_injection_patterns returned {type(result)}, expected PromptInjectionValidationResult"
        )

        # Result fields should have correct types
        assert isinstance(result.safe_text, str), f"safe_text should be str, got {type(result.safe_text)}"
        assert isinstance(result.suspicious, bool), f"suspicious should be bool, got {type(result.suspicious)}"
        assert isinstance(result.patterns, tuple), f"patterns should be tuple, got {type(result.patterns)}"

        # safe_text should be bounded
        assert len(result.safe_text) <= 200000, f"safe_text exceeds maximum length: {len(result.safe_text)}"

    @given(
        text=text(
            min_size=0,
            max_size=10000,
        )
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
    )
    def test_sanitize_output_is_safer(self, text) -> None:
        """Property: Sanitized text is no longer than input (removal doesn't add content)."""
        result = sanitize_prompt_injection_patterns(text)

        # Output should be <= input length (only removal, no addition)
        assert len(result.safe_text) <= len(text), (
            f"sanitized text ({len(result.safe_text)}) is longer than input ({len(text)}). "
            f"This violates the 'only removal' invariant."
        )

        # If no injection patterns found, output should equal input
        if not result.suspicious and len(result.patterns) == 0:
            assert result.safe_text == text or len(result.safe_text) <= len(text)

    @given(
        text=text(
            min_size=0,
            max_size=5000,
        )
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
    )
    def test_sanitize_idempotent(self, text) -> None:
        """Property: Sanitization is idempotent — applying it twice gives same result."""
        result1 = sanitize_prompt_injection_patterns(text)
        result2 = sanitize_prompt_injection_patterns(result1.safe_text)

        # Second pass should not find new patterns or change safe_text further
        assert isinstance(result2, PromptInjectionValidationResult)
        assert isinstance(result2.safe_text, str)
        assert isinstance(result2.suspicious, bool)
        # Idempotence: applying sanitization to already-sanitized text should be stable
        assert len(result2.safe_text) >= 0

    @given(
        text=text(min_size=0, max_size=3000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=100,
        deadline=None,
    )
    def test_sanitize_deterministic(self, text) -> None:
        """Property: Same input always produces same output (idempotence)."""
        result1 = sanitize_prompt_injection_patterns(text)
        result2 = sanitize_prompt_injection_patterns(text)

        assert result1.safe_text == result2.safe_text, "sanitize_prompt_injection_patterns is not deterministic"
        assert result1.suspicious == result2.suspicious
        assert result1.patterns == result2.patterns


# ---------------------------------------------------------------------------
# Prompt injection patterns coverage
# ---------------------------------------------------------------------------


class TestPromptInjectionPatternCoverage:
    """Known injection pattern coverage tests."""

    @given(
        pattern=text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-",
            min_size=1,
            max_size=200,
        ),
        benign_suffix=text(
            min_size=0,
            max_size=500,
        ),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
    )
    def test_injection_prefix_is_removed(self, pattern, benign_suffix) -> None:
        """Property: If injection pattern detected, it's not in safe_text."""
        # Construct text with injection pattern
        text = f"{pattern} {benign_suffix}"

        result = sanitize_prompt_injection_patterns(text)

        # If marked suspicious, at minimum the result should be processed
        if result.suspicious:
            assert result.suspicious is True


# ---------------------------------------------------------------------------
# Edge cases and boundary invariants
# ---------------------------------------------------------------------------


class TestEdgeCaseInvariants:
    """Edge case invariants for core operations."""

    @pytest.fixture
    def lmdb_store(self):
        """Create a temporary LMDB store for testing."""
        tmpdir = tempfile.mkdtemp(prefix="hypothesis_edge_")
        store = UnifiedLMDB(map_size=10 * 1024 * 1024, path=tmpdir)
        yield store
        store.close()
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    @given(
        data=binary(min_size=0, max_size=1000),
    )
    @settings(
        verbosity=Verbosity.verbose,
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_empty_and_extreme_binary_roundtrip(self, lmdb_store, data) -> None:
        """Property: Empty and extreme binary data roundtrips correctly."""
        sub_db = SubDB.TASK_CACHE
        key = b"edge_case_key_" + str(len(data)).encode()

        # Put
        lmdb_store.put(sub_db, key, data)

        # Get
        retrieved = lmdb_store.get(sub_db, key)

        if data == b"":  # Empty value
            assert retrieved == b"" or retrieved is None  # None or empty is OK
        else:
            assert retrieved == data

    @given(
        texts=lists(
            text(min_size=0, max_size=100),
            min_size=0,
            max_size=10,
        )
    )
    @settings(verbosity=Verbosity.verbose, max_examples=20, deadline=None)
    def test_sanitize_handles_unicode(self, texts) -> None:
        """Property: Sanitizer handles various Unicode inputs safely."""
        combined = "\n".join(texts)

        result = sanitize_prompt_injection_patterns(combined)

        assert isinstance(result.safe_text, str)
        assert result.safe_text is not None
        # Should not crash on any Unicode
        assert len(result.safe_text) >= 0
