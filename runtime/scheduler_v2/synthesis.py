"""STEP 4 Phase 5.2 — SprintSynthesis: inline synthesis fallback for SprintSchedulerV2.

F350M-R / Issue SC-06.

Extracts the inline synthesis fallback from SprintSchedulerV2._run_synthesis_sidecar().
Delegates to AcquisitionOrchestrator._run_synthesis_sidecar if available,
otherwise runs the inline SynthesisRunner path.

Usage:
    from runtime.scheduler_v2.synthesis import run_synthesis_sidecar
    await run_synthesis_sidecar(scheduler, query, duckdb_store, lifecycle)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.scheduler_v2.scheduler import SprintSchedulerV2


async def run_synthesis_sidecar(
    scheduler: Any,
    query: str,
    duckdb_store: Any,
    lifecycle: Any,
) -> None:
    """Run synthesis in the windup phase.

    Delegates to AcquisitionOrchestrator._run_synthesis_sidecar if available,
    otherwise falls back to the inline SynthesisRunner implementation.
    """
    import msgspec

    _log = logging.getLogger(__name__)

    # ── Env gate ───────────────────────────────────────────────────────────
    from core.env_config import ENV

    if not ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS"):
        _log.debug("[F259] Synthesis skipped -- HLEDAC_ENABLE_HERMES_SYNTHESIS != '1'")
        scheduler._result.synthesis_success = False
        return

    # ── Try AcquisitionOrchestrator delegation ───────────────────────────────
    try:
        from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator

        orch = AcquisitionOrchestrator()

        class _MinimalCtx:
            __slots__ = ("query", "_result")

            def __init__(self, query: str, result: Any) -> None:
                self.query = query
                self._result = result

            @property
            def result(self) -> Any:
                return self._result

        ctx = _MinimalCtx(query, scheduler._result)
        await orch._run_synthesis_sidecar(ctx, duckdb_store, lifecycle)
        return
    except Exception as e:
        _log.debug("[F259] Delegation failed, using inline: %s", e)

    # ── Inline fallback ────────────────────────────────────────────────────
    if duckdb_store is None:
        _log.debug("[F259] Synthesis skipped -- no duckdb_store")
        return

    if not scheduler._result.accepted_findings:
        _log.info("[F259-SYN] early-exit: no findings, skipping synthesis")
        return

    findings: list[dict] = []
    try:
        if hasattr(duckdb_store, "get_top_findings"):
            findings = await duckdb_store.get_top_findings(limit=15)
        elif hasattr(duckdb_store, "get_recent_findings"):
            findings = await duckdb_store.get_recent_findings(limit=15)
    except Exception as e:
        _log.debug("[F259] Failed to get findings: %s", e)
        return

    if not findings:
        _log.debug("[F259] Synthesis skipped -- no findings")
        return

    try:
        from hledac.universal.core.model_runtime import ModelLifecycle
        from hledac.universal.brain.synthesis_runner import SynthesisRunner
    except ImportError as e:
        _log.debug("[F259] SynthesisRunner import failed: %s", e)
        scheduler._result.synthesis_engine = "import_failed"
        return

    # P2-02: try/finally guarantees runner.close() even on exception
    runner: SynthesisRunner | None = None
    try:
        lifecycle_instance = ModelLifecycle()
        runner = SynthesisRunner(lifecycle_instance)
        runner.set_compression_threshold(4000)
        runner._duckdb_store = duckdb_store
        if lifecycle is not None:
            runner.inject_lifecycle_adapter(lifecycle)
        report = await runner.synthesize_findings(
            query=query, findings=findings, force_synthesis=True
        )
        scheduler._result.synthesis_findings_count = len(findings)
        scheduler._result.synthesis_success = report is not None
        scheduler._result.synthesis_engine = getattr(
            runner, "_last_synthesis_engine", "synthesis_runner"
        ) or "synthesis_runner"
        if report is not None:
            try:
                scheduler._result.synthesis_text = msgspec.json.encode(
                    {
                        "query": query,
                        "ioc_entities": [
                            {"type": e.ioc_type, "value": e.value}
                            for e in getattr(report, "ioc_entities", None) or []
                        ],
                        "threat_summary": getattr(report, "threat_summary", ""),
                        "threat_actors": list(getattr(report, "threat_actors", None) or []),
                        "confidence": getattr(report, "confidence", 0.0),
                        "sources_count": getattr(report, "sources_count", 0),
                        "timestamp": getattr(report, "timestamp", 0.0),
                    }
                ).decode("utf-8")
            except Exception:
                scheduler._result.synthesis_text = str(report)[:4096]
            _log.info(
                "[F259] Synthesis complete: success=%s, findings=%d",
                scheduler._result.synthesis_success,
                scheduler._result.synthesis_findings_count,
            )
        else:
            scheduler._result.synthesis_text = ""
    except Exception as e:
        _log.debug("[F259] Synthesis failed: %s", e)
        scheduler._result.synthesis_success = False
        scheduler._result.synthesis_engine = "error"
        scheduler._result.synthesis_text = ""
    finally:
        # P2-02: ALWAYS close runner — finally guarantees execution even on exception.
        # This fixes the ~2GB Hermes3 model leak when synthesize_findings() raises.
        if runner is not None:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001 — best-effort; cleanup must not raise
                _log.debug("[F259] runner.close() raised (ignored)")
