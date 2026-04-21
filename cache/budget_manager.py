"""
Budget Manager - Resource Control for Autonomous Workflow
=========================================================

Manages resource budgets and stop conditions for autonomous research workflows.
Prevents infinite loops and ensures controlled resource consumption.

Features:
- Iteration budget tracking
- Document collection limits
- Time-based constraints
- Tool call quotas
- Stagnation detection (no new entities for N iterations)
- Confidence threshold monitoring
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from pydantic import BaseModel, Field, ConfigDict

from ..utils.deduplication import SimHash
from ..tools.url_dedup import create_rotating_bloom_filter

logger = logging.getLogger(__name__)


class StopReason(Enum):
    """Reasons for stopping the autonomous workflow"""
    MAX_ITERATIONS = "max_iterations"
    MAX_DOCS = "max_docs"
    MAX_TIME = "max_time"
    MAX_TOOL_CALLS = "max_tool_calls"
    MIN_CONFIDENCE = "min_confidence"
    STAGNATION = "stagnation"
    NONE = "none"


class FrequencyTracker:
    """Count-Min Sketch pro frekvenci + drift detekci (256 KB, width=2**15)."""

    def __init__(self, width=2**15, depth=4):
        self.width = width
        self.depth = depth
        # uint16 = 2 bytes, celkem 4 * 32768 * 2 = 256 KB
        self.counters = np.zeros((depth, width), dtype=np.uint16)
        self.hashes = [self._hash(i) for i in range(self.depth)]

    def _hash(self, seed):
        return lambda x: (hash(f"{seed}:{x}") % self.width)

    def add(self, item, count=1):
        item_str = str(item)
        for i in range(self.depth):
            h = self.hashes[i](item_str)
            new_val = self.counters[i, h] + count
            self.counters[i, h] = min(new_val, 65535)  # saturace

    def estimate(self, item):
        item_str = str(item)
        return min(self.counters[i, self.hashes[i](item_str)] for i in range(self.depth))

    def detect_drift(self, item, new_count, threshold=0.3):
        """Detekuje náhlou změnu frekvence (např. simhash začíná prudce růst)."""
        old = self.estimate(item)
        if old == 0:
            return False
        return abs(new_count - old) / old > threshold

    def size_mb(self):
        return self.counters.nbytes / (1024 * 1024)


class BudgetConfig(BaseModel):
    """Configuration for resource budgets"""
    model_config = ConfigDict(validate_assignment=True)

    max_iterations: int = Field(default=6, ge=1, description="Maximum number of iterations")
    max_docs: int = Field(default=30, ge=1, description="Maximum documents to collect")
    max_time_sec: int = Field(default=180, ge=10, description="Maximum time in seconds")
    max_tool_calls: int = Field(default=60, ge=1, description="Maximum tool calls")
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Minimum confidence to stop early")
    stagnation_threshold: int = Field(default=2, ge=1, description="Iterations without new entities to trigger stagnation")


class BudgetState(BaseModel):
    """Current state of resource consumption"""
    model_config = ConfigDict(validate_assignment=True)

    iteration: int = Field(default=0, ge=0, description="Current iteration count")
    docs_collected: int = Field(default=0, ge=0, description="Documents collected so far")
    tool_calls: int = Field(default=0, ge=0, description="Tool calls made so far")
    start_time: datetime = Field(default_factory=datetime.now, description="Workflow start time")
    last_entities_count: int = Field(default=0, ge=0, description="Entity count in previous iteration")
    stagnation_counter: int = Field(default=0, ge=0, description="Consecutive iterations without new entities")
    current_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Current confidence score")


class IterationSnapshot(BaseModel):
    """Log of evidence collected in an iteration"""
    model_config = ConfigDict(validate_assignment=True)

    iteration: int = Field(ge=0, description="Iteration number")
    entities: List[str] = Field(default_factory=list, description="Discovered entities")
    sources: List[str] = Field(default_factory=list, description="Discovered sources")
    claims: List[str] = Field(default_factory=list, description="Extracted claims")
    findings: List[Any] = Field(default_factory=list, description="Research findings (Sprint 26)")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score")
    timestamp: datetime = Field(default_factory=datetime.now, description="When evidence was logged")


class BudgetStatus(BaseModel):
    """Status report for logging and debugging"""
    should_stop: bool = False
    stop_reason: StopReason = StopReason.NONE
    reason_message: str = ""
    iteration: int = 0
    docs_collected: int = 0
    tool_calls: int = 0
    elapsed_time_sec: float = 0.0
    stagnation_counter: int = 0
    current_confidence: float = 0.0
    budgets: Dict[str, Any] = Field(default_factory=dict)
    utilization: Dict[str, float] = Field(default_factory=dict)


class BudgetManager:
    """
    Manages resource budgets and stop conditions for autonomous workflows.

    Tracks consumption of:
    - Iterations
    - Documents
    - Time
    - Tool calls
    - Confidence progress
    - Stagnation (no new discoveries)

    Example:
        config = BudgetConfig(max_iterations=10, max_docs=50)
        manager = BudgetManager(config)

        for iteration in range(100):  # Will stop at budget limits
            evidence = collect_evidence()
            should_stop, reason = manager.check_should_stop(evidence)

            if should_stop:
                print(f"Stopping: {reason}")
                break

            manager.record_iteration(evidence)
    """

    def __init__(self, config: Optional[BudgetConfig] = None):
        """
        Initialize BudgetManager with configuration.

        Args:
            config: Budget configuration. Uses defaults if None.
        """
        self.config = config or BudgetConfig()
        self.state = BudgetState(start_time=datetime.now())
        # Sprint 32+33: RotatingBloomFilter instead of unbounded Set
        self._entities_seen = create_rotating_bloom_filter()
        self._sources_seen = create_rotating_bloom_filter()
        self._claims_seen = create_rotating_bloom_filter()

        # Sprint 26: SimHash for Jaccard novelty detection
        self._simhash = SimHash(hashbits=64)
        # Sprint 32+33: RotatingBloomFilter for fingerprints
        self._cumulative_fingerprints = create_rotating_bloom_filter()
        self._simhash_fingerprints = create_rotating_bloom_filter()

        # Sprint 37: Frequency Tracker (256 KB Count-Min Sketch)
        self._frequency_tracker = FrequencyTracker()

        self._stop_triggered: bool = False
        self._stop_reason: StopReason = StopReason.NONE
        self._stop_message: str = ""

        logger.debug(
            f"BudgetManager initialized: max_iter={self.config.max_iterations}, "
            f"max_docs={self.config.max_docs}, max_time={self.config.max_time_sec}s"
        )

    def check_should_stop(self, current_evidence: IterationSnapshot) -> Tuple[bool, str]:
        """
        Check if workflow should stop based on budget constraints.

        Checks in order of priority:
        1. Hard limits (iterations, docs, time, tool calls)
        2. Quality thresholds (confidence)
        3. Progress indicators (stagnation)

        Args:
            current_evidence: Evidence collected in current iteration

        Returns:
            Tuple of (should_stop: bool, reason: str)
        """
        # Already stopped - don't re-evaluate
        if self._stop_triggered:
            return True, self._stop_message

        # Check hard limits first
        should_stop, reason, message = self._check_hard_limits()
        if should_stop:
            self._trigger_stop(reason, message)
            return True, message

        # Check confidence threshold
        should_stop, reason, message = self._check_confidence(current_evidence)
        if should_stop:
            self._trigger_stop(reason, message)
            return True, message

        # Check stagnation
        should_stop, reason, message = self._check_stagnation(current_evidence)
        if should_stop:
            self._trigger_stop(reason, message)
            return True, message

        return False, ""

    def _check_hard_limits(self) -> Tuple[bool, StopReason, str]:
        """Check hard resource limits"""
        # Max iterations
        if self.state.iteration >= self.config.max_iterations:
            return (
                True,
                StopReason.MAX_ITERATIONS,
                f"Maximum iterations reached ({self.state.iteration}/{self.config.max_iterations})"
            )

        # Max documents
        if self.state.docs_collected >= self.config.max_docs:
            return (
                True,
                StopReason.MAX_DOCS,
                f"Maximum documents collected ({self.state.docs_collected}/{self.config.max_docs})"
            )

        # Max time
        elapsed = (datetime.now() - self.state.start_time).total_seconds()
        if elapsed >= self.config.max_time_sec:
            return (
                True,
                StopReason.MAX_TIME,
                f"Maximum time exceeded ({elapsed:.1f}s/{self.config.max_time_sec}s)"
            )

        # Max tool calls
        if self.state.tool_calls >= self.config.max_tool_calls:
            return (
                True,
                StopReason.MAX_TOOL_CALLS,
                f"Maximum tool calls reached ({self.state.tool_calls}/{self.config.max_tool_calls})"
            )

        return False, StopReason.NONE, ""

    def _check_confidence(self, evidence: IterationSnapshot) -> Tuple[bool, StopReason, str]:
        """Check if confidence threshold is met"""
        if evidence.confidence >= self.config.min_confidence:
            return (
                True,
                StopReason.MIN_CONFIDENCE,
                f"Minimum confidence reached ({evidence.confidence:.2f} >= {self.config.min_confidence})"
            )
        return False, StopReason.NONE, ""

    def _compute_novelty_score(self, new_fingerprints: Set[int]) -> float:
        """Compute novelty score using Jaccard similarity (Sprint 26).

        With BloomFilter, we approximate Jaccard by checking membership of new fingerprints
        in the cumulative bloom filter and comparing to the total count.
        """
        if not new_fingerprints:
            return 1.0

        # Count how many new fingerprints are already in the cumulative filter
        # BloomFilter requires string keys, convert int fingerprints
        already_seen = sum(1 for fp in new_fingerprints if str(fp) in self._cumulative_fingerprints)

        # Approximate Jaccard: novelty = 1 - (overlap / total)
        # With BloomFilter we can only check overlap, not exact union
        # Approximate union as: total new + estimated unique in cumulative
        total_new = len(new_fingerprints)
        novelty = 1.0 - (already_seen / total_new) if total_new > 0 else 0.0

        return novelty

    def _check_stagnation(self, evidence: IterationSnapshot) -> Tuple[bool, StopReason, str]:
        """Check if workflow is stagnating (no new discoveries) - now with Jaccard novelty (Sprint 26)"""
        # Count new discoveries - FIX 2: BloomFilter uses __contains__, not set subtraction
        new_entities = sum(1 for e in evidence.entities if e not in self._entities_seen)
        new_sources = sum(1 for s in evidence.sources if s not in self._sources_seen)
        new_claims = sum(1 for c in evidence.claims if c not in self._claims_seen)

        total_new = new_entities + new_sources + new_claims

        # Sprint 26: Jaccard-based novelty detection
        # Handle both string findings and objects with content attribute
        findings_list = evidence.findings if evidence.findings else []
        new_fps = set()
        for f in findings_list:
            if hasattr(f, 'content'):
                new_fps.add(self._simhash.compute(f.content))
            elif isinstance(f, str):
                new_fps.add(self._simhash.compute(f))
        novelty = self._compute_novelty_score(new_fps)
        jaccard_stagnation = novelty < 0.1  # less than 10% new content

        existing_stagnation = (total_new == 0)

        if existing_stagnation or jaccard_stagnation:
            self.state.stagnation_counter += 1
            logger.debug(
                f"Stagnation detected: {self.state.stagnation_counter}/"
                f"{self.config.stagnation_threshold} iterations (existing={existing_stagnation}, jaccard={jaccard_stagnation})"
            )

            if self.state.stagnation_counter >= self.config.stagnation_threshold:
                return (
                    True,
                    StopReason.STAGNATION,
                    f"Stagnation detected ({self.state.stagnation_counter} iterations without new content)"
                )
        else:
            # Reset stagnation counter on new discoveries
            if self.state.stagnation_counter > 0:
                logger.debug(f"Stagnation reset: {total_new} new discoveries, novelty={novelty:.2f}")
            self.state.stagnation_counter = 0

        # Update cumulative fingerprints (BloomFilter requires string keys)
        for fp in new_fps:
            self._cumulative_fingerprints.add(str(fp))

        return False, StopReason.NONE, ""

    def _trigger_stop(self, reason: StopReason, message: str) -> None:
        """Record stop condition"""
        self._stop_triggered = True
        self._stop_reason = reason
        self._stop_message = message
        logger.info(f"BudgetManager stop triggered: {message}")

    def record_iteration(self, evidence: IterationSnapshot) -> None:
        """
        Record completion of an iteration.

        Updates state and tracks stagnation. Call this AFTER check_should_stop
        returns False (i.e., when continuing to next iteration).

        Args:
            evidence: Evidence collected in this iteration
        """
        self.state.iteration += 1
        self.state.current_confidence = evidence.confidence

        # Track discoveries - FIX 2: BloomFilter uses .add() not .update()
        for entity in evidence.entities:
            self._entities_seen.add(entity)
        for source in evidence.sources:
            self._sources_seen.add(source)
        for claim in evidence.claims:
            self._claims_seen.add(claim)

        # Update last entity count for stagnation tracking
        # FIX 2: BloomFilter doesn't have len(), approximate using capacity
        self.state.last_entities_count = sum(1 for e in evidence.entities if e in self._entities_seen)

        logger.debug(
            f"Iteration {self.state.iteration} recorded: "
            f"entities={len(evidence.entities)}, sources={len(evidence.sources)}, "
            f"claims={len(evidence.claims)}, confidence={evidence.confidence:.2f}"
        )

    def record_tool_call(self, count: int = 1) -> None:
        """
        Record tool call(s).

        Args:
            count: Number of tool calls to record (default: 1)
        """
        self.state.tool_calls += count
        logger.debug(f"Tool calls: {self.state.tool_calls}/{self.config.max_tool_calls}")

    def record_docs(self, count: int) -> None:
        """
        Record document collection.

        Args:
            count: Number of documents collected
        """
        self.state.docs_collected += count
        logger.debug(f"Documents collected: {self.state.docs_collected}/{self.config.max_docs}")

    def get_status(self) -> BudgetStatus:
        """
        Get current budget status for logging and debugging.

        Returns:
            BudgetStatus with current state and utilization metrics
        """
        elapsed = (datetime.now() - self.state.start_time).total_seconds()

        # Calculate utilization percentages
        utilization = {
            "iterations": min(100.0, (self.state.iteration / self.config.max_iterations) * 100),
            "docs": min(100.0, (self.state.docs_collected / self.config.max_docs) * 100),
            "time": min(100.0, (elapsed / self.config.max_time_sec) * 100),
            "tool_calls": min(100.0, (self.state.tool_calls / self.config.max_tool_calls) * 100),
        }

        # Budget limits
        budgets = {
            "max_iterations": self.config.max_iterations,
            "max_docs": self.config.max_docs,
            "max_time_sec": self.config.max_time_sec,
            "max_tool_calls": self.config.max_tool_calls,
            "min_confidence": self.config.min_confidence,
            "stagnation_threshold": self.config.stagnation_threshold,
        }

        # Determine if any limit is approaching (80% threshold)
        approaching_limit = any(u >= 80 for u in utilization.values())

        return BudgetStatus(
            should_stop=self._stop_triggered,
            stop_reason=self._stop_reason,
            reason_message=self._stop_message,
            iteration=self.state.iteration,
            docs_collected=self.state.docs_collected,
            tool_calls=self.state.tool_calls,
            elapsed_time_sec=elapsed,
            stagnation_counter=self.state.stagnation_counter,
            current_confidence=self.state.current_confidence,
            budgets=budgets,
            utilization=utilization,
        )

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary of budget consumption.

        Returns:
            Dictionary with summary statistics
        """
        elapsed = (datetime.now() - self.state.start_time).total_seconds()

        return {
            "iterations": {
                "used": self.state.iteration,
                "limit": self.config.max_iterations,
                "remaining": max(0, self.config.max_iterations - self.state.iteration),
            },
            "documents": {
                "used": self.state.docs_collected,
                "limit": self.config.max_docs,
                "remaining": max(0, self.config.max_docs - self.state.docs_collected),
            },
            "tool_calls": {
                "used": self.state.tool_calls,
                "limit": self.config.max_tool_calls,
                "remaining": max(0, self.config.max_tool_calls - self.state.tool_calls),
            },
            "time": {
                "used_sec": elapsed,
                "limit_sec": self.config.max_time_sec,
                "remaining_sec": max(0, self.config.max_time_sec - elapsed),
            },
            "discoveries": {
                # FIX 2: BloomFilter doesn't have len(), return approximate count from state
                "entities": self.state.last_entities_count,
                "sources": self.state.last_entities_count,
                "claims": self.state.last_entities_count,
            },
            "confidence": {
                "current": self.state.current_confidence,
                "target": self.config.min_confidence,
                "met": self.state.current_confidence >= self.config.min_confidence,
            },
            "stagnation": {
                "counter": self.state.stagnation_counter,
                "threshold": self.config.stagnation_threshold,
                "is_stagnating": self.state.stagnation_counter >= self.config.stagnation_threshold,
            },
            "stopped": self._stop_triggered,
            "stop_reason": self._stop_reason.value if self._stop_triggered else None,
        }

    def is_approaching_limit(self, threshold: float = 0.8) -> bool:
        """
        Check if any budget is approaching its limit.

        Args:
            threshold: Fraction of budget to consider "approaching" (default: 0.8 = 80%)

        Returns:
            True if any budget is at or above threshold
        """
        elapsed = (datetime.now() - self.state.start_time).total_seconds()

        checks = [
            self.state.iteration / self.config.max_iterations >= threshold,
            self.state.docs_collected / self.config.max_docs >= threshold,
            elapsed / self.config.max_time_sec >= threshold,
            self.state.tool_calls / self.config.max_tool_calls >= threshold,
        ]

        return any(checks)

    def reset(self) -> None:
        """Reset budget manager to initial state"""
        self.state = BudgetState(start_time=datetime.now())
        # Sprint 32+33: Reinitialize RotatingBloomFilter instances
        self._entities_seen = create_rotating_bloom_filter()
        self._sources_seen = create_rotating_bloom_filter()
        self._claims_seen = create_rotating_bloom_filter()
        self._cumulative_fingerprints = create_rotating_bloom_filter()
        self._simhash_fingerprints = create_rotating_bloom_filter()
        self._stop_triggered = False
        self._stop_reason = StopReason.NONE
        self._stop_message = ""
        logger.debug("BudgetManager reset")

    def __repr__(self) -> str:
        status = self.get_status()
        return (
            f"BudgetManager(iter={self.state.iteration}/{self.config.max_iterations}, "
            f"docs={self.state.docs_collected}/{self.config.max_docs}, "
            f"tools={self.state.tool_calls}/{self.config.max_tool_calls}, "
            f"stopped={self._stop_triggered})"
        )

    # Sprint 32+33: Convenience methods for Bloom filter API
    def add_entity(self, entity_id: str) -> bool:
        """Add entity to bloom filter. Returns True if was new."""
        was_new = entity_id not in self._entities_seen
        self._entities_seen.add(entity_id)
        return was_new

    def entity_seen(self, entity_id: str) -> bool:
        """Check if entity was seen."""
        return entity_id in self._entities_seen

    def add_simhash(self, fp: int) -> bool:
        """Add SimHash fingerprint to bloom filter. Returns True if was new."""
        # RotatingBloomFilter requires string keys, convert int to string
        fp_str = str(fp)
        was_new = fp_str not in self._simhash_fingerprints
        self._simhash_fingerprints.add(fp_str)

        # Sprint 37: Frequency tracking
        old_freq = self._frequency_tracker.estimate(fp)
        self._frequency_tracker.add(fp)
        new_freq = self._frequency_tracker.estimate(fp)

        # Sprint 39: Drift alert - při nárůstu frekvence > 50%
        if old_freq > 0 and (new_freq - old_freq) / old_freq > 0.5:
            logger.warning(f"[DRIFT] Simhash {fp} frequency spike: {old_freq}→{new_freq}")

        # Detekce driftu – pokud nový simhash má nečekaně vysokou frekvenci
        if new_freq > 10 and was_new:
            logger.debug(f"[DRIFT] High frequency for new simhash: {fp}")

        return was_new


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_budget_manager(
    max_iterations: int = 6,
    max_docs: int = 30,
    max_time_sec: int = 180,
    max_tool_calls: int = 60,
    min_confidence: float = 0.7,
    stagnation_threshold: int = 2,
) -> BudgetManager:
    """
    Create BudgetManager with specified parameters.

    Args:
        max_iterations: Maximum iterations
        max_docs: Maximum documents to collect
        max_time_sec: Maximum time in seconds
        max_tool_calls: Maximum tool calls
        min_confidence: Minimum confidence for early stop
        stagnation_threshold: Iterations without progress to trigger stop

    Returns:
        Configured BudgetManager instance
    """
    config = BudgetConfig(
        max_iterations=max_iterations,
        max_docs=max_docs,
        max_time_sec=max_time_sec,
        max_tool_calls=max_tool_calls,
        min_confidence=min_confidence,
        stagnation_threshold=stagnation_threshold,
    )
    return BudgetManager(config)


def create_quick_budget() -> BudgetManager:
    """Create BudgetManager for quick research (minimal resources)"""
    return create_budget_manager(
        max_iterations=3,
        max_docs=10,
        max_time_sec=60,
        max_tool_calls=20,
        min_confidence=0.6,
        stagnation_threshold=1,
    )


def create_deep_budget() -> BudgetManager:
    """Create BudgetManager for deep research (generous resources)"""
    return create_budget_manager(
        max_iterations=15,
        max_docs=100,
        max_time_sec=600,
        max_tool_calls=200,
        min_confidence=0.8,
        stagnation_threshold=3,
    )
