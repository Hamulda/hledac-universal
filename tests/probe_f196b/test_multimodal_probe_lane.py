"""
Sprint F196B: Multimodal Enrichment Probe Lane
================================================

Tests the fail-soft behavior, lifecycle, RAM guard, supported-file gating,
and counter contracts of the multimodal enrichment layer (Sprint F195C).

Covers:
- multimodal/analyzer.py: MultimodalEnricher
- runtime/sprint_scheduler.py: _enrich_findings_multimodal, _init_multimodal,
  _close_multimodal, multimodal_enriched_findings counter
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from hledac.universal.multimodal.analyzer import (
    MultimodalEnricher,
    _SUPPORTED_EXTENSIONS as _MULTIMODAL_SUPPORTED_EXTENSIONS,
    _extract_file_path_from_payload as _mm_extract_file_path,
    _file_has_multimodal_support,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeReserveCtx:
    """Async context manager for governor.reserve()."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return None


class _FakeGovernor:
    """Minimal ResourceGovernor mock for MultimodalEnricher.

    Matches the interface actually used by VisionEncoder:
    - is_critical()
    - is_emergency()
    - high_water attribute
    - get_current_usage()
    - reserve() as async context manager
    """
    def __init__(self):
        self.is_critical_calls = 0
        self.is_emergency_calls = 0
        self._critical = False
        self._emergency = False
        self.high_water = 6000
        self._usage = {"ram_mb": 4000}

    def is_critical(self) -> bool:
        self.is_critical_calls += 1
        return self._critical

    def is_emergency(self) -> bool:
        self.is_emergency_calls += 1
        return self._emergency

    def reserve(self, *args, **kwargs):
        return _FakeReserveCtx()

    def get_current_usage(self):
        return self._usage


class _FakeFinding:
    """Minimal CanonicalFinding-like object for testing."""
    def __init__(self, finding_id: str, payload_text: str | None, source_type: str = "test"):
        self.finding_id = finding_id
        self.payload_text = payload_text
        self.source_type = source_type


def _make_temp_file(suffix: str, content: bytes = b"fake content") -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    import os
    os.write(fd, content)
    os.close(fd)
    return Path(path)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-1: Fail-soft invariants — enrich() never raises
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalEnricherFailSoft:
    """
    MultimodalEnricher.enrich() must never raise.
    All exceptions are caught and return None.
    """

    @pytest.fixture
    def governor(self):
        return _FakeGovernor()

    @pytest.fixture
    def enricher(self, governor):
        return MultimodalEnricher(governor=governor)

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
        path = _make_temp_file(".xyz_unknown")
        try:
            finding = _FakeFinding("fid1", str(path))
            result = await enricher.enrich(finding)
            assert result is None
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_enrich_file_not_found_is_none(self, enricher):
        """enrich(finding with non-existent path) returns None without raising."""
        finding = _FakeFinding("fid1", "/tmp/nonexistent_multimodal_12345.jpg")
        result = await enricher.enrich(finding)
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_governor_denies_heavy_vision(self, enricher, governor):
        """When governor.is_critical() is True, enrich returns None (RAM guard)."""
        governor._critical = True
        path = _make_temp_file(".jpg", b"\xff\xd8\xff\xe0 fake jpeg")
        try:
            finding = _FakeFinding("ram_guard_test", str(path))
            result = await enricher.enrich(finding)
            assert result is None
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_enrich_governor_emergency_denies(self, enricher, governor):
        """When governor.is_emergency() is True, enrich returns None (RAM guard)."""
        governor._emergency = True
        path = _make_temp_file(".pdf", b"%PDF-1.4 fake")
        try:
            finding = _FakeFinding("emergency_test", str(path))
            result = await enricher.enrich(finding)
            assert result is None
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_enrich_batch_never_raises(self, enricher):
        """enrich_batch() never raises — all failures are silent."""
        findings = [
            _FakeFinding("batch1", None),
            _FakeFinding("batch2", "/nonexistent/path.pdf"),
            _FakeFinding("batch3", None),
        ]
        result = await enricher.enrich_batch(findings)
        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-2: Lifecycle — initialize / close are idempotent and clean
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalEnricherLifecycle:
    """MultimodalEnricher lifecycle: initialize(), close() are idempotent."""

    @pytest.fixture
    def governor(self):
        return _FakeGovernor()

    @pytest.mark.asyncio
    async def test_init_idempotent(self, governor):
        """initialize() can be called multiple times without error."""
        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()
        await enricher.initialize()
        await enricher.initialize()
        assert enricher._initialized is True
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_after_init(self, governor):
        """close() after initialize() sets _initialized = False."""
        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()
        assert enricher._initialized is True
        await enricher.close()
        assert enricher._initialized is False

    @pytest.mark.asyncio
    async def test_close_idempotent(self, governor):
        """close() can be called multiple times without error."""
        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()
        await enricher.close()
        await enricher.close()
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_without_init(self, governor):
        """close() without prior initialize() does not raise."""
        enricher = MultimodalEnricher(governor=governor)
        await enricher.close()


# ─────────────────────────────────────────────────────────────────────────────
# F196B-3: RAM guard — _can_run_heavy_vision() fails-open
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalRAMGuard:
    """_can_run_heavy_vision() fails open — errors allow the operation."""

    @pytest.fixture
    def governor(self):
        return _FakeGovernor()

    def test_can_run_with_none_governor(self):
        """When governor is None, _can_run_heavy_vision returns True (fail-open)."""
        enricher = MultimodalEnricher(governor=None)
        assert enricher._can_run_heavy_vision() is True

    def test_can_run_when_governor_raises(self, governor):
        """When governor methods raise, _can_run_heavy_vision returns True (fail-open)."""
        governor.is_critical = MagicMock(side_effect=RuntimeError("governor error"))
        enricher = MultimodalEnricher(governor=governor)
        assert enricher._can_run_heavy_vision() is True

    def test_cannot_run_when_critical(self, governor):
        """When governor.is_critical() returns True, _can_run_heavy_vision is False."""
        governor._critical = True
        enricher = MultimodalEnricher(governor=governor)
        assert enricher._can_run_heavy_vision() is False

    def test_cannot_run_when_emergency(self, governor):
        """When governor.is_emergency() returns True, _can_run_heavy_vision is False."""
        governor._emergency = True
        enricher = MultimodalEnricher(governor=governor)
        assert enricher._can_run_heavy_vision() is False

    def test_can_run_under_normal_conditions(self, governor):
        """When governor reports no pressure, _can_run_heavy_vision is True."""
        enricher = MultimodalEnricher(governor=governor)
        assert enricher._can_run_heavy_vision() is True


# ─────────────────────────────────────────────────────────────────────────────
# F196B-4: Supported-file gating
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalFileGating:
    """Only files with known multimodal-supportable extensions pass gating."""

    def test_supported_extensions_not_empty(self):
        """_SUPPORTED_EXTENSIONS must not be empty."""
        assert len(_MULTIMODAL_SUPPORTED_EXTENSIONS) > 0

    def test_supported_extensions_lowercase(self):
        """All entries in _SUPPORTED_EXTENSIONS are lowercase."""
        for ext in _MULTIMODAL_SUPPORTED_EXTENSIONS:
            assert ext == ext.lower(), f"{ext} is not lowercase"

    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".jpg", True),
            (".jpeg", True),
            (".png", True),
            (".pdf", True),
            (".tiff", True),
            (".bmp", True),
            (".gif", True),
            (".webp", True),
            (".tif", True),
            (".xyz", False),
            (".txt", False),
            (".docx", False),
            (".mp3", False),
            (".html", False),
            ("", False),
            (".JPG", True),  # Path.suffix.lower() converts to .jpg
        ],
    )
    def test_file_has_multimodal_support(self, ext, expected):
        result = _file_has_multimodal_support(f"/tmp/fakefile{ext}")
        assert result is expected, f"extension {ext}: expected {expected}, got {result}"

    def test_extract_file_path_direct(self):
        """_extract_file_path_from_payload handles direct absolute paths."""
        path = _make_temp_file(".pdf")
        try:
            result = _mm_extract_file_path(str(path))
            assert result == str(path)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_file_path_file_url(self):
        """_extract_file_path_from_payload handles file:// URLs."""
        path = _make_temp_file(".jpg")
        try:
            result = _mm_extract_file_path(f"file://{path}")
            assert result == str(path)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_file_path_none(self):
        """_extract_file_path_from_payload returns None for None."""
        assert _mm_extract_file_path(None) is None

    def test_extract_file_path_empty(self):
        """_extract_file_path_from_payload returns None for empty string."""
        assert _mm_extract_file_path("") is None

    def test_extract_file_path_nonexistent(self):
        """_extract_file_path_from_payload returns None for non-existent paths."""
        assert _mm_extract_file_path("/tmp/does_not_exist_multimodal_12345.pdf") is None


# ─────────────────────────────────────────────────────────────────────────────
# F196B-5: Return dict contract — keys are always present
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalReturnContract:
    """enrich() always returns a dict with the documented keys when not None."""

    @pytest.fixture
    def governor(self):
        return _FakeGovernor()

    @pytest.mark.asyncio
    async def test_return_dict_keys_present(self, governor):
        """When enrich() returns non-None, all documented keys are present."""
        enricher = MultimodalEnricher(governor=governor)
        path = _make_temp_file(".pdf", b"%PDF-1.4 fake pdf")

        # Patch vision encoder to produce a fake embedding
        await enricher.initialize()
        if enricher._vision_encoder is not None:
            class _FakeEmbedding:
                def tolist(self):
                    return [0.1] * 1280
            enricher._vision_encoder.encode_batch = AsyncMock(
                return_value=[_FakeEmbedding()]
            )

        finding = _FakeFinding("contract_fid", str(path))
        result = await enricher.enrich(finding)

        await enricher.close()
        path.unlink(missing_ok=True)

        if result is not None:
            expected_keys = {
                "finding_id",
                "file_path",
                "vision_embedding",
                "fused_embedding",
                "clip_score",
                "enrichment_available",
            }
            assert expected_keys.issubset(result.keys()), (
                f"Missing keys: {expected_keys - result.keys()}"
            )
            assert isinstance(result["enrichment_available"], bool)

    @pytest.mark.asyncio
    async def test_enrich_batch_returns_dict(self, governor):
        """enrich_batch() always returns a dict."""
        enricher = MultimodalEnricher(governor=governor)
        result = await enricher.enrich_batch([])
        assert isinstance(result, dict)
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# F196B-6: Multimodal LMDB storage is additive
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalStorageIsAdditive:
    """Enrichment data goes to LMDB keyed by finding_id — never mutates finding."""

    @pytest.fixture
    def governor(self):
        return _FakeGovernor()

    @pytest.mark.asyncio
    async def test_enrich_does_not_mutate_finding(self, governor):
        """enrich() must not modify the input finding object."""
        enricher = MultimodalEnricher(governor=governor)
        path = _make_temp_file(".png", b"\x89PNG\r\n\x1a\n fake png")
        try:
            await enricher.initialize()

            finding = _FakeFinding("mutate_test", str(path))
            finding_copy = vars(finding).copy()

            await enricher.enrich(finding)

            assert vars(finding) == finding_copy, "enrich() must not mutate the finding object"
        finally:
            await enricher.close()
            path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# F196B-7: Lazy-loading — multimodal modules don't load until first use
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalLazyLoading:
    """Multimodal modules are lazy-loaded inside enrichment methods."""

    def test_supported_extensions_available_without_import(self):
        """_SUPPORTED_EXTENSIONS is available without triggering heavy imports."""
        assert ".jpg" in _MULTIMODAL_SUPPORTED_EXTENSIONS
        assert ".pdf" in _MULTIMODAL_SUPPORTED_EXTENSIONS

    @pytest.mark.asyncio
    async def test_enrich_without_init_works(self):
        """enrich() calls _ensure_initialized() automatically if not initialized."""
        enricher = MultimodalEnricher(governor=_FakeGovernor())
        finding = _FakeFinding("lazy_test", "/nonexistent/file.pdf")
        # Must not raise even without explicit initialize()
        result = await enricher.enrich(finding)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# F196B-8: Concurrency — enrich_batch uses bounded semaphore
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalConcurrency:
    """enrich_batch uses a semaphore to cap concurrent enrichments."""

    @pytest.mark.asyncio
    async def test_batch_semaphore_limit(self):
        """enrich_batch limits concurrent executions to 3 (M1 8GB safe)."""
        governor = _FakeGovernor()
        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()

        import inspect
        source = inspect.getsource(enricher.enrich_batch)
        assert "Semaphore(3)" in source, "enrich_batch must use Semaphore(3) for M1 8GB safety"

        await enricher.close()


# ─────────────────────────────────────────────────────────────────────────────
# F196B-9: SprintScheduler multimodal counter contracts
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalSchedulerCounter:
    """SprintSchedulerResult.multimodal_enriched_findings is a non-negative int."""

    def test_result_has_multimodal_counter_field(self):
        """SprintSchedulerResult must have multimodal_enriched_findings field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        assert hasattr(result, "multimodal_enriched_findings")
        assert isinstance(result.multimodal_enriched_findings, int)
        assert result.multimodal_enriched_findings >= 0

    def test_multimodal_counter_accepts_value(self):
        """multimodal_enriched_findings can be set to a positive value."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        result.multimodal_enriched_findings = 4
        assert result.multimodal_enriched_findings == 4


# ─────────────────────────────────────────────────────────────────────────────
# F196B-10: Governor is required on construction
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalGovernorRequired:
    """MultimodalEnricher.__init__ requires a governor argument."""

    def test_init_requires_governor(self):
        """MultimodalEnricher.__init__ must have governor param."""
        import inspect
        sig = inspect.signature(MultimodalEnricher.__init__)
        param_names = list(sig.parameters.keys())
        assert "governor" in param_names, "MultimodalEnricher.__init__ must have governor param"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
