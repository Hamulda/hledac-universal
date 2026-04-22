"""
Sprint 8SA: End-to-end smoke test for live feed pipeline.

Tests that the pipeline can produce non-empty signal_stage results
for real RSS/Atom feeds from the default curated seeds.

Invariant tested:
- At least 1 feed from default seeds must return signal_stage != "empty_registry"
- No mocks — real HTTP with 10s timeout per feed
- Network errors → SKIP (not FAIL)
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from hledac.universal.pipeline.live_feed_pipeline import (
    async_run_default_feed_batch,
)


@pytest.mark.live
@pytest.mark.asyncio
async def test_pipeline_produces_findings_for_known_feed():
    """
    Smoke test: alespoň 1 feed z default seeds musí vrátit
    accepted_findings > 0 NEBO signal_stage != 'empty_registry'.
    Pokud všechny vrátí empty_registry, test FAIL s jasným výpisem.

    No mocks — real HTTP, timeout 10s per feed.
    Network connectivity errors → SKIP (not FAIL).
    """
    log = logging.getLogger(__name__)

    try:
        batch_result = await async_run_default_feed_batch(
            store=None,
            max_entries_per_feed=10,
            feed_concurrency=2,
            per_feed_timeout_s=10.0,
            batch_timeout_s=120.0,
        )
    except asyncio.TimeoutError:
        pytest.skip("batch_timeout — feeds took too long to respond")
    except OSError as exc:
        if "Connect" in type(exc).__name__ or "DNS" in type(exc).__name__ or "No route" in str(exc):
            pytest.skip(f"Network unavailable: {exc}")
        raise

    sources = batch_result.sources
    assert len(sources) > 0, "No feed sources were processed"

    non_empty_registry: list[str] = []
    empty_registry: list[str] = []
    network_errors: list[str] = []

    for src in sources:
        stage = getattr(src, "signal_stage", "unknown")
        if src.error and ("Connect" in src.error or "DNS" in src.error or "timeout" in src.error.lower()):
            network_errors.append(f"{src.feed_url} [{src.error}]")
            log.warning("[SKIP] %s — %s", src.feed_url, src.error)
            continue

        if stage not in ("empty_registry", "unknown"):
            non_empty_registry.append(src.feed_url)
            log.info(
                "[PASS] %s | stage=%s patterns=%s avg_len=%.1f findings=%d",
                src.feed_url,
                stage,
                getattr(src, "patterns_configured", 0),
                getattr(src, "avg_assembled_text_len", 0.0),
                src.accepted_findings,
            )
        elif stage == "unknown":
            empty_registry.append(src.feed_url)
            log.warning(
                "[EMPTY] %s | stage=unknown (no entries fetched — network or feed error)",
                src.feed_url,
            )
        else:
            empty_registry.append(src.feed_url)
            log.warning(
                "[EMPTY] %s | patterns=%s avg_len=%.1f findings=%d",
                src.feed_url,
                getattr(src, "patterns_configured", 0),
                getattr(src, "avg_assembled_text_len", 0.0),
                src.accepted_findings,
            )

    log.info("=" * 60)
    log.info("SUMMARY: non_empty=%d empty=%d errors=%d",
             len(non_empty_registry), len(empty_registry), len(network_errors))
    log.info("=" * 60)

    # Assert: at least 1 feed returned non-empty_registry
    assert len(non_empty_registry) > 0, (
        f"ALL feeds returned 'empty_registry' signal stage.\n"
        f"This means bootstrap patterns are likely not configured or not loading.\n"
        f"Empty feeds ({len(empty_registry)}): {empty_registry}\n"
        f"Network errors ({len(network_errors)}): {network_errors}"
    )
