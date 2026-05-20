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

import lmdb

from hledac.universal.utils.async_helpers import _check_gathered

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
    ):
        self._forensics_enricher = forensics_enricher
        self._forensics_lmdb_env = forensics_lmdb_env
        self._multimodal_enricher = multimodal_enricher
        self._multimodal_lmdb_env = multimodal_lmdb_env
        self._multimodal_governor = multimodal_governor

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

    async def enrich_ct_findings(self, findings: list, result: Any = None) -> None:
        """
        Enrich CT findings with forensics analysis before storage.

        Fail-safe: enrichment errors are silent — never crash or abort the sprint.
        Enrichment is best-effort: absence of forensics data is not an error.
        """
        if not findings:
            return
        enricher = self._forensics_enricher
        lmdb_env = self._forensics_lmdb_env
        if enricher is None or lmdb_env is None:
            return

        try:
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                # Sprint F251C: orjson available (requirements.txt line 27)
                                try:
                                    import orjson
                                    payload = orjson.dumps(res)
                                except ImportError:
                                    import json
                                    payload = json.dumps(res).encode()
                                with lmdb_env.begin(write=True) as txn:
                                    txn.put(fid.encode(), payload)
                                if result is not None:
                                    result.forensics_enriched_ct_findings += 1
                    except Exception:
                        pass  # Fail-safe: never crash

            raw_results = await asyncio.gather(
                *[enrich_one(f) for f in findings], return_exceptions=True
            )
            _check_gathered(raw_results, log, "forensics_enrichment")
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

        try:
            semaphore = asyncio.Semaphore(3)

            async def enrich_one(finding) -> None:
                async with semaphore:
                    try:
                        res = await enricher.enrich(finding)
                        if res is not None:
                            fid = getattr(finding, "finding_id", None)
                            if fid:
                                # Sprint F251C: orjson available (requirements.txt line 27)
                                try:
                                    import orjson
                                    payload = orjson.dumps(res)
                                except ImportError:
                                    import json
                                    payload = json.dumps(res).encode()
                                with lmdb_env.begin(write=True) as txn:
                                    txn.put(fid.encode(), payload)
                                if result is not None:
                                    result.multimodal_enriched_findings += 1
                    except Exception:
                        pass  # Fail-safe: never crash

            raw_results = await asyncio.gather(
                *[enrich_one(f) for f in findings], return_exceptions=True
            )
            _check_gathered(raw_results, log, "multimodal_enrichment")
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
            db_path = _get_forensics_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._forensics_lmdb_env = lmdb.open(
                str(db_path),
                map_size=50 * 1024 * 1024,  # 50MB max for enrichment data
                max_dbs=1,
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
            db_path = _get_multimodal_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._multimodal_lmdb_env = lmdb.open(
                str(db_path),
                map_size=50 * 1024 * 1024,  # 50MB max
                max_dbs=1,
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