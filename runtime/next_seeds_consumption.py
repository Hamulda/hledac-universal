"""
Sprint F227C: next_sprint_seeds consumption reality probe
=========================================================

Classification: PRODUCED_AND_CONSUMED_INDIRECT

next_sprint_seeds flow:
  PRODUCER:  sprint_exporter._generate_next_sprint_seeds() → writes {sprint_id}_next_seeds.json
  STORAGE:   duckdb_store (report metadata via evidence_delta_memory)
  PASSIVE:   evidence_delta_memory reads next_seeds_count for KPI (not advisory)
  GAP:       No active consumption by pivot_planner, advisory_runner, or scheduler

F226D seed format (from capability_synthesis + analyst_brief):
  {
    "seed_source": "capability_synthesis" | "analyst_brief",
    "task_type": "query_suggestion" | "pivot_seed" | "investigation_seed" | ...,
    "mission_intent": "expand to .gov domains",
    "suggested_action": "refine" | "narrow_scope" | "broaden" | ...,
    "expected_value": "high" | "medium" | "low",
    "reason": "signal=high_density/accepted=10/ioc_density=2.5",
    "priority": 0.75,
    "value": "optional query string"
  }

Legacy F214 seed format:
  {
    "task_type": "query_suggestion" | ...,
    "suggested_action": "refine" | ...,
    "priority": 0.75,
    "reason": "..."
  }

Shared fields: task_type, suggested_action, priority, reason

Integration: Advisory-only, read from file, fed to SprintAdvisoryRunner
as informational context (no live execution, no scheduler ownership change).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from hledac.universal.paths import get_sprint_next_seeds_path

log = logging.getLogger(__name__)

# Bounds
MAX_SEEDS_BOUND: int = 15  # matches MAX_SEEDS in sprint_exporter

# Required fields in a valid seed entry
_REQUIRED_FIELDS: set[str] = {"task_type", "suggested_action", "priority", "reason"}


def load_next_sprint_seeds(sprint_id: str) -> list[dict[str, Any]]:
    """
    Load next_sprint_seeds from canonical path for a given sprint.

    Accepts both F226D (with seed_source/mission_intent/expected_value)
    and legacy F214 (task_type/suggested_action/priority/reason only) formats.

    Fail-soft: returns [] on any error (file not found, parse error, etc.).
    Bounded: caps at MAX_SEEDS_BOUND entries.

    Args:
        sprint_id: Sprint identifier used to compute canonical path.

    Returns:
        List of seed dicts, sorted by priority descending. Empty list on failure.
    """
    try:
        path = get_sprint_next_seeds_path(sprint_id)
        if not path.exists():
            log.debug(f"[F227C] next_seeds file not found: {path}")
            return []

        raw = path.read_text()
        data = json.loads(raw)

        # Unwrap outer envelope if present
        if isinstance(data, dict):
            if "seeds" in data:
                seeds = data["seeds"]
            else:
                # Single dict with seed data but no wrapper
                seeds = [data]
        elif isinstance(data, list):
            seeds = data
        else:
            log.warning(f"[F227C] unexpected next_seeds structure type={type(data).__name__}")
            return []

        if not isinstance(seeds, list):
            log.warning(f"[F227C] seeds field is not a list: {type(seeds).__name__}")
            return []

        validated: list[dict[str, Any]] = []
        for entry in seeds:
            if not isinstance(entry, dict):
                continue
            # Backward compat: F214 entries lack seed_source
            if not _is_valid_seed_entry(entry):
                log.debug(f"[F227C] skipping malformed seed entry: {entry.get('task_type', '?')}")
                continue
            validated.append(entry)

        # Sort by priority descending (match exporter sort order)
        validated.sort(key=lambda s: s.get("priority", 0.0), reverse=True)

        if len(validated) > MAX_SEEDS_BOUND:
            validated = validated[:MAX_SEEDS_BOUND]

        log.debug(f"[F227C] loaded {len(validated)} seeds from {path}")
        return validated

    except json.JSONDecodeError as e:
        log.warning(f"[F227C] JSON parse error reading next_seeds: {e}")
        return []
    except OSError as e:
        log.warning(f"[F227C] OS error reading next_seeds: {e}")
        return []
    except Exception as e:
        log.warning(f"[F227C] unexpected error loading next_seeds: {e}")
        return []


def _is_valid_seed_entry(entry: dict[str, Any]) -> bool:
    """
    Check if a seed entry has the minimum required fields.

    Both F226D (with F226D_EXTRA_FIELDS) and legacy F214 formats are accepted.
    Only checks required shared fields, not F226D-specific extras.
    """
    if not isinstance(entry, dict):
        return False
    # Check shared required fields exist and are non-None
    for field in _REQUIRED_FIELDS:
        if field not in entry or entry[field] is None:
            return False
    # priority must be numeric
    priority = entry.get("priority")
    if not isinstance(priority, (int, float)):
        return False
    return True


def get_seed_summary(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute a summary of loaded seeds for advisory context.

    Returns a dict suitable for passing to advisory runners as informational context.
    Does not execute anything — purely a data transformation.
    """
    if not seeds:
        return {"count": 0, "by_task_type": {}, "top_priority": None, "has_F226D_format": False}

    by_task_type: dict[str, int] = {}
    top_priority: float = 0.0

    for seed in seeds:
        tt = seed.get("task_type", "unknown")
        by_task_type[tt] = by_task_type.get(tt, 0) + 1
        p = seed.get("priority", 0.0)
        if p > top_priority:
            top_priority = p

    return {
        "count": len(seeds),
        "by_task_type": by_task_type,
        "top_priority": top_priority,
        "has_F226D_format": any(
            "seed_source" in s or "mission_intent" in s for s in seeds
        ),
    }