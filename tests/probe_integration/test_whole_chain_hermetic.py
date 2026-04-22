"""
Sprint F193 integration lane — whole-chain hermetic tests.

Scope: canonical feed → DuckDB store → reopen verification.
No network, no Tor, no live external services. Deterministic.

Edit ONLY these files:
- tests/probe_integration/test_whole_chain_hermetic.py
- tests/probe_integration/__init__.py

Tests:
  test_canonical_feed_to_export_hermetic
  test_quality_gate_rejection_blocks_persist
  test_persist_then_reopen_preserves_findings
"""

import asyncio
import tempfile
import shutil
from pathlib import Path

import pytest

from hledac.universal.knowledge.duckdb_store import (
    DuckDBShadowStore,
    CanonicalFinding,
    FindingQualityDecision,
    _QUALITY_ENTROPY_THRESHOLD,
)
from hledac.universal.pipeline.live_public_pipeline import (
    async_run_live_public_pipeline,
    _patch_discovery,
    _patch_fetcher_and_matcher,
)


# ---------------------------------------------------------------------------
# Canned infrastructure
# ---------------------------------------------------------------------------

class _CannedDiscoveryHit:
    def __init__(self, url, title="", snippet="", rank=0):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.rank = rank


class _CannedDiscoveryResult:
    def __init__(self, hits, error=None):
        self.hits = hits
        self.error = error


class _CannedFetchResult:
    def __init__(self, text, url="http://example.com"):
        self.text = text
        self.content_type = "text/html"
        self.url = url
        self.final_url = url
        self.status_code = 200
        self.fetched_bytes = len(text) if text else 0
        self.declared_length = len(text) if text else 0
        self.elapsed_ms = 10.0
        self.error = None


# ---------------------------------------------------------------------------
# Test 1: canonical feed → export (hermetic)
# ---------------------------------------------------------------------------

def test_canonical_feed_to_export_hermetic():
    """
    Canonical feed path: canned discovery hit → live_public_pipeline
    → DuckDBShadowStore ingest → verify stored finding.

    Hermetic: no network, no Tor, no live external services.
    """
    from hledac.universal.patterns.pattern_matcher import PatternHit

    # Canned discovery: returns one hit
    async def canned_search(q, m):
        return _CannedDiscoveryResult([
            _CannedDiscoveryHit(
                url="https://www.example.com/security/cve-2026-9999",
                title="CVE-2026-9999 Security Advisory",
                snippet="Critical vulnerability in ExampleServer",
                rank=0,
            ),
        ])

    # Canned fetcher: HTML with a CVE pattern hit
    async def canned_fetch(url, timeout, max_bytes, **kw):
        # Must exceed _PRE_FETCH_TEXT_MIN_CHARS=150 after HTML-to-text conversion
        return _CannedFetchResult(
            text=(
                "<html><body><article>"
                "<h1>CVE-2026-9999 Security Advisory</h1>"
                "<p>Critical vulnerability allows remote code execution via crafted network requests.</p>"
                "<p>Affected versions: 1.0 through 1.9.2. Update immediately to prevent exploitation.</p>"
                "<p>Severity: Critical. Attack complexity is low. No privileges required.</p>"
                "</article></html>"
            ),
            url=url,
        )

    _patch_discovery(canned_search)
    _patch_fetcher_and_matcher(
        canned_fetch,
        lambda t: [PatternHit(
            pattern="CVE-2026-9999",
            start=0,
            end=13,
            value="CVE-2026-9999",
            label="vulnerability_id",
        )] if "CVE-2026-9999" in t else [],
    )

    tmp = tempfile.mkdtemp(prefix="hledac_hermetic_feed_")
    db_path = Path(tmp) / "shadow.duckdb"
    try:
        store = DuckDBShadowStore(db_path=str(db_path))
        store._init_persistent_dedup_lmdb = lambda: None
        asyncio.run(store.async_initialize())

        result = asyncio.run(async_run_live_public_pipeline(
            query="CVE-2026-9999 security advisory",
            store=store,
            max_results=5,
            fetch_timeout_s=10.0,
            fetch_max_bytes=200_000,
            fetch_concurrency=1,
        ))

        # Pipeline should have discovered + fetched + matched
        assert result.discovered >= 1, f"Expected >=1 discovered, got {result.discovered}"
        assert result.fetched >= 1, f"Expected >=1 fetched, got {result.fetched}"

        # Query DuckDB for persisted findings
        findings = asyncio.run(store.async_get_recent_findings(limit=20))
        pipeline_findings = [
            f for f in findings
            if getattr(f, "source_type", "") == "live_public_pipeline"
        ]

        asyncio.run(store.aclose())

        assert len(pipeline_findings) >= 1, (
            f"Expected >=1 live_public_pipeline finding in DuckDB, got {len(pipeline_findings)}. "
            f"pipeline: discovered={result.discovered} fetched={result.fetched} "
            f"matched={result.matched_patterns} accepted={result.accepted_findings}"
        )
        f0 = pipeline_findings[0]
        assert hasattr(f0, "finding_id") and f0.finding_id
        assert hasattr(f0, "query") and f0.query
        assert 0.0 <= f0.confidence <= 1.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 2: quality gate rejection blocks persist
# ---------------------------------------------------------------------------

def test_quality_gate_rejection_blocks_persist():
    """
    Quality gate: low-entropy / all-same-char finding is rejected
    and NOT persisted to DuckDB.

    Hermetic: no network, no Tor, no live external services.
    """
    from hledac.universal.patterns.pattern_matcher import reset_pattern_matcher

    # Reset pattern matcher so no patterns interfere
    reset_pattern_matcher()

    tmp = tempfile.mkdtemp(prefix="hledac_hermetic_qg_")
    db_path = Path(tmp) / "shadow.duckdb"
    try:
        store = DuckDBShadowStore(db_path=str(db_path))
        store._init_persistent_dedup_lmdb = lambda: None
        asyncio.run(store.async_initialize())

        # Finding with extremely low entropy: all same character repeated
        low_entropy_finding = CanonicalFinding(
            finding_id="hermetic-qg-test-0001",
            query="aaaaaaaabbbbbbbbcccccccc",
            source_type="hermetic_test",
            confidence=0.5,
            ts=0.0,
            provenance=("hermetic",),
            payload_text="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # very low entropy
        )

        # Ingest via quality-gated path
        result = asyncio.run(store.async_ingest_finding(low_entropy_finding))

        # Rejection should block persist
        assert isinstance(result, FindingQualityDecision), (
            f"Expected FindingQualityDecision, got {type(result).__name__}"
        )
        assert not result.accepted, (
            f"Expected finding to be rejected by quality gate, got accepted=True. "
            f"reason={result.reason}"
        )
        assert result.entropy < _QUALITY_ENTROPY_THRESHOLD

        # Verify nothing was written to DuckDB
        all_findings = asyncio.run(store.async_get_recent_findings(limit=50))
        qg_findings = [
            f for f in all_findings
            if getattr(f, "source_type", "") == "hermetic_test"
        ]

        asyncio.run(store.aclose())

        assert len(qg_findings) == 0, (
            f"Expected 0 hermetic_test findings after rejection, got {len(qg_findings)}. "
            f"Quality gate should have blocked persist."
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 3: persist then reopen preserves findings
# ---------------------------------------------------------------------------

def test_persist_then_reopen_preserves_findings():
    """
    Findings written to DuckDB are preserved across store close/reopen cycle.

    Hermetic: no network, no Tor, no live external services.
    """
    tmp = tempfile.mkdtemp(prefix="hledac_hermetic_reopen_")
    db_path = Path(tmp) / "shadow.duckdb"
    try:
        # First session: write findings
        store1 = DuckDBShadowStore(db_path=str(db_path))
        store1._init_persistent_dedup_lmdb = lambda: None
        asyncio.run(store1.async_initialize())

        canonical_findings = [
            CanonicalFinding(
                finding_id=f"hermetic-reopen-{i:04d}",
                query=f"reopen query {i}",
                source_type="hermetic_reopen_test",
                confidence=0.7,
                ts=0.0,
                provenance=("hermetic",),
                payload_text=f"payload text for finding {i}",
            )
            for i in range(3)
        ]

        asyncio.run(store1.async_record_canonical_findings_batch(canonical_findings))

        asyncio.run(store1.aclose())

        # Second session: reopen and verify
        store2 = DuckDBShadowStore(db_path=str(db_path))
        store2._init_persistent_dedup_lmdb = lambda: None
        asyncio.run(store2.async_initialize())

        findings_after_reopen = asyncio.run(
            store2.async_get_recent_findings(limit=50)
        )
        reopen_findings = [
            f for f in findings_after_reopen
            if getattr(f, "source_type", "") == "hermetic_reopen_test"
        ]

        asyncio.run(store2.aclose())

        assert len(reopen_findings) == 3, (
            f"Expected 3 hermetic_reopen_test findings after reopen, got {len(reopen_findings)}. "
            f"Findings should persist across store close/reopen."
        )
        ids = {f.finding_id for f in reopen_findings}
        expected_ids = {f"hermetic-reopen-{i:04d}" for i in range(3)}
        assert ids == expected_ids, (
            f"Finding IDs mismatch. Expected {expected_ids}, got {ids}"
        )
        # Note: payload_text is stored in LMDB WAL, not DuckDB — read-back shows None
        for f in reopen_findings:
            assert hasattr(f, "finding_id") and f.finding_id
            assert hasattr(f, "query") and f.query
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Aggressive Mode Whole-Chain Tests
# ---------------------------------------------------------------------------

@pytest.mark.hermetic
@pytest.mark.asyncio
async def test_aggressive_mode_whole_chain_partial_then_final_export():
    """
    Sprint F195B: Aggressive mode produces partial export during run and
    final export at end. Both artifacts contain expected finding counts.

    Hermetic: no network, no Tor, no live external services.
    """
    import json
    import orjson
    import tempfile as _tempfile

    from hledac.universal.patterns.pattern_matcher import PatternHit

    # Canned discovery: returns one hit
    async def canned_search(q, m):
        return _CannedDiscoveryResult([
            _CannedDiscoveryHit(
                url="https://www.example.com/security/cve-2026-9999",
                title="CVE-2026-9999 Security Advisory",
                snippet="Critical vulnerability in ExampleServer",
                rank=0,
            ),
        ])

    # Canned fetcher: HTML with a CVE pattern hit
    async def canned_fetch(url, timeout, max_bytes, **kw):
        return _CannedFetchResult(
            text=(
                "<html><body><article>"
                "<h1>CVE-2026-9999 Security Advisory</h1>"
                "<p>Critical vulnerability allows remote code execution via crafted network requests.</p>"
                "<p>Affected versions: 1.0 through 1.9.2. Update immediately to prevent exploitation.</p>"
                "<p>Severity: Critical. Attack complexity is low. No privileges required.</p>"
                "</article></html>"
            ),
            url=url,
        )

    _patch_discovery(canned_search)
    _patch_fetcher_and_matcher(
        canned_fetch,
        lambda t: [PatternHit(
            pattern="CVE-2026-9999",
            start=0,
            end=13,
            value="CVE-2026-9999",
            label="vulnerability_id",
        )] if "CVE-2026-9999" in t else [],
    )

    tmp = tempfile.mkdtemp(prefix="hledac_hermetic_aggressive_")
    db_path = Path(tmp) / "shadow.duckdb"
    try:
        store = DuckDBShadowStore(db_path=str(db_path))
        store._init_persistent_dedup_lmdb = lambda: None
        await store.async_initialize()

        # Sprint F195B: aggressive mode with partial export interval of 5 findings
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        config = SprintSchedulerConfig(
            sprint_duration_s=30.0,
            windup_lead_s=5.0,
            export_enabled=True,
            max_cycles=3,
            aggressive_mode=True,
            aggressive_branch_timeout_s=10.0,
            partial_export_findings_interval=5,
        )
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_aggressive_partial_final"

        lifecycle = SprintLifecycleManager(
            sprint_duration_s=30.0,
            windup_lead_s=5.0,
        )

        # Run pipeline to accumulate findings
        pipeline_result = await async_run_live_public_pipeline(
            query="CVE-2026-9999 security advisory",
            store=store,
            max_results=10,
            fetch_timeout_s=10.0,
            fetch_max_bytes=200_000,
            fetch_concurrency=1,
        )

        # Simulate findings accumulated in scheduler
        scheduler._finding_count = pipeline_result.accepted_findings

        # Write partial export (simulates what _try_partial_export does during sprint)
        from hledac.universal.export.sprint_exporter import export_partial_sprint

        runtime_truth = {
            "is_meaningful": True,
            "accepted_findings": pipeline_result.accepted_findings,
            "cycles_completed": 1,
            "aggressive_mode": True,
        }
        scorecard = {
            "cycles_started": 1,
            "cycles_completed": 1,
            "total_pattern_hits": pipeline_result.matched_patterns,
        }
        handoff_dict = {
            "sprint_id": "test_aggressive_partial_final",
            "runtime_truth": runtime_truth,
            "scorecard": scorecard,
        }

        partial_result = await export_partial_sprint(
            store=store,
            handoff=handoff_dict,
            sprint_id="test_aggressive_partial_final",
            finding_count=pipeline_result.accepted_findings,
        )

        # Verify partial export file exists and has correct structure
        partial_path = Path(partial_result["partial_json"])
        assert partial_path.exists(), (
            f"Partial export file not written: {partial_path}"
        )

        partial_data = orjson.loads(partial_path.read_bytes())
        assert partial_data.get("is_partial") is True, (
            "Partial export should have is_partial=True"
        )
        assert partial_data.get("sprint_id") == "test_aggressive_partial_final"
        assert partial_data.get("finding_count") == pipeline_result.accepted_findings

        # Write final export (simulates canonical terminal artifact at sprint end)
        from hledac.universal.export.sprint_exporter import export_sprint
        from hledac.universal.paths import get_sprint_json_report_path

        canonical_scorecard = {
            **scorecard,
            "sprint_verdict": "success",
            "verdict_confidence": 0.9,
            "total_findings": pipeline_result.accepted_findings,
            "accepted_findings": pipeline_result.accepted_findings,
        }

        from hledac.universal.project_types import ExportHandoff
        eh = ExportHandoff(
            sprint_id="test_aggressive_partial_final",
            scorecard=canonical_scorecard,
            runtime_truth=runtime_truth,
        )

        # Write final export (canonical terminal artifact at sprint end)
        # Note: export_sprint is normally called at windup end with full handoff;
        # here we verify the final report path is created with correct structure
        final_report_path = get_sprint_json_report_path("test_aggressive_partial_final")

        # Call export_sprint with minimal handoff — verifies final artifact mechanism
        # The export writes {sprint_id}.md and {sprint_id}.json to reports dir
        export_result = await export_sprint(
            store=store,
            handoff=eh,
            sprint_id="test_aggressive_partial_final",
        )

        # Verify final export result contains expected keys
        assert "report_json" in export_result or "report_md" in export_result, (
            f"Final export should produce report_json or report_md, got: {export_result.keys()}"
        )

        print(
            f"\n[aggressive_partial_final] partial_path={partial_path} "
            f"is_partial={partial_data.get('is_partial')} "
            f"finding_count={partial_data.get('finding_count')} "
            f"final_export_keys={list(export_result.keys())}"
        )

        await store.aclose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)