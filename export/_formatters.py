"""
export/_formatters.py

Sprint F232C: JSONFormatter class extracted from export/sprint_exporter.py.
Reduces complexity hotspot in sprint_exporter.py (605 lines -> delegates to helpers).

Thin: calls private helpers from sprint_exporter module directly.
The 44 private helpers (seed generation, truth derivation, capability synthesis,
operator brief assembly) live in sprint_exporter.py -- they are NOT moved here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class JSONFormatter:
    """
    JSON formatter for sprint export.

    Encapsulates the logic formerly in export_sprint() lines 156-551.
    Thin: calls private helpers from sprint_exporter module directly.

    The 44 private helpers (seed generation, truth derivation, capability synthesis,
    operator brief assembly) live in sprint_exporter.py  --  they are NOT moved here.
    """

    async def format(
        self,
        store: Any,
        handoff: Any,
        sprint_id: str | None = None,
        enable_security_enrichment: bool = False,
        export_mode: str = "slim",
        evidence_log: Any = None,
    ) -> dict:
        """
        Format sprint export as JSON artifact.

        This method contains the current export_sprint() logic (~400 lines).
        Delegates to private helpers in sprint_exporter.py for:
        - _build_product_value_summary
        - _generate_next_sprint_seeds
        - _get_* truth readers
        - _derive_* derived truth
        - _build_operator_brief
        - _compute_research_depth
        - etc.
        """
        import orjson

        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff
        from hledac.universal.export import sprint_exporter as _se
        from hledac.universal.paths import get_sprint_json_report_path
        from hledac.universal.export.components.pivot_builder import (
            _get_correlation_from_handoff,
        )

        # Sprint F186C: Tighten typed contract
        eh = ensure_export_handoff(handoff, default_sprint_id=sprint_id or "unknown")

        # Resolve sprint_id
        _sprint_id = eh.sprint_id if eh.sprint_id != "unknown" else (sprint_id or "unknown")
        report_path = get_sprint_json_report_path(_sprint_id)

        # Sprint 8VZ Section  C: F10 runtime boundary  --  sanitize_outbound
        boundary_content = _se._make_serializable(eh.scorecard)
        boundary_text = orjson.dumps(boundary_content, option=orjson.OPT_INDENT_2).decode()

        sanitized_str = boundary_text
        sec_coordinator = None
        if enable_security_enrichment and export_mode == "full":
            try:
                from hledac.universal.coordinators.security_coordinator import UniversalSecurityCoordinator
                sec_coordinator = UniversalSecurityCoordinator(max_concurrent=2)
                await sec_coordinator.initialize()
                gate_result = await sec_coordinator.sanitize_outbound(boundary_text, force_fallback=True)
                if "sanitized" in gate_result:
                    sanitized_str = gate_result["sanitized"]
                else:
                    logger.warning("[EXPORT] sanitize_outbound returned no 'sanitized' key  --  using degraded structure")
                    degraded = {
                        "_sanitize_failure": True,
                        "sprint_id": _sprint_id,
                        "report": "sanitization_failed_degraded_export",
                    }
                    sanitized_str = orjson.dumps(degraded).decode()
                if gate_result.get("pii_count"):
                    logger.info("[EXPORT] sanitize_outbound: pii_count=%s, risk=%s",
                                gate_result.get("pii_count"), gate_result.get("risk_level", "unknown"))
            except Exception as e:
                logger.warning("[EXPORT] sanitize_outbound failed (non-fatal): %s", e)
                degraded = {
                    "_sanitize_failure": True,
                    "sprint_id": _sprint_id,
                    "report": "sanitization_failed_degraded_export",
                }
                sanitized_str = orjson.dumps(degraded).decode()
            finally:
                if sec_coordinator is not None:
                    try:
                        await sec_coordinator.shutdown({})
                    except Exception:  # noqa: BLE001
                        pass
        else:
            sanitized_str = boundary_text

        # Sprint F234: Parse once
        try:
            sanitized_obj = orjson.loads(sanitized_str)
        except (orjson.JSONDecodeError, TypeError) as parse_err:
            logger.warning(
                "[EXPORT] sanitize boundary parse failed (size=%d): %s. Using boundary_content as degraded fallback.",
                len(sanitized_str), parse_err
            )
            sanitized_obj = boundary_content if isinstance(boundary_content, dict) else {}

        # Sprint F150I Section  2: Build product_value_summary
        pvs = _se._build_product_value_summary(store, eh, _sprint_id)

        # Sprint F229A: Reconcile terminal truth BEFORE capability_synthesis
        eh_scorecard = eh.scorecard if eh.scorecard else {}
        _cached_runtime_truth = _se._get_runtime_truth(eh)
        _cached_acq_truth = _se._get_acquisition_truth(eh)
        reconciled_pvs, _, truth_recon_applied, truth_recon_reason = _se.reconcile_terminal_truth(
            pvs, eh_scorecard, _cached_runtime_truth
        )
        if truth_recon_applied:
            pvs = reconciled_pvs
            logger.info(f"[EXPORT] F229A truth reconciliation: {truth_recon_reason}")

        # Sprint F225F/F228D: capability_synthesis
        acquisition_report = _cached_acq_truth.get("acquisition_report") if isinstance(_cached_acq_truth, dict) else None
        capability_synthesis = _se._build_capability_synthesis(eh, pvs, acquisition_report)

        # Sprint F150H: Branch value and trend
        branch_value = pvs.get("branch_value", {}) if pvs else {}
        sprint_trend = await _se._get_sprint_trend(store, last_n=5) if store else []

        # Sprint F150J: Seeds (async I/O heavy, move to tail)
        seeds_path = await _se._generate_next_sprint_seeds(
            store, pvs, eh.analyst_brief, eh.investigation_packet,
            export_mode=export_mode,
        )

        # Sprint F150K: sprint_summary
        try:
            seeds_data = orjson.loads(seeds_path.read_bytes()) if seeds_path.exists() else {"seeds": []}
            seeds_count = len(seeds_data.get("seeds", [])) if isinstance(seeds_data, dict) else 0
        except Exception:
            seeds_count = 0
        sprint_summary = _se._build_sprint_summary(pvs, seeds_count) if pvs else None

        # Sprint F150L: operator brief
        source_leaderboard = await _se._get_source_leaderboard(store, days=7)
        correlation = _get_correlation_from_handoff(eh)

        # Sprint F150P: finish-layer truth fields
        runtime_truth = _cached_runtime_truth
        feed_verdict = _se._get_feed_verdict(eh)
        public_verdict = _se._get_public_verdict(eh)
        signal_path = _se._get_signal_path(eh)
        hypothesis_pack = _se._get_hypothesis_pack(eh)
        canonical_run_summary = _se._get_canonical_run_summary(eh)
        sprint_verdict = _se._get_sprint_verdict(eh)
        synthesis_outcome_payload = _se._get_synthesis_outcome_payload(eh)

        run_truth_note = _se._derive_run_truth_note(runtime_truth, canonical_run_summary, sprint_verdict, pvs) if pvs else ""
        branch_truth = _se._derive_branch_truth(feed_verdict, public_verdict, branch_value)
        best_first_move = _se._derive_best_first_move(runtime_truth, signal_path, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""
        why_this_run_matters = _se._derive_why_this_run_matters(runtime_truth, signal_path, hypothesis_pack, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""

        operator_brief = _se._build_operator_brief(
            pvs, branch_value, sprint_trend, source_leaderboard, seeds_count, correlation,
            runtime_truth, feed_verdict, public_verdict, signal_path, hypothesis_pack,
            canonical_run_summary, sprint_verdict, synthesis_outcome_payload
        ) if pvs else None

        research_depth = _se._compute_research_depth(eh, pvs, signal_path, hypothesis_pack, correlation)

        # Findings annotation
        _EXPORT_FINDINGS_LIMIT: int = 200
        findings_for_annotation: list = []
        sprint_diff_findings: list[dict] = []
        kill_chain_findings: list[dict] = []
        forensic_findings: list[dict] = []
        _FORENSIC_ST: tuple[str, ...] = (
            "forensic_analysis",
            "steganography_detection",
            "digital_ghost_detection",
            "blockchain_forensics",
        )
        try:
            if hasattr(store, "async_query_recent_findings"):
                _all_findings = await store.async_query_recent_findings(limit=_EXPORT_FINDINGS_LIMIT)
                for _f in _all_findings:
                    _fd: dict | None = None
                    if isinstance(_f, dict):
                        _fd = _f
                    elif hasattr(_f, "keys"):
                        try:
                            _fd = dict(_f)
                        except Exception:
                            _fd = None
                    if not _fd:
                        continue
                    _st = _fd.get("source_type")
                    if len(findings_for_annotation) < 50:
                        findings_for_annotation.append(_fd)
                    if _st == "sprint_diff" and len(sprint_diff_findings) < 100:
                        sprint_diff_findings.append(_fd)
                    if _st == "killchain_tag" and len(kill_chain_findings) < 100:
                        kill_chain_findings.append(_fd)
                    if _st in _FORENSIC_ST and len(forensic_findings) < 200:
                        forensic_findings.append(_fd)
        except Exception:  # noqa: BLE001
            pass

        graph_context_annotations: list[dict] = []
        if findings_for_annotation and hasattr(store, "annotate_findings_with_graph_context"):
            try:
                graph_context_annotations = store.annotate_findings_with_graph_context(
                    findings_for_annotation, max_hops=2, max_annotations=50
                )
            except Exception:  # noqa: BLE001
                pass

        envelope_findings: list[dict] = []
        try:
            if hasattr(store, "async_get_findings_with_envelope"):
                envelope_findings = await store.async_get_findings_with_envelope(limit=20)
        except Exception:  # noqa: BLE001
            pass

        evidence_chains: list = []
        if export_mode == "full":
            try:
                from hledac.universal.knowledge.evidence_chain import get_all_chains
                all_chains = get_all_chains()
                all_chains.sort(key=lambda c: len(c.steps), reverse=True)
                evidence_chains = [
                    {
                        "root_finding_id": c.root_finding_id,
                        "steps": [
                            {
                                "step_type": s.step_type,
                                "input_ids": s.input_ids,
                                "output_id": s.output_id,
                                "confidence": s.confidence,
                                "reason": s.reason,
                            }
                            for s in c.steps
                        ],
                        "conclusion": c.conclusion,
                    }
                    for c in all_chains[:5]
                ]
            except Exception:  # noqa: BLE001
                pass

        try:
            from hledac.universal.brain.ane_embedder import semantic_dedup_findings
            envelope_findings = await semantic_dedup_findings(envelope_findings, threshold=0.92)
            logger.debug("[ANE:export] %d findings after export dedup", len(envelope_findings))
        except Exception as _ane_err:
            logger.debug("[ANE:export] dedup skipped: %s", _ane_err)

        # [META]-009: Build standalone investigator dashboard (opt-in)
        dashboard_html_path: str | None = None
        try:
            _ff = getattr(store, 'FeatureFlags', None)
            if _ff is not None:
                if isinstance(_ff, dict):
                    _dashboard_enabled = _ff.get("DASHBOARD", False)
                else:
                    _dashboard_enabled = getattr(_ff, "DASHBOARD", False)
                if _dashboard_enabled:
                    from hledac.universal.export.dashboard_builder import WASMDashboardBuilder
                    _ga = getattr(store, "_graph_attachment", None)
                    if _ga is not None:
                        try:
                            topology = _ga.export_graph_topology(max_nodes=500)
                            nodes_raw = topology.get("nodes", [])
                            edges_raw = topology.get("edges", [])
                            graph_topology = {"nodes": nodes_raw[:500], "edges": edges_raw[:1000]}
                        except Exception:
                            graph_topology = {"nodes": [], "edges": []}
                    else:
                        graph_topology = {"nodes": [], "edges": []}
                    try:
                        builder = WASMDashboardBuilder(sprint_id=_sprint_id)
                        dashboard_html_path = builder.build(
                            pvs=pvs,
                            graph_topology=graph_topology,
                            sprint_trend=sprint_trend,
                            source_leaderboard=source_leaderboard or [],
                            capability_synthesis=capability_synthesis,
                        )
                    except Exception as _dash_err:
                        logger.debug("[EXPORT] dashboard build skipped: %s", _dash_err)
        except Exception:  # noqa: BLE001
            pass

        # Assemble final export dict
        _result: dict[str, Any] = {
            "sprint_id": _sprint_id,
            "report_path": str(report_path),
            "timestamp": sanitized_obj.get("timestamp") if isinstance(sanitized_obj, dict) else None,
            "sanitized_scorecard": sanitized_obj,
            "product_value_summary": pvs,
            "capability_synthesis": capability_synthesis,
            "branch_value": branch_value,
            "sprint_trend": sprint_trend,
            "sprint_summary": sprint_summary,
            "operator_brief": operator_brief,
            "research_depth": research_depth,
            "run_truth_note": run_truth_note,
            "branch_truth": branch_truth,
            "best_first_move": best_first_move,
            "why_this_run_matters": why_this_run_matters,
            "source_leaderboard": source_leaderboard,
            "correlation": correlation,
            "seeds": str(seeds_path) if seeds_path else None,
            "envelope_findings": envelope_findings,
            "evidence_chains": evidence_chains,
            "investigation_packet": eh.investigation_packet,
            "analyst_brief": eh.analyst_brief,
            "graph_context_annotations": graph_context_annotations,
            "findings": {
                "total": len(findings_for_annotation),
                "sprint_diff": sprint_diff_findings,
                "killchain": kill_chain_findings,
                "forensic": forensic_findings,
            },
            "dashboard_html_path": dashboard_html_path,
            "export_mode": export_mode,
        }

        if evidence_log is not None:
            try:
                evidence_log.record("sprint_export", _sprint_id, _result)
            except Exception:  # noqa: BLE001
                pass

        return _result
