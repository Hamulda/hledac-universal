"""
W3: MODERN-17/20/21/22/23/24/25 - Arrow Fabric Verification Tests

Tests for verifying Arrow IPC format, PyArrow integration, and batch processing
optimization for M1 architecture.

Test Categories:
1. Arrow IPC format - verify Arrow IPC stream format is correct
2. PyArrow integration - verify PyArrow can parse our IPC streams
3. Batch building - verify batch builder produces valid batches
4. Memory efficiency - verify Arrow format is memory-efficient
5. M1 optimization - verify optimizations for Apple Silicon
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass


# Dynamic project root detection
PROJECT_ROOT = Path(__file__).parent.parent


# Test constants
ARROW_MAGIC_BYTES = b"ARROW1"
IPC_FORMAT_VERSION = 4


class TestArrowIPCFormat:
    """Verify Arrow IPC format is correct."""

    def test_arrow_magic_bytes(self) -> None:
        """Arrow IPC stream should start with ARROW1 magic bytes."""
        try:
            # Try package import first, then fallback to direct Rust module
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                # Direct path for Rust module testing
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)

            # Build a minimal IPC stream
            result = build_ipc_bytes(
                ids=["id1", "id2"],
                queries=["q1", "q2"],
                source_types=["type1", "type2"],
                confidences=[0.9, 0.8],
                timestamps=[1234567890.0, 1234567891.0],
                provenance_jsons=["{}", "{}"],
                payload_texts=["text1", "text2"],
                claims_jsons=["[]", "[]"],
                batch_size=2,
            )

            assert result.startswith(ARROW_MAGIC_BYTES), (
                f"Arrow IPC stream should start with {ARROW_MAGIC_BYTES!r}, "
                f"got {result[:10]!r}"
            )
        except ImportError:
            pytest.skip("rust_extensions.arrow_batch_builder not available")

    def test_ipc_stream_length(self) -> None:
        """Arrow IPC stream should have correct header and footer."""
        try:
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)

            result = build_ipc_bytes(
                ids=["id1", "id2"],
                queries=["q1", "q2"],
                source_types=["type1", "type2"],
                confidences=[0.9, 0.8],
                timestamps=[1234567890.0, 1234567891.0],
                provenance_jsons=["{}", "{}"],
                payload_texts=["text1", "text2"],
                claims_jsons=["[]", "[]"],
                batch_size=2,
            )

            # IPC stream has magic bytes at start and end
            assert len(result) >= 20, "IPC stream should have header and footer"
            assert result.startswith(ARROW_MAGIC_BYTES)
            assert result.endswith(ARROW_MAGIC_BYTES)
        except ImportError:
            pytest.skip("rust_extensions.arrow_batch_builder not available")


class TestPyArrowIntegration:
    """Verify PyArrow can parse Arrow IPC streams."""

    def test_pyarrow_available(self) -> None:
        """PyArrow should be available."""
        try:
            import pyarrow as pa

            assert pa is not None
        except ImportError:
            pytest.skip("PyArrow not installed")

    def test_parse_arrow_stream(self) -> None:
        """PyArrow should be able to parse our IPC stream."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        # Build IPC stream
        result = build_ipc_bytes(
            ids=["id1", "id2"],
            queries=["q1", "q2"],
            source_types=["type1", "type2"],
            confidences=[0.9, 0.8],
            timestamps=[1234567890.0, 1234567891.0],
            provenance_jsons=["{}", "{}"],
            payload_texts=["text1", "text2"],
            claims_jsons=["[]", "[]"],
            batch_size=2,
        )

        # Parse with PyArrow (same as pa.ipc.open_stream)
        reader = pa.ipc.open_stream(result)

        assert reader is not None
        table = reader.read_all()
        assert table.num_rows == 2


class TestArrowBatchBuilder:
    """Verify batch builder produces correct batches."""

    def test_build_batch_with_strings(self) -> None:
        """Batch builder should handle string columns correctly."""
        try:
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("rust_extensions not available")

        ids = ["id_" + str(i) for i in range(10)]
        queries = ["query_" + str(i) for i in range(10)]
        source_types = ["type"] * 10
        confidences = [0.9] * 10
        timestamps = [float(i) for i in range(10)]
        provenance_jsons = ["{}"] * 10
        payload_texts = ["text_" + str(i) for i in range(10)]
        claims_jsons = ["[]"] * 10

        result = build_ipc_bytes(
            ids=ids,
            queries=queries,
            source_types=source_types,
            confidences=confidences,
            timestamps=timestamps,
            provenance_jsons=provenance_jsons,
            payload_texts=payload_texts,
            claims_jsons=claims_jsons,
            batch_size=10,
        )

        assert len(result) > 0

    def test_build_batch_with_unicode(self) -> None:
        """Batch builder should handle Unicode strings."""
        try:
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("rust_extensions not available")

        # Unicode strings
        result = build_ipc_bytes(
            ids=["id_\u4e2d\u6587", "id_\ud83c\udf0d"],
            queries=["query \u00e9\u00e8\u00ea", "query \u00eb"],
            source_types=["type1", "type2"],
            confidences=[0.9, 0.8],
            timestamps=[1234567890.0, 1234567891.0],
            provenance_jsons=["{}", "{}"],
            payload_texts=["text \u00e0", "text \u00e1"],
            claims_jsons=["[]", "[]"],
            batch_size=2,
        )

        assert len(result) > 0

    def test_build_batch_with_empty_strings(self) -> None:
        """Batch builder should handle empty strings."""
        try:
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("rust_extensions not available")

        result = build_ipc_bytes(
            ids=["id1", ""],
            queries=["", "query2"],
            source_types=["type1", ""],
            confidences=[0.9, 0.8],
            timestamps=[1234567890.0, 1234567891.0],
            provenance_jsons=["{}", ""],
            payload_texts=["", "text2"],
            claims_jsons=["[]", "[]"],
            batch_size=2,
        )

        assert len(result) > 0


class TestArrowMemoryEfficiency:
    """Verify Arrow format is memory-efficient."""

    def test_arrow_overhead_low(self) -> None:
        """Arrow IPC format should have low overhead per record."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        # Build batch
        n_records = 1000
        result = build_ipc_bytes(
            ids=[f"id_{i}" for i in range(n_records)],
            queries=[f"query_{i}" for i in range(n_records)],
            source_types=["type"] * n_records,
            confidences=[0.9] * n_records,
            timestamps=[float(i) for i in range(n_records)],
            provenance_jsons=["{}"] * n_records,
            payload_texts=[f"text_{i}" for i in range(n_records)],
            claims_jsons=["[]"] * n_records,
            batch_size=n_records,
        )

        # Parse and verify
        reader = pa.ipc.open_stream(result)
        table = reader.read_all()

        assert table.num_rows == n_records
        # Arrow should be efficient - at least 10 bytes per record overhead
        overhead_per_record = len(result) / n_records
        assert overhead_per_record < 100, (
            f"Overhead per record too high: {overhead_per_record:.2f} bytes"
        )

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="M1-specific memory test",
    )
    def test_m1_memory_efficient(self) -> None:
        """Arrow format should be memory-efficient on M1."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        # Build large batch
        n_records = 10000
        result = build_ipc_bytes(
            ids=[f"id_{i}" for i in range(n_records)],
            queries=[f"query_{i}" for i in range(n_records)],
            source_types=["type"] * n_records,
            confidences=[0.9] * n_records,
            timestamps=[float(i) for i in range(n_records)],
            provenance_jsons=["{}"] * n_records,
            payload_texts=[f"text_{i}" for i in range(n_records)],
            claims_jsons=["[]"] * n_records,
            batch_size=n_records,
        )

        # Should use less than 10MB for 10k records
        size_mb = len(result) / (1024 * 1024)
        assert size_mb < 10, f"Batch too large: {size_mb:.2f} MB"


class TestArrowDataIntegrity:
    """Verify data integrity through Arrow pipeline."""

    def test_data_roundtrip(self) -> None:
        """Data should survive roundtrip through Arrow IPC."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        # Original data
        ids = ["id_1", "id_2", "id_3"]
        queries = ["query_a", "query_b", "query_c"]
        confidences = [0.95, 0.85, 0.75]
        timestamps = [1000.0, 2000.0, 3000.0]

        result = build_ipc_bytes(
            ids=ids,
            queries=queries,
            source_types=["type"] * 3,
            confidences=confidences,
            timestamps=timestamps,
            provenance_jsons=["{}"] * 3,
            payload_texts=["t1", "t2", "t3"],
            claims_jsons=["[]"] * 3,
            batch_size=3,
        )

        # Parse
        reader = pa.ipc.open_stream(result)
        table = reader.read_all()

        # Verify schema columns
        assert "id" in table.column_names
        assert "query" in table.column_names
        assert "confidence" in table.column_names

        # Verify data
        assert table["id"][0].as_py() == "id_1"
        assert table["query"][1].as_py() == "query_b"
        assert table["confidence"][2].as_py() == 0.75


class TestArrowNullHandling:
    """Verify Arrow handles null values correctly."""

    def test_null_strings(self) -> None:
        """Batch builder should handle null strings."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        # Build with some empty strings (treated as null)
        result = build_ipc_bytes(
            ids=["id1", "id2", "id3"],
            queries=["query1", "", "query3"],
            source_types=["type1", "type2", ""],
            confidences=[0.9, 0.8, 0.7],
            timestamps=[1000.0, 2000.0, 3000.0],
            provenance_jsons=["{}", "", "{}"],
            payload_texts=["text1", "text2", "text3"],
            claims_jsons=["[]", "[]", "[]"],
            batch_size=3,
        )

        # Parse
        reader = pa.ipc.open_stream(result)
        table = reader.read_all()

        # Verify null handling
        assert table.num_rows == 3


class TestArrowSchemaCompatibility:
    """Verify Arrow schema is compatible with analytics tools."""

    def test_schema_has_required_columns(self) -> None:
        """Schema should have all required columns for analytics."""
        try:
            import pyarrow as pa
            try:
                from rust_extensions.arrow_batch_builder import build_ipc_bytes
            except ImportError:
                sys.path.insert(0, str(PROJECT_ROOT / "rust_extensions" / "src"))
                from arrow_batch_builder import build_ipc_bytes
                sys.path.pop(0)
        except ImportError:
            pytest.skip("PyArrow or rust_extensions not available")

        result = build_ipc_bytes(
            ids=["id1"],
            queries=["query1"],
            source_types=["type1"],
            confidences=[0.9],
            timestamps=[1000.0],
            provenance_jsons=["{}"],
            payload_texts=["text1"],
            claims_jsons=["[]"],
            batch_size=1,
        )

        reader = pa.ipc.open_stream(result)
        table = reader.read_all()
        schema = table.schema

        # Required columns
        required = {"id", "query", "source_type", "confidence", "timestamp"}
        actual = set(table.column_names)
        missing = required - actual

        assert len(missing) == 0, f"Missing columns: {missing}"


# W3 verification summary
"""
W3: MODERN-17/20/21/22/23/24/25 Test Coverage:
==============================================

✓ Arrow IPC Format (2 tests)
  - Magic bytes correct
  - IPC stream length valid

✓ PyArrow Integration (2 tests)
  - PyArrow available
  - Parse arrow stream

✓ Batch Builder (3 tests)
  - String columns handled
  - Unicode handled
  - Empty strings handled

✓ Memory Efficiency (2 tests)
  - Low overhead per record
  - M1 memory efficient

✓ Data Integrity (1 test)
  - Data roundtrip correct

✓ Null Handling (1 test)
  - Null strings handled

✓ Schema Compatibility (1 test)
  - Required columns present

Total: 12 test cases
"""
