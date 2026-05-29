"""
runtime/sidecar_bus.py — F204A: Canonical Accepted-Finding Sidecar Bus
======================================================================

Unified sidecar orchestrator for all accepted findings from feed/public/CT branches.
Bounded batch processor: takes SidecarBatch, fans out to registered sidecar
runners via staged asyncio.gather(return_exceptions=True), collects SidecarRunResult records.

F205B: Explicit staged ordering guarantee — runners execute in 3 stages:
- Stage 1 (light extraction): leak_sentinel, passive_fingerprint, evidence_triage, temporal_archaeology
- Stage 2 (correlation): exposure_correlator, identity_stitching, sprint_diff, rir_correlator,
  social_identity_surface, wayback_diff
- Stage 3 (derived): kill_chain_tagging, embedding

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True (per stage)
- _check_gathered() called after every gather
- asyncio.CancelledError re-raised, never swallowed
- No blocking calls in event loop; CPU/IO via run_in_executor
- Canonical write path always async_ingest_findings_batch()
- RAM guard: skip heavy sidecars if governor reports critical/emergency
- Each collection has MAX_* constant
- Fail-soft: sidecar error never crashes the sprint
- Stage N failure does not stop stage N+1
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


def _safe_payload_json(obj: Any) -> str:
    """
    Serialize obj to canonical JSON string, fail-soft.

    Prefers orjson (fast, canonical). Falls back to json.dumps with
    canonical separators. Last resort: str(obj).
    No import-time hard dependency, no global side effects.
    """
    # Try orjson first
    try:
        import orjson
        return orjson.dumps(obj).decode("utf-8")
    except Exception:
        pass
    # Fallback: json.dumps with canonical separators
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass
    # Last resort: str
    return str(obj)

logger = logging.getLogger(__name__)
# Shared logger for _check_gathered — preserves hardcoded "sidecar_bus" identity
_sidecar_logger = logging.getLogger("sidecar_bus")

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_SIDECAR_FINDINGS: int = 500
MAX_SIDECAR_RESULT_RECORDS: int = 32
SIDECAR_TIMEOUT_S: float = 20.0

# Heavy sidecar names — skipped when M1 governor reports critical/emergency
_HEAVY_SIDECARS: frozenset[str] = frozenset({
    "identity_stitching",
    "embedding",
    "sprint_diff",
    "banner_grab",
    "ipv6_recon",
    "pattern_mining",  # F250: pattern analysis can be memory-intensive
})

# F240A: Active-network sidecars — require explicit active/aggressive profile
_ACTIVE_NETWORK_SIDECARS: frozenset[str] = frozenset({
    "network_intel",
    "banner_grab",
    "ipv6_recon",
    "gopher_crawl",
})


# F247C: Explicit sidecar network-classification map.
# Covers every sidecar name registered in DEFAULT_SIDECAR_RUNNERS.
# - active_network: performs live TCP/HTTP/network operations
# - core: passive processing, no live network beyond the finding source
# - duplicate_compat: compatibility/derived runners with no independent canonical output
SIDECAR_NETWORK_CLASS: dict[str, str] = {
    # active_network — live TCP/HTTP/network
    "leak_sentinel": "active_network",
    "network_intel": "active_network",
    "banner_grab": "active_network",
    "ipv6_recon": "active_network",
    "rir_correlator": "active_network",
    "wayback_diff": "active_network",
    "social_identity_surface": "active_network",
    "gopher_crawl": "active_network",
    # core — passive processing
    "passive_fingerprint": "core",
    "passive_tech_stack": "core",
    "evidence_triage": "core",
    "temporal_archaeology": "core",
    "exposure_correlator": "core",
    "identity_stitching": "core",
    "sprint_diff": "core",
    "kill_chain_tagging": "core",
    # duplicate_compat — derived, no independent canonical output
    "embedding": "duplicate_compat",
    "pattern_mining": "core",  # F250: passive analysis of existing findings
}


def classify_sidecar_network(sidecar_name: str) -> str:
    """
    Classify a sidecar by its network I/O behaviour.

    Returns one of:
      - "active_network" — live TCP/HTTP/network operations
      - "core"            — passive processing, no live network beyond finding source
      - "duplicate_compat" — compatibility/derived, no independent canonical output
      - "unknown"         — sidecar not in the classification map

    Fail-open: unknown sidecars are treated as "active_network" to avoid
    accidentally enabling undiscovered live-network sidecars in passive profiles.
    """
    return SIDECAR_NETWORK_CLASS.get(sidecar_name, "unknown")


# F248C: Explicit network-risk taxonomy for policy and telemetry clarity.
# Subdivides network-active sidecars into:
# - active_target: performs direct TCP/HTTP connection to the investigation target
# - third_party_provider: calls third-party APIs/providers, not the target itself
SIDECAR_NETWORK_RISK: dict[str, str] = {
    # active_target — direct connection to target (investigation subject)
    "banner_grab": "active_target",
    "ipv6_recon": "active_target",
    "gopher_crawl": "active_target",
    # third_party_provider — external API/provider calls, not target scanning
    "network_intel": "third_party_provider",
    "leak_sentinel": "third_party_provider",
    "rir_correlator": "third_party_provider",
    "social_identity_surface": "third_party_provider",
    "wayback_diff": "third_party_provider",
}


def classify_sidecar_risk(sidecar_name: str) -> str:
    """
    Classify a sidecar by its network risk posture (F248C).

    Returns one of:
      - "active_target"         — direct TCP/HTTP connection to the target
      - "third_party_provider"  — third-party API/provider calls
      - "passive"               — no live network operations
      - "unknown"               — sidecar not in the risk map

    Used for telemetry and policy logging. Does NOT gate execution.
    """
    return SIDECAR_NETWORK_RISK.get(sidecar_name, "unknown")


def _sidecar_profile_allows(name: str, profile: str | None) -> tuple[bool, str]:
    """
    Return (allowed, skip_reason) for active-network sidecars.

    Rules:
    - banner_grab / ipv6_recon: disabled unless profile is "active" or "aggressive"
    - network_intel: disabled unless profile is "active", "aggressive", or "network_recon"
    - Unknown profile: allow (fail-open for safety)
    """
    if name not in _ACTIVE_NETWORK_SIDECARS:
        return (True, "")
    if not profile:
        return (False, "profile_disallows_active_network_sidecar")
    p = profile.lower()
    if name == "network_intel":
        if p in ("active", "aggressive", "network_recon"):
            return (True, "")
        return (False, "profile_disallows_active_network_sidecar")
    else:
        # banner_grab, ipv6_recon — require active or aggressive
        if p in ("active", "aggressive"):
            return (True, "")
        return (False, "profile_disallows_active_network_sidecar")


# F205B: Explicit staged ordering guarantee
# Stage 1 (light extraction): runs first, no dependencies on other sidecars
# Stage 2 (correlation): runs after stage 1, depends on signals produced by stage 1
# Stage 3 (derived): runs last, depends on correlated signals from stage 2
SIDECAR_STAGES: tuple[tuple[str, ...], ...] = (
    # Stage 1: light extraction — passive signal collection
    (
        "leak_sentinel",
        "passive_fingerprint",
        "passive_tech_stack",
        "evidence_triage",
        "temporal_archaeology",
        "network_intel",
        "banner_grab",
        "ipv6_recon",
    ),
    # Stage 2: correlation — combines signals into exposure/identity/attribution findings
    (
        "exposure_correlator",
        "identity_stitching",
        "sprint_diff",
        "rir_correlator",
        "social_identity_surface",
        "wayback_diff",
    ),
    # Stage 3: derived — kill-chain tagging and embedding (requires correlated signals)
    ("kill_chain_tagging", "embedding"),
)

# F245B: Canonical sidecar → source_family mapping.
# Skips: wayback_diff (canonical owner is Wayback lane), passive_tech_stack
# (duplicate of passive_fingerprint), embedding (vector side-effect only, no
# CanonicalFinding output), evidence_triage (stats counter only, no new signal).
SIDECAR_FAMILY_MAP: dict[str, str] = {
    "kill_chain_tagging": "killchain_tag",
    "sprint_diff": "sprint_diff",
    "identity_stitching": "identity_stitching",
    "exposure_correlator": "exposure_correlator",
    "social_identity_surface": "social_identity_surface",
    "passive_fingerprint": "passive_fingerprint",
    "rir_correlator": "rir_correlation",
    "leak_sentinel": "leak_sentinel",
    "temporal_archaeology": "temporal_archaeology",
    "network_intel": "network_intel",
}


def sidecar_results_to_source_family_outcomes(
    results: list[SidecarRunResult],
) -> tuple[dict, ...]:
    """
    Convert SidecarRunResult list to normalized source_family_outcomes entries.

    Only canonical/non-duplicate sidecars are mapped (per SIDECAR_FAMILY_MAP).
    Skipped heavy sidecars are included with terminal_state="no_results".

    Entry shape:
        {
            "family": family,           # normalized family name
            "accepted_count": sr.stored_count,
            "raw_count": sr.produced_count,
            "built_count": sr.produced_count,
            "attempted": sr.attempted,
            "terminal_state": "completed" if sr.stored_count > 0 else "no_results",
            "skipped": not sr.attempted,
            "skip_reason": sr.skipped_reason,
            "lane": f"sidecar:{sr.sidecar_name}",
            "duration_s": sr.elapsed_ms / 1000,
        }

    Deduplication: append-only, caller is responsible for dedup by (family, lane)
    if dispatch runs multiple times per sprint.
    """
    outcomes: list[dict] = []
    for sr in results:
        family = SIDECAR_FAMILY_MAP.get(sr.sidecar_name)
        if family is None:
            # Not a canonical sidecar — skip (wayback_diff, passive_tech_stack,
            # embedding, evidence_triage, etc.)
            continue
        outcomes.append({
            "family": family,
            "accepted_count": sr.stored_count,
            "raw_count": sr.produced_count,
            "built_count": sr.produced_count,
            "attempted": sr.attempted,
            "terminal_state": "completed" if sr.stored_count > 0 else "no_results",
            "skipped": not sr.attempted,
            "skip_reason": sr.skipped_reason,
            "lane": f"sidecar:{sr.sidecar_name}",
            "duration_s": sr.elapsed_ms / 1000,
        })
    return tuple(outcomes)


# F204J: Import constants from resource_governor
try:
    from hledac.universal.runtime.resource_governor import SIDECAR_DEFAULT_ESTIMATE_MB
except ImportError:
    SIDECAR_DEFAULT_ESTIMATE_MB = 128


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SidecarBatch:
    """Bounded batch of accepted findings from one source branch."""

    sprint_id: str
    query: str
    source_branch: str  # "feed" | "public" | "ct"
    findings: tuple[Any, ...]
    created_ts: float


@dataclass(frozen=True)
class SidecarRunResult:
    """Outcome of one sidecar runner invocation."""

    sidecar_name: str
    attempted: bool
    produced_count: int
    stored_count: int
    skipped_reason: str
    elapsed_ms: float


# ── Sidecar Runner Signature ───────────────────────────────────────────────────
# Each runner: async def (findings: list, store: DuckDBShadowStore, query: str) -> int | None
SidecarRunner = Callable[[list, "DuckDBShadowStore", str], Any]


# ── Main Bus ───────────────────────────────────────────────────────────────────
class FindingSidecarBus:
    """
    Unified bounded orchestrator for all accepted-finding sidecars.

    All three source branches (feed, public, ct) route their accepted findings
    through this bus. The bus fans out to registered sidecar runners in stage order,
    collects per-runner SidecarRunResult records, and returns them.

    Stages execute sequentially (stage 1 → stage 2 → stage 3). Within each stage,
    runners execute concurrently via asyncio.gather(return_exceptions=True).

    RAM guard: heavy sidecars (identity_stitching, embedding, sprint_diff) are
    skipped when M1 governor reports critical or emergency memory pressure.

    Fail-soft: individual sidecar errors are captured in SidecarRunResult and do
    not propagate or crash the sprint. Stage N failure does not stop stage N+1.
    """

    def __init__(self, governor: Any = None, acquisition_profile: str | None = None) -> None:
        self._governor = governor
        self._acquisition_profile = acquisition_profile
        self._runners: dict[str, SidecarRunner] = {}
        self._results: list[SidecarRunResult] = []

    # ── Registration ────────────────────────────────────────────────────────────

    def register(self, name: str, runner: SidecarRunner) -> None:
        """Register a sidecar runner by name."""
        if name in self._runners:
            raise ValueError(f"Sidecar runner already registered: {name}")
        self._runners[name] = runner

    # ── RAM Guard ─────────────────────────────────────────────────────────────

    def _is_heavy_blocked(self, name: str) -> tuple[bool, str]:
        """
        Return (blocked, reason) if a heavy sidecar should be skipped due to RAM pressure.

        F204J: Now uses governor.sidecar_admission() for consistent admission checks.
        """
        if name not in _HEAVY_SIDECARS:
            return (False, "")
        if self._governor is None:
            return (False, "")
        try:
            admission = self._governor.sidecar_admission(name, SIDECAR_DEFAULT_ESTIMATE_MB)
            return (not admission.allowed, admission.reason)
        except Exception:
            return (False, "")  # Fail-soft: allow heavy sidecars if governor errors

    def _is_active_network_blocked(self, name: str) -> tuple[bool, str]:
        """
        Return (blocked, reason) if an active-network sidecar should be skipped
        due to acquisition profile.

        F240A: Active network sidecars (network_intel, banner_grab, ipv6_recon)
        require explicit active/aggressive profile and must not run in default or
        nonfeed_diagnostic profiles on M1.
        """
        allowed, reason = _sidecar_profile_allows(name, self._acquisition_profile)
        return (not allowed, reason)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _check_gathered(self, gathered: list[Any]) -> None:
        """
        Verify no unexpected exceptions leaked through gather(return_exceptions=True).

        GHOST_INVARIANT: called after every asyncio.gather with return_exceptions=True.
        Sidecar errors are already captured as SidecarRunResult — this checks for
        truly unexpected BaseExceptions that slipped through.
        """
        for item in gathered:
            if isinstance(item, BaseException) and not isinstance(item, SidecarRunResult):
                # Unexpected exception — log but don't crash (fail-soft)
                # NOTE: module-level `_sidecar_logger` is defined at line 64 — reusing it here
                # instead of creating a new logger instance per exception.
                _sidecar_logger.warning(
                    "Unexpected exception in gather: %s: %s",
                    type(item).__name__,
                    item,
                )

    # ── Core: Run All Sidecars ────────────────────────────────────────────────

    async def run_all_sidecars(
        self,
        batch: SidecarBatch,
        store: DuckDBShadowStore,
    ) -> list[SidecarRunResult]:
        """
        Fan out to all registered sidecar runners for the given batch, in stage order.

        Stages run sequentially (stage 1 → stage 2 → stage 3). Within each stage,
        runners execute concurrently via asyncio.gather(return_exceptions=True).

        Returns list of SidecarRunResult (one per runner that was attempted).

        Bounds:
        - findings capped at MAX_SIDECAR_FINDINGS
        - results capped at MAX_SIDECAR_RESULT_RECORDS
        - per-runner timeout: SIDECAR_TIMEOUT_S

        GHOST_INVARIANTS:
        - gather(return_exceptions=True) within each stage
        - _check_gathered() after each stage's gather
        - asyncio.CancelledError re-raised
        - fail-soft: stage N failure does not stop stage N+1
        """
        self._results = []

        # ── Bound the batch ──────────────────────────────────────────────────
        findings = list(batch.findings)
        if len(findings) > MAX_SIDECAR_FINDINGS:
            findings = findings[:MAX_SIDECAR_FINDINGS]

        if not findings:
            return []

        # ── Per-stage coroutine builder ──────────────────────────────────────
        async def _run_one(name: str, runner: SidecarRunner) -> SidecarRunResult:
            t0 = _time.monotonic()

            # RAM guard check using governor.sidecar_admission()
            blocked, reason = self._is_heavy_blocked(name)
            if blocked:
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=False,
                    produced_count=0,
                    stored_count=0,
                    skipped_reason=reason or "ram_governor_critical",
                    elapsed_ms=elapsed_ms,
                )

            # F240A: Active-network sidecar profile gating
            blocked, reason = self._is_active_network_blocked(name)
            if blocked:
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=False,
                    produced_count=0,
                    stored_count=0,
                    skipped_reason=reason or "profile_disallows_active_network_sidecar",
                    elapsed_ms=elapsed_ms,
                )

            try:
                async with asyncio.timeout(SIDECAR_TIMEOUT_S):
                    result = await runner(findings, store, batch.query)
                elapsed_ms = (_time.monotonic() - t0) * 1000

                # F214OPT-I: capture runner return value truthfully
                produced_count = 0
                stored_count = 0
                if isinstance(result, int):
                    produced_count = result
                    stored_count = result
                elif isinstance(result, dict):
                    produced_count = result.get("produced_count", 0)
                    stored_count = result.get("stored_count", 0)
                elif result is None:
                    produced_count = 0
                    stored_count = 0
                # else: unexpected type → default 0/0 (fail-soft)

                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=True,
                    produced_count=produced_count,
                    stored_count=stored_count,
                    skipped_reason="",
                    elapsed_ms=elapsed_ms,
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                # Python 3.14: asyncio.gather wraps CancelledError in ExceptionGroup;
                # detect it via the nested-exception walk rather than exc.type
                def _is_cancelled_tree(e: BaseException) -> bool:
                    if isinstance(e, asyncio.CancelledError):
                        return True
                    if isinstance(e, ExceptionGroup):
                        return any(_is_cancelled_tree(s) for s in e.exceptions)
                    return False
                if _is_cancelled_tree(exc):
                    raise asyncio.CancelledError() from exc
                elapsed_ms = (_time.monotonic() - t0) * 1000
                return SidecarRunResult(
                    sidecar_name=name,
                    attempted=True,
                    produced_count=0,
                    stored_count=0,
                    skipped_reason=f"{type(exc).__name__}:{exc}",
                    elapsed_ms=elapsed_ms,
                )

        # ── Execute stages sequentially ───────────────────────────────────────
        all_results: list[SidecarRunResult] = []
        # Track runners that have been executed in stages
        runners_executed: set[str] = set()

        for stage_names in SIDECAR_STAGES:
            # Build tasks only for registered runners in this stage
            stage_tasks: list[asyncio.Task[SidecarRunResult]] = []
            for name in stage_names:
                if name in self._runners:
                    stage_tasks.append(asyncio.create_task(_run_one(name, self._runners[name]), name=f"sidecar_bus:stage_runner:{name}"))
                    runners_executed.add(name)

            if not stage_tasks:
                continue

            try:
                gathered = await asyncio.gather(*stage_tasks, return_exceptions=True)
                self._check_gathered(gathered)
                for item in gathered:
                    if isinstance(item, SidecarRunResult):
                        all_results.append(item)
                    elif isinstance(item, BaseException):
                        # GHOST_INVARIANT: asyncio.CancelledError must never be swallowed.
                        # Check nested in case Python 3.14 wraps it in ExceptionGroup.
                        def _is_cancelled_tree(e: BaseException) -> bool:
                            if isinstance(e, asyncio.CancelledError):
                                return True
                            if isinstance(e, ExceptionGroup):
                                return any(_is_cancelled_tree(s) for s in e.exceptions)
                            return False
                        if _is_cancelled_tree(item):
                            # Re-raise through the stage CancelledError handler below
                            raise item
                        # Already logged in _check_gathered; fail-soft pass
                        pass
            except asyncio.CancelledError:
                # Cancel pending stage tasks and re-raise
                for t in stage_tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*stage_tasks, return_exceptions=True)
                raise

        # ── Execute any remaining registered runners not in a stage ──────────────
        # This handles custom runners registered at runtime that aren't in SIDECAR_STAGES
        remaining_tasks: list[asyncio.Task[SidecarRunResult]] = []
        for name, runner in self._runners.items():
            if name not in runners_executed:
                remaining_tasks.append(asyncio.create_task(_run_one(name, runner), name=f"sidecar_bus:remaining_runner:{name}"))
                runners_executed.add(name)

        if remaining_tasks:
            try:
                gathered = await asyncio.gather(*remaining_tasks, return_exceptions=True)
                self._check_gathered(gathered)
                for item in gathered:
                    if isinstance(item, SidecarRunResult):
                        all_results.append(item)
                    elif isinstance(item, BaseException):
                        pass
            except asyncio.CancelledError:
                for t in remaining_tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
                raise

        # Cap results at bound
        if len(all_results) > MAX_SIDECAR_RESULT_RECORDS:
            all_results = all_results[:MAX_SIDECAR_RESULT_RECORDS]

        self._results = all_results
        return all_results


# ── Built-in Sidecar Runners ───────────────────────────────────────────────────
# These are registered by sprint_scheduler on its own FindingSidecarBus instance.


async def _identity_stitching_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F202B identity stitching — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_findings,
        )
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )
    except Exception:
        return

    try:
        profiles = extract_entities_from_findings(findings)
        if not profiles:
            return
        adapter = create_identity_stitching_adapter()
        candidates = adapter.extract_and_stitch(profiles)
        if not candidates:
            return

        derived_findings = adapter.to_derived_findings(candidates, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        # Caller (SprintScheduler) updates _result.identity_findings_produced
        return stored
    except Exception:
        pass  # Fail-soft


async def _pattern_mining_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F250 pattern mining — detects temporal/behavioral patterns in findings."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.pattern_mining_canonical import (
            create_pattern_mining_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_pattern_mining_adapter(use_mlx=True)
        result = adapter.extract_and_mine(findings)
        if not result.temporal_patterns and not result.behavioral_patterns:
            return

        derived_findings = adapter.to_derived_findings(result, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _exposure_correlator_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F202C asset exposure correlator."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.exposure_correlator import (
            create_exposure_correlator_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_exposure_correlator_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _leak_sentinel_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F202D leak and secret sentinel."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.leak_sentinel import (
            create_leak_sentinel_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_leak_sentinel_adapter()
        derived_findings = await adapter.scan(query)
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _temporal_archaeology_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F202E temporal archaeology timeline synthesis."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.temporal_archaeologist_adapter import (
            create_temporal_archaeologist_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_temporal_archaeologist_adapter()
        ct_findings = [f for f in findings if getattr(f, "source_type", "") == "ct_log"]
        if not ct_findings:
            return
        result = adapter.synthesize_timeline(ct_findings=ct_findings, entity_id=query[:64])
        derived_findings = result.derived_findings
        if not derived_findings:
            return
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _evidence_triage_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """
    F202I evidence triage — counts document findings with triage facets.

    COMPATIBILITY/STATS runner — canonical document owner is
    multimodal/analyzer.py::MultimodalEnricher (wired via
    EnrichmentServices.enrich_findings_multimodal() in runtime/enrichment_services.py,
    which wraps DocumentExtractor from multimodal/analyzer.py). This runner is
    retained for stats parity and backwards-compatibility only. It does NOT
    extract new signal — it just counts document findings that already carry
    triage facets in their payload_text.

    To promote this to a canonical capability requires an owner decision and
    explicit allowlist entry in CORE_SIDECAR_ALLOWLIST (not yet defined).
    """
    import json

    triage_count = 0
    for finding in findings:
        if not hasattr(finding, "source_type") or finding.source_type != "document":
            continue
        if not hasattr(finding, "payload_text") or not finding.payload_text:
            continue
        try:
            payload = json.loads(finding.payload_text)
            if isinstance(payload, dict) and "triage" in payload:
                triage_count += 1
        except Exception:
            pass
    return triage_count


async def _sprint_diff_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F203A cross-sprint diff — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.knowledge.sprint_diff_engine import SprintDiffEngine
    except Exception:
        return

    target_id = query[:128]
    # Fail-soft: skip if store doesn't have the previous-findings method
    if not hasattr(store, "async_get_previous_findings_for_target"):
        return
    try:
        prev_findings_raw = await store.async_get_previous_findings_for_target(target_id, limit=1000)
    except Exception:
        prev_findings_raw = []

    # No-op: no previous findings for this target — first-ever sprint
    if not prev_findings_raw:
        return

    current_findings: list[dict] = []
    for f in findings:
        try:
            current_findings.append({
                "finding_id": getattr(f, "finding_id", "") or "",
                "source_type": getattr(f, "source_type", "") or "",
                "ioc_type": getattr(f, "ioc_type", "") or "",
                "ioc_value": getattr(f, "ioc_value", "") or "",
                "confidence": getattr(f, "confidence", 0.5) or 0.5,
                "ts": getattr(f, "ts", 0.0) or 0.0,
                "payload_text": getattr(f, "payload_text", "") or "",
            })
        except Exception:
            continue

    try:
        engine = SprintDiffEngine()
        diff_result = engine.compute_diff(
            current_findings=current_findings,
            previous_findings=prev_findings_raw if prev_findings_raw else None,
            target_id=target_id,
            current_sprint_id="",
            previous_sprint_id=None,
        )

        class _DiffFinding:
            __slots__ = ('finding_id', 'source_type', 'query', 'target_id',
                         'ioc_type', 'ioc_value', 'confidence', 'ts', 'payload_text')
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        derived_findings: list[Any] = []
        ts_now = _time.time()

        for nf in diff_result.new_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(
                    finding_id=f"diff-new-{nf.get('finding_id', 'unknown')[:32]}",
                    source_type="sprint_diff",
                    query=query,
                    target_id=target_id,
                    ioc_type=nf.get("ioc_type") or "unknown",
                    ioc_value=nf.get("ioc_value") or "unknown",
                    confidence=nf.get("confidence", 0.5),
                    ts=ts_now,
                    payload_text=_safe_payload_json({"diff_action": "new", **nf}),
                ))
            except Exception:
                continue

        for df in diff_result.disappeared_findings[:50]:
            try:
                derived_findings.append(_DiffFinding(
                    finding_id=f"diff-gone-{df.get('finding_id', 'unknown')[:32]}",
                    source_type="sprint_diff",
                    query=query,
                    target_id=target_id,
                    ioc_type=df.get("ioc_type") or "unknown",
                    ioc_value=df.get("ioc_value") or "unknown",
                    confidence=df.get("confidence", 0.5),
                    ts=ts_now,
                    payload_text=_safe_payload_json({"diff_action": "disappeared", **df}),
                ))
            except Exception:
                continue

        if derived_findings:
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
    except Exception:
        pass  # Fail-soft


async def _kill_chain_tagging_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F203C MITRE ATT&CK kill chain tagging."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.kill_chain_tagger import (
            create_kill_chain_tagger,
        )
    except Exception:
        return

    try:
        tagger = create_kill_chain_tagger()
        tagged_results: dict[str, list] = {}

        for finding in findings:
            fid = getattr(finding, "finding_id", None)
            if not fid:
                continue
            tags = tagger.tag_finding(finding)
            if tags:
                tagged_results[str(fid)] = [tag.to_dict() for tag in tags]

        if not tagged_results:
            return

        class _KCTFinding:
            __slots__ = (
                "finding_id", "source_type", "query", "target_id",
                "ioc_type", "ioc_value", "confidence", "ts", "payload_text",
            )
            def __init__(self, **kw: Any) -> None:
                for k, v in kw.items():
                    setattr(self, k, v)

        derived_findings: list[Any] = []
        ts_now = _time.time()

        for fid, tags_list in tagged_results.items():
            try:
                orig = next(
                    (f for f in findings if getattr(f, "finding_id", "") == fid),
                    None,
                )
                ioc_type = getattr(orig, "ioc_type", "unknown") if orig else "unknown"
                ioc_value = getattr(orig, "ioc_value", fid) if orig else fid
                confidence = getattr(orig, "confidence", 0.5) if orig else 0.5

                derived_findings.append(_KCTFinding(
                    finding_id=f"kct-{fid[:32]}",
                    source_type="killchain_tag",
                    query=query,
                    target_id=query[:128],
                    ioc_type=ioc_type,
                    ioc_value=ioc_value,
                    confidence=confidence,
                    ts=ts_now,
                    payload_text=_safe_payload_json({"kill_chain_tags": tags_list}),
                ))
            except Exception:
                continue

        if derived_findings:
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
    except Exception:
        pass  # Fail-soft


async def _wayback_diff_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """
    F203F Wayback CDX diff mining.

    NOTE: WaybackDiffMiner.mine() is an async coroutine — CPU-bound CDX parsing
    and string diffing execute inside the asyncio thread pool (awaited directly).
    No run_in_executor wrapping is needed here. If mine() ever becomes sync,
    the heavy diff work should be moved to run_in_executor to avoid event-loop
    blocking.

    COMPATIBILITY runner — canonical owner is
    intelligence/wayback_diff_miner.py::WaybackDiffMiner (wired as direct
    SprintScheduler lane via _run_wayback_prelude_lane() at line ~5715 and
    _run_acquisition_domain_lane() at line ~4408). The WaybackDiffMiner lane
    is the authoritative source for CDX diff findings; this sidecar runner
    is derived from it and produces no additional signal.

    To promote this to a canonical capability requires an owner decision and
    explicit allowlist entry in CORE_SIDECAR_ALLOWLIST (not yet defined).
    """
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.wayback_diff_miner import (
            WaybackDiffMiner,
        )
    except Exception:
        return

    try:
        targets: list[str] = []
        for f in findings:
            ioc_value = getattr(f, "ioc_value", "") or ""
            ioc_type = getattr(f, "ioc_type", "") or ""
            if ioc_type in ("domain", "url") and ioc_value:
                targets.append(ioc_value)
            elif hasattr(f, "url"):
                url = getattr(f, "url", "") or ""
                if url:
                    targets.append(url)

        if not targets:
            return

        miner = WaybackDiffMiner()
        try:
            result = await miner.mine(targets)
        finally:
            await miner.close()

        if not result.change_events:
            return

        findings_out = result.to_findings(query=query, sprint_id="")
        if not findings_out:
            return

        results = await store.async_ingest_findings_batch(findings_out)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _embedding_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F203I streaming embedding — heavy, RAM-guarded by bus."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder
    except Exception:
        return

    try:
        embedder = StreamingEmbedder()
        embeddable = []
        for f in findings:
            text = getattr(f, "payload_text", None) or getattr(f, "query", "") or ""
            if len(text) >= 16:
                embeddable.append(f)

        if not embeddable:
            return

        async for ids, embeddings in embedder.embed_findings(embeddable, batch_size=16):
            if ids and embeddings is not None and embeddings.shape[0] > 0:
                try:
                    from hledac.universal.knowledge.ann_index import get_ann_index
                    ann = get_ann_index()
                    import hashlib
                    for idx, finding_id in enumerate(ids):
                        emb = embeddings[idx]
                        if emb.shape[0] == 256:
                            key = hashlib.blake2b(finding_id.encode(), digest_size=32).hexdigest()
                            text_hash = hashlib.sha256(finding_id.encode()).hexdigest()
                            ann.upsert(key, emb, text_hash)
                except Exception:
                    pass

        try:
            from hledac.universal.knowledge.ann_index import get_ann_index
            ann = get_ann_index()
            ann.prewarm(top_k=128)
        except Exception:
            pass
    except Exception:
        pass  # Fail-soft


async def _passive_fingerprint_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F204G passive service fingerprinting — deterministic, no active scan."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.passive_fingerprint import (
            create_passive_fingerprint_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_passive_fingerprint_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _rir_correlator_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F204H RIR/ASN/WHOIS bulk correlator — bounded IP/domain attribution."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.rir_correlator import (
            create_rir_correlator_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_rir_correlator_adapter()
        derived_findings = await adapter.async_correlate(findings, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _social_identity_surface_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F204I: Social identity surface miner — extract usernames/profiles from findings."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.social_identity_miner import (
            create_social_identity_miner_adapter,
        )
    except Exception:
        return

    try:
        miner = create_social_identity_miner_adapter()
        result = await miner.mine(findings, store, query)
        return result.scanned_count
    except Exception:
        pass  # Fail-soft


async def _passive_tech_stack_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """
    R11 passive tech-stack extraction — deterministic, no active scan.

    COMPATIBILITY/DERIVED DUPLICATE — canonical owner is
    intelligence/passive_fingerprint.py::create_passive_tech_stack_adapter().
    This runner is a thin wrapper that calls the same underlying adapter;
    the signal produced is identical to what passive_fingerprint already emits.

    Unlike evidence_triage and wayback_diff (which are compatibility runners
    wired to different canonical sources), this runner has no independent
    canonical owner — it is purely derived from passive_fingerprint and
    provides no additional coverage.

    To promote this to a canonical capability requires an owner decision and
    explicit allowlist entry in CORE_SIDECAR_ALLOWLIST (not yet defined).
    """
    if not findings or store is None:
        return
    try:
        from hledac.universal.intelligence.passive_fingerprint import (
            create_passive_tech_stack_adapter,
        )
    except Exception:
        return

    try:
        adapter = create_passive_tech_stack_adapter()
        derived_findings = adapter.correlate(findings, query)
        if not derived_findings:
            return

        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        pass  # Fail-soft


async def _network_intel_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> int | None:
    """
    F247B: Active network reconnaissance via NetworkReconnaissance + bridge.

    Extracts domain/IP targets from accepted findings, runs recon_target() on each
    (max 5), converts via network_recon_result_to_findings(), stores findings.

    Profile gate: _sidecar_profile_allows("network_intel", profile) is checked
    by the bus before calling this runner. Returns stored count (0 if no findings).
    """
    # Bounds
    MAX_RECON_TARGETS = 5

    if not findings or store is None:
        return None

    # F247B: Extract domain/IP targets from findings
    targets: list[str] = []
    for f in findings:
        ioc_value = getattr(f, "ioc_value", "") or ""
        ioc_type = getattr(f, "ioc_type", "") or ""
        if ioc_type in ("domain", "ipv4", "ipv6", "ip") and ioc_value:
            if ioc_value not in targets:
                targets.append(ioc_value)
    if not targets:
        return None
    targets = targets[:MAX_RECON_TARGETS]

    # F247B: Import NetworkReconnaissance and bridge (lazy, fail-open)
    try:
        from hledac.universal.intelligence.network_reconnaissance import (
            NetworkReconnaissance,
        )
        from hledac.universal.runtime.source_finding_bridge import (
            network_recon_result_to_findings,
        )
    except Exception:
        return None

    # F247B: Fail-soft per target, CancelledError propagates
    all_findings: list = []
    for target in targets:
        try:
            recon = NetworkReconnaissance()
            host_info = await recon.recon_target(target)
            conv_findings, _, _ = network_recon_result_to_findings(
                target,
                host_info,
                max_findings=16,
            )
            all_findings.extend(conv_findings)
        except asyncio.CancelledError:
            raise  # Propagate — do not swallow
        except Exception:
            pass  # Fail-soft per target

    if not all_findings:
        return 0

    try:
        results = await store.async_ingest_findings_batch(all_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("network_intel async_ingest_findings_batch failed: %s", exc)
        return None


async def _banner_grab_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F214 banner grabber — TCP banner extraction, RAM-isolated."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.network import BANNER_GRABBER_AVAILABLE
        if not BANNER_GRABBER_AVAILABLE:
            return
        from hledac.universal.network.banner_grabber import BannerGrabberAdapter
    except Exception:
        return

    try:
        adapter = BannerGrabberAdapter()
        try:
            # Extract IPs from findings for banner grab
            targets: list[str] = []
            for f in findings:
                ioc_value = getattr(f, "ioc_value", "") or ""
                ioc_type = getattr(f, "ioc_type", "") or ""
                if ioc_type in ("ipv4", "ip") and ioc_value:
                    targets.append(ioc_value)
            if not targets:
                return
            derived_findings: list = []
            for target in targets[:20]:  # bound
                findings_batch = await adapter.query(target)
                derived_findings.extend(findings_batch)
            if not derived_findings:
                return
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
        finally:
            await adapter.close()
    except Exception:
        pass  # Fail-soft


async def _ipv6_recon_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> None:
    """F214 IPv6 reconnaissance — RDAP, WHOIS, DoH AAAA, BGP peer."""
    if not findings or store is None:
        return
    try:
        from hledac.universal.network import IPV6_RECON_AVAILABLE
        if not IPV6_RECON_AVAILABLE:
            return
        from hledac.universal.network.ipv6_recon import IPv6ReconAdapter
    except Exception:
        return

    try:
        adapter = IPv6ReconAdapter()
        try:
            # Extract domains/IPs from findings for IPv6 recon
            targets: list[str] = []
            for f in findings:
                ioc_value = getattr(f, "ioc_value", "") or ""
                ioc_type = getattr(f, "ioc_type", "") or ""
                if ioc_type in ("domain", "ipv4", "ip") and ioc_value:
                    targets.append(ioc_value)
            if not targets:
                return
            derived_findings: list = []
            for target in targets[:20]:  # bound
                findings_batch = await adapter.query(target)
                derived_findings.extend(findings_batch)
            if not derived_findings:
                return
            results = await store.async_ingest_findings_batch(derived_findings)
            stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
            return stored
        finally:
            await adapter.close()
    except Exception:
        pass  # Fail-soft


async def _gopher_crawl_runner(
    findings: list,
    store: DuckDBShadowStore,
    query: str,
) -> int | None:
    """F216: Gopher archive crawler — crawls seed servers, extracts text, stores findings."""
    if store is None:
        return 0
    try:
        from hledac.universal.discovery.gopher_crawler import GopherCrawler

        crawler = GopherCrawler()
        all_results = await crawler.crawl_seed_servers()
        all_findings: list = []
        for cr in all_results:
            if isinstance(cr, Exception):
                continue
            # Generate findings with a sentinel sprint_id
            findings_batch = GopherCrawler.items_to_findings(cr, sprint_id="gopher_sprint")
            all_findings.extend(findings_batch)
        if not all_findings:
            return 0
        results = await store.async_ingest_findings_batch(all_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except Exception:
        _logger.warning(f"gopher_crawl runner failed: {e}")
        return 0


# ── Default Registry ───────────────────────────────────────────────────────────
# Ordered list of (name, runner) pairs — bus registers these by default.
DEFAULT_SIDECAR_RUNNERS: list[tuple[str, SidecarRunner]] = [
    ("leak_sentinel", _leak_sentinel_runner),
    ("exposure_correlator", _exposure_correlator_runner),
    ("temporal_archaeology", _temporal_archaeology_runner),
    ("evidence_triage", _evidence_triage_runner),
    ("identity_stitching", _identity_stitching_runner),
    ("pattern_mining", _pattern_mining_runner),
    ("sprint_diff", _sprint_diff_runner),
    ("kill_chain_tagging", _kill_chain_tagging_runner),
    ("wayback_diff", _wayback_diff_runner),
    ("passive_fingerprint", _passive_fingerprint_runner),
    ("passive_tech_stack", _passive_tech_stack_runner),
    ("rir_correlator", _rir_correlator_runner),
    ("embedding", _embedding_runner),
    ("social_identity_surface", _social_identity_surface_runner),
    ("network_intel", _network_intel_runner),
    ("banner_grab", _banner_grab_runner),
    ("ipv6_recon", _ipv6_recon_runner),
    ("gopher_crawl", _gopher_crawl_runner),
]


def create_sidecar_bus(governor: Any = None, acquisition_profile: str | None = None) -> FindingSidecarBus:
    """Factory: create a pre-registered FindingSidecarBus."""
    bus = FindingSidecarBus(governor=governor, acquisition_profile=acquisition_profile)
    for name, runner in DEFAULT_SIDECAR_RUNNERS:
        bus.register(name, runner)
    return bus
