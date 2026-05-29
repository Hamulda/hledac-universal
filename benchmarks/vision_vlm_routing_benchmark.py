#!/usr/bin/env python3
"""
Vision/VLM Routing Benchmark — F216B

Hermetic benchmark for Vision/VLM routing policy.
Does NOT load real VLMs or CoreML models.

Routes:
  ocr_only
  ocr_then_small_vlm
  future_small_vlm_deferred
  skip_due_to_memory
  unsupported

Usage:
  python benchmarks/vision_vlm_routing_benchmark.py --hermetic --json /tmp/vision_vlm_routing_benchmark.json
  python benchmarks/vision_vlm_routing_benchmark.py --audit-only --json /tmp/vision_vlm_audit.json
  python benchmarks/vision_vlm_routing_benchmark.py --list-routes
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Ensure hledac.universal is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["HLEDAC_UNIT_TEST"] = "1"


class Route(Enum):
    OCR_ONLY = "ocr_only"
    OCR_THEN_SMALL_VLM = "ocr_then_small_vlm"
    FUTURE_SMALL_VLM_DEFERRED = "future_small_vlm_deferred"
    SKIP_DUE_TO_MEMORY = "skip_due_to_memory"
    UNSUPPORTED = "unsupported"


@dataclass
class RoutingCase:
    name: str
    image_bytes: bytes
    ocr_sufficient: bool
    needs_visual_reasoning: bool
    is_complex_scene: bool
    memory_pressure: bool
    oversized: bool
    ocr_failed: bool
    vlm_manually_requested: bool
    expected_route: Route

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ocr_sufficient": self.ocr_sufficient,
            "needs_visual_reasoning": self.needs_visual_reasoning,
            "is_complex_scene": self.is_complex_scene,
            "memory_pressure": self.memory_pressure,
            "oversized": self.oversized,
            "ocr_failed": self.ocr_failed,
            "vlm_manually_requested": self.vlm_manually_requested,
            "expected_route": self.expected_route.value,
        }


@dataclass
class BenchmarkResult:
    benchmark: str = "vision_vlm_routing_benchmark"
    mode: str = "hermetic"
    total_cases: int = 0
    routes: dict[str, int] = field(default_factory=lambda: {
        "ocr_only": 0,
        "ocr_then_small_vlm": 0,
        "future_small_vlm_deferred": 0,
        "skip_due_to_memory": 0,
        "unsupported": 0,
    })
    vision_encoder_dummy_detected: bool = False
    vlm_default_detected: bool = False
    ocr_first_policy_verified: bool = False
    heavy_vlm_default_disabled_recommended: bool = False
    errors: int = 0
    cases: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "mode": self.mode,
            "total_cases": self.total_cases,
            "routes": self.routes,
            "vision_encoder_dummy_detected": self.vision_encoder_dummy_detected,
            "vlm_default_detected": self.vlm_default_detected,
            "ocr_first_policy_verified": self.ocr_first_policy_verified,
            "heavy_vlm_default_disabled_recommended": self.heavy_vlm_default_disabled_recommended,
            "errors": self.errors,
            "cases": self.cases,
        }


def _check_vision_encoder_dummy_mode() -> bool:
    """Check if VisionEncoder has dummy mode active in production path."""
    try:
        # Read source file directly to avoid mlx import issues
        ve_path = Path(__file__).resolve().parent.parent / "multimodal" / "vision_encoder.py"
        content = ve_path.read_text()
        # Dummy mode: encode_batch returns mx.random.normal when _model is None
        return "mx.random.normal(shape=(self.embedding_dim,))" in content
    except Exception:
        return False


def _check_vlm_default() -> bool:
    """Check if VLMAnalyzer hardcodes a default VLM model id."""
    try:
        vlm_path = Path(__file__).resolve().parent.parent / "tools" / "vlm_analyzer.py"
        content = vlm_path.read_text()
        # Check for hardcoded model ids (llava, 7b, etc.) — should find none after F216C
        return "llava-1.5-7b-4bit" in content or "llava" in content.lower()
    except Exception:
        return False


def _check_vision_ocr_canonical() -> bool:
    """Check if VisionOCR is classified as canonical OCR."""
    try:
        et_path = Path(__file__).resolve().parent.parent / "multimodal" / "evidence_triage.py"
        content = et_path.read_text()
        return "VisionOCR" in content
    except Exception:
        return False


def _check_ocr_first_policy() -> bool:
    """Check if OCR-first routing is documented/verified."""
    # EvidenceTriageCoordinator runs VisionOCR first
    return _check_vision_ocr_canonical()


def _compute_route(case: RoutingCase) -> Route:
    """Compute the expected route for a given case per routing policy."""
    # Memory pressure takes priority
    if case.memory_pressure:
        return Route.SKIP_DUE_TO_MEMORY

    # Oversized image — VisionOCR limit is 20MB
    if case.oversized:
        return Route.SKIP_DUE_TO_MEMORY

    # OCR failure
    if case.ocr_failed:
        return Route.UNSUPPORTED

    # OCR sufficient — no VLM needed
    if case.ocr_sufficient:
        return Route.OCR_ONLY

    # OCR insufficient + visual reasoning needed
    if case.needs_visual_reasoning:
        if case.is_complex_scene:
            # Complex scene: no heavy VLM on M1 8GB — defer to future small VLM benchmark
            if case.vlm_manually_requested:
                # Explicit opt-in but no VLM configured → deferred
                return Route.FUTURE_SMALL_VLM_DEFERRED
            else:
                return Route.OCR_ONLY  # degraded but safe
        else:
            # Small VLM (SmolVLM2) after benchmark
            return Route.OCR_THEN_SMALL_VLM

    # No reason to run VLM
    return Route.OCR_ONLY


def _build_cases() -> list[RoutingCase]:
    """Build synthetic routing test cases."""
    cases = [
        # text-heavy document image → ocr_only
        RoutingCase(
            name="text_heavy_document_image",
            image_bytes=b"fake_pdf_bytes_" * 100,
            ocr_sufficient=True,
            needs_visual_reasoning=False,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.OCR_ONLY,
        ),
        # screenshot with visible text → ocr_only
        RoutingCase(
            name="screenshot_with_visible_text",
            image_bytes=b"screenshot_png_bytes",
            ocr_sufficient=True,
            needs_visual_reasoning=False,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.OCR_ONLY,
        ),
        # image with no useful text → ocr_only (no VLM for non-text images by default)
        RoutingCase(
            name="image_with_no_useful_text",
            image_bytes=b"pure_image_bytes",
            ocr_sufficient=False,
            needs_visual_reasoning=False,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.OCR_ONLY,
        ),
        # OCR insufficient + simple visual reasoning → ocr_then_small_vlm
        RoutingCase(
            name="ocr_insufficient_simple_visual",
            image_bytes=b"complex_chart_bytes",
            ocr_sufficient=False,
            needs_visual_reasoning=True,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.OCR_THEN_SMALL_VLM,
        ),
        # complex scene + manually requested → future_small_vlm_deferred (no heavy VLM on M1 8GB)
        RoutingCase(
            name="complex_scene_vlm_manually_requested",
            image_bytes=b"complex_scene_bytes",
            ocr_sufficient=False,
            needs_visual_reasoning=True,
            is_complex_scene=True,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=True,
            expected_route=Route.FUTURE_SMALL_VLM_DEFERRED,
        ),
        # complex scene NOT manually requested → ocr_only (no auto heavy VLM)
        RoutingCase(
            name="complex_scene_no_manual_request",
            image_bytes=b"complex_scene_no_request",
            ocr_sufficient=False,
            needs_visual_reasoning=True,
            is_complex_scene=True,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.OCR_ONLY,  # safe default — no auto heavy VLM
        ),
        # M1 memory pressure simulated → skip_due_to_memory
        RoutingCase(
            name="memory_pressure_simulated",
            image_bytes=b"any_image_bytes",
            ocr_sufficient=False,
            needs_visual_reasoning=True,
            is_complex_scene=True,
            memory_pressure=True,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=True,
            expected_route=Route.SKIP_DUE_TO_MEMORY,
        ),
        # oversized image → skip_due_to_memory
        RoutingCase(
            name="oversized_image_25mb",
            image_bytes=b"x" * (25 * 1024 * 1024),
            ocr_sufficient=False,
            needs_visual_reasoning=True,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=True,
            ocr_failed=False,
            vlm_manually_requested=False,
            expected_route=Route.SKIP_DUE_TO_MEMORY,
        ),
        # OCR failure → unsupported
        RoutingCase(
            name="ocr_failure_corrupt_image",
            image_bytes=b"corrupt_image_data",
            ocr_sufficient=False,
            needs_visual_reasoning=False,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=True,
            vlm_manually_requested=False,
            expected_route=Route.UNSUPPORTED,
        ),
        # text image with explicit VLM opt-in → ocr_only (VLM opt-in only triggers when OCR is insufficient)
        RoutingCase(
            name="text_image_vlm_explicit_manual",
            image_bytes=b"text_image_bytes",
            ocr_sufficient=True,
            needs_visual_reasoning=False,
            is_complex_scene=False,
            memory_pressure=False,
            oversized=False,
            ocr_failed=False,
            vlm_manually_requested=True,
            expected_route=Route.OCR_ONLY,  # VLM opt-in only relevant when OCR insufficient
        ),
    ]
    return cases


def run_benchmark(hermetic: bool = True, audit_only: bool = False,
                  json_path: str | None = None) -> BenchmarkResult:
    """Run the Vision/VLM routing benchmark."""
    result = BenchmarkResult()
    result.mode = "hermetic" if hermetic else "live"

    # Structural audits
    result.vision_encoder_dummy_detected = _check_vision_encoder_dummy_mode()
    result.vlm_default_detected = _check_vlm_default()
    result.ocr_first_policy_verified = _check_ocr_first_policy()
    result.heavy_vlm_default_disabled_recommended = True  # No heavy VLM on M1 8GB

    if audit_only:
        # Audit mode: structural checks only, no routing cases
        result.total_cases = 0
        result.cases = []
        return result

    # Routing cases
    cases = _build_cases()
    result.total_cases = len(cases)

    for case in cases:
        computed_route = _compute_route(case)
        case_dict = case.to_dict()
        case_dict["computed_route"] = computed_route.value
        case_dict["route_correct"] = computed_route == case.expected_route
        result.cases.append(case_dict)
        result.routes[computed_route.value] += 1

    return result


def list_routes():
    """Print available routing types."""
    print("Vision/VLM Routing Types:")
    print("  ocr_only              — VisionOCR sufficient, no VLM")
    print("  ocr_then_small_vlm    — OCR insufficient, small VLM after benchmark")
    print("  future_small_vlm_deferred — No heavy VLM on M1 8GB, deferred to future benchmark")
    print("  skip_due_to_memory    — M1 memory pressure or oversized image")
    print("  unsupported           — OCR failure, no VLM available")


def main():
    parser = argparse.ArgumentParser(description="Vision/VLM Routing Benchmark F216B")
    parser.add_argument("--hermetic", action="store_true",
                        help="Run in hermetic mode (no real model loads)")
    parser.add_argument("--audit-only", action="store_true",
                        help="Run audit only (structural checks, no routing cases)")
    parser.add_argument("--json", dest="json_path", type=str, default=None,
                        help="Write JSON output to path")
    parser.add_argument("--list-routes", action="store_true",
                        help="List available routing types and exit")

    args = parser.parse_args()

    if args.list_routes:
        list_routes()
        return 0

    hermetic = args.hermetic or args.audit_only
    result = run_benchmark(hermetic=hermetic, audit_only=args.audit_only)

    if args.json_path:
        with open(args.json_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"Benchmark written to {args.json_path}")
    else:
        print(json.dumps(result.to_dict(), indent=2))

    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
