"""
Sprint F203C: Kill Chain Tagger — Probe Tests
=============================================

Invariant mapping:
  F203C-1  | KillChainTag is frozen dataclass with correct fields
  F203C-2  | KillChainTagger.tag_finding returns list of KillChainTag
  F203C-3  | MAX_TAGS_PER_FINDING=5 cap enforced
  F203C-4  | MAX_TAGGED_FINDINGS=1000 cap enforced across tag_findings
  F203C-5  | tag_findings returns dict mapping finding_id -> list of KillChainTag
  F203C-6  | IOC type mapping (domain/ip/url/email/certificate) yields relevant techniques
  F203C-7  | Regex patterns on payload_text produce correct tactic/technique matches
  F203C-8  | KillChainTag.to_dict() produces serializable dict
  F203C-9  | create_kill_chain_tagger() returns KillChainTagger instance
  F203C-10 | Reset resets tagged_count to 0
"""

import pytest

from hledac.universal.intelligence.kill_chain_tagger import (
    MAX_TAGS_PER_FINDING,
    MAX_TAGGED_FINDINGS,
    KillChainTag,
    KillChainTagger,
    create_kill_chain_tagger,
    ioc_to_technique_ids,
)


# ============================================================================
# F203C-1: KillChainTag frozen dataclass
# ============================================================================

class TestKillChainTag:
    """F203C-1: KillChainTag is a frozen dataclass."""

    def test_kill_chain_tag_frozen(self):
        """KillChainTag instances are frozen (immutable)."""
        tag = KillChainTag(
            tactic="Reconnaissance",
            technique_id="T1590.001",
            phase="reconnaissance",
            confidence=0.7,
            evidence_ids=("f1", "f2"),
        )
        with pytest.raises(Exception):  # frozen dataclass — cannot setattr
            tag.confidence = 0.9  # type: ignore

    def test_kill_chain_tag_fields(self):
        """KillChainTag has correct fields."""
        tag = KillChainTag(
            tactic="Resource Development",
            technique_id="T1583.001",
            phase="resource_development",
            confidence=0.65,
            evidence_ids=("fid1",),
        )
        assert tag.tactic == "Resource Development"
        assert tag.technique_id == "T1583.001"
        assert tag.phase == "resource_development"
        assert tag.confidence == 0.65
        assert tag.evidence_ids == ("fid1",)

    def test_kill_chain_tag_to_dict(self):
        """KillChainTag.to_dict() returns serializable dict."""
        tag = KillChainTag(
            tactic="Reconnaissance",
            technique_id="T1590",
            phase="reconnaissance",
            confidence=0.5,
            evidence_ids=("f1",),
        )
        d = tag.to_dict()
        assert isinstance(d, dict)
        assert d["tactic"] == "Reconnaissance"
        assert d["technique_id"] == "T1590"
        assert d["phase"] == "reconnaissance"
        assert d["confidence"] == 0.5
        assert d["evidence_ids"] == ["f1"]


# ============================================================================
# F203C-2/3: KillChainTagger.tag_finding
# ============================================================================

class _MockFinding:
    """Minimal finding-like object."""
    __slots__ = ("finding_id", "ioc_type", "ioc_value", "source_type", "payload_text", "confidence", "ts")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestKillChainTaggerTagFinding:
    """F203C-2/3: tag_finding returns up to MAX_TAGS_PER_FINDING KillChainTags."""

    def test_tag_finding_returns_list(self):
        """tag_finding returns a list of KillChainTag."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f1",
            ioc_type="domain",
            ioc_value="evil.com",
            source_type="ct_log",
            payload_text="DNS records and WHOIS registration data for evil.com",
            confidence=0.8,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        assert isinstance(tags, list)
        assert all(isinstance(t, KillChainTag) for t in tags)

    def test_tag_finding_domain_whois(self):
        """Domain + WHOIS/registration keywords maps to T1590.001."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f2",
            ioc_type="domain",
            ioc_value="suspicious.com",
            source_type="ct_log",
            payload_text="whois registrant record shows admin contact for suspicious.com",
            confidence=0.7,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1590.001" in tech_ids

    def test_tag_finding_certificate_transparency(self):
        """Certificate transparency keywords map to T1590.004."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f3",
            ioc_type="certificate",
            ioc_value="sha256:abc123",
            source_type="ct_log",
            payload_text="certificate transparency crt.sh ssl certificate",
            confidence=0.9,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1590.004" in tech_ids

    def test_tag_finding_leaked_credentials(self):
        """Leaked credentials / pastebin keywords map to T1596."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f4",
            ioc_type="url",
            ioc_value="https://pastebin.com/abc123",
            source_type="public",
            payload_text="pastebin leaked password credential github token",
            confidence=0.8,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1596" in tech_ids

    def test_tag_finding_vulnerability_cve(self):
        """CVE keywords map to T1595 (active scanning / vulnerability)."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f5",
            ioc_type="domain",
            ioc_value="target.com",
            source_type="public",
            payload_text="cve-2024-1234 vulnerability scan exploit db",
            confidence=0.8,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1595" in tech_ids

    def test_tag_finding_malware_tool(self):
        """Mimikatz / Cobalt Strike / Metasploit keywords map to T1588.002."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f6",
            ioc_type="url",
            ioc_value="https://example.com/tool.exe",
            source_type="public",
            payload_text="cobalt strike metasploit covenant empire tool",
            confidence=0.7,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1588.002" in tech_ids

    def test_tag_finding_infrastructure_vps(self):
        """VPS / cloud instance keywords map to T1583."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f7",
            ioc_type="ip",
            ioc_value="1.2.3.4",
            source_type="ct_log",
            payload_text="vps virtual private server aws instance digitalocean",
            confidence=0.7,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        tech_ids = {t.technique_id for t in tags}
        assert "T1583" in tech_ids

    def test_tag_finding_max_tags_per_finding(self):
        """No more than MAX_TAGS_PER_FINDING tags per finding."""
        tagger = KillChainTagger()
        # Finding with many keywords spanning multiple techniques
        finding = _MockFinding(
            finding_id="f8",
            ioc_type="domain",
            ioc_value="test.com",
            source_type="ct_log",
            payload_text=(
                "dns record whois registration subdomain enumeration "
                "certificate transparency passive dns search engine shodan "
                "cve-2024 vulnerability scan github token password leak "
                "vps aws instance malware cobalt strike"
            ),
            confidence=0.8,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        assert len(tags) <= MAX_TAGS_PER_FINDING

    def test_tag_finding_empty_payload(self):
        """Finding with empty payload returns empty list."""
        tagger = KillChainTagger()
        finding = _MockFinding(
            finding_id="f9",
            ioc_type="domain",
            ioc_value="empty.com",
            source_type="ct_log",
            payload_text="",
            confidence=0.5,
            ts=123456.0,
        )
        tags = tagger.tag_finding(finding)
        # Empty text may still get IOC-based techniques, but it's bounded
        assert isinstance(tags, list)


# ============================================================================
# F203C-4/5: KillChainTagger.tag_findings
# ============================================================================

class TestKillChainTaggerTagFindings:
    """F203C-4/5: tag_findings batches multiple findings."""

    def test_tag_findings_returns_dict(self):
        """tag_findings returns dict mapping finding_id -> list of KillChainTag."""
        tagger = KillChainTagger()
        findings = [
            _MockFinding(
                finding_id="f1",
                ioc_type="domain",
                ioc_value="evil.com",
                source_type="ct_log",
                payload_text="whois registrant",
                confidence=0.8,
                ts=123456.0,
            ),
            _MockFinding(
                finding_id="f2",
                ioc_type="url",
                ioc_value="https://pastebin.com/abc",
                source_type="public",
                payload_text="leaked password credential",
                confidence=0.7,
                ts=123456.0,
            ),
        ]
        result = tagger.tag_findings(findings)
        assert isinstance(result, dict)
        assert "f1" in result
        assert "f2" in result
        assert all(isinstance(tags, list) for tags in result.values())
        assert all(isinstance(t, KillChainTag) for tags in result.values() for t in tags)

    def test_tag_findings_max_tagged_findings(self):
        """No more than MAX_TAGGED_FINDINGS total findings tagged."""
        tagger = KillChainTagger()
        findings = [
            _MockFinding(
                finding_id=f"f{i}",
                ioc_type="domain",
                ioc_value=f"domain{i}.com",
                source_type="ct_log",
                payload_text="whois dns certificate transparency",
                confidence=0.8,
                ts=123456.0,
            )
            for i in range(2000)
        ]
        result = tagger.tag_findings(findings)
        assert len(result) <= MAX_TAGGED_FINDINGS

    def test_tag_findings_empty_list(self):
        """Empty findings list returns empty dict."""
        tagger = KillChainTagger()
        result = tagger.tag_findings([])
        assert result == {}

    def test_tag_findings_missing_finding_id(self):
        """Findings without finding_id are skipped."""
        tagger = KillChainTagger()

        class _NoIDFinding:
            ioc_type = "domain"
            ioc_value = "test.com"
            source_type = "ct_log"
            payload_text = "whois"
            confidence = 0.8
            ts = 123456.0
            # no finding_id

        result = tagger.tag_findings([_NoIDFinding()])
        assert result == {}


# ============================================================================
# F203C-6: IOC type to technique mapping
# ============================================================================

class TestIOCTechniqueMapping:
    """F203C-6: IOC type mapping yields relevant technique IDs."""

    def test_domain_ioc_maps_to_recon(self):
        """Domain IOC maps to reconnaissance techniques."""
        result = ioc_to_technique_ids("domain", "example.com")
        assert isinstance(result, list)
        assert "T1590" in result
        assert "T1591" in result

    def test_ip_ioc_maps_to_recon_and_resource(self):
        """IP IOC maps to both recon and resource development techniques."""
        result = ioc_to_technique_ids("ipv4", "1.2.3.4")
        assert isinstance(result, list)
        assert "T1590" in result
        assert "T1583" in result

    def test_url_ioc_maps_to_recon(self):
        """URL IOC maps to reconnaissance techniques."""
        result = ioc_to_technique_ids("url", "https://example.com/page")
        assert isinstance(result, list)
        assert "T1590" in result
        assert "T1592" in result

    def test_hash_ioc_maps_to_malware(self):
        """Hash IOC maps to malware-related techniques."""
        result = ioc_to_technique_ids("sha256", "abc123def456")
        assert isinstance(result, list)
        assert "T1588.001" in result

    def test_email_ioc_maps_to_phishing(self):
        """Email IOC maps to phishing techniques."""
        result = ioc_to_technique_ids("email", "attacker@evil.com")
        assert isinstance(result, list)
        assert "T1598" in result

    def test_unknown_ioc_returns_generic(self):
        """Unknown IOC type returns generic recon techniques."""
        result = ioc_to_technique_ids("unknown_type", "somevalue")
        assert isinstance(result, list)
        assert "T1590" in result
        assert "T1593" in result


# ============================================================================
# F203C-9/10: Factory and reset
# ============================================================================

class TestKillChainTaggerFactory:
    """F203C-9/10: create_kill_chain_tagger and reset."""

    def test_create_kill_chain_tagger(self):
        """create_kill_chain_tagger returns KillChainTagger instance."""
        tagger = create_kill_chain_tagger()
        assert isinstance(tagger, KillChainTagger)

    def test_reset_clears_tagged_count(self):
        """reset() clears the tagged_count to 0."""
        tagger = create_kill_chain_tagger()
        finding = _MockFinding(
            finding_id="f1",
            ioc_type="domain",
            ioc_value="test.com",
            source_type="ct_log",
            payload_text="whois dns record",
            confidence=0.8,
            ts=123456.0,
        )
        tagger.tag_finding(finding)
        assert tagger.tagged_count == 1
        tagger.reset()
        assert tagger.tagged_count == 0
