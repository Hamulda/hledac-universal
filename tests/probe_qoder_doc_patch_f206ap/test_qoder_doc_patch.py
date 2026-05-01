"""
Sprint F206AP — Qoder Doc Patch Tests

Validates that top HIGH severity Qoder doc overclaims were patched
in Specialized Domain Probes.md and Benchmark and Performance Probes.md.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SPECIALIZED_DOMAIN_PROBES = (
    ROOT / ".qoder/repowiki/en/content/Testing and Quality Assurance"
    / "Probe Testing System/Probe Categories and Classification"
    / "Specialized Domain Probes.md"
)
BENCHMARK_PERF_PROBES = (
    ROOT / ".qoder/repowiki/en/content/Testing and Quality Assurance"
    / "Probe Testing System/Probe Categories and Classification"
    / "Benchmark and Performance Probes.md"
)
REPORT_DIR = ROOT / "probe_qoder_doc_patch_f206ap"
REPORT_JSON = REPORT_DIR / "qoder_doc_patch.json"


class TestPatchedFilesExist:
    def test_specialized_domain_probes_patched(self):
        assert SPECIALIZED_DOMAIN_PROBES.exists(), (
            "Specialized Domain Probes.md must exist after patch"
        )

    def test_benchmark_perf_probes_patched(self):
        assert BENCHMARK_PERF_PROBES.exists(), (
            "Benchmark and Performance Probes.md must exist after patch"
        )

    def test_report_json_exists(self):
        assert REPORT_JSON.exists(), (
            "qoder_doc_patch.json must exist"
        )

    def test_report_md_exists(self):
        assert (REPORT_DIR / "REPORT_QODER_DOC_PATCH.md").exists(), (
            "REPORT_QODER_DOC_PATCH.md must exist"
        )


class TestRealityStatusBlocks:
    def test_specialized_domain_has_reality_status(self):
        content = SPECIALIZED_DOMAIN_PROBES.read_text()
        assert "Reality status:" in content, (
            "Specialized Domain Probes.md must contain Reality status blocks"
        )
        assert "DONOR_OR_OPTIONAL" in content, (
            "Must have DONOR_OR_OPTIONAL verdict for stealth/temporal layers"
        )

    def test_benchmark_probes_has_reality_status(self):
        content = BENCHMARK_PERF_PROBES.read_text()
        assert "Reality status:" in content, (
            "Benchmark and Performance Probes.md must contain Reality status blocks"
        )
        assert "TEST_ONLY" in content, (
            "Must have TEST_ONLY verdict for benchmark modules"
        )


class TestNoHighSeverityOverclaimsRemain:
    def test_specialized_domain_no_canonical_production_donor(self):
        content = SPECIALIZED_DOMAIN_PROBES.read_text()
        lines = content.split("\n")
        donor_section_lines = [
            l for l in lines
            if "DONOR_OR_OPTIONAL" in l or "optional donor" in l.lower()
        ]
        assert len(donor_section_lines) > 0, (
            "Specialized Domain Probes.md must label stealth/temporal as donor"
        )

    def test_benchmark_probes_no_canonical_production_wired(self):
        content = BENCHMARK_PERF_PROBES.read_text()
        lines = content.split("\n")
        test_only_lines = [
            l for l in lines
            if "TEST_ONLY" in l or "test-only" in l.lower() or "test only" in l.lower()
        ]
        assert len(test_only_lines) > 0, (
            "Benchmark and Performance Probes.md must label benchmarks as test-only"
        )


class TestReportAccuracy:
    def test_report_json_valid(self):
        data = json.loads(REPORT_JSON.read_text())
        assert data["sprint"] == "F206AP"
        assert data["total_high_patches"] == 4
        assert data["total_high_patched"] == 4
        assert len(data["patched_files"]) == 2

    def test_no_production_files_modified(self):
        data = json.loads(REPORT_JSON.read_text())
        assert data["production_files_touched"] == 0, (
            "No production Python/Swift files should be modified by this sprint"
        )
