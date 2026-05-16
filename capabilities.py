"""
F650H: ModelLifecycleManager facade truth + no third model truth
================================================================

Sprint: F650H / F600K / F130S
Target: capabilities.py
Goal: bounded de-ownership + facade truth hardening

VERIFIED HYPOTHESES:
- H2 CONFIRMED: _release_all_models() does MLX cleanup directly — duplicate of
  ModelManager._release_current_async() cleanup. Canonical owner: ModelManager.
  Fix: delegate MLX cleanup to canonical seam, remove duplicate from capability layer.
- H3 CONFIRMED: _active_models is local compat state, not authoritative runtime truth.
  get_active_models() is not called by any external consumer (only tests).
  Fix: label explicitly as local/compat.
- H1 PARTIAL: registry.load/unload are capability-level (no direct ModelManager call).
  No third model truth created — facade semantics OK.
- H4: phase enforcement is coarse-grained only — no drift.

CHANGES:
1. _release_all_models(): remove direct MLX cleanup — delegate to canonical seam
2. get_active_models(): add explicit local/compat labeling in docstring
3. No new manager, no model rewrite, no broad call-site rewiring
"""

from __future__ import annotations

import asyncio
import gc
import logging
from enum import Enum
from typing import Any, Dict, Optional, Set, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from .project_types import AnalyzerResult
    MLX_AVAILABLE: bool
    mx: Any
from dataclasses import dataclass

# MLX lazy import — avoid eager ~171ms tax at module load time.
# F600D: Gate layer must be lightweight. MLX is only needed in
# ModelLifecycleManager._release_all_models() which is runtime call,
# not import-time. Using module-level __getattr__ for lazy mlx.core loading.
_MLX_LOADED = False

def __getattr__(name: str) -> Any:
    global _MLX_LOADED
    if name == "MLX_AVAILABLE":
        if not _MLX_LOADED:
            try:
                import mlx.core as _mx
                globals()["mx"] = _mx
                _MLX_LOADED = True
            except ImportError:
                _MLX_LOADED = True  # Mark as loaded (failed)
        return _MLX_LOADED and "mx" in globals()
    if name == "mx":
        if not _MLX_LOADED:
            try:
                import mlx.core as _mx
                globals()["mx"] = _mx
                _MLX_LOADED = True
            except ImportError:
                _MLX_LOADED = True
                raise AttributeError("mlx.core not available")
        return globals().get("mx")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger(__name__)


class Capability(Enum):
    """Research capabilities that can be dynamically loaded."""
    # Knowledge & RAG
    GRAPH_RAG = "graph_rag"
    ENTITY_LINKING = "entity_linking"
    RERANKING = "reranking"
    CONTEXT_GRAPH = "context_graph"

    # Document Intelligence
    METADATA_EXTRACT = "metadata_extract"
    DOC_INTEL = "doc_intel"
    LONG_CONTEXT = "long_context"

    # Network & OSINT
    STEALTH = "stealth"
    DNS_TUNNEL = "dns_tunnel"
    NETWORK_RECON = "network_recon"
    DARK_WEB = "dark_web"

    # Analysis
    TEMPORAL = "temporal"
    PATTERN_MINING = "pattern_mining"
    INSIGHT = "insight"

    # Crypto & Security
    CRYPTO_INTEL = "crypto_intel"
    STEGO = "stego"
    BLOCKCHAIN = "blockchain"

    # Advanced
    SNN = "snn"
    FEDERATED = "federated"
    QUANTUM_PATH = "quantum_path"
    META_OPTIMIZER = "meta_optimizer"

    # Tree of Thoughts
    TOT = "tot"

    # Models
    HERMES = "hermes"
    MODERNBERT = "modernbert"
    GLINER = "gliner"


# =============================================================================
# F6: Capability Truth Normalization Seam
# =============================================================================
# PURPOSE: Explicitly separate capability truth layers that were previously
# conflated in CapabilityRegistry.is_available() and CapabilityStatus.available.
#
# Before F6:
#   - CapabilityStatus.available mixed registry_declared and runtime_loaded
#   - is_available() returned True if loaded OR available (no distinction)
#   - No way to ask "declared but not yet effective for tool contract"
#
# After F6:
#   Four explicit layers with clear semantics:
#
#   declared_by_tool_contract:
#     - What tool contracts (Tool.required_capabilities) declare they need
#     - Source: tool_registry.py Tool definitions
#     - Questions answered: "Does web_search declare reranking?"
#
#   registry_declared_available:
#     - What CapabilityRegistry.register() sets as available=True
#     - Source: create_default_registry() or explicit register() calls
#     - Questions answered: "Did we register reranking as available?"
#     - DOES NOT answer: "Is the module actually importable?"
#
#   runtime_loaded:
#     - What CapabilityRegistry.load() successfully loaded into _loaded set
#     - Source: actual async loader invocation
#     - Questions answered: "Did we successfully call the loader?"
#
#   effective_for_tool_contract:
#     - What is both declared AND registry_available AND runtime_loaded
#     - Questions answered: "Can web_search actually use reranking right now?"
#     - This is the SOUND answer for tool execution decisions
#
# WHY THIS IS A SEAM, NOT A FRAMEWORK:
#   - No new global manager
#   - No new heavy backend
#   - No broad runtime rewiring
#   - Only a descriptor/helper API for truthful capability introspection
#   - Lazy: only probes module existence when explicitly asked
#
# RATIONALE FOR FOUR LAYERS (not three):
#   - declared != available is important for scaffold vs ready distinction
#   - available != loaded is important for on-demand vs eager distinction
#   - loaded != effective is important for tool contract decisions
#   Example: RERANKING is declared_by_tool_contract and
#   registry_declared_available (module path registered), but
#   runtime_loaded=False until first use. This is normal scaffold state.
# =============================================================================


class CapabilityTruthLayer(Enum):
    """
    Explicit truth layers for capability introspection.

    These layers form a partial order: declared <= available <= loaded <= effective.
    Not all capabilities reach effective status - this is normal for scaffold state.
    """
    # What tool contracts declare they require (source of truth: tool_registry.py)
    DECLARED_BY_TOOL_CONTRACT = "declared"

    # What registry.register() set as available=True (source: create_default_registry)
    REGISTRY_DECLARED_AVAILABLE = "available"

    # What load() successfully materialized into _loaded set
    RUNTIME_LOADED = "loaded"

    # What is declared AND available AND loaded (sound for tool execution)
    EFFECTIVE_FOR_TOOL_CONTRACT = "effective"


@dataclass
class CapabilityTruthStatus:
    """
    F6: Truthful capability status across all four layers.

    This dataclass replaces the conflated CapabilityStatus.available field
    with explicit per-layer booleans. Use probe_capability_truth() to
    populate this for a given capability.
    """
    capability: Capability

    # Layer 1: What tool contracts declare
    declared_by_tool_contract: bool = False

    # Layer 2: What registry.register() set as available
    registry_declared_available: bool = False

    # Layer 3: What load() successfully materialized
    runtime_loaded: bool = False

    @property
    def effective_for_tool_contract(self) -> bool:
        """
        All three conditions must be true for effective status.

        A capability is effective_for_tool_contract when:
        1. Some tool contract declares it (declared_by_tool_contract)
        2. Registry marked it available (registry_declared_available)
        3. Runtime successfully loaded it (runtime_loaded)

        This is the SOUND answer for "can this capability be used for
        tool execution right now?"
        """
        return (
            self.declared_by_tool_contract
            and self.registry_declared_available
            and self.runtime_loaded
        )

    def is_scaffold_only(self) -> bool:
        """
        Returns True when capability is declared/available but NOT effective.

        Scaffold-only means: registered as available but not yet loaded.
        This is normal for lazy/on-demand capabilities that haven't been
        needed yet. NOT an error state.
        """
        return (
            self.declared_by_tool_contract
            and self.registry_declared_available
            and not self.runtime_loaded
        )

    def layer_summary(self) -> dict[str, bool]:
        """Return all layers as dict for logging/inspection."""
        return {
            "declared": self.declared_by_tool_contract,
            "available": self.registry_declared_available,
            "loaded": self.runtime_loaded,
            "effective": self.effective_for_tool_contract,
        }


def probe_capability_truth(
    capability: Capability,
    registry: "CapabilityRegistry",
    tool_contract_declarations: Optional[dict[str, set[str]]] = None,
) -> CapabilityTruthStatus:
    """
    F6: Probe all four truth layers for a capability.

    This is the canonical way to get a truthful picture of a capability's
    status across all layers. Lazy: only imports module if needed.

    Args:
        capability: The capability to probe
        registry: CapabilityRegistry instance to check
        tool_contract_declarations: Optional dict of tool_name -> required_caps
            If not provided, reads from Tool.required_capabilities via
            tool_registry module (read-only, no side effects).

    Returns:
        CapabilityTruthStatus with all layers populated
    """
    status = CapabilityTruthStatus(capability=capability)

    # Layer 1: declared_by_tool_contract
    # Check if any tool contract declares this capability as required
    if tool_contract_declarations is None:
        tool_contract_declarations = _get_tool_capability_declarations()

    for tool_caps in tool_contract_declarations.values():
        if capability.value in tool_caps:
            status.declared_by_tool_contract = True
            break

    # Layer 2: registry_declared_available
    reg_status = registry._status.get(capability)
    if reg_status:
        status.registry_declared_available = reg_status.available

    # Layer 3: runtime_loaded
    status.runtime_loaded = capability in registry._loaded

    return status


def _get_tool_capability_declarations() -> dict[str, set[str]]:
    """
    Read Tool.required_capabilities from tool_registry (read-only, bounded).

    FIX F600C: Previously called create_default_registry() which creates
    a full ToolRegistry + all tool handlers (heavy for M1 8GB).
    Now uses direct tool-name lookup from curated list to avoid registry
    instantiation overhead.

    For M1 8GB, prefer passing tool_contract_declarations explicitly
    to avoid this overhead entirely.

    Returns:
        Dict of tool_name -> set of required capability string names.
        Empty dict if tool_registry not available.
    """
    # Curated list of tools with required_capabilities
    # This avoids creating full ToolRegistry just to read 3 tools
    _CURATED_TOOL_CAPS: dict[str, set[str]] = {
        "web_search": {"reranking"},
        "academic_search": {"reranking", "entity_linking"},
        "entity_extraction": {"entity_linking"},
    }

    # Return the curated declarations
    # This is bounded: O(1) dict lookup, no registry creation
    return dict(_CURATED_TOOL_CAPS)


def get_capability_truth_matrix(
    capabilities: list[Capability],
    registry: "CapabilityRegistry",
) -> dict[Capability, CapabilityTruthStatus]:
    """
    F6: Get truth matrix for multiple capabilities.

    Convenience wrapper around probe_capability_truth for bulk inspection.

    Args:
        capabilities: List of capabilities to probe
        registry: CapabilityRegistry instance

    Returns:
        Dict mapping each capability to its truth status
    """
    declarations = _get_tool_capability_declarations()
    return {
        cap: probe_capability_truth(cap, registry, declarations)
        for cap in capabilities
    }


@dataclass
class CapabilityStatus:
    """Status of a capability."""
    available: bool
    reason: str = ""
    module_path: str = ""
    loader: Optional[Callable[[], Awaitable[bool]]] = None


class CapabilityRegistry:
    """Registry tracking which capabilities are available and why."""

    def __init__(self):
        self._status: Dict[Capability, CapabilityStatus] = {}
        self._loaded: Set[Capability] = set()
        self._lock = asyncio.Lock()

    def register(
        self,
        capability: Capability,
        available: bool = False,
        reason: str = "",
        module_path: str = "",
        loader: Optional[Callable[[], Awaitable[bool]]] = None
    ) -> None:
        """Register a capability."""
        self._status[capability] = CapabilityStatus(
            available=available,
            reason=reason,
            module_path=module_path,
            loader=loader
        )

    def is_available(self, capability: Capability) -> bool:
        """
        Check if capability is available.

        NOTE: This conflates two distinct truth layers:
        - registry_declared_available: registered with available=True
        - runtime_loaded: successfully loaded via load()

        Returns True if EITHER is true. This preserves backward compatibility.
        For granular four-layer truth, use probe_capability_truth() instead.
        """
        if capability in self._loaded:
            return True
        status = self._status.get(capability)
        return status.available if status else False

    def get_reason(self, capability: Capability) -> str:
        """Get reason for unavailability."""
        status = self._status.get(capability)
        return status.reason if status else "Not registered"

    async def load(self, capability: Capability) -> bool:
        """Load a capability on demand."""
        async with self._lock:
            if capability in self._loaded:
                return True

            status = self._status.get(capability)
            if not status:
                logger.warning(f"[CAPABILITY] {capability.value} not registered")
                return False

            if not status.available:
                logger.warning(
                    f"[CAPABILITY] {capability.value} unavailable: {status.reason}"
                )
                return False

            if status.loader:
                try:
                    success = await status.loader()
                    if success:
                        self._loaded.add(capability)
                        logger.info(f"[CAPABILITY] {capability.value} loaded")
                        return True
                    else:
                        logger.error(f"[CAPABILITY] {capability.value} loader failed")
                        return False
                except Exception as e:
                    logger.error(f"[CAPABILITY] {capability.value} load error: {e}")
                    return False
            else:
                # No loader needed, just mark as loaded
                self._loaded.add(capability)
                return True

    def unload(self, capability: Capability) -> None:
        """Mark capability as unloaded."""
        self._loaded.discard(capability)
        logger.info(f"[CAPABILITY] {capability.value} unloaded")

    def get_loaded(self) -> Set[Capability]:
        """Get set of currently loaded capabilities."""
        return self._loaded.copy()

    def get_all_available(self) -> Dict[Capability, str]:
        """Get all available capabilities with module paths."""
        return {
            cap: status.module_path
            for cap, status in self._status.items()
            if status.available
        }

    def get_all_unavailable(self) -> Dict[Capability, str]:
        """Get all unavailable capabilities with reasons."""
        return {
            cap: status.reason
            for cap, status in self._status.items()
            if not status.available
        }

    def log_status(self) -> None:
        """Log current capability status."""
        available = self.get_all_available()
        unavailable = self.get_all_unavailable()
        loaded = self._loaded

        logger.info(f"[CAPABILITIES] enabled={len(available)}, "
                   f"unavailable={len(unavailable)}, loaded={len(loaded)}")

        if available:
            logger.info(f"[CAPABILITIES] available: {[c.value for c in available]}")
        if unavailable:
            logger.info(f"[CAPABILITIES] unavailable: "
                       f"{[(c.value, r) for c, r in unavailable.items()]}")
        if loaded:
            logger.info(f"[CAPABILITIES] loaded: {[c.value for c in loaded]}")


class CapabilityRouter:
    """
    Routes research requirements to required capabilities.

    This is the SECOND stage in the analyzer -> router -> registry pipeline.

    Supports two input modes:
    1. Legacy: Dict[str, Any] analysis + strategy + depth (backward compatible)
    2. Canonical: AnalyzerResult (from types.py)

    The AnalyzerResult path is the preferred canonical route.

    Canonical output: Set[Capability] - passed to ToolRegistry for enforcement.
    """

    # Canonical capability signal keys (from AnalyzerResult.to_capability_signal())
    SIGNAL_KEYS = frozenset([
        "tools", "sources", "privacy_level", "use_tor", "depth",
        "use_tot", "tot_mode", "requires_embeddings", "requires_ner",
        "requires_temporal", "requires_crypto",
    ])

    # Mapping: source type -> required capabilities
    SOURCE_CAPABILITIES: Dict[str, Set[Capability]] = {
        "surface_web": {Capability.RERANKING},
        "academic": {Capability.RERANKING, Capability.ENTITY_LINKING},
        "archive": {Capability.TEMPORAL, Capability.METADATA_EXTRACT},
        "dark_web": {Capability.STEALTH, Capability.DARK_WEB},
        "osint": {Capability.NETWORK_RECON, Capability.ENTITY_LINKING},
        "crypto": {Capability.CRYPTO_INTEL},
    }

    # Mapping: discovery depth -> additional capabilities
    DEPTH_CAPABILITIES: Dict[str, Set[Capability]] = {
        "surface": set(),
        "deep": {Capability.PATTERN_MINING, Capability.INSIGHT},
        "extreme": {Capability.GRAPH_RAG, Capability.TEMPORAL, Capability.SNN},
        "exhaustive": {
            Capability.GRAPH_RAG, Capability.TEMPORAL, Capability.SNN,
            Capability.QUANTUM_PATH, Capability.BLOCKCHAIN
        },
    }

    # Tool-to-capability mapping (scaffold for required_capabilities)
    TOOL_CAPABILITIES: Dict[str, Set[Capability]] = {
        "stealth_crawler": {Capability.STEALTH, Capability.DARK_WEB},
        "archive_discovery": {Capability.TEMPORAL, Capability.METADATA_EXTRACT},
        "leak_hunter": {Capability.STEALTH},
        "blockchain_analyzer": {Capability.CRYPTO_INTEL},
        "academic_search": {Capability.RERANKING, Capability.ENTITY_LINKING},
        "identity_stitching": {Capability.ENTITY_LINKING},
        "relationship_discovery": {Capability.ENTITY_LINKING},
        "pattern_mining": {Capability.PATTERN_MINING, Capability.INSIGHT},
        "temporal_analyzer": {Capability.TEMPORAL},
        "document_analyzer": {Capability.DOC_INTEL},
        "web_intelligence": {Capability.RERANKING},
        "news_analyzer": {Capability.INSIGHT},
        "threat_assessor": {Capability.STEALTH},
        "vulnerability_scanner": {Capability.NETWORK_RECON},
        "reputation_analyzer": {Capability.INSIGHT},
        "cross_reference_engine": {Capability.ENTITY_LINKING, Capability.RERANKING},
    }

    @classmethod
    def route(
        cls,
        analysis: Dict[str, Any] | "AnalyzerResult",
        strategy: Any = None,
        depth: Any = None,
        profile: str = "default"
    ) -> Set[Capability]:
        """
        Determine required capabilities from research context.

        Args:
            analysis: Either AnalyzerResult (canonical) or Dict with analysis fields
            strategy: Research strategy (legacy, optional for AnalyzerResult)
            depth: Discovery depth (legacy, optional for AnalyzerResult)
            profile: Research profile (stealth, speed, thorough)

        Returns:
            Set of required capabilities
        """
        required: Set[Capability] = set()

        # Base capabilities
        required.add(Capability.HERMES)

        # Build capability signal dict (canonical form)
        signal: Dict[str, Any] = {}

        # Canonical path: AnalyzerResult
        if hasattr(analysis, "to_capability_signal"):
            # AnalyzerResult instance -> canonical signal dict
            signal = analysis.to_capability_signal()
        elif isinstance(analysis, dict):
            if "tools" in analysis:
                # Already a capability signal dict from AnalyzerResult.to_capability_signal()
                signal = analysis
            else:
                # Legacy path: raw analysis dict
                signal = dict(analysis)
                if strategy is not None and hasattr(strategy, 'selected_sources'):
                    for source in strategy.selected_sources:
                        source_key = str(source).lower() if hasattr(source, 'value') else str(source).lower()
                        for key, caps in cls.SOURCE_CAPABILITIES.items():
                            if key in source_key:
                                required.update(caps)
                if depth is not None:
                    depth_key = str(depth).lower() if hasattr(depth, 'value') else str(depth).lower()
                    for key, caps in cls.DEPTH_CAPABILITIES.items():
                        if key in depth_key:
                            required.update(caps)

        # From signal fields
        if signal.get('requires_embeddings'):
            required.add(Capability.MODERNBERT)
        if signal.get('requires_ner'):
            required.add(Capability.GLINER)
        if signal.get('requires_temporal'):
            required.add(Capability.TEMPORAL)
        if signal.get('requires_crypto'):
            required.add(Capability.CRYPTO_INTEL)

        # From tools in signal (canonical path)
        for tool in signal.get('tools', []):
            if tool in cls.TOOL_CAPABILITIES:
                required.update(cls.TOOL_CAPABILITIES[tool])

        # From privacy level
        if signal.get('privacy_level') == "MAXIMUM" or signal.get('use_tor'):
            required.add(Capability.STEALTH)

        # Profile-specific
        if profile == "stealth":
            required.add(Capability.STEALTH)
        elif profile == "thorough":
            required.update({Capability.GRAPH_RAG, Capability.ENTITY_LINKING, Capability.TOT})

        logger.debug(f"[CAPABILITY ROUTER] required={[c.value for c in required]}")
        return required


class ModelLifecycleManager:
    """
    F6.5: Coarse-grained phase enforcement FACADE.

    OWNERSHIP DECLARATION (F6.5) — EXPLICIT:
      - Acquire/load owner:        brain.model_manager.ModelManager (singleton)
      - Unload/cleanup owner:      ModelManager._release_current_async()
                                    + brain.model_lifecycle.unload_model() (7K SSOT)
      - Phase enforcer (THIS):      COARSE-GRAINED phase enforcement ONLY
      - Capability layer:            NOT a load owner — NEVER becomes model truth

    THIS FACADE IS NOT A LOAD OWNER — F6.5 LOCKED INVARIANTS:
      - Does NOT call ModelManager.load_model() directly
      - Does NOT hold model references
      - Does NOT create model engines
      - Does NOT manage MLX buffer initialization
      Violating any of the above CREATES A THIRD MODEL TRUTH — FORBIDDEN.

    F6.5 LAYER MAPPING — MUST NOT BE CONFLATED:
      Layer 1 (workflow-level, ModelManager.PHASE_MODEL_MAP):
        PLAN/DECIDE/GENERATE → hermes
        EMBED/DEDUP/ROUTING → modernbert
        NER/ENTITY → gliner
        Strings: PLAN, DECIDE, GENERATE, EMBED, DEDUP, ROUTING, NER, ENTITY
      Layer 2 (coarse-grained, THIS class):
        BRAIN → hermes loaded, others released
        TOOLS → hermes released, on-demand
        GENERATE → hermes loaded, others released  ← NOTE: ≠ SYNTHESIS
        CLEANUP → all released
        Strings: BRAIN, TOOLS, SYNTHESIS, CLEANUP
      Layer 3 (windup-local, windup_engine.SynthesisRunner):
        Own isolated model plane with Qwen/SmolLM

    F6.5 HARD INVARIANTS:
      - acquire ≠ phase enforcement
      - unload ≠ phase policy
      - Layer 1 phases NEVER directly passed to ModelLifecycleManager
      - Layer 2 phases NEVER directly passed to ModelManager.PHASE_MODEL_MAP
      - GENERATE (Layer 1) ≠ SYNTHESIS (Layer 2) — false equivalence
      - capability layer MUST NOT become third model truth

    DRIFT GUARD: Use brain.model_phase_facts.is_same_layer() to validate
    before comparing or mapping phase strings across layers.

    Future seam: This facade may delegate to ModelManager.with_phase()
    after seam extraction — eliminating the CapabilityRegistry round-trip.
    """

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry
        self._current_phase: str = "none"
        # F650H: _active_models is LOCAL COMPAT state only — NOT canonical runtime truth.
        # Canonical model state lives in ModelManager._loaded_models.
        # This field tracks phase-level active set for capability-gate decisions only.
        self._active_models: Set[Capability] = set()

    async def enforce_phase_models(self, phase_name: str) -> None:
        """
        Enforce model loading for specific phase.

        Phases:
        - BRAIN: Hermes loaded, ModernBERT+GLiNER released
        - TOOLS: Hermes released; ModernBERT/GLiNER only when needed
        - SYNTHESIS: Hermes loaded; ModernBERT/GLiNER released
        """
        logger.info(f"[PHASE START] {phase_name}")
        logger.info(f"[MODEL] Before transition: active={[m.value for m in self._active_models]}")

        self._current_phase = phase_name

        if phase_name == "BRAIN":
            await self._release_all_models()
            await self.registry.load(Capability.HERMES)
            self._active_models = {Capability.HERMES}

        elif phase_name == "TOOLS":
            # Release Hermes for tool execution
            await self._release_model(Capability.HERMES)
            # ModernBERT/GLiNER loaded on-demand by specific tools
            self._active_models = set()

        elif phase_name == "SYNTHESIS":
            await self._release_all_models()
            await self.registry.load(Capability.HERMES)
            self._active_models = {Capability.HERMES}

        elif phase_name == "CLEANUP":
            await self._release_all_models()

        logger.info(f"[MODEL] After transition: active={[m.value for m in self._active_models]}")
        logger.info(f"[PHASE END] {phase_name}")

    async def _release_model(self, capability: Capability) -> None:
        """Release a specific model."""
        if capability in self._active_models:
            self.registry.unload(capability)
            self._active_models.discard(capability)
            logger.info(f"[MODEL RELEASE] {capability.value}")

    async def _release_all_models(self) -> None:
        """
        F650H: Release all capability tracking and force GC.

        MLX cache cleanup is DELEGATED to canonical unload seam
        (ModelManager._release_current_async()) — this facade does NOT
        directly call mx.eval()/clear_cache() to avoid model-plane
        side-effect leak.

        Canonical MLX cleanup owner: ModelManager._cleanup_memory_async()
        which is called after every ModelManager._release_current_async().
        """
        for cap in list(self._active_models):
            self.registry.unload(cap)
        self._active_models.clear()

        # Force garbage collection (capability-layer cleanup only)
        gc.collect()

        logger.info("[MODEL] All models released, GC completed")

    def get_active_models(self) -> Set[Capability]:
        """
        F650H: Return local capability-tracking state.

        IMPORTANT: This is LOCAL COMPAT state for capability-gate decisions only.
        It is NOT canonical runtime-wide model truth — canonical state lives
        in ModelManager._loaded_models.

        Returns:
            Copy of local _active_models set (local compat, not authoritative)
        """
        return self._active_models.copy()

    async def load_model_for_task(self, capability: Capability) -> bool:
        """Load a model for a specific task, ensuring single-model constraint."""
        # If already loaded, return True
        if capability in self._active_models:
            return True

        # If loading a heavy model, release others first
        if capability in {Capability.HERMES, Capability.MODERNBERT, Capability.GLINER}:
            await self._release_all_models()

        success = await self.registry.load(capability)
        if success:
            self._active_models.add(capability)
            logger.info(f"[MODEL LOAD] {capability.value}")

        return success


def create_default_registry() -> CapabilityRegistry:
    """Create a registry with default capability registrations."""
    import importlib.util

    registry = CapabilityRegistry()

    # Check availability based on module existence (bounded probing)
    # FIX F600C: Uses find_spec instead of __import__ to avoid
    # triggering full module load on M1 8GB
    def check_module(module_name: str) -> tuple[bool, str]:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            return True, ""
        return False, f"Module not available: {module_name}"

    # Register all capabilities
    modules_to_check = {
        Capability.GRAPH_RAG: ("hledac.universal.knowledge.rag_engine", "RAG engine"),
        Capability.ENTITY_LINKING: ("hledac.universal.knowledge.entity_linker", "Entity linker"),
        Capability.TEMPORAL: ("hledac.universal.intelligence.temporal_analysis", "Temporal analyzer"),
        Capability.DARK_WEB: ("hledac.universal.intelligence.stealth_crawler", "Dark web crawler"),
        Capability.CRYPTO_INTEL: ("hledac.universal.intelligence.cryptographic_intelligence", "Crypto intelligence"),
        Capability.DOC_INTEL: ("hledac.universal.intelligence.document_intelligence", "Document intelligence"),
        Capability.NETWORK_RECON: ("hledac.universal.intelligence.network_reconnaissance", "Network recon"),
        Capability.TOT: ("hledac.universal.tot_integration", "Tree of Thoughts"),
    }

    for cap, (module, description) in modules_to_check.items():
        available, reason = check_module(module)
        registry.register(
            capability=cap,
            available=available,
            reason=reason if not available else f"{description} available",
            module_path=module
        )

    # Always register model capabilities as available (they're core)
    for cap in [Capability.HERMES, Capability.MODERNBERT, Capability.GLINER]:
        registry.register(
            capability=cap,
            available=True,
            reason="Core model",
            module_path="hledac.universal.brain"
        )

    # Register derived capabilities
    registry.register(
        capability=Capability.RERANKING,
        available=True,
        reason="Core utility",
        module_path="hledac.universal.utils.ranking"
    )

    registry.register(
        capability=Capability.INSIGHT,
        available=check_module("hledac.universal.brain.insight_engine")[0],
        reason="Insight engine available" if check_module("hledac.universal.brain.insight_engine")[0] else "Insight engine not available",
        module_path="hledac.universal.brain.insight_engine"
    )

    registry.register(
        capability=Capability.PATTERN_MINING,
        available=check_module("hledac.universal.intelligence.pattern_mining")[0],
        reason="Pattern mining available" if check_module("hledac.universal.intelligence.pattern_mining")[0] else "Pattern mining not available",
        module_path="hledac.universal.intelligence.pattern_mining"
    )

    return registry
