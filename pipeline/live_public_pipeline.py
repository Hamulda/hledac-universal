"""Sprint 8AE: First live public OSINT pipeline wiring.

query -> discovery (8AC duckduckgo) -> fetch (8AD public_fetcher) ->
lightweight HTML extraction -> PatternMatcher (8X) -> quality gate (8W) ->






CanonicalFinding -> storage (8S/8R DuckDBShadowStore).

No LLM calls. No AO. No new storage schema.
All heavy I/O (HTML parsing, pattern scanning) offloaded via asyncio.to_thread().
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
import msgspec.json as _json
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.tools.url_dedup import get_default_bloom_filter
from hledac.universal.utils.locks import LazyAsyncioLock
from hledac.universal.runtime.lane_registry import LANE_REGISTRY

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# F206AB: discovery error taxonomy helper
from hledac.universal.discovery.duckduckgo_adapter import (  # noqa: E402
    DiscoveryHit,
    classify_discovery_error,
)
from hledac.universal.discovery.duckduckgo_adapter import (
    search_multi_engine as _search_multi_engine_bootstrap,
)

# F206AC: fetch error taxonomy helper
from hledac.universal.fetching.public_fetcher import (  # noqa: E402
    classify_fetch_error,
)
from hledac.universal.utils.asyncx import parallel, safe_create_task  # noqa: E402, safe_wait_for
from hledac.universal.utils.config_introspection import safe_attr_get  # noqa: E402
from hledac.universal.pipeline.public_patterns import _make_finding_id  # noqa: E402, F401


# -----------------------------------------------------------------------------
# F363: DiscoveryPhaseResult - returned by DiscoveryEngine.run()
# F364: Converted from dataclass to msgspec.Struct for consistency
# Note: Named DiscoveryPhaseResult to avoid conflict with discovery/base.py:38
# Moved before DiscoveryEngine for forward reference (F363 refactor)
# -----------------------------------------------------------------------------


class DiscoveryPhaseResult(Struct, frozen=True, gc=False):
    """Structured discovery output for downstream phases.

    F363: Replaces 13-element tuple return from DiscoveryEngine.run().
    F364: Converted from dataclass to msgspec.Struct for consistency with codebase.
    Provides named access to all discovery results with type hints.

    Note: Named DiscoveryPhaseResult to avoid conflict with DiscoveryResult
    in discovery/base.py which represents a single discovery hit.
    """
    hits: tuple
    discovery_result: Any
    discovery_error: str | None
    discovery_error_type: str | None
    discovery_elapsed_s: float | None
    discovery_attempted: bool
    discovery_telemetry: dict
    academic_findings_count: int
    ct_injected: int
    cc_injected: int
    onion_findings_count: int
    pastebin_findings_count: int
    github_secrets_count: int
    keyword_seed_fallback_triggered: bool


# -----------------------------------------------------------------------------
# F360: Module-level DiscoveryEngine (extracted from async_run_live_public_pipeline)
# Replaces nested class to reduce CC of async_run_live_public_pipeline from 170 to ~50
# -----------------------------------------------------------------------------

# Sprint F217C: Deterministic bootstrap URL generator - moved to module level for DiscoveryEngine

class DiscoveryEngine(Struct):
    """Engine 1: Handles all discovery-related logic.

    Input state: query, store, max_results, public_bootstrap_enabled, seed_context
    Output state: enriched hits tuple + all discovery telemetry accumulators

    Extracted from async_run_live_public_pipeline for CC reduction (F360).
    F362: Refactored into helper methods to reduce CC from 27 to ~12.
    F364: Refactored to return DiscoveryPhaseResult (msgspec.Struct) instead of 13-element tuple.
    """

    query: str
    store: Any
    max_results: int
    public_bootstrap_enabled: bool
    seed_context: Any | None

    async def run(self, uma_state: str) -> DiscoveryPhaseResult:
        """Run discovery phase with bootstrap, rescue, and keyword fallback.

        Returns DiscoveryPhaseResult with all discovery telemetry and enriched hits.
        """
        # Phase 1: Bootstrap + Rescue
        bs = await self._run_bootstrap_phase()
        bootstrap_hits, rescue_hits = bs["bootstrap_hits"], bs["rescue_hits"]

        # Phase 2: Discovery
        disc = await self._run_discovery_phase(bootstrap_hits, rescue_hits)
        hits = disc["hits"]
        discovery_error = disc["error"]
        discovery_error_type = disc["error_type"]
        discovery_elapsed_s = disc["elapsed"]
        discovery_attempted = disc["attempted"]

        # Phase 3: Keyword fallback (only if no hits)
        kw_result = {"candidates_count": 0, "fetch_attempted": 0, "fetch_success": 0,
                      "bootstrap_order": "disabled", "errors": 0, "hits": ()}
        if not hits:
            kw_result = await self._run_keyword_fallback()
            hits = kw_result["hits"]

        # Build discovery telemetry
        discovery_telemetry = _build_discovery_telemetry(
            discovery_result=disc["result"],
            discovery_error=discovery_error,
            discovery_error_type=discovery_error_type,
            discovery_elapsed_s=discovery_elapsed_s,
            discovery_attempted=discovery_attempted,
            public_discovery_cache_hit=disc["cache_hit"],
            public_discovery_query_count=disc["query_count"],
            hits=hits,
            pub_bootstrap_order=bs["bootstrap_order"],
            pub_bootstrap_prevented_discovery_timeout=bs["prevented_timeout"],
            pub_bootstrap_first_fetch_attempted=bs["first_fetch_attempted"],
            pub_bootstrap_candidates_count=bs["candidates_count"],
            pub_bootstrap_fetch_attempted=bs["fetch_attempted"],
            pub_bootstrap_fetch_success=bs["fetch_success"],
            pub_bootstrap_accepted_findings=0,
            pub_bootstrap_errors=0,
            pub_rescue_candidates_count=bs["rescue_count"],
            pub_rescue_fetch_attempted=0,
            pub_rescue_fetch_success=0,
            pub_rescue_accepted_findings=0,
            pub_rescue_errors=0,
            pub_rescue_order=bs["rescue_order"],
            keyword_seed_fallback_triggered=bs["keyword_fallback_triggered"],
            pub_keyword_bootstrap_candidates_count=kw_result["candidates_count"],
            pub_keyword_bootstrap_fetch_attempted=kw_result["fetch_attempted"],
            pub_keyword_bootstrap_fetch_success=kw_result["fetch_success"],
            pub_keyword_bootstrap_order=kw_result["bootstrap_order"],
            pub_keyword_bootstrap_errors=kw_result["errors"],
            pub_build_success_count=0,
            pub_build_failure_count=0,
            pub_duplicate_count=0,
            pub_provider_selected=disc["provider_selected"],
            pub_provider_skipped=disc["provider_skipped"],
            pub_provider_stub=disc["provider_stub"],
            pub_provider_errors=disc["provider_errors"],
            pub_query_variants=disc["query_variants"],
            pub_provider_timeout_count=disc["provider_timeout_count"],
            pub_provider_import_error_count=disc["provider_import_error_count"],
            pub_discovery_empty_reason=disc["discovery_empty_reason"],
            public_candidates_discovered=0,
            public_candidates_fetch_attempted=0,
            public_candidates_fetch_success=0,
            public_candidates_parse_success=0,
            public_candidates_pattern_matched=0,
            public_candidates_built=0,
            public_candidates_store_attempted=0,
            public_candidates_stored=0,
            public_candidates_rejected=0,
            stage_failure=None,
            stage_failure_reason=None,
        )

        # Empty hits case - return minimal result
        if not hits:
            return DiscoveryPhaseResult(
                hits=(),
                discovery_result=None,
                discovery_error=discovery_error,
                discovery_error_type=discovery_error_type,
                discovery_elapsed_s=discovery_elapsed_s,
                discovery_attempted=discovery_attempted,
                discovery_telemetry=discovery_telemetry,
                academic_findings_count=0,
                ct_injected=0,
                cc_injected=0,
                onion_findings_count=0,
                pastebin_findings_count=0,
                github_secrets_count=0,
                keyword_seed_fallback_triggered=bs["keyword_fallback_triggered"],
            )

        # Run augmentation phases
        augmented_result = await self._run_augmentation_phases(hits)
        hits = augmented_result["hits"]

        # Phase: Onion discovery
        onion_findings_count = await _run_onion_phase(hits, self.query, self.store)

        return DiscoveryPhaseResult(
            hits=hits,
            discovery_result=disc["result"],
            discovery_error=discovery_error,
            discovery_error_type=discovery_error_type,
            discovery_elapsed_s=discovery_elapsed_s,
            discovery_attempted=discovery_attempted,
            discovery_telemetry=discovery_telemetry,
            academic_findings_count=augmented_result["academic_findings_count"],
            ct_injected=augmented_result["ct_injected"],
            cc_injected=augmented_result["cc_injected"],
            onion_findings_count=onion_findings_count,
            pastebin_findings_count=augmented_result["pastebin_findings_count"],
            github_secrets_count=augmented_result["github_secrets_count"],
            keyword_seed_fallback_triggered=bs["keyword_fallback_triggered"],
        )

    async def _run_augmentation_phases(self, hits: tuple) -> dict:
        """Run all augmentation phases: academic, CT, CC, Pastebin, GitHub.

        Returns dict with enriched hits and injection counts.
        """
        # Academic research lane
        academic_findings_count = await _run_academic_lane(self.store, self.query)

        # CT + CC + Pastebin/GitHub in parallel
        ct_augmented, cc_augmented, p20_counts = await _run_phase1_augmentation(
            hits, self.query, self.store
        )
        ct_injected = len(ct_augmented) - len(hits)
        cc_injected = len(cc_augmented) - len(hits)
        pastebin_findings_count, github_secrets_count = p20_counts

        # CC builds on CT result
        enriched_hits = cc_augmented

        return {
            "hits": enriched_hits,
            "academic_findings_count": academic_findings_count,
            "ct_injected": ct_injected,
            "cc_injected": cc_injected,
            "pastebin_findings_count": pastebin_findings_count,
            "github_secrets_count": github_secrets_count,
        }

    # -------------------------------------------------------------------------
    # F362: Helper methods extracted from DiscoveryEngine.run
    # Each method handles one phase with local telemetry accumulation
    # -------------------------------------------------------------------------

    async def _run_bootstrap_phase(self) -> dict:
        """Phase 1: Run bootstrap and rescue URL generation.

        Returns dict with bootstrap_hits, rescue_hits, candidates_count, bootstrap_order,
        prevented_timeout, first_fetch_attempted, fetch_attempted, rescue_count, rescue_order,
        keyword_fallback_triggered.
        """
        bootstrap_hits: list[DiscoveryHit] = []
        rescue_hits: list[DiscoveryHit] = []
        candidates_count = 0
        bootstrap_order = "disabled"
        prevented_timeout = False
        first_fetch_attempted = False
        fetch_attempted = 0
        fetch_success = 0
        rescue_count = 0
        rescue_order = "disabled"
        keyword_fallback_triggered = False

        # F1-3: keyword_seed_fallback (initial rescue)
        try:
            rescue_hits = generate_rescue_urls(self.query, max_urls=5)
            rescue_count = len(rescue_hits)
            if rescue_hits:
                rescue_order = "keyword_seed_fallback"
                keyword_fallback_triggered = True
                bootstrap_hits = rescue_hits
                rescue_hits = []
        except Exception:
            rescue_count = 0

        if self.public_bootstrap_enabled:
            # Deterministic bootstrap URLs
            try:
                bootstrap_urls = generate_bootstrap_urls(self.query, max_urls=_MAX_BOOTSTRAP_URLS)
                candidates_count = len(bootstrap_urls)
                for idx, url in enumerate(bootstrap_urls):
                    bootstrap_hits.append(DiscoveryHit(
                        query=self.query,
                        title=f"Bootstrap {idx+1}",
                        url=url,
                        snippet=f"Deterministic bootstrap URL: {url}",
                        score=0.85,
                        reason="deterministic_bootstrap",
                        rank=-1,
                        source="bootstrap",
                        retrieved_ts=0.0,
                    ))
            except Exception:
                candidates_count = 0

            # Rescue fallback for non-domain threat queries
            if candidates_count == 0:
                try:
                    rescue_hits = generate_rescue_urls(self.query, max_urls=8)
                    rescue_count = len(rescue_hits)
                    if rescue_hits:
                        rescue_order = "rescue_fallback"
                        bootstrap_hits = rescue_hits
                        rescue_hits = []
                except Exception:
                    rescue_count = 0

            # Seed context bootstrap fallback
            if candidates_count == 0 and rescue_count == 0 and self.seed_context is not None:
                try:
                    seed_urls = generate_seed_context_bootstrap_urls(
                        self.seed_context, max_candidates=_MAX_SEED_CONTEXT_BOOTSTRAP
                    )
                    candidates_count = len(seed_urls)
                    for idx, url in enumerate(seed_urls):
                        bootstrap_hits.append(DiscoveryHit(
                            query=self.query,
                            title=f"SeedBootstrap {idx+1}",
                            url=url,
                            snippet=f"Seed context bootstrap URL: {url}",
                            score=0.80,
                            reason="seed_context_bootstrap",
                            rank=-1,
                            source="seed_bootstrap",
                            retrieved_ts=0.0,
                        ))
                except Exception:
                    candidates_count = 0

        return {
            "bootstrap_hits": bootstrap_hits,
            "rescue_hits": rescue_hits,
            "candidates_count": candidates_count,
            "bootstrap_order": bootstrap_order,
            "prevented_timeout": prevented_timeout,
            "first_fetch_attempted": first_fetch_attempted,
            "fetch_attempted": fetch_attempted,
            "fetch_success": fetch_success,
            "rescue_count": rescue_count,
            "rescue_order": rescue_order,
            "keyword_fallback_triggered": keyword_fallback_triggered,
        }

    async def _run_discovery_phase(
        self,
        bootstrap_hits: list,
        rescue_hits: list,
    ) -> dict:
        """Phase 2: Execute the main discovery search and merge with bootstrap/rescue hits.

        Returns dict with hits, result, error, error_type, elapsed, attempted, and
        provider surface telemetry.
        """
        discovery_error: str | None = None
        discovery_error_type: str | None = None
        discovery_elapsed_s: float | None = None
        discovery_attempted = False
        hits: tuple = ()
        discovery_result: Any = None
        _discovery_start: float | None = None
        cache_hit = 0
        query_count = 0
        provider_selected: list[str] = []
        provider_skipped: list[dict] = []
        provider_stub: list[str] = []
        provider_errors: list[dict] = []
        query_variants: list[str] = []
        provider_timeout_count: list[int] = [0]
        provider_import_error_count: list[int] = [0]
        discovery_empty_reason: list[str] = []

        try:
            _discovery_start = time.monotonic()
            discovery_attempted = True
            discovery_result = await safe_wait_for(
                _ASYNC_DISCOVERY_SEARCH(self.query, self.max_results),
                timeout=35.0, label="live_public_discovery",
            )
            discovery_elapsed_s = time.monotonic() - _discovery_start

            cache_hit = int(safe_attr_get(discovery_result, "cache_hit", False))
            query_count = 1

            _extract_provider_surface(
                discovery_result, provider_selected, provider_skipped,
                provider_stub, provider_errors,
                provider_timeout_count, provider_import_error_count,
                discovery_empty_reason,
            )

            # Extract hits from discovery result
            if hasattr(discovery_result, "hits"):
                disc_hits = discovery_result.hits
            elif isinstance(discovery_result, dict):
                disc_hits = discovery_result.get("hits", ())
            else:
                disc_hits = ()

            # Merge bootstrap + rescue hits
            if bootstrap_hits:
                hits = tuple(bootstrap_hits) + disc_hits
                if not disc_hits:
                    # Bootstrap prevented discovery timeout
                    pass
            elif rescue_hits:
                hits = tuple(rescue_hits) + disc_hits
            else:
                hits = disc_hits

            # Extract error
            err_val = discovery_result.get("error") if isinstance(discovery_result, dict) else getattr(discovery_result, "error", None)
            if err_val:
                discovery_error = str(err_val)

            discovery_error_type = classify_discovery_error(
                discovery_error,
                elapsed_s=discovery_elapsed_s,
                timeout_s=35.0,
                hits_count=len(hits),
            )
        except asyncio.CancelledError:
            discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
            discovery_error_type = classify_discovery_error(
                asyncio.CancelledError("cancelled"),
                elapsed_s=discovery_elapsed_s,
                hits_count=0,
            )
            raise
        except Exception as exc:
            discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
            discovery_error = f"discovery_exception:{type(exc).__name__}:{exc}"
            discovery_error_type = classify_discovery_error(
                discovery_error,
                elapsed_s=discovery_elapsed_s,
                hits_count=0,
            )
            hits = ()

        return {
            "hits": hits,
            "result": discovery_result,
            "error": discovery_error,
            "error_type": discovery_error_type,
            "elapsed": discovery_elapsed_s,
            "attempted": discovery_attempted,
            "cache_hit": cache_hit,
            "query_count": query_count,
            "provider_selected": provider_selected,
            "provider_skipped": provider_skipped,
            "provider_stub": provider_stub,
            "provider_errors": provider_errors,
            "query_variants": query_variants,
            "provider_timeout_count": provider_timeout_count,
            "provider_import_error_count": provider_import_error_count,
            "discovery_empty_reason": discovery_empty_reason,
        }

    async def _run_keyword_fallback(self) -> dict:
        """Phase 3: Keyword-based search engine fallback when no hits available.

        Returns dict with hits, candidates_count, bootstrap_order, fetch_attempted,
        fetch_success, errors.
        """
        hits: tuple = ()
        candidates_count = 0
        bootstrap_order = "disabled"
        fetch_attempted = 0
        fetch_success = 0
        errors = 0

        try:
            keyword_hits = await generate_keyword_bootstrap_urls(
                self.query,
                max_urls=_MAX_KEYWORD_BOOTSTRAP_URLS,
            )
            candidates_count = len(keyword_hits)
            if keyword_hits:
                hits = tuple(keyword_hits)
                bootstrap_order = "keyword_bootstrap"
                fetch_attempted = len(keyword_hits)
                fetch_success = len(keyword_hits)
        except Exception:
            errors = 1
            candidates_count = 0

        return {
            "hits": hits,
            "candidates_count": candidates_count,
            "bootstrap_order": bootstrap_order,
            "fetch_attempted": fetch_attempted,
            "fetch_success": fetch_success,
            "errors": errors,
        }


async def _run_academic_lane(store: Any, query: str) -> int:
    """Run academic research lane (Phase 1A)."""
    academic_findings_count = 0
    if store is not None:
        try:
            academic_enabled = LANE_REGISTRY.is_enabled("academic")
            query_lower = query.lower()
            academic_keywords = ["paper", "research", "academic", "scholar", "study", "journal", "citation", "doi", "arxiv", "publication", "conference", "thesis"]
            has_academic_keywords = any(kw in query_lower for kw in academic_keywords)
            deep_research = os.environ.get("HLEDAC_DEEP_RESEARCH", "0").strip().lower() in ("1", "true", "yes", "on")

            if academic_enabled or has_academic_keywords or deep_research:
                from hledac.universal.discovery.academic import ACADEMIC_ENABLED, search_all_academic
                if ACADEMIC_ENABLED:
                    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
                    academic_semaphore = get_semaphore(ConcurrencyCategory.ACADEMIC_SEARCH)
                    async def limited_academic_search():
                        async with academic_semaphore:
                            return await search_all_academic(query, max_results_per_source=10)
                    academic_results = await limited_academic_search()
                    all_findings = []
                    for _source, findings in academic_results.items():
                        all_findings.extend(findings)
                    if all_findings:
                        await store.submit_findings(all_findings)
                        academic_findings_count = len(all_findings)
                        logger.info(f"[F259] Academic lane: {academic_findings_count} findings from {len(academic_results)} sources")
        except Exception as e:
            logger.warning(f"[F259] Academic research lane failed: {e}")
    return academic_findings_count


async def _run_phase1_augmentation(hits: tuple, query: str, store: Any) -> tuple:
    """Phase 1: CT + CC + Pastebin/GitHub in parallel."""
    _original_hit_count = len(hits)

    async def _ct_wrapper():
        try:
            return await _inject_ct_subdomain_hits(hits, query)
        except Exception:
            return hits

    async def _cc_wrapper():
        try:
            return await _inject_commoncrawl_hits(hits, query)
        except Exception:
            return hits

    async def _pastebin_github_wrapper():
        if store is None:
            return 0, 0
        return await _run_pastebin_github_scan(query, store)

    _build_p1 = await parallel(
        [_ct_wrapper(), _cc_wrapper(), _pastebin_github_wrapper()],
        concurrency=4,
        policy="collect",
        ctx="live_public_pipeline:issue32_phase1",
    )
    return _build_p1.ok[0], _build_p1.ok[1], _build_p1.ok[2]


async def _run_pastebin_github_scan(query: str, store: Any) -> tuple[int, int]:
    """Pastebin + GitHub secret scan for domain in query."""
    import re as _re
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    _DOMAIN_ORG_RE = _re.compile(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}")
    try:
        _match = _DOMAIN_ORG_RE.search(query)
        if not _match:
            return 0, 0
        _target = _match.group()
        logger.info(f"[P20] PastebinMonitor targeting: {_target}")

        from hledac.universal.intel.pastebin_monitor import run as _pastebin_run
        _paste_findings = await _pastebin_run(_target)
        _pastebin_count = 0
        if _paste_findings:
            _p20_findings = []
            for _pf in _paste_findings:
                _pf_id = hashlib.sha256(f"{query}\x00{_pf.uri}\x00pastebin".encode()).hexdigest()[:16]
                _p20_findings.append(CanonicalFinding(
                    finding_id=_pf_id,
                    query=query,
                    source_type="pastebin_monitor",
                    confidence=0.6,
                    ts=time.time(),
                    provenance=("pastebin", _pf.source, _target),
                    payload_text=(
                        f"uri={_pf.uri}\n"
                        f"emails={_pf.emails}\n"
                        f"ips={_pf.ip_addresses}\n"
                        f"masked_secrets={_pf.masked_secrets()}\n"
                        f"snippet={_pf.context_snippet[:300]}"
                    ),
                ))
            await store.submit_findings(_p20_findings)
            _pastebin_count = len(_p20_findings)

        _org = _match.group().rsplit(".", 1)[0]
        from hledac.universal.intel.github_secret_scanner import search_org_secrets
        _gh_count = 0
        try:
            _gh_results = await search_org_secrets(_org)
        except Exception:
            _gh_results = []
        if _gh_results:
            _gh_findings = []
            for _gf in _gh_results:
                _gf_id = hashlib.sha256(f"{query}\x00{_gf.file_path}\x00{_gf.pattern}\x00github".encode()).hexdigest()[:16]
                _gh_findings.append(CanonicalFinding(
                    finding_id=_gf_id,
                    query=query,
                    source_type="github_secret_scanner",
                    confidence=0.55,
                    ts=time.time(),
                    provenance=("github", _gf.pattern, _org),
                    payload_text=(
                        f"pattern={_gf.pattern}\n"
                        f"file={_gf.file_path}\n"
                        f"line={_gf.line}\n"
                        f"context={_gf.context[:300]}"
                    ),
                ))
            await store.submit_findings(_gh_findings)
            _gh_count = len(_gh_findings)
        return _pastebin_count, _gh_count
    except Exception as e:
        logging.getLogger("hledac.universal.pipeline.live_public_pipeline").warning("[P20] Pastebin/GitHub scan failed: %s", e)
        return 0, 0


async def _run_onion_phase(hits: tuple, query: str, store: Any) -> int:
    """Phase 2: Onion discovery (serial, data-dependent on Phase 1 hits)."""
    if store is None:
        return 0
    try:
        return await _inject_onion_hits(hits, query, store)
    except Exception as e:
        logger.debug(f"[F193A] Onion discovery wrapper failed: {e}")
        return 0


def _build_discovery_telemetry(
    discovery_result: Any,
    discovery_error: str | None,
    discovery_error_type: str | None,
    discovery_elapsed_s: float | None,
    discovery_attempted: bool,
    public_discovery_cache_hit: int,
    public_discovery_query_count: int,
    hits: tuple,
    pub_bootstrap_order: str,
    pub_bootstrap_prevented_discovery_timeout: bool,
    pub_bootstrap_first_fetch_attempted: bool,
    pub_bootstrap_candidates_count: int,
    pub_bootstrap_fetch_attempted: int,
    pub_bootstrap_fetch_success: int,
    pub_bootstrap_accepted_findings: int,
    pub_bootstrap_errors: int,
    pub_rescue_candidates_count: int,
    pub_rescue_fetch_attempted: int,
    pub_rescue_fetch_success: int,
    pub_rescue_accepted_findings: int,
    pub_rescue_errors: int,
    pub_rescue_order: str,
    keyword_seed_fallback_triggered: bool,
    pub_keyword_bootstrap_candidates_count: int,
    pub_keyword_bootstrap_fetch_attempted: int,
    pub_keyword_bootstrap_fetch_success: int,
    pub_keyword_bootstrap_order: str,
    pub_keyword_bootstrap_errors: int,
    pub_build_success_count: int,
    pub_build_failure_count: int,
    pub_duplicate_count: int,
    pub_provider_selected: list,
    pub_provider_skipped: list,
    pub_provider_stub: list,
    pub_provider_errors: list,
    pub_query_variants: list,
    pub_provider_timeout_count: list,
    pub_provider_import_error_count: list,
    pub_discovery_empty_reason: list,
    public_candidates_discovered: int,
    public_candidates_fetch_attempted: int,
    public_candidates_fetch_success: int,
    public_candidates_parse_success: int,
    public_candidates_pattern_matched: int,
    public_candidates_built: int,
    public_candidates_store_attempted: int,
    public_candidates_stored: int,
    public_candidates_rejected: int,
    stage_failure: str | None = None,
    stage_failure_reason: str | None = None,
) -> dict:
    """Build discovery telemetry dict from collected counters."""
    return {
        "discovery_result": discovery_result,
        "public_stage_failure": stage_failure or ("discovery_empty" if not hits else None),
        "public_stage_failure_reason": stage_failure_reason or (discovery_error if discovery_error else "no URLs returned from discovery"),
        "public_discovery_raw_count": len(hits),
        "public_discovery_deduped_count": public_candidates_discovered,
        "public_discovery_attempted": discovery_attempted,
        "public_discovery_cache_hit": public_discovery_cache_hit,
        "public_discovery_query_count": public_discovery_query_count,
        "public_bootstrap_order": pub_bootstrap_order or "disabled",
        "public_bootstrap_prevented_discovery_timeout": pub_bootstrap_prevented_discovery_timeout,
        "public_bootstrap_first_fetch_attempted": pub_bootstrap_first_fetch_attempted,
        "public_bootstrap_candidates_count": pub_bootstrap_candidates_count,
        "public_bootstrap_fetch_attempted": pub_bootstrap_fetch_attempted,
        "public_rescue_candidates_count": pub_rescue_candidates_count,
        "public_rescue_fetch_attempted": pub_rescue_fetch_attempted,
        "public_rescue_order": pub_rescue_order,
        "keyword_seed_fallback_triggered": keyword_seed_fallback_triggered,
        "public_keyword_bootstrap_candidates_count": pub_keyword_bootstrap_candidates_count,
        "public_keyword_bootstrap_fetch_attempted": pub_keyword_bootstrap_fetch_attempted,
        "public_keyword_bootstrap_fetch_success": pub_keyword_bootstrap_fetch_success,
        "public_keyword_bootstrap_order": pub_keyword_bootstrap_order,
        "public_keyword_bootstrap_errors": pub_keyword_bootstrap_errors,
        "public_build_success_count": pub_build_success_count,
        "public_build_failure_count": pub_build_failure_count,
        "public_duplicate_count": pub_duplicate_count,
        "public_provider_selected": list(pub_provider_selected),
        "public_provider_skipped": list(pub_provider_skipped),
        "public_provider_stub": list(pub_provider_stub),
        "public_provider_errors": list(pub_provider_errors),
        "public_query_variants": list(pub_query_variants),
        "public_provider_timeout_count": pub_provider_timeout_count[0],
        "public_provider_import_error_count": pub_provider_import_error_count[0],
        "public_discovery_empty_reason": pub_discovery_empty_reason[0] if pub_discovery_empty_reason else "",
        "discovery_error_type": discovery_error_type or "",
        "discovery_elapsed_s": round(discovery_elapsed_s, 3) if discovery_elapsed_s else None,
        "public_candidates_discovered": public_candidates_discovered,
        "public_candidates_fetch_attempted": public_candidates_fetch_attempted,
        "public_candidates_fetch_success": public_candidates_fetch_success,
        "public_candidates_parse_success": public_candidates_parse_success,
        "public_candidates_pattern_matched": public_candidates_pattern_matched,
        "public_candidates_built": public_candidates_built,
        "public_candidates_store_attempted": public_candidates_store_attempted,
        "public_candidates_stored": public_candidates_stored,
        "public_candidates_rejected": public_candidates_rejected,
    }


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

MAX_EXTRACTED_TEXT_CHARS: int = 200_000
"""Hard cap on extracted text size per page."""

MAX_METADATA_PREPEND_CHARS: int = 500
"""Max chars of title+snippet prepended to extracted text for pattern scan context."""

_SOURCE_TYPE: str = "live_public_pipeline"
"""source_type value for all findings produced by this pipeline."""

_PUBLIC_SOURCE_TYPE: str = "public"
"""source_type value for public-surface findings from bootstrap/content-only pages (F226B)."""

_REPORT_SOURCE_TYPE: str = "report"
"""source_type value for generated OSINT reports."""

_DEFAULT_CONFIDENCE: float = 0.8

# P6: Top results for report generation
_REPORT_TOP_N: int = 5
"""Number of top results to include in OSINT report."""
"""Confidence for pipeline findings — executed but unverified."""

_FINDING_ID_CONTEXT_RADIUS: int = 100
"""Character radius around pattern hit for payload_text context window."""

# Sprint F150I: tier thresholds (additive, no new framework)
_QUALITY_TIER_VERY_GOOD = "very_good"
_QUALITY_TIER_GOOD = "good"
_QUALITY_TIER_OK = "ok"
_QUALITY_TIER_WEAK = "weak_low_signal"
_QUALITY_TIER_SKIP = "SKIP_WEAK"

# Sprint F161B: conversion truth consolidation
# Changes:
# - _compute_page_usable_fields: distinguish false-positive discovery from structural waste
# - _score_page_quality: pre-fetch skip for extremely low text BEFORE budget spent
# - New derived fields: discovery_false_positive, waste_category, structural_quality
# - Bounded: all additive, backward-compatible, M1-safe

_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3

# Adaptive fetch budget tiers: multiplier on base fetch_timeout_s
_FETCH_BUDGET_STRONG: float = 1.25   # very_good or discovery_score >= 0.7
_FETCH_BUDGET_NORMAL: float = 1.0    # ok, good
_FETCH_BUDGET_WEAK: float = 0.65     # weak_low_signal, low discovery score
_FETCH_BUDGET_SKIP: float = 0.0       # SKIP_WEAK — dead until Fix A in F150J

# Sprint F161B: pre-fetch text-length gate — BEFORE budget is spent
# Previously this check happened post-fetch in _score_page_quality (wasteful)
_PRE_FETCH_TEXT_MIN_CHARS: int = 80  # F275: lowered from 150 to catch metadata-rich thin pages
"""Minimum extracted text chars to consider fetch worthwhile."""

# Sprint F163B: low-entropy gate — detect repetitive placeholder noise
_LOW_ENTROPY_UNIQUE_WORD_RATIO: float = 0.25

# Sprint F188B: CT winner slice — bounded CT subdomain injection
_CT_SUBDOMAIN_BOUND: int = 10
"""Max CT subdomains to inject as synthetic discovery hits."""
_CT_SUBDOMAIN_SCORE: float = 0.85
"""Discovery score assigned to CT-synthesized hits (high confidence)."""
_CT_QUERY_IS_DOMAIN_RE: re.Pattern = re.compile(r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$")
"""Regex to detect domain-like query strings suitable for CT subdomain lookup."""
_CC_QUERY_IS_DOMAIN_RE: re.Pattern = re.compile(
    r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$"
    r"|^(?:site|domain):"
)
"""Regex for CommonCrawl CDX lookup — supports wildcards and site:/domain: operators."""
"""Regex to detect domain-like query strings suitable for CT subdomain lookup."""

# Sprint F161B: discovery false-positive band — legitimate signal but no conversion
_DISCOVERY_FALSE_POSITIVE_THRESHOLD: float = 0.5
"""Discovery score above this with zero patterns = false positive, not waste."""

# Sprint F150J: pre-fetch skip threshold — below this score with no strong signal → SKIP tier
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
"""If discovery_score is below this AND no strong signal, skip fetch entirely."""

# Sprint F217C: Deterministic bootstrap URL generator
# Bounded, no brute force, no wordlists, no JS, no stealth.
_MAX_BOOTSTRAP_URLS: int = 5
"""Max bootstrap URLs per query (domain-sourced)."""
_BOOTSTRAP_DEFAULT_URLS: list[str] = [
    "",           # https://domain/
    "/www.",      # https://www.domain/
    "/.well-known/security.txt",   # deterministic security policy endpoint
    "/robots.txt",                  # robots directive
    "/sitemap.xml",                 # sitemap reference
]
"""Ordered list of URL path templates for deterministic bootstrap."""

# Sprint F220C: Public Provider Rescue for non-domain threat queries
# Known public CTI/news search URLs — lightweight, no new dependency.
# Mapped to (name, base_url_format) tuples. Max 10.
_RESGUE_SOURCE_CANDIDATES: list[tuple[str, str]] = [
    # F273: Expanded OSINT rescue sources (was 5, now 12)
    # Threat intelligence aggregators — open-access only (no login/API key required)
    ("ThreatFox", "https://threatfox.abuse.ch/browse.php?search="),
    # Ransomware-specific trackers — open-access
    # ("Ransomware Tracker", "https://ransomwaretracker.xyz/"),  # OFFLINE 2026-06 -- NS_ERROR_UNKNOWN_HOST
    ("ID Ransomware", "https://id-ransomware.malwarehunterteam.com/"),
    # General CTI/news — open-access
    ("BleepingComputer", "https://www.bleepingcomputer.com/search/?search="),
    ("The Hacker News", "https://thehackernews.com/search?q="),
    ("Krebs on Security", "https://krebsonsecurity.com/?s="),
    ("CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search="),
    # F273: Additional open-access OSINT sources
    ("URLhaus", "https://urlhaus.abuse.ch/"),  # Malware URL database
    ("AlienVault OTX", "https://otx.alienvault.com/api/v1/search?q="),  # OTX pulse search
    ("Maltiverse", "https://maltiverse.com/search?keyword="),  # Malware enrichment
    ("Onyphe", "https://www.onyphe.io/search/?query="),  # Cyber threat intelligence
    ("GreyNoise", "https://greynoise.io/viz/share/"),  # Internet noise scanner
    ("AbuseIPDB", "https://www.abuseipdb.com/check/"),  # IP abuse database
]
"""Static rescue source list for non-domain threat/malware/ransomware queries."""


# -----------------------------------------------------------------------------
# F221H: Public Discovery Relevance / Shopping Noise Filter
# -----------------------------------------------------------------------------

# Blocked domain patterns for shopping/e-commerce noise
_SHOPPING_NOISE_DOMAINS: tuple[str, ...] = (
    "trendyol.com",
    "pazarama.com",
    "amazon.com.tr",
    "n11.com",
    "hepsiburada.com",
    "gittigidiyor.com",
    "cimri.com",
    "akakce.com",
)

# Blocked URL path patterns for e-commerce/shopping/category pages
# Used for non-threat queries (domain-only blocking for threat queries)
_SHOPPING_NOISE_PATHS: tuple[str, ...] = (
    "/gp/bestsellers/",
    "/gp/bestsellers",
    "/bestsellers/",
    "/best-seller",
    "/matkap",
    "/category/",
    "/product/",
    "/products/",
    "/shop/",
    "/shopping/",
    "/cart/",
    "/checkout/",
    "/buy/",
    "/sale/",
    "/offers/",
    "/home-improvement",
    "/home-and-garden",
)

# Strict subset: only unambiguous e-commerce checkout/transaction paths
# Used for threat queries to avoid over-filtering legitimate CTI content
# that happens to have generic paths like /product/ or /category/
_SHOPPING_NOISE_PATHS_STRICT: tuple[str, ...] = (
    "/cart/",
    "/checkout/",
    "/buy/",
    "/sale/",
    "/offers/",
)

# CTI/news domains that are always allowed (override noise filter for threat queries)
_CTI_NEWS_ALLOWED_DOMAINS: tuple[str, ...] = (
    "cisa.gov",
    "krebsonsecurity.com",
    "bleepingcomputer.com",
    "thehackernews.com",
    "abuse.ch",
    "threatfox.abuse.ch",
    # "ransomwaretracker.xyz",  # OFFLINE 2026-06 -- NS_ERROR_UNKNOWN_HOST
    "id-ransomware.malwarehunterteam.com",
    "malwarehunterteam.com",
    "cyberscoop.com",
    "darkreading.com",
    "threatpost.com",
    "therecord.media",
    "securityweek.com",
    "inforisktoday.com",
    "helpnetsecurity.com",
    "ransomwarewiki.com",
    "cybercrime-tracker.net",
    "malware-traffic-analysis.net",
    "unit42.paloaltonetworks.com",
    "securityaffairs.com",
    "thecyberwire.com",
    "bleepinguid.com",
    "ransomware.live",
)


def _is_shopping_noise_url(url: str, is_threat_query: bool) -> tuple[bool, str]:
    """Detect if a URL is shopping/e-commerce noise.

    For threat queries: blocks obvious shopping/ecommerce/category pages.
    For non-threat queries: less strict, only blocks domain-level matches.

    Returns:
        Tuple of (is_noise, reason) where reason is one of:
        - "public_noise_shopping" — blocked shopping domain
        - "public_noise_unrelated_marketplace" — blocked marketplace
        - "public_relevance_pass" — URL is relevant

    """
    if not url:
        return False, "public_relevance_pass"

    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()

    # F221H: CTI/news domains always pass (override noise filter)
    for allowed_domain in _CTI_NEWS_ALLOWED_DOMAINS:
        if netloc.endswith(allowed_domain) or netloc == allowed_domain:
            return False, "public_relevance_pass"

    # Check if domain is in blocked shopping domains
    for blocked_domain in _SHOPPING_NOISE_DOMAINS:
        if netloc.endswith(blocked_domain) or netloc == blocked_domain:
            return True, "public_noise_shopping"

    # For threat queries, only block strict checkout/transaction paths
    # to avoid over-filtering legitimate CTI content with generic paths
    if is_threat_query:
        for blocked_path in _SHOPPING_NOISE_PATHS_STRICT:
            if blocked_path in path:
                return True, "public_noise_unrelated_marketplace"
    # Non-threat queries: no path-based blocking (only domain-level)

    return False, "public_relevance_pass"


def _filter_public_noise(
    hits: list | tuple, is_threat_query: bool
) -> tuple[list, list[tuple[str, str]]]:
    """Filter shopping/e-commerce noise from public discovery hits.

    For threat queries: blocks shopping domains AND path patterns.
    For non-threat queries: only blocks known shopping domains.

    Returns:
        Tuple of (filtered_hits, rejected_reasons) where rejected_reasons
        is list of (url, reason) for each rejected hit.

    """
    filtered: list = []
    rejected: list[tuple[str, str]] = []

    for hit in hits:
        url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else "")
        if not url:
            filtered.append(hit)
            continue

        is_noise, reason = _is_shopping_noise_url(url, is_threat_query)
        if is_noise:
            rejected.append((url, reason))
        else:
            filtered.append(hit)

    return filtered, rejected


def _strip_query_prefix(q: str) -> str:
    """Strip site:, domain:, url:, asn:, ip:, vpn:, tor: prefixes."""
    for prefix in ("site:", "domain:", "url:", "asn:", "ip:", "vpn:", "tor:"):
        if q.lower().startswith(prefix):
            return q[len(prefix):].strip()
    return q

def _check_ip_cve(q: str) -> bool:
    """Check if query is IP address or CVE pattern."""
    import re as _re
    if _re.match(r"^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$|^[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}(?::\d{1,3})?(?:\/\d{1,2})?$", q): return True
    if _re.match(r"^CVE-\d{4}-\d{4,}$", q, _re.IGNORECASE): return True
    return False

def _check_threat_patterns(q: str, first_token: str) -> bool:
    """Check ransomware/malware/threat actor patterns."""
    import re as _re
    THREAT_PAT = _re.compile(r"^(?:lockbit|conti|revil|clop|darkside|blackcat|alphv|ransomware|apt[_\s]?\d+|apt[_-]\w+|sidecopy|callback|triangle|temp|wanna[_\s]?cry|wannacry|petya|notpetya|badrabbit|emotet|trickbot|cobalt[_\s]?strike|koadic|metasploit|fin7|carbanak|finacrypt|prodaft|labyrinth|zCrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic)$", _re.IGNORECASE)
    if THREAT_PAT.match(q): return True
    EXTENDED_PAT = _re.compile(r"^(?:meterpreter|sandworm|lazarus|log4shell|finacrypt|prodaft|labyrinth|zcrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic|sidecopy|callback|triangle|temp|sofacy|平原)$", _re.IGNORECASE)
    for token in _re.split(r"[\s\-_]+", q):
        if len(token) >= 4 and THREAT_PAT.match(token): return True
        if len(token) >= 3 and EXTENDED_PAT.match(token): return True
    if first_token and (THREAT_PAT.match(first_token)): return True
    return False

def _check_generic_keywords(q: str, first_token: str) -> bool:
    """Check generic threat/OSINT keywords."""
    import re as _re
    THREAT_KW = _re.compile(r"^(?:ransomware|malware|threat[_-]?actor|cobalt[_\s]?strike|breach|exploit|0day|zero[_\s]?day|vulnerability|phishing|spam|botnet|trojan|rootkit|keylogger|Ransomware|Malware|ThreatActor|CVE|APT)$", _re.IGNORECASE)
    OSINT_KW = _re.compile(r"^(?:osint|osint infrstructure|infrastructure|telemetry|leak|dark[_\s]?web|exposure|credential|breach|darkweb|onion|leakdb|intel|threat|hunting|recon|scanning|fingerprint|iot|ics|scada)$", _re.IGNORECASE)
    if THREAT_KW.match(q) or OSINT_KW.match(q) or (first_token and OSINT_KW.match(first_token)): return True
    return False

def _check_multi_word_patterns(q: str) -> bool:
    """Check multi-word OSINT/threat compound patterns."""
    import re as _re
    MULTI_PAT = _re.compile(r"(?:ransomware\s+(?:threat|intelligence|leak|attack|group|operation)|threat\s+(?:intelligence|actor|actor\s+group|intel)|malware\s+(?:analysis|sample|family|variant)|data\s+(?:breach|leak|exposure|dump)|dark\s+web|deep\s+web|surface\s+web|credential\s+(?:dump|leak|breach|stuffing)|osint\s+(?:reconnaissance|recon|reconnaissance|automation)|vulnerability\s+(?:scan|scanner|assessment|intelligence)|threat\s+hunting|incident\s+response|digital\s+forensics|infosec|cybersecurity\s+intelligence|iosint|geoint|fintech\s+threat|bloc\s+threat|apts|advanced\s+persistent|supply\s+chain\s+(?:attack|threat)|zero\s+day|zero-day|exploit\s+kit|phishing\s+(?:campaign|kit|template)|botnet\s+(?:infection|command|控|controller)|ransomware\s+as\s+a\s+service|raas|ransomware\s+gang|cyber\s+(?:attack|threat|crime|criminal|espionage)|nation[\s_-]state\s+(?:threat|apt|actor|hacker)|state[\s_-]sponsored|apt[\s_-]\w+)", _re.IGNORECASE)
    return bool(MULTI_PAT.search(q))

def _is_threat_query(query: str) -> bool:
    """Detect if query is a non-domain threat/malware/ransomware/entity query."""
    if not query or not query.strip(): return False
    q, first_token = _strip_query_prefix(query.strip()), query.split()[0] if query else ""
    return _check_ip_cve(q) or _check_threat_patterns(q, first_token) or _check_generic_keywords(q, first_token) or _check_multi_word_patterns(q)


def generate_rescue_urls(query: str, max_urls: int = 8) -> list[DiscoveryHit]:
    """Generate lightweight rescue DiscoveryHits for non-domain threat queries.

    Sprint F220C: When bootstrap generates zero URLs (non-domain query),
    and the query appears to be a threat/malware/ransomware/entity search,
    generate rescue candidate hits from static CTI/news search URLs.

    Behavior:
      - Returns up to max_urls DiscoveryHit from static source list
      - Each hit has source="rescue", score=0.7, reason="rescue_candidate"
      - Does NOT perform network I/O — pure synchronous URL construction
      - Fail-safe: returns empty list for domain-like queries

    Args:
        query: The original OSINT query string.
        max_urls: Maximum number of rescue hits to return (default 5).

    Returns:
        List of DiscoveryHit objects from rescue sources. Empty if
        query looks like a domain or rescue sources exhausted.

    """
    if not query or max_urls < 1:
        return []
    # P0-2: Also trigger rescue for OSINT threat/discovery queries (non-domain but
    # rich search terms) — _is_threat_query now covers OSINT keywords.
    if not _is_threat_query(query):
        return []

    hits: list[DiscoveryHit] = []
    for name, base_url in _RESGUE_SOURCE_CANDIDATES[:max_urls]:
        url = f"{base_url}{urllib.parse.quote(query.strip())}"
        hits.append(DiscoveryHit(
            query=query,
            title=f"Rescue: {name}",
            url=url,
            snippet=f"Rescue search via {name}: {query}",
            score=0.70,
            reason="rescue_candidate",
            rank=-1,
            source="rescue",
            retrieved_ts=0.0,
        ))
    return hits


def generate_bootstrap_urls(query: str, max_urls: int = _MAX_BOOTSTRAP_URLS) -> list[str]:
    """Generate deterministic bootstrap URLs for domain/URL queries.

    Bounded: at most max_urls URLs returned.
    Fail-safe: returns empty list for non-domain queries or parse errors.
    No network I/O — pure synchronous URL construction.

    Bootstrap targets (in order):
      1. https://domain/
      2. https://www.domain/
      3. https://domain/.well-known/security.txt
      4. https://domain/robots.txt
      5. https://domain/sitemap.xml

    Args:
        query: The original OSINT query string.
        max_urls: Maximum number of bootstrap URLs to return (default 5).

    Returns:
        List of absolute URL strings (max max_urls). Empty list if query
        is not a domain or URL cannot be parsed.

    """
    if not query or max_urls < 1:
        return []

    # Strip common prefix operators used in OSINT queries
    clean_query = query.strip()
    for prefix in ("site:", "domain:", "url:"):
        if clean_query.lower().startswith(prefix):
            clean_query = clean_query[len(prefix):].strip()
            break

    # Attempt to extract a domain from the query
    domain = _extract_domain_from_query(clean_query)
    if not domain:
        return []

    # Build bootstrap URL list (paths in order of priority)
    paths = _BOOTSTRAP_DEFAULT_URLS[:max_urls]
    urls: list[str] = [
        f"https://www.{domain}" if path == "/www."
        else f"https://{domain}{path}" if path
        else f"https://{domain}"
        for path in paths
    ]
    return urls


# Sprint F223C: Bounded seed_context bootstrap for nonfeed_diagnostic profile
_MAX_SEED_CONTEXT_BOOTSTRAP: int = 10  # hard cap

def generate_seed_context_bootstrap_urls(seed_context: Any, max_candidates: int = _MAX_SEED_CONTEXT_BOOTSTRAP) -> list[str]:  # noqa: E501
    """Generate deterministic bootstrap URLs from NonfeedSeedContext.

    Bounded: at most max_candidates URLs returned.
    Fail-safe: returns empty list for None seed_context or parse errors.
    No network I/O — pure synchronous URL construction.
    No browser, no recursive crawl.

    Bootstrap sources (in priority order):
      1. seed_context.domains → https://domain/ (top 5 only)
      2. seed_context.urls → as-is (top 5 only)

    Args:
        seed_context: NonfeedSeedContext with domains/urls tuples.
        max_candidates: Maximum number of URLs to return (default 10).

    Returns:
        List of absolute URL strings (max max_candidates). Empty list if
        seed_context is None or has no domains/urls.

    """
    if not seed_context or max_candidates < 1:
        return []

    urls: list[str] = []
    _has_domains = bool(getattr(seed_context, "domains", ()))
    _has_urls = bool(getattr(seed_context, "urls", ()))
    _both_sources = _has_domains and _has_urls

    # Split budget: if both sources present, split evenly (5+5 for max=10)
    # If only one source, use full budget for that source
    if _both_sources:
        _max_per_source = (max_candidates + 1) // 2
    else:
        _max_per_source = max_candidates

    # Domains: construct root URL for each domain (top N)
    if _has_domains:
        for domain in list(getattr(seed_context, "domains", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            # Basic domain validation — skip IPs and obvious noise
            if not domain or "." not in domain:
                continue
            try:
                # Ensure proper URL form
                domain = domain.lower().strip()
                if not domain.startswith(("http://", "https://")):
                    urls.append(f"https://{domain}")
                else:
                    urls.append(domain)
            except Exception:
                continue

    # URLs: use as-is (top N)
    if _has_urls:
        for url in list(getattr(seed_context, "urls", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not url:
                continue
            try:
                url_str = str(url).strip()
                if not url_str.startswith(("http://", "https://")):
                    continue  # skip bare domains that would duplicate domain entries
                urls.append(url_str)
            except Exception:
                continue

    return urls[:max_candidates]


# =============================================================================
# 3.3 Public Discovery Bootstrap — Keyword-based search engine fallback
# Triggered when no URLs discovered from query (bootstrap + rescue both empty)
# =============================================================================

_PUBLIC_BOOTSTRAP_SEARCH_ENGINES: tuple[str, ...] = ("duckduckgo", "yahoo", "bing", "startpage")
"""Fallback search engine order for keyword-based discovery bootstrap."""

_MAX_KEYWORD_BOOTSTRAP_URLS: int = 10  # hard cap per engine


async def generate_keyword_bootstrap_urls(
    query: str,
    max_urls: int = _MAX_KEYWORD_BOOTSTRAP_URLS,
) -> list[DiscoveryHit]:
    """Keyword-based search engine bootstrap — falls back through multiple engines.

    3.3 Public Discovery Bootstrap:
      Triggered when bootstrap + rescue + seed_context all returned zero URLs.
      Runs the original query against DuckDuckGo → Yahoo → Bing → Startpage
      in order, returning hits from the first engine that returns results.

    Bounded: at most max_urls DiscoveryHit per successful engine.
    Fail-safe: returns empty list for any error (network, import, timeout).
    Always-on: no feature flag — this is the final fallback before empty result.

    Args:
        query: The original OSINT query string.
        max_urls: Maximum hits to return (default 10, hard cap per engine).

    Returns:
        List of DiscoveryHit objects from first responding search engine.
        Empty list if all engines fail or return no hits.

    """
    if not query or not query.strip():
        return []

    for engine in _PUBLIC_BOOTSTRAP_SEARCH_ENGINES:
        try:
            raw_results = await _search_multi_engine_bootstrap(
                query,
                max_results=max_urls,
            )
            if not raw_results:
                continue

            hits: list[DiscoveryHit] = []
            for i, item in enumerate(raw_results[:max_urls]):
                url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                snippet = item.get("snippet", "") if isinstance(item, dict) else getattr(item, "snippet", "")
                if not url:
                    continue
                hits.append(DiscoveryHit(
                    query=query,
                    title=title or f"{engine.capitalize()} result {i+1}",
                    url=url,
                    snippet=snippet or f"Keyword bootstrap via {engine}: {query}",
                    score=0.75,
                    reason=f"keyword_bootstrap_{engine}",
                    rank=i,
                    source=engine,
                    retrieved_ts=time.time(),
                ))

            if hits:
                return hits

        except Exception:
            # Fail-safe: try next engine
            continue

    return []


def _strip_prefix(q: str) -> str:
    """Strip site:, domain:, url: prefixes."""
    for prefix in ("site:", "domain:", "url:"):
        if q.lower().startswith(prefix):
            return q[len(prefix):]
    return q

def _extract_host_from_url(q: str) -> str | None:
    """Extract host from URL using urllib."""
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(q)
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return None

def _normalize_domain(q: str) -> str | None:
    """Normalize domain string: strip ports, www, wildcards."""
    q = q.rstrip("/")
    if "/" in q and "://" in q:
        if host := _extract_host_from_url(q): q = host
    if ":" in q: q = q.rsplit(":", 1)[0]
    if q.lower().startswith("www."): q = q[4:]
    if q.startswith("*."): q = q[2:]
    return q

def _is_valid_domain(q: str) -> bool:
    """Validate domain format."""
    import re as _re
    if not q or "." not in q: return False
    if _re.match(r"^\d{1,3}(\.\d{1,3}){3}$", q): return False
    if not _re.match(r"^[a-zA-Z0-9.\-]+$", q): return False
    if len(q.rsplit(".", 1)[-1]) < 2: return False
    return True

def _extract_domain_from_query(query: str) -> str | None:
    """Extract domain from OSINT query string."""
    if not query: return None
    candidates = [query]
    if (" " in query or "\t" in query) and (first := query.strip().split()[0]) and first != query:
        candidates.append(first)
    for candidate in candidates:
        q = _normalize_domain(_strip_prefix(candidate))
        if q and _is_valid_domain(q): return q.lower()
    return None


# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------


# Sprint F193B: Explicit fetch policy — policy-driven JS/DoH/stealth, not dormant defaults


class FetchPolicy(Struct, frozen=True):
    """Bounded fetch policy for canonical public sprint."""

    use_js: bool = False
    use_doh: bool = False
    use_stealth: bool = False

    @classmethod
    def default(cls) -> FetchPolicy:
        """Return default fetch policy with no special options."""
        return cls()

    @classmethod
    def js_capable(cls) -> FetchPolicy:
        """Return fetch policy with JavaScript rendering enabled."""
        return cls(use_js=True)

    @classmethod
    def tor_like(cls) -> FetchPolicy:
        """Return stealth-like fetch policy with DoH and stealth enabled."""
        return cls(use_doh=True, use_stealth=True)




def _compute_fetch_policy(
    url: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    strong_signal: bool,
) -> FetchPolicy:
    """Sprint F193B: Policy-driven fetch policy — JS/DoH/stealth driven by signal strength and URL class, not just dormant defaults.

    Policy rules:
    - discovery_score >= 0.7 OR strong_signal → use_js (JS-heavy page likely)
    - Onion/I2P/Freenet → tor_like policy (use_doh + use_stealth)
    - discovery_reason contains 'ct_' → DoH (accuracy for CT-log sources)
    - discovery_score >= 0.5 with moderate signal → use_doh only
    - everything else → default (plain fetch)

    Bounded: no network calls, no external state.
    """
    if ".onion" in url or ".i2p" in url or ".b32.i2p" in url or ".freenet" in url:
        return FetchPolicy.tor_like()

    if discovery_score is not None and discovery_score >= 0.7:
        return FetchPolicy.js_capable()
    if strong_signal:
        return FetchPolicy.js_capable()
    if discovery_reason and "ct_" in discovery_reason:
        return FetchPolicy(use_doh=True)
    if discovery_score is not None and discovery_score >= 0.5:
        return FetchPolicy(use_doh=True)
    return FetchPolicy.default()


# ---------------------------------------------------------------------------
# F232: Provider surface telemetry extraction
# ---------------------------------------------------------------------------


def _get_provider_status_debug(discovery_result, results_to_process):
    """Extract provider_status_debug from results."""
    for _res in results_to_process:
        _psd = getattr(_res, "provider_status_debug", None) or (_res.get("provider_status_debug") if isinstance(_res, dict) else None)
        if _psd and isinstance(_psd, list):
            return _psd
    return None

def _process_provider_status_entry(entry, selected_out, skipped_out, stub_out):
    """Process a single provider status entry."""
    p = entry.get("provider", "") if isinstance(entry, dict) else getattr(entry, "provider", "")
    state = entry.get("state") if isinstance(entry, dict) else getattr(entry, "state", None)
    if hasattr(state, "value"):
        state = state.value
    state_str = str(state) if state is not None else ""
    if entry.get("selected"):
        selected_out.append(p)
    else:
        skipped_out.append({"provider": p, "reason": entry.get("reason", "") if isinstance(entry, dict) else ""})
    if state_str == "advisory_stub":
        stub_out.append(p)

def _classify_error(error_str, error_type, discovery_result):
    """Classify error and return (empty_reason, timeout_inc, import_inc)."""
    if error_type == "timeout" or "timeout" in error_str.lower():
        return "provider_timeout", 1, 0
    elif error_type == "provider_exception" or "exception" in error_str.lower():
        return "provider_unavailable", 0, 1
    elif error_str == "empty_query":
        return "query_builder_empty", 0, 0
    elif not hits_from_result(discovery_result):
        return "provider_returned_zero", 0, 0
    return None, 0, 0

def _get_results_to_process(discovery_result):
    """Get list of results to process."""
    if isinstance(discovery_result, list): return discovery_result
    return [getattr(discovery_result, "result", discovery_result)]

def _extract_provider_errors(results_to_process):
    """Extract error type and provider name from results."""
    for res in results_to_process:
        if getattr(res, "error", None):
            return getattr(res, "error_type", None) or "", getattr(res, "provider_name", None) or ""
    return "", ""

def _extract_provider_surface(discovery_result, selected_out, skipped_out, stub_out, errors_out, timeout_count_out, import_error_count_out, empty_reason_out) -> None:
    """Extract provider surface telemetry from a DiscoveryBatchResult."""
    result_error = getattr(discovery_result, "error", None) or (discovery_result.get("error") if isinstance(discovery_result, dict) else None)
    error_str = str(result_error) if result_error else ""
    results_to_process = [r for r in _get_results_to_process(discovery_result) if r is not None]
    psd = _get_provider_status_debug(discovery_result, results_to_process)
    if psd and isinstance(psd, list):
        for entry in psd: _process_provider_status_entry(entry, selected_out, skipped_out, stub_out)
        first = psd[0] if psd else {}
        variants = first.get("query_variants", []) if isinstance(first, dict) else getattr(first, "query_variants", [])
        if hits := hits_from_result(discovery_result):
            if q := getattr(hits[0], "query", "") or "": variants.append(q)
    error_type, provider_name = _extract_provider_errors(results_to_process) if isinstance(discovery_result, list) else (getattr(discovery_result, "error_type", None) or "", getattr(discovery_result, "provider_name", None) or "")
    if error_str:
        errors_out.append({"provider": provider_name, "error": error_str, "error_type": error_type})
        empty_reason, t_inc, i_inc = _classify_error(error_str, error_type, discovery_result)
        timeout_count_out[0] += t_inc; import_error_count_out[0] += i_inc
        if empty_reason and not empty_reason_out: empty_reason_out.append(empty_reason)
    if not selected_out and not psd and not empty_reason_out: empty_reason_out.append("no_provider_selected")
    if not hits_from_result(discovery_result) and not empty_reason_out: empty_reason_out.append("provider_returned_zero")


def hits_from_result(discovery_result) -> tuple:
    """Extract hits from DiscoveryBatchResult or dict."""
    if hasattr(discovery_result, "hits"):
        return discovery_result.hits
    if isinstance(discovery_result, dict):
        return discovery_result.get("hits", ())
    return ()


class PipelinePageResult(Struct, frozen=True):
    """Result of processing a single discovered page."""

    url: str
    fetched: bool
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    error: str | None = None
    quality_reason: str | None = None  # why page was good/weak/skipped
    discovery_score: float | None = None  # signal strength from discovery hit
    discovery_reason: str | None = None  # reason from discovery hit
    discovery_signal: bool = False  # True if hit had score >= 0.3 or reason
    # Sprint F150L: usable-value layer — conversion story per page
    usable_signal: bool = False  # True if page converted to usable value
    value_tier: str = "none"  # high | medium | low | waste
    resolution_reason: str = ""  # why this page resolved the way it did
    # Sprint F161B: conversion truth surfaces
    discovery_false_positive: bool = False  # True if discovery signal was legitimate but page converted to waste
    waste_category: str = ""  # "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: str = ""  # "" | "healthy" | "thin" | "dead"
    # Sprint F170D: fetch accessibility truth — failure_stage from FetchResult
    failure_stage: str | None = None  # validation | connection | tls | http | body | size
    # Sprint F171A: redirect truth surfaces — redirect-induced non-content vs weak conversion
    redirected: bool = False  # True when page was redirected (final_url != original_url)
    redirect_target: str | None = None  # redirect destination URL when redirected=True
    # F207F: PUBLIC Yield — per-page JS/feed skip telemetry
    js_renderer_skipped_reason: str | None = None  # xml_or_feed_url | xml_recovered | browser_unavailable
    fetch_blocked_reason: str | None = None  # uma_memory | quality_skip (page not fetched due to gate)
    # F207J-C: PUBLIC Acceptance — per-page acceptance rejection reason
    # None = accepted | rejection reason string
    rejection_reason: str | None = None
    # F208G-A: PUBLIC Yield Taxonomy — canonical terminal classification per URL
    # None = still processing | "accepted" | "skipped_*" | "rejected_*"
    terminal_reason: str | None = None
    # F226B: PUBLIC acceptance uplift — per-page duplicate signal for public_surface findings
    public_surface_dup: bool = False
    # F231A: PUBLIC Candidate Ledger — stage progression per URL
    # build_attempted: page passed quality gate and entered finding-build phase
    build_attempted: bool = False


class PipelineRunResult(Struct, frozen=True):
    """Top-level result of a full pipeline run."""

    query: str
    discovered: int
    fetched: int
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    patterns_configured: int
    pages: tuple[PipelinePageResult, ...]
    error: str | None = None
    # Sprint F150I: branch economics observability (additive)
    strong_pages: int = 0  # very_good tier, high yield
    weak_pages_skipped: int = 0  # SKIP_WEAK early exits (Fix B: was error-based, now quality_reason-based)
    low_value_fetches: int = 0  # fetched but matched nothing + poor quality
    # Sprint F150J: derived value counters
    discovery_strong_content_weak: int = 0  # discovery signal but zero pattern yield
    discovery_and_content_strong: int = 0  # both discovery signal and pattern yield
    # Sprint F150K: additional derived economics signals (additive)
    discovery_squandered: int = 0  # strong discovery hit but page quality weak
    noise_fetch_ratio: float = 0.0  # ratio of fetched pages that yielded zero patterns
    corroboration_vs_burn: float = 0.0  # corroboration signal vs pure budget burn
    public_next_action: str = ""  # operator-facing one-liner next action hint
    public_confidence_note: str = ""  # operator-facing confidence note
    # Sprint F150J: condensed public-branch verdict (additive dict)
    public_branch_verdict: dict = {}
    # Sprint F150L: usable-value run-level aggregates
    usable_findings_ratio: float = 0.0  # stored_findings / max(discovered, 1)
    discovery_to_findings_efficiency: float = 0.0  # discovery_and_content_strong / max(discovered, 1)
    quality_mix: str = ""  # high|medium|low|waste composition summary
    public_proof_grade: str = ""  # proof quality of the public branch run
    public_value_density: float = 0.0  # stored_findings / max(fetched, 1)
    top_waste_pattern: str = ""  # dominant reason pages went to waste (heuristic)
    # Sprint F161B: conversion truth run-level aggregates
    discovery_false_positive_count: int = 0  # pages with discovery signal but no conversion
    waste_category_counts: dict = {}  # {"structural": N, "signalless": N, "false_positive": N, "error": N}
    structural_health_ratio: float = 0.0  # fraction of fetched pages with structural_quality=healthy
    # Sprint F162B: factual value density + clean waste code
    factual_value_density: float = 0.0  # stored / fetched (real conversion density)
    run_waste_pattern_code: str = ""   # dominant waste category clean code
    waste_reason_breakdown: str = ""   # waste category distribution
    # Sprint F163B: backend degradation flag — true when fetch errors dominate discovery output
    backend_degraded: bool = False
    # Sprint F170D: lower-layer truth consumption — discovery block / fetch accessibility
    # None | "uma_emergency_abort" | "backend_error_no_fallback" | "backend_error_fallback_failed"
    public_discovery_blocker: str | None = None
    # True when any page had fetch accessibility failure (DNS/TLS/connection/timeout)
    public_fetch_accessibility_blocker: bool = False
    # None | "primary_failed_fallback_succeeded" | "primary_failed_fallback_failed" | "no_fallback_needed"
    public_discovery_fallback_state: str | None = None
    # Dominant failure mode across all pages and discovery
    dominant_public_failure_mode: str | None = None
    # Sprint F213B: PUBLIC stage accounting — actionable failure classification
    public_stage_failure: str | None = None  # discovery_empty | fetch_zero | None
    public_stage_failure_reason: str | None = None  # human-readable reason
    # Sprint F213B: PUBLIC discovery stage counters
    public_discovery_attempted: bool = False  # discovery was called
    public_discovery_raw_count: int = 0  # raw URLs from discovery (before dedup)
    public_discovery_deduped_count: int = 0  # URLs after dedup (candidates for fetch)
    # Sprint F213B: PUBLIC page/finding acceptance counters
    public_pages_fetched: int = 0  # pages where fetch was called
    public_pages_accepted: int = 0  # pages with accepted_findings > 0
    public_pages_rejected: int = 0  # pages with accepted_findings == 0
    public_findings_accepted: int = 0  # total findings accepted from public lane
    # Sprint F173C: zero-hit evidence — bounded surfaces for next gate
    # zero_hit_accessible_fetch_count: pages that were fetched (fetched=True) with 0 pattern matches
    # (distinct from discovery_strong_content_weak which includes SKIP-tier pages)
    zero_hit_accessible_fetch_count: int = 0
    # Sprint F188B: CT winner slice — bounded CT-discovered subdomain count (additive)
    ct_subdomain_injected: int = 0
    # F192E: CommonCrawl CDX — bounded CC-discovered archive URL count (additive)
    cc_archive_injected: int = 0
    # F193B: Academic discovery persisted findings count (additive)
    academic_findings_count: int = 0
    # P20: PastebinMonitor + GitHubSecretScanner telemetry (additive)
    pastebin_findings_count: int = 0
    github_secrets_count: int = 0
    # Sprint F217C: Deterministic bootstrap telemetry
    public_bootstrap_enabled: bool = False  # True when bootstrap URLs were generated
    public_bootstrap_candidates_count: int = 0  # bootstrap URLs generated from query
    public_bootstrap_fetch_attempted: int = 0  # bootstrap URLs sent to fetch
    public_bootstrap_fetch_success: int = 0  # bootstrap URLs that fetched successfully
    public_bootstrap_accepted_findings: int = 0  # findings accepted from bootstrap hits
    public_bootstrap_errors: int = 0  # bootstrap-specific errors (parse, dedup, etc.)
    # Sprint F229A: Bootstrap ordering telemetry
    public_bootstrap_order: str = "disabled"  # "before_discovery" | "after_discovery" | "disabled"
    public_bootstrap_prevented_discovery_timeout: bool = False  # True when bootstrap produced candidates but discovery would have returned zero  # noqa: E501
    public_bootstrap_first_fetch_attempted: bool = False  # True when bootstrap hits were added to hits before fetch
    # Sprint F220C: Public Provider Rescue telemetry
    public_rescue_candidates_count: int = 0  # rescue URLs generated from threat query
    public_rescue_fetch_attempted: int = 0  # rescue URLs sent to fetch
    public_rescue_fetch_success: int = 0  # rescue URLs that fetched successfully
    public_rescue_accepted_findings: int = 0  # findings accepted from rescue hits
    public_rescue_errors: int = 0  # rescue-specific errors
    public_rescue_order: str = "disabled"  # "rescue_fallback" | "keyword_seed_fallback" | "disabled"
    # F1-3: keyword_seed_fallback — True when rescue URLs generated for threat query with disabled bootstrap
    keyword_seed_fallback_triggered: bool = False
    # zero_hit_quality_reason_counts: breakdown of WHY zero-hit pages failed
    # keys are the specific quality_reason values from PipelinePageResult
    zero_hit_quality_reason_counts: dict = {}
    # zero_hit_title_samples: bounded title+URL sample for zero-hit pages (max 5, no raw text)
    zero_hit_title_samples: tuple = ()
    # public_zero_hit_summary: run-level structured summary for gate review
    public_zero_hit_summary: dict = {}
    # F207F: PUBLIC Yield — discovered→fetched gap telemetry
    public_discovered: int = 0  # URLs discovered in public lane
    public_fetch_attempted: int = 0  # fetch() called for public URLs
    public_fetch_skipped: int = 0  # fetch skipped (UMA, quality gate, etc.)
    public_fetch_skip_reason: str | None = None  # uma_memory | quality_skip | error
    public_js_renderer_unavailable: int = 0  # JS renderer skipped due to browser unavailable
    public_xml_or_rss_detected: int = 0  # JS renderer skipped due to XML/feed URL
    public_fetch_timeout_count: int = 0  # fetch timeouts in public lane
    public_fetch_blocked_by_memory: int = 0  # skipped due to UMA critical
    # F207I-A: PUBLIC Yield — discovery→fetch transition invariants + telemetry
    public_discovery_cache_hit: int = 0  # DDG queries served from per-run cache
    public_discovery_query_count: int = 0  # total DDG queries issued this run
    public_fetch_candidate_count: int = 0  # URLs queued for fetch
    public_fetch_gate: str = "none"  # memory gate verdict: ok | critical_limited | emergency_blocked
    public_fetch_attempted_urls_sample: tuple[str, ...] = ()  # first 5 fetched URLs
    # F207J-C: PUBLIC Acceptance — post-fetch acceptance/rejection telemetry
    public_acceptance_attempted: int = 0  # pages where fetch succeeded (fetched=True)
    public_acceptance_accepted: int = 0  # pages with accepted_findings > 0
    public_acceptance_rejected: int = 0  # pages with accepted_findings == 0 (post-fetch rejection)
    # rejection reason breakdown: {reason: count}
    public_acceptance_reject_reasons: dict = {}
    # bounded URL samples (max 5 each)
    public_accepted_url_sample: tuple[str, ...] = ()
    public_rejected_url_sample: tuple[str, ...] = ()
    # F208G-A: PUBLIC Yield Taxonomy — run-level terminal classification
    # URL-level counts
    public_terminal_classified_count: int = 0  # URLs with terminal_reason != None
    public_unclassified_count: int = 0  # URLs with terminal_reason == None
    public_terminal_reason_counts: dict = {}  # {terminal_reason: count} for all classified URLs
    # Fetch outcome counts
    public_fetch_success: int = 0  # fetched=True with text available
    public_fetch_failed: int = 0  # fetched=False (all skip/error reasons)
    # Skipped reason breakdown
    public_skipped_duplicate: int = 0  # dedup bloom filter hit
    public_skipped_unsupported_scheme: int = 0  # non-http(s) URL
    public_skipped_memory_gate: int = 0  # UMA emergency/critical blocked
    public_skipped_quality_gate: int = 0  # discovery score too low
    public_skipped_browser_unavailable: int = 0  # JS renderer unavailable
    public_skipped_xml_or_feed: int = 0  # XML/feed URL detected
    public_skipped_timeout: int = 0  # fetch timed out
    public_skipped_fetch_error: int = 0  # fetch exception/error
    # Rejected reason breakdown (fetched but not accepted)
    public_rejected_no_pattern_match: int = 0  # fetched text had no pattern matches
    public_rejected_low_information: int = 0  # page quality too low (SKIP_WEAK)
    public_rejected_duplicate: int = 0  # per-page dedup exhausted
    public_rejected_storage_rejected: int = 0  # DuckDB storage rejected findings
    # F226B: PUBLIC acceptance uplift diagnostics
    public_build_success_count: int = 0  # public_surface findings built (pattern-miss pages)
    public_build_failure_count: int = 0  # public_surface build attempts that returned empty
    public_duplicate_count: int = 0  # public_surface findings rejected as duplicate
    public_acceptance_ratio: float = 0.0  # build_success / max(build_success+build_failure, 1)
    # Bounded URL samples (max 5 each)
    public_skipped_url_sample: tuple[str, ...] = ()  # skipped URL samples
    public_rejected_url_samples: tuple[str, ...] = ()  # rejected URL samples

    # F231A: PUBLIC Candidate Ledger — stage progression summary
    # discovery → fetch_attempted → fetch_success → parse_success → pattern_matched → built → store_attempted → stored/rejected  # noqa: E501
    public_candidates_discovered: int = 0
    public_candidates_fetch_attempted: int = 0
    public_candidates_fetch_success: int = 0
    public_candidates_parse_success: int = 0
    public_candidates_pattern_matched: int = 0
    public_candidates_built: int = 0
    public_candidates_store_attempted: int = 0
    public_candidates_stored: int = 0
    public_candidates_rejected: int = 0
    public_rejection_summary: dict = {}  # {stage: count} where candidates were lost
    # F231A: Canonical terminal stage — where PUBLIC evidence stream terminated
    public_terminal_stage: str = ""  # discovery_empty | fetch_zero | parse_zero | match_zero | build_zero | store_zero | accepted  # noqa: E501
    # F232: Provider surface telemetry — discovery provider selection and outcome truth
    # NOTE: msgspec.Struct does NOT support dataclasses.field(default_factory=...);
    # using mutable default=[] is safe here because PipelineRunResult is frozen=True,
    # so mutation is blocked at the struct level.
    public_provider_selected: list[str] = []  # providers with selected=True
    public_provider_skipped: list[dict] = []  # [{provider, reason}] with selected=False
    public_provider_stub: list[str] = []  # providers in ADVISORY_STUB state
    public_provider_errors: list[dict] = []  # [{provider, error, error_type}] provider-level errors
    public_query_variants: list[str] = []  # query variants emitted to providers
    public_provider_timeout_count: int = 0  # providers that timed out
    public_provider_import_error_count: int = 0  # providers that failed to import/initialize
    # F232: Refined discovery_empty subtypes — explicit reason when discovery returns zero
    public_discovery_empty_reason: str = ""  # no_provider_selected | provider_unavailable | provider_timeout | provider_returned_zero | query_builder_empty  # noqa: E501


# -----------------------------------------------------------------------------
# F360: Pipeline Phases — extracted from async_run_live_public_pipeline
# Each phase is a standalone class with run() method.
# Main function becomes a thin orchestrator that wires phases together.
# -----------------------------------------------------------------------------

from dataclasses import dataclass, field
from collections import Counter
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


@dataclass(frozen=True)
class PipelineContext:
    """Immutable context passed through all pipeline phases."""
    query: str
    store: "DuckDBShadowStore | None"
    max_results: int
    fetch_timeout_s: float
    fetch_max_bytes: int
    fetch_concurrency: int
    hermes_engine: Any = None
    graph: Any = None
    memory_manager: Any = None
    session_id: str | None = None
    vector_store: Any = None
    run_loop: bool = False
    rl_steps: int = 0
    enqueue_hypothesis_pivot: Any = None
    public_bootstrap_enabled: bool = False
    seed_context: Any = None
    export_dir: str | None = None
    _sprint_id: str = ""

    # Phase-computed fields (mutable within context during execution)
    uma_state: str = "UMA_STATE_OK"
    effective_concurrency: int = 8
    hits: tuple = field(default_factory=tuple)
    discovery_telemetry: dict = field(default_factory=dict)
    all_page_results: list = field(default_factory=list)
    generated_report: str = ""
    tot_solution_count: int = 0


# =============================================================================
# Phase 1: Initialization
# =============================================================================

class Phase1_Initialization:
    """Phase 1: Setup pipeline context, reset temporal layer, clear caches."""

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Initialize pipeline context with temporal reset, cache clear, DI resolution."""
        from hledac.universal.layers import reset_temporal_signal_layer
        reset_temporal_signal_layer()

        # F207I-A: Clear per-run DDG query cache
        _resolved_clear_cache = ctx.clear_query_cache_fn
        if _resolved_clear_cache is None:
            from hledac.universal.discovery.duckduckgo_adapter import _clear_query_cache
            _resolved_clear_cache = _clear_query_cache
        _resolved_clear_cache()

        # Sprint F206Q: Restore from persistent snapshot
        persistence_enabled = False
        persistence_restored = False
        try:
            from hledac.universal.layers import (
                is_temporal_store_enabled,
                load_temporal_signal_snapshot,
            )
            persistence_enabled = is_temporal_store_enabled()
            if persistence_enabled:
                persistence_restored = load_temporal_signal_snapshot()
        except Exception:
            pass

        # P11: Initialize session ID for memory manager
        session_id = ctx.session_id
        if session_id is None:
            session_id = hashlib.sha256(ctx.query.encode()).hexdigest()[:16]

        # P11: Load relevant RAG history from memory manager
        rag_context: list[dict] = []
        if ctx.memory_manager is not None:
            try:
                history = await ctx.memory_manager.get_session_history(session_id, limit=50)
                for entry in history:
                    value = entry.get("value", {})
                    if isinstance(value, dict):
                        payload = value.get("payload_text", "")
                        if payload:
                            rag_context.append({
                                "query": value.get("query", ""),
                                "payload": payload[:500],
                                "timestamp": value.get("timestamp", 0),
                            })
            except Exception:
                rag_context = []

        # Update context with computed fields
        ctx.uma_state = ctx.uma_state  # Will be set by Phase 2
        return PipelineContext(
            **{**ctx.__dict__,
               "session_id": session_id,
               "_rag_context": rag_context,
               "_persistence_enabled": persistence_enabled,
               "_persistence_restored": persistence_restored,
               "_persistence_saved": False})


# =============================================================================
# Phase 2: Resource Governance (UMA check)
# =============================================================================

class Phase2_ResourceGovernance:
    """Phase 2: Check UMA state, compute effective concurrency."""

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Check UMA state and determine effective fetch concurrency."""
        from hledac.universal._core.resource_governor import (
            UMA_STATE_CRITICAL,
            UMA_STATE_EMERGENCY,
            UMA_STATE_OK,
        )

        uma_state = UMA_STATE_OK
        try:
            uma_state, _ = await _get_uma_state()
        except Exception:
            pass

        effective_concurrency = ctx.fetch_concurrency
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            effective_concurrency = 1

        return PipelineContext(
            **{**ctx.__dict__,
               "uma_state": uma_state,
               "effective_concurrency": effective_concurrency,
               "_semaphore": asyncio.Semaphore(effective_concurrency),
               "_is_emergency": uma_state == UMA_STATE_EMERGENCY})


# =============================================================================
# Phase 3: Discovery Runner (uses existing DiscoveryEngine)
# =============================================================================
# NOTE: DiscoveryPhaseResult is defined before DiscoveryEngine (line ~70)

class Phase3_DiscoveryRunner:
    """Phase 3: Run discovery using DiscoveryEngine."""

    async def run(self, ctx: PipelineContext) -> tuple[PipelineContext, DiscoveryPhaseResult]:
        """Execute discovery phase and return structured result.

        F364: DiscoveryEngine.run() now returns DiscoveryPhaseResult directly,
        eliminating tuple unpacking in this phase.
        """
        discovery_info = await DiscoveryEngine(
            query=ctx.query,
            store=ctx.store,
            max_results=ctx.max_results,
            public_bootstrap_enabled=ctx.public_bootstrap_enabled,
            seed_context=ctx.seed_context,
        ).run(uma_state=ctx.uma_state)

        return (
            PipelineContext(**{**ctx.__dict__,
                              "hits": discovery_info.hits,
                              "discovery_telemetry": discovery_info.discovery_telemetry}),
            discovery_info,
        )


# =============================================================================
# Phase 4: Fetch Orchestration
# =============================================================================

def _extract_hit_metadata(hit) -> dict:
    """F361: Extract URL, title, snippet, score, reason, rank from discovery hit.

    Supports both DiscoveryHit objects and tuple-like hits for backwards compatibility.
    """
    # URL extraction
    hit_url = hit.url if hasattr(hit, "url") else str(hit[2])

    # Score extraction
    hit_score = getattr(hit, "score", None)
    if hit_score is None and hasattr(hit, "__getitem__"):
        try:
            hit_score = float(hit[4]) if len(hit) > 4 else None
        except (ValueError, TypeError):
            hit_score = None

    # Reason extraction
    hit_reason = getattr(hit, "reason", None)
    if hit_reason is None and hasattr(hit, "__getitem__"):
        try:
            hit_reason = str(hit[5]) if len(hit) > 5 else None
        except (ValueError, TypeError):
            hit_reason = None

    # Title extraction
    hit_title = hit.title if hasattr(hit, "title") else str(hit[1] if len(hit) > 1 else "")

    # Snippet extraction
    hit_snippet = hit.snippet if hasattr(hit, "snippet") else str(hit[3] if len(hit) > 3 else "")

    # Rank extraction
    hit_rank = getattr(hit, "rank", 0)

    return {
        "url": hit_url,
        "title": hit_title,
        "snippet": hit_snippet,
        "score": hit_score,
        "reason": hit_reason,
        "rank": hit_rank,
    }


class Phase4_FetchOrchestrator:
    """Phase 4: Create fetch tasks, run parallel execution, assemble results."""

    async def run(self, ctx: PipelineContext, discovery: DiscoveryPhaseResult) -> tuple[PipelineContext, list]:
        """Execute fetch batch and return page results."""
        from .public_fetch import _fetch_and_process_page

        # Noise filtering
        is_threat = _is_threat_query(ctx.query)
        hits, noise_rejections = _filter_public_noise(discovery.hits, is_threat)

        if noise_rejections:
            logger.debug(
                "[F265C] Noise filter rejected %d/%d hits",
                len(noise_rejections), len(hits) + len(noise_rejections))

        # Track noise rejections
        noise_reject_reasons: dict[str, int] = {}
        for url, reason in noise_rejections:
            noise_reject_reasons[reason] = noise_reject_reasons.get(reason, 0) + 1

        # Bloom filter dedup
        bloom_filter = get_default_bloom_filter()
        seen_url_count = 0
        tasks: list[asyncio.Task] = []

        for hit in hits[:500]:  # MAX_FETCH_CANDIDATES
            meta = _extract_hit_metadata(hit)
            if meta["url"] in bloom_filter:
                continue
            bloom_filter.add(meta["url"])
            seen_url_count += 1

            task = safe_create_task(
                _fetch_and_process_page(
                    semaphore=ctx._semaphore,
                    query=ctx.query,
                    hit_url=meta["url"],
                    hit_title=meta["title"],
                    hit_snippet=meta["snippet"],
                    hit_rank=meta["rank"],
                    fetch_timeout_s=ctx.fetch_timeout_s,
                    fetch_max_bytes=ctx.fetch_max_bytes,
                    store=ctx.store,
                    memory_manager=ctx.memory_manager,
                    session_id=ctx.session_id,
                    discovery_score=meta["score"],
                    discovery_reason=meta["reason"],
                    vector_store=ctx.vector_store,
                    graph=ctx.graph,
                ),
                name="fetch:public_page",
            )
            tasks.append(task)

        # Execute parallel fetch
        _result = await parallel(tasks, policy="collect", ctx="live_public_page_fetch")
        ok_results, error_results = _result.ok, _result.errors

        # Assemble page results
        all_page_results = [item for item in ok_results if isinstance(item, PipelinePageResult)]

        return (PipelineContext(**{**ctx.__dict__,
                                   "all_page_results": all_page_results,
                                   "_seen_url_count": seen_url_count,
                                   "_noise_reject_reasons": noise_reject_reasons,
                                   "_error_results": error_results}),
                all_page_results)


# =============================================================================
# Phase 5: Telemetry Aggregation
# =============================================================================

# =============================================================================
# F361: Phase 5 Telemetry Aggregators (split from monolithic Phase5)
# =============================================================================

class _BasicCounter:
    """F361: Compute basic discovery/fetch/pattern counts."""

    def run(self, results: list) -> dict:
        return {
            "total_discovered": len(results),
            "total_fetched": sum(1 for p in results if p.fetched),
            "total_matched": sum(p.matched_patterns for p in results),
            "total_accepted": sum(p.accepted_findings for p in results),
            "total_stored": sum(p.stored_findings for p in results),
        }


class _AcceptanceClassifier:
    """F361: Classify pages into accepted/rejected with samples."""

    def run(self, fetched: list, noise_reasons: dict) -> dict:
        reject_reasons = dict(noise_reasons)
        accepted, rejected = [], []

        for p in fetched:
            rr = getattr(p, "rejection_reason", None)
            if rr is None:
                if len(accepted) < 5:
                    accepted.append(p.url)
            else:
                reject_reasons[rr] = reject_reasons.get(rr, 0) + 1
                if len(rejected) < 5:
                    rejected.append(p.url)

        return {
            "acceptance_reject_reasons": reject_reasons,
            "accepted_urls": accepted,
            "rejected_urls": rejected,
        }


class _TerminalClassifier:
    """F361: Classify terminal states and collect samples."""

    def run(self, results: list) -> dict:
        counter = Counter()
        skipped, rejected = [], []

        for p in results:
            tr = getattr(p, "terminal_reason", None)
            if tr is None:
                counter["accepted"] += 1
            else:
                counter[tr] += 1
                if tr.startswith("skipped_") and len(skipped) < 5:
                    skipped.append(p.url)
                elif tr.startswith("rejected_") and len(rejected) < 5:
                    rejected.append(p.url)

        return {
            "terminal_reason_counts": dict(counter),
            "skipped_samples": skipped,
            "rejected_samples": rejected,
        }


class _ValueAnalyzer:
    """F361: Analyze value ratios, waste, and quality metrics."""

    def run(self, results: list, fetched: list) -> dict:
        strong = sum(1 for p in results if p.quality_reason == "very_good")
        weak_skipped = sum(1 for p in results
                          if p.quality_reason and p.quality_reason.startswith("SKIP_WEAK"))
        low_value = sum(1 for p in fetched
                       if p.matched_patterns == 0
                       and p.quality_reason in ("weak_low_signal", "ok:no_query_signal"))
        disc_strong_weak = sum(1 for p in results
                              if p.discovery_signal and p.matched_patterns == 0)
        disc_strong_content = sum(1 for p in results
                                  if p.discovery_signal and p.matched_patterns > 0)
        squandered = sum(1 for p in results
                        if p.discovery_score is not None and p.discovery_score >= 0.85
                        and p.quality_reason in ("weak_low_signal", "SKIP_WEAK:weak_discovery", "SKIP_WEAK:very_low_text"))

        noise_ratio = round(low_value / len(fetched), 3) if fetched else 0.0

        # Quality tier mix
        tiers = {"high": 0, "medium": 0, "low": 0, "waste": 0, "none": 0}
        for p in results:
            tier = getattr(p, "value_tier", "none")
            tiers[tier] = tiers.get(tier, 0) + 1
        mix = "|".join(f"{v}{k[0]}" for k, v in tiers.items() if v > 0) or "empty"

        # Waste analysis
        waste_reasons: dict[str, int] = {}
        for p in results:
            if getattr(p, "value_tier", "none") == "waste":
                reason = getattr(p, "resolution_reason", "unknown") or "unknown"
                waste_reasons[reason] = waste_reasons.get(reason, 0) + 1
        top_waste = max(waste_reasons, key=lambda r: waste_reasons[r]) if waste_reasons else ""

        waste_cats = {"structural": 0, "signalless": 0, "false_positive": 0, "error": 0}
        for p in results:
            cat = getattr(p, "waste_category", "")
            if cat in waste_cats:
                waste_cats[cat] += 1

        health_ratio = round(
            sum(1 for p in fetched if getattr(p, "structural_quality", "") == "healthy")
            / max(len(fetched), 1), 3) if fetched else 0.0

        waste_code = max(waste_cats, key=lambda k: waste_cats[k]) \
            if any(v > 0 for v in waste_cats.values()) else ""
        waste_breakdown = "|".join(f"{v}{k[:3]}" for k, v in sorted(waste_cats.items()) if v > 0) \
            if any(v > 0 for v in waste_cats.values()) else "none"

        # Branch hint
        if strong >= 2 and disc_strong_content >= 2:
            hint, action, confidence = "high_value", "expand_public_branch", "high_yield_run"
        elif disc_strong_content >= 1:
            hint, action, confidence = "some_value", "continue_public_branch", "positive_signal"
        elif disc_strong_weak >= 1:
            hint, action, confidence = "weak_signal", "review_discovery_quality", "squandered_hits_detected"
        elif weak_skipped > 0 and not fetched:
            hint, action, confidence = "skipped_low_quality", "throttle_public_branch", "low_quality_majority"
        else:
            hint, action, confidence = "low_value", "hold_public_branch", "marginal_signal"

        return {
            "strong_pages": strong,
            "weak_pages_skipped": weak_skipped,
            "low_value_fetches": low_value,
            "discovery_strong_content_weak": disc_strong_weak,
            "discovery_and_content_strong": disc_strong_content,
            "discovery_squandered": squandered,
            "noise_fetch_ratio": noise_ratio,
            "waste_ratio": noise_ratio,
            "value_ratio": round(disc_strong_content / max(len(results), 1), 3) if results else 0.0,
            "public_branch_hint": hint,
            "public_next_action": action,
            "public_confidence_note": confidence,
            "quality_mix": mix,
            "top_waste_pattern": top_waste,
            "waste_category_counts": waste_cats,
            "structural_health_ratio": health_ratio,
            "run_waste_pattern_code": waste_code,
            "waste_reason_breakdown": waste_breakdown,
        }


class _ProofGradeCalculator:
    """F361: Calculate proof grade from telemetry metrics."""

    def run(self, stored: int, fetched: list, noise_ratio: float, error_ratio: float) -> dict:
        backend_degraded = bool(error_ratio > 0.6)

        if backend_degraded:
            grade = "backend_degraded"
        elif fetched and stored / len(fetched) >= 0.5:
            if noise_ratio <= 0.3:
                grade = "strong"
            else:
                grade = "moderate"
        elif fetched and stored / len(fetched) >= 0.3 and noise_ratio <= 0.5:
            grade = "moderate"
        elif stored > 0:
            grade = "weak"
        else:
            grade = "empty"

        return {
            "backend_degraded": backend_degraded,
            "public_proof_grade": grade,
        }


class _CandidateLedger:
    """F361: Aggregate candidate pipeline statistics."""

    def run(self, results: list, fetched: list) -> dict:
        discovered = len(results)
        fetch_attempted = len(fetched)
        fetch_success = sum(1 for p in fetched
                           if not (p.error and p.error.startswith("fetch_text_none_or_empty")))
        parse_success = sum(1 for p in fetched if not p.error)
        pattern_matched = sum(1 for p in fetched if p.matched_patterns > 0)
        built = sum(1 for p in fetched if p.matched_patterns > 0 or p.accepted_findings > 0)
        store_attempted = sum(1 for p in fetched if p.matched_patterns > 0)
        stored = sum(1 for p in fetched if p.stored_findings > 0)
        rejected = sum(1 for p in fetched if p.matched_patterns > 0 and p.stored_findings == 0)

        rej_sum = {}
        if fetch_attempted == 0 and discovered > 0:
            rej_sum["fetch_zero"] = discovered - fetch_attempted
        if pattern_matched == 0 and fetch_success > 0:
            rej_sum["match_zero"] = fetch_success - pattern_matched
        if store_attempted > 0 and stored == 0:
            rej_sum["store_zero"] = store_attempted

        # Terminal stage
        if not discovered:
            terminal = "discovery_empty"
        elif fetch_attempted == 0:
            terminal = "fetch_zero"
        elif pattern_matched == 0:
            terminal = "match_zero"
        elif stored == 0:
            terminal = "store_zero"
        else:
            terminal = "accepted"

        return {
            "candidates_discovered": discovered,
            "candidates_fetch_attempted": fetch_attempted,
            "candidates_fetch_success": fetch_success,
            "candidates_parse_success": parse_success,
            "candidates_pattern_matched": pattern_matched,
            "candidates_built": built,
            "candidates_store_attempted": store_attempted,
            "candidates_stored": stored,
            "candidates_rejected": rejected,
            "rejection_summary": rej_sum,
            "terminal_stage": terminal,
        }


class _FetchSkipAnalyzer:
    """F361: Analyze why pages were skipped during fetch."""

    def run(self, results: list) -> dict:
        skip_reasons = [p.fetch_blocked_reason for p in results
                       if not p.fetched and p.fetch_blocked_reason]
        skip_reason = Counter(skip_reasons).most_common(1)[0][0] if skip_reasons else None

        accessibility_stages = {"connection", "tls", "http"}
        accessibility_blocker = any(getattr(p, "failure_stage", None) in accessibility_stages
                                    for p in results)

        return {
            "public_fetch_skip_reason": skip_reason,
            "fetch_accessibility_blocker": accessibility_blocker,
        }


class Phase5_TelemetryAggregator:
    """Phase 5: Compute all run-level telemetry using composed aggregators.

    F361: Refactored from monolithic 290-line method to composition of 7 focused classes.
    Each aggregator handles one responsibility and is independently testable.
    """

    def __init__(self) -> None:
        self._basic = _BasicCounter()
        self._acceptance = _AcceptanceClassifier()
        self._terminal = _TerminalClassifier()
        self._value = _ValueAnalyzer()
        self._proof = _ProofGradeCalculator()
        self._ledger = _CandidateLedger()
        self._skip = _FetchSkipAnalyzer()

    def run(self, ctx: PipelineContext, discovery: DiscoveryPhaseResult) -> dict:
        """Aggregate telemetry using composed specialized aggregators."""
        results = ctx.all_page_results
        fetched = [p for p in results if p.fetched]
        noise_reasons = ctx._noise_reject_reasons
        seen_count = ctx._seen_url_count

        # Run all aggregators
        basic = self._basic.run(results)
        acceptance = self._acceptance.run(fetched, noise_reasons)
        terminal = self._terminal.run(results)
        value = self._value.run(results, fetched)
        skip = self._skip.run(results)

        # Error ratio for proof grade
        error_count = sum(1 for p in results if p.error and "fetch_exception" in p.error)
        error_ratio = error_count / len(results) if results else 0.0
        proof = self._proof.run(
            basic["total_stored"], fetched, value["noise_fetch_ratio"], error_ratio
        )

        # Ledger from fetched pages
        ledger = self._ledger.run(results, fetched)

        # Discovery telemetry extraction
        dt = discovery.discovery_telemetry
        pub_build_success = dt.get("public_build_success_count", 0)
        pub_build_failure = dt.get("public_build_failure_count", 0)

        # Acceptance ratio
        build_success = sum(1 for p in fetched
                           if p.matched_patterns > 0 or p.accepted_findings > 0)
        build_fail = sum(1 for p in fetched
                        if p.matched_patterns > 0 and p.stored_findings == 0)
        acceptance_ratio = build_success / max(build_success + build_fail, 1) \
            if (build_success + build_fail) > 0 else 0.0

        # Merge all results
        return {
            # Basic
            **basic,
            "seen_url_count": seen_count,

            # Value analysis
            **value,

            # Acceptance
            **acceptance,

            # Terminal
            **terminal,

            # Ledger
            **ledger,

            # Proof
            **proof,

            # Skip analysis
            **skip,

            # Bootstrap telemetry
            "bootstrap_candidates": dt.get("public_bootstrap_candidates_count", 0),
            "bootstrap_fetch_attempted": dt.get("public_bootstrap_fetch_attempted", 0),
            "bootstrap_fetch_success": dt.get("public_bootstrap_fetch_success", 0),
            "bootstrap_accepted": dt.get("public_bootstrap_accepted_findings", 0),
            "bootstrap_errors": dt.get("public_bootstrap_errors", 0),
            "bootstrap_order": dt.get("public_bootstrap_order", "disabled"),
            "bootstrap_prevented": dt.get("public_bootstrap_prevented_discovery_timeout", False),
            "bootstrap_first_attempted": dt.get("public_bootstrap_first_fetch_attempted", False),

            # Rescue telemetry
            "rescue_candidates": dt.get("public_rescue_candidates_count", 0),
            "rescue_fetch_attempted": dt.get("public_rescue_fetch_attempted", 0),
            "rescue_fetch_success": dt.get("public_rescue_fetch_success", 0),
            "rescue_accepted": dt.get("public_rescue_accepted_findings", 0),
            "rescue_errors": dt.get("public_rescue_errors", 0),
            "rescue_order": dt.get("public_rescue_order", "disabled"),

            # Build stats
            "build_success": pub_build_success,
            "build_failure": pub_build_failure,
            "duplicate_count": dt.get("public_duplicate_count", 0),
            "acceptance_ratio": acceptance_ratio,

            # Discovery
            "discovery_empty_reason": dt.get("public_discovery_empty_reason", ""),
        }


# =============================================================================
# Phase 6: Report Generation (OSINT, RL Loop, ToT)
# =============================================================================

class Phase6_ReportGenerator:
    """Phase 6: Generate OSINT report, run RL loop, hypothesis + ToT."""

    async def run(self, ctx: PipelineContext, all_page_results: list) -> tuple[PipelineContext, str, int]:
        """Execute report generation and RL/ToT phases."""
        generated_report = ""
        tot_solution_count = 0

        # OSINT Report (P6)
        if ctx.hermes_engine is not None and all_page_results:
            try:
                generated_report = await _generate_and_store_report(
                    query=ctx.query,
                    pages=tuple(all_page_results),
                    store=ctx.store,
                    hermes_engine=ctx.hermes_engine,
                    vector_store=ctx.vector_store,
                )
            except Exception:
                generated_report = ""

        # RL Loop (P17)
        if ctx.run_loop and ctx.hermes_engine is not None:
            try:
                rl_result = await _run_rl_loop(
                    ctx=ctx,
                    all_page_results=all_page_results,
                )
                tot_solution_count = rl_result.get("tot_solution_count", 0)
            except Exception as e:
                logger.warning(f"[P17] RL loop failed: {e}")

        # Hypothesis + ToT (P12)
        if ctx.store is not None and ctx.hermes_engine is not None:
            try:
                tot_result = await _run_hypothesis_tot(
                    ctx=ctx,
                    all_page_results=all_page_results,
                )
                tot_solution_count = tot_result.get("tot_solution_count", 0)
            except Exception:
                pass  # Fail-soft

        return (PipelineContext(**{**ctx.__dict__,
                                   "generated_report": generated_report,
                                   "tot_solution_count": tot_solution_count}),
                generated_report,
                tot_solution_count)


async def _run_rl_loop(ctx: PipelineContext, all_page_results: list) -> dict:
    """Run reinforcement learning loop (P17)."""
    from hledac.universal.federated.bridge import FederatedBridge
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    RL_TIME_LIMIT_S = 300.0
    RL_LANE = "rl"
    RL_ACTIONS = ["hypothesis_generation", "tot_reasoning", "discovery", "fetch", "graph_update", "evaluate", "done"]

    bridge = FederatedBridge()
    rl_start_time = time.monotonic()
    step_count = 0
    total_reward = 0.0
    rl_state = (ctx.query[:20] if len(ctx.query) > 20 else ctx.query, 0, 0, 6, False)
    rl_next_state = rl_state
    tot_solution_count = 0

    while True:
        if ctx.rl_steps > 0 and step_count >= ctx.rl_steps:
            break
        elapsed = time.monotonic() - rl_start_time
        if elapsed >= RL_TIME_LIMIT_S:
            logger.info(f"[P17] RL loop time limit reached ({elapsed:.1f}s)")
            break

        action = bridge.get_best_action(RL_LANE, rl_state, RL_ACTIONS)
        if action == "done":
            await bridge.persist_if_due()
            break

        reward = 0.0
        action_findings: list = []
        try:
            if action == "hypothesis_generation" and ctx.hermes_engine is not None:
                ctx_h = {"query": ctx.query, "source": "rl_loop"}
                if hasattr(ctx.hermes_engine, "generate_hypotheses_async"):
                    hyp_strings = await ctx.hermes_engine.generate_hypotheses_async(
                        context=ctx_h,
                        hermes_engine=getattr(ctx.hermes_engine, "_inference_engine", None),
                    )
                    for h in (hyp_strings or [])[:10]:
                        action_findings.append({"type": "hypothesis", "content": h, "source": "rl_hypothesis"})
                    reward = len(action_findings) * 0.1
            elif action == "tot_reasoning":
                action_findings.append({"type": "tot", "content": f"ToT reasoning for: {ctx.query[:50]}", "source": "rl_tot"})
                reward = 0.3
            elif action == "discovery":
                action_findings.append({"type": "discovery", "content": f"Discovery: {ctx.query}", "source": "rl_discovery"})
                reward = 0.2
            elif action == "fetch":
                action_findings.append({"type": "fetch", "content": f"Fetch: {ctx.query}", "source": "rl_fetch"})
                reward = 0.1
            elif action == "evaluate":
                action_findings.append({"type": "evaluation", "content": f"Evaluation: {ctx.query}", "source": "rl_evaluate"})
                reward = 0.15
        except Exception as e:
            logger.debug(f"[P17] RL action '{action}' failed: {e}")

        new_findings_count = len(action_findings)
        rl_next_state = (
            ctx.query[:20] if len(ctx.query) > 20 else ctx.query,
            min(step_count // 2, 5),
            min(new_findings_count // 10, 10),
            6,
            action == "tot_reasoning",
        )
        bridge.update(RL_LANE, rl_state, action, reward, rl_next_state)
        total_reward += reward

        # Store findings
        if ctx.store is not None and action_findings:
            try:
                rl_finding_buffer: list[CanonicalFinding] = []
                for finding_data in action_findings:
                    finding_id = hashlib.sha256(f"{ctx.query}\x00{str(finding_data)}\x00rl".encode()).hexdigest()[:16]
                    rl_finding_buffer.append(CanonicalFinding(
                        finding_id=finding_id,
                        query=ctx.query,
                        source_type="rl_research",
                        confidence=0.7,
                        ts=time.time(),
                        provenance=("rl", action),
                        payload_text=str(finding_data)[:500],
                    ))
                if rl_finding_buffer:
                    await ctx.store.submit_findings(rl_finding_buffer)
            except Exception as e:
                logger.warning(f"[P17] Failed to store RL findings: {e}")

        if ctx.memory_manager is not None and ctx.session_id is not None:
            try:
                await ctx.memory_manager.put(
                    ctx.session_id, f"rl_result:{step_count}",
                    {"action": action, "reward": reward, "findings_count": len(action_findings), "timestamp": time.time()}
                )
            except Exception:
                pass

        rl_state = rl_next_state
        step_count += 1
        logger.info(f"[P17] RL step {step_count}: action={action}, reward={reward:.3f}, findings={len(action_findings)}")
        await bridge.persist_if_due()

    logger.info(f"[P17] RL loop completed {step_count} steps, total_reward={total_reward:.3f}")
    return {"tot_solution_count": tot_solution_count, "step_count": step_count, "total_reward": total_reward}


async def _run_hypothesis_tot(ctx: PipelineContext, all_page_results: list) -> dict:
    """Run hypothesis generation and ToT evaluation (P12)."""
    from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine
    from hledac.universal.tot_integration import TotIntegrationLayer
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    if not all_page_results:
        return {"tot_solution_count": 0}

    hypo_engine = HypothesisEngine()
    tot_layer = TotIntegrationLayer()
    tot_layer.attach_hypothesis_engine(hypo_engine)

    # Get recent findings
    recent_findings = await ctx.store.async_get_recent_findings(limit=20)
    if not recent_findings:
        return {"tot_solution_count": 0}

    hypo_context = {
        "query": ctx.query,
        "stored_findings_count": ctx.total_stored if hasattr(ctx, 'total_stored') else 0,
        "findings": [
            {
                "finding_id": f.finding_id if hasattr(f, "finding_id") else str(f.get("finding_id", "")),
                "source_type": f.source_type if hasattr(f, "source_type") else str(f.get("source_type", "")),
                "confidence": f.confidence if hasattr(f, "confidence") else float(f.get("confidence", 0.0)),
                "provenance": f.provenance if hasattr(f, "provenance") else f.get("provenance", ""),
            }
            for f in recent_findings[:20]
        ],
    }

    hypotheses = await hypo_engine.generate_hypotheses_async(
        context=hypo_context,
        hermes_engine=ctx.hermes_engine,
    )

    hypotheses_to_eval = hypotheses[:10]
    if not hypotheses_to_eval:
        return {"tot_solution_count": 0}

    tot_solution_count = 0
    tot_finding_buffer: list[CanonicalFinding] = []

    async def run_tot_with_timeout(hypo: str, timeout_s: float = 15.0) -> str:
        try:
            async with asyncio.timeout(timeout_s):
                return await tot_layer.solve_with_tot(hypo)
        except TimeoutError:
            logger.debug(f"[P12] ToT timed out after {timeout_s}s")
            return ""
        except Exception as e:
            logger.debug(f"[P12] ToT failed: {e}")
            return ""

    tasks_with_hypo = {safe_create_task(run_tot_with_timeout(hypo), name=f"tot:hypo_{i}"): hypo
                        for i, hypo in enumerate(hypotheses_to_eval)}
    tasks = list(tasks_with_hypo.keys())

    for coro in asyncio.as_completed(tasks):
        tot_result = await coro
        hypo = tasks_with_hypo[coro]
        if tot_result:
            tot_solution_count += 1
            try:
                tot_finding_buffer.append(CanonicalFinding(
                    finding_id=f"tot_{hashlib.sha256(tot_result.encode()).hexdigest()[:16]}",
                    query=ctx.query,
                    source_type="tot_synthesis",
                    confidence=0.7,
                    ts=time.time(),
                    provenance=("tot", hypo[:100]),
                    payload_text=tot_result[:1000],
                ))
            except Exception:
                pass

            if ctx.enqueue_hypothesis_pivot is not None:
                try:
                    pivot_seed = tot_result[:200].split()[:5]
                    for term in pivot_seed:
                        ctx.enqueue_hypothesis_pivot(
                            ioc_value=term.lower(),
                            ioc_type="hypothesis",
                            confidence=0.6,
                            depth=1,
                        )
                except Exception:
                    pass

    if tot_finding_buffer and ctx.store is not None:
        await ctx.store.submit_findings(tot_finding_buffer)

    return {"tot_solution_count": tot_solution_count}


# =============================================================================
# Phase 7: Synthesis Runner
# =============================================================================

class Phase7_SynthesisRunner:
    """Phase 7: Run LLM synthesis from findings (bounded for M1 8GB)."""

    async def run(self, ctx: PipelineContext, total_stored: int) -> Any | None:
        """Execute synthesis if conditions met (M1 8GB safe)."""
        if total_stored < 5 or not LANE_REGISTRY.is_enabled("hermes_synthesis"):
            return None

        try:
            import psutil
            rss_gib = psutil.Process().memory_info().rss / (1024**3)
            if rss_gib > 5.5:
                logger.debug("[SYNTHESIS] Skipped: RSS %.1fGiB > 5.5GiB", rss_gib)
                return None
        except Exception:
            pass

        try:
            from hledac.universal._core.model_runtime import ModelLifecycle
            from hledac.universal.brain.synthesis_runner import SynthesisRunner

            findings_for_synth = []
            for pr in ctx.all_page_results:
                if pr.accepted_findings > 0:
                    findings_for_synth.append({
                        "content": (pr.quality_reason or "")[:500],
                        "title": pr.url or "",
                        "source_type": "public_lane",
                        "confidence": 0.5,
                        "url": pr.url or "",
                    })

            if len(findings_for_synth) < 5:
                return None

            findings_for_synth = findings_for_synth[:50]
            lifecycle = ModelLifecycle()
            runner = SynthesisRunner(lifecycle)
            runner.set_compression_threshold(4000)

            async with asyncio.timeout(90.0):
                report = await runner.synthesize_findings(
                    query=ctx.query,
                    findings=findings_for_synth,
                    max_findings=10,
                    force_synthesis=False,
                )

            await runner.close()

            if report is not None:
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                report_id = f"synth_{hashlib.md5(ctx.query.encode()).hexdigest()[:12]}"
                synthesis_finding = CanonicalFinding(
                    finding_id=report_id,
                    query=ctx.query,
                    source_type="llm_synthesis",
                    confidence=getattr(report, "confidence", 0.7) or 0.7,
                    ts=time.time(),
                    payload_text=f"Threat actors: {', '.join(getattr(report, 'threat_actors', []) or [])} | {getattr(report, 'threat_summary', '')[:500]}",
                    provenance=("synthesis", getattr(report, "query", ctx.query)[:50]),
                )
                logger.info("[SYNTHESIS] Report produced: confidence=%.3f", synthesis_finding.confidence)
                return synthesis_finding

        except Exception as e:
            logger.warning("[SYNTHESIS] Synthesis failed: %s", e)

        return None


# =============================================================================
# Phase 8: Export Manager
# =============================================================================

class Phase8_ExportManager:
    """Phase 8: Export to Obsidian Markdown and interactive HTML graph."""

    async def run(self, ctx: PipelineContext, generated_report: str, all_page_results: list) -> None:
        """Execute export to markdown and graph HTML."""
        if ctx.graph is not None and ctx.graph.node_count() > 0:
            try:
                export_path = str(Path("~/new_hledac_graph.html").expanduser())
                ctx.graph.export_html(export_path)
            except Exception:
                pass

        # P18: Export to Obsidian
        try:
            from hledac.universal.export.export_manager import get_export_manager
            from hledac.universal.memory.memory_manager import export_session

            resolved_export_dir = ctx.export_dir or os.environ.get("GHOST_EXPORT_DIR")
            export_mgr = get_export_manager(resolved_export_dir)

            sources = [p.url for p in all_page_results if hasattr(p, "url") and p.url][:20]

            session_findings = []
            if ctx.memory_manager is not None and ctx.session_id is not None:
                try:
                    session_data = await export_session(ctx.session_id)
                    session_findings = session_data.get("findings", [])
                except Exception:
                    session_findings = []

            export_metadata = {
                "query": ctx.query,
                "sources": sources,
                "tags": ["hledac", "osint", "public-pipeline"],
                "session_id": ctx.session_id,
                "stored_findings": str(ctx.total_stored if hasattr(ctx, 'total_stored') else 0),
            }

            md_path = export_mgr.export_markdown(
                report=generated_report,
                findings=session_findings,
                file_path=None,
                metadata=export_metadata,
            )
            if md_path:
                logger.info(f"[P18] Exported markdown to {md_path}")

            if ctx.graph is not None and ctx.graph.node_count() > 0:
                html_path = export_mgr.export_graph_html(
                    graph_manager=ctx.graph,
                    file_path=None,
                    title=f"Hledac Graph - {ctx.query[:50]}",
                )
                if html_path:
                    logger.info(f"[P18] Exported graph HTML to {html_path}")

        except Exception as e:
            logger.warning(f"[P18] Export failed: {e}")


# =============================================================================
# Phase 9: Temporal Signal Persistence
# =============================================================================

class Phase9_TemporalPersistence:
    """Phase 9: Save temporal signal snapshot after pipeline completion."""

    def run(self) -> dict:
        """Save and return persistence status."""
        try:
            from hledac.universal.layers import (
                get_temporal_signal_summary,
                build_temporal_priority_hints,
                save_temporal_signal_snapshot,
            )
            temporal_signal_summary = get_temporal_signal_summary(k=10)
            temporal_priority_hints = build_temporal_priority_hints(k=10)
            persistence_saved = save_temporal_signal_snapshot()
        except Exception:
            temporal_signal_summary = {}
            temporal_priority_hints = []
            persistence_saved = False

        return {
            "temporal_signal_summary": temporal_signal_summary,
            "temporal_priority_hints": temporal_priority_hints,
            "persistence_saved": persistence_saved,
        }


# =============================================================================
# Result Builder — assembles PipelineRunResult from phase outputs
# =============================================================================

class ResultBuilder:
    """Builds PipelineRunResult from phase outputs."""

    @staticmethod
    def build(ctx: PipelineContext, telemetry: dict, discovery: DiscoveryPhaseResult,
              public_stage_failure: str | None, public_stage_failure_reason: str | None,
              generated_report: str, tot_solution_count: int) -> PipelineRunResult:
        """Assemble final PipelineRunResult with all telemetry fields."""

        # Extract common values
        t = telemetry
        total_discovered = t["total_discovered"]
        total_fetched = t["total_fetched"]
        total_matched = t["total_matched"]
        total_accepted = t["total_accepted"]
        total_stored = t["total_stored"]

        # Run error
        run_error = discovery.discovery_error
        if not run_error and ctx._error_results:
            err = ctx._error_results[0]
            run_error = f"batch_error:{type(err).__name__}:{err}"

        # Branch verdict dict
        public_branch_verdict = {
            "waste_ratio": t["waste_ratio"],
            "value_ratio": t["value_ratio"],
            "public_branch_hint": t["public_branch_hint"],
            "strong_pages": t["strong_pages"],
            "weak_pages_skipped": t["weak_pages_skipped"],
            "discovery_strong_content_weak": t["discovery_strong_content_weak"],
            "discovery_and_content_strong": t["discovery_and_content_strong"],
            "low_value_fetches": t["low_value_fetches"],
            "discovery_squandered": t["discovery_squandered"],
            "noise_fetch_ratio": t["noise_fetch_ratio"],
            "corroboration_vs_burn": round((t["discovery_and_content_strong"] + t["strong_pages"]) / max(total_discovered, 1), 3),
            "public_next_action": t["public_next_action"],
            "public_confidence_note": t["public_confidence_note"],
            "backend_degraded": t["backend_degraded"],
            "public_proof_grade": t["public_proof_grade"],
            "discovery_error_detail": discovery.discovery_error,
        }

        # Compute ratios
        usable_findings_ratio = round(total_stored / max(total_discovered, 1), 3)
        discovery_to_findings_efficiency = round(t["discovery_and_content_strong"] / max(total_discovered, 1), 3)
        public_value_density = round(total_stored / max(total_fetched, 1), 3)

        # Zero hit summary
        zero_hit_reasons = {}
        zero_hit_titles = []
        for p in ctx.all_page_results:
            if p.fetched and p.matched_patterns == 0 and p.quality_reason:
                zero_hit_reasons[p.quality_reason] = zero_hit_reasons.get(p.quality_reason, 0) + 1
            if p.fetched and p.matched_patterns == 0 and len(zero_hit_titles) < 5:
                p_title = getattr(p, "discovery_reason", "") or ""
                zero_hit_titles.append((p_title, p.url))

        public_zero_hit_summary = {
            "zero_hit_accessible_fetch_count": sum(1 for p in ctx.all_page_results if p.fetched and p.matched_patterns == 0),
            "zero_hit_unique_reasons": list(zero_hit_reasons.keys()),
            "zero_hit_has_substantive_content": any(p.fetched and p.matched_patterns == 0
                                                     and getattr(p, "structural_quality", "") == "healthy"
                                                     for p in ctx.all_page_results),
            "zero_hit_has_signalless": any(p.fetched and p.matched_patterns == 0
                                           and getattr(p, "waste_category", "") == "signalless"
                                           for p in ctx.all_page_results),
        }

        # Discovery blocker
        fallback_triggered = getattr(discovery.discovery_result, "fallback_triggered", None)
        FALLBACK_STATE_MAP = {
            "primary_backend_failed_fallback_succeeded": "primary_failed_fallback_succeeded",
            "primary_backend_failed_fallback_failed": "primary_failed_fallback_failed",
        }
        public_discovery_fallback_state = FALLBACK_STATE_MAP.get(fallback_triggered) or (
            "no_fallback_needed" if discovery.discovery_error is None else None
        )

        BLOCKER_MAP = {"primary_backend_failed_fallback_failed": "backend_error_fallback_failed"}
        if ctx.uma_state == "UMA_STATE_EMERGENCY":
            public_discovery_blocker = "uma_emergency_abort"
        elif discovery.discovery_error and not fallback_triggered:
            public_discovery_blocker = "backend_error_no_fallback"
        else:
            public_discovery_blocker = BLOCKER_MAP.get(fallback_triggered)

        # Fetch gate
        if ctx.uma_state == "UMA_STATE_EMERGENCY":
            public_fetch_gate = "emergency_blocked"
        elif ctx.uma_state == "UMA_STATE_CRITICAL":
            public_fetch_gate = "critical_limited"
        else:
            public_fetch_gate = "ok"

        # Dominant failure mode
        failure_modes = []
        if public_discovery_blocker:
            failure_modes.append(public_discovery_blocker)
        if t["fetch_accessibility_blocker"]:
            failure_modes.append("fetch_accessibility_blocker")
        if any(p.redirected and p.waste_category in ("structural", "signalless") for p in ctx.all_page_results):
            failure_modes.append("redirect_non_content")
        if t["run_waste_pattern_code"]:
            failure_modes.append(f"waste:{t['run_waste_pattern_code']}")
        dominant_failure_mode = failure_modes[0] if failure_modes else None

        return PipelineRunResult(
            query=ctx.query,
            discovered=total_discovered,
            fetched=total_fetched,
            matched_patterns=total_matched,
            accepted_findings=total_accepted,
            stored_findings=total_stored,
            patterns_configured=_get_patterns_configured_count(),
            pages=tuple(ctx.all_page_results),
            error=run_error,
            strong_pages=t["strong_pages"],
            weak_pages_skipped=t["weak_pages_skipped"],
            low_value_fetches=t["low_value_fetches"],
            discovery_strong_content_weak=t["discovery_strong_content_weak"],
            discovery_and_content_strong=t["discovery_and_content_strong"],
            discovery_squandered=t["discovery_squandered"],
            noise_fetch_ratio=t["noise_fetch_ratio"],
            corroboration_vs_burn=public_branch_verdict["corroboration_vs_burn"],
            public_next_action=t["public_next_action"],
            public_confidence_note=t["public_confidence_note"],
            public_branch_verdict=public_branch_verdict,
            usable_findings_ratio=usable_findings_ratio,
            discovery_to_findings_efficiency=discovery_to_findings_efficiency,
            quality_mix=t["quality_mix"],
            public_proof_grade=t["public_proof_grade"],
            public_value_density=public_value_density,
            top_waste_pattern=t["top_waste_pattern"],
            discovery_false_positive_count=sum(1 for p in ctx.all_page_results
                                              if getattr(p, "discovery_false_positive", False)),
            waste_category_counts=t["waste_category_counts"],
            structural_health_ratio=t["structural_health_ratio"],
            factual_value_density=public_value_density,
            run_waste_pattern_code=t["run_waste_pattern_code"],
            waste_reason_breakdown=t["waste_reason_breakdown"],
            backend_degraded=t["backend_degraded"],
            public_discovery_blocker=public_discovery_blocker,
            public_fetch_accessibility_blocker=t["fetch_accessibility_blocker"],
            public_discovery_fallback_state=public_discovery_fallback_state,
            dominant_public_failure_mode=dominant_failure_mode,
            public_stage_failure=public_stage_failure,
            public_stage_failure_reason=public_stage_failure_reason,
            public_discovery_attempted=discovery.discovery_attempted,
            public_discovery_raw_count=total_discovered,
            public_discovery_deduped_count=t["seen_url_count"],
            public_pages_fetched=total_fetched,
            public_pages_accepted=sum(1 for p in ctx.all_page_results if p.accepted_findings > 0),
            public_pages_rejected=sum(1 for p in ctx.all_page_results if p.fetched and p.accepted_findings == 0),
            public_findings_accepted=total_accepted,
            zero_hit_accessible_fetch_count=sum(1 for p in ctx.all_page_results if p.fetched and p.matched_patterns == 0),
            zero_hit_quality_reason_counts=zero_hit_reasons,
            zero_hit_title_samples=tuple(zero_hit_titles),
            public_zero_hit_summary=public_zero_hit_summary,
            ct_subdomain_injected=discovery.ct_injected,
            cc_archive_injected=discovery.cc_injected,
            academic_findings_count=discovery.academic_findings_count,
            pastebin_findings_count=discovery.pastebin_findings_count,
            github_secrets_count=discovery.github_secrets_count,
            public_bootstrap_enabled=ctx.public_bootstrap_enabled,
            public_bootstrap_candidates_count=t["bootstrap_candidates"],
            public_bootstrap_fetch_attempted=t["bootstrap_fetch_attempted"],
            public_bootstrap_fetch_success=t["bootstrap_fetch_success"],
            public_bootstrap_accepted_findings=t["bootstrap_accepted"],
            public_bootstrap_errors=t["bootstrap_errors"],
            public_bootstrap_order=t["bootstrap_order"],
            public_bootstrap_prevented_discovery_timeout=t["bootstrap_prevented"],
            public_bootstrap_first_fetch_attempted=t["bootstrap_first_attempted"],
            public_rescue_candidates_count=t["rescue_candidates"],
            public_rescue_fetch_attempted=t["rescue_fetch_attempted"],
            public_rescue_fetch_success=t["rescue_fetch_success"],
            public_rescue_accepted_findings=t["rescue_accepted"],
            public_rescue_errors=t["rescue_errors"],
            public_rescue_order=t["rescue_order"],
            keyword_seed_fallback_triggered=discovery.keyword_seed_fallback_triggered,
            public_discovered=total_discovered,
            public_fetch_attempted=total_fetched,
            public_fetch_skipped=total_discovered - t["seen_url_count"],
            public_fetch_skip_reason=t["public_fetch_skip_reason"],
            public_js_renderer_unavailable=sum(1 for p in ctx.all_page_results
                                               if p.fetched and p.js_renderer_skipped_reason == "browser_unavailable"),
            public_xml_or_rss_detected=sum(1 for p in ctx.all_page_results
                                           if p.fetched and p.js_renderer_skipped_reason in ("xml_or_feed_url", "xml_recovered")),
            public_fetch_timeout_count=sum(1 for p in ctx.all_page_results
                                           if not p.fetched and p.fetch_blocked_reason == "timeout"),
            public_fetch_blocked_by_memory=sum(1 for p in ctx.all_page_results
                                               if not p.fetched and p.fetch_blocked_reason == "uma_memory"),
            public_discovery_cache_hit=discovery.discovery_telemetry.get("public_discovery_cache_hit", 0),
            public_discovery_query_count=discovery.discovery_telemetry.get("public_discovery_query_count", 0),
            public_fetch_candidate_count=t["seen_url_count"],
            public_fetch_gate=public_fetch_gate,
            public_fetch_attempted_urls_sample=tuple(p.url for p in ctx.all_page_results if p.fetched)[:5],
            public_acceptance_attempted=t["candidates_fetch_attempted"],
            public_acceptance_accepted=sum(1 for p in ctx.all_page_results if p.fetched and p.accepted_findings > 0),
            public_acceptance_rejected=sum(1 for p in ctx.all_page_results if p.fetched and p.accepted_findings == 0),
            public_acceptance_reject_reasons=t["acceptance_reject_reasons"],
            public_accepted_url_sample=tuple(t["accepted_urls"]),
            public_rejected_url_sample=tuple(t["rejected_urls"]),
            public_build_success_count=t["build_success"],
            public_build_failure_count=t["build_failure"],
            public_duplicate_count=t["duplicate_count"],
            public_acceptance_ratio=t["acceptance_ratio"],
            public_terminal_classified_count=sum(1 for v in t["terminal_reason_counts"].values() if v > 0),
            public_unclassified_count=len(ctx.all_page_results) - sum(1 for v in t["terminal_reason_counts"].values() if v > 0),
            public_terminal_reason_counts=t["terminal_reason_counts"],
            public_fetch_success=t["candidates_fetch_success"],
            public_fetch_failed=t["candidates_fetch_attempted"] - t["candidates_fetch_success"],
            public_skipped_duplicate=total_discovered - t["seen_url_count"],
            public_skipped_unsupported_scheme=t["terminal_reason_counts"].get("skipped_unsupported_scheme", 0),
            public_skipped_memory_gate=t["terminal_reason_counts"].get("skipped_memory_gate", 0),
            public_skipped_quality_gate=t["terminal_reason_counts"].get("skipped_quality_gate", 0),
            public_skipped_browser_unavailable=t["terminal_reason_counts"].get("skipped_browser_unavailable", 0),
            public_skipped_xml_or_feed=t["terminal_reason_counts"].get("skipped_xml_or_feed", 0),
            public_skipped_timeout=t["terminal_reason_counts"].get("skipped_timeout", 0),
            public_skipped_fetch_error=t["terminal_reason_counts"].get("skipped_fetch_error", 0),
            public_rejected_no_pattern_match=t["terminal_reason_counts"].get("rejected_no_pattern_match", 0),
            public_rejected_low_information=t["terminal_reason_counts"].get("rejected_low_information", 0),
            public_rejected_duplicate=t["terminal_reason_counts"].get("rejected_duplicate", 0),
            public_rejected_storage_rejected=t["terminal_reason_counts"].get("rejected_storage_rejected", 0),
            public_skipped_url_sample=tuple(t["skipped_samples"]),
            public_rejected_url_samples=tuple(t["rejected_samples"]),
            public_candidates_discovered=t["candidates_discovered"],
            public_candidates_fetch_attempted=t["candidates_fetch_attempted"],
            public_candidates_fetch_success=t["candidates_fetch_success"],
            public_candidates_parse_success=t["candidates_parse_success"],
            public_candidates_pattern_matched=t["candidates_pattern_matched"],
            public_candidates_built=t["candidates_built"],
            public_candidates_store_attempted=t["candidates_store_attempted"],
            public_candidates_stored=t["candidates_stored"],
            public_candidates_rejected=t["candidates_rejected"],
            public_rejection_summary=t["rejection_summary"],
            public_terminal_stage=t["terminal_stage"],
            public_discovery_empty_reason=t["discovery_empty_reason"],
        )


# =============================================================================
# Helper functions referenced by phases
# =============================================================================
# NOTE: _is_threat_query and _filter_public_noise are defined earlier in the file
# (lines ~968 and ~897 respectively). Duplicates removed in F362 fix.

# -----------------------------------------------------------------------------
# UMA helpers
# -----------------------------------------------------------------------------


async def _get_uma_state() -> tuple[str, bool]:
    """Read UMA status via 8AB surface.

    Returns (state_str, io_only_hint).
    Raises: propagates any exception from resource_governor.

    Sprint 8AK: Uses SSOT labels from resource_governor — no localUMA interpretation.
    ISSUE-003 FIX: Uses sample_uma_status_async() instead of sample_uma_status()
    to avoid blocking the event loop with threading.RLock in _record_transition().
    """
    # Sprint 8AB surface — lazy import to avoid module-level side effects
    from hledac.universal._core.resource_governor import (
        evaluate_uma_state,
        sample_uma_status_async,
    )

    status = await sample_uma_status_async()
    state = evaluate_uma_state(status.system_used_gib)
    io_only = status.io_only
    return state, io_only


# -----------------------------------------------------------------------------
# Finding ID helper
# -----------------------------------------------------------------------------

def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """Deterministic finding ID via SHA-256 hash of pipeline inputs.

    hash() is forbidden (non-deterministic across processes).
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    # xxhash — non-cryptographic, 10-20× faster than sha256 for dedup keys
    # F265C: Use centralized rust backend
    try:
        from hledac.universal._core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.hash is not None:
            return _rust_backend.hash.content_hash_hex(key)
        raise ImportError("Rust hash not available")
    except Exception:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# -----------------------------------------------------------------------------
# Context window helper
# -----------------------------------------------------------------------------
# Sentinel: use a private module-level constant so the call site is self-explanatory
_NO_HIT_START = object()


def _pattern_context(
    text: str,
    start: int,
    end: int,
    radius: int = _FINDING_ID_CONTEXT_RADIUS,
) -> str:
    """Extract a context window around a pattern hit.

    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    if start is _NO_HIT_START or end is _NO_HIT_START:
        return text[:MAX_EXTRACTED_TEXT_CHARS]
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


def _js_confidence_from_verdict(
    verdict: str,
    status_code: int | None = None,
    content_length: int | None = None,
) -> float:
    """Derive js_confidence from verdict string and response signals."""
    if "RETRY_JS:thin_text_strong_signal" in verdict:
        return 0.85
    if "RETRY_JS" in verdict:
        return 0.70
    if status_code in (403, 429):
        return 0.45
    if content_length is not None and content_length < 500:
        return 0.55
    return 0.30


# -----------------------------------------------------------------------------
# Text enrichment with discovery metadata (Sprint F150I)
# Prepend title/snippet to extracted text so pattern scanner gets better signal.
# Hard-capped, M1-safe, no new dependency.
# -----------------------------------------------------------------------------


def _enrich_text_with_metadata(
    title: str,
    snippet: str,
    extracted_text: str,
) -> str:
    """Build a bounded scan text from: [title] [snippet] [extracted_content].

    Rationale: title + snippet contain query-aware signal that raw HTML→text
    loses (e.g. search engine bolded terms). Prepending them gives pattern
    matcher better context without any LLM or external call.

    The result is hard-capped at MAX_EXTRACTED_TEXT_CHARS.

    FIX (F300): HTML-strip title and snippet before concatenation.
    Discovery providers return raw HTML in title/snippet (e.g. <b>bold</b> terms).
    Without stripping, HTML tag characters (<, >, /) create false word boundaries
    in PatternMatcher's boundary_policy="word" check, causing zero matches.
    """
    # FIX F300: Strip HTML from title and snippet before enrichment
    # Uses the same proven function as the feed pipeline (pipeline/scoring.py)
    try:
        from hledac.universal.pipeline.scoring import _strip_html_tags_from_text
    except ImportError:
        def _strip_html_tags_from_text(text: str) -> str:
            """Minimal fallback: strip <...> tags naively."""
            if not text:
                return ""
            import re as _re
            return _re.sub(r"<[^>]+>", " ", text).strip()
    title_clean = _strip_html_tags_from_text(title) if title else ""
    snippet_clean = _strip_html_tags_from_text(snippet) if snippet else ""

    # Build metadata prefix bounded to MAX_METADATA_PREPEND_CHARS
    meta_parts: list[str] = []
    remaining_meta = MAX_METADATA_PREPEND_CHARS

    if title_clean:
        title_trunc = title_clean[:remaining_meta]
        meta_parts.append(title_trunc)
        remaining_meta -= len(title_trunc)

    if snippet_clean and remaining_meta > 20:
        snippet_trunc = snippet_clean[:remaining_meta]
        meta_parts.append(snippet_trunc)

    meta_prefix = "\n".join(meta_parts) + "\n---\n"

    # Hard cap: meta_prefix + extracted_text capped at MAX_EXTRACTED_TEXT_CHARS
    max_content = MAX_EXTRACTED_TEXT_CHARS - len(meta_prefix)
    if max_content < 0:
        # meta_prefix alone exceeds cap — truncate it
        meta_prefix = meta_prefix[:MAX_EXTRACTED_TEXT_CHARS]
        max_content = 0

    content = extracted_text[:max_content] if max_content > 0 else ""

    return meta_prefix + content


# -----------------------------------------------------------------------------
# Page quality scoring (Sprint F150I)
# Query-aware heuristic for fetch budget prioritization.
# Bounded, no ML, no external calls.
# -----------------------------------------------------------------------------


def _score_page_quality(
    *,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    query: str,
    extracted_text: str,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
) -> str:
    """Return a short quality tier string for a discovered page.

    Signals (compositional, no ML):
    - query-term density in title/snippet
    - URL structural depth
    - text richness (avg word len + word count)
    - discovery hit score / reason (if present)
    - rank priority (top-5 benefit of doubt)
    - pre-filter: skip extremely thin pages

    Returns one of:
      SKIP_WEAK: below minimum — skip immediately
      RETRY_JS: thin text but strong discovery signal — retry with JS rendering (F275)
      weak_low_signal: poor signals even after fetch
      ok: acceptable but not exceptional
      good: strong multi-dimensional signals
      very_good: exceptional signals, full investment warranted
    """
    # --- Discovery signal blend (additive, fail-soft) ------------
    has_discovery_signal = (
        (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
        or (discovery_reason is not None and discovery_reason.strip() != "")
    )
    strong_discovery = (
        discovery_score is not None and discovery_score >= 0.7
    )

    query_lower = query.lower()
    query_terms = frozenset(query_lower.split())

    # --- Title query-term density FIRST (F275) ---
    # Moved before text-length gate so title-rich pages with thin body bypass the gate
    title_words = frozenset(hit_title.lower().split())
    title_query_hits = len(query_terms & title_words)
    title_has_query = title_query_hits > 0

    # --- Snippet query-term density ---
    snippet_words = frozenset(hit_snippet.lower().split())
    snippet_query_hits = len(query_terms & snippet_words)
    snippet_has_query = snippet_query_hits > 0

    # --- Pre-filter: skip pages with almost no content BEFORE signal scoring ---
    # Sprint F163B: apply text-length gate first — avoids wasting compute on dead pages
    # F275: Relaxed gate — title-rich pages (query terms in title) bypass text gate
    title_rich = title_has_query and title_query_hits >= 1
    snippet_rich = snippet_has_query and snippet_query_hits >= 2
    if len(extracted_text) < _PRE_FETCH_TEXT_MIN_CHARS:
        # F275: title/snippet-rich pages survive even with thin body (metadata pages)
        if title_rich or snippet_rich:
            pass  # proceed to scoring
        # F275: RETRY_JS — thin page but strong discovery signal → try JS rendering
        elif strong_discovery:
            return "RETRY_JS:thin_text_strong_signal"
        else:
            return "SKIP_WEAK:very_low_text"

    # --- Signalless gate: very low word-level entropy = spam/placeholder ---
    # Sprint F163B: detect "lorem ipsum" / repetitive filler / template noise
    # This is orthogonal to text length — catches thin-but-long pages
    words = extracted_text.split()
    if len(words) >= 10:
        unique_ratio = len(frozenset(w.lower() for w in words)) / len(words)
        if unique_ratio < 0.25:
            return "SKIP_WEAK:low_entropy"

    # --- URL structural signal -----------------------------------
    url_has_path = "/" in hit_url and len(hit_url.split("/")) > 3

    # --- Text richness -----------------------------------------
    text_len = len(extracted_text)
    word_count = len(extracted_text.split())
    avg_word_len = text_len / max(word_count, 1)
    text_is_meaningful = avg_word_len >= 3.5 and word_count >= 50

    # --- Composite scoring --------------------------------------
    signals_good = sum([
        title_has_query,
        snippet_has_query,
        url_has_path,
        text_is_meaningful,
    ])
    if strong_discovery:
        signals_good += 1  # discovery bonus

    # P2.1: If URL was discovered via bootstrap and is highly relevant, lower pattern match threshold.
    # Bootstrap sources (deterministic, seed_context, rescue, keyword_bootstrap) have synthetic
    # titles/snippets with no query terms but the URL itself is directly related to the query
    # (domain/URL query), so the bootstrap bonus compensates for the lack of title/snippet signal
    # while preserving quality filtering for non-bootstrap URLs.
    _is_bootstrap = (
        discovery_reason in ("deterministic_bootstrap", "seed_context_bootstrap", "rescue")
        or (discovery_reason or "").startswith("keyword_bootstrap_")
    )
    if _is_bootstrap:
        signals_good += 1

    rank_bonus = hit_rank < 5

    # --- Tier determination -------------------------------------
    if signals_good >= 4 or (signals_good >= 3 and (rank_bonus or strong_discovery)):
        return "very_good"
    elif signals_good >= 3:
        return "good"
    elif signals_good >= 2:
        return "ok"
    elif signals_good >= 1:
        return "ok"
    elif has_discovery_signal and text_is_meaningful and text_len > 1000:
        return "ok:no_query_signal"
    else:
        return "weak_low_signal"


# -----------------------------------------------------------------------------
# Per-page usable-value computation (Sprint F150L)
# Bounded heuristic — no new analysis, purely derived from existing buckets.
# -----------------------------------------------------------------------------


def _compute_page_usable_fields(
    *,
    fetched: bool,
    matched_patterns: int,
    stored_findings: int,
    quality_reason: str | None,
    discovery_signal: bool,
    discovery_score: float | None,
    error: str | None,
    extracted_text_len: int = 0,
) -> tuple[bool, str, str, bool, str, str]:
    """Derive usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality from existing page data.

    usable_signal: page contributed to real output (stored findings or strong signal).
    value_tier: conversion quality — high/medium/low/waste.
    resolution_reason: human-readable why the page resolved as it did.
    discovery_false_positive: True if discovery signal was legitimate but page wasted.
    waste_category: "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: "" | "healthy" | "thin" | "dead"

    All derived from existing fields — no new heavy analysis.
    """
    if not fetched or error is not None:
        tier = "waste"
        reason = f"unfetched_or_error:{error or 'none'}"
        false_pos = False
        waste_cat = "error"
        structural = "dead"
        return False, tier, reason, false_pos, waste_cat, structural

    if stored_findings > 0:
        tier = "high"
        reason = "stored_findings"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0 and discovery_signal:
        tier = "medium"
        reason = "patterns_found_discovery_signal"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0:
        tier = "medium"
        reason = "patterns_found_no_discovery"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    # Fetched but nothing matched — distinguish waste categories
    # Sprint F163B: signalless detection BEFORE SKIP_WEAK — signalless is a real category
    if not discovery_signal:
        # No discovery signal at all — signalless waste (not structural)
        tier = "waste"
        reason = quality_reason or "no_discovery_signal"
        false_pos = False
        waste_cat = "signalless"
        structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
        return False, tier, reason, false_pos, waste_cat, structural

    if discovery_score is not None and discovery_score >= _DISCOVERY_FALSE_POSITIVE_THRESHOLD:
        # Sprint F161B: legitimate discovery signal, no pattern yield = false positive
        tier = "low"
        reason = "discovery_signal_no_patterns"
        false_pos = True
        waste_cat = "false_positive"
        structural = "healthy" if extracted_text_len >= _PRE_FETCH_TEXT_MIN_CHARS else "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    if quality_reason is not None and quality_reason.startswith("SKIP_WEAK"):
        tier = "waste"
        reason = f"quality_skip:{quality_reason}"
        false_pos = False
        waste_cat = "structural"
        structural = "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    # F275: RETRY_JS verdict — in-flight JS retry attempt, not yet resolved
    if quality_reason is not None and quality_reason.startswith("RETRY_JS"):
        tier = "medium"
        reason = f"js_retry_pending:{quality_reason}"
        false_pos = False
        waste_cat = ""
        structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
        return False, tier, reason, false_pos, waste_cat, structural

    # Final fallback
    tier = "waste"
    reason = quality_reason or "no_match_no_signal"
    false_pos = False
    waste_cat = "signalless"
    structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
    return False, tier, reason, false_pos, waste_cat, structural


# -----------------------------------------------------------------------------
# PatternMatcher helpers
# -----------------------------------------------------------------------------


def _get_patterns_configured_count() -> int:
    """Return current pattern count from singleton registry (0 if dirty/empty)."""
    state = sys.modules["hledac.universal.patterns.pattern_matcher"]._matcher_state
    return len(state._registry_snapshot) if state._registry_snapshot else 0


# -----------------------------------------------------------------------------
# Per-page finding extraction
# -----------------------------------------------------------------------------


async def _build_public_finding(
    *,
    query: str,
    url: str,
    page_text: str,
    hit_title: str,
    hit_snippet: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    http_status_code: int = 0,
) -> tuple:
    """F226B: Build a public-surface CanonicalFinding from a non-pattern-maching page.

    Called when a page fetches successfully, extracts text, but has zero pattern
    matches AND is NOT skipped by quality gate (SKIP_WEAK) — i.e. a "content-only" page
    that provides public surface evidence.

    Also called for bootstrap pages (robots.txt, security.txt, sitemap.xml) that
    have meaningful content even without pattern matches.

    Does NOT bypass quality gate — SKIP_WEAK pages still return empty tuple.

    Returns:
        Tuple of (CanonicalFinding,) or () if page provides no actionable signal.

    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    # P0-FIX (F290): Accept title+snippet even without body text.
    # SERP pages often have no body content but meaningful title/snippet.
    if not page_text or not page_text.strip():
        # Only return () if we have NEITHER title NOR snippet
        if not hit_title and not hit_snippet:
            return ()
        # Fall through with empty page_text — title/snippet will still be used

    # Bounded payload from title + snippet + first chars of body + status
    payload_parts: list[str] = []
    if hit_title:
        payload_parts.append(f"title: {hit_title[:200]}")
    if hit_snippet:
        payload_parts.append(f"snippet: {hit_snippet[:300]}")
    # Include first 500 chars of body as surface evidence (may be empty)
    body_preview = page_text[:500].strip() if page_text else ""
    if body_preview:
        payload_parts.append(f"body: {body_preview}")
    if http_status_code > 0:
        payload_parts.append(f"status: {http_status_code}")
    if not payload_parts:
        return ()

    payload_text = "\n".join(payload_parts)
    # Hard cap
    if len(payload_text) > 2000:
        payload_text = payload_text[:2000]

    # Provenance tags
    provenance_parts = [
        "source_family:public",
        f"url:{url[:300]}",
        "label:public_surface",
    ]
    if discovery_score is not None:
        provenance_parts.append(f"score:{discovery_score:.2f}")
    if discovery_reason:
        provenance_parts.append(f"reason:{discovery_reason[:100]}")
    provenance: tuple[str, ...] = tuple(provenance_parts)

    # Deterministic finding_id using same scheme as pattern findings
    finding_id = _make_finding_id(
        query=query,
        url=url,
        label="public_surface",
        pattern="content_only",
        value=payload_text[:100],
    )

    try:
        finding = CanonicalFinding(
            finding_id=finding_id,
            query=query[:500],
            source_type=_PUBLIC_SOURCE_TYPE,
            confidence=0.65,  # P0-B FIX: Raised from 0.55 — bootstrap SERP pages are valid discovery
            ts=time.time(),
            provenance=provenance,
            payload_text=payload_text,
        )
        return (finding,)
    except Exception:
        return ()


async def _extract_live_public_findings_from_page(
    *,
    query: str,
    url: str,
    hit_label: str,
    hit_pattern: str,
    hit_value: str,
    hit_start: int,
    hit_end: int,
    page_text: str,
    discovery_score: float | None = None,
) -> tuple:  # CanonicalFinding — imported lazily to satisfy runtime
    """Construct CanonicalFinding for a single PatternHit.

    All heavy work (context extraction) offloaded to thread executor.
    """
    # Lazy import to avoid TYPE_CHECKING-only circular issues at runtime
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.runtime.worker_pool import get_rust_pool

    # Extract context in rayon pool — ISSUE 3.1 FIX: was run_in_cpu_pool_async
    # which used cpu_pool_run (GIL wrapper only, no rayon). Now uses channel dispatch.
    pool = get_rust_pool("cpu")
    context: str = await pool.submit(_pattern_context, page_text, hit_start, hit_end)

    # Truncate to hard cap (double-check since context is already bounded)
    if len(context) > MAX_EXTRACTED_TEXT_CHARS:
        context = context[:MAX_EXTRACTED_TEXT_CHARS]

    finding_id = _make_finding_id(query, url, hit_label, hit_pattern, hit_value)

    # provenance: (source_family, source, url, hit_label, hit_pattern)
    provenance: tuple[str, ...] = ("source_family:public", "duckduckgo", url, hit_label or "", hit_pattern)

    # F234: propagate discovery_score as finding confidence if available
    if discovery_score is not None:
        confidence = float(max(0.0, min(1.0, discovery_score)))
    else:
        confidence = _DEFAULT_CONFIDENCE

    finding = CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=_SOURCE_TYPE,
        confidence=confidence,
        ts=time.time(),
        provenance=provenance,
        payload_text=context,
    )
    return (finding,)


# -----------------------------------------------------------------------------
# Single-page fetch + extract + match + store
# Extracted to pipeline/public_fetch.py — this module re-exports for compatibility
# -----------------------------------------------------------------------------


async def _fetch_and_process_page(
    *,
    semaphore: asyncio.Semaphore,
    query: str,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    fetch_timeout_s: float,
    fetch_max_bytes: int,
    store: Any | None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
    vector_store: Any | None = None,
    graph: Any | None = None,
) -> PipelinePageResult:
    """Delegate to public_fetch module (extracted from this file)."""
    from .public_fetch import _fetch_and_process_page as _impl

    return await _impl(
        semaphore=semaphore,
        query=query,
        hit_url=hit_url,
        hit_title=hit_title,
        hit_snippet=hit_snippet,
        hit_rank=hit_rank,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        store=store,
        memory_manager=memory_manager,
        session_id=session_id,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
        vector_store=vector_store,
        graph=graph,
    )


# ---- Legacy fetch/match imports (delegated to public_fetch module) ------------------


def _patch_fetcher_and_matcher(fetch_fn: Any, match_fn: Any) -> None:
    """Legacy compatibility: delegate to public_fetch module."""
    from . import public_fetch
    public_fetch._patch_fetcher_and_matcher(fetch_fn, match_fn)


def _ensure_patched() -> None:
    """Legacy compatibility: delegate to public_fetch module."""
    from . import public_fetch
    public_fetch._ensure_patched()


# -----------------------------------------------------------------------------
# P6: OSINT Report Generation
# -----------------------------------------------------------------------------


async def _generate_and_store_report(
    query: str,
    pages: tuple,
    store: Any | None,
    hermes_engine: Any | None,
    vector_store: Any | None = None,
) -> str:
    """P6: Generate OSINT report from top findings and store in DuckDB.

    Fail-soft: returns empty string on any error. Pipeline continues regardless.
    """
    if hermes_engine is None:
        return ""
    vector_candidates = await _vector_search_context(query, vector_store)
    sorted_pages = sorted(pages, key=lambda p: (p.matched_patterns or 0, p.accepted_findings or 0), reverse=True)
    top_pages = sorted_pages[:_REPORT_TOP_N]
    if not top_pages:
        return ""
    context_items = _build_report_context(pages, top_pages, vector_candidates)
    report_text = await _generate_routed_report(query, context_items, hermes_engine)
    if not report_text:
        return ""
    if store is not None:
        report_id = _make_finding_id(query=query, url="synthetic://report", label="osint_report", pattern="synthetic", value=report_text[:200])
        await _store_report_and_inference(store, query, report_text, report_id, hermes_engine)
    return report_text


async def _vector_search_context(query: str, vector_store) -> list:
    """P13: Perform vector search for RAG context."""
    if vector_store is None:
        return []
    try:
        from hledac.universal.brain.model_manager import get_model_manager
        from hledac.universal.embedding_pipeline import embed_query_async
        model_manager = get_model_manager()
        async with model_manager.embedding_lifecycle():
            query_vec = await embed_query_async(query)
            raw_similar = vector_store.query(query_vec, k=10, index_type="text")
            if raw_similar:
                logger.info(f"[P13] Vector search found {len(raw_similar)} similar docs")
            return raw_similar or []
    except Exception as e:
        logger.warning(f"[P13] Vector search failed: {e}")
        return []


def _build_report_context(pages: tuple, top_pages: list, vector_candidates: list) -> list[str]:
    """Build context items from pages with RRF fusion."""
    pattern_ranked = [(getattr(p, "url", "") or "", (p.matched_patterns or 0) + (p.accepted_findings or 0) * 0.5) for p in top_pages if getattr(p, "url", "")]
    if vector_candidates and pattern_ranked:
        try:
            from hledac.universal.utils.ranking import rrf_fuse
            fused_ids = rrf_fuse([vector_candidates, pattern_ranked], k=60)
            url_order = fused_ids[:_REPORT_TOP_N]
        except Exception:
            url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    else:
        url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    url_to_page = {getattr(p, "url", ""): p for p in pages}
    context_items = []
    for url in url_order:
        page = url_to_page.get(url)
        if page:
            context_items.append(f"URL: {url}\nTitle/Reason: {getattr(page, 'discovery_reason', '') or getattr(page, 'quality_reason', '') or url}\nIOC count: {page.matched_patterns or 0}, Accepted findings: {page.accepted_findings or 0}")
    return context_items


async def _generate_routed_report(query: str, context_items: list, hermes_engine) -> str:
    """Route model and generate report."""
    try:
        from hledac.universal.brain.moe_router import route as moe_route
        model_choice = moe_route(query, {"urls": []})
    except Exception:
        model_choice = "hermes"
    try:
        match model_choice:
            case "vision":
                return "[image description] " + "\n".join(context_items[:3])
            case "modernbert":
                try:
                    from hledac.universal.brain.modernbert_engine import ModernBertEngine
                    return await ModernBertEngine().summarize(context_items)
                except Exception as e:
                    logger.warning(f"[P14] ModernBERT failed: {e}")
                    return await hermes_engine.generate_report(query, context_items)
            case _:
                return await hermes_engine.generate_report(query, context_items)
    except Exception as e:
        logger.warning(f"[REPORT] Generation failed: {e}")
        return ""


async def _store_report_and_inference(store, query: str, report_text: str, report_id: str, hermes_engine) -> None:
    """Store report and Hermes inference findings."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    try:
        report_finding = CanonicalFinding(
            finding_id=report_id, query=query, source_type=_REPORT_SOURCE_TYPE,
            confidence=0.7, ts=time.time(),
            provenance=("source_family:public", "report_generation", hermes_engine.__class__.__name__),
            payload_text=report_text,
        )
        await store.submit_findings([report_finding])
        logger.info(f"[REPORT] Stored report {report_id[:8]}")
    except Exception as e:
        logger.warning(f"[REPORT] Storage failed: {e}")
    try:
        from hledac.universal.runtime.hermes_pivot_contract import HermesInferenceOutput
        key_iocs, key_entities = await _extract_iocs_from_report(report_text)
        hermes_output = HermesInferenceOutput(
            output_id=report_id, source_finding_id=report_id, inference_type="report_synthesis",
            timestamp=time.time(), primary_text=report_text, confidence=0.7,
            key_iocs=key_iocs, key_entities=key_entities, pivot_suggestions=key_iocs[:10],
            bounded=False, tokens_used=0, model_name=hermes_engine.__class__.__name__, source_hints=("public",),
        )
        hermes_finding = CanonicalFinding(
            finding_id=hermes_output.output_id, query=query, source_type="hermes_inference",
            confidence=hermes_output.confidence, ts=hermes_output.timestamp,
            provenance=("source_family:public", "hermes_inference", hermes_engine.__class__.__name__),
            payload_text=_json.encode(hermes_output.to_dict()).decode("utf-8")[:4096],
        )
        await store.submit_findings([hermes_finding])
        logger.info(f"[F256] Stored hermes_inference {hermes_output.output_id[:8]}")
    except Exception as e:
        logger.warning(f"[F256] HermesInferenceOutput failed: {e}")


async def _extract_iocs_from_report(report_text: str) -> tuple[list[str], list[str]]:
    """Extract IOCs and entities from report text."""
    key_iocs, key_entities = [], []
    ioc_json = re.search(r"<IOC_JSON>\s*(\{.*?\})\s*</IOC_JSON>", report_text, re.DOTALL)
    if ioc_json:
        try:
            ioc_data = _json.decode(ioc_json.group(1))
            return list(ioc_data.get("iocs", [])[:20]), list(ioc_data.get("entities", [])[:20])
        except (ValueError, KeyError):
            pass
    try:
        from hledac.universal.utils.ioc_extract import extract_iocs_single
        ioc_tuples = await extract_iocs_single(report_text)
        return [v for _, v in ioc_tuples if len(v) > 3][:20], [v for t, v in ioc_tuples if t in ("org", "person", "gpe", "product")][:20]
    except ImportError:
        try:
            from hledac.universal.brain.ner_engine import extract_iocs_from_text
            ioc_results = extract_iocs_from_text(report_text)
            return [r["value"] for r in ioc_results if r.get("value") and len(r["value"]) > 3][:20], [r["value"] for r in ioc_results if r.get("ioc_type") in ("org", "person", "gpe", "product")][:20]
        except Exception:
            pass
    return [], []



def _build_domain_candidates(query: str) -> list[str]:
    """F363: Extract domain-like candidates from query (shared helper).

    Handles pure domain ("example.com") and mixed OSINT queries
    ("certificate transparency subdomains of mozilla.org" -> "mozilla.org").
    """
    q = query.strip()
    if not q or len(q) > 253:
        return []
    candidates = [q]
    for token in q.split():
        if "." in token and token != q:
            candidates.append(token)
    return candidates


def _query_looks_like_domain(query: str) -> bool:
    """Sprint F188B: Detect if query is a domain name suitable for CT subdomain lookup.

    Returns True for "example.com", "api.example.com", "*.example.com".
    Returns False for "apple inc", "what is DNS", "site:example.com".

    F233E: Also try token with a dot for mixed OSINT queries like
    "certificate transparency subdomains of mozilla.org" — the token
    "mozilla.org" has a dot and is the domain candidate.

    F363: Refactored to use shared _build_domain_candidates helper.
    """
    return any(_CT_QUERY_IS_DOMAIN_RE.match(c) for c in _build_domain_candidates(query))


def _extract_base_domain(domain: str) -> str:
    """Sprint F188B: Extract base domain from a domain string for CT scanner input.

    "www.example.com" -> "example.com"
    "api.example.com" -> "example.com"
    "example.com"     -> "example.com"
    "*.example.com"   -> "example.com"

    Returns the input unchanged if it can't be parsed.
    """
    # Remove wildcard prefix
    if domain.startswith("*."):
        domain = domain[2:]
    parts = domain.split(".")
    if len(parts) >= 3:
        # Heuristic: last two parts are the registered domain
        return ".".join(parts[-2:])
    return domain


async def _inject_ct_subdomain_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """Sprint F188B: Thin CT winner-slice adapter.

    If query looks like a domain, call the CT scanner to get subdomains,
    synthesize them as high-confidence discovery hits, and prepend to the
    existing hits tuple.

    Fail-soft: scanner errors or non-domain queries return hits unchanged.
    Bounded: at most _CT_SUBDOMAIN_BOUND subdomains injected.
    M1-safe: CT scanner owns its cache; shared session reuse via async_session.

    This is NOT a new discovery world — it augments existing discovery hits
    with CT-sourced subdomains within the same fetch batch.
    """
    global _CT_SCANNER_GET_SUBDOMAINS

    if not hits or not _query_looks_like_domain(query):
        return hits

    _ensure_ct_scanner_patched()
    if _CT_SCANNER_GET_SUBDOMAINS is None:
        return hits

    base_domain = _extract_base_domain(query)

    # F4XX: use shared httpx session for connection pooling
    shared_session = None
    try:
        from hledac.universal.network.session_runtime import async_get_httpx_session
        shared_session = await async_get_httpx_session()
    except Exception:  # noqa: BLE001
        pass

    try:
        subdomains: list[str] = await _CT_SCANNER_GET_SUBDOMAINS(
            base_domain, async_session=shared_session
        )
    except Exception:
        subdomains = []

    if not subdomains:
        return hits

    subdomains = subdomains[:_CT_SUBDOMAIN_BOUND]

    # Sprint F188B: synthesize CT hits as simple structs with the same
    # attribute interface that _fetch_and_process_page expects.
    # Attribute-based access: hit.url, hit.title, hit.snippet, hit.rank, hit.score, hit.reason
    class _CTHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, rank: int):
            self.url = url
            self.title = f"[CT] {url}"
            self.snippet = f"Certificate Transparency subdomain of {base_domain}"
            self.rank = rank
            self.score = _CT_SUBDOMAIN_SCORE
            self.reason = "ct_subdomain"

    ct_hits = tuple(
        _CTHit(f"https://{subdomain}", idx) for idx, subdomain in enumerate(subdomains)
    )
    return ct_hits + hits


# F192E: CommonCrawl domain discovery injection
_CC_SCANNER_LOOKUP: Any = None


def _query_looks_like_domain_for_cc(query: str) -> bool:
    """F192E: Detect if query is a domain name suitable for CommonCrawl CDX lookup.

    Returns True for "example.com", "*.example.com", "site:example.com".
    Returns False for "apple inc", "what is DNS", etc.

    F233E: Also try token with a dot for mixed OSINT queries.

    F363: Refactored to use shared _build_domain_candidates helper.
    """
    return any(_CC_QUERY_IS_DOMAIN_RE.match(c) for c in _build_domain_candidates(query))


async def _inject_commoncrawl_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """F192E: Thin CommonCrawl CDX injection as discovery augmentation.

    CommonCrawl CDX API is a domain index (historical URL archive), not a
    general search engine. It only activates for domain-like queries.

    This is NOT a new discovery world — it augments existing discovery hits
    with CC-sourced archived URLs within the same fetch batch.

    Fail-soft: CC errors or non-domain queries return hits unchanged.
    Bounded: at most 20 CC results injected.
    M1-safe: adapter owns its HTTP calls, shared session reuse.
    """
    global _CC_SCANNER_LOOKUP

    if not hits or not _query_looks_like_domain_for_cc(query):
        return hits

    # Lazy-patch CommonCrawl scanner
    if _CC_SCANNER_LOOKUP is None:
        try:
            from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

            class _MinimalStealth:
                async def get(self, url: str) -> str:
                    from hledac.universal.network.session_runtime import async_get_httpx_session
                    s = await async_get_httpx_session()
                    async with s.get(url) as r:
                        return await r.text()

            _CC_SCANNER_LOOKUP = CommonCrawlAdapter(stealth=_MinimalStealth())
        except Exception:
            return hits

    # Extract domain from query (strip site:/domain: prefix)
    import re
    clean_domain = re.sub(r"^(site|domain):", "", query.strip(), flags=re.IGNORECASE).strip()
    if not clean_domain:
        return hits

    try:
        cc_results: list = await _CC_SCANNER_LOOKUP.search(clean_domain, max_results=20)
    except Exception:
        return hits

    if not cc_results:
        return hits

    # Synthesize CC hits as simple attribute-based objects (same interface as CT hits)
    class _CCHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, title: str, snippet: str, rank: int):
            self.url = url
            self.title = title
            self.snippet = snippet
            self.rank = rank
            self.score = 0.75  # F192E: CC hits get strong baseline score
            self.reason = "commoncrawl_archive"

    cc_hits = tuple(
        _CCHit(
            url=r.get("url", ""),
            title=r.get("title", ""),
            snippet=r.get("snippet", ""),
            rank=idx,
        )
        for idx, r in enumerate(cc_results[:20])
    )
    # Prepend CC hits to give them priority in the fetch batch
    return cc_hits + hits


# Sprint F193A: Onion discovery + scraping block
# B18 FIX: Adaptive cap based on available RAM.
# M1 8GB UMA: hard cap 2 TOR circuits at <2GB available, 3 at 2-4GB, 4 at >4GB.
# Each .onion fetch holds a TOR circuit for ~2-5s = ~512MB per concurrent circuit.
def _get_onion_cap() -> int:
    """Return adaptive .onion concurrency cap based on available system RAM."""
    import psutil as _psutil
    try:
        _vm = _psutil.virtual_memory()
        _avail_gib = _vm.available / (1024**3)
        if _avail_gib < 2.0:
            return 1  # extreme memory pressure
        elif _avail_gib < 4.0:
            return 2  # M1 8GB under load
        else:
            return 3  # headroom available
    except Exception:
        return 2  # safe default

_ONION_HIT_MAX = 5  # legacy constant — use _get_onion_cap() at call time
_ONION_CIRCUIT_FAIL_LIMIT = 3
_onion_circuit_state = {"failures": 0, "opened_at": 0.0}
_onion_circuit_lock = LazyAsyncioLock()


def _onion_circuit_is_open() -> bool:
    """Check if onion circuit breaker is open."""
    if _onion_circuit_state["failures"] < _ONION_CIRCUIT_FAIL_LIMIT:
        return False
    if time.time() - _onion_circuit_state["opened_at"] >= 60.0:
        _onion_circuit_state["failures"] = 0
        _onion_circuit_state["opened_at"] = 0.0
        return False
    return True


def _onion_circuit_record_failure() -> None:
    """Record a failure in the onion circuit breaker."""
    _onion_circuit_state["failures"] += 1
    if _onion_circuit_state["failures"] >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_state["opened_at"] = time.time()
        logger.warning("[F193A] Onion circuit breaker OPEN — pausing 60s")


async def _inject_onion_hits(
    hits: tuple,
    query: str,
    store: DuckDBShadowStore,
) -> int:
    """Sprint F193A: Onion discovery + scraping via Tor.

    Discovers .onion URLs via Ahmia search and scrapes them using
    Tor-capable async_fetch_public_text(). Converts results to CanonicalFinding
    and stores via duckdb_store.

    Bounded: max 5 onion hits, circuit breaker after 3 failures, fail-soft.
    Returns number of onion findings stored.
    """
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    # Quick check: skip if circuit is open
    if _onion_circuit_is_open():
        return 0

    # Detect .onion URLs in existing hits (already discovered)
    onion_urls: list[str] = []
    for hit in hits:
        url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else None)
        if url and ".onion" in url.lower():
            onion_urls.append(url if url.startswith("http") else f"http://{url}")

    if not onion_urls:
        return 0

    # B18 FIX: use adaptive cap instead of hardcoded _ONION_HIT_MAX
    _onion_cap = _get_onion_cap()
    onion_urls = onion_urls[:_onion_cap]

    findings: list[CanonicalFinding] = []
    ts_now = time.time()
    failure_count = 0

    # F320: Parallel fetch — replaced sequential await loop with safe_gather.
    # Each coroutine fetches one .onion URL concurrently. Tor is already
    # serialized by its own circuit semaphore, so this parallelizes across
    # multiple .onion targets (typically 2-5) rather than within Tor itself.
    async def _fetch_one_onion(onion_url: str) -> CanonicalFinding | None:
        try:
            result = await async_fetch_public_text(
                onion_url,
                timeout_s=30.0,
                max_bytes=200_000,
            )
            if result.error or result.text is None:
                return None

            content = result.text
            pf_id = hashlib.sha256(
                f"{query}\x00{onion_url}\x00onion_discovery".encode()
            ).hexdigest()[:16]

            return CanonicalFinding(
                finding_id=pf_id,
                query=query,
                source_type="onion_discovery",
                confidence=0.55,
                ts=ts_now,
                provenance=("onion_discovery", onion_url),
                payload_text=content[:500] if content else None,
            )
        except Exception as e:
            logger.debug(f"[F193A] Onion fetch {onion_url}: {e}")
            return None

    _result = await parallel(
        [_fetch_one_onion(url) for url in onion_urls],
        policy="collect",
        concurrency=_onion_cap,
        ctx="onion_hits",
    )
    for finding in _result.ok:
        if finding is not None:
            findings.append(finding)

    successful_urls = {f.provenance[1] for f in findings}
    failure_count = sum(1 for url in onion_urls if url not in successful_urls)
    if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_record_failure()

    if findings and store is not None:
        try:
            await store.submit_findings(findings)
            logger.info(f"[F193A] Stored {len(findings)} onion findings")
        except Exception as e:
            logger.debug(f"[F193A] Onion findings persist failed: {e}")

    return len(findings)


async def async_run_live_public_pipeline(
    query: str,
    store: DuckDBShadowStore | None = None,
    max_results: int = 10,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2_000_000,
    fetch_concurrency: int = 8,
    hermes_engine: Any | None = None,
    graph: Any | None = None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    vector_store: Any | None = None,
    run_loop: bool = False,
    rl_steps: int = 0,
    enqueue_hypothesis_pivot: Any | None = None,
    public_bootstrap_enabled: bool = False,
    seed_context: Any | None = None,
    fetch_fn: Any | None = None,
    match_fn: Any | None = None,
    discovery_fn: Any | None = None,
    ct_subdomains_fn: Any | None = None,
    clear_query_cache_fn: Any | None = None,
    export_dir: str | None = None,
    _sprint_id: str = "",
) -> PipelineRunResult:
    """F360: Refactored pipeline using phase-based architecture.

    Phases:
        1. Initialization - reset temporal layer, clear caches, DI resolution
        2. ResourceGovernance - UMA state check, concurrency setup
        3. DiscoveryRunner - execute discovery using DiscoveryEngine
        4. FetchOrchestrator - parallel fetch execution
        5. TelemetryAggregator - compute all run-level telemetry
        6. ReportGenerator - OSINT report, RL loop, hypothesis + ToT
        7. SynthesisRunner - LLM synthesis (M1 8GB safe)
        8. ExportManager - markdown and graph export
        9. TemporalPersistence - save signal snapshot
    """
    # DI F226: Resolve dependency injection seams
    if fetch_fn is not None:
        from . import public_fetch as _pf
        _pf._ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    if match_fn is not None:
        from . import public_fetch as _pf
        _pf._SYNC_MATCH_TEXT = match_fn
    if discovery_fn is not None:
        global _ASYNC_DISCOVERY_SEARCH
        _ASYNC_DISCOVERY_SEARCH = discovery_fn
    if ct_subdomains_fn is not None:
        global _CT_SCANNER_GET_SUBDOMAINS
        _CT_SCANNER_GET_SUBDOMAINS = ct_subdomains_fn

    _ensure_patched()

    # Initialize pipeline context
    ctx = PipelineContext(
        query=query,
        store=store,
        max_results=max_results,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        fetch_concurrency=fetch_concurrency,
        hermes_engine=hermes_engine,
        graph=graph,
        memory_manager=memory_manager,
        session_id=session_id,
        vector_store=vector_store,
        run_loop=run_loop,
        rl_steps=rl_steps,
        enqueue_hypothesis_pivot=enqueue_hypothesis_pivot,
        public_bootstrap_enabled=public_bootstrap_enabled,
        seed_context=seed_context,
        export_dir=export_dir,
        _sprint_id=_sprint_id,
        clear_query_cache_fn=clear_query_cache_fn,
    )

    # === Phase 1: Initialization ===
    phase1 = Phase1_Initialization()
    ctx = await phase1.run(ctx)

    # === Phase 2: Resource Governance (UMA check) ===
    phase2 = Phase2_ResourceGovernance()
    ctx = await phase2.run(ctx)

    # === Emergency abort if UMA emergency state ===
    if ctx._is_emergency:
        return _build_emergency_result(ctx)

    # === Phase 3: Discovery ===
    phase3 = Phase3_DiscoveryRunner()
    ctx, discovery = await phase3.run(ctx)

    # Unpack stage failure from discovery telemetry
    public_stage_failure = discovery.discovery_telemetry.get("public_stage_failure")
    public_stage_failure_reason = discovery.discovery_telemetry.get("public_stage_failure_reason")

    # === Phase 4: Fetch Orchestration ===
    phase4 = Phase4_FetchOrchestrator()
    ctx, all_page_results = await phase4.run(ctx, discovery)

    # === Phase 5: Telemetry Aggregation ===
    phase5 = Phase5_TelemetryAggregator()
    telemetry = phase5.run(ctx, discovery)

    # === Phase 6: Report Generation (OSINT, RL, ToT) ===
    phase6 = Phase6_ReportGenerator()
    ctx, generated_report, tot_solution_count = await phase6.run(ctx, all_page_results)

    # === Phase 7: Synthesis (if conditions met) ===
    phase7 = Phase7_SynthesisRunner()
    await phase7.run(ctx, telemetry["total_stored"])

    # === Phase 8: Export (if no errors) ===
    if ctx.error is None:
        phase8 = Phase8_ExportManager()
        await phase8.run(ctx, generated_report, all_page_results)

    # === Phase 9: Temporal Persistence ===
    phase9 = Phase9_TemporalPersistence()
    temporal_status = phase9.run()
    ctx = PipelineContext(**{**ctx.__dict__, **temporal_status})

    # === Build Final Result ===
    return ResultBuilder.build(
        ctx=ctx,
        telemetry=telemetry,
        discovery=discovery,
        public_stage_failure=public_stage_failure,
        public_stage_failure_reason=public_stage_failure_reason,
        generated_report=generated_report,
        tot_solution_count=tot_solution_count,
    )


def _build_emergency_result(ctx: PipelineContext) -> PipelineRunResult:
    """Build emergency abort result for UMA emergency state."""
    return PipelineRunResult(
        query=ctx.query,
        discovered=0,
        fetched=0,
        matched_patterns=0,
        accepted_findings=0,
        stored_findings=0,
        patterns_configured=_get_patterns_configured_count(),
        pages=(),
        error="uma_emergency_abort",
        public_discovery_blocker="uma_emergency_abort",
        public_fetch_accessibility_blocker=False,
        public_discovery_fallback_state=None,
        dominant_public_failure_mode="uma_emergency_abort",
        public_stage_failure="uma_emergency",
        public_stage_failure_reason="UMA emergency state blocks all public lane processing",
        public_discovery_attempted=False,
        public_discovery_raw_count=0,
        public_discovery_deduped_count=0,
        public_pages_fetched=0,
        public_pages_accepted=0,
        public_pages_rejected=0,
        public_findings_accepted=0,
        public_fetch_gate="emergency_blocked",
        public_discovered=0,
        public_fetch_attempted=0,
        public_fetch_skipped=0,
        public_fetch_candidate_count=0,
        public_fetch_attempted_urls_sample=(),
        public_acceptance_attempted=0,
        public_acceptance_accepted=0,
        public_acceptance_rejected=0,
        public_acceptance_reject_reasons={},
        public_accepted_url_sample=(),
        public_rejected_url_sample=(),
        public_terminal_classified_count=0,
        public_unclassified_count=0,
        public_terminal_reason_counts={},
        public_fetch_success=0,
        public_fetch_failed=0,
        public_skipped_duplicate=0,
        public_skipped_unsupported_scheme=0,
        public_skipped_memory_gate=0,
        public_skipped_quality_gate=0,
        public_skipped_browser_unavailable=0,
        public_skipped_xml_or_feed=0,
        public_skipped_timeout=0,
        public_skipped_fetch_error=0,
        public_rejected_no_pattern_match=0,
        public_rejected_low_information=0,
        public_rejected_duplicate=0,
        public_rejected_storage_rejected=0,
        public_build_success_count=0,
        public_build_failure_count=0,
        public_duplicate_count=0,
        public_acceptance_ratio=0.0,
        public_skipped_url_sample=(),
        public_rejected_url_samples=(),
        public_candidates_discovered=0,
        public_candidates_fetch_attempted=0,
        public_candidates_fetch_success=0,
        public_candidates_parse_success=0,
        public_candidates_pattern_matched=0,
        public_candidates_built=0,
        public_candidates_store_attempted=0,
        public_candidates_stored=0,
        public_candidates_rejected=0,
        public_rejection_summary={},
        public_rescue_candidates_count=0,
        public_rescue_fetch_attempted=0,
        public_rescue_fetch_success=0,
        public_rescue_accepted_findings=0,
        public_rescue_errors=0,
        public_rescue_order="disabled",
        public_terminal_stage="uma_emergency",
    )


def _ensure_discovery_patched() -> None:
    global _ASYNC_DISCOVERY_SEARCH
    if _ASYNC_DISCOVERY_SEARCH is None:
        # Sprint F206AO: env-gated providerless cascade wiring via LaneRegistry
        if LANE_REGISTRY.is_enabled("providerless_discovery"):
            from hledac.universal.discovery.cascade import (
                async_search_providerless,
            )
            _ASYNC_DISCOVERY_SEARCH = async_search_providerless
        else:
            from hledac.universal.discovery.duckduckgo_adapter import (
                async_search_public_web,
            )
            _ASYNC_DISCOVERY_SEARCH = async_search_public_web


# Ensure discovery is patched on module import
_ensure_discovery_patched()


def _patch_ct_scanner(get_subdomains_fn: Any) -> None:
    """Patch in a CT scanner get_subdomains(domain, async_session) -> list[str]."""
    global _CT_SCANNER_GET_SUBDOMAINS
    _CT_SCANNER_GET_SUBDOMAINS = get_subdomains_fn


def _ensure_ct_scanner_patched() -> None:
    """Lazily patch the CT scanner from network.ct_log_scanner."""
    global _CT_SCANNER_GET_SUBDOMAINS
    if _CT_SCANNER_GET_SUBDOMAINS is not None:
        return
    try:
        from hledac.universal.network.ct_log_scanner import _CTLogScanner

        _scanner = _CTLogScanner(allow_external=True, cache_ttl_days=30)

        async def _get_subdomains(
            domain: str, async_session: Any = None
        ) -> list[str]:
            return await _scanner.get_subdomains(domain, async_session=async_session)

        _CT_SCANNER_GET_SUBDOMAINS = _get_subdomains
    except Exception:
        # Fail-soft: CT scanner unavailable
        _CT_SCANNER_GET_SUBDOMAINS = None

