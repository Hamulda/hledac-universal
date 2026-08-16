"""F211C — Strict Live Result Bundle Sanity Checker.

Meta-checker that compares benchmark JSON + validation JSON + trace JSON
and reports disagreements between the three surfaces.






Strict mode: stale trace verdicts and wallclock budget overruns are always reported.

ABSOLUTE REPO ROOT: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
Work only inside repo root.
"""
import argparse
import json
import sys
from dataclasses import dataclass, field
import msgspec
from enum import Enum
from pathlib import Path
from typing import Any
from collections.abc import Callable
from _core import aclose
from compat.msgspec_gc_compat import Struct


class SanityVerdict(Enum):
    SANITY_PASS = 'SANITY_PASS'
    SANITY_FAIL_SURFACE_DISAGREEMENT = 'SANITY_FAIL_SURFACE_DISAGREEMENT'
    SANITY_FAIL_STALE_TERMINALITY = 'SANITY_FAIL_STALE_TERMINALITY'
    SANITY_FAIL_WALLCLOCK_BUDGET = 'SANITY_FAIL_WALLCLOCK_BUDGET'
    SANITY_FAIL_BENCHMARK_SHAPE_GAP = 'SANITY_FAIL_BENCHMARK_SHAPE_GAP'
    SANITY_FAIL_RESEARCH_QUALITY = 'SANITY_FAIL_RESEARCH_QUALITY'

class BenchmarkSurface(Struct):
    """Parsed benchmark surface."""
    run_quality_verdict: str | None = None
    live_kpi: dict[str, Any] | None = None
    acquisition_report: dict[str, Any] | None = None
    runtime_truth: dict[str, Any] | None = None
    branch_mix: dict[str, int] | None = None
    actual_duration_s: float | None = None
    planned_duration_s: float | None = None
    public_terminal_state: str | None = None
    ct_terminal_state: str | None = None
    comparable_result: bool | None = None
    swap_gate_triggered: bool | None = None
    research_quality: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

class ValidatorSurface(Struct, frozen=True):
    """Parsed validator surface."""
    live_kpi: dict[str, Any] | None = None
    acquisition_report: dict[str, Any] | None = None
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    source_family_outcomes: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

class TraceSurface(Struct, frozen=True):
    """Parsed trace surface."""
    verdict: str | None = None
    stage: str | None = None
    detail: str | None = None
    extended: dict[str, Any] = field(default_factory=dict)
    terminality_satisfied: bool | None = None
    raw_benchmark: dict[str, Any] | None = None
    raw_internal: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

class QualitySurface(Struct, frozen=True):
    """Parsed research quality surface."""
    quality_gate: str | None = None
    grade: str | None = None
    total_quality_score: float | None = None
    research_quality_comparable: bool | None = None
    claims_depth: float | None = None
    public_candidate_depth: float | None = None
    ct_clue_depth: float | None = None
    advisory_clue_depth: float | None = None
    claims_extracted: bool | None = None
    public_candidates_seen: bool | None = None
    ct_clues_present: bool | None = None
    advisory_clues_present: bool | None = None
    nonfeed_clues_without_acceptance: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

# Type aliases
_CheckFn = Callable[..., tuple[bool, str | None]]


# Check specification with runtime args extractor
@dataclass(frozen=True, slots=True)
class _CheckSpec:
    """Single check specification for the dispatch table."""
    name: str
    fn: _CheckFn
    runtime_args: Callable[["SanityParams"], tuple[Any, ...]]

# Simple parameters container (alternative to dataclass for clarity)
@dataclass(frozen=True, slots=True)
class SanityParams:
    """Runtime parameters for sanity_check."""
    benchmark_path: str | Path | None = None
    validation_path: str | Path | None = None
    trace_path: str | Path | None = None
    benchmark_raw: dict[str, Any] | None = None
    validator_raw: dict[str, Any] | None = None
    trace_raw: dict[str, Any] | None = None
    allow_stale_trace: bool = False
    quality_path: str | Path | None = None
    quality_raw: dict[str, Any] | None = None
    min_quality_grade: str | None = None
    allow_feed_only: bool = False

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> "SanityParams":
        """Create from sanity_check kwargs."""
        return cls(
            benchmark_path=kwargs.get('benchmark_path'),
            validation_path=kwargs.get('validation_path'),
            trace_path=kwargs.get('trace_path'),
            benchmark_raw=kwargs.get('benchmark_raw'),
            validator_raw=kwargs.get('validator_raw'),
            trace_raw=kwargs.get('trace_raw'),
            allow_stale_trace=kwargs.get('allow_stale_trace', False),
            quality_path=kwargs.get('quality_path'),
            quality_raw=kwargs.get('quality_raw'),
            min_quality_grade=kwargs.get('min_quality_grade'),
            allow_feed_only=kwargs.get('allow_feed_only', False),
    )


class SanityResult(Struct, frozen=True):
    verdict: SanityVerdict = SanityVerdict.SANITY_PASS
    checks: dict[str, bool] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    benchmark: BenchmarkSurface = field(default_factory=BenchmarkSurface)
    validator: ValidatorSurface = field(default_factory=ValidatorSurface)
    trace: TraceSurface = field(default_factory=TraceSurface)
    quality_surface: QualitySurface = field(default_factory=QualitySurface)

    def to_dict(self) -> dict[str, Any]:
        return {'verdict': self.verdict.value, 'checks': self.checks, 'disagreements': self.disagreements, 'benchmark': {'run_quality_verdict': self.benchmark.run_quality_verdict, 'branch_mix': self.benchmark.branch_mix, 'actual_duration_s': self.benchmark.actual_duration_s, 'planned_duration_s': self.benchmark.planned_duration_s, 'public_terminal_state': self.benchmark.public_terminal_state, 'ct_terminal_state': self.benchmark.ct_terminal_state}, 'validator': {'acquisition_terminality_checked': self.validator.acquisition_terminality_checked, 'acquisition_terminality_satisfied': self.validator.acquisition_terminality_satisfied, 'source_family_outcomes': self.validator.source_family_outcomes}, 'trace': {'verdict': self.trace.verdict, 'stage': self.trace.stage, 'detail': self.trace.detail, 'terminality_satisfied': self.trace.terminality_satisfied}, 'quality': {'quality_gate': self.quality_surface.quality_gate, 'grade': self.quality_surface.grade, 'total_quality_score': self.quality_surface.total_quality_score, 'research_quality_comparable': self.quality_surface.research_quality_comparable}}

    def _format_surface(self, title: str, items: list[tuple[str, Any]]) -> list[str]:
        """Format a surface section with conditional key-value pairs."""
        lines = [f'## {title}', '']
        for key, value in items:
            if value is not None:
                if isinstance(value, float):
                    lines.append(f'- {key}: {value:.1f}')
                elif isinstance(value, dict) and key == 'branch_mix':
                    lines.append(f'- {key}: {value}')
                elif isinstance(value, str):
                    lines.append(f'- {key}: `{value}`')
                else:
                    lines.append(f'- {key}: {value}')
        return lines

    def to_md(self) -> str:
        lines = ['# Live Result Bundle Sanity Report', '',
                 f'**Verdict**: `{self.verdict.value}`', '',
                 '## Checks', '']
        lines += [f'- [{"PASS" if p else "FAIL"}] `{n}`' for n, p in self.checks.items()]

        if self.disagreements:
            lines += ['', '## Disagreements', ''] + [f'- {d}' for d in self.disagreements]

        lines += self._format_surface('Benchmark Surface', [
            ('verdict', self.benchmark.run_quality_verdict),
            ('branch_mix', self.benchmark.branch_mix),
        ])
        lines += self._format_surface('Validator Surface', [
            ('terminality checked', self.validator.acquisition_terminality_checked),
            ('terminality satisfied', self.validator.acquisition_terminality_satisfied),
            ('source_family_outcomes', self.validator.source_family_outcomes),
        ])
        lines += self._format_surface('Trace Surface', [
            ('verdict', self.trace.verdict),
            ('detail', self.trace.detail),
            ('terminality_satisfied', self.trace.terminality_satisfied),
        ])

        if self.quality_surface.quality_gate is not None:
            lines += self._format_surface('Research Quality', [
                ('quality_gate', self.quality_surface.quality_gate),
                ('grade', self.quality_surface.grade),
                ('score', self.quality_surface.total_quality_score),
                ('comparable', self.quality_surface.research_quality_comparable),
            ])

        return '\n'.join(lines)
_TERMINALITY_UNSATISFIED_VERDICTS = frozenset({'FAIL_TERMINALITY_UNSATISFIED', 'FAIL_TERMINALITY_NOT_CHECKED', 'FAIL_MISSING_SOURCE_OUTCOMES', 'FAIL_SCHEDULER_EXIT_MISSING'})
_NONFEED_EVIDENCE_MISSING_VERDICTS = frozenset({'FAIL_NONFEED_EVIDENCE_MISSING'})
_TRACE_STALE_VERDICTS = frozenset({'TRACE_TERMINALITY_STALE_BEFORE_NONFEED', 'TRACE_TERMINALITY_STALE_SNAPSHOT', 'TRACE_DIRECT_PRE_RETURN_BARRIER_MISSING', 'TRACE_TERMINALITY_UNSATISFIED', 'TRACE_DROP_BEFORE_EXPORT', 'TRACE_DROP_AT_BENCHMARK_PARSE', 'TRACE_DROP_AT_EXPORT', 'TRACE_VALIDATOR_ALIAS_ONLY'})

def parse_benchmark(raw: dict[str, Any]) -> BenchmarkSurface:
    surf = BenchmarkSurface(raw=raw)
    surf.run_quality_verdict = raw.get('run_quality_verdict') or raw.get('live_run_status')
    surf.live_kpi = raw.get('live_kpi') or raw.get('live_kpi_snapshot')
    surf.acquisition_report = raw.get('acquisition_report')
    if not surf.acquisition_report and surf.live_kpi:
        surf.acquisition_report = surf.live_kpi.get('acquisition_report')
    surf.runtime_truth = raw.get('runtime_truth')
    surf.branch_mix = raw.get('branch_mix') or (surf.runtime_truth or {}).get('branch_mix')
    surf.actual_duration_s = raw.get('actual_duration_s')
    surf.planned_duration_s = raw.get('planned_duration_s')
    surf.public_terminal_state = raw.get('public_terminal_state')
    surf.ct_terminal_state = raw.get('ct_terminal_state')
    surf.comparable_result = raw.get('comparable_result')
    surf.swap_gate_triggered = raw.get('swap_gate_triggered')
    _lk = surf.live_kpi
    if isinstance(_lk, dict) and isinstance(_lk.get('research_quality'), dict):
        surf.research_quality = _lk['research_quality']
    return surf

def parse_validator(raw: dict[str, Any]) -> ValidatorSurface:
    surf = ValidatorSurface(raw=raw)
    surf.live_kpi = raw.get('live_kpi') or raw.get('live_kpi_snapshot')
    surf.acquisition_report = raw.get('acquisition_report')
    if not surf.acquisition_report and surf.live_kpi:
        surf.acquisition_report = surf.live_kpi.get('acquisition_report')
    if surf.live_kpi:
        surf.acquisition_terminality_checked = surf.live_kpi.get('acquisition_terminality_checked')
        surf.acquisition_terminality_satisfied = surf.live_kpi.get('acquisition_terminality_satisfied')
        surf.source_family_outcomes = surf.live_kpi.get('source_family_outcomes')
    return surf

def parse_trace(raw: dict[str, Any]) -> TraceSurface:
    surf = TraceSurface(raw=raw)
    surf.verdict = raw.get('verdict')
    surf.stage = raw.get('stage')
    surf.detail = raw.get('detail')
    extended = raw.get('extended') or {}
    surf.extended = extended
    surf.terminality_satisfied = extended.get('terminality_satisfied')
    surf.raw_benchmark = raw.get('raw_benchmark')
    surf.raw_internal = raw.get('raw_internal')
    return surf

def parse_quality(raw: dict[str, Any]) -> QualitySurface:
    surf = QualitySurface(raw=raw)
    surf.quality_gate = raw.get('quality_gate')
    surf.grade = raw.get('grade')
    surf.total_quality_score = raw.get('total_quality_score')
    surf.research_quality_comparable = raw.get('research_quality_comparable')
    if surf.research_quality_comparable is None:
        surf.research_quality_comparable = raw.get('research_quality_comparabl')
    ed = raw.get('evidence_depth', {})
    if isinstance(ed, dict):
        surf.claims_depth = ed.get('claims_depth')
        surf.public_candidate_depth = ed.get('public_candidate_depth')
        surf.ct_clue_depth = ed.get('ct_clue_depth')
        surf.advisory_clue_depth = ed.get('advisory_clue_depth')
        surf.claims_extracted = ed.get('claims_extracted')
        surf.public_candidates_seen = ed.get('public_candidates_seen')
        surf.ct_clues_present = ed.get('ct_clues_present')
        surf.advisory_clues_present = ed.get('advisory_clues_present')
        surf.nonfeed_clues_without_acceptance = ed.get('nonfeed_clues_without_acceptance')
    return surf

def parse_quality_with_fallback(raw: dict[str, Any], fallback: dict[str, Any]) -> QualitySurface:
    """
    F215A: Parse quality surface, falling back to embedded research_quality from
    benchmark live_kpi when no explicit quality_json is provided.

    Priority:
    1. raw has quality_gate → use it
    2. raw is empty + fallback has quality_gate → use fallback (embedded in benchmark)
    3. raw is empty + fallback empty → N/A (quality_gate=None, no gate applied)
    4. raw non-empty but quality_gate missing → malformed (quality_gate=None → fail)
    """
    if raw.get('quality_gate') is not None:
        return parse_quality(raw)
    if not raw:
        if isinstance(fallback, dict) and fallback.get('quality_gate') is not None:
            return parse_quality(fallback)
        return QualitySurface(raw=raw)
    return parse_quality(raw)

def _get_sfo_canonical(b: BenchmarkSurface, v: ValidatorSurface) -> list[dict[str, Any]]:
    """
    F221E: Return canonical source_family_outcomes list.

    Priority:
    1. acquisition_report.source_family_outcomes (canonical)
    2. acquisition_report.live_kpi.source_family_outcomes (legacy wrap)
    3. live_kpi.source_family_outcomes (live_kpi direct)

    Returns [] if none available.
    """
    ar = b.acquisition_report
    if ar:
        sfo = ar.get('source_family_outcomes')
        if isinstance(sfo, list):
            return sfo
        lk = ar.get('live_kpi')
        if isinstance(lk, dict) and isinstance(lk.get('source_family_outcomes'), list):
            return lk['source_family_outcomes']
    ar_v = v.acquisition_report
    if ar_v:
        sfo = ar_v.get('source_family_outcomes')
        if isinstance(sfo, list):
            return sfo
    lk = (b.live_kpi or {}) or (v.live_kpi or {})
    if isinstance(lk, dict) and isinstance(lk.get('source_family_outcomes'), list):
        return lk['source_family_outcomes']
    return []

def _check_benchmark_fail_validator_pass(b: BenchmarkSurface, v: ValidatorSurface) -> tuple[bool, str | None]:
    bench_fail = b.run_quality_verdict in _TERMINALITY_UNSATISFIED_VERDICTS
    val_pass = v.acquisition_terminality_satisfied is True or v.acquisition_terminality_checked is False
    if bench_fail and val_pass:
        return (False, f"Benchmark verdict '{b.run_quality_verdict}' but validator terminality_satisfied={v.acquisition_terminality_satisfied}")
    return (True, None)

def _check_benchmark_missing_source_family_outcomes(b: BenchmarkSurface, t: TraceSurface) -> tuple[bool, str | None]:
    ar = b.acquisition_report
    ar_has = ar is not None and isinstance(ar.get('source_family_outcomes'), list)
    live_kpi = b.live_kpi or {}
    lk_has = isinstance(live_kpi.get('source_family_outcomes'), list)
    bench_missing = not ar_has and (not lk_has)
    trace_has = t.raw_internal is not None and ((t.raw_internal.get('live_kpi') or {}).get('source_family_outcomes') or (t.raw_internal.get('live_kpi_snapshot') or {}).get('source_family_outcomes') or (t.raw_internal.get('acquisition_report') or {}).get('source_family_outcomes'))
    if bench_missing and trace_has:
        return (False, 'Benchmark missing source_family_outcomes but internal trace has them')
    return (True, None)

def _check_stale_terminality(t: TraceSurface, allow_stale_trace: bool) -> tuple[bool, str | None]:
    if allow_stale_trace:
        return (True, None)
    if t.verdict in _TRACE_STALE_VERDICTS:
        return (False, f"Stale trace verdict '{t.verdict}' present without --allow-stale-trace")
    return (True, None)

def _check_wallclock_budget(b: BenchmarkSurface) -> tuple[bool, str | None]:
    if b.actual_duration_s and b.planned_duration_s:
        allowed = max(b.planned_duration_s * 1.1, b.planned_duration_s + 30)
        if b.actual_duration_s > allowed:
            return (False, f'Wallclock budget exceeded: actual={b.actual_duration_s:.1f}s vs allowed={allowed:.1f}s (planned={b.planned_duration_s:.1f}s)')
    return (True, None)

def _check_feed_only_accepted_nonfeed_attempted(b: BenchmarkSurface, v: ValidatorSurface) -> tuple[bool, str | None]:
    """
    F221E: Uses acquisition_report.source_family_outcomes as canonical.
    Falls back to live_kpi.source_family_outcomes.

    CT attempted=True when source_family_outcomes says so OR when ct_provider_status
    or ct_terminal_state indicates a terminal outcome (provider_failure/cooldown/timeout).
    PUBLIC attempted=True when source_family_outcomes says so OR when public_terminal_stage
    is set and not NOT_SCHEDULED.
    """
    outcomes = _get_sfo_canonical(b, v)
    family_attempted: dict[str, bool] = {}
    for o in outcomes:
        fam = o.get('family', '').lower()
        if fam not in family_attempted:
            family_attempted[fam] = bool(o.get('attempted'))
    ct_attempted = family_attempted.get('ct', False)
    public_attempted = family_attempted.get('public', False)
    if not ct_attempted:
        ar = b.acquisition_report
        ct_provider_status = ar.get('ct_provider_status') if ar else None
        ct_terminal_state = ar.get('ct_terminal_state') if ar else None
        if ct_provider_status in ('provider_failure', 'cooldown', 'timeout') or ct_terminal_state in ('provider_failure', 'cooldown', 'timeout'):
            ct_attempted = True
    if not public_attempted:
        ar = b.acquisition_report
        public_terminal_stage = ar.get('public_terminal_stage') if ar else None
        if public_terminal_stage and public_terminal_stage != 'NOT_SCHEDULED':
            public_attempted = True
    branch_mix = b.branch_mix or {}
    feed_only = branch_mix.get('feed', 0) > 0 and branch_mix.get('ct_findings', 0) == 0 and (branch_mix.get('public_findings', 0) == 0)
    if feed_only and (ct_attempted or public_attempted):
        reasons = []
        if ct_attempted:
            reasons.append('CT')
        if public_attempted:
            reasons.append('PUBLIC')
        return (False, f"Feed-only accepted branch but nonfeed source outcomes were attempted: {', '.join(reasons)}")
    return (True, None)

def _check_nonfeed_evidence_missing(b: BenchmarkSurface) -> tuple[bool, str | None]:
    """
    F224C: FAIL_NONFEED_EVIDENCE_MISSING means terminality was satisfied but nonfeed
    evidence was insufficient. This is a research quality failure, not terminality.

    Fails sanity if:
    - run_quality_verdict is FAIL_NONFEED_EVIDENCE_MISSING
    """
    if b.run_quality_verdict in _NONFEED_EVIDENCE_MISSING_VERDICTS:
        return (False, f'Nonfeed evidence missing: run_quality_verdict={b.run_quality_verdict} — terminality satisfied but nonfeed evidence insufficient')
    return (True, None)

def _check_ct_loss_stage_present(b: BenchmarkSurface) -> tuple[bool, str | None]:
    """
    F214R2: Check CT loss telemetry is present when CT lane has raw evidence but zero accepted.

    Fails if:
    - CT raw_count > 0 and accepted_count == 0 but ct_loss_stage is missing from live_kpi.
    """
    live_kpi = b.live_kpi or {}
    lane_execution = live_kpi.get('lane_execution_counts', {}) or live_kpi.get('source_family_outcomes', [])
    ct_data = None
    if isinstance(lane_execution, dict):
        ct_data = lane_execution.get('ct') or lane_execution.get('CT')
    elif isinstance(lane_execution, list):
        for entry in lane_execution:
            if isinstance(entry, dict):
                fam = entry.get('family', '').lower()
                if fam == 'ct':
                    ct_data = entry
                    break
    if ct_data is None:
        return (True, None)
    ct_raw = ct_data.get('raw_count', 0) if isinstance(ct_data, dict) else 0
    ct_accepted = ct_data.get('accepted_count', 0) if isinstance(ct_data, dict) else 0
    if ct_raw > 0 and ct_accepted == 0:
        ct_loss_stage = live_kpi.get('ct_loss_stage')
        if ct_loss_stage is None:
            return (False, f'CT raw_count={ct_raw} accepted_count=0 but ct_loss_stage is missing from live_kpi')
    return (True, None)

def _get_public_attempted_signals(b: BenchmarkSurface) -> tuple[bool, bool]:
    """
    Extract public attempted signals from various sources.

    Returns (canonical_attempted, legacy_attempted).
    """
    ar = b.acquisition_report or {}
    live_kpi = b.live_kpi or {}
    runtime_truth = b.runtime_truth or {}

    # Canonical: public_terminal_stage set and not NOT_SCHEDULED
    public_terminal_stage = ar.get('public_terminal_stage')
    if not public_terminal_stage:
        public_terminal_stage = b.public_terminal_state or live_kpi.get('public_terminal_stage')
    canonical = public_terminal_stage is not None and public_terminal_stage != 'NOT_SCHEDULED'

    # Legacy: public_fetch_attempted or public_branch_timed_out
    legacy = live_kpi.get('public_fetch_attempted') or runtime_truth.get('public_branch_timed_out') or False

    return (canonical, bool(legacy))


def _extract_families_from_sfo(sfo: list[dict[str, Any]]) -> frozenset[str]:
    """Extract family names from source_family_outcomes."""
    return frozenset(o.get('family', '').lower() for o in sfo if isinstance(o, dict))


def _public_present_in_lane_execution(b: BenchmarkSurface) -> bool:
    """Check if PUBLIC is present in lane_execution_counts."""
    live_kpi = b.live_kpi or {}
    lane_execution = live_kpi.get('lane_execution_counts') or live_kpi.get('source_family_outcomes') or {}
    if isinstance(lane_execution, dict):
        return 'public' in lane_execution or 'PUBLIC' in lane_execution
    if isinstance(lane_execution, list):
        return 'public' in _extract_families_from_sfo(lane_execution)
    return False


def _check_public_surface_present(b: BenchmarkSurface) -> tuple[bool, str | None]:
    """
    F221E: Check PUBLIC lane surface is present when public was attempted.

    Canonical surfaces — uses acquisition_report as authoritative:
    1. acquisition_report.public_terminal_state (canonical, set when PUBLIC was scheduled)
    2. acquisition_report.source_family_outcomes PUBLIC entry
    3. live_kpi.public_fetch_attempted / runtime_truth.public_branch_timed_out (legacy)

    Fails if public was attempted (canonical signal) but PUBLIC is absent.
    """
    canonical_attempted, legacy_attempted = _get_public_attempted_signals(b)

    # If nothing was attempted, pass
    if not canonical_attempted and not legacy_attempted:
        return (True, None)

    ar = b.acquisition_report or {}
    ar_sfo = ar.get('source_family_outcomes')
    if isinstance(ar_sfo, list):
        ar_families = _extract_families_from_sfo(ar_sfo)
        if 'public' in ar_families:
            return (True, None)  # Found in canonical location
        if canonical_attempted:
            return (False, 'public_terminal_stage indicates PUBLIC was attempted but PUBLIC is absent from acquisition_report.source_family_outcomes')

    # Check legacy lane_execution_counts
    if legacy_attempted and not _public_present_in_lane_execution(b):
        return (False, 'public_fetch_attempted=True but PUBLIC absent from lane_execution_counts')

    return (True, None)

def _check_research_quality(q: QualitySurface, min_grade: str | None, allow_feed_only: bool) -> tuple[bool, str | None]:
    """
    Check research quality gate.

    Fails if:
    - quality_gate is missing (None)
    - quality_gate is QUALITY_FAIL_FEED_ONLY and not allow_feed_only
    - quality_gate is any other QUALITY_FAIL_* (always fail)
    - grade is below min_grade threshold (even for warnings)

    Passes (with warning) for QUALITY_WARN_MULTISOURCE_SHALLOW only when above min_grade.
    """
    if q.quality_gate is None:
        return (False, 'quality_gate is missing from research quality surface')
    if min_grade is not None and q.grade is not None:
        grade_order = ['FEED_ONLY', 'MULTISOURCE_SHALLOW', 'MULTISOURCE_USEFUL', 'DEEP_RESEARCH_READY']
        try:
            min_idx = grade_order.index(min_grade)
            actual_idx = grade_order.index(q.grade)
            if actual_idx < min_idx:
                return (False, f'Grade {q.grade} is below minimum required grade {min_grade}')
        except ValueError:  # noqa: BLE001
            pass
    if q.quality_gate.startswith('QUALITY_FAIL_'):
        if q.quality_gate == 'QUALITY_FAIL_FEED_ONLY' and allow_feed_only:
            return (True, None)
        return (False, f'Research quality gate failed: {q.quality_gate}')
    if q.quality_gate == 'QUALITY_WARN_MULTISOURCE_SHALLOW':
        return (True, None)
    return (True, None)

def _check_hardware_constrained_comparable(b: BenchmarkSurface, q: QualitySurface) -> tuple[bool, str | None]:
    """
    F214R2: Check hardware_constrained and research_quality_comparable are consistent.

    Fails if:
    - hardware_constrained=True but research_quality_comparable is True or None.
    """
    live_kpi = b.live_kpi or {}
    hardware_constrained = live_kpi.get('hardware_constrained', False)
    if hardware_constrained and q.research_quality_comparable is not False:
        return (False, f'hardware_constrained={hardware_constrained} but research_quality_comparable={q.research_quality_comparable} — hardware-constrained runs must not be marked comparable')
    return (True, None)

def _check_swap_gate_comparable(b: BenchmarkSurface) -> tuple[bool, str | None]:
    """
    F215D: active300/active600 with swap_gate_triggered=True must have comparable_result=False.

    Fails if:
    - swap_gate_triggered=True but comparable_result is True or None.
    """
    if b.swap_gate_triggered and b.comparable_result is not False:
        return (False, f'swap_gate_triggered={b.swap_gate_triggered} but comparable_result={b.comparable_result} — active300/600 with high swap must not be marked comparable')
    return (True, None)


# =============================================================================
# REFACTORED: sanity_check helpers - eliminates CC=33 → CC<10
# =============================================================================

def _load_json_or_raw(path: str | Path | None, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Load JSON from path if provided, otherwise use raw dict or empty dict."""
    if path and not raw:
        return json.loads(Path(path).read_text())
    return raw or {}


def _run_check(
    check_fn: _CheckFn,
    check_name: str,
    surfaces: tuple[BenchmarkSurface, ValidatorSurface, TraceSurface, QualitySurface],
    *extra_args: Any,
) -> tuple[dict[str, bool], list[str]]:
    """
    Execute a single sanity check and collect results.

    Returns (checks_dict, disagreements_list) updated with this check's result.
    Raises RuntimeError if check fails but returns None message (internal invariant).
    """
    checks: dict[str, bool] = {}
    disagreements: list[str] = []
    ok, msg = check_fn(*surfaces, *extra_args)
    checks[check_name] = ok
    if not ok:
        if msg is None:
            raise RuntimeError(f'Sanity check {check_name!r} returned False but msg is None — internal invariant violated')
        disagreements.append(msg)
    return checks, disagreements


# Check dispatch table - each check specifies how to get its runtime args
# Using a list of _CheckSpec for clarity (tuples were harder to read)
_SANITY_CHECKS: tuple[_CheckSpec, ...] = (
    _CheckSpec('benchmark_fail_validator_pass', _check_benchmark_fail_validator_pass,
               lambda p: ()),
    _CheckSpec('stale_terminality', _check_stale_terminality,
               lambda p: (p.allow_stale_trace,)),
    _CheckSpec('benchmark_shape_source_family_outcomes', _check_benchmark_missing_source_family_outcomes,
               lambda p: ()),
    _CheckSpec('wallclock_budget', _check_wallclock_budget,
               lambda p: ()),
    _CheckSpec('feed_only_nonfeed_attempted', _check_feed_only_accepted_nonfeed_attempted,
               lambda p: ()),
    _CheckSpec('nonfeed_evidence_missing', _check_nonfeed_evidence_missing,
               lambda p: ()),
    _CheckSpec('research_quality', _check_research_quality,
               lambda p: (p.min_quality_grade, p.allow_feed_only)),
    _CheckSpec('ct_loss_stage_present', _check_ct_loss_stage_present,
               lambda p: ()),
    _CheckSpec('public_surface_present', _check_public_surface_present,
               lambda p: ()),
    _CheckSpec('hardware_constrained_comparable', _check_hardware_constrained_comparable,
               lambda p: ()),
    _CheckSpec('swap_gate_comparable', _check_swap_gate_comparable,
               lambda p: ()),
    )


# Verdict classification using frozensets for robust lookup
_VERDICT_KEYWORDS: tuple[tuple[frozenset[str], SanityVerdict], ...] = (
    (frozenset({'quality_gate', 'Research quality gate', 'Grade'}), SanityVerdict.SANITY_FAIL_RESEARCH_QUALITY),
    (frozenset({'ct_loss_stage', 'public_surface', 'hardware_constrained', 'comparable'}), SanityVerdict.SANITY_FAIL_SURFACE_DISAGREEMENT),
    (frozenset({'actual=', 'Wallclock'}), SanityVerdict.SANITY_FAIL_WALLCLOCK_BUDGET),
    (frozenset({'Stale trace verdict', 'TRACE_TERMINALITY'}), SanityVerdict.SANITY_FAIL_STALE_TERMINALITY),
    (frozenset({'internal trace', 'Benchmark missing'}), SanityVerdict.SANITY_FAIL_BENCHMARK_SHAPE_GAP),
    )


def _classify_verdict(disagreements: list[str]) -> SanityVerdict:
    """Classify final verdict from disagreement list using keyword matching."""
    for keywords, verdict in _VERDICT_KEYWORDS:
        for d in disagreements:
            if any(kw in d for kw in keywords):
                return verdict
    return SanityVerdict.SANITY_FAIL_SURFACE_DISAGREEMENT


def _classify_verdict(disagreements: list[str]) -> SanityVerdict:
    """Classify final verdict from disagreement list using priority rules."""
    for predicate, verdict in _VERDICT_RULES:
        if predicate(disagreements):
            return verdict
    return SanityVerdict.SANITY_FAIL_SURFACE_DISAGREEMENT


def sanity_check(
    benchmark_path: str | Path | None = None,
    validation_path: str | Path | None = None,
    trace_path: str | Path | None = None,
    benchmark_raw: dict[str, Any] | None = None,
    validator_raw: dict[str, Any] | None = None,
    trace_raw: dict[str, Any] | None = None,
    allow_stale_trace: bool = False,
    quality_path: str | Path | None = None,
    quality_raw: dict[str, Any] | None = None,
    min_quality_grade: str | None = None,
    allow_feed_only: bool = False,
) -> SanityResult:
    """Load and sanity-check a result bundle.

    Can accept either file paths (for CLI use) or raw dicts (for test use).
    """
    # Create params container for clean dispatch
    params = SanityParams(
        benchmark_path=benchmark_path, validation_path=validation_path,
        trace_path=trace_path, benchmark_raw=benchmark_raw,
        validator_raw=validator_raw, trace_raw=trace_raw,
        allow_stale_trace=allow_stale_trace, quality_path=quality_path,
        quality_raw=quality_raw, min_quality_grade=min_quality_grade,
        allow_feed_only=allow_feed_only,
    )

    # Phase 1: Load all raw data
    raw_b = _load_json_or_raw(params.benchmark_path, params.benchmark_raw)
    raw_v = _load_json_or_raw(params.validation_path, params.validator_raw)
    raw_t = _load_json_or_raw(params.trace_path, params.trace_raw)
    raw_q = _load_json_or_raw(params.quality_path, params.quality_raw)

    # Phase 2: Parse surfaces
    b = parse_benchmark(raw_b)
    v = parse_validator(raw_v)
    t = parse_trace(raw_t)
    q = parse_quality_with_fallback(raw_q, b.research_quality or {})
    surfaces = (b, v, t, q)

    # Phase 3: Run all checks via dispatch table (no special cases!)
    all_checks: dict[str, bool] = {}
    all_disagreements: list[str] = []

    for spec in _SANITY_CHECKS:
        extra_args = spec.runtime_args(params)
        checks, disagreements = _run_check(spec.fn, spec.name, surfaces, *extra_args)
        all_checks.update(checks)
        all_disagreements.extend(disagreements)

    # Phase 4: Classify verdict from disagreements
    verdict = _classify_verdict(all_disagreements) if all_disagreements else SanityVerdict.SANITY_PASS

    return SanityResult(
        verdict=verdict,
        checks=all_checks,
        disagreements=all_disagreements,
        benchmark=b,
        validator=v,
        trace=t,
        quality_surface=q,
    )

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='F211C Live Result Bundle Sanity Checker')
    parser.add_argument('--benchmark-json', type=Path)
    parser.add_argument('--validation-json', type=Path)
    parser.add_argument('--trace-json', type=Path)
    parser.add_argument('--quality-json', type=Path, help='Path to research quality score JSON from research_quality_score.py')
    parser.add_argument('--min-quality-grade', type=str, default=None, help='Minimum acceptable grade (FEED_ONLY, MULTISOURCE_SHALLOW, MULTISOURCE_USEFUL, DEEP_RESEARCH_READY)')
    parser.add_argument('--output-json', type=Path)
    parser.add_argument('--output-md', type=Path)
    parser.add_argument('--allow-stale-trace', action='store_true', default=False, help='Do not fail when stale trace verdicts are present')
    parser.add_argument('--allow-feed-only', action='store_true', default=False, help='Do not fail when research quality gate is FEED_ONLY (smoke mode only)')
    args = parser.parse_args(argv)
    result = sanity_check(benchmark_path=args.benchmark_json, validation_path=args.validation_json, trace_path=args.trace_json, allow_stale_trace=args.allow_stale_trace, quality_path=args.quality_json, min_quality_grade=args.min_quality_grade, allow_feed_only=args.allow_feed_only)
    if args.output_json:
        args.output_json.write_text(json.dumps(result.to_dict(), indent=2))
    if args.output_md:
        args.output_md.write_text(result.to_md())
    print(result.verdict.value)
    for d in result.disagreements:
        print(f'  ! {d}')
    return 0 if result.verdict == SanityVerdict.SANITY_PASS else 1
if __name__ == '__main__':
    sys.exit(main())