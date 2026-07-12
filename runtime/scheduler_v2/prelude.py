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
"""
import asyncio
from dataclasses import dataclass
from typing import Any

@dataclass(True)
class LaneResult:
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
    Bounded: 10s asyncio.timeout, max 3 results, concurrency 2.
    """
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query
    try:
        _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
        if isinstance(_shaped, dict) or not _shaped:
            return LaneResult(lane='PUBLIC', attempted=False, skipped=True, skip_reason='empty_public_query')
        async with asyncio.timeout(10.0):
            _pipeline_result = await async_run_live_public_pipeline(query=_shaped, store=None, max_results=3, fetch_timeout_s=10.0, fetch_concurrency=2, hermes_engine=None, memory_manager=None, enqueue_hypothesis_pivot=None)
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
    from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner
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
        _wb_acc = 0
        if _wb_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
            try:
                _ing = await duckdb_store.async_ingest_findings_batch(list(_wb_cands))
                _wb_acc = sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
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
        _pdns_acc = 0
        if _pdns_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
            try:
                _ing = await duckdb_store.async_ingest_findings_batch(list(_pdns_cands))
                _pdns_acc = sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
            except Exception:
                pass
        result.pdns_advisory_findings_produced = _pdns_acc
        return LaneResult(lane='PASSIVE_DNS', attempted=True, skipped=False, built_count=len(_pdns_cands), accepted_count=_pdns_acc)
    return LaneResult(lane='PASSIVE_DNS', attempted=False, skipped=True, skip_reason='empty_shaped_query' if not _pdns_query else 'lane_disabled')

async def run_doh_prelude_lane(query: str, result: Any, duckdb_store: Any, time_module: Any, pivot_doh_items: Any=None, seed_context: Any=None) -> LaneResult:
    """Run DOH prelude lane."""
    from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, is_lane_enabled
    from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings
    result.doh_planned = is_lane_enabled(None, AcquisitionLane.DOH)
    result.doh_scheduled = True
    _doh_domain: str | None = None
    if pivot_doh_items:
        for _item in pivot_doh_items:
            if getattr(_item, 'lane', None) == 'DOH' and getattr(_item, 'seed_type', None) == 'domain':
                _doh_domain = getattr(_item, 'seed_value', None)
                break
    if _doh_domain:
        _doh_query: Any = _doh_domain
        result.doh_seed_source = 'pivot_plan'
    else:
        _doh_query = _build_lane_query(query, AcquisitionLane.DOH, seed_context=seed_context)
        result.doh_seed_source = 'seed_context' if seed_context and seed_context.domains else 'raw_query'
    if _doh_query is None or (isinstance(_doh_query, dict) and _doh_query.get('_disabled')):
        result.doh_terminal_stage = 'no_candidates'
        result.doh_seed_source = 'no_domain_seed'
        return LaneResult(lane='DOH', attempted=False, skipped=True)
    _doh_adapter = None
    try:
        from hledac.universal.intelligence.doh_lane import DOHAdapter
        _doh_adapter = DOHAdapter()
    except Exception as _init_exc:
        result.doh_terminal_stage = 'dependency_missing'
        result.doh_provider_errors = (f'doh_adapter_init_failed:{type(_init_exc).__name__}:{_init_exc}',)
        return LaneResult(lane='DOH', attempted=False, skipped=True)
    import httpx
    _doh_session = None
    try:
        _doh_session = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        result.doh_domains_attempted = 1
        _doh_findings = await _doh_adapter.run(domain=str(_doh_query), session=_doh_session)
        result.doh_request_attempted = True
        _cache_used = getattr(_doh_adapter, '_cache', None) and str(_doh_query) in _doh_adapter._cache
        result.doh_cache_used = bool(_cache_used)
        if _doh_findings:
            _doh_cands, _doh_rejs, _doh_tel = doh_results_to_findings(_doh_findings, None, query, f'prelude-doh-{int(time_module.time())}')
            result.doh_raw_count = _doh_tel.get('doh_total', len(_doh_findings))
            _doh_acc = 0
            if _doh_cands and duckdb_store and hasattr(duckdb_store, 'async_ingest_findings_batch'):
                try:
                    _ing = await duckdb_store.async_ingest_findings_batch(list(_doh_cands))
                    _doh_acc = sum((1 for r in _ing if isinstance(r, dict) and r.get('accepted')))
                except Exception:
                    pass
            result.doh_advisory_findings_produced = _doh_acc
            return LaneResult(lane='DOH', attempted=True, skipped=False, built_count=len(_doh_cands), accepted_count=_doh_acc)
        return LaneResult(lane='DOH', attempted=True, skipped=False)
    except Exception as exc:
        result.doh_terminal_stage = f'error:{type(exc).__name__}'
        return LaneResult(lane='DOH', attempted=True, skipped=False, error=f'{type(exc).__name__}:{exc}')
    finally:
        if _doh_session:
            await _doh_session.close()

async def gather_taskgroup(coros: list, concurrency: int, ctx: str) -> tuple[list, list]:
    """Wrapper around utils.async_helpers.gather_taskgroup for prelude lanes."""
    from hledac.universal.utils.async_helpers import gather_taskgroup as _gt
    return await _gt(coros, concurrency=concurrency, ctx=ctx)

def _build_lane_query(query: str, lane: Any, seed_context: Any=None) -> Any:
    from hledac.universal.runtime.acquisition_strategy import build_lane_query as _blq
    return _blq(query, lane, seed_context=seed_context)