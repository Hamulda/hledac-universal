"""
Sprint 8VI §A: WINDUP fáze — run_windup()

Extrahováno z __main__.py WINDUP sekce.
Dedup, GNN inference, ANE semantic dedup, MoE synthesis,
hypothesis enqueue, DuckPGQ checkpoint, scorecard.

======================================================================
DEPRECATED — Replaced by runtime/scheduler_v2/acquisition.py:_run_windup_sequence()
======================================================================

Tento modul je DEPRECATED od Sprint F350M-R (2026-08).
Aktivní implementace windup fáze je nyní v:
    runtime/scheduler_v2/acquisition.py::AcquisitionOrchestrator._run_windup_sequence()

run_windup() je zachována POUZE pro zpětnou kompatibilitu testů.
Nepoužívejte v novém kódu.

Kanonická produkční cesta:
    core.__main__:run_sprint() → SprintSchedulerV2.run()
    → AcquisitionOrchestrator.run() → _run_windup_sequence()

DORMANT PATH (pro back-compat testy):
    tests/test_e2e_dry_run.py:51 — stále volá run_windup() přímo
"""



import logging
import resource as _resource
import time
from typing import TYPE_CHECKING

from hledac.universal.brain.ane_embedder import semantic_dedup_findings
from hledac.universal.brain.gnn_predictor import get_anomaly_scores, predict_from_edge_list
from hledac.universal.core.mlx_embeddings import get_embedding_manager

if TYPE_CHECKING:
    from .sprint_scheduler import SprintScheduler

logger = logging.getLogger(__name__)


def _safe_get_breaker_states() -> dict:
    """Circuit breaker states — failsafe."""
    try:
        from hledac.universal.transport.circuit_breaker import get_all_breaker_states
        return get_all_breaker_states()
    except Exception:
        return {}


def _run_dedup_phase(scheduler: "SprintScheduler") -> str | None:
    """Parquet dedup + ranking. Returns ranked_path or None on error."""
    try:
        ranked_path = scheduler.deduplicate_and_rank_findings()
        logger.info(f"[WINDUP] Dedup ranked → {ranked_path}")
        return ranked_path
    except Exception as e:
        logger.warning(f"[WINDUP] Polars dedup failed: {e}")
        return None


async def _run_gnn_phase(graph: object) -> tuple[list, list, object]:
    """
    GNN inference over IOC graph.

    Returns (gnn_predictions, anomalies, graph). Graph is returned
    so callers can reuse it without re-fetching.
    """
    gnn_predictions: list = []
    anomalies: list = []
    if graph is None:
        logger.warning("[WINDUP] GNN: no graph")
        return gnn_predictions, anomalies, graph
    try:
        edge_list = getattr(graph, "export_edge_list", lambda: [])()
    except Exception as e:
        logger.warning(f"[WINDUP] GNN edge_list: {e}")
        return gnn_predictions, anomalies, graph
    if edge_list is None:
        logger.warning("[WINDUP] GNN: edge_list is None")
        return gnn_predictions, anomalies, graph
    edges = list(edge_list)
    if not edges:
        logger.info("[WINDUP] GNN: empty edge list")
        return gnn_predictions, anomalies, graph
    try:
        gnn_predictions = predict_from_edge_list(edges, top_k=10)
        anomalies = get_anomaly_scores(edges)
    except Exception as e:
        logger.warning(f"[WINDUP] GNN inference: {e}")
    logger.info(
        f"[GNN] {len(gnn_predictions)} predicted links, "
        f"{len(anomalies)} anomalies"
    )
    return gnn_predictions, anomalies, graph


async def _run_graph_stats_phase(graph: object) -> tuple[list, dict]:
    """DuckPGQ stats + top IOC traversal. Returns (top_nodes, ioc_graph_stats)."""
    top_nodes: list = []
    ioc_graph_stats: dict = {"nodes": 0, "edges": 0, "pgq_active": False}
    if graph is None:
        return top_nodes, ioc_graph_stats
    try:
        ioc_graph_stats = getattr(graph, "stats", lambda: {"nodes": 0, "edges": 0})()
        top_nodes = getattr(graph, "get_top_nodes_by_degree", lambda *_a, **_k: [])(n=10)
        logger.info(
            f"[GRAPH] nodes={ioc_graph_stats['nodes']} "
            f"edges={ioc_graph_stats['edges']}"
        )
    except Exception as e:
        logger.warning(f"[WINDUP] DuckPGQ stats: {e}")
    return top_nodes, ioc_graph_stats


async def _run_hypothesis_phase(
    scheduler: "SprintScheduler",
    deduped: list,
    graph: object,
) -> None:
    """Hypothesis enqueue (top-3). Void — best-effort only."""
    from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

    finding_strings = []
    for f in (deduped or [])[:10]:
        text = f.get("text") or f.get("snippet") or f.get("title") or str(f)
        finding_strings.append(text[:500])
    hyp_engine = HypothesisEngine(None)
    hypotheses = await hyp_engine.generate_sprint_hypotheses(
        findings=finding_strings,
        ioc_graph=graph,
        max_hypotheses=3,
    )
    if not hypotheses:
        return
    for h in hypotheses[:3]:
        h_text = h if isinstance(h, str) else str(h)
        scheduler.enqueue_pivot(
            ioc_value=h_text[:200],
            ioc_type="hypothesis",
            confidence=0.82,
            degree=1,
        )
        logger.info(f"[HYPOTHESIS] enqueued: {h_text[:80]}")


async def _run_synthesis_phase(
    scheduler: "SprintScheduler",
    sprint_query: str,
    deduped: list,
    gnn_predictions: list,
) -> tuple[dict, str]:
    """
    MoE synthesis engine selection + runner lifecycle.

    Runner.close() je vždy volán v finally bloku — i při výjimce.
    Vrací (synthesis_meta, engine_name).
    """
    synthesis_meta: dict = {}
    engine_name: str = "unknown"

    try:
        from hledac.universal.brain.moe_router import route_synthesis

        memory_level = "nominal"
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            status = sample_uma_status()
            memory_level = getattr(status, "state", "nominal")
        except Exception:  # noqa: BLE001
            pass

        engine = route_synthesis(
            findings_count=len(deduped),
            has_gnn=bool(gnn_predictions),
            memory_pressure=memory_level,
            sprint_query=sprint_query,
        )
        engine_name = engine
        scheduler._synthesis_engine = engine
        logger.info(f"[MOE] synthesis engine: {engine}")
    except Exception as e:
        logger.warning(f"[WINDUP] MoE route: {e}")
        scheduler._synthesis_engine = "failed"
        return {}, "failed"

    from hledac.universal.core.model_runtime import ModelLifecycle
    from hledac.universal.brain.synthesis_runner import SynthesisRunner

    runner: SynthesisRunner | None = None
    try:
        runner = SynthesisRunner(ModelLifecycle())
        runner.set_compression_threshold(4000)

        graph = getattr(scheduler, "get_graph", lambda: None)()
        if graph is not None:
            runner.inject_graph(graph)
        if hasattr(scheduler, "_lc_adapter") and scheduler._lc_adapter is not None:
            runner.inject_lifecycle_adapter(scheduler._lc_adapter)

        finding_texts = []
        for f in (deduped or []):
            text = f.get("text") or f.get("snippet") or f.get("title") or str(f)
            finding_texts.append(text[:500])

        await runner.synthesize_findings(
            query=sprint_query,
            findings=[{"text": t, "ioc": f.get("ioc", ""), "source": f.get("source", "")}
                      for t, f in zip(finding_texts, deduped or [], strict=False)],
            force_synthesis=True,
        )
        synthesis_meta = runner.last_synthesis_meta
    except Exception as e:
        logger.warning(f"[WINDUP] Synthesis: {e}")
        scheduler._synthesis_engine = "failed"
        return {}, "failed"
    finally:
        if runner is not None:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001 — best-effort
                pass

    return synthesis_meta, engine_name


async def run_windup(
    scheduler: SprintScheduler,
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
    # Sprint 1780830658 fix: early-exit on empty finding set.
    # With 0 findings, GNN/ANE/synthesis have no signal — skip straight
    # to scorecard with empty/zero defaults.
    pre_finding_count = int(getattr(scheduler, "_finding_count", 0) or 0)
    all_findings_raw = getattr(scheduler, "_all_findings", None)
    if pre_finding_count == 0 and not all_findings_raw:
        logger.info("[WINDUP] early-exit: 0 findings, skipping GNN/ANE/synthesis")
        try:
            rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        except Exception:
            rss = 0
        t_windup_dur = round(time.monotonic() - t_active_end, 2) if t_active_end else 0.0
        return {
            "peak_rss_mb": round(rss / 1024 / 1024, 1),
            "accepted_findings_count": 0,
            "deduped_findings_count": 0,
            "synthesis_engine_used": "skipped_zero_findings",
            "dspy_prompt_version": 0,
            "bandit_arm_used": None,
            "bandit_arm_rewards": {},
            "gnn_predicted_links": 0,
            "gnn_anomalies": 0,
            "ioc_graph": {"nodes": 0, "edges": 0, "pgq_active": False},
            "top_graph_nodes": [],
            "phase_duration_seconds": {
                "warmup": 0.0,
                "active": round(t_active_end - t_warmup_end, 2)
                    if t_warmup_end and t_active_end else 0.0,
                "windup": t_windup_dur,
            },
            "cb_open_domains": _safe_get_breaker_states(),
            "ranked_parquet": None,
        }

    # 1. Parquet dedup + ranking
    ranked_path = _run_dedup_phase(scheduler)

    # 2. GNN inference
    graph = getattr(scheduler, "get_graph", lambda: None)()
    gnn_predictions, anomalies, graph = await _run_gnn_phase(graph)

    # 3. DuckPGQ stats
    top_nodes, ioc_graph_stats = await _run_graph_stats_phase(graph)

    # 4. ANE semantic dedup — flat, no nesting
    all_findings = getattr(scheduler, "_all_findings", None) or []
    deduped = all_findings
    if all_findings:
        try:
            deduped = await semantic_dedup_findings(all_findings)
        except Exception as e:
            logger.warning(f"[WINDUP] ANE dedup: {e}")
            deduped = all_findings
        else:
            mgr = get_embedding_manager()
            eng = "MLX-ModernBERT" if (mgr and mgr._is_loaded) else "hash-fallback"
            logger.info(f"[ANE] {len(all_findings)} → {len(deduped)} unique (engine={eng})")

    # 5. MoE synthesis — extracted for flat complexity
    synthesis_meta, synthesis_engine_used = await _run_synthesis_phase(
        scheduler=scheduler,
        sprint_query=sprint_query,
        deduped=deduped,
        gnn_predictions=gnn_predictions,
    )

    # 6. Hypothesis enqueue (top-3) — reuse graph from step 2
    await _run_hypothesis_phase(scheduler, deduped, graph)

    # 7. DuckPGQ checkpoint — graph from step 2, reuse directly
    if graph is not None:
        try:
            getattr(graph, "checkpoint", lambda: None)()
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
