"""
Shadow msgspec twins for AdmissionResult and BacklogCandidate.

SHADOW-ONLY PILOT (Sprint 8AQ) — NOT part of production boot path.
This module is NEVER imported by autonomous_orchestrator.py or any live gate.

Purpose: benchmark msgspec.Struct vs dataclass parity, measure construction
and serialization cost, without touching live DTO definitions.

Live DTO source: autonomous_orchestrator.py lines 285-306
- AdmissionResult: lines 285-292
- BacklogCandidate: lines 295-306
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Literal

import msgspec

# ---------------------------------------------------------------------------
# Shadow twins
# ---------------------------------------------------------------------------

class AdmissionResultShadow(msgspec.Struct, frozen=True, gc=False):
    """
    Shadow of autonomous_orchestrator.AdmissionResult.

    Fields (mirrored exactly):
      status       : Literal["reject", "hold", "admit"]
      score        : float  (0.0-1.0)
      content_hint : str    ("html"/"pdf"/"image"/"unknown")
      source_family: str
      reason       : str    (short, human-readable)
    """
    status: Literal["reject", "hold", "admit"]
    score: float
    content_hint: str
    source_family: str
    reason: str


class BacklogCandidateShadow(msgspec.Struct, frozen=True, gc=False):
    """
    Shadow of autonomous_orchestrator.BacklogCandidate.

    Fields (mirrored exactly):
      url              : str
      score            : float
      source_family    : str
      content_hint     : str
      title_snippet    : str   (~150 chars max)
      contradiction_value: float
      enqueued_at_cycle: int
      lane_id          : str
    """
    url: str
    score: float
    source_family: str
    content_hint: str
    title_snippet: str
    contradiction_value: float
    enqueued_at_cycle: int
    lane_id: str


# ---------------------------------------------------------------------------
# Baseline dataclass clones (mirroring live DTO shape exactly)
# These are used ONLY inside this module for fair benchmark comparison
# ---------------------------------------------------------------------------

@dataclasses.dataclass(slots=True)
class AdmissionResultBaseline:
    """Dataclass baseline clone of AdmissionResult for shadow benchmark."""
    status: Literal["reject", "hold", "admit"]
    score: float
    content_hint: str
    source_family: str
    reason: str


@dataclasses.dataclass(slots=True)
class BacklogCandidateBaseline:
    """Dataclass baseline clone of BacklogCandidate for shadow benchmark."""
    url: str
    score: float
    source_family: str
    content_hint: str
    title_snippet: str
    contradiction_value: float
    enqueued_at_cycle: int
    lane_id: str


# ---------------------------------------------------------------------------
# Adapters (shadow <-> live)
# ---------------------------------------------------------------------------

def admission_from_live(live: Any) -> AdmissionResultShadow:
    """
    Convert a live AdmissionResult dataclass instance to AdmissionResultShadow.

    Works with any object that has the same 5 attributes.
    """
    return AdmissionResultShadow(
        status=live.status,
        score=live.score,
        content_hint=live.content_hint,
        source_family=live.source_family,
        reason=live.reason,
    )


def backlog_from_live(live: Any) -> BacklogCandidateShadow:
    """
    Convert a live BacklogCandidate dataclass instance to BacklogCandidateShadow.

    Works with any object that has the same 8 attributes.
    """
    return BacklogCandidateShadow(
        url=live.url,
        score=live.score,
        source_family=live.source_family,
        content_hint=live.content_hint,
        title_snippet=live.title_snippet,
        contradiction_value=live.contradiction_value,
        enqueued_at_cycle=live.enqueued_at_cycle,
        lane_id=live.lane_id,
    )


def admission_to_dict(shadow: AdmissionResultShadow) -> dict[str, Any]:
    """Dict representation of AdmissionResultShadow (for parity testing)."""
    return msgspec.structs.asdict(shadow)


def backlog_to_dict(shadow: BacklogCandidateShadow) -> dict[str, Any]:
    """Dict representation of BacklogCandidateShadow (for parity testing)."""
    return msgspec.structs.asdict(shadow)


def admission_baseline_to_dict(baseline: AdmissionResultBaseline) -> dict[str, Any]:
    """Dataclass asdict for baseline parity testing."""
    return dataclasses.asdict(baseline)


def backlog_baseline_to_dict(baseline: BacklogCandidateBaseline) -> dict[str, Any]:
    """Dataclass asdict for baseline parity testing."""
    return dataclasses.asdict(baseline)


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

_N = 100_000


def bench_constructor_msgspec() -> dict[str, float]:
    """Construction cost: msgspec.Struct shadow twins."""
    t0 = time.perf_counter()
    for _ in range(_N):
        AdmissionResultShadow(
            status="admit", score=0.75, content_hint="html",
            source_family="web", reason="score=0.75,family=1,coverage=0.50",
        )
        BacklogCandidateShadow(
            url="https://example.com/article",
            score=0.75, source_family="web", content_hint="html",
            title_snippet="Example Article About Things",
            contradiction_value=0.1, enqueued_at_cycle=5, lane_id="expansion",
        )
    elapsed = time.perf_counter() - t0
    total = 2 * _N  # two objects per iteration
    return {"total_s": elapsed, "ns_op": elapsed / total * 1e9, "ops": total}


def bench_constructor_baseline() -> dict[str, float]:
    """Construction cost: dataclass baseline clones."""
    t0 = time.perf_counter()
    for _ in range(_N):
        AdmissionResultBaseline(
            status="admit", score=0.75, content_hint="html",
            source_family="web", reason="score=0.75,family=1,coverage=0.50",
        )
        BacklogCandidateBaseline(
            url="https://example.com/article",
            score=0.75, source_family="web", content_hint="html",
            title_snippet="Example Article About Things",
            contradiction_value=0.1, enqueued_at_cycle=5, lane_id="expansion",
        )
    elapsed = time.perf_counter() - t0
    total = 2 * _N
    return {"total_s": elapsed, "ns_op": elapsed / total * 1e9, "ops": total}


def bench_to_dict_msgspec() -> dict[str, float]:
    """to_dict cost: msgspec.Struct."""
    ar = AdmissionResultShadow(
        status="admit", score=0.75, content_hint="html",
        source_family="web", reason="score=0.75,family=1,coverage=0.50",
    )
    bc = BacklogCandidateShadow(
        url="https://example.com/article",
        score=0.75, source_family="web", content_hint="html",
        title_snippet="Example Article About Things",
        contradiction_value=0.1, enqueued_at_cycle=5, lane_id="expansion",
    )
    t0 = time.perf_counter()
    for _ in range(_N):
        msgspec.structs.asdict(ar)
        msgspec.structs.asdict(bc)
    elapsed = time.perf_counter() - t0
    total = 2 * _N
    return {"total_s": elapsed, "ns_op": elapsed / total * 1e9, "ops": total}


def bench_to_dict_baseline() -> dict[str, float]:
    """to_dict cost: dataclass.asdict baseline."""
    ar = AdmissionResultBaseline(
        status="admit", score=0.75, content_hint="html",
        source_family="web", reason="score=0.75,family=1,coverage=0.50",
    )
    bc = BacklogCandidateBaseline(
        url="https://example.com/article",
        score=0.75, source_family="web", content_hint="html",
        title_snippet="Example Article About Things",
        contradiction_value=0.1, enqueued_at_cycle=5, lane_id="expansion",
    )
    t0 = time.perf_counter()
    for _ in range(_N):
        dataclasses.asdict(ar)
        dataclasses.asdict(bc)
    elapsed = time.perf_counter() - t0
    total = 2 * _N
    return {"total_s": elapsed, "ns_op": elapsed / total * 1e9, "ops": total}


def run_benchmark() -> dict[str, Any]:
    """Run full shadow benchmark, return results dict."""
    results: dict[str, Any] = {}

    results["constructor_msgspec"] = bench_constructor_msgspec()
    results["constructor_baseline"] = bench_constructor_baseline()
    results["to_dict_msgspec"] = bench_to_dict_msgspec()
    results["to_dict_baseline"] = bench_to_dict_baseline()

    # Speed ratios
    c_ns = results["constructor_msgspec"]["ns_op"]
    c_bl = results["constructor_baseline"]["ns_op"]
    results["constructor_speedup"] = c_bl / c_ns  # >1 = msgspec faster

    d_ns = results["to_dict_msgspec"]["ns_op"]
    d_bl = results["to_dict_baseline"]["ns_op"]
    results["to_dict_speedup"] = d_bl / d_ns  # >1 = msgspec faster

    return results
