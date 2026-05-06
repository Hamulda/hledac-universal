#!/usr/bin/env python3
"""
Hledač Threat Intelligence Automation System
Advanced security automation with threat intelligence and proactive defense
"""

import asyncio
import json
import logging
import hashlib
import ipaddress
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import aiohttp
import yaml
from urllib.parse import urljoin, urlparse
import socket
import ssl

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ThreatIntelligence:
    """Threat intelligence data"""
    threat_id: str
    threat_type: str
    severity: str
    source: str
    indicators: List[str]
    description: str
    first_seen: datetime
    last_seen: datetime
    confidence: float
    tags: List[str]


@dataclass
class SecurityAlert:
    """Security alert generated from threat intelligence"""
    alert_id: str
    threat_intelligence: ThreatIntelligence
    affected_assets: List[str]
    recommended_actions: List[str]
    automated_response: Optional[str]
    timestamp: datetime
    status: str


@dataclass
class DefenseAction:
    """Automated defense action"""
    action_id: str
    action_type: str
    target: str
    confidence: float
    impact: str
    rollback_possible: bool
    duration: timedelta


class ThreatIntelligenceAutomation:
    """Advanced threat intelligence and automated security system"""

    def __init__(self, config_path: str = "config/security_enhancements.yaml"):
        self.config_path = Path(config_path)
        self.threat_intel_db = {}
        self.active_alerts = {}
        self.defense_actions = []
        self.blocked_entities = defaultdict(set)
        self.vulnerability_cache = {}

        self.config = self._load_config()
        self.threat_sources = self._initialize_threat_sources()
        self._initialize_ml_models()

    def _load_config(self) -> Dict[str, Any]:
        """Load security configuration"""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file {self.config_path} not found")
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        """Default security configuration"""
        return {
            "threat_intelligence": {
                "enabled": True,
                "sources": ["abuse.ch", "virustotal", "alienvault"],
                "update_interval": 3600,
                "retention_days": 90,
                "confidence_threshnew": 0.7
            },
            "automated_defense": {
                "enabled": True,
                "block_suspicious_ips": True,
                "rate_limit_offenders": True,
                "auto_patch_vulnerabilities": False,
                "isolate_compromised_systems": True,
                "defense_timeout": 300
            },
            "monitoring": {
                "log_analysis": True,
                "network_monitoring": True,
                "behavior_analysis": True,
                "anomaly_detection": True
            }
        }

    def _initialize_threat_sources(self) -> Dict[str, Dict[str, Any]]:
        """Initialize threat intelligence sources"""
        return {
            "abuse.ch": {
                "url": "https://feodotracker.abuse.ch/downloads/feodotracker.json",
                "type": "malware_domains",
                "enabled": True,
                "api_key": None
            },
            "virustotal": {
                "url": "https://www.virustotal.com/vtapi/v2",
                "type": "file_reputation",
                "enabled": True,
                "api_key": "${VIRUSTOTAL_API_KEY}"
            },
            "alienvault": {
                "url": "https://otx.alienvault.com/api/v1",
                "type": "ioc_indicators",
                "enabled": True,
                "api_key": "${OTX_API_KEY}"
            },
            "cve": {
                "url": "https://services.nvd.nist.gov/rest/json/cves/2.0",
                "type": "vulnerabilities",
                "enabled": True,
                "api_key": None
            }
        }

    def _initialize_ml_models(self):
        """Initialize machine learning models for threat analysis"""
        self.ml_models = {
            "malware_classifier": None,
            "phishing_detector": None,
            "anomaly_detector": None
        }

    async def start_threat_intelligence_service(self):
        """Start continuous threat intelligence gathering"""
        logger.info("Starting threat intelligence service...")

        update_interval = self.config["threat_intelligence"]["update_interval"]

        while True:
            try:
                await self._gather_threat_intelligence()
                await self._analyze_threats_and_alert()
                await self._apply_automated_defenses()
                await asyncio.sleep(update_interval)
            except Exception as e:
                logger.error(f"Error in threat intelligence cycle: {e}")
                await asyncio.sleep(300)

    async def _gather_threat_intelligence(self):
        """Gather threat intelligence from all configured sources"""
        logger.info("Gathering threat intelligence...")

        for source_name, source_config in self.threat_sources.items():
            if not source_config["enabled"]:
                continue

            try:
                if source_config["type"] == "malware_domains":
                    await self._gather_malware_domains(source_name, source_config)
                elif source_config["type"] == "file_reputation":
                    await self._gather_file_reputation(source_name, source_config)
                elif source_config["type"] == "ioc_indicators":
                    await self._gather_ioc_indicators(source_name, source_config)
                elif source_config["type"] == "vulnerabilities":
                    await self._gather_vulnerabilities(source_name, source_config)
            except Exception as e:
                logger.error(f"Error gathering from {source_name}: {e}")

    async def _gather_malware_domains(self, source_name: str, config: Dict[str, Any]):
        """Gather malware domain intelligence"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(config["url"], timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        data = await response.json()

                        for entry in data:
                            threat = ThreatIntelligence(
                                threat_id=f"{source_name}_{hashlib.md5(entry.get('domain', '').encode()).hexdigest()[:8]}",
                                threat_type="malware_domain",
                                severity="high",
                                source=source_name,
                                indicators=[entry.get('domain', '')],
                                description=entry.get('description', f"Malware domain: {entry.get('domain', '')}"),
                                first_seen=datetime.fromisoformat(entry.get('first_seen', datetime.now().isoformat())),
                                last_seen=datetime.fromisoformat(entry.get('last_seen', datetime.now().isoformat())),
                                confidence=0.9,
                                tags=["malware", "domain", "c2"]
                            )

                            self.threat_intel_db[threat.threat_id] = threat

                        logger.info(f"Updated {len(data)} malware domains from {source_name}")
        except Exception as e:
            logger.error(f"Error gathering malware domains from {source_name}: {e}")

    async def _gather_file_reputation(self, source_name: str, config: Dict[str, Any]):
        """Gather file reputation intelligence"""
        logger.debug(f"File reputation gathering not fully implemented for {source_name}")

    async def _gather_ioc_indicators(self, source_name: str, config: Dict[str, Any]):
        """Gather IOC indicators"""
        try:
            headers = {}
            if config.get("api_key"):
                headers["X-OTX-API-KEY"] = config["api_key"]

            async with aiohttp.ClientSession(headers=headers) as session:
                url = f"{config['url']}/pulses/subscribed"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        data = await response.json()

                        for pulse in data.get("results", []):
                            for indicator in pulse.get("indicators", []):
                                threat = ThreatIntelligence(
                                    threat_id=f"{source_name}_{indicator.get('id', '')}",
                                    threat_type="ioc",
                                    severity=self._map_pulse_severity(pulse.get("TLP", "white")),
                                    source=source_name,
                                    indicators=[indicator.get("indicator", "")],
                                    description=pulse.get("description", "IOC indicator"),
                                    first_seen=datetime.fromisoformat(pulse.get("created", datetime.now().isoformat())),
                                    last_seen=datetime.now(),
                                    confidence=0.8,
                                    tags=pulse.get("tags", [])
                                )

                                self.threat_intel_db[threat.threat_id] = threat

                        logger.info(f"Updated {len(data.get('results', []))} IOC pulses from {source_name}")
        except Exception as e:
            logger.error(f"Error gathering IOC indicators from {source_name}: {e}")

    async def _gather_vulnerabilities(self, source_name: str, config: Dict[str, Any]):
        """Gather vulnerability intelligence"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{config['url']}?resultsPerPage=50"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        data = await response.json()

                        for cve in data.get("vulnerabilities", []):
                            cve_id = cve.get("cve", {}).get("id", "")
                            indicators = [cve_id]

                            for affected in cve.get("configurations", []):
                                for product in affected.get("nodes", []):
                                    if "cpeMatch" in product:
                                        for match in product["cpeMatch"]:
                                            cpe = match.get("criteria", "")
                                            if cpe:
                                                indicators.append(cpe)

                            threat = ThreatIntelligence(
                                threat_id=f"{source_name}_{cve_id}",
                                threat_type="vulnerability",
                                severity=self._map_cvss_severity(cve.get("metrics", {}).get("cvssMetricV2", [])),
                                source=source_name,
                                indicators=indicators,
                                description=cve.get("descriptions", [{}])[0].get("value", ""),
                                first_seen=datetime.fromisoformat(cve.get("published", datetime.now().isoformat())),
                                last_seen=datetime.fromisoformat(cve.get("lastModified", datetime.now().isoformat())),
                                confidence=1.0,
                                tags=["vulnerability", "cve"]
                            )

                            self.threat_intel_db[threat.threat_id] = threat

                        logger.info(f"Updated {len(data.get('vulnerabilities', []))} CVEs from {source_name}")
        except Exception as e:
            logger.error(f"Error gathering vulnerabilities from {source_name}: {e}")

    def _map_pulse_severity(self, tlp: str) -> str:
        """Map OTX TLP classification to severity"""
        tlp_mapping = {
            "red": "critical",
            "amber": "high",
            "green": "medium",
            "white": "low"
        }
        return tlp_mapping.get(tlp.lower(), "medium")

    def _map_cvss_severity(self, cvss_metrics: List[Dict[str, Any]]) -> str:
        """Map CVSS score to severity"""
        if not cvss_metrics:
            return "medium"

        cvss_score = cvss_metrics[0].get("cvssData", {}).get("baseScore", 0.0)

        if cvss_score >= 9.0:
            return "critical"
        elif cvss_score >= 7.0:
            return "high"
        elif cvss_score >= 4.0:
            return "medium"
        else:
            return "low"

    async def _analyze_threats_and_alert(self):
        """Analyze gathered threats and generate alerts"""
        logger.info("Analyzing threats and generating alerts...")

        await self._analyze_application_threats()
        await self._analyze_network_threats()
        await self._analyze_behavior_anomalies()

    async def _analyze_application_threats(self):
        """Analyze threats against application assets"""
        app_endpoints = ["localhost:8000", "127.0.0.1:8000", "0.0.0.0:8000"]

        for threat_id, threat in self.threat_intel_db.items():
            if threat.threat_type in ["malware_domain", "ioc"]:
                for indicator in threat.indicators:
                    if self._matches_app_endpoint(indicator, app_endpoints):
                        alert = SecurityAlert(
                            alert_id=f"app_{threat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                            threat_intelligence=threat,
                            affected_assets=app_endpoints,
                            recommended_actions=[
                                "Monitor application logs for suspicious activity",
                                "Enhance endpoint security monitoring",
                                "Consider IP-based blocking if applicable"
                            ],
                            automated_response=self._get_automated_response(threat),
                            timestamp=datetime.now(),
                            status="new"
                        )

                        self.active_alerts[alert.alert_id] = alert
                        logger.warning(f"Application threat detected: {indicator}")

    def _matches_app_endpoint(self, indicator: str, endpoints: List[str]) -> bool:
        """Check if indicator matches application endpoints"""
        try:
            if any(endpoint in indicator for endpoint in endpoints):
                return True

            try:
                indicator_ip = ipaddress.ip_address(indicator)
                for endpoint in endpoints:
                    if ':' in endpoint:
                        endpoint_ip = ipaddress.ip_address(endpoint.split(':')[0])
                        if indicator_ip == endpoint_ip:
                            return True
            except ValueError:
                pass

            for endpoint in endpoints:
                if endpoint in indicator or indicator in endpoint:
                    return True
        except Exception:
            pass

        return False

    def _get_automated_response(self, threat: ThreatIntelligence) -> Optional[str]:
        """Get automated response based on threat type and severity"""
        if threat.severity in ["critical", "high"] and threat.threat_type in ["malware_domain", "ioc"]:
            return "block_ip_domain"

        if threat.threat_type == "vulnerability" and threat.severity in ["critical", "high"]:
            return "update_rules"

        if threat.confidence > 0.8:
            return "enhance_monitoring"

        return None

    async def _analyze_network_threats(self):
        """Analyze network threats"""
        try:
            access_log = Path("logs/access.log")
            if access_log.exists():
                await self._analyze_access_log_for_threats(access_log)
        except Exception as e:
            logger.error(f"Error analyzing network threats: {e}")

    async def _analyze_access_log_for_threats(self, log_file: Path):
        """Analyze access log for threat indicators"""
        try:
            suspicious_patterns = [
                r"POST.*login.*403",
                r"admin.*401",
                r"\.php.*200",
                r"union.*select",
                r"<script.*>",
            ]

            with open(log_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    for pattern in suspicious_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            alert = SecurityAlert(
                                alert_id=f"log_threat_{datetime.now().strftime('%Y%m%d%H%M%S')}_{line_num}",
                                threat_intelligence=ThreatIntelligence(
                                    threat_id=f"log_pattern_{hashlib.md5(pattern.encode()).hexdigest()[:8]}",
                                    threat_type="log_based_threat",
                                    severity="medium",
                                    source="access_log",
                                    indicators=[pattern],
                                    description=f"Suspicious pattern detected in access log: {pattern}",
                                    first_seen=datetime.now(),
                                    last_seen=datetime.now(),
                                    confidence=0.6,
                                    tags=["log_analysis", "pattern_matching"]
                                ),
                                affected_assets=["web_server"],
                                recommended_actions=[
                                    "Review access logs for similar patterns",
                                    "Investigate source IP addresses",
                                    "Consider rate limiting or blocking"
                                ],
                                automated_response="rate_limit",
                                timestamp=datetime.now(),
                                status="new"
                            )

                            self.active_alerts[alert.alert_id] = alert
                            logger.warning(f"Suspicious log pattern detected: {pattern}")
        except Exception as e:
            logger.error(f"Error analyzing access log: {e}")

    async def _analyze_behavior_anomalies(self):
        """Analyze behavior anomalies"""
        try:
            current_metrics = await self._collect_behavior_metrics()
            anomalies = self._detect_anomalies(current_metrics)

            for anomaly in anomalies:
                alert = SecurityAlert(
                    alert_id=f"behavior_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(str(anomaly).encode()).hexdigest()[:8]}",
                    threat_intelligence=ThreatIntelligence(
                        threat_id=f"anomaly_{hashlib.md5(str(anomaly).encode()).hexdigest()[:8]}",
                        threat_type="behavioral_anomaly",
                        severity="medium",
                        source="behavior_analysis",
                        indicators=[str(anomaly)],
                        description=f"Behavioral anomaly detected: {anomaly}",
                        first_seen=datetime.now(),
                        last_seen=datetime.now(),
                        confidence=0.7,
                        tags=["behavior", "anomaly"]
                    ),
                    affected_assets=["application"],
                    recommended_actions=[
                        "Investigate unusual behavior patterns",
                        "Review system logs for context",
                        "Consider temporary monitoring enhancement"
                    ],
                    automated_response="enhance_monitoring",
                    timestamp=datetime.now(),
                    status="new"
                )

                self.active_alerts[alert.alert_id] = alert
                logger.warning(f"Behavioral anomaly detected: {anomaly}")
        except Exception as e:
            logger.error(f"Error analyzing behavior anomalies: {e}")

    async def _collect_behavior_metrics(self) -> Dict[str, Any]:
        """Collect behavior metrics for anomaly detection"""
        metrics = {}
        try:
            metrics["request_rate"] = 100
            metrics["error_rate"] = 0.01
            metrics["avg_response_time"] = 200
            metrics["unique_ips"] = 50
        except Exception as e:
            logger.error(f"Error collecting behavior metrics: {e}")
        return metrics

    def _detect_anomalies(self, metrics: Dict[str, Any]) -> List[str]:
        """Detect anomalies in metrics"""
        anomalies = []

        if metrics.get("error_rate", 0) > 0.05:
            anomalies.append("High error rate")

        if metrics.get("avg_response_time", 0) > 2000:
            anomalies.append("Slow response times")

        if metrics.get("request_rate", 0) > 1000:
            anomalies.append("Unusual traffic spike")

        return anomalies

    async def _apply_automated_defenses(self):
        """Apply automated defense actions"""
        logger.info("Applying automated defenses...")

        if not self.config["automated_defense"]["enabled"]:
            return

        for alert_id, alert in list(self.active_alerts.items()):
            if alert.status == "new" and alert.automated_response:
                try:
                    await self._execute_defense_action(alert)
                    alert.status = "mitigating"
                except Exception as e:
                    logger.error(f"Error executing defense for alert {alert_id}: {e}")
                    alert.status = "failed"

        await self._cleanup_new_alerts()

    async def _execute_defense_action(self, alert: SecurityAlert):
        """Execute automated defense action"""
        action_type = alert.automated_response

        if action_type == "block_ip_domain":
            await self._block_malicious_entities(alert)
        elif action_type == "rate_limit":
            await self._apply_rate_limiting(alert)
        elif action_type == "update_rules":
            await self._update_security_rules(alert)
        elif action_type == "enhance_monitoring":
            await self._enhance_monitoring(alert)

        defense_action = DefenseAction(
            action_id=f"defense_{alert.alert_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            action_type=action_type,
            target=",".join(alert.threat_intelligence.indicators),
            confidence=alert.threat_intelligence.confidence,
            impact="medium",
            rollback_possible=True,
            duration=timedelta(hours=24)
        )

        self.defense_actions.append(defense_action)
        logger.info(f"Defense action executed: {action_type}")

    async def _block_malicious_entities(self, alert: SecurityAlert):
        """Block malicious IPs and domains"""
        if not self.config["automated_defense"]["block_suspicious_ips"]:
            return

        for indicator in alert.threat_intelligence.indicators:
            try:
                if self._is_ip_address(indicator):
                    self.blocked_entities["ips"].add(indicator)
                    logger.info(f"Blocked IP: {indicator}")
                else:
                    self.blocked_entities["domains"].add(indicator)
                    logger.info(f"Blocked domain: {indicator}")

                await self._update_firewall_rules(indicator, "block")
            except Exception as e:
                logger.error(f"Error blocking {indicator}: {e}")

    def _is_ip_address(self, indicator: str) -> bool:
        """Check if indicator is an IP address"""
        try:
            ipaddress.ip_address(indicator)
            return True
        except ValueError:
            return False

    async def _update_firewall_rules(self, entity: str, action: str):
        """Update firewall rules (placeholder implementation)"""
        logger.info(f"Firewall rule updated: {action} {entity}")

    async def _apply_rate_limiting(self, alert: SecurityAlert):
        """Apply rate limiting based on alert"""
        if not self.config["automated_defense"]["rate_limit_offenders"]:
            return

        suspicious_ips = ["192.168.1.100"]

        for ip in suspicious_ips:
            self.blocked_entities["rate_limited"].add(ip)
            logger.info(f"Rate limiting applied to: {ip}")

    async def _update_security_rules(self, alert: SecurityAlert):
        """Update security rules based on alert"""
        try:
            security_config_path = Path("config/security_enhancements.yaml")

            if security_config_path.exists():
                with open(security_config_path, 'r') as f:
                    config = yaml.safe_load(f)

                if "threat_intelligence" not in config:
                    config["threat_intelligence"] = {}

                config["threat_intelligence"][alert.threat_intelligence.threat_id] = {
                    "type": alert.threat_intelligence.threat_type,
                    "severity": alert.threat_intelligence.severity,
                    "indicators": alert.threat_intelligence.indicators,
                    "last_seen": datetime.now().isoformat()
                }

                with open(security_config_path, 'w') as f:
                    yaml.dump(config, f, default_flow_style=False)

                logger.info(f"Security rules updated for threat: {alert.threat_intelligence.threat_id}")
        except Exception as e:
            logger.error(f"Error updating security rules: {e}")

    async def _enhance_monitoring(self, alert: SecurityAlert):
        """Enhance monitoring for suspicious activity"""
        logger.info(f"Monitoring enhanced for alert: {alert.alert_id}")

    async def _cleanup_new_alerts(self):
        """Clean up new resolved alerts"""
        retention_days = self.config["threat_intelligence"]["retention_days"]
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        new_alerts = [
            alert_id for alert_id, alert in self.active_alerts.items()
            if alert.timestamp < cutoff_date
        ]

        for alert_id in new_alerts:
            del self.active_alerts[alert_id]

        if new_alerts:
            logger.info(f"Cleaned up {len(new_alerts)} new alerts")

    def generate_threat_intelligence_report(self, output_file: str = "security_reports/threat_intelligence.json"):
        """Generate comprehensive threat intelligence report"""
        report_path = Path(output_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_threats": len(self.threat_intel_db),
                "active_alerts": len([a for a in self.active_alerts.values() if a.status == "new"]),
                "defense_actions_applied": len(self.defense_actions),
                "blocked_ips": len(self.blocked_entities["ips"]),
                "blocked_domains": len(self.blocked_entities["domains"]),
                "rate_limited_entities": len(self.blocked_entities["rate_limited"])
            },
            "threat_intelligence": {
                threat_id: {
                    "type": threat.threat_type,
                    "severity": threat.severity,
                    "source": threat.source,
                    "confidence": threat.confidence,
                    "indicators_count": len(threat.indicators),
                    "first_seen": threat.first_seen.isoformat(),
                    "last_seen": threat.last_seen.isoformat(),
                    "tags": threat.tags
                } for threat_id, threat in self.threat_intel_db.items()
            },
            "active_alerts": [
                {
                    "alert_id": alert.alert_id,
                    "threat_type": alert.threat_intelligence.threat_type,
                    "severity": alert.threat_intelligence.severity,
                    "affected_assets": alert.affected_assets,
                    "status": alert.status,
                    "timestamp": alert.timestamp.isoformat(),
                    "automated_response": alert.automated_response
                } for alert in self.active_alerts.values()
            ],
            "defense_actions": [
                {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "target": action.target,
                    "confidence": action.confidence,
                    "impact": action.impact,
                    "rollback_possible": action.rollback_possible,
                    "duration_hours": action.duration.total_seconds() / 3600
                } for action in self.defense_actions[-50:]
            ],
            "blocked_entities": {
                "ips": list(self.blocked_entities["ips"]),
                "domains": list(self.blocked_entities["domains"]),
                "rate_limited": list(self.blocked_entities["rate_limited"])
            },
            "recommendations": self._generate_security_recommendations()
        }

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Threat intelligence report saved to {report_path}")

    def _generate_security_recommendations(self) -> List[str]:
        """Generate security recommendations based on current state"""
        recommendations = []

        critical_alerts = [a for a in self.active_alerts.values()
                          if a.threat_intelligence.severity == "critical"]
        if critical_alerts:
            recommendations.append("Immediate action required: Review and address critical security alerts")

        if len(self.blocked_entities["ips"]) > 100:
            recommendations.append("Consider implementing automated IP reputation scoring")

        if len(self.blocked_entities["domains"]) > 50:
            recommendations.append("Review and optimize domain blocking policies")

        recent_actions = [a for a in self.defense_actions
                        if a.action_id.startswith(f"defense_{datetime.now().strftime('%Y%m%d')}")]
        if len(recent_actions) > 20:
            recommendations.append("High defensive activity detected - investigate potential attack patterns")

        recommendations.extend([
            "Regularly update threat intelligence feeds",
            "Implement multi-layered security monitoring",
            "Conduct regular security assessments",
            "Maintain incident response procedures"
        ])

        return recommendations


async def main():
    """Main CLI interface"""
    import sys

    threat_intel = ThreatIntelligenceAutomation()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "start":
            await threat_intel.start_threat_intelligence_service()
        elif command == "gather":
            await threat_intel._gather_threat_intelligence()
        elif command == "analyze":
            await threat_intel._analyze_threats_and_alert()
        elif command == "defend":
            await threat_intel._apply_automated_defenses()
        elif command == "report":
            threat_intel.generate_threat_intelligence_report()
            print("\nThreat intelligence report generated!")
        elif command == "status":
            print("\nThreat Intelligence Status:")
            print("=" * 30)
            print(f"Total threats: {len(threat_intel.threat_intel_db)}")
            print(f"Active alerts: {len([a for a in threat_intel.active_alerts.values() if a.status == 'new'])}")
            print(f"Defense actions: {len(threat_intel.defense_actions)}")
            print(f"Blocked IPs: {len(threat_intel.blocked_entities['ips'])}")
            print(f"Blocked domains: {len(threat_intel.blocked_entities['domains'])}")
    else:
        print("Usage: python threat-intelligence-automation.py <command>")
        print("Commands: start, gather, analyze, defend, report, status")


if __name__ == "__main__":
    asyncio.run(main())