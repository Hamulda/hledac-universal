"""
Sprint F350M: EnrichmentServices Extraction
==========================================

Owns forensics and multimodal enricher lifecycle extracted from SprintScheduler.

Lifecycle: init() → enrich_ct_findings() / enrich_findings_multimodal() → flush() → close()

Fail-safe throughout — all methods are noexcept on None inputs.
LMDB paths are derived from paths.py (no absolute paths).

Migrated to ConcurrencyBudgetRegistry (F268).
"""
import asyncio
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
import logging
from pathlib import Path
from typing import Any
from hledac.universal.utils.async_helpers import safe_gather
from hledac.universal.utils.lmdb_bulk import putmulti_bounded
log = logging.getLogger(__name__)
_FORENSICS_LMDB_NAME = 'forensics_enrichment.lmdb'
_MULTIMODAL_LMDB_NAME = 'multimodal_enrichment.lmdb'

def _get_forensics_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT
    return LMDB_ROOT / _FORENSICS_LMDB_NAME

def _get_multimodal_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT
    return LMDB_ROOT / _MULTIMODAL_LMDB_NAME

class EnrichmentServices:
    """
    Owns forensics and multimodal enricher lifecycle.

    Lifecycle: init() → enrich_ct_findings() / enrich_findings_multimodal() → flush() → close()

    Fail-safe throughout — all methods are noexcept on None inputs.
    LMDB paths are derived from paths.py (no absolute paths).
    """
    __slots__ = tuple(('_evidence_log', '_forensics_enricher', '_forensics_lmdb_env', '_multimodal_enricher', '_multimodal_governor', '_multimodal_lmdb_env'))

    def __init__(self, forensics_enricher: Any=None, forensics_lmdb_env: Any=None, multimodal_enricher: Any=None, multimodal_lmdb_env: Any=None, multimodal_governor: Any=None, evidence_log: Any=None):
        self._forensics_enricher = forensics_enricher
        self._forensics_lmdb_env = forensics_lmdb_env
        self._multimodal_enricher = multimodal_enricher
        self._multimodal_lmdb_env = multimodal_lmdb_env
        self._multimodal_governor = multimodal_governor
        self._evidence_log = evidence_log

    def inject_forensics_enricher(self, enricher: Any, lmdb_env: Any=None) -> None:
        """
        F195C: Inject ForensicsEnricher + LMDB env (external wiring).

        OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes
        enricher.enrich() during finding sidecar processing. LMDB env
        is owned by caller and passed here for reference only.
        All calls are fail-soft — exception or None → no-op.
        """
        self._forensics_enricher = enricher
        self._forensics_lmdb_env = lmdb_env

    def inject_multimodal_enricher(self, enricher: Any, lmdb_env: Any=None) -> None:
        """
        F195C: Inject MultimodalEnricher + LMDB env (external wiring).

        OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes
        enricher.enrich() during finding sidecar processing. LMDB env
        is owned by caller and passed here for reference only.
        All calls are fail-soft — exception or None → no-op.
        """
        self._multimodal_enricher = enricher
        self._multimodal_lmdb_env = lmdb_env

    def inject_evidence_log(self, evidence_log: Any) -> None:
        """
        Sprint F261 follow-up: Inject EvidenceLog (external wiring).

        OWNERSHIP: caller owns evidence_log lifecycle. EnrichmentServices
        invokes ``evidence_log.attach_forensic_analysis()`` during forensic
        enrichment to persist the forensic envelope in the tamper-evident
        evidence chain. All calls are fail-soft — exception or None → no-op.

        Mirrors the ``inject_forensics_enricher`` / ``inject_multimodal_enricher``
        setters. If the evidence_log is None, the attach step is silently
        skipped — backward-compatible with callers that do not yet wire
        the evidence chain.

        Pair with::

            from evidence_log import EvidenceLog
            elog = EvidenceLog(run_id=sprint_id, enable_persist=True)
            await elog.initialize()
            # ... pass to EnrichmentServices:
            enrichment_services.inject_evidence_log(elog)
        """
        self._evidence_log = evidence_log

    async def init(self) -> None:
        """F195C: Initialize forensics + multimodal enrichers and LMDBs."""
        await self._init_forensics()
        await self._init_multimodal()

    async def flush(self) -> None:
        """F195C: Flush forensics + multimodal LMDBs (no-op, LMDB auto-flushes)."""
        await self._flush_forensics()
        await self._flush_multimodal()

    async def close(self) -> None:
        """F195C: Close all enrichers and LMDBs at TEARDOWN."""
        await self._close_forensics()
        await self._close_multimodal()

    async def enrich_ct_findings(self, findings: list, result: Any=None, store: Any=None) -> None:
        """
        Enrich CT findings with forensics analysis before storage.

        Fail-safe: enrichment errors are silent — never crash or abort the sprint.
        Enrichment is best-effort: absence of forensics data is not an error.

        Sprint F261: when ``store`` is provided, also writes each successful
        enrichment as a CanonicalFinding (``source_type="forensic_analysis"``)
        via ``store.async_ingest_findings_batch``. The LMDB write is
        preserved as the primary forensic payload store; the DuckDB write
        is a derived finding for cross-source correlation.
        """
        if not findings:
            return
        enricher = self._forensics_enricher
        lmdb_env = self._forensics_lmdb_env
        if enricher is None or lmdb_env is None:
            return
        enriched_pairs: list[tuple[bytes, bytes]] = []
        try:
            semaphore = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, 'finding_id', None)
                            if fid:
                                res_for_lmdb = {k: v for k, v in res.items() if k != '_ioc_canonical_findings'}
                                try:
                                    import orjson
                                    payload = orjson.dumps(res_for_lmdb)
                                except ImportError:
                                    import msgspec.json as _msgspec_json
                                    payload = _msgspec_json.encode(res_for_lmdb)
                                enriched_pairs.append((fid.encode(), payload))
                                if result is not None:
                                    result.forensics_enriched_ct_findings += 1
                                if store is not None:
                                    try:
                                        from forensics.enrichment_service import make_canonical_finding_from_enrichment
                                        canonical = make_canonical_finding_from_enrichment(finding, res)
                                        if canonical is not None:
                                            all_to_ingest: list[Any] = [canonical]
                                            ioc_children = res.get('_ioc_canonical_findings') or []
                                            if ioc_children:
                                                from forensics.ioc_extractor import GLOBAL_IOC_BUDGET_DEFAULT
                                                current_ioc_total = 0
                                                if result is not None and hasattr(result, 'ioc_findings_total'):
                                                    current_ioc_total = int(getattr(result, 'ioc_findings_total', 0))
                                                remaining = max(0, GLOBAL_IOC_BUDGET_DEFAULT - current_ioc_total)
                                                ioc_children_trimmed = ioc_children[:remaining]
                                                all_to_ingest.extend(ioc_children_trimmed)
                                                if result is not None and hasattr(result, 'ioc_findings_total'):
                                                    result.ioc_findings_total += len(ioc_children_trimmed)
                                            await store.async_ingest_findings_batch(all_to_ingest)
                                    except Exception:
                                        pass
                                if self._evidence_log is not None:
                                    res_for_evidence = {k: v for k, v in res.items() if k != '_ioc_canonical_findings'}
                                    self._evidence_log.attach_forensic_analysis(finding_id=str(fid)[:128], forensic_result=res_for_evidence, source_id=str(fid)[:128], confidence=0.95)
                    except Exception:
                        pass
            await safe_gather(*[enrich_one(f) for f in findings], label='forensics_enrichment', logger_instance=log)
            if enriched_pairs:
                try:
                    written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                    log.debug('forensics LMDB bulk-write: %d/%d', written, len(enriched_pairs))
                except Exception as exc:
                    log.warning('forensics LMDB bulk-write failed: %s', exc)
        except Exception:
            pass

    async def enrich_findings_multimodal(self, findings: list, result: Any=None) -> None:
        """
        Enrich PDF/image findings with multimodal analysis before storage.

        Fail-safe: enrichment errors are silent — never crash or abort the sprint.
        Enrichment is best-effort: absence of multimodal data is not an error.
        """
        if not findings:
            return
        enricher = self._multimodal_enricher
        lmdb_env = self._multimodal_lmdb_env
        if enricher is None or lmdb_env is None:
            return
        enriched_pairs: list[tuple[bytes, bytes]] = []
        try:
            semaphore = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, 'finding_id', None)
                            if fid:
                                res_for_lmdb = {k: v for k, v in res.items() if k != '_ioc_canonical_findings'}
                                try:
                                    import orjson
                                    payload = orjson.dumps(res_for_lmdb)
                                except ImportError:
                                    import msgspec.json as _msgspec_json
                                    payload = _msgspec_json.encode(res_for_lmdb)
                                enriched_pairs.append((fid.encode(), payload))
                                if result is not None:
                                    result.multimodal_enriched_findings += 1
                    except Exception:
                        pass
            await safe_gather(*[enrich_one(f) for f in findings], label='multimodal_enrichment', logger_instance=log)
            if enriched_pairs:
                try:
                    written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                    log.debug('multimodal LMDB bulk-write: %d/%d', written, len(enriched_pairs))
                except Exception as exc:
                    log.warning('multimodal LMDB bulk-write failed: %s', exc)
        except Exception:
            pass

    async def _init_forensics(self) -> None:
        """Initialize forensics enricher and LMDB. Fail-safe — never raises."""
        try:
            from forensics.enrichment_service import ForensicsEnricher
            self._forensics_enricher = ForensicsEnricher()
            await self._forensics_enricher.initialize()
        except Exception as exc:
            log.debug('Forensics enricher init failed: %s', exc)
            self._forensics_enricher = None
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            db_path = _get_forensics_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._forensics_lmdb_env = open_lmdb_with_guard(db_path, map_size=50 * 1024 * 1024, max_dbs=1, writemap=False, sync=False)
        except Exception as exc:
            log.debug('Forensics LMDB open failed: %s', exc)
            self._forensics_lmdb_env = None

    async def _flush_forensics(self) -> None:
        """Flush forensics LMDB. Called at WINDUP. No-op if not initialized."""
        pass

    async def _close_forensics(self) -> None:
        """Close forensics enricher and LMDB at TEARDOWN."""
        if self._forensics_enricher is not None:
            try:
                await self._forensics_enricher.close()
            except Exception as exc:
                log.debug('Forensics enricher close failed: %s', exc)
            self._forensics_enricher = None
        if self._forensics_lmdb_env is not None:
            try:
                self._forensics_lmdb_env.close()
            except Exception as exc:
                log.debug('Forensics LMDB close failed: %s', exc)
            self._forensics_lmdb_env = None

    async def _init_multimodal(self) -> None:
        """Initialize multimodal enricher and LMDB. Fail-safe — never raises."""
        try:
            from multimodal.analyzer import MultimodalEnricher
            self._multimodal_enricher = MultimodalEnricher(governor=self._multimodal_governor, embedding_dim=1280, batch_size=4)
            await self._multimodal_enricher.initialize()
        except Exception as exc:
            log.debug('Multimodal enricher init failed: %s', exc)
            self._multimodal_enricher = None
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            db_path = _get_multimodal_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._multimodal_lmdb_env = open_lmdb_with_guard(db_path, map_size=50 * 1024 * 1024, max_dbs=1, writemap=False, sync=False)
        except Exception as exc:
            log.debug('Multimodal LMDB open failed: %s', exc)
            self._multimodal_lmdb_env = None

    async def _flush_multimodal(self) -> None:
        """Flush multimodal LMDB. Called at WINDUP. No-op if not initialized."""
        pass

    async def _close_multimodal(self) -> None:
        """Close multimodal enricher and LMDB at TEARDOWN."""
        if self._multimodal_enricher is not None:
            try:
                await self._multimodal_enricher.close()
            except Exception as exc:
                log.debug('Multimodal enricher close failed: %s', exc)
            self._multimodal_enricher = None
        if self._multimodal_lmdb_env is not None:
            try:
                self._multimodal_lmdb_env.close()
            except Exception as exc:
                log.debug('Multimodal LMDB close failed: %s', exc)
            self._multimodal_lmdb_env = None