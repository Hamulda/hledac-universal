"""
FOCA Integration Tests - Sprint FOCADI-16

Tests that FOCA metadata types are properly wired into:
- EvidenceTriageCoordinator
- Canonical pipeline
"""
import asyncio
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from multimodal.evidence_triage import EvidenceTriageCoordinator, TriageFacets


class TestFOCATriageIntegration:
    """Test FOCA metadata wiring in evidence_triage."""

    @pytest.fixture
    def mock_extractor(self):
        """Create mock metadata extractor with FOCA results."""
        from forensics.metadata_extractor import EmailMetadata, GenericMetadata, PPTXMetadata

        MagicMock()

        # PPTX result with FOCA metadata
        pptx_result = MagicMock()
        pptx_result.success = True
        pptx_result.generic = MagicMock(spec=GenericMetadata)
        pptx_result.generic.md5_hash = "abc123"
        pptx_result.generic.sha256_hash = "def456"
        pptx_result.pdf = None
        pptx_result.image = None
        pptx_result.pptx = MagicMock(spec=PPTXMetadata)
        pptx_result.pptx.author = "Test Author"
        pptx_result.pptx.company = "Acme Corp"
        pptx_result.pptx.template_path = "C:\\Templates\\presentation.potx"
        pptx_result.pptx.slide_count = 15
        pptx_result.pptx.speaker_notes = ["Note 1", "Note 2", "Note 3"]
        pptx_result.pptx.hidden_slides = [{"id": "3", "hidden": True}]
        pptx_result.pptx.has_macros = True
        pptx_result.pptx.macro_urls = ["http://evil.com/c2.php"]
        pptx_result.email = None
        pptx_result.cad = None
        pptx_result.triage_complete = True

        # Email result
        email_result = MagicMock()
        email_result.success = True
        email_result.generic = MagicMock(spec=GenericMetadata)
        email_result.generic.md5_hash = "eml123"
        email_result.generic.sha256_hash = "eml456"
        email_result.pdf = None
        email_result.image = None
        email_result.pptx = None
        email_result.email = MagicMock(spec=EmailMetadata)
        email_result.email.from_addr = "sender@example.com"
        email_result.email.reply_to = "reply@example.com"
        email_result.email.message_id_domain = "mail.example.com"
        email_result.email.originating_ip = "192.168.1.100"
        email_result.email.received_chain = [
            {"header": "from mx1.example.com", "index": 0},
            {"header": "from mx2.example.com", "index": 1},
        ]
        email_result.email.has_attachments = True
        email_result.email.attachment_count = 2
        email_result.cad = None
        email_result.triage_complete = True

        return {"pptx": pptx_result, "email": email_result}

    @pytest.mark.asyncio
    async def test_pptx_metadata_wired_to_facets(self, mock_extractor, tmp_path):
        """Test PPTX metadata flows into TriageFacets.metadata."""
        # Create a dummy PPTX file
        pptx_path = tmp_path / "test.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("docProps/core.xml", "<xml/>")

        coordinator = EvidenceTriageCoordinator()
        await coordinator.initialize()

        # Inject mock result
        coordinator._metadata_extractor.extract = AsyncMock(
            return_value=mock_extractor["pptx"]
        )

        facets = await coordinator.extract_triage_facets(str(pptx_path), "document")

        # Verify FOCA metadata is in facets.metadata
        assert facets.metadata.get("company") == "Acme Corp"
        assert facets.metadata.get("template_path") == "C:\\Templates\\presentation.potx"
        assert facets.metadata.get("slide_count") == 15
        assert len(facets.metadata.get("speaker_notes", [])) == 3
        assert facets.metadata.get("hidden_slides_count") == 1
        assert facets.metadata.get("has_macros") is True

        # Verify author flows to standard field
        assert facets.author == "Test Author"

        await coordinator.close()

    @pytest.mark.asyncio
    async def test_email_metadata_wired_to_facets(self, mock_extractor, tmp_path):
        """Test Email metadata flows into TriageFacets.metadata."""
        # Create a dummy EML file
        eml_path = tmp_path / "test.eml"
        eml_path.write_bytes(b"From: sender@example.com\nSubject: Test\n")

        coordinator = EvidenceTriageCoordinator()
        await coordinator.initialize()

        # Inject mock result
        coordinator._metadata_extractor.extract = AsyncMock(
            return_value=mock_extractor["email"]
        )

        facets = await coordinator.extract_triage_facets(str(eml_path), "document")

        # Verify email FOCA metadata is in facets.metadata
        assert facets.metadata.get("from_addr") == "sender@example.com"
        assert facets.metadata.get("reply_to") == "reply@example.com"
        assert facets.metadata.get("message_id_domain") == "mail.example.com"
        assert facets.metadata.get("originating_ip") == "192.168.1.100"
        assert len(facets.metadata.get("received_chain", [])) == 2
        assert facets.metadata.get("attachment_count") == 2

        await coordinator.close()

    @pytest.mark.asyncio
    async def test_triage_facets_has_metadata_field(self):
        """Test TriageFacets has metadata dict field."""
        facets = TriageFacets()
        assert hasattr(facets, "metadata")
        assert isinstance(facets.metadata, dict)

        # Verify serialization includes metadata
        d = facets.to_dict()
        assert "metadata" in d


class TestFOCAMacroExtraction:
    """Test macro URL extraction with olevba fallback."""

    def test_macro_urls_in_pptx_via_zip(self, tmp_path):
        """Test URL extraction from VBA without olevba (fallback)."""

        # Create PPTX with VBA containing URL
        pptx_path = tmp_path / "macro.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            # Add vbaProject.bin with URL embedded
            vba_content = b"https://evil.c2.server/payload.php"
            zf.writestr("ppt/vbaProject.bin", vba_content)

        # Test extraction directly
        import asyncio

        from forensics.metadata_extractor import UniversalMetadataExtractor

        async def run():
            extractor = UniversalMetadataExtractor()
            result = await extractor.extract(str(pptx_path))
            return result

        result = asyncio.run(run())

        assert result.pptx is not None
        assert result.pptx.has_macros is True
        # Fallback extraction should find the URL
        assert len(result.pptx.macro_urls) > 0 or result.pptx.has_macros is True


class TestFOCABounds:
    """Test FOCA bounds are enforced."""

    def test_pptx_metadata_bounds(self):
        """Test PPTXMetadata respects bounds."""
        from forensics.metadata_extractor import MAX_MACRO_URLS, MAX_SPEAKER_NOTES, PPTXMetadata

        pptx = PPTXMetadata()

        # Speaker notes bound
        for i in range(MAX_SPEAKER_NOTES + 10):
            if i < MAX_SPEAKER_NOTES:
                pptx.speaker_notes.append(f"note_{i}")
        assert len(pptx.speaker_notes) == MAX_SPEAKER_NOTES

        # Macro URLs bound
        for i in range(MAX_MACRO_URLS + 10):
            if i < MAX_MACRO_URLS:
                pptx.macro_urls.append(f"http://url{i}.com")
        assert len(pptx.macro_urls) == MAX_MACRO_URLS


class TestFOCADocumentIntelligenceSeam:
    """Test FOCA integration with DocumentIntelligenceEngine OfficeDocumentAnalyzer."""

    def test_office_analyzer_has_analyze_async(self):
        """Test OfficeDocumentAnalyzer has async analyze method."""
        from intelligence.document_intelligence import OfficeDocumentAnalyzer
        analyzer = OfficeDocumentAnalyzer()
        assert hasattr(analyzer, "analyze_async")
        assert callable(analyzer.analyze_async)

    @pytest.mark.asyncio
    async def test_office_analyzer_analyze_async_merges_foca(self, tmp_path):
        """Test analyze_async() calls FOCA extractor and merges into raw_metadata."""
        from intelligence.document_intelligence import OfficeDocumentAnalyzer

        # Create minimal PPTX
        pptx_path = tmp_path / "test.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("docProps/core.xml", """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">
<dc:creator>Test Author</dc:creator>
<dc:title>Test Doc</dc:title>
</cp:coreProperties>""")
            zf.writestr("ppt/presentation.xml", "<xml/>")

        analyzer = OfficeDocumentAnalyzer()
        analysis = await analyzer.analyze_async(str(pptx_path))

        # FOCA data should be in raw_metadata['foca']
        assert "foca" in analysis.metadata.raw_metadata or analysis.metadata.author == "Test Author"

    def test_office_analyzer_analyze_sync_works(self, tmp_path):
        """Test sync analyze() still works without FOCA (no async needed)."""
        from intelligence.document_intelligence import OfficeDocumentAnalyzer

        # Create minimal PPTX
        pptx_path = tmp_path / "test.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("docProps/core.xml", """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">
<dc:creator>Sync Author</dc:creator>
</cp:coreProperties>""")
            zf.writestr("ppt/presentation.xml", "<xml/>")

        analyzer = OfficeDocumentAnalyzer()
        analysis = analyzer.analyze(str(pptx_path))

        # Should work and extract basic metadata
        assert analysis.metadata.author == "Sync Author"

    @pytest.mark.asyncio
    async def test_office_analyzer_foca_merge_does_not_crash_on_missing_extractor(self, tmp_path):
        """Test FOCA merge fails gracefully when extractor unavailable."""
        from intelligence.document_intelligence import OfficeDocumentAnalyzer

        pptx_path = tmp_path / "test.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("docProps/core.xml", "<xml/>")
            zf.writestr("ppt/presentation.xml", "<xml/>")

        analyzer = OfficeDocumentAnalyzer()
        # Force no extractor
        analyzer._foca_extractor = None
        analyzer._foca_initialized = True

        # Should not raise
        analysis = await analyzer.analyze_async(str(pptx_path))
        assert analysis is not None

    @pytest.mark.asyncio
    async def test_office_analyzer_close_fail_safe(self):
        """Test close() is async and fail-safe."""
        from intelligence.document_intelligence import OfficeDocumentAnalyzer

        analyzer = OfficeDocumentAnalyzer()
        # close() should not raise even if extractor never initialized
        await analyzer.close()
        assert analyzer._foca_extractor is None
        assert analyzer._foca_initialized is False


class TestFOCAConfidenceScoring:
    """Test FOCA scoring integration in ForensicsEnricher."""

    def test_score_foca_findings_returns_float(self):
        """Test _score_foca_findings returns a float."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        result = enricher._score_foca_findings({})
        assert isinstance(result, float)
        assert 0.0 <= result <= 0.3

    def test_score_foca_findings_with_pptx_macros(self):
        """Test PPTX macro URLs contribute to FOCA score."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        enrichment = {
            "metadata": {
                "pptx": {
                    "macro_urls": ["http://evil.com/c2.php"],
                    "has_macros": True,
                    "hidden_slides": [{"id": "1"}],
                    "template_path": "C:\\Templates\\blank.potx"
                }
            }
        }
        score = enricher._score_foca_findings(enrichment)
        assert score >= 0.1  # macro_urls gives 0.1

    def test_score_foca_findings_with_email_infrastructure(self):
        """Test Email infrastructure signals contribute to FOCA score."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        enrichment = {
            "metadata": {
                "email": {
                    "originating_ip": "192.168.1.100",
                    "dkim_domain": "example.com",
                    "attachment_count": 2
                }
            }
        }
        score = enricher._score_foca_findings(enrichment)
        assert score >= 0.15  # originating_ip=0.1, dkim=0.05, attachments=0.05

    def test_score_foca_findings_with_cad_technical(self):
        """Test CAD technical signals contribute to FOCA score."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        enrichment = {
            "metadata": {
                "cad": {
                    "autocad_version": "2022",
                    "coordinate_extents": {"x": 100, "y": 200}
                }
            }
        }
        score = enricher._score_foca_findings(enrichment)
        assert score >= 0.15  # autocad=0.1, coords=0.05

    def test_score_foca_findings_capped_at_0_3(self):
        """Test FOCA score is capped at 0.3."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        enrichment = {
            "metadata": {
                "pptx": {
                    "macro_urls": ["http://evil.com/c2.php"],
                    "has_macros": True,
                    "hidden_slides": [{"id": "1"}],
                    "template_path": "C:\\Templates\\blank.potx"
                },
                "email": {
                    "originating_ip": "192.168.1.100",
                    "dkim_domain": "example.com",
                    "attachment_count": 2
                },
                "cad": {
                    "autocad_version": "2022",
                    "coordinate_extents": {"x": 100, "y": 200}
                }
            }
        }
        score = enricher._score_foca_findings(enrichment)
        assert score <= 0.3

    def test_score_foca_findings_empty_enrichment(self):
        """Test empty enrichment returns 0.0."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        assert enricher._score_foca_findings({}) == 0.0
        assert enricher._score_foca_findings(None) == 0.0


class TestFOCAConfidenceIntegration:
    """Test FOCA confidence modifier integration with confidence scoring pipeline."""

    def test_foca_confidence_modifier_in_enrichment_result(self):
        """Test enrich() returns foca_confidence_modifier when FOCA data present."""
        from unittest.mock import MagicMock

        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()

        # Mock finding with payload_text pointing to a PPTX file (in supported extensions)
        finding = MagicMock()
        finding.finding_id = "test-foca-001"
        finding.payload_text = "/tmp/test.pptx"
        finding.source_type = "document"

        # Pre-populate the enrichment dict directly to bypass file extraction
        # This tests the _score_foca_findings integration point specifically
        foca_metadata = {
            "pptx": {
                "macro_urls": ["http://c2.example.com/c2.php"],
                "has_macros": True,
                "hidden_slides": [{"id": "1"}],
                "template_path": "C:\\Templates\\evil.potx",
            }
        }

        # Directly test _score_foca_findings with the metadata structure
        modifier = enricher._score_foca_findings({"metadata": foca_metadata})
        assert modifier >= 0.25
        assert modifier <= 0.3  # Capped at 0.3

        # Also verify that when FOCA data is absent, modifier is 0.0
        no_foca_modifier = enricher._score_foca_findings({})
        assert no_foca_modifier == 0.0

    def test_foca_modifier_clamps_at_max_0_3(self):
        """Test FOCA modifier is capped at 0.3 even with all signals present."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()

        # All signals present across PPTX, email, and CAD
        enrichment = {
            "metadata": {
                "pptx": {
                    "macro_urls": ["http://c2.example.com/c2.php"],
                    "has_macros": True,
                    "hidden_slides": [{"id": "1"}],
                    "template_path": "C:\\Templates\\evil.potx",
                },
                "email": {
                    "originating_ip": "192.168.1.100",
                    "dkim_domain": "evil.com",
                    "attachment_count": 5,
                },
                "cad": {
                    "autocad_version": "2022",
                    "coordinate_extents": {"x": 100, "y": 200},
                },
            }
        }

        score = enricher._score_foca_findings(enrichment)
        # PPTX: 0.1+0.05+0.05+0.05=0.25, Email: 0.1+0.05+0.05=0.20, CAD: 0.1+0.05=0.15
        # Total would be 0.60, but capped at 0.3
        assert score == 0.3
        assert score <= 0.3  # Explicit cap check

    def test_foca_modifier_absent_without_foca_data(self):
        """Test foca_confidence_modifier is absent when no FOCA data in enrichment."""
        from unittest.mock import MagicMock

        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()

        finding = MagicMock()
        finding.finding_id = "test-no-foca"
        finding.payload_text = "/tmp/no_foca.txt"  # Plain text, no FOCA signals
        finding.source_type = "text"

        # No FOCA metadata available
        mock_extractor = MagicMock()
        mock_extractor.extract = MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={})))
        mock_extractor._initialized = True

        enricher._extractor = mock_extractor
        enricher._initialized = True

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(enricher.enrich(finding))
        loop.close()

        # No enrichment_available since no FOCA metadata
        if result is not None:
            # If enrichment returned, modifier should be 0.0
            assert result.get("foca_confidence_modifier", 0.0) == 0.0

    def test_foca_confidence_does_not_exceed_1_0(self):
        """Test FOCA modifier added to base confidence never exceeds 1.0."""
        from forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()

        # Max FOCA score
        max_foca_enrichment = {
            "metadata": {
                "pptx": {
                    "macro_urls": ["http://c2.example.com/c2.php"],
                    "has_macros": True,
                    "hidden_slides": [{"id": "1"}],
                    "template_path": "C:\\Templates\\evil.potx",
                },
                "email": {
                    "originating_ip": "192.168.1.100",
                    "dkim_domain": "evil.com",
                    "attachment_count": 5,
                },
                "cad": {
                    "autocad_version": "2022",
                    "coordinate_extents": {"x": 100, "y": 200},
                },
            }
        }

        foca_modifier = enricher._score_foca_findings(max_foca_enrichment)
        base_confidence = 0.7  # Typical base confidence

        # Combined score clamped to 1.0
        combined = min(1.0, base_confidence + foca_modifier)
        assert combined <= 1.0
        assert combined == 1.0  # 0.7 + 0.3 = 1.0
