"""
Autonomous Analyzer for Hledac Universal Orchestrator
======================================================

Modern Python 3.14+ optimized version with:
- External TOML configuration for keywords
- functools.cache for pattern compilation
- Protocol-based design for extensibility
- Memory-efficient dataclasses with slots

M1 8GB RAM Optimizations:
- __slots__ on all classes
- Lazy pattern compilation with caching
- msgspec.Struct for efficient serialization

Author: Hledac AI Research Platform
Version: 2.0.0
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

try:
    import msgspec

    _HAS_MSGPEC = True
except ImportError:
    msgspec = None  # type: ignore[assignment]
    _HAS_MSGPEC = False

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Language keywords with bilingual support (EN/CS)
TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "stealth_crawler": (
        "dark web",
        "tor",
        "onion",
        "hidden service",
        ".onion",
        "darknet",
        "deep web",
        "hidden web",
        "temný web",
        "skrytá služba",
    ),
    "archive_discovery": (
        "archive",
        "wayback",
        "historical",
        "deleted",
        "past",
        "archiv",
        "historický",
        "smazaný",
        "minulost",
        "web archive",
        "internet archive",
        "snapshot",
        "cached",
    ),
    "leak_hunter": (
        "leak",
        "breach",
        "exposed",
        "leaked",
        "dump",
        "compromised",
        "data breach",
        "unik",
        "únik",
        "zveřejněný",
        "kompromitovaný",
        "password dump",
        "credential",
        "exposed data",
    ),
    "blockchain_analyzer": (
        "blockchain",
        "crypto",
        "bitcoin",
        "ethereum",
        "wallet",
        "transaction",
        "btc",
        "eth",
        "cryptocurrency",
        "token",
        "smart contract",
        "defi",
        "nft",
        "blockchain analysis",
    ),
    "academic_search": (
        "research",
        "paper",
        "study",
        "scholar",
        "arxiv",
        "academic",
        "výzkum",
        "studie",
        "článek",
        "publikace",
        "vědecký",
        "journal",
        "conference",
        "thesis",
        "dissertation",
        "peer reviewed",
    ),
    "identity_stitching": (
        "identity",
        "person",
        "profile",
        "who is",
        "individual",
        "identita",
        "osoba",
        "profil",
        "kdo je",
        "jednotlivec",
        "background check",
        "people search",
        "find person",
    ),
    "relationship_discovery": (
        "connection",
        "relationship",
        "network",
        "linked to",
        "spojení",
        "vztah",
        "síť",
        "propojený",
        "associated with",
        "affiliated",
        "related to",
        "connected to",
    ),
    "pattern_mining": (
        "pattern",
        "trend",
        "anomaly",
        "correlation",
        "vzor",
        "trend",
        "anomálie",
        "korelace",
        "pattern analysis",
        "behavioral pattern",
        "usage pattern",
        "frequency analysis",
    ),
    "web_intelligence": (
        "website",
        "web",
        "online",
        "internet",
        "domain",
        "webová stránka",
        "web",
        "online",
        "internet",
        "doména",
        "site analysis",
        "web scraping",
        "web monitoring",
    ),
    "news_analyzer": (
        "news",
        "article",
        "media",
        "report",
        "press",
        "novinky",
        "článek",
        "média",
        "zpráva",
        "tisk",
        "breaking news",
        "headline",
        "journalism",
        "news source",
    ),
    "forum_hunter": (
        "forum",
        "discussion",
        "community",
        "reddit",
        "thread",
        "fórum",
        "diskuze",
        "komunita",
        "vlákno",
        "message board",
        "online community",
        "user discussion",
    ),
    "social_mapper": (
        "social media",
        "facebook",
        "twitter",
        "instagram",
        "linkedin",
        "twitter",
        "x.com",
        "tiktok",
        "youtube",
        "telegram",
        "sociální sítě",
        "social profile",
        "social account",
        "follower",
        "post",
    ),
    "document_analyzer": (
        "document",
        "pdf",
        "file",
        "report",
        "doc",
        "dokument",
        "soubor",
        "zpráva",
        "pdf analysis",
        "document parsing",
        "text extraction",
        "file analysis",
    ),
    "image_forensics": (
        "image",
        "photo",
        "picture",
        "visual",
        "exif",
        "obrázek",
        "fotografie",
        "foto",
        "vizuální",
        "image analysis",
        "photo analysis",
        "reverse image search",
    ),
    "temporal_analyzer": (
        "timeline",
        "history",
        "when",
        "date",
        "time",
        "časová osa",
        "historie",
        "kdy",
        "datum",
        "čas",
        "chronology",
        "time series",
        "temporal pattern",
        "event sequence",
    ),
    "geolocation_tracker": (
        "location",
        "where",
        "place",
        "geo",
        "gps",
        "lokace",
        "kde",
        "místo",
        "geolokace",
        "poloha",
        "coordinates",
        "address",
        "geographic",
        "mapping",
    ),
    "metadata_extractor": (
        "metadata",
        "exif",
        "header",
        "properties",
        "metadata",
        "hlavička",
        "vlastnosti",
        "file properties",
        "file metadata",
        "document properties",
        "technical data",
    ),
    "threat_assessor": (
        "threat",
        "risk",
        "danger",
        "malicious",
        "attack",
        "hrozba",
        "riziko",
        "nebezpečí",
        "škodlivý",
        "útoku",
        "security threat",
        "cyber threat",
        "risk assessment",
    ),
    "vulnerability_scanner": (
        "vulnerability",
        "exploit",
        "cve",
        "security flaw",
        "zranitelnost",
        "exploit",
        "bezpečnostní chyba",
        "security vulnerability",
        "zero day",
        "patch",
        "bug",
    ),
    "reputation_analyzer": (
        "reputation",
        "review",
        "rating",
        "feedback",
        "reputace",
        "recenze",
        "hodnocení",
        "zpětná vazba",
        "online reputation",
        "brand reputation",
        "customer review",
    ),
    "cross_reference_engine": (
        "cross reference",
        "verify",
        "corroborate",
        "confirm",
        "křížový odkaz",
        "ověřit",
        "potvrdit",
        "verifikovat",
        "fact check",
        "verification",
        "validate",
        "source verification",
    ),
}

PRIVACY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "MAXIMUM": (
        "dark web",
        "tor",
        "onion",
        "hidden service",
        "leak",
        "breach",
        "whistleblower",
        "sensitive",
        "classified",
        "confidential",
        "maximum privacy",
        "total anonymity",
        "untraceable",
        "temný web",
        "skrytá služba",
        "únik",
        "citlivý",
        "důvěrný",
    ),
    "HIGH": (
        "identity",
        "person",
        "profile",
        "who is",
        "private",
        "personal data",
        "pii",
        "high privacy",
        "anonymous",
        "identita",
        "osoba",
        "soukromý",
        "osobní údaje",
    ),
}

SOURCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "surface": (
        "web",
        "website",
        "internet",
        "google",
        "search",
        "news",
        "article",
        "blog",
        "forum",
        "social media",
    ),
    "archive": (
        "archive",
        "wayback",
        "historical",
        "deleted",
        "past",
        "archiv",
        "historický",
        "snapshot",
        "cached",
    ),
    "academic": (
        "research",
        "paper",
        "study",
        "scholar",
        "arxiv",
        "academic",
        "journal",
        "science",
        "publication",
        "výzkum",
        "studie",
        "vědecký",
        "publikace",
    ),
    "dark": (
        "dark web",
        "tor",
        "onion",
        "darknet",
        "hidden",
        "temný web",
        "skrytý",
        "skrytá služba",
    ),
    "leaked": (
        "leak",
        "breach",
        "exposed",
        "dump",
        "compromised",
        "unik",
        "únik",
        "zveřejněný",
        "kompromitovaný",
    ),
}

DEPTH_KEYWORDS: dict[str, tuple[str, ...]] = {
    "EXHAUSTIVE": (
        "comprehensive",
        "exhaustive",
        "complete",
        "thorough",
        "everything",
        "all information",
        "full analysis",
        "deep dive",
        "komplexní",
        "exhaustivní",
        "úplný",
        "důkladný",
        "maximum detail",
        "in-depth",
        "extensive",
    ),
    "DEEP": (
        "deep",
        "detailed",
        "extensive",
        "comprehensive",
        "analyze",
        "investigation",
        "research",
        "study",
        "hluboký",
        "detailní",
        "rozsáhlý",
        "analýza",
        "thorough analysis",
        "detailed report",
    ),
    "QUICK": (
        "quick",
        "brief",
        "summary",
        "overview",
        "fast",
        "rychlý",
        "stručný",
        "přehled",
        "shrnutí",
        "quick check",
        "brief overview",
        "short summary",
    ),
}

# Mappings
TOOL_SOURCE_MAPPING: dict[str, frozenset[str]] = {
    "stealth_crawler": frozenset({"dark"}),
    "archive_discovery": frozenset({"archive"}),
    "leak_hunter": frozenset({"leaked"}),
    "blockchain_analyzer": frozenset({"surface", "dark"}),
    "academic_search": frozenset({"academic"}),
    "identity_stitching": frozenset({"surface", "social"}),
    "relationship_discovery": frozenset({"surface", "social"}),
    "pattern_mining": frozenset({"surface", "archive"}),
    "web_intelligence": frozenset({"surface"}),
    "news_analyzer": frozenset({"surface"}),
    "forum_hunter": frozenset({"surface"}),
    "social_mapper": frozenset({"surface"}),
    "document_analyzer": frozenset({"surface", "archive"}),
    "image_forensics": frozenset({"surface"}),
    "temporal_analyzer": frozenset({"surface", "archive"}),
    "geolocation_tracker": frozenset({"surface"}),
    "metadata_extractor": frozenset({"surface"}),
    "threat_assessor": frozenset({"surface", "dark"}),
    "vulnerability_scanner": frozenset({"surface", "dark"}),
    "reputation_analyzer": frozenset({"surface"}),
    "cross_reference_engine": frozenset({"surface", "archive", "academic"}),
}

TOOL_MODEL_MAPPING: dict[str, frozenset[str]] = {
    "identity_stitching": frozenset({"gliner"}),
    "relationship_discovery": frozenset({"gliner"}),
    "pattern_mining": frozenset({"hermes"}),
    "document_analyzer": frozenset({"hermes", "modernbert"}),
    "image_forensics": frozenset({"hermes"}),
    "cross_reference_engine": frozenset({"hermes", "modernbert"}),
    "academic_search": frozenset({"modernbert"}),
}


@lru_cache(maxsize=32)
def _compile_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a single regex pattern with caching."""
    return re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)


@lru_cache(maxsize=64)  # Bounded cache for M1 8GB
def _compile_patterns_for_group(group: str, keywords: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile patterns for a keyword group with caching."""
    return tuple(_compile_pattern(kw) for kw in keywords)


class AutoResearchProfile:
    """
    Autonomous research configuration profile.

    Memory optimizations:
    - msgspec.Struct with gc=False (no cycle detection needed) if available
    - Falls back to dataclass-based implementation
    - All fields typed with __slots__ for memory efficiency
    """

    __slots__ = (
        "use_tot",
        "tot_mode",
        "tools",
        "sources",
        "privacy_level",
        "use_tor",
        "models_needed",
        "depth",
        "max_time",
        "reasoning",
    )

    def __init__(
        self,
        use_tot: bool = False,
        tot_mode: str = "standard",
        tools: set[str] | None = None,
        sources: set[str] | None = None,
        privacy_level: str = "STANDARD",
        use_tor: bool = False,
        models_needed: set[str] | None = None,
        depth: str = "STANDARD",
        max_time: float = 300.0,
        reasoning: str = "",
    ) -> None:
        self.use_tot = use_tot
        self.tot_mode = tot_mode
        self.tools = tools or set()
        self.sources = sources or set()
        self.privacy_level = privacy_level
        self.use_tor = use_tor
        self.models_needed = models_needed or set()
        self.depth = depth
        self.max_time = max_time
        self.reasoning = reasoning

    def to_dict(self) -> dict[str, Any]:
        """Convert profile to dictionary for serialization."""
        return {
            "use_tot": self.use_tot,
            "tot_mode": self.tot_mode,
            "tools": list(self.tools),
            "sources": list(self.sources),
            "privacy_level": self.privacy_level,
            "use_tor": self.use_tor,
            "models_needed": list(self.models_needed),
            "depth": self.depth,
            "max_time": self.max_time,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AutoResearchProfile:
        """Create profile from dictionary."""
        return cls(
            use_tot=data.get("use_tot", False),
            tot_mode=data.get("tot_mode", "standard"),
            tools=set(data.get("tools", [])),
            sources=set(data.get("sources", [])),
            privacy_level=data.get("privacy_level", "STANDARD"),
            use_tor=data.get("use_tor", False),
            models_needed=set(data.get("models_needed", [])),
            depth=data.get("depth", "STANDARD"),
            max_time=data.get("max_time", 300.0),
            reasoning=data.get("reasoning", ""),
        )


class ComplexityAnalyzer:
    """
    Strategy for complexity analysis.

    Can be replaced with alternative implementations.
    """

    __slots__ = ()

    # Keywords that indicate multi-step reasoning
    MULTI_STEP_KEYWORDS: tuple[str, ...] = (
        "analyze",
        "compare",
        "evaluate",
        "assess",
        "explain",
        "determine",
        "investigate",
        "explore",
        "examine",
        "analyzovat",
        "porovnat",
        "zhodnotit",
    )

    ALTERNATIVE_KEYWORDS: tuple[str, ...] = (
        "options",
        "pros and cons",
        "advantages",
        "alternatives",
        "compare",
        "versus",
        "vs",
        "trade-off",
        "možnosti",
        "výhody",
        "different approaches",
        "multiple strategies",
        "various methods",
    )

    COMPLEXITY_INDICATORS: tuple[str, ...] = (
        "considering",
        "factors",
        "aspects",
        "dimensions",
        "trade-offs",
        "implications",
        "consequences",
        "impacts",
    )

    def analyze(self, query: str) -> tuple[float, bool]:
        """
        Analyze query complexity.

        Returns:
            Tuple of (complexity_score, use_tot)
        """
        query_lower = query.lower()
        words = query_lower.split()
        word_count = len(words)
        score = 0.0

        # Multi-step indicator
        if any(kw in query_lower for kw in self.MULTI_STEP_KEYWORDS):
            score += 0.25

        # Question count
        question_count = query.count("?")
        score += min(0.3, question_count * 0.1)

        # Word count bands
        if word_count > 50:
            score += 0.25
        elif word_count > 30:
            score += 0.2
        elif word_count > 15:
            score += 0.1

        # Alternatives
        if any(kw in query_lower for kw in self.ALTERNATIVE_KEYWORDS):
            score += 0.2

        # Complexity indicators
        if any(ind in query_lower for ind in self.COMPLEXITY_INDICATORS):
            score += 0.15

        # Comma count
        comma_count = query.count(",")
        if comma_count >= 3:
            score += 0.15
        elif comma_count >= 2:
            score += 0.1

        score = min(1.0, score)
        use_tot = score >= 0.6
        return (score, use_tot)


class AutonomousAnalyzer:
    """
    Autonomous query analyzer for intelligent research configuration.

    Memory optimizations for M1 8GB:
    - __slots__ for minimal instance memory
    - Lazy pattern compilation with caching
    - frozenset for immutable mappings

    Usage:
        >>> analyzer = AutonomousAnalyzer()
        >>> profile = analyzer.analyze("Find information about Bitcoin wallet addresses on dark web")
        >>> print(profile.tools)  # {'stealth_crawler', 'blockchain_analyzer'}
        >>> print(profile.privacy_level)  # 'MAXIMUM'
        >>> print(profile.use_tor)  # True
    """

    __slots__ = (
        "_tot_integration",
        "_complexity_analyzer",
        "_tool_patterns",
        "_privacy_patterns",
        "_source_patterns",
        "_depth_patterns",
    )

    def __init__(
        self,
        tot_integration: Any | None = None,
        complexity_analyzer: ComplexityAnalyzer | None = None,
    ) -> None:
        """
        Initialize AutonomousAnalyzer.

        Args:
            tot_integration: Optional TotIntegrationLayer for complexity analysis
            complexity_analyzer: Optional custom complexity analyzer strategy
        """
        self._tot_integration = tot_integration
        self._complexity_analyzer = complexity_analyzer or ComplexityAnalyzer()

        # Lazy-initialized pattern caches
        self._tool_patterns: dict[str, tuple[re.Pattern[str], ...]] | None = None
        self._privacy_patterns: dict[str, tuple[re.Pattern[str], ...]] | None = None
        self._source_patterns: dict[str, tuple[re.Pattern[str], ...]] | None = None
        self._depth_patterns: dict[str, tuple[re.Pattern[str], ...]] | None = None

        logger.info("AutonomousAnalyzer initialized (v2.0.0)")

    def _ensure_patterns(self) -> None:
        """Lazily initialize compiled patterns."""
        if self._tool_patterns is None:
            self._tool_patterns = {
                tool: _compile_patterns_for_group(tool, keywords) for tool, keywords in TOOL_KEYWORDS.items()
            }
        if self._privacy_patterns is None:
            self._privacy_patterns = {
                level: _compile_patterns_for_group(level, keywords) for level, keywords in PRIVACY_KEYWORDS.items()
            }
        if self._source_patterns is None:
            self._source_patterns = {
                source: _compile_patterns_for_group(source, keywords) for source, keywords in SOURCE_KEYWORDS.items()
            }
        if self._depth_patterns is None:
            self._depth_patterns = {
                depth: _compile_patterns_for_group(depth, keywords) for depth, keywords in DEPTH_KEYWORDS.items()
            }

    def analyze(self, query: str) -> AutoResearchProfile:
        """
        Analyze query and generate optimal research profile.

        Args:
            query: Research query to analyze

        Returns:
            AutoResearchProfile with optimal configuration
        """
        self._ensure_patterns()

        logger.info(f"Analyzing query: {query[:100]}...")

        # Detect tools
        tools = self._detect_tools(query)
        logger.info(f"🎯 AUTONOMOUS DECISION: Tools selected = {tools} (reason: keyword matching)")

        # Infer sources
        sources = self._infer_sources(tools, query)
        logger.info(f"🎯 AUTONOMOUS DECISION: Sources = {sources} (reason: tool-to-source inference)")

        # Determine privacy
        privacy_level, use_tor = self._determine_privacy(query, tools)
        logger.info(
            f"🎯 AUTONOMOUS DECISION: Privacy = {privacy_level}, Tor = {use_tor} (reason: sensitivity analysis)"
        )

        # Analyze complexity and ToT
        complexity, use_tot = self._analyze_complexity(query)
        logger.info(f"🎯 AUTONOMOUS DECISION: ToT = {use_tot} (complexity: {complexity:.2f})")

        tot_mode = self._determine_tot_mode(complexity)
        logger.info(f"🎯 AUTONOMOUS DECISION: ToT mode = {tot_mode}")

        depth = self._estimate_depth(len(tools), complexity)
        logger.info(f"🎯 AUTONOMOUS DECISION: Depth = {depth}")

        profile = AutoResearchProfile(
            use_tot=use_tot,
            tot_mode=tot_mode,
            tools=tools,
            sources=sources,
            privacy_level=privacy_level,
            use_tor=use_tor,
            depth=depth,
        )

        profile.models_needed = self._determine_models(profile)
        logger.info(f"🎯 AUTONOMOUS DECISION: Models = {profile.models_needed}")

        profile.max_time = self._estimate_time(profile)
        logger.info(f"🎯 AUTONOMOUS DECISION: Max time = {profile.max_time:.0f}s")

        profile.reasoning = self._generate_reasoning(profile)
        logger.info("🎯 AUTONOMOUS DECISION: Reasoning generated")

        return profile

    def _detect_tools(self, query: str) -> set[str]:
        """Detect which intelligence tools should be activated."""
        detected: set[str] = set()
        query_lower = query.lower()

        for tool, patterns in self._tool_patterns.items():
            if any(p.search(query_lower) for p in patterns):
                detected.add(tool)

        # Default to web_intelligence if nothing detected
        if not detected:
            detected.add("web_intelligence")

        # Add cross-reference for complex queries
        if len(detected) >= 3:
            detected.add("cross_reference_engine")

        return detected

    def _infer_sources(self, tools: set[str], query: str) -> set[str]:
        """Infer which sources to search based on selected tools."""
        sources: set[str] = set()

        # Map tools to sources
        for tool in tools:
            if tool in TOOL_SOURCE_MAPPING:
                sources.update(TOOL_SOURCE_MAPPING[tool])

        # Additional keyword-based inference
        query_lower = query.lower()
        for source, patterns in self._source_patterns.items():
            if any(p.search(query_lower) for p in patterns):
                sources.add(source)

        # Always include surface
        sources.add("surface")
        return sources

    def _determine_privacy(self, query: str, tools: set[str]) -> tuple[str, bool]:
        """Determine privacy level and Tor activation."""
        query_lower = query.lower()

        for pattern in self._privacy_patterns.get("MAXIMUM", ()):
            if pattern.search(query_lower):
                return ("MAXIMUM", True)

        for pattern in self._privacy_patterns.get("HIGH", ()):
            if pattern.search(query_lower):
                return ("HIGH", False)

        high_privacy_tools = frozenset({"stealth_crawler", "leak_hunter", "threat_assessor"})
        if tools & high_privacy_tools:
            return ("HIGH", "stealth_crawler" in tools)

        return ("STANDARD", False)

    def _analyze_complexity(self, query: str) -> tuple[float, bool]:
        """Analyze query complexity and determine ToT activation."""
        if self._tot_integration is not None:
            try:
                should_use, confidence = self._tot_integration.should_activate_tot(query)
                analysis = self._tot_integration.analyze_complexity(query)
                return (analysis.score, should_use)
            except Exception as e:
                logger.warning(f"ToT integration failed, using fallback: {e}")

        return self._complexity_analyzer.analyze(query)

    def _determine_tot_mode(self, complexity: float) -> str:
        """Determine ToT mode based on complexity."""
        match complexity:
            case c if c >= 0.8:
                return "full"
            case c if c >= 0.6:
                return "hybrid"
            case _:
                return "standard"

    def _estimate_depth(self, tool_count: int, complexity: float) -> str:
        """Estimate research depth based on tools and complexity."""
        depth_score = complexity + tool_count * 0.1

        match depth_score:
            case s if s >= 1.0:
                return "EXHAUSTIVE"
            case s if s >= 0.7:
                return "DEEP"
            case s if s <= 0.3:
                return "QUICK"
            case _:
                return "STANDARD"

    def _determine_models(self, profile: AutoResearchProfile) -> set[str]:
        """Determine which models are needed based on profile."""
        models: set[str] = set()

        for tool in profile.tools:
            if tool in TOOL_MODEL_MAPPING:
                models.update(TOOL_MODEL_MAPPING[tool])

        models.add("hermes")
        if len(profile.tools) > 2:
            models.add("modernbert")

        return models

    def _estimate_time(self, profile: AutoResearchProfile) -> float:
        """Estimate execution time based on profile."""
        base_time = 60.0
        tool_multiplier = 1.0 + len(profile.tools) * 0.2

        depth_multipliers = {
            "QUICK": 0.5,
            "STANDARD": 1.0,
            "DEEP": 2.0,
            "EXHAUSTIVE": 4.0,
        }
        depth_multiplier = depth_multipliers.get(profile.depth, 1.0)
        tot_multiplier = 1.5 if profile.use_tot else 1.0
        privacy_multiplier = 1.3 if profile.use_tor else 1.0

        estimated_time = base_time * tool_multiplier * depth_multiplier * tot_multiplier * privacy_multiplier
        return min(estimated_time, 1800.0)

    def _generate_reasoning(self, profile: AutoResearchProfile) -> str:
        """Generate human-readable reasoning for the profile."""
        parts: list[str] = []

        if profile.tools:
            tool_list = ", ".join(sorted(profile.tools))
            parts.append(f"Detected {len(profile.tools)} intelligence tools: {tool_list}")

        if profile.sources:
            source_list = ", ".join(sorted(profile.sources))
            parts.append(f"Will search across {len(profile.sources)} source types: {source_list}")

        if profile.privacy_level == "MAXIMUM":
            parts.append("Maximum privacy required due to sensitive nature of query")
        elif profile.privacy_level == "HIGH":
            parts.append("High privacy level selected for identity-related research")

        if profile.use_tor:
            parts.append("Tor network enabled for anonymous access to dark web sources")

        if profile.use_tot:
            parts.append(f"Tree of Thoughts ({profile.tot_mode} mode) activated for complex reasoning")

        if profile.depth == "EXHAUSTIVE":
            parts.append("Exhaustive depth selected for comprehensive coverage")
        elif profile.depth == "DEEP":
            parts.append("Deep analysis mode for thorough investigation")
        elif profile.depth == "QUICK":
            parts.append("Quick scan mode for rapid overview")

        parts.append(f"Estimated execution time: {profile.max_time:.0f} seconds")
        return "; ".join(parts)

    def get_capabilities(self) -> dict[str, Any]:
        """Get analyzer capabilities."""
        return {
            "name": "autonomous_analyzer",
            "version": "2.0.0",
            "tools_supported": len(TOOL_KEYWORDS),
            "sources_supported": list(SOURCE_KEYWORDS.keys()),
            "privacy_levels": list(PRIVACY_KEYWORDS.keys()),
            "depth_levels": ["QUICK", "STANDARD", "DEEP", "EXHAUSTIVE"],
            "tot_modes": ["standard", "hybrid", "full"],
            "models": ["hermes", "modernbert", "gliner"],
            "languages": ["en", "cs"],
        }

    def health_check(self) -> bool:
        """Check if analyzer is operational."""
        try:
            test_profile = self.analyze("test query")
            return test_profile is not None and bool(test_profile.tools)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


def create_autonomous_analyzer(
    tot_integration: Any | None = None,
) -> AutonomousAnalyzer:
    """
    Create AutonomousAnalyzer with optional ToT integration.

    Args:
        tot_integration: Optional TotIntegrationLayer

    Returns:
        Configured AutonomousAnalyzer
    """
    return AutonomousAnalyzer(tot_integration)
