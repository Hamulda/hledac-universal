"""
Coordinators Domain Catalog
===========================

Provides structured access to coordinators via domain grouping.
Lazy loading ensures only needed coordinators are imported.

Usage:
    from hledac.universal.coordinators import catalog

    # List all domains
    catalog.domains

    # Get coordinators by domain
    catalog.get('core')       # Core coordinators
    catalog.get('advanced')   # Advanced coordinators
    catalog.get('optimization')  # Optimization coordinators

    # Lazy-load a specific coordinator
    MemoryCoordinator = catalog.load('UniversalMemoryCoordinator')

Domain Groups:
    - core: Research, Execution, Security, Monitoring, Memory, Validation
    - advanced: AdvancedResearch, Swarm, MetaReasoning, PrivacyEnhanced
    - optimization: Performance, Benchmark, Resource, ResearchOptimizer
    - infrastructure: Base, Registry, Mixins
    - specialized: Fetch, Graph, Archive, Claims, Multimodal, Render, AgentCoordination
"""


import importlib
from typing import Any, Self

# Domain definitions - coordinator name -> module mapping
_DOMAIN_MODULES: dict[str, dict[str, str]] = {
    'core': {
        'UniversalResearchCoordinator': '.research_coordinator',
        'UniversalExecutionCoordinator': '.execution_coordinator',
        'UniversalSecurityCoordinator': '.security_coordinator',
        'UniversalMonitoringCoordinator': '.monitoring_coordinator',
        'UniversalMemoryCoordinator': '.memory_coordinator',
        'UniversalValidationCoordinator': '.validation_coordinator',
    },
    'advanced': {
        'UniversalSwarmCoordinator': '.swarm_coordinator',
        'UniversalMetaReasoningCoordinator': '.meta_reasoning_coordinator',
        'PrivacyEnhancedResearch': '.privacy_enhanced_research',
    },
    'optimization': {
        'AgentPerformanceOptimizer': '.performance_coordinator',
        # AgentBenchmarker archived 2026-07-05 — use benchmarks/ scripts
        'IntelligentResourceAllocator': '.resource_allocator',
        'ResearchOptimizer': '.research_optimizer',
    },
    'infrastructure': {
        'UniversalCoordinator': '.base',
        'OperationTrackingMixin': '.base',
        'MemoryPressureLevel': '.enums',
    },
    'specialized': {
        'FetchCoordinator': '.fetch_coordinator',
        'GraphCoordinator': '.graph_coordinator',
        'ArchiveCoordinator': '.archive_coordinator',
        'ClaimsCoordinator': '.claims_coordinator',
        'MultimodalCoordinator': '.multimodal_coordinator',
        'RenderCoordinator': '.render_coordinator',
        'AgentCoordinationEngine': '.agent_coordination_engine',
    },
}

# Additional exports per coordinator (non-Coordinator classes)
_COORDINATOR_EXPORTS: dict[str, list[str]] = {
    'UniversalMemoryCoordinator': ['MemoryAllocation', 'MemoryStatistics', 'MemoryZone'],
    'UniversalValidationCoordinator': ['ValidationSeverity', 'OutputFormat', 'ValidationResult', 'CleaningResult'],
    'UniversalResearchCoordinator': ['ExcavationConfig', 'ExcavationStrategy', 'ResearchPaper', 'ResearchThread', 'MetaPattern', 'ResearchTheory', 'ResearchDepth', 'HierarchicalPlan'],  # noqa: E501
    'UniversalSwarmCoordinator': ['SwarmState', 'SwarmMetrics', 'AdaptiveStrategy', 'SwarmAgent'],
    'UniversalMetaReasoningCoordinator': ['ReasoningStrategy', 'ReasoningStep', 'ReasoningChain', 'ThoughtNode'],
    'AgentPerformanceOptimizer': ['AgentPool', 'IntelligentLoadBalancer', 'AsyncExecutionOptimizer', 'LoadBalancingConfig', 'OptimizationReport', 'AgentMetrics'],  # noqa: E501
    'IntelligentResourceAllocator': ['ResourceRequest', 'ResourceAllocation', 'ResourceType', 'Priority'],
    'AgentCoordinationEngine': ['AgentType', 'TaskPriority', 'AgentCapability', 'AgentPerformance', 'TaskRequest', 'TaskResult', 'CoordinationStrategy', 'coordinated_search'],  # noqa: E501
    'PrivacyEnhancedResearch': ['PrivacyConfig', 'DataRetention', 'AuditRecord', 'AnonymizedRequest', 'SanitizedResult', 'private_research'],  # noqa: E501
    'ResearchOptimizer': ['OptimizationConfig', 'OptimizationStrategy', 'CachePolicy', 'QueryMetrics', 'OptimizedResult', 'optimized_research', 'create_optimized_pipeline'],  # noqa: E501
}


class CoordinatorCatalog:
    """
    Lazy-loading catalog for coordinators organized by domain.

    Provides structured access to coordinators without eagerly importing
    all modules at startup. Each domain is a logical grouping of related
    coordinators.
    """

    def __init__(self) -> None:
        self._cache: dict[str, type] = {}
        self._domains = list(_DOMAIN_MODULES.keys())

    @property
    def domains(self) -> list[str]:
        """List all available domains."""
        return self._domains.copy()

    def get(self, domain: str) -> dict[str, str]:
        """Get coordinator name -> module mapping for a domain."""
        if domain not in _DOMAIN_MODULES:
            available = ', '.join(self._domains)
            raise ValueError(f"Unknown domain '{domain}'. Available: {available}")
        return _DOMAIN_MODULES[domain].copy()

    def load(self, name: str) -> Any:
        """
        Lazily load a coordinator or export by name.

        Args:
            name: Coordinator class name or export name (e.g., 'UniversalMemoryCoordinator')

        Returns:
            The requested class or function

        Raises:
            AttributeError: If name not found in any domain
        """
        if name in self._cache:
            return self._cache[name]

        # Find which domain has this name
        for _domain, mappings in _DOMAIN_MODULES.items():
            if name in mappings:
                module_path = mappings[name]
                # Handle relative imports
                if module_path.startswith('.'):
                    full_module = f'coordinators{module_path}'
                else:
                    full_module = module_path

                # noaudit[python.lang.security.audit.non-literal-import.non-literal-import]
                # _DOMAIN_MODULES is a static module-level constant dict; full_module
                # is derived from hardcoded module paths — no user input reaches here.
                mod = importlib.import_module(full_module)
                result = getattr(mod, name)
                self._cache[name] = result
                return result

        # Check additional exports
        for _coordinator_name, exports in _COORDINATOR_EXPORTS.items():
            if name in exports:
                # Load the coordinator first, then get the export
                self.load(_coordinator_name)
                # The export should be on the module - find it
                for _domain, mappings in _DOMAIN_MODULES.items():
                    if _coordinator_name in mappings:
                        module_path = mappings[_coordinator_name]
                        if module_path.startswith('.'):
                            full_module = f'coordinators{module_path}'
                        else:
                            full_module = module_path
                        # noaudit[python.lang.security.audit.non-literal-import.non-literal-import]
                        # _DOMAIN_MODULES is a static module-level constant dict; full_module
                        # is derived from hardcoded module paths — no user input reaches here.
                        mod = importlib.import_module(full_module)
                        result = getattr(mod, name)
                        self._cache[name] = result
                        return result

        raise AttributeError(
            f"'{name}' not found in any domain. "
            f"Available: {', '.join(self._get_all_names())}"
        )

    def _get_all_names(self) -> list[str]:
        """Get all available coordinator and export names."""
        names = set()
        for mappings in _DOMAIN_MODULES.values():
            names.update(mappings.keys())
        for exports in _COORDINATOR_EXPORTS.values():
            names.update(exports)
        return sorted(names)

    def list_domain(self, domain: str) -> list[str]:
        """List all coordinator names in a domain."""
        if domain not in _DOMAIN_MODULES:
            raise ValueError(f"Unknown domain '{domain}'")
        return list(_DOMAIN_MODULES[domain].keys())

    def list_all(self) -> dict[str, list[str]]:
        """
        Aggregate every domain → its coordinator names. Useful for ops dashboards
        and per-flag smoke runners that need to introspect what is available
        without importing any coordinator module (lazy, zero RAM).

        Returns:
            {"core": ["UniversalResearchCoordinator", ...],
             "advanced": [...], ...}
        """
        return {d: self.list_domain(d) for d in self._domains}

    def is_known(self, name: str) -> bool:
        """Check whether a coordinator or export name exists in any domain. Cheap."""
        for mappings in _DOMAIN_MODULES.values():
            if name in mappings:
                return True
        for exports in _COORDINATOR_EXPORTS.values():
            if name in exports:
                return True
        return False

    def search(self, query: str) -> list[str]:
        """
        Search for coordinators/exports matching query (case-insensitive).

        Returns list of matching names.
        """
        query_lower = query.lower()
        results = []
        for name in self._get_all_names():
            if query_lower in name.lower():
                results.append(name)
        return results


# Singleton instance
catalog = CoordinatorCatalog()
