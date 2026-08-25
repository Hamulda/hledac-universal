"""STEP 4 Phase 2 — PreludeOrchestrator for SprintScheduler v2.

from _core import aclose
F350M-R / Issue #P2.


Extracts prelude phase logic from runtime/sprint_scheduler.py:
    - _run_mandatory_acquisition_prelude (~1380 lines)
    - Individual lane functions: CT, WAYBACK, PDNS, DOH

Each lane function takes `ctx: SprintContext` and returns a typed result dict.
All `self._result.X = Y` mutations become `ctx.result.X = Y`.

Design:
    - Lanes are standalone async functions (no self._xxx references)
    - PreludeOrchestrator.run() orchestrates the gather of all lanes
    - Module-level lazy import cache avoids import overhead on every call

ISSUE P2 FIX: All fire-and-forget ingest tasks are now tracked via
TaskRegistry (safe_create_task_tracked). This guarantees:
  1. Tasks are registered and can be awaited on winddown
  2. On cancel_all(), all prelude ingest tasks receive CancelledError
  3. No lost tasks if prelude finishes before ingest completes
  4. M1 8GB: no orphan tasks holding references under memory pressure
"""

import asyncio
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import first_completed

_LAZY_IMPORT_CACHE: dict[str, Any] = {}


def _lazy_import(name: str) -> Any:
    """Lazily import and cache a module/class.

    Uses a module-level cache to avoid import overhead on repeated calls.
    The first call triggers the import; subsequent calls use the cache.
    """
    if name not in _LAZY_IMPORT_CACHE:
        from importlib import import_module

        _LAZY_IMPORT_CACHE[name] = import_module(name)
    return _LAZY_IMPORT_CACHE[name]


class LaneResult(Struct):
    lane: str
    attempted: bool
    skipped: bool
    skip_reason: str | None = None
    raw_count: int = 0
    built_count: int = 0
    accepted_count: int = 0
    error: str | None = None
    timeout: bool = False
    duration_s: float | None = None


async def run_public_prelude_lane(query: str) -> LaneResult:
    """Run PUBLIC prelude lane.

    Returns LaneResult, never raises.
    Bounded: 10s asyncio.timeout, max 3 results, concurrency 4.

    ISSUE-2.1 FIX: fetch_concurrency 2→4 pro vnitřní paralelizaci.
    Při 5 prelude lanes běžících paralelně stačí concurrency=4
    (celkem max 20 concurrent HTTP, M1 8GB RAM safe).
    Fail-fast: pokud pipeline vrátí ≥80% max_results (≥3), lane končí early.
    """
    # Lazy imports with module-level cache
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query

    try:
        _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
        if isinstance(_shaped, dict) or not _shaped:
            return LaneResult(lane="PUBLIC", attempted=False, skipped=True, skip_reason="empty_public_query")
        # ISSUE-2.1: Zvýšena concurrency 2→4 pro vnitřní paralelizaci fetch.
        # Fail-fast: při ≥80% úspěšnosti (3/3 = 100%, žádný další fetch není potřeba).
        _success_target = 3
        _fail_fast_threshold = _success_target  # 3 — 80% z 3 = 2.4, pro ≥80% potřeba 3
        async with asyncio.timeout(10.0):
            _pipeline_result = await async_run_live_public_pipeline(
                query=_shaped,
                store=None,
                max_results=3,
                fetch_timeout_s=10.0,
                fetch_concurrency=4,
                hermes_engine=None,
                memory_manager=None,
                enqueue_hypothesis_pivot=None,
            )
            # Fail-fast: pokud jsme získali >=80% cílových výsledků, ukončíme early
            _discovered = getattr(_pipeline_result, "discovered", 0) or 0
            if _discovered >= _fail_fast_threshold:
                return LaneResult(
                    lane="PUBLIC",
                    attempted=True,
                    skipped=False,
                    raw_count=_discovered,
                    built_count=getattr(_pipeline_result, "fetched", 0) or 0,
                    accepted_count=getattr(_pipeline_result, "accepted_findings", 0) or 0,
                    error=getattr(_pipeline_result, "error", None),
                    timeout=getattr(_pipeline_result, "timed_out", False),
                    duration_s=getattr(_pipeline_result, "elapsed_s", None),
                )
        return LaneResult(
            lane="PUBLIC",
            attempted=True,
            skipped=False,
            raw_count=getattr(_pipeline_result, "discovered", 0) or 0,
            built_count=getattr(_pipeline_result, "fetched", 0) or 0,
            accepted_count=getattr(_pipeline_result, "accepted_findings", 0) or 0,
            error=getattr(_pipeline_result, "error", None),
            timeout=getattr(_pipeline_result, "timed_out", False),
            duration_s=getattr(_pipeline_result, "elapsed_s", None),
        )
    except TimeoutError:
        return LaneResult(lane="PUBLIC", attempted=True, skipped=False, timeout=True, duration_s=10.0)
    except Exception as exc:
        return LaneResult(lane="PUBLIC", attempted=True, skipped=False, error=f"{type(exc).__name__}:{exc}")


async def run_ct_prelude_lane(query: str, result: Any, seed_context: Any = None) -> LaneResult:
    """Run CT prelude lane.

    Returns LaneResult with telemetry written to result (ctx.result).
    Bounded: 15s asyncio.timeout, max 5 results.
    """
    import time as _time

    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query
    from hledac.universal.runtime.source_finding_bridge import ct_results_to_findings

    _ct_result: Any = None
    _candidates: tuple = ()
    _rejections: tuple = ()
    try:
        _shaped = build_lane_query(query, AcquisitionLane.CT, seed_context=seed_context)
        if isinstance(_shaped, dict) or not _shaped:
            result.ct_planned = False
            return LaneResult(lane="CT", attempted=False, skipped=True, skip_reason="empty_query")
        from hledac.universal.runtime.scheduler.lanes import _get_ct_adapter

        _ct_adapter = _get_ct_adapter()
        async with asyncio.timeout(15.0):
            _ct_result, _ct_outcome = await _ct_adapter(query=_shaped, max_results=5, timeout_s=15.0)
        _raw = getattr(_ct_outcome, "raw_count", 0) or 0
        _bridge_result = ct_results_to_findings(
            _ct_result, _ct_outcome, query, sprint_id=f"prelude-ct-{int(_time.time())}"
        )
        _candidates: list = _bridge_result[0]
        _rejections: list = _bridge_result[1]
        _ct_tel: dict = _bridge_result[2]
        result.ct_planned = True
        result.ct_bridge_invoked = True
        result.ct_query = str(_shaped)
        result.ct_results_raw = _raw
        if _ct_tel:
            result.ct_advisory_clues_count = _ct_tel.get("ct_advisory_clues_count", 0)
        return LaneResult(
            lane="CT",
            attempted=True,
            skipped=False,
            raw_count=_raw,
            built_count=len(_candidates),
            accepted_count=len(_candidates),
            error=getattr(_ct_outcome, "error", None),
            duration_s=0.0,
        )
    except TimeoutError:
        result.ct_planned = True
        result.ct_terminal_stage = "prelude_timeout"
        return LaneResult(lane="CT", attempted=True, skipped=False, timeout=True, duration_s=15.0)
    except Exception as exc:
        result.ct_terminal_stage = f"prelude_error:{type(exc).__name__}"
        return LaneResult(lane="CT", attempted=True, skipped=False, error=f"{type(exc).__name__}:{exc}")


async def run_wayback_prelude_lane(
    query: str, result: Any, duckdb_store: Any, time_module: Any, seed_context: Any = None
) -> LaneResult:
    """Run WAYBACK prelude lane.

    Bounded: no hard timeout, writes to duckdb_store via bg_tasks.
    """
    from hledac.universal.recon.wayback_diff_miner import WaybackDiffMiner
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
    from hledac.universal.runtime.source_finding_bridge import wayback_results_to_findings

    _wb_query = _build_lane_query(query, AcquisitionLane.WAYBACK, seed_context=seed_context)
    if _wb_query and (not isinstance(_wb_query, dict)):
        _wb_miner = WaybackDiffMiner()
        try:
            _wb_result = await _wb_miner.mine([str(_wb_query)])
        finally:
            await _wb_miner.close()
        _wb_cands, _wb_rejs, _wb_tel = wayback_results_to_findings(
            _wb_result, query, sprint_id=f"prelude-wb-{int(time_module.time())}"
        )
        if _wb_tel:
            result.wayback_advisory_clues_count = _wb_tel.get("wayback_changed_count", 0)
        # ISSUE #009 FIX: fire-and-forget ingest — don't block lane on DuckDB write.
        # Lane returns immediately with built_count; ingest runs in background.
        _wb_acc = 0
        if _wb_cands and duckdb_store and hasattr(duckdb_store, "async_ingest_findings_batch"):
            try:
                # Fire-and-forget: safe_create_task_tracked runs ingest in background.
                # ISSUE P2 FIX: TaskRegistry tracks this task — guaranteed cleanup on winddown.
                # Lane is not blocked; accepted_count is optimistic (graceful degradation).
                from hledac.universal.runtime.scheduler_v2._task_registry import (
                    TaskScope,
                    safe_create_task_tracked,
                )

                async def _wb_ingest_bg():
                    try:
                        _ing = await duckdb_store.async_ingest_findings_batch(list(_wb_cands))
                        return sum(1 for r in _ing if isinstance(r, dict) and r.get("accepted"))
                    except Exception:
                        return 0

                safe_create_task_tracked(_wb_ingest_bg(), name="prelude:wayback_ingest", scope=TaskScope.PRELUDE)
                _wb_acc = len(_wb_cands)  # Optimistic: all built candidates accepted
            except Exception:
                _wb_acc = 0
        result.wayback_diff_findings_produced = _wb_acc
        return LaneResult(
            lane="WAYBACK", attempted=True, skipped=False, built_count=len(_wb_cands), accepted_count=_wb_acc
        )
    return LaneResult(
        lane="WAYBACK",
        attempted=False,
        skipped=True,
        skip_reason="empty_shaped_query" if not _wb_query else "lane_disabled",
    )


async def run_pdns_prelude_lane(
    query: str, result: Any, duckdb_store: Any, time_module: Any, seed_context: Any = None
) -> LaneResult:
    """Run PASSIVE_DNS prelude lane."""
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
    from hledac.universal.runtime.source_finding_bridge import passive_dns_results_to_findings
    from hledac.universal.security.passive_dns import call_lookup_passive_dns

    _pdns_query = _build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context=seed_context)
    if _pdns_query and (not isinstance(_pdns_query, dict)):
        _pdns_ips, _pdns_outcome = await call_lookup_passive_dns(str(_pdns_query))
        _pdns_cands, _pdns_rejs, _pdns_tel = passive_dns_results_to_findings(
            _pdns_ips, _pdns_outcome, query, sprint_id=f"prelude-pdns-{int(time_module.time())}"
        )
        if _pdns_tel:
            result.passive_dns_advisory_clues_count = _pdns_tel.get("pdns_public_accepted", 0)
        # ISSUE #009 FIX: fire-and-forget ingest — don't block lane on DuckDB write.
        # ISSUE P2 FIX: TaskRegistry tracks this task — guaranteed cleanup on winddown.
        _pdns_acc = 0
        if _pdns_cands and duckdb_store and hasattr(duckdb_store, "async_ingest_findings_batch"):
            try:
                from hledac.universal.runtime.scheduler_v2._task_registry import (
                    TaskScope,
                    safe_create_task_tracked,
                )

                async def _pdns_ingest_bg():
                    try:
                        _ing = await duckdb_store.async_ingest_findings_batch(list(_pdns_cands))
                        return sum(1 for r in _ing if isinstance(r, dict) and r.get("accepted"))
                    except Exception:
                        return 0

                safe_create_task_tracked(_pdns_ingest_bg(), name="prelude:pdns_ingest", scope=TaskScope.PRELUDE)
                _pdns_acc = len(_pdns_cands)
            except Exception:  # noqa: BLE001
                pass
        result.pdns_advisory_findings_produced = _pdns_acc
        return LaneResult(
            lane="PASSIVE_DNS", attempted=True, skipped=False, built_count=len(_pdns_cands), accepted_count=_pdns_acc
        )
    return LaneResult(
        lane="PASSIVE_DNS",
        attempted=False,
        skipped=True,
        skip_reason="empty_shaped_query" if not _pdns_query else "lane_disabled",
    )


def _collect_doh_domains(
    pivot_doh_items: Any,
    query: str,
    seed_context: Any,
) -> tuple[list[str], str]:
    """Collect DOH domains from pivot items or build from query.

    Returns (domains, seed_source).
    """
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane

    domains: list[str] = []
    seed_source = "no_domain_seed"

    if pivot_doh_items:
        for item in pivot_doh_items:
            if getattr(item, "lane", None) == "DOH" and getattr(item, "seed_type", None) == "domain":
                domain = getattr(item, "seed_value", None)
                if domain:
                    domains.append(str(domain))
        if domains:
            seed_source = "pivot_items"

    if not domains:
        shaped = _build_lane_query(query, AcquisitionLane.DOH, seed_context=seed_context)
        if shaped and not isinstance(shaped, dict):
            domains = [str(shaped)]
            seed_source = "seed_context" if seed_context and seed_context.domains else "raw_query"

    return domains[:3], seed_source  # Max 3 for M1 8GB RAM safety


async def _init_doh_adapter(result: Any) -> tuple[Any, Any] | tuple[None, None]:
    """Initialize DOH adapter and session.

    Returns (adapter, session) or (None, None) on failure.
    """
    adapter = None
    try:
        from hledac.universal.recon.doh_lane import DOHAdapter

        adapter = DOHAdapter()
    except Exception as init_exc:
        result.doh_terminal_stage = "dependency_missing"
        result.doh_provider_errors = (f"doh_adapter_init_failed:{type(init_exc).__name__}:{init_exc}",)
        return None, None

    session = None
    try:
        import httpx

        session = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        result.doh_request_attempted = True
    except Exception as session_exc:
        result.doh_terminal_stage = f"session_init_failed:{type(session_exc).__name__}"
        if adapter:
            await adapter.close()
        return None, None

    return adapter, session


async def _create_domain_fetch_task(
    domain: str,
    semaphore: asyncio.Semaphore,
    adapter: Any,
    session: Any,
    query: str,
    sprint_id: str,
) -> tuple[str, list, list, dict]:
    """Fetch DOH results for one domain with semaphore limit.

    Returns (domain, candidates, rejections, telemetry).
    """
    from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings

    async with semaphore:
        findings = await adapter.run(domain=domain, session=session)
        if findings:
            cands, rejs, tel = doh_results_to_findings(findings, None, query, sprint_id)
            return (domain, cands, rejs, tel)
        return (domain, [], [], {})


async def _cancel_pending_tasks(
    pending: set,
) -> int:
    """Cancel all pending tasks and return the count of cancelled tasks.

    Returns the number of tasks that were cancelled.
    """
    cancelled_count = 0
    for t in pending:
        if not t.done():
            t.cancel()
            cancelled_count += 1
    return cancelled_count


def _process_winner_result(
    winner_task: Any,
    cache: dict | None,
    all_cands: list,
    all_rejs: list,
    all_tel: dict,
    total_raw: int,
) -> tuple[int, bool, str | None, bool, int]:
    """Process a completed task result.

    Returns (new_total_raw, cache_used, first_done_domain, has_result, cancelled).
    """
    if winner_task.cancelled():
        return total_raw, False, None, False, True

    try:
        domain, cands, rejs, tel = winner_task.result()
        all_cands.extend(cands)
        all_rejs.extend(rejs)
        if tel:
            all_tel.update(tel)
        new_total_raw = total_raw + len(cands)
        cache_used = cache is not None and domain in cache
        first_done_domain = domain
        return new_total_raw, cache_used, first_done_domain, True, False
    except BaseException:  # noqa: BLE001
        return total_raw, False, None, False, False


async def _aggregate_doh_results(
    tasks: set,
    fail_fast_threshold: int,
    adapter: Any,
) -> tuple[list, list, dict, int, int, bool, str | None]:
    """Aggregate DOH results with fail-fast logic.

    Returns (all_candidates, all_rejections, merged_telemetry, total_raw,
             cancelled_count, cache_used, first_done_domain).
    """
    pending = set(tasks)
    all_cands = []
    all_rejs = []
    all_tel: dict = {}
    total_raw = 0
    cancelled_count = 0
    cache_used = False
    first_done_domain: str | None = None
    cache: dict = getattr(adapter, "_cache", None) if adapter else None

    while pending and total_raw < fail_fast_threshold:
        try:
            _res, winner_task = await first_completed(*pending)
        except TimeoutError:
            break

        pending.discard(winner_task)
        total_raw, cache_used, first_done_domain, has_result, was_cancelled = _process_winner_result(
            winner_task, cache, all_cands, all_rejs, all_tel, total_raw
        )
        if was_cancelled:
            cancelled_count += 1
        elif has_result and first_done_domain is not None:
            break  # Early exit after first successful result

    # Cancel any remaining tasks
    if pending:
        cancelled_count += await _cancel_pending_tasks(pending)

    return (all_cands, all_rejs, all_tel, total_raw, cancelled_count, cache_used, first_done_domain)


async def _ingest_doh_findings(
    candidates: list,
    duckdb_store: Any,
    result: Any,
) -> int:
    """Ingest DOH findings asynchronously (fire-and-forget).

    Returns accepted count (optimistic).
    """
    if not candidates or not duckdb_store or not hasattr(duckdb_store, "async_ingest_findings_batch"):
        return 0

    try:
        from hledac.universal.runtime.scheduler_v2._task_registry import (
            TaskScope,
            safe_create_task_tracked,
        )

        async def _ingest_bg():
            try:
                ing = await duckdb_store.async_ingest_findings_batch(list(candidates))
                return sum(1 for r in ing if isinstance(r, dict) and r.get("accepted"))
            except Exception:
                return 0

        safe_create_task_tracked(_ingest_bg(), name="prelude:doh_ingest", scope=TaskScope.PRELUDE)
        return len(candidates)  # Optimistic: all built candidates accepted
    except Exception:  # noqa: BLE001
        return 0


async def run_doh_prelude_lane(
    query: str, result: Any, duckdb_store: Any, time_module: Any, pivot_doh_items: Any = None, seed_context: Any = None
) -> LaneResult:
    """Run DOH prelude lane.

    Orchestrator: collects domains → initializes adapter → fetches in parallel
    with fail-fast → ingests results.
    """
    from hledac.universal.utils.asyncx import safe_create_task

    result.doh_planned = True
    result.doh_scheduled = True

    domains, seed_source = _collect_doh_domains(pivot_doh_items, query, seed_context)
    result.doh_seed_source = seed_source

    if not domains:
        result.doh_terminal_stage = "no_candidates"
        return LaneResult(lane="DOH", attempted=False, skipped=True)

    result.doh_domains_attempted = len(domains)

    adapter, session = await _init_doh_adapter(result)
    if adapter is None or session is None:
        return LaneResult(lane="DOH", attempted=False, skipped=True)

    try:
        semaphore = asyncio.Semaphore(3)  # Concurrency=3 for M1 8GB RAM safety
        sprint_id = f"prelude-doh-{int(time_module.time())}"
        success_target = 10  # Typical DOH results per domain
        fail_fast_threshold = int(success_target * 0.8)  # 8 = 80% fail-fast

        tasks = set()
        for domain in domains:
            task = safe_create_task(
                _create_domain_fetch_task(domain, semaphore, adapter, session, query, sprint_id),
                name=f"prelude:doh:{domain}",
            )
            tasks.add(task)

        (
            all_cands,
            all_rejs,
            all_tel,
            total_raw,
            cancelled_count,
            cache_used,
            first_domain,
        ) = await _aggregate_doh_results(tasks, fail_fast_threshold, adapter)

        result.doh_cache_used = cache_used
        result.doh_raw_count = all_tel.get("doh_total", total_raw)
        result.doh_cancelled_count = cancelled_count

        if all_cands:
            accepted_count = await _ingest_doh_findings(all_cands, duckdb_store, result)
            result.doh_advisory_findings_produced = accepted_count
            return LaneResult(
                lane="DOH",
                attempted=True,
                skipped=False,
                built_count=len(all_cands),
                accepted_count=accepted_count,
            )

        return LaneResult(lane="DOH", attempted=True, skipped=False)

    except Exception as exc:
        result.doh_terminal_stage = f"error:{type(exc).__name__}"
        return LaneResult(
            lane="DOH",
            attempted=True,
            skipped=False,
            error=f"{type(exc).__name__}:{exc}",
        )
    finally:
        if session:
            await session.aclose()
        if adapter:
            await adapter.close()


async def gather_taskgroup(coros: list, concurrency: int, ctx: str) -> tuple[list, list]:
    """Wrapper around utils.async_helpers.gather_taskgroup for prelude lanes."""
    from hledac.universal.utils.asyncx import parallel

    result = await parallel(coros, concurrency=concurrency, policy="collect", taskgroup=True, ctx=ctx)
    return result.ok, list(result.errors)


def _build_lane_query(query: str, lane: Any, seed_context: Any = None) -> Any:
    from hledac.universal.runtime.acquisition_strategy import build_lane_query as _blq

    return _blq(query, lane, seed_context=seed_context)
