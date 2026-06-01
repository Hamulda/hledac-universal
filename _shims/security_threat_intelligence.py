"""
ThreatIntelligence — IOC lookup and threat analysis for OSINT findings.

Provides:
- IOC (Indicator of Compromise) lookup against local threat feeds
- Threat level assessment for analyzed entities
- Graceful degradation: returns empty results if no feeds available

Interface expected by security_coordinator.py:
- __init__(*args, **kwargs)
- async initialize()
- async analyze_threats(context, priority_level, security_level) -> dict
- async cleanup()
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Static IOC fallback — known malicious patterns
_STATIC_IOC_PATTERNS: dict[str, list[str]] = {
    "domain": [
        "malware-c2.net",
        "phishing-site.io",
        "suspicious-cdn.com",
    ],
    "ip": [
        "185.220.101.0/24",  # Known Tor exit nodes (example range)
        "192.0.2.0/24",      # TEST-NET-1, placeholder
    ],
    "hash": [
        # Example malware hashes (sha256)
    ],
    "url": [
        "http://malware-download.com/payload.exe",
        "https://phishing-site.io/steal",
    ],
}


class ThreatIntelligence:
    """
    Threat intelligence analysis for OSINT findings.

    Uses local IOC feeds for threat detection:
    - Loads from config/feeds/threat_feeds.json if available
    - Falls back to static IOC patterns
    - Returns typed empty results (not exceptions) for graceful degradation

    Attributes:
        _initialized: Whether async initialize() completed
        _iocs: Dict of loaded IOCs by type
        _feed_source: Source of loaded IOCs ("file", "static")
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize without parameters — async initialize() loads feeds."""
        self._initialized = False
        self._iocs: dict[str, set[str]] = {
            "domain": set(),
            "ip": set(),
            "hash": set(),
            "url": set(),
        }
        self._feed_source: str = "none"
        self._threat_count: int = 0

    async def initialize(self) -> None:
        """
        Load threat intelligence feeds.

        Attempts to load from:
        1. config/feeds/threat_feeds.json
        2. Falls back to static IOC patterns

        Logs WARNING if no feeds loaded (not error — graceful degradation).
        """
        # Try loading from config file
        feed_path = Path("config/feeds/threat_feeds.json")
        if not feed_path.is_absolute():
            feed_path = Path(__file__).parent.parent.parent / feed_path

        if feed_path.exists():
            try:
                import json
                with open(feed_path) as f:
                    data = json.load(f)
                for ioc_type, iocs in data.get("iocs", {}).items():
                    if ioc_type in self._iocs:
                        self._iocs[ioc_type] = set(iocs)
                self._feed_source = "file"
                logger.info(f"ThreatIntelligence: Loaded IOCs from {feed_path}")
            except Exception as e:
                logger.warning(f"ThreatIntelligence: Failed to load feeds: {e}")
                self._load_static_iocs()
        else:
            self._load_static_iocs()

        self._initialized = True

    def _load_static_iocs(self) -> None:
        """Load static IOC patterns as fallback."""
        for ioc_type, patterns in _STATIC_IOC_PATTERNS.items():
            self._iocs[ioc_type] = set(patterns)
        self._feed_source = "static"
        total = sum(len(v) for v in self._iocs.values())
        logger.warning(f"ThreatIntelligence: Using static IOCs ({total} patterns)")

    async def analyze_threats(
        self,
        context: dict[str, Any],
        priority_level: int = 5,
        security_level: int = 3,
    ) -> dict[str, Any]:
        """
        Analyze context for threat indicators.

        Args:
            context: Dict with keys like 'query', 'findings', 'entities'
            priority_level: 1-10 priority (higher = more urgent)
            security_level: 1-4 security level (higher = more thorough)

        Returns:
            dict with keys:
                - threats: list of detected threat dicts
                - threat_level: float 0.0-1.0
                - analyzed_count: int
                - ioc_matches: int
        """
        if not self._initialized:
            await self.initialize()

        threats: list[dict[str, Any]] = []
        analyzed = 0
        matches = 0

        # Extract entities from context
        entities = context.get("entities", [])
        if isinstance(entities, str):
            entities = [entities]

        # Analyze each entity
        for entity in entities:
            if not entity:
                continue
            analyzed += 1

            entity_str = str(entity).lower()
            ioc_type = self._classify_entity(entity_str)

            # Check if entity matches any IOC
            if self._check_ioc_match(entity_str, ioc_type):
                matches += 1
                threats.append({
                    "entity": entity,
                    "type": ioc_type,
                    "severity": "high",
                    "source": self._feed_source,
                    "description": f"Matched known {ioc_type} IOC pattern",
                })

        # Calculate threat level
        if analyzed == 0:
            threat_level = 0.0
        else:
            match_ratio = matches / analyzed
            # Scale by priority and security level
            threat_level = min(1.0, match_ratio * (priority_level / 5) * (security_level / 3))

        return {
            "threats": threats,
            "threat_level": threat_level,
            "analyzed_count": analyzed,
            "ioc_matches": matches,
            "feed_source": self._feed_source,
        }

    def _classify_entity(self, entity: str) -> str:
        """Classify entity type based on pattern."""
        # IP address
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", entity):
            return "ip"
        # URL
        if "://" in entity or entity.startswith("http"):
            return "url"
        # Hash (32+ hex chars)
        if re.match(r"^[a-f0-9]{32,}$", entity, re.IGNORECASE):
            return "hash"
        # Domain
        if "." in entity and not entity.startswith("http"):
            return "domain"
        return "unknown"

    def _check_ioc_match(self, entity: str, ioc_type: str) -> bool:
        """Check if entity matches any IOC of given type."""
        if ioc_type not in self._iocs:
            return False

        iocs = self._iocs[ioc_type]
        if not iocs:
            return False

        # Direct match
        if entity in iocs:
            return True

        # Subdomain match for domains
        if ioc_type == "domain":
            for ioc in iocs:
                if entity.endswith(ioc) or ioc in entity:
                    return True

        return False

    async def lookup_ioc(self, ioc: str) -> dict[str, Any]:
        """
        Direct IOC lookup.

        Args:
            ioc: Indicator to look up (domain, IP, hash, URL)

        Returns:
            dict with keys:
                - found: bool
                - type: str
                - severity: str or None
                - source: str
        """
        if not self._initialized:
            await self.initialize()

        ioc_str = str(ioc).lower()
        ioc_type = self._classify_entity(ioc_str)

        matched = self._check_ioc_match(ioc_str, ioc_type)

        return {
            "found": matched,
            "type": ioc_type,
            "severity": "high" if matched else None,
            "source": self._feed_source,
            "ioc": ioc,
        }

    async def cleanup(self) -> None:
        """Cleanup resources — no-op for local IOC lookup."""
        self._iocs = {k: set() for k in self._iocs}
        self._initialized = False
        logger.debug("ThreatIntelligence: Cleanup complete")


__all__ = ["ThreatIntelligence"]
