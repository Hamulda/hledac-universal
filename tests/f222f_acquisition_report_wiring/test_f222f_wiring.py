"""Probe F222F: Acquisition Report Builder Wiring Verification."""
import pytest
from hledac.universal.runtime.acquisition_strategy import build_acquisition_report


class TestAcquisitionReportFieldWiring:
    """Verify build_acquisition_report signature accepts all required fields."""

    def test_public_provider_selection_debug_accepted(self):
        """Sprint F214-ACQ: public_provider_selection_debug must not raise TypeError."""
        report = build_acquisition_report(
            plan=None,
            public_provider_selection_debug={"provider": "crowdsec", "reason": "lower_error_rate"},
        )
        assert report is not None
        assert report.get("public_provider_selection_debug") == {
            "provider": "crowdsec", "reason": "lower_error_rate"
        }

    def test_query_param_accepted(self):
        """F214: query must be accepted as named parameter."""
        report = build_acquisition_report(query="LockBit ransomware", plan=None)
        assert report is not None

    def test_nonfeed_diagnostic_profile_preserved(self):
        """F216B: nonfeed_diagnostic profile must not collapse to default."""
        report = build_acquisition_report(
            acquisition_profile="nonfeed_diagnostic",
            nonfeed_priority_enabled=True,
            nonfeed_profile_expected_lanes=["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"],
        )
        assert report.get("acquisition_profile") == "nonfeed_diagnostic"
        assert report.get("nonfeed_priority_enabled") is True
        lanes = report.get("nonfeed_profile_expected_lanes", [])
        for lane in ["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"]:
            assert lane in lanes, f"{lane} missing"

    def test_nonfeed_priority_enabled_survives(self):
        """F216B: nonfeed_priority_enabled=True must survive into report."""
        report = build_acquisition_report(
            nonfeed_priority_enabled=True,
            nonfeed_profile_expected_lanes=["CT", "WAYBACK", "PASSIVE_DNS"],
        )
        assert report.get("nonfeed_priority_enabled") is True

    def test_expected_lanes_include_ct_wayback_passive_dns_doh(self):
        """F228C: expected lanes include all nonfeed lanes."""
        expected = ["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"]
        report = build_acquisition_report(nonfeed_profile_expected_lanes=expected)
        lanes = report.get("nonfeed_profile_expected_lanes", [])
        for lane in expected:
            assert lane in lanes

    def test_doh_fields_survive(self):
        """F214: DOH acquisition report fields must survive into final report."""
        report = build_acquisition_report(
            doh_planned=True,
            doh_scheduled=True,
            doh_request_attempted=True,
            doh_domains_attempted=3,
            doh_raw_count=12,
            doh_accepted_findings=5,
            doh_terminal_stage="attempted_accepted",
            doh_provider_errors=("timeout",),
            doh_cache_used=True,
        )
        assert report.get("doh_planned") is True
        assert report.get("doh_scheduled") is True
        assert report.get("doh_request_attempted") is True
        assert report.get("doh_domains_attempted") == 3
        assert report.get("doh_raw_count") == 12
        assert report.get("doh_accepted_findings") == 5
        assert report.get("doh_terminal_stage") == "attempted_accepted"
        assert report.get("doh_provider_errors") == ["timeout"]
        assert report.get("doh_cache_used") is True

    def test_ct_fields_survive(self):
        """F232: CT loss-stage telemetry fields must survive."""
        report = build_acquisition_report(
            ct_planned=True,
            ct_scheduled=True,
            ct_request_attempted=True,
            ct_terminal_stage="attempted_accepted",
            ct_raw_count=50,
            ct_provider_selected="certstream",
        )
        assert report.get("ct_planned") is True
        assert report.get("ct_scheduled") is True
        assert report.get("ct_request_attempted") is True
        assert report.get("ct_terminal_stage") == "attempted_accepted"
        assert report.get("ct_raw_count") == 50
        assert report.get("ct_provider_selected") == "certstream"

    def test_public_provider_debug_survives(self):
        """F214-ACQ: public_provider_selection_debug dict survives."""
        debug_info = {
            "provider": "censys.io",
            "selected_reason": "lower_error_rate",
            "fallback_used": False,
        }
        report = build_acquisition_report(public_provider_selection_debug=debug_info)
        assert report.get("public_provider_selection_debug") == debug_info

    def test_wayback_and_passive_dns_terminal_state_survive(self):
        """F228C: wayback and passive_dns terminal state survive."""
        report = build_acquisition_report(
            wayback_terminal_state="attempted_accepted",
            passive_dns_terminal_state="attempted_empty",
            nonfeed_surface_complete=False,
        )
        assert report.get("wayback_terminal_state") == "attempted_accepted"
        assert report.get("passive_dns_terminal_state") == "attempted_empty"
        assert report.get("nonfeed_surface_complete") is False

    def test_pivot_seed_fields_survive(self):
        """F222I: pivot seed domain/IP/URL/hash/CVE tuples survive."""
        report = build_acquisition_report(
            pivot_seed_domains=("evil.com", "malware.net"),
            pivot_seed_ips=("1.2.3.4",),
            pivot_seed_urls=("https://evil.com/payload",),
            pivot_seed_hashes=("abc123def456",),
            pivot_seed_cves=("CVE-2024-1234",),
        )
        assert report.get("pivot_seed_domains") == ["evil.com", "malware.net"]
        assert report.get("pivot_seed_ips") == ["1.2.3.4"]
        assert report.get("pivot_seed_urls") == ["https://evil.com/payload"]
        assert report.get("pivot_seed_hashes") == ["abc123def456"]
        assert report.get("pivot_seed_cves") == ["CVE-2024-1234"]

    def test_seed_context_fields_survive(self):
        """F222I: seed_context_available/propagated/lanes_unlocked survive."""
        report = build_acquisition_report(
            seed_context_available=True,
            seed_context_propagated=True,
            lanes_unlocked_by_seed_context=["CT", "DOH"],
        )
        assert report.get("seed_context_available") is True
        assert report.get("seed_context_propagated") is True
        assert report.get("lanes_unlocked_by_seed_context") == ["CT", "DOH"]

    def test_acquisition_report_fallback_field_exists(self):
        """Fail-soft: acquisition_report_fallback_used field must exist."""
        report = build_acquisition_report(acquisition_profile="nonfeed_diagnostic")
        assert "acquisition_report_fallback_used" in report

    def test_public_stage_counters_survive(self):
        """F208G-A: public_stage_counters dict survives."""
        counters = {
            "discovery_attempted": 3,
            "discovery_accepted": 1,
            "fetch_attempted": 2,
            "fetch_accepted": 1,
        }
        report = build_acquisition_report(public_stage_counters=counters)
        assert report.get("public_stage_counters") == counters

    def test_schema_version_present(self):
        """F208C: canonical schema_version must be present."""
        report = build_acquisition_report()
        assert "schema_version" in report


class TestNonfeedDiagnosticProfileNoCollapse:
    """F216B: nonfeed_diagnostic must NOT collapse to default profile."""

    def test_nonfeed_diagnostic_not_forced_to_default(self):
        """Explicit nonfeed_diagnostic profile must survive."""
        report = build_acquisition_report(
            acquisition_profile="nonfeed_diagnostic",
            nonfeed_priority_enabled=True,
            nonfeed_profile_expected_lanes=["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"],
        )
        assert report["acquisition_profile"] == "nonfeed_diagnostic"
        assert report["nonfeed_priority_enabled"] is True

    def test_explicit_none_profile_handled(self):
        """F223A: None acquisition_profile must not crash."""
        report = build_acquisition_report(acquisition_profile=None)
        assert "acquisition_profile" in report

    def test_default_profile_works(self):
        """Baseline: default profile must still produce a valid report."""
        report = build_acquisition_report(acquisition_profile="default")
        assert report.get("acquisition_profile") == "default"
        assert "schema_version" in report
