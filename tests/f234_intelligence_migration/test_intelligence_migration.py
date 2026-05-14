"""
Sprint F234: Intelligence Migration Tests
=========================================

Tests that verify:
1. Import availability of migrated modules
2. Export availability of required functions
3. Pipeline import smoke (live_public_pipeline loads without ImportError)
4. Fail-soft behavior of academic_discovery and pastebin_monitor
5. No parent intelligence/ imports in canonical runtime

Run with: pytest hledac/universal/tests/probe_f234_intelligence_migration/ -v
"""

from unittest.mock import patch

import pytest


class TestImportAvailability:
    """Phase 4.1: Import availability smoke tests."""

    def test_academic_discovery_import(self):
        """academic_discovery module is importable from canonical path."""
        from hledac.universal.intelligence import academic_discovery
        assert academic_discovery is not None

    def test_pastebin_monitor_import(self):
        """pastebin_monitor module is importable from canonical path."""
        from hledac.universal.intelligence import pastebin_monitor
        assert pastebin_monitor is not None

    def test_academic_discovery_exports(self):
        """academic_discovery exports search_academic_all."""
        from hledac.universal.intelligence.academic_discovery import search_academic_all
        assert callable(search_academic_all)

    def test_pastebin_monitor_exports(self):
        """pastebin_monitor exports run function."""
        from hledac.universal.intelligence.pastebin_monitor import run
        assert callable(run)

    def test_pasteFinding_dataclass(self):
        """pastebin_monitor exports PasteFinding dataclass."""
        from hledac.universal.intelligence.pastebin_monitor import PasteFinding
        pf = PasteFinding(
            uri="https://example.com",
            source="test",
            extracted_secrets=["secret"],
            emails=[],
            ip_addresses=[],
            context_snippet="test",
        )
        assert pf.uri == "https://example.com"
        assert pf.masked_secrets() == ["se****"]  # Last 4 chars masked


class TestIntelligenceInitExports:
    """Phase 4.1: __init__.py exports the new modules."""

    def test_academic_discovery_available_flag(self):
        """ACADEMIC_DISCOVERY_AVAILABLE flag exists."""
        from hledac.universal.intelligence import ACADEMIC_DISCOVERY_AVAILABLE
        assert isinstance(ACADEMIC_DISCOVERY_AVAILABLE, bool)

    def test_pastebin_monitor_available_flag(self):
        """PASTEBIN_MONITOR_AVAILABLE flag exists."""
        from hledac.universal.intelligence import PASTEBIN_MONITOR_AVAILABLE
        assert isinstance(PASTEBIN_MONITOR_AVAILABLE, bool)

    def test_pasteFinding_in_exports(self):
        """PasteFinding is in __all__."""
        from hledac.universal.intelligence import PasteFinding
        assert PasteFinding is not None

    def test_pastebin_run_alias(self):
        """pastebin_run is exported from __init__."""
        from hledac.universal.intelligence import pastebin_run
        assert callable(pastebin_run)


class TestFailSoftAcademicDiscovery:
    """Phase 4.4: academic_discovery fail-soft behavior."""

    @pytest.mark.asyncio
    async def test_search_arxiv_returns_empty_on_exception(self):
        """search_arxiv returns [] when AcademicSearchEngine fails."""
        from hledac.universal.intelligence.academic_discovery import search_arxiv

        with patch(
            'hledac.universal.intelligence.academic_discovery._get_academic_search_engine'
        ) as mock_engine:
            mock_engine.side_effect = Exception("Simulated failure")
            result = await search_arxiv("test query")
            assert result == []

    @pytest.mark.asyncio
    async def test_search_academic_all_returns_structured_on_exception(self):
        """search_academic_all returns dict with empty lists on per-source failure."""
        from hledac.universal.intelligence.academic_discovery import search_academic_all

        with patch(
            'hledac.universal.intelligence.academic_discovery.search_arxiv',
            side_effect=Exception("Simulated failure")
        ), patch(
            'hledac.universal.intelligence.academic_discovery.search_crossref',
            side_effect=Exception("Simulated failure")
        ), patch(
            'hledac.universal.intelligence.academic_discovery.search_semantic_scholar',
            side_effect=Exception("Simulated failure")
        ):
            result = await search_academic_all("test query")
            assert isinstance(result, dict)
            assert "arxiv" in result
            assert "crossref" in result
            assert "semantic_scholar" in result
            assert result["arxiv"] == []
            assert result["crossref"] == []
            assert result["semantic_scholar"] == []


class TestFailSoftPastebinMonitor:
    """Phase 4.4: pastebin_monitor fail-soft behavior."""

    @pytest.mark.asyncio
    async def test_run_returns_empty_on_circuit_open(self):
        """run() returns [] when circuit breaker is open."""
        import time
        from hledac.universal.intelligence.pastebin_monitor import run, _circuit

        # Force circuit open: set recent opened_at so it hasn't reset yet
        _circuit.failures = 999
        _circuit.opened_at = time.time()  # Recent = still open

        try:
            result = await run("test query")
            # Circuit is open, should return []
            assert result == []
        finally:
            # Reset circuit
            _circuit.failures = 0
            _circuit.opened_at = 0.0

    @pytest.mark.asyncio
    async def test_run_returns_empty_on_network_error(self):
        """run() returns [] when aiohttp session fails."""
        from hledac.universal.intelligence.pastebin_monitor import run

        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.side_effect = Exception("Network error")
            result = await run("test query")
            assert result == []

    def test_paste_finding_masked_secrets(self):
        """PasteFinding.masked_secrets() masks actual secrets."""
        from hledac.universal.intelligence.pastebin_monitor import PasteFinding

        pf = PasteFinding(
            uri="https://pastebin.com/abc123",
            source="pastebin",
            extracted_secrets=["my_super_secret_key_12345"],
            emails=[],
            ip_addresses=[],
            context_snippet="test",
        )
        masked = pf.masked_secrets()
        # Verify: ends with ****, not equal to original, same length
        assert masked[0].endswith("****")
        assert masked[0] != "my_super_secret_key_12345"
        assert len(masked[0]) == len("my_super_secret_key_12345")


class TestNoParentIntelligenceImports:
    """Phase 4.5: Verify no parent intelligence/ imports in canonical runtime."""

    def test_no_parent_intelligence_imports_in_pipeline(self):
        """live_public_pipeline does not import from parent intelligence/."""
        pipeline_path = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py"

        with open(pipeline_path, 'r') as f:
            content = f.read()

            # Check for parent intelligence imports
            forbidden_imports = [
                'from intelligence.',
                'import intelligence.',
                'from hledac.intelligence',
                'import hledac.intelligence',
            ]
            for forbidden in forbidden_imports:
                assert forbidden not in content, \
                    f"Found forbidden import '{forbidden}' in live_public_pipeline.py"

    def test_no_parent_intelligence_imports_in_sprint_scheduler(self):
        """runtime/sprint_scheduler does not import from parent intelligence/."""
        scheduler_path = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py"

        try:
            with open(scheduler_path, 'r') as f:
                content = f.read()

            forbidden_imports = [
                'from intelligence.',
                'import intelligence.',
                'from hledac.intelligence',
                'import hledac.intelligence',
            ]
            for forbidden in forbidden_imports:
                assert forbidden not in content, \
                    f"Found forbidden import '{forbidden}' in sprint_scheduler.py"
        except FileNotFoundError:
            pytest.skip("sprint_scheduler.py not found at expected path")


class TestAcademicPaperDataclass:
    """AcademicPaper dataclass from academic_discovery."""

    def test_academic_paper_to_dict(self):
        """AcademicPaper.to_dict() returns correctly structured dict."""
        from hledac.universal.intelligence.academic_discovery import AcademicPaper

        paper = AcademicPaper(
            title="Test Paper",
            authors=["Author One", "Author Two"],
            year=2024,
            link="https://arxiv.org/abs/1234.5678",
            source="arxiv",
            abstract="This is a test abstract.",
            doi="10.1234/test",
            citations=42,
            tags=["AI", "ML"],
        )

        d = paper.to_dict()
        assert d["title"] == "Test Paper"
        assert d["authors"] == ["Author One", "Author Two"]
        assert d["year"] == 2024
        assert d["source"] == "arxiv"
        assert d["citations"] == 42


class TestBoundedBehavior:
    """Verify bounded behavior for M1 8GB constraints."""

    def test_pastebin_max_pastes_constant_exists(self):
        """_MAX_PASTES_PER_SOURCE constant bounds paste collection."""
        from hledac.universal.intelligence.pastebin_monitor import _MAX_PASTES_PER_SOURCE
        assert _MAX_PASTES_PER_SOURCE == 10

    def test_academic_semaphore_bounded(self):
        """academic_discovery uses bounded Semaphore(5)."""
        from hledac.universal.intelligence import academic_discovery
        import inspect
        source = inspect.getsource(academic_discovery.search_academic_all)
        assert "Semaphore(5)" in source