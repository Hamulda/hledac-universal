"""
Domain Concurrency Bandit — Gradient Bandit for Per-Domain Adaptive Concurrency
=============================================================================

Sprint 8AC: Gradient Bandit algorithm for adaptive per-host connection limits.
Uses softmax action selection over Fibonacci-like arm values [1, 2, 3, 5, 8].

Unlike AIMD (which is too aggressive for OSINT stealth), Gradient Bandit
provides smooth, probabilistic exploration with exponential reward smoothing.

Authoritative arms: network/domain_concurrency.py
"""

from __future__ import annotations

import math
import random
from typing import List

# =============================================================================
# Arm Definition — Fibonacci-like conservative concurrency levels
# =============================================================================
ARM_VALUES: List[int] = [1, 2, 3, 5, 8]
N_ARMS: int = len(ARM_VALUES)

# Gradient Bandit hyperparameters
_ALPHA: float = 0.1  # learning rate for preference updates
_ALPHA_REWARD: float = 0.9  # exponential smoothing for baseline


class DomainConcurrencyBandit:
    """
    Gradient Bandit for adaptive per-domain concurrency limit.

    Uses softmax action selection (softmax action selection, not epsilon-greedy)
    with exponential baseline reward for stable learning.

    Invariants:
        [I1] select_arm() always returns a valid arm index [0, N_ARMS)
        [I2] record_outcome() updates preferences and baseline atomically
        [I3] current_limit property returns ARM_VALUES[selected_arm]
        [I4] consecutive_429 > 2 forces arm[0] (minimum concurrency)
    """

    __slots__ = (
        "_preferences",
        "_baseline",
        "_consecutive_429",
        "_selected_arm",
        "_hit429_since_last_select",
    )

    def __init__(self) -> None:
        # Softmax preferences — initialized to 0 (uniform initially)
        self._preferences: List[float] = [0.0] * N_ARMS
        # Running average reward (exponential smoothing baseline)
        self._baseline: float = 0.0
        # Consecutive 429 counter for emergency arm reduction
        self._consecutive_429: int = 0
        # Currently selected arm index
        self._selected_arm: int = 2  # default: arms[2] = 3
        # Track if we hit 429 since last select_arm() call
        self._hit429_since_last_select: bool = False

    # -------------------------------------------------------------------------
    # Softmax action selection
    # -------------------------------------------------------------------------

    def _softmax_probs(self) -> List[float]:
        """
        Compute softmax probability distribution over arms.

        Uses numerically stable softmax: subtract max for stability.
        """
        prefs = self._preferences
        # Numerical stability: subtract max
        max_pref = max(prefs)
        exp_prefs = [math.exp(p - max_pref) for p in prefs]
        sum_exp = sum(exp_prefs)
        return [e / sum_exp for e in exp_prefs]

    def select_arm(self) -> int:
        """
        Select an arm using softmax action selection.

        Returns:
            int: arm index in [0, N_ARMS)

        Invariant [I1]: always returns valid arm index
        """
        # Emergency override: consecutive 429 > 2 → force minimum arm
        if self._consecutive_429 > 2:
            self._selected_arm = 0
            self._hit429_since_last_select = False
            return 0

        probs = self._softmax_probs()
        r = random.random()
        cumulative = 0.0
        for i, p in enumerate(probs):
            cumulative += p
            if r < cumulative:
                self._selected_arm = i
                self._hit429_since_last_select = False
                return i

        # Fallback: return most probable arm (should not reach here)
        self._selected_arm = max(range(N_ARMS), key=lambda i: probs[i])
        self._hit429_since_last_select = False
        return self._selected_arm

    # -------------------------------------------------------------------------
    # Reward function
    # -------------------------------------------------------------------------

    def _compute_reward(
        self, latency_ms: float, status_code: int, got_captcha: bool = False
    ) -> float:
        """
        Compute reward from a single outcome observation.

        Args:
            latency_ms: Response latency in milliseconds
            status_code: HTTP status code
            got_captcha: Whether CAPTCHA was detected

        Returns:
            float: reward in (0, 1] range (1 = best, 0 = worst)
        """
        # Speed reward: linear decay from 1 at 0ms to 0 at 5000ms
        speed_reward = max(0.0, 1.0 - latency_ms / 5000.0)

        # Detection penalty based on HTTP status or CAPTCHA signal
        if status_code == 429:
            detection_penalty = 0.8  # rate limit = strong negative signal
        elif status_code == 403:
            detection_penalty = 0.6  # forbidden
        elif got_captcha:
            detection_penalty = 0.9  # CAPTCHA = critical detection
        else:
            detection_penalty = 0.0

        return speed_reward * (1.0 - detection_penalty)

    # -------------------------------------------------------------------------
    # Outcome recording and learning
    # -------------------------------------------------------------------------

    def record_outcome(
        self, arm_idx: int, latency_ms: float, status_code: int, got_captcha: bool = False
    ) -> None:
        """
        Record an outcome and update bandit preferences.

        Uses Gradient Bandit update rule:
            preferences[selected] += α * (reward - baseline) * (1 - softmax[selected])
            preferences[other]   -= α * (reward - baseline) * softmax[other]

        Invariant [I2]: updates are atomic (preferences + baseline together)

        Args:
            arm_idx: Arm index that was selected
            latency_ms: Response latency in milliseconds
            status_code: HTTP status code
            got_captcha: Whether CAPTCHA was detected
        """
        reward = self._compute_reward(latency_ms, status_code, got_captcha)

        # Track consecutive 429s for emergency override
        if status_code == 429:
            self._consecutive_429 += 1
            self._hit429_since_last_select = True
        else:
            self._consecutive_429 = 0

        # [I4] Immediate arm reduction on 429 flood
        if self._consecutive_429 > 2:
            self._selected_arm = 0

        # Update baseline with exponential moving average
        # baseline_new = α * reward + (1 - α) * baseline_old
        self._baseline = _ALPHA_REWARD * reward + (1.0 - _ALPHA_REWARD) * self._baseline

        # Compute advantage: reward - baseline
        advantage = reward - self._baseline

        # Gradient Bandit update
        probs = self._softmax_probs()
        for i in range(N_ARMS):
            if i == arm_idx:
                # Selected arm: push preference UP proportional to advantage
                self._preferences[i] += _ALPHA * advantage * (1.0 - probs[i])
            else:
                # Other arms: push preference DOWN proportional to softmax prob
                self._preferences[i] -= _ALPHA * advantage * probs[i]

    # -------------------------------------------------------------------------
    # Public accessors
    # -------------------------------------------------------------------------

    @property
    def current_limit(self) -> int:
        """
        Return the currently selected concurrency limit.

        Returns:
            int: ARM_VALUES[selected_arm], i.e., the actual limit value
        """
        return ARM_VALUES[self._selected_arm]

    @property
    def consecutive_429(self) -> int:
        """Return consecutive 429 count for monitoring."""
        return self._consecutive_429
