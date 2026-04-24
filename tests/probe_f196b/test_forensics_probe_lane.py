"""
Sprint F196B: Forensics Enrichment Probe Lane
==============================================

Tests the fail-soft behavior, lifecycle, supported-file gating, and counter
contracts of the forensics enrichment layer introduced in Sprint F195C.

Covers:
- forensics/enrichment_service.py: ForensicsEnricher
- runtime/sprint_scheduler.py: _enrich_ct_findings_forensics, _init_forensics,
  _close_forensics, forensics_enriched_ct_findings counter
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from hledac.universal.forensics.enrichment_service import (
    ForensicsEnricher,
    _SUPPORTED_EXTENSIONS,
    _extract_file_path_from_payload,
    _file_has_forensics_support,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeFinding:
    """Minimal CanonicalFinding-like object for testing."""
    def __init__(self, finding_id: str, payload_text: str | None, source_type: str = "test"):
        self.finding_id = finding_id
        self.payload_text = payload_text
        self.source_type = source_type


def _make_temp_file(suffix: str, content: bytes = b"fake content") -> Path:
    """Create a temp file with the given extension."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    import os
    os.write(fd, content)
    os.close(fd)
    return Path(path)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-1: Fail-soft invariants — enrich() never raises
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsEnricherFailSoft:
    """
    ForensicsEnricher.enrich() must never raise.
    All exceptions are caught and return None.
    """

    @pytest.fixture
    def enricher(self):
        return ForensicsEnricher()

    @pytest.mark.asyncio
    async def test_enrich_none_finding_is_none(self, enricher):
        """enrich(None) returns None without raising."""
        result = await enricher.enrich(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_no_payload_text_is_none(self, enricher):
        """enrich(finding with no payload_text) returns None without raising."""
        finding = _FakeFinding("fid1", None)
        result = await enricher.enrich(finding)
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_unsupported_extension_is_none(self, enricher):
        """enrich(finding with unsupported extension) returns None without raising."""
        path = _make_temp_file(".xyz_unknown_ext")
        try:
            finding = _FakeFinding("fid1", str(path))
            result = await enricher.enrich(finding)
            assert result is None
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_enrich_file_not_found_is_none(self, enricher):
        """enrich(finding with non-existent file path) returns None without raising."""
        finding = _FakeFinding("fid1", "/tmp/this_file_does_not_exist_12345.jpg")
        result = await enricher.enrich(finding)
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_extractor_raises_returns_none(self, enricher):
        """enrich() returns None when extractor.extract() raises — never propagates."""
        path = _make_temp_file(".jpg", b"\xff\xd8\xff\xe0 fake jpeg")
        try:
            await enricher.initialize()

            # Force the extractor to raise
            original_extract = enricher._extractor.extract
            enricher._extractor.extract = AsyncMock(side_effect=RuntimeError("inject failure"))

            finding = _FakeFinding("fid1", str(path))
            result = await enricher.enrich(finding)

            assert result is None
        finally:
            await enricher.close()
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_enrich_batch_never_raises(self, enricher):
        """enrich_batch() never raises — all failures are silent."""
        findings = [
            _FakeFinding("fid1", None),
            _FakeFinding("fid2", "/nonexistent/path.pdf"),
            _FakeFinding("fid3", None),
        ]
        # Must not raise
        result = await enricher.enrich_batch(findings)
        # Returns a dict (empty when all fail)
        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-2: Lifecycle — initialize / close are idempotent and clean
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsEnricherLifecycle:
    """ForensicsEnricher lifecycle: initialize(), close() are idempotent."""

    @pytest.mark.asyncio
    async def test_init_idempotent(self):
        """initialize() can be called multiple times without error."""
        enricher = ForensicsEnricher()
        await enricher.initialize()
        await enricher.initialize()
        await enricher.initialize()
        assert enricher._initialized is True
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_after_init(self):
        """close() after initialize() sets _initialized = False."""
        enricher = ForensicsEnricher()
        await enricher.initialize()
        assert enricher._initialized is True
        await enricher.close()
        assert enricher._initialized is False

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        """close() can be called multiple times without error."""
        enricher = ForensicsEnricher()
        await enricher.initialize()
        await enricher.close()
        await enricher.close()
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_without_init(self):
        """close() without prior initialize() does not raise."""
        enricher = ForensicsEnricher()
        await enricher.close()


# ─────────────────────────────────────────────────────────────────────────────
# F196B-3: Supported-file gating
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsFileGating:
    """Only files with known forensics-supportable extensions pass gating."""

    def test_supported_extensions_not_empty(self):
        """_SUPPORTED_EXTENSIONS must not be empty."""
        assert len(_SUPPORTED_EXTENSIONS) > 0

    def test_supported_extensions_lowercase(self):
        """All entries in _SUPPORTED_EXTENSIONS are lowercase."""
        for ext in _SUPPORTED_EXTENSIONS:
            assert ext == ext.lower(), f"{ext} is not lowercase"

    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".jpg", True),
            (".jpeg", True),
            (".png", True),
            (".pdf", True),
            (".docx", True),
            (".mp3", True),
            (".mp4", True),
            (".zip", True),
            (".tar", True),
            (".xyz", False),
            (".txt", False),
            (".html", False),
            ("", False),
            (".JPG", True),  # Path.suffix.lower() converts to .jpg
        ],
    )
    def test_file_has_forensics_support(self, ext, expected):
        result = _file_has_forensics_support(f"/tmp/fakefile{ext}")
        assert result is expected, f"extension {ext}: expected {expected}, got {result}"

    def test_extract_file_path_from_payload_direct(self):
        """_extract_file_path_from_payload handles direct absolute paths."""
        path = _make_temp_file(".pdf")
        try:
            result = _extract_file_path_from_payload(str(path))
            assert result == str(path)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_file_path_from_payload_file_url(self):
        """_extract_file_path_from_payload handles file:// URLs."""
        path = _make_temp_file(".jpg")
        try:
            result = _extract_file_path_from_payload(f"file://{path}")
            assert result == str(path)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_file_path_from_payload_none(self):
        """_extract_file_path_from_payload returns None for None."""
        assert _extract_file_path_from_payload(None) is None

    def test_extract_file_path_from_payload_empty(self):
        """_extract_file_path_from_payload returns None for empty string."""
        assert _extract_file_path_from_payload("") is None

    def test_extract_file_path_from_payload_nonexistent(self):
        """_extract_file_path_from_payload returns None for non-existent paths."""
        assert _extract_file_path_from_payload("/tmp/does_not_exist_12345.pdf") is None


# ─────────────────────────────────────────────────────────────────────────────
# F196B-4: Return dict contract — keys are always present
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsReturnContract:
    """enrich() always returns a dict with the documented keys when not None."""

    @pytest.mark.asyncio
    async def test_return_dict_keys_present(self):
        """When enrich() returns non-None, all documented keys are present."""
        enricher = ForensicsEnricher()

        # Create a supported file that will be processed
        path = _make_temp_file(".pdf", b"%PDF-1.4 fake")

        # Patch extractor to return a fake result
        class _FakeResult:
            def to_dict(self):
                return {"title": "test", "author": "test"}

        await enricher.initialize()
        if enricher._extractor is not None:
            enricher._extractor.extract = AsyncMock(return_value=_FakeResult())

        finding = _FakeFinding("contract_fid", str(path))
        result = await enricher.enrich(finding)

        await enricher.close()
        path.unlink(missing_ok=True)

        # If result is not None, it must have all expected keys
        if result is not None:
            expected_keys = {
                "finding_id",
                "file_path",
                "metadata",
                "steganography",
                "ghosts",
                "enrichment_available",
            }
            assert expected_keys.issubset(result.keys()), (
                f"Missing keys: {expected_keys - result.keys()}"
            )
            assert isinstance(result["enrichment_available"], bool)

    @pytest.mark.asyncio
    async def test_enrich_batch_returns_dict(self):
        """enrich_batch() always returns a dict."""
        enricher = ForensicsEnricher()
        result = await enricher.enrich_batch([])
        assert isinstance(result, dict)
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# F196B-5: SprintScheduler forensics counter contracts
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsSchedulerCounter:
    """SprintSchedulerResult.forensics_enriched_ct_findings is a non-negative int."""

    def test_result_has_forensics_counter_field(self):
        """SprintSchedulerResult must have forensics_enriched_ct_findings field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        assert hasattr(result, "forensics_enriched_ct_findings")
        assert isinstance(result.forensics_enriched_ct_findings, int)
        assert result.forensics_enriched_ct_findings >= 0

    def test_forensics_counter_accepts_value(self):
        """forensics_enriched_ct_findings can be set to a positive value."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        result.forensics_enriched_ct_findings = 2
        assert result.forensics_enriched_ct_findings == 2


# ─────────────────────────────────────────────────────────────────────────────
# F196B-6: LMDB enrichment storage path is additive (not in finding.metadata)
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsStorageIsAdditive:
    """Enrichment data goes to LMDB keyed by finding_id — never mutates finding."""

    @pytest.mark.asyncio
    async def test_enrich_does_not_mutate_finding(self):
        """enrich() must not modify the input finding object."""
        enricher = ForensicsEnricher()
        path = _make_temp_file(".png", b"\x89PNG\r\n\x1a\n fake png")
        try:
            await enricher.initialize()

            finding = _FakeFinding("mutate_test", str(path))
            finding_copy = vars(finding).copy()

            await enricher.enrich(finding)

            # Finding object must not be mutated
            assert vars(finding) == finding_copy, "enrich() must not mutate the finding object"
        finally:
            await enricher.close()
            path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-7: Lazy-loading — forensics modules don't load until first use
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsLazyLoading:
    """Forensics modules are lazy-loaded inside enrichment methods."""

    def test_supported_extensions_available_without_import(self):
        """_SUPPORTED_EXTENSIONS is available without triggering forensics imports."""
        assert ".jpg" in _SUPPORTED_EXTENSIONS
        assert ".pdf" in _SUPPORTED_EXTENSIONS

    @pytest.mark.asyncio
    async def test_enrich_without_init_works(self):
        """enrich() calls _ensure_initialized() automatically if not initialized."""
        enricher = ForensicsEnricher()
        finding = _FakeFinding("lazy_test", "/nonexistent/file.pdf")
        # Must not raise even without explicit initialize()
        result = await enricher.enrich(finding)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# F196B-8: Concurrency — enrich_batch uses bounded semaphore
# ─────────────────────────────────────────────────────────────────────────────

class TestForensicsConcurrency:
    """enrich_batch uses a semaphore to cap concurrent enrichments."""

    @pytest.mark.asyncio
    async def test_batch_semaphore_limit(self):
        """enrich_batch limits concurrent executions to 3 (M1 8GB safe)."""
        enricher = ForensicsEnricher()
        await enricher.initialize()

        import inspect
        source = inspect.getsource(enricher.enrich_batch)
        assert "Semaphore(3)" in source, "enrich_batch must use Semaphore(3) for M1 8GB safety"

        await enricher.close()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
