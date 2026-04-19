"""
autonomous_orchestrator — RE-EXPORT FACADE (8VC)
=============================================

.. deprecated::
    This module has been migrated to ``legacy/autonomous_orchestrator.py``.
    This file is kept for backward compatibility only.

CONTAINMENT METADATA (Sprint F13)
=================================
runtime_status: ACTIVE_BACKWARD_COMPAT
    Facade slouží jako import surface pro __init__.py, orchestrator/, smoke_runner.py a testy.
    NENÍ v production pipeline (__main__.py → SprintScheduler).

authority_status: NON_CANONICAL_FACADE
    Tento modul NENÍ canonical owner. Veškerá implementace žije v legacy/.
    False-authority risk: modul vypadá jako primary orchestrator, ale není.

replacement_owner:
    - legacy.autonomous_orchestrator (implementace, 31k+ lines)
    - runtime.sprint_scheduler (production orchestrace, 30min sprint cycle)

migration_blocker:
    - __init__.py re-export chain (řádky 53-71): 15+ names
    - orchestrator/__init__.py: re-export FullyAutonomousOrchestrator
    - smoke_runner.py: import FullyAutonomousOrchestrator
    - Testy: probe_3c, probe_3b, probe_5a a další přímo importují zde

removal_precondition:
    1. Všichni consumeri přesměrováni na legacy.autonomous_orchestrator nebo přímé importy
    2. __init__.py updated: import z legacy/ místo .
    3. orchestrator/__init__.py updated
    4. smoke_runner.py updated
    5. Všechny testy prošly bez facade

runtime_impact_if_removed:
    VYSOKÝ — okamžitý break:
    - __init__.py: "from .autonomous_orchestrator import ..." selže
    - orchestrator/: "from ..autonomous_orchestrator import FullyAutonomousOrchestrator" selže
    - smoke_runner.py: import selže
    - Všechny probe testy (probe_3c, probe_3b, probe_5a, ...) fail

donor_capability_list:
    Hlavní třídy/funkce (COMPAT EXPORTS):
    - FullyAutonomousOrchestrator
    - autonomous_research / create_autonomous_orchestrator
    - deep_research
    - UnifiedToolRegistry, ToolCategory, ToolCapability
    - AutonomousWorkflowEngine, WorkflowState
    - ResilientExecutionManager
    - AdmissionResult, BacklogCandidate, TokenBucket
    - WelfordStats, ReservoirSampler, ThermalState
    - IterationTrace, NetworkReconRunTrace, CapabilityHealth
    - SynthesisCompression, SimHash

    Internal managery (pro orchestrator/ submoduly):
    - _SecurityManager, _ResearchManager, _ToolRegistryManager
    - _StateManager, _BrainManager, _ForensicsManager
    - _IntelligenceManager, _SynthesisManager

    Enumy:
    - DiscoveryDepth, ResearchPhase, SourceType
    - AutonomousStrategy, ResearchSource, ResearchFinding
    - ComprehensiveResearchResult
"""

import sys
import os

# Sprint 8VC: CRITICAL - Register this module in sys.modules BEFORE any other imports
# This prevents __init__.py from failing when it does "from .autonomous_orchestrator import ..."
# The key: Python checks sys.modules first, so if we're already there, it won't re-import

_legacy_path = os.path.join(os.path.dirname(__file__), "legacy", "autonomous_orchestrator.py")

# Create a placeholder so recursive imports find this module already in sys.modules
import types
_facade_mod = types.ModuleType("hledac.universal.autonomous_orchestrator")
_facade_mod.__file__ = __file__
_facade_mod.__package__ = "hledac.universal"
_facade_mod.__name__ = "hledac.universal.autonomous_orchestrator"
sys.modules["hledac.universal.autonomous_orchestrator"] = _facade_mod

# Now do the actual loading - this WON'T trigger __init__.py re-entry
# because hledac.universal.autonomous_orchestrator is already in sys.modules
import warnings
warnings.warn(
    "autonomous_orchestrator has been migrated to legacy/. "
    "Import FullyAutonomousOrchestrator from runtime/sprint_scheduler.py instead. "
    "This facade will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
)

# Load the legacy module directly
import importlib.util
_spec = importlib.util.spec_from_file_location("legacy.autonomous_orchestrator", _legacy_path)
assert _spec is not None, f"Failed to load spec for {_legacy_path}"
_legacy_mod = importlib.util.module_from_spec(_spec)
_legacy_mod.__package__ = "hledac.universal"
_legacy_mod.__path__ = [os.path.dirname(__file__)]
sys.modules["legacy.autonomous_orchestrator"] = _legacy_mod
assert _spec.loader is not None
_spec.loader.exec_module(_legacy_mod)

# Copy all exported names to this facade module
_for_export = [
    "FullyAutonomousOrchestrator",
    "autonomous_research",
    "DiscoveryDepth",
    "ResearchPhase",
    "SourceType",
    "AutonomousStrategy",
    "ResearchSource",
    "ResearchFinding",
    "ComprehensiveResearchResult",
    "ResilientExecutionManager",
    "AutonomousWorkflowEngine",
    "WorkflowState",
    "UnifiedToolRegistry",
    "ToolCategory",
    "ToolCapability",
    "deep_research",
    "create",
    "AdmissionResult",
    "BacklogCandidate",
    "TokenBucket",
    "WelfordStats",
    "ReservoirSampler",
    "ThermalState",
    "IterationTrace",
    "NetworkReconRunTrace",
    "CapabilityHealth",
    "SynthesisCompression",
    "SimHash",
    "MicroPlan",
    "MICROPLAN_DEADLINE_SEC",
    "normalize_url",
    # Additional exports needed by tests
    "BudgetManager",
    "CONTRADICTION_WHITELIST",
    "Checkpoint",
    "CheckpointManager",
    "DomainLimiter",
    "DomainStats",
    "DomainStatsManager",
    "FrontierEntry",
    "HttpCacheEntry",
    "HttpDiskCache",
    "MAX_HOST_PENALTIES",
    "MetadataExtractor",
    "RecrawlItem",
    "RecrawlPlanner",
    "SnapshotStorage",
    "TimingProfile",
    "UrlFrontier",
    "_ACQUISITION_PHASE_1_2_ALLOWED",
    "_ACQUISITION_PHASE_3_RESCUE_ONLY",
    "_ACQUISITION_PHASE_4_NONE",
    "_ARCHIVE_MIRRORS",
    "_COMMONS_CRAWL_MAX_LINES",
    "_CROSS_ARCHIVE_DIGEST_MAX",
    "_CT_DISCOVERY_MAX_SUBDOMAINS",
    "_CT_DISCOVERY_TIMEOUT_SEC",
    "_FINAL_SYNTHESIS_MAX_CHARS",
    "_FINAL_SYNTHESIS_MAX_CLAIMS",
    "_FINAL_SYNTHESIS_MAX_GAPS",
    "_FORCE_GC_BEFORE_SYNTHESIS",
    "_GAP_CHECK_BUDGET",
    "_MemoryManager",
    "_NECROMANCER_BUDGET_PER_SPRINT",
    "_NECROMANCER_MAX_ATTEMPTS",
    "_ONION_BUDGET_PER_SPRINT",
    "_ONION_PREFLIGHT_CACHE_TTL_SEC",
    "_PRF_MAX_EXPANSION_TERMS",
    "_PRF_STOP_WORDS",
    "_WAYBACK_CDX_MAX_LINES",
    "_WAYBACK_QUICK_TIMEOUT_SEC",
    "_check_tor_available_cached",
    "_prf_expand",
    "_validate_archive_content",
    # Additional exports from coordinators/
    "AgentCoordinationEngine",
    "AgentType",
    "AgentCapability",
    "TaskRequest",
    "TaskPriority",
    "CoordinationStrategy",
    "ResearchOptimizer",
    "OptimizationConfig",
    "OptimizationStrategy",
    "FetchCoordinator",
    "apply_fcntl_nocache",
    "ClaimsCoordinator",
    "GraphCoordinator",
    "ArchiveCoordinator",
    "ReciprocalRankFusion",
    "QueryExpander",
    "LanguageDetector",
    "SimHash",
    "MetadataExtractor",
    "MLX_AVAILABLE",
    # Internal managers used by orchestrator/
    "_SecurityManager",
    "_ResearchManager",
    "_ToolRegistryManager",
    "_StateManager",
    "_BrainManager",
    "_ForensicsManager",
    "_IntelligenceManager",
    "_SynthesisManager",
]

for _name in _for_export:
    _val = getattr(_legacy_mod, _name, None)
    if _val is not None:
        setattr(_facade_mod, _name, _val)
        globals()[_name] = _val

create_autonomous_orchestrator = getattr(_legacy_mod, "autonomous_research", None)
globals()["create_autonomous_orchestrator"] = create_autonomous_orchestrator
setattr(_facade_mod, "create_autonomous_orchestrator", create_autonomous_orchestrator)

__all__ = _for_export + ["create_autonomous_orchestrator"]
