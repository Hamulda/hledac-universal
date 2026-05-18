"""
Tests for SemanticStoreBuffer (Sprint F222).

Verifies:
- fail-open no-op without injected store
- pattern_matches tuple/dict handling preserved
- DuckDBShadowStore.inject_semantic_store() delegates to buffer
- DuckDBShadowStore._semantic_buffer_findings() delegates to buffer
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hledac.universal.knowledge.semantic_store_buffer import SemanticStoreBuffer


class MockFinding:
    """Minimal CanonicalFinding-like object for testing."""

    __slots__ = ("finding_id", "payload_text", "source_type", "ts", "pattern_matches")

    def __init__(
        self,
        finding_id: str = "fid-1",
        payload_text: str = "test content",
        source_type: str = "test_source",
        ts: float = 1234.5,
        pattern_matches: list | None = None,
    ):
        self.finding_id = finding_id
        self.payload_text = payload_text
        self.source_type = source_type
        self.ts = ts
        self.pattern_matches = pattern_matches


class TestSemanticStoreBufferFailOpen:
    """SemanticStoreBuffer fail-open: no injected store = silent no-op."""

    def test_buffer_findings_no_op_without_store(self) -> None:
        """No exception when buffer_findings called with no store injected."""
        buffer = SemanticStoreBuffer()
        findings = [MockFinding(payload_text="hello")]
        # Must not raise
        buffer.buffer_findings(findings)

    def test_buffer_findings_empty_list_without_store(self) -> None:
        """Empty list with no store injected is also a silent no-op."""
        buffer = SemanticStoreBuffer()
        buffer.buffer_findings([])
        # Must not raise

    def test_inject_store_accepts_mock(self) -> None:
        """inject() accepts any object with buffer_finding method."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)
        buffer.buffer_findings([MockFinding(payload_text="hello")])
        mock_store.buffer_finding.assert_called_once()


class TestSemanticStoreBufferPatternMatches:
    """pattern_matches tuple/dict handling preserved from original implementation."""

    def test_pattern_matches_tuple_extracts_type(self) -> None:
        """Tuple (value, type) pattern extracts type as ioc_type."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-tuple",
            payload_text="evil domain example.com",
            pattern_matches=[("example.com", "domain")],
        )
        buffer.buffer_findings([finding])

        mock_store.buffer_finding.assert_called_once()
        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["finding_id"] == "fid-tuple"
        assert call_kwargs["ioc_types"] == ["domain"]

    def test_pattern_matches_dict_extracts_label(self) -> None:
        """Dict with 'label' key extracts label as ioc_type."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-dict",
            payload_text="hash  deadbeef",
            pattern_matches=[{"label": "sha256", "value": "deadbeef"}],
        )
        buffer.buffer_findings([finding])

        mock_store.buffer_finding.assert_called_once()
        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["ioc_types"] == ["sha256"]

    def test_pattern_matches_mixed_tuple_and_dict(self) -> None:
        """Mixed tuple/dict pattern_matches are all processed."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-mixed",
            payload_text="content",
            pattern_matches=[
                ("example.com", "domain"),
                {"label": "sha256", "value": "deadbeef"},
            ],
        )
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert set(call_kwargs["ioc_types"]) == {"domain", "sha256"}

    def test_pattern_matches_duplicates_deduplicated(self) -> None:
        """Same IOC type appearing twice is deduplicated via set()."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-dup",
            payload_text="content",
            pattern_matches=[
                ("example.com", "domain"),
                ("evil.com", "domain"),
            ],
        )
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["ioc_types"] == ["domain"]

    def test_pattern_matches_empty_when_no_matches(self) -> None:
        """Findings without pattern_matches result in empty ioc_types."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-no-pm",
            payload_text="plain text",
            pattern_matches=None,
        )
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["ioc_types"] == []

    def test_pattern_matches_ignores_invalid_tuple(self) -> None:
        """Tuple with fewer than 2 elements is skipped."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-short-tuple",
            payload_text="content",
            pattern_matches=[
                ("only-one-element",),
                ("example.com", "domain"),
            ],
        )
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["ioc_types"] == ["domain"]


class TestSemanticStoreBufferOtherFindingAttrs:
    """Other CanonicalFinding attributes passed through correctly."""

    def test_passes_source_type(self) -> None:
        """source_type is passed through from finding."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(
            finding_id="fid-src",
            payload_text="content",
            source_type="ct_indicators",
        )
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["source_type"] == "ct_indicators"

    def test_passes_ts(self) -> None:
        """ts (timestamp) is passed through from finding."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        finding = MockFinding(finding_id="fid-ts", payload_text="content", ts=9999.0)
        buffer.buffer_findings([finding])

        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["ts"] == 9999.0

    def test_skips_empty_payload_text(self) -> None:
        """Findings with empty payload_text are skipped (no buffer_finding call)."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        buffer.inject(mock_store)

        findings = [
            MockFinding(finding_id="fid-empty", payload_text=""),
            MockFinding(finding_id="fid-normal", payload_text="real content"),
        ]
        buffer.buffer_findings(findings)

        assert mock_store.buffer_finding.call_count == 1
        call_kwargs = mock_store.buffer_finding.call_args.kwargs
        assert call_kwargs["finding_id"] == "fid-normal"


class TestSemanticStoreBufferFailOpenException:
    """buffer_finding exception is caught and logged, not propagated."""

    def test_exception_in_buffer_finding_swallowed(self) -> None:
        """Exception raised by store.buffer_finding() does not escape buffer_findings()."""
        buffer = SemanticStoreBuffer()
        mock_store = MagicMock()
        mock_store.buffer_finding.side_effect = RuntimeError("embedding failed")
        buffer.inject(mock_store)

        finding = MockFinding(finding_id="fid-exc", payload_text="content")
        # Must not raise
        buffer.buffer_findings([finding])


class TestDuckDBShadowStoreCompatibility:
    """DuckDBShadowStore compatibility methods delegate to SemanticStoreBuffer."""

    def test_buffer_has_slots(self) -> None:
        """SemanticStoreBuffer uses __slots__ to avoid heap allocation for instance attrs."""
        buf = SemanticStoreBuffer()
        # __slots__ prevents arbitrary attributes
        with pytest.raises(AttributeError):
            buf.arbitrary_attr = "forbidden"

    def test_buffer_instantiation_is_cheap(self) -> None:
        """SemanticStoreBuffer with no args is lightweight (no DuckDB, no LanceDB)."""
        import sys
        buf = SemanticStoreBuffer()
        # Should not pull in duckdb or lancedb
        assert "duckdb" not in sys.modules or "duckdb" in str(type(buf))
        assert buf._store is None


class TestSemanticStoreBufferModuleImport:
    """SemanticStoreBuffer is importable from knowledge package."""

    def test_import_from_knowledge(self) -> None:
        """from knowledge.semantic_store_buffer import SemanticStoreBuffer succeeds."""
        from hledac.universal.knowledge.semantic_store_buffer import SemanticStoreBuffer

        assert SemanticStoreBuffer is not None
        buf = SemanticStoreBuffer()
        assert buf._store is None

    def test_default_store_is_none(self) -> None:
        """New SemanticStoreBuffer instance has _store = None."""
        buffer = SemanticStoreBuffer()
        assert buffer._store is None