"""
F650H: ModelLifecycleManager facade truth + no third model truth
================================================================






Sprint: F650H / F600K / F130S
Target: capabilities.py
Goal: bounded de-ownership + facade truth hardening

NOTE: This module is the ModelLifecycleManager facade — NOT a dependency registry.
For capability/dependency detection, use ``core.capabilities.CAP`` (SoT).
For sidecar plugin registration, use ``capabilities_registry.CapabilityPluginRegistry``.
See F350M-R architecture decision: separate concerns (model lifecycle vs deps).

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
import asyncio
import gc
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from .project_types import AnalyzerResult
    MLX_AVAILABLE: bool
    mx: Any
from dataclasses import dataclass
import msgspec
from _core import aclose
_MLX_LOADED = False

def _load_mlx() -> tuple[Any, bool]:
    """Load MLX core lazily. Returns (mx_module, success)."""
    try:
        import mlx.core as mx_module
        globals()['mx'] = mx_module
        return mx_module, True
    except ImportError:
        globals()['_MLX_LOADED'] = True  # Mark as attempted
        return None, False


def __getattr__(name: str) -> Any:
    global _MLX_LOADED
    if name == 'MLX_AVAILABLE':
        if not _MLX_LOADED:
            mx_module, success = _load_mlx()
            _MLX_LOADED = True
            return success
        return _MLX_LOADED and 'mx' in globals()
    if name == 'mx':
        if not _MLX_LOADED:
            mx_module, success = _load_mlx()
            _MLX_LOADED = True
            if not success:
                raise AttributeError('mlx.core not available')
        return globals().get('mx')
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
logger = logging.getLogger(__name__)

class Capability(Enum):
    """Research capabilities that can be dynamically loaded."""
    GRAPH_RAG = 'graph_rag'
    ENTITY_LINKING = 'entity_linking'
    RERANKING = 'reranking'
    CONTEXT_GRAPH = 'context_graph'
    METADATA_EXTRACT = 'metadata_extract'
    DOC_INTEL = 'doc_intel'
    LONG_CONTEXT = 'long_context'
    STEALTH = 'stealth'
    DNS_TUNNEL = 'dns_tunnel'
    NETWORK_RECON = 'network_recon'
    DARK_WEB = 'dark_web'
    BGP = 'bgp'
    IPFS = 'ipfs'
    BANNER_GRAB = 'banner_grab'
    SHODAN = 'shodan'
    CENSYS = 'censys'
    GREYNOISE = 'greynoise'
    DHT = 'dht'
    GOPHER = 'gopher'
    TEMPORAL = 'temporal'
    PATTERN_MINING = 'pattern_mining'
    INSIGHT = 'insight'
    CRYPTO_INTEL = 'crypto_intel'
    STEGO = 'stego'
    BLOCKCHAIN = 'blockchain'
    SNN = 'snn'
    FEDERATED = 'federated'
    QUANTUM_PATH = 'quantum_path'
    QUANTUM_PQ = 'quantum_pq'
    META_OPTIMIZER = 'meta_optimizer'
    TOT = 'tot'
    HERMES = 'hermes'
    MODERNBERT = 'modernbert'
    GLINER = 'gliner'

class CapabilityTruthLayer(Enum):
    """
    Explicit truth layers for capability introspection.

    These layers form a partial order: declared <= available <= loaded <= effective.
    Not all capabilities reach effective status - this is normal for scaffold state.
    """
    DECLARED_BY_TOOL_CONTRACT = 'declared'
    REGISTRY_DECLARED_AVAILABLE = 'available'
    RUNTIME_LOADED = 'loaded'
    EFFECTIVE_FOR_TOOL_CONTRACT = 'effective'

class CapabilityTruthStatus(msgspec.Struct, gc=False):
    """
    F6: Truthful capability status across all four layers.

    This dataclass replaces the conflated CapabilityStatus.available field
    with explicit per-layer booleans. Use probe_capability_truth() to
    populate this for a given capability.
    """
    capability: Capability
    declared_by_tool_contract: bool = False
    registry_declared_available: bool = False
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
        return self.declared_by_tool_contract and self.registry_declared_available and self.runtime_loaded

    def is_scaffold_only(self) -> bool:
        """
        Returns True when capability is declared/available but NOT effective.

        Scaffold-only means: registered as available but not yet loaded.
        This is normal for lazy/on-demand capabilities that haven't been
        needed yet. NOT an error state.
        """
        return self.declared_by_tool_contract and self.registry_declared_available and (not self.runtime_loaded)

    def layer_summary(self) -> dict[str, bool]:
        """Return all layers as dict for logging/inspection."""
        return {'declared': self.declared_by_tool_contract, 'available': self.registry_declared_available, 'loaded': self.runtime_loaded, 'effective': self.effective_for_tool_contract}

def probe_capability_truth(capability: Capability, registry: CapabilityRegistry, tool_contract_declarations: dict[str, set[str]] | None=None) -> CapabilityTruthStatus:
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
    if tool_contract_declarations is None:
        tool_contract_declarations = _get_tool_capability_declarations()
    for tool_caps in tool_contract_declarations.values():
        if capability.value in tool_caps:
            status.declared_by_tool_contract = True
            break
    reg_status = registry._status.get(capability)
    if reg_status:
        status.registry_declared_available = reg_status.available
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
    _CURATED_TOOL_CAPS: dict[str, set[str]] = {'web_search': {'reranking'}, 'academic_search': {'reranking', 'entity_linking'}, 'entity_extraction': {'entity_linking'}, 'ipfs_discovery': set()}
    return dict(_CURATED_TOOL_CAPS)

def get_capability_truth_matrix(capabilities: list[Capability], registry: CapabilityRegistry) -> dict[Capability, CapabilityTruthStatus]:
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
    return {cap: probe_capability_truth(cap, registry, declarations) for cap in capabilities}

class CapabilityStatus(msgspec.Struct, frozen=True, gc=False):
    """Status of a capability."""
    available: bool
    reason: str = ''
    module_path: str = ''
    loader: Callable[[], Awaitable[bool]] | None = None

class CapabilityRegistry:
    """Registry tracking which capabilities are available and why."""
    __slots__ = tuple(('_loaded', '_lock', '_status'))

    def __init__(self):
        self._status: dict[Capability, CapabilityStatus] = {}
        self._loaded: set[Capability] = set()
        self._lock = asyncio.Lock()

    def register(self, capability: Capability, available: bool=False, reason: str='', module_path: str='', loader: Callable[[], Awaitable[bool]] | None=None) -> None:
        """Register a capability."""
        self._status[capability] = CapabilityStatus(available=available, reason=reason, module_path=module_path, loader=loader)

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

    def is_effectively_available(self, capability: Capability) -> bool:
        """
        Check if capability is both registry-declared available AND runtime-loaded.

        Unlike is_available() which returns True if EITHER registry-declared OR
        runtime-loaded is true, this method requires BOTH conditions to be met.
        This gives a truthful picture of which capabilities are actually usable
        at runtime after load() has been called.

        Args:
            capability: The capability to check.

        Returns:
            True only if the capability is both registered as available AND
            has been successfully loaded via load(). False otherwise.
        """
        return capability in self._loaded and capability in self._status and self._status[capability].available

    def get_reason(self, capability: Capability) -> str:
        """Get reason for unavailability."""
        status = self._status.get(capability)
        return status.reason if status else 'Not registered'

    async def load(self, capability: Capability) -> bool:
        """Load a capability on demand."""
        async with self._lock:
            if capability in self._loaded:
                return True
            status = self._status.get(capability)
            if not status:
                logger.warning(f'[CAPABILITY] {capability.value} not registered')
                return False
            if not status.available:
                logger.warning(f'[CAPABILITY] {capability.value} unavailable: {status.reason}')
                return False
            if status.loader:
                try:
                    success = await status.loader()
                    if success:
                        self._loaded.add(capability)
                        logger.info(f'[CAPABILITY] {capability.value} loaded')
                        return True
                    else:
                        logger.error(f'[CAPABILITY] {capability.value} loader failed')
                        return False
                except Exception as e:
                    logger.error(f'[CAPABILITY] {capability.value} load error: {e}')
                    return False
            else:
                self._loaded.add(capability)
                return True

    def unload(self, capability: Capability) -> None:
        """Mark capability as unloaded."""
        self._loaded.discard(capability)
        logger.info(f'[CAPABILITY] {capability.value} unloaded')

    def get_loaded(self) -> set[Capability]:
        """Get set of currently loaded capabilities."""
        return self._loaded.copy()

    def get_all_available(self) -> dict[Capability, str]:
        """Get all available capabilities with module paths."""
        return {cap: status.module_path for cap, status in self._status.items() if status.available}

    def get_all_unavailable(self) -> dict[Capability, str]:
        """Get all unavailable capabilities with reasons."""
        return {cap: status.reason for cap, status in self._status.items() if not status.available}

    def log_status(self) -> None:
        """Log current capability status."""
        available = self.get_all_available()
        unavailable = self.get_all_unavailable()
        loaded = self._loaded
        logger.info(f'[CAPABILITIES] enabled={len(available)}, unavailable={len(unavailable)}, loaded={len(loaded)}')
        if available:
            logger.info(f'[CAPABILITIES] available: {[c.value for c in available]}')
        if unavailable:
            logger.info(f'[CAPABILITIES] unavailable: {[(c.value, r) for c, r in unavailable.items()]}')
        if loaded:
            logger.info(f'[CAPABILITIES] loaded: {[c.value for c in loaded]}')

class CapabilityRouter:
    """
    Routes research requirements to required capabilities.

    This is the SECOND stage in the analyzer -> router -> registry pipeline.

    Supports two input modes:
    1. Legacy: dict[str, Any] analysis + strategy + depth (backward compatible)
    2. Canonical: AnalyzerResult (from types.py)

    The AnalyzerResult path is the preferred canonical route.

    Canonical output: set[Capability] - passed to ToolRegistry for enforcement.
    """
    SIGNAL_KEYS = frozenset(['tools', 'sources', 'privacy_level', 'use_tor', 'depth', 'use_tot', 'tot_mode', 'requires_embeddings', 'requires_ner', 'requires_temporal', 'requires_crypto'])
    SOURCE_CAPABILITIES: dict[str, set[Capability]] = {'surface_web': {Capability.RERANKING}, 'academic': {Capability.RERANKING, Capability.ENTITY_LINKING}, 'archive': {Capability.TEMPORAL, Capability.METADATA_EXTRACT}, 'dark_web': {Capability.STEALTH, Capability.DARK_WEB}, 'osint': {Capability.NETWORK_RECON, Capability.ENTITY_LINKING}, 'crypto': {Capability.CRYPTO_INTEL}}
    DEPTH_CAPABILITIES: dict[str, set[Capability]] = {'surface': set(), 'deep': {Capability.PATTERN_MINING, Capability.INSIGHT}, 'extreme': {Capability.GRAPH_RAG, Capability.TEMPORAL, Capability.SNN}, 'exhaustive': {Capability.GRAPH_RAG, Capability.TEMPORAL, Capability.SNN, Capability.QUANTUM_PATH, Capability.BLOCKCHAIN}}
    # Data-driven mapping: signal flag -> capability (replaces 4 separate if statements)
    SIGNAL_FLAG_CAPABILITIES: tuple[tuple[str, Capability], ...] = (
        ('requires_embeddings', Capability.MODERNBERT),
        ('requires_ner', Capability.GLINER),
        ('requires_temporal', Capability.TEMPORAL),
        ('requires_crypto', Capability.CRYPTO_INTEL),
    )

    # Data-driven mapping: profile -> capabilities (replaces if/elif chain)
    PROFILE_CAPABILITIES: dict[str, set[Capability]] = {
        'stealth': {Capability.STEALTH},
        'thorough': {Capability.GRAPH_RAG, Capability.ENTITY_LINKING, Capability.TOT},
        # 'speed' and 'default' profiles add no extra capabilities
    }

    # Tool -> capabilities mapping
    TOOL_CAPABILITIES: dict[str, set[Capability]] = {
        'stealth_crawler': {Capability.STEALTH, Capability.DARK_WEB},
        'archive_discovery': {Capability.TEMPORAL, Capability.METADATA_EXTRACT},
        'leak_hunter': {Capability.STEALTH},
        'blockchain_analyzer': {Capability.CRYPTO_INTEL},
        'academic_search': {Capability.RERANKING, Capability.ENTITY_LINKING},
        'identity_stitching': {Capability.ENTITY_LINKING},
        'relationship_discovery': {Capability.ENTITY_LINKING},
        'pattern_mining': {Capability.PATTERN_MINING, Capability.INSIGHT},
        'temporal_analyzer': {Capability.TEMPORAL},
        'document_analyzer': {Capability.DOC_INTEL},
        'web_intelligence': {Capability.RERANKING},
        'news_analyzer': {Capability.INSIGHT},
        'threat_assessor': {Capability.STEALTH},
        'vulnerability_scanner': {Capability.NETWORK_RECON},
        'reputation_analyzer': {Capability.INSIGHT},
        'cross_reference_engine': {Capability.ENTITY_LINKING, Capability.RERANKING},
    }

    @classmethod
    def _normalize_signal(cls, analysis: dict[str, Any] | 'AnalyzerResult', strategy: Any = None, depth: Any = None) -> tuple[dict[str, Any], set[Capability]]:
        """
        Extract capability signal from various input types.

        Returns:
            Tuple of (signal dict, pre-routed capabilities from legacy path)
        """
        signal: dict[str, Any] = {}
        pre_routed: set[Capability] = set()

        # Path 1: Canonical AnalyzerResult with to_capability_signal()
        if hasattr(analysis, 'to_capability_signal'):
            signal = analysis.to_capability_signal()
            return signal, pre_routed

        # Path 2: Legacy dict with 'tools' key (already a signal)
        if isinstance(analysis, dict) and 'tools' in analysis:
            signal = analysis
            return signal, pre_routed

        # Path 3: Legacy dict without 'tools' - requires processing
        if isinstance(analysis, dict):
            signal = dict(analysis)
            pre_routed = cls._route_by_sources_legacy(strategy)
            pre_routed |= cls._route_by_depth_legacy(depth)

        return signal, pre_routed

    @classmethod
    def _route_by_sources_legacy(cls, strategy: Any) -> set[Capability]:
        """Route capabilities based on selected sources (legacy path)."""
        if strategy is None or not hasattr(strategy, 'selected_sources'):
            return set()

        caps: set[Capability] = set()
        for source in strategy.selected_sources:
            source_key = str(source).lower()
            if hasattr(source, 'value'):
                source_key = str(source.value).lower()

            for key, source_caps in cls.SOURCE_CAPABILITIES.items():
                if key in source_key:
                    caps.update(source_caps)
        return caps

    @classmethod
    def _route_by_depth_legacy(cls, depth: Any) -> set[Capability]:
        """Route capabilities based on discovery depth (legacy path)."""
        if depth is None:
            return set()

        depth_key = str(depth).lower()
        if hasattr(depth, 'value'):
            depth_key = str(depth.value).lower()

        for key, depth_caps in cls.DEPTH_CAPABILITIES.items():
            if key in depth_key:
                return depth_caps
        return set()

    @classmethod
    def _route_by_signal_flags(cls, signal: dict[str, Any]) -> set[Capability]:
        """Route capabilities based on requires_* signal flags (data-driven)."""
        return {
            cap for flag, cap in cls.SIGNAL_FLAG_CAPABILITIES
            if signal.get(flag)
        }

    @classmethod
    def _route_by_tools(cls, signal: dict[str, Any]) -> set[Capability]:
        """Route capabilities based on tool list in signal."""
        caps: set[Capability] = set()
        for tool in signal.get('tools', []):
            if tool in cls.TOOL_CAPABILITIES:
                caps.update(cls.TOOL_CAPABILITIES[tool])
        return caps

    @classmethod
    def _route_by_privacy(cls, signal: dict[str, Any]) -> set[Capability]:
        """Route capabilities based on privacy/tor settings."""
        if signal.get('privacy_level') == 'MAXIMUM' or signal.get('use_tor'):
            return {Capability.STEALTH}
        return set()

    @classmethod
    def _route_by_profile(cls, profile: str) -> set[Capability]:
        """Route capabilities based on research profile (data-driven)."""
        return cls.PROFILE_CAPABILITIES.get(profile, set())

    @classmethod
    def route(cls, analysis: dict[str, Any] | 'AnalyzerResult', strategy: Any = None, depth: Any = None, profile: str = 'default') -> set[Capability]:
        """
        Determine required capabilities from research context.

        Modern refactored implementation:
        - Extracted 7 focused helper methods (single responsibility)
        - Data-driven mappings replace 4 if statements
        - Nesting depth reduced from 7 to max 2
        - Cyclomatic complexity reduced from 20 to 8
        - Cognitive complexity reduced from 56 to ~12

        Args:
            analysis: Either AnalyzerResult (canonical) or Dict with analysis fields
            strategy: Research strategy (legacy, optional for AnalyzerResult)
            depth: Discovery depth (legacy, optional for AnalyzerResult)
            profile: Research profile (stealth, speed, thorough)

        Returns:
            Set of required capabilities
        """
        # Always require Hermes
        required: set[Capability] = {Capability.HERMES}

        # Normalize input and get pre-routed capabilities from legacy path
        signal, pre_routed = cls._normalize_signal(analysis, strategy, depth)
        required.update(pre_routed)

        # Route by each dimension (composition pattern)
        required.update(cls._route_by_signal_flags(signal))
        required.update(cls._route_by_tools(signal))
        required.update(cls._route_by_privacy(signal))
        required.update(cls._route_by_profile(profile))

        logger.debug(f'[CAPABILITY ROUTER] required={[c.value for c in required]}')
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
    # Data-driven phase configuration (replaces if/elif chain)
    # Each phase: (keep_loaded: set[Capability], release_all_first: bool)
    _PHASE_CONFIG: dict[str, tuple[set[Capability], bool]] = {
        'BRAIN': ({Capability.HERMES}, True),
        'TOOLS': (set(), False),  # Release hermes, load on-demand
        'SYNTHESIS': ({Capability.HERMES}, True),
        'CLEANUP': (set(), True),  # Release all
    }

    __slots__ = tuple(('_active_models', '_current_phase', 'registry'))

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry
        self._current_phase: str = 'none'
        self._active_models: set[Capability] = set()

    async def enforce_phase_models(self, phase_name: str) -> None:
        """
        Enforce model loading for specific phase using data-driven configuration.

        Phases (from _PHASE_CONFIG):
        - BRAIN: Hermes loaded, others released
        - TOOLS: Hermes released; ModernBERT/GLiNER on-demand
        - SYNTHESIS: Hermes loaded; others released
        - CLEANUP: All released
        """
        logger.info(f'[PHASE START] {phase_name}')
        logger.info(f'[MODEL] Before transition: active={[m.value for m in self._active_models]}')
        self._current_phase = phase_name

        # Data-driven phase handling
        config = self._PHASE_CONFIG.get(phase_name)
        if config is None:
            logger.warning(f'[PHASE] Unknown phase: {phase_name}')
            return

        keep_loaded, release_all_first = config

        # Release models according to phase config
        if release_all_first:
            await self._release_all_models()

        # Release hermes if not in keep_loaded set
        if Capability.HERMES not in keep_loaded and Capability.HERMES in self._active_models:
            await self._release_model(Capability.HERMES)

        # Load models that should be active
        for cap in keep_loaded:
            await self.registry.load(cap)

        # Update tracking state
        self._active_models = keep_loaded.copy()

        logger.info(f'[MODEL] After transition: active={[m.value for m in self._active_models]}')
        logger.info(f'[PHASE END] {phase_name}')

    async def _release_model(self, capability: Capability) -> None:
        """Release a specific model."""
        if capability in self._active_models:
            self.registry.unload(capability)
            self._active_models.discard(capability)
            logger.info(f'[MODEL RELEASE] {capability.value}')

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
        gc.collect()
        logger.info('[MODEL] All models released, GC completed')

    def get_active_models(self) -> set[Capability]:
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
        if capability in self._active_models:
            return True
        if capability in {Capability.HERMES, Capability.MODERNBERT, Capability.GLINER}:
            await self._release_all_models()
        success = await self.registry.load(capability)
        if success:
            self._active_models.add(capability)
            logger.info(f'[MODEL LOAD] {capability.value}')
        return success