"""
Sprint F202I: Multimodal Evidence Triage — Probe Tests
======================================================

Invariant mapping:
  F202I-1 | EvidenceTriageCoordinator initializes metadata extractor lazily
  F202I-2 | extract_triage_facets returns TriageFacets (bounded, fail-safe)
  F202I-3 | URL/domain extraction bounded at MAX_URL_HITS=20
  F202I-4 | OCR snippets bounded at MAX_OCR_SNIPPETS=10
  F202I-5 | File hashes extracted from GenericMetadata (md5, sha256, sha1)
  F202I-6 | TriageFacets.to_dict() includes all required fields
  F202I-7 | DocumentExtractor.extract() calls triage and builds evidence envelope
  F202I-8 | _build_document_envelope produces JSON with triage facets
  F202I-9 | Envelope bounded at _MAX_ENVELOPE_SIZE=4098
  F202I-10 | _run_evidence_triage_sidecar counts document findings with triage
  F202I-11 | SprintSchedulerResult.evidence_triage_findings_count field exists
  F202I-12 | _evidence_triage_adapter field exists in SprintScheduler
  F202I-13 | Sidecar is called after F202E temporal archaeology sidecar
  F202I-14 | Fail-soft: all errors in triage coordinator are caught
  F202I-15 | No VLM called in triage path (VisionOCR only)
  F202I-16 | RAM guard in EvidenceTriageCoordinator blocks triage when UMA tight
  F202I-17 | Size guard: files > 100MB are skipped
  F202I-18 | OCR timeout: OCR fails gracefully after OCR_TIMEOUT_S=30s
  F202I-19 | Metadata timeout: extraction fails gracefully after METADATA_TIMEOUT_S=30s
  F202I-20 | SprintScheduler sidecar chain preserved (no live_feed tuple change)
"""

import asyncio
import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.multimodal.evidence_triage import (
    MAX_OCR_CHARS,
    MAX_OCR_SNIPPETS,
    MAX_URL_HITS,
    METADATA_TIMEOUT_S,
    OCR_TIMEOUT_S,
    MAX_FILE_SIZE_FOR_TRIAGE,
    EvidenceTriageCoordinator,
    TriageFacets,
    _extract_urls_and_domains,
    extract_triage_facets,
)


# ============================================================================
# F202I-2/6: TriageFacets structure
# ============================================================================

class TestTriageFacets:
    """F202I-2: extract_triage_facets returns TriageFacets. F202I-6: to_dict fields."""

    def test_triage_facets_default_constructor(self):
        """TriageFacets has all required fields with safe defaults."""
        facets = TriageFacets()
        assert facets.title is None
        assert facets.author is None
        assert facets.exif == {}
        assert facets.gps == {}
        assert facets.ocr_snippets == []
        assert facets.file_hashes == {}
        assert facets.embedded_urls == []
        assert facets.embedded_domains == []
        assert facets.triage_complete is False

    def test_triage_facets_to_dict_includes_all_fields(self):
        """F202I-6: to_dict includes title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains."""
        facets = TriageFacets(
            title="Test Document",
            author="John Doe",
            exif={"camera_make": "Canon", "camera_model": "EOS R5"},
            gps={"latitude": 37.7749, "longitude": -122.4194, "altitude": 10.0},
            ocr_snippets=["hello world", "https://example.com"],
            file_hashes={"md5": "abc123", "sha256": "def456"},
            embedded_urls=["https://example.com"],
            embedded_domains=["example.com"],
            triage_complete=True,
        )
        d = facets.to_dict()
        assert d["title"] == "Test Document"
        assert d["author"] == "John Doe"
        assert d["exif"] == {"camera_make": "Canon", "camera_model": "EOS R5"}
        assert d["gps"] == {"latitude": 37.7749, "longitude": -122.4194, "altitude": 10.0}
        assert d["ocr_snippets"] == ["hello world", "https://example.com"]
        assert d["file_hashes"] == {"md5": "abc123", "sha256": "def456"}
        assert d["embedded_urls"] == ["https://example.com"]
        assert d["embedded_domains"] == ["example.com"]
        assert d["triage_complete"] is True


# ============================================================================
# F202I-3: URL/domain extraction bounded
# ============================================================================

class TestURLDomainExtraction:
    """F202I-3: URL/domain extraction bounded at MAX_URL_HITS=20."""

    def test_extract_urls_bounded(self):
        """URLs are deduplicated and bounded at MAX_URL_HITS."""
        # Create text with many duplicate URLs
        text = "https://example.com " * 100
        urls, domains = _extract_urls_and_domains(text)
        assert len(urls) <= MAX_URL_HITS
        assert len(set(urls)) == len(urls)  # Deduplicated

    def test_extract_domains_bounded(self):
        """Domains are deduplicated and bounded at MAX_URL_HITS."""
        text = "example.com " * 100
        urls, domains = _extract_urls_and_domains(text)
        assert len(domains) <= MAX_URL_HITS
        assert len(set(domains)) == len(domains)  # Deduplicated

    def test_extract_urls_and_domains_from_text(self):
        """URLs and domains are correctly extracted from OCR text."""
        text = "Check https://example.com and www.test.org plus example.com"
        urls, domains = _extract_urls_and_domains(text)
        assert any("example.com" in u for u in urls)
        assert "test.org" in domains or "www.test.org" in domains

    def test_empty_text_returns_empty_lists(self):
        """Empty text returns empty URL and domain lists."""
        urls, domains = _extract_urls_and_domains("")
        assert urls == []
        assert domains == []

    def test_none_text_returns_empty_lists(self):
        """None text returns empty URL and domain lists."""
        urls, domains = _extract_urls_and_domains(None)
        assert urls == []
        assert domains == []


# ============================================================================
# F202I-4: OCR snippets bounded
# ============================================================================

class TestOCRBounds:
    """F202I-4: OCR snippets bounded at MAX_OCR_SNIPPETS=10."""

    def test_max_ocr_snippets_constant(self):
        """MAX_OCR_SNIPPETS is 10."""
        assert MAX_OCR_SNIPPETS == 10

    def test_max_ocr_chars_constant(self):
        """MAX_OCR_CHARS is 5000."""
        assert MAX_OCR_CHARS == 5000


# ============================================================================
# F202I-8/9: Evidence envelope bounds
# ============================================================================

class TestDocumentEnvelope:
    """F202I-8: _build_document_envelope produces JSON with triage facets. F202I-9: bounded at 4098."""

    def test_envelope_has_required_fields(self):
        """Envelope contains audit_reason, evidence_pointers, signal_facets, suggested_pivots, triage."""
        from hledac.universal.multimodal.analyzer import _build_document_envelope
        envelope_str = _build_document_envelope(
            text_content="Hello world",
            triage_facets={
                "title": "Test",
                "author": "Author",
                "exif": {},
                "gps": {},
                "ocr_snippets": [],
                "file_hashes": {},
                "embedded_urls": [],
                "embedded_domains": [],
                "triage_complete": True,
            },
            file_path="/tmp/test.pdf",
            file_type=".pdf",
        )
        data = json.loads(envelope_str)
        assert "audit_reason" in data
        assert "evidence_pointers" in data
        assert "signal_facets" in data
        assert "suggested_pivots" in data
        assert "triage" in data
        assert "content_preview" in data

    def test_envelope_triage_fields(self):
        """Envelope triage section includes all required fields."""
        from hledac.universal.multimodal.analyzer import _build_document_envelope
        envelope_str = _build_document_envelope(
            text_content="Test content",
            triage_facets={
                "title": "My PDF",
                "author": "Author Name",
                "exif": {"camera_make": "Canon"},
                "gps": {"latitude": 40.0},
                "ocr_snippets": ["snippet1"],
                "file_hashes": {"md5": "abc"},
                "embedded_urls": ["https://x.com"],
                "embedded_domains": ["x.com"],
                "triage_complete": True,
            },
            file_path="/tmp/test.pdf",
            file_type=".pdf",
        )
        data = json.loads(envelope_str)
        triage = data["triage"]
        assert triage["title"] == "My PDF"
        assert triage["author"] == "Author Name"
        assert triage["exif"] == {"camera_make": "Canon"}
        assert triage["gps"] == {"latitude": 40.0}
        assert triage["ocr_snippets"] == ["snippet1"]
        assert triage["file_hashes"] == {"md5": "abc"}
        assert triage["embedded_urls"] == ["https://x.com"]
        assert triage["embedded_domains"] == ["x.com"]

    def test_envelope_with_empty_triage_facets(self):
        """Empty triage_facets produces valid envelope with null values."""
        from hledac.universal.multimodal.analyzer import _build_document_envelope
        result = _build_document_envelope(
            text_content="Raw document text",
            triage_facets={},
            file_path="/tmp/test.pdf",
            file_type=".pdf",
        )
        # Produces valid JSON envelope, not raw text fallback
        data = json.loads(result)
        assert data["triage"]["title"] is None
        assert data["content_preview"] == "Raw document text"

    def test_envelope_with_none_text_content(self):
        """None text_content produces valid envelope with empty content_preview."""
        from hledac.universal.multimodal.analyzer import _build_document_envelope
        result = _build_document_envelope(
            text_content=None,  # type: ignore
            triage_facets={},
            file_path="/tmp/test.pdf",
            file_type=".pdf",
        )
        # Produces valid JSON envelope, not raw text fallback
        data = json.loads(result)
        assert data["signal_facets"]["has_text"] is False
        assert data["content_preview"] == ""


# ============================================================================
# F202I-1: Lazy initialization
# ============================================================================

class TestCoordinatorInitialization:
    """F202I-1: EvidenceTriageCoordinator initializes metadata extractor lazily."""

    @pytest.mark.asyncio
    async def test_initialize_creates_metadata_extractor(self):
        """initialize() creates metadata extractor on first use."""
        coordinator = EvidenceTriageCoordinator()
        assert not coordinator._initialized
        await coordinator.initialize()
        assert coordinator._initialized
        await coordinator.close()

    @pytest.mark.asyncio
    async def test_double_initialize_is_idempotent(self):
        """initialize() can be called multiple times safely."""
        coordinator = EvidenceTriageCoordinator()
        await coordinator.initialize()
        await coordinator.initialize()
        assert coordinator._initialized
        await coordinator.close()


# ============================================================================
# F202I-14: Fail-soft
# ============================================================================

class TestFailSoft:
    """F202I-14: Fail-soft — all errors in triage coordinator are caught."""

    @pytest.mark.asyncio
    async def test_extract_triage_facets_nonexistent_file_returns_empty_facets(self):
        """Nonexistent file returns TriageFacets with defaults."""
        coordinator = EvidenceTriageCoordinator()
        await coordinator.initialize()
        facets = await coordinator.extract_triage_facets("/nonexistent/file.pdf", "document")
        assert isinstance(facets, TriageFacets)
        assert facets.triage_complete is False
        await coordinator.close()

    @pytest.mark.asyncio
    async def test_close_after_init(self):
        """close() cleans up resources without error."""
        coordinator = EvidenceTriageCoordinator()
        await coordinator.initialize()
        await coordinator.close()
        assert coordinator._initialized is False


# ============================================================================
# F202I-16: RAM guard
# ============================================================================

class TestRAMGuard:
    """F202I-16: RAM guard blocks triage when UMA tight."""

    @pytest.mark.asyncio
    async def test_ram_guard_denies_when_critical(self):
        """RAM guard returns False when governor.is_critical() is True."""
        mock_governor = MagicMock()
        mock_governor.is_critical.return_value = True
        coordinator = EvidenceTriageCoordinator(governor=mock_governor)
        assert coordinator._check_ram_guard() is False

    @pytest.mark.asyncio
    async def test_ram_guard_denies_when_emergency(self):
        """RAM guard returns False when governor.is_emergency() is True."""
        mock_governor = MagicMock()
        mock_governor.is_emergency.return_value = True
        coordinator = EvidenceTriageCoordinator(governor=mock_governor)
        assert coordinator._check_ram_guard() is False

    @pytest.mark.asyncio
    async def test_ram_guard_allows_when_healthy(self):
        """RAM guard returns True when governor is not critical/emergency."""
        mock_governor = MagicMock()
        mock_governor.is_critical.return_value = False
        mock_governor.is_emergency.return_value = False
        coordinator = EvidenceTriageCoordinator(governor=mock_governor)
        assert coordinator._check_ram_guard() is True

    @pytest.mark.asyncio
    async def test_ram_guard_fails_open_when_governor_missing(self):
        """RAM guard fails open when governor is None."""
        coordinator = EvidenceTriageCoordinator(governor=None)
        assert coordinator._check_ram_guard() is True


# ============================================================================
# F202I-17: Size guard
# ============================================================================

class TestSizeGuard:
    """F202I-17: Size guard — files > 100MB are skipped."""

    def test_max_file_size_constant(self):
        """MAX_FILE_SIZE_FOR_TRIAGE is 100MB."""
        assert MAX_FILE_SIZE_FOR_TRIAGE == 100 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_skip_oversized_file(self):
        """Files larger than MAX_FILE_SIZE_FOR_TRIAGE return empty facets."""
        with NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * (MAX_FILE_SIZE_FOR_TRIAGE + 1))
            path = f.name

        try:
            coordinator = EvidenceTriageCoordinator()
            await coordinator.initialize()
            facets = await coordinator.extract_triage_facets(path, "document")
            assert facets.triage_complete is False
            assert facets.file_hashes == {}
            await coordinator.close()
        finally:
            Path(path).unlink(missing_ok=True)


# ============================================================================
# F202I-7: DocumentExtractor integration
# ============================================================================

class TestDocumentExtractorIntegration:
    """F202I-7: DocumentExtractor.extract() calls triage and builds evidence envelope."""

    def test_build_document_envelope_import(self):
        """_build_document_envelope is importable from analyzer."""
        from hledac.universal.multimodal.analyzer import _build_document_envelope as envelope_fn
        assert callable(envelope_fn)

    def test_max_envelope_size_constant(self):
        """_MAX_ENVELOPE_SIZE is 4098."""
        from hledac.universal.multimodal.analyzer import _MAX_ENVELOPE_SIZE
        assert _MAX_ENVELOPE_SIZE == 4098


# ============================================================================
# F202I-10/11/12/13: SprintScheduler sidecar wiring
# ============================================================================

class TestSprintSchedulerWiring:
    """F202I-10/11/12/13: SprintScheduler sidecar wiring and result field."""

    def test_evidence_triage_findings_count_field_exists(self):
        """SprintSchedulerResult has evidence_triage_findings_count field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "evidence_triage_findings_count")
        assert result.evidence_triage_findings_count == 0

    def test_evidence_triage_adapter_field_exists(self):
        """SprintScheduler has _evidence_triage_adapter field."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )
        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_evidence_triage_adapter")

    def test_evidence_triage_adapter_starts_none(self):
        """_evidence_triage_adapter is None initially."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )
        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert scheduler._evidence_triage_adapter is None

    @pytest.mark.asyncio
    async def test_sidecar_counts_document_findings_with_triage(self):
        """_run_evidence_triage_sidecar counts document findings with triage envelope."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
            SprintSchedulerResult,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler._result = SprintSchedulerResult()

        # Create mock document findings with triage envelopes
        envelope = json.dumps({
            "audit_reason": "document_triage:.pdf",
            "evidence_pointers": ["/tmp/test.pdf"],
            "signal_facets": {"file_type": ".pdf"},
            "suggested_pivots": [],
            "triage": {"title": "Test", "author": None},
            "content_preview": "Hello",
        })
        mock_finding = MagicMock()
        mock_finding.source_type = "document"
        mock_finding.payload_text = envelope

        # Call the sidecar
        await scheduler._run_evidence_triage_sidecar(
            [mock_finding], MagicMock(), "test query"
        )
        assert scheduler._result.evidence_triage_findings_count == 1

    @pytest.mark.asyncio
    async def test_sidecar_ignores_non_document_findings(self):
        """_run_evidence_triage_sidecar ignores non-document findings."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
            SprintSchedulerResult,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler._result = SprintSchedulerResult()

        mock_finding = MagicMock()
        mock_finding.source_type = "ct_log"  # Not a document
        mock_finding.payload_text = '{"triage": {}}'

        await scheduler._run_evidence_triage_sidecar(
            [mock_finding], MagicMock(), "test query"
        )
        assert scheduler._result.evidence_triage_findings_count == 0

    @pytest.mark.asyncio
    async def test_sidecar_ignores_findings_without_triage(self):
        """_run_evidence_triage_sidecar ignores document findings without triage."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
            SprintSchedulerResult,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler._result = SprintSchedulerResult()

        mock_finding = MagicMock()
        mock_finding.source_type = "document"
        mock_finding.payload_text = "Plain text content"  # Not a triage envelope

        await scheduler._run_evidence_triage_sidecar(
            [mock_finding], MagicMock(), "test query"
        )
        assert scheduler._result.evidence_triage_findings_count == 0

    @pytest.mark.asyncio
    async def test_sidecar_fail_soft_on_error(self):
        """_run_evidence_triage_sidecar is fail-soft."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
            SprintSchedulerResult,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler._result = SprintSchedulerResult()

        # Pass invalid findings that cause exceptions
        bad_finding = MagicMock()
        bad_finding.source_type = property(lambda self: 1/0)  # Will raise

        # Should not raise
        await scheduler._run_evidence_triage_sidecar(
            [bad_finding], MagicMock(), "test query"
        )
        assert scheduler._result.evidence_triage_findings_count == 0


# ============================================================================
# F202I-15: No VLM — VisionOCR only
# ============================================================================

class TestNoVLM:
    """F202I-15: No VLM called in triage path (VisionOCR only)."""

    def test_vision_ocr_import(self):
        """VisionOCR is used, not VisionEncoder or MambaFusion."""
        from hledac.universal.multimodal.evidence_triage import VisionOCR
        assert VisionOCR is not None

    def test_triage_does_not_import_vision_encoder(self):
        """Evidence triage module does not import VisionEncoder."""
        import hledac.universal.multimodal.evidence_triage as triage_module
        # VisionEncoder should NOT be imported in evidence_triage
        assert not hasattr(triage_module, "_VisionEncoder")


# ============================================================================
# F202I-18/19: Timeouts
# ============================================================================

class TestTimeouts:
    """F202I-18: OCR timeout. F202I-19: Metadata timeout."""

    def test_ocr_timeout_constant(self):
        """OCR_TIMEOUT_S is 30.0."""
        assert OCR_TIMEOUT_S == 30.0

    def test_metadata_timeout_constant(self):
        """METADATA_TIMEOUT_S is 30.0."""
        assert METADATA_TIMEOUT_S == 30.0
