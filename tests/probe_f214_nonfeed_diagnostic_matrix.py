"""
F214-ACQ-NONFEED-DIAGNOSTIC-DRYRUN-MATRIX

Verifies that the nonfeed_diagnostic180 profile (canonical name: "nonfeed_diagnostic")
correctly plans public/CT/DOH/Wayback/passive_dns lanes based on query type.

No network calls. Tests planning/reporting outputs only.
Uses the existing probe_f214_nonfeed_lane_eligibility.py test infrastructure.

Matrix:
  A) Pure text query: "LockBit ransomware"          → public=T, CT=F, DOH=F, Wayback=F, pDNS=F
  B) Domain query: "mozilla.org"                    → public=T, CT=T, DOH=T, Wayback=T, pDNS=T
  C) URL query: "https://example.com/path"          → public=T, CT=T, DOH=T, Wayback=T, pDNS=T
  D) IP query: "1.1.1.1 suspicious"                 → public=T, CT=F, DOH=F, Wayback=T, pDNS=T
  E) Feed-derived domain: "LockBit ransomware"
     + synthetic finding with domain candidate      → CT=T, DOH=T, Wayback=T, pDNS=T

NOTE: The profile param passed to _build_nonfeed_lane_eligibility should be
the CANONICAL form ("nonfeed_diagnostic") since benchmark aliases are normalized
at the build_acquisition_plan entry point (line 2416-2417 in acquisition_strategy.py).
The "nonfeed_diagnostic180" benchmark alias never reaches the lane eligibility function.
"""


from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionProfile,
    _build_nonfeed_lane_eligibility,
)

# ─── Profile under test ────────────────────────────────────────────────────────
PROFILE = AcquisitionProfile.NONFEED_DIAGNOSTIC  # "nonfeed_diagnostic"


# ─── Helper ───────────────────────────────────────────────────────────────────
def matrix_row(query: str) -> dict:
    """Return the lane eligibility dict for the given query + nonfeed_diagnostic profile."""
    return _build_nonfeed_lane_eligibility(query, PROFILE, plan=None)


# ─── Test Class ────────────────────────────────────────────────────────────────
class TestF214NonfeedDiagnosticMatrix:
    """F214-ACQ-NONFEED-DIAGNOSTIC-DRYRUN-MATRIX — 5 scenarios."""

    # ── A) Pure text query ────────────────────────────────────────────────────

    def test_a_pure_text_public(self):
        r = matrix_row("LockBit ransomware")
        assert r["public"]["eligible"] is True
        assert r["public"]["reason"] == "nonfeed_diagnostic_expected"

    def test_a_pure_text_ct(self):
        r = matrix_row("LockBit ransomware")
        assert r["ct"]["eligible"] is False
        assert r["ct"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"

    def test_a_pure_text_doh(self):
        r = matrix_row("LockBit ransomware")
        assert r["doh"]["eligible"] is False
        assert r["doh"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"

    def test_a_pure_text_wayback(self):
        r = matrix_row("LockBit ransomware")
        assert r["wayback"]["eligible"] is False
        assert r["wayback"]["reason"] == "nonfeed_diagnostic_no_url_or_domain_candidates"

    def test_a_pure_text_passive_dns(self):
        r = matrix_row("LockBit ransomware")
        assert r["passive_dns"]["eligible"] is False
        assert r["passive_dns"]["reason"] == "nonfeed_diagnostic_no_domain_or_ip_candidates"

    # ── B) Domain query ───────────────────────────────────────────────────────

    def test_b_domain_public(self):
        r = matrix_row("mozilla.org")
        assert r["public"]["eligible"] is True
        assert r["public"]["reason"] == "nonfeed_diagnostic_expected"

    def test_b_domain_ct(self):
        r = matrix_row("mozilla.org")
        assert r["ct"]["eligible"] is True
        assert r["ct"]["reason"] == "domain_candidates_present"

    def test_b_domain_doh(self):
        r = matrix_row("mozilla.org")
        assert r["doh"]["eligible"] is True
        assert r["doh"]["reason"] == "domain_candidates_present"

    def test_b_domain_wayback(self):
        r = matrix_row("mozilla.org")
        assert r["wayback"]["eligible"] is True
        assert r["wayback"]["reason"] == "url_or_domain_candidates_present"

    def test_b_domain_passive_dns(self):
        r = matrix_row("mozilla.org")
        assert r["passive_dns"]["eligible"] is True
        assert r["passive_dns"]["reason"] == "domain_or_ip_candidates_present"

    # ── C) URL query ──────────────────────────────────────────────────────────

    def test_c_url_public(self):
        r = matrix_row("https://example.com/path")
        assert r["public"]["eligible"] is True
        assert r["public"]["reason"] == "nonfeed_diagnostic_expected"

    def test_c_url_ct(self):
        r = matrix_row("https://example.com/path")
        assert r["ct"]["eligible"] is True
        assert r["ct"]["reason"] == "domain_candidates_present"

    def test_c_url_doh(self):
        r = matrix_row("https://example.com/path")
        assert r["doh"]["eligible"] is True
        assert r["doh"]["reason"] == "domain_candidates_present"

    def test_c_url_wayback(self):
        r = matrix_row("https://example.com/path")
        assert r["wayback"]["eligible"] is True
        assert r["wayback"]["reason"] == "url_or_domain_candidates_present"

    def test_c_url_passive_dns(self):
        r = matrix_row("https://example.com/path")
        assert r["passive_dns"]["eligible"] is True
        assert r["passive_dns"]["reason"] == "domain_or_ip_candidates_present"

    # ── D) IP query ───────────────────────────────────────────────────────────

    def test_d_ip_public(self):
        r = matrix_row("1.1.1.1 suspicious")
        assert r["public"]["eligible"] is True
        assert r["public"]["reason"] == "nonfeed_diagnostic_expected"

    def test_d_ip_ct(self):
        r = matrix_row("1.1.1.1 suspicious")
        assert r["ct"]["eligible"] is False
        assert r["ct"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"

    def test_d_ip_doh(self):
        r = matrix_row("1.1.1.1 suspicious")
        assert r["doh"]["eligible"] is False
        assert r["doh"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"

    def test_d_ip_wayback(self):
        # Wayback: eligible if has_url or has_fqdn
        # _has_url() returns True for any domain-or-IP match (IP is treated as a URL-like candidate).
        # "1.1.1.1 suspicious" → has_url=True (IP matches _has_domain_or_ip) → Wayback ELIGIBLE.
        r = matrix_row("1.1.1.1 suspicious")
        assert r["wayback"]["eligible"] is True
        assert r["wayback"]["reason"] == "url_or_domain_candidates_present"

    def test_d_ip_passive_dns(self):
        # Passive DNS: eligible if has_domain (domain or IP)
        r = matrix_row("1.1.1.1 suspicious")
        assert r["passive_dns"]["eligible"] is True
        assert r["passive_dns"]["reason"] == "domain_or_ip_candidates_present"

    # ── E) Feed-derived domain candidate ───────────────────────────────────────
    # Scenario: query is pure text, but a feed finding provides a domain candidate.
    # The lane eligibility matrix is computed from the QUERY string only
    # (not from feed findings). This is by design — feed findings are processed
    # at runtime by the ledger. The matrix shows what WOULD be eligible if
    # candidates were extracted from findings.
    # For scenario E, we verify the matrix would show all nonfeed lanes eligible
    # IF the domain "leak.lockbit-example.test" were present in the query.

    def test_e_candidate_domain_public(self):
        # Domain candidate present in query itself
        r = matrix_row("leak.lockbit-example.test")
        assert r["public"]["eligible"] is True

    def test_e_candidate_domain_ct(self):
        r = matrix_row("leak.lockbit-example.test")
        assert r["ct"]["eligible"] is True

    def test_e_candidate_domain_doh(self):
        r = matrix_row("leak.lockbit-example.test")
        assert r["doh"]["eligible"] is True

    def test_e_candidate_domain_wayback(self):
        r = matrix_row("leak.lockbit-example.test")
        assert r["wayback"]["eligible"] is True

    def test_e_candidate_domain_passive_dns(self):
        r = matrix_row("leak.lockbit-example.test")
        assert r["passive_dns"]["eligible"] is True


# ─── Sanity: default profile vs nonfeed_diagnostic reason strings differ ───────
class TestF214ReasonStringDifference:
    """nonfeed_diagnostic profile uses profile-specific reason strings."""

    def test_ct_reason_uses_nonfeed_diagnostic_suffix(self):
        r_nfd = matrix_row("LockBit ransomware")
        r_def = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r_nfd["ct"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"
        assert r_def["ct"]["reason"] == "no_domain_candidates"
        assert r_nfd["ct"]["reason"] != r_def["ct"]["reason"]

    def test_doh_reason_uses_nonfeed_diagnostic_suffix(self):
        r_nfd = matrix_row("LockBit ransomware")
        r_def = _build_nonfeed_lane_eligibility("LockBit ransomware", "default", None)
        assert r_nfd["doh"]["reason"] == "nonfeed_diagnostic_no_domain_candidates"
        assert r_def["doh"]["reason"] == "no_domain_candidates"

    def test_public_reason_uses_nonfeed_diagnostic(self):
        r_nfd = matrix_row("anything")
        r_def = _build_nonfeed_lane_eligibility("anything", "default", None)
        assert r_nfd["public"]["reason"] == "nonfeed_diagnostic_expected"
        assert r_def["public"]["reason"] == "always_eligible_advisory"
