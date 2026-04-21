"""
GhostExecutor - Vykonávací engine pro UniversalResearchOrchestrator

Implementuje 14+ akcí pro deep research:
- SCAN, GOOGLE, DOWNLOAD, SEARCH, SMART_SEARCH
- MEMORIZE, PROBE, TRACK
- RESEARCH_PAPER, DEEP_RESEARCH, DEEP_READ
- ANSWER, CRACK, ERROR
- ARCHIVE_FALLBACK, FACT_CHECK, STEALTH_HARVEST, OSINT_DISCOVERY

Sprint 8VF BRIDGE SEAM:
═══════════════════════════════════════════════════════════════
GhostExecutor je DONOR/COMPAT vrstva — není canonical authority.
Canonical authority: ToolRegistry.execute_with_limits()

Tento soubor obsahuje THIN TYPED BRIDGE SEAM pro budoucí cutover:
- _ACTION_TO_CANONICAL_TOOL: read-only mapping Ghost akce → canonical tool name
- GhostBridge.to_execution_request(): Ghost params → ExecutionRequest
- GhostBridge.to_execution_result(): Ghost ActionResult → ExecutionResult

BRIDGE je READ-SIDE ADAPTER — nemění GhostExecutor.execute() path.
BRIDGE nevolá ToolRegistry.execute_with_limits() — jen typovou konverzi.

REMOVAL CONDITION:
Až všechny Ghost akce (SCAN, GOOGLE, DEEP_READ, STEALTH_HARVEST,
OSINT_DISCOVERY, atd.) budou migrovány do ToolRegistry jako proper
Tool handlery, GhostExecutor se stane kandidátem na deprecation.
Do té doby zůstává donor/compat vrstvou s tímto bridge seamem.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.project_types import ExecutionRequest, ExecutionResult, RunCorrelation

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """
    Typy akcí.

    NON-CANONICAL — DUPLICATE of types.py:119 ActionResultType.
    Sprint 8VF: This enum is a LOCAL scaffold in the Ghost layer.
    It is NOT the canonical action taxonomy.

    The canonical ActionResultType enum lives in types.py:42.
    GhostExecutor uses this local enum because its action handlers
    predate the canonical scaffold.

    BRIDGE NOTE (Sprint 8VF):
    GhostBridge._ACTION_TO_CANONICAL_TOOL provides a read-only mapping
    from this local enum to canonical tool names.

    MIGRATION: When all Ghost actions migrate to ToolRegistry, this enum
    becomes deprecated in favor of types.py ActionResultType.

    See: types.py CANONICAL SCAFFOLD header (line 1269)
    """
    SCAN = "scan"
    GOOGLE = "google"
    DOWNLOAD = "download"
    SEARCH = "search"
    SMART_SEARCH = "smart_search"
    MEMORIZE = "memorize"
    PROBE = "probe"
    TRACK = "track"
    RESEARCH_PAPER = "research_paper"
    DEEP_RESEARCH = "deep_research"
    DEEP_READ = "deep_read"
    ANSWER = "answer"
    CRACK = "crack"
    ERROR = "error"
    ARCHIVE_FALLBACK = "archive_fallback"
    FACT_CHECK = "fact_check"
    STEALTH_HARVEST = "stealth_harvest"
    OSINT_DISCOVERY = "osint_discovery"


@dataclass
class ActionResult:
    """Výsledek akce"""
    success: bool
    action: str
    data: Dict[str, Any]
    error: Optional[str] = None
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "data": self.data,
            "error": self.error,
            "execution_time": self.execution_time,
        }


# =============================================================================
# Sprint 8VF: Thin Typed Bridge Seam
# =============================================================================
# READ-SIDE ADAPTER — nemění GhostExecutor.execute() path
# Pouze typová konverze mezi Ghost světem a canonical ExecutionRequest/ExecutionResult
# Neprodukuje side effects, nevolá ToolRegistry.execute_with_limits()
# =============================================================================

# =============================================================================
# Sprint 8VF: Delegation Matrix — Ghost Action → Canonical Tool
# =============================================================================
# STRICT CLASSIFICATION — nepřekládat vyšší coverage než repo skutečně má
#
# 3 kategorie:
#   canonical_ready       = plná sémantická shoda, lze delegovat
#   mapped_but_lossy      = mapping exists, ale sémantika ≠ (viz blokery)
#   runtime_only_compat   = žádný canonical ekvivalent, zůstává v Ghost path
#
# Audit pravidla:
#   - canonical_ready pouze když Ghost action a Tool handler dělajÍ TOTÉŽ
#   - mapped_but_lossy když mapping existuje ale sémantika se liší
#   - runtime_only_compat když není canonical ekvivalent
# =============================================================================

# === CANONICAL-READY SLICE ===
# Kritérium: Ghost action a ToolRegistry tool DĚLAJÍ TOTÉŽ (ne jen podobně)
# Aktuální stav: ŽÁDNÁ akce nesplňuje toto kritérium.
# Důvod: Ghost akce mají vlastní implementace (byť placeholder),
#        ToolRegistry handlery jsou placeholder/univerzální.
# SPRINT 8VF: Delegation seam existuje, ale canonical-ready slice je PRÁZDNÁ.
_CANONICAL_READY_ACTIONS: Set[str] = set()  # prázdná — audit viz níže

# === MAPPED-BUT-LOSSY SLICE ===
# Kritérium: Ghost action má mapping na ToolRegistry tool,
#            ale sémantika provádění se ZÁSADNĚ liší.
#
# Blokery pro canonical-ready:
#   SEARCH/GOOGLE/SMART_SEARCH → web_search:
#     Ghost: harvestuje URL přes network driver + stealth + Bloom dedup
#     TR: placeholder handler (vrací prázdné výsledky)
#     Sémantika ≠ → MAPPED-BUT-LOSSY, ne canonical-ready
#
#   RESEARCH_PAPER → academic_search:
#     Ghost: placeholder (vrací prázdné)
#     TR: placeholder (vrací prázdné)
#     Žádný skutečný obsah → MAPPED-BUT-LOSSY
#
#   DEEP_READ → file_read:
#     Ghost: URL harvest + BloomFilter dedup + context update + 8KB chunking + 5MB limit
#     TR: local file read (žádný network, žádný Bloom, žádný context)
#     Sémantika ≠ → MAPPED-BUT-LOSSY
#
_MAPPED_BUT_LOSSY_ACTIONS: Set[str] = {
    ActionType.SEARCH.value,
    ActionType.GOOGLE.value,
    ActionType.SMART_SEARCH.value,
    ActionType.RESEARCH_PAPER.value,
    ActionType.DEEP_READ.value,
}

# === RUNTIME-ONLY-COMPAT SLICE ===
# Kritérium: žádný canonical ekvivalent v ToolRegistry
_RUNTIME_ONLY_COMPAT_ACTIONS: Set[str] = {
    ActionType.SCAN.value,
    ActionType.DOWNLOAD.value,
    ActionType.MEMORIZE.value,
    ActionType.PROBE.value,
    ActionType.TRACK.value,
    ActionType.DEEP_RESEARCH.value,
    ActionType.ANSWER.value,
    ActionType.CRACK.value,
    ActionType.ERROR.value,
    ActionType.ARCHIVE_FALLBACK.value,
    ActionType.FACT_CHECK.value,
    ActionType.STEALTH_HARVEST.value,
    ActionType.OSINT_DISCOVERY.value,
}

# === UNIFIED MAPPING pro diagnostiku a delegation seam ===
# Současný stav: pouze diagnostický — obsahuje i mapped-but-lossy položky
# Future: po F9 cutover bude obsahovat pouze canonical-ready
_ACTION_TO_CANONICAL_TOOL: Dict[str, str] = {
    # Mapped (ale lossy) akce
    ActionType.SEARCH.value: "web_search",
    ActionType.GOOGLE.value: "web_search",
    ActionType.SMART_SEARCH.value: "web_search",
    ActionType.RESEARCH_PAPER.value: "academic_search",
    ActionType.DEEP_READ.value: "file_read",
    # Runtime-only akce — žádný canonical ekvivalent
    ActionType.SCAN.value: "",
    ActionType.DOWNLOAD.value: "",
    ActionType.MEMORIZE.value: "",
    ActionType.PROBE.value: "",
    ActionType.TRACK.value: "",
    ActionType.DEEP_RESEARCH.value: "",
    ActionType.ANSWER.value: "",
    ActionType.CRACK.value: "",
    ActionType.ERROR.value: "",
    ActionType.ARCHIVE_FALLBACK.value: "",
    ActionType.FACT_CHECK.value: "",
    ActionType.STEALTH_HARVEST.value: "",
    ActionType.OSINT_DISCOVERY.value: "",
}


def _get_action_classification(action: str) -> str:
    """
    Vrátí klasifikaci Ghost akce.

    Returns:
        "canonical_ready"      — lze delegovat na ToolRegistry path
        "mapped_but_lossy"     — má mapping, ale sémantika ≠
        "runtime_only_compat" — žádný canonical ekvivalent
    """
    if action in _CANONICAL_READY_ACTIONS:
        return "canonical_ready"
    elif action in _MAPPED_BUT_LOSSY_ACTIONS:
        return "mapped_but_lossy"
    elif action in _RUNTIME_ONLY_COMPAT_ACTIONS:
        return "runtime_only_compat"
    return "unknown"


class GhostBridge:
    """
    Sprint 8VF: Thin typed bridge — READ-SIDE ADAPTER mezi Ghost a canonical světem.

    ÚČEL:
    - Konvertuje Ghost akce/params na ExecutionRequest bez volání ToolRegistry
    - Konvertuje Ghost ActionResult na ExecutionResult bez side effects
    - Poskytuje read-only metadata pro dispatch parity preview

    CO NENÍ:
    - Není nový execution framework
    - Nemění GhostExecutor.execute() path
    - Nevolá ToolRegistry.execute_with_limits()
    - Neprodukuje side effects

    INVARIANTS:
    - execute_with_limits() zůstává jediný canonical execution-control surface
    - GhostExecutor zůstává donor/compat
    - Bridge je read-side adapter, ne execution authority

    REMOVAL CONDITION:
    Až všechny Ghost akce migrují do ToolRegistry, bridge zůstává
    jako backward-compat pro existující volající kód.
    """

    @staticmethod
    def action_has_canonical_tool(action: str) -> bool:
        """
        Vrátí True pokud Ghost akce má mapping na ToolRegistry tool.

        DIAGNOSTIC ONLY — čte z _ACTION_TO_CANONICAL_TOOL.
        """
        canonical = _ACTION_TO_CANONICAL_TOOL.get(action, "")
        return bool(canonical)

    @staticmethod
    def get_canonical_tool_name(action: str) -> str:
        """
        Vrátí canonical tool name pro Ghost akci, nebo prázdný string.

        DIAGNOSTIC ONLY — read-only mapping lookup.
        """
        return _ACTION_TO_CANONICAL_TOOL.get(action, "")

    @staticmethod
    def to_execution_request(
        action: str,
        params: Dict[str, Any],
        priority: int = 5,
        correlation: Optional["RunCorrelation"] = None,
    ) -> "ExecutionRequest":
        """
        Konvertuje Ghost akci + params na canonical ExecutionRequest.

        TOTO JE TYPOVÁ KONVERZE — žádné volání ToolRegistry.execute_with_limits().
        Výsledný ExecutionRequest lze předat do ToolRegistry.execute_with_limits()
        v budoucím cutover kroku.

        Args:
            action: Ghost ActionType string (např. "google", "deep_read")
            params: Parametry akce
            priority: Execution priority (1-10, lower = higher priority)
            correlation: Optional run correlation context

        Returns:
            ExecutionRequest — canonical typed request

        NOTE: Tato funkce neověřuje, zda action má canonical tool mapping.
        Použij action_has_canonical_tool() pro diagnostiku před konverzí.
        """
        from hledac.universal.project_types import ExecutionRequest as _ExecReq
        return _ExecReq(
            action_type=action,
            parameters=params,
            priority=priority,
            correlation=correlation,
        )

    @staticmethod
    def to_execution_result(
        ghost_result: ActionResult,
        correlation: Optional["RunCorrelation"] = None,
    ) -> "ExecutionResult":
        """
        Konvertuje Ghost ActionResult na canonical ExecutionResult.

        TOTO JE TYPOVÁ KONVERZE — žádné side effects.

        Args:
            ghost_result: Ghost ActionResult z execute()
            correlation: Optional run correlation (echoed from request)

        Returns:
            ExecutionResult — canonical typed result

        NOTE: execution_time je echo zpět z GhostResult, nemeasured
        canonical execution time (protože Ghost nevolá execute_with_limits).
        """
        from hledac.universal.project_types import ExecutionResult as _ExecRes
        return _ExecRes(
            action_type=ghost_result.action,
            success=ghost_result.success,
            data=ghost_result.data,
            execution_time=ghost_result.execution_time,
            error=ghost_result.error,
            correlation=correlation,
        )

    @staticmethod
    def get_action_classification(action: str) -> str:
        """
        Vrátí klasifikaci Ghost akce.

        Returns:
            "canonical_ready"      — lze delegovat na ToolRegistry path
            "mapped_but_lossy"     — má mapping, ale sémantika ≠
            "runtime_only_compat"  — žádný canonical ekvivalent
        """
        return _get_action_classification(action)

    @staticmethod
    def is_delegation_allowed(action: str) -> bool:
        """
        Vrátí True pokud lze akci delegovat na ToolRegistry path.

        Delegation je povolena POUZE pro canonical-ready akce.
        mapped_but_lossy akce mají mapping, ale sémantika ≠ —
        delegace by ztratila funkčnost.

        Returns:
            True pouze pro canonical-ready akce
        """
        return _get_action_classification(action) == "canonical_ready"

    @staticmethod
    def to_delegation_request(
        action: str,
        params: Dict[str, Any],
        priority: int = 5,
        correlation: Optional["RunCorrelation"] = None,
    ) -> Optional["ExecutionRequest"]:
        """
        Konvertuje Ghost akci na canonical ExecutionRequest PRO CANONICAL-READY AKCE.

        Používá _ACTION_TO_CANONICAL_TOOL mapping a překládá Ghost action
        na canonical tool name v action_type poli.

        Args:
            action: Ghost ActionType string (např. "google", "deep_read")
            params: Parametry akce
            priority: Execution priority (1-10, lower = higher priority)
            correlation: Optional run correlation context

        Returns:
            ExecutionRequest s canonical tool name v action_type,
            NEBO None pokud akce není canonical-ready.

        Delegation rules:
            canonical_ready   → vrací ExecutionRequest s canonical tool name
            mapped_but_lossy  → vrací None (sémantika ≠, nelze beztrastně delegovat)
            runtime_only_compat → vrací None (žádný canonical ekvivalent)
        """
        if not GhostBridge.is_delegation_allowed(action):
            return None

        canonical_tool = _ACTION_TO_CANONICAL_TOOL.get(action, "")
        if not canonical_tool:
            return None

        from hledac.universal.project_types import ExecutionRequest as _ExecReq
        return _ExecReq(
            action_type=canonical_tool,
            parameters=params,
            priority=priority,
            correlation=correlation,
        )

    @staticmethod
    def get_delegation_matrix() -> Dict[str, str]:
        """
        Vrátí kompletní klasifikační matici Ghost akcí.

        Returns:
            Dict mapping action → classification pro všechny Ghost akce.
            Použitelné pro audit a dispatch parity preview.
        """
        matrix = {}
        for action in _ACTION_TO_CANONICAL_TOOL:
            matrix[action] = _get_action_classification(action)
        return matrix

    @staticmethod
    def get_canonical_ready_actions() -> Set[str]:
        """Vrátí set canonical-ready akcí (prázdná množina v current repo)."""
        return set(_CANONICAL_READY_ACTIONS)

    @staticmethod
    def get_mapped_but_lossy_actions() -> Set[str]:
        """Vrátí set mapped-but-lossy akcí."""
        return set(_MAPPED_BUT_LOSSY_ACTIONS)

    @staticmethod
    def get_runtime_only_compat_actions() -> Set[str]:
        """Vrátí set runtime-only-compat akcí."""
        return set(_RUNTIME_ONLY_COMPAT_ACTIONS)

    @staticmethod
    def get_action_canonical_tool_mapping() -> Dict[str, str]:
        """
        Vrátí kopii read-only _ACTION_TO_CANONICAL_TOOL mappingu.

        DIAGNOSTIC ONLY — pro dispatch parity preview a audit.
        """
        return dict(_ACTION_TO_CANONICAL_TOOL)


class GhostExecutor:
    """
    Donor/compatibility backend for research actions.

    ROLE (Sprint 8VF):
    ════════════════════════════════════════════════════════
    This class is a DONOR/COMPATIBILITY backend — NOT the
    canonical execution-control surface.

    Canonical authority: ToolRegistry.execute_with_limits()
    Donor reason:          Gradual migration from ActionType-based
                           actions to Tool-based handlers

    REMOVAL CONDITION:
    When all GhostExecutor actions (SCAN, GOOGLE, DEEP_READ,
    STEALTH_HARVEST, OSINT_DISCOVERY, etc.) are migrated to
    ToolRegistry as proper Tool handlers, this class becomes
    a candidate for deprecation.

    BOUNDARY SEAMS:
    - Uses ActionType enum (NOT Tool model)
    - Has own _actions dict (NOT _tools registry)
    - execute() does NOT call ToolRegistry.execute_with_limits()
    - Action handlers live here, not in ToolRegistry

    INTEGRATION NOTE:
    - Do NOT reference GhostExecutor as "the executor" in docs
    - Use ToolRegistry as the primary execution surface
    - GhostExecutor.execute() is a SEPARATE execution path
    ════════════════════════════════════════════════════════

    Integruje:
    - GhostNetworkDriver pro web crawling
    - StealthManager pro stealth operace
    - ArchiveDiscovery pro Wayback fallback
    - Bloom Filter pro deduplikaci
    """
    
    def __init__(self, enable_stealth: bool = True):
        self.enable_stealth = enable_stealth
        
        # Lazy-loaded komponenty
        self._network_driver = None
        self._stealth_manager = None
        self._bloom_filter = None
        
        # Registr akcí
        self._actions = self._init_actions()
        
    def _init_actions(self) -> Dict[str, callable]:
        """Inicializovat mapování akcí"""
        return {
            ActionType.SCAN.value: self._action_scan,
            ActionType.GOOGLE.value: self._action_google,
            ActionType.DOWNLOAD.value: self._action_download,
            ActionType.SEARCH.value: self._action_search,
            ActionType.SMART_SEARCH.value: self._action_smart_search,
            ActionType.MEMORIZE.value: self._action_memorize,
            ActionType.PROBE.value: self._action_probe,
            ActionType.TRACK.value: self._action_track,
            ActionType.RESEARCH_PAPER.value: self._action_research_paper,
            ActionType.DEEP_RESEARCH.value: self._action_deep_research,
            ActionType.DEEP_READ.value: self._action_deep_read,
            ActionType.ANSWER.value: self._action_answer,
            ActionType.CRACK.value: self._action_crack,
            ActionType.ARCHIVE_FALLBACK.value: self._action_archive_fallback,
            ActionType.FACT_CHECK.value: self._action_fact_check,
            ActionType.STEALTH_HARVEST.value: self._action_stealth_harvest,
            ActionType.OSINT_DISCOVERY.value: self._action_osint_discovery,
            ActionType.ERROR.value: self._action_error,
        }
    
    async def initialize(self) -> None:
        """Inicializovat executor"""
        logger.info("Initializing GhostExecutor...")
        
        # Inicializovat Bloom Filter pro URL deduplikaci
        await self._init_bloom_filter()
        
        logger.info("✓ GhostExecutor initialized")
    
    async def _init_bloom_filter(self) -> None:
        """Inicializovat Bloom Filter — bounded RotatingBloomFilter."""
        try:
            # Sprint F1: Use canonical RotatingBloomFilter from url_dedup (bounded)
            # instead of ScalableBloomFilter (unbounded growth)
            from ..tools.url_dedup import create_rotating_bloom_filter
            self._bloom_filter = create_rotating_bloom_filter(
                est_elements=10000,
                false_positive_rate=0.01
            )
            logger.info("✓ Bloom Filter initialized (RotatingBloomFilter, 10K URLs, bounded)")
        except Exception as e:
            logger.warning(f"Bloom Filter not available: {e}")
    
    async def _get_network_driver(self):
        """Lazy load network driver"""
        if self._network_driver is None:
            from hledac.network.ghost_network_driver import GhostNetworkDriver
            logger.info("Loading GhostNetworkDriver...")
            self._network_driver = GhostNetworkDriver(headless=True)
            await self._network_driver.initialize()
            logger.info("✓ GhostNetworkDriver loaded")
        return self._network_driver
    
    async def _get_stealth_manager(self):
        """
        Lazy load stealth manager.

        NOTE (Sprint F900G): This import path was stale — hledac.stealth_toolkit
        does not exist as a top-level package. The canonical stealth system lives at:
        - hledac.universal.stealth.stealth_manager (active, canonical)
        - hledac.outdated.stealth_toolkit (deprecated, donor compat)

        This lazy-load path is kept for backward compat with existing call-sites
        that pass enable_stealth=True. If module resolution fails, returns None
        (degraded stub) rather than crashing.
        """
        if self._stealth_manager is None and self.enable_stealth:
            try:
                # Sprint F900G: Truthful import path — was hledac.stealth_toolkit
                # (non-existent), redirecting to actual deprecated location
                from hledac.outdated.stealth_toolkit.stealth_orchestrator import (
                    StealthOrchestrator as _SO
                )
                logger.info("Loading StealthOrchestrator (outdated path)...")
                self._stealth_manager = _SO()
                logger.info("✓ StealthOrchestrator loaded (degraded/stub)")
            except ModuleNotFoundError:
                logger.warning(
                    "StealthOrchestrator not available — "
                    "hledac.outdated.stealth_toolkit not in path. "
                    "Stealth features will be degraded/stub."
                )
                self._stealth_manager = None
        return self._stealth_manager
    
    async def execute(
        self,
        action: str,
        params: Dict[str, Any],
        context=None
    ) -> Dict[str, Any]:
        """
        Vykonat akci.
        
        Args:
            action: Typ akce
            params: Parametry akce
            context: Výzkumný kontext
            
        Returns:
            Výsledek akce jako slovník
        """
        logger.info(f"Executing action: {action}")
        
        # Najít handler
        handler = self._actions.get(action)
        if handler is None:
            return ActionResult(
                success=False,
                action=action,
                data={},
                error=f"Unknown action: {action}"
            ).to_dict()
        
        # Vykonat
        try:
            result = await handler(params, context)
            return result
        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return ActionResult(
                success=False,
                action=action,
                data={},
                error=str(e)
            ).to_dict()
    
    # =====================================================================
    # AKCE
    # =====================================================================
    
    async def _action_search(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """Vyhledávání"""
        query = params.get("query", "")
        logger.info(f"Searching: {query}")
        
        # TODO: Implementovat vlastní vyhledávání nebo Google
        return ActionResult(
            success=True,
            action="search",
            data={"query": query, "results": []},
        ).to_dict()
    
    async def _action_google(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """Google vyhledávání"""
        query = params.get("query", "")
        logger.info(f"Google search: {query}")
        
        try:
            driver = await self._get_network_driver()
            
            # Použít stealth pokud je povolen
            if self.enable_stealth:
                stealth_mgr = await self._get_stealth_manager()
                if stealth_mgr:
                    # TODO: Implementovat stealth google search
                    pass
            
            # Prozatím placeholder
            return ActionResult(
                success=True,
                action="google",
                data={"query": query, "results": []},
            ).to_dict()
            
        except Exception as e:
            return ActionResult(
                success=False,
                action="google",
                data={},
                error=str(e)
            ).to_dict()
    
    async def _action_deep_read(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """
        Bezpečné čtení obsahu z URL (8KB chunks, 5MB limit).

        Bezpečně stáhne a přečte obsah z URL s ochranou před velkými soubory.

        CONTEXT CARRIER COMPATIBILITY (Sprint F900A):
        - Používá add_visited_url() místo přímého .add() pro správné
          domain extraction přes ResearchContext carrier
        - Nezapisuje content_hashes/collected_data přímo na context —
          tyto atributy existují pouze na ExecutionContext (types.py),
          ne na ResearchContext (research_context.py)
        - Fallback: používá pouze visited_urls.add() pro compatibility
        """
        url = params.get("url", "")

        # Kontrola duplicity
        if self._bloom_filter and self._bloom_filter.contains(url):
            logger.info(f"Skipping duplicate URL: {url}")
            return ActionResult(
                success=True,
                action="deep_read",
                data={"url": url, "skipped": True, "reason": "duplicate"},
            ).to_dict()

        logger.info(f"Deep reading: {url}")

        try:
            driver = await self._get_network_driver()

            # Harvestovat stránku
            result = await driver.harvest(url)

            if result.success:
                # Přidat do Bloom Filter
                if self._bloom_filter:
                    self._bloom_filter.add(url)

                # Přidat do kontextu přes canonical carrier method
                if context:
                    # Sprint F900A: Use add_visited_url() instead of direct .add()
                    # This properly extracts domain and updates timestamp
                    if hasattr(context, 'add_visited_url'):
                        context.add_visited_url(url)
                    elif hasattr(context, 'visited_urls'):
                        # Fallback for ExecutionContext (types.py)
                        context.visited_urls.add(url)
                    # Note: content_hashes and collected_data are NOT written here
                    # They exist on ExecutionContext but NOT on ResearchContext.
                    # Writing them directly would cause AttributeError with RC.

                return ActionResult(
                    success=True,
                    action="deep_read",
                    data={
                        "url": url,
                        "title": result.metadata.get("title", ""),
                        "content_length": len(result.main_content),
                        "content_preview": result.main_content[:500],
                    },
                ).to_dict()
            else:
                return ActionResult(
                    success=False,
                    action="deep_read",
                    data={"url": url},
                    error=result.error or "Harvest failed"
                ).to_dict()

        except Exception as e:
            return ActionResult(
                success=False,
                action="deep_read",
                data={"url": url},
                error=str(e)
            ).to_dict()
    
    async def _action_research_paper(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """Vyhledávání akademických prací"""
        query = params.get("query", "")
        logger.info(f"Research paper search: {query}")
        
        try:
            # Použít Semantic Scholar nebo jiný akademický zdroj
            # TODO: Implementovat akademické vyhledávání
            
            return ActionResult(
                success=True,
                action="research_paper",
                data={"query": query, "papers": []},
            ).to_dict()
            
        except Exception as e:
            return ActionResult(
                success=False,
                action="research_paper",
                data={},
                error=str(e)
            ).to_dict()
    
    async def _action_archive_fallback(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """Wayback Machine fallback"""
        url = params.get("url", "")
        logger.info(f"Archive fallback: {url}")
        
        try:
            from hledac.universal.intelligence.archive_discovery import search_archives

            archive_result = await search_archives(url)

            if archive_result and archive_result.snapshots:
                latest = archive_result.snapshots[0]
                
                return ActionResult(
                    success=True,
                    action="archive_fallback",
                    data={
                        "original_url": url,
                        "archive_url": latest.archive_url,
                        "timestamp": latest.timestamp,
                    },
                ).to_dict()
            else:
                return ActionResult(
                    success=False,
                    action="archive_fallback",
                    data={"url": url},
                    error="No archive snapshots found"
                ).to_dict()
                
        except Exception as e:
            return ActionResult(
                success=False,
                action="archive_fallback",
                data={},
                error=str(e)
            ).to_dict()
    
    async def _action_fact_check(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """Ověření faktů"""
        claims = params.get("claims", [])
        logger.info(f"Fact checking {len(claims)} claims")
        
        try:
            from hledac.fact_checking import quick_fact_check
            
            results = []
            for claim in claims:
                result = await quick_fact_check(claim)
                results.append({
                    "claim": claim,
                    "verdict": result.verdict if hasattr(result, 'verdict') else "unknown",
                })
            
            return ActionResult(
                success=True,
                action="fact_check",
                data={"results": results},
            ).to_dict()
            
        except Exception as e:
            return ActionResult(
                success=False,
                action="fact_check",
                data={},
                error=str(e)
            ).to_dict()
    
    async def _action_stealth_harvest(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """
        Stealth harvestování.

        CONTEXT CARRIER COMPATIBILITY (Sprint F900A):
        - stealth_activated field exists only on ExecutionContext (types.py),
          NOT on ResearchContext (research_context.py)
        - Removed direct write to context.stealth_activated to prevent
          AttributeError when context is ResearchContext

        SPRINT F900G: DetectionEvader import path was stale/non-existent.
        This action is now a degraded/stub handler — it cannot perform
        real stealth harvest because the DetectionEvader class does not
        exist at hledac.advanced_web.detection_evader.
        Returns truthful stub result with stub=True metadata.
        """
        url = params.get("url", "")
        logger.warning(f"stealth_harvest is stub-only — DetectionEvader not available at hledac.advanced_web.detection_evader")
        return ActionResult(
            success=True,
            action="stealth_harvest",
            data={"url": url, "stub": True, "reason": "detection_evader_not_available"},
        ).to_dict()
    
    async def _action_osint_discovery(self, params: Dict[str, Any], context) -> Dict[str, Any]:
        """OSINT objevování skrytých zdrojů"""
        query = params.get("query", "")
        logger.info(f"OSINT discovery: {query}")
        
        try:
            from hledac.osint import HiddenSourcesCrawler
            
            crawler = HiddenSourcesCrawler()
            discovered = await crawler.discover_sources(query, max_sources=5)
            
            sources = [
                {
                    "url": d.url,
                    "title": d.title,
                    "source_type": d.source_type,
                }
                for d in discovered
            ]
            
            return ActionResult(
                success=True,
                action="osint_discovery",
                data={
                    "query": query,
                    "sources": sources,
                    "count": len(sources),
                },
            ).to_dict()
            
        except Exception as e:
            return ActionResult(
                success=False,
                action="osint_discovery",
                data={},
                error=str(e)
            ).to_dict()
    
    # Placeholder akce — Sprint F130F: stub metadata pro truthful distinguishability
    STUB_METADATA = {
        "stub": True,
        "degraded": True,
        "implemented": False,
        "contract": "noop_placeholder",
    }

    async def _action_scan(self, params, context):
        return ActionResult(success=True, action="scan", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_download(self, params, context):
        return ActionResult(success=True, action="download", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_smart_search(self, params, context):
        return ActionResult(success=True, action="smart_search", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_memorize(self, params, context):
        return ActionResult(success=True, action="memorize", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_probe(self, params, context):
        return ActionResult(success=True, action="probe", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_track(self, params, context):
        return ActionResult(success=True, action="track", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_deep_research(self, params, context):
        return ActionResult(success=True, action="deep_research", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_answer(self, params, context):
        return ActionResult(success=True, action="answer", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_crack(self, params, context):
        return ActionResult(success=True, action="crack", data=self.STUB_METADATA.copy()).to_dict()

    async def _action_error(self, params, context):
        """Error placeholder — used when an action fails to resolve."""
        error_msg = params.get("error", "Unknown error") if params else "Unknown error"
        return ActionResult(
            success=False,
            action="error",
            data={"error": error_msg},
        ).to_dict()

    # =====================================================================
    # CLEANUP
    # =====================================================================
    
    async def unload(self) -> None:
        """Uvolnit zdroje"""
        logger.info("Unloading GhostExecutor...")
        
        if self._network_driver:
            await self._network_driver.close()
            self._network_driver = None
        
        self._stealth_manager = None
        self._bloom_filter = None
        
        logger.info("✓ GhostExecutor unloaded")
    
    async def close(self) -> None:
        """Zavřít executor"""
        await self.unload()
