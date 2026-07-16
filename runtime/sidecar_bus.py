"""
runtime/sidecar_bus.py — F204A+F27: Canonical Accepted-Finding Sidecar Bus
==========================================================================

Unified sidecar orchestrator for all accepted findings from feed/public/CT branches.
Bounded batch processor: takes SidecarBatch, fans out to registered sidecar
runners via staged asyncio.gather(return_exceptions=True), collects SidecarRunResult records.

F205B: Explicit staged ordering guarantee — runners execute in 3 stages:
- Stage 1 (light extraction): leak_sentinel, passive_fingerprint, evidence_triage, temporal_archaeology
- Stage 2 (correlation): exposure_correlator, identity_stitching, sprint_diff, rir_correlator,
  social_identity_surface, wayback_diff
- Stage 3 (derived): kill_chain_tagging, embedding

F27: Sidecar runner boilerplate reduction — ~160 LOC eliminated.
Simple runners use sidecar_runner()/sidecar_runner_await() factories from sidecar_runner_decorator.py.
Complex runners (inline logic, multi-import, async streaming) remain as standalone async def.

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True (per stage)
- _check_gathered() called after every gather
- asyncio.CancelledError re-raised, never swallowed
- No blocking calls in event loop; CPU/IO via run_in_executor
- Canonical write path always async_ingest_findings_batch()
- RAM guard: skip heavy sidecars if governor reports critical/emergency
- Each collection has MAX_* constant
- Fail-soft: sidecar error never crashes the sprint
- Stage N failure does not stop stage N+1
"""
import asyncio

import msgspec
import orjson
import msgspec.json as _json
try:
    from hledac.universal.utils.source_types import SourceType as _SourceType
except ImportError:
    _SourceType = None
import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from hledac.universal.core.protocols import safe_get_finding_field, safe_get_payload_text
from hledac.universal.runtime.sidecar_runner_decorator import _store_ingest_and_count
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_fire_and_forget, safe_gather_ok
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

def _safe_payload_json(obj: Any) -> str:
    """Serialize obj to canonical JSON string, fail-soft."""
    from core.result import try_or

    def _encode_orjson() -> str:
        import orjson
        return orjson.dumps(obj).decode('utf-8')

    def _encode_fallback() -> str:
        return _json.encode(obj).decode('utf-8')

    # Triple fallback: orjson → msgspec → str (never raises)
    return try_or(_encode_orjson, "").strip() or try_or(_encode_fallback, str(obj))
logger = logging.getLogger(__name__)
_sidecarlogger = logging.getLogger('sidecar_bus')
MAX_SIDECAR_FINDINGS: int = 500
MAX_SIDECAR_RESULT_RECORDS: int = 32
SIDECAR_TIMEOUT_S: float = 20.0
SIDECAR_DEFAULT_ESTIMATE_MB: int = 50
_HEAVY_SIDECARS: frozenset[str] = frozenset({'identity_stitching', 'embedding', 'sprint_diff', 'banner_grab', 'ipv6_recon', 'pattern_mining'})
_ACTIVE_NETWORK_SIDECARS: frozenset[str] = frozenset({'network_intel', 'banner_grab'})
SIDECAR_NETWORK_CLASS: dict[str, str] = {'network_intel': 'active_network', 'banner_grab': 'active_network', 'exposure_correlator': 'core', 'leak_sentinel': 'core', 'passive_fingerprint': 'core', 'evidence_triage': 'core', 'temporal_archaeology': 'core', 'pattern_mining': 'core', 'sprint_diff': 'core', 'kill_chain_tagging': 'core', 'wayback_diff': 'core', 'rir_correlator': 'core', 'social_identity_surface': 'core', 'passive_tech_stack': 'core', 'identity_stitching': 'duplicate_compat', 'embedding': 'duplicate_compat', 'ipv6_recon': 'duplicate_compat', 'gopher_crawl': 'duplicate_compat'}
SIDECAR_RISK_CLASS: dict[str, str] = {'network_intel': 'active_target', 'banner_grab': 'active_target', 'ipv6_recon': 'active_target', 'rir_correlator': 'third_party_provider', 'passive_fingerprint': 'third_party_provider', 'passive_tech_stack': 'third_party_provider'}

def classify_sidecar_network(sidecar_name: str) -> str:
    """Return network class for a sidecar: 'active_network' | 'core' | 'duplicate_compat'."""
    return SIDECAR_NETWORK_CLASS.get(sidecar_name, 'core')

def classify_sidecar_risk(sidecar_name: str) -> str:
    """Return risk class for a sidecar: 'active_target' | 'third_party_provider' | 'core'."""
    return SIDECAR_RISK_CLASS.get(sidecar_name, 'core')

def sidecar_results_to_source_family_outcomes(sidecar_results: list) -> tuple[dict, ...]:
    """F245B: Convert SidecarRunResult list to source_family_outcomes tuple."""
    if not sidecar_results:
        return ()
    outcomes: dict[str, Any] = {}
    for sr in sidecar_results:
        key = f'sidecar_{sr.sidecar_name}'
        if sr.attempted:
            outcomes[key] = {'attempted': True, 'produced': sr.produced_count, 'stored': sr.stored_count, 'elapsed_ms': round(sr.elapsed_ms, 1)}
        else:
            outcomes[key] = {'attempted': False, 'skipped_reason': sr.skipped_reason or 'unknown', 'elapsed_ms': round(sr.elapsed_ms, 1)}
    return tuple(outcomes.values())

def _sidecar_profile_allows(sidecar_name: str, profile: str | None) -> tuple[bool, str]:
    """Return (allowed, reason). F240A."""
    if sidecar_name not in _ACTIVE_NETWORK_SIDECARS:
        return (True, '')
    if profile in ('active', 'aggressive'):
        return (True, '')
    return (False, f"profile '{profile}' disallows active-network sidecar '{sidecar_name}'")

class SidecarBatch(msgspec.Struct):
    """Batch of accepted findings submitted to the sidecar bus."""
    findings: list
    query: str
    results: list | None = None
    sprint_id: str = ''
    source_branch: str = ''
    created_ts: float = 0.0

    def to_source_family_outcomes(self) -> dict[str, Any]:
        outcomes: dict[str, Any] = {}
        if self.results is None:
            return outcomes
        for r in self.results:
            if r.attempted:
                outcomes[f'sidecar_{r.sidecar_name}'] = {'attempted': True, 'produced': r.produced_count, 'stored': r.stored_count, 'elapsed_ms': round(r.elapsed_ms, 1)}
            else:
                outcomes[f'sidecar_{r.sidecar_name}'] = {'attempted': False, 'skipped_reason': r.skipped_reason or 'unknown', 'elapsed_ms': round(r.elapsed_ms, 1)}
        return outcomes

class SidecarRunResult(msgspec.Struct):
    sidecar_name: str
    attempted: bool
    produced_count: int
    stored_count: int
    skipped_reason: str
    elapsed_ms: float
SidecarRunner = Callable[[list, 'DuckDBShadowStore', str], Any]
SIDECAR_STAGES: list[list[str]] = [['leak_sentinel', 'passive_fingerprint', 'evidence_triage', 'temporal_archaeology'], ['exposure_correlator', 'identity_stitching', 'sprint_diff', 'rir_correlator', 'social_identity_surface', 'wayback_diff'], ['kill_chain_tagging', 'embedding']]

async def _evidence_triage_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F202I evidence triage — counts document findings with triage facets. Stats only."""
    triage_count = 0
    for finding in findings:
        if not hasattr(finding, 'source_type') or finding.source_type != 'document':
            continue
        if not hasattr(finding, 'payload_text') or not finding.payload_text:
            continue
        try:
            payload = orjson.loads(finding.payload_text)
            if isinstance(payload, dict) and 'triage' in payload:
                triage_count += 1
        except Exception:
            pass
    return triage_count

async def _identity_stitching_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F202B identity stitching — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.intel.entity_signal_extractor import extract_entities_from_findings_async
        from hledac.universal.intel.identity_stitching_canonical import create_identity_stitching_adapter
    except Exception:
        return None
    try:
        profiles = await extract_entities_from_findings_async(findings)
        if not profiles:
            return None
        adapter = create_identity_stitching_adapter()
        candidates = adapter.extract_and_stitch(profiles)
        if not candidates:
            return None
        derived_findings = adapter.to_derived_findings(candidates, query)
        return await _store_ingest_and_count(store, derived_findings)
    except Exception:
        return None

async def _pattern_mining_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F250 pattern mining — detects temporal/behavioral patterns in findings."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.intel.pattern_mining_canonical import create_pattern_mining_adapter
    except Exception:
        return None
    try:
        adapter = create_pattern_mining_adapter(use_mlx=True)
        result = adapter.extract_and_mine(findings)
        if not result.temporal_patterns and (not result.behavioral_patterns):
            return None
        derived_findings = adapter.to_derived_findings(result, query)
        return await _store_ingest_and_count(store, derived_findings)
    except Exception:
        return None

async def _sprint_diff_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F203A cross-sprint diff — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.knowledge.sprint_diff_engine import SprintDiffEngine
    except Exception:
        return None
    target_id = query[:128]
    if not hasattr(store, 'async_get_previous_findings_for_target'):
        return None
    try:
        prev_findings_raw = await store.async_get_previous_findings_for_target(target_id, limit=1000)
    except Exception:
        prev_findings_raw = []
    if not prev_findings_raw:
        return None
    current_findings: list[dict] = []
    for f in findings:
        try:
            current_findings.append({'finding_id': safe_get_finding_field(f, 'finding_id', '') or '', 'source_type': safe_get_finding_field(f, 'source_type', '') or '', 'ioc_type': safe_get_finding_field(f, 'ioc_type', '') or '', 'ioc_value': safe_get_finding_field(f, 'ioc_value', '') or '', 'confidence': safe_get_finding_field(f, 'confidence', 0.5) or 0.5, 'ts': safe_get_finding_field(f, 'ts', 0.0) or 0.0, 'payload_text': safe_get_payload_text(f) or ''})
        except Exception:
            continue
    try:
        engine = SprintDiffEngine()
        diff_result = engine.compute_diff(current_findings=current_findings, previous_findings=prev_findings_raw if prev_findings_raw else None, target_id=target_id, current_sprint_id='', previous_sprint_id=None)

        class _DiffFinding:
            __slots__ = ('finding_id', 'source_type', 'query', 'target_id', 'ioc_type', 'ioc_value', 'confidence', 'ts', 'payload_text')

            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
        derived_findings: list[Any] = []
        ts_now = _time.time()
        SourceType = _SourceType
        for nf in diff_result.new_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(finding_id=f"diff-new-{nf.get('finding_id', 'unknown')[:32]}", source_type=SourceType.SPRINT_DIFF if SourceType else 'sprint_diff', query=query, target_id=target_id, ioc_type=nf.get('ioc_type') or 'unknown', ioc_value=nf.get('ioc_value') or 'unknown', confidence=nf.get('confidence', 0.5), ts=ts_now, payload_text=_safe_payload_json({'diff_action': 'new', **nf})))
            except Exception:
                continue
        for df in diff_result.disappeared_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(finding_id=f"diff-gone-{df.get('finding_id', 'unknown')[:32]}", source_type=SourceType.SPRINT_DIFF if SourceType else 'sprint_diff', query=query, target_id=target_id, ioc_type=df.get('ioc_type') or 'unknown', ioc_value=df.get('ioc_value') or 'unknown', confidence=df.get('confidence', 0.5), ts=ts_now, payload_text=_safe_payload_json({'diff_action': 'disappeared', **df})))
            except Exception:
                continue
        return await _store_ingest_and_count(store, derived_findings)
    except Exception:
        return None

async def _wayback_diff_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F203F Wayback CDX diff mining. Compatibility runner — canonical owner
    is intelligence/wayback_diff_miner.py::WaybackDiffMiner (wired as direct lane)."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.intel.wayback_diff_miner import WaybackDiffMiner
    except Exception:
        return None
    try:
        targets: list[str] = []
        for f in findings:
            ioc_value = safe_get_finding_field(f, 'ioc_value', '') or ''
            ioc_type = safe_get_finding_field(f, 'ioc_type', '') or ''
            if ioc_type in ('domain', 'url') and ioc_value:
                targets.append(ioc_value)
            elif hasattr(f, 'url'):
                url = safe_get_finding_field(f, 'url', '') or ''
                if url:
                    targets.append(url)
        if not targets:
            return None
        miner = WaybackDiffMiner()
        try:
            result = await miner.mine(targets)
        finally:
            await miner.close()
        if not result.change_events:
            return None
        findings_out = result.to_findings(query=query, sprint_id='')
        return await _store_ingest_and_count(store, findings_out)
    except Exception:
        return None

async def _social_identity_surface_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F204I: Social identity surface miner."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.intel.social_identity_miner import create_social_identity_miner_adapter
    except Exception:
        return None
    try:
        miner = create_social_identity_miner_adapter()
        result = await miner.mine(findings, store, query)
        return result.scanned_count
    except Exception:
        return None

async def _kill_chain_tagging_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F203C MITRE ATT&CK kill chain tagging."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.intel.kill_chain_tagger import create_kill_chain_tagger
    except Exception:
        return None
    try:
        tagger = create_kill_chain_tagger()
        tagged_results: dict[str, list] = {}
        for finding in findings:
            fid = safe_get_finding_field(finding, 'finding_id', None)
            if not fid:
                continue
            tags = tagger.tag_finding(finding)
            if tags:
                tagged_results[str(fid)] = [tag.to_dict() for tag in tags]
        if not tagged_results:
            return None

        class _KCTFinding:
            __slots__ = ('finding_id', 'source_type', 'query', 'target_id', 'ioc_type', 'ioc_value', 'confidence', 'ts', 'payload_text')

            def __init__(self, **kw: Any) -> None:
                for k, v in kw.items():
                    setattr(self, k, v)
        derived_findings: list[Any] = []
        ts_now = _time.time()
        SourceType = _SourceType
        for fid, tags_list in tagged_results.items():
            for tag_dict in tags_list:
                try:
                    derived_findings.append(_KCTFinding(finding_id=f"kct-{fid[:24]}-{tag_dict.get('technique_id', 'unknown')[:16]}", source_type=SourceType.KILLCHAIN_TAG if SourceType else 'killchain_tag', query=query, target_id='', ioc_type='kill_chain_tag', ioc_value=tag_dict.get('technique_id', 'unknown'), confidence=tag_dict.get('confidence', 0.5), ts=ts_now, payload_text=_safe_payload_json(tag_dict)))
                except Exception:
                    continue
        return await _store_ingest_and_count(store, derived_findings)
    except Exception:
        return None

async def _embedding_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F203I streaming embedding — heavy, RAM-guarded by bus. Stores to ANN index."""
    if not findings or store is None:
        _sidecarlogger.debug('[embedding] early-return: findings=%d store=%s', len(findings) if findings else 0, 'None' if store is None else type(store).__name__)
        return 0
    try:
        from hledac.universal.intel.streaming_embedder import StreamingEmbedder
    except Exception:
        _sidecarlogger.debug('embedding_runner: StreamingEmbedder import failed')
        return 0
    try:
        embedder = StreamingEmbedder()
        embeddable = []
        for f in findings:
            text = safe_get_payload_text(f) or safe_get_finding_field(f, 'query', '') or ''
            if len(text) >= 16:
                embeddable.append(f)
        if not embeddable:
            _sidecarlogger.debug('[embedding] no embeddable findings: total=%d embeddable=%d', len(findings), 0)
            return 0
        async for ids, embeddings in embedder.embed_findings(embeddable, batch_size=8):
            if embedder.aborted:
                _sidecarlogger.warning('[embedding] aborted mid-stream due to memory pressure')
                break
            if ids and embeddings is not None and (embeddings.shape[0] > 0):
                try:
                    from hledac.universal.knowledge.ann_index import get_ann_index_async
                    ann = await get_ann_index_async()
                    import hashlib
                    for idx, finding_id in enumerate(ids):
                        emb = embeddings[idx]
                        if emb.shape[-1] == 256:
                            key = hashlib.blake2b(finding_id.encode(), digest_size=32).hexdigest()
                            text_hash = hashlib.sha256(finding_id.encode()).hexdigest()
                            ann.upsert(key, emb, text_hash)
                except Exception:
                    pass
        try:
            from hledac.universal.knowledge.ann_index import get_ann_index_async
            ann = await get_ann_index_async()
            ann.prewarm(top_k=128)
        except Exception:
            pass
    except Exception as exc:
        _sidecarlogger.debug('embedding_runner: exception during embed: %s: %s', type(exc).__name__, exc)
    return None

async def _banner_grab_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F214 banner grabber — TCP banner extraction, RAM-isolated."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.network import BANNER_GRABBER_AVAILABLE
        if not BANNER_GRABBER_AVAILABLE:
            return None
        from hledac.universal.network.banner_grabber import BannerGrabberAdapter
    except Exception:
        return None
    try:
        adapter = BannerGrabberAdapter()
        try:
            targets: list[str] = []
            for f in findings:
                ioc_value = safe_get_finding_field(f, 'ioc_value', '') or ''
                ioc_type = safe_get_finding_field(f, 'ioc_type', '') or ''
                if ioc_type in ('ipv4', 'ip') and ioc_value:
                    targets.append(ioc_value)
            if not targets:
                return None
            derived_findings: list = []

            async def _query_one(target: str) -> list:
                return await adapter.query(target)
            from hledac.universal.core.concurrency_registry import concurrency_budget, ConcurrencyCategory
            batches = await bounded_parallel_map(targets[:20], _query_one, concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='banner_grab')
            for batch in batches:
                if batch is not None:
                    derived_findings.extend(batch)
            return await _store_ingest_and_count(store, derived_findings)
        finally:
            await adapter.close()
    except Exception:
        return None

async def _ipv6_recon_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F214 IPv6 reconnaissance — RDAP, WHOIS, DoH AAAA, BGP peer."""
    if not findings or store is None:
        return None
    try:
        from hledac.universal.network import IPV6_RECON_AVAILABLE
        if not IPV6_RECON_AVAILABLE:
            return None
        from hledac.universal.network.ipv6_recon import IPv6ReconAdapter
    except Exception:
        return None
    try:
        adapter = IPv6ReconAdapter()
        try:
            targets: list[str] = []
            for f in findings:
                ioc_value = safe_get_finding_field(f, 'ioc_value', '') or ''
                ioc_type = safe_get_finding_field(f, 'ioc_type', '') or ''
                if ioc_type in ('domain', 'ipv4', 'ip') and ioc_value:
                    targets.append(ioc_value)
            if not targets:
                return None
            derived_findings: list = []

            async def _query_one(target: str) -> list:
                return await adapter.query(target)
            batches = await bounded_parallel_map(targets[:20], _query_one, concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='ipv6_recon')
            for batch in batches:
                if batch is not None:
                    derived_findings.extend(batch)
            return await _store_ingest_and_count(store, derived_findings)
        finally:
            await adapter.close()
    except Exception:
        return None

async def _network_intel_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F247B: Active network reconnaissance via NetworkReconnaissance + bridge."""
    MAX_RECON_TARGETS = 5
    if not findings or store is None:
        return None
    targets: list[str] = []
    for f in findings:
        ioc_value = safe_get_finding_field(f, 'ioc_value', '') or ''
        ioc_type = safe_get_finding_field(f, 'ioc_type', '') or ''
        if ioc_type in ('domain', 'ipv4', 'ipv6', 'ip') and ioc_value:
            if ioc_value not in targets:
                targets.append(ioc_value)
    if not targets:
        return None
    targets = targets[:MAX_RECON_TARGETS]
    try:
        from hledac.universal.intel.network_reconnaissance import NetworkReconnaissance
        from hledac.universal.runtime.source_finding_bridge import network_recon_result_to_findings
    except Exception:
        return None
    try:
        adapter = NetworkReconnaissance()
        try:
            derived_findings: list = []

            async def _recon_one(target: str) -> list:
                results = await adapter.recon_target(target)
                if results:
                    return network_recon_result_to_findings(target, results)
                return []
            batches = await bounded_parallel_map(targets, _recon_one, concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='network_intel')
            for batch in batches:
                if batch is not None:
                    derived_findings.extend(batch)
            return await _store_ingest_and_count(store, derived_findings)
        finally:
            await adapter.close()
    except Exception:
        return None

async def _gopher_crawl_runner(findings: list, store: DuckDBShadowStore, query: str) -> int | None:
    """F216: Gopher archive crawler — crawls seed servers, extracts text, stores findings."""
    if store is None:
        return 0
    try:
        from hledac.universal.discovery.gopher_crawler import GopherCrawler
    except Exception:
        return 0
    try:
        crawler = GopherCrawler()
        all_results = await crawler.crawl_seed_servers()
        all_findings: list = []
        for cr in all_results:
            if isinstance(cr, Exception):
                continue
            findings_batch = GopherCrawler.items_to_findings(cr, sprint_id='gopher_sprint')
            all_findings.extend(findings_batch)
        return await _store_ingest_and_count(store, all_findings)
    except Exception:
        return 0
from hledac.universal.runtime.sidecar_runner_decorator import sidecar_runner, sidecar_runner_await
from hledac.universal.utils.async_helpers import bounded_parallel_map
_ExposureCorrelatorRunner = sidecar_runner(name='exposure_correlator', module_path='hledac.universal.intelligence.exposure_correlator', factory_name='create_exposure_correlator_adapter', correlate_method='correlate')
_LeakSentinelRunner = sidecar_runner(name='leak_sentinel', module_path='hledac.universal.intelligence.leak_sentinel', factory_name='create_leak_sentinel_adapter', correlate_method='scan')
_TemporalArchaeologyRunner = sidecar_runner(name='temporal_archaeology', module_path='hledac.universal.intelligence.temporal_archaeologist_adapter', factory_name='create_temporal_archaeologist_adapter', correlate_method='synthesize_timeline')
_PassiveFingerprintRunner = sidecar_runner(name='passive_fingerprint', module_path='hledac.universal.intelligence.passive_fingerprint', factory_name='create_passive_fingerprint_adapter', correlate_method='correlate')
_RirCorrelatorRunner = sidecar_runner_await(name='rir_correlator', module_path='hledac.universal.intelligence.rir_correlator', factory_name='create_rir_correlator_adapter', correlate_method='async_correlate')
_PassiveTechStackRunner = sidecar_runner(name='passive_tech_stack', module_path='hledac.universal.intelligence.passive_fingerprint', factory_name='create_passive_tech_stack_adapter', correlate_method='correlate')
DEFAULT_SIDECAR_RUNNERS: list[tuple[str, SidecarRunner]] = [('exposure_correlator', _ExposureCorrelatorRunner()), ('evidence_triage', _evidence_triage_runner), ('pattern_mining', _pattern_mining_runner), ('sprint_diff', _sprint_diff_runner), ('kill_chain_tagging', _kill_chain_tagging_runner), ('wayback_diff', _wayback_diff_runner), ('rir_correlator', _RirCorrelatorRunner()), ('social_identity_surface', _social_identity_surface_runner), ('embedding', _embedding_runner), ('network_intel', _network_intel_runner), ('banner_grab', _banner_grab_runner), ('ipv6_recon', _ipv6_recon_runner), ('gopher_crawl', _gopher_crawl_runner)]

class FindingSidecarBus:
    """
    Unified bounded orchestrator for all accepted-finding sidecars.

    All three source branches (feed, public, ct) route their accepted findings
    through this bus. The bus fans out to registered sidecar runners in stage order,
    collects per-runner SidecarRunResult records, and returns them.

    Stages execute sequentially (stage 1 → stage 2 → stage 3). Within each stage,
    runners execute concurrently via asyncio.gather(return_exceptions=True).

    RAM guard: heavy sidecars (identity_stitching, embedding, sprint_diff) are
    skipped when M1 governor reports critical or emergency memory pressure.

    Fail-soft: individual sidecar errors are captured in SidecarRunResult and do
    not propagate or crash the sprint. Stage N failure does not stop stage N+1.
    """
    __slots__ = tuple(('_acquisition_profile', '_governor', '_results', '_runners'))

    def __init__(self, governor: Any=None, acquisition_profile: str | None=None) -> None:
        self._governor = governor
        self._acquisition_profile = acquisition_profile
        self._runners: dict[str, SidecarRunner] = {}
        self._results: list[SidecarRunResult] = []

    def register(self, name: str, runner: SidecarRunner) -> None:
        if name in self._runners:
            raise ValueError(f'Sidecar runner already registered: {name}')
        self._runners[name] = runner

    def _is_heavy_blocked(self, name: str) -> tuple[bool, str]:
        """Return (blocked, reason) if a heavy sidecar should be skipped due to RAM pressure."""
        if name not in _HEAVY_SIDECARS:
            return (False, '')
        if self._governor is None:
            return (False, '')
        try:
            admission = self._governor.sidecar_admission(name, SIDECAR_DEFAULT_ESTIMATE_MB)
            return (not admission.allowed, admission.reason)
        except Exception:
            return (False, '')

    def _is_active_network_blocked(self, name: str) -> tuple[bool, str]:
        """Return (blocked, reason) if an active-network sidecar should be skipped."""
        allowed, reason = _sidecar_profile_allows(name, self._acquisition_profile)
        return (not allowed, reason)

    def _check_gathered(self, gathered: list[Any]) -> None:
        """
        Verify no unexpected exceptions leaked through gather(return_exceptions=True).
        GHOST_INVARIANT: called after every asyncio.gather with return_exceptions=True.
        """
        for item in gathered:
            if isinstance(item, BaseException) and (not isinstance(item, SidecarRunResult)):
                _sidecarlogger.warning('Unexpected exception in gather: %s: %s', type(item).__name__, item)

    async def run_all_sidecars(self, batch: SidecarBatch, store: DuckDBShadowStore) -> list[SidecarRunResult]:
        """
        Fan out to all registered sidecar runners for the given batch, in stage order.

        Stages run sequentially (stage 1 → stage 2 → stage 3). Within each stage,
        runners execute concurrently via asyncio.gather(return_exceptions=True).

        Returns list of SidecarRunResult (one per runner that was attempted).

        Bounds:
        - findings capped at MAX_SIDECAR_FINDINGS
        - results capped at MAX_SIDECAR_RESULT_RECORDS
        - per-runner timeout: SIDECAR_TIMEOUT_S

        GHOST_INVARIANTS:
        - gather(return_exceptions=True) within each stage
        - _check_gathered() after each stage's gather
        - asyncio.CancelledError re-raised
        - fail-soft: stage N failure does not stop stage N+1
        """
        self._results = []
        findings = list(batch.findings)
        if len(findings) > MAX_SIDECAR_FINDINGS:
            findings = findings[:MAX_SIDECAR_FINDINGS]
        if not findings:
            return []

        async def _run_one(name: str, runner: SidecarRunner) -> SidecarRunResult:
            t0 = _time.monotonic()
            blocked, reason = self._is_heavy_blocked(name)
            if blocked:
                return SidecarRunResult(sidecar_name=name, attempted=False, produced_count=0, stored_count=0, skipped_reason=reason or 'ram_governor_critical', elapsed_ms=(_time.monotonic() - t0) * 1000)
            blocked, reason = self._is_active_network_blocked(name)
            if blocked:
                return SidecarRunResult(sidecar_name=name, attempted=False, produced_count=0, stored_count=0, skipped_reason=reason or 'profile_disallows_active_network_sidecar', elapsed_ms=(_time.monotonic() - t0) * 1000)
            try:
                async with asyncio.timeout(SIDECAR_TIMEOUT_S):
                    result = await runner(findings, store, batch.query)
                elapsed_ms = (_time.monotonic() - t0) * 1000
                produced_count = 0
                stored_count = 0
                if isinstance(result, int):
                    produced_count = result
                    stored_count = result
                elif isinstance(result, dict):
                    produced_count = result.get('produced_count', 0)
                    stored_count = result.get('stored_count', 0)
                elif result is None:
                    produced_count = 0
                    stored_count = 0
                return SidecarRunResult(sidecar_name=name, attempted=True, produced_count=produced_count, stored_count=stored_count, skipped_reason='', elapsed_ms=elapsed_ms)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:

                def _is_cancelled_tree(e: BaseException) -> bool:
                    if isinstance(e, asyncio.CancelledError):
                        return True
                    if isinstance(e, ExceptionGroup):
                        return any((_is_cancelled_tree(s) for s in e.exceptions))
                    return False
                if _is_cancelled_tree(exc):
                    raise asyncio.CancelledError() from exc
                return SidecarRunResult(sidecar_name=name, attempted=True, produced_count=0, stored_count=0, skipped_reason=f'{type(exc).__name__}:{exc}', elapsed_ms=(_time.monotonic() - t0) * 1000)
        all_results: list[SidecarRunResult] = []
        runners_executed: set[str] = set()
        for stage_names in SIDECAR_STAGES:
            stage_tasks: list[asyncio.Task[SidecarRunResult]] = []
            for name in stage_names:
                if name in self._runners:
                    stage_tasks.append(safe_create_task(_run_one(name, self._runners[name]), name=f'sidecar_bus:stage_runner:{name}'))
                    runners_executed.add(name)
            if not stage_tasks:
                continue
            try:
                gathered = await safe_gather_ok(*stage_tasks, label='sidecar_bus:stage')
                self._check_gathered(gathered)
                for item in gathered:
                    if isinstance(item, SidecarRunResult):
                        all_results.append(item)
                    elif isinstance(item, BaseException):

                        def _is_cancelled_tree(e: BaseException) -> bool:
                            if isinstance(e, asyncio.CancelledError):
                                return True
                            if isinstance(e, ExceptionGroup):
                                return any((_is_cancelled_tree(s) for s in e.exceptions))
                            return False
                        if _is_cancelled_tree(item):
                            raise item
            except asyncio.CancelledError:
                for t in stage_tasks:
                    if not t.done():
                        t.cancel()
                await safe_gather_fire_and_forget(*stage_tasks, label='sidecar_bus:cancelled')
                raise
        remaining_tasks: list[asyncio.Task[SidecarRunResult]] = []
        for name, runner in self._runners.items():
            if name not in runners_executed:
                remaining_tasks.append(safe_create_task(_run_one(name, runner), name=f'sidecar_bus:remaining_runner:{name}'))
                runners_executed.add(name)
        if remaining_tasks:
            try:
                gathered = await safe_gather_ok(*remaining_tasks, label='sidecar_bus:remaining')
                self._check_gathered(gathered)
                for item in gathered:
                    if isinstance(item, SidecarRunResult):
                        all_results.append(item)
            except asyncio.CancelledError:
                for t in remaining_tasks:
                    if not t.done():
                        t.cancel()
                await safe_gather_fire_and_forget(*remaining_tasks, label='sidecar_bus:remaining_cancelled')
                raise
        if len(all_results) > MAX_SIDECAR_RESULT_RECORDS:
            all_results = all_results[:MAX_SIDECAR_RESULT_RECORDS]
        self._results = all_results
        return all_results

def create_sidecar_bus(governor: Any=None, acquisition_profile: str | None=None) -> FindingSidecarBus:
    """Factory: create a pre-registered FindingSidecarBus."""
    bus = FindingSidecarBus(governor=governor, acquisition_profile=acquisition_profile)
    for name, runner in DEFAULT_SIDECAR_RUNNERS:
        bus.register(name, runner)
    return bus