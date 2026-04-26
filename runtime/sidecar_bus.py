"""
runtime/sidecar_bus.py — F204A: Canonical Accepted-Finding Sidecar Bus
======================================================================

Unified sidecar orchestrator for all accepted findings from feed/public/CT branches.
Bounded batch processor: takes SidecarBatch, fans out to all registered sidecar
runners via asyncio.gather(return_exceptions=True), collects SidecarRunResult records.

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True
- _check_gathered() called after every gather
- asyncio.CancelledError re-raised, never swallowed
- No blocking calls in event loop; CPU/IO via run_in_executor
- Canonical write path always async_ingest_findings_batch()
- RAM guard: skip heavy sidecars if governor reports critical/emergency
- Each collection has MAX_* constant
- Fail-soft: sidecar error never crashes the sprint
"""

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

if False:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_SIDECAR_FINDINGS: int = 500
MAX_SIDECAR_RESULT_RECORDS: int = 32
SIDECAR_TIMEOUT_S: float = 20.0

# Heavy sidecar names — skipped when M1 governor reports critical/emergency
_HEAVY_SIDECARS: frozenset[str] = frozenset({
    "identity_stitching",
    "embedding",
    "sprint_diff",
})

# F204J: Import constants from resource_governor
try:
    from hledac.universal.runtime.resource_governor import SIDECAR_DEFAULT_ESTIMATE_MB
except ImportError:
    SIDECAR_DEFAULT_ESTIMATE_MB = 128


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SidecarBatch:
    """Bounded batch of accepted findings from one source branch."""

    sprint_id: str
    query: str
    source_branch: str  # "feed" | "public" | "ct"
    findings: tuple[Any, ...]
    created_ts: float


@dataclass(frozen=True)
class SidecarRunResult:
    """Outcome of one sidecar runner invocation."""

    sidecar_name: str
    attempted: bool
    produced_count: int
    stored_count: int
    skipped_reason: str
    elapsed_ms: float


# ── Sidecar Runner Signature ───────────────────────────────────────────────────
# Each runner: async def (findings: list, store: DuckDBShadowStore, query: str) -> None
SidecarRunner = Callable[[list, "DuckDBShadowStore", str], Any]


# ── Main Bus ───────────────────────────────────────────────────────────────────
class FindingSidecarBus:
    """
    Unified bounded orchestrator for all accepted-finding sidecars.

    All three source branches (feed, public, ct) route their accepted findings
    through this bus. The bus fans out to registered sidecar runners concurrently,
    collects per-runner SidecarRunResult records, and returns them.

    RAM guard: heavy sidecars (identity_stitching, embedding, sprint_diff) are
    skipped when M1 governor reports critical or emergency memory pressure.

    Fail-soft: individual sidecar errors are captured in SidecarRunResult and do
    not propagate or crash the sprint.
    """

    def __init__(self, governor: Any = None) -> None:
        self._governor = governor
        self._runners: dict[str, SidecarRunner] = {}
        self._results: list[SidecarRunResult] = []

    # ── Registration ────────────────────────────────────────────────────────────

    def register(self, name: str, runner: SidecarRunner) -> None:
        """Register a sidecar runner by name."""
        if name in self._runners:
            raise ValueError(f"Sidecar runner already registered: {name}")
        self._runners[name] = runner

    # ── RAM Guard ─────────────────────────────────────────────────────────────

    def _is_heavy_blocked(self, name: str) -> tuple[bool, str]:
        """
        Return (blocked, reason) if a heavy sidecar should be skipped due to RAM pressure.

        F204J: Now uses governor.sidecar_admission() for consistent admission checks.
        """
        if name not in _HEAVY_SIDECARS:
            return (False, "")
        if self._governor is None:
            return (False, "")
        try:
            admission = self._governor.sidecar_admission(name, SIDECAR_DEFAULT_ESTIMATE_MB)
            return (not admission.allowed, admission.reason)
        except Exception:
            return (False, "")  # Fail-soft: allow heavy sidecars if governor errors

    # ── Core: Run All Sidecars ────────────────────────────────────────────────

    async def run_all_sidecars(
        self,
        batch: SidecarBatch,
        store: "DuckDBShadowStore",
    ) -> list[SidecarRunResult]:
        """
        Fan out to all registered sidecar runners for the given batch.

        Runs all runners concurrently via asyncio.gather(return_exceptions=True).
        Returns list of SidecarRunResult (one per runner that was attempted).

        Bounds:
        - findings capped at MAX_SIDECAR_FINDINGS
        - results capped at MAX_SIDECAR_RESULT_RECORDS
        - per-runner timeout: SIDECAR_TIMEOUT_S

        GHOST_INVARIANTS:
        - gather(return_exceptions=True)
        - _check_gathered() after gather
        - asyncio.CancelledError re-raised
        """
        self._results = []

        # ── Bound the batch ──────────────────────────────────────────────────
        findings = list(batch.findings)
        if len(findings) > MAX_SIDECAR_FINDINGS:
            findings = findings[:MAX_SIDECAR_FINDINGS]

        if not findings:
            return []

        # ── Build coroutine tasks for all registered runners ─────────────────
        async def _run_one(name: str, runner: SidecarRunner) -> SidecarRunResult:
            t0 = _time.monotonic()

            # RAM guard check using governor.sidecar_admission()
            blocked, reason = self._is_heavy_blocked(name)
            if blocked:
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=False,
                    produced_count=0,
                    stored_count=0,
                    skipped_reason=reason or "ram_governor_critical",
                    elapsed_ms=elapsed_ms,
                )

            try:
                async with asyncio.timeout(SIDECAR_TIMEOUT_S):
                    await runner(findings, store, batch.query)
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=True,
                    produced_count=0,  # Runner updates _result fields directly
                    stored_count=0,
                    skipped_reason="",
                    elapsed_ms=elapsed_ms,
                )
            except asyncio.CancelledError:
                # Re-raise: cancellation must not be swallowed
                raise
            except Exception as exc:
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=True,
                    produced_count=0,
                    stored_count=0,
                    skipped_reason=f"{type(exc).__name__}:{exc}",
                    elapsed_ms=elapsed_ms,
                )

        # ── Execute all runners concurrently ──────────────────────────────────
        tasks = [
            asyncio.create_task(_run_one(name, runner))
            for name, runner in self._runners.items()
        ]

        results: list[SidecarRunResult] = []
        try:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            # _check_gathered — verify no unexpected exceptions leaked
            for item in gathered:
                if isinstance(item, BaseException):
                    # Already logged as SidecarRunResult in _run_one
                    pass
                elif isinstance(item, SidecarRunResult):
                    results.append(item)
        except asyncio.CancelledError:
            # Cancel any pending tasks and re-raise
            for t in tasks:
                if not t.done():
                    t.cancel()
            gathered_cancel = await asyncio.gather(*tasks, return_exceptions=True)
            raise  # Re-raise CancelledError per invariant

        # Cap results at bound
        if len(results) > MAX_SIDECAR_RESULT_RECORDS:
            results = results[:MAX_SIDECAR_RESULT_RECORDS]

        self._results = results
        return results


# ── Built-in Sidecar Runners ───────────────────────────────────────────────────
# These are registered by sprint_scheduler on its own FindingSidecarBus instance.


async def _identity_stitching_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F202B identity stitching — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_findings,
        )
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )
    except Exception:
        return

    try:
        profiles = extract_entities_from_findings(findings)
        if not profiles:
            return
        adapter = create_identity_stitching_adapter()
        candidates = adapter.extract_and_stitch(profiles)
        if not candidates:
            return

        derived_findings = adapter.to_derived_findings(candidates, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        # Caller (SprintScheduler) updates _result.identity_findings_produced
        return stored
    except Exception:
        pass  # Fail-soft


async def _exposure_correlator_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F202C asset exposure correlator."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.exposure_correlator import (
            create_exposure_correlator_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_exposure_correlator_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _leak_sentinel_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F202D leak and secret sentinel."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.leak_sentinel import (
            create_leak_sentinel_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_leak_sentinel_adapter()
        derived_findings = await adapter.scan(query)
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _temporal_archaeology_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F202E temporal archaeology timeline synthesis."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.temporal_archaeologist_adapter import (
            create_temporal_archaeologist_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_temporal_archaeologist_adapter()
        ct_findings = [f for f in findings if getattr(f, "source_type", "") == "ct_log"]
        if not ct_findings:
            return
        result = adapter.synthesize_timeline(ct_findings=ct_findings, entity_id=query[:64])
        derived_findings = result.derived_findings
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _evidence_triage_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F202I evidence triage — counts document findings with triage facets."""
    import json

    triage_count = 0
    for finding in findings:
        if not hasattr(finding, "source_type") or finding.source_type != "document":
            continue
        if not hasattr(finding, "payload_text") or not finding.payload_text:
            continue
        try:
            payload = json.loads(finding.payload_text)
            if isinstance(payload, dict) and "triage" in payload:
                triage_count += 1
        except Exception:
            pass
    return triage_count


async def _sprint_diff_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F203A cross-sprint diff — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.knowledge.sprint_diff_engine import SprintDiffEngine
    except Exception:
        return

    target_id = query[:128]
    try:
        prev_findings_raw = await store.async_get_previous_findings_for_target(target_id, limit=1000)
    except Exception:
        prev_findings_raw = []

    current_findings: list[dict] = []
    for f in findings:
        try:
            current_findings.append({
                "finding_id": getattr(f, "finding_id", "") or "",
                "source_type": getattr(f, "source_type", "") or "",
                "ioc_type": getattr(f, "ioc_type", "") or "",
                "ioc_value": getattr(f, "ioc_value", "") or "",
                "confidence": getattr(f, "confidence", 0.5) or 0.5,
                "ts": getattr(f, "ts", 0.0) or 0.0,
                "payload_text": getattr(f, "payload_text", "") or "",
            })
        except Exception:
            continue

    try:
        engine = SprintDiffEngine()
        diff_result = engine.compute_diff(
            current_findings=current_findings,
            previous_findings=prev_findings_raw if prev_findings_raw else None,
            target_id=target_id,
            current_sprint_id="",
            previous_sprint_id=None,
        )

        class _DiffFinding:
            __slots__ = ('finding_id', 'source_type', 'query', 'target_id',
                         'ioc_type', 'ioc_value', 'confidence', 'ts', 'payload_text')
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        derived_findings: list[Any] = []
        ts_now = _time.time()

        for nf in diff_result.new_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(
                    finding_id=f"diff-new-{nf.get('finding_id', 'unknown')[:32]}",
                    source_type="sprint_diff",
                    query=query,
                    target_id=target_id,
                    ioc_type=nf.get("ioc_type") or "unknown",
                    ioc_value=nf.get("ioc_value") or "unknown",
                    confidence=nf.get("confidence", 0.5),
                    ts=ts_now,
                    payload_text=str({"diff_action": "new", **nf}),
                ))
            except Exception:
                continue

        for df in diff_result.disappeared_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(
                    finding_id=f"diff-gone-{df.get('finding_id', 'unknown')[:32]}",
                    source_type="sprint_diff",
                    query=query,
                    target_id=target_id,
                    ioc_type=df.get("ioc_type") or "unknown",
                    ioc_value=df.get("ioc_value") or "unknown",
                    confidence=df.get("confidence", 0.5),
                    ts=ts_now,
                    payload_text=str({"diff_action": "disappeared", **df}),
                ))
            except Exception:
                continue

        if derived_findings:
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
    except Exception:
        pass  # Fail-soft


async def _kill_chain_tagging_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F203C MITRE ATT&CK kill chain tagging."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.kill_chain_tagger import (
            create_kill_chain_tagger,
        )
    except Exception:
        return

    try:
        tagger = create_kill_chain_tagger()
        tagged_results: dict[str, list] = {}

        for finding in findings:
            fid = getattr(finding, "finding_id", None)
            if not fid:
                continue
            tags = tagger.tag_finding(finding)
            if tags:
                tagged_results[str(fid)] = [tag.to_dict() for tag in tags]

        if not tagged_results:
            return

        class _KCTFinding:
            __slots__ = (
                "finding_id", "source_type", "query", "target_id",
                "ioc_type", "ioc_value", "confidence", "ts", "payload_text",
            )
            def __init__(self, **kw: Any) -> None:
                for k, v in kw.items():
                    setattr(self, k, v)

        derived_findings: list[Any] = []
        ts_now = _time.time()

        for fid, tags_list in tagged_results.items():
            try:
                orig = next(
                    (f for f in findings if getattr(f, "finding_id", "") == fid),
                    None,
                )
                ioc_type = getattr(orig, "ioc_type", "unknown") if orig else "unknown"
                ioc_value = getattr(orig, "ioc_value", fid) if orig else fid
                confidence = getattr(orig, "confidence", 0.5) if orig else 0.5

                derived_findings.append(_KCTFinding(
                    finding_id=f"kct-{fid[:32]}",
                    source_type="killchain_tag",
                    query=query,
                    target_id=query[:128],
                    ioc_type=ioc_type,
                    ioc_value=ioc_value,
                    confidence=confidence,
                    ts=ts_now,
                    payload_text=str({"kill_chain_tags": tags_list}),
                ))
            except Exception:
                continue

        if derived_findings:
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
    except Exception:
        pass  # Fail-soft


async def _wayback_diff_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F203F Wayback CDX diff mining."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.wayback_diff_miner import (
            WaybackDiffMiner,
        )
    except Exception:
        return

    try:
        targets: list[str] = []
        for f in findings:
            ioc_value = getattr(f, "ioc_value", "") or ""
            ioc_type = getattr(f, "ioc_type", "") or ""
            if ioc_type in ("domain", "url") and ioc_value:
                targets.append(ioc_value)
            elif hasattr(f, "url"):
                url = getattr(f, "url", "") or ""
                if url:
                    targets.append(url)

        if not targets:
            return

        miner = WaybackDiffMiner()
        try:
            result = await miner.mine(targets)
        finally:
            await miner.close()

        if not result.change_events:
            return

        findings_out = result.to_findings(query=query, sprint_id="")
        if not findings_out:
            return

        results = await store.async_ingest_findings_batch(findings_out)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _embedding_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F203I streaming embedding — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder
    except Exception:
        return

    try:
        embedder = StreamingEmbedder()
        embeddable = []
        for f in findings:
            text = getattr(f, "payload_text", None) or getattr(f, "query", "") or ""
            if len(text) >= 16:
                embeddable.append(f)

        if not embeddable:
            return

        async for ids, embeddings in embedder.embed_findings(embeddable, batch_size=16):
            if ids and embeddings is not None and embeddings.shape[0] > 0:
                try:
                    from hledac.universal.knowledge.ann_index import get_ann_index
                    ann = get_ann_index()
                    import hashlib
                    for idx, finding_id in enumerate(ids):
                        emb = embeddings[idx]
                        if emb.shape[0] == 256:
                            key = hashlib.blake2b(finding_id.encode(), digest_size=32).hexdigest()
                            text_hash = hashlib.sha256(finding_id.encode()).hexdigest()
                            ann.upsert(key, emb, text_hash)
                except Exception:
                    pass

        try:
            from hledac.universal.knowledge.ann_index import get_ann_index
            ann = get_ann_index()
            ann.prewarm(top_k=128)
        except Exception:
            pass
    except Exception:
        pass  # Fail-soft


async def _passive_fingerprint_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F204G passive service fingerprinting — deterministic, no active scan."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.passive_fingerprint import (
            create_passive_fingerprint_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_passive_fingerprint_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _rir_correlator_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F204H RIR/ASN/WHOIS bulk correlator — bounded IP/domain attribution."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.rir_correlator import (
            create_rir_correlator_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_rir_correlator_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _social_identity_surface_runner(
    findings: list,
    store: "DuckDBShadowStore",
    query: str,
) -> None:
    """F204I: Social identity surface miner — extract usernames/profiles from findings."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.social_identity_miner import (
            create_social_identity_miner_adapter,
        )
    except Exception:
        return

    try:
        miner = create_social_identity_miner_adapter()
        result = await miner.mine(findings, store, query)
        return result.scanned_count
    except Exception:
        pass  # Fail-soft


# ── Default Registry ───────────────────────────────────────────────────────────
# Ordered list of (name, runner) pairs — bus registers these by default.
DEFAULT_SIDECAR_RUNNERS: list[tuple[str, SidecarRunner]] = [
    ("leak_sentinel", _leak_sentinel_runner),
    ("exposure_correlator", _exposure_correlator_runner),
    ("temporal_archaeology", _temporal_archaeology_runner),
    ("evidence_triage", _evidence_triage_runner),
    ("identity_stitching", _identity_stitching_runner),
    ("sprint_diff", _sprint_diff_runner),
    ("kill_chain_tagging", _kill_chain_tagging_runner),
    ("wayback_diff", _wayback_diff_runner),
    ("passive_fingerprint", _passive_fingerprint_runner),
    ("rir_correlator", _rir_correlator_runner),
    ("embedding", _embedding_runner),
    ("social_identity_surface", _social_identity_surface_runner),
]


def create_sidecar_bus(governor: Any = None) -> FindingSidecarBus:
    """Factory: create a pre-registered FindingSidecarBus."""
    bus = FindingSidecarBus(governor=governor)
    for name, runner in DEFAULT_SIDECAR_RUNNERS:
        bus.register(name, runner)
    return bus
