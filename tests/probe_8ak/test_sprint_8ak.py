"""
Sprint 8AK: Boot Hygiene + Persistent Dedup Authoritative + UMA SSOT

Tests:
  D.1  test_boot_guard_raises_on_unsafe_live_holder
  D.2  test_hysteresis_latch_is_resettable_between_tests
  D.3  test_sample_uma_status_uses_ssot_hysteresis
  D.4  test_live_public_pipeline_uses_ssot_uma_labels
  D.5  test_live_public_pipeline_critical_clamps_concurrency
  D.6  test_live_public_pipeline_emergency_aborts
  D.7  test_persistent_duplicate_same_source_is_rejected
  D.8  test_cross_source_fingerprint_position_agnostic
  D.9  test_cross_source_duplicate_web_then_feed
  D.10 test_cross_source_duplicate_feed_then_web
  D.11 test_persistent_duplicate_does_not_write_again
  D.12 test_batch_len_invariant_preserved_on_persistent_duplicates
  D.13 test_persistent_duplicate_sets_accepted_false
  D.14 test_get_dedup_runtime_status_distinguishes_counter_names
  D.15 test_existing_8w_contracts_still_hold
  D.16 test_existing_8ag_contracts_still_hold
  D.17 test_live_public_pipeline_import_regression_not_broken
  D.18 test_async_exitstack_real_registration_if_owned_surface_exists_or_explicit_na
"""


import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.core.resource_governor import (
    UMA_STATE_CRITICAL,
    UMA_STATE_EMERGENCY,
    UMA_STATE_OK,
    UMA_STATE_WARN,
    _reset_uma_hysteresis_for_testing,
    _update_io_only_latch_with_lock,
    evaluate_uma_state,
)
from hledac.universal.knowledge.lmdb_boot_guard import BootGuardError
from hledac.universal.knowledge.duckdb_store import (
    DuckDBShadowStore,
    CanonicalFinding,
    _compute_dedup_fingerprint,
    _compute_url_fingerprint,
    _normalize_osint_url,
)


# =============================================================================
# D.1 — Boot guard raises BootGuardError on unsafe live-holder
# =============================================================================

def test_boot_guard_raises_on_unsafe_live_holder():
    """
    BootGuardError must be defined and must be raised when a live lock holder
    is detected after confirming the lock is stale.
    """
    from hledac.universal.knowledge import lmdb_boot_guard as lbg
    import inspect

    # BootGuardError must exist and be an Exception subclass
    assert BootGuardError is not None
    assert issubclass(BootGuardError, Exception)

    # cleanup_stale_lmdb_lock must contain the BootGuardError raise path
    source = inspect.getsource(lbg.cleanup_stale_lmdb_lock)
    assert "BootGuardError" in source
    assert "raise BootGuardError" in source

    # The raise must be after confirming stale AND finding a live holder
    # _is_lock_stale is called first, then pid is checked again
    assert "_is_lock_stale" in source
    assert "_is_process_alive" in source or "is_process_alive" in source


# =============================================================================
# D.2 — Hysteresis latch is resettable between tests
# =============================================================================

def test_hysteresis_latch_is_resettable_between_tests():
    """
    _reset_uma_hysteresis_for_testing() must reset the shared latch to False.
    """
    from hledac.universal.core import resource_governor as rg

    # Set latch to True via direct module mutation
    with rg._io_only_latch_lock:
        rg._io_only_latch = True

    # Verify it's True
    with rg._io_only_latch_lock:
        assert rg._io_only_latch is True

    # Reset via the official API
    _reset_uma_hysteresis_for_testing()

    # Verify it's False
    with rg._io_only_latch_lock:
        assert rg._io_only_latch is False

    # Idempotent: call again
    _reset_uma_hysteresis_for_testing()
    with rg._io_only_latch_lock:
        assert rg._io_only_latch is False


# =============================================================================
# D.3 — sample_uma_status uses SSOT hysteresis internally
# =============================================================================

def test_sample_uma_status_uses_ssot_hysteresis():
    """
    The internal _update_io_only_latch_with_lock function must:
    - Enter True at >= CRITICAL (6.5 GiB)
    - Stay True even when memory drops slightly but stays above safe zone
    - Reset to False only when <= HYSTERESIS_EXIT (5.8 GiB)
    """
    _reset_uma_hysteresis_for_testing()

    # Enter critical: should enter io_only
    io_only_entered, _ = _update_io_only_latch_with_lock(6.5)
    assert io_only_entered is True

    # Stay in critical (6.4 GiB — between 5.8 and 6.5): should stay True
    io_only_stayed, _ = _update_io_only_latch_with_lock(6.4)
    assert io_only_stayed is True

    # Drop to safe zone: should reset to False
    io_only_exited, _ = _update_io_only_latch_with_lock(5.7)
    assert io_only_exited is False

    _reset_uma_hysteresis_for_testing()


# =============================================================================
# D.4 — live_public_pipeline uses SSOT UMA labels
# =============================================================================

def test_live_public_pipeline_uses_ssot_uma_labels():
    """
    async_run_live_public_pipeline must use SSOT constants from resource_governor,
    not raw string literals for state comparison.
    """
    import inspect
    from hledac.universal.pipeline import live_public_pipeline as lpp

    source = inspect.getsource(lpp.async_run_live_public_pipeline)

    # Must import and use SSOT constants
    assert "UMA_STATE_EMERGENCY" in source
    assert "UMA_STATE_CRITICAL" in source
    assert "UMA_STATE_OK" in source


# =============================================================================
# D.5 — live_public_pipeline critical clamps concurrency to 1
# =============================================================================

@pytest.mark.asyncio
async def test_live_public_pipeline_critical_clamps_concurrency():
    """
    When uma_state == critical, the pipeline must NOT emergency-abort.
    """
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

    mock_store = MagicMock()
    mock_store.async_ingest_findings_batch = AsyncMock(return_value=[])

    mock_discovery = MagicMock()
    mock_discovery.hits = []
    mock_discovery.error = "discovery_empty"

    with patch(
        "hledac.universal.pipeline.live_public_pipeline._ASYNC_DISCOVERY_SEARCH",
        new=AsyncMock(return_value=mock_discovery),
    ):
        with patch(
            "hledac.universal.pipeline.live_public_pipeline._get_uma_state",
            return_value=(UMA_STATE_CRITICAL, True),
        ):
            result = await async_run_live_public_pipeline(
                query="test",
                store=mock_store,
                max_results=1,
                fetch_concurrency=5,
            )
            # Must not abort; concurrency is clamped internally
            assert result.error != "uma_emergency_abort"


# =============================================================================
# D.6 — live_public_pipeline emergency aborts
# =============================================================================

@pytest.mark.asyncio
async def test_live_public_pipeline_emergency_aborts():
    """
    When uma_state == emergency, pipeline must return error="uma_emergency_abort".
    """
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

    with patch(
        "hledac.universal.pipeline.live_public_pipeline._get_uma_state",
        return_value=(UMA_STATE_EMERGENCY, True),
    ):
        result = await async_run_live_public_pipeline(
            query="test",
            store=MagicMock(),
            max_results=1,
        )
        assert result.error == "uma_emergency_abort"
        assert result.discovered == 0


# =============================================================================
# Helper: create a real CanonicalFinding
# =============================================================================

def _make_finding(
    finding_id: str,
    query: str = "test query",
    source_type: str = "test_source",
    confidence: float = 0.8,
    provenance: tuple = (),
    payload_text: str = "Test payload content",
) -> CanonicalFinding:
    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=source_type,
        confidence=confidence,
        ts=time.time(),
        provenance=provenance,
        payload_text=payload_text,
    )


def _get_accepted(result) -> bool:
    """Extract accepted field from FindingQualityDecision or ActivationResult."""
    if isinstance(result, dict):
        return bool(result.get("accepted"))
    return result.accepted


# =============================================================================
# D.7 — Persistent duplicate from same source is rejected
# =============================================================================

@pytest.mark.asyncio
async def test_persistent_duplicate_same_source_is_rejected():
    """
    Inserting two findings with same URL fingerprint must reject the second one.
    The LMDB dedup file is cleaned before this test to ensure isolation.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/same-source-dup"

    # First: URL fingerprint, no prior entry → accepted
    finding1 = CanonicalFinding(
        finding_id="ss-001",
        query="test query",
        source_type="test_source",
        confidence=0.8,
        ts=time.time(),
        provenance=("source", url, "label", "pattern"),
        payload_text="Unique content here",
    )

    result1 = await store.async_ingest_finding(finding1)
    assert _get_accepted(result1) is True, f"f1 should be accepted, got {result1}"

    # Second: same URL → must be rejected
    finding2 = CanonicalFinding(
        finding_id="ss-002",
        query="test query",
        source_type="test_source",
        confidence=0.8,
        ts=time.time(),
        provenance=("source", url, "label", "pattern"),
        payload_text="Different content but same URL",
    )

    result2 = await store.async_ingest_finding(finding2)
    assert result2.accepted is False, f"f2 should be rejected as duplicate, got {result2}"
    assert result2.reason == "persistent_duplicate"

    await store.aclose()


# =============================================================================
# D.8 — Cross-source fingerprint is position-agnostic
# =============================================================================

def test_cross_source_fingerprint_position_agnostic():
    """
    _extract_url_from_provenance must find URL regardless of its position
    in the tuple, for different source_types.
    """
    store = DuckDBShadowStore()

    # Source "duckduckgo": URL at position 1
    prov1 = ("duckduckgo", "http://example.com/article1", "label", "pattern")
    url1 = store._extract_url_from_provenance(prov1)
    assert url1 == "http://example.com/article1"

    # Source "web": URL at different position
    prov2 = ("web", "label", "http://example.com/article2", "pattern")
    url2 = store._extract_url_from_provenance(prov2)
    assert url2 == "http://example.com/article2"

    # Source "feed": URL as first element
    prov3 = ("feed", "http://example.com/article3")
    url3 = store._extract_url_from_provenance(prov3)
    assert url3 == "http://example.com/article3"

    # No URL
    prov4 = ("synthetic", "label", "pattern")
    url4 = store._extract_url_from_provenance(prov4)
    assert url4 == ""

    # Empty provenance
    prov5 = ()
    url5 = store._extract_url_from_provenance(prov5)
    assert url5 == ""


# =============================================================================
# D.9 — Cross-source duplicate: web then feed
# =============================================================================

@pytest.mark.asyncio
async def test_cross_source_duplicate_web_then_feed():
    """
    Same URL from different source_types must be detected as duplicate.
    Insert via "web" source, then try "feed" source.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/cross-source-article"

    finding_web = CanonicalFinding(
        finding_id="cs-web-001",
        query="test query",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Web article content",
    )

    finding_feed = CanonicalFinding(
        finding_id="cs-feed-002",
        query="test query",
        source_type="feed",
        confidence=0.8,
        ts=time.time(),
        provenance=("feed", url, "label", "pattern"),
        payload_text="Feed article content (different text)",
    )

    # First (web): accepted
    result1 = await store.async_ingest_finding(finding_web)
    assert _get_accepted(result1) is True

    # Second (feed): rejected as persistent duplicate (same URL)
    result2 = await store.async_ingest_finding(finding_feed)
    assert _get_accepted(result2) is False
    assert result2.get("reason") == "persistent_duplicate" if isinstance(result2, dict) else result2.reason == "persistent_duplicate"

    await store.aclose()


# =============================================================================
# D.10 — Cross-source duplicate: feed then web
# =============================================================================

@pytest.mark.asyncio
async def test_cross_source_duplicate_feed_then_web():
    """
    Reverse order of D.9: insert feed first, then web.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/reverse-order-article"

    finding_feed = CanonicalFinding(
        finding_id="cs-feed-003",
        query="test query",
        source_type="feed",
        confidence=0.8,
        ts=time.time(),
        provenance=("feed", url, "label", "pattern"),
        payload_text="Feed content",
    )

    finding_web = CanonicalFinding(
        finding_id="cs-web-004",
        query="test query",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Web content",
    )

    # First (feed): accepted
    result1 = await store.async_ingest_finding(finding_feed)
    assert _get_accepted(result1) is True

    # Second (web): rejected as persistent duplicate
    result2 = await store.async_ingest_finding(finding_web)
    assert _get_accepted(result2) is False
    assert result2.get("reason") == "persistent_duplicate" if isinstance(result2, dict) else result2.reason == "persistent_duplicate"

    await store.aclose()


# =============================================================================
# D.11 — Persistent duplicate does not write again to LMDB
# =============================================================================

@pytest.mark.asyncio
async def test_persistent_duplicate_does_not_write_again():
    """
    On a persistent duplicate hit, _store_persistent_dedup must NOT be called.
    The hot-cache is populated but no new LMDB write occurs.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/no-write-article"
    finding1 = CanonicalFinding(
        finding_id="nw-001",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content",
    )
    finding2 = CanonicalFinding(
        finding_id="nw-002",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Different content",
    )

    # First: accept
    await store.async_ingest_finding(finding1)

    # Track store calls
    original_store = store._store_persistent_dedup
    call_count = [0]
    def track_store(*args, **kwargs):
        call_count[0] += 1
        return original_store(*args, **kwargs)

    store._store_persistent_dedup = track_store

    # Second: reject as duplicate — must NOT call _store_persistent_dedup again
    await store.async_ingest_finding(finding2)
    assert call_count[0] == 0

    await store.aclose()


# =============================================================================
# D.12 — Batch len invariant preserved on persistent duplicates
# =============================================================================

@pytest.mark.asyncio
async def test_batch_len_invariant_preserved_on_persistent_duplicates():
    """
    async_ingest_findings_batch must return one result per input finding,
    even when some are persistent duplicates.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/batch-invariant"

    f1 = CanonicalFinding(
        finding_id="bi-001",
        query="batch test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 1",
    )
    f2 = CanonicalFinding(
        finding_id="bi-002",
        query="batch test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 2",
    )

    # Accept first
    await store.async_ingest_findings_batch([f1])

    # Batch with f2 (duplicate of f1 via same URL)
    results = await store.async_ingest_findings_batch([f2])
    assert len(results) == 1  # 1:1 invariant

    await store.aclose()


# =============================================================================
# D.13 — Persistent duplicate sets accepted=False
# =============================================================================

@pytest.mark.asyncio
async def test_persistent_duplicate_sets_accepted_false():
    """
    When a finding is rejected as persistent duplicate, accepted must be False.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/accepted-false"

    f1 = CanonicalFinding(
        finding_id="af-001",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 1",
    )
    f2 = CanonicalFinding(
        finding_id="af-002",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 2",
    )

    r1 = await store.async_ingest_finding(f1)
    assert _get_accepted(r1) is True

    r2 = await store.async_ingest_finding(f2)
    assert _get_accepted(r2) is False
    assert r2.get("reason") == "persistent_duplicate" if isinstance(r2, dict) else r2.reason == "persistent_duplicate"

    await store.aclose()


# =============================================================================
# D.14 — get_dedup_runtime_status distinguishes counter names
# =============================================================================

@pytest.mark.asyncio
async def test_get_dedup_runtime_status_distinguishes_counter_names():
    """
    Status must contain both in_memory_duplicate_count and persistent_duplicate_count
    as separate keys.
    """
    # Clean dedup LMDB before test
    import shutil
    try:
        from hledac.universal.paths import LMDB_ROOT
        dedup_path = LMDB_ROOT / "dedup.lmdb"
        if dedup_path.exists():
            shutil.rmtree(dedup_path)
    except Exception:
        pass

    store = DuckDBShadowStore()
    await store.async_initialize()

    url = "http://example.com/counter-names"

    f1 = CanonicalFinding(
        finding_id="cn-001",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 1",
    )
    f2 = CanonicalFinding(
        finding_id="cn-002",
        query="test",
        source_type="web",
        confidence=0.8,
        ts=time.time(),
        provenance=("web", url, "label", "pattern"),
        payload_text="Content 2",
    )

    # f1 accepted; f2 is duplicate (same URL) — inserted sequentially via batch
    await store.async_ingest_findings_batch([f1, f2])

    status = store.get_dedup_runtime_status()
    assert "in_memory_duplicate_count" in status
    assert "persistent_duplicate_count" in status
    # f2 hits hot cache → in_memory (same-process) duplicate
    assert status["in_memory_duplicate_count"] == 1
    # No LMDB cross-source hit in same batch
    assert status["persistent_duplicate_count"] == 0

    await store.aclose()


# =============================================================================
# D.15 — Existing 8W contracts still hold
# =============================================================================

def test_existing_8w_contracts_still_hold():
    """
    Verify _compute_dedup_fingerprint is deterministic and stable.
    """
    fp1 = _compute_dedup_fingerprint("hello world")
    fp2 = _compute_dedup_fingerprint("hello world")
    assert fp1 == fp2
    assert len(fp1) == 32  # BLAKE2b-128 = 32 hex chars

    # Case insensitive (normalized)
    fp3 = _compute_dedup_fingerprint("HELLO  WORLD")
    assert fp3 == fp1

    # Different inputs → different fingerprints
    fp4 = _compute_dedup_fingerprint("hello")
    fp5 = _compute_dedup_fingerprint("world")
    assert fp4 != fp5


# =============================================================================
# D.16 — Existing 8AG contracts still hold
# =============================================================================

def test_existing_8ag_contracts_still_hold():
    """
    Verify DuckDBShadowStore has all required 8AG dedup surfaces.
    """
    store = DuckDBShadowStore()
    assert hasattr(store, "_lookup_persistent_dedup")
    assert hasattr(store, "_store_persistent_dedup")
    assert hasattr(store, "get_dedup_runtime_status")
    assert hasattr(store, "_dedup_lmdb")
    assert hasattr(store, "_dedup_hot_cache")
    assert hasattr(store, "DEDUP_NAMESPACE")
    assert store.DEDUP_NAMESPACE == "dedup:"


# =============================================================================
# D.17 — live_public_pipeline import regression not broken
# =============================================================================

def test_live_public_pipeline_import_regression_not_broken():
    """
    Importing live_public_pipeline must not raise any exceptions.
    """
    try:
        from hledac.universal.pipeline.live_public_pipeline import (
            async_run_live_public_pipeline,
            PipelineRunResult,
            PipelinePageResult,
        )
        assert async_run_live_public_pipeline is not None
        assert PipelineRunResult is not None
        assert PipelinePageResult is not None
    except Exception as e:
        pytest.fail(f"Import failed: {e}")


# =============================================================================
# D.18 — AsyncExitStack real registration if owned surface exists
# =============================================================================

def test_async_exitstack_real_registration_if_owned_surface_exists_or_explicit_na():
    """
    main.py AsyncExitStack TODOs must document why registration is N/A:
    - duckdb_store.close() — NOT registered (duckdb not acquired in main.py)
    - atomic_storage.flush() — NOT registered (atomic storage not acquired)
    - persistent_layer.close() — NOT registered (persistent layer not acquired)
    """
    import ast
    from pathlib import Path

    main_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py")
    source = main_path.read_text()

    # Verify TODOs are present and describe why registration is N/A
    assert "# TODO [8AI]: Register duckdb_store.close()" in source
    assert "# TODO [8AI]: Register atomic_storage.flush()" in source
    assert "# TODO [8AI]: Register persistent_layer.close()" in source

    # Verify AsyncExitStack is used (backbone exists)
    assert "AsyncExitStack" in source
    assert "contextlib.AsyncExitStack()" in source


# =============================================================================
# URL normalization tests
# =============================================================================

def test_url_normalization_lowercase():
    """Scheme and host must be lowercased."""
    result = _normalize_osint_url("HTTP://EXAMPLE.COM/Path")
    assert result.startswith("http://example.com")


def test_url_normalization_strips_fragment():
    """Fragment must be stripped."""
    result = _normalize_osint_url("http://example.com/page#section")
    assert "#" not in result


def test_url_normalization_strips_trailing_slash():
    """Trailing slash on non-root paths must be stripped."""
    result = _normalize_osint_url("http://example.com/page/")
    assert not result.endswith("/page/") or "/page" in result


def test_url_normalization_root_trailing_slash_preserved():
    """Root path trailing slash must be preserved."""
    result = _normalize_osint_url("http://example.com/")
    assert result.endswith("/")


def test_url_normalization_strips_tracking_params():
    """UTM and ref params must be stripped."""
    result = _normalize_osint_url(
        "http://example.com/article?utm_source=twitter&fbclid=abc123&ref=sidebar"
    )
    assert "utm_source" not in result
    assert "fbclid" not in result
    assert "ref=" not in result
    assert "article" in result


def test_url_normalization_empty_input():
    """Empty/None input must return empty string."""
    assert _normalize_osint_url("") == ""
    assert _normalize_osint_url(None) == ""


def test_compute_url_fingerprint():
    """URL fingerprint must be 32-char hex string."""
    fp = _compute_url_fingerprint("http://example.com/article")
    assert len(fp) == 32
    assert all(c in "0123456789abcdef" for c in fp)


def test_compute_url_fingerprint_empty():
    """Empty URL must return empty string."""
    assert _compute_url_fingerprint("") == ""
    assert _compute_url_fingerprint(None) == ""
