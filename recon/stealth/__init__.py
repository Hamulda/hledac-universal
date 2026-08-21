"""
Stealth package — web intelligence a stealth crawler.

Rozděleno z původního stealth_crawler.py (ISSUE-028).

Moduly:
- _models.py: Dataclass/Enum modely, helper funkce
- scraper.py: StealthCrawler, StealthWebScraper
- monitor.py: StreamingMonitor

Pro backwards compatibility lze importovat přímo z recon.stealth:
    from hledac.universal.recon.stealth import StealthCrawler, StealthWebScraper, StreamingMonitor
"""

from __future__ import annotations

# Re-export all public symbols for backwards compatibility
from ._models import (
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
)
from .monitor import StreamingMonitor
from .scraper import (
    StealthCrawler,
    StealthWebScraper,
    create_stealth_crawler,
    get_stealth_web_scraper,
    quick_scrape,
)

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
