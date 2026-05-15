"""
Sprint F214: Nonfeed Lane Eligibility Matrix — probe tests.

Synthetic validation of _build_nonfeed_lane_eligibility():
- No-domain query: CT/DOH/Wayback/passive_dns false, public true
- Domain candidate: all nonfeed eligible
- IP-only candidate: CT/DOH false, Wayback/pDNS true
- nonfeed_diagnostic profile: different reason strings
- Schema: correct required_inputs per lane
"""

import pytest

from hledac.universal.runtime.acquisition_strategy import (
    _build_nonfeed_lane_eligibility,
)


class TestF214NoDomainQuery:
    """LockBit ransomware — no domains → only public eligible."""

    def test_public_eligible(self):
        r = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r["public"]["eligible"] is True

    def test_ct_not_eligible(self):
        r = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r["ct"]["eligible"] is False
        assert r["ct"]["reason"] == "no_domain_candidates"

    def test_doh_not_eligible(self):
        r = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r["doh"]["eligible"] is False
        assert r["doh"]["reason"] == "no_domain_candidates"

    def test_wayback_not_eligible(self):
        r = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r["wayback"]["eligible"] is False
        assert r["wayback"]["reason"] == "no_url_or_domain_candidates"

    def test_passive_dns_not_eligible(self):
        r = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r["passive_dns"]["eligible"] is False
        assert r["passive_dns"]["reason"] == "no_domain_or_ip_candidates"


class TestF214DomainCandidate:
    """Query with domain candidate → CT/DOH/Wayback/pDNS eligible."""

    def test_all_nonfeed_eligible(self):
        r = _build_nonfeed_lane_eligibility("evil.com malicious", "default", None)
        assert r["ct"]["eligible"] is True
        assert r["doh"]["eligible"] is True
        assert r["wayback"]["eligible"] is True
        assert r["passive_dns"]["eligible"] is True

    def test_reasons_domain_candidates_present(self):
        r = _build_nonfeed_lane_eligibility("evil.com malicious", "default", None)
        assert r["ct"]["reason"] == "domain_candidates_present"
        assert r["doh"]["reason"] == "domain_candidates_present"
        assert r["wayback"]["reason"] == "url_or_domain_candidates_present"
        assert r["passive_dns"]["reason"] == "domain_or_ip_candidates_present"

    def test_public_always_eligible(self):
        r = _build_nonfeed_lane_eligibility("evil.com malicious", "default", None)
        assert r["public"]["eligible"] is True
        assert r["public"]["reason"] == "always_eligible_advisory"


class TestF214IPCandidate:
    """IP-only candidate → CT/DOH false, pDNS true, Wayback true (via domain-or-IP)."""

    def test_ct_not_eligible_ip_only(self):
        r = _build_nonfeed_lane_eligibility("192.168.1.1 suspicious", "default", None)
        assert r["ct"]["eligible"] is False
        assert r["ct"]["reason"] == "no_domain_candidates"

    def test_doh_not_eligible_ip_only(self):
        r = _build_nonfeed_lane_eligibility("192.168.1.1 suspicious", "default", None)
        assert r["doh"]["eligible"] is False
        assert r["doh"]["reason"] == "no_domain_candidates"

    def test_wayback_eligible_ip(self):
        r = _build_nonfeed_lane_eligibility("192.168.1.1 suspicious", "default", None)
        assert r["wayback"]["eligible"] is True  # has_url = has_domain_or_ip

    def test_passive_dns_eligible_ip(self):
        r = _build_nonfeed_lane_eligibility("192.168.1.1 suspicious", "default", None)
        assert r["passive_dns"]["eligible"] is True

    def test_available_inputs_domain_false_ip_true(self):
        r = _build_nonfeed_lane_eligibility("192.168.1.1 suspicious", "default", None)
        assert r["public"]["available_inputs"]["domain"] is False
        assert r["public"]["available_inputs"]["ip"] is True


class TestF214NonfeedDiagnosticProfile:
    """nonfeed_diagnostic profile uses profile-specific reason strings when NOT eligible."""

    def test_ct_nonfeed_diagnostic_reason_when_not_eligible(self):
        # With no domain candidates, nonfeed_diagnostic uses profile-specific reason
        r_nfd = _build_nonfeed_lane_eligibility("LockBit ransomware", "nonfeed_diagnostic", None)
        r_def = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r_nfd["ct"]["eligible"] is False
        assert "nonfeed_diagnostic" in r_nfd["ct"]["reason"]
        assert r_nfd["ct"]["reason"] != r_def["ct"]["reason"]

    def test_doh_nonfeed_diagnostic_reason_when_not_eligible(self):
        r_nfd = _build_nonfeed_lane_eligibility("LockBit ransomware", "nonfeed_diagnostic", None)
        r_def = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r_nfd["doh"]["eligible"] is False
        assert "nonfeed_diagnostic" in r_nfd["doh"]["reason"]
        assert r_nfd["doh"]["reason"] != r_def["doh"]["reason"]

    def test_public_nonfeed_diagnostic_reason(self):
        r = _build_nonfeed_lane_eligibility("example.com", "nonfeed_diagnostic", None)
        assert r["public"]["reason"] == "nonfeed_diagnostic_expected"

    def test_ct_eligible_when_domain_present_nonfeed_diagnostic(self):
        # When domain IS present, reason is the same regardless of profile
        r_nfd = _build_nonfeed_lane_eligibility("example.com", "nonfeed_diagnostic", None)
        assert r_nfd["ct"]["eligible"] is True
        assert r_nfd["ct"]["reason"] == "domain_candidates_present"


class TestF214SchemaRequiredInputs:
    """Each lane declares the inputs it requires."""

    def test_ct_required_inputs_domain(self):
        r = _build_nonfeed_lane_eligibility("example.com", "default", None)
        assert r["ct"]["required_inputs"] == ["domain"]

    def test_doh_required_inputs_domain(self):
        r = _build_nonfeed_lane_eligibility("example.com", "default", None)
        assert r["doh"]["required_inputs"] == ["domain"]

    def test_wayback_required_inputs_url_or_domain(self):
        r = _build_nonfeed_lane_eligibility("example.com", "default", None)
        assert set(r["wayback"]["required_inputs"]) == {"url", "domain"}

    def test_passive_dns_required_inputs_domain_or_ip(self):
        r = _build_nonfeed_lane_eligibility("example.com", "default", None)
        assert set(r["passive_dns"]["required_inputs"]) == {"domain", "ip"}

    def test_public_required_inputs_empty(self):
        r = _build_nonfeed_lane_eligibility("example.com", "default", None)
        assert r["public"]["required_inputs"] == []


class TestF214AvailableInputs:
    """available_inputs reflects what the query actually contains."""

    def test_ip_only_available_inputs(self):
        r = _build_nonfeed_lane_eligibility("10.0.0.1", "default", None)
        avail = r["public"]["available_inputs"]
        assert avail["domain"] is False
        assert avail["ip"] is True

    def test_fqdn_available_inputs(self):
        r = _build_nonfeed_lane_eligibility("evil.com", "default", None)
        avail = r["public"]["available_inputs"]
        assert avail["domain"] is True
        assert avail["ip"] is False

    def test_url_available_inputs(self):
        r = _build_nonfeed_lane_eligibility("https://example.com/path", "default", None)
        avail = r["public"]["available_inputs"]
        assert avail["url"] is True

    def test_domain_also_sets_url_in_available(self):
        # _has_url returns True for anything matching _has_domain_or_ip
        r = _build_nonfeed_lane_eligibility("evil.com", "default", None)
        assert r["public"]["available_inputs"]["url"] is True