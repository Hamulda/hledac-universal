# Sprint F214Z: Export Formatter Class Hierarchy
# Problem: export_sprint fans out to 48 private helpers without class boundaries.
# Solution: ExportFormatter ABC → JSONFormatter (and future STIX/MD formatters).
# Benefits: locality, testability, extensibility.
"""
Export formatter class hierarchy.

Architecture:
  ExportFormatter (ABC)
    ├── JSONFormatter   # current export_sprint logic (~400 lines)
    ├── STIXFormatter  # future: STIX bundle export
    └── MarkdownFormatter  # future: sprint markdown report

Each formatter encapsulates its format's logic. The sprint_exporter module
acts as a thin dispatcher.

Sparrow Principle: Don't move code, just organize it. The 44 private helpers
stay in sprint_exporter.py — JSONFormatter.format() calls them directly.

**CONSTRAINT: No circular imports.**
`sprint_exporter.py` must NEVER import from `formatters.py`.
`sprint_exporter.py` is the stable foundation; `formatters.py` imports from it.
If `sprint_exporter` ever needs to import from `formatters`, the architecture breaks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
import itertools

if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff

import logging

logger = logging.getLogger(__name__)

# Re-export public API for backward compatibility
from .sprint_exporter import export_sprint, export_partial_sprint

__all__ = [
    "ExportFormatter",
    "JSONFormatter",
    "export_sprint",
    "export_partial_sprint",
    "render_investigation_packet_markdown",
]


class ExportFormatter(ABC):
    """
    Abstract base class for export formatters.

    Each formatter encapsulates its format's logic:
    - format() produces the export artifact
    - format_specific helpers live in sprint_exporter module (not here)

    The formatter is stateful during format() call but stateless across calls.
    """

    @abstractmethod
    async def format(
        self,
        store: Any,
        handoff: "ExportHandoff",
        sprint_id: str | None = None,
        enable_security_enrichment: bool = False,
        export_mode: str = "slim",
    ) -> dict:
        """
        Format and write sprint export artifact.

        Args:
            store: DuckDB store instance
            handoff: ExportHandoff with canonical truth surfaces
            sprint_id: override sprint ID
            enable_security_enrichment: enable PII sanitization
            export_mode: "slim" (M1-safe) or "full" (all enrichments)

        Returns:
            dict with artifact paths and metadata (same as original export_sprint return)
        """
        ...  # pragma: no cover


class JSONFormatter(ExportFormatter):
    """
    JSON formatter for sprint export.

    Encapsulates the logic formerly in export_sprint() lines 156-551.
    Thin: calls private helpers from sprint_exporter module directly.

    The 44 private helpers (seed generation, truth derivation, capability synthesis,
    operator brief assembly) live in sprint_exporter.py — they are NOT moved here.
    JSONFormatter.format() calls them by importing from sprint_exporter.
    """

    async def format(
        self,
        store: Any,
        handoff: "ExportHandoff",
        sprint_id: str | None = None,
        enable_security_enrichment: bool = False,
        export_mode: str = "slim",
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
        # Import and call the original export_sprint logic
        # This preserves all existing behavior while enabling class-based organization
        from .sprint_exporter import (
            _build_product_value_summary,
            _generate_next_sprint_seeds,
            _get_sprint_trend,
            _get_source_leaderboard,
            _get_correlation_from_handoff,
            _get_runtime_truth,
            _get_acquisition_truth,
            _reconcile_acquisition_terminality_from_source_outcomes,
            _get_feed_verdict,
            _get_public_verdict,
            _get_signal_path,
            _get_hypothesis_pack,
            _get_canonical_run_summary,
            _get_sprint_verdict,
            _get_synthesis_outcome_payload,
            _compute_research_depth,
            _build_capability_synthesis,
            _derive_run_truth_note,
            reconcile_terminal_truth,
            _derive_branch_truth,
            _derive_best_first_move,
            _derive_why_this_run_matters,
            _get_branch_value,
            _build_operator_brief,
            _build_sprint_summary,
            _make_serializable,
        )
        from hledac.universal.paths import get_sprint_json_report_path
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff
        import json

        # Sprint F186C: Tighten typed contract
        eh = ensure_export_handoff(handoff, default_sprint_id=sprint_id or "unknown")

        # Resolve sprint_id
        _sprint_id = eh.sprint_id if eh.sprint_id != "unknown" else (sprint_id or "unknown")
        report_path = get_sprint_json_report_path(_sprint_id)

        # Sprint 8VZ §C: F10 runtime boundary — sanitize_outbound
        boundary_content = _make_serializable(eh.scorecard)
        boundary_text = json.dumps(boundary_content, indent=2, default=str)

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
                    logger.warning("[EXPORT] sanitize_outbound returned no 'sanitized' key — using degraded structure")
                    degraded = {
                        "_sanitize_failure": True,
                        "sprint_id": _sprint_id,
                        "report": "sanitization_failed_degraded_export",
                    }
                    sanitized_str = json.dumps(degraded, default=str)
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
                sanitized_str = json.dumps(degraded, default=str)
            finally:
                if sec_coordinator is not None:
                    try:
                        await sec_coordinator.shutdown({})
                    except Exception:
                        pass
        else:
            sanitized_str = boundary_text

        # Sprint F234: Parse once — boundary_content stays as dict, sanitize works on JSON string.
        # No dict→str→dict→str roundtrip. All downstream ops on dict.
        try:
            sanitized_obj = json.loads(sanitized_str)
        except (json.JSONDecodeError, TypeError) as parse_err:
            logger.warning(
                "[EXPORT] sanitize boundary parse failed (size=%d): %s. Using boundary_content as degraded fallback.",
                len(sanitized_str), parse_err
            )
            sanitized_obj = boundary_content if isinstance(boundary_content, dict) else {}

        # Sprint F150I §2: Build product_value_summary
        pvs = _build_product_value_summary(store, eh, _sprint_id)

        # Sprint F229A: Reconcile terminal truth BEFORE capability_synthesis
        # Resolves pvs.accepted=0 vs runtime_truth.accepted=5 contradiction.
        # capability_runtime_truth is computed fresh here (not from eh yet) to avoid
        # forward-reference issues. It is re-derived below for report attachment.
        eh_scorecard = eh.scorecard if eh.scorecard else {}
        _pre_runtime_truth = _get_runtime_truth(eh)
        reconciled_pvs, _, truth_recon_applied, truth_recon_reason = reconcile_terminal_truth(
            pvs, eh_scorecard, _pre_runtime_truth
        )
        if truth_recon_applied:
            pvs = reconciled_pvs
            logger.info(f"[EXPORT] F229A truth reconciliation: {truth_recon_reason}")

        # Sprint F225F/F228D: capability_synthesis
        acquisition_report = _get_acquisition_truth(eh).get("acquisition_report")
        capability_runtime_truth = _get_runtime_truth(eh)
        capability_research_depth = _compute_research_depth(eh, pvs, None, None, None)
        capability_synthesis = _build_capability_synthesis(
            pvs, eh.analyst_brief, capability_runtime_truth, acquisition_report, capability_research_depth
        )

        # 1. JSON report — canonical path
        try:
            # Sprint F234: sanitized_obj already parsed from sanitized_str above.
            # No dict→str→dict→str roundtrip, no 5000-char truncation fallback.
            # boundary_content (dict) used as degraded fallback on parse failure.

            # Sprint F206S §3: Attach additive canonical truth surfaces
            if isinstance(sanitized_obj, dict):
                sanitized_obj["product_value_summary"] = pvs
                if eh.analyst_brief:
                    sanitized_obj["analyst_brief"] = _make_serializable(eh.analyst_brief)
                if eh.canonical_run_summary:
                    sanitized_obj["canonical_run_summary"] = eh.canonical_run_summary
                    if "timing_truth" in eh.canonical_run_summary:
                        sanitized_obj["timing_truth"] = eh.canonical_run_summary["timing_truth"]
                # Sprint F238E Phase C: Optional runtime_timing section (timer events)
                from hledac.universal.export.sprint_exporter import _extract_runtime_timing
                _rt = _extract_runtime_timing(eh)
                if _rt is not None:
                    sanitized_obj["runtime_timing"] = _rt
                    # Sprint F240B: runtime_diagnosis derived from telemetry
                    from hledac.universal.export.sprint_exporter import _compute_runtime_diagnosis
                    _diag = _compute_runtime_diagnosis(_rt.get("summary"))
                    if _diag:
                        sanitized_obj["runtime_diagnosis"] = _diag
                if eh.runtime_truth:
                    sanitized_obj["runtime_truth"] = eh.runtime_truth
                acq_truth = _get_acquisition_truth(eh)
                for _field, _value in acq_truth.items():
                    if _field not in sanitized_obj or not sanitized_obj[_field]:
                        sanitized_obj[_field] = _value
                sanitized_obj = _reconcile_acquisition_terminality_from_source_outcomes(sanitized_obj)
                sanitized_obj["capability_synthesis"] = capability_synthesis
                # Sprint F232A: investigation_packet — report enrichment via existing owners
                from hledac.universal.export.sprint_exporter import _build_investigation_packet
                sanitized_obj["investigation_packet"] = _build_investigation_packet(sanitized_obj)
                # Sprint F250C: provider yield diagnosis — surfaced alongside investigation_packet
                from hledac.universal.export.sprint_exporter import _build_provider_yield_diagnosis
                sanitized_obj["provider_yield_diagnosis"] = _build_provider_yield_diagnosis(sanitized_obj)
            elif isinstance(sanitized_obj, list):
                sanitized_obj = {"_truncated_content": sanitized_obj, "product_value_summary": pvs, "capability_synthesis": capability_synthesis}

            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(sanitized_obj, f, indent=2, default=str)
            logger.info(f"[EXPORT] JSON report → {report_path}")
        except Exception as e:
            logger.warning(f"[EXPORT] JSON write failed: {e}")
            report_path = None

        # 2. Seed tasky pro příští sprint
        top_nodes = eh.top_nodes if eh.top_nodes else []
        if not top_nodes and store is not None:
            try:
                if hasattr(store, "get_top_seed_nodes"):
                    top_nodes = store.get_top_seed_nodes(n=5)
            except Exception:
                pass

        branch_value = _get_branch_value(eh)
        sprint_trend = await _get_sprint_trend(store, last_n=3)
        investigation_packet = sanitized_obj.get("investigation_packet") if isinstance(sanitized_obj, dict) else None
        seeds_path = _generate_next_sprint_seeds(
            top_nodes, _sprint_id, report_path, pvs, branch_value, sprint_trend,
            export_mode=export_mode, capability_synthesis=capability_synthesis, analyst_brief=eh.analyst_brief,
            investigation_packet=investigation_packet,
        )

        # Sprint F150K: sprint_summary
        try:
            seeds_data = json.loads(seeds_path.read_text()) if seeds_path.exists() else {"seeds": []}
            seeds_count = len(seeds_data.get("seeds", [])) if isinstance(seeds_data, dict) else 0
        except Exception:
            seeds_count = 0
        sprint_summary = _build_sprint_summary(pvs, seeds_count) if pvs else None

        # Sprint F150L: operator brief
        source_leaderboard = await _get_source_leaderboard(store, days=7)
        correlation = _get_correlation_from_handoff(eh)

        # Sprint F150P: finish-layer truth fields
        runtime_truth = _get_runtime_truth(eh)
        feed_verdict = _get_feed_verdict(eh)
        public_verdict = _get_public_verdict(eh)
        signal_path = _get_signal_path(eh)
        hypothesis_pack = _get_hypothesis_pack(eh)
        canonical_run_summary = _get_canonical_run_summary(eh)
        sprint_verdict = _get_sprint_verdict(eh)
        synthesis_outcome_payload = _get_synthesis_outcome_payload(eh)

        run_truth_note = _derive_run_truth_note(runtime_truth, canonical_run_summary, sprint_verdict, pvs) if pvs else ""
        branch_truth = _derive_branch_truth(feed_verdict, public_verdict, branch_value)
        best_first_move = _derive_best_first_move(runtime_truth, signal_path, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""
        why_this_run_matters = _derive_why_this_run_matters(runtime_truth, signal_path, hypothesis_pack, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""

        operator_brief = _build_operator_brief(
            pvs, branch_value, sprint_trend, source_leaderboard, seeds_count, correlation,
            runtime_truth, feed_verdict, public_verdict, signal_path, hypothesis_pack,
            canonical_run_summary, sprint_verdict, synthesis_outcome_payload
        ) if pvs else None

        research_depth = _compute_research_depth(eh, pvs, signal_path, hypothesis_pack, correlation)

        # Sprint F193A: graph annotations
        findings_for_annotation = []
        try:
            if hasattr(store, "async_query_recent_findings"):
                raw_findings = await store.async_query_recent_findings(limit=50)
                findings_for_annotation = [dict(f) if hasattr(f, "keys") else f for f in raw_findings]
        except Exception:
            pass

        graph_context_annotations: list[dict] = []
        if findings_for_annotation and hasattr(store, "annotate_findings_with_graph_context"):
            try:
                graph_context_annotations = store.annotate_findings_with_graph_context(
                    findings_for_annotation, max_hops=2, max_annotations=50
                )
            except Exception:
                pass

        # Sprint F202A: envelope findings
        envelope_findings: list[dict] = []
        try:
            if hasattr(store, "async_get_findings_with_envelope"):
                envelope_findings = await store.async_get_findings_with_envelope(limit=20)
        except Exception:
            pass

        # Sprint F203A: sprint diff findings
        sprint_diff_findings: list[dict] = []
        try:
            if hasattr(store, "async_query_recent_findings"):
                all_findings = await store.async_query_recent_findings(limit=100)
                sprint_diff_findings = [
                    dict(f) if hasattr(f, "keys") else f
                    for f in all_findings
                    if (f.get("source_type") == "sprint_diff" if isinstance(f, dict) else False)
                ]
        except Exception:
            pass

        # Sprint F203C: kill chain findings
        kill_chain_findings: list[dict] = []
        try:
            if hasattr(store, "async_query_recent_findings"):
                all_findings = await store.async_query_recent_findings(limit=100)
                kill_chain_findings = [
                    dict(f) if hasattr(f, "keys") else f
                    for f in all_findings
                    if (f.get("source_type") == "killchain_tag" if isinstance(f, dict) else False)
                ]
        except Exception:
            pass

        # Sprint F203D: evidence chains (igraph, gated)
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
            except Exception:
                pass

        # ANE export dedup
        try:
            from hledac.universal.brain.ane_embedder import semantic_dedup_findings
            envelope_findings = await semantic_dedup_findings(envelope_findings, threshold=0.92)
            logger.debug("[ANE:export] %d findings after export dedup", len(envelope_findings))
        except Exception as _ane_err:
            logger.debug("[ANE:export] dedup skipped: %s", _ane_err)

        return {
            "report_json": str(report_path) if report_path else "",
            "seeds_json": str(seeds_path),
            "product_value_summary": pvs,
            "sprint_summary": sprint_summary,
            "operator_brief": operator_brief,
            "run_truth_note": run_truth_note,
            "branch_truth": branch_truth,
            "best_first_move": best_first_move,
            "why_this_run_matters": why_this_run_matters,
            "research_depth_metric": research_depth,
            "graph_enriched_findings": graph_context_annotations,
            "envelope_findings": envelope_findings,
            "sprint_diff_findings": sprint_diff_findings,
            "kill_chain_findings": kill_chain_findings,
            "evidence_chains": evidence_chains,
            "capability_synthesis": capability_synthesis,
            "capability_synthesis_generated": True,
            "capability_synthesis_skip_reason": None,
            "next_sprint_seeds_generated": True,
            "next_sprint_seeds_count": seeds_count,
            "next_sprint_seeds_path": str(seeds_path) if seeds_path else None,
            "investigation_packet": sanitized_obj.get("investigation_packet") if isinstance(sanitized_obj, dict) else None,
        }


def render_investigation_packet_markdown(packet: dict | None) -> str:
    """
    Sprint F232A: Render compact Investigation Packet markdown section.

    Deterministic. No LLM. No new report file type.
    Sections: Seed Context, Source Family Coverage, Corroboration, Gaps,
              Recommended Next Actions.

    Applied to existing export/formatters.py as Phase 3 integration point.
    No new markdown formatter file created.
    """
    if not packet:
        return ""

    lines: list[str] = []
    lines.append("## Investigation Packet")

    # ── Seed Context ───────────────────────────────────────────────────────
    sc = packet.get("seed_context") or {}
    lines.append("### Seed Context")
    available = sc.get("available", False)
    source = sc.get("source", "") or "unknown"
    lines.append(f"- **Available**: {available}")
    lines.append(f"- **Source**: {source}")
    domains = sc.get("domains", [])
    ips = sc.get("ips", [])
    urls = sc.get("urls", [])
    hashes = sc.get("hashes", [])
    cves = sc.get("cves", [])
    if domains:
        lines.append(f"- **Domains** ({len(domains)}): {', '.join(str(d) for d in domains[:10])}")
    if ips:
        lines.append(f"- **IPs** ({len(ips)}): {', '.join(str(ip) for ip in ips[:10])}")
    if urls:
        lines.append(f"- **URLs** ({len(urls)}): {', '.join(str(u) for u in urls[:5])}")
    if hashes:
        lines.append(f"- **Hashes** ({len(hashes)}): {', '.join(str(h) for h in hashes[:5])}")
    if cves:
        lines.append(f"- **CVEs** ({len(cves)}): {', '.join(str(c) for c in cves[:5])}")
    if not (domains or ips or urls or hashes or cves):
        lines.append("- _No seed context available_")
    lines.append("")

    # ── Source Family Coverage ─────────────────────────────────────────────
    sfs = packet.get("source_family_summary") or []
    lines.append("### Source Family Coverage")
    if sfs:
        for sf in sfs[:20]:
            fam = sf.get("family", "?")
            accepted = sf.get("accepted", 0)
            ts = sf.get("terminal_state", "") or "no_attempt"
            has_f = sf.get("has_findings", False)
            term_only = sf.get("terminal_only", False)
            status = "FINDINGS" if has_f else ("TERMINAL_ONLY" if term_only else "no_result")
            lines.append(f"- **{fam}**: {accepted} accepted, {ts} [{status}]")
    else:
        lines.append("- _No source family data_")
    lines.append("")

    # ── Corroboration ─────────────────────────────────────────────────────
    corr = packet.get("corroboration") or {}
    lines.append("### Corroboration")
    if corr:
        for ioc, score in itertools.islice(corr.items(), 20):
            lines.append(f"- {ioc}: {round(float(score), 4) if score is not None else 0.0}")
    else:
        lines.append("- _No corroboration scores_")
    lines.append("")

    # ── Gaps ──────────────────────────────────────────────────────────────
    gaps = packet.get("gaps") or []
    lines.append("### Gaps")
    if gaps:
        for gap in gaps[:20]:
            lines.append(f"- {gap}")
    else:
        lines.append("- _No significant gaps identified_")
    lines.append("")

    # ── Recommended Next Actions ─────────────────────────────────────────
    actions = packet.get("planner_actions") or []
    lines.append("### Recommended Next Actions")
    if actions:
        for act in actions[:10]:
            act_type = act.get("action", "?")
            target = act.get("target", "") or ""
            priority = act.get("priority", 0.0)
            lane = act.get("lane", "")
            reason = act.get("reason", "") or ""
            target_str = f" → {target}" if target else ""
            lines.append(f"- **{act_type}**{target_str} (p={round(priority, 3)}, lane={lane}) — {reason}")
    else:
        lines.append("- _No actions generated_")

    return "\n".join(lines)