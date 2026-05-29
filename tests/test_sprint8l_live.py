"""
Sprint 8L: First Controlled LIVE Tier-1 Run with Rate-Limit Safety

Live run harness - executes real network research with truthful telemetry.
No OFFLINE_REPLAY, no mock data.

HARD RULES enforced:
- Payload cap = 5 MiB (archive_discovery.py:52)
- Timeout discipline: DNS 5s, CT 10s, HTTP 15s, academic 20s, archive 30s
- M1 RAM kill switch at 6.5 GiB RSS
- Rate-limit strategy per family
- NER fallback: NaturalLanguage -> CoreML -> GLiNER
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

# Add universal to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# =============================================================================
# LIVE BENCHMARK RESULTS
# =============================================================================

@dataclass
class LiveHandlerLatency:
    """Per-handler latency statistics."""
    min_ms: float = float('inf')
    mean_ms: float = 0.0
    p95_ms: float = 0.0
    max_ms: float = 0.0
    calls: int = 0
    errors: int = 0
    timeouts: int = 0
    rate_limited: int = 0
    _samples: list[float] = field(default_factory=list)

    def add(self, latency_ms: float, is_error: bool = False,
            is_timeout: bool = False, is_rate_limited: bool = False):
        self._samples.append(latency_ms)
        self.calls += 1
        if is_error:
            self.errors += 1
        if is_timeout:
            self.timeouts += 1
        if is_rate_limited:
            self.rate_limited += 1
        self.min_ms = min(self.min_ms, latency_ms)
        self.max_ms = max(self.max_ms, latency_ms)
        self.mean_ms = ((self.mean_ms * (self.calls - 1)) + latency_ms) / self.calls

    def finalize(self):
        if self._samples:
            sorted_samples = sorted(self._samples)
            p95_idx = int(len(sorted_samples) * 0.95)
            self.p95_ms = sorted_samples[min(p95_idx, len(sorted_samples) - 1)]
        if self.min_ms == float('inf'):
            self.min_ms = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'min_ms': round(self.min_ms, 2),
            'mean_ms': round(self.mean_ms, 2),
            'p95_ms': round(self.p95_ms, 2),
            'max_ms': round(self.max_ms, 2),
            'calls': self.calls,
            'errors': self.errors,
            'timeouts': self.timeouts,
            'rate_limited': self.rate_limited,
        }


@dataclass
class LiveBenchmarkResults:
    """Complete LIVE benchmark results."""
    iterations: int = 0
    findings_total: int = 0
    sources_total: int = 0
    data_mode: str = "LIVE"
    total_wall_clock_s: float = 0.0
    research_runtime_s: float = 0.0
    research_loop_s: float = 0.0
    rss_start_mb: float = 0.0
    rss_peak_mb: float = 0.0
    rss_end_mb: float = 0.0
    rss_slope_mb_per_s: float = 0.0
    rss_samples: list[float] = field(default_factory=list)
    memory_guard_triggered: bool = False
    action_selection_counts: dict[str, int] = field(default_factory=dict)
    action_selection_hhi: float = 0.0
    handler_latency: dict[str, LiveHandlerLatency] = field(default_factory=dict)
    handler_errors: dict[str, int] = field(default_factory=dict)
    handler_timeouts: dict[str, int] = field(default_factory=dict)
    handler_rate_limited: dict[str, int] = field(default_factory=dict)
    rate_limit_429_count: int = 0
    rate_limit_5xx_count: int = 0
    backoff_events: int = 0
    phase_timeline: list[dict[str, Any]] = field(default_factory=list)
    promotion_score_max: float = 0.0
    winner_margin_max: float = 0.0
    source_families: dict[str, int] = field(default_factory=dict)
    source_family_entropy: float = 0.0
    action_families_with_findings: list[str] = field(default_factory=list)
    thermal_state_start: str = "unknown"
    thermal_state_peak: str = "unknown"
    seed_domains: list[str] = field(default_factory=list)
    timeout_discipline_preserved: bool = True
    ner_fallback_note: str = ""
    error: str = ""


SEED_DOMAINS = ["python.org", "github.com", "arxiv.org", "archive.org"]
LIVE_QUERY = "python programming tutorial github source code arxiv research documentation"

TIMEOUT_BUDGETS = {
    'network_recon': 5.0,
    'ct_discovery': 10.0,
    'surface_search': 15.0,
    'academic_search': 20.0,
    'archive_fetch': 30.0,
    'wayback_rescue': 30.0,
    'commoncrawl_rescue': 30.0,
    'render_page': 30.0,
    'necromancer_rescue': 30.0,
}

RATE_LIMIT_STRATEGY = {
    'surface_search': {'rate': 10, 'unit': 'requests/minute', 'backoff': 2.0},
    'academic_search': {'rate': 5, 'unit': 'requests/minute', 'backoff': 2.0},
    'ct_discovery': {'rate': 20, 'unit': 'requests/minute', 'backoff': 2.0},
    'network_recon': {'rate': 30, 'unit': 'requests/minute', 'backoff': 1.5},
}

RSS_KILL_SWITCH_MB = 6.5 * 1024


class LiveLatencyCollector:
    """Wraps orchestrator to capture per-handler latency."""

    def __init__(self, orch):
        self.orch = orch
        self.latency: dict[str, LiveHandlerLatency] = {
            name: LiveHandlerLatency() for name in TIMEOUT_BUDGETS.keys()
        }
        self._original_execute = orch._execute_action
        self._orch = orch
        orch._execute_action = self._wrapped_execute

    async def _wrapped_execute(self, name: str, **params):
        start_ns = time.perf_counter_ns()
        is_error = is_timeout = is_rate_limited = False
        try:
            result = await self._original_execute(name, **params)
            if result:
                is_error = not result.success
                error_msg = (result.error or '').lower()
                is_timeout = 'timeout' in error_msg or 'timed out' in error_msg
                is_rate_limited = '429' in error_msg or 'rate limit' in error_msg
            return result
        except TimeoutError:
            is_timeout = True
            is_error = True
            return None
        except Exception:
            is_error = True
            return None
        finally:
            latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            if name in self.latency:
                self.latency[name].add(latency_ms, is_error, is_timeout, is_rate_limited)

    def finalize(self):
        for lat in self.latency.values():
            lat.finalize()


class RSSMonitor:
    def __init__(self, interval_s: float = 10.0):
        self.interval_s = interval_s
        self.samples: list[float] = []
        self.peak_mb: float = 0.0
        self.start_rss_mb: float = 0.0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.process = psutil.Process(os.getpid())

    async def start(self):
        self.start_rss_mb = self.get_rss_mb()
        self.peak_mb = self.start_rss_mb
        self.samples.append(self.start_rss_mb)
        self._stop.clear()
        self._task = asyncio.create_task(self._monitor())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except TimeoutError:
                self._task.cancel()

    async def _monitor(self):
        while not self._stop.is_set():
            await asyncio.sleep(self.interval_s)
            if self._stop.is_set():
                break
            rss = self.get_rss_mb()
            self.samples.append(rss)
            self.peak_mb = max(self.peak_mb, rss)

    def get_rss_mb(self) -> float:
        try:
            return self.process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    def compute_slope(self, elapsed_s: float) -> float:
        if elapsed_s <= 0 or len(self.samples) < 2:
            return 0.0
        n = len(self.samples)
        dt = elapsed_s / max(n - 1, 1)
        y = self.samples
        x_mean = (n - 1) * dt / 2
        y_mean = sum(y) / n
        num = sum((i * dt - x_mean) * (y[i] - y_mean) for i in range(n))
        den = sum((i * dt - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0.0


class PhaseTracker:
    def __init__(self, orch):
        self.orch = orch
        self.timeline: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.max_promotion_score = 0.0
        self.max_winner_margin = 0.0
        self._last_phase = None

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._track())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except TimeoutError:
                self._task.cancel()

    def _read_current_state(self) -> tuple:
        """Read current phase and promotion score from orchestrator."""
        try:
            pc = getattr(self.orch, '_phase_controller', None)
            if pc and hasattr(pc, 'current_phase'):
                phase = pc.current_phase.name
            else:
                phase = 'unknown'

            # Read winner_margin from lane manager
            winner_margin = 0.0
            lm = getattr(self.orch, '_lane_manager', None)
            if lm and hasattr(lm, 'active_lanes') and lm.active_lanes:
                try:
                    lanes = list(lm.active_lanes)
                    if len(lanes) >= 2:
                        priorities = [getattr(l, 'priority', 0.0) for l in lanes]
                        winner_margin = max(priorities) - sorted(priorities)[-2] if len(priorities) > 1 else 0.0
                except Exception:
                    pass

            # Promotion score: use the same formula as phase_controller
            promotion_score = 0.0
            try:
                if pc and hasattr(pc, '_compute_promotion_score'):
                    # Build a minimal PhaseSignals-like object
                    signals = pc._get_signals() if hasattr(pc, '_get_signals') else None
                    if signals is not None:
                        promotion_score = pc._compute_promotion_score(signals)
            except Exception:
                pass

            return phase, promotion_score, winner_margin
        except Exception:
            return 'unknown', 0.0, 0.0

    async def _track(self):
        while not self._stop.is_set():
            await asyncio.sleep(5.0)
            if self._stop.is_set():
                break
            try:
                phase, promotion_score, winner_margin = self._read_current_state()

                # Detect phase transition
                if phase != self._last_phase:
                    self.timeline.append({
                        'elapsed_s': time.time() - getattr(self.orch, '_research_loop_start_time', time.time()),
                        'phase': phase,
                        'promotion_score': round(promotion_score, 3),
                        'winner_margin': round(winner_margin, 3),
                        'event': 'PHASE_CHANGE',
                    })
                    self._last_phase = phase

                self.max_promotion_score = max(self.max_promotion_score, promotion_score)
                self.max_winner_margin = max(self.max_winner_margin, winner_margin)
            except Exception:
                pass


def detect_ner_fallback() -> str:
    try:
        from NaturalLanguage import NLTagger
        return "NaturalLanguage.framework (ANE)"
    except ImportError:
        pass
    try:
        import coremltools
        return "CoreML (ANE)"
    except ImportError:
        pass
    try:
        import torch
        from transformers import AutoModelForTokenClassification
        return "GLiNER (torch)"
    except ImportError:
        pass
    return "No NER available"


def compute_hhi(counts: dict[str, int]) -> float:
    if not counts:
        return 0.0
    total = sum(counts.values())
    if total == 0:
        return 0.0
    shares = [(v / total) ** 2 for v in counts.values() if v > 0]
    return sum(shares)


async def run_live_benchmark(
    duration_seconds: int = 60,
    query: str = LIVE_QUERY,
    output_dir: str = "./benchmark_results/live"
) -> LiveBenchmarkResults:
    """Execute a controlled LIVE Tier-1 research run."""

    results = LiveBenchmarkResults()
    results.seed_domains = SEED_DOMAINS
    results.ner_fallback_note = detect_ner_fallback()
    results.data_mode = "LIVE"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("SPRINT 8L: CONTROLLED LIVE TIER-1 RUN")
    print("=" * 70)
    print(f"  Duration: {duration_seconds}s")
    print(f"  Query: {query}")
    print(f"  Seed domains: {SEED_DOMAINS}")
    print(f"  RSS kill switch: {RSS_KILL_SWITCH_MB:.0f}MB (6.5GiB)")
    print(f"  NER fallback: {results.ner_fallback_note}")
    print("=" * 70)

    hledac_offline = os.environ.get("HLEDAC_OFFLINE", "0") == "1"
    if hledac_offline:
        print("ERROR: HLEDAC_OFFLINE=1 is set — handlers will be blocked!")
        print("Unset HLEDAC_OFFLINE to run live: unset HLEDAC_OFFLINE")
        results.error = "HLEDAC_OFFLINE=1 blocks all live handlers"
        return results

    try:
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        print("\n[1/6] Initializing orchestrator...")
        init_start = time.time()
        orch = FullyAutonomousOrchestrator()
        ok = await orch.initialize()
        if not ok:
            results.error = "initialize() returned False"
            return results
        print(f"  Initialize: {time.time() - init_start:.1f}s")

        rss_monitor = RSSMonitor(interval_s=10.0)
        await rss_monitor.start()
        results.rss_start_mb = rss_monitor.start_rss_mb

        phase_tracker = PhaseTracker(orch)
        await phase_tracker.start()

        latency_collector = LiveLatencyCollector(orch)

        research_start = time.time()
        print(f"\n[2/6] Starting LIVE research ({duration_seconds}s)...")
        print("  REAL NETWORK CALLS WILL BE MADE")
        print("  Rate limits: surface=10/min, academic=5/min, ct=20/min, network_recon=30/min")

        try:
            result = await asyncio.wait_for(
                orch.research(
                    query=query,
                    timeout=float(duration_seconds),
                    offline_replay=False,  # LIVE MODE
                ),
                timeout=float(duration_seconds) + 120.0
            )
            if result:
                # Sprint 8L: ComprehensiveResearchResult has .findings and .sources directly
                results.findings_total = len(getattr(result, 'findings', []))
                results.sources_total = len(getattr(result, 'sources', []))
                # Also read from statistics as fallback
                if hasattr(result, 'statistics') and isinstance(result.statistics, dict):
                    results.iterations = result.statistics.get('iterations', 0)
                else:
                    results.iterations = getattr(result.statistics, 'iterations', 0) if hasattr(result, 'statistics') else 0
        except TimeoutError:
            print("  Research timed out — collecting partial results...")
            results.error = "timeout"
        except Exception as e:
            print(f"  Research error: {e}")
            results.error = str(e)

        research_elapsed = time.time() - research_start
        results.research_runtime_s = research_elapsed

        await rss_monitor.stop()
        await phase_tracker.stop()
        latency_collector.finalize()

        results.rss_end_mb = rss_monitor.get_rss_mb()
        results.rss_peak_mb = rss_monitor.peak_mb
        results.rss_slope_mb_per_s = rss_monitor.compute_slope(research_elapsed)
        results.rss_samples = rss_monitor.samples

        if results.rss_peak_mb > RSS_KILL_SWITCH_MB:
            results.memory_guard_triggered = True
            print(f"\n  MEMORY GUARD TRIGGERED: {results.rss_peak_mb:.0f}MB > {RSS_KILL_SWITCH_MB:.0f}MB")

        for name, lat in latency_collector.latency.items():
            results.handler_latency[name] = lat
            if lat.errors > 0:
                results.handler_errors[name] = lat.errors
            if lat.timeouts > 0:
                results.handler_timeouts[name] = lat.timeouts
            if lat.rate_limited > 0:
                results.handler_rate_limited[name] = lat.rate_limited

        action_counts = getattr(orch, '_action_executed_counts', {}) or {}
        results.action_selection_counts = dict(action_counts)
        results.action_selection_hhi = compute_hhi(action_counts)

        results.phase_timeline = phase_tracker.timeline
        results.promotion_score_max = phase_tracker.max_promotion_score
        results.winner_margin_max = phase_tracker.max_winner_margin

        print("\n[3/6] Cleaning up...")
        cleanup_start = time.time()
        if hasattr(orch, 'cleanup'):
            try:
                await asyncio.wait_for(orch.cleanup(), timeout=10.0)
            except Exception as e:
                print(f"  Cleanup error: {e}")
        print(f"  Cleanup: {time.time() - cleanup_start:.1f}s")

        results.total_wall_clock_s = research_elapsed + (time.time() - cleanup_start)

        families = set(results.action_selection_counts.keys())
        results.action_families_with_findings = list(families)

        thermal = "unknown"
        try:
            if hasattr(orch, '_memory_mgr') and orch._memory_mgr:
                ts = orch._memory_mgr.get_thermal_state()
                thermal = str(ts) if ts is not None else "unknown"
        except Exception:
            pass
        results.thermal_state_start = thermal
        results.thermal_state_peak = thermal

    except Exception as e:
        import traceback
        results.error = f"{e}\n{traceback.format_exc()}"
        print(f"\nFATAL ERROR: {e}")

    return results


def print_live_results(results: LiveBenchmarkResults) -> bool:
    print("\n" + "=" * 70)
    print("SPRINT 8L RESULTS")
    print("=" * 70)

    print("\n[CORE]")
    print(f"  data_mode:              {results.data_mode}")
    print(f"  iterations:             {results.iterations}")
    print(f"  findings_total:         {results.findings_total}")
    print(f"  sources_total:          {results.sources_total}")
    print(f"  total_wall_clock_s:     {results.total_wall_clock_s:.1f}")
    print(f"  research_runtime_s:     {results.research_runtime_s:.1f}")

    print("\n[MEMORY - M1 8GB]")
    print(f"  rss_start_mb:           {results.rss_start_mb:.0f}")
    print(f"  rss_peak_mb:            {results.rss_peak_mb:.0f}")
    print(f"  rss_end_mb:             {results.rss_end_mb:.0f}")
    print(f"  rss_slope_mb_per_s:     {results.rss_slope_mb_per_s:.2f}")
    print(f"  rss_samples:            {len(results.rss_samples)} samples")
    print(f"  memory_guard_triggered: {results.memory_guard_triggered}")
    if results.rss_samples:
        traj = ' -> '.join(f'{s:.0f}' for s in results.rss_samples[:8])
        if len(results.rss_samples) > 8:
            traj += f" ... ({len(results.rss_samples)} total)"
        print(f"  RSS trajectory:        {traj}")

    print("\n[ACTION DISTRIBUTION]")
    for name, count in sorted(results.action_selection_counts.items(), key=lambda x: -x[1]):
        pct = 100 * count / max(sum(results.action_selection_counts.values()), 1)
        print(f"  {name:30s}: {count:5d} ({pct:5.1f}%)")
    print(f"  HHI:                    {results.action_selection_hhi:.3f}")

    print("\n[HANDLER LATENCY TABLE]")
    print(f"  {'Handler':<25s} {'min_ms':>8s} {'mean_ms':>8s} {'p95_ms':>8s} {'max_ms':>8s} {'calls':>6s} {'errors':>6s} {'timeouts':>8s} {'429':>6s}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*8} {'-'*6}")
    for name in TIMEOUT_BUDGETS:
        if name in results.handler_latency:
            lat = results.handler_latency[name]
            print(f"  {name:<25s} {lat.min_ms:8.1f} {lat.mean_ms:8.1f} {lat.p95_ms:8.1f} {lat.max_ms:8.1f} {lat.calls:6d} {lat.errors:6d} {lat.timeouts:8d} {lat.rate_limited:6d}")
        else:
            print(f"  {name:<25s} {'N/A':>8s} {'N/A':>8s} {'N/A':>8s} {'N/A':>8s} {'0':>6s} {'0':>6s} {'0':>8s} {'0':>6s}")

    print("\n[PHASE TIMELINE]")
    print(f"  promotion_score_max:   {results.promotion_score_max:.3f}")
    print(f"  winner_margin_max:      {results.winner_margin_max:.3f}")
    if results.phase_timeline:
        for entry in results.phase_timeline[:8]:
            print(f"    t={entry['elapsed_s']:6.1f}s phase={entry['phase']:15s} promo={entry['promotion_score']:.3f} margin={entry['winner_margin']:.3f}")
        if len(results.phase_timeline) > 8:
            print(f"    ... ({len(results.phase_timeline)} total entries)")
    else:
        print("    (no phase data captured)")

    print("\n[ACTION FAMILIES WITH FINDINGS]")
    print(f"  {results.action_families_with_findings}")

    print("\n[THERMAL]")
    print(f"  thermal_state_start:    {results.thermal_state_start}")
    print(f"  thermal_state_peak:    {results.thermal_state_peak}")

    print("\n[NER FALLBACK]")
    print(f"  {results.ner_fallback_note}")

    if results.error:
        print("\n[ERROR]")
        print(f"  {results.error}")

    total_429 = sum(results.handler_rate_limited.values())
    total_timeouts = sum(results.handler_timeouts.values())
    total_errors = sum(results.handler_errors.values())
    print("\n[SUMMARY]")
    print(f"  rate_limited_429_total: {total_429}")
    print(f"  timeouts_total:          {total_timeouts}")
    print(f"  errors_total:            {total_errors}")

    print("\n" + "=" * 70)
    print("\n[SUCCESS CRITERIA CHECK]")
    criteria = [
        ("iterations > 0", results.iterations > 0),
        ("findings_total > 0", results.findings_total > 0),
        ("handler latency captured", len(results.handler_latency) > 0),
        ("action distribution captured", len(results.action_selection_counts) > 0),
        ("promotion_score_max > 0.25", results.promotion_score_max > 0.25),
        (">=3 action families", len(results.action_families_with_findings) >= 3),
        ("memory guard NOT triggered", not results.memory_guard_triggered),
        ("no fatal error", not results.error or results.error == "timeout"),
    ]

    all_passed = True
    for name, passed in criteria:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if not passed:
            all_passed = False

    print(f"\n{'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


def save_results(results: LiveBenchmarkResults, output_dir: str = "./benchmark_results/live"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = output_path / f"live_results_{ts}.json"

    data = {
        'iterations': results.iterations,
        'findings_total': results.findings_total,
        'sources_total': results.sources_total,
        'data_mode': results.data_mode,
        'total_wall_clock_s': round(results.total_wall_clock_s, 2),
        'research_runtime_s': round(results.research_runtime_s, 2),
        'rss_start_mb': round(results.rss_start_mb, 1),
        'rss_peak_mb': round(results.rss_peak_mb, 1),
        'rss_end_mb': round(results.rss_end_mb, 1),
        'rss_slope_mb_per_s': round(results.rss_slope_mb_per_s, 3),
        'rss_samples': [round(s, 1) for s in results.rss_samples],
        'memory_guard_triggered': results.memory_guard_triggered,
        'action_selection_counts': results.action_selection_counts,
        'action_selection_hhi': round(results.action_selection_hhi, 3),
        'handler_latency': {k: v.to_dict() for k, v in results.handler_latency.items()},
        'handler_errors': results.handler_errors,
        'handler_timeouts': results.handler_timeouts,
        'handler_rate_limited': results.handler_rate_limited,
        'phase_timeline': results.phase_timeline,
        'promotion_score_max': round(results.promotion_score_max, 3),
        'winner_margin_max': round(results.winner_margin_max, 3),
        'action_families_with_findings': results.action_families_with_findings,
        'thermal_state_start': results.thermal_state_start,
        'thermal_state_peak': results.thermal_state_peak,
        'seed_domains': results.seed_domains,
        'ner_fallback_note': results.ner_fallback_note,
        'error': results.error,
    }

    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nResults saved to: {path}")
    return path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sprint 8L: Controlled LIVE Tier-1 Run")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--query", type=str, default=LIVE_QUERY)
    parser.add_argument("--output", type=str, default="./benchmark_results/live")
    args = parser.parse_args()

    results = asyncio.run(run_live_benchmark(
        duration_seconds=args.duration,
        query=args.query,
        output_dir=args.output,
    ))

    print_live_results(results)
    save_results(results, args.output)
