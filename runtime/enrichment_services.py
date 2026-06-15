"""
Sprint F350M: EnrichmentServices Extraction
==========================================

Owns forensics and multimodal enricher lifecycle extracted from SprintScheduler.

Lifecycle: init() → enrich_ct_findings() / enrich_findings_multimodal() → flush() → close()

Fail-safe throughout — all methods are noexcept on None inputs.
LMDB paths are derived from paths.py (no absolute paths).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from hledac.universal.utils.async_helpers import safe_gather
from hledac.universal.utils.lmdb_bulk import putmulti_bounded

log = logging.getLogger(__name__)

_FORENSICS_LMDB_NAME = "forensics_enrichment.lmdb"
_MULTIMODAL_LMDB_NAME = "multimodal_enrichment.lmdb"


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

    def __init__(
        self,
        forensics_enricher: Any = None,
        forensics_lmdb_env: Any = None,
        multimodal_enricher: Any = None,
        multimodal_lmdb_env: Any = None,
        multimodal_governor: Any = None,
        evidence_log: Any = None,  # Sprint F261 follow-up
    ):
        self._forensics_enricher = forensics_enricher
        self._forensics_lmdb_env = forensics_lmdb_env
        self._multimodal_enricher = multimodal_enricher
        self._multimodal_lmdb_env = multimodal_lmdb_env
        self._multimodal_governor = multimodal_governor
        # Sprint F261 follow-up: tamper-evident evidence chain attachment.
        # None = skip the attach step (backward-compatible default).
        self._evidence_log = evidence_log

    # ── injection setters ──────────────────────────────────────────────────

    def inject_forensics_enricher(self, enricher: Any, lmdb_env: Any = None) -> None:
        """
        F195C: Inject ForensicsEnricher + LMDB env (external wiring).

        OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes
        enricher.enrich() during finding sidecar processing. LMDB env
        is owned by caller and passed here for reference only.
        All calls are fail-soft — exception or None → no-op.
        """
        self._forensics_enricher = enricher
        self._forensics_lmdb_env = lmdb_env

    def inject_multimodal_enricher(self, enricher: Any, lmdb_env: Any = None) -> None:
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

    # ── lifecycle (called by SprintScheduler.run()) ───────────────────────

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

    # ── read sites (called from sprint_ct_log_pipeline) ──────────────────

    async def enrich_ct_findings(
        self,
        findings: list,
        result: Any = None,
        store: Any = None,
    ) -> None:
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

        # Sprint F265B: collect all (fid, payload) pairs then bulk-write
        # in a single txn — ~6-7× faster on M1 UMA (TLB shootdown is the
        # bottleneck, not memcpy). Per-item txn.put() = N mutex acquisitions.
        enriched_pairs: list[tuple[bytes, bytes]] = []

        try:
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                # Sprint F251C: orjson available (requirements.txt line 27)
                                # Sprint F262: strip transient IOC children field
                                # before LMDB serialization — orjson cannot encode
                                # msgspec.Struct (the IOC CanonicalFinding objects).
                                # IOC children go to DuckDB via the batched ingest
                                # below, not LMDB. No-op for multimodal enrichment
                                # where the field is absent.
                                res_for_lmdb = {
                                    k: v
                                    for k, v in res.items()
                                    if k != "_ioc_canonical_findings"
                                }
                                try:
                                    import orjson
                                    payload = orjson.dumps(res_for_lmdb)
                                except ImportError:
                                    import json
                                    payload = json.dumps(res_for_lmdb).encode()
                                enriched_pairs.append((fid.encode(), payload))
                                if result is not None:
                                    result.forensics_enriched_ct_findings += 1

                                # Sprint F261: derived CanonicalFinding
                                # (source_type="forensic_analysis") into DuckDB.
                                # Best-effort: never crash on store failure.
                                # Sprint F262: also includes IOC children (per-finding
                                # IOC findings emitted by ForensicsEnricher.enrich()
                                # sub-step 6) in a SINGLE batched ingest call. The
                                # IOC children use deterministic content-hash
                                # finding_ids so cross-parent duplicates collapse
                                # via the LMDB WAL upsert.
                                if store is not None:
                                    try:
                                        from forensics.enrichment_service import (
                                            make_canonical_finding_from_enrichment,
                                        )
                                        canonical = (
                                            make_canonical_finding_from_enrichment(
                                                finding, res
                                            )
                                        )
                                        if canonical is not None:
                                            # Collect forensic parent + IOC children
                                            all_to_ingest: list[Any] = [canonical]
                                            ioc_children = res.get(
                                                "_ioc_canonical_findings"
                                            ) or []
                                            if ioc_children:
                                                # Sprint F262: per-sprint IOC budget
                                                from forensics.ioc_extractor import (
                                                    GLOBAL_IOC_BUDGET_DEFAULT,
                                                )
                                                current_ioc_total = 0
                                                if result is not None and hasattr(
                                                    result, "ioc_findings_total"
                                                ):
                                                    current_ioc_total = int(
                                                        getattr(
                                                            result,
                                                            "ioc_findings_total",
                                                            0,
                                                        )
                                                    )
                                                remaining = max(
                                                    0,
                                                    GLOBAL_IOC_BUDGET_DEFAULT
                                                    - current_ioc_total,
                                                )
                                                # Trim IOC children to remaining budget
                                                ioc_children_trimmed = (
                                                    ioc_children[:remaining]
                                                )
                                                all_to_ingest.extend(
                                                    ioc_children_trimmed
                                                )
                                                if (
                                                    result is not None
                                                    and hasattr(
                                                        result,
                                                        "ioc_findings_total",
                                                    )
                                                ):
                                                    result.ioc_findings_total += len(
                                                        ioc_children_trimmed
                                                    )
                                            # Single batched call — chunking handled
                                            # inside async_ingest_findings_batch (CHUNK_SIZE=500)
                                            await store.async_ingest_findings_batch(
                                                all_to_ingest
                                            )
                                    except Exception:
                                        # Fail-safe: DuckDB write is best-effort
                                        pass

                                # Sprint F261 follow-up: persist forensic envelope
                                # in the tamper-evident evidence chain. Best-effort
                                # — attach_forensic_analysis is itself fail-safe
                                # (returns None on any error). No additional
                                # try/except needed: the outer block catches
                                # anything, and the call is bounded by
                                # _FORENSIC_MAX_* in evidence_log.py.
                                # Sprint F262: also strip transient IOC children
                                # before envelope serialization (same reason as LMDB).
                                if self._evidence_log is not None:
                                    res_for_evidence = {
                                        k: v
                                        for k, v in res.items()
                                        if k != "_ioc_canonical_findings"
                                    }
                                    self._evidence_log.attach_forensic_analysis(
                                        finding_id=str(fid)[:128],
                                        forensic_result=res_for_evidence,
                                        source_id=str(fid)[:128],
                                        confidence=0.95,
                                    )
                    except Exception:
                        pass  # Fail-safe: never crash

            # F261: safe_gather centralizes [I6][I7][I8] invariants.
            await safe_gather(
                *[enrich_one(f) for f in findings],
                label="forensics_enrichment",
                logger_instance=log,
            )

            # Sprint F265B: bulk write — single txn for all pairs.
            if enriched_pairs:
                try:
                    written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                    log.debug("forensics LMDB bulk-write: %d/%d", written, len(enriched_pairs))
                except Exception as exc:
                    log.warning("forensics LMDB bulk-write failed: %s", exc)
        except Exception:
            pass  # Fail-safe: never crash

    async def enrich_findings_multimodal(
        self, findings: list, result: Any = None
    ) -> None:
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

        # Sprint F265B: collect all (fid, payload) pairs then bulk-write
        # in a single txn — ~6-7× faster on M1 UMA (TLB shootdown is the
        # bottleneck, not memcpy). Per-item txn.put() = N mutex acquisitions.
        enriched_pairs: list[tuple[bytes, bytes]] = []

        try:
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                # Sprint F251C: orjson available (requirements.txt line 27)
                                # Sprint F262: strip transient IOC children field
                                # before LMDB serialization — orjson cannot encode
                                # msgspec.Struct (the IOC CanonicalFinding objects).
                                # IOC children go to DuckDB via the batched ingest
                                # below, not LMDB. No-op for multimodal enrichment
                                # where the field is absent.
                                res_for_lmdb = {
                                    k: v
                                    for k, v in res.items()
                                    if k != "_ioc_canonical_findings"
                                }
                                try:
                                    import orjson
                                    payload = orjson.dumps(res_for_lmdb)
                                except ImportError:
                                    import json
                                    payload = json.dumps(res_for_lmdb).encode()
                                enriched_pairs.append((fid.encode(), payload))
                                if result is not None:
                                    result.multimodal_enriched_findings += 1
                    except Exception:
                        pass  # Fail-safe: never crash

            # F261: safe_gather centralizes [I6][I7][I8] invariants.
            await safe_gather(
                *[enrich_one(f) for f in findings],
                label="multimodal_enrichment",
                logger_instance=log,
            )

            # Sprint F265B: bulk write — single txn for all pairs.
            if enriched_pairs:
                try:
                    written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                    log.debug("multimodal LMDB bulk-write: %d/%d", written, len(enriched_pairs))
                except Exception as exc:
                    log.warning("multimodal LMDB bulk-write failed: %s", exc)
        except Exception:
            pass  # Fail-safe: never crash

    # ── internal init/close/flush ─────────────────────────────────────────

    async def _init_forensics(self) -> None:
        """Initialize forensics enricher and LMDB. Fail-safe — never raises."""
        try:
            from forensics.enrichment_service import ForensicsEnricher

            self._forensics_enricher = ForensicsEnricher()
            await self._forensics_enricher.initialize()
        except Exception as exc:
            log.debug("Forensics enricher init failed: %s", exc)
            self._forensics_enricher = None

        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            db_path = _get_forensics_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._forensics_lmdb_env = open_lmdb_with_guard(
                db_path,
                map_size=50 * 1024 * 1024,  # 50MB max for enrichment data
                max_dbs=1,
                writemap=False,
                sync=False,
            )
        except Exception as exc:
            log.debug("Forensics LMDB open failed: %s", exc)
            self._forensics_lmdb_env = None

    async def _flush_forensics(self) -> None:
        """Flush forensics LMDB. Called at WINDUP. No-op if not initialized."""
        pass  # LMDB write-only env auto-flushes; nothing to do

    async def _close_forensics(self) -> None:
        """Close forensics enricher and LMDB at TEARDOWN."""
        if self._forensics_enricher is not None:
            try:
                await self._forensics_enricher.close()
            except Exception as exc:
                log.debug("Forensics enricher close failed: %s", exc)
            self._forensics_enricher = None
        if self._forensics_lmdb_env is not None:
            try:
                self._forensics_lmdb_env.close()
            except Exception as exc:
                log.debug("Forensics LMDB close failed: %s", exc)
            self._forensics_lmdb_env = None

    async def _init_multimodal(self) -> None:
        """Initialize multimodal enricher and LMDB. Fail-safe — never raises."""
        try:
            from multimodal.analyzer import MultimodalEnricher

            self._multimodal_enricher = MultimodalEnricher(
                governor=self._multimodal_governor,
                embedding_dim=1280,
                batch_size=4,
            )
            await self._multimodal_enricher.initialize()
        except Exception as exc:
            log.debug("Multimodal enricher init failed: %s", exc)
            self._multimodal_enricher = None

        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            db_path = _get_multimodal_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._multimodal_lmdb_env = open_lmdb_with_guard(
                db_path,
                map_size=50 * 1024 * 1024,  # 50MB max
                max_dbs=1,
                writemap=False,
                sync=False,
            )
        except Exception as exc:
            log.debug("Multimodal LMDB open failed: %s", exc)
            self._multimodal_lmdb_env = None

    async def _flush_multimodal(self) -> None:
        """Flush multimodal LMDB. Called at WINDUP. No-op if not initialized."""
        pass  # LMDB write-only env auto-flushes; nothing to do

    async def _close_multimodal(self) -> None:
        """Close multimodal enricher and LMDB at TEARDOWN."""
        if self._multimodal_enricher is not None:
            try:
                await self._multimodal_enricher.close()
            except Exception as exc:
                log.debug("Multimodal enricher close failed: %s", exc)
            self._multimodal_enricher = None
        if self._multimodal_lmdb_env is not None:
            try:
                self._multimodal_lmdb_env.close()
            except Exception as exc:
                log.debug("Multimodal LMDB close failed: %s", exc)
            self._multimodal_lmdb_env = None
