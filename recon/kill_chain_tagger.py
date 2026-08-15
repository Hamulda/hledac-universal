"""
F203C: Kill Chain Tagger — MITRE ATT&CK Mapping for OSINT Findings

Maps raw OSINT findings to MITRE ATT&CK tactics and techniques:


  - Reconnaissance (TA0043): T1590-T1598 — target reconnaissance
  - Resource Development (TA0042): T1583-T1588 — capability development

Deterministic: no model, no network, pure Python.
Bounded: MAX_TAGS_PER_FINDING=5, MAX_TAGGED_FINDINGS=1000.

M1 safe: pure Python, no model load, no JS renderer.
"""
import re
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any
from _core import aclose
__all__ = ['KillChainTag', 'KillChainTagger', 'create_kill_chain_tagger', 'ioc_to_technique_ids']
MAX_TAGS_PER_FINDING: int = 5
MAX_TAGGED_FINDINGS: int = 1000
_ATTACK_PATTERNS: list[tuple[str, str, str, str, float, list[re.Pattern[str]]]] = []

def _compile(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.IGNORECASE)

def _add_pattern(tactic: str, technique_id: str, technique_name: str, phase: str, confidence: float, *patterns: str) -> None:
    compiled = [_compile(p) for p in patterns]
    _ATTACK_PATTERNS.append((tactic, technique_id, technique_name, phase, confidence, compiled))
_add_pattern('Reconnaissance', 'T1590', 'Gather Victim Network Information', 'reconnaissance', 0.5, 'dns record', 'nameserver', 'mx record', 'a record', 'aaaa record', 'ptr record', 'txt record', 'soa record', 'dns lookup', 'reverse dns', 'zone transfer', 'axfr', 'dns enumeration')
_add_pattern('Reconnaissance', 'T1590.001', 'DNS WHOIS/Registration Data', 'reconnaissance', 0.7, 'whois', 'domain registration', 'registrant', 'registration date', 'name server', 'domain expiry', 'registrar', 'admin contact', ' registrant ')
_add_pattern('Reconnaissance', 'T1590.002', 'Subdomain Enumeration', 'reconnaissance', 0.65, 'subdomain', 'sub domain', 'subdomain enumeration', 'dns bruteforce', 'dns scan')
_add_pattern('Reconnaissance', 'T1590.003', 'Network Boundary Mapping', 'reconnaissance', 0.45, 'ip range', 'cidr', 'network boundary', 'asn', 'bgp')
_add_pattern('Reconnaissance', 'T1590.004', 'SSL/TLS Certificate Intelligence', 'reconnaissance', 0.75, 'certificate transparency', 'certspotter', 'crt\\.sh', 'sslyze', 'ssl certificate', 'tls cert', 'san.*certificate', 'subject alternative name', 'certificate fingerprint', 'sha-?256.*cert', 'cert.*sha-?256')
_add_pattern('Reconnaissance', 'T1590.005', 'Passive DNS Records', 'reconnaissance', 0.7, 'passive dns', 'dns history', 'historical dns', 'dnsdb', 'forward dns', 'reverse dns record')
_add_pattern('Reconnaissance', 'T1591', 'Domain Properties Discovery', 'reconnaissance', 0.6, 'domain name', 'domainalexpiration', 'domain age', 'domain created', 'domain updated')
_add_pattern('Reconnaissance', 'T1592', 'Vulnerable Web Services', 'reconnaissance', 0.55, 'web server', 'http server', 'nginx', 'apache', 'iis', 'lighttpd', 'caddy', 'tomcat', 'jetty', 'open port', 'http banner', 'http title')
_add_pattern('Reconnaissance', 'T1593', 'Search Open Websites/Databases', 'reconnaissance', 0.6, 'search engine', 'google dork', 'shodan', 'censys', 'zoomeye', 'fofa', 'hunter', 'securitytrails', 'builtwith', 'wappalyzer', 'similarweb', 'alexa rank')
_add_pattern('Reconnaissance', 'T1594', 'Threat Intelligence Platform Lookup', 'reconnaissance', 0.65, 'threatintel', 'threat intel', 'alienvault', 'otx', 'pastebin', 'abuseipdb', 'ipvoid', 'urlvoid', 'virustotal', 'hybrid-analysis', 'threatfox', 'malware bazaar')
_add_pattern('Reconnaissance', 'T1595', 'Active Scanning: Vulnerability Scanning', 'reconnaissance', 0.6, 'cve-', 'vulnerability scan', 'cve scanning', 'vulnerability intelligence', 'exploit db', 'edb-')
_add_pattern('Reconnaissance', 'T1595.001', 'Active Scanning: WordPress Scanning', 'reconnaissance', 0.55, 'wpscan', 'wordpress', 'wp-content', 'wp-admin', 'wordpress version', 'wp-plugin')
_add_pattern('Reconnaissance', 'T1595.002', 'Active Scanning: SSH Scanning', 'reconnaissance', 0.55, 'ssh-', 'openssh', 'ssh version', 'ssh banner', 'ssh scan', 'port 22')
_add_pattern('Reconnaissance', 'T1595.003', 'Active Scanning: VPN Scanning', 'reconnaissance', 0.55, 'openvpn', 'ike', 'ipsec', 'vpn scan', 'port 500', 'port 4500', 'ike-scan')
_add_pattern('Reconnaissance', 'T1596', 'Search Public Repositories / Leaked Credentials', 'reconnaissance', 0.8, 'github.*token', 'gitlab.*token', 'aws.*key', 'api.key', 'apikey', 'secret.*key', 'password', 'credential', 'leak', 'breach', 'pwned', 'have i been pwned', 'leaked', 'pastebin', 'gist', 'commit.*secret', '.git/config', '.env.*password', 'id_rsa', 'id_ed25519', 'oauth.*token', 'bearer.*token', 'private.*key')
_add_pattern('Reconnaissance', 'T1597', 'Compromise Supply Chain', 'reconnaissance', 0.7, 'supply chain', 'npm package', 'pypi package', 'rubygems', 'nuget', 'dependency confusion', 'typosquatting', 'brand impersonation', 'package仿冒')
_add_pattern('Reconnaissance', 'T1598', 'Phishing for Information', 'reconnaissance', 0.7, 'spear phishing', 'phishing', 'email spoofing', 'typosquatting.*domain', 'lookalike domain', 'brand impersonation.*email', 'login page', 'credential harvesting')
_add_pattern('Resource Development', 'T1583', 'Acquire Infrastructure', 'resource_development', 0.6, 'vps', 'virtual private server', 'dedicated server', 'cloud instance', 'aws.*instance', 'azure.*vm', 'gcp.*instance', 'digitalocean', 'linode', 'vultr', 'ransomware.*infrastructure', 'bulletproof host', 'rogue dns')
_add_pattern('Resource Development', 'T1583.001', 'Acquisition: DNS Server', 'resource_development', 0.65, 'dns server', 'authoritative dns', 'recursive dns', 'private dns', 'dns tunneling', 'dnscat', 'iodine.*dns')
_add_pattern('Resource Development', 'T1583.002', 'Acquisition: Web Services', 'resource_development', 0.65, 'tor.*relay', 'tor bridge', 'onion service', 'dark web host', 'free host', 'file hosting', 'paste service', 'transfer.sh', '0x0\\.sh')
_add_pattern('Resource Development', 'T1583.003', 'Acquisition: VPN Services', 'resource_development', 0.6, 'vpn service', 'commercial vpn', 'mullvad', 'nordvpn', 'surfshark', 'private vpn', 'anonymous vpn')
_add_pattern('Resource Development', 'T1584', 'Compromise Infrastructure', 'resource_development', 0.55, 'compromised server', 'hacked server', 'botnet', 'zombie', 'zmap', 'masscan', 'compromised host', 'legit.*hijacked')
_add_pattern('Resource Development', 'T1584.001', 'Compromise DNS', 'resource_development', 0.6, 'dns hijack', 'dns takeover', 'domain hijacking', 'expired domain.*redirect', 'subdomain takeover')
_add_pattern('Resource Development', 'T1584.002', 'Compromise Web Services', 'resource_development', 0.55, 'web shell', 'webshell', 'backdoor', 'defaced', 'compromised wordpress', 'compromised cms')
_add_pattern('Resource Development', 'T1585', 'Develop Capabilities', 'resource_development', 0.45, 'malware development', 'ransomware builder', 'keylogger.*source', 'exploit kit', 'payload.*development', 'c2.*framework')
_add_pattern('Resource Development', 'T1585.001', 'Develop Malware', 'resource_development', 0.5, 'source code.*malware', 'github.*malware', 'malware source', 'ransomware source code', 'trojan.*source', 'bot.*source code')
_add_pattern('Resource Development', 'T1585.002', 'Code Signing Certificates', 'resource_development', 0.55, 'code signing', 'code sign', 'ev certificate', 'authenticode', 'signtool')
_add_pattern('Resource Development', 'T1586', 'Obtain Capabilities', 'resource_development', 0.5, 'buy.*malware', 'purchase.*exploit', 'rent.*botnet', 'subscription.*c2', 'ransomware-as-a-service', 'rss')
_add_pattern('Resource Development', 'T1586.001', 'Phishing Kits', 'resource_development', 0.65, 'phishing kit', 'phishing template', 'credential harvest.*kit', 'social engineering toolkit', 'setoolkit', 'gophish', 'king phisher')
_add_pattern('Resource Development', 'T1587', 'Obtain Capabilities', 'resource_development', 0.45, '0-day', 'zeroday', 'exploit purchase', 'bug bounty', 'vulnerability purchase')
_add_pattern('Resource Development', 'T1588', 'Obtain Capabilities', 'resource_development', 0.45, 'buy exploit', 'purchase exploit', 'acquire capability', 'obtain tool')
_add_pattern('Resource Development', 'T1588.001', 'Obtain Malware', 'resource_development', 0.55, 'malware download', 'malware sample', 'download.*malware', 'malware repo', 'github.*malware', 'malware dropper')
_add_pattern('Resource Development', 'T1588.002', 'Obtain Tools', 'resource_development', 0.5, 'mimikatz', 'cobalt strike', 'metasploit', 'covenant', 'empire', 'koadic', 'psexec', 'bloodhound', 'sharphound', 'crackmapexec', 'hydra', 'john the ripper', 'hashcat')
_add_pattern('Resource Development', 'T1588.003', 'Obtain Code Signing Certificates', 'resource_development', 0.55, 'code signing cert', 'ev code sign', 'code sign.*purchase', 'authenticode.*buy')
_add_pattern('Resource Development', 'T1588.004', 'Obtain Digital Certificates', 'resource_development', 0.6, 'ssl certificate purchase', 'buy certificate', 'domain validated cert', 'wildcard cert', 'letsencrypt.*automation', 'acme.*protocol', 'certificate authority', 'ca cert')
_add_pattern('Resource Development', 'T1588.005', 'Obtain Exploits', 'resource_development', 0.5, 'exploitdb', 'metasploit module', 'cve-20\\d\\d', 'edb-id', 'exploit purchase', '0-day exploit', 'pentest-exploit', 'poc.*exploit', 'proof of concept.*exploit')

class KillChainTag(msgspec.Struct, frozen=True, gc=False):
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
        return {'tactic': self.tactic, 'technique_id': self.technique_id, 'phase': self.phase, 'confidence': round(self.confidence, 4), 'evidence_ids': list(self.evidence_ids)}

def _extract_text(finding: CanonicalFinding | dict) -> str:
    """Extract searchable text from a finding (dict or CanonicalFinding)."""
    parts: list[str] = []
    if isinstance(finding, dict):
        parts.append(str(finding.get('ioc_value', '')))
        parts.append(str(finding.get('ioc_type', '')))
        parts.append(str(finding.get('source_type', '')))
        parts.append(str(finding.get('finding_id', '')))
        payload = finding.get('payload_text', '')
        if payload:
            parts.append(str(payload))
    else:
        parts.append(str(getattr(finding, 'ioc_value', '') or ''))
        parts.append(str(getattr(finding, 'ioc_type', '') or ''))
        parts.append(str(getattr(finding, 'source_type', '') or ''))
        parts.append(str(getattr(finding, 'finding_id', '') or ''))
        payload = getattr(finding, 'payload_text', '')
        if payload:
            parts.append(str(payload))
    return ' '.join(parts)

def _get_finding_id(finding: CanonicalFinding | dict) -> str:
    """Get finding_id from a finding."""
    if isinstance(finding, dict):
        return str(finding.get('finding_id', '') or '')
    return str(getattr(finding, 'finding_id', '') or '')

def ioc_to_technique_ids(ioc_type: str, ioc_value: str) -> list[str]:
    """
    Map IOC type + value to likely ATT&CK technique IDs.

    Returns a list of matching technique_ids based on IOC context.
    Used for quick triage when full text matching is unnecessary.
    """
    val_lower = ioc_value.lower()

    # Base techniques by IOC type
    base_techniques: dict[str, list[str]] = {
        'domain': ['T1590', 'T1590.001', 'T1590.002', 'T1591', 'T1598'],
        'fqdn': ['T1590', 'T1590.001', 'T1590.002', 'T1591', 'T1598'],
        'ipv4': ['T1590', 'T1590.003', 'T1590.004', 'T1592', 'T1583', 'T1583.001'],
        'ipv6': ['T1590', 'T1590.003', 'T1590.004', 'T1592', 'T1583', 'T1583.001'],
        'ip': ['T1590', 'T1590.003', 'T1590.004', 'T1592', 'T1583', 'T1583.001'],
        'url': ['T1590', 'T1592', 'T1598'],
        'md5': ['T1588.001', 'T1585.001'],
        'sha1': ['T1588.001', 'T1585.001'],
        'sha256': ['T1588.001', 'T1585.001'],
        'sha512': ['T1588.001', 'T1585.001'],
        'email': ['T1598', 'T1586.001'],
        'email_addr': ['T1598', 'T1586.001'],
        'certificate': ['T1590.004', 'T1588.004'],
        'cert_fingerprint': ['T1590.004', 'T1588.004'],
    }

    # Keyword modifiers by IOC type
    keyword_modifiers: dict[str, dict[str, list[str]]] = {
        'domain': {
            'github': ['T1596', 'T1585'],
            'gitlab': ['T1596', 'T1585'],
            'aws': ['T1583', 'T1583.002'],
            's3': ['T1583', 'T1583.002'],
            'cloudfront': ['T1583', 'T1583.002'],
            'azure': ['T1583', 'T1583.002'],
        },
        'fqdn': {
            'github': ['T1596', 'T1585'],
            'gitlab': ['T1596', 'T1585'],
            'aws': ['T1583', 'T1583.002'],
            's3': ['T1583', 'T1583.002'],
            'cloudfront': ['T1583', 'T1583.002'],
            'azure': ['T1583', 'T1583.002'],
        },
        'ipv4': {
            'tor': ['T1583.002', 'T1584.002'],
            'onion': ['T1583.002', 'T1584.002'],
            'vpn': ['T1583.003'],
            'openvpn': ['T1583.003'],
        },
        'ipv6': {
            'tor': ['T1583.002', 'T1584.002'],
            'onion': ['T1583.002', 'T1584.002'],
            'vpn': ['T1583.003'],
            'openvpn': ['T1583.003'],
        },
        'ip': {
            'tor': ['T1583.002', 'T1584.002'],
            'onion': ['T1583.002', 'T1584.002'],
            'vpn': ['T1583.003'],
            'openvpn': ['T1583.003'],
        },
        'url': {
            'pastebin': ['T1596', 'T1585.001'],
            'github': ['T1596', 'T1585.001'],
            'gist': ['T1596', 'T1585.001'],
            'phishing': ['T1598', 'T1586.001'],
            'login': ['T1598', 'T1586.001'],
            'signin': ['T1598', 'T1586.001'],
            'download': ['T1588.001', 'T1585'],
            'malware': ['T1588.001', 'T1585'],
        },
        'md5': {
            'malware': ['T1585'],
            'ransomware': ['T1585'],
            'trojan': ['T1585'],
        },
        'sha1': {
            'malware': ['T1585'],
            'ransomware': ['T1585'],
            'trojan': ['T1585'],
        },
        'sha256': {
            'malware': ['T1585'],
            'ransomware': ['T1585'],
            'trojan': ['T1585'],
        },
        'sha512': {
            'malware': ['T1585'],
            'ransomware': ['T1585'],
            'trojan': ['T1585'],
        },
        'email': {
            'spearphishing': ['T1598'],
            'phishing': ['T1598'],
        },
        'email_addr': {
            'spearphishing': ['T1598'],
            'phishing': ['T1598'],
        },
    }

    results: list[str] = list(base_techniques.get(ioc_type, ['T1590', 'T1593', 'T1594']))

    # Apply keyword modifiers
    modifiers = keyword_modifiers.get(ioc_type, {})
    for keyword, techniques in modifiers.items():
        if keyword in val_lower:
            results.extend(techniques)

    # Deduplicate while preserving order
    return list(dict.fromkeys(results))

class KillChainTagger:
    """
    Maps OSINT findings to MITRE ATT&CK kill chain phases.

    Deterministic: pattern matching only, no model inference.
    Bounded: MAX_TAGS_PER_FINDING=5, MAX_TAGGED_FINDINGS=1000.

    Usage:
        tagger = KillChainTagger()
        tags = tagger.tag_finding(finding)  # list[KillChainTag]
    """
    __slots__ = ('_tagged_count',)

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
        if isinstance(finding, dict):
            ioc_type = str(finding.get('ioc_type', '') or '')
            ioc_value = str(finding.get('ioc_value', '') or '')
        else:
            ioc_type = str(getattr(finding, 'ioc_type', '') or '')
            ioc_value = str(getattr(finding, 'ioc_value', '') or '')
        matches: list[tuple[float, str, str, str]] = []
        for tactic, tech_id, tech_name, phase, confidence, patterns in _ATTACK_PATTERNS:
            for pat in patterns:
                try:
                    if pat.search(text):
                        matches.append((confidence, tactic, tech_id, tech_name))
                        break
                except Exception:
                    continue
        ioc_tech_ids = ioc_to_technique_ids(ioc_type, ioc_value)
        for tech_id in ioc_tech_ids:
            for tactic, tid, tech_name, phase, confidence, _ in _ATTACK_PATTERNS:
                if tid == tech_id:
                    matches.append((confidence, tactic, tid, tech_name))
                    break
        tech_seen: dict[str, tuple[float, str, str]] = {}
        for conf, tactic, tid, tname in matches:
            if tid not in tech_seen or conf > tech_seen[tid][0]:
                tech_seen[tid] = (conf, tactic, tname)
        sorted_tags = sorted(tech_seen.items(), key=lambda kv: -kv[1][0])
        top_items = sorted_tags[:MAX_TAGS_PER_FINDING]
        result: list[KillChainTag] = []
        for tech_id, (conf, tactic, tname) in top_items:
            phase = 'reconnaissance'
            for _, t_id, _, ph, _, _ in _ATTACK_PATTERNS:
                if t_id == tech_id:
                    phase = ph
                    break
            result.append(KillChainTag(tactic=tactic, technique_id=tech_id, phase=phase, confidence=conf, evidence_ids=(finding_id,) if finding_id else ()))
        if result:
            self._tagged_count += 1
        return result

    def tag_findings(self, findings: list[CanonicalFinding | dict]) -> dict[str, list[KillChainTag]]:
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

def create_kill_chain_tagger() -> KillChainTagger:
    """Create a new KillChainTagger instance."""
    return KillChainTagger()
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding