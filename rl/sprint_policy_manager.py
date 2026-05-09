"""
SprintPolicyManager — opt-in RL sprint policy layer.

Plugged into SprintScheduler.run() as a policy advisor.
Does NOT own lifecycle or execution — only provides action hints.

Design:
- Disabled by default — zero effect on sprint behavior when not enabled
- Every 5th sprint is exploration (ACTION_DEEP_DIVE), rest are exploitation
- Policy persists via JSON file so state survives instance restarts
- Reward computed from real SprintSchedulerResult fields, not placeholder telemetry

Canonical owner: runtime/sprint_scheduler.py (integration point)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

from hledac.universal.rl.actions import ACTION_CONTINUE, ACTION_DEEP_DIVE

log = logging.getLogger(__name__)

# Path for persisted policy state
_POLICY_PATH = Path(__file__).parent / ".sprint_policy_state.json"

# Exploration interval (every N sprints)
_EXPLORATION_INTERVAL = 5

# Epsilon for epsilon-greedy exploration
_DEFAULT_EPSILON = 0.1


@dataclass
class SprintPolicyState:
    """Serialized policy state persisted to disk."""
    sprint_sequence_number: int = 0       # how many sprints have run
    epsilon: float = _DEFAULT_EPSILON     # current epsilon
    total_reward: float = 0.0             # cumulative reward
    sprint_rewards: list[float] = field(default_factory=list)  # recent rewards


class SprintPolicyManager:
    """
    Opt-in RL policy advisor for sprint execution.

    Integration contract:
      - call update(result) after each sprint → updates internal state + persists
      - call should_explore() before next sprint → returns bool action hint
      - Enabled = True means policy is active; Enabled = False means all methods are no-op

    State persists via JSON at _POLICY_PATH — survives instance restarts.
    """

    def __init__(
        self,
        enabled: bool = False,
        policy_path: Optional[Path] = None,
        epsilon: float = _DEFAULT_EPSILON,
        exploration_interval: int = _EXPLORATION_INTERVAL,
    ) -> None:
        """
        Args:
            enabled: If False (default), all methods are no-op — no effect on sprint behavior
            policy_path: Override path for persisted state; defaults to _POLICY_PATH
            epsilon: Epsilon for epsilon-greedy fallback
            exploration_interval: Every N sprints is exploration (default 5)
        """
        self._enabled = enabled
        self._policy_path = policy_path or _POLICY_PATH
        self._epsilon = epsilon
        self._exploration_interval = exploration_interval
        self._state = SprintPolicyState()
        self._loaded = False

        if self._enabled:
            self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load persisted state from disk. No-op if file missing or invalid."""
        if self._loaded:
            return
        try:
            if self._policy_path.exists():
                data = json.loads(self._policy_path.read_text())
                self._state = SprintPolicyState(
                    sprint_sequence_number=data.get("sprint_sequence_number", 0),
                    epsilon=data.get("epsilon", _DEFAULT_EPSILON),
                    total_reward=data.get("total_reward", 0.0),
                    sprint_rewards=data.get("sprint_rewards", []),
                )
                log.debug(
                    f"[SprintPolicyManager] Loaded state: "
                    f"sprint #{self._state.sprint_sequence_number}, "
                    f"epsilon={self._state.epsilon:.3f}"
                )
        except Exception as e:
            log.warning(f"[SprintPolicyManager] Failed to load policy state: {e}")
            self._state = SprintPolicyState()
        self._loaded = True

    def _save(self) -> None:
        """Persist state to disk. Fail-safe — do not crash on write errors."""
        if not self._enabled:
            return
        try:
            data = {
                "sprint_sequence_number": self._state.sprint_sequence_number,
                "epsilon": self._state.epsilon,
                "total_reward": self._state.total_reward,
                "sprint_rewards": self._state.sprint_rewards[-100:],  # keep last 100
            }
            self._policy_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"[SprintPolicyManager] Failed to save policy state: {e}")

    # ── Reward computation ───────────────────────────────────────────────────

    def _compute_reward(self, result: "SprintSchedulerResult") -> float:
        """
        Compute reward from real SprintSchedulerResult fields.

        Fields used:
          - cycles_completed: positive signal (more cycles = more work done)
          - accepted_findings: primary value signal
          - unique_entry_hashes_seen: coverage signal
          - duplicate_entry_hashes_skipped: dedup efficiency signal
          - aborted / abort_reason: negative signal if terminated early

        Returns reward in range [-10, 100].
        """
        reward = 0.0

        # Primary value: accepted findings (positive)
        reward += result.accepted_findings * 2.0

        # Coverage: unique entries seen
        reward += min(result.unique_entry_hashes_seen, 1000) * 0.1

        # Dedup efficiency: duplicates skipped relative to total
        total_seen = result.unique_entry_hashes_seen + result.duplicate_entry_hashes_skipped
        if total_seen > 0:
            dup_ratio = result.duplicate_entry_hashes_skipped / total_seen
            reward += dup_ratio * 1.0

        # Cycle completion bonus
        reward += result.cycles_completed * 0.5

        # Abort penalty
        if result.aborted:
            reward -= 5.0

        # Time efficiency (cycles per sprint duration as proxy)
        if result.cycles_completed > 0:
            reward += min(result.cycles_completed / 10.0, 2.0)

        return max(-10.0, min(reward, 100.0))

    # ── Public API ──────────────────────────────────────────────────────────

    def update(self, result: "SprintSchedulerResult") -> None:
        """
        Update policy state from the completed sprint result.

        Called by SprintScheduler after run() returns.
        Does nothing if policy is disabled.
        """
        if not self._enabled:
            return

        reward = self._compute_reward(result)
        self._state.sprint_sequence_number += 1
        self._state.total_reward += reward
        self._state.sprint_rewards.append(reward)

        # Epsilon decay (simple multiplicative decay, floor at 0.05)
        self._epsilon = max(0.05, self._epsilon * 0.999)
        self._state.epsilon = self._epsilon

        self._save()

    def update_with_quality_decisions(
        self, decisions: list, feed_url: str = "unknown"
    ) -> None:
        """
        F199A: Update policy with per-source FindingQualityDecision list.

        Called by SprintScheduler when quality decisions are available from the store
        (e.g., after async_ingest_findings_batch returns FindingQualityDecision list).

        For each decision:
          - Extract source family (source_type) and accepted flag
          - Accumulate accepted/total per source_type in _pending_feedback (bounded 200 source_types)
          - If _scheduler is available (via inject_scheduler), merge accumulated feedback
            into scheduler._source_quality_feedback for processing by the scheduler's
            own _adapt_source_weights_from_feedback at teardown
          - Fail-soft: catch all exceptions and log at DEBUG level
        """
        if not self._enabled:
            return

        # Initialise bounded pending feedback store (survives across calls within a sprint)
        if not hasattr(self, "_pending_feedback"):
            self._pending_feedback: dict[str, dict[str, int]] = defaultdict(
                lambda: {"fetched": 0, "accepted": 0}
            )

        accepted_count = 0
        total_count = 0

        for decision in decisions:
            # Handle both FindingQualityDecision (msgspec.Struct) and dict (ActivationResult)
            if isinstance(decision, dict):
                accepted = bool(decision.get("accepted", False))
                source_family = str(decision.get("source_family", feed_url))
            else:
                # msgspec.Struct — attribute access
                accepted = getattr(decision, "accepted", False)
                source_family = str(getattr(decision, "source_family", feed_url))

            total_count += 1
            if accepted:
                accepted_count += 1

            # Bounded accumulation — max 200 source_types tracked
            if len(self._pending_feedback) < 200:
                fb = self._pending_feedback.setdefault(
                    source_family, {"fetched": 0, "accepted": 0}
                )
                fb["fetched"] = fb.get("fetched", 0) + 1
                if accepted:
                    fb["accepted"] = fb.get("accepted", 0) + 1

        log.debug(
            f"[SprintPolicyManager] update_with_quality_decisions: "
            f"feed_url={feed_url!r}, total={total_count}, accepted={accepted_count}"
        )

        # Attempt delegation to SprintScheduler._adapt_source_weights_from_feedback
        # if self._scheduler is injected (via inject_scheduler helper below)
        sources_count = len(self._pending_feedback)
        if hasattr(self, "_scheduler") and self._scheduler is not None and sources_count > 0:
            try:
                # Merge pending feedback into scheduler's _source_quality_feedback
                for source_type, fb in self._pending_feedback.items():
                    sched_fb = self._scheduler._source_quality_feedback.setdefault(
                        source_type, {"fetched": 0, "accepted": 0}
                    )
                    sched_fb["fetched"] += fb["fetched"]
                    sched_fb["accepted"] += fb["accepted"]
                self._pending_feedback.clear()
                log.debug(
                    f"[SprintPolicyManager] delegated {sources_count} sources to "
                    f"_adapt_source_weights_from_feedback"
                )
            except Exception as e:
                log.debug(
                    f"[SprintPolicyManager] delegation to "
                    f"_adapt_source_weights_from_feedback failed: {e}"
                )

    def should_explore(self) -> bool:
        """
        Decide whether the next sprint should be exploration (deep dive) or exploitation.

        Exploration triggered when:
          - every _exploration_interval sprints, OR
          - epsilon-greedy random flip

        Returns False (exploitation) by default when disabled.
        """
        if not self._enabled:
            return False

        # Deterministic interval-based exploration
        # Fires every N sprints (1-indexed: sprint #5, #10, ... → sequence_number 4, 9, ...)
        if self._state.sprint_sequence_number > 0 and \
                (self._state.sprint_sequence_number + 1) % self._exploration_interval == 0:
            return True

        # Epsilon-greedy stochastic exploration
        import random

        if random.random() < self._epsilon:
            return True

        return False

    def get_action(self) -> int:
        """
        Return the RL action hint for the next sprint.

        Only valid to call when enabled; otherwise returns ACTION_CONTINUE.
        """
        if not self._enabled:
            return ACTION_CONTINUE

        if self.should_explore():
            return ACTION_DEEP_DIVE
        return ACTION_CONTINUE

    @property
    def enabled(self) -> bool:
        """Read-only enabled flag."""
        return self._enabled

    @property
    def sprint_sequence_number(self) -> int:
        """Current sprint count (persisted)."""
        return self._state.sprint_sequence_number

    @property
    def epsilon(self) -> float:
        """Current epsilon value (persisted)."""
        return self._state.epsilon

    @property
    def total_reward(self) -> float:
        """Cumulative reward (in-memory, not persisted separately)."""
        return self._state.total_reward

    @property
    def recent_rewards(self) -> list[float]:
        """Copy of recent reward list."""
        return list(self._state.sprint_rewards)

    # ── Scheduler linkage ──────────────────────────────────────────────────────

    def inject_scheduler(self, scheduler: Any) -> None:
        """
        Inject SprintScheduler reference so update_with_quality_decisions can
        delegate weight adaptation to scheduler._adapt_source_weights_from_feedback.

        Call this from SprintScheduler.inject_policy_manager() alongside the
        policy manager injection so both references are available.

        F228A: No-op when policy is disabled — _scheduler must not be set
        to avoid leaking scheduler reference into disabled policy manager.
        """
        if not self._enabled:
            return
        self._scheduler = scheduler

    # ── Reset ────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset internal state and delete persisted file. Does nothing when disabled."""
        if not self._enabled:
            return
        self._state = SprintPolicyState()
        self._loaded = True
        try:
            if self._policy_path.exists():
                self._policy_path.unlink()
        except Exception as e:
            log.warning(f"[SprintPolicyManager] Failed to delete policy state file: {e}")
