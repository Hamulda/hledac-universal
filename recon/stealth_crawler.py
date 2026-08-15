"""
Stealth Crawler - Web Intelligence (Facade)
============================================

Tento modul je nyní facade re-exportující z `recon.stealth` package.
Zachovává backwards compatibility pro existující importy.

Nová struktura (ISSUE-028):
- recon/stealth/_models.py — Dataclass/Enum modely
- recon/stealth/scraper.py — StealthCrawler, StealthWebScraper
- recon/stealth/monitor.py — StreamingMonitor

Pro nový kód preferujte importy z recon.stealth přímo:
    from hledac.universal.recon.stealth import StealthCrawler, StreamingMonitor
"""
from __future__ import annotations

# Re-export everything from the new stealth package for backwards compatibility
from hledac.universal.recon.stealth import (
from core import aclose
    # Models
    Alert,
    AlertRule,
    BypassMethod,
    Change,
    ChangeType,
    FingerprintProfile,
    HeaderConfig,
    HeaderSpoofer,
    MonitoredSource,
    ProtectionType,
    ProxyConfig,
    ScrapingResult,
    SearchResult,
    Severity,
    SourceType,
    StreamEvent,
    TorProxyManager,
    get_stealth_headers,
    # Scraper
    StealthCrawler,
    StealthWebScraper,
    create_stealth_crawler,
    get_stealth_web_scraper,
    quick_scrape,
    # Monitor
    StreamingMonitor,
)

# backwards-compatible module-level state (used by some internal code)
_PATCHED_SURFACES: set[str] = set()
_UNPATCHED_SURFACES: set[str] = set()

__all__ = [
    # Models
    "Alert",
    "AlertRule",
    "BypassMethod",
    "Change",
    "ChangeType",
    "FingerprintProfile",
    "HeaderConfig",
    "HeaderSpoofer",
    "MonitoredSource",
    "ProtectionType",
    "ProxyConfig",
    "ScrapingResult",
    "SearchResult",
    "Severity",
    "SourceType",
    "StreamEvent",
    "TorProxyManager",
    "get_stealth_headers",
    # Scraper
    "StealthCrawler",
    "StealthWebScraper",
    "create_stealth_crawler",
    "get_stealth_web_scraper",
    "quick_scrape",
    # Monitor
    "StreamingMonitor",
]
