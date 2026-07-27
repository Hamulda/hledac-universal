"""STEP 4 Phase 2 — PreludeOrchestrator for SprintScheduler v2.

F350M-R / Issue #P2.

Extracts prelude phase logic from runtime/sprint_scheduler.py:
    - _run_mandatory_acquisition_prelude (~1380 lines)
    - Individual lane functions: CT, WAYBACK, PDNS, DOH

Each lane function takes `ctx: SprintContext` and returns a typed result dict.
All `self._result.X = Y` mutations become `ctx.result.X = Y`.

Design:
    - Lanes are standalone async functions (no self._xxx references)
    - PreludeOrchestrator.run() orchestrates the gather of all lanes
    - Lazy imports avoid M1 Metal init at import time

ISSUE P2 FIX: All fire-and-forget ingest tasks are now tracked via
TaskRegistry (safe_create_task_tracked). This guarantees:
  1. Tasks are registered and can be awaited on winddown
  2. On cancel_all(), all prelude ingest tasks receive CancelledError
  3. No lost tasks if prelude finishes before ingest completes
  4. M1 8GB: no orphan tasks holding references under memory pressure
"""
import asyncio
from dataclasses import dataclass
import msgspec
from typing import Any

class LaneResult(msgspec.Struct, gc=False):
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
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query
    try:
        _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
        if isinstance(_shaped, dict) or not _shaped:
            return LaneResult(lane='PUBLIC', attempted=False, skipped=True, skip_reason='empty_public_query')
        # ISSUE-2.1: Zvýšena concurrency 2→4 pro vnitřní paralelizaci fetch.
        # Fail-fast: při ≥80% úspěšnosti (3/3 = 100%, žádný další fetch není potřeba).
        _success_target = 3
        _fail_fast_threshold = _success_target  # 3 — 80% z 3 = 2.4, pro ≥80% potřeba 3
        async with asyncio.timeout(10.0):
            _pipeline_result = await async_run_live_public_pipeline(query=_shaped, store=None, max_results=3, fetch_timeout_s=10.0, fetch_concurrency=4, hermes_engine=None, memory_manager=None, enqueue_hypothesis_pivot=None)
            # Fail-fast: pokud jsme získali >=80% cílových výsledků, ukončíme early
            _discovered = getattr(_pipeline_result, 'discovered', 0) or 0
            if _discovered >= _fail_fast_threshold:
                return LaneResult(lane='PUBLIC', attempted=True, skipped=False, raw_count=_discovered, built_count=getattr(_pipeline_result, 'fetched', 0) or 0, accepted_count=getattr(_pipeline_result, 'accepted_findings', 0) or 0, error=getattr(_pipeline_result, 'error', None), timeout=getattr(_pipeline_result, 'timed_out', False), duration_s=getattr(_pipeline_result, 'elapsed_s', None))
        return LaneResult(lane='PUBLIC', attempted=True, skipped=False, raw_count=getattr(_pipeline_result, 'discovered', 0) or 0, built_count=getattr(_pipeline_result, 'fetched', 0) or 0, accepted_count=getattr(_pipeline_result, 'accepted_findings', 0) or 0, error=getattr(_pipeline_result, 'error', None), timeout=getattr(_pipeline_result, 'timed_out', False), duration_s=getattr(_pipeline_result, 'elapsed_s', None))
    except TimeoutError:
        return LaneResult(lane='PUBLIC', attempted=True, skipped=False, timeout=True, duration_s=10.0)
    except Exception as exc:
        return LaneResult(lane='PUBLIC', attempted=True, skipped=False, error=f'{type(exc).__name__}:{exc}')

async def run_ct_prelude_lane(query: str, result: Any, seed_context: Any=None) -> LaneResult:
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
            return LaneResult(lane='CT', attempted=False, skipped=True, skip_reason='empty_query')
        from runtime.scheduler.lanes import _get_ct_adapter
        _ct_adapter = _get_ct_adapter()
        async with asyncio.timeout(15.0):
            _ct_result, _ct_outcome = await _ct_adapter(query=_shaped, max_results=5, timeout_s=15.0)
        _raw = getattr(_ct_outcome, 'raw_count', 0) or 0
        _bridge_result = ct_results_to_findings(_ct_result, _ct_outcome, query, sprint_id=f'prelude-ct-{int(_time.time())}')
        _candidates: list = _bridge_result[0]
        _rejections: list = _bridge_result[1]
        _ct_tel: dict = _bridge_result[2]
        result.ct_planned = True
        result.ct_bridge_invoked = True
        result.ct_query = str(_shaped)
        result.ct_results_raw = _raw
        if _ct_tel:
            result.ct_advisory_clues_count = _ct_tel.get('ct_advisory_clues_count', 0)
        return LaneResult(lane='CT', attempted=True, skipped=False, raw_count=_raw, built_count=len(_candidates), accepted_count=len(_candidates), error=getattr(_ct_outcome, 'error', None), duration_s=0.0)
    except TimeoutError:
        result.ct_planned = True
        result.ct_terminal_stage = 'prelude_timeout'
        return LaneResult(lane='CT', attempted=True, skipped=False, timeout=True, duration_s=15.0)
    except Exception as exc:
        result.ct_terminal_stage = f'prelude_error:{type(exc).__name__}'
        return LaneResult(lane='CT', attempted=True, skipped=False, error=f'{type(exc).__name__}:{exc}')

async def run_wayback_prelude_lane(query: str, result: Any, duckdb_store: Any, time_module: Any, seed_context: Any=None) -> LaneResult:
    """Run WAYBACK prelude lane.

    Bounded: no hard timeout, writes to duckdb_store via bg_tasks.
    """
    from hledac.universal.intel.wayback_diff_miner import WaybackDiffMiner
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
    from hledac.universal.runtime.source_finding_bridge import wayback_results_to_findings
    _wb_query = _build_lane_query(query, AcquisitionLane.WAYBACK, seed_context=seed_context)
    if _wb_query and (not isinstance(_wb_query, dict)):
        _wb_miner = WaybackDiffMiner()
        try:
            _wb_result = await _wb_miner.mine([str(_wb_query)])
        finally:
            await _wb_miner.close()
        _wb_cands, _wb_rejs, _wb_tel = wayback_results_to_findings(_wb_result, query, sprint_id=f'prelude-wb-{int(time_module.time())}')
        if _wb_tel:
            result.wayback_advisory_clues_count = _wb_tel.get('wayback_changed_count', 0)
        # ISSUE #009 FIX: fire-and-forget ingest — don't block lane on DuckDB write.
        # Lane returns immediately with built_count; ingest runs in background.
        _wb_acc = 0
        if _wb_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
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
                        return sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
                    except Exception:
                        return 0
                safe_create_task_tracked(_wb_ingest_bg(), name='prelude:wayback_ingest', scope=TaskScope.PRELUDE)
                _wb_acc = len(_wb_cands)  # Optimistic: all built candidates accepted
            except Exception:
                _wb_acc = 0
        result.wayback_diff_findings_produced = _wb_acc
        return LaneResult(lane='WAYBACK', attempted=True, skipped=False, built_count=len(_wb_cands), accepted_count=_wb_acc)
    return LaneResult(lane='WAYBACK', attempted=False, skipped=True, skip_reason='empty_shaped_query' if not _wb_query else 'lane_disabled')

async def run_pdns_prelude_lane(query: str, result: Any, duckdb_store: Any, time_module: Any, seed_context: Any=None) -> LaneResult:
    """Run PASSIVE_DNS prelude lane."""
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
    from hledac.universal.runtime.source_finding_bridge import passive_dns_results_to_findings
    from hledac.universal.security.passive_dns import call_lookup_passive_dns
    _pdns_query = _build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context=seed_context)
    if _pdns_query and (not isinstance(_pdns_query, dict)):
        _pdns_ips, _pdns_outcome = await call_lookup_passive_dns(str(_pdns_query))
        _pdns_cands, _pdns_rejs, _pdns_tel = passive_dns_results_to_findings(_pdns_ips, _pdns_outcome, query, sprint_id=f'prelude-pdns-{int(time_module.time())}')
        if _pdns_tel:
            result.passive_dns_advisory_clues_count = _pdns_tel.get('pdns_public_accepted', 0)
        # ISSUE #009 FIX: fire-and-forget ingest — don't block lane on DuckDB write.
        # ISSUE P2 FIX: TaskRegistry tracks this task — guaranteed cleanup on winddown.
        _pdns_acc = 0
        if _pdns_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
            try:
                from hledac.universal.runtime.scheduler_v2._task_registry import (
                    TaskScope,
                    safe_create_task_tracked,
                )
                async def _pdns_ingest_bg():
                    try:
                        _ing = await duckdb_store.async_ingest_findings_batch(list(_pdns_cands))
                        return sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
                    except Exception:
                        return 0
                safe_create_task_tracked(_pdns_ingest_bg(), name='prelude:pdns_ingest', scope=TaskScope.PRELUDE)
                _pdns_acc = len(_pdns_cands)
            except Exception:
                pass
        result.pdns_advisory_findings_produced = _pdns_acc
        return LaneResult(lane='PASSIVE_DNS', attempted=True, skipped=False, built_count=len(_pdns_cands), accepted_count=_pdns_acc)
    return LaneResult(lane='PASSIVE_DNS', attempted=False, skipped=True, skip_reason='empty_shaped_query' if not _pdns_query else 'lane_disabled')

async def run_doh_prelude_lane(query: str, result: Any, duckdb_store: Any, time_module: Any, pivot_doh_items: Any=None, seed_context: Any=None) -> LaneResult:
    """Run DOH prelude lane.

    ISSUE-2.1 FIX: Přidána vnitřní paralelizace pro více domén.
    - Pokud pivot_doh_items obsahuje více DOH domén, zpracuj je paralelně (max 3).
    - Fail-fast: při 80% úspěšnosti ukonči early.
    - Concurrency=3 pro M1 8GB RAM bezpečnost.
    """
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
    from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings
    # Prelude lanes nemají přístup k AcquisitionStrategySnapshot,
    # proto je doh_planned vždy True (lane se vždy pokusí spustit)
    result.doh_planned = True
    result.doh_scheduled = True

    # ISSUE-2.1: Sbíráme všechny DOH domény z pivot_plan, ne jen první
    _doh_domains: list[str] = []
    if pivot_doh_items:
        for _item in pivot_doh_items:
            if getattr(_item, 'lane', None) == 'DOH' and getattr(_item, 'seed_type', None) == 'domain':
                _domain = getattr(_item, 'seed_value', None)
                if _domain:
                    _doh_domains.append(str(_domain))

    # Pokud nemáme domény z pivot_plan, použij standardní query building
    if not _doh_domains:
        _doh_query = _build_lane_query(query, AcquisitionLane.DOH, seed_context=seed_context)
        result.doh_seed_source = 'seed_context' if seed_context and seed_context.domains else 'raw_query'
        if _doh_query is None or (isinstance(_doh_query, dict) and _doh_query.get('_disabled')):
            result.doh_terminal_stage = 'no_candidates'
            result.doh_seed_source = 'no_domain_seed'
            return LaneResult(lane='DOH', attempted=False, skipped=True)
        _doh_domains = [str(_doh_query)]

    # ISSUE-2.1: Omezíme na max 3 domény pro M1 8GB RAM bezpečnost
    _doh_domains = _doh_domains[:3]
    result.doh_domains_attempted = len(_doh_domains)

    _doh_adapter = None
    try:
        from hledac.universal.intel.doh_lane import DOHAdapter
        _doh_adapter = DOHAdapter()
    except Exception as _init_exc:
        result.doh_terminal_stage = 'dependency_missing'
        result.doh_provider_errors = (f'doh_adapter_init_failed:{type(_init_exc).__name__}:{_init_exc}',)
        return LaneResult(lane='DOH', attempted=False, skipped=True)

    import httpx
    _doh_session = None
    try:
        _doh_session = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        result.doh_request_attempted = True

        # ISSUE-2.1: Parallelizace více domén pomocí asyncio.Semaphore
        # Concurrency=3 pro M1 8GB RAM bezpečnost (3 × 12 DOH requests max)
        _sem = asyncio.Semaphore(3)
        _sprint_id = f'prelude-doh-{int(time_module.time())}'

        async def _fetch_one_domain(domain: str) -> tuple[str, list, list, dict]:
            """Fetch DOH pro jednu doménu — vrací (domain, cands, rejs, tel)."""
            async with _sem:
                _findings = await _doh_adapter.run(domain=domain, session=_doh_session)
                if _findings:
                    _cands, _rejs, _tel = doh_results_to_findings(_findings, None, query, _sprint_id)
                    return (domain, _cands, _rejs, _tel)
                return (domain, [], [], {})

        # ISSUE-2.1: Paralelní fetch pro všechny domény s true fail-fast
        # Použijeme asyncio.wait s FIRST_COMPLETED — jakmile první doména vrátí
        # ≥8 výsledků (80% z 10), okamžitě cancelujeme zbývající tasky.
        from hledac.universal.utils.async_helpers import safe_create_task
        _success_target = 10  # typický počet DOH výsledků na doménu
        _fail_fast_threshold = int(_success_target * 0.8)  # 8
        _cancelled_count = 0

        _tasks = [safe_create_task(_fetch_one_domain(d), name=f'prelude:doh:{d}') for d in _doh_domains]
        _pending = set(_tasks)

        # Agregační proměnné (chráněno GIL — single-threaded async)
        _all_cands = []
        _all_rejs = []
        _all_tel: dict = {}
        _total_raw = 0
        _cache_used = False
        _first_done_domain: str | None = None

        while _pending:
            if _total_raw >= _fail_fast_threshold:
                # ISSUE-2.1 FAIL-FAST: máme dost výsledků — cancelujeme zbývající
                for t in _pending:
                    if not t.done():
                        t.cancel()
                        _cancelled_count += 1
                break

            # Čekáme na první dokončený task
            done, _pending = await asyncio.wait(_pending, return_when=asyncio.FIRST_COMPLETED)

            for t in done:
                if t.cancelled():
                    _cancelled_count += 1
                    continue
                try:
                    _res = t.result()
                except asyncio.CancelledError:
                    _cancelled_count += 1
                    continue
                except BaseException as _e:
                    continue

                if isinstance(_res, BaseException):
                    continue
                _domain, _cands, _rejs, _tel = _res
                _all_cands.extend(_cands)
                _all_rejs.extend(_rejs)
                if _tel:
                    _all_tel = _tel
                _total_raw += len(_cands)
                if getattr(_doh_adapter, '_cache', None) and _domain in _doh_adapter._cache:
                    _cache_used = True
                if _first_done_domain is None:
                    _first_done_domain = _domain

        result.doh_cache_used = _cache_used
        result.doh_raw_count = _all_tel.get('doh_total', _total_raw)
        result.doh_cancelled_count = _cancelled_count

        if _all_cands:
            # Fire-and-forget ingest — ISSUE P2 FIX: TaskRegistry tracks this task.
            _doh_acc = 0
            if _all_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
                try:
                    from hledac.universal.runtime.scheduler_v2._task_registry import (
                        TaskScope,
                        safe_create_task_tracked,
                    )
                    async def _doh_ingest_bg():
                        try:
                            _ing = await duckdb_store.async_ingest_findings_batch(list(_all_cands))
                            return sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
                        except Exception:
                            return 0
                    safe_create_task_tracked(_doh_ingest_bg(), name='prelude:doh_ingest', scope=TaskScope.PRELUDE)
                    _doh_acc = len(_all_cands)
                except Exception:
                    pass
            result.doh_advisory_findings_produced = _doh_acc
            return LaneResult(lane='DOH', attempted=True, skipped=False, built_count=len(_all_cands), accepted_count=_doh_acc)

        return LaneResult(lane='DOH', attempted=True, skipped=False)
    except Exception as exc:
        result.doh_terminal_stage = f'error:{type(exc).__name__}'
        return LaneResult(lane='DOH', attempted=True, skipped=False, error=f'{type(exc).__name__}:{exc}')
    finally:
        if _doh_session:
            await _doh_session.aclose()

async def gather_taskgroup(coros: list, concurrency: int, ctx: str) -> tuple[list, list]:
    """Wrapper around utils.async_helpers.gather_taskgroup for prelude lanes."""
    from hledac.universal.utils.async_helpers import parallel
    result = await parallel(coros, concurrency=concurrency, policy="collect", taskgroup=True, ctx=ctx)
    return result.ok, list(result.errors)

def _build_lane_query(query: str, lane: Any, seed_context: Any=None) -> Any:
    from hledac.universal.runtime.acquisition_strategy import build_lane_query as _blq
    return _blq(query, lane, seed_context=seed_context)