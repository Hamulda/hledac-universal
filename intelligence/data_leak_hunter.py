"""
Data Leak Hunter - Breach and Data Leak Monitoring System
==========================================================

Integrated from stealth_osint/data_leak_hunter.py:
- Breach API integration (HaveIBeenPwned, DeHashed, Intelligence X)
- Dark web monitoring (Tor/I2P breach forums)
- Paste site surveillance (Pastebin, Ghostbin)
- Real-time alerts via WebSocket/email
- Temporal anonymization for stealth

M1 8GB Optimized: Streaming processing, minimal memory footprint
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# Security imports
try:
    from hledac.security.temporal_anonymizer import TemporalAnonymizer
    from hledac.security.zero_attribution_engine import ZeroAttributionEngine
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class AlertSeverity(Enum):
    """Severity levels for data leak alerts"""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LeakSource(Enum):
    """Sources of data leaks"""
    BREACH_API = "breach_api"
    DARK_WEB = "dark_web"
    PASTE_SITE = "paste_site"
    PUBLIC_RECORDS = "public_records"
    HACKER_FORUM = "hacker_forum"


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class LeakAlert:
    """Data leak alert"""
    alert_id: str
    timestamp: datetime
    target: str
    target_type: str  # email, username, domain, ip, hash
    source: LeakSource
    severity: AlertSeverity
    breach_name: Optional[str]
    leaked_data: Dict[str, Any]
    raw_sample: Optional[str] = None  # Sanitized sample
    url: Optional[str] = None


@dataclass
class MonitoringTarget:
    """Target to monitor for leaks"""
    target_id: str
    value: str
    target_type: str  # email, username, domain, ip, hash, phone
    description: Optional[str]
    added_at: datetime
    last_check: Optional[datetime] = None
    alert_count: int = 0


@dataclass
class BreachAPIConfig:
    """Configuration for breach APIs"""
    haveibeenpwned_api_key: Optional[str] = None
    dehashed_api_key: Optional[str] = None
    intelligencex_api_key: Optional[str] = None
    leaklookup_api_key: Optional[str] = None


# =============================================================================
# MAIN CLASS
# =============================================================================

class DataLeakHunter:
    """
    Advanced data leak monitoring system.
    
    Features:
    - Continuous monitoring of breach databases
    - Dark web forum scraping (Tor/I2P)
    - Paste site surveillance
    - Real-time alerting
    - Temporal anonymization for stealth operations
    
    Integrated from stealth_osint for universal orchestrator.
    
    Example:
        hunter = DataLeakHunter()
        await hunter.initialize()
        
        # Add monitoring target
        await hunter.add_target("user@example.com", "email")
        
        # Start continuous monitoring
        await hunter.start_monitoring()
        
        # Or single check
        alerts = await hunter.check_target("user@example.com")
    """
    
    # Major breach API sources
    BREACH_APIS = {
        "haveibeenpwned": {
            "url": "https://haveibeenpwned.com/api/v3/breachedaccount/",
            "rate_limit": 6,  # seconds between requests
            "headers": {"User-Agent": "Hledac-DataLeakHunter"},
        },
        "leaklookup": {
            "url": "https://leak-lookup.com/api/search",
            "rate_limit": 1,
        },
        "dehashed": {
            "url": "https://api.dehashed.com/search",
            "rate_limit": 2,
        },
        "intelligencex": {
            "url": "https://public.intelligencex.com/api/",
            "rate_limit": 10,
        },
    }
    
    # Paste sites to monitor
    PASTE_SITES = [
        "https://pastebin.com/raw/",
        "https://ghostbin.co/",
        "https://0bin.net/",
        "https://privatebin.net/",
    ]
    
    # Common breach forum indicators (on clearnet indexes)
    FORUM_INDICATORS = [
        "breachforums",
        "raidforums",
        "cracked.io",
        "nulled.to",
    ]
    
    def __init__(
        self,
        api_config: Optional[BreachAPIConfig] = None,
        check_interval: int = 3600,  # 1 hour
        alert_handlers: Optional[List[callable]] = None
    ):
        """
        Initialize DataLeakHunter.
        
        Args:
            api_config: API keys for breach databases
            check_interval: Seconds between checks
            alert_handlers: Callbacks for alerts
        """
        self.api_config = api_config or BreachAPIConfig()
        self.check_interval = check_interval
        self.alert_handlers = alert_handlers or []
        
        # Security components
        self._anonymizer = None
        self._zero_attribution = None
        
        # Monitoring state
        self._targets: Dict[str, MonitoringTarget] = {}
        self._is_monitoring = False
        self._monitoring_task: Optional[asyncio.Task] = None
        
        # Alert storage
        self._alerts: List[LeakAlert] = []
        self._recent_alerts: Set[str] = set()  # Deduplication
        
        # Performance metrics
        self._checks_performed = 0
        self._alerts_generated = 0
        self._api_calls = 0
        
        # HTTP session
        self._session = None
        
        logger.info("DataLeakHunter initialized")
    
    async def initialize(self) -> bool:
        """Initialize security components and HTTP session"""
        if not AIOHTTP_AVAILABLE:
            logger.error("aiohttp not available")
            return False
        
        try:
            # Initialize security components
            if SECURITY_AVAILABLE:
                try:
                    self._anonymizer = TemporalAnonymizer()
                    self._zero_attribution = ZeroAttributionEngine()
                except Exception as e:
                    logger.warning(f"Security components not available: {e}")
            
            # Create HTTP session with stealth headers
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30)
            )
            
            logger.info("✅ DataLeakHunter initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            return False
    
    async def add_target(
        self,
        value: str,
        target_type: str,
        description: Optional[str] = None
    ) -> str:
        """
        Add a target to monitor.
        
        Args:
            value: Target value (email, username, etc.)
            target_type: Type of target (email, username, domain, ip, hash)
            description: Optional description
            
        Returns:
            Target ID
        """
        target_id = hashlib.sha256(f"{value}:{target_type}".encode()).hexdigest()[:16]
        
        target = MonitoringTarget(
            target_id=target_id,
            value=value,
            target_type=target_type,
            description=description,
            added_at=datetime.now()
        )
        
        self._targets[target_id] = target
        logger.info(f"🎯 Added monitoring target: {value} ({target_type})")
        return target_id
    
    async def remove_target(self, target_id: str) -> bool:
        """Remove a monitoring target"""
        if target_id in self._targets:
            del self._targets[target_id]
            logger.info(f"🗑️ Removed target: {target_id}")
            return True
        return False
    
    async def start_monitoring(self) -> None:
        """Start continuous monitoring loop"""
        if self._is_monitoring:
            logger.warning("Monitoring already active")
            return
        
        self._is_monitoring = True
        self._monitoring_task = asyncio.create_task(
            self._monitoring_loop(),
            name="data_leak_monitoring"
        )
        logger.info(f"▶️ Started monitoring ({self.check_interval}s interval)")
    
    async def stop_monitoring(self) -> None:
        """Stop continuous monitoring"""
        self._is_monitoring = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("⏹️ Stopped monitoring")
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop"""
        while self._is_monitoring:
            try:
                logger.debug("Running periodic leak check...")
                
                # Check all targets
                for target in self._targets.values():
                    alerts = await self.check_target(target.value, target.target_type)
                    
                    for alert in alerts:
                        await self._process_alert(alert)
                    
                    target.last_check = datetime.now()
                
                self._checks_performed += len(self._targets)
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
                await asyncio.sleep(60)  # Wait 1 min on error
    
    async def check_target(
        self,
        value: str,
        target_type: str
    ) -> List[LeakAlert]:
        """
        Perform single check for a target.
        
        Args:
            value: Target value
            target_type: Type of target
            
        Returns:
            List of LeakAlerts
        """
        alerts = []
        
        # Apply temporal anonymization
        if self._anonymizer:
            await asyncio.sleep(self._anonymizer.get_random_delay())
        
        # Check breach APIs
        api_alerts = await self._check_breach_apis(value, target_type)
        alerts.extend(api_alerts)
        
        # Check paste sites (for emails and usernames)
        if target_type in ("email", "username"):
            paste_alerts = await self._check_paste_sites(value, target_type)
            alerts.extend(paste_alerts)
        
        return alerts
    
    async def _check_breach_apis(
        self,
        value: str,
        target_type: str
    ) -> List[LeakAlert]:
        """Check breach APIs for target"""
        alerts = []

        # HaveIBeenPwned check (for emails)
        if target_type == "email" and self.api_config.haveibeenpwned_api_key:
            try:
                hibp_alerts = await self._check_haveibeenpwned(value)
                alerts.extend(hibp_alerts)
            except Exception as e:
                logger.debug(f"HIBP check failed: {e}")

        # LeakLookup check
        if self.api_config.leaklookup_api_key:
            try:
                lookup_alerts = await self._check_leaklookup(value, target_type)
                alerts.extend(lookup_alerts)
            except Exception as e:
                logger.debug(f"LeakLookup check failed: {e}")

        # Dehashed check
        if self.api_config.dehashed_api_key:
            try:
                dehashed_alerts = await self._check_dehashed(value, target_type)
                alerts.extend(dehashed_alerts)
            except Exception as e:
                logger.debug(f"Dehashed check failed: {e}")

        # IntelligenceX check
        if self.api_config.intelligencex_api_key:
            try:
                ix_alerts = await self._check_intelligencex(value, target_type)
                alerts.extend(ix_alerts)
            except Exception as e:
                logger.debug(f"IntelligenceX check failed: {e}")

        return alerts
    
    async def _check_haveibeenpwned(self, email: str) -> List[LeakAlert]:
        """Check HaveIBeenPwned API"""
        alerts = []
        
        config = self.BREACH_APIS["haveibeenpwned"]
        url = f"{config['url']}{email}"
        headers = {
            **config["headers"],
            "hibp-api-key": self.api_config.haveibeenpwned_api_key
        }
        
        try:
            async with self._session.get(url, headers=headers) as resp:
                self._api_calls += 1
                
                if resp.status == 200:
                    breaches = await resp.json()
                    
                    for breach in breaches:
                        alert = LeakAlert(
                            alert_id=str(uuid.uuid4()),
                            timestamp=datetime.now(),
                            target=email,
                            target_type="email",
                            source=LeakSource.BREACH_API,
                            severity=AlertSeverity.HIGH,
                            breach_name=breach.get("Name"),
                            leaked_data={
                                "title": breach.get("Title"),
                                "date": breach.get("BreachDate"),
                                "compromised_data": breach.get("DataClasses", []),
                                "description": breach.get("Description", "")[:200],
                            },
                            url=breach.get("Domain")
                        )
                        alerts.append(alert)
                        
                elif resp.status == 404:
                    # No breaches found
                    pass
                else:
                    logger.warning(f"HIBP API error: {resp.status}")
                    
            # Rate limiting
            await asyncio.sleep(config["rate_limit"])
            
        except Exception as e:
            logger.debug(f"HIBP request failed: {e}")
        
        return alerts
    
    async def _check_leaklookup(
        self,
        value: str,
        target_type: str
    ) -> List[LeakAlert]:
        """Check LeakLookup API"""
        alerts = []
        
        config = self.BREACH_APIS["leaklookup"]
        
        # Map target type to LeakLookup type
        type_mapping = {
            "email": "email",
            "username": "username",
            "domain": "domain",
            "ip": "ip",
            "hash": "hash",
        }
        
        lookup_type = type_mapping.get(target_type, "email")
        
        try:
            payload = {
                "key": self.api_config.leaklookup_api_key,
                "type": lookup_type,
                "query": value,
            }
            
            async with self._session.post(config["url"], data=payload) as resp:
                self._api_calls += 1
                
                if resp.status == 200:
                    data = await resp.json()
                    
                    if data.get("found"):
                        for source in data.get("sources", []):
                            alert = LeakAlert(
                                alert_id=str(uuid.uuid4()),
                                timestamp=datetime.now(),
                                target=value,
                                target_type=target_type,
                                source=LeakSource.BREACH_API,
                                severity=AlertSeverity.HIGH,
                                breach_name=source,
                                leaked_data={
                                    "database": source,
                                    "total_results": data.get("count", 0),
                                }
                            )
                            alerts.append(alert)
                            
            await asyncio.sleep(config["rate_limit"])

        except Exception as e:
            logger.debug(f"LeakLookup request failed: {e}")

        return alerts

    async def _check_dehashed(self, value: str, target_type: str) -> List[LeakAlert]:
        """Check Dehashed API for leaks."""
        alerts = []

        if not self.api_config.dehashed_api_key:
            return alerts

        config = self.BREACH_APIS["dehashed"]
        headers = {
            "Authorization": f"Bearer {self.api_config.dehashed_api_key}",
            "Accept": "application/json",
        }
        params = {"query": value, "type": target_type}

        try:
            async with self._session.get(config["url"], headers=headers, params=params) as resp:
                self._api_calls += 1

                if resp.status == 200:
                    data = await resp.json()
                    for entry in data.get("matches", []):
                        alert = LeakAlert(
                            alert_id=str(uuid.uuid4()),
                            timestamp=datetime.now(),
                            target=value,
                            target_type=target_type,
                            source=LeakSource.BREACH_API,
                            severity=AlertSeverity.HIGH,
                            breach_name=entry.get("source", "Dehashed"),
                            leaked_data={
                                "email": entry.get("email"),
                                "username": entry.get("username"),
                                "password": entry.get("password"),
                                "hash": entry.get("hash"),
                                "database": entry.get("database"),
                            },
                            raw_sample=entry.get("password", "")[:50] if entry.get("password") else None,
                        )
                        alerts.append(alert)
                elif resp.status == 429:
                    logger.warning("Dehashed API rate limited")
                    await asyncio.sleep(config["rate_limit"] * 5)
                else:
                    logger.debug(f"Dehashed API: {resp.status}")

            await asyncio.sleep(config["rate_limit"])

        except Exception as e:
            logger.debug(f"Dehashed request failed: {e}")

        return alerts

    async def _check_intelligencex(self, value: str, target_type: str) -> List[LeakAlert]:
        """Check IntelligenceX API for leaks."""
        alerts = []

        if not self.api_config.intelligencex_api_key:
            return alerts

        config = self.BREACH_APIS["intelligencex"]
        headers = {
            "Authorization": f"Bearer {self.api_config.intelligencex_api_key}",
            "Content-Type": "application/json",
        }

        type_map = {
            "email": "email",
            "domain": "domain",
            "username": "username",
            "ip": "ip",
            "hash": "hash",
        }
        ix_type = type_map.get(target_type, "email")

        payload = {
            "searchtype": ix_type,
            "searchquery": value,
            "limit": 20,
        }

        try:
            async with self._session.post(
                config["url"],
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                self._api_calls += 1

                if resp.status == 200:
                    data = await resp.json()
                    for result in data.get("results", []):
                        alert = LeakAlert(
                            alert_id=str(uuid.uuid4()),
                            timestamp=datetime.now(),
                            target=value,
                            target_type=target_type,
                            source=LeakSource.BREACH_API,
                            severity=AlertSeverity.HIGH,
                            breach_name=result.get("source", "IntelligenceX"),
                            leaked_data={
                                "url": result.get("url"),
                                "category": result.get("category"),
                                "date": result.get("date"),
                            },
                            url=result.get("url"),
                        )
                        alerts.append(alert)
                elif resp.status == 429:
                    logger.warning("IntelligenceX API rate limited")
                    await asyncio.sleep(config["rate_limit"] * 6)
                else:
                    logger.debug(f"IntelligenceX API: {resp.status}")

            await asyncio.sleep(config["rate_limit"])

        except Exception as e:
            logger.debug(f"IntelligenceX request failed: {e}")

        return alerts

    async def _check_paste_sites(
        self,
        value: str,
        target_type: str
    ) -> List[LeakAlert]:
        """Check paste sites for leaked data"""
        alerts = []
        
        # Search paste search engines
        search_engines = [
            f"https://psbdmp.ws/api/v3/search/{value}",  # Pastebin dump search
        ]
        
        for engine_url in search_engines:
            try:
                async with self._session.get(engine_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        for result in data.get("data", []):
                            # Check if it contains the target
                            if value.lower() in result.get("text", "").lower():
                                alert = LeakAlert(
                                    alert_id=str(uuid.uuid4()),
                                    timestamp=datetime.now(),
                                    target=value,
                                    target_type=target_type,
                                    source=LeakSource.PASTE_SITE,
                                    severity=AlertSeverity.MEDIUM,
                                    breach_name=f"Paste: {result.get('title', 'Unknown')}",
                                    leaked_data={
                                        "paste_id": result.get("id"),
                                        "tags": result.get("tags", []),
                                        "length": result.get("length", 0),
                                    },
                                    url=f"https://pastebin.com/raw/{result.get('id')}"
                                )
                                alerts.append(alert)
                                
            except Exception as e:
                logger.debug(f"Paste site check failed: {e}")
        
        return alerts
    
    async def _process_alert(self, alert: LeakAlert) -> None:
        """Process and dispatch alert"""
        # Deduplication
        alert_hash = hashlib.sha256(
            f"{alert.target}:{alert.breach_name}:{alert.source.value}".encode()
        ).hexdigest()[:16]
        
        if alert_hash in self._recent_alerts:
            return
        
        self._recent_alerts.add(alert_hash)
        self._alerts.append(alert)
        self._alerts_generated += 1
        
        # Update target stats
        for target in self._targets.values():
            if target.value == alert.target:
                target.alert_count += 1
        
        # Log alert
        logger.warning(
            f"🚨 LEAK ALERT [{alert.severity.value.upper()}]: "
            f"{alert.target} found in {alert.breach_name} "
            f"({alert.source.value})"
        )
        
        # Call alert handlers
        for handler in self.alert_handlers:
            try:
                await handler(alert)
            except Exception as e:
                logger.error(f"Alert handler error: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get hunter statistics"""
        return {
            "targets_monitored": len(self._targets),
            "checks_performed": self._checks_performed,
            "alerts_generated": self._alerts_generated,
            "api_calls_made": self._api_calls,
            "is_monitoring": self._is_monitoring,
            "recent_alerts": len(self._recent_alerts),
        }
    
    def get_alerts(
        self,
        target: Optional[str] = None,
        severity: Optional[AlertSeverity] = None,
        limit: int = 100
    ) -> List[LeakAlert]:
        """Get alerts with optional filtering"""
        alerts = self._alerts
        
        if target:
            alerts = [a for a in alerts if a.target == target]
        
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        return sorted(alerts, key=lambda x: x.timestamp, reverse=True)[:limit]
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        await self.stop_monitoring()
        
        if self._session:
            await self._session.close()
        
        logger.info("DataLeakHunter cleanup complete")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def check_email_breaches(
    email: str,
    hibp_api_key: Optional[str] = None
) -> List[LeakAlert]:
    """
    Quick check for email breaches.
    
    Example:
        alerts = await check_email_breaches("user@example.com", "api_key")
        for alert in alerts:
            print(f"Found in: {alert.breach_name}")
    """
    config = BreachAPIConfig(haveibeenpwned_api_key=hibp_api_key)
    hunter = DataLeakHunter(api_config=config)
    
    if await hunter.initialize():
        return await hunter.check_target(email, "email")
    
    return []


# Global instance
_data_leak_hunter: Optional[DataLeakHunter] = None


def get_data_leak_hunter() -> DataLeakHunter:
    """Get or create global DataLeakHunter instance"""
    global _data_leak_hunter
    if _data_leak_hunter is None:
        _data_leak_hunter = DataLeakHunter()
    return _data_leak_hunter


# =============================================================================
# FÁZE P5: API Key Loading from KeyManager
# =============================================================================


async def load_api_keys_from_keymanager(
    bucket_id: str = "osint_apis",
) -> BreachAPIConfig:
    """
    Load breach API keys from KeyManager.

    Reads API keys from LMDB keystore using KeyManager.get_bucket_key().
    Falls back to environment variables if KeyManager unavailable.

    Args:
        bucket_id: KeyManager bucket for OSINT API keys

    Returns:
        BreachAPIConfig with loaded keys
    """
    config = BreachAPIConfig()

    try:
        from hledac.security.key_manager import KeyManager
        km = KeyManager()

        key, _ = await km.get_bucket_key(bucket_id)

        import base64
        key_str = base64.b64encode(key).decode()

        config.haveibeenpwned_api_key = key_str[:40] if len(key_str) >= 40 else key_str
        config.dehashed_api_key = key_str[40:80] if len(key_str) >= 80 else None
        config.intelligencex_api_key = key_str[80:120] if len(key_str) >= 120 else None

    except Exception as e:
        logger.debug(f"KeyManager API key load: {e}")

        import os
        config.haveibeenpwned_api_key = os.environ.get("HIBP_API_KEY")
        config.dehashed_api_key = os.environ.get("DEHASHED_API_KEY")
        config.intelligencex_api_key = os.environ.get("INTELLIGENCEX_API_KEY")
        config.leaklookup_api_key = os.environ.get("LEAKLOOKUP_API_KEY")

    return config


# =============================================================================
# PasteMonitorClient — Sprint 8UB: Pastebin recent pastes monitoring
# =============================================================================

class PasteMonitorClient:
    """Scraping Pastebin scraping API + Archive.org text/plain pastes.
    Zadarmo, 1 req/min Pastebin scraping API. Primární médium pro
    credential dumps, malware configs, leaked DB excerpts."""

    _SCRAPE_URL = "https://scrape.pastebin.com/api_scraping.php"
    _RATE_S = 61.0  # Pastebin: strict 1/min pro public endpoint
    _CACHE_TTL = 300  # 5min — pastes jsou nové

    def __init__(self, cache_dir: str | Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def get_recent_pastes(
        self,
        session: aiohttp.ClientSession,
        limit: int = 20,
    ) -> list[dict]:
        """Vrátí [{key, date, title, size, syntax, user}]"""
        import orjson

        zst_path = self._cache_dir / "paste_recent.json.zst"
        json_path = self._cache_dir / "paste_recent.json"
        if zst_path.exists() and (time.time() - zst_path.stat().st_mtime < self._CACHE_TTL):
            try:
                import compression.zstd as _zstd
                return orjson.loads(_zstd.decompress(zst_path.read_bytes()))
            except (ImportError, Exception):
                pass
        if json_path.exists() and (time.time() - json_path.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(json_path.read_bytes())

        await self._throttle()
        try:
            async with session.get(
                f"{self._SCRAPE_URL}?limit={limit}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status in (403, 401):
                    logger.debug("Pastebin scraping API: IP not whitelisted (expected in dev)")
                    return []
                r.raise_for_status()
                data = await r.json(content_type=None)
        except Exception as e:
            logger.debug(f"PasteMonitor fetch: {e}")
            return []

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        import orjson
        try:
            import compression.zstd as _zstd
            zst_path.write_bytes(_zstd.compress(orjson.dumps(data or [])))
        except (ImportError, Exception):
            json_path.write_bytes(orjson.dumps(data or []))
        return data or []

    async def fetch_paste_content(
        self,
        paste_key: str,
        session: aiohttp.ClientSession,
    ) -> str:
        """Stáhnout raw obsah pasty."""
        try:
            async with session.get(
                f"https://pastebin.com/raw/{paste_key}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return ""
                return await r.text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()

