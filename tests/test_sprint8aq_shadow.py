"""
Sprint 8AQ: Shadow-Only msgspec Pilot Tests

SHADOW-ONLY — these tests verify the shadow DTO module in isolation.
They do NOT touch autonomous_orchestrator.py or any live DTO definitions.

Tests:
  1. AdmissionResultShadow constructor parity
  2. BacklogCandidateShadow constructor parity
  3. to_dict wire-shape parity (shadow == dataclass baseline)
  4. Frozen immutability
  5. from_live adapter (live dataclass → shadow Struct)
  6. Cold import regression (shadow_dtos does NOT regress boot)
  7. Benchmark sanity (msgspec faster than dataclass)
"""

import dataclasses
import statistics
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Import the shadow module
# ---------------------------------------------------------------------------
from hledac.universal.utils.shadow_dtos import (
    AdmissionResultBaseline,
    AdmissionResultShadow,
    BacklogCandidateBaseline,
    BacklogCandidateShadow,
    admission_baseline_to_dict,
    admission_from_live,
    admission_to_dict,
    backlog_baseline_to_dict,
    backlog_from_live,
    backlog_to_dict,
    run_benchmark,
)

# ---------------------------------------------------------------------------
# Test 1: AdmissionResultShadow constructor parity
# ---------------------------------------------------------------------------

class TestAdmissionResultShadow:
    def test_constructor_all_fields(self):
        ar = AdmissionResultShadow(
            status="admit",
            score=0.75,
            content_hint="html",
            source_family="web",
            reason="score=0.75,family=1,coverage=0.50",
        )
        assert ar.status == "admit"
        assert ar.score == 0.75
        assert ar.content_hint == "html"
        assert ar.source_family == "web"
        assert ar.reason == "score=0.75,family=1,coverage=0.50"

    def test_constructor_reject_status(self):
        ar = AdmissionResultShadow(
            status="reject",
            score=0.0,
            content_hint="unknown",
            source_family="unknown",
            reason="malformed_url",
        )
        assert ar.status == "reject"
        assert ar.score == 0.0

    def test_constructor_hold_status(self):
        ar = AdmissionResultShadow(
            status="hold",
            score=0.35,
            content_hint="pdf",
            source_family="academic",
            reason="score=0.35,family=2,coverage=0.20",
        )
        assert ar.status == "hold"
        assert ar.score == 0.35


# ---------------------------------------------------------------------------
# Test 2: BacklogCandidateShadow constructor parity
# ---------------------------------------------------------------------------

class TestBacklogCandidateShadow:
    def test_constructor_all_fields(self):
        bc = BacklogCandidateShadow(
            url="https://example.com/article",
            score=0.75,
            source_family="web",
            content_hint="html",
            title_snippet="Example Article About Things and Stuff",
            contradiction_value=0.1,
            enqueued_at_cycle=5,
            lane_id="expansion",
        )
        assert bc.url == "https://example.com/article"
        assert bc.score == 0.75
        assert bc.source_family == "web"
        assert bc.content_hint == "html"
        assert bc.title_snippet == "Example Article About Things and Stuff"
        assert bc.contradiction_value == 0.1
        assert bc.enqueued_at_cycle == 5
        assert bc.lane_id == "expansion"

    def test_constructor_minimal(self):
        bc = BacklogCandidateShadow(
            url="https://x.com",
            score=0.0,
            source_family="social",
            content_hint="unknown",
            title_snippet="",
            contradiction_value=0.0,
            enqueued_at_cycle=0,
            lane_id="falsification",
        )
        assert bc.url == "https://x.com"
        assert bc.enqueued_at_cycle == 0


# ---------------------------------------------------------------------------
# Test 3: to_dict wire-shape parity
# ---------------------------------------------------------------------------

class TestWireShapeParity:
    def test_admission_result_to_dict_parity(self):
        """shadow msgspec Struct → dict must match dataclass baseline → dict."""
        ar_s = AdmissionResultShadow(
            status="admit",
            score=0.75,
            content_hint="html",
            source_family="web",
            reason="score=0.75,family=1,coverage=0.50",
        )
        ar_b = AdmissionResultBaseline(
            status="admit",
            score=0.75,
            content_hint="html",
            source_family="web",
            reason="score=0.75,family=1,coverage=0.50",
        )
        d_shadow = admission_to_dict(ar_s)
        d_baseline = admission_baseline_to_dict(ar_b)
        assert d_shadow == d_baseline
        # Explicit field checks
        assert d_shadow["status"] == "admit"
        assert d_shadow["score"] == 0.75
        assert d_shadow["content_hint"] == "html"
        assert d_shadow["source_family"] == "web"
        assert "reason" in d_shadow

    def test_backlog_candidate_to_dict_parity(self):
        bc_s = BacklogCandidateShadow(
            url="https://example.com",
            score=0.5,
            source_family="web",
            content_hint="html",
            title_snippet="Example Title",
            contradiction_value=0.1,
            enqueued_at_cycle=3,
            lane_id="expansion",
        )
        bc_b = BacklogCandidateBaseline(
            url="https://example.com",
            score=0.5,
            source_family="web",
            content_hint="html",
            title_snippet="Example Title",
            contradiction_value=0.1,
            enqueued_at_cycle=3,
            lane_id="expansion",
        )
        d_shadow = backlog_to_dict(bc_s)
        d_baseline = backlog_baseline_to_dict(bc_b)
        assert d_shadow == d_baseline
        # All 8 fields present
        assert set(d_shadow.keys()) == {
            "url", "score", "source_family", "content_hint",
            "title_snippet", "contradiction_value", "enqueued_at_cycle", "lane_id",
        }


# ---------------------------------------------------------------------------
# Test 4: Frozen immutability
# ---------------------------------------------------------------------------

class TestFrozenImmutability:
    def test_admission_result_frozen(self):
        ar = AdmissionResultShadow(
            status="admit",
            score=0.75,
            content_hint="html",
            source_family="web",
            reason="test",
        )
        with pytest.raises(AttributeError):
            ar.status = "reject"

    def test_backlog_candidate_frozen(self):
        bc = BacklogCandidateShadow(
            url="https://x.com",
            score=0.5,
            source_family="social",
            content_hint="unknown",
            title_snippet="x",
            contradiction_value=0.0,
            enqueued_at_cycle=0,
            lane_id="x",
        )
        with pytest.raises(AttributeError):
            bc.score = 0.99


# ---------------------------------------------------------------------------
# Test 5: from_live adapter
# ---------------------------------------------------------------------------

class TestFromLiveAdapter:
    def test_admission_from_live(self):
        # Simulate live AdmissionResult (dataclass with same fields)
        @dataclasses.dataclass(slots=True)
        class LiveAR:
            status: str
            score: float
            content_hint: str
            source_family: str
            reason: str

        live = LiveAR(status="hold", score=0.4, content_hint="pdf",
                       source_family="academic", reason="low_score")
        shadow = admission_from_live(live)
        assert shadow.status == "hold"
        assert shadow.score == 0.4
        assert shadow.content_hint == "pdf"
        assert shadow.source_family == "academic"
        assert shadow.reason == "low_score"

    def test_backlog_from_live(self):
        @dataclasses.dataclass(slots=True)
        class LiveBC:
            url: str
            score: float
            source_family: str
            content_hint: str
            title_snippet: str
            contradiction_value: float
            enqueued_at_cycle: int
            lane_id: str

        live = LiveBC(
            url="https://kernel.org/MAINTAINERS",
            score=0.8, source_family="academic", content_hint="txt",
            title_snippet="Linux Kernel Maintainers",
            contradiction_value=0.0, enqueued_at_cycle=10, lane_id="winner_deepening",
        )
        shadow = backlog_from_live(live)
        assert shadow.url == "https://kernel.org/MAINTAINERS"
        assert shadow.score == 0.8
        assert shadow.lane_id == "winner_deepening"


# ---------------------------------------------------------------------------
# Test 6: Cold import regression
# ---------------------------------------------------------------------------

class TestColdImportRegression:
    def test_shadow_module_does_not_regress_boot(self):
        """
        shadow_dtos.py must NOT add any significant import overhead
        to the hledac.universal boot path.

        We verify the module imports without triggering the hledac boot chain
        by importing it as a top-level utility (outside the hledac package path).
        The shadow_dtos module only brings in msgspec + dataclasses — no hledac deps.
        """
        import os
        # Get the absolute path to shadow_dtos
        shadow_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "utils", "shadow_dtos.py")
        )
        # Use the same python that runs pytest, with correct sys.path
        code = (
            f"import sys; sys.path.insert(0, '{os.path.dirname(shadow_path)}'); "
            f"import time; "
            f"t=time.perf_counter(); "
            f"import shadow_dtos; "
            f"print(f'{{time.perf_counter()-t:.6f}}')"
        )
        vals = []
        for _ in range(3):
            r = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, check=True,
            )
            import re as _re
            floats = _re.findall(r'[\d.]+$', r.stdout.strip())
            vals.append(float(floats[-1]))
        median_ms = statistics.median(vals) * 1000
        # Shadow module import (msgspec + dataclasses only) must be < 30ms
        assert median_ms < 30, f"shadow_dtos import too slow: {median_ms:.1f}ms"


# ---------------------------------------------------------------------------
# Test 7: Benchmark sanity
# ---------------------------------------------------------------------------

class TestBenchmarkSanity:
    def test_msgspec_faster_than_baseline(self):
        """
        msgspec.Struct construction and serialization must be faster
        than dataclass baseline. This is the core hypothesis of the pilot.
        """
        results = run_benchmark()

        # Constructor speedup must be > 1.0 (msgspec faster)
        assert results["constructor_speedup"] > 1.0, (
            f"Constructor speedup {results['constructor_speedup']:.2f}x — msgspec must be faster"
        )

        # to_dict speedup must be > 1.0 (msgspec faster)
        assert results["to_dict_speedup"] > 1.0, (
            f"to_dict speedup {results['to_dict_speedup']:.2f}x — msgspec must be faster"
        )

    def test_benchmark_output_fields(self):
        """Benchmark must return all expected fields."""
        results = run_benchmark()
        dict_fields = [
            "constructor_msgspec", "constructor_baseline",
            "to_dict_msgspec", "to_dict_baseline",
        ]
        float_fields = ["constructor_speedup", "to_dict_speedup"]
        for field in dict_fields:
            assert field in results, f"Missing benchmark field: {field}"
            assert isinstance(results[field], dict), f"{field} must be a dict"
            assert "ns_op" in results[field], f"{field} missing ns_op"
        for field in float_fields:
            assert field in results, f"Missing benchmark field: {field}"
            assert isinstance(results[field], float), f"{field} must be a float"
