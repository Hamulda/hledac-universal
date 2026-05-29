"""
Sprint 8VK: Shadow Scheduler Inputs Scaffold
============================================

**VERDICT: ACTIVE (diagnostic only)** — 2026-04-27, F206B

Tento modul je ACTIVE diagnostic —纯粹的shadow inputs pro čtení faktů.
NESAHÁ na runtime behavior, canonical write path, ani tool execution.

Shadow mode čte facts z canonical modulů (sprint_lifecycle, duckdb_store, model_manager).
PreDecisionSummary z těchto inputů je DIAGNOSTICKÝ artifact, NENÍ nový truth store.
Canonical facts zůstávají vlastněny svými canonical moduly:
  - lifecycle_snapshot → runtime/sprint_lifecycle.py
  - graph_summary → knowledge/duckdb_store.py
  - model/control_facts → types.py (AnalyzerResult)
  - export_handoff → export/COMPAT_HANDOFF.py

Parity-ready scaffold pro sběr shadow inputs pro budoucí shadow scheduler.
NESAHÁ na runtime behavior — pouze scaffolding a pure functions.

Design rules:
- Pure functions, no side effects
- No scheduler dependency
- No shell mutation
- Typed contracts kde existují, compat dict kde ne

Phase systems (STRICTLY SEPARATED):
- workflow_phase: BOOT | WARMUP | ACTIVE | WINDUP | EXPORT | TEARDOWN (SprintLifecycleManager)
- control_phase: tool pruning / resource governance decisions (model/control split)
- windup_local_phase: synthesis mode within windup (structured generation)

Feature flag vocabulary (scaffold only, NOT activated):
- legacy_runtime: today's runtime path (default)
- scheduler_shadow: shadow mode, reads only
- scheduler_active: full scheduler-driven mode (future)

Inventory of shadow inputs:
1. lifecycle_snapshot   → SprintLifecycleManager.snapshot()
2. export_handoff       → ExportHandoff (typed) or scorecard dict (compat)
3. graph_summary        → ioc_graph.stats() + ioc_graph.get_top_nodes_by_degree() (DuckPGQGraph direct)
                         or duckdb_store.get_graph_stats() + duckdb_store.get_top_seed_nodes() (public seam)
4. graph_backend_caps   → which graph backend (Kuzu vs DuckPGQ)
5. model/control_facts → AnalyzerResult / AutoResearchProfile
6. provider_recommend   → from capabilities.py registry (future)
7. branch_decision_facts → BranchDecision (typed, Sprint 8WA)
8. top_nodes_facts      → top_nodes from export handoff

Owned by: this module (shadow scaffold)
Future owners:
- lifecycle_snapshot → runtime/sprint_lifecycle.py (already there)
- export_handoff → export/COMPAT_HANDOFF.py (already there)
- graph_summary → knowledge/duckdb_store.py (duckdb_store.get_graph_stats() public seam)
                 DuckPGQGraph is the analytics donor via duckdb_store._ioc_graph injection
- graph_backend_caps → knowledge/ioc_graph.py or knowledge/graph_layer.py
- model/control_facts → autonomous_analyzer.py / capabilities.py
- provider_recommend → capabilities.py CapabilityRegistry
- branch_decision_facts → types.py BranchDecision (already there)
- top_nodes_facts → export/COMPAT_HANDOFF.py (from_windup)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from hledac.universal.project_types import (
        AnalyzerResult,
        ExportHandoff,
    )
    from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager


# =============================================================================
# Feature flag vocabulary — scaffold only, NOT runtime-activated
# =============================================================================

class RuntimeMode:
    """
    Feature flag vocabulary pro budoucí scheduler režimy.

    Nikdy neaktivováno v tomto scaffoldu — pouze dokumentační.
    Aktivace půjde přes explicitní flag v config nebo env var.

    Enums:
        LEGACY_RUNTIME — dnešní runtime path (default)
        SCHEDULER_SHADOW — shadow mode, čte facts, žádné řízení
        SCHEDULER_ACTIVE — plný scheduler-driven režim (budoucí)

    Shrouded ownership — this scaffold module defines the vocabulary
    but does NOT own any canonical fact contracts. All facts remain
    owned by their respective canonical modules (sprint_lifecycle.py,
    types.py, duckdb_store.py, autonomous_analyzer.py, capabilities.py).
    """
    LEGACY_RUNTIME = "legacy_runtime"
    SCHEDULER_SHADOW = "scheduler_shadow"
    SCHEDULER_ACTIVE = "scheduler_active"

    @classmethod
    def get_current(cls) -> str:
        """
        Return current runtime mode string based on environment.

        Default: LEGACY_RUNTIME
        Shadow mode: HLEDAC_RUNTIME_MODE=scheduler_shadow
        Active mode: HLEDAC_RUNTIME_MODE=scheduler_active (future)
        """
        mode = os.getenv("HLEDAC_RUNTIME_MODE", cls.LEGACY_RUNTIME)
        if mode == cls.SCHEDULER_SHADOW:
            return cls.SCHEDULER_SHADOW
        if mode == cls.SCHEDULER_ACTIVE:
            return cls.SCHEDULER_ACTIVE
        return cls.LEGACY_RUNTIME

    @classmethod
    def is_shadow_mode(cls) -> bool:
        """
        True if HLEDAC_RUNTIME_MODE=scheduler_shadow is set.

        This is a VOCABULARY flag only — no runtime activation is performed
        by this scaffold. Activation requires explicit config/env wiring elsewhere.
        """
        return cls.get_current() == cls.SCHEDULER_SHADOW

    @classmethod
    def is_active_mode(cls) -> bool:
        """
        True if HLEDAC_RUNTIME_MODE=scheduler_active is set.

        This is a VOCABULARY flag only — SCHEDULER_ACTIVE is not yet
        implemented. Activation requires explicit future config/env wiring.
        """
        return cls.get_current() == cls.SCHEDULER_ACTIVE

    @classmethod
    def is_legacy_mode(cls) -> bool:
        """True if running in legacy runtime mode (default)."""
        return cls.get_current() == cls.LEGACY_RUNTIME


# =============================================================================
# Phase systems — STRICTLY SEPARATED, NEVER merged
# =============================================================================

@dataclass(frozen=True)
class WorkflowPhase:
    """
    Workflow phase — řídí celý sprint lifecycle.

    Canonical owner: SprintLifecycleManager (runtime/sprint_lifecycle.py)
    This scaffold only READS and packages it.
    """
    phase: str  # BOOT | WARMUP | ACTIVE | WINDUP | EXPORT | TEARDOWN
    entered_at_monotonic: float | None = None
    started_at_monotonic: float | None = None
    sprint_duration_s: float = 1800.0
    windup_lead_s: float = 180.0

    @classmethod
    def from_lifecycle_snapshot(cls, snap: dict[str, Any]) -> WorkflowPhase:
        """Extract from SprintLifecycleManager.snapshot() dict."""
        return cls(
            phase=snap.get("current_phase", "UNKNOWN"),
            entered_at_monotonic=snap.get("entered_phase_at"),
            started_at_monotonic=snap.get("started_at_monotonic"),
            sprint_duration_s=snap.get("sprint_duration_s", 1800.0),
            windup_lead_s=snap.get("windup_lead_s", 180.0),
        )


@dataclass(frozen=True)
class ControlPhase:
    """
    Control phase — tool pruning / resource governance decisions.

    Toto řídí JINOU osu než workflow_phase:
    - workflow_phase řídí kdy se co děje (BOOT→ACTIVE→WINDUP)
    - control_phase řídí jak intenzivně se to děje (normal/prune/panic)

    Canonical owner: SprintLifecycleManager.recommended_tool_mode()
    This scaffold only READS and packages it.
    """
    mode: str  # normal | prune | panic
    thermal_state: str = "nominal"  # nominal | throttled | fair | critical
    remaining_s: float = 0.0

    @classmethod
    def from_lifecycle(
        cls,
        lifecycle: SprintLifecycleManager,
        now_monotonic: float | None = None,
        thermal_state: str = "nominal",
    ) -> ControlPhase:
        """Derive from SprintLifecycleManager.recommended_tool_mode()."""
        mode = lifecycle.recommended_tool_mode(now_monotonic, thermal_state)
        remaining = lifecycle.remaining_time(now_monotonic)
        return cls(mode=mode, thermal_state=thermal_state, remaining_s=remaining)


@dataclass(frozen=True)
class WindupLocalPhase:
    """
    Windup-local synthesis mode — special режим внутри WINDUP fáze.

    Toto je ODDĚLENÉ od workflow_phase i control_phase.
    Popisuje režim structured generation uvnitř windup:
    - synthesis: normální syntéza
    - structured: structured output režim (např. JSON schema)
    - minimal: pouze scorecard bez plné syntézy

    Canonical owner: windup_engine (runtime/windup_engine.py) — future
    Currently hardcoded in run_windup() as "synthesis" unless exception occurs.
    """
    mode: str = "synthesis"  # synthesis | structured | minimal
    error_encountered: bool = False
    synthesis_engine: str = "unknown"


# =============================================================================
# Shadow input bundles
# =============================================================================

@dataclass
class LifecycleSnapshotBundle:
    """
    Bundle všech lifecycle-related shadow inputs.

    Obsahuje workflow_phase, control_phase, windup_local_phase
    v ODDĚLENÝCH polích — NIKDY neslité do jednoho phase pole.

    Shrouded ownership — this bundle is a DIAGNOSTIC SCAFFOLD.
    It does NOT become a shared contract. Canonical facts remain
    owned by SprintLifecycleManager (runtime/sprint_lifecycle.py).

    Fact stability:
    - workflow_phase: STABLE (from SprintLifecycleManager.snapshot())
    - control_phase: STABLE (from recommended_tool_mode())
    - windup_local_phase: COMPAT (from windup_engine hardcoded "synthesis")
      → future_owner: runtime/windup_engine.py (when it gains structured mode)

    Class-level attributes (NOT instance overrides — these document canonical ownership):
    - __future_owner__: "runtime/sprint_lifecycle.py" — canonical owner of workflow/control phases
    - __compat_note__: None for STABLE path; set for COMPAT paths
    """
    workflow_phase: WorkflowPhase
    control_phase: ControlPhase
    windup_local_phase: WindupLocalPhase | None = None
    # Raw snapshot for compatibility only
    raw_snapshot: dict[str, Any] = field(default_factory=dict)
    # Fact stability classification
    fact_stability: str = "STABLE"  # STABLE | COMPAT | UNKNOWN
    # Compat note: set when fact_stability != STABLE
    __compat_note__: str | None = None

    # future_owner is a class-level attribute documenting canonical ownership.
    # Instance-level overrides are NOT supported — each bundle class has one canonical owner.
    __future_owner__: ClassVar[str] = "runtime/sprint_lifecycle.py"

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_phase": self.workflow_phase.phase,
            "workflow_phase_entered_at": self.workflow_phase.entered_at_monotonic,
            "workflow_phase_started_at": self.workflow_phase.started_at_monotonic,
            "control_phase_mode": self.control_phase.mode,
            "control_phase_thermal": self.control_phase.thermal_state,
            "control_phase_remaining_s": self.control_phase.remaining_s,
            "windup_local_mode": self.windup_local_phase.mode if self.windup_local_phase else None,
            "windup_local_synthesis_engine": (
                self.windup_local_phase.synthesis_engine if self.windup_local_phase else None
            ),
            # Fact stability — diagnostic metadata for distinguishing STABLE vs COMPAT vs UNKNOWN
            "fact_stability": self.fact_stability,
            "future_owner": self.__future_owner__,
            "__compat_note__": self.__compat_note__,
        }


@dataclass
class GraphSummaryBundle:
    """
    Bundle graph-related shadow inputs.

    source: ioc_graph.stats() + ioc_graph.get_top_nodes_by_degree() (DuckPGQGraph direct).
            duckdb_store.get_graph_stats() and duckdb_store.get_top_seed_nodes() are the
            store-facing public seams wrapping the injected _ioc_graph (DuckPGQGraph).
    compat source: scorecard["top_graph_nodes"]

    Shrouded ownership — DIAGNOSTIC SCAFFOLD, NOT a shared contract.
    Canonical facts owned by knowledge/duckdb_store.py (public seam: get_graph_stats())
    via the injected DuckPGQGraph analytics donor.

    Fact stability:
    - from_ioc_graph_stats: STABLE (from DuckPGQGraph.stats() via duckdb_store.get_graph_stats() seam)
    - from_scorecard_top_nodes: COMPAT (legacy compat path)
      → future_owner: duckdb_store.get_top_seed_nodes() — already implemented, scorecard path deprecated

    Class-level attributes (NOT instance overrides):
    - __future_owner__: "knowledge/duckdb_store.py" — canonical owner

    duckdb_store public seams (Sprint 8VY/8TF):
    - get_graph_stats() → {nodes, edges, pgq_available} or {} fail-open (DuckPGQGraph.stats() truth, F700E)
    - get_top_seed_nodes(n=5) → list[dict] or [] fail-open
    - get_connected_iocs(value, max_hops) → list or [] fail-open
    - get_analytics_graph_for_synthesis() → DuckPGQGraph | None
    """
    node_count: int = 0
    edge_count: int = 0
    pgq_active: bool = False
    top_nodes: list[Any] = field(default_factory=list)  # noqa: A003
    backend: str = "unknown"  # duckpgq | kuzu | none
    raw_stats: dict[str, Any] = field(default_factory=dict)
    # Fact stability classification
    fact_stability: str = "UNKNOWN"  # STABLE | COMPAT | UNKNOWN
    # Compat note: set when fact_stability != STABLE
    __compat_note__: str | None = None

    # future_owner is class-level — canonical owner of graph facts
    __future_owner__: ClassVar[str] = "knowledge/duckdb_store.py"

    @classmethod
    def from_ioc_graph_stats(cls, stats: dict[str, Any], top_nodes: list[Any] | None = None) -> GraphSummaryBundle:
        """Build from DuckPGQGraph.stats() dict via duckdb_store.get_graph_stats() seam. STABLE path."""
        # F700E fix: DuckPGQGraph.stats() returns pgq_available (truth).
        # Fall back to pgq_active for backward compat with older callers.
        pgq_value = stats.get("pgq_available", stats.get("pgq_active", False))
        return cls(
            node_count=stats.get("nodes", 0),
            edge_count=stats.get("edges", 0),
            pgq_active=pgq_value,
            top_nodes=top_nodes or [],
            backend="duckpgq",
            raw_stats=stats,
            fact_stability="STABLE",
        )

    @classmethod
    def from_scorecard_top_nodes(cls, top_nodes: list[Any]) -> GraphSummaryBundle:
        """Build from scorecard top_nodes (compat path). COMPAT — deprecated."""
        return cls(
            node_count=0,  # unknown from compat path
            edge_count=0,
            pgq_active=False,
            top_nodes=top_nodes,
            backend="unknown",
            raw_stats={},
            fact_stability="COMPAT",
            __compat_note__="scorecard path is deprecated; use duckdb_store.get_graph_stats() seam",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_nodes": self.node_count,
            "graph_edges": self.edge_count,
            "graph_pgq_active": self.pgq_active,
            "graph_backend": self.backend,
            "graph_top_nodes": self.top_nodes,
            "graph_fact_stability": self.fact_stability,
            "future_owner": self.__future_owner__,
            "__compat_note__": self.__compat_note__,
        }


@dataclass
class ModelControlFactsBundle:
    """
    Bundle model/control-related shadow inputs.

    source: AnalyzerResult (types.py)
    compat source: AutoResearchProfile (autonomous_analyzer.py) via .to_capability_signal()

    Shrouded ownership — DIAGNOSTIC SCAFFOLD, NOT a shared contract.
    Canonical facts owned by autonomous_analyzer.py / types.py.

    Fact stability:
    - from_analyzer_result: STABLE (typed path from AnalyzerResult)
    - from raw_profile dict: COMPAT (legacy compat path)
      → future_owner: autonomous_analyzer.py / capabilities.py

    Class-level attributes (NOT instance overrides):
    - __future_owner__: "autonomous_analyzer.py / types.py" — canonical owner
    """
    tools: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    privacy_level: str = "STANDARD"
    use_tor: bool = False
    depth: str = "STANDARD"
    use_tot: bool = False
    tot_mode: str = "standard"
    models_needed: list[str] = field(default_factory=list)
    # From capability signal
    requires_embeddings: bool = False
    requires_ner: bool = False
    raw_profile: dict[str, Any] | None = None
    # Fact stability classification
    fact_stability: str = "UNKNOWN"  # STABLE | COMPAT | UNKNOWN
    # Compat note: set when fact_stability != STABLE
    __compat_note__: str | None = None

    # future_owner is class-level — canonical owner of model/control facts
    __future_owner__: ClassVar[str] = "autonomous_analyzer.py / types.py"

    @classmethod
    def from_analyzer_result(cls, result: AnalyzerResult) -> ModelControlFactsBundle:
        """Build from AnalyzerResult (typed path). STABLE."""
        sig = result.to_capability_signal()
        return cls(
            tools=list(result.tools),
            sources=list(result.sources),
            privacy_level=result.privacy_level,
            use_tor=result.use_tor,
            depth=result.depth,
            use_tot=result.use_tot,
            tot_mode=result.tot_mode,
            models_needed=list(result.models_needed),
            requires_embeddings=sig.get("requires_embeddings", False),
            requires_ner=sig.get("requires_ner", False),
            raw_profile=sig,
            fact_stability="STABLE",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mc_tools": self.tools,
            "mc_sources": self.sources,
            "mc_privacy": self.privacy_level,
            "mc_use_tor": self.use_tor,
            "mc_depth": self.depth,
            "mc_use_tot": self.use_tot,
            "mc_tot_mode": self.tot_mode,
            "mc_models_needed": self.models_needed,
            "mc_requires_embeddings": self.requires_embeddings,
            "mc_requires_ner": self.requires_ner,
            "mc_fact_stability": self.fact_stability,
            "future_owner": self.__future_owner__,
            "__compat_note__": self.__compat_note__,
        }


# =============================================================================
# Main collector function — pure, no side effects
# =============================================================================

def collect_lifecycle_snapshot(
    lifecycle: SprintLifecycleManager,
    now_monotonic: float | None = None,
    thermal_state: str = "nominal",
    windup_synthesis_mode: str = "synthesis",
    windup_error: bool = False,
    windup_engine: str = "unknown",
) -> LifecycleSnapshotBundle:
    """
    Collect all lifecycle-related shadow inputs.

    This is a PURE function — no side effects, no I/O, no scheduler dependency.

    Args:
        lifecycle: SprintLifecycleManager instance
        now_monotonic: optional fake clock for testing
        thermal_state: nominal | throttled | fair | critical
        windup_synthesis_mode: synthesis | structured | minimal
        windup_error: True if windup encountered an error
        windup_engine: synthesis engine name from windup

    Returns:
        LifecycleSnapshotBundle with SEPARATED phase fields
    """
    raw = lifecycle.snapshot()
    wf_phase = WorkflowPhase.from_lifecycle_snapshot(raw)
    ctrl_phase = ControlPhase.from_lifecycle(lifecycle, now_monotonic, thermal_state)

    windup_local = None
    fact_stability = "STABLE"  # workflow and control are always STABLE from canonical sources
    compat_note: str | None = None

    if wf_phase.phase == "WINDUP":
        windup_local = WindupLocalPhase(
            mode=windup_synthesis_mode,
            error_encountered=windup_error,
            synthesis_engine=windup_engine,
        )
        # windup_local_phase is COMPAT — currently hardcoded in windup_engine.run_windup()
        # future_owner: runtime/windup_engine.py when it gains structured mode
        fact_stability = "COMPAT"
        compat_note = "windup_local_phase is COMPAT: currently hardcoded in windup_engine.run_windup()"

    return LifecycleSnapshotBundle(
        workflow_phase=wf_phase,
        control_phase=ctrl_phase,
        windup_local_phase=windup_local,
        raw_snapshot=raw,
        fact_stability=fact_stability,
        __compat_note__=compat_note,
    )


def collect_graph_summary(
    ioc_graph: Any | None = None,
    scorecard: dict[str, Any] | None = None,
) -> GraphSummaryBundle:
    """
    Collect graph-related shadow inputs.

    PURE function — no side effects, no I/O.

    Args:
        ioc_graph: DuckPGQGraph instance (from duckdb_store._ioc_graph or duckdb_store.get_analytics_graph_for_synthesis()); None for scorecard-only path
        scorecard: scorecard dict (compat path) or None

    Returns:
        GraphSummaryBundle
    """
    # Primary path: DuckPGQGraph stats
    if ioc_graph is not None:
        try:
            stats = ioc_graph.stats()
            top = []
            try:
                top = ioc_graph.get_top_nodes_by_degree(n=10) or []
            except Exception:
                pass
            return GraphSummaryBundle.from_ioc_graph_stats(stats, top)
        except Exception:
            pass

    # Compat path: scorecard top_graph_nodes
    if scorecard is not None:
        top_nodes = scorecard.get("top_graph_nodes", [])
        if top_nodes:
            return GraphSummaryBundle.from_scorecard_top_nodes(top_nodes)

    # Nothing provided — unknown stability
    return GraphSummaryBundle(
        fact_stability="UNKNOWN",
        __compat_note__="no ioc_graph and no scorecard provided",
    )


def collect_model_control_facts(
    analyzer_result: AnalyzerResult | None = None,
    raw_profile: dict[str, Any] | None = None,
) -> ModelControlFactsBundle:
    """
    Collect model/control-related shadow inputs.

    PURE function.

    Args:
        analyzer_result: AnalyzerResult (typed path) or None
        raw_profile: AutoResearchProfile.asdict() (compat path) or None

    Returns:
        ModelControlFactsBundle
    """
    if analyzer_result is not None:
        return ModelControlFactsBundle.from_analyzer_result(analyzer_result)

    if raw_profile is not None:
        return ModelControlFactsBundle(
            tools=raw_profile.get("tools", []),
            sources=raw_profile.get("sources", []),
            privacy_level=raw_profile.get("privacy_level", "STANDARD"),
            use_tor=raw_profile.get("use_tor", False),
            depth=raw_profile.get("depth", "STANDARD"),
            use_tot=raw_profile.get("use_tot", False),
            tot_mode=raw_profile.get("tot_mode", "standard"),
            models_needed=raw_profile.get("models_needed", []),
            raw_profile=raw_profile,
            fact_stability="COMPAT",
            __compat_note__="raw_profile dict path is legacy compat; use AnalyzerResult (typed path)",
        )

    return ModelControlFactsBundle(
        fact_stability="UNKNOWN",
        __compat_note__="no analyzer_result and no raw_profile provided",
    )


def collect_export_handoff_facts(
    handoff: ExportHandoff | None = None,
    scorecard: dict[str, Any] | None = None,
    sprint_id: str = "unknown",
) -> dict[str, Any]:
    """
    Collect export handoff facts.

    PURE function — thin wrapper around existing typed/contract.

    Fact stability:
    - with ExportHandoff (typed): STABLE
    - with scorecard dict: COMPAT
    - no inputs: UNKNOWN

    Returns a dict with keys:
        - sprint_id
        - synthesis_engine
        - gnn_predictions
        - top_nodes_count
        - ranked_parquet_present
        - phase_durations
        - fact_stability: STABLE | COMPAT | UNKNOWN
        - future_owner: export/COMPAT_HANDOFF.py
    """
    if handoff is not None:
        result = {
            "sprint_id": handoff.sprint_id,
            "synthesis_engine": handoff.synthesis_engine,
            "gnn_predictions": handoff.gnn_predictions,
            "top_nodes_count": len(handoff.top_nodes),
            "ranked_parquet_present": handoff.ranked_parquet is not None,
            "phase_durations": handoff.phase_durations,
        }
        result["fact_stability"] = "STABLE"
        result["future_owner"] = "export/COMPAT_HANDOFF.py"
        return result

    if scorecard is not None:
        result = {
            "sprint_id": scorecard.get("sprint_id", sprint_id),
            "synthesis_engine": scorecard.get("synthesis_engine_used", "unknown"),
            "gnn_predictions": scorecard.get("gnn_predicted_links", 0),
            "top_nodes_count": len(scorecard.get("top_graph_nodes", [])),
            "ranked_parquet_present": scorecard.get("ranked_parquet") is not None,
            "phase_durations": scorecard.get("phase_duration_seconds", {}),
        }
        result["fact_stability"] = "COMPAT"
        result["__compat_note__"] = "scorecard dict path is legacy compat; use ExportHandoff (typed path)"
        result["future_owner"] = "export/COMPAT_HANDOFF.py"
        return result

    return {
        "sprint_id": sprint_id,
        "synthesis_engine": "unknown",
        "gnn_predictions": 0,
        "top_nodes_count": 0,
        "ranked_parquet_present": False,
        "phase_durations": {},
        "fact_stability": "UNKNOWN",
        "__compat_note__": "no handoff and no scorecard provided",
        "future_owner": "export/COMPAT_HANDOFF.py",
    }


# =============================================================================
# Sprint F3.13: Provider Runtime Facts Seam
# Read-only runtime facts about current model/provider state
# =============================================================================

@dataclass
class ProviderRuntimeFactsBundle:
    """
    Bundle provider/model runtime-related shadow inputs.

    source: brain/model_manager.py::ModelManager.get_current_model()
            brain/model_lifecycle.py::get_model_lifecycle_status()

    Shrouded ownership — DIAGNOSTIC SCAFFOLD, NOT a shared contract.
    Canonical facts owned by brain/model_manager.py and brain/model_lifecycle.py.

    This bundle provides RUNTIME-WIDE FACTS about what model is currently loaded:
    - current_model: which model is loaded (hermes/modernbert/gliner/None)
    - is_loaded: whether a model is currently loaded
    - initialized: whether MLX/runtime is initialized

    This is DISTINCT from model_control.models_needed which tells what SHOULD be loaded.

    Fact stability:
    - from_manager_and_lifecycle: STABLE (direct from ModelManager + model_lifecycle)
    - from_lifecycle_only: COMPAT (model_lifecycle shadow-state only)
    - no inputs: UNKNOWN

    Class-level attributes (NOT instance overrides):
    - __future_owner__: "brain/model_manager.py / brain/model_lifecycle.py" — canonical owner
    """
    current_model: str | None = None  # "hermes" | "modernbert" | "gliner" | None
    is_loaded: bool = False
    initialized: bool = False
    last_error: str | None = None
    # Fact stability classification
    fact_stability: str = "UNKNOWN"  # STABLE | COMPAT | UNKNOWN
    # Compat note: set when fact_stability != STABLE
    __compat_note__: str | None = None

    # future_owner is class-level — canonical owner of runtime facts
    __future_owner__: ClassVar[str] = "brain/model_manager.py / brain/model_lifecycle.py"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_current_model": self.current_model,
            "runtime_is_loaded": self.is_loaded,
            "runtime_initialized": self.initialized,
            "runtime_last_error": self.last_error,
            "runtime_fact_stability": self.fact_stability,
            "future_owner": self.__future_owner__,
            "__compat_note__": self.__compat_note__,
        }


def collect_provider_runtime_facts(
    model_manager: Any = None,
    lifecycle_status: dict[str, Any] | None = None,
) -> ProviderRuntimeFactsBundle:
    """
    Collect provider/model runtime facts from ModelManager and model_lifecycle.

    PURE function — no side effects, no I/O, no model loading.

    This function reads from EXISTING read-only surfaces:
    - ModelManager.get_current_model() — what is currently loaded
    - model_lifecycle.get_model_lifecycle_status() — lifecycle shadow-state

    Args:
        model_manager: ModelManager instance (or None)
        lifecycle_status: Result of get_model_lifecycle_status() dict (or None)

    Returns:
        ProviderRuntimeFactsBundle

    Invariant §F3.13: This function NEVER calls load_model(), acquire(),
    or any activation API. It only reads existing state.
    """
    # Primary path: ModelManager available
    if model_manager is not None:
        try:
            current = model_manager.get_current_model()
            is_loaded = current is not None
            # model_lifecycle status for initialization state
            lc_status = lifecycle_status or {}
            initialized = lc_status.get("initialized", False)
            last_error = lc_status.get("last_error")
            return ProviderRuntimeFactsBundle(
                current_model=current,
                is_loaded=is_loaded,
                initialized=initialized,
                last_error=last_error,
                fact_stability="STABLE",
            )
        except Exception:
            pass

    # Compat path: lifecycle_status only
    if lifecycle_status is not None:
        try:
            current = lifecycle_status.get("current_model")
            is_loaded = lifecycle_status.get("loaded", False)
            initialized = lifecycle_status.get("initialized", False)
            last_error = lifecycle_status.get("last_error")
            return ProviderRuntimeFactsBundle(
                current_model=current,
                is_loaded=is_loaded,
                initialized=initialized,
                last_error=last_error,
                fact_stability="COMPAT",
                __compat_note__="model_manager not available, using model_lifecycle shadow-state only",
            )
        except Exception:
            pass

    # Nothing available — unknown
    return ProviderRuntimeFactsBundle(
        fact_stability="UNKNOWN",
        __compat_note__="no model_manager and no lifecycle_status provided",
    )
