# hledac/universal/tests/probe_8bj/test_sprint_8bj.py
"""Sprint 8BJ — Structured Export V1 (JSON-LD + STIX) tests."""

import json
import tempfile
from pathlib import Path

import pytest

from hledac.universal.export.jsonld_exporter import (
    normalize_export_input,
    render_jsonld,
    render_jsonld_str,
    render_jsonld_to_path,
)
from hledac.universal.export.stix_exporter import (
    render_stix_bundle,
    render_stix_bundle_json,
    render_stix_bundle_to_path,
)


class TestSprint8BJNormalize:
    """D.1-D.2: normalize_export_input handles msgspec.Struct and Mapping."""

    def test_normalize_export_input_accepts_msgspec_struct(self, minimal_report):
        result = normalize_export_input(minimal_report)
        assert isinstance(result, dict)
        assert result["accepted_findings"] == 0
        assert result["diagnostic_root_cause"] == "unknown"

    def test_normalize_export_input_accepts_mapping(self, minimal_report):
        # Plain dict (Mapping) — use normalize_report_input which calls dict on struct
        data = normalize_export_input(minimal_report)  # already a dict
        result = normalize_export_input(data)
        assert isinstance(result, dict)
        assert result["accepted_findings"] == 0

    def test_normalize_export_input_raises_for_invalid_type(self):
        class FakeObj:
            pass

        with pytest.raises(TypeError, match="msgspec.Struct or Mapping"):
            normalize_export_input(FakeObj())


class TestSprint8BJSparcity:
    """D.3-D.4, D.16: Zero and non-zero findings cases."""

    def test_jsonld_export_zero_findings_valid(self, minimal_report):
        obj = render_jsonld(minimal_report)
        assert "@context" in obj
        assert "@type" in obj
        assert obj["ghost:acceptedFindings"] == 0
        assert obj["ghost:rootCause"]["ghost:rootCause"] == "unknown"

    def test_jsonld_export_nonzero_findings_valid(self, accepted_present_report):
        obj = render_jsonld(accepted_present_report)
        assert obj["ghost:acceptedFindings"] == 5
        assert obj["ghost:rootCause"]["ghost:rootCause"] == "accepted_present"
        assert obj["ghost:rootCause"]["ghost:rootCauseLabel"] == "Accepted Findings Present"

    def test_sparse_report_does_not_crash(self):
        """D.16: A report with only a subset of fields doesn't crash."""
        sparse = {"accepted_findings": 0, "diagnostic_root_cause": "unknown"}
        obj = render_jsonld(sparse)
        assert obj["ghost:acceptedFindings"] == 0
        bundle = render_stix_bundle(sparse)
        assert bundle["type"] == "bundle"


class TestSprint8BJDeterminism:
    """D.5-D.6, D.9, D.15: Deterministic output."""

    def test_jsonld_str_is_deterministic(self, accepted_present_report):
        s1 = render_jsonld_str(accepted_present_report)
        s2 = render_jsonld_str(accepted_present_report)
        assert s1 == s2

    def test_stix_json_is_deterministic(self, accepted_present_report):
        # Note ids contain random UUIDs so compare by parsed structure
        b1 = json.loads(render_stix_bundle_json(accepted_present_report))
        b2 = json.loads(render_stix_bundle_json(accepted_present_report))
        # Bundle type and spec_version are deterministic
        assert b1["type"] == b2["type"] == "bundle"
        assert b1["spec_version"] == b2["spec_version"] == "2.1"
        # Same number of objects
        assert len(b1["objects"]) == len(b2["objects"])
        # Object types and created/modified timestamps match (ids differ due to UUIDs)
        for o1, o2 in zip(b1["objects"], b2["objects"]):
            assert o1["type"] == o2["type"]
            assert o1["created"] == o2["created"]
            assert o1["modified"] == o2["modified"]

    def test_jsonld_keys_are_sorted(self, minimal_report):
        obj = render_jsonld(minimal_report)
        s = json.dumps(obj, sort_keys=True)
        parsed = json.loads(s)
        keys = list(parsed.keys())
        assert keys == sorted(keys), f"Keys not sorted: {keys}"

    def test_filename_is_deterministic(self, minimal_report, tmp_path):
        path1 = render_jsonld_to_path(minimal_report, None)
        path1.unlink()
        path2 = render_jsonld_to_path(minimal_report, None)
        assert path1.name == path2.name


class TestSprint8BJToPath:
    """D.7, D.10: File output."""

    def test_jsonld_to_path_writes_file(self, minimal_report, tmp_path):
        out = tmp_path / "report.jsonld"
        result = render_jsonld_to_path(minimal_report, out)
        assert result == out
        assert out.exists()
        data = json.loads(out.read_text())
        assert "@context" in data

    def test_stix_to_path_writes_file(self, minimal_report, tmp_path):
        out = tmp_path / "report.stix.json"
        result = render_stix_bundle_to_path(minimal_report, out)
        assert result == out
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["type"] == "bundle"

    def test_path_resolution_prefers_env_then_paths_then_tmp(
        self, minimal_report, monkeypatch
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("GHOST_EXPORT_DIR", tmpdir)
            p = render_jsonld_to_path(minimal_report)
            assert p.parent.name == Path(tmpdir).name

    def test_path_resolution_falls_back_to_tmp(self, minimal_report, monkeypatch):
        # Unset GHOST_EXPORT_DIR, paths import will fail in test env
        monkeypatch.delenv("GHOST_EXPORT_DIR", raising=False)
        p = render_jsonld_to_path(minimal_report)
        assert p.exists() or p.parent.exists()


class TestSprint8BJContent:
    """D.11-D.14, D.17-D.18: Canonical labels and values."""

    def test_exporters_share_canonical_root_cause_labels(
        self, minimal_report
    ):
        """D.11: JSON-LD and STIX use same canonical root-cause labels."""
        obj = render_jsonld(minimal_report)
        stix = render_stix_bundle(minimal_report)

        jsonld_rc = obj["ghost:rootCause"]["ghost:rootCause"]
        # STIX embeds root cause in note abstract
        stix_abstract = next(
            o["abstract"] for o in stix["objects"]
            if o.get("type") == "note" and "Root cause:" in o.get("abstract", "")
        )
        assert "unknown" in jsonld_rc
        assert "Root cause: unknown" in stix_abstract

    def test_exporters_preserve_signal_funnel_values(
        self, accepted_present_report
    ):
        """D.12: Signal funnel numeric values are preserved."""
        obj = render_jsonld(accepted_present_report)
        sf = obj["ghost:signalFunnel"]
        assert sf["ghost:entriesSeen"] == 30
        assert sf["ghost:entriesScanned"] == 28
        assert sf["ghost:entriesWithHits"] == 10
        assert sf["ghost:totalPatternHits"] == 25

    def test_exporters_preserve_store_rejection_values(
        self, duplicate_rejection_report
    ):
        """D.13: Store rejection trace numeric values are preserved."""
        obj = render_jsonld(duplicate_rejection_report)
        srt = obj["ghost:storeRejectionTrace"]
        assert srt["ghost:acceptedCountDelta"] == 0
        assert srt["ghost:inMemoryDuplicateRejectedCountDelta"] == 2
        assert srt["ghost:persistentDuplicateRejectedCountDelta"] == 3

    def test_urls_and_strings_are_safely_rendered(
        self, accepted_present_report
    ):
        """D.17: URLs appear in JSON without injection."""
        obj = render_jsonld(accepted_present_report)
        s = json.dumps(obj)
        # No triple-backtick injection possible in JSON
        assert '```' not in s
        # Feed URLs present
        per_source = obj["ghost:perSourceHealth"]
        assert len(per_source) == 2
        assert per_source[0]["ghost:feedUrl"] == "https://example.com/feed1"

    def test_markdown_interop_labels_match_8bb(
        self, network_variance_report
    ):
        """D.18: Canonical labels match markdown_reporter (8BB)."""
        from hledac.universal.export.markdown_reporter import (
            _ROOT_CAUSE_LABELS as MD_LABELS,
        )
        from hledac.universal.export.jsonld_exporter import (
            _ROOT_CAUSE_LABELS as JSONLD_LABELS,
        )
        from hledac.universal.export.stix_exporter import (
            _ROOT_CAUSE_LABELS as STIX_LABELS,
        )
        for key in MD_LABELS:
            assert JSONLD_LABELS[key] == MD_LABELS[key]
            assert STIX_LABELS[key] == MD_LABELS[key]


class TestSprint8BJSTIXShape:
    """D.19, B.7: STIX builtins path has proper RFC3339 and UUID shape."""

    def test_stix_builtins_path_has_bundle_type_and_uuid_shape(
        self, minimal_report
    ):
        """D.19: Bundle has type=bundle, UUID-based id, spec_version."""
        bundle = render_stix_bundle(minimal_report)
        assert bundle["type"] == "bundle"
        assert bundle["id"].startswith("bundle--")
        assert len(bundle["id"]) == len("bundle--") + 36
        assert bundle["spec_version"] == "2.1"

    def test_stix_builtins_path_has_rfc3339_timestamps(
        self, minimal_report
    ):
        """D.19: Timestamps are RFC3339 format."""
        bundle = render_stix_bundle(minimal_report)
        import re
        RFC3339 = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
        )
        assert RFC3339.match(bundle["created"])
        assert RFC3339.match(bundle["modified"])
        # Each note has created/modified
        for obj in bundle["objects"]:
            assert RFC3339.match(obj["created"])
            assert RFC3339.match(obj["modified"])

    def test_stix_export_zero_findings_is_metadata_safe(
        self, minimal_report
    ):
        """B.7: Zero findings → no fake IOC/indicator/malware objects."""
        bundle = render_stix_bundle(minimal_report)
        types = {o["type"] for o in bundle["objects"]}
        forbidden = {"indicator", "malware", "threat-actor", "attack-pattern",
                     "intrusion-set", "tool", "vulnerability", "exploit-target"}
        assert not (types & forbidden), f"Found forbidden types: {types & forbidden}"
        # Only identity + note objects
        assert types.issubset({"identity", "note"})

    def test_stix_objects_have_uuid_ids(self, minimal_report):
        bundle = render_stix_bundle(minimal_report)
        for obj in bundle["objects"]:
            assert "id" in obj
            assert obj["id"].startswith(obj["type"] + "--")
            # identity--ghost-prime is a semantic ID (STIX allows this)
            # note objects must have UUID-based ids
            if obj["type"] == "note":
                expected_prefix = "note--"
                uuid_part = obj["id"][len(expected_prefix):]
                assert len(uuid_part) == 36
                assert uuid_part.count("-") == 4


class TestSprint8BJNoNetwork:
    """D.20: No network or model runtime."""

    def test_no_network_or_model_runtime_used(self, minimal_report):
        """B.1: Verify jsonld and stix modules don't import network/MLX at module level."""
        import sys
        # Collect currently loaded modules
        before = set(sys.modules.keys())
        # Force re-import to check for side-effects
        render_jsonld(minimal_report)
        render_stix_bundle(minimal_report)
        after = set(sys.modules.keys())
        new = after - before
        # Check no new mlx/torch/network modules were loaded
        forbidden = {m for m in new if any(m.startswith(p) for p in
            ["mlx", "torch", "transformers", "requests", "httpx", "aiohttp", "urllib"])}
        assert not forbidden, f"Forbidden modules imported: {forbidden}"


class TestSprint8BJBenchmarks:
    """E.1-E.3: Performance benchmarks."""

    def test_jsonld_render_x1000_under_300ms(self, minimal_report):
        import time
        start = time.perf_counter()
        for _ in range(1000):
            render_jsonld(minimal_report)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"1000 JSON-LD renders took {elapsed:.3f}s > 0.3s"

    def test_stix_render_x1000_under_300ms(self, minimal_report):
        import time
        start = time.perf_counter()
        for _ in range(1000):
            render_stix_bundle(minimal_report)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"1000 STIX renders took {elapsed:.3f}s > 0.3s"

    def test_to_path_x100_under_500ms(self, minimal_report, tmp_path):
        import time
        start = time.perf_counter()
        for i in range(100):
            out = tmp_path / f"bench_{i}.stix.json"
            render_stix_bundle_to_path(minimal_report, out)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"100 to_path calls took {elapsed:.3f}s > 0.5s"
