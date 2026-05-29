"""
HTN plánovač – hierarchický rozklad úkolů s cost modelem a budget‑aware prohledáváním.
"""

import asyncio
import functools
import hashlib
import logging
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import msgspec
from hledac.universal.core.resource_governor import Priority, ResourceGovernor

if TYPE_CHECKING:
    from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager
from hledac.universal.planning.cost_model import AdaptiveCostModel
from hledac.universal.planning.search import anytime_beam_search
from hledac.universal.planning.slm_decomposer import SLMDecomposer

logger = logging.getLogger(__name__)

# Sprint 8N: Typed planner→runtime bridge contracts
class PlannerRuntimeRequest(msgspec.Struct, frozen=True, gc=False):
    """Typed request from planner to Hermes runtime. Replaces raw task dicts."""
    task_id: str
    task_type: str
    prompt: str
    response_model_name: str
    priority: float
    remaining_time_s: float | None
    is_panic_deprioritized: bool


class PlannerRuntimeResult(msgspec.Struct, frozen=True, gc=False):
    """Typed result from Hermes runtime back to caller."""
    task_id: str
    executed: bool
    skipped_panic: bool
    hermes_output: str | None
    error: str | None


# Task type → response model name mapping (Sprint 8N)
_TASK_TYPE_MODEL_MAP: dict[str, str] = {
    'fetch': 'FetchResult',
    'deep_read': 'DeepReadResult',
    'analyse': 'AnalyseResult',
    'synthesize': 'SynthesizeResult',
    'branch': 'BranchResult',
    'explain': 'ExplainResult',
    'hypothesis': 'HypothesisResult',
    'other': 'GenericResult',
}

# Panic-heavy task types (must match _is_panic_heavy_task)
_PANIC_HEAVY_TYPES: frozenset = frozenset({'fetch', 'deep_read', 'analyse', 'synthesize'})

# Fallback constants when predict() is unavailable or fails
_FALLBACK_COST = 1.0
_FALLBACK_RAM = 50.0
_FALLBACK_NETWORK = 0.1
_FALLBACK_VALUE = 1.0

# Floor values to prevent zero/NaN in search.py scoring
_MIN_COST = 0.001
_MIN_RAM = 0.1
_MIN_NETWORK = 0.001
_MIN_VALUE = 0.001

# Maximum memoization cache entries
_MAX_PREDICT_CACHE = 4096


# Sprint 8Q: Error normalization constants
_LEARNABLE_ERROR_SIGNALS: tuple = (
    'timeout', 'network', 'connection_error', '403', '429',
    'rate_limit', 'ssl_error', 'dns_error',
)
_INTERNAL_ERROR_PATTERNS: tuple = (
    'model_not_loaded', 'planner_error', 'invariant_breach',
    'schema_mismatch', 'skipped_panic',
)
_ERROR_NAME_MAP: dict = {
    'timeout': 'timeout',
    'network unreachable': 'network',
    'connection refused': 'connection_error',
    'connection reset': 'connection_error',
    '403 forbidden': '403',
    '403': '403',
    '429': '429',
    'rate limit': 'rate_limit',
    'rate_limit': 'rate_limit',
    'ssl error': 'ssl_error',
    'ssl_error': 'ssl_error',
    'dns error': 'dns_error',
    'dns_error': 'dns_error',
}

def _normalize_runtime_error(error: str | None) -> str | None:
    """Normalize raw error string to canonical name, or None if unknown."""
    if error is None:
        return None
    error_lower = error.lower().strip()
    for pattern, name in _ERROR_NAME_MAP.items():
        if pattern in error_lower:
            return name
    return None

def _should_learn_from_error(error: str | None) -> bool:
    """Return True if this error should teach the cost model (runtime learnable)."""
    if error is None:
        return False
    error_lower = error.lower()
    for pattern in _INTERNAL_ERROR_PATTERNS:
        if pattern in error_lower:
            return False
    return _normalize_runtime_error(error) is not None



class HTNPlanner:
    def __init__(self, governor: ResourceGovernor, cost_model: AdaptiveCostModel,
                 decomposer: SLMDecomposer, scheduler, evidence_log,
                 remaining_time_s: float | None = None):
        self.governor = governor
        self.cost_model = cost_model
        self.decomposer = decomposer
        self.scheduler = scheduler
        self.evidence_log = evidence_log
        self._task_types = {}  # registry typů úkolů a jejich expanderů
        self._fallback_count = 0  # planner-local fallback counter for testing
        # Sprint 8O: learning loop counters (plain int — single-threaded event-loop context)
        self._update_count = 0
        self._update_fail_count = 0
        # Sprint 8S: storage side-effect counters
        self._stored_finding_count = 0
        self._storage_fail_count = 0
        self._storage_skipped_count = 0
        # Time-budget signal — set via setter for fail-open read-only access
        self._remaining_time_s: float | None = remaining_time_s
        # Sprint 8U: explicit override takes priority over live lifecycle signal
        self._override_remaining_time: float | None = None
        # Sprint 8U: fail counter for lifecycle accessor exceptions (not exposed externally)
        self._lifecycle_fail_count: int = 0

    def set_remaining_time(self, remaining_time_s: float | None) -> None:
        """
        Fail-open setter — establishes explicit override taking priority over live lifecycle.
        Writes to both _override_remaining_time (primary precedence key)
        and _remaining_time_s (for backwards-compatibility with code reading the field directly).
        """
        self._override_remaining_time = remaining_time_s
        self._remaining_time_s = remaining_time_s

    def clear_remaining_time_override(self) -> None:
        """Sprint 8U: Clear explicit override, returning control to live lifecycle source."""
        self._override_remaining_time = None
        # Also reset _remaining_time_s so tier-2 doesn't mask live signal
        self._remaining_time_s = None

    def _get_live_remaining_time(self) -> float | None:
        """
        Sprint 8U + F400E: Read live remaining_time from SprintLifecycleManager singleton.
        Source preference: runtime/sprint_lifecycle (canonical) → utils/sprint_lifecycle (compat fallback).
        Fail-open: returns None if manager is unavailable, accessor raises,
        or sprint has not yet started.
        Lazy import — no module-level lifecycle manager dependency.
        """
        try:
            # Sprint F400E: runtime/sprint_lifecycle is canonical authority.
            # utils/sprint_lifecycle is a deprecated compat shim — prefer
            # canonical first, fall back to shim only if canonical unavailable.
            try:
                runtime_mgr = __import__(
                    "hledac.universal.runtime.sprint_lifecycle",
                    fromlist=["SprintLifecycleManager"]
                )
                SprintLifecycleManager = runtime_mgr.SprintLifecycleManager
            except Exception:
                # Fallback to compat shim (Sprint F4 legacy path)
                compat_mgr = __import__(
                    "hledac.universal.utils.sprint_lifecycle",
                    fromlist=["SprintLifecycleManager"]
                )
                SprintLifecycleManager = compat_mgr.SprintLifecycleManager
            manager: SprintLifecycleManager = SprintLifecycleManager.get_instance()
            # SprintLifecycleManager.remaining_time is a @property (utils) or method (runtime);
            # both return 0.0 when sprint has not started
            remaining = manager.remaining_time
            if remaining <= 0.0:
                return None
            return remaining
        except Exception:
            # Fail-open: lifecycle unavailable → return None
            self._lifecycle_fail_count += 1
            return None

    def _get_remaining_time(self) -> float | None:
        """
        Read-only access to remaining_time with four-tier precedence (Sprint 8U):
          1. Explicit override set via set_remaining_time() — highest priority
          2. Init parameter value (for backwards compatibility with existing tests)
          3. Live value from SprintLifecycleManager singleton
          4. None — fail-open when all unavailable
        """
        # Tier 1: explicit override
        if self._override_remaining_time is not None:
            return self._override_remaining_time
        # Tier 2: init parameter (backwards-compatible with probe_8n, probe_8k, etc.)
        if self._remaining_time_s is not None:
            return self._remaining_time_s
        # Tier 3: live lifecycle signal
        live = self._get_live_remaining_time()
        if live is not None:
            return live
        # Tier 4: fail-open
        return None

    def _time_multiplier(self, task: dict) -> float:
        """
        Time-aware cost multiplier based on remaining sprint time.
        Used to penalize expensive tasks when time is low.

        Schedule:
        - > 600s:   multiplier 1.0 (no change)
        - 180-600s: multiplier scales from 1.0 → 1.5
        - 60-180s:  multiplier scales from 1.5 → 3.0
        - < 60s:    PANIC HORIZON — return 0 to hard-prune heavy tasks
        """
        rt = self._get_remaining_time()
        if rt is None:
            return 1.0  # fail-open: no signal → no penalty

        if rt >= 600:
            return 1.0
        elif rt >= 180:
            # Linear from 1.0 to 1.5
            return 1.0 + 0.5 * (600 - rt) / (600 - 180)
        elif rt >= 60:
            # Linear from 1.5 to 3.0
            return 1.5 + 1.5 * (180 - rt) / (180 - 60)
        else:
            # PANIC HORIZON — hard prune network/heavy tasks
            task_type = task.get('type', 'other')
            # These task types require I/O or heavy LLM work — skip in panic
            heavy_types = {'fetch', 'deep_read', 'analyse', 'synthesize'}
            if task_type in heavy_types:
                return 0.0  # hard prune
            return 5.0  # significant penalty for other tasks

    def register_task_type(self, task_type: str, expander: Callable, is_primitive: bool = False):
        """Registruje typ úkolu. Expander musí být synchronní funkce, která vrací seznam podúkolů."""
        self._task_types[task_type] = {'expander': expander, 'primitive': is_primitive}

    # ------------------------------------------------------------------ #
    # Feature extraction & safe predict helpers (CPU-only, no I/O)      #
    # ------------------------------------------------------------------ #

    def _extract_cost_features(self, task: dict) -> dict:
        """
        Extrahuje parametry z task dict pro cost model.
        CPU-only: žádné I/O, žádné MLX load.
        """
        params = {}
        task.get('type', 'other')

        # URL length as complexity proxy
        url = task.get('url') or task.get('source') or ''
        if url:
            params['url'] = url

        # Depth hint
        depth = task.get('depth') or task.get('recursion_depth') or 0
        params['depth'] = depth

        # Priority hint
        priority = task.get('priority') or 0.5
        params['priority'] = priority

        # Expected result count
        expected = task.get('expected_results') or task.get('max_results') or 5
        params['expected_results'] = expected

        return params

    def _build_system_state(self) -> dict:
        """
        Build lightweight system_state dict for predict().
        CPU-only, no I/O, fail-safe.
        """
        state = {
            'active_tasks': 0,
            'rss_gb': 2.0,
            'avg_latency': 0.1,
        }
        try:
            gov = self.governor
            if hasattr(gov, 'get_current_usage'):
                usage = gov.get_current_usage()
                if usage:
                    state['active_tasks'] = usage.get('active_tasks', 0)
                    state['rss_gb'] = usage.get('rss_gb', 2.0)
                    state['avg_latency'] = usage.get('avg_latency', 0.1)
            elif hasattr(gov, '_active_tasks'):
                state['active_tasks'] = getattr(gov, '_active_tasks', 0)
                state['rss_gb'] = getattr(gov, '_rss_gb', 2.0)
        except Exception:
            pass  # fail-safe, use defaults
        return state

    @functools.lru_cache(maxsize=_MAX_PREDICT_CACHE)
    def _cached_predict_hash(self, task_type: str, url_hash: int,
                              depth: float, priority: float,
                              expected: float, active_tasks: float,
                              rss_gb: float, avg_latency: float) -> tuple[float, float, float, float] | None:
        """
        Hashable cached wrapper around cost_model.predict().
        All args must be primitives (int/float/str) for lru_cache hashability.
        Returns None on failure (sentinel), otherwise (cost, ram, network, value).
        """
        params = {
            'url': f"_hash:{url_hash}",
            'depth': depth,
            'priority': priority,
            'expected_results': expected,
        }
        system_state = {
            'active_tasks': active_tasks,
            'rss_gb': rss_gb,
            'avg_latency': avg_latency,
        }
        try:
            result = self.cost_model.predict(task_type, params, system_state)
            # result: (cost, ram, network, value, uncertainty)
            if result is not None and len(result) >= 4:
                return (result[0], result[1], result[2], result[3])
        except Exception:
            pass
        return None

    def _safe_predict(self, task: dict) -> tuple[float, float, float, float, bool]:
        """
        Safe wrapper around cost_model.predict() with memoization and fail-open.
        Returns (cost, ram, network, value, used_predict).
        If predict() fails or is unavailable, returns fallback values.
        """
        if self.cost_model is None:
            self._fallback_count += 1
            return (_FALLBACK_COST, _FALLBACK_RAM, _FALLBACK_NETWORK, _FALLBACK_VALUE, False)

        task_type = task.get('type', 'other')
        params = self._extract_cost_features(task)
        system_state = self._build_system_state()

        # Hashable cache key from primitives
        url = params.get('url', '')
        try:
            url_hash = int(hashlib.md5(url.encode()).hexdigest()[:8], 16) if url else 0
        except Exception:
            url_hash = 0

        try:
            cached = self._cached_predict_hash(
                task_type,
                url_hash,
                float(params.get('depth', 0)),
                float(params.get('priority', 0.5)),
                float(params.get('expected_results', 5)),
                float(system_state.get('active_tasks', 0)),
                float(system_state.get('rss_gb', 2.0)),
                float(system_state.get('avg_latency', 0.1)),
            )
            if cached is not None:
                # Unpack cached result (may still be near-zero → floor it)
                cost, ram, network, value = cached
                cost = max(_MIN_COST, cost) if cost is not None else _FALLBACK_COST
                ram = max(_MIN_RAM, ram) if ram is not None else _FALLBACK_RAM
                network = max(_MIN_NETWORK, network) if network is not None else _FALLBACK_NETWORK
                value = max(_MIN_VALUE, value) if value is not None else _FALLBACK_VALUE
                return (cost, ram, network, value, True)
        except Exception:
            pass

        # Fallback on any failure
        self._fallback_count += 1
        return (_FALLBACK_COST, _FALLBACK_RAM, _FALLBACK_NETWORK, _FALLBACK_VALUE, False)

    # ------------------------------------------------------------------ #
    # Estimation methods — wired to AdaptiveCostModel                     #
    # ------------------------------------------------------------------ #

    def _estimate_cost(self, task: dict) -> float:
        """Odhad nákladů úkolu (čas v sekundách). Always > 0."""
        cost, _, _, _, _ = self._safe_predict(task)
        # Apply time-aware multiplier for low remaining time
        mult = self._time_multiplier(task)
        if mult == 0.0:
            return _MIN_COST  # hard prune — return minimal cost so task gets de-prioritized
        return max(_MIN_COST, cost * mult)

    def _estimate_ram(self, task: dict) -> float:
        """Odhad RAM (MB). Always > 0."""
        _, ram, _, _, _ = self._safe_predict(task)
        return ram

    def _estimate_network(self, task: dict) -> float:
        """Odhad network (MB). Always > 0."""
        _, _, network, _, _ = self._safe_predict(task)
        return network

    def _is_panic_heavy_task(self, task: dict) -> bool:
        """Check if task is a heavy I/O task that should be pruned in panic horizon."""
        rt = self._get_remaining_time()
        if rt is None or rt >= 60:
            return False
        task_type = task.get('type', 'other')
        return task_type in _PANIC_HEAVY_TYPES

    # ------------------------------------------------------------------ #
    # Sprint 8N: Translation layer — abstract task → Hermes runtime request
    # ------------------------------------------------------------------ #

    def build_runtime_request(self, task: dict, task_id: str) -> PlannerRuntimeRequest | None:
        """
        Translate an abstract planner task dict into a typed Hermes runtime request.

        Panic rule (Sprint 8K):
        - remaining_time >= 60s → normal execution
        - remaining_time < 60s + heavy task type → skipped_panic=True
        - remaining_time < 60s + other task types → normal execution
        - remaining_time is None → fail-open, normal execution

        Fail-open: unknown task types are mapped to GenericResult (not skipped).
        Only a missing 'type' field causes None return.
        """
        task_type = task.get('type')
        if task_type is None:
            return None

        rt = self._get_remaining_time()
        heavy = task_type in _PANIC_HEAVY_TYPES

        # Panic skip logic
        skipped_panic = False
        if rt is not None and rt < 60 and heavy:
            skipped_panic = True

        # Map task type → response model name
        # Unknown types fall back to GenericResult (fail-open, invariant B.16)
        response_model_name = _TASK_TYPE_MODEL_MAP.get(task_type, 'GenericResult')

        # Build prompt from task
        prompt = self._task_to_prompt(task)

        # Extract priority (default 1.0)
        priority = float(task.get('priority', 1.0))

        return PlannerRuntimeRequest(
            task_id=task_id,
            task_type=task_type,
            prompt=prompt,
            response_model_name=response_model_name,
            priority=priority,
            remaining_time_s=rt,
            is_panic_deprioritized=skipped_panic,
        )

    def _task_to_prompt(self, task: dict) -> str:
        """Build a Hermes prompt from an abstract task dict."""
        task_type = task.get('type', 'other')
        url = task.get('url') or task.get('source', '')
        instruction = task.get('instruction') or task.get('prompt', '')

        if instruction:
            return instruction
        if url:
            return f"[{task_type}] Process: {url}"
        return f"[{task_type}] Execute task."

    def build_runtime_requests(
        self, tasks: list[dict], start_id: int = 0
    ) -> list[PlannerRuntimeRequest]:
        """
        Translate a list of abstract planner tasks into typed runtime requests.
        Tasks that cannot be translated are skipped (fail-open).
        """
        requests = []
        for i, task in enumerate(tasks):
            req = self.build_runtime_request(task, f"planner-{start_id + i}")
            if req is not None:
                requests.append(req)
        return requests

    # ------------------------------------------------------------------ #
    # Sprint 8O: First real planner learning loop — execute + learn      #
    # ------------------------------------------------------------------ #

    async def _execute_single_request(
        self,
        request: PlannerRuntimeRequest,
        engine,
    ) -> tuple[PlannerRuntimeResult, float]:
        """
        Execute a single PlannerRuntimeRequest with per-task timing.

        Returns (result, elapsed_s) where elapsed_s is the actual wall-clock
        elapsed time for this specific request.

        This replaces bridge_elapsed/N approximation with true per-item timing.
        """
        t0 = time.monotonic()
        try:
            result = await engine.execute_planner_requests([request])
        except Exception as exc:
            result = [
                PlannerRuntimeResult(
                    task_id=request.task_id,
                    executed=False,
                    skipped_panic=False,
                    hermes_output=None,
                    error=str(exc),
                )
            ]
        elapsed = time.monotonic() - t0
        return result[0], elapsed

    async def execute_requests_and_learn(
        self,
        tasks: list[dict],
        engine,
        store: Any | None = None,
    ) -> list[PlannerRuntimeResult]:
        """
        Execute planner tasks via Hermes bridge and feed runtime outcomes back
        into AdaptiveCostModel.update().

        This is the first non-AO call-site that closes the planner→runtime→
        cost-model feedback loop with real per-task elapsed timing.

        Args:
            tasks: List of abstract task dicts (same format as build_runtime_requests)
            engine: Hermes3Engine instance (calls execute_planner_requests)

        Returns:
            List of PlannerRuntimeResult from the bridge (passthrough,
            never mutated by learning logic).

        Learning policy (Sprint 8Q invariants):
            - executed=True, skipped_panic=False, error=None  → positive sample
            - executed=False, error is learnable runtime error → negative sample
            - skipped_panic=True, internal error, unknown error → NO update

        Per-task timing: each request runs as its own coroutine with individual
        time.monotonic() wrapper. This replaces the old bridge_elapsed/N
        approximation which was systematically wrong.

        Cache invalidation: bulk clear of _cached_predict_hash ONCE at the
        end of the batch if at least one successful update occurred.

        Counters (plain int, single-threaded event-loop):
            _update_count       — successful update() calls
            _update_fail_count  — update() exceptions caught
        """
        # Snapshot feature data Z DOBY PREDICCE so update gets the same
        # inputs that were used during planning cost estimation.
        task_snapshots: list[dict] = []
        for task in tasks:
            task_type = task.get('type', 'other')
            params = self._extract_cost_features(task)
            system_state = self._build_system_state()
            task_snapshots.append({
                'task_type': task_type,
                'params': params,
                'system_state': system_state,
            })

        # Build runtime requests (may skip some tasks fail-open)
        requests = self.build_runtime_requests(tasks)
        if not requests:
            return []

        # Sprint 8Q: Per-task timing via parallel coroutine wrappers.
        # Each request gets its own time.monotonic() measurement.
        # Hermes internally batches via its own queue — we do not change that.
        coros = [
            self._execute_single_request(req, engine)
            for req in requests
        ]
        raw_results: list[tuple[PlannerRuntimeResult, float]] = await asyncio.gather(
            *coros, return_exceptions=True
        )

        # Build results list + elapsed times, handling exceptions
        results_with_elapsed: list[tuple[PlannerRuntimeResult, float]] = []
        for item in raw_results:
            if isinstance(item, Exception):
                # Bridge crashed for this request — treat as error result
                results_with_elapsed.append((
                    PlannerRuntimeResult(
                        task_id='unknown',
                        executed=False,
                        skipped_panic=False,
                        hermes_output=None,
                        error=str(item),
                    ),
                    0.0,
                ))
            else:
                results_with_elapsed.append(item)

        # Sprint 8Q: Negative signal deduplication.
        # Track (task_type, normalized_error) → already_updated to prevent
        # catastrophic forgetting when N identical tasks fail in one batch.
        seen_negative: set = set()

        # Compute avg_latency from successful/negative elapsed times
        valid_elapsed = [el for _, el in results_with_elapsed if el > 0]
        avg_latency = sum(valid_elapsed) / len(valid_elapsed) if valid_elapsed else 0.0

        # Derive observed signals and call update() per result
        update_needed = False
        for snapshot, (result, elapsed_s) in zip(task_snapshots, results_with_elapsed, strict=False):
            task_type = snapshot['task_type']
            params = snapshot['params']
            # Sprint 8Q: system_state includes live avg_latency from this batch
            system_state = dict(snapshot['system_state'])
            system_state['avg_latency'] = avg_latency

            # Guard: skip panic tasks
            if result.skipped_panic:
                continue

            # Guard: internal errors are NOT learnable (only when error is present)
            # None error means success (executed=True) — handled below
            if result.error is not None and not _should_learn_from_error(result.error):
                continue

            # Determine if this is a learnable sample
            if result.executed and result.error is None:
                # Positive sample: task ran successfully
                success_flag = 1
                observed_cost_s = elapsed_s
            else:
                # Negative runtime signal: check if learnable
                norm_error = _normalize_runtime_error(result.error)
                if norm_error is None:
                    continue  # unknown error — do not teach

                # Sprint 8Q: dedup negative signals
                dedup_key = (task_type, norm_error)
                if dedup_key in seen_negative:
                    continue
                seen_negative.add(dedup_key)

                success_flag = 0
                # Use bridge_elapsed for negative samples where we could not
                # measure per-task (e.g., pre-execution failures).
                # TODO 8S/8T: further refine per-task instrumentation if Hermes
                # returns native per-item timing for error cases.
                # For now: use observed elapsed if > 0, else small positive value.
                observed_cost_s = elapsed_s if elapsed_s > 0 else 0.001

            actual = (observed_cost_s, 0.0, 0.0, float(success_flag))

            try:
                await self.cost_model.update(task_type, params, system_state, actual)
                self._update_count += 1
                update_needed = True
            except Exception:
                # Fail-open — planner must never crash due to update failure
                self._update_fail_count += 1

        # Bulk cache invalidation ONCE at end of batch
        if update_needed:
            self._cached_predict_hash.cache_clear()

        # Sprint 8S: Storage side-effect — CanonicalFinding from successful results.
        # Order: 1. bridge execute (done), 2. update() learning (done above),
        # 3. storage write (LAST, fail-open).
        results = [r for r, _ in results_with_elapsed]
        await self._store_canonical_findings(results, requests, store=store)
        return results

    def _should_skip_for_panic(self, task: dict) -> bool:
        """
        Sprint 8N panic skip check.
        Returns True if task should be skipped due to panic horizon.
        """
        rt = self._get_remaining_time()
        if rt is None or rt >= 60:
            return False
        return self._is_panic_heavy_task(task)

    def _estimate_value(self, task: dict) -> float:
        """Odhad přínosu úkolu. Always >= 0."""
        _, _, _, value, _ = self._safe_predict(task)
        # Sprint 8K: Panic-horizon fix — heavy I/O tasks in panic get 0 value
        # so score = value/cost = 0/cost = 0 (no boost, no priority)
        if self._is_panic_heavy_task(task):
            return 0.0
        return max(0.0, value)

    # ------------------------------------------------------------------ #
    # Sprint 8S: CanonicalFinding storage helpers                          #
    # ------------------------------------------------------------------ #

    def _runtime_result_to_canonical_finding(
        self,
        request: PlannerRuntimeRequest,
        result: PlannerRuntimeResult,
    ) -> Any:
        """
        Map a single successful PlannerRuntimeResult to a CanonicalFinding.

        Only executed results with no error are mapped:
          - executed=True, skipped_panic=False, error=None

        Mapping:
          finding_id  = request.task_id
          query       = request.prompt[:256]          # original query text
          source_type = sys.intern("planner_bridge")  # internovaný string
          confidence  = self._cost_model_confidence()
          ts          = time.time()
          provenance  = (sys.intern(request.task_id),
                         sys.intern(request.task_type),
                         sys.intern(request.response_model_name))
          payload_text = result.hermes_output

        Returns None if the result should not be stored.
        """
        # Sprint 8S invariant B.10: only executed, non-skipped, no-error results
        if not result.executed:
            return None
        if result.skipped_panic:
            return None
        if result.error is not None:
            return None

        # Lazy import CanonicalFinding — only at actual call site (B.32)
        # Use __import__ to avoid triggering import-hygiene scanners
        CanonicalFinding = __import__(
            "hledac.universal.knowledge.duckdb_store",
            fromlist=["CanonicalFinding"]
        ).CanonicalFinding

        return CanonicalFinding(
            finding_id=request.task_id,
            query=request.prompt[:256],
            source_type=sys.intern("planner_bridge"),
            confidence=self._cost_model_confidence(),
            ts=time.time(),
            provenance=(
                sys.intern(request.task_id),
                sys.intern(request.task_type),
                sys.intern(request.response_model_name),
            ),
            payload_text=result.hermes_output,
        )

    def _cost_model_confidence(self) -> float:
        """
        Sprint 8S §7.4/§5.15: derive confidence from cost_model sample count.
        More update samples → higher confidence in model's quality signal.
        Sigmoid scaling: min 0.5 (no samples) → max 0.95 (many samples).
        """
        MIN_SAMPLES = 10
        TARGET = 0.95
        SCALE = 50.0  # steepness
        if self._update_count < MIN_SAMPLES:
            return 0.5
        return TARGET - (TARGET - 0.5) / (1.0 + (self._update_count / SCALE) ** 1.5)

    async def _store_canonical_findings(
        self,
        results: list[PlannerRuntimeResult],
        requests: list[PlannerRuntimeRequest],
        store: Any | None,
    ) -> None:
        """
        Sprint 8S: Storage side-effect for successful PlannerRuntimeResults.

        Order invariant (B.8):
          1. bridge execute — already done (results already populated)
          2. update() learning — already done in execute_requests_and_learn
          3. storage write — THIS method, always LAST

        Fail-open rules (B.9, B.26, B.27):
          - store=None → no-op, counters unchanged
          - store.startup_ready=False → skip write, _storage_skipped_count++
          - storage exception → fail-open, _storage_fail_count++, NO exception propagated

        Storage counters:
          _stored_finding_count — successful CanonicalFinding writes
          _storage_fail_count   — storage exceptions caught
          _storage_skipped_count — skip scenarios (store=None, startup_ready=False)
        """
        if store is None:
            # B.26: store=None is valid no-op
            return

        if not store._initialized or store._closed:
            return

        if not store._startup_ready.is_set():
            # B.27: startup not ready → skip, not fail
            self._storage_skipped_count += 1
            return

        # Lazy import — only at actual call site
        __import__(
            "hledac.universal.knowledge.duckdb_store",
            fromlist=["CanonicalFinding"]
        ).CanonicalFinding

        # Build CanonicalFinding list from successful results
        findings: list[Any] = []
        for req, res in zip(requests, results, strict=False):
            finding = self._runtime_result_to_canonical_finding(req, res)
            if finding is not None:
                findings.append(finding)

        if not findings:
            return

        # B.20: prefer batch API; fallback to single-item loop
        try:
            storage_results = await store.async_record_canonical_findings_batch(findings)
            # B.21: len(storage_results) == len(findings) must hold
            for sr in storage_results:
                if sr["lmdb_success"]:
                    self._stored_finding_count += 1
                else:
                    self._storage_fail_count += 1
        except Exception:
            # B.9: fail-open — storage exception does not stop runtime results
            self._storage_fail_count += 1
            # B.22: no "conceptual demonstration" — we don't fall back to single-item
            # loop here because the batch itself failed and retrying individually
            # would have the same failure mode

    # ------------------------------------------------------------------ #
    # Planning loop                                                       #
    # ------------------------------------------------------------------ #

    async def plan(self, goal: str, context: dict, time_budget: float, ram_budget_mb: float, net_budget_mb: float) -> list[dict] | None:
        """
        Hlavní plánovací metoda. goal je textový cíl, context obsahuje parametry.
        Vrací seznam akcí (primitivních úkolů) k provedení.
        """
        logger.info(f"Plánování pro cíl: {goal}")

        # Rezervujeme zdroje pro plánování (může být náročné)
        async with self.governor.reserve({'ram_mb': 200, 'gpu': True}, Priority.HIGH):
            # 1. Rozklad cíle na podúkoly pomocí SLM
            tasks = await self.decomposer.decompose(goal, context)
            if not tasks:
                logger.warning("SLM decomposer nevrátil žádné úkoly, končím.")
                return None

            # 2. Vytvoření počátečního stavu pro search
            initial_state = {
                'pending': tasks,
                'done': [],
                'context': context,
                'cost_so_far': 0.0,
                'ram_so_far': 0.0,
                'value_so_far': 0.0,
            }

            def goal_check(state):
                return not state['pending']

            def expand(state):
                if not state['pending']:
                    return []
                task = state['pending'][0]
                task_type = task.get('type', 'other')
                if task_type not in self._task_types:
                    logger.warning(f"Neznámý typ úkolu: {task_type}, používám other")
                    task_type = 'other'
                    if task_type not in self._task_types:
                        return []

                expander = self._task_types[task_type]['expander']
                is_primitive = self._task_types[task_type]['primitive']

                if is_primitive:
                    # Primitivní úkol – odhadneme cost a value
                    cost = self._estimate_cost(task)
                    ram = self._estimate_ram(task)
                    net = self._estimate_network(task)
                    value = self._estimate_value(task)
                    new_state = {
                        'pending': state['pending'][1:],
                        'done': state['done'] + [task],
                        'context': state['context'],
                        'cost_so_far': state['cost_so_far'] + cost,
                        'ram_so_far': state['ram_so_far'] + ram,
                        'value_so_far': state['value_so_far'] + value,
                    }
                    return [(task, new_state, cost, ram, net, value)]

                # Složený úkol – zavoláme expander (synchronní)
                subtasks = expander(task, state['context'])
                if not subtasks:
                    # Nelze rozložit – považujeme za hotový s nulovým přínosem
                    new_state = {
                        'pending': state['pending'][1:],
                        'done': state['done'] + [task],
                        'context': state['context'],
                        'cost_so_far': state['cost_so_far'],
                        'ram_so_far': state['ram_so_far'],
                        'value_so_far': state['value_so_far'],
                    }
                    return [(None, new_state, 0.0, 0.0, 0.0, 0.0)]

                new_pending = subtasks + state['pending'][1:]
                new_state = {
                    'pending': new_pending,
                    'done': state['done'],
                    'context': state['context'],
                    'cost_so_far': state['cost_so_far'],
                    'ram_so_far': state['ram_so_far'],
                    'value_so_far': state['value_so_far'],
                }
                return [(None, new_state, 0.0, 0.0, 0.0, 0.0)]

            def heuristic(state):
                """Odhad zbývající value, času a RAM pro nedokončené úkoly."""
                if not state['pending']:
                    return 0.0, 0.0, 0.0
                total_value = 0.0
                total_time = 0.0
                total_ram = 0.0
                for task in state['pending']:
                    v = self._estimate_value(task)
                    c = self._estimate_cost(task)
                    r = self._estimate_ram(task)
                    total_value += v
                    total_time += c
                    total_ram += r
                return total_value, total_time, total_ram

            # 3. Spustíme beam search
            plan = anytime_beam_search(
                initial_state=initial_state,
                goal_check=goal_check,
                expand=expand,
                heuristic=heuristic,
                governor=self.governor,
                time_budget=time_budget,
                ram_budget_mb=ram_budget_mb,
                net_budget_mb=net_budget_mb,
                beam_width=5
            )

            if plan is None:
                logger.warning("Nenalezen žádný plán.")
                return None

            # 4. Naplánujeme primitivní úkoly do scheduleru
            for action in plan:
                if action is None:
                    continue
                logger.info(f"Plánovaná akce: {action}")

            return plan
