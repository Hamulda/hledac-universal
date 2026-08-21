"""
Sprint F350M-R — Acquisition Strategy RUNNER (Async Network I/O).

ROLE:
  Async lane execution: run_enabled_acquisition_lanes() executes lane adapters
  with network access and graph/DB accumulation.

================================================================================
RUNNER SECTION — HAS NETWORK I/O
================================================================================
  - run_enabled_acquisition_lanes() — async, invokes network adapters
  - Nested async closures: _run_ct_lane, _run_wayback_lane, _run_pdns_lane,
    _run_doh_lane, _run_blockchain_lane, _run_ipfs_lane, etc.
  - DOHAdapter via async_get_httpx_session() — HTTP fetch
  - All lane adapters (crtsh, wayback, passive_dns, shodan, censys, etc.)

RUNNER INVARIANTS (run_enabled_acquisition_lanes variants):
  - gather(return_exceptions=True) so one lane crash never fails others
  - Per-lane asyncio.timeout enforced
  - STEALTH never auto-enabled
  - No MLX/model load
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from hledac.universal.utils.asyncx import parallel_ok

logger = logging.getLogger(__name__)
from hledac.universal.runtime.acquisition_strategy_planner import (
    AcquisitionLane,
    AcquisitionLaneOutcome,
    NonfeedSeedContext,
)

# Module-level constant — canonical lane→family mapping (F360M dedup).
_LANE_TO_FAMILY: dict[str, str] = {
    AcquisitionLane.FEED: "feed",
    AcquisitionLane.PUBLIC: "public",
    AcquisitionLane.CT: "ct",
    AcquisitionLane.WAYBACK: "archive",
    AcquisitionLane.PASSIVE_DNS: "passive_dns",
    AcquisitionLane.BLOCKCHAIN: "blockchain",
    AcquisitionLane.STEALTH: "stealth",
    AcquisitionLane.PIVOT_EXECUTOR: "pivot",
    AcquisitionLane.ACADEMIC: "academic",
    AcquisitionLane.OPEN_SOURCE: "public",
    AcquisitionLane.DOH: "doh",
}


def _build_lane_outcome(
    lane: AcquisitionLane,
    plan,
    start: float,
    *,
    error: str | None = None,
    timeout: bool = False,
    produced_items: int = 0,
    candidate_findings: tuple = (),
    rejection_reasons: tuple = (),
    rejected_count: int = 0,
    sample_rejections: tuple = (),
    source_family: str | None = None,
    **extra_fields,
) -> AcquisitionLaneOutcome:
    """
    Helper to build AcquisitionLaneOutcome with common fields filled.

    Consolidates the repetitive outcome construction pattern across all lane runners.
    """
    return AcquisitionLaneOutcome(
        lane=lane,
        enabled=plan.enabled,
        attempted=True,
        timeout=timeout,
        accepted_findings=0,
        produced_items=produced_items,
        error=error,
        duration_s=time.monotonic() - start,
        source_family=source_family or _LANE_TO_FAMILY.get(str(lane), "unknown"),
        candidate_findings=candidate_findings,
        rejection_reasons=rejection_reasons,
        rejected_count=rejected_count,
        sample_rejections=sample_rejections,
        **extra_fields,
    )


async def _accumulate_to_graph(
    findings: list,
    sprint_id_suffix: str,
    graph_accumulator: Any | None = None,
) -> None:
    """Helper: accumulate findings to graph, fail-soft."""
    if findings and graph_accumulator is not None:
        try:
            graph_accumulator.accumulate_findings(findings, sprint_id=sprint_id_suffix)
        except Exception:  # noqa: BLE001
            pass


_ct_adapter: Any = None


def _get_ct_adapter():
    """Return the CT adapter: real call_crtsh or the patched fake."""
    global _ct_adapter
    if _ct_adapter is not None:
        return _ct_adapter
    from hledac.universal.discovery.crtsh_adapter import call_crtsh

    return call_crtsh


async def run_enabled_acquisition_lanes(
    snapshot,
    query: str,
    store,
    uma_state: str = "ok",
    seed_context: NonfeedSeedContext | None = None,
    graph_accumulator=None,
) -> tuple:
    """
    Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)
    bounded by their per-lane plans from the acquisition strategy snapshot.

    FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler
    via its own pipeline calls.

    STEALTH lane is NOT run here — caller must explicitly enable it.

    Args:
        snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().
        query:      Sprint query string.
        store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).
        uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").
        seed_context: NonfeedSeedContext for domain/IP seeding.
        graph_accumulator: SprintGraphAccumulator instance for graph wiring.
                           If None, graph accumulation is skipped (fail-soft, F265C).

    Returns:
        Tuple of AcquisitionLaneOutcome, one per optional lane.

    GHOST_INVARIANTS:
      - gather(return_exceptions=True) so one lane crash never fails others
      - per-lane timeout enforced via asyncio.timeout
      - per-lane max_items enforced by each lane adapter
      - STEALTH never auto-enabled
      - No MLX/model load
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.runtime.acquisition_strategy_planner import (
        MAX_SAMPLE_REJECTIONS,
        build_lane_query,
        ct_results_to_findings,
        passive_dns_results_to_findings,
        wayback_results_to_findings,
    )

    outcomes: list = []
    tasks: list[asyncio.Task] = []
    hardware_critical = uma_state in ("critical", "emergency")

    async def _run_ct_lane(plan) -> AcquisitionLaneOutcome:
        """Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome."""
        start = time.monotonic()
        _raw = build_lane_query(query, AcquisitionLane.CT, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ""
        ct_error: str | None = None
        ct_results_raw = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                _ct_call = _get_ct_adapter()
                result, ct_outcome = await _ct_call(
                    query=shaped_query, max_results=plan.max_items, timeout_s=plan.timeout_s
                )
                ct_results_raw = ct_outcome.raw_count
                candidates, rejections, _ct_telemetry = ct_results_to_findings(
                    result, ct_outcome, query, sprint_id=f"ct-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(
                            list(candidate_findings), sprint_id=f"ct-{int(time.time())}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                if ct_outcome.error:
                    ct_error = ct_outcome.error
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=ct_results_raw,
                    duration_s=time.monotonic() - start,
                    source_family="ct",
                    ct_query=shaped_query,
                    ct_results_raw=ct_results_raw,
                    error=ct_error,
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                    ct_candidates_built=len(candidate_findings),
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="ct",
                ct_query=shaped_query,
                ct_results_raw=ct_results_raw,
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                ct_candidates_built=len(candidate_findings),
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=plan.enabled,
                attempted=True,
                accepted_findings=0,
                produced_items=ct_results_raw,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="ct",
                ct_query=shaped_query,
                ct_results_raw=ct_results_raw,
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                ct_candidates_built=len(candidate_findings),
            )

    async def _run_wayback_lane(plan) -> AcquisitionLaneOutcome:
        """Run Wayback diff mining lane — runtime safety check before network call."""
        start = time.monotonic()
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        shaped_query_str = query
        try:
            from hledac.universal.intel.wayback_diff_miner import WaybackDiffMiner as _WDM

            if not callable(_WDM):
                raise ImportError("WaybackDiffMiner not callable")
        except Exception as _exc:
            return _build_lane_outcome(
                AcquisitionLane.WAYBACK,
                plan,
                start,
                error=f"adapter_not_runtime_safe: {_exc}",
                produced_items=0,
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                wayback_raw_count=0,
                wayback_query=shaped_query_str,
                source_family="archive",
            )
        try:
            async with asyncio.timeout(plan.timeout_s):
                shaped_query = build_lane_query(query, AcquisitionLane.WAYBACK, seed_context)
                shaped_query_str = shaped_query if isinstance(shaped_query, str) else query
                miner = _WDM()
                try:
                    result = await miner.mine([shaped_query_str])
                finally:
                    await miner.close()
                candidates, rejections, _wb_telemetry = wayback_results_to_findings(
                    result, query, sprint_id=f"wayback-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                await _accumulate_to_graph(list(candidate_findings), f"wayback-{int(time.time())}", graph_accumulator)
                return _build_lane_outcome(
                    AcquisitionLane.WAYBACK,
                    plan,
                    start,
                    produced_items=len(result.change_events),
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                    wayback_raw_count=len(result.change_events),
                    wayback_query=shaped_query_str,
                    source_family="archive",
                )
        except TimeoutError:
            return _build_lane_outcome(
                AcquisitionLane.WAYBACK,
                plan,
                start,
                timeout=True,
                error="timeout",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                wayback_raw_count=0,
                wayback_query=shaped_query_str,
                source_family="archive",
            )
        except Exception as exc:
            return _build_lane_outcome(
                AcquisitionLane.WAYBACK,
                plan,
                start,
                error=f"{type(exc).__name__}:{exc}",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                wayback_raw_count=0,
                wayback_query=shaped_query_str,
                source_family="archive",
            )

    async def _run_pdns_lane(plan) -> AcquisitionLaneOutcome:
        """Run passive DNS lookup lane."""
        start = time.monotonic()
        _raw = build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ""
        pdns_error: str | None = None
        produced = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.security.passive_dns import call_lookup_passive_dns as _pdns_lookup

                ips, pdns_outcome = await _pdns_lookup(shaped_query)
                produced = pdns_outcome.result_count
                if pdns_outcome.skip_reason:
                    pdns_error = pdns_outcome.skip_reason
                elif pdns_outcome.error:
                    pdns_error = pdns_outcome.error
                candidates, rejections, _pdns_telemetry = passive_dns_results_to_findings(
                    ips, pdns_outcome, query, sprint_id=f"pdns-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                await _accumulate_to_graph(list(candidate_findings), f"pdns-{int(time.time())}", graph_accumulator)
                return _build_lane_outcome(
                    AcquisitionLane.PASSIVE_DNS,
                    plan,
                    start,
                    error=pdns_error,
                    produced_items=produced,
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                    passive_dns_raw_count=produced,
                    passive_dns_query=shaped_query,
                    source_family="passive_dns",
                )
        except TimeoutError:
            return _build_lane_outcome(
                AcquisitionLane.PASSIVE_DNS,
                plan,
                start,
                timeout=True,
                error="timeout",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                passive_dns_raw_count=0,
                passive_dns_query=shaped_query,
                source_family="passive_dns",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _build_lane_outcome(
                AcquisitionLane.PASSIVE_DNS,
                plan,
                start,
                error=f"{type(exc).__name__}:{exc}",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                passive_dns_raw_count=0,
                passive_dns_query=shaped_query,
                source_family="passive_dns",
            )

    async def _run_academic_lane(plan) -> AcquisitionLaneOutcome:
        """Run academic search lane — R9: bounded, research-profile-only."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.academic_search import AcademicSearchEngine, SearchResult
                from hledac.universal.runtime.source_finding_bridge import academic_results_to_findings

                engine = AcademicSearchEngine(enable_expansion=False)
                try:
                    result = await engine.search(query, max_results=plan.max_items, sources=["arxiv", "crossref"])
                finally:
                    await engine.cleanup()
                search_results = [r for r in result.deduplicated_results if isinstance(r, SearchResult)]
                candidates, rejections, _telemetry = academic_results_to_findings(
                    search_results, query, sprint_id=f"academic-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:5])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(
                            list(candidate_findings), sprint_id=f"academic-{int(time.time())}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.ACADEMIC,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=len(search_results),
                    duration_s=time.monotonic() - start,
                    source_family="academic",
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.ACADEMIC,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="academic",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.ACADEMIC,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="academic",
            )

    async def _run_ipfs_lane(plan) -> AcquisitionLaneOutcome:
        """R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT."""
        start = time.monotonic()
        from hledac.universal.runtime.acquisition_strategy_planner import _has_explicit_ipfs_cid

        query_cid = query.strip()
        all_cids: list[str] = [query_cid] if _has_explicit_ipfs_cid(query_cid) else []
        MAX_IPFS_CIDS = 5
        cids_to_fetch = all_cids[:MAX_IPFS_CIDS]
        produced = 0
        candidate_findings: tuple = ()
        terminal_state = "success"
        ipfs_cid_count = 0
        try:
            async with asyncio.timeout(plan.timeout_s):
                import hashlib

                from hledac.universal.network.ipfs_client import fetch_ipfs, ipfs_content_to_finding_dict

                findings_list: list = []
                for cid in cids_to_fetch:
                    content: bytes | None = None
                    gateway_used = "none"
                    for gw_name, _gw_url in [
                        ("cloudflare", "https://cloudflare-ipfs.com/ipfs/"),
                        ("ipfs.io", "https://ipfs.io/ipfs/"),
                    ]:
                        try:
                            content = await fetch_ipfs(cid, timeout=25)
                            if content is not None:
                                gateway_used = gw_name
                                break
                        except Exception:
                            continue
                    if content is None:
                        terminal_state = "empty"
                        continue
                    content_text = content.decode("utf-8", errors="replace")
                    content_hash = hashlib.sha256(content_text[:2000].encode()).hexdigest()[:16]
                    f"ipfs_{cid}_{int(start * 1000)}_{content_hash}"
                    finding_dict = ipfs_content_to_finding_dict(
                        cid=cid,
                        content=content,
                        gateway=gateway_used,
                        query=query_cid,
                        ts=start,
                        finding_id_prefix="ipfs",
                    )
                    try:
                        finding = CanonicalFinding(
                            finding_id=finding_dict["finding_id"],
                            query=finding_dict["query"],
                            source_type=finding_dict["source_type"],
                            confidence=finding_dict["confidence"],
                            ts=finding_dict["ts"],
                            provenance=finding_dict["provenance"],
                            payload_text=finding_dict.get("payload_text"),
                        )
                        findings_list.append(finding)
                        produced += 1
                        ipfs_cid_count += 1
                    except Exception:
                        terminal_state = "error"
                        continue
                candidate_findings = tuple(findings_list)
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(
                            list(candidate_findings), sprint_id=f"ipfs-{int(time.time())}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.IPFS,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=produced,
                    duration_s=time.monotonic() - start,
                    source_family="ipfs",
                    candidate_findings=candidate_findings,
                    ipfs_cid_count=ipfs_cid_count,
                    ipfs_terminal_state=terminal_state,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.IPFS,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="ipfs",
                ipfs_terminal_state="timeout",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.IPFS,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="ipfs",
                ipfs_terminal_state="error",
            )

    async def _run_open_source_lane(plan) -> AcquisitionLaneOutcome:
        """Run OpenSourceCollectors lane."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.recon.open_source_collectors import get_open_source_collectors

                collector = get_open_source_collectors()
                results = await collector.gather_all(query)
                all_findings: list = []
                for _source, findings in results.items():
                    all_findings.extend(findings)
                if all_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(all_findings, sprint_id=f"open_source-{int(time.time())}")
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.OPEN_SOURCE,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=len(all_findings),
                    duration_s=time.monotonic() - start,
                    source_family="public",
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.OPEN_SOURCE,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="public",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.OPEN_SOURCE,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="public",
            )

    async def _run_doh_lane(plan) -> AcquisitionLaneOutcome:
        """Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.

        F222B: First-class nonfeed lane. No model load, no browser, no stealth.
        Bounds: max_items=20, timeout_s=30, concurrency=2.
        Fail-soft: provider errors never break other lanes.

        NOTE: This closure PERFORMS NETWORK I/O via async_get_httpx_session()
        (HTTP fetch through DOHAdapter).
        """
        start = time.monotonic()
        from hledac.universal.runtime.acquisition_strategy_planner import build_lane_query

        doh_raw_count = 0
        candidate_findings: tuple = ()
        shaped_query = build_lane_query(query, AcquisitionLane.DOH, seed_context)
        if shaped_query is None or (isinstance(shaped_query, dict) and shaped_query.get("_disabled")):
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=False,
                source_family="doh",
                error=shaped_query.get("_disabled_reason", "no_domain_seed")
                if isinstance(shaped_query, dict)
                else "build_lane_query_returned_none",
                doh_query=shaped_query if isinstance(shaped_query, str) else "",
            )
        domain = shaped_query if isinstance(shaped_query, str) else str(shaped_query)
        if not domain:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=False,
                source_family="doh",
                error="empty_domain",
                doh_query=domain,
            )
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.doh_lane import DOHAdapter
                from hledac.universal.network.session_runtime import async_get_httpx_session
                from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings

                adapter = DOHAdapter()
                session = await async_get_httpx_session()
                findings = await adapter.run(domain=domain, session=session)
                doh_raw_count = len(findings)
                if findings:
                    candidates, _rejections, _tel = doh_results_to_findings(
                        findings, None, query, sprint_id=f"doh-{int(time.time())}"
                    )
                    candidate_findings = tuple(candidates)
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(
                            list(candidate_findings), sprint_id=f"doh-{int(time.time())}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.DOH,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=doh_raw_count,
                    duration_s=time.monotonic() - start,
                    source_family="doh",
                    doh_query=domain,
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="doh",
                produced_items=doh_raw_count,
                doh_query=domain,
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="doh",
                produced_items=doh_raw_count,
                doh_query=domain,
            )

    async def _run_blockchain_lane(plan) -> AcquisitionLaneOutcome:
        """Run blockchain forensics lane."""
        start = time.monotonic()
        from hledac.universal.runtime.acquisition_strategy_planner import (
            _extract_crypto_from_query,
            _wallet_to_findings,
        )

        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.recon.blockchain_analyzer import BlockchainForensics

                wallets = _extract_crypto_from_query(query)
                total_tx = 0
                all_blockchain_findings: list = []
                for address in wallets[: plan.max_items]:
                    try:
                        bf = BlockchainForensics()
                        result = await bf.analyze_wallet(address)
                        await bf.close()
                        if result:
                            findings = _wallet_to_findings(result, query)
                            if findings:
                                all_blockchain_findings.extend(findings)
                                total_tx += getattr(result, "transaction_count", 0) or 0
                    except Exception:
                        continue
                if all_blockchain_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(
                            all_blockchain_findings, sprint_id=f"blockchain-{int(time.time())}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.BLOCKCHAIN,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=total_tx,
                    duration_s=time.monotonic() - start,
                    source_family="blockchain",
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.BLOCKCHAIN,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="blockchain",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.BLOCKCHAIN,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="blockchain",
            )

    async def _stealth_never_run(plan) -> AcquisitionLaneOutcome:
        """STEALTH is never auto-run — always record the skip."""
        return AcquisitionLaneOutcome(
            lane=AcquisitionLane.STEALTH,
            enabled=False,
            attempted=False,
            error="stealth_not_auto_run",
            source_family="stealth",
        )

    async def _run_shodan_lane(plan) -> AcquisitionLaneOutcome:
        """Run Shodan intelligence lane — device/IP fingerprints."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.recon.shodan_lane import ShodanLane

                lane_obj = ShodanLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f"shodan-{int(time.time())}")
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.SHODAN,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=len(findings),
                    duration_s=time.monotonic() - start,
                    source_family="shodan_intel",
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.SHODAN,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="shodan_intel",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.SHODAN,
                enabled=plan.enabled,
                attempted=True,
                duration_s=time.monotonic() - start,
                error=f"{type(exc).__name__}:{exc}",
                source_family="shodan_intel",
            )

    async def _run_censys_lane(plan) -> AcquisitionLaneOutcome:
        """Run Censys intelligence lane — certificate transparency."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.recon.censys_lane import CensysLane

                lane_obj = CensysLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f"censys-{int(time.time())}")
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CENSYS,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=len(findings),
                    duration_s=time.monotonic() - start,
                    source_family="censys_intel",
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CENSYS,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="censys_intel",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CENSYS,
                enabled=plan.enabled,
                attempted=True,
                duration_s=time.monotonic() - start,
                error=f"{type(exc).__name__}:{exc}",
                source_family="censys_intel",
            )

    async def _run_greynoise_lane(plan) -> AcquisitionLaneOutcome:
        """Run GreyNoise intelligence lane — mass scanner classification."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.recon.greynoise_lane import GreyNoiseLane

                lane_obj = GreyNoiseLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f"greynoise-{int(time.time())}")
                    except Exception:  # noqa: BLE001
                        pass
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.GREYNOISE,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=0,
                    produced_items=len(findings),
                    duration_s=time.monotonic() - start,
                    source_family="greynoise_intel",
                )
        except TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.GREYNOISE,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="greynoise_intel",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.GREYNOISE,
                enabled=plan.enabled,
                attempted=True,
                duration_s=time.monotonic() - start,
                error=f"{type(exc).__name__}:{exc}",
                source_family="greynoise_intel",
            )

    if snapshot is None:
        return ()
    lane_runners = {
        AcquisitionLane.CT: _run_ct_lane,
        AcquisitionLane.WAYBACK: _run_wayback_lane,
        AcquisitionLane.PASSIVE_DNS: _run_pdns_lane,
        AcquisitionLane.BLOCKCHAIN: _run_blockchain_lane,
        AcquisitionLane.STEALTH: _stealth_never_run,
        AcquisitionLane.ACADEMIC: _run_academic_lane,
        AcquisitionLane.IPFS: _run_ipfs_lane,
        AcquisitionLane.OPEN_SOURCE: _run_open_source_lane,
        AcquisitionLane.DOH: _run_doh_lane,
        AcquisitionLane.SHODAN: _run_shodan_lane,
        AcquisitionLane.CENSYS: _run_censys_lane,
        AcquisitionLane.GREYNOISE: _run_greynoise_lane,
    }
    for plan in snapshot.plans:
        lane = plan.lane
        if lane not in lane_runners:
            continue
        if not plan.enabled:
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane=lane, enabled=False, attempted=False, source_family=_LANE_TO_FAMILY.get(lane, "unknown")
                )
            )
            continue
        if hardware_critical and lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane=lane,
                    enabled=False,
                    attempted=False,
                    error="hardware_critical",
                    source_family=_LANE_TO_FAMILY.get(lane, "unknown"),
                )
            )
            continue
        tasks.append(safe_create_task(lane_runners[lane](plan), name="acquisition:lane_runner"))
    if not tasks:
        return tuple(outcomes)
    results = await parallel_ok(*tasks, label="acquisition_strategy:runner")
    all_candidates: list = []
    lane_candidates: list[list] = []
    for result in results:
        if isinstance(result, AcquisitionLaneOutcome):
            outcomes.append(result)
            if result.candidate_findings:
                lane_candidates.append(list(result.candidate_findings))
                all_candidates.extend(result.candidate_findings)
            else:
                lane_candidates.append([])
        elif isinstance(result, Exception):
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane="UNKNOWN",
                    enabled=True,
                    attempted=True,
                    error=f"gather_error:{result}",
                    source_family="unknown",
                )
            )
            lane_candidates.append([])
    if all_candidates and store is not None and hasattr(store, "async_ingest_findings_batch"):
        try:
            ingest_results = await store.async_ingest_findings_batch(all_candidates)
            idx = 0
            for outcome_idx, outcome in enumerate(outcomes):
                lane_len = len(lane_candidates[outcome_idx])
                lane_results = ingest_results[idx : idx + lane_len]
                accepted = sum(1 for r in lane_results if isinstance(r, dict) and r.get("accepted"))
                outcomes[outcome_idx] = AcquisitionLaneOutcome(
                    lane=outcome.lane,
                    enabled=outcome.enabled,
                    attempted=outcome.attempted,
                    accepted_findings=accepted,
                    produced_items=outcome.produced_items,
                    timeout=outcome.timeout,
                    error=outcome.error,
                    duration_s=outcome.duration_s,
                    source_family=outcome.source_family,
                    ct_query=outcome.ct_query,
                    ct_results_raw=outcome.ct_results_raw,
                    candidate_findings=outcome.candidate_findings,
                    rejection_reasons=outcome.rejection_reasons,
                    rejected_count=outcome.rejected_count,
                    sample_rejections=outcome.sample_rejections,
                    ct_candidates_built=outcome.ct_candidates_built,
                    wayback_raw_count=outcome.wayback_raw_count,
                    passive_dns_raw_count=outcome.passive_dns_raw_count,
                    doh_query=outcome.doh_query,
                    wayback_query=outcome.wayback_query,
                    passive_dns_query=outcome.passive_dns_query,
                    ipfs_cid_count=outcome.ipfs_cid_count,
                    ipfs_terminal_state=outcome.ipfs_terminal_state,
                )
                idx += lane_len
        except Exception:  # noqa: BLE001
            pass
    return tuple(outcomes)
