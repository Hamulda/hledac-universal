"""
Sprint F3.6: Shadow Pre-Decision Consumer Layer
================================================

**VERDICT: ACTIVE (diagnostic only)** — 2026-04-27, F206B

Tento modul je ACTIVE diagnostic — read-only consumer layer, která čte ParityArtifact
a skládá pre-decision summary. NIKDY nevolá canonical write path, tool execution,
ani network execution.

Shadow-only, read-only vrstva která čte ParityArtifact (z shadow_parity.py)
a skládá z něj pre-decision summary — interpretaci faktů pro scheduler decision,
aniž by sahala do core decision loopu.

STRICT BOUNDARIES:
- Shadow mode NIKDY nevolá tools execution
- Shadow mode NIKDY nevytváří findings writes
- Shadow mode NIKDY nevolá network execution
- Shadow mode NIKDY neprodukuje side effects
- Shadow mode NIKDY nezapisuje do produkčních ledgerů
- Pre-decision summary je DIAGNOSTICKÝ artifact, NENÍ nový truth store
- Žádné nové mutable fields na SprintScheduler
- Žádné background tasks
- Žádné nové caches

Co pre-decision consumer UMÍ:
- Čte ParityArtifact z run_shadow_parity()
- Skládá lifecycle interpretation summary
- Skládá graph capability summary
- Skládá export readiness summary
- Skládá model/control fact summary
- Skládá provider/branch precursor summary
- Generuje diff taxonomy (insufficient_input, lifecycle_mismatch, etc.)
- Identifikuje mismatch reasons
- Produkuje PreDecisionSummary artifact

Co pre-decision consumer NESMÍ:
- Nesahej do SprintScheduler.run() decision loopu
- Nepřidávej scheduler state
- Nevytvářej side effects
- Neaktivuj tools/providery
- Nezapisuj parity do produkčních ledgerů
- Nesluj phase vrstvy do jednoho pole

Owned by: runtime/shadow_pre_decision.py
Inputs: ParityArtifact (from shadow_parity.py)
Outputs: PreDecisionSummary (diagnostic artifact)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

# =============================================================================
# Diff Taxonomy — categorizace pre-decision mismatch reasons
# =============================================================================

class DiffTaxonomy(Enum):
    """
    Diff taxonomy pro pre-decision mismatch reasons.

    Každá kategorie reprezentuje distinct failure mode
    v pre-decision layer — NENÍ to scheduler decision samo.

    unlike ParityArtifact.mismatch_categories which are RAW mismatch flags,
    DiffTaxonomy je COMPOSED interpretation — bere raw mismatches
    a skládá z nich higher-level diagnosis.

    Každá kategorie má také `_stability` tag:
    - STABLE mismatch: problém v stable/typed path, vyžaduje pozornost
    - COMPAT mismatch: problém v compat/legacy path, může být expected
    - UNKNOWN mismatch: nedostatek informací, obvykle není blocker
    """
    # Základní stav
    NONE = auto()               # Všechny pre-decision vstupy jsou dostatečné

    # Input quality
    INSUFFICIENT_INPUT = auto()  # Fact bundles nemají dost informací pro pre-decision

    # Lifecycle mismatch — STABLE (from SprintLifecycleManager)
    LIFECYCLE_MISMATCH = auto()  # Lifecycle fáze je v nekonzistentním stavu

    # Phase layer conflict — STABLE (structural invariant check)
    PHASE_LAYER_CONFLICT = auto()  # Dvě nebo více phase vrstev si odporují

    # Graph capability ambiguity — STABILITY závisí na path (STABLE pokud duckpgq, UNKNOWN pokud unknown)
    GRAPH_CAPABILITY_AMBIGUITY = auto()  # Graph backend/neural capability je nejasný

    # Export handoff ambiguity — UNKNOWN stability (depends on handoff source)
    EXPORT_HANDOFF_AMBIGUITY = auto()    # Export handoff facts jsou nejasné/neúplné

    # Model/Control ambiguity — STABILITY závisí na path (STABLE pokud AnalyzerResult, COMPAT pokud raw_profile)
    MODEL_CONTROL_AMBIGUITY = auto()     # Model/control konfigurace je nejasná

    # Provider precursor ambiguity — UNKNOWN (provider_recommend is future)
    PROVIDER_PRECURSOR_AMBIGUITY = auto()  # Provider doporučení je nejasné

    # Branch precursor ambiguity — UNKNOWN (depends on branch_decision source)
    BRANCH_PRECURSOR_AMBIGUITY = auto()   # Branch rozhodnutí je nejasné

    # Compat seam warning — COMPAT only, not a real mismatch
    # Toto je FYZIOLOGICKÝ stav, ne problém. Označuje že jsme v compat path.
    COMPAT_SEAM_ACTIVE = auto()   # Compat seam je aktivní (windup_local_phase, scorecard, raw_profile)

    # Decision gate readiness — richer preview (Sprint 8VQ)
    DECISION_GATE_READY = auto()    # Všechny facts dostatečné, žádné blockers
    DECISION_GATE_BLOCKED = auto()  # Hard blockers present — cannot proceed
    DECISION_GATE_INSUFFICIENT = auto()  # Facts insufficient for decision
    DECISION_GATE_UNKNOWN = auto()   # Cannot determine readiness

    # Tool readiness — DIAGNOSTIC ONLY, no dispatch
    TOOL_READINESS_READY = auto()    # Tools available, can execute
    TOOL_READINESS_DEGRADED = auto()  # Some tools unavailable due to resource pressure
    TOOL_READINESS_PRUNED = auto()  # Tools heavily pruned (panic mode)
    TOOL_READINESS_UNKNOWN = auto() # Cannot determine tool readiness

    # Windup readiness — from existing fact bundles only
    WINDUP_READY = auto()           # Windup facts sufficient
    WINDUP_PARTIAL = auto()         # Some windup facts missing
    WINDUP_INSUFFICIENT = auto()    # Windup facts insufficient
    WINDUP_NOT_ACTIVE = auto()      # Not in WINDUP phase

    # Provider activation — deferred/unknown note only, NO simulation
    PROVIDER_DEFERRED = auto()       # Provider activation deferred to future phase
    PROVIDER_UNKNOWN = auto()        # Cannot determine provider readiness
    PROVIDER_NOT_READY = auto()      # Provider not ready
    PROVIDER_BLOCKED = auto()       # Provider blocked by hard constraint


# =============================================================================
# Pre-Decision Summary — diagnostic artifact, NOT a truth store
# =============================================================================

@dataclass(slots=True)
class LifecycleInterpretation:
    """
    Lifecycle interpretation summary — composed from ParityArtifact.

    Interpretuje workflow_phase, control_phase a windup_local_phase
    z hlediska scheduler pre-decision, aniž by zasahovalo do lifecycle.
    """
    workflow_phase: str
    workflow_phase_entered_at: float | None
    control_phase_mode: str
    control_phase_thermal: str
    windup_local_mode: str | None

    # Pre-decision interpretation
    is_active: bool          # workflow_phase == ACTIVE
    is_windup: bool          # workflow_phase == WINDUP
    is_export_ready: bool     # workflow_phase == EXPORT
    is_terminal: bool        # workflow_phase in (EXPORT, TEARDOWN)
    can_accept_work: bool    # workflow_phase in (BOOT, WARMUP, ACTIVE)
    should_prune: bool       # control_phase_mode in (prune, panic)
    synthesis_mode_known: bool  # windup_local_mode is known

    # Phase conflict detection
    phase_conflict: bool     # True pokud phase vrstvy jsou v konfliktu
    phase_conflict_reason: str | None  # Popis konfliktu pokud existuje


@dataclass(slots=True)
class GraphCapabilitySummary:
    """
    Graph capability summary — composed from ParityArtifact.

    Interpretuje graph facts z hlediska pre-decision.
    """
    backend: str
    nodes: int
    edges: int
    pgq_active: bool
    top_nodes_count: int

    # Pre-decision interpretation
    is_initialized: bool    # backend != "unknown"
    has_structured_data: bool  # nodes > 0 and edges > 0
    is_rich: bool           # top_nodes_count >= 5
    readiness: str           # "unknown" | "sparse" | "ready" | "rich"


@dataclass(slots=True)
class ExportReadinessSummary:
    """
    Export readiness summary — composed from ParityArtifact.

    Interpretuje export handoff facts z hlediska pre-decision.
    """
    sprint_id: str
    synthesis_engine: str
    ranked_parquet_present: bool
    gnn_predictions: int

    # Pre-decision interpretation
    is_ready: bool           # sprint_id known and engine known
    has_gnn_predictions: bool  # gnn_predictions > 0
    has_ranked_data: bool    # ranked_parquet_present
    readiness: str           # "unknown" | "partial" | "ready"


@dataclass(slots=True)
class ModelControlSummary:
    """
    Model/control fact summary — composed from ParityArtifact.

    Interpretuje model/control facts z hlediska pre-decision.
    """
    tools_count: int
    sources_count: int
    privacy: str
    depth: str
    models_needed: list[str]

    # Pre-decision interpretation
    has_tools: bool         # tools_count > 0
    has_sources: bool       # sources_count > 0
    is_high_quality: bool   # depth in (DEEP, STANDARD) and privacy != UNKNOWN
    readiness: str          # "unknown" | "partial" | "ready"


@dataclass(slots=True)
class PrecursorSummary:
    """
    Provider/Branch precursor summary — composed from ParityArtifact.

    Interpretuje provider a branch decision precursors z hlediska pre-decision.
    """
    branch_decision_id: str | None
    provider_recommend: str | None
    correlation_run_id: str | None
    correlation_branch_id: str | None

    # Pre-decision interpretation
    has_branch_decision: bool  # branch_decision_id is not None
    has_provider_recommend: bool  # provider_recommend is not None
    has_correlation: bool     # correlation_run_id is not None
    is_correlation_linked: bool  # correlation_run_id == branch_decision_id (if both set)

    # Readiness
    readiness: str  # "unknown" | "partial" | "ready"


@dataclass(slots=True)
class DecisionGateReadiness:
    """
    Decision gate readiness — explicit rozlišení pro scheduler decision gate.

    DIAGNOSTIC ONLY — tento artifact NESMÍ být použit pro skutečná
    scheduler rozhodnutí. Pouze pro diagnostický výstup.

    Rozlišuje:
    - DECISION_GATE_READY: všechny facts dostatečné, žádné blockers
    - DECISION_GATE_BLOCKED: hard blockers present — cannot proceed
    - DECISION_GATE_INSUFFICIENT: facts insufficient for decision
    - DECISION_GATE_UNKNOWN: cannot determine readiness
    """
    gate_status: str  # "ready" | "blocked" | "insufficient" | "unknown"
    blocker_count: int
    unknown_count: int
    compat_seam_count: int
    # Detail per category
    blocker_categories: list[str]  # Which categories are blocking
    unknown_categories: list[str]  # Which categories are unknown
    is_proceed_allowed: bool  # True iff gate_status == "ready"
    defer_to_provider: bool  # Provider activation deferred


@dataclass(slots=True)
class ToolReadinessPreview:
    """
    Tool readiness preview — DIAGNOSTIC ONLY, no dispatch, no execute_with_limits.

    Čte POUZE z existujícího ToolRegistry surface (list_tools, get_tool_cards).
    NESMÍ volat acquire(), load_model(), nebo jakékoli provider activation.

    Tento preview rozlišuje:
    - TOOL_READINESS_READY: tools available, can execute
    - TOOL_READINESS_DEGRADED: some tools unavailable due to resource pressure
    - TOOL_READINESS_PRUNED: tools heavily pruned (panic mode)
    - TOOL_READINESS_UNKNOWN: cannot determine tool readiness
    """
    readiness: str  # "ready" | "degraded" | "pruned" | "unknown"
    tool_count: int
    tool_names: list[str]
    has_network_tools: bool
    has_high_memory_tools: bool
    # Control phase impact
    control_mode: str  # "normal" | "prune" | "panic"
    pruned_tool_count: int  # Estimated pruned tools (based on control mode)
    # Resource-based assessment (read-only, no actual measurement)
    resource_constraint: str  # "none" | "memory" | "thermal" | "unknown"
    can_execute: bool  # True iff readiness in ("ready", "degraded")
    defer_reason: str | None  # Why deferred or unknown


@dataclass(slots=True)
class WindupReadinessPreview:
    """
    Windup readiness preview — from existing fact bundles, DIAGNOSTIC ONLY.

    Čte z LifecycleSnapshotBundle a ExportReadinessSummary.
    NESMÍ měnit ownership, NESMÍ aktivovat windup engine.

    Rozlišuje:
    - WINDUP_READY: windup facts sufficient
    - WINDUP_PARTIAL: some windup facts missing
    - WINDUP_INSUFFICIENT: windup facts insufficient
    - WINDUP_NOT_ACTIVE: not in WINDUP phase
    """
    readiness: str  # "ready" | "partial" | "insufficient" | "not_active"
    is_windup_phase: bool
    synthesis_mode: str | None  # "synthesis" | "structured" | "minimal" | None
    synthesis_engine: str
    has_export_data: bool  # ranked_parquet or gnn_predictions available
    export_data_quality: str  # "none" | "sparse" | "ready"
    defer_reason: str | None  # Why deferred or not ready


@dataclass(slots=True)
class AdvisoryGateSnapshot:
    """
    Advisory gate snapshot — computed at scheduler decision points (WINDUP entry).

    DIAGNOSTIC ONLY — this artifact NESMÍ ovlivnit dispatch ani source ordering.
    Pouze ukládá výsledek advisory gate evaluation pro diagnostiku/telemetry.

    Na rozdíl od PreDecisionSummary (celkový stav), AdvisoryGateSnapshot
    je scoped na konkrétní rozhodovací bod v scheduler loopu.

    Rozlišuje:
    - gate_outcome: "proceed" | "blocked" | "insufficient" | "unknown"
    - blocker_reasons: konkrétní důvody blocking
    - compat_seam_reasons: fyziologické compat seam důvody
    - unknown_reasons: co je neznámé
    - defer_to_provider: zda je provider activation deferred
    """
    gate_outcome: str  # "proceed" | "blocked" | "insufficient" | "unknown"
    gate_status: str  # "ready" | "blocked" | "insufficient" | "unknown"
    blocker_count: int
    unknown_count: int
    compat_seam_count: int
    blocker_reasons: list[str]
    unknown_reasons: list[str]
    compat_seam_reasons: list[str]
    defer_to_provider: bool
    gate_evaluated_at_monotonic: float
    gate_evaluated_at_wall: str
    # Reference na source PreDecisionSummary (not copied — for debugging only)
    source_pd_timestamp: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_outcome": self.gate_outcome,
            "gate_status": self.gate_status,
            "blocker_count": self.blocker_count,
            "unknown_count": self.unknown_count,
            "compat_seam_count": self.compat_seam_count,
            "blocker_reasons": self.blocker_reasons,
            "unknown_reasons": self.unknown_reasons,
            "compat_seam_reasons": self.compat_seam_reasons,
            "defer_to_provider": self.defer_to_provider,
            "gate_evaluated_at_monotonic": self.gate_evaluated_at_monotonic,
            "gate_evaluated_at_wall": self.gate_evaluated_at_wall,
            "source_pd_timestamp": self.source_pd_timestamp,
        }


@dataclass(slots=True)
class ProviderActivationNote:
    """
    Provider activation note — deferred/unknown only, NO simulation.

    DIAGNOSTIC ONLY. Tento note NESMÍ:
    - Simulovat load order providerů
    - Simulovat provider state machine
    - Vzniknout pseudo-authorita provider plane

    Rozlišuje:
    - PROVIDER_DEFERRED: activation deferred to future phase
    - PROVIDER_UNKNOWN: cannot determine provider readiness
    - PROVIDER_NOT_READY: provider not ready
    - PROVIDER_BLOCKED: blocked by hard constraint
    """
    status: str  # "deferred" | "unknown" | "not_ready" | "blocked"
    deferral_reason: str  # Why deferred
    has_recommendation: bool  # provider_recommend available
    recommendation: str | None  # Raw recommendation string
    next_phase_hint: str | None  # Hint about when activation might proceed
    # NO: load_order, provider_state, activation_sequence


@dataclass(slots=True)
class ProviderReadinessPreview:
    """
    Provider readiness preview — explicitní klasifikace provider readiness.

    DIAGNOSTIC ONLY — preview readiness bez activation.
    NESMÍ volat load_model(), acquire(), unload(), execute_with_limits().
    NESMÍ domýšlet chybějící facts jako ready.

    Rozlišuje TŘI různé věci (NESMÍ splývat):
    1. recommendation fact — co capabilities.py doporučuje
    2. readiness preview — diagnostická klasifikace readiness z facts
    3. actual activation — skutečné volání provider pool

    Readiness klasifikace (pouze pokud facts podporují):
    - ready: lifecycle ACTIVE/WINDUP + has_recommendation + normal/prune control
    - deferred: lifecycle not ready OR recommendation deferred to future phase
    - blocked: hard constraint (terminal phase, phase_conflict, panic mode)
    - unknown: facts insufficient to determine readiness
    - compat: lifecycle in COMPAT path (WARMUP), readiness indeterminate
    """
    # Source facts
    has_recommendation: bool
    recommendation: str | None

    # Readiness classification
    readiness: str  # "ready" | "deferred" | "blocked" | "unknown" | "compat"

    # Per-dimension facts
    lifecycle_ready: bool          # is_active or is_windup
    control_ready: bool          # control_mode in (normal, prune)
    thermal_safe: bool          # thermal_state in (nominal, fair, throttled) — NOT critical
    has_facts: bool              # has_recommendation AND has lifecycle facts

    # Blockers/detections
    blockers: list[str]          # Hard constraints blocking readiness
    unknowns: list[str]          # Facts insufficient to determine readiness

    # Hints
    next_phase_hint: str | None  # What would change readiness
    deferred_reasons: list[str]     # Why deferred (if readiness = deferred)

    # Sprint F3.13: Runtime facts — read-only runtime model state
    runtime_loaded: bool = False          # is a model currently loaded
    runtime_current_model: str | None = None  # which model is loaded (hermes/modernbert/gliner/None)
    runtime_initialized: bool = False       # is MLX/runtime initialized

    # No simulation fields (enforced by tests)
    # NO: load_order, provider_state, activation_sequence, actual_model_loaded


@dataclass(slots=True)
class PreDecisionSummary:
    """
    Pre-decision summary artifact — composed from ParityArtifact.

    Toto je DIAGNOSTICKÝ artifact. Nesmí být zapsán do produkčních ledgerů
    jako runtime facts. Nesmí participovat v control flow rozhodnutích.

    Struktura:
    - lifecycle: LifecycleInterpretation (composed from ParityArtifact)
    - graph: GraphCapabilitySummary (composed from ParityArtifact)
    - export: ExportReadinessSummary (composed from ParityArtifact)
    - model_control: ModelControlSummary (composed from ParityArtifact)
    - precursors: PrecursorSummary (composed from ParityArtifact)
    - diff_taxonomy: list[DiffTaxonomy] (composed from ParityArtifact.mismatch_categories)
    - blockers: list[str] — co brání pre-decision confidence
    - unknowns: list[str] — co je neznámé
    - mismatch_reasons: dict[str, str] — pro každý mismatch category důvod

    Phase separation: VŠECHNY phase fields jsou ODDĚLENÉ v LifecycleInterpretation.
    Žádné slité phase pole neexistuje.
    """
    # Source parity artifact reference
    parity_timestamp_monotonic: float
    parity_timestamp_wall: str
    runtime_mode: str

    # Composed interpretations
    lifecycle: LifecycleInterpretation
    graph: GraphCapabilitySummary
    export_readiness: ExportReadinessSummary
    model_control: ModelControlSummary
    precursors: PrecursorSummary

    # Diff taxonomy — composed from parity artifact mismatches
    diff_taxonomy: list[DiffTaxonomy]

    # Diagnostic metadata
    blockers: list[str]  # Co brání pre-decision confidence
    unknowns: list[str]  # Co je neznámé
    mismatch_reasons: dict[str, str]  # category → reason string
    # Compat seams — FYSIOLOGICAL, not blockers. Lists which bundles use legacy paths.
    compat_seams: list[str] = field(default_factory=list)

    # Sprint 8VQ: Richer readiness previews
    decision_gate: DecisionGateReadiness | None = None
    tool_readiness: ToolReadinessPreview | None = None
    windup_readiness: WindupReadinessPreview | None = None
    provider_note: ProviderActivationNote | None = None

    # Sprint F3.5-F3.6: Provider readiness preview — diagnostic only, no activation
    provider_readiness: ProviderReadinessPreview | None = None

    # Sprint F3.11: Dispatch parity preview — diagnostic only, no execute_with_limits
    dispatch_parity: DispatchReadinessPreview | None = None

    # Sprint F3.13: Provider runtime facts — read-only runtime model state
    runtime_facts: ProviderRuntimeFactsBundle | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parity_timestamp_monotonic": self.parity_timestamp_monotonic,
            "parity_timestamp_wall": self.parity_timestamp_wall,
            "runtime_mode": self.runtime_mode,
            "lifecycle": {
                "workflow_phase": self.lifecycle.workflow_phase,
                "workflow_phase_entered_at": self.lifecycle.workflow_phase_entered_at,
                "control_phase_mode": self.lifecycle.control_phase_mode,
                "control_phase_thermal": self.lifecycle.control_phase_thermal,
                "windup_local_mode": self.lifecycle.windup_local_mode,
                "is_active": self.lifecycle.is_active,
                "is_windup": self.lifecycle.is_windup,
                "is_export_ready": self.lifecycle.is_export_ready,
                "is_terminal": self.lifecycle.is_terminal,
                "can_accept_work": self.lifecycle.can_accept_work,
                "should_prune": self.lifecycle.should_prune,
                "synthesis_mode_known": self.lifecycle.synthesis_mode_known,
                "phase_conflict": self.lifecycle.phase_conflict,
                "phase_conflict_reason": self.lifecycle.phase_conflict_reason,
            },
            "graph": {
                "backend": self.graph.backend,
                "nodes": self.graph.nodes,
                "edges": self.graph.edges,
                "pgq_active": self.graph.pgq_active,
                "top_nodes_count": self.graph.top_nodes_count,
                "is_initialized": self.graph.is_initialized,
                "has_structured_data": self.graph.has_structured_data,
                "is_rich": self.graph.is_rich,
                "readiness": self.graph.readiness,
            },
            "export_readiness": {
                "sprint_id": self.export_readiness.sprint_id,
                "synthesis_engine": self.export_readiness.synthesis_engine,
                "ranked_parquet_present": self.export_readiness.ranked_parquet_present,
                "gnn_predictions": self.export_readiness.gnn_predictions,
                "is_ready": self.export_readiness.is_ready,
                "has_gnn_predictions": self.export_readiness.has_gnn_predictions,
                "has_ranked_data": self.export_readiness.has_ranked_data,
                "readiness": self.export_readiness.readiness,
            },
            "model_control": {
                "tools_count": self.model_control.tools_count,
                "sources_count": self.model_control.sources_count,
                "privacy": self.model_control.privacy,
                "depth": self.model_control.depth,
                "models_needed": self.model_control.models_needed,
                "has_tools": self.model_control.has_tools,
                "has_sources": self.model_control.has_sources,
                "is_high_quality": self.model_control.is_high_quality,
                "readiness": self.model_control.readiness,
            },
            "precursors": {
                "branch_decision_id": self.precursors.branch_decision_id,
                "provider_recommend": self.precursors.provider_recommend,
                "correlation_run_id": self.precursors.correlation_run_id,
                "correlation_branch_id": self.precursors.correlation_branch_id,
                "has_branch_decision": self.precursors.has_branch_decision,
                "has_provider_recommend": self.precursors.has_provider_recommend,
                "has_correlation": self.precursors.has_correlation,
                "is_correlation_linked": self.precursors.is_correlation_linked,
                "readiness": self.precursors.readiness,
            },
            "diff_taxonomy": [dt.name for dt in self.diff_taxonomy],
            "blockers": self.blockers,
            "unknowns": self.unknowns,
            "mismatch_reasons": self.mismatch_reasons,
            "compat_seams": self.compat_seams,
            # Sprint 8VQ: Richer readiness previews
            "decision_gate": {
                "gate_status": self.decision_gate.gate_status,
                "blocker_count": self.decision_gate.blocker_count,
                "unknown_count": self.decision_gate.unknown_count,
                "compat_seam_count": self.decision_gate.compat_seam_count,
                "blocker_categories": self.decision_gate.blocker_categories,
                "unknown_categories": self.decision_gate.unknown_categories,
                "is_proceed_allowed": self.decision_gate.is_proceed_allowed,
                "defer_to_provider": self.decision_gate.defer_to_provider,
            } if self.decision_gate else None,
            "tool_readiness": {
                "readiness": self.tool_readiness.readiness,
                "tool_count": self.tool_readiness.tool_count,
                "tool_names": self.tool_readiness.tool_names,
                "has_network_tools": self.tool_readiness.has_network_tools,
                "has_high_memory_tools": self.tool_readiness.has_high_memory_tools,
                "control_mode": self.tool_readiness.control_mode,
                "pruned_tool_count": self.tool_readiness.pruned_tool_count,
                "resource_constraint": self.tool_readiness.resource_constraint,
                "can_execute": self.tool_readiness.can_execute,
                "defer_reason": self.tool_readiness.defer_reason,
            } if self.tool_readiness else None,
            "windup_readiness": {
                "readiness": self.windup_readiness.readiness,
                "is_windup_phase": self.windup_readiness.is_windup_phase,
                "synthesis_mode": self.windup_readiness.synthesis_mode,
                "synthesis_engine": self.windup_readiness.synthesis_engine,
                "has_export_data": self.windup_readiness.has_export_data,
                "export_data_quality": self.windup_readiness.export_data_quality,
                "defer_reason": self.windup_readiness.defer_reason,
            } if self.windup_readiness else None,
            "provider_note": {
                "status": self.provider_note.status,
                "deferral_reason": self.provider_note.deferral_reason,
                "has_recommendation": self.provider_note.has_recommendation,
                "recommendation": self.provider_note.recommendation,
                "next_phase_hint": self.provider_note.next_phase_hint,
            } if self.provider_note else None,
            # Sprint F3.5-F3.6: Provider readiness preview
            "provider_readiness": {
                "readiness": self.provider_readiness.readiness,
                "has_recommendation": self.provider_readiness.has_recommendation,
                "recommendation": self.provider_readiness.recommendation,
                "lifecycle_ready": self.provider_readiness.lifecycle_ready,
                "control_ready": self.provider_readiness.control_ready,
                "thermal_safe": self.provider_readiness.thermal_safe,
                "has_facts": self.provider_readiness.has_facts,
                "blockers": self.provider_readiness.blockers,
                "unknowns": self.provider_readiness.unknowns,
                "next_phase_hint": self.provider_readiness.next_phase_hint,
                "deferred_reasons": self.provider_readiness.deferred_reasons,
            } if self.provider_readiness else None,
            # Sprint F3.11: Dispatch parity preview
            "dispatch_parity": self.dispatch_parity.to_dict() if self.dispatch_parity else None,
            # Sprint F3.13: Provider runtime facts — read-only runtime model state
            "runtime_facts": self.runtime_facts.to_dict() if self.runtime_facts else None,
        }

    @classmethod
    def is_diagnostic_only(cls) -> bool:
        """
        PreDecisionSummary is DIAGNOSTIC ONLY — not a truth store.

        This class method confirms the artifact must NOT be written
        to production ledgers or used as runtime truth.
        Must NOT participate in control flow decisions.
        """
        return True


# =============================================================================
# Pre-Decision Composer — pure function, no side effects
# =============================================================================

def compose_pre_decision(
    parity_artifact: ParityArtifact,
    runtime_facts: ProviderRuntimeFactsBundle | None = None,
) -> PreDecisionSummary:
    """
    Sestaví PreDecisionSummary z ParityArtifact.

    Toto je PURE FUNCTION — žádné side effects, žádné I/O, žádné network.

    Args:
        parity_artifact: ParityArtifact z run_shadow_parity()
        runtime_facts: ProviderRuntimeFactsBundle | None — read-only runtime facts
            about current model state (from ModelManager.get_current_model())

    Returns:
        PreDecisionSummary — composed pre-decision artifact
    """
    # --- Lifecycle Interpretation ---
    lc = _compose_lifecycle_interpretation(parity_artifact)

    # --- Graph Capability Summary ---
    gr = _compose_graph_capability_summary(parity_artifact)

    # --- Export Readiness Summary ---
    er = _compose_export_readiness_summary(parity_artifact)

    # --- Model/Control Summary ---
    mc = _compose_model_control_summary(parity_artifact)

    # --- Precursor Summary ---
    pr = _compose_precursor_summary(parity_artifact)

    # --- Diff Taxonomy ---
    diffs = _compose_diff_taxonomy(parity_artifact, lc, gr, er, pr)

    # --- Blockers, Unknowns, Mismatch Reasons ---
    blockers, unknowns, mismatch_reasons = _compose_diagnostic_metadata(
        parity_artifact, lc, gr, er, mc, pr
    )

    # --- Sprint 8VQ: Decision Gate Readiness ---
    gate_readiness = _compose_decision_gate_readiness(
        blockers, unknowns, parity_artifact.compat_seams
    )

    # --- Sprint 8VQ: Tool Readiness Preview (read-only, no dispatch) ---
    tool_readiness = _compose_tool_readiness_preview(
        lc.control_phase_mode,
        gr,
    )

    # --- Sprint 8VQ: Windup Readiness Preview ---
    windup_readiness = _compose_windup_readiness_preview(
        lc, er
    )

    # --- Sprint 8VQ: Provider Activation Note (deferred/unknown only) ---
    provider_note = _compose_provider_activation_note(
        pr, lc
    )

    # --- Sprint F3.5-F3.6: Provider Readiness Preview (diagnostic only) ---
    # F360A FIX: pass pr (PrecursorSummary) to access has_provider_recommend
    # has_recommendation comes from pr.has_provider_recommend (provider_recommend fact)
    # has_facts comes from model_control.models_needed (model availability fact)
    # These are DISTINCT facts and must NOT be conflated
    provider_readiness = _compose_provider_readiness_preview(
        lc, mc, pr, runtime_facts
    )

    return PreDecisionSummary(
        parity_timestamp_monotonic=parity_artifact.timestamp_monotonic,
        parity_timestamp_wall=parity_artifact.timestamp_wall,
        runtime_mode=parity_artifact.mode,
        lifecycle=lc,
        graph=gr,
        export_readiness=er,
        model_control=mc,
        precursors=pr,
        diff_taxonomy=diffs,
        blockers=blockers,
        unknowns=unknowns,
        mismatch_reasons=mismatch_reasons,
        compat_seams=parity_artifact.compat_seams,
        # Sprint 8VQ: Richer readiness previews
        decision_gate=gate_readiness,
        tool_readiness=tool_readiness,
        windup_readiness=windup_readiness,
        provider_note=provider_note,
        # Sprint F3.5-F3.6: Provider readiness preview
        provider_readiness=provider_readiness,
        # Sprint F3.13: Provider runtime facts
        runtime_facts=runtime_facts,
        # F350A: dispatch_parity NOT populated here — requires task_candidates +
        # available_capabilities which are not in ParityArtifact. Consumers call
        # preview_dispatch_parity() separately and assign dispatch_parity field directly.
        dispatch_parity=None,
    )


def _compose_lifecycle_interpretation(
    artifact: ParityArtifact,
) -> LifecycleInterpretation:
    """Sestaví lifecycle interpretation z ParityArtifact."""
    wf = artifact.workflow_phase
    ctrl = artifact.control_phase_mode
    ctrl_thermal = artifact.control_phase_thermal
    windup = artifact.windup_local_mode

    # Phase state
    is_active = wf == "ACTIVE"
    is_windup = wf == "WINDUP"
    is_export_ready = wf == "EXPORT"
    is_terminal = wf in ("EXPORT", "TEARDOWN")
    can_accept_work = wf in ("BOOT", "WARMUP", "ACTIVE")

    # Control phase
    should_prune = ctrl in ("prune", "panic")

    # Windup local
    synthesis_mode_known = windup is not None

    # Phase conflict detection
    phase_conflict = False
    phase_conflict_reason: str | None = None

    # Konflikt: WINDUP bez windup_local_mode
    if is_windup and not synthesis_mode_known:
        phase_conflict = True
        phase_conflict_reason = "workflow_phase=WINDUP but windup_local_mode is None"

    # Konflikt: non-WINDUP s windup_local_mode
    if not is_windup and synthesis_mode_known:
        phase_conflict = True
        phase_conflict_reason = f"workflow_phase={wf} but windup_local_mode={windup}"

    return LifecycleInterpretation(
        workflow_phase=wf,
        workflow_phase_entered_at=artifact.workflow_phase_entered_at,
        control_phase_mode=ctrl,
        control_phase_thermal=ctrl_thermal,
        windup_local_mode=windup,
        is_active=is_active,
        is_windup=is_windup,
        is_export_ready=is_export_ready,
        is_terminal=is_terminal,
        can_accept_work=can_accept_work,
        should_prune=should_prune,
        synthesis_mode_known=synthesis_mode_known,
        phase_conflict=phase_conflict,
        phase_conflict_reason=phase_conflict_reason,
    )


def _compose_graph_capability_summary(
    artifact: ParityArtifact,
) -> GraphCapabilitySummary:
    """Sestaví graph capability summary z ParityArtifact."""
    backend = artifact.graph_backend
    nodes = artifact.graph_nodes
    edges = artifact.graph_edges
    pgq = artifact.graph_pgq_active
    top_n = artifact.graph_top_nodes_count

    is_initialized = backend != "unknown"
    has_structured_data = nodes > 0 and edges > 0
    is_rich = top_n >= 5

    if backend == "unknown":
        readiness = "unknown"
    elif not is_initialized:
        readiness = "unknown"
    elif nodes == 0 and edges == 0:
        readiness = "sparse"
    elif top_n >= 5:
        readiness = "rich"
    else:
        readiness = "ready"

    return GraphCapabilitySummary(
        backend=backend,
        nodes=nodes,
        edges=edges,
        pgq_active=pgq,
        top_nodes_count=top_n,
        is_initialized=is_initialized,
        has_structured_data=has_structured_data,
        is_rich=is_rich,
        readiness=readiness,
    )


def _compose_export_readiness_summary(
    artifact: ParityArtifact,
) -> ExportReadinessSummary:
    """Sestaví export readiness summary z ParityArtifact."""
    sprint_id = artifact.export_sprint_id
    engine = artifact.export_synthesis_engine
    ranked = artifact.export_ranked_parquet_present
    gnn = artifact.export_gnn_predictions

    is_ready = sprint_id != "unknown" and engine != "unknown"
    has_gnn = gnn > 0
    has_ranked = ranked

    if sprint_id == "unknown":
        readiness = "unknown"
    elif engine == "unknown":
        readiness = "partial"
    else:
        readiness = "ready"

    return ExportReadinessSummary(
        sprint_id=sprint_id,
        synthesis_engine=engine,
        ranked_parquet_present=ranked,
        gnn_predictions=gnn,
        is_ready=is_ready,
        has_gnn_predictions=has_gnn,
        has_ranked_data=has_ranked,
        readiness=readiness,
    )


def _compose_model_control_summary(
    artifact: ParityArtifact,
) -> ModelControlSummary:
    """Sestaví model/control summary z ParityArtifact."""
    tools = artifact.mc_tools_count
    sources = artifact.mc_sources_count
    privacy = artifact.mc_privacy
    depth = artifact.mc_depth
    models = artifact.mc_models_needed

    has_tools = tools > 0
    has_sources = sources > 0
    is_high_quality = depth in ("DEEP", "STANDARD") and privacy != "UNKNOWN"

    if not has_tools and not has_sources:
        readiness = "unknown"
    elif not has_tools or not has_sources:
        readiness = "partial"
    else:
        readiness = "ready"

    return ModelControlSummary(
        tools_count=tools,
        sources_count=sources,
        privacy=privacy,
        depth=depth,
        models_needed=models,
        has_tools=has_tools,
        has_sources=has_sources,
        is_high_quality=is_high_quality,
        readiness=readiness,
    )


def _compose_precursor_summary(
    artifact: ParityArtifact,
) -> PrecursorSummary:
    """Sestaví precursor summary z ParityArtifact."""
    branch_id = artifact.branch_decision_id
    provider = artifact.provider_recommend
    corr_run = artifact.correlation_run_id
    corr_branch = artifact.correlation_branch_id

    has_branch = branch_id is not None
    has_provider = provider is not None
    has_corr = corr_run is not None
    is_linked = has_branch and has_corr and (corr_branch == branch_id)

    if not has_branch and not has_provider:
        readiness = "unknown"
    elif not has_branch or not has_provider:
        readiness = "partial"
    else:
        readiness = "ready"

    return PrecursorSummary(
        branch_decision_id=branch_id,
        provider_recommend=provider,
        correlation_run_id=corr_run,
        correlation_branch_id=corr_branch,
        has_branch_decision=has_branch,
        has_provider_recommend=has_provider,
        has_correlation=has_corr,
        is_correlation_linked=is_linked,
        readiness=readiness,
    )


def _compose_diff_taxonomy(
    artifact: ParityArtifact,
    lc: LifecycleInterpretation,
    gr: GraphCapabilitySummary,
    er: ExportReadinessSummary,
    pr: PrecursorSummary,
) -> list[DiffTaxonomy]:
    """
    Sestaví diff taxonomy z ParityArtifact mismatch_categories
    a composed interpretations.

    Unlike ParityArtifact.mismatch_categories (raw flags),
    DiffTaxonomy je composed higher-level diagnosis.

    compat_seams from ParityArtifact are mapped to COMPAT_SEAM_ACTIVE.
    This is a FYSIOLOGICAL state, not a blocker — it indicates
    we are using legacy compat paths rather than typed contracts.
    """
    diffs: list[DiffTaxonomy] = []
    raw_mismatches = artifact.mismatch_categories or []

    # Map raw mismatches to DiffTaxonomy
    for mismatch in raw_mismatches:
        if mismatch == "NONE":
            diffs.append(DiffTaxonomy.NONE)
        elif mismatch == "LIFECYCLE":
            diffs.append(DiffTaxonomy.LIFECYCLE_MISMATCH)
        elif mismatch == "GRAPH_CAPABILITY":
            diffs.append(DiffTaxonomy.GRAPH_CAPABILITY_AMBIGUITY)
        elif mismatch == "MODEL_CONTROL":
            diffs.append(DiffTaxonomy.MODEL_CONTROL_AMBIGUITY)
        elif mismatch == "EXPORT_HANDOFF":
            diffs.append(DiffTaxonomy.EXPORT_HANDOFF_AMBIGUITY)
        elif mismatch == "PHASE_FIELD_MERGE":
            # PHASE_FIELD_MERGE is a BUG — elevated to PHASE_LAYER_CONFLICT
            diffs.append(DiffTaxonomy.PHASE_LAYER_CONFLICT)
        elif mismatch == "INSUFFICIENT_INPUT":
            diffs.append(DiffTaxonomy.INSUFFICIENT_INPUT)

    # Compose additional diffs from interpretations (not just raw mismatches)
    # Phase layer conflict detection
    if lc.phase_conflict and DiffTaxonomy.PHASE_LAYER_CONFLICT not in diffs:
        diffs.append(DiffTaxonomy.PHASE_LAYER_CONFLICT)

    # Insufficient input detection
    if gr.readiness == "unknown" and er.readiness == "unknown":
        if DiffTaxonomy.INSUFFICIENT_INPUT not in diffs:
            diffs.append(DiffTaxonomy.INSUFFICIENT_INPUT)

    # Provider precursor ambiguity
    if pr.readiness == "unknown" and DiffTaxonomy.PROVIDER_PRECURSOR_AMBIGUITY not in diffs:
        diffs.append(DiffTaxonomy.PROVIDER_PRECURSOR_AMBIGUITY)

    # Branch precursor ambiguity
    if pr.readiness == "unknown" and DiffTaxonomy.BRANCH_PRECURSOR_AMBIGUITY not in diffs:
        diffs.append(DiffTaxonomy.BRANCH_PRECURSOR_AMBIGUITY)

    # Compat seam detection — this is a FYSIOLOGICAL state, not a mismatch
    # It indicates we are using legacy compat paths instead of typed contracts
    # compat_seams are reported separately in PreDecisionSummary
    # and do NOT appear as blockers in the diff taxonomy

    # Deduplicate
    seen: set[DiffTaxonomy] = set()
    result: list[DiffTaxonomy] = []
    for d in diffs:
        if d not in seen:
            seen.add(d)
            result.append(d)

    # NONE only if nothing else
    if not result:
        result.append(DiffTaxonomy.NONE)

    return result


def _compose_diagnostic_metadata(
    artifact: ParityArtifact,
    lc: LifecycleInterpretation,
    gr: GraphCapabilitySummary,
    er: ExportReadinessSummary,
    mc: ModelControlSummary,
    pr: PrecursorSummary,
) -> tuple[list[str], list[str], dict[str, str]]:
    """
    Sestaví blockers, unknowns a mismatch_reasons z composed interpretations.

    Rule: UNKNOWN stability facts go to unknowns (not blockers).
    Only STABLE-phase-conflict facts produce blockers.
    COMPAT seams are physiological, not blockers.

    Returns:
        (blockers, unknowns, mismatch_reasons)
    """
    blockers: list[str] = []
    unknowns: list[str] = []
    mismatch_reasons: dict[str, str] = {}

    # Map raw mismatch details to reasons
    details = artifact.mismatch_details or {}
    for category, detail in details.items():
        if category == "note":
            continue
        mismatch_reasons[category] = str(detail)

    # Fact stability breakdown — determines whether unknown readiness is a blocker
    stability = artifact.fact_stability_breakdown or {}

    # Add interpretation-based blockers — ONLY for STABLE readiness failures
    # UNKNOWN readiness → goes to unknowns, not blockers (insufficient info)
    if not lc.can_accept_work and not lc.is_terminal:
        blockers.append(f"lifecycle not ready: workflow_phase={lc.workflow_phase}")

    # Graph: unknown backend from STABLE path is blocker; from UNKNOWN/COMPAT is unknown
    if gr.readiness == "unknown":
        if stability.get("graph_summary") == "STABLE":
            blockers.append("graph backend unknown — cannot determine graph capability")
        else:
            unknowns.append("graph backend unknown — DuckPGQ may not be initialized")

    # Export: unknown from UNKNOWN handoff source is unknown, not blocker
    if er.readiness == "unknown":
        unknowns.append("export handoff not ready: sprint_id or engine unknown")

    # Model/Control: unknown from STABLE path is blocker; from UNKNOWN/COMPAT is unknown
    if mc.readiness == "unknown":
        if stability.get("model_control_facts") == "STABLE":
            blockers.append("model/control facts unknown: no tools or sources configured")
        else:
            unknowns.append("model/control facts: using legacy compat path")

    # Phase conflict is ALWAYS a blocker (structural invariant violation)
    if lc.phase_conflict:
        blockers.append(f"phase layer conflict: {lc.phase_conflict_reason}")

    # Unknowns (things we don't know but would help) — non-blocking
    if pr.readiness == "unknown":
        unknowns.append("branch decision precursor: no branch_decision_id available")
        unknowns.append("provider recommendation precursor: no provider_recommend available")

    if gr.readiness == "sparse":
        unknowns.append("graph data sparse: low node/edge count for meaningful analysis")

    if not mc.is_high_quality:
        unknowns.append(f"model/control quality: privacy={mc.privacy}, depth={mc.depth}")

    return blockers, unknowns, mismatch_reasons


def _compose_decision_gate_readiness(
    blockers: list[str],
    unknowns: list[str],
    compat_seams: list[str],
) -> DecisionGateReadiness:
    """
    Sestaví DecisionGateReadiness z blockers/unknowns/compat_seams.

    DIAGNOSTIC ONLY — tento artifact NESMÍ být použit pro skutečná
    scheduler rozhodnutí.

    Rozlišuje:
    - gate_status = "ready": žádné blockers, může proceed
    - gate_status = "blocked": hard blockers present
    - gate_status = "insufficient": insufficient facts for decision
    - gate_status = "unknown": cannot determine readiness
    """
    blocker_count = len(blockers)
    unknown_count = len(unknowns)
    compat_seam_count = len(compat_seams)

    # Determine gate status
    if blocker_count > 0:
        gate_status = "blocked"
        is_proceed_allowed = False
    elif unknown_count > 2:
        # Too many unknowns — insufficient for decision
        gate_status = "insufficient"
        is_proceed_allowed = False
    elif unknown_count > 0:
        # Some unknowns but can still proceed with caution
        gate_status = "ready"  # Proceed allowed despite unknowns
        is_proceed_allowed = True
    else:
        gate_status = "ready"
        is_proceed_allowed = True

    # Provider deferral: if we have unknowns about providers, defer activation
    defer_to_provider = any("provider" in u.lower() for u in unknowns)

    # Categorize blockers
    blocker_categories = []
    for b in blockers:
        if "phase" in b.lower() or "lifecycle" in b.lower():
            blocker_categories.append("lifecycle")
        elif "graph" in b.lower():
            blocker_categories.append("graph")
        elif "model" in b.lower() or "tool" in b.lower():
            blocker_categories.append("model_control")
        elif "export" in b.lower():
            blocker_categories.append("export")
        else:
            blocker_categories.append("unknown")

    # Categorize unknowns
    unknown_categories = []
    for u in unknowns:
        if "provider" in u.lower():
            unknown_categories.append("provider")
        elif "branch" in u.lower():
            unknown_categories.append("branch")
        elif "graph" in u.lower():
            unknown_categories.append("graph")
        elif "export" in u.lower():
            unknown_categories.append("export")
        else:
            unknown_categories.append("general")

    return DecisionGateReadiness(
        gate_status=gate_status,
        blocker_count=blocker_count,
        unknown_count=unknown_count,
        compat_seam_count=compat_seam_count,
        blocker_categories=blocker_categories,
        unknown_categories=unknown_categories,
        is_proceed_allowed=is_proceed_allowed,
        defer_to_provider=defer_to_provider,
    )


def _compose_tool_readiness_preview(
    control_mode: str,
    graph: GraphCapabilitySummary,
) -> ToolReadinessPreview:
    """
    Sestaví ToolReadinessPreview z control_phase_mode a graph readiness.

    DIAGNOSTIC ONLY — čte pouze z existujících fact bundles.
    NESMÍ volat execute_with_limits() ani provider activation.

    Read-only resource assessment based on thermal/graph hints:
    - "none": nominal conditions
    - "memory": graph is rich (high memory consumer)
    - "thermal": thermal state indicates pressure
    """
    if control_mode == "panic":
        readiness = "pruned"
        pruned_tool_count = 3  # Estimated pruned tools in panic
        can_execute = False
        defer_reason = "panic mode: tools heavily pruned"
        resource_constraint = "memory" if graph.is_rich else "thermal"
    elif control_mode == "prune":
        readiness = "degraded"
        pruned_tool_count = 1
        can_execute = True
        defer_reason = None
        resource_constraint = "memory" if graph.is_rich else "none"
    else:
        readiness = "ready"
        pruned_tool_count = 0
        can_execute = True
        defer_reason = None
        resource_constraint = "memory" if graph.is_rich else "none"

    return ToolReadinessPreview(
        readiness=readiness,
        tool_count=0,  # Filled by consumer seam from ToolRegistry
        tool_names=[],  # Filled by consumer seam from ToolRegistry
        has_network_tools=False,  # Filled by consumer seam
        has_high_memory_tools=graph.is_rich,
        control_mode=control_mode,
        pruned_tool_count=pruned_tool_count,
        resource_constraint=resource_constraint,
        can_execute=can_execute,
        defer_reason=defer_reason,
    )


def _compose_windup_readiness_preview(
    lifecycle: LifecycleInterpretation,
    export: ExportReadinessSummary,
) -> WindupReadinessPreview:
    """
    Sestaví WindupReadinessPreview z LifecycleInterpretation a ExportReadinessSummary.

    DIAGNOSTIC ONLY — z existujících fact bundles.
    NESMÍ měnit ownership, NESMÍ aktivovat windup engine.
    """
    if not lifecycle.is_windup:
        return WindupReadinessPreview(
            readiness="not_active",
            is_windup_phase=False,
            synthesis_mode=None,
            synthesis_engine=export.synthesis_engine,
            has_export_data=export.has_ranked_data or export.has_gnn_predictions,
            export_data_quality=_assess_export_quality(export),
            defer_reason="not in WINDUP phase",
        )

    # In WINDUP — assess windup readiness
    synthesis_mode = lifecycle.windup_local_mode
    has_export_data = export.has_ranked_data or export.has_gnn_predictions
    export_quality = _assess_export_quality(export)

    if export_quality == "none":
        readiness = "insufficient"
        defer_reason = "no export data available for windup synthesis"
    elif export_quality == "sparse":
        readiness = "partial"
        defer_reason = "limited export data for windup synthesis"
    else:
        readiness = "ready"
        defer_reason = None

    return WindupReadinessPreview(
        readiness=readiness,
        is_windup_phase=True,
        synthesis_mode=synthesis_mode,
        synthesis_engine=export.synthesis_engine,
        has_export_data=has_export_data,
        export_data_quality=export_quality,
        defer_reason=defer_reason,
    )


def _assess_export_quality(export: ExportReadinessSummary) -> str:
    """Assess export data quality for windup synthesis."""
    if not export.has_ranked_data and not export.has_gnn_predictions:
        return "none"
    if export.has_ranked_data and export.gnn_predictions > 0:
        return "ready"
    return "sparse"


def _compose_provider_activation_note(
    precursors: PrecursorSummary,
    lifecycle: LifecycleInterpretation,
) -> ProviderActivationNote:
    """
    Sestaví ProviderActivationNote z PrecursorSummary a LifecycleInterpretation.

    DIAGNOSTIC ONLY — deferred/unknown only.
    NESMÍ simulovat load order, NESMÍ simulovat provider state machine.
    NESMÍ vytvořit pseudo-authoritu provider plane.

    Rozlišuje:
    - status = "deferred": activation deferred to future phase
    - status = "unknown": cannot determine provider readiness
    - status = "not_ready": provider not ready
    - status = "blocked": blocked by hard constraint
    """
    # Provider activation deferred in these cases:
    # 1. Not in ACTIVE phase yet
    # 2. No provider recommendation available
    # 3. Hard constraints (lifecycle not ready)

    if lifecycle.is_terminal:
        status = "blocked"
        deferral_reason = "lifecycle in terminal phase — sprint ending"
        next_phase_hint = None
    elif not lifecycle.is_active and not lifecycle.is_windup:
        status = "deferred"
        deferral_reason = f"lifecycle phase={lifecycle.workflow_phase} — not ACTIVE or WINDUP"
        next_phase_hint = "ACTIVATE phase required"
    elif lifecycle.should_prune:
        status = "deferred"
        deferral_reason = "resource pressure — control mode=prune/panic"
        next_phase_hint = "NORMAL control mode required"
    elif not precursors.has_provider_recommend:
        status = "unknown"
        deferral_reason = "no provider_recommend available in precursors"
        next_phase_hint = "capabilities.py provider recommendation required"
    elif lifecycle.phase_conflict:
        status = "blocked"
        deferral_reason = f"phase conflict: {lifecycle.phase_conflict_reason}"
        next_phase_hint = None
    else:
        # Provider could activate but we defer to future phase
        status = "deferred"
        deferral_reason = "provider activation deferred — decision gate not yet passed"
        next_phase_hint = "DECISION_GATE_READY required"

    return ProviderActivationNote(
        status=status,
        deferral_reason=deferral_reason,
        has_recommendation=precursors.has_provider_recommend,
        recommendation=precursors.provider_recommend,
        next_phase_hint=next_phase_hint,
    )


def _compose_provider_readiness_preview(
    lifecycle: LifecycleInterpretation,
    model_control: ModelControlSummary,
    precursor: PrecursorSummary,
    runtime_facts: ProviderRuntimeFactsBundle | None = None,
) -> ProviderReadinessPreview:
    """
    Sestaví ProviderReadinessPreview z LifecycleInterpretation, ModelControlSummary a PrecursorSummary.

    DIAGNOSTIC ONLY — readiness preview bez activation.
    NESMÍ volat load_model(), acquire(), unload(), execute_with_limits().
    NESMÍ domýšlet chybějící facts jako ready.

    Rozlišuje TŘI různé věci (NESMÍ splývat):
    1. recommendation fact — co capabilities.py doporučuje (z precursor.provider_recommend)
    2. readiness preview — diagnostická klasifikace readiness z facts
    3. actual activation — skutečné volání provider pool

    F360A FIX: has_recommendation a has_facts jsou DISTINCT facts:
    - has_recommendation: z precursor.has_provider_recommend (provider_recommend fact exists)
    - has_facts: z model_control.models_needed (model availability)

    Readiness klasifikace:
    - ready: lifecycle ACTIVE/WINDUP + has_recommendation=True + normal/prune control
    - deferred: lifecycle not ready OR no provider_recommend
    - blocked: hard constraint (terminal phase, phase_conflict, panic mode)
    - unknown: facts insufficient to determine readiness
    - compat: lifecycle in COMPAT path (WARMUP), readiness indeterminate
    """
    blockers: list[str] = []
    unknowns: list[str] = []
    deferred_reasons: list[str] = []

    # Per-dimension facts
    lifecycle_ready = lifecycle.is_active or lifecycle.is_windup
    control_ready = lifecycle.control_phase_mode in ("normal", "prune")
    thermal_safe = lifecycle.control_phase_thermal != "critical"

    # F360A FIX: SEPARATE the two distinct facts
    # has_recommendation: provider_recommend fact exists (from precursor)
    has_recommendation_fact = precursor.has_provider_recommend and precursor.provider_recommend is not None
    # has_facts: model availability from analyzer (from model_control.models_needed)
    has_facts = model_control.models_needed is not None and len(model_control.models_needed) > 0

    # Sprint F3.13: Runtime facts — read-only runtime model state
    runtime_loaded = runtime_facts.is_loaded if runtime_facts else False
    runtime_current_model = runtime_facts.current_model if runtime_facts else None
    runtime_initialized = runtime_facts.initialized if runtime_facts else False

    # Blocked: hard constraints
    if lifecycle.is_terminal:
        blockers.append(f"lifecycle in terminal phase={lifecycle.workflow_phase}")
        return ProviderReadinessPreview(
            has_recommendation=has_recommendation_fact,
            recommendation=precursor.provider_recommend if has_recommendation_fact else None,
            readiness="blocked",
            lifecycle_ready=lifecycle_ready,
            control_ready=control_ready,
            thermal_safe=thermal_safe,
            has_facts=has_facts,
            blockers=blockers,
            unknowns=[],
            next_phase_hint=None,
            deferred_reasons=[],
            runtime_loaded=runtime_loaded,
            runtime_current_model=runtime_current_model,
            runtime_initialized=runtime_initialized,
        )

    if lifecycle.phase_conflict:
        blockers.append(f"phase_conflict: {lifecycle.phase_conflict_reason}")
        return ProviderReadinessPreview(
            has_recommendation=has_recommendation_fact,
            recommendation=precursor.provider_recommend if has_recommendation_fact else None,
            readiness="blocked",
            lifecycle_ready=lifecycle_ready,
            control_ready=control_ready,
            thermal_safe=thermal_safe,
            has_facts=has_facts,
            blockers=blockers,
            unknowns=[],
            next_phase_hint=None,
            deferred_reasons=[],
            runtime_loaded=runtime_loaded,
            runtime_current_model=runtime_current_model,
            runtime_initialized=runtime_initialized,
        )

    if lifecycle.control_phase_mode == "panic":
        blockers.append("control_mode=panic — provider activation blocked")
        return ProviderReadinessPreview(
            has_recommendation=has_recommendation_fact,
            recommendation=precursor.provider_recommend if has_recommendation_fact else None,
            readiness="blocked",
            lifecycle_ready=lifecycle_ready,
            control_ready=control_ready,
            thermal_safe=thermal_safe,
            has_facts=has_facts,
            blockers=blockers,
            unknowns=[],
            next_phase_hint=None,
            deferred_reasons=[],
            runtime_loaded=runtime_loaded,
            runtime_current_model=runtime_current_model,
            runtime_initialized=runtime_initialized,
        )

    if not lifecycle_ready:
        # Lifecycle not active yet — deferred
        deferred_reasons.append(f"lifecycle phase={lifecycle.workflow_phase} not ACTIVE/WINDUP")
        unknowns.append("provider readiness deferred — lifecycle not in ACTIVE/WINDUP")

        # COMPAT path: WARMUP phase means we can't determine
        if lifecycle.workflow_phase == "WARMUP":
            return ProviderReadinessPreview(
                has_recommendation=has_recommendation_fact,
                recommendation=precursor.provider_recommend if has_recommendation_fact else None,
                readiness="compat",
                lifecycle_ready=lifecycle_ready,
                control_ready=control_ready,
                thermal_safe=thermal_safe,
                has_facts=has_facts,
                blockers=[],
                unknowns=unknowns,
                next_phase_hint="ACTIVE phase required",
                deferred_reasons=deferred_reasons,
                runtime_loaded=runtime_loaded,
                runtime_current_model=runtime_current_model,
                runtime_initialized=runtime_initialized,
            )

        return ProviderReadinessPreview(
            has_recommendation=has_recommendation_fact,
            recommendation=precursor.provider_recommend if has_recommendation_fact else None,
            readiness="deferred",
            lifecycle_ready=lifecycle_ready,
            control_ready=control_ready,
            thermal_safe=thermal_safe,
            has_facts=has_facts,
            blockers=[],
            unknowns=unknowns,
            next_phase_hint="ACTIVE phase required",
            deferred_reasons=deferred_reasons,
            runtime_loaded=runtime_loaded,
            runtime_current_model=runtime_current_model,
            runtime_initialized=runtime_initialized,
        )

    # Lifecycle is ACTIVE or WINDUP — assess readiness dimensions
    if not control_ready:
        deferred_reasons.append(f"control_mode={lifecycle.control_phase_mode} not normal/prune")
        unknowns.append("provider readiness deferred — control mode not ready")

    if not thermal_safe:
        deferred_reasons.append(f"thermal_state={lifecycle.control_phase_thermal} is critical")
        unknowns.append("provider readiness deferred — thermal pressure critical")

    # No recommendation fact available
    if not has_facts:
        unknowns.append("provider recommendation fact not available — models_needed empty/unknown")

    # Classification: ready requires ALL of lifecycle_ready, control_ready, thermal_safe, has_facts
    if lifecycle_ready and control_ready and thermal_safe and has_facts:
        # Only mark as ready if we have actual recommendation fact
        if model_control.readiness in ("ready", "partial"):
            return ProviderReadinessPreview(
                has_recommendation=has_recommendation_fact,
                recommendation=precursor.provider_recommend if has_recommendation_fact else None,
                readiness="ready",
                lifecycle_ready=lifecycle_ready,
                control_ready=control_ready,
                thermal_safe=thermal_safe,
                has_facts=has_facts,
                blockers=[],
                unknowns=[],
                next_phase_hint=None,
                deferred_reasons=[],
                runtime_loaded=runtime_loaded,
                runtime_current_model=runtime_current_model,
                runtime_initialized=runtime_initialized,
            )
        else:
            # readiness="unknown" from model_control — insufficient facts
            unknowns.append("model_control.readiness=unknown — insufficient facts")
            return ProviderReadinessPreview(
                has_recommendation=has_recommendation_fact,
                recommendation=precursor.provider_recommend if has_recommendation_fact else None,
                readiness="unknown",
                lifecycle_ready=lifecycle_ready,
                control_ready=control_ready,
                thermal_safe=thermal_safe,
                has_facts=has_facts,
                blockers=[],
                unknowns=unknowns,
                next_phase_hint="model_control readiness required",
                deferred_reasons=deferred_reasons,
                runtime_loaded=runtime_loaded,
                runtime_current_model=runtime_current_model,
                runtime_initialized=runtime_initialized,
            )

    # Deferred or unknown
    if unknowns or deferred_reasons:
        return ProviderReadinessPreview(
            has_recommendation=has_recommendation_fact,
            recommendation=precursor.provider_recommend if has_recommendation_fact else None,
            readiness="deferred" if deferred_reasons else "unknown",
            lifecycle_ready=lifecycle_ready,
            control_ready=control_ready,
            thermal_safe=thermal_safe,
            has_facts=has_facts,
            blockers=[],
            unknowns=unknowns,
            next_phase_hint="normal control mode + non-critical thermal required",
            deferred_reasons=deferred_reasons,
            runtime_loaded=runtime_loaded,
            runtime_current_model=runtime_current_model,
            runtime_initialized=runtime_initialized,
        )

    # Fallback — should not reach here
    return ProviderReadinessPreview(
        has_recommendation=has_recommendation_fact,
        recommendation=precursor.provider_recommend if has_recommendation_fact else None,
        readiness="unknown",
        lifecycle_ready=lifecycle_ready,
        control_ready=control_ready,
        thermal_safe=thermal_safe,
        has_facts=has_facts,
        blockers=[],
        unknowns=["insufficient facts to classify provider readiness"],
        next_phase_hint="additional lifecycle/model facts required",
        deferred_reasons=[],
        runtime_loaded=runtime_loaded,
        runtime_current_model=runtime_current_model,
        runtime_initialized=runtime_initialized,
    )


def compose_advisory_gate(
    pd: PreDecisionSummary,
) -> AdvisoryGateSnapshot:
    """
    Sestaví AdvisoryGateSnapshot z PreDecisionSummary.

    Toto je PURE FUNCTION — žádné side effects, žádné I/O.

    Advisory gate snapshot je COMPUTED AT SCHEDULER DECISION POINTS (WINDUP entry).
    Na rozdíl od PreDecisionSummary (celkový stav), AdvisoryGateSnapshot je
    scoped na konkrétní rozhodovací bod v scheduler loopu.

    DIAGNOSTIC ONLY — NESMÍ ovlivnit dispatch ani source ordering.

    Rozlišuje:
    - gate_outcome = "proceed": žádné blockers, může proceed
    - gate_outcome = "blocked": hard blockers present
    - gate_outcome = "insufficient": insufficient facts
    - gate_outcome = "unknown": cannot determine
    """
    import time

    gate = pd.decision_gate
    now_mono = time.monotonic()
    now_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if gate is None:
        return AdvisoryGateSnapshot(
            gate_outcome="unknown",
            gate_status="unknown",
            blocker_count=0,
            unknown_count=len(pd.unknowns),
            compat_seam_count=len(pd.compat_seams),
            blocker_reasons=[],
            unknown_reasons=list(pd.unknowns),
            compat_seam_reasons=list(pd.compat_seams),
            defer_to_provider=False,
            gate_evaluated_at_monotonic=now_mono,
            gate_evaluated_at_wall=now_wall,
            source_pd_timestamp=pd.parity_timestamp_monotonic,
        )

    # Determine gate outcome (actionable vs non-actionable)
    if gate.gate_status == "blocked":
        gate_outcome = "blocked"
    elif gate.gate_status == "insufficient":
        gate_outcome = "insufficient"
    elif gate.gate_status == "unknown":
        gate_outcome = "unknown"
    elif gate.is_proceed_allowed:
        gate_outcome = "proceed"
    else:
        gate_outcome = "unknown"

    return AdvisoryGateSnapshot(
        gate_outcome=gate_outcome,
        gate_status=gate.gate_status,
        blocker_count=gate.blocker_count,
        unknown_count=gate.unknown_count,
        compat_seam_count=gate.compat_seam_count,
        blocker_reasons=list(pd.blockers) if gate.blocker_categories else list(pd.blockers),
        unknown_reasons=list(pd.unknowns),
        compat_seam_reasons=list(pd.compat_seams),
        defer_to_provider=gate.defer_to_provider,
        gate_evaluated_at_monotonic=now_mono,
        gate_evaluated_at_wall=now_wall,
        source_pd_timestamp=pd.parity_timestamp_monotonic,
    )


# =============================================================================
# F3.11: Dispatch Parity Preview
# =============================================================================

class DispatchTaxonomy(Enum):
    """
    Dispatch taxonomy pro scheduler-shadow dispatch parity preview.

    Rozlišuje mezi:
    - CANONICAL_TOOL_DISPATCH: task/tool má čistý ToolRegistry mapping
    - RUNTIME_ONLY_COMPAT_DISPATCH: task/type používá inline get_task_handler(),
      nemá canonical ToolRegistry mapping
    - DISPATCH_READY: všechny podmínky pro dispatch jsou splněny
    - DISPATCH_BLOCKED: capability missing nebo hard constraint
    - DISPATCH_PRUNED: control mode prune/panic
    - DISPATCH_UNKNOWN: nelze určit readiness
    """
    # Dispatch path classification
    CANONICAL_TOOL_DISPATCH = auto()   # Má ToolRegistry mapping, jde přes execute_with_limits
    RUNTIME_ONLY_COMPAT_DISPATCH = auto()  # Inline task handler, ne ToolRegistry

    # Readiness states (additive on path classification)
    DISPATCH_READY = auto()           # Všechny podmínky splněny
    DISPATCH_BLOCKED = auto()         # Capability missing nebo hard constraint
    DISPATCH_PRUNED = auto()          # Control mode prune/panic
    DISPATCH_UNKNOWN = auto()         # Nelze určit

    # Blocker reasons
    CAPABILITY_MISSING = auto()       # Tool requires capabilities not in available set
    CONTROL_MODE_PRUNE = auto()       # Control phase je prune nebo panic
    GRAPH_NOT_READY = auto()          # Graph není ready pro tuto operaci
    NO_TOOL_MAPPING = auto()           # Task type nemá ToolRegistry tool mapping
    RUNTIME_HANDLER_ONLY = auto()     # Používá get_task_handler(), ne execute_with_limits


@dataclass(slots=True)
class ToolCapabilityGap:
    """
    Capability gap pro jeden tool.
    """
    tool_name: str
    required_capabilities: set[str]
    available_capabilities: set[str]
    missing_capabilities: set[str]
    is_satisfied: bool
    is_network_tool: bool
    is_high_memory: bool


@dataclass(slots=True)
class ExecutionContextReadiness:
    """
    Execution context readiness — DIAGNOSTIC ONLY.

    Separované readiness pro tři dimenze:
    1. Capability readiness — zda jsou available_capabilities dostatečné
    2. Correlation readiness — zda scheduler má korelační klíče
    3. Audit readiness — zda je exec_logger dostupný

    Všechny tři dimenze musí být "ready" pro canonical execute_with_limits call.
    """
    # Capability readiness
    capability_ready: bool
    capability_missing: list[str]  # seznam chybějících capabilities

    # Correlation readiness (scheduler-side)
    correlation_ready: bool
    run_id_present: bool
    branch_id_present: bool
    provider_id_present: bool
    action_id_present: bool
    correlation_note: str  # lidsky čitelný stav

    # Audit readiness
    audit_ready: bool  # exec_logger present v scheduler kontextu
    exec_logger_note: str  # lidsky čitelný stav

    # Canonical dispatch classification (čistá oddělenost)
    canonical_tool_dispatch: bool  # True pokud všichni kandidáti mají ToolRegistry mapping
    runtime_only_compat_dispatch: bool  # True pokud všichni kandidáti jsou runtime_only

    # Blokery pro canonical execute_with_limits call
    blocker_matrix: dict[str, str]  # tool_name → blocker reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_ready": self.capability_ready,
            "capability_missing": self.capability_missing,
            "correlation_ready": self.correlation_ready,
            "run_id_present": self.run_id_present,
            "branch_id_present": self.branch_id_present,
            "provider_id_present": self.provider_id_present,
            "action_id_present": self.action_id_present,
            "correlation_note": self.correlation_note,
            "audit_ready": self.audit_ready,
            "exec_logger_note": self.exec_logger_note,
            "canonical_tool_dispatch": self.canonical_tool_dispatch,
            "runtime_only_compat_dispatch": self.runtime_only_compat_dispatch,
            "blocker_matrix": self.blocker_matrix,
        }


@dataclass(slots=True)
class DispatchReadinessPreview:
    """
    Dispatch readiness preview — DIAGNOSTIC ONLY.

    Previewuje dispatch readiness pro sadu task/tool kandidátů
    bez volání execute_with_limits() nebo provider activation.

    Rozlišuje:
    - dispatch_ready: kandidát má čistý canonical path, capabilities satisfied
    - dispatch_blocked: kandidát má path ale capability gap
    - dispatch_pruned: kandidát je pruned control modem
    - dispatch_unknown: nelze určit
    - runtime_only_compat_dispatch: kandidát nemá ToolRegistry mapping,
      používá inline get_task_handler()

    Nikdy nevola:
    - execute_with_limits()
    - acquire() na provider pool
    - load_model()
    - Žádný dispatch
    """
    readiness: str  # "ready" | "blocked" | "pruned" | "unknown"
    dispatch_path: str  # "canonical_tool" | "runtime_only_compat"

    # Tool candidates (task_type → tool_name mapping kde existuje)
    tool_candidates: dict[str, str]  # task_type → tool_name

    # Tool capability gaps (tool_name → ToolCapabilityGap)
    capability_gaps: dict[str, ToolCapabilityGap]

    # Blocker reasons
    blockers: list[str]
    pruned_tools: list[str]
    unknown_tools: list[str]
    runtime_only_handlers: list[str]  # task types bez ToolRegistry mapping

    # Control mode impact
    control_mode: str
    will_be_pruned: bool

    # Canonical vs runtime-only summary
    canonical_count: int
    runtime_only_count: int
    satisfied_count: int
    blocked_count: int

    # Sprint F9: Execution context readiness (separované capability/correlation/audit)
    execution_context: ExecutionContextReadiness | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness": self.readiness,
            "dispatch_path": self.dispatch_path,
            "tool_candidates": self.tool_candidates,
            "capability_gaps": {
                k: {
                    "tool_name": v.tool_name,
                    "required_capabilities": list(v.required_capabilities),
                    "available_capabilities": list(v.available_capabilities),
                    "missing_capabilities": list(v.missing_capabilities),
                    "is_satisfied": v.is_satisfied,
                    "is_network_tool": v.is_network_tool,
                    "is_high_memory": v.is_high_memory,
                }
                for k, v in self.capability_gaps.items()
            },
            "blockers": self.blockers,
            "pruned_tools": self.pruned_tools,
            "unknown_tools": self.unknown_tools,
            "runtime_only_handlers": self.runtime_only_handlers,
            "control_mode": self.control_mode,
            "will_be_pruned": self.will_be_pruned,
            "canonical_count": self.canonical_count,
            "runtime_only_count": self.runtime_only_count,
            "satisfied_count": self.satisfied_count,
            "blocked_count": self.blocked_count,
            "execution_context": self.execution_context.to_dict() if self.execution_context else None,
        }


def build_execution_context_readiness(
    dispatch_preview: DispatchReadinessPreview,
    correlation_context: dict[str, Any] | None = None,
    exec_logger_available: bool = False,
) -> ExecutionContextReadiness:
    """
    Build execution context readiness z DispatchReadinessPreview — DIAGNOSTIC ONLY.

    Rozkládá readiness na tři oddělené dimenze:
    1. Capability readiness — zda jsou available_capabilities dostatečné
    2. Correlation readiness — zda scheduler má korelační klíče
    3. Audit readiness — zda je exec_logger dostupný

    Args:
        dispatch_preview: Výsledek preview_dispatch_parity()
        correlation_context: Volitelný dict s korelačními klíči
            (run_id, branch_id, provider_id, action_id)
        exec_logger_available: Zda má scheduler přístup k ToolExecLog

    Returns:
        ExecutionContextReadiness — DIAGNOSTIC ONLY artifact

    Nikdy nevola:
    - execute_with_limits()
    - ToolExecLog.log()
    - Žádný dispatch
    """
    # 1. Capability readiness
    capability_missing = []
    for gap in dispatch_preview.capability_gaps.values():
        if not gap.is_satisfied:
            capability_missing.extend(sorted(gap.missing_capabilities))
    capability_ready = len(capability_missing) == 0

    # 2. Correlation readiness
    run_id_present = False
    branch_id_present = False
    provider_id_present = False
    action_id_present = False

    if correlation_context is not None:
        run_id_present = correlation_context.get("run_id") is not None
        branch_id_present = correlation_context.get("branch_id") is not None
        provider_id_present = correlation_context.get("provider_id") is not None
        action_id_present = correlation_context.get("action_id") is not None

    correlation_ready = run_id_present and branch_id_present

    if correlation_context is None:
        correlation_note = "correlation_context not provided — cannot determine readiness"
    elif not (run_id_present or branch_id_present):
        correlation_note = "correlation keys absent — run_id and/or branch_id missing"
    else:
        present = []
        if run_id_present:
            present.append("run_id")
        if branch_id_present:
            present.append("branch_id")
        if provider_id_present:
            present.append("provider_id")
        if action_id_present:
            present.append("action_id")
        correlation_note = f"correlation ready — present: {', '.join(present)}"

    # 3. Audit readiness
    if exec_logger_available:
        audit_ready = True
        exec_logger_note = "exec_logger available — audit logging possible"
    else:
        audit_ready = False
        exec_logger_note = "exec_logger not available — audit logging not possible (not an error for diagnostic mode)"

    # 4. Canonical dispatch classification
    canonical_tool_dispatch = dispatch_preview.dispatch_path == "canonical_tool"
    runtime_only_compat_dispatch = dispatch_preview.dispatch_path == "runtime_only_compat"

    # 5. Blocker matrix
    blocker_matrix: dict[str, str] = {}
    for tool_name, gap in dispatch_preview.capability_gaps.items():
        if not gap.is_satisfied:
            blocker_matrix[tool_name] = f"missing capabilities: {sorted(gap.missing_capabilities)}"
    for handler in dispatch_preview.runtime_only_handlers:
        blocker_matrix[handler] = "runtime_only_compat_dispatch — no canonical tool mapping"

    return ExecutionContextReadiness(
        capability_ready=capability_ready,
        capability_missing=capability_missing,
        correlation_ready=correlation_ready,
        run_id_present=run_id_present,
        branch_id_present=branch_id_present,
        provider_id_present=provider_id_present,
        action_id_present=action_id_present,
        correlation_note=correlation_note,
        audit_ready=audit_ready,
        exec_logger_note=exec_logger_note,
        canonical_tool_dispatch=canonical_tool_dispatch,
        runtime_only_compat_dispatch=runtime_only_compat_dispatch,
        blocker_matrix=blocker_matrix,
    )


def preview_dispatch_parity(
    task_candidates: list[str],
    available_capabilities: set[str],
    control_mode: str,
    registry_tools: list[Tool] | None = None,
) -> DispatchReadinessPreview:
    """
    Preview dispatch parity pro task kandidáty — DIAGNOSTIC ONLY.

    Tato funkce:
    - Čte ToolRegistry metadata (list_tools, required_capabilities)
    - Mapuje task_type → tool_name přes known mappings
    - Počítá capability gaps bez volání execute_with_limits()
    - Rozlišuje canonical tool dispatch vs runtime_only_compat_dispatch

    Args:
        task_candidates: Seznam task_type řetězců kandidátů
        available_capabilities: Set dostupných capability names
        control_mode: "normal" | "prune" | "panic"
        registry_tools: list[Tool] | None — pokud None, vrací truthful unknown/dispatch_unknown
            bez heavy create_default_registry() fallback (M1-unfriendly v shadow-only path)

    Returns:
        DispatchReadinessPreview — DIAGNOSTIC ONLY artifact

    Nikdy nevola:
    - execute_with_limits()
    - acquire() / load_model()
    - Provider activation
    - create_default_registry() (M1-unfriendly heavy init v shadow-only path)
    """
    # Sprint F3.11: Read mapping from canonical read-side owner (tool_registry.py)
    # Previously this was a local constant — now centralized to prevent drift
    from ..tool_registry import get_task_tool_preview_mapping
    TASK_TYPE_TO_TOOL = get_task_tool_preview_mapping()

    # Load tools from registry (read-only, no execute)
    # H350A FIX: M1-friendly fallback — truthful unknown místo heavy create_default_registry()
    # v shadow-only diagnostic path. create_default_registry() je M1-unfriendly:
    # - registruje všechny handlery včetně _web_search_handler, _entity_extraction_handler atd.
    # - tyto handlery importují celé moduly (requests, bs4, pdfplumber,...)
    # - v shadow-only path nemáme tool execution context, jen metadata
    # SPRÁVNÁ odpověď: return truthful dispatch_unknown když registry_tools=None
    if registry_tools is None:
        # M1-friendly: truthful unknown bez heavy registry init
        # Build dispatch readiness s prázdným registry — mapped handlers = runtime_only
        will_prune = control_mode in ("prune", "panic")

        # All candidates become runtime_only since we have no registry metadata
        runtime_only_handlers = list(task_candidates)
        readiness = "unknown"
        dispatch_path = "runtime_only_compat"

        return DispatchReadinessPreview(
            readiness=readiness,
            dispatch_path=dispatch_path,
            tool_candidates={},  # No registry → no tool mapping
            capability_gaps={},  # No registry → no gap analysis
            blockers=["registry_tools not provided — cannot determine capability gaps in shadow-only path"],
            pruned_tools=[],
            unknown_tools=[],
            runtime_only_handlers=runtime_only_handlers,
            control_mode=control_mode,
            will_be_pruned=will_prune,
            canonical_count=0,
            runtime_only_count=len(runtime_only_handlers),
            satisfied_count=0,
            blocked_count=0,
            execution_context=None,
        )
    else:
        tools = registry_tools

    # Build tool lookup: tool_name → Tool
    tool_lookup: dict[str, Tool] = {t.name: t for t in tools}

    # Analyze each candidate
    tool_candidates: dict[str, str] = {}
    capability_gaps: dict[str, ToolCapabilityGap] = {}
    runtime_only_handlers: list[str] = []
    satisfied_tools: list[str] = []
    blocked_tools: list[str] = []
    pruned_tools: list[str] = []
    unknown_tools: list[str] = []
    blockers: list[str] = []

    # Prune check
    will_prune = control_mode in ("prune", "panic")

    for task_type in task_candidates:
        tool_name = TASK_TYPE_TO_TOOL.get(task_type)

        if tool_name is None:
            # No ToolRegistry mapping — runtime_only_compat_dispatch
            runtime_only_handlers.append(task_type)
            continue

        tool_candidates[task_type] = tool_name
        tool = tool_lookup.get(tool_name)

        if tool is None:
            # Tool exists in mapping but not in registry — unknown
            unknown_tools.append(tool_name)
            continue

        required = tool.required_capabilities
        missing = required - available_capabilities if required else set()
        is_satisfied = len(missing) == 0

        # Check network/high-memory
        is_network = tool.cost_model.network
        is_high_mem = tool.cost_model.ram_mb_est >= 500

        gap = ToolCapabilityGap(
            tool_name=tool_name,
            required_capabilities=required,
            available_capabilities=available_capabilities,
            missing_capabilities=missing,
            is_satisfied=is_satisfied,
            is_network_tool=is_network,
            is_high_memory=is_high_mem,
        )
        capability_gaps[tool_name] = gap

        if will_prune and (is_network or is_high_mem):
            pruned_tools.append(tool_name)
        elif not is_satisfied:
            blocked_tools.append(tool_name)
            blockers.append(f"{tool_name}: missing capabilities {sorted(missing)}")
        else:
            satisfied_tools.append(tool_name)

    # Determine overall readiness
    if will_prune and (pruned_tools or blocked_tools):
        readiness = "pruned"
    elif blocked_tools:
        readiness = "blocked"
    elif unknown_tools or runtime_only_handlers:
        readiness = "unknown"
    else:
        readiness = "ready"

    # Determine dispatch path
    dispatch_path = (
        "runtime_only_compat"
        if runtime_only_handlers
        else "canonical_tool"
    )

    return DispatchReadinessPreview(
        readiness=readiness,
        dispatch_path=dispatch_path,
        tool_candidates=tool_candidates,
        capability_gaps=capability_gaps,
        blockers=blockers,
        pruned_tools=pruned_tools,
        unknown_tools=unknown_tools,
        runtime_only_handlers=runtime_only_handlers,
        control_mode=control_mode,
        will_be_pruned=will_prune,
        canonical_count=len(satisfied_tools) + len(blocked_tools),
        runtime_only_count=len(runtime_only_handlers),
        satisfied_count=len(satisfied_tools),
        blocked_count=len(blocked_tools),
        execution_context=None,  # F9: Attach via build_execution_context_readiness() separately
    )


# =============================================================================
# TYPE CHECKING imports — only used behind TYPE_CHECKING guard
# =============================================================================

if TYPE_CHECKING:
    from typing import Any

    from ..tool_registry import Tool
    from .shadow_inputs import ProviderRuntimeFactsBundle
    from .shadow_parity import ParityArtifact
