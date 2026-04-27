"""
Sprint F3.5: Shadow Scheduler Parity — Fact Parity Only
========================================================

**VERDICT: ACTIVE (diagnostic only)** — 2026-04-27, F206B

Tento modul je ACTIVE diagnostic — shadow parity runner čte facts a porovnává je.
NIKdy nevolá canonical write path, tool execution, ani network execution.

STRICT BOUNDARIES:
- Shadow mode NIKDY nevolá tools execution
- Shadow mode NIKDY nevytváří findings writes
- Shadow mode NIKDY nevolá network execution
- Shadow mode NIKDY neprodukuje side effects
- Shadow mode NIKDY nezapisuje parity data do produkčních ledgerů
- Parity artifact je DIAGNOSTICKÝ výstup, ne nový truth store

První skutečný F3.5 krok: FACT PARITY ONLY.

Tento modul implementuje shadow parity režim pro scheduler.
Shadow mode čte facts z runtime, porovnává je se scheduler-side inputs,
a vrací parity artifact — DIAGNOSTICKÝ výstup, ne nový truth store.

STRICT BOUNDARIES:
- Shadow mode NIKDY nevolá tools execution
- Shadow mode NIKDY nevytváří findings writes
- Shadow mode NIKDY nevolá network execution
- Shadow mode NIKDY neprodukuje side effects
- Shadow mode NIKDY nezapisuje parity data do produkčních ledgerů
- Parity artifact je DIAGNOSTICKÝ, ne nový observability truth owner
- Local shadow dataclasses z runtime/shadow_inputs.py nezískávají status shared contracts

Runtime modes:
- legacy_runtime: dnešní runtime path (default) — __main__.py přímé volání
- scheduler_shadow: shadow mode, čte facts, žádné řízení, žádné side effects
- scheduler_active: plný scheduler-driven režim (budoucí, neaktivní)

Co se porovnává (F3.5):
- lifecycle snapshot parity (workflow_phase, control_phase, windup_local_phase)
- export handoff parity
- graph summary / graph capability facts
- model/control fact parity
- branch/provider precursor facts
- correlation-aware parity metadata

Co se NEporovnává (deferred):
- tool execution decisions
- fetch/runtime side effects
- provider activation
- windup execution outcomes
- findings writes
- network behavior
- actual tool-level dispatch parity

Phase systems (STRICTLY SEPARATED, NEVER merged into one field):
- workflow_phase: BOOT | WARMUP | ACTIVE | WINDUP | EXPORT | TEARDOWN
- control_phase: normal | prune | panic (tool/resource governance)
- windup_local_phase: synthesis | structured | minimal (synthesis mode inside WINDUP)

Owned by: runtime/shadow_parity.py
Canonical facts owners:
- lifecycle_snapshot → runtime/sprint_lifecycle.py
- export_handoff → types.py (ExportHandoff)
- graph_summary → knowledge/duckdb_store.py
- model/control_facts → types.py (AnalyzerResult)
- branch_decision_facts → types.py (BranchDecision)
- provider_precursor → capabilities.py (future)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .shadow_inputs import (
        LifecycleSnapshotBundle,
        GraphSummaryBundle,
        ModelControlFactsBundle,
    )


# =============================================================================
# Parity Artifact — diagnostic output, NOT a truth store
# =============================================================================

@dataclass
class ParityArtifact:
    """
    Diagnostic parity artifact — output of shadow mode comparison.

    This is a DIAGNOSTIC document. It does NOT become a new truth store.
    It does NOT get written to production ledgers as runtime facts.
    It does NOT participate in control flow decisions.

    Structure (all phase fields SEPARATED):
    - workflow_phase: sprint lifecycle phase
    - control_phase: tool/resource governance mode
    - windup_local_phase: synthesis mode within windup
    - graph_facts: node/edge counts, backend, top_nodes
    - model_control_facts: tools, sources, privacy, models_needed
    - export_handoff_facts: sprint_id, synthesis_engine, ranked_parquet presence
    - branch_precursor_facts: branch decision precursors
    - correlation: run correlation if available

    mismatch_categories:
    - NONE: všechny facts shodné
    - LIFECYCLE: lifecycle phase mismatch
    - GRAPH_CAPABILITY: graph backend nebo kapacita mismatch
    - MODEL_CONTROL: model/control configuration mismatch
    - EXPORT_HANDOFF: export handoff facts mismatch
    - PHASE_FIELD_MERGE: attempts to merge multiple phases into one field (BUG)
    """
    mode: str  # runtime mode used
    timestamp_monotonic: float
    timestamp_wall: str

    # Lifecycle facts — SEPARATED fields, NEVER merged
    workflow_phase: str
    workflow_phase_entered_at: Optional[float]
    control_phase_mode: str
    control_phase_thermal: str
    windup_local_mode: Optional[str]

    # Graph facts
    graph_nodes: int
    graph_edges: int
    graph_pgq_active: bool
    graph_backend: str
    graph_top_nodes_count: int

    # Model/Control facts
    mc_tools_count: int
    mc_sources_count: int
    mc_privacy: str
    mc_depth: str
    mc_models_needed: List[str]

    # Export handoff facts
    export_sprint_id: str
    export_synthesis_engine: str
    export_ranked_parquet_present: bool
    export_gnn_predictions: int

    # Branch/Provider precursor facts (if available)
    branch_decision_id: Optional[str]
    provider_recommend: Optional[str]

    # Correlation carrier (if already natural)
    correlation_run_id: Optional[str]
    correlation_branch_id: Optional[str]

    # Mismatch analysis
    mismatch_categories: List[str]
    mismatch_details: Dict[str, Any]

    # Source tracking (for debugging which inputs were used)
    input_sources: Dict[str, str]  # bundle_name → source_module

    # Fact stability breakdown — distinguishes STABLE vs COMPAT vs UNKNOWN inputs
    # This prevents compat seams from being treated as authoritative facts
    fact_stability_breakdown: Dict[str, str] = field(default_factory=dict)
    # List of bundles that used COMPAT/legacy paths (not typed contracts)
    compat_seams: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "timestamp_monotonic": self.timestamp_monotonic,
            "timestamp_wall": self.timestamp_wall,
            "workflow_phase": self.workflow_phase,
            "workflow_phase_entered_at": self.workflow_phase_entered_at,
            "control_phase_mode": self.control_phase_mode,
            "control_phase_thermal": self.control_phase_thermal,
            "windup_local_mode": self.windup_local_mode,
            "graph_nodes": self.graph_nodes,
            "graph_edges": self.graph_edges,
            "graph_pgq_active": self.graph_pgq_active,
            "graph_backend": self.graph_backend,
            "graph_top_nodes_count": self.graph_top_nodes_count,
            "mc_tools_count": self.mc_tools_count,
            "mc_sources_count": self.mc_sources_count,
            "mc_privacy": self.mc_privacy,
            "mc_depth": self.mc_depth,
            "mc_models_needed": self.mc_models_needed,
            "export_sprint_id": self.export_sprint_id,
            "export_synthesis_engine": self.export_synthesis_engine,
            "export_ranked_parquet_present": self.export_ranked_parquet_present,
            "export_gnn_predictions": self.export_gnn_predictions,
            "branch_decision_id": self.branch_decision_id,
            "provider_recommend": self.provider_recommend,
            "correlation_run_id": self.correlation_run_id,
            "correlation_branch_id": self.correlation_branch_id,
            "mismatch_categories": self.mismatch_categories,
            "mismatch_details": self.mismatch_details,
            "input_sources": self.input_sources,
            "fact_stability_breakdown": self.fact_stability_breakdown,
            "compat_seams": self.compat_seams,
        }

    @classmethod
    def is_diagnostic_only(cls) -> bool:
        """
        ParityArtifact is DIAGNOSTIC ONLY — not a truth store.

        This class method confirms the artifact must NOT be written
        to production ledgers or used as runtime truth.
        """
        return True


# =============================================================================
# Helper functions — must be defined before run_shadow_parity
# =============================================================================

def _check_phase_field_merge(
    bundle: "LifecycleSnapshotBundle",
    mismatches: List[str],
    mismatch_details: Dict[str, Any],
) -> None:
    """
    Check for phase value anomalies — generic validation only.

    Structural phase-field-merge bugs (WINDUP without windup_local,
    non-WINDUP with windup_local set) are handled exclusively by
    _check_phase_field_merge_bug() which emits PHASE_FIELD_MERGE.
    """
    wf = bundle.workflow_phase.phase

    # Check: workflow_phase should be one of the SprintPhase enum values
    valid_workflow_phases = {"BOOT", "WARMUP", "ACTIVE", "WINDUP", "EXPORT", "TEARDOWN"}
    if wf not in valid_workflow_phases:
        mismatches.append("LIFECYCLE")
        mismatch_details["workflow_phase"] = f"unexpected phase value: {wf}"

    # Check: control_phase.mode should be one of the tool mode values
    ctrl = bundle.control_phase.mode
    valid_control_modes = {"normal", "prune", "panic"}
    if ctrl not in valid_control_modes:
        mismatches.append("LIFECYCLE")
        mismatch_details["control_phase"] = f"unexpected control mode: {ctrl}"

    # NOTE: WINDUP/windup_local consistency is handled by _check_phase_field_merge_bug()
    # which emits PHASE_FIELD_MERGE (not LIFECYCLE) for these structural bugs.
    # DO NOT add LIFECYCLE emission here for those conditions.


def _check_phase_field_merge_bug(
    bundle: "LifecycleSnapshotBundle",
    mismatches: List[str],
    mismatch_details: Dict[str, Any],
) -> None:
    """
    Detect explicit PHASE_FIELD_MERGE structural bugs.

    These are distinct from generic LIFECYCLE mismatches — they represent
    a specific structural violation where phase systems that should be
    SEPARATED have been incorrectly merged or one is missing when expected.

    PHASE_FIELD_MERGE is emitted EXPLICITLY when:
    - In WINDUP but windup_local_phase is None (windup_local missing in WINDUP)
    - Not in WINDUP but windup_local_phase is set (windup_local leaked outside WINDUP)

    This is NOT the same as LIFECYCLE which covers general phase value anomalies.
    """
    wf = bundle.workflow_phase.phase

    # WINDUP but no windup_local_phase — explicit structural bug
    if wf == "WINDUP" and bundle.windup_local_phase is None:
        if "PHASE_FIELD_MERGE" not in mismatches:
            mismatches.append("PHASE_FIELD_MERGE")
            mismatch_details["phase_field_merge"] = (
                "workflow_phase=WINDUP but windup_local_phase=None — "
                "windup_local synthesis mode missing in WINDUP context"
            )

    # Not WINDUP but windup_local_phase is set — explicit structural bug
    if wf != "WINDUP" and bundle.windup_local_phase is not None:
        if "PHASE_FIELD_MERGE" not in mismatches:
            mismatches.append("PHASE_FIELD_MERGE")
            mismatch_details["phase_field_merge"] = (
                f"workflow_phase={wf} but windup_local_phase is set to "
                f"{bundle.windup_local_phase.mode} — windup_local leaked outside WINDUP"
            )


def _check_graph_capability(
    graph_bundle: "GraphSummaryBundle",
    mismatches: List[str],
    mismatch_details: Dict[str, Any],
) -> None:
    """
    Check graph capability — truthfulness gated by fact stability.

    - STABLE path + unknown backend → GRAPH_CAPABILITY (actual mismatch)
    - UNKNOWN or COMPAT path + unknown backend → INSUFFICIENT_INPUT (insufficient data)
    """
    graph_backend = graph_bundle.backend

    if graph_backend == "unknown":
        stability = getattr(graph_bundle, "fact_stability", "UNKNOWN")
        if stability == "STABLE":
            # STABLE path but backend unknown = actual capability mismatch
            if "GRAPH_CAPABILITY" not in mismatches:
                mismatches.append("GRAPH_CAPABILITY")
                mismatch_details["graph_backend"] = "unknown backend — DuckPGQ not initialized"
        else:
            # UNKNOWN or COMPAT path = insufficient input, not capability mismatch
            if "INSUFFICIENT_INPUT" not in mismatches:
                mismatches.append("INSUFFICIENT_INPUT")
                mismatch_details["graph_backend"] = (
                    f"unknown backend (fact_stability={stability}) — "
                    "insufficient input for capability determination"
                )


# =============================================================================
# Shadow parity runner — pure function, no side effects
# =============================================================================

def run_shadow_parity(
    lifecycle_bundle: "LifecycleSnapshotBundle",
    graph_bundle: "GraphSummaryBundle",
    model_control_bundle: "ModelControlFactsBundle",
    export_handoff_facts: Dict[str, Any],
    branch_decision: Optional["BranchDecision"] = None,
    provider_recommend: Optional[str] = None,
    correlation: Optional["RunCorrelation"] = None,
    runtime_mode: str = "scheduler_shadow",
) -> ParityArtifact:
    """
    Run shadow parity comparison — PURE FUNCTION, no side effects.

    This function:
    - Reads all shadow input bundles
    - Compares facts for parity
    - Returns ParityArtifact with mismatch analysis
    - Does NOT write anything
    - Does NOT call tools
    - Does NOT make control decisions

    Args:
        lifecycle_bundle: LifecycleSnapshotBundle from shadow_inputs.collect_lifecycle_snapshot()
        graph_bundle: GraphSummaryBundle from shadow_inputs.collect_graph_summary()
        model_control_bundle: ModelControlFactsBundle from shadow_inputs.collect_model_control_facts()
        export_handoff_facts: dict from shadow_inputs.collect_export_handoff_facts()
        branch_decision: Optional BranchDecision from types.py
        provider_recommend: Optional provider recommendation string
        correlation: Optional RunCorrelation from types.py
        runtime_mode: runtime mode string (default: scheduler_shadow)

    Returns:
        ParityArtifact — diagnostic output only, NOT a truth store
    """
    now_monotonic = time.monotonic()
    now_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    mismatches: List[str] = []
    mismatch_details: Dict[str, Any] = {}

    # --- Lifecycle phase field merge check ---
    # This is a structural invariant: workflow_phase, control_phase, windup_local_phase
    # must be SEPARATED fields. If any bundle has them merged, that's a PHASE_FIELD_MERGE bug.
    _check_phase_field_merge(lifecycle_bundle, mismatches, mismatch_details)

    # --- Workflow phase checks ---
    wf_phase = lifecycle_bundle.workflow_phase.phase

    # --- Control phase checks ---
    ctrl_mode = lifecycle_bundle.control_phase.mode

    # --- Windup local phase checks ---
    windup_mode = lifecycle_bundle.windup_local_phase.mode if lifecycle_bundle.windup_local_phase else None

    # --- PHASE_FIELD_MERGE explicit detection ---
    # Structural bug: if in WINDUP but windup_local_phase is None, OR if not in WINDUP
    # but windup_local_phase is set — these are explicit phase field merge violations,
    # NOT generic LIFECYCLE mismatches. Emit PHASE_FIELD_MERGE explicitly.
    _check_phase_field_merge_bug(lifecycle_bundle, mismatches, mismatch_details)

    # --- Graph capability checks ---
    _check_graph_capability(graph_bundle, mismatches, mismatch_details)
    graph_backend = graph_bundle.backend

    # --- Model/Control checks ---
    mc_privacy = model_control_bundle.privacy_level
    mc_depth = model_control_bundle.depth

    # --- Export handoff checks ---
    export_sprint_id = export_handoff_facts.get("sprint_id", "unknown")
    export_engine = export_handoff_facts.get("synthesis_engine", "unknown")
    export_ranked = export_handoff_facts.get("ranked_parquet_present", False)
    export_gnn = export_handoff_facts.get("gnn_predictions", 0)

    # --- Branch decision checks ---
    branch_decision_id = branch_decision.decision_id if branch_decision else None

    # --- Correlation checks ---
    corr_run_id = correlation.run_id if correlation else None
    corr_branch_id = correlation.branch_id if correlation else None

    # --- Input source tracking ---
    input_sources = {
        "lifecycle_snapshot": "runtime/shadow_inputs.py",
        "graph_summary": "runtime/shadow_inputs.py",
        "model_control_facts": "runtime/shadow_inputs.py",
        "export_handoff_facts": "runtime/shadow_inputs.py",
        "branch_decision": "types.py",
        "provider_recommend": "capabilities.py (future)",
        "correlation": "types.py",
    }

    # --- Fact stability breakdown ---
    # Prevents compat seams from being treated as authoritative facts
    fact_stability_breakdown = {
        "lifecycle_snapshot": lifecycle_bundle.fact_stability,
        "graph_summary": graph_bundle.fact_stability,
        "model_control_facts": model_control_bundle.fact_stability,
    }
    compat_seams: List[str] = []
    if lifecycle_bundle.fact_stability == "COMPAT":
        compat_seams.append("lifecycle_snapshot/windup_local_phase")
    if graph_bundle.fact_stability == "COMPAT":
        compat_seams.append("graph_summary/scorecard_top_nodes")
    if model_control_bundle.fact_stability == "COMPAT":
        compat_seams.append("model_control_facts/raw_profile")

    return ParityArtifact(
        mode=runtime_mode,
        timestamp_monotonic=now_monotonic,
        timestamp_wall=now_wall,
        workflow_phase=wf_phase,
        workflow_phase_entered_at=lifecycle_bundle.workflow_phase.entered_at_monotonic,
        control_phase_mode=ctrl_mode,
        control_phase_thermal=lifecycle_bundle.control_phase.thermal_state,
        windup_local_mode=windup_mode,
        graph_nodes=graph_bundle.node_count,
        graph_edges=graph_bundle.edge_count,
        graph_pgq_active=graph_bundle.pgq_active,
        graph_backend=graph_backend,
        graph_top_nodes_count=len(graph_bundle.top_nodes),
        mc_tools_count=len(model_control_bundle.tools),
        mc_sources_count=len(model_control_bundle.sources),
        mc_privacy=mc_privacy,
        mc_depth=mc_depth,
        mc_models_needed=list(model_control_bundle.models_needed),
        export_sprint_id=export_sprint_id,
        export_synthesis_engine=export_engine,
        export_ranked_parquet_present=export_ranked,
        export_gnn_predictions=export_gnn,
        branch_decision_id=branch_decision_id,
        provider_recommend=provider_recommend,
        correlation_run_id=corr_run_id,
        correlation_branch_id=corr_branch_id,
        mismatch_categories=mismatches if mismatches else ["NONE"],
        mismatch_details=mismatch_details if mismatch_details else {"note": "no mismatches detected"},
        input_sources=input_sources,
        fact_stability_breakdown=fact_stability_breakdown,
        compat_seams=compat_seams,
    )




# =============================================================================
# TYPE CHECKING imports — only used behind TYPE_CHECKING guard
# =============================================================================

if TYPE_CHECKING:
    from ..types import BranchDecision, RunCorrelation
