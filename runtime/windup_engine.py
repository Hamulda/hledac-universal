"""
Sprint 8VI §A: WINDUP fáze — run_windup()

Extrahováno z __main__.py WINDUP sekce.
Dedup, GNN inference, ANE semantic dedup, MoE synthesis,
hypothesis enqueue, DuckPGQ checkpoint, scorecard.

**VERDICT: DORMANT (donor/alternate)** — 2026-04-27, F206B

DORMANT / ALTERNATE PATH (Sprint 8F7)
======================================
run_windup() je DORMANT — definovaná ale NIKDY nevolaná v produkci.
Active runtime path je __main__._windup_synthesis().

Tento modul je ponechán jako donor/alternate pro případné budoucí použití.
NEPOUŽÍVÁ se v aktuálním produkčním běhu.
"""

from __future__ import annotations

import logging
import resource as _resource
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .sprint_scheduler import SprintScheduler

logger = logging.getLogger(__name__)


def _safe_get_breaker_states() -> dict:
    """Circuit breaker states — failsafe."""
    try:
        from transport.circuit_breaker import get_all_breaker_states
        return get_all_breaker_states()
    except Exception:
        return {}


async def run_windup(
    scheduler: "SprintScheduler",
    sprint_query: str,
    t_warmup_end: float,
    t_active_end: float,
) -> dict:
    """
    WINDUP fáze — scorecard, dedup, graph stats, hypothesis enqueue.

    Kroky:
      1. Parquet dedup + ranking (Polars)
      2. GNN inference + anomaly scores
      3. DuckPGQ stats + top IOC traversal
      4. ANE semantic dedup
      5. MoE synthesis engine selection + synthesis
      6. Hypothesis enqueue (top-3)
      7. DuckPGQ checkpoint
      8. Scorecard dict

    Nikdy nevyhodí výjimku.
    """
    # 1. Parquet dedup + ranking
    ranked_path: Optional[str] = None
    try:
        ranked_path = scheduler.deduplicate_and_rank_findings()
        logger.info(f"[WINDUP] Dedup ranked → {ranked_path}")
    except Exception as e:
        logger.warning(f"[WINDUP] Polars dedup failed: {e}")

    # 2. GNN inference nad IOC grafem
    gnn_predictions: list = []
    anomalies: list = []
    try:
        from brain.gnn_predictor import predict_from_edge_list, get_anomaly_scores
        if hasattr(scheduler, "_ioc_graph") and scheduler._ioc_graph is not None:
            edge_list = scheduler._ioc_graph.export_edge_list()
            if edge_list:
                gnn_predictions = predict_from_edge_list(edge_list, top_k=10)
                anomalies = get_anomaly_scores(edge_list)
                logger.info(
                    f"[GNN] {len(gnn_predictions)} predicted links, "
                    f"{len(anomalies)} anomalies"
                )
    except Exception as e:
        logger.warning(f"[WINDUP] GNN inference: {e}")

    # 3. DuckPGQ stats + top IOC traversal
    top_nodes: list = []
    ioc_graph_stats: dict = {"nodes": 0, "edges": 0, "pgq_active": False}
    if hasattr(scheduler, "_ioc_graph") and scheduler._ioc_graph is not None:
        try:
            ioc_graph_stats = scheduler._ioc_graph.stats()
            top_nodes = scheduler._ioc_graph.get_top_nodes_by_degree(n=10)
            logger.info(
                f"[GRAPH] nodes={ioc_graph_stats['nodes']} "
                f"edges={ioc_graph_stats['edges']}"
            )
        except Exception as e:
            logger.warning(f"[WINDUP] DuckPGQ stats: {e}")

    # 4. ANE semantic dedup
    all_findings = getattr(scheduler, "_all_findings", [])
    deduped = all_findings
    try:
        from brain.ane_embedder import semantic_dedup_findings, get_ane_embedder
        if all_findings:
            deduped = await semantic_dedup_findings(all_findings)
            engine = "ANE-MiniLM" if get_ane_embedder() else "hash-fallback"
            logger.info(
                f"[ANE] {len(all_findings)} → {len(deduped)} unique "
                f"(engine={engine})"
            )
    except Exception as e:
        logger.warning(f"[WINDUP] ANE dedup: {e}")

    # 5. MoE synthesis engine selection + synthesis
    synthesis_meta: dict = {}
    synthesis_engine_used = "unknown"

    try:
        from brain.moe_router import route_synthesis

        memory_level = "nominal"
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            status = sample_uma_status()
            memory_level = getattr(status, "state", "nominal")
        except Exception:
            pass

        engine = route_synthesis(
            findings_count=len(deduped),
            has_gnn=bool(gnn_predictions),
            memory_pressure=memory_level,
            sprint_query=sprint_query,
        )
        synthesis_engine_used = engine
        scheduler._synthesis_engine = engine
        logger.info(f"[MOE] synthesis engine: {engine}")

        from brain.synthesis_runner import SynthesisRunner
        from brain.model_lifecycle import ModelLifecycle

        runner = SynthesisRunner(ModelLifecycle())
        if hasattr(scheduler, "_ioc_graph") and scheduler._ioc_graph is not None:
            runner.inject_graph(scheduler._ioc_graph)
        # Sprint 8VL: Inject lifecycle adapter — PREFERRED truth path for windup gate
        if hasattr(scheduler, "_lc_adapter") and scheduler._lc_adapter is not None:
            runner.inject_lifecycle_adapter(scheduler._lc_adapter)

        # Extract finding texts for synthesis
        finding_texts = []
        for f in (deduped or []):
            text = f.get("text") or f.get("snippet") or f.get("title") or str(f)
            finding_texts.append(text[:500])

        await runner.synthesize_findings(
            query=sprint_query,
            findings=[{"text": t, "ioc": f.get("ioc", ""), "source": f.get("source", "")}
                      for t, f in zip(finding_texts, deduped or [])],
            force_synthesis=True,
        )
        synthesis_meta = runner.last_synthesis_meta

        # Sprint F234: Bandit update already handled by synthesis_runner.synthesize_findings()
        # windup_engine.py is DORMANT — this path never executes in production.
        # See DSPY_OPTIMIZATION_MAP.md for architecture context.

        await runner.close()
    except Exception as e:
        logger.warning(f"[WINDUP] Synthesis: {e}")
        synthesis_engine_used = "failed"
        scheduler._synthesis_engine = "failed"

    # 6. Hypothesis enqueue (top-3)
    try:
        from brain.hypothesis_engine import HypothesisEngine

        # Extract finding strings from deduped findings
        finding_strings = []
        for f in (deduped or [])[:10]:
            text = f.get("text") or f.get("snippet") or f.get("title") or str(f)
            finding_strings.append(text[:500])

        hyp_engine = HypothesisEngine(None)
        hypotheses = hyp_engine.generate_sprint_hypotheses(
            findings=finding_strings,
            ioc_graph=getattr(scheduler, "_ioc_graph", None),
            max_hypotheses=3,
        )

        for h in (hypotheses or [])[:3]:
            h_text = h if isinstance(h, str) else str(h)
            scheduler.enqueue_pivot(
                ioc_value=h_text[:200],
                ioc_type="hypothesis",
                confidence=0.82,
                degree=1,
            )
            logger.info(f"[HYPOTHESIS] enqueued: {h_text[:80]}")
    except Exception as e:
        logger.warning(f"[WINDUP] Hypothesis enqueue: {e}")

    # 7. DuckPGQ checkpoint — persistuj data
    if hasattr(scheduler, "_ioc_graph") and scheduler._ioc_graph is not None:
        try:
            scheduler._ioc_graph.checkpoint()
        except Exception as e:
            logger.warning(f"[WINDUP] DuckPGQ checkpoint: {e}")

    # 8. Circuit breaker states
    cb_states = _safe_get_breaker_states()

    # 9. Scorecard
    rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    finding_count = getattr(scheduler, "_finding_count", 0)

    # Phase durations — NOTE: t_warmup_dur is actually the ACTIVE period duration
    # (t_active_end - t_warmup_end). run_windup() does not receive t_warmup_start,
    # so warmup cannot be measured directly. Label reflects what we compute.
    try:
        t_warmup_dur = round(t_active_end - t_warmup_end, 2) if t_warmup_end and t_active_end else 0.0
        t_active_dur = round(t_active_end - t_warmup_end, 2) if t_warmup_end and t_active_end else 0.0
        t_windup_dur = round(time.monotonic() - t_active_end, 2) if t_active_end else 0.0
    except Exception:
        t_warmup_dur = t_active_dur = t_windup_dur = 0.0

    scorecard = {
        "peak_rss_mb": round(rss / 1024 / 1024, 1),
        "accepted_findings_count": finding_count,
        "deduped_findings_count": len(deduped),
        "synthesis_engine_used": synthesis_engine_used,
        "dspy_prompt_version": synthesis_meta.get("dspy_prompt_version", 0),
        "bandit_arm_used": synthesis_meta.get("bandit_arm_used"),
        "bandit_arm_rewards": synthesis_meta.get("bandit_arm_rewards", {}),
        "gnn_predicted_links": len(gnn_predictions),
        "gnn_anomalies": len(anomalies),
        "ioc_graph": ioc_graph_stats,
        "top_graph_nodes": top_nodes[:5],
        "phase_duration_seconds": {
            "warmup": t_warmup_dur,
            "active": t_active_dur,
            "windup": t_windup_dur,
        },
        "cb_open_domains": cb_states,
        "ranked_parquet": ranked_path,
    }

    logger.info(f"[SCORECARD] {scorecard}")
    return scorecard
