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
"""

from __future__ import annotations

# Archive Discovery (from deep_research/advanced_archive_discovery.py)
# Enhanced with stealth_osint integration
try:
    from .archive_discovery import (
        ArchiveDiscovery,
        ArchiveResult,
        SnapshotInfo,
        WaybackMachineClient,
        ArchiveTodayClient,
        IPFSClient,
        GitHubHistoricalClient,
        WaybackCDX,
        WaybackCDXClient,  # DEPRECATED: alias pro WaybackCDX, odstranit po CE-001
        CDXSnapshot,
        DiscoveredEndpoint,
        search_archives,
        get_wayback_snapshots,
        discover_from_wayback,
        wayback_cdx_lookup,  # COMPAT: search-shaped wrapper pro fetch_coordinator
        # From stealth_osint/archive_resurrector.py
        ArchiveResurrector,
        ContentSource,
        ContentType,
        Snapshot,
        ResurrectionResult,
        ResurrectionRequest,
        resurrect_url,
        get_archive_resurrector,
    )
    ARCHIVE_AVAILABLE = True
except ImportError:
    ARCHIVE_AVAILABLE = False

# Temporal Analysis (from deep_research/temporal_analyzer.py + predictive_modeler.py)
try:
    from .temporal_analysis import (
        TemporalAnalyzer,
        TemporalAnalysisResult,
        TrendAnalysis,
        TrendDirection,
        TemporalPattern,
        PatternType,
        CausalEvent,
        Scenario,
        TurningPoint,
        create_temporal_analyzer,
        # Advanced predictive methods from predictive_modeler.py:
        # - ARIMA projection
        # - Monte Carlo simulation
        # - Bayesian updating
        # - Exponential smoothing
        # - Ensemble prediction
    )
    TEMPORAL_AVAILABLE = True
except ImportError:
    TEMPORAL_AVAILABLE = False

# Stealth Crawler (from deep_research/distributed_dark_web_crawler.py)
# Enhanced with stealth_toolkit and stealth_osint integration
try:
    from .stealth_crawler import (
        StealthCrawler,
        SearchResult,
        create_stealth_crawler,
        # Header Spoofer (from stealth_toolkit integration)
        HeaderSpoofer,
        HeaderConfig,
        get_stealth_headers,
        # From stealth_osint/stealth_web_scraper.py
        StealthWebScraper,
        ScrapingResult,
        ProxyConfig,
        FingerprintProfile,
        ProtectionType,
        BypassMethod,
        quick_scrape,
        get_stealth_web_scraper,
        # Streaming Monitor (continuous monitoring capabilities)
        StreamingMonitor,
        MonitoredSource,
        StreamEvent,
        Alert,
        AlertRule,
        Change,
        ChangeType,
        Severity,
        SourceType,
    )
    CRAWLER_AVAILABLE = True
except ImportError:
    CRAWLER_AVAILABLE = False

# Web Intelligence
try:
    from .web_intelligence import (
        UnifiedWebIntelligence,
        IntelligenceTarget,
        IntelligenceResult,
        IntelligenceOperationType,
        OperationStatus,
    )
    WEB_INTEL_AVAILABLE = True
except ImportError:
    WEB_INTEL_AVAILABLE = False

# Academic Search (from MSQES)
try:
    from .academic_search import (
        AcademicSearchEngine,
        AcademicSearchResult,
        SearchResult,
        SourceResult,
        QueryAnalysis,
        SourcePerformance,
        BaseSourceAdapter,
        ArxivAdapter,
        CrossrefAdapter,
        SemanticScholarAdapter,
        ResultType,
        AcademicSource,
        search_academic,
    )
    ACADEMIC_SEARCH_AVAILABLE = True
except ImportError:
    ACADEMIC_SEARCH_AVAILABLE = False

# Data Leak Hunter (from stealth_osint/data_leak_hunter.py)
try:
    from .data_leak_hunter import (
        DataLeakHunter,
        LeakAlert,
        MonitoringTarget,
        BreachAPIConfig,
        AlertSeverity,
        LeakSource,
        check_email_breaches,
        get_data_leak_hunter,
    )
    DATA_LEAK_HUNTER_AVAILABLE = True
except ImportError:
    DATA_LEAK_HUNTER_AVAILABLE = False

# Cryptographic Intelligence (cryptanalysis, hash analysis, certificates)
try:
    from .cryptographic_intelligence import (
        CryptographicIntelligence,
        ClassicalCryptanalysis,
        HashAnalyzer,
        EncryptionDetector,
        CertificateAnalyzer,
        CryptanalysisResult,
        HashAnalysis,
        EncryptionDetection,
        CertificateInfo,
        CipherType,
        HashType,
    )
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# Document Intelligence (PDF, Office, Images, MLX Long-Context)
try:
    from .document_intelligence import (
        DocumentIntelligenceEngine,
        PDFAnalyzer,
        OfficeDocumentAnalyzer,
        ImageAnalyzer,
        DocumentAnalysis,
        DocumentMetadata,
        EXIFData,
        GeoLocation,
        EmbeddedObject,
        DocumentType,
        MLXLongContextAnalyzer,
        LongContextAnalysis,
        EntityMention,
        CrossDocumentLink,
        TimelineEvent,
    )
    DOCUMENT_INTELLIGENCE_AVAILABLE = True
except ImportError:
    DOCUMENT_INTELLIGENCE_AVAILABLE = False

# Temporal Archaeologist (deleted content recovery, timeline reconstruction)
try:
    from .temporal_archaeologist import (
        TemporalArchaeologist,
        ArchivedVersion,
        EntityTimeline,
        EntitySnapshot,
        IdentityChange,
        TemporalGap,
        TemporalAnomaly,
        TemporalCorrelation,
        ResolvedEntity,
        RecoveryResult,
        ArchiveSource,
        AnomalyType,
        EntityType,
        recover_deleted_content,
        reconstruct_timeline,
        detect_anomalies,
        create_temporal_archaeologist,
    )
    TEMPORAL_ARCHAEOLOGIST_AVAILABLE = True
except ImportError:
    TEMPORAL_ARCHAEOLOGIST_AVAILABLE = False

# Timeline Synthesizer (Sprint F202E)
try:
    from .timeline_synthesizer import (
        TimelineSynthesizer,
        TimelineEvent,
        TimelineMetadata,
        SynthesizedTimeline,
        create_timeline_synthesizer,
        MAX_TIMELINE_EVENTS,
        MAX_EVENT_AGE_DAYS,
    )
    TIMELINE_SYNTHESIZER_AVAILABLE = True
except ImportError:
    TIMELINE_SYNTHESIZER_AVAILABLE = False

# Temporal Archaeologist Adapter (Sprint F202E)
try:
    from .temporal_archaeologist_adapter import (
        TemporalArchaeologistAdapter,
        TimelineFindingResult,
        create_temporal_archaeologist_adapter,
        MAX_TIMELINE_FINDINGS,
    )
    TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE = True
except ImportError:
    TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE = False

# Exposed Service Hunter (S3, Databases, GraphQL, CT logs, Container APIs)
try:
    from .exposed_service_hunter import (
        ExposedServiceHunter,
        S3BucketEnumerator,
        DatabasePortScanner,
        GraphQLIntrospector,
        CertificateTransparency,
        ContainerAPIExplorer,
        ExposedService,
        S3Bucket,
        CertificateInfo,
        ServiceType,
        ExposureType,
        RiskLevel,
        quick_hunt,
        check_s3_bucket,
        scan_graphql_endpoint,
    )
    EXPOSED_SERVICE_HUNTER_AVAILABLE = True
except ImportError:
    EXPOSED_SERVICE_HUNTER_AVAILABLE = False


# Open Source Collectors (Paste sites, Usenet, Matrix, Academic, SEC EDGAR, Court Records)
try:
    from .open_source_collectors import (
        OpenSourceCollectors,
        get_open_source_collectors,
    )
    OPEN_SOURCE_COLLECTORS_AVAILABLE = True
except ImportError:
    OPEN_SOURCE_COLLECTORS_AVAILABLE = False


# Academic Discovery (P14 — convenience wrapper around AcademicSearchEngine)
try:
    from .academic_discovery import (
        AcademicPaper,
        search_arxiv,
        search_crossref,
        search_semantic_scholar,
        search_academic_all,
        search_arxiv_sync,
        search_crossref_sync,
        search_semantic_scholar_sync,
    )
    ACADEMIC_DISCOVERY_AVAILABLE = True
except ImportError:
    ACADEMIC_DISCOVERY_AVAILABLE = False


# PastebinMonitor (P20 — paste site scraper for leak OSINT)
try:
    from .pastebin_monitor import (
        PasteFinding,
        run as pastebin_run,
    )
    PASTEBIN_MONITOR_AVAILABLE = True
except ImportError:
    PASTEBIN_MONITOR_AVAILABLE = False


# Relationship Discovery (Social Network Analysis)
try:
    from .relationship_discovery import (
        RelationshipDiscoveryEngine,
        Entity,
        Relationship,
        ConnectionPath,
        Community,
        AffinityMatrix,
        Communication,
        Document,
        InfluenceModel,
        EntityType,
        RelationshipType,
        create_relationship_engine,
    )
    RELATIONSHIP_DISCOVERY_AVAILABLE = True
except ImportError:
    RELATIONSHIP_DISCOVERY_AVAILABLE = False

# Pattern Mining Engine (behavioral, temporal, communication patterns)
try:
    from .pattern_mining import (
        PatternMiningEngine,
        Pattern,
        TemporalPattern,
        BehavioralPattern,
        CommunicationPattern,
        FlowPattern,
        StructuralPattern,
        SequentialPattern,
        Anomaly,
        Event,
        Action,
        Communication,
        Transaction,
        PatternType,
        SeasonalityType,
        TrendDirection,
        AnomalyType,
        create_pattern_mining_engine,
    )
    PATTERN_MINING_AVAILABLE = True
except ImportError:
    PATTERN_MINING_AVAILABLE = False

# Identity Stitching Engine (cross-platform identity linking)
try:
    from .identity_stitching import (
        IdentityStitchingEngine,
        IdentityProfile,
        IdentityMatch,
        StitchedIdentity,
        UsernameEntry,
        create_identity_stitching_engine,
    )
    IDENTITY_STITCHING_AVAILABLE = True
except ImportError:
    IDENTITY_STITCHING_AVAILABLE = False

# Blockchain Forensics (cryptocurrency analysis and tracing)
try:
    from .blockchain_analyzer import (
        BlockchainForensics,
        WalletAnalysis,
        TransactionPattern,
        Cluster,
        CrossChainResult,
        Transaction,
        ChainType,
        EntityType,
        PatternType as BlockchainPatternType,
        RiskLevel as BlockchainRiskLevel,
        analyze_blockchain_address,
        detect_transaction_patterns,
        get_blockchain_forensics,
    )
    BLOCKCHAIN_FORENSICS_AVAILABLE = True
except ImportError:
    BLOCKCHAIN_FORENSICS_AVAILABLE = False

# Phase 11: Input Detector
try:
    from .input_detector import (
        IntelligentInputDetector,
        InputAnalysis,
        Pattern,
        ComplexityScore,
        create_input_detector,
        IntelligenceConfig,
    )
    INPUT_DETECTOR_AVAILABLE = True
except ImportError:
    INPUT_DETECTOR_AVAILABLE = False
    IntelligentInputDetector = None  # type: ignore
    InputAnalysis = None  # type: ignore
    Pattern = None  # type: ignore
    ComplexityScore = None  # type: ignore
    create_input_detector = None  # type: ignore

# Phase 11: Workflow Orchestrator
try:
    from .workflow_orchestrator import (
        WorkflowOrchestrator,
        ComprehensiveReport,
        SharedContext,
        CorrelationReport,
        Anomaly,
        Finding,
        create_workflow_orchestrator,
    )
    WORKFLOW_ORCHESTRATOR_AVAILABLE = True
except ImportError:
    WORKFLOW_ORCHESTRATOR_AVAILABLE = False
    WorkflowOrchestrator = None  # type: ignore
    ComprehensiveReport = None  # type: ignore
    SharedContext = None  # type: ignore
    CorrelationReport = None  # type: ignore
    Anomaly = None  # type: ignore
    Finding = None  # type: ignore
    create_workflow_orchestrator = None  # type: ignore


__all__ = [
    # Availability flags
    "ARCHIVE_AVAILABLE",
    "TEMPORAL_AVAILABLE",
    "CRAWLER_AVAILABLE",
    "WEB_INTEL_AVAILABLE",
    "ACADEMIC_SEARCH_AVAILABLE",
    "DATA_LEAK_HUNTER_AVAILABLE",
    "CRYPTO_AVAILABLE",
    "DOCUMENT_INTELLIGENCE_AVAILABLE",
    "TEMPORAL_ARCHAEOLOGIST_AVAILABLE",
    "TIMELINE_SYNTHESIZER_AVAILABLE",
    "TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE",
    "EXPOSED_SERVICE_HUNTER_AVAILABLE",
    "OPEN_SOURCE_COLLECTORS_AVAILABLE",
    # Archive
    "ArchiveDiscovery",
    "ArchiveResult",
    "SnapshotInfo",
    "WaybackMachineClient",
    "ArchiveTodayClient",
    "IPFSClient",
    "GitHubHistoricalClient",
    "WaybackCDXClient",
    "CDXSnapshot",
    "DiscoveredEndpoint",
    "search_archives",
    "get_wayback_snapshots",
    "discover_from_wayback",
    # Temporal
    "TemporalAnalyzer",
    "TemporalAnalysisResult",
    "TrendAnalysis",
    "TrendDirection",
    "TemporalPattern",
    "PatternType",
    "CausalEvent",
    "Scenario",
    "TurningPoint",
    "create_temporal_analyzer",
    # Crawler
    "StealthCrawler",
    "SearchResult",
    "create_stealth_crawler",
    # Header Spoofer (from stealth_toolkit)
    "HeaderSpoofer",
    "HeaderConfig",
    "get_stealth_headers",
    # Web Intelligence
    "UnifiedWebIntelligence",
    "IntelligenceTarget",
    "IntelligenceResult",
    "IntelligenceOperationType",
    "OperationStatus",
    # Academic Search (from MSQES)
    "AcademicSearchEngine",
    "AcademicSearchResult",
    "SearchResult",
    "SourceResult",
    "QueryAnalysis",
    "SourcePerformance",
    "BaseSourceAdapter",
    "ArxivAdapter",
    "CrossrefAdapter",
    "SemanticScholarAdapter",
    "ResultType",
    "AcademicSource",
    "search_academic",
    # Archive Resurrector (from stealth_osint)
    "ArchiveResurrector",
    "ContentSource",
    "ContentType",
    "Snapshot",
    "ResurrectionResult",
    "ResurrectionRequest",
    "resurrect_url",
    "get_archive_resurrector",
    # Stealth Web Scraper (from stealth_osint)
    "StealthWebScraper",
    "ScrapingResult",
    "ProxyConfig",
    "FingerprintProfile",
    "ProtectionType",
    "BypassMethod",
    "quick_scrape",
    "get_stealth_web_scraper",
    # Data Leak Hunter (from stealth_osint)
    "DataLeakHunter",
    "LeakAlert",
    "MonitoringTarget",
    "BreachAPIConfig",
    "AlertSeverity",
    "LeakSource",
    "check_email_breaches",
    "get_data_leak_hunter",
    # Cryptographic Intelligence
    "CryptographicIntelligence",
    "ClassicalCryptanalysis",
    "HashAnalyzer",
    "EncryptionDetector",
    "CertificateAnalyzer",
    "CryptanalysisResult",
    "HashAnalysis",
    "EncryptionDetection",
    "CertificateInfo",
    "CipherType",
    "HashType",
    # Document Intelligence
    "DocumentIntelligenceEngine",
    "PDFAnalyzer",
    "OfficeDocumentAnalyzer",
    "ImageAnalyzer",
    "DocumentAnalysis",
    "DocumentMetadata",
    "EXIFData",
    "GeoLocation",
    "EmbeddedObject",
    "DocumentType",
    "MLXLongContextAnalyzer",
    "LongContextAnalysis",
    "EntityMention",
    "CrossDocumentLink",
    "TimelineEvent",
    # Temporal Archaeologist
    "TemporalArchaeologist",
    "ArchivedVersion",
    "EntityTimeline",
    "EntitySnapshot",
    "IdentityChange",
    "TemporalGap",
    "TemporalAnomaly",
    "TemporalCorrelation",
    "ResolvedEntity",
    "RecoveryResult",
    "ArchiveSource",
    "AnomalyType",
    "EntityType",
    "recover_deleted_content",
    "reconstruct_timeline",
    "detect_anomalies",
    "create_temporal_archaeologist",
    # Timeline Synthesizer (Sprint F202E)
    "TimelineSynthesizer",
    "TimelineEvent",
    "TimelineMetadata",
    "SynthesizedTimeline",
    "create_timeline_synthesizer",
    "MAX_TIMELINE_EVENTS",
    "MAX_EVENT_AGE_DAYS",
    # Temporal Archaeologist Adapter (Sprint F202E)
    "TemporalArchaeologistAdapter",
    "TimelineFindingResult",
    "create_temporal_archaeologist_adapter",
    "MAX_TIMELINE_FINDINGS",
    # Exposed Service Hunter
    "EXPOSED_SERVICE_HUNTER_AVAILABLE",
    "ExposedServiceHunter",
    "S3BucketEnumerator",
    "DatabasePortScanner",
    "GraphQLIntrospector",
    "CertificateTransparency",
    "ContainerAPIExplorer",
    "ExposedService",
    "S3Bucket",
    "CertificateInfo",
    "ServiceType",
    # Open Source Collectors
    "OPEN_SOURCE_COLLECTORS_AVAILABLE",
    "OpenSourceCollectors",
    "get_open_source_collectors",
    "ExposureType",
    "RiskLevel",
    "quick_hunt",
    "check_s3_bucket",
    "scan_graphql_endpoint",
    # Academic Discovery (P14)
    "ACADEMIC_DISCOVERY_AVAILABLE",
    "AcademicPaper",
    "search_arxiv",
    "search_crossref",
    "search_semantic_scholar",
    "search_academic_all",
    "search_arxiv_sync",
    "search_crossref_sync",
    "search_semantic_scholar_sync",
    # PastebinMonitor (P20)
    "PASTEBIN_MONITOR_AVAILABLE",
    "PasteFinding",
    "pastebin_run",
    # Relationship Discovery
    "RELATIONSHIP_DISCOVERY_AVAILABLE",
    "RelationshipDiscoveryEngine",
    "Entity",
    "Relationship",
    "ConnectionPath",
    "Community",
    "AffinityMatrix",
    "Communication",
    "Document",
    "InfluenceModel",
    "RelationshipType",
    "create_relationship_engine",
    # Pattern Mining
    "PATTERN_MINING_AVAILABLE",
    "PatternMiningEngine",
    "Pattern",
    "TemporalPattern",
    "BehavioralPattern",
    "CommunicationPattern",
    "FlowPattern",
    "StructuralPattern",
    "SequentialPattern",
    "Anomaly",
    "Event",
    "Action",
    "Communication",
    "Transaction",
    "PatternType",
    "SeasonalityType",
    "TrendDirection",
    "AnomalyType",
    "create_pattern_mining_engine",
    # Identity Stitching
    "IDENTITY_STITCHING_AVAILABLE",
    "IdentityStitchingEngine",
    "IdentityProfile",
    "IdentityMatch",
    "StitchedIdentity",
    "UsernameEntry",
    "create_identity_stitching_engine",
    # Streaming Monitor
    "StreamingMonitor",
    "MonitoredSource",
    "StreamEvent",
    "Alert",
    "AlertRule",
    "Change",
    "ChangeType",
    "Severity",
    "SourceType",
    # Blockchain Forensics
    "BLOCKCHAIN_FORENSICS_AVAILABLE",
    "INPUT_DETECTOR_AVAILABLE",
    "WORKFLOW_ORCHESTRATOR_AVAILABLE",
    "BlockchainForensics",
    "WalletAnalysis",
    "TransactionPattern",
    "Cluster",
    "CrossChainResult",
    "Transaction",
    "ChainType",
    "EntityType",
    "BlockchainPatternType",
    "BlockchainRiskLevel",
    "analyze_blockchain_address",
    "detect_transaction_patterns",
    "get_blockchain_forensics",
    # Input Detector
    "IntelligentInputDetector",
    "InputAnalysis",
    "Pattern",
    "ComplexityScore",
    "create_input_detector",
    "IntelligenceConfig",  # config class used by IntelligentInputDetector
    # Workflow Orchestrator
    "WorkflowOrchestrator",
    "ComprehensiveReport",
    "SharedContext",
    "CorrelationReport",
    "Anomaly",
    "Finding",
    "create_workflow_orchestrator",
]
