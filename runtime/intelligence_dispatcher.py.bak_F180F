"""
IntelligenceDispatcher — Lazy-load dispatcher seam pro intelligence moduly.

TICKET-006: runtime/intelligence_dispatcher.py jako nový bounded wiring seam

Scope:
- NENÍ runtime rewrite — existující __main__.py flow zůstává netknutý
- NENÍ controller — pouze adapter seam, žádný runtime ownership
- NENÍ orchestrator — žádné nové scheduler/manager frameworky

Entrypoints (tier-based):
  TIER1 (high value, low risk): ct_log_client, academic_search, stealth_crawler
  TIER2 (medium): network_recon, archive_discovery (později)

Garanty:
- Lazy import cache — žádné eager modulenačítání při init
- Fail-soft — broken/unknown modul vrací prázdný výsledek, ne exception
- Bounded — max 3 TIER1 modulů v první vlně
- No runtime cutover — pouze nový isolated seam, žádné přepojení existujících flow
"""

from __future__ import annotations

import asyncio
import logging
import time as time_
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import aiohttp
    from hledac.universal.runtime.memory_watchdog import MemoryWatchdog

logger = logging.getLogger(__name__)


# ============================================================================
# Tier enum
# ============================================================================

class IntelligenceTier(Enum):
    """Intelligence module priority tier."""
    TIER1 = auto()  # High value, low risk — ct_log, academic, stealth_crawler
    TIER2 = auto()  # Medium — network_recon, archive_discovery (later)
    TIER3 = auto()  # Future — všechny ostatní moduly


_TIER1_MODULES = frozenset({"ct_log", "academic_search", "stealth_crawler"})
_TIER2_MODULES = frozenset({"network_recon", "archive_discovery"})


# ============================================================================
# Result type
# ============================================================================

@dataclass
class IntelligenceResult:
    """Výsledek jednoho intelligence modulového volání.

    Frozen contract fields (F150F.2):
      module      — str: název modulu
      tier        — IntelligenceTier: tier příslušnost
      ok          — bool: True pokud modul doběhl bez chyby
      findings    — list[dict]: normalizované findings, min {source_type, content, confidence, provenance, ts}
      elapsed_ms  — int: kolik ms modul běžel
      error       — Optional[str]: chyba nebo None
    """
    module: str
    tier: IntelligenceTier
    ok: bool = False
    findings: list[dict] = field(default_factory=list)
    raw_result: Any = field(default=None)
    error: Optional[str] = None
    elapsed_ms: int = 0


@dataclass
class TieredIntelligenceResults:
    """Seskupené výsledky z více tierů (public API = list[dict])."""
    tier1: list[dict] = field(default_factory=list)
    tier2: list[dict] = field(default_factory=list)
    tier3: list[dict] = field(default_factory=list)
    total_latency_s: float = 0.0

    def all_results(self) -> list[dict]:
        return self.tier1 + self.tier2 + self.tier3


# ============================================================================
# Module registry (tier mapping)
# ============================================================================

def _tier_for_module(module_name: str) -> IntelligenceTier:
    if module_name in _TIER1_MODULES:
        return IntelligenceTier.TIER1
    if module_name in _TIER2_MODULES:
        return IntelligenceTier.TIER2
    return IntelligenceTier.TIER3


# ============================================================================
# Adapter: ct_log_client → standard dispatch signature
# ============================================================================

def _build_ct_log_adapter():
    """
    Adapter: CTLogClient.pivot_domain(domain) → dispatch(query, context).
    Query je očekáván jako domain string.
    """
    from pathlib import Path
    from hledac.universal.intelligence.ct_log_client import CTLogClient

    _client: Optional[CTLogClient] = None

    def _get_client() -> CTLogClient:
        nonlocal _client
        if _client is None:
            try:
                from hledac.universal.paths import CACHE_ROOT
                cache_dir = CACHE_ROOT / "ct_log"
            except Exception:
                cache_dir = Path("/tmp/hledac_ct_log")
            _client = CTLogClient(cache_dir)
        return _client

    async def _run(
        query: str,
        context: dict[str, Any],
        session: "Optional[aiohttp.ClientSession]" = None,
    ) -> tuple[str, Any]:
        """Returns (module_name, raw_result). query = domain string."""
        import aiohttp
        client = _get_client()
        _ctx = context  # noqa: F841 — reserved for future use
        if session is None:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as sess:
                result = await client.pivot_domain(query, sess)
        else:
            result = await client.pivot_domain(query, session)
        return ("ct_log", result)

    return _run, "ct_log"


# ============================================================================
# Adapter: academic_search → standard dispatch signature
# ============================================================================

def _build_academic_search_adapter():
    """
    Adapter: AcademicSearchEngine.search() → dispatch(query, context).
    """
    from hledac.universal.intelligence.academic_search import AcademicSearchEngine

    _engine: Optional[AcademicSearchEngine] = None

    def _get_engine() -> AcademicSearchEngine:
        nonlocal _engine
        if _engine is None:
            _engine = AcademicSearchEngine()
        return _engine

    async def _run(
        query: str,
        context: dict[str, Any],
        session: "Optional[aiohttp.ClientSession]" = None,
    ) -> tuple[str, Any]:
        """Returns (module_name, raw_result)."""
        _sess = session  # noqa: F841 — reserved for future use
        engine = _get_engine()
        max_results = context.get("max_results", 10) if context else 10
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: engine.search(query, max_results=max_results)
            )
        except TypeError:
            # Fallback: search() is async
            result = await engine.search(query, max_results=max_results)
        return ("academic_search", result)

    return _run, "academic_search"


# ============================================================================
# Adapter: stealth_crawler → standard dispatch signature
# ============================================================================

def _build_stealth_crawler_adapter():
    """
    Adapter: StealthCrawler (DuckDuckGo/Google search) → dispatch(query, context).

    Realizuje lightweight web search pres DuckDuckGo HTML.
    Pouziva _search_duckduckgo (interni metoda) přes run_in_executor.
    """
    from hledac.universal.intelligence.stealth_crawler import StealthCrawler

    _crawler: Optional[StealthCrawler] = None

    def _get_crawler() -> StealthCrawler:
        nonlocal _crawler
        if _crawler is None:
            _crawler = StealthCrawler()
        return _crawler

    async def _run(
        query: str,
        context: dict[str, Any],
        session: "Optional[aiohttp.ClientSession]" = None,
    ) -> tuple[str, Any]:
        """Returns (module_name, dict s 'results' seznamem)."""
        _sess = session  # noqa: F841 — reserved for future use
        crawler = _get_crawler()
        num_results = context.get("max_results", 10) if context else 10

        collected: list[dict] = []
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: crawler._search_duckduckgo(query, num_results),  # type: ignore[attr-defined]
            )
            for r in results:
                if hasattr(r, "__dataclass_fields__"):
                    import dataclasses
                    collected.append(dataclasses.asdict(r))
                elif isinstance(r, dict):
                    collected.append(r)
                else:
                    collected.append({"raw": str(r)})
        except Exception as e:
            logger.debug(f"[stealth_crawler] search error: {e}")

        return ("stealth_crawler", {"results": collected})

    return _run, "stealth_crawler"


# ============================================================================
# Lazy import cache
# ============================================================================

# Maps module_name → (run_fn, module_name)
_MODULE_ADAPTERS: dict[str, tuple[Any, str]] = {}


def _get_module_adapter(module_name: str) -> Optional[tuple[Any, str]]:
    """
    Vrací (run_fn, module_name) pro daný modul.
    Moduly jsou lazy-loaded při prvním volání.
    """
    if module_name in _MODULE_ADAPTERS:
        return _MODULE_ADAPTERS[module_name]

    if module_name == "ct_log":
        try:
            adapter_fn, mod_name = _build_ct_log_adapter()
            _MODULE_ADAPTERS[module_name] = (adapter_fn, mod_name)
            return _MODULE_ADAPTERS[module_name]
        except Exception as e:
            logger.warning(f"[dispatcher] ct_log adapter failed: {e}")
            return None

    if module_name == "academic_search":
        try:
            adapter_fn, mod_name = _build_academic_search_adapter()
            _MODULE_ADAPTERS[module_name] = (adapter_fn, mod_name)
            return _MODULE_ADAPTERS[module_name]
        except Exception as e:
            logger.warning(f"[dispatcher] academic_search adapter failed: {e}")
            return None

    if module_name == "stealth_crawler":
        try:
            adapter_fn, mod_name = _build_stealth_crawler_adapter()
            _MODULE_ADAPTERS[module_name] = (adapter_fn, mod_name)
            return _MODULE_ADAPTERS[module_name]
        except Exception as e:
            logger.warning(f"[dispatcher] stealth_crawler adapter failed: {e}")
            return None

    logger.debug(f"[dispatcher] unknown module: {module_name}")
    return None


# ── Result serialization ────────────────────────────────────────────────────

def _result_to_dict(result: IntelligenceResult) -> dict:
    """Convert IntelligenceResult → frozen contract dict."""
    return {
        "module": result.module,
        "tier": result.tier,
        "ok": result.ok,
        "findings": result.findings,
        "elapsed_ms": result.elapsed_ms,
        "error": result.error,
    }


# ============================================================================
# IntelligenceDispatcher
# ============================================================================

class IntelligenceDispatcher:
    """
    Lazy-load dispatcher pro intelligence moduly.

    NENÍ controller — pouze adapter seam.
    NENÍ orchestrator — žádné runtime ownership.
    Žádný runtime cutover v tomto sprintu.

    Garanty:
    - __init__ nenačítá žádné moduly eager
    - Lazy load nastává až při run_tier() / dispatch()
    - Fail-soft pro broken/unknown moduly
    - Žádné nové public API pro moduly mimo whitelist

    Memory pressure integration (TICKET-007):
    - _memory_watchdog: Optional[MemoryWatchdog] — attached seam
    - _suspended_tiers: set[str] — tiers suspended due to memory pressure
    - run_tier() checks suspension before execution
    """

    __slots__ = ("_session", "_max_tier1_latency_s", "_memory_watchdog", "_suspended_tiers")

    def __init__(
        self,
        session: Optional["aiohttp.ClientSession"] = None,
        max_tier1_latency_s: float = 30.0,
    ) -> None:
        self._session = session
        self._max_tier1_latency_s = max_tier1_latency_s
        self._memory_watchdog: Optional["MemoryWatchdog"] = None
        self._suspended_tiers: set[str] = set()

    # ── Tier-based dispatch ────────────────────────────────────────────────

    async def run_tier1(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> list[dict]:
        """Spustí všechny TIER1 moduly paralelně. Fail-soft."""
        return await self.run_tier(IntelligenceTier.TIER1, query, context)

    async def run_tier(
        self,
        tier: IntelligenceTier,
        query: str,
        context: Optional[dict[str, Any]] = None,
        timeout_s: Optional[float] = None,
    ) -> list[dict]:
        """
        Spustí všechny moduly daného tieru paralelně. Fail-soft.

        Frozen contract returns list[dict] with fields:
          module, tier, ok, findings, elapsed_ms, error
        """
        ctx = context or {}
        effective_timeout = timeout_s if timeout_s is not None else self._max_tier1_latency_s

        # TICKET-007: check tier suspension before execution
        tier_name = tier.name
        if tier_name in self._suspended_tiers:
            logger.debug(f"[dispatcher] tier {tier_name} suspended, skipping")
            return []

        if tier == IntelligenceTier.TIER1:
            module_names = list(_TIER1_MODULES)
        elif tier == IntelligenceTier.TIER2:
            module_names = list(_TIER2_MODULES)
        else:
            module_names = []

        if not module_names:
            return []

        tasks = [self._run_single(mod, query, ctx) for mod in module_names]

        results: list[IntelligenceResult] = []
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=effective_timeout,
            )
            for item in gathered:
                if isinstance(item, Exception):
                    logger.debug(f"[dispatcher] task exception: {item}")
                elif isinstance(item, IntelligenceResult):
                    results.append(item)
        except asyncio.TimeoutError:
            logger.debug(
                f"[dispatcher] tier {tier.name} timeout "
                f"after {effective_timeout}s"
            )
        except Exception as e:
            logger.debug(f"[dispatcher] tier {tier.name} error: {e}")

        # Return list[dict] — frozen contract public API
        return [_result_to_dict(r) for r in results]

    async def run_tiered(
        self,
        query: str,
        context: Optional[dict[str, Any]] = None,
        max_tier: IntelligenceTier = IntelligenceTier.TIER1,
    ) -> TieredIntelligenceResults:
        """Spustí všechny tiery od TIER1 po max_tier sekvenčně."""
        results = TieredIntelligenceResults()
        ctx = context or {}

        if int(max_tier.value) >= int(IntelligenceTier.TIER1.value):
            t1_results = await self.run_tier1(query, ctx)
            results.tier1 = t1_results

        if int(max_tier.value) >= int(IntelligenceTier.TIER2.value):
            t2_results = await self.run_tier(IntelligenceTier.TIER2, query, ctx)
            results.tier2 = t2_results

        results.total_latency_s = sum(r["elapsed_ms"] for r in results.all_results()) / 1000.0
        return results

    # ── Single module dispatch ─────────────────────────────────────────────

    async def dispatch(
        self,
        module_name: str,
        query: str,
        context: Optional[dict[str, Any]] = None,
    ) -> IntelligenceResult:
        """Spustí jeden modul podle jména. Fail-soft."""
        return await self._run_single(module_name, query, context or {})

    async def _run_single(
        self,
        module_name: str,
        query: str,
        context: dict[str, Any],
    ) -> IntelligenceResult:
        """Interní: spustí jeden modul s fail-soft wrapperem."""
        tier = _tier_for_module(module_name)
        result = IntelligenceResult(module=module_name, tier=tier)

        start = time_.monotonic()
        try:
            adapter = _get_module_adapter(module_name)
            if adapter is None:
                result.error = f"unknown_module:{module_name}"
                result.elapsed_ms = int((time_.monotonic() - start) * 1000)
                return result

            run_fn, _ = adapter
            _, raw = await run_fn(query, context, self._session)
            result.raw_result = raw
            result.elapsed_ms = int((time_.monotonic() - start) * 1000)
            result.findings = self._normalize_findings(module_name, raw)
            result.ok = True

        except asyncio.TimeoutError:
            result.error = "timeout"
            result.elapsed_ms = int((time_.monotonic() - start) * 1000)
        except Exception as e:
            result.error = f"{type(e).__name__}:{e}"
            result.elapsed_ms = int((time_.monotonic() - start) * 1000)
            logger.debug(f"[dispatcher] {module_name} error: {e}")

        return result

    # ── Finding normalization (lightweight, no NER) ───────────────────────────

    def _normalize_findings(self, module_name: str, raw_result: Any) -> list[dict]:
        """
        Převede raw_result na seznam normalizovaných findings.
        Min normalized finding: {source_type, content, confidence, provenance, ts}
        """
        if not raw_result:
            return []

        findings: list[dict] = []
        ts = int(time_.time() * 1000)

        if module_name == "ct_log" and isinstance(raw_result, dict):
            san_names = raw_result.get("san_names", [])
            domain = raw_result.get("domain", "")
            content = "; ".join([domain] + [n for n in san_names if n and n != domain])
            if content:
                findings.append({
                    "source_type": "ct_log",
                    "content": content,
                    "confidence": 0.7,
                    "provenance": "crt.sh",
                    "ts": ts,
                })

        elif module_name == "academic_search" and isinstance(raw_result, dict):
            results = raw_result.get("results", [])
            for r in results:
                if isinstance(r, dict):
                    doi = r.get("doi")
                    url = r.get("url")
                    title = r.get("title", "")
                    if doi:
                        findings.append({
                            "source_type": "academic",
                            "content": doi,
                            "confidence": 0.8,
                            "provenance": url or "academic_search",
                            "ts": ts,
                        })
                    if url:
                        findings.append({
                            "source_type": "academic",
                            "content": url,
                            "confidence": 0.6,
                            "provenance": "academic_search",
                            "ts": ts,
                        })

        elif module_name == "stealth_crawler" and isinstance(raw_result, dict):
            results = raw_result.get("results", [])
            for r in results:
                url = r.get("url") if isinstance(r, dict) else None
                title = r.get("title", "") if isinstance(r, dict) else ""
                if url:
                    findings.append({
                        "source_type": "web",
                        "content": title or url,
                        "confidence": 0.5,
                        "provenance": url,
                        "ts": ts,
                    })

        return findings


# ============================================================================
# Top-level convenience
# ============================================================================

async def dispatch_intelligence(
    query: str,
    context: Optional[dict[str, Any]] = None,
    tier: IntelligenceTier = IntelligenceTier.TIER1,
    session: Optional["aiohttp.ClientSession"] = None,
) -> TieredIntelligenceResults:
    """
    One-shot intelligence dispatch.

    Lazy-loaduje pouze požadované moduly.
    Fail-soft — broken modul nezastaví ostatní.

    Example:
        results = await dispatch_intelligence(
            "example.com",
            {"max_results": 10},
            IntelligenceTier.TIER1,
        )
        for r in results.tier1:
            print(f"{r.module}: {len(r.findings)} findings in {r.elapsed_ms}ms")
    """
    dispatcher = IntelligenceDispatcher(session=session)
    return await dispatcher.run_tiered(query, context, tier)


# ============================================================================
# Module availability helpers (no eager imports)
# ============================================================================

def is_module_available(module_name: str) -> bool:
    """Check zda je modul dostupný (lazy). Vrací True/False bez exception."""
    if module_name in _MODULE_ADAPTERS:
        return True
    try:
        result = _get_module_adapter(module_name)
        return result is not None
    except Exception:
        return False


def list_tier1_modules() -> list[str]:
    """Vrátí seznam TIER1 modulů."""
    return list(_TIER1_MODULES)


def list_tier2_modules() -> list[str]:
    """Vrátí seznam TIER2 modulů."""
    return list(_TIER2_MODULES)
