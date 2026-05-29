"""
F203C: Kill Chain Tagger — MITRE ATT&CK Mapping for OSINT Findings

Maps raw OSINT findings to MITRE ATT&CK tactics and techniques:
  - Reconnaissance (TA0043): T1590-T1598 — target reconnaissance
  - Resource Development (TA0042): T1583-T1588 — capability development

Deterministic: no model, no network, pure Python.
Bounded: MAX_TAGS_PER_FINDING=5, MAX_TAGGED_FINDINGS=1000.

M1 safe: pure Python, no model load, no JS renderer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

__all__ = [
    "KillChainTag",
    "KillChainTagger",
    "create_kill_chain_tagger",
    "ioc_to_technique_ids",
]

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_TAGS_PER_FINDING: int = 5
MAX_TAGGED_FINDINGS: int = 1000

# ATT&CK tactic + technique registry
# Each entry: (tactic, technique_id, technique_name, phase, confidence, patterns)
# phase: reconnaissance | resource_development | initial_access | ... (kill chain phase)
_ATTACK_PATTERNS: list[tuple[str, str, str, str, float, list[re.Pattern[str]]]] = []

# ── Helpers ────────────────────────────────────────────────────────────────────


def _compile(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.IGNORECASE)


def _add_pattern(
    tactic: str,
    technique_id: str,
    technique_name: str,
    phase: str,
    confidence: float,
    *patterns: str,
) -> None:
    compiled = [_compile(p) for p in patterns]
    _ATTACK_PATTERNS.append((tactic, technique_id, technique_name, phase, confidence, compiled))


# ── Reconnaissance Patterns (TA0043 / T1590-T1598) ────────────────────────────

# T1590 — Gather Victim Network Information
_add_pattern(
    "Reconnaissance",
    "T1590",
    "Gather Victim Network Information",
    "reconnaissance",
    0.50,
    r"dns record",
    r"nameserver",
    r"mx record",
    r"a record",
    r"aaaa record",
    r"ptr record",
    r"txt record",
    r"soa record",
    r"dns lookup",
    r"reverse dns",
    r"zone transfer",
    r"axfr",
    r"dns enumeration",
)

# T1590.001 — DNS Records
_add_pattern(
    "Reconnaissance",
    "T1590.001",
    "DNS WHOIS/Registration Data",
    "reconnaissance",
    0.70,
    r"whois",
    r"domain registration",
    r"registrant",
    r"registration date",
    r"name server",
    r"domain expiry",
    r"registrar",
    r"admin contact",
    r" registrant ",
)

# T1590.002 — Subdomain Enumeration
_add_pattern(
    "Reconnaissance",
    "T1590.002",
    "Subdomain Enumeration",
    "reconnaissance",
    0.65,
    r"subdomain",
    r"sub domain",
    r"subdomain enumeration",
    r"dns bruteforce",
    r"dns scan",
)

# T1590.003 — Network Boundaries
_add_pattern(
    "Reconnaissance",
    "T1590.003",
    "Network Boundary Mapping",
    "reconnaissance",
    0.45,
    r"ip range",
    r"cidr",
    r"network boundary",
    r"asn",
    r"bgp",
)

# T1590.004 — SSL Certificate Patterns
_add_pattern(
    "Reconnaissance",
    "T1590.004",
    "SSL/TLS Certificate Intelligence",
    "reconnaissance",
    0.75,
    r"certificate transparency",
    r"certspotter",
    r"crt\.sh",
    r"sslyze",
    r"ssl certificate",
    r"tls cert",
    r"san.*certificate",
    r"subject alternative name",
    r"certificate fingerprint",
    r"sha-?256.*cert",
    r"cert.*sha-?256",
)

# T1590.005 — Passive DNS
_add_pattern(
    "Reconnaissance",
    "T1590.005",
    "Passive DNS Records",
    "reconnaissance",
    0.70,
    r"passive dns",
    r"dns history",
    r"historical dns",
    r"dnsdb",
    r"forward dns",
    r"reverse dns record",
)

# T1591 — Domain Properties
_add_pattern(
    "Reconnaissance",
    "T1591",
    "Domain Properties Discovery",
    "reconnaissance",
    0.60,
    r"domain name",
    r"domainalexpiration",
    r"domain age",
    r"domain created",
    r"domain updated",
)

# T1592 — Vulnerable/Interesting Web Services
_add_pattern(
    "Reconnaissance",
    "T1592",
    "Vulnerable Web Services",
    "reconnaissance",
    0.55,
    r"web server",
    r"http server",
    r"nginx",
    r"apache",
    r"iis",
    r"lighttpd",
    r"caddy",
    r"tomcat",
    r"jetty",
    r"open port",
    r"http banner",
    r"http title",
)

# T1593 — Search Open Websites/Databases
_add_pattern(
    "Reconnaissance",
    "T1593",
    "Search Open Websites/Databases",
    "reconnaissance",
    0.60,
    r"search engine",
    r"google dork",
    r"shodan",
    r"censys",
    r"zoomeye",
    r"fofa",
    r"hunter",
    r"securitytrails",
    r"builtwith",
    r"wappalyzer",
    r"similarweb",
    r"alexa rank",
)

# T1594 — Threat Intelligence Platforms
_add_pattern(
    "Reconnaissance",
    "T1594",
    "Threat Intelligence Platform Lookup",
    "reconnaissance",
    0.65,
    r"threatintel",
    r"threat intel",
    r"alienvault",
    r"otx",
    r"pastebin",
    r"abuseipdb",
    r"ipvoid",
    r"urlvoid",
    r"virustotal",
    r"hybrid-analysis",
    r"threatfox",
    r"malware bazaar",
)

# T1595 — Active Scanning — CVE/WORDPRESS/SSH/VPN
_add_pattern(
    "Reconnaissance",
    "T1595",
    "Active Scanning: Vulnerability Scanning",
    "reconnaissance",
    0.60,
    r"cve-",
    r"vulnerability scan",
    r"cve scanning",
    r"vulnerability intelligence",
    r"exploit db",
    r"edb-",
)

_add_pattern(
    "Reconnaissance",
    "T1595.001",
    "Active Scanning: WordPress Scanning",
    "reconnaissance",
    0.55,
    r"wpscan",
    r"wordpress",
    r"wp-content",
    r"wp-admin",
    r"wordpress version",
    r"wp-plugin",
)

_add_pattern(
    "Reconnaissance",
    "T1595.002",
    "Active Scanning: SSH Scanning",
    "reconnaissance",
    0.55,
    r"ssh-",
    r"openssh",
    r"ssh version",
    r"ssh banner",
    r"ssh scan",
    r"port 22",
)

_add_pattern(
    "Reconnaissance",
    "T1595.003",
    "Active Scanning: VPN Scanning",
    "reconnaissance",
    0.55,
    r"openvpn",
    r"ike",
    r"ipsec",
    r"vpn scan",
    r"port 500",
    r"port 4500",
    r"ike-scan",
)

# T1596 — Credentials/Content from Repositories
_add_pattern(
    "Reconnaissance",
    "T1596",
    "Search Public Repositories / Leaked Credentials",
    "reconnaissance",
    0.80,
    r"github.*token",
    r"gitlab.*token",
    r"aws.*key",
    r"api.key",
    r"apikey",
    r"secret.*key",
    r"password",
    r"credential",
    r"leak",
    r"breach",
    r"pwned",
    r"have i been pwned",
    r"leaked",
    r"pastebin",
    r"gist",
    r"commit.*secret",
    r".git/config",
    r".env.*password",
    r"id_rsa",
    r"id_ed25519",
    r"oauth.*token",
    r"bearer.*token",
    r"private.*key",
)

# T1597 — Supply Chain/Compromise
_add_pattern(
    "Reconnaissance",
    "T1597",
    "Compromise Supply Chain",
    "reconnaissance",
    0.70,
    r"supply chain",
    r"npm package",
    r"pypi package",
    r"rubygems",
    r"nuget",
    r"dependency confusion",
    r"typosquatting",
    r"brand impersonation",
    r"package仿冒",
)

# T1598 — Phishing for Information
_add_pattern(
    "Reconnaissance",
    "T1598",
    "Phishing for Information",
    "reconnaissance",
    0.70,
    r"spear phishing",
    r"phishing",
    r"email spoofing",
    r"typosquatting.*domain",
    r"lookalike domain",
    r"brand impersonation.*email",
    r"login page",
    r"credential harvesting",
)

# ── Resource Development Patterns (TA0042 / T1583-T1588) ───────────────────────

# T1583 — Acquire Infrastructure
_add_pattern(
    "Resource Development",
    "T1583",
    "Acquire Infrastructure",
    "resource_development",
    0.60,
    r"vps",
    r"virtual private server",
    r"dedicated server",
    r"cloud instance",
    r"aws.*instance",
    r"azure.*vm",
    r"gcp.*instance",
    r"digitalocean",
    r"linode",
    r"vultr",
    r"ransomware.*infrastructure",
    r"bulletproof host",
    r"rogue dns",
)

# T1583.001 — DNS Server
_add_pattern(
    "Resource Development",
    "T1583.001",
    "Acquisition: DNS Server",
    "resource_development",
    0.65,
    r"dns server",
    r"authoritative dns",
    r"recursive dns",
    r"private dns",
    r"dns tunneling",
    r"dnscat",
    r"iodine.*dns",
)

# T1583.002 — Acquire Web Services
_add_pattern(
    "Resource Development",
    "T1583.002",
    "Acquisition: Web Services",
    "resource_development",
    0.65,
    r"tor.*relay",
    r"tor bridge",
    r"onion service",
    r"dark web host",
    r"free host",
    r"file hosting",
    r"paste service",
    r"transfer.sh",
    r"0x0\.sh",
)

# T1583.003 — Acquire VPN Services
_add_pattern(
    "Resource Development",
    "T1583.003",
    "Acquisition: VPN Services",
    "resource_development",
    0.60,
    r"vpn service",
    r"commercial vpn",
    r"mullvad",
    r"nordvpn",
    r"surfshark",
    r"private vpn",
    r"anonymous vpn",
)

# T1584 — Compromise Infrastructure
_add_pattern(
    "Resource Development",
    "T1584",
    "Compromise Infrastructure",
    "resource_development",
    0.55,
    r"compromised server",
    r"hacked server",
    r"botnet",
    r"zombie",
    r"zmap",
    r"masscan",
    r"compromised host",
    r"legit.*hijacked",
)

# T1584.001 — Compromise DNS
_add_pattern(
    "Resource Development",
    "T1584.001",
    "Compromise DNS",
    "resource_development",
    0.60,
    r"dns hijack",
    r"dns takeover",
    r"domain hijacking",
    r"expired domain.*redirect",
    r"subdomain takeover",
)

# T1584.002 — Compromise Web Services
_add_pattern(
    "Resource Development",
    "T1584.002",
    "Compromise Web Services",
    "resource_development",
    0.55,
    r"web shell",
    r"webshell",
    r"backdoor",
    r"defaced",
    r"compromised wordpress",
    r"compromised cms",
)

# T1585 — Develop Capabilities
_add_pattern(
    "Resource Development",
    "T1585",
    "Develop Capabilities",
    "resource_development",
    0.45,
    r"malware development",
    r"ransomware builder",
    r"keylogger.*source",
    r"exploit kit",
    r"payload.*development",
    r"c2.*framework",
)

# T1585.001 — Malware
_add_pattern(
    "Resource Development",
    "T1585.001",
    "Develop Malware",
    "resource_development",
    0.50,
    r"source code.*malware",
    r"github.*malware",
    r"malware source",
    r"ransomware source code",
    r"trojan.*source",
    r"bot.*source code",
)

# T1585.002 — Code Signing Certificates
_add_pattern(
    "Resource Development",
    "T1585.002",
    "Code Signing Certificates",
    "resource_development",
    0.55,
    r"code signing",
    r"code sign",
    r"ev certificate",
    r"authenticode",
    r"signtool",
)

# T1586 — Obtain/Use Capabilities
_add_pattern(
    "Resource Development",
    "T1586",
    "Obtain Capabilities",
    "resource_development",
    0.50,
    r"buy.*malware",
    r"purchase.*exploit",
    r"rent.*botnet",
    r"subscription.*c2",
    r"ransomware-as-a-service",
    r"rss",
)

# T1586.001 — Phishing Kits
_add_pattern(
    "Resource Development",
    "T1586.001",
    "Phishing Kits",
    "resource_development",
    0.65,
    r"phishing kit",
    r"phishing template",
    r"credential harvest.*kit",
    r"social engineering toolkit",
    r"setoolkit",
    r"gophish",
    r"king phisher",
)

# T1587 — Obtain Capabilities
_add_pattern(
    "Resource Development",
    "T1587",
    "Obtain Capabilities",
    "resource_development",
    0.45,
    r"0-day",
    r"zeroday",
    r"exploit purchase",
    r"bug bounty",
    r"vulnerability purchase",
)

# T1588 — Obtain Capabilities
_add_pattern(
    "Resource Development",
    "T1588",
    "Obtain Capabilities",
    "resource_development",
    0.45,
    r"buy exploit",
    r"purchase exploit",
    r"acquire capability",
    r"obtain tool",
)

# T1588.001 — Malware
_add_pattern(
    "Resource Development",
    "T1588.001",
    "Obtain Malware",
    "resource_development",
    0.55,
    r"malware download",
    r"malware sample",
    r"download.*malware",
    r"malware repo",
    r"github.*malware",
    r"malware dropper",
)

# T1588.002 — Tool
_add_pattern(
    "Resource Development",
    "T1588.002",
    "Obtain Tools",
    "resource_development",
    0.50,
    r"mimikatz",
    r"cobalt strike",
    r"metasploit",
    r"covenant",
    r"empire",
    r"koadic",
    r"psexec",
    r"bloodhound",
    r"sharphound",
    r"crackmapexec",
    r"hydra",
    r"john the ripper",
    r"hashcat",
)

# T1588.003 — Code Signing Certificates
_add_pattern(
    "Resource Development",
    "T1588.003",
    "Obtain Code Signing Certificates",
    "resource_development",
    0.55,
    r"code signing cert",
    r"ev code sign",
    r"code sign.*purchase",
    r"authenticode.*buy",
)

# T1588.004 — Digital Certificates
_add_pattern(
    "Resource Development",
    "T1588.004",
    "Obtain Digital Certificates",
    "resource_development",
    0.60,
    r"ssl certificate purchase",
    r"buy certificate",
    r"domain validated cert",
    r"wildcard cert",
    r"letsencrypt.*automation",
    r"acme.*protocol",
    r"certificate authority",
    r"ca cert",
)

# T1588.005 — Exploit
_add_pattern(
    "Resource Development",
    "T1588.005",
    "Obtain Exploits",
    "resource_development",
    0.50,
    r"exploitdb",
    r"metasploit module",
    r"cve-20\d\d",
    r"edb-id",
    r"exploit purchase",
    r"0-day exploit",
    r"pentest-exploit",
    r"poc.*exploit",
    r"proof of concept.*exploit",
)


# ── Dataclass ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KillChainTag:
    """
    MITRE ATT&CK kill chain tag attached to an OSINT finding.

    Attributes:
        tactic:          ATT&CK tactic name (e.g. "Reconnaissance").
        technique_id:    ATT&CK technique ID (e.g. "T1590.001").
        phase:           Kill chain phase (e.g. "reconnaissance").
        confidence:      Confidence score 0.0-1.0.
        evidence_ids:    Finding IDs that contributed to this tag.
    """

    tactic: str
    technique_id: str
    phase: str
    confidence: float
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tactic": self.tactic,
            "technique_id": self.technique_id,
            "phase": self.phase,
            "confidence": round(self.confidence, 4),
            "evidence_ids": list(self.evidence_ids),
        }


# ── Main Tagger ────────────────────────────────────────────────────────────────


def _extract_text(finding: CanonicalFinding | dict) -> str:
    """Extract searchable text from a finding (dict or CanonicalFinding)."""
    parts: list[str] = []

    if isinstance(finding, dict):
        parts.append(str(finding.get("ioc_value", "")))
        parts.append(str(finding.get("ioc_type", "")))
        parts.append(str(finding.get("source_type", "")))
        parts.append(str(finding.get("finding_id", "")))
        payload = finding.get("payload_text", "")
        if payload:
            parts.append(str(payload))
    else:
        parts.append(str(getattr(finding, "ioc_value", "") or ""))
        parts.append(str(getattr(finding, "ioc_type", "") or ""))
        parts.append(str(getattr(finding, "source_type", "") or ""))
        parts.append(str(getattr(finding, "finding_id", "") or ""))
        payload = getattr(finding, "payload_text", "")
        if payload:
            parts.append(str(payload))

    return " ".join(parts)


def _get_finding_id(finding: CanonicalFinding | dict) -> str:
    """Get finding_id from a finding."""
    if isinstance(finding, dict):
        return str(finding.get("finding_id", "") or "")
    return str(getattr(finding, "finding_id", "") or "")


def ioc_to_technique_ids(ioc_type: str, ioc_value: str) -> list[str]:
    """
    Map IOC type + value to likely ATT&CK technique IDs.

    Returns a list of matching technique_ids based on IOC context.
    Used for quick triage when full text matching is unnecessary.
    """
    results: list[str] = []
    val_lower = ioc_value.lower()

    # Domain-based techniques
    if ioc_type in ("domain", "fqdn"):
        results.extend(["T1590", "T1590.001", "T1590.002", "T1591", "T1598"])
        if "github" in val_lower or "gitlab" in val_lower:
            results.extend(["T1596", "T1585"])
        if any(k in val_lower for k in ("aws", "s3", "cloudfront")):
            results.extend(["T1583", "T1583.002"])
        if "azure" in val_lower:
            results.extend(["T1583", "T1583.002"])

    # IP-based techniques
    elif ioc_type in ("ipv4", "ipv6", "ip"):
        results.extend(["T1590", "T1590.003", "T1590.004", "T1592"])
        results.extend(["T1583", "T1583.001"])
        if any(k in val_lower for k in ("tor", "onion")):
            results.extend(["T1583.002", "T1584.002"])
        if "vpn" in val_lower or "openvpn" in val_lower:
            results.append("T1583.003")

    # URL-based techniques
    elif ioc_type == "url":
        results.extend(["T1590", "T1592", "T1598"])
        if any(k in val_lower for k in ("pastebin", "github", "gist")):
            results.extend(["T1596", "T1585.001"])
        if "phishing" in val_lower or "login" in val_lower or "signin" in val_lower:
            results.extend(["T1598", "T1586.001"])
        if "download" in val_lower or "malware" in val_lower:
            results.extend(["T1588.001", "T1585"])

    # Hash-based techniques
    elif ioc_type in ("md5", "sha1", "sha256", "sha512"):
        results.extend(["T1588.001", "T1585.001"])
        if any(k in val_lower for k in ("malware", "ransomware", "trojan")):
            results.append("T1585")

    # Email-based techniques
    elif ioc_type in ("email", "email_addr"):
        results.extend(["T1598", "T1586.001"])
        if any(k in val_lower for k in ("spearphishing", "phishing")):
            results.append("T1598")

    # Certificate fingerprints
    elif ioc_type in ("certificate", "cert_fingerprint"):
        results.extend(["T1590.004", "T1588.004"])

    # Defensive: unknown type — return generic recon
    else:
        results.extend(["T1590", "T1593", "T1594"])

    # Deduplicate while preserving order
    seen = set()
    unique: list[str] = []
    for tid in results:
        if tid not in seen:
            seen.add(tid)
            unique.append(tid)
    return unique


class KillChainTagger:
    """
    Maps OSINT findings to MITRE ATT&CK kill chain phases.

    Deterministic: pattern matching only, no model inference.
    Bounded: MAX_TAGS_PER_FINDING=5, MAX_TAGGED_FINDINGS=1000.

    Usage:
        tagger = KillChainTagger()
        tags = tagger.tag_finding(finding)  # list[KillChainTag]
    """

    __slots__ = ("_tagged_count",)

    def __init__(self) -> None:
        self._tagged_count: int = 0

    @property
    def tagged_count(self) -> int:
        return self._tagged_count

    def tag_finding(self, finding: CanonicalFinding | dict) -> list[KillChainTag]:
        """
        Tag a single finding with MITRE ATT&CK kill chain labels.

        Args:
            finding: CanonicalFinding or dict with ioc_type, ioc_value,
                     source_type, finding_id, payload_text fields.

        Returns:
            List of KillChainTag (max MAX_TAGS_PER_FINDING=5).
        """
        if self._tagged_count >= MAX_TAGGED_FINDINGS:
            return []

        text = _extract_text(finding)
        finding_id = _get_finding_id(finding)
        if not text:
            return []

        # Quick IOC-based technique mapping for fast triage
        if isinstance(finding, dict):
            ioc_type = str(finding.get("ioc_type", "") or "")
            ioc_value = str(finding.get("ioc_value", "") or "")
        else:
            ioc_type = str(getattr(finding, "ioc_type", "") or "")
            ioc_value = str(getattr(finding, "ioc_value", "") or "")

        # Collect all matching patterns
        matches: list[tuple[float, str, str, str]] = []  # (confidence, tactic, technique_id, technique_name)

        for (tactic, tech_id, tech_name, phase, confidence, patterns) in _ATTACK_PATTERNS:
            for pat in patterns:
                try:
                    if pat.search(text):
                        matches.append((confidence, tactic, tech_id, tech_name))
                        break  # one match per pattern group is enough
                except Exception:
                    continue

        # Also add IOC-based technique hints
        ioc_tech_ids = ioc_to_technique_ids(ioc_type, ioc_value)
        for tech_id in ioc_tech_ids:
            for (tactic, tid, tech_name, phase, confidence, _) in _ATTACK_PATTERNS:
                if tid == tech_id:
                    matches.append((confidence, tactic, tid, tech_name))
                    break

        # Deduplicate by technique_id, keeping highest confidence
        tech_seen: dict[str, tuple[float, str, str]] = {}  # tech_id -> (confidence, tactic, tech_name)
        for conf, tactic, tid, tname in matches:
            if tid not in tech_seen or conf > tech_seen[tid][0]:
                tech_seen[tid] = (conf, tactic, tname)

        # Sort by confidence descending, take top N
        # tech_seen: tech_id -> (confidence, tactic, tech_name)
        sorted_tags = sorted(tech_seen.items(), key=lambda kv: -kv[1][0])
        top_items = sorted_tags[:MAX_TAGS_PER_FINDING]

        # Build KillChainTag list
        result: list[KillChainTag] = []
        for tech_id, (conf, tactic, tname) in top_items:
            # Find phase for this technique_id
            phase = "reconnaissance"
            for (_, t_id, _, ph, _, _) in _ATTACK_PATTERNS:
                if t_id == tech_id:
                    phase = ph
                    break

            result.append(KillChainTag(
                tactic=tactic,
                technique_id=tech_id,
                phase=phase,
                confidence=conf,
                evidence_ids=(finding_id,) if finding_id else (),
            ))

        if result:
            self._tagged_count += 1

        return result

    def tag_findings(
        self, findings: list[CanonicalFinding | dict]
    ) -> dict[str, list[KillChainTag]]:
        """
        Tag multiple findings.

        Args:
            findings: List of CanonicalFinding or dict.

        Returns:
            Dict mapping finding_id -> list of KillChainTag.
        """
        results: dict[str, list[KillChainTag]] = {}
        for finding in findings:
            if self._tagged_count >= MAX_TAGGED_FINDINGS:
                break
            fid = _get_finding_id(finding)
            if not fid:
                continue
            tags = self.tag_finding(finding)
            if tags:
                results[fid] = tags
        return results

    def reset(self) -> None:
        """Reset the tagged count (for new sprint)."""
        self._tagged_count = 0


# ── Factory ────────────────────────────────────────────────────────────────────


def create_kill_chain_tagger() -> KillChainTagger:
    """Create a new KillChainTagger instance."""
    return KillChainTagger()


# TYPE_CHECKING block — import CanonicalFinding without creating circular dependency
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
