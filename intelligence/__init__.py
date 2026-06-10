"""
Universal Intelligence Module — CAPABILITY FOREST, NOT PRODUCTION OWNER
======================================================================

.. availability_flags::
    ``_AVAILABLE`` flags in this module indicate import success, NOT production
    readiness or canonical wiring. A ``_AVAILABLE = True`` flag means the module
    was successfully imported. It does NOT mean the capability is:
      - production-wired into the canonical sprint path
      - recommended for new development
      - free of import-time side-effects (torch, sklearn, networkx may load)

    Production sprint path: ``core.__main__:run_sprint()``
    Canonical orchestrator: ``runtime.sprint_scheduler:SprintScheduler``

Integrated from deep_research:
- Archive Discovery (Wayback, Archive.today, IPFS, GitHub)
- Temporal Analysis (time-series, trend detection)
- Stealth Crawler (DuckDuckGo/Google scraping)
- Web Intelligence (unified platform)

Sprint F-A2: lazy module loading via PEP 562 ``__getattr__``.
Each optional subsystem defers its import until first attribute access.
Cold ``import intelligence`` now only pays for the spec table (~5-10ms)
instead of all 21 try/except blocks (~200ms).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

_log = logging.getLogger(__name__)

# Lazy spec table. Each spec = (submodule, flag, names_imported, names_set_to_None_on_import_error).
# Loading order matches the historical import order; for name collisions
# (Anomaly, Pattern, EntityType, etc. imported from multiple submodules),
# the LAST spec wins — same behaviour as the original ``from X import Y``
# shadowing at module top level.
_LAZY_SPECS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (".archive_discovery", "ARCHIVE_AVAILABLE", (
        "ArchiveDiscovery", "ArchiveResult",
        "ArchiveResurrector", "ArchiveTodayClient", "CDXSnapshot",
        "ContentSource", "ContentType", "DiscoveredEndpoint",
        "GitHubHistoricalClient", "IPFSClient",
        "ResurrectionRequest", "ResurrectionResult",
        "Snapshot", "SnapshotInfo",
        "WaybackCDX",
        "WaybackCDXClient",
        "WaybackMachineClient",
        "discover_from_wayback", "get_archive_resurrector",
        "get_wayback_snapshots", "resurrect_url", "search_archives",
        "wayback_cdx_lookup",
    ), ()),
    (".temporal_analysis", "TEMPORAL_AVAILABLE", (
        "CausalEvent", "PatternType",
        "Scenario", "TemporalAnalysisResult", "TemporalAnalyzer",
        "TemporalPattern", "TrendAnalysis", "TrendDirection",
        "TurningPoint", "create_temporal_analyzer",
    ), ()),
    (".stealth_crawler", "CRAWLER_AVAILABLE", (
        "Alert", "AlertRule", "BypassMethod", "Change", "ChangeType",
        "FingerprintProfile", "HeaderConfig", "HeaderSpoofer",
        "MonitoredSource", "ProtectionType", "ProxyConfig",
        "ScrapingResult", "SearchResult", "Severity", "SourceType",
        "StealthCrawler", "StealthWebScraper", "StreamEvent",
        "StreamingMonitor",
        "create_stealth_crawler", "get_stealth_headers",
        "get_stealth_web_scraper", "quick_scrape",
    ), ()),
    (".web_intelligence", "WEB_INTEL_AVAILABLE", (
        "IntelligenceOperationType", "IntelligenceResult",
        "IntelligenceTarget", "OperationStatus",
        "UnifiedWebIntelligence",
    ), ()),
    (".academic_search", "ACADEMIC_SEARCH_AVAILABLE", (
        "AcademicSearchEngine", "AcademicSearchResult", "AcademicSource",
        "ArxivAdapter", "BaseSourceAdapter", "CrossrefAdapter",
        "QueryAnalysis", "ResultType", "SearchResult",
        "SemanticScholarAdapter", "SourcePerformance", "SourceResult",
        "search_academic",
    ), ()),
    (".data_leak_hunter", "DATA_LEAK_HUNTER_AVAILABLE", (
        "AlertSeverity", "BreachAPIConfig", "DataLeakHunter",
        "LeakAlert", "LeakSource", "MonitoringTarget",
        "check_email_breaches", "get_data_leak_hunter",
    ), ()),
    (".cryptographic_intelligence", "CRYPTO_AVAILABLE", (
        "CertificateAnalyzer", "CertificateInfo", "CipherType",
        "ClassicalCryptanalysis", "CryptanalysisResult",
        "CryptographicIntelligence", "EncryptionDetection",
        "EncryptionDetector", "HashAnalysis", "HashAnalyzer", "HashType",
    ), ()),
    (".document_intelligence", "DOCUMENT_INTELLIGENCE_AVAILABLE", (
        "CrossDocumentLink", "DocumentAnalysis",
        "DocumentIntelligenceEngine", "DocumentMetadata", "DocumentType",
        "EmbeddedObject", "EntityMention", "EXIFData", "GeoLocation",
        "ImageAnalyzer", "LongContextAnalysis", "MLXLongContextAnalyzer",
        "OfficeDocumentAnalyzer", "PDFAnalyzer", "TimelineEvent",
    ), ()),
    (".temporal_archaeologist", "TEMPORAL_ARCHAEOLOGIST_AVAILABLE", (
        "AnomalyType", "ArchivedVersion", "ArchiveSource",
        "EntitySnapshot", "EntityTimeline", "EntityType", "IdentityChange",
        "RecoveryResult", "ResolvedEntity", "TemporalAnomaly",
        "TemporalArchaeologist", "TemporalCorrelation", "TemporalGap",
        "create_temporal_archaeologist", "detect_anomalies",
        "reconstruct_timeline", "recover_deleted_content",
    ), ()),
    (".timeline_synthesizer", "TIMELINE_SYNTHESIZER_AVAILABLE", (
        "MAX_EVENT_AGE_DAYS", "MAX_TIMELINE_EVENTS",
        "SynthesizedTimeline", "TimelineEvent", "TimelineMetadata",
        "TimelineSynthesizer", "create_timeline_synthesizer",
    ), ()),
    (".temporal_archaeologist_adapter", "TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE", (
        "MAX_TIMELINE_FINDINGS", "TemporalArchaeologistAdapter",
        "TimelineFindingResult", "create_temporal_archaeologist_adapter",
    ), ()),
    (".exposed_service_hunter", "EXPOSED_SERVICE_HUNTER_AVAILABLE", (
        "CertificateInfo", "CertificateTransparency",
        "ContainerAPIExplorer", "DatabasePortScanner",
        "ExposedService", "ExposedServiceHunter", "ExposureType",
        "GraphQLIntrospector", "RiskLevel", "S3Bucket",
        "S3BucketEnumerator", "ServiceType",
        "check_s3_bucket", "quick_hunt", "scan_graphql_endpoint",
    ), ()),
    (".open_source_collectors", "OPEN_SOURCE_COLLECTORS_AVAILABLE", (
        "OpenSourceCollectors", "get_open_source_collectors",
    ), ()),
    (".academic_discovery", "ACADEMIC_DISCOVERY_AVAILABLE", (
        "AcademicPaper", "search_academic_all", "search_arxiv",
        "search_arxiv_sync", "search_crossref", "search_crossref_sync",
        "search_semantic_scholar", "search_semantic_scholar_sync",
    ), ()),
    (".pastebin_monitor", "PASTEBIN_MONITOR_AVAILABLE", (
        "PasteFinding", "pastebin_run",
    ), ()),
    (".relationship_discovery", "RELATIONSHIP_DISCOVERY_AVAILABLE", (
        "AffinityMatrix", "Communication", "Community", "ConnectionPath",
        "Document", "Entity", "EntityType", "InfluenceModel",
        "Relationship", "RelationshipDiscoveryEngine", "RelationshipType",
        "create_relationship_engine",
    ), ()),
    (".pattern_mining", "PATTERN_MINING_AVAILABLE", (
        "Action", "Anomaly", "AnomalyType", "BehavioralPattern",
        "Communication", "CommunicationPattern", "Event", "FlowPattern",
        "Pattern", "PatternMiningEngine", "PatternType",
        "SeasonalityType", "SequentialPattern", "StructuralPattern",
        "TemporalPattern", "Transaction", "TrendDirection",
        "create_pattern_mining_engine",
    ), ()),
    (".identity_stitching", "IDENTITY_STITCHING_AVAILABLE", (
        "IdentityMatch", "IdentityProfile", "IdentityStitchingEngine",
        "StitchedIdentity", "UsernameEntry",
        "create_identity_stitching_engine",
    ), ()),
    # blockchain_analyzer: original code had 3 separate ``from`` blocks (names,
    # then PatternType alias, then RiskLevel alias). One merged spec preserves
    # the same final namespace contents.
    (".blockchain_analyzer", "BLOCKCHAIN_FORENSICS_AVAILABLE", (
        "BlockchainForensics", "ChainType", "Cluster", "CrossChainResult",
        "EntityType", "Transaction", "TransactionPattern", "WalletAnalysis",
        "analyze_blockchain_address", "detect_transaction_patterns",
        "get_blockchain_forensics",
        "BlockchainPatternType", "BlockchainRiskLevel",
    ), ()),
    # input_detector + workflow_orchestrator: original set explicit
    # ``Name = None`` in the except branch so callers can ``if X is None`` guard.
    (".input_detector", "INPUT_DETECTOR_AVAILABLE", (
        "ComplexityScore", "InputAnalysis", "IntelligenceConfig",
        "IntelligentInputDetector", "Pattern", "create_input_detector",
    ), (
        "IntelligentInputDetector", "InputAnalysis", "Pattern",
        "ComplexityScore", "create_input_detector",
    )),
    (".workflow_orchestrator", "WORKFLOW_ORCHESTRATOR_AVAILABLE", (
        "Anomaly", "ComprehensiveReport", "CorrelationReport", "Finding",
        "SharedContext", "WorkflowOrchestrator", "create_workflow_orchestrator",
    ), (
        "WorkflowOrchestrator", "ComprehensiveReport", "SharedContext",
        "CorrelationReport", "Anomaly", "Finding", "create_workflow_orchestrator",
    )),
)

# Build the name -> spec index. For duplicate names (Anomaly, Pattern, EntityType,
# AlertSeverity, CertificateInfo imported from multiple submodules), the LAST
# spec in _LAZY_SPECS wins — mirroring the historical ``from X import Y`` shadowing
# where the final assignment to the module namespace is the one that sticks.
_NAME_TO_SPEC: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
for _spec in _LAZY_SPECS:
    _modname, _flag, _names, _nulls = _spec
    for _n in _names:
        _NAME_TO_SPEC[_n] = _spec  # last write wins (intentional)
_FLAG_TO_SPEC: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    _flag: _spec for _spec in _LAZY_SPECS for _flag in [_spec[1]]
}

# Track which specs have already been resolved (one-shot load per process).
_RESOLVED_SPECS: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()


def _load_spec(spec: tuple[str, str, tuple[str, ...], tuple[str, ...]]) -> None:
    """Import the submodule, inject names + flag into module globals, set None fallbacks on failure.

    Idempotent — re-entry (e.g. re-raised import) is a no-op once resolved.
    """
    if spec in _RESOLVED_SPECS:
        return
    modname, flag, names, nulls = spec
    try:
        mod = importlib.import_module(modname, __name__)
    except Exception as exc:  # ImportError + all transitive failures
        _log.debug("intelligence lazy load failed: %s (%s)", modname, type(exc).__name__)
        for n in nulls:
            globals()[n] = None
        globals()[flag] = False
        _RESOLVED_SPECS.add(spec)
        return
    for n in names:
        globals()[n] = getattr(mod, n, None)
    globals()[flag] = True
    _RESOLVED_SPECS.add(spec)


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access.

    Lookup order:
      1. ``name`` is one of the imported names → ensure the owning spec is loaded, return the value.
      2. ``name`` is a flag (``XXX_AVAILABLE``) → ensure the owning spec is loaded, return the flag.
      3. Raise ``AttributeError`` so ``hasattr(...)`` and dir() behave correctly.
    """
    spec = _NAME_TO_SPEC.get(name)
    if spec is not None:
        _load_spec(spec)
        return globals().get(name)
    spec = _FLAG_TO_SPEC.get(name)
    if spec is not None:
        _load_spec(spec)
        return globals().get(name, False)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Tab completion: union of eager globals + all lazy names + all flags."""
    names: set[str] = set(globals().keys())
    for _modname, _flag, _names, _nulls in _LAZY_SPECS:
        names.add(_flag)
        names.update(_names)
    return sorted(names)


def _lazy_stats() -> dict[str, Any]:
    """Diagnostic snapshot: which specs are loaded, which are pending.

    Returned shape: ``{"loaded": [flag, ...], "pending": [flag, ...],
    "resolved_count": int, "total_count": int}``.
    """
    loaded: list[str] = []
    pending: list[str] = []
    for spec in _LAZY_SPECS:
        if spec in _RESOLVED_SPECS:
            loaded.append(spec[1])
        else:
            pending.append(spec[1])
    return {
        "loaded": loaded,
        "pending": pending,
        "resolved_count": len(loaded),
        "total_count": len(_LAZY_SPECS),
    }


__all__ = sorted(set([
    # Availability flags
    "ARCHIVE_AVAILABLE", "TEMPORAL_AVAILABLE", "CRAWLER_AVAILABLE",
    "WEB_INTEL_AVAILABLE", "ACADEMIC_SEARCH_AVAILABLE",
    "DATA_LEAK_HUNTER_AVAILABLE", "CRYPTO_AVAILABLE",
    "DOCUMENT_INTELLIGENCE_AVAILABLE", "TEMPORAL_ARCHAEOLOGIST_AVAILABLE",
    "TIMELINE_SYNTHESIZER_AVAILABLE",
    "TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE",
    "EXPOSED_SERVICE_HUNTER_AVAILABLE", "OPEN_SOURCE_COLLECTORS_AVAILABLE",
    "ACADEMIC_DISCOVERY_AVAILABLE", "PASTEBIN_MONITOR_AVAILABLE",
    "RELATIONSHIP_DISCOVERY_AVAILABLE", "PATTERN_MINING_AVAILABLE",
    "IDENTITY_STITCHING_AVAILABLE", "BLOCKCHAIN_FORENSICS_AVAILABLE",
    "INPUT_DETECTOR_AVAILABLE", "WORKFLOW_ORCHESTRATOR_AVAILABLE",
    # Archive
    "ArchiveDiscovery", "ArchiveResult", "SnapshotInfo",
    "WaybackMachineClient", "ArchiveTodayClient", "IPFSClient",
    "GitHubHistoricalClient", "WaybackCDXClient", "CDXSnapshot",
    "DiscoveredEndpoint", "search_archives", "get_wayback_snapshots",
    "discover_from_wayback",
    # Temporal
    "TemporalAnalyzer", "TemporalAnalysisResult", "TrendAnalysis",
    "TrendDirection", "TemporalPattern", "PatternType", "CausalEvent",
    "Scenario", "TurningPoint", "create_temporal_analyzer",
    # Crawler
    "StealthCrawler", "SearchResult", "create_stealth_crawler",
    "HeaderSpoofer", "HeaderConfig", "get_stealth_headers",
    # Web Intelligence
    "UnifiedWebIntelligence", "IntelligenceTarget", "IntelligenceResult",
    "IntelligenceOperationType", "OperationStatus",
    # Academic Search (MSQES)
    "AcademicSearchEngine", "AcademicSearchResult", "SourceResult",
    "QueryAnalysis", "SourcePerformance", "BaseSourceAdapter",
    "ArxivAdapter", "CrossrefAdapter", "SemanticScholarAdapter",
    "ResultType", "AcademicSource", "search_academic",
    # Archive Resurrector (stealth_osint)
    "ArchiveResurrector", "ContentSource", "ContentType", "Snapshot",
    "ResurrectionResult", "ResurrectionRequest", "resurrect_url",
    "get_archive_resurrector",
    # Stealth Web Scraper (stealth_osint)
    "StealthWebScraper", "ScrapingResult", "ProxyConfig",
    "FingerprintProfile", "ProtectionType", "BypassMethod",
    "quick_scrape", "get_stealth_web_scraper",
    # Data Leak Hunter (stealth_osint)
    "DataLeakHunter", "LeakAlert", "MonitoringTarget", "BreachAPIConfig",
    "AlertSeverity", "LeakSource", "check_email_breaches",
    "get_data_leak_hunter",
    # Cryptographic Intelligence
    "CryptographicIntelligence", "ClassicalCryptanalysis", "HashAnalyzer",
    "EncryptionDetector", "CertificateAnalyzer", "CryptanalysisResult",
    "HashAnalysis", "EncryptionDetection", "CertificateInfo",
    "CipherType", "HashType",
    # Document Intelligence
    "DocumentIntelligenceEngine", "PDFAnalyzer", "OfficeDocumentAnalyzer",
    "ImageAnalyzer", "DocumentAnalysis", "DocumentMetadata", "EXIFData",
    "GeoLocation", "EmbeddedObject", "DocumentType",
    "MLXLongContextAnalyzer", "LongContextAnalysis", "EntityMention",
    "CrossDocumentLink", "TimelineEvent",
    # Temporal Archaeologist
    "TemporalArchaeologist", "ArchivedVersion", "EntityTimeline",
    "EntitySnapshot", "IdentityChange", "TemporalGap", "TemporalAnomaly",
    "TemporalCorrelation", "ResolvedEntity", "RecoveryResult",
    "ArchiveSource", "AnomalyType", "EntityType", "recover_deleted_content",
    "reconstruct_timeline", "detect_anomalies",
    "create_temporal_archaeologist",
    # Timeline Synthesizer (F202E)
    "TimelineSynthesizer", "TimelineEvent", "TimelineMetadata",
    "SynthesizedTimeline", "create_timeline_synthesizer",
    "MAX_TIMELINE_EVENTS", "MAX_EVENT_AGE_DAYS",
    # Temporal Archaeologist Adapter (F202E)
    "TemporalArchaeologistAdapter", "TimelineFindingResult",
    "create_temporal_archaeologist_adapter", "MAX_TIMELINE_FINDINGS",
    # Exposed Service Hunter
    "ExposedServiceHunter", "S3BucketEnumerator", "DatabasePortScanner",
    "GraphQLIntrospector", "CertificateTransparency",
    "ContainerAPIExplorer", "ExposedService", "S3Bucket", "ServiceType",
    "ExposureType", "RiskLevel", "quick_hunt", "check_s3_bucket",
    "scan_graphql_endpoint",
    # Open Source Collectors
    "OpenSourceCollectors", "get_open_source_collectors",
    # Academic Discovery (P14)
    "AcademicPaper", "search_arxiv", "search_crossref",
    "search_semantic_scholar", "search_academic_all", "search_arxiv_sync",
    "search_crossref_sync", "search_semantic_scholar_sync",
    # PastebinMonitor (P20)
    "PasteFinding", "pastebin_run",
    # Relationship Discovery
    "RelationshipDiscoveryEngine", "Entity", "Relationship",
    "ConnectionPath", "Community", "AffinityMatrix", "Communication",
    "Document", "InfluenceModel", "RelationshipType",
    "create_relationship_engine",
    # Pattern Mining
    "PatternMiningEngine", "Pattern", "TemporalPattern", "BehavioralPattern",
    "CommunicationPattern", "FlowPattern", "StructuralPattern",
    "SequentialPattern", "Anomaly", "Event", "Action", "Transaction",
    "SeasonalityType", "create_pattern_mining_engine",
    # Identity Stitching
    "IdentityStitchingEngine", "IdentityProfile", "IdentityMatch",
    "StitchedIdentity", "UsernameEntry", "create_identity_stitching_engine",
    # Streaming Monitor
    "StreamingMonitor", "MonitoredSource", "StreamEvent",
    # Blockchain Forensics
    "BlockchainForensics", "WalletAnalysis", "TransactionPattern",
    "Cluster", "CrossChainResult", "Transaction", "ChainType",
    "BlockchainPatternType", "BlockchainRiskLevel",
    "analyze_blockchain_address", "detect_transaction_patterns",
    "get_blockchain_forensics",
    # Input Detector
    "IntelligentInputDetector", "InputAnalysis", "ComplexityScore",
    "create_input_detector", "IntelligenceConfig",
    # Workflow Orchestrator
    "WorkflowOrchestrator", "ComprehensiveReport", "SharedContext",
    "CorrelationReport", "Finding", "create_workflow_orchestrator",
    # Lazy internals (diagnostic)
    "_lazy_stats",
]))
